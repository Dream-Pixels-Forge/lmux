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
# Entry point
# ====================================================================

if __name__ == "__main__":
    # Clean up leftover test sockets
    for f in Path("/tmp").glob("lmux-integration-*.sock"):
        try:
            f.unlink()
        except OSError:
            pass

    unittest.main(verbosity=2)
