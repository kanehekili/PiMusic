import os
import random
import re
import subprocess
import threading
import time

from config import PLAYLIST_EXTENSIONS
from OSTools import Log


# ======================================================================
# Shared base — playlist management, state, parsers
# ======================================================================

class _BasePlayer:
    audio_extensions = frozenset()  # overridden by each backend

    def __init__(self):
        self._lock = threading.Lock()
        self.playlist = []
        self.current_index = -1
        self.current_file = None
        self.state = "stopped"    # "stopped" | "playing" | "paused"
        self.stream_title = None  # ICY metadata, streams only
        self._generation = 0      # incremented on every load; invalidates stale timers
        self.position = 0.0       # current playback position in seconds
        self.duration = 0.0       # total track duration in seconds
        self.source_path = None   # file passed to play() — audio file or playlist
        self.shuffle = False
        self._playlist_orig = []  # original order before shuffling
        self._track_changed = False  # consumed once by status()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def play(self, path):
        with self._lock:
            self.source_path = path
            ext = os.path.splitext(path)[1].lower()
            if ext in PLAYLIST_EXTENSIONS:
                self.playlist = self._parse_playlist(path)
                self.current_index = 0
                Log.info("Playlist loaded: %d tracks from %s", len(self.playlist), os.path.basename(path))
                if not self.playlist:
                    Log.warning("Playlist is empty — no playable tracks found in %s", path)
                    return
            else:
                self.playlist, self.current_index = self._folder_playlist(path)
            self._playlist_orig = list(self.playlist)
            if self.shuffle and len(self.playlist) > 1:
                first = self.playlist[self.current_index]
                random.shuffle(self.playlist)
                self.playlist.remove(first)
                self.playlist.insert(0, first)
                self.current_index = 0
            self._load_locked()

    def next(self):
        with self._lock:
            self._advance_locked()

    def previous(self):
        with self._lock:
            if self.current_index > 0:
                self.current_index -= 1
                self._load_locked()

    def pause(self):
        with self._lock:
            self._do_pause_locked()

    def toggle_shuffle(self):
        with self._lock:
            self.shuffle = not self.shuffle
            if len(self.playlist) > 1 and self.current_index >= 0:
                tail_start = self.current_index + 1
                if self.shuffle:
                    tail = self.playlist[tail_start:]
                    random.shuffle(tail)
                    self.playlist[tail_start:] = tail
                else:
                    current = self.playlist[self.current_index]
                    try:
                        orig_idx = self._playlist_orig.index(current)
                    except ValueError:
                        orig_idx = -1
                    self.playlist[tail_start:] = self._playlist_orig[orig_idx + 1:]
            Log.info("Shuffle: %s", self.shuffle)
            return self.shuffle

    def seek(self, fraction):
        pass  # overridden by backends that support seeking

    def stop(self):
        with self._lock:
            self.playlist = []
            self.current_index = -1
            self.current_file = None
            self.stream_title = None
            self.state = "stopped"
            self.position = 0.0
            self.duration = 0.0
            self._generation += 1
            self._do_stop()

    def status(self):
        with self._lock:
            track_changed = self._track_changed
            self._track_changed = False
            local = not any(self._is_url(t) for t in self.playlist)
            return {
                "state": self.state,
                "track_name": self._display_name(),
                "current_file": self.current_file,
                "source_path": self.source_path,
                "shuffle": self.shuffle,
                "playlist_pos": self.current_index + 1 if self.current_index >= 0 else 0,
                "playlist_len": len(self.playlist),
                "has_next": self.current_index < len(self.playlist) - 1 and local,
                "has_prev": self.current_index > 0 and local,
                "position": self.position,
                "duration": self.duration,
                "track_changed": track_changed,
            }

    # ------------------------------------------------------------------
    # Shared internals
    # ------------------------------------------------------------------

    def _display_name(self):
        if self.current_file is None:
            return None
        if self._is_url(self.current_file):
            return self.stream_title or self.current_file
        return os.path.basename(self.current_file)

    def _try_auto_advance(self, gen):
        with self._lock:
            if self.state != "stopped" or gen != self._generation:
                return
            self._generation += 1
            self._advance_locked()

    def _advance_locked(self):
        if self.current_index < len(self.playlist) - 1:
            self.current_index += 1
            Log.info("Auto-advance: track %d of %d", self.current_index + 1, len(self.playlist))
            self._load_locked()
        else:
            Log.info("Playlist finished")

    def _load_locked(self):
        if 0 <= self.current_index < len(self.playlist):
            self.current_file = self.playlist[self.current_index]
            self.stream_title = None
            self.position = 0.0
            self.duration = 0.0
            self._track_changed = True
            self._generation += 1
            self._do_load(self.current_file)

    # ------------------------------------------------------------------
    # Hooks — implemented by each backend (all called with lock held)
    # ------------------------------------------------------------------

    def _do_load(self, path):    raise NotImplementedError
    def _do_stop(self):          raise NotImplementedError
    def _do_pause_locked(self):  raise NotImplementedError

    # ------------------------------------------------------------------
    # Playlist parsers
    # ------------------------------------------------------------------

    def _folder_playlist(self, path):
        """Return (sorted audio files in same folder, index of path in that list)."""
        folder = os.path.dirname(path)
        try:
            names = sorted(
                (n for n in os.listdir(folder)
                 if os.path.splitext(n)[1].lower() in self.audio_extensions),
                key=str.lower,
            )
        except OSError:
            return [path], 0
        tracks = [os.path.join(folder, n) for n in names]
        idx = tracks.index(path) if path in tracks else 0
        return tracks or [path], idx

    def _parse_playlist(self, path):
        ext = os.path.splitext(path)[1].lower()
        base = os.path.dirname(path)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                if ext in (".m3u", ".m3u8"):
                    return self._parse_m3u(fh, base)
                if ext == ".pls":
                    return self._parse_pls(fh, base)
        except OSError:
            pass
        return []

    def _parse_m3u(self, fh, base):
        tracks = []
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if self._is_url(line):
                tracks.append(line)
            else:
                norm = line.replace("\\", "/")   # handle Windows-style paths
                p = norm if os.path.isabs(norm) else os.path.join(base, norm)
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in self.audio_extensions:
                    tracks.append(os.path.realpath(p))
                else:
                    Log.warning("M3U: skipped (not found or wrong type): %s", line)
        return tracks

    def _parse_pls(self, fh, base):
        tracks = []
        for line in fh:
            line = line.strip()
            if not re.match(r"(?i)^file\d+=", line):
                continue
            _, _, val = line.partition("=")
            val = val.strip()
            if self._is_url(val):
                tracks.append(val)
            else:
                p = val if os.path.isabs(val) else os.path.join(base, val)
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in self.audio_extensions:
                    tracks.append(p)
        return tracks

    @staticmethod
    def _is_url(s):
        return s.startswith(("http://", "https://", "mms://", "rtmp://", "rtsp://"))


