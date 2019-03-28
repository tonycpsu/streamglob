import logging
# logger = logging.getLogger(__name__)
import sys
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
from .widgets import *

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

class PatchedAsyncioEventLoop(urwid.AsyncioEventLoop):
    def _exception_handler(self, loop, context):
        exc = context.get('exception')
        if exc:
            loop.stop()
            if not isinstance(exc, urwid.ExitMainLoop):
                # Store the exc_info so we can re-raise after the loop stops
                import sys
                self._exc_info = sys.exc_info()
                if self._exc_info == (None, None, None):
                    self._exc_info = (type(exc), exc, exc.__traceback__)
        else:
            loop.default_exception_handler(context)


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

    signals = ["provider_change", "profile_change"]
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

        self.provider_dropdown = BaseDropdown(AttrDict(
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

        self.profile_dropdown = BaseDropdown(
            AttrDict(
                [ (k, k) for k in config.settings.profiles.keys()]
            ),
            label="Profile",
            default=config.settings.profile_name, margin=1
        )

        urwid.connect_signal(
            self.profile_dropdown, "change",
            lambda w, b, v: self._emit("profile_change", v)
        )

        self.max_concurrent_tasks_widget = providers.filters.IntegerTextFilterWidget(
            default=config.settings.tasks.max,
                minimum=1
        )

        def set_max_concurrent_tasks(v):
            config.settings.tasks.max = int(v)

        self.max_concurrent_tasks_widget.connect("changed", set_max_concurrent_tasks)
        # urwid.connect_signal(
        #     self.max_concurrent_tasks_widget,
        #     "change",
        #     set_max_concurrent_tasks
        # )

        self.columns = urwid.Columns([
            # ('weight', 1, urwid.Padding(urwid.Edit("foo"))),
            (self.provider_dropdown.width, self.provider_dropdown),
            ("pack", urwid.Text(("Downloads"))),
            (5, self.max_concurrent_tasks_widget),
            ("weight", 1, urwid.Padding(urwid.Text(""))),
            # (1, urwid.Divider(u"\N{BOX DRAWINGS LIGHT VERTICAL}")),
            (self.profile_dropdown.width, self.profile_dropdown),
        ], dividechars=3)
        # self.filler = urwid.Filler(self.columns)
        super(MainToolbar, self).__init__(urwid.Filler(self.columns))

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
        urwid.connect_signal(
            self.toolbar, "profile_change",
            lambda w, p: config.settings.set_profile(p)
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


class TasksDataTable(BaseDataTable):

    index = "task_id"
    empty_message = None

    COLUMN_DEFS = AttrDict([
        (c.name, c)
        for c in [
                # DataTableColumn("action", width=8),
                DataTableColumn("program", width=16, format_fn = lambda p: p.cmd if p else ""),
                DataTableColumn("started", width=20, format_fn = utils.format_datetime),
                DataTableColumn("elapsed",  width=14, align="right",
                                format_fn = utils.format_timedelta),
                DataTableColumn("provider", width=18),
                DataTableColumn(
                    "title", width=("weight", 3),
                    # FIXME: urwid miscalculates width of some unicode glyphs,
                    # which causes data table to raise an exception when rows
                    # are calculated with a different height than they render
                    # at.  See https://github.com/urwid/urwid/issues/225
                    # Workaround is to strip emoji
                    format_fn = utils.strip_emoji,
                    truncate=True
                ),
                DataTableColumn(
                    "sources", label="sources", width=("weight", 1), wrap="any",
                    format_fn = lambda l: f"{l[0].locator if len(l) == 1 else '[%s]' %(len(l))}",
                    truncate=True
                ),
                DataTableColumn(
                    "dest", width=40,
                    format_fn = utils.strip_emoji,
                    # format_fn = functools.partial(utils.format_str_truncated, 40),
                    truncate=True
                ),
                DataTableColumn(
                    "size", width=8, align="right",
                    value = lambda t, r: r.data.program.progress.size_total,
                    format_fn = lambda v: v if v else "?"
                    # value = foo,
                ),
                DataTableColumn(
                    "size/total", width=16, align="right",
                    value = lambda t, r: (
                        r.data.program.progress.size_downloaded,
                        r.data.program.progress.size_total
                    ),
                    format_fn = lambda v: (f"{v[0] or '?'}/{v[1] or '?'}") if v else ""
                    # value = foo,
                ),
                DataTableColumn(
                    "pct", width=5, align="right",
                    # format_fn = lambda r: f"{r.data.program.progress.get('pct', '').split('.')[0]}%"
                    value = lambda t, r: r.data.program.progress.percent_downloaded,
                    format_fn = lambda v: f"{round(v, 1)}%" if v else "?",
                    # value = foo,
                ),
                DataTableColumn(
                    "rate", width=10, align="right",
                    value = lambda t, r: r.data.program.progress.transfer_rate,
                    format_fn = lambda v: f"{v}/s" if v else "?"
                )
        ]
    ])

    COLUMNS = ["provider", "program", "sources", "title"]

    def detail_fn(self, data):
        return urwid.Columns([
            (4, urwid.Padding(urwid.Text(""))),
            ("weight", 1, urwid.Pile([
                (1, urwid.Filler(DataTableText(s.locator)))
                for s in data["sources"]]
            )
        )
        ])


    def __init__(self, *args, **kwargs):
        self.columns = [
            self.COLUMN_DEFS[n] for n in self.COLUMNS
        ]
        super().__init__(*args, **kwargs)

    @classmethod
    def filter_task(cls, t):
        return True

    def keypress(self, size, key):
        if key == "ctrl r":
            self.refresh()
        elif key == "ctrl k":
            logger.info(type(self.selection.data.program.progress.transfer_rate))
        elif key == ".":
            self.selection.toggle_details()
            # self.selection.data._details_open = not self.selection.data._details_open
        else:
            return super().keypress(size, key)

class PlayingDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title"]

    def query(self, *args, **kwargs):
        # return [ t for t in state.task_manager.playing ]
        for t in state.task_manager.playing:
            yield t

    def keypress(self, size, key):

        if key == "delete" and self.selection:
            self.selection.data.program.proc.terminate()
        else:
            return super().keypress(size, key)

class PendingDataTable(TasksDataTable):

    COLUMNS = ["provider", "sources", "title"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.to_download ]

    def keypress(self, size, key):
        if key == "delete" and self.selection:
            state.task_manager.to_download.remove_by_id(self.selection.data.task_id)
            del self[self.focus_position]
        else:
            return super().keypress(size, key)


class ActiveDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "size/total", "pct", "rate", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.active if isinstance(t, model.DownloadMediaTask) ]

    def keypress(self, size, key):

        if key == "delete" and self.selection:
            try:
                self.selection.data.program.proc.terminate()
            except ProcessLookupError:
                pass
            # state.task_manager.active.remove_by_id(self.selection.data.task_id)
        else:
            return super().keypress(size, key)

class CompletedDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "size", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.done ]

class TasksView(BaseView):

    def __init__(self):

        self.playing = PlayingDataTable()
        self.pending = PendingDataTable()
        self.active_downloads = ActiveDownloadsDataTable()
        self.completed_downloads = CompletedDownloadsDataTable()
        self.pile = urwid.Pile([
            urwid.Columns([
                ("weight", 1, urwid.Pile([
                    (1, urwid.Filler(urwid.Text("Playing"))),
                    ("weight", 1, self.playing),
                ])),
                ("weight", 1, urwid.Pile([
                    (1, urwid.Filler(urwid.Text("Pending"))),
                    ("weight", 1, self.pending),
                ]))
            ], dividechars=1),
            (1, urwid.Filler(urwid.Text("Active Downloads"))),
            ("weight", 1, self.active_downloads),
            (1, urwid.Filler(urwid.Text("Completed Downloads"))),
            ("weight", 1, self.completed_downloads)
        ])
        super().__init__(self.pile)

    def refresh(self):
        self.playing.refresh()
        self.pending.refresh()
        self.active_downloads.refresh()
        self.completed_downloads.refresh()

def load_palette():

    state.palette_entries = {}
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
    profile = config.settings.profile_name
    config.load(merge_default=True)
    providers.load_config()
    if profile:
        config.settings.set_profile(profile)
    for k in list(state.screen._palette.keys()):
        del state.screen._palette[k]
    state.palette = load_palette()
    state.screen.register_palette(state.palette)


def run_gui(action, provider, **kwargs):

    state.palette = load_palette()
    state.screen = urwid.raw_display.Screen()
    state.screen.set_terminal_properties(256)

    state.browser_view = BrowserView(provider)
    state.tasks_view = TasksView()
    # state.task_manager.connect("play_done", state.tasks_view.on_play_done)
    # state.task_manager.connect("download_done", state.tasks_view.on_download_done)

    state.views = [
        Tab("Browser", state.browser_view, locked=True),
        Tab("Tasks", state.tasks_view, locked=True)
    ]

    state.main_view = BaseTabView(state.views)

    # log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    pile = urwid.Pile([
        ("weight", 5, urwid.LineBox(state.main_view)),
    ])

    set_stdout_level(logging.CRITICAL)
    if options.debug_console:
        log_console = ConsoleWindow()

        ulh = UrwidLoggingHandler()

        add_log_handler(ulh)
        pile.contents.append(
            (urwid.LineBox(log_console), pile.options("weight", 1))
        )


    def global_input(key):
        if key in ('q', 'Q'):
            quit_app()
        elif key == "meta C":
            reload_config()
        else:
            return False

    state.loop = urwid.MainLoop(
        pile,
        state.palette,
        screen=state.screen,
        event_loop=PatchedAsyncioEventLoop(loop=state.asyncio_loop),
        unhandled_input=global_input,
        pop_ups=True
    )

    if options.debug_console:
        ulh.connect(state.loop.watch_pipe(log_console.log_message))

    if options.verbose:
        logger.setLevel(logging.DEBUG)

    def activate_view(loop, user_data):
        state.browser_view.activate()

    # tasks.start_task_manager()

    state.loop.set_alarm_in(0, activate_view)
    state.loop.run()

