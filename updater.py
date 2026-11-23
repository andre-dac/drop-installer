"""
drop-installer updater: checks for and applies updates for installed applications
from GitHub Releases, direct download URLs, or system APT repositories.
"""

import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from inspector import infer_app_version, inspect_target, sanitize_app_id
from installer import (
    cleanup_portable_files,
    load_manifest,
    refresh_desktop_database,
    save_manifest,
    update_installed_app,
)

USER_AGENT = "Drop-Installer/1.0 (+https://github.com/andre-dac/drop-installer)"
VALID_APT_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
SUPPORTED_ARCHIVE_EXTS = (".appimage", ".deb", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".zip")


@dataclass
class UpdateCheckResult:
    app_id: str
    has_update: bool
    current_version: str
    latest_version: Optional[str] = None
    source_type: str = "none"  # 'github', 'url', 'apt', 'none'
    download_url: Optional[str] = None
    asset_name: Optional[str] = None
    message: str = ""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    error: Optional[str] = None


def normalize_github_repo(raw: str) -> str:
    """Normalize 'https://github.com/owner/repo.git' or 'owner/repo' into 'owner/repo'."""
    val = raw.strip().rstrip("/")
    val = re.sub(r"^https?://(www\.)?github\.com/", "", val, flags=re.IGNORECASE)
    if val.endswith(".git"):
        val = val[:-4]
    parts = [p for p in val.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return val


def _version_tokens(ver: str) -> List[Tuple[int, object]]:
    """Convert a version string into comparable (is_alpha, value) tokens."""
    cleaned = (ver or "").strip().lstrip("vV")
    tokens = []
    for part in re.findall(r"\d+|[a-zA-Z]+", cleaned):
        if part.isdigit():
            tokens.append((1, int(part)))
        else:
            tokens.append((0, part.lower()))
    return tokens


def is_newer_version(current: Optional[str], latest: Optional[str]) -> bool:
    """Return True if `latest` represents a strictly newer version than `current`."""
    if not latest or latest in ("(none)", "unknown"):
        return False
    if not current or current in ("(none)", "unknown"):
        return True
    c_clean = current.strip().lstrip("vV")
    l_clean = latest.strip().lstrip("vV")
    if c_clean == l_clean:
        return False

    c_toks = _version_tokens(c_clean)
    l_toks = _version_tokens(l_clean)
    max_len = max(len(c_toks), len(l_toks))
    for i in range(max_len):
        c_val = c_toks[i] if i < len(c_toks) else (1, 0)
        l_val = l_toks[i] if i < len(l_toks) else (1, 0)
        if c_val == l_val:
            continue
        if c_val[0] == l_val[0]:
            return l_val[1] > c_val[1]
        # Numeric release > prerelease string tag (e.g. 1.0.0 > 1.0.0-beta)
        return l_val[0] > c_val[0]
    return False


def get_system_arch() -> str:
    """Return normalized CPU architecture ('x86_64' or 'arm64')."""
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "x86_64"


def select_best_github_asset(
    assets: List[dict],
    asset_pattern: Optional[str] = None,
    preferred_kind: Optional[str] = None
) -> Optional[dict]:
    """
    Choose the most suitable Linux release asset from a GitHub release assets list.
    """
    arch = get_system_arch()
    best_asset = None
    best_score = -1000

    for asset in assets:
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if not name or not url:
            continue
        name_lower = name.lower()

        # If user specified an explicit glob pattern, enforce it
        if asset_pattern and asset_pattern.strip():
            if not fnmatch.fnmatch(name_lower, asset_pattern.strip().lower()):
                continue

        # Must be a supported installable format
        if not any(name_lower.endswith(ext) for ext in SUPPORTED_ARCHIVE_EXTS):
            continue

        # Exclude non-Linux / checksum / signature artifacts
        if any(bad in name_lower for bad in (".sha256", ".sha512", ".sig", ".asc", ".zsync", ".blockmap", "windows", "win32", "win64", "macos", "darwin", "osx", "apple", ".dmg", ".exe", ".msi")):
            continue

        score = 10

        # Architecture match
        if arch == "x86_64":
            if any(a in name_lower for a in ("x86_64", "amd64", "x64", "64bit", "64-bit")):
                score += 40
            elif any(a in name_lower for a in ("arm64", "aarch64", "armv7", "armhf", "i386", "i686", "32bit")):
                score -= 100
        elif arch == "arm64":
            if any(a in name_lower for a in ("arm64", "aarch64")):
                score += 40
            elif any(a in name_lower for a in ("x86_64", "amd64", "x64", "i386", "i686")):
                score -= 100

        if "linux" in name_lower:
            score += 20

        # Prefer matching the currently installed format
        if preferred_kind == "appimage" and name_lower.endswith(".appimage"):
            score += 35
        elif preferred_kind == "deb" and name_lower.endswith(".deb"):
            score += 35
        elif preferred_kind in ("archive", "folder") and name_lower.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".zip")):
            score += 30
        elif name_lower.endswith(".appimage"):
            score += 15
        elif name_lower.endswith((".tar.gz", ".tar.xz")):
            score += 12
        elif name_lower.endswith(".deb"):
            score += 10

        if score > best_score:
            best_score = score
            best_asset = asset

    return best_asset if best_score > -50 else None


