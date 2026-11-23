"""
drop-installer ui: GTK 3 graphical user interface with drag-and-drop support,
app configuration preview, and installed apps management for Pop!_OS.
"""

import os
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from inspector import AppCandidate, inspect_target, format_executable_display_name
from installer import (
    install_candidate,
    uninstall_app,
    load_manifest,
    get_opt_dir,
    get_apps_dir,
    get_app_launchers,
    update_launcher_icon,
    find_matching_installed_app,
    parse_installed_settings,
    update_installed_app
)
from updater import (
    UpdateCheckResult,
    apply_online_update,
    check_all_updates,
    check_for_update,
    detect_apt_package,
    set_update_source
)


APP_CSS = """
.drop-zone {
    border: 2px dashed rgba(128, 128, 128, 0.4);
    border-radius: 12px;
    background-color: alpha(@theme_bg_color, 0.5);
    padding: 30px;
    transition: all 200ms ease;
}

.drop-zone:hover, .drop-zone-drag-over {
    border-color: #48b9c7;
    background-color: alpha(#48b9c7, 0.08);
}

.title-large {
    font-size: 16pt;
    font-weight: bold;
}

.subtitle {
    font-size: 10pt;
    opacity: 0.75;
}

.card {
    background-color: alpha(@theme_bg_color, 0.8);
    border: 1px solid alpha(@borders, 0.6);
    border-radius: 10px;
    padding: 16px;
}

.badge {
    background-color: alpha(#48b9c7, 0.2);
    color: #48b9c7;
    border-radius: 6px;
    padding: 2px 8px;
    font-weight: bold;
    font-size: 8.5pt;
}

.version-badge {
    background-color: alpha(@theme_fg_color, 0.1);
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 8.5pt;
}

.update-badge {
    background-color: alpha(#3da853, 0.2);
    color: #3da853;
    border-radius: 6px;
    padding: 2px 8px;
    font-weight: bold;
    font-size: 8.5pt;
}

.info-banner {
    background-color: alpha(#48b9c7, 0.12);
    border: 1px solid alpha(#48b9c7, 0.5);
    border-radius: 8px;
    padding: 10px 12px;
}

.success-banner {
    background-color: alpha(#3da853, 0.15);
    border: 1px solid #3da853;
    border-radius: 8px;
    padding: 12px;
}

.danger-button {
    color: #e05555;
}
"""


