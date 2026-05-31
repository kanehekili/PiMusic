# PiMusic

A lightweight web-based music player for Raspberry Pi. Browse a local or NAS-mounted music folder from any browser on your network, and play MP3 files and playlists through the Pi's audio output using mpg123.

## Features

- Browse nested folders of MP3 files, M3U and PLS playlists
- Play internet radio streams from PLS/M3U playlists
- Displays live ICY stream metadata (artist / track title) for internet radio
- Previous and next track navigation for local playlists
- Single-click to select, double-click to play immediately
- Automatically advances to the next track when a song finishes
- Runs as a systemd service, starts on boot

## Requirements

- Raspberry Pi running Raspberry Pi OS (tested on Pi 1B with Bullseye)
- Python 3.7 or newer
- Flask (see installation below)
- One of the supported audio backends (see below)

## Audio backends

PiMusic supports two backends, configured in `pimusic.conf`:

| Backend | Formats | Install |
|---|---|---|
| `mpg123` (default) | MP3 only | `sudo apt install mpg123` |
| `mplayer` | MP3, OGG, FLAC, AAC, WAV, and more | `sudo apt install mplayer` |

Switch backend by editing `pimusic.conf`:

```ini
[player]
backend = mplayer
```

With `mpg123`, unsupported formats are silently skipped in the file browser. With `mplayer`, all formats mplayer understands are playable.

## Installation

### 1. Copy files to the Pi

```bash
rsync -av src/ pi@<pi-ip>:/home/pi/pimusic/
```

### 2. Install Flask

On Raspberry Pi OS Bullseye, install Flask via apt — this avoids pip conflicts with the system Python:

```bash
sudo apt install python3-flask
```

If you are on a different OS and prefer pip:

```bash
# Install pip3 first if needed:
sudo apt install python3-pip
pip3 install -r /home/pi/pimusic/requirements.txt
```

### 3. Configure the music folder

Edit `/home/pi/pimusic/pimusic.conf`:

```ini
[music]
# Full path to the folder containing your music files and playlists.
root = /mnt/radio

[server]
host = 0.0.0.0
port = 5054
```

Set `root` to wherever your music is — a local folder or a NAS mount point. The folder must be accessible when the service starts.

### 4. Install and start the systemd service

Edit `/home/pi/pimusic/pimusic.service` and adjust the paths if you deployed to a different location than `/home/pi/pimusic`:

```ini
[Service]
User=pi
WorkingDirectory=/home/pi/pimusic
ExecStart=/usr/bin/python3 /home/pi/pimusic/app.py
```

Then install it:

```bash
sudo cp /home/pi/pimusic/pimusic.service /etc/systemd/system/
sudo systemctl daemon-reload
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
|---|---|
| Select a file | Single click |
| Play a file or playlist | Double-click, or single-click then press **▶ Play** |
| Pause / Resume | **⏸ Pause** / **▶ Resume** button |
| Stop playback | **■** button |
| Previous / Next track | **◀◀** / **▶▶** (local playlists only) |

Previous and next track navigation is disabled for internet radio streams, since you cannot seek backwards in a live broadcast.

## File structure

```
pimusic/
  app.py            Flask web server and API
  player.py         mpg123 process manager
  config.py         Reads pimusic.conf
  pimusic.conf      Configuration file — edit this
  pimusic.service   systemd unit file
  requirements.txt  Python dependencies
  templates/
    index.html      Web UI
  static/
    style.css       Styling
    app.js          Browser logic
```

## Troubleshooting

**Empty browser / red error message** — the music root is not set or not mounted. Check `pimusic.conf` and verify the path exists on the Pi.

**No sound** — confirm mpg123 works from the command line: `mpg123 /path/to/file.mp3`. Check the Pi's audio output is configured correctly.

**Service won't start** — check the log: `sudo journalctl -u pimusic -n 50`.
