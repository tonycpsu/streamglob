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
# from ..widgets import *
from ..providers.widgets import *
from .. import providers
from ..widgets.browser import FileBrowser, DirectoryNode, FileNode
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



class FilesMediaListingMixin(object):

    @property
    def key(self):
        return hashlib.md5(self.sources[0].locator.encode("utf-8")).hexdigest()

    # @property
    # def locator(self):
    #     return self.sources[0].locator

@model.attrclass()
class FilesMediaListing(FilesMediaListingMixin, model.TitledMediaListing):
    pass

class FilesMediaSourceMixin(object):

    @property
    def locator_preview(self):
        return utils.BLANK_IMAGE_URI

    @property
    def locator_thumbnail_embedded(self):
        track_id = self.thumbnail_track_id
        if track_id:
            return AttrDict(
                locator=self.locator,
                video_track=track_id,
                audio_track=0,
            )

    @property
    def streams(self):
        if not getattr(self, "_streams", None):
            object.__setattr__(self, "_streams", ffmpeg.probe(self.locator)["streams"])
        return self._streams

    @property
    def video_streams(self):
        return [
            stream
            for stream in self.streams
            if stream.get("codec_type") == "video"
        ]

    @property
    def thumbnail_stream_id(self):
        try:
            return next(
                i for i, stream in enumerate(self.video_streams)
                if stream.get("disposition", []).get("attached_pic")
            )
        except (StopIteration):
            return None

    @property
    def thumbnail_track_id(self):
        stream_id = self.thumbnail_stream_id
        if stream_id:
            return stream_id + 1
        return None



@model.attrclass()
class FilesMediaSource(FilesMediaSourceMixin, model.InflatableMediaSource):
    pass


