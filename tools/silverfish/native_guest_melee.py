"""Scope an authenticated guest's stock melee attacks and diagnostic fixtures."""

import math
import re
import time

from . import native_authored_pickup, native_inventory, party
from .enemy_loot_profiles import BY_ACTOR, BY_MAP
from .native_gold import valid_guid
from .travel import CROSSROADS, DEAD_BRIDGE


EQUIP = "native-guest-melee-canary-equip"
CANARY = "native-guest-melee-canary"
DAMAGE = "native-guest-melee-damage-canary"
SWING = "native-guest-melee-swing"
AIM_FIXTURE = "native-guest-melee-aim-fixture"
CONTACT_FIXTURE = "native-guest-melee-contact-fixture"
KIND = "old-pipe"
KINDS = (KIND, "melee")
EQUIP_KEY = "native_guest_melee_equip"
CANARY_KEY = "native_guest_melee_canary"
SWING_KEY = "native_guest_melee_swing"
AIM_KEY = "native_guest_melee_aim_fixture"
CONTACT_KEY = "native_guest_melee_contact_fixture"
TARGET = BY_MAP[DEAD_BRIDGE].actors[0]
CONTACT_STAGES = frozenset({"awaiting_contact", "camera_identity", "camera_vector_abi", "hit_result_abi",
                            "line_call_abi", "sphere_call_abi", "trace_parameters_abi", "line_contact", "line_too_near",
                            "sphere_empty", "sphere_bounds", "sphere_contact", "sphere_other_actor",
                            "target_health", "target_identity", "selected_contact"})


def registered_target(area: str, target: str) -> bool:
    profile = BY_ACTOR.get(target) if isinstance(target, str) else None
    return profile is not None and profile.map == area


def ordinary_map(area) -> bool:
    return isinstance(area, str) and re.fullmatch(r"/Game/Classes/Maps/(?:[\w-]+/)*[\w-]+", area, re.ASCII) is not None


def ordinary_target(area, target) -> bool:
    if not ordinary_map(area) or not isinstance(target, str) or len(target) > 512:
        return False
    level = area.rsplit("/", 1)[1]
    prefix = re.escape(f"{area}.{level}:PersistentLevel.")
    return re.fullmatch(rf"\w+ {prefix}\w+", target, re.ASCII) is not None


def verified_damage(proof: dict, *, fixture=False) -> bool:
    values = tuple(proof.get(key) for key in ("enemy_health_before", "enemy_health_after", "stock_damage"))
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        return False
    before, after, amount = values
    counters = proof.get("sweeps"), proof.get("damage_calls")
    eligible = registered_target if fixture else ordinary_target
    return (eligible(proof.get("map"), proof.get("target"))
            and all(type(counter) is int and counter == 1 for counter in counters)
            and before > 0 and after < before and amount > 0
            and (not fixture or 100 <= before <= 160 and after > 0 and amount <= 90)
            and (math.isclose(before - after, amount, rel_tol=0, abs_tol=1e-6)
                 or not fixture and after == 0 and amount >= before - 1e-6))


