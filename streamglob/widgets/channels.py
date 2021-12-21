import logging
logger = logging.getLogger(__name__)

import itertools
import re
import os
from functools import partial
from datetime import datetime

import urwid
import yaml
from pony.orm import *
from panwid.autocomplete import AutoCompleteMixin
from panwid.keymap import *
from panwid.dialog import ChoiceDialog, ConfirmDialog, BasePopUp
from unidecode import unidecode

from .tree import *
from .. import config
from .. import model
from .. import utils
from ..widgets import StreamglobScrollBar, TextEditDialog

from ..state import *

class ChannelTreeWidget(HighlightableTreeWidgetMixin, AttributeTreeWidget):
    """ Display widget for leaf nodes """

    @property
    def name(self):
        key = self.get_node().get_key()
        value = self.get_node().get_value()
        if not value:
            return key
        elif isinstance(value, str):
            return value
        elif isinstance(value, dict) and "name" in value:
            return value["name"]
        elif key:
            return key
        else:
            return "no name"

    @property
    def text(self):
        return self.name

    def selectable(self):
        return True


class ListingCountMixin(object):

    @property
    def listing_count(self):
        return self.channel.listing_count if self.channel else 0

    @property
    def unread_count(self):
        return self.channel.unread_count if self.channel else 0

    @property
    def count_attr(self):
        return "browser count"

    @property
    def count_unread_attr(self):
        return "browser count_unread"

    @property
    def count_total_attr(self):
        return "browser count_total"

    @property
    @db_session
    def first_listing_date(self):

        return select(
            min(item.created)
            for item in self.channel.items
        )[:][0] if self.channel else None

    @property
    @db_session
    def last_listing_date(self):
        return select(
            max(item.created)
            for item in self.channel.items
        )[:][0] if self.channel else None

    @property
    def fetched(self):
        return self.channel.fetched if self.channel else None

    @property
    def content_age_text(self):
        if self.last_listing_date:
            return utils.format_age(self.last_listing_date)
        else:
            return None

    @property
    def fetched_age_text(self):
        if self.fetched:
            return utils.format_age(
                self.fetched
            )
        else:
            return None

    @property
    def text(self):
        unread = self.unread_count
        total = self.listing_count

        return [
            (self.attr, self.name), " ",
            (self.count_attr, "("),
            (self.count_unread_attr if unread else self.count_attr,
             str(unread)
             ),
            (self.count_attr, "/"),
            (self.count_total_attr if total else self.count_attr,
             str(total)
             )
        ] + ([
            (self.count_attr, "/"),
            (self.count_attr, self.fetched_age_text)
        ] if self.fetched_age_text else []) + ([
            (self.count_attr, "/"),
            (self.count_attr, self.content_age_text)
        ] if self.content_age_text else []) + [
            (self.count_attr, ")")
        ]

class AggregateListingCountMixin(ListingCountMixin):

    @property
    def listing_count(self):
        return sum([
            n.get_widget().listing_count or 0
            for n in self.get_node().get_leaf_nodes()
        ])

    @property
    def unread_count(self):
        return sum([
            n.get_widget().unread_count or 0
            for n in self.get_node().get_leaf_nodes()
        ])

    @property
    @db_session
    def first_listing_date(self):
        try:
            return min([
                n.get_widget().first_listing_date
                for n in self.get_node().get_leaf_nodes()
                if n.get_widget().first_listing_date is not None
            ])
        except ValueError:
            return None

    @property
    @db_session
    def last_listing_date(self):
        try:
            return max([
                n.get_widget().last_listing_date
                for n in self.get_node().get_leaf_nodes()
                if n.get_widget().last_listing_date is not None
            ])
        except ValueError:
            return None

    @property
    def age(self):
        try:
            return max([
                n.get_widget().age
                for n in self.get_node().get_leaf_nodes()
                if n.get_widget().age is not None
            ])
        except ValueError:
            return None

    @property
    def fetched(self):
        try:
            return min([
                n.get_widget().fetched
                for n in self.get_node().get_leaf_nodes()
                if n.get_widget().fetched is not None
            ])
        except ValueError:
            return None

