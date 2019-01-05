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

import urwid
import urwid.raw_display
from urwid_utils.palette import *
from panwid.datatable import *
from panwid.listbox import ScrollingListBox
from panwid.dropdown import *
from panwid.dialog import *
from tonyc_utils.logging import *

import pytz
from orderedattrdict import AttrDict
import requests
import dateutil.parser
import yaml
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader

from .state import *
from .state import memo
from . import config
from . import model
from . import widgets
from . import utils
from . import session
from . import providers
from . import player
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
        ])
        self.filler = urwid.Filler(self.columns)
        super(MainToolbar, self).__init__(self.filler)

    @property
    def provider(self):
        return (self.provider_dropdown.selected_label)



class MainView(BaseView):

    def __init__(self, provider):

        self.provider = provider# or providers.DEFAULT_PROVIDER
        self.toolbar = MainToolbar(self.provider.IDENTIFIER)
        urwid.connect_signal(
            self.toolbar, "provider_change",
            lambda w, p: self.set_provider(p)
        )

        self.provider_view_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))

        self.pile  = urwid.Pile([
            (1, self.toolbar),
            (1, urwid.Filler(urwid.Divider("-"))),
            ('weight', 1, self.provider_view_placeholder),
        ])
        self.pile.focus_position = 2
        super(MainView, self).__init__(self.pile)
        self.set_provider(self.provider.IDENTIFIER)

    def set_provider(self, provider):

        logger.warning(f"set provider: {provider}")
        self.provider = providers.get(provider)
        self.provider_view_placeholder.original_widget = self.provider.view

def run_gui(provider, **kwargs):

    log_file = os.path.join(config.CONFIG_DIR, f"{PACKAGE_NAME}.log")

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
    # raise Exception(entries)
    palette = Palette("default", **entries)
    screen = urwid.raw_display.Screen()
    screen.set_terminal_properties(256)

    view = MainView(provider)

    log_console = widgets.ConsoleWindow()
    # log_box = urwid.BoxAdapter(urwid.LineBox(log_console), 10)
    pile = urwid.Pile([
        ("weight", 5, urwid.LineBox(view)),
        ("weight", 1, urwid.LineBox(log_console))
    ])

    def global_input(key):
        if key in ('q', 'Q'):
            raise urwid.ExitMainLoop()
        else:
            return False

    state.asyncio_loop = asyncio.get_event_loop()

    state.loop = urwid.MainLoop(
        pile,
        palette,
        screen=screen,
        event_loop=urwid.AsyncioEventLoop(loop=state.asyncio_loop),
        unhandled_input=global_input,
        pop_ups=True
    )
    ulh.connect(state.loop.watch_pipe(log_console.log_message))
    if options.verbose:
        logger.setLevel(logging.DEBUG)

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

    config.settings.load()
    player.Player.load()

    if options.profile:
        config.settings.set_profile(options.profile)

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    parser.add_argument("spec", metavar="SPECIFIER",
                        help="media specifier", nargs="?")
    options, args = parser.parse_known_args()

    state.options = AttrDict(vars(options))

    logger = logging.getLogger(config.PACKAGE_NAME)

    sh = logging.StreamHandler()
    setup_logging(options.verbose - options.quiet,
                  quiet_stdout=True)
    logger.debug(f"{PACKAGE_NAME} starting")

    providers.load()
    model.init()

    model.Feed.purge_all(
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
