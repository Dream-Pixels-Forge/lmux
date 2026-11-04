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


if __name__ == "__main__":
    unittest.main()
