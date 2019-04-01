import logging
logger = logging.getLogger(__name__)

import abc

import urwid
import panwid
from datetime import datetime, timedelta
import dateutil.parser
from dateutil.relativedelta import relativedelta
from orderedattrdict import AttrDict

from .. import config
from ..widgets import *

from .widgets import *
from ..exceptions import *


class Filter(Observable):

    def __init__(self, provider, name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider = provider
        self.name = name

    @property
    def value(self):
        return self.widget.value

    @value.setter
    def value(self, value):
        # logger.info(f"filter {self} = {value}")
        changed = (self.widget.value != value)
        # self.widget.set_value(value)
        self.widget.value = value
        if changed and self.provider.is_active:
            self.changed()


class MaybeLabeledWidget(urwid.WidgetWrap):

    def __init__(self, widget, label=None, sizing=("weight", 1)):
        self.widget = widget

        self.innerwidget = self.widget
        if label:
            self._text = urwid.Text(f"{label}: ")
            self._columns = urwid.Columns([
                ("pack", self._text),
            ], dividechars=1)
            self._columns.contents += [
                (widget, self._columns.options(*sizing)),
            ]
            # self._columns.selectable = lambda: True
            self._columns.focus_position = 1
            self.innerwidget = self._columns
        return super().__init__(self.innerwidget)


class WidgetFilter(Filter):

    def __init__(self, provider, name, label=None, hidden=False, *args, **kwargs):
        super().__init__(provider, name)
        if not hasattr(self, "label"):
            if label is not None:
                self.label = label
            else:
                self.label = f"{self.name.replace('_', ' ').title()}"

        self.hidden = hidden
        self._placeholder = None
        self._widget = None

    @property
    def widget(self):
        if not self._widget:
            self._widget = self.WIDGET_CLASS(
                *self.widget_args, **self.widget_kwargs
            )
            if isinstance(self._widget, Observable):
                self._widget.connect("changed", lambda v: self.changed())
                # self.changed()
        return self._widget

    @property
    def placeholder(self):
        if not self._placeholder:
            self.innerwidget = MaybeLabeledWidget(
                self.widget, self.label , sizing=self.widget_sizing(self.widget)
            )
            self._placeholder = urwid.WidgetPlaceholder(self.innerwidget)
            if self.hidden:
                self.hide()
        return self._placeholder

    # def on_change(self, source, *args):
    #     urwid.signals.emit_signal(self, "filter_change", *args)

    def show(self):
        self.placeholder.original_widget = self.inner_widget

    def hide(self):
        self.placeholder.original_widget = urwid.Text("")

    @property
    def widget_args(self):
        return list()

    @property
    def widget_kwargs(self):
        return dict()

    @property
    def auto_refresh(self):
        return False

    @property
    def widget_sizing(self):
        return lambda w: ("weight", 1)

    def cycle(self, step=1):
        raise Exception


class BooleanFilterWidget(urwid.CheckBox):

    @property
    def value(self):
        return self.get_state()

    @value.setter
    def value(self, value):
        self.set_state(value)


class BooleanFilter(WidgetFilter, abc.ABC):

    WIDGET_CLASS = BooleanFilterWidget

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self.widget, 'postchange',
            lambda w, v: self.changed()
        )

    @property
    def widget_args(self):
        return ("",)

    @property
    def widget_kwargs(self):
        return {"state": True}

    def cycle(self, step=1):
        self.widget.value = not self.widget.value


class TextFilter(WidgetFilter):

    WIDGET_CLASS = TextFilterWidget

class IntegerFilter(WidgetFilter):

    WIDGET_CLASS = IntegerTextFilterWidget

class IntegerTextFilter(TextFilter):

    WIDGET_CLASS = IntegerTextFilterWidget

    @property
    def widget_kwargs(self):
        return dict(align="right")


