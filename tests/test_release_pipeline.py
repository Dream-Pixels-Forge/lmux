#!/usr/bin/env python3
"""test_release_pipeline.py — ISS-0044 content-assertion gates for the release
pipeline.

The release workflow and signing workflow are *content*, not code: there is no
runtime to exercise locally. The honest test is a content assertion — read the
files as fixtures and pin down the safety properties so a future edit that
silently deletes them fails CI:

- release.yml asserts tag == package.json version (fail-fast identity guard)
- release.yml runs the Python integration suite (like ci.yml's `integration`
  job, `python3 tests/test_integration.py`)
- release.yml produces sha256sums.txt for the built assets
- sign.yml signs the release *assets* (not a nonexistent container image) and
  falls back to a loud "artifacts unsigned" warning instead of silently no-op
- build-appimage.sh declares the binaries it actually uses (rsync, curl) in
  check_prereqs with a clear install hint

Pure-read tests: no daemon, no network.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(relpath: str) -> str:
    path = ROOT / relpath
    assert path.is_file(), f"missing fixture: {relpath}"
    return path.read_text(encoding="utf-8")


class TestReleaseVersionGuard(unittest.TestCase):
    """release.yml must fail fast when the tag disagrees with package.json."""

    @classmethod
    def setUpClass(cls):
        cls.RELEASE_YML = _read(".github/workflows/release.yml")

    def test_version_guard_step_exists(self):
        self.assertIn(
            "git describe --tags --exact-match",
            self.RELEASE_YML,
            "release.yml must verify the pushed tag is an exact match",
        )
        self.assertIn(
            "package.json",
            self.RELEASE_YML,
            "release.yml must compare the tag against the package.json version",
        )

    def test_guard_fails_on_mismatch(self):
        # The guard must *fail*, not just print.
        step = re.search(
            r"name: Verify tag matches package\.json[\s\S]+?(?=\n      - )",
            self.RELEASE_YML,
        )
        assert step is not None, (
            "release.yml must have a 'Verify tag matches package.json' step"
        )
        self.assertIn(
            "exit 1",
            step.group(0),
            "version guard must exit non-zero on mismatch, not warn",
        )


class TestReleaseIntegrationSuite(unittest.TestCase):
    """release.yml must run the full Python integration suite (ISS-0044 #3)."""

    @classmethod
    def setUpClass(cls):
        cls.RELEASE_YML = _read(".github/workflows/release.yml")

    def test_runs_python_integration_suite(self):
        self.assertIn(
            "python3 tests/test_integration.py",
            self.RELEASE_YML,
            "release.yml must run the Python integration suite like ci.yml",
        )


class TestReleaseChecksums(unittest.TestCase):
    """release.yml must publish sha256sums.txt alongside the assets."""

    @classmethod
    def setUpClass(cls):
        cls.RELEASE_YML = _read(".github/workflows/release.yml")

    def test_generates_sha256sums(self):
        self.assertIn(
            "sha256sums.txt",
            self.RELEASE_YML,
            "release.yml must produce a sha256sums.txt for release assets",
        )
        self.assertIn(
            "sha256sum",
            self.RELEASE_YML,
            "release.yml must actually compute sha256 sums",
        )

    def test_checksums_uploaded_as_release_asset(self):
        files_block = re.search(
            r"files: \|[^\n]*\n(?:\s+[^\n]*\n)*", self.RELEASE_YML
        )
        assert files_block is not None, "release.yml must upload files"
        self.assertIn(
            "sha256sums.txt",
            files_block.group(0),
            "sha256sums.txt must be uploaded with the release assets",
        )


class TestSignWorkflow(unittest.TestCase):
    """sign.yml must sign real assets, not a container that never exists."""

    @classmethod
    def setUpClass(cls):
        cls.SIGN_YML = _read(".github/workflows/sign.yml")

    def test_no_dead_container_signing(self):
        self.assertNotIn(
            "Sign container image",
            self.SIGN_YML,
            "sign.yml must not reference a nonexistent container image",
        )

    def test_signs_release_assets(self):
        self.assertIn(
            "sha256sums.txt",
            self.SIGN_YML,
            "sign.yml must sign the released asset set (incl. sha256sums.txt)",
        )
        self.assertIn(
            "deb",
            self.SIGN_YML,
            "sign.yml must sign the .deb asset",
        )

    def test_uses_oidc_keyless_or_secret_key(self):
        self.assertIn(
            "id-token: write",
            self.SIGN_YML,
            "keyless cosign signing requires id-token: write permission",
        )
        self.assertIn(
            "COSIGN_KEY",
            self.SIGN_YML,
            "sign.yml must support a COSIGN_KEY secret fallback",
        )

    def test_loud_warning_when_unsigned(self):
        self.assertIn(
            "artifacts unsigned",
            self.SIGN_YML,
            "sign.yml must print a loud 'artifacts unsigned' warning instead of "
            "silently skipping",
        )


class TestAppImagePrereqs(unittest.TestCase):
    """build-appimage.sh check_prereqs must list every binary it uses."""

    @classmethod
    def setUpClass(cls):
        cls.SCRIPT = _read("packaging/build-appimage.sh")

    def test_prereqs_mention_rsync_and_curl(self):
        block = re.search(r"check_prereqs\(\) \{[\s\S]*?\n\}", self.SCRIPT)
        assert block is not None, "check_prereqs function not found"
        body = block.group(0)
        self.assertIn(
            "rsync",
            body,
            "check_prereqs must verify rsync (used to sync AppDir)",
        )
        self.assertIn(
            "curl",
            body,
            "check_prereqs must verify curl (used to fetch appimagetool)",
        )

    def test_prereqs_fail_with_install_hint(self):
        block = re.search(r"check_prereqs\(\) \{[\s\S]*?\n\}", self.SCRIPT)
        self.assertIn(
            "apt-get install",
            block.group(0),
            "check_prereqs failure must include a concrete install hint",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
