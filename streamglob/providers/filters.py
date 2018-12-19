import abc

import urwid
import panwid
from additional_urwid_widgets.widgets.date_picker import DatePicker
from datetime import datetime, timedelta
import dateutil.parser
from dateutil.relativedelta import relativedelta

class Filter(abc.ABC):

    signals = ["change"]

    def __init__(self, provider, *args, **kwargs):
        self.provider = provider
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

class DateFilterWidget(urwid.WidgetWrap):

    signals = ["change"]

    def __init__(self, initial_date=None):

        self.date_picker = DatePicker(
            initial_date=initial_date,
            space_between = 0,
            columns=(DatePicker.PICKER.YEAR, DatePicker.PICKER.MONTH, DatePicker.PICKER.DAY),
            return_unused_navigation_input = True,
            day_format = (DatePicker.DAY_FORMAT.DAY_OF_MONTH, DatePicker.DAY_FORMAT.WEEKDAY),
            # topBar_endCovered_prop=("ᐃ", "dp_barActive_focus", "dp_barActive_offFocus"),
            # topBar_endExposed_prop=("───", "dp_barInactive_focus", "dp_barInactive_offFocus"),
            # bottomBar_endCovered_prop=("ᐁ", "dp_barActive_focus", "dp_barActive_offFocus"),
            # bottomBar_endExposed_prop=("───", "dp_barInactive_focus", "dp_barInactive_offFocus"),
            highlight_prop=("dp_highlight_focus", "dp_highlight_offFocus")
        )
        self.button = urwid.Button("OK", on_press=lambda w: self.date_changed())
        self.columns = urwid.Columns([
            # (6, urwid.Padding(urwid.Text(""))),
            (40, self.date_picker),
            # (10, urwid.BoxAdapter(urwid.Filler(self.button), 3))
        ])
        # self.columns.focus_position = 1
        super(DateFilterWidget, self).__init__(self.columns)

    def selectable(self):
        return True

    @property
    def date(self):
        return self.date_picker.get_date()

    def cycle_day(self, n=1):
        d = self.date + timedelta(days=n)
        self.date_picker.set_date(d)

    def cycle_month(self, n=1):
        d = self.date + relativedelta(months=n)
        self.date_picker.set_date(d)

    def date_changed(self):
        self._emit("change", self, self.date)

    def keypress(self, size, key):
        # key = super(DateFilterWidget, self).keypress(size, key)
        # if key == "enter":
        #     self.date_picker.enable()
        # elif key == "esc":
        #     self.date_picker.disable()

        if key in ["-", "="]:
            self.cycle_day(-1 if key == "-" else 1)
            self.date_changed()
        if key in ["_", "+"]:
            self.cycle_month(-1 if key == "_" else 1)
            self.date_changed()
        elif key in ["ctrl left", "ctrl right"]:
            self.cycle_day(-1 if key == "ctrl left" else 1)
        else:
            return super(DateFilterWidget, self).keypress(size, key)
            # return key

class DateFilter(Filter):

    # FIXME: use calendar
    # WIDGET_CLASS = urwid.Edit
    # WIDGET_CLASS = panwid.CalendarWidget
    WIDGET_CLASS = DateFilterWidget

    # def __init__(self):
    #     self.start_date = start_date

    @property
    def widget_kwargs(self):
        # return {"edit_text": "2018-01-01"}
        # return {"begindate": datetime.now().date()}
        return {"initial_date": datetime.now().date()}

    # def make_widget(self):
    #     w = super(DateFilter, self).make_widget()
    #     w.on_date_change = lambda d: self.set_date(d)

    @property
    def value(self):
        return self.widget.date
        # return dateutil.parser.parse(self.widget.get_text()[0])


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
