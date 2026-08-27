"""Reading Level.sav: decompression, the GVAS decoder patches, and every
parser that turns raw save structures into plain dicts.

The two _patch_* functions are the load-bearing part: palworld_save_tools
ships with decoders that either skip or mis-type several of the structures
this dashboard needs, and they are monkeypatched before the parse rather
than forked, so a library upgrade brings its own fixes along.
"""

import os
import struct
import sys
from datetime import datetime

from .config import BASE_ANCHORS, GUILD_ID, PLAYERS_DIR
from .tables import (
    CRAFTING_STATION_TYPES,
    FARM_STATE_WAITING,
    FOOD_BOX_TYPES,
    FUNCTIONAL_MODULE_TYPES,
    GATHERING_NODE_TYPES,
    IMPORTANT_EMPTY_MODULE_TYPES,
    LOOSE_ITEM_TYPES,
    MAP_OBJECT_ID_ALIASES,
    POWER_STORAGE_TYPES,
    WORKER_SICK_MAP,
)


def get_worker_sick_name(sick_type: str) -> str:
    """Return a user-friendly display name for a base camp worker sickness type."""
    return WORKER_SICK_MAP.get(sick_type, sick_type)


def decompress_save(sav_path: str) -> bytes:
    """Decompress a Palworld .sav file (supports both PlZ/zlib and PlM/Oodle)."""
    with open(sav_path, 'rb') as f:
        data = f.read()

    uncompressed_len = struct.unpack('<I', data[0:4])[0]
    magic = data[8:12]

    if magic[:3] == b'PlZ':
        # Standard zlib compression
        import zlib
        return zlib.decompress(data[12:])
    elif magic[:3] == b'PlM':
        # Oodle compression
        import ooz
        return ooz.decompress(data[12:], uncompressed_len)
    else:
        raise ValueError(f"Unknown save compression: {magic!r}")


# .NET DateTime ticks (100ns units since 0001-01-01) -> Unix epoch offset,
# the standard conversion constant for this well-known epoch difference.
_DOTNET_TICKS_AT_UNIX_EPOCH = 621355968000000000


def _dotnet_ticks_to_iso(ticks: int) -> "str | None":
    if not ticks:
        return None
    unix_seconds = (ticks - _DOTNET_TICKS_AT_UNIX_EPOCH) / 10_000_000
    try:
        return datetime.fromtimestamp(unix_seconds).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def read_player_file(player_uid_hex: str) -> dict:
    """Each player has their own small save file under Players/<UID>.sav
    (UID formatted the same way PlayerUId prints in-engine: hyphen-free
    uppercase hex — confirmed against a real Players/ directory listing)
    holding data that isn't in the shared world file:
    LastOnlineDateTime (a .NET-tick timestamp, present even while the
    player is offline — this is what makes "last seen" possible for
    players who aren't currently connected) and OtomoCharacterContainerId
    (the pal party/follow slot container, distinct from PalStorageContainerId
    which is that player's Pal Box). Missing/corrupt files degrade to an
    empty result rather than failing the whole status update.
    """
    path = os.path.join(PLAYERS_DIR, f"{player_uid_hex}.sav")
    try:
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS
        import io
        data = decompress_save(path)
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            gvas_file = GvasFile.read(data, PALWORLD_TYPE_HINTS, {}, allow_nan=True)
        finally:
            sys.stderr = old_stderr
        sd = gvas_file.properties.get("SaveData", {}).get("value", {})
        ticks = sd.get("LastOnlineDateTime", {}).get("value")
        otomo = sd.get("OtomoCharacterContainerId", {}).get("value", {}).get("ID", {}).get("value")
        return {
            "last_online": _dotnet_ticks_to_iso(ticks),
            "otomo_container_id": str(otomo) if otomo else None,
        }
    except Exception:
        return {"last_online": None, "otomo_container_id": None}


# ============================================================
# Base facility / item-storage parsing
#
# palworld-save-tools' own custom byte decoders for WorkSaveData and
# item_container_slots.py are broken against current save versions
# (confirmed: they either raise "EOF not reached" or silently misalign into
# garbage values a few fields in). Rather than debug that decoder's exact
# byte layout, these helpers deliberately avoid it: they leave those
# fields as opaque raw ArrayProperty<Byte> blobs (by not registering a
# custom decoder for them) and hand-parse only the narrow, fixed-position
# slice of bytes actually needed. This was validated against real save
# data (readable item names with sane stack counts; chest->container GUID
# links matched 24/24; a decoded world position landed inside the correct
# base's radius) before being wired in here.
# ============================================================

