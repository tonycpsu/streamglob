import urwid
import panwid

class FilterToolbar(urwid.WidgetWrap):

    signals = ["provider_change"]
    def __init__(self, filters):

        self.filters = [ f.make_widget() for f in filters ]
        self.columns = urwid.Columns([
            ('weight', 1, f)
            for f in self.filters
        ])
        self.filler = urwid.Filler(self.columns)
        super(FilterToolbar, self).__init__(self.filler)


class ProviderDataTable(panwid.DataTable):

    # columns = [panwid.DataTableColumn("item")]

    def __init__(self, listings_method, columns, *args, **kwargs):
        self.listings_method = listings_method
        self.columns = columns
        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.listings_method()
