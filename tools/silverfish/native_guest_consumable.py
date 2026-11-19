"""Consume an exact stock Bandage from the authenticated guest's own save."""

import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-bandage"


async def use(host, bridge, character: str, guid: str, operation_id: str) -> dict:
    if not valid_guid(guid):
        raise party.RuleError("invalid_bandage_guid")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if (not host.native_guest or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_bandage_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner["connected"] or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [ACTION, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] is not None or character in host.party["pending_spawns"]:
        raise party.RuleError("guest_bandage_refused_downed_or_respawning")
    area, pawn = host.last_game["map"], owner["pawn"]
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(ACTION, pawn, guid, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("guest_bandage_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get("native_guest_bandage", {})
        before, after, hp, regen, maximum = (proof.get(key) for key in (
            "quantity_before", "quantity_after", "health_before", "regen_after", "max_health"))
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or tuple(proof.get(key) for key in ("state", "map", "pawn", "guid")) != ("used", area, pawn, guid)
                or type(before) is not int or type(after) is not int or after < 0 or before - after != 1
                or any(type(value) not in (int, float) for value in (hp, regen, maximum, proof.get("regen_before")))
                or not 0 < hp <= maximum or not 25 <= regen <= maximum or not 0 <= proof["regen_before"] <= maximum
                or proof.get("health_after") != hp or type(proof.get("hemophilia")) is not bool):
            raise RuntimeError("The native Bandage did not verify its exact consumption and stock regeneration")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        candidate["players"][character].get("native_inventory", {}).pop(guid, None)
        result = {"native_guest_bandage": proof}
        recent = candidate["receipts"].setdefault(character, [])
        recent.append({"id": operation_id, "request": fingerprint, "result": result})
        del recent[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    host.native_pickup.forget_committed_row(character, guid)
    host.native_checkpoint_unsafe = False
    host.control.event("native_guest_bandage_committed", character_id=character, operation_id=operation_id,
                       guid=guid, revision=revision, quantity=after, regen=regen)
    return result
