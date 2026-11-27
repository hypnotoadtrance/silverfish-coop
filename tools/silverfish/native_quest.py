"""Route stock board actions to the live contract and authored objective handlers."""

from . import native_broker, native_computer_board, native_flood_jammer, native_meet_broker, native_wire_quest, native_turret_photo, party
from . import native_board_contract, native_mosby_tape, native_prototype_drill, native_subway_valve

COMPLETED = -10

AUTHORED_BOARD_ACTIONS = frozenset({
    native_subway_valve.QUEST, native_mosby_tape.MOSBY_QUEST, native_prototype_drill.QUEST_ID,
    native_meet_broker.QUEST_ID, native_wire_quest.QUEST_ID, native_turret_photo.QUEST_ID,
    *native_computer_board.CONTRACTS, *native_flood_jammer.CONTRACTS, *native_broker.QUESTS,
})


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
            if verb == "native-quest-start":
                return await native_board_contract.perform(host, bridge, actor, "start", target, board=board, visit=visit)
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
    raise party.RuleError("unknown_native_quest")
