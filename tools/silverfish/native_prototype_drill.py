"""Settle Prototype Drill with its Scientist callback and personal typed rewards."""

import asyncio
import re
import time
import uuid

from . import native_authored_pickup, native_host_save, native_mosby_tape, native_rewards, native_wire_quest, party
from .authored_routes import MAPS as EXPANDED_MAPS
from .campaign import CampaignFormatError
from .native_gold import valid_guid
from .travel import AUTHORED_MAPS, SCIENTIST_SHOP


QUEST_ID = "Clt_DrillHead"
BOARD = native_mosby_tape.BOARD
STATION = f"DataTape_DownloadStation_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.DataTape_DownloadStation_BP_C_1"
ARMOR_CLASS = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
TAPE_CLASS = "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"
SOURCE = native_authored_pickup.PROTOTYPE_DRILL
TAG, REP_TAG, REWARD_TAG = "native-prototype-drill", "native-prototype-drill-rep", "native-prototype-drill-reward"
KEY, REWARD_KEY = "native_prototype_drill", "native_prototype_drill_reward"
PREFLIGHT, TURN_IN = "native-prototype-drill-preflight", "native-prototype-drill-turn-in"
CAPTURE, GRANT = "native-prototype-drill-reward-capture", "native-prototype-drill-reward-grant"
REWARDS = "native_prototype_drill_rewards"
CASH, XP = 10_000, 3
FACTIONS = {2: "commune", 3: "fae", 4: "state"}
_REP_DELTA = {"commune": 300, "fae": -200, "state": -200}
_PHASE_KEYS = ("step", "cash", "xp", "flag", "drill_tested", "tape")
_INT = re.compile(r"0|-?[1-9][0-9]{0,9}")
_STEPS = {"-": None, "1": 1, "2": 2, "-10": -10}
_SAFE_REFUSALS = ("native_prototype_drill_", "prototype_drill_board_", "prototype_drill_objective_",
                  "native_dialogue_mosby_", "stock_wire_quest_")


def _number(value: str) -> int:
    if isinstance(value, str) and _INT.fullmatch(value):
        number = int(value)
        if -2**31 <= number < 2**31:
            return number
    raise ValueError("Invalid Prototype Drill integer")


