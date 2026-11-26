"""Apply ordinary contract actions on the host board identified by the player."""

import re
import time

from . import native_computer_board, native_inventory, native_loot, native_rewards, party
from .native_gold import valid_guid
from .travel import PLAYTEST_MAPS

COMMAND = "native-board-contract"
KEY = "native_board_contract"
CATALOG = "native-board-contract-catalog"
CATALOG_KEY = "native_board_contract_catalog"
MENU_OPEN = "native-wire-board-stock-open"
MENU_KEY = "native_wire_board_stock"
MENU_REPLY = "native-board-menu-reply"
REFUSAL = "stock_board_contract_"
QUEST_ID = re.compile(r"[A-Za-z0-9_]{1,96}")
VISIT_ID = re.compile(r"[0-9a-f]{32}")
BOARD_PATH = re.compile(
    r"BountyBoard_BP_C (?P<map>/Game/Classes/Maps/[A-Za-z0-9_/]+)\.(?P<level>[A-Za-z0-9_]+)"
    r":PersistentLevel\.[A-Za-z0-9_]+"
)
COMPLETE = -10
_INSPECTED_FIELDS = ("step_before", "expected_step", "cash", "xp", "remaining")


def quest_id(value):
    return isinstance(value, str) and QUEST_ID.fullmatch(value) is not None


def board_map(name):
    match = BOARD_PATH.fullmatch(name) if isinstance(name, str) and len(name) <= 512 else None
    if match and match["map"].rsplit("/", 1)[-1] == match["level"] and match["map"] not in PLAYTEST_MAPS:
        return match["map"]
    return None


def menu_selection(target):
    if not isinstance(target, str) or len(target) > 642:
        raise party.RuleError("native_board_contract_selection_invalid")
    visit, separator, remainder = target.partition("|")
    board, second, quest = remainder.partition("|")
    if (not separator or not second or not VISIT_ID.fullmatch(visit)
            or not board_map(board) or not quest_id(quest)):
        raise party.RuleError("native_board_contract_selection_invalid")
    return visit, board, quest


def menu_target(visit, board, quest):
    encoded = f"{visit}|{board}|{quest}"
    menu_selection(encoded)
    return encoded


def require_visit(host, visit):
    if visit is not None and visit != host.zone_epoch:
        raise party.RuleError("native_board_contract_stale_visit")


def parse(fields):
    if len(fields) != 21:
        raise ValueError("Invalid native board result")
    proof = dict(zip(("verb", "mode", "map", "pawn", "board", "quest", "state"), fields[1:8], strict=True))
    proof.update(zip(("step_before", "step_after", "expected_step"),
                     (None if field == "-" else int(field) for field in fields[8:11]), strict=True))
    proof.update(zip(("cash_before", "cash_after", "xp_before", "xp_after", "cash", "xp"),
                     map(int, fields[11:17]), strict=True))
    proof["guid"], units, remaining, proof["consumed"] = fields[17:]
    proof["units"] = None if units == "-" else int(units)
    proof["remaining"] = None if remaining == "-" else int(remaining)
    if (not quest_id(proof["quest"]) or proof["verb"] not in {"start", "turnin"}
            or proof["mode"] not in {"inspect", "apply"} or proof["state"] not in {"qualified", "already", "applied"}
            or min(proof["cash"], proof["xp"]) < 0):
        raise ValueError("Unqualified native board result")
    if proof["guid"] == "-":
        if proof["units"] is not None or proof["remaining"] is not None or proof["consumed"] != "-":
            raise ValueError("Unexpected board deposit result")
    elif (not valid_guid(proof["guid"]) or proof["verb"] != "turnin"
            or proof["units"] is None or proof["remaining"] is None
            or not 0 <= proof["units"] <= 2_147_483_647
            or proof["remaining"] not in {0, max(0, proof["units"] - 1)}
            or (proof["state"] != "applied" and proof["consumed"] != "-")
            or (proof["consumed"] != "-" and not valid_guid(proof["consumed"]))):
        raise ValueError("Unqualified board deposit result")
    return proof


def parse_catalog(fields):
    if len(fields) != 8:
        raise ValueError("Invalid stock board catalog")

    def ids(raw, size):
        records = [] if raw == "-" else raw.split(",")
        if (not size.isascii() or not size.isdigit() or len(size) > 3 or int(size) > 128
                or len(records) != int(size) or not all(map(quest_id, records)) or len(set(records)) != len(records)):
            raise ValueError("Invalid stock board catalog entries")
        return records

    area, pawn, board, offers_count, ready_count, offers, ready = fields[1:]
    offers, ready = ids(offers, offers_count), ids(ready, ready_count)
    if set(offers) & set(ready):
        raise ValueError("A board contract cannot be both offered and ready")
    return {"map": area, "pawn": pawn, "board": board, "offers": offers, "turnins": ready}


