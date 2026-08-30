"""Workspace sidebar panel for lmux GUI."""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, GObject, GLib
import threading
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daemon_client import DaemonClient
from auth import AuthManager

# Optional feature imports with graceful fallback
try:
    from ssh_client import SSHClient
except ImportError:
    SSHClient = None
try:
    from agent_hooks import HookRegistry
except ImportError:
    HookRegistry = None
try:
    from auto_naming import AutoNamer
except ImportError:
    AutoNamer = None
try:
    from focus_history import FocusHistory
except ImportError:
    FocusHistory = None


class WorkspaceRow(Gtk.ListBoxRow):
    """A single workspace entry in the sidebar."""

    def __init__(self, ws_id, title, unread=False, waiting=False):
        super().__init__()
        self.ws_id = ws_id
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Indicator dot
        self._indicator = Gtk.Label(label="" if not unread else "\u25cf")
        self._indicator.set_width_chars(2)
        style = self._indicator.get_style_context()
        if waiting:
            style.add_class("waiting-indicator")
        elif unread:
            style.add_class("unread-indicator")
        box.pack_start(self._indicator, False, False, 0)

        # Title line (workspace title)
        self._title_label = Gtk.Label(label=title)
        self._title_label.set_xalign(0)
        self._title_label.set_ellipsize(True)
        self._title_label.set_max_width_chars(30)
        box.pack_start(self._title_label, True, True, 0)

        # Metadata line (git branch, cwd)
        self._meta_label = Gtk.Label(label="")
        self._meta_label.set_xalign(0)
        self._meta_label.set_opacity(0.7)
        self._meta_label.set_max_width_chars(30)
        box.pack_start(self._meta_label, True, True, 0)

        self.add(box)
        self.show_all()

    def set_metadata(self, cwd=None, branch=None):
        """Set secondary metadata label. Show cwd and git branch if present."""
        parts = []
        if branch:
            parts.append(branch)
        if cwd:
            # Abbreviate home directory to ~
            home = os.path.expanduser("~")
            display_cwd = cwd
            if cwd.startswith(home):
                display_cwd = "~" + cwd[len(home):]
            parts.append(display_cwd)
        self._meta_label.set_text(" \u2022 ".join(parts))

    def set_unread(self, unread):
        self._indicator.set_text("\u25cf" if unread else "")
        style = self._indicator.get_style_context()
        if unread:
            style.add_class("unread-indicator")
        else:
            style.remove_class("unread-indicator")

    def set_waiting(self, waiting):
        style = self._indicator.get_style_context()
        if waiting:
            style.add_class("waiting-indicator")
        else:
            style.remove_class("waiting-indicator")


