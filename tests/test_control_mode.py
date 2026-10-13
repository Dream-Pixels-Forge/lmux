#!/usr/bin/env python3
"""test_control_mode.py — ISS-0029 control-mode subscription protocol (Extend).

tmux-compatible bidirectional control channel over the Unix socket:

  - `%begin <epoch> <seq> 0` / `%end` / `%error` framing per command
  - async `%layout-change`, `%session-changed`, `%window-pane-changed`,
    `%output` notifications (never inside a %begin/%end block)
  - `refresh-client -B name:what:format` subscriptions (+ unsubscribe)
  - framing layer reuses the JSON dispatcher; non-control one-shot
    clients stay byte-identical (no `%` lines).

TDD RED: fails pre-fix (server closes after one response, no framing).
TDD GREEN: control.attach keeps the connection open with %blocks.
"""

import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lmux import LmuxClient, LmuxDaemon


class ControlClient:
    """Persistent control-mode connection (attach -C equivalent)."""

    def __init__(self, socket_path, timeout=10.0):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(timeout)
        self.sock.connect(socket_path)
        self.lines: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        self._buf = b""
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Enter control mode. The attach reply itself is %framed.
        self.send({"cmd": "control.attach", "args": {}})

    def _read_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    chunk = self.sock.recv(65536)
                except (socket.timeout, OSError):
                    continue
                if not chunk:
                    break
                self._buf += chunk
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    self.lines.put(line.decode(errors="replace"))
        except OSError:
            pass

    def send(self, obj):
        payload = json.dumps(obj) + "\n"
        self.sock.sendall(payload.encode())

    def send_raw(self, line):
        self.sock.sendall((line + "\n").encode())

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                return out

    def wait_for(self, prefix, timeout=8.0):
        """Wait for a line starting with prefix; return it."""
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(line)
            if line.startswith(prefix):
                return line, seen
        raise AssertionError(
            f"timed out waiting for {prefix!r}; saw {len(seen)} lines: "
            f"{seen[-8:]}"
        )

    def run_block(self, obj, timeout=8.0):
        """Send a command; collect lines until the closing %end/%error."""
        self.drain()
        self.send(obj)
        block = []
        deadline = time.time() + timeout
        opened = False
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            block.append(line)
            if line.startswith("%begin"):
                opened = True
            elif line.startswith(("%end", "%error")) and opened:
                return block
        raise AssertionError(f"unterminated %block for {obj}: {block[-8:]}")

    def close(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


class TestControlMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._home = tempfile.mkdtemp(prefix="lmux-ctl-home-")
        Path(cls._home, ".local", "share", "lmux").mkdir(
            parents=True, exist_ok=True
        )
        env = dict(os.environ)
        env["HOME"] = cls._home
        env["XDG_DATA_HOME"] = str(Path(cls._home, ".local", "share"))
        env["XDG_CONFIG_HOME"] = str(Path(cls._home, ".config"))
        Path(cls._home, ".config").mkdir(parents=True, exist_ok=True)
        cls._sock = f"/tmp/lmux-ctl-{os.getpid()}.sock"
        Path(cls._sock).unlink(missing_ok=True)
        cls._daemon = LmuxDaemon(cls._sock, env=env)
        cls._probe = cls._daemon.start(timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls._daemon.stop()

    def test_01_control_framing_begin_end(self):
        """control.attach keeps the connection open; commands are %framed."""
        ctl = ControlClient(self._sock)
        try:
            # Attach ack is itself a framed block.
            line, _ = ctl.wait_for("%begin")
            self.assertIn("%begin", line)
            line, _ = ctl.wait_for("%end", timeout=8.0)
            self.assertTrue(line.startswith("%end"))

            block = ctl.run_block(
                {"cmd": "workspace.create", "args": {"title": "ctl-ws"}}
            )
            self.assertTrue(block[0].startswith("%begin"),
                            f"block must open with %begin: {block}")
            self.assertTrue(block[-1].startswith("%end"),
                            f"block must close with %end: {block}")
            payload = [ln for ln in block if ln.startswith("{")]
            self.assertTrue(payload, f"%block carries JSON payload: {block}")
            self.assertIn('"ok":true', payload[0])
        finally:
            ctl.close()

    def test_02_layout_change_on_split_and_close(self):
        """%layout-change fires on split + close."""
        ctl = ControlClient(self._sock)
        try:
            ctl.wait_for("%begin")
            ctl.wait_for("%end")
            ctl.drain()
            ctl.send({"cmd": "workspace.create",
                      "args": {"title": "layout-ws"}})
            # Consume the create block + its %session-changed noise.
            time.sleep(1.0)
            ctl.drain()
            # Explicitly focus the just-created workspace; surface targets
            # default to the focused workspace, which may be stale.
            block = ctl.run_block({"cmd": "workspace.list", "args": {}})
            payload = [ln for ln in block if ln.startswith("{") and "layout-ws" in ln]
            wsid = str(json.loads(payload[0])["result"]["workspaces"][-1]["id"])
            ctl.run_block({"cmd": "workspace.select", "args": {"id": wsid}})
            ctl.send({"cmd": "surface.split",
                      "args": {"direction": "horizontal", "workspace_id": wsid}})
            line, _ = ctl.wait_for("%layout-change", timeout=8.0)
            self.assertIn("%layout-change", line)

            ctl.drain()
            # Close one surface -> layout changes again.
            ctl.send({"cmd": "surface.list", "args": {"workspace_id": wsid}})
            time.sleep(1.0)
            ctl.drain()
            ctl.send({"cmd": "surface.close", "args": {"workspace_id": wsid}})
            # surface.close without id closes... may error; either way the
            # daemon must stay framed. If it errors we still get %error.
            deadline = time.time() + 8.0
            saw_layout = False
            saw_close = False
            while time.time() < deadline and not (saw_layout and saw_close):
                try:
                    ln = ctl.lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ln.startswith("%layout-change"):
                    saw_layout = True
                if ln.startswith(("%end", "%error")):
                    saw_close = True
            self.assertTrue(saw_layout,
                            "surface.close must emit %layout-change")
        finally:
            ctl.close()

    def test_03_refresh_client_subscription_filter(self):
        """refresh-client -B filters async notifications per subscription."""
        ctl = ControlClient(self._sock)
        try:
            ctl.wait_for("%begin")
            ctl.wait_for("%end")
            ctl.run_block({"cmd": "workspace.create",
                           "args": {"title": "sub-ws"}})
            # Focus the workspace so default surface targeting works.
            block = ctl.run_block({"cmd": "workspace.list", "args": {}})
            payload = [ln for ln in block if ln.startswith("{") and "sub-ws" in ln]
            wsid = str(json.loads(payload[0])["result"]["workspaces"][-1]["id"])
            ctl.run_block({"cmd": "workspace.select", "args": {"id": wsid}})
            # Subscribe to layout notifications only.
            block = ctl.run_block(
                {"cmd": "refresh-client",
                 "args": {"subscription": "lo:layout:fmt"}})
            self.assertTrue(block[0].startswith("%begin"))
            ctl.drain()

            # A split must still surface %layout-change ...
            ctl.send({"cmd": "surface.split",
                      "args": {"direction": "horizontal", "workspace_id": wsid}})
            ctl.wait_for("%layout-change", timeout=8.0)

            ctl.drain()
            # ... but %output must stay filtered out.
            ctl.send({"cmd": "surface.send_text",
                      "args": {"text": "hello-filtered", "workspace_id": wsid}})
            deadline = time.time() + 3.0
            leaked = []
            while time.time() < deadline:
                try:
                    ln = ctl.lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ln.startswith("%output"):
                    leaked.append(ln)
            self.assertEqual(leaked, [],
                             f"layout-only sub leaked %output: {leaked}")

            # Re-subscribe (same name replaces `lo`) to an output-only filter:
            # layout notifications must now be filtered out. With no
            # subscriptions at all, every class is delivered (documented
            # semantics), so assert the filter, not the absence of subs.
            ctl.run_block({"cmd": "refresh-client",
                           "args": {"subscription": "lo:output:fmt"}})
            ctl.drain()
            ctl.send({"cmd": "surface.split",
                      "args": {"direction": "vertical", "workspace_id": wsid}})
            deadline = time.time() + 3.0
            leaked = []
            while time.time() < deadline:
                try:
                    ln = ctl.lines.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ln.startswith("%layout-change"):
                    leaked.append(ln)
            self.assertEqual(leaked, [],
                             f"output-only sub leaked %layout-change: {leaked}")
        finally:
            ctl.close()

    def test_04_non_control_protocol_byte_identical(self):
        """One-shot JSON clients never see `%` framing lines."""
        client = LmuxClient(self._sock)
        resp = client.send("workspace.create", {"title": "plain-ws"})
        self.assertTrue(resp.get("ok"), resp)
        # Raw socket level: response must be a single JSON line.
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect(self._sock)
        try:
            s.sendall(b'{"cmd":"workspace.list","args":{}}\n')
            chunks = []
            while True:
                try:
                    c = s.recv(65536)
                except socket.timeout:
                    break
                if not c:
                    break
                chunks.append(c)
        finally:
            s.close()
        raw = b"".join(chunks).decode().strip()
        self.assertTrue(raw.startswith("{"),
                        f"one-shot reply must be pure JSON, got: {raw[:120]}")
        self.assertNotIn("%begin", raw)
        self.assertNotIn("%layout-change", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
