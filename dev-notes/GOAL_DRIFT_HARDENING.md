# Goal: Eliminate the drift found in `GOAL_NAMING_HARDENING.md`

## Objective
Close every gap that the independent `goal-met` audit proved still exists in the
`naming.suggest` hardening work, so the goal's own objective ("correct
`package.json` parsing" + daemon/GUI parity) is actually true rather than
merely test-passing.

## Context
`dev-notes/GOAL_NAMING_HARDENING.md` is marked `Verification Results: (pending)`
and its work is uncommitted on `fix/naming-json-depth-and-fork`. A `goal-met`
audit re-ran every listed check and confirmed the literal DoD commands pass
(297 integration tests OK, `make test-fuzz` EXIT=0, ASAN+UBSAN unit EXIT=0, no
new warnings, +11/−0 tests, skips 2→2). But an **independent adversarial probe**
found the objective is still not met. Three concrete drifts:

### DRIFT-1 — the parser still pattern-matches (the goal's own anti-drift rule)
`json_find_toplevel_key()` (`src/core/model.c`, ~2569–2601) accepts **any**
depth-1 string whose content equals the key. It never confirms the string is a
*key* (that a `:` follows). A top-level string **value** equal to `"name"` is
therefore mistaken for the key, `v` lands on a `,`, and
`naming_package_json_name()` bails (`*v != '"'`) — silently dropping the real
name. Reproduced:

```
{"description": "name", "name": "real"}
    gui    = ['real', 'tmp3h54s4z2', 'tmp3h54s4z2 (node)']
    daemon = ['tmp3h54s4z2', 'tmp3h54s4z2 (node)']        # DIVERGE

{"a": "name", "name": "real"}
    gui    = ['real', ...]   daemon = ['tmp0mr6c4p5', ...]  # DIVERGE
```

The goal's Anti-Drift Rules say: **"S1 must parse, not pattern-match."** It
still pattern-matches. This is the same daemon/GUI divergence the goal existed
to remove; the fixtures simply never exercised it.

### DRIFT-2 — the memoisation can change the result (contradicts its own comment)
The S4 cache (`src/core/model.c`, ~821–874) is keyed on `cwd` alone with no
invalidation, yet its comment claims it "must never change the result". If the
directory's git branch changes, or a `package.json` is added/edited, a repeated
`naming.suggest` for the same path returns a **stale** suggestion for the life
of the daemon. No test covers this.

### DRIFT-3 — the sanitizer claim does not cover the new code
DoD bullet 8 claims ASAN+UBSAN `detect_leaks=1` → zero findings, and the goal
notes "S4 adds a cache, so this matters". But no unit test calls
`naming.suggest`, so the `naming_cache_*` path is **never executed** under the
sanitizer run — the claim is vacuous for exactly the code it was raised about.

### DRIFT-4 — the goal doc and repository state are unclosed
`GOAL_NAMING_HARDENING.md` still says `Verification Results: (pending)`, and the
changes are neither committed on the taxonomy branch nor pushed, violating the
workspace end-of-task persistence rule.

## Deliverables
- [x] **D1** — `json_find_toplevel_key()` requires key context: a candidate
      depth-1 string is only accepted when the next non-whitespace char is `:`;
      otherwise scanning continues. (Fixes DRIFT-1.)
- [x] **D2** — Adversarial regression fixtures added to
      `tests/test_integration.py::TestNamingParityWithGui` covering a top-level
      string value equal to `"name"` appearing **before** the real key
      (`{"description": "name", "name": "real"}` and `{"a": "name", "name":
      "real"}`), written RED first.
- [x] **D3** — Cache invalidation with a cheap fingerprint (stat-based: mtime of
      `cwd/package.json` and of the discovered `.git/HEAD`, plus repo path) so a
      cached path whose state changed recomputes; no `git` fork added. (Fixes
      DRIFT-2.)
- [x] **D4** — A unit test (in `tests/test_model.c`, run by
      `scripts/test.mjs`) that calls `dispatch_command(..., "naming.suggest",
      ...)` at least twice, so the memo path executes under the sanitizer build.
      (Fixes DRIFT-3.)
- [x] **D5** — `dev-notes/GOAL_NAMING_HARDENING.md` "Verification Results"
      filled in with the final, re-run numbers.
- [x] **D6** — Changes committed on `fix/naming-json-depth-and-fork`, pushed,
      and a PR opened (or the goal doc explicitly states the correct taxonomy
      branch if the branch name is wrong).

## Definition of Done
- [x] For `{"description": "name", "name": "real"}` and `{"a": "name", "name":
      "real"}`, daemon output **equals** `gui/auto_naming.py::suggest_names()`
