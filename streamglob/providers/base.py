import logging
logger = logging.getLogger(__name__)
import sys
import os
import abc
import asyncio
import dataclasses
import re
from itertools import chain
# import textwrap
# import tempfile

from orderedattrdict import AttrDict, defaultdict
from pony.orm import *
from panwid.dialog import BaseView
from panwid.keymap import *
from pydantic import BaseModel

from .widgets import *
from .filters import *
from ..session import *
from ..state import *
from ..player import Player, Downloader
from .. import model
from .. import config
from  ..utils import *

# @keymapped()
class BaseProviderView(StreamglobView):

    def update(self):
        pass

    # def keypress(self, size, key):
    #     return super().keypress(size, key)

    def refresh(self):
        pass

    # def init_config(self, config):
    #     pass

    def selectable(self):
        return True


class TabularProviderMixin(object):

    def init_config(self):
        super().init_config()
        # raise Exception(self.provider.ATTRIBUTES)
        # for name, attrs in config.columns.items():
        #     for attr, value in attrs.items():
        #         col = next(c for c in self._columns if c.name == name)
        #         logger.warning(f"set {col}.{attr}={value}")
        #         setattr(col, attr, value)
        # for name, options in self.provider.ATTRIBUTES.items():
            # if name == "title":
            #
        attrs = self.ATTRIBUTES
        for name, opts in self.config.view.columns.items():
            for optname, optvalue in opts.items():
                print(name, optname, optvalue)
                attr = next(a for a in self.ATTRIBUTES if a == name)
                self.ATTRIBUTES[attr].update({optname: optvalue})

@keymapped()
class SimpleProviderView(BaseProviderView):

    PROVIDER_BODY_CLASS = ProviderDataTable

    KEYMAP = {
        "[": ("cycle_filter", [0, -1]),
        "]": ("cycle_filter", [0, 1]),
        "{": ("cycle_filter", [1, -1]),
        "}": ("cycle_filter", [1, 1]),
        "-": ("cycle_filter", [2, -1]),
        "=": ("cycle_filter", [2, 1]),
        "_": ("cycle_filter", [3, -1]),
        "+": ("cycle_filter", [3, 1]),
        # "ctrl d": "download"
    }

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body#(self.provider, self)
        self.toolbar = FilterToolbar(self.provider.filters)
        # self.body = self.PROVIDER_BODY_CLASS(self.provider, self)
        # urwid.connect_signal(self.toolbar, "filter_change", self.filter_change)
        # urwid.connect_signal(self.body, "select", self.provider.on_select)
        urwid.connect_signal(self.body, "cycle_filter", self.cycle_filter)
        if "keypress" in self.body.signals:
            urwid.connect_signal(self.body, "keypress", self.on_keypress)

        self.pile  = urwid.Pile([
            ("pack", self.toolbar),
            ("weight", 1, self.body)
        ])
        self.pile.focus_position = 1
        super().__init__(self.pile)

    @keymap_command
    def cycle_filter(self, n, step):
        self.toolbar.cycle_filter(n, step)

    def refresh(self):
        self.body.refresh()

    def reset(self):
        logger.info("reset")
        # import traceback; logger.info("".join(traceback.format_stack()))
        self.body.reset()

    def on_keypress(self, source, key):
        self.keypress((100, 100), key)

    def on_deactivate(self):
        self.body.on_deactivate()

    def keypress(self, size, key):
        return super().keypress(size, key)

    def __getattr__(self, attr):
        return getattr(self.body, attr)

    def apply_search_query(self, query):
        # FIXME: assumes body is a data table
        self.body.apply_filters([
            lambda listing: query.lower() in listing["title"].lower()
        ])
        self.body.refresh()



class InvalidConfigView(BaseProviderView):

    def __init__(self, name, required_config):
        super().__init__(
            urwid.Filler(urwid.Pile(
            [("pack", urwid.Text(
                f"The {name} provider requires additional configuration.\n"
                "Please ensure that the following settings are valid:\n")),
             ("pack", urwid.Text("    " + ", ".join(required_config)))
            ]), valign="top")
        )

MEDIA_SPEC_RE=re.compile(r"(?:/([^:]+))?(?::(.*))?")

