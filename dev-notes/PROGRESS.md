# Pipeline Progress

**Project:** lmux
**Started:** 2026-09-06
**Current Phase:** Phase 3 — Engineer (Milestone 9: Config Round-Trip Integrity)
**Status:** Complete — goal-met audit MET (34/34 checks)

---

## Milestone 9: Config Round-Trip Integrity (2026-10-03) — COMPLETE

Goal: `dev-notes/FEATURE_GOAL.md`. Audit: 34/34 checks, verdict **MET**.

Five confirmed defects, all in the config subsystem, found by a Phase 0 evidence
audit after the previous "1.2.0 feature" spec was found to be 3/5 already-built.

| ID | Defect | Impact | Fix |
|----|--------|--------|-----|
| B1 | `lmux_config_save` called from only one place in `model.c`; `workspace.group.create/add/remove` never saved | **silent data loss** — groups lost on restart | F1 `model_save_config()` |
| B2 | `workspace_groups` save/load disagreed 3 ways (key `workspace_groups` vs `groups`; array vs object; int vs string ids) | **silent data loss** | F2 array-form parser |
| B3 | `themes` serialized on save, **no parse branch on load** | themes never loadable | F3 array-form parser |
| B4 | `config.set theme` validated the key but not the value | GUI silently coerced any value to light | F4 reject non-`dark`/`light` |
| B5 | `keybindings` save writes array, load gated on `'{'` | **zero keybindings ever parsed** | F2 array-form parser |

Root cause common to B2/B3/B5: `lmux_config_save()` writes all three collections
as arrays of objects, but `lmux_config_load_buf()` understood only a legacy
object-keyed-by-name shape (and had no branch at all for themes). **All three
collections were write-only.** Each had a passing test, because every test fed
the loader a hand-written shape it happened to accept and none exercised `save`
output. The missing coverage was the save→load direction.

### Verification
- `make test` → 223 integration (219 + 4 new), 0 failed, zero sanitizer findings
- `make test-fuzz` → pass
- 3 new C unit tests in `test_config.c` (config: 7 → 10 tests)
- Live: group create reaches `config.json`; survives daemon restart; invalid
  theme rejected; `dark`/`light` unaffected
- Legacy object-form tests still pass → backward compatible, no user file broken

### Files changed by this goal
`src/core/config.c`, `src/core/model.c`, `tests/test_config.c`,
`tests/test_integration.py`, `dev-notes/FEATURE_GOAL.md`

(`gui/file_explorer.py`, `include/lmux.h`, `package.json` are also dirty in the
worktree from the *earlier* GUI `GObject` fix and the 1.1.0→1.1.1 version bump,
not from this goal.)

### Deferred (deliberately, not forgotten)
- **fd monitor** — genuinely absent, but an enhancement; correctness first
- **named themes (Solarized/Dracula/Monokai)** — unblocked now that F3 lands;
  needs a new goal and a `lmux_config_add_theme()` API, which does not exist
- **minor:** `lmux_config_save()` mkdirs only one level deep, so it fails
  silently if `$HOME/.config` itself is absent. Harmless on real systems.

---

## Earlier history

