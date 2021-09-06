import logging
logger = logging.getLogger(__name__)

import itertools
import re
import os
from functools import partial

import urwid
import yaml
from pony.orm import *
from panwid.autocomplete import AutoCompleteMixin
from panwid.highlightable import HighlightableTextMixin
from panwid.keymap import *
from panwid.dialog import BaseDialog, BasePopUp
from unidecode import unidecode

from .. import config
from .. import model
from .. import utils
from ..widgets import StreamglobScrollBar, SquareButton

from ..state import *

class ChannelTreeWidget(HighlightableTextMixin, urwid.TreeWidget):
    """ Display widget for leaf nodes """

    def get_display_text(self):
        # return self.highlight_content
        return self.display_text

    @property
    def display_text(self):
        return (self.attr, self.text)

    @property
    def attr(self):
        return "browser normal"

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

    def keypress(self, size, key):
        if key == " ":
            self.toggle_mark()
        elif self._w.selectable():
            return self.__super.keypress(size, key)
        else:
            return key

    @property
    def highlight_source(self):
        return self._innerwidget.text

    @property
    def highlightable_attr_normal(self):
        return self.attr

    @property
    def highlightable_attr_highlight(self):
        return "browser highlight"

    def on_highlight(self):
        self._innerwidget.set_text(self.highlight_content)

    def on_unhighlight(self):
        self._innerwidget.set_text(self.display_text)

    def __str__(self):
        return self._innerwidget.text


class MarkableMixin(object):

    def __init__(self, node):
        super().__init__(node)
        # insert an extra AttrWrap for our own use
        self._w = urwid.AttrMap(self._w, "browser_normal")
        self.marked = False
        self.update_w()

    def mark(self):
        self.marked = True
        self.update_w()

    def unmark(self):
        self.marked = False
        self.update_w()

    def toggle_mark(self):
        if self.marked:
            self.unmark()
        else:
            self.mark()

    def update_w(self):
        """Update the attributes of self.widget based on self.marked.
        """
        if self.marked:
            self._w.attr_map = {
                None: "browser normal",
                "browser normal": "browser marked",
                "browser head": "browser head marked",
                "browser tail": "browser tail marked",
                "browser head_tail": "browser head_tail marked",
            }
            self._w.focus_map = {
                "browser normal": "browser marked_focus",
                "browser head": "browser head marked_focus",
                "browser tail": "browser tail marked_focus",
                "browser head_tail": "browser head_tail marked_focus",
            }
        else:
            self._w.attr_map = {
                None: "browser normal",
                "browser normal": "browser normal",
                "browser head": "browser head",
                "browser tail": "browser tail",
                "browser head_tail": "browser head_tail",
            }
            self._w.focus_map = {
                # None: "browser normal",
                "browser normal": "browser focus",
                "browser head": "browser head focus",
                "browser tail": "browser tail focus",
                "browser head_tail": "browser head_tail focus",
            }


class ListingCountMixin(object):

    @property
    def listing_count(self):
        return self.channel.listing_count if self.channel else None

    @property
    def unread_count(self):
        return self.channel.unread_count if self.channel else None

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
             ),
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
    def provider(self):
        return self.get_node().get_parent().tree.provider

    @property
    def attr(self):
        tail = self.channel.attrs.get("tail_fetched") if self.channel else None
        error = self.channel and self.channel.attrs.get("error")
        if error:
            return "browser error"
        elif self.unread_count and tail:
            return "browser head_tail"
        elif self.unread_count:
            return "browser head"
        elif tail:
            return "browser tail"
        else:
            return "browser normal"

    def keypress(self, size, key):
        return super().keypress(size, key)
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


class ChannelGroupWidget(AggregateListingCountMixin, MarkableMixin, ChannelTreeWidget):

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
            return value.get("name", None)
        elif isinstance(value, str):
            return value
        return self.locator

    @property
    def attrs(self):
        value = self.get_value()
        if not isinstance(value, dict):
            return {}
        return {k: v for k, v in value.items() if k != "name"}

class ChannelNode(ChannelPropertiesMixin, urwid.TreeNode):

    @property
    def is_leaf(self):
        return True

    def load_widget(self):
        return ChannelWidget(self)

    @property
    def identifier(self):
        return self.get_key()

    @property
    def marked(self):
        return self.get_widget().marked



