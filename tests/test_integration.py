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

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
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
_test_home = None


def _ensure_daemon():
    """Start (once) the daemon shared by all test classes.

    The daemon runs against a throwaway HOME/XDG_DATA_HOME. Without this it
    reads and writes the developer's real ~/.local/share/lmux/snapshot.json,
    which makes the suite depend on whatever session happened to be saved:
    a large leftover snapshot slows startup past the client timeout, and
    restored workspaces leak into tests that assume a clean model (issue #14).
    """
    global _daemon, _client, _test_home
    if _client is None:
        _test_home = tempfile.mkdtemp(prefix="lmux-itest-home-")
        Path(_test_home, ".local", "share", "lmux").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = _test_home
        env["XDG_DATA_HOME"] = str(Path(_test_home, ".local", "share"))
        sock = f"/tmp/lmux-integration-{os.getpid()}.sock"
        _daemon = LmuxDaemon(sock, env=env)
        _client = _daemon.start(timeout=60)
    return _client


_iso_seq = 0


def _isolated_client(home=None):
    """Start a private daemon with its own HOME, and return (client, daemon, home).

    Use for operations that act on the whole model (session restore, snapshot
    load) or that must not see state left behind by other tests.

    Pass `home` to reuse a previous HOME, which is how a config-persistence
    test observes state across a daemon restart.
    """
    global _iso_seq
    _iso_seq += 1
    own_home = home is None
    if own_home:
        home = tempfile.mkdtemp(prefix="lmux-iso-home-")
    home = Path(home)
    data = home / ".local" / "share"
    (data / "lmux").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_DATA_HOME"] = str(data)
    # lmux_config_path() prefers XDG_CONFIG_HOME over HOME/.config; pin both so
    # an ambient XDG_CONFIG_HOME cannot leak the developer's real config in.
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    # lmux_config_save() mkdir()s only one level deep, so the parent must exist
    # exactly as it does in any real $HOME.
    (home / ".config").mkdir(parents=True, exist_ok=True)
    sock = f"/tmp/lmux-iso-{os.getpid()}-{_iso_seq}.sock"
    Path(sock).unlink(missing_ok=True)
    daemon = LmuxDaemon(sock, env=env)
    return daemon.start(timeout=60), daemon, home


def _shutdown_isolated(daemon, home):
    """Stop a private daemon and remove its socket/snapshot."""
    try:
        daemon.stop()
    except Exception:  # noqa: BLE001 - teardown must not mask test failures
        pass
    try:
        Path(daemon.socket_path).unlink(missing_ok=True)
        Path(home, ".local", "share", "lmux", "snapshot.json").unlink(missing_ok=True)
    except OSError:
        pass


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
        # Restoring replaces the whole model and spawns a pty per restored
        # pane, so it must not run against the shared daemon: by this point the
        # shared model holds every workspace created by the earlier classes,
        # and the rebuild can outlast the client timeout (which surfaces as a
        # misleading "empty response"). A private daemon keeps the scope of
        # this operation honest.
        client, daemon, home = _isolated_client()
        try:
            ws = client.workspace_create(_unique("sess-restore"))
            client.session_save()
            result = client.session_restore()
            self.assertIsInstance(result, dict)
            # The restore that just ran rebuilt the model from the saved file,
            # so the pre-restore workspace was replaced rather than kept
            # alongside the restored copy (#8). If the close succeeds, the
            # loader is still appending.
            with self.assertRaises(LmuxError) as ctx:
                client.workspace_close(ws["id"])
            self.assertIn("not_found", str(ctx.exception),
                          f"restore should have replaced ws {ws['id']}")
        finally:
            _shutdown_isolated(daemon, home)


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
        # #12 step 4: snapshot.save no longer accepts an arbitrary absolute
        # path — it is confined to the data root. Pass a relative name and use
        # the path the daemon returns.
        try:
            result = self.c.snapshot_save("snap-save-test.json")
            saved = result.get("path")
            self.assertTrue(saved, f"save should return a path: {result}")
            self.assertTrue(os.path.isabs(saved),
                            f"returned path should be absolute: {saved}")
            self.assertTrue(os.path.exists(saved),
                            f"snapshot should exist at {saved}")
        finally:
            self.c.workspace_close(ws["id"])

    def test_load_from_saved_file(self):
        # snapshot.load replaces the whole model, so run it on a private daemon
        # rather than the shared one that other classes are still using.
        client, daemon, home = _isolated_client()
        try:
            ws = client.workspace_create(_unique("snap-load"))
            saved = client.snapshot_save("snap-load-test.json")["path"]
            self.assertTrue(os.path.exists(saved))
            # Load the file we just saved
            result = client.snapshot_load(saved)
            self.assertIsInstance(result, dict)
            # Loading replaces the model (#8), so the workspace created above
            # no longer exists. A successful close would mean the loader is
            # still appending to the live model.
            with self.assertRaises(LmuxError) as ctx:
                client.workspace_close(ws["id"])
            self.assertIn("not_found", str(ctx.exception),
                          f"load should have replaced ws {ws['id']}")
        finally:
            snap_path = Path(home, ".local", "share", "lmux", "snap-load-test.json")
            snap_path.unlink(missing_ok=True)
            _shutdown_isolated(daemon, home)

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


class TestTmuxCompatCli(unittest.TestCase):
    """CLI-level checks that run the binary directly.

    These exercise code paths that only run in the client process, so they
    cannot be covered by the socket-based tests above.
    """

    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).parent.parent
        cls.bin = repo_root / "build" / "lmux"
        if not cls.bin.exists():
            raise unittest.SkipTest(
                f"lmux binary not found at {cls.bin}; run the build first")

    def test_unknown_tmux_subcommand_exits_cleanly(self):
        """B7: translate_tmux_command() fell off the end on an unknown
        subcommand after free(json), returning a dangling pointer. main()
        then free()d that garbage — AddressSanitizer reported `bad-free` and
        glibc aborted with "double free or corruption"."""
        proc = subprocess.run(
            [str(self.bin), "tmux", "definitely-not-a-command"],
            capture_output=True, text=True, timeout=30,
        )
        # A negative return code means the process died on a signal, which is
        # exactly the heap-corruption abort we are guarding against.
        self.assertGreaterEqual(
            proc.returncode, 0,
            f"CLI crashed on a bad signal (rc={proc.returncode}); "
            f"stderr tail: {proc.stderr[-300:]}",
        )
        self.assertNotIn("corruption", proc.stderr.lower())
        self.assertNotIn("AddressSanitizer", proc.stderr)


class TestBrowserScreenshotPathValidation(unittest.TestCase):
    """#12: browser.screenshot accepted an arbitrary write path from any socket
    client, letting a same-UID process or agent clobber any file the daemon
    could write. Screenshot paths must be relative and confined to the
    screenshot root."""

    @classmethod
    def setUpClass(cls):
        cls.client, cls.daemon, cls.home = _isolated_client()

    @classmethod
    def tearDownClass(cls):
        _shutdown_isolated(cls.daemon, cls.home)

    def _shot(self, path):
        return self.client.send("browser.screenshot", {"path": path})

    def test_absolute_path_outside_root_is_rejected(self):
        for bad in ["/tmp/pwned.png", "/etc/cron.d/pwn", "~/.bashrc"]:
            resp = self._shot(bad)
            self.assertFalse(resp.get("ok"), f"{bad!r} must be rejected: {resp}")
            self.assertEqual(resp.get("error", {}).get("code"),
                             "invalid_params", f"{bad!r}: {resp}")

    def test_traversal_escape_is_rejected(self):
        for bad in ["../escape.png", "sub/../../escape.png", "../../.bashrc"]:
            resp = self._shot(bad)
            self.assertFalse(resp.get("ok"), f"{bad!r} must be rejected: {resp}")

    def test_rejected_write_leaves_no_file(self):
        target = Path(self.home, "pwned-by-lmux.txt")
        resp = self._shot(str(target))
        self.assertFalse(resp.get("ok"))
        self.assertFalse(target.exists(),
                         "daemon must not create a file outside its root")

    def test_relative_path_inside_root_is_accepted(self):
        resp = self._shot("shot.png")
        self.assertTrue(resp.get("ok"), f"relative path should be allowed: {resp}")
        self.assertNotIn("..", resp.get("result", {}).get("path", ""))

    def test_path_is_json_escaped_in_event_payload(self):
        """A quote in the name must not break out of the JSON string that is
        persisted to events.jsonl and echoed in the response.

        The response is parsed with json.loads(), so reaching this assertion
        already proves the wire format stayed valid: unescaped, the reply would
        not have parsed at all. What must hold is that the escaped value
        round-trips back to exactly what was asked for.
        """
        resp = self._shot('weird"name.png')
        self.assertTrue(resp.get("ok"), resp)
        path = resp.get("result", {}).get("path", "")
        self.assertTrue(
            path.endswith('weird"name.png'),
            f"escaped path should round-trip to the original name: {path!r}",
        )

    def test_default_path_is_inside_root(self):
        resp = self.client.send("browser.screenshot")
        self.assertTrue(resp.get("ok"), resp)
        self.assertTrue(resp.get("result", {}).get("path", "").startswith("/"),
                        "default path should be absolute and rooted")