def detect_apt_package(app_id: str, app_name: str = "") -> Optional[Dict[str, str]]:
    """
    Check if a matching APT package exists in configured system repositories via `apt-cache policy`.
    Returns {'package_name', 'installed', 'candidate'} or None if unavailable.
    """
    candidates = []
    for raw in (app_id, sanitize_app_id(app_name) if app_name else ""):
        if raw and VALID_APT_PKG_RE.match(raw) and raw not in candidates:
            candidates.append(raw)

    for pkg in candidates:
        try:
            res = subprocess.run(
                ["apt-cache", "policy", pkg],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6,
                check=False
            )
            if res.returncode != 0 or not res.stdout:
                continue
            installed_ver = "(none)"
            candidate_ver = "(none)"
            for line in res.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("Installed:"):
                    installed_ver = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("Candidate:"):
                    candidate_ver = stripped.split(":", 1)[1].strip()
            if candidate_ver and candidate_ver != "(none)":
                return {
                    "package_name": pkg,
                    "installed": installed_ver,
                    "candidate": candidate_ver
                }
        except Exception:
            continue

    return None


def set_update_source(app_id: str, source: Optional[dict]) -> Tuple[bool, str]:
    """
    Save or clear an application's online update source configuration in manifest.json.
    Supported source dicts:
      - {"type": "github", "repo": "owner/repo", "asset_pattern": "*.AppImage"}
      - {"type": "url", "url": "https://example.com/app.tar.gz"}
      - {"type": "apt", "package_name": "my-app"}
      - None or {"type": "none"} to clear
    """
    manifest = load_manifest()
    if app_id not in manifest:
        return False, f"App '{app_id}' not found in manifest."

    if not source or source.get("type") in (None, "", "none"):
        manifest[app_id].pop("update_source", None)
        save_manifest(manifest)
        return True, "Cleared update source."

    src_type = source.get("type")
    cleaned: Dict[str, str] = {"type": src_type}

    if src_type == "github":
        repo = normalize_github_repo(source.get("repo", ""))
        if not repo or "/" not in repo:
            return False, "GitHub repository must be in 'owner/repo' format (e.g. arduino/arduino-ide)."
        cleaned["repo"] = repo
        pattern = (source.get("asset_pattern") or "").strip()
        if pattern:
            cleaned["asset_pattern"] = pattern
    elif src_type == "url":
        url = (source.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return False, "Download URL must start with http:// or https://."
        cleaned["url"] = url
        # Preserve cached ETag / Last-Modified if URL unchanged
        prev = manifest[app_id].get("update_source") or {}
        if prev.get("type") == "url" and prev.get("url") == url:
            if prev.get("etag"):
                cleaned["etag"] = prev["etag"]
            if prev.get("last_modified"):
                cleaned["last_modified"] = prev["last_modified"]
    elif src_type == "apt":
        pkg = (source.get("package_name") or "").strip()
        if not pkg or not VALID_APT_PKG_RE.match(pkg):
            return False, f"Invalid APT package name: '{pkg}'."
        cleaned["package_name"] = pkg
    else:
        return False, f"Unsupported update source type: {src_type}"

    manifest[app_id]["update_source"] = cleaned
    save_manifest(manifest)
    return True, "Saved update source."


def _extract_filename_from_headers(headers, final_url: str) -> str:
    """Derive a clean filename from Content-Disposition header or final URL."""
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', cd, flags=re.IGNORECASE)
    if m:
        return Path(urllib.parse.unquote(m.group(1).strip())).name
    parsed = urllib.parse.urlparse(final_url)
    name = Path(urllib.parse.unquote(parsed.path)).name
    return name or "download"


def check_for_update(app_id: str) -> UpdateCheckResult:
    """
    Check if a newer version is available for `app_id` using its configured update source
    (or APT package info for .deb installs).
    """
    manifest = load_manifest()
    entry = manifest.get(app_id)
    if not entry:
        return UpdateCheckResult(
            app_id=app_id,
            has_update=False,
            current_version="",
            error=f"App '{app_id}' not found."
        )

    current_ver = entry.get("version", "1.0")
    source = entry.get("update_source")
    if not source and entry.get("kind") == "deb":
        source = {"type": "apt", "package_name": entry.get("package_name") or app_id}

    if not source or source.get("type") in (None, "", "none"):
        return UpdateCheckResult(
            app_id=app_id,
            has_update=False,
            current_version=current_ver,
            source_type="none",
            message="No online update source configured."
        )

    src_type = source.get("type")

    # 1. APT Package Check
    if src_type == "apt":
        pkg = source.get("package_name") or entry.get("package_name") or app_id
        apt_info = detect_apt_package(pkg)
        if not apt_info:
            return UpdateCheckResult(
                app_id=app_id,
                has_update=False,
                current_version=current_ver,
                source_type="apt",
                error=f"APT package '{pkg}' was not found in configured repositories."
            )
        installed_ver = apt_info["installed"]
        candidate_ver = apt_info["candidate"]

        # Portable install that has an official APT repo available
        if installed_ver == "(none)":
            return UpdateCheckResult(
                app_id=app_id,
                has_update=True,
                current_version=current_ver,
                latest_version=candidate_ver,
                source_type="apt",
                message=f"Available via APT (v{candidate_ver}). Updating will migrate from portable folder to system APT package."
            )

        has_upd = is_newer_version(installed_ver, candidate_ver)
        msg = (
            f"APT update available: {installed_ver} → {candidate_ver}"
            if has_upd
            else f"Up to date via APT ({installed_ver})"
        )
        return UpdateCheckResult(
            app_id=app_id,
            has_update=has_upd,
            current_version=installed_ver,
            latest_version=candidate_ver,
            source_type="apt",
            message=msg
        )

    # 2. GitHub Releases Check
    if src_type == "github":
        repo = normalize_github_repo(source.get("repo", ""))
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return UpdateCheckResult(
                app_id=app_id,
                has_update=False,
                current_version=current_ver,
                source_type="github",
                error=f"GitHub API error (HTTP {e.code}) for '{repo}'."
            )
        except Exception as e:
            return UpdateCheckResult(
                app_id=app_id,
                has_update=False,
                current_version=current_ver,
                source_type="github",
                error=f"Failed to contact GitHub: {e}"
            )

        tag_name = (data.get("tag_name") or "").strip()
        latest_ver = tag_name.lstrip("vV") or infer_app_version(data.get("name", ""))
        assets = data.get("assets", [])
        chosen_asset = select_best_github_asset(
            assets,
            asset_pattern=source.get("asset_pattern"),
            preferred_kind=entry.get("kind")
        )
        if not chosen_asset:
            return UpdateCheckResult(
                app_id=app_id,
                has_update=False,
                current_version=current_ver,
                latest_version=latest_ver,
                source_type="github",
                error=f"Release {tag_name} found, but no matching Linux asset (.AppImage, .tar.gz, .zip, .deb) was found."
            )

        has_upd = is_newer_version(current_ver, latest_ver)
        msg = (
            f"Update available: v{current_ver} → v{latest_ver} ({chosen_asset['name']})"
            if has_upd
            else f"Up to date (v{latest_ver})"
        )
        return UpdateCheckResult(
            app_id=app_id,
            has_update=has_upd,
            current_version=current_ver,
            latest_version=latest_ver,
            source_type="github",
            download_url=chosen_asset["browser_download_url"],
            asset_name=chosen_asset["name"],
            message=msg
        )

    # 3. Direct Download URL Check
    if src_type == "url":
        url = source.get("url", "")
        headers = None
        final_url = url
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=12) as resp:
                headers = resp.headers
                final_url = resp.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (403, 405):
                # Server does not allow HEAD; try lightweight GET and read only headers
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        headers = resp.headers
                        final_url = resp.geturl()
                except Exception as get_err:
                    return UpdateCheckResult(
                        app_id=app_id,
                        has_update=False,
                        current_version=current_ver,
                        source_type="url",
                        error=f"Failed to check URL: {get_err}"
                    )
            else:
                return UpdateCheckResult(
                    app_id=app_id,
                    has_update=False,
                    current_version=current_ver,
                    source_type="url",
                    error=f"HTTP {e.code} checking download URL."
                )
        except Exception as e:
            return UpdateCheckResult(
                app_id=app_id,
                has_update=False,
                current_version=current_ver,
                source_type="url",
                error=f"Failed to check URL: {e}"
            )

        etag = headers.get("ETag")
        last_mod = headers.get("Last-Modified")
        filename = _extract_filename_from_headers(headers, final_url)
        inferred_ver = infer_app_version(filename)

        prev_etag = source.get("etag")
        prev_last_mod = source.get("last_modified")

        has_upd = False
        if prev_etag and etag and etag != prev_etag:
            has_upd = True
            msg = f"Newer file detected on server ({filename})."
        elif prev_last_mod and last_mod and last_mod != prev_last_mod:
            has_upd = True
            msg = f"Updated file available ({last_mod})."
        elif inferred_ver != "1.0" and is_newer_version(current_ver, inferred_ver):
            has_upd = True
            msg = f"Update available: v{current_ver} → v{inferred_ver} ({filename})"
        elif not prev_etag and not prev_last_mod:
            # Never downloaded through Drop Installer yet; ready to fetch
            has_upd = True
            msg = f"Ready to download ({filename})."
        else:
            msg = f"Up to date ({filename})."

        return UpdateCheckResult(
            app_id=app_id,
            has_update=has_upd,
            current_version=current_ver,
            latest_version=inferred_ver if inferred_ver != "1.0" else None,
            source_type="url",
            download_url=final_url,
            asset_name=filename,
            message=msg,
            etag=etag,
            last_modified=last_mod
        )

    return UpdateCheckResult(
        app_id=app_id,
        has_update=False,
        current_version=current_ver,
        source_type=src_type,
        error=f"Unknown update source type: {src_type}"
    )


