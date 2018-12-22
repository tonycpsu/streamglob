import logging
logger = logging.getLogger(__name__)

import urwid

from orderedattrdict import AttrDict
from panwid.datatable import *

from ..session import *

from .base import *
from .bam import *
from .filters import *
from ..exceptions import *
from ..state import *


class NHLStreamSession(AuthenticatedStreamSession):

    AUTH = b"web_nhl-v1.0.0:2d1d846ea3b194a18ef40ac9fbce97e3"

    SCHEDULE_TEMPLATE = (
        "https://statsapi.web.nhl.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    RESOLUTIONS = AttrDict([
        ("720p", "720p"),
        ("540p", "540p"),
        ("504p", "504p"),
        ("360p", "360p"),
        ("288p", "288p"),
        ("216p", "216p")
    ])

    def __init__(
            self,
            username, password,
            session_key=None,
            *args, **kwargs
    ):
        super(NHLStreamSession, self).__init__(
            username, password,
            *args, **kwargs
        )
        self.session_key = session_key


    def login(self):

        if self.logged_in:
            logger.info("already logged in")
            return

        auth = base64.b64encode(self.AUTH).decode("utf-8")

        token_url = "https://user.svc.nhl.com/oauth/token?grant_type=client_credentials"

        headers = {
            "Authorization": f"Basic {auth}",
            # "Referer": "https://www.nhl.com/login/freeGame?forwardUrl=https%3A%2F%2Fwww.nhl.com%2Ftv%2F2018020013%2F221-2000552%2F61332703",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://www.nhl.com"
        }

        res = self.session.post(token_url, headers=headers)
        self.token = json.loads(res.text)["access_token"]

        login_url="https://gateway.web.nhl.com/ws/subscription/flow/nhlPurchase.login"

        auth = base64.b64encode(b"web_nhl-v1.0.0:2d1d846ea3b194a18ef40ac9fbce97e3")

        params = {
            "nhlCredentials":  {
                "email": self.username,
                "password": self.password
            }
        }

        headers = {
            "Authorization": self.token,
            "Origin": "https://www.nhl.com",
            # "Referer": "https://www.nhl.com/login/freeGame?forwardUrl=https%3A%2F%2Fwww.nhl.com%2Ftv%2F2018020013%2F221-2000552%2F61332703",
        }

        res = self.session.post(
            login_url,
            json=params,
            headers=headers
        )
        self.save()
        print(res.status_code)
        return (res.status_code == 200)


    @property
    def logged_in(self):

        logged_in_url = "https://account.nhl.com/ui/AccountProfile"
        content = self.get(logged_in_url).text
        # FIXME: this is gross
        if '"NHL Account - Profile"' in content:
            return True
        return False

    @property
    def session_key(self):
        return self._state.session_key

    @session_key.setter
    def session_key(self, value):
        self._state.session_key = value

    @property
    def token(self):
        return self._state.token

    @token.setter
    def token(self, value):
        self._state.token = value


    def get_stream(self, media):

        url = "https://mf.svc.nhl.com/ws/media/mf/v2.4/stream"

        self.login()

        event_id = media["eventId"]
        if not self.session_key:
            logger.info("getting session key")


            params = {
                "eventId": event_id,
                "format": "json",
                "platform": "WEB_MEDIAPLAYER",
                "subject": "NHLTV",
                "_": int(datetime.now().timestamp())*1000
            }

            res = self.get(
                url,
                params=params
            )
            j = res.json()
            logger.trace(json.dumps(j, sort_keys=True,
                             indent=4, separators=(',', ': ')))

            self.session_key = j["session_key"]
            self.save()

        params = {
            "contentId": media["mediaPlaybackId"],
            "playbackScenario": "HTTP_CLOUD_WIRED_WEB",
            "sessionKey": self.session_key,
            "auth": "response",
            "platform": "WEB_MEDIAPLAYER",
            "_": "1538708097285"
        }
        res = self.get(
            url,
            params=params
        )
        j = res.json()
        logger.trace(json.dumps(j, sort_keys=True,
                                   indent=4, separators=(',', ': ')))

        try:
            media_auth = next(x["attributeValue"]
                              for x in j["session_info"]["sessionAttributes"]
                              if x["attributeName"] == "mediaAuth_v2")
        except KeyError:
            raise StreamSessionException(f"No stream found for event {event_id}")

        self.cookies.set_cookie(
            Cookie(0, 'mediaAuth_v2', media_auth,
                   '80', '80', '.nhl.com',
                   None, None, '/', True, False, 4102444800, None, None, None, {}),
        )

        stream = AttrDict(
            (j["user_verified_event"][0]
             ["user_verified_content"][0]
             ["user_verified_media_item"][0]
            )
        )

        return stream



