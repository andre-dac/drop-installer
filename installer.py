"""
drop-installer installer: handles installing apps to standard Pop!_OS XDG paths,
managing desktop entries, icons, symlinks, and the uninstaller manifest.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import re

from inspector import AppCandidate, sanitize_app_id, format_executable_display_name


# Pop!_OS standard user XDG locations
HOME = Path.home()

def get_base_dir() -> Path:
    env_base = os.environ.get("DROP_INSTALLER_BASE_DIR")
    if env_base:
        return Path(env_base)
    return HOME / ".local"

def get_opt_dir() -> Path:
    return get_base_dir() / "opt"

def get_apps_dir() -> Path:
    return get_base_dir() / "share" / "applications"

def get_icons_dir() -> Path:
    return get_base_dir() / "share" / "icons" / "hicolor"

def get_bin_dir() -> Path:
    return get_base_dir() / "bin"

def get_data_dir() -> Path:
    return get_base_dir() / "share" / "drop-installer"

def get_manifest_path() -> Path:
    return get_data_dir() / "manifest.json"

# Convenience aliases
OPT_DIR = get_opt_dir()
APPS_DIR = get_apps_dir()
ICONS_DIR = get_icons_dir()
BIN_DIR = get_bin_dir()
DATA_DIR = get_data_dir()
MANIFEST_PATH = get_manifest_path()


def ensure_xdg_directories():
    """Ensure standard user XDG directories exist."""
    get_opt_dir().mkdir(parents=True, exist_ok=True)
    get_apps_dir().mkdir(parents=True, exist_ok=True)
    get_bin_dir().mkdir(parents=True, exist_ok=True)
    get_data_dir().mkdir(parents=True, exist_ok=True)
    (get_icons_dir() / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
    (get_icons_dir() / "256x256" / "apps").mkdir(parents=True, exist_ok=True)


def load_manifest() -> Dict[str, dict]:
    """Load the installed apps manifest dictionary."""
    manifest_path = get_manifest_path()
    if not manifest_path.exists():
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_manifest(manifest: Dict[str, dict]):
    """Persist the installed apps manifest."""
    ensure_xdg_directories()
    with open(get_manifest_path(), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def refresh_desktop_database():
    """Notify Pop!_OS / GNOME / COSMIC desktop of new or removed apps."""
    try:
        subprocess.run(
            ["update-desktop-database", str(get_apps_dir())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )
    except Exception:
        pass
        
    try:
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(get_icons_dir().parent)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )
    except Exception:
        pass


def generate_fallback_svg_icon(name: str, target_svg_path: Path):
    """Generate a clean, modern fallback SVG icon with the app's first initial."""
    initial = (name.strip()[:1] or "A").upper()
    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#48b9c7" />
      <stop offset="100%" stop-color="#2a7b88" />
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="28" fill="url(#grad)" />
  <text x="50%" y="54%" font-family="Ubuntu, sans-serif" font-size="64" font-weight="bold" fill="#ffffff" text-anchor="middle" dominant-baseline="middle">{initial}</text>
