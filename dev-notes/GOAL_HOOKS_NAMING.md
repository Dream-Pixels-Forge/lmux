# Goal: Hook execution, naming parity, and cleanup

## Objective
Finish the two items PR #22 deliberately left open, and clean up. Several
findings below **correct conclusions I stated earlier**, which is why they are
written down rather than quietly patched.

## Context
PR #22 implemented 10 advertised-but-undispatchable commands and left two notes:

1. `hooks.*` stores and lists but does **not execute**.
2. `naming.suggest` duplicates GUI logic in C and will drift.

Investigating both to decide what "fix" means overturned several earlier claims.

### C1 — The GUI *does* call these commands (I previously said it did not)

In PR #21/#22 I asserted "no GUI code ever sends any of them, so this is a false
API promise rather than a broken feature." That was **wrong**.

`gui/hooks_setup.py:329` builds:

```python
cmd = ["lmux", "hooks", "add", event, hook_config["command"]]
```

and treats `returncode == 0` as success. The feature was broken and I called it
unused. The grep behind my earlier answer searched for the quoted string
`"hooks.add"`; this call site passes the tokens separately, so it never matched.

### C2 — `lmux hooks add` silently does the wrong thing

`build_json_command()` maps the bare word `hooks` to `hooks.list` via its
shorthand table (`src/cli/main.c:941`). So the GUI's three-token invocation runs:

```
$ lmux hooks add stop "/bin/true"
{"ok":true,"result":{"hooks":[...]}}      # a LIST, exit 0
```

It returns **exit 0**, so `hooks_setup.py` records the hook as installed while
nothing was registered. Silent corruption: the GUI believes setup succeeded.
This is pre-existing and not introduced by #22.

### C3 — `session-start` / `prompt-submit` are agent-side events

The four lifecycle names come from `gui/hooks_setup.py` agent configs
(`~/.claude/settings.json` and friends). The **agent** fires them and the hook
command calls *back into* lmux (e.g. `lmux agent.spawn -- claude`).

The daemon emits none of these names — it emits `agent.spawned`,
`agent.stopped`, `pane.focused` and so on. So "execute hooks on daemon events",
as I framed it in #22, is **the wrong design**: there is no daemon-side
`session-start` to hang execution on.

## Decisions

### D1 — Make the CLI accept `hooks add <event> <script>`

Match what the GUI actually invokes. This is the real bug behind C2 and is
squarely in scope. Accepted forms: `hooks add|list|remove`, `hooks.add`,
`hooks_add`.

### D2 — Hook firing is agent-side; say so plainly

Given C3, the daemon registry's honest job is to **record the agent-side
binding** so `hooks.list` can report it and `hooks.remove` can undo it. It must
**not** pretend to execute on daemon events, because those events do not exist in
the daemon. So no fabricated execution path. Instead:
- record an optional `agent` field so a hook records which config owns it,
- keep `hooks.list` truthful,
- document explicitly that firing is performed by the agent process.

This replaces the "store and list but does not execute" note with something
defensible rather than merely incomplete.

### D3 — `naming.suggest`: port the real rules, then pin them with a parity test

The GUI's `suggest_names()` (in `gui/auto_naming.py`) implements an ordered
confidence chain the C version does **not** have:

1. git repo root basename
2. `repo:branch`, when the branch is not main/master
3. `package.json` `name`, scope stripped
4. directory basename
5. `basename (project-type)` from a marker table

Porting this into C genuinely duplicates logic, so the drift risk is real. The
durable fix is a **parity test** running both implementations over the same
fixtures and asserting identical output, so drift becomes a test failure.

### D4 — Cleanup

- Remove the unused `LIFECYCLE` attribute left in `TestNewCommandsWorkFromTheShell`.
- Re-verify no dead code or new warnings.

## Deliverables
- [ ] **F1** — CLI accepts `hooks add|list|remove`
- [ ] **F2** — hooks registry records the owning agent; docs state firing is agent-side
- [ ] **F3** — `naming.suggest` implements the full ordered rule chain
- [ ] **F4** — GUI↔daemon naming parity test over real fixtures
- [ ] **F5** — cleanup of leftovers from #22
- [ ] **T1** — RED: `lmux hooks add stop /bin/true` registers and `hooks list` shows it
- [ ] **T2** — RED: `hooks_setup.py`'s exact argv form registers instead of listing
- [ ] **T3** — RED: naming parity against `suggest_names()`

## Definition of Done
- [ ] `lmux hooks add stop /bin/true` registers the hook (was: silently listed)
- [ ] Daemon and GUI produce identical suggestions for every fixture
- [ ] `naming.suggest` returns repo/branch/package.json forms, not just basename
- [ ] `make test` exits 0, none deleted/skipped/weakened
- [ ] `make test-fuzz`, `make test-unit` exit 0
- [ ] ASAN+UBSAN `detect_leaks=1` → zero findings
- [ ] No new compiler warnings

## Anti-Drift Rules
- **Do not fabricate a daemon-side `session-start` event** to make execution
  look implemented. If a name has no daemon event, document it instead.
- The parity test is the point of F4. Do not weaken it to a subset.
- Do not "fix" the GUI by changing it to the two-token form without also
  supporting the form it already uses — support both.
- Bounded registry, unchanged cap.
- No test deleted, skipped, or weakened.

## Verification Results

- **9 new tests** (4 hooks multi-token, 5 naming parity), all confirmed RED
  before the fix.
- `make test` → **286 tests, OK** (277 baseline + 9). `make test-fuzz`,
  `make test-unit` → passed.
- ASAN+UBSAN `detect_leaks=1` → **zero findings**, including the new
  `fork`/`pipe` path in naming.
- No new compiler warnings. No test deleted, skipped, or weakened (skips 1 → 1).

### The bug, proved

Ran the real `gui/hooks_setup.py` against a daemon, before and after:

```
BEFORE: GUI reports  success: True, installed: ['session-start','prompt-submit','stop']
        registry actually contains: 0 hooks        <-- silent data loss

AFTER:  GUI reports  success: True, installed: ['session-start','prompt-submit','stop']
        registry actually contains: 3 hooks        <-- session-start -> lmux agent.spawn -- claude --resume
```

`hooks_setup.py` treats `returncode == 0` as success, and the shorthand table
made every invocation return a **list** with exit 0, so it recorded hooks that
were never registered.

### Naming parity

The parity test compares the C implementation against
`gui/auto_naming.py::suggest_names()` on real fixtures — git repo, feature
branch, scoped npm package, Python project, and a plain directory. Divergence
before the fix was concrete:

```
gui   = ['thing', 'tmp0k9yhitr', 'tmp0k9yhitr (node)']
daemon= ['tmp0k9yhitr']
```

The C side missed `package.json` names, the `(type)` suffix, and git repo/branch
forms entirely. It now implements the full ordered chain.

### A correction to the record

Earlier (PR #21/#22) I stated the ten advertised commands were unused because no
GUI code referenced them. **That was wrong.** `gui/hooks_setup.py:329` calls
`lmux hooks add`, and my grep missed it because the tokens are passed separately
rather than as a single quoted string. The commands were not unused — they were
broken, which is a materially different finding.

### One design point, stated plainly

`session-start` and `prompt-submit` are **agent-side** events: the agent's own
config fires them and the hook command calls back into lmux. The daemon emits
neither — it emits `agent.spawned`, `agent.stopped`, `pane.focused`. So the
registry deliberately does **not** fabricate a daemon-side `session-start` event
to make execution look implemented. Its honest job is to record the binding so
`hooks.list` reports it and `hooks.remove` undoes it.