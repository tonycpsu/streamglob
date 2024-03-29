import logging
logger = logging.getLogger(__name__)
import sys
import os
import abc
import asyncio
import dataclasses
import re
from itertools import chain
from collections.abc import Mapping
# import textwrap
# import tempfile

import urwid
from orderedattrdict import AttrDict, DefaultAttrDict, Tree
from pony.orm import *
from panwid.dialog import *
from panwid.keymap import *
from pydantic import BaseModel
import imgkit

# from .widgets import *
from . import widgets
from .filters import TextFilter
# from ..session import *
from ..import session
from ..exceptions import *
from ..state import *
from ..rules import HighlightRuleConfig, HighlightRule
from ..programs import Player, Downloader
from .. import model
from .. import config
from .. import utils
from  ..utils import classproperty


# @keymapped()
class BaseProviderView(widgets.StreamglobView):

    def update(self):
        pass

    def keypress(self, size, key):
        return super().keypress(size, key)

    def refresh(self):
        pass

    # def init_config(self, config):
    #     pass

    def selectable(self):
        return True

    @property
    def config(self):
        return self.provider.config

# class TabularProviderMixin(object):

#     def init_config(self):
#         super().init_config()
#         for name, opts in self.config.display.tables.columns.items():
#             opts = {
#                 k: (
#                     v
#                     if k != "value"
#                     else (
#                             self.token_value(v.split(".")[1])
#                             if v.startswith("token.")
#                             else v
#                     )
#                 )
#                 for k, v in (opts or {}).items()
#             }
#             if name in self.attributes:
#                 self.attributes[name].update(**opts)
#             else:
#                 # import ipdb; ipdb.set_trace()
#                 self.attributes[name] = AttrDict(opts)


@keymapped()
class SimpleProviderView(BaseProviderView):

    PROVIDER_BODY_CLASS = widgets.ProviderDataTable

    KEYMAP = {
        "[": ("cycle_filter", [0, -1]),
        "]": ("cycle_filter", [0, 1]),
        "{": ("cycle_filter", [1, -1]),
        "}": ("cycle_filter", [1, 1]),
        "-": ("cycle_filter", [2, -1]),
        "=": ("cycle_filter", [2, 1]),
        "_": ("cycle_filter", [3, -1]),
        "+": ("cycle_filter", [3, 1]),
        "ctrl p": ("cycle_filter", [0, -1]),
        "ctrl n": ("cycle_filter", [0, 1]),
        "ctrl f": ("focus_filter", ["search"]),
        "ctrl k": "clear_search_query",
        "/": ("focus_filter", ["search"]),
        "ctrl r": "reset_provider"
        # "ctrl d": "download"
    }

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body#(self.provider, self)
        self.toolbar = widgets.FilterToolbar(self.provider.filters)
        # self.body = self.PROVIDER_BODY_CLASS(self.provider, self)
        # urwid.connect_signal(self.toolbar, "filter_change", self.filter_change)
        # urwid.connect_signal(self.body, "select", self.provider.on_select)
        try:
            urwid.connect_signal(self.body, "cycle_filter", self.cycle_filter)
        except NameError:
            pass
        try:
            urwid.connect_signal(self.body, "keypress", self.on_keypress)
        except NameError:
            pass

        self.pile  = urwid.Pile([
            ("pack", self.toolbar),
            ("weight", 1, self.body)
        ])
        self.pile.focus_position = 1
        super().__init__(self.pile)

    @property
    def tmp_dir(self):
        return self.provider.tmp_dir

    @keymap_command
    def cycle_filter(self, n, step):
        self.toolbar.cycle_filter(n, step)

    def on_keypress(self, source, key):
        self.keypress((100, 100), key)

    def on_activate(self):
        # self.reset()
        self.body.on_activate()

    def on_deactivate(self):
        self.body.on_deactivate()

    def keypress(self, size, key):
        return super().keypress(size, key)

    def reset_provider(self):
        self.provider.reset()

    def reset(self):
        self.body.reset()

    # def __getattr__(self, attr):
    #     return getattr(self.body, attr)

    def apply_search_query(self, query):
        # FIXME: assumes body is a data table
        self.body.apply_filters([
            lambda listing: query.lower() in listing["title"].lower()
        ])
        self.body.refresh()

    def clear_search_query(self):
        self.toolbar.filters["search"].value = ""
        self.reset()

    def focus_filter(self, name):
        self.toolbar.focus_filter(name)
        self.pile.focus_position = 0

    def sort(self, field, reverse=False):
        self.body.sort_by_column(field, reverse=reverse)
        self.body.reset()


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

    def reset(self):
        pass

MEDIA_SPEC_RE=re.compile(r"(?:/([^:]+))?(?::(.*))?")

