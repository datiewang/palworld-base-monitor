"""Where the save is, how to reach the server, and where state is written.

Everything installation-specific lives here and nowhere else: no other
module contains a path, a port, a password or a base-camp id. Values come
from a TOML file, and every one of them has a default that works for a
dedicated server installed by SteamCMD under the current user, so a fresh
install usually needs a config file only for the REST API password.

The file is looked for in this order:

    1. $PALMON_CONFIG                       (what ``--config`` sets)
    2. $XDG_CONFIG_HOME/palmon/config.toml  (~/.config/palmon/config.toml)
    3. <repository>/config.toml

Names are read at import time, so ``--config`` has to be turned into the
environment variable before the rest of the package is imported — cli.py
does that, and it is the reason this module imports nothing from the rest
of the package.

Two directories matter and they are deliberately separate:

    web/   in the repository — the dashboard page. Never written to.
    state  outside it (~/.local/share/palmon by default) — status.json,
           the history files, the lock, and the downloaded game data.

That split is what lets the repository be a checkout that can be pulled
over, and the state directory be the thing worth backing up.
"""

import glob
import os
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _xdg(var: str, default: str) -> str:
    return os.environ.get(var) or os.path.join(os.path.expanduser("~"), default)


def _find_config() -> "str | None":
    for path in (os.environ.get("PALMON_CONFIG"),
                 os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "palmon", "config.toml"),
                 os.path.join(REPO_ROOT, "config.toml")):
        if path and os.path.isfile(path):
            return path
    return None


CONFIG_FILE = _find_config()
if CONFIG_FILE:
    with open(CONFIG_FILE, "rb") as _f:
        _CFG = tomllib.load(_f)
else:
    _CFG = {}


def setting(section: str, key: str, default):
    """One config value, with an environment override.

    PALMON_SERVER_SAVE_PATH beats [server] save_path, so a systemd unit or a
    container can set anything without a file. Environment values are
    strings; they are coerced to the default's type.
    """
    env = os.environ.get(f"PALMON_{section.upper()}_{key.upper()}")
    if env is not None:
        if isinstance(default, bool):
            return env.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(env)
        if isinstance(default, float):
            return float(env)
        if isinstance(default, list):
            return [p for p in (s.strip() for s in env.split(",")) if p]
        return env
    return _CFG.get(section, {}).get(key, default)


# ─── Where the save file is ───────────────────────────────────────────────
# A dedicated server keeps its world under a directory named after the save
# id, which is generated per world — so the path can't be a constant and is
# discovered instead. If several worlds exist, the most recently written one
# is the one being played.
SAVE_SEARCH_GLOBS = [
    "~/.local/share/Steam/steamapps/common/PalServer/Pal/Saved/SaveGames/0/*/Level.sav",
    "~/Steam/steamapps/common/PalServer/Pal/Saved/SaveGames/0/*/Level.sav",
    "~/PalServer/Pal/Saved/SaveGames/0/*/Level.sav",
    "/opt/palworld/Pal/Saved/SaveGames/0/*/Level.sav",
    "/srv/palworld/Pal/Saved/SaveGames/0/*/Level.sav",
]


def discover_save() -> str:
    found = []
    for pattern in SAVE_SEARCH_GLOBS:
        found += glob.glob(os.path.expanduser(pattern))
    if not found:
        return ""
    return max(found, key=lambda p: os.path.getmtime(p))


LEVEL_SAV = os.path.expanduser(setting("server", "save_path", "") or discover_save())
PLAYERS_DIR = os.path.join(os.path.dirname(LEVEL_SAV), "Players") if LEVEL_SAV else ""

# ─── The server's own REST API ────────────────────────────────────────────
# Enabled by RESTAPIEnabled=True in PalWorldSettings.ini; the password is
# that file's AdminPassword. It is read from the config file and never has a
# default, because a default password in a repository is worse than an
# error message.
REST_API_URL = setting("server", "rest_api_url", "http://127.0.0.1:8212/v1/api")
AUTH_USER = setting("server", "rest_api_user", "admin")
AUTH_PASS = setting("server", "rest_api_password", "")

