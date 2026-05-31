import configparser
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_conf_path = os.path.join(_here, "pimusic.conf")

_conf = configparser.ConfigParser()
if not _conf.read(_conf_path):
    print(f"ERROR: configuration file not found: {_conf_path}", file=sys.stderr)

MUSIC_ROOT     = _conf.get("music",  "root",    fallback=None) or None
HOST           = _conf.get("server", "host",    fallback="0.0.0.0")
PORT           = _conf.getint("server", "port", fallback=5054)
LOG_CONSOLE    = _conf.getboolean("server", "log_console", fallback=False)
PLAYER_BACKEND = _conf.get("player", "backend", fallback="mpg123")

PLAYLIST_EXTENSIONS = frozenset({".m3u", ".m3u8", ".pls"})
