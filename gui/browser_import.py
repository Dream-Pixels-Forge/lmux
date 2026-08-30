"""Browser import for lmux — import bookmarks, history, and settings from other browsers."""
import json
import os
import sqlite3
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import xml.etree.ElementTree as ET

logger = logging.getLogger("lmux.browser_import")


@dataclass
class Bookmark:
    """Represents a browser bookmark."""
    title: str
    url: str
    folder: str = ""
    date_added: int = 0


@dataclass
class HistoryEntry:
    """Represents a browser history entry."""
    url: str
    title: str
    visit_count: int = 1
    last_visit: int = 0


class BrowserImporter:
    """Import bookmarks and history from various browsers."""

    # Supported browsers and their data paths
    BROWSER_PATHS = {
        "chrome": {
            "name": "Google Chrome",
            "bookmarks": "~/.config/google-chrome/Default/Bookmarks",
            "history": "~/.config/google-chrome/Default/History",
        },
        "chromium": {
            "name": "Chromium",
            "bookmarks": "~/.config/chromium/Default/Bookmarks",
            "history": "~/.config/chromium/Default/History",
        },
        "firefox": {
            "name": "Mozilla Firefox",
            "bookmarks": "~/.mozilla/firefox/*/places.sqlite",
            "history": "~/.mozilla/firefox/*/places.sqlite",
        },
        "brave": {
            "name": "Brave Browser",
            "bookmarks": "~/.config/BraveSoftware/Brave-Browser/Default/Bookmarks",
            "history": "~/.config/BraveSoftware/Brave-Browser/Default/History",
        },
        "vivaldi": {
            "name": "Vivaldi",
            "bookmarks": "~/.config/vivaldi/Default/Bookmarks",
            "history": "~/.config/vivaldi/Default/History",
        },
        "opera": {
            "name": "Opera",
            "bookmarks": "~/.config/opera/Default/Bookmarks",
            "history": "~/.config/opera/Default/History",
        },
        "edge": {
            "name": "Microsoft Edge",
            "bookmarks": "~/.config/microsoft-edge/Default/Bookmarks",
            "history": "~/.config/microsoft-edge/Default/History",
        },
    }

    def __init__(self):
        self._detected_browsers: Dict[str, str] = {}

    def detect_installed_browsers(self) -> Dict[str, str]:
        """Detect installed browsers and return their data paths."""
        detected = {}
        for browser_id, info in self.BROWSER_PATHS.items():
            bookmark_path = Path(os.path.expanduser(info["bookmarks"])).expanduser()
            if bookmark_path.exists():
                detected[browser_id] = info["name"]
                self._detected_browsers[browser_id] = str(bookmark_path)
        return detected

    def import_bookmarks(self, browser_id: str) -> List[Bookmark]:
        """Import bookmarks from a browser."""
        if browser_id not in self.BROWSER_PATHS:
            raise ValueError(f"Unsupported browser: {browser_id}")

        info = self.BROWSER_PATHS[browser_id]
        bookmark_path = Path(os.path.expanduser(info["bookmarks"])).expanduser()

        if not bookmark_path.exists():
            raise FileNotFoundError(f"Bookmark file not found: {bookmark_path}")

        if browser_id in ("chrome", "chromium", "brave", "vivaldi", "opera", "edge"):
            return self._import_chrome_bookmarks(bookmark_path)
        elif browser_id == "firefox":
            return self._import_firefox_bookmarks(bookmark_path)
        else:
            raise ValueError(f"Import not implemented for: {browser_id}")

    def _import_chrome_bookmarks(self, path: Path) -> List[Bookmark]:
        """Import bookmarks from Chrome-based browsers."""
        bookmarks = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            def walk_node(node, folder=""):
                if node.get("type") == "folder":
                    folder_name = node.get("name", "")
                    new_folder = f"{folder}/{folder_name}" if folder else folder_name
                    for child in node.get("children", []):
                        walk_node(child, new_folder)
                elif node.get("type") == "url":
                    bookmarks.append(Bookmark(
                        title=node.get("name", ""),
                        url=node.get("url", ""),
                        folder=folder,
                        date_added=int(node.get("date_added", 0)),
                    ))

            for root in data.get("roots", {}).values():
                if isinstance(root, dict):
                    walk_node(root)

        except Exception as e:
            logger.error(f"Failed to import Chrome bookmarks: {e}")

        return bookmarks

    def _import_firefox_bookmarks(self, path: Path) -> List[Bookmark]:
        """Import bookmarks from Firefox places.sqlite."""
        bookmarks = []
        try:
            # Firefox locks the database, so copy it first
            import shutil
            import tempfile
            temp_dir = tempfile.mkdtemp()
            temp_db = os.path.join(temp_dir, "places.sqlite")
            shutil.copy2(str(path), temp_db)

            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()

            # Get bookmark folders
            folders = {}
            cursor.execute("SELECT id, parent, title FROM moz_bookmarks WHERE type = 2")
            for row in cursor.fetchall():
                folders[row[0]] = {"parent": row[1], "title": row[2]}

            # Get bookmarks
            cursor.execute("""
                SELECT b.title, p.url, b.dateAdded
                FROM moz_bookmarks b
                JOIN moz_places p ON b.fk = p.id
                WHERE b.type = 1
            """)

            for row in cursor.fetchall():
                # Build folder path
                folder_path = []
                # This is simplified - full implementation would traverse parent chain
                bookmarks.append(Bookmark(
                    title=row[0] or "",
                    url=row[1] or "",
                    folder="/".join(folder_path) if folder_path else "",
                    date_added=row[2] or 0,
                ))

            conn.close()
            shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"Failed to import Firefox bookmarks: {e}")

        return bookmarks

    def import_history(self, browser_id: str) -> List[HistoryEntry]:
        """Import history from a browser."""
        if browser_id not in self.BROWSER_PATHS:
            raise ValueError(f"Unsupported browser: {browser_id}")

        info = self.BROWSER_PATHS[browser_id]
        history_path = Path(os.path.expanduser(info["history"])).expanduser()

        if not history_path.exists():
            raise FileNotFoundError(f"History file not found: {history_path}")

        if browser_id in ("chrome", "chromium", "brave", "vivaldi", "opera", "edge"):
            return self._import_chrome_history(history_path)
        elif browser_id == "firefox":
            return self._import_firefox_history(history_path)
        else:
            raise ValueError(f"Import not implemented for: {browser_id}")

    def _import_chrome_history(self, path: Path) -> List[HistoryEntry]:
        """Import history from Chrome-based browsers."""
        history = []
        try:
            # Chrome locks the database, so copy it first
            import shutil
            import tempfile
            temp_dir = tempfile.mkdtemp()
            temp_db = os.path.join(temp_dir, "History")
            shutil.copy2(str(path), temp_db)

            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT url, title, visit_count, last_visit_time
                FROM urls
                ORDER BY last_visit_time DESC
                LIMIT 1000
            """)

            for row in cursor.fetchall():
                history.append(HistoryEntry(
                    url=row[0] or "",
                    title=row[1] or "",
                    visit_count=row[2] or 1,
                    last_visit=row[3] or 0,
                ))

            conn.close()
            shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"Failed to import Chrome history: {e}")

        return history

    def _import_firefox_history(self, path: Path) -> List[HistoryEntry]:
        """Import history from Firefox places.sqlite."""
        history = []
        try:
            import shutil
            import tempfile
            temp_dir = tempfile.mkdtemp()
            temp_db = os.path.join(temp_dir, "places.sqlite")
            shutil.copy2(str(path), temp_db)

            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT p.url, p.title, p.visit_count, p.last_visit_date
                FROM moz_places p
                WHERE p.visit_count > 0
                ORDER BY p.last_visit_date DESC
                LIMIT 1000
            """)

            for row in cursor.fetchall():
                history.append(HistoryEntry(
                    url=row[0] or "",
                    title=row[1] or "",
                    visit_count=row[2] or 1,
                    last_visit=row[3] or 0,
                ))

            conn.close()
            shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"Failed to import Firefox history: {e}")

        return history

    def import_all(self, browser_id: str) -> Dict[str, List]:
        """Import both bookmarks and history from a browser."""
        result = {
            "bookmarks": [],
            "history": [],
        }

        try:
            result["bookmarks"] = self.import_bookmarks(browser_id)
        except Exception as e:
            logger.error(f"Failed to import bookmarks: {e}")

        try:
            result["history"] = self.import_history(browser_id)
        except Exception as e:
            logger.error(f"Failed to import history: {e}")

        return result