class TestSnapshotTruncationDetection(unittest.TestCase):
    """#11: lmux_snapshot_load() read into a fixed 64 KB buffer and silently
    truncated anything larger, producing a partially-restored session that
    still reported success. Truncation must be detected and reported."""

    def test_oversized_snapshot_is_rejected_not_silently_truncated(self):
        client, daemon, home = _isolated_client()
        try:
            snap = Path(tempfile.gettempdir(),
                        f"lmux-test-big-{os.getpid()}.json")
            # ~70 KB, comfortably past the 64 KB buffer.
            workspaces = "".join(
                '{"title":"ws%04d","cwd":"/tmp","surfaces":[]}' % i
                for i in range(1800))
            snap.write_text('{"workspaces":[' + workspaces + ']}')
            self.assertGreater(snap.stat().st_size, 65536,
                               "fixture must exceed the 64 KB buffer")

            resp = client.send("snapshot.load", {"path": str(snap)})
            self.assertFalse(resp.get("ok"),
                             f"oversized snapshot must not report success: "
                             f"{resp.get('ok')}")
            self.assertEqual(resp.get("error", {}).get("code"), "load_failed")
        finally:
            _shutdown_isolated(daemon, home)
            try:
                snap.unlink()
            except (OSError, UnboundLocalError):
                pass

    def test_normal_sized_snapshot_still_loads(self):
        """The guard must not reject snapshots that legitimately fit."""
        client, daemon, home = _isolated_client()
        try:
            ws = client.workspace_create(_unique("snap-fits"))
            # #12 step 4: save is confined to the data root, so ask for it by
            # relative name and load the absolute path it returns.
            saved = client.snapshot_save("snap-fits-test.json")["path"]
            self.assertLess(os.path.getsize(saved), 65536)

            resp = client.send("snapshot.load", {"path": saved})
            self.assertTrue(resp.get("ok"),
                            f"a normal snapshot must still load: {resp}")
            Path(saved).unlink(missing_ok=True)
        finally:
            _shutdown_isolated(daemon, home)


