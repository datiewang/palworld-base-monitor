"""Everything computed *from* parsed save data: rates, ratios, roles, food.

Nothing here reads a file or knows where data is stored — every function
takes the parsed structures (and, where a trend is needed, a history list)
and returns plain numbers, so each one can be reasoned about on its own.
"""

from datetime import datetime

from .gamedata import BUILDINGS_META, ITEMS_META, pal_food_amount
from .history import PAL_JOBS_SCHEMA
from .tables import (
    CRAFTED_ITEM_OVERRIDES,
    FOOD_BOX_TYPES,
    ROLE_OUTPUT_BUCKET,
    SATIETY_VALUES,
)


def get_memory_info() -> dict:
    """Get system memory and PalServer process memory usage."""
    mem = {"system_used_gb": 0, "system_total_gb": 0, "system_pct": 0,
           "palserver_mb": 0}
    try:
        with open('/proc/meminfo') as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(':')] = int(parts[1])
        total = info.get('MemTotal', 0)
        avail = info.get('MemAvailable', info.get('MemFree', 0))
        used = total - avail
        mem['system_total_gb'] = round(total / 1048576, 1)
        mem['system_used_gb'] = round(used / 1048576, 1)
        mem['system_pct'] = round(used / total * 100, 1) if total else 0
    except Exception:
        pass

    # PalServer RSS: take the max across all matching processes. The launcher
    # wrapper "PalServer.sh" also matches 'PalServer' in /proc/*/comm but its
    # RSS is negligible (~6MB) next to the actual game binary
    # "PalServer-Linux-Shipping" (~1GB+) — picking the first match instead of
    # the max previously reported the wrapper's RSS as the server's memory use.
    try:
        import glob
        best_rss_kb = 0
        for stat_path in glob.glob('/proc/[0-9]*/comm'):
            try:
                with open(stat_path) as f:
                    if 'PalServer' in f.read():
                        pid = stat_path.split('/')[2]
                        with open(f'/proc/{pid}/status') as sf:
                            for line in sf:
                                if line.startswith('VmRSS:'):
                                    best_rss_kb = max(best_rss_kb, int(line.split()[1]))
                                    break
            except (FileNotFoundError, PermissionError):
                continue
        if best_rss_kb:
            mem['palserver_mb'] = round(best_rss_kb / 1024)
    except Exception:
        pass

    return mem


# Below this many samples, a work ratio is noise (one lucky/unlucky poll
# reading 0% or 100%) rather than a real trend — better to say "not enough
# data yet" than print a number that looks precise but isn't.
WORK_RATIO_MIN_SAMPLES = 5


def compute_work_ratio(history: list, pal_jobs_now: dict) -> dict:
    """For each base and each pal currently present, look back across every
    history snapshot the pal appears in (it won't appear in samples from
    before it was caught/moved to this base, nor in any recorded under an
    older PAL_JOBS_SCHEMA) and compute what fraction of those samples had
    it assigned to a job. Returns
    {base_key: {instance_id: {"ratio": 0.0-1.0, "samples": n}}} — a pal
    with fewer than WORK_RATIO_MIN_SAMPLES observations is omitted rather
    than given a misleadingly precise-looking percentage.
    """
    result = {k: {} for k in pal_jobs_now}
    for base_key, jobs_now in pal_jobs_now.items():
        jobs_key = f"{base_key}_jobs"
        counts = {iid: {"samples": 0, "worked": 0} for iid in jobs_now}
        for snap in history:
            if snap.get("jobs_v") != PAL_JOBS_SCHEMA:
                continue
            snap_jobs = snap.get(jobs_key, {})
            for iid in jobs_now:
                if iid not in snap_jobs:
                    continue
                counts[iid]["samples"] += 1
                if snap_jobs[iid]:
                    counts[iid]["worked"] += 1
        # Also fold in the live "now" reading as one more sample point.
        for iid, job in jobs_now.items():
            counts[iid]["samples"] += 1
            if job:
                counts[iid]["worked"] += 1

        for iid, c in counts.items():
            if c["samples"] >= WORK_RATIO_MIN_SAMPLES:
                result[base_key][iid] = {
                    "ratio": round(c["worked"] / c["samples"], 3),
                    "samples": c["samples"],
                }

    return result


# Same "not enough data yet" guard as WORK_RATIO_MIN_SAMPLES, applied to
# facility-type staffing samples instead of per-pal ones.
FACILITY_IDLE_MIN_SAMPLES = 5


