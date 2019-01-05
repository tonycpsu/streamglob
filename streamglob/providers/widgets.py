import logging
logger = logging.getLogger(__name__)

import functools

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
        elif key == "?":
            logger.info(self.selection.data.read)
        else:
            return key

class CachedFeedProviderDataTable(ProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ignore_blur = False
        urwid.connect_signal(
            self, "blur",
            self.on_blur
        )

    def row_attr_fn(self, row):
        if not row.get("read"):
            return "unread"
        return None


    @db_session
    def on_blur(self, source, position):
        if self.ignore_blur:
            self.ignore_blur = False
            return
        self.mark_item_read(position)

    @db_session
    def mark_item_read(self, position):
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_read()
        self.selection.clear_attr("unread")
        self.set_value(position, "read", item.read)

    @db_session
    def mark_item_unread(self, position):
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_unread()
        self.selection.set_attr("unread")
        self.set_value(position, "read", item.read)

    @db_session
    def toggle_item_read(self, position):
        logger.info(self.get_value(position, "read"))
        if self.get_value(position, "read") is not None:
            self.mark_item_unread(position)
        else:
            self.mark_item_read(position)

    @db_session
    def item_at_position(self, position):
        return self.provider.feed.ITEM_CLASS.get(
            guid=self[position].data.get("guid")
        )

    def keypress(self, size, key):

        if key == "meta r":
            self.provider.update()
            self.reset()
        elif key == "A":
            with db_session:
                self.provider.feed.mark_all_read()
            self.reset()
        elif key == "u":
            self.toggle_item_read(self.focus_position)
            self.ignore_blur = True
        else:
            return super().keypress(size, key)
        return key
