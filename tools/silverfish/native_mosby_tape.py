"""Trade an owned blank tape at the stock station and settle Stone Maze Readings."""

import asyncio
import time
import uuid

from . import native_host_save, native_rewards, party
from .native_dialogue import MOSBY_QUEST
from .native_gold import valid_guid
from .travel import SCIENTIST_SHOP


STONE_MAZE = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P"
STATION = f"DataTape_DownloadStation_BP_C {STONE_MAZE}.StoneMaze_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"
BOARD = f"BountyBoard_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2"
BLANK_CLASS = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
READINGS_CLASS = "/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"
ARMOR_CLASS = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
PREFLIGHT = "native-mosby-tape-preflight"
CONVERT = "native-mosby-tape-convert"
APPROACH = "native-mosby-tape-approach"
READINGS_PREFLIGHT = "native-mosby-readings-preflight"
TURN_IN = "native-mosby-readings-turn-in"
CAPTURE, GRANT = "native-mosby-reward-capture", "native-mosby-reward-grant"
EXCHANGES = "native_mosby_tape_exchanges"
REWARDS = "native_mosby_rewards"
CASH, XP = 7500, 3
FACTIONS = {2: "commune", 4: "state", 5: "coup"}


def _optional_guid(value: str) -> str | None:
    if value == "-":
        return None
    if not valid_guid(value):
        raise ValueError("Invalid Stone Maze inventory GUID")
    return value


def _step(value: str) -> int | None:
    if value == "-":
        return None
    if value not in ("1", "2", "-10"):
        raise ValueError("Invalid Stone Maze quest step")
    return int(value)


def parse_exchange(fields: list[str]) -> dict:
    _, operation, area, pawn, station, status, before, after, step, cash, xp, give, receive, debit, credit = fields
    blank, filled = _optional_guid(before), _optional_guid(after)
    if ((area, station, give, receive, debit, credit) != (STONE_MAZE, STATION, "1", "1", "0", "0")
            or step not in ("1", "2") or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
            or not (operation == PREFLIGHT and status == "offer" and filled is None
                    or operation == CONVERT and status in ("exchanged", "already_exchanged") and blank and filled and blank != filled)):
        raise ValueError("Invalid Stone Maze station exchange proof")
    return dict(operation=operation, map=area, pawn=pawn, station=station, state=status, blank_guid=blank, filled_guid=filled,
                quest_step=int(step), cash=int(cash), xp=int(xp), give={"class_path": BLANK_CLASS, "quantity": 1, "cash": 0},
                receive={"class_path": READINGS_CLASS, "quantity": 1, "cash": 0})


def parse_readings(fields: list[str], rows: list[list[str]]) -> dict:
    (_, operation, area, pawn, board, quest, objective, owner, armor, before, after,
     cash_before, cash_after, xp_before, xp_after, flag_before, flag_after) = fields
    readings, gear = _optional_guid(objective), _optional_guid(armor)
    step_before, step_after = _step(before), _step(after)
    if ((area, board, quest) != (SCIENTIST_SHOP, BOARD, MOSBY_QUEST) or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
            or not (readings is None and owner == "-" or readings is not None and owner in ("host", "guest"))
            or not (operation == READINGS_PREFLIGHT and gear is None and (before, cash_before, xp_before, flag_before)
                    == (after, cash_after, xp_after, flag_after) or operation == TURN_IN and readings and gear
                    and readings != gear and step_before in (1, 2) and step_after == -10)):
        raise ValueError("Invalid Scientist Shop Stone Maze contract proof")
    reputation = {}
    for _, faction, old, old_found, new, new_found, advantage, advantage_after in rows:
        key = FACTIONS.get(int(faction))
        if not key or key in reputation or old_found not in ("0", "1") or new_found not in ("0", "1") or advantage != advantage_after:
            raise ValueError("Invalid Stone Maze faction transition")
        reputation[key] = {"before": int(old), "before_found": old_found == "1", "after": int(new),
                           "after_found": new_found == "1", "advantage": int(advantage)}
    if reputation.keys() != set(FACTIONS.values()):
        raise ValueError("Missing Stone Maze faction report")
    return dict(operation=operation, map=area, pawn=pawn, board=board, quest_id=quest, objective_guid=readings,
                objective_owner=None if owner == "-" else owner, armor_guid=gear, step_before=step_before, step_after=step_after,
                cash_before=int(cash_before), cash_after=int(cash_after), xp_before=int(xp_before), xp_after=int(xp_after),
                flag_before=int(flag_before), flag_after=int(flag_after), reputation=reputation)


