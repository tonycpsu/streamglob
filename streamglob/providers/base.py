import abc

from orderedattrdict import AttrDict
from itertools import chain

from .widgets import *
from panwid.dialog import BaseView
from .filters import *
from ..session import *
from ..state import *
from ..player import Player
from .. import model

class MediaItem(AttrDict):

    def __repr__(self):
        s = ",".join(f"{k}={v}" for k, v in self.items() if k != "title")
        return f"<{self.__class__.__name__}: {self.title}{ ' (' + s if s else ''})>"


# FIXME: move
def get_output_filename(game, station, resolution, offset=None):

    try:
        start_time = dateutil.parser.parse(
            game["gameDate"]
        ).astimezone(pytz.timezone("US/Eastern"))

        game_date = start_time.date().strftime("%Y%m%d")
        game_time = start_time.time().strftime("%H%M")
        if offset:
            game_time = "%s_%s" %(game_time, offset)
        return "mlb.%s.%s@%s.%s.%s.ts" \
               % (game_date,
                  game["teams"]["away"]["team"]["fileCode"],
                  game["teams"]["home"]["team"]["fileCode"],
                  game_time,
                  station.lower()
                  )
    except KeyError:
        return "mlb.%d.%s.ts" % (game["gamePk"], resolution)



class SimpleProviderView(BaseView):

    PROVIDER_DATA_TABLE_CLASS = ProviderDataTable

    def __init__(self, provider):
        self.provider = provider
        self.toolbar = FilterToolbar(self.provider.filters)
        self.table = self.PROVIDER_DATA_TABLE_CLASS(self.provider)
        urwid.connect_signal(self.toolbar, "filter_change", self.on_filter_change)
        urwid.connect_signal(self.table, "select", self.provider.on_select)
        urwid.connect_signal(self.table, "cycle_filter", self.cycle_filter)

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        super().__init__(self.pile)

    def on_filter_change(self, index, *args):
        self.update()

    def cycle_filter(self, widget, n, step):
        self.toolbar.cycle_filter(n, step)

    def update(self):
        self.table.reset()

class ClassPropertyDescriptor(object):

    def __init__(self, fget, fset=None):
        self.fget = fget
        self.fset = fset

    def __get__(self, obj, klass=None):
        if klass is None:
            klass = type(obj)
        return self.fget.__get__(obj, klass)()

    def __set__(self, obj, value):
        if not self.fset:
            raise AttributeError("can't set attribute")
        type_ = type(obj)
        return self.fset.__get__(obj, type_)(value)

    def setter(self, func):
        if not isinstance(func, (classmethod, staticmethod)):
            func = classmethod(func)
        self.fset = func
        return self

def classproperty(func):
    if not isinstance(func, (classmethod, staticmethod)):
        func = classmethod(func)

    return ClassPropertyDescriptor(func)

class ClassPropertyMetaClass(type):
    def __setattr__(self, key, value):
        if key in self.__dict__:
            obj = self.__dict__.get(key)
        if obj and type(obj) is ClassPropertyDescriptor:
            return obj.__set__(self, value)

        return super(ClassPropertyMetaClass, self).__setattr__(key, value)

def with_view(view):
    def inner(cls):
        def make_view(self):
            return view(self)
        return type(cls.__name__, (cls,), {'make_view': make_view})
    return inner

