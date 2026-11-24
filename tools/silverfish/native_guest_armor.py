"""Equip exact guest-owned stock armor and helmets in the detached save."""

import asyncio
from dataclasses import dataclass
import math
import re
import time

from . import native_inventory, party
from .enemy_loot_profiles import BY_MAP
from .native_gold import valid_guid
from .travel import DEAD_BRIDGE


ACTION = "native-guest-armor-equip"
HELMET_ACTION = "native-guest-helmet-equip"
FIXTURE = "native-guest-armor-fixture"
DISGUISE_PROBE = "native-guest-disguise-probe-fixture"
CLASS_PATH = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
HAZOP_CLASS_PATH = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
HUNTER_JACKET_PATH = "/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C"
HAZARD_SUIT_PATH = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
DUST_FILTER_PATH = "/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"
BASIC_HELMET_PATH = "/Game/Classes/Armor_Items/Helm_Basic.Helm_Basic_C"
GOGGLES_PATH = "/Game/Classes/Armor_Items/Helm_Goggles.Helm_Goggles_C"
CONSCRIPT_UNIFORM_PATH = "/Game/Classes/Armor_Items/Armor_ConscriptUniform.Armor_ConscriptUniform_C"
STANDARD_INFANTRY_HELMET_PATH = "/Game/Classes/Armor_Items/Helm_StandardInf.Helm_StandardInf_C"
_APPLIED = {"equipped", "swapped", "reapplied", "already_applied"}


@dataclass(frozen=True)
class GearProfile:
    class_path: str
    weight: float
    slash: float
    acid: float
    poison: float
    minimum: float = 20.0
    voice_filter: bool = False


PROFILES = {
    "bomber": GearProfile(CLASS_PATH, 0.5, 0.2, 0.2, 0.0, 25.0),
    "hazop": GearProfile(HAZOP_CLASS_PATH, 2.0, 0.2, 0.2, 0.1, 32.0),
    "hunter-jacket": GearProfile(HUNTER_JACKET_PATH, 1.2, 0.1, 0.05, 0.0, 25.0),
    "hazard-suit": GearProfile(HAZARD_SUIT_PATH, 10.5, 0.3, 0.5, 0.1, 28.0),
    "conscript-uniform": GearProfile(CONSCRIPT_UNIFORM_PATH, 1.8, 0.15, 0.2, 0.1, 28.0),
}
HEAD_PROFILES = {
    "dust-filter": GearProfile(DUST_FILTER_PATH, 0.4, 0.1, 0.2, 0.5, voice_filter=True),
    "basic-helmet": GearProfile(BASIC_HELMET_PATH, 1.4, 0.1, 0.1, 0.0),
    "goggles": GearProfile(GOGGLES_PATH, 0.1, 0.0, 0.1, 0.0),
    "standard-infantry-helmet": GearProfile(STANDARD_INFANTRY_HELMET_PATH, 2.4, 0.1, 0.2, 0.8, voice_filter=True),
}
CLASS_PATHS = frozenset(profile.class_path for profile in PROFILES.values())
EQUIP_ACTIONS = {**dict.fromkeys(CLASS_PATHS, ACTION),
                 **dict.fromkeys((profile.class_path for profile in HEAD_PROFILES.values()), HELMET_ACTION)}
SEED_PROFILES = {"basic-helmet": HEAD_PROFILES["basic-helmet"], "goggles": HEAD_PROFILES["goggles"],
                 "dust-filter": HEAD_PROFILES["dust-filter"],
                 "hunter-jacket": PROFILES["hunter-jacket"], "hazard-suit": PROFILES["hazard-suit"],
                 "conscript-uniform": PROFILES["conscript-uniform"],
                 "standard-infantry-helmet": HEAD_PROFILES["standard-infantry-helmet"]}


_WORN_CLASS = re.compile(r"/Game/Classes/Armor_Items/(?:[A-Za-z0-9_]+/)*(?P<name>(?:Armor|Helm)_[A-Za-z0-9_]+)\.(?P=name)_C")


def equip_action(class_path):
    match = _WORN_CLASS.fullmatch(class_path) if isinstance(class_path, str) and len(class_path) <= 256 else None
    if match is None:
        return None
    return HELMET_ACTION if match["name"].startswith("Helm_") else ACTION


