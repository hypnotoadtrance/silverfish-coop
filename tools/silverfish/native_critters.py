"""Route stock live-critter pickups to the owning guest inventory."""

import re
import time

from . import native_inventory, native_loot, native_pickup_audio, native_stock_door, party
from .native_gold import valid_guid

ACTION = "native-stock-critter-pickup"
PROOF = "native_stock_critter_pickup"
ROW = "native-stock-critter-claim"
_TARGET = re.compile(r"stock-critter:([1-9][0-9]{0,4})")
_ITEM = re.compile(r"/Game/Classes/Items/(Item_[A-Za-z0-9]+)\.\1_C")


def target_id(target):
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    if match and int(match[1]) <= 65535:
        return match[1]
    return None


def parse(fields, phase, area):
    if len(fields) != 12 or fields[:2] != [ROW, area] or phase != "hosting":
        raise ValueError("Invalid stock critter receipt")
    _, _, number, pawn, actor, item, guid, status, inventory_guid, amount, before, after = fields
    if (target_id(f"stock-critter:{number}") is None or native_stock_door.actor_map(actor) != area
            or not _ITEM.fullmatch(item) or not valid_guid(guid) or status not in {"claimed", "merged"}):
        raise ValueError("Invalid stock critter receipt")
    result = {"map": area, "number": number, "pawn": pawn, "actor": actor, "class_path": item, "guid": guid}
    if status == "merged":
        result.update(native_loot.verified_stack_merge(
            {"guid": guid, "inventory_guid": inventory_guid, "amount": int(amount), "before": int(before), "after": int(after)},
            guid, int(amount),
        ))
    elif (inventory_guid, amount, before, after) != ("-", "0", "0", "0"):
        raise ValueError("Invalid unmerged critter receipt")
    return result


async def pickup(host, bridge, character, requested, operation):
    epoch, _, number = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    player = host.party["players"].get(character)
    member = host.native_recipients().get(character, {})
    if (not epoch or epoch != host.zone_epoch or target_id(f"stock-critter:{number}") is None
            or game.get("state") != "hosting" or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or not member.get("connected") or not player or player["downed"] or host.party["wipe"]
            or character in host.party["pending_spawns"] or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_critter_scope_unavailable")
    fingerprint = [ACTION, requested, None]
    receipts = host.party["receipts"].setdefault(character, [])
    for receipt in receipts:
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if not await native_inventory.retain_guest_save(host, bridge, character, member["pawn"]):
        raise party.RuleError("stock_critter_inventory_unavailable")
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(ACTION, number, member["pawn"], timeout=4)
        except RuntimeError as error:
            if str(error).startswith("stock_critter_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(PROOF, {})
        if (proof.get("map"), proof.get("number"), proof.get("pawn")) != (game["map"], number, member["pawn"]):
            raise RuntimeError("The stock critter pickup did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {PROOF: proof}
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.native_checkpoint_unsafe = False
        host.control.event("native_stock_critter_claimed", operation_id=operation, **proof)
        native_pickup_audio.committed(host, member["pawn"], operation, proof["class_path"])
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
