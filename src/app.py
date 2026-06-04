import json
import logging
import os
import re
import subprocess
import threading
import time

from flask import Flask, jsonify, render_template, request

import OSTools
from OSTools import Log
from config import HOST, LOG_CONSOLE, MUSIC_ROOT, PLAYER_BACKEND, PLAYLIST_EXTENSIONS, PORT
from player import create_player

OSTools.setupRotatingLogger("PiMusic", LOG_CONSOLE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

@app.context_processor
def _inject_static_ver():
    def static_ver(filename):
        try:
            return int(os.path.getmtime(os.path.join(_STATIC_DIR, filename)))
        except OSError:
            return 0
    return {"static_ver": static_ver}
_player = create_player(PLAYER_BACKEND)
Log.info("PiMusic started — backend: %s, music root: %s", PLAYER_BACKEND, MUSIC_ROOT)

# ---------------------------------------------------------------------------
# Duration cache (mediainfo, background)
# ---------------------------------------------------------------------------

_dur_cache = {}
_dur_lock = threading.Lock()
_NOT_CACHED = object()  # sentinel: distinguishes "not tried" from "tried, no result"
_retriever_lock = threading.Lock()  # only one retriever thread at a time
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "duration_cache.json")


def _fmt_dur(secs):
    mins, s = divmod(secs, 60)
    h, m = divmod(mins, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{mins}:{s:02d}"


def _load_duration_cache():
    try:
        with open(_CACHE_FILE) as f:
            data = json.load(f)
        with _dur_lock:
            for path, secs in data.items():
                if isinstance(secs, int) and secs > 0:
                    _dur_cache[path] = secs
        Log.info("Duration cache loaded: %d entries from %s", len(_dur_cache), _CACHE_FILE)
    except FileNotFoundError:
        pass
    except Exception as e:
        Log.warning("Could not load duration cache: %s", e)


def _save_duration_cache():
    try:
        with _dur_lock:
            data = {k: v for k, v in _dur_cache.items() if isinstance(v, int) and v > 0}
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        Log.warning("Could not save duration cache: %s", e)


def _fetch_durations(root):
    if not _retriever_lock.acquire(blocking=False):
        Log.info("Duration retriever: already running, skipping new request")
        return
    try:
        all_found = set()
        queue = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.splitext(name)[1].lower() in _player.audio_extensions:
                    all_found.add(full)
                    with _dur_lock:
                        if full not in _dur_cache:
                            queue.append(full)

        with _dur_lock:
            stale = [p for p in _dur_cache if p.startswith(root) and p not in all_found]
            for p in stale:
                del _dur_cache[p]
        if stale:
            _save_duration_cache()
            Log.info("Duration retriever: removed %d stale cache entr%s",
                     len(stale), "y" if len(stale) == 1 else "ies")

        if not queue:
            Log.info("Duration retriever: nothing new under %s", root)
            return

        Log.info("Duration retriever: started, %d file(s) queued under %s",
                 len(queue), os.path.relpath(root, MUSIC_ROOT) or '/')
        waiting = False
        for path in queue:
            while _player.status()["state"] != "stopped":
                if not waiting:
                    Log.info("Duration retriever: paused — player is active")
                    waiting = True
                time.sleep(3)
            if waiting:
                Log.info("Duration retriever: resumed")
                waiting = False

            secs = None
            try:
                with open(path, 'rb') as f:
                    f.read(4096)  # wake the NAS drive before mediainfo reads headers
            except OSError as e:
                Log.warning("Duration retriever: cannot read %s: %s", os.path.basename(path), e)
                with _dur_lock:
                    _dur_cache[path] = None
                continue
            try:
                r = subprocess.run(
                    ["nice", "-n", "19", "mediainfo", "--Inform=General;%Duration%", path],
                    capture_output=True, text=True, timeout=60,
                )
                ms_str = r.stdout.strip()
                if ms_str:
                    s = int(float(ms_str)) // 1000
                    if 0 < s < 86400 * 7:
                        secs = s
            except FileNotFoundError:
                Log.warning("mediainfo not found — install it for track duration display")
                return
            except Exception as e:
                Log.warning("Duration retriever: error on %s: %s", os.path.basename(path), e)
            with _dur_lock:
                _dur_cache[path] = secs
            if secs:
                _save_duration_cache()
                Log.info("Duration retriever: %s → %s — UI will refresh", os.path.basename(path), _fmt_dur(secs))
            else:
                Log.warning("Duration retriever: %s → no duration found", os.path.basename(path))

        Log.info("Duration retriever: finished")
    except Exception as e:
        Log.error("Duration retriever: crashed — %s", e)
    finally:
        _retriever_lock.release()


def _check_root():
    """Return an error dict if MUSIC_ROOT is unusable, else None."""
    if not MUSIC_ROOT:
        return {"error": "not_configured",
                "message": "Music root is not set. Edit pimusic.conf and set root = /path/to/music under [music]."}
    if not os.path.isdir(MUSIC_ROOT):
        return {"error": "not_found",
                "message": f"Music root '{MUSIC_ROOT}' does not exist or is not mounted. Check pimusic.conf and your NAS mount."}
    return None


def _safe_abs(rel_path):
    """Resolve rel_path under MUSIC_ROOT; return None if it escapes the root."""
    root = os.path.realpath(MUSIC_ROOT)
    candidate = os.path.realpath(os.path.join(root, rel_path.lstrip("/")))
    return candidate if candidate.startswith(root) else None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# File browser
# ---------------------------------------------------------------------------

@app.route("/api/files")
def list_files():
    root_err = _check_root()
    if root_err:
        return jsonify(root_err), 503

    rel = request.args.get("path", "").strip("/")
    abs_path = _safe_abs(rel)
    if abs_path is None or not os.path.isdir(abs_path):
        return jsonify({"error": "Invalid path"}), 400

    def _sort_key(n):
        m = re.match(r'^(\d+)', n)
        num = int(m.group(1)) if m else float('inf')
        return (not os.path.isdir(os.path.join(abs_path, n)), num, n.lower())

    try:
        names = sorted(os.listdir(abs_path), key=_sort_key)
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    entries = []
    uncached = []
    for name in names:
        full = os.path.join(abs_path, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isdir(full):
            entries.append({"name": name, "type": "dir",
                            "path": f"{rel}/{name}".lstrip("/")})
        elif ext in _player.audio_extensions:
            with _dur_lock:
                cached = _dur_cache.get(full, _NOT_CACHED)
            if cached is _NOT_CACHED:
                uncached.append(full)
                dur_str = None
            else:
                dur_str = _fmt_dur(cached) if cached else None
            entries.append({"name": name, "type": "audio",
                            "path": f"{rel}/{name}".lstrip("/"),
                            "duration": dur_str})
        elif ext in PLAYLIST_EXTENSIONS:
            entries.append({"name": name, "type": "playlist",
                            "path": f"{rel}/{name}".lstrip("/")})

    threading.Thread(target=_fetch_durations, args=(os.path.realpath(MUSIC_ROOT),), daemon=True).start()

    parent = rel.rsplit("/", 1)[0] if "/" in rel else ("" if rel else None)

    return jsonify({"path": rel, "parent": parent, "entries": entries,
                    "pending_durations": len(uncached)})


# ---------------------------------------------------------------------------
# Player controls
# ---------------------------------------------------------------------------

@app.route("/api/play", methods=["POST"])
def play():
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip("/")
    abs_path = _safe_abs(rel)
    if abs_path is None or not os.path.isfile(abs_path):
        return jsonify({"error": "File not found"}), 404
    Log.info("Play: %s", rel)
    _player.play(abs_path)
    return status()


@app.route("/api/pause", methods=["POST"])
def pause():
    state = _player.status()["state"]
    Log.info("Pause" if state == "playing" else "Resume")
    _player.pause()
    return jsonify({"ok": True})


@app.route("/api/next", methods=["POST"])
def next_track():
    Log.info("Next track")
    _player.next()
    return jsonify({"ok": True})


@app.route("/api/previous", methods=["POST"])
def previous_track():
    Log.info("Previous track")
    _player.previous()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def stop():
    Log.info("Stop")
    _player.stop()
    return jsonify({"ok": True})


@app.route("/api/shuffle", methods=["POST"])
def toggle_shuffle():
    return jsonify({"ok": True, "shuffle": _player.toggle_shuffle()})


@app.route("/api/seek", methods=["POST"])
def seek():
    data = request.get_json(force=True, silent=True) or {}
    try:
        fraction = float(data["fraction"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "fraction required"}), 400
    _player.seek(max(0.0, min(1.0, fraction)))
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    s = _player.status()
    root = os.path.realpath(MUSIC_ROOT) if MUSIC_ROOT else None

    cf = s.pop("current_file", None)
    if cf and not cf.startswith(("http://", "https://", "mms://", "rtmp://", "rtsp://")):
        if root:
            cf_real = os.path.realpath(cf)
            s["current_path"] = cf_real[len(root):].lstrip("/") if cf_real.startswith(root) else None
            with _dur_lock:
                cached_secs = _dur_cache.get(cf_real, _NOT_CACHED)
            if cached_secs is not _NOT_CACHED and cached_secs:
                s["duration"] = cached_secs
        else:
            s["current_path"] = None
    else:
        s["current_path"] = None

    sp = s.pop("source_path", None)
    if sp and root:
        sp_real = os.path.realpath(sp)
        s["source_path"] = sp_real[len(root):].lstrip("/") if sp_real.startswith(root) else None
    else:
        s["source_path"] = None

    return jsonify(s)


# ---------------------------------------------------------------------------

_load_duration_cache()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, threaded=True)
