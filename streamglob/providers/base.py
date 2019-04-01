import logging
logger = logging.getLogger(__name__)
import sys
import os
import abc
import asyncio
import dataclasses

from orderedattrdict import AttrDict, defaultdict
from itertools import chain
import re

from .widgets import *
from panwid.dialog import BaseView
from .filters import *
from ..session import *
from ..state import *
from ..player import Player, Helper, Downloader
from .. import model
from .. import config
from  ..utils import *

class BaseProviderView(BaseView):

    def update(self):
        pass


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


class SimpleProviderView(BaseProviderView):

    PROVIDER_DATA_TABLE_CLASS = ProviderDataTable

    def __init__(self, provider):
        self.provider = provider
        self.toolbar = FilterToolbar(self.provider.filters)
        self.table = self.PROVIDER_DATA_TABLE_CLASS(self.provider)
        # urwid.connect_signal(self.toolbar, "filter_change", self.filter_change)
        urwid.connect_signal(self.table, "select", self.provider.on_select)
        urwid.connect_signal(self.table, "cycle_filter", self.cycle_filter)

        self.pile  = urwid.Pile([
            ("pack", self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        super().__init__(self.pile)

    # def filter_change(self, f, name, *args):
    #     logger.debug(f"filter_change: {name}, {args}")
    #     func = getattr(self.provider, f"on_{name}_change", None)
    #     if func:
    #         func(*args)
    #     # self.table.refresh()
    #     # self.table.reset()

    def cycle_filter(self, n, step):
        self.toolbar.cycle_filter(n, step)

    def refresh(self):
        self.table.refresh()

    def reset(self):
        logger.info("reset")
        self.table.reset()

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "ctrl r":
            self.reset()
            # state.asyncio_loop.create_task(self.provider.refresh())
        # elif key == "d":
        #     self.download(self.table.selection.data)
        elif key in ["[", "]", "meta left", "meta right"]:
            self.cycle_filter(0, -1 if key in ["[", "meta left"] else 1)
        elif key in ["{", "}", "shift left", "shift right"]:
            self.cycle_filter(1, -1 if key in ["{", "shift left"] else 1)
        elif key in ["-", "=", "ctrl left", "ctrl right"]:
            self.cycle_filter(2, -1 if key in ["-", "ctrl left"] else 1)
        elif key in ["_", "+", "shift meta left", "shift meta right"]:
            self.cycle_filter(3, -1 if key in ["_", "shift meta left"] else 1)
        else:
            return key

    def selectable(self):
        return True
    # def update(self):
    #     self.refresh()


def with_view(view):
    def inner(cls):
        def make_view(self):
            if not self.config_is_valid:
                return InvalidConfigView(self.NAME, self.REQUIRED_CONFIG)
            return view(self)
        return type(cls.__name__, (cls,), {'make_view': make_view})
    return inner

@with_view(SimpleProviderView)
class BaseProvider(abc.ABC):
    """
    Abstract base class from which providers should inherit from
    """

    SESSION_CLASS = StreamSession
    ITEM_CLASS = model.MediaItem
    # VIEW_CLASS = SimpleProviderView
    FILTERS = AttrDict()
    ATTRIBUTES = AttrDict(title={"width": ("weight", 1)})
    MEDIA_TYPES = None

    def __init__(self, *args, **kwargs):
        self._view = None
        self._session = None
        self._active = False
        self._filters = AttrDict({n: f(provider=self, name=n)
                                  for n, f in self.FILTERS.items() })

        rules = AttrDict(
            self.config.rules.label,
            **config.settings.profile.rules.label
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

    def init_config(self):
        pass

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
        return model.MediaListing

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
        pass

    @abc.abstractmethod
    def make_view(self):
        pass

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
        return AttrDict()

    @property
    def FILTERS(self):
        d = getattr(self, "FILTERS_BROWSE", AttrDict())
        d.update(getattr(self, "FILTERS_OPTIONS", {}))
        return d

    def parse_identifier(self, identifier):
        return

    def new_media_source(self, *args, **kwargs):
        return self.MEDIA_SOURCE_CLASS(
            self.IDENTIFIER,
            *args,
            **kwargs
        )

    def new_listing(self, **kwargs):
        return self.LISTING_CLASS(
            self.IDENTIFIER,
            **kwargs
        )

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

    def parse_options(self, options):
        if not options:
            return AttrDict()
        return AttrDict([
            (list(self.FILTERS_OPTIONS.keys())[n], v)
            for n, v in enumerate(
                    [o for o in options.split(",") if "=" not in o]
            )], **dict(o.split("=") for o in options.split(",") if "=" in o)
    )

    def get_source(self, selection, **kwargs):
        source = selection.content
        if not isinstance(source, list):
            source = [source]
        return source

    def play_args(self, selection, **kwargs):
        source = self.get_source(selection, **kwargs)
        kwargs = {k: v
                  for k, v in list(kwargs.items())
                  + [ (f, self.filters[f].value)
                      for f in self.filters
                      if f not in kwargs]}
        return (source, kwargs)

    def play(self, selection, no_task_manager=False, **kwargs):

        try:
            sources, kwargs = self.play_args(selection, **kwargs)
        except SGStreamNotFound as e:
            logger.error(f"stream not found: {e}")
            return
        # media_type = kwargs.pop("media_type", None)

        # FIXME: For now, we just throw playlists of media items at the default
        # player program and hope it can handle all of them.

        player_spec = None
        helper_spec = None

        if not isinstance(sources, list):
            sources = [sources]

        for s in sources:
            if not s.media_type:
                # Try to set the content types of the source(s) with a HTTP HEAD
                # request if the provider didn't specify one.
                s.media_type = self.session.head(
                    s.locator
                ).headers.get("Content-Type").split("/")[0]

        media_types = set([s.media_type for s in sources if s.media_type])
        player_spec = {"media_types": media_types}
        if media_types == {"image"}:
            helper_spec = {None: None}
        else:
            helper_spec = getattr(self.config, "helpers", None) or sources[0].helper

        task = model.PlayMediaTask(
            provider=self.NAME,
            title=selection.title,
            sources = sources
        )

        if not (no_task_manager or state.options.debug_console):
            return state.task_manager.play(task, player_spec, helper_spec, **kwargs)
        else:
            return Player.play(task, player_spec, helper_spec, **kwargs)


    def download(self, selection, no_task_manager=False, **kwargs):

        sources, kwargs = self.play_args(selection, **kwargs)
        # filename = selection.download_filename

        if not isinstance(sources, list):
            sources = [sources]

        for i, s in enumerate(sources):
            # filename = s.download_filename
            # kwargs = {"ext": getattr(s, "ext", None)}
            if len(sources):
                kwargs["index"] = i
            try:
                filename = selection.download_filename(**kwargs)
            except SGInvalidFilenameTemplate as e:
                logger.warn(f"filename template for provider {self.IDENTIFIER} is invalid: {e}")
            helper_spec = getattr(self.config, "helpers") or s.helper
            # logger.info(f"helper: {helper_spec}")

            task = model.DownloadMediaTask(
                provider=self.NAME,
                title=selection.title,
                sources = [s],
                dest=filename
            )

            # s = AttrDict(dataclasses.asdict(s))
            # s.provider = self.NAME
            # s.title = selection.title
            # s.dest = filename

            if not (no_task_manager or state.options.debug_console):
                return state.task_manager.download(task, filename, helper_spec, **kwargs)
            else:
                return Downloader.download(task, filename, helper_spec, **kwargs)

    def on_select(self, widget, selection):
        self.play(selection)

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

class PaginatedProviderMixin(object):

    @property
    def limit(self):
        if getattr(self, "_limit", None) is not None:
            return self._limit
        return (self.config.get("limit") or
                config.settings.profile.tables.get("limit"))

    @limit.setter
    def limit(self, value):
        self._limit = value


class BackgroundTasksMixin(object):

    DEFAULT_INTERVAL = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tasks = defaultdict(lambda: None)

    def run_in_background(self, fn, interval=DEFAULT_INTERVAL,
                          *args, **kwargs):

        logger.info(f"run_in_background {fn.__name__} {interval}")
        async def run():
            while True:
                logger.info(f"running {fn.__name__} {args} {kwargs}")
                # self._tasks[fn.__name__] = None
                # fn(*args, **kwargs)
                # await state.asyncio_loop.run_in_executor(
                #     None, lambda: fn(*args, **kwargs)
                # )

                # logger.info(fn)
                # await fn(*args, **kwargs)
                state.asyncio_loop.create_task(fn(*args, **kwargs))
                # state.asyncio_loop.run_in_executor(None, lambda: fn(*args, **kwargs))

                # state.loop.event_loop.enter_idle(lambda: fn(*args, **kwargs))
                logger.info(f"sleeping for {interval}")
                await asyncio.sleep(interval)

        self._tasks[fn.__name__] = state.asyncio_loop.create_task(run())

    def on_activate(self):
        # self.update()
        for task in self.TASKS:
            args = []
            kwargs = {}
            interval = self.DEFAULT_INTERVAL
            if isinstance(task, tuple):
                if len(task) == 4:
                    (task, interval, args, kwargs) = task
                elif len(task) == 3:
                    (task, interval, args) = task
                elif len(task) == 2:
                    (task, interval) = task
            fn = getattr(self, task)
            self.run_in_background(fn, interval, *args, **kwargs)


    def on_deactivate(self):
        for name, task in self._tasks.items():
            if task:
                task.cancel()
                self._tasks[name] = None
        # if self._refresh_alarm:
        #     state.loop.remove_alarm(self._tasks[fn.__name__])
        # self._tasks[fn.__name__] = None
