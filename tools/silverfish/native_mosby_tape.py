"""Trade an owned blank tape at the stock station and settle Stone Maze Readings."""

import asyncio
import time
import uuid

from . import native_host_save, native_rewards, party
from .native_dialogue import MOSBY_QUEST
from .native_gold import valid_guid
from .native_tape_profiles import (
    APPROACHES, BLANK_CLASS, BY_MAP, BY_PREFIX, CONVERSIONS, DRILL, INSTRUCTIONS, PREFLIGHTS, STONE, STONE_MAZE,
    drill_download_unlocked, instructions_download_unlocked,
)
from .travel import SCIENTIST_SHOP


STATION = STONE.station
BOARD = f"BountyBoard_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2"
READINGS_CLASS = STONE.product
ARMOR_CLASS = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
PREFLIGHT, CONVERT, APPROACH = (STONE.verb(action) for action in ("preflight", "convert", "approach"))
READINGS_PREFLIGHT = "native-mosby-readings-preflight"
TURN_IN = "native-mosby-readings-turn-in"
CAPTURE, GRANT = "native-mosby-reward-capture", "native-mosby-reward-grant"
EXCHANGES = STONE.ledger
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
    profile = BY_PREFIX.get(fields[0]) if fields else None
    if not profile or len(fields) != (16 if profile in (DRILL, INSTRUCTIONS) else 15):
        raise ValueError("Invalid stock station exchange proof")
    _, operation, area, pawn, station, status, before, after, step, cash, xp, give, receive, debit, credit, *extra = fields
    blank, filled = _optional_guid(before), _optional_guid(after)
    if ((area, station, give, receive, debit, credit) != (profile.map, profile.station, "1", "1", "0", "0")
            or (profile.requires_active_quest and step not in ("1", "2"))
            or (profile == DRILL and (step != "-10" or extra[0] not in ("1", "2", "3")))
            or (profile == INSTRUCTIONS and (step != "-" or extra[0] != "2"))
            or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
            or not (operation == profile.verb("preflight") and status == "offer" and filled is None
                    or operation == profile.verb("convert") and status in ("exchanged", "already_exchanged")
                    and blank and filled and blank != filled)):
        raise ValueError("Invalid stock station exchange proof")
    result = dict(operation=operation, map=area, pawn=pawn, station=station, state=status, blank_guid=blank, filled_guid=filled,
                  quest_step=_step(step), cash=int(cash), xp=int(xp), give={"class_path": BLANK_CLASS, "quantity": 1, "cash": 0},
                  receive={"class_path": profile.product, "quantity": 1, "cash": 0})
    if profile == DRILL:
        result["mosby_drill_test"] = int(extra[0])
    elif profile == INSTRUCTIONS:
        result["console_status"] = int(extra[0])
    return result


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


def _unlocked(host, profile):
    if profile == DRILL and not drill_download_unlocked(host.party):
        raise party.RuleError("native_drill_test_results_durable_completion_required")
    if profile == INSTRUCTIONS:
        from . import native_mindcore

        if not instructions_download_unlocked(host.party) or not native_mindcore._ready(host.last_game, 2):
            raise party.RuleError("native_drill_instructions_compiled_console_required")


async def _command(host, bridge, verb, pawn, *args):
    profile = PREFLIGHTS.get(verb) or CONVERSIONS.get(verb)
    key, area, actor_key, expected = ((profile.key, profile.map, "station", profile.station) if profile
                                      else ("native_mosby_readings", SCIENTIST_SHOP, "board", BOARD))
    snapshot = await bridge.command(verb, pawn, *args, timeout=7)
    proof = snapshot.get(key, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("operation"), proof.get("pawn"),
            proof.get(actor_key)) != ("hosting", area, area, verb, pawn, expected):
        raise RuntimeError("Stock tape proof did not match the exact authoritative pawn and actor")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor, action, target):
    if profile := PREFLIGHTS.get(action):
        if target is not None and not valid_guid(target):
            raise party.RuleError("native_mosby_tape_exact_blank_guid_required")
        pawn = _scope(host, actor, profile.map, guest_only=not profile.allows_host)
        _unlocked(host, profile)
        return {profile.key: await _command(host, bridge, action, pawn, target or "-")}
    if target is not None:
        raise party.RuleError("native_mosby_readings_has_fixed_board")
    pawn = _scope(host, actor, SCIENTIST_SHOP)
    return {"native_mosby_readings": await _command(host, bridge, action, pawn)}