def compute_facility_idle(history: list, pal_jobs_now: dict, facilities_now: dict) -> dict:
    """For each base, and each *workable* facility type currently placed
    there, the time-averaged number of pals working it per placed
    instance, capped at 1.0, across history (snapshots older than the
    current PAL_JOBS_SCHEMA are skipped — see that constant) — a long-run "how idle has this facility type been"
    number, as opposed to compute_work_ratio's per-pal version.

    "Workable" is derived empirically (any facility type ever seen as a
    pal's job, in this history or right now) rather than from a hardcoded
    suitability list, since that list only exists client-side (index.html's
    facility->suitability map) — a storage chest or other type that no pal
    has ever been assigned to naturally never enters the set, so it's never
    reported as "idle" (it was never workable in the first place).

    The denominator is the number of placed instances, not the number of
    work slots those instances offer: a 家畜牧场 seats four pals and an
    ancient furnace two, but a save only records assignments that are
    *filled*, so total capacity isn't in the data to divide by. That makes
    this "is anyone working this facility type at all", not "how full is
    it" — a ranch with one grazing pal out of four reads the same as a
    full one. It's the honest read of what the save actually says.

    Each sample's staffed count is divided by placed count *as of now*
    (not as of that sample) and capped at 1.0 — this reuses
    resource_history the same way compute_resource_gross_rate does, so a
    facility built after some history samples were recorded doesn't
    inflate its own idle ratio from samples that predate it (those
    samples simply read 0 staffed / current placed, correctly counting as
    "not staffed yet" rather than being excluded).

    Returns {base_key: {facility_type: {"avg_staff_ratio": 0-1, "placed": n,
    "samples": n}}}. A facility type with fewer than
    FACILITY_IDLE_MIN_SAMPLES observations is omitted.
    """
    result = {}
    for base_key, facilities in facilities_now.items():
        jobs_key = f"{base_key}_jobs"

        usable = [snap for snap in history if snap.get("jobs_v") == PAL_JOBS_SCHEMA]

        workable = set()
        for snap in usable:
            workable.update(v for v in snap.get(jobs_key, {}).values() if v)
        workable.update(v for v in pal_jobs_now.get(base_key, {}).values() if v)

        placed_types = {t: c for t, c in facilities.items() if c > 0 and t in workable}
        ratios = {t: [] for t in placed_types}
        for snap in usable:
            if jobs_key not in snap:
                continue
            counts = {}
            for v in snap.get(jobs_key, {}).values():
                if v:
                    counts[v] = counts.get(v, 0) + 1
            for t, placed in placed_types.items():
                ratios[t].append(min(1.0, counts.get(t, 0) / placed))

        now_counts = {}
        for v in pal_jobs_now.get(base_key, {}).values():
            if v:
                now_counts[v] = now_counts.get(v, 0) + 1
        for t, placed in placed_types.items():
            ratios[t].append(min(1.0, now_counts.get(t, 0) / placed))

        result[base_key] = {
            t: {"avg_staff_ratio": round(sum(rs) / len(rs), 3), "placed": placed_types[t], "samples": len(rs)}
            for t, rs in ratios.items() if len(rs) >= FACILITY_IDLE_MIN_SAMPLES
        }
    return result


def compute_resource_gross_rate(
    history: list, current_resources: dict, current_pending: dict, window_minutes: int = 60
) -> dict:
    """Estimate gross production (not net) per item: walk every consecutive
    pair of snapshots in the window and sum only the *increases* in
    (stored + pending) combined — a step where a mining pit's backlog goes
    up counts, a step where a chest's stock goes down doesn't, and a step
    where pending drops because a carrier moved it into storage nets to
    ~zero (stored and pending move opposite ways together) instead of
    being double-counted as two separate gains.

    This recovers production that a simple endpoint-to-endpoint net delta
    can't see when consumption is roughly keeping pace with it — but
    resolution is capped by how often snapshots land (see
    RESOURCE_HISTORY_MIN_INTERVAL_SECONDS): a gain and a loss that both
    happen inside the same gap between two snapshots still cancel out and
    go unseen, same as with the plain net-delta trend.

    Snapshots recorded before pending-tracking existed have no
    f"{base}_pending" key at all — treating that as "pending was 0" would
    make the *entire* backlog a mining pit had already accumulated look
    like it was produced the instant tracking started (confirmed: this
    inflated an early build's CopperOre rate by ~3500 in one step). Those
    snapshots are excluded from the series entirely instead, so the window
    only spans time where pending was actually being measured.
    """
    result = {k: {"items": [], "minutes": 0} for k in current_resources}
    if not history:
        return result

    now_ts = int(datetime.now().timestamp())
    cutoff_ts = now_ts - window_minutes * 60

    for base_key, cur_stored in current_resources.items():
        cur_pending = current_pending.get(base_key, {})
        pending_key = f"{base_key}_pending"
        in_window = [
            s for s in history
            if s.get("ts", 0) >= cutoff_ts and pending_key in s
        ]
        if not in_window:
            continue

        series = []
        for snap in in_window:
            stored = snap.get(base_key, {})
            pending = snap.get(pending_key, {})
            ids = set(stored) | set(pending)
            series.append({i: stored.get(i, 0) + pending.get(i, 0) for i in ids})
        now_ids = set(cur_stored) | set(cur_pending)
        series.append({i: cur_stored.get(i, 0) + cur_pending.get(i, 0) for i in now_ids})

        oldest_ts = in_window[0].get("ts", now_ts)
        actual_minutes = round((now_ts - oldest_ts) / 60)
        if actual_minutes <= 0 or len(series) < 2:
            continue

        gains = {}
        for prev, cur in zip(series, series[1:]):
            for item_id in set(prev) | set(cur):
                d = cur.get(item_id, 0) - prev.get(item_id, 0)
                if d > 0:
                    gains[item_id] = gains.get(item_id, 0) + d

        items = [{"item": k, "gross_gain": v} for k, v in gains.items()]
        items.sort(key=lambda x: -x["gross_gain"])
        result[base_key] = {"items": items, "minutes": actual_minutes}

    return result