@keymapped()
class FilesView(
        SynchronizedPlayerProviderMixin,
        PlayListingProviderMixin,
        PlayListingViewMixin,
        ShellCommandViewMixin,
        StreamglobView):

    signals = ["requery"]

    KEYMAP = {
        "meta p": "preview_all",
        "ctrl r": "refresh",
        "g": "change_root",
        "m": ("set_file_sort", ["mtime", False]),
        "M": ("set_file_sort", ["mtime", True]),
        "b": ("set_file_sort", ["basename", False]),
        "B": ("set_file_sort", ["basename", True]),
        "backspace": "directory_up",
        # "enter": "open_selection",
        "c": "create_directory",
        "o": "organize_selection",
        "V": "move_selection",
        "(": ("select_prefix", [-1]),
        ")": ("select_prefix", [1]),
        "O": ("organize_selection", [True]),
        "delete": "delete_selection",
        "!": "open_run_command_dialog"
    }

    SPLIT_RE = re.compile(r"(\w+)\W*", re.U)
    FILTER_RE = re.compile(r"^\D")
    STRIP_NUM_PREFIX_EXPR = r"(?:\d+\W*)"
    STRIP_NUM_PREFIX_RE = re.compile(STRIP_NUM_PREFIX_EXPR)

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
        self.prefix_re = None
        self.prefix_scope = 0

        state.event_loop.create_task(self.check_updated())

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

    async def make_preview_embedded(self, listing, output_file, cfg):

        source = listing.sources[0]
        input_file = source.locator
        track_id = source.thumbnail_stream_id
        if not track_id:
            return None

        await (
            ffmpeg
            .input(input_file)[f"v:{track_id}"]
            .output(output_file)
            .overwrite_output()
            .run_asyncio(quiet=True)
        )
        return output_file

    async def make_preview_tile(
            self,
            listing, output_file,
            position=0, width=1280
    ):

        input_file = listing.locators[0]

        proc = await (
            ffmpeg
            .input(input_file, ss=position)
            .filter("scale", width, -2)
            .output(output_file, vframes=1, report=None)
            .overwrite_output()
            # .run_asyncio()
            .run_asyncio(quiet=True)
        )
        await proc.wait()
        # import ipdb; ipdb.set_trace()
        return output_file

    async def make_preview_thumbnail(self, listing, output_file, cfg):

        thumbnail_file = await self.make_preview_tile(
            listing, output_file,
            position=0.25*cfg.video_duration
        )
        return thumbnail_file

    async def thumbnail_for(self, listing, cfg):

        if listing.key not in self.thumbnails:
            # doesn't work for MKV...
            # duration = float(next(
            #     stream for stream in ffmpeg.probe(listing.locator)["streams"]
            #     if stream.get("codec_type") == "video" and not stream.get("disposition", []).get("attached_pic")
            # )["duration"])

            media_info = MediaInfo.parse(listing.locators[0])
            try:
                duration = next(
                    t for t in media_info.tracks
                    if t.track_type == "General"
                ).duration/1000
            except TypeError:
                duration = None

            cfg.video_duration = duration
            thumbnail_file = os.path.join(self.tmp_dir, f"thumbnail.{listing.key}.jpg")
            thumbnail = await self.make_preview_embedded(
                listing, thumbnail_file, cfg
            )
            if not thumbnail:
                thumbnail = await self.make_preview_thumbnail(
                    listing, thumbnail_file, cfg
                )
            self.thumbnails[listing.key] = AttrDict(
                thumbnail_file=thumbnail,
                video_duration=duration
            )

        return self.thumbnails[listing.key]


    async def preview_content_thumbnail(self, cfg, listing, source):

        return source.locator_thumbnail_embedded or (
            await self.thumbnail_for(listing, cfg)
        ).thumbnail_file


    async def make_preview_storyboard(self, listing, cfg):

        # FIXME: refactor with YouTube version
        PREVIEW_WIDTH = 1280
        PREVIEW_HEIGHT = 720
        PREVIEW_DURATION_RATIO = 0.95

        inset_scale = cfg.scale or 0.25
        inset_offset = cfg.offset or 0
        border_color = cfg.border.color or "black"
        border_width = cfg.border.width or 1
        num_tiles = cfg.num_tiles or 10

        thumbnail = await self.thumbnail_for(listing, cfg)
        thumbnail_file = thumbnail.thumbnail_file
        video_duration = thumbnail.video_duration

        (done, pending) = await asyncio.wait([
            self.make_preview_tile(
                listing,
                os.path.join(self.tmp_dir, f"board.{listing.key}.{n:04d}.jpg"),
                position=(video_duration*PREVIEW_DURATION_RATIO)*(n+1)*(1/num_tiles),
                width=480
            )
            for n in range(num_tiles)
        ])
        # import ipdb; ipdb.set_trace()
        # assert not pending
        # import ipdb; ipdb.set_trace()

        exceptions = [e for e in [f.exception() for f in done] if e]
        if len(exceptions):
            import ipdb; ipdb.set_trace()
        board_files = sorted([f.result() for f in done])
        logger.info(board_files)
        thumbnail = wand.image.Image(filename=thumbnail_file)
        thumbnail.trim(fuzz=5)
        if thumbnail.width != PREVIEW_WIDTH:
            thumbnail.transform(resize=f"{PREVIEW_WIDTH}x-2")

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
                tile_file = os.path.join(self.tmp_dir, f"tile.{listing.key}.{n:04d}.jpg")
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
            ffmpeg.input(
                os.path.join(self.tmp_dir, f"tile.{listing.key}.*.jpg"),
                pattern_type="glob", framerate=frame_rate,
            )
        )
        storyboard_file = os.path.join(self.tmp_dir, f"storyboard.{listing.key}.mp4")
        proc = await inputs.output(
            storyboard_file,
                report=None
        ).run_asyncio(overwrite_output=True, quiet=True)
        await proc.wait()

        # for p in itertools.chain(
        #     pathlib.Path(self.tmp_dir).glob(f"board.{listing.key}.*"),
        #     pathlib.Path(self.tmp_dir).glob(f"tile.{listing.key}.*")
        # ):
        #     p.unlink()

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
            import ipdb; ipdb.set_trace()
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
            root=top_dir,
            dir_sort=self.config.dir_sort,
            file_sort=self.config.file_sort,
            ignore_files=False
        )
        self.monitor_path(top_dir)
        urwid.connect_signal(self.browser, "focus", self.on_focus)
        self.browser_placeholder.original_widget = self.browser

    def set_file_sort(self, order, reverse=False):
        self.browser.file_sort = (order, reverse)

    def keypress(self, size, key):
        return super().keypress(size, key)

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
        return f"[{self.browser.root}/{self.selection.full_path}]"

    @property
    def root(self):
        return self.browser.root

    def on_focus(self, source, selection):

        if state.main_view.focused_widget != state.files_view:
            return

        self.prefix_scope = 0
        if isinstance(selection, FileNode):
            super().on_focus(source, selection)
            # state.event_loop.create_task(self.sync_playlist_position())
        elif isinstance(selection, DirectoryNode):
            # FIXME
            self.load_play_items()

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
                title=listing.title,
                key=listing.sources[0].key,
                locator=listing.locators[0],
                locator_preview=listing.sources[0].locator_preview
            )
            for listing in [
                    self.get_listing(n)
                    for n in range(len(self.browser.cwd_node.child_files))
            ]
        ]

    @property
    def selection(self):
        return self.browser.selection

    @property
    def selection_index(self):
        try:
            return self.browser.cwd_node.child_files.index(
                self.selection
            )
        except ValueError:
            return 0

    def get_listing(self, index=None):
        if index is None:
            index = self.selection_index
        path = self.browser.cwd_node.child_files[index].full_path

        return self.provider.new_listing(
            # path=path,
            title=os.path.basename(path),
            sources=[
                self.new_media_source(
                    media_type="video", # FIXME
                    url=path
                )
            ]
        )

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
                self.parent.set_root(value)

        dialog = ChangeDirectoryDialog(self, self.browser.cwd)
        self.open_popup(dialog, width=60, height=8)

    def create_directory(self):

        dialog = CreateDirectoryDialog(self)
        self.open_popup(dialog, width=60, height=8)


    def move_selection(self):

        marked_files = [
            f.full_path
            for f in self.browser.marked_items
        ]

        dest = self.browser.cwd
        if not os.path.isdir(dest):
            logger.warning(f"{dest} is not a directory")

        for src in marked_files:
            self.browser.move_path(src, dest)


    def directory_up(self):
        self.set_root("..")


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

        dirs = [d.name for d in self.browser.tree_root.child_dirs]

        files = [
            f.full_path
            for f in self.browser.selected_items
        ]

        src = files[0]

        matches = [
            m[0] for m in find_fuzzy_matches(
                os.path.basename(src),
                dirs,
                fuzzy_unicode=True
            )
        ]

        # if not len(matches):
        #     dialog = OrganizeSelectionCreateDirectoryDialog(
        #         self, files, orig_value=guess_subject(os.path.basename(src))
        #     )
        #     self.open_popup(dialog, width=60, height=8)
        if len(matches) == 1 and accept_unique:
            self.browser.move_path(
                src, os.path.join(self.browser.cwd, matches[0])
            )
        else:
            dialog = OrganizeSelectionChooseDestinationDialog(
                self, files, matches
            )
            self.open_popup(dialog, width=60, height=8)
        # else:
        #     self.browser.move_path(
        #         src, os.path.join(self.browser.cwd, matches[0])
        #     )

    def delete_selection(self):

        class DeleteConfirmDialog(ConfirmDialog):

            @property
            def prompt(self):
                return f"""Delete "{self.parent.browser.selection.full_path}"?"""

            def action(self):
                self.parent.browser.delete_node(
                    self.parent.browser.selection, remove=True
                )

        if isinstance(self.selection, DirectoryNode):
            dialog = DeleteConfirmDialog(self)
            self.open_popup(dialog, width=60, height=5)
        else:
            self.browser.delete_node(self.selection)

    def select_prefix(self, step=1):

        if step > 0 or self.prefix_scope > 0:
            self.prefix_scope += step
        if not self.prefix_scope:
            self.browser.cwd_node.unmark()
            return

        words = [
            unidecode(w) if self.config.select_prefix.unidecode else w
            for w in self.SPLIT_RE.findall(self.selection.name)
        ]
        if self.config.select_prefix.strip_num:
            words = list(itertools.dropwhile(
                lambda w: self.STRIP_NUM_PREFIX_RE.search(w) is not None,
                words
            ))

        prefix = r"\W*".join(words[:self.prefix_scope])
        logger.info(prefix)
        prefix_expr = (
            "^"
            +
            ( "%s*" %(self.STRIP_NUM_PREFIX_EXPR) if self.config.select_prefix.strip_num else "")
            +
            r"\W*"
            +
            (".*" if self.config.select_prefix.match_anywhere else "")
            +
            prefix
            +
            (".*" if self.config.select_prefix.match_anywhere else "")
            +
            r"\b"
        )
        prefix_re = re.compile(
            prefix_expr,
            re.IGNORECASE if self.config.select_prefix.case_sensitive else 0
        )

        for f in self.browser.cwd_node.child_files:
            if f.marked:
                f.unmark()
            if (prefix_re.search(unidecode(f.name))
                if self.config.select_prefix.unidecode
                else prefix_re.search(f.name)):
                f.mark()

            


    def browse_file(self, filename):
        # if filename.startswith(self.root):
        #     filename = filename[len(self.root):]
        # if filename.startswith(os.path.sep):
        #     filename = filename[1:]
        # logger.info(filename)

        node = self.browser.find_path(filename)
        if not node:
            logger.info(f"{filename} not found under {self.root}")
            return

        self.browser.body.set_focus(node)

    def activate(self):

        if state.main_view.focused_widget != state.files_view:
            return

        async def activate_preview_player():
            if self.provider.auto_preview_enabled:
                await self.preview_all()

        state.event_loop.create_task(activate_preview_player())


    @property
    def playlist_position_text(self):
        return f"[{self.selection_index}/{len(self)}]"

    def __len__(self):
        return len(self.browser.cwd_node.child_files)



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
