"""Commit the stock Copper Wire contract and the eligible party's exact rewards."""

import asyncio
import time
import uuid

from . import native_host_save, native_rewards, party
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
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        verified = await _command(host, bridge, START, pawn)
        balances = ("native_cash", "native_xp", "native_commune_rep")
        if (verified["step"] != 1 or verified["changed"] is not True
                or tuple(verified[field] for field in balances) != tuple(prior[field] for field in balances)):
            raise RuntimeError("Adding Copper Wire changed balances or did not enter the stock ready step")
        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST_ID, "step": 1, "actor": actor}
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_wire_quest_started", revision=revision, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def _reward_command(host, bridge, verb, arguments, pawn, slot, outcomes) -> dict:
    area = host.last_game.get("map")
    if area not in host.allowed_maps or verb == "native-wire-quest-capture" and area != BOROUGH:
        raise RuntimeError("The stock Copper Wire reward is outside its qualified listen world")
    snapshot = await bridge.command(verb, *arguments, timeout=7)
    proof = snapshot.get("native_wire_quest_reward", {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot")) != (
            "hosting", area, area, pawn, slot) or proof.get("state") not in outcomes:
        raise RuntimeError("The stock Copper Wire reward report did not match its exact recipient")
    if (not valid_guid(proof.get("weapon_guid")) or not valid_guid(proof.get("ammo_guid"))
            or proof["weapon_guid"] == proof["ammo_guid"] or type(proof.get("weapon_mag")) is not int
            or not 0 <= proof["weapon_mag"] <= 256 or proof.get("ammo_units") != 14):
        raise RuntimeError("The stock Copper Wire escrow did not report both exact typed items")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def _grant(host, bridge, candidate: dict) -> list[str]:
    reward = candidate[REWARD_KEY]
    if not host.last_game.get("native_guest_xp_ready"):
        return []
    eligible = host.native_recipients()
    accepted = [character for character in reward["pending"] if character in eligible and eligible[character]["connected"]]
    if not accepted:
        return []
    frame = reward["escrow"]
    if frame.get("map") != BOROUGH:
        raise RuntimeError("The Copper Wire reward escrow belongs to the wrong map")
    await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    for character in accepted:
        pawn = eligible[character]["pawn"]
        weapon, ammo = reward["pending"][character]
        proof = await _reward_command(host, bridge, "native-wire-quest-grant",
                                      (pawn, frame["slot"], reward["weapon"], reward["ammo"], weapon, ammo),
                                      pawn, frame["slot"], ("granted", "already_granted"))
        if (proof["weapon_guid"], proof["ammo_guid"], proof["weapon_mag"]) != (weapon, ammo, reward["weapon_mag"]):
            raise RuntimeError("The guest's stock Copper Wire reward did not match the immutable template")
        del reward["pending"][character]
    if not reward["pending"]:
        candidate.pop(REWARD_KEY)
    return accepted


async def _grant_xp(host, bridge, candidate: dict, accepted: list[str]) -> None:
    for character in accepted:
        native_rewards.queue(candidate["players"][character], 1)
    projected = [row for row in native_rewards.pending_recipients(host, candidate) if row[0] in accepted]
    await native_rewards.project(host, bridge, candidate, projected)


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
        award = uuid.uuid4()
        guest_awards = {
            character: [uuid.uuid5(award, f"{character}:{item}").hex.upper() for item in ("weapon", "ammo")]
            for character in recipients if character != "host"
        }
        accepted = []
        if guest_awards:
            slot = native_host_save.new_slot()
            host_pawn = snapshot["pawns"]["local"]["name"]
            template = await _reward_command(host, bridge, "native-wire-quest-capture", (slot, proof["weapon_guid"]),
                                             host_pawn, slot, ("captured",))
            if template["weapon_guid"] != proof["weapon_guid"]:
                raise RuntimeError("The Copper Wire template did not capture the exact awarded stock weapon")
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, BOROUGH, flush=True)
            candidate[REWARD_KEY] = {"escrow": frame, "weapon": template["weapon_guid"], "ammo": template["ammo_guid"],
                                     "weapon_mag": template["weapon_mag"], "pending": guest_awards}
            accepted = await _grant(host, bridge, candidate)
        for character in recipients:
            player = candidate["players"][character]
            player["quest_xp"] = player.get("quest_xp", 0) + 1
        await _grant_xp(host, bridge, candidate, accepted)
        record = {"quest_id": QUEST_ID, "step": -10, "actor": actor, "recipients": recipients, "cash": 0, "xp": 1,
                  "commune_rep": proof["rep_after"], "consumed_wire": proof["objective_guid"],
                  "objective_owner": actor if proof["objective_owner"] == "guest" else "host"}
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(accepted))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_wire_quest_completed", revision=revision, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def settle(host, bridge) -> None:
    if (host.last_game.get("state") != "hosting" or host.last_game.get("map") not in host.allowed_maps
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or not host.party.get(REWARD_KEY)):
        return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        accepted = await _grant(host, bridge, candidate)
        if accepted:
            await _grant_xp(host, bridge, candidate, accepted)
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(accepted))
            host.party = candidate
            host.control.event("native_wire_quest_pending_awards_committed", revision=revision, recipients=accepted)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
