import logging
logger = logging.getLogger(__name__)
import asyncio
import typing
from typing import (
    Any, Awaitable, Callable, Iterable, Iterator, Dict, List, Tuple, Union
)
import heapq

import urwid
import panwid
from panwid.keymap import *
from panwid.tabview import *
from orderedattrdict import AttrDict, DefaultAttrDict

from ..state import *
from .. import utils

from .browser import *


class BaseTabView(TabView):

    CHANGE_TAB_KEYS = "!@#$%^&*()"

    last_refresh = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(self, "activate", self.on_activate)

    def on_activate(self, source, tab):
        self.active_tab.content.on_view_activate()

    def keypress(self, size, key):

        if key in self.CHANGE_TAB_KEYS:
            idx = int(self.CHANGE_TAB_KEYS.index(key))
            if idx < 0:
                idx += 10
            self.set_active_tab(idx)

        elif key == 'tab':
            self.set_active_next()

        elif key == 'shift tab':
            self.set_active_prev()

        else:
            return super(BaseTabView, self).keypress(size, key)

@keymapped()
class StreamglobView(panwid.BaseView):

    KEYMAP = {
        "q": "quit_app"
    }

    @property
    def view_name(self):
        return self.__class__.__name__.replace("View", "").lower()

    @keymap_command
    def quit_app(self):

        state.listings_view.provider.deactivate()
        state.event_loop.create_task(state.task_manager.stop())
        state.task_manager_task.cancel()
        raise urwid.ExitMainLoop()

    def on_view_activate(self):
        pass

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

