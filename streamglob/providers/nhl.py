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


class NHLProvider(SimpleProviderViewMixin,
                  BAMProviderMixin,
                  BaseProvider):

    DATA_TABLE_CLASS = NHLLineScoreDataTable

    SESSION_CLASS = NHLStreamSession

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

    FILTERS = AttrDict([
        ("date", DateFilter),
        ("resolution", ResolutionFilter)
    ])
