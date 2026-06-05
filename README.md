# PiMusic

A lightweight web-based music player for Raspberry Pi. Browse a local or NAS-mounted music library from any browser on your network and play music through the Pi's audio output using mpg123 or mplayer.

Tested on a **Raspberry Pi 1B** — the oldest Pi hardware still in circulation.

## Features

- Browse nested folders of music files, M3U and PLS playlists
- Play internet radio streams from PLS/M3U playlists
- Displays live ICY stream metadata (artist / track title) for internet radio
- Previous / next track navigation, shuffle
- Track durations shown in the file list — retrieved in the background via `mediainfo` while the player is idle, cached to disk and restored on restart
- Files with leading track numbers sort numerically (1, 2, 10 … not 1, 10, 2)
- Single-click to select, double-click to play
- Automatically advances to the next track when a song finishes
- Progress bar with seek by click
- Responsive layout — works on desktop and mobile (portrait and landscape)
- Installable as a PWA — add to Android or iOS home screen for a native app feel
- Runs as a systemd service, starts on boot

## Requirements

- Raspberry Pi running a Linux distribution with systemd (tested on Pi 1B)
- Python 3.7 or newer
- Flask
- `mediainfo` — for track duration display
- One of the supported audio backends (see below)

## Audio backends

PiMusic supports two backends, configured in `pimusic.conf`:

| Backend | Formats |
|---------|---------|
| **mpg123** (default) | MP3 only |
| **mplayer** | MP3, OGG, FLAC, AAC, WAV, M4A, WMA, Opus and more |

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
sudo apt install python3-flask mediainfo mpg123    # mpg123 backend
sudo apt install python3-flask mediainfo mplayer   # mplayer backend
```

**Arch / Manjaro ARM**

```bash
sudo pacman -S python-flask mediainfo mpg123       # mpg123 backend
sudo pacman -S python-flask mediainfo mplayer      # mplayer backend
```

If your distro does not provide these packages, install via pip:

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

Set `root` to wherever your music lives — a local folder or a NAS mount point. The folder must be accessible when the service starts.

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

| Action | How |
|--------|-----|
| Select a file | Single click |
| Play | Double-click, or single-click then **▶ Play** |
| Pause / Resume | **⏸ / ▶** button |
| Stop | **■** button |
| Previous / Next track | **◀◀ / ▶▶** (local playlists only) |
| Shuffle | **⇄** button |
| Seek | Click anywhere on the progress bar |

Previous / next and shuffle are disabled for internet radio streams.

### Track durations

Durations are fetched in the background by `mediainfo` the first time you browse the library. The retriever runs only while the player is idle, so playback is never interrupted. Progress is visible in the file list as durations appear one by one. On a large NAS library this can take a while — durations are cached to `duration_cache.json` and reloaded instantly on every subsequent start.

### Installing as a PWA (Android / iOS)

Open the player in Chrome or Safari on your phone and use **Add to Home Screen** from the browser menu. PiMusic will appear as an app icon and open in standalone mode without the browser chrome.

## File structure

```
pimusic/
  app.py                Flask web server and API
  player.py             audio backend (mpg123 / mplayer)
  config.py             reads pimusic.conf
  OSTools.py            rotating log helper
  pimusic.conf          configuration — edit this
  pimusic.service       systemd unit file
  requirements.txt      Python dependencies
  duration_cache.json   cached track durations (auto-generated)
  templates/
    index.html          web UI
  static/
    style.css           styling
    app.js              browser logic
    site.webmanifest    PWA manifest
    android-chrome-192x192.png
    android-chrome-512x512.png
    apple-touch-icon.png
    favicon.ico
```

## Troubleshooting

**Empty browser / red error message** — the music root is not set or not mounted. Check `pimusic.conf` and verify the path exists on the Pi.

**No sound** — confirm your backend works from the command line (`mpg123 /path/to/file.mp3` or `mplayer /path/to/file.mp3`). Check the Pi's audio output is configured correctly.

**No track durations** — confirm `mediainfo` is installed (`mediainfo --version`). On a NAS, the first scan may take a long time as the drive spins up for each file; leave the service running overnight.

**Service won't start** — check the log: `sudo journalctl -u pimusic -n 50`.
