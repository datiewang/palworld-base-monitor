"""The dashboard's HTTP server.

Two directories are served as if they were one tree: the page itself comes
from the repository's web/ directory, while everything the parser produces
(status.json, the history files, the downloaded game data) comes from the
state directory. Keeping them apart on disk is what lets the checkout stay
read-only; stitching them together here is what lets the page go on
fetching plain relative paths.

GET /status.json re-parses the save before answering, so the page is never
older than the moment it was asked for. That costs about a second per
request, which is the right trade for a LAN dashboard one person has open;
set [web] live_regenerate = false to serve whatever the timer last wrote.
"""

import logging
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

from . import status
from .config import STATE_DIR, WEB_DIR, WEB_HOST, WEB_LIVE_REGENERATE, WEB_PORT

log = logging.getLogger(__name__)

# Paths answered from the state directory rather than from web/.
STATE_FILES = {
    "/status.json",
    "/memory_history.json",
    "/resource_history.json",
    "/food_history.json",
}
STATE_PREFIXES = ("/data/",)


class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean in STATE_FILES or clean.startswith(STATE_PREFIXES):
            self.directory = STATE_DIR
        else:
            self.directory = WEB_DIR
        return super().translate_path(path)

    def do_GET(self):
        if WEB_LIVE_REGENERATE and self.path.split("?", 1)[0] == "/status.json":
            try:
                status.analyze_data()
            except Exception as e:
                # A failed regenerate is not a failed request: analyze_data
                # leaves the previous status.json in place, and serving
                # slightly stale numbers beats serving an error page.
                log.warning("live regenerate failed: %s", e)
        super().do_GET()

    def log_message(self, fmt, *args):
        pass


def serve() -> int:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        print(f"no index.html under {WEB_DIR}", file=sys.stderr)
        return 1
    # Single-threaded on purpose: requests are handled one at a time, which
    # is fine at ~1s each for a personal dashboard and means no reasoning
    # about concurrent handlers. The cross-process lock in status.py still
    # guards against the timer job racing a live request.
    httpd = HTTPServer((WEB_HOST, WEB_PORT), DashboardHandler)
    print(f"serving {WEB_DIR} on http://{WEB_HOST}:{WEB_PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
