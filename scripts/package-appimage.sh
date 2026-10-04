#!/bin/bash
# package-appimage.sh — Build an AppImage for lmux.
#
# This is a thin shim that delegates to the canonical packager at
# packaging/build-appimage.sh. All AppImage packaging logic lives there; this
# path is kept so historical entry points (`make appimage`, docs) keep working
# and produce the identical, correct package.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${ROOT}/packaging/build-appimage.sh" "$@"
