import logging
logger = logging.getLogger(__name__)

import abc

import urwid
from panwid import *
from orderedattrdict import AttrDict
from datetime import datetime, timedelta
import dateutil.parser
import pytz
import distutils.spawn
import dataclasses
from dataclasses import *
import typing

from .. import player
from .. import config
from .. import model
from .base import *
from .filters import *
from ..player import *
from .widgets import *


class BAMLineScoreBox(urwid.WidgetWrap):

    def __init__(self, columns, data, index):
        kw = {}
        if index:
            kw = dict(tlcorner="├", trcorner="┤")

        self.table = DataTable(columns, data=data)
        self.box = urwid.BoxAdapter(
            urwid.Filler(
                urwid.LineBox(
                    urwid.BoxAdapter(
                        self.table, 3,
                    ),
                    **kw
                )
            ), 4
        )
        super().__init__(self.box)

    def render(self, size, focus=False):
        self._width = size[0]
        if len(size) > 1:
            self._height = size[1]
        return super().render(size, focus)

    @property
    def width(self):
        return self._width

    @property
    def min_width(self):
        # raise Exception(self.table.width)
        # logger.info(f"{id(self.table)}")
        return self.table.min_width + 2
        # return self._width

    @property
    def height(self):
        return self._height

    def selectable(self):
        return True

    def keypress(self, size, key):
        return super().keypress(size, key)


class BAMLineScoreDataTable(DataTable):

    OVERTIME_LABEL = None

    # @classmethod
    # def for_game(
    #         cls, game, index, self.SCORING_ATTRS,
    #         self.PLAYING_PERIOD_ATTR, self.NUM_PLAYING_PERIODS, overtime_label = None
    # ):
    @classmethod
    def for_game(cls, game, index):

        line_score = game.get("linescore", None)

        primary_scoring_attr = cls.SCORING_ATTRS[0]
        # code = game["status"]["statusCode"]
        status = game["status"]["detailedState"]
        if status == "Scheduled":
            start_time = dateutil.parser.parse(game["gameDate"]).astimezone(
                pytz.timezone(config.settings.profile.time_zone)
            ).strftime("%I:%M%p").lower()
            status = start_time[1:] if start_time.startswith("0") else start_time
        elif status == "In Progress" and line_score:
            status = cls.PLAYING_PERIOD_DESC(line_score)

        status_str = f"""{status}"""

        columns = [
            DataTableColumn("team", width=10, label=status_str, align="right", padding=1),
            DataTableColumn("empty_1", label="", width=3)
        ]

        away_team = game["teams"]["away"]["team"]["abbreviation"]
        home_team = game["teams"]["home"]["team"]["abbreviation"]


        hide_spoiler_teams = config.settings.profile.get("hide_spoiler_teams", [])
        if isinstance(hide_spoiler_teams, bool):
            hide_spoilers = hide_spoiler_teams
        else:
            hide_spoilers = set([away_team, home_team]).intersection(
                set(hide_spoiler_teams))

        if not line_score:
            line_score = {
                "innings": [],
                "teams": {
                    "away": {},
                    "home": {}
                }
            }

        if "teams" in line_score:
            tk = line_score["teams"]
        else:
            tk = line_score

        data = []
        for s, side in enumerate(["away", "home"]):

            i = -1
            line = AttrDict()
            line.team = away_team if s == 0 else home_team

            if isinstance(line_score[cls.PLAYING_PERIOD_ATTR], list):
                for i, playing_period in enumerate(line_score[cls.PLAYING_PERIOD_ATTR]):
                    if not s:
                        columns.append(
                            DataTableColumn(
                                str(i+1),
                                label=(
                                    str(i+1)
                                    if (
                                            cls.OVERTIME_LABEL is None
                                            or i < cls.NUM_PLAYING_PERIODS
                                    )
                                    else cls.OVERTIME_LABEL
                                ),
                                width=3
                            )
                        )

                    if hide_spoilers:
                        setattr(line, str(i+1), "?")

                    elif side in playing_period:
                        if isinstance(playing_period[side], dict) and primary_scoring_attr in playing_period[side]:
                            setattr(line, str(i+1), parse_int(playing_period[side][primary_scoring_attr]))
                    else:
                        setattr(line, str(i+1), "X")

                for n in range(i+1, cls.NUM_PLAYING_PERIODS):
                    if not s:
                        columns.append(
                            DataTableColumn(str(n+1), label=str(n+1), width=3)
                        )
                    if hide_spoilers:
                        setattr(line, str(n+1), "?")

            if not s:
                columns.append(
                    DataTableColumn("empty_2", label="", width=3)
                )

            for stat in cls.SCORING_ATTRS:
                if not stat in tk[side]:# and len(data) > 0:
                    line[stat] = None
                    continue

                if not s:
                    columns.append(
                        DataTableColumn(stat, label=stat[0].upper(), width=3)
                    )
                if not hide_spoilers:
                    setattr(line, stat, parse_int(tk[side][stat]))
                else:
                    setattr(line, stat, "?")

            data.append(line)

        if not hide_spoilers and len(data):

            for s, side in enumerate(["away", "home"]):
                for i, playing_period in enumerate(line_score[cls.PLAYING_PERIOD_ATTR]):
                    if str(i+1) in data[s] and data[s][str(i+1)] == 0:
                        data[s][str(i+1)] = urwid.Text(("dim", str(data[s][str(i+1)])))

            if None not in [ data[i][primary_scoring_attr] for i in range(2) ]:
                if data[0][primary_scoring_attr] > data[1][primary_scoring_attr]:
                    data[0][primary_scoring_attr] = urwid.Text(("bold", str(data[0][primary_scoring_attr])))
                    # data[1][primary_scoring_attr] = urwid.Text(("dim", str(data[1][primary_scoring_attr])))
                elif data[1][primary_scoring_attr] > data[0][primary_scoring_attr]:
                    data[1][primary_scoring_attr] = urwid.Text(("bold", str(data[1][primary_scoring_attr])))
                    # data[0][primary_scoring_attr] = urwid.Text(("dim", str(data[0][primary_scoring_attr])))

        # return urwid.BoxAdapter(panwid.DataTable(columns, data=data), 3)
        return BAMLineScoreBox(columns, data, index)


