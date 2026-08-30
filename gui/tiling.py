"""Tiling window manager for lmux — automatic pane layout management."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("lmux.tiling")


class TilingLayout(Enum):
    """Available tiling layouts."""
    GRID = "grid"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    MONOCLE = "monocle"
    TALL = "tall"
    WIDE = "wide"
    CENTERED = "centered"


@dataclass
class PaneInfo:
    """Information about a pane for tiling."""
    widget: Gtk.Widget
    pane_id: str
    workspace_id: str
    surface_id: str
    visible: bool = True


class TilingManager:
    """Manages tiling layouts for panes in a workspace."""

    def __init__(self, container: Gtk.Container):
        self._container = container
        self._panes: List[PaneInfo] = []
        self._current_layout = TilingLayout.GRID
        self._gap = 4  # Gap between panes in pixels

    def set_layout(self, layout: TilingLayout):
        """Set the tiling layout and reapply."""
        self._current_layout = layout
        self.tile()

    def get_layout(self) -> TilingLayout:
        """Get the current tiling layout."""
        return self._current_layout

    def add_pane(self, pane: PaneInfo):
        """Add a pane to the tiling manager."""
        self._panes.append(pane)
        self.tile()

    def remove_pane(self, pane_id: str):
        """Remove a pane from the tiling manager."""
        self._panes = [p for p in self._panes if p.pane_id != pane_id]
        self.tile()

    def clear_panes(self):
        """Clear all panes."""
        self._panes.clear()

    def tile(self):
        """Apply the current tiling layout."""
        visible_panes = [p for p in self._panes if p.visible]
        if not visible_panes:
            return

        # Get container allocation
        alloc = self._container.get_allocation()
        if alloc.width <= 0 or alloc.height <= 0:
            return

        width = alloc.width
        height = alloc.height

        layout = self._current_layout

        if layout == TilingLayout.GRID:
            self._tile_grid(visible_panes, width, height)
        elif layout == TilingLayout.HORIZONTAL:
            self._tile_horizontal(visible_panes, width, height)
        elif layout == TilingLayout.VERTICAL:
            self._tile_vertical(visible_panes, width, height)
        elif layout == TilingLayout.MONOCLE:
            self._tile_monocle(visible_panes, width, height)
        elif layout == TilingLayout.TALL:
            self._tile_tall(visible_panes, width, height)
        elif layout == TilingLayout.WIDE:
            self._tile_wide(visible_panes, width, height)
        elif layout == TilingLayout.CENTERED:
            self._tile_centered(visible_panes, width, height)

    def _tile_grid(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes in a grid layout."""
        n = len(panes)
        if n == 0:
            return

        # Calculate grid dimensions
        cols = int(n ** 0.5)
        if cols * cols < n:
            cols += 1
        rows = (n + cols - 1) // cols

        cell_w = (width - self._gap * (cols + 1)) // cols
        cell_h = (height - self._gap * (rows + 1)) // rows

        for i, pane in enumerate(panes):
            row = i // cols
            col = i % cols

            x = self._gap + col * (cell_w + self._gap)
            y = self._gap + row * (cell_h + self._gap)

            pane.widget.set_size_request(cell_w, cell_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

    def _tile_horizontal(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes horizontally (side by side)."""
        n = len(panes)
        if n == 0:
            return

        cell_w = (width - self._gap * (n + 1)) // n
        cell_h = height - self._gap * 2

        for i, pane in enumerate(panes):
            x = self._gap + i * (cell_w + self._gap)
            y = self._gap

            pane.widget.set_size_request(cell_w, cell_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

    def _tile_vertical(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes vertically (stacked)."""
        n = len(panes)
        if n == 0:
            return

        cell_w = width - self._gap * 2
        cell_h = (height - self._gap * (n + 1)) // n

        for i, pane in enumerate(panes):
            x = self._gap
            y = self._gap + i * (cell_h + self._gap)

            pane.widget.set_size_request(cell_w, cell_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

    def _tile_monocle(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes in monocle layout (only active pane visible)."""
        # In monocle, only show the first pane
        for i, pane in enumerate(panes):
            if i == 0:
                pane.widget.set_size_request(width - self._gap * 2, height - self._gap * 2)
                pane.widget.set_margin_start(self._gap)
                pane.widget.set_margin_top(self._gap)
                pane.widget.show_all()
            else:
                pane.widget.hide()

    def _tile_tall(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes in tall layout (main pane on left, others stacked on right)."""
        n = len(panes)
        if n == 0:
            return

        if n == 1:
            self._tile_monocle(panes, width, height)
            return

        # Main pane takes 2/3 width
        main_w = int(width * 2 / 3) - self._gap * 2
        side_w = width - main_w - self._gap * 3

        # Stack side panes vertically
        side_h = (height - self._gap * (n + 1)) // (n - 1)

        # Main pane
        pane = panes[0]
        pane.widget.set_size_request(main_w, height - self._gap * 2)
        pane.widget.set_margin_start(self._gap)
        pane.widget.set_margin_top(self._gap)
        pane.widget.show_all()

        # Side panes
        for i, pane in enumerate(panes[1:], 1):
            x = main_w + self._gap * 2
            y = self._gap + (i - 1) * (side_h + self._gap)

            pane.widget.set_size_request(side_w, side_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

    def _tile_wide(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes in wide layout (main pane on top, others side by side below)."""
        n = len(panes)
        if n == 0:
            return

        if n == 1:
            self._tile_monocle(panes, width, height)
            return

        # Main pane takes 2/3 height
        main_h = int(height * 2 / 3) - self._gap * 2
        side_h = height - main_h - self._gap * 3

        # Stack side panes horizontally
        side_w = (width - self._gap * (n + 1)) // (n - 1)

        # Main pane
        pane = panes[0]
        pane.widget.set_size_request(width - self._gap * 2, main_h)
        pane.widget.set_margin_start(self._gap)
        pane.widget.set_margin_top(self._gap)
        pane.widget.show_all()

        # Side panes
        for i, pane in enumerate(panes[1:], 1):
            x = self._gap + (i - 1) * (side_w + self._gap)
            y = main_h + self._gap * 2

            pane.widget.set_size_request(side_w, side_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

    def _tile_centered(self, panes: List[PaneInfo], width: int, height: int):
        """Tile panes in centered layout (main pane centered, others around it)."""
        n = len(panes)
        if n == 0:
            return

        if n == 1:
            self._tile_monocle(panes, width, height)
            return

        # Main pane in center, others around it
        main_w = int(width * 0.6)
        main_h = int(height * 0.6)
        main_x = (width - main_w) // 2
        main_y = (height - main_h) // 2

        # Main pane
        pane = panes[0]
        pane.widget.set_size_request(main_w, main_h)
        pane.widget.set_margin_start(main_x)
        pane.widget.set_margin_top(main_y)
        pane.widget.show_all()

        # Side panes around the edges
        side_w = (width - main_w - self._gap * 3) // 2
        side_h = (height - main_h - self._gap * 3) // 2

        # Top and bottom panes
        remaining = panes[1:]
        for i, pane in enumerate(remaining[:2]):
            x = main_x
            y = self._gap if i == 0 else main_y + main_h + self._gap

            pane.widget.set_size_request(main_w, side_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

        # Left and right panes
        for i, pane in enumerate(remaining[2:4], 2):
            x = self._gap if i == 2 else main_x + main_w + self._gap
            y = main_y

            pane.widget.set_size_request(side_w, main_h)
            pane.widget.set_margin_start(x)
            pane.widget.set_margin_top(y)
            pane.widget.show_all()

        # Remaining panes
        for i, pane in enumerate(remaining[4:], 4):
            # Arrange in corners
            corner_x = self._gap if i % 2 == 0 else main_x + main_w + self._gap
            corner_y = self._gap if i < 6 else main_y + main_h + self._gap

            pane.widget.set_size_request(side_w, side_h)
            pane.widget.set_margin_start(corner_x)
            pane.widget.set_margin_top(corner_y)
            pane.widget.show_all()


class TilingPanel(Gtk.Box):
    """Panel for controlling tiling layouts."""

    def __init__(self, tiling_manager: TilingManager):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._manager = tiling_manager

        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        # Layout buttons
        layouts = [
            ("Grid", TilingLayout.GRID),
            ("Horiz", TilingLayout.HORIZONTAL),
            ("Vert", TilingLayout.VERTICAL),
            ("Mono", TilingLayout.MONOCLE),
            ("Tall", TilingLayout.TALL),
            ("Wide", TilingLayout.WIDE),
            ("Center", TilingLayout.CENTERED),
        ]

        for label, layout in layouts:
            btn = Gtk.Button(label=label)
            btn.set_size_request(60, 24)
            btn.connect("clicked", lambda _, l=layout: self._set_layout(l))
            self.pack_start(btn, False, False, 0)

    def _set_layout(self, layout: TilingLayout):
        """Set the tiling layout."""
        self._manager.set_layout(layout)


def create_tiling_window(container: Gtk.Container) -> Tuple[TilingManager, TilingPanel]:
    """Create a tiling manager and panel for a container."""
    manager = TilingManager(container)
    panel = TilingPanel(manager)
    return manager, panel