class BaseProvider(
        session.SessionMixin,
        config.HierarchicalConfigMixin,
        widgets.PlayListingProviderMixin,
        widgets.DownloadListingProviderMixin,
        abc.ABC,
        widgets.Observable
):
    """
    Abstract base class from which providers should inherit from
    """

    SESSION_CLASS = session.StreamSession
    LISTING_CLASS = model.TitledMediaListing

    MEDIA_TYPES = None
    RPC_METHODS = []

    def __init__(self, *args, **kwargs):
        self._view = None
        self._session = None
        self._active = False
        self._filters = AttrDict({n: f(provider=self, name=n)
                                  for n, f in self.FILTERS.items() })
        self.attributes = self.column_config or self.ATTRIBUTES
        self.filters["search"].connect("changed", self.on_search_change)
        super().__init__(*args, **kwargs)
        logger.info(f"provider {self.CONFIG_IDENTIFIER} initialized")

    @property
    def column_config(self):
        def parse_column_value(v):
            if not isinstance(v, dict):
                return v
            elif isinstance(v, dict):
                value_type, opts = next(iter(v.items()))
                if value_type == "token":
                    attr = opts["attr"]
                    default = opts.get("default")
                    return self.token_value(attr, default=default)
                else:
                    raise NotImplementedError

        return config.ConfigTree([
            (
                column,
                config.ConfigTree(
                    self.ATTRIBUTES.get(column, {}), **{
                        k: (
                            v
                            if k != "value"
                            else parse_column_value(v)
                        )
                        for k, v in options.items()
                    }
                )
            )
            for column, options in [
                    (
                        list(column.keys())[0]
                        if isinstance(column, dict)
                        else column,
                        (
                            (list(column.values())[0] or {})
                            if isinstance(column, dict)
                            else {}
                        )
                    )
                for column in (self.config.display.tables.columns or [])
            ]
        ])

    @property
    def ATTRIBUTES(self):
        return AttrDict(
            title={"width": ("weight", 1), "truncate": True},
            group={"width": 20, "truncate": True},
        )

    def token_value(self, token, default=None):
        def inner(table, row):
            tokens = self.rules.tokenize(
                row.title
            )
            return next(
                (
                    value
                    for ((label, attr), value)
                    in tokens
                    if label == token
                ),
                default
            )
        return inner


    @property
    def tmp_dir(self):
        if not hasattr(self, "_tmp_dir"):
            tmp_dir = os.path.join(state.tmp_dir, self.CONFIG_IDENTIFIER)
            os.makedirs(tmp_dir)
            self._tmp_dir = tmp_dir
        return self._tmp_dir

    @property
    def conf_dir(self):
        if not hasattr(self, "_conf_dir"):
            conf_dir = os.path.join(
                config.settings._config_dir,
                self.CONFIG_IDENTIFIER
            )
            if not os.path.exists(conf_dir):
                os.makedirs(conf_dir)
            self._conf_dir = conf_dir
        return self._conf_dir

    def init_config(self):
        with db_session:
            try:
                self.provider_data = model.ProviderData.get(name=self.CONFIG_IDENTIFIER).settings
            except AttributeError:
                self.provider_data = model.ProviderData(name=self.CONFIG_IDENTIFIER).settings

        self.load_rules()

    def apply_settings(self):
        for name, f in self.filters.items():
            value = self.default_filter_values.get(name, None)
            if value:
                try:
                    f.value = value
                except (ValueError,):
                    # import ipdb; ipdb.set_trace()
                    pass

    @property
    def conf_rules(self):
        if not getattr(self, "_conf_rules", None):
            self._conf_rules = config.Config(
            os.path.join(
                self.conf_dir,
                "rules.yaml"
            )
        )
        return self._conf_rules

    def load_rules(self):

        self._conf_rules = None
        self.rules = HighlightRuleConfig(
            os.path.join(
                self.conf_dir,
                "rules.yaml"
            )
        )

    @property
    def default_filter_values(self):
        return AttrDict()

    @db_session
    def save_provider_data(self):
        model.ProviderData.get(name=self.CONFIG_IDENTIFIER).settings = self.provider_data
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
    def helper(self):
        return None

    @property
    def PREVIEW_TYPES(self):
        return ["default"]

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
    def toolbar(self):
        return self.view.toolbar

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
        if isinstance(self.view, InvalidConfigView):
            return
        self.reset()

    def on_deactivate(self):
        if isinstance(self.view, InvalidConfigView):
            return
        self.view.on_deactivate()

    @property
    def VIEW(self):
        return SimpleProviderView(self, widgets.ProviderDataTable(self))

    # @abc.abstractmethod
    def make_view(self):
        logger.info(f"provider {self.CONFIG_IDENTIFIER} initializing view")
        if not self.config_is_valid:
            return InvalidConfigView(self.NAME, getattr(self, "REQUIRED_CONFIG", []))
        return self.VIEW

    @classproperty
    def CONFIG_IDENTIFIER_CLASS(cls):
        return BaseProvider

    @classproperty
    def CONFIG_IDENTIFIER_KEY(cls):
        return "providers"

    # @classproperty
    # def IDENTIFIERS(cls):
    #     return list(
    #         dict.fromkeys(
    #             [
    #                 c.__module__.split(".")[-1]
    #                 for c in cls.__mro__
    #                 if issubclass(c, BaseProvider)
    #             ]
    #         )
    #     )

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

        async def apply_search_async():
            await asyncio.sleep(1)
            await self.apply_search_query(value)

        self.search_task = state.event_loop.create_task(apply_search_async())

    async def apply_search_query(self, query):
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
                except ValueError:
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
            provider_id = self.CONFIG_IDENTIFIER,
            *args,
            **kwargs
        )

    def new_listing(self, **kwargs):

        (extra_attrs, entity_attrs) = [dict(l) for l in utils.partition(
            lambda t: t[0] in self.LISTING_CLASS._adict_.keys(),
            kwargs.items()
        )]

        return self.LISTING_CLASS.attr_class(
            provider_id=self.CONFIG_IDENTIFIER,
            attrs=extra_attrs,
            **entity_attrs
        )


    async def play(self, listing, **kwargs):
        task = self.create_play_task(listing, **kwargs)
        yield state.task_manager.play(task)

    async def download(self, listing, index=None, no_task_manager=False, **kwargs):
        for task in self.create_download_tasks(listing, index=index, **kwargs):
            yield state.task_manager.download(task)


    def translate_template(self, template):
        return template

    def sort(self, field, reverse=False):
        self.view.sort(field, reverse=reverse)

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    # def should_download(self, listing):
    #     return listing.label in (
    #         list(self.config.rules)
    #         + list(config.settings.profile.rules.download)
    #     )

    def action_download(self, listing):
        self.download(listing)

    def action_mark_read(self, listing):
        listing.mark_read()

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
        sources = selection.sources
        if isinstance(selection, model.MediaListing):
            with db_session:
                listing = model.MediaListing[selection.media_listing_id]
                sources = sorted(
                    listing.sources,
                    key = lambda  s: getattr(s, "rank", 0)
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
        # import ipdb; ipdb.set_trace()

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

    # def create_preview_task(self, listing, **kwargs):
    #     return ListingsPlayMediaTask.attr_class(
    #         provider=self.NAME,
    #         title=listing.title,
    #         sources=listing.sources
    #     )

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
    def playlist_title(self):
        return f"[{self.CONFIG_IDENTIFIER}"

    @property
    def auto_preview_enabled(self):
        # return state.listings_view.auto_preview_mode
        return not self.config.auto_preview.disabled

    # @property
    # def auto_preview_default(self):
    #     return self.config.auto_preview.default if self.auto_preview_enabled else "default"

    @property
    def strip_emoji(self):
        return (self.config.get("strip_emoji") or
                config.settings.profile.tables.get("strip_emoji")
                or False)

    @property
    def translate(self):
        return (self.config.get("translate") or
                config.settings.profile.tables.get("translate")
                or False)

    @property
    def translate_src(self):
        return "auto"

    @property
    def translate_dest(self):
        return (self.config.get("translate_dest") or
                config.settings.profile.tables.get("translate_dest")
                or "en")

    @property
    def output_path(self):
        return (
            self.config.get_path("output.path")
            or
            config.settings.profile.get_path("output.path")
        )

    @property
    def check_downloaded(self):
        return (
            self.config.get_path("output.check_downloaded")
            or
            config.settings.profile.get_path("output.check_downloaded")
            or False
        )


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
        self._tasks = DefaultAttrDict(lambda: None)

    def run_in_background(self, fn, interval=DEFAULT_INTERVAL,
                          instant=False,
                          *args, **kwargs):

        logger.debug(f"run_in_background {fn.__name__} {interval}")
        async def run():
            while True:
                logger.debug(f"running task {fn.__name__} {args} {kwargs}")

                if instant:
                    state.event_loop.create_task(fn(*args, **kwargs))
                logger.debug(f"sleeping for {interval}")
                await asyncio.sleep(interval)
                # state.event_loop.run_in_executor(None, lambda: fn(*args, **kwargs))

                # state.loop.event_loop.enter_idle(lambda: fn(*args, **kwargs))

        self._tasks[fn.__name__] = state.event_loop.create_task(run())

    def on_activate(self):
        super().on_activate()
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
                logger.debug("deactivate cancel task")
                task.cancel()
                self._tasks[name] = None
        super().on_deactivate()

class PreviewState(object):

    def __init__(self, stages):
        self.stages = stages
        self.lock = asyncio.Lock()
        self.previews = DefaultAttrDict(lambda: asyncio.Future())
        for stage in stages:
            self.previews[stage.mode] = asyncio.Future()

    def __getitem__(self, key):
        return self.previews.__getitem__(key)

    def __setitem__(self, key, value):
        self.previews.__setitem__(key, value)

    @property
    def available_previews(self):
        return [
            k
            for k, v in self.previews.items()
            if v and v.done() and v.result()
        ]

    def get_available_previews_text(self, current_stage):
        stages = self.previews.keys()
        loaded = [p for p in self.previews.values() if p.done()]
        return f"""{
        ", ".join(
            "[%s]" %(stage) if stage == current_stage else stage
            for stage in stages
            if stage in self.available_previews
        )
        }{"…" if len(loaded) < len(stages) else ""}"""



@keymapped()
class SynchronizedPlayerMixin(object):

    signals = ["keypress"]

    KEYMAP = {
        ".": "preview_content",
        "meta p": "preview_all"
    }

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)
        if "requery" in self.signals:
            urwid.connect_signal(self, "requery", self.on_requery)

        state.task_manager.connect("player-load-failed", self.on_player_load_failed)
        # self.player = None
        self.player_task = None
        self.queued_task = None
        self.pending_event_tasks = []
        self.on_focus_handler = None
        self.sync_player_playlist = False
        self.video_filters = []
        self.playlist_lock = asyncio.Lock()
        self.preview_lock = asyncio.Lock()
        self.preview_stage = -1

    @property
    def preview_stage_mode(self):
        return self.preview_stages[self.preview_stage].mode

    @property
    def previews(self):
        if not hasattr(self, "_previews"):
            self._previews = DefaultAttrDict(
                lambda: PreviewState(self.preview_stages)
            )
            # self._previews = DefaultAttrDict(lambda: DefaultAttrDict(lambda: asyncio.Future()))
        return self._previews

    def on_requery(self, source, count):

        self.disable_focus_handler()
        while len(self.pending_event_tasks):
            t = self.pending_event_tasks.pop()
            t.cancel()
        # if state.get("tui_enabled"):
        #     state.event_loop.create_task(self.preview_all())
        self.enable_focus_handler()

    def load_more(self, position):
        while len(self.pending_event_tasks):
            t = self.pending_event_tasks.pop()
            t.cancel()
        super().load_more(position)
        state.event_loop.create_task(self.preview_all())

    def extract_sources(self, listing, **kwargs):
        return (listing.sources if listing else [], kwargs)

    async def preview_selection(self):

        if not len(self):
            return

        while len(self.pending_event_tasks):
            t = self.pending_event_tasks.pop()
            t.cancel()

        path = getattr(self.selected_source, "local_path", self.selected_source.locator)

        location = path or self.selected_source.locator
        await self.playlist_replace(location)

        # if state.listings_view.preview_mode != "full":
            # listing = self.selection.data_source
            # source = listing.sources[0]

        # else:
        #     await self.preview_all(playlist_position=self.playlist_position)

        # await self.set_playlist_pos(self.playlist_position)

    @property
    def play_items(self):
        return []

    def new_media_source(self, **kwargs):
        return model.MediaSource.attr_class(**kwargs)

    def enable_focus_handler(self):
        if self.on_focus_handler:
            return
            # urwid.disconnect_by_key(self, "focus", self.on_focus_handler)
        self.on_focus_handler = urwid.connect_signal(self, "focus", self.on_focus)

    def disable_focus_handler(self):
        if not self.on_focus_handler:
            return
        self.on_focus_handler = urwid.signals.disconnect_signal_by_key(self, "focus", self.on_focus_handler)

    async def preview_listing(self, listing, **kwargs):
        await state.task_manager.preview(
            listing, self, **kwargs
        )

    @keymap_command()
    async def preview_all(self, playlist_position=None):

        # import ipdb; ipdb.set_trace()

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
        # await self.preview_listing(listing)

    def playlist_pos_to_row(self, pos):
        return self.play_items[pos].row_num

    def row_to_playlist_pos(self, row):
        try:
            media_listing_id = self[row].data.media_listing_id
        except IndexError:
            return row
        try:
            return next(
                n for n, i in enumerate(self.play_items)
                if i.media_listing_id == media_listing_id
            )
        except (AttributeError, StopIteration):
            # FIXME
            return row

    async def update_video_filters(self, filters=None):

        if not (state.task_manager.preview_player and len(self)):
            return

        # logger.debug(f"filters before: {self.video_filters}")

        filters = filters or [
            "upscale",
            "framerate",
            "box",
            "playlist",
            "title",
            "previews"
        ]
        if not filters:
            return

        pos = self.playlist_position

        # import ipdb; ipdb.set_trace()
        remove_filters = [
            f"@{f}" for f in filters
            if any([vf.startswith(f"@{f}") for vf in self.video_filters])
        ]

        if remove_filters:
            await state.task_manager.preview_player.command(
                "vf", "remove", *remove_filters
            )

        # try:
        #     event = await state.task_manager.preview_player.wait_for_event(
        #         "video-reconfig", 1
        #     )
        # except StopAsyncIteration:
        #     pass

        self.video_filters = [f for f in self.video_filters if f not in filters]

        # try to ensure video filters aren't lost by waiting for next track
        # try:
        #     event = await state.task_manager.preview_player.wait_for_event(
        #         "playback-restart", 1
        #     )
        # except StopAsyncIteration:
        #     pass

        cfg = config.settings.profile.display.overlay

        ox = str(cfg.x or 0)
        oy = str(cfg.y or 0)

        padding = cfg.text.padding or 0

        added_filters = []

        if "upscale" in filters:
            upscale = cfg.upscale or 1280
            vf_scale = f"@upscale:lavfi=[scale=w=max(iw\\,{upscale}):h=-2]"
            added_filters = [vf_scale]

        if "framerate" in filters:
            frame_count = await state.task_manager.preview_player.command(
                "get_property", "estimated-frame-count"
            )
            logger.debug(f"frame_count: {frame_count}")
            if frame_count is None or frame_count <= 1:
                vf_framerate = f"@framerate:framerate=fps={cfg.fps or 30}"
                added_filters += [vf_framerate]
                # filters += [vf_scale, vf_framerate]

        if "box" in filters:
            if cfg.box:
                box_color = cfg.box.color.default or "000000@0.5"
                if self.playlist_position == len(self.play_items)-1:
                    box_color = cfg.box.color.end or box_color
                vf_box = f"@box:drawbox=x={ox}:y={oy}:w=iw:h=(ih/{cfg.text.size or 50}*2)+{padding}:color={box_color}:t=fill"
                added_filters.append(vf_box)

        # import ipdb; ipdb.set_trace()

        try:
            title = self.play_items[pos].title
        except IndexError:
            logger.warning(f"IndexError in play_items: {pos}, {len(self.play_items)}")
            return

        text_map = dict(
            playlist=lambda: f"{self.playlist_title} {self.playlist_position_text}",
            title=lambda: title,
            previews=lambda: self.previews[self.selected_source.key].get_available_previews_text(
                self.preview_stage_mode
            )
        )
        # FIXME
        # import ipdb; ipdb.set_trace()
        if set(filters).intersection(set(text_map.keys())):
        # if "playlist" in filters or "title" in filters:
            for element, text_fn in text_map.items():
                text = utils.ffmpeg_escape(text_fn())
                el_cfg = cfg.get(element, cfg)
                color = el_cfg.text.color.default or cfg.text.color.default or "white"
                if self.playlist_position == len(self.play_items)-1:
                    color = el_cfg.text.color.end or cfg.text.color.end or color
                elif getattr(self.active_table.selected_source, "local_path", None):
                    color = el_cfg.text.color.downloaded or cfg.text.color.downloaded or color

                font = utils.ffmpeg_escape(el_cfg.text.font or cfg.text.font or "sans")
                size = el_cfg.text.size or cfg.text.size or 50
                shadow_color = el_cfg.text.shadow.color or cfg.text.shadow.color or "black"
                border_color = el_cfg.text.border.color or cfg.text.border.color or "black"
                border_width = el_cfg.text.border.width or cfg.text.border.width or 1
                shadow_x = el_cfg.text.shadow.x or cfg.text.shadow.x or 1
                shadow_y = el_cfg.text.shadow.y or cfg.text.shadow.y or 1

                x = el_cfg.x or ox
                y = el_cfg.y or oy
                if isinstance(x, int):
                    x = str(x)
                if isinstance(x, dict):
                    scroll_start = str(x.scroll.start or 0).format(ox=ox)
                    scroll_speed = x.scroll.speed or 5
                    pause = x.scroll.pause or 3
                    x=f"{scroll_start} + w - w/{scroll_speed}*mod(if(lt(t, {pause}), 0, if(gt(text_w, w), t-{pause}, 0) ),{scroll_speed}*(w+tw/2)/w)-w"
                x = x.format(ox=ox, oy=oy, padding=padding)
                # import ipdb; ipdb.set_trace()
                y = str(y).format(ox=ox, oy=oy, padding=padding)
                ox = x
                oy = y
                vf_text=f"""@{element}:lavfi=[drawtext=text='{text}':fontfile='{font}':\
x='{x}':y='{y}':fontsize=(h/{size}):fontcolor={color}:bordercolor={border_color}:\
borderw={border_width}:shadowx={shadow_x}:shadowy={shadow_y}:shadowcolor={shadow_color}:expansion=none]"""
                logger.debug(vf_text)
                if element in filters:
                    added_filters.append(vf_text)

        # return
        await state.task_manager.preview_player.command(
            "vf", "add", ",".join(added_filters)
        )
        self.video_filters += added_filters
        # logger.debug(f"filters after: {self.video_filters}")


    async def set_playlist_pos(self, pos):

        if not state.task_manager.preview_player:
            return

        await self.update_video_filters()

        await state.task_manager.preview_player.command(
            "set_property", "playlist-pos", pos
        )

    @property
    def playlist_position(self):
        return self.row_to_playlist_pos(self.focus_position)

    async def playlist_position_changed(self, pos):
        pass

    async def preview_content_default(self, cfg, listing, source):
        return source.locator_preview

    async def preview_content_cover(self, cfg, listing, source):
        return listing.cover_locator
        # await self.playlist_replace(source.locator_thumbnail, idx=position)

    async def preview_content_thumbnail(self, cfg, listing, source):
        logger.debug(f"preview_content_thumbnail")
        if source.locator_thumbnail is None:
            logger.debug("no thumbnail")
            return
        return source.locator_thumbnail
        # await self.playlist_replace(source.locator_thumbnail, idx=position)

    async def preview_content_full(self, cfg, listing, source):
        logger.debug(f"preview_content_full")
        # if getattr(source, "locator_thumbnail", None) is None:
        #     logger.debug("full: no thumbnail")
        #     return
        if source.locator_play is None:
            return
        return source.locator_play
        # await self.playlist_replace(source.locator, idx=position)


    async def get_preview(self, stages, listing, source):

        previews = self.previews[source.key]

        async def generate_preview(cfg):

            # import ipdb; ipdb.set_trace()
            preview_fn = getattr(self, f"preview_content_{cfg.mode}")

            try:
                res = await preview_fn(cfg, listing, source)
                previews[cfg.mode].set_result(res)
            except asyncio.exceptions.CancelledError:
                logger.warning("CancelledError from preview function")
                previews[cfg.mode] = asyncio.Future()

        # import ipdb; ipdb.set_trace()

        async with previews.lock:
            for cfg in stages:
                if not previews[cfg.mode].done():
                    if isinstance(cfg.preload, int):
                        await asyncio.sleep(cfg.preload)
                    await generate_preview(cfg)
                    if previews[cfg.mode].done() and previews[cfg.mode].result():
                        break

        await self.update_video_filters()
        return previews[cfg.mode]

    @property
    def preview_stage_default(self):
        return (
            self.config.auto_preview.get("default", None)
        ) or "default"

    @property
    def preview_stages(self):
        return [
            Tree(
                mode=self.preview_stage_default,
                duration=self.config.auto_preview.get("duration")
            )
        ] + (
            self.config.auto_preview.stages
            or []
        )

    async def preview_content(self):

        listing = self.selected_listing
        source = self.selected_source
        position = self.playlist_position

        if self.config.auto_preview.delay:
            logger.debug(f"sleeping: {self.config.auto_preview.delay}")
            await asyncio.sleep(self.config.auto_preview.delay)

        self.preview_stage = (self.preview_stage+1) % len(self.preview_stages)

        stages = self.preview_stages[self.preview_stage:]
        preview_state = self.previews[source.key]

        for stage_index in range(len(stages)):

            cfg = stages[stage_index]
            logger.debug(f"stage: {cfg.mode} {self.preview_stage}")

            if cfg.mode in [None, "default"]:
                await self.set_playlist_pos(position)
                break

            if cfg.media_types and source.media_type not in cfg.media_types:
                continue

            preview = await (await self.get_preview([cfg], listing, source))
            if preview:
                locator = getattr(preview, "locator", preview)
                video_track = getattr(preview, "video_track", None)
                audio_track = getattr(preview, "audio_track", None)
                await self.playlist_replace(locator, idx=position, video_track=video_track, audio_track=audio_track)
                # await self.set_playlist_pos(position)

                self.pending_event_tasks.append(
                    asyncio.create_task(
                        self.get_preview(stages[stage_index:], listing, source)
                    )
                )
            else:
                continue

            duration = await self.preview_duration(cfg, listing)
            if duration is not None: # advance automatically
                await asyncio.sleep(duration)
            else: # wait for next manual advance
                break

        self.preview_stage += stage_index
        # self.preview_stage = (self.preview_stage+stage_index+1) % len(self.preview_stages)
        # if self.preview_stage == 0 and self.preview_stage_default == "default":
        #     self.preview_stage = 1
        logger.debug(f"new stage: {self.preview_stage}")


    async def preview_duration(self, cfg, listing):

        return (
            cfg.duration
            if "duration" in cfg
            else self.config.auto_preview.duration
            if "duration" in self.config.auto_preview
            else
            None
        )

    # FIXME: inner_focus comes from MultiSourceListingMixin
    async def sync_playlist_position(self):

        await state.task_manager._preview_player
        if len(self):

            try:
                position = self.playlist_position
            except AttributeError:
                return
            # if position is None:
            #     return

            while len(self.pending_event_tasks):
                t = self.pending_event_tasks.pop()
                t.cancel()

            self.pending_event_tasks.append(
                asyncio.create_task(
                    self.preview_content()
                )
            )

    def on_focus(self, source, position):
        super().on_focus(source, position)

        self.preview_stage = -1
        # self.preview_stage = (
        #     0
        #     if self.preview_stage_default == "default"
        #     else -1
        # )
        # import ipdb; ipdb.set_trace()
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
                    async def reload(listing_id):
                        self.invalidate_rows([listing_id])
                        self.selection.close_details()
                        self.selection.open_details()
                        # necessary to reload sources that may hae changed
                        # self.refresh()
                        await self.preview_all(playlist_position=self.playlist_position)
                    state.event_loop.create_task(reload(listing.media_listing_id))
        # state.loop.draw_screen()

    def on_deactivate(self):
        while len(self.pending_event_tasks):
            t = self.pending_event_tasks.pop()
            t.cancel()
        super().on_deactivate()

    def on_player_load_failed(self, url):

        logger.debug(f"on_player_load_failed: {url}")

        if state.task_manager.preview_task.provider != self.provider.CONFIG_IDENTIFIER:
            return

        class RefreshingMessage(BasePopUp):
            def __init__(self):
                self.text = urwid.Text("Media URL(s) expired, refreshing listing...", align="center")
                self.filler = urwid.Filler(self.text)
                super().__init__(self.filler)

            def selectable(self):
                return False

        message = RefreshingMessage()
        self.provider.view.open_popup(message, width=40, height=5)

        async def async_handler():

            pos = await state.task_manager.preview_player.command(
                "get_property", "playlist-pos"
            )

            await state.task_manager.preview_player.command(
                "playlist-play-index", "none"
            )

            failed_index = next(
                (
                    i for i, item in enumerate(self.play_items)
                    if url in [item.locator, item.locator_preview]
                ),
                pos
            )

            # import ipdb; ipdb.set_trace()
            play_item = self.play_items[failed_index]
            failed_listing_id = play_item.media_listing_id
            source_rank = play_item.index

            with db_session(optimistic=False):
                # have to force a reload here since sources may have changed
                listing = self.provider.LISTING_CLASS[failed_listing_id]#
                await self.playlist_replace(listing.cover, idx=failed_index)
                if hasattr(self, "inflate_listing"):
                    await self.inflate_listing(self.playlist_pos_to_row(failed_index))
                    listing = listing.attach().detach()
                    source = next(
                        s for s in listing.sources
                        if s.rank == source_rank
                    )
                    # print(self.play_items[failed_index].locator)
                    # import ipdb; ipdb.set_trace()
                    # FIXME
                    # print(listing.media_listing_id)
                    # import ipdb; ipdb.set_trace()
                    self.df.update_rows([listing])
                    # print(self.play_items[failed_index].locator)
                    self.load_play_items()
                    await self.playlist_replace(source.locator, idx=failed_index)

            self.provider.view.close_popup()

        asyncio.create_task(async_handler())


    async def playlist_replace(
            self, source,
            idx=None, pos=None,
            video_track=None, audio_track=None
    ):

        locator = getattr(source, "locator", source)

        async with self.playlist_lock:
            if idx is None:
                idx = self.playlist_position

            logger.debug(f"playlist_replace: {idx}, {locator}")
            # FIXME: standardize media source preview locator
            # if hasattr(self.play_items[idx], "locator_preview"):
            self.play_items[idx].locator_preview = locator

            count = await state.task_manager.preview_player.command(
                "get_property", "playlist-count"
            )

            loadfile_args = ["loadfile", locator, "append"]
            loadfile_opts = []
            if video_track is not None:
                loadfile_opts.append(f"video={video_track}")
            if audio_track is not None:
                loadfile_opts.append(f"audio={audio_track}")
            if loadfile_opts:
                loadfile_args.append(",".join(loadfile_opts))
            await state.task_manager.preview_player.command(
                *loadfile_args
            )

            await state.task_manager.preview_player.command(
                "playlist-play-index", "none"
            )

            await state.task_manager.preview_player.command(
                "playlist-move", str(count), str(idx)
            )

            await state.task_manager.preview_player.command(
                "playlist-remove", str(idx+1)
            )

            await state.task_manager.preview_player.command(
                "playlist-play-index", str(idx)
            )



    def quit_player(self):
        try:
            state.event_loop.create_task(self.player.quit())
        except BrokenPipeError:
            pass