class BaseProvider(abc.ABC):
    """
    Abstract base class from which providers should inherit from
    """

    SESSION_CLASS = StreamSession
    LISTING_CLASS = model.TitledMediaListing
    # VIEW_CLASS = SimpleProviderView
    # FILTERS = AttrDict()
    ATTRIBUTES = AttrDict(title={"width": ("weight", 1)})
    MEDIA_TYPES = None
    RPC_METHODS = []

    def __init__(self, *args, **kwargs):
        self._view = None
        self._session = None
        self._active = False
        self._filters = AttrDict({n: f(provider=self, name=n)
                                  for n, f in self.FILTERS.items() })

        rules = AttrDict(
            self.config.rules.label or {},
            **config.settings.profile.rules.label or {}
        )

        labels = AttrDict(
            self.config.labels,
            **config.settings.profile.labels
        )

        self.rule_map = AttrDict([
            (re.compile(k, re.IGNORECASE), v)
            for k, v in
            [(r, rules[r])
             for r in rules.keys()]
        ])

        self.highlight_map = AttrDict([
            (re.compile(k, re.IGNORECASE), labels[v])
            for k, v in rules.items()
        ])

        self.highlight_re = re.compile(
            "("
            + "|".join([k.pattern for k in self.highlight_map.keys()])
            + ")", re.IGNORECASE
        )
        print(self.filters)
        self.filters["search"].connect("changed", self.on_search_change)

    def init_config(self):
        with db_session:
            try:
                self.provider_data = model.ProviderData.get(name=self.IDENTIFIER).settings
            except AttributeError:
                self.provider_data = model.ProviderData(name=self.IDENTIFIER).settings
        # try:
        #     self.view.init_config(self.config.view)
        # except:
        #     raise
        #     logger.warn(f"couldn't initialize configuration for {self.IDENTIFIER}")


    @db_session
    def save_provider_data(self):
        model.ProviderData.get(name=self.IDENTIFIER).settings = self.provider_data
        commit()

    @property
    def LISTING_CLASS(self):
        for cls in [self.__class__] + list(self.__class__.__bases__):
            pkg = sys.modules.get(cls.__module__)
            pkgname =  pkg.__name__.split(".")[-1]
            try:
                return next(
                    v for k, v in pkg.__dict__.items()
                    if pkgname in k.lower() and k.endswith("MediaListing")
                )
            except StopIteration:
                continue
        return model.TitledMediaListing

    @property
    def MEDIA_SOURCE_CLASS(self):
        for cls in [self.__class__] + list(self.__class__.mro()):
            pkg = sys.modules.get(cls.__module__)
            pkgname =  pkg.__name__.split(".")[-1]
            try:
                return next(
                    v for k, v in pkg.__dict__.items()
                    if pkgname in k.lower() and k.endswith("MediaSource")
                )
            except (StopIteration, AttributeError):
                continue
        return model.MediaSource

    @property
    def session_params(self):
        return {"proxies": config.settings.profile.get("proxies")}

    @property
    def session(self):
        if self._session is None:
            session_params = self.session_params
            self._session = self.SESSION_CLASS.new(
                self.IDENTIFIER,
                **session_params
            )
        return self._session

    @property
    def gui(self):
        return self._view is not None

    @property
    def filters(self):
        return self._filters

    @property
    def view(self):
        if not self._view:
            self._view = self.make_view()
            self._view.update()
        return self._view

    @property
    def is_active(self):
        return self._active

    def activate(self):
        if self.is_active:
            return
        self._active = True
        self.on_activate()

    def deactivate(self):
        if not self.is_active:
            return
        self.on_deactivate()
        self._active = False

    def on_activate(self):
        pass

    def on_deactivate(self):
        self.view.on_deactivate()

    @property
    def VIEW(self):
        return SimpleProviderView(self, ProviderDataTable)

    # @abc.abstractmethod
    def make_view(self):
        if not self.config_is_valid:
            return InvalidConfigView(self.NAME, self.REQUIRED_CONFIG)
        return self.VIEW

    @classproperty
    def IDENTIFIER(cls):
        return next(
            c.__module__ for c in cls.__mro__
            if __package__ in c.__module__).split(".")[-1]

    @classproperty
    @abc.abstractmethod
    def NAME(cls):
        return cls.__name__.replace("Provider", "")

    @property
    def FILTERS_BROWSE(self):
        return AttrDict()

    @property
    def FILTERS_OPTIONS(self):
        return AttrDict([
            ("search", TextFilter)
        ])

    @property
    def FILTERS(self):
        d = getattr(self, "FILTERS_BROWSE", AttrDict())
        d.update(getattr(self, "FILTERS_OPTIONS", {}))
        return d

    def on_search_change(self, value, *args):

        if getattr(self, "search_task", False):
            self.search_task.cancel()
        self.search_task = state.event_loop.call_later(
            1,
            self.apply_search_query,
            value
        )

    def apply_search_query(self, query):
        self.view.apply_search_query(query)

    def parse_spec(self, spec):

        (identifier, options) = MEDIA_SPEC_RE.search(spec).groups()

        try:
            selection, filters, identifier_options = self.parse_identifier(identifier)
            self.apply_identifier(selection, filters, identifier_options)
        except SGIncompleteIdentifier:
            selection, identifier_options = None, {}

        options = AttrDict(identifier_options, **self.parse_options(options))
        self.apply_options(options)
        return (selection, options)

    def parse_identifier(self, identifier):
        return (None, identifier, {})

    def apply_identifier(self, selection, filters, options):

        if filters:
            selected_filters = zip(self.filters.keys(), filters)

            for f, value in selected_filters:
                if value is None or value in [getattr(self.filters[f], "selected_label", None), self.filters[f].value]:
                    continue
                try:
                    self.filters[f].selected_label = value
                except StopIteration:
                    self.filters[f].value = value

    def parse_options(self, options):
        if not options:
            options=""

        d = AttrDict([
            (list(self.FILTERS_OPTIONS.keys())[n], v)
            for n, v in enumerate(
                    [o for o in options.split(",") if "=" not in o]
            ) if v], **dict(o.split("=") for o in options.split(",") if "=" in o)
        )
        return d

    def apply_options(self, options):

        for k, v in options.items():
            if v is None:
                continue
            if k in self.filters:
                logger.debug(f"option: {k}={v}")
                try:
                    if self.filters[k].value != v:
                        self.filters[k].value = v
                except StopIteration:
                    raise SGException("invalid value for %s: %s" %(k, v))


    def new_media_source(self, *args, **kwargs):
        return self.MEDIA_SOURCE_CLASS.attr_class(
            provider_id = self.IDENTIFIER,
            *args,
            **kwargs
        )

    def new_listing(self, **kwargs):
        return self.LISTING_CLASS.attr_class(
            provider_id = self.IDENTIFIER,
            **kwargs
        )


    # def new_listing_attr(self, **kwargs):
    #     return self.LISTING_CLASS.attr_class(
    #         provider_id = self.IDENTIFIER,
    #         **kwargs
    #     )


    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    def should_download(self, listing):
        return listing.label in (
            list(self.config.rules)
            + list(config.settings.profile.rules.download)
        )

    def on_new_listing(self, listing):
        try:
            label = next(
                l
                for r, l in self.rule_map.items()
                if r.search(listing.title)
            )
            listing.label = label
            if self.should_download(listing):
                self.download(listing)

        except StopIteration:
            pass


    @property
    def config(self):
        return config.ConfigTree(
            config.settings.profile.providers.get(
                self.IDENTIFIER, {}
            )
        )

    @property
    def config_is_valid(self):
        def check_config(required, cfg):
            if isinstance(required, dict):
                for k, v in required.items():
                    if not k in cfg:
                        return False
                    if not check_config(required[k], cfg[k]):
                        return False
            else:
                for k in required:
                    if not k in cfg:
                        return False
            return True

        # return all([ self.config.get(x, None) is not None
                     # for x in getattr(self, "REQUIRED_CONFIG", [])
        return check_config(
            getattr(self, "REQUIRED_CONFIG", []),
            self.config
        )

    def get_source(self, selection, **kwargs):
        sources = sorted(
            selection.sources,
            key = lambda  s: s.rank
        )
        if not isinstance(sources, list):
            sources = [sources]

        return sources

    def play_args(self, selection, **kwargs):
        source = self.get_source(selection, **kwargs)
        return (source, kwargs)

    def filter_args(self):
        return {f: self.filters[f].value for f in self.filters}

    def extract_sources(self, listing, **kwargs):
        try:
            sources, kwargs = self.play_args(listing, **kwargs)
            kwargs.update({
                k: v
                for k, v in self.filter_args().items()
                if k not in kwargs
            })
        except SGStreamNotFound as e:
            logger.error(f"stream not found: {e}")
            return

        # FIXME: For now, we just throw playlists of media items at the default
        # player program and hope it can handle all of them.

        player_spec = None
        downloader_spec = None

        if not isinstance(sources, list):
            sources = [sources]

        for s in sources:
            if not s.media_type:
                # Try to set the content types of the source(s) with a HTTP HEAD
                # request if the provider didn't specify one.
                s.media_type = self.session.head(
                    s.locator
                ).headers.get("Content-Type").split("/")[0]

        return sources, kwargs

    def create_play_task(self, listing, **kwargs):

        sources, kwargs = self.extract_sources(listing, **kwargs)

        media_types = set([s.media_type for s in sources if s.media_type])

        player_spec = {"media_types": media_types}

        if media_types == {"image"}:
            downloader_spec = {None: None}
        else:
            downloader_spec = getattr(self.config, "helpers", None) or sources[0].helper

        return ListingsPlayMediaTask.attr_class(
            provider=self.NAME,
            title=listing.title,
            sources = sources,
            args = (player_spec, downloader_spec),
            kwargs = kwargs
        )

    def create_download_tasks(self, listing, index=None, **kwargs):

        with db_session:
            if isinstance(listing, model.InflatableMediaListing) and not listing.is_inflated:
                listing = selection.attach()
                state.asyncio.create_task(listing.inflate())
                listing = listing.detach()

        sources, kwargs = self.extract_sources(listing, **kwargs)

        if not isinstance(sources, list):
            sources = [sources]

        if "num" not in kwargs:
            kwargs["num"] = len(sources)

        for i, source in enumerate(sources):

            if index is not None and index != i:
                continue
            try:
                filename = source.download_filename(listing=listing, index=index, **kwargs)
            except SGInvalidFilenameTemplate as e:
                logger.warning(f"filename template for provider {self.IDENTIFIER} is invalid: {e}")
                raise
            downloader_spec = getattr(self.config, "helpers") or source.download_helper
            task = model.DownloadMediaTask.attr_class(
                provider = self.NAME,
                title = sanitize_filename(listing.title),
                sources = [source],
                listing = listing,
                dest = filename,
                args = (downloader_spec,),
                kwargs = dict(index=index, **kwargs),
                postprocessors = (self.config.get("postprocessors", None) or []).copy()
            )
            yield task


    async def play(self, listing, **kwargs):
        # sources, kwargs = self.extract_sources(listing, **kwargs)
        task = self.create_play_task(listing, **kwargs)
        yield state.task_manager.play(task)

    async def download(self, listing, index=None, no_task_manager=False, **kwargs):
        for task in self.create_download_tasks(listing, index=index, **kwargs):
            yield state.task_manager.download(task)

    def on_select(self, widget, selection):
        # self.play(selection)
        self.download(selection)

    @property
    def limit(self):
        return None

    def refresh(self):
        self.view.refresh()

    def reset(self):
        self.view.reset()

    def __str__(self):
        return self.NAME

    def __repr__(self):
        return f"<{type(self)}: {self.NAME}>"

    @property
    def auto_preview_enabled(self):
        return self.config.auto_preview.enabled

    @property
    def auto_preview_content_delay(self):
        return self.config.auto_preview.content_delay

    @property
    def auto_preview_thumbnail_delay(self):
        return self.config.auto_preview.thumbnail_delay