def _bit(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError("Invalid Prototype Drill flag")
    return value == "1"


def _pawn(value: str) -> bool:
    return isinstance(value, str) and 12 < len(value) <= 512 and value.isprintable() and value.startswith("Player_FP_C ")


def _optional_guid(value: str) -> str | None:
    if value == "-":
        return None
    if not valid_guid(value):
        raise ValueError("Invalid Prototype Drill inventory identity")
    return value


def _reputation(rows: list[list[str]], *, turning_in: bool) -> dict:
    reputation, scales = {}, set()
    for tag, faction, old, old_found, new, new_found, adv, adv_found, next_adv, next_adv_found in rows:
        key = FACTIONS.get(_number(faction))
        if tag != REP_TAG or not key or key in reputation:
            raise ValueError("Unexpected Prototype Drill faction report")
        earlier, later, advantage = _number(old), _number(new), _number(adv)
        existed, exists, credited = _bit(old_found), _bit(new_found), _bit(adv_found)
        if ((adv, adv_found) != (next_adv, next_adv_found) or not credited and advantage != 0
                or not existed and earlier != 0 or not exists and later != 0):
            raise ValueError("Invalid Prototype Drill reputation or advantage report")
        if turning_in:
            scale, remainder = divmod(later - earlier, _REP_DELTA[key])
            if not exists or remainder or scale < 1 or not existed and scale != 1:
                raise ValueError("Invalid Prototype Drill reputation change")
            if existed:
                scales.add(scale)
        elif (earlier, existed) != (later, exists):
            raise ValueError("Prototype Drill preflight changed reputation")
        reputation[key] = dict(before=earlier, before_found=existed, after=later, after_found=exists,
                               advantage=advantage, advantage_found=credited)
    if set(reputation) != set(FACTIONS.values()) or len(scales) > 1:
        raise ValueError("Missing or inconsistent Prototype Drill faction proof")
    return reputation


def parse_redemption(fields: list[str], rows: list[list[str]]) -> dict:
    (tag, operation, area, pawn, board, quest, objective, owner, armor, step_before, step_after,
     cash_before, cash_after, xp_before, xp_after, flag_before, flag_after, tested_before, tested_after,
     station, tape_before, tape_after, eligible) = fields
    objective, armor = _optional_guid(objective), _optional_guid(armor)
    if ((tag, area, board, quest, station) != (TAG, SCIENTIST_SHOP, BOARD, QUEST_ID, STATION) or not _pawn(pawn)
            or operation not in (PREFLIGHT, TURN_IN) or step_before not in _STEPS or step_after not in _STEPS
            or (owner not in ("host", "guest") if objective else owner != "-")):
        raise ValueError("Invalid Prototype Drill board proof")
    before = (_STEPS[step_before], _number(cash_before), _number(xp_before), _number(flag_before),
              _bit(tested_before), _bit(tape_before))
    after = (_STEPS[step_after], _number(cash_after), _number(xp_after), _number(flag_after),
             _bit(tested_after), _bit(tape_after))
    ready = _bit(eligible)
    if (before[2] < 0 or after[2] < 0 or not 0 <= before[3] <= 3 or not 0 <= after[3] <= 3
            or ready and (before[0] not in (1, 2) or before[3:] != (0, False, False))):
        raise ValueError("Invalid Prototype Drill eligibility proof")
    if operation == PREFLIGHT:
        if before != after or armor is not None:
            raise ValueError("Prototype Drill preflight changed stock state")
    elif (not ready or not objective or not armor or objective == armor
          or after != (-10, before[1] + CASH, before[2] + XP, 1, True, True)):
        raise ValueError("Invalid Prototype Drill redemption or station callback proof")
    return dict(operation=operation, map=area, pawn=pawn, board=board, quest_id=quest, station=station,
                objective_guid=objective, objective_owner=None if owner == "-" else owner, armor_guid=armor,
                eligible=ready, reputation=_reputation(rows, turning_in=operation == TURN_IN),
                **dict(zip((f"{key}_before" for key in _PHASE_KEYS), before, strict=True)),
                **dict(zip((f"{key}_after" for key in _PHASE_KEYS), after, strict=True)))


def parse_reward(fields: list[str]) -> dict:
    tag, area, pawn, status, slot, template, guid = fields
    if (tag != REWARD_TAG or not _pawn(pawn) or not native_host_save.SLOT.fullmatch(slot)
            or not valid_guid(template) or not valid_guid(guid)
            or not (status == "captured" and area == SCIENTIST_SHOP and template == guid
                    or status in ("granted", "already_granted") and area in AUTHORED_MAPS | EXPANDED_MAPS and template != guid)):
        raise ValueError("Invalid Prototype Drill Hazard Suit proof")
    return dict(map=area, pawn=pawn, state=status, slot=slot, template_guid=template, guid=guid, class_path=ARMOR_CLASS)


def _award_guid(template: str, actor: str) -> str:
    return uuid.uuid5(uuid.UUID(hex=template), f"prototype-drill-hazard-suit:{actor}").hex.upper()


def escrow_frames(checkpoint: dict) -> list[dict]:
    """Validate outstanding Hazard Suit custody and return the frame for hash checking."""
    state = checkpoint["world"].get("party") or {}
    invalid = "The Prototype Drill Hazard Suit escrow is invalid"
    if type(state) is not dict:
        raise CampaignFormatError(invalid)
    pending = state.get(REWARDS)
    if pending is None:
        return []
    if type(pending) is not dict or set(pending) != {"escrow", "template_guid", "pending"}:
        raise CampaignFormatError(invalid)
    frame, template, delivery = pending["escrow"], pending["template_guid"], pending["pending"]
    quests = state.get("native_quests")
    record = quests.get(QUEST_ID) if type(quests) is dict else None
    if (type(frame) is not dict or set(frame) != {"map", "slot", "size", "sha256"} or frame["map"] != SCIENTIST_SHOP
            or not isinstance(frame["slot"], str) or not native_host_save.SLOT.fullmatch(frame["slot"])
            or type(frame["size"]) is not int or frame["size"] < 16 or not isinstance(frame["sha256"], str)
            or not native_host_save.SHA256.fullmatch(frame["sha256"]) or not valid_guid(template)
            or type(delivery) is not dict or not delivery or type(record) is not dict
            or (record.get("quest_id"), record.get("step"), record.get("mosby_drill_test")) != (QUEST_ID, -10, 1)):
        raise CampaignFormatError(invalid)
    recipients, awards = record.get("recipients"), record.get("armor")
    members = {"host", *checkpoint["characters"]}
    if (type(recipients) is not list or any(not isinstance(actor, str) or actor not in members for actor in recipients)
            or len(recipients) != len(set(recipients)) or "host" not in recipients or type(awards) is not dict
            or set(awards) != set(recipients) or awards["host"] != template or record.get("actor") not in recipients
            or any(actor == "host" or actor not in awards or guid != awards[actor] for actor, guid in delivery.items())):
        raise CampaignFormatError(invalid)
    if (any(actor != "host" and award != _award_guid(template, actor) for actor, award in awards.items())
            or len(set(awards.values())) != len(awards)):
        raise CampaignFormatError(invalid)
    return [frame]


def _authored_objective(state: dict, actor: str, proof: dict) -> bool:
    guid = proof.get("objective_guid")
    if not valid_guid(guid) or proof.get("objective_owner") != ("host" if actor == "host" else "guest"):
        return False
    pickups = state.get(native_authored_pickup.STATE_KEY)
    source = pickups.get(SOURCE) if isinstance(pickups, dict) else None
    if type(source) is not dict:
        return False
    area, actor_name, item = native_authored_pickup.SOURCES[SOURCE]
    if (tuple(source.get(key) for key in ("source", "map", "actor", "class_path", "owner", "guid"))
            != (SOURCE, area, actor_name, item, actor, guid)
            or type(source.get("topic_micro_drill")) is not int or source["topic_micro_drill"] < 1):
        return False
    broker = state.get("native_quests", {}).get("Clt_DrillHead_Brkr")
    return broker is None or isinstance(broker, dict) and broker.get("consumed_guid") != guid


async def _command(host, bridge, action, pawn, *arguments):
    snapshot = await bridge.command(action, pawn, *arguments, timeout=7)
    proof = snapshot.get(KEY, {})
    if (snapshot.get("state"), snapshot.get("map"),
            *tuple(proof.get(key) for key in ("operation", "map", "pawn", "board", "quest_id", "station"))) != (
            "hosting", SCIENTIST_SHOP, action, SCIENTIST_SHOP, pawn, BOARD, QUEST_ID, STATION):
        raise RuntimeError("The Prototype Drill proof belongs to a different authoritative board or pawn")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor, action=PREFLIGHT, target=None):
    if action != PREFLIGHT or target is not None:
        raise party.RuleError("native_prototype_drill_has_fixed_board")
    pawn = native_mosby_tape._scope(host, actor, SCIENTIST_SHOP)
    proof = await _command(host, bridge, PREFLIGHT, pawn)
    issued = host.party.get("native_quests", {}).get(QUEST_ID, {})
    qualified = (proof["eligible"] and issued.get("quest_id") == QUEST_ID and issued.get("step") in (1, 2)
                 and _authored_objective(host.party, actor, proof))
    return {KEY: dict(proof, objective_qualified=qualified)}


