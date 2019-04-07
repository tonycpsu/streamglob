import logging
logger = logging.getLogger(__name__)

import urwid

from orderedattrdict import AttrDict
from panwid.datatable import *
from pony.orm import *
import requests
import dateutil.parser
import random
import string

from .. import session

from .base import *
from .bam import *
from .filters import *

from .. import model
from ..exceptions import *
from ..state import *

def gen_random_string(n):
    return ''.join(
        random.choice(
            string.ascii_uppercase + string.digits
        ) for _ in range(64)
    )

class MLBLineScoreDataTable(BAMLineScoreDataTable):

    SCORING_ATTRS = ["runs", "hits", "errors"]
    PLAYING_PERIOD_ATTR = "innings"
    NUM_PLAYING_PERIODS = 9

    @classmethod
    def PLAYING_PERIOD_DESC(cls, line_score):
        return f"""{line_score.get("inningHalf")[:3]} {line_score.get("currentInningOrdinal")}"""

class MLBHighlightsDataTable(HighlightsDataTable):

    columns = [
        DataTableColumn("inning", width=5,
                        value = lambda t, r: r.data.attrs.inning),
        # DataTableColumn("top_play", width=5,
        #                 value = lambda t, r: r.data.attrs.top_play),
    ] + HighlightsDataTable.COLUMNS



class MLBDetailBox(BAMDetailBox):

    HIGHLIGHT_TABLE_CLASS = MLBHighlightsDataTable

    EVENT_TYPES = AttrDict(
        hitting="H",
        pitching="P",
        defense="F",
        baserunning="R"
    )

    def get_editorial_item(self, editorial):
        return editorial.get("mlb", None)

    def __repr__(self):
        return ""

@dataclass
class MLBMediaListing(BAMMediaListing):

    @property
    def line(self):
        style = self.provider.config.listings.line.style
        table = MLBLineScoreDataTable.for_game(
            self.provider, self.game_data, self.hide_spoilers,
            # style = style
        )
        return BAMLineScoreBox(table, style)

    @property
    def HIGHLIGHT_ATTR(self):
        return "highlights"


    def get_highlight_attrs(self, highlight):

        timestamp = None
        running_time = None
        event_type = None
        inning = None

        plays = self.plays
        keywords = highlight.get("keywordsAll", None)

        game_start = dateutil.parser.parse(
            self.game_data["gameDate"]
        )

        try:
            event_id = next(k["value"] for k in keywords if k["type"] == "sv_id")
        except StopIteration:
            event_id = None

        try:
            play, event = next( (p, pe) for p in plays
                        for pe in p["playEvents"]
                        if event_id and pe.get("playId", None) == event_id)
        except StopIteration:
            play = None
            event = None

        if play:
            event_type = play["result"].get("event", None)

            timestamp = dateutil.parser.parse(play["about"].get(
                    "startTime", None)
            ).astimezone(
                pytz.timezone(config.settings.profile.time_zone)
            )

            running_time = timestamp - game_start
            inning = f"{play['about']['halfInning'][:3].title()} {play['about']['inning']}"

        if not event_type:
            if any((k["type"] == "mlbtax"
                   and k["displayName"] == "Interview"
                   for k in keywords)):
                event_type = "Interview"
            elif any((k["type"] == "mlbtax"
                   and k["displayName"] == "Managers"
                   for k in keywords)):
                event_type = "Postgame"
            elif any((k["type"] == "mlbtax"
                   and k["displayName"] == "Managers"
                   for k in keywords)):
                event_type = "News Conference"
            else:
                event_type = "Other"

        return AttrDict(
            timestamp = timestamp,
            running_time = running_time,
            event_type = event_type,
            inning = inning
            # top_play = top_play,
            # description = play["result"].get("description", None),
        )


@dataclass
class MLBMediaSource(BAMMediaSource):

    @property
    def milestones(self):

        try:
            # try to get the precise timestamps for this stream
            airing = next(a for a in self.provider.session.airings(self.game_id)
                          if len(a["milestones"])
                          and a["mediaId"] == self.media_id)
        except StopIteration:
            # welp, no timestamps -- try to get them from whatever feed has them
            try:
                airing = next(a for a in self.provider.session.airings(self.game_id)
                            if len(a["milestones"]))
            except StopIteration:
                logger.warning(SGStreamSessionException(
                    "No airing for media %s" %(self.media_id))
                )
                return AttrDict([("Start", 0)])

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

        # start_timestamps.append(
        #     ("Start", start_time)
        # )

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
            ("Start", start_offset)
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


