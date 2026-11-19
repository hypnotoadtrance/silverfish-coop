"""Equip exact guest-owned stock body armor in the detached save."""

import asyncio
from dataclasses import dataclass
import math
import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-armor-equip"
FIXTURE = "native-guest-armor-fixture"
CLASS_PATH = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
HAZOP_CLASS_PATH = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
_APPLIED = {"equipped", "swapped", "reapplied", "already_applied"}


@dataclass(frozen=True)
class BodyArmor:
    class_path: str
    weight: float
    minimum: float


PROFILES = {"bomber": BodyArmor(CLASS_PATH, 0.5, 25.0), "hazop": BodyArmor(HAZOP_CLASS_PATH, 2.0, 32.0)}
CLASS_PATHS = frozenset(profile.class_path for profile in PROFILES.values())


def parse(fields):
    proof = {
        "map": fields[1], "pawn": fields[2], "guid": fields[3], "action": fields[4],
        "ok": fields[5] == "1", "status": fields[6],
        "before": int(fields[7]), "after": int(fields[8]),
        **{key: float(value) for key, value in zip(
            ("carry_before", "carry_after", "minimum", "slash", "acid"), fields[9:14]
        )},
    }
    if len(fields) == 17:
        profile, previous, guid = fields[14:]
        if (profile not in (*PROFILES, "-") or profile == "-" and proof["ok"]
                or previous not in (*PROFILES, "-") or not (guid == "-" if previous == "-" else valid_guid(guid))):
            raise ValueError("Invalid exact stock armor profile receipt")
        proof.update(profile=None if profile == "-" else profile, replaced_profile=None if previous == "-" else previous,
                     replaced_guid=None if guid == "-" else guid)
        if proof["profile"]:
            proof["class_path"] = PROFILES[profile].class_path
        if proof["replaced_profile"]:
            proof["replaced_class_path"] = PROFILES[previous].class_path
    return proof


def effects(proof):
    carry_before, carry_after, minimum, slash, acid = (
        proof.get(key) for key in ("carry_before", "carry_after", "minimum", "slash", "acid")
    )
    numbers = (carry_before, carry_after, minimum, slash, acid)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in numbers):
        return False
    selected, previous = proof.get("profile", "bomber"), proof.get("replaced_profile")
    profile = PROFILES.get(selected)
    if not profile or proof.get("class_path", profile.class_path) != profile.class_path:
        return False
    status = proof.get("status")
    if status == "swapped":
        prior = PROFILES.get(previous)
        if (not prior or previous == selected or not valid_guid(proof.get("replaced_guid"))
                or proof.get("replaced_guid") == proof.get("guid")
                or proof.get("replaced_class_path", prior.class_path) != prior.class_path):
            return False
        debit = (profile.weight - prior.weight) / 2
    else:
        if previous is not None or proof.get("replaced_guid") is not None or status not in _APPLIED:
            return False
        debit = profile.weight / 2 if status == "equipped" else 0.0
    return (carry_before >= 0 and carry_after >= 0
            and math.isclose(carry_before - carry_after, debit, abs_tol=1e-6)
            and (minimum, slash, acid) == (profile.minimum, 0.2, 0.2))


async def perform(host, bridge, character, operation_id, action, guid):
    seed = action == FIXTURE
    if (action not in (ACTION, FIXTURE) or not host.native_guest or not host.native_zones or host.native_profile is None
            or (seed and not host.fixtures) or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_armor_unavailable")
    if not valid_guid(guid):
        raise party.RuleError("native_guest_armor_guid_required")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    scope = host.native_recipients().get(character)
    if not scope or not scope.get("connected") or not scope.get("pawn"):
        raise party.RuleError("guest_not_active")
    fingerprint = [action, guid]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if host.party["players"].get(character, {}).get("downed") or host.party["wipe"]:
        raise party.RuleError("downed")
    if character in host.party["pending_spawns"]:
        raise party.RuleError("respawn_in_progress")

    source, pawn = host.last_game["map"], scope["pawn"]
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, guid, timeout=5)
        except RuntimeError as error:
            if str(error) in {"native_guest_armor_exact_context_required", "native_guest_armor_exact_owner_required",
                              "native_guest_armor_unavailable"}:
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get("native_guest_armor", {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", source)
                or (proof.get("map"), proof.get("pawn"), proof.get("guid"), proof.get("action"))
                != (source, pawn, guid, "seed" if seed else "equip")):
            raise RuntimeError("The stock armor action did not verify the exact authenticated guest")
        if proof.get("ok") is not True:
            reason = proof.get("status")
            if not isinstance(reason, str) or not reason.startswith("native_guest_armor_"):
                raise RuntimeError(reason or "The stock armor mutation did not verify")
            host.native_checkpoint_unsafe = False
            raise party.RuleError(reason)

        status = proof.get("status")
        before, after = proof.get("before"), proof.get("after")
        selected = proof.get("profile", "bomber")
        delta = 1 if seed else -1 if status == "equipped" else 0
        if (type(before) is not int or type(after) is not int or not 0 <= before <= 4096
                or not 0 <= after <= 4096 or after != before + delta
                or (status != "seeded" or selected != "bomber" if seed else (status not in _APPLIED or not effects(proof)))):
            raise RuntimeError("The typed stock armor slot and derived stats did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {"native_guest_armor": dict(proof, class_path=PROFILES[selected].class_path)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, ValueError, RuntimeError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
