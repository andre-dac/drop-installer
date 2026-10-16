# Drop Installer

A lightweight, native GTK desktop application for Linux that provides a frictionless drag-and-drop installer for portable apps, archives, AppImages, Debian packages, and unpacked application folders.

Drop any `.tar.gz`, `.zip`, `.AppImage`, `.deb`, or unpacked folder into the window. Drop Installer automatically inspects the contents, discovers the correct executables and high-resolution icons, configures standard FreeDesktop `.desktop` launchers, and integrates the app directly into your system's application menu and launcher.

---

## 🚀 Features

- **Drag & Drop Simplicity**: Drop any supported archive, AppImage, deb package, or existing folder to install or migrate it in seconds.
- **Smart Binary Discovery**: Scans and heuristic-ranks candidate executables, filtering out internal noise binaries (like `chrome-sandbox`, crashpad handlers, and bundled runtime libraries).
- **Multi-Executable Suite Support**: Automatically detects application suites that contain multiple runnable binaries (e.g., primary app + 3D edition or companion tools) and lets you generate launchers for all of them at once with proper working directory (`Path=`) settings.
- **Full Icon Lifecycle**:
  - Automatically identifies high-resolution bundled SVG and PNG icons.
  - Converts non-standard raster formats (`.ico`, `.jpg`, `.bmp`, `.webp`) to clean PNG via `GdkPixbuf`.
  - Generates a modern vector letter-badge icon if no icon is bundled.
  - **Post-Installation Icon Editor**: Customize or change icons for any launcher individually after installation from the **Installed Apps** tab.
- **Standards-Compliant (No Root Required for Portable Apps)**:
  - Application files: `~/.local/opt/<app-id>/`
  - Desktop launchers: `~/.local/share/applications/dropinstall-<app-id>.desktop`
  - Icons: `~/.local/share/icons/hicolor/<size>/apps/`
  - Terminal CLI commands: `~/.local/bin/<app-id>`
- **Installed Apps Manager**: Built-in tab to launch apps, open their installed folder, edit their launcher icons, or cleanly uninstall them and remove all associated shortcuts.
- **Native Desktop Integration**: Fully compatible with GNOME, Pop!_OS / COSMIC, KDE Plasma, XFCE, and any desktop environment following FreeDesktop XDG specifications.

---

## 📦 Supported Formats

| Format | Actions Taken |
| :--- | :--- |
| **Unpacked Folders** | Inspects executables and icons; copies or moves files into `~/.local/opt/` and creates launcher shortcuts. |
| **Compressed Archives** (`.tar.gz`, `.tar.xz`, `.tar.bz2`, `.zip`) | Extracts in a temporary sandbox, unwraps wrapper directories, inspects binaries and icons, and installs into `~/.local/opt/`. |
| **AppImages** (`.AppImage`) | Installs to `~/.local/opt/`, ensures executable permissions (`+x`), extracts internal metadata (`.DirIcon` / desktop entry), and registers launcher. |
| **Debian Packages** (`.deb`) | Reads metadata via `dpkg-deb`, invokes standard system package installation via `pkexec apt-get install`, and tracks the package in the manager. |

---

## 🛠️ Prerequisites

Drop Installer is written in Python 3 using standard libraries and PyGObject (GTK 3).

### Debian / Ubuntu / Pop!_OS
```bash
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 desktop-file-utils
```

### Fedora
```bash
sudo dnf install python3 python3-gobject gtk3 desktop-file-utils
```

### Arch Linux
```bash
sudo pacman -S python python-gobject gtk3 desktop-file-utils
```

---

## 📥 Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/andre-dac/drop-installer.git
   cd drop-installer
   ```

2. **Install Drop Installer on your system:**
   ```bash
   python3 install_self.py
   ```

This registers Drop Installer into your system's application menu, adds an SVG launcher icon, sets up the `drop-installer` command in `~/.local/bin`, and registers file association handlers.

---

## 🖥️ Usage

You can launch Drop Installer in any of the following ways:
- Search **Drop Installer** in your system application launcher (press `Super` / `Win` key).
- Run `drop-installer` from any terminal.
- Pass a file directly:
  ```bash
  drop-installer /path/to/application.tar.gz
  ```
- Right-click any archive, AppImage, or `.deb` in your file manager and choose **Open With Drop Installer**.

### Migrating Loose Folders (e.g. apps stored in `~/Downloads` or `~/Documents`)
1. Open **Drop Installer**.
2. Drag and drop the application folder into the window (or click **Select Folder…**).
3. Review the preview card: verify the detected executable, companion launchers, and icon.
4. Check **"Move original folder / delete archive after installation"** if you want Drop Installer to clean up the source directory.
5. Click **Install to Launcher**.

### Editing Icons After Installation
1. Switch to the **Installed Apps** tab in Drop Installer.
2. Click the **Edit Icon** button on the target application's row.
3. In the modal dialog, click **Change Icon…** next to any launcher (primary or companion) to select any image file, or click **Reset** to generate a letter badge.

---

## 🧪 Running Tests

A comprehensive unit test suite is included, verifying archive handling, folder unwrapping, icon generation, multi-executable management, and uninstallation:

```bash
python3 -m unittest discover -s tests -v
```

---

## 📂 Project Structure

```
drop-installer/
├── main.py              # Application entry point and CLI handler
├── ui.py                # GTK 3 user interface and drag-and-drop handlers
├── inspector.py         # Archive extraction, executable scoring, icon detection
├── installer.py         # XDG path management, desktop entries, manifest & uninstaller
├── install_self.py      # Self-installer script
├── tests/
│   └── test_installer.py # Unit and integration test suite
├── .gitignore           # Git ignore rules
└── README.md            # Project documentation
```

---

## 📄 License

This project is open-source and available under the [MIT License](LICENSE).
