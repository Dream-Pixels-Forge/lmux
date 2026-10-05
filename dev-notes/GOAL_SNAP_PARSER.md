## Goal: Replace the hand-rolled snapshot scanner with a bounded recursive-descent parser (#11 structural fix)

### Objective
Replace the 234-line `strstr` + brace-counting snapshot loader with a small,
correct, dependency-free parser for the known schema, eliminating every fixed
stack buffer and the brace-matching fragility.

### Context
#11 was closed as *partially* fixed in #17 (oversized-file guard) and #18
(nested clamps now warn). The structural fix was explicitly left open.

**The scanner is not merely size-limited — it silently corrupts.** A `}` inside
any string value terminates the enclosing object early. Reproduced serially on
`master` (`ea220a8`) with a workspace titled `evil}`:

```
saved file, parsed by Python (ground truth):
  [(1, 'default', 2 surfaces), (2, 'evil}', 1 surface)]

after ./build/lmux --json snapshot.load <file>   -> {"ok":true,...}
lmux workspace.list:
  [(3, 'default'), (4, 'evil}'), (5, 'Shell')]
```

`'Shell'` is a **surface** title that leaked in as a **workspace**, and the load
reported success. A workspace name is ordinary user input, so this is reachable
without any adversarial effort.

**Schema the writer emits** (verified by capturing a real snapshot — three
nesting levels, all leaves scalar):

```json
{"version":1,"workspaces":[
 {"id":1,"title":"...","cwd":"...","git_branch":"","surfaces":[
   {"id":1,"title":"...","panes":[
     {"id":1,"kind":"terminal","command":"..."}]}]}]}
```

**No JSON library is linked** and `src/core/model.c` states *"Pure C, no GLib, no
GTK"*. Per decision, add **no dependency**: write a bounded recursive-descent
parser for exactly this schema. The schema is small and fully known, so a
generic parser would be more code than the schema.

### Deliverables
- [ ] **F1** — tokenizer plus `snap_parse_string`, `snap_parse_uint`, `snap_parse_object`, `snap_parse_array`, all bounded and string-aware
- [ ] **F2** — `lmux_snapshot_load()` walks the parsed tree; no `strstr` array hunting, no brace counting
- [ ] **F3** — every fixed scratch buffer (`buf[65536]`, `buf_copy[4096]`, `sfcopy[2048]`, `pncopy[1024]`) removed from the load path
- [ ] **F4** — a structurally invalid snapshot is rejected with `load_failed`, never partially applied
- [ ] **T1** — RED: a workspace title containing `}` round-trips intact and does **not** leak surfaces into the workspace list
- [ ] **T2** — RED: same for `}`, `{`, and `"` inside title / cwd / command
- [ ] **T3** — RED: a snapshot whose braces are unbalanced or truncated mid-object is rejected rather than half-applied
- [ ] **T4** — RED: a snapshot with more workspaces/surfaces/panes than any previous buffer allowed loads fully (the old 64 KB cap is gone)

### Definition of Done
- [ ] `workspace.title = "}"` survives save→load with the identical title, and the workspace count is unchanged
- [ ] Titles containing `{`, `}`, `"` and `\` all round-trip exactly

### Verification Steps
```bash
cd ~/path/to/lmux  # repo root
git checkout -b fix/snapshot-parser master
make clean && node scripts/build-core.mjs && node scripts/build-cli.mjs
ASAN_OPTIONS=detect_leaks=0 make test      # serially, never in parallel
make test-fuzz

# T1 live — the corruption that motivated this
pkill -9 lmux; rm -rf /tmp/sp; mkdir -p /tmp/sp/.config
export HOME=/tmp/sp XDG_CONFIG_HOME=/tmp/sp/.config
nohup ./build/lmux daemon >/dev/null 2>&1 & sleep 3
./build/lmux --json workspace.create 'evil}' >/dev/null
./build/lmux --json surface.create >/dev/null
./build/lmux --json snapshot.save >/dev/null
./build/lmux --json snapshot.load "$HOME/.local/share/lmux/snapshot.json"
./build/lmux --json workspace.list    # must NOT contain 'Shell' as a workspace

# T4 live — larger than the old 64 KB ceiling
python3 - <<'EOF'
import json
ws=[{"id":i,"title":f"ws{i}","cwd":"/tmp","git_branch":"","surfaces":[]} for i in range(1,900)]
open("/tmp/sp/big.json","w").write(json.dumps({"version":1,"workspaces":ws}))
EOF
ls -l /tmp/sp/big.json          # must exceed 65536
./build/lmux --json snapshot.load /tmp/sp/big.json
```

### Anti-Drift Rules
- **No new dependency.** Pure C, no jansson/cJSON/json-c. Do not add a
  `-ljson*` flag or a package dependency.
- Parse **only** the documented schema. Do not build a general-purpose JSON
  library; unknown keys are skipped, not modelled.
- Do **not** change the snapshot **format** the writer emits.
- Do **not** change `lmux_snapshot_save()`. Only the load path is in scope.
- Do not "fix" the additive-restore behaviour from #8 while here — it is a
  separate semantic question, currently covered by its own test. This goal
  changes *how* the document is read, not *what* restoring means.
- Preserve the #17 oversized guard's intent: a document larger than memory can
  still be refused, but the 64 KB ceiling must not reject a legitimately larger
  snapshot. Size the buffer from the file rather than capping it.
- No test may be deleted, `.skip()`ed, or weakened to reach green.
- If the parser needs a struct in `include/lmux.h`, stop and report it.

### Estimated Effort
A day. The schema has three object levels and four scalar fields; a correct
bounded parser is ~200 lines, replacing ~234 lines of fragile scanning.

### Pairing Strategy
Strictly serialized — parser and tests are one unit and `model.c` cannot be
edited concurrently by two agents. T1–T4 RED first, then F1–F4, then an
independent `goal-met` audit.

### Merging
Branch off `master`. Merge only when all CI checks pass and the `goal-met` audit
returns MET. If the audit is NOT-MET, fix the listed gaps and re-audit.

- [ ] No surface title appears as a workspace title after any load
- [ ] A snapshot > 64 KB loads successfully (previously refused outright)
- [ ] A truncated / unbalanced snapshot returns `load_failed` and leaves the model untouched
- [ ] No array-hunting `strstr`/`strrchr` sites remain in the load function
- [ ] `grep -c 'buf_copy\|sfcopy\|pncopy' src/core/model.c` returns 0
- [ ] `make test` exits 0, ≥ 241 + 4 new tests, none deleted or weakened
- [ ] `make test-fuzz` exits 0
- [ ] `ASAN_OPTIONS=detect_leaks=0 make test` reports zero sanitizer findings
- [ ] `git diff --stat` touches only `src/core/model.c`, an optional new `src/core/snap_parse.*`, `tests/test_integration.py`, `dev-notes/GOAL_SNAP_PARSER.md`