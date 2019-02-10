import logging
# logger = logging.getLogger(__name__)
import os
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess
import select
import time
import re
import asyncio
import functools

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *
from panwid.dialog import *
from panwid.tabview import *
from pony.orm import db_session
from tonyc_utils.logging import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

from .state import *
from . import config
from . import model
from . import widgets
from . import utils
from . import session
from . import providers
from . import player
from . import tasks
from .exceptions import *

urwid.AsyncioEventLoop._idle_emulation_delay = 1/20

PACKAGE_NAME=__name__.split('.')[0]

class UrwidLoggingHandler(logging.Handler):

    pipe = None
    # def __init__(self, console):

    #     self.console = console
    #     super(UrwidLoggingHandler, self).__init__()

    def connect(self, pipe):
        self.pipe = pipe

    def emit(self, rec):

        if not self.pipe:
            return
        msg = self.format(rec)
        (ignore, ready, ignore) = select.select([], [self.pipe], [])
        if self.pipe in ready:
            os.write(self.pipe, (msg[:512]+"\n").encode("utf-8"))



# urwid uses sys.exc_info() to get `(type, value, traceback)`, which is needed
# in python2 to re-raise exceptions.  However, sys.exc_info() can return `(None,
# None, None)` which leaves us with nothing to re-raise. It's possible this
# happens only with the asyncio event loop.
#
# This is easily solved for asyncio/python3 since we only need the exception
# which is guaranteed by asyncio.
class AsyncioEventLoop_patched(urwid.AsyncioEventLoop):
    def _exception_handler(self, loop, context):
        exc = context.get('exception')
        if exc:
            loop.stop()
            if not isinstance(exc, urwid.ExitMainLoop):
                self._exc_info = exc
        else:
            loop.default_exception_handler(context)

    def run(self):
        self._loop.set_exception_handler(self._exception_handler)
        self._loop.run_forever()
        if self._exc_info:
            raise self._exc_info

urwid.AsyncioEventLoop = AsyncioEventLoop_patched


def quit_app():

    # tasks.stop_task_manager()
    state.asyncio_loop.create_task(state.task_manager.stop())
    state.task_manager_task.cancel()

    raise urwid.ExitMainLoop()


class BaseTabView(TabView):

    CHANGE_TAB_KEYS = "!@#$%^&*()"

    last_refresh = None

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

class MainToolbar(urwid.WidgetWrap):

    signals = ["provider_change"]
    def __init__(self, default_provider):

        def format_provider(n, p):
            return p.NAME if p.config_is_valid else f"* {p.NAME}"

        def providers_sort_key(p):
            k, v = p
            # providers = list(config.settings.profile.providers.keys())
            # if k in providers:
            # raise Exception(v)
            if v.config_is_valid:
                return (0, str(v.NAME))
            else:
                return (1, str(v.NAME))

        self.provider_dropdown = Dropdown(AttrDict(
            [(format_provider(n, p), n)
              for n, p in sorted(
                      providers.PROVIDERS.items(),
                      key = providers_sort_key
              )]
        ) , label="Provider", default=default_provider, margin=1)

        urwid.connect_signal(
            self.provider_dropdown, "change",
            lambda w, b, v: self._emit("provider_change", v)
        )

        self.columns = urwid.Columns([
            # ('weight', 1, urwid.Padding(urwid.Edit("foo"))),
            ('weight', 1, self.provider_dropdown),
            (1, urwid.Divider("|")),

        ])
        self.filler = urwid.Filler(self.columns)
        super(MainToolbar, self).__init__(self.filler)

    def cycle_provider(self, step=1):

        self.provider_dropdown.cycle(step)

    @property
    def provider(self):
        return (self.provider_dropdown.selected_label)


class BrowserView(BaseView):

    def __init__(self, provider):

        self.provider = provider
        self.toolbar = MainToolbar(self.provider.IDENTIFIER)
        urwid.connect_signal(
            self.toolbar, "provider_change",
            lambda w, p: self.set_provider(p)
        )

        self.browser_view_placeholder = urwid.WidgetPlaceholder(
            urwid.Filler(urwid.Text(""))
        )

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, urwid.Filler(urwid.Divider("-"))),
            ('weight', 1, self.browser_view_placeholder),
        ])
        super().__init__(self.pile)

    def set_provider(self, provider):

        self.provider.deactivate()
        self.provider = providers.get(provider)
        self.browser_view_placeholder.original_widget = self.provider.view
        if self.provider.config_is_valid:
            self.pile.focus_position = 2
        else:
            self.pile.focus_position = 0
        self.provider.activate()

    def activate(self):
        self.set_provider(self.provider.IDENTIFIER)

    def keypress(self, size, key):

        if key in ["meta up", "meta down"]:
            self.toolbar.cycle_provider(-1 if key == "meta up" else 1)

        else:
            return super().keypress(size, key)


class TasksDataTable(DataTable):

    columns = [
        DataTableColumn("pid", width=6),
        DataTableColumn("started", width=20, format_fn = utils.format_datetime),
        DataTableColumn("elapsed",  width=14, format_fn = utils.format_timedelta),
        DataTableColumn("provider", width=18),
        DataTableColumn("title", width=("weight", 1)),
        DataTableColumn(
            "locator", width=40,
            # format_fn = lambda s: s[:39] + u"\u2026" if len(s) >= 40 else s)
            format_fn = functools.partial(utils.format_str_truncated, 40)
        )
    ]

    @classmethod
    def filter_task(cls, t):
        return True


    def query(self, *args, **kwargs):
        # logger.info(f"query: {state.task_manager.active}")
        return [ t for t in state.task_manager.active if self.filter_task(t) ]

    def keypress(self, size, key):
        if key == "ctrl r":
            self.refresh()
        else:
            return super().keypress(size, key)


