import os
import logging
logger = logging.getLogger(__name__)
import asyncio
import re
import pipes
from functools import partial

import urwid
from panwid.keymap import *
from panwid.dialog import ConfirmDialog

from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from .. import model
from .. import programs
from ..utils import strip_emoji
from .. import config
from ..widgets import *
from ..providers.widgets import *
from .. import providers
# from ..widgets.browser import FileBrowser, DirectoryNode, FileNode
from ..providers.base import SynchronizedPlayerProviderMixin

@model.attrclass()
class FilesPlayMediaTask(model.PlayMediaTask):
    pass

class FilesViewEventHandler(FileSystemEventHandler):

    def __init__(self, view, root):
        self.view = view
        self.root = root
        self.refresh_task = None
        super().__init__()

    def on_modified(self, event):
        self.view.browser.refresh_path(event.src_path)
        self.view.updated = True


class RunCommandDropdown(BaseDropdown):

    @property
    def items(self):
        return config.settings.profile.files.commands

    @property
    def expanded(self):
        return True

class RunCommandPopUp(OKCancelDialog):

    def __init__(self, parent):

        super().__init__(parent)
        urwid.connect_signal(self.dropdown, "change", self.on_dropdown_change)

    def on_dropdown_change(self, source,label,  value):
        self.pile.set_focus_path(self.ok_focus_path)

    @property
    def widgets(self):

        return dict(
            dropdown=RunCommandDropdown()
        )

    async def action(self):

        await self.parent.run_command_on_seleciton(
            self.dropdown.selected_value
        )


@keymapped()
class FilesView(
        SynchronizedPlayerProviderMixin,
        PlayListingProviderMixin,
        PlayListingViewMixin,
        StreamglobView):

    signals = ["requery"]

    KEYMAP = {
        "meta p": "preview_all",
        "ctrl r": "refresh",
        "c": "create_directory",
        "g": "change_root",
        ";": "debug",
        "m": ("set_file_sort", ["mtime", False]),
        "M": ("set_file_sort", ["mtime", True]),
        "b": ("set_file_sort", ["basename", False]),
        "B": ("set_file_sort", ["basename", True]),
        "backspace": "directory_up",
        # "enter": "open_selection",
        "delete": "delete_selection",
        "!": "open_run_command_dialog"
    }

    def __init__(self):

        self.browser_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.pile  = urwid.Pile([
            ('weight', 1, self.browser_placeholder),
        ])
        super().__init__(self.browser_placeholder)
        self.pile.focus_position = 0
        self.updated = False
        state.event_loop.create_task(self.check_updated())
        for cmd, cfg in config.settings.profile.files.commands.items():
            if "key" in cfg:
                func = partial(
                    self.run_command_on_selection,
                    cfg
                    )
                self.keymap_register(cfg.key, func)


    def debug(self):
        import ipdb; ipdb.set_trace()

    @property
    def provider(self):
        return self

    @property
    def NAME(self):
        return "files"

    @property
    def config(self):
        return config.settings.profile.files

    def selectable(self):
        return True

    def load_browser(self, top_dir):

        self.browser = FileBrowser(
            top_dir,
            root=config.settings.profile.files.root,
            dir_sort = self.config.dir_sort,
            file_sort = self.config.file_sort,
            ignore_files=False
        )
        self.monitor_path(top_dir)
        urwid.connect_signal(self.browser, "focus", self.on_focus)
        self.browser_placeholder.original_widget = self.browser

    def set_file_sort(self, order, reverse=False):
        self.browser.file_sort = (order, reverse)

    # def keypress(self, size, key):
    #     return super().keypress(size, key)

    async def check_updated(self):
        while True:
            if self.updated:
                self.refresh()
                self.updated = False
            await asyncio.sleep(1)

    @property
    def playlist_position(self):
        return self.selection_index

    @property
    def playlist_title(self):
        return f"[{self.browser.root}/{self.browser.selection.full_path}]"

    @property
    def root(self):
        return self.browser.root

    def on_focus(self, source, selection):

        if isinstance(selection, FileNode):
            logger.info("sync")
            state.event_loop.create_task(self.sync_playlist_position())