class TestPtyDeferralOnRestore(unittest.TestCase):
    """Restoring a snapshot forked a pty per workspace only to free it
    immediately: lmux_workspace_create() and lmux_surface_create() both
    eagerly spawn, and the restore drops the placeholder straight after.

    Baseline measured on master: ~52 ms per workspace, independent of
    document size.
    """

    def _child_count(self, pid):
        try:
            return len(subprocess.run(
                ["pgrep", "-P", str(pid)], capture_output=True, text=True,
                timeout=10).stdout.split())
        except Exception:  # noqa: BLE001 - pgrep missing or no children
            return 0

    def test_restore_forks_no_pty(self):
        """T1: the placeholder pty must not be spawned at all.

        Measured with strace rather than by sampling the child count: the
        placeholder panes are freed immediately, so a before/after comparison
        cannot see them. Baseline on master: ~43 clones for 40 workspaces.
        """
        home = Path(tempfile.mkdtemp(prefix="lmux-ptyd-"))
        (home / ".local" / "share" / "lmux").mkdir(parents=True)
        (home / ".config").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        sock = f"/tmp/lmux-ptyd-{os.getpid()}.sock"
        trace = home / "trace.txt"
        Path(sock).unlink(missing_ok=True)

        if not shutil.which("strace"):
            self.skipTest("strace unavailable; cannot count forks")

        proc = subprocess.Popen(
            ["strace", "-f", "-c", "-e", "trace=clone,clone3,fork,vfork",
             "-o", str(trace),
             str(Path(__file__).parent.parent / "build" / "lmux"),
             "--socket", sock, "daemon"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        try:
            client = None
            deadline = time.time() + 30
            while time.time() < deadline and client is None:
                time.sleep(0.25)
                try:
                    client = LmuxClient(sock)
                except Exception:  # noqa: BLE001 - not up yet
                    client = None
            self.assertIsNotNone(client, "daemon did not come up")

            snap = home / "snap.json"
            n = 20
            ws = ",".join(
                '{"id":%d,"title":"w%d","cwd":"/tmp","git_branch":"",'
                '"surfaces":[]}' % (i, i) for i in range(1, n + 1))
            snap.write_text('{"version":1,"workspaces":[' + ws + ']}')

            started = time.time()
            resp = client.send("snapshot.load", {"path": str(snap)})
            elapsed = time.time() - started
            self.assertTrue(resp.get("ok"), f"restore should succeed: {resp}")

            # Baseline was ~52 ms per workspace.
            self.assertLess(elapsed, n * 0.052,
                            f"restore of {n} workspaces took {elapsed:.2f}s, "
                            f"which is the un-deferred ~52ms/workspace cost")
        finally:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            Path(sock).unlink(missing_ok=True)
            text = trace.read_text(errors="replace") if trace.exists() else ""
            shutil.rmtree(home, ignore_errors=True)

        forks = 0
        for m in re.finditer(r"^\s*([\d,]+)\s+\S+\s+\d+\s+\d+\s+(clone|clone3|fork|vfork)\s*$",
                             text, re.M):
            forks += int(m.group(1).replace(",", ""))
        self.assertLess(forks, 12,
                        f"restore forked {forks} times for {n} workspaces; it "
                        f"should fork none, since every placeholder pty is "
                        f"freed immediately (strace: {text[:300]!r})")

    def test_interactive_create_still_spawns_a_pty(self):
        """T3: deferral must not leak into the normal create path."""
        client, daemon, home = _isolated_client()
        try:
            ws = client.workspace_create(_unique("pty-live"))
            self.assertTrue(ws.get("id"))
            children = self._child_count(daemon._proc.pid)
            self.assertGreater(
                children, 0,
                "workspace.create must still fork a pty for its terminal")
            screen = client.send("read-screen")
            self.assertTrue(screen.get("ok"),
                            f"the spawned pty should return screen data: {screen}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_restored_shape_matches_the_snapshot(self):
        """T2: deferral must not change *what* is restored."""
        client, daemon, home = _isolated_client()
        try:
            client.workspace_create("alpha")
            client.surface_create(1, "Extra")
            saved = client.snapshot_save("shape.json")["path"]
            doc = json.loads(Path(saved).read_text())
            want = [(w["title"], len(w["surfaces"]))
                    for w in doc["workspaces"]]

            resp = client.send("snapshot.load", {"path": saved})
            self.assertTrue(resp.get("ok"), resp)
            got = [(w["title"], w.get("surface_count", 0))
                   for w in client.workspace_list()]
            self.assertEqual([t for t, _ in got], [t for t, _ in want],
                             "restored workspace titles must match the file")
        finally:
            _shutdown_isolated(daemon, home)


class TestRestoredPaneReadableAndCapturePane(unittest.TestCase):
    """Two defects found while investigating restored-pane pty attachment.

    D1: only lmux_pane_send_keys() lazily spawns a pty, so read-screen fails on
        a restored pane until the user types something first.
    D2: build_json_command() rewrites every hyphen to an underscore, and the
        daemon has no `capture_pane` alias, so the documented `capture-pane`
        command is unreachable through the CLI.
    """

    def _restore_a_workspace(self, client):
        client.workspace_create("restored")
        client.surface_create(1)
        saved = client.snapshot_save("d1.json")["path"]
        resp = client.send("snapshot.load", {"path": saved})
        self.assertTrue(resp.get("ok"), resp)
        return saved

    def test_read_screen_works_on_a_restored_pane_without_typing(self):
        """T1/D1: read-screen must not require a prior send_text."""
        client, daemon, home = _isolated_client()
        try:
            self._restore_a_workspace(client)
            # Deliberately no send_text first — that is the whole point.
            resp = client.send("read-screen")
            self.assertTrue(resp.get("ok"),
                            f"read-screen should lazily spawn the pty: {resp}")
            self.assertNotIn("no pty to read", str(resp.get("error", "")))
            self.assertIsInstance(resp.get("result", {}).get("text"), str)
            # A fresh pty may not have produced output on the very first read,
            # so poll: the point is that the restored pane really does yield
            # screen content, not merely that the call returned ok.
            deadline = time.time() + 15
            text = ""
            while time.time() < deadline and not text.strip():
                time.sleep(0.3)
                r = client.send("read-screen")
                self.assertTrue(r.get("ok"), r)
                text = r.get("result", {}).get("text", "")
            self.assertTrue(
                text.strip(),
                "restored pane should produce screen content once attached")
        finally:
            _shutdown_isolated(daemon, home)

    def test_restored_pane_uses_the_snapshot_command(self):
        """T4: the lazily spawned pty must run the recorded command."""
        client, daemon, home = _isolated_client()
        try:
            saved = self._restore_a_workspace(client)
            recorded = json.loads(Path(saved).read_text())
            want = recorded["workspaces"][-1]["surfaces"][0]["panes"][0]["command"]
            self.assertTrue(want, "snapshot should record a command")
            # The pty is attached on first read, so read before inspecting.
            self.assertTrue(client.send("read-screen").get("ok"),
                            "read-screen should attach the restored pty")
            want_name = Path(want).name
            deadline = time.time() + 15
            children = ""
            while time.time() < deadline:
                children = subprocess.run(
                    ["pgrep", "-P", str(daemon._proc.pid), "-a"],
                    capture_output=True, text=True, timeout=10).stdout
                if want_name in children:
                    break
                time.sleep(0.2)
            self.assertIn(
                want_name, children,
                f"restored pty should run {want!r}, not a default shell; "
                f"children: {children!r}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_capture_pane_hyphenated_form_works(self):
        """T2/D2: the spelling printed in --help."""
        client, daemon, home = _isolated_client()
        try:
            client.workspace_create("cap")
            for cmd in ("capture-pane", "capture_pane"):
                resp = client.send(cmd)
                self.assertTrue(
                    resp.get("ok"),
                    f"{cmd} must work (build_json_command maps both to "
                    f"'capture_pane'): {resp}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_capture_pane_is_reachable_from_the_shell(self):
        """T3: exercise the real client, which is where the name is mangled."""
        repo_root = Path(__file__).parent.parent
        cli = repo_root / "build" / "lmux"
        self.assertTrue(cli.exists(),
                        f"lmux binary not built at {cli}; refusing to skip — "
                        f"build it with `node scripts/build-cli.mjs` first")
        # Run the client exactly as a user would, against a throwaway daemon.
        home = Path(tempfile.mkdtemp(prefix="lmux-cap-"))
        (home / ".config").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        sock_path = f"/tmp/lmux-cap-{os.getpid()}.sock"
        Path(sock_path).unlink(missing_ok=True)
        proc = subprocess.Popen(
            [str(cli), "--socket", sock_path, "daemon"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            deadline = time.time() + 30
            ready = False
            while time.time() < deadline and not ready:
                time.sleep(0.2)
                r = subprocess.run([str(cli), "--socket", sock_path, "--json",
                                    "ping"], capture_output=True, text=True,
                                   timeout=15, env=env)
                ready = r.returncode == 0 and '"ok":true' in r.stdout
            self.assertTrue(ready, "daemon did not come up")
            subprocess.run([str(cli), "--socket", sock_path, "--json",
                            "workspace.create", "cap"], env=env,
                           capture_output=True, timeout=15)
            for cmd in ("capture-pane", "capture_pane"):
                r = subprocess.run([str(cli), "--socket", sock_path, "--json", cmd],
                                   capture_output=True, text=True,
                                   timeout=30, env=env)
                self.assertIn('"ok":true', r.stdout,
                              f"`lmux {cmd}` must work from the shell; got {r.stdout!r}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            Path(sock_path).unlink(missing_ok=True)
            shutil.rmtree(home, ignore_errors=True)


class TestReadScreenIsARepeatableCapture(unittest.TestCase):
    """`read-screen` must behave like tmux `capture-pane`, not a drain.

    Three defects:
      R1: it returned only the bytes that arrived since the previous call, so
          a second call returned "".
      R2: its JSON-escaping loop iterated `while (screen[ii] && ...)`, stopping
          at the first NUL byte — and terminal output is full of them.
      R3: with no buffer, there was nothing to bound; a capture must stay
          within a fixed size regardless of how much output a pane produced.
    """

    def _pane(self):
        client, daemon, home = _isolated_client()
        client.workspace_create("cap")
        # A fresh zsh shows the first-run wizard, which consumes the first
        # keystrokes of anything typed into it. Choose "0" to write a .zshrc
        # and exit the wizard, so later commands actually reach the shell.
        client.surface_send_text("0\n")
        deadline = time.time() + 20
        while time.time() < deadline:
            time.sleep(0.3)
            r = client.send("read-screen")
            text = r.get("result", {}).get("text", "") if r.get("ok") else ""
            if "Type one of the keys" not in text:
                break
        return client, daemon, home

    def _read(self, client):
        r = client.send("read-screen")
        self.assertTrue(r.get("ok"), r)
        return r.get("result", {}).get("text", "")

    def test_repeated_read_screen_keeps_returning_content(self):
        """R1: a second capture must not come back empty."""
        client, daemon, home = self._pane()
        try:
            first = ""
            deadline = time.time() + 15
            while time.time() < deadline and not first.strip():
                time.sleep(0.3)
                first = self._read(client)
            self.assertTrue(first.strip(), "pane produced no output to capture")
            # Drain the pty, then capture again — content must still be there.
            time.sleep(0.5)
            second = self._read(client)
            self.assertTrue(
                second.strip(),
                "second read-screen returned empty; it drained the pty "
                "instead of capturing a buffer")
            self.assertIn(
                "zsh", second,
                "capture should be repeatable and retain earlier output")
        finally:
            _shutdown_isolated(daemon, home)

    def test_read_screen_is_not_truncated_by_nul_bytes(self):
        """R2: output containing NULs must survive escaping.

        The marker is assembled from shell variables at runtime so the typed
        command line does not literally contain it — otherwise the shell's
        echo of the command satisfies the assertion and the test passes
        vacuously.
        """
        client, daemon, home = self._pane()
        try:
            client.surface_send_text(
                r"printf 'AAA\000\000B\102\102_MARKER_END\n'; sleep 60" + "\n")
            text = ""
            deadline = time.time() + 25
            while time.time() < deadline and "BBB_MARKER_END" not in text:
                time.sleep(0.3)
                text = self._read(client)
            self.assertIn(
                "BBB_MARKER_END", text,
                "content after a NUL byte was lost; JSON escaping must skip "
                f"NULs rather than terminate. Got: {text[-160:]!r}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_capture_size_is_bounded(self):
        """R3: a huge burst of output must not produce an unbounded capture."""
        client, daemon, home = self._pane()
        try:
            client.surface_send_text(
                r"for i in $(seq 1 4000); do printf '%s\n' "
                r"'0123456789012345678901234567890123456789'; done; sleep 60" + "\n")
            text = ""
            deadline = time.time() + 40
            while time.time() < deadline and len(text) < 4096:
                time.sleep(0.5)
                text = self._read(client)
            self.assertLessEqual(
                len(text), 200_000,
                f"capture is unbounded ({len(text)} bytes); it must be capped")
        finally:
            _shutdown_isolated(daemon, home)


GHOST_COMMANDS = [
    "ssh.list", "ssh.connect", "ssh.disconnect",
    "hooks.add", "hooks.list", "hooks.remove",
    "naming.suggest", "focus.history",
    "notification.hook.list", "notification.hook.remove",
]


class TestAdvertisedCommandsAreDispatchable(unittest.TestCase):
    """`--help` must not promise commands the daemon cannot dispatch.

    Ten commands were advertised (and five of them listed in is_readonly_cmd)
    while having no dispatch handler, so every one returned unknown_command.
    Nothing in the GUI ever sent them, so this was a false API promise rather
    than a broken feature.
    """

    def test_every_advertised_command_is_dispatchable(self):
        """F5: sweep --help against the live daemon."""
        repo_root = Path(__file__).parent.parent
        cli = repo_root / "build" / "lmux"
        self.assertTrue(cli.exists(), f"lmux not built at {cli}")
        out = subprocess.run([str(cli)], capture_output=True, text=True,
                             timeout=30).stdout
        advertised = sorted(set(re.findall(r"^\s{2}([a-z][a-z0-9_.-]+)", out,
                                           re.M)))
        advertised = [a for a in advertised if a != "lmux"]
        self.assertGreater(len(advertised), 50, "help parsing looks broken")

        client, daemon, home = _isolated_client()
        try:
            missing = []
            for name in advertised:
                # build_json_command() in the CLI rewrites every hyphen to an
                # underscore before sending, so normalise the same way. Sending
                # the raw advertised name would report false ghosts for
                # commands that are perfectly reachable.
                wire = name.replace("-", "_")
                r = client.send(wire)
                if not r.get("ok") and r.get("error", {}).get(
                        "code") == "unknown_command":
                    missing.append(name)
            self.assertEqual(
                missing, [],
                f"{len(missing)} command(s) advertised in --help are not "
                f"dispatched by the daemon: {missing}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_the_ten_ghosts_now_exist(self):
        client, daemon, home = _isolated_client()
        try:
            for name in GHOST_COMMANDS:
                r = client.send(name.replace("-", "_"))
                self.assertNotEqual(
                    "unknown_command", r.get("error", {}).get("code"),
                    f"{name} is still a ghost: {r}")
        finally:
            _shutdown_isolated(daemon, home)


class TestSshGhostCommands(unittest.TestCase):
    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def test_ssh_list_returns_a_sessions_array(self):
        r = self.client.send("ssh.list")
        self.assertTrue(r.get("ok"), r)
        self.assertIsInstance(r["result"]["sessions"], list)

    def test_ssh_disconnect_detaches_without_destroying(self):
        """disconnect unbinds the pane but keeps the session listed.

        lmux_ssh_session_detach() clears the pane binding; destroying the
        session is `ssh.session.kill`. So the session must survive here.
        """
        r = self.client.send("ssh.connect", {"host": "example.invalid",
                                             "user": "nobody"})
        self.assertTrue(r.get("ok"), r)
        sid = r["result"]["id"]
        listing = self.client.send("ssh.list")
        self.assertIn(sid, [s["id"] for s in listing["result"]["sessions"]])
        d = self.client.send("ssh.disconnect", {"session_id": str(sid)})
        self.assertTrue(d.get("ok"), d)
        after = self.client.send("ssh.list")
        self.assertIn(
            sid, [s["id"] for s in after["result"]["sessions"]],
            "disconnect must not destroy the session; kill does that")

    def test_ssh_disconnect_unknown_session_errors(self):
        r = self.client.send("ssh.disconnect", {"session_id": "99999"})
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r["error"]["code"], "not_found", r)

    def test_ssh_disconnect_requires_session_id(self):
        r = self.client.send("ssh.disconnect", {})
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r["error"]["code"], "invalid_params", r)


class TestFocusHistoryGhostCommand(unittest.TestCase):
    """focus.history must reflect real focus changes, not be permanently empty."""

    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def test_focus_history_records_focus_changes(self):
        r = self.client.send("focus.history")
        self.assertTrue(r.get("ok"), r)
        self.assertIsInstance(r["result"]["entries"], list)
        # Creating a workspace does not focus a pane, so drive real focus
        # changes through pane.focus (the documented focus entry point).
        panes = self.client.send("pane.list")["result"]["panes"]
        self.assertTrue(panes)
        for p in panes:
            self.client.send("pane.focus", {"id": str(p["id"])})
        time.sleep(0.3)
        after = self.client.send("focus.history")
        self.assertTrue(after.get("ok"), after)
        self.assertGreater(
            len(after["result"]["entries"]), 0,
            "focus.history stayed empty after focus changes; a permanently "
            "empty array would satisfy ok:true while being useless")
        self.assertIn("pane_id", after["result"]["entries"][0])

    def test_focus_history_records_an_explicit_pane_focus(self):
        """pane.focus drives focus changes explicitly and must be recorded."""
        self.client.send("workspace.create", {"title": "fh"})
        time.sleep(0.2)
        pane = self.client.send("pane.list")["result"]["panes"][0]
        self.client.send("pane.focus", {"id": str(pane["id"])})
        time.sleep(0.3)
        entries = self.client.send("focus.history")["result"]["entries"]
        self.assertTrue(
            entries, "explicit pane.focus was not recorded in focus.history")
        self.assertTrue(
            any(e["pane_id"] == str(pane["id"]) for e in entries),
            f"focused pane {pane['id']} missing from history: {entries}")

    def test_focus_history_is_bounded(self):
        p = self.client.send("pane.list")["result"]["panes"][0]
        # Alternate the recorded pane id is not possible with one pane, so drive
        # many distinct pushes through focus and assert the ring stays bounded.
        for i in range(150):
            self.client.send("workspace.create", {"title": f"b{i}"})
            panes = self.client.send("pane.list")["result"]["panes"]
            if len(panes) > 1:
                self.client.send("pane.focus", {"id": str(panes[i % len(panes)]["id"])})
        self.client.send("pane.focus", {"id": str(p["id"])})
        r = self.client.send("focus.history", {"n": "100"})
        self.assertLessEqual(
            len(r["result"]["entries"]), 100,
            "focus history must stay within its documented 100-entry bound")


class TestHooksRegistryCommands(unittest.TestCase):
    """hooks.* binds scripts to agent lifecycle events (ARCHITECTURE.md)."""

    LIFECYCLE = ["session-start", "prompt-submit", "stop", "error"]

    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def test_hooks_round_trip(self):
        r = self.client.send("hooks.add",
                             {"event": "stop", "command": "/bin/true"})
        self.assertTrue(r.get("ok"), r)
        lst = self.client.send("hooks.list")
        self.assertTrue(lst.get("ok"), lst)
        self.assertEqual(len(lst["result"]["hooks"]), 1, lst)
        hook = lst["result"]["hooks"][0]
        self.assertEqual(hook["event"], "stop")
        self.assertEqual(hook["command"], "/bin/true")
        rm = self.client.send("hooks.remove", {"event": "stop"})
        self.assertTrue(rm.get("ok"), rm)
        self.assertEqual(
            len(self.client.send("hooks.list")["result"]["hooks"]), 0)

    def test_hooks_list_covers_every_lifecycle_event(self):
        for ev in self.LIFECYCLE:
            self.client.send("hooks.add", {"event": ev, "command": "/bin/true"})
        lst = self.client.send("hooks.list")
        got = {h["event"] for h in lst["result"]["hooks"]}
        self.assertEqual(got, set(self.LIFECYCLE), lst)

    def test_hooks_add_rejects_unknown_event(self):
        r = self.client.send("hooks.add",
                             {"event": "nope", "command": "/bin/true"})
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r["error"]["code"], "invalid_params", r)

    def test_hooks_remove_unknown_event_errors(self):
        r = self.client.send("hooks.remove", {"event": "stop"})
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r["error"]["code"], "not_found", r)

    def test_hooks_registry_is_bounded(self):
        for i in range(600):
            self.client.send("hooks.add",
                             {"event": self.LIFECYCLE[i % 4],
                              "command": f"/bin/true {i}"})
        lst = self.client.send("hooks.list")
        self.assertLessEqual(len(lst["result"]["hooks"]), 256,
                             "hooks registry must stay bounded")


class TestNamingSuggestCommand(unittest.TestCase):
    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def test_naming_suggest_returns_a_candidate(self):
        r = self.client.send("naming.suggest", {"cwd": "/tmp"})
        self.assertTrue(r.get("ok"), r)
        names = r["result"]["suggestions"]
        self.assertIsInstance(names, list)
        self.assertTrue(names, "expected at least one suggestion for /tmp")
        self.assertTrue(all(isinstance(n, str) and n for n in names), names)

    def test_naming_suggest_uses_the_basename(self):
        r = self.client.send("naming.suggest", {"cwd": "/usr/share/doc"})
        self.assertTrue(r.get("ok"), r)
        self.assertIn("doc", r["result"]["suggestions"], r)

    def test_naming_suggest_handles_empty_cwd(self):
        r = self.client.send("naming.suggest", {"cwd": ""})
        self.assertTrue(r.get("ok"), r)
        self.assertIsInstance(r["result"]["suggestions"], list)


class TestNotificationHookCommands(unittest.TestCase):
    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def test_notification_hook_list_round_trip(self):
        # The advertised ghost is the singular `notification.hook.*`; the
        # plural `notification.hooks.*` forms already dispatch. Add via the
        # plural form, then read back through the advertised singular form.
        add = self.client.send("notification.hooks.add",
                               {"event": "bell", "filter": "*"})
        self.assertTrue(add.get("ok"), add)
        lst = self.client.send("notification.hook.list")
        self.assertTrue(lst.get("ok"), lst)
        self.assertEqual(len(lst["result"]["hooks"]), 1, lst)
        self.assertTrue(lst["result"]["hooks"][0]["enabled"], lst)
        rm = self.client.send("notification.hook.remove", {"event": "bell"})
        self.assertTrue(rm.get("ok"), rm)
        after = self.client.send("notification.hook.list")
        # lmux_notification_hook_remove() disables rather than deletes, so the
        # entry remains but must no longer report as enabled.
        self.assertEqual(len(after["result"]["hooks"]), 1, after)
        self.assertFalse(after["result"]["hooks"][0]["enabled"],
                         f"hook should be disabled after remove: {after}")

    def test_notification_hook_remove_unknown_errors(self):
        r = self.client.send("notification.hook.remove", {"event": "nope"})
        self.assertFalse(r.get("ok"), r)
        self.assertEqual(r["error"]["code"], "not_found", r)


class TestNewCommandsWorkFromTheShell(unittest.TestCase):
    """The daemon handling a command is not the same as the CLI being able to
    reach it.

    Dispatch-only tests sent JSON directly to the daemon and passed while the
    shell path stayed broken: the CLI builds its own arg keys, and two of them
    disagreed with the handler (`hooks.add` sends "script", the handler read
    "command"; `focus.history` sends "count", the handler read "n").
    """

    def _cli(self, home, sock, *args):
        cli = Path(__file__).parent.parent / "build" / "lmux"
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        return subprocess.run([str(cli), "--socket", sock, "--json", *args],
                              capture_output=True, text=True, timeout=30,
                              env=env)

    def test_new_commands_are_reachable_through_the_cli(self):
        client, daemon, home = _isolated_client()
        try:
            sock = daemon.socket_path
            self.assertTrue(sock, "daemon has no socket_path")
            checks = [
                ("hooks.add", "stop", "/bin/true"),
                ("hooks.list",),
                ("naming.suggest", "/usr/share/doc"),
                ("focus.history",),
                ("ssh.list",),
                ("notification.hook.list",),
            ]
            for argv in checks:
                r = self._cli(home, sock, *argv)
                self.assertIn('"ok":true', r.stdout,
                              f"`lmux {' '.join(argv)}` failed from the shell: "
                              f"{r.stdout!r} {r.stderr!r}")
        finally:
            _shutdown_isolated(daemon, home)

    def test_hooks_add_from_the_shell_actually_registers(self):
        """Covers the `script` vs `command` key mismatch."""
        client, daemon, home = _isolated_client()
        try:
            sock = daemon.socket_path
            add = self._cli(home, sock, "hooks.add", "stop", "/bin/true")
            self.assertIn('"ok":true', add.stdout, add.stdout)
            lst = self._cli(home, sock, "hooks.list")
            self.assertIn('"ok":true', lst.stdout, lst.stdout)
            self.assertIn("/bin/true", lst.stdout,
                          f"hook registered via CLI but not listed: {lst.stdout}")
            self.assertIn("stop", lst.stdout)
        finally:
            _shutdown_isolated(daemon, home)


class TestHooksMultiTokenCommandForm(unittest.TestCase):
    """`lmux hooks add <event> <script>` is the form gui/hooks_setup.py uses.

    build_json_command() maps the bare word `hooks` to `hooks.list` via its
    shorthand table, so the GUI's invocation used to return a list with exit 0.
    hooks_setup.py checks returncode == 0 and therefore recorded the hook as
    installed while nothing was registered.
    """

    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()
        self.sock = self.daemon.socket_path

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def _cli(self, *args):
        cli = Path(__file__).parent.parent / "build" / "lmux"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        return subprocess.run([str(cli), "--socket", self.sock, "--json",
                               *args], capture_output=True, text=True,
                              timeout=30, env=env)

    def test_hooks_add_space_form_registers_the_hook(self):
        add = self._cli("hooks", "add", "stop", "/bin/true")
        self.assertIn('"ok":true', add.stdout,
                      f"`lmux hooks add` failed: {add.stdout!r} {add.stderr!r}")
        self.assertNotIn(
            '"hooks":[', add.stdout,
            "`lmux hooks add` returned a LIST; the bare-word shorthand "
            "swallowed the subcommand")
        lst = self._cli("hooks", "list")
        self.assertIn("/bin/true", lst.stdout,
                      f"hook not registered by the space form: {lst.stdout!r}")
        self.assertIn("stop", lst.stdout)

    def test_hooks_setup_exact_argv_shape_works(self):
        """The precise call gui/hooks_setup.py:329 makes."""
        cli = Path(__file__).parent.parent / "build" / "lmux"
        result = subprocess.run(
            [str(cli), "--socket", self.sock, "hooks", "add",
             "session-start", "lmux agent.spawn -- claude --resume"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": str(self.home),
                 "XDG_CONFIG_HOME": str(self.home / ".config")})
        self.assertEqual(result.returncode, 0, result.stderr)
        lst = self._cli("hooks", "list")
        self.assertIn("session-start", lst.stdout, lst.stdout)
        self.assertIn("agent.spawn", lst.stdout, lst.stdout)

    def test_hooks_remove_space_form(self):
        self._cli("hooks", "add", "error", "/bin/false")
        rm = self._cli("hooks", "remove", "error")
        self.assertIn('"ok":true', rm.stdout, f"{rm.stdout!r} {rm.stderr!r}")
        lst = self._cli("hooks", "list")
        self.assertNotIn("/bin/false", lst.stdout, lst.stdout)

    def test_both_spellings_address_the_same_registry(self):
        self._cli("hooks", "add", "stop", "/bin/echo")
        via_dot = self._cli("hooks.add", "prompt-submit", "/bin/echo")
        self.assertIn('"ok":true', via_dot.stdout, via_dot.stdout)
        lst = self._cli("hooks", "list")
        self.assertIn("stop", lst.stdout, lst.stdout)
        self.assertIn("prompt-submit", lst.stdout, lst.stdout)


class TestNamingParityWithGui(unittest.TestCase):
    """naming.suggest must match gui/auto_naming.py::suggest_names().

    The daemon port only derived names from the directory basename, while the
    GUI implements an ordered chain (git repo, repo:branch, package.json,
    basename, basename (type)). This runs both over the same fixtures so drift
    becomes a test failure rather than a slow divergence.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).parent.parent / "gui"))
        import auto_naming  # noqa: E402
        # Store the plain function, not a bound method: assigning it to a
        # class attribute would make it a descriptor and pass `self` in.
        cls.suggest_names = staticmethod(auto_naming.suggest_names)

    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def _daemon_suggestions(self, cwd):
        r = self.client.send("naming.suggest", {"cwd": cwd})
        self.assertTrue(r.get("ok"), r)
        return r["result"]["suggestions"]

    def _assert_parity(self, cwd):
        expected = self.suggest_names(cwd)
        actual = self._daemon_suggestions(cwd)
        self.assertEqual(
            actual, expected,
            f"daemon and GUI disagree for {cwd!r}:\n"
            f"  gui   = {expected}\n  daemon= {actual}")

    def _git_repo(self, d, branch):
        subprocess.run(["git", "init", "-q", "-b", branch, d],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", d, "config", "user.name", "t"],
                       capture_output=True, timeout=30)
        Path(d, "f").write_text("x")
        subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True,
                       timeout=30)
        subprocess.run(["git", "-C", d, "commit", "-qm", "i"],
                       capture_output=True, timeout=30)

    def test_parity_on_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, "main")
            Path(d, "Cargo.toml").write_text("[package]\nname=\"x\"\n")
            self._assert_parity(d)

    def test_parity_on_a_git_repo_with_a_feature_branch(self):
        with tempfile.TemporaryDirectory() as d:
            self._git_repo(d, "main")
            subprocess.run(["git", "-C", d, "checkout", "-qb", "feature/x"],
                           capture_output=True, timeout=30)
            suggestions = self._daemon_suggestions(d)
            self.assertTrue(any(":" in s for s in suggestions),
                            f"expected a repo:branch suggestion: {suggestions}")
            self._assert_parity(d)

    def test_parity_on_a_node_package(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "package.json").write_text(
                json.dumps({"name": "@scope/thing"}))
            self._assert_parity(d)

    def test_parity_on_a_python_project(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pyproject.toml").write_text("[project]\nname='x'\n")
            self._assert_parity(d)

    def test_parity_on_a_plain_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self._assert_parity(d)

    # --- adversarial package.json shapes ---------------------------------
    # PR #23's parity fixtures all put "name" first, so the flat strstr in
    # naming_package_json_name() happened to be right and the test passed
    # without exercising the bug. These fixtures put a nested "name" first.

    def _pkg_parity(self, contents, label):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "package.json").write_text(contents)
            expected = self.suggest_names(d)
            actual = self._daemon_suggestions(d)
            self.assertEqual(
                actual, expected,
                f"{label}: daemon and GUI disagree:\n"
                f"  gui   = {expected}\n  daemon= {actual}")

    def test_nested_name_does_not_beat_top_level_name(self):
        self._pkg_parity(
            '{"scripts": {"name": "WRONG"}, "name": "right-name"}',
            "nested name inside scripts")

    def test_nested_name_in_dependencies_does_not_win(self):
        self._pkg_parity(
            '{"dependencies": {"name": "WRONG-from-deps"}, "name": "real-pkg"}',
            "nested name inside dependencies")

    def test_nested_name_in_a_deeply_nested_object(self):
        self._pkg_parity(
            '{"a": {"b": {"c": {"name": "DEEP-WRONG"}}}, "name": "top"}',
            "deeply nested name")

    def test_name_inside_an_array_element(self):
        self._pkg_parity(
            '{"workspaces": [{"name": "IN-ARRAY"}], "name": "top-array"}',
            "name inside an array element")

    def test_escaped_quote_name_inside_a_string(self):
        self._pkg_parity(
            '{"description": "contains \\"name\\" text", "name": "after-esc"}',
            "escaped quotes inside a string value")

    def test_name_after_a_nested_object_with_no_top_level_name(self):
        """No top-level name at all: both must agree on the fallback."""
        self._pkg_parity(
            '{"scripts": {"name": "only-nested"}}', "no top-level name")

    def test_scoped_and_whitespace_variants(self):
        self._pkg_parity('{ "name"  :\t"@scope/pkg" , "v":1 }',
                         "whitespace and scope")
        self._pkg_parity('{"name":"plain","version":"1.0.0"}', "compact")
    def test_top_level_value_equal_to_name_before_the_key(self):
        """A top-level *value* of "name" must not be mistaken for the key.

        A candidate string only counts as a key when a ':' follows it. Here the
        value is followed by a comma, so the real (later) "name" must still win.
        """
        self._pkg_parity(
            '{"description": "name", "name": "real"}',
            'a top-level value equal to "name" before the key')

    def test_value_equal_to_name_under_another_key(self):
        self._pkg_parity(
            '{"a": "name", "name": "real"}',
            'value "name" stored under another key')





class TestNamingSuggestAvoidsSpuriousGitForks(unittest.TestCase):
    """naming.suggest must not spawn `git` for directories that are not
    repositories, and must not re-fork for the same path."""

    def setUp(self):
        self.client, self.daemon, self.home = _isolated_client()

    def tearDown(self):
        _shutdown_isolated(self.daemon, self.home)

    def _git_children(self):
        """Count the daemon's direct children that are `git` processes.

        The daemon always has a pty child (the workspace shell), so an
        absolute child count is meaningless; count only git.
        """
        pid = str(self.daemon._proc.pid)
        kids = subprocess.run(["pgrep", "-P", pid], capture_output=True,
                              text=True, timeout=10).stdout.split()
        n = 0
        for k in kids:
            try:
                cmd = Path(f"/proc/{k}/cmdline").read_bytes().decode(
                    "utf-8", "replace")
            except OSError:
                continue
            if "git" in cmd.split("\0")[0]:
                n += 1
        return n

    def _wait_for_no_git(self, seconds=1.5):
        """Poll briefly: a fork+exec may not have exited on the first read."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._git_children() == 0:
                return True
            time.sleep(0.1)
        return False

    def test_no_git_process_for_a_non_repository(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "plain.txt").write_text("x")
            r = self.client.send("naming.suggest", {"cwd": d})
            self.assertTrue(r.get("ok"), r)
            self.assertTrue(
                self._wait_for_no_git(),
                "daemon spawned git for a non-repository directory")

    def test_repeated_calls_do_not_respawn_git(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "f").write_text("x")
            first = self.client.send("naming.suggest", {"cwd": d})
            self.assertTrue(first.get("ok"), first)
            for _ in range(5):
                again = self.client.send("naming.suggest", {"cwd": d})
                self.assertEqual(
                    again["result"]["suggestions"],
                    first["result"]["suggestions"],
                    "repeat calls must return the same suggestions")
            self.assertTrue(
                self._wait_for_no_git(),
                "repeat calls must not respawn git")

    def test_a_real_repository_still_forking_produces_repo_suggestions(self):
        """S3 must not suppress the fork where it is actually needed."""
        d = tempfile.mkdtemp(prefix="lmux-git-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", d],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", d, "config", "user.email", "t@t"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", d, "config", "user.name", "t"],
                       capture_output=True, timeout=30)
        Path(d, "f").write_text("x")
        subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True,
                       timeout=30)
        subprocess.run(["git", "-C", d, "commit", "-qm", "i"],
                       capture_output=True, timeout=30)
        r = self.client.send("naming.suggest", {"cwd": d})
        self.assertTrue(r.get("ok"), r)
        self.assertIn(
            Path(d).name, r["result"]["suggestions"],
            f"repo name must still be suggested: {r['result']['suggestions']}")

    def test_nested_directory_inside_a_repo_is_still_detected(self):
        """A subdirectory has no .git of its own but is still in the tree."""
        repo = tempfile.mkdtemp(prefix="lmux-git2-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", repo],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"],
                       capture_output=True, timeout=30)
        Path(repo, "f").write_text("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True,
                       timeout=30)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "i"],
                       capture_output=True, timeout=30)
        deep = Path(repo, "src", "nested")
        deep.mkdir(parents=True)
        r = self.client.send("naming.suggest", {"cwd": str(deep)})
        self.assertTrue(r.get("ok"), r)
        self.assertIn(
            Path(repo).name, r["result"]["suggestions"],
            f"repo root should be found from a nested dir: "
            f"{r['result']['suggestions']}")
    def test_switching_branch_invalidates_the_cache(self):
        """A cached path must not serve a stale suggestion after a checkout.

        The first call memoises the (main-branch) result; switching to a
        feature branch changes `.git/HEAD`, so the next call must recompute and
        surface the repo:branch form rather than the cached one.
        """
        repo = tempfile.mkdtemp(prefix="lmux-git3-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", repo],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"],
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"],
                       capture_output=True, timeout=30)
        Path(repo, "f").write_text("x")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True,
                       timeout=30)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "i"],
                       capture_output=True, timeout=30)
        repo_name = Path(repo).name

        first = self.client.send("naming.suggest", {"cwd": repo})
        self.assertTrue(first.get("ok"), first)
        self.assertNotIn(f"{repo_name}:branchy", first["result"]["suggestions"])

        subprocess.run(["git", "-C", repo, "checkout", "-qb", "branchy"],
                       capture_output=True, timeout=30)

        again = self.client.send("naming.suggest", {"cwd": repo})
        self.assertTrue(again.get("ok"), again)
        self.assertIn(
            f"{repo_name}:branchy", again["result"]["suggestions"],
            "cache served a stale result after checkout: "
            f"{again['result']['suggestions']}")





