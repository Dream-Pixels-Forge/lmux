#!/usr/bin/env python3
"""
test_custom_commands_security.py — shell=False default + env strip
tests for gui/custom_commands.py (ISS-0016).

Custom commands run config-supplied strings.  The old code passed them
to ``Popen(..., shell=True)`` with the full ambient environment plus an
unfiltered per-command overlay (LD_PRELOAD injection, ; && chaining).

Fixed behavior:

  * default: ``shlex.split`` + ``shell=False`` (no shell metachars);
  * opt-in ``{"shell": true, "confirm": true}`` runs through a REAL
    confirmation callback; ``shell=true`` without ``confirm=true``
    (or a declined/unwired prompt) refuses;
  * dangerous env keys (LD_PRELOAD, LD_LIBRARY_PATH, PYTHONPATH,
    BASH_ENV, ENV) are stripped from the ambient base AND cannot be
    reintroduced via the per-command overlay.

Run:
    python3 tests/test_custom_commands_security.py
    python3 tests/test_custom_commands_security.py -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Add gui/ to path so we can import custom_commands standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

from custom_commands import CustomCommandsManager, CustomCommand


def _manager():
    tmp = tempfile.mkdtemp(prefix="lmux-ccmd-test-")
    return CustomCommandsManager(config_dir=tmp)


class TestShellFalseDefault(unittest.TestCase):
    """Compound commands without opt-in must run argv-split, no shell."""

    def test_compound_command_runs_argv_split_without_shell(self):
        mgr = _manager()
        mgr._commands["chain"] = CustomCommand(
            name="chain", command="make build && echo hi")
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(mgr.execute_command("chain"))
        self.assertEqual(popen.call_count, 1)
        _, kwargs = popen.call_args
        self.assertFalse(
            kwargs.get("shell"),
            "default execution must not use a shell")
        argv = popen.call_args[0][0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], "make")

    def test_simple_command_is_split(self):
        mgr = _manager()
        mgr._commands["st"] = CustomCommand(
            name="st", command="git status --short")
        with mock.patch("subprocess.Popen") as popen:
            mgr.execute_command("st")
        argv = popen.call_args[0][0]
        self.assertEqual(argv, ["git", "status", "--short"])


class TestShellOptIn(unittest.TestCase):
    """shell:true requires confirm:true plus a REAL approval."""

    def test_shell_opt_in_without_confirm_refuses(self):
        mgr = _manager()
        mgr._commands["evil"] = CustomCommand(
            name="evil", command="rm -rf ~",
            shell=True, confirm=False)
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(
                mgr.execute_command(
                    "evil", confirm_callback=lambda cmd: True),
                "shell opt-in without confirm=true must refuse")
        popen.assert_not_called()

    def test_shell_opt_in_without_callback_refuses(self):
        mgr = _manager()
        mgr._commands["sh"] = CustomCommand(
            name="sh", command="echo hi",
            shell=True, confirm=True)
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(
                mgr.execute_command("sh"),
                "shell opt-in with no prompt wired must refuse")
        popen.assert_not_called()

    def test_shell_opt_in_declined_refuses(self):
        mgr = _manager()
        mgr._commands["sh"] = CustomCommand(
            name="sh", command="echo hi",
            shell=True, confirm=True)
        cb = mock.Mock(return_value=False)
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(mgr.execute_command("sh", confirm_callback=cb))
        popen.assert_not_called()
        cb.assert_called_once()

    def test_shell_opt_in_approved_runs_with_shell(self):
        mgr = _manager()
        mgr._commands["sh"] = CustomCommand(
            name="sh", command="echo hi",
            shell=True, confirm=True)
        cb = mock.Mock(return_value=True)
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(mgr.execute_command("sh", confirm_callback=cb))
        cb.assert_called_once()
        _, kwargs = popen.call_args
        self.assertTrue(kwargs.get("shell"))
        self.assertEqual(popen.call_args[0][0], "echo hi")

    def test_confirm_dialog_invoked_with_command(self):
        """The REAL dialog receives the command for review."""
        mgr = _manager()
        cmd = CustomCommand(
            name="sh", command="deploy --prod",
            shell=True, confirm=True)
        mgr._commands["sh"] = cmd
        seen = []
        with mock.patch("subprocess.Popen"):
            mgr.execute_command(
                "sh", confirm_callback=lambda c: seen.append(c) or True)
        self.assertEqual(seen, [cmd])


class TestEnvStrip(unittest.TestCase):
    """Dangerous env keys must not reach the child."""

    DANGEROUS = ["LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH",
                 "BASH_ENV", "ENV"]

    def test_ambient_dangerous_env_stripped(self):
        mgr = _manager()
        mgr._commands["x"] = CustomCommand(name="x", command="env")
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "/tmp/evil.so",
                                          "PYTHONPATH": "/tmp/evil"}):
            with mock.patch("subprocess.Popen") as popen:
                mgr.execute_command("x")
        env = popen.call_args[1]["env"]
        for key in self.DANGEROUS:
            self.assertNotIn(key, env)

    def test_overlay_cannot_reintroduce_dangerous_env(self):
        mgr = _manager()
        mgr._commands["x"] = CustomCommand(
            name="x", command="env",
            env={"LD_PRELOAD": "/tmp/evil.so", "FOO": "bar"})
        with mock.patch("subprocess.Popen") as popen:
            mgr.execute_command("x")
        env = popen.call_args[1]["env"]
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env.get("FOO"), "bar")

    def test_safe_overlay_keys_pass_through(self):
        mgr = _manager()
        mgr._commands["x"] = CustomCommand(
            name="x", command="env",
            env={"MYAPP_MODE": "test"})
        with mock.patch("subprocess.Popen") as popen:
            mgr.execute_command("x")
        self.assertEqual(
            popen.call_args[1]["env"].get("MYAPP_MODE"), "test")


class TestConfigRoundTrip(unittest.TestCase):
    """The shell flag persists through config load/save."""

    def test_shell_flag_round_trips(self):
        mgr = _manager()
        mgr.add_command(CustomCommand(
            name="sh", command="echo hi", shell=True, confirm=True))
        mgr2 = CustomCommandsManager(
            config_dir=str(mgr._config_dir))
        cmd = mgr2.get_command("sh")
        self.assertTrue(cmd.shell)
        self.assertTrue(cmd.confirm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
