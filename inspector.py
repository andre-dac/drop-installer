"""
drop-installer inspector: analyzes dropped files and folders to detect app metadata,
executables, icons, and package configuration.
"""

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class CandidateExecutable:
    path: Path
    relative_path: str
    name: str
    score: int
    is_elf: bool
    is_script: bool


@dataclass
class CandidateIcon:
    path: Path
    relative_path: str
    format: str
    size: Optional[Tuple[int, int]]
    score: int


@dataclass
class AppCandidate:
    source_path: Path
    kind: str  # 'archive', 'folder', 'appimage', 'deb'
    app_id: str
    name: str
    version: str = "1.0"
    comment: str = ""
    categories: str = "Utility;"
    startup_wm_class: str = ""
    is_electron: bool = False
    
    # Working paths
    extracted_root: Optional[Path] = None  # None if kind == 'folder' or 'deb'
    is_staged: bool = False
    
    # Executable info
    executables: List[CandidateExecutable] = field(default_factory=list)
    selected_executable: Optional[CandidateExecutable] = None
    
    # Icon info
    icons: List[CandidateIcon] = field(default_factory=list)
    selected_icon: Optional[CandidateIcon] = None
    
    # DEB specific info
    deb_package_name: Optional[str] = None
    deb_maintainer: Optional[str] = None
    
    def cleanup_staging(self):
        """Clean up temporary extracted files if any."""
        if self.is_staged and self.extracted_root and self.extracted_root.exists():
            try:
                shutil.rmtree(self.extracted_root)
            except OSError:
                pass


import stat

MAX_SCAN_DEPTH = 3

NOISE_BINARY_NAMES = {
    "chrome-sandbox", "crashpad_handler", "minidump_stackwalk",
    "snapshot_blob.bin", "v8_context_snapshot.bin",
    "uninstall", "uninstall.sh", "uninstaller",
    "setup", "installer", "install", "install.sh", "update", "updater",
    "gdb", "lldb", "elevated", "helper",
    # Java runtime internal tools
    "java", "javac", "javadoc", "jcmd", "jdb", "jfr", "jinfo",
    "jmap", "jps", "jrunscript", "jstack", "jstat", "keytool",
    "rmiregistry", "serialver", "jpackage"
}

NOISE_EXTENSIONS = {
    ".so", ".a", ".o", ".node", ".dylib", ".dll", ".h", ".c", ".cpp",
    ".json", ".yaml", ".yml", ".md", ".txt", ".map", ".pak", ".dat"
}

SKIP_SCAN_EXTENSIONS = {
    ".nlogo", ".nlogo3d", ".jar", ".class", ".html", ".htm", ".css",
    ".js", ".ts", ".map", ".json", ".yaml", ".yml", ".md", ".txt",
    ".pdf", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".ico", ".xpm",
    ".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".7z", ".pak",
    ".dat", ".dylib", ".dll", ".xml", ".csv", ".properties"
}

PRUNE_SCAN_DIRS = {
    "node_modules", ".git", ".svn", ".hg", "__pycache__",
    "runtime", "jre", "jdk", "models", "docs", "doc", "man",
    "include", "locales", "locale", "translations"
}


def sanitize_app_id(name: str) -> str:
    """Generate a clean, valid slug for app_id and desktop entry."""
    slug = name.lower()
    # Replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    if not slug:
        slug = "custom-app"
    return slug


def is_executable_binary(file_path: Path) -> Tuple[bool, bool, bool]:
    """
    Returns (is_executable, is_elf, is_script).
    Uses fast-path filtering to avoid opening non-executable/data files.
    """
    ext = file_path.suffix.lower()
    name_lower = file_path.name.lower()

    # Skip files that are clearly data or libraries
    if ext in SKIP_SCAN_EXTENSIONS or ".so" in name_lower:
        return False, False, False

    try:
        stat_res = file_path.stat()
        mode = stat_res.st_mode
        # Must be a regular file (never open FIFOs/sockets/devices)
        if not stat.S_ISREG(mode):
            return False, False, False

        # If 0 bytes, not an executable
        if stat_res.st_size == 0 or stat_res.st_size > 500 * 1024 * 1024:
            return False, False, False

        is_mode_exec = bool(mode & 0o111)
        # Fast path: if not executable mode and has non-script extension, skip opening
        if not is_mode_exec and ext not in (".sh", ".py", ".bash", ""):
            return False, False, False

        # Read first 4 bytes safely
        with open(file_path, "rb") as f:
            header = f.read(4)

        is_elf = header.startswith(b"\x7fELF")
        is_script = header.startswith(b"#!")

        is_exec = is_mode_exec or is_elf or (is_script and ext in (".sh", ".py", ".bash", ""))
        return is_exec, is_elf, is_script
    except (OSError, PermissionError):
        return False, False, False


