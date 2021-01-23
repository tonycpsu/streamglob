import urwid
from panwid.keymap import *

from . import config
from ..widgets import *
# from ..widgets.browser import FileBrowser, DirectoryNode, FileNode
from ..providers.base import SynchronizedPlayerMixin


@keymapped()
class FilesView(SynchronizedPlayerMixin, StreamglobView):

    signals = ["requery"]

    KEYMAP = {
        "meta p": "preview_all",
        "ctrl r": "reset"
    }

    def __init__(self):

        self.browser = FileBrowser(
            config.settings.profile.get_path("output.path"),
            dir_sort=("mtime", True), file_sort=("alpha", True),
            ignore_files=False
        )
        self.pile  = urwid.Pile([
            ('weight', 1, self.browser),
        ])
        super().__init__(self.pile)
        urwid.connect_signal(self.browser, "focus", self.on_focus)
        # self._emit("requery", self)

    def on_focus(self, source, selection):
        if isinstance(selection, DirectoryNode):
            return
        elif isinstance(selection, FileNode):
            state.event_loop.create_task(self.preview_all())

    @property
    def play_items(self):
        return [
            AttrDict(
                title = "foo",
                url = self.browser.selection.full_path
            )
        ]

    def reset(self):
        self.browser.reset()

    def on_view_activate(self):
        state.event_loop.create_task(self.play_empty())

    def __len__(self):
        return 1
    def __iter__(self):
        return iter(self.browser.selection.full_path)