class ChannelWidget(ListingCountMixin,
                    MarkableMixin,
                    ChannelTreeWidget):

    @property
    def channel(self):
        with db_session:
            return model.MediaChannel.get(
                provider_id=self.provider.IDENTIFIER,
                locator=self.get_node().locator
            )

    @property
    def browser(self):
        return self.get_node().get_parent().tree

    @property
    def provider(self):
        return self.browser.provider

    @property
    def attr(self):

        head = self.unread_count > 0
        tail = not self.channel.attrs.get("tail_fetched") if self.channel else None
        error = self.channel and self.channel.attrs.get("error")

        dormant = False
        if (self.channel
            and self.browser.config.dormant_days
            and self.channel.fetched
            and self.last_listing_date):

            dormant_days = (
                self.channel.fetched - self.last_listing_date
            ).days

            if (dormant_days >= self.browser.config.dormant_days
                and (not self.browser.config.dormant_reset_days
                     or self.channel.fetched_age.days < self.browser.config.dormant_reset_days)
                ):
                dormant = True

        if error:
            return "browser error"
        elif dormant:
            return "browser dormant"
        elif head and tail:
            return "browser head_tail"
        elif head:
            return "browser head"
        elif tail:
            return "browser tail"
        else:
            return "browser normal"

    # def keypress(self, size, key):
    #     return super().keypress(size, key)
    #     if key == "enter":
    #         self.mark()

    @property
    def highlightable_attr_normal(self):
        return self.attr

    @property
    def highlightable_attr_highlight(self):
        return "browser highlight"



class ChannelUnionWidget(AggregateListingCountMixin, ChannelWidget):

    def __init__(self, node):
        super().__init__(node)
        self.is_leaf = True

    def get_indented_widget(self):
        widget = self.get_inner_widget()
        if not self.is_leaf:
            widget = urwid.Columns([('fixed', 0,
                [self.unexpanded_icon, self.expanded_icon][self.expanded]),
                widget], dividechars=1)
        indent_cols = self.get_indent_cols()
        return urwid.Padding(widget,
            width=('relative', 100), left=indent_cols)


class ChannelGroupWidget(
        AggregateListingCountMixin, ExpandableMarkedTreeWidget,
        ChannelTreeWidget
):

    indent_cols = 3

    # apply an attribute to the expand/unexpand icons
    unexpanded_icon = urwid.AttrMap(
        urwid.TreeWidget.unexpanded_icon,
        "browser dirmark", "browser dirmark_focus"
    )
    expanded_icon = urwid.AttrMap(
        urwid.TreeWidget.expanded_icon,
        "browser dirmark", "browser dirmark_focus")

    def selectable(self):
        return True

    def keypress(self, size, key):
        """allow subclasses to intercept keystrokes"""
        key = self.__super.keypress(size, key)
        if key:
            key = self.unhandled_keys(size, key)
        return key

    def unhandled_keys(self, size, key):
        """
        Override this method to intercept keystrokes in subclasses.
        """
        if key == "right":
            self.get_node().expand()
        elif key == "left":
            self.get_node().collapse()
        else:
            return key

    def mark(self):
        super().mark()
        for node in self.get_node().get_nodes():
            node.get_widget().mark()

    def unmark(self):
        super().unmark()
        for node in self.get_node().get_nodes():
            node.get_widget().unmark()

class ChannelPropertiesMixin(object):

    async def refresh(self):
        node = self
        while node:
            widget = node.get_widget(reload=True)
            widget._invalidate()
            node = node.get_parent()
        self.get_parent().tree.listbox._invalidate()
        # self.get_parent().tree.listbox.body._modified()

    @property
    def locator(self):
        return self.get_key()

    @property
    def name(self):
        value = self.get_value()
        if isinstance(value, dict):
            return value.get("name", self.locator)
        elif isinstance(value, str):
            return value
        return self.locator

    @property
    def attrs(self):
        value = self.get_value()
        if not isinstance(value, dict):
            return {}
        return {k: v for k, v in value.items() if k != "name"}

