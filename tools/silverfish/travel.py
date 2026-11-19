"""Evaluate the host gate for verified co-op map routes."""

from __future__ import annotations

import math
from typing import TypedDict

HOST_ID = "host"
PLAYTEST_MAPS = frozenset({
    "/Game/Classes/Maps/PlaytestMap_01",
    "/Game/Classes/Maps/PlaytestMap_02",
})
CROSSROADS = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
DEAD_BRIDGE = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
CISTERN = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
GOLD_NEXUS = "/Game/Classes/Maps/Dreams/GoldNexus_P"
OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
EXCHANGE = "/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P"
THE_ROOK = "/Game/Classes/Maps/Dreams/TheRook_P"
PYROCLAST = "/Game/Classes/Maps/Dreams/Pyroclast_P"
SLATE = "/Game/Classes/Maps/Dreams/Slate_P"
BLACK_OCEAN = "/Game/Classes/Maps/Dreams/BlackOcean_P"
NOTHING = "/Game/Classes/Maps/Dreams/Nothing_P"
DREAM_MAPS = frozenset({EXCHANGE, THE_ROOK, PYROCLAST, SLATE, BLACK_OCEAN, NOTHING})
LAB_E206 = "/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P"
EAST_WING = "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P"
OLD_PIPES = "/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P"
SCI_TUNNEL = "/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P"
SLUMS_TUNNEL = "/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P"
SUBWAY = "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P"
UTILITY_TUNNELS = "/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P"
TRASH_PIT = "/Game/Classes/Maps/Maps_OverWorld/TrashPit_P"
RAIL_YARD = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
RUIN_FIELDS = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
NORTHEAST_RAIL = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P"
SLUMS = "/Game/Classes/Maps/Maps_OverWorld/Slums_P"
CITY = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
SCAV_BAR = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
SHOPKEEP = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"
BLACK_MARKET = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
SCIENTIST_SHOP = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
FOREST = "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P"
LAKE = "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P"
BEACH = "/Game/Classes/Maps/Maps_OverWorld/Beach_01_P"
DOCKS = "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"
REFINERY = "/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P"
RED_DESTINATIONS = (CISTERN, LAB_E206, EAST_WING, OLD_PIPES, SLUMS_TUNNEL, SCI_TUNNEL, SUBWAY,
                    UTILITY_TUNNELS, TRASH_PIT)
AUTHORED_EXITS = {
    (CROSSROADS, DEAD_BRIDGE): "DB_CR",
    (DEAD_BRIDGE, CROSSROADS): "DB_CR",
    (CROSSROADS, CISTERN): "CR_Cstrn",
    (CISTERN, CROSSROADS): "CR_Cstrn",
    (GOLD_NEXUS, CROSSROADS): "Nexus_CrossRoads",
    (CROSSROADS, OBSERVATORY): "Obsv_Etr",
    (OBSERVATORY, CROSSROADS): "Obsv_Etr",
    (LAB_E206, RAIL_YARD): "RY_Lab206",
    (EAST_WING, RUIN_FIELDS): "RgEast_Flood",
    (OLD_PIPES, NORTHEAST_RAIL): "NE_Pipe",
    (SCI_TUNNEL, CISTERN): "Cistern_Sci",
    (SLUMS_TUNNEL, SLUMS): "Slums_GreenLine",
    (SUBWAY, CITY): "Subway",
    (UTILITY_TUNNELS, RAIL_YARD): "Rail_Utility",
    (TRASH_PIT, NORTHEAST_RAIL): "M35_Maw",
    (RAIL_YARD, CROSSROADS): "Depot_Cross_Rail",
    (RUIN_FIELDS, CROSSROADS): "CR_RF",
    (NORTHEAST_RAIL, CROSSROADS): "Cross_NE",
    (SLUMS, RAIL_YARD): "Slum_Depot",
    (CITY, RAIL_YARD): "Rail_City",
    (SCAV_BAR, SLUMS): "Bar_Slums",
    (SHOPKEEP, NORTHEAST_RAIL): "ShopKeep",
    (BOROUGH, CITY): "City_Borough",
    (BLACK_MARKET, LAKE): "BlackMarket",
    (LAKE, CROSSROADS): "Lake_Cave",
    (SCIENTIST_SHOP, RUIN_FIELDS): "Market_Flood",
    (FOREST, CROSSROADS): "CR_Forest",
    (BEACH, RUIN_FIELDS): "Flood_Beach",
    (DOCKS, CITY): "Metro_Dock",
    (REFINERY, SLUMS): "Slum_Refine",
}
AUTHORED_MAPS = DREAM_MAPS | {map_name for pair in AUTHORED_EXITS for map_name in pair}
TRAVEL_RADIUS_UU = 1500


class TravelBlocker(TypedDict):
    id: str
    reason: str


class TravelDecision(TypedDict):
    allowed: bool
    blockers: list[TravelBlocker]


def _deny(participant: str, reason: str) -> TravelDecision:
    return {"allowed": False, "blockers": [{"id": participant, "reason": reason}]}


def _position_in_zone(pawn: object, zone: str) -> tuple[tuple[float, ...] | None, str]:
    if not isinstance(pawn, dict):
        return None, "missing_pawn"
    if pawn.get("map") != zone:
        return None, "wrong_zone"
    coords = pawn.get("location")
    if not isinstance(coords, (list, tuple)) or len(coords) != 3:
        return None, "invalid_position"
    if any(type(axis) not in (int, float) for axis in coords):
        return None, "invalid_position"
    try:
        position = tuple(float(axis) for axis in coords)
    except OverflowError:
        return None, "invalid_position"
    if not all(math.isfinite(axis) for axis in position):
        return None, "invalid_position"
    return position, ""


def travel_decision(
    role: str,
    source: str,
    destination: str,
    active_ids: tuple[str, ...],
    pawns: dict,
    *,
    allowed_maps: frozenset[str] = PLAYTEST_MAPS,
    allow_same_zone: bool = False,
) -> TravelDecision:
    """Return a deterministic travel decision from host-authoritative pawn positions.

    The caller includes any in-grace participants in active_ids. The host always
    requires a position in the source map. The gate treats an unknown or stale
    location as a blocker, even when the client connection is recovering.
    """
    if role != "host":
        return _deny(HOST_ID, "host_only")
    if not isinstance(source, str) or source not in allowed_maps:
        return _deny(HOST_ID, "unknown_source")
    if not isinstance(destination, str) or destination not in allowed_maps:
        return _deny(HOST_ID, "unknown_destination")
    if source == destination and not allow_same_zone:
        return _deny(HOST_ID, "same_zone")

    host_position, host_issue = _position_in_zone(pawns.get(HOST_ID), source)
    if host_position is None:
        return _deny(HOST_ID, host_issue)

    blockers: list[TravelBlocker] = []
    for participant in sorted(set(active_ids) - {HOST_ID}):
        position, issue = _position_in_zone(pawns.get(participant), source)
        if position is None:
            blockers.append({"id": participant, "reason": issue})
        elif math.dist(host_position, position) > TRAVEL_RADIUS_UU:
            blockers.append({"id": participant, "reason": "too_far"})
    return {"allowed": not blockers, "blockers": blockers}
