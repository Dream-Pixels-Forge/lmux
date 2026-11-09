# GOAL — lmux Production Readiness & Multiplexer Maturity (tmux-parity program)

**Date:** 2026-10-18
**Status:** Proposed — awaiting execution
**Reference target:** tmux source tree at `Dev/cli/tmux` (reverse-engineered for maturity surface)
**Baseline:** lmux v1.2.0 — 217 integration tests + 37 C unit tests green; security/observability/supply-chain hardening already complete (see `GOAL.md`, `AUDIT.md`)

---

## Objective

Bring lmux from "feature-complete AI-agent terminal" to "production-ready, mature terminal
multiplexer" by closing the gap with tmux's core multiplexer primitives — full command
surface and grammar, runtime key-binding tables, a typed option system, `#{}` format
expansion, alert classes, environment handling, prompt/menu, and reference documentation —
each with binary verification gates, without regressing the existing 254-test baseline.

## Context

lmux's prior hardening goals (`GOAL.md`, `AUDIT.md`, `ROADMAP.md`) closed the security,
reliability, observability, testing, and supply-chain gaps. What remains is **maturity as a
terminal multiplexer**. tmux — the reference implementation studied at `Dev/cli/tmux` —
achieves this maturity through a set of orthogonal, well-tested subsystems. Reverse-engineering
that tree yields a concrete parity checklist. This goal turns the checklist into verifiable
milestones.

**Constraint:** C17 core daemon + Python GTK3/VTE GUI (unchanged). Features must land in the
core daemon / CLI and be exercised over the existing Unix-socket JSON protocol (and control
mode) so they are testable headlessly. GUI-only frills (in-app browser, email, weather) are
already done and are out of scope here.

**tmux maturity pillars → lmux gap map (the reverse-engineered evidence):**

| Pillar | tmux evidence (`Dev/cli/tmux`) | lmux status | Gap |
|---|---|---|---|
| Client/server + detach/attach | `server.c`, `client.c` | ✅ daemon + detach | — |
| Full command surface + grammar | ~50 `cmd-*.c`, `cmd-parse.y`, `cmd.c` | ⚠️ 10 tmux-compat + agent cmds, no grammar/aliases | P0 |
| Runtime key-binding tables | `key-bindings.c`, `key-string.c`, `list-keys`/`bind-key`/`unbind-key` | ⚠️ static config only | P0 |
| Typed option system + inheritance | `options-table.c` (64 KB), `options.c` | ❌ none | P0 |
| Format expansion `#{}` | `format.c` (163 KB), `format-draw.c` | ⚠️ partial — `src/core/format.c` has `#{var}`, `#{?cond:t:f}`, `#{W:}` loops, 10 vars | P1 |
| Alert classes | `alerts.c`, `monitor.c` (activity/silence/bell) | ⚠️ activity + bell; **no silence** | P1 |
| Environment handling | `environ.c`, `set-environment`/`show-environment` | ❌ none | P1 |
| Prompt / menu / confirm | `prompt.c`, `menu.c`, `confirm-before`, `command-prompt` | ❌ none | P2 |
| Status line (daemon-side) | `status.c`, `status-left`/`status-right` | ❌ none | P2 |
| Interactive modes | `mode-tree.c`, `window-tree.c`, `window-customize.c`, `window-clock.c` | ❌ none | P2 |
| Saved/preset layouts | `layout.c`, `layout-custom.c`, `layout-set.c` | ⚠️ split + canvas only | P2 |
| Copy mode (emacs + vi) | `window-copy.c` (197 KB) | ⚠️ vi only | P3 |
| Man page / reference docs | `tmux.1` (225 KB) | ⚠️ README only | P2 |
| Regression + fuzz suites | `regress/`, `fuzz/` | ✅ 217 + fuzz | — |

---

## Deliverables

- [ ] `src/core/cmd.c` (or equivalent) — a table-driven command registry with per-command
      schema, aliases, and `list-commands`, replacing ad-hoc dispatch for the tmux-compat
      surface and registering every existing command
- [ ] `src/core/keybind.c` — runtime key tables (`root`, `prefix`, `copy-mode-vi`,
      `copy-mode-emacs`) with `bind-key`/`unbind-key`/`list-keys` and `send-keys` routing
- [ ] `src/core/options.c` + `options-table.c` — typed option store (server/session/window
      scope, string/number/flag/colour) with defaults, `set-option`/`show-options`, and
      scope inheritance
- [ ] `src/core/format.c` — **extend the existing** `#{}` engine (already has `#{var}`,
      `#{?cond:t:f}`, `#{W:}` loops, 10 vars) to a documented variable table and wire it into
      `list-*` output and the status line
- [ ] `monitor.c` silence-alert support (alongside existing activity/bell), surfaced via
      `monitor-silence` and control-mode notifications
- [ ] `environ.c` — `set-environment`/`show-environment`/`update-environment` scoped to
      server/session/global
- [ ] `prompt.c`/`menu.c` — `command-prompt`, `confirm-before`, and `display-menu` (minimal
      item model) over the control protocol
