"""The dashboard's HTTP server.

Two directories are served as if they were one tree: the page itself comes
from the repository's web/ directory, while everything the parser produces
(status.json, the history files, the downloaded game data) comes from the
state directory. Keeping them apart on disk is what lets the checkout stay
read-only; stitching them together here is what lets the page go on
fetching plain relative paths.

This process deliberately knows nothing about parsing saves. A GET of
/status.json that needs fresh data runs `palmon update` as a *subprocess*
and serves the file it writes. Doing the parse in-process instead costs
about 450MB at its peak and leaves ~170MB of it resident afterwards, which
a long-running server never gives back — this way the memory belongs to a
process that exits, and the server itself stays at around 20MB.
"""

import json
import logging
import os
import subprocess
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

from .live import server_live

from .config import (
    CONFIG_FILE,
    LEVEL_SAV,
    OUTPUT_JSON,
    REPO_ROOT,
    STATE_DIR,
    WEB_DIR,
    WEB_HOST,
    WEB_LIVE_MAX_AGE,
    WEB_LIVE_REGENERATE,
    WEB_PORT,
)

log = logging.getLogger(__name__)

# Paths answered from the state directory rather than from web/.
STATE_FILES = {
    "/status.json",
    "/memory_history.json",
    "/resource_history.json",
    "/food_history.json",
}
STATE_PREFIXES = ("/data/",)

# Answered from the REST API on the spot rather than from any file. It is
# three calls and about ten milliseconds, so an open dashboard can poll it
# every minute without ever paying for a save parse — see live.py.
LIVE_PATH = "/live.json"

# A parse takes ~3s; this only has to be long enough that a slow one isn't
# killed halfway through leaving the lock held.
UPDATE_TIMEOUT_SECONDS = 120


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _needs_refresh() -> bool:
    """Whether status.json is worth regenerating before answering.

    Two reasons, and neither of them is "a browser asked". The save is
    rewritten by the server every few minutes; between two writes a re-parse
    produces byte-identical results, so the only honest trigger is the save
    having moved on. The age ceiling covers what isn't in the save at all —
    the player list and uptime, which come from the REST API.
    """
    status_at = _mtime(OUTPUT_JSON)
    if not status_at:
        return True
    if status_at < _mtime(LEVEL_SAV):
        return True
    return (time.time() - status_at) > WEB_LIVE_MAX_AGE


def _run_update() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [REPO_ROOT, env.get("PYTHONPATH", "")]))
    if CONFIG_FILE:
        env["PALMON_CONFIG"] = CONFIG_FILE
    try:
        subprocess.run([sys.executable, "-m", "palmon.cli", "update"],
                       cwd=REPO_ROOT, env=env, timeout=UPDATE_TIMEOUT_SECONDS,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True)
    except Exception as e:
        # A failed refresh is not a failed request: the previous status.json
        # is still there, and slightly stale numbers beat an error page.
        log.warning("refresh failed: %s", e)


class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean in STATE_FILES or clean.startswith(STATE_PREFIXES):
            self.directory = STATE_DIR
        else:
            self.directory = WEB_DIR
        return super().translate_path(path)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == LIVE_PATH:
            self.send_live()
            return
        if WEB_LIVE_REGENERATE and path == "/status.json" and _needs_refresh():
            _run_update()
        super().do_GET()

    def send_live(self):
        try:
            body = json.dumps(server_live(), ensure_ascii=False).encode("utf-8")
        except Exception as e:
            log.warning("live query failed: %s", e)
            self.send_error(503, "live data unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Always a fresh read; a cached one would defeat the point.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def serve() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        print(f"no index.html under {WEB_DIR}", file=sys.stderr)
        return 1
    # Single-threaded on purpose: requests are handled one at a time, which
    # is fine for a personal dashboard and means no reasoning about
    # concurrent handlers. The cross-process lock in status.py still guards
    # against the timer job racing a refresh started here.
    httpd = HTTPServer((WEB_HOST, WEB_PORT), DashboardHandler)
    print(f"serving {WEB_DIR} on http://{WEB_HOST}:{WEB_PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
