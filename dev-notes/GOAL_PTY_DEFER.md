## Goal: Defer PTY spawn during snapshot restore

### Objective
Stop `lmux_snapshot_load()` from forking a pty for every workspace it is about
to throw away, by creating the default surface and pane without spawning.

### Context
`lmux_workspace_create()` eagerly builds a surface with a forked pty, and
`lmux_surface_create()` eagerly builds a pty-backed terminal pane. The snapshot
restore calls both, then immediately frees the placeholder via
`snap_drop_eager_surfaces()` / `snap_drop_eager_panes()` — so every restored
workspace pays for a forkpty that lives a few microseconds.

Measured on master (`4219daa`), cost is **per workspace, not per byte**:

| Workspaces | Document size | Load time | Per workspace |
|---|---|---|---|
| 40 | 138 KB | 2.14 s | 53 ms |
| 100 | 146 KB | 5.18 s | 52 ms |

Same document size, 2.5× the workspaces, 2.4× the time — the parser is
negligible; `forkpty` is the entire cost. A 900-workspace restore takes ~45 s.

Safety is already established: `pane_new()` sets `pty_fd = -1` and
`child_pid = 0`, and `pane_kill_pty()` guards both (`if (child_pid > 0)`,
`if (pty_fd >= 0)`), so freeing a never-spawned pane is a no-op.

`lmux_pane_split()` already takes a `spawn_pty` flag and the restore already
passes `false` — the pattern exists; it is simply not available on the two
eager constructors.

### Design
Add internal `_ex` variants rather than a module-level flag, so there is no
mutable global that a failed restore could leave set:

- `lmux_workspace *workspace_create_ex(lmux_app*, const char*, bool spawn_pty)`
- `lmux_surface *lmux_surface_create_ex(lmux_workspace*, const char*, bool spawn_pty)`

`lmux_workspace_create` / `lmux_surface_create` keep their signatures and
delegate with `true`, so no existing caller changes behaviour. The restore calls
the `_ex` variants with `false`.

### Deliverables
- [ ] **F1** — `workspace_create_ex` / `lmux_surface_create_ex`, spawning only when asked
- [ ] **F2** — `snap_load_workspaces` and `snap_load_surfaces` use the `_ex` variants with `false`
- [ ] **F3** — no `forkpty` occurs during a restore (verifiable via strace or child-count)
- [ ] **T1** — RED: restoring N workspaces forks no more children than the workspace count needs (i.e. 0 during restore)
- [ ] **T2** — RED: a restored workspace still has its surfaces and panes from the snapshot, with correct titles — deferral must not change what is restored
- [ ] **T3** — RED: `workspace.create` / `surface.create` still spawn a working pty (no regression to the interactive path)
- [ ] **T4** — RED: restoring is measurably faster than the ~52 ms/workspace baseline

### Definition of Done
- [ ] Restoring a 100-workspace snapshot forks **0** children
- [ ] `lmux_workspace_create()` and `lmux_surface_create()` still spawn a pty — `workspace.create` then `read-screen` returns content
- [ ] Restored workspaces/surfaces/panes match the snapshot exactly (titles, cwd, pane commands)
- [ ] 100-workspace restore completes in under 5 s (baseline was 5.18 s for the same fixture; target < 2 s)
- [ ] `make test` exits 0, ≥ 246 + 4 new tests, none deleted or weakened
- [ ] `make test-fuzz` exits 0
- [ ] `ASAN_OPTIONS=detect_leaks=0 make test` reports zero sanitizer findings
- [ ] `git diff --stat` touches only `src/core/model.c`, `tests/test_integration.py`, `dev-notes/GOAL_PTY_DEFER.md`

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
git checkout -b perf/defer-pty-on-restore master
make clean && node scripts/build-core.mjs && node scripts/build-cli.mjs
ASAN_OPTIONS=detect_leaks=0 make test      # serially
make test-fuzz

# T1 — no forks during restore. Count children before/after.
H=$(mktemp -d /tmp/ptyd-XXXX); mkdir -p "$H/.config"
export HOME=$H XDG_CONFIG_HOME=$H/.config
python3 -c "
import json
ws=[{'id':i,'title':f'w{i}','cwd':'/tmp','git_branch':'','surfaces':[]} for i in range(1,101)]
open('$H/big.json','w').write(json.dumps({'version':1,'workspaces':ws}))"
nohup ./build/lmux daemon >/dev/null 2>&1 & sleep 3
BEFORE=$(pgrep -c -P $(pgrep -f 'build/lmux daemon') || echo 0)
time ./build/lmux --json snapshot.load "$H/big.json"
AFTER=$(pgrep -c -P $(pgrep -f 'build/lmux daemon') || echo 0)
echo "children before=$BEFORE after=$AFTER   # must be unchanged"

# T3 — interactive path still spawns
./build/lmux --json workspace.create interactive
./build/lmux --json read-screen | head -c 60
```

### Anti-Drift Rules
- **No module-level flag.** A global "defer" toggle can be left set if a
  restore fails partway, silently disabling pty spawn for the rest of the
  daemon's life. Use explicit parameters.
- Do **not** change the public signatures of `lmux_workspace_create` or
  `lmux_surface_create`. Other callers depend on the spawning behaviour.
- Do **not** defer pty for the **saved** panes in the snapshot. Those are real
  terminals the user is restoring and must get their pty; only the throwaway
  placeholder is deferred. `lmux_pane_split()` already passes `false` during
  restore — **check whether that is itself a bug** and report it rather than
  silently changing it.
- Do not change the snapshot format.
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- If this needs a change to `include/lmux.h`, stop and report it.

### Estimated Effort
Half a day. Two constructor variants plus call sites.

### Merging
Merge only when all CI checks pass and an independent `goal-met` audit returns
MET. Do not merge on a self-audit.

---

## Open question found during investigation

`lmux_pane_split()` is called during restore with `spawn_pty = false`, meaning
**panes restored from a snapshot get no pty at all**. That may be intentional
(the host attaches them later) or it may mean a restored terminal is dead. It is
outside this goal's scope — this goal defers only the *placeholder* pty — but it
should be investigated next.