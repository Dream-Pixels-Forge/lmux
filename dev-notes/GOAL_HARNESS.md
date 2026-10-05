## Goal: Fix integration-harness defects #37 (ptyd daemon leak) and #38 (second runner unlinks live sockets)

### Objective
Make `tests/test_integration.py` teardown leak-free and safe under a second concurrent runner: every daemon the runner starts dies with it, and startup cleanup never deletes a live socket.

### Context
Handoff open items #1/#2 (dev-notes/HANDOFF.md §Open items): every run leaks one `lmux-ptyd-*` daemon (issue #37, 14 orphans observed Oct 4–5); the `__main__` block unconditionally unlinks all `/tmp/lmux-integration-*.sock`, so a second runner destroys a live runner's socket (issue #38, A/B-proven false red). Both share `tests/test_integration.py` + `tests/lmux.py` teardown paths — one branch avoids conflicts.

### Deliverables
- [ ] **F1 (#38)** — `__main__` cleanup only unlinks dead sockets (connect-probe before unlink; live socket skipped)
- [ ] **F2 (#37)** — the ptyd helper daemon from `test_restore_forks_no_pty` is killed in the `finally` block even on success (currently only `proc.kill()` on the strace wrapper; the daemon under test survives)
- [ ] **T1 (#38 RED)** — regression test: a live listener on a matching `lmux-integration-*.sock` path survives runner startup cleanup
- [ ] **T2 (#37 RED)** — regression test: after the ptyd test's teardown, no `lmux --socket /tmp/lmux-ptyd-*.sock daemon` child of the runner survives
- [ ] Goal-met audit verdict MET before any PR

### Definition of Done
- [ ] `grep -n "glob(\"lmux-integration" tests/test_integration.py` shows a liveness probe before any unlink
- [ ] `test_restore_forks_no_pty`'s `finally` kills the daemon-under-test process as well as the strace wrapper
- [ ] T1 fails on master (live socket deleted), passes with F1
- [ ] T2 fails on master (daemon survives), passes with F2
- [ ] Full `make test` (309 integration + unit) exits 0, run **serially**, no test deleted/skipped/weakened (skips 1 → 1)
- [ ] `make test-fuzz` exits 0
- [ ] `ps -eo pid,cmd | grep lmux-ptyd` shows zero strays after the suite
- [ ] `git diff --stat` touches only `tests/test_integration.py`, `tests/lmux.py`, `dev-notes/GOAL_HARNESS.md`

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
git checkout -b fix/harness-ptyd-and-socket-cleanup master
# RED: pristine behavior reproduced in a throwaway worktree before any fix:
#   MUT-T1: pristine __main__ block deletes a live lmux-integration-*.sock — CONFIRMED
#   MUT-T2: pristine ptyd finally (proc.kill on strace only) leaves daemon pid alive — CONFIRMED (pid 555225)
make clean && node scripts/build-core.mjs && node scripts/build-cli.mjs
python3 tests/test_integration.py   # serially — never two runners at once (HANDOFF Proven #1)
# Result: Ran 313 tests in 87.741s — OK (309 baseline + 4 new), 0 skipped/weakened
ps -eo pid,cmd | grep lmux-ptyd | grep -v grep && echo "LEAK" || echo "CLEAN"   # CLEAN
node scripts/test.mjs --unit        # pass
node scripts/test.mjs --unit --fuzz # pass
```

### Anti-Drift Rules
- Fix **only** the two harness defects. Do not touch `src/`, `include/`, CI, or snapshot/pty behavior.
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- Never run two integration runners concurrently during verification (handoff Proven #1) — RED-in-worktree and GREEN-on-branch runs are sequential.
- The connect-probe must mirror the client's own connect logic (AF_UNIX + timeout), not a file-exists check.
- If the ptyd daemon needs a lifetime tied to the parent, prefer explicit teardown in the test's `finally` over a global atexit — surgical, local, auditable.
- If `include/lmux.h` or any `src/` change looks needed, stop and report — that is wider blast radius than this goal authorizes.

### Estimated Effort
Half a day. Two small teardown fixes + two regression tests, all in `tests/`.