class PaginatedProviderMixin(object):

    DEFAULT_PAGE_SIZE = 50

    @property
    def limit(self):
        if getattr(self, "_limit", None) is not None:
            return self._limit
        return (self.config.get("page_size") or
                config.settings.profile.tables.get("page_size")
                or self.DEFAULT_PAGE_SIZE)

    @limit.setter
    def limit(self, value):
        self._limit = value


class BackgroundTasksMixin(object):

    DEFAULT_INTERVAL = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tasks = defaultdict(lambda: None)

    def run_in_background(self, fn, interval=DEFAULT_INTERVAL,
                          instant=False,
                          *args, **kwargs):

        logger.info(f"run_in_background {fn.__name__} {interval}")
        async def run():
            while True:
                logger.info(f"running task {fn.__name__} {args} {kwargs}")
                # self._tasks[fn.__name__] = None
                # fn(*args, **kwargs)
                # await state.event_loop.run_in_executor(
                #     None, lambda: fn(*args, **kwargs)
                # )

                # logger.info(fn)
                # await fn(*args, **kwargs)
                if instant:
                    logger.info("foo")
                    state.event_loop.create_task(fn(*args, **kwargs))
                    logger.info("bar")
                logger.debug(f"sleeping for {interval}")
                await asyncio.sleep(interval)
                # state.event_loop.run_in_executor(None, lambda: fn(*args, **kwargs))

                # state.loop.event_loop.enter_idle(lambda: fn(*args, **kwargs))

        self._tasks[fn.__name__] = state.event_loop.create_task(run())

    def on_activate(self):
        # self.update()
        for task in self.TASKS:
            args = []
            kwargs = {}
            interval = self.DEFAULT_INTERVAL
            if isinstance(task, tuple):
                # if len(task) == 4:
                #     (task, interval, args, kwargs) = task
                if len(task) == 3:
                    (func, interval, kwargs) = task
                    # (task, interval, args) = task
                elif len(task) == 2:
                    (func, interval) = task
            fn = getattr(self, func)
            self.run_in_background(fn, interval, *args, **kwargs)


    def on_deactivate(self):
        for name, task in self._tasks.items():
            if task:
                logger.info("deactivate cancel task")
                task.cancel()
                self._tasks[name] = None
        super().on_deactivate()