class ChannelUnionNode(ChannelPropertiesMixin, urwid.ParentNode):

    def __init__(self, tree, value, parent=None, key=None, depth=None):
        self.tree = tree
        super().__init__(value, parent=parent, key=key, depth=depth)

    @property
    def is_leaf(self):
        return False

    def load_widget(self):
        return ChannelUnionWidget(self)

    @property
    def identifier(self):
        return self.get_key()

    @property
    def marked(self):
        return self.get_widget().marked

    def get_nodes(self, pred=None):
        for key in self.get_child_keys():
            child = self.get_child_node(key)
            if not child.is_leaf:
                if pred is None or pred(child):
                    yield child
                yield from child.get_nodes(pred)
            if pred is None or pred(child):
                yield child

    def get_leaf_nodes(self):
        yield from self.get_nodes(
            lambda n: n.is_leaf
        )

    def get_leaf_keys(self):
        yield from (n.get_key() for n in self.get_leaf_nodes())

    def get_marked_nodes(self):
        yield from self.get_nodes(
            lambda n: n.marked
        )

    def load_child_keys(self):
        data = self.get_value()
        if not data:
            return []
        try:
            return list(data.keys())
        except AttributeError:
            raise Exception(data)

    def load_child_node(self, key):

        childdata = self.get_value()
        childdepth = self.get_depth() + 1
        if isinstance(key, config.Folder):
            return ChannelGroupNode(self.tree, childdata[key], parent=self, key=key, depth=childdepth)
        elif isinstance(key, config.Union):
            return ChannelUnionNode(self.tree, childdata[key], parent=self, key=key, depth=childdepth)
            # node.collapse()
            # return node
        else:
            return ChannelNode(childdata[key], parent=self, key=key, depth=childdepth)
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



