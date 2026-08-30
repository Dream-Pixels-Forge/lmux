"""Browser find functionality for lmux — Ctrl+F in browser."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import logging

logger = logging.getLogger("lmux.browser_find")


class BrowserFindBar(Gtk.Box):
    """Find bar for the browser panel."""

    def __init__(self, browser_widget):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._browser = browser_widget
        self._match_count = 0
        self._current_match = 0

        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        # Find entry
        self._find_entry = Gtk.SearchEntry()
        self._find_entry.set_placeholder_text("Find in page...")
        self._find_entry.connect("search-changed", self._on_search_changed)
        self._find_entry.connect("next-match", self._on_next_match)
        self._find_entry.connect("previous-match", self._on_previous_match)
        self._find_entry.connect("stop-search", self._on_stop_search)
        self.pack_start(self._find_entry, True, True, 0)

        # Match count label
        self._match_label = Gtk.Label(label="")
        self._match_label.set_width_chars(12)
        self.pack_start(self._match_label, False, False, 0)

        # Navigation buttons
        prev_btn = Gtk.Button(label="▲")
        prev_btn.set_size_request(28, 28)
        prev_btn.connect("clicked", lambda _: self._on_previous_match())
        self.pack_start(prev_btn, False, False, 0)

        next_btn = Gtk.Button(label="▼")
        next_btn.set_size_request(28, 28)
        next_btn.connect("clicked", lambda _: self._on_next_match())
        self.pack_start(next_btn, False, False, 0)

        # Close button
        close_btn = Gtk.Button(label="×")
        close_btn.set_size_request(28, 28)
        close_btn.connect("clicked", lambda _: self.hide())
        self.pack_end(close_btn, False, False, 0)

        self.hide()

    def show_find(self):
        """Show the find bar."""
        self.show_all()
        self._find_entry.grab_focus()

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        text = entry.get_text()
        if text:
            self._find_in_browser(text)
        else:
            self._clear_highlight()
            self._match_label.set_text("")

    def _on_next_match(self, *args):
        """Go to next match."""
        if self._match_count > 0:
            self._current_match = (self._current_match + 1) % self._match_count
            self._update_match_label()
            self._navigate_to_match(self._current_match)

    def _on_previous_match(self, *args):
        """Go to previous match."""
        if self._match_count > 0:
            self._current_match = (self._current_match - 1) % self._match_count
            self._update_match_label()
            self._navigate_to_match(self._current_match)

    def _on_stop_search(self, *args):
        """Close the find bar."""
        self._clear_highlight()
        self.hide()

    def _find_in_browser(self, text: str):
        """Find text in the browser."""
        if not self._browser:
            return

        try:
            # Use WebKit's find functionality
            if hasattr(self._browser, 'get_find_controller'):
                find_controller = self._browser.get_find_controller()
                find_controller.search(text, 0, 1000)  # Find all matches
                self._match_count = 100  # Placeholder
                self._current_match = 0
                self._update_match_label()
            else:
                # Fallback: inject JavaScript
                js = f"""
                window.find('{text}', false, false, true);
                """
                if hasattr(self._browser, 'execute_javascript'):
                    self._browser.execute_javascript(js)
        except Exception as e:
            logger.error(f"Error finding text: {e}")

    def _clear_highlight(self):
        """Clear search highlights."""
        if self._browser and hasattr(self._browser, 'get_find_controller'):
            try:
                self._browser.get_find_controller().search_finish()
            except Exception:
                pass

    def _navigate_to_match(self, match_index: int):
        """Navigate to a specific match."""
        # Navigation is handled by WebKit's find controller
        pass

    def _update_match_label(self):
        """Update the match count label."""
        if self._match_count > 0:
            self._match_label.set_text(
                f"{self._current_match + 1} of {self._match_count}"
            )
        else:
            self._match_label.set_text("No matches")
