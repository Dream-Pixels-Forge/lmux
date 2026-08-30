"""GTK4 sidebar panel for lmux — ported from GTK3 with GPU rendering."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, GObject
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daemon_client import DaemonClient


class WorkspaceRow(Gtk.ListBoxRow):
    """A single workspace entry in the sidebar."""

    def __init__(self, ws_id, title, unread=False, waiting=False):
        super().__init__()
        self.ws_id = ws_id

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Status indicator
        self._status = Gtk.Label(label="\u25cf")
        self._status.set_opacity(0.0)
        self._status.get_style_context().add_class("status-indicator")
        box.append(self._status)

        # Title
        self._title_label = Gtk.Label(label=title)
        self._title_label.set_xalign(0)
        self._title_label.set_hexpand(True)
        self._title_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.append(self._title_label)

        # Branch indicator
        self._branch_label = Gtk.Label(label="")
        self._branch_label.set_opacity(0.6)
        self._branch_label.set_xalign(1)
        box.append(self._branch_label)

        self.set_child(box)
        self.set_activatable(True)

        if unread:
            self.set_status("unread")
        if waiting:
            self.set_status("waiting")

    def set_status(self, status):
        if status == "unread":
            self._status.set_opacity(1.0)
            self._status.get_style_context().add_class("unread")
        elif status == "waiting":
            self._status.set_opacity(1.0)
            self._status.get_style_context().add_class("waiting-indicator")
        else:
            self._status.set_opacity(0.0)
            self._status.get_style_context().remove_class("unread")
            self._status.get_style_context().remove_class("waiting-indicator")

    def set_metadata(self, cwd=None, branch=None):
        if branch:
            self._branch_label.set_text(branch)
            self._branch_label.set_opacity(0.6)
        else:
            self._branch_label.set_text("")
            self._branch_label.set_opacity(0.0)


class Sidebar(Gtk.Box):
    """Sidebar panel listing all workspaces."""

    __gsignals__ = {
        "workspace-selected": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        "workspace-create-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, client=None, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client or DaemonClient()
        self._rows = {}
        self._empty_count = 0

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_margin_start(8)
        header.set_margin_end(8)
        header.set_margin_top(6)
        header.set_margin_bottom(4)

        lbl = Gtk.Label(label="Workspaces")
        lbl.set_xalign(0)
        lbl.get_style_context().add_class("workspace-header")
        header.append(lbl)

        add_btn = Gtk.Button(label="+")
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", lambda _: self.emit("workspace-create-requested"))
        header.append(add_btn)

        self.append(header)

        # Workspace list
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_row_selected)
        self._listbox.set_vexpand(True)
        self.append(self._listbox)

        # Error label
        self._error_label = Gtk.Label(label="")
        self._error_label.set_xalign(0)
        self._error_label.get_style_context().add_class("error")
        self._error_label.set_opacity(0.0)
        self.append(self._error_label)

    def refresh(self):
        """Fetch workspace list from daemon."""
        if self._empty_count > 2:
            return

        def _do_refresh():
            try:
                resp = self._client.send("workspace.list")
                if resp.get("ok"):
                    workspaces = resp.get("result", {}).get("workspaces", [])
                    GLib.idle_add(lambda: self._update_list(workspaces))
            except Exception as e:
                GLib.idle_add(lambda: self._show_error(str(e)))

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _show_error(self, msg):
        self._error_label.set_text(msg)
        self._error_label.set_opacity(0.9)
        GLib.timeout_add_seconds(5, lambda: self._error_label.set_opacity(0.0))

    def _update_list(self, workspaces):
        if not workspaces:
            self._empty_count += 1
        else:
            self._empty_count = 0

        current_ids = {w.get("id") for w in workspaces}
        for ws_id in list(self._rows.keys()):
            if ws_id not in current_ids:
                row = self._rows.pop(ws_id)
                self._listbox.remove(row)

        for ws in workspaces:
            ws_id = ws.get("id")
            title = ws.get("title", f"Workspace {ws_id}")
            branch = ws.get("git_branch")

            if ws_id in self._rows:
                row = self._rows[ws_id]
                row._title_label.set_text(title)
                row.set_metadata(branch=branch)
            else:
                row = WorkspaceRow(ws_id, title)
                row.set_metadata(branch=branch)
                self._rows[ws_id] = row
                self._listbox.append(row)

    def _on_row_selected(self, list_box, row):
        if row and isinstance(row, WorkspaceRow):
            self.emit("workspace-selected", row.ws_id)
