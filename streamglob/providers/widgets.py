import logging
logger = logging.getLogger(__name__)

import functools
import re
import bisect

import urwid
from panwid.dialog import ConfirmDialog
from panwid.datatable import *
from panwid.keymap import *
from panwid.autocomplete import AutoCompleteEdit
from pony.orm import *
import httpcore
httpcore.SyncHTTPTransport = None
from googletrans import Translator
from orderedattrdict import DefaultAttrDict
# from pygoogletranslation import Translator

from . import config
from .. import utils
from ..exceptions import *
from ..widgets import *
from .filters import *
from ..state import *
from .. import model
from .. import programs

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

class DownloadDirEdit(AutoCompleteEdit):

    HISTORY_LENGTH = 20

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        super().__init__(*args, **kwargs)
        self.load_history()
        self._history_index = len(self._history)
        self._completions = self._history + sorted([
            e.name for e in os.scandir(
                self.provider.output_path
            )
            if e.is_dir()
            and e.name not in self._history
        ])
        self._autocomplete_delims = "\t\n;"
        self.enable_autocomplete(self.auto_complete)

    def keypress(self, size, key):
        key = super().keypress(size, key)
        if key in ["ctrl n", "ctrl p"]:
            if not len(self._history):
                return
            step = -1 if key == "ctrl p" else 1
            self._history_index = max(
                min(
                    self._history_index + step,
                    len(self._history) - 1
                ),
                0
            )
            value = self._history[self._history_index]
            self.set_edit_text(value)
            self.set_edit_pos(len(value))
        else:
            return key

    def auto_complete(self, text, state):
        tmp = [
            c for c in self._completions
            if c and c.lower().startswith(text.lower())
        ] if text else self._completions
        try:
            return tmp[state]
        except (IndexError, TypeError):
            return None

    def load_history(self):
        self._history = state.app_data.get("downloads", {}).get("group_history", [])

    def save_history(self):
        # import ipdb; ipdb.set_trace()
        selection = self.get_edit_text()
        self._history = (
            [
                item for item in self._history
                if item != selection
            ] + [selection]
        )[:self.HISTORY_LENGTH]
        self._history_index = len(self._history)
        state.app_data.downloads["group_history"] = self._history
        state.app_data.save()


class DownloadDialog(OKCancelDialog):

    def __init__(self, parent, default_group):

        self.default_group = default_group
        super().__init__(parent)

    @property
    def widgets(self):

        return dict(
            group=DownloadDirEdit(
                provider=self.parent.provider,
                edit_text=self.default_group or "",
                caption=("bold", "Group: ")
            ),
            tag=BaseDropdown(
                [("(none)", None)]
                + list(self.parent.provider.rules.labels),
                label="Label: "
            )
        )

    async def action(self):

        self.group.save_history()
        group = self.group.get_edit_text()
        if self.tag.selected_value:
            path = os.path.join(self.parent.provider.output_path, group)
            if not os.path.exists(path):
                os.makedirs(path)
            self.parent.provider.rules.add_rule(
                self.tag.selected_label,
                group
            )

        self.parent.provider.load_rules()
        self.parent.provider.reset()
        await self.parent.download_selection(group=group or None)


@keymapped()
class DownloadListingViewMixin(ListingViewMixin):

    KEYMAP = {
        "l": ("download_selection_with_options", {"fast": True}),
        "L": ("download_selection_with_options", {"fast": False})
    }

    @property
    def NAME(self):
        return "downloader"

    async def download(self, listing, index=None, no_task_manager=False, **kwargs):
        for task in self.provider.create_download_tasks(listing, index=index, **kwargs):
            yield state.task_manager.download(task)

    async def download_selection(self, **kwargs):

        source = self.selected_source
        if not source:
            return
        async for task in self.download(
            self.selected_listing, index=source.rank or 0, **kwargs
        ):
            pass

    async def download_selection_with_options(self, fast=False):
        listing = self.selected_listing
        if not listing:
            return

        if fast and listing.group:
            await self.download_selection()
            return
        else:
            dialog = DownloadDialog(self, listing.group)
            self.provider.view.open_popup(dialog, width=60, height=8)


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

            downloader_spec = downloader_spec or source.download_helper
            with db_session:
                listing = model.MediaListing[listing.media_listing_id]
                download = model.MediaDownload.upsert(
                    dict(
                        media_listing=listing,
                        source_index=index
                    )
                )

            task = model.DownloadMediaTask.attr_class(
                provider=self.NAME,
                title=utils.sanitize_filename(listing.title),
                sources=[source],
                listing=listing,
                # dest=filename,
                args=(downloader_spec,),
                kwargs=dict(index=index, **kwargs),
                postprocessors=(self.config.get("postprocessors", None) or []).copy()
            )
            yield task

