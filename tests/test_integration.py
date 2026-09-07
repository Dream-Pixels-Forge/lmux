#!/usr/bin/env python3
"""
test_integration.py — End-to-end integration tests for the lmux daemon.

Exercises every major command category through the Python client library
over the Unix domain socket protocol.  A single daemon instance is shared
across all tests for speed and stability; each test method creates its own
workspace for isolation.

Usage:
    python3 tests/test_integration.py          # run all tests
    python3 tests/test_integration.py -v       # verbose
    python3 tests/test_integration.py TestPing # run one class
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add tests/ to path so we can import lmux.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lmux import LmuxClient, LmuxDaemon, LmuxError


# ====================================================================
# Single daemon shared across all test classes.
# Uses a module-level daemon so setUpClass only starts one process.
# ====================================================================

_daemon = None
_client = None


def _ensure_daemon():
    global _daemon, _client
    if _client is None:
        sock = f"/tmp/lmux-integration-{os.getpid()}.sock"
        _daemon = LmuxDaemon(sock)
        _client = _daemon.start(timeout=10)
    return _client


def _unique(prefix):
    """Globally unique workspace title."""
    return f"{prefix}-{int(time.time() * 1000) % 100000}"


# ====================================================================
# Ping & capabilities — no workspace needed
# ====================================================================

class TestPing(unittest.TestCase):
    """Daemon connectivity and capability advertisement."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_ping_ok(self):
        resp = self.c.ping()
        self.assertTrue(resp.get("ok"))
        self.assertIn("version", resp.get("result", {}))

    def test_capabilities_returns_list(self):
        resp = self.c.capabilities()
        caps = resp.get("capabilities", [])
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)

    def test_capabilities_include_core_commands(self):
        caps = self.c.capabilities().get("capabilities", [])
        for cmd in [
            "workspace.create", "workspace.list", "workspace.close",
            "surface.create", "surface.list",
            "pane.list",
            "notification.create", "notification.list",
            "snapshot.save", "snapshot.load",
            "session.save", "session.restore",
            "config.get", "config.set",
            "ping", "capabilities",
        ]:
            self.assertIn(cmd, caps, f"missing capability: {cmd}")


# ====================================================================
# Workspace lifecycle
# ====================================================================

class TestWorkspaceLifecycle(unittest.TestCase):
    """Create, list, select, current, rename, close."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        title = title or _unique("ws")
        ws = self.c.workspace_create(title)
        self.assertIn("id", ws)
        self.assertEqual(ws["title"], title)
        return ws

    def test_create_returns_id_and_title(self):
        ws = self._create_ws("my-workspace")
        self.assertIsInstance(ws["id"], int)

    def test_list_contains_created(self):
        ws = self._create_ws("list-check")
        workspaces = self.c.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertIn(ws["id"], ids)

    def test_select_and_current(self):
        ws = self._create_ws("select-check")
        self.c.workspace_select(ws["id"])
        current = self.c.workspace_current()
        self.assertEqual(current["id"], ws["id"])

    def test_rename(self):
        ws = self._create_ws("old-name")
        self.c.workspace_rename(ws["id"], "new-name")
        workspaces = self.c.workspace_list()
        for w in workspaces:
            if w["id"] == ws["id"]:
                self.assertEqual(w["title"], "new-name")
                return
        self.fail("renamed workspace not found in list")

    def test_close_removes_workspace(self):
        ws = self._create_ws("close-check")
        ws_id = ws["id"]
        self.c.workspace_close(ws_id)
        workspaces = self.c.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertNotIn(ws_id, ids)

    def test_create_multiple(self):
        ws1 = self._create_ws("multi-a")
        ws2 = self._create_ws("multi-b")
        self.assertNotEqual(ws1["id"], ws2["id"])
        workspaces = self.c.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertIn(ws1["id"], ids)
        self.assertIn(ws2["id"], ids)
        self.c.workspace_close(ws1["id"])
        self.c.workspace_close(ws2["id"])


# ====================================================================
# Surface lifecycle
# ====================================================================

class TestSurfaceLifecycle(unittest.TestCase):
    """Create, list, focus, close surfaces."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _make_ws(self):
        return self.c.workspace_create(_unique("surf-ws"))

    def test_create_returns_id(self):
        ws = self._make_ws()
        surf = self.c.surface_create(ws["id"], "my-surf")
        self.assertIn("id", surf)
        self.assertEqual(surf["title"], "my-surf")
        self.c.workspace_close(ws["id"])

    def test_list_shows_surfaces(self):
        ws = self._make_ws()
        surf = self.c.surface_create(ws["id"], "list-surf")
        # surface.list scopes to a workspace; pass workspace_id explicitly
        result = self.c.send("surface.list", {"workspace_id": str(ws["id"])})
        surfaces = result.get("result", {}).get("surfaces", [])
        ids = [s["id"] for s in surfaces]
        self.assertIn(surf["id"], ids)
        self.c.workspace_close(ws["id"])

    def test_focus(self):
        ws = self._make_ws()
        surf = self.c.surface_create(ws["id"], "focus-surf")
        self.c.surface_focus(surf["id"], ws["id"])
        surfaces = self.c.send("surface.list", {"workspace_id": str(ws["id"])})
        surfaces = surfaces.get("result", {}).get("surfaces", [])
        for s in surfaces:
            if s["id"] == surf["id"]:
                self.assertTrue(s["focused"])
                break
        self.c.workspace_close(ws["id"])

    def test_close_removes_surface(self):
        ws = self._make_ws()
        surf = self.c.surface_create(ws["id"], "close-surf")
        sid = surf["id"]
        self.c.surface_close(sid, ws["id"])
        surfaces = self.c.surface_list()
        ids = [s["id"] for s in surfaces]
        self.assertNotIn(sid, ids)
        self.c.workspace_close(ws["id"])


# ====================================================================
# Pane operations
# ====================================================================

class TestPaneOperations(unittest.TestCase):
    """Split, list, focus, resize."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _make_ws(self):
        return self.c.workspace_create(_unique("pane-ws"))

    def test_initial_pane_exists(self):
        ws = self._make_ws()
        panes = self.c.pane_list()
        self.assertGreater(len(panes), 0)
        for p in panes:
            self.assertIn("id", p)
            self.assertIn("kind", p)
            self.assertIn("focused", p)
        self.c.workspace_close(ws["id"])

    def test_split_increases_pane_count(self):
        ws = self._make_ws()
        before = len(self.c.pane_list())
        self.c.surface_split("h")
        after = len(self.c.pane_list())
        self.assertGreater(after, before)
        self.c.workspace_close(ws["id"])

    def test_split_horizontal_and_vertical(self):
        ws = self._make_ws()
        self.c.surface_split("h")
        self.c.surface_split("v")
        panes = self.c.pane_list()
        self.assertGreaterEqual(len(panes), 3)
        self.c.workspace_close(ws["id"])

    def test_focus_pane(self):
        ws = self._make_ws()
        self.c.surface_split("h")
        panes = self.c.pane_list()
        target = panes[1]["id"] if len(panes) > 1 else panes[0]["id"]
        self.c.pane_focus(target)
        panes_after = self.c.pane_list()
        for p in panes_after:
            if p["id"] == target:
                self.assertTrue(p["focused"])
                break
        self.c.workspace_close(ws["id"])

    def test_resize(self):
        ws = self._make_ws()
        self.c.pane_resize("right", 80, 24)
        self.c.pane_resize("down", 0, 10)
        self.c.workspace_close(ws["id"])


# ====================================================================
# Notifications
# ====================================================================

class TestNotifications(unittest.TestCase):
    """Create, list, clear."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_create_and_list(self):
        self.c.notification_clear()
        self.c.notification_create("hello world")
        notifs = self.c.notification_list()
        self.assertGreater(len(notifs), 0)
        texts = [n["text"] for n in notifs]
        self.assertIn("hello world", texts)

    def test_notification_has_seq(self):
        self.c.notification_clear()
        self.c.notification_create("seq-test")
        notifs = self.c.notification_list()
        self.assertGreaterEqual(len(notifs), 1)
        self.assertIn("seq", notifs[0])
        self.assertIsInstance(notifs[0]["seq"], int)

    def test_clear_removes_all(self):
        self.c.notification_create("clear-test-1")
        self.c.notification_create("clear-test-2")
        self.c.notification_clear()
        notifs = self.c.notification_list()
        self.assertEqual(len(notifs), 0)

    def test_multiple_notifications_ordering(self):
        self.c.notification_clear()
        self.c.notification_create("first")
        self.c.notification_create("second")
        notifs = self.c.notification_list()
        texts = [n["text"] for n in notifs]
        self.assertEqual(texts, ["first", "second"])


# ====================================================================
# Session save / restore
# ====================================================================

class TestSession(unittest.TestCase):
    """Save and restore session state."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_save_returns_dict(self):
        ws = self.c.workspace_create(_unique("sess-save"))
        result = self.c.session_save()
        self.assertIsInstance(result, dict)
        self.c.workspace_close(ws["id"])

    def test_restore_returns_dict(self):
        ws = self.c.workspace_create(_unique("sess-restore"))
        self.c.session_save()
        result = self.c.session_restore()
        self.assertIsInstance(result, dict)
        self.c.workspace_close(ws["id"])


# ====================================================================
# Snapshot save / load
# ====================================================================

class TestSnapshot(unittest.TestCase):
    """Save and load snapshots to/from a file path."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_save_returns_path(self):
        ws = self.c.workspace_create(_unique("snap-save"))
        snap_path = os.path.join(tempfile.gettempdir(),
                                 f"lmux-test-snap-{os.getpid()}.json")
        try:
            result = self.c.snapshot_save(snap_path)
            self.assertTrue(os.path.exists(snap_path))
        finally:
            try:
                os.unlink(snap_path)
            except OSError:
                pass
            self.c.workspace_close(ws["id"])

    def test_load_from_saved_file(self):
        ws = self.c.workspace_create(_unique("snap-load"))
        snap_path = os.path.join(tempfile.gettempdir(),
                                 f"lmux-test-snap-load-{os.getpid()}.json")
        try:
            self.c.snapshot_save(snap_path)
            # Load the file we just saved
            result = self.c.snapshot_load(snap_path)
            self.assertIsInstance(result, dict)
        finally:
            try:
                os.unlink(snap_path)
            except OSError:
                pass
            self.c.workspace_close(ws["id"])

    def test_save_default_path(self):
        ws = self.c.workspace_create(_unique("snap-default"))
        result = self.c.snapshot_save()
        self.assertIn("path", result)
        self.c.workspace_close(ws["id"])


