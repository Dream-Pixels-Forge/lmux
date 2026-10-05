## Goal: Run the Python integration suite in CI

### Objective
Add an `integration` job to `.github/workflows/ci.yml` that builds core+CLI and runs the full `tests/test_integration.py` suite serially on every PR and push to master.

### Context
Handoff open item: "The Python integration suite is not in CI. The 309 tests exist, run green locally, and are the only full gate." Now 313 tests after PR #40 (harness fixes #37/#38 merged as `44ef100`), which removed the two known flakes that would have poisoned CI: the ptyd leak and the concurrent-runner socket kill. The suite is the only full gate — `ci.yml` runs `node scripts/test.mjs --unit` only.

Needs: ubuntu-latest needs `strace` (T1 fork-count test skips without it — must NOT skip in CI) and `zsh` (read-screen tests drive a real shell). The suite takes ~90 s locally; allow a generous timeout. Serial discipline: one runner per job (default), no matrix splitting.

### Deliverables
- [ ] **T1 (RED)** — `test_ci_runs_the_integration_suite`: asserts `ci.yml` contains a job that builds core+CLI and runs `tests/test_integration.py` (mirrors `test_ci_builds_the_flatpak_manifest` pinning pattern)
- [ ] **F1** — `integration` job in `ci.yml`: checkout, apt deps (incl. `strace`, `zsh`), setup-node 20, build core + CLI, run `python3 tests/test_integration.py`
- [ ] Goal-met audit + PR with green "Integration" check visible in `gh pr checks`

### Definition of Done
- [ ] T1 fails on master (no such job), passes on branch
- [ ] `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` parses
- [ ] After push, `gh pr checks` lists an "Integration" check that goes green on the PR
- [ ] `make test` (313 integration + unit) still exits 0 locally, serially; no test deleted/skipped/weakened
- [ ] `git diff --stat` touches only `.github/workflows/ci.yml`, `tests/test_integration.py`, `dev-notes/GOAL_INTEGRATION_CI.md`

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
git checkout -b chore/integration-suite-in-ci master
# RED: T1 fails on pristine master (AssertionError: 'tests/test_integration.py'
# not found in ci.yml) — recorded before the fix; GREEN on branch.
python3 tests/test_integration.py TestPackagingEntryPoints TestIntegrationSuiteInCI  # pin classes green
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
# after push: gh pr checks <N> must list Integration → green
```

### Anti-Drift Rules
- One job, no matrix. Do not split the suite across runners (shared-socket + timing assumptions).
- Do not touch `src/`, `include/`, `Makefile`, or the flatpak job.
- Do not weaken T1 to a substring that could match a comment — assert on job-shaped structure (job key + run steps).
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- If the job goes red in CI on first run, report the failure — do not suppress it to get green.

### Estimated Effort
One sitting. One test, one job (~25 lines of YAML), one suite run to confirm timing.