@keymapped()
class SynchronizedPlayerMixin(object):

    signals = ["keypress"]

    KEYMAP = {
        " ": "preview_selection",
        "meta p": "preview_all"
    }

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)
        if "focus" in self.signals:
            self.on_focus_handler = urwid.connect_signal(self, "focus", self.on_focus)
        # urwid.connect_signal(self, "requery", self.on_requery)
        self.player = None
        self.player_task = None
        self.queued_task = None
        self.pending_event_task = None
        self.on_focus_handler = None
        self.sync_player_playlist = False
        self.playlist_lock = asyncio.Lock()

    def extract_sources(self, listing, **kwargs):
        return (listing.sources if listing else [], kwargs)

    def create_play_task(self, listing, *args, **kwargs):
        return model.PlayMediaTask.attr_class(
            title=listing.title,
            sources=listing.sources,
            args=args,
            kwargs=kwargs
        )

    async def preview_selection(self):
        if not len(self.body):
            return

        if state.listings_view.preview_mode != "full":
            listing = self.selection.data_source
            source = listing.sources[0]
            await self.playlist_replace(source.locator)
        else:
            await self.preview_all(playlist_position=self.playlist_position)

        # await self.set_playlist_pos(self.playlist_position)

    @property
    def play_items(self):
        return []

    def new_listing(self, **kwargs):
        return model.TitledMediaListing.attr_class(**kwargs)

    def new_media_source(self, **kwargs):
        return model.MediaSource.attr_class(**kwargs)

    # def enable_focus_handler(self):
    #     if self.on_focus_handler:
    #         return
    #         # urwid.disconnect_by_key(self, "focus", self.on_focus_handler)
    #     self.on_focus_handler = urwid.connect_signal(self, "focus", self.on_focus)

    # def disable_focus_handler(self):
    #     if not self.on_focus_handler:
    #         return
    #     self.on_focus_handler = urwid.signals.disconnect_signal_by_key(self, "focus", self.on_focus_handler)

    async def preview_listing(self, listing, **kwargs):
        await state.task_manager.preview(
            listing, self, **kwargs
        )

    @keymap_command()
    async def preview_all(self, playlist_position=None):
        if not playlist_position:
            try:
                playlist_position = self.playlist_position
            except AttributeError:
                playlist_position = 0

        if len(self.play_items):
            listing = state.task_manager.make_playlist(self.playlist_title, self.play_items)
        else:
            listing = None

        await self.preview_listing(listing, playlist_position=playlist_position)

    @property
    def playlist_title(self):
        return f"playlist"

    def playlist_pos_to_row(self, pos):
        return self.play_items[pos].row_num

    def row_to_playlist_pos(self, row):
        try:
            media_listing_id = self[row].data.media_listing_id
        except IndexError:
            return 0
        try:
            return next(
                n for n, i in enumerate(self.play_items)
                if i.media_listing_id == media_listing_id
            )
        except StopIteration:
            return 0

    async def set_playlist_pos(self, pos):
        if not state.task_manager.preview_player:
            return

        # if await state.task_manager.preview_player.command(
        #     "get_property", "playlist-pos"
        # ) == pos:
        #     return

        await state.task_manager.preview_player.command(
            "set_property", "playlist-pos", pos
        )

    def run_queued_task(self):

        if self.pending_event_task:
            state.event_loop.create_task(self.queued_task())
            self.pending_event_task = None

    @property
    def playlist_position(self):
        return self.row_to_playlist_pos(self.focus_position)

    # FIXME: inner_focus comes from MultiSourceListingMixin
    async def sync_playlist_position(self):

        if state.task_manager.preview_player and len(self):

            try:
                index = self.playlist_position
            except AttributeError:
                return
            if index is None:
                return

            delay = (
                self.provider.auto_preview_content_delay
                if state.listings_view.preview_mode == "full"
                else self.provider.auto_preview_thumbnail_delay
            )
            if delay:
                await asyncio.sleep(delay)
            logger.info("calling set_playlist_pos")
            await self.set_playlist_pos(index)

            # async def sync_playlist_async(index): # O_o

            # if self.pending_event_task:
            #     self.pending_event_task.cancel()

            # self.pending_event_task = state.event_loop.create_task(
            #     sync_playlist_async(index)
            # )
            # await sync_playlist_async(index)
            # await self.pending_event_task = sync

    def on_focus(self, source, position):
        if self.provider.auto_preview_enabled:
            state.event_loop.create_task(self.sync_playlist_position())
        if len(self):
            with db_session:
                try:
                    listing = self[position].data_source#.attach()
                except (TypeError, IndexError): # FIXME
                    return
                # listing.on_focus()
                if hasattr(listing, "on_focus") and listing.on_focus():
                    async def reload():
                        self.invalidate_rows([listing.media_listing_id])
                        self.selection.close_details()
                        self.selection.open_details()
                        self.refresh()
                        await self.preview_all(playlist_position=self.playlist_position)
                    state.event_loop.create_task(reload())
        state.loop.draw_screen()

    async def playlist_replace(self, url, pos=None):

        async with self.playlist_lock:
            if pos is None:
                pos = self.focus_position

            count = len(self)

            await state.task_manager.preview_player.command(
                "loadfile", url, "append"
            )

            logger.info(f"count: {count}")

            # if pos == self.focus_position:
            #     logger.info("idle")
            #     await state.task_manager.preview_player.command(
            #         "playlist-play-index", "none"
            #     )


            logger.info(f"move: {count} -> {pos}")
            await state.task_manager.preview_player.command(
                "playlist-move", str(count), str(pos)
            )

            logger.info(f"remove: {pos+1}")
            await state.task_manager.preview_player.command(
                "playlist-remove", str(pos+1)
            )

            if pos == self.focus_position:
                await state.task_manager.preview_player.command(
                    "playlist-play-index", pos
                )


            # if pos == self.focus_position:
            #     logger.info(f"play: {pos}")
            #     await state.task_manager.preview_player.command(
            #         "playlist-play-index", pos
            #     )


    # async def download(self):

    #     row_num = self.focus_position
    #     listing = self[row_num].data_source
    #     index = self.playlist_position

    #     # FIXME inner_focus comes from MultiSourceListingMixin
    #     self.provider.download(listing, index = self.inner_focus or 0)

    def quit_player(self):
        try:
            state.event_loop.create_task(self.player.quit())
        except BrokenPipeError:
            pass

