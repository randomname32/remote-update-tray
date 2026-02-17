# remote-update-tray

A lightweight system tray applet for Ubuntu/GNOME that monitors remote (and local) machines for available APT package updates over SSH.

## Features

- Lives in the system tray using AyatanaAppIndicator
- Checks each configured machine for available `apt` updates every 10 minutes
- Tray icon reflects overall status: up-to-date, updates available, or connection error
- Per-machine submenu to install updates or open an SSH terminal
- Settings dialog to add, edit, and remove machines
- Supports `localhost` for monitoring the local machine
- Autostarts on login via XDG autostart

## Configuration

Machine definitions are stored in `~/.config/remote-update-tray/config.json` (created automatically on first run). You can manage machines through the Settings dialog in the tray menu.

SSH connections use your existing SSH config and keys — make sure you can `ssh <host>` without a password prompt for each configured machine.

## Dependencies

- `python3`
- `gir1.2-ayatanaappindicator3-0.1`
- `gir1.2-gtk-3.0`
- `gnome-terminal`
- `openssh-client`

## Building the .deb package

Install build dependencies:

```bash
sudo apt install dpkg-dev debhelper
```

Build:

```bash
./build.sh
```

The resulting `.deb` file will be in `build/deb/`.

## Installing

```bash
sudo dpkg -i build/deb/remote-update-tray_1.0.0-1_all.deb
```

If dependencies are missing, fix them with:

```bash
sudo apt install -f
```

## Running

The applet autostarts on login. To start it manually:

```bash
remote-update-tray
```

## Uninstalling

```bash
sudo apt remove remote-update-tray
```