# state.event_loop.create_task(self.preview_all())

    def monitor_path(self, path, recursive=False):

        # return # FIXME
        logger.info(f"monitor_path: {path}")
        if getattr(self, "observer", None):
            self.observer.stop()
        self.observer = PollingObserver(5)
        self.observer.schedule(
            FilesViewEventHandler(self, self.browser.cwd), path, recursive=recursive
        )
        self.observer.start()

    @property
    def play_items(self):
        # import ipdb; ipdb.set_trace()
        return [
            # AttrDict(
            #     title=self.selected_listing.title,
            #     locator=self.selected_listing.sources[0].locator
            # )
            # for i in range(len(self))

            AttrDict(
                title=n.get_key(),
                locator=n.full_path
            )
            for n in self.browser.cwd_node.child_files
        ]

    @property
    def selected_listing(self):
        idx = self.selection_index
        path = self.play_items[idx].locator
        return model.TitledMediaListing.attr_class(
            provider_id="files", # FIXME
            title=os.path.basename(path),
            sources = [
                model.MediaSource.attr_class(
                    provider_id="files", # FIXME
                    url=path
                )
            ]
        )

    @property
    def selection_index(self):
        try:
            return self.browser.cwd_node.child_files.index(
                self.browser.selection
            )
        except ValueError:
            return 0

    @property
    def selected_source(self):
        idx = self.selection_index
        return self.play_items[idx]

    def refresh(self):
        self.browser.refresh()


    def set_root(self, root):

        self.browser.change_directory(root)
        self.monitor_path(root)

    def change_root(self):

        class ChangeDirectoryDialog(TextEditDialog):

            @property
            def title(self):
                return "Go to directory"

            def action(self, value):
                self.parentparent.set_root(value)

        dialog = ChangeDirectoryDialog(self, self.browser.cwd)
        self.open_popup(dialog, width=60, height=8)

    def create_directory(self):

        class CreateDirectoryDialog(TextEditDialog):

            @property
            def title(self):
                return "Create directory"


            def action(self, value):
                self.parent.browser.create_directory(value)

        dialog = CreateDirectoryDialog(self)
        self.open_popup(dialog, width=60, height=8)


    def directory_up(self):
        self.set_root("..")

    # def open_selection(self):
    #     selection = self.browser.selection
    #     if isinstance(selection, DirectoryNode):
    #         self.browser.change_directory(selection.full_path)

    #         # self.load_browser(selection.full_path)


    def delete_selection(self):

        class DeleteConfirmDialog(ConfirmDialog):

            @property
            def prompt(self):
                return f"""Delete "{self.parent.browser.selection.full_path}"?"""

            def action(self):
                self.parent.browser.delete_node(
                    self.parent.browser.selection, confirm=True
                )

        if isinstance(self.browser.selection, DirectoryNode):
            dialog = DeleteConfirmDialog(self)
            self.open_popup(dialog, width=60, height=5)
        else:
            self.browser.delete_node(self.browser.selection)


    def browse_file(self, filename):
        if filename.startswith(self.root):
            filename = filename[len(self.root):]
        if filename.startswith(os.path.sep):
            filename = filename[1:]
        logger.info(filename)

        node = self.browser.find_path(filename)
        if not node:
            return
        self.browser.body.set_focus(node)
        state.main_view.focus_widget(self)

    async def run_command_on_selection(self, cmd_cfg):

        cmd = cmd_cfg.command
        try:
            prog = next(programs.ShellCommand.get(cmd))
        except StopIteration:
            logger.error(f"program {prog} not found")

        args = [
            a.format(
                path=self.browser.selection.full_path,
                socket=state.task_manager.preview_player.ipc_socket_name
            )
            for a in cmd_cfg.args
        ]
        # async def show_output():
        #     output = await prog.output_ready
        #     logger.info(f"output: {output}")
        # state.event_loop.create_task(show_output())
        await prog.run(args)


    def open_run_command_dialog(self):

        path = self.browser.selection.full_path
        popup = RunCommandPopUp(self)
        self.open_popup(popup, width=60, height=10)


    def on_view_activate(self):

        async def activate_preview_player():
            if state.listings_view.auto_preview_mode:
                await self.preview_all()

        state.event_loop.create_task(activate_preview_player())

    def __len__(self):
        # return 1
        logger.info(len(self.browser.cwd_node.child_files))
        return len(self.browser.cwd_node.child_files)

    # def __iter__(self):
    #     return iter(self.browser.selection.full_path)
