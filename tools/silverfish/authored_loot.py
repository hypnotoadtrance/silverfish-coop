"""Exact stock persistent-level small crates qualified for shared loot."""

from .travel import CITY, CISTERN, CROSSROADS, DEAD_BRIDGE, RAIL_YARD


PLACEMENTS = (
    (CROSSROADS, "LootCrate_Small_BP_C_3", -1911374248),
    (CROSSROADS, "LootCrate_Small_BP_C_9", -564104064),
    (DEAD_BRIDGE, "LootCrate_Small_BP_C_1", -1960753196),
    (CITY, "LootCrate_Small_BP_C_5", -1685293152),
    (CITY, "LootCrate_Small_BP_C_7", -436292608),
    (CITY, "LootCrate_Small_BP_C_9", -140629248),
    (CITY, "LootCrate_Small_BP_C_11", -1132847040),
    (CITY, "LootCrate_Small_BP_C_13", -1857173376),
    (CITY, "LootCrate_Small_BP_C_15", -1228919296),
    (CITY, "LootCrate_Small_BP_C_17", -1284951360),
    (RAIL_YARD, "LootCrate_Small_BP_C_0", -1578844576),
    (RAIL_YARD, "LootCrate_Small_BP_C_1", -1652760384),
    (RAIL_YARD, "LootCrate_Small_BP_C_3", -1449629184),
    (RAIL_YARD, "LootCrate_Small_BP_C_4", -619281664),
    (RAIL_YARD, "LootCrate_Small_BP_C_11", -451063552),
    (RAIL_YARD, "LootCrate_Small_BP_C_14", -1014618368),
    (RAIL_YARD, "LootCrate_Small_BP_C_15", -780724480),
    (CISTERN, "LootCrate_Small_BP_C_1", -1486678656),
    (CISTERN, "LootCrate_Small_BP_C_3", -400280704),
    (CISTERN, "LootCrate_Small_BP_C_4", -1515671264),
    (CISTERN, "LootCrate_Small_BP_C_5", -1430707776),
    (CISTERN, "LootCrate_Small_BP_C_7", -1247535616),
)


def full_name(area: str, actor: str) -> str:
    return f"LootCrate_Small_BP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.{actor}"


CRATES = {full_name(area, actor): area for area, actor, _ in PLACEMENTS}
LEGACY_CRATE = full_name(*PLACEMENTS[0][:2])