class DateDisplay(urwid.WidgetWrap):

    def __init__(self, initial_date, date_format=None, selectable=False):
        self.initial_date = initial_date
        self.date_format = date_format or "%Y-%m-%d"
        if selectable:
            self.widget = urwid.SelectableIcon("", 0)
        else:
            self.widget = urwid.Text("")
        super().__init__(self.widget)
        self.value = self.initial_date

    @property
    def value(self):
        return self._date

    @value.setter
    def value(self, value):
        self._date = value
        self.widget.set_text(self._date.strftime(self.date_format))


class DateFilterWidget(Observable, urwid.WidgetWrap):

    # signals = ["change"]

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
        # self.button = urwid.Button("OK", on_press=lambda w: self.date_changed())
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
    def value(self):
        return self.date_picker.value

    @value.setter
    def value(self, value):
        self.date_picker.value = value
        # logger.info("DateFilterWidget set value")
        self.changed()
        # self.date_changed()

    def reset(self):
        self.value = self.initial_date

    def cycle_day(self, n=1):
        d = self.value + timedelta(days=n)
        self.value = d
        # self.date_picker.date = d
        # self.date_changed()

    def cycle_week(self, n=1):
        d = self.value + timedelta(weeks=n)
        # self.date_picker.date = d
        self.value = d

        # self.date_changed()

    def cycle_month(self, n=1):
        d = self.value + relativedelta(months=n)
        self.value = d
        # self.date_picker.date = d
        # self.date_changed()

    def cycle_year(self, n=1):
        d = self.value + relativedelta(years=n)
        self.value = d
        # self.date_picker.date = d
        # self.date_changed()

    def date_changed(self):
        self.changed()
        # self._emit("change", self, self.value)

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


class DateFilter(WidgetFilter):

    WIDGET_CLASS = DateFilterWidget

    # def __init__(self):
    #     self.start_date = start_date

    @property
    def widget_kwargs(self):
        return {"initial_date": datetime.now().date()}

    @property
    def auto_refresh(self):
        return True

    def reset(self):
        self.widget.reset()

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


class ListingFilter(WidgetFilter, abc.ABC):

    WIDGET_CLASS = BaseDropdown
    # WIDGET_CLASS = panwid.Dropdown

    CYCLE_REVERSE = False

    @property
    def widget_args(self):
        return [self.values]

    @property
    def widget_kwargs(self):
        return dict(default=self.default)

    @property
    def default(self):
        return None

    @property
    def selected_label(self):
        return self.widget.selected_label

    @selected_label.setter
    def selected_label(self, value):
        self.widget.select_label(value)

    # @property
    # def value(self):
    #     return self.widget.selected_value

    # @value.setter
    # def value(self, value):
    #     try:
    #         self.widget.select_value(value)
    #     except StopIteration:
    #         try:
    #             self.widget.select_label(value)
    #         except:
    #             raise SGInvalidFilterValue(f"Filter value {value} not valid")

    def cycle(self, step=1):
        self.widget.cycle(step if not self.CYCLE_REVERSE else -step)

    @property
    def auto_refresh(self):
        return True

    @property
    @abc.abstractmethod
    def values(self):
        pass

    def __getitem__(self, key):
        return self.widget.items[key]


class ConfigFilter(ListingFilter, abc.ABC):

    @property
    @abc.abstractmethod
    def key(self):
        pass

    @property
    def with_all(self):
        return False

    @property
    def values(self):
        cfg = getattr(self.provider.config, self.key)

        if self.with_all:
            items = [("All", None)]
        else:
            items = list()

        if isinstance(cfg, dict):
            return AttrDict(items, **cfg)
        elif isinstance(cfg, list):
            return AttrDict(items, **AttrDict([ (i, i) for i in cfg ]))

    # @property
    # def widget_kwargs(self):
    #     return {"label": "foo"}

    # @property
    # def widget_sizing(self):
    #     return lambda w: ("given", 40)




def with_filters(*filters):
    def outer(cls):
        @wraps(cls)
        def inner(cls, filters):
            cls.FILTERS = filters
            return cls

        return inner(cls, filters)

    return outer

class ResolutionFilter(ListingFilter):

    CYCLE_REVERSE=True

    @property
    def default(self):
        return self.provider.config.defaults.resolution

    @property
    def values(self):
        return self.provider.RESOLUTIONS