@keymapped()
class SynchronizedPlayerProviderMixin(SynchronizedPlayerMixin):

    def new_listing(self, **kwargs):
        return self.provider.new_listing(**kwargs)

    def new_media_source(self, **kwargs):
        return self.provider.new_media_source(**kwargs)

    @property
    def play_items(self):
        if not getattr(self, "_play_items", False):
            self.load_play_items()
        return self._play_items

    def load_play_items(self):
        # FIXME: this is gross...
        self._play_items = [
            AttrDict(
                media_listing_id=row.data.media_listing_id,
                title=utils.sanitize_filename(row.data_source.title),
                created=getattr(row.data, "created", None),
                # feed=row.data.channel.name,
                # locator=row.data.channel.locator,
                index=index,
                row_num=row_num,
                count=len(row.data_source.sources) if hasattr(row.data_source, "sources") else 1,
                media_type=source.media_type,
                preview_mode=state.listings_view.preview_mode,
                # locator=(
                #     (source.locator or getattr(source, "locator_thumbnail", None))
                #     if self.provider.auto_preview_default == "full"
                #     else (getattr(source, "locator_thumbnail", None) or source.locator)
                # )
                # locator=source.locator or getattr(source, "locator_thumbnail", None),
                # locator_preview=source.locator_for_preview(state.listings_view.preview_mode),
                locator_preview=source.locator_preview,
                locator=source.locator
            )
            for (row_num, row, index, source) in [
                    (row_num, row, index, source) for row_num, row in enumerate(self)
                    for index, source in enumerate(
                            row.data_source.sources or []
                            if hasattr(row.data_source, "sources")
                            else [
                                model.MediaSource.attr_class(
                                    locator=row.data_source.cover,
                                    media_type="image"
                                )
                            ]
                    )
                    # if not source.is_bad
            ]
        ]
        # import ipdb; ipdb.set_trace()

    @property
    def playlist_position_text(self):
        return f"[{self.focus_position+1}/{len(self)}]"

    def on_requery(self, source, count):
        if state.get("tui_enabled"):
            self.load_play_items()
        super().on_requery(source, count)


    def create_preview_task(self, listing, **kwargs):
        return model.PlayMediaTask.attr_class(
            provider=self.provider.CONFIG_IDENTIFIER,
            title=listing.title,
            sources=listing.sources
        )


