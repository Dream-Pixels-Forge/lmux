# Handoff — lmux @ main

**Date:** 2026-11-10
**Branch state:** `main` @ `0a3fd37` (PR #65), CI green (CI, Nightly Build,
Dependency Scanning)
**Version:** 1.2.0 (an Unreleased section in CHANGELOG.md collects everything below)
**Tests:** 428 integration (5 skipped), C unit suites incl. `test_json_fuzz`
(149 commands fuzzed for valid-JSON replies/events), sanitizer run clean (no
ASan/UBSan errors, no leaks), GUI tests run under Xvfb.

---

## Where things stand

Merged since the previous handoff (oldest first):

| PR | What |
|----|------|
| #62 | Shortcuts no longer eat capital letters (GDK `0x2` is Caps Lock, not Ctrl); agent notifications reach the sidebar (`pane_id` attribution, per-workspace unread/waiting) |
| #63 | **Daemon crash**: one request with a `\uXXXX` escape at the end of a field overflowed a stack buffer and killed `lmuxd` and every pane. JSON helpers moved to `src/core/json.c` (bounds-safe, surrogate pairs, keys never matched inside values); event-stream payloads escaped; socket auth vs `geteuid()`; 256-client cap; HTTP log-server fixes. **GUI**: the window can be resized (min 1121×698 → 447×148); browser panel actually hides; canvas toggle works |
| #54 | Copy mode (vi/emacs key tables, `send-keys -X`) — merged with current main |
| #57 | Session groups + linked windows — sanitizer leaks fixed (`lmux_app_free` never freed session groups) |
| #58 | `wait-for` barriers + `command-error` hook |
| #59 | **Closed, superseded** — everything it adds was already on main |
| #64 | Every command reply JSON-escapes user strings (112 were raw); replies grow to fit via `RESULT_SETF`/`RESULT_APPENDF` (the old `written += snprintf` could overflow the heap); `JESC` no longer prints one value twice; `events` `after_seq` injection closed |
| #65 | "Auto-save session" actually controls restore and save-on-quit; `lmux workspace.list` lists every workspace; the duplicate "Files" sidebar tab removed |

This branch (next PR) — "remaining items" pass:

- **Pane exit under the daemon's SIGCHLD handler.** The handler reaped every
  child with `waitpid(-1)`, so `check_pane_child_exit()` got `ECHILD`: in the
  real daemon `pane-died`/`pane-exited` hooks never fired and exit codes were
  lost (unit tests don't install the handler, which is why nothing caught it).
  The handler now only wakes the loop; `lmux_app_tick()` checks each pane's
  own child first, then `reap_stray_children()` peeks with `WNOWAIT` and reaps
  only children no pane owns (pipe-pane, notify-send, resumed agents).
- **Snapshot strings.** `snap_read_string` now decodes `\uXXXX` (control
  characters in titles used to come back as the text `u0007`), truncates an
  over-long value at a whole UTF-8 character, and logs a warning instead of
  cutting silently.

## How to work on it

- `node scripts/build-core.mjs && node scripts/build-cli.mjs` (or `make build`).
  Rebuild the CLI whenever you rebuild the core (static-library trap).
- Fast checks: `node scripts/test.mjs --unit`, then `--sanitize --unit`.
  Full gate: `python3 tests/test_integration.py` (~65 s).
- GUI tests need a display: `xvfb-run -a python3 tests/test_gui_startup.py`.
  On a box without GTK bindings they skip, which looks like a pass.
- **Command replies**: build them with `RESULT_SETF(...)` / `RESULT_APPENDF(...)`
  and wrap every user string in `lmux_json_esc()` (`JESC()` is the same thing).
  `test_json_fuzz` will fail a new handler that forgets either.
- **Children**: don't install a SIGCHLD reaper and don't `waitpid(-1)`; the tick
  owns reaping (see `reap_stray_children`). Code that forks and waits for its
  own child must do so under the model lock, like the tick.
- Local cppcheck over `model.c` takes many minutes; CI's `lint` job runs it.

## Open items

None blocking. Optional polish, not done:
- The sidebar's minimum width (~316 px) is set by the extension tab switcher.
- `lmux_json_esc()` slots are 4 KiB, so a single escaped value over ~4 KiB is
  truncated (cleanly, never mid-escape) in a reply.
- The JSON fuzz test skips ~100 commands with outside effects (hooks, agents,
  ssh, browser, file writes, send-keys); their reply code was fixed by the
  static scan but is not exercised end to end.
- tmux-style hooks (`pane-died`, `pane-exited`, `monitor-*`) can only be
  registered through the C API (`lmux_hook_add`); there is no socket command.

## Practices now enforced

- Run `make test`, `make test-fuzz`, `make test-unit` **serially** — and never
  start a second Python integration runner while one is live (they share
  socket paths).
- `git fetch` before any `merge --ff-only origin/main`; verify the sha.
- If you rebuild the core you **must** rebuild the CLI (stale-static-library
  trap).
- `goal-loop` after every goal: `goal-writer` → execute (TDD) → `goal-met`.

## Environment notes (developer machine, from the 2026-10-05 session)

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

## Known gaps

1. ~~Restored panes get no pty at all.~~ **Resolved** (verified 2026-11-10): a
   restored pane's shell is spawned on first use (#61's lazy spawn in
   `pane_view_attach` / send-keys); `send-keys` + `read-screen` on a restored
   pane returns the command's output.
2. ~~`snap_read_string` clamps at `cap` silently.~~ **Fixed**: truncation is
   logged and never splits a UTF-8 character; `\uXXXX` is decoded.
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
