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
import signal

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
from aiohttp.web import Application, AppRunner, TCPSite
from aiohttp_json_rpc import JsonRpc

from .state import *
from .widgets import *
from .browser import FileBrowser
from .providers.base import SynchronizedPlayerMixin

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

class UrwidLoggingHandler(logging.Handler):

    pipe = None

    def connect(self, pipe):
        self.pipe = pipe

    def emit(self, rec):

        if not self.pipe:
            return
        msg = self.format(rec)
        (ignore, ready, ignore) = select.select([], [self.pipe], [], 0)
        if self.pipe in ready:
            msg = utils.format_str_truncated(511, msg, encoding="utf-8") + "\n"
            os.write(self.pipe, msg.encode("utf-8"))


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


class ListingsView(StreamglobView):

    def __init__(self, provider):

        self.provider = provider
        self.toolbar = MainToolbar(self.provider.IDENTIFIER)
        urwid.connect_signal(
            self.toolbar, "provider_change",
            lambda w, p: self.set_provider(p)
        )

        def profile_change(p):
            config.settings.toggle_profile(p)
            player.Player.load()

        urwid.connect_signal(
            self.toolbar, "profile_change",
            lambda w, p: profile_change(p)
        )

        self.listings_view_placeholder = urwid.WidgetPlaceholder(
            urwid.Filler(urwid.Text(""))
        )

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, urwid.Filler(urwid.Divider("-"))),
            ('weight', 1, self.listings_view_placeholder),
        ])
        super().__init__(self.pile)

    def set_provider(self, provider):

        self.provider.deactivate()
        self.provider = providers.get(provider)
        self.listings_view_placeholder.original_widget = self.provider.view
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

class Dummy(object):
    def __init__(self, data):
        self.data = data

@keymapped()
class FilesView(SynchronizedPlayerMixin, StreamglobView):

    signals = ["requery"]

    KEYMAP = {
        "meta p": "play_all"
    }

    def __init__(self):

        self.browser = FileBrowser(config.settings.profile.get_path("output.path"), ignore_files=False)
        self.pile  = urwid.Pile([
            ('weight', 1, self.browser),
        ])
        super().__init__(self.pile)
        # self._emit("requery", self)

    @property
    def play_items(self):
        return [
            AttrDict(
                title = "foo",
                url = self.browser.selection
            )
        ]

    # def reset(self):
    #     super().reset()

    # @property
    # def provider(self):
    #     return AttrDict(
    #         IDENTIFER="foo",
    #         NAME="foo",
    #         feed=AttrDict(name="foo", locator="bar"),
    #         new_listing = lambda **kwargs: AttrDict(**kwargs),
    #         new_media_source = lambda **kwargs: AttrDict(**kwargs),
    #         status="foo"
    #     )

    # @property
    # def new_listing(self, **kwargs):
    #     return AttrDict(**kwargs)
    #
    def __iter__(self):
        return iter(self.browser.selection)

    # def __iter__(self):
    #     return iter([
    #         Dummy(
    #             AttrDict(
    #                 media_listing_id=0,
    #                 title="foo",
    #                 created=datetime.now(),
    #                 feed=AttrDict(name="foo", locator="bar"),
    #                 # locator=self.browser.selection,
    #                 sources=[
    #                     AttrDict(
    #                         locator=self.browser.selection,
    #                         is_bad = False
    #                     )
    #                 ]
    #             )
    #         )
    #     ])



