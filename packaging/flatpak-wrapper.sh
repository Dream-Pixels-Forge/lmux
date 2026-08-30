#!/bin/bash
# flatpak-wrapper.sh — Build lmux as a Flatpak.
# Usage: ./packaging/flatpak-wrapper.sh [--repo] [--install]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${ROOT}/packaging/io.github.lmux.lmux.yml"
APP_ID="io.github.lmux.lmux"
BUILD_DIR="${ROOT}/build/flatpak"
BRANCH="stable"

# ── Parse flags ──────────────────────────────────────────────
USE_REPO=false
DO_INSTALL=false
for arg in "$@"; do
  case "$arg" in
    --repo)    USE_REPO=true  ;;
    --install) DO_INSTALL=true ;;
    *) echo "Unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# ── Check prerequisites ──────────────────────────────────────
if ! command -v flatpak-builder &>/dev/null; then
  echo "==> flatpak-builder not found. Installing via apt..."
  sudo apt-get update -qq && sudo apt-get install -y flatpak-builder
fi

if ! flatpak remote-list --user 2>/dev/null | grep -q flathub; then
  echo "==> Adding Flathub remote..."
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
fi

# ── Install runtime and SDK if missing ───────────────────────
for ref in org.gnome.Platform org.gnome.Sdk; do
  if ! flatpak list --user --runtime | grep -q "$ref"; then
    echo "==> Installing ${ref}//46..."
    flatpak install --user -y flathub "${ref}//46" || true
  fi
done

# ── Build ────────────────────────────────────────────────────
echo "==> Building ${APP_ID} Flatpak..."
BUILD_ARGS=(
  --user
  --install-deps-from=flathub
  --force-clean
  --repo="${BUILD_DIR}/repo"
  --branch="${BRANCH}"
)

if [ "$USE_REPO" = false ]; then
  # Output a .flatpak-ref file instead of a repo
  BUILD_ARGS+=("--repo=${BUILD_DIR}/repo")
fi

flatpak-builder "${BUILD_ARGS[@]}" "${BUILD_DIR}/build-dir" "${MANIFEST}"

# ── Export .flatpak-ref ──────────────────────────────────────
REF_FILE="${BUILD_DIR}/${APP_ID}.flatpak-ref"
if [ -f "${BUILD_DIR}/repo/${APP_ID}.flatpakref" ]; then
  cp "${BUILD_DIR}/repo/${APP_ID}.flatpakref" "${REF_FILE}"
  echo "==> Flatpak ref exported to: ${REF_FILE}"
fi

# ── Optional local install ───────────────────────────────────
if [ "$DO_INSTALL" = true ]; then
  echo "==> Installing ${APP_ID} locally..."
  flatpak install --user "${BUILD_DIR}/repo" "${APP_ID}" -y
  echo "==> Run with: flatpak run ${APP_ID}"
fi

echo "==> Done."