def parse_desktop_file(desktop_path: Path) -> dict:
    """Extract standard keys from an existing .desktop file."""
    info = {}
    try:
        with open(desktop_path, "r", encoding="utf-8", errors="ignore") as f:
            in_desktop_entry = False
            for line in f:
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_desktop_entry = True
                    continue
                elif line.startswith("[") and line.endswith("]"):
                    in_desktop_entry = False
                    continue
                if in_desktop_entry and "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key not in info:
                        info[key] = val
    except OSError:
        pass
    return info


def inspect_deb(deb_path: Path) -> AppCandidate:
    """Inspect a .deb file using dpkg-deb."""
    app_id = sanitize_app_id(deb_path.stem)
    name = deb_path.stem
    version = "1.0"
    comment = ""
    maintainer = ""
    
    try:
        res = subprocess.run(
            ["dpkg-deb", "-f", str(deb_path), "Package", "Version", "Description", "Maintainer", "Section"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        fields = {}
        current_key = None
        for line in res.stdout.splitlines():
            if line.startswith(" ") and current_key:
                fields[current_key] += " " + line.strip()
            elif ":" in line:
                key, val = line.split(":", 1)
                current_key = key.strip()
                fields[current_key] = val.strip()
                
        pkg_name = fields.get("Package", deb_path.stem)
        name = pkg_name.replace("-", " ").title()
        app_id = sanitize_app_id(pkg_name)
        version = fields.get("Version", "1.0")
        comment = fields.get("Description", f"Debian package {pkg_name}")
        maintainer = fields.get("Maintainer", "")
    except Exception:
        pass
        
    candidate = AppCandidate(
        source_path=deb_path,
        kind="deb",
        app_id=app_id,
        name=name,
        version=version,
        comment=comment,
        deb_package_name=pkg_name if 'pkg_name' in locals() else app_id,
        deb_maintainer=maintainer
    )
    return candidate


def extract_appimage_metadata(appimage_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Attempt to extract .desktop and icon from AppImage using --appimage-extract.
    Returns (desktop_file, icon_file) from a temp extraction directory.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="appimage_ext_"))
    try:
        # Make sure source is executable
        os.chmod(appimage_path, os.stat(appimage_path).st_mode | 0o755)
        # Run extraction for desktop and icons
        cmd = [str(appimage_path), "--appimage-extract"]
        # Run inside temp_dir
        subprocess.run(
            cmd,
            cwd=temp_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False
        )
        squash_root = temp_dir / "squashfs-root"
        if not squash_root.exists():
            return None, None
            
        desktop_file = None
        icon_file = None
        
        # Look for .desktop
        for f in squash_root.glob("*.desktop"):
            desktop_file = f
            break
            
        # Look for .DirIcon or app image icon
        dir_icon = squash_root / ".DirIcon"
        if dir_icon.exists() and dir_icon.is_file():
            icon_file = dir_icon
        else:
            for f in squash_root.glob("*.png"):
                icon_file = f
                break
            if not icon_file:
                for f in squash_root.glob("*.svg"):
                    icon_file = f
                    break
                    
        return desktop_file, icon_file
    except Exception:
        return None, None


def score_executable(file_path: Path, root_dir: Path, target_name: str, desktop_exec: Optional[str] = None) -> int:
    """Calculate heuristic suitability score for an executable file."""
    name_lower = file_path.name.lower()
    
    # Filter noise
    if name_lower in NOISE_BINARY_NAMES:
        return -100
    if file_path.suffix.lower() in NOISE_EXTENSIONS or ".so" in name_lower:
        return -100
    if "chrome-sandbox" in name_lower or "crashpad" in name_lower:
        return -100
        
    score = 10
    
    # Bonus if name matches target archive/folder name
    target_clean = re.sub(r"[^a-z0-9]", "", target_name.lower())
    name_clean = re.sub(r"[^a-z0-9]", "", name_lower)
    
    if target_clean and (target_clean == name_clean or target_clean in name_clean or name_clean in target_clean):
        score += 60
        
    # Bonus if matches .desktop Exec line
    if desktop_exec:
        parts = desktop_exec.split()
        if parts:
            exec_base = Path(parts[0]).name.lower()
            if exec_base == name_lower:
                score += 100
            
    # Proximity & location bonus
    parent_name = file_path.parent.name.lower()
    rel = file_path.relative_to(root_dir)
    depth = len(rel.parts) - 1

    if parent_name in ("bin", "usr", "app"):
        score += 35
    elif depth == 0:
        score += 30
    else:
        score -= (depth * 5)
        
    # Standard launch script names
    if name_lower in ("run", "run.sh", "start", "start.sh", "launch", "launch.sh"):
        score += 20
        
    # Penalize headless scripts
    if "headless" in name_lower:
        score -= 35
        
    return score


def format_executable_display_name(raw_name: str, base_app_name: Optional[str] = None) -> str:
    """Format names like NetLogo3D -> NetLogo 3D, HubNetClient -> HubNet Client."""
    name = raw_name
    if name.endswith(".sh"):
        name = name[:-3]

    if base_app_name and name.lower().startswith(base_app_name.lower()):
        remainder = name[len(base_app_name):].strip()
        remainder = re.sub(r"([a-zA-Z])(\d+)", r"\1 \2", remainder)
        remainder = re.sub(r"([a-z])([A-Z])", r"\1 \2", remainder)
        remainder = remainder.replace("-", " ").replace("_", " ").strip()
        if remainder:
            return f"{base_app_name} {remainder}".strip()
        return base_app_name

    # If already contains spaces, don't over-split camelCase words unless followed by numbers
    if " " in name:
        name = re.sub(r"([a-zA-Z])(\d+)", r"\1 \2", name)
        return re.sub(r"\s+", " ", name).strip()

    # Insert space before numbers if preceded by letter (e.g. NetLogo3D -> NetLogo 3D)
    name = re.sub(r"([a-zA-Z])(\d+)", r"\1 \2", name)
    # Insert space between lowercase and uppercase letter (e.g. HubNetClient -> HubNet Client)
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return name


def score_icon(file_path: Path, root_dir: Path, target_name: str, desktop_icon: Optional[str] = None) -> int:
    """Calculate suitability score for an icon candidate."""
    name_lower = file_path.name.lower()
    suffix = file_path.suffix.lower()
    
    # Exclude non-icon or irrelevant files
    if suffix not in (".svg", ".png", ".xpm", ".ico"):
        return -100
    if "node_modules" in file_path.parts or ".git" in file_path.parts:
        return -100
        
    score = 10
    if suffix == ".svg":
        score += 35
    elif suffix == ".png":
        score += 25
        
    # Bonus for name matching target
    target_clean = re.sub(r"[^a-z0-9]", "", target_name.lower())
    name_clean = re.sub(r"[^a-z0-9]", "", file_path.stem.lower())
    if target_clean and (target_clean == name_clean or target_clean in name_clean):
        score += 40
    elif name_clean in ("icon", "logo", "app", "default", "application"):
        score += 30
        
    if desktop_icon:
        d_icon = Path(desktop_icon).stem.lower()
        if d_icon == file_path.stem.lower():
            score += 60
            
    # Search for resolution in path e.g. 512x512, 256x256
    rel_str = str(file_path.relative_to(root_dir)).lower()
    if "512x512" in rel_str:
        score += 30
    elif "256x256" in rel_str:
        score += 25
    elif "128x128" in rel_str:
        score += 20
    elif "scalable" in rel_str:
        score += 30
    elif "16x16" in rel_str or "24x24" in rel_str or "32x32" in rel_str:
        score -= 10  # too small
        
    # Proximity bonus
    depth = len(file_path.relative_to(root_dir).parts) - 1
    if depth <= 2:
        score += 15
        
    return score


def scan_directory(root_dir: Path, target_name: str) -> Tuple[List[CandidateExecutable], List[CandidateIcon], dict, bool]:
    """
    Scans a directory for executables, icons, existing desktop files, and Electron signatures.
    Returns (executables, icons, desktop_metadata, is_electron).
    """
    desktop_meta = {}
    is_electron = False
    
    # Check for Electron signatures
    if (root_dir / "chrome-sandbox").exists() or \
       (root_dir / "resources" / "app.asar").exists() or \
       (root_dir / "resources" / "app").exists() or \
       (root_dir / "v8_context_snapshot.bin").exists():
        is_electron = True
        
    executables: List[CandidateExecutable] = []
    icons: List[CandidateIcon] = []
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        current_dir = Path(dirpath)
        try:
            rel = current_dir.relative_to(root_dir)
            rel_depth = len(rel.parts)
        except ValueError:
            rel_depth = 0

        if rel_depth >= MAX_SCAN_DEPTH:
            dirnames.clear()
            continue

        # Prune hidden dirs and heavy non-binary subdirectories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in PRUNE_SCAN_DIRS
        ]

        # Check for desktop file in current directory if not found yet
        if not desktop_meta:
            for fname in filenames:
                if fname.lower().endswith(".desktop"):
                    d_path = current_dir / fname
                    desktop_meta = parse_desktop_file(d_path)
                    if desktop_meta:
                        break

        desktop_exec = desktop_meta.get("Exec")
        desktop_icon = desktop_meta.get("Icon")
        
        for fname in filenames:
            fpath = current_dir / fname
            if fpath.is_symlink():
                continue
                
            # Check for icon
            ext = fpath.suffix.lower()
            if ext in (".svg", ".png", ".xpm", ".ico"):
                score = score_icon(fpath, root_dir, target_name, desktop_icon)
                if score > 0:
                    icons.append(CandidateIcon(
                        path=fpath,
                        relative_path=str(fpath.relative_to(root_dir)),
                        format=ext.lstrip("."),
                        size=None,
                        score=score
                    ))
                    
            # Check for executable
            is_exec, is_elf, is_script = is_executable_binary(fpath)
            if is_exec:
                score = score_executable(fpath, root_dir, target_name, desktop_exec)
                if score > 0:
                    executables.append(CandidateExecutable(
                        path=fpath,
                        relative_path=str(fpath.relative_to(root_dir)),
                        name=fname,
                        score=score,
                        is_elf=is_elf,
                        is_script=is_script
                    ))
                    
    # Sort by score descending
    executables.sort(key=lambda x: x.score, reverse=True)
    icons.sort(key=lambda x: x.score, reverse=True)
    
    return executables, icons, desktop_meta, is_electron


