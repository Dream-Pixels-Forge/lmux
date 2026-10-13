#!/usr/bin/env python3
"""
test_extensions_trust.py — Trust-on-first-use + allowlist tests for
gui/extensions.py (ISS-0017).

Third-party extensions are arbitrary Python executed in the GUI
process.  Minimum shippable trust model:

  * packaged extensions (shipped with the app) load directly;
  * any other directory is scanned only when explicitly allowlisted
    (``extensions.allowlist``), and each extension from it requires
    trust-on-first-use approval ("Enable X?" once/always/never) with
    the file hash pinned in a trust database;
  * a hash mismatch on a pinned extension re-prompts (or refuses when
    no prompt callback is wired — fail closed);
  * per-extension exception isolation in ``notify_event`` is preserved.

Run:
    python3 tests/test_extensions_trust.py
    python3 tests/test_extensions_trust.py -v
"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add gui/ to path so we can import extensions without the full app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

from extensions import ExtensionLoader


EXT_TEMPLATE = '''\
from extensions import ExtensionBase


class {cls}(ExtensionBase):
    name = "{name}"
    description = "test extension"

    def on_event(self, event):
        pass
'''


def _write_ext(directory, fname, cls="MyExt", name="MyExt"):
    path = Path(directory) / fname
    path.write_text(EXT_TEMPLATE.format(cls=cls, name=name))
    return path


class TestExtensionAllowlist(unittest.TestCase):
    """Unlisted third-party dirs must be skipped (ISS-0017)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.user_dir = self.root / "user_ext"
        self.user_dir.mkdir()
        _write_ext(self.user_dir, "myext.py")
        self.pkg_dir = self.root / "pkg_ext"
        self.pkg_dir.mkdir()
        _write_ext(self.pkg_dir, "pkgext.py", cls="PkgExt", name="PkgExt")
        self.trust_db = self.root / "trust.json"

    def test_unlisted_user_dir_is_skipped(self):
        loader = ExtensionLoader(
            extension_dirs=[str(self.user_dir)],
            trusted_dirs=[str(self.pkg_dir)],
            trust_db_path=str(self.trust_db),
            prompt_callback=lambda name, path: "always",
        )
        found = loader.discover()
        self.assertEqual(
            found, [],
            "extension from a non-allowlisted dir must be skipped",
        )

    def test_allowlisted_dir_is_scanned(self):
        loader = ExtensionLoader(
            extension_dirs=[str(self.user_dir)],
            trusted_dirs=[str(self.pkg_dir)],
            allowlist=[str(self.user_dir)],
            trust_db_path=str(self.trust_db),
            prompt_callback=lambda name, path: "always",
        )
        found = loader.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "MyExt")

    def test_packaged_dir_loads_without_prompt(self):
        calls = []
        loader = ExtensionLoader(
            extension_dirs=[str(self.pkg_dir)],
            trusted_dirs=[str(self.pkg_dir)],
            trust_db_path=str(self.trust_db),
            prompt_callback=lambda name, path: calls.append(name) or "always",
        )
        found = loader.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "PkgExt")
        self.assertEqual(
            calls, [], "packaged extensions must not trigger a prompt")


