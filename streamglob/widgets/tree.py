import urwid
from panwid.highlightable import HighlightableTextMixin

class FancyTreeWalker(urwid.TreeWalker):

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
            # if not pos.hidden:
            yield pos
            if reverse:
                pos = self.get_prev(pos)[1]
            else:
                pos = self.get_next(pos)[1]

    def __getitem__(self, pos):
        if not pos:
            raise IndexError
        return pos.get_widget()

    def get_prev(self, position):
        return urwid.ListWalker.get_prev(self, position)

    def get_next(self, position):
        return urwid.ListWalker.get_next(self, position)

    def prev_position(self, position):
        target = position.get_widget()
        while True:
            target = target.prev_inorder()
            if target is None:
                return None
            elif target.get_node().hidden:
                continue
            else:
                return target.get_node()

    def next_position(self, position):
        target = position.get_widget()
        while True:
            target = target.next_inorder()
            if target is None:
                return None
            elif target.get_node().hidden:
                continue
            else:
                return target.get_node()


class StickyFocusFancyTreeWalker(FancyTreeWalker):

    NORMAL_ATTR = "tree normal"
    FOCUS_ATTR = "tree selected"

    def set_focus(self, position):
        if self.focus:
            w = self.focus.get_widget()
            w.set_text((self.NORMAL_ATTR, w.get_text()[0]))
        w = position.get_widget()
        w.set_text((self.FOCUS_ATTR, w.get_text()[0]))
        super().set_focus(position)

class HighlightableTreeWidgetMixin(HighlightableTextMixin):

    @property
    def highlight_source(self):
        return self._innerwidget.text

    @property
    def highlightable_attr_normal(self):
        return self.attr

    @property
    def highlightable_attr_highlight(self):
        return "tree highlight"

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
        "tree dirmark", "tree dirmark_focus"
    )
    expanded_icon = urwid.AttrMap(
        urwid.TreeWidget.expanded_icon,
        "tree dirmark", "tree dirmark_focus"
    )


    def __init__(self, node, expanded=None):
        super().__init__(node)
        if not expanded:
            expanded = self.get_node().starts_expanded

        self.expanded = expanded
        self.update_expanded_icon()

    def expand(self):
        self.get_node().refresh()
        # if self.get_node().get_parent():
        #     self.get_node().get_parent().refresh()
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
        self._w = urwid.AttrMap(self._w, "tree normal")
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
        # FIXME
        if self.marked:
            self._w.attr_map = {
                # None: "tree marked",
                "tree normal": "tree marked",
                "tree head": "tree head marked",
                "tree tail": "tree tail marked",
                "tree head_tail": "tree head_tail marked",
                "tree dormant": "tree dormant marked",
            }
            self._w.focus_map = {
                # None: "tree marked_focus",
                "tree normal": "tree marked_focus",
                "tree selected": "tree selected marked_focus",
                "tree head": "tree head marked_focus",
                "tree tail": "tree tail marked_focus",
                "tree head_tail": "tree head_tail marked_focus",
                "tree dormant": "tree dormant marked_focus",
            }
        else:
            self._w.attr_map = {
                # None: "tree normal",
                "tree normal": "tree normal",
                "tree head": "tree head",
                "tree tail": "tree tail",
                "tree head_tail": "tree head_tail",
                "tree dormant": "tree dormant",
            }
            self._w.focus_map = {
                # None: "tree focus",
                "tree normal": "tree focus",
                "tree selected": "tree selected focus",
                "tree head": "tree head focus",
                "tree tail": "tree tail focus",
                "tree head_tail": "tree head_tail focus",
                "tree dormant": "tree dormant focus",
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

    def __init__(self, *args, **kwargs):
        # self._attr = self.default_attr
        self.reset_attr()
        super().__init__(*args, **kwargs)

    def reset_attr(self):
        self._attr = self.default_attr

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

    def get_text(self):
        return self._innerwidget.get_text()

    def set_text(self, text):
        self._innerwidget.set_text(text)

    @property
    def default_attr(self):
        return "tree normal"

    @property
    def attr(self):
        return self._attr

    @attr.setter
    def attr(self, value):
        logger.info(value)
        self._attr = value

    def selectable(self):
        return True


class MarkedTreeWidget(MarkableMixin, HighlightableTreeWidgetMixin, AttributeTreeWidget):
    pass

class ExpandableMarkedTreeWidget(ExpandableMixin, MarkedTreeWidget):

    indent_cols = 2

    @property
    def selected_items(self):

        selection = self.get_node().tree.selection
        marked = self.marked_items

        if marked:
            # ensure selection is first in the list
            return ([selection] if selection in marked else []) + [
                m for m in marked if m != selection
            ]
        else:
            return [selection]

    @property
    def marked_items(self):
        return list(self.get_node().get_marked_nodes(shallow=True))


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

    def get_parents(self):

        node = self.get_parent()
        while node is not None:
            yield node
            node = node.get_parent()

    @property
    def hidden(self):
        return False



class TreeParentNode(TreeNode, urwid.ParentNode):

    @property
    def starts_expanded(self):
        return False

    @property
    def selected_items(self):
        return self.get_widget().selected_items

    @property
    def marked_items(self):
        return self.get_widget().marked_items

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
