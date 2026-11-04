#!/usr/bin/env python3
"""
test_daemon_autostart.py — DaemonClient.ensure_daemon() starts the daemon
when its socket is missing, so the GUI no longer opens with an empty
sidebar unless the user ran `lmux daemon` first.

Uses a fake `lmux` binary (a tiny Python Unix-socket listener) so the test
needs no build and no display.

Run:
    python3 tests/test_daemon_autostart.py -v
"""

import os
import signal
import socket
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

from daemon_client import DaemonClient  # noqa: E402

FAKE_LMUX = textwrap.dedent("""\
    #!{python}
    import os, socket, sys, time
    args = sys.argv[1:]
    path = args[args.index("--socket") + 1]
    assert args[-1] == "daemon", args
    with open(os.environ["FAKE_PIDFILE"], "w") as f:
        f.write(str(os.getpid()))
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)
    s.listen(8)
    end = time.time() + 30
    s.settimeout(0.2)
    while time.time() < end:
        try:
            c, _ = s.accept(); c.close()
        except socket.timeout:
            pass
""")


class EnsureDaemonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.tmp.name)
        self.sock = str(root / "d.sock")
        self.pidfile = root / "fake.pid"
        self.fake = root / "lmux"
        self.fake.write_text(FAKE_LMUX.format(python=sys.executable))
        self.fake.chmod(0o755)
        self._env = {k: os.environ.get(k) for k in
                     ("LMUX_BIN", "LMUX_NO_AUTOSTART", "FAKE_PIDFILE",
                      "XDG_STATE_HOME")}
        os.environ["LMUX_BIN"] = str(self.fake)
        os.environ["FAKE_PIDFILE"] = str(self.pidfile)
        os.environ["XDG_STATE_HOME"] = str(root / "state")
        os.environ.pop("LMUX_NO_AUTOSTART", None)

    def tearDown(self):
        if self.pidfile.exists():
            try:
                os.kill(int(self.pidfile.read_text()), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_starts_daemon_when_socket_missing(self):
        client = DaemonClient(self.sock)
        self.assertTrue(client.ensure_daemon(timeout=5))
        self.assertTrue(self.pidfile.exists(), "fake lmux was not launched")
        self.assertTrue(client.is_daemon_alive())
        log = Path(os.environ["XDG_STATE_HOME"]) / "lmux" / "daemon.log"
        self.assertTrue(log.exists(), "daemon output should go to a log file")

    def test_does_not_spawn_when_daemon_already_running(self):
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(self.sock)
        srv.listen(1)
        try:
            client = DaemonClient(self.sock)
            self.assertTrue(client.ensure_daemon(timeout=2))
            self.assertFalse(self.pidfile.exists(), "must not start a second daemon")
        finally:
            srv.close()

    def test_opt_out_env_disables_autostart(self):
        os.environ["LMUX_NO_AUTOSTART"] = "1"
        client = DaemonClient(self.sock)
        self.assertFalse(client.ensure_daemon(timeout=1))
        self.assertFalse(self.pidfile.exists())

    def test_missing_binary_returns_false(self):
        os.environ["LMUX_BIN"] = str(Path(self.tmp.name) / "does-not-exist")
        client = DaemonClient(self.sock)
        # Must not raise; PATH lookup may still find a real lmux, so only
        # assert the call is safe when LMUX_BIN points nowhere and PATH is empty.
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            self.assertFalse(client.ensure_daemon(timeout=1))
        finally:
            os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
