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
            ["gtk-update-icon-cache", "-f", "-t", str(get_icons_dir())],
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
            if source_icon_path.resolve() != dest.resolve():
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
                if source_icon_path.resolve() != dest.resolve():
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
                    if source_icon_path.resolve() != dest.resolve():
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


def cleanup_portable_files(entry: dict):
    """Remove portable app directory, desktop entries, icons, and symlinks for a manifest entry."""
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


def install_candidate(
    candidate: AppCandidate,
    name: Optional[str] = None,
    category: Optional[str] = None,
    executable_rel: Optional[str] = None,
    icon_source: Optional[Path] = None,
    use_no_sandbox: bool = False,
    move_original: bool = False,
    startup_wm_class: Optional[str] = None,
    additional_executables: Optional[List[str]] = None,
    target_app_id: Optional[str] = None,
    preserve_existing_icons: bool = False
) -> Tuple[bool, str]:
    """
    Installs or updates the inspected candidate into ~/.local/opt/<app_id>/, registers
    the launcher shortcut, icon, and symlink. Uses atomic replacement when updating.
    """
    ensure_xdg_directories()
    app_id = target_app_id or candidate.app_id
    final_name = name or candidate.name
    final_category = category or candidate.categories
    final_wm_class = startup_wm_class if startup_wm_class is not None else candidate.startup_wm_class

    manifest = load_manifest()
    existing_entry = manifest.get(app_id)
    is_update = existing_entry is not None
    existing_update_source = existing_entry.get("update_source") if existing_entry else None
    original_installed_at = existing_entry.get("installed_at") if existing_entry else None
    now_iso = datetime.now().isoformat()

    # 1. DEB Package Handler
    if candidate.kind == "deb":
        ok, msg = install_deb_package(candidate.source_path)
        if ok:
            # If upgrading from a previous portable install, clean up old portable files so they don't shadow APT
            if existing_entry and existing_entry.get("kind") != "deb":
                cleanup_portable_files(existing_entry)

            pkg_name = candidate.deb_package_name or app_id
            update_source = existing_update_source or {"type": "apt", "package_name": pkg_name}
            entry_data = {
                "app_id": app_id,
                "name": final_name,
                "kind": "deb",
                "version": candidate.version,
                "package_name": pkg_name,
                "update_source": update_source,
                "installed_at": original_installed_at or now_iso,
                "source_path": str(candidate.source_path)
            }
            if is_update:
                entry_data["updated_at"] = now_iso
            manifest[app_id] = entry_data
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
        temp_appimage = target_dir / f".{app_id}.AppImage.new.{int(time.time() * 1000)}"

        try:
            shutil.copy2(str(candidate.source_path), str(temp_appimage))
            os.chmod(temp_appimage, os.stat(temp_appimage).st_mode | 0o755)
            os.replace(str(temp_appimage), str(target_appimage))
        except Exception as e:
            if temp_appimage.exists():
                try:
                    temp_appimage.unlink()
                except OSError:
                    pass
            return False, f"Failed to install AppImage: {e}"

        if move_original and candidate.source_path.exists() and candidate.source_path.resolve() != target_appimage.resolve():
            try:
                candidate.source_path.unlink()
            except OSError:
                pass

        # Icon (preserve custom/existing icon on update if requested)
        chosen_icon = icon_source
        kept_custom_icon = False
        if not chosen_icon and existing_entry and (preserve_existing_icons or existing_entry.get("custom_icon")):
            prev_icon = existing_entry.get("icon_path")
            if prev_icon and Path(prev_icon).exists():
                chosen_icon = Path(prev_icon)
                kept_custom_icon = bool(existing_entry.get("custom_icon"))
        if not chosen_icon and candidate.selected_icon:
            chosen_icon = candidate.selected_icon.path

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

        entry_data = {
            "app_id": app_id,
            "name": final_name,
            "kind": "appimage",
            "version": candidate.version,
            "category": final_category,
            "use_no_sandbox": use_no_sandbox,
            "startup_wm_class": final_wm_class or app_id,
            "installed_dir": str(target_dir),
            "exec_path": str(target_appimage),
            "desktop_file": str(desktop_file),
            "icon_path": str(installed_icon),
            "bin_symlink": str(cli_link),
            "installed_at": original_installed_at or now_iso
        }
        if kept_custom_icon:
            entry_data["custom_icon"] = True
        if existing_update_source:
            entry_data["update_source"] = existing_update_source
        if is_update:
            entry_data["updated_at"] = now_iso

        manifest[app_id] = entry_data
        save_manifest(manifest)
        refresh_desktop_database()
        action_word = "Updated" if is_update else "Installed"
        return True, f"{action_word} {final_name} (v{candidate.version}) successfully!"

    # 3. Portable Archive or Folder (with atomic swap & rollback on failure)
    exec_relative = executable_rel or (candidate.selected_executable.relative_path if candidate.selected_executable else None)
    if not exec_relative:
        return False, "No executable specified or detected."

    target_dir = get_opt_dir() / app_id
    ts_suffix = int(time.time() * 1000)
    staging_target = get_opt_dir() / f".{app_id}.new.{ts_suffix}"
    backup_dir = get_opt_dir() / f".{app_id}.bak.{ts_suffix}"
    folder_to_install = candidate.extracted_root or candidate.source_path

    try:
        if candidate.kind == "archive":
            if not candidate.extracted_root or not candidate.extracted_root.exists():
                return False, "Extracted staging directory is missing."
            shutil.move(str(candidate.extracted_root), str(staging_target))
            candidate.is_staged = False  # moved, no need to cleanup
        elif candidate.kind == "folder":
            shutil.copytree(str(folder_to_install), str(staging_target), symlinks=True)

        # Verify executable exists in staged copy BEFORE replacing old install
        staged_exec = staging_target / exec_relative
        if not staged_exec.exists():
            shutil.rmtree(staging_target, ignore_errors=True)
            return False, f"Executable not found at expected path: {exec_relative}"

        # Atomic swap: existing -> backup, staging -> target
        had_existing_dir = target_dir.exists()
        if had_existing_dir:
            os.rename(str(target_dir), str(backup_dir))
        try:
            os.rename(str(staging_target), str(target_dir))
        except Exception as swap_err:
            if had_existing_dir and backup_dir.exists() and not target_dir.exists():
                try:
                    os.rename(str(backup_dir), str(target_dir))
                except OSError:
                    pass
            shutil.rmtree(staging_target, ignore_errors=True)
            return False, f"Failed to swap application directory: {swap_err}"

        # Remove backup directory (non-fatal)
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
    except Exception as e:
        shutil.rmtree(staging_target, ignore_errors=True)
        return False, f"Failed to install application files: {e}"

    final_exec_path = target_dir / exec_relative
    # Ensure +x
    try:
        os.chmod(final_exec_path, os.stat(final_exec_path).st_mode | 0o755)
    except OSError:
        pass

    # Resolve icon (preserve custom/existing icon on update if requested)
    chosen_icon = icon_source
    kept_custom_icon = False
    if not chosen_icon and existing_entry and (preserve_existing_icons or existing_entry.get("custom_icon")):
        prev_icon = existing_entry.get("icon_path")
        if prev_icon and Path(prev_icon).exists():
            chosen_icon = Path(prev_icon)
            kept_custom_icon = bool(existing_entry.get("custom_icon"))
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

    # Index any existing companion launcher icons so custom companion icons are preserved on update
    existing_subs_by_id = {}
    if existing_entry:
        for old_sub in existing_entry.get("additional_launchers", []):
            if old_sub.get("app_id"):
                existing_subs_by_id[old_sub["app_id"]] = old_sub

    # Install any additional executables as companion launchers (e.g. NetLogo 3D)
    additional_launchers = []
    new_sub_ids = set()
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
            new_sub_ids.add(sub_id)

            old_sub = existing_subs_by_id.get(sub_id)
            sub_icon_path = None
            kept_sub_custom_icon = False
            if old_sub and (preserve_existing_icons or old_sub.get("custom_icon")):
                prev_sub_ic = old_sub.get("icon_path")
                if prev_sub_ic and Path(prev_sub_ic).exists():
                    sub_icon_path = Path(prev_sub_ic)
                    kept_sub_custom_icon = bool(old_sub.get("custom_icon"))

            if not sub_icon_path:
                # Match dedicated companion icon if available (e.g. NetLogo3D.png for NetLogo3D)
                sub_stem_clean = re.sub(r"[^a-z0-9]", "", sub_stem.lower())
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
            sub_entry = {
                "name": sub_name,
                "app_id": sub_id,
                "desktop_file": str(sub_desktop_file),
                "bin_symlink": str(sub_cli_link),
                "exec_path": str(sub_exec_path),
                "icon_path": str(sub_installed_icon)
            }
            if kept_sub_custom_icon:
                sub_entry["custom_icon"] = True
            additional_launchers.append(sub_entry)

    # Clean up stale companion launchers removed during an update
    if existing_entry:
        for old_sub_id, old_sub in existing_subs_by_id.items():
            if old_sub_id not in new_sub_ids:
                for path_key in ("desktop_file", "bin_symlink", "icon_path"):
                    old_p = old_sub.get(path_key)
                    if old_p and (Path(old_p).exists() or Path(old_p).is_symlink()):
                        try:
                            Path(old_p).unlink()
                        except OSError:
                            pass

    # Save manifest and refresh launcher
    entry_data = {
        "app_id": app_id,
        "name": final_name,
        "kind": candidate.kind,
        "version": candidate.version,
        "category": final_category,
        "use_no_sandbox": use_no_sandbox,
        "startup_wm_class": final_wm_class or app_id,
        "installed_dir": str(target_dir),
        "exec_path": str(final_exec_path),
        "desktop_file": str(desktop_file),
        "icon_path": str(installed_icon),
        "bin_symlink": str(cli_link),
        "additional_launchers": additional_launchers,
        "installed_at": original_installed_at or now_iso
    }
    if kept_custom_icon:
        entry_data["custom_icon"] = True
    if existing_update_source:
        entry_data["update_source"] = existing_update_source
    if is_update:
        entry_data["updated_at"] = now_iso

    manifest[app_id] = entry_data
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
                cleanup_note = " (Note: Original folder has root permissions and was preserved; you can safely delete it with sudo rm -rf if desired.)"
            except OSError:
                pass

    add_msg = f" (along with {len(additional_launchers)} companion launcher(s))" if additional_launchers else ""
    action_word = "Updated" if is_update else "Installed"
    return True, f"{action_word} {final_name}{add_msg} successfully into ~/.local/opt/{app_id}!{cleanup_note}"


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
    cleanup_portable_files(entry)
            
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
    is_custom = new_icon_source is not None
    if is_main:
        entry["icon_path"] = str(installed_icon)
        entry["custom_icon"] = is_custom
    else:
        found_in_manifest = False
        if "additional_launchers" not in entry:
            entry["additional_launchers"] = []
        for sub in entry["additional_launchers"]:
            if (target_sub_id and sub.get("app_id") == target_sub_id) or (desktop_file and sub.get("desktop_file") == str(desktop_file)):
                sub["icon_path"] = str(installed_icon)
                sub["custom_icon"] = is_custom
                found_in_manifest = True
                break
        if not found_in_manifest:
            entry["additional_launchers"].append({
                "name": target_name,
                "app_id": icon_id,
                "desktop_file": str(desktop_file) if desktop_file else "",
                "icon_path": str(installed_icon),
                "custom_icon": is_custom
            })

    save_manifest(manifest)
    refresh_desktop_database()

    return True, f"Updated icon for {target_name} successfully!", installed_icon


