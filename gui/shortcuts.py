"""Keyboard shortcuts manager for lmux GUI — customizable keybindings."""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
import logging

logger = logging.getLogger("lmux.shortcuts")


@dataclass
class Shortcut:
    """Represents a keyboard shortcut."""
    action: str
    keys: List[Tuple[int, int]]  # List of (keyval, modifiers) tuples
    description: str = ""
    category: str = "general"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "keys": [{"keyval": k, "modifiers": m} for k, m in self.keys],
            "description": self.description,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Shortcut":
        keys = [(k["keyval"], k["modifiers"]) for k in d.get("keys", [])]
        return cls(
            action=d["action"],
            keys=keys,
            description=d.get("description", ""),
            category=d.get("category", "general"),
        )


# Default shortcuts for lmux
DEFAULT_SHORTCUTS: List[Shortcut] = [
    # Workspace actions
    Shortcut("workspace.new", [(ord("t"), 0x1 | 0x2)], "New workspace", "workspace"),
    Shortcut("workspace.close", [(ord("w"), 0x1 | 0x2)], "Close workspace", "workspace"),
    # Tab/Surface actions
    Shortcut("surface.new", [(ord("t"), 0x1 | 0x2)], "New tab", "surface"),
    Shortcut("surface.close", [(ord("w"), 0x1 | 0x2)], "Close tab", "surface"),
    # Pane actions
    Shortcut("pane.split_v", [(ord("-"), 0x1 | 0x2)], "Split vertical", "pane"),
    Shortcut("pane.split_h", [(ord("="), 0x1 | 0x2)], "Split horizontal", "pane"),
    Shortcut("pane.close", [(ord("x"), 0x1 | 0x2)], "Close pane", "pane"),
    # Navigation
    Shortcut("pane.focus_up", [(0xff52, 0x1 | 0x2 | 0x4)], "Focus pane up", "navigation"),  # Up arrow
    Shortcut("pane.focus_down", [(0xff54, 0x1 | 0x2 | 0x4)], "Focus pane down", "navigation"),  # Down arrow
    Shortcut("pane.focus_left", [(0xff51, 0x1 | 0x2)], "Focus pane left", "navigation"),  # Left arrow
    Shortcut("pane.focus_right", [(0xff53, 0x1 | 0x2)], "Focus pane right", "navigation"),  # Right arrow
    # View actions
    Shortcut("view.sidebar.toggle", [(ord("s"), 0x1 | 0x2)], "Toggle sidebar", "view"),
    Shortcut("view.command_palette", [(ord("p"), 0x1 | 0x2)], "Command palette", "view"),
    Shortcut("view.browser.toggle", [(ord("b"), 0x1 | 0x2)], "Toggle browser panel", "view"),
    # Application
    Shortcut("app.quit", [(ord("q"), 0x1 | 0x2)], "Quit lmux", "application"),
]