def check_all_updates() -> Dict[str, UpdateCheckResult]:
    """Check updates for all apps that have an update source or are .deb packages."""
    manifest = load_manifest()
    results: Dict[str, UpdateCheckResult] = {}
    for app_id, entry in manifest.items():
        if entry.get("update_source") or entry.get("kind") == "deb":
            results[app_id] = check_for_update(app_id)
    return results


def _download_file(
    url: str,
    dest_dir: Path,
    preferred_name: Optional[str] = None,
    progress_cb: Optional[Callable[[Optional[float], str], None]] = None
) -> Tuple[Path, Optional[str], Optional[str]]:
    """
    Stream-download `url` into `dest_dir` with progress callbacks.
    Returns (downloaded_path, etag, last_modified).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        final_url = resp.geturl()
        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")
        total_len_str = resp.headers.get("Content-Length")
        total_bytes = int(total_len_str) if total_len_str and total_len_str.isdigit() else 0

        filename = preferred_name or _extract_filename_from_headers(resp.headers, final_url)
        # Ensure safe basename
        filename = Path(filename).name
        dest_path = dest_dir / filename

        downloaded = 0
        chunk_size = 64 * 1024
        with open(dest_path, "wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    if total_bytes > 0:
                        frac = min(1.0, downloaded / total_bytes)
                        mb_done = downloaded / (1024 * 1024)
                        mb_tot = total_bytes / (1024 * 1024)
                        progress_cb(frac, f"Downloading {filename} ({mb_done:.1f} / {mb_tot:.1f} MB)…")
                    else:
                        mb_done = downloaded / (1024 * 1024)
                        progress_cb(None, f"Downloading {filename} ({mb_done:.1f} MB)…")

    return dest_path, etag, last_mod


def _apply_apt_update(
    app_id: str,
    pkg_name: str,
    progress_cb: Optional[Callable[[Optional[float], str], None]] = None
) -> Tuple[bool, str]:
    """Refresh APT cache and install/upgrade `pkg_name` via pkexec, migrating portable installs cleanly."""
    if not VALID_APT_PKG_RE.match(pkg_name):
        return False, f"Invalid APT package name: '{pkg_name}'."

    if progress_cb:
        progress_cb(None, f"Refreshing APT repositories and updating '{pkg_name}'… (authentication required)")

    cmd = [
        "pkexec",
        "sh",
        "-c",
        'apt-get update && apt-get install -y -- "$1"',
        "_",
        pkg_name
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            err_text = (res.stderr or res.stdout or "").strip()
            return False, f"APT update failed (code {res.returncode}):\n{err_text}"
    except Exception as e:
        return False, f"Failed to run APT update: {e}"

    # Query newly installed version
    new_ver = "1.0"
    try:
        ver_res = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", pkg_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if ver_res.returncode == 0 and ver_res.stdout.strip():
            new_ver = ver_res.stdout.strip()
    except Exception:
        pass

    manifest = load_manifest()
    existing = manifest.get(app_id, {})
    was_portable = existing.get("kind") and existing.get("kind") != "deb"
    if was_portable:
        cleanup_portable_files(existing)

    now_iso = datetime.now().isoformat()
    manifest[app_id] = {
        "app_id": app_id,
        "name": existing.get("name", app_id.replace("-", " ").title()),
        "kind": "deb",
        "version": new_ver,
        "package_name": pkg_name,
        "update_source": {"type": "apt", "package_name": pkg_name},
        "installed_at": existing.get("installed_at", now_iso),
        "updated_at": now_iso
    }
    save_manifest(manifest)
    refresh_desktop_database()

    if was_portable:
        return True, f"Migrated '{manifest[app_id]['name']}' to system APT package '{pkg_name}' (v{new_ver}) and cleaned up old portable files. System updates will now keep it up to date automatically!"
    return True, f"Updated '{manifest[app_id]['name']}' via APT to v{new_ver}!"


def apply_online_update(
    app_id: str,
    check_result: Optional[UpdateCheckResult] = None,
    preserve_settings: bool = True,
    progress_cb: Optional[Callable[[Optional[float], str], None]] = None
) -> Tuple[bool, str]:
    """
    Download and apply an update for `app_id` from its configured online source
    (GitHub Releases, direct URL, or APT).
    """
    manifest = load_manifest()
    entry = manifest.get(app_id)
    if not entry:
        return False, f"App '{app_id}' not found in manifest."

    source = entry.get("update_source")
    if not source and entry.get("kind") == "deb":
        source = {"type": "apt", "package_name": entry.get("package_name") or app_id}

    if not source or source.get("type") in (None, "", "none"):
        return False, "No update source configured for this application."

    src_type = source.get("type")

    if src_type == "apt":
        pkg = source.get("package_name") or entry.get("package_name") or app_id
        return _apply_apt_update(app_id, pkg, progress_cb=progress_cb)

    # GitHub or Direct URL: ensure we have a download URL
    result = check_result
    if not result or not result.download_url:
        if progress_cb:
            progress_cb(None, "Checking remote update source…")
        result = check_for_update(app_id)
        if result.error:
            return False, result.error
        if not result.download_url:
            return False, result.message or "No download URL found."

    dl_dir = Path(tempfile.mkdtemp(prefix="dropinstall_dl_"))
    candidate = None
    try:
        downloaded_path, etag, last_mod = _download_file(
            result.download_url,
            dl_dir,
            preferred_name=result.asset_name,
            progress_cb=progress_cb
        )

        if progress_cb:
            progress_cb(None, f"Inspecting {downloaded_path.name}…")
        candidate = inspect_target(downloaded_path)
        if result.latest_version and candidate.version == "1.0":
            candidate.version = result.latest_version

        if progress_cb:
            progress_cb(None, f"Installing update for {entry.get('name', app_id)}…")
        ok, msg = update_installed_app(
            app_id,
            candidate,
            preserve_settings=preserve_settings,
            move_original=False
        )

        # Persist ETag / Last-Modified for URL sources
        if ok and src_type == "url":
            updated_manifest = load_manifest()
            if app_id in updated_manifest and isinstance(updated_manifest[app_id].get("update_source"), dict):
                if etag:
                    updated_manifest[app_id]["update_source"]["etag"] = etag
                if last_mod:
                    updated_manifest[app_id]["update_source"]["last_modified"] = last_mod
                save_manifest(updated_manifest)

        return ok, msg
    except Exception as e:
        return False, f"Update failed: {e}"
    finally:
        if candidate:
            candidate.cleanup_staging()
        shutil.rmtree(dl_dir, ignore_errors=True)
