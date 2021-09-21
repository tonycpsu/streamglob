import urwid

class TreeParentNode(urwid.ParentNode):

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