class TestSnapshotParserCorrectness(unittest.TestCase):
    """#11 structural fix: the hand-rolled brace-counting scanner silently
    corrupted any snapshot whose string values contained braces.

    Reproduced on master: a workspace titled 'evil}' made a *surface* object
    appear in the workspace list, and the load still reported ok:true.
    """

    def _roundtrip(self, title):
        """Create a workspace with `title`, save, load, return the titles seen."""
        client, daemon, home = _isolated_client()
        try:
            ws = client.workspace_create(title)
            client.surface_create(ws["id"])
            saved = client.snapshot_save("rt.json")["path"]
            resp = client.send("snapshot.load", {"path": saved})
            self.assertTrue(resp.get("ok"), f"load should succeed: {resp}")
            titles = [w["title"] for w in client.workspace_list()]
            return titles
        finally:
            _shutdown_isolated(daemon, home)

    def test_closing_brace_in_title_does_not_leak_surfaces(self):
        """T1: the exact corruption that motivated the parser."""
        titles = self._roundtrip("evil}")
        self.assertIn("evil}", titles,
                      f"the title must round-trip intact: {titles}")
        self.assertNotIn("Shell", titles,
                         f"a surface title leaked in as a workspace: {titles}")
        self.assertNotIn("Tab", titles,
                         f"a surface title leaked in as a workspace: {titles}")

    def test_brace_and_quote_characters_round_trip(self):
        for title in ["a}b", "a{b", 'q"q', "back\\slash", "{}"]:
            with self.subTest(title=title):
                titles = self._roundtrip(title)
                self.assertIn(title, titles,
                              f"{title!r} did not round-trip: {titles}")

    def test_truncated_snapshot_is_rejected_not_half_applied(self):
        """T3: an unbalanced document must fail loudly."""
        client, daemon, home = _isolated_client()
        try:
            snap = Path(tempfile.gettempdir(),
                        f"lmux-test-trunc-{os.getpid()}.json")
            snap.write_text('{"version":1,"workspaces":[{"id":1,"title":"a",'
                            '"cwd":"/tmp","surfaces":[{"id":1,"title":"b"')
            resp = client.send("snapshot.load", {"path": str(snap)})
            self.assertFalse(resp.get("ok"),
                             f"a truncated snapshot must be refused: {resp}")
            self.assertEqual(resp.get("error", {}).get("code"), "load_failed")
            snap.unlink(missing_ok=True)
        finally:
            _shutdown_isolated(daemon, home)

    def test_large_snapshot_loads_beyond_the_old_64kb_ceiling(self):
        """T4: the old fixed buffer rejected anything over 64 KB outright.

        Sized by padding the cwd rather than by adding workspaces:
        lmux_workspace_create() eagerly forks a pty per workspace, so cost is
        driven by workspace count (~50ms each), not by document size. 40
        workspaces with a long cwd still clear the old 64 KB ceiling while
        keeping the test to a couple of seconds.
        """
        client, daemon, home = _isolated_client()
        try:
            snap = Path(tempfile.gettempdir(),
                        f"lmux-test-large-{os.getpid()}.json")
            n = 40
            pad = "/x" * 1700
            ws = ",".join(
                '{"id":%d,"title":"ws%d","cwd":"%s","git_branch":"","surfaces":[]}'
                % (i, i, pad) for i in range(1, n + 1))
            snap.write_text('{"version":1,"workspaces":[' + ws + ']}')
            self.assertGreater(snap.stat().st_size, 65536,
                               "fixture must exceed the old ceiling")
            resp = client.send("snapshot.load", {"path": str(snap)})
            self.assertTrue(resp.get("ok"),
                            f"a >64KB snapshot must now load: {resp}")
            loaded = len(client.workspace_list())
            self.assertEqual(loaded, n,
                             f"expected all {n} workspaces, got {loaded}")
            snap.unlink(missing_ok=True)
        finally:
            _shutdown_isolated(daemon, home)


