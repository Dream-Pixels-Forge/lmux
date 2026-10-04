# Handoff — lmux @ master

**Date:** 2026-10-04
**Branch state:** `master` @ `48e2a49`, clean, in sync with origin
**Open PR:** #32 — flatpak runtime 46 → 50 (built and verified; ready to merge)
**Version:** 1.1.2
**Tests:** 306 integration + 23 model + 8 osc + 12 config unit. ASan/UBSan
clean, fuzz clean, 4 compiler warnings (== baseline, unchanged all session).

---

## Where things stand

Seven PRs merged this session, all on `master`:

| PR | Commit | What |
|----|--------|------|
| #24 | `f227082` | `package.json` key lookup was pattern-matching, so a nested *or value* `"name"` shadowed the real one; `naming.suggest` cache served stale results |
| #25 | `a5c1a71` | GUI died on every read-only install; `package.json` advertised 5 scripts that never existed; the canonical AppImage builder could not build at all |
| #26 | `5dad08e` | 9 dead `github.com/lmux/lmux` references, incl. the clone URL and the security-disclosure link |
| #27 | `1e3a643` | flatpak AppStream metainfo had no `<launchable>`; added that file's first automated check |
| #28 | `39b3ca9` | packagers shipped **GUI** as `lmux`; now CLI is `lmux`, GUI is `lmux-gui` |
| #30 | `a3c7086` | OCI registry namespace aligned to the GitHub org |
| #31 | `48e2a49` | flatpak manifest could not build: dead `--build-arg`, no network for pip, wrong wheel tags |

All three packaging formats are now verified **end-to-end** for the first time:

```
deb       → dpkg-deb -c shows ./usr/bin/lmux -> ./usr/lib/lmux/build/lmux + lmux-gui
AppImage  → builds, launches, stays up (exit 124), 0 read-only errors
flatpak   → builds, installs, `flatpak run --version` → lmux version 1.1.2
```

## Behaviour changes users may notice

- **`lmux` now means the CLI** for deb and AppImage installs; the GUI is
  `lmux-gui`. This matches what README, CONTRIBUTING, CHANGELOG and
  `gui/main.py` had always claimed, and what the flatpak manifest already did.
  **Breaking for existing deb users**, where `lmux` opened the GUI and the CLI
  was `lmux-cli` (which no longer exists).
- `naming.suggest` no longer returns a nested or value-level `package.json`
  `"name"`, and no longer serves a cached result after a branch switch.
- Version is **1.1.2**.

## Practices now enforced

- Run `make test`, `make test-fuzz`, `make test-unit` **serially**. In parallel
  they relink the same binaries and fail `ETXTBSY` — an audit artifact that
  looks like a product failure. This caused one real misdiagnosis.
- **A stale git ref will silently fast-forward to the wrong place.**
  `git merge --ff-only origin/master` without a preceding `git fetch` merged
  against a cached ref and produced a branch missing a just-merged PR. Fetch
  first, then verify the resulting sha.
- If you rebuild the core you **must** rebuild the CLI; it links the static
  library, and tests silently run against a stale binary otherwise.
- `goal-loop` after every goal: `goal-writer` → execute (TDD) → `goal-met`.

## Three times a result looked right and wasn't

The most valuable lesson of the session. Each would have shipped.

1. **Fixed the instance, not the class.** The wrong-repo URL was corrected in
   the AppImage metainfo and left in place in nine other files — including the
   security-disclosure link. An independent audit caught it. Same shape as the
   original `strstr` bug in #24.
2. **A test that could not go green.** The first stale-URL test put the literal
   `github.com/lmux/lmux` in its own docstring and grep argument, so `git grep`
   matched its own source. Separately, a naming assertion was written against
   install *tokens* rather than paths and would have forced the comment
   explaining a rename out of the file.
3. **A verification script that lied.** After the flatpak fix, a one-liner
   printed `launchable = None` on a correct file: the *attribute* is
   `type="desktop-id"` and the desktop file is the element's **text**. Verified
   against `cat -A` and raw validator output rather than my own summary.

Also: the file **reader normalizes leading whitespace**, which made two edits
fail against text that looked correct on screen. Use `cat -A` for exact bytes.

## Open items

1. **PR #32** — flatpak runtime 46 → 50. Built and verified locally. Needs
   merge. It also corrects a date error in #31's description: the EOL date is
   **April 17, 2025**, not 2026.
2. **The flatpak path is not in CI.** All three defects in #31 survived because
   nothing ever built it. A flatpak build job is the real root-cause fix.
3. **Duplicate `flathub` remotes.** A user-scoped one was added to obtain
   `flatpak-builder` without sudo; bare `flatpak` commands now prompt for which
   installation to use. It **cannot** be deleted while the toolchain is
   installed from it (all 19 refs list it as origin). To resolve:
   `sudo apt install flatpak-builder`, then
   `flatpak uninstall --user --unused`,
   `flatpak uninstall --user org.flatpak.Builder`,
   `flatpak remote-delete --user flathub`.
4. **Memorius was never written** — no `memorius_*` tool is exposed in this
   session, so §5/§7 of the workspace mandate is unsatisfied. The session
   summary and lessons need recording manually.

## Environment changes made here

- `appimagetool` installed to `~/.local/bin/` (needed for AppImage builds).
- Flatpak toolchain installed **user-level**: `org.flatpak.Builder` plus the
  GNOME 46 and 50 SDKs — **~9 GB** in `~/.local/share/flatpak`.
- `io.github.lmux.lmux` installed as a user flatpak.
- `sudo dpkg --purge lmux` run by the owner: old deb fully removed.

## Known gaps, deliberately left open

1. **Restored panes get no pty at all.** `lmux_pane_split()` is called during
   restore with `spawn_pty = false`. Predates the placeholder-fork fix and is
   not covered by it. May be intentional (the host attaches panes later) or may
   mean a restored terminal is dead. **Uninvestigated.**
2. **`snap_read_string` clamps at `cap` silently.** No worse than before, but an
   over-long value is dropped without warning.
3. **The GNOME runtime needs periodic attention.** Wheel tags are pinned to the
   SDK's Python and cannot be derived automatically — hence the preflight
   assertion in the manifest, which fails loudly and tells the next person to
   re-probe. It works, but it is a recurring tax.

## Working method that worked

**Reproducing before believing**, at every scale. The flatpak work is the
clearest case: the manifest had never been built in this environment and failed
three separate times — a dead `--build-arg`, `pip` with no network in the build
sandbox, and wheels tagged for a Python the SDK does not ship. All three are
invisible to inspection, to the unit suite, and to `appstreamcli validate`. None
would ever have been found without running the thing.

The second habit: **verify the verifier.** Independent audits returned NOT-MET
twice and were right both times. They also returned NOT-MET once for a
pre-existing defect that was correctly out of scope, and said so plainly when
asked to separate signal from noise.