def parse_reward(fields: list[str]) -> dict:
    _, area, pawn, state, slot, template, guid = fields
    if (state not in ("captured", "granted", "already_granted") or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
            or not native_host_save.SLOT.fullmatch(slot) or not valid_guid(template) or not valid_guid(guid)
            or state == "captured" and (area != SCIENTIST_SHOP or template != guid) or state != "captured" and template == guid):
        raise ValueError("Invalid typed Stone Maze reward")
    return dict(map=area, pawn=pawn, state=state, slot=slot, template_guid=template, guid=guid, class_path=ARMOR_CLASS)


def _scope(host, actor, area, *, guest_only=False):
    player = host.party["players"].get(actor)
    if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"] or actor in host.party["pending_spawns"]
            or (host.last_game.get("state"), host.last_game.get("map")) != ("hosting", area)
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]):
        raise party.RuleError("native_mosby_tape_exact_zone_unavailable")
    if actor == "host" and guest_only:
        raise party.RuleError("native_mosby_tape_detached_guest_required")
    if actor == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(actor, {})
        pawn = member.get("pawn") if member.get("connected") else None
    if not pawn:
        raise party.RuleError("native_mosby_tape_interactor_unavailable")
    return pawn


def _same_guest(host, actor, pawn):
    now = host.native_recipients().get(actor, {})
    if not now.get("connected") or now.get("pawn") != pawn:
        raise party.RuleError("native_mosby_tape_interactor_changed")


async def _command(host, bridge, verb, pawn, *args):
    key, area, actor_key, expected = (("native_mosby_tape", STONE_MAZE, "station", STATION) if verb in (PREFLIGHT, CONVERT)
                                      else ("native_mosby_readings", SCIENTIST_SHOP, "board", BOARD))
    snapshot = await bridge.command(verb, pawn, *args, timeout=7)
    proof = snapshot.get(key, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("operation"), proof.get("pawn"),
            proof.get(actor_key)) != ("hosting", area, area, verb, pawn, expected):
        raise RuntimeError("Stock Stone Maze proof did not match the exact authoritative pawn and actor")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor, action, target):
    if action == PREFLIGHT:
        if target is not None and not valid_guid(target):
            raise party.RuleError("native_mosby_tape_exact_blank_guid_required")
        pawn = _scope(host, actor, STONE_MAZE, guest_only=True)
        return {"native_mosby_tape": await _command(host, bridge, action, pawn, target or "-")}
    if target is not None:
        raise party.RuleError("native_mosby_readings_has_fixed_board")
    pawn = _scope(host, actor, SCIENTIST_SHOP)
    return {"native_mosby_readings": await _command(host, bridge, action, pawn)}


async def approach(host, bridge, actor, variant=None):
    if not host.fixtures:
        raise party.RuleError("native_mosby_tape_approach_unavailable")
    area = host.last_game.get("map")
    if area not in (STONE_MAZE, SCIENTIST_SHOP):
        raise party.RuleError("native_mosby_tape_exact_zone_unavailable")
    if variant is not None and (area != STONE_MAZE or type(variant) is not int or not 1 <= variant <= 8):
        raise party.RuleError("native_mosby_tape_approach_invalid_variant")
    pawn = _scope(host, actor, area, guest_only=area == STONE_MAZE)
    snapshot = await bridge.command(APPROACH, pawn, *((str(variant),) if variant is not None else ()), timeout=6)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise RuntimeError("Stone Maze approach left the qualified stock zone")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_mosby_tape_approach": {"map": area, "pawn": pawn, "target": STATION if area == STONE_MAZE else BOARD}}


