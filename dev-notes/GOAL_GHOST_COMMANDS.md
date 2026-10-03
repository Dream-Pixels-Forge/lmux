# Goal: Implement the 10 advertised-but-missing commands

## Objective
`lmux --help` advertises 10 commands the daemon never dispatches. Every one
returns `unknown_command`. Implement them against the subsystems that already
exist, and add a test that stops this class of defect recurring.

## Context
Follow-up to PR #21, whose alias sweep proved the hyphen/underscore footgun is
closed (0 unreachable). The same sweep surfaced a different defect: `--help` and
`is_readonly_cmd` promise commands that do not exist.

Audited: 88 commands advertised, 11 unreachable by that test, of which `lmux` is
a help usage artifact rather than a command. **So 10 real ghosts:**

```
ssh.list  ssh.connect  ssh.disconnect
hooks.add  hooks.list  hooks.remove
naming.suggest  focus.history
notification.hook.list  notification.hook.remove
```

Confirmed **no GUI code ever sends any of them**, so nothing is broken today;
this is a false API promise, not a broken feature. Five of them
(`ssh.list`, `hooks.list`, `focus.history`, `naming.suggest`,
`notification.hook.list`) are even listed in `is_readonly_cmd`, so the code
itself expects them to exist.

### What already exists (this is dispatch work, not new features)

| Command | Existing implementation |
|---|---|
| `ssh.list` | `app->ssh_sessions` + `lmux_ssh_session_count/at`; `ssh.session.list` already renders this shape |
| `ssh.connect` | `lmux_ssh_session_create()` + `lmux_ssh_session_attach()` |
| `ssh.disconnect` | `lmux_ssh_session_detach()` |
| `notification.hook.list` | `app->notification_hooks` + `lmux_notification_hook_count/at` |
| `notification.hook.remove` | `lmux_notification_hook_remove()` |

### What genuinely needs building

- **`focus.history`** — `lmux_focus_history` is fully implemented
  (`init`/`push`/`recent`/`clear`, `model.c:8305+`) but is **never instantiated**
  in `struct lmux_app` and never fed. So this needs: an instance in the app, a
  `push` call on every focus change, and the dispatch handler.
- **`hooks.*`** — no daemon-side hooks model exists at all. Needs a small
  registry (`add`/`list`/`remove`) plus dispatch.
- **`naming.suggest`** — the namer lives **only in the GUI**
  (`gui/auto_naming_integration.py::get_naming_suggestions`). The daemon has no
  naming rules. Needs a daemon-side suggester over the pane's working directory.

## Deliverables
- [ ] **F1** — 5 dispatch handlers over existing subsystems (ssh.*, notification.hook.*)
- [ ] **F2** — `focus.history`: instantiate the history in the app, push on focus change, dispatch
- [ ] **F3** — `hooks.add/list/remove`: minimal daemon-side registry
- [ ] **F4** — `naming.suggest`: daemon-side suggester over pane working dir
- [ ] **F5** — regression test: every command in `--help` is dispatchable

## Definition of Done
- [ ] All 10 return `ok:true` against a live daemon
- [ ] `focus.history` reflects **real** focus changes (not an always-empty array)
- [ ] `hooks.add` then `hooks.list` then `hooks.remove` round-trips
- [ ] `naming.suggest` returns a plausible name for a real working directory
- [ ] A test parses `--help` and fails if any advertised command is unknown
- [ ] `make test` exits 0, no test deleted/skipped/weakened
- [ ] `make test-fuzz`, `make test-unit` exit 0
- [ ] ASAN+UBSAN with `detect_leaks=1` reports zero findings
- [ ] Diff is scoped; `include/lmux.h` changes only if unavoidable

## Anti-Drift Rules
- **Do not delete these commands from `--help` or `is_readonly_cmd`.** The
  decision is to implement them. Removing the advertisement would make the
  regression test pass without fixing anything.
- Do **not** reimplement the subsystems; dispatch over what already exists.
- `focus.history` must not ship as a permanently-empty array — that would
  satisfy "returns ok:true" while being useless.
- `hooks.*` needs a real registry with real add/remove; a no-op stub is a
  failure, not an implementation.
- Bounded storage for every new registry, matching the existing patterns.
- No test may be deleted, skipped, or weakened to reach green.

## Open questions for review
1. `naming.suggest` duplicates GUI logic in C. Is a daemon-side suggester
   actually wanted, or should the advertisement be the thing that changes?
2. `hooks.*` has no defined semantics anywhere in the codebase. What is a hook
   meant to *do* here? A registry that stores and lists hooks is the honest
   minimum, but it may not match intent.
3. `ssh.connect`/`ssh.disconnect` overlap `ssh.session.create`/`kill`. Should
   these be aliases, or gain distinct semantics?

**Resolved during implementation** (see Verification Results): `hooks.*` binds
scripts to the four agent lifecycle events from ARCHITECTURE.md, confirmed with
the user. `ssh.disconnect` maps to `lmux_ssh_session_detach()`, which *unbinds*
the pane but keeps the session — destroying it is `ssh.session.kill`.

## Verification Results

- **21 new tests, all mutation-checked**: with `model.c` reverted to master,
  every one fails; restored, all pass.
- `make test` → **277 tests, OK** (256 baseline + 21). `make test-fuzz` and
  `make test-unit` → passed.
- ASAN+UBSAN with `detect_leaks=1` → **zero findings**, including after the
  hooks registry is populated and the daemon shuts down cleanly.
- No test deleted, skipped, or weakened; skip count unchanged (1 → 1).
- `include/lmux.h` untouched.
- End-to-end from the shell: `hooks.add`, `hooks.list`, `naming.suggest`,
  `focus.history`, `ssh.list`, `notification.hook.list` all return `ok:true`.

### Defects the new tests found (not the original audit)

1. **CLI/daemon argument-key mismatches.** Dispatch-level tests passed while the
   shell path stayed broken, because `build_json_command()` builds its own keys
   and two disagreed with the handlers: `hooks.add` sends `script` (handler read
   `command`) and `focus.history` sends `count` (handler read `n`). Both now
   accept either spelling, and a new test class drives each command through the
   real CLI.
2. **`notification.hook.remove` does not delete.** It sets `enabled = false`, so
   the list now reports `enabled` rather than implying the entry vanished.
3. **`ssh.disconnect` detaches rather than destroys** — my first test asserted
   the wrong semantics and was corrected to match the existing model.

### Two of my own errors worth recording

- I invoked `json_extract_long()`, which **does not exist** here; a grep misled
  me. The established pattern is `json_extract_string()` + `atol`.
- A test run produced a **stale-binary false failure**: `build/lmux` was 46s
  older than `model.c`, so shell tests failed against already-fixed code. Same
  class of error as the pre-fix verification in the earlier goal — rebuild and
  confirm timestamps before trusting a failure.