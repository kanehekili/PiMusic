import os
import random
import re
import signal
import subprocess
import threading
import time

from config import MUSIC_ROOT, PLAYLIST_EXTENSIONS
from OSTools import Log


def track_sort_key(name):
    """Natural sort: a leading number sorts numerically ("2-x" before "10-x")."""
    m = re.match(r'^(\d+)', name)
    num = int(m.group(1)) if m else float('inf')
    return (num, name.lower())


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
                key=track_sort_key,
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
                if not os.path.isfile(p) and os.path.isabs(norm) and MUSIC_ROOT:
                    p = self._remap_abs_path(norm) or p
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in self.audio_extensions:
                    tracks.append(os.path.realpath(p))
                else:
                    Log.warning("M3U: skipped (not found or wrong type): %s", line)
        return tracks

    def _remap_abs_path(self, abs_path):
        parts = abs_path.lstrip("/").split("/")
        for i in range(1, len(parts)):
            candidate = os.path.join(MUSIC_ROOT, *parts[i:])
            if os.path.isfile(candidate):
                Log.info("M3U: remapped %s → .../%s", abs_path, "/".join(parts[i:]))
                return candidate
        return None

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
                norm = val.replace("\\", "/")
                p = norm if os.path.isabs(norm) else os.path.join(base, norm)
                if not os.path.isfile(p) and os.path.isabs(norm) and MUSIC_ROOT:
                    p = self._remap_abs_path(norm) or p
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in self.audio_extensions:
                    tracks.append(os.path.realpath(p))
                else:
                    Log.warning("PLS: skipped (not found or wrong type): %s", val)
        return tracks

    @staticmethod
    def _is_url(s):
        return s.startswith(("http://", "https://", "mms://", "rtmp://", "rtsp://"))


# ======================================================================
# mpg123 backend
# ======================================================================