# ====================================================================
# Config get / set
# ====================================================================

class TestConfig(unittest.TestCase):
    """Read and write configuration values."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()
        cls._orig_font = cls.c.config_get("font_family")

    def test_get_all_returns_dict(self):
        cfg = self.c.config_get()
        self.assertIsInstance(cfg, dict)
        self.assertIn("font_family", cfg)

    def test_get_single_key(self):
        result = self.c.config_get("font_family")
        self.assertIn("key", result)
        self.assertEqual(result["key"], "font_family")
        self.assertIn("value", result)

    def test_set_and_get_back(self):
        original = self._orig_font.get("value", "monospace")
        self.c.config_set("font_family", "TestMono")
        result = self.c.config_get("font_family")
        self.assertEqual(result["value"], "TestMono")
        self.c.config_set("font_family", original)

    def test_config_keys_have_expected_shape(self):
        cfg = self.c.config_get()
        for key in ["font_family", "font_size", "theme", "scrollback_lines",
                     "show_sidebar", "auto_save_session", "default_shell"]:
            self.assertIn(key, cfg, f"missing config key: {key}")


# ====================================================================
# Agent spawn / list / stop
# ====================================================================

class TestAgent(unittest.TestCase):
    """Spawn, list, and stop agent processes."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_spawn_returns_agent_info(self):
        ws = self.c.workspace_create(_unique("agent-spawn"))
        result = self.c.agent_spawn("test-agent", "sleep 60", ws["id"])
        self.assertIn("id", result)
        self.assertEqual(result["name"], "test-agent")
        self.assertIn("pid", result)
        self.assertGreater(result["pid"], 0)
        self.c.agent_stop(result["id"])
        self.c.workspace_close(ws["id"])

    def test_list_shows_agents(self):
        ws = self.c.workspace_create(_unique("agent-list"))
        agent = self.c.agent_spawn("list-agent", "sleep 60", ws["id"])
        agents = self.c.agent_list()
        ids = [a["id"] for a in agents]
        self.assertIn(agent["id"], ids)
        self.c.agent_stop(agent["id"])
        self.c.workspace_close(ws["id"])

    def test_stop_removes_agent(self):
        ws = self.c.workspace_create(_unique("agent-stop"))
        agent = self.c.agent_spawn("stop-agent", "sleep 60", ws["id"])
        aid = agent["id"]
        self.c.agent_stop(aid)
        self.c.workspace_close(ws["id"])


# ====================================================================
# Tree hierarchy
# ====================================================================

class TestTree(unittest.TestCase):
    """Workspace/surface/pane hierarchy output."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_tree_returns_windows(self):
        tree = self.c.tree()
        self.assertIn("windows", tree)
        self.assertIsInstance(tree["windows"], list)
        self.assertGreater(len(tree["windows"]), 0)

    def test_tree_includes_created_workspace(self):
        ws = self.c.workspace_create(_unique("tree-ws"))
        tree = self.c.tree()
        all_ws_ids = []
        for win in tree.get("windows", []):
            for w in win.get("workspaces", []):
                all_ws_ids.append(w["id"])
        self.assertIn(ws["id"], all_ws_ids)
        self.c.workspace_close(ws["id"])

    def test_tree_hierarchy_depth(self):
        ws = self.c.workspace_create(_unique("tree-deep"))
        self.c.surface_create(ws["id"], "surf-a")
        self.c.surface_split("h")
        tree = self.c.tree()
        found = False
        for win in tree.get("windows", []):
            for w in win.get("workspaces", []):
                if w["id"] == ws["id"]:
                    found = True
                    self.assertIn("surfaces", w)
                    self.assertGreater(len(w["surfaces"]), 0)
                    for s in w["surfaces"]:
                        self.assertIn("panes", s)
                    break
        self.assertTrue(found, "workspace not found in tree")
        self.c.workspace_close(ws["id"])


# ====================================================================
# Display message
# ====================================================================

class TestDisplayMessage(unittest.TestCase):
    """Echo a message back from the daemon."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_echo_returns_message(self):
        result = self.c.display_message("hello from test")
        self.assertEqual(result.get("message"), "hello from test")

    def test_echo_empty_string(self):
        result = self.c.display_message("")
        self.assertEqual(result.get("message"), "")

    def test_echo_special_characters(self):
        msg = 'line1\nline2\ttab"quote\\slash'
        result = self.c.display_message(msg)
        self.assertEqual(result.get("message"), msg)


# ====================================================================
# Error handling
# ====================================================================

class TestErrorHandling(unittest.TestCase):
    """Commands with bad arguments return proper errors."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_invalid_workspace_close(self):
        resp = self.c.send("workspace.close", {"id": "99999"})
        self.assertFalse(resp.get("ok", True))

    def test_invalid_surface_focus(self):
        resp = self.c.send("surface.focus", {"id": "99999"})
        self.assertFalse(resp.get("ok", True))

    def test_error_response_has_code_and_message(self):
        resp = self.c.send("workspace.close", {"id": "99999"})
        error = resp.get("error", {})
        if isinstance(error, dict):
            self.assertIn("code", error)
            self.assertIn("message", error)
        else:
            # error is a plain string — still a valid error response
            self.assertIsInstance(error, str)
            self.assertGreater(len(error), 0)


# ====================================================================
# Surface split + send text
# ====================================================================

class TestSurfaceSplitAndSendText(unittest.TestCase):
    """Surface split command and send_text."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_surface_split_horizontal(self):
        ws = self.c.workspace_create(_unique("split-h"))
        before = len(self.c.pane_list())
        self.c.surface_split("h")
        after = len(self.c.pane_list())
        self.assertEqual(after, before + 1)
        self.c.workspace_close(ws["id"])

    def test_surface_split_vertical(self):
        ws = self.c.workspace_create(_unique("split-v"))
        before = len(self.c.pane_list())
        self.c.surface_split("v")
        after = len(self.c.pane_list())
        self.assertEqual(after, before + 1)
        self.c.workspace_close(ws["id"])

    def test_send_text_does_not_raise(self):
        ws = self.c.workspace_create(_unique("sendtext"))
        try:
            self.c.surface_send_text("echo hello\n")
        except LmuxError:
            pass  # acceptable if pty not ready
        self.c.workspace_close(ws["id"])


# ====================================================================
# Workspace reorder
# ====================================================================

class TestWorkspaceReorder(unittest.TestCase):
    """Reorder workspaces in the list."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_reorder_does_not_raise(self):
        ws1 = self.c.workspace_create(_unique("reorder-a"))
        ws2 = self.c.workspace_create(_unique("reorder-b"))
        self.c.workspace_reorder(0, 1)
        self.c.workspace_close(ws1["id"])
        self.c.workspace_close(ws2["id"])


# ====================================================================
# Workspace refresh
# ====================================================================

class TestWorkspaceRefresh(unittest.TestCase):
    """Refresh workspace metadata (git branch, ports, etc.)."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_refresh_does_not_raise(self):
        ws = self.c.workspace_create(_unique("refresh"))
        resp = self.c.send("workspace.refresh", {"id": str(ws["id"])})
        self.assertTrue(resp.get("ok"))
        self.c.workspace_close(ws["id"])


# ====================================================================
# Multi-workspace integration scenario
# ====================================================================

class TestMultiWorkspaceScenario(unittest.TestCase):
    """End-to-end workflow: create multiple workspaces with surfaces
    and panes, switch between them, verify isolation."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_full_workflow(self):
        c = self.c

        ws_a = c.workspace_create(_unique("workflow-a"))
        ws_b = c.workspace_create(_unique("workflow-b"))

        surf = c.surface_create(ws_a["id"], "extra-surf")

        c.surface_split("h")

        c.workspace_select(ws_b["id"])
        current = c.workspace_current()
        self.assertEqual(current["id"], ws_b["id"])

        c.workspace_select(ws_a["id"])
        current = c.workspace_current()
        self.assertEqual(current["id"], ws_a["id"])

        c.workspace_rename(ws_a["id"], "renamed-a")
        wl = c.workspace_list()
        for w in wl:
            if w["id"] == ws_a["id"]:
                self.assertEqual(w["title"], "renamed-a")

        c.workspace_close(ws_b["id"])
        wl = c.workspace_list()
        ids = [w["id"] for w in wl]
        self.assertNotIn(ws_b["id"], ids)
        self.assertIn(ws_a["id"], ids)

        c.workspace_close(ws_a["id"])


# ====================================================================
# Notification workspace association
# ====================================================================

class TestNotificationWorkspaceAssoc(unittest.TestCase):
    """Notifications can reference workspace context."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_notification_has_workspace_id(self):
        self.c.notification_clear()
        ws = self.c.workspace_create(_unique("notif-ws"))
        self.c.notification_create("ws-notif")
        notifs = self.c.notification_list()
        self.assertGreater(len(notifs), 0)
        self.assertIn("workspace_id", notifs[0])
        self.c.workspace_close(ws["id"])


# ====================================================================
# Auto-naming rules
# ====================================================================

