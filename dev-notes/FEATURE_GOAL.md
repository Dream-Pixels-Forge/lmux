## Goal: Fix config persistence round-trip defects in lmux 1.1.1 → 1.1.2

> **SPEC REWRITTEN 2026-10-03.** The previous version of this file proposed five
> "new features". A Phase 0 evidence audit showed three already existed and one
> was a non-issue. See "Audit of the previous spec" at the bottom for what was
> dropped and why. This version replaces feature work with confirmed correctness
> defects, each backed by a live reproduction or a source-level proof.

### Objective
Fix four confirmed config-persistence defects — two of them silent user-data-loss
bugs in an advertised CLI feature — and add the round-trip regression tests whose
absence let them ship green.

### Context
Phase 0 audit of the prior 1.2.0 feature spec surfaced these while verifying
existing config behavior. All four are reproduced below.

**B1 — `workspace.group.*` never persists (data loss).**
`lmux_config_save` is called from exactly **one** place in `model.c` (line 3856,
inside `config.set`). `workspace.group.create`, `.add`, and `.remove` mutate
`cfg->workspace_groups` in memory and return `{"ok":true}` without ever writing.
Repro: `./build/lmux --json workspace.group.create mygroup` → `{"ok":true}`, but
`~/.config/lmux/config.json` mtime is unchanged and `workspace_groups` stays `[]`.
The group is silently lost on daemon restart.

**B2 — `workspace_groups` save/load formats disagree three ways (data loss).**
- key: save writes `"workspace_groups"` (`config.c:379`), load reads `"groups"` (`config.c:255`)
- shape: save writes an array of objects, load expects an object keyed by name
- IDs: save writes integers (`fprintf("%u")`), load expects strings (`["1","2"]`)

So even with B1 fixed, a saved group would still never load back. The load branch
is unreachable from anything lmux itself writes.

**B3 — `themes` is write-only.**
`lmux_config_save` serializes `themes` (`config.c:366-374`) and
`lmux_config_find_theme()` searches the vector (`config.c:411`), but the load path
has **no parse branch for `themes`** (compare: `keybindings` is parsed at
`config.c:218-247`). Any hand-authored theme in config.json is silently ignored
and `find_theme` always returns NULL for disk-loaded config.

