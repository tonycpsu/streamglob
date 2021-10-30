import logging
logger = logging.getLogger(__name__)

import urwid

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


class MarkableMixin(object):

    def __init__(self, node):
        super().__init__(node)
        # insert an extra AttrWrap for our own use
        self._w = urwid.AttrMap(self._w, "browser normal")
        self.marked = False
        self.update_w()

    def mark(self):
        self.marked = True
        self.update_w()
        if not self.is_leaf:
            for node in self.get_node().get_nodes():
                node.get_widget().mark()

    def unmark(self):
        self.marked = False
        self.update_w()
        if not self.is_leaf:
            for node in self.get_node().get_nodes():
                node.get_widget().unmark()

    def toggle_mark(self):
        if self.marked:
            self.unmark()
        else:
            self.mark()
        self.update_w()

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

    def mark_more(self):
        node = self.get_node()
        if not node.marked:
            node.mark()
            return
        else:
            node = node.get_parent()
            while node:
                try:
                    unmarked = next(node.get_unmarked_nodes())
                    node.mark()
                    break
                except StopIteration:
                    node = node.get_parent()

    def keypress(self, size, key):
        if key == " ":
            self.toggle_mark()
        elif key == "<":
            self.mark_more()
        elif key == ";":
            self.toggle_mark_all()
        else:
            return super().keypress(size, key)

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
    def is_leaf(self):
        return False

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

    def get_unmarked_nodes(self):
        yield from self.get_nodes(
            lambda n: not n.marked
        )
