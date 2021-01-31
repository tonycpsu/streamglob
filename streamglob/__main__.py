import logging
# logger = logging.getLogger(__name__)
import sys
import os
import traceback
from datetime import datetime, timedelta
from collections import namedtuple
import argparse
import subprocess
import select
import time
import re
import asyncio
import functools
import itertools
import signal
import inspect

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *
from panwid.dialog import *
from pony.orm import db_session
from tonyc_utils.logging import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
from aiohttp.web import Application, AppRunner, TCPSite
from aiohttp_json_rpc import JsonRpc

from .state import *
from .widgets import *
from .views import *

from . import config
from . import model
from . import utils
from . import session
from . import providers
from . import player
from . import tasks
from .exceptions import *

urwid.AsyncioEventLoop._idle_emulation_delay = 1/20

PACKAGE_NAME=__name__.split('.')[0]

def load_palette():

    state.palette_entries = {}
    # FIXME: move to provider config
    for (n, f, b) in  [
            ("unread", "white", "black"),
    ]:
        state.palette_entries[n] = PaletteEntry(
            name=n,
            mono="white",
            foreground=f,
            background=b,
            foreground_high=f,
            background_high=b
        )

    for k, v in config.settings.profile.attributes.items():
        state.palette_entries[k] = PaletteEntry.from_config(v)

    for pname, p in providers.PROVIDERS.items():
        if not hasattr(p.config, "attributes"):
            continue
        for gname, group in p.config.attributes.items():
            for k, v in group.items():
                ename = f"{pname}.{gname}.{k}"
                state.palette_entries[ename] = PaletteEntry.from_config(v)

    state.palette_entries.update(DataTable.get_palette_entries(
        user_entries=state.palette_entries
    ))
    state.palette_entries.update(Dropdown.get_palette_entries())
    state.palette_entries.update(
        ScrollingListBox.get_palette_entries()
    )
    state.palette_entries.update(TabView.get_palette_entries())

    # raise Exception(state.palette_entries)
    return Palette("default", **state.palette_entries)


def reload_config():

    logger.info("reload config")
    profiles = config.settings.profile_names
    config.load(options.config_dir, merge_default=True)
    providers.load_config()
    for p in profiles:
        config.settings.include_profile(p)

    for k in list(state.screen._palette.keys()):
        del state.screen._palette[k]
    state.palette = load_palette()
    state.screen.register_palette(state.palette)


intersperse = lambda e,l: sum([[x, e] for x in l],[])[:-1]