def find_matching_installed_app(candidate: AppCandidate) -> Optional[dict]:
    """
    Check if a newly inspected candidate matches an already installed app in manifest.json.
    Matches by app_id, Debian package_name, or sanitized display name.
    """
    manifest = load_manifest()
    if candidate.app_id in manifest:
        return manifest[candidate.app_id]

    for app_id, entry in manifest.items():
        if candidate.kind == "deb" and candidate.deb_package_name:
            if entry.get("package_name") == candidate.deb_package_name:
                return entry
        entry_name_slug = sanitize_app_id(entry.get("name", ""))
        if entry_name_slug and entry_name_slug == candidate.app_id:
            return entry

    return None


def parse_installed_settings(entry: dict) -> dict:
    """
    Recover category, use_no_sandbox, startup_wm_class, and executable relative path
    from a manifest entry, falling back to parsing its .desktop file for older entries.
    """
    settings = {
        "name": entry.get("name", entry.get("app_id", "")),
        "category": entry.get("category", ""),
        "use_no_sandbox": entry.get("use_no_sandbox"),
        "startup_wm_class": entry.get("startup_wm_class", ""),
        "exec_rel": None,
        "companion_rels": []
    }

    installed_dir = entry.get("installed_dir")
    exec_path = entry.get("exec_path")
    if installed_dir and exec_path:
        try:
            settings["exec_rel"] = str(Path(exec_path).relative_to(Path(installed_dir)))
        except ValueError:
            pass

        for sub in entry.get("additional_launchers", []):
            sub_exec = sub.get("exec_path")
            if sub_exec:
                try:
                    settings["companion_rels"].append(str(Path(sub_exec).relative_to(Path(installed_dir))))
                except ValueError:
                    pass

    desktop_file = entry.get("desktop_file")
    if desktop_file and Path(desktop_file).exists():
        try:
            for line in Path(desktop_file).read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Categories=") and not settings["category"]:
                    settings["category"] = line.split("=", 1)[1].strip()
                elif line.startswith("Exec=") and settings["use_no_sandbox"] is None:
                    settings["use_no_sandbox"] = "--no-sandbox" in line
                elif line.startswith("StartupWMClass=") and not settings["startup_wm_class"]:
                    settings["startup_wm_class"] = line.split("=", 1)[1].strip()
        except OSError:
            pass

    if settings["use_no_sandbox"] is None:
        settings["use_no_sandbox"] = False
    return settings


