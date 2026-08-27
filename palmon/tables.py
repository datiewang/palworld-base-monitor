"""Lookup tables read off the game's own data, kept apart from the code.

Every table here answers "what kind of thing is this MapObjectId / ItemId",
and each one carries the evidence for how it was derived in its own comment.
They live in their own module because both the save parser and the metrics
layer need them, and neither should have to import the other.
"""


# EPalBaseCampWorkerSickType -> Chinese name (base camp worker illness/injury).
# Enumerator names confirmed against a dumped Palworld C++ header (the
# in-game enum, not a guess): None, Cold, Sprain, Bulimia, GastricUlcer,
# Fracture, Weakness, DepressionSprain, DisturbingElement. The previous
# version of this map had "Ulcer"/"Weakened"/"Depressed" — none of which
# are the real enum names (GastricUlcer/Weakness/DepressionSprain), so
# those three sicknesses were silently falling back to raw English.
WORKER_SICK_MAP = {
    "Cold": "感冒",
    "Sprain": "扭伤",
    "Bulimia": "暴食症",
    "GastricUlcer": "胃溃疡",
    "Fracture": "骨折",
    "Weakness": "虚弱",
    "DepressionSprain": "抑郁",
    "DisturbingElement": "烦躁不安",
}


# Raw-material gathering nodes (mining pits, logging stations, the oil
# pump, fishing ponds, the skill-fruit orchard): each has its own small
# ItemContainer buffer holding what it has produced but a carrier pal
# hasn't moved to real storage yet. Grounded in real save data (every
# MapObjectId carrying an ItemContainer+Workee+Energy module signature)
# cross-checked against the game's own building descriptions — these all read "挖掘/生产...的
# 设施" (raw extraction), as opposed to the crafting stations below.
GATHERING_NODE_TYPES = {
    "CoalPit", "CopperPit", "CopperPit_2", "CrystalPit", "QuartzPit",
    "SkyIslandOrePit", "StonePit", "SulfurPit",
    "StationDeforest2", "StationDeforest3", "OilPump02",
    "FishingPond1", "FishingPond2", "Farm_SkillFruits",
}


# The save spells the quarry's MapObjectId "Stonepit"; the game's own
# building datatable — and so buildings_zh.json, the frontend's facility
# icon table, and its facility -> work-suitability map — spells it
# "StonePit". Nothing else about it differs: same
# Energy+ItemContainer+Workee signature, same stone in its buffer. It is
# folded into the datatable spelling as the save is read, because under
# the save's own spelling a quarry matches no lookup keyed by facility type
# at all — it falls outside GATHERING_NODE_TYPES, so its mined stone counts
# as warehouse stock instead of an uncollected backlog, and it renders in
# the facility list under the raw English id.
MAP_OBJECT_ID_ALIASES = {"Stonepit": "StonePit"}


# Loose loot lying on the base floor rather than a placed facility: a
# CommonDropItem3D is the pile left when a pal drops what it was carrying
# and nothing picks it back up — a busy base accumulates dozens of them,
# holding thousands of items. It carries an ItemContainer module
# exactly like a chest does, so without this it was counted twice over —
# once as a facility in its own right, once as base storage — padding the
# storage totals with items no crafting station or feed box can actually
# draw on. Excluded from the facility list and from every item bucket.
#
# Placed eggs (PalEgg_Dark and friends) are deliberately NOT here. They are
# also bare single-item ItemContainers, but an egg is a real possession
# sitting in the base rather than spillage, and a count of dark eggs in
# the facility list is worth seeing where a count of dropped piles is not.
LOOSE_ITEM_TYPES = {"CommonDropItem3D"}


# Crafting/processing stations sharing the same module signature: their
# ItemContainer buffer mixes queued input materials with finished output —
# neither "in storage" nor "raw materials awaiting pickup" in the sense the
# two buckets below mean, so these are deliberately excluded from both
# rather than guessed into either.
#
# Written from the first-tier buildings originally, which ages badly: a
# base that has upgraded its stations matches almost none of them, and each
# upgraded station then drops its mixed input/output buffer into the
# storage bucket — hundreds of items per station, counted as though they
# were sitting in a chest. The list now carries each station family's whole
# upgrade chain, since a tier behaves identically for this purpose.
CRAFTING_STATION_TYPES = {
    "AncientBlastFurnace", "AncientCookingStove", "AncientRelicRecycler",
    "AncientWorkBench", "BreedFarm", "HugeKitchen",
    "BlastFurnace", "BlastFurnace2", "BlastFurnace3", "BlastFurnace4",
    "CampFire",
    "CookingStove", "ElectricKitchen", "CompositeDesk", "Crusher",
    "FlourMill", "Factory_Hard_01", "Factory_Hard_02", "Factory_Hard_03",
    "Factory_Hard_04",
    "ElectricHatchingPalEgg", "HatchingPalEgg", "MultiHatchingPalEgg",
    "MultiElectricHatchingPalEgg", "MultiElectricHatchingPalEggWithBreed",
    "MedicineFacility_01", "MedicineFacility_02", "MedicineFacility_03",
    "SphereFactory_Black_02", "SphereFactory_Black_03", "SphereFactory_Black_04",
    "WeaponFactory_Dirty_01", "WeaponFactory_Dirty_02", "WeaponFactory_Dirty_03",
    "WorkBench_SkillUnlock", "Workbench",
}


