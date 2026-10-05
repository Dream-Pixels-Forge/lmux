## Goal: Make lmux_config_save create its parent directories (B6)

### Objective
`lmux_config_save()` must create missing parent directories at any depth, so a
config write cannot silently fail on a machine without `~/.config`.

### Context
Found and recorded during the Milestone 9 audit as a deferred minor issue. It is
the same silent-failure class as B1: `lmux_config_save()` does
`mkdir(dir, 0755)` on the **immediate** parent only (`src/core/config.c:517`)
and ignores the return value. If `$HOME/.config` does not exist, that `mkdir`
fails with `ENOENT`, the subsequent `fopen(tmp, "w")` fails, and the function
returns `false`. Both callers discard that return:

- `config.set` (`model.c`) → reports `{"ok":true}`
- `model_save_config()` → reports `{"ok":true}`

So on a machine with no `~/.config`, `lmux config.set font_size 14` prints
success and writes nothing. Repro used during the audit: an isolated test HOME
without `.config` failed to persist a group until the test harness created the
directory itself.

This is last-writer territory only for the top level — every real `$HOME` has
`.config` — but the failure is silent and the class is exactly the one Milestone 9
spent its effort removing.

### Deliverables
- [ ] **F1** — `lmux_config_save()` creates each missing path component (not just the leaf), still respecting an existing directory
- [ ] **T1** — RED test: saving to a path whose parent chain is entirely absent returns true and the file is readable back
- [ ] **T2** — RED test: an existing parent directory is reused, not clobbered
- [ ] **T3** — the pre-existing config suite still passes (backward compatible)

### Definition of Done
- [ ] `lmux_config_save(cfg, "/tmp/a/b/c/config.json")` with none of `a`, `b`, `c` present → returns `true`, file loads back
- [ ] Saving twice into the same path succeeds (idempotent, existing dir reused)
- [ ] `tests/test_config.c` gains 2 tests; suite goes 10 → 12, 0 failed
- [ ] `make test` → 223 integration tests, 0 failed, zero sanitizer findings
- [ ] `make test-fuzz` → pass
- [ ] `git diff --stat` touches only `src/core/config.c` and `tests/test_config.c`

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
make clean && node scripts/build-core.mjs
ASAN_OPTIONS=detect_leaks=0 make test
make test-fuzz
# deep-parent live check
H=$(mktemp -d); rm -rf $H; HOME=$H XDG_CONFIG_HOME=$H/nope/deeper nohup ./build/lmux daemon &
sleep 3; HOME=$H XDG_CONFIG_HOME=$H/nope/deeper ./build/lmux --json config.set font_size 14
test -f $H/nope/deeper/lmux/config.json && echo "B6 FIXED" || echo "B6 STILL BROKEN"
```

### Anti-Drift Rules
- Fix **only** the parent-directory creation. Do not restructure the save path,
  the atomic temp+rename, or the JSON emission.
- Do not change the on-disk format.
- Do not touch Milestone 9's fixes; they are already audited and merged-in-place.
- No test may be deleted or weakened to reach green.
- If this needs a change to `include/lmux.h`, stop and report it.

### Estimated Effort
Under an hour. One function, two tests.

### Pairing Strategy
Single file pair (`config.c` + `test_config.c`), strictly serialized: one agent
writes the tests RED, the same agent implements, then the `goal-met` audit. No
subagent parallelism — there is no disjoint tree to split.