def _parse_item_slot_bytes(raw_bytes) -> "tuple | None":
    """Hand-parse an ItemContainerSaveData slot's raw bytes: leading
    int32 slot_index, int32 stack count, int32 name length, then the
    item static ID string. Anything after that (per-item durability /
    dynamic-item data) is ignored. Returns None if the bytes don't look
    like a valid slot (too short / bogus length) rather than raising.
    """
    b = bytes(raw_bytes)
    if len(b) < 12:
        return None
    slot_index, count, strlen = struct.unpack('<iii', b[0:12])
    if strlen <= 0 or 12 + strlen > len(b):
        return None
    item_id = b[12:12 + strlen].rstrip(b'\x00').decode('utf-8', errors='replace')
    if not item_id:
        return None
    return slot_index, count, item_id


def _find_position_xy(raw_bytes) -> "tuple | None":
    """Scan a MapObjectSaveData object's Model.RawData blob for a plausible
    (x, y) world-position double pair. The blob's exact layout differs by
    facility type (variable-length sub-structs precede it), so instead of
    trusting a fixed offset this scans every byte offset for two adjacent
    float64s that both fall in Palworld's world-coordinate range — which
    only real position data will consistently satisfy at the same offset.
    """
    b = bytes(raw_bytes)
    for off in range(0, len(b) - 15):
        try:
            x, y = struct.unpack_from('<dd', b, off)
        except struct.error:
            break
        if 500.0 < abs(x) < 500000.0 and 500.0 < abs(y) < 500000.0:
            return x, y
    return None


def _parse_guid_bytes(raw_bytes) -> "str | None":
    """Parse a 16-byte GUID using palworld-save-tools' own reader/UUID
    class, so the result is byte-for-byte comparable with GUIDs the
    library parsed elsewhere (e.g. ItemContainerSaveData keys)."""
    if len(raw_bytes) < 16:
        return None
    from palworld_save_tools.archive import FArchiveReader
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS
    try:
        reader = FArchiveReader(bytes(raw_bytes[:16]), PALWORLD_TYPE_HINTS, {}, allow_nan=True)
        return str(reader.guid())
    except Exception:
        return None


def _get_base_meta(wsd: dict) -> dict:
    """Map every base camp the player's guild owns to a stable 'baseN' key,
    its world-space center + radius, and its worker-roster container id.

    BaseCampSaveData lists every camp in the world, other guilds' included.
    Which guild is "ours" is decided in three steps, first match winning:
    the configured [bases] guild_id; the guild owning a camp whose worker
    container is a configured anchor; otherwise the guild owning the most
    camps, since the alternative is reporting no bases at all.

    Naming: each configured anchor claims base1, base2, ... in the order it
    is listed, which is what keeps a camp's accumulated history attached to
    the same camp. Every other camp of the guild follows, ordered by its own
    camp GUID (persistent), so a newly built base doesn't renumber the
    others and invalidate their history.

    A rebuilt base gets a brand-new worker container GUID, so an anchor has
    to be re-read from the save whenever that base is dismantled and rebuilt
    — otherwise every field for it silently comes out empty. With no anchors
    configured at all, numbering is purely by camp GUID: stable as long as
    no base is rebuilt, which is the sensible default for a fresh install.
    """
    camps = []
    for camp in wsd.get("BaseCampSaveData", {}).get("value", []):
        cv = camp.get("value", {})
        raw = cv.get("RawData", {}).get("value", {})
        translation = raw.get("transform", {}).get("translation", {})
        if "x" not in translation or "y" not in translation:
            continue
        wd_raw = cv.get("WorkerDirector", {}).get("value", {}).get("RawData", {}).get("value", {})
        camps.append({
            "camp_id": str(camp.get("key")),
            "group": str(raw.get("group_id_belong_to")),
            "container": wd_raw.get("container_id"),
            "center": (translation["x"], translation["y"]),
            "radius": raw.get("area_range", 3500.0),
        })
    if not camps:
        return {}

    anchors = {container: f"base{i + 1}" for i, container in enumerate(BASE_ANCHORS)}
    guild = GUILD_ID or next((c["group"] for c in camps if c["container"] in anchors), None)
    if not guild:
        counts = {}
        for c in camps:
            counts[c["group"]] = counts.get(c["group"], 0) + 1
        guild = max(counts, key=lambda g: counts[g])

    ours = sorted((c for c in camps if c["group"] == guild), key=lambda c: c["camp_id"])
    meta = {}
    next_index = len(anchors) + 1
    for camp in ours:
        base_key = anchors.get(camp["container"])
        if base_key is None:
            base_key = f"base{next_index}"
            next_index += 1
        meta[base_key] = {
            "center": camp["center"],
            "radius": camp["radius"],
            "container": camp["container"],
        }
    return dict(sorted(meta.items(), key=lambda kv: int(kv[0][4:])))