def _profile(token, named, *, head=False):
    # Runtime receipts are produced by the host's local Unreal bridge after it
    # qualifies the actual class and item row. Clients never provide these stats.
    if not isinstance(token, str) or len(token) > 512:
        return None
    if token in named:
        return named[token]
    parts = token.split(",")
    if (len(parts) != 7 or parts[6] not in ("0", "1")
            or equip_action(parts[0]) != (HELMET_ACTION if head else ACTION)):
        return None
    try:
        weight, slash, acid, poison, minimum = map(float, parts[1:6])
    except ValueError:
        return None
    voice_filter = parts[6] == "1"
    if (not all(math.isfinite(value) and value >= 0 for value in (weight, slash, acid, poison, minimum))
            or head and minimum != 20.0 or not head and voice_filter):
        return None
    return GearProfile(parts[0], weight, slash, acid, poison, minimum, voice_filter)


def parse(fields):
    head = fields[0] in ("native-guest-helmet", "native-guest-helmet-projection")
    if (head and len(fields) not in (21, 22) or not head and len(fields) not in (14, 17)):
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
        profiles = HEAD_PROFILES if head else PROFILES
        profile, previous, guid = fields[14:17]
        if not head and proof["action"] == "seed":
            fixture_profiles = {"bomber": PROFILES["bomber"], **SEED_PROFILES}
            selected, displaced = fixture_profiles.get(profile), fixture_profiles.get(previous)
        else:
            selected = _profile(profile, profiles, head=head)
            displaced = _profile(previous, profiles, head=head)
        if ((not selected and (profile != "-" or proof["ok"]))
                or (not displaced and previous != "-")
                or not (guid == "-" if previous == "-" else valid_guid(guid))):
            raise ValueError("Invalid exact stock armor profile receipt")
        proof.update(profile=None if profile == "-" else profile, replaced_profile=None if previous == "-" else previous,
                     replaced_guid=None if guid == "-" else guid)
        if selected:
            proof["class_path"] = selected.class_path
        if displaced:
            proof["replaced_class_path"] = displaced.class_path
    if head:
        torso, slash, acid, poison = fields[17:21]
        if ((torso != "-" and not _profile(torso, PROFILES)) or proof["action"] not in ("equip", "project")
                or fields[5] not in ("0", "1") or not valid_guid(proof["guid"])
                or not proof["pawn"].startswith("Player_FP_C " + proof["map"] + ".")):
            raise ValueError("Invalid stock helmet identity")
        proof.update(torso_profile=None if torso == "-" else torso, combined_slash=float(slash),
                     combined_acid=float(acid), combined_poison=float(poison))
        if len(fields) == 22:
            if fields[21] not in ("0", "1"):
                raise ValueError("Invalid stock helmet voice filter readback")
            proof["voice_filter"] = fields[21] == "1"
        if proof["ok"] and not helmet_effects(proof):
            raise ValueError("Invalid combined stock armor and helmet stats")
    return proof


def parse_recognition(fields):
    profile = BY_MAP[DEAD_BRIDGE]
    if (len(fields) != 12 or fields[1] != DEAD_BRIDGE or fields[3] not in profile.actors
            or not fields[2].startswith(f"Player_FP_C {profile.world}:PersistentLevel.")):
        raise ValueError("Invalid State disguise fixture identity")
    proof = {"map": fields[1], "pawn": fields[2], "npc": fields[3]}
    for role, offset in (("host", 4), ("guest", 8)):
        configured, compromised, recognized, relation = map(int, fields[offset:offset + 4])
        if not (0 <= configured < 15 and compromised in (0, 1) and 0 <= recognized < 15 and 0 <= relation <= 6):
            raise ValueError("Invalid stock disguise recognition")
        proof[role] = {"configured": configured, "compromised": bool(compromised),
                       "recognized": recognized, "relation": relation}
    return proof


def _weight_debit(proof, profile, profiles, *, head=False):
    previous = proof.get("replaced_profile")
    status = proof.get("status")
    if status == "swapped":
        prior = _profile(previous, profiles, head=head)
        if (not prior or not valid_guid(proof.get("replaced_guid"))
                or proof.get("replaced_guid") == proof.get("guid")
                or proof.get("replaced_class_path", prior.class_path) != prior.class_path):
            return None
        return (profile.weight - prior.weight) / 2
    if previous is not None or proof.get("replaced_guid") is not None or status not in _APPLIED:
        return None
    return profile.weight / 2 if status == "equipped" else 0.0


