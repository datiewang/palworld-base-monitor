"""Assembling status.json — the single document the dashboard page reads.

analyze_data() is the entry point for both the CLI and the web server: it
takes the cross-process lock, parses the save, computes every metric, and
writes the file. When a run fails it degrades in steps (last good status,
then a blank one) rather than leaving a half-written file behind.
"""

import logging
import json
import fcntl
from datetime import datetime

from .config import LEVEL_SAV, LOCK_FILE, OUTPUT_JSON, ensure_dirs
from .history import (
    append_food_history,
    append_memory_history,
    append_resource_history,
    load_cache,
    save_cache,
)
from .metrics import (
    classify_base_role,
    compute_facility_idle,
    compute_food_consumption,
    compute_food_depletion,
    compute_resource_gross_rate,
    compute_work_ratio,
    get_memory_info,
    summarize_food_storage,
)
from .restapi import fetch_rest_api
from .save import read_player_file, decompress_save, parse_save_data

log = logging.getLogger(__name__)


def build_status_json(parsed: dict, metrics: dict | None) -> dict:
    """Build the final status.json output."""

    # Server metrics (from REST API)
    server_online = metrics is not None
    server_fps = round(metrics.get("serverfps", 60.0), 1) if metrics else 60.0
    uptime = metrics.get("uptime", 0) if metrics else 0
    online_players = metrics.get("currentplayernum", 0) if metrics else 0
    max_players = metrics.get("maxplayernum", 8) if metrics else 8

    container_pals = parsed["container_pals"]

    # Base camp pals, per base, sorted by slot index. Keyed off whatever
    # bases _get_base_meta found rather than a fixed pair — everything
    # downstream (history, rates, idle, the status.json field names) is
    # driven by base_keys, so a base built in-game shows up on the
    # dashboard without a code change.
    base_containers = parsed.get("base_containers", {})
    base_keys = sorted(base_containers, key=lambda k: int(k[4:]))
    base_pals = {
        key: sorted(
            container_pals.get(container, []),
            key=lambda p: p.get("slot_index", 999),
        )
        for key, container in base_containers.items()
    }

    # Remove internal fields from output — instance_id stays a little
    # longer, it's still needed below to key the work-history snapshot.
    all_base_pals = [pal for key in base_keys for pal in base_pals[key]]
    for pal in all_base_pals:
        pal.pop("slot_index", None)
    warning_count = sum(1 for p in all_base_pals if p["status_code"] == "WARNING")
    danger_count = sum(1 for p in all_base_pals if p["status_code"] == "DANGER")

    # Fetch additional data from REST API
    server_info = fetch_rest_api("info")
    players_data = fetch_rest_api("players")

    server_name = server_info.get("servername", "") if server_info else ""
    server_version = server_info.get("version", "") if server_info else ""
    game_days = metrics.get("days", 0) if metrics else 0

    # If server offline, try loading cached game_days. Also track when the
    # server was last seen online, so an offline run can report *when* it
    # went down instead of just "offline" — anchored to the last confirmed
    # online timestamp (accurate to within one update interval).
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = load_cache()
    offline_since = None
    if metrics:
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

    # Memory info
    mem = get_memory_info()
    # Appended (and persisted to MEMORY_HISTORY_FILE) for its own sake, not
    # for the return value: memory_history/food_history are deliberately NOT
    # inlined into status.json any more. Together they were ~212KB of a
    # ~370KB payload that the dashboard re-fetched on every poll even though
    # both charts are collapsed by default. The frontend now reads
    # memory_history.json / food_history.json directly, and only while a
    # chart is actually open.
    append_memory_history(mem, online_players)

    # Format uptime
    h, rem = divmod(uptime, 3600)
    m, s = divmod(rem, 60)
    uptime_fmt = f"{h}h {m}m" if h else f"{m}m {s}s"

    # Online player list
    online_list = []
    if players_data and "players" in players_data:
        for pl in players_data["players"]:
            online_list.append({
                "name": pl.get("name", "?"),
                "level": pl.get("level", 0),
                "ping": round(pl.get("ping", 0), 0),
            })

    # All known players (online and offline), each with their current
    # party (Otomo) pals. REST API's "players" endpoint only lists who's
    # online right now, keyed by playerId in the same hyphen-free-hex form
    # as the uid captured above, so it's used purely to flag online/offline
    # here (name/level for the online case already come from the save
    # itself, which is available whether or not the server's REST API is
    # reachable).
    online_uids = {
        pl.get("playerId", "").upper() for pl in (players_data or {}).get("players", [])
    }
    all_players = []
    for p in parsed.get("players", []):
        file_info = read_player_file(p["uid"])
        otomo_id = file_info.get("otomo_container_id")
        otomo_pals = container_pals.get(otomo_id, []) if otomo_id else []
        for pal in otomo_pals:
            pal.pop("slot_index", None)
            pal.pop("instance_id", None)
        all_players.append({
            "name": p["name"],
            "level": p["level"],
            "online": p["uid"] in online_uids,
            "last_online": file_info.get("last_online"),
            "pals": otomo_pals,
        })
    # Two stable passes instead of one composite key: strings can't be
    # numerically negated for a "descending within ascending" sort.
    all_players.sort(key=lambda p: p["last_online"] or "", reverse=True)
    all_players.sort(key=lambda p: not p["online"])

    # Total pals across all containers
    total_all_pals = sum(len(v) for v in container_pals.values())

    # Base facilities + resource storage, with hourly trend
    base_facilities = parsed.get("base_facilities", {})
    base_resources = parsed.get("base_resources", {})
    base_resources_pending = parsed.get("base_resources_pending", {})
    base_food_storage = parsed.get("base_food_storage", {})
    power_storage = parsed.get("power_storage", {})
    farm_growth_state = parsed.get("farm_growth_state", {})

    pal_jobs = {
        key: {p["instance_id"]: p["job"] for p in pals if p.get("instance_id")}
        for key, pals in base_pals.items()
    }
    resource_history = append_resource_history(base_resources, base_resources_pending, pal_jobs)
    resource_gross_rate = compute_resource_gross_rate(
        resource_history, base_resources, base_resources_pending, window_minutes=60
    )
    work_ratio = compute_work_ratio(resource_history, pal_jobs)
    facility_idle = compute_facility_idle(resource_history, pal_jobs, base_facilities)
    for base_key, pals in base_pals.items():
        ratios = work_ratio.get(base_key, {})
        for pal in pals:
            r = ratios.get(pal.get("instance_id"))
            pal["work_ratio"] = r["ratio"] if r else None
            pal["work_ratio_samples"] = r["samples"] if r else 0
            pal.pop("instance_id", None)

    food_consumption = {k: compute_food_consumption(base_pals[k]) for k in base_keys}
    food_summary = {k: summarize_food_storage(base_food_storage.get(k, {})) for k in base_keys}
    food_history = append_food_history({k: food_summary[k]["satiety_total"] for k in base_keys})
    for key, summary in food_summary.items():
        summary.update(compute_food_depletion(food_history, key, summary["satiety_total"]))

    # Per-base fields stay flat (base1_resources, base2_resources, ...)
    # rather than nesting under a "bases" object: the frontend, the history
    # files and the stale/blank fallbacks all already address them that
    # way, and flattening a generated list costs one loop where migrating
    # every reader would cost a rewrite. "bases" carries the key list so
    # the dashboard can build one panel per base instead of hardcoding two.
    result = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "server_online": server_online,
        "server_name": server_name,
        "server_version": server_version,
        "uptime_display": uptime_fmt,
        "offline_since": offline_since,
        "game_days": game_days,
        "online_players": online_players,
        "max_players": max_players,
        "all_players": all_players,
        "memory": mem,
        "total_all_pals": total_all_pals,
        "bases": base_keys,
        "total_base_pals": len(all_base_pals),
        "warning_count": warning_count,
        "danger_count": danger_count,
    }
    for key in base_keys:
        result[f"{key}_pals"] = base_pals[key]
        result[f"total_{key}_count"] = len(base_pals[key])
        result[f"{key}_facilities"] = base_facilities.get(key, {})
        result[f"{key}_power_storage"] = power_storage.get(key, {"current": 0.0, "capacity": 0})
        result[f"{key}_resources"] = base_resources.get(key, {})
        result[f"{key}_resources_pending"] = base_resources_pending.get(key, {})
        result[f"{key}_gross_rate"] = resource_gross_rate.get(key, {"items": [], "minutes": 0})
        result[f"{key}_facility_idle"] = facility_idle.get(key, {})
        result[f"{key}_farm_growth"] = farm_growth_state.get(key, {})
        result[f"{key}_food_storage"] = food_summary[key]
        result[f"{key}_food_consumption"] = food_consumption[key]
        result[f"{key}_role"] = classify_base_role(
            base_facilities.get(key, {}), resource_gross_rate.get(key, {})
        )
    return result


