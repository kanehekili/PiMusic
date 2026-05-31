# PiMusic

A lightweight web-based music player for Raspberry Pi. Browse a local or NAS-mounted music folder from any browser on your network, and play music through the Pi's audio output using mpg123 or mplayer. With mplayer as the backend, a wide range of formats is supported — MP3, OGG, FLAC, AAC, WAV, and more.

## Features

- Browse nested folders of music files, M3U and PLS playlists
- Play internet radio streams from PLS/M3U playlists
- Displays live ICY stream metadata (artist / track title) for internet radio
- Previous and next track navigation for local playlists
- Single-click to select, double-click to play immediately
- Automatically advances to the next track when a song finishes
- Progress bar with seek by mouse click
- Runs as a systemd service, starts on boot

## Requirements

- Raspberry Pi (tested on Pi 1B) running a Linux distribution with systemd
- Python 3.7 or newer
- Flask
- One of the supported audio backends (see below)

## Audio backends

PiMusic supports two backends, configured in `pimusic.conf`:

- **mpg123** (default) — MP3 only
- **mplayer** — MP3, OGG, FLAC, AAC, WAV, M4A and more

Switch backend by editing `pimusic.conf`:

```ini
[player]
backend = mplayer
```

With `mpg123`, unsupported formats are silently skipped in the file browser. With `mplayer`, all formats mplayer understands are playable.

## Installation

### 1. Copy files to the Pi

```bash
rsync -av src/ <user>@<pi-ip>:~/pimusic/
```

### 2. Install dependencies

**Debian / Raspberry Pi OS**

```bash
sudo apt install python3-flask mpg123        # for mpg123 backend
sudo apt install python3-flask mplayer       # for mplayer backend
```

**Arch / Manjaro ARM**

```bash
sudo pacman -S python-flask mpg123           # for mpg123 backend
sudo pacman -S python-flask mplayer          # for mplayer backend
```

If your distro does not provide a Flask package, install it via pip as a last resort:

```bash
pip install -r ~/pimusic/requirements.txt
```

### 3. Configure the music folder

Edit `~/pimusic/pimusic.conf`:

```ini
[music]
# Full path to the folder containing your music files and playlists.
root = /mnt/music

[server]
host = 0.0.0.0
port = 5054
```

Set `root` to wherever your music is — a local folder or a NAS mount point. The folder must be accessible when the service starts.

### 4. Install and start the systemd service

Edit `~/pimusic/pimusic.service` to match your username and deploy path:

```ini
[Service]
User=<user>
WorkingDirectory=/home/<user>/pimusic
ExecStart=/usr/bin/python3 /home/<user>/pimusic/app.py
```

Then install it:

```bash
sudo cp ~/pimusic/pimusic.service /etc/systemd/system/
sudo systemctl enable pimusic
sudo systemctl start pimusic
```

Check that it started correctly:

```bash
sudo systemctl status pimusic
```

## Usage

Open a browser and go to `http://<pi-ip>:5054`.

- **Select** a file — single click
- **Play** — double-click, or single-click then press **▶ Play**
- **Pause / Resume** — the **⏸ / ▶** button
- **Stop** — the **■** button
- **Previous / Next track** — **◀◀ / ▶▶** (local playlists only)
- **Seek** — click anywhere on the progress bar at the bottom

Previous and next track navigation is disabled for internet radio streams.

## File structure

```
pimusic/
  app.py            Flask web server and API
  player.py         audio backend (mpg123 / mplayer)
  config.py         reads pimusic.conf
  pimusic.conf      configuration — edit this
  pimusic.service   systemd unit file
  requirements.txt  Python dependencies
  templates/
    index.html      web UI
  static/
    style.css       styling
    app.js          browser logic
```

## Troubleshooting

**Empty browser / red error message** — the music root is not set or not mounted. Check `pimusic.conf` and verify the path exists on the Pi.

**No sound** — confirm your backend works from the command line (`mpg123 /path/to/file.mp3` or `mplayer /path/to/file.mp3`). Check the Pi's audio output is configured correctly.

**Service won't start** — check the log: `sudo journalctl -u pimusic -n 50`.