def effects(proof):
    values = tuple(proof.get(key) for key in ("carry_before", "carry_after", "minimum", "slash", "acid"))
    if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values):
        return False
    carry_before, carry_after, minimum, slash, acid = values
    selected = proof.get("profile", "bomber")
    profile = _profile(selected, PROFILES)
    if not profile or proof.get("class_path", profile.class_path) != profile.class_path:
        return False
    debit = _weight_debit(proof, profile, PROFILES)
    return (debit is not None and math.isclose(carry_before - carry_after, debit, abs_tol=1e-6)
            and (minimum, slash, acid) == (profile.minimum, profile.slash, profile.acid))


def helmet_effects(proof):
    selected = proof.get("profile")
    profile = _profile(selected, HEAD_PROFILES, head=True)
    torso = _profile(proof.get("torso_profile"), PROFILES)
    if (not profile or proof.get("class_path") != profile.class_path
            or (torso is None and proof.get("torso_profile") is not None)
            or proof.get("voice_filter", profile.voice_filter) is not profile.voice_filter
            or (proof.get("action") == "project" and proof.get("status") not in ("reapplied", "already_applied"))):
        return False
    values = tuple(proof.get(key) for key in ("carry_before", "carry_after", "minimum", "slash", "acid",
                                             "combined_slash", "combined_acid", "combined_poison"))
    if not all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values):
        return False
    carry_before, carry_after, minimum, slash, acid, combined_slash, combined_acid, combined_poison = values
    debit = _weight_debit(proof, profile, HEAD_PROFILES, head=True)
    return (debit is not None and math.isclose(carry_before - carry_after, debit, abs_tol=1e-6)
            and (minimum, slash, acid) == (torso.minimum if torso else profile.minimum, profile.slash, profile.acid)
            and math.isclose(combined_slash, (torso.slash if torso else 0) + slash, abs_tol=1e-9)
            and math.isclose(combined_acid, (torso.acid if torso else 0) + acid, abs_tol=1e-9)
            and math.isclose(combined_poison, (torso.poison if torso else 0) + profile.poison, abs_tol=1e-9))


async def perform(host, bridge, character, operation_id, action, guid, *, seed_kind=None):
    seed = action == FIXTURE
    head = action == HELMET_ACTION
    key = "native_guest_helmet" if head else "native_guest_armor"
    if (action not in (ACTION, FIXTURE, HELMET_ACTION) or not host.native_guest or not host.native_zones or host.native_profile is None
            or (seed and not host.fixtures) or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_armor_unavailable")
    if not valid_guid(guid):
        raise party.RuleError("native_guest_armor_guid_required")
    if seed_kind is not None and (not seed or not isinstance(seed_kind, str) or seed_kind not in SEED_PROFILES):
        raise party.RuleError("native_guest_armor_fixture_profile_unavailable")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    scope = host.native_recipients().get(character)
    if not scope or not scope.get("connected") or not scope.get("pawn"):
        raise party.RuleError("guest_not_active")
    extra = (seed_kind,) if seed_kind is not None else ()
    fingerprint = [action, guid, *extra]
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
    deferred = not seed and await native_inventory.retain_guest_save(host, bridge, character, pawn)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, guid, *extra, timeout=5)
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
        if seed:
            expected = seed_kind or "bomber"
            profiles = SEED_PROFILES if seed_kind else PROFILES
            profile = profiles[expected]
            verified = (status == "seeded" and selected == expected
                        and proof.get("class_path", profile.class_path) == profile.class_path)
        else:
            profile = _profile(selected, HEAD_PROFILES if head else PROFILES, head=head)
            verified = status in _APPLIED and (helmet_effects(proof) if head else effects(proof))
        if (not profile or type(before) is not int or type(after) is not int or not 0 <= before <= 4096
                or not 0 <= after <= 4096 or after != before + delta or not verified):
            raise RuntimeError("The typed stock armor slot and derived stats did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: dict(proof, class_path=profile.class_path)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        if not deferred:
            await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, ValueError, RuntimeError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
