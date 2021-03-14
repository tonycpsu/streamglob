import logging
logger = logging.getLogger(__name__)

import itertools
import re
import os
from functools import partial

import urwid
import yaml

from .. import config


class ChannelTreeWidget(urwid.TreeWidget):
    """ Display widget for leaf nodes """

    unexpanded_icon = urwid.AttrMap(urwid.TreeWidget.unexpanded_icon,
        'browser_dirmark')
    expanded_icon = urwid.AttrMap(urwid.TreeWidget.expanded_icon,
        'browser_dirmark')

    def __init__(self, node):
        super().__init__(node)
        # insert an extra AttrWrap for our own use
        self._w = urwid.AttrWrap(self._w, None)
        self.flagged = False
        self.update_w()

    def get_display_text(self):
        key = self.get_node().get_key()
        value = self.get_node().get_value()
        if not value:
            return key
        elif isinstance(value, str):
            return value
        else:
            return value.get("name", "no name")
        # elif key:
        #     return value.get(key, f"no value: {key}")
        # else:
        #     return "no key"
        # return value.get("name", "none")

    def selectable(self):
        return True

    def keypress(self, size, key):

        if self.is_leaf:
            return key
        elif key == "right":
            self.get_node().expand()
        elif key == "left":
            self.get_node().collapse()
        elif key == " ":
            self.flagged = not self.flagged
            self.update_w()
        elif self._w.selectable():
            return self.__super.keypress(size, key)
        else:
            return key

    def update_w(self):
        """Update the attributes of self.widget based on self.flagged.
        """
        if self.flagged:
            self._w.attr = 'flagged'
            self._w.focus_attr = 'flagged focus'
        else:
            self._w.attr = 'browser_body'
            self._w.focus_attr = 'browser_focus'

class ChannelWidget(ChannelTreeWidget):
    pass

class ChannelGroupWidget(ChannelTreeWidget):
    # apply an attribute to the expand/unexpand icons
    unexpanded_icon = urwid.AttrMap(urwid.TreeWidget.unexpanded_icon,
        'dirmark')
    expanded_icon = urwid.AttrMap(urwid.TreeWidget.expanded_icon,
        'dirmark')

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
        Default behavior: Toggle flagged on space, ignore other keys.
        """
        if key == " ":
            self.flagged = not self.flagged
            self.update_w()
        else:
            return key



class ChannelNode(urwid.TreeNode):

    def load_widget(self):
        return ChannelWidget(self)


class ChannelGroupNode(urwid.ParentNode):

    def load_widget(self):
        return ChannelGroupWidget(self)

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
            childclass = ChannelGroupNode
        else:
            childclass = ChannelNode
        return childclass(childdata[key], parent=self, key=key, depth=childdepth)

    def leaf_values(self):
        for key in self.get_child_keys():
            child = self.get_child_node(key)
            if isinstance(child, urwid.ParentNode):
                yield from child.leaf_values()
            else:
                yield(child.get_key())


    def expand(self):
        self.get_widget().expanded = True
        self.get_widget().update_expanded_icon()

    def collapse(self):
        self.get_widget().expanded = False
        self.get_widget().update_expanded_icon()

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


class ChannelTreeBrowser(urwid.WidgetWrap):

    signals = ["change"]

    palette = [
        ('body', 'light gray', 'black'),
        ('focus', 'light green', 'black', 'standout'),
        ('flagged', 'black', 'dark green', ('bold','underline')),
        ('focus', 'light gray', 'dark blue', 'standout'),
        ('flagged focus', 'yellow', 'dark cyan',
                ('bold','standout','underline')),
        ('head', 'yellow', 'black', 'standout'),
        ('foot', 'light gray', 'black'),
        ('key', 'light cyan', 'black','underline'),
        ('title', 'white', 'black', 'bold'),
        ('flag', 'dark gray', 'light gray'),
        ('error', 'dark red', 'light gray'),
        ]

    footer_text = [
        ('title', "Example Data Browser"), "    ",
        ('key', "UP"), ",", ('key', "DOWN"), ",",
        ('key', "PAGE UP"), ",", ('key', "PAGE DOWN"),
        "  ",
        ('key', "+"), ",",
        ('key', "-"), "  ",
        ('key', "LEFT"), "  ",
        ('key', "HOME"), "  ",
        ('key', "END"), "  ",
        ('key', "Q"),
        ]

    def __init__(self, data, label="channels"):
        self.tree = ChannelGroupNode(data, key=label + "/")
        self.listbox = urwid.TreeListBox(urwid.TreeWalker(self.tree))
        self.listbox.offset_rows = 1
        super().__init__(self.listbox)

    @property
    def body(self):
        return self.listbox.body

    @property
    def selection(self):
        return self.body.get_focus()[1]

    def keypress(self, size, key):

        node = self.selection

        if key == "enter":
            if isinstance(node, ChannelNode):
                # channels = [node.get_key()]
                channel = node.get_key()
            else:
                channel = ("channel", node.get_key())
                # channels = list(node.leaf_values())

            self._emit("change", channel)
        else:
            return super().keypress(size, key)

    @selection.setter
    def set_selection(self, path):
        self.find_path(path)

    def find_path(self, path):
        return self.tree_root.find_path(path)


def get_example_tree(root):
    """ generate a quick leaf tree for demo purposes """
    retval = {"name":"parent","children":[]}
    for i in range(11):
        retval['children'].append({"name":"child " + str(i)})
        retval['children'][i]['children']=[]
        for j in range(5):
            retval['children'][i]['children'].append({"name":"grandchild " +
                                                      str(i) + "." + str(j)})
    return retval


def main():
    config.load("channels.yml")
    ChannelTreeBrowser({"channels/": config.settings}).main()


if __name__=="__main__":
    main()