- [ ] Status-line primitives: `status-left`/`status-right`/`status-interval` with `#{}`
      expansion, emitted as a control-mode `%status` notification (GUI rendering optional)
- [ ] `layout.c` — preset layout set (`even-horizontal`, `even-vertical`, `main-horizontal`,
      `main-vertical`, `tiled`) + `select-layout`/`next-layout`
- [ ] `docs/lmux.1` — man page generated from the command registry (`lmux <cmd> --help` parity)
- [ ] Tests for every new subsystem: unit (C) + integration (Python) + at least one fuzz
      target for the format engine and the command parser
- [ ] Updated `README.md` feature table and `CHANGELOG.md`

## Definition of Done (binary)

- [ ] `list-commands` enumerates **every** command in the registry, and each accepts
      `--help` returning its schema
- [ ] `list-keys`, `bind-key`, `unbind-key` round-trip: a runtime binding persists, triggers,
      and can be removed, verified in integration tests
- [ ] `show-options -g` emits the full typed option table; `set-option` of a scoped option is
      inherited correctly (window → session → global) per test
- [ ] `#{pane_id}`/`#{session_name}`/`#{window_index}` and ≥ 10 other format variables expand
      correctly in `list-*` output
- [ ] `monitor-silence` fires a control-mode notification after the configured interval
- [ ] `set-environment`/`show-environment` round-trip across server/session/global scope
- [ ] `command-prompt`, `confirm-before`, and `display-menu` are exercisable over control mode
- [ ] Status-left/right render with `#{}` expansion in the `%status` notification
- [ ] All 5 preset layouts apply and `next-layout` cycles them
- [ ] `man lmux` opens `docs/lmux.1` and it lists every command
- [ ] Full suite green: `make test` (all existing 217 + new tests), C unit tests, fuzz smoke
- [ ] No `-fsanitize=address,undefined` violations in the new C code

## Verification Steps

1. `make test` — full integration + unit + fuzz smoke passes with the new tests included.
2. `./build/lmux list-commands` — registry count matches the command count in README.
3. `./build/lmux show-options -g` — prints the full typed table without error.
4. Control-mode smoke: `attach` → `display-menu` → read `%block` → `command-prompt` round-trip.
5. `man -l docs/lmux.1` renders and lists every command.
6. `node scripts/test.mjs --unit` — C unit tests green.
7. ASan/UBSan build: run the new format/parser fuzz target over the seed corpus with no crash.

## Anti-Drift Rules

- Scope is **multiplexer-core maturity only**. Agent features (teams, cloud VMs, browser,
  calendar/email/weather) are already complete — do not extend them here.
- Each milestone is a separate, independently mergeable change (Strangler Fig) — no big-bang
  rewrite of `server.c`/`model.c`.
- Do not claim a milestone done without running its gate; use `goal-met` for the final audit.
- If a tmux feature is GUI-irrelevant (e.g. built-in clock widget), record the skip and its
  rationale instead of implementing it in the daemon.
- Preserve the existing JSON protocol shape; new fields must be additive (no breaking change
  to `workspace.*`, `surface.*`, `pane.*`, `control.*`).

## Estimated Effort

- P0 (command registry + keybindings + options): large — core refactor.
- P1 (format + silence alert + environment): medium.
- P2 (prompt/menu + status line + layouts + man page): medium.
- P3 (emacs copy mode): large, optional.

## Milestones & Gates

### M1 — Command registry + grammar (P0)
- Deliver: `cmd.c` table-driven registry, aliases, `list-commands`, `--help` schema.
- Gate: `list-commands` count == documented count; `lmux <cmd> --help` returns schema for all.

### M2 — Runtime key-binding tables (P0)
- Deliver: `keybind.c`, `bind-key`/`unbind-key`/`list-keys`, `send-keys` via tables.
- Gate: bind → trigger → unbind round-trip passes in integration test.

### M3 — Typed option system (P0)
- Deliver: `options.c` + `options-table.c`, scoped `set-option`/`show-options`, inheritance.
- Gate: `show-options -g` prints full table; inheritance test green.

### M4 — Format expansion (P1)
- Deliver: extend existing `src/core/format.c` — grow the variable table, wire into `list-*`
  and status line.
- Gate: ≥ 10 variables expand correctly; fuzz target runs crash-free.

### M5 — Silence alert + environment (P1)
- Deliver: `monitor-silence`, `set-environment`/`show-environment`/`update-environment`.
- Gate: silence fires notification; env round-trips across scopes.

### M6 — Prompt / menu / confirm (P2)
- Deliver: `command-prompt`, `confirm-before`, `display-menu` over control mode.
- Gate: control-mode smoke passes.

### M7 — Status line + layouts + man page (P2)
- Deliver: `status-left/right` with `#{}`, 5 preset layouts + `next-layout`, `docs/lmux.1`.
- Gate: `%status` renders expansion; layouts cycle; `man lmux` lists all commands.

### M8 — Emacs copy mode (P3, optional)
- Deliver: `copy-mode-emacs` key table + movement/yank/paste.
- Gate: emacs bindings round-trip in integration test.
