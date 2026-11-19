"""Scope exact stock weapon operations to a connected guest's native save."""

import re
import time

from . import party

GUEST_ACTIONS = ("native-guest-weapon-inspect", "native-guest-weapon-equip", "native-guest-weapon-fire")
GUID = re.compile(r"[0-9A-F]{32}\Z")
READBACK_STATES = {"backpack", "equipped", "stored_equipment"}


async def perform(host, bridge, character: str, operation_id: str, action: str, guid: str) -> dict:
    if (not host.native_guest or not host.fixtures or host.native_profile is None
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen > 3 or host.last_game.get("state") != "hosting"):
        raise party.RuleError("native_guest_weapon_unavailable")
    if not isinstance(guid, str) or not GUID.fullmatch(guid):
        raise party.RuleError("invalid_weapon_guid")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")

    scope = host.native_recipients().get(character)
    if not scope or not scope["connected"]:
        raise party.RuleError("guest_not_active")
    operation = action.removeprefix("native-guest-weapon-")
    fingerprint = [action, guid, None]
    mutates = operation != "inspect"
    if mutates:
        for receipt in host.party["receipts"].get(character, []):
            if receipt["id"] == operation_id:
                if receipt["request"] != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                return receipt["result"]
        if host.party["players"].get(character, {}).get("downed") or host.party["wipe"] is not None:
            raise party.RuleError("downed")
        if character in host.party["pending_spawns"]:
            raise party.RuleError("respawn_in_progress")

    source, pawn = host.last_game["map"], scope["pawn"]
    if mutates:
        host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(action, pawn, guid, timeout=5)
        proof = snapshot.get("native_guest_weapon", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != source
                or (proof.get("map"), proof.get("action"), proof.get("pawn"), proof.get("guid"))
                != (source, operation, pawn, guid)):
            raise RuntimeError("Native guest weapon response did not match the authenticated pawn")
        if proof.get("ok") is not True:
            reason = proof.get("status") or "native_guest_weapon_denied"
            if mutates and reason.startswith("mutation_unverified_"):
                raise RuntimeError(reason)
            if mutates:
                host.native_checkpoint_unsafe = False
            raise party.RuleError(reason)

        actor, stored = proof.get("actor_ammo"), proof.get("stored_ammo")
        if (proof.get("status") not in READBACK_STATES or type(stored) is not int or stored < 0
                or type(actor) is not int or actor != (stored if proof["status"] == "equipped" else -1)):
            raise RuntimeError("Native guest stock weapon ammunition did not verify")
        if operation == "fire" and (proof["status"] != "equipped" or type(proof.get("before")) is not int
                                    or proof.get("after") != stored or proof["before"] - stored != 1
                                    or proof.get("projectiles") != 1):
            raise RuntimeError("The stock shot did not produce one verified guest-owned projectile and ammo debit")

        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {"native_guest_weapon": proof}
        if mutates:
            candidate = party.load_party(host.party)
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": operation_id, "request": fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
            await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
