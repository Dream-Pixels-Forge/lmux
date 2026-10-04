# Goal: Correct package.json parsing and cheapen `naming.suggest`

## Objective
Fix two known weaknesses in the `naming.suggest` port, in small verified steps:

1. The `package.json` reader mis-parses nested keys (a **real, reproducible bug**).
2. The command forks `git` up to twice per call, even for non-repositories.

## Context
PR #23 ported the GUI's ordered rule chain into C and pinned it with a parity
test. Two weaknesses were flagged and left. Investigating them turned the first
from "could misread" into "does misread, right now".

### Bug 1 — nested keys beat the top-level key (confirmed)

`naming_package_json_name()` locates `"name"` with `strstr()` and takes the
first hit. Any nested `"name"` that appears earlier wins:

```
/tmp/pj/nested/package.json:
  { "scripts": { "name": "WRONG-from-scripts" }, "name": "right-name" }

daemon = ['WRONG-from-scripts', 'nested', 'nested (node)']
gui    = ['right-name',        'nested', 'nested (node)']
```

Same for `"dependencies": {"name": ...}` → daemon returns `WRONG-from-deps`,
GUI returns `real-pkg`.

**Why the parity test missed it:** every fixture put `"name"` first, so the
first `strstr` hit happened to be correct. The test was real but its fixtures
were not adversarial. This is the lesson — a parity test only pins what its
fixtures exercise.

The codebase's own `json_find_key()` is also flat (`strstr` + a `"key":`
shape check), so it cannot be reused: it has the same defect.

### Bug 2 — forking `git` for directories that are not repositories

`naming_run()` forks `git rev-parse --show-toplevel` and then
`git rev-parse --abbrev-ref HEAD` on every call. In a plain directory both fork
and both fail, costing two process spawns to learn "not a repo".

## Deliverables, as separate steps

- [x] **S1** — depth-aware top-level key lookup for `package.json`
      (skip nested objects/arrays; respect `\"` escapes inside strings)
- [x] **S2** — adversarial parity fixtures: nested `name`, `name` after a
      nested object, escaped quotes, whitespace variants
- [x] **S3** — skip the `git` forks when `cwd` is not inside a work tree
      (walk parents for `.git` first), matching what git itself does
- [x] **S4** — bound repeated calls with a tiny per-path cache

## Definition of Done
- [x] daemon and GUI agree on every fixture, including all adversarial ones
- [x] A nested `"name"` never wins over the top-level `"name"`
- [x] A string containing `\"name\"` does not fool the parser
- [x] No `git` process is spawned for a non-repository directory
- [x] Repeated `naming.suggest` for the same path does not re-fork
- [x] `make test` exits 0, none deleted/skipped/weakened
- [x] `make test-fuzz`, `make test-unit` exit 0
- [x] ASAN+UBSAN `detect_leaks=1` → zero findings (S4 adds a cache, so this matters)
- [x] No new compiler warnings

## Anti-Drift Rules
- **S1 must parse, not pattern-match.** Do not "fix" the nested case by taking
  the *last* `"name"` instead of the first; that merely trades one wrong answer
  for another. Track depth.
- Respect `\"` escapes: a `"` inside a string value must not change parser state.
- S3 must not change results for genuine repositories — parity still required.
- S4's cache must be bounded and must not grow with the number of paths seen.
- No test deleted, skipped, or weakened to reach green.

## Verification Results

- **DRIFT audit found 4 gaps**; all closed (see `GOAL_DRIFT_HARDENING.md`).
- **S1 parser** now verifies key context (a `:` must follow a candidate
  string), so a top-level *value* equal to `"name"` no longer shadows the real
  key. Probe: `{"description": "name", "name": "real"}` and
  `{"a": "name", "name": "real"}` → daemon == GUI (`['real', ...]`).
- **S2 fixtures**: +3 integration tests (2 adversarial value-collision parity
  fixtures, 1 cache-invalidation). All were RED before their fix.
- **S3/S4**: unchanged behaviour; cache bound stays 16.
- **D3 cache invalidation**: stat-based fingerprint (package.json mtime +
  `.git/HEAD` mtime); switching branch now recomputes instead of serving stale.
  Proven RED by temporarily disabling the fingerprint comparison.
- **D4 sanitizer coverage**: new unit test `test_dispatch_naming_suggest_cache`
  calls `naming.suggest` twice, so the memo path runs under `--sanitize`.
- **Results**: `make test` → **300 tests, OK** (286 baseline + 14).
  `make test-fuzz` → **RESULT: PASS**. `test_model` 23, `test_osc` 8,
  `test_config` 12. ASAN+UBSAN `detect_leaks=1` → **EXIT=0, zero findings**
  (naming cache exercised). Warnings **4 == baseline 4**. Skips **2 == 2**;
  0 tests removed.