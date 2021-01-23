import logging
logger = logging.getLogger(__name__)
import asyncio

import urwid
from panwid.keymap import *

from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from ..utils import strip_emoji
from .. import config
from ..widgets import *
# from ..widgets.browser import FileBrowser, DirectoryNode, FileNode
from ..providers.base import SynchronizedPlayerMixin


class FilesViewEventHandler(FileSystemEventHandler):

    def __init__(self, view):
        self.view = view
        self.refresh_task = None
        super().__init__()

    def on_any_event(self, event):
        self.view.updated = True


@keymapped()
class FilesView(SynchronizedPlayerMixin, StreamglobView):

    signals = ["requery"]

    KEYMAP = {
        "meta p": "preview_all",
        "ctrl r": "refresh"
    }

    def __init__(self):

        self.browser = FileBrowser(
            self.root,
            dir_sort=("mtime", True), file_sort=("alpha", True),
            ignore_files=False
        )
        self.pile  = urwid.Pile([
            ('weight', 1, self.browser),
        ])
        super().__init__(self.pile)
        urwid.connect_signal(self.browser, "focus", self.on_focus)
        self.updated = False
        state.event_loop.create_task(self.check_updated())
        # self._emit("requery", self)

    async def check_updated(self):
        while True:
            if self.updated:
                self.refresh()
                self.updated = False
            await asyncio.sleep(1)

    @property
    def root(self):
        return config.settings.profile.get_path("output.path")

    def on_focus(self, source, selection):

        if isinstance(selection, DirectoryNode):
            self.monitor_path(selection.full_path)
        elif isinstance(selection, FileNode):
            state.event_loop.create_task(self.preview_all())

    def monitor_path(self, path):
        if path == self.root:
            return
        logger.info(f"monitor_path: {path}")
        if getattr(self, "observer", None):
            self.observer.stop()
        self.observer = PollingObserver(5)
        self.observer.schedule(
            FilesViewEventHandler(self), path, recursive=True
        )
        self.observer.start()

    @property
    def play_items(self):
        return [
            AttrDict(
                title = "foo",
                url = self.browser.selection.full_path
            )
        ]

    def refresh(self):
        self.browser.refresh()

    def on_view_activate(self):
        state.event_loop.create_task(self.play_empty())

    def __len__(self):
        return 1
    def __iter__(self):
        return iter(self.browser.selection.full_path)
