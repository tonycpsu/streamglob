import logging
logger = logging.getLogger(__name__)

import functools
import re

import urwid
from panwid.datatable import *
from panwid.keymap import *
from pony.orm import *
from googletransx import Translator

from . import config
from .. import utils
from ..exceptions import *
from ..widgets import *
from ..state import *
from .. import model

class FilterToolbar(urwid.WidgetWrap):

    signals = ["filter_change"]

    def __init__(self, filters):

        self.filters = filters
        self.columns = urwid.Columns([], dividechars=1)
        for n, f in self.filters.items():
            self.columns.contents += [
                (f.placeholder, self.columns.options("weight", 1)),
            ]

        self.filler = urwid.Filler(urwid.Padding(self.columns))
        super(FilterToolbar, self).__init__(urwid.BoxAdapter(self.filler, 1))

    def cycle_filter(self, index, step=1):
        if index >= len(self.filters):
            return
        list(self.filters.values())[index].cycle(step)

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)

    def get_pref_col(self, size):
        return 0

@keymapped()
class ProviderDataTable(BaseDataTable):

    ui_sort = False

    signals = ["cycle_filter"]

    KEYMAP = {
        " ": "preview_selection",
        "p": "play_selection",
        "l": "download_selection"
    }

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        self.translate = False
        self.translate_src = None
        self._translator = None
        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    @property
    def columns(self):
        return [
            DataTableColumn(k, **v if v else {})
            for k, v in self.provider.ATTRIBUTES.items()
        ]

    @property
    def limit(self):
        return self.provider.limit

    def query(self, *args, **kwargs):
        try:
            for l in self.listings(*args, **kwargs):
                # FIXME
                # l._provider = self.provider

                # self.provider.on_new_listing(l)
                yield(l)

        except SGException as e:
            logger.exception(e)
            return []

    def listings(self, *args, **kwargs):
        yield from self.provider.listings(*args, **kwargs)

    @property
    def translator(self):
        if not self._translator:
            self._translator = Translator()
        return self._translator

    def toggle_translation(self):
        self.translate = not self.translate
        if self.translate:
            texts = [
                (row.index, row.get("title"))
                for row in self
                if not isinstance(row.get("_title_translated"), str)
                and isinstance(row.get("title"), str)
                and len(row.get("title"))
            ]
            translations = self.translator.translate(
                [ t[1] for t in texts ],
                src=self.translate_src or "auto",
                dest=config.settings.profile.translate
            )
            for (i, _), t in zip(texts, translations):
                self.df.set(i, "_title_translated", utils.strip_emoji(t.text))
        self.invalidate_rows(
            [ row.index for row in self if row.get("_title_translated") ]
        )

    def keypress(self, size, key):

        key = super().keypress(size, key)
        # if key == "ctrl r":
        #     self.provider.reset()
        if key == "ctrl t":
            self.toggle_translation()
        else:
            return key

    async def play_selection(self):
        raise NotImplementedError

    async def download_selection(self):

        row_num = self.focus_position
        listing = self[row_num].data_source
        index = self.playlist_position

        # FIXME inner_focus comes from MultiSourceListingMixin
        async for task in self.provider.download(listing, index = self.inner_focus or 0):
            pass

    def reset(self, *args, **kwargs):
        self.translate = False
        super().reset(*args, **kwargs)

    @property
    def playlist_title(self):
        return self.provider.playlist_title

    def decorate(self, row, column, value):

        if column.name == "title":

            if self.translate and row.get("_title_translated"):
                value = row.get("_title_translated")

            if self.provider.highlight_map:
                markup = [
                    ( next(v for k, v in self.provider.highlight_map.items()
                           if k.search(x)), x)
                    if self.provider.highlight_re.search(x)
                    else x for x in self.provider.highlight_re.split(value) if x
                ]
                if len(markup):
                    value = urwid.Text(markup)

        return super().decorate(row, column, value)

    def on_deactivate(self):
        state.event_loop.create_task(state.task_manager.preview(None, self))

    def apply_search_query(self, query):
        self.apply_filters([lambda row: query in row["title"]])

    def clear_search_query(self):
        self.reset_filters()
