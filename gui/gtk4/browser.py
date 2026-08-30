"""GTK4 browser panel for lmux — WebKit2 for GTK4.

Requires: gir1.2-webkit-6.0 (WebKit2 for GTK4)
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Gdk, GLib, WebKit
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BrowserPanel(Gtk.Box):
    """WebKit browser panel for GTK4."""

    def __init__(self, client=None, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._client = client
        self._parent = parent_window

        # Navigation bar
        nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        nav_bar.set_margin_top(4)
        nav_bar.set_margin_bottom(4)
        nav_bar.set_margin_start(8)
        nav_bar.set_margin_end(8)

        self._btn_back = Gtk.Button(label="\u25c0")
        self._btn_back.add_css_class("flat")
        self._btn_back.connect("clicked", lambda _: self._web_view.go_back())
        nav_bar.append(self._btn_back)

        self._btn_forward = Gtk.Button(label="\u25b6")
        self._btn_forward.add_css_class("flat")
        self._btn_forward.connect("clicked", lambda _: self._web_view.go_forward())
        nav_bar.append(self._btn_forward)

        self._btn_reload = Gtk.Button(label="\u21bb")
        self._btn_reload.add_css_class("flat")
        self._btn_reload.connect("clicked", lambda _: self._web_view.reload())
        nav_bar.append(self._btn_reload)

        self._url_entry = Gtk.Entry()
        self._url_entry.set_hexpand(True)
        self._url_entry.set_placeholder_text("Enter URL...")
        self._url_entry.connect("activate", self._on_url_activate)
        nav_bar.append(self._url_entry)

        self.append(nav_bar)

        # WebKit view
        self._web_view = WebKit.WebView()
        self._web_view.set_vexpand(True)

        # Settings
        settings = self._web_view.get_settings()
        settings.set_enable_javascript(True)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self._web_view)
        self.append(scroll)

        # Status bar
        self._status_bar = Gtk.Label(label="Ready")
        self._status_bar.set_xalign(0)
        self._status_bar.set_opacity(0.7)
        self._status_bar.set_margin_start(8)
        self.append(self._status_bar)

        self.navigate("about:blank")

    def navigate(self, url):
        if not url:
            return
        if not url.startswith(("http://", "https://", "file://", "about:")):
            url = "https://" + url
        self._web_view.load_uri(url)

    def _on_url_activate(self, entry):
        url = entry.get_text().strip()
        if url:
            self.navigate(url)

        # Connect signals
        self._web_view.connect("notify::uri", self._on_uri_changed)
        self._web_view.connect("notify::title", self._on_title_changed)
        self._web_view.connect("load-changed", self._on_load_changed)

    def _on_uri_changed(self, web_view, param):
        uri = web_view.get_uri() or ""
        self._url_entry.set_text(uri)
        self._btn_back.set_sensitive(web_view.can_go_back())
        self._btn_forward.set_sensitive(web_view.can_go_forward())

    def _on_title_changed(self, web_view, param):
        title = web_view.get_title() or ""
        if self._parent and title:
            self._parent.set_title(f"lmux — {title}")

    def _on_load_changed(self, web_view, load_event):
        if load_event == WebKit.LoadEvent.STARTED:
            self._status_bar.set_text("Loading...")
        elif load_event == WebKit.LoadEvent.FINISHED:
            self._status_bar.set_text("Done")
