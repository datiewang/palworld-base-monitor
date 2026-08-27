"""The append-only time series behind every trend on the dashboard.

Three files, each with its own retention rule, plus the small metrics cache.
Sampling policy lives here too: callers append on every run and these
functions decide whether the point is actually recorded.
"""

import json
from datetime import datetime

from .config import (
    CACHE_FILE,
    FOOD_HISTORY_FILE,
    FOOD_HISTORY_MAX_POINTS,
    MEMORY_HISTORY_FILE,
    MEMORY_HISTORY_MAX_POINTS,
    RESOURCE_HISTORY_FILE,
    RESOURCE_HISTORY_MAX_POINTS,
    RESOURCE_HISTORY_MIN_INTERVAL_SECONDS,
)

# Bumped whenever a fix changes what the baseN_jobs columns *mean*, so
# readers can ignore snapshots recorded by a version known to have got them
# wrong instead of averaging bad data into a trend. Version 2:
# _parse_work_assignments stopped collapsing every worker at a multi-slot
# facility down to one (see its docstring) — before it, "no job" in a
# snapshot could mean either "idle" or "dropped by the parser", which is
# exactly the ambiguity a work ratio can't be computed from. The
# alternative, rewriting the old snapshots to what we now think was true,
# would be inventing observations that were never made.
PAL_JOBS_SCHEMA = 2


def append_memory_history(mem: dict, online_players: int = 0) -> list:
    """Append the current memory + online-player snapshot to the rolling history and persist it."""
    try:
        with open(MEMORY_HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        history = []

    history.append({
        "t": datetime.now().strftime("%H:%M"),
        "sys_pct": mem.get("system_pct", 0),
        "palserver_mb": mem.get("palserver_mb", 0),
        "online_players": online_players,
    })
    history = history[-MEMORY_HISTORY_MAX_POINTS:]

    try:
        with open(MEMORY_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception:
        pass

    return history


def load_cache() -> dict:
    """Load cached metrics for when server is offline."""
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(data: dict):
    """Save metrics cache."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def append_resource_history(resources: dict, pending_resources: dict, pal_jobs: dict) -> list:
    """Append the current per-base resource + pal-job snapshot to the
    rolling history and persist it. Uses epoch seconds (not the
    memory-history HH:MM convention) since trend windows span hours/days
    and need to survive a midnight rollover.

    Stores stored and pending (gathering-node backlog) quantities under
    separate keys (base1/base2 vs base1_pending/base2_pending) in the same
    snapshot; compute_resource_gross_rate reads both and combines them.
    pal_jobs (base1_jobs/base2_jobs: {instance_id: facility_type_or_None})
    is what compute_work_ratio uses to turn "was this pal assigned a job at
    each sample" into "% of observed time this pal has spent working".

    Pal Food Box contents were tried here too (base1_food/base2_food) to
    derive a production/consumption rate the same way, but players moving
    food in and out by hand makes a raw item-count delta indistinguishable
    from a pal eating — rejected in favor of compute_food_consumption,
    which uses each pal's own food_amount stat instead of history.

    Unlike memory_history, this is throttled to RESOURCE_HISTORY_MIN_INTERVAL
    regardless of how often the caller runs: the dashboard's live path
    (pal_base_web_server.py) recomputes on every GET /status.json, which the
    frontend polls every 60s while a tab is open. A memory_history point is
    a few hundred bytes, so appending on every live poll is free; a resource
    snapshot holds full per-base item counts and measured ~8KB/point, so
    appending unthrottled would mean rewriting a multi-MB file to disk on
    every poll for as long as someone leaves the dashboard open. Throttling
    to disk writes every few minutes keeps the same hours-long trend window
    at a fraction of the I/O.
    """
    try:
        with open(RESOURCE_HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        history = []

    now = datetime.now()
    now_ts = int(now.timestamp())
    if history and now_ts - history[-1].get("ts", 0) < RESOURCE_HISTORY_MIN_INTERVAL_SECONDS:
        return history

    snapshot = {"ts": now_ts, "t": now.strftime("%Y-%m-%d %H:%M"),
                "jobs_v": PAL_JOBS_SCHEMA, **resources}
    for base_key, items in pending_resources.items():
        snapshot[f"{base_key}_pending"] = items
    for base_key, jobs in pal_jobs.items():
        snapshot[f"{base_key}_jobs"] = jobs
    history.append(snapshot)
    history = history[-RESOURCE_HISTORY_MAX_POINTS:]

    try:
        with open(RESOURCE_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception:
        pass

    return history


def append_food_history(satiety_by_base: dict) -> list:
    """Append the current per-base satiety_total to a rolling history, same
    unthrottled per-poll cadence as memory_history (a couple dozen bytes a
    point, cheap to write on every live dashboard poll). Powers both
    the satiety trend line and compute_food_depletion's "hours until
    empty" estimate — deliberately a separate, tiny, scalar-only history
    rather than reusing resource_history's full Food Box item snapshots,
    which append_resource_history's docstring already explains were tried
    and rejected for rate-measurement (player restocking looks identical
    to a pal eating in a raw item-count delta). Storing just the total here
    doesn't fix that ambiguity, but compute_food_depletion works around it
    by only trusting unbroken *declines*, and a restock is exactly what
    breaks the run.

    Takes a {base_key: satiety_total} dict so a base added later simply
    starts its own column. Older points keep only the columns that existed
    when they were written, and compute_food_depletion reads each base's
    column independently, so a new base reports "not enough data yet"
    until it has accumulated its own samples rather than corrupting
    anyone else's trend.
    """
    try:
        with open(FOOD_HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        history = []

    now = datetime.now()
    history.append({
        "ts": int(now.timestamp()),
        "t": now.strftime("%H:%M"),
        **satiety_by_base,
    })
    history = history[-FOOD_HISTORY_MAX_POINTS:]

    try:
        with open(FOOD_HISTORY_FILE, "w") as f:
            json.dump(history, f)
    except Exception:
        pass

    return history
