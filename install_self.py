#!/usr/bin/env python3
"""
install_self.py: Installs Drop Installer into ~/.local/opt/drop-installer,
registers ~/.local/share/applications/drop-installer.desktop, installs its SVG icon,
and sets up ~/.local/bin/drop-installer.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


HOME = Path.home()
BASE_DIR = Path(os.environ.get("DROP_INSTALLER_BASE_DIR", str(HOME / ".local")))
TARGET_OPT = BASE_DIR / "opt" / "drop-installer"
APPS_DIR = BASE_DIR / "share" / "applications"
ICONS_DIR = BASE_DIR / "share" / "icons" / "hicolor" / "scalable" / "apps"
BIN_DIR = BASE_DIR / "bin"
SOURCE_DIR = Path(__file__).parent.resolve()


APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#48b9c7"/>
      <stop offset="100%" stop-color="#1f6570"/>
    </linearGradient>
    <linearGradient id="box" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fdfdfd"/>
      <stop offset="100%" stop-color="#d6dadf"/>
    </linearGradient>
    <filter id="shadow" x="-10%" y="-10%" width="130%" height="130%">
      <feDropShadow dx="0" dy="4" stdDeviation="4" flood-opacity="0.3"/>
    </filter>
  </defs>

  <!-- Background rounded squircle -->
  <rect width="128" height="128" rx="28" fill="url(#bg)"/>

  <!-- Package box shape -->
  <g filter="url(#shadow)" transform="translate(24, 26)">
    <!-- Top flap -->
    <path d="M40 8 L72 24 L40 40 L8 24 Z" fill="#ffffff" opacity="0.95"/>
    <path d="M40 8 L8 24 L8 28 L40 12 Z" fill="#b0b8c0" opacity="0.5"/>
    <!-- Left side -->
    <path d="M8 24 L40 40 L40 68 L8 52 Z" fill="url(#box)"/>
    <!-- Right side -->
    <path d="M40 40 L72 24 L72 52 L40 68 Z" fill="#c4cbd4"/>
    <!-- Inner box tape/accent -->
    <path d="M36 22 L44 26 L44 54 L36 50 Z" fill="#f89406" opacity="0.9"/>
  </g>

  <!-- Download / Drop arrow -->
  <g transform="translate(64, 46)">
    <path d="M-8 -24 L8 -24 L8 -8 L18 -8 L0 12 L-18 -8 L-8 -8 Z" fill="#ffb834" filter="url(#shadow)"/>
  </g>
</svg>"""


DESKTOP_CONTENT = f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Drop Installer
GenericName=App Installer
Comment=Drag-and-drop installer for portable apps, archives, debs, and AppImages on Pop!_OS
Exec=/usr/bin/python3 {TARGET_OPT}/main.py %U
Icon=drop-installer
Terminal=false
Categories=Utility;System;Settings;
Keywords=install;archive;deb;appimage;tar;zip;launcher;pop;
MimeType=application/x-tar;application/zip;application/x-compressed-tar;application/x-xz;application/x-bzip2;application/vnd.debian.binary-package;application/x-appimage;
StartupWMClass=drop-installer
X-PopOS-App=true
"""


def main():
    print(f"Installing Drop Installer to {TARGET_OPT}...")

    # Ensure directories
    TARGET_OPT.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy source files to ~/.local/opt/drop-installer
    for file_name in ("main.py", "ui.py", "inspector.py", "installer.py"):
        src = SOURCE_DIR / file_name
        dest = TARGET_OPT / file_name
        shutil.copy2(src, dest)
        if file_name == "main.py":
            os.chmod(dest, 0o755)

    # 2. Write icon (both scalable SVG and 256x256 PNG for maximum desktop/dock compatibility)
    icon_file = ICONS_DIR / "drop-installer.svg"
    with open(icon_file, "w", encoding="utf-8") as f:
        f.write(APP_ICON_SVG)
    print(f"✓ Installed SVG icon to {icon_file}")

    png_dir = BASE_DIR / "share" / "icons" / "hicolor" / "256x256" / "apps"
    png_dir.mkdir(parents=True, exist_ok=True)
    png_file = png_dir / "drop-installer.png"
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_file), 256, 256, True)
        pixbuf.savev(str(png_file), "png", [], [])
        print(f"✓ Installed 256x256 PNG icon to {png_file}")
    except Exception as e:
        print(f"Note: Could not generate PNG icon: {e}")

    # 3. Write desktop file
    desktop_file = APPS_DIR / "drop-installer.desktop"
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(DESKTOP_CONTENT)
    os.chmod(desktop_file, 0o755)
    print(f"✓ Registered launcher to {desktop_file}")

    # 4. Create symlink in ~/.local/bin/drop-installer
    symlink_file = BIN_DIR / "drop-installer"
    if symlink_file.is_symlink() or symlink_file.exists():
        try:
            symlink_file.unlink()
        except OSError:
            pass
    try:
        symlink_file.symlink_to(TARGET_OPT / "main.py")
        print(f"✓ Created CLI command {symlink_file}")
    except OSError as e:
        print(f"Warning: Could not create symlink: {e}")

    # 5. Refresh databases
    try:
        subprocess.run(["update-desktop-database", str(APPS_DIR)], check=False)
        hicolor_dir = BASE_DIR / "share" / "icons" / "hicolor"
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(hicolor_dir)], check=False)
    except Exception:
        pass

    print("\n✓ Drop Installer successfully installed!")
    print("You can now open it via:")
    print("  1. Pressing the Super key and typing 'Drop Installer'")
    print("  2. Running 'drop-installer' in the terminal")
    print("  3. Right-clicking .tar.gz, .zip, .deb, or .AppImage files -> Open With Drop Installer")


if __name__ == "__main__":
    main()