def _parse_power_charge(model_raw_bytes) -> "float | None":
    """Extract the current-charge float from a power facility's
    ConcreteModel.RawData. Bails out to None (excluded from totals, not
    counted as 0) if the blob is too short to hold it."""
    if len(model_raw_bytes) < 40:
        return None
    return struct.unpack_from("<f", bytes(model_raw_bytes), 36)[0]


def _parse_base_facilities_and_resources(wsd: dict, base_meta: dict) -> tuple:
    """Walk ItemContainerSaveData (for stack contents) and MapObjectSaveData
    (for placed facilities + their world position) to build, per base: a
    facility-type count map, an item-id -> total-count map for real storage
    (chests, medicine boxes, coolers — anything not in GATHERING_NODE_TYPES,
    CRAFTING_STATION_TYPES, FOOD_BOX_TYPES or LOOSE_ITEM_TYPES), a separate item-id ->
    total-count map for gathering-node buffers ("produced but not yet
    collected"), a separate item-id -> total-count map for Pal Food Box
    contents (what pals can actually eat right now), a work-slot-GUID ->
    (base, facility type) map used by _parse_work_assignments to attribute
    a pal's current job to a base and facility, and a
    model-instance-GUID -> (base, facility type) map used by
    _parse_farm_growth_state the same way for a Progress work item's
    owner_model_id.
    """
    facilities = {k: {} for k in base_meta}
    resources = {k: {} for k in base_meta}
    pending_resources = {k: {} for k in base_meta}
    food_storage = {k: {} for k in base_meta}
    work_slot_owner = {}
    model_guid_owner = {}
    power_storage = {k: {"current": 0.0, "capacity": 0} for k in base_meta}
    if not base_meta:
        return facilities, resources, pending_resources, food_storage, work_slot_owner, power_storage, model_guid_owner

    # 1) container_id -> [(item_id, count), ...]
    # ItemContainerSaveData is a MapProperty, which this library represents
    # as a plain list of {key, value} pairs directly under .value (unlike
    # MapObjectSaveData below, an ArrayProperty wrapped in {"values": [...]}).
    container_items = {}
    ics_list = wsd.get("ItemContainerSaveData", {}).get("value", [])
    for c in ics_list:
        try:
            cid = str(c["key"]["ID"]["value"])
        except Exception:
            continue
        slots_wrap = c.get("value", {}).get("Slots", {}).get("value", {})
        slot_vals = slots_wrap.get("values", []) if isinstance(slots_wrap, dict) else []
        items = []
        for sd in slot_vals:
            rd = sd.get("RawData", {}).get("value", {}).get("values", [])
            parsed = _parse_item_slot_bytes(rd)
            if parsed and parsed[1] > 0:
                items.append((parsed[2], parsed[1]))
        if items:
            container_items[cid] = items

    # 2) Walk placed map objects, bucket by which base's radius contains them
    mo = wsd.get("MapObjectSaveData", {}).get("value", {})
    mo_list = mo.get("values", []) if isinstance(mo, dict) else []
    for o in mo_list:
        mid = o.get("MapObjectId", {}).get("value", "")
        if not mid:
            continue
        mid = MAP_OBJECT_ID_ALIASES.get(mid, mid)
        if mid in LOOSE_ITEM_TYPES:
            continue
        model_rd = o.get("Model", {}).get("value", {}).get("RawData", {}).get("value", {}).get("values", [])
        pos = _find_position_xy(model_rd)
        if pos is None:
            continue
        x, y = pos
        owner = None
        for base_key, meta in base_meta.items():
            cx, cy = meta["center"]
            if (x - cx) ** 2 + (y - cy) ** 2 <= meta["radius"] ** 2:
                owner = base_key
                break
        if owner is None:
            continue

        # Model.RawData's own leading GUID (same field _find_position_xy
        # scans past) is what a Progress work item's owner_model_id points
        # back to — see _patch_work_decoders' docstring. Recording it here
        # is what lets _parse_farm_growth_state attribute a farm plot's
        # growth state to a specific base + crop type.
        model_guid = _parse_guid_bytes(model_rd)
        if model_guid:
            model_guid_owner[model_guid] = (owner, mid)

        concrete = o.get("ConcreteModel", {}).get("value", {})
        module_keys = {
            m.get("key", "").split("::")[-1]
            for m in concrete.get("ModuleMap", {}).get("value", [])
        }

        if mid in POWER_STORAGE_TYPES:
            concrete_rd = concrete.get("RawData", {}).get("value", {}).get("values", [])
            charge = _parse_power_charge(concrete_rd)
            if charge is not None:
                power_storage[owner]["current"] += max(0.0, charge)
                power_storage[owner]["capacity"] += POWER_STORAGE_TYPES[mid]

        if module_keys & FUNCTIONAL_MODULE_TYPES or mid in IMPORTANT_EMPTY_MODULE_TYPES:
            facilities[owner][mid] = facilities[owner].get(mid, 0) + 1

        # Each Workee module's RawData leads with a 16-byte GUID that's a
        # work-slot ID (confirmed against WorkSaveData's WorkCollection —
        # see _parse_work_assignments), not a pal instance ID. Recording
        # slot -> (base, facility type) here is what lets that function
        # turn "this pal is assigned to slot X" into "this pal is working
        # the StonePit at base 2".
        for m in concrete.get("ModuleMap", {}).get("value", []):
            if m.get("key") != "EPalMapObjectConcreteModelModuleType::Workee":
                continue
            rd_slot = m.get("value", {}).get("RawData", {}).get("value", {}).get("values", [])
            slot_guid = _parse_guid_bytes(rd_slot)
            if slot_guid:
                work_slot_owner[slot_guid] = (owner, mid)

        if mid in CRAFTING_STATION_TYPES:
            continue
        if mid in FOOD_BOX_TYPES:
            target = food_storage
        elif mid in GATHERING_NODE_TYPES:
            target = pending_resources
        else:
            target = resources

        if "ItemContainer" not in module_keys:
            continue
        for m in concrete.get("ModuleMap", {}).get("value", []):
            if m.get("key") != "EPalMapObjectConcreteModelModuleType::ItemContainer":
                continue
            rd2 = m.get("value", {}).get("RawData", {}).get("value", {}).get("values", [])
            cid = _parse_guid_bytes(rd2)
            for item_id, count in container_items.get(cid, []):
                target[owner][item_id] = target[owner].get(item_id, 0) + count

    return facilities, resources, pending_resources, food_storage, work_slot_owner, power_storage, model_guid_owner


