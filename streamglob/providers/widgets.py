import urwid
import panwid

class FilterToolbar(urwid.WidgetWrap):

    signals = ["filter_change"]

    def __init__(self, filters):

        self.filters = filters
        self.columns = urwid.Columns([
            ('weight', 1, f.widget)
            for f in self.filters.values()
        ])
        # self.columns.focus_position = 0

        for n, f in self.filters.items():
            urwid.connect_signal(
                f.widget, "change",
                lambda s, w, v: self._emit("filter_change", n, v)
            )
        self.filler = urwid.Filler(self.columns)
        super(FilterToolbar, self).__init__(self.filler)

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)

class ProviderDataTable(panwid.DataTable):

    # columns = [panwid.DataTableColumn("item")]

    def __init__(self, listings_method, columns, *args, **kwargs):
        self.listings_method = listings_method
        self.columns = columns
        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.listings_method()