class DetailBox(urwid.WidgetWrap):

    def __init__(self, listing, parent_table):
        self.listing = listing
        self.parent_table = parent_table
        self.table = self.detail_table()
        self.box = urwid.BoxAdapter(self.table, 1)
        super().__init__(self.box)

    def detail_table(self):
        columns = self.parent_table.columns.copy()
        next(c for c in columns if c.name=="title").truncate = True
        return DetailDataTable(
            self.parent_table.provider,
            self.listing,
            self.parent_table, columns=columns
        )

    @property
    def focus_position(self):
        return self.table.focus_position

    def keypress(self, size, key):
        return super().keypress(size, key)


@keymapped()
class DetailDataTable(
        widgets.DecoratedTableMixin,
        widgets.PlayListingViewMixin,
        widgets.DownloadListingViewMixin,
        widgets.BaseDataTable):

    KEYMAP = {
        "meta up": "prev_item",
        "meta down": "next_item"
    }

    with_header = False
    with_scrollbar = False

    def __init__(self, provider, listing, parent_table, columns=None):
        self.provider = provider
        self.listing = listing
        self.parent_table = parent_table
        super().__init__(provider, columns=columns)

    @property
    def selected_listing(self):
        return self.parent_table.selected_listing

    @property
    def selected_source(self):
        return self.parent_table.selected_source


    def keypress(self, size, key):
        if key in ["home", "K"]:
            if self.focus_position != 0:
                self.focus_position = 0
            else:
                self.parent_table.focus_position = 0
        elif key in ["end", "J"]:
            if self.focus_position != len(self)-1:
                self.focus_position = len(self)-1
            else:
                self.parent_table.focus_position = len(self.parent_table)-1
        else:
            return key

    def query(self, *args, **kwargs):
        return [
            merge(
                AttrDict(),
                (
                    source.listing if isinstance(source.listing, dict)
                    else source.listing.dict() if hasattr(source.listing, "dict")
                    else source.listing.to_dict() if hasattr(source.listing, "to_dict")
                    else {}
                ),
                (
                    source if isinstance(source, dict)
                    else source.dict() if hasattr(source, "dict")
                    else source.to_dict() if hasattr(source, "to_dict")
                    else {}
                ),
                dict(
                    token_aliases=source.listing.token_aliases,
                    group=source.listing.group,
                    content_date=source.listing.content_date
                )
            )
            for i, source in enumerate(self.listing.sources)
        ]

    @keymap_command
    async def prev_item(self):
        await self.parent_table.prev_item()

    @keymap_command
    async def next_item(self):
        await self.parent_table.next_item()