def create_import_dialog(parent_window, browser_panel):
    """Create a dialog for importing from other browsers."""
    dialog = Gtk.Dialog(
        title="Import from Browser",
        transient_for=parent_window,
        flags=0,
    )
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Import", Gtk.ResponseType.OK)
    dialog.set_default_size(400, 300)

    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_top(16)

    label = Gtk.Label(label="Select browser to import from:", xalign=0)
    box.pack_start(label, False, False, 0)

    # Browser list
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)

    importer = BrowserImporter()
    detected = importer.detect_installed_browsers()

    for browser_id, browser_name in detected.items():
        row = Gtk.ListBoxRow()
        row.browser_id = browser_id

        label_item = Gtk.Label(label=browser_name, xalign=0)
        label_item.set_margin_start(8)
        label_item.set_margin_end(8)
        label_item.set_margin_top(4)
        label_item.set_margin_bottom(4)
        row.add(label_item)

        listbox.add(row)

    if not detected:
        no_browser_label = Gtk.Label(label="No supported browsers detected", xalign=0)
        no_browser_label.set_margin_start(8)
        listbox.add(no_browser_label)

    box.pack_start(listbox, True, True, 0)

    # Import options
    options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    options_box.set_margin_top(8)

    bookmarks_check = Gtk.CheckMenuItem(label="Import Bookmarks", active=True)
    options_box.pack_start(bookmarks_check, False, False, 0)

    history_check = Gtk.CheckMenuItem(label="Import History", active=True)
    options_box.pack_start(history_check, False, False, 0)

    box.pack_start(options_box, False, False, 0)

    dialog.show_all()
    response = dialog.run()

    if response == Gtk.ResponseType.OK:
        selected_row = listbox.get_selected_row()
        if selected_row and hasattr(selected_row, 'browser_id'):
            browser_id = selected_row.browser_id
            import_bookmarks = bookmarks_check.get_active()
            import_history = history_check.get_active()

            return {
                "browser_id": browser_id,
                "import_bookmarks": import_bookmarks,
                "import_history": import_history,
            }

    dialog.destroy()
    return None


def perform_import(browser_panel, import_options: Dict):
    """Perform the actual import."""
    if not import_options:
        return

    browser_id = import_options["browser_id"]
    importer = BrowserImporter()

    result = importer.import_all(browser_id)

    # TODO: Store imported data in browser panel
    # For now, just log the results
    logger.info(f"Imported {len(result['bookmarks'])} bookmarks from {browser_id}")
    logger.info(f"Imported {len(result['history'])} history entries from {browser_id}")

    return result
