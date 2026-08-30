#!/bin/bash
# build-appimage.sh — Build a portable AppImage for lmux.
#
# Creates a self-contained AppImage that bundles:
#   - Python GUI files (no system package install required)
#   - C core binary (if build succeeds; GUI-only fallback if not)
#   - System python3 is required at runtime (not bundled)
#
# Usage: ./packaging/build-appimage.sh
set -euo pipefail

# ── Resolve project root ──────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VERSION="$(node -p "require('${ROOT}/package.json').version" 2>/dev/null || echo "0.0.0")"
APP="lmux"
ARCH="x86_64"
DIST="$ROOT/dist"
APPDIR="$DIST/appimage/$APP.AppDir"
OUTPUT="$DIST/$APP-$VERSION-$ARCH.AppImage"
APPIMAGETOOL_CACHE="$HOME/.local/bin"
APPIMAGETOOL="$APPIMAGETOOL_CACHE/appimagetool"
APPIMAGETOOL_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/appimagetool-x86_64.AppImage"

info()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33mWARN:\033[0m %s\n" "$*"; }
err()   { printf "\033[1;31mERROR:\033[0m %s\n" "$*" >&2; }

# ── Check prerequisites ───────────────────────────────────────
check_prereqs() {
    local missing=()
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    command -v gcc     >/dev/null 2>&1 || missing+=("gcc (for C core)")
    command -v node    >/dev/null 2>&1 || missing+=("node (for build scripts)")

    if [ ${#missing[@]} -gt 0 ]; then
        err "Missing required tools: ${missing[*]}"
        exit 1
    fi
}

# ── Download appimagetool if needed ───────────────────────────
ensure_appimagetool() {
    if [ -x "$APPIMAGETOOL" ]; then
        info "Using cached appimagetool: $APPIMAGETOOL"
        return 0
    fi

    info "Downloading appimagetool..."
    mkdir -p "$APPIMAGETOOL_CACHE"
    curl -fSL -o "$APPIMAGETOOL" "$APPIMAGETOOL_URL"
    chmod +x "$APPIMAGETOOL"
    info "Installed appimagetool to $APPIMAGETOOL"
}

# ── Generate icon as SVG (no external tool needed) ────────────
generate_icon_svg() {
    local target="$1"
    cat > "$target" << 'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f2027"/>
      <stop offset="50%" stop-color="#203a43"/>
      <stop offset="100%" stop-color="#2c5364"/>
    </linearGradient>
    <linearGradient id="accent" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#00d4aa"/>
      <stop offset="100%" stop-color="#00b4d8"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="36" fill="url(#bg)"/>
  <rect x="8" y="8" width="240" height="240" rx="28" fill="none"
        stroke="url(#accent)" stroke-width="2" opacity="0.3"/>
  <!-- Terminal prompt: > _ -->
  <text x="64" y="156" font-family="monospace" font-size="96" font-weight="bold"
        fill="url(#accent)" opacity="0.9">&gt;</text>
  <text x="118" y="156" font-family="monospace" font-size="96" font-weight="bold"
        fill="#ffffff" opacity="0.95">_</text>
  <!-- Title bar dots -->
  <circle cx="40" cy="44" r="8" fill="#ff5f56"/>
  <circle cx="68" cy="44" r="8" fill="#ffbd2e"/>
  <circle cx="96" cy="44" r="8" fill="#27c93f"/>
</svg>
SVG
}

# ── Build the AppDir ──────────────────────────────────────────
build_appdir() {
    info "Building C core (release)..."
    node "$ROOT/scripts/build-core.mjs" || warn "Core build failed — AppImage will be GUI-only"
    node "$ROOT/scripts/build-cli.mjs"  || warn "CLI build failed — skipping binary install"

    info "Cleaning previous AppDir..."
    rm -rf "$APPDIR" "$OUTPUT"

    info "Creating AppDir structure..."
    mkdir -p "$APPDIR/usr/bin"
    mkdir -p "$APPDIR/usr/lib/$APP/gui"
    mkdir -p "$APPDIR/usr/share/applications"
    mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
    mkdir -p "$APPDIR/usr/share/metainfo"

    # ── Install CLI binary (if it exists) ─────────────────────
    if [ -f "$ROOT/build/lmux" ]; then
        cp "$ROOT/build/lmux" "$APPDIR/usr/bin/lmux-core"
        chmod 755 "$APPDIR/usr/bin/lmux-core"
        info "Installed C core binary"
    fi

    # ── Install Python GUI files ──────────────────────────────
    info "Installing Python GUI..."
    # Copy the gui directory, excluding __pycache__ and .pyc files
    rsync -a --exclude='__pycache__' --exclude='*.pyc' \
        "$ROOT/gui/" "$APPDIR/usr/lib/$APP/gui/"
    chmod -R u=rwX,go=rX "$APPDIR/usr/lib/$APP/gui/"

    # ── Wrapper script ────────────────────────────────────────
    cat > "$APPDIR/usr/bin/$APP" << 'WRAPPER'
#!/bin/bash
# lmux AppImage wrapper — sets up Python path and launches the GUI.
# System python3 is required; not bundled in the AppImage.
SELF_DIR="$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$SELF_DIR/../lib/lmux:$PYTHONPATH"
exec python3 "$SELF_DIR/../lib/lmux/gui/main.py" "$@"
WRAPPER
    chmod 755 "$APPDIR/usr/bin/$APP"

    # ── AppRun entry point ────────────────────────────────────
    cat > "$APPDIR/AppRun" << 'APPRUN'
#!/bin/bash
# AppRun — AppImage entry point. Resolves APPDIR and launches lmux.
SELF="$(readlink -f "$0")"
APPDIR="$(dirname "$SELF")"
export APPDIR
exec "$APPDIR/usr/bin/lmux" "$@"
APPRUN
    chmod 755 "$APPDIR/AppRun"

    # ── Desktop entry ─────────────────────────────────────────
    cat > "$APPDIR/usr/share/applications/$APP.desktop" << DESKTOP
[Desktop Entry]
Name=lmux
GenericName=Terminal Multiplexer
Comment=Native Linux terminal for running AI coding agents in parallel
Exec=lmux %F
Icon=lmux
Terminal=false
Type=Application
Categories=Utility;TerminalEmulator;System;
Keywords=terminal;multiplexer;ai;coding;
StartupNotify=true
StartupWMClass=lmux
DESKTOP

    # ── Icon (SVG) ───────────────────────────────────────────
    generate_icon_svg "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP.svg"
    ln -sf "usr/share/icons/hicolor/256x256/apps/$APP.svg" "$APPDIR/$APP.svg"

    # ── AppStream metainfo ────────────────────────────────────
    cat > "$APPDIR/usr/share/metainfo/$APP.appdata.xml" << 'METAEOF'
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>io.github.lmux.lmux</id>
  <name>lmux</name>
  <summary>Native Linux terminal for AI coding agents</summary>
  <metadata_license>MIT</metadata_license>
  <project_license>MIT</project_license>
  <description>
    <p>A native Linux terminal multiplexer purpose-built for developers running
    multiple AI coding agents in parallel. Features workspace management, pane
    splitting, real-time monitoring, and SSH remote session support.</p>
  </description>
  <url type="homepage">https://github.com/lmux/lmux</url>
  <url type="bugtracker">https://github.com/lmux/lmux/issues</url>
  <launchable type="desktop-id">lmux.desktop</launchable>
</component>
METAEOF

    # ── Top-level symlinks for appimagetool ───────────────────
    ln -sf usr/share/applications/$APP.desktop "$APPDIR/$APP.desktop"
    ln -sf usr/bin "$APPDIR/usr" 2>/dev/null || true
}

# ── Build the AppImage ────────────────────────────────────────
build_appimage() {
    info "Building AppImage..."
    "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUTPUT"

    local size
    size=$(du -h "$OUTPUT" | cut -f1)
    info "AppImage created: $OUTPUT ($size)"
}

# ── Main ──────────────────────────────────────────────────────
main() {
    info "lmux AppImage builder v$VERSION"
    check_prereqs
    ensure_appimagetool
    build_appdir
    build_appimage

    echo ""
    info "Success! Your AppImage is at:"
    echo "  $OUTPUT"
    echo ""
    info "Run it with:"
    echo "  chmod +x $OUTPUT"
    echo "  $OUTPUT"
}

main "$@"