class TasksDataTable(BaseDataTable):

    index = "task_id"
    empty_message = None

    COLUMN_DEFS = AttrDict([
        (c.name, c)
        for c in [
                # DataTableColumn("action", width=8),
                DataTableColumn("program", width=16, format_fn = lambda p: p.result().cmd if p and p.done() else ""),
                DataTableColumn("started", width=20, format_fn = utils.format_datetime),
                DataTableColumn("elapsed",  width=14, align="right",
                                format_fn = utils.format_timedelta),
                DataTableColumn("provider", width=12),
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
                    format_fn = lambda l: f"{str(l[0]) if len(l) == 1 else '[%s]' %(len(l))}",
                    truncate=True
                ),
                DataTableColumn(
                    "dest", width=20,
                    format_fn = utils.strip_emoji,
                    # format_fn = functools.partial(utils.format_str_truncated, 40),
                    truncate=True
                ),
                DataTableColumn(
                    "size", width=8, align="right",
                    value = lambda t, r: r.data.program.result().progress.size_total,
                    format_fn = lambda v: v if v else "?"
                    # value = foo,
                ),
                DataTableColumn(
                    "size/total", width=16, align="right",
                    value = lambda t, r: (
                        r.data.program.result().progress.size_downloaded,
                        r.data.program.result().progress.size_total
                    ),
                    format_fn = lambda v: (f"{v[0] or '?'}/{v[1] or '?'}") if v else ""
                    # value = foo,
                ),
                DataTableColumn(
                    "pct", width=5, align="right",
                    # format_fn = lambda r: f"{r.data.program.progress.get('pct', '').split('.')[0]}%"
                    value = lambda t, r: r.data.program.result().progress.percent_downloaded,
                    format_fn = lambda v: f"{round(v, 1)}%" if v else "?",
                    # value = foo,
                ),
                DataTableColumn(
                    "rate", width=10, align="right",
                    value = lambda t, r: r.data.program.result().progress.transfer_rate,
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

class PostprocessingDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.postprocessing ]


class CompletedDownloadsDataTable(TasksDataTable):

    COLUMNS = ["provider", "program", "sources", "title",
               "started", "elapsed", "size", "dest"]

    def query(self, *args, **kwargs):
        return [ t for t in state.task_manager.done ]

class TasksView(StreamglobView):

    def __init__(self):

        self.playing = PlayingDataTable()
        self.pending = PendingDataTable()
        self.active_downloads = ActiveDownloadsDataTable()
        self.postprocessing_downloads = PostprocessingDownloadsDataTable()
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
            (1, urwid.Filler(urwid.Text("Postprocessing Downloads"))),
            ("weight", 1, self.postprocessing_downloads),
            (1, urwid.Filler(urwid.Text("Completed Downloads"))),
            ("weight", 1, self.completed_downloads)
        ])
        super().__init__(self.pile)

    def refresh(self):
        self.playing.refresh()
        self.pending.refresh()
        self.active_downloads.refresh()
        self.postprocessing_downloads.refresh()
        self.completed_downloads.refresh()

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

    state.views = [
        Tab("Files", state.files_view, locked=True),
        Tab("Listings", state.listings_view, locked=True),
        # Tab("Tasks", state.tasks_view, locked=True)
    ]

    state.main_view = BaseTabView(state.views)

    pile = urwid.Pile([
        ("weight", 5, urwid.LineBox(state.main_view)),
    ])

    set_stdout_level(logging.CRITICAL)
    log_console = ConsoleWindow()

    ulh = UrwidLoggingHandler()

    add_log_handler(ulh)
    pile.contents.append(
        (urwid.LineBox(log_console), pile.options("weight", 2))
    )


    def global_input(key):
        if key in ('q', 'Q'):
            state.listings_view.quit_app()
        elif key == "meta C":
            reload_config()
        else:
            return False

    state.loop = urwid.MainLoop(
        pile,
        state.palette,
        screen=state.screen,
        event_loop = urwid.AsyncioEventLoop(loop=state.event_loop),
        unhandled_input=global_input,
        pop_ups=True
    )

    ulh.connect(state.loop.watch_pipe(log_console.log_message))

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


def run_cli(action, provider, selection, **kwargs):

    try:
        method = getattr(provider, action)
    except AttributeError:
        raise Exception(f"unknown action: {action}")

    try:
        task = method(
            selection,
            progress=False,
            stdout=sys.stdout, stderr=sys.stderr, **kwargs
        )
        loop_result = state.event_loop.run_until_complete(task.result)
        result = task.result.result()
        if isinstance(result, Exception):
            logger.exception(traceback.print_exception(type(result), result, result.__traceback__))
        if task.proc.done():
            proc = task.proc.result()
        else:
            proc = None
    except KeyboardInterrupt:
        logger.info("Exiting on keyboard interrupt")
    if proc:
        rc = proc.returncode
    else:
        rc = -1
    return rc


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
