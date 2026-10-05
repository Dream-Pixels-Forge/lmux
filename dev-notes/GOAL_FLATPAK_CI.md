# Goal: Put the flatpak build path in CI (and fix the stale entry points it exposes)

**Date:** 2026-10-05
**Source:** `dev-notes/HANDOFF.md` open item 2 — "The flatpak path is not in CI.
All three defects in #31 survived because nothing ever built it."

## Goal: Add the flatpak build path to CI

### Objective

Make CI build `packaging/io.github.lmux.lmux.yml` with `flatpak-builder` on
every PR and push to master, and fix the two stale local entry points discovered
while scoping it (`make flatpak` references a nonexistent manifest; the wrapper
hardcodes the pre-#32 runtime).

### Context

#31's three defects (dead `--build-arg`, no network for pip, wrong wheel tags)
all survived because nothing in CI ever ran `flatpak-builder` against the
manifest — the metainfo test explicitly says so. While scoping this, two more
instances of the same class were found:

- `Makefile` `flatpak` target runs `flatpak-builder … packaging/io.lmux.lmux.yml`
  — that file does not exist (real name: `io.github.lmux.lmux.yml`). `make
  flatpak` can never have worked in its current form.
- `packaging/flatpak-wrapper.sh` pre-installs `${ref}//46`; PR #32 moved the
  manifest to runtime **50**. The wrapper would install the wrong runtime.

TDD applies: the tests are written first and must fail before any fix lands.

### Deliverables

- [ ] Three new tests in `tests/test_integration.py` (written first, RED):
  1. `make flatpak`'s manifest path resolves to an existing file.
  2. The runtime version `flatpak-wrapper.sh` installs equals the manifest's
     `runtime-version`.
  3. `.github/workflows/ci.yml` contains a job that invokes `flatpak-builder`
     on `packaging/io.github.lmux.lmux.yml`.
- [ ] `Makefile` `flatpak` target points at the real manifest.
- [ ] `packaging/flatpak-wrapper.sh` derives the runtime version from the
  manifest instead of hardcoding `46`.
- [ ] A `flatpak` job in `.github/workflows/ci.yml` that installs the
  flatpak toolchain, adds Flathub, and builds the manifest (with the SDK/ runtime
  download cached).

### Definition of Done

- [ ] `python3 tests/test_integration.py` — all tests pass, including the three
      new ones, and no pre-existing test was removed, skipped, or weakened.
- [ ] The three new tests fail when run against unmodified `master` (RED
      evidence recorded before implementing).
- [ ] `grep -n 'flatpak-builder' .github/workflows/ci.yml` shows the build step.
- [ ] `bash -n packaging/flatpak-wrapper.sh` and `make -n flatpak` both succeed.
- [ ] No core/CLI code touched → no rebuild required; `make test-unit` still
      passes.

### Verification Steps

1. RED evidence: `python3 tests/test_integration.py <the three new names>` on
   unmodified master → 3 failures recorded.
2. GREEN: same command after implementation → 3 passes.
3. Full suite serially: `make test-unit && make test-integration && make
   test-fuzz` — all green, test count ≥ baseline (306 integration + 12 config
   unit + 23 model + 8 osc).
4. `make -n flatpak` expands without error and names
   `packaging/io.github.lmux.lmux.yml`.
5. Wrapper/manifest agreement: test 2 passes (wrapper installs `//50`).
6. CI job present and syntactically valid: `python3 -c "import yaml;
   yaml.safe_load(open('.github/workflows/ci.yml'))"` (if pyyaml available) or
   equivalent structural check via the test suite.

### Anti-Drift Rules

- Do not fix unrelated Makefile targets, wrapper quirks (e.g. the duplicate
  `--repo` flag), or any other bit-rot found along the way — file it in the
  handoff instead.
- Do not change the manifest itself (PR #32 just verified it end-to-end).
- Do not add new dependencies or new workflows beyond the `flatpak` job in
  `ci.yml`.
- Do not claim completion without running every Verification Step.

### Estimated Effort

One sitting: ~3 tests + 3 small edits + suite run.
