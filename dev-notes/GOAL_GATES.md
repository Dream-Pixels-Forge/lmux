## Goal: Activate the dormant dependency gate, confine snapshot.save, finish truncation detection (#11/#12/#13 remainder)

### Objective
Fix the three verified gaps left after PR #17, each backed by a live
reproduction or a source-level proof, on a branch off `master`.

### Context
PR #17 merged to master as `e279599` with CI green. It closed #12 and #11
partially, leaving three items. **Every claim below was verified by inspection
or reproduction before being written here** — two of the three turned out to
differ from how the issue described them.

**F1 — the fail-closed dependency gate has never run (#13).**
`.github/workflows/trivy.yml` already exists and is already correct:
`exit-code: 1`, `severity: HIGH,CRITICAL`, filesystem scan, SARIF upload. But
its triggers are `branches: [main]`, while this repo's default branch is
`master`. Evidence:

- `gh repo view --json defaultBranchRef` → `master`
- `ci.yml`, `nightly.yml` → `branches: [master]`
- `trivy.yml` → `branches: [main]` (the only workflow targeting `main`)
- PR #17's checks were `build-and-test`, `lint`, `packaging`, `sanitizers` —
  **no "Dependency Scanning"**, consistent with it never firing

So #13 is not "add a gate"; it is "point the existing gate at the branch that
exists". One line.

**F2 — `snapshot.save` is still an arbitrary file write (#12 step 4).**
It calls `is_safe_path(path, NULL)`, which rejects `..` components but places
no containment, so an absolute path anywhere is accepted. Reproduced:

```
$ ./build/lmux --json snapshot.save "$T/.config/autostart/pwn.desktop"
{"ok":true,"result":{"path":"/tmp/s12-xRaj/.config/autostart/pwn.desktop"}}
$ head -c 40 "$T/.config/autostart/pwn.desktop"
{"version":1,"workspaces":[
```

Note: an earlier attempt in the same directory returned `save_failed` — that was
only because the parent directory did not exist, **not** a security control.
Creating it first let the write through. Do not mistake that for a block.

**F3 — two nested truncation clamps are still fully silent (#11 remainder).**
`buf_copy[4096]` now warns after #17. The other two do not:

- `sfcopy[2048]` — `if (sblen >= sizeof sfcopy) sblen = sizeof sfcopy - 1;`

### Deliverables

- [ ] **F1** — `trivy.yml` triggers on `master` (push + pull_request)
- [ ] **F2** — `snapshot.save` confines writes to a data root, mirroring the treatment `browser.screenshot` got in #17
- [ ] **F3** — `sfcopy` and `pncopy` clamps log a warning, matching `buf_copy`
- [ ] **T1** — RED test: `snapshot.save` to an absolute path outside the root is rejected and creates no file
- [ ] **T2** — RED test: `snapshot.save` with no path still lands in the default location
- [ ] **T3** — RED test: an oversized surface/pane block produces a warning rather than silence
- [ ] **T4** — RED test: the dependency workflow references `master`, not `main`

### Definition of Done
All binary and independently checkable:
- [ ] `grep -c 'branches: \[main\]' .github/workflows/trivy.yml` → `0`
- [ ] `snapshot.save` with an absolute path outside the root → `ok:false`, `invalid_params`, **and no file created**
- [ ] `snapshot.save` with no path → `ok:true`, file under the data root
- [ ] `snapshot.save` to a relative filename inside the root → `ok:true`
- [ ] A snapshot containing a surface block over 2048 bytes logs a warning naming the clamp
- [ ] `make test` exits 0, test count ≥ 232 + 4 new, no test deleted or weakened
- [ ] `make test-fuzz` exits 0
- [ ] `ASAN_OPTIONS=detect_leaks=0 make test` reports zero sanitizer findings
- [ ] `git diff --stat` touches only `.github/workflows/trivy.yml`, `src/core/model.c`, `tests/test_integration.py`
- [ ] After push, `gh pr checks` lists a **"Dependency Scanning"** check on the PR — proof F1 took effect

### Verification Steps
```bash
cd /home/dimona/Dream-Pixels-Forge/Dev/cli/lmux
git checkout -b chore/deps-gate-and-snapshot-root
make clean && node scripts/build-core.mjs && node scripts/build-cli.mjs
ASAN_OPTIONS=detect_leaks=0 make test
make test-fuzz

# F1 — the gate now points at a real branch
grep -A2 'branches:' .github/workflows/trivy.yml

# F2 — arbitrary write refused, daemon survives
H=$(mktemp -d /tmp/snaproot-XXXX); mkdir -p "$H/.config/autostart"
HOME=$H XDG_CONFIG_HOME=$H/.config nohup ./build/lmux daemon >/tmp/sr.log 2>&1 & sleep 3
HOME=$H XDG_CONFIG_HOME=$H/.config ./build/lmux --json snapshot.save "$H/.config/autostart/pwn.desktop"
test -f "$H/.config/autostart/pwn.desktop" && echo "STILL WRITABLE" || echo "CONFINED"
timeout 5 env HOME=$H XDG_CONFIG_HOME=$H/.config ./build/lmux --json ping

# after push, the gate must appear
gh pr checks <N>
```

### Anti-Drift Rules
- Fix **only** F1–F3. The JSON parser rewrite is out of scope and stays open.
- F1 is a one-line branch-name change. Do **not** "improve" the trivy config,
  add new scanners, or change severities — an unrequested change to a security
  gate could turn the build red for unrelated reasons.
- F2 must **mirror** the #17 `browser.screenshot` treatment (same helper shape,
  same `invalid_params` code, same escaping) rather than inventing a second
  policy. Reusing `is_safe_path()` is required; do not add a parallel checker.
- F2 is a behaviour change: callers passing an absolute path now get rejected.
  State that plainly in the PR body.
- Do not change the snapshot **format** — F2 changes where it is written, not
  what is written.
- Do not touch `src/cli/main.c`, `src/core/config.c`, or anything merged in
  PR #17 beyond what F3 requires.
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- If activating the dependency gate turns it **red** on the branch, do **not**
  suppress it to get green. Report the finding — a gate that fails on first
  real run is information, not an obstacle.

### Estimated Effort
Half a day. F1 is one line. F2 is a small, mirrored change with four tests.
F3 is two warning blocks.

### Pairing Strategy
Strictly serialized — F1, F2, and F3 all touch files whose tests share one
suite, and two agents in `model.c` would conflict. One agent writes T1–T4 RED,
the same agent implements, then an independent `goal-met` audit. No subagent
parallelism: there is no disjoint tree to split.

### Merging
Branch off `master`, open a PR, and merge only once all four CI checks pass
**and** "Dependency Scanning" appears in the check list. If the gate fires and
fails on its first real run, report and stop — do not weaken it.

- `pncopy[1024]` — `if (pblen >= sizeof pncopy) pblen = sizeof pncopy - 1;`

A surface with many panes or a pane with a long command is clipped with no
signal at all, so a restore silently drops panes.

**Explicitly out of scope:** replacing the scanner with a real JSON parser. That
is the structural fix #11 asks for, it is a large change in code Milestone 8 and
PR #17 both hardened, and it does not affect greenness. It stays open.
