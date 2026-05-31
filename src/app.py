import logging
import os

from flask import Flask, jsonify, render_template, request

try:
    from mutagen import File as _MutagenFile
    def _duration(path):
        try:
            f = _MutagenFile(path)
            if f and hasattr(f, "info"):
                s = int(f.info.length)
                m, s = divmod(s, 60)
                h, m = divmod(m, 60)
                return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        except Exception:
            pass
        return None
except ImportError:
    def _duration(_): return None

import OSTools
from OSTools import Log
from config import HOST, LOG_CONSOLE, MUSIC_ROOT, PLAYER_BACKEND, PLAYLIST_EXTENSIONS, PORT
from player import create_player

OSTools.setupRotatingLogger("PiMusic", LOG_CONSOLE)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
_player = create_player(PLAYER_BACKEND)
Log.info("PiMusic started — backend: %s, music root: %s", PLAYER_BACKEND, MUSIC_ROOT)


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

    try:
        names = sorted(
            os.listdir(abs_path),
            key=lambda n: (not os.path.isdir(os.path.join(abs_path, n)), n.lower()),
        )
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    entries = []
    for name in names:
        full = os.path.join(abs_path, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isdir(full):
            entries.append({"name": name, "type": "dir",
                            "path": f"{rel}/{name}".lstrip("/")})
        elif ext in _player.audio_extensions:
            entries.append({"name": name, "type": "audio",
                            "path": f"{rel}/{name}".lstrip("/"),
                            "duration": _duration(full)})
        elif ext in PLAYLIST_EXTENSIONS:
            entries.append({"name": name, "type": "playlist",
                            "path": f"{rel}/{name}".lstrip("/")})

    parent = rel.rsplit("/", 1)[0] if "/" in rel else ("" if rel else None)

    return jsonify({"path": rel, "parent": parent, "entries": entries})


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
    return jsonify({"ok": True})


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


@app.route("/api/seek", methods=["POST"])
def seek():
    data = request.get_json(force=True, silent=True) or {}
    try:
        position = float(data["position"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "position required"}), 400
    _player.seek(position)
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    s = _player.status()
    cf = s.pop("current_file", None)
    if cf and not cf.startswith(("http://", "https://", "mms://", "rtmp://", "rtsp://")):
        if MUSIC_ROOT:
            root = os.path.realpath(MUSIC_ROOT)
            cf_real = os.path.realpath(cf)
            s["current_path"] = cf_real[len(root):].lstrip("/") if cf_real.startswith(root) else None
        else:
            s["current_path"] = None
    else:
        s["current_path"] = None
    return jsonify(s)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, threaded=True)
