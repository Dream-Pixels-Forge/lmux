"""In-app browser panel for lmux GUI — WebKit2GTK 4.1 integration."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, Gdk, GLib, WebKit2
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daemon_client import DaemonClient


# ── Browser panel ───────────────────────────────────────────

class BrowserPanel(Gtk.Box):
    """WebKit browser panel with address bar and navigation controls."""

    def __init__(self, client=None, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._parent = parent_window
        self._history = []
        self._history_index = -1

        # CSS
        css = b"""
        .browser-bar {
            background: #2d2d2d;
            padding: 4px 8px;
        }
        .browser-entry {
            background: #313244;
            color: #cdd6f4;
            font-size: 12px;
            padding: 4px 8px;
            border: 1px solid #45475a;
            border-radius: 4px;
        }
        .browser-entry:focus {
            border-color: #89b4fa;
        }
        .browser-btn {
            padding: 2px 8px;
            min-width: 24px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Navigation bar
        nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        nav_bar.get_style_context().add_class("browser-bar")

        self._btn_back = Gtk.Button(label="\u25c0")
        self._btn_back.get_style_context().add_class("browser-btn")
        self._btn_back.set_tooltip_text("Back")
        self._btn_back.connect("clicked", lambda _: self.go_back())
        nav_bar.pack_start(self._btn_back, False, False, 0)

        self._btn_forward = Gtk.Button(label="\u25b6")
        self._btn_forward.get_style_context().add_class("browser-btn")
        self._btn_forward.set_tooltip_text("Forward")
        self._btn_forward.connect("clicked", lambda _: self.go_forward())
        nav_bar.pack_start(self._btn_forward, False, False, 0)

        self._btn_reload = Gtk.Button(label="\u21bb")
        self._btn_reload.get_style_context().add_class("browser-btn")
        self._btn_reload.set_tooltip_text("Reload")
        self._btn_reload.connect("clicked", lambda _: self.reload())
        nav_bar.pack_start(self._btn_reload, False, False, 0)

        self._url_entry = Gtk.Entry()
        self._url_entry.get_style_context().add_class("browser-entry")
        self._url_entry.set_hexpand(True)
        self._url_entry.set_placeholder_text("Enter URL...")
        self._url_entry.connect("activate", self._on_url_activate)
        nav_bar.pack_start(self._url_entry, True, True, 0)

        self._btn_go = Gtk.Button(label="\u21b5")
        self._btn_go.get_style_context().add_class("browser-btn")
        self._btn_go.set_tooltip_text("Go")
        self._btn_go.connect("clicked", lambda _: self.navigate(self._url_entry.get_text()))
        nav_bar.pack_start(self._btn_go, False, False, 0)

        self._btn_devtools = Gtk.Button(label="\u2699")
        self._btn_devtools.get_style_context().add_class("browser-btn")
        self._btn_devtools.set_tooltip_text("Toggle Developer Tools")
        self._btn_devtools.connect("clicked", lambda _: self._toggle_devtools())
        nav_bar.pack_start(self._btn_devtools, False, False, 0)

        self._btn_external = Gtk.Button(label="\u2197")
        self._btn_external.get_style_context().add_class("browser-btn")
        self._btn_external.set_tooltip_text("Open in External Browser")
        self._btn_external.connect("clicked", lambda _: self._open_external())
        nav_bar.pack_start(self._btn_external, False, False, 0)

        self.pack_start(nav_bar, False, False, 0)

        # WebKit view
        self._web_view = WebKit2.WebView()
        self._web_view.connect("notify::uri", self._on_uri_changed)
        self._web_view.connect("notify::title", self._on_title_changed)
        self._web_view.connect("load-changed", self._on_load_changed)

        # Settings
        settings = self._web_view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(True)

        scroll = Gtk.ScrolledWindow()
        scroll.add(self._web_view)
        self.pack_start(scroll, True, True, 0)

        # Status bar
        self._status_bar = Gtk.Label(label="Ready")
        self._status_bar.set_xalign(0)
        self._status_bar.set_ellipsize(3)  # Pango.EllipsizeMode.END
        self._status_bar.set_margin_start(8)
        self._status_bar.set_margin_end(8)
        self._status_bar.set_opacity(0.7)
        self.pack_start(self._status_bar, False, False, 2)

        # Load default page
        self.navigate("about:blank")

    # ── navigation ──────────────────────────────────────────

    def navigate(self, url):
        """Navigate to a URL."""
        if not url:
            return
        # Add protocol if missing
        if not url.startswith(("http://", "https://", "file://", "about:")):
            url = "https://" + url
        self._web_view.load_uri(url)

    def go_back(self):
        """Go back in history."""
        if self._web_view.can_go_back():
            self._web_view.go_back()

    def go_forward(self):
        """Go forward in history."""
        if self._web_view.can_go_forward():
            self._web_view.go_forward()

    def reload(self):
        """Reload current page."""
        self._web_view.reload()

    def _on_url_activate(self, entry):
        """URL entry activated — navigate."""
        url = entry.get_text().strip()
        if url:
            self.navigate(url)

    def _on_uri_changed(self, web_view, param):
        """URI changed — update URL entry."""
        uri = web_view.get_uri() or ""
        self._url_entry.set_text(uri)
        # Update back/forward button sensitivity
        self._btn_back.set_sensitive(web_view.can_go_back())
        self._btn_forward.set_sensitive(web_view.can_go_forward())

    def _on_title_changed(self, web_view, param):
        """Title changed — update window title if parent."""
        title = web_view.get_title() or ""
        if self._parent and title:
            self._parent.set_title(f"lmux — {title}")

    def _on_load_changed(self, web_view, load_event):
        """Load status changed — update status bar."""
        if load_event == WebKit2.LoadEvent.STARTED:
            self._status_bar.set_text("Loading...")
        elif load_event == WebKit2.LoadEvent.REDIRECTED:
            self._status_bar.set_text("Redirecting...")
        elif load_event == WebKit2.LoadEvent.COMMITTED:
            self._status_bar.set_text("Loading content...")
        elif load_event == WebKit2.LoadEvent.FINISHED:
            self._status_bar.set_text("Done")

    def _toggle_devtools(self):
        """Toggle WebKit inspector."""
        inspector = self._web_view.get_inspector()
        if inspector.get_inspector_view():
            inspector.close()
        else:
            inspector.show()

    def _open_external(self):
        """Open current URL in system default browser."""
        uri = self._web_view.get_uri()
        if uri and uri != "about:blank":
            try:
                import subprocess
                subprocess.Popen(["xdg-open", uri],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass

    # ── public API ──────────────────────────────────────────

    def get_uri(self):
        """Get current URI."""
        return self._web_view.get_uri()

    def get_title(self):
        """Get current page title."""
        return self._web_view.get_title()

    def set_session_state(self, state):
        """Restore session state (list of URIs)."""
        if state and isinstance(state, list):
            for uri in state:
                # Could open in tabs, but for now just navigate to last
                pass
            if state:
                self.navigate(state[-1])

    def get_session_state(self):
        """Get current session state."""
        uri = self._web_view.get_uri()
        return [uri] if uri and uri != "about:blank" else []


# ── Standalone launcher (for testing) ───────────────────────

if __name__ == "__main__":
    win = Gtk.Window(title="Browser Test")
    win.set_default_size(800, 600)

    browser = BrowserPanel(parent_window=win)
    win.add(browser)

    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
