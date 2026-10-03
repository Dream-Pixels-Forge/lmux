# Handoff — lmux @ master

**Date:** 2026-10-03
**Branch state:** `master` @ `4219daa`, clean, in sync with origin
**Open issues:** 0
**Version:** 1.1.1

---

## Where things stand

Three PRs merged this session, all green on master:

| PR | Commit | What |
|----|--------|------|
| #17 | `e279599` | config collections were write-only; heap corruption on a bad tmux subcommand; arbitrary write in `browser.screenshot` |
| #18 | `ea220a8` | dependency gate pointed at `master` (it had never fired); `snapshot.save` confined to a data root; nested truncation announced |
| #19 | `4219daa` | snapshot scanner replaced with a string-aware parser |

`master` CI: `build-and-test` `sanitizers` `lint` `packaging` `Nightly Build`
`Dependency Scanning` — all passing. Master had been **red** since the PR #15
merge; it is green now.

Tests: **246 integration** + 12 config unit + 22 model + 8 osc. ASan/UBSan
clean, fuzz clean.

## Defects fixed and closed

| Issue | Defect | Severity |
|---|---|---|
| B1 | `lmux_config_save` called from one place; group writes never persisted | data loss |
| B2 | `workspace_groups` save/load disagreed 3 ways | data loss |
| B3 | `themes` serialized but never parsed | data loss |
| B4 | `config.set theme` accepted any value; GUI silently coerced | correctness |
| B5 | `keybindings` never parsed from a real file | correctness |
| B6 | `lmux_config_save` mkdir'd one level deep; silent save failure | silent failure |
| B7 | `translate_tmux_command` fell off a non-void function | **heap corruption** |
| #12 | `browser.screenshot` took an arbitrary write path | **arbitrary write** |
| #11 | Oversized snapshot wedged the daemon | **DoS** |
| #11 | `}` in a string value corrupted the whole restore | **silent corruption** |
| #13 | Fail-closed dependency gate never ran (targeted `main`, repo uses `master`) | dead gate |

## Behaviour changes users may notice

- `browser.screenshot` — absolute paths rejected; pass a filename relative to
  `XDG_CACHE_HOME/lmux/screenshots`. Default is `<root>/screenshot.png`.
- `snapshot.save` — same confinement; default is `<data-root>/snapshot.json`,
  no longer `$HOME/.lmux_snapshot.json`.

Both are breaking for callers that passed absolute paths. Nothing in the repo
did; external callers might.

## Practices now enforced

- `goal-loop` is mandatory after every goal: `goal-writer` → execute (TDD) →
  `goal-met`. Rules live in `~/.agents/AGENTS.md`; skill in
  `~/.agents/skills/goal-loop/`.
- The gate binds a verdict to a fingerprint of the goal spec, `HEAD`, the
  working diff and `git status`. **Any commit after an audit revokes it** —
  re-run the audit and re-record. `gate` also requires 10/10 confidence.
- Run `make test`, `make test-fuzz`, `make test-unit` **serially**. In parallel
  they relink the same binaries and fail `ETXTBSY` — an audit artifact that
  looks like a product failure.
- If you rebuild the core you **must** rebuild the CLI; it links the static
  library, and tests silently run against a stale binary otherwise.

## Known gaps, deliberately left open

1. **Restore spawns a PTY per workspace, then destroys it.**
   `lmux_workspace_create()` eagerly allocates a surface with a forked pty, and
   the restore immediately frees it. Measured cost **~52 ms per workspace**,
   independent of document size (40 ws / 138 KB = 2.14 s; 100 ws / 146 KB =
   5.18 s). A 900-workspace restore takes ~45 s. Next task — see
   `dev-notes/GOAL_PTY_DEFER.md`.
2. **`snap_read_string` clamps at `cap` silently.** Bounded and no worse than
   master (destination fields are the same size or smaller), but an
   over-long value is dropped without a warning.
3. **Naming drift** between `GOAL_SNAP_PARSER.md` (says `snap_parse_*`) and the
   implementation (`snap_read_*`). Spec is stale, code is fine.

## Documentation that is stale

`dev-notes/` accumulated specs during this work and several no longer describe
current behaviour. `PROGRESS.md` is the accurate record. `FEATURE_GOAL.md`,
`GOAL_B6.md`, `GOAL_GATES.md` and `GOAL_SNAP_PARSER.md` are historical goal
specs, kept for audit traceability — read them as "what was required at the
time", not "what the code does now".

## Working method that worked

The highest-value habit was **reproducing before believing**. Four findings
came from it, each contradicting how the issue was written:

- #12's "arbitrary write" also **wedged** the daemon, not just corrupted data.
- #11's "silent truncation" was really a **DoS** (0% CPU, unresponsive).
- #13's "add a gate" — the gate **already existed**, never fired.
- The `}` corruption was found by round-tripping a title with a brace, not by
  reading the code.

The second habit: **audit tooling fails too**. Twice a checker reported a
confident wrong answer — test counts read from the wrong stream, and
`check_goal.py grep` args inverted. Always verify the verifier before filing
the bug.

---

# Next task: defer PTY spawn during snapshot restore

Started immediately after this handoff. Spec: `dev-notes/GOAL_PTY_DEFER.md`.