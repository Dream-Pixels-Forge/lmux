"""GPU-accelerated terminal widget for lmux — VTE 3.91 on GTK4.

GTK4 renders through GSK (Gtk Scene Graph) which uses OpenGL/Vulkan
by default. VTE 3.91 on GTK4 inherits this GPU acceleration.

Requires: gir1.2-gtk-4.0, gir1.2-vte-3.91
Run with: GSK_RENDERER=opengl python3 gui/gtk4/main.py
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Vte, Pango, Gio
import os
import sys
import signal
import subprocess


def _get_children(widget):
    """GTK4 helper: get all children of a widget."""
    children = []
    child = widget.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    return children
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daemon_client import DaemonClient


# ── GPU-accelerated terminal widget ─────────────────────────

class TerminalWidget(Gtk.Box):
    """VTE terminal widget with GPU rendering via GTK4 GSK."""

    def __init__(self, client=None, ws_id=None, surface_id=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._ws_id = ws_id
        self._surface_id = surface_id

        # VTE terminal
        self._terminal = Vte.Terminal()
        self._terminal.set_scrollback_lines(10000)
        self._terminal.set_font(Pango.FontDescription.from_string("Monospace 12"))
        self._terminal.set_mouse_autohide(True)
        self._terminal.set_bold_is_bright(True)

        # Enable GPU rendering hints
        self._terminal.set_can_focus(True)

        # Connect signals
        self._terminal.connect("child-exited", self._on_child_exited)

        # Scrolled window
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self._terminal)
        scroll.set_vexpand(True)
        self.append(scroll)

        # Spawn shell
        self._spawn_shell()

    def _spawn_shell(self):
        """Spawn a shell in the terminal."""
        shell = os.environ.get("SHELL", "/bin/bash")
        envp = [
            f"{k}={v}" for k, v in os.environ.items()
            if k not in ("TERM", "COLORTERM")
        ]
        envp.append("TERM=xterm-256color")
        envp.append("COLORTERM=truecolor")

        # Use spawn_sync for VTE 3.91 compatibility (spawn_async arg order differs)
        result = self._terminal.spawn_sync(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            [shell],
            envp,
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            Gio.Cancellable(),
        )
        if not result[0]:
            print("Failed to spawn shell", file=sys.stderr)

    def _on_child_exited(self, terminal, status):
        """Shell exited — close this terminal."""
        parent = self.get_parent()
        if parent:
            page_num = parent.page_num(self)
            if page_num >= 0:
                parent.remove_page(page_num)

    def feed_text(self, text):
        """Send text to the terminal."""
        self._terminal.feed_child(text.encode())

    def feed_key(self, keyval, modifiers=0):
        """Send a key event to the terminal."""
        self._terminal.feed_child_event(
            Gdk.Event.new_keypress(
                keyval=keyval,
                state=modifiers,
                time=Gdk.CURRENT_TIME,
            )
        )

    def get_vte_terminal(self):
        """Get the underlying VTE terminal widget."""
        return self._terminal


# ── Terminal notebook (tab container) ───────────────────────

class TerminalNotebook(Gtk.Box):
    """Tabbed container for terminal widgets."""

    def __init__(self, client=None, ws_id=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._ws_id = ws_id

        # Header bar with tabs
        self._header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._header.set_margin_top(2)
        self._header.set_margin_bottom(2)
        self._header.set_margin_start(4)
        self._header.set_margin_end(4)
        self.append(self._header)

        # Tab bar
        self._tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._header.append(self._tab_bar)

        # Add tab button
        add_btn = Gtk.Button(label="+")
        add_btn.set_size_request(24, 24)
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", lambda _: self.create_terminal())
        self._header.append(add_btn)

        # Stack for terminals
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.append(self._stack)

        # Stack switcher
        self._stack_switcher = Gtk.StackSwitcher()
        self._stack_switcher.set_stack(self._stack)
        self._stack_switcher.set_halign(Gtk.Align.CENTER)
        self._stack_switcher.set_margin_top(4)
        # Track terminals
        self._terminals = {}  # page_num -> TerminalWidget
        self._tab_names = {}  # child -> name
        self._next_id = 1

        # Create first terminal
        self.create_terminal()

    def create_terminal(self):
        """Create a new terminal tab."""
        term = TerminalWidget(
            client=self._client,
            ws_id=self._ws_id,
            surface_id=str(self._next_id),
        )
        page_name = f"term-{self._next_id}"
        display_name = f"Terminal {self._next_id}"
        self._stack.add_titled(term, page_name, display_name)
        self._terminals[self._next_id] = term
        self._tab_names[term] = display_name
        self._next_id += 1

        self._stack.set_visible_child(term)
        self._update_tabs()

    def close_current(self):
        """Close the currently visible terminal."""
        visible = self._stack.get_visible_child()
        if visible:
            self._stack.remove(visible)
            # Remove from tracking
            for tid, term in list(self._terminals.items()):
                if term == visible:
                    del self._terminals[tid]
                    break
            self._update_tabs()

    def _update_tabs(self):
        """Update tab bar to match stack."""
        # Clear tab bar
        while True:
            child = self._tab_bar.get_first_child()
            if child is None:
                break
            self._tab_bar.remove(child)

        for child in _get_children(self._stack):
            name = self._tab_names.get(child, "?")
            btn = Gtk.Button(label=name)
            btn.set_size_request(80, 24)
            btn.add_css_class("flat")
            visible = self._stack.get_visible_child() == child
            if visible:
                btn.add_css_class("accent")
            btn.connect("clicked", lambda b, c: self._stack.set_visible_child(c), child)
            self._tab_bar.append(btn)

        self._tab_bar.show()

    def get_active_terminal(self):
        """Get the currently visible terminal."""
        return self._stack.get_visible_child()


# ── Workspace view ──────────────────────────────────────────

class WorkspaceView(Gtk.Box):
    """A workspace view containing a terminal notebook."""

    def __init__(self, client=None, ws_id=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._ws_id = ws_id

        self._notebook = TerminalNotebook(client=client, ws_id=ws_id)
        self.append(self._notebook)

    def create_surface(self):
        """Create a new terminal surface (tab)."""
        self._notebook.create_terminal()

    def close_current_surface(self):
        """Close the current terminal surface."""
        self._notebook.close_current()

    def split_pane(self, direction):
        """Split the current terminal."""
        # Create new terminal (simplified — full impl would split VTE)
        self._notebook.create_terminal()

    def focus_next_pane(self):
        """Focus next terminal."""
        children = _get_children(self._notebook._stack)
        visible = self._notebook._stack.get_visible_child()
        if visible and children:
            idx = children.index(visible)
            next_idx = (idx + 1) % len(children)
            self._notebook._stack.set_visible_child(children[next_idx])

    def focus_prev_pane(self):
        """Focus previous terminal."""
        children = _get_children(self._notebook._stack)
        visible = self._notebook._stack.get_visible_child()
        if visible and children:
            idx = children.index(visible)
            prev_idx = (idx - 1) % len(children)
            self._notebook._stack.set_visible_child(children[prev_idx])
