# P2 Features Implementation Context

## Project: lmux
- Path: `/home/dimona/Dream-Pixels-Forge/Dev/cli/lmux/`
- C17 core daemon + Python GTK3/VTE GUI
- Client-server over Unix domain sockets (JSON wire protocol)
- 85 integration tests (77 existing + 8 new canvas) + 22 C unit tests + 19 browser tests all passing

## P2 Features to Implement

### P2.1 Tmux Compatibility Layer
**Goal:** Allow users to use tmux commands that map to lmux equivalents.

**Implementation approach:**
1. Add tmux command parser in `src/cli/main.c`
2. Map common tmux commands to lmux JSON commands:
   - `tmux new-session` → `{"cmd":"workspace.create","args":{...}}`
   - `tmux kill-session` → `{"cmd":"workspace.close","args":{"id":X}}`
   - `tmux split-window -h` → `{"cmd":"surface.split","args":{"id":X,"direction":"horizontal"}}`
   - `tmux split-window -v` → `{"cmd":"surface.split","args":{"id":X,"direction":"vertical"}}`
   - `tmux select-pane -t` → `{"cmd":"pane.focus","args":{"id":X}}`
   - `tmux select-window -t` → `{"cmd":"workspace.select","args":{"id":X}}`
   - `tmux list-sessions` → `{"cmd":"workspace.list","args":{}}`
   - `tmux list-panes` → `{"cmd":"surface.list","args":{"workspace_id":X}}`
   - `tmux send-keys` → `{"cmd":"pane.send_text","args":{"id":X,"text":"..."}}`
   - `tmux rename-session` → `{"cmd":"workspace.rename","args":{"id":X,"title":"..."}}`
   - `tmux kill-server` → `{"cmd":"app.quit","args":{}}`

3. Add `--tmux-compat` flag to daemon command that intercepts tmux-style CLI args
4. Add `tmux` subcommand that translates args to JSON and sends to daemon

**Files to modify:**
- `src/cli/main.c` — Add tmux command translation
- `include/lmux.h` — Add tmux compat declarations if needed

**Tests to add:**
- `tests/test_integration.py` — Test tmux command translation

### P2.2 Copy Mode (Vi-style)
**Goal:** Vi-style text selection and clipboard integration.

**Implementation approach:**
1. Add copy mode state to `lmux_pane` struct in `model.c`:
   - `bool copy_mode_active`
   - `int cursor_row, cursor_col`
   - `int sel_start_row, sel_start_col`
   - `char selection[65536]`

2. Add copy mode commands to dispatch:
   - `pane.copy_mode.enter` — Enter copy mode
   - `pane.copy_mode.exit` — Exit copy mode
   - `pane.copy_mode.move` — Move cursor (h/j/k/l, w/b, 0/$, gg/G)
   - `pane.copy_mode.select` — Start selection (v)
   - `pane.copy_mode.yank` — Copy selection to clipboard (y)
   - `pane.copy_mode.paste` — Paste from clipboard (p)
   - `pane.copy_mode.search` — Search in scrollback (/)

3. Add clipboard integration (xclip/xsel on Linux)

**Files to modify:**
- `src/core/model.c` — Copy mode state and logic
- `src/core/server.c` — Dispatch new commands
- `include/lmux.h` — Declarations

**Tests to add:**
- `tests/test_integration.py` — Test copy mode commands

### P2.3 File Explorer
**Goal:** Integrated file browsing panel.

**Implementation approach:**
1. Add file explorer struct in `model.c`:
   - `lmux_file_explorer` with path, entries, filter, sort

2. Add file explorer commands to dispatch:
   - `file_explorer.open` — Open file explorer at path
   - `file_explorer.list` — List directory contents
   - `file_explorer.navigate` — Navigate to path
   - `file_explorer.refresh` — Refresh current view
   - `file_explorer.filter` — Filter by extension/name
   - `file_explorer.sort` — Sort by name/size/date
   - `file_explorer.open_file` — Open file in editor
   - `file_explorer.create_dir` — Create directory
   - `file_explorer.delete` — Delete file/directory
   - `file_explorer.rename` — Rename file/directory
   - `file_explorer.search` — Search for files by name

3. Add `surface.show_file_explorer` / `surface.hide_file_explorer` to toggle panel

**Files to modify:**
- `src/core/model.c` — File explorer implementation
- `src/core/server.c` — Dispatch new commands
- `include/lmux.h` — Declarations

**Tests to add:**
- `tests/test_integration.py` — Test file explorer commands

### P2.4 Canvas Layout ✅ DONE
**Goal:** Alternative freeform layout mode (vs grid-based).

**Implementation approach:**
1. Add canvas layout state to `lmux_surface`:
   - `bool canvas_mode`
   - Per-pane position: `int x, y, width, height` (absolute or relative)
   - Z-order for overlapping panes

2. Add canvas commands to dispatch:
   - `surface.canvas.enable` — Switch surface to canvas mode
   - `surface.canvas.disable` — Switch back to grid mode
   - `surface.canvas.move_pane` — Move pane to position
   - `surface.canvas.resize_pane` — Resize pane
   - `surface.canvas.set_z` — Set pane z-order
   - `surface.canvas.get_layout` — Get current layout
   - `surface.canvas.set_layout` — Set layout from JSON

3. Add layout serialization/deserialization for save/restore

**Files modified:**
- `src/core/model.c` — Canvas layout implementation (struct fields ~line 285, dispatch commands ~lines 5017-5430, canvas_layout_save/load ~lines 5470-5560, readonly list updated)
- `tests/test_integration.py` — 8 tests in `TestCanvasLayout` class (~line 1618)

**Tests added:** 8/8 passing
- test_canvas_enable_disable
- test_canvas_errors
- test_canvas_get_layout
- test_canvas_invalid_pane_index
- test_canvas_move_pane
- test_canvas_resize_pane
- test_canvas_set_layout
- test_canvas_set_z

**Key decisions:**
- Used `int[64]` fixed arrays in `lmux_surface` (not vec_t) per header constraints
- `json_extract_string` + `atol()`/`atoi()` for parsing (no `json_extract_int` exists)
- JSON params for canvas commands must be strings (`"0"` not `0`)
- `get_layout` is in the readonly commands list

## Implementation Order
1. P2.1 Tmux Compatibility (simplest, most immediately useful)
2. P2.2 Copy Mode (terminal feature)
3. P2.3 File Explorer (file management)
4. P2.4 Canvas Layout (layout system) ✅ DONE

## Completed Features
- **P2.4 Canvas Layout** — 7 commands implemented, 8 integration tests passing, no regressions

## Key Constraints
- All existing 77 integration tests must continue to pass
- All 22 C unit tests must continue to pass
- All 19 browser tests must continue to pass
- Follow existing code patterns in model.c and server.c
- Use existing `lmux_id` type for IDs
- Use `%u` format specifier for `lmux_id` (unsigned int)
- Do NOT use `vec_t` in header — use fixed arrays for struct fields in `lmux.h`
