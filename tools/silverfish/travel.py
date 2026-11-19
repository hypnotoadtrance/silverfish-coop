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
AUTHORED_EXITS = {
    (CROSSROADS, DEAD_BRIDGE): "DB_CR",
    (DEAD_BRIDGE, CROSSROADS): "DB_CR",
    (CROSSROADS, CISTERN): "CR_Cstrn",
    (CISTERN, CROSSROADS): "CR_Cstrn",
    (GOLD_NEXUS, CROSSROADS): "Nexus_CrossRoads",
    (CROSSROADS, OBSERVATORY): "Obsv_Etr",
    (OBSERVATORY, CROSSROADS): "Obsv_Etr",
}
AUTHORED_MAPS = frozenset({CROSSROADS, DEAD_BRIDGE, CISTERN, GOLD_NEXUS, OBSERVATORY, EXCHANGE})
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