@model.attrclass()
class ListingsPlayMediaTask(model.PlayMediaTask):
    pass

@keymapped()
class SynchronizedPlayerProviderMixin(SynchronizedPlayerMixin):

    def new_listing(self, **kwargs):
        return self.provider.new_listing(**kwargs)

    def new_media_source(self, **kwargs):
        return self.provider.new_media_source(**kwargs)

    @property
    def play_items(self):
        return [
            AttrDict(
                media_listing_id = row.data.media_listing_id,
                title = f"{self.playlist_title} {utils.sanitize_filename(row.data.title)}",
                created = row.data.created,
                # feed = row.data.channel.name,
                # locator = row.data.channel.locator,
                index = index,
                row_num = row_num,
                count = len(row.data.sources),
                locator = (
                    (source.locator or getattr(source, "preview_locator", None))
                    if state.listings_view.preview_mode == "full"
                    else (getattr(source, "preview_locator", None) or source.locator)
                )
            )
            for (row_num, row, index, source) in [
                    (row_num, row, index, source) for row_num, row in enumerate(self)
                    for index, source in enumerate(row.data.sources)
                    if not source.is_bad
            ]
        ]

    def create_play_task(self, listing, *args, **kwargs):
        if not listing:
            listing = self.empty_listing
        return self.provider.create_play_task(listing, *args, **kwargs)


    def extract_sources(self, listing, **kwargs):
        if not listing:
            return ([], kwargs)
        return self.provider.extract_sources(listing, **kwargs)

    def reset(self, *args, **kwargs):
        self.sync_player_playlist = False
        # self.disable_focus_handler()
        super().reset(*args, **kwargs)

        if self.provider.auto_preview_enabled:
            # async def preview():
            #     if delay:
            #         await asyncio.sleep(delay)
            #         await self.preview_all()
            state.event_loop.create_task(self.preview_all())



