# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-10-13

### Changed

- Session snapshot default moved to the data directory
  (`$XDG_DATA_HOME/lmux/snapshot.json`, else
  `~/.local/share/lmux/snapshot.json`); `snapshot.save`/`snapshot.load`
  confine paths to that directory. The legacy default
  `~/.lmux_snapshot.json` is no longer loaded — move it once to the new
  location (see "Session snapshots" in the README).

### Fixed

- Harness PTY descriptor reap on teardown (#40).
- Shutdown race, SIGTERM handling, `/api/logs` limit + tail parameters
  (#43).
- SSH host keys accept-new policy, `sshpass -e` password handling (#45).

### Security

- SSE streaming with log rotation, fail-closed updater, snapshot
  directory confinement (#42).
- Web gates: XSS escaping, path-traversal confinement, token
  authentication; dropped wildcard CORS (#44).
- Updater trust chain, `log_file` confinement, extension type-on-first-use
  verification, `shell=False` subprocess hardening (#46).
- Companion least-privilege allowlist, auth honesty, TOCTOU/shlex/SSE
  hardening, test hardening (#47).

### CI

- Integration test suite runs on every PR (#41).

### Known limitations

- Auto-update stays inactive until the release-signing key ships.
- Daemon HTTP is fixed at `:8080` (the dashboard accepts `--port`).
- RPM packaging is unsupported.
- Headless installs still pull GUI dependencies.

## [0.1.0] - 2026-06-20

### Added

- Workspace management with create, list, and close operations
- Surface (tab) management within workspaces with create, list, and close operations
- Pane (terminal) management with create, list, close, and vertical/horizontal splitting
- SSH workspace support for remote terminal sessions
- Client-server architecture over Unix domain sockets with a single daemon process
- JSON wire protocol with newline-delimited messages for commands and events
- Static library core (`liblmux_core`) embeddable in other tools
- CLI binary (`lmux`) with 40+ commands covering workspaces, surfaces, panes, and daemon control
- Daemon mode with background (`--detach`) and foreground operation
- GTK3/VTE graphical frontend (`lmux-gui`) with workspace sidebar and tiled pane manager
- GUI keyboard shortcuts for surface and pane management
- OSC terminal notification parser and notification list/clear commands
- Event streaming over the JSON protocol for real-time state updates
- Snapshot save and restore for session persistence
- Configuration system reading from `~/.config/lmux/config.json`
- Configurable font family, font size, color theme, and default shell
- Agent management paths for Claude Code, OpenCode, Codex CLI, Aider, and Goose
- Workspace groups for organizing related workspaces
- Customizable keybindings for split operations
- Debian/Ubuntu package build script
- AppImage package build script
- C17 unit tests for model, config, and OSC modules
- Node.js build scripts for core library, CLI binary, watch mode, and test runner

[unreleased]: https://github.com/Dream-Pixels-Forge/lmux/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/Dream-Pixels-Forge/lmux/releases/tag/v1.2.0
[0.1.0]: https://github.com/Dream-Pixels-Forge/lmux/releases/tag/v0.1.0
