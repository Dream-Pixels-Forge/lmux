#!/usr/bin/env python3
"""SSH security gates — ISS-0022 (MITM) + ISS-0019 (sshpass argv leak).

Fast, daemon-free: source assertions on gui/ssh_advanced.py (which needs
Gtk at import time, so it is never imported here) plus command-construction
behavior tests on gui/ssh_client.py (plain stdlib — importable).
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "gui"


class TestSshAdvancedHostKeyChecking(unittest.TestCase):
    """ISS-0022: ssh_advanced must verify host keys (accept-new), never
    disable them (no + /dev/null invited MITM on every connect/scp)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (GUI / "ssh_advanced.py").read_text()

    def test_no_strict_host_key_checking_no(self):
        self.assertNotIn(
            "StrictHostKeyChecking=no", self.src,
            "ssh_advanced must not disable host-key checking (MITM)",
        )

    def test_no_dev_null_known_hosts(self):
        self.assertNotIn(
            "UserKnownHostsFile=/dev/null", self.src,
            "ssh_advanced must persist known hosts, not discard them",
        )

    def test_accept_new_at_both_sites(self):
        n = self.src.count("StrictHostKeyChecking=accept-new")
        self.assertEqual(
            2, n,
            f"both ssh connect() and scp upload sites must pin new keys, found {n}",
        )


class TestSshpassNoArgvLeak(unittest.TestCase):
    """ISS-0019: the password must never travel on the sshpass command
    line (``-p`` leaks via /proc/PID/cmdline to every local user)."""

    @classmethod
    def setUpClass(cls):
        cls.src = (GUI / "ssh_client.py").read_text()
        sys.path.insert(0, str(GUI))
        import ssh_client as mod
        cls.mod = mod
        # sshpass is absent in CI — pin a fake path so the password
        # branch of _build_base_cmd is exercised deterministically.
        cls._saved = mod._SSHPASS_BIN
        mod._SSHPASS_BIN = "/usr/bin/sshpass"

    @classmethod
    def tearDownClass(cls):
        cls.mod._SSHPASS_BIN = cls._saved

    def test_no_dash_p_password_in_source(self):
        self.assertNotIn(
            '"-p", password', self.src,
            "sshpass -p exposes the password on the command line",
        )

    def test_password_never_in_built_argv(self):
        client = self.mod.SSHClient()
        host = self.mod.SSHHost(host="example.com", user="root")
        cmd = client._build_base_cmd(host, password="s3cr3t-pw")
        self.assertNotIn("s3cr3t-pw", cmd,
                         f"password must not appear in argv: {cmd!r}")

    def test_passwordless_build_unchanged(self):
        client = self.mod.SSHClient()
        host = self.mod.SSHHost(host="example.com", user="root")
        cmd = client._build_base_cmd(host)
        self.assertIn("-o", cmd)
        self.assertIn("BatchMode=yes", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)