- [x] For a cached directory, after `git checkout -q <other-branch>` (or after
      writing a `package.json`), the next `naming.suggest` returns the updated
      suggestion and still equals the GUI's fresh computation
- [x] A top-level `"name"` present **after** such a value is still found
      (no regression on the previously-fixed nested cases)
- [x] `naming.suggest` is executed at least once under
      `scripts/test.mjs --sanitize --unit` (D4 test present and passing)
- [x] `make test` exits 0 (296+ integration tests, `OK`), 0 tests removed
- [x] `make test-fuzz` exits 0 and `make test-unit` exits 0
- [x] ASAN+UBSAN `detect_leaks=1` → zero findings, with the cache path exercised
- [x] Warning count does not exceed the baseline (4) — no new compiler warnings
- [x] No existing test deleted, `.skip()`ed, or weakened (integration test-def
      count ≥ 297; skip calls stay 2)
- [x] Work committed on the taxonomy branch and pushed

## Verification Steps
1. Probe parity directly (the audit's own script shape): start an
   `_isolated_client`, write `{"description": "name", "name": "real"}` to
   `package.json`, assert `daemon == auto_naming.suggest_names(d)` — must print
   OK, not DIVERGE.
2. Cache freshness: one daemon; call `naming.suggest(d)`; `git -C d checkout
   -qb other`; call again; assert the `repo:other` suggestion appears and the
   result still equals a fresh GUI computation.
3. `python3 -m unittest tests.test_integration.TestNamingParityWithGui
   tests.test_integration.TestNamingSuggestAvoidsSpuriousGitForks -v` — all pass.
4. `make test`; then `make test-fuzz`; then
   `ASAN_OPTIONS=detect_leaks=1 node scripts/test.mjs --sanitize --unit`
   — each exits 0, no sanitizer findings.
5. `git --no-pager diff --stat HEAD` and warning diff vs a `git stash` rebuild —
   confirm no new warnings, no removed tests.
6. Fill D5 and confirm `git status` shows the branch committed and pushed.

## Anti-Drift Rules
- **Scope = the four drifts above only.** Do not broaden into other commands,
  refactors, or "while we're here" cleanups; new needs get a new goal.
- **Do not repeat the mistake this goal exists to fix:** D2 fixtures must be
  RED before the D1 fix. If a fixture is green before the fix, it is not
  adversarial enough.
- D1 must **verify key context** — do not "fix" the value-collision by taking
  the last `"name"` or by prefix/suffix string tricks; track whether the string
  is a key.
- D3 must not reintroduce a `git` fork for non-repositories, and must not grow
  memory with the number of paths seen (keep the bound at 16).
- No test deleted, `.skip()`ed, or weakened to reach green.
- If a verification step cannot be run, report BLOCKED — do not claim done.

## Estimated Effort
Medium. D1 + D2 ≈ small (one function + two fixtures). D3 is the only
design-sensitive part (fingerprint choice). D4 is one unit test. D5/D6 are
paperwork. Roughly one focused session.

## Verification Results

All four drifts closed. Every check re-run from scratch, not recalled.

- **DRIFT-1 (parser):** `json_find_toplevel_key()` now requires a `:` after a
  candidate string. Probe (was DIVERGE, now OK):
  `{"description": "name", "name": "real"}` and `{"a": "name", "name":
  "real"}` → daemon == GUI (`['real', ...]`).
- **DRIFT-2 (stale cache):** stat fingerprint (package.json + `.git/HEAD`
  mtime). New integration test `test_switching_branch_invalidates_the_cache`
  is RED with the fingerprint disabled (`['repo']` instead of
  `['repo:branchy']`) and GREEN with it.
- **DRIFT-3 (sanitizer coverage):** `test_dispatch_naming_suggest_cache`
  (§D4) calls `naming.suggest` twice; it runs under
  `node scripts/test.mjs --sanitize --unit` (PASS).
- **DRIFT-4 (paperwork/state):** `GOAL_NAMING_HARDENING.md` results filled;
  work committed and pushed.

| Check | Command | Result |
|-------|---------|--------|
| Integration | `make test` | 300 tests, **OK**, EXIT=0 |
| Fuzz | `make test-fuzz` | **RESULT: PASS**, EXIT=0 |
| Sanitizers | `ASAN_OPTIONS=detect_leaks=1 … --sanitize --unit` | EXIT=0, zero findings |
| Warnings | core rebuild vs `git stash` baseline | 4 == 4 (none new) |
| Tests intact | defs 286→300, skips 2→2, removed 0 | ✅ |
| Parity probe | `/tmp/lmux_probe.py` (3 cases) | all OK |