@with_view(SimpleProviderView)
class BaseProvider(abc.ABC):
    """
    Abstract base class from which providers should inherit from
    """

    SESSION_CLASS = StreamSession
    ITEM_CLASS = model.Item
    # VIEW_CLASS = SimpleProviderView
    FILTERS = AttrDict()
    ATTRIBUTES = AttrDict(title={"width": ("weight", 1)})
    MEDIA_TYPES = None
    HELPER = None

    def __init__(self, *args, **kwargs):
        self._session = self.SESSION_CLASS.new(*args, **kwargs)
        self._view = None
        self._filters = AttrDict([
            (k, DummyFilter(self, v))
            for k, v in list(state.options.items()) + [("resolution", "best")]
        ])
        # self._filters.resolution = DummyFilter("best")
        # self.player = Player.get(
        #     self.MEDIA_TYPES
        # )

    @property
    def gui(self):
        return self._view is not None

    @property
    def filters(self):
        return self._filters

    @property
    def view(self):
        if not self._view:
            self._filters = AttrDict({n: f(provider=self)
                                     for n, f in self.FILTERS.items() })
            self._view = self.make_view()

        return self._view

    @abc.abstractmethod
    def make_view(self):
        pass

    @classproperty
    @abc.abstractmethod
    def IDENTIFIER(cls):
        return next(
            c.__module__ for c in cls.__mro__
            if __package__ in c.__module__).split(".")[-1]
        # return cls.NAME.lower()

    @classproperty
    @abc.abstractmethod
    def NAME(cls):
        return cls.__name__.replace("Provider", "")

    def parse_specifier(self, spec):
        raise NotImplementedError

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @property
    def config(self):
        # raise Exception(self.__module__ + "." + self.__class__.__qualname__)
        # raise Exception(self.__class__.__init_subclass__)
        # module = self.__class__.__module__.lower()
        # raise Exception(module)
        return config.settings.profile.providers.get(self.IDENTIFIER)

    # @classmethod
    # def should_load(cls):
    #     module = cls.__module__.lower()
    #     cfg = config.settings.profile.providers.get(module)
    #     raise Exception(cls)
    #     return cls.config_is_valid(cfg)

    @property
    def config_is_valid(self):
        return all([ self.config.get(x, None) is not None
                     for x in getattr(self, "REQUIRED_CONFIG", [])
        ])

    @property
    def session(self):
        return self._session

    def play_args(self, selection):
        url = selection.url
        if not isinstance(url, list):
            url = [url]
        return ( url, {} )

    def play(self, selection, **kwargs):

        (source, kwargs) = self.play_args(selection, **kwargs)
        media_type = kwargs.pop("media_type", None)
        if media_type:
            player = Player.get(set([media_type]))
        else:
            player = Player.get(self.MEDIA_TYPES)

        if self.HELPER:
            helper = Player.get(self.HELPER)#, *args, **kwargs)
            helper.source = source
            player.source = helper
        else:
            player.source = source

        state.spawn_play_process(player, **kwargs)
        # player.play(**kwargs)

    def on_select(self, widget, selection):
        self.play(selection)

    # @abc.abstractmethod
    # def login(self):
    #     pass

    @property
    def limit(self):
        return None


class PaginatedProviderMixin(object):

    @property
    def limit(self):
        return (self.config.get("limit") or
                config.settings.profile.tables.get("limit"))

class FeedsFilter(ListingFilter):

    @property
    def values(self):
        cfg = self.provider.config.feeds
        if isinstance(cfg, dict):
            return cfg
        elif isinstance(cfg, list):
            return [ (i, i) for i in cfg ]


class FeedProvider(BaseProvider):
    """
    A provider that offers multiple feeds to select from
    """

    FILTERS = AttrDict([
        ("feed", FeedsFilter)
    ])

    REQUIRED_CONFIG = ["feeds"]

    @property
    def selected_feed(self):
        return self.filters.feed.value

class CachedFeedProvider(FeedProvider):

    UPDATE_INTERVAL = 300
    MAX_ITEMS = 100

    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     self.last_update = None


    # @abc.abstractmethod
    # def update_feed(self):
    #     pass

    def listings(self, offset=None, limit=None, *args, **kwargs):

        count = 0

        with db_session:
            f = self.FEED_CLASS.get(
                provider_name = self.IDENTIFIER,
                name = self.selected_feed
            )

            if not f:
                f = self.FEED_CLASS(
                    provider_name = self.IDENTIFIER,
                    name = self.selected_feed
                )

            if (f.updated is None
                or
                datetime.now() - f.updated
                > timedelta(seconds=f.update_interval)
            ):
                # self.update_feed(self.selected_feed, self.MAX_ITEMS)
                # f.updated = datetime.now()
                f.update()


            for r in select(
                i for i in self.ITEM_CLASS
                if i.feed == f
            )[offset:offset+limit]:
                # commit()
                yield AttrDict(
                    id = r.guid,
                    time = r.created,
                    title = r.subject,
                    type = r.media_type,
                    url = r.content
                )
