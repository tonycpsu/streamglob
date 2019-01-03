import logging
logger = logging.getLogger(__name__)

import abc

import urwid
import panwid
from orderedattrdict import AttrDict
from datetime import datetime, timedelta
import dateutil.parser
import pytz
import distutils.spawn


from .. import player
from .. import config
from .. import model
from .base import *
from .filters import *
from ..player import *
from .widgets import *


class BAMProviderData(model.ProviderData):

    season_year = Required(int)
    start = Required(datetime)
    end = Required(datetime)

    composite_key(model.ProviderData.classtype, season_year)


def parse_int(n):
    try:
        return int(n)
    except ValueError:
        return n
    except TypeError:
        return None


class MediaAttributes(AttrDict):

    def __repr__(self):
        state = "!" if self.state == "MEDIA_ON" else "."
        free = "_" if self.free else "$"
        return f"{state}{free}"

def format_start_time(d):
    s = datetime.strftime(d, "%I:%M%p").lower()[:-1]
    if s[0] == "0":
        s = s[1:]
    return s

class BasePopUp(urwid.WidgetWrap):

    signals = ["close_popup"]

    def selectable(self):
        return True

class BAMDateFilter(DateFilter):

    @property
    def widget_kwargs(self):
        return {"initial_date": self.provider.start_date}



class OffsetDropdown(urwid.WidgetWrap):


    def __init__(self, timestamps, live=False, default=None):

        del timestamps["S"]
        timestamp_map = AttrDict(

            ( "Start" if k == "SO" else k, v ) for k, v in timestamps.items()
        )
        if live:
            timestamp_map["Live"] = False

        self.dropdown = panwid.Dropdown(
            timestamp_map, label="Begin playback",
            default = timestamp_map[default]
        )
        super().__init__(self.dropdown)

    @property
    def selected_value(self):
        return self.dropdown.selected_value




class WatchDialog(BasePopUp):

    signals = ["play"]

    def __init__(self,
                 provider,
                 selection,
                 media_title=None,
                 default_resolution=None,
                 watch_live=None):

        self.provider = provider
        self.game_id = selection["game_id"]
        self.media_title = media_title
        self.default_resolution = default_resolution
        self.watch_live = watch_live

        self.title = urwid.Text("%s@%s" %(
            selection["away"],
            selection["home"]
        ))

        media = list(self.provider.get_media(self.game_id, title=self.media_title))
        # raise Exception(media)
        feed_map = sorted([
            ("%s (%s)" %(e["mediaFeedType"].title(),
                         e["callLetters"]), e["mediaId"].lower())
            for e in media
        ], key=lambda v: v[0])

        try:
            home_feed = next(
                e for e in media
                if e["mediaFeedType"].lower() == "home"
            )
        except StopIteration:
            home_feed = media[0]

        self.live_stream = (home_feed.get("mediaState") == "MEDIA_ON")
        self.feed_dropdown = panwid.Dropdown(
            feed_map,
            label="Feed",
            default=home_feed["mediaId"]
        )
        urwid.connect_signal(
            self.feed_dropdown,
            "change",
            lambda s, b, media_id: self.update_offset_dropdown(media_id)
        )

        # self.resolution_dropdown = ResolutionDropdown(
        #     default=resolution
        # )

        self.resolution_dropdown = panwid.Dropdown(
            self.provider.RESOLUTIONS, default=self.default_resolution
        )

        self.offset_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.update_offset_dropdown(self.feed_dropdown.selected_value)

        def ok(s):
            self.provider.play(
                selection,
                offset=self.offset_dropdown.selected_value,
                resolution=self.resolution_dropdown.selected_value
            )
            self._emit("close_popup")

        def cancel(s):
            self._emit("close_popup")

        self.ok_button = urwid.Button("OK")
        self.cancel_button = urwid.Button("Cancel")

        urwid.connect_signal(self.ok_button, "click", ok)
        urwid.connect_signal(self.cancel_button, "click", cancel)

        pile = urwid.Pile([
            ("pack", self.title),
            ("weight", 1, urwid.Pile([
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                        ("weight", 1, self.feed_dropdown),
                        ("weight", 1, self.resolution_dropdown),
                    ]))),
                ("weight", 1, urwid.Filler(self.offset_dropdown_placeholder)),
                ("weight", 1, urwid.Filler(
                    urwid.Columns([
                    ("weight", 1, self.ok_button),
                    ("weight", 1, self.cancel_button),
                ])))
            ]))
        ])
        super(WatchDialog, self).__init__(pile)
        pile.contents[1][0].focus_position = 2


    def update_offset_dropdown(self, media_id):

        self.offset_dropdown = OffsetDropdown(
            self.provider.media_timestamps(self.game_id, media_id),
            live = self.live_stream,
            default = "Live" if self.watch_live and self.live_stream else "Start"
        )
        self.offset_dropdown_placeholder.original_widget = self.offset_dropdown


    def keypress(self, size, key):

        if key == "meta enter":
            self.ok_button.keypress(size, "enter")
        elif key in ["<", ">"]:
            self.resolution_dropdown.cycle(1 if key == "<" else -1)
        elif key in ["[", "]"]:
            self.feed_dropdown.cycle(-1 if key == "[" else 1)
        elif key in ["-", "="]:
            self.offset_dropdown.cycle(-1 if key == "-" else 1)
        else:
            # return super(WatchDialog, self).keypress(size, key)
            key = super(WatchDialog, self).keypress(size, key)
        if key:
            return
        return key