def parse(fields: list[str]) -> tuple[str, dict] | None:
    if not fields:
        return None
    if fields[0] == "native-guest-melee-equip" and len(fields) == 6 and fields[4] in ("0", "1"):
        return EQUIP_KEY, dict(zip(("map", "pawn", "guid"), fields[1:4]), ok=fields[4] == "1", status=fields[5])
    if fields[0] == AIM_FIXTURE and len(fields) == 5 and registered_target(fields[1], fields[4]) and valid_guid(fields[3]):
        return AIM_KEY, dict(zip(("map", "pawn", "guid", "target"), fields[1:]))
    if fields[0] == CONTACT_FIXTURE and len(fields) in (14, 15):
        area, pawn, guid, serial, target, stage, component = fields[1:8]
        camera_scope = fields[14] if len(fields) == 15 else None
        if (not registered_target(area, target) or not valid_guid(guid) or stage not in CONTACT_STAGES
                or camera_scope not in (None, "unchanged", "restored", "restore_unverified")
                or not pawn.startswith("Player_FP_C " + area + ".") or len(pawn) > 512
                or not component or len(component) > 512 or len(serial) > 16 or not serial.isascii() or not serial.isdecimal()):
            return None
        try:
            initial, current, selected, eye_drift, facing, target_drift = map(float, fields[8:14])
        except ValueError:
            return None
        if (not 1 <= int(serial) < 2**53 or not 100 <= initial <= 160 or not -1 <= current <= 1_000_000
                or not -1 <= selected <= 1_000_000 or not -1 <= eye_drift <= 1_000_000
                or not (facing == -2 or -1 <= facing <= 1) or not -1 <= target_drift <= 1_000_000):
            return None
        return CONTACT_KEY, dict(map=area, pawn=pawn, guid=guid, serial=int(serial), target=target, stage=stage,
                                 component=component, camera_scope=camera_scope, initial_health=initial,
                                 contact_health=None if current == -1 else current,
                                 selected_health=None if selected == -1 else selected,
                                 camera_displacement=None if eye_drift == -1 else eye_drift,
                                 camera_direction_dot=None if facing == -2 else facing,
                                 target_displacement=None if target_drift == -1 else target_drift)
    ordinary = fields[0] == SWING
    if (fields[0] in (CANARY, SWING) and len(fields) in ((18,) if ordinary else (12, 18))
            and fields[10] in ("0", "1") and fields[11] in ("0", "1")):
        proof = dict(zip(("map", "pawn", "guid"), fields[1:4]), serial=int(fields[4]), status=fields[5],
                     presses=int(fields[6]), releases=int(fields[7]), contacts=int(fields[8]),
                     qualified=int(fields[9]), settled=fields[10] == "1", passive_free=fields[11] == "1")
        if len(fields) == 18:
            proof.update(target=fields[12], sweeps=int(fields[13]), damage_calls=int(fields[14]),
                         enemy_health_before=float(fields[15]), enemy_health_after=float(fields[16]), stock_damage=float(fields[17]))
            miss = ordinary and proof["status"] == "stock_miss_verified"
            cancelled = ordinary and proof["status"] == "stock_swing_cancelled"
            target = proof["target"]
            if ordinary:
                eligible = ordinary_map(proof["map"]) and (target == "-" or ordinary_target(proof["map"], target))
            else:
                eligible = (proof["map"], target) == (DEAD_BRIDGE, TARGET)
            if (not eligible or proof["status"] == "surface_verified"
                    or proof["settled"] != (proof["status"] == "stock_damage_verified" or miss)
                    or not 0 <= proof["damage_calls"] <= proof["sweeps"] <= 1
                    or ((miss or cancelled) and not verified_miss(proof))
                    or (proof["settled"] and not miss and not verified_damage(proof, fixture=not ordinary))):
                return None
        elif proof["map"] != CROSSROADS or proof["status"] == "stock_damage_verified":
            return None
        return SWING_KEY if ordinary else CANARY_KEY, proof
    return None


def verified_miss(proof: dict) -> bool:
    return (ordinary_map(proof.get("map")) and proof.get("target") == "-"
            and tuple(proof.get(key) for key in ("sweeps", "damage_calls", "enemy_health_before", "enemy_health_after",
                                                "stock_damage")) == (0, 0, -1, -1, -1))


def is_equipped(equipment, area, guid):
    return (ordinary_map(area) and valid_guid(guid) and isinstance(equipment, dict)
            and equipment.get("kind") in KINDS and equipment.get("guid") == guid)


def request_target(guid: str, epoch: str) -> str:
    if not valid_guid(guid) or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
        raise party.RuleError("native_guest_melee_exact_epoch_and_guid_required")
    return f"{epoch}@{guid}"


def current_guid(target: str | None, epoch: str) -> str:
    observed, delimiter, guid = target.partition("@") if isinstance(target, str) else ("", "", "")
    if not delimiter or not epoch or observed != epoch or not valid_guid(guid):
        raise party.RuleError("native_guest_melee_exact_epoch_and_guid_required")
    return guid


