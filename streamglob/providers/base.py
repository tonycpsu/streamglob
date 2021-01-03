import logging
logger = logging.getLogger(__name__)
import sys
import os
import abc
import asyncio
import dataclasses
import re
from itertools import chain

from orderedattrdict import AttrDict, defaultdict
from pony.orm import *

from .widgets import *
from panwid.dialog import BaseView
from .filters import *
from ..session import *
from ..state import *
from ..player import Player, Downloader
from .. import model
from .. import config
from  ..utils import *

class BaseProviderView(StreamglobView):

    def update(self):
        pass

    def refresh(self):
        pass

    def init_config(self, config):
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
        self.table = self.PROVIDER_DATA_TABLE_CLASS(self.provider, self)
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
            # state.event_loop.create_task(self.provider.refresh())
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

    def init_config(self, config):
        for name, attrs in config.columns.items():
            for attr, value in attrs.items():
                col = next(c for c in self.table._columns if c.name == name)
                setattr(col, attr, value)
                # config.view.columns[name][attr] = value

def with_view(view):
    def inner(cls):
        def make_view(self):
            if not self.config_is_valid:
                return InvalidConfigView(self.NAME, self.REQUIRED_CONFIG)
            return view(self)
        return type(cls.__name__, (cls,), {'make_view': make_view})
    return inner


MEDIA_SPEC_RE=re.compile(r"(?:/([^:]+))?(?::(.*))?")

@with_view(SimpleProviderView)
class BaseProvider(abc.ABC):
    """
    Abstract base class from which providers should inherit from
    """

    SESSION_CLASS = StreamSession
    LISTING_CLASS = model.TitledMediaListing
    # VIEW_CLASS = SimpleProviderView
    FILTERS = AttrDict()
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

    def init_config(self):
        with db_session:
            try:
                self.provider_data = model.ProviderData.get(name=self.IDENTIFIER).settings
            except AttributeError:
                self.provider_data = model.ProviderData(name=self.IDENTIFIER).settings
        try:
            self.view.init_config(self.config.view)
        except:
            logger.warn(f"couldn't initialize configuration for {self.IDENTIFIER}")


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
                if value is None:
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
        sources = selection.sources
        if not isinstance(sources, list):
            sources = [sources]

        return sources
        # return [ source for source in sources ]

    def play_args(self, selection, **kwargs):
        source = self.get_source(selection, **kwargs)
        return (source, kwargs)

    def filter_args(self):
        return {f: self.filters[f].value for f in self.filters}

    def play(self, selection, no_task_manager=False, **kwargs):

        try:
            sources, kwargs = self.play_args(selection, **kwargs)
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

        media_types = set([s.media_type for s in sources if s.media_type])
        player_spec = {"media_types": media_types}
        if media_types == {"image"}:
            downloader_spec = {None: None}
        else:
            downloader_spec = getattr(self.config, "helpers", None) or sources[0].helper

        task = model.PlayMediaTask.attr_class(
            provider=self.NAME,
            title=selection.title,
            sources = sources
        )

        return state.task_manager.play(task, player_spec, downloader_spec, **kwargs)


    def download(self, selection, index=None, no_task_manager=False, **kwargs):

        listing = selection
        with db_session:
            if not listing.is_inflated:
                listing = selection.attach()
                listing.inflate()
                listing = listing.detach()

        sources, kwargs = self.play_args(listing, **kwargs)

        if not isinstance(sources, list):
            sources = [sources]

        if "num" not in kwargs:
            kwargs["num"] = len(sources)
        for i, source in enumerate(sources):

            if index is not None and index != i:
                continue
            try:
                filename = source.download_filename(index=index, **kwargs)
            except SGInvalidFilenameTemplate as e:
                logger.warn(f"filename template for provider {self.IDENTIFIER} is invalid: {e}")
                raise
            downloader_spec = getattr(self.config, "helpers") or source.download_helper
            task = model.DownloadMediaTask.attr_class(
                provider = self.NAME,
                title = selection.title,
                sources = sources,
                listing = selection,
                dest = filename,
                postprocessors = (self.config.get("postprocessors", None) or []).copy()
            )
            state.task_manager.download(task, downloader_spec, **kwargs)

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
                    state.event_loop.create_task(fn(*args, **kwargs))
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
                logger.info("deactivate cancel task")
                task.cancel()
                self._tasks[name] = None
        # if self._refresh_alarm:
        #     state.loop.remove_alarm(self._tasks[fn.__name__])
        # self._tasks[fn.__name__] = None