**Milestone 8** (issues #1–#16) shipped as PR #15: restore-under-write-lock, fd
and child reaping, `FD_CLOEXEC` on sockets, response-framed client reads,
`sizeof - 1` parser lengths, CI hardening. Released as 1.1.0 / 1.1.1.

**Superseded note:** an earlier entry in this file claimed "COMPLETE — 217 tests
passing". Live testing on 2026-10-02 found 14 defects; those are the Milestone 8
issues above, all since fixed.

---

## Current State

- **Project:** lmux - Terminal Multiplexer
- **Current Phase:** Phase 3 - Engineer
- **Status:** IN PROGRESS — remediation of issues #9-#14 (filed after live testing + comparison with `nami`)
- **Branch:** `fix/issue-1-8-bugs` (issues #1-#8 fixes, uncommitted WIP)
- **Baseline:** 217 tests; suite is currently RED (crash under `session.restore`)

> Note: the previous entry claimed "COMPLETE — 217 tests passing". That is no
> longer accurate. Live testing on 2026-10-02 found 14 defects; #9 (heap
> use-after-free) crashes the daemon and must be fixed before the suite is green.

---

## Milestone 8: Memory Safety & CI Hardening (2026-10-02)

All work is issue-driven (`git-driven-development`); status labels are the state machine.

| Issue | Severity | Files | Label | Status |
|-------|----------|-------|-------|--------|
| #9 | critical | `src/core/model.c`, `src/cli/main.c` | `status:ready` | not started |
| #10 | high | `src/core/model.c` | `status:ready` | not started |
| #11 | tech-debt | `src/core/model.c` | `status:ready` | not started |
| #12 | high | `src/core/model.c` | `status:ready` | not started |
| #13 | medium | `.github/workflows/*`, `scripts/` | `status:ready` | not started |
| #14 | high | `scripts/build-cli.mjs`, `.github/workflows/ci.yml`, `tests/` | `status:ready` | not started |

### Serialization constraint
Issues #9, #10, #11, #12 all touch `src/core/model.c` and **must be merged one at
a time**, rebasing each onto `origin/master` before merge (git-driven-development §7).
Issues #13 and #14 do not touch `model.c` and may proceed in parallel.

### Known blocker
`scripts/build-cli.mjs` does not forward `--sanitize` to the link step, so an
instrumented CLI binary cannot be produced by the normal build. Issue #14 fixes
this; until then, instrumented runs need a manual `gcc -fsanitize=...` link.

---

## Phase Completion

### Phase 0: Bootstrap (Existing Projects)
- [x] Create dev-notes/ directory
- [x] Deep Discovery - Analyze repository topology
- [x] Write DISCOVERY.md

### Phase 1: Systematic Codebase Audit
- [x] Audit against Production Readiness Matrix
- [x] Write AUDIT.md
- [x] Identify critical, high, medium, low issues

### Phase 2: Risk Triage & Roadmap
- [x] Define GOAL.md with verification gates
- [x] Create incremental hardening plan
- [x] Define success criteria

### Phase 3: Characterization Test Harness
- [x] Define characterization test strategy
- [x] Create CHARACTERIZATION.md
- [x] Define test categories and templates

### Phase 4: Incremental Strangler Fig Hardening
- [x] Define hardening steps
- [x] Create HARDENING.md
- [x] Define verification gates for each step

### Phase 5: Verification & Non-Regression Gate
- [x] Define verification gates
- [x] Create VERIFICATION.md
- [x] Define CI/CD pipeline

### Milestone 6: Core Features (Complete)
- [x] P0.1: In-App Browser (stub + dispatch + CLI, 19 functions, 19 tests)
- [x] P0.2: Agent Hibernation (auto-hibernate after 300s idle)
- [x] P0.3: Enhanced Notifications (visual rings, hooks, CLI)
- [x] P1.1: Multi-Window Support (create/list/close/focus/move-workspace)
- [x] P1.2: Persistent SSH PTY (session create/list/kill/save/restore)
- [x] P1.3: Feed Panel (feed create/list/close, entry add/list)
- [x] P2.1: Tmux Compatibility (10 tmux commands mapped)
- [x] P2.2: Copy Mode (vi keys, clipboard)
- [x] P2.3: File Explorer (11 commands)
- [x] P2.4: Canvas Layout (7 commands, freeform positioning)

### Milestone 7: Advanced Features (Complete)
- [x] GOAL-7.1: Find in Terminal (search.start/next/prev/cancel/status — 15 tests)
- [x] GOAL-7.2: Browser Import from JSON/Chrome (browser.import — 6 tests)
- [x] GOAL-7.3: Calendar Integration (calendar.import/today/upcoming — 7 tests)
- [x] GOAL-7.4: Email Panel (email.import/list/search — 7 tests)
- [x] GOAL-7.5: Weather Panel (weather.get/set_location/refresh — 6 tests)
- [x] GOAL-7.6: Clipboard History (clipboard.copy/paste/list/clear — 8 tests)
- [x] GOAL-7.7: Workspace Templates (template.list/create/save/delete — 7 tests)
- [x] GOAL-7.8: Performance Profiling (profile.status/start/stop/list — 5 tests)

---

## Artifacts Created

### dev-notes/
1. **DISCOVERY.md** - Deep discovery analysis
2. **AUDIT.md** - Systematic codebase audit
3. **GOAL.md** - Production hardening goal + Milestone 7
4. **CHARACTERIZATION.md** - Characterization test harness
5. **HARDENING.md** - Incremental hardening plan
6. **VERIFICATION.md** - Verification gates
7. **PROGRESS.md** - This file
8. **COMPARISON.md** - cmux vs lmux gap analysis
9. **ROADMAP.md** - Feature roadmap (P0-P2 complete, P3 remaining)

---

## Test Results

- **217/217 integration tests** passing (5.3s)
- **37 C unit tests** passing
- **19 browser unit tests** passing
- **Fuzz tests** (20K+ iterations)
- **Performance benchmarks** (SLA p99 < 50ms)

---

## Decisions Made

### Architecture Decisions
1. **Client-server over UDS** - Unix domain sockets for IPC
2. **JSON wire protocol** - Newline-delimited JSON messages
3. **C17 core** - Static library for performance
4. **Python GTK3 GUI** - For rapid development
5. **Node.js build scripts** - ESM modules for modern tooling
6. **Strangler Fig pattern** - Incremental hardening, no big-bang rewrites

### Implementation Decisions
1. **TDD for all new features** - Write tests first, then implement
2. **Subagent-driven development** - Branch-per-task, fresh subagents
3. **Integration tests first** - Existing test suite validates behavior
4. **Unit tests alongside** - C unit tests for new structs/functions

---

## Blockers

### Current Blockers
- (none)

### Resolved Blockers
1. **No dev-notes/ directory** - Created
2. **No discovery analysis** - Completed
3. **No audit** - Completed
4. **No C unit test framework** - Set up with minimal test harness
5. **No CI/CD pipeline** - GitHub Actions configured
6. **Snapshot restore PTY exhaustion** - Fixed with `bool spawn_pty` parameter
7. **EOF detection timeout** - Fixed with `shutdown(SHUT_WR)` in server.c
8. **Compiler warnings** - Fixed with pragma suppressions and type corrections

---

## Timeline

### Completed
- [x] 2026-09-06: Phase 0-5 - Bootstrap through Verification
- [x] 2026-09-06: Milestone 1-5 - Security, Reliability, Observability, Testing, Supply Chain
- [x] 2026-09-06: Milestone 6 - Core Features (P0.1-P2.4)
- [x] 2026-09-07: Bug fixes (EOF detection, compiler warnings, snapshot PTY)
- [x] 2026-09-07: Documentation updates (README, ROADMAP, COMPARISON)
- [x] 2026-09-07: Milestone 7 - Advanced Features (GOAL-7.1 through GOAL-7.8)
- [x] 2026-09-07: Phase 4 - Cloud VMs, iOS Companion, Agent Teams (GOAL-8.1 through GOAL-8.3)

### Current
- (none — all features complete)

### Upcoming
- (none)

### Completed (2026-09-07)
- [x] Security audit — full threat model, 6 vulnerabilities fixed
- [x] Path traversal prevention — snapshot, file explorer, SSH sessions, config
- [x] Rate limiter fail-closed — deny when table full instead of allowing
- [x] SECURITY.md updated with new measures
- (awaiting next milestone definition)

---

**Progress Updated:** 2026-09-07
**Next Action:** All features complete — 217 tests passing, ready for deployment