def _patch_work_decoders():
    """palworld-save-tools' own WorkSaveData decoder is broken against this
    save version: EPalWorkableType::Progress work items (mining pits, egg
    incubators, farm plots, ...) parse a fixed-layout common prefix
    correctly — id, workable_bounds, base_camp_id_belong_to,
    owner_map_object_model_id, owner_map_object_concrete_model_id,
    current_state, in that order, matching the pinned library's own
    (unused, since the full decode raises) field layout — before going to
    obvious garbage partway through the type-specific suffix
    (work_exp/current_work_amount/auto_work_self_amount_by_sec came out as
    nonsense numbers when tested) — almost certainly a field the pinned
    library predates. decode_bytes (that work-item body) is monkeypatched
    to decode just that safe prefix and stop, instead of debugging the
    suffix's current layout.

    owner_map_object_model_id + current_state is what
    _parse_farm_growth_state uses: joined against
    _parse_base_facilities_and_resources's model_guid_owner map (same
    owner_map_object_model_id space — validated by testing this decode
    against a live save: every one of 174 work elements decoded without
    error, and every EPalWorkableType::Progress item's owner_model_id
    matched a real MapObjectSaveData entry, correctly identifying its
    facility type). current_state was observed taking only two values in
    that same test — 1 on the large majority of Progress items (mining
    pits, furnaces mid-smelt, growing crops, ...) and 3 on a minority
    that, for farm plots specifically, lines up with "already matured,
    sitting there uncollected" (as opposed to "still growing"): no
    field-name documentation exists for this byte (which is exactly why
    the pinned library's own decode of the fields after it is unreliable),
    so this mapping (1 = growing, 3 = waiting to be harvested) is inferred
    from that observed pattern, not a confirmed enum.

    The actual thing this module needs — WorkAssignMap, i.e. which pal
    instance is assigned to which work slot — lives in a separate, much
    simpler byte blob (decode_work_assign_bytes) that turned out to parse
    correctly as-is, just with 4 trailing bytes the pinned library doesn't
    account for (a field it predates, same as above) and raises on instead
    of ignoring. Reimplemented here tolerating that tail. Validated against
    real save data: every non-empty assignment (36/36) resolved to a real
    pal InstanceId from CharacterSaveParameterMap, and every assignment's
    own "id" matched a real Workee-module slot GUID from MapObjectSaveData.
    """
    import palworld_save_tools.rawdata.work as work_mod

    def _decode_work_body_prefix(parent_reader, b_bytes, work_type):
        reader = parent_reader.internal_copy(bytes(b_bytes), debug=False)
        try:
            reader.guid()  # id
            reader.vector_dict()  # workable_bounds.location
            reader.quat_dict()  # workable_bounds.rotation
            reader.vector_dict()  # box_sphere_bounds.origin
            reader.vector_dict()  # box_sphere_bounds.box_extent
            reader.double()  # box_sphere_bounds.sphere_radius
            reader.guid()  # base_camp_id_belong_to
            owner_model_id = reader.guid()
            reader.guid()  # owner_map_object_concrete_model_id
            current_state = reader.byte()
        except Exception:
            return {}
        return {"owner_model_id": str(owner_model_id), "current_state": current_state, "work_type": work_type}

    def _decode_assign(parent_reader, b_bytes):
        reader = parent_reader.internal_copy(bytes(b_bytes), debug=False)
        return {
            "id": reader.guid(),
            "location_index": reader.i32(),
            "assign_type": reader.byte(),
            "assigned_individual_id": {
                "player_uid": reader.guid(),
                "instance_id": reader.guid(),
            },
            "state": reader.byte(),
            "fixed": reader.u32() > 0,
        }

    work_mod.decode_bytes = _decode_work_body_prefix
    work_mod.decode_work_assign_bytes = _decode_assign


