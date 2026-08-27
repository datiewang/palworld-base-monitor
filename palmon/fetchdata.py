"""Downloading and building the game data the dashboard reads.

Nothing here is shipped in the repository: these files are datamines of the
game's own datatables, they are large, and they go stale with every game
update. `palmon fetch-data` writes them into the state directory instead.

Three kinds of file end up there:

  *_meta.json / *_l10n.json  copied straight from palworld-save-pal's
                             vendored datatables (item/building/pal stats,
                             and their localized names).
  recipes.json               built from palworld.wiki.gg's datamined recipe
                             module, joined onto internal item ids.
  merchants.json             built from the same wiki's MerchantItem Cargo
                             table plus the shop-location table below.

Why the wiki for recipes: the authoritative source is the server's own
Pal/Content/Pal/DataTable/Item/DT_ItemRecipeDataTable inside
Pal-LinuxServer.pak. That pak's index is unencrypted and parses fine, but
every asset in it is Oodle-compressed, and Oodle is proprietary — so the
recipes have to come from a datamine someone else already did.

Two wiki access notes worth keeping: ?action=raw sits behind wiki.gg's bot
filter and answers 403, while api.php does not; and Cargo hands back shop
group names page-title-normalised ("Wander Shop 1"), so they are mapped
back to the underscore ids the templates use.
"""

import json
import os
import sys
import urllib.request

from .config import DATA_DIR, setting

# The language used for every localized name on the dashboard. Any
# directory under the upstream l10n/ tree works: en, ja, ko, de, fr, ru,
# zh-Hans, zh-Hant, ...
LANGUAGE = setting("data", "language", "zh-Hans")

UPSTREAM = ("https://raw.githubusercontent.com/oMaN-Rod/palworld-save-pal/main/data/json")
WIKI_API = "https://palworld.wiki.gg/api.php"
# wiki.gg serves a bot-check page to the default urllib agent.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")

# upstream name -> local name. The dashboard reads the local names.
DATATABLES = {
    "items": "items_meta.json",
    "buildings": "buildings_meta.json",
    "pals": "pals_meta.json",
}
L10N_TABLES = {
    "items": "items_l10n.json",
    "buildings": "buildings_l10n.json",
    "pals": "pals_l10n.json",
    "work_suitability": "work_suitability_l10n.json",
}

# Which merchant sells a shop group, and where that merchant stands.
#
# Hand-transcribed from palworld.wiki.gg's merchant pages on purpose: it is
# a dozen rows that change about never, and the alternative is scraping
# prose bullet lists ("Shared by: *Small Settlement merchant (78, -477)")
# and tabber labels ("|-|Duneshelter red shirt merchant="), which is far
# more fragile than a transcription whose source text is quoted right here.
#
# The four Wander_Shop_1 spots are quoted verbatim from the Wandering
# Merchant page, coordinates included, in the game's own map coordinate
# system. Settlement merchants get a place name and no coordinates: the
# wiki does not publish theirs, and converting the fast-travel points'
# world coordinates was tried and abandoned — fitting the transform against
# 32 alpha-pal positions whose map coordinates the wiki does publish left
# residuals of hundreds of map units (the DLC islands appear to use their
# own coordinate spaces), and a coordinate that is confidently wrong is
# worse than a place name that is right.
SHOPS = {
    "Wander_Shop_1": {
        "merchant": "流浪商人", "merchant_en": "Wandering Merchant",
        "locations": [
            {"name": "小型聚落", "en": "Small Settlement", "coords": [78, -477]},
            {"name": "潮风群岛", "en": "Sea Breeze Archipelago", "coords": [-190, -600]},
            {"name": "Forgotten Island 海滩", "en": "Forgotten Island beach", "coords": [-397, 19]},
            {"name": "湿地之岛海岸", "en": "Marsh Island shore", "coords": [434, -273]},
        ],
        "note": "固定商品，另有随机事件中出现的同款流浪商人",
    },
    "Desert_Shop_1": {
        "merchant": "流浪商人（红衣）", "merchant_en": "Duneshelter red shirt merchant",
        "locations": [{"name": "沙漠之镇", "en": "Duneshelter"}],
    },
    "Desert_Shop_2": {
        "merchant": "流浪商人（绿衣）", "merchant_en": "Duneshelter green shirt merchant",
        "locations": [{"name": "沙漠之镇", "en": "Duneshelter"}],
    },
    "Volcano_Shop_1": {
        "merchant": "流浪商人（红衣）", "merchant_en": "Fisherman's Point red shirt merchant",
        "locations": [{"name": "边远渔村", "en": "Fisherman's Point"}],
    },
    "Volcano_Shop_2": {
        "merchant": "流浪商人（绿衣）", "merchant_en": "Fisherman's Point green shirt merchant",
        "locations": [{"name": "边远渔村", "en": "Fisherman's Point"}],
    },
    "Caravan_Shop_1": {
        "merchant": "流民商队长", "merchant_en": "Caravan Leader",
        "locations": [], "note": "周期性造访玩家据点，无固定位置",
    },
    "Caravan_Shop_2": {
        "merchant": "流民商队长", "merchant_en": "Caravan Leader",
        "locations": [], "note": "周期性造访玩家据点，无固定位置",
    },
    "Caravan_Shop_3": {
        "merchant": "流民商队长", "merchant_en": "Caravan Leader",
        "locations": [], "note": "周期性造访玩家据点，无固定位置",
    },
    "Arena_Shop_1": {
        "merchant": "竞技场商人", "merchant_en": "Arena Merchant",
        "locations": [{"name": "帕鲁竞技场", "en": "Pal Arena"}],
        "note": "只收战斗券",
    },
    "Medal_Shop_1": {
        "merchant": "奖章商人", "merchant_en": "Medal Merchant",
        "locations": [{"name": "各处废弃教堂", "en": "abandoned churches"}],
        "note": "只收汪汪币",
    },
    "Bounty_Shop_1": {
        "merchant": "PIDF 悬赏官", "merchant_en": "PIDF Bounty Officer",
        "locations": [{"name": "小型聚落", "en": "Small Settlement"}],
        "note": "只收讨伐令牌",
    },
}

