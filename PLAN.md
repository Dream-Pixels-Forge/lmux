# lmux Implementation Plan — Close the cmux Gap

> **Date:** 2026-06-20
> **Baseline:** lmux v1.0.0 — 81 tests passing, all performance optimizations applied
> **Target:** Feature parity with cmux where Linux-appropriate
> **Constraint:** C17 core + Python GTK3 GUI, no new dependencies beyond what's installed

---

## Gap Analysis Summary

| cmux Feature | lmux Status | Linux Equivalent | Effort | Priority |
|---|---|---|---|---|
| Command palette (fuzzy search) | ❌ | fzf subprocess or pure C fuzzy match | 1 day | P0 |
| In-app browser (WebKit) | ❌ | WebKit2GTK 4.1 (installed) | 2 days | P0 |
| Sidebar extensions (plugin system) | ❌ | GLib GModule (.so plugins) | 2 days | P1 |
| Web dashboard | ❌ | Python Flask/FastAPI + static SPA | 3 days | P1 |
| Auto-updater | ❌ | apt repository + version check | 0.5 day | P1 |
| Canvas 2D layout | ❌ | Custom GTK3 DrawingArea | 5 days | P2 |
| Auth system | ❌ | PAM or socket ownership (already have) | 1 day | P2 |
| iOS companion | N/A | Linux not mobile | — | Skip |
| Rust experiment | N/A | Not needed | — | Skip |
| GPU terminal rendering | ❌ | VTE already fast enough for terminal use | — | Skip |

**Total estimated effort:** ~15 days for P0+P1, ~21 days for P0+P1+P2

---

## Phase 1: Command Palette (P0 — 1 day)

### What
Fuzzy command search like cmux's command palette. Type `>` to open, fuzzy match against all 40+ commands.

### Implementation
- **File:** `gui/command_palette.py` (new, ~200 lines)
- **Backend:** Use `fzf` subprocess for fuzzy matching, or implement pure C fuzzy in `model.c`
- **Trigger:** `Ctrl+Shift+P` or `>` in sidebar
- **Flow:**
  1. User presses `Ctrl+Shift+P`
  2. Palette overlay appears with text input
  3. User types query → fzf filters command list in real-time
  4. Select command → executes via `LmuxClient.send()`
- **Commands to expose:** workspace.create, workspace.close, workspace.switch, surface.create, surface.close, pane.split, pane.close, pane.focus, agent.spawn, snapshot.save, snapshot.restore, config.reload, tree, etc.

### Files to Create/Modify
- Create: `gui/command_palette.py`
- Modify: `gui/main.py` — add palette toggle keybinding
- Modify: `gui/daemon_client.py` — expose command list getter

### Verification
- Launch GUI, press Ctrl+Shift+P, type "work" → see workspace commands filtered
- Select command → verify it executes
- Press Escape → palette closes

---

## Phase 2: In-App Browser (P0 — 2 days)

### What
WebKitGTK browser panel like cmux's WebKit browser. Open URLs in a side panel alongside terminals.

### Implementation
- **File:** `gui/browser.py` (new, ~300 lines)
- **Backend:** `WebKit2 4.1` Python bindings (verified working)
- **Layout:** Right panel (toggleable) with address bar + WebKit view
- **Features:**
  - Navigate to URL
  - Back/Forward/Reload
  - Open in external browser
  - Dev tools toggle
  - Session persistence (save/restore open URLs)

### Files to Create/Modify
- Create: `gui/browser.py`
- Modify: `gui/main.py` — add browser panel toggle, integrate with paned layout
- Modify: `gui/panes.py` — support 3-pane layout (sidebar | terminal | browser)

### Verification
- Launch GUI, press `Ctrl+Shift+B` → browser panel appears
- Navigate to `https://example.com` → page loads
- Toggle off → panel hides, terminal expands

---

## Phase 3: Sidebar Extensions (P1 — 2 days)

### What
Plugin system for custom sidebar panels, like cmux's Swift sidebar extensions.

### Implementation
- **File:** `gui/extensions.py` (new, ~250 lines)
- **Backend:** GLib `GModule` for loading `.so` plugins, or Python `importlib` for `.py` plugins
- **Plugin API:**
  ```python
  # Each plugin is a Python file in ~/.config/lmux/extensions/
  class Extension:
      name: str           # Display name
      icon: str           # GTK icon name
      def activate(self, sidebar): ...  # Called when panel opens
      def deactivate(self): ...         # Called when panel closes
      def get_widget(self) -> Gtk.Widget: ...  # Returns GTK widget
  ```