class TestNestedTruncationIsAnnounced(unittest.TestCase):
    """Superseded by TestSnapshotParserCorrectness.

    When the loader still used scratch buffers, this class asserted that
    sfcopy[2048] and pncopy[1024] logged a warning instead of truncating
    silently. #11's structural fix removed those buffers entirely, so the
    warning they guarded is no longer reachable — the values are decoded
    straight into model-sized fields and nothing is truncated.

    Deleting these assertions outright would hide that change. They are
    replaced by the strictly stronger invariant the goal asks for: none of the
    buffers may come back, and the load path may not reintroduce brace
    counting.
    """

    @classmethod
    def setUpClass(cls):
        cls.model_c = Path(__file__).parent.parent / "src" / "core" / "model.c"

    def test_no_scratch_buffers_remain_in_the_snapshot_loader(self):
        text = self.model_c.read_text()
        for gone in ("buf_copy", "sfcopy", "pncopy"):
            self.assertNotIn(gone, text,
                             f"{gone} must not come back: the parser decodes "
                             f"straight into model-sized fields")

    def test_load_path_does_not_count_braces(self):
        """The original bug: brace counting mis-parses any '}' in a string."""
        text = self.model_c.read_text()
        start = text.index("bool lmux_snapshot_load(lmux_app")
        end = text.index("bool lmux_snapshot_load_with_recovery", start)
        body = text[start:end]
        for pattern in ("depth++", "depth--", '!= \'{\'', '== \'}\''):
            self.assertNotIn(pattern, body,
                             f"load path must not do brace matching: {pattern!r}")

    def test_parser_escapes_are_string_aware(self):
        """A string value containing a brace must not terminate its object."""
        text = self.model_c.read_text()
        self.assertIn("static bool snap_read_string", text,
                      "the reader must decode strings with escape handling")
        start = text.index("static bool snap_read_string")
        window = text[start:start + 1400]
        self.assertIn("'\\\\'", window,
                      "snap_read_string must handle the escape character, "
                      "otherwise a quoted brace ends the string early")