class HighlightsDataTable(DataTable):

    with_header = False
    ui_sort = False

    columns = [
        DataTableColumn("title", value = "{data.title}: ({data.media_id})"),
        DataTableColumn("url", hide=True),
        # DataTableColumn("duration", width=10),
    ]

    def detail_fn(self, data):
        return urwid.Columns([
            (4, urwid.Text("")),
            ("weight", 1, urwid.Text(data.get("description")))
        ])

class BAMDetailBox(Observable, urwid.WidgetWrap):

    signals = ["play"]

    def __init__(self, game):
        self.game = game
        self.table = HighlightsDataTable(
            data= [
                AttrDict(dict(
                    media_id = h.get("guid", h.get("id")),
                    title = h["title"],
                    description = h["description"],
                    duration = h["duration"],
                    url = next(
                        p for p in h["playbacks"]
                        if p["name"] == "HTTP_CLOUD_WIRED_60"
                    )["url"],
                    _details_open = True

                )) for h in game["content"]["highlights"][self.HIGHLIGHT_ATTR]["items"]
            ]
        )

        self.columns = urwid.Columns([
            (4, urwid.Filler(urwid.Text(""))),
            ("weight", 1, self.table)
        ])
        self.attr = urwid.AttrMap(
            self.columns,
            # attr_map = {"table_row_body": "red"},
            attr_map = {},
            focus_map = {
                "table_row_body focused": "blue",
            },
        )
        self.box = urwid.BoxAdapter(self.attr, height=10)
        super().__init__(self.box)

    @property
    def HIGHLIGHT_ATTR(self):
        raise NotImplementedError

    def keypress(self, size, key):
        if key == "enter":
            logger.info(f"detail keypress: {key}")
            self.notify("play", self.table.selection.data)
            return
        else:
            key = super().keypress(size, key)
            return key

    def selectable(self):
        return True




