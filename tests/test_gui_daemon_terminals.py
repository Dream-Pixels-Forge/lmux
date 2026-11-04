#!/usr/bin/env python3
"""
test_gui_daemon_terminals.py — the GUI renders the daemon's panes.

Before this, gui/terminal.py spawned its own shell per VTE while the daemon
ran a separate set of ptys, so `lmux send-keys` / `read-screen` /
`surface.split` acted on terminals nobody could see. These tests start a
real daemon, build the main window against it and check that:

  * the visible panes are the daemon's (same ids, VTE attached to the
    daemon pty via pane.attach / SCM_RIGHTS);
  * output written by the daemon shows in the VTE and keystrokes typed in
    the VTE reach the daemon pane;
  * a split made from the CLI (as from a shell inside a pane) appears in
    the window, and GUI splits / new tabs are created in the daemon.

Requires a display (X11/Wayland) and build/lmux; skipped otherwise.

Run:
    python3 tests/test_gui_daemon_terminals.py -v
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gui"))
sys.path.insert(0, str(ROOT / "tests"))

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
LMUX_BIN = ROOT / "build" / "lmux"

if HAS_DISPLAY:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Vte", "2.91")
    from gi.repository import Gtk, Vte

from lmux import LmuxDaemon  # noqa: E402


def _pump(cond, timeout=8.0):
    """Run the GTK loop until cond() is truthy or timeout; return cond()."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    return cond()


def _vte_text(vte):
    try:
        return vte.get_text_format(Vte.Format.TEXT) or ""
    except (AttributeError, TypeError):
        res = vte.get_text()
        return (res[0] if isinstance(res, tuple) else res) or ""


@unittest.skipUnless(HAS_DISPLAY and LMUX_BIN.exists(),
                     "needs a display and build/lmux")
