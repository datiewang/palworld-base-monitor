"""The Palworld dedicated server's own REST API (the one it serves on 8212).

Used for the things a save file can't answer: who is online right now, the
server's uptime, and its self-reported metrics.
"""

import json
import base64
import urllib.request

from .config import AUTH_PASS, AUTH_USER, REST_API_URL


def fetch_rest_api(endpoint: str):
    """Fetch data from Palworld REST API."""
    try:
        url = f"{REST_API_URL}/{endpoint}"
        req = urllib.request.Request(url)
        credentials = f"{AUTH_USER}:{AUTH_PASS}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        req.add_header("Authorization", f"Basic {encoded_credentials}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode('utf-8'))
    except Exception:
        pass
    return None
