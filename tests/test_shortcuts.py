#!/usr/bin/env python3
"""
test_shortcuts.py — default GUI shortcuts must never steal normal typing.

The defaults used modifier bits 0x1|0x2, intended as Ctrl+Shift. In GDK
0x2 is LOCK_MASK (Caps Lock), not Control (0x4), and GTK ignores Lock when
matching accelerators, so every shortcut fired on plain Shift+letter.
Typing a capital T in a terminal opened a workspace, W closed a tab,
X closed a pane and Q quit the app.

Run:
    python3 tests/test_shortcuts.py -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

import shortcuts  # noqa: E402

SHIFT, LOCK, CONTROL, ALT = 0x1, 0x2, 0x4, 0x8


class DefaultShortcutTests(unittest.TestCase):
    def _all_keys(self):
        for s in shortcuts.DEFAULT_SHORTCUTS:
            for keyval, mods in s.keys:
                yield s.action, keyval, mods

    def test_no_default_uses_caps_lock(self):
        bad = [(a, hex(m)) for a, _, m in self._all_keys() if m & LOCK]
        self.assertEqual(bad, [], "LOCK_MASK (0x2) is Caps Lock, not Control")

    def test_every_default_needs_ctrl_or_alt(self):
        """Without Ctrl/Alt a shortcut swallows keys the terminal needs."""
        bad = [(a, hex(m)) for a, _, m in self._all_keys()
               if not m & (CONTROL | ALT)]
        self.assertEqual(bad, [])

    def test_letter_shortcuts_are_ctrl_shift(self):
        """Terminal convention (and cmux-style): Ctrl+Shift+<letter>, so
        plain Ctrl+<letter> still reaches the shell (Ctrl+W, Ctrl+T...)."""
        bad = [(a, chr(k), hex(m)) for a, k, m in self._all_keys()
               if chr(k).isalpha() if k < 0x100
               and (m & (CONTROL | SHIFT)) != (CONTROL | SHIFT)]
        self.assertEqual(bad, [])

    def test_no_two_actions_share_a_key(self):
        seen = {}
        dupes = []
        for a, k, m in self._all_keys():
            if (k, m) in seen:
                dupes.append((seen[(k, m)], a))
            seen[(k, m)] = a
        self.assertEqual(dupes, [])

    def test_defaults_do_not_clash_with_hardcoded_accelerators(self):
        """Other modules (session_reopen, font_size...) bind keys directly
        with Gtk.accelerator_parse; a default must not reuse one."""
        import re
        gui = Path(__file__).resolve().parent.parent / "gui"
        mods_of = {"<Ctrl>": CONTROL, "<Shift>": SHIFT, "<Alt>": ALT}
        taken = {}
        for f in gui.glob("*.py"):
            if f.name == "shortcuts.py":
                continue
            for spec in re.findall(r'accelerator_parse\("([^"]+)"\)', f.read_text()):
                m = 0
                for tok, bit in mods_of.items():
                    if tok in spec:
                        m |= bit
                key = re.sub(r"<[^>]+>", "", spec)
                if len(key) == 1:
                    taken[(ord(key.lower()), m)] = f"{f.name}:{spec}"
        clashes = [(a, taken[(k, m)]) for a, k, m in self._all_keys()
                   if (k, m) in taken]
        self.assertEqual(clashes, [])

    def test_stale_saved_lock_masks_are_repaired_on_load(self):
        """A shortcuts.json saved by the buggy version holds 0x3 masks; load
        must not resurrect the Shift-steals-typing behaviour."""
        loaded = shortcuts.Shortcut.from_dict(
            {"action": "app.quit", "keys": [{"keyval": ord("q"),
                                             "modifiers": SHIFT | LOCK}]})
        (_, mods), = loaded.keys
        self.assertFalse(mods & LOCK)
        self.assertEqual(mods & (CONTROL | SHIFT), CONTROL | SHIFT)


if __name__ == "__main__":
    unittest.main()
