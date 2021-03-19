import logging
logger = logging.getLogger(__name__)

import itertools
import re
import os
from functools import partial

import urwid
import yaml
from pony.orm import *

from .. import config
from .. import model

class ChannelTreeWidget(urwid.TreeWidget):
    """ Display widget for leaf nodes """


    def get_display_text(self):
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
        # elif key:
        #     return value.get(key, f"no value: {key}")
        # else:
        #     return "no key"
        # return value.get("name", "none")

    def selectable(self):
        return True

    def keypress(self, size, key):

        if key == " ":
            self.toggle_mark()
        elif self._w.selectable():
            return self.__super.keypress(size, key)
        else:
            return key


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

class ChannelWidget(MarkableMixin, ChannelTreeWidget):

    @property
    def channel(self):
        with db_session:
            return model.MediaChannel.get(
                provider_id=self.provider.IDENTIFIER,
                locator=self.get_node().locator
            )

    @property
    def name(self):
        return super().get_display_text()

    @property
    def text(self):
        if self.unread_count:
            return f"{self.name} ({self.unread_count})"
        else:
            return self.name

    @property
    def unread_count(self):
        return self.channel.unread_count if self.channel else None

    @property
    def provider(self):
        return self.get_node().get_parent().tree.provider

    @property
    def attr(self):
        tail = self.channel.attrs.get("tail_fetched") if self.channel else None
        if self.unread_count and tail:
            return "browser head_tail"
        elif self.unread_count:
            return "browser head"
        elif tail:
            return "browser tail"
        else:
            return "browser normal"

    def get_display_text(self):
        return (self.attr, self.text)

    def keypress(self, size, key):
        if key == "enter":
            self.mark()
        return super().keypress(size, key)

class ChannelUnionWidget(ChannelWidget):

    # indent_cols = 0
    # unexpanded_icon = urwid.SelectableIcon(" ", 0)

    def __init__(self, node):
        super().__init__(node)
        self.is_leaf = True
        self.expanded = False
        self.update_expanded_icon()

    @property
    def unread_count(self):
        return sum([
            n.get_widget().unread_count
            for n in self.get_node().get_nodes()
        ])


    def get_indented_widget(self):
        widget = self.get_inner_widget()
        if not self.is_leaf:
            widget = urwid.Columns([('fixed', 0,
                [self.unexpanded_icon, self.expanded_icon][self.expanded]),
                widget], dividechars=1)
        indent_cols = self.get_indent_cols()
        return urwid.Padding(widget,
            width=('relative', 100), left=indent_cols)



    def update_expanded_icon(self):
        return


class ChannelGroupWidget(MarkableMixin, ChannelTreeWidget):

    indent_cols = 3

    # apply an attribute to the expand/unexpand icons
    unexpanded_icon = urwid.AttrMap(
        urwid.TreeWidget.unexpanded_icon,
        "browser dirmark", "browser dirmark_focus"
    )
    expanded_icon = urwid.AttrMap(
        urwid.TreeWidget.expanded_icon,
        "browser dirmark", "browser dirmark_focus")

    def get_display_text(self):
        return self.get_node().get_key() or "none"

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

    @property
    def locator(self):
        return self.get_key()

    @property
    def name(self):
        value = self.get_value()
        if isinstance(value, dict):
            name = value.get("name", None)
        elif isinstance(value, str):
            return value
        return self.locator

    @property
    def attrs(self):
        value = self.get_value()
        if not isinstance(value, dict):
            return {}
        value.pop("name", None)
        return value

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
        return ("group", self.get_key())


class ChannelTreeBrowser(urwid.WidgetWrap):

    signals = ["change", "select"]

    def __init__(self, data, provider, label="channels"):
        self.provider = provider
        self.tree = ChannelGroupNode(self, data, key=label)
        self.listbox = urwid.TreeListBox(urwid.TreeWalker(self.tree))
        self.listbox.offset_rows = 1
        super().__init__(self.listbox)

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

    def keypress(self, size, key):

        if key == "enter":
            marked = list(self.tree.get_marked_nodes())
            if len(marked) <= 1:
                self.unmark_all()
                self.selection.get_widget().mark()
            self.update_selection()
        elif key == " ":
            super().keypress(size, key)
            self._emit("change", self.selected_items)
        elif key == ";":
            marked = list(self.tree.get_marked_nodes())
            if marked:
                self.unmark_all()
            else:
                self.mark_all()
        else:
            return super().keypress(size, key)

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

def main():
    config.load("channels.yml")
    ChannelTreeBrowser({"channels/": config.settings}).main()


if __name__=="__main__":
    main()
