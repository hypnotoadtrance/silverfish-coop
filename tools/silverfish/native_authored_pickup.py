"""Commit the qualified Observatory circuit board to one stock inventory."""

import asyncio
import time

from . import native_host_save, party
from .native_gold import valid_guid
from .travel import OBSERVATORY


SOURCE = "observatory-comp-board"
ACTOR = f"Item_CompBoard_C {OBSERVATORY}.Observitory_P:PersistentLevel.Item_CompBoard_C_2"
ITEM_CLASS = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
ACTION = "native-authored-pickup-claim"
STATE_KEY = "native_authored_pickups"


async def project(service, bridge, pickups: dict) -> None:
    game = service.last_game
    if game.get("map") != OBSERVATORY:
        return
    desired = "claimed" if SOURCE in pickups else "available"
    if game.get("native_authored_pickup", {}).get("state") != "available" or desired != "claimed":
        return
    snapshot = await bridge.command("native-authored-pickup-project", desired, timeout=5)
    if snapshot.get("native_authored_pickup", {}).get("state") != desired:
        raise RuntimeError("The game did not verify the consumed Observatory pickup projection")
    service.last_game = snapshot


async def claim(host, bridge, character: str, target: str | None, operation_id: str) -> dict:
    if target != SOURCE or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_authored_pickup_request")
    if (not host.native_zones or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != OBSERVATORY or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_authored_pickup_unavailable")
    fingerprint = [ACTION, SOURCE, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if SOURCE in host.party.get(STATE_KEY, {}):
        raise party.RuleError("native_authored_pickup_already_claimed")
    if (host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    if character == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(character) or {}
        pawn = member.get("pawn") if member.get("connected") else None
    if not pawn:
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    await host.checkpoint_native(bridge)
    slot = native_host_save.new_slot()
    host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(ACTION, pawn, slot, timeout=7)
        report = snapshot.get("native_authored_pickup_claim", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != OBSERVATORY
                or tuple(report.get(key) for key in ("source", "map", "pawn", "slot")) != (SOURCE, OBSERVATORY, pawn, slot)):
            raise RuntimeError("The authored pickup proof belongs to a different pawn or generation")
        if not report["ok"] and not report["applied"]:
            host.native_checkpoint_unsafe = False
            raise party.RuleError(report["status"])
        if not report["ok"] or not report["applied"] or report["status"] != "claimed" or not valid_guid(report["guid"]):
            raise RuntimeError(f"The authored pickup did not verify ({report['status']})")
        await asyncio.to_thread(native_host_save.record, host.native_profile, slot, OBSERVATORY, flush=True)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        row = {"source": SOURCE, "map": OBSERVATORY, "actor": ACTOR, "class_path": ITEM_CLASS, "guid": report["guid"],
               "owner": character}
        result = {"native_authored_pickup_claim": row}
        candidate = party.load_party(host.party)
        candidate.setdefault(STATE_KEY, {})[SOURCE] = row
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
    host.control.event("native_authored_pickup_committed", revision=revision, **row)
    return result