class PlayingDataTable(TasksDataTable):

    @classmethod
    def filter_task(cls, t):
        return t.action == "play"

class DownloadsDataTable(TasksDataTable):

    columns = PlayingDataTable.columns + [
        DataTableColumn(
            "dest", width=40,
            format_fn = functools.partial(utils.format_str_truncated, 40)
        ),
        DataTableColumn(
            "pct", width=5, format_record=True,
            format_fn = lambda r: f"{r.program.progress.get('pct', '').split('.')[0]}%"
        ),
        DataTableColumn(
            "rate", width=5, format_record=True,
            format_fn = lambda r: f"{r.program.progress.get('rate')}%"
        ),
    ]

    @classmethod
    def filter_task(cls, t):
        return t.action == "download"


class TasksView(BaseView):

    def __init__(self):

        self.playing = PlayingDataTable()
        self.downloads = DownloadsDataTable()
        self.pile = urwid.Pile([
            (1, urwid.Filler(urwid.Text("Playing"))),
            ("weight", 1, self.playing),
            (1, urwid.Filler(urwid.Text("Downloading"))),
            ("weight", 1, self.downloads)
        ])
        super().__init__(self.pile)

    def refresh(self):
        self.playing.refresh()
        self.downloads.refresh()


def run_gui(provider, **kwargs):

    log_file = os.path.join(config.CONFIG_DIR, f"{PACKAGE_NAME}.log")
    state.asyncio_loop = asyncio.get_event_loop()
    state.task_manager = tasks.TaskManager()
    state.task_manager_task = state.asyncio_loop.create_task(state.task_manager.start())

    ulh = UrwidLoggingHandler()
    setup_logging(options.verbose - options.quiet,
                  handlers=[logging.FileHandler(log_file), ulh],
                  quiet_stdout=True)

    entries = {}
    for (n, f, b) in  [
            ("unread", "white", "black"),
    ]:
        entries[n] = PaletteEntry(
            name=n,
            mono="white",
            foreground=f,
            background=b,
            foreground_high=f,
            background_high=b
        )

    entries.update(DataTable.get_palette_entries(user_entries=entries))
    entries.update(Dropdown.get_palette_entries())
    entries.update(ScrollingListBox.get_palette_entries())
    entries.update(TabView.get_palette_entries())
    # raise Exception(entries)
    palette = Palette("default", **entries)
    state.screen = urwid.raw_display.Screen()
    state.screen.set_terminal_properties(256)

    state.browser_view = BrowserView(provider)
    state.tasks_view = TasksView()

    state.views = [
        Tab("Browser", state.browser_view, locked=True),
        Tab("Tasks", state.tasks_view, locked=True)
    ]

    state.main_view = BaseTabView(state.views)

    log_console = widgets.ConsoleWindow()
    # log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    pile = urwid.Pile([
        ("weight", 5, urwid.LineBox(state.main_view)),
        ("weight", 1, urwid.LineBox(log_console))
    ])

    def global_input(key):
        if key in ('q', 'Q'):
            quit_app()
        else:
            return False


    state.loop = urwid.MainLoop(
        pile,
        palette,
        screen=state.screen,
        event_loop=urwid.AsyncioEventLoop(loop=state.asyncio_loop),
        unhandled_input=global_input,
        pop_ups=True
    )
    ulh.connect(state.loop.watch_pipe(log_console.log_message))
    if options.verbose:
        logger.setLevel(logging.DEBUG)

    def activate_view(loop, user_data):
        state.browser_view.activate()

    # tasks.start_task_manager()

    state.loop.set_alarm_in(0, activate_view)
    state.loop.run()

def run_cli(provider, selection, **kwargs):

    # provider.play(selection)
    provider.play(selection, **kwargs)
    while True:
        state.procs = [p for p in state.procs if p.poll() is None]
        if not len(state.procs):
            break
        time.sleep(0.25)

def main():

    global options
    global logger

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    init_parser = argparse.ArgumentParser()
    init_parser.add_argument("-p", "--profile", help="use alternate config profile")
    options, args = init_parser.parse_known_args()

    config.load(merge_default=True)
    if options.profile:
        config.settings.set_profile(options.profile)
    player.Player.load()

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("spec", metavar="SPECIFIER",
                        help="media specifier", nargs="?")
    options, args = parser.parse_known_args(args)

    state.options = AttrDict(vars(options))

    logger = logging.getLogger(config.PACKAGE_NAME)

    providers.load()

    sh = logging.StreamHandler()
    setup_logging(options.verbose - options.quiet,
                  quiet_stdout=True)
    logger.debug(f"{PACKAGE_NAME} starting")

    model.init()

    with db_session(optimistic=False):
        model.MediaFeed.purge_all(
            min_items = config.settings.profile.cache.min_items,
            max_items = config.settings.profile.cache.max_items,
            max_age = config.settings.profile.cache.max_age
        )

    spec = None
    provider, selection, opts = providers.parse_spec(options.spec)

    if selection:
        run_cli(provider, selection, **opts)
    else:
        run_gui(provider, **opts)


if __name__ == "__main__":
    main()
