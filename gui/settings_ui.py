"""Settings UI for lmux — native Settings window."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger("lmux.settings")


@dataclass
class SettingsData:
    """Application settings."""
    # General
    theme: str = "dark"
    language: str = "en"
    
    # Terminal
    font_family: str = "Monospace"
    font_size: int = 12
    cursor_shape: str = "block"
    cursor_blink: bool = True
    scrollback_lines: int = 10000
    
    # Window
    width: int = 1200
    height: int = 800
    maximized: bool = False
    
    # SSH
    control_persist: int = 600
    strict_host_key: bool = False
    
    # Notifications
    notifications_enabled: bool = True
    notification_sounds: bool = True
    
    # Browser
    browser_enabled: bool = True
    browser_zoom: float = 1.0


class SettingsManager:
    """Manages application settings."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._config_file = self._config_dir / "config.json"
        self._settings = SettingsData()
        self._listeners: list = []
        self._load_settings()

    def _load_settings(self):
        """Load settings from file."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
                    settings_data = config.get("settings", {})
                    for key, value in settings_data.items():
                        if hasattr(self._settings, key):
                            setattr(self._settings, key, value)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")

    def _save_settings(self):
        """Save settings to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        config = {}
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
            except Exception:
                pass

        config["settings"] = {
            "theme": self._settings.theme,
            "language": self._settings.language,
            "font_family": self._settings.font_family,
            "font_size": self._settings.font_size,
            "cursor_shape": self._settings.cursor_shape,
            "cursor_blink": self._settings.cursor_blink,
            "scrollback_lines": self._settings.scrollback_lines,
            "width": self._settings.width,
            "height": self._settings.height,
            "maximized": self._settings.maximized,
            "control_persist": self._settings.control_persist,
            "strict_host_key": self._settings.strict_host_key,
            "notifications_enabled": self._settings.notifications_enabled,
            "notification_sounds": self._settings.notification_sounds,
            "browser_enabled": self._settings.browser_enabled,
            "browser_zoom": self._settings.browser_zoom
        }

        with open(self._config_file, "w") as f:
            json.dump(config, f, indent=2)

    def get_settings(self) -> SettingsData:
        """Get current settings."""
        return self._settings

    def update_setting(self, key: str, value: Any):
        """Update a single setting."""
        if hasattr(self._settings, key):
            setattr(self._settings, key, value)
            self._save_settings()
            self._notify_listeners(key, value)

    def reset_defaults(self):
        """Reset all settings to defaults."""
        self._settings = SettingsData()
        self._save_settings()
        self._notify_listeners("all", None)

    def add_listener(self, callback):
        """Add a listener for settings changes."""
        self._listeners.append(callback)

    def _notify_listeners(self, key: str, value: Any):
        """Notify listeners of settings changes."""
        for listener in self._listeners:
            try:
                listener(key, value)
            except Exception as e:
                logger.error(f"Error notifying settings listener: {e}")


