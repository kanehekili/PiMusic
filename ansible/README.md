# Deploying PiMusic with Ansible

Pushes the sources to the device over SSH with `rsync` and writes the systemd
unit. Only changed files are transferred.

There are two playbooks:

| | `deploy.yml` | `update.yml` |
|---|---|---|
| packages, audio group, `pimusic.conf`, systemd unit | yes | no |
| file sync | yes | yes |
| service restart | left to you — the commands are printed | done for you |

`deploy.yml` is the full install and never touches systemd on a device that
already has the unit, so it cannot cut off playback. `update.yml` is the code
update you run while working: it syncs and restarts, nothing else, which keeps it
to three operations on the device.

On a first install `deploy.yml` also enables the service and starts it.

## Prerequisites

On your dev machine:

- `ansible-core` 2.17 or newer (`ansible --version`)
- The `ansible.posix` collection, for the rsync-based file sync
  (`ansible-galaxy collection install ansible.posix` — already present if you
  installed the full `ansible` package)
- `rsync`
- An SSH key that logs into the device without a password

On the device:

- SSH access and `sudo` (for packages, the systemd unit and the restart)
- `rsync`

## Setup

```bash
cd ansible
cp inventory.ini.example inventory.ini
```

Edit `inventory.ini` — hostname, SSH user, deploy path and the initial music
root. This file is gitignored: it stays on your machine and is never
distributed.

## Deploy

```bash
ansible-playbook deploy.yml -K          # full install; -K asks for the sudo password
ansible-playbook update.yml -K          # code update: sync changed files, restart
ansible-playbook deploy.yml --check     # dry run, changes nothing
```

`deploy.yml` installs `flask`, `mediainfo` and the configured audio backend,
syncs the sources, and installs `/etc/systemd/system/pimusic.service`.

## Picking up the new code

The service keeps running the code it loaded at start. `update.yml` restarts it
for you, and only when files actually changed. After a `deploy.yml` run, do it
yourself:

```bash
sudo systemctl daemon-reload && sudo systemctl restart pimusic
```

`deploy.yml` prints exactly this line when files changed. `daemon-reload` is only
needed when the unit file itself changed, and it never disturbs a running
service. Browser assets under `static/` are read from disk per request — those
need no restart, only a page reload; `templates/index.html` does need one.

## What is left alone on the device

`duration_cache.json`, the log file and `pimusic.conf` are never overwritten, so
the duration cache and your hand-tuned config survive every deploy. To push the
config values from the inventory anyway:

```bash
ansible-playbook deploy.yml -K -e overwrite_conf=true
```

Skip the package step with `-e install_packages=false`.

## Troubleshooting

```bash
ssh <user>@<host> 'systemctl status pimusic'
ssh <user>@<host> 'journalctl -u pimusic -n 50'
```

A deploy that cannot reach the host is an SSH problem first — check
`ssh <user>@<host>` by hand, then `ansible pimusic -m ping`.
