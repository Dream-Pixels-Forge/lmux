# Contributing to lmux

Thanks for your interest in lmux! This guide covers everything you need to get started.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **GCC** | any recent | `sudo apt-get install gcc` |
| **Node.js** | >= 20 | [nodejs.org](https://nodejs.org/) or `nvm` |
| **Python** | 3 | `sudo apt-get install python3 python3-gi` |
| **GTK 3 dev libs** | — | `sudo apt-get install libgtk-3-dev` |
| **VTE dev libs** | — | `sudo apt-get install libvte-2.91-dev` |
| **pkg-config** | — | `sudo apt-get install pkg-config` |
| **pnpm** | latest | `npm install -g pnpm` |

Or install all APT dependencies in one shot:

```sh
pnpm deps:apt
```

## Setup

```sh
git clone https://github.com/lmux/lmux.git
cd lmux
pnpm install          # install Node dev deps (build scripts)
pnpm deps:apt         # install system libraries (GTK3, VTE, etc.)
```

## Build Commands

| Command | What it does |
|---------|-------------|
| `pnpm build` | Build core library, GUI, and CLI |
| `pnpm build:core` | Compile `liblmux_core.a` (C static library) |
| `pnpm build:cli` | Compile the `lmux` CLI binary (requires `build:core`) |
| `pnpm build:app` | Build the Python GTK GUI |
| `pnpm test` | Run unit tests and CLI smoke test |
| `pnpm dev` | Run in development mode |
| `pnpm clean` | Remove build artifacts |

Individual build scripts also accept flags:

```sh
node scripts/build-core.mjs --debug          # unoptimized, symbols
node scripts/build-core.mjs --sanitize       # ASan + UBSan
```

## Code Style

### C

- **Standard**: C17 (`-std=c17`).
- **Warnings**: `-Wall -Wextra -Wpedantic` — your code must compile cleanly.
- **Indentation**: 4 spaces, no tabs.
- **Naming**: `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for macros.
- **JSON**: No external JSON library — use `snprintf` for all JSON assembly.
- **Header guards**: `#ifndef LMUX_FOO` / `#define LMUX_FOO` / `#endif`.
- Follow the existing style in `src/core/` and `include/lmux.h`.

### Python

- **Indentation**: 4 spaces.
- Follow [PEP 8](https://peps.python.org/pep-0008/) conventions.
- GUI code lives in `gui/` and uses GTK3/VTE via PyGObject.

## Testing

Run the full test suite:

```sh
pnpm test
```

This compiles and runs three C unit tests (`test_model`, `test_osc`, `test_config`) plus a CLI smoke test.

### Sanitizer builds

To run tests under AddressSanitizer and UndefinedBehaviorSanitizer:

```sh
node scripts/test.mjs --sanitize
```

This rebuilds the core library with `-fsanitize=address,undefined` and links the test binaries with the same flags.

### Writing tests

- Test files live in `tests/` and follow the naming convention `test_<module>.c`.
- Each test file should have a `main()` that returns 0 on success and non-zero on failure.
- Link against `build/liblmux_core.a`.

## Project Architecture

```
┌──────────────────────────────────────────────┐
│  lmux CLI (C)         lmux-gui (Python)      │
│  src/cli/main.c        gui/*.py               │
└────────┬───────────────────┬─────────────────┘
         │ Unix domain socket │ (JSON)
         ▼                    ▼
┌──────────────────────────────────────────────┐
│  lmuxd — Core Daemon (C17 static library)     │
│  liblmux_core.a                               │
│    model.c  — workspace/surface/pane model     │
│    server.c — UDS JSON server                  │
│    config.c — configuration loader             │
│    osc.c    — OSC notification parser          │
└──────────────────────────────────────────────┘
```

- **C core** — Pure C library with zero GTK dependencies. Can run headless for testing and the CLI.
- **Python GUI** — GTK3/VTE frontend that links to the core via the JSON socket protocol.
- **CLI** — Lightweight C binary for scripting and automation.
- **Protocol** — Newline-delimited JSON over Unix domain sockets. Requests are `{"cmd": "...", "args": {...}}`, responses are `{"ok": true, "result": ...}`.

## Pull Request Process

1. **Fork** the repository and create a branch from `main`.
2. **Make your changes.** Keep commits focused and messages clear.
3. **Build and test** before pushing:
   ```sh
   pnpm build
   pnpm test
   ```
4. **Open a PR** against `main` with:
   - A clear title describing what changed and why.
   - Reference any related issues.
   - Note any new dependencies or breaking changes.
5. **Respond to review feedback.** Maintainers may request changes before merging.

### What we look for

- Clean compilation with `-Wall -Wextra -Wpedantic` (no warnings).
- All tests passing, including under `--sanitize` for new C code.
- No unnecessary dependencies — the project values minimalism.
- Changes that fit the existing architecture (C core stays GTK-free).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