@dataclass
class BAMMediaListing(model.MediaListing):

    FEED_TYPE_ORDER = [
        "away",
        "in_market_away",
        "home",
        "in_market_home",
        "national",
        "condensed",
        "recap",
        "..."
    ]

    game_id: int = None
    game_type: str = None
    away: int = None
    home: int = None
    away_abbrev: str = None
    home_abbrev: str = None
    start: datetime = None
    attrs: str = None
    index: int = None

    @classmethod
    def from_json(cls, provider, g, index):

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
            # FIXME: this is wrong
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

        return cls(
            provider_id = provider,
            game_id = game_pk,
            game_type = game_type,
            away = away_team,
            home = home_team,
            away_abbrev = away_abbrev,
            home_abbrev = home_abbrev,
            start = start_time,
            attrs = attrs,
            index = index
        )

    # FIXME
    # @property
    # def provider(self):
    #     return self.provider.NAME.lower()

    @property
    def start_date(self):
        return self.start.strftime("%Y%m%d")

    @property
    def start_time(self):
        return self.start.strftime("%H:%M:%S")

    @property
    def start_date_time(self):
        return f"{self.start_date}_{self.start_time}"

    @property
    def ext(self):
        return "mp4"

    @property
    def title(self):
        return f"{self.away}@{self.home}"

    @property
    def game_data(self):
        return self.provider.game_data(self.game_id)
        # schedule = self.provider.schedule(game_id=self.game_id)
        # try:
        #     # Get last date for games that have been rescheduled to a later date
        #     game = schedule["dates"][-1]["games"][0]
        # except KeyError:
        #     logger.warn("no game data for %s" %(self.game_id))
        # # logger.info(f"game: {game}")
        # return game

    @property
    @memo(region="short")
    def media(self):

        def fix_feed_type(feed_type, epg_title, title, description, blurb):
            # MLB-specific -- mediaSubType is sometimes a team ID instead
            # of away/home
            if feed_type and feed_type.isdigit():
                if int(feed_type) == game["teams"]["away"]["team"]["id"]:
                    return "AWAY"
                elif int(feed_type) == game["teams"]["home"]["team"]["id"]:
                    return "HOME"
            elif "Recap" in epg_title:
                return "Recap"
            elif "Highlights" in epg_title:
                if ("CG" in title
                    or "Condensed" in description
                    or "Condensed" in blurb):
                    return "Condensed"

            if feed_type is None:
                return title or "..."
            return feed_type


        logger.debug(f"geting media for game {self.game_id}")

        game = self.game_data

        try:
            epgs = (game["content"]["media"]["epg"]
                    + game["content"]["media"].get("epgAlternate", []))
        except KeyError:
            raise SGStreamNotFound("no matching media for game %d" %(self.game_id))

        # raise Exception(self.game_id, epgs)

        if not isinstance(epgs, list):
            epgs = [epgs]

        items = sorted(
            [ self.provider.new_media_source(
                # mediaId and guid fields are both used to identify streams
                # provider_id = self.provider_id,
                game_id = self.game_id,
                media_id = item.get(self.provider.MEDIA_ID_FIELD,
                                    item.get("guid", "")),
                title = item.get("title", ""),
                description = item.get("description", ""),
                state = (
                    "live" if (item.get("mediaState") == "MEDIA_ON")
                    else
                    "archive" if (item.get("mediaState") == "MEDIA_ARCHIVE")
                    else
                    "off" if (item.get("mediaState") == "MEDIA_OFF")
                    else
                    "done" if (item.get("mediaState") == "MEDIA_DONE")
                    else
                    "unknown"
                ),
                call_letters = item.get("callLetters", ""),
                # epg_title=epg["title"],
                language=item.get("language", "").lower(),
                media_type = "audio" if "audio" in epg["title"].lower() else "video",
                feed_type = fix_feed_type(
                    item.get("mediaFeedType", item.get("mediaFeedSubType")),
                    epg["title"],
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("blurb", ""),
                ),
                playbacks = item.get("playbacks", []),
                **self.extra_media_attributes(item)
            )
              for epg in epgs
              for item in epg["items"]],
            key = lambda i: (
                i.get("media_type", "") != "video",
                self.FEED_TYPE_ORDER.index(i.get("feed_type", "").lower())
                if i.get("feed_type", "").lower() in self.FEED_TYPE_ORDER
                else len(self.FEED_TYPE_ORDER),
                i.get("language", ""),
            )
        )

        items = [
            i for i in items
            if i.media_id
            and i.state not in ["off", "done" ]
        ]
        return items

    def extra_media_attributes(self, item):
        return {}


