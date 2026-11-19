"""Bank and claim ammunition from exact staged stock enemy encounters."""

import asyncio
import math
import os
import time

from . import native_host_save, native_loot, party
from .native_gold import valid_guid
from .native_host_save import CQC_DROP_SOURCE as CQC
from .runtime import MAP
from .travel import DEAD_BRIDGE

BANK = "native-enemy-loot-bank"
CLAIM = "native-enemy-loot-claim"
APPROACH = "native-enemy-loot-approach"
STATE_KEY = "native_enemy_loot_rolls"
SHELLS = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"


def watch_target(game: dict, target: str | None) -> str | None:
    area = game.get("map")
    if area == MAP and target and target == game.get("native_bandit", {}).get("npc"):
        return target
    if area == DEAD_BRIDGE and target == CQC:
        return target
    return None


def published(records: dict) -> dict:
    return {key: native_loot.public_roll(roll) for key, roll in records.items()}


def _available(host):
    area = host.last_game.get("map")
    if (not host.fixtures or not host.native_guest or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party["wipe"] or host.last_game.get("state") != "hosting"
            or area not in (MAP, DEAD_BRIDGE) or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_enemy_loot_unavailable")
    if area == DEAD_BRIDGE and any(os.environ.get(name) != "1" for name in (
        "SILVERFISH_COOP_NATIVE_ENEMY_LOOT", "SILVERFISH_COOP_NATIVE_NPC_TEST",
    )):
        raise party.RuleError("native_enemy_loot_unavailable")
    return area


async def approach(host, bridge, target: str | None) -> dict:
    area = _available(host)
    role, sep, guid = target.partition("|") if isinstance(target, str) else ("", "", "")
    roll = host.party.get(STATE_KEY, {}).get(guid) if sep and valid_guid(guid) else None
    character = host.roles().get(role)
    pawn = native_loot._scope(host, character) if character else None
    if (not roll or roll.get("map") != area or (area == DEAD_BRIDGE and roll.get("bandit") != CQC)
            or roll.get("claimed_by") is not None
            or not pawn or host.party["players"][character]["downed"]):
        raise party.RuleError("native_enemy_loot_fixture_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, guid, *(str(axis) for axis in roll["position"]), timeout=5)
    proof = snapshot.get("native_enemy_loot_escrow", {})
    if (proof.get("map"), proof.get("state"), proof.get("pawn"), proof.get("guid")) != (area, "approached", pawn, guid):
        raise RuntimeError("The headless stock drop approach did not verify")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_enemy_loot_approach": proof}


async def _commit(host, bridge, candidate, snapshot, *, require_guest_frames=frozenset()):
    host.last_game, host.last_seen = snapshot, time.monotonic()
    revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=require_guest_frames)
    host.party = candidate
    host.native_checkpoint_unsafe = False
    return revision


async def bank(host, bridge, target: str | None) -> dict:
    area = _available(host)
    source = host.last_game.get("native_enemy_loot", {})
    if (source.get("state"), source.get("map"), source.get("loot_calls"), source.get("ammo_class")) != (
            "stock_loot_observed", area, 1, SHELLS) or target != source.get("bandit") or (area == DEAD_BRIDGE and target != CQC):
        raise party.RuleError("native_enemy_loot_exact_drop_unavailable")
    roles = host.roles()
    killer = roles.get(source.get("killer_role"))
    if not killer or native_loot._scope(host, killer) != source.get("killer_pawn"):
        raise party.RuleError("native_enemy_loot_exact_killer_unavailable")
    for recorded in host.party.get(STATE_KEY, {}).values():
        if (recorded["epoch"], recorded["bandit"], recorded["actor"]) == (host.zone_epoch, target, source["ammo_actor"]):
            return {"native_enemy_loot_roll": native_loot.public_roll(recorded)}
    slot = native_host_save.new_slot()
    host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(BANK, target, slot, source["ammo_actor"], timeout=7)
        proof = snapshot.get("native_enemy_loot_escrow", {})
        at, guid = proof.get("position"), proof.get("guid")
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("state"), proof.get("pawn"),
            proof.get("actor"), proof.get("slot"), proof.get("units")) != (
                "hosting", area, area, "captured", source["killer_pawn"], source["ammo_actor"], slot, source["ammo_quantity"]
        ) or not valid_guid(guid) or not at or any(coord is None or abs(coord) > 10000000 for coord in at):
            raise RuntimeError("The exact stock enemy drop escrow did not verify")
        frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
        roll = {"map": area, "epoch": host.zone_epoch, "bandit": target, "actor": proof["actor"], "guid": guid,
                "class_path": SHELLS, "quantity": proof["units"], "position": at, "killer": killer, "claimed_by": None,
                "escrow": frame}
        candidate = party.load_party(host.party)
        records = candidate.setdefault(STATE_KEY, {})
        if guid in records:
            raise RuntimeError("The stock enemy drop identity already exists")
        records[guid] = roll
        revision = await _commit(host, bridge, candidate, snapshot)
        report = native_loot.public_roll(roll)
        host.control.event("native_enemy_loot_roll_committed", revision=revision, **report)
        return {"native_enemy_loot_roll": report}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise


async def claim(host, bridge, character: str, target: str | None, operation: str) -> dict:
    area = _available(host)
    if not valid_guid(target) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_enemy_loot_exact_claim_required")
    fingerprint = [CLAIM, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    roll = host.party.get(STATE_KEY, {}).get(target)
    if not roll or roll.get("map") != area or (area == DEAD_BRIDGE and roll.get("bandit") != CQC):
        raise party.RuleError("native_enemy_loot_item_unavailable")
    if roll.get("claimed_by") is not None:
        raise party.RuleError("native_enemy_loot_already_claimed")
    record = host.party["players"].get(character)
    pawn = native_loot._scope(host, character)
    if not record or record["downed"] or not pawn or character in host.party["pending_spawns"]:
        raise party.RuleError("native_enemy_loot_claimant_unavailable")
    location = next((row.get("location") for row in host.last_game.get("pawns", {}).values()
                     if row.get("name") == pawn), None)
    if not location or any(axis is None for axis in location) or math.dist(location, roll["position"]) > party.INTERACT_RANGE:
        raise party.RuleError("native_enemy_loot_out_of_reach")
    frame = roll["escrow"]
    await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(CLAIM, pawn, frame["slot"], target,
                                            *(str(axis) for axis in roll["position"]), timeout=7)
        except RuntimeError as error:
            if str(error) == "native_enemy_loot_out_of_reach":
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get("native_enemy_loot_escrow", {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot"), proof.get("guid"))
                != ("hosting", area, area, pawn, frame["slot"], target) or proof.get("state") not in ("claimed", "already_claimed")):
            raise RuntimeError("The typed stock enemy claim did not match its exact participant")
        candidate = party.load_party(host.party)
        committed = candidate[STATE_KEY][target]
        committed["claimed_by"] = character
        committed.pop("escrow")
        report = native_loot.public_roll(committed)
        result = {"native_enemy_loot_claim": report}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await _commit(host, bridge, candidate, snapshot, require_guest_frames=frozenset({character} - {"host"}))
        host.control.event("native_enemy_loot_claim_committed", revision=revision, **report)
        return result
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