class LiveStreamFilter(ListingFilter):

    @property
    def values(self):
        return AttrDict([
            ("Live", True),
            ("From Start", False),
        ])


class BAMProviderDataTable(ProviderDataTable):

    def keypress(self, size, key):
        if key in ["meta left", "meta right"]:
            self._emit(f"cycle_filter", 0,("w", -1 if key == "meta left" else 1))
        if key in ["ctrl left", "ctrl right"]:
            self._emit(f"cycle_filter", 0, ("m", -1 if key == "ctrl left" else 1))
        elif key == "meta enter":
            self.provider.play(self.selection.data)
        else:
            return super().keypress(size, key)



class BAMProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = BAMProviderDataTable

@with_view(BAMProviderView)
class BAMProviderMixin(abc.ABC):
    """
    StreamSession subclass for BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """
    sport_id = 1 # FIXME

    FILTERS = AttrDict([
        ("date", BAMDateFilter),
        ("resolution", ResolutionFilter),
        ("live_stream", LiveStreamFilter),
    ])

    ATTRIBUTES = AttrDict(
        attrs = {"width": 6},
        start = {"width": 6, "format_fn": format_start_time},
        away = {"width": 16},
        home = {"width": 16},
        line = {}
    )

    HELPER = "streamlink"

    REQUIRED_CONFIG = ["username", "password"]

    @property
    def config_is_valid(self):
        return (
            super().config_is_valid
            and
            self.HELPER in list(player.PLAYERS.keys())
        )

    # @memo(region="short")
    def schedule(
            self,
            # sport_id=None,
            season=None, # season only works for NHL
            start=None,
            end=None,
            game_type=None,
            team_id=None,
            game_id=None,
            brief=False
    ):

        logger.debug(
            "getting schedule: %s, %s, %s, %s, %s, %s, %s" %(
                self.sport_id,
                season,
                start,
                end,
                game_type,
                team_id,
                game_id
            )
        )
        if brief:
            template = self.SCHEDULE_TEMPLATE_BRIEF
        else:
            template = self.SCHEDULE_TEMPLATE

        url = template.format(
            sport_id = self.sport_id,
            season = season if season else "",
            start = start.strftime("%Y-%m-%d") if start else "",
            end = end.strftime("%Y-%m-%d") if end else "",
            game_type = game_type if game_type else "",
            team_id = team_id if team_id else "",
            game_id = game_id if game_id else ""
        )
        # with self.cache_responses_short():
        return self.session.get(url).json()


    def listings(self, offset=None, limit=None, *args, **kwargs):

        j = self.schedule(
        #     # sport_id=self.sport_id,
            start = self.filters.date.value,
            end = self.filters.date.value,
            # game_type = "R"
        )
        # logger.info(j)

        for d in j["dates"]:
            games = sorted(d["games"], key= lambda g: g["gameDate"])
            for g in games:
                logger.info(g)
                game_pk = g["gamePk"]
                game_type = g["gameType"]
                status = g["status"]["statusCode"]
                away_team = g["teams"]["away"]["team"]["teamName"]
                home_team = g["teams"]["home"]["team"]["teamName"]
                away_abbrev = g["teams"]["away"]["team"]["abbreviation"]
                home_abbrev = g["teams"]["home"]["team"]["abbreviation"]
                start_time = dateutil.parser.parse(g["gameDate"])
                attrs = MediaAttributes()
                try:
                    item = free_game = g["content"]["media"]["epg"][0]["items"][0]
                    attrs.state = item["mediaState"]
                    attrs.free = item["freeGame"]
                except:
                    attrs.state = None
                    attrs.free = None

                if config.settings.profile.time_zone:
                    start_time = start_time.astimezone(
                        pytz.timezone(config.settings.profile.time_zone)
                    )

                hide_spoiler_teams = config.settings.profile.get("hide_spoiler_teams", [])
                if isinstance(hide_spoiler_teams, bool):
                    hide_spoilers = hide_spoiler_teams
                else:
                    hide_spoilers = set([away_abbrev, home_abbrev]).intersection(
                        set(hide_spoiler_teams))
                if "linescore" in g:
                    self.line_score_table = self.DATA_TABLE_CLASS.from_json(
                            g["linescore"],
                            g["teams"]["away"]["team"]["abbreviation"],
                            g["teams"]["home"]["team"]["abbreviation"],
                            hide_spoilers
                    )
                    self.line_score = urwid.BoxAdapter(
                        self.line_score_table,
                        3
                    )
                else:
                    self.line_score = None

                yield(AttrDict(
                    game_id = game_pk,
                    game_type = game_type,
                    away = away_team,
                    home = home_team,
                    start = start_time,
                    line = self.line_score,
                    attrs = attrs
                ))


    # @memo(region="short")
    def get_epgs(self, game_id, title=None):

        schedule = self.schedule(game_id=game_id)
        try:
            # Get last date for games that have been rescheduled to a later date
            game = schedule["dates"][-1]["games"][0]
        except KeyError:
            logger.debug("no game data")
            return
        epgs = game["content"]["media"]["epg"]

        if not isinstance(epgs, list):
            epgs = [epgs]

        return [ e for e in epgs if (not title) or title == e["title"] ]

    def get_media(self,
                  game_id,
                  media_id=None,
                  title=None,
                  preferred_stream=None,
                  call_letters=None):

        logger.debug(f"geting media for game {game_id} ({media_id}, {title}, {call_letters})")

        epgs = self.get_epgs(game_id, title)
        # raise Exception(epgs)
        for epg in epgs:
            for item in epg["items"]:
                if "mediaId" not in item:
                    item["mediaId"] = item.get("guid", "")
                if (not preferred_stream
                    or (item.get("mediaFeedType", "").lower() == preferred_stream)
                ) and (
                    not call_letters
                    or (item.get("callLetters", "").lower() == call_letters)
                ) and (
                    not media_id
                    or (item.get("mediaId", "").lower() == media_id)
                ):
                    logger.debug("found preferred stream")
                    yield AttrDict(item)
            else:
                if len(epg["items"]):
                    logger.debug("using non-preferred stream")
                    yield AttrDict(epg["items"][0])
        # raise StopIteration


    def get_url(self, game_specifier,
                offset=None,
                media_id = None,
                preferred_stream=None,
                call_letters=None,
                output=None,
                verbose=0):

        live = False
        team = None
        game_number = 1
        game_date = None
        # sport_code = "mlb" # default sport is MLB

        # media_title = "MLBTV"
        media_id = None
        allow_stdout=False

        if isinstance(game_specifier, int):
            game_id = game_specifier
            schedule = self.schedule(
                game_id = game_id
            )

        else:
            try:
                (game_date, team, game_number) = game_specifier.split(".")
            except ValueError:
                try:
                    (game_date, team) = game_specifier.split(".")
                except ValueError:
                    game_date = datetime.now().date()
                    team = game_specifier

            if "-" in team:
                (sport_code, team) = team.split("-")

            game_date = dateutil.parser.parse(game_date)
            game_number = int(game_number)
            teams =  self.teams(season=game_date.year)
            team_id = teams.get(team)

            if not team:
                msg = "'%s' not a valid team code, must be one of:\n%s" %(
                    game_specifier, " ".join(teams)
                )
                raise argparse.ArgumentTypeError(msg)

            schedule = self.schedule(
                start = game_date,
                end = game_date,
                # sport_id = sport["id"],
                team_id = team_id
            )
            # raise Exception(schedule)


        try:
            date = schedule["dates"][-1]
            game = date["games"][game_number-1]
            game_id = game["gamePk"]
        except IndexError:
            raise SGException("No game %d found for %s on %s" %(
                game_number, team, game_date)
            )
        away_team_abbrev = game["teams"]["away"]["team"]["abbreviation"].lower()
        home_team_abbrev = game["teams"]["home"]["team"]["abbreviation"].lower()

        if not preferred_stream or call_letters:
            preferred_stream = (
                "away"
                if team == away_team_abbrev
                else "home"
            )

        try:
            media = next(self.get_media(
                game_id,
                media_id = media_id,
                # title=media_title,
                preferred_stream=preferred_stream,
                call_letters = call_letters
            ))
        except StopIteration:
            raise SGException("no matching media for game %d" %(game_id))

        # media_id = media["mediaId"] if "mediaId" in media else media["guid"]

        media_state = media["mediaState"]

        # Get any team-specific profile overrides, and apply settings for them
        profiles = tuple([ list(d.values())[0]
                     for d in config.settings.profile_map.get("team", {})
                     if list(d.keys())[0] in [
                             away_team_abbrev, home_team_abbrev
                     ] ])

        if len(profiles):
            # override proxies for team, if defined
            if len(config.settings.profiles[profiles].proxies):
                old_proxies = self.session.proxies
                self.session.proxies = config.settings.profiles[profiles].proxies
                # self.session.refresh_access_token(clear_token=True)
                self.session.proxies = old_proxies

        if "playbacks" in media:
            playback = media["playbacks"][0]
            media_url = playback["location"]
        else:
            stream = self.session.get_stream(media)

            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d" %(game_id))

        return media_url

    @abc.abstractmethod
    def teams(self, season=None):
        pass

    @property
    @abc.abstractmethod
    def start_date(self):
        pass

    def on_select(self, widget, selection):
        self.open_watch_dialog(selection)

    def open_watch_dialog(self, selection):
        media = list(self.get_media(selection["game_id"]))
        dialog = WatchDialog(self,
                             selection,
                             media_title = self.MEDIA_TITLE,
                             default_resolution = self.filters.resolution.value,
                             watch_live = self.filters.live_stream.value
        )
        self.view.open_popup(dialog, width=30, height=20)

        # self.play(selection)


    def play_args(self, selection, **kwargs):

        game_id = selection.get("game_id")

        url = self.get_url(game_id)
        args = [url]
        # if not "resolution" in kwargs:
        kwargs["resolution"] = self.filters.resolution.value
        offset = kwargs.pop("offset", None)
        if offset:
            if (selection.attrs.state == "MEDIA_ON"): # live stream
                logger.debug("live stream")
                # calculate HLS offset, which is negative from end of stream
                # for live streams
                # start_time = dateutil.parser.parse(timestamps["S"])
                start_time = dateutil.parser.parse(selection.start_time)
                offset_delta = (
                    datetime.now(pytz.utc)
                    - start_time.astimezone(pytz.utc)
                    + (timedelta(seconds=-offset))
                )
            else:
                logger.debug("recorded stream")
                offset_delta = timedelta(seconds=offset)

            # offset_seconds = offset_delta.seconds
            kwargs["offset"] = offset_delta.seconds

        kwargs["headers"] = self.session.headers
        kwargs["cookies"] = self.session.cookies

        return (args, kwargs)