class Sidebar(Gtk.Box):
    """Sidebar panel listing all workspaces with metadata and indicators."""

    __gsignals__ = {
        "workspace-selected": (GObject.SIGNAL_RUN_LAST, None, (int,)),
        "workspace-create-requested": (GObject.SIGNAL_RUN_LAST, None, ()),
    }

    def __init__(self, client=None, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client or DaemonClient()
        self._rows = {}  # ws_id -> WorkspaceRow
        self._empty_count = 0  # L4: consecutive refreshes with 0 workspaces
        self._extensions = {}  # name -> extension instance
        self._ext_widgets = {}  # name -> widget
        self._auth = AuthManager(parent_window=parent_window)

        # Header with label, auth status, and "+" button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(6)
        header.set_margin_bottom(4)
        lbl = Gtk.Label(label="Workspaces")
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("workspace-header")
        header.pack_start(lbl, True, True, 0)
        # Auth button (shows user or login prompt)
        self._auth_btn = Gtk.Button(label="\u2b1a")
        self._auth_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._auth_btn.set_tooltip_text("Login")
        self._auth_btn.connect("clicked", self._on_auth_click)
        header.pack_end(self._auth_btn, False, False, 0)
        add_btn = Gtk.Button(label="+")
        add_btn.set_relief(Gtk.ReliefStyle.NONE)
        add_btn.connect("clicked", lambda _: self.emit("workspace-create-requested"))
        header.pack_end(add_btn, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Stack for workspace list + extension panels
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        self.pack_start(self._stack, True, True, 0)

        # Workspace list (default page)
        ws_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list_box.connect("row-selected", self._on_row_selected)
        ws_box.pack_start(self._list_box, True, True, 0)
        self._stack.add_titled(ws_box, "workspaces", "Workspaces")

        # Extension switcher (shown when extensions exist)
        self._ext_switcher = Gtk.StackSwitcher()
        self._ext_switcher.set_stack(self._stack)
        self._ext_switcher.set_halign(Gtk.Align.CENTER)
        self._ext_switcher.set_margin_top(4)
        self._ext_switcher.set_margin_bottom(4)
        self._ext_switcher_visible = False

        # ── SSH Sessions section (collapsible) ─────────────
        self._ssh_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._ssh_box.set_margin_start(8)
        self._ssh_box.set_margin_end(8)
        self._ssh_box.set_margin_top(4)
        self._ssh_header = Gtk.Label(label="\u25b6 SSH Sessions")
        self._ssh_header.set_xalign(0)
        self._ssh_header.get_style_context().add_class("workspace-header")
        self._ssh_header.set_opacity(0.8)
        self._ssh_header.connect("button-press-event", self._toggle_ssh_section)
        self._ssh_box.pack_start(self._ssh_header, False, False, 0)
        self._ssh_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._ssh_list_box.set_margin_start(12)
        self._ssh_list_box.set_no_show_all(True)
        self._ssh_box.pack_start(self._ssh_list_box, False, False, 0)
        self._ssh_expanded = False
        self.pack_start(self._ssh_box, False, False, 0)

        # ── Focus History section ──────────────────────────
        self._focus_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._focus_box.set_margin_start(8)
        self._focus_box.set_margin_end(8)
        self._focus_box.set_margin_top(4)
        focus_lbl = Gtk.Label(label="Focus History")
        focus_lbl.set_xalign(0)
        focus_lbl.get_style_context().add_class("workspace-header")
        focus_lbl.set_opacity(0.8)
        self._focus_box.pack_start(focus_lbl, False, False, 0)
        self._focus_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._focus_list_box.set_margin_start(12)
        self._focus_box.pack_start(self._focus_list_box, False, False, 0)
        self.pack_start(self._focus_box, False, False, 0)

        # ── Hook event indicators ──────────────────────────
        self._hook_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._hook_box.set_margin_start(8)
        self._hook_box.set_margin_end(8)
        self._hook_box.set_margin_top(2)
        hook_lbl = Gtk.Label(label="Hooks:")
        hook_lbl.set_opacity(0.6)
        self._hook_box.pack_start(hook_lbl, False, False, 0)
        self._hook_dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._hook_box.pack_start(self._hook_dots, False, False, 0)
        self.pack_start(self._hook_box, False, False, 0)

        # Error bar (hidden by default)
        self._error_label = Gtk.Label(label="")
        self._error_label.set_xalign(0)
        self._error_label.get_style_context().add_class("error")
        self._error_label.set_opacity(0.0)
        self.pack_end(self._error_label, False, False, 0)

        self.show_all()

    def _toggle_ssh_section(self, widget, event):
        """Toggle SSH sessions section visibility."""
        self._ssh_expanded = not self._ssh_expanded
        if self._ssh_expanded:
            self._ssh_header.set_text("\u25bc SSH Sessions")
            self._ssh_list_box.show_all()
            self._refresh_ssh_sessions()
        else:
            self._ssh_header.set_text("\u25b6 SSH Sessions")
            self._ssh_list_box.hide()

    def _refresh_ssh_sessions(self):
        """Refresh the SSH sessions list."""
        # Clear existing
        for child in self._ssh_list_box.get_children():
            self._ssh_list_box.remove(child)
        # Add placeholder
        lbl = Gtk.Label(label="  No active sessions")
        lbl.set_xalign(0)
        lbl.set_opacity(0.5)
        self._ssh_list_box.pack_start(lbl, False, False, 0)
        self._ssh_list_box.show_all()

    def refresh_focus_history(self):
        """Update the focus history display."""
        if not self._client._focus_history:
            return
        # Clear existing
        for child in self._focus_list_box.get_children():
            self._focus_list_box.remove(child)
        entries = self._client._focus_history.recent(5)
        if not entries:
            lbl = Gtk.Label(label="  No history")
            lbl.set_xalign(0)
            lbl.set_opacity(0.5)
            self._focus_list_box.pack_start(lbl, False, False, 0)
        else:
            for entry in entries:
                pane = entry.get("pane_id", "?")
                ws = entry.get("workspace_id", "?")
                lbl = Gtk.Label(label=f"  WS-{ws} / Pane {pane}")
                lbl.set_xalign(0)
                lbl.set_opacity(0.6)
                lbl.set_max_width_chars(30)
                lbl.set_ellipsize(True)
                self._focus_list_box.pack_start(lbl, False, False, 0)
        self._focus_list_box.show_all()

    def refresh_hook_indicators(self):
        """Update hook event indicator dots."""
        # Clear existing dots
        for child in self._hook_dots.get_children():
            self._hook_dots.remove(child)
        if not self._client._hook_registry:
            return
        recent = self._client._hook_registry.recent_events(5)
        for evt in recent:
            dot = Gtk.Label(label="\u25cf")
            dot.set_opacity(0.7)
            style = dot.get_style_context()
            event_name = evt.get("event", "")
            if "created" in event_name:
                style.add_class("unread-indicator")
            elif "closed" in event_name or "stopped" in event_name:
                style.add_class("waiting-indicator")
            else:
                dot.set_opacity(0.4)
            self._hook_dots.pack_start(dot, False, False, 0)
        self._hook_dots.show_all()

    # ── public API ──────────────────────────────────────────
    def refresh(self):
        """Fetch workspace list from daemon and update sidebar rows."""
        # L4 fix: skip daemon call when no workspaces exist after first check
        if self._empty_count > 2:
            return

        def _do_refresh():
            try:
                resp = self._client.send("workspace.list")
                if resp.get("ok"):
                    workspaces = resp.get("result", {}).get("workspaces", [])
                    GLib.idle_add(lambda: self._update_list(workspaces) or GLib.SOURCE_REMOVE)
            except Exception as e:
                GLib.idle_add(lambda: self._show_error(str(e)) or GLib.SOURCE_REMOVE)

        threading.Thread(target=_do_refresh, daemon=True).start()
        # Also refresh focus history and hook indicators
        GLib.idle_add(lambda: self.refresh_focus_history() or GLib.SOURCE_REMOVE)
        GLib.idle_add(lambda: self.refresh_hook_indicators() or GLib.SOURCE_REMOVE)


    def _show_error(self, msg):
        """Display an error message at the bottom of the sidebar."""
        self._error_label.set_opacity(0.9)
        # Auto-hide after 5 seconds
        GLib.timeout_add_seconds(5, lambda: (self._error_label.set_opacity(0.0), GLib.SOURCE_REMOVE)[1])

    # ── internals ───────────────────────────────────────────

    def _update_list(self, workspaces):
        """Sync sidebar rows with workspace list from daemon."""
        # L4 fix: track empty count
        if not workspaces:
            self._empty_count += 1
        else:
            self._empty_count = 0

        # Remove stale rows
        current_ids = {w.get("id") for w in workspaces}
        for ws_id in list(self._rows.keys()):
            if ws_id not in current_ids:
                row = self._rows.pop(ws_id)
                self._list_box.remove(row)

        # Update existing and add new
        for ws in workspaces:
            ws_id = ws.get("id")
            title = ws.get("title", f"Workspace {ws_id}")
            cwd = ws.get("cwd")
            branch = ws.get("git_branch")

            if ws_id in self._rows:
                row = self._rows[ws_id]
                row._title_label.set_text(title)
                row.set_metadata(cwd=cwd, branch=branch)
            else:
                row = WorkspaceRow(ws_id, title)
                row.set_metadata(cwd=cwd, branch=branch)
                self._rows[ws_id] = row
                self._list_box.add(row)

        self._list_box.show_all()

    def _on_row_selected(self, list_box, row):
        """Emit workspace-selected when a row is clicked."""
        if row and isinstance(row, WorkspaceRow):
            self.emit("workspace-selected", row.ws_id)

    # ── extensions ─────────────────────────────────────────

    def register_extension(self, ext):
        """Register a sidebar extension and add its panel to the stack."""
        self._extensions[ext.name] = ext
        widget = ext.get_widget()
        self._ext_widgets[ext.name] = widget
        self._stack.add_titled(widget, f"ext_{ext.name}", ext.name)
        self._update_ext_switcher()
        ext.activate()

    def unregister_extension(self, name):
        """Unregister a sidebar extension."""
        ext = self._extensions.pop(name, None)
        if ext:
            ext.deactivate()
        widget = self._ext_widgets.pop(name, None)
        if widget:
            self._stack.remove(widget)
        self._update_ext_switcher()

    def _update_ext_switcher(self):
        """Show or hide the extension switcher based on extension count."""
        has_exts = len(self._extensions) > 0
        if has_exts and not self._ext_switcher_visible:
            self._ext_switcher_visible = True
            self.pack_end(self._ext_switcher, False, False, 0)
            self._ext_switcher.show_all()
        elif not has_exts and self._ext_switcher_visible:
            self._ext_switcher_visible = False
            self._ext_switcher.hide()
            self.remove(self._ext_switcher)

    # ── authentication ──────────────────────────────────────

    def _on_auth_click(self, btn):
        """Handle auth button click — login or show user menu."""
        if self._auth.is_authenticated:
            # Show logout option
            menu = Gtk.Menu()
            item = Gtk.MenuItem(label=f"Logged in as {self._auth.current_user}")
            item.set_sensitive(False)
            menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())
            item = Gtk.MenuItem(label="Logout")
            item.connect("activate", lambda _: self._do_logout())
            menu.append(item)
            menu.show_all()
            menu.popup_at_widget(btn, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)
        else:
            self._do_login()

    def _do_login(self):
        """Show login dialog."""
        if self._auth.login():
            self._auth_btn.set_label(self._auth.current_user[:2].upper())
            self._auth_btn.set_tooltip_text(f"Logged in as {self._auth.current_user}")

    def _do_logout(self):
        """Logout and update UI."""
        self._auth.logout()
        self._auth_btn.set_label("\u2b1a")
        self._auth_btn.set_tooltip_text("Login")

    def require_auth(self, action="this action"):
        """Require authentication for an action. Returns True if OK."""
        return self._auth.require_auth(action)
