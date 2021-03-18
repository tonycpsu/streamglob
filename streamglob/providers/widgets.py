import logging
logger = logging.getLogger(__name__)

import functools
import re

import urwid
from panwid.datatable import *
from panwid.keymap import *
from pony.orm import *
from pygoogletranslation import Translator

from . import config
from .. import utils
from ..exceptions import *
from ..widgets import *
from .filters import *
from ..state import *
from .. import model

class FilterToolbar(urwid.WidgetWrap):

    signals = ["filter_change"]

    def __init__(self, filters):

        self.filters = filters
        self.columns = urwid.Columns([], dividechars=1)
        for n, f in self.filters.items():
            self.columns.contents += [
                (f.placeholder, self.columns.options(*f.filter_sizing)),
            ]

        self.filler = urwid.Filler(urwid.Padding(self.columns))
        super(FilterToolbar, self).__init__(urwid.BoxAdapter(self.filler, 1))

    def cycle_filter(self, index, step=1):
        if index >= len(self.filters):
            return
        list(self.filters.values())[index].cycle(step)

    def focus_filter(self, name):
        try:
            target = next(
                i for i, f in enumerate(self.filters)
                if f == name
            )
        except StopIteration:
            raise RuntimeError(f"filter {name} not found")

        self.columns.focus_position = target
        state.loop.draw_screen()

    def keypress(self, size, key):
        return super(FilterToolbar, self).keypress(size, key)

    def get_pref_col(self, size):
        return 0

@keymapped()
class PlayListingMixin(object):

    KEYMAP = {
        "p": "play_selection",
    }

    @property
    def active_table(self):
        # subclasses can override to return an inner table
        return self

    @property
    def selected_listing(self):
        row_num = self.focus_position
        try:
            listing = self[row_num].data_source
        except IndexError:
            return None
        return listing

    @property
    def selected_source(self):
        return self.selected_listing.sources[0]

    async def play_selection(self):
        listing = self.selected_listing
        if not listing:
            return
        async for task in self.play(listing):
            pass

    async def play(self, listing, **kwargs):
        # sources, kwargs = self.extract_sources(listing, **kwargs)
        task = self.create_play_task(listing, **kwargs)
        yield state.task_manager.play(task)

    def create_play_task(self, listing, *args, **kwargs):
        return model.PlayMediaTask.attr_class(
            title=listing.title,
            sources=listing.sources,
            args=args,
            kwargs=kwargs
        )

@keymapped()
class DownloadListingMixin(object):

    KEYMAP = {
        "l": "download_selection"
    }

    @property
    def NAME(self):
        return "downloader"

    async def download(self, listing, index=None, no_task_manager=False, **kwargs):
        for task in self.create_download_tasks(listing, index=index, **kwargs):
            yield state.task_manager.download(task)

    async def download_selection(self):

        listing = self.selected_listing
        if not listing:
            return
        source = self.selected_source
        if not source:
            return
        async for task in self.download(listing, index=source.rank or 0):
            pass


    def create_download_tasks(self, listing, index=None, downloader_spec=None, **kwargs):

        sources, kwargs = self.extract_sources(listing, **kwargs)

        if not isinstance(sources, list):
            sources = [sources]

        if "num" not in kwargs:
            kwargs["num"] = len(sources)

        for i, source in enumerate(sources):

            if index is not None and index != i:
                continue
            try:
                filename = source.download_filename(**kwargs, listing=listing)
            except SGInvalidFilenameTemplate as e:
                logger.warning(f"filename template is invalid: {e}")
                raise
            downloader_spec = downloader_spec or source.download_helper
            task = model.DownloadMediaTask.attr_class(
                provider = self.NAME,
                title = utils.sanitize_filename(listing.title),
                sources = [source],
                listing = listing,
                dest = filename,
                args = (downloader_spec,),
                kwargs = dict(index=index, **kwargs),
                postprocessors = (self.config.get("postprocessors", None) or []).copy()
            )
            yield task


