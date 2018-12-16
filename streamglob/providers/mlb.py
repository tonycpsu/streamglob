import logging
logger = logging.getLogger(__name__)

import shlex
import subprocess
from itertools import chain

import urwid

from orderedattrdict import AttrDict
from panwid.datatable import *

from ..session import *

from .base import *
from .filters import *
from . import bam
from ..exceptions import *
from ..state import *

def parse_int(n):
    try:
        return int(n)
    except ValueError:
        return n
    except TypeError:
        return None

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


def format_start_time(d):
    s = datetime.strftime(d, "%I:%M%p").lower()[:-1]
    if s[0] == "0":
        s = s[1:]
    return s

class MLBListingFilter(ListingFilter):

    @property
    def values(self):
        return ["foo", "bar", "baz"]

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

        self.game_data = state.session.schedule(
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
            for e in state.session.get_media(self.game_id)
        ], key=lambda v: v[0])
        home_feed = next(state.session.get_media(
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
        self.timestamps = state.session.media_timestamps(
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
                  bam.BAMProviderMixin,
                  BaseProvider):

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    SESSION_CLASS = MLBStreamSession

    ATTRIBUTES = {
        "attrs": {"width": 6},
        "start": {"width": 6, "format_fn": format_start_time},
        "away": {"width": 16},
        "home": {"width": 16},
        "line": {}
    }

    FILTERS = AttrDict([
        ("date", DateFilter),
        ("foo", MLBListingFilter)
    ])

    def login(self):
        print(self.session)

    def listings(self):
        # return [ MediaItem(line=t) for t in ["a", "b" ,"c" ] ]

        # logger.info(f"listings: {self.filters.date.value}")
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
                    line_score_cls = MLBLineScoreDataTable #globals().get(f"{self.provider.upper()}LineScoreDataTable")
                    self.line_score_table = line_score_cls.from_json(
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

                yield(dict(
                    game_id = game_pk,
                    game_type = game_type,
                    away = away_team,
                    home = home_team,
                    start = start_time,
                    line = self.line_score,
                    attrs = attrs
                ))

    def play(self, selection):

        game_id = selection.get("game_id")
        self.watch(game_id)

    def watch(self, game_id,
              resolution=None, feed=None,
              offset=None, preferred_stream=None):

        try:
            state.proc = self.play_stream(
                game_id,
                resolution,
                call_letters = feed,
                preferred_stream = preferred_stream,
                offset = offset
            )
        except SGException as e:
            logger.warning(e)


    def play_stream(self, game_specifier, resolution=None,
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

        if resolution is None:
            resolution = "best"

        if isinstance(game_specifier, int):
            game_id = game_specifier
            schedule = state.session.schedule(
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
            teams =  state.session.teams(season=game_date.year)
            team_id = teams.get(team)

            if not team:
                msg = "'%s' not a valid team code, must be one of:\n%s" %(
                    game_specifier, " ".join(teams)
                )
                raise argparse.ArgumentTypeError(msg)

            schedule = state.session.schedule(
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

        logger.info("playing game %d at %s" %(
            game_id, resolution)
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
            media = next(state.session.get_media(
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
                old_proxies = state.session.proxies
                state.session.proxies = config.settings.profiles[profiles].proxies
                state.session.refresh_access_token(clear_token=True)
                state.session.proxies = old_proxies

        if "playbacks" in media:
            playback = media["playbacks"][0]
            media_url = playback["location"]
        else:
            stream = state.session.get_stream(media)

            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d" %(game_id))

        offset_timestamp = None
        offset_seconds = None

        if (offset is not False and offset is not None):

            timestamps = state.session.media_timestamps(game_id, media_id)

            if isinstance(offset, str):
                if not offset in timestamps:
                    raise SGException("Couldn't find inning %s" %(offset))
                offset = timestamps[offset] - timestamps["SO"]
                logger.debug("inning offset: %s" %(offset))

            if (media_state == "MEDIA_ON"): # live stream
                logger.debug("live stream")
                # calculate HLS offset, which is negative from end of stream
                # for live streams
                start_time = dateutil.parser.parse(timestamps["S"])
                offset_delta = (
                    datetime.now(pytz.utc)
                    - start_time.astimezone(pytz.utc)
                    + (timedelta(seconds=-offset))
                )
            else:
                logger.debug("recorded stream")
                offset_delta = timedelta(seconds=offset)

            offset_seconds = offset_delta.seconds
            offset_timestamp = str(offset_delta)
            logger.info("starting at time offset %s" %(offset))

        header_args = []
        cookie_args = []

        if state.session.headers:
            header_args = list(
                chain.from_iterable([
                    ("--http-header", f"{k}={v}")
                for k, v in state.session.headers.items()
            ]))

        if state.session.cookies:
            cookie_args = list(
                chain.from_iterable([
                    ("--http-cookie", f"{c.name}={c.value}")
                for c in state.session.cookies
            ]))

        cmd = [
            "streamlink",
            # "-l", "debug",
            "--player", config.settings.profile.player,
        ] + cookie_args + header_args + [
            media_url,
            resolution,
        ]

        if config.settings.profile.streamlink_args:
            cmd += shlex.split(config.settings.profile.streamlink_args)

        if offset_timestamp:
            cmd += ["--hls-start-offset", offset_timestamp]

        if verbose > 1:

            allow_stdout=True
            cmd += ["-l", "debug"]

            if verbose > 2:
                if not output:
                    cmd += ["-v"]
                cmd += ["--ffmpeg-verbose"]

        if output is not None:
            if output == True or os.path.isdir(output):
                outfile = get_output_filename(
                    game,
                    media["callLetters"],
                    resolution,
                    offset=str(offset_seconds)
                )
                if os.path.isdir(output):
                    outfile = os.path.join(output, outfile)
            else:
                outfile = output

            cmd += ["-o", outfile]

        logger.debug("Running cmd: %s" % " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=None if allow_stdout else open(os.devnull, 'w'))
        return proc
            

# register_provider(MLBProvider)