# ─── Which camps are "our" bases ──────────────────────────────────────────
# BaseCampSaveData lists every camp in the world, other guilds' included.
# Anchors are worker-container ids that pin a camp to a stable baseN name so
# its accumulated history keeps meaning the same base; see _get_base_meta.
# Both settings are optional: with neither, the guild owning the most camps
# is taken as the player's and its camps are numbered by camp id.
BASE_ANCHORS = setting("bases", "anchors", [])
GUILD_ID = setting("bases", "guild_id", "")

# ─── Where state is written ───────────────────────────────────────────────
STATE_DIR = os.path.expanduser(
    setting("paths", "state_dir", os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "palmon")))
DATA_DIR = os.path.expanduser(setting("paths", "data_dir", os.path.join(STATE_DIR, "data")))
WEB_DIR = os.path.expanduser(setting("paths", "web_dir", os.path.join(REPO_ROOT, "web")))

OUTPUT_JSON = os.path.join(STATE_DIR, "status.json")
CACHE_FILE = os.path.join(STATE_DIR, ".metrics_cache.json")
LOCK_FILE = os.path.join(STATE_DIR, ".update.lock")
MEMORY_HISTORY_FILE = os.path.join(STATE_DIR, "memory_history.json")
RESOURCE_HISTORY_FILE = os.path.join(STATE_DIR, "resource_history.json")
FOOD_HISTORY_FILE = os.path.join(STATE_DIR, "food_history.json")

BUILDINGS_META_FILE = os.path.join(DATA_DIR, "buildings_meta.json")
ITEMS_META_FILE = os.path.join(DATA_DIR, "items_meta.json")
PALS_META_FILE = os.path.join(DATA_DIR, "pals_meta.json")

# ─── Retention ────────────────────────────────────────────────────────────
# Sampling is not on a strict clock (the web server triggers a run on every
# page load), so these are sized by how much history is wanted rather than
# by an interval. A resource snapshot is ~8KB against a few hundred bytes
# for a memory point, hence the throttle on the former only.
MEMORY_HISTORY_MAX_POINTS = setting("history", "memory_max_points", 2000)
RESOURCE_HISTORY_MIN_INTERVAL_SECONDS = setting("history", "resource_min_interval_seconds", 240)
RESOURCE_HISTORY_MAX_POINTS = setting("history", "resource_max_points", 500)
FOOD_HISTORY_MAX_POINTS = setting("history", "food_max_points", 2000)

# ─── The dashboard's own HTTP server ──────────────────────────────────────
WEB_HOST = setting("web", "host", "0.0.0.0")
WEB_PORT = setting("web", "port", 8088)
# Whether GET /status.json re-parses the save before answering. On, the page
# is never stale and each request costs about a second; off, it serves
# whatever the last run wrote and the timer alone keeps it fresh.
WEB_LIVE_REGENERATE = setting("web", "live_regenerate", True)


def ensure_dirs() -> None:
    for d in (STATE_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)


def describe() -> str:
    return "\n".join([
        f"config file : {CONFIG_FILE or '(none — using defaults)'}",
        f"save        : {LEVEL_SAV or '(not found)'}",
        f"rest api    : {REST_API_URL} as {AUTH_USER}"
        f"{'' if AUTH_PASS else '  [no password set]'}",
        f"state dir   : {STATE_DIR}",
        f"game data   : {DATA_DIR}",
        f"web root    : {WEB_DIR}",
        f"listen      : {WEB_HOST}:{WEB_PORT}",
    ])


def check(stream=sys.stderr) -> bool:
    """Report anything that will stop a run from working. Called by the CLI."""
    ok = True
    if not LEVEL_SAV or not os.path.isfile(LEVEL_SAV):
        print("no Level.sav found — set [server] save_path in the config file",
              file=stream)
        ok = False
    if not AUTH_PASS:
        print("no REST API password — set [server] rest_api_password "
              "(player list and uptime will be empty without it)", file=stream)
    if not os.path.isfile(ITEMS_META_FILE):
        print("game data missing — run 'palmon fetch-data'", file=stream)
    return ok