class MyTreeWalker(urwid.TreeWalker):

    def positions(self, reverse=False):

        widget, pos = self.get_focus()
        rootnode = pos.get_root()
        if reverse:
            rootwidget = rootnode.get_widget()
            lastwidget = rootwidget.last_child()
            if lastwidget:
                first = lastwidget.get_node()
            else:
                return
        else:
            first = rootnode

        pos = first
        while pos:
            yield pos
            if reverse:
                pos = self.get_prev(pos)[1]
            else:
                pos = self.get_next(pos)[1]


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
        self.pile = urwid.Pile([
            ("weight", 1, self.placeholder)
            # ("weight", 1, self.listbox)
        ])
        super().__init__(self.pile)
        self.pile.selectable = lambda: True
        self.load()

    @property
    def conf(self):
        if not getattr(self, "_conf", None):
            self._conf = config.Config(
            os.path.join(
                config.settings._config_dir,
                self.provider.IDENTIFIER,
                "feeds.yaml"
            )
        )
        return self._conf

    @conf.setter
    def conf(self, conf):
        self._conf = conf

    def load(self):
        self.tree = ChannelGroupNode(self, self.conf, key=self.label)
        self.listbox = MyTreeListBox(MyTreeWalker(self.tree))
        self.listbox.offset_rows = 1
        self.scrollbar = StreamglobScrollBar(self.listbox)
        self.placeholder.original_widget = self.scrollbar
        self.pile._invalidate()

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
    def complete_body(self):
        return self.listbox.body

    def complete_widget_at_pos(self, pos):
        return pos.get_widget()

    def on_complete_select(self, pos):
        return
        self.update_selection()

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

    def mark_all(self):
        self.tree.get_widget().mark()

    def unmark_all(self):
        self.tree.get_widget().unmark()

    @property
    def all_channels(self):
        return self.tree.get_leaf_nodes()

    @property
    def selected_items(self):

        marked = list(self.tree.get_marked_nodes())

        if marked:
            # selection = [node.identifier for node in marked]
            selection = marked
        else:
            selection = [self.selection]

        return selection

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
        self._emit("change", self.selected_items)
        self._emit("select", self.selected_items)

    def open_confirm_dialog(self, prompt, action):

        class ConfirmDialog(BaseDialog):

            signals = ["message"]

            def __init__(self, parent, action, *args, **kwargs):

                self.action = action
                super(ConfirmDialog, self).__init__(parent, *args, **kwargs)

            @property
            def prompt(self):
                return prompt

            def confirm(self):
                self.action()
                self.close()

            def cancel(self):
                self.close()

            def close(self):
                self.parent.close_popup()

            @property
            def choices(self):
                return {
                    "y": self.confirm,
                    "n": self.cancel
                }

        dialog = ConfirmDialog(self.provider.view, action)
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
            val = pop_key(self.conf, c)
            move_key(self.conf, c, val, dst)

        self.conf.save()
        self.load()
        key = self.find_key(src[0])
        self.listbox.set_focus(key)

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

        rename_key(self.conf, key)
        self.conf.save()
        self.load()
        focus = self.find_node(
            new_identifier
            if node_type
            else key
        )
        self.listbox.set_focus(focus)


    def rename_selection(self):

        channel = self.listbox.focus_position

        class RenameDialog(BasePopUp):

            signals = ["rename"]

            def __init__(self, channel):
                self.channel = channel

                self.orig_name = (
                    self.channel.identifier[1]
                    if isinstance(self.channel.identifier, tuple)
                    else self.channel.name
                )
                self.edit = urwid.Edit(
                    caption=("bold", "Name: "),
                    edit_text=self.orig_name
                )
                self.ok_button = SquareButton(("bold", "OK"))

                urwid.connect_signal(
                    self.ok_button, "click",
                    lambda s: self.confirm()
                )

                self.cancel_button = SquareButton(("bold", "Cancel"))

                urwid.connect_signal(
                    self.cancel_button, "click",
                    lambda s: self.cancel()
                )

                self.pile = urwid.Pile(
                    [
                        (2, urwid.Filler(urwid.Padding(self.edit), valign="top")),
                        ("weight", 1, urwid.Padding(
                            urwid.Columns([
                                ("weight", 1,
                                 urwid.Padding(
                                     self.ok_button, align="center", width=12)
                                 ),
                                ("weight", 1,
                                 urwid.Padding(
                                     self.cancel_button, align="center", width=12)
                                 )
                            ]),
                            align="center"
                        )),
                    ]
                )
                self.pile.selectable = lambda: True
                self.pile.focus_position = 0
                super(RenameDialog, self).__init__(urwid.Filler(self.pile))

            def confirm(self):
                new_name = self.edit.get_edit_text()
                if new_name != self.orig_name:
                    self._emit("rename", new_name)
                self.close()

            def cancel(self):
                self.close()

            def close(self):
                # self.provider.view.close_popup()
                self._emit("close_popup")

            def selectable(self):
                return True

            def keypress(self, size, key):
                key = super().keypress(size, key)
                if key == "enter":
                    self.confirm()
                else:
                    return key

        dialog = RenameDialog(channel)
        urwid.connect_signal(
            dialog, "rename",
            lambda source, name: self.rename_channel(
                channel.identifier, name
            )
        )
        self.provider.view.open_popup(dialog, width=60, height=5)


    def delete_channel(self, locator):

        def remove_key(d, key):
            if isinstance(d, dict):
                for k in list(d.keys()):
                    if k == key:
                        del d[k]
                    else:
                        remove_key(d[k], key)

        remove_key(self.conf, locator)

    def delete_selection(self):

        channel = self.listbox.focus.channel

        def action():

            target = self.listbox.focus_position
            try:
                new_selection = self.listbox.body.get_next(target)[1].identifier
            except IndexError:
                new_selection = self.listbox.body.get_prev(target)[1].identifier

            self.delete_channel(channel.locator)
            self.conf.save()
            self.load()
            self.listbox.set_focus(self.find_node(new_selection))

        self.open_confirm_dialog(
            f"""Delete "{channel.name}"?""",
            action
        )

    def keypress(self, size, key):

        key = super().keypress(size, key)
        if key == "enter":
            marked = list(self.tree.get_marked_nodes())
            if len(marked) <= 1:
                self.unmark_all()
                self.selection.get_widget().mark()
            self.update_selection()
        elif key == " ":
            self._emit("change", self.selected_items)
        elif key == ";":
            marked = list(self.tree.get_marked_nodes())
            if marked:
                self.unmark_all()
            else:
                self.mark_all()
        elif key == "e":
            self.rename_selection()
        elif key == "V":
            self.move_channel([c.get_key() for c in self.tree.get_marked_nodes()], self.selection.get_key(), -1)
            self.unmark_all()
        else:
            return key


    def find_path(self, path):
        return self.tree.find_path(path)

    def find_key(self, key):
        return self.tree.find_key(key)

    def cycle(self, step=1):
        focus = self.listbox.body.get_focus()[1]
        for i in range(abs(step)):
            nxt = self.listbox.body.get_next(focus) if step > 0 else self.listbox.body.get_prev(focus)
            if not nxt:
                return
            focus = nxt[1]
            if not focus:
                return
        self.unmark_all()
        self.listbox.set_focus(focus)
        self.selection.get_widget().mark()
        self.update_selection()
        return focus

    def advance(self):
        while True:
            if self.selection.get_widget().unread_count > 0:
                break
            if not self.cycle():
                break
        self._emit("advance")
