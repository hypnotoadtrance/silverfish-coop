"""Equip exact guest-owned stock armor and helmets in the detached save."""

import asyncio
from dataclasses import dataclass
import math
import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-armor-equip"
HELMET_ACTION = "native-guest-helmet-equip"
FIXTURE = "native-guest-armor-fixture"
CLASS_PATH = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
HAZOP_CLASS_PATH = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
DUST_FILTER_PATH = "/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"
_APPLIED = {"equipped", "swapped", "reapplied", "already_applied"}


@dataclass(frozen=True)
class BodyArmor:
    class_path: str
    weight: float
    minimum: float


PROFILES = {"bomber": BodyArmor(CLASS_PATH, 0.5, 25.0), "hazop": BodyArmor(HAZOP_CLASS_PATH, 2.0, 32.0)}
CLASS_PATHS = frozenset(profile.class_path for profile in PROFILES.values())
EQUIP_ACTIONS = {**dict.fromkeys(CLASS_PATHS, ACTION), DUST_FILTER_PATH: HELMET_ACTION}
HEAD_PROFILES = {"dust-filter": DUST_FILTER_PATH}


def parse(fields):
    head = fields[0] in ("native-guest-helmet", "native-guest-helmet-projection")
    if (head and len(fields) != 21 or not head and len(fields) not in (14, 17)):
        raise ValueError("Invalid stock worn-gear report bounds")
    proof = {
        "map": fields[1], "pawn": fields[2], "guid": fields[3], "action": fields[4],
        "ok": fields[5] == "1", "status": fields[6],
        "before": int(fields[7]), "after": int(fields[8]),
        **{key: float(value) for key, value in zip(
            ("carry_before", "carry_after", "minimum", "slash", "acid"), fields[9:14]
        )},
    }
    if len(fields) >= 17:
        paths = HEAD_PROFILES if head else {kind: profile.class_path for kind, profile in PROFILES.items()}
        profile, previous, guid = fields[14:17]
        if (profile not in (*paths, "-") or profile == "-" and proof["ok"]
                or previous not in (*paths, "-") or not (guid == "-" if previous == "-" else valid_guid(guid))):
            raise ValueError("Invalid exact stock armor profile receipt")
        proof.update(profile=None if profile == "-" else profile, replaced_profile=None if previous == "-" else previous,
                     replaced_guid=None if guid == "-" else guid)
        if proof["profile"]:
            proof["class_path"] = paths[profile]
        if proof["replaced_profile"]:
            proof["replaced_class_path"] = paths[previous]
    if head:
        torso, slash, acid, poison = fields[17:]
        if (torso not in (*PROFILES, "-") or proof["action"] not in ("equip", "project")
                or fields[5] not in ("0", "1") or not valid_guid(proof["guid"])
                or not proof["pawn"].startswith("Player_FP_C " + proof["map"] + ".")):
            raise ValueError("Invalid stock helmet identity")
        proof.update(torso_profile=None if torso == "-" else torso, combined_slash=float(slash),
                     combined_acid=float(acid), combined_poison=float(poison))
        if proof["ok"] and not helmet_effects(proof):
            raise ValueError("Invalid combined stock armor and helmet stats")
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


def helmet_effects(proof):
    if (proof.get("profile"), proof.get("class_path"), proof.get("replaced_guid"), proof.get("replaced_profile")) != (
            "dust-filter", DUST_FILTER_PATH, None, None):
        return False
    status = proof.get("status")
    if status not in ("equipped", "reapplied", "already_applied"):
        return False
    torso = proof.get("torso_profile")
    if torso is not None and torso not in PROFILES:
        return False
    values = tuple(proof.get(key) for key in ("carry_before", "carry_after", "minimum", "slash", "acid",
                                             "combined_slash", "combined_acid", "combined_poison"))
    if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values):
        return False
    carry_before, carry_after, minimum, slash, acid, combined_slash, combined_acid, combined_poison = values
    body_resist = 0.2 if torso else 0.0
    return (math.isclose(carry_before - carry_after, 0.2 if status == "equipped" else 0, abs_tol=1e-6)
            and (minimum, slash, acid) == (PROFILES[torso].minimum if torso else 20.0, 0.1, 0.2)
            and math.isclose(combined_slash, body_resist + slash, abs_tol=1e-9)
            and math.isclose(combined_acid, body_resist + acid, abs_tol=1e-9)
            and math.isclose(combined_poison, 0.6 if torso == "hazop" else 0.5, abs_tol=1e-9))


async def perform(host, bridge, character, operation_id, action, guid):
    seed = action == FIXTURE
    head = action == HELMET_ACTION
    key = "native_guest_helmet" if head else "native_guest_armor"
    if (action not in (ACTION, FIXTURE, HELMET_ACTION) or not host.native_guest or not host.native_zones or host.native_profile is None
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
        proof = snapshot.get(key, {})
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
        verified = helmet_effects(proof) if head else effects(proof)
        if (type(before) is not int or type(after) is not int or not 0 <= before <= 4096
                or not 0 <= after <= 4096 or after != before + delta
                or (status != "seeded" or selected != "bomber" if seed else (status not in _APPLIED or not verified))):
            raise RuntimeError("The typed stock armor slot and derived stats did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: dict(proof, class_path=HEAD_PROFILES[selected] if head else PROFILES[selected].class_path)}
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
