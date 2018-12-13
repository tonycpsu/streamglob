import abc

import urwid
import panwid
from datetime import datetime

class Filter(abc.ABC):

    signals = ["change"]

    def __init__(self, *args, **kwargs):
        self.widget = self.WIDGET_CLASS(
            *self.widget_args, **self.widget_kwargs
        )
        urwid.connect_signal(
            self.widget, "change",
            self.on_change
        )

    def on_change(self, source, widget, value):
        # raise Exception(source, widget, value)
        urwid.signals.emit_signal(self, "change", value)

    def make_widget(self):
        return self.widget

    @property
    def widget_args(self):
        return list()

    @property
    def widget_kwargs(self):
        return dict()


class TextFilter(Filter):

    WIDGET_CLASS = urwid.Edit


class DateFilter(Filter):

    # FIXME: use calendar
    # WIDGET_CLASS = urwid.Edit
    WIDGET_CLASS = panwid.CalendarWidget

    # def __init__(self):
    #     self.start_date = start_date

    @property
    def widget_kwargs(self):
        return {"begindate": datetime.now().date()}

    # def make_widget(self):
    #     w = super(DateFilter, self).make_widget()
    #     w.on_date_change = lambda d: self.set_date(d)

    @property
    def value(self):
        return self.widget.date


class ListingFilter(Filter, abc.ABC):

    WIDGET_CLASS = panwid.Dropdown

    @property
    def widget_args(self):
        return [self.values]

    @property
    def value(self):
        return self.widget.selected_value

    def populate(self, values):
        self.values = values

    @property
    @abc.abstractmethod
    def values(self):
        pass


def with_filters(*filters):
    def outer(cls):
        @wraps(cls)
        def inner(cls, filters):
            cls.FILTERS = filters
            return cls

        return inner(cls, filters)

    return outer
