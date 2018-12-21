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

    SCHEDULE_TEMPLATE = (
        "http://statsapi.mlb.com/api/v1/schedule"
        "?sportId={sport_id}&startDate={start}&endDate={end}"
        "&gameType={game_type}&gamePk={game_id}"
        "&teamId={team_id}"
        "&hydrate=linescore,team,game(content(summary,media(epg)),tickets)"
    )

    DATA_TABLE_CLASS = MLBLineScoreDataTable

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


# register_provider(MLBProvider)
