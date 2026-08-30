"""Workspace groups GUI for lmux — manage workspace groups."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import os
import sys
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("lmux.workspace_groups")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daemon_client import DaemonClient


class WorkspaceGroupItem(Gtk.ListBoxRow):
    """A row representing a workspace group."""

    def __init__(self, group_name: str, workspace_ids: List[str]):
        super().__init__()
        self.group_name = group_name
        self.workspace_ids = workspace_ids

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Group name
        self._name_label = Gtk.Label(label=group_name, xalign=0)
        self._name_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        box.pack_start(self._name_label, True, True, 0)

        # Workspace count badge
        count_label = Gtk.Label(label=f"{len(workspace_ids)}")
        count_label.set_xalign(0.5)
        count_label.set_yalign(0.5)
        count_label.set_size_request(24, 24)
        ctx = count_label.get_style_context()
        ctx.add_class("workspace-count")
        box.pack_end(count_label, False, False, 0)

        self.add(box)
        self.show_all()


class WorkspaceGroupsPanel(Gtk.Box):
    """Panel for managing workspace groups."""

    def __init__(self, client: DaemonClient, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._parent_window = parent_window
        self._groups: Dict[str, List[str]] = {}

        self._setup_ui()

    def _setup_ui(self):
        """Build the workspace groups UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_start(8)
        header_box.set_margin_end(8)
        header_box.set_margin_top(8)
        header_box.set_margin_bottom(8)

        title = Gtk.Label(label="Workspace Groups", xalign=0)
        ctx = title.get_style_context()
        ctx.add_class("workspace-header")
        header_box.pack_start(title, True, True, 0)

        # Add group button
        add_btn = Gtk.Button(label="+")
        add_btn.set_size_request(28, 28)
        add_btn.connect("clicked", self._on_add_group)
        header_box.pack_end(add_btn, False, False, 0)

        self.pack_start(header_box, False, False, 0)

        # Groups list
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.connect("row-selected", self._on_group_selected)
        self.pack_start(self._listbox, True, True, 0)

        # Action bar
        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_margin_start(8)
        action_box.set_margin_end(8)
        action_box.set_margin_top(8)
        action_box.set_margin_bottom(8)

        self._add_ws_btn = Gtk.Button(label="Add Workspace")
        self._add_ws_btn.set_sensitive(False)
        self._add_ws_btn.connect("clicked", self._on_add_workspace_to_group)
        action_box.pack_start(self._add_ws_btn, True, True, 0)

        self._remove_ws_btn = Gtk.Button(label="Remove Workspace")
        self._remove_ws_btn.set_sensitive(False)
        self._remove_ws_btn.connect("clicked", self._on_remove_workspace_from_group)
        action_box.pack_end(self._remove_ws_btn, True, True, 0)

        self.pack_end(action_box, False, False, 0)

        # Load groups
        self.refresh()

    def refresh(self):
        """Refresh the groups list from daemon."""
        def _load():
            try:
                resp = self._client.send("workspace.group.list", {})
                if resp.get("ok"):
                    result = resp.get("result", {})
                    groups = result.get("groups", {})
                    GLib.idle_add(lambda: self._update_groups(groups))
            except Exception as e:
                logger.error(f"Failed to load workspace groups: {e}")

        import threading
        threading.Thread(target=_load, daemon=True).start()

    def _update_groups(self, groups: Dict[str, List[str]]):
        """Update the groups list."""
        self._groups = groups
        self._listbox.foreach(lambda child: self._listbox.remove(child))

        for group_name, workspace_ids in groups.items():
            row = WorkspaceGroupItem(group_name, workspace_ids)
            self._listbox.add(row)

        self._listbox.show_all()

    def _on_group_selected(self, listbox, row):
        """Handle group selection."""
        if row:
            self._add_ws_btn.set_sensitive(True)
            self._remove_ws_btn.set_sensitive(True)
        else:
            self._add_ws_btn.set_sensitive(False)
            self._remove_ws_btn.set_sensitive(False)

    def _on_add_group(self, button):
        """Show dialog to create a new group."""
        dialog = Gtk.Dialog(
            title="Create Workspace Group",
            transient_for=self._parent_window,
            flags=0,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Create", Gtk.ResponseType.OK)
        dialog.set_default_size(300, 100)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)

        label = Gtk.Label(label="Group name:", xalign=0)
        box.pack_start(label, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g., frontend, backend, devops")
        box.pack_start(entry, False, False, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            group_name = entry.get_text().strip()
            if group_name:
                self._create_group(group_name)

        dialog.destroy()

    def _create_group(self, name: str):
        """Create a new workspace group."""
        def _create():
            try:
                resp = self._client.send("workspace.group.create", {"name": name})
                if resp.get("ok"):
                    GLib.idle_add(self.refresh)
            except Exception as e:
                logger.error(f"Failed to create group: {e}")

        import threading
        threading.Thread(target=_create, daemon=True).start()

    def _on_add_workspace_to_group(self, button):
        """Add a workspace to the selected group."""
        row = self._listbox.get_selected_row()
        if not row:
            return

        group_name = row.group_name

        # Show workspace selection dialog
        dialog = Gtk.Dialog(
            title=f"Add Workspace to {group_name}",
            transient_for=self._parent_window,
            flags=0,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.OK)
        dialog.set_default_size(300, 200)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)

        label = Gtk.Label(label="Select workspace:", xalign=0)
        box.pack_start(label, False, False, 0)

        # Workspace list
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        # Load workspaces
        def _load_workspaces():
            try:
                resp = self._client.send("workspace.list", {})
                if resp.get("ok"):
                    workspaces = resp.get("result", [])
                    GLib.idle_add(lambda: self._populate_workspace_list(listbox, workspaces, group_name))
            except Exception as e:
                logger.error(f"Failed to load workspaces: {e}")

        import threading
        threading.Thread(target=_load_workspaces, daemon=True).start()

        box.pack_start(listbox, True, True, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            selected_row = listbox.get_selected_row()
            if selected_row:
                ws_id = selected_row.workspace_id
                self._add_workspace_to_group(group_name, ws_id)

        dialog.destroy()

    def _populate_workspace_list(self, listbox, workspaces, group_name):
        """Populate workspace list for selection."""
        for ws in workspaces:
            ws_id = ws.get("id", "")
            ws_title = ws.get("title", f"Workspace {ws_id}")

            # Skip workspaces already in this group
            if ws_id in self._groups.get(group_name, []):
                continue

            row = Gtk.ListBoxRow()
            row.workspace_id = ws_id

            label = Gtk.Label(label=f"{ws_title} (ID: {ws_id})", xalign=0)
            label.set_margin_start(8)
            label.set_margin_end(8)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.add(label)

            listbox.add(row)

        listbox.show_all()

    def _add_workspace_to_group(self, group_name: str, ws_id: str):
        """Add a workspace to a group."""
        def _add():
            try:
                resp = self._client.send("workspace.group.add", {
                    "name": group_name,
                    "workspace_id": ws_id
                })
                if resp.get("ok"):
                    GLib.idle_add(self.refresh)
            except Exception as e:
                logger.error(f"Failed to add workspace to group: {e}")

        import threading
        threading.Thread(target=_add, daemon=True).start()

    def _on_remove_workspace_from_group(self, button):
        """Remove a workspace from the selected group."""
        row = self._listbox.get_selected_row()
        if not row:
            return

        group_name = row.group_name
        workspace_ids = self._groups.get(group_name, [])

        if not workspace_ids:
            return

        # Show workspace selection dialog
        dialog = Gtk.Dialog(
            title=f"Remove Workspace from {group_name}",
            transient_for=self._parent_window,
            flags=0,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Remove", Gtk.ResponseType.OK)
        dialog.set_default_size(300, 200)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)

        label = Gtk.Label(label="Select workspace to remove:", xalign=0)
        box.pack_start(label, False, False, 0)

        # Workspace list
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

        for ws_id in workspace_ids:
            row_item = Gtk.ListBoxRow()
            row_item.workspace_id = ws_id

            # Get workspace title
            def _get_title(ws_id):
                try:
                    resp = self._client.send("workspace.get", {"id": ws_id})
                    if resp.get("ok"):
                        return resp.get("result", {}).get("title", f"Workspace {ws_id}")
                except:
                    pass
                return f"Workspace {ws_id}"

            title = _get_title(ws_id)
            label_item = Gtk.Label(label=f"{title} (ID: {ws_id})", xalign=0)
            label_item.set_margin_start(8)
            label_item.set_margin_end(8)
            label_item.set_margin_top(4)
            label_item.set_margin_bottom(4)
            row_item.add(label_item)

            listbox.add(row_item)

        listbox.show_all()
        box.pack_start(listbox, True, True, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            selected_row = listbox.get_selected_row()
            if selected_row:
                ws_id = selected_row.workspace_id
                self._remove_workspace_from_group(group_name, ws_id)

        dialog.destroy()

    def _remove_workspace_from_group(self, group_name: str, ws_id: str):
        """Remove a workspace from a group."""
        def _remove():
            try:
                resp = self._client.send("workspace.group.remove", {
                    "name": group_name,
                    "workspace_id": ws_id
                })
                if resp.get("ok"):
                    GLib.idle_add(self.refresh)
            except Exception as e:
                logger.error(f"Failed to remove workspace from group: {e}")

        import threading
        threading.Thread(target=_remove, daemon=True).start()


def add_workspace_groups_to_sidebar(sidebar, client, parent_window):
    """Add workspace groups panel to the sidebar."""
    # Create a notebook for tabs
    notebook = Gtk.Notebook()

    # Workspace groups panel
    groups_panel = WorkspaceGroupsPanel(client, parent_window)
    notebook.append_page(groups_panel, Gtk.Label(label="Groups"))

    # Add to sidebar
    sidebar.pack_end(notebook, True, True, 0)
    sidebar.show_all()

    return groups_panel
