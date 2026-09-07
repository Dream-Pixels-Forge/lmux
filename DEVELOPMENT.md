# Development Guide

## Prerequisites

- **C17** compiler (GCC or Clang)
- **Node.js** 18+ (build scripts)
- **Python 3** with PyGObject + VTE (GUI)
- **meson** / ninja (optional native build)
- **fakeroot** + **dpkg-deb** (for Debian packaging)

## Build

```bash
# Build everything
node scripts/run.mjs

# Build core library only
node scripts/build-core.mjs

# Build CLI binary only
node scripts/build-cli.mjs

# Debug build
node scripts/build-core.mjs --debug
node scripts/build-cli.mjs --debug

# Watch mode (auto-rebuild on source changes)
node scripts/dev.mjs

# Clean build artifacts
node scripts/clean.mjs
```

## Test

```bash
# Run all tests
make test

# Run C unit tests only
node scripts/test.mjs --unit

# Run integration tests only
python3 tests/test_integration.py -v

# Run fuzz tests
./tests/fuzz_json

# Run benchmarks
python3 tests/benchmarks.py

# Build with sanitizers (ASan/UBSan)
node scripts/build-core.mjs --sanitize
node scripts/test.mjs --sanitize --unit
```

## Directory Structure

```
lmux/
├── src/
│   ├── core/              # C17 core library
│   │   ├── model.c        # Workspace/surface/pane model + all commands
│   │   ├── server.c       # UDS JSON server + auth + rate limiting
│   │   ├── config.c       # Configuration loader
│   │   └── osc.c          # OSC notification parser
│   ├── cli/
│   │   └── main.c         # CLI entry point + daemon mode
│   └── browser.c          # Browser integration (WebKit2GTK stub)
├── include/
│   ├── lmux.h             # Public API
│   └── lmux_browser.h     # Browser API
├── gui/                   # Python GTK3/VTE GUI
│   ├── main.py            # GTK window entry
│   ├── daemon_client.py   # UDS socket client
│   ├── terminal.py        # VTE terminal widget
│   ├── panes.py           # Tiled pane manager
│   ├── sidebar.py         # Workspace sidebar
│   └── ...                # Feature modules
├── tests/
│   ├── test_integration.py  # 217 integration tests
│   ├── test_model.c         # C unit tests
│   ├── test_browser.c       # Browser unit tests
│   ├── test_config.c        # Config unit tests
│   └── lmux.py              # Python test client
├── scripts/               # Build scripts (Node.js ESM)
├── packaging/             # Debian/AppImage packaging
├── dev-notes/             # Development documentation
└── .github/workflows/     # CI/CD
    ├── ci.yml             # Build + test + lint + package
    ├── nightly.yml        # Nightly build + release
    ├── release.yml        # Tag-triggered release
    ├── sbom.yml           # SBOM generation
    ├── sign.yml           # Container signing
    └── trivy.yml          # Dependency scanning
```

## Architecture

- **C core** (`src/core/`) — static library `liblmux_core.a` with all workspace/surface/pane logic
- **CLI** (`src/cli/`) — thin CLI wrapper that links against the core library
- **Server** (`server.c`) — Unix domain socket server, one-shot per connection, JSON wire protocol
- **Model** (`model.c`) — all command dispatch, ~7800 lines, handles 80+ commands
- **GUI** (`gui/`) — Python GTK3/VTE frontend, connects to daemon via socket

## Wire Protocol

Commands are newline-delimited JSON:
```json
{"cmd":"workspace.create","args":{"name":"my-project"}}\n
```

Responses:
```json
{"ok":true,"result":{"id":1,"title":"my-project"}}\n
```

Errors (RFC 7807):
```json
{"ok":false,"error":{"code":"not_found","message":"workspace not found"}}\n
```

## CI/CD

All workflows run on push to `master` and on PRs:
- **ci.yml** — build, unit tests, sanitizers, cppcheck, Debian packaging
- **nightly.yml** — nightly build + release
- **release.yml** — triggered by `v*` tags
- **sbom.yml** — SBOM generation on release
- **trivy.yml** — dependency vulnerability scanning

## Packaging

### Debian/Ubuntu
```bash
make install-deb
# Or: bash packaging/build-deb.sh
# Produces: dist/deb/lmux_<version>_amd64.deb
```

### AppImage
```bash
./packaging/build-appimage.sh
# Produces: build/lmux-<version>-x86_64.AppImage
```
