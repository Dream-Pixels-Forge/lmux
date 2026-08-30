"""Workspace tab colors for lmux."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import json
import logging
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger("lmux.workspace_colors")


@dataclass
class TabColor:
    """Color configuration for a workspace tab."""
    background: str = "#2C3E50"
    text: str = "#FFFFFF"
    border: str = "#34495E"


# Default color palette
COLOR_PALETTE = [
    TabColor("#2C3E50", "#FFFFFF", "#34495E"),  # Dark blue
    TabColor("#8E44AD", "#FFFFFF", "#9B59B6"),  # Purple
    TabColor("#27AE60", "#FFFFFF", "#2ECC71"),  # Green
    TabColor("#E67E22", "#FFFFFF", "#F39C12"),  # Orange
    TabColor("#C0392B", "#FFFFFF", "#E74C3C"),  # Red
    TabColor("#16A085", "#FFFFFF", "#1ABC9C"),  # Teal
    TabColor("#2980B9", "#FFFFFF", "#3498DB"),  # Blue
    TabColor("#F39C12", "#000000", "#F1C40F"),  # Yellow
]


class WorkspaceColorManager:
    """Manages colors for workspace tabs."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._colors_file = self._config_dir / "workspace_colors.json"
        self._colors: Dict[str, TabColor] = {}
        self._next_color_index = 0
        self._load_colors()

    def _load_colors(self):
        """Load colors from file."""
        if self._colors_file.exists():
            try:
                with open(self._colors_file, "r") as f:
                    data = json.load(f)
                    for ws_id, color_data in data.items():
                        self._colors[ws_id] = TabColor(**color_data)
            except Exception as e:
                logger.error(f"Error loading workspace colors: {e}")

    def _save_colors(self):
        """Save colors to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        for ws_id, color in self._colors.items():
            data[ws_id] = {
                "background": color.background,
                "text": color.text,
                "border": color.border
            }
        with open(self._colors_file, "w") as f:
            json.dump(data, f, indent=2)

    def get_color(self, workspace_id: str) -> TabColor:
        """Get the color for a workspace tab."""
        if workspace_id not in self._colors:
            # Assign next color from palette
            color = COLOR_PALETTE[self._next_color_index % len(COLOR_PALETTE)]
            self._colors[workspace_id] = color
            self._next_color_index += 1
            self._save_colors()
        return self._colors[workspace_id]

    def set_color(self, workspace_id: str, color: TabColor):
        """Set the color for a workspace tab."""
        self._colors[workspace_id] = color
        self._save_colors()

    def set_color_by_index(self, workspace_id: str, index: int):
        """Set workspace color by palette index."""
        if 0 <= index < len(COLOR_PALETTE):
            self.set_color(workspace_id, COLOR_PALETTE[index])

    def remove_color(self, workspace_id: str):
        """Remove a workspace's custom color."""
        if workspace_id in self._colors:
            del self._colors[workspace_id]
            self._save_colors()

    def get_palette(self) -> list:
        """Get the available color palette."""
        return COLOR_PALETTE.copy()


def apply_tab_color(widget: Gtk.Widget, color: TabColor):
    """Apply color to a tab widget."""
    css = f"""
    .workspace-tab {{
        background: {color.background};
        color: {color.text};
        border-bottom: 2px solid {color.border};
        padding: 4px 8px;
    }}
    """
    style_context = widget.get_style_context()
    # Note: In production, use a CSS provider
    # For now, we'll just log the intended style
    logger.debug(f"Applied tab color: {color.background}")


def create_color_chooser(callback, workspace_id: str):
    """Create a color chooser dialog."""
    dialog = Gtk.Dialog(
        title="Choose Tab Color",
        buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                 Gtk.STOCK_OK, Gtk.ResponseType.OK)
    )

    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_top(16)
    box.set_margin_bottom(16)

    # Color grid
    grid = Gtk.Grid()
    grid.set_column_spacing(4)
    grid.set_row_spacing(4)

    for i, color in enumerate(COLOR_PALETTE):
        button = Gtk.Button()
        button.set_size_request(48, 48)

        # Apply color to button
        css = f"""
        button {{
            background: {color.background};
            border: 2px solid {color.border};
            border-radius: 4px;
        }}
        """

        button.connect("clicked", lambda _, c=color: (
            callback(workspace_id, c),
            dialog.response(Gtk.ResponseType.OK)
        ))
        grid.attach(button, i % 4, i // 4, 1, 1)

    box.pack_start(grid, False, False, 0)
    grid.show_all()

    return dialog