# Wiki item names that differ from the game's own English name. Only ones
# verified to be the same item are listed — the rest of the unmatched names
# (skill fruits, schematics, cosmetics) are reported and skipped rather than
# guessed at, since none of them are crafting materials.
NAME_ALIASES = {
    "Pal Fluids": "Aquatic Pal Fluids",
    "Ring of Earth Resistance": "Ring of Ground Resistance",
    "Ring of Lightning Resistance": "Ring of Electric Resistance",
    "Ring of Flame Resistance": "Ring of Fire Resistance",
}


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _write(name: str, obj) -> int:
    path = os.path.join(DATA_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    size = os.path.getsize(path)
    print(f"  {name:26s} {size / 1024:8.0f} KB")
    return size


def _load_local(name: str):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def fetch_datatables() -> None:
    print("datatables (palworld-save-pal):")
    for upstream, local in DATATABLES.items():
        _write(local, _get_json(f"{UPSTREAM}/{upstream}.json"))
    print(f"localized names ({LANGUAGE}):")
    for upstream, local in L10N_TABLES.items():
        _write(local, _get_json(f"{UPSTREAM}/l10n/{LANGUAGE}/{upstream}.json"))


def _english_name_index() -> dict:
    """English display name -> [item id, ...].

    Ids the game has disabled are dropped first: several retired items still
    hold a name a live item now uses, and they would otherwise win the join
    by sort order.
    """
    en_items = _get_json(f"{UPSTREAM}/l10n/en/items.json")
    items_meta = _load_local(DATATABLES["items"])
    index = {}
    for iid, v in en_items.items():
        name = (v.get("localized_name") or "").strip()
        if not name:
            continue
        if items_meta.get(iid, {}).get("disabled"):
            continue
        index.setdefault(name, []).append(iid)
    return index


def build_recipes() -> int:
    """recipes.json — every craftable item's recipe, keyed by internal id.

    The wiki module is keyed by English display name, so the join is on that
    name. It is exact for this dataset (814 recipes, every product and every
    material resolving), and the build refuses to write a half-joined file
    rather than silently hiding recipes when the two sources drift apart.

    The one soft spot is products whose name is shared by several ids —
    tiered accessories (Attack Pendant I/II/III are three ids with one
    display name) and Gunpowder/Gunpowder2. The wiki carries one recipe for
    the name, so it is attributed to the lowest id (the base tier; the higher
    tiers are upgrades, not separate crafts) and the alternatives are listed
    under "ambiguous" so the page can say so instead of quietly picking one.
    """
    print("recipes (palworld.wiki.gg):")
    wiki = _get_json(
        f"{WIKI_API}?action=query&prop=revisions&titles=Module:DataManager/item_data.json"
        "&rvslots=main&rvprop=content&format=json&formatversion=2")
    try:
        content = wiki["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    except (KeyError, IndexError):
        print("  wiki API returned an unexpected shape — nothing written", file=sys.stderr)
        return 1
    wiki_items = json.loads(content)

    name_to_ids = _english_name_index()
    buildings_meta = _load_local(DATATABLES["buildings"])

    def resolve(name: str):
        ids = sorted(name_to_ids.get((name or "").strip(), []))
        return (ids[0], ids[1:]) if ids else (None, [])

    recipes, ambiguous_count = {}, 0
    unresolved_products, unresolved_materials = [], []
    for wiki_name, entry in wiki_items.items():
        for recipe in entry.get("recipe") or []:
            product_id, alternatives = resolve(wiki_name)
            if product_id is None:
                unresolved_products.append(wiki_name)
                continue
            materials, ok = [], True
            for spec in recipe.get("materials") or []:
                # "Red Berries*8" — the name itself can contain spaces, so
                # split on the last asterisk only.
                mat_name, _, count = spec.rpartition("*")
                mat_id, _ = resolve(mat_name)
                if mat_id is None or not count.isdigit():
                    unresolved_materials.append(spec)
                    ok = False
                    continue
                materials.append([mat_id, int(count)])
            if not ok or not materials:
                continue
            row = {"materials": materials,
                   "count": recipe.get("production_count") or 1,
                   "work": recipe.get("workload_to_craft") or 0}
            if alternatives:
                row["ambiguous"] = alternatives
                ambiguous_count += 1
            recipes.setdefault(product_id, []).append(row)

    if unresolved_products or unresolved_materials:
        print(f"  unresolved products: {unresolved_products[:20]}", file=sys.stderr)
        print(f"  unresolved materials: {unresolved_materials[:20]}", file=sys.stderr)
        print("  refusing to write a partial recipes.json", file=sys.stderr)
        return 2

    # Building costs are already id-keyed in the datatable — no join needed.
    buildings = {}
    for bid, meta in buildings_meta.items():
        mats = meta.get("materials") or []
        if not mats:
            continue
        buildings[bid] = {
            "materials": [[m["id"], m["count"]] for m in mats if m.get("id")],
            "work": meta.get("required_build_work_amount") or 0,
        }

    _write("recipes.json", {
        "source": {"items": "palworld.wiki.gg Module:DataManager/item_data.json",
                   "names": "palworld-save-pal l10n/en/items.json",
                   "buildings": "buildings_meta.json (materials field)"},
        "items": recipes,
        "buildings": buildings,
    })
    rows = sum(len(v) for v in recipes.values())
    print(f"    {len(recipes)} products / {rows} recipes "
          f"({ambiguous_count} with a shared name), {len(buildings)} buildings")
    return 0


def build_merchants() -> int:
    """merchants.json — what each merchant sells and where that merchant is.

    Stock comes from the wiki's MerchantItem Cargo table (the wiki's own shop
    pages are generated from it); locations come from SHOPS above.
    """
    print("merchants (palworld.wiki.gg):")
    rows, offset = [], 0
    while True:
        page = _get_json(
            f"{WIKI_API}?action=cargoquery&format=json&formatversion=2&tables=MerchantItem"
            "&fields=_pageName%3D_pageName,shopGroup%3DshopGroup,itemName%3DitemName,"
            "costAmount%3DcostAmount,currency%3Dcurrency,minQty%3DminQty,maxQty%3DmaxQty"
            f"&limit=500&offset={offset}")
        got = [r["title"] for r in page.get("cargoquery", [])]
        rows += got
        if len(got) < 500:
            break
        offset += 500

    name_to_ids = _english_name_index()

    def resolve(name: str):
        key = (name or "").strip()
        ids = sorted(name_to_ids.get(NAME_ALIASES.get(key, key), []))
        return ids[0] if ids else None

    items, unknown_shops, unmatched = {}, set(), []
    for row in rows:
        group = (row.get("shopGroup") or "").strip().replace(" ", "_")
        if group not in SHOPS:
            unknown_shops.add(f"{row.get('_pageName')}/{group}")
            continue
        item_id = resolve(row.get("itemName"))
        if item_id is None:
            # Schematics and cosmetics the item table doesn't carry under
            # that exact name. Recorded, not silently dropped — but they are
            # not base materials, so they don't block the build.
            unmatched.append(row.get("itemName"))
            continue
        entry = {
            "shop": group,
            "price": int(row["costAmount"]) if str(row.get("costAmount", "")).isdigit() else None,
            # Currency as an item id where it is one (Gold Coin -> Money),
            # otherwise the wiki's own label.
            "currency": resolve(row.get("currency")) or (row.get("currency") or "").strip(),
        }
        for qty_key in ("minQty", "maxQty"):
            if str(row.get(qty_key, "")).isdigit():
                entry[qty_key] = int(row[qty_key])
        items.setdefault(item_id, []).append(entry)

    _write("merchants.json", {
        "source": {"stock": "palworld.wiki.gg Cargo table MerchantItem",
                   "shops": "transcribed from the same wiki's merchant pages"},
        "shops": SHOPS,
        "items": items,
    })
    print(f"    {len(rows)} rows, {len(items)} items sold")
    if unknown_shops:
        print(f"    shop groups not in SHOPS (skipped): {sorted(unknown_shops)}", file=sys.stderr)
    if unmatched:
        print(f"    item names with no id ({len(unmatched)}), e.g. {unmatched[:6]}", file=sys.stderr)
    return 0


STEPS = {
    "datatables": fetch_datatables,
    "recipes": build_recipes,
    "merchants": build_merchants,
}


def fetch_all(only=None) -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    wanted = only or list(STEPS)
    unknown = [w for w in wanted if w not in STEPS]
    if unknown:
        print(f"unknown dataset(s): {unknown}; known: {list(STEPS)}", file=sys.stderr)
        return 2
    print(f"writing into {DATA_DIR}")
    rc = 0
    for name in wanted:
        rc = STEPS[name]() or rc
    return rc
