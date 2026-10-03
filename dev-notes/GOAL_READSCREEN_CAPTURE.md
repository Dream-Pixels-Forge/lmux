# Goal: Make `read-screen` a repeatable capture

## Objective
`read-screen` must behave like tmux `capture-pane`: repeatable, non-destructive,
and correct for arbitrary byte content.

## Context
Follow-up to `dev-notes/GOAL_RESTORE_PTY.md`, which recorded as a known
limitation that `read-screen` "drains the pty; it is not a screen buffer". That
limitation was accepted as out of scope there and is fixed here.

### R1 — `read-screen` is a destructive drain

The command read whatever bytes had arrived since the previous call. A second
`read-screen` seconds later returned `""`. Any consumer that reads a pane twice
— a poll loop, a diffing UI, a test — sees content vanish.

### R2 — content after a NUL byte was lost forever

The escaping loop was:

```c
for (size_t ii = 0; screen[ii] && elen < sizeof escaped - 6; ii++)
```

The `screen[ii]` condition terminates the loop at the first **NUL**. Terminal
output is full of NUL bytes, so captures were silently truncated at the first
one — and because R1 had already drained those bytes out of the pty, the
discarded remainder could never be recovered. R1 and R2 compound: each makes the
other's data loss permanent.

Verified empirically: `printf 'AAA\000\000B\102\102_MARKER_END\n'` yielded only
`AAA`.

### R3 — capture size was incidental

Output was capped at a fixed 2048-byte stack buffer and then 4096 bytes of
escaped text. Any replacement must keep the capture bounded, not merely bound the
buffer it happens to use today.

## Deliverables
- [ ] **F1** — panes keep a bounded scrollback; `read-screen` renders its tail
- [ ] **F2** — escaping iterates by length, so embedded NULs cannot truncate
- [ ] **F3** — capture is bounded by an explicit constant
- [ ] **F4** — scrollback freed on every pane teardown path
- [ ] **T1** — RED: a second `read-screen` returns content (R1)
- [ ] **T2** — RED: output after a NUL survives (R2)
- [ ] **T3** — guard: capture stays bounded under a large burst (R3)

## Definition of Done
- [ ] `read-screen` twice in a row both return content
- [ ] A pane that emits `AAA\0\0BBB_MARKER_END` yields the full marker
- [ ] Capture length stays bounded under a 4000-line burst
- [ ] `make test` exits 0 with 256 tests, none deleted, skipped, or weakened
- [ ] `make test-fuzz` and `make test-unit` exit 0
- [ ] ASAN+UBSAN with `detect_leaks=1` reports zero findings across app free,
      workspace close, and surface close
- [ ] Diff touches only `src/core/model.c`, `tests/test_integration.py`, and docs

## Anti-Drift Rules
- The buffer is bounded by `LMUX_SCROLLBACK_MAX`; never make it grow unbounded.
- Free the buffer on **every** pane teardown path — there are three, not one.
- Escaping must iterate by length. Do not reintroduce NUL termination.
- No test may be deleted, skipped, or weakened to reach green.
- Do not change the snapshot format or add a `scrollback` field to it.

## Verification Results

- **T1 and T2 fail with the fix reverted, pass with it** (mutation-checked).
  T3 passes either way by design: it asserts a bound, not a behaviour change,
  and exists to stop a future unbounded buffer.
- `make test` → **256 tests, OK** (249 baseline + 4 + 3).
- `make test-fuzz` → passed. `make test-unit` → passed.
- ASAN+UBSAN, `detect_leaks=1`: **0 findings** across the pane-related tests and
  across clean shutdown after `workspace.close` / `surface.close`.
- Skip count unchanged vs master (1 → 1). No test removed.

## Notes on testing this

Two test-authoring traps cost real time here and are worth recording:

1. **A marker typed into the shell can satisfy its own assertion.** The first
   version of T2 searched for `BBB_MARKER_END`, which appeared in the *echo of
   the command itself*, so the test passed against unfixed code. The marker is
   now assembled from printf octal escapes so the typed text differs from the
   output.
2. **A fresh zsh runs a first-run wizard** that consumes the first keystrokes
   sent to it, so commands typed immediately after pane creation are swallowed
   (`zsh: B not found`). T2 now dismisses the wizard first.

Also note the environment constraint that shaped this work: `read-screen` is
called under the app **write** lock, not the read lock, so concurrent calls are
serialised and no additional locking was needed around the buffer.