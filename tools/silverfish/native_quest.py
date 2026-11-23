"""Host-owned actions for the staged Signal Jammer quest."""

from . import native_broker, native_computer_board, native_flood_jammer, native_meet_broker, native_wire_quest, native_rewards, native_turret_photo, party
from . import native_board_contract, native_mosby_tape, native_prototype_drill, native_subway_valve
from .travel import CROSSROADS

QUEST_ID = "Jmr_CrossRoads"
STARTED = 1
READY = 2
COMPLETED = -10
REWARD_CASH = 200
REWARD_XP = 1

AUTHORED_BOARD_ACTIONS = frozenset({
    native_subway_valve.QUEST, native_mosby_tape.MOSBY_QUEST, native_prototype_drill.QUEST_ID,
    native_meet_broker.QUEST_ID, native_wire_quest.QUEST_ID, native_turret_photo.QUEST_ID,
    *native_computer_board.CONTRACTS, *native_flood_jammer.CONTRACTS, *native_broker.QUESTS,
})


def _stock_step(snapshot: dict) -> int | None:
    quest = snapshot.get("native_quest", {})
    if quest.get("id") == QUEST_ID and quest.get("reward_cash") == str(REWARD_CASH):
        try:
            return int(quest["step"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _record(host, actor: str, step: int, eligible: set[str]) -> dict:
    candidate = party.load_party(host.party)
    record = {"quest_id": QUEST_ID, "step": step, "actor": actor}
    if step == COMPLETED:
        recipients = sorted(eligible & candidate["players"].keys())
        for character in recipients:
            player = candidate["players"][character]
            player["quest_xp"] = player.get("quest_xp", 0) + REWARD_XP
            if character != "host":
                native_rewards.queue(player, REWARD_XP)
        candidate["economy"]["wallet"] += REWARD_CASH
        record.update(recipients=recipients, xp=REWARD_XP, cash=REWARD_CASH,
                      wallet=candidate["economy"]["wallet"])
    candidate.setdefault("native_quests", {})[QUEST_ID] = record
    host.checkpoint(candidate)
    host.party = candidate
    return record


async def action(host, bridge, actor: str, verb: str, target: str | None) -> dict:
    stock_verb = verb in ("native-quest-start", "native-quest-turnin")
    visit, board = None, None
    if stock_verb and isinstance(target, str) and "|" in target:
        visit, board, target = native_board_contract.menu_selection(target)
        native_board_contract.require_visit(host, visit)
        area, _, _ = native_board_contract.scope(host, actor, board)
        jammer_board = (board == native_flood_jammer.OBSERVATORY_BOUNTY_BOARD
                        and target in native_flood_jammer.OBSERVATORY_BOUNTIES)
        if target in AUTHORED_BOARD_ACTIONS and board != native_computer_board.BOARDS.get(area) and not jammer_board:
            raise party.RuleError("native_board_contract_requires_authored_board")
    if target == native_subway_valve.QUEST:
        if verb not in ("native-quest-start", "native-quest-turnin"):
            raise party.RuleError("stock_subway_valve_requires_authored_objective")
        operation = native_subway_valve.START if verb == "native-quest-start" else native_subway_valve.TURN_IN
        return await native_subway_valve.board_action(host, bridge, actor, operation)
    if target == native_mosby_tape.MOSBY_QUEST:
        if verb != "native-quest-turnin":
            raise party.RuleError("native_mosby_readings_uses_authored_dialogue_and_station")
        return await native_mosby_tape.turn_in(host, bridge, actor)
    if target == native_prototype_drill.QUEST_ID:
        if verb != "native-quest-turnin":
            raise party.RuleError("native_prototype_drill_uses_authored_mosby_dialogue")
        return await native_prototype_drill.turn_in(host, bridge, actor)
    if target == native_meet_broker.QUEST_ID and verb in ("native-quest-start", "native-quest-turnin"):
        return await native_meet_broker.commit(host, bridge, actor, turning_in=verb == "native-quest-turnin")
    if target == native_wire_quest.QUEST_ID and verb == "native-quest-start":
        return await native_wire_quest.start(host, bridge, actor)
    if target == native_wire_quest.QUEST_ID and verb == "native-quest-turnin":
        return await native_wire_quest.turn_in(host, bridge, actor)
    if target == native_turret_photo.QUEST_ID and verb in ("native-quest-start", "native-quest-turnin"):
        action = "native-turret-photo-start" if verb == "native-quest-start" else "native-turret-photo-turn-in"
        return await native_turret_photo.advance(host, bridge, actor, action)
    collection = native_computer_board.CONTRACTS.get(target) if isinstance(target, str) else None
    if collection and verb in ("native-quest-start", "native-quest-turnin"):
        operation = native_computer_board.start if verb == "native-quest-start" else native_computer_board.turn_in
        return await operation(host, bridge, actor, spec=collection)
    jammer = native_flood_jammer.CONTRACTS.get(target) if isinstance(target, str) else None
    if jammer:
        if verb not in ("native-quest-start", "native-quest-turnin"):
            raise party.RuleError(f"{jammer.key}_requires_the_authored_objective")
        operation = jammer.command("start" if verb == "native-quest-start" else "turn-in")
        selected_board = board if board == native_flood_jammer.OBSERVATORY_BOUNTY_BOARD else None
        return await native_flood_jammer.act(host, bridge, actor, operation, selected_board=selected_board)
    if isinstance(target, str) and target in native_broker.QUESTS:
        return await native_broker.action(host, bridge, actor, verb, quest_id=target)
    if native_board_contract.quest_id(target) and stock_verb:
        return await native_board_contract.perform(
            host, bridge, actor, verb.removeprefix("native-quest-"), target, board=board, visit=visit)
    roles = host.roles()
    player = host.party["players"].get(actor)
    if (not host.fixtures or not player or actor not in host.authoritative_pawns(roles)
            or host.party["wipe"] or host.transitioning):
        raise party.RuleError("native_quest_unavailable")
    if player["downed"]:
        raise party.RuleError("actor_downed")
    if target != QUEST_ID:
        raise party.RuleError("unknown_native_quest")

    current = host.party.get("native_quests", {}).get(QUEST_ID)
    old_step = current["step"] if current else None
    if verb == "native-quest-start":
        step = old_step if current else STARTED
        if current:
            return {"effect": "native_quest_present", **current}
        command = ("native-quest-project", str(step))
    elif verb == "native-quest-objective" and actor == "host":
        if old_step not in (STARTED, READY):
            raise party.RuleError("native_quest_not_active")
        step = READY
        command = (("native-quest-trigger",) if host.last_game["map"] == CROSSROADS and old_step != READY
                   else ("native-quest-project", str(step)))
    elif verb == "native-quest-turnin":
        if old_step == COMPLETED:
            return {"effect": "native_quest_already_settled", **current}
        if old_step != READY:
            raise party.RuleError("native_quest_objective_incomplete")
        step = COMPLETED
        command = ("native-quest-turn-in",)
    else:
        raise party.RuleError("native_quest_action_unavailable")

    snapshot = await bridge.command(*command, timeout=4)
    if _stock_step(snapshot) != step:
        raise RuntimeError("Stock quest read-back differs from the requested state")
    return {"effect": "native_quest_changed", **_record(host, actor, step, set(roles.values()))}


async def project(host, bridge) -> None:
    quest = host.party.get("native_quests", {}).get(QUEST_ID)
    observed = _stock_step(host.last_game)
    if quest and observed != quest["step"]:
        if (quest["step"], observed) in ((STARTED, READY), (READY, COMPLETED)):
            _record(host, "world", observed, set(host.roles().values()))
        else:
            await bridge.command("native-quest-project", str(quest["step"]), timeout=4)
