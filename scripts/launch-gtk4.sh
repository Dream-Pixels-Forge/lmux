#!/bin/bash
# lmux GTK4 launcher — GPU-accelerated terminal multiplexer
#
# Usage:
#   ./launch-gtk4.sh              # Default (auto GPU)
#   ./launch-gtk4.sh --opengl    # Force OpenGL backend
#   ./launch-gtk4.sh --vulkan    # Force Vulkan backend
#   ./launch-gtk4.sh --software  # Software rendering (fallback)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUI_DIR="$(dirname "$SCRIPT_DIR")"

# Parse arguments
RENDERER="default"
case "${1:-}" in
    --opengl)  RENDERER="opengl" ;;
    --vulkan)  RENDERER="vulkan" ;;
    --software) RENDERER="software" ;;
    --help|-h)
        echo "Usage: $0 [--opengl|--vulkan|--software]"
        echo ""
        echo "GPU Rendering Options:"
        echo "  --opengl    Force OpenGL backend (default if available)"
        echo "  --vulkan    Force Vulkan backend"
        echo "  --software  Software rendering (fallback)"
        echo ""
        echo "Requires: gir1.2-gtk-4.0, gir1.2-vte-3.91"
        echo "Install:  sudo apt install gir1.2-gtk-4.0 gir1.2-vte-3.91"
        exit 0
        ;;
esac

# Check for GTK4
if ! python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null; then
    echo "Error: GTK4 Python bindings not found."
    echo ""
    echo "Install GTK4 bindings:"
    echo "  Ubuntu/Debian: sudo apt install gir1.2-gtk-4.0 gir1.2-vte-3.91"
    echo "  Fedora:        sudo dnf install gtk4 vte291-gtk4"
    echo "  Arch:          sudo pacman -S gtk4 vte4"
    exit 1
fi

# Check for VTE 3.91 (GTK4 version)
if ! python3 -c "import gi; gi.require_version('Vte', '3.91')" 2>/dev/null; then
    echo "Warning: VTE 3.91 (GTK4 version) not found."
    echo "Terminal rendering will use fallback."
    echo ""
    echo "Install VTE for GTK4:"
    echo "  Ubuntu/Debian: sudo apt install gir1.2-vte-3.91"
    echo "  Fedora:        sudo dnf install vte291-gtk4"
fi

# Set renderer
if [ "$RENDERER" != "default" ]; then
    export GSK_RENDERER="$RENDERER"
fi

echo "lmux GTK4 — GPU-accelerated terminal multiplexer"
echo "GSK renderer: ${GSK_RENDERER:-auto}"
echo ""

# Run
cd "$GUI_DIR"
exec python3 -m gui.gtk4.main "$@"
