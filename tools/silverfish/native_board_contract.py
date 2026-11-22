"""Apply ordinary contract actions on the host board identified by the player."""

import re
import time

from . import native_computer_board, native_inventory, native_loot, native_rewards, party
from .native_gold import valid_guid

COMMAND = "native-board-contract"
KEY = "native_board_contract"
REFUSAL = "stock_board_contract_"
QUEST_ID = re.compile(r"[A-Za-z0-9_]{1,96}")
COMPLETE = -10


def quest_id(value):
    return isinstance(value, str) and QUEST_ID.fullmatch(value) is not None


def parse(fields):
    if len(fields) != 18:
        raise ValueError("Invalid native board result")
    proof = dict(zip(("verb", "mode", "map", "pawn", "board", "quest", "state"), fields[1:8], strict=True))
    proof.update(zip(("step_before", "step_after", "expected_step"),
                     (None if field == "-" else int(field) for field in fields[8:11]), strict=True))
    proof.update(zip(("cash_before", "cash_after", "xp_before", "xp_after", "cash", "xp"),
                     map(int, fields[11:17]), strict=True))
    proof["guid"] = fields[17]
    if (not quest_id(proof["quest"]) or proof["verb"] not in {"start", "turnin"}
            or proof["mode"] not in {"inspect", "apply"} or proof["state"] not in {"qualified", "already", "applied"}
            or (proof["guid"] != "-" and not valid_guid(proof["guid"]))
            or min(proof["cash"], proof["xp"]) < 0):
        raise ValueError("Unqualified native board result")
    return proof


def scope(host, actor, selected_board):
    player = host.party["players"].get(actor)
    area = host.last_game.get("map")
    board = native_computer_board.BOARDS.get(area)
    if (not board or selected_board not in (None, board)
            or not host.native_profile or not host.native_zones or not host.native_guest
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3
            or not player or player["downed"] or actor in host.party["pending_spawns"]):
        raise party.RuleError("native_board_contract_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError("native_board_interactor_unavailable")
    return area, pawn, board


async def _command(host, bridge, verb, mode, area, pawn, board, quest, *arguments):
    try:
        snapshot = await bridge.command(COMMAND, verb, mode, pawn, board, quest, *arguments, timeout=5)
    except RuntimeError as error:
        if str(error).startswith(REFUSAL):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get(KEY, {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in
            ("verb", "mode", "map", "pawn", "board", "quest"))) != ("hosting", area, verb, mode, area, pawn, board, quest):
        raise RuntimeError("The stock board receipt did not identify the current actor and board")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


def _receipt(actor, before, result):
    step = before["expected_step"]
    if (result["state"], result["step_after"]) != ("applied", step):
        raise RuntimeError("The stock board action did not reach the qualified quest step")
    for key in ("step_before", "expected_step", "guid", "cash_before", "xp_before", "cash", "xp"):
        if result[key] != before[key]:
            raise RuntimeError("The stock board action differs from its inspected recipe")
    cash, xp = result["cash"], result["xp"]
    if (result["cash_after"] - result["cash_before"], result["xp_after"] - result["xp_before"]) != (cash, xp):
        raise RuntimeError("The stock board action did not reconcile cash and XP")
    record = {"quest_id": result["quest"], "step": step, "actor": actor}
    if step == COMPLETE:
        record.update(cash=cash, xp=xp, wallet=result["cash_after"])
        if result["guid"] != "-":
            record.update(consumed_guid=result["guid"], objective_owner=actor)
    return record


async def perform(host, bridge, actor, verb, quest, *, board=None):
    if verb not in {"start", "turnin"} or not quest_id(quest):
        raise party.RuleError("native_board_contract_arguments")
    area, pawn, board = scope(host, actor, board)
    turning_in = verb == "turnin"
    prior = host.party.get("native_quests", {}).get(quest, {})
    if turning_in and prior.get("step") == COMPLETE:
        return {"effect": "native_quest_already_settled", **prior}

    host.transitioning = True
    try:
        before = await _command(host, bridge, verb, "inspect", area, pawn, board, quest)
        if before["state"] == "already":
            return {"effect": "native_quest_present", "quest_id": quest, "step": before["step_after"]}
        if (before["state"] != "qualified" or before["expected_step"] is None
                or (before["expected_step"] == COMPLETE) != turning_in
                or (before["cash_before"], before["xp_before"], before["step_before"]) !=
                (before["cash_after"], before["xp_after"], before["step_after"])):
            raise RuntimeError("The stock board did not return a qualified inspection")

        candidate = party.load_party(host.party)
        recipients = sorted(set(host.roles().values()) & candidate["players"].keys()) if turning_in else []
        if turning_in and actor not in recipients:
            raise party.RuleError("native_board_interactor_unavailable")
        if turning_in and before["guid"] != "-" and actor != "host":
            if not await native_inventory.retain_guest_save(host, bridge, actor, pawn):
                raise party.RuleError("native_board_objective_retention_unavailable")

        host.native_checkpoint_unsafe = True
        result = await _command(host, bridge, verb, "apply", area, pawn, board, quest, before["guid"])
        record = _receipt(actor, before, result)
        if turning_in:
            record["recipients"] = recipients
            for character in recipients:
                player = candidate["players"][character]
                player["quest_xp"] = player.get("quest_xp", 0) + record["xp"]
                if character != "host":
                    native_rewards.queue(player, record["xp"])
            candidate["economy"].update(wallet=record["wallet"], native_wallet_active=True)
        candidate.setdefault("native_quests", {})[quest] = record
        host.party = candidate
        host.pending_host_save = True
        host.native_checkpoint_unsafe = False
        host.control.event("native_board_contract_applied", board=board, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