class DetailBox(urwid.WidgetWrap):

    def __init__(self, listing, parent_table):
        self.listing = listing
        self.parent_table = parent_table
        self.table = self.detail_table()
        self.box = urwid.BoxAdapter(self.table, 1)
        super().__init__(self.box)

    def detail_table(self):
        columns = self.parent_table.columns.copy()
        # next(c for c in columns if c.name=="title").truncate = True
        return DetailDataTable(
            self.listing,
            self.parent_table, columns=columns
        )

    @property
    def focus_position(self):
        return self.table.focus_position

    def keypress(self, size, key):
        return super().keypress(size, key)


@keymapped()
class DetailDataTable(BaseDataTable):

    KEYMAP = {
        "j": "keypress down",
        "k": "keypress up",
    }

    with_header = False

    def __init__(self, listing, parent_table, columns=None):
        self.listing = listing
        self.parent_table = parent_table
        super().__init__(columns=columns)

    def keypress(self, size, key):
        return super().keypress(size, key)

    def query(self, *args, **kwargs):
        return [
            dict(
                source,
                **dict(
                    title=f"[{i+1}/{len(source.listing.sources)}] {source.listing.title}",
                    feed = source.listing.feed,
                    created = source.listing.created,
                    read = source.listing.attrs.get("parts_read", {}).get(i, False)
                ))
              for i, source in enumerate(self.listing.sources)
        ]

    def row_attr_fn(self, position, data, row):
        if not getattr(data.listing, "read", False):
            try:
                if not data.listing.attrs["parts_read"][str(self.focus_position)]:
                    return "unread"
            except (IndexError, KeyError):
                pass
        return None