class EditIconsDialog(Gtk.Dialog):
    """
    Modal dialog allowing users to customize and change the icon for an installed app
    and all of its companion launchers (e.g. NetLogo and NetLogo 3D).
    """
    def __init__(self, parent: Gtk.Window, app_id: str):
        super().__init__(
            title="Edit Application Icons",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT
        )
        self.parent_window = parent
        self.app_id = app_id
        self.set_default_size(560, 460)
        self.set_border_width(12)

        content_box = self.get_content_area()
        content_box.set_spacing(12)

        manifest = load_manifest()
        app_info = manifest.get(app_id, {})
        app_name = app_info.get("name", app_id)
        self.installed_dir = app_info.get("installed_dir")

        lbl_header = Gtk.Label()
        lbl_header.set_markup(f"<span size='large' weight='bold'>Edit Icons — {app_name}</span>")
        lbl_header.set_halign(Gtk.Align.START)
        content_box.pack_start(lbl_header, False, False, 0)

        lbl_desc = Gtk.Label(
            label="Customize the icon for this application and each companion launcher.\n"
                  "Changes take effect immediately in the Pop!_OS launcher."
        )
        lbl_desc.set_line_wrap(True)
        lbl_desc.set_halign(Gtk.Align.START)
        lbl_desc.get_style_context().add_class("subtitle")
        content_box.pack_start(lbl_desc, False, False, 0)

        # Status / Feedback label
        self.lbl_feedback = Gtk.Label()
        self.lbl_feedback.set_halign(Gtk.Align.START)
        self.lbl_feedback.set_no_show_all(True)
        content_box.pack_start(self.lbl_feedback, False, False, 0)

        # Scrolled launcher list
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_min_content_height(260)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.listbox)
        content_box.pack_start(scroller, True, True, 0)

        self.populate_launchers()

        btn_close = self.add_button("Done", Gtk.ResponseType.CLOSE)
        btn_close.get_style_context().add_class("suggested-action")
        self.connect("response", lambda d, r: self.destroy())
        self.show_all()
        self.lbl_feedback.hide()

    def populate_launchers(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        launchers = get_app_launchers(self.app_id)
        if not launchers:
            empty_lbl = Gtk.Label(label="No launcher shortcuts found for this application.")
            empty_lbl.set_margin_top(20)
            self.listbox.add(empty_lbl)
            self.listbox.show_all()
            return

        for launcher in launchers:
            row = Gtk.ListBoxRow()
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            card.set_border_width(12)

            icon_img = Gtk.Image()
            icon_img.set_pixel_size(48)
            self.update_image_widget(icon_img, launcher.get("icon_path"))
            card.pack_start(icon_img, False, False, 0)

            details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            lbl_name = Gtk.Label()
            lbl_name.set_markup(f"<b>{launcher.get('name', 'Launcher')}</b>")
            lbl_name.set_halign(Gtk.Align.START)
            title_box.pack_start(lbl_name, False, False, 0)

            badge_text = "MAIN" if launcher.get("is_main") else "COMPANION"
            badge = Gtk.Label(label=badge_text)
            badge.get_style_context().add_class("badge")
            title_box.pack_start(badge, False, False, 0)
            details_box.pack_start(title_box, False, False, 0)

            exec_str = launcher.get("exec_path") or Path(launcher.get("desktop_file", "")).name
            lbl_exec = Gtk.Label(label=str(exec_str))
            lbl_exec.set_halign(Gtk.Align.START)
            lbl_exec.get_style_context().add_class("subtitle")
            lbl_exec.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl_exec.set_max_width_chars(34)
            details_box.pack_start(lbl_exec, False, False, 0)

            card.pack_start(details_box, True, True, 0)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_change = Gtk.Button(label="Change Icon…")
            btn_change.set_tooltip_text("Select a custom PNG, SVG, or ICO image file")
            btn_change.connect("clicked", lambda b, l=launcher, img=icon_img: self.on_change_icon_clicked(l, img))
            btn_box.pack_start(btn_change, False, False, 0)

            btn_reset = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.BUTTON)
            btn_reset.set_tooltip_text("Reset to letter badge icon")
            btn_reset.connect("clicked", lambda b, l=launcher, img=icon_img: self.on_reset_letter_clicked(l, img))
            btn_box.pack_start(btn_reset, False, False, 0)

            card.pack_end(btn_box, False, False, 0)

            row.add(card)
            self.listbox.add(row)

        self.listbox.show_all()

    def update_image_widget(self, img_widget: Gtk.Image, icon_path_str: Optional[str]):
        if icon_path_str and Path(icon_path_str).exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path_str), 48, 48, True)
                img_widget.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        img_widget.set_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)

    def on_change_icon_clicked(self, launcher: dict, img_widget: Gtk.Image):
        chooser = Gtk.FileChooserDialog(
            title=f"Choose Icon for {launcher.get('name', 'Launcher')}",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        filter_img = Gtk.FileFilter()
        filter_img.set_name("Image Files (*.png, *.svg, *.ico, *.jpg, *.jpeg, *.webp, *.xpm)")
        for pat in ("*.png", "*.svg", "*.ico", "*.jpg", "*.jpeg", "*.webp", "*.xpm"):
            filter_img.add_pattern(pat)
        chooser.add_filter(filter_img)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All Files")
        filter_all.add_pattern("*")
        chooser.add_filter(filter_all)

        if self.installed_dir and Path(self.installed_dir).exists():
            chosen_start = Path(self.installed_dir)
            for cd in (
                Path(self.installed_dir) / "icons",
                Path(self.installed_dir) / "lib",
                Path(self.installed_dir) / "resources"
            ):
                if cd.exists() and cd.is_dir():
                    chosen_start = cd
                    break
            chooser.set_current_folder(str(chosen_start))

        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            selected_path = Path(chooser.get_filename())
            chooser.destroy()

            df_path = Path(launcher["desktop_file"]) if launcher.get("desktop_file") else None
            sub_id = launcher.get("app_id")

            ok, msg, new_path = update_launcher_icon(
                self.app_id,
                selected_path,
                target_desktop_file=df_path,
                target_sub_id=sub_id
            )
            if ok and new_path:
                launcher["icon_path"] = str(new_path)
                self.update_image_widget(img_widget, str(new_path))
                self.show_feedback(f"✓ Icon updated for '{launcher.get('name')}'")
                if hasattr(self.parent_window, "refresh_installed_apps_list"):
                    self.parent_window.refresh_installed_apps_list()
            else:
                self.show_feedback(f"Error: {msg}", is_error=True)
        else:
            chooser.destroy()

    def on_reset_letter_clicked(self, launcher: dict, img_widget: Gtk.Image):
        df_path = Path(launcher["desktop_file"]) if launcher.get("desktop_file") else None
        sub_id = launcher.get("app_id")

        ok, msg, new_path = update_launcher_icon(
            self.app_id,
            None,
            target_desktop_file=df_path,
            target_sub_id=sub_id
        )
        if ok and new_path:
            launcher["icon_path"] = str(new_path)
            self.update_image_widget(img_widget, str(new_path))
            self.show_feedback(f"✓ Reset icon for '{launcher.get('name')}' to letter badge")
            if hasattr(self.parent_window, "refresh_installed_apps_list"):
                self.parent_window.refresh_installed_apps_list()
        else:
            self.show_feedback(f"Error: {msg}", is_error=True)

    def show_feedback(self, text: str, is_error: bool = False):
        color = "#e05555" if is_error else "#3da853"
        self.lbl_feedback.set_markup(f"<span color='{color}'><b>{GLib.markup_escape_text(text)}</b></span>")
        self.lbl_feedback.show()


class UpdateAppDialog(Gtk.Dialog):
    """
    Modal dialog allowing users to update an installed application either:
      1. In-place from a local file/folder (preserving custom icons & settings), or
      2. Automatically via a configured online update source (GitHub Releases, Direct URL, or APT).
    """
    def __init__(self, parent: Gtk.Window, app_id: str, initial_check: Optional[UpdateCheckResult] = None):
        super().__init__(
            title="Update Application",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True
        )
        self.parent_window = parent
        self.app_id = app_id
        self.latest_check: Optional[UpdateCheckResult] = initial_check
        self.is_busy = False
        self._pulse_source_id: Optional[int] = None

        self.set_default_size(580, 520)
        self.set_border_width(14)

        manifest = load_manifest()
        self.app_info = manifest.get(app_id, {})
        self.app_name = self.app_info.get("name", app_id)

        content_box = self.get_content_area()
        content_box.set_spacing(12)

        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.lbl_header = Gtk.Label()
        self.lbl_header.set_markup(
            f"<span size='large' weight='bold'>Update — {GLib.markup_escape_text(self.app_name)}</span>"
        )
        self.lbl_header.set_halign(Gtk.Align.START)
        header_box.pack_start(self.lbl_header, False, False, 0)

        self.lbl_cur_ver = Gtk.Label(
            label=f"v{self.app_info.get('version', '1.0')} • {self.app_info.get('kind', 'portable').upper()}"
        )
        self.lbl_cur_ver.get_style_context().add_class("version-badge")
        header_box.pack_start(self.lbl_cur_ver, False, False, 0)
        content_box.pack_start(header_box, False, False, 0)

        # APT auto-detection suggestion banner (hidden until detected)
        self.apt_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.apt_banner.get_style_context().add_class("info-banner")
        self.apt_banner.set_no_show_all(True)
        self.lbl_apt_banner = Gtk.Label()
        self.lbl_apt_banner.set_line_wrap(True)
        self.lbl_apt_banner.set_halign(Gtk.Align.START)
        self.apt_banner.pack_start(self.lbl_apt_banner, True, True, 0)
        self.btn_use_apt = Gtk.Button(label="Switch to APT")
        self.btn_use_apt.get_style_context().add_class("suggested-action")
        self.btn_use_apt.connect("clicked", self.on_switch_to_apt_clicked)
        self.apt_banner.pack_end(self.btn_use_apt, False, False, 0)
        self.detected_apt_pkg: Optional[str] = None
        content_box.pack_start(self.apt_banner, False, False, 0)

        # Scrollable body
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroller.add(body)
        content_box.pack_start(scroller, True, True, 0)

        # --- Card 1: Local File / Folder Update ---
        local_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        local_card.get_style_context().add_class("card")

        lbl_local_title = Gtk.Label()
        lbl_local_title.set_markup("<b>1. Update from Local File or Folder</b>")
        lbl_local_title.set_halign(Gtk.Align.START)
        local_card.pack_start(lbl_local_title, False, False, 0)

        lbl_local_desc = Gtk.Label(
            label="Select or drag & drop a newer archive (.tar.gz, .zip), .AppImage, .deb, or folder to replace this app in-place."
        )
        lbl_local_desc.set_line_wrap(True)
        lbl_local_desc.set_halign(Gtk.Align.START)
        lbl_local_desc.get_style_context().add_class("subtitle")
        local_card.pack_start(lbl_local_desc, False, False, 0)

        self.chk_preserve = Gtk.CheckButton(label="Preserve custom name, icons, companion launchers, and settings")
        self.chk_preserve.set_active(True)
        local_card.pack_start(self.chk_preserve, False, False, 0)

        self.chk_move_src = Gtk.CheckButton(label="Delete / move source file or folder after updating")
        self.chk_move_src.set_active(False)
        local_card.pack_start(self.chk_move_src, False, False, 0)

        local_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_local_file = Gtk.Button(label="Select New File…")
        self.btn_local_file.connect("clicked", self.on_local_file_clicked)
        local_btn_box.pack_start(self.btn_local_file, False, False, 0)

        self.btn_local_folder = Gtk.Button(label="Select New Folder…")
        self.btn_local_folder.connect("clicked", self.on_local_folder_clicked)
        local_btn_box.pack_start(self.btn_local_folder, False, False, 0)
        local_card.pack_start(local_btn_box, False, False, 4)

        # Enable drag-and-drop onto the dialog for quick local updates
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-data-received", self.on_dialog_drag_data_received)

        body.pack_start(local_card, False, False, 0)

        # --- Card 2: Online Update Source ---
        online_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        online_card.get_style_context().add_class("card")

        lbl_online_title = Gtk.Label()
        lbl_online_title.set_markup("<b>2. Online Update Source (Check &amp; Download)</b>")
        lbl_online_title.set_halign(Gtk.Align.START)
        online_card.pack_start(lbl_online_title, False, False, 0)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)

        lbl_type = Gtk.Label(label="Source Type:")
        lbl_type.set_halign(Gtk.Align.END)
        grid.attach(lbl_type, 0, 0, 1, 1)

        self.combo_src_type = Gtk.ComboBoxText()
        self.combo_src_type.append("none", "None (Manual Local Updates Only)")
        self.combo_src_type.append("github", "GitHub Releases (owner/repo)")
        self.combo_src_type.append("url", "Direct Download URL")
        self.combo_src_type.append("apt", "System APT Package")
        self.combo_src_type.connect("changed", self.on_source_type_changed)
        self.combo_src_type.set_hexpand(True)
        grid.attach(self.combo_src_type, 1, 0, 1, 1)

        self.lbl_primary_field = Gtk.Label(label="Repository:")
        self.lbl_primary_field.set_halign(Gtk.Align.END)
        grid.attach(self.lbl_primary_field, 0, 1, 1, 1)

        self.entry_primary = Gtk.Entry()
        self.entry_primary.set_hexpand(True)
        grid.attach(self.entry_primary, 1, 1, 1, 1)

        self.lbl_pattern = Gtk.Label(label="Asset Filter:")
        self.lbl_pattern.set_halign(Gtk.Align.END)
        grid.attach(self.lbl_pattern, 0, 2, 1, 1)

        self.entry_pattern = Gtk.Entry()
        self.entry_pattern.set_placeholder_text("Optional glob, e.g. *Linux_64bit.AppImage")
        self.entry_pattern.set_hexpand(True)
        grid.attach(self.entry_pattern, 1, 2, 1, 1)

        online_card.pack_start(grid, False, False, 0)

        online_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_save_source = Gtk.Button(label="Save Source")
        self.btn_save_source.connect("clicked", self.on_save_source_clicked)
        online_btn_box.pack_start(self.btn_save_source, False, False, 0)

        self.btn_check_update = Gtk.Button(label="Check Now")
        self.btn_check_update.connect("clicked", self.on_check_now_clicked)
        online_btn_box.pack_start(self.btn_check_update, False, False, 0)

        self.btn_apply_online = Gtk.Button(label="Download & Update")
        self.btn_apply_online.get_style_context().add_class("suggested-action")
        self.btn_apply_online.connect("clicked", self.on_apply_online_clicked)
        online_btn_box.pack_end(self.btn_apply_online, False, False, 0)

        online_card.pack_start(online_btn_box, False, False, 4)
        body.pack_start(online_card, False, False, 0)

        # Progress bar & status feedback
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_no_show_all(True)
        content_box.pack_start(self.progress_bar, False, False, 0)

        self.lbl_feedback = Gtk.Label()
        self.lbl_feedback.set_line_wrap(True)
        self.lbl_feedback.set_halign(Gtk.Align.START)
        self.lbl_feedback.set_no_show_all(True)
        content_box.pack_start(self.lbl_feedback, False, False, 0)

        # Action button
        self.btn_close = self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", self.on_dialog_response)

        # Load existing update source into form
        self.load_existing_source()

        self.show_all()
        self.on_source_type_changed(self.combo_src_type)
        self.progress_bar.hide()
        self.apt_banner.hide()

        if initial_check:
            if initial_check.error:
                self.show_feedback(initial_check.error, level="error")
            elif initial_check.message:
                self.show_feedback(initial_check.message, level="success" if initial_check.has_update else "info")
        else:
            self.lbl_feedback.hide()

        # Check in background if a matching system APT package exists for portable installs
        if not self.app_info.get("update_source") and self.app_info.get("kind") != "deb":
            self.probe_apt_suggestion()

    def load_existing_source(self):
        src = self.app_info.get("update_source")
        if not src and self.app_info.get("kind") == "deb":
            src = {"type": "apt", "package_name": self.app_info.get("package_name") or self.app_id}

        if not src:
            self.combo_src_type.set_active_id("none")
            return

        src_type = src.get("type", "none")
        self.combo_src_type.set_active_id(src_type)
        if src_type == "github":
            self.entry_primary.set_text(src.get("repo", ""))
            self.entry_pattern.set_text(src.get("asset_pattern", ""))
        elif src_type == "url":
            self.entry_primary.set_text(src.get("url", ""))
        elif src_type == "apt":
            self.entry_primary.set_text(src.get("package_name", self.app_id))

    def probe_apt_suggestion(self):
        def worker():
            apt_info = detect_apt_package(self.app_id, self.app_name)
            if apt_info:
                GLib.idle_add(self.show_apt_suggestion, apt_info)
        threading.Thread(target=worker, daemon=True).start()

    def show_apt_suggestion(self, apt_info: dict):
        pkg = apt_info["package_name"]
        cand = apt_info["candidate"]
        self.detected_apt_pkg = pkg
        self.lbl_apt_banner.set_markup(
            f"<b>APT Repository Detected:</b> Package <b>{GLib.markup_escape_text(pkg)}</b> "
            f"(v{GLib.markup_escape_text(cand)}) is available in your system repositories. "
            f"Switching to APT enables automatic system updates."
        )
        self.apt_banner.show_all()

    def on_switch_to_apt_clicked(self, button):
        if not self.detected_apt_pkg or self.is_busy:
            return
        self.combo_src_type.set_active_id("apt")
        self.entry_primary.set_text(self.detected_apt_pkg)
        ok, msg = set_update_source(self.app_id, {"type": "apt", "package_name": self.detected_apt_pkg})
        if not ok:
            self.show_feedback(msg, level="error")
            return
        self.apt_banner.hide()
        self.on_apply_online_clicked(None)

    def on_source_type_changed(self, combo):
        src_type = combo.get_active_id() or "none"
        if src_type == "none":
            self.lbl_primary_field.hide()
            self.entry_primary.hide()
            self.lbl_pattern.hide()
            self.entry_pattern.hide()
            self.btn_check_update.set_sensitive(False)
            self.btn_apply_online.set_sensitive(False)
        elif src_type == "github":
            self.lbl_primary_field.set_text("GitHub Repo:")
            self.entry_primary.set_placeholder_text("owner/repo (e.g. arduino/arduino-ide)")
            self.lbl_primary_field.show()
            self.entry_primary.show()
            self.lbl_pattern.show()
            self.entry_pattern.show()
            self.btn_check_update.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_label("Download & Update")
        elif src_type == "url":
            self.lbl_primary_field.set_text("Download URL:")
            self.entry_primary.set_placeholder_text("https://example.com/releases/app-latest.tar.gz")
            self.lbl_primary_field.show()
            self.entry_primary.show()
            self.lbl_pattern.hide()
            self.entry_pattern.hide()
            self.btn_check_update.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_label("Download & Update")
        elif src_type == "apt":
            self.lbl_primary_field.set_text("APT Package:")
            self.entry_primary.set_placeholder_text(f"e.g. {self.app_id}")
            if not self.entry_primary.get_text().strip():
                self.entry_primary.set_text(self.app_info.get("package_name") or self.app_id)
            self.lbl_primary_field.show()
            self.entry_primary.show()
            self.lbl_pattern.hide()
            self.entry_pattern.hide()
            self.btn_check_update.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_sensitive(not self.is_busy)
            self.btn_apply_online.set_label("Update via APT")

    def _build_source_dict(self) -> Optional[dict]:
        src_type = self.combo_src_type.get_active_id() or "none"
        if src_type == "none":
            return None
        val = self.entry_primary.get_text().strip()
        if src_type == "github":
            return {"type": "github", "repo": val, "asset_pattern": self.entry_pattern.get_text().strip()}
        if src_type == "url":
            return {"type": "url", "url": val}
        if src_type == "apt":
            return {"type": "apt", "package_name": val}
        return None

    def set_busy(self, busy: bool, status_text: str = ""):
        self.is_busy = busy
        for w in (
            self.btn_local_file,
            self.btn_local_folder,
            self.btn_save_source,
            self.btn_check_update,
            self.btn_apply_online,
            self.combo_src_type,
            self.btn_close,
            self.btn_use_apt
        ):
            w.set_sensitive(not busy)

        if busy:
            self.progress_bar.set_text(status_text or "Working…")
            self.progress_bar.show()
            if self._pulse_source_id is None:
                self._pulse_source_id = GLib.timeout_add(100, self._pulse_progress)
        else:
            if self._pulse_source_id is not None:
                GLib.source_remove(self._pulse_source_id)
                self._pulse_source_id = None
            self.progress_bar.hide()
            self.on_source_type_changed(self.combo_src_type)

    def _pulse_progress(self):
        if self.is_busy and self._pulse_source_id is not None:
            self.progress_bar.pulse()
            return True
        return False

    def _update_progress_ui(self, fraction: Optional[float], text: str):
        self.progress_bar.set_text(text)
        if fraction is not None:
            if self._pulse_source_id is not None:
                GLib.source_remove(self._pulse_source_id)
                self._pulse_source_id = None
            self.progress_bar.set_fraction(max(0.0, min(1.0, fraction)))
        else:
            if self._pulse_source_id is None and self.is_busy:
                self._pulse_source_id = GLib.timeout_add(100, self._pulse_progress)

    def show_feedback(self, text: str, level: str = "info"):
        color = "#48b9c7"
        if level == "success":
            color = "#3da853"
        elif level == "error":
            color = "#e05555"
        self.lbl_feedback.set_markup(f"<span color='{color}'><b>{GLib.markup_escape_text(text)}</b></span>")
        self.lbl_feedback.show()

    def _refresh_header_from_manifest(self):
        manifest = load_manifest()
        self.app_info = manifest.get(self.app_id, self.app_info)
        self.app_name = self.app_info.get("name", self.app_id)
        self.lbl_cur_ver.set_text(
            f"v{self.app_info.get('version', '1.0')} • {self.app_info.get('kind', 'portable').upper()}"
        )
        if hasattr(self.parent_window, "refresh_installed_apps_list"):
            self.parent_window.refresh_installed_apps_list()

    def on_save_source_clicked(self, button):
        src = self._build_source_dict()
        ok, msg = set_update_source(self.app_id, src)
        self.show_feedback(f"✓ {msg}" if ok else msg, level="success" if ok else "error")
        if ok:
            self.latest_check = None
            self._refresh_header_from_manifest()

    def on_check_now_clicked(self, button):
        if self.is_busy:
            return
        src = self._build_source_dict()
        ok, msg = set_update_source(self.app_id, src)
        if not ok:
            self.show_feedback(msg, level="error")
            return

        self.set_busy(True, "Checking for updates…")

        def worker():
            try:
                res = check_for_update(self.app_id)
            except Exception as e:
                res = UpdateCheckResult(self.app_id, False, self.app_info.get("version", "1.0"), error=str(e))
            GLib.idle_add(self.on_check_finished, res)

        threading.Thread(target=worker, daemon=True).start()

    def on_check_finished(self, res: UpdateCheckResult):
        self.set_busy(False)
        self.latest_check = res
        if hasattr(self.parent_window, "update_results"):
            self.parent_window.update_results[self.app_id] = res
            self.parent_window.refresh_installed_apps_list()

        if res.error:
            self.show_feedback(res.error, level="error")
        elif res.has_update:
            self.show_feedback(f"↑ {res.message}", level="success")
        else:
            self.show_feedback(f"✓ {res.message}", level="info")

    def on_apply_online_clicked(self, button):
        if self.is_busy:
            return
        src = self._build_source_dict()
        ok, msg = set_update_source(self.app_id, src)
        if not ok:
            self.show_feedback(msg, level="error")
            return

        preserve = self.chk_preserve.get_active()
        self.set_busy(True, "Starting update…")

        def progress_cb(frac, text):
            GLib.idle_add(self._update_progress_ui, frac, text)

        def worker():
            try:
                upd_ok, upd_msg = apply_online_update(
                    self.app_id,
                    check_result=self.latest_check,
                    preserve_settings=preserve,
                    progress_cb=progress_cb
                )
            except Exception as e:
                upd_ok, upd_msg = False, f"Update error: {e}"
            GLib.idle_add(self.on_update_applied, upd_ok, upd_msg)

        threading.Thread(target=worker, daemon=True).start()

    def on_local_file_clicked(self, button):
        if self.is_busy:
            return
        chooser = Gtk.FileChooserDialog(
            title=f"Select New Version File for {self.app_name}",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        filter_all = Gtk.FileFilter()
        filter_all.set_name("Supported App Files (.deb, .tar.gz, .zip, .AppImage)")
        for pat in ("*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.zip", "*.deb", "*.AppImage", "*.appimage"):
            filter_all.add_pattern(pat)
        chooser.add_filter(filter_all)

        downloads_dir = Path.home() / "Downloads"
        if downloads_dir.exists():
            chooser.set_current_folder(str(downloads_dir))

        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            chosen = Path(chooser.get_filename())
            chooser.destroy()
            self.run_local_update(chosen)
        else:
            chooser.destroy()

    def on_local_folder_clicked(self, button):
        if self.is_busy:
            return
        chooser = Gtk.FileChooserDialog(
            title=f"Select New Version Folder for {self.app_name}",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            chosen = Path(chooser.get_filename())
            chooser.destroy()
            self.run_local_update(chosen)
        else:
            chooser.destroy()

    def on_dialog_drag_data_received(self, widget, context, x, y, data, info, time):
        if not self.is_busy:
            uris = data.get_uris()
            if uris:
                parsed = urllib.parse.urlparse(uris[0])
                local_path = Path(urllib.parse.unquote(parsed.path))
                if local_path.exists():
                    self.run_local_update(local_path)
        context.finish(True, False, time)

    def run_local_update(self, target_path: Path):
        preserve = self.chk_preserve.get_active()
        move_orig = self.chk_move_src.get_active()
        self.set_busy(True, f"Inspecting {target_path.name}…")

        def worker():
            candidate = None
            try:
                candidate = inspect_target(target_path)
                GLib.idle_add(self._update_progress_ui, None, f"Updating {self.app_name} to v{candidate.version}…")
                ok, msg = update_installed_app(
                    self.app_id,
                    candidate,
                    preserve_settings=preserve,
                    move_original=move_orig
                )
            except Exception as e:
                ok, msg = False, f"Update failed: {e}"
            finally:
                if candidate:
                    candidate.cleanup_staging()
            GLib.idle_add(self.on_update_applied, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def on_update_applied(self, ok: bool, msg: str):
        self.set_busy(False)
        if ok:
            self.latest_check = None
            if hasattr(self.parent_window, "update_results"):
                self.parent_window.update_results.pop(self.app_id, None)
            self._refresh_header_from_manifest()
            self.show_feedback(f"✓ {msg}", level="success")
        else:
            self.show_feedback(msg, level="error")

    def on_dialog_response(self, dialog, response_id):
        if self.is_busy:
            return
        if self._pulse_source_id is not None:
            GLib.source_remove(self._pulse_source_id)
            self._pulse_source_id = None
        self.destroy()


class DropInstallerWindow(Gtk.Window):
    def __init__(self, initial_path: Optional[Path] = None):
        super().__init__(title="Drop Installer")
        self.set_default_size(720, 600)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Desktop Dock & Taskbar Grouping (WM_CLASS must match StartupWMClass in .desktop)
        self.set_wmclass("drop-installer", "drop-installer")
        self.set_role("drop-installer")
        self.set_default_icon_name("drop-installer")
        self.set_icon_name("drop-installer")

        # Load window icon directly from installed icon files if present
        base_dir = Path(os.environ.get("DROP_INSTALLER_BASE_DIR", str(Path.home() / ".local")))
        for icon_cand in (
            base_dir / "share" / "icons" / "hicolor" / "scalable" / "apps" / "drop-installer.svg",
            base_dir / "share" / "icons" / "hicolor" / "256x256" / "apps" / "drop-installer.png"
        ):
            if icon_cand.exists():
                try:
                    self.set_icon_from_file(str(icon_cand))
                    break
                except Exception:
                    pass

        # Apply CSS
        self.apply_styles()

        # State
        self.current_candidate: Optional[AppCandidate] = None
        self.chosen_icon_path: Optional[Path] = None
        self.updating_existing_app_id: Optional[str] = None
        self.update_results: Dict[str, UpdateCheckResult] = {}
        self.checking_updates: bool = False

        # Main Layout
        self.setup_header()
        self.setup_body()

        # Initial path check
        if initial_path:
            GLib.idle_add(self.load_target_path, initial_path)

    def apply_styles(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def setup_header(self):
        self.header = Gtk.HeaderBar()
        self.header.set_show_close_button(True)
        self.header.set_title("Drop Installer")
        self.header.set_subtitle("Pop!_OS Application Launcher & Installer")
        self.set_titlebar(self.header)

        # Stack Switcher
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)

        self.stack_switcher = Gtk.StackSwitcher()
        self.stack_switcher.set_stack(self.stack)
        self.header.set_custom_title(self.stack_switcher)

    def setup_body(self):
        # Page 1: Install
        self.install_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.install_page.set_border_width(20)

        # Sub-stack for Install: 1. DropZone, 2. PreviewCard, 3. Progress / Success
        self.install_sub_stack = Gtk.Stack()
        self.install_sub_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.build_drop_zone_view()
        self.build_preview_view()
        self.build_status_view()

        self.install_sub_stack.add_named(self.drop_zone_box, "drop_zone")
        self.install_sub_stack.add_named(self.preview_box, "preview")
        self.install_sub_stack.add_named(self.status_box, "status")

        self.install_page.pack_start(self.install_sub_stack, True, True, 0)
        self.stack.add_titled(self.install_page, "install_tab", "Install App")

        # Page 2: Installed Apps Manager
        self.build_installed_apps_view()
        self.stack.add_titled(self.installed_page, "installed_tab", "Installed Apps")

        self.add(self.stack)

    # -------------------------------------------------------------------------
    # View 1: Drop Zone
    # -------------------------------------------------------------------------
    def build_drop_zone_view(self):
        self.drop_zone_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.drop_zone_box.set_valign(Gtk.Align.CENTER)
        self.drop_zone_box.set_halign(Gtk.Align.CENTER)

        # EventBox for Drag & Drop
        self.drop_event_box = Gtk.EventBox()
        self.drop_event_box.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [],
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.drop_event_box.drag_dest_add_uri_targets()

        self.drop_event_box.connect("drag-motion", self.on_drag_motion)
        self.drop_event_box.connect("drag-leave", self.on_drag_leave)
        self.drop_event_box.connect("drag-data-received", self.on_drag_data_received)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        inner_box.get_style_context().add_class("drop-zone")
        inner_box.set_size_request(580, 320)
        inner_box.set_valign(Gtk.Align.CENTER)
        inner_box.set_halign(Gtk.Align.CENTER)

        # Icon
        icon_image = Gtk.Image.new_from_icon_name("system-software-install", Gtk.IconSize.DIALOG)
        icon_image.set_pixel_size(80)
        inner_box.pack_start(icon_image, False, False, 10)

        # Label
        lbl_title = Gtk.Label()
        lbl_title.set_markup("<span size='large' weight='bold'>Drag &amp; Drop Application Here</span>")
        inner_box.pack_start(lbl_title, False, False, 0)

        lbl_desc = Gtk.Label()
        lbl_desc.set_markup(
            "<span alpha='70%'>Drop any <b>.tar.gz</b>, <b>.zip</b>, <b>.tar.xz</b>, <b>.deb</b>, <b>.AppImage</b>, "
            "or an <b>unpacked app folder</b></span>"
        )
        lbl_desc.set_justify(Gtk.Justification.CENTER)
        inner_box.pack_start(lbl_desc, False, False, 0)

        # Divider or buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        btn_file = Gtk.Button(label="Select File…")
        btn_file.connect("clicked", self.on_select_file_clicked)
        btn_box.pack_start(btn_file, False, False, 0)

        btn_folder = Gtk.Button(label="Select Folder…")
        btn_folder.connect("clicked", self.on_select_folder_clicked)
        btn_box.pack_start(btn_folder, False, False, 0)

        inner_box.pack_start(btn_box, False, False, 10)

        self.drop_event_box.add(inner_box)
        self.drop_zone_box.pack_start(self.drop_event_box, True, True, 0)

    # -------------------------------------------------------------------------
    # View 2: Preview & Configuration Card
    # -------------------------------------------------------------------------
    def build_preview_view(self):
        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        
        # Scrollable container for preview
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.get_style_context().add_class("card")
        card.set_margin_top(10)
        card.set_margin_bottom(10)

        # Update detection banner (shown when dropping a newer version of an installed app)
        self.preview_update_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.preview_update_banner.get_style_context().add_class("info-banner")
        self.preview_update_banner.set_no_show_all(True)
        self.lbl_preview_update_banner = Gtk.Label()
        self.lbl_preview_update_banner.set_line_wrap(True)
        self.lbl_preview_update_banner.set_halign(Gtk.Align.START)
        self.preview_update_banner.pack_start(self.lbl_preview_update_banner, True, True, 0)
        card.pack_start(self.preview_update_banner, False, False, 0)

        # Header Row: Icon + Name + Badge
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        
        # Icon Frame
        icon_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.preview_icon_image = Gtk.Image()
        self.preview_icon_image.set_pixel_size(72)
        icon_box.pack_start(self.preview_icon_image, False, False, 0)

        self.btn_change_icon = Gtk.Button(label="Change Icon")
        self.btn_change_icon.set_size_request(80, -1)
        self.btn_change_icon.connect("clicked", self.on_change_icon_clicked)
        icon_box.pack_start(self.btn_change_icon, False, False, 0)
        header_row.pack_start(icon_box, False, False, 0)

        # Name and Kind info
        info_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.lbl_kind_badge = Gtk.Label()
        self.lbl_kind_badge.set_halign(Gtk.Align.START)
        self.lbl_kind_badge.get_style_context().add_class("badge")
        info_col.pack_start(self.lbl_kind_badge, False, False, 0)

        self.entry_name = Gtk.Entry()
        self.entry_name.set_placeholder_text("Application Name")
        info_col.pack_start(self.entry_name, False, False, 0)

        self.lbl_source_info = Gtk.Label()
        self.lbl_source_info.set_line_wrap(True)
        self.lbl_source_info.set_halign(Gtk.Align.START)
        self.lbl_source_info.get_style_context().add_class("subtitle")
        info_col.pack_start(self.lbl_source_info, False, False, 0)

        header_row.pack_start(info_col, True, True, 0)
        card.pack_start(header_row, False, False, 0)

        # Separator
        card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        # Form Grid
        grid = Gtk.Grid()
        grid.set_row_spacing(12)
        grid.set_column_spacing(16)

        # Row 0: Category
        lbl_cat = Gtk.Label(label="Category:")
        lbl_cat.set_halign(Gtk.Align.END)
        grid.attach(lbl_cat, 0, 0, 1, 1)

        self.combo_category = Gtk.ComboBoxText()
        categories = ["Development", "Utility", "Graphics", "AudioVideo", "Game", "Office", "System", "Network"]
        for cat in categories:
            self.combo_category.append_text(cat)
        self.combo_category.set_active(0)
        grid.attach(self.combo_category, 1, 0, 2, 1)

        # Row 1: Executable Target (for archives & folders)
        self.lbl_exec = Gtk.Label(label="Main Executable:")
        self.lbl_exec.set_halign(Gtk.Align.END)
        grid.attach(self.lbl_exec, 0, 1, 1, 1)

        self.combo_exec = Gtk.ComboBoxText()
        grid.attach(self.combo_exec, 1, 1, 1, 1)

        self.btn_browse_exec = Gtk.Button(label="Browse…")
        self.btn_browse_exec.connect("clicked", self.on_browse_exec_clicked)
        grid.attach(self.btn_browse_exec, 2, 1, 1, 1)

        # Row 2: Companion Launchers (e.g., NetLogo 3D)
        self.lbl_companion = Gtk.Label(label="Also Create:")
        self.lbl_companion.set_halign(Gtk.Align.END)
        grid.attach(self.lbl_companion, 0, 2, 1, 1)

        self.box_companion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        grid.attach(self.box_companion, 1, 2, 2, 1)
        self.companion_checkboxes = []

        # Row 3: Options
        lbl_opts = Gtk.Label(label="Options:")
        lbl_opts.set_halign(Gtk.Align.END)
        grid.attach(lbl_opts, 0, 3, 1, 1)

        opts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.chk_no_sandbox = Gtk.CheckButton(label="Use --no-sandbox (recommended for Electron apps like Arduino IDE 2)")
        opts_box.pack_start(self.chk_no_sandbox, False, False, 0)

        self.chk_move_source = Gtk.CheckButton(label="Move original folder / delete archive after installation")
        opts_box.pack_start(self.chk_move_source, False, False, 0)

        self.chk_open_after = Gtk.CheckButton(label="Launch app immediately after installation")
        self.chk_open_after.set_active(True)
        opts_box.pack_start(self.chk_open_after, False, False, 0)

        grid.attach(opts_box, 1, 3, 2, 1)

        card.pack_start(grid, False, False, 0)
        scroller.add(card)
        self.preview_box.pack_start(scroller, True, True, 0)

        # Bottom Buttons
        action_bar = Gtk.ActionBar()
        self.btn_cancel = Gtk.Button(label="Cancel")
        self.btn_cancel.connect("clicked", self.on_cancel_preview)
        action_bar.pack_start(self.btn_cancel)

        self.btn_install = Gtk.Button(label="Install to Launcher")
        self.btn_install.get_style_context().add_class("suggested-action")
        self.btn_install.connect("clicked", self.on_install_clicked)
        action_bar.pack_end(self.btn_install)

        self.preview_box.pack_end(action_bar, False, False, 0)

    # -------------------------------------------------------------------------
    # View 3: Installing & Status View
    # -------------------------------------------------------------------------
    def build_status_view(self):
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.status_box.set_valign(Gtk.Align.CENTER)
        self.status_box.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        self.status_box.pack_start(self.spinner, False, False, 0)

        self.lbl_status = Gtk.Label()
        self.lbl_status.set_markup("<span size='large' weight='bold'>Installing Application…</span>")
        self.status_box.pack_start(self.lbl_status, False, False, 0)

        self.lbl_status_detail = Gtk.Label()
        self.lbl_status_detail.get_style_context().add_class("subtitle")
        self.status_box.pack_start(self.lbl_status_detail, False, False, 0)

        # Success Action Buttons (initially hidden)
        self.status_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.status_btn_box.set_halign(Gtk.Align.CENTER)

        self.btn_launch_installed = Gtk.Button(label="Launch App")
        self.btn_launch_installed.get_style_context().add_class("suggested-action")
        self.btn_launch_installed.connect("clicked", self.on_launch_just_installed)
        self.status_btn_box.pack_start(self.btn_launch_installed, False, False, 0)

        self.btn_install_another = Gtk.Button(label="Install Another App")
        self.btn_install_another.connect("clicked", self.reset_to_drop_zone)
        self.status_btn_box.pack_start(self.btn_install_another, False, False, 0)

        self.status_box.pack_start(self.status_btn_box, False, False, 10)

    # -------------------------------------------------------------------------
    # View 4: Installed Apps Manager
    # -------------------------------------------------------------------------
    def build_installed_apps_view(self):
        self.installed_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.installed_page.set_border_width(16)

        # Toolbar
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        lbl_heading = Gtk.Label()
        lbl_heading.set_markup("<span size='large' weight='bold'>Manage Installed Applications</span>")
        top_bar.pack_start(lbl_heading, False, False, 0)

        btn_refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        btn_refresh.set_tooltip_text("Refresh List")
        btn_refresh.connect("clicked", lambda b: self.refresh_installed_apps_list())
        top_bar.pack_end(btn_refresh, False, False, 0)

        self.btn_check_all_updates = Gtk.Button(label="Check Updates")
        self.btn_check_all_updates.set_image(
            Gtk.Image.new_from_icon_name("software-update-available-symbolic", Gtk.IconSize.BUTTON)
        )
        self.btn_check_all_updates.set_always_show_image(True)
        self.btn_check_all_updates.set_tooltip_text("Check for updates on all apps with configured update sources or APT")
        self.btn_check_all_updates.connect("clicked", self.on_check_all_updates_clicked)
        top_bar.pack_end(self.btn_check_all_updates, False, False, 0)

        self.installed_page.pack_start(top_bar, False, False, 0)

        self.lbl_updates_summary = Gtk.Label()
        self.lbl_updates_summary.set_halign(Gtk.Align.START)
        self.lbl_updates_summary.set_no_show_all(True)
        self.installed_page.pack_start(self.lbl_updates_summary, False, False, 0)

        # Scrolled list
        self.installed_scroller = Gtk.ScrolledWindow()
        self.installed_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.installed_listbox = Gtk.ListBox()
        self.installed_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.installed_scroller.add(self.installed_listbox)
        self.installed_page.pack_start(self.installed_scroller, True, True, 0)

        self.refresh_installed_apps_list()

    def refresh_installed_apps_list(self):
        # Clear existing rows
        for child in self.installed_listbox.get_children():
            self.installed_listbox.remove(child)

        manifest = load_manifest()
        if not manifest:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_halign(Gtk.Align.CENTER)
            empty_box.set_margin_top(80)

            empty_icon = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
            empty_icon.set_pixel_size(64)
            empty_box.pack_start(empty_icon, False, False, 0)

            lbl_empty = Gtk.Label()
            lbl_empty.set_markup("<span weight='bold'>No apps installed via Drop Installer yet</span>")
            empty_box.pack_start(lbl_empty, False, False, 0)

            lbl_sub = Gtk.Label(label="Drag and drop any .tar.gz, .zip, .deb, .AppImage, or folder to install it.")
            lbl_sub.get_style_context().add_class("subtitle")
            empty_box.pack_start(lbl_sub, False, False, 0)

            self.installed_listbox.add(empty_box)
            self.installed_listbox.show_all()
            return

        for app_id, app_info in manifest.items():
            row = Gtk.ListBoxRow()
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            card.set_border_width(12)

            # Icon
            icon_img = Gtk.Image()
            icon_img.set_pixel_size(44)
            icon_path = app_info.get("icon_path")
            if icon_path and Path(icon_path).exists():
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), 44, 44, True)
                    icon_img.set_from_pixbuf(pixbuf)
                except Exception:
                    icon_img.set_from_icon_name("application-x-executable", Gtk.IconSize.DND)
            else:
                icon_img.set_from_icon_name("application-x-executable", Gtk.IconSize.DND)
            card.pack_start(icon_img, False, False, 0)

            # Name and Path
            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            
            lbl_name = Gtk.Label()
            lbl_name.set_markup(f"<b>{GLib.markup_escape_text(app_info.get('name', app_id))}</b>")
            title_box.pack_start(lbl_name, False, False, 0)

            badge = Gtk.Label(label=app_info.get("kind", "portable").upper())
            badge.get_style_context().add_class("badge")
            title_box.pack_start(badge, False, False, 0)

            ver_str = app_info.get("version")
            if ver_str:
                ver_badge = Gtk.Label(label=f"v{ver_str}")
                ver_badge.get_style_context().add_class("version-badge")
                title_box.pack_start(ver_badge, False, False, 0)

            upd_res = self.update_results.get(app_id)
            if upd_res and upd_res.has_update:
                upd_label = f"↑ v{upd_res.latest_version}" if upd_res.latest_version else "↑ Update Available"
                upd_badge = Gtk.Label(label=upd_label)
                upd_badge.get_style_context().add_class("update-badge")
                title_box.pack_start(upd_badge, False, False, 0)

            text_box.pack_start(title_box, False, False, 0)

            install_path = app_info.get("installed_dir") or app_info.get("source_path") or (
                f"APT package: {app_info.get('package_name')}" if app_info.get("kind") == "deb" else ""
            )
            lbl_path = Gtk.Label(label=str(install_path))
            lbl_path.set_halign(Gtk.Align.START)
            lbl_path.get_style_context().add_class("subtitle")
            lbl_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl_path.set_max_width_chars(45)
            text_box.pack_start(lbl_path, False, False, 0)

            # Update source hint
            upd_src = app_info.get("update_source")
            if isinstance(upd_src, dict) and upd_src.get("type") not in (None, "", "none"):
                st = upd_src.get("type")
                if st == "github":
                    src_txt = f"Auto-update: GitHub ({upd_src.get('repo', '')})"
                elif st == "url":
                    src_txt = f"Auto-update: URL"
                elif st == "apt":
                    src_txt = f"Auto-update: APT ({upd_src.get('package_name', app_id)})"
                else:
                    src_txt = ""
                if src_txt:
                    lbl_src = Gtk.Label()
                    lbl_src.set_markup(f"<span size='small' alpha='75%'>{GLib.markup_escape_text(src_txt)}</span>")
                    lbl_src.set_halign(Gtk.Align.START)
                    text_box.pack_start(lbl_src, False, False, 0)

            # Companion launchers note
            companions = app_info.get("additional_launchers", [])
            if companions:
                comp_names = [c.get("name", "Companion") for c in companions[:3]]
                comp_summary = ", ".join(comp_names)
                if len(companions) > 3:
                    comp_summary += f" (+{len(companions)-3} more)"
                lbl_comp = Gtk.Label()
                lbl_comp.set_markup(f"<span size='small' color='#48b9c7'><b>Also includes:</b> {GLib.markup_escape_text(comp_summary)}</span>")
                lbl_comp.set_halign(Gtk.Align.START)
                text_box.pack_start(lbl_comp, False, False, 0)

            card.pack_start(text_box, True, True, 0)

            # Buttons
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            btn_launch = Gtk.Button.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)
            btn_launch.set_tooltip_text("Launch Application")
            desktop_file = app_info.get("desktop_file")
            exec_path = app_info.get("exec_path")
            pkg_name = app_info.get("package_name")
            btn_launch.connect("clicked", lambda b, d=desktop_file, e=exec_path, p=pkg_name: self.launch_app(d, e, p))
            btn_box.pack_start(btn_launch, False, False, 0)

            btn_update = Gtk.Button.new_from_icon_name("software-update-available-symbolic", Gtk.IconSize.BUTTON)
            btn_update.set_tooltip_text("Update Application / Configure Update Source")
            if upd_res and upd_res.has_update:
                btn_update.get_style_context().add_class("suggested-action")
            btn_update.connect("clicked", lambda b, aid=app_id: self.open_update_dialog(aid))
            btn_box.pack_start(btn_update, False, False, 0)

            btn_edit_icon = Gtk.Button.new_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.BUTTON)
            btn_edit_icon.set_tooltip_text("Change Application Icon(s)")
            btn_edit_icon.connect("clicked", lambda b, aid=app_id: self.open_edit_icons_dialog(aid))
            btn_box.pack_start(btn_edit_icon, False, False, 0)

            if install_path and Path(install_path).exists():
                btn_open_folder = Gtk.Button.new_from_icon_name("folder-symbolic", Gtk.IconSize.BUTTON)
                btn_open_folder.set_tooltip_text("Open Installed Folder")
                btn_open_folder.connect("clicked", lambda b, p=install_path: self.open_folder(p))
                btn_box.pack_start(btn_open_folder, False, False, 0)

            btn_uninstall = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            btn_uninstall.set_tooltip_text("Uninstall Application")
            btn_uninstall.get_style_context().add_class("danger-button")
            btn_uninstall.connect("clicked", lambda b, a=app_id, n=app_info.get("name", app_id): self.confirm_uninstall(a, n))
            btn_box.pack_start(btn_uninstall, False, False, 0)

            card.pack_end(btn_box, False, False, 0)

            row.add(card)
            self.installed_listbox.add(row)

        self.installed_listbox.show_all()

    def launch_app(self, desktop_file: Optional[str], exec_path: Optional[str], package_name: Optional[str] = None):
        if desktop_file and Path(desktop_file).exists():
            subprocess.Popen(["gtk-launch", Path(desktop_file).stem])
        elif exec_path and Path(exec_path).exists():
            subprocess.Popen([str(exec_path)])
        elif package_name:
            # Try launching system-installed .deb desktop entry or binary
            sys_desktop = Path("/usr/share/applications") / f"{package_name}.desktop"
            if sys_desktop.exists():
                subprocess.Popen(["gtk-launch", package_name])
            else:
                subprocess.Popen([package_name])

    def open_folder(self, folder_path: str):
        target = Path(folder_path)
        if target.is_file():
            target = target.parent
        subprocess.Popen(["xdg-open", str(target)])

    def confirm_uninstall(self, app_id: str, app_name: str):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"Uninstall '{app_name}'?"
        )
        dialog.format_secondary_text(
            "This will remove the application files, launcher shortcut, and icon from your system."
        )
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            ok, msg = uninstall_app(app_id)
            if ok:
                self.refresh_installed_apps_list()
            else:
                err_dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="Uninstall Failed"
                )
                err_dialog.format_secondary_text(msg)
                err_dialog.run()
                err_dialog.destroy()

    def open_edit_icons_dialog(self, app_id: str):
        dialog = EditIconsDialog(self, app_id)
        dialog.run()
        dialog.destroy()
        self.refresh_installed_apps_list()

    def open_update_dialog(self, app_id: str):
        dialog = UpdateAppDialog(self, app_id, initial_check=self.update_results.get(app_id))
        dialog.run()
        dialog.destroy()
        self.refresh_installed_apps_list()

    def on_check_all_updates_clicked(self, button):
        if self.checking_updates:
            return
        self.checking_updates = True
        self.btn_check_all_updates.set_sensitive(False)
        self.btn_check_all_updates.set_label("Checking…")
        self.lbl_updates_summary.set_markup("<span color='#48b9c7'>Checking configured update sources &amp; APT packages…</span>")
        self.lbl_updates_summary.show()

        def worker():
            try:
                results = check_all_updates()
            except Exception as e:
                results = {}
                GLib.idle_add(self.on_check_all_updates_finished, results, str(e))
                return
            GLib.idle_add(self.on_check_all_updates_finished, results, None)

        threading.Thread(target=worker, daemon=True).start()

    def on_check_all_updates_finished(self, results: Dict[str, UpdateCheckResult], err: Optional[str]):
        self.checking_updates = False
        self.btn_check_all_updates.set_sensitive(True)
        self.btn_check_all_updates.set_label("Check Updates")

        if err:
            self.lbl_updates_summary.set_markup(f"<span color='#e05555'>Update check failed: {GLib.markup_escape_text(err)}</span>")
            self.lbl_updates_summary.show()
            return

        self.update_results.update(results)
        self.refresh_installed_apps_list()

        if not results:
            self.lbl_updates_summary.set_markup(
                "<span alpha='75%'>No online update sources configured yet. Click the update button on any app to configure GitHub, URL, or APT updates.</span>"
            )
        else:
            avail = [r for r in results.values() if r.has_update]
            if avail:
                self.lbl_updates_summary.set_markup(
                    f"<span color='#3da853'><b>↑ {len(avail)} update(s) available!</b> Click the highlighted update button on an app to install it.</span>"
                )
            else:
                self.lbl_updates_summary.set_markup(
                    f"<span color='#3da853'>✓ All {len(results)} checked app(s) are up to date.</span>"
                )
        self.lbl_updates_summary.show()

    # -------------------------------------------------------------------------
    # Drag & Drop Handlers
    # -------------------------------------------------------------------------
    def on_drag_motion(self, widget, context, x, y, time):
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def on_drag_leave(self, widget, context, time):
        pass

    def on_drag_data_received(self, widget, context, x, y, data, info, time):
        uris = data.get_uris()
        if uris:
            raw_path = uris[0]
            parsed = urllib.parse.urlparse(raw_path)
            local_path = Path(urllib.parse.unquote(parsed.path))
            self.load_target_path(local_path)
        context.finish(True, False, time)

    def on_select_file_clicked(self, button):
        chooser = Gtk.FileChooserDialog(
            title="Select Application File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        # Filters
        filter_all = Gtk.FileFilter()
        filter_all.set_name("All Supported App Files (.deb, .tar.gz, .zip, .AppImage)")
        for pattern in ("*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.zip", "*.deb", "*.AppImage", "*.appimage"):
            filter_all.add_pattern(pattern)
        chooser.add_filter(filter_all)

        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            filename = chooser.get_filename()
            chooser.destroy()
            self.load_target_path(Path(filename))
        else:
            chooser.destroy()

    def on_select_folder_clicked(self, button):
        chooser = Gtk.FileChooserDialog(
            title="Select Application Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            foldername = chooser.get_filename()
            chooser.destroy()
            self.load_target_path(Path(foldername))
        else:
            chooser.destroy()

    # -------------------------------------------------------------------------
    # Target Loading & Inspection
    # -------------------------------------------------------------------------
    def load_target_path(self, path: Path):
        self.lbl_status.set_markup("<span size='large' weight='bold'>Inspecting Application…</span>")
        self.lbl_status_detail.set_text(f"Scanning {path.name} for executables and icons...")
        self.spinner.start()
        self.status_btn_box.hide()
        self.install_sub_stack.set_visible_child_name("status")

        def inspect_worker():
            try:
                candidate = inspect_target(path)
                GLib.idle_add(self.on_inspect_success, candidate)
            except Exception as e:
                GLib.idle_add(self.on_inspect_error, str(e))

        threading.Thread(target=inspect_worker, daemon=True).start()

    def on_inspect_error(self, error_message: str):
        self.spinner.stop()
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Could not inspect target"
        )
        dialog.format_secondary_text(error_message)
        dialog.run()
        dialog.destroy()
        self.reset_to_drop_zone()

    def on_inspect_success(self, candidate: AppCandidate):
        try:
            self.spinner.stop()
            self.current_candidate = candidate
            self.chosen_icon_path = candidate.selected_icon.path if candidate.selected_icon else None

            # Check if this matches an already-installed application
            existing_match = find_matching_installed_app(candidate)
            if existing_match:
                self.updating_existing_app_id = existing_match["app_id"]
                old_settings = parse_installed_settings(existing_match)
                display_name = old_settings["name"] or candidate.name
                old_ver = existing_match.get("version", "1.0")
                self.lbl_preview_update_banner.set_markup(
                    f"<b>Updating Existing Installation:</b> {GLib.markup_escape_text(display_name)} "
                    f"(v{GLib.markup_escape_text(old_ver)} → v{GLib.markup_escape_text(candidate.version)}). "
                    f"Your custom settings and icons will be preserved."
                )
                self.preview_update_banner.show_all()
                self.btn_install.set_label("Update Application")

                prev_icon = existing_match.get("icon_path")
                if prev_icon and Path(prev_icon).exists() and existing_match.get("custom_icon"):
                    self.chosen_icon_path = Path(prev_icon)
            else:
                self.updating_existing_app_id = None
                old_settings = None
                self.preview_update_banner.hide()
                self.btn_install.set_label("Install to Launcher")

            # Populate Preview Card
            self.entry_name.set_text(old_settings["name"] if old_settings and old_settings["name"] else candidate.name)
            self.lbl_kind_badge.set_text(f"{candidate.kind.upper()} • v{candidate.version}")
            self.lbl_source_info.set_text(f"Source: {candidate.source_path}")

            # Category
            cat_source = old_settings["category"] if old_settings and old_settings["category"] else candidate.categories
            cat_lower = cat_source.lower()
            active_cat_idx = 0
            cat_list = ["Development", "Utility", "Graphics", "AudioVideo", "Game", "Office", "System", "Network"]
            for idx, cat_item in enumerate(cat_list):
                if cat_item.lower() in cat_lower:
                    active_cat_idx = idx
                    break
            self.combo_category.set_active(active_cat_idx)

            # Clear previous companion launchers
            for child in self.box_companion.get_children():
                self.box_companion.remove(child)
            self.companion_checkboxes.clear()

            # Executable combo
            self.combo_exec.remove_all()
            if candidate.kind in ("archive", "folder"):
                self.lbl_exec.show()
                self.combo_exec.show()
                self.btn_browse_exec.show()
                for cand in candidate.executables:
                    display_label = f"{cand.relative_path} (Score: {cand.score})"
                    self.combo_exec.append(cand.relative_path, display_label)
                if old_settings and old_settings["exec_rel"] and any(c.relative_path == old_settings["exec_rel"] for c in candidate.executables):
                    self.combo_exec.set_active_id(old_settings["exec_rel"])
                elif candidate.selected_executable:
                    self.combo_exec.set_active_id(candidate.selected_executable.relative_path)
                elif candidate.executables:
                    self.combo_exec.set_active(0)

                # Populate companion launchers if multiple executables detected (e.g. NetLogo + NetLogo 3D)
                primary_rel = self.combo_exec.get_active_id()
                old_comp_rels = set(old_settings["companion_rels"]) if old_settings else set()
                for cand in candidate.executables:
                    if cand.relative_path == primary_rel:
                        continue
                    clean_display_name = format_executable_display_name(cand.name, base_app_name=candidate.name)
                    chk = Gtk.CheckButton(label=f"Create launcher for '{clean_display_name}' ({cand.relative_path})")
                    if old_comp_rels:
                        if cand.relative_path in old_comp_rels:
                            chk.set_active(True)
                    else:
                        # Auto-check major companion apps (e.g. NetLogo 3D, HubNet Client), but not scripts or redundant helpers
                        is_script = cand.name.endswith(".sh") or cand.is_script
                        is_headless = "headless" in cand.name.lower()
                        if not is_script and not is_headless and any(k in cand.name.lower() for k in ("3d", "client", "search")):
                            chk.set_active(True)
                    self.box_companion.pack_start(chk, False, False, 0)
                    self.companion_checkboxes.append((cand.relative_path, chk))

                if self.companion_checkboxes:
                    self.lbl_companion.show()
                    self.box_companion.show_all()
                else:
                    self.lbl_companion.hide()
                    self.box_companion.hide()
            else:
                self.lbl_exec.hide()
                self.combo_exec.hide()
                self.btn_browse_exec.hide()
                self.lbl_companion.hide()
                self.box_companion.hide()

            # Options
            self.chk_no_sandbox.set_active(
                old_settings["use_no_sandbox"] if old_settings is not None else candidate.is_electron
            )
            # Default move_source to True if dragging an existing folder or archive from Documents/Downloads
            src_parent = str(candidate.source_path.parent).lower()
            if "documents" in src_parent or "downloads" in src_parent:
                self.chk_move_source.set_active(True)
            else:
                self.chk_move_source.set_active(False)

            # Icon display
            self.update_preview_icon()

            self.install_sub_stack.set_visible_child_name("preview")
        except Exception as e:
            self.on_inspect_error(f"Failed to prepare preview: {e}")

    def update_preview_icon(self):
        if self.chosen_icon_path and self.chosen_icon_path.exists():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(self.chosen_icon_path), 72, 72, True)
                self.preview_icon_image.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        self.preview_icon_image.set_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)

    def on_change_icon_clicked(self, button):
        chooser = Gtk.FileChooserDialog(
            title="Choose Custom Icon",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        filter_img = Gtk.FileFilter()
        filter_img.set_name("Images (*.svg, *.png, *.ico)")
        for pat in ("*.svg", "*.png", "*.ico", "*.xpm"):
            filter_img.add_pattern(pat)
        chooser.add_filter(filter_img)

        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            chosen = chooser.get_filename()
            self.chosen_icon_path = Path(chosen)
            self.update_preview_icon()
        chooser.destroy()

    def on_browse_exec_clicked(self, button):
        if not self.current_candidate or not self.current_candidate.extracted_root:
            return
        chooser = Gtk.FileChooserDialog(
            title="Select Main Executable",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        chooser.set_current_folder(str(self.current_candidate.extracted_root))
        response = chooser.run()
        if response == Gtk.ResponseType.OK:
            chosen = Path(chooser.get_filename())
            try:
                rel = str(chosen.relative_to(self.current_candidate.extracted_root))
                self.combo_exec.append(rel, rel)
                self.combo_exec.set_active_id(rel)
            except ValueError:
                pass
        chooser.destroy()

    def on_cancel_preview(self, button):
        if self.current_candidate:
            self.current_candidate.cleanup_staging()
            self.current_candidate = None
        self.reset_to_drop_zone()

    def reset_to_drop_zone(self, button=None):
        self.current_candidate = None
        self.chosen_icon_path = None
        self.updating_existing_app_id = None
        self.install_sub_stack.set_visible_child_name("drop_zone")

    # -------------------------------------------------------------------------
    # Installation Execution
    # -------------------------------------------------------------------------
    def on_install_clicked(self, button):
        if not self.current_candidate:
            return

        name = self.entry_name.get_text().strip() or self.current_candidate.name
        cat = self.combo_category.get_active_text() + ";"
        exec_rel = self.combo_exec.get_active_id()
        no_sandbox = self.chk_no_sandbox.get_active()
        move_source = self.chk_move_source.get_active()
        open_after = self.chk_open_after.get_active()
        target_app_id = self.updating_existing_app_id
        is_update = bool(target_app_id)

        # Collect any selected companion launchers
        additional_execs = [
            rel_path for rel_path, chk in self.companion_checkboxes
            if chk.get_active() and rel_path != exec_rel
        ]

        # Switch to status progress view
        action_ing = "Updating" if is_update else "Installing"
        self.lbl_status.set_markup(f"<span size='large' weight='bold'>{action_ing} {GLib.markup_escape_text(name)}…</span>")
        self.lbl_status_detail.set_text("Writing launcher entry, installing icon and linking binary...")
        self.spinner.start()
        self.status_btn_box.hide()
        self.install_sub_stack.set_visible_child_name("status")

        def install_worker():
            try:
                ok, msg = install_candidate(
                    self.current_candidate,
                    name=name,
                    category=cat,
                    executable_rel=exec_rel,
                    icon_source=self.chosen_icon_path,
                    use_no_sandbox=no_sandbox,
                    move_original=move_source,
                    additional_executables=additional_execs,
                    target_app_id=target_app_id,
                    preserve_existing_icons=is_update
                )
            except Exception as e:
                ok, msg = False, f"Unexpected error during installation: {e}"
            GLib.idle_add(self.on_install_finished, ok, msg, name, open_after, is_update, target_app_id)

        threading.Thread(target=install_worker, daemon=True).start()

    def on_install_finished(self, ok: bool, message: str, name: str, open_after: bool, is_update: bool = False, target_app_id: Optional[str] = None):
        self.spinner.stop()
        effective_id = target_app_id or (self.current_candidate.app_id if self.current_candidate else None)
        if ok:
            verb = "Updated" if is_update else "Installed"
            self.lbl_status.set_markup(f"<span size='large' weight='bold' color='#3da853'>✓ {GLib.markup_escape_text(name)} {verb}!</span>")
            self.lbl_status_detail.set_markup(
                f"Available now in the <b>Super key launcher</b> and Applications menu.\n\n{GLib.markup_escape_text(message)}"
            )
            self.status_btn_box.show_all()
            if effective_id:
                self.update_results.pop(effective_id, None)
            self.refresh_installed_apps_list()

            if open_after and effective_id:
                manifest = load_manifest()
                app_info = manifest.get(effective_id)
                if app_info:
                    self.launch_app(app_info.get("desktop_file"), app_info.get("exec_path"), app_info.get("package_name"))
        else:
            title = "Update Failed" if is_update else "Installation Failed"
            self.lbl_status.set_markup(f"<span size='large' weight='bold' color='#e05555'>{title}</span>")
            self.lbl_status_detail.set_text(message)
            self.status_btn_box.show_all()

    def on_launch_just_installed(self, button):
        effective_id = self.updating_existing_app_id or (self.current_candidate.app_id if self.current_candidate else None)
        if effective_id:
            manifest = load_manifest()
            app_info = manifest.get(effective_id)
            if app_info:
                self.launch_app(app_info.get("desktop_file"), app_info.get("exec_path"), app_info.get("package_name"))