class GuiRendersDaemonPanes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lmux-gui-own-")
        home = Path(self.tmp.name)
        (home / ".local" / "share" / "lmux").mkdir(parents=True)
        (home / ".config").mkdir()
        self.sock = str(home / "lmux.sock")
        env = dict(os.environ)
        env.update({
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            # /bin/sh: zsh's new-user wizard would eat the first keystroke.
            "SHELL": "/bin/sh",
        })
        env.pop("LMUX_PANE_ID", None)
        self.daemon = LmuxDaemon(self.sock, env=env)
        self.c = self.daemon.start(timeout=30)

        self._saved = {k: os.environ.get(k) for k in
                       ("HOME", "LMUX_SOCKET_PATH", "LMUX_NO_AUTOSTART")}
        os.environ["HOME"] = str(home)
        os.environ["LMUX_SOCKET_PATH"] = self.sock
        os.environ["LMUX_NO_AUTOSTART"] = "1"
        import main
        self.win = main.LmuxWindow()
        self.view = _pump(lambda: next(iter(self.win._workspace_map.values()), None))
        self.assertIsNotNone(self.view, "window should show the current workspace")

    def tearDown(self):
        try:
            self.win.destroy()
            _pump(lambda: False, 0.1)
        finally:
            for k, v in self._saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            self.daemon.stop()
            self.tmp.cleanup()

    def _daemon_panes(self):
        ws = self.view.ws_id
        return self.c.assert_ok("pane.list", {"workspace_id": str(ws)})["panes"]

    def _container(self):
        return self.view.notebook.current_container()

    def test_visible_pane_is_the_daemon_pane(self):
        self.assertTrue(self.view.daemon_backed)
        daemon_ids = sorted(p["id"] for p in self._daemon_panes())
        self.assertEqual(sorted(self._container().pane_ids()), daemon_ids)
        term = self._container().focused_terminal()
        self.assertTrue(term.attached, "VTE must render the daemon's pty")

    def test_daemon_output_shows_in_vte(self):
        term = self._container().focused_terminal()
        self.c.assert_ok("surface.send_text", {
            "pane_id": str(term.pane_id), "text": "echo SHOWN_$((20+22))\n"})
        self.assertTrue(_pump(lambda: "SHOWN_42" in _vte_text(term.vte)),
                        _vte_text(term.vte)[-400:])

    def test_keys_typed_in_vte_reach_daemon(self):
        term = self._container().focused_terminal()
        _pump(lambda: False, 0.3)  # let VTE size the pty
        term.vte.feed_child(b"echo TYPED_$((3*3))\n")

        def screen_has():
            r = self.c.send("read-screen", {"pane_id": str(term.pane_id)})
            return "TYPED_9" in r.get("result", {}).get("text", "")
        self.assertTrue(_pump(screen_has))

    def test_cli_split_appears_in_window(self):
        first = self._container().pane_ids()[0]
        env = dict(os.environ)
        env.update({"LMUX_SOCKET_PATH": self.sock, "LMUX_PANE_ID": str(first)})
        r = subprocess.run([str(LMUX_BIN), "surface.split", "v"], env=env,
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(_pump(lambda: self._container().pane_count() == 2),
                        "CLI split should show up in the GUI")
        daemon_ids = sorted(p["id"] for p in self._daemon_panes())
        self.assertEqual(sorted(self._container().pane_ids()), daemon_ids)
        new_term = [t for t in self._container().iter_terminals()
                    if t.pane_id != first][0]
        self.assertTrue(new_term.attached)

    def test_lmux_run_inside_visible_pane_acts_on_it(self):
        """The acceptance scenario: a shell in a GUI pane runs `lmux`."""
        term = self._container().focused_terminal()
        _pump(lambda: False, 0.3)
        term.vte.feed_child(
            f"{LMUX_BIN} send-keys 'echo INSIDE_$((5+5))' Enter\n".encode())
        self.assertTrue(_pump(lambda: "INSIDE_10" in _vte_text(term.vte)),
                        _vte_text(term.vte)[-400:])
        term.vte.feed_child(f"{LMUX_BIN} read-screen > /dev/null && "
                            f"{LMUX_BIN} surface.split v\n".encode())
        self.assertTrue(_pump(lambda: self._container().pane_count() == 2),
                        "split typed in the pane should split the window")

    def test_gui_split_is_created_in_daemon(self):
        self.view.split_pane("h")
        gui_ids = sorted(self._container().pane_ids())
        self.assertEqual(len(gui_ids), 2)
        self.assertEqual(gui_ids, sorted(p["id"] for p in self._daemon_panes()))
        # The echoed pane.created event must not add a duplicate.
        _pump(lambda: False, 0.5)
        self.assertEqual(self._container().pane_count(), 2)

    def test_gui_new_tab_is_daemon_surface(self):
        self.view.create_surface()
        _pump(lambda: False, 0.3)
        surfaces = self.c.assert_ok(
            "surface.list", {"workspace_id": str(self.view.ws_id)})["surfaces"]
        self.assertEqual(sorted(s["id"] for s in surfaces),
                         sorted(self.view.notebook._pages.keys()))

    def test_shell_exit_closes_pane(self):
        self.view.split_pane("h")
        self.assertEqual(self._container().pane_count(), 2)
        term = [t for t in self._container().iter_terminals()][-1]
        self.c.assert_ok("surface.send_text",
                         {"pane_id": str(term.pane_id), "text": "exit\n"})
        self.assertTrue(_pump(lambda: self._container().pane_count() == 1))
        self.assertNotIn(term.pane_id, [p["id"] for p in self._daemon_panes()])


@unittest.skipUnless(HAS_DISPLAY, "needs a display for GTK")
class GuiWithoutDaemon(unittest.TestCase):
    def test_offline_falls_back_to_local_shell(self):
        import main
        tmp = tempfile.TemporaryDirectory()
        saved = os.environ.get("LMUX_SOCKET_PATH")
        os.environ["LMUX_SOCKET_PATH"] = os.path.join(tmp.name, "none.sock")
        try:
            view = main.WorkspaceView(1, "offline", main.DaemonClient(),
                                      daemon_backed=False)
            self.assertFalse(view.daemon_backed)
            term = view.notebook.current_container().focused_terminal()
            self.assertFalse(term.attached)
            view.destroy()
        finally:
            if saved is None:
                os.environ.pop("LMUX_SOCKET_PATH", None)
            else:
                os.environ["LMUX_SOCKET_PATH"] = saved
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