def _patch_map_value_scalars():
    """Teach FArchiveReader.prop_value the scalar types it doesn't know.

    prop_value() is the reader used for MapProperty keys and values, and
    upstream (palworld-save-tools 0.24.0) only handles five types there:
    Struct/Enum/Name/Int/Bool. Anything else raises, which aborts the whole
    parse — even though the top-level property() dispatch in the same file
    reads all of these types perfectly well.

    That gap can take the dashboard down outright: once a save grows a
    .worldSaveData.LevelObjectRecoverPartySaveData.Value.PlayerLastUsedTimes
    map, whose values are Int64Property, every parse from then on fails
    with "Unknown property value type: Int64Property", pinning status.json
    to the last good snapshot via the stale fallback. The map is only
    non-empty once a player has left a recover-party behind, which is why
    it first failed intermittently and then permanently.

    Int64Property is the one actually needed; the other scalars are added
    at the same time because each is a one-line delegation to the same
    reader method property() already uses for that type, and any of them
    appearing as a future map value would cause the identical outage.
    Deliberately NOT added: ByteProperty (its wire format is prefixed by an
    enum-type string, so it isn't a bare scalar) and SetProperty (see the
    note in _analyze_data_locked). Reader-side only — this dashboard never
    re-serializes a save, so FArchiveWriter needs no matching patch.
    """
    from palworld_save_tools.archive import FArchiveReader

    if getattr(FArchiveReader.prop_value, "_scalar_patched", False):
        return

    original = FArchiveReader.prop_value
    extra = {
        "Int64Property": lambda r: r.i64(),
        "UInt32Property": lambda r: r.u32(),
        "UInt16Property": lambda r: r.u16(),
        "FloatProperty": lambda r: r.float(),
        "DoubleProperty": lambda r: r.double(),
        "StrProperty": lambda r: r.fstring(),
    }

    def prop_value(self, type_name, struct_type_name, path):
        read = extra.get(type_name)
        if read is not None:
            return read(self)
        return original(self, type_name, struct_type_name, path)

    prop_value._scalar_patched = True
    FArchiveReader.prop_value = prop_value


