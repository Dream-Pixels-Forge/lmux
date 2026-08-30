"""Multiple windows support for lmux."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging
from typing import List, Optional, Dict, Any

logger = logging.getLogger("lmux.multi_window")


class WindowManager:
    """Manages multiple application windows."""

    def __init__(self, main_app):
        self._main_app = main_app
        self._windows: List[Gtk.Window] = []
        self._window_counter = 0

    def create_window(self, title: Optional[str] = None) -> Gtk.Window:
        """Create a new application window."""
        self._window_counter += 1
        window_title = title or f"lmux — Window {self._window_counter}"

        window = Gtk.Window(title=window_title)
        window.set_default_size(1200, 800)
        window.set_position(Gtk.WindowPosition.CENTER)

        # Store window reference
        self._windows.append(window)

        # Connect destroy signal
        window.connect("destroy", self._on_window_destroy)

        return window

    def close_window(self, window: Gtk.Window):
        """Close a specific window."""
        if window in self._windows:
            self._windows.remove(window)
            window.destroy()

    def close_all_windows(self):
        """Close all windows."""
        for window in self._windows.copy():
            window.destroy()
        self._windows.clear()

    def get_windows(self) -> List[Gtk.Window]:
        """Get all open windows."""
        return self._windows.copy()

    def get_window_count(self) -> int:
        """Get the number of open windows."""
        return len(self._windows)

    def focus_window(self, index: int):
        """Focus a window by index."""
        if 0 <= index < len(self._windows):
            self._windows[index].present()

    def _on_window_destroy(self, window):
        """Handle window destruction."""
        if window in self._windows:
            self._windows.remove(window)

        # If no windows left, quit the application
        if not self._windows:
            Gtk.main_quit()


def setup_multiple_windows(app, accel_group):
    """Set up keyboard shortcuts for multiple windows."""
    # Ctrl+Shift+N: New window
    keyval, mods = Gtk.accelerator_parse("<Ctrl><Shift>n")
    if keyval:
        accel_group.connect(
            keyval, mods, Gtk.AccelFlags.VISIBLE,
            lambda *_: app._window_manager.create_window()
        )
