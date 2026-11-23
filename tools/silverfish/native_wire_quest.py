"""Commit the stock Copper Wire contract and the eligible party's exact rewards."""

import asyncio
import time
import uuid
from dataclasses import dataclass

from . import native_host_save, native_inventory, native_rewards, party
from .native_gold import valid_guid
from .travel import BOROUGH


QUEST_ID = "Clt_Wire"
BOARD = f"BountyBoard_BP_C {BOROUGH}.Borough:PersistentLevel.BountyBoard_BP_C_3"
STEPS = {"missing": None, "active": 1, "ready": 2, "completed": -10}
PREFLIGHT = "native-wire-quest-preflight"
START = "native-wire-quest-start"
APPROACH = "native-wire-quest-approach"
TURN_IN = "native-wire-quest-turn-in"
REWARD_KEY = "native_wire_quest_reward"

@dataclass(frozen=True)
class RewardProfile:
    key: str
    area: str
    command: str
    ammo_units: int


COPPER_STOP_REWARDS = RewardProfile(REWARD_KEY, BOROUGH, "native-wire-quest", 14)


def _pawn(host, actor: str) -> str:
    player = host.party["players"].get(actor)
    if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != BOROUGH
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError("native_wire_quest_unavailable")
    if actor == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(actor, {})
        pawn = member.get("pawn") if member.get("connected") else None
    if not pawn:
        raise party.RuleError("native_wire_quest_interactor_unavailable")
    return pawn


async def _command(host, bridge, verb: str, pawn: str) -> dict:
    snapshot = await bridge.command(verb, pawn, timeout=5)
    proof = snapshot.get("native_wire_quest", {})
    state = proof.get("state")
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != BOROUGH
            or tuple(proof.get(field) for field in ("operation", "map", "pawn", "board", "id"))
            != (verb, BOROUGH, pawn, BOARD, QUEST_ID)
            or state not in STEPS or proof.get("step") != STEPS[state]
            or tuple(proof.get(field) for field in ("final_step", "cash_reward", "xp_reward", "commune_base_reward"))
            != (2, 0, 1, 20)):
        raise RuntimeError("The stock Copper Wire board did not return the exact authoritative report")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor: str) -> dict:
    return {"native_wire_quest": await _command(host, bridge, PREFLIGHT, _pawn(host, actor))}


async def approach(host, bridge, actor: str) -> dict:
    pawn = _pawn(host, actor)
    if not host.fixtures:
        raise party.RuleError("native_wire_quest_approach_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, timeout=5)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_wire_quest_approach": {"pawn": pawn, "board": BOARD}}


async def start(host, bridge, actor: str) -> dict:
    pawn = _pawn(host, actor)
    host.transitioning = True
    try:
        prior = await _command(host, bridge, PREFLIGHT, pawn)
        if prior["step"] is not None:
            return {"effect": "native_quest_present", "quest_id": QUEST_ID, "step": prior["step"]}
        host.native_checkpoint_unsafe = True
        verified = await _command(host, bridge, START, pawn)
        balances = ("native_cash", "native_xp", "native_commune_rep")
        if (verified["step"] != 1 or verified["changed"] is not True
                or tuple(verified[field] for field in balances) != tuple(prior[field] for field in balances)):
            raise RuntimeError("Adding Copper Wire changed balances or did not enter the stock ready step")
        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST_ID, "step": 1, "actor": actor}
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        # Acceptance changes stock quest progress only. The next normal save captures it.
        host.pending_host_save = bool(host.pending_host_save)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_wire_quest_started", revision=None, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def _reward_command(host, bridge, profile, operation, arguments, pawn, slot, outcomes) -> dict:
    area = host.last_game.get("map")
    if area not in host.allowed_maps or operation == "capture" and area != profile.area:
        raise RuntimeError("The stock quest reward is outside its qualified listen world")
    verb = f"{profile.command}-{operation}"
    snapshot = await bridge.command(verb, *arguments, timeout=7)
    proof = snapshot.get(profile.key, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot")) != (
            "hosting", area, area, pawn, slot) or proof.get("state") not in outcomes:
        raise RuntimeError("The stock quest reward report did not match its exact recipient")
    if (not valid_guid(proof.get("weapon_guid")) or not valid_guid(proof.get("ammo_guid"))
            or proof["weapon_guid"] == proof["ammo_guid"] or type(proof.get("weapon_mag")) is not int
            or not 0 <= proof["weapon_mag"] <= 256 or proof.get("ammo_units") != profile.ammo_units):
        raise RuntimeError("The stock quest reward escrow did not report both exact typed items")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