async def perform(host, bridge, action: str, character: str, guid: str, operation_id: str, *, fixture_lane: int | None = None) -> dict:
    area = host.last_game.get("map")
    ordinary = action == SWING
    fixtures = {DAMAGE: (DEAD_BRIDGE,), CANARY: (CROSSROADS,), EQUIP: (CROSSROADS, DEAD_BRIDGE),
                AIM_FIXTURE: tuple(BY_MAP)}
    map_ok = ordinary_map(area) if ordinary else area in fixtures.get(action, ())
    if (not host.native_guest or (not ordinary and not host.fixtures) or (ordinary and not host.native_guest_controls)
            or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or not map_ok or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_melee_swing_unavailable" if ordinary else "native_guest_melee_fixture_unavailable")
    if (not isinstance(character, str) or not valid_guid(guid) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_guest_melee_exact_guid_required")
    if fixture_lane is not None and (action != AIM_FIXTURE or type(fixture_lane) is not int
                                     or not 1 <= fixture_lane <= len(BY_MAP[area].actors)):
        raise party.RuleError("native_guest_melee_fixture_registry_target_required")
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
    if ordinary or action == AIM_FIXTURE:
        equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
        if not is_equipped(equipped, area, guid):
            raise party.RuleError("native_guest_melee_exact_equipment_required")
        if ordinary and host.smg_holds is not None and host.smg_holds.strokes:
            raise party.RuleError("native_guest_melee_combat_busy")
    else:
        claimed = host.party.get(native_authored_pickup.STATE_KEY, {}).get(native_authored_pickup.OLD_PIPE, {})
        if ((claimed.get("owner"), claimed.get("guid")) != (character, guid)
                or area == DEAD_BRIDGE and tuple(claimed.get(key) for key in ("source", "map", "actor", "class_path")) != (
                    native_authored_pickup.OLD_PIPE, *native_authored_pickup.SOURCES[native_authored_pickup.OLD_PIPE])):
            raise party.RuleError("native_guest_melee_exact_authored_claim_required")

    def exact(snapshot: dict, key: str) -> dict:
        proof = snapshot.get(key, {})
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid")) == (
                "hosting", area, area, pawn, guid):
            return proof
        return {}

    if action == AIM_FIXTURE:
        target = BY_MAP[area].actors[(fixture_lane or 1) - 1]
        snapshot = await bridge.command(action, pawn, guid, target, timeout=5)
        proof = exact(snapshot, AIM_KEY)
        if not proof or proof.get("target") != target:
            raise RuntimeError("The staged stock Old Pipe aim directive did not match the guest")
        return {AIM_KEY: proof}

    key = SWING_KEY if ordinary else EQUIP_KEY if action == EQUIP else CANARY_KEY
    deferred = ordinary and await native_inventory.retain_guest_save(host, bridge, character, pawn)
    if ordinary and not deferred:
        raise party.RuleError("native_guest_melee_save_retention_unavailable")
    if not deferred:
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
            raise RuntimeError("The stock melee receipt does not match the exact guest and GUID")
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
                raise RuntimeError("The stock melee animation did not report a bound serial")
            if proof.get("status") == "pending":
                def terminal(frame):
                    current = exact(frame, key)
                    return current.get("serial") == serial and current.get("status") != "pending"

                snapshot = await bridge.wait_for(terminal, timeout=5)
                proof = exact(snapshot, key)
            if action == DAMAGE and proof.get("status") == "stock_damage_aim_fenced":
                host.native_checkpoint_unsafe = False
                raise party.RuleError("native_guest_melee_stock_damage_contact_aim_changed")
            if ordinary and proof.get("status") == "stock_swing_cancelled" and verified_miss(proof):
                host.native_checkpoint_unsafe = False
                raise party.RuleError("native_guest_melee_swing_cancelled")
            miss = ordinary and proof.get("status") == "stock_miss_verified"
            expected = "stock_miss_verified" if miss else "stock_damage_verified" if ordinary or action == DAMAGE else "surface_verified"
            if (proof.get("status"), proof.get("presses"), proof.get("releases"), proof.get("contacts"),
                    proof.get("qualified"), proof.get("settled"), proof.get("passive_free")) != (
                    expected, 1, 1, 1, 1, True, True) or (miss and not verified_miss(proof)) or (
                    (ordinary or action == DAMAGE) and not miss and not verified_damage(proof, fixture=not ordinary)) or (
                    action == DAMAGE and proof.get("target") != TARGET):
                raise RuntimeError(proof.get("status") or "The fenced stock animation did not settle")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: proof}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        if not deferred:
            await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError, TimeoutError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
