import logging
logger = logging.getLogger(__name__)

import urwid
import panwid
from pony.orm import *

from ..exceptions import *
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
        self.columns = urwid.Columns([])
        for n, f in self.filters.items():
            self.columns.contents += [
                (urwid.Text(f"{n.replace('_', ' ').title()}: "), self.columns.options("pack")),
                (f.widget, self.columns.options(*f.widget_sizing(f.widget))),
            ]
        if len(self.columns.contents):
            self.columns.focus_position = 1

        for n, f in self.filters.items():
            if f.auto_refresh:
                urwid.connect_signal(
                    f.widget, "change",
                    lambda s, *args: self._emit("filter_change", n, *args)
                )
            else:
                if "select" in f.widget.signals:
                    urwid.connect_signal(
                        f.widget, "select",
                        lambda s, *args: self._emit("filter_change", n, *args)
                    )

        self.filler = urwid.Filler(self.columns)
        super(FilterToolbar, self).__init__(self.filler)

    def cycle_filter(self, index, step=1):
        if index > len(self.filters)+1:
            return
        list(self.filters.values())[index].cycle(step)

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)



class ProviderDataTable(panwid.DataTable):

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
        else:
            return key

class CachedFeedProviderDataTable(ProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self, "focus",
            self.on_update_focus
        )

    @db_session
    def on_update_focus(self, source, position):
        # logger.info(f"focus: {position}")
        item = self.provider.feed.ITEM_CLASS.get(
            guid=self[position].data.get("guid")
        )
        item.mark_seen()


    def keypress(self, size, key):

        if key == "ctrl r":
            self.provider.update()
            self.reset()
        else:
            return super().keypress(size, key)
        return key

    # @property
    # def focus_position(self):
    #     print(f"get focus_position")
    #     return super(CachedFeedProviderDataTable, self).focus_position
    #     # val = ProviderDataTable.focus_position
    #     # raise Exception(dir(val))

    # @ProviderDataTable.focus_position.setter
    # def focus_position(self, value):
    #     print(f"focus_position: {value}")
    #     ProviderDataTable.focus_position.fset(self, value)
    #     # super(CachedFeedProviderDataTable, self).focus_position = value
    #     # .focus_position = value
