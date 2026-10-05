# Handoff — lmux @ master

**Date:** 2026-10-05 (evening update — memorius session)
**Branch state:** `master` @ `4a50768` (PR #39), clean, in sync with origin
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
| #36 | `f28331a` | **the flatpak-CI goal** — CI builds the manifest; `make flatpak` manifest path fixed; wrapper runtime derived from manifest; 3 new tests (goal-loop MET, 10/10) |
| #39 | `4a50768` | handoff correction (orphan-daemon claim) + issues #37/#38 filed |

Previous session executed the flatpak-CI open item (spec:
`dev-notes/GOAL_FLATPAK_CI.md`, verdict MET via goal-loop):

- **CI now builds the flatpak manifest.** New `flatpak` job in
  `.github/workflows/ci.yml`: installs the toolchain, adds Flathub (user),
  caches `~/.local/share/flatpak` keyed on the manifest with a fallback
  restore key, runs `flatpak-builder --user … packaging/io.github.lmux.lmux.yml`,
  then installs the result and smoke-tests `flatpak run … --version`. The
  root-cause fix for all three #31 defects. The new job caught a real bug on
  its first live run (broken relative-path `flatpak install` usage) — fixed
  and re-verified in CI.
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
2. **The Python integration suite is not in CI.** The 309 tests exist, run
   green locally, and are the only full gate. Adding them to CI is a natural
   next goal (needs the daemon + CLI built first).
3. **Harness defects filed as issues #37 (ptyd daemon leak per run) and
   #38 (second runner unlinks live sockets)** — both OPEN, both need their
   own RED tests.
4. **No stray processes left** — verified `no_stray_processes` before writing
   this handoff (all 14 Oct 4–5 orphans killed earlier).

## Environment notes

- Flatpak toolchain is installed **user-level** (`org.flatpak.Builder` +
  GNOME 46 and 50 SDKs, ~9 GB in `~/.local/share/flatpak`); `appimagetool` is
  in `~/.local/bin/`. The manifest build cache in CI is cold the first time.
- **Memorius is FIXED and working** (see § Memorius below). 19 vaults,
  276 memories, embeddings up.
- pipx memorius venv is 6.1 GB (torch/CUDA wheels) — do not delete it
  thinking it is bloat.

## Memorius — repaired this session ✅

Why it was broken: (1) pipx held a stale 0.2.0 venv whose symlinks were
squatted on by old `pip install --user` wrappers, so `pipx reinstall` errored
and the shell silently used the pip copy; (2) Cline's MCP settings file had
no memorius entry, so
the server was never spawned.

What was done (PyPI package only — the sibling `memorius` checkout on this
machine is a dev
tree and was deliberately NOT used):

- Clean `pipx install memorius` → **0.8.2** (latest on PyPI), proper
  symlinks, `memorius status` shows the vault intact.
- `memorius serve` smoke-tested over stdio: handshake → `tools/list`
  (28 tools) → `tools/call memorius_status` returns live data; CLI semantic
  search verified (torch path works).
- MCP registered in the Cline MCP settings file alongside brandly:
  `"memorius": {"transport": {"type": "stdio", "command":
  "~/.local/bin/memorius", "args": ["serve"]}}`.

Torch question, answered: torch (~800 MB, hard dep via
`memorius → sentence-transformers>=2.6.0 → torch>=2.2`) is the runtime for
the local embedding model behind semantic search — not optional in the
published package (unlike the dev repo's `local-embeddings` extra).

⚠️ **After restarting the session/IDE: the Cline extension must reload to
pick up the new MCP entry.** If `memorius_*` tools are still absent, restart
the extension host. Verify with `memorius_status` as the first call.

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
