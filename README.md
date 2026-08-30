# lmux — Terminal Multiplexer

**lmux** is a modern terminal multiplexer with a client-server architecture over Unix domain sockets. It combines a lightweight C core daemon with a GTK3/VTE graphical frontend, supporting SSH workspaces, AI agent integration, and OSC terminal notifications.

## Architecture

```
┌─────────────────────────────────────────┐
│  lmux (CLI)        lmux-gui (Python)    │
│  C binary           GTK3/VTE GUI        │
└────────┬────────────────────┬───────────┘
         │ Unix domain socket │ (JSON)
         ▼                    ▼
┌─────────────────────────────────────────┐
│  lmuxd (Core Daemon)                    │
│  liblmux_core.a — C17 static library    │
│    ├── model.c  — workspace/surface/    │
│    │              pane lifecycle         │
│    ├── server.c — UDS JSON server       │
│    ├── config.c — configuration loader  │
│    └── osc.c    — OSC notification      │
│                  parser                 │
└─────────────────────────────────────────┘
```

### Key design decisions

- **No external JSON library** — all JSON is assembled with `snprintf` to keep dependencies minimal
- **Client-server over UDS** — single daemon process manages all sessions, clients connect via Unix domain sockets
- **JSON wire protocol** — commands and events are JSON messages over a newline-delimited stream
- **Static library core** — `liblmux_core.a` can be embedded in other tools

## Quick Start

### Build from source

```bash
# Install dependencies (Debian/Ubuntu)
sudo apt install build-essential python3 python3-gi gir1.2-vte-2.91

# Build the core library
node scripts/build-core.mjs

# Build the CLI binary
node scripts/build-cli.mjs

# Build both with one command
node scripts/run.mjs
```

### CLI usage

```bash
# Show version
./build/lmux version

# List workspaces
./build/lmux workspace list

# Create a workspace
./build/lmux workspace create

# Show available commands
./build/lmux help

# Daemon mode (background)
./build/lmux daemon --detach

# Daemon mode (foreground)
./build/lmux daemon
```

### GUI usage

```bash
# Run the GUI (requires GTK3 + VTE)
./build/lmux-gui
```

Or via Python directly:
```bash
python3 gui/main.py
```

## GPU Rendering (GTK4)

lmux has an experimental GTK4 frontend with GPU-accelerated rendering. GTK4 renders through GSK (Gtk Scene Graph) which uses OpenGL/Vulkan by default, giving hardware-accelerated terminal rendering.

### Install GTK4 dependencies

```bash
# Ubuntu/Debian
sudo apt install gir1.2-gtk-4.0 gir1.2-vte-2.91

# Fedora
sudo dnf install gtk4 vte291-gtk4

# Arch
sudo pacman -S gtk4 vte4
```

### Run with GPU rendering

```bash
# Auto-detect GPU backend
./scripts/launch-gtk4.sh

# Force OpenGL
./scripts/launch-gtk4.sh --opengl

# Force Vulkan
./scripts/launch-gtk4.sh --vulkan

# Software fallback
./scripts/launch-gtk4.sh --software

# Or directly
GSK_RENDERER=opengl python3 gui/gtk4/main.py
```

### GTK4 vs GTK3

| Feature | GTK3 | GTK4 |
|---------|------|------|
| Rendering | CPU (Cairo/Pango) | GPU (GSK OpenGL/Vulkan) |
| Terminal | VTE 2.91 (CPU) | VTE 4.1 (GPU via GSK) |
| Compositing | Window manager | Built-in compositor |
| Input | X11/Wayland separate | Wayland-native |
| Animations | Manual | Automatic (GSK) |

## Workspace Model

```
lmuxd
 ├── Workspace 1
 │    ├── Surface 1 (tab)
 │    │    ├── Pane 1  ← terminal
 │    │    └── Pane 2  ← terminal (split)
 │    └── Surface 2
 │         └── Pane 1
 ├── Workspace 2 (SSH)
 │    └── Surface 1
 │         └── Pane 1
 └── ...
```

- **Workspace** — a named collection of surfaces
- **Surface** — a tab within a workspace, containing one or more panes
- **Pane** — a terminal session (local shell or SSH)

## Configuration