class ChannelNode(ChannelPropertiesMixin, TreeNode):

    def load_widget(self):
        return ChannelWidget(self)

    @property
    def identifier(self):
        return self.get_key()

class ChannelUnionNode(ChannelPropertiesMixin, TreeParentNode):

    def __init__(self, tree, value, parent=None, key=None, depth=None):
        self.tree = tree
        super().__init__(value, parent=parent, key=key, depth=depth)

    @property
    def starts_expanded(self):
        return True

    def load_widget(self):
        return ChannelUnionWidget(self)

    @property
    def identifier(self):
        return self.get_key()

    def load_child_keys(self):
        data = self.get_value()
        if not data:
            return []
        try:
            keys = list(data.get("channels", data).keys())
            return keys
        except AttributeError:
            raise Exception(data)

    def load_child_node(self, key):

        childdata = self.get_value()
        childdepth = self.get_depth() + 1
        if isinstance(key, config.Folder):
            return ChannelGroupNode(self.tree, childdata.get("channels", childdata)[key], parent=self, key=key, depth=childdepth)
        elif isinstance(key, config.Union):
            return ChannelUnionNode(self.tree, childdata.get("channels", childdata)[key], parent=self, key=key, depth=childdepth)
            # node.collapse()
            # return node
        else:
            return ChannelNode(childdata.get("channels", childdata)[key], parent=self, key=key, depth=childdepth)
        # return childclass(childdata[key], parent=self, key=key, depth=childdepth)

    def find_key(self, key):
        try:
            return next(self.get_nodes(lambda n: n.get_key() == key))
        except StopIteration:
            return None

    def find_node(self, identifier):
        try:
            return next(self.get_nodes(lambda n: n.identifier == identifier))
        except StopIteration:
            return None

    def expand(self):
        self.get_widget().expanded = True
        self.get_widget().update_expanded_icon()

    def collapse(self):
        self.get_widget().expanded = False
        self.get_widget().update_expanded_icon()


class ChannelGroupNode(ChannelUnionNode):

    @property
    def is_leaf(self):
        return False

    def load_widget(self):
        return ChannelGroupWidget(self)

    @property
    def marked(self):
        return self.get_widget().marked

    def find_path(self, path):
        node = self.get_first_child()

        head, *tail = path
        while True:
            if head:
                if node.get_key() == tail:
                    return node
            elif node.get_key() == head:
                node.expand()
                return node.find_path(tail) or node
            node = node.next_sibling()
            if not node:
                break

    @property
    def identifier(self):
        return ("folder", self.get_key())


class MyTreeListBox(urwid.TreeListBox):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows_max = None

    def get_scrollpos(self, size, focus=False):
        """Current scrolling position
        Lower limit is 0, upper limit is the highest index of `body`.
        """
        middle, top, bottom = self.calculate_visible(size, focus)
        if middle is None:
            return 0
        else:
            offset_rows, _, focus_pos, _, _ = middle
            maxcol, maxrow = size
            flow_size = (maxcol,)

            positions = tuple(self.body.positions())
            focus_index = positions.index(focus_pos)
            widgets_above_focus = (
                pos.get_widget() for pos in positions[:focus_index]
            )

            rows_above_focus = sum(w.rows(flow_size) for w in widgets_above_focus)
            rows_above_top = rows_above_focus - offset_rows
            return rows_above_top

    def rows_max(self, size, focus=False):
        if self._rows_max is None:
            flow_size = (size[0],)
            body = self.body
            self._rows_max = sum(
                pos.get_widget().rows(flow_size)
                for pos in body.positions()
            )
        return self._rows_max

