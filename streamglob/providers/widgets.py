import logging
logger = logging.getLogger(__name__)

import functools

import urwid
import panwid
from pony.orm import *

from ..exceptions import *
from ..state import *
from .. import model

# class ResolutionDropdown(panwid.Dropdown):

#     label = "Resolution"

#     def __init__(self, resolutions, default=None):
#         self.resolutions = resolutions
#         super(ResolutionDropdown, self).__init__(resolutions, default=default)

#     @property
#     def items(self):
#         return self.resolutions


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
        if len(self.columns.contents):
            self.columns.focus_position = 1

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
            return self.provider.listings(*args, **kwargs)
        except SGException as e:
            logger.exception(e)
            return []

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "ctrl r":
            self.reset()
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
