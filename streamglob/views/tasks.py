import os
from datetime import datetime

import urwid
from panwid.datatable import *
from panwid.progressbar import *
from panwid.keymap import *
from orderedattrdict import AttrDict

from .. import model
from .. import utils
from .. import config

from ..state import *
from ..widgets import StreamglobView, BaseDataTable
from ..providers.base import SynchronizedPlayerProviderMixin
from ..providers.widgets import PlayListingProviderMixin, PlayListingViewMixin

class TaskWidget(urwid.WidgetWrap):

    def __init__(self, task):

        self.task = task
        try:
            lines = self.display_lines
        except ConnectionRefusedError:
            lines = [
                urwid.Filler(urwid.Text(""))
            ]
        self.pile = urwid.Pile([
            ("pack", line)
            for line in self.display_lines
        ])
        super().__init__(self.pile)

    @property
    def display_lines(self):
        return getattr(self, f"display_lines_{self.task.status}",
                       self.display_lines_default)

    @property
    def display_lines_default(self):
        return [
            self.display_line_default
        ]

    @property
    def display_line_default(self):
        return urwid.Columns([
            ("pack", self.provider),
            ("weight", 1, urwid.Padding(self.title)),
            (18, urwid.Padding(
                urwid.Text(self.status, align="right"),
                right=1)),
            ("pack", urwid.Text(str(f"{self.transfer_rate}/s"))),
            ("pack", self.progress_bar),
            # ("pack", self.elapsed)
        ], dividechars=1)


    @property
    def display_line_source(self):
        return urwid.Columns([
            ("weight", 1, self.pad_text(self.task.sources[0]))
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
                maximum=self.size_total,
                value=self.size_downloaded,
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
        if self.task.status == "downloading":
            return self.progress.status or self.task.status
        else:
            return self.task.status

    @property
    def elapsed(self):
        return urwid.Text(utils.format_timedelta(self.task.elapsed))


    def pad_text(self, text):
        return urwid.Padding(urwid.Text(str(text), wrap="clip"))

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
        return self.task.program.result() if self.task.program.done() else None

    @property
    def progress(self):
        return self.program.progress if self.program else None


    @property
    def size(self):
        return self.progress.size_total or "?" if self.progress else None

    @property
    def size_downloaded(self):
        return self.progress.size_downloaded or 0 if self.progress else 0

    @property
    def size_total(self):
        return self.progress.size_total or 0 if self.progress else 0

    # @property
    # def size_total(self):
    #     return f"""{self.progress.size_downloaded or "?"}/{self.progress.size_total or "?"}"""

    @property
    def pct(self):
        return self.progress.percent_downloaded or "?" if self.progress else None

    @property
    def transfer_rate(self):
        return self.progress.transfer_rate or "?" if self.progress else None


def format_task(task):

    return TaskWidget(task)

@keymapped()
class TaskTable(BaseDataTable):


    index = "task_id"

    ui_sort = False

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
            for task in sorted(
                    getattr(state.task_manager, task_list),
                    key=lambda t:t.started if t.started else datetime.min
                ):
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


@keymapped()
class TasksView(SynchronizedPlayerProviderMixin,
                PlayListingProviderMixin,
                PlayListingViewMixin,
                StreamglobView):

    def __init__(self):
        self.table = TaskTable()
        urwid.connect_signal(self.table, "focus", self.on_focus)
        self.pile = urwid.Pile([
            ("weight", 1, self.table)
        ])
        super().__init__(self.pile)

    @property
    def provider(self):
        return self

    @property
    def body(self):
        return self

    @property
    def NAME(self):
        return "tasks"

    @property
    def config(self):
        return config.settings.profile.tasks

    @property
    def play_items(self):
        if not self.selected_listing:
            return []
        return [
            AttrDict(
                title=self.selected_listing.title,
                locator=self.selected_listing.sources[0].locator
            )
        ]

    def on_focus(self, source, selection):

        state.event_loop.create_task(self.preview_all())


    @property
    def selected_listing(self):
        if not self.table.selection:
            return
        task = self.table.selection.data_source.task
        if not task:
            return None
        if task.status != "done":
            return None
        path = task.sources[0].local_path
        return model.TitledMediaListing.attr_class(
            provider_id="tasks", # FIXME
            title=os.path.basename(path),
            sources = [
                model.MediaSource.attr_class(
                    provider_id="tasks", # FIXME
                    url=path
                )
            ]
        )

    @property
    def selected_source(self):
        if not self.selected_listing:
            return None
        return self.selected_listing.sources[0]

    def refresh(self):
        self.table.refresh()

    @property
    def playlist_position(self):
        return 0

    @property
    def playlist_title(self):
        return f"self.selected_listing.title"

    def __len__(self):
        return 1

    def __iter__(self):
        return iter([self.selected_listing])
