#!/usr/bin/env python3
"""lmux-gui — GTK3/VTE terminal frontend for lmux daemon."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gtk, GLib, Gdk

import os
import sys
import threading
import logging

# Set up logging for optional import warnings
logger = logging.getLogger("lmux.gui")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daemon_client import DaemonClient
from sidebar import Sidebar
from terminal import TerminalNotebook, TerminalWidget
from command_palette import CommandPalette
from browser import BrowserPanel
from updater import add_update_notification, SparkleUpdater, UpdateDialog
from canvas import CanvasContainer
from shortcuts import get_shortcuts_manager
from workspace_groups import WorkspaceGroupsPanel
from browser_api import setup_browser_api, setup_browser_api_bridge
from browser_import import create_import_dialog, perform_import
from localization import get_localization, _
from file_explorer import FileExplorer
from tiling import TilingManager, TilingPanel, TilingLayout

# New feature imports for cmux parity
from hooks_setup import HooksSetup
from notifications import NotificationManager, NotificationPanel, NotificationBadge
from ssh_advanced import SSHSessionManager, SSHBrowserRouter, create_ssh_session_panel
from agent_sessions import AgentSessionManager, AgentSessionPanel
from diff_viewer import DiffViewerPanel
from workspace_colors import WorkspaceColorManager
from session_index import SessionIndexManager, SessionIndexPanel
from custom_commands import CustomCommandsManager
from browser_find import BrowserFindBar
from font_size import FontSizeManager, setup_font_shortcuts
from session_reopen import SessionReopenManager, setup_session_reopen
from settings_ui import SettingsManager, SettingsWindow
from multiple_windows import WindowManager
from tmux_compat import TmuxCompat

# Optional feature imports with graceful fallback
try:
    from ssh_client import SSHClient
except ImportError:
    SSHClient = None
    logger.debug("SSH client module not available (ssh_client not found)")
try:
    from agent_hooks import HookRegistry
except ImportError:
    HookRegistry = None
    logger.debug("Agent hooks module not available (agent_hooks not found)")
try:
    from auto_naming import AutoNamer
except ImportError:
    AutoNamer = None
    logger.debug("Auto-naming module not available (auto_naming not found)")
try:
    from focus_history import FocusHistory
except ImportError:
    FocusHistory = None
    logger.debug("Focus history module not available (focus_history not found)")

# ============================================================
# Desktop notification helper (freedesktop notify-send)
# ============================================================

def _notify_desktop(title, body):
    """Fire a desktop notification via notify-send (non-blocking)."""
    import subprocess
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=lmux", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass  # notify-send not available


# ============================================================
# Settings dialog
# ============================================================

class SettingsDialog(Gtk.Dialog):
    """Configuration dialog for lmux preferences."""

    def __init__(self, parent, client=None):
        super().__init__(title="lmux Settings", transient_for=parent, flags=0)
        self._client = client
        self.set_default_size(400, 300)

        box = self.get_content_area()
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_margin_top(16)
        grid.set_margin_bottom(16)
        box.pack_start(grid, True, True, 0)

        # Font family
        lbl = Gtk.Label(label="Font Family", xalign=0)
        grid.attach(lbl, 0, 0, 1, 1)
        self._font_family = Gtk.Entry()
        self._font_family.set_text("monospace")
        grid.attach(self._font_family, 1, 0, 1, 1)

        # Font size
        lbl = Gtk.Label(label="Font Size", xalign=0)
        grid.attach(lbl, 0, 1, 1, 1)
        adj = Gtk.Adjustment(value=12, lower=6, upper=72, step_inc=1)
        self._font_size = Gtk.SpinButton(adjustment=adj)
        grid.attach(self._font_size, 1, 1, 1, 1)

        # Theme
        lbl = Gtk.Label(label="Theme", xalign=0)
        grid.attach(lbl, 0, 2, 1, 1)
        self._theme = Gtk.ComboBoxText()
        self._theme.append_text("dark")
        self._theme.append_text("light")
        self._theme.set_active(0)
        grid.attach(self._theme, 1, 2, 1, 1)

        # Auto-save
        lbl = Gtk.Label(label="Auto-Save Session", xalign=0)
        grid.attach(lbl, 0, 3, 1, 1)
        self._auto_save = Gtk.Switch()
        self._auto_save.set_active(True)
        grid.attach(self._auto_save, 1, 3, 1, 1)

        # Load current values from daemon
        self._load_config()

        # Buttons
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.OK)

        self.show_all()

    def _load_config(self):
        """Fetch current config from daemon and populate fields."""
        if not self._client:
            return

        def _fetch():
            try:
                resp = self._client.send("config.get", {})
                if resp.get("ok"):
                    cfg = resp.get("result", {})
                    GLib.idle_add(self._apply_config, cfg)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_config(self, cfg):
        self._font_family.set_text(cfg.get("font_family", "monospace"))
        self._font_size.set_value(cfg.get("font_size", 12))
        theme = cfg.get("theme", "dark")
        self._theme.set_active(0 if theme == "dark" else 1)
        self._auto_save.set_active(cfg.get("auto_save_session", True))

    def get_settings(self):
        """Return a dict of the configured settings."""
        return {
            "font_family": self._font_family.get_text().strip(),
            "font_size": int(self._font_size.get_value()),
            "theme": self._theme.get_active_text() or "dark",
            "auto_save_session": self._auto_save.get_active(),
        }


# ============================================================
# Workspace view — surfaces notebook + per-surface pane management
# ============================================================

class WorkspaceView(Gtk.Box):
    """Container for a single workspace's surfaces notebook.

    Used as a page in the main stack.
    """

    def __init__(self, ws_id, title="Workspace", client=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.ws_id = ws_id
        self._title = title
        self._client = client
        self._surface_counter = 0

        # Header bar with workspace name
        self._header = Gtk.EventBox()
        self._header.get_style_context().add_class("workspace-header")
        self._header_label = Gtk.Label(label=title)
        self._header_label.set_xalign(0)
        self._header_label.set_margin_start(8)
        self._header_label.set_margin_top(2)
        self._header_label.set_margin_bottom(2)
        self._header.add(self._header_label)
        self.pack_start(self._header, False, False, 0)

        self._header_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(self._header_sep, False, False, 0)

        # Tiling layout panel
        self._tiling_manager = None  # Will be set when notebook is added
        self._tiling_panel = None

        # Notebook of surfaces (tabs)
        self._notebook = TerminalNotebook(client)
        self.pack_start(self._notebook, True, True, 0)

        # Tiling manager for the notebook
        self._tiling_manager = TilingManager(self._notebook)
        self._tiling_panel = TilingPanel(self._tiling_manager)
        self._tiling_panel.set_no_show_all(True)
        self.pack_start(self._tiling_panel, False, False, 0)

        # Keyboard shortcuts for tiling
        self._setup_tiling_shortcuts()

        # Start with one initial surface
        self._initial_surface()

        self.show_all()

    # ── properties ──────────────────────────────────────────

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value
        self._header_label.set_text(value)

    @property
    def notebook(self):
        return self._notebook

    # ── surface / pane lifecycle ────────────────────────────

    def _initial_surface(self):
        """Create the first surface for this workspace."""
        self._surface_counter += 1
        container, term = self._notebook.add_surface(
            surface_id=self._surface_counter, title="Shell"
        )
        return container, term

    def create_surface(self):
        """Add a new surface (tab) to this workspace."""
        self._surface_counter += 1
        if self._client:
            # Optionally create on daemon too
            try:
                self._client.send("surface.create", {"workspace_id": self.ws_id})
            except Exception:
                pass
        container, term = self._notebook.add_surface(
            surface_id=self._surface_counter, title="Shell"
        )
        # Switch to the new surface
        return container, term

    def close_current_surface(self):
        """Close the currently visible surface tab."""
        sid, page_n = self._notebook.current_pane_ids()
        if sid is not None and self._notebook.surface_count() > 1:
            self._notebook.remove_surface(sid)

    def split_pane(self, orientation="v"):
        """Split the current surface's focused pane."""
        container = self._notebook.current_container()
        if container is None:
            return
        focused = container._focused
        if focused is None and container._pane_order:
            focused = container._pane_order[0]
        if focused is None:
            return
        new_id = max(container._panes.keys()) + 1 if container._panes else 1
        term = container.split_pane(focused, orientation=orientation, new_id=new_id)
        if term:
            term.grab_focus_to_vte()

    def close_current_pane(self):
        """Close the current surface's focused pane."""
        container = self._notebook.current_container()
        if container:
            container.close_focused()

    def focus_next_pane(self):
        container = self._notebook.current_container()
        if container:
            container.focus_next()
            term = container.focused_terminal()
            if term:
                term.grab_focus_to_vte()

    def focus_prev_pane(self):
        container = self._notebook.current_container()
        if container:
            container.focus_prev()
            term = container.focused_terminal()
            if term:
                term.grab_focus_to_vte()

    # ── tiling ──────────────────────────────────────────────

    def _setup_tiling_shortcuts(self):
        """Set up keyboard shortcuts for tiling layouts."""
        shortcuts = [
            ("<Ctrl><Alt>g", TilingLayout.GRID),
            ("<Ctrl><Alt>h", TilingLayout.HORIZONTAL),
            ("<Ctrl><Alt>v", TilingLayout.VERTICAL),
            ("<Ctrl><Alt>m", TilingLayout.MONOCLE),
            ("<Ctrl><Alt>t", TilingLayout.TALL),
            ("<Ctrl><Alt>w", TilingLayout.WIDE),
            ("<Ctrl><Alt>c", TilingLayout.CENTERED),
            ("<Ctrl><Alt>space", None),  # Cycle
        ]

        accel_group = Gtk.AccelGroup()

        for key, layout in shortcuts:
            keyval, mods = Gtk.accelerator_parse(key)
            if keyval:
                if layout is None:
                    accel_group.connect(
                        keyval, mods, Gtk.AccelFlags.VISIBLE,
                        lambda *_: self.cycle_tiling_layout()
                    )
                else:
                    accel_group.connect(
                        keyval, mods, Gtk.AccelFlags.VISIBLE,
                        lambda _, __, l=layout: self._tiling_manager.set_layout(l)
                    )

        self._accel_group = accel_group

    def cycle_tiling_layout(self):
        """Cycle through tiling layouts."""
        layouts = list(TilingLayout)
        current = self._tiling_manager.get_layout()
        idx = layouts.index(current)
        next_idx = (idx + 1) % len(layouts)
        self._tiling_manager.set_layout(layouts[next_idx])

    def toggle_tiling_panel(self):
        """Toggle visibility of the tiling panel."""
        if self._tiling_panel:
            visible = self._tiling_panel.get_visible()
            if visible:
                self._tiling_panel.hide()
            else:
                self._tiling_panel.show_all()

    def get_tiling_manager(self):
        """Get the tiling manager for this workspace."""
        return self._tiling_manager

    @property
    def accel_group(self):
        """Get the acceler group for this workspace."""
        return self._accel_group


