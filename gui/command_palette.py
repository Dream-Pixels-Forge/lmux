"""Command palette for lmux GUI — fuzzy search over all daemon commands."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GObject
import threading
import os
import sys
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daemon_client import DaemonClient


# ── Command registry ────────────────────────────────────────

COMMANDS = [
    # Workspace
    ("workspace.create",        "Create workspace",              "workspace"),
    ("workspace.close",         "Close workspace",               "workspace"),
    ("workspace.list",          "List workspaces",               "workspace"),
    ("workspace.current",       "Current workspace",             "workspace"),
    ("workspace.rename",        "Rename workspace",              "workspace"),
    ("workspace.reorder",       "Reorder workspace",             "workspace"),
    ("workspace.refresh",       "Refresh workspace metadata",    "workspace"),
    ("workspace.select",        "Select workspace by index",     "workspace"),
    ("workspace.env",           "Set workspace env var",         "workspace"),
    # Workspace groups
    ("workspace.group.create",  "Create workspace group",        "group"),
    ("workspace.group.add",     "Add workspace to group",        "group"),
    ("workspace.group.remove",  "Remove workspace from group",   "group"),
    ("workspace.group.list",    "List workspace groups",          "group"),
    # Surface (tab)
    ("surface.create",          "New surface (tab)",             "surface"),
    ("surface.close",           "Close surface",                 "surface"),
    ("surface.list",            "List surfaces",                 "surface"),
    ("surface.focus",           "Focus surface",                 "surface"),
    ("surface.rename",          "Rename surface",                "surface"),
    ("surface.split",           "Split surface",                 "surface"),
    ("surface.reorder",         "Reorder surface",               "surface"),
    ("surface.send_text",       "Send text to surface",          "surface"),
    ("surface.send_key",        "Send key to surface",           "surface"),
    # Pane
    ("pane.list",               "List panes",                    "pane"),
    ("pane.focus",              "Focus pane",                    "pane"),
    ("pane.focus_dir",          "Focus pane by direction",       "pane"),
    ("pane.resize",             "Resize pane",                   "pane"),
    # Notification
    ("notification.create",     "Create notification",           "notification"),
    ("notification.list",       "List notifications",            "notification"),
    ("notification.mark_read",  "Mark notification read",        "notification"),
    ("notification.clear",      "Clear notifications",           "notification"),
    # Agent
    ("agent.spawn",             "Spawn agent",                   "agent"),
    ("agent.list",              "List agents",                   "agent"),
    ("agent.stop",              "Stop agent",                    "agent"),
    # Snapshot / Session
    ("snapshot.save",           "Save snapshot",                 "snapshot"),
    ("snapshot.load",           "Load snapshot",                 "snapshot"),
    ("session.save",            "Save session",                  "session"),
    ("session.restore",         "Restore session",               "session"),
    # System
    ("tree",                    "Show workspace tree",           "system"),
    ("ping",                    "Ping daemon",                   "system"),
    ("capabilities",            "Daemon capabilities",           "system"),
    ("config.get",              "Get config value",              "config"),
    ("config.set",              "Set config value",              "config"),
    ("reload-config",           "Reload configuration",          "config"),
    ("events",                  "Stream events",                 "system"),
    ("ssh",                     "SSH connect",                   "system"),
    ("help",                    "Show help",                     "system"),
]


def _fuzzy_match(query, text):
    """Simple fuzzy match: all query chars appear in order in text."""
    if not query:
        return True
    q = query.lower()
    t = text.lower()
    qi = 0
    for ch in t:
        if qi < len(q) and ch == q[qi]:
            qi += 1
    return qi == len(q)


# ── Palette widget ──────────────────────────────────────────

class CommandPalette(Gtk.Window):
    """Floating command palette overlay with fuzzy search."""

    __gsignals__ = {
        "command-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent=None, client=None):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_title("Command Palette")
        self.set_default_size(500, 400)
        self.set_modal(True)
        self.set_transient_for(parent)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        self._client = client
        self._commands = list(COMMANDS)
        self._selected_index = 0

        # Dark theme
        css = b"""
        .palette-window {
            background: #1e1e2e;
            border: 1px solid #585b70;
            border-radius: 8px;
        }
        .palette-entry {
            background: #313244;
            color: #cdd6f4;
            font-size: 14px;
            padding: 8px 12px;
            border: none;
            border-radius: 6px;
        }
        .palette-entry:focus {
            border: 1px solid #89b4fa;
        }
        .palette-list {
            background: #1e1e2e;
        }
        .palette-row {
            padding: 6px 12px;
            color: #cdd6f4;
            font-size: 13px;
        }
        .palette-row:selected {
            background: #313244;
            color: #89b4fa;
        }
        .palette-category {
            padding: 4px 12px;
            color: #6c7086;
            font-size: 11px;
            font-weight: bold;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Main container
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.get_style_context().add_class("palette-window")
        self.add(outer)

        # Search entry
        self._entry = Gtk.SearchEntry()
        self._entry.get_style_context().add_class("palette-entry")
        self._entry.set_placeholder_text("Type a command...")
        self._entry.connect("search-changed", self._on_search_changed)
        self._entry.connect("key-press-event", self._on_key_press)
        self._entry.connect("activate", self._on_activate)
        outer.pack_start(self._entry, False, False, 8)

        # Scrolled list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(280)
        scroll.set_max_content_height(350)
        outer.pack_start(scroll, True, True, 0)

        self._listbox = Gtk.ListBox()
        self._listbox.get_style_context().add_class("palette-list")
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-activated", self._on_row_activated)
        scroll.add(self._listbox)

        self._populate_list("")

    def _populate_list(self, query):
        """Fill listbox with filtered commands."""
        # Clear existing rows
        for child in self._listbox.get_children():
            self._listbox.remove(child)

        self._filtered = []
        seen_categories = set()

        for cmd, label, category in self._commands:
            if not _fuzzy_match(query, cmd) and not _fuzzy_match(query, label):
                continue

            # Category header
            if category not in seen_categories:
                seen_categories.add(category)
                cat_label = Gtk.Label(label=category.upper())
                cat_label.set_xalign(0)
                cat_label.get_style_context().add_class("palette-category")
                row = Gtk.ListBoxRow()
                row.set_activatable(False)
                row.set_selectable(False)
                row.add(cat_label)
                self._listbox.add(row)

            # Command row
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cmd_label = Gtk.Label(label=cmd)
            cmd_label.set_xalign(0)
            cmd_label.set_hexpand(True)
            box.pack_start(cmd_label, True, True, 0)

            desc_label = Gtk.Label(label=label)
            desc_label.set_xalign(1)
            desc_label.set_opacity(0.6)
            box.pack_end(desc_label, False, False, 0)

            row = Gtk.ListBoxRow()
            row.add(box)
            row._command = cmd
            self._listbox.add(row)
            self._filtered.append(cmd)

        self._listbox.show_all()
        self._selected_index = 0
        if self._filtered:
            first_cmd_row = self._get_cmd_row(0)
            if first_cmd_row:
                self._listbox.select_row(first_cmd_row)

    def _get_cmd_row(self, index):
        """Get the nth command row (skipping category headers)."""
        cmd_idx = 0
        for child in self._listbox.get_children():
            if hasattr(child, '_command'):
                if cmd_idx == index:
                    return child
                cmd_idx += 1
        return None

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        self._populate_list(query)

    def _on_key_press(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        if keyname == "Escape":
            self.hide()
            return True
        elif keyname == "Down":
            self._move_selection(1)
            return True
        elif keyname == "Up":
            self._move_selection(-1)
            return True
        return False

    def _move_selection(self, delta):
        if not self._filtered:
            return
        self._selected_index = max(0, min(len(self._filtered) - 1, self._selected_index + delta))
        row = self._get_cmd_row(self._selected_index)
        if row:
            self._listbox.select_row(row)
            self._listbox.scroll_to_row(row, Gtk.ScrollType.MOVE_IF_VISIBLE)

    def _on_activate(self, entry):
        """Enter pressed — execute selected command."""
        row = self._listbox.get_selected_row()
        if row and hasattr(row, '_command'):
            self._execute(row._command)

    def _on_row_activated(self, listbox, row):
        """Row clicked — execute command."""
        if hasattr(row, '_command'):
            self._execute(row._command)

    def _execute(self, command):
        """Execute a command via the daemon and close palette."""
        self.hide()
        GLib.idle_add(lambda: (self.emit("command-selected", command), GLib.SOURCE_REMOVE)[1])

    def show_palette(self):
        """Show the palette and focus the search entry."""
        self._entry.set_text("")
        self._populate_list("")
        self.show_all()
        self._entry.grab_focus()


# ── Standalone launcher (for testing) ───────────────────────

if __name__ == "__main__":
    win = Gtk.Window(title="Palette Test")
    win.set_default_size(400, 200)

    palette = CommandPalette(parent=win)

    def on_cmdSelected(widget, cmd):
        print(f"Selected: {cmd}")

    palette.connect("command-selected", on_cmdSelected)

    btn = Gtk.Button(label="Open Palette (Ctrl+Shift+P)")
    btn.connect("clicked", lambda _: palette.show_palette())
    win.add(btn)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
