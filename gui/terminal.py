"""VTE terminal widget wrapper for lmux GUI."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib, Gdk
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panes import PaneContainer


class TerminalWidget(Gtk.Box):
    """A VTE terminal wrapped in a box with title bar."""

    def __init__(self, title="Terminal"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._title = title
        self._pane_id = None

        # Title bar
        self._title_bar = Gtk.EventBox()
        self._title_bar.get_style_context().add_class("terminal-title")
        self._title_label = Gtk.Label(label=title)
        self._title_label.set_xalign(0)
        self._title_label.set_margin_start(8)
        self._title_label.set_margin_end(8)
        self._title_label.set_margin_top(2)
        self._title_label.set_margin_bottom(2)
        self._title_bar.add(self._title_label)
        self.pack_start(self._title_bar, False, False, 0)

        # VTE Terminal
        self._vte = Vte.Terminal()
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.add(self._vte)
        self.pack_start(self._scroll, True, True, 0)

        # Spawn shell in terminal (use spawn_sync)
        shell = os.environ.get("SHELL", "/bin/sh")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._vte.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                os.getcwd(),
                [shell],
                [],
                GLib.SpawnFlags.SEARCH_PATH,
                None,
                None,  # cancellable
            )

        # Connect VTE signals
        self._vte.connect("window-title-changed", self._on_window_title_changed)

        self.show_all()

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value
        self._title_label.set_text(value)

    @property
    def pane_id(self):
        return self._pane_id

    @pane_id.setter
    def pane_id(self, value):
        self._pane_id = value

    @property
    def vte(self):
        return self._vte

    def _on_window_title_changed(self, terminal):
        """Sync the VTE terminal's window title to the title bar label."""
        title = terminal.get_window_title()
        if title:
            self._title = title
            self._title_label.set_text(title)

    def send_text(self, text):
        """Send text to the terminal."""
        self._vte.feed_child(text.encode())

    def get_text(self):
        """Get visible text from the terminal."""
        return self._vte.get_text()[0]

    def clear(self):
        """Clear terminal."""
        self._vte.reset(True, True)

    def grab_focus_to_vte(self):
        """Redirect keyboard focus to the VTE widget."""
        self._vte.grab_focus()


class TerminalNotebook(Gtk.Notebook):
    """A notebook that holds multiple surfaces.

    Each notebook page is a PaneContainer (which holds one or more
    terminal panes).

    Signals:
        "surface-switched" (notebook, old_surface_id, new_surface_id)
    """

    def __init__(self, client=None):
        super().__init__()
        self.set_scrollable(True)
        self.set_show_tabs(True)
        self._client = client
        self._pages = {}       # surface_id -> PaneContainer
        self._page_order = []  # ordered surface_ids
        self._counter = 0
        self._callbacks = {}

        self.connect("switch-page", self._on_switch_page)

    def add_surface(self, surface_id=None, title=None):
        """Add a new surface (notebook tab) with an initial terminal pane.

        Returns (PaneContainer, TerminalWidget).
        """
        self._counter += 1
        if surface_id is None:
            surface_id = self._counter
        if title is None:
            title = f"Surface {self._counter}"

        container = PaneContainer(surface_id=surface_id)
        # Create the initial terminal pane for this surface
        pane_id = 1
        term = container.add_pane(pane_id, title)

        # Notebook tab label
        label = Gtk.Label(label=title)
        self.append_page(container, label)

        self._pages[surface_id] = container
        self._page_order.append(surface_id)
        self.show_all()

        return container, term

    def remove_surface(self, surface_id):
        """Remove a surface by id."""
        if surface_id in self._pages:
            container = self._pages.pop(surface_id)
            self._page_order.remove(surface_id)
            page_num = self.page_num(container)
            if page_num >= 0:
                self.remove_page(page_num)

    def current_container(self):
        """Get the PaneContainer for the currently visible surface."""
        n = self.get_current_page()
        if n >= 0:
            return self.get_nth_page(n)
        return None

    def current_pane_ids(self):
        """Get (surface_id, page_nth) for the current tab."""
        n = self.get_current_page()
        if n < 0:
            return None, None
        container = self.get_nth_page(n)
        # Find surface_id from reverse map
        for sid, c in self._pages.items():
            if c is container:
                return sid, n
        return None, n

    def surface_count(self):
        return len(self._pages)

    def focus_next_surface(self):
        n = self.get_current_page()
        if n >= 0:
            self.set_current_page((n + 1) % max(self.get_n_pages(), 1))

    def focus_prev_surface(self):
        n = self.get_current_page()
        if n >= 0:
            self.set_current_page((n - 1) % max(self.get_n_pages(), 1))

    def _on_switch_page(self, notebook, page, page_num):
        cb = self._callbacks.get("switch-page")
        if cb:
            container = self.get_nth_page(page_num)
            sid = None
            for sid_candidate, c in self._pages.items():
                if c is container:
                    sid = sid_candidate
                    break
            cb(sid, page_num)
        # Focus the VTE in the new page's current pane
        container = self.get_nth_page(page_num)
        term = container.focused_terminal()
        if term:
            term.grab_focus_to_vte()

    def on(self, event, callback):
        self._callbacks[event] = callback

    def get_all_surfaces(self):
        return list(self._pages.keys()), list(self._pages.values())