# The Pal Food Box: what pals actually eat from directly, as opposed to
# player storage (chests) or a crafting station's input/output buffer.
# Tracked as its own bucket (food_storage), separate from both
# GATHERING_NODE_TYPES/CRAFTING_STATION_TYPES above and the general
# ItemContainer resources merge below, so "how much food can a pal
# actually reach right now" isn't diluted by whatever's sitting in an
# unrelated chest.
# Both entries are feed boxes a hungry pal walks up to and eats from —
# they are exactly the buildings whose zh-Hans metadata carries
# type_a="Food" + type_b="Food_Basic" and is not a farm plot
# (CoolerPalFoodBox 低温保鲜饲料箱 is just a PalFoodBox that keeps its
# contents from spoiling). CoolerBox 保冷箱 is deliberately NOT here:
# despite the similar name it is type_a="Storage", a food chest pals do
# not eat from, so it belongs in the general resources bucket.
FOOD_BOX_TYPES = {"PalFoodBox", "CoolerPalFoodBox"}


# Satiety (hunger) restored per item, sourced from palworld.wiki.gg's
# "Food" and "Food/List" pages plus paldb.cc's raw-ingredient table
# (checked 2026-08-17). This is deliberately partial: the wiki only
# publishes numbers by display name, not by the game's internal item ID,
# and Palworld has well over a hundred food variants (many pal-specific
# meats/dishes with no generic equivalent on those pages) — so only
# entries below were confidently matched (exact name + description
# correspondence to a known internal ID). Anything not listed here is
# real food that a pal can eat, just with an amount this dashboard can't
# verify; food-satiety totals below explicitly exclude it rather than
# guess, and report it as a separate "unmapped" count instead.
SATIETY_VALUES = {
    # Raw ingredients (paldb.cc/en/Ingredient)
    "Berries": 15, "Tomato": 15, "Wheat": 6, "Flour": 3, "Lettuce": 15,
    "Egg": 16, "Milk": 12, "Mushroom": 13, "Honey": 10, "Potato": 11,
    "Carrot": 10, "Onion": 9, "CaveMushroom": 15,
    # Cooked dishes (palworld.wiki.gg/wiki/Food/List), matched to internal
    # IDs by exact name (Baked_Berries="Baked Berries", Pan="Bread" — Pan
    # is the game's internal ID for the basic bread dish, confirmed via its
    # localized name matching "面包"/Bread).
    "Baked_Berries": 21, "BakedMushroom": 18, "Pan": 27, "Cake": 656,
    "MushroomSoup": 52, "Salad": 84, "Omelet": 67, "Pancake": 42,
    "Pizza": 184, "Minestrone": 146, "Gratin": 113, "SpringRolls": 115,
    "JamBun": 51,
}


# A facility counts as "functional" (shown in the dashboard's facility
# list) if its ConcreteModel has at least one of these modules — anything
# with none of them is pure scenery (walls/roofs/foundations, statues,
# fake trees, rugs, signboards: verified empty-module in real saves). The
# exception is IMPORTANT_EMPTY_MODULE_TYPES below: a handful of facilities
# that matter operationally despite carrying no module data at all (the
# Pal Box, beds, spas, the repair bench, the battery). Filtering happens
# server-side since the module signature isn't otherwise sent to the
# frontend.
FUNCTIONAL_MODULE_TYPES = {
    "ItemContainer", "Workee", "Energy", "CharacterContainer",
    "GuildSecurity", "PasswordLock",
}


