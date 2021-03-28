import urwid
from panwid.datatable import *
from panwid.progressbar import *
from panwid.keymap import *

from .. import model
from .. import utils
from ..widgets import *

class TaskWidget(urwid.WidgetWrap):

    def __init__(self, task, details=False):

        self.task = task
        self.details = details
        self.pile = urwid.Pile([
            ("pack", line)
            for line in self.display_lines
        ])
        super().__init__(self.pile)

    @property
    def display_lines(self):
        if self.details:
            return self.display_lines_details
        else:
            return getattr(self, f"display_lines_{self.task.status}",
                           self.display_lines_default)

    @property
    def display_lines_default(self):
        return [
            self.display_line_default
        ]

    # @property
    # def display_lines_downloading(self):
    #     return self.display_lines_default + [
    #         self.display_line_progress
    #     ]


    @property
    def display_line_default(self):
        return urwid.Columns([
            ("pack", self.provider),
            ("weight", 1, urwid.Padding(self.title)),
            (18, urwid.Padding(
                urwid.Text(self.status, align="right"),
                right=1)),
            ("pack", self.progress_bar),
            # ("pack", self.elapsed)
        ], dividechars=1)

    @property
    def display_lines_details(self):
        return [
            self.display_line_source,
            self.display_line_destination
        ]

    @property
    def display_line_source(self):
        return urwid.Columns([
            ("weight", 1, self.pad_text(self.task.sources))
        ])

    @property
    def display_line_destination(self):
        return urwid.Columns([
            ("weight", 1, self.pad_text(self.task.dest))
        ])

    @property
    def progress_bar(self):
        return ProgressBar(
                width=20,
                maximum=self.progress.size_total or 1,
                value=self.progress.size_downloaded or 0,
                progress_color="light blue",
                remaining_color="dark blue"
            )

    @property
    def provider(self):
        return urwid.Text(self.task.provider)

    @property
    def title(self):
        return urwid.Text(self.task.title)

    @property
    def status(self):
        return self.task.status

    @property
    def elapsed(self):
        return urwid.Text(utils.format_timedelta(self.task.elapsed))


    def pad_text(self, text):
        return urwid.Padding(urwid.Text(str(text)))

    @property
    def sources(self):
        if len(self.sources) == 1:
            return str(self.sources[0])
        else:
            return str(len(self.sources))

    @property
    def dest(self):
        return utils.strip_emoji(
            getattr(self.sources[0], "dest", None)
            or
            self.dest
        )

    @property
    def started(self):
        return utils.format_datetime(self.started)

    @property
    def program(self):
        return self.task.program.result()

    @property
    def progress(self):
        return self.program.progress

    @property
    def size(self):
        return self.progress.size_total or "?"

    @property
    def size_total(self):
        return f"""{self.progress.size_downloaded or "?"}/{self.progress.size_total or "?"}"""

    @property
    def pct(self):
        return self.progress.percent_downloaded or "?"

    @property
    def rate(self):
        return self.progress.transfer_rate or "?"


def format_task(task):

    return TaskWidget(task)

@keymapped()
class TaskTable(BaseDataTable):

    KEYMAP = {
        " ": "toggle_details"
    }

    index = "task_id"

    columns = [
        DataTableColumn("task_id", hide=True),
        DataTableColumn("task", format_fn=format_task)
    ]

    # FIXME
    STATUS_MAP = AttrDict(
        playing="playing",
        to_download="pending",
        active="downloading",
        postprocessing="processing",
        done="done"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tasks = AttrDict()

    def detail_fn(self, data):
        return TaskWidget(data.task, details=True)

    def toggle_details(self):
        self.selection.toggle_details()

    def get_tasks(self):

        for task_list, status in self.STATUS_MAP.items():
            for task in getattr(state.task_manager, task_list):
                yield AttrDict(
                    task,
                    status=status
                )

    def query(self, *args, **kwargs):

        for task in self.get_tasks():
            yield AttrDict(
                task_id=task.task_id,
                task=task
            )


        
class TasksView(StreamglobView):

    def __init__(self):
        self.table = TaskTable()
        self.pile = urwid.Pile([
            ("weight", 1, self.table)
        ])
        super().__init__(self.pile)

    def refresh(self):
        self.table.refresh()


class TasksView2(StreamglobView):

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
