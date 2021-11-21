import logging
logger = logging.getLogger(__name__)

import functools
import re
import bisect

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

    @property
    def filter_state(self):
        return AttrDict([
            (f.name, f.value)
            for f in self.filters
        ])

    def apply_filter_state(self, state):
        for k, v in state.items():
            self.filters[k].value = v


class ListingViewMixin(object):

    @property
    def selected_listing(self):
        return self.get_listing()

    def get_listing(self, index=None):
        if index is None:
            index = self.focus_position
        try:
            return self[index].data_source
        except IndexError:
            return None

    @property
    def selected_listing(self):
        return self.get_listing()

    def get_source(self, listing=None, index=None):
        if listing is None:
            listing = self.selected_listing
        if index is None:
            index = 0
        return listing.sources[index]

    @property
    def selected_source(self):
        return self.get_source()


@keymapped()
class PlayListingViewMixin(ListingViewMixin):

    KEYMAP = {
        "p": "play_selection",
    }

    @property
    def active_table(self):
        # subclasses can override to return an inner table
        return self

    async def play_selection(self, *args, **kwargs):
        listing = self.selected_listing
        if not listing:
            return
        async for task in self.play(listing, *args, **kwargs):
            pass

    async def play(self, listing, **kwargs):
        # sources, kwargs = self.extract_sources(listing, **kwargs)
        task = self.provider.create_play_task(listing, **kwargs)
        yield state.task_manager.play(task)


class PlayListingProviderMixin(object):

    def create_play_task(self, listing, **kwargs):

        sources, kwargs = self.extract_sources(listing, **kwargs)

        media_types = set([s.media_type for s in sources if s.media_type])

        player_spec = {"media_types": media_types}

        if media_types == {"image"}:
            downloader_spec = {None: None}
        else:
            downloader_spec = (
                getattr(self.config, "helpers", None)
                or getattr(sources[0], "helper", None)
                or getattr(self, "helper", None)
            )

        return model.PlayMediaTask.attr_class(
            provider=self.NAME,
            title=listing.title,
            listing=listing,
            sources=sources,
            args=(player_spec, downloader_spec),
            kwargs=kwargs
        )




@keymapped()
class DownloadListingViewMixin(ListingViewMixin):

    KEYMAP = {
        "l": "download_selection"
    }

    @property
    def NAME(self):
        return "downloader"

    async def download(self, listing, index=None, no_task_manager=False, **kwargs):
        for task in self.provider.create_download_tasks(listing, index=index, **kwargs):
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


class DownloadListingProviderMixin(object):

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
            with db_session:
                listing = model.MediaListing[listing.media_listing_id]
                download = model.MediaDownload.upsert(
                    dict(media_listing=listing)
                )

            task = model.DownloadMediaTask.attr_class(
                provider=self.NAME,
                title=utils.sanitize_filename(listing.title),
                sources=[source],
                listing=listing,
                dest=filename,
                args=(downloader_spec,),
                kwargs=dict(index=index, **kwargs),
                postprocessors=(self.config.get("postprocessors", None) or []).copy()
            )
            yield task


