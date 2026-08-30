#!/bin/bash
set -euo pipefail

# build-deb.sh — Build a complete Debian package for lmux.
# Includes: C core binary, Python GUI, GTK4 port, web dashboard,
#           CLI scripts, man pages, desktop entry, and all dependencies.
#
# Usage: ./packaging/build-deb.sh [--no-build]

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VERSION=$(grep -oP '"version":\s*"\K[^"]+' package.json)
PACKAGE="lmux"
ARCH="amd64"
BUILD_DIR="dist/deb/${PACKAGE}_${VERSION}_${ARCH}"

echo "==> Building Debian package for ${PACKAGE} v${VERSION}"

# ─── 1. Build the C core ───────────────────────────────────────────
if [[ "${1:-}" != "--no-build" ]]; then
    echo "==> Building C core..."
    node scripts/build-core.mjs
fi

# Verify binary exists
if [[ ! -f build/lmux ]]; then
    echo "ERROR: build/lmux not found. Run 'node scripts/build-core.mjs' first." >&2
    exit 1
fi

# ─── 2. Clean and create directory structure ────────────────────────
echo "==> Creating package structure..."
rm -rf "$BUILD_DIR" dist/deb/*.deb
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/build"
mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/include"
mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/gui"
mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/web"
mkdir -p "$BUILD_DIR/usr/share/${PACKAGE}/docs"
mkdir -p "$BUILD_DIR/usr/share/${PACKAGE}/examples"
mkdir -p "$BUILD_DIR/usr/share/doc/${PACKAGE}"
mkdir -p "$BUILD_DIR/usr/share/man/man1"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$BUILD_DIR/etc/${PACKAGE}"

# ─── 3. Install the C binary and headers ───────────────────────────
echo "==> Installing C core..."
cp build/lmux "$BUILD_DIR/usr/lib/${PACKAGE}/build/lmux"
chmod 755 "$BUILD_DIR/usr/lib/${PACKAGE}/build/lmux"
cp build/liblmux_core.a "$BUILD_DIR/usr/lib/${PACKAGE}/build/" 2>/dev/null || true
cp include/lmux.h "$BUILD_DIR/usr/lib/${PACKAGE}/include/"

# ─── 4. Install Python GUI files ───────────────────────────────────
echo "==> Installing Python GUI..."
for f in gui/*.py; do
    cp "$f" "$BUILD_DIR/usr/lib/${PACKAGE}/gui/"
done

# gui/cli/ subdirectory
if [[ -d gui/cli ]]; then
    mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/gui/cli"
    cp gui/cli/*.py "$BUILD_DIR/usr/lib/${PACKAGE}/gui/cli/" 2>/dev/null || true
fi

# gui/gtk4/ subdirectory
if [[ -d gui/gtk4 ]]; then
    mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/gui/gtk4"
    cp gui/gtk4/*.py "$BUILD_DIR/usr/lib/${PACKAGE}/gui/gtk4/" 2>/dev/null || true
fi

# gui/updates/ subdirectory
if [[ -d gui/updates ]]; then
    mkdir -p "$BUILD_DIR/usr/lib/${PACKAGE}/gui/updates"
    cp gui/updates/*.py "$BUILD_DIR/usr/lib/${PACKAGE}/gui/updates/" 2>/dev/null || true
fi

# Strip __pycache__ directories
find "$BUILD_DIR/usr/lib/${PACKAGE}/gui" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# ─── 5. Install web dashboard ──────────────────────────────────────
echo "==> Installing web dashboard..."
if [[ -d web ]]; then
    for f in web/*; do
        if [[ -f "$f" ]]; then
            cp "$f" "$BUILD_DIR/usr/lib/${PACKAGE}/web/"
        fi
    done
    # Strip __pycache__ from web if present
    find "$BUILD_DIR/usr/lib/${PACKAGE}/web" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
fi

# ─── 6. Install docs ───────────────────────────────────────────────
echo "==> Installing documentation..."
cp README.md "$BUILD_DIR/usr/share/doc/${PACKAGE}/README.md" 2>/dev/null || true
cp ARCHITECTURE.md "$BUILD_DIR/usr/share/doc/${PACKAGE}/ARCHITECTURE.md" 2>/dev/null || true
cp LICENSE "$BUILD_DIR/usr/share/doc/${PACKAGE}/LICENSE" 2>/dev/null || true

# Also install docs into share/lmux/docs for runtime reference
if [[ -d docs ]]; then
    for f in docs/*; do
        if [[ -f "$f" ]]; then
            cp "$f" "$BUILD_DIR/usr/share/${PACKAGE}/docs/"
        fi
    done
fi

# Compress docs
find "$BUILD_DIR/usr/share/doc/${PACKAGE}" -type f -exec gzip -9 -f {} \; 2>/dev/null || true

# ─── 7. Install examples ──────────────────────────────────────────
echo "==> Installing examples..."
if [[ -d examples ]]; then
    for f in examples/*; do
        if [[ -f "$f" ]]; then
            cp "$f" "$BUILD_DIR/usr/share/${PACKAGE}/examples/"
        fi
    done
fi

# ─── 8. Create the wrapper scripts ────────────────────────────────
echo "==> Creating wrapper scripts..."

# /usr/bin/lmux — GUI launcher
cat > "$BUILD_DIR/usr/bin/lmux" << 'WRAPPER'
#!/bin/bash
# lmux — GUI launcher
# Starts the Python-based GTK graphical interface.

exec python3 /usr/lib/lmux/gui/main.py "$@"
WRAPPER
chmod 755 "$BUILD_DIR/usr/bin/lmux"

# /usr/bin/lmux-cli — CLI-only launcher (runs the C binary directly)
cat > "$BUILD_DIR/usr/bin/lmux-cli" << 'WRAPPER'
#!/bin/bash
# lmux-cli — CLI-only launcher
# Runs the compiled C core binary for daemon, config, and socket operations.

exec /usr/lib/lmux/build/lmux "$@"
WRAPPER
chmod 755 "$BUILD_DIR/usr/bin/lmux-cli"

# ─── 9. Man page ───────────────────────────────────────────────────
echo "==> Installing man page..."
cat > "$BUILD_DIR/usr/share/man/man1/lmux.1" << 'MANEOF'
.TH LMUX 1 "2026-06-20" "lmux" "User Commands"
.SH NAME
lmux \- Linux terminal multiplexer for AI coding agents
.SH SYNOPSIS
.B lmux
.RB [ \-\-socket
.IR path ]
.RB [ \-\-json ]
.I command
.RI [ args... ]
.br
.B lmux-cli
.RI [ args... ]
.SH DESCRIPTION
.B lmux
is a native Linux terminal purpose\-built for developers running multiple AI coding agents in parallel.
It features a lightweight C core daemon, a GTK3/GTK4 graphical frontend with VTE terminals,
SSH workspace support, AI agent integration, and OSC terminal notification handling.
.PP
The GUI is launched by the
.B lmux
command.
The C core binary is accessible directly via
.BR lmux-cli .
.SH COMMANDS
.TP
.BR lmux-cli\ daemon
Start the lmux daemon.
.TP
.BR lmux-cli\ config
Show or edit configuration.
.TP
.BR lmux-cli\ status
Show daemon status.
.TP
.BR lmux-cli\ help
Show all available commands.
.SH OPTIONS
.TP
.BI \-\-socket " path"
Use a specific Unix domain socket path.
.TP
.B \-\-json
Output raw JSON.
.SH FILES
.TP
.I ~/.lmux/
User data directory (created on first run).
.TP
.I ~/.config/lmux/config.json
Per\-user configuration file.
.TP
.I /etc/lmux/
System\-wide default configuration.
.TP
.I /usr/lib/lmux/
Installed Python modules and C core.
.SH SEE ALSO
.BR tmux (1),
.BR screen (1),
.BR ghostty (1)
.SH AUTHORS
lmux contributors.
.SH LICENSE
MIT.
MANEOF
gzip -9 -f "$BUILD_DIR/usr/share/man/man1/lmux.1"

# ─── 10. Desktop entry ─────────────────────────────────────────────
echo "==> Creating desktop entry..."
cat > "$BUILD_DIR/usr/share/applications/lmux.desktop" << 'DESKTOP'
[Desktop Entry]
Name=lmux
GenericName=Terminal Multiplexer
Comment=Linux terminal for AI coding agents
Exec=lmux %F
Icon=lmux
Terminal=false
Type=Application
Categories=Utility;TerminalEmulator;System;
Keywords=terminal;multiplexer;tmux;ai;agents;
MimeType=inode/directory;
StartupNotify=true
DESKTOP

# ─── 11. Icon (SVG rendered to PNG via python3) ────────────────────
echo "==> Generating application icon..."
# Generate a proper 256x256 PNG using python3 + cairo
python3 -c "
import struct, zlib, io

# Create a 256x256 RGBA PNG directly — no external dependencies needed.
W, H = 256, 256

def make_png(w, h):
    # Generate pixel data: rounded dark rectangle with 'L' letterform
    rows = []
    for y in range(h):
        row = bytearray([0])  # filter byte: None
        for x in range(w):
            # Rounded rectangle background
            margin, radius = 8, 32
            in_rect = (margin <= x < w - margin and margin <= y < h - margin)
            # Corner rounding check
            if in_rect:
                corners = [(margin + radius, margin + radius),
                           (w - margin - radius, margin + radius),
                           (margin + radius, h - margin - radius),
                           (w - margin - radius, h - margin - radius)]
                in_corner = False
                for cx, cy in corners:
                    if ((x < margin + radius or x >= w - margin - radius) and
                        (y < margin + radius or y >= h - margin - radius)):
                        dx, dy = x - cx, y - cy
                        if dx * dx + dy * dy > radius * radius:
                            in_corner = True
                            break
                if in_corner:
                    in_rect = False

            if in_rect:
                # Draw 'L' character in teal
                # L vertical bar: x 70-100, y 50-200
                # L horizontal bar: x 70-190, y 170-200
                in_L = (70 <= x <= 105 and 50 <= y <= 205) or (70 <= x <= 195 and 170 <= y <= 205)
                if in_L:
                    row.extend([0x00, 0xd4, 0xaa, 255])  # #00d4aa teal
                else:
                    row.extend([0x1a, 0x1a, 0x2e, 255])  # #1a1a2e dark
            else:
                row.extend([0, 0, 0, 0])  # transparent
        rows.append(bytes(row))

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = b''.join(rows)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')

png_data = make_png(W, H)
with open('${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps/lmux.png', 'wb') as f:
    f.write(png_data)
print(f'Icon written: {len(png_data)} bytes')
" || {
    echo "WARNING: python3 icon generation failed, installing SVG fallback"
    cat > "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/lmux.svg" << 'SVGEOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect width="256" height="256" rx="32" fill="#1a1a2e"/>
  <text x="128" y="170" text-anchor="middle" font-family="monospace" font-size="140" font-weight="bold" fill="#00d4aa">L</text>
</svg>
SVGEOF
}

# ─── 12. Default configuration ─────────────────────────────────────
echo "==> Installing default configuration..."
cat > "$BUILD_DIR/etc/${PACKAGE}/config.json" << 'CONFIG'
{
    "daemon": {
        "socket_path": "/tmp/lmux.sock",
        "log_level": "info"
    },
    "gui": {
        "theme": "dark",
        "font": "monospace 12",
        "opacity": 0.95
    },
    "terminal": {
        "scrollback_lines": 10000,
        "audible_bell": false
    },
    "workspace": {
        "auto_save_interval": 30,
        "snapshot_dir": "~/.local/share/lmux/snapshots"
    }
}
CONFIG

# ─── 13. DEBIAN/control ────────────────────────────────────────────
echo "==> Writing control file..."
cat > "$BUILD_DIR/DEBIAN/control" << CONTROL
Package: ${PACKAGE}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: libc6 (>= 2.31),
         python3 (>= 3.10),
         python3-gi (>= 3.40),
         python3-gi-cairo,
         gir1.2-gtk-3.0,
         gir1.2-vte-2.91 (>= 0.60) | gir1.2-vte-3.91,
         gir1.2-webkit2-4.1,
         libgtk-3-0t64 | libgtk-3-0
Recommends: git,
             gh,
             python3-pip,
             libvte-2.91-0
Suggests: gir1.2-gtk-4.0
Installed-Size: $(du -sk "$BUILD_DIR/usr" | cut -f1)
Maintainer: lmux developers <dev@lmux.dev>
Homepage: https://github.com/lmux/lmux
Description: Terminal multiplexer with GUI for AI coding agents
 lmux is a native Linux terminal purpose-built for developers running
 multiple AI coding agents in parallel. It features a lightweight C
 core daemon, a GTK3 graphical frontend with VTE terminal widgets,
 an experimental GTK4 port, a built-in web dashboard, SSH workspace
 support, AI agent hooks, auto-naming, focus history, task management,
 and OSC terminal notification handling.
 .
 This package includes the GUI launcher (lmux), CLI-only launcher
 (lmux-cli), Python GUI modules, web dashboard, and C core binary.
CONTROL

# ─── 14. DEBIAN/conffiles ──────────────────────────────────────────
echo "==> Writing conffiles..."
cat > "$BUILD_DIR/DEBIAN/conffiles" << 'CONFFILES'
/etc/lmux/config.json
CONFFILES

# ─── 15. DEBIAN/postinst ───────────────────────────────────────────
echo "==> Writing postinst script..."
cat > "$BUILD_DIR/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e

# Ensure the core binary is executable
chmod 755 /usr/lib/lmux/build/lmux 2>/dev/null || true

# Refresh desktop + icon caches so the app appears in the launcher immediately
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
fi

# Per-user config (~/.config/lmux/) and data (~/.lmux/) are NOT created here:
# the app creates them itself on first run. Running this script as root during
# install would otherwise write into /root, not the installing user's home.

echo ""
echo "lmux has been installed successfully."
echo ""
echo "  GUI:    Run 'lmux' to start the graphical interface."
echo "  CLI:    Run 'lmux-cli daemon' to start the daemon."
echo "  Config: Edit ~/.config/lmux/config.json or /etc/lmux/config.json"
echo ""
POSTINST
chmod 755 "$BUILD_DIR/DEBIAN/postinst"

# ─── 16. DEBIAN/postrm ─────────────────────────────────────────────
echo "==> Writing postrm script..."
cat > "$BUILD_DIR/DEBIAN/postrm" << 'POSTRM'
#!/bin/bash
set -e

case "$1" in
    remove)
        # Refresh desktop + icon caches after the entry/icon were removed
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q /usr/share/applications 2>/dev/null || true
        fi
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
        fi
        echo ""
        echo "lmux has been removed."
        echo "User data in ~/.lmux/ and ~/.config/lmux/ was NOT removed."
        echo "To remove user data: rm -rf ~/.lmux ~/.config/lmux"
        echo ""
        ;;
    purge)
        # Per-user data cannot be purged from a system script (no way to tell
        # which user owns it); leave it for the user to remove manually.
        echo "lmux purged. Remove user data manually: rm -rf ~/.lmux ~/.config/lmux"
        ;;
    upgrade|failed-upgrade|abort-install|abort-upgrade|disappear)
        ;;
    *)
        echo "postrm called with unknown argument: $1" >&2
        exit 1
        ;;
esac
POSTRM
chmod 755 "$BUILD_DIR/DEBIAN/postrm"

# ─── 17. Set permissions ───────────────────────────────────────────
echo "==> Setting file permissions..."
find "$BUILD_DIR/usr/lib/${PACKAGE}" -type f -name '*.py' -exec chmod 644 {} \;
find "$BUILD_DIR/usr/lib/${PACKAGE}" -type f -name '*.c' -o -name '*.h' | xargs chmod 644 2>/dev/null || true
find "$BUILD_DIR/usr/lib/${PACKAGE}" -type f \( -name '*.html' -o -name '*.css' \) -exec chmod 644 {} \;
find "$BUILD_DIR/usr/share" -type f -exec chmod 644 {} \;
chmod 644 "$BUILD_DIR/etc/${PACKAGE}/config.json"
chmod 755 "$BUILD_DIR/usr/bin/lmux"
chmod 755 "$BUILD_DIR/usr/bin/lmux-cli"
chmod 755 "$BUILD_DIR/usr/lib/${PACKAGE}/build/lmux"

# ─── 18. Build the .deb ────────────────────────────────────────────
echo "==> Building .deb package..."
fakeroot dpkg-deb --build "$BUILD_DIR" "dist/deb/${PACKAGE}_${VERSION}_${ARCH}.deb"

echo ""
echo "==> Done!"
echo "Package: dist/deb/${PACKAGE}_${VERSION}_${ARCH}.deb"
ls -lh "dist/deb/${PACKAGE}_${VERSION}_${ARCH}.deb"
echo ""
echo "Install:    make install-deb"
echo "  (stages the .deb in /tmp so the apt _apt sandbox can read it, avoiding"
echo "   the 'couldn't be accessed by user _apt' warning from a private home dir)"
echo "Manual:     cp dist/deb/${PACKAGE}_${VERSION}_${ARCH}.deb /tmp/ && sudo apt install /tmp/${PACKAGE}_${VERSION}_${ARCH}.deb"