class RunCommandDropdown(BaseDropdown):

    @property
    def items(self):
        return config.settings.profile.files.commands

    @property
    def expanded(self):
        return True

class RunCommandPopUp(OKCancelDialog):

    def __init__(self, parent):

        super().__init__(parent)
        urwid.connect_signal(self.dropdown, "change", self.on_dropdown_change)

    def on_dropdown_change(self, source,label,  value):
        self.pile.set_focus_path(self.ok_focus_path)

    @property
    def widgets(self):

        return dict(
            dropdown=RunCommandDropdown()
        )

    async def action(self):

        await self.parent.run_command_on_selection(
            self.dropdown.selected_value
        )


@keymapped()
class ShellCommandViewMixin(object):

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)
        for cmd, cfg in (
                list(config.settings.profile.commands.items())
                + list(self.config.commands.items())
        ):
            if "key" in cfg:
                func = functools.partial(
                    self.run_command_on_selection,
                    cfg
                    )
                self.keymap_register(cfg.key, func)

    async def run_command_on_selection(self, cmd_cfg):

        cmd = cmd_cfg.command
        try:
            prog = next(programs.ShellCommand.get(cmd))
        except StopIteration:
            logger.error(f"program {prog} not found")

        args = [
            a.format(
                locator=self.selection.locator or self.selection.locators[0],
                socket=state.task_manager.preview_player.ipc_socket_name
            )
            for a in cmd_cfg.args
        ]
        logger.info(args)
        # async def show_output():
        #     output = await prog.output_ready
        #     logger.info(f"output: {output}")
        # state.event_loop.create_task(show_output())
        await prog.run(args)


    def open_run_command_dialog(self):

        path = self.browser.selection.full_path
        popup = RunCommandPopUp(self)
        self.open_popup(popup, width=60, height=10)


class DecoratedTableMixin(object):

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        self.translate = self.provider.translate
        self.strip_emoji = self.provider.strip_emoji
        self._translator = None
        super(DecoratedTableMixin,  self).__init__(*args, **kwargs)

    def decorate(self, row, column, value):

        for attr in self.provider.config.display.tables.decorate:
            if column.name == attr:

                if row.get(f"_{attr}_translated") and (self.translate or row.get("_translate")):
                    value = row.get(f"_{attr}_translated")

                if self.strip_emoji or row.get("_strip_emoji"):
                    value = utils.strip_emoji(value)

                if self.provider.rules:
                    listing = row.data_source
                    index = getattr(listing, self.df.index_name)
                    markup_column = f"_markup_{attr}"
                    if not row.get(markup_column):
                        self.df.set(
                            index, markup_column,
                            self.provider.rules.apply(
                                value,
                                aliases=listing.token_aliases,
                            )
                        )
                    markup = self.df.get(index, markup_column)
                    if len(markup):
                        value = urwid.Text(markup)

        return super().decorate(row, column, value)

    @property
    def translator(self):
        if not self._translator:
            # self._translator = Translator(sleep=1)
            self._translator = Translator()
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
            for attr in ["title", "group"]:
                if f"_{attr}_translated" not in self.df.columns or not self.df.get(index, "_title_translated"):
                    translated = self.translator.translate(
                        getattr(self.selection.data_source, attr),
                        src=self.selection.data_source.translate_src,
                        dest=self.provider.translate_dest
                    ).text
                    self.df.set(index, f"_{attr}_translated", translated)
                    # force regeneration of text markup
                    self.df.set(index, f"_markup_{attr}", None)
        self.invalidate_rows([index])

    def toggle_translate_all(self):
        self.translate = not self.translate
        self.apply_translation()

    def apply_translation(self):
        if len(self) and self.translate:
            for attr in ["title", "group"]:
                texts = [
                    (row.index, row.get(attr, ""))
                    for row in self
                    if not isinstance(row.get(f"_{attr}_translated"), str)
                    and isinstance(row.get(attr, ""), str)
                    # and len(row.get(attr))
                ]
                sentinel = "\n\N{RIGHTWARDS ARROW}\n"
                # FIXME: bulk translate not working, so we improvise...
                translates = self.translator.translate(
                    sentinel.join(
                        [t[1].replace(
                            sentinel, "|"
                        ) for t in texts]),
                    src=self.provider.translate_src or "auto",
                    dest=self.provider.translate_dest
                ).text.split(sentinel)
                for (index, _), t in zip(texts, translates):
                    self.df.set(index, "_translate", True)
                    self.df.set(index, f"_{attr}_translated", t)
                    self.df.set(index, f"_markup_{attr}", None)
                self.invalidate_rows(
                    [ row.index for row in self if row.get(f"_{attr}_translated") ]
                )

    def toggle_strip_emoji_all(self):
        self.strip_emoji = not self.strip_emoji
        self.invalidate_rows(
            [ row.index for row in self ]
        )

    def reset(self, *args, **kwargs):
        super().reset(*args, **kwargs)
        self.translate = self.provider.translate
        self.apply_translation()
        # if self.update_task:
        #     self.update_task.cancel()
        # self.update_task = state.event_loop.create_task(self.update_row_attributes())