class _Mpg123Player(_BasePlayer):
    """
    Local files:  mpg123 --remote --quiet  (pause and seek via PAUSE/JUMP commands)
    Stream URLs:  mpg123 <url> direct      (reads output line by line; detects errors immediately)
    Only one process runs at a time.
    """

    audio_extensions = frozenset({".mp3"})

    def __init__(self):
        super().__init__()
        self._total_frames = 0
        self._process = None
        self._mode = "remote"   # "remote" | "stream"
        self._start_remote()

    # ------------------------------------------------------------------ process management

    def _kill_current(self):
        if self._process and self._process.poll() is None:
            self._process.kill()
        self._process = None

    def _start_remote(self):
        self._kill_current()
        self._mode = "remote"
        cmd = ["mpg123", "--remote", "--quiet"]
        Log.info("mpg123 remote: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._remote_monitor, args=[self._process], daemon=True).start()
        except FileNotFoundError:
            Log.error("mpg123 not found — install it for MP3 playback")
            self._process = None

    def _start_stream(self, url, gen):
        self._kill_current()
        self._mode = "stream"
        Log.info("mpg123 stream << %s", url)
        try:
            self._process = subprocess.Popen(
                ["mpg123", "--timeout", "10", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            threading.Thread(target=self._stream_monitor, args=[self._process, url, gen], daemon=True).start()
        except FileNotFoundError:
            Log.error("mpg123 not found")
            self._process = None

    # ------------------------------------------------------------------ remote monitor (local files)

    def _remote_monitor(self, proc):
        Log.info("mpg123 remote monitor started (pid %s)", proc.pid)
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("@F"):
                Log.info("mpg123 >> %s", line)
            if line.startswith("@E"):
                Log.warning("mpg123 error: %s", line[2:].strip())
            elif not line.startswith("@"):
                Log.warning("mpg123: %s", line)
            elif line.startswith("@P"):
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    code = int(parts[1])
                except ValueError:
                    continue
                gen = None
                with self._lock:
                    if self._process is not proc:
                        return
                    if code == 2:
                        self.state = "playing"
                        Log.info("Now playing: %s", self._display_name())
                    elif code == 1:
                        self.state = "paused"
                    elif code == 0:
                        self.state = "stopped"
                        gen = self._generation
                if gen is not None:
                    threading.Timer(0.3, self._try_auto_advance, args=[gen]).start()
            elif line.startswith("@F"):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        cur_frame  = int(parts[1])
                        rem_frames = int(parts[2])
                        pos        = float(parts[3])
                        rem        = float(parts[4])
                        with self._lock:
                            self.position = pos
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

        with self._lock:
            if self._process is not proc:
                return  # intentionally replaced; don't restart
            crashed = self.state == "playing"
            if crashed:
                self.state = "stopped"
            gen = self._generation if crashed else None
        if gen is not None:
            threading.Timer(0.3, self._try_auto_advance, args=[gen]).start()
        Log.warning("mpg123 remote process exited unexpectedly; restarting")
        self._start_remote()

    # ------------------------------------------------------------------ stream monitor (direct URL)

    def _stream_monitor(self, proc, url, gen):
        Log.info("mpg123 stream monitor started (pid %s)", proc.pid)
        confirmed = False
        for bline in iter(proc.stdout.readline, b""):
            line = bline.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            Log.info("stream >> %s", line)

            with self._lock:
                if self._process is not proc or gen != self._generation:
                    return  # superseded

            if re.search(r'error', line, re.IGNORECASE):
                Log.error("Stream error (%s): %s", url, line)
                if not confirmed:
                    with self._lock:
                        if self._process is proc and gen == self._generation:
                            self.state = "stopped"
                    self._try_auto_advance(gen)
                    self._start_remote()
                    return

            if not confirmed and re.search(r'StreamTitle|ICY|kbit|MPEG', line, re.IGNORECASE):
                confirmed = True
                with self._lock:
                    if self._process is proc and gen == self._generation:
                        self.state = "playing"
                Log.info("Now playing stream: %s", url)

            m = re.search(r"StreamTitle='([^']*)'", line)
            if m:
                title = m.group(1).strip()
                with self._lock:
                    if self._process is proc and gen == self._generation:
                        self.stream_title = title or None
                if title:
                    Log.info("Stream title: %s", title)

        # Process exited (stream ended or killed)
        with self._lock:
            if self._process is not proc or gen != self._generation:
                return
            self.state = "stopped"
            g = self._generation
        Log.info("Stream ended: %s", url)
        self._try_auto_advance(g)
        self._start_remote()

    # ------------------------------------------------------------------ send / control

    def _send(self, cmd):
        if self._mode == "remote" and self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(cmd + "\n")
                self._process.stdin.flush()
            except BrokenPipeError:
                Log.warning("mpg123 stdin broken pipe: %s", cmd)

    def seek(self, fraction):
        with self._lock:
            if self._mode == "remote" and self._total_frames > 0:
                frame = int(self._total_frames * fraction)
                Log.info("mpg123 << JUMP %d", frame)
                self._send(f"JUMP {frame}")

    def _do_load(self, path):
        self._total_frames = 0
        if self._is_url(path):
            self._start_stream(path, self._generation)
        else:
            if self._mode != "remote":
                self._start_remote()
            Log.info("mpg123 << LOAD %s", path)
            self._send(f"LOAD {path}")

    def _do_stop(self):
        if self._mode == "stream":
            self._kill_current()
            self._start_remote()
        else:
            self._send("STOP")

    def _do_pause_locked(self):
        if self._mode == "stream":
            return  # streams: pause is disabled; use Stop to disconnect
        if self.state == "playing":
            self.state = "paused"
            self._send("PAUSE")
        elif self.state == "paused":
            self.state = "playing"
            self._send("PAUSE")


# ======================================================================
# mplayer backend
# ======================================================================

class _MplayerPlayer(_BasePlayer):
    """Uses mplayer -slave -idle mode. State managed optimistically; EOF from output."""

    audio_extensions = frozenset({".mp3", ".ogg", ".flac", ".aac", ".wav", ".m4a", ".wma", ".opus"})

    def __init__(self):
        super().__init__()
        self._process = None
        self._pgid = None               # saved at start; valid even after parent dies
        self._grace_until = 0.0
        self._watchdog_timer = None
        self._stream_confirmed = True   # False while a stream URL has never produced output
        self._pending_load = None       # deferred loadfile path after kill-restart
        self._start_process()

    def _start_process(self):
        cmd = ["mplayer", "-slave", "-idle", "-nolirc",
               "-vo", "null", "-ao", "alsa"]
        Log.info("mplayer command: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            self._pgid = self._process.pid  # with start_new_session pgid == pid
            Log.info("mplayer started: pid %d pgid %d", self._process.pid, self._pgid)
            threading.Thread(target=self._monitor, daemon=True).start()
        except FileNotFoundError:
            Log.error("mplayer not found — install it for audio playback")
            self._process = None
            self._pgid = None

    def _kill_mplayer(self):
        """Kill mplayer's entire process group (children hold the pipe write-end).
        Safe to call after the parent has already been reaped — pgid remains valid."""
        pgid = self._pgid
        if not pgid:
            return
        self._pgid = None  # clear before kill so a second call is a no-op
        Log.info("mplayer: sending SIGKILL to process group %d", pgid)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as e:
            Log.warning("mplayer: killpg(%d) failed: %s", pgid, e)

    def _reset_watchdog(self, gen, is_stream, initial=False):
        # Must be called with self._lock held.
        if self._watchdog_timer:
            self._watchdog_timer.cancel()
        if is_stream:
            timeout = 10.0 if initial else 30.0
        else:
            timeout = 1.5
        t = threading.Timer(timeout, self._eof_watchdog, args=[gen])
        t.daemon = True
        t.start()
        self._watchdog_timer = t

    def _monitor(self):
        for line in self._process.stdout:
            line = re.sub(r'\x1b\[[^a-zA-Z]*[a-zA-Z]|\x1b.', '', line).strip()
            if not line:
                continue

            if line.startswith("A:"):
                m = re.match(r'A:\s*([\d.]+).*?of\s*([\d.]+)', line)
                now = time.monotonic()
                with self._lock:
                    gen = self._generation
                    is_stream = self._is_url(self.current_file or "")
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
                    self._stream_confirmed = True
                    self._reset_watchdog(gen, is_stream)
                if need_dur:
                    self._send("get_time_length")
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
                    self._stream_confirmed = True
                    self._reset_watchdog(gen, True)
                if title:
                    Log.info("Stream: %s", title)
                continue

            # Sync state when mplayer reports which file it actually started.
            # Rapid loadfile commands can be swallowed during cache-fill, so
            # mplayer may play a different file than current_file reflects.
            m = re.match(r"^Playing (.+)\.$", line)
            if m:
                actual = m.group(1)
                if not self._is_url(actual):
                    with self._lock:
                        if actual != self.current_file and actual in self.playlist:
                            Log.warning(
                                "mplayer plays %s — expected %s, syncing state",
                                os.path.basename(actual),
                                os.path.basename(self.current_file or ""),
                            )
                            self.current_index = self.playlist.index(actual)
                            self.current_file = actual
                            self.stream_title = None

            # All other lines — log and check for stream errors.
            is_ipv6_noise = "AF_INET6" in line
            is_error = not is_ipv6_noise and bool(re.search(
                r"error|failed|refused|could not|couldn't|invalid|timeout",
                line, re.IGNORECASE,
            ))
            if is_error:
                Log.error("mplayer: %s", line)
                with self._lock:
                    gen = self._generation
                    is_stream = self._is_url(self.current_file or "")
                    confirmed = self._stream_confirmed
                if is_stream and not confirmed:
                    self._eof_watchdog(gen)
            else:
                Log.info("mplayer: %s", line)

        # Process exited (killed or crashed).
        with self._lock:
            if self._watchdog_timer:
                self._watchdog_timer.cancel()
                self._watchdog_timer = None
            if self.state == "playing":
                self.state = "stopped"
                gen = self._generation
            else:
                gen = None
            pending_path = self._pending_load
            self._pending_load = None

        if not pending_path and gen is not None:
            threading.Timer(0.3, self._try_auto_advance, args=[gen]).start()

        Log.warning("mplayer process exited; restarting")
        self._start_process()

        if pending_path:
            is_url = self._is_url(pending_path)
            with self._lock:
                self.state = "playing"
                self._grace_until = time.monotonic() + 0.5
                self._stream_confirmed = not is_url
                self._reset_watchdog(self._generation, is_url, initial=True)
            escaped = pending_path.replace("\\", "\\\\").replace('"', '\\"')
            Log.info("mplayer << loadfile %s (deferred after restart)", pending_path)
            self._send(f'loadfile "{escaped}"')

    def _eof_watchdog(self, gen):
        with self._lock:
            if gen != self._generation or self.state != "playing":
                return
            failed = self.current_file
            self.state = "stopped"
        if self._is_url(failed or ""):
            Log.error("Stream URL failed or timed out: %s", failed)
        else:
            Log.info("mplayer: track ended")
        self._try_auto_advance(gen)

    def _send(self, cmd):
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(cmd + "\n")
                self._process.stdin.flush()
            except BrokenPipeError:
                Log.warning("mplayer stdin broken pipe on command: %s", cmd)

    def seek(self, fraction):
        with self._lock:
            if self.state in ("playing", "paused") and self.duration > 0:
                secs = fraction * self.duration
                Log.info("mplayer << seek %.2fs (%.1f%%)", secs, fraction * 100)
                self._send(f"seek {secs:.2f} 2")

    def _do_load(self, path):
        is_url = self._is_url(path)
        # mplayer blocks stdin while connecting to a dead stream.
        # Kill it and defer the load to after the process restarts.
        if not self._stream_confirmed and self._pgid:
            Log.info("mplayer: killing hung stream, will load %s after restart", path)
            self._pending_load = path
            self._kill_mplayer()
            return
        self.state = "playing"
        self._grace_until = time.monotonic() + 0.5
        self._stream_confirmed = not is_url
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        Log.info("mplayer << loadfile %s", path)
        self._send(f'loadfile "{escaped}"')
        self._reset_watchdog(self._generation, is_url, initial=True)

    def _do_stop(self):
        self._pending_load = None  # discard any deferred load
        if not self._stream_confirmed and self._pgid:
            Log.info("mplayer: killing stream on stop")
            self._kill_mplayer()
        else:
            self._send("stop")

    def _do_pause_locked(self):
        if self._is_url(self.current_file or ""):
            return  # streams: pause is disabled; use Stop to disconnect
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