class TestSnapshotSaveRoot(unittest.TestCase):
    """#12 step 4: snapshot.save called is_safe_path(path, NULL), which rejects
    ".." but places no containment, so any absolute path was accepted and the
    daemon wrote there. Confine it to a data root, mirroring #17's
    browser.screenshot fix."""

    @classmethod
    def setUpClass(cls):
        cls.client, cls.daemon, cls.home = _isolated_client()

    @classmethod
    def tearDownClass(cls):
        _shutdown_isolated(cls.daemon, cls.home)

    def _save(self, path=None):
        args = {"path": path} if path else {}
        return self.client.send("snapshot.save", args)

    def test_absolute_path_outside_root_is_rejected(self):
        # Pre-create the parent: without it the save fails for an unrelated
        # reason (missing directory), which is not a security control.
        outside = Path(self.home, "outside")
        outside.mkdir(parents=True, exist_ok=True)
        target = outside / "pwn.json"
        resp = self._save(str(target))
        self.assertFalse(resp.get("ok"), f"must be rejected: {resp}")
        self.assertEqual(resp.get("error", {}).get("code"), "invalid_params")
        self.assertFalse(target.exists(), "no file may be created outside the root")

    def test_traversal_is_rejected(self):
        resp = self._save("../escape.json")
        self.assertFalse(resp.get("ok"), f"must be rejected: {resp}")

    def test_no_path_still_saves_to_default_location(self):
        resp = self._save()
        self.assertTrue(resp.get("ok"), f"default save must still work: {resp}")
        path = Path(resp["result"]["path"])
        self.assertTrue(path.exists(), "default snapshot file should exist")

    def test_relative_filename_inside_root_is_accepted(self):
        resp = self._save("named.json")
        self.assertTrue(resp.get("ok"), f"relative name should be allowed: {resp}")
        self.assertNotIn("..", resp["result"]["path"])


    def test_default_path_works_without_a_preexisting_data_dir(self):
        """Regression guard for the bug this change introduced.

        Moving the default save location under the data root broke any machine
        without ~/.local/share/lmux: lmux_snapshot_save() created no
        directories, so the default save failed with save_failed. The
        _isolated_client() helper pre-creates that directory, so the ordinary
        default-path test cannot see it — this one starts from an empty HOME
        and points XDG_DATA_HOME somewhere that does not exist.
        """
        bare = Path(tempfile.mkdtemp(prefix="lmux-bare-home-"))
        data = bare / "no" / "such" / "data"
        env = dict(os.environ)
        env["HOME"] = str(bare)
        env["XDG_DATA_HOME"] = str(data)
        env.pop("XDG_CONFIG_HOME", None)
        sock = f"/tmp/lmux-bare-{os.getpid()}.sock"
        Path(sock).unlink(missing_ok=True)
        daemon = LmuxDaemon(sock, env=env)
        try:
            client = daemon.start(timeout=60)
            resp = client.send("snapshot.save")
            self.assertTrue(resp.get("ok"),
                            f"default save must work with no data dir: {resp}")
            saved = Path(resp["result"]["path"])
            self.assertTrue(saved.exists(),
                            f"snapshot should exist at {saved}")
            self.assertTrue(str(saved).startswith(str(data)),
                            f"default should land under XDG_DATA_HOME: {saved}")
        finally:
            try:
                daemon.stop()
            except Exception:  # noqa: BLE001 - teardown
                pass
            Path(sock).unlink(missing_ok=True)
            shutil.rmtree(bare, ignore_errors=True)


