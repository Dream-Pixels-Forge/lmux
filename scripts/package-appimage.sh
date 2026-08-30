#!/bin/bash
# package-appimage.sh — Build an AppImage for lmux.
# Usage: ./scripts/package-appimage.sh [--version <version>]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${2:-$(node -p "require('${ROOT}/package.json').version")}"
BUILD="${ROOT}/build"
APPDIR="${BUILD}/AppDir"

echo "==> Building AppImage for lmux v${VERSION}"

# 1. Build binaries (release mode)
echo "==> Building binaries..."
cd "${ROOT}"
node scripts/build-core.mjs
node scripts/build-cli.mjs

# 2. Create AppDir structure
echo "==> Creating AppDir..."
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/share/lmux/gui"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

# 3. Install binaries
cp "${BUILD}/lmux" "${APPDIR}/usr/bin/lmux"

# GUI launcher
cat > "${APPDIR}/usr/bin/lmux-gui" << 'SCRIPT'
#!/bin/bash
exec python3 "${APPDIR}/usr/share/lmux/gui/main.py" "$@"
SCRIPT
chmod +x "${APPDIR}/usr/bin/lmux-gui"

# 4. Install GUI files
cp -r "${ROOT}/gui/"* "${APPDIR}/usr/share/lmux/gui/"
find "${APPDIR}/usr/share/lmux/gui" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# 5. Desktop entry
cat > "${APPDIR}/lmux.desktop" << DESKTOP
[Desktop Entry]
Name=lmux
Comment=Terminal Multiplexer
Exec=lmux-gui
Icon=lmux
Terminal=false
Type=Application
Categories=Utility;TerminalEmulator;System;
DESKTOP

# 6. Icon
cat > "${APPDIR}/usr/share/icons/hicolor/256x256/apps/lmux.svg" << 'ICON'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" rx="24" fill="#1a1a2e"/>
  <text x="128" y="168" text-anchor="middle" font-family="monospace" font-size="128" font-weight="bold" fill="#00d4aa">L</text>
</svg>
ICON
cp "${APPDIR}/usr/share/icons/hicolor/256x256/apps/lmux.svg" "${APPDIR}/lmux.svg"

# 7. AppRun entry point
cat > "${APPDIR}/AppRun" << 'APPRUN'
#!/bin/bash
SELF="$(readlink -f "$0")"
APPDIR="$(dirname "$SELF")"
exec "${APPDIR}/usr/bin/lmux-gui"
APPRUN
chmod +x "${APPDIR}/AppRun"

# 8. Check for appimagetool
APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "${APPIMAGETOOL}" ]; then
    echo "==> appimagetool not found. Creating AppDir only."
    echo "    Install appimagetool to build the .AppImage:"
    echo "    wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    echo "    chmod +x appimagetool-x86_64.AppImage"
    echo ""
    echo "    AppDir ready at: ${APPDIR}"
    exit 0
fi

# 9. Build AppImage
echo "==> Building AppImage..."
"${APPIMAGETOOL}" "${APPDIR}" "${BUILD}/lmux-${VERSION}-x86_64.AppImage"

echo "==> Done: ${BUILD}/lmux-${VERSION}-x86_64.AppImage"
du -h "${BUILD}/lmux-${VERSION}-x86_64.AppImage"