@keymapped()
class ProviderDataTable(PlayListingMixin, DownloadListingMixin, BaseDataTable):

    ui_sort = True
    query_sort = True

    signals = ["cycle_filter"]

    KEYMAP = {
        ".": "browse_selection",
        "ctrl o": "strip_emoji_selection",
        "ctrl t": "translate_selection",
        "meta O": "toggle_strip_emoji_all",
        "meta T": "toggle_translate_all"
    }

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        self.translate = self.provider.translate
        self.strip_emoji = self.provider.strip_emoji
        self._translator = None
        super(ProviderDataTable,  self).__init__(*args, **kwargs)


    @property
    def NAME(self):
        return self.provider.NAME

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

    @property
    def config(self):
        return self.provider.config


    def playlist_position(self):
        return self.focus_position

    def listings(self, *args, **kwargs):
        yield from self.provider.listings(*args, **kwargs)

    @property
    def translator(self):
        if not self._translator:
            self._translator = Translator(sleep=1)
        return self._translator

    def strip_emoji_selection(self):
        strip_emoji = self.strip_emoji
        index = getattr(self.selection.data_source, self.df.index_name)
        try:
            strip_emoji = not self.df.get(index, "_strip_emoji")
        except ValueError:
            strip_emoji = not strip_emoji
        self.df.set(index, "_strip_emoji", strip_emoji)
        self.invalidate_rows([index])

    def translate_selection(self):
        translate = self.translate
        index = getattr(self.selection.data_source, self.df.index_name)
        try:
            translate = not self.df.get(index, "_translate")
        except ValueError:
            translate = not translate
        self.df.set(index, "_translate", translate)
        if translate:
            if "_title_translated" not in self.df.columns or not self.df.get(index, "_title_translated"):
                translated = self.translator.translate(
                    self.selection.data_source.title,
                    src=self.selection.data_source.translate_src,
                    dest=self.provider.translate_dest
                ).text
                self.df.set(index, "_title_translated", translated)
        self.invalidate_rows([index])

    def toggle_translate_all(self):
        self.translate = not self.translate
        self.apply_translation()

    def apply_translation(self):
        if len(self) and self.translate:
            texts = [
                (row.index, row.get("title"))
                for row in self
                if not isinstance(row.get("_title_translated"), str)
                and isinstance(row.get("title"), str)
                and len(row.get("title"))
            ]
            # FIXME: bulk translate not working, so we improvise...
            # translates = self.translator.translate(
            #     [ t[1] for t in texts ],
            #     src=self.provider.translate_src or "auto",
            #     dest=self.provider.translate_dest
            # )
            translates = self.translator.translate(
                "\N{VERTICAL LINE}".join(
                    [ t[1].replace(
                        "\N{VERTICAL LINE}", "|"
                    ) for t in texts ]),
                src=self.provider.translate_src or "auto",
                dest=self.provider.translate_dest
            ).text.split("\N{VERTICAL LINE}")
            # raise Exception(translated)
            for (i, _), t in zip(texts, translates):
                self.df.set(i, "_translate", True)
                self.df.set(i, "_title_translated", t)
            self.invalidate_rows(
                [ row.index for row in self if row.get("_title_translated") ]
            )

    def toggle_strip_emoji_all(self):
        self.strip_emoji = not self.strip_emoji
        self.invalidate_rows(
            [ row.index for row in self ]
        )

    def keypress(self, size, key):
        return super().keypress(size, key)

    def reset(self, *args, **kwargs):
        super().reset(*args, **kwargs)
        self.translate = self.provider.translate
        self.apply_translation()

    @property
    def playlist_title(self):
        return self.provider.playlist_title

    @property
    def playlist_position_text(self):
        return f"[{self.focus_position+1}/{len(self)}]"

    def decorate(self, row, column, value):

        if column.name == "title":

            if row.get("_title_translated") and (self.translate or row.get("_translate")):
                value = row.get("_title_translated")

            if self.strip_emoji or row.get("_strip_emoji"):
                value = utils.strip_emoji(value)

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

    def on_activate(self):
        pass

    def on_deactivate(self):
        state.event_loop.create_task(state.task_manager.preview(None, self))


    def create_download_tasks(self, listing, index=None, **kwargs):

        with db_session:
            if isinstance(listing, model.InflatableMediaListing) and not listing.is_inflated:
                listing = listing.attach()
                state.asyncio.create_task(listing.inflate())
                listing = listing.detach()

        return super().create_download_tasks(
            listing,
            downloader_spec=getattr(self.provider.config, "helpers"),
            index=index,
            **kwargs
        )

    async def browse_selection(self):
        listing = self.selected_listing
        filename = self.selected_source.download_filename(listing=listing)
        state.files_view.browse_file(filename)

    def apply_search_query(self, query):
        self.apply_filters([lambda row: query in row["title"]])
        # self.reset()

    def clear_search_query(self):
        self.reset_filters()