class NHLLineScoreDataTable(DataTable):

    @classmethod
    def from_json(cls, line_score,
                     away_team=None, home_team=None,
                     hide_spoilers=False
    ):

        columns = [
            DataTableColumn("team", width=6, label="", align="right", padding=1),
        ]

        if "teams" in line_score:
            tk = line_score["teams"]
        else:
            tk = line_score

        data = []
        for s, side in enumerate(["away", "home"]):

            i = -1
            line = AttrDict()
            if "periods" in line_score and isinstance(line_score["periods"], list):
                for i, period in enumerate(line_score["periods"]):
                    if not s:
                        columns.append(
                            DataTableColumn(str(i+1), label=str(i+1) if i < 3 else "O", width=3)
                        )
                        line.team = away_team
                    else:
                        line.team = home_team

                    if hide_spoilers:
                        setattr(line, str(i+1), "?")

                    elif side in period:
                        if isinstance(period[side], dict) and "goals" in period[side]:
                            setattr(line, str(i+1), parse_int(period[side]["goals"]))
                    else:
                        setattr(line, str(i+1), "X")

                for n in list(range(i+1, 3)):
                    if not s:
                        columns.append(
                            DataTableColumn(str(n+1), label=str(n+1), width=3)
                        )
                    if hide_spoilers:
                        setattr(line, str(n+1), "?")

            if not s:
                columns.append(
                    DataTableColumn("empty", label="", width=3)
                )

            for stat in ["goals", "shotsOnGoal"]:
                if not stat in tk[side]: continue

                if not s:
                    columns.append(
                        DataTableColumn(stat, label=stat[0].upper(), width=3)
                    )
                if not hide_spoilers:
                    setattr(line, stat, parse_int(tk[side][stat]))
                else:
                    setattr(line, stat, "?")


            data.append(line)
        return cls(columns, data=data)


class NHLProvider(BAMProviderMixin,
                  # SimpleProviderViewMixin,
                  BaseProvider):

    SESSION_CLASS = NHLStreamSession

    RESOLUTIONS = AttrDict([
        ("720p", "720p"),
        ("540p", "540p"),
        ("504p", "504p"),
        ("360p", "360p"),
        ("288p", "288p"),
        ("216p", "216p")
    ])

    SCHEDULE_TEMPLATE = (
        "https://statsapi.web.nhl.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&expand=schedule.game.content.media.milestones"
        "&expand=schedule.game.content.media.epg"
        # "&expand=schedule.venue"
        "&expand=schedule.status"
        "&expand=schedule.teams"
        "&expand=schedule.linescore"
        "&expand=schedule.broadcasts.all"
        # "&expand=schedule.ticket"
        "&expand=schedule.radioBroadcasts"
        # "&expand=schedule.game.seriesSummary"
        # "&expand=seriesSummary.series"
        # "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
        # "&expand=schedule.game.content.media.milestones"
    )

    DATA_TABLE_CLASS = NHLLineScoreDataTable

    MEDIA_TITLE = "NHLTV"

    def teams(self, season=None):

        teams_url = (
            "https://statsapi.web.nhl.com/api/v1/teams"
            "?{season}".format(
                season=season if season else ""
            )
        )

        # raise Exception(state.session.get(teams_url).json())
        with state.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
                for team in sorted(state.session.get(teams_url).json()["teams"],
                                   key=lambda t: t["abbreviation"])
            )

        return teams

    # def media_timestamps(self, game_id, media_id):
    #     raise NotImplementedError


    def media_timestamps(self, game_id, media_id):
        j =  self.schedule(game_id=game_id)
        milestones = j["dates"][0]["games"][0]["content"]["media"]["milestones"]

        # try:
        #     airing = next(a for a in self.session.airings(game_id)
        #                   if a["mediaId"] == media_id)
        # except StopIteration:
        #     raise StreamSessionException("No airing for media %s" %(media_id))

        start_timestamps = []
        # try:
        #     start_time = next(
        #             t["startDatetime"] for t in
        #             next(m for m in airing["milestones"]
        #              if m["milestoneType"] == "BROADCAST_START"
        #             )["milestoneTime"]
        #         if t["type"] == "absolute"
        #         )

        # except StopIteration:
        #     # Some streams don't have a "BROADCAST_START" milestone.  We need
        #     # something, so we use the scheduled game start time, which is
        #     # probably wrong.
        #     start_time = airing["startDate"]

        start_time = next(
            m["timeAbsolute"]
            for m in milestones["items"]
            if m["type"] == "BROADCAST_START"
        )
        start_timestamps.append(
            ("S", start_time)
        )

        # try:
        #     start_offset = next(
        #         t["start"] for t in
        #         next(m for m in airing["milestones"]
        #              if m["milestoneType"] == "BROADCAST_START"
        #         )["milestoneTime"]
        #         if t["type"] == "offset"
        #     )
        # except StopIteration:
        #     # Same as above.  Missing BROADCAST_START milestone means we
        #     # probably don't get accurate offsets for inning milestones.
        #     start_offset = 0

        start_offset = next(
            m["timeOffset"]
            for m in milestones["items"]
            if m["type"] == "BROADCAST_START"
        )
        start_timestamps.append(
            ("SO", start_offset)
        )

        timestamps = AttrDict(start_timestamps)
        timestamps.update(AttrDict([
            (m["period"], int(m["timeOffset"]))
            for m in milestones["items"]
            if m["type"] == "PERIOD_START"
        ]))
        # raise Exception(timestamps)
        return timestamps