async def approach(host, bridge, actor, variant=None, *, profile=STONE):
    if not host.fixtures:
        raise party.RuleError("native_mosby_tape_approach_unavailable")
    area = host.last_game.get("map")
    if area != profile.map and (profile != STONE or area != SCIENTIST_SHOP):
        raise party.RuleError("native_mosby_tape_exact_zone_unavailable")
    if variant is not None and (area != profile.map or type(variant) is not int or not 1 <= variant <= 8):
        raise party.RuleError("native_mosby_tape_approach_invalid_variant")
    pawn = _scope(host, actor, area, guest_only=area == profile.map and not profile.allows_host)
    _unlocked(host, profile)
    snapshot = await bridge.command(profile.verb("approach"), pawn, *((str(variant),) if variant is not None else ()), timeout=6)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise RuntimeError("The station approach left the qualified stock zone")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    target = profile.station if area == profile.map else BOARD
    if profile == INSTRUCTIONS:
        from . import native_mindcore

        target = native_mindcore.CRADLE
    return {profile.key + "_approach": {"map": area, "pawn": pawn, "target": target}}


async def convert(host, bridge, character, blank_guid, operation, *, profile=STONE):
    if not valid_guid(blank_guid) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_mosby_tape_exact_blank_guid_required")
    pawn = _scope(host, character, profile.map, guest_only=not profile.allows_host)
    _unlocked(host, profile)
    preflight, exchange = profile.verb("preflight"), profile.verb("convert")
    ledger = host.party.get(profile.ledger, {})
    for other in BY_MAP.values():
        trades = host.party.get(other.ledger, {})
        if any(row["operation_id"] == operation and row["owner"] == character and (other != profile or old != blank_guid)
               for old, row in trades.items()):
            raise party.RuleError("operation_id_conflict")
        if other != profile and blank_guid in trades:
            raise party.RuleError("native_mosby_tape_blank_already_exchanged")
    previous = ledger.get(blank_guid)
    if previous:
        if previous["owner"] != character:
            raise party.RuleError("native_mosby_tape_blank_already_exchanged")
        await _command(host, bridge, preflight, pawn, blank_guid)
        return {"effect": profile.effect + "_already_exchanged", **previous}
    host.transitioning = True
    try:
        before = await _command(host, bridge, preflight, pawn, blank_guid)
        if before["blank_guid"] != blank_guid:
            raise party.RuleError("native_mosby_tape_blank_not_owned")
        if profile == INSTRUCTIONS:
            await host.checkpoint_native(bridge, require_guest_frames=frozenset({character}))
        else:
            await host.checkpoint_native(bridge)
        if character == "host":
            if (host.last_game.get("state"), host.last_game.get("map"),
                    host.last_game.get("pawns", {}).get("local", {}).get("name")) != ("hosting", profile.map, pawn):
                raise party.RuleError("native_mosby_tape_interactor_changed")
        else:
            _same_guest(host, character, pawn)
        host.native_checkpoint_unsafe = True
        product = uuid.uuid5(uuid.NAMESPACE_URL, f"{profile.namespace}:{character}:{blank_guid}").hex.upper()
        try:
            after = await _command(host, bridge, exchange, pawn, blank_guid, product)
        except RuntimeError as error:
            if str(error).startswith(("native_mosby_tape_", "stone_maze_tape_", "eather_pump_", "drill_test_results_",
                                      "native_mindcore_refused_", "drill_instructions_", "stock_drill_instructions_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if (after["blank_guid"], after["filled_guid"]) != (blank_guid, product) or any(
                after.get(key) != before.get(key) for key in (
                    "quest_step", "cash", "xp", "give", "receive", "mosby_drill_test", "console_status",
                )):
            raise RuntimeError("The typed conversion did not reconcile with its stock offer")
        candidate = party.load_party(host.party)
        receipt = {"owner": character, "blank_guid": blank_guid, "filled_guid": product, "operation_id": operation}
        if profile != STONE:
            receipt.update(map=profile.map, station=profile.station, class_path=profile.product)
        if profile == DRILL:
            receipt["mosby_drill_test"] = after["mosby_drill_test"]
        if profile == INSTRUCTIONS:
            receipt["console_status"] = after["console_status"]
        candidate.setdefault(profile.ledger, {})[blank_guid] = receipt
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
        host.party, host.native_checkpoint_unsafe = candidate, False
        host.control.event(profile.key + "_committed", revision=revision, **receipt)
        return {"effect": profile.effect + "_exchanged", **receipt}
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
