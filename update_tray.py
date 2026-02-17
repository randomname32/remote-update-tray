#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")

from gi.repository import Gtk, GLib
from gi.repository import AyatanaAppIndicator3 as AppIndicator3

import os
import json
import subprocess
import threading

APP_NAME = "remote-update-tray"
CONFIG_DIR = os.path.expanduser("~/.config/remote-update-tray")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "remote-update-tray.desktop")
CHECK_INTERVAL_SECONDS = 600


# =========================
# CONFIG MANAGEMENT
# =========================

def ensure_config():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

    if not os.path.exists(CONFIG_FILE):
        default = {"machines": [{"name": "localhost", "host": "localhost", "root": False}]}
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=4)


def load_config():
    ensure_config()
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)


# =========================
# AUTOSTART MANAGEMENT
# =========================

def get_autostart_enabled():
    if not os.path.exists(AUTOSTART_FILE):
        return True
    with open(AUTOSTART_FILE) as f:
        return "X-GNOME-Autostart-enabled=false" not in f.read()


def set_autostart_enabled(enabled):
    if enabled:
        if os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
    else:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        with open(AUTOSTART_FILE, "w") as f:
            f.write("[Desktop Entry]\n")
            f.write("Type=Application\n")
            f.write("Name=Remote Update Tray\n")
            f.write("Exec=remote-update-tray\n")
            f.write("X-GNOME-Autostart-enabled=false\n")


# =========================
# SSH CHECK
# =========================

def check_updates(host):
    try:
        if host in ("localhost", "127.0.0.1"):
            cmd = ["bash", "-c", "apt list --upgradable 2>/dev/null | grep -v Listing"]
        else:
            cmd = ["ssh", host, "apt list --upgradable 2>/dev/null | grep -v Listing"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode == 255:
            return -1  # SSH connection failure

        if result.returncode not in (0, 1):
            return -1  # unexpected error

        lines = [l for l in result.stdout.strip().split("\n") if l]
        return len(lines)

    except Exception:
        return -1


# =========================
# SETTINGS DIALOG
# =========================

class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, config):
        super().__init__(title="Settings", transient_for=parent, flags=0)
        self.set_default_size(400, 300)

        self.config = config

        box = self.get_content_area()

        self.listbox = Gtk.ListBox()
        box.add(self.listbox)

        self.refresh_list()

        button_box = Gtk.Box(spacing=6)
        box.add(button_box)

        add_button = Gtk.Button(label="Add")
        add_button.connect("clicked", self.add_machine)
        button_box.pack_start(add_button, True, True, 0)

        self.autostart_check = Gtk.CheckButton(label="Autostart on login")
        self.autostart_check.set_active(get_autostart_enabled())
        self.autostart_check.connect("toggled", self.on_autostart_toggled)
        box.add(self.autostart_check)

        self.show_all()

    def refresh_list(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)

        for machine in self.config["machines"]:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(spacing=10)

            label = Gtk.Label(label=f"{machine['name']} ({machine['host']})", xalign=0)
            hbox.pack_start(label, True, True, 0)

            edit_btn = Gtk.Button(label="Edit")
            edit_btn.connect("clicked", self.edit_machine, machine)
            hbox.pack_start(edit_btn, False, False, 0)

            del_btn = Gtk.Button(label="Delete")
            del_btn.connect("clicked", self.delete_machine, machine)
            hbox.pack_start(del_btn, False, False, 0)

            row.add(hbox)
            self.listbox.add(row)

        self.show_all()

    def on_autostart_toggled(self, widget):
        set_autostart_enabled(widget.get_active())

    def add_machine(self, widget):
        self.machine_editor()

    def edit_machine(self, widget, machine):
        self.machine_editor(machine)

    def delete_machine(self, widget, machine):
        self.config["machines"].remove(machine)
        save_config(self.config)
        self.refresh_list()

    def machine_editor(self, machine=None):
        dialog = Gtk.Dialog(title="Machine", transient_for=self)
        box = dialog.get_content_area()

        name_entry = Gtk.Entry()
        host_entry = Gtk.Entry()
        root_check = Gtk.CheckButton(label="Connect as root (no sudo)")

        if machine:
            name_entry.set_text(machine["name"])
            host_entry.set_text(machine["host"])
            root_check.set_active(machine.get("root", False))

        box.add(Gtk.Label(label="Name"))
        box.add(name_entry)
        box.add(Gtk.Label(label="Host"))
        box.add(host_entry)
        box.add(root_check)

        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            name = name_entry.get_text()
            host = host_entry.get_text()
            root = root_check.get_active()

            if machine:
                machine["name"] = name
                machine["host"] = host
                machine["root"] = root
            else:
                self.config["machines"].append({"name": name, "host": host, "root": root})

            save_config(self.config)
            self.refresh_list()

        dialog.destroy()