async def convert(host, bridge, character, blank_guid, operation):
    if not valid_guid(blank_guid) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_mosby_tape_exact_blank_guid_required")
    pawn = _scope(host, character, STONE_MAZE, guest_only=True)
    ledger = host.party.get(EXCHANGES, {})
    if any(row["operation_id"] == operation and row["owner"] == character and old != blank_guid for old, row in ledger.items()):
        raise party.RuleError("operation_id_conflict")
    previous = ledger.get(blank_guid)
    if previous:
        if previous["owner"] != character:
            raise party.RuleError("native_mosby_tape_blank_already_exchanged")
        await _command(host, bridge, PREFLIGHT, pawn, blank_guid)
        return {"effect": "stone_maze_tape_already_exchanged", **previous}
    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn, blank_guid)
        if before["blank_guid"] != blank_guid:
            raise party.RuleError("native_mosby_tape_blank_not_owned")
        await host.checkpoint_native(bridge)
        _same_guest(host, character, pawn)
        host.native_checkpoint_unsafe = True
        product = uuid.uuid5(uuid.NAMESPACE_URL, f"silverfish-stone-maze:{character}:{blank_guid}").hex.upper()
        try:
            after = await _command(host, bridge, CONVERT, pawn, blank_guid, product)
        except RuntimeError as error:
            if str(error).startswith(("native_mosby_tape_", "stone_maze_tape_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if (after["blank_guid"], after["filled_guid"]) != (blank_guid, product) or any(
                after[key] != before[key] for key in ("quest_step", "cash", "xp", "give", "receive")):
            raise RuntimeError("The typed Stone Maze conversion did not reconcile with its stock offer")
        candidate = party.load_party(host.party)
        receipt = {"owner": character, "blank_guid": blank_guid, "filled_guid": product, "operation_id": operation}
        candidate.setdefault(EXCHANGES, {})[blank_guid] = receipt
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party, host.native_checkpoint_unsafe = candidate, False
        host.control.event("native_mosby_tape_committed", revision=revision, **receipt)
        return {"effect": "stone_maze_tape_exchanged", **receipt}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def _reward(host, bridge, command, pawn, slot, template, assigned=None):
    area = host.last_game["map"]
    options = (pawn, assigned) if assigned else ()
    snapshot = await bridge.command(command, slot, template, *options, timeout=7)
    proof = snapshot.get("native_mosby_reward", {})
    states = ("granted", "already_granted") if assigned else ("captured",)
    if (tuple(proof.get(key) for key in ("map", "pawn", "slot", "template_guid", "guid")) != (area, pawn, slot, template, assigned or template)
            or proof.get("state") not in states or (snapshot.get("state"), snapshot.get("map")) != ("hosting", area)):
        raise RuntimeError("The exact stock HazOp Gear escrow or personal award did not verify")
    host.last_game, host.last_seen = snapshot, time.monotonic()


async def _deliver(host, bridge, candidate):
    pending = candidate.get(REWARDS)
    if not pending or not host.last_game.get("native_guest_xp_ready"):
        return frozenset()
    eligible = host.native_recipients()
    recipients = [actor for actor in pending["pending"] if eligible.get(actor, {}).get("connected")]
    if not recipients:
        return frozenset()
    frame, template = pending["escrow"], pending["template_guid"]
    await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    for actor in recipients:
        guid = pending["pending"][actor]
        await _reward(host, bridge, GRANT, eligible[actor]["pawn"], frame["slot"], template, guid)
        native_rewards.queue(candidate["players"][actor], XP)
        del pending["pending"][actor]
    if not pending["pending"]:
        del candidate[REWARDS]
    xp = [row for row in native_rewards.pending_recipients(host, candidate) if row[0] in recipients]
    await native_rewards.project(host, bridge, candidate, xp)
    return frozenset(recipients)


async def turn_in(host, bridge, actor):
    pawn = _scope(host, actor, SCIENTIST_SHOP)
    prior = host.party.get("native_quests", {}).get(MOSBY_QUEST, {})
    if prior.get("step") == -10:
        return {"effect": "native_quest_already_settled", **prior}
    host.transitioning = True
    try:
        before = await _command(host, bridge, READINGS_PREFLIGHT, pawn)
        if before["step_after"] == -10:
            return {"effect": "native_quest_already_completed", "quest_id": MOSBY_QUEST, "step": -10}
        objective = before["objective_guid"]
        if before["step_after"] not in (1, 2) or not valid_guid(objective):
            raise party.RuleError("native_mosby_readings_owned_tape_required")
        await host.checkpoint_native(bridge)
        if actor != "host":
            _same_guest(host, actor, pawn)
        host.native_checkpoint_unsafe = True
        try:
            after = await _command(host, bridge, TURN_IN, pawn, objective)
        except RuntimeError as error:
            if str(error).startswith(("native_mosby_tape_", "stone_maze_objective_", "stock_wire_quest_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if ((after["step_before"], after["step_after"], after["objective_guid"], after["objective_owner"], after["flag_after"])
                != (before["step_after"], -10, objective, before["objective_owner"], 1)
                or any(after[key] != before[key] for key in ("cash_before", "xp_before", "flag_before"))
                or after["cash_after"] != before["cash_before"] + CASH or after["xp_after"] != before["xp_before"] + XP
                or not valid_guid(after["armor_guid"])):
            raise RuntimeError("Stone Maze quest turn-in did not reconcile the exact objective, cash, XP, and stock flag")
        candidate = party.load_party(host.party)
        recipients = sorted(set(host.roles().values()) & candidate["players"].keys())
        armor = after["armor_guid"]
        awarded = {actor: armor for actor in recipients if actor == "host"}
        assigned = {actor: uuid.uuid5(uuid.UUID(hex=armor), f"stone-maze-hazop:{actor}").hex.upper()
                    for actor in recipients if actor != "host"}
        awarded.update(assigned)
        if assigned:
            slot = native_host_save.new_slot()
            await _reward(host, bridge, CAPTURE, host.last_game["pawns"]["local"]["name"], slot, armor)
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, SCIENTIST_SHOP, flush=True)
            candidate[REWARDS] = {"escrow": frame, "template_guid": armor, "pending": assigned.copy()}
        delivered = await _deliver(host, bridge, candidate)
        for recipient in recipients:
            player = candidate["players"][recipient]
            player["quest_xp"] = player.get("quest_xp", 0) + XP
        candidate["economy"]["wallet"] = after["cash_after"]
        record = {"quest_id": MOSBY_QUEST, "step": -10, "actor": actor, "recipients": recipients, "cash": CASH, "xp": XP,
                  "wallet": after["cash_after"], "consumed_tape": objective, "objective_owner": actor,
                  "mosby_sm_readings": 1, "armor": awarded, "reputation": after["reputation"]}
        candidate.setdefault("native_quests", {})[MOSBY_QUEST] = record
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=delivered | (frozenset({actor}) if actor != "host" else frozenset()))
        host.party, host.native_checkpoint_unsafe = candidate, False
        host.control.event("native_mosby_readings_completed", revision=revision, **record)
        return {"effect": "native_quest_changed", **record}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def settle(host, bridge):
    if (host.last_game.get("state") != "hosting" or host.last_game.get("map") not in host.allowed_maps
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"] or not host.party.get(REWARDS)):
        return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        delivered = await _deliver(host, bridge, candidate)
        if delivered:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=delivered)
            host.party = candidate
            host.control.event("native_mosby_rewards_delivered", revision=revision, recipients=sorted(delivered))
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        host.stopping = True
        raise