class ShortcutsManager:
    """Manages keyboard shortcuts with persistence and customization."""

    CONFIG_DIR = Path.home() / ".config" / "lmux"
    SHORTCUTS_FILE = CONFIG_DIR / "shortcuts.json"

    def __init__(self):
        self._shortcuts: Dict[str, Shortcut] = {}
        self._actions: Dict[str, Callable] = {}
        self._load()

    def _load(self):
        """Load shortcuts from config or use defaults."""
        # Start with defaults
        for shortcut in DEFAULT_SHORTCUTS:
            self._shortcuts[shortcut.action] = shortcut

        # Override with user config if exists
        if self.SHORTCUTS_FILE.exists():
            try:
                with open(self.SHORTCUTS_FILE, "r") as f:
                    data = json.load(f)
                for action, shortcut_data in data.get("shortcuts", {}).items():
                    self._shortcuts[action] = Shortcut.from_dict(shortcut_data)
                logger.info(f"Loaded shortcuts from {self.SHORTCUTS_FILE}")
            except Exception as e:
                logger.warning(f"Failed to load shortcuts: {e}")

    def save(self):
        """Save shortcuts to config file."""
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "shortcuts": {action: s.to_dict() for action, s in self._shortcuts.items()}
        }
        try:
            with open(self.SHORTCUTS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved shortcuts to {self.SHORTCUTS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save shortcuts: {e}")

    def get_shortcut(self, action: str) -> Optional[Shortcut]:
        """Get shortcut for an action."""
        return self._shortcuts.get(action)

    def set_shortcut(self, action: str, keys: List[Tuple[int, int]], description: str = "", category: str = "general"):
        """Set shortcut for an action."""
        self._shortcuts[action] = Shortcut(action, keys, description, category)

    def get_all_shortcuts(self) -> Dict[str, Shortcut]:
        """Get all shortcuts."""
        return self._shortcuts.copy()

    def get_shortcuts_by_category(self) -> Dict[str, List[Shortcut]]:
        """Get shortcuts grouped by category."""
        categories: Dict[str, List[Shortcut]] = {}
        for shortcut in self._shortcuts.values():
            if shortcut.category not in categories:
                categories[shortcut.category] = []
            categories[shortcut.category].append(shortcut)
        return categories

    def register_action(self, action: str, callback: Callable):
        """Register a callback for an action."""
        self._actions[action] = callback

    def execute_action(self, action: str):
        """Execute an action callback."""
        if action in self._actions:
            self._actions[action]()
        else:
            logger.warning(f"No callback registered for action: {action}")

    def keyval_to_string(self, keyval: int, modifiers: int) -> str:
        """Convert keyval and modifiers to a human-readable string."""
        parts = []
        if modifiers & 0x1:  # Ctrl
            parts.append("Ctrl")
        if modifiers & 0x2:  # Shift
            parts.append("Shift")
        if modifiers & 0x4:  # Alt
            parts.append("Alt")
        if modifiers & 0x8:  # Super
            parts.append("Super")

        # Convert keyval to string
        key_name = self._keyval_name(keyval)
        parts.append(key_name)

        return "+".join(parts)

    def _keyval_name(self, keyval: int) -> str:
        """Get human-readable name for a keyval."""
        # Common key names
        key_names = {
            0xff52: "Up",
            0xff54: "Down",
            0xff51: "Left",
            0xff53: "Right",
            0xff08: "Backspace",
            0xff0d: "Enter",
            0xff1b: "Escape",
            0xff09: "Tab",
            0xffe1: "Shift_L",
            0xffe2: "Shift_R",
            0xffe3: "Control_L",
            0xffe4: "Control_R",
            0xffe9: "Alt_L",
            0xffea: "Alt_R",
        }
        if keyval in key_names:
            return key_names[keyval]
        if 32 <= keyval < 127:
            return chr(keyval).upper()
        return f"Key({keyval})"

    def string_to_keyval(self, s: str) -> Optional[Tuple[int, int]]:
        """Convert a string like 'Ctrl+Shift+T' to (keyval, modifiers)."""
        parts = s.split("+")
        modifiers = 0
        key_name = parts[-1].strip()

        for part in parts[:-1]:
            part = part.strip().lower()
            if part == "ctrl" or part == "control":
                modifiers |= 0x1
            elif part == "shift":
                modifiers |= 0x2
            elif part == "alt" or part == "mod1":
                modifiers |= 0x4
            elif part == "super" or part == "mod4":
                modifiers |= 0x8

        # Convert key name to keyval
        keyval = self._name_to_keyval(key_name)
        if keyval is None:
            return None

        return (keyval, modifiers)

    def _name_to_keyval(self, name: str) -> Optional[int]:
        """Convert a key name to keyval."""
        name_map = {
            "up": 0xff52,
            "down": 0xff54,
            "left": 0xff51,
            "right": 0xff53,
            "backspace": 0xff08,
            "enter": 0xff0d,
            "return": 0xff0d,
            "escape": 0xff1b,
            "esc": 0xff1b,
            "tab": 0xff09,
            "space": 0x20,
        }
        if name.lower() in name_map:
            return name_map[name.lower()]
        if len(name) == 1:
            return ord(name.upper())
        return None


# Singleton instance
_shortcuts_manager: Optional[ShortcutsManager] = None


def get_shortcuts_manager() -> ShortcutsManager:
    """Get the singleton shortcuts manager."""
    global _shortcuts_manager
    if _shortcuts_manager is None:
        _shortcuts_manager = ShortcutsManager()
    return _shortcuts_manager
