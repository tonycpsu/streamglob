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



class MLBStreamSession(AuthenticatedStreamSession):

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    PLATFORM = "macintosh"
    BAM_SDK_VERSION = "3.0"

    API_KEY_URL = "https://www.mlb.com/tv/g490865/"

    API_KEY_RE = re.compile(r'"apiKey":"([^"]+)"')

    CLIENT_API_KEY_RE = re.compile(r'"clientApiKey":"([^"]+)"')

    TOKEN_URL_TEMPLATE = (
        "https://media-entitlement.mlb.com/jwt"
        "?ipid={ipid}&fingerprint={fingerprint}==&os={platform}&appname=mlbtv_web"
    )

    GAME_CONTENT_URL_TEMPLATE="http://statsapi.mlb.com/api/v1/game/{game_id}/content"

    ACCESS_TOKEN_URL = "https://edge.bamgrid.com/token"

    STREAM_URL_TEMPLATE="https://edge.svcs.mlb.com/media/{media_id}/scenarios/browser"

    AIRINGS_URL_TEMPLATE=(
        "https://search-api-mlbtv.mlb.com/svc/search/v2/graphql/persisted/query/"
        "core/Airings?variables={{%22partnerProgramIds%22%3A[%22{game_id}%22]}}"
    )

    def __init__(
            self,
            username, password,
            api_key=None,
            client_api_key=None,
            token=None,
            access_token=None,
            access_token_expiry=None,
            *args, **kwargs
    ):
        super(MLBStreamSession, self).__init__(
            username, password,
            *args, **kwargs
        )
        self._state.api_key = api_key
        self._state.client_api_key = client_api_key
        self._state.token = token
        self._state.access_token = access_token
        self._state.access_token_expiry = access_token_expiry

    def login(self):

        if self.logged_in:
            logger.debug("already logged in")
            return

        # logger.debug("checking for existing log in")

        initial_url = ("https://secure.mlb.com/enterworkflow.do"
                       "?flowId=registration.wizard&c_id=mlb")

        # res = self.get(initial_url)
        # if not res.status_code == 200:
        #     raise StreamSessionException(res.content)

        data = {
            "uri": "/account/login_register.jsp",
            "registrationAction": "identify",
            "emailAddress": self.username,
            "password": self.password,
            "submitButton": ""
        }
        logger.debug("attempting new log in")

        login_url = "https://securea.mlb.com/authenticate.do"

        res = self.post(
            login_url,
            data=data,
            headers={"Referer": (initial_url)}
        )

        if not (self.ipid and self.fingerprint):
            # print(res.content)
            raise StreamSessionException("Couldn't get ipid / fingerprint")

        logger.info("logged in: %s" %(self.ipid))
        self.save()

    @property
    def logged_in(self):

        logged_in_url = ("https://web-secure.mlb.com/enterworkflow.do"
                         "?flowId=registration.newsletter&c_id=mlb")
        content = self.get(logged_in_url).text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)
        if "Login/Register" in data.xpath(".//title")[0].text:
            return False

    @property
    def headers(self):

        return {
            "Authorization": self.access_token
        }


    @property
    def ipid(self):
        return self.get_cookie("ipid")

    @property
    def fingerprint(self):
        return self.get_cookie("fprt")

    @property
    def api_key(self):

        if not self._state.get("api_key"):
            self.update_api_keys()
        return self._state.api_key

    @property
    def client_api_key(self):

        if not self._state.get("client_api_key"):
            self.update_api_keys()
        return self._state.client_api_key

    def update_api_keys(self):

        logger.debug("updating api keys")
        content = self.get("https://www.mlb.com/tv/g490865/").text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)

        scripts = data.xpath(".//script")
        for script in scripts:
            if script.text and "apiKey" in script.text:
                self._state.api_key = self.API_KEY_RE.search(script.text).groups()[0]
            if script.text and "clientApiKey" in script.text:
                self._state.client_api_key = self.CLIENT_API_KEY_RE.search(script.text).groups()[0]
        self.save()

    @property
    def token(self):
        if not self._state.token:
            logger.debug("getting token")
            headers = {"x-api-key": self.api_key}

            response = self.get(
                self.TOKEN_URL_TEMPLATE.format(
                    ipid=self.ipid, fingerprint=self.fingerprint,
                    platform=self.PLATFORM
                ),
                headers=headers
            )
            self._state.token = response.text
        return self._state.token

    @token.setter
    def token(self, value):
        self._state.token = value

    @property
    def access_token_expiry(self):

        if self._state.access_token_expiry:
            return dateutil.parser.parse(self._state.access_token_expiry)

    @access_token_expiry.setter
    def access_token_expiry(self, val):
        if val:
            self._state.access_token_expiry = val.isoformat()

    @property
    def access_token(self):
        if not self._state.access_token or not self.access_token_expiry or \
                self.access_token_expiry < datetime.now(tz=pytz.UTC):
            try:
                self.refresh_access_token()
            except requests.exceptions.HTTPError:
                # Clear token and then try to get a new access_token
                self.refresh_access_token(clear_token=True)

        logger.debug("access_token: %s" %(self._state.access_token))
        return self._state.access_token

    def refresh_access_token(self, clear_token=False):
        if not self.logged_in:
            self.login()
        logger.debug("refreshing access token")
        if clear_token:
            self.token = None
        headers = {
            "Authorization": "Bearer %s" % (self.client_api_key),
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": self.BAM_SDK_VERSION,
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "platform": "browser",
            "setCookie": "false",
            "subject_token": self.token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt"
        }
        response = self.post(
            self.ACCESS_TOKEN_URL,
            data=data,
            headers=headers
        )
        # from requests_toolbelt.utils import dump
        # print(dump.dump_all(response).decode("utf-8"))
        response.raise_for_status()
        token_response = response.json()

        self.access_token_expiry = datetime.now(tz=pytz.UTC) + \
                       timedelta(seconds=token_response["expires_in"])
        self._state.access_token = token_response["access_token"]
        self.save()

    def content(self, game_id):

        return self.get(
            self.GAME_CONTENT_URL_TEMPLATE.format(game_id=game_id)).json()


    def airings(self, game_id):

        airings_url = self.AIRINGS_URL_TEMPLATE.format(game_id = game_id)
        airings = self.get(
            airings_url
        ).json()["data"]["Airings"]
        return airings


    def media_timestamps(self, game_id, media_id):

        try:
            airing = next(a for a in self.airings(game_id)
                          if a["mediaId"] == media_id)
        except StopIteration:
            raise StreamSessionException("No airing for media %s" %(media_id))

        start_timestamps = []
        try:
            start_time = next(
                    t["startDatetime"] for t in
                    next(m for m in airing["milestones"]
                     if m["milestoneType"] == "BROADCAST_START"
                    )["milestoneTime"]
                if t["type"] == "absolute"
                )

        except StopIteration:
            # Some streams don't have a "BROADCAST_START" milestone.  We need
            # something, so we use the scheduled game start time, which is
            # probably wrong.
            start_time = airing["startDate"]

        start_timestamps.append(
            ("S", start_time)
        )

        try:
            start_offset = next(
                t["start"] for t in
                next(m for m in airing["milestones"]
                     if m["milestoneType"] == "BROADCAST_START"
                )["milestoneTime"]
                if t["type"] == "offset"
            )
        except StopIteration:
            # Same as above.  Missing BROADCAST_START milestone means we
            # probably don't get accurate offsets for inning milestones.
            start_offset = 0

        start_timestamps.append(
            ("SO", start_offset)
        )

        timestamps = AttrDict(start_timestamps)
        timestamps.update(AttrDict([
            (
            "%s%s" %(
                "T"
                if next(
                        k for k in m["keywords"]
                        if k["type"] == "top"
                )["value"] == "true"
                else "B",
                int(
                    next(
                        k for k in m["keywords"] if k["type"] == "inning"
                    )["value"]
                )),
            next(t["start"]
                      for t in m["milestoneTime"]
                      if t["type"] == "offset"
                 )
            )
                 for m in airing["milestones"]
                 if m["milestoneType"] == "INNING_START"
        ]))
        return timestamps

    def get_stream(self, media):

        media_id = media.get("mediaId", media.get("guid"))

        headers={
            "Authorization": self.access_token,
            "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": "3.0",
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream_url = self.STREAM_URL_TEMPLATE.format(media_id=media_id)
        logger.info("getting stream %s" %(stream_url))
        stream = self.get(
            stream_url,
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            return None
        stream = AttrDict(stream)
        stream.url = stream["stream"]["complete"]
        return stream


class MLBLineScoreDataTable(DataTable):

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

            if isinstance(line_score["innings"], list):
                for i, inning in enumerate(line_score["innings"]):
                    if not s:
                        columns.append(
                            DataTableColumn(str(i+1), label=str(i+1), width=3)
                        )
                        line.team = away_team
                    else:
                        line.team = home_team

                    if hide_spoilers:
                        setattr(line, str(i+1), "?")

                    elif side in inning:
                        if isinstance(inning[side], dict) and "runs" in inning[side]:
                            setattr(line, str(i+1), parse_int(inning[side]["runs"]))
                        # else:
                        #     if "runs" in inning[side]:
                        #         inning_score.append(parse_int(inning[side]))
                    else:
                        setattr(line, str(i+1), "X")

                for n in range(i+1, 9):
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

            for stat in ["runs", "hits", "errors"]:
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


class BasePopUp(urwid.WidgetWrap):

    signals = ["close_popup"]

    def selectable(self):
        return True


class MLBWatchDialog(BasePopUp):

    signals = ["watch"]

    def __init__(self, game_id,
                 resolution=None, from_beginning=None):

        self.game_id = game_id
        self.resolution = resolution
        self.from_beginning = from_beginning

        self.game_data = self.session.schedule(
            game_id=self.game_id,
        )["dates"][0]["games"][0]
        # raise Exception(self.game_data)

        self.title = urwid.Text("%s@%s" %(
            self.game_data["teams"]["away"]["team"]["abbreviation"],
            self.game_data["teams"]["home"]["team"]["abbreviation"],
        ))

        feed_map = sorted([
            ("%s (%s)" %(e["mediaFeedType"].title(),
                         e["callLetters"]), e["mediaId"].lower())
            for e in self.session.get_media(self.game_id)
        ], key=lambda v: v[0])
        home_feed = next(self.session.get_media(
            self.game_id,
            preferred_stream = "home"
        ))
        self.live_stream = (home_feed.get("mediaState") == "MEDIA_ON")
        self.feed_dropdown = Dropdown(
            feed_map,
            label="Feed",
            default=home_feed["mediaId"]
        )
        urwid.connect_signal(
            self.feed_dropdown,
            "change",
            lambda s, b, media_id: self.update_inning_dropdown(media_id)
        )

        self.resolution_dropdown = ResolutionDropdown(
            default=resolution
        )

        self.inning_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.update_inning_dropdown(self.feed_dropdown.selected_value)

        self.ok_button = urwid.Button("OK")
        urwid.connect_signal(self.ok_button, "click", self.watch)

        self.cancel_button = urwid.Button("Cancel")
        urwid.connect_signal(
            self.cancel_button, "click",
            lambda b: urwid.signals.emit_signal(self, "close_popup")
        )

        pile = urwid.Pile([
            ("pack", self.title),
            ("weight", 1, urwid.Pile([
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                        ("weight", 1, self.feed_dropdown),
                        ("weight", 1, self.resolution_dropdown),
                    ]))),
                ("weight", 1, urwid.Filler(self.inning_dropdown_placeholder)),
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                    ("weight", 1, self.ok_button),
                    ("weight", 1, self.cancel_button),
                ])))
            ]))
        ])
        super(MLBWatchDialog, self).__init__(pile)

    def update_inning_dropdown(self, media_id):
        # raise Exception(media_id)
        self.timestamps = self.session.media_timestamps(
            self.game_id, media_id
        )
        del self.timestamps["S"]
        timestamp_map = AttrDict(
            ( k if k[0] in "TB" else "Start", k ) for k in self.timestamps.keys()
        )
        timestamp_map["Live"] = False
        self.inning_dropdown = Dropdown(
            timestamp_map, label="Begin playback",
            default = (
                timestamp_map["Start"] if (
                    not self.live_stream or self.from_beginning
                ) else timestamp_map["Live"]
            )
        )
        self.inning_dropdown_placeholder.original_widget = self.inning_dropdown


    def watch(self, source):
        urwid.signals.emit_signal(
            self,
            "watch",
            self.game_id,
            self.resolution_dropdown.selected_value,
            self.feed_dropdown.selected_value,
            self.inning_dropdown.selected_value
        )
        urwid.signals.emit_signal(self, "close_popup")

    def keypress(self, size, key):

        if key == "meta enter":
            self.ok_button.keypress(size, "enter")
        elif key in ["<", ">"]:
            self.resolution_dropdown.cycle(1 if key == "<" else -1)
        elif key in ["[", "]"]:
            self.feed_dropdown.cycle(-1 if key == "[" else 1)
        elif key in ["-", "="]:
            self.inning_dropdown.cycle(-1 if key == "-" else 1)
        else:
            # return super(MLBWatchDialog, self).keypress(size, key)
            key = super(MLBWatchDialog, self).keypress(size, key)
        if key:
            return
        return key


