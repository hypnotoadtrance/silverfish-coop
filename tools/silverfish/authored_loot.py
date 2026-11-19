"""Exact stock persistent-level small crates qualified for shared loot."""

from .travel import CROSSROADS, DEAD_BRIDGE


PLACEMENTS = (
    (CROSSROADS, "LootCrate_Small_BP_C_3", -1911374248),
    (CROSSROADS, "LootCrate_Small_BP_C_9", -564104064),
    (DEAD_BRIDGE, "LootCrate_Small_BP_C_1", -1960753196),
)


def full_name(area: str, actor: str) -> str:
    return f"LootCrate_Small_BP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.{actor}"


CRATES = {full_name(area, actor): area for area, actor, _ in PLACEMENTS}
LEGACY_CRATE = full_name(*PLACEMENTS[0][:2])