class ChannelTreeFooter(urwid.WidgetWrap):

    def __init__(self, parent):

        self.parent = parent
        # self.details_placeholder = urwid.WidgetPlaceholder(self.details)
        self.details = urwid.Text("")
        self.filler = urwid.Filler(
            urwid.AttrMap(
                urwid.Columns([
                    ("weight", 1, urwid.Padding(self.details))
                ], dividechars=1),
                "footer"
            )
        )
        super().__init__(self.filler)

    def set_details(self, details):
        self.details.set_text(details)


@keymapped()
class ChannelTreeBrowser(AutoCompleteMixin, urwid.WidgetWrap):

    signals = ["change", "select", "advance"]

    KEYMAP = {
        "/": "complete substring",
        "?": "complete prefix",
        "n": "advance",
        "enter": "confirm",
        "esc": "cancel",
        "ctrl p": "complete_prev",
        "ctrl n": "complete_next"
    }

    def __init__(self, data, provider, label="channels"):
        self.label = label
        self.provider = provider
        self.placeholder = urwid.WidgetPlaceholder(urwid.Filler(urwid.Text("")))
        self.channel_count_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.header = urwid.Filler(
            urwid.AttrMap(
                urwid.Columns([
                    ("pack", urwid.Text(self.provider.CHANNELS_LABEL)),
                    ("weight", 1, self.channel_count_placeholder)
                ], dividechars=1),
                "header"
            )
        )
        self.footer = ChannelTreeFooter(self)
        self.pile = urwid.Pile([
            (1, self.header),
            ("weight", 1, self.placeholder),
            (1, self.footer)
        ])
        super().__init__(self.pile)
        self.pile.selectable = lambda: True
        self.load()

    def __getitem__(self, key):
        return self.find_key(key)

    @property
    def config(self):
        return config.settings.profile.channels

    @property
    def feed_config(self):
        if not getattr(self, "_feed_config", None):
            self._feed_config = config.Config(
            os.path.join(
                config.settings._config_dir,
                self.provider.IDENTIFIER,
                "feeds.yaml"
            )
        )
        return self._feed_config

    @feed_config.setter
    def feed_config(self, feed_config):
        self._feed_config = feed_config

    def load(self):
        self.tree = ChannelGroupNode(self, self.feed_config, key=self.label)
        self.listbox = MyTreeListBox(PositionsTreeWalker(self.tree))
        self.listbox.offset_rows = 1
        self.scrollbar = StreamglobScrollBar(self.listbox)
        self.placeholder.original_widget = self.scrollbar
        self.pile._invalidate()
        self.update_header()


    # def selectable(self):
    #     return True

    @property
    def focus_position(self):
        return self.listbox.focus_position

    @focus_position.setter
    def focus_position(self, pos):
        self.listbox.focus_position = pos

    @property
    def complete_container(self):
        return self.pile

    @property
    def complete_body_position(self):
        return 1

    @property
    def complete_container_pos(self):
        return 3

    @property
    def complete_body(self):
        return self.listbox.body

    def complete_widget_at_pos(self, pos):
        return pos.get_widget()

    def on_complete_select(self, pos):
        return
        # self.update_selection()

    def complete_compare_substring(self, search, candidate):
        try:
            return unidecode(candidate).index(unidecode(search))
        except ValueError:
            return None

    @property
    def body(self):
        return self.listbox.body

    @property
    def selection(self):
        return self.body.get_focus()[1]

    @property
    def all_channels(self):
        return self.tree.get_leaf_nodes()

    @property
    def selected_items(self):
        return self.tree.selected_items

    @selected_items.setter
    def selected_items(self, value):
        for i, identifier in enumerate(value):
            if isinstance(identifier, list):
                # JSON can't store tuples
                identifier = tuple(identifier)
            node = self.find_node(identifier)
            if not node:
                continue
            node.get_widget().mark()
            if i == 0:
                self.listbox.focus_position = node
        self.update_selection()

    def find_node(self, identifier):
        return self.tree.find_node(identifier)

    def update_selection(self):
        if len(list(self.selected_items)) == 1:
            self.provider.view.hide_columns(["channel"])
        else:
            self.provider.view.show_columns(["channel"])
        self._emit("change", self.selected_items)
        self._emit("select", self.selected_items)
        self.update_header()
        self.update_footer()

    def update_header(self):
        all_channels = list(self.all_channels)
        selected_channels = list(self.selected_items)
        self.channel_count_placeholder.original_widget = urwid.Text(
            f"({len(selected_channels)}/{len(all_channels)})"
            f""" {", ".join(c.name for c in selected_channels)}""",
            wrap="ellipsis"
        )

    def update_footer(self):

        details = [
            ("footer_dim", "["),
            ("footer", f"{len(self.selected_items)}"),
            ("footer_dim", "] ")
        ]

        wids = [
            n.get_widget()
            for n in self.selected_items
        ]
        if len(wids):
            try:
                min_date = min(
                    wid.first_listing_date
                    for wid in wids
                    if wid.first_listing_date
                )
            except ValueError:
                min_date = None

            try:
                max_date = min(
                    wid.last_listing_date
                    for wid in wids
                    if wid.last_listing_date
                )
            except ValueError:
                max_date = None

            try:
                min_fetched = min(
                    wid.fetched
                    for wid in wids
                    if wid.fetched
                )
            except ValueError:
                min_fetched = None

            if min_date and max_date:
                content_age = utils.format_age(max_date)

                fetched_age = utils.format_age(min_fetched)

                details += [
                    ("footer_dim", "("),
                    ("footer", min_date.strftime("%Y-%m-%d")),
                    ("footer_dim", "\N{EM DASH}"),
                    ("footer", max_date.strftime("%Y-%m-%d")),
                    ("footer_dim", ","),
                    ("footer", fetched_age),
                    ("footer_dim", ":"),
                    ("footer", content_age),
                    ("footer_dim", ")")
                ]

        self.footer.set_details(details)

    def open_delete_confirm_dialog(self, channel):

        class DeleteConfirmDialog(ConfirmDialog):

            @property
            def prompt(self):
                return f"""Delete "{channel.name}"?"""

            def action(self):
                channels = self.parent.channels
                # import ipdb; ipdb.set_trace()
                target = channels.focus_position
                try:
                    new_selection = channels.body.get_next(target)[1].identifier
                except IndexError:
                    new_selection = channels.body.get_prev(target)[1].identifier

                channels.delete_channel(channel.locator)
                channels.feed_config.save()
                channels.load()
                channels.listbox.set_focus(channels.find_node(new_selection))

        dialog = DeleteConfirmDialog(self.provider.view)
        self.provider.view.open_popup(dialog, width=60, height=5)

    def close_confirm_dialog(self):
        self.provider.view.close_popup()

    def move_selection(self, direction):

        this = self.listbox.focus_position
        self.listbox.set_focus(this.get_root())

        that = this
        while True:
            logger.info(that.get_key())
            that = (
                self.listbox.body.get_prev(that)[1]
                if direction < 0
                else self.listbox.body.get_next(that)[1]
            )
            if isinstance(this, ChannelGroupNode):
                if that.get_key() not in this._children:
                    break
            elif that != this.get_parent():
                break

        if isinstance(that, ChannelGroupNode):
            if this.get_parent() == that.get_parent():
                that = (
                    that.get_first_child()
                    if direction > 0
                    else that.get_last_child()
                )

        self.move_channel(
            this.get_key(), that.get_key(),
            direction
            if this.get_parent() == that.get_parent()
            else -direction
        )

    def move_channel(self, src, dst, direction):

        if not isinstance(src, list):
            src = [src]

        def pop_key(obj, key):
            if key in obj:
                return obj.pop(key)
            for k, v in obj.items():
                if isinstance(v,dict):
                    item = pop_key(v, key)
                    if item is not None:
                        return item

        def move_key(d, key, value, target):
            if isinstance(d, dict):
                keys = list(d.keys())
                for i, k in enumerate(keys):
                    if k == target:
                        idx = (
                            i
                            if direction < 0
                            else i+1
                        )
                        for kk in keys[:idx] + [key] + keys[idx:]:
                            d[kk] = d.pop(kk, value)
                    else:
                        move_key(d[k], key, value, target)


        for c in src:
            if c == dst:
                continue
            val = pop_key(self.feed_config, c)
            move_key(self.feed_config, c, val, dst)

        self.feed_config.save()
        self.load()
        try:
            key = self.find_key(src[0])
            self.listbox.set_focus(key)
        except:
            pass

    def rename_channel(self, identifier, name):

        if isinstance(identifier, tuple):
            node_type, key = identifier
            new_identifier = (node_type, name)
            new_key = getattr(config, node_type.title())(name)
        else:
            key = identifier
            node_type = None

        def rename_key(d, key):
            if isinstance(d, dict):
                for k in list(d.keys()):
                    if k == key:
                        if node_type:
                            for kk, vv in list(d.items()):
                                d[kk if kk != key else new_key] = d.pop(kk)
                        else:
                            d[k] = name
                    else:
                        rename_key(d[k], key)

        rename_key(self.feed_config, key)
        self.feed_config.save()
        self.load()
        focus = self.find_node(
            new_identifier
            if node_type
            else key
        )
        self.listbox.set_focus(focus)


    def rename_selection(self):


        channel = self.listbox.focus_position

        class RenameDialog(TextEditDialog):

            signals = ["rename"]

            @property
            def title(self):
                return "Rename feed"

            def action(self, value):
                self.parent.rename_channel(channel.identifier, value)

        orig_name = (
            channel.identifier[1]
            if isinstance(channel.identifier, tuple)
            else channel.name
        )

        dialog = RenameDialog(self, orig_value=orig_name)
        self.provider.view.open_popup(dialog, width=60, height=8)


    def delete_channel(self, locator):

        def remove_key(d, key):
            if isinstance(d, dict):
                for k in list(d.keys()):
                    if k == key:
                        del d[k]
                    else:
                        remove_key(d[k], key)

        remove_key(self.feed_config, locator)

    def delete_selection(self):

        self.open_delete_confirm_dialog(self.listbox.focus.channel)

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "enter":
            marked = list(self.tree.get_marked_nodes())
            if len(marked) <= 1:
                self.tree.unmark()
                self.selection.get_widget().mark()
            self.update_selection()
        elif key == "e":
            self.rename_selection()
        elif key == "V":
            self.move_channel([c.get_key() for c in self.tree.get_marked_nodes()], self.selection.get_key(), -1)
            self.tree.unmark()
        else:
            return key


    def find_path(self, path):
        return self.tree.find_path(path)

    def find_key(self, key):
        return self.tree.find_key(key)

    def cycle_unread(self, step=1):
        self.cycle(
            step,
            lambda n: n.get_widget().unread_count > 0 and not isinstance(n, ChannelGroupNode)
        )

    def cycle(self, step=1, pred=None):
        cur = self.listbox.body.get_focus()[1]
        for i in range(abs(step)):
            while True:
                cur = self.listbox.body.get_next(cur) if step > 0 else self.listbox.body.get_prev(cur)
                if not cur:
                    return
                cur = cur[1]
                if not cur:
                    return
                if not pred or (pred and pred(cur)):
                    break
        self.tree.unmark()
        self.listbox.set_focus(cur)
        self.selection.get_widget().mark()
        self.update_selection()
        return cur

    def advance(self, skip=False):
        self._emit("advance")

    #     while True:
    #         logger.info("advance")
    #         if self.selection.get_widget().unread_count > 0:
    #             if skip:
    #                 skip = False
    #             else:
    #                 break
    #         if not self.cycle():
    #             break
    #     self._emit("advance")