@keymapped()
class MultiSourceListingMixin(object):

    KEYMAP = {

    }
    # with_sidecar = True

    DETAIL_BOX_CLASS = DetailBox

    def listings(self, offset=None, limit=None, *args, **kwargs):
        for listing in super().listings(offset=offset, limit=limit, *args, **kwargs):
            yield listing
            # yield (listing, dict(source_count=len(listing.sources)))

    @property
    def active_table(self):
        return self.inner_table or self

    @property
    def selected_source(self):
        return super().get_source(index=self.inner_focus)

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

        box = self.DETAIL_BOX_CLASS(data, self)
        urwid.connect_signal(box.table, "focus", lambda s, i: self.on_focus(s, self.focus_position))
        return box

        # return self.detail_box

    async def sync_playlist_position(self):
        return await super().sync_playlist_position()

    @property
    def playlist_position(self):
        return self.row_to_playlist_pos(self.focus_position) + self.inner_focus

    @property
    def playlist_position_text(self):
        if self.inner_table:
            inner_text = f"{self.inner_focus+1}/{len(self.inner_table)}"
        else:
            inner_text=""
        return f"{self.focus_position+1}/{len(self)} {inner_text}"

    @property
    def inner_table(self):
        if self.selection and self.selection.details_open and self.selection.details:
            return self.selection.details.contents.table
        return None

    @property
    def inner_focus(self):
        if self.inner_table:
            return self.inner_table.focus_position
        return 0

    # def row_attr_fn(self, position, data, row):
    #     return (
    #         "downloaded"
    #         if all([s.local_path for s in data.sources])
    #         else super().row_attr_fn(position, data, row)
    #     )