def defer_reward_save(host) -> bool:
    return host.native_loot_input and host.last_game.get("native_guest_save_retention") is True


async def _retain_reward_guest(host, bridge, character: str, pawn: str) -> bool:
    try:
        return await native_inventory.retain_guest_save(host, bridge, character, pawn)
    except RuntimeError as error:
        if str(error) in ("An exact authoritative co-op guest is required", "The exact guest inventory could not be retained"):
            return False
        raise


async def _grant(host, bridge, candidate: dict, profile: RewardProfile, *, defer_save: bool) -> list[str]:
    reward = candidate[profile.key]
    if not host.last_game.get("native_guest_xp_ready"):
        return []
    eligible = host.native_recipients()
    ready = [character for character in reward["pending"] if eligible.get(character, {}).get("connected")]
    if not ready:
        return []
    frame = reward["escrow"]
    if frame.get("map") != profile.area:
        raise RuntimeError("The stock quest reward escrow belongs to the wrong map")
    memory = frame.get("pending") is True
    if not memory:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    accepted = []
    for character in ready:
        member = host.native_recipients().get(character, {})
        if not member.get("connected"):
            continue
        pawn = member["pawn"]
        if defer_save:
            if not await _retain_reward_guest(host, bridge, character, pawn):
                continue
            current = host.native_recipients().get(character, {})
            if not current.get("connected") or current.get("pawn") != pawn:
                continue
        weapon, ammo = reward["pending"][character]
        arguments = (pawn, frame["slot"], reward["weapon"], reward["ammo"], weapon, ammo)
        if memory:
            arguments += ("memory",)
        try:
            proof = await _reward_command(host, bridge, profile, "grant", arguments,
                                          pawn, frame["slot"], ("granted", "already_granted"))
        except RuntimeError as error:
            # These refusals occur before a typed grant can change the detached save.
            if defer_save and str(error) in ("An exact authoritative co-op guest is required",
                                             "wire_reward_exact_guest_required", "reward_exact_detached_guest_required"):
                continue
            raise
        if (proof["weapon_guid"], proof["ammo_guid"], proof["weapon_mag"]) != (weapon, ammo, reward["weapon_mag"]):
            raise RuntimeError("The guest's stock quest reward did not match the immutable template")
        del reward["pending"][character]
        accepted.append(character)
    if not reward["pending"]:
        if memory:
            snapshot = await bridge.command("native-loot-release-escrows", frame["slot"], timeout=3)
            if (snapshot.get("state"), snapshot.get("map")) != ("hosting", host.last_game["map"]):
                raise RuntimeError("The stock quest escrow release changed campaign map")
            host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate.pop(profile.key)
    return accepted


async def _grant_xp(host, bridge, candidate: dict, accepted: list[str]) -> None:
    for character in accepted:
        native_rewards.queue(candidate["players"][character], 1)
    projected = [row for row in native_rewards.pending_recipients(host, candidate) if row[0] in accepted]
    await native_rewards.project(host, bridge, candidate, projected)

