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

    limit = 25

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        self.columns = [ panwid.DataTableColumn(k, **v if v else {})
                         for k, v in self.provider.ATTRIBUTES.items() ]

        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    def query(self, *args, **kwargs):

        return self.provider.listings(*args, **kwargs)

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key in ["left", "right"]:
            self._emit(f"cycle_filter", 0, -1 if key == "left" else 1)
        elif key in ["[", "]"]:
            self._emit(f"cycle_filter", 1, -1 if key == "[" else 1)
        else:
            return key
