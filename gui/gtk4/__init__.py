"""lmux GTK4 GUI — GPU-accelerated terminal multiplexer.

Usage:
    GSK_RENDERER=opengl python3 -m gui.gtk4.main

Requires:
    - gir1.2-gtk-4.0 (GTK4 Python bindings)
    - gir1.2-vte-2.91 (VTE terminal widget for GTK4)
    - libgtk-4-1 (GTK4 runtime)

Install on Ubuntu/Debian:
    sudo apt install gir1.2-gtk-4.0 gir1.2-vte-2.91

Install on Fedora:
    sudo dnf install gtk4 vte291-gtk4

GPU Rendering:
    GTK4 renders through GSK (Gtk Scene Graph) which uses OpenGL/Vulkan
    by default. Set GSK_RENDERER=opengl to force OpenGL backend.

    Check active renderer:
        GSK_RENDERER=opengl python3 -c "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk; print(Gtk.get_major_version())"
"""
