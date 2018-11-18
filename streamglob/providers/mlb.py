import logging
logger = logging.getLogger(__name__)

from panwid.datatable import *

from ..session import *

from .base import *
from .filters import *
from . import bam

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

    SESSION_CLASS = AuthenticatedStreamSession
    FILTERS = [
        FixedListingFilter(["foo", "bar", "baz"])
    ]
    ATTRIBUTES = {
        "attrs": {"width": 6},
        "start": {"width": 6, "format_fn": format_start_time},
        "away": {"width": 16},
        "home": {"width": 16},
        "line": {}
    }

    def login(self):
        print(self.session)

    def listings(self):
        # return [ MediaItem(line=t) for t in ["a", "b" ,"c" ] ]

        j = self.schedule(
        #     # sport_id=self.sport_id,
            start = datetime(2018, 9, 29),
            end = datetime(2018, 9, 29),
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
        # return []