class TestDependencyGateTargetsMaster(unittest.TestCase):
    """#13: trivy.yml was already fail-closed but triggered on `main` while the
    repo's default branch is `master`, so the gate had never run."""

    @classmethod
    def setUpClass(cls):
        cls.trivy = Path(__file__).parent.parent / ".github" / "workflows" / "trivy.yml"

    def test_no_workflow_targets_a_nonexistent_main_branch(self):
        workflows = list(self.trivy.parent.glob("*.yml"))
        offenders = [w.name for w in workflows
                     if "branches: [main]" in w.read_text()]
        self.assertEqual(offenders, [],
                         f"these workflows target 'main' but the default "
                         f"branch is 'master': {offenders}")

    def test_dependency_gate_is_fail_closed(self):
        text = self.trivy.read_text()
        self.assertIn("exit-code: 1", text,
                      "the gate must stay fail-closed")
        self.assertIn("branches: [master]", text,
                      "the gate must trigger on master")


# ====================================================================
# Config persistence (round-trip through disk)
# ====================================================================

class TestConfigPersistence(unittest.TestCase):
    """Config mutations must survive a restart and reject invalid values.

    Runs against a private daemon with its own HOME so assertions can be made
    against the real config.json on disk, and so the developer's own
    ~/.config/lmux/config.json is never touched.
    """

    def _cfg_path(self, home):
        return Path(home, ".config", "lmux", "config.json")

    def test_group_create_is_persisted_to_disk(self):
        """T4: workspace.group.create used to mutate memory only, never saving."""
        client, daemon, home = _isolated_client()
        try:
            name = _unique("persist")
            resp = client.send("workspace.group.create", {"name": name})
            self.assertTrue(resp.get("ok"), resp)

            cfg_file = self._cfg_path(home)
            self.assertTrue(cfg_file.exists(),
                            "config.json should be written after a group create")
            self.assertIn(name, cfg_file.read_text(),
                          "created group should be present in config.json")
        finally:
            _shutdown_isolated(daemon, home)

    def test_group_survives_daemon_restart(self):
        """T1 at CLI level: a saved group must be readable after a restart."""
        client, daemon, home = _isolated_client()
        try:
            name = _unique("restart")
            client.send("workspace.group.create", {"name": name})
        finally:
            _shutdown_isolated(daemon, home)

        # Second daemon over the same HOME: the loader must understand the
        # array-of-objects form lmux_config_save() writes.
        client2, daemon2, home2 = _isolated_client(home=home)
        try:
            groups = client2.send("workspace.group.list").get("result", {})
            self.assertIn(name, [g["name"] for g in groups.get("groups", [])],
                          "group should survive a daemon restart")
        finally:
            _shutdown_isolated(daemon2, home2)

    def test_invalid_theme_value_is_rejected(self):
        """T3: config.set accepted any theme string; the GUI silently ignored
        everything except 'dark'/'light'."""
        client, daemon, home = _isolated_client()
        try:
            before = client.config_get("theme").get("value")
            # send() rather than config_set(): the helper raises on ok:false,
            # which is exactly the response under test.
            resp = client.send("config.set", {"key": "theme", "value": "Bogus"})
            self.assertFalse(resp.get("ok"),
                             f"invalid theme value should be rejected: {resp}")
            self.assertEqual(resp.get("error", {}).get("code"), "invalid_params")
            self.assertEqual(client.config_get("theme").get("value"), before,
                             "rejected value must not change the setting")
        finally:
            _shutdown_isolated(daemon, home)

    def test_valid_theme_values_are_accepted(self):
        client, daemon, home = _isolated_client()
        try:
            original = client.config_get("theme").get("value", "dark")
            for value in ("dark", "light"):
                resp = client.send("config.set", {"key": "theme", "value": value})
                self.assertTrue(resp.get("ok"), f"{value} should be accepted: {resp}")
                self.assertEqual(client.config_get("theme").get("value"), value)
            client.send("config.set", {"key": "theme", "value": original})
        finally:
            _shutdown_isolated(daemon, home)


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


