"""Consume an exact supported item from the authenticated guest's own save."""

import asyncio
import math
import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-bandage"
BANDAGE_CLASS = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
MRE = "native-guest-mre"
MRE_CLASS = "/Game/Classes/Items/Item_MRE.Item_MRE_C"
COMMANDS = {BANDAGE_CLASS: ACTION, MRE_CLASS: MRE}
RESULT = {ACTION: "native_guest_bandage", MRE: "native_guest_mre"}


def parse_mre(fields: list[str]) -> dict:
    if len(fields) != 17:
        raise ValueError("Invalid stock MRE report size")
    (tag, status, area, pawn, guid, initial, remaining, food_before, food_after,
     regen_before, regen_after, hp, maximum, intox_before, intox_after, host_intox, hemophilia) = fields
    if ((tag, status) != (MRE, "used") or not valid_guid(guid) or len(pawn) > 512
            or not pawn.startswith("Player_FP_C " + area + ".") or hemophilia not in ("0", "1")):
        raise ValueError("Invalid stock MRE identity")
    count, after = int(initial), int(remaining)
    food, restored, regen, buffered, health, max_health = map(float, (food_before, food_after, regen_before, regen_after, hp, maximum))
    intox_start, intox_end, shared_intox = map(int, (intox_before, intox_after, host_intox))
    trait = hemophilia == "1"
    if (not 1 <= count <= (2**31 - 1) // 25 or after != count - 1
            or not all(math.isfinite(value) for value in (food, restored, regen, buffered, health, max_health))
            or not 25 <= max_health <= 10_000 or not 0 < health <= max_health
            or not 0 <= food <= 200 or restored != min(food + 30, 200)
            or not 0 <= regen <= max_health or buffered != min(max(regen + (6 if trait else 10), 25), max_health)
            or not 0 <= intox_start <= 100 or intox_end != max(intox_start - 3, 0) or not 0 <= shared_intox <= 100):
        raise ValueError("Invalid stock MRE effect proof")
    return {"state": status, "map": area, "pawn": pawn, "guid": guid, "class_path": MRE_CLASS,
            "quantity_before": count, "quantity_after": after, "food_before": food, "food_after": restored,
            "regen_before": regen, "regen_after": buffered, "health_before": health, "health_after": health,
            "max_health": max_health, "hemophilia": trait, "intox_before": intox_start, "intox_after": intox_end,
            "host_intox_before": shared_intox, "host_intox_after": shared_intox}


async def use(host, bridge, character: str, guid: str, operation_id: str, *, action: str = ACTION) -> dict:
    if action not in RESULT:
        raise party.RuleError("native_guest_consumable_unavailable")
    is_mre = action == MRE
    kind = "mre" if is_mre else "bandage"
    key = RESULT[action]
    if not valid_guid(guid):
        raise party.RuleError(f"invalid_{kind}_guid")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if (not host.native_guest or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or time.monotonic() - host.last_seen > 3 or is_mre and not host.guest_mre):
        raise party.RuleError(f"native_guest_{kind}_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner["connected"] or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [action, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError(f"guest_{kind}_refused_downed_or_respawning")
    area, pawn = host.last_game["map"], owner["pawn"]
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, guid, timeout=6)
        except RuntimeError as error:
            if str(error).startswith(f"guest_{kind}_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(key, {})
        before, after, hp, regen, maximum = (proof.get(key) for key in (
            "quantity_before", "quantity_after", "health_before", "regen_after", "max_health"))
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or tuple(proof.get(key) for key in ("state", "map", "pawn", "guid")) != ("used", area, pawn, guid)
                or type(before) is not int or type(after) is not int or after < 0 or before - after != 1
                or any(type(value) not in (int, float) for value in (hp, regen, maximum, proof.get("regen_before")))
                or not 0 < hp <= maximum or not 25 <= regen <= maximum or not 0 <= proof["regen_before"] <= maximum
                or proof.get("health_after") != hp or type(proof.get("hemophilia")) is not bool):
            raise RuntimeError("The native consumable did not verify its exact quantity and stock regeneration")
        if is_mre and (proof.get("class_path") != MRE_CLASS
                       or not {"food_before", "food_after", "intox_before", "intox_after"}.issubset(proof)):
            raise RuntimeError("The native MRE did not verify its detached food and intoxication effects")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        if not is_mre or after == 0:
            candidate["players"][character].get("native_inventory", {}).pop(guid, None)
        result = {key: proof}
        recent = candidate["receipts"].setdefault(character, [])
        recent.append({"id": operation_id, "request": fingerprint, "result": result})
        del recent[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    if not is_mre or after == 0:
        host.native_pickup.forget_committed_row(character, guid)
    host.native_checkpoint_unsafe = False
    details = {"food": proof["food_after"], "intoxication": proof["intox_after"]} if is_mre else {}
    host.control.event(f"{key}_committed", character_id=character, operation_id=operation_id,
                       guid=guid, revision=revision, quantity=after, regen=regen, **details)
    return result
