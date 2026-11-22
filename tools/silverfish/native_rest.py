"""Rest the listen host at the verified Observatory stock checkpoint."""

import math
import time

from . import party
from .native_blue import BED_IDS
from .travel import OBSERVATORY


LEVEL = OBSERVATORY.rsplit("/", 1)[1]
BED = BED_IDS[OBSERVATORY]
ACTOR = f"SavePoint_BP_C {OBSERVATORY}.{LEVEL}:PersistentLevel.SavePoint_BP_C_3"

def site(map_name: str) -> tuple[str, str, str] | None:
    bed = BED_IDS.get(map_name)
    if not bed:
        return None
    level = map_name.rsplit("/", 1)[1]
    actor = f"SavePoint_BP_C {map_name}.{level}:PersistentLevel.{bed.split('-', 1)[0]}"
    return level, bed, actor


async def checked(host, bridge, action: str, *arguments: str) -> dict:
    area = host.last_game.get("map")
    checkpoint = site(area)
    pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    if (not host.native_zones or host.native_profile is None or not pawn
            or host.last_game.get("state") != "hosting" or area not in host.allowed_maps or not checkpoint):
        raise party.RuleError("native_rest_unavailable")
    level, bed, actor = checkpoint
    snapshot = await bridge.command(action, pawn, actor, *arguments, timeout=5)
    proof = snapshot.get("native_rest", {})
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
            or (proof.get("map"), proof.get("pawn"), proof.get("actor"), proof.get("bed"), proof.get("level"))
            != (area, pawn, actor, bed, level)
            or len(proof.get("location", ())) != 3 or any(axis is None for axis in proof["location"])):
        raise RuntimeError("The exact stock rest was not verified")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def perform(host, bridge, operation_id: str, hours: int, *, insomnia: bool = False) -> dict:
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if type(hours) is not int or not 0 <= hours <= 10:
        raise party.RuleError("Choose a whole number of rest hours from zero through ten")
    if (host.stopping or host.transitioning or host.item_travel_pending() or host.native_checkpoint_unsafe
            or host.party["wipe"] or host.party["pending_spawns"] or host.party["players"]["host"]["downed"]):
        raise party.RuleError("native_rest_unavailable")
    area = host.last_game.get("map")
    checkpoint = site(area)
    if not checkpoint:
        raise party.RuleError("native_rest_unavailable")
    fingerprint = ["native-rest", checkpoint[2], hours]
    if insomnia:
        fingerprint.append("insomnia")
    for receipt in host.party["receipts"].get("host", []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    inspected = await checked(host, bridge, "native-rest-inspect")
    if insomnia and (hours == 0 or inspected["reason"] != "insomnia_requires_stock_sleep_selection"):
        raise party.RuleError("native_rest_insomnia_selection_unavailable")
    if not insomnia and not inspected["eligible"]:
        raise party.RuleError(inspected["reason"] or "native_rest_denied")

    host.native_checkpoint_unsafe = True
    try:
        selection = (str(hours), "insomnia") if insomnia else (str(hours),)
        proof = await checked(host, bridge, "native-rest", *selection)
        before, after = proof.get("before"), proof.get("after")
        if (proof.get("hours") != hours or before is None or after is None or insomnia and proof.get("insomnia") is not True
                or not math.isclose(after - before, hours * 3600, rel_tol=0, abs_tol=.25)):
            raise RuntimeError("The rest did not advance the exact stock game clock")
        if host.world_flush_supported:
            await host.flush_native_world(bridge)
        candidate = party.load_party(host.party)
        candidate["checkpoint"] = {"map": area, "location": list(proof["location"])}
        result = {"native_rest": proof}
        receipts = candidate["receipts"].setdefault("host", [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
    host.control.event("native_rest_checkpoint", revision=revision, operation_id=operation_id, **proof)
    return result