def analyze_data():
    """Main entry point: decompress, parse, and write status.json.

    Called both by the 5-minute timer and, live, by the web server on every
    dashboard page load — wrapped in a file lock since both paths read and
    rewrite the same memory_history.json / .metrics_cache.json state files.
    """
    with open(LOCK_FILE, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        return _analyze_data_locked()


def _analyze_data_locked():
    # 1) Fetch server metrics via REST API
    metrics = fetch_rest_api("metrics")

    # 2) Decompress and parse Level.sav
    #
    # A parse failure here has two real causes seen in practice, and only
    # one of them should ever blank out the dashboard:
    # - A read race against the game server's own periodic save write —
    #   Level.sav briefly reflects a half-written state. Self-corrects on
    #   the very next read, so one immediate retry clears it.
    # - A genuine gap in the parser (an unhandled property type on some
    #   rarely-populated save section — observed once on
    #   InvaderDeclarationSaveData, which this dashboard doesn't even use)
    #   that a retry won't fix. This is rarer but real, so it still needs
    #   a fallback: reuse the last successfully parsed base data (marked
    #   stale) rather than wiping every base panel to empty, which is far
    #   more disruptive than a dashboard showing a few-minutes-old snapshot.
    try:
        gvas_data = decompress_save(LEVEL_SAV)
        parsed = parse_save_data(gvas_data)
    except Exception:
        try:
            gvas_data = decompress_save(LEVEL_SAV)
            parsed = parse_save_data(gvas_data)
        except Exception as e:
            log.error(f"Failed to parse Level.sav (after retry): {e}")
            stale = _load_last_good_status()
            if stale:
                log.error("Falling back to last successfully parsed base data")
                return _write_stale_status(stale, metrics, str(e))
            return _write_blank_status(metrics, str(e))

    # 3) Build and write status.json
    result = build_status_json(parsed, metrics)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result


def _load_last_good_status() -> "dict | None":
    """The status.json already on disk — used as a fallback source of base
    data when Level.sav can't be parsed this time. Only real base pal
    data makes it usable; a blank status (from _write_blank_status, no
    prior good parse to chain from) returns None. Deliberately NOT
    rejected for already being marked stale — if Level.sav fails to parse
    several updates in a row, each one can keep chaining off the same
    last-good base data instead of only surviving one failure before
    falling through to a full blank-out.
    """
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return None
    if not any(prev.get(f"{key}_pals") for key in prev.get("bases", ["base1", "base2"])):
        return None
    return prev


def _write_stale_status(stale: dict, metrics: "dict | None", error: str) -> dict:
    """Refresh only what doesn't depend on Level.sav (server/memory/online
    status) on top of the last good parse's base data, and mark it
    explicitly stale — data_stale_since is when that base data actually
    came from, not now, so the frontend/user can tell how old it is
    rather than mistaking it for a live read.
    """
    mem = get_memory_info()
    online_players = metrics.get("currentplayernum", 0) if metrics else 0
    append_memory_history(mem, online_players)
    result = dict(stale)
    result.update({
        "server_online": metrics is not None,
        "online_players": online_players,
        "max_players": metrics.get("maxplayernum", 8) if metrics else 8,
        "memory": mem,
        "data_stale": True,
        "data_stale_since": stale.get("updated_at"),
        "parse_error": error,
    })
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result


def _write_blank_status(metrics: "dict | None", error: str) -> dict:
    """Last resort when Level.sav can't be parsed AND there's no previous
    good status.json to fall back on (e.g. right after a fresh install) —
    a minimal status with every base field blanked rather than missing,
    so the frontend has something well-formed to render.
    """
    mem = get_memory_info()
    online_players = metrics.get("currentplayernum", 0) if metrics else 0
    append_memory_history(mem, online_players)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = load_cache()
    offline_since = None
    if metrics:
        save_cache({**cache, "last_online_at": now_str})
    else:
        offline_since = cache.get("offline_since") or cache.get("last_online_at") or now_str
        save_cache({**cache, "last_online_at": cache.get("last_online_at", now_str), "offline_since": offline_since})
    # Which bases to blank: the last status.json's own list if there is one,
    # otherwise the two anchor bases. Nothing here has parsed a save, so
    # there is no other way to know how many bases exist — and a base
    # missing from this fallback renders as an empty panel for one update,
    # which is what the whole blank status is for anyway.
    try:
        with open(OUTPUT_JSON, encoding="utf-8") as f:
            base_keys = json.load(f).get("bases") or ["base1", "base2"]
    except Exception:
        base_keys = ["base1", "base2"]

    result = {
        "updated_at": now_str,
        "server_online": metrics is not None,
        "offline_since": offline_since,
        "online_players": online_players,
        "max_players": metrics.get("maxplayernum", 8) if metrics else 8,
        "memory": mem,
        "bases": base_keys,
        "total_base_pals": 0,
        "warning_count": 0,
        "danger_count": 0,
        "all_players": [],
        "parse_error": error,
    }
    for key in base_keys:
        result[f"{key}_pals"] = []
        result[f"total_{key}_count"] = 0
        result[f"{key}_facilities"] = {}
        result[f"{key}_power_storage"] = {"current": 0.0, "capacity": 0}
        result[f"{key}_resources"] = {}
        result[f"{key}_resources_pending"] = {}
        result[f"{key}_gross_rate"] = {"items": [], "minutes": 0}
        result[f"{key}_facility_idle"] = {}
        result[f"{key}_farm_growth"] = {}
        result[f"{key}_food_storage"] = {
            "satiety_total": 0, "unmapped_items": {},
            "hours_remaining": None,
        }
        result[f"{key}_food_consumption"] = 0
        result[f"{key}_role"] = {
            "counts": {}, "tags": [], "primary": None,
            "output": {}, "output_minutes": 0, "stalled": [],
        }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return result
