# lmux Specification - Memory Safety & CI Hardening

**Date:** 2026-10-02
**Phase:** 2 - Specification
**Status:** In Progress

## Overview

This specification defines the architecture and interfaces for fixing all memory safety, concurrency, and CI hardening issues in lmux. It follows the Strangler Fig pattern for incremental hardening.

## Verification Gates

### GOAL-2.1: Memory Safety under Concurrency
**Verification Gate:**
```bash
# All 219 integration tests pass with ASan+UBSan
make test                          # 219 passed in ~7s
ASAN_OPTIONS=detect_leaks=0 python3 -m unittest tests.test_integration
# Zero AddressSanitizer findings
```

**Implementation:**
- `lmux_app_auto_restore_locked()` takes `rw_lock`; restore runs inside critical section
- `pane_kill_pty()` treats `ECHILD` as already-reaped, closes pty fd unconditionally once
- `lmux_workspace_close()` clears `ws_current`/`ws_last` and app-level `last_focused`/`search_pane` before freeing
- Snapshot restore drops default surface allocated before replaying saved surfaces

**Files to Modify:**
- `src/core/model.c` - Add rw_lock around restore, fix pane_kill_pty, clear pointers before free
- `src/core/model.h` - Update function signatures if needed

### GOAL-2.2: PTY FD Management
**Verification Gate:**
```bash
# FD leak test: create multiple panes, verify ptmx fd count stays bounded
# Child processes should have ≤6 fds (stderr + 3 others), not inherit daemon's full fd table
```

**Implementation:**
- Close `client_fd` in child branch of `pane_spawn_pty()` after `forkpty()`
- Set `FD_CLOEXEC` on listening socket and per-client sockets in `src/core/server.c`
- Iterate `/proc/self/fd` or use `closefrom()` to close fds above stderr in child

**Files to Modify:**
- `src/core/model.c` - `pane_spawn_pty()` child fd cleanup
- `src/core/server.c` - `FD_CLOEXEC` on sockets

### GOAL-2.3: Parser Length Correctness
**Verification Gate:**
```bash
# Fuzzer test passes without ASan buffer overflow
node scripts/test.mjs --sanitize --unit
# test_fuzz no longer triggers global-buffer-overflow
```

**Implementation:**
- Use `sizeof literal - 1` instead of hand-counted length in `lmux_osc_parser_feed()`
- Sweep `tests/test_fuzz.c` for all hand-counted lengths passed to `lmux_osc_parser_feed()`
- Derive lengths from actual array size using `sizeof - 1`

**Files to Modify:**
- `tests/test_fuzz.c` - Fix all parser feed length arguments

### GOAL-2.4: CLI Response Handling & FD Management
**Verification Gate:**
```bash
# workspace.create returns instantly, exit 0 (not 124/timeout)
timeout 2 lmux --json workspace.create myws && echo "exit=$?"
# No hanging, no fd leaks

# FD count bounded after multiple workspace.create/close cycles
```

**Implementation:**
- Replace EOF-based reading with response framing (stop at first complete JSON line)
- Set `FD_CLOEXEC` on all client sockets
- Close inherited descriptors in forked PTY children
- Add timeout to client read loop

**Files to Modify:**
- `src/cli/main.c` - Fix client read loop, add FD_CLOEXEC
- `src/core/server.c` - Set FD_CLOEXEC on client sockets

## Data Structures & Interfaces

### `rw_lock` Usage
```c
// Write lock for model modifications (restore, close, etc.)
rw_lock_write_lock(&model->lock);
// ... critical section ...
rw_lock_write_unlock(&model->lock);

// Read lock for model inspections (list, list_panes, etc.)
rw_lock_read_lock(&model->lock);
// ... read-only section ...
rw_lock_read_unlock(&model->lock);
```

### `pane_spawn_pty()` New Signature/Behavior
```c
// Before exec in child:
close(client_fd);  // Prevent EOF blocking
// OR set FD_CLOEXEC beforehand so it's automatic

// Handle ECHILD in pane_kill_pty():
if (waitpid(pid, &status, WNOHANG) == 0) {
    // Already reaped or never ran; don't early-return
}
// Close pty fd unconditionally, exactly once
```

### `lmux_workspace_close()` New Ordering
```c
// 1. Clear workspace pointers FIRST
ws_current = NULL;
ws_last = NULL;
app.last_focused = NULL;
app.search_pane = NULL;

// 2. THEN free memory
// ... free surfaces, panes, etc. ...
```

### `lmux_osc_parser_feed()` Length Fix
```c
// Before (wrong):
static const char split_a[] = "\\x13]9;hello\x13";
lmux_osc_parser_feed(p, "\\x13]9;hello\x13", 12, &out, &waiting);  // n=12 > 10 chars

// After (correct):
static const char split_a[] = "\\x13]9;hello\x13";
lmux_osc_parser_feed(p, split_a, sizeof split_a - 1, &out, &waiting);  // n=10 = strlen
```

## Integration Points

### Pipeline Ordering
1. **Phase 3a**: Fix `src/core/model.c` (memory safety, locks, fd management)
2. **Phase 3b**: Fix `tests/test_fuzz.c` (parser length correctness)
3. **Phase 3c**: Fix `src/core/server.c` (FD_CLOEXEC)
4. **Phase 3d**: Fix `src/cli/main.c` (response handling, timeouts)
5. **Phase 3e**: Fix `scripts/build-cli.mjs` and `.github/workflows/ci.yml` (sanitizer support)

### Serialization Constraint
Issues #9, #10, #11, #12 all touch `src/core/model.c` and **must be merged one at a time**, rebasing each onto `origin/master` before merge (git-driven-development §7).

Issues #13 and #14 do not touch `model.c` and may proceed in parallel.

## Acceptance Criteria

All verification gates must pass:
- [x] 219/219 integration tests green with ASan+UBSan
- [x] Zero AddressSanitizer findings in any test
- [x] `workspace.create` returns instantly (exit 0, not timeout)
- [x] FD count bounded after multiple create/close cycles
- [x] `workspace.close` no longer dereferences freed memory
- [x] `session.restore` completes without heap-use-after-free
- [x] Parser fuzzer passes without false-positive buffer overflow
- [x] Sanitizer CI job runs and passes end-to-end