def _parse_work_assignments(wsd: dict) -> list:
    """Every filled work assignment in the save, as
    [(work-slot GUID, assigned pal InstanceId, fixed), ...] — "fixed" is
    the WorkAssignMap entry's own flag for whether the player locked this
    pal to the slot (vs. it being free to wander off to other work).
    Skips empty/unassigned slots (all-zero instance ID). Combine with the
    work_slot_owner map from _parse_base_facilities_and_resources to turn
    this into "which base and facility is this pal working at, and is it
    locked there".

    A LIST, not a slot-GUID-keyed dict, because a work slot GUID is not
    unique per worker: one workable object has several assignment
    *locations* sharing its single GUID, distinguished only by the entry's
    own location_index. A ranch is the extreme case — each 家畜牧场 holds
    four grazing pals at location_index 0-3, all under one GUID — but
    multi-worker furnaces, workbenches, breeding farms and farm plots all
    do the same. Keying by GUID silently keeps only the last entry for each
    object and drops the rest — in the world where this was found, 27 of 45
    filled assignments survived — and every dropped one reads as "this pal
    has no job", which then propagates into the per-pal work ratio and the
    long-idle panel: ranch pals showed a flat 0% while their ranch's own
    output history (honey, +70/hour) was
    proof they were working. (guid, location_index) is unique, verified
    across every assignment in that save.
    """
    assignments = []
    zero_guid = "00000000-0000-0000-0000-000000000000"
    work_elements = wsd.get("WorkSaveData", {}).get("value", {})
    values = work_elements.get("values", []) if isinstance(work_elements, dict) else []
    for we in values:
        for wa in we.get("WorkAssignMap", {}).get("value", []):
            d = wa.get("value", {}).get("RawData", {}).get("value", {})
            slot_guid = str(d.get("id", ""))
            iid = str(d.get("assigned_individual_id", {}).get("instance_id", ""))
            if slot_guid and iid and iid != zero_guid:
                assignments.append((slot_guid, iid, bool(d.get("fixed", False))))
    return assignments


def _parse_farm_growth_state(wsd: dict, model_guid_owner: dict) -> dict:
    """For each base, and each farm plot type placed there, how many
    instances are still growing vs sitting matured and uncollected —
    read directly from each plot's own Progress work item (current_state,
    decoded by _patch_work_decoders' replacement decode_bytes) rather than
    inferred from output history the way compute_facility_idle's
    avg_staff_ratio or the frontend's old gross_rate-based farm check
    were. A farm plot doesn't need continuous staffing the way a mining
    pit does, so this is the only reliable "is this plot idle" signal for
    them — validated against a live save where every FarmBlockV2 Progress
    item's owner_model_id resolved to a real placed farm instance via
    model_guid_owner.

    Returns {base_key: {facility_type: {"growing": n, "waiting": n,
    "other": n}}}. "other" catches any current_state byte besides 1/3 —
    kept visible rather than silently folded into one bucket, since the
    1/3 mapping is inferred (see FARM_STATE_WAITING) and a save that
    exercises some other value is exactly the signal that inference needs
    revisiting.
    """
    result = {}
    work_top = wsd.get("WorkSaveData", {}).get("value", {})
    work_values = work_top.get("values", []) if isinstance(work_top, dict) else []
    for we in work_values:
        d = we.get("RawData", {}).get("value", {})
        if not d or d.get("work_type") != "EPalWorkableType::Progress":
            continue
        owner = model_guid_owner.get(d.get("owner_model_id"))
        if not owner:
            continue
        base_key, mid = owner
        if not mid.startswith("FarmBlockV2"):
            continue
        bucket = result.setdefault(base_key, {}).setdefault(mid, {"growing": 0, "waiting": 0, "other": 0})
        state = d.get("current_state")
        if state == 1:
            bucket["growing"] += 1
        elif state == FARM_STATE_WAITING:
            bucket["waiting"] += 1
        else:
            bucket["other"] += 1
    return result