# =========================
# TRAY APP
# =========================

class UpdateTray:

    def __init__(self):
        self.config = load_config()

        self.indicator = AppIndicator3.Indicator.new(
            APP_NAME,
            "software-update-available",
            AppIndicator3.IndicatorCategory.SYSTEM_SERVICES,
        )

        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.indicator.set_menu(self.menu)

        self.build_menu()
        self.refresh()

        GLib.timeout_add_seconds(CHECK_INTERVAL_SECONDS, self.refresh)

    def build_menu(self):
        for item in self.menu.get_children():
            self.menu.remove(item)

        self.machine_items = {}

        for machine in self.config["machines"]:
            item = Gtk.MenuItem(label=f"{machine['name']}: checking...")
            submenu = Gtk.Menu()

            install_item = Gtk.MenuItem(label="Install updates")
            install_item.connect("activate", self.install_updates, machine["host"], machine.get("root", False))
            submenu.append(install_item)

            terminal_item = Gtk.MenuItem(label="Open terminal")
            terminal_item.connect("activate", self.open_terminal, machine["host"])
            submenu.append(terminal_item)

            item.set_submenu(submenu)
            item.show_all()
            self.menu.append(item)
            self.machine_items[machine["host"]] = item

        self.menu.append(Gtk.SeparatorMenuItem())

        settings_item = Gtk.MenuItem(label="Settings")
        settings_item.connect("activate", self.open_settings)
        settings_item.show()
        self.menu.append(settings_item)

        refresh_item = Gtk.MenuItem(label="Refresh now")
        refresh_item.connect("activate", self.refresh)
        refresh_item.show()
        self.menu.append(refresh_item)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.quit)
        quit_item.show()
        self.menu.append(quit_item)

    def open_settings(self, *_):
        dialog = SettingsDialog(None, self.config)
        dialog.run()
        dialog.destroy()

        self.config = load_config()
        self.build_menu()
        self.refresh()

    def refresh(self, *_):
        thread = threading.Thread(target=self.update_all)
        thread.daemon = True
        thread.start()
        return True

    def update_all(self):
        total = 0
        error = False

        for machine in self.config["machines"]:
            count = check_updates(machine["host"])

            GLib.idle_add(
                self.update_menu_item,
                machine["host"],
                machine["name"],
                count,
            )

            if count == -1:
                error = True
            else:
                total += count

        GLib.idle_add(self.update_icon, total, error)

    def update_menu_item(self, host, name, count):
        item = self.machine_items.get(host)
        if not item:
            return

        if count == -1:
            item.set_label(f"{name}: ERROR")
        elif count == 0:
            item.set_label(f"{name}: ✓ up to date")
        else:
            item.set_label(f"{name}: {count} updates")

    def update_icon(self, total, error):
        if error:
            self.indicator.set_icon("dialog-warning")
        elif total == 0:
            self.indicator.set_icon("emblem-default")
        else:
            self.indicator.set_icon("software-update-available")

    def install_updates(self, widget, host, root):
        prefix = "" if root else "sudo "
        upgrade_cmd = f"{prefix}apt update && {prefix}apt upgrade -y; echo ''; echo 'Done. Press Enter to close.'; read"
        if host in ("localhost", "127.0.0.1"):
            cmd = ["gnome-terminal", "--", "bash", "-c", upgrade_cmd]
        else:
            cmd = ["gnome-terminal", "--", "ssh", "-t", host, upgrade_cmd]
        subprocess.Popen(cmd)

    def open_terminal(self, widget, host):
        if host in ("localhost", "127.0.0.1"):
            cmd = ["gnome-terminal"]
        else:
            cmd = ["gnome-terminal", "--", "ssh", host]
        subprocess.Popen(cmd)

    def quit(self, *_):
        Gtk.main_quit()


if __name__ == "__main__":
    UpdateTray()
    Gtk.main()
