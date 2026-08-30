"""Pane split container for lmux GUI — manages tiled terminal panes within a surface.

Uses a tree structure: SplitNode (orientation + left/right children) or
PaneLeaf (terminal widget). Operations build and traverse the tree so that
the split layout reflects the order splits were created.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import time


ORIENT_MAP = {
    "v": Gtk.Orientation.VERTICAL,
    "h": Gtk.Orientation.HORIZONTAL,
}


class _PaneLeaf:
    """Leaf node wrapping a TerminalWidget."""
    __slots__ = ("id", "widget")

    def __init__(self, pane_id, widget):
        self.id = pane_id
        self.widget = widget


class _SplitNode:
    """Internal node representing a split between two sub-trees."""
    __slots__ = ("orientation", "left", "right", "position")

    def __init__(self, orientation, left, right):
        self.orientation = orientation  # Gtk.Orientation
        self.left = left
        self.right = right
        self.position = None  # stored as Gtk.Paned ref on build


class PaneContainer(Gtk.Box):
    """Container that manages split terminal panes within a surface.

    Internally builds a tree of Gtk.Paned widgets from a tree of _SplitNode
    / _PaneLeaf. Operations: split, remove, focus-next, focus-prev, focus-dir.
    """

    def __init__(self, surface_id=None):
        super().__init__()
        self.surface_id = surface_id
        self._tree = None        # root: _PaneLeaf | _SplitNode
        self._panes = {}         # int pane_id -> _PaneLeaf
        self._pane_order = []    # ordered list of pane_ids (insertion order)
        self._focused = None     # focused pane_id
        self._prefix_count = 0

    # ── lifecycle ───────────────────────────────────────────

    def add_pane(self, pane_id, title=None):
        """Register a new terminal pane as the first/only pane in the surface."""
        from terminal import TerminalWidget

        if pane_id in self._panes:
            return self._panes[pane_id].widget

        if title is None:
            self._prefix_count += 1
            title = f"Pane {self._prefix_count}"

        term = TerminalWidget(title)
        term.pane_id = pane_id
        leaf = _PaneLeaf(pane_id, term)
        self._panes[pane_id] = leaf
        self._pane_order.append(pane_id)

        if self._tree is None:
            self._tree = leaf
        else:
            # Subsequent panes via add_pane (no explicit split) get
            # auto-tiled by alternating orientation per pane.
            self._tree = self._auto_split(self._tree, leaf)

        if self._focused is None:
            self._focused = pane_id

        self._rebuild_layout()
        self.show_all()
        return term

    def remove_pane(self, pane_id):
        """Remove a pane by id. Last pane is never removed."""
        if pane_id not in self._panes or len(self._panes) <= 1:
            return
        self._panes.pop(pane_id)
        self._pane_order.remove(pane_id)
        if self._focused == pane_id:
            self._focused = self._pane_order[0] if self._pane_order else None
        # Rebuild tree by removing the leaf
        self._tree = self._remove_leaf(self._tree, pane_id)
        self._rebuild_layout()

    # ── split ───────────────────────────────────────────────

    def split_pane(self, target_id, orientation="v", new_id=None, title=None):
        """Split pane target_id, adding a new pane alongside it.

        Returns the new TerminalWidget or None if target_id not found.
        """
        if target_id not in self._panes:
            return None
        from terminal import TerminalWidget

        if new_id is None:
            new_id = max(self._panes.keys()) + 1 if self._panes else 1
        if new_id in self._panes:
            return self._panes[new_id].widget

        if title is None:
            self._prefix_count += 1
            title = f"Pane {self._prefix_count}"

        new_term = TerminalWidget(title)
        new_term.pane_id = new_id
        new_leaf = _PaneLeaf(new_id, new_term)
        self._panes[new_id] = new_leaf
        self._pane_order.append(new_id)

        # Replace the target leaf with a SplitNode
        gtkor = ORIENT_MAP.get(orientation, Gtk.Orientation.VERTICAL)
        target_leaf = self._panes[target_id]
        # The new pane goes on the right; existing target on the left
        split = _SplitNode(gtkor, target_leaf, new_leaf)
        self._tree = self._replace_leaf(self._tree, target_id, split)

        self._focused = new_id
        self._rebuild_layout()
        self.show_all()
        return new_term

    def close_focused(self):
        """Close the currently focused pane."""
        if self._focused is not None:
            self.remove_pane(self._focused)

    def pane_count(self):
        return len(self._panes)

    # ── focus navigation ────────────────────────────────────

    def focus_pane(self, pane_id):
        if pane_id in self._panes:
            self._focused = pane_id

    def focus_next(self):
        if not self._pane_order:
            return
        cur = (
            self._pane_order.index(self._focused)
            if self._focused in self._pane_order
            else -1
        )
        nxt = (cur + 1) % len(self._pane_order)
        self._focused = self._pane_order[nxt]

    def focus_prev(self):
        if not self._pane_order:
            return
        cur = (
            self._pane_order.index(self._focused)
            if self._focused in self._pane_order
            else -1
        )
        prv = (cur - 1) % len(self._pane_order)
        self._focused = self._pane_order[prv]

    def focused_terminal(self):
        if self._focused in self._panes:
            return self._panes[self._focused].widget
        return None

    def iter_terminals(self):
        for pid in self._pane_order:
            yield self._panes[pid].widget

    # ── tree helpers ────────────────────────────────────────

    @staticmethod
    def _auto_split(left_node, right_leaf, depth=0):
        """Auto-tile a new leaf alongside the existing tree.

        Alternate orientation by depth so panes tile in a snaking grid.
        """
        ori = Gtk.Orientation.HORIZONTAL if depth % 2 == 0 else Gtk.Orientation.VERTICAL
        return _SplitNode(ori, left_node, right_leaf)

    @staticmethod
    def _replace_leaf(node, target_id, new_node):
        """Walk the tree and replace _PaneLeaf(target_id) with new_node."""
        if isinstance(node, _PaneLeaf):
            if node.id == target_id:
                return new_node
            return node
        # _SplitNode
        node.left = PaneContainer._replace_leaf(node.left, target_id, new_node)
        node.right = PaneContainer._replace_leaf(node.right, target_id, new_node)
        return node

    @staticmethod
    def _remove_leaf(node, target_id):
        """Walk the tree, remove _PaneLeaf(target_id), promote its sibling."""
        if isinstance(node, _PaneLeaf):
            return None if node.id == target_id else node
        # _SplitNode — recurse into both sides
        node.left = PaneContainer._remove_leaf(node.left, target_id)
        node.right = PaneContainer._remove_leaf(node.right, target_id)

        if node.left is None and node.right is None:
            return None
        if node.left is None:
            return node.right
        if node.right is None:
            return node.left
        return node

    @staticmethod
    def _find_leaf(node, pane_id):
        """Find a _PaneLeaf by pane_id, or None."""
        if isinstance(node, _PaneLeaf):
            return node if node.id == pane_id else None
        found = PaneContainer._find_leaf(node.left, pane_id)
        if found:
            return found
        return PaneContainer._find_leaf(node.right, pane_id)

    # ── layout mgmt ─────────────────────────────────────────

    def _rebuild_layout(self):
        """Rebuild the Gtk.Paned tree from the internal split tree."""
        for child in list(self.get_children()):
            self.remove(child)

        if self._tree is None:
            self.show_all()
            return

        paned = self._tree_to_widget(self._tree)
        self.pack_start(paned, True, True, 0)
        self.show_all()

    def _tree_to_widget(self, node):
        """Convert a _SplitNode / _PaneLeaf into a Gtk widget tree."""
        if isinstance(node, _PaneLeaf):
            return node.widget
        # _SplitNode
        left_w = self._tree_to_widget(node.left)
        right_w = self._tree_to_widget(node.right)
        p = Gtk.Paned(orientation=node.orientation)
        p.pack1(left_w, True, True)
        p.pack2(right_w, True, True)
        return p

    # ── equalize splits ──────────────────────────────────────

    def equalize_splits(self):
        """Equalize all split pane sizes to 50/50."""
        self._equalize_node(self._tree)

    def _equalize_node(self, node):
        """Recursively equalize split positions."""
        if node is None or isinstance(node, _PaneLeaf):
            return

        # Find the Paned widget for this split
        paned = node.position
        if paned and isinstance(paned, Gtk.Paned):
            # Get the allocation to calculate 50% position
            alloc = paned.get_allocation()
            if alloc.width > 0 and alloc.height > 0:
                if node.orientation == Gtk.Orientation.HORIZONTAL:
                    position = alloc.width // 2
                else:
                    position = alloc.height // 2
                paned.set_position(position)

        # Recurse into children
        self._equalize_node(node.left)
        self._equalize_node(node.right)

    # ── flash focused panel ──────────────────────────────────

    def flash_focused(self, duration_ms: int = 300):
        """Flash the focused pane with a visual indicator."""
        if self._focused is None or self._focused not in self._panes:
            return

        leaf = self._panes[self._focused]
        widget = leaf.widget

        # Get the style context
        style_ctx = widget.get_style_context()
        css_class = "pane-flash"

        # Add flash class
        style_ctx.add_class(css_class)

        # Remove after duration
        def remove_flash():
            style_ctx.remove_class(css_class)
            return False  # Don't repeat

        GLib.timeout_add(duration_ms, remove_flash)

    def flash_pane(self, pane_id: int, duration_ms: int = 300):
        """Flash a specific pane."""
        if pane_id not in self._panes:
            return

        leaf = self._panes[pane_id]
        widget = leaf.widget

        style_ctx = widget.get_style_context()
        css_class = "pane-flash"

        style_ctx.add_class(css_class)

        def remove_flash():
            style_ctx.remove_class(css_class)
            return False

        GLib.timeout_add(duration_ms, remove_flash)

    # ── get all paned widgets ────────────────────────────────

    def _collect_paned_widgets(self, node, paneds=None):
        """Collect all Gtk.Paned widgets from the tree."""
        if paneds is None:
            paneds = []

        if node is None or isinstance(node, _PaneLeaf):
            return paneds

        if node.position and isinstance(node.position, Gtk.Paned):
            paneds.append(node.position)
            # Store reference back to node for equalize
            node.position._split_node = node

        self._collect_paned_widgets(node.left, paneds)
        self._collect_paned_widgets(node.right, paneds)

        return paneds