def scope(host, actor, selected_board):
    player = host.party["players"].get(actor)
    area = host.last_game.get("map")
    board = native_computer_board.BOARDS.get(area) if selected_board is None else selected_board
    if (not area or area not in host.allowed_maps or board_map(board) != area
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
    for key in (*_INSPECTED_FIELDS, "guid", "units"):
        if result[key] != before[key]:
            raise RuntimeError("The stock board action differs from its inspected recipe")
    cash, xp = result["cash"], result["xp"]
    # A separate stock action can change the balance between inspection and apply.
    # Use the game-thread balance at apply time and verify the exact reward delta.
    if (result["cash_after"] - result["cash_before"], result["xp_after"] - result["xp_before"]) != (cash, xp):
        raise RuntimeError("The stock board action did not reconcile cash and XP")
    record = {"quest_id": result["quest"], "step": step, "actor": actor}
    if step == COMPLETE:
        record.update(cash=cash, xp=xp, wallet=result["cash_after"])
        if result["guid"] != "-":
            guid, left, consumed = result["guid"], result["remaining"], result["consumed"]
            if ((left == 0 and consumed != guid)
                    or (left > 0 and actor == "host" and consumed != "-")
                    or (left > 0 and actor != "host" and consumed in {"-", guid})):
                raise RuntimeError("The stock board action reported the wrong consumed or remaining item")
            record.update(objective_guid=guid, objective_owner=actor, objective_units_before=left + 1,
                          objective_units_consumed=1, objective_units_remaining=left)
            if consumed != "-":
                record["consumed_guid"] = consumed
    return record


def apply_arguments(proof):
    def encode(value):
        return "-" if value is None else str(value)

    inspected = "|".join(encode(proof[key]) for key in _INSPECTED_FIELDS)
    return proof["guid"], encode(proof["units"]), inspected


def _present(proof):
    step = proof["step_after"]
    if (step is None or step != proof["step_before"] or (proof["verb"] == "turnin" and step != COMPLETE)
            or proof["expected_step"] is not None or proof["cash"] or proof["xp"] or proof["guid"] != "-"
            or proof["cash_after"] != proof["cash_before"] or proof["xp_after"] != proof["xp_before"]):
        raise RuntimeError("The stock board did not confirm an unchanged quest")
    return {"effect": "native_quest_present", "quest_id": proof["quest"], "step": step}


async def catalog(host, bridge, actor, selected_board=None, *, specialized=(), excluded=()):
    area, pawn, board = scope(host, actor, selected_board)
    policies = (",".join(sorted(specialized)) or "-", ",".join(sorted(excluded)) or "-")
    try:
        snapshot = await bridge.command(CATALOG, pawn, board, *policies, timeout=5)
    except RuntimeError as error:
        if str(error).startswith(REFUSAL):
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get(CATALOG_KEY, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("board")) != (
            "hosting", area, area, pawn, board):
        raise RuntimeError("The stock board catalog did not identify the current actor and board")
    if not snapshot.get(MENU_REPLY):
        host.last_game, host.last_seen = snapshot, time.monotonic()
    return {CATALOG_KEY: proof}


async def perform(host, bridge, actor, verb, quest, *, board=None, visit=None):
    if verb not in {"start", "turnin"} or not quest_id(quest):
        raise party.RuleError("native_board_contract_arguments")
    require_visit(host, visit)
    area, pawn, board = scope(host, actor, board)
    turning_in = verb == "turnin"
    prior = host.party.get("native_quests", {}).get(quest, {})
    if turning_in and prior.get("step") == COMPLETE:
        return {"effect": "native_quest_already_settled", **prior}

    host.transitioning = True
    try:
        before = await _command(host, bridge, verb, "inspect", area, pawn, board, quest)
        require_visit(host, visit)
        if before["state"] == "already":
            return _present(before)
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

        require_visit(host, visit)
        host.native_checkpoint_unsafe = True
        result = await _command(host, bridge, verb, "apply", area, pawn, board, quest, *apply_arguments(before))
        if result["state"] == "already":
            present = _present(result)
            host.native_checkpoint_unsafe = False
            require_visit(host, visit)
            return present
        require_visit(host, visit)
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
        host.pending_host_save = bool(host.pending_host_save or (actor == "host" and "objective_guid" in record))
        host.native_checkpoint_unsafe = False
        host.control.event("native_board_contract_applied", board=board, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
