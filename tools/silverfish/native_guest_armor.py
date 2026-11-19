"""Equip the exact guest-owned stock Bomber Jacket in the detached body slot."""

import math
import time

from . import party
from .native_gold import valid_guid


ACTION = "native-guest-armor-equip"
FIXTURE = "native-guest-armor-fixture"
CLASS_PATH = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
_APPLIED = {"equipped", "reapplied", "already_applied"}


def parse(fields):
    return {
        "map": fields[1], "pawn": fields[2], "guid": fields[3], "action": fields[4],
        "ok": fields[5] == "1", "status": fields[6],
        "before": int(fields[7]), "after": int(fields[8]),
        **{key: float(value) for key, value in zip(
            ("carry_before", "carry_after", "minimum", "slash", "acid"), fields[9:]
        )},
    }


def effects(proof):
    carry_before, carry_after, minimum, slash, acid = (
        proof.get(key) for key in ("carry_before", "carry_after", "minimum", "slash", "acid")
    )
    numbers = (carry_before, carry_after, minimum, slash, acid)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in numbers):
        return False
    debit = 0.25 if proof.get("status") == "equipped" else 0.0
    return (carry_before >= 0 and carry_after >= 0
            and math.isclose(carry_before - carry_after, debit, abs_tol=1e-6)
            and (minimum, slash, acid) == (25.0, 0.2, 0.2))


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
        delta = 1 if seed else -1 if status == "equipped" else 0
        if (type(before) is not int or type(after) is not int or not 0 <= before <= 4096
                or not 0 <= after <= 4096 or after != before + delta
                or (status != "seeded" if seed else (status not in _APPLIED or not effects(proof)))):
            raise RuntimeError("The typed stock armor slot and derived stats did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {"native_guest_armor": dict(proof, class_path=CLASS_PATH)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, ValueError, RuntimeError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
