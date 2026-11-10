#!/usr/bin/env python3
"""
test_gui_startup.py — Regression tests for the GUI startup blockers.

The main window never appeared because:
  1. FileExplorer recursively scanned the whole home directory on the GTK
     thread before the window was shown (unbounded _build_tree recursion);
  2. _setup_accelerators passed plain ints as modifiers to
     Gtk.AccelGroup.connect (TypeError);
  3. LmuxWindow referenced self._accel_group, which was never set
     (AttributeError);
  4. _accel_handler had been removed while every accelerator still called it.

Requires a display (X11/Wayland); skipped otherwise.

Run:
    python3 tests/test_gui_startup.py -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

if HAS_DISPLAY:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk


def _make_tree(root: Path, depth: int) -> None:
    cur = root
    for i in range(depth):
        cur = cur / f"d{i}"
        cur.mkdir()
        (cur / "file.txt").write_text("x")


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class FileExplorerScanTests(unittest.TestCase):
    def setUp(self):
        from file_explorer import FileExplorer
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.FileExplorer = FileExplorer

    def tearDown(self):
        self.tmp.cleanup()

    def _child(self, node, name):
        return next(c for c in node.children if c.name == name)

    def test_initial_scan_is_depth_limited(self):
        _make_tree(self.root, 5)
        explorer = self.FileExplorer(initial_path=str(self.root))
        d0 = self._child(explorer._root_node, "d0")
        d1 = self._child(d0, "d1")
        # d1's own contents must not be scanned up front.
        self.assertEqual(d1.children, [])

    def test_symlink_loop_does_not_hang(self):
        (self.root / "a").mkdir()
        os.symlink(self.root, self.root / "a" / "loop")
        explorer = self.FileExplorer(initial_path=str(self.root))
        a = self._child(explorer._root_node, "a")
        loop = self._child(a, "loop")
        self.assertTrue(loop.is_dir)

    def test_expanding_unscanned_dir_loads_children(self):
        _make_tree(self.root, 4)
        explorer = self.FileExplorer(initial_path=str(self.root))
        d1 = self._child(self._child(explorer._root_node, "d0"), "d1")
        explorer._ensure_children(d1)
        names = sorted(c.name for c in d1.children)
        self.assertEqual(names, ["d2", "file.txt"])


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class WorkspaceGroupsShapeTests(unittest.TestCase):
    def test_daemon_list_shape_is_normalised(self):
        """workspace.group.list returns a list of {name, workspace_ids};
        the panel used to call .items() on it and crash."""
        from workspace_groups import normalize_groups
        daemon = [{"name": "a", "workspace_count": 2, "workspace_ids": [1, 3]},
                  {"name": "b", "workspace_count": 0}]
        self.assertEqual(normalize_groups(daemon), {"a": ["1", "3"], "b": []})

    def test_legacy_dict_shape_still_accepted(self):
        from workspace_groups import normalize_groups
        self.assertEqual(normalize_groups({"a": ["1"]}), {"a": ["1"]})
        self.assertEqual(normalize_groups(None), {})


class _RecordingClient:
    """Stand-in DaemonClient: records requests, answers ok."""

    def __init__(self):
        self.sent = []
        self._hook_registry = None
        self._focus_history = None
        self._browser_api_bridge = None

    def send(self, cmd, args=None):
        self.sent.append((cmd, args or {}))
        return {"ok": True, "result": {}}

    def is_daemon_alive(self):
        return False

    def send_events(self, callback=None):
        import threading
        return threading.Event()


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class SidebarAttentionTests(unittest.TestCase):
    """cmux-style attention: a workspace whose agent raised a notification
    shows a dot (and the message) in the sidebar until it is opened."""

    def setUp(self):
        from sidebar import Sidebar
        self.sb = Sidebar(_RecordingClient())

    def _row(self, ws_id=1):
        return self.sb._rows[ws_id]

    def test_unread_waiting_workspace_shows_dot_and_message(self):
        self.sb._update_list([{"id": 1, "title": "agent", "unread": True,
                               "waiting": True,
                               "last_notify": "Agent needs input"}])
        row = self._row()
        self.assertEqual(row._indicator.get_text(), "●")
        self.assertTrue(row._indicator.get_style_context().has_class("waiting-indicator"))
        self.assertIn("Agent needs input", row._notify_label.get_text())
        self.assertTrue(row._notify_label.get_visible())

    def test_read_workspace_clears_dot_and_message(self):
        self.sb._update_list([{"id": 1, "title": "a", "unread": True,
                               "waiting": True, "last_notify": "x"}])
        self.sb._update_list([{"id": 1, "title": "a", "unread": False,
                               "waiting": False, "last_notify": "x"}])
        row = self._row()
        self.assertEqual(row._indicator.get_text(), "")
        ctx = row._indicator.get_style_context()
        self.assertFalse(ctx.has_class("waiting-indicator"))
        self.assertFalse(ctx.has_class("unread-indicator"))
        self.assertFalse(row._notify_label.get_visible())

    def test_unread_without_waiting_uses_unread_style(self):
        self.sb._update_list([{"id": 1, "title": "a", "unread": True,
                               "waiting": False, "last_notify": "done"}])
        ctx = self._row()._indicator.get_style_context()
        self.assertTrue(ctx.has_class("unread-indicator"))
        self.assertFalse(ctx.has_class("waiting-indicator"))


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class OpenWorkspaceMarksReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k) for k in ("HOME", "LMUX_SOCKET_PATH")}
        os.environ["HOME"] = self.tmp.name
        os.environ["LMUX_SOCKET_PATH"] = os.path.join(self.tmp.name, "none.sock")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_selecting_workspace_marks_its_notifications_read(self):
        import main
        client = _RecordingClient()
        win = main.LmuxWindow(client=client)
        try:
            win._on_workspace_selected(None, 7)
            deadline = __import__("time").time() + 3
            while ("notification.mark_read", {"workspace_id": "7"}) not in client.sent \
                    and __import__("time").time() < deadline:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            self.assertIn(("notification.mark_read", {"workspace_id": "7"}),
                          client.sent)
        finally:
            win.destroy()


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class MainWindowStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {k: os.environ.get(k) for k in ("HOME", "LMUX_SOCKET_PATH")}
        os.environ["HOME"] = self.tmp.name
        os.environ["LMUX_SOCKET_PATH"] = os.path.join(self.tmp.name, "none.sock")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_window_constructs_and_wires_accelerators(self):
        import main
        win = main.LmuxWindow()
        try:
            self.assertIsInstance(win._accel_group, Gtk.AccelGroup)
            self.assertTrue(callable(getattr(win, "_accel_handler", None)))
            self.assertTrue(win.get_visible())
        finally:
            win.destroy()

    def test_window_can_shrink(self):
        """The window must not be pinned at its starting size.

        The sidebar's stacked panels used to set a ~700px minimum height,
        and a long workspace path label plus the (never really hidden)
        browser panel set a ~1120px minimum width, so the window could not
        be resized smaller in either direction.
        """
        import main
        win = main.LmuxWindow()
        try:
            min_h, _ = win.get_preferred_height()
            min_w, _ = win.get_preferred_width()
            self.assertLess(min_h, 300, "window minimum height too large")
            self.assertLess(min_w, 700, "window minimum width too large")
        finally:
            win.destroy()

    def test_sidebar_has_one_file_browser(self):
        """The FileExplorer panel and a built-in "Files" extension tab both
        showed a file list (the extension's of the GUI process's own cwd)."""
        import main
        win = main.LmuxWindow()
        try:
            st = win._sidebar._stack
            pages = [st.child_get_property(c, "title") for c in st.get_children()]
            self.assertNotIn("Files", pages, pages)
            from file_explorer import FileExplorer
            def walk(w):
                yield w
                if isinstance(w, Gtk.Container):
                    for c in w.get_children():
                        yield from walk(c)
            explorers = [w for w in walk(win) if isinstance(w, FileExplorer)]
            self.assertEqual(len(explorers), 1)
        finally:
            win.destroy()

    def test_browser_panel_hidden_until_toggled(self):
        import main
        win = main.LmuxWindow()
        try:
            self.assertFalse(win._browser.get_visible(),
                             "show_all() revealed the hidden browser panel")
            win._toggle_browser()
            self.assertTrue(win._browser.get_visible())
            win._toggle_browser()
            self.assertFalse(win._browser.get_visible(),
                             "toggling off left the browser visible")
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