# ============================================================
# Main window
# ============================================================

class LmuxWindow(Gtk.Window):
    """Main application window with sidebar and terminal area."""

    def __init__(self, client=None):
        super().__init__(title="lmux")
        self.set_default_size(1000, 700)
        self._client = client or DaemonClient()
        self._workspace_map = {}  # ws_id -> WorkspaceView
        self._desktop_notify = True  # enable desktop notifications
        self._event_stop = None
        self._reconnect_bar = None

        # CSS
        css = b"""
        .terminal-title { background: #2d2d2d; color: #ccc; font-size: 11px; }
        .workspace-header { background: #333; color: #ddd; font-size: 12px; }
        .unread-indicator { color: #ffa500; font-weight: bold; }
        .waiting-indicator { color: #ff4444; font-weight: bold; }
        .reconnect-bar { background: #800; color: #fff; font-weight: bold; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Top-level vertical box: menu | reconnect | paned
        self._outer_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        self.add(self._outer_vbox)

        # Menu bar
        self._setup_menu_bar()

        # Outer horizontal paned: (sidebar | terminal) | browser
        self._outer_hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._outer_vbox.pack_start(self._outer_hpaned, True, True, 0)

        # Inner paned: sidebar | terminal area
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._outer_hpaned.pack1(self._paned, True, False)

        # Sidebar
        self._sidebar = Sidebar(client, parent_window=self)
        self._sidebar.connect("workspace-selected", self._on_workspace_selected)
        self._sidebar.connect(
            "workspace-create-requested", self._on_workspace_create
        )
        self._paned.pack1(self._sidebar, False, False)

        # Workspace groups panel (below sidebar)
        self._workspace_groups_panel = WorkspaceGroupsPanel(client, parent_window=self)
        self._sidebar.pack_end(self._workspace_groups_panel, True, True, 0)

        # File explorer panel (below workspace groups)
        self._file_explorer = FileExplorer(parent_window=self)
        self._sidebar.pack_end(self._file_explorer, True, True, 0)

        # Terminal stack: one WorkspaceView per workspace
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._paned.pack2(self._stack, True, False)

        # Browser panel (hidden by default)
        self._browser = BrowserPanel(client=client, parent_window=self)
        self._browser_visible = False
        self._outer_hpaned.pack2(self._browser, False, False)
        self._browser.set_visible(False)

        # Browser scriptable API
        self._browser_api = setup_browser_api(self._browser)
        self._browser_api_bridge = setup_browser_api_bridge(self._browser_api)
        self._client._browser_api_bridge = self._browser_api_bridge

        # Canvas 2D layout (toggleable alternative to tree panes)
        self._canvas_container = CanvasContainer()
        self._canvas_visible = False
        self._canvas_container.set_visible(False)

        # Keyboard accelerators
        self._setup_accelerators()
        # Command palette
        self._palette = CommandPalette(parent=self, client=self._client)
        self._palette.connect("command-selected", self._on_command_selected)

        # Event stream for live updates
        self._start_events()

        # Periodic sidebar refresh (5s)
        GLib.timeout_add_seconds(5, self._tick_refresh)

        # Initial one-shot refresh
        GLib.idle_add(lambda: self._sidebar.refresh() or GLib.SOURCE_REMOVE)
        # Check for updates (background)
        # Check for updates (background)
        add_update_notification(self)
        # Load sidebar extensions
        from extensions import ExtensionLoader, BUILTIN_EXTENSIONS
        self._ext_loader = ExtensionLoader()
        for ext_cls in BUILTIN_EXTENSIONS:
            ext = ext_cls(sidebar=self._sidebar, client=self._client)
            self._sidebar.register_extension(ext)

        # ── Feature initialization ─────────────────────────
        # SSH client
        self._ssh_client = SSHClient() if SSHClient else None
        # Auto-namer
        self._auto_namer = AutoNamer() if AutoNamer else None
        # Register default hooks
        if self._client._hook_registry:
            self._register_default_hooks()

        # ── New feature managers for cmux parity ──────────
        # Hooks setup for 15+ agents
        self._hooks_setup = HooksSetup()
        # Notification system
        self._notification_manager = NotificationManager()
        self._notification_badge = NotificationBadge()
        # SSH advanced features
        self._ssh_session_manager = SSHSessionManager()
        self._ssh_browser_router = SSHBrowserRouter(self._ssh_session_manager)
        # Agent session manager
        self._agent_session_manager = AgentSessionManager()
        # Workspace colors
        self._workspace_colors = WorkspaceColorManager()
        # Session index
        self._session_index = SessionIndexManager()
        # Custom commands
        self._custom_commands = CustomCommandsManager()
        # Font size manager
        self._font_size_manager = FontSizeManager()
        # Session reopen
        self._session_reopen = SessionReopenManager()
        # Settings manager
        self._settings_manager = SettingsManager()
        # Window manager
        self._window_manager = WindowManager(self)
        # tmux compatibility
        self._tmux_compat = TmuxCompat()

        # Add notification badge to sidebar
        self._sidebar.pack_start(self._notification_badge, False, False, 0)

        # Set up font size shortcuts
        setup_font_shortcuts(self._accel_group, self._font_size_manager)

        # Set up session reopen shortcuts
        setup_session_reopen(self._accel_group, self._session_reopen, self)

        self.show_all()

    def _register_default_hooks(self):
        """Register default hook scripts for common events."""
        registry = self._client._hook_registry
        if not registry:
            return
        # Register a no-op default for each event so the registry is non-empty
        # Real hooks would be user-configured scripts
        for event in ("workspace_created", "workspace_closed", "pane_split",
                       "pane_focused", "surface_created", "surface_closed"):
            # Don't overwrite user-registered hooks
            hooks = registry.list_hooks()
            if event not in hooks or not hooks[event]:
                pass  # No default script; hooks are opt-in

    # ── menu bar ────────────────────────────────────────────

    def _setup_menu_bar(self):
        """Build the application menu bar."""
        menubar = Gtk.MenuBar()
        loc = get_localization()

        # File menu
        file_menu = Gtk.Menu()
        file_item = Gtk.MenuItem(label=f"_{loc.get('menu.file')}", use_underline=True)
        file_item.set_submenu(file_menu)

        item = Gtk.MenuItem(label=f"_{loc.get('menu.file.new_workspace')}", use_underline=True)
        item.connect("activate", lambda _: self._on_workspace_create(None))
        file_menu.append(item)

        item = Gtk.MenuItem(label=f"_{loc.get('menu.file.settings')}", use_underline=True)
        item.connect("activate", lambda _: self._show_settings())
        file_menu.append(item)

        file_menu.append(Gtk.SeparatorMenuItem())

        item = Gtk.MenuItem(label=f"_{loc.get('menu.file.quit')}", use_underline=True)
        item.connect("activate", lambda _: Gtk.main_quit())
        file_menu.append(item)
        menubar.append(file_item)

        # Terminal menu
        term_menu = Gtk.Menu()
        term_item = Gtk.MenuItem(label=f"_{loc.get('menu.terminal')}", use_underline=True)
        term_item.set_submenu(term_menu)

        item = Gtk.MenuItem(label=loc.get('menu.terminal.split_vertical'), use_underline=True)
        item.connect("activate", lambda _: self._action_split_v())
        term_menu.append(item)

        item = Gtk.MenuItem(label=loc.get('menu.terminal.split_horizontal'), use_underline=True)
        item.connect("activate", lambda _: self._action_split_h())
        term_menu.append(item)

        item = Gtk.MenuItem(label=loc.get('menu.terminal.close_pane'), use_underline=True)
        item.connect("activate", lambda _: self._action_close_pane())
        term_menu.append(item)

        term_menu.append(Gtk.SeparatorMenuItem())

        item = Gtk.MenuItem(label=loc.get('menu.terminal.new_tab'), use_underline=True)
        item.connect("activate", lambda _: self._action_new_tab())
        term_menu.append(item)

        item = Gtk.MenuItem(label=loc.get('menu.terminal.close_tab'), use_underline=True)
        item.connect("activate", lambda _: self._action_close_tab())
        term_menu.append(item)
        menubar.append(term_item)

        # View menu
        view_menu = Gtk.Menu()
        view_item = Gtk.MenuItem(label=f"_{loc.get('menu.view')}", use_underline=True)
        view_item.set_submenu(view_menu)

        self._toggle_sidebar_item = Gtk.CheckMenuItem(
            label=f"_{loc.get('menu.view.sidebar')}", use_underline=True, active=True
        )
        self._toggle_sidebar_item.connect(
            "toggled", self._on_toggle_sidebar
        )
        view_menu.append(self._toggle_sidebar_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        item = Gtk.MenuItem(label=loc.get('menu.view.command_palette'), use_underline=True)
        item.connect("activate", lambda _: self._palette.show_palette())
        view_menu.append(item)
        item = Gtk.MenuItem(label=loc.get('menu.view.browser_panel'), use_underline=True)
        item.connect("activate", lambda _: self._toggle_browser())
        view_menu.append(item)
        item = Gtk.MenuItem(label=loc.get('menu.view.canvas_layout'), use_underline=True)
        item.connect("activate", lambda _: self._toggle_canvas())
        view_menu.append(item)

        menubar.append(view_item)

        # Tools menu
        tools_menu = Gtk.Menu()
        tools_item = Gtk.MenuItem(label=f"_{loc.get('menu.tools')}", use_underline=True)
        tools_item.set_submenu(tools_menu)

        item = Gtk.MenuItem(label=loc.get('menu.tools.import_browser'), use_underline=True)
        item.connect("activate", lambda _: self._import_from_browser())
        tools_menu.append(item)

        menubar.append(tools_item)

        self._outer_vbox.pack_start(menubar, False, False, 0)

    # ── menu actions ────────────────────────────────────────

    def _action_split_v(self):
        view = self._active_view()
        if view:
            view.split_pane("v")

    def _action_split_h(self):
        view = self._active_view()
        if view:
            view.split_pane("h")

    def _action_close_pane(self):
        view = self._active_view()
        if view:
            view.close_current_pane()

    def _action_new_tab(self):
        view = self._active_view()
        if view:
            view.create_surface()

    def _action_close_tab(self):
        view = self._active_view()
        if view:
            view.close_current_surface()

    def _on_toggle_sidebar(self, check_item):
        visible = check_item.get_active()
        self._sidebar.set_visible(visible)

    def _toggle_browser(self):
        """Toggle the browser panel visibility."""
        self._browser_visible = not self._browser_visible
        self._browser.set_visible(self._browser_visible)
        if self._browser_visible:
            # Set initial position for the hpaned divider
            alloc = self._outer_hpaned.get_allocation()
            if alloc.width > 0:
                self._outer_hpaned.set_position(int(alloc.width * 0.65))
        self._outer_hpaned.show_all()

    def _toggle_canvas(self):
        """Toggle canvas 2D layout mode."""
        self._canvas_visible = not self._canvas_visible
        if self._canvas_visible:
            # Switch stack to canvas
            self._stack.add_titled(self._canvas_container, "canvas", "Canvas")
            self._stack.set_visible_child(self._canvas_container)
        else:
            # Remove canvas from stack, show workspace views
            self._stack.remove(self._canvas_container)
            # Show first workspace if available
            for child in self._stack.get_children():
                if isinstance(child, WorkspaceView):
                    self._stack.set_visible_child(child)
                    break

    def _import_from_browser(self):
        """Import bookmarks and history from another browser."""
        import_options = create_import_dialog(self, self._browser)
        if import_options:
            result = perform_import(self._browser, import_options)
            if result:
                # Show success message
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text="Import Complete",
                )
                dialog.format_secondary_text(
                    f"Imported {len(result['bookmarks'])} bookmarks and "
                    f"{len(result['history'])} history entries."
                )
                dialog.run()
                dialog.destroy()

    def _show_settings(self):
        """Open the settings dialog."""
        dialog = SettingsDialog(self, self._client)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            settings = dialog.get_settings()
            # Apply to config via daemon
            self._apply_settings(settings)
        dialog.destroy()

    def _apply_settings(self, settings):
        """Push settings to daemon config (async)."""
        def _push():
            try:
                self._client.send("config.set", settings)
                # Also try to set them individually for immediate effect
                for key, value in settings.items():
                    if isinstance(value, bool):
                        value = "true" if value else "false"
                    self._client.send("config.set", {"key": key, "value": str(value)})
            except Exception:
                pass
        threading.Thread(target=_push, daemon=True).start()

    # ── accelerators ────────────────────────────────────────

    def _setup_accelerators(self):
        """Register keyboard shortcuts using customizable shortcuts manager."""
        accel = Gtk.AccelGroup()
        self.add_accel_group(accel)
        self._accel = accel

        shortcuts_mgr = get_shortcuts_manager()

        # Register action callbacks
        shortcuts_mgr.register_action("workspace.new", lambda: self._on_workspace_create(None))
        shortcuts_mgr.register_action("surface.new", lambda: self._active_view() and self._active_view().create_surface())
        shortcuts_mgr.register_action("surface.close", lambda: self._active_view() and self._active_view().close_current_surface())
        shortcuts_mgr.register_action("pane.split_v", self._action_split_v)
        shortcuts_mgr.register_action("pane.split_h", self._action_split_h)
        shortcuts_mgr.register_action("pane.close", lambda: self._active_view() and self._active_view().close_current_pane())
        shortcuts_mgr.register_action("pane.focus_up", lambda: self._active_view() and self._active_view().focus_prev_pane())
        shortcuts_mgr.register_action("pane.focus_down", lambda: self._active_view() and self._active_view().focus_next_pane())
        shortcuts_mgr.register_action("pane.focus_left", lambda: self._active_view() and self._active_view().focus_prev_pane())
        shortcuts_mgr.register_action("pane.focus_right", lambda: self._active_view() and self._active_view().focus_next_pane())
        shortcuts_mgr.register_action("view.sidebar.toggle", lambda: self._toggle_sidebar_item.set_active(not self._toggle_sidebar_item.get_active()))
        shortcuts_mgr.register_action("view.command_palette", lambda: self._palette.show_palette())
        shortcuts_mgr.register_action("view.browser.toggle", self._toggle_browser)
        shortcuts_mgr.register_action("app.quit", lambda: Gtk.main_quit())

        # Connect accelerators from shortcuts manager
        for action, shortcut in shortcuts_mgr.get_all_shortcuts().items():
            for keyval, modifiers in shortcut.keys:
                accel.connect(keyval, modifiers, Gtk.AccelFlags.VISIBLE, 
                            lambda a, k, m, act=action: self._accel_handler(act))

    def _accel_handler(self, action: str):
        """Handle accelerator action."""
        shortcuts_mgr = get_shortcuts_manager()
        shortcuts_mgr.execute_action(action)
        return True

    # ── accelerator callbacks ───────────────────────────────

    def _active_view(self):
        """Get the currently visible WorkspaceView."""
        page = self._stack.get_visible_child()
        if isinstance(page, WorkspaceView):
            return page
        return None

    def _accel_handler(self, action: str):
        """Handle accelerator action."""
        shortcuts_mgr = get_shortcuts_manager()
        shortcuts_mgr.execute_action(action)
        return True

    def _on_command_selected(self, palette, command):
        """Handle command palette selection — execute via daemon."""
        def _exec():
            try:
                # Commands that need args based on current context
                args = {}
                if command in ("workspace.close", "workspace.rename", "workspace.env"):
                    view = self._active_view()
                    if view:
                        args["id"] = view._ws_id
                elif command in ("surface.close", "surface.focus", "surface.rename"):
                    view = self._active_view()
                    if view:
                        nb = view._notebook
                        if nb:
                            page_num = nb.get_current_page()
                            if page_num >= 0:
                                args["surface_id"] = str(page_num)
                elif command in ("pane.focus", "pane.resize"):
                    view = self._active_view()
                    if view:
                        args["pane_id"] = "0"  # default to first pane
                elif command == "workspace.create":
                    pass  # no args needed
                elif command == "snapshot.save":
                    pass  # no args needed
                elif command == "snapshot.load":
                    pass  # no args needed
                elif command == "ping":
                    pass  # no args needed
                elif command == "tree":
                    pass  # no args needed
                elif command == "help":
                    pass  # no args needed
                elif command == "capabilities":
                    pass  # no args needed
                elif command == "reload-config":
                    pass  # no args needed

                resp = self._client.send(command, args)
                if resp.get("ok"):
                    result = resp.get("result", {})
                    # Show result in a popup for read-only commands
                    if command in ("ping", "tree", "capabilities", "help",
                                   "workspace.list", "surface.list", "pane.list",
                                   "agent.list", "notification.list", "config.get"):
                        GLib.idle_add(lambda: self._show_command_result(command, result))
                    # Refresh sidebar for mutations
                    elif any(k in command for k in ("workspace", "surface", "pane", "snapshot", "session")):
                        GLib.idle_add(lambda: self._sidebar.refresh())
                else:
                    err = resp.get("error", "Unknown error")
                    GLib.idle_add(lambda: self._show_command_error(command, err))
            except Exception as e:
                GLib.idle_add(lambda: self._show_command_error(command, str(e)))

        threading.Thread(target=_exec, daemon=True).start()

    def _show_command_result(self, command, result):
        """Show command result in a small dialog."""
        import json
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f"{command}",
        )
        dialog.format_secondary_text(json.dumps(result, indent=2)[:2000])
        dialog.run()
        dialog.destroy()

    def _show_command_error(self, command, error):
        """Show command error in a dialog."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=f"Command failed: {command}",
        )
        dialog.format_secondary_text(str(error))
        dialog.run()
        dialog.destroy()

    # ── events ──────────────────────────────────────────────

    def _start_events(self):
        """Subscribe to daemon event stream for live sidebar updates."""
        notify_enabled = self._desktop_notify

        def on_event(evt):
            evt_type = evt.get("type")
            if evt_type == "event":
                name = evt.get("name", "")
                data = evt.get("data") or {}
                # Desktop notification on notification.created
                if notify_enabled and name == "notification.created":
                    text = data.get("text", "") or data.get(
                        "message", "Notification received"
                    )
                    ws_title = data.get("workspace_id", "")
                    GLib.idle_add(
                        lambda: (
                            _notify_desktop(f"lmux: WS-{ws_title}", text),
                            GLib.SOURCE_REMOVE,
                        )[1]
                    )
                # Refresh sidebar on workspace/surface/pane/notification changes
                if "workspace" in name or "surface" in name or "pane" in name or "notification" in name:
                    GLib.idle_add(
                        lambda: (self._sidebar.refresh(), GLib.SOURCE_REMOVE)[1]
                    )

        try:
            self._event_stop = self._client.send_events(callback=on_event)
        except Exception:
            pass  # daemon not yet available — reconnect will retry

    def _tick_refresh(self):
        """Periodic refresh of sidebar and workspace state. Also health-checks the daemon."""
        self._sidebar.refresh()
        # Health check: ping daemon once every 5 ticks (25s)
        if not hasattr(self, "_health_tick"):
            self._health_tick = 0
        self._health_tick += 1
        if self._health_tick % 5 == 0:
            self._check_daemon_health()
        return True  # keep GLib timeout active

    # ── daemon health / reconnect ───────────────────────────

    def _check_daemon_health(self):
        """Ping the daemon; show reconnect bar if unreachable."""
        def _ping():
            try:
                resp = self._client.send("ping")
                if resp.get("ok"):
                    GLib.idle_add(self._on_daemon_connected)
                else:
                    GLib.idle_add(self._on_daemon_disconnected)
            except Exception:
                GLib.idle_add(self._on_daemon_disconnected)
        threading.Thread(target=_ping, daemon=True).start()

    def _on_daemon_connected(self):
        """Hide reconnect bar and restart event stream if needed."""
        if self._reconnect_bar is not None:
            self._reconnect_bar.destroy()
            self._reconnect_bar = None
        if self._event_stop is None:
            self._start_events()

    def _on_daemon_disconnected(self):
        """Show or update the reconnect bar."""
        if self._reconnect_bar is not None:
            return  # already showing
        self._reconnect_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self._reconnect_bar.set_margin_start(8)
        self._reconnect_bar.set_margin_end(8)
        self._reconnect_bar.set_margin_top(4)
        self._reconnect_bar.set_margin_bottom(4)

        ctx = self._reconnect_bar.get_style_context()
        ctx.add_class("reconnect-bar")

        lbl = Gtk.Label(label="Daemon disconnected")
        lbl.set_xalign(0)
        self._reconnect_bar.pack_start(lbl, True, True, 0)

        btn = Gtk.Button(label="Reconnect")
        btn.connect("clicked", lambda _: self._reconnect_daemon())
        self._reconnect_bar.pack_end(btn, False, False, 0)

        # Insert reconnect bar before the paned
        self._outer_vbox.pack_start(self._reconnect_bar, False, False, 0)
        self._outer_vbox.reorder_child(self._reconnect_bar, 1)  # after menu bar
        self.show_all()

    def _reconnect_daemon(self):
        """Force reconnection to the daemon."""
        if self._event_stop is not None:
            self._event_stop.set()
            self._event_stop = None
        self._on_daemon_connected()

    # ── workspace selection ─────────────────────────────────

    def _on_workspace_selected(self, sidebar, ws_id):
        """Switch to a workspace, creating its terminal view if needed."""
        if ws_id not in self._workspace_map:
            client = self._client
            view = WorkspaceView(ws_id, f"Workspace {ws_id}", client)
            self._workspace_map[ws_id] = view
            self._stack.add_titled(view, str(ws_id), f"WS-{ws_id}")
        self._stack.set_visible_child(self._workspace_map[ws_id])

    def _on_workspace_create(self, sidebar):
        """Create a new workspace via daemon and switch to it."""
        # Require auth for workspace creation
        if not self._sidebar.require_auth("create workspace"):
            return
        def _create():
            try:
                resp = self._client.send("workspace.create", {})
                ws_id = resp.get("result", {}).get("id")
                if ws_id:
                    GLib.idle_add(
                        lambda: (
                            self._on_workspace_selected(None, ws_id),
                            GLib.SOURCE_REMOVE,
                        )[1]
                    )
            except Exception as e:
                GLib.idle_add(
                    lambda: (
                        self._sidebar._show_error(str(e)),
                        GLib.SOURCE_REMOVE,
                    )[1]
                )

        threading.Thread(target=_create, daemon=True).start()


# ============================================================
# Entry point
# ============================================================

def main():
    """Entry point: create and show the main window, run GTK main loop."""
    win = LmuxWindow()

    def _on_destroy(w):
        # Cleanup SSH sessions on quit
        if hasattr(win, '_ssh_client') and win._ssh_client:
            win._ssh_client.cleanup_all()
        Gtk.main_quit()

    win.connect("destroy", _on_destroy)
    Gtk.main()


if __name__ == "__main__":
    main()