@keymapped()
class ProviderDataTable(PlayListingViewMixin, DownloadListingViewMixin, BaseDataTable):

    ui_sort = True
    query_sort = True

    signals = ["cycle_filter"]

    KEYMAP = {
        ",": "browse_selection",
        "?": "show_subject",
        "h": "add_highlight_rule",
        "H": "remove_highlight_rule",
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
        self.update_task = None
        super(ProviderDataTable,  self).__init__(*args, **kwargs)

    @property
    def tmp_dir(self):
        return self.provider.tmp_dir

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

    def show_subject(self):
        logger.info(self.selection.data_source.subject)

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
        if self.update_task:
            self.update_task.cancel()
        self.update_task = state.event_loop.create_task(self.update_row_attributes())

    async def row_attr(self, row):
        if not self.provider.check_downloaded:
            return None
        if all([s.local_path for s in row.data.sources]):
            return "downloaded"
        return None

    async def update_row_attribute(self, row):
        attr = await self.row_attr(row)
        if attr:
            cur = row.get_attr()
            row.set_attr(" ".join([ a for a in [attr, cur] if a ]))

    async def update_row_attributes(self):
        for row in self:
            await asyncio.sleep(config.settings.profile.get_path("display.attribute_delay", 1))
            await self.update_row_attribute(row)

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
                    ( next(v["attr"] for k, v in self.provider.highlight_map.items()
                           if k.search(x)), x)
                    if self.provider.highlight_re.search(x)
                    else x for x in self.provider.highlight_re.split(value) if x
                ]
                if len(markup):
                    row.tokens = [x[1] for x in markup if isinstance(x, tuple)]
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


    def add_highlight_rule(self):

        class AddHighlightRuleDialog(OKCancelDialog):

            focus = "ok"

            @property
            def widgets(self):
                edit_pos = 0
                if self.parent.provider.conf_rules.highlight_words:
                    try:
                        edit_pos = [
                            m.start() for m in re.finditer(r"\S+",self.parent.selection.data.title)
                        ][self.parent.provider.conf_rules.highlight_words]-1
                    except IndexError:
                        pass

                return dict(
                    token=urwid_readline.ReadlineEdit(
                        caption=("bold", "Token: "),
                        edit_text=self.parent.selection.data.title,
                        edit_pos=edit_pos
                    ),
                    subject=urwid_readline.ReadlineEdit(
                        caption=("bold", "Subject: ")
                    ),
                    group=urwid_readline.ReadlineEdit(
                        caption=("bold", "Group: ")
                    ),
                    tag=BaseDropdown(list(self.parent.provider.rules.keys()))
                )

            def action(self):
                pattern = self.token.get_edit_text()
                if (
                        pattern.lower()
                        in [
                            x.lower() if isinstance(x, str) else x["pattern"].lower()
                            for x in self.parent.provider.conf_rules.label[self.tag.selected_label]
                        ]
                    ):
                    return

                subjects = [
                    s.strip()
                    for s in self.subject.get_edit_text().split(",")
                ]
                cfg = {
                    k: v
                    for k, v in dict(
                            pattern=pattern,
                            subjects=subjects,
                            group=self.group.get_edit_text()
                    ).items()
                    if v
                }
                # FIXME: bisect.insort_right doesn't support a key param until
                # Python 3.10, which we can't upgrade to until Pony ORM is
                # updated with Python 3.10 support.
                # bisect.insort_right(
                #     self.parent.provider.conf_rules.label[self.tag.selected_label],
                #     cfg,
                #     key=lambda cfg: cfg["pattern"] if isinstance(cfg, dict) else cfg
                # )

                for i, rule in enumerate(self.parent.provider.conf_rules.label[self.tag.selected_label]):
                    pattern = rule if isinstance(rule, str) else rule["pattern"]
                    # import ipdb; ipdb.set_trace()
                    if pattern.lower() > cfg["pattern"].lower():
                        self.parent.provider.conf_rules.label[self.tag.selected_label].insert(
                            i, cfg
                        )
                        break

                self.parent.provider.conf_rules.save()
                self.parent.provider.load_rules()
                self.parent.reset()

        dialog = AddHighlightRuleDialog(self)
        self.provider.view.open_popup(dialog, width=60, height=8)

    def remove_highlight_rule(self):

        class RemoveHighlightRuleDialog(OKCancelDialog):

            @property
            def widgets(self):
                row = self.parent.selection
                return dict(
                    text=urwid_readline.ReadlineEdit(
                        caption=("bold", "Text: "),
                        edit_text="" if not getattr(row, "tokens", None) else row.tokens[0]
                    )
                )

            def action(self):
                rules = self.parent.provider.conf_rules.label
                for label in rules.keys():
                    try:
                        rules[label] = [
                            r for r in rules[label]
                            if not re.search(
                                    r if isinstance(r, str) else r["pattern"],
                                    self.text.get_edit_text(), re.IGNORECASE
                            )
                        ]
                        self.parent.provider.conf_rules.save()
                        self.parent.provider.load_rules()
                        self.parent.reset()
                        break
                    except ValueError:
                        continue

        dialog = RemoveHighlightRuleDialog(self)
        self.provider.view.open_popup(dialog, width=60, height=8)
    
    async def browse_selection(self):
        listing = self.selected_listing
        filename = self.selected_source.local_path
        if filename:
            state.files_view.browse_file(filename)

    def apply_search_query(self, query):
        self.apply_filters([lambda row: query in row["title"]])
        # self.reset()

    def clear_search_query(self):
        self.reset_filters()
