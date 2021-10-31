import logging
logger = logging.getLogger(__name__)
import os
import asyncio
import re
import pipes
from functools import partial
import hashlib

import urwid
from panwid.keymap import *
from panwid.dialog import ConfirmDialog
import thefuzz.fuzz, thefuzz.process
from unidecode import unidecode
import ffmpeg
from pymediainfo import MediaInfo

from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

from .. import model
from .. import utils
from .. import programs
from ..utils import strip_emoji, classproperty
from .. import config
from ..widgets import *
from ..providers.widgets import *
from .. import providers
# from ..widgets.browser import FileBrowser, DirectoryNode, FileNode
from ..providers.base import SynchronizedPlayerProviderMixin

DEFAULT_FUZZ_RATIO = 0.9

def find_fuzzy_matches(
    target, candidates, fuzz_ratio=DEFAULT_FUZZ_RATIO, fuzzy_unicode=False
):

    if fuzzy_unicode:
        target = unidecode(target)
        candidates = dict(zip([unidecode(c) for c in candidates], candidates))
    else:
        candidates = dict(zip(candidates, candidates))

    ranked = thefuzz.process.extractBests(
        target,
        candidates.keys(),
        scorer=thefuzz.fuzz.partial_token_set_ratio,
        score_cutoff=fuzz_ratio*100
    )
    return [
        (candidates[r[0]], r[1])
        for r in ranked
        if len(target) >= len(candidates[r[0]])
    ]


class CreateDirectoryDialog(TextEditDialog):

    @property
    def title(self):
        return "Create directory"

    def action(self, value):
        self.parent.browser.create_directory(value)


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


@model.attrclass()
class FilesMediaListing(model.TitledMediaListing):
    pass