def run_cli(action, provider, selection, **kwargs):


    # raise Exception(selection)
    # provider.play(selection)
    sources, kwargs = provider.play_args(selection, **kwargs)

    if action == "play":
        task = provider.play(
            selection,
            no_task_manager=True,
            no_progress=True,
            stdout=sys.stdout, stderr=sys.stderr, **kwargs
        )
    elif action == "download":
        task = provider.download(
            selection,
            no_task_manager=True, no_progress=True,
            tdout=sys.stdout, stderr=sys.stderr, **kwargs
        )
    else:
        raise Exception(f"unknown action: {action}")
    # task = state.asyncio_loop.create_task(state.task_manager.join())
    state.asyncio_loop.run_until_complete(task)

    # while True:
    #     state.procs = [p for p in state.procs if p.poll() is None]
    #     if not len(state.procs):
    #         break
    #     time.sleep(0.25)

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
    parser.add_argument("-d", "--debug-console",
                        help="show logging console (disables task manager UI)",
                        action="store_true")
    parser.add_argument("spec", metavar="SPECIFIER",
                        help="media specifier", nargs="?")


    options, args = parser.parse_known_args(args)

    state.options = AttrDict(vars(options))

    logger = logging.getLogger()

    providers.load()

    model.init()

    with db_session(optimistic=False):
        model.MediaFeed.purge_all(
            min_items = config.settings.profile.cache.min_items,
            max_items = config.settings.profile.cache.max_items,
            max_age = config.settings.profile.cache.max_age
        )

    spec = None

    sh = logging.StreamHandler()
    state.logger = setup_logging(options.verbose - options.quiet, quiet_stdout=False)
    logger.debug(f"{PACKAGE_NAME} starting")
    action, provider, selection, opts = providers.parse_spec(options.spec)
    state.asyncio_loop = asyncio.get_event_loop()
    state.task_manager = tasks.TaskManager()

    state.task_manager_task = state.asyncio_loop.create_task(state.task_manager.start())

    log_file = os.path.join(config.CONFIG_DIR, f"{PACKAGE_NAME}.log")
    fh = logging.FileHandler(log_file)
    add_log_handler(fh)

    if selection:
        run_cli(action, provider, selection, **opts)
    else:
        run_gui(action, provider, **opts)


if __name__ == "__main__":
    main()
