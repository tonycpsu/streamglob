import logging
logger = logging.getLogger(__name__)

import functools
import re

import urwid
import panwid
from pony.orm import *

from ..exceptions import *
from ..widgets import *
from ..state import *
from .. import model

class FilterToolbar(urwid.WidgetWrap):

    signals = ["filter_change"]

    def __init__(self, filters):

        self.filters = filters
        self.columns = urwid.Columns([], dividechars=1)
        for n, f in self.filters.items():
            self.columns.contents += [
                (f.placeholder, self.columns.options("weight", 1)),
            ]

        self.filler = urwid.Filler(urwid.Padding(self.columns))
        super(FilterToolbar, self).__init__(urwid.BoxAdapter(self.filler, 1))

    def cycle_filter(self, index, step=1):
        if index >= len(self.filters):
            return
        list(self.filters.values())[index].cycle(step)

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)

    def get_pref_col(self, size):
        return 0


class ProviderDataTable(BaseDataTable):

    ui_sort = False

    signals = ["cycle_filter"]

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        self.columns = [ panwid.DataTableColumn(k, **v if v else {})
                         for k, v in self.provider.ATTRIBUTES.items() ]

        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    @property
    def limit(self):
        return self.provider.limit

    def query(self, *args, **kwargs):
        try:
            for l in self.provider.listings(*args, **kwargs):
                # FIXME
                # l._provider = self.provider

                # self.provider.on_new_listing(l)
                yield(l)

        except SGException as e:
            logger.exception(e)
            return []

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "ctrl r":
            self.provider.reset()
            # state.asyncio_loop.create_task(self.provider.refresh())
        # elif key == "d":
        #     self.provider.download(self.selection.data)
        else:
            return key

    def decorate(self, row, column, value):

        if column.name == "title":
            return urwid.Text([
                ( next(v for k, v in self.provider.highlight_map.items()
                       if k.search(x)), x)
                if self.provider.highlight_re.search(x)
                else x for x in self.provider.highlight_re.split(value) if x
            ])
        else:
            return super().decorate(row, column, value)
