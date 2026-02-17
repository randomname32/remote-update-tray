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

SSH connections use your existing SSH config and keys — make sure you can `ssh <host>` without a password prompt for each configured machine. See [Setting up passwordless SSH](#setting-up-passwordless-ssh) for instructions.

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
sudo dpkg -i build/deb/remote-update-tray_*.deb
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

**Note:** The app must be run from the system Python environment, not from a conda or virtualenv. It depends on the GObject Introspection bindings (`gi`), which are installed as system packages and not available inside conda environments.

## Uninstalling

```bash
sudo apt remove remote-update-tray
```

## Setting up passwordless SSH

The app connects to remote machines using your existing SSH configuration. Each host must be reachable via `ssh <host>` without a password prompt.

**1. Generate an SSH key (if you don't have one):**

```bash
ssh-keygen -t ed25519
```

Accept the default path (`~/.ssh/id_ed25519`). You can leave the passphrase empty for fully unattended access, or use `ssh-agent` if you prefer a passphrase.

**2. Copy your public key to the remote machine:**

```bash
ssh-copy-id user@hostname
```

Replace `user` with your username on the remote machine. This appends your public key to `~/.ssh/authorized_keys` on the remote.

**3. Test the connection:**

```bash
ssh user@hostname
```

It should log in without asking for a password.

**4. Add a host alias (optional but recommended):**

Edit `~/.ssh/config` to define a short alias:

```
Host myserver
    HostName 192.168.1.100
    User user
    IdentityFile ~/.ssh/id_ed25519
```

You can then use `myserver` as the host name in the Settings dialog.

**Using `ssh-agent` with a passphrase-protected key:**

If your key has a passphrase, the app won't be able to connect unless `ssh-agent` is running and has the key loaded. GNOME typically starts an agent automatically on login. To add your key:

```bash
ssh-add ~/.ssh/id_ed25519
```