class MLBBAMProviderData(BAMProviderData):
    pass

class MLBStreamSession(session.AuthenticatedStreamSession):

    PLATFORM = "macintosh"

    BAM_SDK_VERSION = "3.4"

    MLB_API_KEY_URL = "https://www.mlb.com/tv/g490865/"

    API_KEY_RE = re.compile(r'"apiKey":"([^"]+)"')

    CLIENT_API_KEY_RE = re.compile(r'"clientApiKey":"([^"]+)"')

    OKTA_CLIENT_ID_RE = re.compile("""production:{clientId:"([^"]+)",""")

    MLB_OKTA_URL = "https://www.mlbstatic.com/mlb.com/vendor/mlb-okta/mlb-okta.js"

    AUTHN_URL = "https://ids.mlb.com/api/v1/authn"

    AUTHZ_URL = "https://ids.mlb.com/oauth2/aus1m088yK07noBfh356/v1/authorize"

    BAM_DEVICES_URL = "https://us.edge.bamgrid.com/devices"

    BAM_SESSION_URL = "https://us.edge.bamgrid.com/session"

    BAM_TOKEN_URL = "https://us.edge.bamgrid.com/token"

    BAM_ENTITLEMENT_URL = "https://media-entitlement.mlb.com/api/v3/jwt"

    GAME_CONTENT_URL_TEMPLATE="http://statsapi.mlb.com/api/v1/game/{game_id}/content"

    STREAM_URL_TEMPLATE="https://edge.svcs.mlb.com/media/{media_id}/scenarios/browser~csai"

    AIRINGS_URL_TEMPLATE=(
        "https://search-api-mlbtv.mlb.com/svc/search/v2/graphql/persisted/query/"
        "core/Airings?variables={{%22partnerProgramIds%22%3A[%22{game_id}%22]}}"
    )

    def __init__(
            self,
            provider_id,
            username, password,
            api_key=None,
            client_api_key=None,
            okta_client_id=None,

            session_token=None,
            access_token=None,
            access_token_expiry=None,
            *args, **kwargs
    ):
        super(MLBStreamSession, self).__init__(
            provider_id,
            username, password,
            *args, **kwargs
        )
        self._state.api_key = api_key
        self._state.client_api_key = client_api_key
        self._state.okta_client_id = okta_client_id

        self._state.session_token = session_token

        self._state.session_token = session_token
        self._state.access_token = access_token
        self._state.access_token_expiry = access_token_expiry


    def login(self):

        if self.logged_in:
            logger.debug("already logged in")
            return

        AUTHN_PARAMS = {
            "username": self.username,
            "password": self.password,
            "options": {
                "multiOptionalFactorEnroll": False,
                "warnBeforePasswordExpired": True
            }
        }
        authn_response = self.post(self.AUTHN_URL, json=AUTHN_PARAMS).json()
        self.session_token = authn_response["sessionToken"]

        # logger.debug("logged in: %s" %(self.ipid))
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
    def session_token(self):
        return self._state.session_token

    @session_token.setter
    def session_token(self, value):
        self._state.session_token = value


    # @property
    # def ipid(self):
    #     return self.get_cookie("ipid")

    # @property
    # def fingerprint(self):
    #     return self.get_cookie("fprt")

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

    @property
    def okta_client_id(self):

        if not self._state.get("okta_client_id"):
            self.update_api_keys()
        return self._state.okta_client_id

    def update_api_keys(self):

        logger.debug("updating MLB api keys")
        content = self.get(self.MLB_API_KEY_URL).text
        parser = lxml.etree.HTMLParser()
        data = lxml.etree.parse(StringIO(content), parser)

        scripts = data.xpath(".//script")
        for script in scripts:
            if script.text and "apiKey" in script.text:
                self._state.api_key = self.API_KEY_RE.search(script.text).groups()[0]
            if script.text and "clientApiKey" in script.text:
                self._state.client_api_key = self.CLIENT_API_KEY_RE.search(script.text).groups()[0]

        logger.debug("updating Okta api keys")
        content = self.get(self.MLB_OKTA_URL).text
        self._state.okta_client_id = self.OKTA_CLIENT_ID_RE.search(content).groups()[0]
        self.save()

    @property
    def session_token(self):
        if not self._state.session_token:
            self.login()
        if not self._state.session_token:
            raise Exception("no session token")
        return self._state.session_token

    @session_token.setter
    def session_token(self, value):
        self._state.session_token = value

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

        logger.trace("access_token: %s" %(self._state.access_token))
        return self._state.access_token


    def refresh_access_token(self, clear_token=False):
        logger.debug("refreshing access token")

        if clear_token:
            self.session_token = None

        # ----------------------------------------------------------------------
        # Okta authentication -- used to get media entitlement later
        # ----------------------------------------------------------------------

        def get_okta_token():

            STATE = gen_random_string(64)
            NONCE = gen_random_string(64)

            AUTHZ_PARAMS = {
                "client_id": self.okta_client_id,
                "redirect_uri": "https://www.mlb.com/login",
                "response_type": "id_token token",
                "response_mode": "okta_post_message",
                "state": STATE,
                "nonce": NONCE,
                "prompt": "none",
                "sessionToken": self.session_token,
                "scope": "openid email"
            }
            authz_response = self.get(self.AUTHZ_URL, params=AUTHZ_PARAMS)
            authz_content = authz_response.text
            for line in authz_content.split("\n"):
                if "data.access_token" in line:
                    return line.split("'")[1].encode('utf-8').decode('unicode_escape')
                elif "data.error = 'login_required'" in line:
                    raise SGProviderLoginException
            raise Exception("could not authenticate: {authz_contet}")

        try:
            self.OKTA_ACCESS_TOKEN = get_okta_token()
        except SGProviderLoginException:
            # not logged in -- get session token and try again
            self.login()
            self.OKTA_ACCESS_TOKEN = get_okta_token()

        assert self.OKTA_ACCESS_TOKEN is not None

        # ----------------------------------------------------------------------
        # Get device assertion - used to get device token
        # ----------------------------------------------------------------------
        DEVICES_HEADERS = {
            "Authorization": "Bearer %s" % (self.client_api_key),
            "Origin": "https://www.mlb.com",
        }

        DEVICES_PARAMS = {
            "applicationRuntime": "firefox",
            "attributes": {},
            "deviceFamily": "browser",
            "deviceProfile": "macosx"
        }

        devices_response = self.post(
            self.BAM_DEVICES_URL,
            headers=DEVICES_HEADERS, json=DEVICES_PARAMS
        ).json()

        DEVICES_ASSERTION=devices_response["assertion"]

        # ----------------------------------------------------------------------
        # Get device token
        # ----------------------------------------------------------------------

        TOKEN_PARAMS = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "latitude": "0",
            "longitude": "0",
            "platform": "browser",
            "subject_token": DEVICES_ASSERTION,
            "subject_token_type": "urn:bamtech:params:oauth:token-type:device"
        }
        token_response = self.post(
            self.BAM_TOKEN_URL, headers=DEVICES_HEADERS, data=TOKEN_PARAMS
        ).json()


        DEVICE_ACCESS_TOKEN = token_response["access_token"]
        DEVICE_REFRESH_TOKEN = token_response["refresh_token"]

        # ----------------------------------------------------------------------
        # Create session -- needed for device ID, which is used for entitlement
        # ----------------------------------------------------------------------
        SESSION_HEADERS = {
            "Authorization": DEVICE_ACCESS_TOKEN,
            "User-agent": session.USER_AGENT,
            "Origin": "https://www.mlb.com",
            "Accept": "application/vnd.session-service+json; version=1",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "x-bamsdk-version": self.BAM_SDK_VERSION,
            "x-bamsdk-platform": self.PLATFORM,
            "Content-type": "application/json",
            "TE": "Trailers"
        }
        session_response = self.get(
            self.BAM_SESSION_URL,
            headers=SESSION_HEADERS
        ).json()
        DEVICE_ID = session_response["device"]["id"]

        # ----------------------------------------------------------------------
        # Get entitlement token
        # ----------------------------------------------------------------------
        ENTITLEMENT_PARAMS={
            "os": self.PLATFORM,
            "did": DEVICE_ID,
            "appname": "mlbtv_web"
        }

        ENTITLEMENT_HEADERS = {
            "Authorization": "Bearer %s" % (self.OKTA_ACCESS_TOKEN),
            "Origin": "https://www.mlb.com",
            "x-api-key": self.api_key

        }
        entitlement_response = self.get(
            self.BAM_ENTITLEMENT_URL,
            headers=ENTITLEMENT_HEADERS,
            params=ENTITLEMENT_PARAMS
        )

        ENTITLEMENT_TOKEN = entitlement_response.content

        # ----------------------------------------------------------------------
        # Finally (whew!) get access token using entitlement token
        # ----------------------------------------------------------------------
        headers = {
            "Authorization": "Bearer %s" % (self.client_api_key),
            "User-agent": session.USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": self.BAM_SDK_VERSION,
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "platform": "browser",
            "subject_token": ENTITLEMENT_TOKEN,
            "subject_token_type": "urn:bamtech:params:oauth:token-type:account"
        }
        response = self.post(
            self.BAM_TOKEN_URL,
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


    def get_stream(self, media):

        headers={
            "Authorization": self.access_token,
            "User-agent": session.USER_AGENT,
            "Accept": "application/vnd.media-service+json; version=1",
            "x-bamsdk-version": self.BAM_SDK_VERSION,
            "x-bamsdk-platform": self.PLATFORM,
            "origin": "https://www.mlb.com"
        }
        stream_url = self.STREAM_URL_TEMPLATE.format(media_id=media.media_id)
        logger.debug("getting stream %s" %(stream_url))
        stream = self.get(
            stream_url,
            headers=headers
        ).json()
        logger.debug("stream response: %s" %(stream))
        if "errors" in stream and len(stream["errors"]):
            raise SGStreamNotFound(stream["errors"])
        stream = AttrDict(stream)
        stream.url = stream["stream"]["complete"]
        return stream


class MLBProvider(BAMProviderMixin,
                  BaseProvider):

    SESSION_CLASS = MLBStreamSession

    MEDIA_TYPES = {"video"}

    RESOLUTIONS = AttrDict([
        ("720p", "720p_alt"),
        ("720p@30", "720p"),
        ("540p", "540p"),
        ("504p", "504p"),
        ("360p", "360p"),
        ("288p", "288p"),
        ("224p", "224p")
    ])

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg),"
        "editorial(preview,recap),highlights(highlights(items))))"
    )

    SCHEDULE_TEMPLATE_BRIEF = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
    )

    GAME_DATA_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    )

    # DATA_TABLE_CLASS = MLBLineScoreDataTable

    MEDIA_TITLE = "MLBTV"

    MEDIA_ID_FIELD = "mediaId"

    DETAIL_BOX_CLASS = MLBDetailBox

    URL_ROOT = "http://www.mlb.com"

    @classproperty
    def NAME(cls):
        return "MLB.tv"

    # @classmethod
    # def config_is_valid(cls, cfg):
    #     return all(c


    def teams(self, sport_code="mlb", season=None):

        if sport_code != "mlb":
            media_title = "MiLBTV"
            raise SGException("Sorry, MiLB.tv streams are not yet supported")

        sports_url = (
            "http://statsapi.mlb.com/api/v1/sports"
        )
        with self.session.cache_responses_long():
            sports = self.session.get(sports_url).json()

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

        with self.session.cache_responses_long():
            teams = AttrDict(
                (team["abbreviation"].lower(), team["id"])
                for team in sorted(self.session.get(teams_url).json()["teams"],
                                   key=lambda t: t["fileCode"])
            )

        return teams

    @property
    @db_session
    def start_date(self):

        now = self.current_game_day
        year = now.year
        season_year = (now - relativedelta(months=2)).year

        r = MLBBAMProviderData.get(season_year=season_year)
        if r:
            start = r.start
            end = r.end
        else:
            schedule = self.schedule(
                start=datetime(year, 1, 1),
                end=datetime(year, 12, 31),
                brief=True
            )
            start = dateutil.parser.parse(schedule["dates"][0]["date"])
            end = dateutil.parser.parse(schedule["dates"][-1]["date"])
            r = MLBBAMProviderData(
                season_year=season_year,
                start = start,
                end = end
            )

        if now < start.date():
            return start.date()
        elif now > end.date():
            return end.date()
        else:
            return now
