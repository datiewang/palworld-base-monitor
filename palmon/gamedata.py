"""The vendored game datatables (items / buildings / pals), loaded once.

These files are not in the repository — `palmon fetch-data` downloads them
into the state directory (see tools/fetch_game_data.py), because they are
someone else's datamine of the game's own tables and go stale with every
game update. A missing file is not fatal: every lookup here degrades to
"unknown" so the dashboard still parses a save without them.
"""

import json
import re

from .config import BUILDINGS_META_FILE, ITEMS_META_FILE, PALS_META_FILE


try:
    with open(BUILDINGS_META_FILE) as _f:
        BUILDINGS_META = json.load(_f)
except Exception:
    BUILDINGS_META = {}


try:
    with open(ITEMS_META_FILE) as _f:
        ITEMS_META = json.load(_f)
except Exception:
    ITEMS_META = {}


try:
    with open(PALS_META_FILE) as _f:
        PALS_META = json.load(_f)
except Exception:
    PALS_META = {}


def pal_meta_lookup(char_id: str) -> "dict | None":
    """pals_meta.json entry for a species, with the BOSS_-prefix fallback
    real save data needs (a boss variant like "BOSS_Kirin" or "Boss_Anubis"
    shares its base species' stats but isn't separately keyed in the
    vendored data) — the same fallback used client-side for names and
    work suitability."""
    meta = PALS_META.get(char_id)
    if meta is None:
        m = re.match(r"(?i)^boss_(.+)$", char_id)
        if m:
            meta = PALS_META.get(m.group(1))
    return meta


def pal_food_amount(char_id: str) -> "int | None":
    """Per-species food consumption tier — the game's own food_amount stat
    (vendored in pals_meta.json alongside max_full_stomach), not a wiki
    estimate or a size-class guess."""
    meta = pal_meta_lookup(char_id)
    return meta.get("food_amount") if meta else None
