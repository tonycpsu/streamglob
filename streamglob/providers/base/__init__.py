import abc

import urwid
import panwid

from ... import session
from functools import wraps

def get(provider, *args, **kwargs):
    provider_class = next( v for k, v in globals().items()
                           if k.lower() == f"{provider}Provider".lower())
    return provider_class(*args, **kwargs)


class BaseProvider(abc.ABC):

    SESSION_CLASS = session.StreamSession
    FILTERS = []

    def __init__(self, *args, **kwargs):
        self.session = self.SESSION_CLASS(*args, **kwargs)
        # self.filters = [ f() for f in self.FILTERS ]

    @abc.abstractmethod
    def login(self):
        pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @abc.abstractmethod
    def make_view(self):
        pass


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
    
    columns = [panwid.DataTableColumn("item")]

    def query(self, *args, **kwargs):
        return []
        
    
class SimpleProviderViewMixin(object):

    def make_view(self):        

        self.toolbar = FilterToolbar(self.FILTERS)
        self.table = ProviderDataTable()
        
        self.pile  = urwid.Pile([
            (1, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 1
        return self.pile

    
    
class Filter(abc.ABC):
    
    def make_widget(self):
        return self.WIDGET_CLASS(self.values)
    

class TextFilter(object):

    WIDGET_CLASS = urwid.Edit

    
class DateFilter(Filter):

    # FIXME: use calendar
    WIDGET_CLASS = urwid.Edit

class ListingFilter(Filter):
    
    WIDGET_CLASS = panwid.Dropdown

class FixedListingFilter(ListingFilter):
    
    def __init__(self, values):
        self.values = values

class VariableListingFilter(ListingFilter):
    
    def populate(self, values):
        self.values = values


def with_filters(*filters):
    def outer(cls):
        @wraps(cls)
        def inner(cls, filters):
            cls.FILTERS = filters
            return cls
        
        return inner(cls, filters)

    return outer


# @with_filters(DateFilter, FixedListingFilter)
class TestProvider(SimpleProviderViewMixin, BaseProvider):
    
    SESSION_CLASS = session.StreamSession
    FILTERS = [
        FixedListingFilter(["foo", "bar", "baz"])
    ]
    
    def login(self):
        print(self.session)

    def listings(self):
        pass
        