def update_installed_app(
    app_id: str,
    new_candidate: AppCandidate,
    preserve_settings: bool = True,
    move_original: bool = False
) -> Tuple[bool, str]:
    """
    Updates an existing installed application in-place with a newly inspected candidate.
    When preserve_settings=True, retains the user's custom name, custom icons, category,
    --no-sandbox flag, companion launcher choices, and update source configuration.
    """
    manifest = load_manifest()
    if app_id not in manifest:
        return False, f"App ID '{app_id}' not found in manifest."

    existing_entry = manifest[app_id]
    old_settings = parse_installed_settings(existing_entry)

    final_name = old_settings["name"] if preserve_settings and old_settings["name"] else new_candidate.name
    final_category = old_settings["category"] if preserve_settings and old_settings["category"] else new_candidate.categories
    final_no_sandbox = old_settings["use_no_sandbox"] if preserve_settings else new_candidate.is_electron
    final_wm_class = old_settings["startup_wm_class"] if preserve_settings and old_settings["startup_wm_class"] else new_candidate.startup_wm_class

    # Map existing primary executable relative path if it still exists in the new version
    exec_rel = None
    avail_rels = {c.relative_path for c in new_candidate.executables}
    if preserve_settings and old_settings["exec_rel"] and old_settings["exec_rel"] in avail_rels:
        exec_rel = old_settings["exec_rel"]
    elif new_candidate.selected_executable:
        exec_rel = new_candidate.selected_executable.relative_path

    # Map existing companion launchers onto new candidate executables
    additional_execs = []
    if preserve_settings and old_settings["companion_rels"]:
        for rel in old_settings["companion_rels"]:
            if rel in avail_rels and rel != exec_rel:
                additional_execs.append(rel)
    elif new_candidate.kind in ("archive", "folder"):
        for cand in new_candidate.executables:
            if cand.relative_path == exec_rel:
                continue
            is_script = cand.name.endswith(".sh") or cand.is_script
            is_headless = "headless" in cand.name.lower()
            if not is_script and not is_headless and any(k in cand.name.lower() for k in ("3d", "client", "search")):
                additional_execs.append(cand.relative_path)

    return install_candidate(
        new_candidate,
        name=final_name,
        category=final_category,
        executable_rel=exec_rel,
        icon_source=None,
        use_no_sandbox=final_no_sandbox,
        move_original=move_original,
        startup_wm_class=final_wm_class,
        additional_executables=additional_execs,
        target_app_id=app_id,
        preserve_existing_icons=preserve_settings
    )
