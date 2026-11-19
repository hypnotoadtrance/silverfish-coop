"""Verify a stock Old Pipe animation or one exact NPC hit in a headless co-op."""

import math
import time

from . import native_authored_pickup, party
from .native_gold import valid_guid
from .travel import CROSSROADS, DEAD_BRIDGE


EQUIP = "native-guest-melee-canary-equip"
CANARY = "native-guest-melee-canary"
DAMAGE = "native-guest-melee-damage-canary"
EQUIP_KEY = "native_guest_melee_equip"
CANARY_KEY = "native_guest_melee_canary"
TARGET = f"NPC_State_Infantry_CQC_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"


def verified_damage(proof: dict) -> bool:
    values = tuple(proof.get(key) for key in ("enemy_health_before", "enemy_health_after", "stock_damage"))
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        return False
    before, after, amount = values
    counters = proof.get("sweeps"), proof.get("damage_calls")
    return ((proof.get("map"), proof.get("target")) == (DEAD_BRIDGE, TARGET)
            and all(type(counter) is int and counter == 1 for counter in counters)
            and 100 <= before <= 160 and 0 < after < before and 0 < amount <= 90
            and math.isclose(before - after, amount, abs_tol=1e-6))


def parse(fields: list[str]) -> tuple[str, dict] | None:
    if not fields:
        return None
    if fields[0] == "native-guest-melee-equip" and len(fields) == 6 and fields[4] in ("0", "1"):
        return EQUIP_KEY, dict(zip(("map", "pawn", "guid"), fields[1:4]), ok=fields[4] == "1", status=fields[5])
    if fields[0] == CANARY and len(fields) in (12, 18) and fields[10] in ("0", "1") and fields[11] in ("0", "1"):
        proof = dict(zip(("map", "pawn", "guid"), fields[1:4]), serial=int(fields[4]), status=fields[5],
                     presses=int(fields[6]), releases=int(fields[7]), contacts=int(fields[8]),
                     qualified=int(fields[9]), settled=fields[10] == "1", passive_free=fields[11] == "1")
        if len(fields) == 18:
            proof.update(target=fields[12], sweeps=int(fields[13]), damage_calls=int(fields[14]),
                         enemy_health_before=float(fields[15]), enemy_health_after=float(fields[16]), stock_damage=float(fields[17]))
            if (proof["map"] != DEAD_BRIDGE or proof["target"] != TARGET or proof["status"] == "surface_verified"
                    or proof["settled"] != (proof["status"] == "stock_damage_verified")
                    or not 0 <= proof["damage_calls"] <= proof["sweeps"] <= 1
                    or (proof["settled"] and not verified_damage(proof))):
                return None
        elif proof["map"] != CROSSROADS or proof["status"] == "stock_damage_verified":
            return None
        return CANARY_KEY, proof
    return None


async def perform(host, bridge, action: str, character: str, guid: str, operation_id: str) -> dict:
    area = host.last_game.get("map")
    allowed = {DAMAGE: (DEAD_BRIDGE,), CANARY: (CROSSROADS,), EQUIP: (CROSSROADS, DEAD_BRIDGE)}
    if (not host.native_guest or not host.fixtures or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or area not in allowed.get(action, ()) or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_melee_fixture_unavailable")
    if (not isinstance(character, str) or not valid_guid(guid) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_guest_melee_exact_guid_required")
    fingerprint = [action, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    owner = host.native_recipients().get(character) or {}
    pawn = owner.get("pawn") if owner.get("connected") else None
    if (not pawn or host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_guest_melee_exact_owner_required")
    claimed = host.party.get(native_authored_pickup.STATE_KEY, {}).get(native_authored_pickup.OLD_PIPE, {})
    if (claimed.get("owner"), claimed.get("guid")) != (character, guid):
        raise party.RuleError("native_guest_melee_exact_authored_claim_required")
    if area == DEAD_BRIDGE and tuple(claimed.get(key) for key in ("source", "map", "actor", "class_path")) != (
            native_authored_pickup.OLD_PIPE, *native_authored_pickup.SOURCES[native_authored_pickup.OLD_PIPE]):
        raise party.RuleError("native_guest_melee_exact_authored_claim_required")

    def exact(snapshot: dict, key: str) -> dict:
        proof = snapshot.get(key, {})
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid")) == (
                "hosting", area, area, pawn, guid):
            return proof
        return {}

    key = EQUIP_KEY if action == EQUIP else CANARY_KEY
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action if action == EQUIP else action + "-start", pawn, guid, timeout=5)
        except RuntimeError as error:
            if str(error).startswith("native_guest_melee_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = exact(snapshot, key)
        if not proof:
            raise RuntimeError("The Old Pipe receipt does not match the exact guest and GUID")
        if action == EQUIP:
            if proof.get("ok") is not True:
                status = proof.get("status", "")
                if not status or status.startswith("mutation_unverified_"):
                    raise RuntimeError(status or "The stock Old Pipe equip could not be verified")
                host.native_checkpoint_unsafe = False
                raise party.RuleError(status)
            if proof.get("status") != "old_pipe_equipped":
                raise RuntimeError("The exact stock Old Pipe was not equipped")
        else:
            serial = proof.get("serial")
            if type(serial) is not int or serial < 1:
                raise RuntimeError("The stock Old Pipe animation did not report a bound serial")
            if proof.get("status") == "pending":
                def terminal(frame):
                    current = exact(frame, key)
                    return current.get("serial") == serial and current.get("status") != "pending"

                snapshot = await bridge.wait_for(terminal, timeout=5)
                proof = exact(snapshot, key)
            if action == DAMAGE and proof.get("status") == "stock_damage_aim_fenced":
                host.native_checkpoint_unsafe = False
                raise party.RuleError("native_guest_melee_stock_damage_contact_aim_changed")
            expected = "stock_damage_verified" if action == DAMAGE else "surface_verified"
            if (proof.get("status"), proof.get("presses"), proof.get("releases"), proof.get("contacts"),
                    proof.get("qualified"), proof.get("settled"), proof.get("passive_free")) != (
                    expected, 1, 1, 1, 1, True, True) or (action == DAMAGE and not verified_damage(proof)):
                raise RuntimeError(proof.get("status") or "The fenced stock animation did not settle")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: proof}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError, TimeoutError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
