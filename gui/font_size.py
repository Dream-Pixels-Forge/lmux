"""Font size adjust shortcuts for lmux."""
import json
import logging
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger("lmux.font_size")


@dataclass
class FontConfig:
    """Font configuration."""
    family: str = "Monospace"
    size: int = 12
    min_size: int = 6
    max_size: int = 72


class FontSizeManager:
    """Manages font size across the application."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._config_file = self._config_dir / "config.json"
        self._font = FontConfig()
        self._listeners: list = []
        self._load_config()

    def _load_config(self):
        """Load font config from file."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
                    font_data = config.get("font", {})
                    self._font.family = font_data.get("family", "Monospace")
                    self._font.size = font_data.get("size", 12)
                    self._font.min_size = font_data.get("min_size", 6)
                    self._font.max_size = font_data.get("max_size", 72)
            except Exception as e:
                logger.error(f"Error loading font config: {e}")

    def _save_config(self):
        """Save font config to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)

        config = {}
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    config = json.load(f)
            except Exception:
                pass

        config["font"] = {
            "family": self._font.family,
            "size": self._font.size,
            "min_size": self._font.min_size,
            "max_size": self._font.max_size
        }

        with open(self._config_file, "w") as f:
            json.dump(config, f, indent=2)

    def get_font(self) -> FontConfig:
        """Get current font configuration."""
        return self._font

    def set_font_size(self, size: int):
        """Set font size."""
        size = max(self._font.min_size, min(self._font.max_size, size))
        if self._font.size != size:
            self._font.size = size
            self._save_config()
            self._notify_listeners()

    def increase_font_size(self, step: int = 1):
        """Increase font size."""
        self.set_font_size(self._font.size + step)

    def decrease_font_size(self, step: int = 1):
        """Decrease font size."""
        self.set_font_size(self._font.size - step)

    def reset_font_size(self):
        """Reset font size to default."""
        self.set_font_size(12)

    def set_font_family(self, family: str):
        """Set font family."""
        if self._font.family != family:
            self._font.family = family
            self._save_config()
            self._notify_listeners()

    def add_listener(self, callback: Callable):
        """Add a listener for font changes."""
        self._listeners.append(callback)

    def _notify_listeners(self):
        """Notify listeners of font changes."""
        for listener in self._listeners:
            try:
                listener(self._font)
            except Exception as e:
                logger.error(f"Error notifying font listener: {e}")

    def get_css(self) -> str:
        """Get CSS for current font settings."""
        return f"""
        terminal, .vte-terminal {{
            font-family: {self._font.family};
            font-size: {self._font.size}pt;
        }}
        """


def setup_font_shortcuts(accel_group, font_manager: FontSizeManager):
    """Set up keyboard shortcuts for font size adjustment."""
    # Ctrl++: Increase font size
    keyval, mods = Gtk.accelerator_parse("<Ctrl>plus")
    if keyval:
        accel_group.connect(
            keyval, mods, Gtk.AccelFlags.VISIBLE,
            lambda *_: font_manager.increase_font_size()
        )

    # Ctrl+-: Decrease font size
    keyval, mods = Gtk.accelerator_parse("<Ctrl>minus")
    if keyval:
        accel_group.connect(
            keyval, mods, Gtk.AccelFlags.VISIBLE,
            lambda *_: font_manager.decrease_font_size()
        )

    # Ctrl+0: Reset font size
    keyval, mods = Gtk.accelerator_parse("<Ctrl>0")
    if keyval:
        accel_group.connect(
            keyval, mods, Gtk.AccelFlags.VISIBLE,
            lambda *_: font_manager.reset_font_size()
        )


# Import Gtk for accelerator parsing
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