async def _reward(host, bridge, action, pawn, slot, template, assigned=None, *, memory=False):
    area = host.last_game["map"]
    arguments = (pawn, assigned) if assigned else ()
    if memory:
        arguments += ("memory",)
    snapshot = await bridge.command(action, slot, template, *arguments, timeout=7)
    proof = snapshot.get(REWARD_KEY, {})
    allowed = ("granted", "already_granted") if assigned else ("captured",)
    if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", area) or proof.get("state") not in allowed
            or tuple(proof.get(key) for key in ("map", "pawn", "slot", "template_guid", "guid", "class_path")) != (
                area, pawn, slot, template, assigned or template, ARMOR_CLASS)):
        raise RuntimeError("The exact Prototype Drill Hazard Suit escrow or personal award did not verify")
    host.last_game, host.last_seen = snapshot, time.monotonic()


def _connected_pawn(host, character):
    member = host.native_recipients().get(character, {})
    pawn = member.get("pawn")
    return pawn if member.get("connected") and _pawn(pawn) else None


async def _deliver(host, bridge, candidate, *, defer_save):
    pending = candidate.get(REWARDS)
    if not pending or not host.last_game.get("native_guest_xp_ready"):
        return frozenset()
    recipients = {actor: pawn for actor in pending["pending"] if (pawn := _connected_pawn(host, actor))}
    if not recipients:
        return frozenset()
    frame, template = pending["escrow"], pending["template_guid"]
    memory = frame.get("pending") is True
    if not memory:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    delivered = {}
    for actor, pawn in recipients.items():
        if defer_save and not await native_wire_quest._retain_reward_guest(host, bridge, actor, pawn):
            continue
        if _connected_pawn(host, actor) != pawn:
            continue
        await _reward(host, bridge, GRANT, pawn, frame["slot"], template, pending["pending"][actor], memory=memory)
        native_rewards.queue(candidate["players"][actor], XP)
        delivered[actor] = pawn
    if not delivered:
        return frozenset()
    recipients = [row for row in native_rewards.pending_recipients(host, candidate) if row[0] in delivered]
    if {actor: pawn for actor, pawn, _ in recipients} != delivered:
        raise RuntimeError("A Prototype Drill armor recipient changed before their stock XP could apply")
    applied = await native_rewards.project(host, bridge, candidate, recipients)
    if applied != delivered.keys():
        raise RuntimeError("A Prototype Drill armor recipient did not receive their exact stock XP")
    for actor in applied:
        del pending["pending"][actor]
    if not pending["pending"]:
        if memory:
            area = host.last_game["map"]
            snapshot = await bridge.command("native-loot-release-escrows", frame["slot"], timeout=3)
            if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
                raise RuntimeError("The stock Prototype Drill armor template release changed campaign map")
            host.last_game, host.last_seen = snapshot, time.monotonic()
        del candidate[REWARDS]
    return applied