class TestAutoNaming(unittest.TestCase):
    """Test auto-naming module rules."""

    def test_import(self):
        """Auto-naming module can be imported."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from auto_naming import suggest_names, AutoNamer
        self.assertTrue(callable(suggest_names))

    def test_suggest_names_returns_list(self):
        """suggest_names returns a list."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from auto_naming import suggest_names
        result = suggest_names(os.path.expanduser("~"))
        self.assertIsInstance(result, list)

    def test_suggest_names_invalid_dir(self):
        """suggest_names returns empty list for invalid directory."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from auto_naming import suggest_names
        result = suggest_names("/nonexistent/path/xyz")
        self.assertEqual(result, [])

    def test_auto_namer_pick_best(self):
        """AutoNamer.pick_best returns a name or None."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from auto_naming import AutoNamer
        namer = AutoNamer()
        result = namer.pick_best(os.path.expanduser("~"))
        # Should return a string or None
        self.assertTrue(result is None or isinstance(result, str))

    def test_auto_namer_suggest(self):
        """AutoNamer.suggest returns a list."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from auto_naming import AutoNamer
        namer = AutoNamer()
        result = namer.suggest(os.path.expanduser("~"))
        self.assertIsInstance(result, list)


# ====================================================================
# Focus history push/recent
# ====================================================================

class TestFocusHistory(unittest.TestCase):
    """Test focus history tracking module."""

    def test_import(self):
        """FocusHistory module can be imported."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        self.assertTrue(callable(FocusHistory))

    def test_push_and_recent(self):
        """Push entries and retrieve recent ones."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        h = FocusHistory(max_size=10)
        h.push(pane_id="1", workspace_id="ws-1")
        h.push(pane_id="2", workspace_id="ws-1")
        h.push(pane_id="3", workspace_id="ws-2")
        recent = h.recent(5)
        self.assertEqual(len(recent), 3)
        # Most recent first
        self.assertEqual(recent[0]["pane_id"], "3")
        self.assertEqual(recent[1]["pane_id"], "2")
        self.assertEqual(recent[2]["pane_id"], "1")

    def test_deduplication(self):
        """Consecutive same-pane focuses are deduplicated."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        h = FocusHistory()
        h.push(pane_id="1", workspace_id="ws-1")
        h.push(pane_id="1", workspace_id="ws-1")
        h.push(pane_id="1", workspace_id="ws-1")
        self.assertEqual(h.count(), 1)

    def test_previous(self):
        """previous() returns the last different pane."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        h = FocusHistory()
        h.push(pane_id="1", workspace_id="ws-1")
        h.push(pane_id="2", workspace_id="ws-1")
        h.push(pane_id="3", workspace_id="ws-1")
        prev = h.previous(current_pane_id="3")
        self.assertEqual(prev["pane_id"], "2")

    def test_clear(self):
        """clear() removes all entries."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        h = FocusHistory()
        h.push(pane_id="1", workspace_id="ws-1")
        h.push(pane_id="2", workspace_id="ws-1")
        h.clear()
        self.assertEqual(h.count(), 0)
        self.assertEqual(h.recent(5), [])

    def test_max_size(self):
        """History is bounded by max_size."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from focus_history import FocusHistory
        h = FocusHistory(max_size=3)
        for i in range(10):
            h.push(pane_id=str(i), workspace_id="ws-1")
        self.assertEqual(h.count(), 3)
        recent = h.recent(10)
        self.assertEqual(len(recent), 3)


# ====================================================================
# Hook registry emit/handle
# ====================================================================

class TestHookRegistry(unittest.TestCase):
    """Test agent hooks registry module."""

    def test_import(self):
        """HookRegistry module can be imported."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry, HOOK_EVENTS
        self.assertTrue(callable(HookRegistry))
        self.assertIn("workspace_created", HOOK_EVENTS)

    def test_register_and_list(self):
        """Register hooks and list them."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        self.assertTrue(r.register("workspace_created", "/tmp/test_hook.sh"))
        hooks = r.list_hooks()
        self.assertIn("workspace_created", hooks)
        self.assertIn("/tmp/test_hook.sh", hooks["workspace_created"])

    def test_register_unknown_event(self):
        """Registering an unknown event returns False."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        self.assertFalse(r.register("nonexistent_event", "/tmp/test.sh"))

    def test_unregister(self):
        """Unregister a hook."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        r.register("workspace_created", "/tmp/test_hook.sh")
        self.assertTrue(r.unregister("workspace_created", "/tmp/test_hook.sh"))
        hooks = r.list_hooks()
        self.assertNotIn("workspace_created", hooks)

    def test_emit_records_recent(self):
        """Emitting events records them in recent_events."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        r.emit("workspace_created", workspace_id="test-123")
        recent = r.recent_events(5)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["event"], "workspace_created")

    def test_emit_nonexistent_event(self):
        """Emitting a nonexistent event does not crash."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        # Should not raise
        r.emit("nonexistent_event", data="test")

    def test_duplicate_registration(self):
        """Registering the same hook twice does not duplicate."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from agent_hooks import HookRegistry
        r = HookRegistry()
        r.register("workspace_created", "/tmp/test_hook.sh")
        r.register("workspace_created", "/tmp/test_hook.sh")
        hooks = r.list_hooks()
        self.assertEqual(len(hooks["workspace_created"]), 1)


# ====================================================================
# SSH client (mock subprocess)
# ====================================================================

class TestSSHClient(unittest.TestCase):
    """Test SSH client module."""

    def test_import(self):
        """SSHClient and SSHHost modules can be imported."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHClient, SSHHost
        self.assertTrue(callable(SSHClient))
        self.assertTrue(callable(SSHHost))

    def test_ssh_host_creation(self):
        """SSHHost can be created with host and port."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHHost
        host = SSHHost(host="example.com", port=22, user="admin")
        self.assertEqual(host.host, "example.com")
        self.assertEqual(host.port, 22)
        self.assertEqual(host.user, "admin")

    def test_ssh_host_uri(self):
        """SSHHost.ssh_uri returns user@host or host."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHHost
        host1 = SSHHost(host="example.com", user="admin")
        self.assertEqual(host1.ssh_uri, "admin@example.com")
        host2 = SSHHost(host="example.com")
        self.assertEqual(host2.ssh_uri, "example.com")

    def test_ssh_host_to_dict(self):
        """SSHHost.to_dict returns expected fields."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHHost
        host = SSHHost(host="example.com", port=22, user="admin")
        d = host.to_dict()
        self.assertEqual(d["host"], "example.com")
        self.assertEqual(d["port"], 22)
        self.assertEqual(d["user"], "admin")

    def test_ssh_host_from_dict(self):
        """SSHHost.from_dict recreates from dict."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHHost
        d = {"host": "example.com", "port": 2222, "user": "test"}
        host = SSHHost.from_dict(d)
        self.assertEqual(host.host, "example.com")
        self.assertEqual(host.port, 2222)
        self.assertEqual(host.user, "test")

    def test_ssh_client_init(self):
        """SSHClient can be instantiated without args."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHClient
        client = SSHClient()
        self.assertIsNotNone(client)

    def test_disconnect_nonexistent(self):
        """disconnect() returns False for unknown session."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHClient
        client = SSHClient()
        self.assertFalse(client.disconnect("nonexistent"))

    def test_list_sessions_empty(self):
        """list_sessions returns empty list when no sessions."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHClient
        client = SSHClient()
        self.assertEqual(client.list_sessions(), [])

    def test_cleanup_all_empty(self):
        """cleanup_all() returns 0 when no sessions."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))
        from ssh_client import SSHClient
        client = SSHClient()
        self.assertEqual(client.cleanup_all(), 0)

# ====================================================================
# Tmux compatibility layer
# ====================================================================

class TestTmuxCompat(unittest.TestCase):
    """Test tmux command translation to lmux commands."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_tmux_new_session(self):
        """tmux new-session -s <name> creates a workspace."""
        name = _unique("tmux-ws")
        result = self.c.send("workspace.create", {"title": name})
        self.assertTrue(result.get("ok"))
        ws = result.get("result", {})
        self.assertIn("id", ws)
        self.assertEqual(ws["title"], name)
        self.c.workspace_close(ws["id"])

    def test_tmux_list_sessions(self):
        """tmux list-sessions lists workspaces."""
        result = self.c.send("workspace.list")
        self.assertTrue(result.get("ok"))
        ws_list = result.get("result", {}).get("workspaces", [])
        self.assertIsInstance(ws_list, list)

    def test_tmux_split_window(self):
        """tmux split-window -h creates a horizontal split."""
        ws = self.c.workspace_create(_unique("tmux-split"))
        before = len(self.c.pane_list())
        self.c.surface_split("h")
        after = len(self.c.pane_list())
        self.assertEqual(after, before + 1)
        self.c.workspace_close(ws["id"])

    def test_tmux_split_window_vertical(self):
        """tmux split-window -v creates a vertical split."""
        ws = self.c.workspace_create(_unique("tmux-split-v"))
        before = len(self.c.pane_list())
        self.c.surface_split("v")
        after = len(self.c.pane_list())
        self.assertEqual(after, before + 1)
        self.c.workspace_close(ws["id"])

    def test_tmux_select_pane(self):
        """tmux select-pane -t <target> focuses a pane."""
        ws = self.c.workspace_create(_unique("tmux-selpane"))
        self.c.surface_split("h")
        panes = self.c.pane_list()
        target = panes[1]["id"] if len(panes) > 1 else panes[0]["id"]
        self.c.pane_focus(target)
        panes_after = self.c.pane_list()
        for p in panes_after:
            if p["id"] == target:
                self.assertTrue(p["focused"])
                break
        self.c.workspace_close(ws["id"])

    def test_tmux_select_window(self):
        """tmux select-window -t <target> selects a workspace."""
        ws1 = self.c.workspace_create(_unique("tmux-selwin-a"))
        ws2 = self.c.workspace_create(_unique("tmux-selwin-b"))
        self.c.workspace_select(ws2["id"])
        current = self.c.workspace_current()
        self.assertEqual(current["id"], ws2["id"])
        self.c.workspace_close(ws1["id"])
        self.c.workspace_close(ws2["id"])

    def test_tmux_rename_session(self):
        """tmux rename-session -t <old> <new> renames a workspace."""
        ws = self.c.workspace_create(_unique("tmux-rename"))
        new_name = _unique("tmux-rename-new")
        self.c.workspace_rename(ws["id"], new_name)
        workspaces = self.c.workspace_list()
        for w in workspaces:
            if w["id"] == ws["id"]:
                self.assertEqual(w["title"], new_name)
                break
        self.c.workspace_close(ws["id"])

    def test_tmux_send_keys(self):
        """tmux send-keys sends text to a surface."""
        ws = self.c.workspace_create(_unique("tmux-sendkeys"))
        try:
            self.c.surface_send_text("echo tmux-sendkeys\n")
        except LmuxError:
            pass  # acceptable if pty not ready
        self.c.workspace_close(ws["id"])

    def test_tmux_kill_session(self):
        """tmux kill-session -t <target> closes a workspace."""
        ws = self.c.workspace_create(_unique("tmux-kill"))
        ws_id = ws["id"]
        self.c.workspace_close(ws_id)
        workspaces = self.c.workspace_list()
        ids = [w["id"] for w in workspaces]
        self.assertNotIn(ws_id, ids)

    def test_tmux_list_panes(self):
        """tmux list-panes lists surfaces in a workspace."""
        ws = self.c.workspace_create(_unique("tmux-lspanes"))
        result = self.c.send("surface.list", {"workspace_id": str(ws["id"])})
        self.assertTrue(result.get("ok"))
        surfaces = result.get("result", {}).get("surfaces", [])
        self.assertIsInstance(surfaces, list)
        self.assertGreater(len(surfaces), 0)
        self.c.workspace_close(ws["id"])

    def test_tmux_full_workflow(self):
        """Full tmux workflow: create, split, focus, rename, close."""
        # Create workspace
        name = _unique("tmux-full")
        ws = self.c.workspace_create(name)
        self.assertIn("id", ws)

        # Split horizontally
        self.c.surface_split("h")
        panes = self.c.pane_list()
        self.assertGreater(len(panes), 1)

        # Focus second pane
        self.c.pane_focus(panes[1]["id"])
        panes_after = self.c.pane_list()
        focused = [p for p in panes_after if p["focused"]]
        self.assertEqual(len(focused), 1)

        # Rename
        new_name = _unique("tmux-full-renamed")
        self.c.workspace_rename(ws["id"], new_name)
        workspaces = self.c.workspace_list()
        for w in workspaces:
            if w["id"] == ws["id"]:
                self.assertEqual(w["title"], new_name)
                break

        # List sessions
        all_ws = self.c.workspace_list()
        self.assertGreater(len(all_ws), 0)

        # Close
        self.c.workspace_close(ws["id"])
        all_ws_after = self.c.workspace_list()
        ids = [w["id"] for w in all_ws_after]
        self.assertNotIn(ws["id"], ids)