</svg>"""
    target_svg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)


def install_icon(app_id: str, app_name: str, source_icon_path: Optional[Path]) -> Path:
    """
    Copies or creates an icon in standard ~/.local/share/icons/hicolor/...
    Returns the path to the installed icon file.
    """
    ensure_xdg_directories()
    icons_dir = get_icons_dir()

    if source_icon_path and source_icon_path.exists():
        ext = source_icon_path.suffix.lower()
        if ext == ".svg":
            dest = icons_dir / "scalable" / "apps" / f"dropinstall-{app_id}.svg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_icon_path, dest)
            # Remove stale png if exists
            stale_png = icons_dir / "256x256" / "apps" / f"dropinstall-{app_id}.png"
            if stale_png.exists():
                try:
                    stale_png.unlink()
                except OSError:
                    pass
            return dest
        else:
            dest = icons_dir / "256x256" / "apps" / f"dropinstall-{app_id}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Remove stale svg if exists
            stale_svg = icons_dir / "scalable" / "apps" / f"dropinstall-{app_id}.svg"
            if stale_svg.exists():
                try:
                    stale_svg.unlink()
                except OSError:
                    pass

            if ext == ".png":
                shutil.copy2(source_icon_path, dest)
            else:
                converted = False
                try:
                    import gi
                    gi.require_version('GdkPixbuf', '2.0')
                    from gi.repository import GdkPixbuf
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(source_icon_path))
                    pixbuf.savev(str(dest), "png", [], [])
                    converted = True
                except Exception:
                    converted = False
                if not converted:
                    dest = icons_dir / "256x256" / "apps" / f"dropinstall-{app_id}{ext}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_icon_path, dest)
            return dest
    else:
        dest = icons_dir / "scalable" / "apps" / f"dropinstall-{app_id}.svg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Remove stale png if exists
        stale_png = icons_dir / "256x256" / "apps" / f"dropinstall-{app_id}.png"
        if stale_png.exists():
            try:
                stale_png.unlink()
            except OSError:
                pass
        generate_fallback_svg_icon(app_name, dest)
        return dest



def create_desktop_entry(
    app_id: str,
    name: str,
    exec_cmd: str,
    icon_path: Path,
    categories: str = "Utility;",
    comment: str = "Installed via Drop Installer",
    startup_wm_class: str = "",
    terminal: bool = False,
    work_dir: Optional[Path] = None
) -> Path:
    """Creates a standard FreeDesktop .desktop launcher entry in ~/.local/share/applications."""
    ensure_xdg_directories()
    desktop_file = get_apps_dir() / f"dropinstall-{app_id}.desktop"
    
    if not categories.endswith(";"):
        categories += ";"
        
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={name}",
        f"Comment={comment}",
        f"Exec={exec_cmd}",
        f"Icon={str(icon_path)}",
        f"Terminal={'true' if terminal else 'false'}",
        f"Categories={categories}",
    ]
    if work_dir:
        lines.append(f"Path={str(work_dir)}")
    if startup_wm_class:
        lines.append(f"StartupWMClass={startup_wm_class}")
        
    lines.append("X-Installed-By=Drop-Installer\n")
    
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    # Mark desktop file executable (standard for desktop entries)
    try:
        os.chmod(desktop_file, os.stat(desktop_file).st_mode | 0o755)
    except OSError:
        pass
        
    return desktop_file


def create_cli_symlink(app_id: str, target_exec: Path) -> Path:
    """Create a symlink in ~/.local/bin/<app_id> for CLI terminal execution."""
    ensure_xdg_directories()
    symlink_path = get_bin_dir() / app_id
    if symlink_path.is_symlink() or symlink_path.exists():
        try:
            symlink_path.unlink()
        except OSError:
            pass
    try:
        symlink_path.symlink_to(target_exec)
    except OSError:
        pass
    return symlink_path


def install_deb_package(deb_path: Path) -> Tuple[bool, str]:
    """
    Installs a .deb package using pkexec apt-get install -y.
    Triggers the native Pop!_OS authentication dialog.
    """
    cmd = ["pkexec", "apt-get", "install", "-y", str(deb_path.resolve())]
    try:
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if process.returncode == 0:
            return True, "Successfully installed via apt."
        else:
            return False, f"Installation failed (code {process.returncode}):\n{process.stderr or process.stdout}"
    except Exception as e:
        return False, f"Failed to execute pkexec: {e}"


def install_candidate(
    candidate: AppCandidate,
    name: Optional[str] = None,
    category: Optional[str] = None,
    executable_rel: Optional[str] = None,
    icon_source: Optional[Path] = None,
    use_no_sandbox: bool = False,
    move_original: bool = False,
    startup_wm_class: Optional[str] = None,
    additional_executables: Optional[List[str]] = None
) -> Tuple[bool, str]:
    """
    Installs the inspected candidate into ~/.local/opt/<app_id>/, registers
    the launcher shortcut, icon, and symlink.
    """
    ensure_xdg_directories()
    app_id = candidate.app_id
    final_name = name or candidate.name
    final_category = category or candidate.categories
    final_wm_class = startup_wm_class if startup_wm_class is not None else candidate.startup_wm_class
    
    # 1. DEB Package Handler
    if candidate.kind == "deb":
        ok, msg = install_deb_package(candidate.source_path)
        if ok:
            manifest = load_manifest()
            manifest[app_id] = {
                "app_id": app_id,
                "name": final_name,
                "kind": "deb",
                "version": candidate.version,
                "package_name": candidate.deb_package_name,
                "installed_at": datetime.now().isoformat(),
                "source_path": str(candidate.source_path)
            }
            save_manifest(manifest)
            refresh_desktop_database()
            if move_original:
                try:
                    candidate.source_path.unlink()
                except OSError:
                    pass
        return ok, msg

    # 2. AppImage Handler
    if candidate.kind == "appimage":
        target_dir = get_opt_dir() / app_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_appimage = target_dir / f"{app_id}.AppImage"
        
        if move_original:
            shutil.move(str(candidate.source_path), str(target_appimage))
        else:
            shutil.copy2(str(candidate.source_path), str(target_appimage))
            
        os.chmod(target_appimage, os.stat(target_appimage).st_mode | 0o755)
        
        # Icon
        chosen_icon = icon_source or (candidate.selected_icon.path if candidate.selected_icon else None)
        installed_icon = install_icon(app_id, final_name, chosen_icon)
        
        # Executable string
        exec_str = f'"{target_appimage}"'
        if use_no_sandbox:
            exec_str += " --no-sandbox"
        exec_str += " %U"
        
        desktop_file = create_desktop_entry(
            app_id=app_id,
            name=final_name,
            exec_cmd=exec_str,
            icon_path=installed_icon,
            categories=final_category,
            comment=candidate.comment or f"{final_name} AppImage",
            startup_wm_class=final_wm_class or app_id,
            work_dir=target_dir
        )
        
        cli_link = create_cli_symlink(app_id, target_appimage)
        
        manifest = load_manifest()
        manifest[app_id] = {
            "app_id": app_id,
            "name": final_name,
            "kind": "appimage",
            "version": candidate.version,
            "installed_dir": str(target_dir),
            "exec_path": str(target_appimage),
            "desktop_file": str(desktop_file),
            "icon_path": str(installed_icon),
            "bin_symlink": str(cli_link),
            "installed_at": datetime.now().isoformat()
        }
        save_manifest(manifest)
        refresh_desktop_database()
        return True, f"Installed {final_name} successfully!"

    # 3. Portable Archive or Folder
    target_dir = get_opt_dir() / app_id
    if target_dir.exists():
        # Remove existing older install of same app
        try:
            shutil.rmtree(target_dir)
        except OSError:
            pass

    if candidate.kind == "archive":
        # Staged extraction exists
        if not candidate.extracted_root or not candidate.extracted_root.exists():
            return False, "Extracted staging directory is missing."
        shutil.move(str(candidate.extracted_root), str(target_dir))
        candidate.is_staged = False  # moved, no need to cleanup
    elif candidate.kind == "folder":
        folder_to_install = candidate.extracted_root or candidate.source_path
        # Copy directory tree preserving symlinks (and ensuring new copy is owned by user)
        shutil.copytree(str(folder_to_install), str(target_dir), symlinks=True)

    # Resolve primary executable inside target_dir
    exec_relative = executable_rel or (candidate.selected_executable.relative_path if candidate.selected_executable else None)
    if not exec_relative:
        return False, "No executable specified or detected."
        
    final_exec_path = target_dir / exec_relative
    if not final_exec_path.exists():
        return False, f"Executable not found at expected path: {final_exec_path}"
        
    # Ensure +x
    try:
        os.chmod(final_exec_path, os.stat(final_exec_path).st_mode | 0o755)
    except OSError:
        pass

    # Resolve icon
    chosen_icon = icon_source
    if not chosen_icon and candidate.selected_icon:
        # Check if icon is inside the newly moved target_dir
        candidate_icon_rel = candidate.selected_icon.relative_path
        inside_icon = target_dir / candidate_icon_rel
        if inside_icon.exists():
            chosen_icon = inside_icon
        elif candidate.selected_icon.path.exists():
            chosen_icon = candidate.selected_icon.path

    installed_icon = install_icon(app_id, final_name, chosen_icon)

    # Executable command
    exec_str = f'"{final_exec_path}"'
    if use_no_sandbox:
        exec_str += " --no-sandbox"
    exec_str += " %U"

    desktop_file = create_desktop_entry(
        app_id=app_id,
        name=final_name,
        exec_cmd=exec_str,
        icon_path=installed_icon,
        categories=final_category,
        comment=candidate.comment or f"{final_name} application",
        startup_wm_class=final_wm_class or app_id,
        work_dir=target_dir
    )

    cli_link = create_cli_symlink(app_id, final_exec_path)

    # Install any additional executables as companion launchers (e.g. NetLogo 3D)
    additional_launchers = []
    if additional_executables:
        for add_rel in additional_executables:
            if not add_rel or add_rel == exec_relative:
                continue
            sub_exec_path = target_dir / add_rel
            if not sub_exec_path.exists():
                continue
            try:
                os.chmod(sub_exec_path, os.stat(sub_exec_path).st_mode | 0o755)
            except OSError:
                pass

            sub_file_name = Path(add_rel).name
            sub_stem = Path(add_rel).stem
            sub_name = format_executable_display_name(sub_file_name, base_app_name=final_name)

            if sub_stem.lower().startswith(app_id.lower()):
                sub_id = sanitize_app_id(sub_stem)
            else:
                sub_id = sanitize_app_id(f"{app_id}-{sub_stem}")

            # Match dedicated companion icon if available (e.g. NetLogo3D.png for NetLogo3D)
            sub_stem_clean = re.sub(r"[^a-z0-9]", "", sub_stem.lower())
            sub_icon_path = None
            for cand_ic in candidate.icons:
                cand_ic_stem = re.sub(r"[^a-z0-9]", "", cand_ic.path.stem.lower())
                if cand_ic_stem == sub_stem_clean:
                    try:
                        rel_ic = cand_ic.path.relative_to(candidate.extracted_root or candidate.source_path)
                        if (target_dir / rel_ic).exists():
                            sub_icon_path = target_dir / rel_ic
                        else:
                            sub_icon_path = cand_ic.path
                    except Exception:
                        sub_icon_path = cand_ic.path
                    break

            sub_installed_icon = install_icon(sub_id, sub_name, sub_icon_path or chosen_icon)

            sub_exec_str = f'"{sub_exec_path}"'
            if use_no_sandbox:
                sub_exec_str += " --no-sandbox"
            sub_exec_str += " %U"

            sub_desktop_file = create_desktop_entry(
                app_id=sub_id,
                name=sub_name,
                exec_cmd=sub_exec_str,
                icon_path=sub_installed_icon,
                categories=final_category,
                comment=f"{sub_name} application",
                startup_wm_class=sub_id,
                work_dir=target_dir
            )
            sub_cli_link = create_cli_symlink(sub_id, sub_exec_path)
            additional_launchers.append({
                "name": sub_name,
                "app_id": sub_id,
                "desktop_file": str(sub_desktop_file),
                "bin_symlink": str(sub_cli_link),
                "exec_path": str(sub_exec_path),
                "icon_path": str(sub_installed_icon)
            })

    # Save manifest and refresh launcher
    manifest = load_manifest()
    manifest[app_id] = {
        "app_id": app_id,
        "name": final_name,
        "kind": candidate.kind,
        "version": candidate.version,
        "installed_dir": str(target_dir),
        "exec_path": str(final_exec_path),
        "desktop_file": str(desktop_file),
        "icon_path": str(installed_icon),
        "bin_symlink": str(cli_link),
        "additional_launchers": additional_launchers,
        "installed_at": datetime.now().isoformat()
    }
    save_manifest(manifest)
    refresh_desktop_database()

    # Non-fatal source cleanup if requested
    cleanup_note = ""
    if move_original:
        if candidate.kind == "archive" and candidate.source_path.exists():
            try:
                candidate.source_path.unlink()
            except OSError:
                pass
        elif candidate.kind == "folder":
            try:
                shutil.rmtree(str(folder_to_install))
                if candidate.source_path != folder_to_install and candidate.source_path.exists():
                    rem = [p for p in candidate.source_path.iterdir() if not p.name.startswith(".")]
                    if not rem:
                        shutil.rmtree(str(candidate.source_path))
            except PermissionError:
                cleanup_note = " (Note: Original folder in Documents has root permissions and was preserved; you can safely delete it with sudo rm -rf if desired.)"
            except OSError:
                pass

    add_msg = f" (along with {len(additional_launchers)} companion launcher(s))" if additional_launchers else ""
    return True, f"Installed {final_name}{add_msg} successfully into ~/.local/opt/{app_id}!{cleanup_note}"


def uninstall_app(app_id: str) -> Tuple[bool, str]:
    """
    Cleanly uninstalls an application: removes desktop entry, icon,
    CLI symlink, any companion launchers, and ~/.local/opt directory (or runs apt-get remove for deb).
    """
    manifest = load_manifest()
    if app_id not in manifest:
        return False, f"App ID '{app_id}' not found in manifest."
        
    entry = manifest[app_id]
    kind = entry.get("kind")
    
    if kind == "deb":
        pkg = entry.get("package_name") or app_id
        cmd = ["pkexec", "apt-get", "remove", "-y", pkg]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            return False, f"Apt removal failed:\n{res.stderr or res.stdout}"
        del manifest[app_id]
        save_manifest(manifest)
        refresh_desktop_database()
        return True, f"Successfully uninstalled deb package {pkg}."
        
    # Portable / AppImage / Folder
    installed_dir = entry.get("installed_dir")
    if installed_dir and Path(installed_dir).exists():
        try:
            shutil.rmtree(installed_dir)
        except OSError:
            pass
            
    desktop_file = entry.get("desktop_file")
    if desktop_file and Path(desktop_file).exists():
        try:
            Path(desktop_file).unlink()
        except OSError:
            pass

    # Remove any additional companion launchers
    for sub in entry.get("additional_launchers", []):
        sub_df = sub.get("desktop_file")
        if sub_df and Path(sub_df).exists():
            try:
                Path(sub_df).unlink()
            except OSError:
                pass
        sub_link = sub.get("bin_symlink")
        if sub_link and (Path(sub_link).is_symlink() or Path(sub_link).exists()):
            try:
                Path(sub_link).unlink()
            except OSError:
                pass
        sub_icon = sub.get("icon_path")
        if sub_icon and Path(sub_icon).exists():
            try:
                Path(sub_icon).unlink()
            except OSError:
                pass
        sub_id = sub.get("app_id")
        if sub_id:
            for cand_p in (
                get_icons_dir() / "scalable" / "apps" / f"dropinstall-{sub_id}.svg",
                get_icons_dir() / "256x256" / "apps" / f"dropinstall-{sub_id}.png"
            ):
                if cand_p.exists():
                    try:
                        cand_p.unlink()
                    except OSError:
                        pass
            
    icon_path = entry.get("icon_path")
    if icon_path and Path(icon_path).exists():
        try:
            Path(icon_path).unlink()
        except OSError:
            pass
            
    bin_symlink = entry.get("bin_symlink")
    if bin_symlink and (Path(bin_symlink).is_symlink() or Path(bin_symlink).exists()):
        try:
            Path(bin_symlink).unlink()
        except OSError:
            pass
            
    del manifest[app_id]
    save_manifest(manifest)
    refresh_desktop_database()
    return True, f"Successfully uninstalled {entry.get('name', app_id)}."


def get_app_launchers(app_id: str) -> List[Dict[str, Any]]:
    """
    Returns a list of all launcher configurations for an installed application,
    including the primary launcher and any companion launchers.
    """
    manifest = load_manifest()
    entry = manifest.get(app_id)
    if not entry:
        return []

    launchers = []

    # 1. Main launcher
    main_name = entry.get("name", app_id)
    main_desktop = entry.get("desktop_file")
    main_icon = entry.get("icon_path")
    main_exec = entry.get("exec_path")

    if not main_icon and main_desktop and Path(main_desktop).exists():
        for line in Path(main_desktop).read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("Icon="):
                main_icon = line.split("=", 1)[1].strip()
                break

    launchers.append({
        "name": main_name,
        "app_id": app_id,
        "is_main": True,
        "desktop_file": str(main_desktop) if main_desktop else "",
        "icon_path": str(main_icon) if main_icon else "",
        "exec_path": str(main_exec) if main_exec else ""
    })

    # 2. Recorded companion launchers from manifest
    seen_desktop_files = {str(main_desktop)} if main_desktop else set()
    for sub in entry.get("additional_launchers", []):
        sub_df = sub.get("desktop_file", "")
        if sub_df:
            seen_desktop_files.add(str(sub_df))
        sub_icon = sub.get("icon_path")
        if not sub_icon and sub_df and Path(sub_df).exists():
            for line in Path(sub_df).read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Icon="):
                    sub_icon = line.split("=", 1)[1].strip()
                    break
        sub_id = sub.get("app_id", "")
        if not sub_icon and sub_id:
            for cand_p in (
                get_icons_dir() / "scalable" / "apps" / f"dropinstall-{sub_id}.svg",
                get_icons_dir() / "256x256" / "apps" / f"dropinstall-{sub_id}.png"
            ):
                if cand_p.exists():
                    sub_icon = str(cand_p)
                    break
        launchers.append({
            "name": sub.get("name", sub.get("app_id", "Companion")),
            "app_id": sub_id,
            "is_main": False,
            "desktop_file": str(sub_df),
            "icon_path": str(sub_icon) if sub_icon else "",
            "exec_path": str(sub.get("exec_path", ""))
        })

    # 3. Discover any unrecorded companion .desktop files matching dropinstall-{app_id}-*.desktop
    apps_dir = get_apps_dir()
    if apps_dir.exists():
        for df in sorted(apps_dir.glob(f"dropinstall-{app_id}*.desktop")):
            df_str = str(df)
            if df_str in seen_desktop_files:
                continue
            seen_desktop_files.add(df_str)
            df_name = df.stem
            df_icon = ""
            df_exec = ""
            for line in df.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Name="):
                    df_name = line.split("=", 1)[1].strip()
                elif line.startswith("Icon="):
                    df_icon = line.split("=", 1)[1].strip()
                elif line.startswith("Exec="):
                    df_exec = line.split("=", 1)[1].strip()
            sub_id = df.stem.replace("dropinstall-", "")
            launchers.append({
                "name": df_name,
                "app_id": sub_id,
                "is_main": False,
                "desktop_file": df_str,
                "icon_path": df_icon,
                "exec_path": df_exec
            })

    return launchers


def update_launcher_icon(
    app_id: str,
    new_icon_source: Optional[Path],
    target_desktop_file: Optional[Path] = None,
    target_sub_id: Optional[str] = None
) -> Tuple[bool, str, Optional[Path]]:
    """
    Updates the icon for an installed application or companion launcher.
    - If new_icon_source is None, resets/generates a fallback letter SVG icon.
    - Updates ~/.local/share/icons/hicolor/...
    - Updates the Icon= property in the target .desktop file.
    - Updates manifest.json entry.
    - Refreshes FreeDesktop icon cache and desktop database.
    """
    manifest = load_manifest()
    if app_id not in manifest:
        return False, f"App ID '{app_id}' not found in manifest.", None

    entry = manifest[app_id]

    # Determine which launcher is being modified
    is_main = True
    target_name = entry.get("name", app_id)
    icon_id = app_id
    desktop_file = Path(entry.get("desktop_file", "")) if entry.get("desktop_file") else None

    if target_desktop_file:
        target_df_str = str(target_desktop_file)
        if entry.get("desktop_file") and str(entry.get("desktop_file")) != target_df_str:
            is_main = False
            desktop_file = target_desktop_file
            matched_sub = None
            for sub in entry.get("additional_launchers", []):
                if sub.get("desktop_file") == target_df_str or (target_sub_id and sub.get("app_id") == target_sub_id):
                    matched_sub = sub
                    break
            if matched_sub:
                icon_id = matched_sub.get("app_id") or sanitize_app_id(target_desktop_file.stem)
                target_name = matched_sub.get("name", icon_id)
            elif target_sub_id:
                icon_id = target_sub_id
                target_name = target_sub_id
            else:
                icon_id = sanitize_app_id(target_desktop_file.stem.replace("dropinstall-", ""))
                target_name = icon_id
    elif target_sub_id and target_sub_id != app_id:
        is_main = False
        icon_id = target_sub_id
        for sub in entry.get("additional_launchers", []):
            if sub.get("app_id") == target_sub_id:
                target_name = sub.get("name", icon_id)
                if sub.get("desktop_file"):
                    desktop_file = Path(sub.get("desktop_file"))
                break

    # Install or generate the new icon
    installed_icon = install_icon(icon_id, target_name, new_icon_source)

    # Update .desktop file if it exists
    if desktop_file and desktop_file.exists():
        try:
            content = desktop_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            new_lines = []
            icon_found = False
            for line in lines:
                if line.startswith("Icon="):
                    new_lines.append(f"Icon={str(installed_icon)}")
                    icon_found = True
                else:
                    new_lines.append(line)
            if not icon_found:
                new_lines.append(f"Icon={str(installed_icon)}")
            desktop_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            os.chmod(desktop_file, os.stat(desktop_file).st_mode | 0o755)
        except OSError as e:
            return False, f"Could not update desktop file: {e}", None

    # Update manifest.json
    if is_main:
        entry["icon_path"] = str(installed_icon)
    else:
        found_in_manifest = False
        if "additional_launchers" not in entry:
            entry["additional_launchers"] = []
        for sub in entry["additional_launchers"]:
            if (target_sub_id and sub.get("app_id") == target_sub_id) or (desktop_file and sub.get("desktop_file") == str(desktop_file)):
                sub["icon_path"] = str(installed_icon)
                found_in_manifest = True
                break
        if not found_in_manifest:
            entry["additional_launchers"].append({
                "name": target_name,
                "app_id": icon_id,
                "desktop_file": str(desktop_file) if desktop_file else "",
                "icon_path": str(installed_icon)
            })

    save_manifest(manifest)
    refresh_desktop_database()

    return True, f"Updated icon for {target_name} successfully!", installed_icon
