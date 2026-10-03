# lmux Security Audit - Memory Safety & CI Hardening

**Date:** 2026-10-02
**Phase:** 2 - Security
**Status:** In Progress

## Security Scope

This audit covers all memory safety, concurrency, and CI hardening issues identified in the lmux repository. The goal is production readiness through incremental hardening.

## Critical Security Issues

### #1 & #2: PTY FD Leak & Inheritance (HIGH)
**Issue:** Forked PTY children inherit the daemon's entire file descriptor table, causing:
- Unbounded `/dev/ptmx` fd growth (1 fd per pane, never cleaned up)
- `EMFILE` errors when fd table overflows
- Daemon crash preventing new pane creation
- Socket EOF blocking when child holds inherited client_fd

**Security Impact:**
- Denial of service via fd exhaustion
- Potential for EMFILE to block legitimate operations
- Silent memory growth in kernel fd tables

**Fix:**
- Close `client_fd` in child branch of `pane_spawn_pty()` after `forkpty()`
- Set `FD_CLOEXEC` on listening socket and per-client sockets in `src/core/server.c`
- Use `closefrom()` or `/proc/self/fd` loop to close fds above stderr in child process

### #9 & #10: Heap Use-After-Free (CRITICAL)
**Issue:** `session.restore` operates without `rw_lock` held, allowing concurrent model walks to access freed memory.

**Security Impact:**
- Arbitrary code execution via heap corruption
- Use-after-free is a classic exploitation vector
- Daemon crashes under normal concurrent usage

**Fix:**
- `lmux_app_auto_restore_locked()` takes `rw_lock` write lock
- `session.restore` runs inside critical section of `dispatch_command`
- Restore must complete before socket server starts (before `listen()`)
- `pane_kill_pty()` handles `ECHILD` as already-reaped, closes pty fd unconditionally once

### #16: Falsy Positive Buffer Overflow in Sanitizer CI (HIGH)
**Issue:** `test_fuzz` passes `n=12` for a 10-byte literal (10 chars + NUL = 11 bytes), causing ASan to flag global-buffer-overflow.

**Security Impact:**
- Sanitizer gate becomes unusable - any real memory bug indistinguishable from false positive
- ASan cannot catch genuine findings when flooded with false positives

**Fix:**
- Use `sizeof split_a - 1` instead of hand-counted length `12`
- Sweep `tests/test_fuzz.c` for all similar off-by-one errors
- Derive lengths from actual array size using `sizeof - 1`

### #14: Sanitizer CI Incomplete (MEDIUM)
**Issue:** `sanitizers` CI job only builds core, but `test.mjs --unit` shells out to `build/lmux`. Since `build-cli.mjs` drops `-fsanitize` at link, the CLI binary is not instrumented, causing the job to die with `CLI test FAILED (exit 127)` before exercising any C code.

**Security Impact:**
- CI gate cannot verify memory safety in CLI code path
- Invisible to sanitizer checks that should catch real bugs

**Fix:**
- `build-cli.mjs` forwards `--sanitize` flag to the link step
- `.github/workflows/ci.yml` builds CLI sanitized before running suite
- Both `nightly` and `release` rebuild both core and CLI before running tests

## Medium Security Issues

### #12: Unknown Config Key Handling (HIGH)
**Issue:** `config.set` unknown keys not properly validated, potentially allowing injection or unexpected behavior.

**Fix:** Ensure `config.set` returns `{"ok":false,"error":{"code":"invalid_params","message":"unknown config key"}}` for unrecognized keys.

### #5: Whitespace-Sensitive Parser (MEDIUM)
**Issue:** Parser truncates JSON strings at whitespace, losing data like `he said "hi"` → `he said`.

**Fix:** `json_find_key()` must skip spaces/tabs/CR/LF between key and value.

## High-Level Security Requirements

### 1. Fail-Closed Defaults
- All error paths must secure resources (close fds, release locks)
- Default deny for unknown config keys, unauthorized connections
- Sanitizer errors must fail, not silently continue

### 2. Bounded Resource Usage
- PTY fd count must remain bounded per pane
- FD table must not leak across fork/exec
- Memory must be freed exactly once, no leaks or double-frees

### 3. Defensible Error Handling
- All system calls checked for errors
- FD cleanup in all exit paths
- Timeouts on all blocking I/O to prevent hangs

### 4. Auditability
- All security-critical operations logged
- FD lifecycle tracked (open → use → close)
- Sanitizer findings tracked and resolved

## Compliance Checklist

- [ ] No use-after-free in any code path
- [ ] All fds set FD_CLOEXEC where appropriate
- [ ] PTY children do not inherit client_fd
- [ ] session.restore under rw_lock
- [ ] Parser lengths derived from sizeof, not hand-counted
- [ ] CI runs with ASan+USBsan and passes end-to-end
- [ ] workspace.create returns promptly (not hanging)
- [ ] workspace.close clears pointers before freeing
- [ ] No silent fd leaks in any code path