async def stage_party_rewards(host, bridge, candidate: dict, profile: RewardProfile, recipients: list[str],
                              stock_weapon_guid: str, *, defer_save: bool) -> list[str]:
    award = uuid.uuid4()
    guest_awards = {
        character: [uuid.uuid5(award, f"{character}:{item}").hex.upper() for item in ("weapon", "ammo")]
        for character in recipients if character != "host"
    }
    if not guest_awards:
        return []
    slot = native_host_save.new_slot()
    host_pawn = host.last_game["pawns"]["local"]["name"]
    arguments = (slot, stock_weapon_guid, "memory") if defer_save else (slot, stock_weapon_guid)
    template = await _reward_command(host, bridge, profile, "capture", arguments,
                                     host_pawn, slot, ("captured",))
    if template["weapon_guid"] != stock_weapon_guid:
        raise RuntimeError("The quest template did not capture the exact awarded stock weapon")
    if defer_save:
        frame = {"slot": slot, "map": profile.area, "pending": True}
    else:
        frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, profile.area, flush=True)
    candidate[profile.key] = {"escrow": frame, "weapon": template["weapon_guid"], "ammo": template["ammo_guid"],
                              "weapon_mag": template["weapon_mag"], "pending": guest_awards}
    return await _grant(host, bridge, candidate, profile, defer_save=defer_save)


async def turn_in(host, bridge, actor: str) -> dict:
    pawn = _pawn(host, actor)
    receipt = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if receipt.get("step") == -10:
        return {"effect": "native_quest_already_settled", **receipt}
    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn)
        if before["step"] == -10:
            return {"effect": "native_quest_already_completed", "quest_id": QUEST_ID, "step": -10}
        if before["step"] not in (1, 2):
            raise party.RuleError("stock_wire_quest_not_ready")
        if not valid_guid(before.get("objective_guid")) or before.get("objective_owner") not in ("host", "guest"):
            raise party.RuleError("stock_wire_quest_objective_required")
        defer_save = defer_reward_save(host)
        if defer_save and before["objective_owner"] == "guest" and not await _retain_reward_guest(host, bridge, actor, pawn):
            raise party.RuleError("stock_quest_reward_guest_retention_unavailable")
        if not defer_save:
            await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        recipients = sorted(set(host.roles().values()) & host.party["players"].keys())
        snapshot = await bridge.command(TURN_IN, pawn, before["objective_guid"], before["objective_owner"], timeout=8)
        proof = snapshot.get("native_wire_quest_redemption", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != BOROUGH
                or tuple(proof.get(field) for field in ("map", "pawn", "board", "id")) != (BOROUGH, pawn, BOARD, QUEST_ID)
                or not valid_guid(proof.get("weapon_guid")) or proof.get("ammo_units") != 14
                or proof.get("native_cash") != before["native_cash"] or proof.get("native_xp") != before["native_xp"] + 1
                or proof.get("rep_before") != before["native_commune_rep"]
                or (proof.get("objective_guid"), proof.get("objective_owner")) !=
                (before["objective_guid"], before["objective_owner"])):
            raise RuntimeError("The stock Copper Wire completion did not reconcile")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        accepted = await stage_party_rewards(host, bridge, candidate, COPPER_STOP_REWARDS, recipients,
                                             proof["weapon_guid"], defer_save=defer_save)
        for character in recipients:
            player = candidate["players"][character]
            player["quest_xp"] = player.get("quest_xp", 0) + 1
        await _grant_xp(host, bridge, candidate, accepted)
        record = {"quest_id": QUEST_ID, "step": -10, "actor": actor, "recipients": recipients, "cash": 0, "xp": 1,
                  "commune_rep": proof["rep_after"], "consumed_wire": proof["objective_guid"],
                  "objective_owner": actor if proof["objective_owner"] == "guest" else "host"}
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = None
        if defer_save:
            host.pending_host_save = True
        else:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(accepted))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_wire_quest_completed", revision=revision, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def settle(host, bridge, profile: RewardProfile = COPPER_STOP_REWARDS) -> None:
    if (host.last_game.get("state") != "hosting" or host.last_game.get("map") not in host.allowed_maps
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or not host.party.get(profile.key)):
        return
    defer_save = defer_reward_save(host)
    if host.party[profile.key]["escrow"].get("pending") is True and not defer_save:
        return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        accepted = await _grant(host, bridge, candidate, profile, defer_save=defer_save)
        if accepted:
            await _grant_xp(host, bridge, candidate, accepted)
            revision = None
            if not defer_save:
                revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(accepted))
            host.party = candidate
            host.control.event(f"{profile.command.replace('-', '_')}_pending_awards_committed",
                               revision=revision, recipients=accepted)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        host.stopping = True
        raise