def summarize_food_storage(food_items: dict) -> dict:
    """Snapshot view of one base's Pal Food Box contents: total satiety
    available right now (SATIETY_VALUES-mapped items only) plus the raw
    item counts split into "known" (counted toward the total) and
    "unmapped" (real food, just no verified satiety value — see
    SATIETY_VALUES) so the unmapped portion is visible instead of quietly
    missing from the total.
    """
    known = {}
    unmapped = {}
    satiety_total = 0
    for item_id, count in food_items.items():
        if item_id in SATIETY_VALUES:
            known[item_id] = count
            satiety_total += count * SATIETY_VALUES[item_id]
        else:
            unmapped[item_id] = count
    return {
        "satiety_total": satiety_total,
        "unmapped_items": unmapped,
    }


# ─── What a base is *for* ───
# Which kind of work a placed facility represents. Read off the game's own
# building datatable (the type_a / type_b fields already vendored in
# buildings_meta.json) rather than a hand-kept list, so a facility added by
# a game update classifies itself instead of waiting for this file to be
# edited.
#
# Anything not matched here is deliberately neutral and classifies nothing.
# Beds, chests, spas, the Pal Box, the medicine cabinet, lamps, foundations
# and decor stand in every base whatever its purpose, and counting them
# would bury the signal: they are routinely half of a base's facility
# count, and would make every base look like the same base.
#
# Feed boxes are excluded for the same reason even though they are Food:
# a base feeds its own pals regardless of what it produces, so a food box
# says nothing about the base's job.
#
# Pal_Modify (the Pal condenser, the dismantling conveyor) is folded in
# with Pal_Breed rather than given a tag of its own: condensing, hatching,
# breeding and dismantling are all the same activity — turning pals into
# better pals — and split apart each would be a one-facility tag that
# classifies nothing.
def _facility_role(facility_id: str) -> "str | None":
    meta = BUILDINGS_META.get(facility_id)
    if not meta or facility_id in FOOD_BOX_TYPES:
        return None
    type_a, type_b = meta.get("type_a"), meta.get("type_b")
    if type_b == "Prod_Resource":
        return "gathering"
    if type_b in ("Pal_Breed", "Pal_Modify"):
        return "breeding"
    if type_a == "Food":
        # Kitchens are Prod_Craft; farm plots and the skill-fruit orchard
        # are not, and are the only other Food-typed buildings left once
        # the feed boxes are gone.
        return "cooking" if type_b == "Prod_Craft" else "farming"
    if type_b in ("Prod_Craft", "Prod_Furnace", "Prod_Medicine", "Pal_Capture"):
        return "crafting"
    if type_b == "Infra_GeneratePower":
        return "power"
    return None


def _item_role(item_id: str) -> str:
    if item_id in CRAFTED_ITEM_OVERRIDES:
        return CRAFTED_ITEM_OVERRIDES[item_id]
    meta = ITEMS_META.get(item_id)
    if not meta:
        return "other"
    type_a, type_b = meta.get("type_a"), meta.get("type_b")
    if type_b in ("MaterialOre", "MaterialStone", "MaterialWood"):
        return "gathering"
    if type_b == "MaterialIngot":
        return "crafting"
    if type_a == "Food":
        return "food"
    return "other"


# A tag has to account for at least this share of a base's classifying
# facilities to make it into the headline label. At 25% a base reads as
# one or two things it actually is, rather than a list of everything it
# happens to contain — a handful of farm plots at a smelting base are real
# but they are not what that base is for.
ROLE_LABEL_MIN_SHARE = 0.25


