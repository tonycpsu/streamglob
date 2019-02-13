import logging
logger = logging.getLogger(__name__)

import functools
import re

import urwid
import panwid
from pony.orm import *

from ..exceptions import *
from ..state import *
from .. import model


class TextFilterWidget(urwid.Edit):

    signals = ["change", "select"]

    def keypress(self, size, key):
        if key == "enter":
            if len(self.get_edit_text()):
                self._emit("select", self.get_edit_text()[0])
        else:
            return super().keypress(size, key)

    @property
    def value(self):
        return self.get_text()[0]

    @value.setter
    def value(self, value):
        # t = list(self.get_text())
        self.set_edit_text(value)


class IntegerTextFilterWidget(TextFilterWidget):

    def __init__(self, default=0, minimum=0, maximum=None, big_step=10):
        self.minimum = minimum
        self.maximum = maximum
        self.big_step = big_step

        self.default = default
        if self.minimum is not None:
            self.default = max(self.minimum, self.default)
        if self.maximum is not None:
            self.default = min(self.maximum, self.default)
        super().__init__(edit_text=str(self.default))

    @property
    def value(self):
        v = super().value
        try:
            return int(v)
        except ValueError:
            return 0

    @value.setter
    def value(self, value):
        # https://gitlab.gnome.org/GNOME/gnome-music/snippets/31
        super(IntegerTextFilterWidget, self.__class__).value.fset(self, str(value))

    def cycle(self, step):
        v = self.value + step
        if (
                (self.minimum is None or v >= self.minimum)
                and (self.maximum is None or v <= self.maximum)
        ):
            self.value = v

    def keypress(self, size, key):

        if key == "ctrl up":
            self.cycle(1)
        elif key == "ctrl down":
            self.cycle(-1)
        elif key == "page up":
            self.cycle(self.big_step)
        elif key == "page down":
            self.cycle(-self.big_step)
        else:
            if len(key) == 1 and key not in [ str(n) for n in range(10) ]:
                return
            return super().keypress(size, key)



class FilterToolbar(urwid.WidgetWrap):

    signals = ["filter_change"]

    def __init__(self, filters):

        self.filters = filters
        self.columns = urwid.Columns([], dividechars=1)
        for n, f in self.filters.items():
            # self.columns.contents += [
            #     (urwid.Text(f"{n.replace('_', ' ').title()}: "), self.columns.options("pack")),
            #     (f.placeholder, self.columns.options(*f.widget_sizing(f.widget))),
            # ]
            self.columns.contents += [
                (f.placeholder, self.columns.options(*f.widget_sizing(f.widget))),
            ]

        # for i, (n, f) in enumerate(self.filters.items()):
        for n, f in self.filters.items():
            if f.auto_refresh:
                urwid.connect_signal(
                    f.widget, "change",
                    # lambda s, *args: self._emit("filter_change", i, n, *args)
                    functools.partial(self._emit, "filter_change", n, f)
                )
            else:
                if "select" in f.widget.signals:
                    urwid.connect_signal(
                        f.widget, "select",
                        functools.partial(self._emit, "filter_change", n, f)
                    )

        self.filler = urwid.Filler(self.columns)
        super(FilterToolbar, self).__init__(self.filler)

    def cycle_filter(self, index, step=1):
        if index >= len(self.filters):
            return
        list(self.filters.values())[index].cycle(step)

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)



class ProviderDataTable(panwid.DataTable):

    no_load_on_init = True

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
                l._provider = self.provider
                self.provider.on_new_listing(l)
                yield(l)

        except SGException as e:
            logger.exception(e)
            return []

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "ctrl r":
            self.provider.refresh()
            # state.asyncio_loop.create_task(self.provider.refresh())
        elif key == "d":
            self.provider.download(self.selection.data)
        elif key in ["left", "right"]:
            self._emit(f"cycle_filter", 0, -1 if key == "left" else 1)
        elif key in ["[", "]"]:
            self._emit(f"cycle_filter", 1, -1 if key == "[" else 1)
        elif key in ["{", "}"]:
            self._emit(f"cycle_filter", 2, -1 if key == "{" else 1)
        elif key == "?":
            logger.info(self.selection.data.read)
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
