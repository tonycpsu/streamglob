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

    def cycle(self, step=1):
        # pass
        raise Exception



class TextFilter(Filter):

    WIDGET_CLASS = urwid.Edit


class DateDisplay(urwid.WidgetWrap):

    def __init__(self, initial_date, date_format=None, selectable=False):
        self.initial_date = initial_date
        self.date_format = date_format or "%Y-%m-%d"
        if selectable:
            self.widget = urwid.SelectableIcon("", 0)
        else:
            self.widget = urwid.Text("")
        super().__init__(self.widget)
        self.date = self.initial_date

    @property
    def date(self):
        return self._date

    @date.setter
    def date(self, value):
        self._date = value
        self.widget.set_text(self._date.strftime(self.date_format))


class DateFilterWidget(urwid.WidgetWrap):

    signals = ["change"]

    def __init__(self, initial_date=None, date_format=None):

        self.initial_date = initial_date
        # self.date_picker = DatePicker(
        #     initial_date=initial_date,
        #     space_between = 0,
        #     columns=(DatePicker.PICKER.YEAR, DatePicker.PICKER.MONTH, DatePicker.PICKER.DAY),
        #     return_unused_navigation_input = True,
        #     day_format = (DatePicker.DAY_FORMAT.DAY_OF_MONTH, DatePicker.DAY_FORMAT.WEEKDAY),
        #     highlight_prop=("dp_highlight_focus", "dp_highlight_offFocus")
        # )
        self.date_picker = DateDisplay(self.initial_date, selectable=True)
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
        return self.date_picker.date

    def cycle_day(self, n=1):
        d = self.date + timedelta(days=n)
        self.date_picker.date = d
        self.date_changed()

    def cycle_week(self, n=1):
        d = self.date + timedelta(weeks=n)
        self.date_picker.date = d
        self.date_changed()

    def cycle_month(self, n=1):
        d = self.date + relativedelta(months=n)
        self.date_picker.date = d
        self.date_changed()

    def cycle_year(self, n=1):
        d = self.date + relativedelta(years=n)
        self.date_picker.date = d
        self.date_changed()

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

    WIDGET_CLASS = DateFilterWidget

    # def __init__(self):
    #     self.start_date = start_date

    @property
    def widget_kwargs(self):
        return {"initial_date": datetime.now().date()}


    @property
    def widget_sizing(self):
        return lambda w: ("weight", 1)

    @property
    def value(self):
        return self.widget.date

    def cycle(self, step=1):
        if isinstance(step, int):
            self.widget.cycle_day(step)
        elif isinstance(step, tuple):
            (p, step) = step
            if p == "w":
                self.widget.cycle_week(step)
            elif p == "m":
                self.widget.cycle_month(step)
            elif p == "y":
                self.widget.cycle_year(step)
            else:
                raise Exception("invalid time period: %s" %(p))



class ListingFilter(Filter, abc.ABC):

    # WIDGET_CLASS = BoxedDropdown
    WIDGET_CLASS = panwid.Dropdown

    @property
    def widget_args(self):
        return [self.values]

    @property
    def widget_sizing(self):
        return lambda w: ("weight", 1)

    @property
    def value(self):
        return self.widget.selected_value

    def cycle(self, step=1):
        self.widget.cycle(step)

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

class ResolutionFilter(ListingFilter):

    @property
    def values(self):
        return self.provider.RESOLUTIONS
