#!/usr/bin/env python3
"""
test_updater_security.py — Security regression tests for gui/updater.py.

These tests cover the tarball extraction path in SparkleUpdater, which
consumes an archive downloaded from a remote update feed.  A malicious or
compromised feed must not be able to write outside the extraction
directory.

Run:
    python3 tests/test_updater_security.py
    python3 tests/test_updater_security.py -v
"""

import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

# Add gui/ to path so we can import updater without pulling in GTK.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

from updater import SparkleUpdater


def _make_tar(path, entries):
    """Build a tar archive at *path* from ``[(name, content), ...]``."""
    with tarfile.open(str(path), "w") as tf:
        for name, content in entries:
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return path


class TestTarExtractionEscape(unittest.TestCase):
    """The extraction path-escape check must reject every escaping member."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.install = self.root / "install"
        self.install.mkdir()
        self.updater = SparkleUpdater()

    def tearDown(self):
        self._tmp.cleanup()

    def _apply(self, entries):
        archive = _make_tar(self.root / "update.tar", entries)
        return self.updater.apply_update(str(archive), str(self.install))

    # ------------------------------------------------------------------
    # T1: sibling-prefix escape (the bypass that motivated this suite).
    # ------------------------------------------------------------------
    def test_sibling_prefix_escape_is_rejected(self):
        """A member escaping into a name-sharing sibling dir must be refused.

        ``_apply_tarball`` extracts to ``<install>/.extract_<stem>`` and used a
        ``str.startswith`` containment test.  ``/a/.extract_x_evil`` passes a
        ``startswith("/a/.extract_x")`` check while being outside the tree.
        """
        stem = "update"
        sibling = f"../.extract_{stem}_evil/pwned.txt"
        escaped = self.root / f".extract_{stem}_evil" / "pwned.txt"

        result = self._apply([(sibling, "owned")])

        self.assertFalse(
            escaped.exists(),
            "path-escape: file was written outside the extraction dir",
        )
        self.assertFalse(result, "apply_update must report failure on escape")

    # ------------------------------------------------------------------
    # T2: plain parent-directory traversal.
    # ------------------------------------------------------------------
    def test_parent_traversal_is_rejected(self):
        """A member containing ``../`` must not land outside the target."""
        escaped = self.root / "pwned.txt"
        result = self._apply([("../pwned.txt", "owned")])

        self.assertFalse(
            escaped.exists(),
            "path-escape: ../ member escaped the extraction dir",
        )
        self.assertFalse(result, "apply_update must report failure on escape")

    # ------------------------------------------------------------------
    # T3: absolute paths must not be honoured.
    # ------------------------------------------------------------------
    def test_absolute_path_member_is_rejected(self):
        """An absolute member name must not write to that absolute location."""
        target = self.root / "absolute_pwned.txt"
        result = self._apply([(str(target), "owned")])

        self.assertFalse(
            target.exists(),
            "absolute path member was written outside the extraction dir",
        )
        self.assertFalse(result, "apply_update must report failure on escape")

    # ------------------------------------------------------------------
    # T4: a symlink pointing outside must not be created.
    # ------------------------------------------------------------------
    def test_symlink_escape_is_rejected(self):
        """A symlink whose target is outside the tree must be refused.

        Even with a safe member *name*, the link target can escape.  Writing
        the link is what matters here; extraction of a following member could
        then write through it.
        """
        archive = self.root / "link.tar"
        with tarfile.open(str(archive), "w") as tf:
            info = tarfile.TarInfo("evil-link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)

        result = self.updater.apply_update(str(archive), str(self.install))

        link = self.install / ".extract_update" / "evil-link"
        self.assertFalse(
            link.exists() or link.is_symlink(),
            "symlink escape: link to /etc/passwd was created",
        )
        self.assertFalse(result, "apply_update must report failure on symlink")

    # ------------------------------------------------------------------
    # T5 (control): benign archives must still install successfully.
    # ------------------------------------------------------------------
    def test_benign_archive_still_installs(self):
        """The security check must not break normal updates."""
        result = self._apply([("app", "binary"), ("README.md", "docs")])

        self.assertTrue(result, "a benign archive must still install")
        self.assertTrue(
            (self.install / "app").exists(),
            "benign member should have been installed",
        )


class TestUnsignedUpdatesRejected(unittest.TestCase):
    """ISS-0001: an update with no verifiable signature must be rejected.

    ``verify_signature`` used to return ``True`` when no signature was
    supplied ("accept silently but log warning"), so a compromised feed
    could push unsigned payloads that the dialog path then accepted.
    Verification must fail closed.
    """

    def test_empty_signature_is_rejected(self):
        from updater import verify_signature
        self.assertFalse(verify_signature(b"payload bytes", "", None))

    def test_garbage_signature_is_rejected(self):
        from updater import verify_signature
        self.assertFalse(verify_signature(b"payload bytes", "not-hex!!", None))

    def test_wrong_sha256_is_rejected(self):
        from updater import verify_signature
        self.assertFalse(verify_signature(b"payload bytes", "00" * 32, None))

    def test_correct_sha256_still_accepted(self):
        """Fail-closed must not break the SHA-256 fallback for good sigs."""
        import hashlib
        from updater import verify_signature
        data = b"legit payload"
        sig = hashlib.sha256(data).hexdigest()
        self.assertTrue(verify_signature(data, sig, None))


class TestInstallScriptNotAutoExecuted(unittest.TestCase):
    """ISS-0001: ``apply_update`` must not auto-run ``install.sh`` (RCE).

    A feed-supplied archive is untrusted input.  Executing a script from
    it without explicit opt-in turns a feed compromise into remote code
    execution.  Extraction/copy must still succeed; execution requires
    ``allow_exec=True``.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.install = self.root / "install"
        self.install.mkdir()
        self.updater = SparkleUpdater()
        self.marker = self.root / "PWNED_MARKER"

    def tearDown(self):
        self._tmp.cleanup()

    def _apply(self, entries, **kwargs):
        archive = _make_tar(self.root / "update.tar", entries)
        return self.updater.apply_update(
            str(archive), str(self.install), **kwargs
        )

    def test_install_sh_is_not_executed_by_default(self):
        script = f"#!/bin/bash\ntouch {self.marker}\n"
        result = self._apply(
            [("pkg/install.sh", script), ("pkg/app", "binary")]
        )

        self.assertTrue(result, "install should still succeed (files copied)")
        self.assertTrue(
            (self.install / "app").exists(),
            "payload files should have been installed",
        )
        self.assertFalse(
            self.marker.exists(),
            "install.sh was executed without opt-in (RCE)",
        )

    def test_top_level_install_sh_is_not_executed_by_default(self):
        script = f"#!/bin/bash\ntouch {self.marker}\n"
        result = self._apply([("install.sh", script), ("app", "binary")])

        self.assertTrue(result, "install should still succeed (files copied)")
        self.assertFalse(
            self.marker.exists(),
            "top-level install.sh was executed without opt-in (RCE)",
        )

    def test_opt_in_exec_still_runs_script(self):
        """Explicit ``allow_exec=True`` preserves the escape hatch."""
        script = f"#!/bin/bash\ntouch {self.marker}\n"
        result = self._apply(
            [("pkg/install.sh", script), ("pkg/app", "binary")],
            allow_exec=True,
        )

        self.assertTrue(result, "opt-in exec install should succeed")
        self.assertTrue(
            self.marker.exists(),
            "allow_exec=True should still run install.sh",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)