class SettingsWindow(Gtk.Dialog):
    """Settings window with tabs."""

    def __init__(self, parent, settings_manager: SettingsManager):
        super().__init__(
            title="lmux Settings",
            transient_for=parent,
            modal=True
        )
        self._manager = settings_manager
        self._settings = settings_manager.get_settings()

        self.set_default_size(600, 500)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_APPLY, Gtk.ResponseType.APPLY,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )

        self._setup_ui()

    def _setup_ui(self):
        """Build the settings UI."""
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(16)
        box.set_margin_bottom(16)

        # Notebook for tabs
        self._notebook = Gtk.Notebook()
        box.pack_start(self._notebook, True, True, 0)

        # General tab
        general_page = self._create_general_page()
        self._notebook.append_page(general_page, Gtk.Label(label="General"))

        # Terminal tab
        terminal_page = self._create_terminal_page()
        self._notebook.append_page(terminal_page, Gtk.Label(label="Terminal"))

        # Window tab
        window_page = self._create_window_page()
        self._notebook.append_page(window_page, Gtk.Label(label="Window"))

        # SSH tab
        ssh_page = self._create_ssh_page()
        self._notebook.append_page(ssh_page, Gtk.Label(label="SSH"))

        # Notifications tab
        notifications_page = self._create_notifications_page()
        self._notebook.append_page(notifications_page, Gtk.Label(label="Notifications"))

        self.show_all()

    def _create_general_page(self) -> Gtk.Box:
        """Create the general settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Theme
        theme_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        theme_label = Gtk.Label(label="Theme:", xalign=0)
        theme_label.set_width_chars(12)
        theme_box.pack_start(theme_label, False, False, 0)

        self._theme_combo = Gtk.ComboBoxText()
        self._theme_combo.append_text("dark")
        self._theme_combo.append_text("light")
        self._theme_combo.set_active_id(self._settings.theme)
        theme_box.pack_start(self._theme_combo, True, True, 0)
        box.pack_start(theme_box, False, False, 0)

        # Language
        lang_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lang_label = Gtk.Label(label="Language:", xalign=0)
        lang_label.set_width_chars(12)
        lang_box.pack_start(lang_label, False, False, 0)

        self._lang_combo = Gtk.ComboBoxText()
        self._lang_combo.append_text("en")
        self._lang_combo.append_text("fr")
        self._lang_combo.append_text("de")
        self._lang_combo.set_active_id(self._settings.language)
        lang_box.pack_start(self._lang_combo, True, True, 0)
        box.pack_start(lang_box, False, False, 0)

        return box

    def _create_terminal_page(self) -> Gtk.Box:
        """Create the terminal settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Font family
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        font_label = Gtk.Label(label="Font Family:", xalign=0)
        font_label.set_width_chars(12)
        font_box.pack_start(font_label, False, False, 0)

        self._font_entry = Gtk.Entry()
        self._font_entry.set_text(self._settings.font_family)
        font_box.pack_start(self._font_entry, True, True, 0)
        box.pack_start(font_box, False, False, 0)

        # Font size
        size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        size_label = Gtk.Label(label="Font Size:", xalign=0)
        size_label.set_width_chars(12)
        size_box.pack_start(size_label, False, False, 0)

        self._size_spin = Gtk.SpinButton.new_with_range(6, 72, 1)
        self._size_spin.set_value(self._settings.font_size)
        size_box.pack_start(self._size_spin, True, True, 0)
        box.pack_start(size_box, False, False, 0)

        # Cursor shape
        cursor_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cursor_label = Gtk.Label(label="Cursor Shape:", xalign=0)
        cursor_label.set_width_chars(12)
        cursor_box.pack_start(cursor_label, False, False, 0)

        self._cursor_combo = Gtk.ComboBoxText()
        self._cursor_combo.append_text("block")
        self._cursor_combo.append_text("underline")
        self._cursor_combo.append_text("bar")
        self._cursor_combo.set_active_id(self._settings.cursor_shape)
        cursor_box.pack_start(self._cursor_combo, True, True, 0)
        box.pack_start(cursor_box, False, False, 0)

        # Cursor blink
        self._blink_check = Gtk.CheckButton(label="Cursor Blink")
        self._blink_check.set_active(self._settings.cursor_blink)
        box.pack_start(self._blink_check, False, False, 0)

        # Scrollback lines
        scroll_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scroll_label = Gtk.Label(label="Scrollback Lines:", xalign=0)
        scroll_label.set_width_chars(12)
        scroll_box.pack_start(scroll_label, False, False, 0)

        self._scroll_spin = Gtk.SpinButton.new_with_range(100, 1000000, 100)
        self._scroll_spin.set_value(self._settings.scrollback_lines)
        scroll_box.pack_start(self._scroll_spin, True, True, 0)
        box.pack_start(scroll_box, False, False, 0)

        return box

    def _create_window_page(self) -> Gtk.Box:
        """Create the window settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Window size
        size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        size_label = Gtk.Label(label="Default Size:", xalign=0)
        size_label.set_width_chars(12)
        size_box.pack_start(size_label, False, False, 0)

        self._width_spin = Gtk.SpinButton.new_with_range(800, 3840, 10)
        self._width_spin.set_value(self._settings.width)
        size_box.pack_start(self._width_spin, True, True, 0)

        size_box.pack_start(Gtk.Label(label="×"), False, False, 0)

        self._height_spin = Gtk.SpinButton.new_with_range(600, 2160, 10)
        self._height_spin.set_value(self._settings.height)
        size_box.pack_start(self._height_spin, True, True, 0)
        box.pack_start(size_box, False, False, 0)

        # Maximized
        self._maximized_check = Gtk.CheckButton(label="Start Maximized")
        self._maximized_check.set_active(self._settings.maximized)
        box.pack_start(self._maximized_check, False, False, 0)

        return box

    def _create_ssh_page(self) -> Gtk.Box:
        """Create the SSH settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Control persist
        persist_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        persist_label = Gtk.Label(label="Control Persist:", xalign=0)
        persist_label.set_width_chars(12)
        persist_box.pack_start(persist_label, False, False, 0)

        self._persist_spin = Gtk.SpinButton.new_with_range(60, 86400, 60)
        self._persist_spin.set_value(self._settings.control_persist)
        persist_box.pack_start(self._persist_spin, True, True, 0)
        box.pack_start(persist_box, False, False, 0)

        # Strict host key checking
        self._strict_check = Gtk.CheckButton(label="Strict Host Key Checking")
        self._strict_check.set_active(self._settings.strict_host_key)
        box.pack_start(self._strict_check, False, False, 0)

        return box

    def _create_notifications_page(self) -> Gtk.Box:
        """Create the notifications settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Enable notifications
        self._notifications_check = Gtk.CheckButton(label="Enable Notifications")
        self._notifications_check.set_active(self._settings.notifications_enabled)
        box.pack_start(self._notifications_check, False, False, 0)

        # Notification sounds
        self._sounds_check = Gtk.CheckButton(label="Notification Sounds")
        self._sounds_check.set_active(self._settings.notification_sounds)
        box.pack_start(self._sounds_check, False, False, 0)

        return box

    def do_response(self, response_id):
        """Handle dialog response."""
        if response_id == Gtk.ResponseType.APPLY or response_id == Gtk.ResponseType.OK:
            self._apply_settings()
        self.destroy()

    def _apply_settings(self):
        """Apply settings from UI to settings manager."""
        # General
        self._manager.update_setting("theme", self._theme_combo.get_active_id())
        self._manager.update_setting("language", self._lang_combo.get_active_id())

        # Terminal
        self._manager.update_setting("font_family", self._font_entry.get_text())
        self._manager.update_setting("font_size", int(self._size_spin.get_value()))
        self._manager.update_setting("cursor_shape", self._cursor_combo.get_active_id())
        self._manager.update_setting("cursor_blink", self._blink_check.get_active())
        self._manager.update_setting("scrollback_lines", int(self._scroll_spin.get_value()))

        # Window
        self._manager.update_setting("width", int(self._width_spin.get_value()))
        self._manager.update_setting("height", int(self._height_spin.get_value()))
        self._manager.update_setting("maximized", self._maximized_check.get_active())

        # SSH
        self._manager.update_setting("control_persist", int(self._persist_spin.get_value()))
        self._manager.update_setting("strict_host_key", self._strict_check.get_active())

        # Notifications
        self._manager.update_setting("notifications_enabled", self._notifications_check.get_active())
        self._manager.update_setting("notification_sounds", self._sounds_check.get_active())