lmux reads configuration from `~/.config/lmux/config.json`. The config supports:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `font_family` | string | `monospace` | Terminal font family |
| `font_size` | int | `12` | Terminal font size |
| `theme` | string | `"dark"` | Color theme (`"dark"` or `"light"`) |
| `default_shell` | string | system SHELL | Default shell for new panes |
| `agent_claude_code` | string | `""` | Path to Claude Code binary |
| `agent_opencode` | string | `""` | Path to OpenCode binary |
| `agent_codex` | string | `""` | Path to Codex CLI binary |
| `agent_aider` | string | `""` | Path to Aider binary |
| `agent_goose` | string | `""` | Path to Goose binary |
| `auto_save_session` | bool | `true` | Save/restore sessions automatically |

### Workspace groups

```json
{
  "groups": {
    "dev": ["ws-1", "ws-2"],
    "ops": ["ws-3"]
  },
  "keybindings": {
    "split-v": "C-b %",
    "split-h": "C-b \""
  }
}
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `version` | Print version and exit |
| `help` | Show available commands |
| `workspace list` | List all workspaces |
| `workspace create [--name <name>]` | Create new workspace |
| `workspace close <id>` | Close a workspace |
| `surface create <workspace_id>` | Create new surface in workspace |
| `surface list <workspace_id>` | List surfaces in workspace |
| `surface close <workspace_id> <surface_id>` | Close a surface |
| `pane create <workspace_id> <surface_id>` | Create new pane |
| `pane list <workspace_id> <surface_id>` | List panes in workspace |
| `pane close <workspace_id> <surface_id> <pane_id>` | Close a pane |
| `notification list` | List pending notifications |
| `notification clear <id>` | Clear a notification |
| `daemon` | Run in daemon mode |

## GUI Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+T` | New surface (tab) |
| `Ctrl+Shift+W` | Close surface |
| `Ctrl+Shift+-` | Split pane vertical |
| `Ctrl+Shift+=` | Split pane horizontal |
| `Ctrl+Shift+N` | New workspace |
| `Ctrl+Shift+Q` | Quit |
| `Ctrl+Shift+Up/Down` | Focus prev/next pane |
| `Ctrl+Shift+Left/Right` | Focus prev/next pane |
| `Ctrl+Shift+Alt+Up/Down` | Focus prev/next pane |

## Development

### Prerequisites

- **C17** compiler (GCC, Clang)
- **Node.js** 18+ (for build scripts)
- **Python 3** with PyGObject + VTE (for GUI)
- **meson** / ninja (optional, for native build)

### Build scripts

```bash
# Debug build
node scripts/build-core.mjs --debug
node scripts/build-cli.mjs --debug

# Watch mode (auto-rebuild on source changes)
node scripts/dev.mjs

# Clean build artifacts
node scripts/clean.mjs

# Run tests
node scripts/test.mjs
```

### Directory structure

```
lmux/
├── src/
│   ├── core/          # C17 core library
│   │   ├── model.c    # Workspace/surface/pane model
│   │   ├── server.c   # UDS JSON server
│   │   ├── config.c   # Configuration loader
│   │   └── osc.c      # OSC notification parser
│   └── cli/
│        └── main.c    # CLI entry point + daemon mode
├── include/
│   └── lmux.h         # Public API header
├── gui/
│   ├── main.py        # GTK window entry
│   ├── daemon_client.py # UDS socket client
│   ├── terminal.py    # VTE terminal widget
│   ├── panes.py       # Tiled pane manager
│   └── sidebar.py     # Workspace sidebar
├── scripts/
│   ├── build-core.mjs # Core C build
│   ├── build-cli.mjs  # CLI C build
│   ├── dev.mjs        # Watch-mode rebuild
│   ├── run.mjs        # Build + run
│   ├── test.mjs       # Test runner
│   └── clean.mjs      # Clean artifacts
├── tests/             # Test directory
├── ARCHITECTURE.md    # Architecture documentation
└── package.json       # Project metadata
```

## Packaging

### Debian/Ubuntu

```bash
# Build + install in one step (auto-resolves deps, no _apt sandbox warning):
make install-deb

# Or build only (produces dist/deb/lmux_<version>_amd64.deb):
# ./packaging/build-deb.sh
```

### AppImage

```bash
# Build AppImage
./scripts/package-appimage.sh
# Produces: build/lmux-<version>-x86_64.AppImage
```

## License

MIT
