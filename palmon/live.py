"""What the server says about itself, as opposed to what its save file holds.

Three REST calls, about ten milliseconds, and not one of them needs
Level.sav — which is the whole reason this lives apart from status.py. The
two halves of the dashboard move at completely different speeds: base and
pal data can only change when the server rewrites the save (every few
minutes), while who is online changes the moment somebody logs in. Folding
both into one document meant the only way to refresh the cheap half was to
redo the expensive one, a ~3s parse to pick up a number that took 10ms to
fetch.

So status.py calls this to fill in status.json (the timer still writes one
complete document), and the web server serves the same dict at /live.json,
which is what an open dashboard polls in between saves.
"""

from datetime import datetime

from .history import load_cache, save_cache
from .restapi import fetch_rest_api


def _uptime_display(seconds) -> str:
    h, rem = divmod(int(seconds or 0), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def server_live(metrics: "dict | None" = None, fetch_metrics: bool = True) -> dict:
    """Everything in status.json that comes from the REST API rather than
    the save.

    metrics can be passed in when the caller has already fetched it (the
    update path does, since it needs to know whether the server answered
    before it decides how to handle a parse failure).

    When the server doesn't answer, the last known name/version/day count
    come back from the cache instead of blanking, and offline_since reports
    when it was last seen — anchored to the last confirmed online timestamp,
    so it stays accurate to within one poll rather than one update cycle.
    """
    if fetch_metrics and metrics is None:
        metrics = fetch_rest_api("metrics")
    online = metrics is not None
    info = fetch_rest_api("info") if online else None
    players = fetch_rest_api("players") if online else None

    server_name = info.get("servername", "") if info else ""
    server_version = info.get("version", "") if info else ""
    game_days = metrics.get("days", 0) if metrics else 0

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = load_cache()
    offline_since = None
    if online:
        save_cache({
            "game_days": game_days,
            "server_name": server_name,
            "server_version": server_version,
            "last_online_at": now_str,
        })
    else:
        game_days = game_days or cache.get("game_days", 0)
        server_name = server_name or cache.get("server_name", "")
        server_version = server_version or cache.get("server_version", "")
        offline_since = cache.get("offline_since") or cache.get("last_online_at") or now_str
        save_cache({
            "game_days": game_days,
            "server_name": server_name,
            "server_version": server_version,
            "last_online_at": cache.get("last_online_at", now_str),
            "offline_since": offline_since,
        })

    # Who is online is matched on playerId, not on name: the REST API keys
    # players by the same hyphen-free hex uid the save uses, and a name is
    # neither unique nor stable.
    online_uids = sorted({
        pl.get("playerId", "").upper() for pl in (players or {}).get("players", [])
        if pl.get("playerId")
    })

    return {
        "server_online": online,
        "server_name": server_name,
        "server_version": server_version,
        "uptime_display": _uptime_display(metrics.get("uptime", 0) if metrics else 0),
        "game_days": game_days,
        "online_players": metrics.get("currentplayernum", 0) if metrics else 0,
        "max_players": metrics.get("maxplayernum", 8) if metrics else 8,
        "offline_since": offline_since,
        "online_uids": online_uids,
    }
