# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Command replies now JSON-escape every user-supplied string. 112 values were
  inserted raw: titles, names, paths, hosts, hook commands, config values and
  error-message fragments. One `"` in any of them made the reply invalid
  JSON, or let it carry extra fields.
- Replies grow to fit instead of being written with `written += snprintf(...)`.
  An over-long value used to push `written` past the buffer, so the next
  `result_cap - written` wrapped around and the following write overflowed
  the heap. Fifteen list commands had the same pattern in fixed stack
  buffers.
- `events`: `after_seq` was copied into the reply as a bare value, so a
  non-numeric one could add fields that the server then read back; it now
  accepts digits only. The server parses the stream filters with the JSON
  helpers instead of `strstr`.
- Fixed a stack buffer overflow in the daemon's JSON string parser: a
  `\uXXXX` escape landing at the end of a field's buffer wrote past it, so a
  single request could crash `lmuxd` (and every terminal it owns). A string
  ending in a lone backslash also read past the end of the request.
- Event-stream payloads (`notification.created`, `workspace.created`,
  `surface.renamed`, `agent.*`, `browser.*`, buffer/hook events, ...) now
  JSON-escape user text; a quote in a title or notification could break or
  forge events for every subscriber. Same for `pane.list` `formatted`.
- Request fields are only matched as real JSON keys, never as text inside a
  value, and `cmd`/`args` only at the top level of the request.
- Socket auth compares the peer to the daemon's own UID instead of
  `stat()`ing the socket path; concurrent connections are capped (256).

### Fixed
- Replies that escaped two values with `JESC()` (`template.create`,
  `cloud.vms.create`, `team.add`, `file_explorer.open_file`/`create_dir`) printed
  one of the values twice, because both calls shared a single buffer.
  `template.create` reported the workspace title as the template name.
- `workspace.env` listing cut keys using the raw string's length applied to
  the escaped string, which gave wrong keys and could split an escape.
- `naming.suggest` could cache a list truncated mid-element and later replay
  it as invalid JSON.
- `lmux_app_free` now frees windows, SSH sessions and feed panels/entries
  that are still open at shutdown.
- Emoji and other non-BMP characters (`\ud83d\ude00` pairs) decode
  correctly instead of becoming U+FFFD.
- `buffer-*`, `hook-fired`, `window.created`, `ssh.session_created`,
  `feed.panel_created` and `weather.refreshed` events carried
  `"data":{{...}}`, which is not valid JSON.
- HTTP log server: an early read failure could release an SSE slot it never
  took (uninitialized flag); writes now retry short writes; the heartbeat
  timer is per connection instead of shared across threads.
- `#{...}` format expansion no longer truncates long URLs to 255 bytes and no
  longer has a shared static fallback buffer.
- Build is warning-free under `-Wall -Wextra -Wpedantic`.

### Added

- `src/core/json.c` / `include/lmux_json.h`: the shared JSON field helpers
  used by the dispatcher, server and config loader.
- `tests/test_json_fuzz.c`: regressions plus fuzzing of the JSON layer and
  dispatcher, asserting every response and event stays valid JSON.

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
