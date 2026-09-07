# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

Only the latest release receives security updates. Users on older versions
should upgrade.

## Reporting a Vulnerability

If you discover a security vulnerability in lmux, please report it
responsibly. **Do not open a public GitHub issue.**

**Option 1 — Email**

Send a description to **security@lmux.dev** (or the maintainer's direct
email listed in `package.json`).

**Option 2 — GitHub Security Advisory**

Use [GitHub's private vulnerability reporting](https://github.com/lmux/lmux/security/advisories/new)
to create an advisory directly.

Include:

- Steps to reproduce
- Affected version
- Potential impact
- Any suggested fix (if applicable)

## Response Timeline

| Action               | Target              |
| --------------------- | ------------------- |
| Acknowledgement       | Within **48 hours** |
| Critical fix          | Within **7 days**   |
| Non-critical fix      | Within **30 days**  |

We will coordinate disclosure with the reporter before any public
announcement.

## Security Measures

lmux applies the following hardening practices:

- **Socket ownership verification** — The daemon refuses connections to
  sockets not owned by the current user, preventing impersonation.
  (`gui/daemon_client.py`)
- **Rate limiting** — 1000 requests per 60 seconds per UID. When the
  rate limit table is full, new UIDs are denied (fail-closed).
  (`src/core/server.c`)
- **Path traversal prevention** — All file paths from client commands
  are validated against `..` sequences and absolute path escapes.
  Affected: snapshot save/load, file explorer create/delete/rename,
  SSH session save/restore, config load. (`src/core/model.c`)
- **JSON input validation** — All JSON received over the socket is
  validated for structural integrity before parsing. (`src/core/config.c`)
- **Structured error responses** — RFC 7807 format errors with no
  stack traces or internal paths leaked to clients.
- **CWD validation** — Shell injection prevention in workspace refresh
  validates current working directory exists and is accessible.
  (`src/core/model.c`)
- **ASan / USan in CI** — Every pull request and nightly build compiles
  and runs the test suite with AddressSanitizer and UndefinedBehaviorSanitizer
  enabled. (`.github/workflows/ci.yml`)
- **No hardcoded secrets** — Credentials and tokens are never stored in
  source. Auth fields are reserved for future use only.
- **Atomic config writes** — Config files are written to a temporary file
  then renamed, preventing corruption on crash. (`src/core/config.c`)

## Scope

This policy covers the `lmux` C core (`src/core/`), CLI (`src/cli/`),
Python GUI (`gui/`), and build/packaging scripts. Third-party dependencies
(such as VTE or GTK) are out of scope; report issues in those projects
directly.

## Disclosure Policy

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure).
Researchers who report in good faith will not be pursued legally.
