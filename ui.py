"""
drop-installer ui: GTK 3 graphical user interface with drag-and-drop support,
app configuration preview, and installed apps management for Pop!_OS.
"""

import os
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

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
    update_launcher_icon
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
        self.lbl_feedback.set_markup(f"<span color='{color}'><b>{text}</b></span>")
        self.lbl_feedback.show()


class DropInstallerWindow(Gtk.Window):
    def __init__(self, initial_path: Optional[Path] = None):
        super().__init__(title="Drop Installer")
        self.set_default_size(720, 600)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Apply CSS
        self.apply_styles()

        # State
        self.current_candidate: Optional[AppCandidate] = None
        self.chosen_icon_path: Optional[Path] = None

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
        self.installed_page.pack_start(top_bar, False, False, 0)

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
            lbl_name.set_markup(f"<b>{app_info.get('name', app_id)}</b>")
            title_box.pack_start(lbl_name, False, False, 0)

            badge = Gtk.Label(label=app_info.get("kind", "portable").upper())
            badge.get_style_context().add_class("badge")
            title_box.pack_start(badge, False, False, 0)
            text_box.pack_start(title_box, False, False, 0)

            install_path = app_info.get("installed_dir") or app_info.get("source_path", "")
            lbl_path = Gtk.Label(label=str(install_path))
            lbl_path.set_halign(Gtk.Align.START)
            lbl_path.get_style_context().add_class("subtitle")
            lbl_path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lbl_path.set_max_width_chars(45)
            text_box.pack_start(lbl_path, False, False, 0)

            # Companion launchers note
            companions = app_info.get("additional_launchers", [])
            if companions:
                comp_names = [c.get("name", "Companion") for c in companions[:3]]
                comp_summary = ", ".join(comp_names)
                if len(companions) > 3:
                    comp_summary += f" (+{len(companions)-3} more)"
                lbl_comp = Gtk.Label()
                lbl_comp.set_markup(f"<span size='small' color='#48b9c7'><b>Also includes:</b> {comp_summary}</span>")
                lbl_comp.set_halign(Gtk.Align.START)
                text_box.pack_start(lbl_comp, False, False, 0)

            card.pack_start(text_box, True, True, 0)

            # Buttons
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            btn_launch = Gtk.Button.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON)
            btn_launch.set_tooltip_text("Launch Application")
            desktop_file = app_info.get("desktop_file")
            exec_path = app_info.get("exec_path")
            btn_launch.connect("clicked", lambda b, d=desktop_file, e=exec_path: self.launch_app(d, e))
            btn_box.pack_start(btn_launch, False, False, 0)

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

    def launch_app(self, desktop_file: Optional[str], exec_path: Optional[str]):
        if desktop_file and Path(desktop_file).exists():
            subprocess.Popen(["gtk-launch", Path(desktop_file).stem])
        elif exec_path and Path(exec_path).exists():
            subprocess.Popen([str(exec_path)])

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

            # Populate Preview Card
            self.entry_name.set_text(candidate.name)
            self.lbl_kind_badge.set_text(candidate.kind.upper())
            self.lbl_source_info.set_text(f"Source: {candidate.source_path}")

            # Category
            cat_lower = candidate.categories.lower()
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
                if candidate.selected_executable:
                    self.combo_exec.set_active_id(candidate.selected_executable.relative_path)
                elif candidate.executables:
                    self.combo_exec.set_active(0)

                # Populate companion launchers if multiple executables detected (e.g. NetLogo + NetLogo 3D)
                primary_rel = self.combo_exec.get_active_id()
                for cand in candidate.executables:
                    if cand.relative_path == primary_rel:
                        continue
                    clean_display_name = format_executable_display_name(cand.name, base_app_name=candidate.name)
                    chk = Gtk.CheckButton(label=f"Create launcher for '{clean_display_name}' ({cand.relative_path})")
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
            self.chk_no_sandbox.set_active(candidate.is_electron)
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

        # Collect any selected companion launchers
        additional_execs = [
            rel_path for rel_path, chk in self.companion_checkboxes
            if chk.get_active() and rel_path != exec_rel
        ]

        # Switch to status progress view
        self.lbl_status.set_markup(f"<span size='large' weight='bold'>Installing {name}…</span>")
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
                    additional_executables=additional_execs
                )
            except Exception as e:
                ok, msg = False, f"Unexpected error during installation: {e}"
            GLib.idle_add(self.on_install_finished, ok, msg, name, open_after)

        threading.Thread(target=install_worker, daemon=True).start()

    def on_install_finished(self, ok: bool, message: str, name: str, open_after: bool):
        self.spinner.stop()
        if ok:
            self.lbl_status.set_markup(f"<span size='large' weight='bold' color='#3da853'>✓ {name} Installed!</span>")
            self.lbl_status_detail.set_markup(
                f"Available now in the <b>Super key launcher</b> and Applications menu.\n\n{message}"
            )
            self.status_btn_box.show_all()
            self.refresh_installed_apps_list()

            if open_after and self.current_candidate:
                manifest = load_manifest()
                app_info = manifest.get(self.current_candidate.app_id)
                if app_info:
                    self.launch_app(app_info.get("desktop_file"), app_info.get("exec_path"))
        else:
            self.lbl_status.set_markup("<span size='large' weight='bold' color='#e05555'>Installation Failed</span>")
            self.lbl_status_detail.set_text(message)
            self.status_btn_box.show_all()

    def on_launch_just_installed(self, button):
        if self.current_candidate:
            manifest = load_manifest()
            app_info = manifest.get(self.current_candidate.app_id)
            if app_info:
                self.launch_app(app_info.get("desktop_file"), app_info.get("exec_path"))
