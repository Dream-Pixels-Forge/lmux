"""lmux Python client library for integration testing.

Connects to the lmux daemon via Unix domain socket and sends JSON-RPC commands.
Similar to cmux's tests/cmux.py but for the Linux lmux daemon.

Usage:
    from lmux import LmuxClient
    with LmuxClient() as client:
        ws = client.workspace_create("my-workspace")
        client.surface_create(ws["id"])
        client.pane_split(ws["id"], "h")
"""

import json
import os
import socket
import subprocess
import time
import signal
import sys
from pathlib import Path
from typing import Any, Optional


class LmuxError(Exception):
    """Error from lmux daemon."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class LmuxClient:
    """Client for lmux daemon over Unix domain socket."""

    def __init__(self, socket_path: Optional[str] = None, timeout: float = 5.0):
        self.socket_path = socket_path or os.environ.get(
            "LMUX_SOCKET_PATH", "/tmp/lmux-test.sock"
        )
        self.timeout = timeout
        self._daemon_proc = None

    def _connect(self) -> socket.socket:
        """Connect to the daemon socket."""
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(self.timeout)
        s.connect(self.socket_path)
        return s

    def send(self, cmd: str, args: Optional[dict] = None) -> dict:
        """Send a command and return parsed response."""
        payload = json.dumps({"cmd": cmd, "args": args or {}}) + "\n"
        with self._connect() as s:
            s.sendall(payload.encode())
            chunks = []
            while True:
                try:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                except socket.timeout:
                    break
                except OSError:
                    break
            raw = b"".join(chunks)
        if not raw:
            return {"ok": False, "error": "empty response"}
        return json.loads(raw.decode().strip())

    def assert_ok(self, cmd: str, args: Optional[dict] = None) -> dict:
        """Send command and assert success. Returns the result."""
        resp = self.send(cmd, args)
        if not resp.get("ok"):
            err = resp.get("error", {})
            # Handle both structured dict errors and plain string errors
            if isinstance(err, dict):
                raise LmuxError(
                    err.get("code", "unknown"),
                    err.get("message", "no message"),
                )
            else:
                raise LmuxError("unknown", str(err))
        return resp.get("result", {})

    # ── Workspace operations ─────────────────────────────────

    def workspace_create(self, title: str = "test-ws") -> dict:
        return self.assert_ok("workspace.create", {"title": title})

    def workspace_list(self) -> list:
        return self.assert_ok("workspace.list").get("workspaces", [])

    def workspace_close(self, ws_id: int) -> dict:
        return self.assert_ok("workspace.close", {"id": str(ws_id)})

    def workspace_select(self, ws_id: int) -> dict:
        return self.assert_ok("workspace.select", {"id": str(ws_id)})

    def workspace_current(self) -> dict:
        return self.assert_ok("workspace.current")

    def workspace_rename(self, ws_id: int, title: str) -> dict:
        return self.assert_ok("workspace.rename", {"id": str(ws_id), "title": title})

    def workspace_reorder(self, from_idx: int, to_idx: int) -> dict:
        return self.assert_ok("workspace.reorder", {"from_index": str(from_idx), "to_index": str(to_idx)})

    def workspace_ssh(self, target: str, port: int = 22) -> dict:
        return self.assert_ok("ssh", {"target": target, "port": str(port)})

    # ── Surface operations ───────────────────────────────────

    def surface_create(self, ws_id: int, title: str = "test-surface") -> dict:
        return self.assert_ok("surface.create", {"workspace_id": str(ws_id), "title": title})

    def surface_list(self) -> list:
        return self.assert_ok("surface.list").get("surfaces", [])

    def surface_close(self, surface_id: int, ws_id: Optional[int] = None) -> dict:
        args = {"id": str(surface_id)}
        if ws_id is not None:
            args["workspace_id"] = str(ws_id)
        return self.assert_ok("surface.close", args)

    def surface_focus(self, surface_id: int, ws_id: Optional[int] = None) -> dict:
        args = {"id": str(surface_id)}
        if ws_id is not None:
            args["workspace_id"] = str(ws_id)
        return self.assert_ok("surface.focus", args)

    def surface_send_text(self, text: str) -> dict:
        return self.assert_ok("surface.send_text", {"text": text})

    def surface_split(self, direction: str = "h") -> dict:
        return self.assert_ok("surface.split", {"direction": direction})

    # ── Pane operations ──────────────────────────────────────

    def pane_list(self) -> list:
        return self.assert_ok("pane.list").get("panes", [])

    def pane_focus(self, pane_id: int, ws_id: Optional[int] = None) -> dict:
        args = {"id": str(pane_id)}
        if ws_id is not None:
            args["workspace_id"] = str(ws_id)
        return self.assert_ok("pane.focus", args)

    def pane_resize(self, direction: str, cols: int = 0, rows: int = 0) -> dict:
        return self.assert_ok("pane.resize", {
            "direction": direction,
            "cols": str(cols),
            "rows": str(rows),
        })

    # ── Copy mode operations ──────────────────────────────────

    def pane_copy_mode_enter(self) -> dict:
        return self.assert_ok("pane.copy_mode.enter")

    def pane_copy_mode_exit(self) -> dict:
        return self.assert_ok("pane.copy_mode.exit")

    def pane_copy_mode_move(self, key: str) -> dict:
        return self.assert_ok("pane.copy_mode.move", {"key": key})

    def pane_copy_mode_select_start(self) -> dict:
        return self.assert_ok("pane.copy_mode.select_start")

    def pane_copy_mode_select_end(self) -> dict:
        return self.assert_ok("pane.copy_mode.select_end")

    def pane_copy_mode_yank(self) -> dict:
        return self.assert_ok("pane.copy_mode.yank")

    def pane_copy_mode_paste(self) -> dict:
        return self.assert_ok("pane.copy_mode.paste")

    # ── Notification operations ──────────────────────────────

    def notification_create(self, text: str) -> dict:
        return self.assert_ok("notification.create", {"text": text})

    def notification_list(self) -> list:
        return self.assert_ok("notification.list").get("notifications", [])

    def notification_clear(self) -> dict:
        return self.assert_ok("notification.clear")

    # ── Session operations ───────────────────────────────────

    def session_save(self) -> dict:
        return self.assert_ok("session.save")

    def session_restore(self) -> dict:
        return self.assert_ok("session.restore")

    def snapshot_save(self, path: Optional[str] = None) -> dict:
        args = {}
        if path:
            args["path"] = path
        return self.assert_ok("snapshot.save", args)

    def snapshot_load(self, path: Optional[str] = None) -> dict:
        args = {}
        if path:
            args["path"] = path
        return self.assert_ok("snapshot.load", args)

    # ── Config operations ────────────────────────────────────

    def config_get(self, key: Optional[str] = None) -> dict:
        args = {}
        if key:
            args["key"] = key
        return self.assert_ok("config.get", args)

    def config_set(self, key: str, value: str) -> dict:
        return self.assert_ok("config.set", {"key": key, "value": value})

    # ── Agent operations ─────────────────────────────────────

    def agent_spawn(self, name: str, command: str, ws_id: Optional[int] = None) -> dict:
        args = {"name": name, "command": command}
        if ws_id is not None:
            args["workspace_id"] = str(ws_id)
        return self.assert_ok("agent.spawn", args)

    def agent_list(self) -> list:
        return self.assert_ok("agent.list").get("agents", [])

    def agent_stop(self, agent_id: int) -> dict:
        return self.assert_ok("agent.stop", {"id": str(agent_id)})

    # ── File explorer operations ─────────────────────────────

    def file_explorer_open(self, path: Optional[str] = None) -> dict:
        args = {}
        if path:
            args["path"] = path
        return self.assert_ok("file_explorer.open", args)

    def file_explorer_navigate(self, path: str) -> dict:
        return self.assert_ok("file_explorer.navigate", {"path": path})

    def file_explorer_list(self) -> dict:
        return self.assert_ok("file_explorer.list")

    def file_explorer_refresh(self) -> dict:
        return self.assert_ok("file_explorer.refresh")

    def file_explorer_filter(self, filter_str: str) -> dict:
        return self.assert_ok("file_explorer.filter", {"filter": filter_str})

    def file_explorer_sort(self, mode: str) -> dict:
        return self.assert_ok("file_explorer.sort", {"mode": mode})

    def file_explorer_open_file(self, name: str) -> dict:
        return self.assert_ok("file_explorer.open_file", {"name": name})

    def file_explorer_create_dir(self, name: str) -> dict:
        return self.assert_ok("file_explorer.create_dir", {"name": name})

    def file_explorer_delete(self, name: str) -> dict:
        return self.assert_ok("file_explorer.delete", {"name": name})

    def file_explorer_rename(self, old_name: str, new_name: str) -> dict:
        return self.assert_ok("file_explorer.rename", {"old_name": old_name, "new_name": new_name})

    def file_explorer_search(self, query: str) -> dict:
        return self.assert_ok("file_explorer.search", {"query": query})

    def file_explorer_close(self) -> dict:
        return self.assert_ok("file_explorer.close")

    # ── Utility operations ───────────────────────────────────

    def tree(self) -> dict:
        return self.assert_ok("tree")

    def display_message(self, message: str) -> dict:
        return self.assert_ok("display-message", {"message": message})

    def ping(self) -> dict:
        return self.send("ping")

    def capabilities(self) -> dict:
        return self.assert_ok("capabilities")


class LmuxDaemon:
    """Manage an lmux daemon process for testing."""

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or f"/tmp/lmux-test-{os.getpid()}.sock"
        self._proc = None
        self._cli_path = None

    def start(self, timeout: float = 5.0) -> LmuxClient:
        """Start the lmux daemon and return a connected client."""
        # Find the lmux binary
        repo_root = Path(__file__).parent.parent
        build_dir = repo_root / "build"
        self._cli_path = build_dir / "lmux"

        if not self._cli_path.exists():
            raise FileNotFoundError(
                f"lmux binary not found at {self._cli_path}. "
                "Run: node scripts/build-core.mjs && node scripts/build-cli.mjs"
            )

        # Start daemon — redirect stdout/stderr to /dev/null so the daemon
        # never blocks on pipe writes.  The daemon's output is only
        # informational and not needed for test assertions.
        self._proc = subprocess.Popen(
            [str(self._cli_path), "--socket", self.socket_path, "daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for socket to appear
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(self.socket_path):
                # Try connecting
                try:
                    s = socket.socket(socket.AF_UNIX)
                    s.settimeout(1)
                    s.connect(self.socket_path)
                    s.close()
                    return LmuxClient(self.socket_path)
                except (ConnectionRefusedError, FileNotFoundError):
                    pass
            time.sleep(0.1)

        raise TimeoutError(f"Daemon did not start within {timeout}s")

    def stop(self):
        """Stop the daemon process."""
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

        # Clean up socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    def __enter__(self):
        self.client = self.start()
        return self.client

    def __exit__(self, *args):
        self.stop()

    def __del__(self):
        self.stop()
