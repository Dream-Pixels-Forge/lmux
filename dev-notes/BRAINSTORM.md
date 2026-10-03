# lmux Brainstorm - Memory Safety & CI Hardening

**Date:** 2026-10-02
**Phase:** 1 - Brainstorm
**Status:** In Progress

## Executive Summary

This brainstorm documents the architecture vision and approach for fixing all open issues and achieving production readiness. The focus is on memory safety, concurrency correctness, and CI hardening.

## Open Issues Summary

Based on the git status and issue tracker, the following issues need to be addressed:

### Critical / High Priority
- **#1** `workspace.create` hangs forever - PTY child inherits client socket, blocking EOF
- **#2** PTY children inherit daemon's entire fd table - unbounded `/dev/ptmx` fd leak
- **#9** Heap-use-after-free under `session.restore` - concurrent model walk without lock
- **#10** High severity issue in `src/core/model.c`
- **#12** High severity issue in `src/core/model.c`
- **#16** test_fuzz passes n=12 for a 10-byte literal - global-buffer-overflow

### Medium / Tech Debt
- **#11** Tech debt in `src/core/model.c`
- **#13** Medium issues in `.github/workflows/*`, `scripts/`
- **#14** High issues in `scripts/build-cli.mjs`, `.github/workflows/ci.yml`, `tests/`

### Already Fixed (PR #15)
- **#15** Comprehensive fix closing #1-#10, #14, #16 - memory safety, fd reaping races

## Architecture Vision

The fix approach follows these principles:

### 1. Lock-Critical Sections for Concurrency
- `session.restore` must operate under `rw_lock` write protection
- Global model walks must hold appropriate locks to prevent use-after-free
- `pane_kill_pty()` must handle `ECHILD` properly and close fds unconditionally

### 2. FD Management in PTY Children
- Close inherited fds in forked PTY children immediately after `forkpty()`
- Set `FD_CLOEXEC` on listening and per-client sockets
- Use `closefrom()` or `/proc/self/fd` loop to clean up excess fds

### 3. Snapshot Restore Correctness
- Do not allocate default surface before replaying saved surfaces
- Clear app pane pointers (`ws_current`, `ws_last`, `last_focused`, `search_pane`) before freeing
- Clear workspace pointers before freeing to dereference freed memory

### 4. Parser Length Correctness
- Use `sizeof literal - 1` to exclude NUL terminator
- Never pass hand-counted lengths that can drift from actual array size
- Sweep all `lmux_osc_parser_feed()` calls for similar off-by-one errors

### 5. CLI Response Handling
- Replace EOF-based reading with response framing (stop at first complete JSON line)
- Add timeouts to prevent hanging on incomplete responses
- Set `FD_CLOEXEC` on all client sockets

## Key Files to Modify

### Core Memory Safety (`src/core/model.c`)
- Add `rw_lock` protection around `session.restore` and snapshot loading
- Fix `pane_kill_pty()` ECHILD handling and fd cleanup
- Clear workspace pointers before freeing in `lmux_workspace_close()`
- Fix snapshot restore to not duplicate surfaces

### PTY/FD Management (`src/core/model.c`, `src/cli/main.c`)
- Close `client_fd` in child branch of `pane_spawn_pty()`
- Set `FD_CLOEXEC` on sockets in `src/core/server.c`
- Fix client read loop to use response framing

### Parser Fixes (`tests/test_fuzz.c`)
- Use `sizeof split_a - 1` instead of hand-counted length
- Sweep for other off-by-one errors in `lmux_osc_parser_feed()` calls

### CI/Build Fixes (`scripts/build-cli.mjs`, `.github/workflows/ci.yml`)
- Forward `--sanitize` flag to CLI link step
- Build CLI against instrumented core for sanitizer runs

## Execution Plan

Follow `test-driven-development` discipline:
1. **RED** - Write failing test that defines desired behavior
2. **GREEN** - Write minimum code to make test pass
3. **REFACTOR** - Clean up while keeping tests green

Branch strategy: `fix/issue-1-8-bugs` → fix individual issues → rebase onto `main` → PR → review → merge

## Decisions Made

1. **Restore before listen**: `session.restore` must complete before socket server starts to avoid write lock contention
2. **ECHILD as already-reaped**: `pane_kill_pty()` treats `ECHILD` as already-reaped, closes pty fd unconditionally once
3. **Clear-before-free**: Workspace pointers cleared before freeing to prevent dereferencing freed memory
4. **sizeof-1 for literals**: Use `sizeof array - 1` to exclude NUL and prevent length drift

## Blockers

- Issues #9, #10, #11, #12 all touch `src/core/model.c` and must be merged one at a time
- Issue #14 (sanitizer CI) must be fixed before instrumented runs can verify others
- PR #15 must be merged first as it closes #1-#10, #14, #16 and provides the base fixes