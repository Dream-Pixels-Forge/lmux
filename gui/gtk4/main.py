"""lmux GTK4 main window — GPU-accelerated via GSK OpenGL backend.

Requires: gir1.2-gtk-4.0, gir1.2-vte-3.91
Run with: GSK_RENDERER=opengl python3 gui/gtk4/main.py

GTK4 renders all widgets through GSK (Gtk Scene Graph) which uses
OpenGL/Vulkan by default. VTE 3.91 terminal rendering inherits this
GPU acceleration automatically.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daemon_client import DaemonClient

# GTK4 submodules
from terminal import TerminalNotebook, TerminalWidget
from sidebar import Sidebar

def _get_children(widget):
    """GTK4 helper: get all children of a widget."""
    children = []
    child = widget.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    return children


# ── Application ─────────────────────────────────────────────

class LmuxApplication(Gtk.Application):
    """GTK4 Application with GPU rendering."""

    def __init__(self):
        super().__init__(
            application_id="com.dream-pixels-forge.lmux",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._client = DaemonClient()
        self._active_ws_id = None

    def do_activate(self):
        """Create and show the main window."""
        win = LmuxWindow(application=self, client=self._client)
        win.present()


# ── Main Window ─────────────────────────────────────────────

class LmuxWindow(Gtk.ApplicationWindow):
    """GTK4 main window with GPU-accelerated rendering."""

    def __init__(self, application=None, client=None):
        super().__init__(application=application, title="lmux (GPU)")
        self.set_default_size(1200, 800)
        self._client = client or DaemonClient()

        # Main layout
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)

        # Header bar
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        # Menu button
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_model = Gio.Menu()
        menu_model.append("About", "app.about")
        menu_model.append("Quit", "app.quit")
        menu_btn.set_menu_model(menu_model)
        header.pack_end(menu_btn)

        # GPU info label
        gpu_label = Gtk.Label(label="GPU: OpenGL via GSK")
        gpu_label.set_opacity(0.5)
        header.set_title_widget(gpu_label)

        # Content paned
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        outer.append(self._paned)

        # Sidebar
        self._sidebar = Sidebar(client=client, parent_window=self)
        self._sidebar.connect("workspace-selected", self._on_workspace_selected)
        self._sidebar.connect("workspace-create-requested", self._on_workspace_create)
        self._paned.set_start_child(self._sidebar)

        # Stack for workspace views
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._paned.set_end_child(self._stack)

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Event stream
        self._start_events()

        # Periodic refresh
        GLib.timeout_add_seconds(5, self._tick_refresh)

        # Initial refresh
        GLib.idle_add(lambda: self._sidebar.refresh())

        # Show
        self.present()

        # Print GPU renderer info
        renderer = Gdk.Display.get_default().get_name()
        print(f"lmux GTK4 started — Display: {renderer}")
        print("GPU rendering: GSK backend (OpenGL/Vulkan)")

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        ctrl_shift = Gtk.ShortcutController()
        ctrl_shift.set_scope(Gtk.ShortcutScope.MANAGED)
        self.add_controller(ctrl_shift)

        # Ctrl+Shift+N — new workspace
        ctrl_shift.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control><Shift>n"),
            action=Gtk.CallbackAction.new(lambda *_: self._on_workspace_create(None)),
        ))

        # Ctrl+Shift+Q — quit
        ctrl_shift.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control><Shift>q"),
            action=Gtk.CallbackAction.new(lambda *_: self.quit()),
        ))

    def _on_workspace_selected(self, sidebar, ws_id):
        """Switch to a workspace."""
        self._active_ws_id = ws_id
        child_name = f"ws-{ws_id}"
        if child_name in _get_children(self._stack):
            self._stack.set_visible_child_name(child_name)

    def _on_workspace_create(self, sidebar):
        """Create a new workspace."""
        def _create():
            try:
                resp = self._client.send("workspace.create", {})
                ws_id = resp.get("result", {}).get("id")
                if ws_id:
                    GLib.idle_add(lambda: self._add_workspace(ws_id))
            except Exception as e:
                GLib.idle_add(lambda: self._sidebar._show_error(str(e)))

        threading.Thread(target=_create, daemon=True).start()

    def _add_workspace(self, ws_id):
        """Add a new workspace view to the stack."""
        view = WorkspaceView(client=self._client, ws_id=ws_id)
        child_name = f"ws-{ws_id}"
        self._stack.add_titled(view, child_name, f"WS-{ws_id}")
        self._stack.set_visible_child_name(child_name)
        self._sidebar.refresh()

    def _start_events(self):
        """Subscribe to daemon events."""
        def on_event(evt):
            name = evt.get("name", "")
            if "workspace" in name or "surface" in name:
                GLib.idle_add(lambda: self._sidebar.refresh())

        try:
            self._client.send_events(callback=on_event)
        except Exception:
            pass

    def _tick_refresh(self):
        """Periodic sidebar refresh."""
        GLib.idle_add(lambda: self._sidebar.refresh())
        return True


# ── Workspace View ──────────────────────────────────────────

class WorkspaceView(Gtk.Box):
    """Workspace view containing terminal notebook."""

    def __init__(self, client=None, ws_id=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._ws_id = ws_id

        self._notebook = TerminalNotebook(client=client, ws_id=ws_id)
        self.append(self._notebook)

    def create_surface(self):
        self._notebook.create_terminal()

    def close_current_surface(self):
        self._notebook.close_current()

    def split_pane(self, direction):
        self._notebook.create_terminal()

    def focus_next_pane(self):
        children = _get_children(self._notebook._stack)
        visible = self._notebook._stack.get_visible_child()
        if visible and children:
            idx = children.index(visible)
            next_idx = (idx + 1) % len(children)
            self._notebook._stack.set_visible_child(children[next_idx])

    def focus_prev_pane(self):
        children = _get_children(self._notebook._stack)
        visible = self._notebook._stack.get_visible_child()
        if visible and children:
            idx = children.index(visible)
            prev_idx = (idx - 1) % len(children)
            self._notebook._stack.set_visible_child(children[prev_idx])


# ── Entry point ─────────────────────────────────────────────

def main():
    # Check for GTK4
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Vte", "3.91")
    except ValueError as e:
        print(f"Error: {e}")
        print("Install GTK4 bindings: sudo apt install gir1.2-gtk-4.0 gir1.2-vte-3.91")
        print("Or on Fedora: sudo dnf install gtk4 vte291-gtk4")
        sys.exit(1)

    # Check GPU renderer
    renderer = os.environ.get("GSK_RENDERER", "default")
    print(f"GSK renderer: {renderer}")
    if renderer == "default":
        print("Tip: Set GSK_RENDERER=opengl for GPU acceleration")

    app = LmuxApplication()
    app.run(sys.argv)


if __name__ == "__main__":
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gio
    main()