def _verify_turn_in(before, after):
    if (tuple(after[f"{key}_before"] for key in _PHASE_KEYS) != tuple(before[f"{key}_after"] for key in _PHASE_KEYS)
            or (after["objective_guid"], after["objective_owner"]) != (before["objective_guid"], before["objective_owner"])
            or not after["eligible"] or not valid_guid(after["armor_guid"]) or after["armor_guid"] == after["objective_guid"]
            or (after["step_after"], after["cash_after"], after["xp_after"], after["flag_after"],
                after["drill_tested_after"], after["tape_after"]) != (
                    -10, before["cash_after"] + CASH, before["xp_after"] + XP, 1, True, True)):
        raise RuntimeError("Prototype Drill completion did not reconcile with its exact preflight")
    for faction, old in before["reputation"].items():
        updated = after["reputation"][faction]
        if (old["after"], old["after_found"], old["advantage"], old["advantage_found"]) != (
                updated["before"], updated["before_found"], updated["advantage"], updated["advantage_found"]):
            raise RuntimeError("Prototype Drill reputation changed between preflight and redemption")


async def turn_in(host, bridge, actor):
    pawn = native_mosby_tape._scope(host, actor, SCIENTIST_SHOP)
    host.transitioning = True
    try:
        prior = host.party.get("native_quests", {}).get(QUEST_ID, {})
        before = await _command(host, bridge, PREFLIGHT, pawn)
        step = before["step_after"]
        if prior.get("step") == -10:
            if prior.get("quest_id") != QUEST_ID or step != -10 or before["flag_after"] < 1:
                raise RuntimeError("The committed Prototype Drill receipt has no completed stock quest")
            return {"effect": "native_quest_already_settled", **prior}
        if step == -10:
            if before["flag_after"] < 1:
                raise RuntimeError("The completed stock Prototype Drill quest has no test flag")
            return {"effect": "native_quest_already_completed", "quest_id": QUEST_ID, "step": -10}
        if (prior.get("quest_id"), prior.get("step")) not in ((QUEST_ID, 1), (QUEST_ID, 2)) or not before["eligible"]:
            raise party.RuleError("native_prototype_drill_stock_dialogue_issue_required")
        if not _authored_objective(host.party, actor, before):
            raise party.RuleError("native_prototype_drill_exact_authored_objective_required")
        objective = before["objective_guid"]
        defer_save = native_wire_quest.defer_reward_save(host)
        if not defer_save:
            await host.checkpoint_native(bridge)
        elif actor != "host" and not await native_wire_quest._retain_reward_guest(host, bridge, actor, pawn):
            raise party.RuleError("native_prototype_drill_stock_interactor_changed")
        recipients = sorted(set(host.roles().values()) & host.party["players"].keys())
        if (host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
                or "host" not in recipients or actor not in recipients or actor in host.party["pending_spawns"]
                or host.party["players"][actor]["downed"]
                or (host.last_game.get("state"), host.last_game.get("map")) != ("hosting", SCIENTIST_SHOP)
                or (host.last_game.get("pawns", {}).get("local", {}).get("name") if actor == "host"
                    else _connected_pawn(host, actor)) != pawn
                or not _authored_objective(host.party, actor, before)):
            raise party.RuleError("native_prototype_drill_stock_interactor_changed")
        host.native_checkpoint_unsafe = True
        try:
            after = await _command(host, bridge, TURN_IN, pawn, objective)
        except RuntimeError as error:
            if str(error).startswith(_SAFE_REFUSALS):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        _verify_turn_in(before, after)
        candidate = party.load_party(host.party)
        armor = after["armor_guid"]
        guests = {character: _award_guid(armor, character) for character in recipients if character != "host"}
        for character in recipients:
            player = candidate["players"][character]
            player["quest_xp"] = player.get("quest_xp", 0) + XP
        candidate["economy"].update(wallet=after["cash_after"], native_wallet_active=True)
        record = {"quest_id": QUEST_ID, "step": -10, "actor": actor, "recipients": recipients, "cash": CASH, "xp": XP,
                  "wallet": after["cash_after"], "consumed_drill_head": objective, "objective_owner": actor,
                  "mosby_drill_test": 1, "armor": {"host": armor, **guests}, "reputation": after["reputation"],
                  "test_results": {"station": STATION, "class_path": TAPE_CLASS}}
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        delivered = frozenset()
        if guests:
            slot = native_host_save.new_slot()
            host_pawn = host.last_game["pawns"]["local"]["name"]
            await _reward(host, bridge, CAPTURE, host_pawn, slot, armor, memory=defer_save)
            frame = ({"slot": slot, "map": SCIENTIST_SHOP, "pending": True} if defer_save else
                     await asyncio.to_thread(native_host_save.record, host.native_profile, slot, SCIENTIST_SHOP, flush=True))
            candidate[REWARDS] = {"escrow": frame, "template_guid": armor, "pending": guests.copy()}
            delivered = await _deliver(host, bridge, candidate, defer_save=defer_save)
        await _commit(host, bridge, candidate, delivered | ({actor} - {"host"}), defer_save=defer_save, record=record)
        return {"effect": "native_quest_changed", **record, KEY: after}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def _commit(host, bridge, candidate, required, *, defer_save, record=None):
    revision = None
    if not defer_save:
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(required))
    elif record is not None:
        host.pending_host_save = True
    host.party, host.native_checkpoint_unsafe = candidate, False
    if record is not None:
        host.control.event("native_prototype_drill_completed", revision=revision, **record)
    else:
        host.control.event("native_prototype_drill_rewards_delivered", revision=revision, recipients=sorted(required))


async def settle(host, bridge):
    reward = host.party.get(REWARDS)
    if (not reward or not host.native_guest or not host.native_profile or not host.native_zones
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") not in host.allowed_maps
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]):
        return
    defer_save = native_wire_quest.defer_reward_save(host)
    if not defer_save and reward["escrow"].get("pending") is True:
        # Wait for retention to return or for a normal checkpoint to materialize this template.
        return
    candidate = party.load_party(host.party)
    host.transitioning = host.native_checkpoint_unsafe = True
    try:
        delivered = await _deliver(host, bridge, candidate, defer_save=defer_save)
        if delivered:
            await _commit(host, bridge, candidate, delivered, defer_save=defer_save)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
