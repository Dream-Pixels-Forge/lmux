#!/bin/bash
# package-deb.sh — build the Debian package for lmux.
#
# This is a thin shim that delegates to the canonical packager at
# packaging/build-deb.sh. All deb packaging logic lives there; this path is
# kept so historical entry points (`make deb`, docs) keep working and produce
# the identical, correct package.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${ROOT}/packaging/build-deb.sh" "$@"