**B4 — `config.set theme <value>` accepts unvalidated values.**
The key is validated (unknown key → `invalid_params`, the issue #6 fix) but the
value is not: `snprintf(app->config->theme, ..., "%s", value)` accepts any string
and persists it. The GUI then does `gui/main.py:167`
`self._theme.set_active(0 if theme == "dark" else 1)`, so **any** non-`"dark"`
value renders as light with no warning. Found by setting `theme=Dracula` during
the audit; config was reverted to `"dark"` and `config.json:4` confirmed restored.

**B5 — `keybindings` also never round-trips (found during F2 implementation).**
Same defect class as B2, found by noticing that `test_load_keybindings` fed
`{"keybindings": {"split-v": "C-b percent"}}` (an object) while `save` writes
`"keybindings": [ {"key": ..., "action": ...} ]` (an array). The loader gated on
`if (kb_start && *kb_start == '{')`, so with save output the branch was never
entered and **zero** keybindings were ever parsed from a real config file.
This is why the finding is stated as a pattern, not three coincidences:

| Collection | `save` writes | `load` expected | Round-tripped? |
|---|---|---|---|
| `keybindings` | array of `{key,action}` | object `{name: action}` | **no** |
| `themes` | array of `{name,fg,bg,cursor}` | *(no branch at all)* | **no** |
| `workspace_groups` | array of `{name,workspace_ids:[int]}` | object `{name: [str ids]}` | **no** |

All three were write-only. All three tests passed anyway, because each fed the
loader a hand-written shape it happened to accept and none exercised `save`
output. One defect pattern, three collections, three green tests.

**B1–B5 matter more than the features originally proposed:** these are
correctness and data-loss defects in features the CLI already advertises in
`lmux --help`, not speculative enhancements. Per this project's own PRIDES
posture (memory safety / CI hardening first), they outrank adding an fd monitor.

### Deliverables

**Fixes** (all implemented)
- [x] **F1 (B1)** — `model_save_config()` added at file scope in `model.c`; `workspace.group.create`, `.add`, `.remove` call it after a successful mutation
- [x] **F2 (B2 + B5)** — loader parses the array-of-objects form `save` writes for **all three** collections; legacy object-keyed-by-name forms still accepted for `keybindings` and `groups`, so no existing test or user file breaks
- [x] **F3 (B3)** — loader parses the `themes` array into `cfg->themes`; `lmux_config_find_theme()` now works for disk-loaded config
- [x] **F4 (B4)** — `config.set theme` rejects anything but `dark`/`light` with `invalid_params`; value is not mutated or saved on rejection

**Tests** (all written RED first, then confirmed GREEN)
- [x] **T1** — `tests/test_config.c::test_groups_save_load_roundtrip` — real temp file, `save` then `load`, asserts the group and both workspace ids survive
- [x] **T2** — `tests/test_config.c::test_load_themes` — save-format themes array; asserts 2 parsed and `find_theme("dracula")` returns the right `bg`
- [x] **T3** — `tests/test_integration.py::TestConfigPersistence::test_invalid_theme_value_is_rejected` — `ok:false`, `invalid_params`, setting unchanged
- [x] **T4** — `tests/test_integration.py::TestConfigPersistence::test_group_create_is_persisted_to_disk` — group name present in the real `config.json`
- [x] **T5** — `tests/test_config.c::test_keybindings_save_format_loads` — save-format keybindings array parses (this is the test that would have caught B5)
- [x] **T6** — `tests/test_integration.py::TestConfigPersistence::test_group_survives_daemon_restart` — second daemon over the same `HOME` still lists the group
- [x] **T7** — `tests/test_integration.py::TestConfigPersistence::test_valid_theme_values_are_accepted` — `dark` and `light` still succeed (no regression to the valid path)

**Supporting change (not a new deliverable)**
- `_isolated_client()` gained an optional `home=` parameter so a persistence test can restart a daemon against the same `HOME`; it now also pins `XDG_CONFIG_HOME` and creates `HOME/.config`, so the developer's real config can never be touched by a test.

**Not in scope (deferred, deliberately)**
- fd monitor (D2) — no such feature exists, but it is an *enhancement*; deferred behind these correctness fixes
- named color themes Solarized/Dracula/Monokai — **blocked by B3/F3**; the loader ignores the themes array entirely, so shipping palettes first would produce silently-unapplied themes. Re-scope after F3 lands.

### Definition of Done
All binary, all independently checkable:
- [ ] `./build/lmux --json workspace.group.create g1` → `ok:true`, and `~/.config/lmux/config.json` contains `g1` (T4)
- [ ] Daemon restart preserves the group (load parses saved form) (T1)
- [ ] A theme written to `config.json` survives reload and `find_theme` returns non-NULL (T2)
- [ ] `./build/lmux --json config.set theme Bogus` → `ok:false`, `error.code == "invalid_params"`, disk value unchanged (T3)
- [ ] `config.set theme dark` still succeeds (no regression to the valid path)
- [ ] `./build/lmux --json config.get theme` returns `"dark"`
- [ ] `make test` exits 0 — the existing integration tests plus new cases, no test deleted or skipped
- [ ] `make test-fuzz` exits 0
- [ ] `ASAN_OPTIONS=detect_leaks=0 make test` reports zero sanitizer findings
- [ ] `git diff --stat` touches only `src/core/config.c`, `src/core/model.c`, `tests/test_config.c`, `tests/test_integration.py`, and this file

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
make clean && node scripts/build-core.mjs && node scripts/build-cli.mjs
pkill -9 lmux; sleep 1; nohup ./build/lmux daemon >/tmp/lmuxd.log 2>&1 & sleep 3

# T4 — group reaches disk (B1)
./build/lmux --json workspace.group.create persist_check
grep -q 'persist_check' ~/.config/lmux/config.json && echo "T4 PASS" || echo "T4 FAIL"

# B2 — group survives restart (reload parses saved form)
pkill -9 lmux; sleep 1; nohup ./build/lmux daemon >/tmp/lmuxd.log 2>&1 & sleep 3
./build/lmux --json workspace.group.list   # must still contain persist_check

# T3 — invalid theme value rejected (B4)
./build/lmux --json config.set theme Bogus            # expect ok:false / invalid_params
./build/lmux --json config.get theme                   # expect "dark", unchanged

# full suite
make test && make test-fuzz
ASAN_OPTIONS=detect_leaks=0 make test
```

### Anti-Drift Rules
- Fix **only** F1–F4. The fd monitor and named color themes are explicitly deferred — implementing them now is scope drift.
- Do **not** change the on-disk format beyond making load accept what save already writes. Keep backward compatibility with the legacy `"groups"` object form rather than migrating users' files.
- Do **not** rename or delete `test_load_groups`; extend it to cover the save→load direction.
- Do **not** touch the 1.1.1 bug-fix logic (FD_CLOEXEC, response framing, rw_lock, parser lengths) — different files, unrelated behavior.
- Do **not** bump the version. That is a release step after this goal is MET.
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- If a fix requires changing a public struct in `include/lmux.h`, stop and report it — that is a wider blast radius than this goal authorizes.

### Estimated Effort
Half a day. Four small, well-localized fixes plus four regression tests — a
deliberate contrast with the previous spec's "2-3 weeks", three quarters of which
described work that already exists.

### Pairing Strategy
F1–F4 all touch `src/core/config.c` and/or the group commands in `model.c`, so
they are **serialized, not parallelized** — subagents writing these files
concurrently would conflict. Correct approach:
1. T1/T2/T3/T4 written first, RED, by one agent against `tests/`
2. F1–F4 implemented by one agent against `src/core/`
3. Independent `goal-met` audit re-running every Verification Step from scratch

Parallelism is appropriate only across the two disjoint trees (`tests/` vs
`src/core/`), and only for test-authoring vs. fix-authoring — never two agents in
the same file.

---

## Audit of the previous spec

Recorded so the same mistake is not repeated. Each "feature" was proposed in
conversation without first grepping the codebase:

| Proposed as new | Actual state (evidence) | Disposition |
|---|---|---|
| GUI persistent workspace state | `gui/session_index.py` (9.2 KB), `gui/session_reopen.py` (5.9 KB, `restore_session()`), `auto_save_session` config key | Dropped — already built |
| GUI agent status panel | `gui/agent_sessions.py` (19.3 KB) "Agent session panels for lmux — web-rendered UI with hibernation", `gui/task_manager.py` (10.8 KB) | Dropped — already built |
| CLI `config.get`/`config.set` | In `--help`; `model.c:3775`, `model.c:3822`; live-tested OK; 14-key schema; unknown-key validation | Dropped — already built |
| Named themes (Solarized/Dracula/Monokai) | GUI offers only dark/light (`main.py:125-128`, `settings_ui.py:203-205`); `themes: []` exists but is never parsed | **Reframed** — the real blocker is B3/F3, not missing palettes |
| Live fd monitor | Genuinely absent (0 hits for `fd_count`, `/proc/self/fd`, `fd_monitor`) | Deferred — real, but an enhancement; correctness first |

**Lesson encoded as a rule:** before writing any spec line asserting a feature is
absent, grep for it and record the evidence. The previous spec would have had the
pipeline rebuild and re-test working, already-covered functionality.
