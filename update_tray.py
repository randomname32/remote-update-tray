#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Vte", "2.91")

from gi.repository import Gtk, Gdk, GLib, Pango
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from gi.repository import Vte

import os
import sys
import json
import re
import signal
import subprocess
import threading

APP_NAME = "remote-update-tray"
CONFIG_DIR = os.path.expanduser("~/.config/remote-update-tray")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "remote-update-tray.desktop")


# =========================
# CONFIG MANAGEMENT
# =========================

def ensure_config():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

    if not os.path.exists(CONFIG_FILE):
        default = {"check_interval_minutes": 10, "machines": [{"name": "localhost", "host": "localhost", "root": False}]}
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

# Simulate exactly what "apt upgrade" would do. Packages that are kept back
# (new dependencies) or deferred by phased updates are not installed by it,
# so they only count as "held", not as available updates.
APT_SIMULATE = "LANG=C apt-get -s upgrade 2>/dev/null"

# Refresh the package lists first, otherwise the check only sees what the
# daily apt run knew. Needs root or passwordless sudo; without either it
# silently falls back to the cached lists.
APT_REFRESH = (
    '(if [ "$(id -u)" = 0 ]; then apt-get update -qq; '
    "else sudo -n apt-get update -qq; fi) >/dev/null 2>&1"
)

# Snaps and Flatpaks are checked on every machine that has them installed.
CHECK_SCRIPT = (
    APT_REFRESH + "; " + APT_SIMULATE + "; "
    "echo @@SNAP; command -v snap >/dev/null && LANG=C snap refresh --list 2>&1; "
    "echo @@FLATPAK; command -v flatpak >/dev/null && "
    "flatpak remote-ls --updates --columns=application 2>/dev/null; true"
)


def check_updates(host):
    """Return (installable, held, detail), or (-1, 0, "") on error.

    installable counts apt, snap and flatpak updates; detail names the
    snap/flatpak share, e.g. "snap 3, flatpak 2" (empty if there is none).
    """
    try:
        if host in ("localhost", "127.0.0.1"):
            cmd = ["bash", "-c", CHECK_SCRIPT]
        else:
            cmd = ["ssh", host, CHECK_SCRIPT]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 255:
            return -1, 0, ""  # SSH connection failure

        apt_out, _, rest = result.stdout.partition("@@SNAP")
        snap_out, _, flatpak_out = rest.partition("@@FLATPAK")

        summary = re.search(r"(\d+) upgraded, .* (\d+) not upgraded", apt_out)
        if not summary:
            return -1, 0, ""  # unexpected error

        installable = sum(1 for l in apt_out.splitlines() if l.startswith("Inst "))

        # "snap refresh --list" prints a table with a "Name" header, or
        # "All snaps up to date."
        snap_lines = snap_out.strip().splitlines()
        snaps = len(snap_lines) - 1 if snap_lines and snap_lines[0].startswith("Name") else 0
        flatpaks = len([l for l in flatpak_out.splitlines() if l.strip()])

        detail = ", ".join(f"{name} {n}" for name, n in (("snap", snaps), ("flatpak", flatpaks)) if n)
        return installable + snaps + flatpaks, int(summary.group(2)), detail

    except Exception:
        return -1, 0, ""


# =========================
# SETTINGS DIALOG
# =========================

# GNOME-settings look in plain GTK 3: rounded "boxed" lists, two-line rows,
# pill badges. Colors come from the theme, so light and dark mode both work.
CSS = b"""
.boxed-list {
    background-color: @theme_base_color;
    border: 1px solid @borders;
    border-radius: 12px;
}
.boxed-list row {
    padding: 10px 12px;
}
.boxed-list row:first-child {
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}
.boxed-list row:last-child {
    border-bottom-left-radius: 12px;
    border-bottom-right-radius: 12px;
}
.section-title {
    font-weight: bold;
    margin: 18px 6px 8px 6px;
}
.row-title {
    font-weight: bold;
}
.row-subtitle {
    font-size: 0.9em;
    opacity: 0.65;
}
.badge {
    border-radius: 999px;
    padding: 1px 8px;
    font-size: 0.8em;
    background-color: alpha(@theme_fg_color, 0.08);
}
.badge.accent {
    background-color: alpha(@theme_selected_bg_color, 0.15);
    color: @theme_selected_bg_color;
}
.empty-hint {
    padding: 24px;
    opacity: 0.65;
}
.update-sidebar row {
    padding: 8px 12px;
}
.attention {
    color: @theme_selected_bg_color;
    font-weight: bold;
    opacity: 1;
}
.update-sidebar row:selected .attention {
    color: inherit;
}
"""

