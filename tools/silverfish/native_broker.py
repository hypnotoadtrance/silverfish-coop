"""Commit qualified stock Broker contracts against the host save."""

import time

from . import native_rewards, party
from .native_gold import valid_guid
from .travel import BLACK_MARKET


QUEST_ID = "Clct_EManifest"
DRILL_ID = "Clt_DrillHead_Brkr"
QUESTS = {QUEST_ID: ("native-broker", 10_000), DRILL_ID: ("native-broker-drill", 0)}
DEBT_REWARD = 1_000_000
CASH = 10_000
XP = 1
LEVEL = BLACK_MARKET + ".Shop_BlackMarket_01:PersistentLevel."
BROKER = "NPC_Broker_C " + LEVEL + "NPC_Broker_C_1"
STATION = "ItemRequest_TradeStation_BP_C " + LEVEL + "ItemRequest_TradeStation_BP_C_1"
STEPS = {"missing": None, "active": 1, "ready": 2, "completed": -10}
INTERACT = "native-quest-interact"


async def _command(host, bridge, verb: str, pawn: str, *arguments: str, quest_id: str = QUEST_ID) -> dict:
    snapshot = await bridge.command(verb, pawn, *arguments, timeout=6)
    proof = snapshot.get("native_broker", {})
    state = proof.get("state")
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != BLACK_MARKET
            or proof.get("operation") != verb or proof.get("map") != BLACK_MARKET
            or proof.get("pawn") != pawn or proof.get("id") != quest_id
            or state not in STEPS or proof.get("step") != STEPS[state] or proof.get("final_step") != 2
            or proof.get("cash") != QUESTS[quest_id][1] or proof.get("xp") != XP
            or (proof.get("manifest_guid") != "-" and not valid_guid(proof.get("manifest_guid")))):
        raise RuntimeError("The authoritative stock Broker quest report could not be verified")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def interact(host, bridge, character: str, station: str) -> dict:
    if (not host.broker_input or host.last_game.get("map") != BLACK_MARKET or station != STATION or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]):
        raise party.RuleError("stock_broker_station_unavailable")
    member = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not member or not member["connected"] or not player or player["downed"]:
        raise party.RuleError("stock_broker_interactor_unavailable")
    available = []
    for quest, (prefix, _) in QUESTS.items():
        proof = await _command(host, bridge, f"{prefix}-inspect", member["pawn"], quest_id=quest)
        if proof["step"] in (1, 2) and valid_guid(proof["manifest_guid"]):
            available.append(quest)
    if len(available) != 1:
        raise party.RuleError("stock_broker_objective_required" if not available else "stock_broker_choose_exact_quest")
    return await action(host, bridge, character, "native-quest-turnin", quest_id=available[0])


async def action(host, bridge, actor: str, verb: str, *, fixture: bool = False, quest_id: str = QUEST_ID) -> dict:
    roles = host.roles()
    player = host.party["players"].get(actor)
    pawn = host.authoritative_pawns(roles).get(actor)
    if (not host.native_profile or not pawn or not player or host.transitioning or host.party["wipe"]
            or host.last_game.get("map") != BLACK_MARKET or (fixture and not host.fixtures)):
        raise party.RuleError("stock_broker_unavailable")
    if player["downed"]:
        raise party.RuleError("actor_downed")
    slot = next(slot for slot, character in roles.items() if character == actor)
    alias, _ = host.role_pawn(slot)
    expected = host.last_game["pawns"].get(alias, {}).get("name")
    if not expected:
        raise party.RuleError("stock_broker_interactor_unavailable")

    prefix, cash_reward = QUESTS[quest_id]
    receipt = host.party.get("native_quests", {}).get(quest_id)
    if receipt and receipt["step"] == -10:
        return {"effect": "native_quest_already_settled", **receipt}
    if verb not in ("native-quest-start", "native-quest-turnin"):
        raise party.RuleError("stock_broker_action_unavailable")

    host.transitioning = True
    try:
        before = await _command(host, bridge, f"{prefix}-inspect", expected, quest_id=quest_id)
        if quest_id == DRILL_ID and (before.get("debt_reward") != DEBT_REWARD or not isinstance(before.get("native_debt"), int)):
            raise RuntimeError("The authoritative Drill Head debt reward was unavailable")
        if before["step"] == -10:
            # Stock completion present in an imported generation needs no second award.
            return {"effect": "native_quest_already_completed", "quest_id": quest_id, "step": -10}
        if verb == "native-quest-start" and before["step"] is not None:
            return {"effect": "native_quest_present", "quest_id": quest_id, "step": before["step"]}
        if verb == "native-quest-turnin":
            if before["step"] is None or not valid_guid(before["manifest_guid"]):
                raise party.RuleError("stock_broker_manifest_required" if quest_id == QUEST_ID else "stock_broker_drill_required")
            wallet = before["native_cash"]
            if not -(2**31) <= wallet <= 2_147_483_647 - cash_reward:
                raise party.RuleError("wallet_capacity_exceeded")

        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        applied_xp = frozenset()
        if verb == "native-quest-start":
            operation = f"{prefix}-issue-fixture" if fixture else f"{prefix}-issue"
            after = await _command(host, bridge, operation, expected, BROKER, quest_id=quest_id)
            if (not after["changed"] or after["step"] != 1 or (quest_id == DRILL_ID and
                    (after.get("topic_epc", 0) < 1 or after.get("topic_drill_lab", 0) < 1))):
                raise RuntimeError("The stock Broker quest did not start exactly once")
            record = {"quest_id": quest_id, "step": 1, "actor": actor}
            candidate = party.load_party(host.party)
        else:
            after = await _command(host, bridge, f"{prefix}-turn-in", expected, STATION, before["manifest_guid"],
                                   quest_id=quest_id)
            if (not after["changed"] or after["step"] != -10 or after["consumed_guid"] != before["manifest_guid"]
                    or after["native_cash"] != wallet + cash_reward or after["native_xp"] != before["native_xp"] + XP):
                raise RuntimeError("The native Broker completion and stock rewards did not reconcile")
            candidate = party.load_party(host.party)
            recipients = sorted(set(roles.values()) & candidate["players"].keys())
            for character in recipients:
                recipient = candidate["players"][character]
                recipient["quest_xp"] = recipient.get("quest_xp", 0) + XP
                if character != "host":
                    native_rewards.queue(recipient, XP)
            applied_xp = await native_rewards.project(host, bridge, candidate, native_rewards.pending_recipients(host, candidate))
            candidate["economy"]["wallet"] = after["native_cash"]
            record = {"quest_id": quest_id, "step": -10, "actor": actor, "recipients": recipients, "xp": XP,
                      "cash": cash_reward, "wallet": after["native_cash"], "consumed_guid": after["consumed_guid"]}
            if quest_id == DRILL_ID:
                if (after.get("native_debt") != before["native_debt"] - DEBT_REWARD
                        or after["freelancer_rep"] <= before["freelancer_rep"] or after["fae_rep"] >= before["fae_rep"]):
                    raise RuntimeError("The stock Drill Head debt and reputation effects did not verify")
                record.update(debt=after["native_debt"], debt_reward=DEBT_REWARD,
                              freelancer_rep=after["freelancer_rep"], fae_rep=after["fae_rep"])

        candidate.setdefault("native_quests", {})[quest_id] = record
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=applied_xp)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_broker_quest_committed", revision=revision, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
