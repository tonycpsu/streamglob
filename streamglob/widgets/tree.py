import logging
logger = logging.getLogger(__name__)

import urwid
from panwid.highlightable import HighlightableTextMixin

class PositionsTreeWalker(urwid.TreeWalker):

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

class HighlightableTreeWidgetMixin(HighlightableTextMixin):

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


class ExpandableMixin(object):

    # apply an attribute to the expand/unexpand icons
    unexpanded_icon = urwid.AttrMap(
        urwid.TreeWidget.unexpanded_icon,
        "browser dirmark", "browser dirmark_focus"
    )
    expanded_icon = urwid.AttrMap(
        urwid.TreeWidget.expanded_icon,
        "browser dirmark", "browser dirmark_focus"
    )


    def __init__(self, node, expanded=None):
        super().__init__(node)
        if not expanded:
            expanded = self.get_node().starts_expanded

        self.expanded = expanded
        self.update_expanded_icon()


    def expand(self):
        self.get_node().get_parent().refresh()
        self.expanded = True
        self.update_expanded_icon()

    def collapse(self):
        self.expanded = False
        self.update_expanded_icon()

    def keypress(self, size, key):
        key = super().keypress(size, key)
        if key == "left":
            self.collapse()
        elif key == "right":
            self.expand()
        else:
            return key


class MarkableMixin(object):

    def __init__(self, node, marked=False):
        super().__init__(node)
        # insert an extra AttrWrap for our own use
        self._w = urwid.AttrMap(self._w, "browser normal")
        self.marked = marked
        self.update_w()

    def mark(self):
        self.marked = True
        self.update_w()
        if not self.is_leaf:
            for node in self.get_node().get_unmarked_nodes(shallow=True):
                node.get_widget().mark()

    def unmark(self):
        self.marked = False
        self.update_w()
        if not self.is_leaf:
            for node in self.get_node().get_marked_nodes(shallow=True):
                node.get_widget().unmark()

    def toggle_mark(self):
        if self.marked:
            self.unmark()
        else:
            self.mark()
        self.update_w()

    def mark_more(self):
        node = self.get_node()
        if not node.marked:
            node.mark()
            return
        else:
            node = node.get_parent()
            while node:
                try:
                    unmarked = next(node.get_unmarked_nodes(shallow=True))
                    node.mark()
                    break
                except StopIteration:
                    node = node.get_parent()

    def mark_all(self):
        self.get_node().root.get_widget().mark()

    def unmark_all(self):
        self.get_node().root.get_widget().unmark()

    def toggle_mark_all(self):
        root = self.get_node().root
        if root.marked:
            root.unmark()
        else:
            root.mark()

    def update_w(self):
        """Update the attributes of self.widget based on self.marked.
        """
        if self.marked:
            self._w.attr_map = {
                # None: "browser marked",
                "browser normal": "browser marked",
                "browser head": "browser head marked",
                "browser tail": "browser tail marked",
                "browser head_tail": "browser head_tail marked",
            }
            self._w.focus_map = {
                # None: "browser marked_focus",
                "browser normal": "browser marked_focus",
                "browser head": "browser head marked_focus",
                "browser tail": "browser tail marked_focus",
                "browser head_tail": "browser head_tail marked_focus",
            }
        else:
            self._w.attr_map = {
                # None: "browser normal",
                "browser normal": "browser normal",
                "browser head": "browser head",
                "browser tail": "browser tail",
                "browser head_tail": "browser head_tail",
            }
            self._w.focus_map = {
                # None: "browser focus",
                "browser normal": "browser focus",
                "browser head": "browser head focus",
                "browser tail": "browser tail focus",
                "browser head_tail": "browser head_tail focus",
            }

    def keypress(self, size, key):
        if key == " ":
            self.toggle_mark()
        elif key == ";":
            self.unmark_all()
        elif key == ":":
            self.mark_more()
        else:
            return super().keypress(size, key)


class AttributeTreeWidget(urwid.TreeWidget):

    @property
    def text(self):
        return self.get_node().get_key()

    def get_display_text(self):
        # return self.highlight_content
        return self.display_text

    @property
    def display_text(self):
        # logger.info(f"{self.attr}, {self.text}")
        return (self.attr, self.text)

    @property
    def attr(self):
        return "browser normal"

    def selectable(self):
        return True


class MarkedTreeWidget(MarkableMixin, HighlightableTreeWidgetMixin, AttributeTreeWidget):
    pass

class ExpandableMarkedTreeWidget(ExpandableMixin, MarkedTreeWidget):

    indent_cols = 2

    @property
    def selected_items(self):

        selection = self.get_node().tree.selection
        marked = list(self.get_node().get_marked_nodes(shallow=True))
        if marked:
            # ensure selection is first in the list
            return ([selection] if selection in marked else []) + [
                m for m in marked if m != selection
            ]
        else:
            return [selection]

class TreeNode(urwid.TreeNode):

    @property
    def root(self):
        if not self.get_parent():
            return self
        return self.get_parent().root

    @property
    def is_leaf(self):
        return True

    def mark(self):
        self.get_widget().mark()

    def unmark(self):
        self.get_widget().unmark()

    def toggle_mark(self):
        self.get_widget().toggle_mark()

    @property
    def marked(self):
        return self.get_widget().marked


class TreeParentNode(TreeNode, urwid.ParentNode):

    @property
    def starts_expanded(self):
        return self.get_depth() < 1

    @property
    def selected_items(self):
        return self.get_widget().selected_items

    @property
    def is_leaf(self):
        return False

    def get_nodes(self, pred=None, shallow=False):
        if shallow and not self._child_keys:
            return
        for key in self.get_child_keys():
            child = self.get_child_node(key)
            if not child.is_leaf:
                if pred is None or pred(child):
                    yield child
                yield from child.get_nodes(pred, shallow=shallow)
            if pred is None or pred(child):
                yield child

    def get_leaf_nodes(self):
        yield from self.get_nodes(
            lambda n: n.is_leaf
        )

    def get_leaf_keys(self):
        yield from (n.get_key() for n in self.get_leaf_nodes())

    def get_marked_nodes(self, shallow=False):
        yield from self.get_nodes(
            lambda n: n.marked,
            shallow=shallow
        )

    def get_unmarked_nodes(self, shallow=False):
        yield from self.get_nodes(
            lambda n: not n.marked,
            shallow=shallow
        )