#
# Everything past the beds was found by checking every object standing
# inside a base against this filter: all of them carry an empty ModuleMap,
# so all of them were being discarded along with the walls and the
# flowerbeds, and each does something a base operator cares about — the two wave generators are
# base-wide work-speed and sanity buffs, the Statue of Power and the Pal
# condenser change pal stats, the transmission tower carries the power
# grid, the monitoring stand sets work priority.
#
# The beds were the costlier miss: this named three of the five bed types
# in the game's datatable, so the two upper tiers went uncounted and any
# base using them under-reported
# their real 15 sleeping places.
IMPORTANT_EMPTY_MODULE_TYPES = {
    "PalBoxV2",
    "Ancient_MedicalPalBed", "MedicalPalBed_02", "MedicalPalBed_03",
    "MedicalPalBed_04", "MedicalPalBed_05",
    "Spa", "Spa2", "RepairBench", "EnergyStorage_Electric",
    "GlobalPalStorage", "ToolBoxV1",
    "BaseCampItemDispenser", "BuildableGoddessStatue", "CharacterRankUp",
    "OperatingTable", "SanityDecrease1", "TransmissionTower",
    "WorkSpeedIncrease1",
    # The two lower monitoring-stand tiers are inferred from the third,
    # which is confirmed empty-module; it is the same building.
    "BaseCampWorkHard", "BaseCampWorkHard02", "BaseCampWorkHard03",
    # Inferred, not confirmed — no instance available to check; every
    # other Infra_GeneratePower facility has an empty ModuleMap, so this
    # almost certainly does too.
    "ManualElectricGenerator",
}


# Generators and the battery all carry a live "current charge" float at a
# fixed byte offset in ConcreteModel.RawData (there's no module for it —
# these three are exactly the IMPORTANT_EMPTY_MODULE_TYPES entries with no
# ModuleMap data at all). Confirmed real and live, not a static per-type
# constant: re-read ~40 minutes apart in the same session, values had moved
# off their round starting points (e.g. 1,000,000.0 -> 997,496.375) instead
# of staying fixed.
#
# The capacities were palworld.wiki.gg's storage-capacity table until real
# readings contradicted it: EnergyStorage_Electric reads exactly
# 1,200,000.0 when full, and an ElectricGenerator_Large was seen at
# 1,175,977.5 — both above the 1,000,000 they were supposedly capped at, so
# a full battery reported 89% charge. A charge cannot exceed its own
# capacity, so the wiki figures were simply stale; two batteries resting on
# a round 1,200,000 is what full looks like.
#
# AncientElectricGenerator was missing from this table altogether, which
# made a base with one report 0 / 0 power while its generator sat on a full
# 2,400,000.0 — exactly twice the above, read at the same offset.
POWER_STORAGE_TYPES = {
    "ElectricGenerator_Large": 1200000,
    "EnergyStorage_Electric": 1200000,
    "AncientElectricGenerator": 2400000,
    # Neither of these was available to read, so both capacities are still
    # the wiki's — and the wiki was already wrong about the two above, so
    # treat them as placeholders to be re-read against a real instance
    # rather than as verified numbers.
    "ElectricGenerator": 250000,
    "ManualElectricGenerator": 50000,
}


# See _patch_work_decoders' docstring: current_state == 1 means a Progress
# work item is still running (for a farm plot, still growing); this one
# value means it's sitting done and uncollected. Inferred from an observed
# pattern, not a documented enum — flagged here as one named constant
# rather than a bare "3" so the uncertainty has one place to revisit if a
# save ever contradicts it.
FARM_STATE_WAITING = 3


# Same idea for the *output* side, attributing an hour's measured
# production to a kind of work. Deliberately coarser than the facility
# tags above: a farm plot, a ranch and a kitchen all end up as "food",
# because an item's datatable category can say what it is but not which
# building made it (an Egg is FoodMeat whether it came from a ranch or a
# hunt), and inventing a finer split would be a guess dressed up as data.
#
# The overrides are the items whose category contradicts how they are
# actually obtained here: Paldium Fragment files as MaterialOre, Flour as
# FoodVegetable and Charcoal as MaterialIngot, but all three come out of a
# crafting station (Crusher / Flour Mill / furnace), not out of the ground.
# Rainbow Crystal is deliberately NOT among them — it looked like the same
# case, but a base with a 六棱晶矿采矿场 really does mine it, so it is
# gathered after all. Anything unmatched is counted as "other" rather than dropped.
CRAFTED_ITEM_OVERRIDES = {"Pal_crystal_S": "crafting", "Flour": "crafting",
                          "Charcoal": "crafting"}


# Which output bucket counts as evidence that a facility tag is actually
# running. Kitchens, farm plots and ranches all produce "food", so they
# share one bucket; power has no output item at all and is never checked.
ROLE_OUTPUT_BUCKET = {
    "gathering": "gathering", "crafting": "crafting",
    "farming": "food", "cooking": "food", "breeding": "food",
}
