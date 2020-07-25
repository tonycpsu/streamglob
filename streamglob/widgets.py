import logging
logger = logging.getLogger(__name__)

import urwid
import panwid
from orderedattrdict import AttrDict, DefaultAttrDict

class SquareButton(urwid.Button):

    button_left = urwid.Text("[")
    button_right = urwid.Text("]")

    def pack(self, size, focus=False):
        cols = sum(
            [ w.pack()[0] for w in [
                self.button_left,
                self._label,
                self.button_right
            ]]) + self._w.dividechars*2

        return ( cols, )

class BaseDataTable(panwid.DataTable):
    pass
    # def keypress(self, size, key):
    #     key = super().keypress(size, key)

    #     if key == "ctrl d":
    #         logger.info(self.focus_position)
    #         self.log_dump(20)
    #     else:
    #         return key

class Observable(object):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._callbacks = DefaultAttrDict(list)

    def connect(self, event, callback):
        self._callbacks[event].append(callback)
        # logger.info(f"{self.__class__.__name__}, {self._callbacks}")

    def notify(self, event, *args):
        for fn in self._callbacks[event]:
            # logger.info(f"callback: {event}, {fn}")
            # raise Exception(fn, args)
            fn(*args)

    def changed(self):
        self.notify("changed", self.value)

    def selected(self):
        self.notify("selected")


class IntEdit(urwid.Edit):

    def valid_char(self, ch):
        return len(ch)==1 and ch in "0123456789"


class TextFilterWidget(Observable, urwid.WidgetWrap):

    # signals = ["change", "select"]

    EDIT_WIDGET = urwid.Edit
    VALUE_TYPE = str

    def __init__(self, value, align="left", padding=1):
        self.edit = self.EDIT_WIDGET(align=align, wrap="clip")
        # FIXME: use different attributes
        self.padding = urwid.Padding(self.edit, left=padding, right=padding)
        self.attr = urwid.AttrMap(self.padding, "dropdown_text", "dropdown_focused")
        super().__init__(self.attr)
        # urwid.connect_signal(self.edit, "select", lambda s, w: self.selected)
        self.value = value

    def keypress(self, size, key):
        if key == "enter":
            if len(self.edit.get_edit_text()):
                self.selected()
                # self._emit("select", self.edit.get_edit_text()[0])
        else:
            return super().keypress(size, key)

    @property
    def value(self):
        try:
            return self.VALUE_TYPE(self.edit.get_text()[0])
        except ValueError:
            return None

    @value.setter
    def value(self, value):
        changed = (self.value != value)
        # t = list(self.get_text())
        if not changed:
            return
        self.edit.set_edit_text(str(value))
        self.edit.set_edit_pos(len(str(self.value))-1)
        self.changed()

class IntegerTextFilterWidget(TextFilterWidget):

    EDIT_WIDGET = IntEdit
    VALUE_TYPE = int

    def __init__(self, default=0, minimum=0, maximum=None, big_step=10):
        self.minimum = minimum
        self.maximum = maximum
        self.big_step = big_step

        self.default = default
        if self.minimum is not None:
            self.default = max(self.minimum, self.default)
        if self.maximum is not None:
            self.default = min(self.maximum, self.default)
        super().__init__(str(self.default))

    # @property
    # def value(self):
    #     v = super().value
    #     try:
    #         return int(v)
    #     except ValueError:
    #         return 0

    # @value.setter
    # def value(self, value):
    #     # https://gitlab.gnome.org/GNOME/gnome-music/snippets/31
    #     super(IntegerTextFilterWidget, self.__class__).value.fset(self, str(value))

    def cycle(self, step):
        v = self.value + step
        if (self.minimum is not None and v < self.minimum):
            self.value = self.minimum
        elif (self.maximum is not None and v > self.maximum):
            self.value = self.maximum
        else:
            self.value = v

    def keypress(self, size, key):

        if key == "ctrl up":
            self.cycle(1)
        elif key == "ctrl down":
            self.cycle(-1)
        elif key == "page up":
            self.cycle(self.big_step)
        elif key == "page down":
            self.cycle(-self.big_step)
        elif key == "right" and self.edit.edit_pos==len(str(self.value))-1:
            self.edit.set_edit_pos(len(str(self.value)))
            super().keypress(size, "right")
            # self.edit.set_edit_pos(len(str(self.value))-1)
        #     return super().keypress(size, "next selectable")
        else:
            return super().keypress(size, key)



class BaseDropdown(Observable, panwid.Dropdown):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self,
            "change",
            lambda s, w, v: self.changed()
        )

    def keypress(self, size, key):

        if key == "ctrl up":
            self.cycle(-1)
        elif key == "ctrl down":
            self.cycle(1)
        else:
            return super().keypress(size, key)

class ScrollbackListBox(panwid.listbox.ScrollingListBox):

    signals = ["updated"]

    def _modified(self):
        self.body._modified()

    def append(self, text):

        result = urwid.Text(text)
        self.body.append(result)
        self.on_updated()

    # def keypress(self, size, key):

    #     if key == 'up' or key == 'k':
    #         self._listbox.keypress(size, 'up')
    #     elif key == 'page up' or key == 'ctrl u':
    #         self._listbox.keypress(size, 'page up')
    #     elif key == 'down' or key == 'j':
    #         self._listbox.keypress(size, 'down')
    #     elif key == 'page down' or key == 'ctrl d':
    #         self._listbox.keypress(size, 'page down')
    #     elif key == 'home':
    #         if len(self._listbox.body):
    #             self._listbox.focus_position = 0
    #             self.listbox._invalidate()
    #     elif key == 'end':
    #         if len(self._listbox.body):
    #             self._listbox.focus_position = len(self._listbox.body)-1
    #             self._listbox._invalidate()
    #     return super(ScrollbackListBox, self).keypress(size, key)

    # def clear(self):
    #     self._results.reset()

    def on_updated(self):
        self._invalidate()
        self.set_focus(len(self.body)-1)
        if not (getattr(self, "_width", None) and getattr(self, "_height", None)):
            return
        self.listbox.make_cursor_visible((self._width, self._height))
        # state.loop.draw_screen()

    def selectable(self):
        return True


class ConsoleWindow(urwid.WidgetWrap):

    def __init__(self, verbose=False):

        self.verbose = verbose
        self.listbox =  ScrollbackListBox([], with_scrollbar=True)
        super(ConsoleWindow, self).__init__(self.listbox)

    def log_message(self, msg):
        self.listbox.append(msg.rstrip())
        self.listbox._modified()

    def mark(self):
        self.log_message("-" * 80)

    def selectable(self):
        return False

    def keypress(self, size, key):
        if key == "m":
            self.mark()
        # return super(ConsoleWindow, self).kepyress(size, key)
        return key