@dataclass
class BAMMediaSource(model.MediaSource):

    # provider: typing.Optional[BaseProvider] = None
    game_id: str = ""
    media_id: str = ""
    title: str = ""
    description: str = ""
    state: str = "unknown"
    call_letters: str = ""
    language: str = ""
    feed_type: str = ""
    playbacks: typing.List[dict] = field(default_factory=list)

    @property
    def helper(self):
        return "streamlink"

    @property
    @memo(region="long")
    def locator(self):

        # # FIXME: borked
        # # Get any team-specific profile overrides, and apply settings for them
        # profiles = tuple([ list(d.values())[0]
        #              for d in config.settings.profile_map.get("team", {})
        #              if list(d.keys())[0] in [
        #                      self.listing.away_abbrev,
        #                      self.listing.home_abbrev
        #              ] ])

        # if len(profiles):
        #     # override proxies for team, if defined
        #     if len(config.settings.profiles[profiles].proxies):
        #         old_proxies = self.session.proxies
        #         self.session.proxies = config.settings.profiles[profiles].proxies
        #         # self.session.refresh_access_token(clear_token=True)
        #         self.session.proxies = old_proxies

        if len(self.playbacks):
            playback = next(p for p in self.playbacks
                            if p["name"] == "HTTP_CLOUD_WIRED_60")

            try:
                media_url = playback["url"]
            except:
                from pprint import pformat
                raise Exception(pformat(media))
        else:
            stream = self.provider.session.get_stream(self)
            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d, %s" %(self.game_id))

        return media_url



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

    def __len__(self):
        return len(str(self))

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

        if "S" in timestamps: del timestamps["S"]
        timestamp_map = AttrDict(

            ( "Start" if k == "SO" else k, v ) for k, v in timestamps.items()
        )
        if live:
            timestamp_map["Live"] = False

        self.dropdown = Dropdown(
            timestamp_map, label="Begin playback",
            default = timestamp_map.get(default, None)
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
            selection["home"],

        ))

        # media = list(self.provider.get_media(self.game_id, title=self.media_title))
        # media = list(self.provider.get_media(self.game_id))
        # raise Exception(selection)
        try:
            media = selection.media
        except SGStreamNotFound as e:
            logger.warn(e)

        if not len(media):
            raise SGStreamNotFound

        feed_map = [
            (
                (f"""{e.media_type.title()}: {e.get("feed_type", "").title()} """
                 f"""({e.get("call_letters", "")}{"/"+e.get("language") if e.get("language") else ""})"""),
                e["media_id"].lower()
            )
            for e in media
        ]

        # raise Exception(feed_map)

        try:
            home_feed = next(
                e for e in media
                if e["feed_type"].lower() == "home"
            )
        except StopIteration:
            home_feed = media[0]

        self.live_stream = (home_feed.get("state") == "live")
        self.feed_dropdown = Dropdown(
            feed_map,
            label="Feed",
            default=home_feed["media_id"],
            max_height=8
        )
        urwid.connect_signal(
            self.feed_dropdown,
            "change",
            lambda s, b, *args: self.update_offset_dropdown(*args)
        )

        self.resolution_dropdown = Dropdown(
            self.provider.RESOLUTIONS, default=self.default_resolution
        )

        self.offset_dropdown_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.update_offset_dropdown(self.feed_dropdown.selected_value)

        def ok(s):
            media_id = self.feed_dropdown.selected_value
            self.provider.play(
                selection,
                # media = selected_media,
                media_id = media_id,
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
                ("weight", 5, urwid.Filler(
                    urwid.Columns([
                        ("weight", 4, self.feed_dropdown),
                        ("weight", 1, self.resolution_dropdown),
                    ]), valign="top")),
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
        # raise Exception(self.provider.media_timestamps(self.game_id, media_id))
        self.offset_dropdown = OffsetDropdown(
            self.provider.media_timestamps(self.game_id, media_id),
            live = self.live_stream,
            default = "Live" if self.watch_live and self.live_stream else "Start"
        )
        self.offset_dropdown_placeholder.original_widget = self.offset_dropdown


    def keypress(self, size, key):

        key = super(WatchDialog, self).keypress(size, key)
        if key == "meta enter":
            self.ok_button.keypress(size, "enter")
        elif key in ["<", ">"]:
            self.resolution_dropdown.cycle(1 if key == "<" else -1)
        elif key in ["[", "]"]:
            self.feed_dropdown.cycle(-1 if key == "[" else 1)
        elif key in ["-", "="]:
            self.offset_dropdown.cycle(-1 if key == "-" else 1)
        if key == "q":
            self.cancel()
        else:
            # return super(WatchDialog, self).keypress(size, key)
            # return super(WatchDialog, self).keypress(size, key)
            return key


class LiveStreamFilter(ListingFilter):

    @property
    def values(self):
        return AttrDict([
            ("Live", "live"),
            ("From Start", "start"),
        ])


class BAMProviderDataTable(ProviderDataTable):

    index = "game_id"
    detail_selectable = True
    ui_sort = False

    @property
    def detail_fn(self):
        return self.provider.get_details

    def keypress(self, size, key):
        key =super().keypress(size, key)
        if key in ["meta left", "meta right"]:
            self._emit(f"cycle_filter", 0,("w", -1 if key == "meta left" else 1))
        elif key in ["ctrl left", "ctrl right"]:
            self._emit(f"cycle_filter", 0, ("m", -1 if key == "ctrl left" else 1))
        elif key == "t":
            self.provider.filters.date.value = datetime.today()
        elif key == "meta enter":
            self.provider.play(self.selection.data)
        elif key == ".":
            self.selection.toggle_details()
        elif key == "ctrl w":
            logger.info(self.width)
        else:
            return key



class BAMProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = BAMProviderDataTable

@with_view(BAMProviderView)
class BAMProviderMixin(abc.ABC):
    """
    Mixin class for use by BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """
    sport_id = 1 # FIXME

    FILTERS_BROWSE = AttrDict([
        ("date", BAMDateFilter)
    ])

    FILTERS_OPTIONS = AttrDict([
        ("resolution", ResolutionFilter),
        ("live_stream", LiveStreamFilter),
    ])

    ATTRIBUTES = AttrDict(
        attrs = {"width": 6},
        start = {"width": 6, "format_fn": format_start_time},
        away = {"width": 16},
        home = {"width": 16},
        line = {"width": "pack"},
        game_id = {"width": 10},
        empty = {"label": "", "width": ("weight", 1)}
    )

    HELPER = "streamlink"

    REQUIRED_CONFIG = {"credentials": ["username", "password"]}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["date"].connect("changed", self.on_date_change)
        self.game_map = AttrDict()

    @property
    def session_params(self):
        return self.config.credentials

    @property
    def config_is_valid(self):
        return (
            super().config_is_valid
            and
            self.HELPER in list(player.PROGRAMS[Helper].keys())
        )

    def on_date_change(self, date):
        schedule = self.schedule(start=date, end=date)
        games = sorted(schedule["dates"][-1]["games"],
                       key= lambda g: g["gameDate"])

        self.game_map.clear()
        for game in games:
            self.game_map[game["gamePk"]] = AttrDict(game)
        self.view.table.reset()

    def game_data(self, game_id):

        return self.game_map[game_id]
        # schedule = self.schedule(game_id=game_id)
        # try:
        #     # Get last date for games that have been rescheduled to a later date
        #     game = schedule["dates"][-1]["games"][0]
        # except KeyError:
        #     raise SGException("no game data")
        # return game


    @memo(region="short")
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

        return iter(self.LISTING_CLASS.from_json(self.IDENTIFIER, g, i)
                         # for g in list(self.game_map.values())[-1:])
                         for i, g in enumerate(self.game_map.values()))

    def get_url(self, game_id,
                media = None,
                offset=None,
                preferred_stream=None,
                call_letters=None,
                output=None,
                verbose=0):

        live = False
        team = None
        # sport_code = "mlb" # default sport is MLB

        # media_title = "MLBTV"
        # media_id = None
        allow_stdout=False

        # schedule = self.schedule(
        #     game_id = game_id
        # )

        # date = schedule["dates"][-1]
        # game = date["games"][-1]

        game = self.game_data(game_id)

        away_team_abbrev = game["teams"]["away"]["team"]["abbreviation"].lower()
        home_team_abbrev = game["teams"]["home"]["team"]["abbreviation"].lower()

        if not preferred_stream or call_letters:
            preferred_stream = (
                "away"
                if team == away_team_abbrev
                else "home"
            )


        if not media:
            try:
                media = next(self.get_media(
                    game_id,
                    # media_id = media_id,
                    # title=media_title,
                    preferred_stream=preferred_stream,
                    call_letters = call_letters
                ))
                # raise Exception(media)
            except StopIteration:
                raise SGStreamNotFound("no matching media for game %d" %(game_id))

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
            playback = next(p for p in media["playbacks"]
                            if p["name"] == "HTTP_CLOUD_WIRED_60")

            try:
                media_url = playback["url"]
            except:
                from pprint import pformat
                raise Exception(pformat(media))
        else:
            stream = self.session.get_stream(media)
            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d, %s" %(game_id))

        return media_url

    @abc.abstractmethod
    def teams(self, season=None):
        pass

    @property
    @abc.abstractmethod
    def start_date(self):
        pass


    def parse_identifier(self, identifier):

        game_number = 1
        game_date = None
        team = None

        game_date = datetime.now().date()

        if isinstance(identifier, int):
            game_id = identifier

        else:
            try:
                (game_date, team, game_number) = identifier.split(".")
            except ValueError:
                try:
                    (game_date, team) = identifier.split(".")
                except ValueError:
                    try:
                        game_date = dateutil.parser.parse(identifier).date()
                    except ValueError:
                        # assume it's a team code with today's date
                        team = identifier
                    # raise SGIncompleteIdentifier

            except AttributeError:
                pass

            self.filters["date"].value = game_date

            if not team:
                raise SGIncompleteIdentifier

            if "-" in team:
                (sport_code, team) = team.split("-")

            game_number = int(game_number)
            teams =  self.teams(season=game_date.year)
            team_id = teams.get(team)

            if not team:
                msg = "'%s' not a valid team code, must be one of:\n%s" %(
                    identifier, " ".join(teams)
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
                away_team = game["teams"]["away"]["team"]["teamName"]
                home_team = game["teams"]["home"]["team"]["teamName"]

            except IndexError:
                raise SGException("No game %d found for %s on %s" %(
                    game_number, team, game_date)
                )

        return self.new_listing(game_id=game_id, title=f"{away_team}@{home_team}")

    def on_select(self, widget, selection):
        logger.info("select")
        self.open_watch_dialog(selection)

    def on_activate(self):
        # logger.info(f"activate: {self.filters.date.value}")
        self.filters.date.changed()
        # self.refresh()
        # if not self.filters.date.value:
        #     raise Exception
        #     self.filters.date.value = datetime.now()

    def open_watch_dialog(self, selection):
        # media = list(self.get_media(selection["game_id"]))
        try:
            dialog = WatchDialog(
                self,
                selection,
                media_title = self.MEDIA_TITLE,
                default_resolution = self.filters.resolution.value,
                watch_live = self.filters.live_stream.value == "live"
            )
            self.view.open_popup(dialog, width=80, height=15)
        except SGStreamNotFound:
            logger.warn(f"no stream found for game {selection['game_id']}")


        # self.play(selection)

    def get_details(self, data):

        game_id = data.game_id
        game = self.game_data(game_id)

        def play_highlight(selection):
            logger.info(f"play_highlight: {selection}")
            task = model.PlayMediaTask(
                provider=self.NAME,
                title=selection.title,
                sources = [
                    model.MediaSource(
                        provider_id=self.IDENTIFIER,
                        url = selection.url,
                        media_type = "video"
                    )
                ]
            )
            logger.info(f"task: {task}")
            player_spec = {"media_types": {"video"}}
            helper_spec = None
            # asyncio.create_task/(Player.play(task, player_spec, helper_spec))
            state.task_manager.play(task, player_spec, helper_spec)

        box = self.DETAIL_BOX_CLASS(game)
        box.connect("play", play_highlight)
        return box

    def get_source(self, selection, media_id=None, **kwargs):
        try:
            selected_media = next(m for m in selection.media if m.media_id == media_id)
        except StopIteration:
            selected_media = selection.media[0]
        return selected_media

    def play_args(self, selection, **kwargs):

        source, kwargs = super().play_args(selection, **kwargs)

        # kwargs["resolution"] = self.filters.resolution.value
        media_type = source.media_type
        # don't use video resolution for audio feeds
        if media_type == "audio":
            kwargs["resolution"] = "best"
        offset = kwargs.pop("offset", None)
        if offset:
            # raise Exception(selection)
            if (source.state == "live"): # live stream
                logger.debug("live stream")
                # calculate HLS offset, which is negative from end of stream
                # for live streams
                # start_time = dateutil.parser.parse(timestamps["S"])
                start_time = selection.start
                # start_time = selection.start
                offset_delta = (
                    datetime.now(pytz.utc)
                    - start_time.astimezone(pytz.utc)
                    - (timedelta(seconds=offset))
                )
                # offset_delta = (timedelta(seconds=-offset))
                # raise Exception(
                #     start_time,
                #     start_time.astimezone(pytz.utc),
                #     datetime.now(pytz.utc),
                #     datetime.now(pytz.utc) - start_time.astimezone(pytz.utc),
                #     (timedelta(seconds=-offset)),
                #     offset,
                #     offset_delta,
                #     offset_delta.seconds,
                # )
            else:
                # raise Exception
                logger.debug("recorded stream")
                offset_delta = timedelta(seconds=offset)

            # offset_seconds = offset_delta.seconds
            kwargs["offset"] = offset_delta.seconds

        kwargs["headers"] = self.session.headers
        kwargs["cookies"] = self.session.cookies

        return (source, kwargs)