@model.attrclass()
class FilesMediaSource(model.InflatableMediaSource):
    pass



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
        "m": ("set_file_sort", ["mtime", False]),
        "M": ("set_file_sort", ["mtime", True]),
        "b": ("set_file_sort", ["basename", False]),
        "B": ("set_file_sort", ["basename", True]),
        "backspace": "directory_up",
        # "enter": "open_selection",
        "o": "organize_selection",
        "O": ("organize_selection", [True]),
        "delete": "delete_selection",
        "!": "open_run_command_dialog"
    }

    def __init__(self, provider):

        self.provider = provider
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

    def update(self):
        pass

    @property
    def tmp_dir(self):
        return self.provider.tmp_dir

    @property
    def thumbnails(self):
        if not hasattr(self, "_thumbnails"):
            self._thumbnails = AttrDict()
        return self._thumbnails

    async def preview_content_thumbnail(self, cfg, position, listing, source):

        # import ipdb; ipdb.set_trace()
        async def preview(listing):
            thumbnail = await self.thumbnail_for(listing, cfg)
            if not thumbnail:
                return
            logger.info(position)
            logger.info(thumbnail)
            # import ipdb; ipdb.set_trace()
            await self.playlist_replace(thumbnail, idx=position)
            state.loop.draw_screen()

        # if getattr(self, "preview_task", False):
        #     self.preview_task.cancel()
        # self.preview_task = state.event_loop.create_task(preview(listing))

        await preview(listing)

    async def make_thumbnail(self, input_file, output_file):

        NUM_THUMBNAILS = 4
        THUMBNAIL_WIDTH = 480

        (
            ffmpeg
            .input(input_file, ss=5)
            .filter("scale", THUMBNAIL_WIDTH, -1)
            .output(output_file, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        # media_info = MediaInfo.parse(path)
        # duration = next(
        #     t for t in media_info.tracks
        #     if t.track_type == "General"
        # ).duration
        # logger.info(duration)
        # interval = duration / NUM_THUMBNAILS
        # for i in range(NUM_THUMBNAILS):

        return output_file

    async def thumbnail_for(self, listing, cfg):

        path = listing.path
        # import ipdb; ipdb.set_trace()

        key = hashlib.md5(path.encode("utf-8")).hexdigest()
        if key not in self.thumbnails:
            thumbnail_file = os.path.join(self.tmp_dir, f"thumbnail.{key}.jpg")
            self.thumbnails[key] = await self.make_thumbnail(path, thumbnail_file)
        return self.thumbnails[key]

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
            dir_sort=self.config.dir_sort,
            file_sort=self.config.file_sort,
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
            super().on_focus(source, selection)
            # state.event_loop.create_task(self.sync_playlist_position())
        elif isinstance(selection, DirectoryNode):
            self.load_play_items()

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

    def load_play_items(self):

        self._play_items = [
            AttrDict(
                title=n.get_key(),
                path=n.full_path,
                locator=n.full_path,
                preview_locator=utils.BLANK_IMAGE_URI
            )
            for n in self.browser.cwd_node.child_files
        ]

    @property
    def selected_listing(self):
        idx = self.selection_index
        path = self.play_items[idx].path
        return AttrDict(
            provider_id="files", # FIXME
            path=path,
            title=os.path.basename(path),
            sources=[
                AttrDict(
                    provider_id="files", # FIXME
                    media_type="video", # FIXME
                    path=path
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

        dialog = CreateDirectoryDialog(self)
        self.open_popup(dialog, width=60, height=8)


    def directory_up(self):
        self.set_root("..")

    # def open_selection(self):
    #     selection = self.browser.selection
    #     if isinstance(selection, DirectoryNode):
    #         self.browser.change_directory(selection.full_path)

    #         # self.load_browser(selection.full_path)

    def organize_selection(self, accept_unique=False):

        def guess_subject(title, words=2, max_len=40):
            try:
                spaces =  [
                    m.start()+1 for m in re.finditer(r"[^\d-]\S+", title)
                ][:words+1]
                indices = [spaces[0], spaces[-1]-1]
            except IndexError:
                indices = [0, max_len]
            return title[slice(*indices)]

        class OrganizeSelectionCreateDirectoryDialog(CreateDirectoryDialog):

            focus = "ok"

            def __init__(self, parent, files, orig_value=None):
                super().__init__(parent, orig_value=orig_value)
                self.files = files

            def action(self, value):

                destdir = os.path.join(self.parent.browser.cwd, value)
                if not os.path.exists(destdir):
                    self.parent.browser.create_directory(destdir)
                for src in self.files:
                    self.parent.browser.move_path(src, destdir)

        class OrganizeSelectionChooseDestinationDialog(OKCancelDialog):

            focus = "ok"

            def __init__(self, parent, files, dests, *args, **kwargs):

                self.files = files
                self.dests = dict(zip(dests + ["Other..."], dests + [None]))
                super().__init__(parent, *args, **kwargs)

            @property
            def widgets(self):

                edit_text = guess_subject(os.path.basename(self.files[0]))

                return dict(
                    dest=BaseDropdown(self.dests),
                    text=urwid_readline.ReadlineEdit(
                        caption=("bold", "Text: "),
                        edit_text=edit_text
                    ),
                )

            def action(self):

                if self.dest.selected_value:
                    dest = self.dest.selected_label
                else:
                    dest = self.text.get_edit_text()

                destdir = os.path.join(self.parent.browser.cwd, dest)
                if not os.path.exists(destdir):
                    self.parent.browser.create_directory(destdir)

                for src in self.files:
                    self.parent.browser.move_path(src, destdir)

        dirs = [d.get_key() for d in self.browser.tree_root.child_dirs]

        files = [
            f.full_path
            for f in [
                self.browser.selection
            ] + [
                item for item in self.browser.selected_items
                if item != self.browser.selection
            ]
        ]

        src = files[0]

        matches = [
            m[0] for m in find_fuzzy_matches(
                os.path.basename(src),
                dirs,
                fuzzy_unicode=True
            )
        ]

        if not len(matches):
            dialog = OrganizeSelectionCreateDirectoryDialog(
                self, files, orig_value=guess_subject(os.path.basename(src))
            )
            self.open_popup(dialog, width=60, height=8)
        elif len(matches) > 1 or not accept_unique:
            dialog = OrganizeSelectionChooseDestinationDialog(
                self, files, matches
            )
            self.open_popup(dialog, width=60, height=8)
        else:
            self.browser.move_path(
                src, os.path.join(self.browser.cwd, matches[0])
            )

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
            if self.config.auto_preview.enabled:
                await self.preview_all()

        state.event_loop.create_task(activate_preview_player())


    @property
    def playlist_position_text(self):
        return f"[{self.selection_index}/{len(self)}]"

    def __len__(self):
        # return 1
        # logger.info(len(self.browser.cwd_node.child_files))
        return len(self.browser.cwd_node.child_files)

    # def __iter__(self):
    #     return iter(self.browser.selection.full_path)


class FilesProvider(providers.base.BaseProvider):

    @classproperty
    def IDENTIFIER(cls):
        return "files"

    @property
    def VIEW(self):
        return FilesView(self)

    @property
    def listings(self):
        return []