_css_loaded = False


def load_css():
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_loaded = True


def add_class(widget, *names):
    for name in names:
        widget.get_style_context().add_class(name)
    return widget


def separator_header(row, before):
    if before and not row.get_header():
        row.set_header(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))


def boxed_list():
    listbox = add_class(Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE), "boxed-list")
    listbox.set_header_func(separator_header)
    return listbox


def section_title(text):
    return add_class(Gtk.Label(label=text, xalign=0), "section-title")


def settings_row(title, subtitle=None, prefix=None, suffixes=()):
    """A two-line row: optional icon, title + dim subtitle, widgets on the right."""
    row = Gtk.ListBoxRow(activatable=False)
    hbox = Gtk.Box(spacing=12)

    if prefix:
        hbox.pack_start(prefix, False, False, 0)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
    title_label = add_class(Gtk.Label(label=title, xalign=0), "row-title")
    title_label.set_ellipsize(Pango.EllipsizeMode.END)
    text.pack_start(title_label, False, False, 0)
    if subtitle:
        sub = add_class(Gtk.Label(label=subtitle, xalign=0), "row-subtitle")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(sub, False, False, 0)
    hbox.pack_start(text, True, True, 0)

    for widget in suffixes:
        widget.set_valign(Gtk.Align.CENTER)
        hbox.pack_start(widget, False, False, 0)

    row.add(hbox)
    return row


def icon_button(icon_name, tooltip, callback, *args):
    button = Gtk.Button.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
    button.set_tooltip_text(tooltip)
    button.set_relief(Gtk.ReliefStyle.NONE)
    add_class(button, "circular")
    button.connect("clicked", callback, *args)
    return button


def badge(text, accent=False):
    label = add_class(Gtk.Label(label=text), "badge")
    if accent:
        add_class(label, "accent")
    return label


