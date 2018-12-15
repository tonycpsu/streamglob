import abc

from orderedattrdict import AttrDict

from ..session import *
from .widgets import *

class MediaItem(AttrDict):

    def __repr__(self):
        s = ",".join(f"{k}={v}" for k, v in self.items() if k != "title")
        return f"<{self.__class__.__name__}: {self.title}{ ' (' + s if s else ''})>"


class MediaAttributes(AttrDict):

    def __repr__(self):
        state = "!" if self.state == "MEDIA_ON" else "."
        free = "_" if self.free else "$"
        return f"{state}{free}"


class BaseProvider(abc.ABC):

    SESSION_CLASS = StreamSession
    # FILTERS = AttrDict()
    ATTRIBUTES = ["title"]

    def __init__(self, *args, **kwargs):
        self.session = self.SESSION_CLASS(*args, **kwargs)
        self.filters = AttrDict({n: f() for n, f in self.FILTERS.items() })

    # @property
    # @abc.abstractmethod
    # def filters(self):
    #     pass

    @abc.abstractmethod
    def login(self):
        pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @abc.abstractmethod
    def make_view(self):
        pass

    @abc.abstractmethod
    def update(self):
        pass


class SimpleProviderViewMixin(object):

    def make_view(self):

        self.toolbar = FilterToolbar(self.filters)
        self.table = ProviderDataTable(
            self.listings,
            [ panwid.DataTableColumn(k, **v if v else {}) for k, v in self.ATTRIBUTES.items() ]
        )
        urwid.connect_signal(self.toolbar, "filter_change", self.on_filter_change)

        self.pile  = urwid.Pile([
            (3, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 0
        return self.pile

    def on_filter_change(self, source, widget, value):
        self.update()

    def update(self):

        self.table.reset()
        # self.table.requery()