ROLE_LABEL_MAX_TAGS = 2


def classify_base_role(facilities: dict, gross_rate: dict) -> dict:
    """Classify one base from what is built in it and what it has actually
    produced, for the dashboard's per-base heading and — more usefully —
    to scope its production advice: a breeding base has no business being
    told to build a quarry, and a smelting base short on ore wants to know
    which base is already mining it, not to sink another pit next to the
    furnace.

    Facility counts are per placed instance, not per distinct type: seven
    farm plots really is seven times as much farming as one, and the
    alternative (counting types) would rate a base with one plot and one
    furnace as evenly split between the two.

    "output" is each bucket's gross gain over compute_resource_gross_rate's
    window, and "stalled" names the tags this base is built for that
    produced nothing at all in it — an empty window (not enough history
    yet) reports no stall rather than a false one.
    """
    counts = {}
    for facility_id, n in facilities.items():
        role = _facility_role(facility_id)
        if role:
            counts[role] = counts.get(role, 0) + n
    total = sum(counts.values())

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    tags = [t for t, n in ordered if total and n / total >= ROLE_LABEL_MIN_SHARE]
    tags = tags[:ROLE_LABEL_MAX_TAGS]

    output = {}
    for entry in (gross_rate or {}).get("items", []):
        bucket = _item_role(entry.get("item", ""))
        output[bucket] = output.get(bucket, 0) + entry.get("gross_gain", 0)
    minutes = (gross_rate or {}).get("minutes", 0)

    stalled = []
    if minutes:
        for tag in counts:
            bucket = ROLE_OUTPUT_BUCKET.get(tag)
            if bucket and not output.get(bucket):
                stalled.append(tag)

    return {
        "counts": counts,
        "tags": tags,
        "primary": ordered[0][0] if ordered else None,
        "output": output,
        "output_minutes": minutes,
        "stalled": sorted(stalled),
    }


def compute_food_consumption(pals: list) -> int:
    """Sum each present pal's food_amount — the base's total food
    consumption expressed in the game's own per-pal consumption units.
    Deliberately not converted to a per-hour rate: measuring real
    consumption from Food Box history was tried and rejected (players
    manually moving food in/out pollutes the signal — a pal eating and a
    player restocking look identical as a raw item-count delta), and no
    verified real-time hunger-tick interval exists to convert food_amount
    into one either. This total is comparable base-to-base and against
    itself over time, just not literally "units per hour".
    """
    return sum(pal_food_amount(p.get("type", "")) or 0 for p in pals)


# Below this many minutes of unbroken decline, an extrapolated "hours until
# empty" is one poll cycle's noise rather than a real trend.
FOOD_DEPLETION_MIN_MINUTES = 20


def compute_food_depletion(history: list, base_key: str, current_satiety: "int | None") -> dict:
    """Estimate real "hours until empty" for one base's Food Box, measured
    from the satiety_total history itself — not from compute_food_consumption's
    food_amount sum, which (see that function's docstring) has no verified
    real-time unit, so satiety_total / consumption would print a
    precise-looking number built from two incompatible units.

    Walks backward from the latest sample and finds the longest unbroken
    run where satiety has been non-increasing. Any increase means a player
    put food in the box, which invalidates comparing further back as "the
    same depletion trend" — so the run stops there. The decline across the
    surviving run, divided by its real elapsed time, is an actually
    measured burn rate, extrapolated forward to when current_satiety would
    hit zero.

    Returns {"hours_remaining": float|None}.
    None means satiety isn't currently declining (production/consumption
    are balanced, or it was just restocked) or the run is shorter than
    FOOD_DEPLETION_MIN_MINUTES — too little to trust.
    """
    points = [(h.get("ts", 0), h.get(base_key)) for h in history if h.get(base_key) is not None]
    if current_satiety is not None:
        points.append((int(datetime.now().timestamp()), current_satiety))
    points.sort(key=lambda p: p[0])

    if len(points) < 2:
        return {"hours_remaining": None}

    start_idx = len(points) - 1
    for i in range(len(points) - 1, 0, -1):
        if points[i][1] > points[i - 1][1]:
            break
        start_idx = i - 1

    start_ts, start_val = points[start_idx]
    end_ts, end_val = points[-1]
    elapsed_minutes = (end_ts - start_ts) / 60
    decline = start_val - end_val

    if elapsed_minutes < FOOD_DEPLETION_MIN_MINUTES or decline <= 0:
        return {"hours_remaining": None}

    rate_per_hour = decline / elapsed_minutes * 60
    hours_remaining = round(end_val / rate_per_hour, 1) if rate_per_hour > 0 else None
    return {"hours_remaining": hours_remaining}