@keymapped()
class BaseDataTable(panwid.DataTable):

    KEYMAP = {
        "j": "keypress down",
        "k": "keypress up",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def __init__(self, value="", align="left", padding=1):
        self.edit = self.EDIT_WIDGET(align=align, wrap="clip")
        # FIXME: use different attributes
        self.padding = urwid.Padding(self.edit, left=padding, right=padding)
        self.attr = urwid.AttrMap(self.padding, "dropdown_text", "dropdown_focused")
        super().__init__(self.attr)
        urwid.connect_signal(self.edit, "postchange", lambda s, w: self.changed())
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

    auto_complete = True

    KEYMAP_GLOBAL = {
        "dropdown": {
            "k": "up",
            "j": "down",
            "page up": "page up",
            "page down": "page down",
            "ctrl up": ("cycle", [1]),
            "ctrl down": ("cycle", [-1]),
            "home": "home",
            "end": "end",
            "/": "complete prefix",
            "?": "complete substring",
        },
        "dropdown_dialog": {
            "esc": "cancel",
            "/": "complete substring",
            "?": "complete prefix",
            "ctrl p": ("complete", [], {"step": -1, "no_wrap": True}),
            "ctrl n": ("complete", [], {"step": 1, "no_wrap": True}),
        }
    }

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

# Logging widget (c) Ben Niemann, https://github.com/odahoda/noisicaa, with
# modifications to strip emoji as a workaround to urwid/urwid#225

class LogBuffer(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.__records = {}  # type: Dict[int, List[Tuple[int, logging.LogRecord]]]
        self.__next_record_num = 0
        self.__listeners = {}  # type: Dict[str, Callable[[logging.LogRecord], None]]

    @property
    def records(self) -> Iterator[logging.LogRecord]:
        # pylint: disable=protected-access
        heappop = heapq.heappop
        siftup = heapq._siftup  # type: ignore[attr-defined]
        _StopIteration = StopIteration

        h = []  # type: List[Any]
        h_append = h.append
        for it in map(iter, self.__records.values()):  # type: ignore[arg-type]
            try:
                h_append([next(it), it])
            except _StopIteration:
                pass
        heapq.heapify(h)

        while 1:
            try:
                while 1:
                    (_, record), it = s = h[0]
                    yield record
                    s[0] = next(it)
                    siftup(h, 0)
            except _StopIteration:
                heappop(h)
            except IndexError:
                return

    def emit(self, record: logging.LogRecord) -> None:
        with self.lock:
            records = self.__records.setdefault(record.levelno, [])
            records.append((self.__next_record_num, record))
            if len(records) > 10000:
                del records[:1]
            self.__next_record_num += 1

            for listener in self.__listeners.values():
                listener(record)

    def add_listener(self, name: str, listener: Callable[[logging.LogRecord], None]) -> None:
        with self.lock:
            assert name not in self.__listeners
            self.__listeners[name] = listener

    def remove_listener(self, name: str) -> None:
        with self.lock:
            self.__listeners.pop(name, None)


class LogViewer(urwid.Widget):
    _sizing = frozenset(['box'])
    _selectable = True
    ignore_focus = True

    def __init__(self, event_loop: asyncio.AbstractEventLoop, log_buffer: LogBuffer) -> None:
        super().__init__()

        self.__mode = 'tail'
        self.__min_loglevel = logging.INFO

        self.__cols = 80
        self.__rows = 20
        self.__lines = []  # type: List[str]
        self.__cursor = 0

        self.__formatter = logging.Formatter(
            "%(asctime)s [%(module)16s:%(lineno)-4d] [%(levelname)8s] %(message)s"
        )

        self.__event_loop = event_loop
        self.__log_buffer = log_buffer
        self.__log_buffer.add_listener('viewer', self.__new_record_threadsafe)

    async def cleanup(self) -> None:
        self.__log_buffer.remove_listener('viewer')

    def __new_record_threadsafe(self, record: logging.LogRecord) -> None:
        self.__event_loop.call_soon_threadsafe(self.__new_record, record)

    def __new_record(self, record: logging.LogRecord) -> None:
        if self.__mode != 'tail':
            return

        if not self.__filter(record):
            return

        for line in self.__format(record):
            self.__lines.append(utils.strip_emoji(line))
        if len(self.__lines) > 10000:
            del self.__lines[:len(self.__lines) - 10000]

        self._invalidate()

    def __filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < self.__min_loglevel:
            return False

        return True

    def __format(self, record: logging.LogRecord) -> Iterator[str]:
        formatted = self.__formatter.format(record)
        for line in formatted.split('\n'):
            for c in range(0, len(line), self.__cols):
                yield line[c:c+self.__cols]

    def __populate(self) -> None:
        self.__lines = []

        with self.__log_buffer.lock:
            for record in self.__log_buffer.records:
                if not self.__filter(record):
                     continue
                for line in self.__format(record):
                    self.__lines.append(utils.strip_emoji(line))

    def render(self, size: Tuple[int, ...], focus: bool = False) -> urwid.Canvas:
        if (self.__cols, self.__rows) != size:
            self.__populate()

        self.__cols, self.__rows = size

        if self.__mode == 'tail':
            lines = self.__lines[-self.__rows:]
        else:
            lines = self.__lines[self.__cursor:self.__cursor + self.__rows]

        lines.extend([''] * (self.__rows - len(lines)))

        e = []
        c = []
        for line in lines:
            line = line[:self.__cols]
            line += ' ' * (self.__cols - len(line))
            text, cs = urwid.apply_target_encoding(line)
            e.append(text)
            c.append(cs)

        return urwid.TextCanvas(e, None, c)

    def keypress(self, size: Tuple[int, ...], key: str) -> typing.Optional[str]:
        _, rows = size

        if key in ('d', 'i', 'w', 'e'):
            self.__min_loglevel = {
                'd': logging.DEBUG,
                'i': logging.INFO,
                'w': logging.WARNING,
                'e': logging.ERROR,
            }[key]
            self.__populate()
            if self.__mode == 'scroll':
                self.__cursor = max(0, len(self.__lines) - rows)
            self._invalidate()
            return None

        if key == 'p':
            if self.__mode == 'tail':
                self.__mode = 'scroll'
                self.__cursor = max(0, len(self.__lines) - rows)
            else:
                self.__mode = 'tail'
                self.__populate()
            self._invalidate()
            return None

        if key in ('up', 'down', 'page up', 'page down', 'home', 'end'):
            if self.__mode != 'scroll':
                self.__mode = 'scroll'
                self.__cursor = max(0, len(self.__lines) - rows)

            if key == 'up':
                self.__cursor = max(self.__cursor - 1, 0)
            elif key == 'down':
                self.__cursor = min(self.__cursor + 1, max(0, len(self.__lines) - self.__rows))
            elif key == 'page up':
                self.__cursor = max(self.__cursor - self.__rows, 0)
            elif key == 'page down':
                self.__cursor = min(
                    self.__cursor + self.__rows, max(0, len(self.__lines) - self.__rows))
            elif key == 'home':
                self.__cursor = 0
            elif key == 'end':
                self.__cursor = max(0, len(self.__lines) - self.__rows)

            self._invalidate()
            return None

        return key
