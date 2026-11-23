"""
Unit and integration tests for Drop Installer update engine (in-place updates,
atomic rollback, GitHub Releases, Direct URL, and APT update/migration).
"""

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from inspector import infer_app_version, inspect_target
from installer import (
    find_matching_installed_app,
    get_apps_dir,
    get_bin_dir,
    get_opt_dir,
    install_candidate,
    load_manifest,
    uninstall_app,
    update_installed_app,
    update_launcher_icon,
)
from updater import (
    apply_online_update,
    check_for_update,
    detect_apt_package,
    is_newer_version,
    normalize_github_repo,
    select_best_github_asset,
    set_update_source,
)


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_dropupdate_"))
        os.environ["DROP_INSTALLER_BASE_DIR"] = str(self.temp_dir / ".local")

    def tearDown(self):
        if "DROP_INSTALLER_BASE_DIR" in os.environ:
            del os.environ["DROP_INSTALLER_BASE_DIR"]
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _make_sample_folder(self, dirname: str, version: str, body_text: str) -> Path:
        folder = self.temp_dir / dirname
        folder.mkdir(parents=True, exist_ok=True)
        bin_dir = folder / "bin"
        bin_dir.mkdir(exist_ok=True)

        main_bin = bin_dir / "SampleSuite"
        main_bin.write_text(f"#!/bin/bash\necho '{body_text}'\n", encoding="utf-8")
        os.chmod(main_bin, 0o755)

        comp_bin = bin_dir / "SampleSuite 3D"
        comp_bin.write_text(f"#!/bin/bash\necho '{body_text} 3D'\n", encoding="utf-8")
        os.chmod(comp_bin, 0o755)

        pkg_dir = folder / "resources" / "app"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "package.json").write_text(json.dumps({"name": "sample", "version": version}), encoding="utf-8")

        icon_file = folder / "icon.png"
        icon_file.write_bytes(b"\x89PNG\r\n\x1a\nbundledicon")
        return folder

    def test_version_inference_and_comparison(self):
        self.assertEqual(infer_app_version("arduino-ide_2.3.10_Linux_64bit.AppImage"), "2.3.10")
        self.assertEqual(infer_app_version("NetLogo-7.0.4.tar.gz"), "7.0.4")

        self.assertTrue(is_newer_version("1.0", "1.0.1"))
        self.assertTrue(is_newer_version("2.3.9", "2.3.10"))
        self.assertTrue(is_newer_version("v1.22.2-1775164135", "1.23.2-1776332190"))
        self.assertFalse(is_newer_version("2.3.10", "2.3.10"))
        self.assertFalse(is_newer_version("2.4.0", "2.3.10"))
        self.assertTrue(is_newer_version("1.0.0-beta", "1.0.0"))

    def test_github_helpers(self):
        self.assertEqual(normalize_github_repo("https://github.com/arduino/arduino-ide.git"), "arduino/arduino-ide")
        self.assertEqual(normalize_github_repo("owner/repo/"), "owner/repo")

        assets = [
            {"name": "app-1.2.0-win64.exe", "browser_download_url": "https://ex/win.exe"},
            {"name": "app-1.2.0-macos.dmg", "browser_download_url": "https://ex/mac.dmg"},
            {"name": "app-1.2.0-linux-x86_64.AppImage.sha256", "browser_download_url": "https://ex/sha"},
            {"name": "app-1.2.0-linux-x86_64.AppImage", "browser_download_url": "https://ex/linux.AppImage"},
            {"name": "app-1.2.0-linux-x86_64.tar.gz", "browser_download_url": "https://ex/linux.tar.gz"},
        ]
        with patch("updater.get_system_arch", return_value="x86_64"):
            picked_ai = select_best_github_asset(assets, preferred_kind="appimage")
            self.assertEqual(picked_ai["name"], "app-1.2.0-linux-x86_64.AppImage")

            picked_tar = select_best_github_asset(assets, preferred_kind="archive")
            self.assertEqual(picked_tar["name"], "app-1.2.0-linux-x86_64.tar.gz")

            picked_glob = select_best_github_asset(assets, asset_pattern="*.tar.gz")
            self.assertEqual(picked_glob["name"], "app-1.2.0-linux-x86_64.tar.gz")

    def test_in_place_update_preserves_custom_settings_and_icons(self):
        v1_dir = self._make_sample_folder("SampleSuite-1.0.0", "1.0.0", "v1")
        cand1 = inspect_target(v1_dir)
        self.assertEqual(cand1.version, "1.0.0")

        ok, msg = install_candidate(
            cand1,
            name="My Custom Suite",
            category="Development;",
            executable_rel="bin/SampleSuite",
            use_no_sandbox=True,
            additional_executables=["bin/SampleSuite 3D"],
        )
        self.assertTrue(ok, msg)

        # User sets custom icon and update source
        custom_svg = self.temp_dir / "my_custom_icon.svg"
        custom_svg.write_text("<svg>custom-user-art</svg>", encoding="utf-8")
        u_ok, u_msg, custom_icon_installed = update_launcher_icon(cand1.app_id, custom_svg)
        self.assertTrue(u_ok, u_msg)
        set_update_source(cand1.app_id, {"type": "github", "repo": "example/sample-suite"})

        # Now update with v2.0.0 folder
        v2_dir = self._make_sample_folder("SampleSuite-2.0.0", "2.0.0", "v2")
        cand2 = inspect_target(v2_dir)
        self.assertEqual(cand2.version, "2.0.0")

        # Smart matching finds existing app
        matched = find_matching_installed_app(cand2)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["app_id"], cand1.app_id)

        up_ok, up_msg = update_installed_app(cand1.app_id, cand2, preserve_settings=True)
        self.assertTrue(up_ok, up_msg)

        manifest = load_manifest()
        entry = manifest[cand1.app_id]
        self.assertEqual(entry["version"], "2.0.0")
        self.assertEqual(entry["name"], "My Custom Suite")
        self.assertEqual(entry["category"], "Development;")
        self.assertTrue(entry["use_no_sandbox"])
        self.assertTrue(entry.get("custom_icon"))
        self.assertEqual(entry["update_source"]["repo"], "example/sample-suite")
        self.assertIn("updated_at", entry)

        # Verify binary content actually updated to v2
        installed_bin = Path(entry["exec_path"])
        self.assertIn("echo 'v2'", installed_bin.read_text(encoding="utf-8"))

        # Verify custom icon content preserved
        self.assertEqual(Path(entry["icon_path"]).read_text(encoding="utf-8"), "<svg>custom-user-art</svg>")

        # Verify companion launcher preserved
        self.assertEqual(len(entry["additional_launchers"]), 1)
        self.assertEqual(entry["additional_launchers"][0]["name"], "SampleSuite 3D")

        uninstall_app(cand1.app_id)

    def test_failed_update_preserves_existing_install(self):
        v1_dir = self._make_sample_folder("RollbackApp-1.0.0", "1.0.0", "v1_good")
        cand1 = inspect_target(v1_dir)
        ok, _ = install_candidate(cand1, executable_rel="bin/SampleSuite")
        self.assertTrue(ok)

        # Build a broken v2 folder missing the expected executable
        broken_dir = self.temp_dir / "RollbackApp-2.0.0"
        broken_dir.mkdir()
        other_bin = broken_dir / "other"
        other_bin.write_text("#!/bin/bash\necho other\n", encoding="utf-8")
        os.chmod(other_bin, 0o755)
        cand_bad = inspect_target(broken_dir)

        # Requesting a non-existent executable must fail WITHOUT deleting v1
        bad_ok, bad_msg = install_candidate(
            cand_bad,
            executable_rel="bin/does-not-exist",
            target_app_id=cand1.app_id
        )
        self.assertFalse(bad_ok)
        self.assertIn("Executable not found", bad_msg)

        # Original v1 binary is completely untouched
        orig_bin = get_opt_dir() / cand1.app_id / "bin" / "SampleSuite"
        self.assertTrue(orig_bin.exists())
        self.assertIn("v1_good", orig_bin.read_text(encoding="utf-8"))

        uninstall_app(cand1.app_id)

    def test_apt_detection_and_portable_migration(self):
        # Install a portable folder named 'demo-editor'
        folder = self.temp_dir / "DemoEditor"
        folder.mkdir()
        bin_file = folder / "demoeditor"
        bin_file.write_text("#!/bin/bash\necho old_portable\n", encoding="utf-8")
        os.chmod(bin_file, 0o755)

        cand = inspect_target(folder)
        ok, msg = install_candidate(cand, name="DemoEditor")
        self.assertTrue(ok, msg)
        old_opt = get_opt_dir() / "demoeditor"
        old_desktop = get_apps_dir() / "dropinstall-demoeditor.desktop"
        old_symlink = get_bin_dir() / "demoeditor"
        self.assertTrue(old_opt.exists())
        self.assertTrue(old_desktop.exists())
        self.assertTrue(old_symlink.exists())

        mock_policy_out = (
            "demoeditor:\n"
            "  Installed: (none)\n"
            "  Candidate: 1.23.2-1776332190\n"
            "  Version table:\n"
            "     1.23.2-1776332190 500\n"
        )
        mock_policy = MagicMock(returncode=0, stdout=mock_policy_out, stderr="")
        with patch("updater.subprocess.run", return_value=mock_policy):
            info = detect_apt_package("demoeditor", "DemoEditor")
            self.assertIsNotNone(info)
            self.assertEqual(info["candidate"], "1.23.2-1776332190")

            set_ok, _ = set_update_source("demoeditor", {"type": "apt", "package_name": "demoeditor"})
            self.assertTrue(set_ok)

            chk = check_for_update("demoeditor")
            self.assertTrue(chk.has_update)
            self.assertEqual(chk.latest_version, "1.23.2-1776332190")

        # Mock pkexec apt-get update && install and dpkg-query
        def fake_run(cmd, **kwargs):
            if cmd[0] == "pkexec":
                return MagicMock(returncode=0, stdout="Installed", stderr="")
            if cmd[0] == "dpkg-query":
                return MagicMock(returncode=0, stdout="1.23.2-1776332190", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("updater.subprocess.run", side_effect=fake_run):
            ap_ok, ap_msg = apply_online_update("demoeditor")
            self.assertTrue(ap_ok, ap_msg)
            self.assertIn("Migrated", ap_msg)

        # Verify old portable files were cleaned up and manifest switched to deb/apt
        self.assertFalse(old_opt.exists())
        self.assertFalse(old_desktop.exists())
        self.assertFalse(old_symlink.exists())

        manifest = load_manifest()
        self.assertEqual(manifest["demoeditor"]["kind"], "deb")
        self.assertEqual(manifest["demoeditor"]["version"], "1.23.2-1776332190")
        self.assertEqual(manifest["demoeditor"]["update_source"]["type"], "apt")

    def test_github_check_and_download_update(self):
        v1_dir = self._make_sample_folder("CloudTool-1.0.0", "1.0.0", "v1")
        cand1 = inspect_target(v1_dir)
        ok, _ = install_candidate(cand1, executable_rel="bin/SampleSuite")
        self.assertTrue(ok)
        set_update_source(cand1.app_id, {"type": "github", "repo": "acme/cloudtool"})

        # Build a real v2 tar.gz to serve from mocked urlopen
        v2_dir = self._make_sample_folder("CloudTool-2.1.0", "2.1.0", "v2_downloaded")
        v2_tar = self.temp_dir / "cloudtool-2.1.0-linux-x86_64.tar.gz"
        with tarfile.open(v2_tar, "w:gz") as tar:
            tar.add(v2_dir, arcname="CloudTool-2.1.0")
        tar_bytes = v2_tar.read_bytes()

        release_payload = json.dumps({
            "tag_name": "v2.1.0",
            "assets": [
                {
                    "name": "cloudtool-2.1.0-linux-x86_64.tar.gz",
                    "browser_download_url": "https://example.invalid/cloudtool-2.1.0-linux-x86_64.tar.gz"
                }
            ]
        }).encode("utf-8")

        class FakeResp:
            def __init__(self, data: bytes, url: str):
                self._bio = io.BytesIO(data)
                self._url = url
                self.headers = {"Content-Length": str(len(data))}

            def read(self, n=-1):
                return self._bio.read(n)

            def geturl(self):
                return self._url

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=12):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "api.github.com" in url:
                return FakeResp(release_payload, url)
            return FakeResp(tar_bytes, url)

        with patch("updater.urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("updater.get_system_arch", return_value="x86_64"):
            chk = check_for_update(cand1.app_id)
            self.assertTrue(chk.has_update, chk.message)
            self.assertEqual(chk.latest_version, "2.1.0")

            progress_events = []
            up_ok, up_msg = apply_online_update(
                cand1.app_id,
                check_result=chk,
                progress_cb=lambda frac, txt: progress_events.append((frac, txt))
            )
            self.assertTrue(up_ok, up_msg)
            self.assertTrue(len(progress_events) > 0)

        manifest = load_manifest()
        self.assertEqual(manifest[cand1.app_id]["version"], "2.1.0")
        installed_bin = Path(manifest[cand1.app_id]["exec_path"])
        self.assertIn("v2_downloaded", installed_bin.read_text(encoding="utf-8"))
        uninstall_app(cand1.app_id)


if __name__ == "__main__":
    unittest.main()
