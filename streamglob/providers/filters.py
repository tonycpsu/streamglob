import abc

import urwid
import panwid

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