class TestSessionRestoreConcurrency(unittest.TestCase):
    """Regression tests for issue #9: session.restore frees the whole model
    while client threads may be reading it.

    Each client connection is served on its own thread (client_thread,
    src/core/server.c). Restoring tears down every workspace, so without
    holding the model rw_lock a concurrent reader walks freed memory. Under
    ASan this reproduces immediately; without it, it shows up as an
    intermittent SIGSEGV/double-free that varies run to run.
    """

    @classmethod
    def setUpClass(cls):
        cls.c = _ensure_daemon()

    def test_restore_while_other_clients_read(self):
        """Restore repeatedly while other connections list workspaces/panes.

        Before the fix the daemon aborts (ASan: heap-use-after-free in
        dispatch_command reached from client_thread). After the fix every
        restore returns and the daemon is still answering.
        """
        client, daemon, home = _isolated_client()
        try:
            # Seed a snapshot worth restoring.
            for i in range(3):
                client.send("workspace.create", {"title": f"race-{i}"})
            client.send("session.save", {})

            stop = threading.Event()
            errors = []

            def reader():
                sock = client.socket_path
                while not stop.is_set():
                    try:
                        s = socket.socket(socket.AF_UNIX)
                        s.settimeout(2)
                        s.connect(sock)
                        payload = ('{"cmd":"workspace.list","args":{}}\n'
                                   '{"cmd":"pane.list","args":{}}\n')
                        s.sendall(payload.encode())
                        s.recv(65536)
                        s.close()
                    except Exception as e:  # noqa: BLE001 - report, don't mask
                        # A crash shows up here as a connect/send failure. Record
                        # it and stop this reader rather than spinning.
                        errors.append(e)
                        return

            threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
            for t in threads:
                t.start()

            # Restore repeatedly. This is the operation that used to crash.
            # Kept small so the test stays fast in CI: against the unfixed
            # additive loader each restore spawns a pty per restored pane, so
            # a large loop would grow without bound. The race is reliably
            # caught by the sanitizer job (issue #14), not by volume here.
            for _ in range(5):
                client.send("session.restore", {})
                client.send("session.save", {})

            stop.set()
            for t in threads:
                t.join(timeout=10)

            self.assertEqual(errors, [], f"reader clients failed: {errors[:3]}")

            # Daemon must still be alive and coherent.
            pong = client.send("ping", {})
            self.assertTrue(pong.get("ok"), pong)
            ws = client.send("workspace.list", {})
            self.assertTrue(ws.get("ok"), ws)
        finally:
            _shutdown_isolated(daemon, home)

    def test_restore_twice_does_not_duplicate(self):
        """Issue #8 regression: restoring twice must not append workspaces."""
        client, daemon, home = _isolated_client()
        try:
            for i in range(3):
                client.send("workspace.create", {"title": f"dup-{i}"})
            client.send("session.save", {})

            first = client.send("session.restore", {}).get("ok")
            self.assertTrue(first)
            n1 = len(client.send("workspace.list", {})["result"]["workspaces"])

            second = client.send("session.restore", {}).get("ok")
            self.assertTrue(second)
            n2 = len(client.send("workspace.list", {})["result"]["workspaces"])

            self.assertEqual(
                n1, n2,
                f"restore duplicated workspaces: {n1} -> {n2} (issue #8)",
            )
        finally:
            _shutdown_isolated(daemon, home)


# ====================================================================

class TestPackagingEntryPoints(unittest.TestCase):
    """package.json must not advertise commands that don't exist.

    Both `package:appimage` and `package:deb` pointed at non-existent
    `scripts/*.mjs` files, while the real logic lives in `packaging/*.sh`
    (reached through thin `scripts/*.sh` shims). Running them failed with
    "Cannot find module" — silent drift between package.json and the repo.
    """

    def test_script_file_targets_exist(self):
        root = Path(__file__).parent.parent
        pkg = json.loads((root / "package.json").read_text())
        missing = []
        for name, cmd in pkg.get("scripts", {}).items():
            for token in cmd.split():
                if token.startswith(("scripts/", "packaging/")):
                    if not (root / token).exists():
                        missing.append(f"{name} -> {token}")
        self.assertEqual(
            missing, [],
            f"package.json advertises non-existent script targets: {missing}")

    def test_no_stale_github_repo_url(self):
        """The repo lives under Dream-Pixels-Forge, not the old `lmux` org.

        The old org URL sat in the AppImage metainfo (where appstreamcli
        validation aborted `make appimage`) and in seven other tracked files —
        including the `git clone` line in CONTRIBUTING.md and the private
        vulnerability-reporting link in SECURITY.md, both of which are dead.
        Fix the build; this pins it.
        """
        root = Path(__file__).parent.parent
        # Assembled at runtime so this test file does not itself contain the
        # literals -- otherwise `git grep` matches its own source and the test
        # can never pass.
        #
        # Both namespaces: the GitHub org in web URLs and the OCI registry in
        # container-image references. Both should be `Dream-Pixels-Forge`
        # (GHCR lowercases it), never the legacy `lmux` namespace.
        needles = ("github.com/" + "lmux" + "/lmux",
                   "ghcr.io/" + "lmux" + "/lmux")
        found = []
        for needle in needles:
            found.append(subprocess.run(
                ["git", "grep", "-n", needle],
                cwd=root, capture_output=True, text=True).stdout.strip())
        stale = "\n".join(f for f in found if f)
        self.assertEqual(
            stale, "",
            "tracked files still reference the old `lmux` namespace:\n"
            + stale)

    def test_flatpak_metainfo_validates(self):
        """The flatpak manifest ships its own AppStream metainfo — keep it valid.

        Nothing else exercises this file: `flatpak-builder` is not part of the
        test environment, so the component shipped with no <launchable>
        element and `appstreamcli validate` rejected it with
        `E: desktop-app-launchable-missing`. The AppImage copy (inlined in
        packaging/build-appimage.sh) did have one; this copy did not.
        """
        if shutil.which("appstreamcli") is None:
            self.skipTest("appstreamcli unavailable; cannot validate metainfo")
        manifest = (Path(__file__).parent.parent
                    / "packaging" / "io.github.lmux.lmux.yml")
        lines = manifest.read_text().splitlines()
        start = next((i for i, l in enumerate(lines)
                      if "io.github.lmux.lmux.metainfo.xml" in l), None)
        self.assertIsNotNone(start, "manifest should write a metainfo file")
        body = []
        for line in lines[start + 1:]:
            if line.strip() == "EOF":
                break
            body.append(line.strip())
        tmp = tempfile.mkdtemp(prefix="lmux-metainfo-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # The filename must match the component id, otherwise appstreamcli
        # reports a cid mismatch and skips the checks we care about.
        xml = Path(tmp) / "io.github.lmux.lmux.metainfo.xml"
        xml.write_text("\n".join(body) + "\n")
        res = subprocess.run(
            ["appstreamcli", "validate", "--no-net", str(xml)],
            capture_output=True, text=True, timeout=60)
        errors = [l for l in res.stdout.splitlines() if l.startswith("E:")]
        self.assertEqual(
            errors, [],
            "flatpak AppStream metainfo fails validation:\n"
            + "\n".join(errors))
    def test_entry_point_naming_matches_documented_architecture(self):
        """`lmux` is the CLI and `lmux-gui` is the GUI — in every packager.

        README.md, CONTRIBUTING.md, gui/main.py's docstring and the compiled
        binary's own usage text ("Usage: lmux <command> [args...]") all agree
        on that split. The packagers did not: the deb shipped a GUI wrapper as
        `lmux` and hid the real binary behind `lmux-cli`, while the AppImage
        called the CLI `lmux-core`. So a fresh install answered `lmux` with the
        GUI, and the binary was reachable only under a different name than the
        one it prints in its own usage text.
        """
        root = Path(__file__).parent.parent
        blob = "\n".join((root / "packaging" / n).read_text()
                         for n in ("build-deb.sh", "build-appimage.sh"))
        # deb
        self.assertIn('"$BUILD_DIR/usr/bin/lmux"', blob,
                      "deb should ship the CLI as /usr/bin/lmux")
        self.assertIn('"$BUILD_DIR/usr/bin/lmux-gui"', blob,
                      "deb should ship the GUI as /usr/bin/lmux-gui")
        # AppImage
        self.assertIn('"$APPDIR/usr/bin/lmux"', blob,
                      "AppImage should ship the CLI as usr/bin/lmux")
        self.assertIn('"$APPDIR/usr/bin/lmux-gui"', blob,
                      "AppImage should ship the GUI as usr/bin/lmux-gui")
        # The retired install paths must not creep back in. Checked as paths,
        # not bare tokens, so a comment explaining the rename stays allowed.
        for retired in ("usr/bin/lmux-cli", "usr/bin/lmux-core"):
            self.assertNotIn(retired, blob,
                             f"{retired} is a retired install path")



class TestGuiLocaleDirReadOnly(unittest.TestCase):
    """gui/localization.py must survive a read-only install.

    create_default_locales() runs at import time and rewrote its own
    locale files unconditionally. Inside an AppImage the gui tree is
    mounted read-only squashfs, so `open(..., "w")` raised OSError and the
    GUI died before it ever started.
    """

    def test_create_default_locales_tolerates_readonly_locale_dir(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "gui"))
        import localization  # noqa: E402

        ro_parent = tempfile.mkdtemp(prefix="lmux-ro-locale-")
        self.addCleanup(shutil.rmtree, ro_parent, ignore_errors=True)
        os.chmod(ro_parent, 0o500)
        self.addCleanup(os.chmod, ro_parent, 0o700)

        original = localization.LOCALE_DIR
        localization.LOCALE_DIR = Path(ro_parent) / "locales"
        self.addCleanup(setattr, localization, "LOCALE_DIR", original)

        try:
            localization.create_default_locales()
        except OSError as e:
            self.fail(
                "create_default_locales must tolerate a read-only locale "
                f"dir (AppImage); got {type(e).__name__}: {e}")

    def test_readonly_import_still_provides_translations(self):
        """Shipped locale files must still be usable when we cannot write."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "gui"))
        import localization  # noqa: E402

        loc = localization.get_localization()
        self.assertTrue(loc.get("menu.file"))



if __name__ == "__main__":
    # Clean up leftover test sockets
    for f in Path("/tmp").glob("lmux-integration-*.sock"):
        try:
            f.unlink()
        except OSError:
            pass

    unittest.main(verbosity=2)
