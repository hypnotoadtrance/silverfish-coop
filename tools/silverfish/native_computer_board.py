"""Start the stock Computer Board contract and redeem it at its Scientist board."""

import time

from . import native_loot, native_rewards, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BOROUGH, SCIENTIST_SHOP


QUEST_ID = "Clt_ComputerBoard"
PREFLIGHT = "native-computer-board-preflight"
START = "native-computer-board-start"
APPROACH = "native-computer-board-approach"
TURN_IN = "native-computer-board-turn-in"
BOARDS = {
    BOROUGH: native_wire_quest.BOARD,
    SCIENTIST_SHOP: f"BountyBoard_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2",
}
FACTIONS = {"2", "4", "5"}
STEPS = {None, 1, 2, -10}


def _scope(host, actor: str) -> tuple[str, str]:
    player = host.party["players"].get(actor)
    area = host.last_game.get("map")
    if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or area not in BOARDS
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError("native_computer_board_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError("native_computer_board_interactor_unavailable")
    return pawn, area


async def _command(host, bridge, verb: str, pawn: str, area: str, *arguments: str) -> dict:
    try:
        snapshot = await bridge.command(verb, pawn, *arguments, timeout=8 if verb == TURN_IN else 5)
    except RuntimeError as error:
        if str(error).startswith("stock_computer_board_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get("native_computer_board", {})
    if not isinstance(proof, dict) or (snapshot.get("state"), snapshot.get("map"), *(proof.get(field) for field in
            ("operation", "map", "pawn", "board", "quest", "can_turn_in"))) != (
            "hosting", area, verb, area, pawn, BOARDS[area], QUEST_ID, area == SCIENTIST_SHOP):
        raise RuntimeError("The stock Computer Board report did not match the exact board and player")
    reputation = proof.get("reputation", {})
    if (not isinstance(reputation, dict) or reputation.keys() != FACTIONS
            or type(proof.get("can_turn_in")) is not bool
            or any(not isinstance(row, dict) or type(row.get("before")) is not int or type(row.get("after")) is not int
                   for row in reputation.values())
            or any(field not in proof or proof[field] is not None and
                   (type(proof[field]) is not int or proof[field] not in STEPS) for field in ("step_before", "step_after"))
            or any(type(proof.get(field)) is not int for field in ("cash_before", "cash_after", "xp_before", "xp_after"))):
        raise RuntimeError("The stock Computer Board report omitted the authoritative quest balances")
    guid, owner = proof.get("objective_guid"), proof.get("objective_owner")
    if ((guid not in (None, "-") or owner not in (None, "-")) and (not valid_guid(guid) or owner not in ("host", "guest"))
            or verb == PREFLIGHT and (any(proof[f"{field}_before"] != proof[f"{field}_after"] for field in ("step", "cash", "xp"))
                                      or any(row["before"] != row["after"] for row in reputation.values()))):
        raise RuntimeError("The stock Computer Board report did not preserve preflight state and item custody")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor: str) -> dict:
    pawn, area = _scope(host, actor)
    return {"native_computer_board": await _command(host, bridge, PREFLIGHT, pawn, area)}


async def approach(host, bridge, actor: str) -> dict:
    pawn, area = _scope(host, actor)
    if not host.fixtures:
        raise party.RuleError("native_computer_board_approach_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, timeout=5)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise RuntimeError("The Computer Board approach left the qualified listen world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_computer_board_approach": {"pawn": pawn, "board": BOARDS[area]}}


async def start(host, bridge, actor: str) -> dict:
    return await _commit(host, bridge, actor, START)


async def turn_in(host, bridge, actor: str) -> dict:
    return await _commit(host, bridge, actor, TURN_IN)


async def _commit(host, bridge, actor: str, verb: str) -> dict:
    pawn, area = _scope(host, actor)
    turning_in = verb == TURN_IN
    if turning_in and area != SCIENTIST_SHOP:
        raise party.RuleError("stock_computer_board_scientist_redemption_required")
    receipt = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if turning_in and receipt.get("step") == -10:
        return {"effect": "native_quest_already_settled", **receipt}

    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn, area)
        step = before["step_after"]
        if not turning_in and step is not None:
            return {"effect": "native_quest_present", "quest_id": QUEST_ID, "step": step}
        if turning_in and step == -10:
            return {"effect": "native_quest_already_completed", "quest_id": QUEST_ID, "step": step}

        arguments = ()
        if turning_in:
            if step not in (1, 2):
                raise party.RuleError("stock_computer_board_quest_not_active")
            guid = before.get("objective_guid")
            owner = "host" if actor == "host" else "guest"
            if not valid_guid(guid) or before.get("objective_owner") != owner:
                raise party.RuleError("stock_computer_board_objective_required")
            arguments = (guid, owner)

        await host.checkpoint_native(bridge)
        recipients = sorted(set(host.roles().values()) & host.party["players"].keys()) if turning_in else []
        if turning_in and actor not in recipients:
            raise party.RuleError("native_computer_board_interactor_unavailable")
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, verb, pawn, area, *arguments)
        cash = proof["cash_after"] - proof["cash_before"]
        xp = proof["xp_after"] - proof["xp_before"]
        expected_step = -10 if turning_in else 1
        reputation = proof["reputation"]
        if ((proof["step_before"], proof["step_after"], cash, xp) !=
                (step, expected_step, 800 if turning_in else 0, 1 if turning_in else 0)
                or proof["cash_before"] != before["cash_after"] or proof["xp_before"] != before["xp_after"]
                or any(row["before"] != before["reputation"][faction]["after"]
                       or not turning_in and row["before"] != row["after"] for faction, row in reputation.items())
                or turning_in and (proof.get("objective_guid"), proof.get("objective_owner")) != arguments):
            raise RuntimeError("The stock Computer Board transition did not reconcile with its authoritative preflight")

        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST_ID, "step": expected_step, "actor": actor}
        if turning_in:
            for character in recipients:
                player = candidate["players"][character]
                player["quest_xp"] = player.get("quest_xp", 0) + xp
                if character != "host":
                    native_rewards.queue(player, xp)
            candidate["economy"].update(wallet=proof["cash_after"], native_wallet_active=True)
            record.update(recipients=recipients, cash=cash, xp=xp, wallet=proof["cash_after"],
                          reputation={faction: row["after"] for faction, row in reputation.items()},
                          consumed_board=arguments[0], objective_owner=actor)
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        event = "native_computer_board_completed" if turning_in else "native_computer_board_started"
        host.control.event(event, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, "native_computer_board": proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
