# lmux Design - Memory Safety & CI Hardening

**Date:** 2026-10-02
**Phase:** 2 - Design
**Status:** In Progress

## Architecture Overview

The lmux design follows a Strangler Fig pattern for incremental hardening. Each fix is applied independently, verified, and merged before the next. The core pattern is:

1. Identify the defect
2. Write a test that reproduces it (RED)
3. Fix the minimum code to pass the test (GREEN)
4. Refactor while keeping tests green
5. Merge and rebase onto main
6. Repeat

## Component Design

### 1. Concurrency Layer (`src/core/model.c`)

**rw_lock Design:**
- Read-write lock protecting the global model state
- Multiple readers allowed simultaneously
- Writer exclusive - blocks all readers and other writers
- Used for: `session.restore`, `lmux_snapshot_load`, workspace create/close

**Lock Ordering:**
```
# Correct: Restore under write lock BEFORE socket server starts
lmux_app_auto_restore_locked():
    rw_lock_write_lock(&model->lock)
    lmux_snapshot_load(model)  // Safe: holds write lock
    rw_lock_write_unlock(&model->lock)

# Correct: pane_kill_pty with ECHILD handling
pane_kill_pty(pid_t pid):
    int status
    if (waitpid(pid, &status, WNOHANG) == 0) {
        // ECHILD: already reaped or never ran
        // Do NOT early-return; continue to close pty fd
    }
    // Close pty fd unconditionally, exactly once
    close(pty_fd);
```

### 2. PTY/FD Management

**`pane_spawn_pty()` Child Path:**
```c
// In the child process after forkpty():
// 1. Close client_fd to allow EOF propagation
close(client_fd);

// 2. Close all fds above stderr (or use FD_CLOEXEC)
// Option A: Explicit closefrom loop
for (int fd = 3; fd < sysconf(_SC_OPEN_MAX); fd++) {
    close(fd);
}

// Option B: Set FD_CLOEXEC beforehand so it's automatic
fcntl(listen_fd, F_SETFD, FD_CLOEXEC);
fcntl(client_fd, F_SETFD, FD_CLOEXEC);
```

**`server.c` Socket Setup:**
```c
// In server initialization:
int listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
fcntt(listen_fd, F_SETFD, FD_CLOEXEC);  // Auto-close on exec

// Per-client socket after accept:
int client_fd = accept(listen_fd, ...);
fcntl(client_fd, F_SETFD, FD_CLOEXEC);  // Prevent fd leaks in children
```

### 3. Snapshot Restore

**Before (broken):**
```c
lmux_workspace_create():  // Eagerly allocates default surface
    surface = surface_new(...);  // Default surface
    // ... then replay saved surfaces ...
    // Problem: default surface persists, gets counted twice on restore
```

**After (fixed):**
```c
lmux_workspace_create():
    // NO default surface allocation
    // Replay saved surfaces first
    lmux_snapshot_load(model);
    // App pane pointers cleared by lmux_workspace_close(), not by restore

lmux_app_auto_restore_locked():
    rw_lock_write_lock(&model->lock)
    // Drop default surface that workspace_create eagerly allocated
    // Replay saved surfaces
    lmux_snapshot_load(model)
    // Clear app pane pointers BEFORE freeing
    app.last_focused = NULL;
    app.search_pane = NULL;
    rw_lock_write_unlock(&model->lock)
```

### 4. Workspace Close

**Before (broken):**
```c
lmux_workspace_close():
    // Free surfaces, panes, etc.
    free(surfaces);
    free(panes);
    // Pointers still dangling - use-after-free risk
```

**After (fixed):**
```c
lmux_workspace_close():
    // 1. Clear workspace pointers FIRST (before freeing)
    ws_current = NULL;
    ws_last = NULL;
    app.last_focused = NULL;
    app.search_pane = NULL;
    
    // 2. THEN free memory
    free(surfaces);
    free(panes);
    // No dangling pointers - last-pane/workspace.current safe
```

### 5. Parser Length Correctness

**Before (broken):**
```c
static const char split_a[] = "\\x13]9;hello\\x13";  // 10 chars + NUL = 11 bytes
lmux_osc_parser_feed(p, "\\x13]9;hello\\x13", 12, &out, &waiting);
// n=12 reads indices 0-11, but valid indices are 0-10 (11 bytes)
// ASan: global-buffer-overflow - READ of size 1 past end
```

**After (fixed):**
```c
static const char split_a[] = "\\x13]9;hello\\x13";  // 10 chars + NUL = 11 bytes
lmux_osc_parser_feed(p, split_a, sizeof split_a - 1, &out, &waiting);
// sizeof - 1 = 11 - 1 = 10 = correct length
// No buffer overflow, ASan happy
```

**Sweep Pattern:**
Find ALL calls to `lmux_osc_parser_feed()` in `tests/test_fuzz.c` and verify:
- Length argument matches `strlen()` or `sizeof array - 1`
- No hand-counted literals that could drift from actual array size

## Integration Sequence

### Phase 3a: Model Core Fixes (Serialization Required)
**Order:** Must be done one at a time, rebasing each onto `origin/master`
1. Fix `lmux_app_auto_restore_locked()` - add rw_lock
2. Fix `pane_kill_pty()` - ECHILD handling + fd close
3. Fix `lmux_workspace_close()` - clear-before-free
4. Fix snapshot restore - drop default surface

### Phase 3b: Parser Fixes (Independent)
**Order:** Can proceed in parallel with #3a
- Fix `tests/test_fuzz.c` - `sizeof - 1` for all parser feeds
- Run fuzzer tests with ASan - must pass

### Phase 3c: FD/CLI Fixes (Independent of model.c)
**Order:** Can proceed in parallel with others
1. `src/core/server.c` - FD_CLOEXEC on sockets
2. `src/cli/main.c` - Response framing + timeouts
3. `scripts/build-cli.mjs` - Forward --sanitize
4. `.github/workflows/ci.yml` - Build CLI sanitized

## Verification Matrix

| Check | Before | After Target |
|-------|--------|--------------|
| 219 integration tests + ASan | RED (crashes) | GREEN (0 findings) |
| `workspace.create` timeout | 124s (hung) | < 2s (returns 0) |
| FD count after 10 create/close cycles | Grows unbounded | Bounded (≤ original + 2) |
| `workspace.close` use-after-free | Possible | Impossible (cleared before free) |
| `session.restore` concurrency | Crash | Safe under rw_lock |
| Parser fuzzer ASan findings | global-buffer-overflow | None |
| Sanitizer CI job exit code | 127 (CLI failed) | 0 (all tests pass) |