@keymapped()
class MultiSourceListingMixin(object):

    KEYMAP = {

    }
    with_sidecar = True

    DETAIL_BOX_CLASS = DetailBox

    def listings(self, offset=None, limit=None, *args, **kwargs):
        for listing in super().listings(offset=offset, limit=limit, *args, **kwargs):
            yield (listing, dict(source_count=len(listing.sources)))

    def decorate(self, row, column, value):

        if column.name == "title":
            listing = row.data_source.attach()
            # source_count = self.df[listing.media_listing_id, "source_count"]
            source_count = len(row.get("sources"))
            if source_count > 1:
                value = f"[{source_count}] {row.get('title')}"

        return super().decorate(row, column, value)

    def detail_fn(self, data):
        if len(data.sources) <= 1:
            return
        # urwid.connect_signal(box.table, "focus", lambda s, i: self.on_focus(s, self.focus_position))

        # def on_inner_focus(source, position):
        #     logger.info(position)
        box = self.DETAIL_BOX_CLASS(data, self)
        urwid.connect_signal(box.table, "focus", lambda s, i: self.on_focus(s, self.focus_position))
        return box

        # return self.detail_box

    async def sync_playlist_position(self):
        return await super().sync_playlist_position()


    def on_inner_focus(self, position):
        state.event_loop.create_task(self.sync_playlist_position())

    @property
    def playlist_position(self):
        return self.row_to_playlist_pos(self.focus_position) + self.inner_focus

    @property
    def inner_table(self):
        if self.selection.details_open and self.selection.details:
            return self.selection.details.contents.table
        return None

    @property
    def inner_focus(self):
        if self.inner_table:
            return self.inner_table.focus_position
        return 0

    def row_attr_fn(self, position, data, row):
        # logger.info(self.inner_table)
        if len(data.sources) > 1:
            box = row.details.contents
            with db_session:
                listing = box.listing
                sources = sorted(listing.sources, key=lambda s: s.rank)
                source = sources[box.table.focus_position]
                if isinstance(source, BaseModel):
                    source = source.attach()
            return "unread" if not source.seen else None
        else:
            return "unread" if not data.read else None
