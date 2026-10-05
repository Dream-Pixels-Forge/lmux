# Handoff — lmux @ master

**Date:** 2026-10-05
**Branch state:** `master` @ `a87ee34` (PR #33), clean, in sync with origin;
this session's work on `fix/flatpak-ci-job`
**Version:** 1.1.2
**Tests:** 309 integration (306 + 3 new) + 23 model + 8 osc + 12 config unit,
fuzz 0 errors — all re-run fresh for the goal-met audit (gate PASS, 10/10).
Sanitizer jobs run in CI only; not re-run locally this session.

---

## Where things stand

Merged since the previous handoff:

| PR | Commit | What |
|----|--------|------|
| #32 | `3b2f156` | flatpak runtime 46 → 50 (GNOME 46 EOL was 2025-04-17); cp313 wheel pins + loud Python preflight assertion |
| #33 | `a87ee34` | the previous handoff document |
| #34 | `2717706` | rate limiter guarded with a mutex |
| #35 | `22cf3ce` | updater rejects tar members escaping the extraction directory |

This session executed the flatpak-CI open item (spec:
`dev-notes/GOAL_FLATPAK_CI.md`, verdict MET via goal-loop):

- **CI now builds the flatpak manifest.** New `flatpak` job in
  `.github/workflows/ci.yml`: installs the toolchain, adds Flathub (user),
  caches `~/.local/share/flatpak` keyed on the manifest with a fallback
  restore key, runs `flatpak-builder --user … packaging/io.github.lmux.lmux.yml`,
  then installs the result and smoke-tests `flatpak run … --version`. The
  root-cause fix for all three #31 defects.
- **`make flatpak` pointed at a manifest that does not exist**
  (`packaging/io.lmux.lmux.yml`) — it never could have worked. Now points at
  the real file.
- **`packaging/flatpak-wrapper.sh` still installed runtime 46** after PR #32
  moved to 50. It now derives `RUNTIME_VERSION` from the manifest's
  `runtime-version` key and fails loudly if it cannot read it.
- Three new tests in `TestPackagingEntryPoints` pin all three (TDD: RED
  reproduced independently against pristine `HEAD` in a throwaway worktree).

## master was red at session start — fixed

`test_no_stale_github_repo_url` failed on `master` because the PR #33 handoff
**itself** contained the literal old-org URL twice, in prose about the bug —
the exact anti-pattern the previous handoff warned about (a test grepping its
own documentation). Fixed by rewording the two lines; the test is untouched at
full strength.

**CI never caught it: `ci.yml` runs `node scripts/test.mjs --unit` only — the
309-test Python suite runs solely via `make test`, locally.**

## Proven this session (evidence, not theory)

1. **Two concurrent integration runners destroy each other.** The runner's
   `__main__` block unlinks *all* `/tmp/lmux-integration-*.sock` on startup —
   so a second runner deletes a live runner's active socket. A/B experiment:
   runner A started, runner B at t+45 s → **A: FAILED (errors=60, all
   `FileNotFoundError` from that instant on), B: OK (309)**. An audit run
   failed this way while the RED-reproduction runner ran concurrently — the
   red was an artifact of the audit procedure, not the deliverable.
   **Never run two suites at once** (the python-side twin of the ETXTBSY
   rule). Fix candidate: only unlink sockets that refuse a connection.
2. **Every integration run leaks one `lmux-ptyd-*` daemon** — green exit or
   SIGKILL alike (filed as issue #37; 14 orphans from Oct 4–5 were found and
   killed, all with dead owner PIDs). Normal exit does clean up the
   `lmux-integration-*` and `lmux-iso-*` daemons; `kill -9` strands those too.
   The concurrent-runner socket unlink (Proven #1) is issue #38.

## Behaviour changes users may notice

- `make flatpak` now actually builds (it referenced a nonexistent manifest
  before); output dir unchanged: `dist/flatpak/build`.
- `packaging/flatpak-wrapper.sh` installs whatever runtime the manifest
  declares instead of a hardcoded one.
- CI has a new `flatpak` job; PRs now build and smoke-test the flatpak.

## Practices now enforced

- Run `make test`, `make test-fuzz`, `make test-unit` **serially** — and never
  start a second Python integration runner while one is live (Proven #1).
- `git fetch` before any `merge --ff-only origin/master`; verify the sha.
- If you rebuild the core you **must** rebuild the CLI (stale-static-library
  trap).
- `goal-loop` after every goal: `goal-writer` → execute (TDD) → `goal-met`.

## Open items

1. **Duplicate `flathub` remotes** (unchanged; needs sudo → owner decision):
   `sudo apt install flatpak-builder`, then `flatpak uninstall --user
   --unused`, `flatpak uninstall --user org.flatpak.Builder`,
   `flatpak remote-delete --user flathub`.
2. **Memorius is still not exposed** in this session either — §5/§7 of the
   workspace mandate remain manually unsatisfied; record the summary by hand.
3. **The Python integration suite is not in CI.** The 309 tests exist, run
   green locally, and are the only full gate. Adding them to CI is a natural
   next goal (needs the daemon + CLI built first).
4. **Harness defects filed as issues #37 (ptyd daemon leak per run) and
   #38 (second runner unlinks live sockets)** — both need their own RED tests.

## Environment notes

- Flatpak toolchain is installed **user-level** (`org.flatpak.Builder` +
  GNOME 46 and 50 SDKs, ~9 GB in `~/.local/share/flatpak`); `appimagetool` is
  in `~/.local/bin/`. The manifest build cache in CI is cold the first time.
- **Seven `lmux-ptyd-*` daemons from Oct 4** are still running (pre-existing).

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

**Reproduce before believing** — and when an audit fails but the diff cannot
explain it, **audit the audit**. This session's one red run was caused by the
verification procedure itself (a concurrent runner), proven with a two-process
A/B experiment rather than argued from code reading.

Second habit, unchanged: **verify the verifier.** The RED check was re-run in
a pristine worktree instead of trusting the first capture, and the stale-URL
failure was confirmed against pristine `HEAD` before any fix was attempted.
