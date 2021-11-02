import logging
logger = logging.getLogger(__name__)
import os
import asyncio
import re
import pipes
from functools import partial
import hashlib
import pathlib

import urwid
from panwid.keymap import *
from panwid.dialog import ConfirmDialog
import thefuzz.fuzz, thefuzz.process
from unidecode import unidecode
import ffmpeg
from pymediainfo import MediaInfo
import wand.image
import wand.drawing

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
        self.storyboard_lock = asyncio.Lock()
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

    @property
    def storyboards(self):
        if not hasattr(self, "_storyboards"):
            self._storyboards = AttrDict()
        return self._storyboards

    async def make_preview_tile(
            self,
            input_file, output_file,
            position=0, width=1280
    ):

        async def run_ffmpeg():
            await (
                ffmpeg
                .input(input_file, ss=position)
                .filter("scale", width, -1)
                .output(output_file, vframes=1)
                .overwrite_output()
                # .run()
                .run_asyncio(quiet=True)
            )
            return output_file

        return await run_ffmpeg()

    async def make_preview_thumbnail(self, input_file, output_file, cfg):

        media_info = MediaInfo.parse(input_file)
        duration = next(
            t for t in media_info.tracks
            if t.track_type == "General"
        ).duration/1000
        logger.info(duration)

        thumbnail_file = await self.make_preview_tile(
            input_file, output_file,
            position=0.25*duration
        )
        # import ipdb; ipdb.set_trace()
        return AttrDict(
            thumbnail_file=thumbnail_file,
            duration=duration
        )

    async def thumbnail_for(self, listing, cfg):
        # import ipdb; ipdb.set_trace()

        if listing.key not in self.thumbnails:
            thumbnail_file = os.path.join(self.tmp_dir, f"thumbnail.{listing.key}.jpg")
            self.thumbnails[listing.key] = await self.make_preview_thumbnail(
                listing.path, thumbnail_file, cfg
            )
        return self.thumbnails[listing.key]

    async def preview_content_thumbnail(self, cfg, listing, source):

        # import ipdb; ipdb.set_trace()
        return (await self.thumbnail_for(listing, cfg)).thumbnail_file


    async def make_preview_storyboard(self, listing, cfg):

        # FIXME: refactor with YouTube version
        PREVIEW_WIDTH = 1280
        PREVIEW_HEIGHT = 720
        PREVIEW_DURATION_RATIO=0.95

        inset_scale = cfg.scale or 0.25
        inset_offset = cfg.offset or 0
        border_color = cfg.border.color or "black"
        border_width = cfg.border.width or 1
        tile_skip = cfg.skip or None

        num_tiles = 10

        thumbnail = await self.thumbnail_for(listing, cfg)
        thumbnail_file = thumbnail.thumbnail_file
        duration = thumbnail.duration

        # import ipdb; ipdb.set_trace()

        (done, pending) = await asyncio.wait([
            self.make_preview_tile(
                listing.path,
                os.path.join(self.tmp_dir, f"board.{listing.key}.{n:04d}.jpg"),
                position=(duration*PREVIEW_DURATION_RATIO)*(n+1)*(1/num_tiles),
                width=480
            )
            for n in range(num_tiles)
        ])

        board_files = [f.result() for f in done]

        thumbnail = wand.image.Image(filename=thumbnail_file)
        thumbnail.trim(fuzz=5)
        if thumbnail.width != PREVIEW_WIDTH:
            raise Exception
            thumbnail.transform(resize=f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}")
        i = 0
        tile_width = 0
        tile_height = 0
        for n, board_file in enumerate(board_files):
            # logger.debug(board_file)
            with wand.image.Image(filename=board_file) as tile:
                clone = thumbnail.clone()
                tile.resize(int(thumbnail.width * inset_scale),
                             int(thumbnail.height * inset_scale))
                tile.border(border_color, border_width, border_width)
                thumbnail.composite(
                    tile,
                    left=thumbnail.width-tile.width-inset_offset,
                    top=thumbnail.height-tile.height-inset_offset
                )
                tile_file=os.path.join(self.tmp_dir, f"tile.{listing.key}.{n:04d}.jpg")
                # import ipdb; ipdb.set_trace()
                thumbnail.save(filename=tile_file)

        if cfg.frame_rate:
            frame_rate = cfg.frame_rate
            duration = n/frame_rate
        elif "duration" in cfg:
            duration = cfg.duration
            frame_rate = n/duration
        else:
            duration = n
            frame_rate = 1

        inputs = ffmpeg.concat(
            ffmpeg.input(os.path.join(self.tmp_dir, f"tile.{listing.key}.*.jpg"),
                                      pattern_type="glob",framerate=frame_rate)
        )
        storyboard_file=os.path.join(self.tmp_dir, f"storyboard.{listing.key}.mp4")
        proc = await inputs.output(storyboard_file).run_asyncio(overwrite_output=True, quiet=True)
        await proc.wait()

        for p in itertools.chain(
            pathlib.Path(self.tmp_dir).glob(f"board.{listing.key}.*"),
            pathlib.Path(self.tmp_dir).glob(f"tile.{listing.key}.*")
        ):
            p.unlink()

        # return storyboard_file
        return AttrDict(
            img_file=storyboard_file,
            duration=duration
        )


    async def storyboard_for(self, listing, cfg):

        async with self.storyboard_lock:
            if listing.key not in self.storyboards:
                self.storyboards[listing.key] = await self.make_preview_storyboard(listing, cfg)
        return self.storyboards[listing.key]


    async def preview_content_storyboard(self, cfg, listing, source):

        storyboard = await self.storyboard_for(listing, cfg)
        if not storyboard:
            return
        logger.info(storyboard)
        return storyboard.img_file

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
                key=hashlib.md5(n.full_path.encode("utf-8")).hexdigest(),
                # locator=n.full_path,
                locator=utils.BLANK_IMAGE_URI,
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
            key=hashlib.md5(path.encode("utf-8")).hexdigest(),
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