@keymapped()
class ProviderDataTable(
        DecoratedTableMixin,
        PlayListingViewMixin,
        DownloadListingViewMixin,
        ShellCommandViewMixin,
        BaseDataTable):

    ui_sort = True
    query_sort = True

    signals = ["cycle_filter"]

    KEYMAP = {
        ",": ("browse_selection", {"focus": True}),
        "?": "show_details",
        "h": "add_highlight_rule",
        "H": "remove_highlight_rule",
        # "ctrl o": "strip_emoji_selection",
        "ctrl t": "translate_selection",
        # "meta O": "toggle_strip_emoji_all",
        "meta T": "toggle_translate_all",
        "delete": "delete_selection",
        "meta up": "prev_item",
        "meta down": "next_item"
    }

    def __init__(self, provider, *args, **kwargs):

        self.provider = provider
        # self.translate = self.provider.translate
        # self.strip_emoji = self.provider.strip_emoji
        # self._translator = None
        self.update_task = None
        self.browse_selection_task = None
        super(ProviderDataTable,  self).__init__(provider, *args, **kwargs)

    @property
    def tmp_dir(self):
        return self.provider.tmp_dir

    @property
    def preview_files(self):
        if not hasattr(self, "_preview_files"):
            self._preview_files = DefaultAttrDict(lambda: DefaultAttrDict(None))
        return self._preview_files

    @property
    def NAME(self):
        return self.provider.NAME

    @property
    def columns(self):
        return [
            DataTableColumn(k, **v if v else {})
            for k, v in self.provider.attributes.items()
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

    def show_details(self):
        logger.info(self.selection.data_source.group)
        logger.info(self.selection.data_source.subjects)
        logger.info(
            self.selected_source.get_local_path()
        )

    def playlist_position(self):
        return self.focus_position

    def listings(self, *args, **kwargs):
        yield from self.provider.listings(*args, **kwargs)

    def keypress(self, size, key):
        return super().keypress(size, key)

    def reset(self, *args, **kwargs):
        super().reset(*args, **kwargs)
        # self.translate = self.provider.translate
        # self.apply_translation()
        if self.update_task:
            self.update_task.cancel()
        self.update_task = state.event_loop.create_task(self.update_row_attributes())

    async def row_attr(self, row):
        if not self.provider.check_downloaded:
            return None
        paths = set(
            [s.get_local_path() for s in row.data.sources]
        )
        status = {
            t[1]
            for t in paths
            if t and len(t) > 1
        }
        # import ipdb; ipdb.set_trace()
        if not len(status):
            return None
        if status == {"exact"}:
            return "downloaded"
        elif len(status) == 1:
            return f"downloaded.{status.pop()}"
        else:
            return "downloaded.mixed"

    async def update_row_attribute(self, row):
        attr = await self.row_attr(row)
        if attr:
            cur = row.get_attr()
            attr = " ".join([ a for a in [attr, cur] if a ])
            # logger.info(attr)
            row.set_attr(attr)

    async def update_row_attributes(self):
        for row in self:
            await asyncio.sleep(config.settings.profile.get_path("display.attribute_delay", 1))
            await self.update_row_attribute(row)

    def on_focus(self, source, position):
        if self.browse_selection_task:
            self.browse_selection_task.cancel()
        self.browse_selection_task = state.event_loop.create_task(
            self.browse_selection(focus=False)
        )

    @property
    def playlist_title(self):
        return self.provider.playlist_title

    @property
    def playlist_position_text(self):
        return f"[{self.focus_position+1}/{len(self)}]"

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

            @property
            def default_subject(self):
                if not getattr(self, "_default_subject", None):
                    if self.parent.selection.data_source.subjects:
                        self._default_subject = self.parent.selection.data_source.subjects[0]
                    elif self.parent.provider.conf_rules.highlight_words:
                        self._default_subject = utils.strip_emoji(self.parent.selection.data.title).strip()
                    else:
                        self._default_subject = None
                return self._default_subject

            @property
            def widgets(self):
                try:
                    edit_pos = [
                        m.start() for m in re.finditer(r"\S+", self.default_subject)
                    ][self.parent.provider.conf_rules.highlight_words]-1
                except (AttributeError, IndexError):
                    edit_pos = 0

                # # default_subject = None
                # if self.parent.selection.data_source.group:
                #     default_subject = self.parent.selection.data_source.group
                # elif self.parent.provider.conf_rules.highlight_words:
                #     default_subject = self.parent.selection.data.title
                #     try:
                #         edit_pos = [
                #             m.start() for m in re.finditer(r"\S+", default_subject)
                #         ][self.parent.provider.conf_rules.highlight_words]-1
                #     except IndexError:
                #         pass

                label, rule = self.parent.provider.rules.rule_for_token(
                    self.default_subject
                )
                if rule:
                    rule_cfg = rule.to_dict()
                    group = rule_cfg.get("group", "")
                    patterns = rule_cfg.get("paterns", [])
                else:
                    group = ""
                    patterns = []

                return dict(
                    subject=urwid_readline.ReadlineEdit(
                        edit_text=self.default_subject or "",
                        caption=("bold", "Subject: "),
                        edit_pos=edit_pos
                    ),
                    group=urwid_readline.ReadlineEdit(
                        edit_text=group,
                        caption=("bold", "Group: ")
                    ),
                    patterns=urwid_readline.ReadlineEdit(
                        caption=("bold", "Patterns: "),
                        edit_text=", ".join(patterns),
                    ),
                    create=urwid.CheckBox("Create directory?", state=True),
                    tag=BaseDropdown(list(self.parent.provider.rules.labels))
                )

            @property
            def focus(self):
                return "ok" if self.parent.selection.data_source.group else 0


            def action(self):

                subject = self.subject.get_edit_text().strip()

                patterns = [
                    pattern
                    for pattern in [
                        p.strip()
                        for p in self.patterns.get_edit_text().split(",")
                    ]
                    if pattern
                ]

                group = self.group.get_edit_text().strip()

                if self.create.get_state():
                    dirname = group or subject
                    if dirname in model.SUBJECT_MAP:
                        del model.SUBJECT_MAP[dirname]
                    path = os.path.join(self.parent.provider.output_path, dirname)
                    if not os.path.exists(path):
                        os.makedirs(path)

                self.parent.provider.rules.add_rule(
                    self.tag.selected_label,
                    subject, group=group, patterns=patterns
                )
                self.parent.provider.load_rules()
                self.parent.reset()

        dialog = AddHighlightRuleDialog(self)
        self.provider.view.open_popup(dialog, width=60, height=12)

    def remove_highlight_rule(self):

        class RemoveHighlightRuleDialog(OKCancelDialog):

            @property
            def widgets(self):
                try:
                    default_text = getattr(self.parent.selected_listing, "subjects")[0]
                except (IndexError, AttributeError):
                    default_text = ""
                return dict(
                    text=urwid_readline.ReadlineEdit(
                        caption=("bold", "Text: "),
                        edit_text=default_text
                    )
                )

            def action(self):

                self.parent.provider.rules.remove_rule(
                    self.text.get_edit_text()
                )
                self.parent.provider.load_rules()
                self.parent.reset()

        dialog = RemoveHighlightRuleDialog(self)
        self.provider.view.open_popup(dialog, width=60, height=8)

    async def prev_item(self):
        if self.focus_position > 0:
            self.focus_position -= 1

    async def next_item(self):
        if self.focus_position < len(self)-1:
            self.focus_position += 1

    async def browse_selection(self, focus=False):
        listing = self.selected_listing
        match = self.selected_source.get_local_path(
            self.provider.config.output.match_types + ["directory"]
        )
        if match:
            (path, match_type) = match
            state.files_view.browse_file(path)
            if focus:
                # FIXME: util function?
                state.main_view.focus_widget(state.files_view)

    def apply_search_query(self, query):
        self.apply_filters([lambda row: query in row["title"]])
        # self.reset()

    def clear_search_query(self):
        self.reset_filters()

    def open_delete_confirm_dialog(self, filename):

        class DeleteConfirmDialog(ConfirmDialog):

            def __init__(self, parent, filename):
                self.filename = filename
                super().__init__(parent)

            @property
            def prompt(self):
                return f"""Delete "{self.filename}"?"""

            def action(self):
                os.remove(self.filename)

        dialog = DeleteConfirmDialog(self.provider.view, filename)
        self.provider.view.open_popup(dialog, width=80, height=8)

    def delete_selection(self):

        listing = self.selected_listing
        filename = self.selected_source.local_path
        if filename:
            self.open_delete_confirm_dialog(filename)
