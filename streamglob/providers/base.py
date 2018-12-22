import abc

from orderedattrdict import AttrDict
from itertools import chain

from .widgets import *
from panwid.dialog import BaseView
from ..session import *
from ..state import *
from ..player import *

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
        self.table = self.PROVIDER_DATA_TABLE_CLASS(
            self.provider.listings,
            [ panwid.DataTableColumn(k, **v if v else {})
              for k, v in self.provider.ATTRIBUTES.items() ]
        )
        urwid.connect_signal(self.toolbar, "filter_change", self.on_filter_change)
        urwid.connect_signal(self.table, "select", self.provider.on_select)
        urwid.connect_signal(self.table, "cycle_filter", self.cycle_filter)

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        super().__init__(self.pile)

    def on_filter_change(self, source, widget, value):
        self.update()

    def cycle_filter(self, widget, n, step):
        self.toolbar.cycle_filter(n, step)

    def update(self):

        self.table.reset()


class BaseProvider(abc.ABC):

    SESSION_CLASS = StreamSession
    VIEW_CLASS = SimpleProviderView
    PLAYER_CLASS = BasicPlayer
    FILTERS = AttrDict()
    ATTRIBUTES = AttrDict(title={"width": ("weight", 1)})

    def __init__(self, *args, **kwargs):
        # self.session = self.SESSION_CLASS(*args, **kwargs)
        self._session = self.SESSION_CLASS.new(*args, **kwargs)
        self.filters = AttrDict({n: f(provider=self) for n, f in self.FILTERS.items() })
        self.view = self.VIEW_CLASS(self)
        self.player = self.PLAYER_CLASS()

    @property
    def session(self):
        return self._session

    def play_args(self, selection):
        return ( [url], {} )

    def play(self, selection, **kwargs):
        (args, kwargs) = self.play_args(selection, **kwargs)
        self.player.play(*args, **kwargs)

    def on_select(self, widget, selection):
        self.provider.play(selection)

    # @abc.abstractmethod
    # def login(self):
    #     pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    # @abc.abstractmethod
    # def update(self):
    #     pass

    # @abc.abstractmethod
    # def make_view(self):
    #     pass


# class SimpleProviderViewMixin(object):

#     PROVIDER_DATA_TABLE_CLASS = ProviderDataTable

#     def make_view(self):

#         self.toolbar = FilterToolbar(self.filters)
#         raise Exception(self.PROVIDER_DATA_TABLE_CLASS)
#         self.table = self.PROVIDER_DATA_TABLE_CLASS(
#             self.listings,
#             [ panwid.DataTableColumn(k, **v if v else {}) for k, v in self.ATTRIBUTES.items() ]
#         )
#         urwid.connect_signal(self.toolbar, "filter_change", self.on_filter_change)
#         urwid.connect_signal(self.table, "select", self.on_select)
#         urwid.connect_signal(self.table, "cycle_filter", self.cycle_filter)

#         self.pile  = urwid.Pile([
#             (1, self.toolbar),
#             ("weight", 1, self.table)
#         ])
#         self.pile.focus_position = 1
#         return self.pile

#     def on_filter_change(self, source, widget, value):
#         self.update()

#     def on_select(self, widget, selection):
#         self.play(selection)

#     def cycle_filter(self, widget, n, step):
#         self.toolbar.cycle_filter(n, step)

#     def update(self):

#         self.table.reset()