class TiledView(urwid.WidgetWrap):

    def __init__(self, widgets, weight=1, dividers=False):

        self.widgets = widgets
        self.weight = weight
        self.dividers = dividers
        self.last_focused_index = None

        self.columns = urwid.Columns([
            ("weight", self.weight[i][0], urwid.Pile([
                ("weight", self.weight[i][1][j]
                 if isinstance(self.weight, list)
                 else self.weight, w)
                for j, w in enumerate(col)
            ]))
            for i, col in enumerate(zip(*self.widgets))
        ])
        for i in range(len(self.columns.contents)):
            pile = self.columns.contents[i][0]
            pile.contents = intersperse(
                ( urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT HORIZONTAL}"),
                  pile.options("given", 1)
                 ),
                pile.contents
            )

        if self.dividers:
            self.columns.contents = intersperse(
                ( urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT VERTICAL}"),
                  self.columns.options("given", 1)
                 ),
                self.columns.contents
            )

        super().__init__(self.columns)
        self.focus_paths = [
            [x*2 if self.dividers else x, y*2 if self.dividers else y]
            for y in range(len(self.widgets))
            for x in range(len(self.widgets[y]))
        ]

        self.set_focus(1, 0)

    def __getitem__(self, index):
        return self.widgets[index//len(self.widgets)][index%len(self.widgets)]

    def __len__(self):
        return len(self.widgets)*(len(self.widgets[0]))

    def cycle_focus(self, step=1):

        if step > 0:
            c = itertools.cycle(range(0, len(self), step))
        else:
            c = itertools.cycle(range(len(self)-1, 0, step))

        while next(c) != self.focused_index:
            pass

        indexes = [ next(c) for i in range(len(self)) ]

        for i in indexes:
            if not self[i].selectable():
                continue
            break
        self._w.set_focus_path(self.focus_paths[i])
        self.focused_widget.on_view_activate()
        self.last_focused_index = self.focused_index

    @property
    def focused_index(self):
        return next(
            i for i, p in enumerate(self.focus_paths)
            if p == self._w.get_focus_path()
        )

    @property
    def focused_widget(self):
        return self[self.focused_index]
        # return self[self.focused_pane]

    def set_focus(self, x, y):
        self._w.set_focus_path(
            [
                x*2 if self.dividers else x,
                y*2 if self.dividers else y
            ]
        )

    def keypress(self, size, key):

        key = super().keypress(size, key)
        try:
            if self.last_focused_index != self.focused_index and hasattr(self.focused_widget, "on_view_activate"):
                self.focused_widget.on_view_activate()
            self.last_focused_index = self.focused_index
        except StopIteration:
            pass

        if key == "tab":
            self.cycle_focus()
        elif key == "shift tab":
            self.cycle_focus(-1)
        else:
            return key

    def mouse_event(self, size, event, button, col, row, focus):
        logger.info("mouse_event")
        try:
            if self.last_focused_index != self.focused_index and hasattr(self.focused_widget, "on_view_activate"):
                self.focused_widget.on_view_activate()
            self.last_focused_index = self.focused_index
        except StopIteration:
            pass

        return super().mouse_event(size, event, button, col, row, focus)

    def get_column(self, y):
        return self.columns.contents[y][0]

    def get_widget(self, x, y):
        return self.columns.contents[y][0].contents[x][0]



def run_gui(action, provider, **kwargs):

    state.palette = load_palette()
    state.screen = urwid.raw_display.Screen()

    def get_colors():
        if config.settings.profile.colors == "true":
            return 2**24
        elif isinstance(config.settings.profile.colors, int):
            return config.settings.profile.colors
        else:
            return 16

    state.screen.set_terminal_properties(get_colors())

    state.listings_view = ListingsView(provider)
    state.files_view = FilesView()
    state.tasks_view = TasksView()

    set_stdout_level(logging.CRITICAL)

    state.log_buffer = LogBuffer()
    log_console = LogViewer(state.event_loop, state.log_buffer)

    add_log_handler(state.log_buffer)

    class VideoPlaceholder(urwid.WidgetWrap):

        def __init__(self):
            super().__init__(urwid.Filler(urwid.Text("")))

        def selectable(self):
            return False

    state.main_view = TiledView([
        [ state.tasks_view, state.listings_view ],
        [ state.files_view, VideoPlaceholder() ]
    ], weight=[
        [ 1, [2, 3] ],
        [ 2, [1, 2] ]
    ], dividers=True)

    # raise Exception(state.main_view.get_widget(0, 0))

    if options.verbose:
        left_column = state.main_view.get_column(0)
        left_column.contents.append(
            (urwid.LineBox(log_console), left_column.options("weight", 1))
            # (log_console, pile.options("given", 20))
        )


    def global_input(key):
        if key in ('q', 'Q'):
            state.listings_view.quit_app()
        elif key == "meta C":
            reload_config()
        else:
            return False

    state.loop = urwid.MainLoop(
        state.main_view,
        state.palette,
        screen=state.screen,
        event_loop = urwid.AsyncioEventLoop(loop=state.event_loop),
        unhandled_input=global_input,
        pop_ups=True
    )

    if options.verbose:
        logger.setLevel(logging.DEBUG)

    def activate_view(loop, user_data):
        state.listings_view.activate()


    def start_server(loop, user_data):

        app = Application()

        async def start_server_async():
            runner = AppRunner(app)
            await runner.setup()
            site = TCPSite(runner, 'localhost', 8080)
            try:
                await site.start()
            except OSError as e:
                logger.warning(e)

        rpc = JsonRpc()

        methods = []
        for pname, p in providers.PROVIDERS.items():
            methods += [
                (pname, func)
                for name, func in p.RPC_METHODS
            ]

        rpc.add_methods(*methods)
        app.router.add_route("*", "/", rpc.handle_request)
        asyncio.create_task(start_server_async())

    state.loop.set_alarm_in(0, start_server)
    state.loop.set_alarm_in(0, activate_view)
    state.loop.run()


async def run_tasks(tasks):

    async for task in tasks:
        loop_result = await task.result
        result = task.result.result()
        if isinstance(result, Exception):
            logger.exception("".join(traceback.format_exception(type(result), result, result.__traceback__)))
        if task.proc.done():
            proc = task.proc.result()
        else:
            proc = None


def run_cli(action, provider, selection, **kwargs):

    try:
        method = getattr(provider, action)
    except AttributeError:
        raise Exception(f"unknown action: {action}")

    try:
        if inspect.isasyncgenfunction(method):
            tasks = method(
                selection,
                progress=False,
                stdout=sys.stdout, stderr=sys.stderr, **kwargs
            )
        else:
            tasks = [
                method(
                    selection,
                    progress=False,
                    stdout=sys.stdout, stderr=sys.stderr, **kwargs
                )
            ]

        state.event_loop.run_until_complete(run_tasks(tasks))

    except KeyboardInterrupt:
        logger.info("Exiting on keyboard interrupt")


def main():

    global options
    global logger

    today = datetime.now(pytz.timezone('US/Eastern')).date()

    init_parser = argparse.ArgumentParser()
    init_parser.add_argument("-c", "--config-dir", help="use alternate config directory")
    init_parser.add_argument("-p", "--profile", help="use alternate config profile")
    options, args = init_parser.parse_known_args()

    config.load(options.config_dir, merge_default=True)
    if options.profile:
        for p in options.profile.split(","):
            config.settings.include_profile(p)
    player.Player.load()

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("uri", metavar="URI",
                        help="media URI", nargs="?")

    options, args = parser.parse_known_args(args)

    state.options = AttrDict(vars(options))

    logging.captureWarnings(True)
    logger = logging.getLogger()
    sh = logging.StreamHandler()
    state.logger = setup_logging(options.verbose - options.quiet, quiet_stdout=False)

    providers.load()
    model.init()
    providers.load_config()

    spec = None

    logger.debug(f"{PACKAGE_NAME} starting")
    state.task_manager = tasks.TaskManager()

    state.task_manager_task = state.event_loop.create_task(state.task_manager.start())

    log_file = os.path.join(config.settings.CONFIG_DIR, f"{PACKAGE_NAME}.log")
    fh = logging.FileHandler(log_file)
    add_log_handler(fh)
    logging.getLogger("panwid.keymap").setLevel(logging.INFO)
    logging.getLogger("panwid.datatable").setLevel(logging.INFO)
    logging.getLogger("aio_mpv_jsonipc").setLevel(logging.INFO)

    action, provider, selection, opts = providers.parse_uri(options.uri)

    if selection:
        rc = run_cli(action, provider, selection, **opts)
    else:
        rc = run_gui(action, provider, **opts)
    return rc

if __name__ == "__main__":
    main()
