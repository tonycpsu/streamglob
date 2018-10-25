import abc

from ..session import *

from .widgets import *

class MediaItem(AttrDict):

    def __repr__(self):
        s = ",".join(f"{k}={v}" for k, v in self.items() if k != "title")
        return f"<{self.__class__.__name__}: {self.title}{ ' (' + s if s else ''})>"


class BaseProvider(abc.ABC):

    SESSION_CLASS = StreamSession
    FILTERS = []
    ATTRIBUTES = ["title"]

    def __init__(self, *args, **kwargs):
        self.session = self.SESSION_CLASS(*args, **kwargs)
        # self.filters = [ f() for f in self.FILTERS ]

    @abc.abstractmethod
    def login(self):
        pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @abc.abstractmethod
    def make_view(self):
        pass

class SimpleProviderViewMixin(object):

    def make_view(self):

        self.toolbar = FilterToolbar(self.FILTERS)
        self.table = ProviderDataTable(
            self.listings,
            [ panwid.DataTableColumn(a) for a in self.ATTRIBUTES ]
        )

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        return self.pile
