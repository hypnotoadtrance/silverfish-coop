"""Commit MeetBroker at the Black Market board and distribute the stock starter kit."""

import time

from . import native_loot, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BLACK_MARKET


QUEST_ID = "MeetBroker"
BOARD = f"BountyBoard_BP_C {BLACK_MARKET}.Shop_BlackMarket_01:PersistentLevel.BountyBoard_BP_C_0"
PREFLIGHT = "native-meet-broker-preflight"
START = "native-meet-broker-start"
TURN_IN = "native-meet-broker-turn-in"
APPROACH = "native-meet-broker-approach"
REWARDS = native_wire_quest.RewardProfile("native_meet_broker_reward", BLACK_MARKET, "native-meet-broker", 9)


def _pawn(host, actor: str) -> str:
    player = host.party["players"].get(actor)
    if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != BLACK_MARKET
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError("native_meet_broker_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError("native_meet_broker_interactor_unavailable")
    return pawn


async def _command(host, bridge, verb: str, pawn: str) -> dict:
    try:
        snapshot = await bridge.command(verb, pawn, timeout=8 if verb == TURN_IN else 5)
    except RuntimeError as error:
        if str(error).startswith("stock_meet_broker_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get("native_meet_broker", {})
    expected = (verb, BLACK_MARKET, pawn, BOARD, QUEST_ID)
    if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", BLACK_MARKET)
            or tuple(proof.get(key) for key in ("operation", "map", "pawn", "board", "quest")) != expected
            or any(proof.get(key) not in (None, 1, -10) for key in ("step_before", "step_after"))
            or any(type(proof.get(key)) is not int for key in (
                "cash_before", "cash_after", "xp_before", "xp_after", "reputation_before", "reputation_after",
            ))
            or (not valid_guid(proof.get("weapon_guid")) if verb == TURN_IN else proof.get("weapon_guid") != "-")
            or verb == PREFLIGHT and any(proof[f"{key}_before"] != proof[f"{key}_after"]
                                         for key in ("step", "cash", "xp", "reputation"))):
        raise RuntimeError("The stock MeetBroker board did not return the exact authoritative report")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor: str) -> dict:
    return {"native_meet_broker": await _command(host, bridge, PREFLIGHT, _pawn(host, actor))}


async def approach(host, bridge, actor: str) -> dict:
    pawn = _pawn(host, actor)
    if not host.fixtures:
        raise party.RuleError("native_meet_broker_approach_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, timeout=5)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", BLACK_MARKET):
        raise RuntimeError("The MeetBroker approach left the qualified listen world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_meet_broker_approach": {"pawn": pawn, "board": BOARD}}


async def commit(host, bridge, actor: str, *, turning_in: bool) -> dict:
    pawn = _pawn(host, actor)
    receipt = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if turning_in and receipt.get("step") == -10:
        return {"effect": "native_quest_already_settled", **receipt}
    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn)
        step = before["step_after"]
        if not turning_in and step is not None:
            return {"effect": "native_quest_present", "quest_id": QUEST_ID, "step": step}
        if turning_in and step == -10:
            return {"effect": "native_quest_already_completed", "quest_id": QUEST_ID, "step": step}
        if turning_in and step != 1:
            raise party.RuleError("stock_meet_broker_quest_not_ready")
        await host.checkpoint_native(bridge)
        recipients = sorted(set(host.roles().values()) & host.party["players"].keys()) if turning_in else []
        if turning_in and actor not in recipients:
            raise party.RuleError("native_meet_broker_interactor_unavailable")
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, TURN_IN if turning_in else START, pawn)
        expected_step = -10 if turning_in else 1
        if (proof["step_before"], proof["step_after"], proof["cash_before"], proof["cash_after"],
                proof["xp_before"], proof["xp_after"]) != (
                step, expected_step, before["cash_after"], before["cash_after"],
                before["xp_after"], before["xp_after"] + int(turning_in)) or (
                proof["reputation_before"] != before["reputation_after"]
                or not turning_in and proof["reputation_after"] != before["reputation_after"]):
            raise RuntimeError("The stock MeetBroker transition did not match its authoritative preflight")
        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST_ID, "step": expected_step, "actor": actor}
        granted = []
        if turning_in:
            granted = await native_wire_quest.stage_party_rewards(host, bridge, candidate, REWARDS, recipients,
                                                                  proof["weapon_guid"])
            for character in recipients:
                player = candidate["players"][character]
                player["quest_xp"] = player.get("quest_xp", 0) + 1
            await native_wire_quest._grant_xp(host, bridge, candidate, granted)
            record.update(recipients=recipients, cash=0, xp=1, weapon_guid=proof["weapon_guid"],
                          ammo_units=REWARDS.ammo_units, freelancer_rep=proof["reputation_after"])
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(granted))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        event = "native_meet_broker_completed" if turning_in else "native_meet_broker_started"
        host.control.event(event, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, "native_meet_broker": proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
