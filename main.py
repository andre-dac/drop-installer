#!/usr/bin/env python3
"""
drop-installer main entry point.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gtk, GLib

    # Set process and application name so Pop!_OS and GNOME Shell dock
    # accurately group the window under drop-installer.desktop rather than "main.py"
    GLib.set_prgname("drop-installer")
    GLib.set_application_name("Drop Installer")
except ImportError as e:
    print(f"Error: Required GTK 3 dependencies not found: {e}", file=sys.stderr)
    print("Please install python3-gi, gir1.2-gtk-3.0.", file=sys.stderr)
    sys.exit(1)

from ui import DropInstallerWindow


def main():
    target_path = None
    if len(sys.argv) > 1:
        arg_path = Path(sys.argv[1]).resolve()
        if arg_path.exists():
            target_path = arg_path

    win = DropInstallerWindow(initial_path=target_path)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