def unwrap_single_folder(staging_dir: Path) -> Path:
    """If the directory contains only a single subfolder (or one main subfolder), unwrap it."""
    entries = [p for p in staging_dir.iterdir() if not p.name.startswith(".")]
    dirs = [p for p in entries if p.is_dir()]
    if len(dirs) == 1 and len(entries) == 1:
        if dirs[0].name.lower() in ("bin", "usr", "lib", "share", "etc", "opt", "resources", "app"):
            return staging_dir
        return dirs[0]
    return staging_dir


def extract_archive(archive_path: Path) -> Path:
    """Extracts a compressed archive to a staging directory and returns the root path."""
    staging_dir = Path(tempfile.mkdtemp(prefix="dropinstall_stage_"))
    
    lower_name = archive_path.name.lower()
    if lower_name.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar")):
        with tarfile.open(archive_path, "r:*") as tar:
            try:
                tar.extractall(staging_dir, filter="tar")
            except Exception:
                tar.extractall(staging_dir)
    elif lower_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as z:
            z.extractall(staging_dir)
    else:
        # Fallback to tar
        shutil.unpack_archive(str(archive_path), str(staging_dir))
        
    return unwrap_single_folder(staging_dir)


def infer_app_name(raw_name: str) -> str:
    """Transforms a filename like 'arduino-ide_2.3.2_Linux_64bit' into 'Arduino IDE'."""
    # Strip common suffixes and architectures
    cleaned = raw_name
    for ext in (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".txz", ".zip", ".appimage", ".deb", ".tar"):
        if cleaned.lower().endswith(ext):
            cleaned = cleaned[:-len(ext)]
            break
            
    # Strip Linux architecture tags
    cleaned = re.sub(r"[-_](linux|x86_64|x64|amd64|64bit|32bit|arm64|aarch64|bin)", "", cleaned, flags=re.IGNORECASE)
    # Strip version numbers like _2.3.2 or -1.0.0 or -7.0.4
    cleaned = re.sub(r"[-_]v?\d+(\.\d+)*.*$", "", cleaned)
    
    # Replace separators with spaces
    cleaned = cleaned.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        cleaned = raw_name

    # If the string already has mixed case (e.g. NetLogo, FreeCAD), preserve inner capitals
    if any(c.isupper() for c in cleaned) and any(c.islower() for c in cleaned):
        words = cleaned.split()
        return " ".join(w[0].upper() + w[1:] if len(w) > 1 else w.upper() for w in words)

    return cleaned.title()