class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, config):
        super().__init__(title="Settings", transient_for=parent, flags=0, use_header_bar=True)
        load_css()
        self.set_default_size(520, 720)
        self.get_header_bar().set_subtitle("Remote Update Tray")

        self.config = config

        add_button = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_button.set_tooltip_text("Add machine")
        add_button.connect("clicked", self.add_machine)
        self.get_header_bar().pack_start(add_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for side in ("top", "bottom"):
            getattr(content, f"set_margin_{side}")(12)
        for side in ("start", "end"):
            getattr(content, f"set_margin_{side}")(24)

        content.pack_start(section_title("Machines"), False, False, 0)
        self.listbox = boxed_list()
        content.pack_start(self.listbox, False, False, 0)

        content.pack_start(section_title("General"), False, False, 0)
        general = boxed_list()

        self.interval_spin = Gtk.SpinButton.new_with_range(1, 1440, 1)
        self.interval_spin.set_value(self.config.get("check_interval_minutes", 10))
        self.interval_spin.connect("value-changed", self.on_interval_changed)
        general.add(settings_row("Check interval", "Minutes between update checks",
                                 suffixes=[self.interval_spin]))

        self.autostart_switch = Gtk.Switch()
        self.autostart_switch.set_active(get_autostart_enabled())
        self.autostart_switch.connect("notify::active", self.on_autostart_toggled)
        general.add(settings_row("Start on login", "Show the tray icon after logging in",
                                 suffixes=[self.autostart_switch]))
        content.pack_start(general, False, False, 0)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(content)
        box = self.get_content_area()
        box.pack_start(scroller, True, True, 0)

        self.refresh_list()
        self.show_all()

    def refresh_list(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)

        if not self.config["machines"]:
            row = Gtk.ListBoxRow(activatable=False)
            row.add(add_class(Gtk.Label(label="No machines yet. Add one with +."), "empty-hint"))
            self.listbox.add(row)

        for machine in self.config["machines"]:
            local = machine["host"] in ("localhost", "127.0.0.1")
            icon = Gtk.Image.new_from_icon_name(
                "computer-symbolic" if local else "network-server-symbolic", Gtk.IconSize.LARGE_TOOLBAR
            )
            badges = []
            if machine.get("root", False):
                badges.append(badge("root"))
            if machine.get("auto_yes", True):
                badges.append(badge("auto-confirm", accent=True))

            self.listbox.add(settings_row(
                machine["name"],
                machine["host"],
                prefix=icon,
                suffixes=badges + [
                    icon_button("document-edit-symbolic", "Edit", self.edit_machine, machine),
                    icon_button("user-trash-symbolic", "Remove", self.delete_machine, machine),
                ],
            ))

        self.listbox.show_all()

    def on_interval_changed(self, widget):
        self.config["check_interval_minutes"] = widget.get_value_as_int()
        save_config(self.config)

    def on_autostart_toggled(self, widget, _param):
        set_autostart_enabled(widget.get_active())

    def add_machine(self, widget):
        self.machine_editor()

    def edit_machine(self, widget, machine):
        self.machine_editor(machine)

    def delete_machine(self, widget, machine):
        confirm = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Remove “{machine['name']}”?",
        )
        confirm.format_secondary_text("The tray stops checking this machine. Its SSH setup is not touched.")
        confirm.add_button("Cancel", Gtk.ResponseType.CANCEL)
        remove = confirm.add_button("Remove", Gtk.ResponseType.OK)
        add_class(remove, "destructive-action")
        response = confirm.run()
        confirm.destroy()

        if response == Gtk.ResponseType.OK:
            self.config["machines"].remove(machine)
            save_config(self.config)
            self.refresh_list()

    def machine_editor(self, machine=None):
        dialog = Gtk.Dialog(
            title="Edit Machine" if machine else "Add Machine",
            transient_for=self,
            use_header_bar=True,
        )
        dialog.set_default_size(440, -1)
        box = dialog.get_content_area()

        name_entry = Gtk.Entry(placeholder_text="e.g. docker-host", width_chars=22)
        host_entry = Gtk.Entry(placeholder_text="host from ~/.ssh/config", width_chars=22)
        root_switch = Gtk.Switch()
        auto_yes_switch = Gtk.Switch()

        if machine:
            name_entry.set_text(machine["name"])
            host_entry.set_text(machine["host"])
            root_switch.set_active(machine.get("root", False))
            auto_yes_switch.set_active(machine.get("auto_yes", True))
        else:
            auto_yes_switch.set_active(True)

        fields = boxed_list()
        fields.add(settings_row("Name", "Shown in the tray menu", suffixes=[name_entry]))
        fields.add(settings_row("Host", "localhost or an SSH host", suffixes=[host_entry]))

        options = boxed_list()
        options.add(settings_row("Connect as root", "Run apt and snap without sudo",
                                 suffixes=[root_switch]))
        options.add(settings_row("Auto-confirm", "Install updates without asking",
                                 suffixes=[auto_yes_switch]))

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, f"set_margin_{side}")(18)
        inner.pack_start(fields, False, False, 0)
        inner.pack_start(options, False, False, 0)
        box.pack_start(inner, True, True, 0)

        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save_button = dialog.add_button("Save", Gtk.ResponseType.OK)
        add_class(save_button, "suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        def update_save(*_):
            save_button.set_sensitive(bool(name_entry.get_text().strip() and host_entry.get_text().strip()))
        name_entry.connect("changed", update_save)
        host_entry.connect("changed", update_save)
        update_save()

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            host = host_entry.get_text().strip()
            root = root_switch.get_active()
            auto_yes = auto_yes_switch.get_active()

            if machine:
                machine["name"] = name
                machine["host"] = host
                machine["root"] = root
                machine["auto_yes"] = auto_yes
            else:
                self.config["machines"].append({"name": name, "host": host, "root": root, "auto_yes": auto_yes})

            save_config(self.config)
            self.refresh_list()

        dialog.destroy()


# =========================
# UPDATE WINDOW
# =========================

# The line the cursor sits on, once the output has paused, looks like one of
# these when a process waits for input: apt/flatpak/our snap "[Y/n]", the
# German apt "[J/n]", sudo and ssh password prompts, or any question.
PROMPT_RE = re.compile(
    r"(\[[YyJj]/[Nn]\]|\(y/n\)|pass(word|wort|phrase)[^\n]*:|\?)\s*:?\s*$",
    re.IGNORECASE,
)
PROMPT_IDLE_MS = 700  # output must have paused this long before a prompt counts


class UpdateWindow(Gtk.Window):
    """All running updates in one window: machines with their state on the
    left, the selected machine's terminal (a real VTE terminal, so prompts
    and sudo passwords work) on the right."""

    def __init__(self, on_finished, on_change=lambda: None):
        super().__init__(title="Updates")
        load_css()
        self.on_finished = on_finished  # called with the machine after its update ended
        self.on_change = on_change  # called whenever a machine's state changes (for the tray)
        self.jobs = {}  # host -> job dict
        self.set_default_size(980, 620)
        self.set_icon_name("software-update-available")

        self.header = Gtk.HeaderBar(title="Updates", show_close_button=True)
        self.set_titlebar(self.header)

        self.sidebar = add_class(Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE), "update-sidebar")
        self.sidebar.connect("row-selected", self.on_row_selected)
        side_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        side_scroll.set_size_request(210, -1)
        side_scroll.add(self.sidebar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.pack1(side_scroll, False, False)
        paned.pack2(self.stack, True, False)
        self.add(paned)

        self.connect("delete-event", self.on_delete)

    def running(self):
        return [job for job in self.jobs.values() if job["running"]]

    def state_of(self, host):
        """"waiting", "running" or None (no update running for that host)."""
        job = self.jobs.get(host)
        if not job or not job["running"]:
            return None
        return "waiting" if job["waiting"] else "running"

    def add_job(self, machine, argv, select=True):
        host = machine["host"]
        job = self.jobs.get(host)

        if job and job["running"]:
            # no second process; just bring the running one into view
            if select:
                self.select(host)
            self.present()
            return

        if not job:
            job = self.create_job(machine)
            self.jobs[host] = job
        else:
            job["terminal"].reset(True, True)
        job["machine"] = machine

        job["running"] = True
        job["cancelled"] = False
        job["pid"] = None
        self.set_state(job, "running")
        job["terminal"].spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.path.expanduser("~"),
            argv,
            None,
            GLib.SpawnFlags.SEARCH_PATH,
            None, None,
            -1,
            None,
            self.on_spawned,
            host,
        )

        self.show_all()
        if select or self.sidebar.get_selected_row() is None:
            self.select(host)
        self.present()

    def create_job(self, machine):
        spinner = Gtk.Spinner()
        icon = Gtk.Image()
        waiting_icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic", Gtk.IconSize.BUTTON)
        state_stack = Gtk.Stack()
        state_stack.add_named(spinner, "running")
        state_stack.add_named(icon, "finished")
        state_stack.add_named(waiting_icon, "waiting")
        state_stack.show_all()  # a stack only switches to children that are visible

        name = add_class(Gtk.Label(label=machine["name"], xalign=0), "row-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        status = add_class(Gtk.Label(xalign=0), "row-subtitle")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(name, False, False, 0)
        text.pack_start(status, False, False, 0)

        hbox = Gtk.Box(spacing=10)
        hbox.pack_start(state_stack, False, False, 0)
        hbox.pack_start(text, True, True, 0)

        row = Gtk.ListBoxRow()
        row.host = machine["host"]
        row.add(hbox)
        self.sidebar.add(row)

        terminal = Vte.Terminal()
        terminal.set_scrollback_lines(10000)
        terminal.connect("child-exited", self.on_child_exited, machine["host"])
        terminal.connect("contents-changed", self.on_contents_changed, machine["host"])
        terminal.connect("commit", self.on_commit, machine["host"])
        term_scroll = Gtk.ScrolledWindow()
        term_scroll.add(terminal)
        self.stack.add_named(term_scroll, machine["host"])

        return {
            "row": row, "terminal": terminal, "spinner": spinner, "icon": icon,
            "state_stack": state_stack, "status": status, "running": False,
            "waiting": False, "prompt_timer": None, "pid": None, "cancelled": False,
            "result": None,
        }

    def set_state(self, job, state, exit_code=0):
        job["waiting"] = state == "waiting"
        job["status"].get_style_context().remove_class("attention")
        if state == "running":
            job["spinner"].start()
            job["state_stack"].set_visible_child_name("running")
            job["status"].set_text("Updating…")
        elif state == "waiting":
            job["spinner"].stop()
            job["state_stack"].set_visible_child_name("waiting")
            job["status"].set_text("Waiting for input")
            job["status"].get_style_context().add_class("attention")
        elif state == "cancelling":
            job["status"].set_text("Cancelling…")
        else:
            job["spinner"].stop()
            if state == "cancelled":
                job["result"], icon_name, text = "cancelled", "process-stop-symbolic", "Cancelled"
            elif exit_code == 0:
                job["result"], icon_name, text = "done", "emblem-ok-symbolic", "Done"
            else:
                job["result"], icon_name, text = "failed", "dialog-error-symbolic", f"Failed (exit {exit_code})"
            job["icon"].set_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
            job["state_stack"].set_visible_child_name("finished")
            job["status"].set_text(text)
        self.update_header()
        self.on_change()

    def update_header(self):
        jobs = list(self.jobs.values())
        self.header.set_subtitle(self.summary(include_finished=True))
        # flags the window in the dock/taskbar while something waits for an answer
        self.set_urgency_hint(any(j["running"] and j["waiting"] for j in self.jobs.values()))

    def summary(self, include_finished=False):
        """e.g. "1 waiting for input · 2 running · 3 done"."""
        jobs = list(self.jobs.values())
        counts = [
            (sum(1 for j in jobs if j["running"] and j["waiting"]), "waiting for input"),
            (sum(1 for j in jobs if j["running"] and not j["waiting"]), "running"),
        ]
        if include_finished:
            for result in ("done", "failed", "cancelled"):
                counts.append((sum(1 for j in jobs if not j["running"] and j["result"] == result), result))
        return " · ".join(f"{n} {label}" for n, label in counts if n)

    def on_contents_changed(self, terminal, host):
        # check for a prompt only once the output has paused, not on every line
        job = self.jobs[host]
        if job["prompt_timer"]:
            GLib.source_remove(job["prompt_timer"])
        job["prompt_timer"] = GLib.timeout_add(PROMPT_IDLE_MS, self.check_prompt, host)

    def check_prompt(self, host):
        job = self.jobs[host]
        job["prompt_timer"] = None
        if job["running"]:
            terminal = job["terminal"]
            col, row = terminal.get_cursor_position()
            line = terminal.get_text_range_format(Vte.Format.TEXT, row, 0, row, terminal.get_column_count())[0] or ""
            waiting = bool(PROMPT_RE.search(line.rstrip("\n")))
            if waiting != job["waiting"]:
                self.set_state(job, "waiting" if waiting else "running")
        return False

    def on_commit(self, terminal, text, size, host):
        # the user typed something: the prompt is being answered
        job = self.jobs[host]
        if job["running"] and job["waiting"]:
            self.set_state(job, "running")

    def select(self, host):
        self.sidebar.select_row(self.jobs[host]["row"])

    def on_row_selected(self, listbox, row):
        if row is not None:
            self.stack.set_visible_child_name(row.host)
            self.jobs[row.host]["terminal"].grab_focus()

    def on_spawned(self, terminal, pid, error, host):
        job = self.jobs[host]
        job["pid"] = pid
        if error is not None:
            job["running"] = False
            terminal.feed(f"\r\nCould not start: {error.message}\r\n".encode())
            self.set_state(job, "finished", exit_code=-1)

    def on_child_exited(self, terminal, wait_status, host):
        job = self.jobs[host]
        job["running"] = False
        job["waiting"] = False
        if job["cancelled"]:
            self.set_state(job, "cancelled")
        else:
            self.set_state(job, "finished", exit_code=os.waitstatus_to_exitcode(wait_status))
        self.on_finished(job["machine"])

        # closed while updates were running: go away once the last one is done
        if not self.get_visible() and not self.running():
            self.destroy()

    def cancel(self, job):
        """Stop a job that waits for input. At a [Y/n] or password prompt
        nothing has been installed yet, so this is safe."""
        job["cancelled"] = True
        self.set_state(job, "cancelling")
        try:
            # the child leads its own process group (VTE starts it in a new
            # session), so this reaches sudo/apt as well as bash or ssh
            os.killpg(job["pid"], signal.SIGHUP)
        except (ProcessLookupError, TypeError):
            pass

    def on_delete(self, *_):
        running = self.running()
        if not running:
            return False

        # Prompts are cancelled: nothing has been installed at that point.
        # Updates that are installing keep running in the background, since
        # killing apt/dpkg halfway can break a system; the tray shows them.
        waiting = [j for j in running if j["waiting"]]
        installing = [j for j in running if not j["waiting"]]
        if waiting:
            names = lambda jobs: ", ".join(j["machine"]["name"] for j in jobs)
            lines = [f"Waiting for input, will be cancelled: {names(waiting)}."]
            if installing:
                lines.append(f"Still installing, keeps running in the background: {names(installing)}.")
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Close the update window?",
            )
            dialog.format_secondary_text("\n".join(lines))
            dialog.add_button("Keep Open", Gtk.ResponseType.CANCEL)
            dialog.add_button("Cancel Prompts & Close" if installing else "Cancel Updates & Close",
                              Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return True
            for job in waiting:
                self.cancel(job)

        # hidden until the last job has ended, then on_child_exited destroys it
        self.hide()
        return True


# =========================
# TRAY APP
# =========================

class UpdateTray:

    def __init__(self):
        self.config = load_config()
        self.results = {}  # host -> last update count (-1 = error)
        self.update_window = None

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

        interval = self.config.get("check_interval_minutes", 10) * 60
        GLib.timeout_add_seconds(interval, self.refresh)

    def build_menu(self):
        for item in self.menu.get_children():
            self.menu.remove(item)

        self.machine_items = {}

        update_all_item = Gtk.MenuItem(label="Update all")
        update_all_submenu = Gtk.Menu()

        configured_item = Gtk.MenuItem(label="update as configured")
        configured_item.connect("activate", self.update_all_machines, False)
        update_all_submenu.append(configured_item)

        yes_item = Gtk.MenuItem(label="force auto-confirm")
        yes_item.connect("activate", self.update_all_machines, True)
        update_all_submenu.append(yes_item)

        update_all_item.set_submenu(update_all_submenu)
        update_all_item.show_all()
        self.menu.append(update_all_item)

        # only visible while the update window exists (it may be hidden with updates running)
        self.window_item = Gtk.MenuItem(label="Show update window")
        self.window_item.connect("activate", self.show_update_window)
        self.menu.append(self.window_item)
        self.window_item.set_visible(self.update_window is not None)

        self.menu.append(Gtk.SeparatorMenuItem())

        for machine in self.config["machines"]:
            item = Gtk.MenuItem(label=f"{machine['name']}: checking...")
            submenu = Gtk.Menu()

            install_item = Gtk.MenuItem(label="Install updates")
            install_item.connect("activate", self.install_updates, machine["host"], machine.get("root", False), machine.get("auto_yes", True))
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

        about_item = Gtk.MenuItem(label="About")
        about_item.connect("activate", self.open_about)
        about_item.show()
        self.menu.append(about_item)

        refresh_item = Gtk.MenuItem(label="Refresh now")
        refresh_item.connect("activate", self.refresh)
        refresh_item.show()
        self.menu.append(refresh_item)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.quit)
        quit_item.show()
        self.menu.append(quit_item)

    def open_about(self, *_):
        try:
            version = subprocess.check_output(
                ["dpkg-query", "-W", "-f=${Version}", APP_NAME],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            version = "dev"

        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f"Remote Update Tray  v{version}",
        )
        dialog.format_secondary_text(
            "Monitors remote machines for available APT updates over SSH.\n\n"
            "Checks at regular intervals. Tray icon reflects overall status:\n"
            "  • Green check — all machines up to date\n"
            "  • Package icon — updates available\n"
            "  • Warning — one or more machines unreachable\n\n"
            "Manage machines and autostart via the Settings menu.\n\n"
            "Released under the MIT License."
        )
        dialog.run()
        dialog.destroy()

    def open_settings(self, *_):
        old_interval = self.config.get("check_interval_minutes", 10)

        dialog = SettingsDialog(None, self.config)
        dialog.run()
        dialog.destroy()

        self.config = load_config()

        if self.config.get("check_interval_minutes", 10) != old_interval:
            os.execv(sys.executable, [sys.executable] + sys.argv)

        self.build_menu()
        self.refresh()

    def refresh(self, *_):
        thread = threading.Thread(target=self.update_all)
        thread.daemon = True
        thread.start()
        return True

    def update_all(self):
        for machine in self.config["machines"]:
            self.check_machine(machine)

    def check_machine(self, machine):
        """Check one machine and update its menu entry and the tray icon (runs in a thread)."""
        count, held, detail = check_updates(machine["host"])
        self.results[machine["host"]] = count

        GLib.idle_add(
            self.update_menu_item,
            machine["host"],
            machine["name"],
            count,
            held,
            detail,
        )
        GLib.idle_add(self.update_icon)

    def update_menu_item(self, host, name, count, held=0, detail=""):
        item = self.machine_items.get(host)
        if not item:
            return
        if self.job_state(host):
            return  # an update is running there; on_jobs_changed owns the label until it ends

        notes = [n for n in (detail, f"{held} held back" if held else "") if n]
        note = f" ({'; '.join(notes)})" if notes else ""
        if count == -1:
            item.set_label(f"{name}: ERROR")
        elif count == 0:
            item.set_label(f"{name}: ✓ up to date{note}")
        else:
            item.set_label(f"{name}: {count} updates{note}")

    def update_icon(self):
        # only machines that have been checked count; unchecked ones keep the current icon state
        counts = [self.results[m["host"]] for m in self.config["machines"] if m["host"] in self.results]
        error = -1 in counts
        total = sum(c for c in counts if c > 0)
        states = [self.job_state(m["host"]) for m in self.config["machines"]]

        # running updates come first: they need attention, or at least must not be forgotten
        if "waiting" in states:
            self.indicator.set_icon_full("dialog-question", "An update is waiting for input")
        elif "running" in states:
            self.indicator.set_icon_full("emblem-synchronizing", "Updates running")
        elif error:
            self.indicator.set_icon_full("dialog-warning", "Connection error")
        elif total == 0:
            self.indicator.set_icon_full("emblem-default", "Up to date")
        else:
            self.indicator.set_icon_full("software-update-available", "Updates available")

    def update_all_machines(self, widget, force_yes):
        for i, machine in enumerate(self.config["machines"]):
            auto_yes = True if force_yes else machine.get("auto_yes", True)
            # show the first machine; the others run alongside in the sidebar
            self.install_updates(None, machine["host"], machine.get("root", False),
                                 auto_yes=auto_yes, select=(i == 0))

    def install_updates(self, widget, host, root, auto_yes=True, select=True):
        prefix = "" if root else "sudo "
        yes_flag = "-y " if auto_yes else ""
        # snap refresh has no confirmation prompt of its own, so ask unless auto-confirm is set
        snap_cmd = f"{prefix}snap refresh" if auto_yes else (
            "if snap refresh --list 2>/dev/null | grep -q .; then "
            "snap refresh --list; read -p 'Install these snap updates? [Y/n] ' a; "
            f"case \"$a\" in [nN]*) echo 'Skipped.';; *) {prefix}snap refresh;; esac; "
            "else echo 'All snaps up to date.'; fi"
        )
        # the exit code tells the update window whether it worked
        upgrade_cmd = (
            "rc=0; "
            f"{{ {prefix}apt update && {prefix}apt upgrade {yes_flag}; }} || rc=$?; "
            f"if command -v snap >/dev/null; then echo; echo '== snap'; {{ {snap_cmd}; }} || rc=$?; fi; "
            f"if command -v flatpak >/dev/null; then echo; echo '== flatpak'; flatpak update {yes_flag}|| rc=$?; fi; "
            "echo; if [ $rc = 0 ]; then echo 'Done.'; else echo \"Finished with errors (exit $rc).\"; fi; "
            "exit $rc"
        )
        if host in ("localhost", "127.0.0.1"):
            argv = ["bash", "-c", upgrade_cmd]
        else:
            argv = ["ssh", "-t", host, upgrade_cmd]

        machine = next((m for m in self.config["machines"] if m["host"] == host), {"name": host, "host": host})
        self.get_update_window().add_job(machine, argv, select=select)

    def get_update_window(self):
        if self.update_window is None:
            self.update_window = UpdateWindow(on_finished=self.on_update_finished,
                                              on_change=self.on_jobs_changed)
            self.update_window.connect("destroy", self.on_update_window_destroyed)
            self.window_item.show()
        return self.update_window

    def job_state(self, host):
        return self.update_window.state_of(host) if self.update_window else None

    def on_jobs_changed(self):
        """Mirror the update window in the menu and the icon, so running
        updates stay visible while the window is hidden."""
        for machine in self.config["machines"]:
            state = self.job_state(machine["host"])
            item = self.machine_items.get(machine["host"])
            if item and state:
                item.set_label(f"{machine['name']}: "
                               + ("❓ waiting for input" if state == "waiting" else "⟳ updating…"))
        summary = self.update_window.summary() if self.update_window else ""
        self.window_item.set_label(f"Show update window ({summary})" if summary else "Show update window")
        self.update_icon()

    def show_update_window(self, *_):
        if self.update_window is not None:
            self.update_window.show_all()
            self.update_window.present()

    def on_update_window_destroyed(self, *_):
        self.update_window = None
        self.window_item.set_label("Show update window")
        self.window_item.hide()
        self.update_icon()

    def on_update_finished(self, machine):
        self.set_checking(machine["host"], machine["name"])
        thread = threading.Thread(target=self.check_machine, args=(machine,))
        thread.daemon = True
        thread.start()

    def set_checking(self, host, name):
        item = self.machine_items.get(host)
        if item:
            item.set_label(f"{name}: checking...")

    def open_terminal(self, widget, host):
        if host in ("localhost", "127.0.0.1"):
            cmd = ["gnome-terminal"]
        else:
            cmd = ["gnome-terminal", "--", "ssh", host]
        subprocess.Popen(cmd)

    def quit(self, *_):
        # quitting kills the update processes in the window; mid-dpkg that can break a system
        running = self.update_window.running() if self.update_window else []
        if running:
            names = ", ".join(job["machine"]["name"] for job in running)
            dialog = Gtk.MessageDialog(
                transient_for=self.update_window,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text="Updates are still running",
            )
            dialog.format_secondary_text(
                f"Quitting stops them: {names}.\n"
                "Interrupting apt or dpkg halfway can leave a system broken."
            )
            dialog.add_button("Show Updates", Gtk.ResponseType.CANCEL)
            quit_button = dialog.add_button("Quit Anyway", Gtk.ResponseType.OK)
            add_class(quit_button, "destructive-action")
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                self.show_update_window()
                return
        Gtk.main_quit()


if __name__ == "__main__":
    UpdateTray()
    Gtk.main()
