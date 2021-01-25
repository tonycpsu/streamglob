import urwid
from panwid.datatable import *

from .. import model
from ..widgets import *

class TasksView(StreamglobView):

    def __init__(self):

        self.playing = PlayingDataTable()
        self.pending = PendingDataTable()
        self.active_downloads = ActiveDownloadsDataTable()
        self.postprocessing_downloads = PostprocessingDownloadsDataTable()
        self.completed_downloads = CompletedDownloadsDataTable()
        self.pile = urwid.Pile([
            urwid.Columns([
                ("weight", 1, urwid.Pile([
                    (1, urwid.Filler(urwid.Text("Playing"))),
                    ("weight", 1, self.playing),
                ])),
                ("weight", 1, urwid.Pile([
                    (1, urwid.Filler(urwid.Text("Pending"))),
                    ("weight", 1, self.pending),
                ]))
            ], dividechars=1),
            (1, urwid.Filler(urwid.Text("Active Downloads"))),
            ("weight", 1, self.active_downloads),
            (1, urwid.Filler(urwid.Text("Postprocessing Downloads"))),
            ("weight", 1, self.postprocessing_downloads),
            (1, urwid.Filler(urwid.Text("Completed Downloads"))),
            ("weight", 1, self.completed_downloads)
        ])
        super().__init__(self.pile)

    def refresh(self):
        self.playing.refresh()
        self.pending.refresh()
        self.active_downloads.refresh()
        self.postprocessing_downloads.refresh()
        self.completed_downloads.refresh()

class TasksDataTable(BaseDataTable):

    index = "task_id"
    empty_message = None

    COLUMN_DEFS = AttrDict([
        (c.name, c)
        for c in [
                # DataTableColumn("action", width=8),
                DataTableColumn("program", width=16, format_fn = lambda p: p.result().cmd if p and p.done() else ""),
                DataTableColumn("started", width=20, format_fn = utils.format_datetime),
                DataTableColumn("elapsed",  width=14, align="right",
                                format_fn = utils.format_timedelta),
                DataTableColumn("provider", width=12),
                DataTableColumn(
                    "title", width=("weight", 3),
                    # FIXME: urwid miscalculates width of some unicode glyphs,
                    # which causes data table to raise an exception when rows
                    # are calculated with a different height than they render
                    # at.  See https://github.com/urwid/urwid/issues/225
                    # Workaround is to strip emoji
                    format_fn = utils.strip_emoji,
                    truncate=True
                ),
                DataTableColumn(
                    "sources", label="sources", width=("weight", 1), wrap="any",
                    format_fn = lambda l: f"{str(l[0]) if len(l) == 1 else '[%s]' %(len(l))}",
                    truncate=True
                ),
                DataTableColumn(
                    "dest", width=20,
                    format_fn = utils.strip_emoji,
                    # format_fn = functools.partial(utils.format_str_truncated, 40),
                    truncate=True
                ),
                DataTableColumn(
                    "size", width=8, align="right",
                    value = lambda t, r: r.data.program.result().progress.size_total,
                    format_fn = lambda v: v if v else "?"
                    # value = foo,
                ),
                DataTableColumn(
                    "size/total", width=16, align="right",
                    value = lambda t, r: (
                        r.data.program.result().progress.size_downloaded,
                        r.data.program.result().progress.size_total
                    ),
                    format_fn = lambda v: (f"{v[0] or '?'}/{v[1] or '?'}") if v else ""
                    # value = foo,
                ),
                DataTableColumn(
                    "pct", width=5, align="right",
                    # format_fn = lambda r: f"{r.data.program.progress.get('pct', '').split('.')[0]}%"
                    value = lambda t, r: r.data.program.result().progress.percent_downloaded,
                    format_fn = lambda v: f"{round(v, 1)}%" if v else "?",
                    # value = foo,
                ),
                DataTableColumn(
                    "rate", width=10, align="right",
                    value = lambda t, r: r.data.program.result().progress.transfer_rate,
                    format_fn = lambda v: f"{v}/s" if v else "?"
                )
        ]
    ])

    COLUMNS = ["provider", "program", "sources", "title"]

    def detail_fn(self, data):
        return urwid.Columns([
            (4, urwid.Padding(urwid.Text(""))),
            ("weight", 1, urwid.Pile([
                (1, urwid.Filler(DataTableText(s.locator)))
                for s in data["sources"]]
            )
        )
        ])


    def __init__(self, *args, **kwargs):
        self.columns = [
            self.COLUMN_DEFS[n] for n in self.COLUMNS
        ]
        super().__init__(*args, **kwargs)

    @classmethod
    def filter_task(cls, t):
        return True

    def keypress(self, size, key):
        if key == "ctrl r":
            self.refresh()
        elif key == "ctrl k":
            logger.info(type(self.selection.data.program.progress.transfer_rate))
        elif key == ".":
            self.selection.toggle_details()
            # self.selection.data._details_open = not self.selection.data._details_open
        else:
            return super().keypress(size, key)

class PlayingDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title"]

    def query(self, *args, **kwargs):
        # return [ t for t in state.task_manager.playing ]
        for t in state.task_manager.playing:
            yield t

    def keypress(self, size, key):

        if key == "delete" and self.selection:
            self.selection.data.program.proc.terminate()
        else:
            return super().keypress(size, key)

class PendingDataTable(TasksDataTable):

    COLUMNS = ["provider", "sources", "title"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.to_download ]

    def keypress(self, size, key):
        if key == "delete" and self.selection:
            state.task_manager.to_download.remove_by_id(self.selection.data.task_id)
            del self[self.focus_position]
        else:
            return super().keypress(size, key)


class ActiveDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "size/total", "pct", "rate", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.active if isinstance(t, model.DownloadMediaTask) ]

    def keypress(self, size, key):

        if key == "delete" and self.selection:
            try:
                self.selection.data.program.proc.terminate()
            except ProcessLookupError:
                pass
            # state.task_manager.active.remove_by_id(self.selection.data.task_id)
        else:
            return super().keypress(size, key)

class PostprocessingDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.postprocessing ]


class CompletedDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "size", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.done ]