# ======================================================================
# mpg123 backend
# ======================================================================

class _Mpg123Player(_BasePlayer):
    """Uses mpg123 --remote mode. State from @P output. ICY titles from @I lines."""

    audio_extensions = frozenset({".mp3"})

    def __init__(self):
        super().__init__()
        self._total_frames = 0
        self._process = None
        self._start_process()

    def _start_process(self):
        cmd = ["mpg123", "--remote", "--quiet"]
        Log.info("mpg123 command: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._monitor, daemon=True).start()
        except FileNotFoundError:
            self._process = None

    def _monitor(self):
        for line in self._process.stdout:
            line = line.strip()

            if line.startswith("@P"):
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    code = int(parts[1])
                except ValueError:
                    continue
                with self._lock:
                    if code == 2:
                        self.state = "playing"
                        Log.info("Now playing: %s", self._display_name())
                    elif code == 1:
                        self.state = "paused"
                    elif code == 0:
                        self.state = "stopped"
                        gen = self._generation
                if code == 0:
                    threading.Timer(0.3, self._try_auto_advance, args=[gen]).start()

            elif line.startswith("@F"):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        cur_frame = int(parts[1])
                        rem_frames = int(parts[2])
                        pos = float(parts[3])
                        rem = float(parts[4])
                        with self._lock:
                            self.position = pos
                            if self.duration == 0.0:
                                self.duration = pos + rem
                                self._total_frames = cur_frame + rem_frames
                    except ValueError:
                        pass

            elif line.startswith("@I"):
                m = re.search(r"StreamTitle='([^']*)'", line)
                if m:
                    title = m.group(1).strip()
                    with self._lock:
                        self.stream_title = title or None
                    if title:
                        Log.info("Stream: %s", title)

    def seek(self, fraction):
        with self._lock:
            if self._total_frames > 0:
                frame = int(self._total_frames * fraction)
                Log.info("mpg123 << JUMP %d", frame)
                self._send(f"JUMP {frame}")

    def _send(self, cmd):
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(cmd + "\n")
                self._process.stdin.flush()
            except BrokenPipeError:
                pass

    def _do_load(self, path):
        Log.info("mpg123 << LOAD %s", path)
        self._send(f"LOAD {path}")
    def _do_stop(self):             self._send("STOP")
    def _do_pause_locked(self):     self._send("PAUSE")


# ======================================================================
# mplayer backend
# ======================================================================

class _MplayerPlayer(_BasePlayer):
    """Uses mplayer -slave -idle mode. State managed optimistically; EOF from output."""

    audio_extensions = frozenset({".mp3", ".ogg", ".flac", ".aac", ".wav", ".m4a", ".wma", ".opus"})

    def __init__(self):
        super().__init__()
        self._process = None
        self._grace_until = 0.0
        self._start_process()

    def _start_process(self):
        cmd = ["mplayer", "-slave", "-idle", "-nolirc",
               "-vo", "null", "-ao", "alsa",
               "-cache", "512", "-cache-min", "10"]
        Log.info("mplayer command: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._monitor, daemon=True).start()
        except FileNotFoundError:
            self._process = None

    def _monitor(self):
        _watchdog = [None]

        def _reset_watchdog(gen):
            if _watchdog[0]:
                _watchdog[0].cancel()
            with self._lock:
                is_stream = self._is_url(self.current_file or "")
            timeout = 30.0 if is_stream else 1.5
            t = threading.Timer(timeout, self._eof_watchdog, args=[gen])
            t.daemon = True
            t.start()
            _watchdog[0] = t

        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue

            if line.startswith("A:"):
                m = re.match(r'A:\s*([\d.]+).*?of\s*([\d.]+)', line)
                now = time.monotonic()
                with self._lock:
                    if m:
                        try:
                            self.position = float(m.group(1))
                            if self.duration == 0.0 and now >= self._grace_until:
                                self.duration = float(m.group(2))
                        except ValueError:
                            pass
                    else:
                        m2 = re.match(r'A:\s*([\d.]+)', line)
                        if m2:
                            try:
                                self.position = float(m2.group(1))
                            except ValueError:
                                pass
                    need_dur = self.duration == 0.0 and now >= self._grace_until
                    gen = self._generation
                if need_dur:
                    self._send("get_time_length")
                _reset_watchdog(gen)
                continue

            if line.startswith("ANS_LENGTH="):
                try:
                    length = float(line.split("=", 1)[1])
                except (ValueError, IndexError):
                    length = 0.0
                with self._lock:
                    if length > 0 and self.duration == 0.0 and time.monotonic() >= self._grace_until:
                        self.duration = length
                        Log.info("mplayer: duration %.1fs", length)
                continue

            m = re.search(r"StreamTitle='([^']*)'", line)
            if m:
                title = m.group(1).strip()
                with self._lock:
                    self.stream_title = title or None
                    gen = self._generation
                if title:
                    Log.info("Stream: %s", title)
                _reset_watchdog(gen)

    def _eof_watchdog(self, gen):
        with self._lock:
            if gen != self._generation or self.state != "playing":
                return
            Log.info("mplayer: track ended")
            self.state = "stopped"
        self._try_auto_advance(gen)

    def _send(self, cmd):
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(cmd + "\n")
                self._process.stdin.flush()
            except BrokenPipeError:
                pass

    def seek(self, fraction):
        with self._lock:
            if self.state in ("playing", "paused"):
                Log.info("mplayer << seek %.1f%%", fraction * 100)
                self._send(f"seek {fraction * 100:.2f} 1")

    def _do_load(self, path):
        self.state = "playing"          # optimistic; lock is held by caller
        self._grace_until = time.monotonic() + 0.5  # ignore stale A: lines for 500ms
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        Log.info("mplayer << loadfile %s", path)
        self._send(f'loadfile "{escaped}"')

    def _do_stop(self):
        self._send("stop")

    def _do_pause_locked(self):
        if self.state == "playing":
            self.state = "paused"
            self._send("pause")
        elif self.state == "paused":
            self.state = "playing"
            self._send("pause")


# ======================================================================
# Factory
# ======================================================================

def create_player(backend="mpg123"):
    b = backend.lower().strip()
    if b == "mplayer":
        return _MplayerPlayer()
    return _Mpg123Player()
