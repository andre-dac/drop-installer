"""
Unit and integration tests for drop-installer inspector and installer modules.
"""

import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

# Add parent directory to sys.path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from inspector import inspect_target, sanitize_app_id, infer_app_name
from installer import (
    install_candidate,
    uninstall_app,
    load_manifest,
    get_opt_dir,
    get_apps_dir,
    get_icons_dir,
    get_bin_dir,
    get_app_launchers,
    update_launcher_icon
)


class TestInspectorAndInstaller(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_dropinstall_"))
        os.environ["DROP_INSTALLER_BASE_DIR"] = str(self.temp_dir / ".local")
        
    def tearDown(self):
        if "DROP_INSTALLER_BASE_DIR" in os.environ:
            del os.environ["DROP_INSTALLER_BASE_DIR"]
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            
    def test_sanitize_and_infer_name(self):
        self.assertEqual(sanitize_app_id("Arduino IDE 2.3.2"), "arduino-ide-2-3-2")
        self.assertEqual(sanitize_app_id("SampleApp!"), "sampleapp")
        self.assertEqual(infer_app_name("arduino-ide_2.3.2_Linux_64bit.tar.gz"), "Arduino Ide")
        self.assertEqual(infer_app_name("sample-app-linux-x64.zip"), "Sample App")

    def test_mock_arduino_archive(self):
        """Simulate an archive like Arduino IDE with chrome-sandbox, main binary and icon."""
        archive_stage = self.temp_dir / "stage"
        root_inside = archive_stage / "arduino-ide_2.3.2_Linux_64bit"
        root_inside.mkdir(parents=True)
        
        # Main binary
        main_bin = root_inside / "arduino-ide"
        with open(main_bin, "wb") as f:
            f.write(b"\x7fELFfakebinary")
        os.chmod(main_bin, 0o755)
        
        # Noise binary (chrome-sandbox)
        sandbox_bin = root_inside / "chrome-sandbox"
        with open(sandbox_bin, "wb") as f:
            f.write(b"\x7fELFfakesandbox")
        os.chmod(sandbox_bin, 0o755)
        
        # Icon
        icon_dir = root_inside / "resources" / "app"
        icon_dir.mkdir(parents=True)
        icon_file = icon_dir / "arduino.png"
        with open(icon_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfakeimage")
            
        # Create tar.gz
        tar_path = self.temp_dir / "arduino-ide_2.3.2_Linux_64bit.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(root_inside, arcname="arduino-ide_2.3.2_Linux_64bit")
            
        candidate = inspect_target(tar_path)
        self.assertEqual(candidate.kind, "archive")
        self.assertIn("Arduino", candidate.name)
        self.assertTrue(candidate.is_electron)
        self.assertIsNotNone(candidate.selected_executable)
        self.assertEqual(candidate.selected_executable.name, "arduino-ide")
        self.assertIsNotNone(candidate.selected_icon)
        self.assertEqual(candidate.selected_icon.path.name, "arduino.png")
        
        # Test installation
        ok, msg = install_candidate(
            candidate,
            name="Arduino IDE",
            category="Development;IDE;",
            use_no_sandbox=True,
            move_original=False
        )
        self.assertTrue(ok, msg)
        
        # Verify installed files
        app_opt = get_opt_dir() / candidate.app_id
        desktop_file = get_apps_dir() / f"dropinstall-{candidate.app_id}.desktop"
        bin_link = get_bin_dir() / candidate.app_id
        
        self.assertTrue(app_opt.exists())
        self.assertTrue((app_opt / "arduino-ide").exists())
        self.assertTrue(desktop_file.exists())
        self.assertTrue(bin_link.exists())
        
        # Check desktop file contents
        with open(desktop_file, "r") as f:
            content = f.read()
            self.assertIn("Name=Arduino IDE", content)
            self.assertIn("--no-sandbox", content)
            self.assertIn("Categories=Development;IDE;", content)
            
        # Test uninstall
        un_ok, un_msg = uninstall_app(candidate.app_id)
        self.assertTrue(un_ok, un_msg)
        self.assertFalse(app_opt.exists())
        self.assertFalse(desktop_file.exists())
        self.assertFalse(bin_link.exists())

    def test_mock_folder_migration(self):
        """Simulate an already extracted portable app folder."""
        folder = self.temp_dir / "SampleApp"
        folder.mkdir(parents=True)
        
        exec_file = folder / "sampleapp"
        with open(exec_file, "w") as f:
            f.write("#!/bin/sh\necho SampleApp\n")
        os.chmod(exec_file, 0o755)
        
        icon_file = folder / "sampleapp.svg"
        with open(icon_file, "w") as f:
            f.write("<svg></svg>")
            
        candidate = inspect_target(folder)
        self.assertEqual(candidate.kind, "folder")
        self.assertEqual(candidate.name, "SampleApp")
        self.assertEqual(candidate.selected_executable.name, "sampleapp")
        self.assertEqual(candidate.selected_icon.path.name, "sampleapp.svg")
        
        # Install with move_original=False
        ok, msg = install_candidate(candidate, name="SampleApp")
        self.assertTrue(ok, msg)
        
        app_opt = get_opt_dir() / candidate.app_id
        self.assertTrue(app_opt.exists())
        self.assertTrue((app_opt / "sampleapp").exists())
        
        # Clean up
        uninstall_app(candidate.app_id)

    def test_zip_archive_and_fallback_icon(self):
        """Test zip archive with no embedded icon triggering fallback SVG generator."""
        zip_stage = self.temp_dir / "zip_stage"
        zip_stage.mkdir(parents=True)
        bin_file = zip_stage / "my-tool"
        with open(bin_file, "w") as f:
            f.write("#!/bin/bash\necho 'hello tool'\n")
        os.chmod(bin_file, 0o755)
        
        zip_path = self.temp_dir / "my-tool-linux.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.write(bin_file, arcname="my-tool")
            
        candidate = inspect_target(zip_path)
        self.assertEqual(candidate.kind, "archive")
        self.assertEqual(candidate.name, "My Tool")
        self.assertIsNone(candidate.selected_icon)
        
        ok, msg = install_candidate(candidate)
        self.assertTrue(ok, msg)
        
        desktop_file = get_apps_dir() / f"dropinstall-{candidate.app_id}.desktop"
        self.assertTrue(desktop_file.exists())
        
        # Verify fallback icon was generated
        fallback_icon = get_icons_dir() / "scalable" / "apps" / f"dropinstall-{candidate.app_id}.svg"
        self.assertTrue(fallback_icon.exists())
        with open(fallback_icon, "r") as f:
            svg_content = f.read()
            self.assertIn("<svg", svg_content)
            self.assertIn(">M<", svg_content)  # First letter 'M' from 'My Tool'
            
        uninstall_app(candidate.app_id)

    def test_nested_netlogo_multi_executable(self):
        """Simulate NetLogo structure with wrapper folder, multiple executables, and heavy runtime/model noise."""
        wrapper_dir = self.temp_dir / "NetLogo"
        inner_app_dir = wrapper_dir / "NetLogo-7.0.4"
        inner_app_dir.mkdir(parents=True)
        
        # Companion executables in bin/
        bin_dir = inner_app_dir / "bin"
        bin_dir.mkdir()
        
        netlogo_bin = bin_dir / "NetLogo"
        with open(netlogo_bin, "w") as f:
            f.write("#!/bin/bash\necho 'Starting NetLogo'\n")
        os.chmod(netlogo_bin, 0o755)

        netlogo_3d_bin = bin_dir / "NetLogo 3D"
        with open(netlogo_3d_bin, "w") as f:
            f.write("#!/bin/bash\necho 'Starting NetLogo 3D'\n")
        os.chmod(netlogo_3d_bin, 0o755)

        behaviorspace_bin = bin_dir / "BehaviorSpace"
        with open(behaviorspace_bin, "w") as f:
            f.write("#!/bin/bash\necho 'Starting BehaviorSpace'\n")
        os.chmod(behaviorspace_bin, 0o755)

        # Noise: runtime/bin/java and libjvm.so.1 (must be pruned/ignored)
        runtime_bin = inner_app_dir / "runtime" / "bin"
        runtime_bin.mkdir(parents=True)
        java_bin = runtime_bin / "java"
        with open(java_bin, "wb") as f:
            f.write(b"\x7fELFfakejava")
        os.chmod(java_bin, 0o755)

        runtime_lib = inner_app_dir / "lib" / "runtime" / "lib"
        runtime_lib.mkdir(parents=True)
        libjvm = runtime_lib / "libjvm.so.1"
        with open(libjvm, "wb") as f:
            f.write(b"\x7fELFfakelib")
        os.chmod(libjvm, 0o755)

        # Noise: models/ and docs/ (must be pruned/ignored)
        models_dir = inner_app_dir / "models"
        models_dir.mkdir()
        for i in range(10):
            with open(models_dir / f"model_{i}.nlogo", "w") as f:
                f.write(";; dummy model")

        # Icon
        icon_file = inner_app_dir / "netlogo.png"
        with open(icon_file, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfakeimage")

        # Inspect the outer wrapper directory
        candidate = inspect_target(wrapper_dir)
        self.assertEqual(candidate.kind, "folder")
        self.assertEqual(candidate.name, "NetLogo")
        self.assertEqual(candidate.app_id, "netlogo")
        self.assertEqual(candidate.extracted_root, inner_app_dir)

        # Verify executables found
        exec_names = [cand.name for cand in candidate.executables]
        self.assertIn("NetLogo", exec_names)
        self.assertIn("NetLogo 3D", exec_names)
        self.assertIn("BehaviorSpace", exec_names)
        # Verify noise binaries are NOT present
        self.assertNotIn("java", exec_names)
        self.assertNotIn("libjvm.so.1", exec_names)

        # Test installation with additional companion launcher for NetLogo 3D
        ok, msg = install_candidate(
            candidate,
            name="NetLogo",
            executable_rel="bin/NetLogo",
            additional_executables=["bin/NetLogo 3D"],
            move_original=False
        )
        self.assertTrue(ok, msg)

        # Verify primary launcher
        primary_desktop = get_apps_dir() / "dropinstall-netlogo.desktop"
        self.assertTrue(primary_desktop.exists())
        with open(primary_desktop, "r") as f:
            content = f.read()
            self.assertIn("Name=NetLogo\n", content)
            self.assertIn("Path=", content)
            self.assertIn("bin/NetLogo", content)

        # Verify companion launcher for NetLogo 3D
        companion_desktop = get_apps_dir() / "dropinstall-netlogo-3d.desktop"
        self.assertTrue(companion_desktop.exists())
        with open(companion_desktop, "r") as f:
            content_3d = f.read()
            self.assertIn("Name=NetLogo 3D", content_3d)
            self.assertIn("Path=", content_3d)
            self.assertIn('bin/NetLogo 3D"', content_3d)

        # Verify both CLI symlinks
        self.assertTrue((get_bin_dir() / "netlogo").exists())
        self.assertTrue((get_bin_dir() / "netlogo-3d").exists())

        # Test uninstallation cleans up both launchers
        un_ok, un_msg = uninstall_app(candidate.app_id)
        self.assertTrue(un_ok, un_msg)
        self.assertFalse(primary_desktop.exists())
        self.assertFalse(companion_desktop.exists())
        self.assertFalse((get_bin_dir() / "netlogo").exists())
        self.assertFalse((get_bin_dir() / "netlogo-3d").exists())
        self.assertFalse((get_opt_dir() / candidate.app_id).exists())

    def test_folder_install_with_permission_error_on_cleanup(self):
        """Simulate root-owned directory where rmtree fails with PermissionError."""
        from unittest.mock import patch

        folder = self.temp_dir / "RootOwnedApp"
        folder.mkdir(parents=True)
        bin_file = folder / "rootapp"
        with open(bin_file, "w") as f:
            f.write("#!/bin/bash\necho hello\n")
        os.chmod(bin_file, 0o755)

        candidate = inspect_target(folder)

        with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            ok, msg = install_candidate(candidate, name="RootApp", move_original=True)
            self.assertTrue(ok, msg)
            self.assertIn("root permissions and was preserved", msg)

        # Confirm target was installed
        self.assertTrue((get_opt_dir() / candidate.app_id).exists())
        uninstall_app(candidate.app_id)

    def test_get_app_launchers_and_update_icons(self):
        """Test retrieving launcher details and editing icons for primary & companion launchers."""
        folder = self.temp_dir / "MultiApp"
        folder.mkdir(parents=True)
        bin_dir = folder / "bin"
        bin_dir.mkdir()

        main_bin = bin_dir / "MultiApp"
        with open(main_bin, "w") as f:
            f.write("#!/bin/bash\necho main\n")
        os.chmod(main_bin, 0o755)

        comp_bin = bin_dir / "MultiApp 3D"
        with open(comp_bin, "w") as f:
            f.write("#!/bin/bash\necho companion\n")
        os.chmod(comp_bin, 0o755)

        candidate = inspect_target(folder)
        ok, msg = install_candidate(
            candidate,
            name="MultiApp",
            executable_rel="bin/MultiApp",
            additional_executables=["bin/MultiApp 3D"],
            move_original=False
        )
        self.assertTrue(ok, msg)

        # 1. Test get_app_launchers
        launchers = get_app_launchers(candidate.app_id)
        self.assertEqual(len(launchers), 2)
        main_l = next(l for l in launchers if l["is_main"])
        comp_l = next(l for l in launchers if not l["is_main"])
        self.assertEqual(main_l["name"], "MultiApp")
        self.assertEqual(comp_l["name"], "MultiApp 3D")
        self.assertTrue(Path(main_l["desktop_file"]).exists())
        self.assertTrue(Path(comp_l["desktop_file"]).exists())

        # 2. Test updating main launcher icon with custom PNG
        custom_main_png = self.temp_dir / "custom_main.png"
        with open(custom_main_png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\ncustommain")

        up_ok, up_msg, up_path = update_launcher_icon(candidate.app_id, custom_main_png)
        self.assertTrue(up_ok, up_msg)
        self.assertTrue(up_path.exists())
        self.assertTrue(str(up_path).endswith(".png"))

        # Check main desktop file updated
        main_content = Path(main_l["desktop_file"]).read_text()
        self.assertIn(f"Icon={str(up_path)}", main_content)

        # Check companion desktop file was NOT changed
        comp_content_before = Path(comp_l["desktop_file"]).read_text()
        self.assertNotIn(f"Icon={str(up_path)}", comp_content_before)

        # 3. Test updating companion launcher icon with custom SVG
        custom_comp_svg = self.temp_dir / "custom_comp.svg"
        with open(custom_comp_svg, "w") as f:
            f.write("<svg>custom_comp</svg>")

        c_ok, c_msg, c_path = update_launcher_icon(
            candidate.app_id,
            custom_comp_svg,
            target_desktop_file=Path(comp_l["desktop_file"]),
            target_sub_id=comp_l["app_id"]
        )
        self.assertTrue(c_ok, c_msg)
        self.assertTrue(c_path.exists())
        self.assertTrue(str(c_path).endswith(".svg"))

        # Check companion desktop file updated
        comp_content_after = Path(comp_l["desktop_file"]).read_text()
        self.assertIn(f"Icon={str(c_path)}", comp_content_after)

        # 4. Test reset main launcher to letter icon
        r_ok, r_msg, r_path = update_launcher_icon(candidate.app_id, None)
        self.assertTrue(r_ok, r_msg)
        self.assertTrue(r_path.exists())
        self.assertTrue(str(r_path).endswith(".svg"))
        with open(r_path) as f:
            svg_txt = f.read()
            self.assertIn(">M<", svg_txt)

        # 5. Uninstall cleans up both launchers and both icons
        un_ok, un_msg = uninstall_app(candidate.app_id)
        self.assertTrue(un_ok, un_msg)
        self.assertFalse(Path(main_l["desktop_file"]).exists())
        self.assertFalse(Path(comp_l["desktop_file"]).exists())
        self.assertFalse(c_path.exists())
        self.assertFalse(r_path.exists())


if __name__ == "__main__":
    unittest.main()


