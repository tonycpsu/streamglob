import urwid
import panwid


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
            urwid.connect_signal(
                f.widget, "change",
                lambda s, w, v: self._emit("filter_change", n, v)
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

    def __init__(self, listings_method, columns, *args, **kwargs):

        self.listings_method = listings_method
        self.columns = columns
        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    def query(self, *args, **kwargs):

        return self.listings_method()

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key in ["left", "right"]:
            self._emit(f"cycle_filter", 0, -1 if key == "left" else 1)
        elif key in ["[", "]"]:
            self._emit(f"cycle_filter", 1, -1 if key == "[" else 1)
        else:
            return key