class MLBProvider(SimpleProviderViewMixin,
                  BAMProviderMixin,
                  BaseProvider):

    SESSION_CLASS = MLBStreamSession

    RESOLUTIONS = AttrDict([
        ("720p", "720p_alt"),
        ("720p@30", "720p"),
        ("540p", "540p"),
        ("504p", "504p"),
        ("360p", "360p"),
        ("288p", "288p"),
        ("224p", "224p")
    ])

    FILTERS = AttrDict([
        ("date", DateFilter),
        ("resolution", ResolutionFilter)
    ])

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    DATA_TABLE_CLASS = MLBLineScoreDataTable


    def teams(self, sport_code="mlb", season=None):

        if sport_code != "mlb":
            media_title = "MiLBTV"
            raise SGException("Sorry, MiLB.tv streams are not yet supported")

        sports_url = (
            "http://statsapi.mlb.com/api/v1/sports"
        )
        with state.session.cache_responses_long():
            sports = state.session.get(sports_url).json()

        sport = next(s for s in sports["sports"] if s["code"] == sport_code)

        # season = game_date.year
        teams_url = (
            "http://statsapi.mlb.com/api/v1/teams"
            "?sportId={sport}&{season}".format(
                sport=sport["id"],
                season=season if season else ""
            )
        )

        # raise Exception(state.session.get(teams_url).json())
        with state.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
                for team in sorted(state.session.get(teams_url).json()["teams"],
                                   key=lambda t: t["fileCode"])
            )

        return teams

    def get_stream(self, media):

        media_id = media.get("mediaId", media.get("guid"))

        headers={
            "Authorization": self.session.access_token,
            # "User-agent": USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": "3.0",
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream_url = self.STREAM_URL_TEMPLATE.format(media_id=media_id)
        logger.info("getting stream %s" %(stream_url))
        stream = self.get(
            stream_url,
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            return None
        stream = Stream(stream)
        stream.url = stream["stream"]["complete"]
        return stream




# register_provider(MLBProvider)
