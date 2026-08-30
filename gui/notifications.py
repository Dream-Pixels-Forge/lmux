"""Notification system for lmux — rings, panel, badge, sounds."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import logging
import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

logger = logging.getLogger("lmux.notifications")


class NotificationPriority(Enum):
    """Notification priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Notification:
    """A single notification."""
    id: str
    title: str
    body: str
    agent: Optional[str] = None
    workspace_id: Optional[str] = None
    pane_id: Optional[str] = None
    priority: NotificationPriority = NotificationPriority.NORMAL
    read: bool = False
    timestamp: float = 0.0
    icon: Optional[str] = None
    actions: Optional[List[Dict[str, str]]] = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = datetime.now().timestamp()


class NotificationSound(Enum):
    """Notification sound types."""
    NONE = "none"
    BELL = "bell"
    CHIME = "chime"
    ALERT = "alert"
    PING = "ping"


class NotificationManager:
    """Manages notifications for lmux."""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = Path(config_dir or "~/.config/lmux").expanduser()
        self._notifications_file = self._config_dir / "notifications.json"
        self._notifications: List[Notification] = []
        self._listeners: List[callable] = []
        self._sound_enabled = True
        self._default_sound = NotificationSound.BELL
        self._load_notifications()

    def _load_notifications(self):
        """Load notifications from file."""
        if self._notifications_file.exists():
            try:
                with open(self._notifications_file, "r") as f:
                    data = json.load(f)
                    for item in data:
                        item["priority"] = NotificationPriority(item.get("priority", "normal"))
                        self._notifications.append(Notification(**item))
            except Exception as e:
                logger.error(f"Error loading notifications: {e}")

    def _save_notifications(self):
        """Save notifications to file."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = []
        for n in self._notifications[-100:]:  # Keep last 100
            data.append({
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "agent": n.agent,
                "workspace_id": n.workspace_id,
                "pane_id": n.pane_id,
                "priority": n.priority.value,
                "read": n.read,
                "timestamp": n.timestamp,
                "icon": n.icon,
                "actions": n.actions
            })
        with open(self._notifications_file, "w") as f:
            json.dump(data, f, indent=2)

    def add(self, notification: Notification):
        """Add a new notification."""
        self._notifications.append(notification)
        self._save_notifications()

        # Play sound
        if self._sound_enabled and notification.priority in (
            NotificationPriority.HIGH, NotificationPriority.URGENT
        ):
            self._play_sound(self._default_sound)

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(notification)
            except Exception as e:
                logger.error(f"Error notifying listener: {e}")

    def mark_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        for n in self._notifications:
            if n.id == notification_id:
                n.read = True
                self._save_notifications()
                return True
        return False

    def mark_all_read(self):
        """Mark all notifications as read."""
        for n in self._notifications:
            n.read = True
        self._save_notifications()

    def clear(self):
        """Clear all notifications."""
        self._notifications.clear()
        self._save_notifications()

    def get_unread_count(self) -> int:
        """Get count of unread notifications."""
        return sum(1 for n in self._notifications if not n.read)

    def get_notifications(self, unread_only: bool = False) -> List[Notification]:
        """Get notifications."""
        if unread_only:
            return [n for n in self._notifications if not n.read]
        return self._notifications.copy()

    def add_listener(self, callback: callable):
        """Add a listener for new notifications."""
        self._listeners.append(callback)

    def remove_listener(self, callback: callable):
        """Remove a listener."""
        self._listeners = [l for l in self._listeners if l != callback]

    def _play_sound(self, sound: NotificationSound):
        """Play a notification sound."""
        if sound == NotificationSound.NONE:
            return

        sound_map = {
            NotificationSound.BELL: "bell.oga",
            NotificationSound.CHIME: "chime.oga",
            NotificationSound.ALERT: "alert.oga",
            NotificationSound.PING: "ping.oga"
        }

        sound_file = sound_map.get(sound)
        if sound_file:
            try:
                # Try to play sound via paplay or aplay
                sound_path = Path(__file__).parent / "sounds" / sound_file
                if sound_path.exists():
                    subprocess.Popen(
                        ["paplay", str(sound_path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
            except Exception:
                pass  # Sound playback is best-effort


class NotificationRing(Gtk.DrawingArea):
    """Visual ring indicator for notifications on panes."""

    def __init__(self, color: str = "#4A90D9", thickness: int = 3):
        super().__init__()
        self._color = Gdk.color_parse(color)
        self._thickness = thickness
        self._active = False
        self.set_size_request(thickness * 2 + 4, thickness * 2 + 4)

    def set_active(self, active: bool):
        """Toggle the ring visibility."""
        self._active = active
        self.queue_draw()

    def do_draw(self, cr):
        """Draw the notification ring."""
        if not self._active:
            return

        alloc = self.get_allocation()
        cx = alloc.width / 2
        cy = alloc.height / 2
        radius = min(cx, cy) - self._thickness

        # Set color
        cr.set_source_rgb(
            self._color.red / 65535,
            self._color.green / 65535,
            self._color.blue / 65535
        )
        cr.set_line_width(self._thickness)

        # Draw circle
        cr.arc(cx, cy, radius, 0, 2 * 3.14159)
        cr.stroke()


class NotificationBadge(Gtk.Label):
    """Badge showing unread notification count."""

    def __init__(self):
        super().__init__()
        self.get_style_context().add_class("notification-badge")
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.END)
        self.set_margin_top(2)
        self.set_margin_end(2)
        self._count = 0
        self.update(0)

    def update(self, count: int):
        """Update the badge count."""
        self._count = count
        if count > 0:
            self.set_text(str(count) if count < 100 else "99+")
            self.show()
        else:
            self.hide()


class NotificationPanel(Gtk.Box):
    """Panel showing notification history."""

    def __init__(self, manager: NotificationManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._manager = manager
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Notifications")
        title.set_xalign(0)
        title.get_style_context().add_class("heading")
        header.pack_start(title, True, True, 0)

        clear_btn = Gtk.Button(label="Clear All")
        clear_btn.connect("clicked", lambda _: self._on_clear())
        header.pack_end(clear_btn, False, False, 0)

        mark_read_btn = Gtk.Button(label="Mark All Read")
        mark_read_btn.connect("clicked", lambda _: self._on_mark_all_read())
        header.pack_end(mark_read_btn, False, False, 0)

        self.pack_start(header, False, False, 0)

        # Scrolled list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._list)
        self.pack_start(scroll, True, True, 0)

        # Refresh button
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda _: self.refresh())
        self.pack_end(refresh_btn, False, False, 0)

        self.refresh()

    def refresh(self):
        """Refresh the notification list."""
        # Clear existing rows
        for child in self._list.get_children():
            self._list.remove(child)

        # Add notifications
        for notification in self._manager.get_notifications():
            row = self._create_row(notification)
            self._list.add(row)

        self._list.show_all()

    def _create_row(self, notification: Notification) -> Gtk.ListBoxRow:
        """Create a row for a notification."""
        row = Gtk.ListBoxRow()
        row._notification = notification

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Unread indicator
        if not notification.read:
            dot = Gtk.Label(label="●")
            dot.get_style_context().add_class("unread-indicator")
            box.pack_start(dot, False, False, 0)

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        title_label = Gtk.Label(label=notification.title)
        title_label.set_xalign(0)
        title_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        if not notification.read:
            title_label.get_style_context().add_class("bold")
        content.pack_start(title_label, False, False, 0)

        if notification.body:
            body_label = Gtk.Label(label=notification.body)
            body_label.set_xalign(0)
            body_label.set_ellipsize(3)
            body_label.get_style_context().add_class("dim-label")
            content.pack_start(body_label, False, False, 0)

        # Metadata
        meta_parts = []
        if notification.agent:
            meta_parts.append(notification.agent)
        if notification.workspace_id:
            meta_parts.append(f"ws:{notification.workspace_id}")
        meta_parts.append(self._format_time(notification.timestamp))

        meta_label = Gtk.Label(label=" · ".join(meta_parts))
        meta_label.set_xalign(0)
        meta_label.get_style_context().add_class("dim-label")
        meta_label.get_style_context().add_class("small")
        content.pack_start(meta_label, False, False, 0)

        box.pack_start(content, True, True, 0)

        # Priority indicator
        if notification.priority in (NotificationPriority.HIGH, NotificationPriority.URGENT):
            priority_color = "#FF6B6B" if notification.priority == NotificationPriority.URGENT else "#FFA726"
            priority_label = Gtk.Label(label="!")
            priority_label.get_style_context().add_class("priority-indicator")
            box.pack_end(priority_label, False, False, 0)

        row.add(box)
        return row

    def _format_time(self, timestamp: float) -> str:
        """Format timestamp to relative time."""
        now = datetime.now().timestamp()
        diff = now - timestamp

        if diff < 60:
            return "just now"
        elif diff < 3600:
            minutes = int(diff / 60)
            return f"{minutes}m ago"
        elif diff < 86400:
            hours = int(diff / 3600)
            return f"{hours}h ago"
        else:
            days = int(diff / 86400)
            return f"{days}d ago"

    def _on_clear(self):
        """Clear all notifications."""
        self._manager.clear()
        self.refresh()

    def _on_mark_all_read(self):
        """Mark all notifications as read."""
        self._manager.mark_all_read()
        self.refresh()


class NotificationPopover(Gtk.Popover):
    """Popover showing recent notifications."""

    def __init__(self, manager: NotificationManager, relative_to: Gtk.Widget):
        super().__init__()
        self._manager = manager
        self.set_relative_to(relative_to)
        self.set_position(Gtk.PositionType.BOTTOM)

        self._panel = NotificationPanel(manager)
        self.add(self._panel)

        # Connect to manager updates
        manager.add_listener(self._on_notification)

    def _on_notification(self, notification: Notification):
        """Handle new notification."""
        self._panel.refresh()


def create_notification_css():
    """Create CSS for notification styles."""
    css = """
    .notification-badge {
        background: #FF6B6B;
        color: white;
        border-radius: 10px;
        padding: 2px 6px;
        font-size: 10px;
        font-weight: bold;
    }
    .unread-indicator {
        color: #4A90D9;
        font-size: 8px;
    }
    .priority-indicator {
        color: #FF6B6B;
        font-weight: bold;
        font-size: 14px;
    }
    .bold {
        font-weight: bold;
    }
    .small {
        font-size: 11px;
    }
    """
    return css