def inspect_target(path: Path) -> AppCandidate:
    """
    Main inspection function: examines the target path (archive, folder, AppImage, deb)
    and returns an AppCandidate with ranked executables and icons.
    """
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Target path does not exist: {path}")
        
    name_lower = path.name.lower()
    
    # 1. DEB Package
    if path.is_file() and name_lower.endswith(".deb"):
        return inspect_deb(path)
        
    # 2. AppImage
    if path.is_file() and name_lower.endswith(".appimage"):
        app_name = infer_app_name(path.stem)
        app_id = sanitize_app_id(app_name)
        desktop_file, icon_file = extract_appimage_metadata(path)
        
        icons = []
        desktop_meta = {}
        if desktop_file and desktop_file.exists():
            desktop_meta = parse_desktop_file(desktop_file)
            if "Name" in desktop_meta:
                app_name = desktop_meta["Name"]
                app_id = sanitize_app_id(app_name)
                
        if icon_file and icon_file.exists():
            icons.append(CandidateIcon(
                path=icon_file,
                relative_path=icon_file.name,
                format=icon_file.suffix.lstrip("."),
                size=None,
                score=100
            ))
            
        exec_cand = CandidateExecutable(
            path=path,
            relative_path=path.name,
            name=path.name,
            score=100,
            is_elf=True,
            is_script=False
        )
        
        return AppCandidate(
            source_path=path,
            kind="appimage",
            app_id=app_id,
            name=app_name,
            version=desktop_meta.get("X-AppImage-Version", "1.0"),
            comment=desktop_meta.get("Comment", f"{app_name} AppImage"),
            categories=desktop_meta.get("Categories", "Utility;"),
            startup_wm_class=desktop_meta.get("StartupWMClass", app_id),
            is_electron=True,  # Most modern AppImages (like Arduino IDE 2) are Electron
            executables=[exec_cand],
            selected_executable=exec_cand,
            icons=icons,
            selected_icon=icons[0] if icons else None
        )
        
    # 3. Compressed Archive
    if path.is_file() and name_lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".zip", ".tar")):
        extracted_root = extract_archive(path)
        app_name = infer_app_name(path.stem)
        app_id = sanitize_app_id(app_name)
        
        executables, icons, desktop_meta, is_electron = scan_directory(extracted_root, app_name)
        
        # Override with desktop metadata if available
        if "Name" in desktop_meta:
            app_name = desktop_meta["Name"]
            app_id = sanitize_app_id(app_name)
            
        wm_class = desktop_meta.get("StartupWMClass", app_id)
        category = desktop_meta.get("Categories", "Development;Utility;" if any(k in app_id for k in ("arduino", "netlogo", "code", "ide")) else "Utility;")
        comment = desktop_meta.get("Comment", f"{app_name} application")
        
        candidate = AppCandidate(
            source_path=path,
            kind="archive",
            app_id=app_id,
            name=app_name,
            version=desktop_meta.get("Version", "1.0"),
            comment=comment,
            categories=category,
            startup_wm_class=wm_class,
            is_electron=is_electron,
            extracted_root=extracted_root,
            is_staged=True,
            executables=executables,
            selected_executable=executables[0] if executables else None,
            icons=icons,
            selected_icon=icons[0] if icons else None
        )
        return candidate
        
    # 4. Existing Folder (e.g. unpacked portable application directory)
    if path.is_dir():
        effective_path = unwrap_single_folder(path)
        app_name = infer_app_name(effective_path.name if effective_path != path else path.name)
        app_id = sanitize_app_id(app_name)
        
        executables, icons, desktop_meta, is_electron = scan_directory(effective_path, app_name)
        
        if "Name" in desktop_meta:
            app_name = desktop_meta["Name"]
            app_id = sanitize_app_id(app_name)
            
        wm_class = desktop_meta.get("StartupWMClass", app_id)
        category = desktop_meta.get("Categories", "Development;Utility;" if any(k in app_id for k in ("arduino", "netlogo", "code", "ide")) else "Utility;")
        comment = desktop_meta.get("Comment", f"{app_name} application")
        
        candidate = AppCandidate(
            source_path=path,
            kind="folder",
            app_id=app_id,
            name=app_name,
            version=desktop_meta.get("Version", "1.0"),
            comment=comment,
            categories=category,
            startup_wm_class=wm_class,
            is_electron=is_electron,
            extracted_root=effective_path,
            is_staged=False,
            executables=executables,
            selected_executable=executables[0] if executables else None,
            icons=icons,
            selected_icon=icons[0] if icons else None
        )
        return candidate
        
    raise ValueError(f"Unsupported file format: {path.name}")