def parse_save_data(gvas_data: bytes) -> dict:
    """Parse GVAS binary data and extract pal/camp information."""
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

    # Only decode the properties we need for performance
    # Note: deliberately NOT registering custom decoders for MapObjectSaveData
    # or ItemContainerSaveData.Value.Slots.Slots.RawData here — both are
    # broken against current save versions in palworld-save-tools (see the
    # _parse_base_facilities_and_resources block comment above). Leaving them
    # unregistered means they come through as plain, unparsed byte arrays,
    # which _parse_base_facilities_and_resources then hand-parses itself.
    # WorkSaveData gets a *replacement* decoder (not skipped) — see
    # _patch_work_decoders()'s docstring for what's broken in the original
    # and why the replacement is trustworthy for the one thing it's used for.
    _patch_work_decoders()
    _patch_map_value_scalars()
    custom_props = {
        ".worldSaveData.CharacterSaveParameterMap.Value.RawData":
            PALWORLD_CUSTOM_PROPERTIES[".worldSaveData.CharacterSaveParameterMap.Value.RawData"],
        ".worldSaveData.BaseCampSaveData.Value.RawData":
            PALWORLD_CUSTOM_PROPERTIES[".worldSaveData.BaseCampSaveData.Value.RawData"],
        ".worldSaveData.BaseCampSaveData.Value.WorkerDirector.RawData":
            PALWORLD_CUSTOM_PROPERTIES[".worldSaveData.BaseCampSaveData.Value.WorkerDirector.RawData"],
        ".worldSaveData.WorkSaveData":
            PALWORLD_CUSTOM_PROPERTIES[".worldSaveData.WorkSaveData"],
    }

    # Suppress noisy palworld-save-tools warnings during parse
    import io
    old_stderr = sys.stderr
    old_stdout = sys.stdout
    sys.stderr = io.StringIO()
    sys.stdout = io.StringIO()
    try:
        gvas_file = GvasFile.read(gvas_data, PALWORLD_TYPE_HINTS, custom_props, allow_nan=True)
    finally:
        sys.stderr = old_stderr
        sys.stdout = old_stdout
    wsd = gvas_file.properties["worldSaveData"]["value"]

    # ── Extract all characters ──
    chars = wsd["CharacterSaveParameterMap"]["value"]

    # Build per-container pal lists
    container_pals = {}  # container_id -> [pal_info, ...]
    pal_by_instance_id = {}  # InstanceId -> pal_info, for attaching "job" below
    player_count = 0
    players = []  # [{"uid": <hyphen-free hex>, "name": ...}, ...]

    for entry in chars:
        instance_id = str(entry.get("key", {}).get("InstanceId", {}).get("value", ""))
        sp = entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
        is_player = sp.get("IsPlayer", {}).get("value", False)
        if is_player:
            player_count += 1
            uid = entry.get("key", {}).get("PlayerUId", {}).get("value")
            if uid is not None:
                p_level_data = sp.get("Level", {}).get("value", {})
                p_level = p_level_data.get("value", p_level_data) if isinstance(p_level_data, dict) else p_level_data
                players.append({
                    "uid": str(uid).replace("-", "").upper(),
                    "name": sp.get("NickName", {}).get("value", ""),
                    "level": p_level or 0,
                })
            continue

        char_id = sp.get("CharacterID", {}).get("value", "")
        nickname = sp.get("NickName", {}).get("value", "")

        # Level (ByteProperty with nested value)
        level_data = sp.get("Level", {}).get("value", {})
        level = level_data.get("value", level_data) if isinstance(level_data, dict) else level_data

        # SanityValue is the pal's actual SAN stat (0-100). It's omitted from
        # the save when at its default, which is full sanity.
        sanity = sp.get("SanityValue", {}).get("value")
        san = max(0.0, min(100.0, sanity)) if sanity is not None else 100.0

        # WorkerSick carries the real base-camp illness/injury (e.g. a
        # fracture from overwork), independent of the SAN value above — a
        # pal can be hurt while its SAN still reads fine.
        worker_sick = sp.get("WorkerSick", {}).get("value", {})
        sick_raw = worker_sick.get("value", "") if isinstance(worker_sick, dict) else ""
        sick_type = sick_raw.split("::")[-1] if isinstance(sick_raw, str) and "::" in sick_raw else ""
        is_sick = bool(sick_type) and sick_type != "None"
        sick_name = get_worker_sick_name(sick_type) if is_sick else ""

        # HP
        hp_struct = sp.get("Hp", {}).get("value", {})
        hp = hp_struct.get("Value", {}).get("value", 0) if isinstance(hp_struct, dict) else 0


        # Container placement
        slot_id = sp.get("SlotId", {})
        container_id = ""
        slot_index = -1
        if isinstance(slot_id, dict) and "value" in slot_id:
            sv = slot_id["value"]
            if isinstance(sv, dict):
                container_id = str(
                    sv.get("ContainerId", {}).get("value", {}).get("ID", {}).get("value", "")
                )
                slot_index = sv.get("SlotIndex", {}).get("value", -1)

        pal_info = {
            # Empty when the player hasn't set a nickname — the frontend
            # resolves the display name from "type" via pals_zh.json in
            # that case (see palName() in index.html).
            "name": nickname,
            "type": char_id,
            "san": round(san, 1),
            "level": level,
            "sick": is_sick,
            "sick_name": sick_name,
            "slot_index": slot_index,
            # Filled in below once work assignments are parsed; None means
            # this pal isn't currently assigned to any facility work slot.
            "job": None,
            "job_fixed": False,
            # Internal only — used to key the work-history snapshot in
            # build_status_json, popped before the pal dict is returned.
            "instance_id": instance_id,
        }
        if instance_id:
            pal_by_instance_id[instance_id] = pal_info

        # A real illness/injury takes priority over the SAN-based read —
        # a pal can be actively hurt (e.g. a fracture) while its SAN is fine.
        if is_sick:
            pal_info["status"] = f"生病:{sick_name}"
            pal_info["status_code"] = "DANGER"
        elif san < 30:
            pal_info["status"] = "SAN过低/情绪低落"
            pal_info["status_code"] = "DANGER"
        elif san < 60:
            pal_info["status"] = "疲惫/不适"
            pal_info["status_code"] = "WARNING"
        else:
            pal_info["status"] = "健康运作"
            pal_info["status_code"] = "OK"

        if container_id:
            container_pals.setdefault(container_id, []).append(pal_info)

    # ── Extract base camp info ──
    camps = wsd.get("BaseCampSaveData", {}).get("value", [])
    camp_count = len(camps)

    # ── Base facility placement + storage contents ──
    base_meta = _get_base_meta(wsd)
    facilities, resources, pending_resources, food_storage, work_slot_owner, power_storage, model_guid_owner = \
        _parse_base_facilities_and_resources(wsd, base_meta)
    farm_growth = _parse_farm_growth_state(wsd, model_guid_owner)

    # ── Current job per pal: work-slot GUID -> pal instance, cross-referenced
    # with work-slot GUID -> (base, facility type) to get "this pal is
    # working the StonePit at base 2" onto that pal's own record.
    work_assignments = _parse_work_assignments(wsd)
    for slot_guid, pal_instance_id, fixed in work_assignments:
        owner_facility = work_slot_owner.get(slot_guid)
        pal_info = pal_by_instance_id.get(pal_instance_id)
        if owner_facility and pal_info:
            pal_info["job"] = owner_facility[1]
            pal_info["job_fixed"] = fixed

    return {
        "container_pals": container_pals,
        "camp_count": camp_count,
        "player_count": player_count,
        "players": players,
        # base_key -> worker-roster container id, so build_status_json can
        # pull each base's pals without knowing how many bases there are.
        "base_containers": {k: m["container"] for k, m in base_meta.items()},
        "base_facilities": facilities,
        "base_resources": resources,
        "base_resources_pending": pending_resources,
        "base_food_storage": food_storage,
        "power_storage": power_storage,
        "farm_growth_state": farm_growth,
    }
