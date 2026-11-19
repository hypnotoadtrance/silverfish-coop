"""Commit the stock Broker manifest issue and redemption against the host save."""

import time

from . import party
from .native_gold import valid_guid
from .travel import BLACK_MARKET


QUEST_ID = "Clct_EManifest"
CASH = 10_000
XP = 1
LEVEL = BLACK_MARKET + ".Shop_BlackMarket_01:PersistentLevel."
BROKER = "NPC_Broker_C " + LEVEL + "NPC_Broker_C_1"
STATION = "ItemRequest_TradeStation_BP_C " + LEVEL + "ItemRequest_TradeStation_BP_C_1"
STEPS = {"missing": None, "active": 1, "ready": 2, "completed": -10}


async def _command(host, bridge, verb: str, pawn: str, *arguments: str) -> dict:
    snapshot = await bridge.command(verb, pawn, *arguments, timeout=6)
    proof = snapshot.get("native_broker", {})
    state = proof.get("state")
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != BLACK_MARKET
            or proof.get("operation") != verb or proof.get("map") != BLACK_MARKET
            or proof.get("pawn") != pawn or proof.get("id") != QUEST_ID
            or state not in STEPS or proof.get("step") != STEPS[state] or proof.get("final_step") != 2
            or proof.get("cash") != CASH or proof.get("xp") != XP
            or (proof.get("manifest_guid") != "-" and not valid_guid(proof.get("manifest_guid")))):
        raise RuntimeError("The authoritative stock Broker quest report could not be verified")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def action(host, bridge, actor: str, verb: str, *, fixture: bool = False) -> dict:
    roles = host.roles()
    player = host.party["players"].get(actor)
    pawn = host.authoritative_pawns(roles).get(actor)
    if (not host.native_profile or not pawn or not player or host.transitioning or host.party["wipe"]
            or host.last_game.get("map") != BLACK_MARKET or (fixture and not host.fixtures)):
        raise party.RuleError("stock_broker_unavailable")
    if player["downed"]:
        raise party.RuleError("actor_downed")
    slot = next(slot for slot, character in roles.items() if character == actor)
    expected = host.last_game["pawns"].get("local" if slot == "host" else slot, {}).get("name")
    if not expected:
        raise party.RuleError("stock_broker_interactor_unavailable")

    receipt = host.party.get("native_quests", {}).get(QUEST_ID)
    if receipt and receipt["step"] == -10:
        return {"effect": "native_quest_already_settled", **receipt}
    if verb not in ("native-quest-start", "native-quest-turnin"):
        raise party.RuleError("stock_broker_action_unavailable")

    host.transitioning = True
    try:
        before = await _command(host, bridge, "native-broker-inspect", expected)
        if before["step"] == -10:
            # Stock completion present in an imported generation needs no second award.
            return {"effect": "native_quest_already_completed", "quest_id": QUEST_ID, "step": -10}
        if verb == "native-quest-start" and before["step"] is not None:
            return {"effect": "native_quest_present", "quest_id": QUEST_ID, "step": before["step"]}
        if verb == "native-quest-turnin":
            if before["step"] is None or not valid_guid(before["manifest_guid"]):
                raise party.RuleError("stock_broker_manifest_required")
            wallet = host.party["economy"]["wallet"]
            if wallet + CASH > 2_147_483_647:
                raise party.RuleError("wallet_capacity_exceeded")
            if before["native_cash"] != wallet:
                await bridge.command("wallet-set", str(wallet), timeout=4)
                before = await _command(host, bridge, "native-broker-inspect", expected)
                if before["native_cash"] != wallet:
                    raise RuntimeError("The native wallet could not be synchronized before Broker redemption")

        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        if verb == "native-quest-start":
            operation = "native-broker-issue-fixture" if fixture else "native-broker-issue"
            after = await _command(host, bridge, operation, expected, BROKER)
            if not after["changed"] or after["step"] != 1:
                raise RuntimeError("The stock Broker quest did not start exactly once")
            record = {"quest_id": QUEST_ID, "step": 1, "actor": actor}
            candidate = party.load_party(host.party)
        else:
            after = await _command(host, bridge, "native-broker-turn-in", expected, STATION, before["manifest_guid"])
            if (not after["changed"] or after["step"] != -10 or after["consumed_guid"] != before["manifest_guid"]
                    or after["native_cash"] != wallet + CASH or after["native_xp"] != before["native_xp"] + XP):
                raise RuntimeError("The native Broker completion and stock rewards did not reconcile")
            candidate = party.load_party(host.party)
            recipients = sorted(set(roles.values()) & candidate["players"].keys())
            for character in recipients:
                recipient = candidate["players"][character]
                recipient["quest_xp"] = recipient.get("quest_xp", 0) + XP
            candidate["economy"]["wallet"] = after["native_cash"]
            record = {"quest_id": QUEST_ID, "step": -10, "actor": actor, "recipients": recipients, "xp": XP,
                      "cash": CASH, "wallet": after["native_cash"], "consumed_guid": after["consumed_guid"]}

        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate)
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
