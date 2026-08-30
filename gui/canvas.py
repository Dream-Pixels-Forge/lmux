"""Canvas 2D layout for lmux GUI — free-form pane positioning with drag-and-drop.

Provides a GTK3 DrawingArea-based canvas where panes can be positioned
freely, resized by dragging edges, and snapped to a grid.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GObject
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── Constants ───────────────────────────────────────────────

GRID_SIZE = 20  # snap grid in pixels
MIN_PANE_SIZE = 80  # minimum pane width/height
HANDLE_SIZE = 8  # resize handle size
PANE_COLORS = [
    "#89b4fa",  # blue
    "#a6e3a1",  # green
    "#f9e2af",  # yellow
    "#f38ba8",  # red
    "#cba6f7",  # purple
    "#94e2d5",  # teal
    "#fab387",  # peach
    "#74c7ec",  # sapphire
]


# ── Pane item ───────────────────────────────────────────────

class CanvasPane:
    """A single pane on the canvas with position and size."""

    def __init__(self, pane_id, x=0, y=0, width=300, height=200, title=""):
        self.pane_id = pane_id
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.title = title or f"Pane {pane_id}"
        self.color = PANE_COLORS[hash(str(pane_id)) % len(PANE_COLORS)]
        self.selected = False

    def contains(self, px, py):
        """Check if point is inside this pane."""
        return (self.x <= px <= self.x + self.width and
                self.y <= py <= self.y + self.height)

    def handle_at(self, px, py):
        """Check if point is on a resize handle. Returns handle type or None."""
        h = HANDLE_SIZE
        # Bottom-right corner
        if (abs(px - (self.x + self.width)) < h and
            abs(py - (self.y + self.height)) < h):
            return "se"
        # Right edge
        if (abs(px - (self.x + self.width)) < h and
            self.y < py < self.y + self.height):
            return "e"
        # Bottom edge
        if (self.x < px < self.x + self.width and
            abs(py - (self.y + self.height)) < h):
            return "s"
        return None

    def to_dict(self):
        return {
            "id": self.pane_id,
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            pane_id=d["id"],
            x=d.get("x", 0), y=d.get("y", 0),
            width=d.get("width", 300), height=d.get("height", 200),
            title=d.get("title", ""),
        )


# ── Canvas widget ───────────────────────────────────────────

class Canvas2D(Gtk.DrawingArea):
    """DrawingArea-based 2D canvas for free-form pane layout."""

    __gsignals__ = {
        "pane-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "pane-moved": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
        "pane-resized": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
    }

    def __init__(self):
        super().__init__()
        self._panes = {}  # pane_id -> CanvasPane
        self._selected = None  # selected pane_id
        self._dragging = False
        self._resizing = False
        self._resize_handle = None
        self._drag_offset = (0, 0)
        self._grid_visible = True
        self._next_id = 1

        self.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.KEY_PRESS_MASK
        )
        self.set_can_focus(True)
        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("key-press-event", self._on_key_press)

    # ── public API ──────────────────────────────────────────

    def add_pane(self, pane_id=None, x=None, y=None, width=300, height=200, title=""):
        """Add a pane to the canvas."""
        if pane_id is None:
            pane_id = str(self._next_id)
            self._next_id += 1
        if x is None:
            x = 20 + len(self._panes) * 30 % 300
        if y is None:
            y = 20 + len(self._panes) * 30 % 200
        pane = CanvasPane(pane_id, x, y, width, height, title)
        self._panes[pane_id] = pane
        self.queue_draw()
        return pane

    def remove_pane(self, pane_id):
        """Remove a pane from the canvas."""
        if pane_id in self._panes:
            del self._panes[pane_id]
            if self._selected == pane_id:
                self._selected = None
            self.queue_draw()

    def get_pane(self, pane_id):
        """Get a pane by ID."""
        return self._panes.get(pane_id)

    def get_all_panes(self):
        """Get all panes."""
        return list(self._panes.values())

    def select_pane(self, pane_id):
        """Select a pane."""
        if self._selected:
            old = self._panes.get(self._selected)
            if old:
                old.selected = False
        self._selected = pane_id
        pane = self._panes.get(pane_id)
        if pane:
            pane.selected = True
        self.queue_draw()

    def toggle_grid(self):
        """Toggle grid visibility."""
        self._grid_visible = not self._grid_visible
        self.queue_draw()

    def clear(self):
        """Remove all panes."""
        self._panes.clear()
        self._selected = None
        self.queue_draw()

    def save_layout(self, filepath):
        """Save layout to JSON file."""
        data = {
            "panes": [p.to_dict() for p in self._panes.values()],
            "next_id": self._next_id,
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def load_layout(self, filepath):
        """Load layout from JSON file."""
        if not os.path.isfile(filepath):
            return False
        with open(filepath) as f:
            data = json.load(f)
        self._panes.clear()
        for pd in data.get("panes", []):
            pane = CanvasPane.from_dict(pd)
            self._panes[pane.pane_id] = pane
        self._next_id = data.get("next_id", len(self._panes) + 1)
        self._selected = None
        self.queue_draw()
        return True

    def auto_tile(self):
        """Auto-tile panes in a grid layout."""
        panes = list(self._panes.values())
        if not panes:
            return
        count = len(panes)
        cols = max(1, int(count ** 0.5) + (1 if count ** 0.5 % 1 else 0))
        rows = (count + cols - 1) // cols
        alloc = self.get_allocation()
        w = max(MIN_PANE_SIZE, (alloc.width - 20 * (cols + 1)) // cols)
        h = max(MIN_PANE_SIZE, (alloc.height - 20 * (rows + 1)) // rows)
        for i, pane in enumerate(panes):
            col = i % cols
            row = i // cols
            pane.x = 20 + col * (w + 20)
            pane.y = 20 + row * (h + 20)
            pane.width = w
            pane.height = h
        self.queue_draw()

    # ── drawing ─────────────────────────────────────────────

    def _on_draw(self, widget, cr):
        alloc = self.get_allocation()
        # Background
        cr.set_source_rgb(0.12, 0.12, 0.18)
        cr.rectangle(0, 0, alloc.width, alloc.height)
        cr.fill()

        # Grid
        if self._grid_visible:
            cr.set_source_rgba(0.3, 0.3, 0.4, 0.3)
            cr.set_line_width(0.5)
            for x in range(0, alloc.width, GRID_SIZE):
                cr.move_to(x, 0)
                cr.line_to(x, alloc.height)
            for y in range(0, alloc.height, GRID_SIZE):
                cr.move_to(0, y)
                cr.line_to(alloc.width, y)
            cr.stroke()

        # Panes
        for pane in self._panes.values():
            self._draw_pane(cr, pane)

    def _draw_pane(self, cr, pane):
        """Draw a single pane with border and title."""
        # Parse color
        color = Gdk.RGBA()
        color.parse(pane.color)

        # Fill
        cr.set_source_rgba(color.red, color.green, color.blue, 0.3)
        cr.rectangle(pane.x, pane.y, pane.width, pane.height)
        cr.fill()

        # Border
        if pane.selected:
            cr.set_source_rgba(color.red, color.green, color.blue, 1.0)
            cr.set_line_width(3)
        else:
            cr.set_source_rgba(color.red, color.green, color.blue, 0.6)
            cr.set_line_width(1)
        cr.rectangle(pane.x, pane.y, pane.width, pane.height)
        cr.stroke()

        # Title bar
        cr.set_source_rgba(color.red, color.green, color.blue, 0.4)
        cr.rectangle(pane.x, pane.y, pane.width, 24)
        cr.fill()

        # Title text
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.select_font_face("monospace", 0, 0)
        cr.set_font_size(12)
        cr.move_to(pane.x + 8, pane.y + 16)
        cr.show_text(pane.title[:30])

        # Resize handles
        if pane.selected:
            cr.set_source_rgba(color.red, color.green, color.blue, 0.8)
            # SE corner
            cr.rectangle(
                pane.x + pane.width - HANDLE_SIZE,
                pane.y + pane.height - HANDLE_SIZE,
                HANDLE_SIZE, HANDLE_SIZE
            )
            cr.fill()
            # E edge
            cr.rectangle(
                pane.x + pane.width - 3,
                pane.y + pane.height // 2 - HANDLE_SIZE // 2,
                6, HANDLE_SIZE
            )
            cr.fill()
            # S edge
            cr.rectangle(
                pane.x + pane.width // 2 - HANDLE_SIZE // 2,
                pane.y + pane.height - 3,
                HANDLE_SIZE, 6
            )
            cr.fill()

    # ── input handling ──────────────────────────────────────

    def _snap(self, value):
        """Snap value to grid."""
        if self._grid_visible:
            return round(value / GRID_SIZE) * GRID_SIZE
        return value

    def _on_button_press(self, widget, event):
        self.grab_focus()
        px, py = event.x, event.y

        # Check if clicking on a pane
        clicked = None
        for pane in reversed(list(self._panes.values())):
            if pane.contains(px, py):
                clicked = pane
                break

        if clicked:
            self.select_pane(clicked.pane_id)
            # Check for resize handle
            handle = clicked.handle_at(px, py)
            if handle:
                self._resizing = True
                self._resize_handle = handle
                self._drag_offset = (px - clicked.x, py - clicked.y)
            else:
                self._dragging = True
                self._drag_offset = (px - clicked.x, py - clicked.y)
            self.emit("pane-selected", clicked.pane_id)
        else:
            # Deselect
            if self._selected:
                old = self._panes.get(self._selected)
                if old:
                    old.selected = False
            self._selected = None
            self.queue_draw()

        return True

    def _on_button_release(self, widget, event):
        if self._dragging or self._resizing:
            self._dragging = False
            self._resizing = False
            self._resize_handle = None
        return True

    def _on_motion(self, widget, event):
        px, py = event.x, event.y

        if self._dragging and self._selected:
            pane = self._panes.get(self._selected)
            if pane:
                pane.x = self._snap(px - self._drag_offset[0])
                pane.y = self._snap(py - self._drag_offset[1])
                self.emit("pane-moved", pane.pane_id, int(pane.x), int(pane.y))
                self.queue_draw()
        elif self._resizing and self._selected:
            pane = self._panes.get(self._selected)
            if pane:
                if self._resize_handle == "se":
                    pane.width = max(MIN_PANE_SIZE, self._snap(px - pane.x))
                    pane.height = max(MIN_PANE_SIZE, self._snap(py - pane.y))
                elif self._resize_handle == "e":
                    pane.width = max(MIN_PANE_SIZE, self._snap(px - pane.x))
                elif self._resize_handle == "s":
                    pane.height = max(MIN_PANE_SIZE, self._snap(py - pane.y))
                self.emit("pane-resized", pane.pane_id, int(pane.width), int(pane.height))
                self.queue_draw()
        else:
            # Update cursor based on handle proximity
            cursor = None
            for pane in self._panes.values():
                handle = pane.handle_at(px, py)
                if handle:
                    if handle in ("se",):
                        cursor = Gdk.Cursor(Gdk.CursorType.SIZINGSE)
                    elif handle == "e":
                        cursor = Gdk.Cursor(Gdk.CursorType.SIZINGE)
                    elif handle == "s":
                        cursor = Gdk.Cursor(Gdk.CursorType.SIZINGS)
                    break
            window = self.get_window()
            if window:
                window.set_cursor(cursor)

        return True

    def _on_key_press(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        if keyname == "Delete" and self._selected:
            self.remove_pane(self._selected)
            return True
        elif keyname == "g":
            self.toggle_grid()
            return True
        elif keyname == "t":
            self.auto_tile()
            return True
        return False


# ── Canvas container with toolbar ───────────────────────────

class CanvasContainer(Gtk.Box):
    """Canvas with toolbar for add/remove/toggle grid/auto-tile."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_margin_start(4)
        toolbar.set_margin_end(4)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        btn_add = Gtk.Button(label="+ Pane")
        btn_add.connect("clicked", lambda _: self._canvas.add_pane())
        toolbar.pack_start(btn_add, False, False, 0)

        btn_remove = Gtk.Button(label="- Pane")
        btn_remove.connect("clicked", lambda _: self._canvas.remove_pane(self._canvas._selected))
        toolbar.pack_start(btn_remove, False, False, 0)

        btn_grid = Gtk.Button(label="Grid")
        btn_grid.connect("clicked", lambda _: self._canvas.toggle_grid())
        toolbar.pack_start(btn_grid, False, False, 0)

        btn_tile = Gtk.Button(label="Auto-Tile")
        btn_tile.connect("clicked", lambda _: self._canvas.auto_tile())
        toolbar.pack_start(btn_tile, False, False, 0)

        self.pack_start(toolbar, False, False, 0)

        # Canvas
        self._canvas = Canvas2D()
        self.pack_start(self._canvas, True, True, 0)

    @property
    def canvas(self):
        return self._canvas


# ── Standalone test ─────────────────────────────────────────

if __name__ == "__main__":
    win = Gtk.Window(title="Canvas 2D Test")
    win.set_default_size(800, 600)

    container = CanvasContainer()
    # Add some test panes
    container.canvas.add_pane("term-1", 20, 20, 350, 250, "Terminal 1")
    container.canvas.add_pane("term-2", 400, 20, 350, 250, "Terminal 2")
    container.canvas.add_pane("term-3", 20, 300, 730, 250, "Terminal 3")

    def on_pane_selected(widget, pane_id):
        print(f"Selected: {pane_id}")

    container.canvas.connect("pane-selected", on_pane_selected)

    win.add(container)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