class TestExtensionHashPin(unittest.TestCase):
    """Hash-pin recorded on approval; mismatch re-prompts/refuses."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.user_dir = self.root / "user_ext"
        self.user_dir.mkdir()
        self.ext_path = _write_ext(self.user_dir, "myext.py")
        self.trust_db = self.root / "trust.json"

    def _loader(self, prompt):
        return ExtensionLoader(
            extension_dirs=[str(self.user_dir)],
            trusted_dirs=[],
            allowlist=[str(self.user_dir)],
            trust_db_path=str(self.trust_db),
            prompt_callback=prompt,
        )

    def test_approval_pins_hash_in_trust_db(self):
        loader = self._loader(lambda name, path: "always")
        found = loader.discover()
        self.assertEqual(len(found), 1)
        db = json.loads(self.trust_db.read_text())
        self.assertIn("MyExt", db)
        want = hashlib.sha256(self.ext_path.read_bytes()).hexdigest()
        self.assertEqual(db["MyExt"]["sha256"], want)
        self.assertEqual(db["MyExt"]["policy"], "always")

    def test_hash_mismatch_reprompts(self):
        prompts = []
        loader = self._loader(
            lambda name, path: prompts.append(name) or "always")
        self.assertEqual(len(loader.discover()), 1)
        self.assertEqual(prompts, ["MyExt"])
        # Attacker (or update) modifies the file: hash changes.
        self.ext_path.write_text(
            EXT_TEMPLATE.format(cls="MyExt", name="MyExt")
            + "\n# malicious addition\n")
        loader2 = self._loader(
            lambda name, path: prompts.append(name) or "never")
        found = loader2.discover()
        self.assertEqual(
            found, [],
            "hash mismatch answered 'never' must refuse the extension")
        self.assertEqual(
            prompts, ["MyExt", "MyExt"],
            "hash mismatch must re-prompt, not silently trust")

    def test_hash_mismatch_without_prompt_refuses(self):
        loader = self._loader(lambda name, path: "always")
        self.assertEqual(len(loader.discover()), 1)
        self.ext_path.write_text("# tampered\n")
        noprompt = ExtensionLoader(
            extension_dirs=[str(self.user_dir)],
            trusted_dirs=[],
            allowlist=[str(self.user_dir)],
            trust_db_path=str(self.trust_db),
            prompt_callback=None,  # fail closed
        )
        self.assertEqual(
            noprompt.discover(), [],
            "hash mismatch with no prompt wired must refuse")

    def test_once_policy_prompts_every_time(self):
        prompts = []
        loader = self._loader(
            lambda name, path: prompts.append(name) or "once")
        self.assertEqual(len(loader.discover()), 1)
        loader2 = self._loader(
            lambda name, path: prompts.append(name) or "once")
        self.assertEqual(len(loader2.discover()), 1)
        self.assertEqual(prompts, ["MyExt", "MyExt"])

    def test_always_policy_prompts_once(self):
        prompts = []
        mk = lambda: self._loader(  # noqa: E731 - test helper
            lambda name, path: prompts.append(name) or "always")
        self.assertEqual(len(mk().discover()), 1)
        self.assertEqual(len(mk().discover()), 1)
        self.assertEqual(prompts, ["MyExt"])


class TestExtensionIsolation(unittest.TestCase):
    """Per-extension exception isolation must survive (ISS-0017)."""

    def test_crashing_extension_does_not_break_event_loop(self):
        import extensions as ext_mod
        loader = ExtensionLoader(extension_dirs=[])
        good_events = []

        class Good(ext_mod.ExtensionBase):
            name = "Good"

            def on_event(self, event):
                good_events.append(event)

        class Bad(ext_mod.ExtensionBase):
            name = "Bad"

            def on_event(self, event):
                raise RuntimeError("boom")

        loader._extensions = {"Bad": Bad(), "Good": Good()}
        for ext in loader._extensions.values():
            ext._active = True
        loader.notify_event({"type": "test"})
        self.assertEqual(good_events, [{"type": "test"}])


class TestExtensionDocs(unittest.TestCase):
    """Trust model must be documented (ISS-0017)."""

    def test_security_md_documents_extension_trust(self):
        repo = Path(__file__).resolve().parent.parent
        text = (repo / "SECURITY.md").read_text()
        self.assertIn("extensions.allowlist", text)
        self.assertIn("trust", text.lower())

    def test_config_key_helper_exists(self):
        from extensions import load_extension_allowlist
        import tempfile as tf
        with tf.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps(
                {"extensions": {"allowlist": ["/x"]}}))
            self.assertEqual(
                load_extension_allowlist(str(Path(tmp))),
                ["/x"])
            self.assertEqual(load_extension_allowlist(str(Path(tmp)) + "-nope"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
