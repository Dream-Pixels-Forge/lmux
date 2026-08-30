"""Sidebar extensions plugin system for lmux GUI.

Extensions are Python files in ~/.config/lmux/extensions/ that define
a class inheriting from ExtensionBase. The loader discovers, imports,
and manages their lifecycle.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import importlib.util
import os
import sys
import traceback


# ── Extension base class ────────────────────────────────────

class ExtensionBase:
    """Base class for sidebar extensions. Subclass this in your extension."""

    name = "Unnamed Extension"
    description = ""
    icon = "text-x-generic"  # GTK icon name

    def __init__(self, sidebar=None, client=None):
        self.sidebar = sidebar
        self.client = client
        self._active = False

    def activate(self):
        """Called when the extension panel is opened."""
        self._active = True

    def deactivate(self):
        """Called when the extension panel is closed."""
        self._active = False

    def get_widget(self):
        """Return a Gtk.Widget to display in the sidebar panel."""
        label = Gtk.Label(label=f"Extension '{self.name}' has no widget.")
        label.set_opacity(0.5)
        return label

    def get_menu_items(self):
        """Return a list of (label, callback) tuples for the extension's context menu."""
        return []

    def on_event(self, event):
        """Called when a daemon event arrives. Override to react to events."""
        pass


# ── Extension loader ────────────────────────────────────────

class ExtensionLoader:
    """Discovers, loads, and manages sidebar extensions."""

    def __init__(self, extension_dirs=None):
        """
        Args:
            extension_dirs: List of directories to scan for extensions.
                          Defaults to [~/.config/lmux/extensions/, <package>/extensions/]
        """
        if extension_dirs is None:
            extension_dirs = [
                os.path.expanduser("~/.config/lmux/extensions"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "extensions"),
            ]
        self._dirs = extension_dirs
        self._extensions = {}  # name -> ExtensionBase instance
        self._loaded_modules = {}  # name -> module

    def discover(self):
        """Scan extension directories and return list of (name, description) tuples."""
        found = []
        for ext_dir in self._dirs:
            if not os.path.isdir(ext_dir):
                continue
            for fname in os.listdir(ext_dir):
                if fname.endswith(".py") and not fname.startswith("_"):
                    ext_path = os.path.join(ext_dir, fname)
                    try:
                        spec = importlib.util.spec_from_file_location(
                            f"lmux_ext_{fname[:-3]}", ext_path
                        )
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            # Find ExtensionBase subclasses
                            for attr_name in dir(module):
                                attr = getattr(module, attr_name)
                                if (isinstance(attr, type)
                                    and issubclass(attr, ExtensionBase)
                                    and attr is not ExtensionBase):
                                    ext = attr()
                                    found.append((ext.name, ext.description, ext))
                                    self._loaded_modules[ext.name] = module
                    except Exception as e:
                        print(f"[lmux] Failed to load extension {fname}: {e}",
                              file=sys.stderr)
                        traceback.print_exc()
        return found

    def load_all(self, sidebar=None, client=None):
        """Discover and instantiate all extensions."""
        found = self.discover()
        for name, desc, ext in found:
            ext.sidebar = sidebar
            ext.client = client
            self._extensions[name] = ext
        return list(self._extensions.values())

    def get(self, name):
        """Get an extension by name."""
        return self._extensions.get(name)

    def activate(self, name):
        """Activate an extension by name."""
        ext = self._extensions.get(name)
        if ext and not ext._active:
            ext.activate()

    def deactivate(self, name):
        """Deactivate an extension by name."""
        ext = self._extensions.get(name)
        if ext and ext._active:
            ext.deactivate()

    def deactivate_all(self):
        """Deactivate all extensions."""
        for ext in self._extensions.values():
            if ext._active:
                ext.deactivate()

    def get_all(self):
        """Return all loaded extensions."""
        return list(self._extensions.values())

    def notify_event(self, event):
        """Forward a daemon event to all active extensions."""
        for ext in self._extensions.values():
            if ext._active:
                try:
                    ext.on_event(event)
                except Exception:
                    pass  # Don't let extension errors break the event loop


# ── Built-in extensions ─────────────────────────────────────

class ProcessListExtension(ExtensionBase):
    """Shows running processes in the workspace."""
    name = "Processes"
    description = "List running processes"
    icon = "utilities-system-monitor"

    def get_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)

        title = Gtk.Label(label="Running Processes")
        title.set_xalign(0)
        title.set_opacity(0.7)
        box.pack_start(title, False, False, 0)

        self._list = Gtk.ListBox()
        box.pack_start(self._list, True, True, 0)

        self.refresh()
        return box

    def refresh(self):
        """Refresh process list."""
        for child in self._list.get_children():
            self._list.remove(child)
        try:
            import subprocess
            result = subprocess.run(
                ["ps", "aux", "--sort=-pcpu"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n")[1:11]:  # top 10
                row = Gtk.ListBoxRow()
                lbl = Gtk.Label(label=line.strip())
                lbl.set_xalign(0)
                lbl.set_ellipsize(3)
                row.add(lbl)
                self._list.add(row)
            self._list.show_all()
        except Exception:
            pass


class FileBrowserExtension(ExtensionBase):
    """Simple file browser for the workspace directory."""
    name = "Files"
    description = "Browse workspace files"
    icon = "folder"

    def get_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)

        title = Gtk.Label(label="File Browser")
        title.set_xalign(0)
        title.set_opacity(0.7)
        box.pack_start(title, False, False, 0)

        self._list = Gtk.ListBox()
        scroll = Gtk.ScrolledWindow()
        scroll.add(self._list)
        scroll.set_min_content_height(200)
        box.pack_start(scroll, True, True, 0)

        self.refresh()
        return box

    def refresh(self, path=None):
        """Refresh file list."""
        for child in self._list.get_children():
            self._list.remove(child)

        if path is None:
            path = os.getcwd()

        try:
            entries = sorted(os.listdir(path))
            # Parent directory
            if path != "/":
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl = Gtk.Label(label="\u2b06 .. (parent)")
                lbl.set_xalign(0)
                box.pack_start(lbl, True, True, 0)
                row.add(box)
                row._path = os.path.dirname(path)
                row.connect("activate", lambda r: self.refresh(r._path))
                self._list.add(row)

            for entry in entries:
                if entry.startswith("."):
                    continue
                full = os.path.join(path, entry)
                is_dir = os.path.isdir(full)
                row = Gtk.ListBoxRow()
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                icon = "\U0001f4c1" if is_dir else "\U0001f4c4"
                lbl = Gtk.Label(label=f"{icon} {entry}")
                lbl.set_xalign(0)
                box.pack_start(lbl, True, True, 0)
                row.add(box)
                if is_dir:
                    row._path = full
                    row.connect("activate", lambda r: self.refresh(r._path))
                self._list.add(row)

            self._list.show_all()
        except PermissionError:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label="(permission denied)")
            lbl.set_opacity(0.5)
            row.add(lbl)
            self._list.add(row)
            self._list.show_all()


# ── Extension registry (for sidebar integration) ────────────

# Built-in extensions that ship with lmux
BUILTIN_EXTENSIONS = [
    ProcessListExtension,
    FileBrowserExtension,
]