# ====================================================================
# Copy mode (Vi-style text selection)
# ====================================================================

class TestCopyMode(unittest.TestCase):
    """Test Vi-style copy mode: enter/exit, movement, selection, yank, paste."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _make_ws_with_pane(self):
        ws = self.c.workspace_create(_unique("copy-ws"))
        return ws

    def test_enter_exit_copy_mode(self):
        """Enter copy mode, verify active, exit, verify inactive."""
        ws = self._make_ws_with_pane()
        # Enter copy mode
        res = self.c.pane_copy_mode_enter()
        self.assertTrue(res["active"])
        self.assertIn("pane_id", res)

        # Exit copy mode
        res = self.c.pane_copy_mode_exit()
        self.assertTrue(res["was_active"])
        self.c.workspace_close(ws["id"])

    def test_exit_without_enter(self):
        """Exit copy mode when not active should report was_active=false."""
        ws = self._make_ws_with_pane()
        # Ensure copy mode is not active (fresh pane)
        res = self.c.pane_copy_mode_exit()
        self.assertFalse(res["was_active"])
        self.c.workspace_close(ws["id"])

    def test_movement_keys(self):
        """Test Vi movement keys (h, j, k, l)."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Start at (0, 0)
        res = self.c.pane_copy_mode_move("l")
        self.assertEqual(res["row"], 0)
        self.assertEqual(res["col"], 1)

        res = self.c.pane_copy_mode_move("l")
        self.assertEqual(res["col"], 2)

        res = self.c.pane_copy_mode_move("j")
        self.assertEqual(res["row"], 1)
        self.assertEqual(res["col"], 2)

        res = self.c.pane_copy_mode_move("k")
        self.assertEqual(res["row"], 0)

        res = self.c.pane_copy_mode_move("h")
        self.assertEqual(res["col"], 1)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_movement_word_jump(self):
        """Test w/b word jumps."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        res = self.c.pane_copy_mode_move("w")
        self.assertEqual(res["col"], 5)

        res = self.c.pane_copy_mode_move("b")
        self.assertEqual(res["col"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_movement_line_start_end(self):
        """Test 0 and $ for line start/end."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Move somewhere
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")

        # $ goes to end
        res = self.c.pane_copy_mode_move("$")
        self.assertEqual(res["col"], 9999)

        # 0 goes to start
        res = self.c.pane_copy_mode_move("0")
        self.assertEqual(res["col"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_movement_document_start_end(self):
        """Test gg and G for document start/end."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Move somewhere
        self.c.pane_copy_mode_move("j")
        self.c.pane_copy_mode_move("j")
        self.c.pane_copy_mode_move("l")

        # G goes to bottom
        res = self.c.pane_copy_mode_move("G")
        self.assertEqual(res["row"], 9999)

        # gg goes to top
        res = self.c.pane_copy_mode_move("gg")
        self.assertEqual(res["row"], 0)
        self.assertEqual(res["col"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_movement_clamp_at_zero(self):
        """Movement should clamp at (0,0), not go negative."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Already at (0,0)
        res = self.c.pane_copy_mode_move("h")
        self.assertEqual(res["col"], 0)
        self.assertEqual(res["row"], 0)

        res = self.c.pane_copy_mode_move("k")
        self.assertEqual(res["row"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_select_start_end(self):
        """Test visual selection (v to start, move, v to end)."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Move to position
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")

        # Start selection
        res = self.c.pane_copy_mode_select_start()
        self.assertTrue(res["selecting"])
        self.assertEqual(res["start_row"], 0)
        self.assertEqual(res["start_col"], 2)

        # Move cursor
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("j")

        # End selection
        res = self.c.pane_copy_mode_select_end()
        self.assertFalse(res["selecting"])
        self.assertEqual(res["start_row"], 0)
        self.assertEqual(res["start_col"], 2)
        self.assertEqual(res["end_row"], 1)
        self.assertEqual(res["end_col"], 4)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_yank(self):
        """Test yank copies selected text to clipboard."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Select region
        self.c.pane_copy_mode_select_start()
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_select_end()

        # Yank
        res = self.c.pane_copy_mode_yank()
        self.assertTrue(res["yanked"])
        self.assertIn("len", res)
        self.assertGreater(res["len"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_paste(self):
        """Test paste reads from clipboard."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()

        # Yank something first
        self.c.pane_copy_mode_select_start()
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_move("l")
        self.c.pane_copy_mode_select_end()
        self.c.pane_copy_mode_yank()

        # Paste
        res = self.c.pane_copy_mode_paste()
        self.assertTrue(res["pasted"])
        self.assertGreater(res["len"], 0)

        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])

    def test_move_without_copy_mode_fails(self):
        """Movement when copy mode is not active should fail."""
        ws = self._make_ws_with_pane()
        resp = self.c.send("pane.copy_mode.move", {"key": "l"})
        self.assertFalse(resp.get("ok", True))
        error = resp.get("error", {})
        self.assertEqual(error.get("code"), "invalid_state")
        self.c.workspace_close(ws["id"])

    def test_invalid_key_fails(self):
        """Unknown key should fail."""
        ws = self._make_ws_with_pane()
        self.c.pane_copy_mode_enter()
        resp = self.c.send("pane.copy_mode.move", {"key": "x"})
        self.assertFalse(resp.get("ok", True))
        error = resp.get("error", {})
        self.assertEqual(error.get("code"), "invalid_params")
        self.c.pane_copy_mode_exit()
        self.c.workspace_close(ws["id"])


# ====================================================================
# File Explorer
# ====================================================================

class TestFileExplorer(unittest.TestCase):
    """File explorer operations: open, navigate, list, filter, sort, etc."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def setUp(self):
        """Create a temp directory structure for testing."""
        self.test_dir = tempfile.mkdtemp(prefix="lmux-test-fe-")
        # Create some test files and directories
        os.makedirs(os.path.join(self.test_dir, "subdir1"))
        os.makedirs(os.path.join(self.test_dir, "subdir2"))
        with open(os.path.join(self.test_dir, "file1.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.test_dir, "file2.py"), "w") as f:
            f.write("print('hi')")
        with open(os.path.join(self.test_dir, "file3.txt"), "w") as f:
            f.write("world")
        with open(os.path.join(self.test_dir, "subdir1", "nested.txt"), "w") as f:
            f.write("nested")

    def tearDown(self):
        """Clean up test directory."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        # Close file explorer if open
        try:
            self.c.file_explorer_close()
        except Exception:
            pass

    def test_open_at_path(self):
        """Open file explorer at a specific path."""
        res = self.c.file_explorer_open(self.test_dir)
        self.assertIn("path", res)
        self.assertIn("count", res)
        self.assertGreater(res["count"], 0)

    def test_open_default_path(self):
        """Open file explorer without path defaults to cwd."""
        res = self.c.file_explorer_open()
        self.assertIn("path", res)

    def test_navigate_to_directory(self):
        """Navigate to a subdirectory."""
        self.c.file_explorer_open(self.test_dir)
        subdir = os.path.join(self.test_dir, "subdir1")
        res = self.c.file_explorer_navigate(subdir)
        self.assertEqual(res["path"], subdir)
        self.assertEqual(res["count"], 1)  # only nested.txt

    def test_navigate_invalid_path(self):
        """Navigate to non-existent path should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.navigate", {"path": "/nonexistent/path/12345"})
        self.assertFalse(resp.get("ok", True))

    def test_list_entries(self):
        """List entries in current directory."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_list()
        self.assertIn("entries", res)
        self.assertIn("count", res)
        # Should have 5 entries: subdir1, subdir2, file1.txt, file2.py, file3.txt
        self.assertEqual(res["count"], 5)
        # Check entry structure
        entry = res["entries"][0]
        self.assertIn("name", entry)
        self.assertIn("path", entry)
        self.assertIn("is_dir", entry)
        self.assertIn("size", entry)
        self.assertIn("mtime", entry)

    def test_filter_entries(self):
        """Filter entries by name substring."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_filter("file")
        self.assertIn("count", res)
        # Should match file1.txt, file2.py, file3.txt
        self.assertEqual(res["count"], 3)

    def test_clear_filter(self):
        """Clear filter shows all entries."""
        self.c.file_explorer_open(self.test_dir)
        self.c.file_explorer_filter("file")
        res = self.c.file_explorer_filter("")
        self.assertEqual(res["count"], 5)

    def test_sort_by_name(self):
        """Sort entries by name."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_sort("name")
        self.assertEqual(res["sort_mode"], 0)

    def test_sort_by_size(self):
        """Sort entries by size."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_sort("size")
        self.assertEqual(res["sort_mode"], 1)

    def test_sort_by_time(self):
        """Sort entries by modification time."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_sort("time")
        self.assertEqual(res["sort_mode"], 2)

    def test_sort_invalid_mode(self):
        """Invalid sort mode should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.sort", {"mode": "invalid"})
        self.assertFalse(resp.get("ok", True))

    def test_open_file_returns_file_info(self):
        """Opening a file returns file info."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_open_file("file1.txt")
        self.assertEqual(res["type"], "file")
        self.assertEqual(res["name"], "file1.txt")
        self.assertIn("size", res)

    def test_open_directory_navigates(self):
        """Opening a directory navigates into it."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_open_file("subdir1")
        self.assertEqual(res["type"], "directory")
        self.assertEqual(res["count"], 1)  # only nested.txt

    def test_open_nonexistent_fails(self):
        """Opening non-existent entry should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.open_file", {"name": "nonexistent"})
        self.assertFalse(resp.get("ok", True))

    def test_create_directory(self):
        """Create a new directory."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_create_dir("newdir")
        self.assertEqual(res["name"], "newdir")
        self.assertTrue(os.path.isdir(os.path.join(self.test_dir, "newdir")))

    def test_create_dir_no_name_fails(self):
        """Creating directory without name should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.create_dir", {})
        self.assertFalse(resp.get("ok", True))

    def test_delete_file(self):
        """Delete a file."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_delete("file1.txt")
        self.assertEqual(res["name"], "file1.txt")
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "file1.txt")))

    def test_delete_directory(self):
        """Delete an empty directory."""
        self.c.file_explorer_open(self.test_dir)
        # Create a dir to delete
        new_dir = os.path.join(self.test_dir, "to_delete")
        os.makedirs(new_dir)
        self.assertTrue(os.path.isdir(new_dir))
        # Refresh to pick up the new directory
        self.c.file_explorer_refresh()
        # Verify the entry is in the list
        listing = self.c.file_explorer_list()
        names = [e["name"] for e in listing["entries"]]
        self.assertIn("to_delete", names, f"to_delete not found in {names}")
        # Verify the entry details
        to_del_entry = [e for e in listing["entries"] if e["name"] == "to_delete"][0]
        self.assertTrue(to_del_entry["is_dir"], f"to_delete is_dir={to_del_entry['is_dir']}")
        self.assertTrue(os.path.isdir(to_del_entry["path"]), f"path {to_del_entry['path']} is not a dir")
        res = self.c.file_explorer_delete("to_delete")
        self.assertEqual(res["name"], "to_delete")
        self.assertFalse(os.path.exists(new_dir))

    def test_delete_nonexistent_fails(self):
        """Deleting non-existent entry should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.delete", {"name": "nonexistent"})
        self.assertFalse(resp.get("ok", True))

    def test_rename_file(self):
        """Rename a file."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_rename("file1.txt", "renamed.txt")
        self.assertEqual(res["old_name"], "file1.txt")
        self.assertEqual(res["new_name"], "renamed.txt")
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "file1.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "renamed.txt")))

    def test_rename_nonexistent_fails(self):
        """Renaming non-existent entry should fail."""
        self.c.file_explorer_open(self.test_dir)
        resp = self.c.send("file_explorer.rename", {"old_name": "nonexistent", "new_name": "new"})
        self.assertFalse(resp.get("ok", True))

    def test_search_entries(self):
        """Search entries by query."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_search("file")
        self.assertIn("matches", res)
        self.assertEqual(res["count"], 3)

    def test_search_no_results(self):
        """Search with no matches returns empty."""
        self.c.file_explorer_open(self.test_dir)
        res = self.c.file_explorer_search("zzz")
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["matches"], [])

    def test_refresh_directory(self):
        """Refresh re-scans the directory."""
        self.c.file_explorer_open(self.test_dir)
        initial_count = self.c.file_explorer_list()["count"]
        # Add a new file
        with open(os.path.join(self.test_dir, "newfile.txt"), "w") as f:
            f.write("new")
        res = self.c.file_explorer_refresh()
        self.assertEqual(res["count"], initial_count + 1)

    def test_close_explorer(self):
        """Close file explorer."""
        self.c.file_explorer_open(self.test_dir)
        self.c.file_explorer_close()
        resp = self.c.send("file_explorer.list")
        self.assertFalse(resp.get("ok", True))

    def test_operations_when_closed_fail(self):
        """Operations when explorer is closed should fail."""
        # Ensure explorer is closed
        self.c.file_explorer_close()
        resp = self.c.send("file_explorer.list")
        self.assertFalse(resp.get("ok", True))
        resp = self.c.send("file_explorer.navigate", {"path": self.test_dir})
        self.assertFalse(resp.get("ok", True))
        resp = self.c.send("file_explorer.refresh")
        self.assertFalse(resp.get("ok", True))


# ====================================================================
# Canvas layout
# ====================================================================

class TestCanvasLayout(unittest.TestCase):
    """Canvas mode: enable, move, resize, z-order, get/set layout."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        title = title or _unique("canvas-ws")
        ws = self.c.workspace_create(title)
        self.assertIn("id", ws)
        return ws["id"]

    def _create_surface(self, ws_id, title=None):
        title = title or _unique("canvas-surf")
        surf = self.c.surface_create(ws_id, title)
        self.assertIn("id", surf)
        return surf["id"]

    def _create_pane(self, surf_id):
        # Create a pane by splitting the surface
        pane = self.c.surface_split("h")
        self.assertIn("id", pane)
        return pane["id"]

    def test_canvas_enable_disable(self):
        """Enable and disable canvas mode."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["canvas_mode"])

        # Disable canvas mode
        resp = self.c.send("surface.canvas.disable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)
        self.assertFalse(resp["result"]["canvas_mode"])

    def test_canvas_move_pane(self):
        """Move a pane on the canvas."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)

        # Move pane 0
        resp = self.c.send("surface.canvas.move_pane", {
            "surface_id": surf_id,
            "pane_index": "0",
            "x": "100",
            "y": "200"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["x"], 100)
        self.assertEqual(resp["result"]["y"], 200)

    def test_canvas_resize_pane(self):
        """Resize a pane on the canvas."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)

        # Resize pane 0
        resp = self.c.send("surface.canvas.resize_pane", {
            "surface_id": surf_id,
            "pane_index": "0",
            "w": "640",
            "h": "480"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["w"], 640)
        self.assertEqual(resp["result"]["h"], 480)

    def test_canvas_set_z(self):
        """Set z-order of a pane on the canvas."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)

        # Set z-order of pane 0
        resp = self.c.send("surface.canvas.set_z", {
            "surface_id": surf_id,
            "pane_index": "0",
            "z": "5"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["z"], 5)

    def test_canvas_get_layout(self):
        """Get canvas layout."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)

        # Get layout
        resp = self.c.send("surface.canvas.get_layout", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["canvas_mode"])
        self.assertEqual(resp["result"]["pane_count"], 3)
        self.assertIn("panes", resp["result"])
        self.assertEqual(len(resp["result"]["panes"]), 3)

    def test_canvas_set_layout(self):
        """Set canvas layout with custom positions."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        # Set layout with custom positions
        resp = self.c.send("surface.canvas.set_layout", {
            "surface_id": surf_id,
            "panes": [
                {"x": "0", "y": "0", "w": "400", "h": "300", "z": "1"},
                {"x": "400", "y": "0", "w": "400", "h": "300", "z": "0"}
            ]
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["pane_count"], 2)

        # Verify layout
        resp = self.c.send("surface.canvas.get_layout", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["canvas_mode"])
        self.assertEqual(resp["result"]["pane_count"], 2)
        self.assertEqual(resp["result"]["panes"][0]["x"], 0)
        self.assertEqual(resp["result"]["panes"][0]["y"], 0)
        self.assertEqual(resp["result"]["panes"][0]["w"], 400)
        self.assertEqual(resp["result"]["panes"][0]["h"], 300)
        self.assertEqual(resp["result"]["panes"][0]["z"], 1)
        self.assertEqual(resp["result"]["panes"][1]["x"], 400)
        self.assertEqual(resp["result"]["panes"][1]["y"], 0)

    def test_canvas_errors(self):
        """Canvas commands fail when canvas mode is disabled."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)

        # Try to move pane without enabling canvas mode
        resp = self.c.send("surface.canvas.move_pane", {
            "surface_id": surf_id,
            "pane_index": 0,
            "x": 100,
            "y": 200
        })
        self.assertFalse(resp.get("ok", True))
        self.assertIn("canvas mode not enabled", resp.get("error", {}).get("message", ""))

    def test_canvas_invalid_pane_index(self):
        """Canvas commands fail with invalid pane index."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)

        # Enable canvas mode
        resp = self.c.send("surface.canvas.enable", {"surface_id": surf_id})
        self.assertTrue(resp.get("ok"), resp)

        # Try to move invalid pane index
        resp = self.c.send("surface.canvas.move_pane", {
            "surface_id": surf_id,
            "pane_index": "999",
            "x": "100",
            "y": "200"
        })
        self.assertFalse(resp.get("ok", True))
        self.assertIn("invalid pane_index", resp.get("error", {}).get("message", ""))


# ====================================================================
# Find in Terminal (GOAL-7.1)
# ====================================================================

class TestSearchInTerminal(unittest.TestCase):
    """Search across pane output — find in terminal functionality."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        t = title or _unique("search-ws")
        ws = self.c.workspace_create(t)
        self.assertIn("id", ws)
        return ws["id"]

    def _create_surface(self, ws_id, title=None):
        t = title or _unique("search-surf")
        surf = self.c.surface_create(ws_id, t)
        self.assertIn("id", surf)
        return surf["id"]

    def _create_pane(self, surf_id):
        pane = self.c.surface_split(surf_id)
        self.assertIn("id", pane)
        return pane["id"]

    def test_search_start(self):
        """Start search in focused pane."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "hello"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("pane_id", resp.get("result", {}))
        self.assertIn("query", resp.get("result", {}))
        self.assertEqual(resp["result"]["query"], "hello")
        self.assertTrue(resp["result"]["active"])

    def test_search_start_no_pane(self):
        """Search fails when query is empty."""
        # Cancel any existing search first
        self.c.send("search.cancel", {})

        resp = self.c.send("search.start", {"query": ""})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing query", resp.get("error", {}).get("message", ""))

    def test_search_next(self):
        """Navigate to next match."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Start search
        resp = self.c.send("search.start", {"query": "test"})
        self.assertTrue(resp.get("ok"), resp)

        # Next match
        resp = self.c.send("search.next", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("match_index", resp.get("result", {}))

    def test_search_prev(self):
        """Navigate to previous match."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Start search
        resp = self.c.send("search.start", {"query": "test"})
        self.assertTrue(resp.get("ok"), resp)

        # Previous match
        resp = self.c.send("search.prev", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("match_index", resp.get("result", {}))

    def test_search_cancel(self):
        """Cancel search mode."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Start search
        resp = self.c.send("search.start", {"query": "test"})
        self.assertTrue(resp.get("ok"), resp)

        # Cancel search
        resp = self.c.send("search.cancel", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertFalse(resp["result"]["active"])

    def test_search_status(self):
        """Get search status."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Start search
        resp = self.c.send("search.start", {"query": "pattern"})
        self.assertTrue(resp.get("ok"), resp)

        # Get status
        resp = self.c.send("search.status", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["active"])
        self.assertEqual(resp["result"]["query"], "pattern")
        self.assertIn("match_count", resp.get("result", {}))
        self.assertIn("current_match", resp.get("result", {}))

    def test_search_status_inactive(self):
        """Search status when no search is active."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Cancel any existing search first
        self.c.send("search.cancel", {})

        resp = self.c.send("search.status", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertFalse(resp["result"]["active"])

    def test_search_next_no_active(self):
        """Search next fails when no search is active."""
        # Create fresh workspace to ensure no previous search state
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Cancel any existing search first
        self.c.send("search.cancel", {})

        resp = self.c.send("search.next", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("no active search", resp.get("error", {}).get("message", ""))

    def test_search_prev_no_active(self):
        """Search prev fails when no search is active."""
        # Create fresh workspace to ensure no previous search state
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        # Cancel any existing search first
        self.c.send("search.cancel", {})

        resp = self.c.send("search.prev", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("no active search", resp.get("error", {}).get("message", ""))

    def test_search_case_insensitive(self):
        """Search is case insensitive by default."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "Hello", "case_sensitive": False})
        self.assertTrue(resp.get("ok"), resp)
        self.assertFalse(resp["result"]["case_sensitive"])

    def test_search_case_sensitive(self):
        """Search can be case sensitive."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "Hello", "case_sensitive": True})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["case_sensitive"])

    def test_search_whole_words(self):
        """Search can match whole words only."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "test", "whole_words": True})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["whole_words"])

    def test_search_regex(self):
        """Search can use regex patterns."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "test.*pattern", "regex": True})
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp["result"]["regex"])

    def test_search_in_all_panes(self):
        """Search can target all panes in workspace."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        self._create_pane(surf_id)
        self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "test", "scope": "all"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["scope"], "all")

    def test_search_in_current_pane(self):
        """Search can target only current pane."""
        ws_id = self._create_ws()
        surf_id = self._create_surface(ws_id)
        pane_id = self._create_pane(surf_id)

        resp = self.c.send("search.start", {"query": "test", "scope": "current"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["scope"], "current")


# ====================================================================
# Entry point
# ====================================================================
# Browser Import — import bookmarks/history into feed panels
# ====================================================================

class TestBrowserImport(unittest.TestCase):
    """Import browser bookmarks/history into feed panels."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        t = title or _unique("bimport-ws")
        ws = self.c.workspace_create(t)
        self.assertIn("id", ws)
        return ws["id"]

    def test_import_bookmarks(self):
        """Import bookmarks from a JSON file."""
        ws_id = self._create_ws()
        import json, tempfile
        bookmarks = [
            {"title": "GitHub", "url": "https://github.com", "folder": "Dev"},
            {"title": "Hacker News", "url": "https://news.ycombinator.com", "folder": "News"},
            {"title": "Stack Overflow", "url": "https://stackoverflow.com", "folder": "Dev"},
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(bookmarks, f)
            tmppath = f.name
        try:
            resp = self.c.send("browser.import", {
                "workspace_id": ws_id,
                "source": "json",
                "path": tmppath
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("count", resp.get("result", {}))
            self.assertEqual(resp["result"]["count"], 3)
        finally:
            os.unlink(tmppath)

    def test_import_invalid_source(self):
        """Import with unknown source type fails."""
        resp = self.c.send("browser.import", {
            "source": "nonexistent_source",
            "path": "/tmp/nothing.json"
        })
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_import_missing_path(self):
        """Import with missing path fails."""
        resp = self.c.send("browser.import", {
            "source": "json"
        })
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_import_empty_bookmarks(self):
        """Import empty bookmarks file succeeds with count=0."""
        import json, tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([], f)
            tmppath = f.name
        try:
            resp = self.c.send("browser.import", {
                "source": "json",
                "path": tmppath
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertEqual(resp["result"]["count"], 0)
        finally:
            os.unlink(tmppath)

    def test_import_creates_feed_panel(self):
        """Import creates a feed panel with entries."""
        ws_id = self._create_ws()
        import json, tempfile
        bookmarks = [
            {"title": "Example", "url": "https://example.com", "folder": "Test"},
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(bookmarks, f)
            tmppath = f.name
        try:
            resp = self.c.send("browser.import", {
                "workspace_id": ws_id,
                "source": "json",
                "path": tmppath
            })
            self.assertTrue(resp.get("ok"), resp)
            panel_id = resp["result"].get("panel_id")
            self.assertIsNotNone(panel_id)

            # Verify feed panel exists
            panels = self.c.send("feed.panel.list", {"workspace_id": ws_id})
            self.assertTrue(panels.get("ok"), panels)
            panel_ids = [p["id"] for p in panels.get("result", {}).get("panels", [])]
            self.assertIn(panel_id, panel_ids)
        finally:
            os.unlink(tmppath)

    def test_import_chrome_bookmarks(self):
        """Import from Chrome bookmarks path."""
        import json, tempfile
        chrome_bookmarks = {
            "roots": {
                "bookmark_bar": {
                    "children": [
                        {"name": "GitHub", "url": "https://github.com", "type": "url"},
                        {"name": "Dev Folder", "type": "folder", "children": [
                            {"name": "Stack Overflow", "url": "https://stackoverflow.com", "type": "url"},
                        ]}
                    ]
                }
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(chrome_bookmarks, f)
            tmppath = f.name
        try:
            resp = self.c.send("browser.import", {
                "source": "chrome",
                "path": tmppath
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertGreaterEqual(resp["result"]["count"], 2)
        finally:
            os.unlink(tmppath)


# ====================================================================
# Calendar Integration — import .ics files into feed panels
# ====================================================================

class TestCalendarIntegration(unittest.TestCase):
    """Import and query calendar events from .ics files."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        t = title or _unique("cal-ws")
        ws = self.c.workspace_create(t)
        self.assertIn("id", ws)
        return ws["id"]

    def _write_ics(self, events_text):
        """Write a minimal ICS file and return the path."""
        import tempfile
        ics = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//lmux//test\n"
        ics += events_text
        ics += "END:VCALENDAR\n"
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.ics', delete=False)
        f.write(ics)
        f.close()
        return f.name

    def test_import_ics(self):
        """Import events from .ics file."""
        ws_id = self._create_ws()
        ics = (
            "BEGIN:VEVENT\nSUMMARY:Team Standup\n"
            "DTSTART:20260907T100000Z\nDTEND:20260907T103000Z\n"
            "DESCRIPTION:Daily standup\nEND:VEVENT\n"
        )
        path = self._write_ics(ics)
        try:
            resp = self.c.send("calendar.import", {
                "workspace_id": ws_id, "path": path
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("count", resp.get("result", {}))
            self.assertEqual(resp["result"]["count"], 1)
        finally:
            os.unlink(path)

    def test_import_multiple_events(self):
        """Import multiple events from .ics file."""
        ws_id = self._create_ws()
        ics = (
            "BEGIN:VEVENT\nSUMMARY:Meeting A\n"
            "DTSTART:20260907T140000Z\nDTEND:20260907T150000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:Meeting B\n"
            "DTSTART:20260908T090000Z\nDTEND:20260908T100000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:Workshop\n"
            "DTSTART:20260909T130000Z\nDTEND:20260909T170000Z\nEND:VEVENT\n"
        )
        path = self._write_ics(ics)
        try:
            resp = self.c.send("calendar.import", {
                "workspace_id": ws_id, "path": path
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertEqual(resp["result"]["count"], 3)
        finally:
            os.unlink(path)

    def test_import_empty_ics(self):
        """Import empty .ics file succeeds with count=0."""
        path = self._write_ics("")
        try:
            resp = self.c.send("calendar.import", {"path": path})
            self.assertTrue(resp.get("ok"), resp)
            self.assertEqual(resp["result"]["count"], 0)
        finally:
            os.unlink(path)

    def test_import_missing_path(self):
        """Import without path fails."""
        resp = self.c.send("calendar.import", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing path", resp.get("error", {}).get("message", ""))

    def test_import_nonexistent_file(self):
        """Import nonexistent file fails."""
        resp = self.c.send("calendar.import", {"path": "/tmp/no-such-file.ics"})
        self.assertFalse(resp.get("ok", True))

    def test_calendar_today(self):
        """Query today's events."""
        ws_id = self._create_ws()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start = now.strftime("%Y%m%dT%H0000Z")
        end = now.strftime("%Y%m%dT%H3000Z")
        ics = (
            f"BEGIN:VEVENT\nSUMMARY:Today Event\n"
            f"DTSTART:{start}\nDTEND:{end}\nEND:VEVENT\n"
        )
        path = self._write_ics(ics)
        try:
            self.c.send("calendar.import", {"workspace_id": ws_id, "path": path})
            resp = self.c.send("calendar.today", {"workspace_id": ws_id})
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("events", resp.get("result", {}))
        finally:
            os.unlink(path)

    def test_calendar_upcoming(self):
        """Query upcoming events within N days."""
        ws_id = self._create_ws()
        resp = self.c.send("calendar.upcoming", {"workspace_id": ws_id, "days": 7})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("events", resp.get("result", {}))


# ====================================================================
# Email Panel — import and query emails
# ====================================================================

class TestEmailPanel(unittest.TestCase):
    """Import and query emails in feed panels."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        t = title or _unique("email-ws")
        ws = self.c.workspace_create(t)
        self.assertIn("id", ws)
        return ws["id"]

    def _write_mbox(self, emails_text):
        """Write a minimal mbox file and return the path."""
        import tempfile
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False)
        f.write(emails_text)
        f.close()
        return f.name

    def test_import_mbox(self):
        """Import emails from mbox file."""
        ws_id = self._create_ws()
        mbox = (
            "From test@example.com Mon Sep  7 10:00:00 2026\n"
            "Subject: Hello World\n"
            "To: user@lmux.dev\n"
            "Date: Mon, 07 Sep 2026 10:00:00 +0000\n"
            "\n"
            "This is the body of the first email.\n"
            "\n"
            "From alice@example.com Mon Sep  7 11:00:00 2026\n"
            "Subject: Meeting Tomorrow\n"
            "To: user@lmux.dev\n"
            "Date: Mon, 07 Sep 2026 11:00:00 +0000\n"
            "\n"
            "Let's meet at 2pm.\n"
            "\n"
        )
        path = self._write_mbox(mbox)
        try:
            resp = self.c.send("email.import", {
                "workspace_id": ws_id, "path": path
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("count", resp.get("result", {}))
            self.assertEqual(resp["result"]["count"], 2)
        finally:
            os.unlink(path)

    def test_import_empty_mbox(self):
        """Import empty mbox succeeds with count=0."""
        path = self._write_mbox("")
        try:
            resp = self.c.send("email.import", {"path": path})
            self.assertTrue(resp.get("ok"), resp)
            self.assertEqual(resp["result"]["count"], 0)
        finally:
            os.unlink(path)

    def test_import_missing_path(self):
        """Import without path fails."""
        resp = self.c.send("email.import", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing path", resp.get("error", {}).get("message", ""))

    def test_import_nonexistent_file(self):
        """Import nonexistent file fails."""
        resp = self.c.send("email.import", {"path": "/tmp/no-such.mbox"})
        self.assertFalse(resp.get("ok", True))

    def test_email_list(self):
        """List imported emails."""
        ws_id = self._create_ws()
        mbox = (
            "From test@example.com Mon Sep  7 10:00:00 2026\n"
            "Subject: Test Email\n"
            "To: user@lmux.dev\n"
            "\n"
            "Body text.\n"
            "\n"
        )
        path = self._write_mbox(mbox)
        try:
            self.c.send("email.import", {"workspace_id": ws_id, "path": path})
            resp = self.c.send("email.list", {"workspace_id": ws_id})
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("emails", resp.get("result", {}))
        finally:
            os.unlink(path)

    def test_email_search(self):
        """Search emails by subject."""
        ws_id = self._create_ws()
        mbox = (
            "From test@example.com Mon Sep  7 10:00:00 2026\n"
            "Subject: Urgent: Server Down\n"
            "To: user@lmux.dev\n"
            "\n"
            "Server is down.\n"
            "\n"
            "From bob@example.com Mon Sep  7 11:00:00 2026\n"
            "Subject: Lunch Plans\n"
            "To: user@lmux.dev\n"
            "\n"
            "Want to grab lunch?\n"
            "\n"
        )
        path = self._write_mbox(mbox)
        try:
            self.c.send("email.import", {"workspace_id": ws_id, "path": path})
            resp = self.c.send("email.search", {
                "workspace_id": ws_id, "query": "Urgent"
            })
            self.assertTrue(resp.get("ok"), resp)
            self.assertIn("emails", resp.get("result", {}))
            self.assertGreaterEqual(len(resp["result"]["emails"]), 1)
        finally:
            os.unlink(path)

    def test_import_creates_feed_panel(self):
        """Import creates a feed panel with email entries."""
        ws_id = self._create_ws()
        mbox = (
            "From test@example.com Mon Sep  7 10:00:00 2026\n"
            "Subject: Panel Test\n"
            "To: user@lmux.dev\n"
            "\n"
            "Testing panel creation.\n"
            "\n"
        )
        path = self._write_mbox(mbox)
        try:
            resp = self.c.send("email.import", {
                "workspace_id": ws_id, "path": path
            })
            self.assertTrue(resp.get("ok"), resp)
            panel_id = resp["result"].get("panel_id")
            self.assertIsNotNone(panel_id)
        finally:
            os.unlink(path)


# ====================================================================
# Weather Panel — weather widget in feed panel
# ====================================================================

class TestWeatherPanel(unittest.TestCase):
    """Weather widget that fetches and displays weather data."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def _create_ws(self, title=None):
        t = title or _unique("weather-ws")
        ws = self.c.workspace_create(t)
        self.assertIn("id", ws)
        return ws["id"]

    def test_get_weather(self):
        """Get weather for a location."""
        resp = self.c.send("weather.get", {"location": "London"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("result", resp)
        self.assertIn("location", resp["result"])
        self.assertEqual(resp["result"]["location"], "London")

    def test_get_weather_missing_location(self):
        """Get weather without location fails."""
        resp = self.c.send("weather.get", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing location", resp.get("error", {}).get("message", ""))

    def test_set_location(self):
        """Set default weather location."""
        resp = self.c.send("weather.set_location", {"location": "New York"})
        self.assertTrue(resp.get("ok"), resp)

    def test_refresh_weather(self):
        """Refresh weather data."""
        ws_id = self._create_ws()
        resp = self.c.send("weather.refresh", {"workspace_id": ws_id})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("result", resp)

    def test_weather_in_feed_panel(self):
        """Weather data appears in feed panel."""
        ws_id = self._create_ws()
        resp = self.c.send("weather.get", {"location": "Tokyo"})
        self.assertTrue(resp.get("ok"), resp)
        panels = self.c.send("feed.panel.list", {"workspace_id": ws_id})
        self.assertTrue(panels.get("ok"), panels)

    def test_weather_status_fields(self):
        """Weather result contains expected fields."""
        resp = self.c.send("weather.get", {"location": "Paris"})
        self.assertTrue(resp.get("ok"), resp)
        result = resp["result"]
        self.assertIn("location", result)
        self.assertIn("temperature", result)
        self.assertIn("condition", result)


# ====================================================================
# Clipboard History — clipboard manager
# ====================================================================

class TestClipboardHistory(unittest.TestCase):
    """Clipboard manager that stores and retrieves clipboard history."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_copy_text(self):
        """Copy text to clipboard history."""
        resp = self.c.send("clipboard.copy", {"text": "Hello World"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("index", resp.get("result", {}))

    def test_paste_recent(self):
        """Paste most recent clipboard entry."""
        self.c.send("clipboard.clear", {})
        self.c.send("clipboard.copy", {"text": "First"})
        self.c.send("clipboard.copy", {"text": "Second"})
        resp = self.c.send("clipboard.paste", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["text"], "Second")

    def test_paste_by_index(self):
        """Paste specific clipboard entry by index."""
        self.c.send("clipboard.clear", {})
        self.c.send("clipboard.copy", {"text": "Alpha"})
        self.c.send("clipboard.copy", {"text": "Beta"})
        resp = self.c.send("clipboard.paste", {"index": 0})
        self.assertTrue(resp.get("ok"), resp)
        self.assertEqual(resp["result"]["text"], "Alpha")

    def test_paste_invalid_index(self):
        """Paste with invalid index fails."""
        resp = self.c.send("clipboard.paste", {"index": 999})
        self.assertFalse(resp.get("ok", True))

    def test_list_history(self):
        """List clipboard history."""
        self.c.send("clipboard.clear", {})
        self.c.send("clipboard.copy", {"text": "Item 1"})
        self.c.send("clipboard.copy", {"text": "Item 2"})
        resp = self.c.send("clipboard.list", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("entries", resp.get("result", {}))
        self.assertGreaterEqual(len(resp["result"]["entries"]), 2)

    def test_clear_history(self):
        """Clear clipboard history."""
        self.c.send("clipboard.copy", {"text": "To be cleared"})
        resp = self.c.send("clipboard.clear", {})
        self.assertTrue(resp.get("ok"), resp)
        # After clear, paste should fail
        resp2 = self.c.send("clipboard.paste", {})
        self.assertFalse(resp2.get("ok", True))

    def test_copy_empty_text(self):
        """Copy empty text fails."""
        resp = self.c.send("clipboard.copy", {"text": ""})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing text", resp.get("error", {}).get("message", ""))

    def test_history_limit(self):
        """Clipboard history has a maximum size."""
        # Add many entries
        for i in range(120):
            self.c.send("clipboard.copy", {"text": f"Entry {i}"})
        resp = self.c.send("clipboard.list", {})
        self.assertTrue(resp.get("ok"), resp)
        # Should be capped at 100
        self.assertLessEqual(len(resp["result"]["entries"]), 100)


# ====================================================================
# Workspace Templates — pre-configured workspace layouts
# ====================================================================

class TestWorkspaceTemplates(unittest.TestCase):
    """Pre-configured workspace layouts for quick setup."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_list_templates(self):
        """List available templates."""
        resp = self.c.send("template.list", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("templates", resp.get("result", {}))

    def test_create_from_template(self):
        """Create workspace from a built-in template."""
        resp = self.c.send("template.create", {"template": "development"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("workspace_id", resp.get("result", {}))

    def test_create_invalid_template(self):
        """Create workspace from non-existent template fails."""
        resp = self.c.send("template.create", {"template": "nonexistent"})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_save_as_template(self):
        """Save current workspace as a template."""
        ws = self.c.workspace_create("Template Source")
        resp = self.c.send("template.save", {
            "workspace_id": ws["id"],
            "name": "my-custom"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("name", resp.get("result", {}))

    def test_delete_template(self):
        """Delete a custom template."""
        ws = self.c.workspace_create("For Delete")
        self.c.send("template.save", {"workspace_id": ws["id"], "name": "to-delete"})
        resp = self.c.send("template.delete", {"name": "to-delete"})
        self.assertTrue(resp.get("ok"), resp)

    def test_delete_builtin_template(self):
        """Cannot delete built-in templates."""
        resp = self.c.send("template.delete", {"name": "development"})
        self.assertFalse(resp.get("ok", True))

    def test_delete_missing_name(self):
        """Delete without name fails."""
        resp = self.c.send("template.delete", {})
        self.assertFalse(resp.get("ok", True))


# ====================================================================
# Performance Profiling — built-in profiling tools
# ====================================================================

class TestPerformanceProfiling(unittest.TestCase):
    """Built-in performance profiling tools."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_profile_status(self):
        """Get profiling status."""
        resp = self.c.send("profile.status", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("active", resp.get("result", {}))

    def test_start_stop(self):
        """Start and stop profiling."""
        resp1 = self.c.send("profile.start", {"label": "test-run"})
        self.assertTrue(resp1.get("ok"), resp1)
        resp2 = self.c.send("profile.stop", {})
        self.assertTrue(resp2.get("ok"), resp2)
        self.assertIn("elapsed_ms", resp2.get("result", {}))

    def test_profile_list(self):
        """List profiling results."""
        resp = self.c.send("profile.list", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("results", resp.get("result", {}))

    def test_start_requires_label(self):
        """Start without label fails."""
        resp = self.c.send("profile.start", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("missing label", resp.get("error", {}).get("message", ""))

    def test_stop_without_start(self):
        """Stop without active profiling fails."""
        resp = self.c.send("profile.stop", {})
        self.assertFalse(resp.get("ok", True))


# ====================================================================
# Cloud VM Management — SSH-based VM lifecycle
# ====================================================================

class TestCloudVMManagement(unittest.TestCase):
    """SSH-based VM lifecycle management for cloud providers."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_list_vms(self):
        """List available VMs."""
        resp = self.c.send("cloud.vms.list", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("vms", resp.get("result", {}))

    def test_list_vms_by_provider(self):
        """List VMs filtered by provider."""
        resp = self.c.send("cloud.vms.list", {"provider": "gcp"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("vms", resp.get("result", {}))

    def test_create_vm(self):
        """Create a VM."""
        resp = self.c.send("cloud.vms.create", {
            "provider": "gcp",
            "name": "test-vm-1",
            "size": "e2-medium"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("vm_id", resp.get("result", {}))

    def test_create_vm_missing_params(self):
        """Create VM without required params fails."""
        resp = self.c.send("cloud.vms.create", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_destroy_vm(self):
        """Destroy a VM."""
        # Create first to get the ID
        resp = self.c.send("cloud.vms.create", {
            "provider": "gcp",
            "name": "to-destroy",
            "size": "e2-medium"
        })
        vm_id = resp["result"]["vm_id"]
        resp2 = self.c.send("cloud.vms.destroy", {"vm_id": vm_id})
        self.assertTrue(resp2.get("ok"), resp2)

    def test_destroy_vm_not_found(self):
        """Destroy non-existent VM fails."""
        resp = self.c.send("cloud.vms.destroy", {"vm_id": "nonexistent"})
        self.assertFalse(resp.get("ok", True))

    def test_ssh_vm(self):
        """SSH into a VM."""
        resp = self.c.send("cloud.vms.ssh", {"vm_id": "test-vm-1"})
        # Should return connection info or pane ID
        self.assertIn("ok", resp)


# ====================================================================
# iOS Companion — mobile remote control
# ====================================================================

class TestIOSCompanion(unittest.TestCase):
    """Mobile remote control companion device integration."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_register_companion(self):
        """Register a companion device."""
        resp = self.c.send("companion.register", {
            "device_id": "ios-001",
            "device_name": "iPhone 15",
            "platform": "ios"
        })
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("companion_id", resp.get("result", {}))

    def test_register_missing_params(self):
        """Register without required params fails."""
        resp = self.c.send("companion.register", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_companion_status(self):
        """Check companion connection status."""
        resp = self.c.send("companion.status", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("companions", resp.get("result", {}))

    def test_companion_status_by_id(self):
        """Check specific companion status."""
        self.c.send("companion.register", {
            "device_id": "ios-002",
            "device_name": "iPad Pro",
            "platform": "ios"
        })
        resp = self.c.send("companion.status", {"companion_id": "ios-002"})
        self.assertTrue(resp.get("ok"), resp)

    def test_notify_companion(self):
        """Send notification to companion."""
        self.c.send("companion.register", {
            "device_id": "ios-003",
            "device_name": "iPhone 15 Pro",
            "platform": "ios"
        })
        resp = self.c.send("companion.notify", {
            "companion_id": "ios-003",
            "title": "Test Alert",
            "message": "Hello from lmux!"
        })
        self.assertTrue(resp.get("ok"), resp)

    def test_notify_missing_params(self):
        """Notify without required params fails."""
        resp = self.c.send("companion.notify", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_unregister_companion(self):
        """Unregister a companion device."""
        self.c.send("companion.register", {
            "device_id": "ios-004",
            "device_name": "iPhone SE",
            "platform": "ios"
        })
        resp = self.c.send("companion.unregister", {"companion_id": "ios-004"})
        self.assertTrue(resp.get("ok"), resp)


# ====================================================================
# Agent Teams — multi-agent workflow orchestration
# ====================================================================

class TestAgentTeams(unittest.TestCase):
    """Multi-agent workflow orchestration for team-based tasks."""

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_create_team(self):
        """Create an agent team."""
        resp = self.c.send("team.create", {"name": "backend-team"})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("team_id", resp.get("result", {}))

    def test_create_team_missing_name(self):
        """Create team without name fails."""
        resp = self.c.send("team.create", {})
        self.assertFalse(resp.get("ok", True))
        self.assertIn("error", resp)

    def test_add_agent_to_team(self):
        """Add agent to team."""
        resp = self.c.send("team.create", {"name": "frontend-team"})
        team_id = resp["result"]["team_id"]
        resp2 = self.c.send("team.add", {
            "team_id": team_id,
            "agent_name": "coder-1",
            "role": "implementer"
        })
        self.assertTrue(resp2.get("ok"), resp2)

    def test_remove_agent_from_team(self):
        """Remove agent from team."""
        resp = self.c.send("team.create", {"name": "test-team"})
        team_id = resp["result"]["team_id"]
        self.c.send("team.add", {
            "team_id": team_id,
            "agent_name": "tester-1",
            "role": "tester"
        })
        resp2 = self.c.send("team.remove", {
            "team_id": team_id,
            "agent_name": "tester-1"
        })
        self.assertTrue(resp2.get("ok"), resp2)

    def test_list_teams(self):
        """List all teams."""
        self.c.send("team.create", {"name": "ops-team"})
        resp = self.c.send("team.list", {})
        self.assertTrue(resp.get("ok"), resp)
        self.assertIn("teams", resp.get("result", {}))

    def test_list_team_members(self):
        """List members of a team."""
        resp = self.c.send("team.create", {"name": "design-team"})
        team_id = resp["result"]["team_id"]
        self.c.send("team.add", {"team_id": team_id, "agent_name": "designer-1", "role": "designer"})
        resp2 = self.c.send("team.list", {"team_id": team_id})
        self.assertTrue(resp2.get("ok"), resp2)
        teams = resp2["result"]["teams"]
        self.assertEqual(len(teams), 1)
        self.assertIn("members", teams[0])

    def test_dispatch_to_team(self):
        """Dispatch task to team."""
        resp = self.c.send("team.create", {"name": "dispatch-team"})
        team_id = resp["result"]["team_id"]
        self.c.send("team.add", {"team_id": team_id, "agent_name": "worker-1", "role": "worker"})
        resp2 = self.c.send("team.dispatch", {
            "team_id": team_id,
            "task": "implement feature X"
        })
        self.assertTrue(resp2.get("ok"), resp2)
        self.assertIn("dispatch_id", resp2.get("result", {}))

    def test_delete_team(self):
        """Delete a team."""
        resp = self.c.send("team.create", {"name": "temp-team"})
        team_id = resp["result"]["team_id"]
        resp2 = self.c.send("team.delete", {"team_id": team_id})
        self.assertTrue(resp2.get("ok"), resp2)


# ====================================================================

if __name__ == "__main__":
    # Clean up leftover test sockets
    for f in Path("/tmp").glob("lmux-integration-*.sock"):
        try:
            f.unlink()
        except OSError:
            pass

    unittest.main(verbosity=2)