- **Built-in extensions:** Git status, process list, file browser
- **Extension discovery:** Scan `~/.config/lmux/extensions/*.py` at startup

### Files to Create/Modify
- Create: `gui/extensions.py`
- Create: `gui/extensions/` directory with built-in examples
- Modify: `gui/sidebar.py` — add extension tab switching
- Modify: `gui/main.py` — load extensions on startup

### Verification
- Create a test extension in `~/.config/lmux/extensions/test.py`
- Launch GUI → extension appears in sidebar
- Click extension → its widget renders

---

## Phase 4: Auto-Updater (P1 — 0.5 day)

### What
Check for updates and notify user, like cmux's Sparkle updater.

### Implementation
- **File:** `gui/updater.py` (new, ~100 lines)
- **Backend:** Check GitHub releases API for latest version
- **Flow:**
  1. On startup, check `https://api.github.com/repos/<owner>/lmux/releases/latest`
  2. Compare version with current (`1.0.0` from `lmux.h`)
  3. If newer: show notification bar with "Update available" + link
  4. User clicks → opens browser to releases page
- **Alternative:** If published to apt, check `apt list --upgradable lmux`

### Files to Create/Modify
- Create: `gui/updater.py`
- Modify: `gui/main.py` — run version check on startup

### Verification
- Launch GUI → see "checking for updates" in status bar
- If update available → notification appears

---

## Phase 5: Web Dashboard (P1 — 3 days)

### What
Web-based management UI like cmux's Next.js dashboard. View workspaces, agents, events from browser.

### Implementation
- **File:** `web/` directory (new)
- **Backend:** Python `http.server` + JSON API wrapping `LmuxClient`
- **Frontend:** Single HTML file with vanilla JS (no build step needed)
- **Endpoints:**
  - `GET /api/tree` — workspace/surface/pane tree
  - `GET /api/events` — event stream (SSE)
  - `POST /api/command` — execute command
  - `GET /api/agents` — list agents
- **UI:** Dark theme, responsive, shows tree + event log + agent status

### Files to Create/Modify
- Create: `web/server.py` (~200 lines)
- Create: `web/index.html` (~400 lines)
- Create: `web/style.css` (~150 lines)
- Modify: `src/cli/main.c` — add `lmux web` command to start dashboard

### Verification
- Run `lmux web` → server starts on port 8080
- Open `http://localhost:8080` → dashboard loads
- Create workspace in GUI → dashboard updates in real-time

---

## Phase 6: Canvas 2D Layout (P2 — 5 days)

### What
Drag-and-drop 2D tiling like cmux's Canvas system.自由にペインを配置・リサイズ。

### Implementation
- **File:** `gui/canvas.py` (new, ~500 lines)
- **Backend:** GTK3 `DrawingArea` + custom layout engine
- **Features:**
  - Free-form pane positioning (not just tree splits)
  - Drag edges to resize
  - Snap to grid
  - Save/load layouts
- **This is the most complex feature — consider deferring**

### Verification
- Toggle canvas mode → panes become draggable
- Drag pane to new position → layout updates
- Save layout → restore after restart

---

## Implementation Order

```
Phase 1: Command Palette (1 day)     ← Quick win, high UX impact
Phase 2: In-App Browser (2 days)     ← Major feature gap
Phase 4: Auto-Updater (0.5 day)      ← Easy, good for users
Phase 3: Sidebar Extensions (2 days) ← Extensibility
Phase 5: Web Dashboard (3 days)      ← Remote management
Phase 6: Canvas 2D (5 days)          ← Advanced, defer if needed
```

**P0+P1 total: ~8.5 days**
**P0+P1+P2 total: ~13.5 days**

---

## Testing Strategy

Each phase must include:
1. **Unit tests** — Python tests for new modules
2. **Integration test additions** — Extend `tests/test_integration.py`
3. **Manual verification** — Launch GUI, exercise feature end-to-end
4. **No regressions** — All 81 existing tests must still pass

## Definition of Done

- [ ] Command palette: Ctrl+Shift+P opens fuzzy search, executes commands
- [ ] In-app browser: WebKit panel toggles, navigates, persists sessions
- [ ] Sidebar extensions: Plugin discovery, loading, widget rendering
- [ ] Auto-updater: Version check on startup, notification on update
- [ ] Web dashboard: API + UI showing live workspace state
- [ ] All 81+ tests passing
- [ ] No ASan/UBSan violations
