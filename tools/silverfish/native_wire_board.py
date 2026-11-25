"""Route stock board presses through the authenticated contract service."""

from dataclasses import dataclass
import time
import uuid

from . import native_board_contract, native_computer_board, native_flood_jammer, native_mosby_tape, native_subway_valve, native_turret_photo, native_wire_quest, party
from . import native_prototype_drill
from .native_gold import valid_guid
from .runtime import write_text
from .travel import BLACK_MARKET, BOROUGH, OBSERVATORY, SCAV_BAR, SCIENTIST_SHOP, SHOPKEEP

HEARTBEAT = "native-wire-board-heartbeat"
FIXTURE = "native-wire-board-fixture"
STOCK_FIXTURE = "native-wire-board-stock-fixture"
STOCK_F_FIXTURE = "native-wire-board-stock-f-fixture"
WAITING = {"view": "loading", "start": "starting", "turnin": "redeeming",
           "stock-view": "loading", "stock-start": "starting", "stock-turnin": "redeeming"}
QUEST = native_wire_quest.QUEST_ID
BOARD = native_wire_quest.BOARD
BOARDS = native_computer_board.BOARDS
MAPS = frozenset(BOARDS)
COUP_REPORT = native_computer_board.COUP_REPORT
DEPOT_DOCUMENTS = native_computer_board.DEPOT_DOCUMENTS
LAKE_JAMMER = native_flood_jammer.BLACK_LAKE
CROSSROADS_JAMMER = native_flood_jammer.CROSSROADS_JAMMER
LENNY = native_flood_jammer.LENNY
SPRATT = native_flood_jammer.SPRATT
QUESTS = {
    SHOPKEEP: frozenset({native_computer_board.MOLASSES.quest_id}),
    OBSERVATORY: frozenset({native_computer_board.SAVLO_KEYCARD.quest_id, native_computer_board.DEAD_DROP.quest_id,
                            native_computer_board.BLACK_BOX.quest_id, CROSSROADS_JAMMER.quest, LAKE_JAMMER.quest}),
    BOROUGH: frozenset({QUEST, native_computer_board.QUEST_ID, native_turret_photo.QUEST_ID, COUP_REPORT.quest_id,
                        native_subway_valve.QUEST}),
    SCIENTIST_SHOP: frozenset({native_computer_board.QUEST_ID, native_mosby_tape.MOSBY_QUEST, native_prototype_drill.QUEST_ID}),
    SCAV_BAR: frozenset({DEPOT_DOCUMENTS.quest_id, native_computer_board.POCKET_WATCH.quest_id,
                         native_computer_board.SAVLO_RING.quest_id}),
    BLACK_MARKET: frozenset({native_computer_board.BRANDY.quest_id, native_computer_board.PUMP_ROOM.quest_id,
                            native_computer_board.STOLEN_BAG.quest_id, native_computer_board.DEAD_DROP.quest_id,
                            native_computer_board.METRO_DEAD_DROP.quest_id,
                            native_computer_board.BLACK_BOX.quest_id, native_computer_board.MISSING_CONTACT.quest_id,
                            LAKE_JAMMER.quest, LENNY.quest, SPRATT.quest}),
}
PREFLIGHTS = {
    native_subway_valve.QUEST: (native_subway_valve.PREFLIGHT, native_subway_valve.BOARD_KEY),
    LAKE_JAMMER.quest: (LAKE_JAMMER.command("preflight"), LAKE_JAMMER.key),
    CROSSROADS_JAMMER.quest: (CROSSROADS_JAMMER.command("preflight"), CROSSROADS_JAMMER.key),
    LENNY.quest: (LENNY.command("preflight"), LENNY.key),
    SPRATT.quest: (SPRATT.command("preflight"), SPRATT.key),
    QUEST: (native_wire_quest.PREFLIGHT, "native_wire_quest"),
    native_turret_photo.QUEST_ID: (native_turret_photo.PREFLIGHT, "native_turret_photo"),
    native_mosby_tape.MOSBY_QUEST: (native_mosby_tape.READINGS_PREFLIGHT, "native_mosby_readings"),
    native_prototype_drill.QUEST_ID: (native_prototype_drill.PREFLIGHT, native_prototype_drill.KEY),
    **{spec.quest_id: (spec.command("preflight"), spec.key) for spec in native_computer_board.CONTRACTS.values()},
}
HIDDEN_OFFERS = {"stock_turret_photo_quest_not_offered", "stock_lake_jammer_quest_not_offered", "stock_lenny_quest_not_offered",
                 "stock_crossroads_jammer_quest_not_offered",
                 "stock_spratt_quest_not_offered",
                 "stock_subway_valve_quest_not_offered",
                 *{f"stock_{spec.key.removeprefix('native_')}_quest_not_offered" for spec in native_computer_board.CONTRACTS.values()}}
STOCK_MENU = {LAKE_JAMMER.quest: LAKE_JAMMER, CROSSROADS_JAMMER.quest: CROSSROADS_JAMMER}


def catalog_specialists(area, board):
    if area == OBSERVATORY and board == native_flood_jammer.OBSERVATORY_BOUNTY_BOARD:
        return native_flood_jammer.OBSERVATORY_BOUNTIES
    if board is not None and board != BOARDS.get(area):
        return frozenset()
    # These contracts still use the existing authored action handlers.
    return QUESTS.get(area, frozenset()) - {native_mosby_tape.MOSBY_QUEST, native_prototype_drill.QUEST_ID}


def renew(bridge, area, timestamp):
    if bridge.local_bridge is not None:
        bridge.local_bridge.publish_stock_board_heartbeat(timestamp)
    elif area in MAPS:
        write_text(bridge.directory / HEARTBEAT, f"{timestamp}\n")


def stock_fixture_board(area, target):
    if target is None:
        return ()
    if not area or native_board_contract.board_map(target) != area:
        raise party.RuleError("Choose an exact stock board on the current map")
    return (target,)


def fixture_selection(area, target):
    if target is None:
        return ()
    if not isinstance(target, str) or target not in QUESTS.get(area, ()):
        raise party.RuleError("Choose a supported stock contract on the current board")
    return (target,)


def stock_fixture_selection(area, target, projected=False):
    quest, separator, verb = target.partition("|") if isinstance(target, str) else ("", "", "")
    profile = STOCK_MENU.get(quest)
    supported = profile and profile.board_at(area) is not None
    if projected:
        supported = native_board_contract.quest_id(quest)
    if separator != "|" or verb not in ("start", "turnin") or not supported:
        raise party.RuleError("Choose a supported stock board action on the current map")
    return quest, verb


@dataclass(frozen=True)
class Confirmation:
    identity: tuple
    state: str
    expires: float


@dataclass(frozen=True)
class StockMenu:
    identity: tuple
    offers: frozenset
    turnins: frozenset


class WireBoardView:
    def __init__(self, bridge, control):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.opened = None
        self.stock_open = None
        self.pending = None

    async def sync(self, snapshot, epoch, active, action, *, visit=None):
        events = self.bridge.native_wire_board_requests()
        now = time.monotonic()
        if not active or not epoch:
            self.opened = self.stock_open = self.pending = None
            return snapshot
        if events:
            self.pending = (epoch, now, events[-1])
        if self.pending and (self.pending[0] != epoch or now - self.pending[1] > 2):
            self.pending = None
        if not self.pending:
            return snapshot
        pawn = snapshot.get("pawns", {}).get("local", {}).get("name")
        sequence, opened, kind, area, actor, board, quest = self.pending[2]
        stock_menu = kind.startswith("stock-")
        shown = snapshot.get("native_wire_board_stock" if stock_menu else "native_wire_board", {})
        expected_quest = (quest == "-" and opened == sequence if kind == "stock-view" else
                          native_board_contract.quest_id(quest) if stock_menu else quest in QUESTS.get(area, ()))
        board_matches = native_board_contract.board_map(board) == area if stock_menu else board == BOARDS.get(area)
        if (sequence <= self.latest or area != snapshot.get("map") or actor != pawn or not board_matches
                or not expected_quest
                or tuple(shown.get(name) for name in ("map", "pawn", "board", "quest", "state", "sequence", "open"))
                != (area, actor, board, quest, WAITING[kind], sequence, opened)):
            return snapshot
        self.pending = None
        self.latest = sequence
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:wire-board:{sequence}").hex
        if kind == "stock-view":
            return await self._open_stock_menu(snapshot, epoch, area, pawn, board, sequence, operation, action)
        if stock_menu:
            return await self._stock_menu(snapshot, epoch, area, pawn, board, quest, kind, sequence, opened,
                                          operation, action, visit=epoch if visit is None else visit)
        identity = (epoch, area, pawn, board, quest, opened)
        try:
            state = await self._perform(identity, kind, sequence, operation, action)
            self.control.event("native_wire_board_interaction", sequence=sequence, operation_id=operation,
                               kind=kind, state=state, board=board, quest=quest)
        except (OSError, RuntimeError, ValueError) as error:
            state = "unavailable"
            self.control.event("native_wire_board_interaction_rejected", sequence=sequence, operation_id=operation,
                               kind=kind, reason=str(error), board=board, quest=quest)
        try:
            rendered = await self.bridge.command("native-wire-board-render", str(sequence), area, pawn, state, timeout=2)
            report = rendered.get("native_wire_board", {})
            if tuple(report.get(name) for name in ("map", "pawn", "board", "quest", "sequence", "open", "state")) != (
                    area, pawn, board, quest, sequence, opened, state):
                raise RuntimeError("The stock board view did not verify the current response")
            self.opened = Confirmation(identity, state, time.monotonic() + 30)
            return rendered
        except (OSError, RuntimeError, ValueError) as error:
            self.opened = None
            self.control.event("native_wire_board_render_rejected", operation_id=operation, reason=str(error))
        return snapshot

    async def _open_stock_menu(self, snapshot, epoch, area, pawn, board, sequence, operation, action):
        self.opened = self.stock_open = None
        try:
            catalog = (await action(native_board_contract.CATALOG, operation, board))[native_board_contract.CATALOG_KEY]
            if (catalog.get("map"), catalog.get("board")) != (area, board) or not catalog.get("pawn"):
                raise RuntimeError("The host board catalog did not identify the requested board")
            offers, turnins = catalog["offers"], catalog["turnins"]
            if (not isinstance(offers, list) or not isinstance(turnins, list)
                    or len(offers) > 128 or len(turnins) > 128
                    or not all(native_board_contract.quest_id(quest) for quest in (*offers, *turnins))
                    or len(set(offers + turnins)) != len(offers) + len(turnins)):
                raise RuntimeError("The host board catalog contains invalid options")
            rendered = await self.bridge.command("native-wire-board-stock-open", str(sequence), area, pawn,
                                                  ",".join(offers) or "-", ",".join(turnins) or "-", timeout=3)
            report = rendered.get("native_wire_board_stock", {})
            if tuple(report.get(name) for name in ("map", "pawn", "board", "quest", "state", "sequence", "open")) != (
                    area, pawn, board, "-", "visible", sequence, sequence):
                raise RuntimeError("The game did not verify the native bounty board menu")
            self.stock_open = StockMenu((epoch, area, pawn, board, sequence), frozenset(offers), frozenset(turnins))
            self.control.event("native_wire_board_stock_opened", operation_id=operation, board=board, sequence=sequence,
                               offers=len(offers), turnins=len(turnins))
            return rendered
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            self.control.event("native_wire_board_stock_unavailable", operation_id=operation, board=board, reason=str(error))
            return snapshot

    async def _stock_menu(self, snapshot, epoch, area, pawn, board, quest, kind, sequence, opened,
                          operation, action, *, visit):
        starting = kind == "stock-start"
        menu = self.stock_open
        projected = opened < sequence and menu and menu.identity == (epoch, area, pawn, board, opened)
        if projected:
            authorized = menu.offers if starting else menu.turnins
            if quest not in authorized:
                return snapshot
        else:
            spec = STOCK_MENU.get(quest)
            if spec is None or spec.board_at(area) != board or opened != sequence:
                return snapshot
        verb, state = ("native-quest-start", "accepted") if starting else ("native-quest-turnin", "completed")
        try:
            target = quest
            observatory_jammer = area == OBSERVATORY and quest in native_flood_jammer.OBSERVATORY_BOUNTIES
            if projected and (observatory_jammer or quest not in catalog_specialists(area, board)):
                target = native_board_contract.menu_target(visit, board, quest)
            result = await action(verb, operation, target)
            step = result.get("step")
            valid_step = type(step) is int and (step > 0 if projected and starting else step == (1 if starting else -10))
            if result.get("quest_id") != quest or not valid_step:
                raise RuntimeError("The host did not verify the stock board action")
            if projected:
                self.stock_open = None
            self.control.event("native_wire_board_interaction", sequence=sequence, operation_id=operation,
                               kind=kind, state=state, board=board, quest=quest)
        except (OSError, RuntimeError, ValueError) as error:
            state = "unavailable"
            self.control.event("native_wire_board_interaction_rejected", sequence=sequence, operation_id=operation,
                               kind=kind, reason=str(error), board=board, quest=quest)
        try:
            return await self.bridge.command("native-wire-board-stock-result", str(sequence), area, pawn, quest, state, timeout=2)
        except (OSError, RuntimeError, ValueError) as error:
            self.control.event("native_wire_board_render_rejected", operation_id=operation, reason=str(error))
        return snapshot

    async def _perform(self, identity, kind, sequence, operation, action):
        previous = self.opened
        self.opened = None
        _, area, _pawn, board, quest, opened = identity
        if kind == "view":
            if opened != sequence:
                raise party.RuleError("native_wire_board_stale_view")
        elif (not opened < sequence or not previous or previous.identity != identity
              or previous.state != ("offer" if kind == "start" else "ready") or time.monotonic() >= previous.expires):
            raise party.RuleError("native_wire_board_stale_confirmation")
        if kind == "start":
            if quest == native_mosby_tape.MOSBY_QUEST:
                raise party.RuleError("native_mosby_readings_uses_authored_dialogue_and_station")
            if quest == native_prototype_drill.QUEST_ID:
                raise party.RuleError("native_prototype_drill_uses_authored_mosby_dialogue")
            await action("native-quest-start", operation, quest)
        elif kind == "turnin":
            if quest == native_computer_board.QUEST_ID and area != SCIENTIST_SHOP:
                raise party.RuleError("stock_computer_board_scientist_redemption_required")
            result = await action("native-quest-turnin", operation, quest)
            if result.get("quest_id") == quest and result.get("step") == -10:
                return "completed"
        return await self._inspect(area, board, quest, operation, action)

    async def _inspect(self, area, board, quest, operation, action):
        verb, report = PREFLIGHTS[quest]
        try:
            proof = (await action(verb, operation, None)).get(report, {})
        except (RuntimeError, ValueError) as error:
            if quest != QUEST and str(error) in HIDDEN_OFFERS:
                return "not_offered"
            raise
        field = "id" if quest == QUEST else "quest"
        if quest in (native_mosby_tape.MOSBY_QUEST, native_prototype_drill.QUEST_ID):
            field = "quest_id"
        expected_quest = proof.get(field)
        expected_board = proof.get("target") if quest == native_turret_photo.QUEST_ID else proof.get("board")
        # The authenticated service verifies the authority pawn. Its Unreal name can differ from the receiving client's name.
        if (proof.get("map"), expected_board, expected_quest) != (area, board, quest):
            raise RuntimeError("The host board report did not identify the selected stock contract")
        if quest == QUEST:
            return self._wire_status(proof)
        step = proof.get("step_after")
        last = 3 if quest == native_turret_photo.QUEST_ID else 2
        if quest in (LAKE_JAMMER.quest, CROSSROADS_JAMMER.quest) and step == -7:
            return "completed"
        if "step_after" not in proof or step not in (None, -10, *range(1, last + 1)):
            raise RuntimeError("The host board report did not verify the selected stock quest step")
        if quest == native_mosby_tape.MOSBY_QUEST:
            if step is None:
                return "not_offered"
            if step == -10:
                return "completed"
            return "ready" if valid_guid(proof.get("objective_guid")) and proof.get("objective_owner") in ("host", "guest") else "missing"
        if quest == native_prototype_drill.QUEST_ID:
            if step is None:
                return "not_offered"
            if step == -10:
                return "completed"
            if proof.get("eligible") is not True:
                return "unavailable"
            return "ready" if proof.get("objective_qualified") is True else "missing"
        if step is None:
            return "offer"
        if step == -10:
            return "completed"
        if quest == SPRATT.quest:
            return "ready" if step == 2 and proof.get("objective_qualified") is True else "missing"
        if quest in (native_turret_photo.QUEST_ID, LAKE_JAMMER.quest, CROSSROADS_JAMMER.quest, LENNY.quest,
                     native_subway_valve.QUEST):
            return "ready" if step == last else "missing"
        spec = native_computer_board.CONTRACTS[quest]
        if proof.get("can_turn_in") is not (area == spec.redeem_map):
            raise RuntimeError("The host board report has an unexpected redemption location")
        if area != spec.redeem_map:
            return "elsewhere"
        requires_provenance = spec.pickup_source or spec is native_computer_board.MISSING_CONTACT
        qualified = proof.get("objective_qualified") is True if requires_provenance else valid_guid(proof.get("objective_guid"))
        return "ready" if qualified else "missing"

    @staticmethod
    def _wire_status(proof):
        if proof.get("state") not in native_wire_quest.STEPS:
            raise RuntimeError("The host did not verify the stock Copper Stop preflight")
        if proof.get("state") == "missing":
            return "offer"
        if proof.get("state") == "completed":
            return "completed"
        return "ready" if valid_guid(proof.get("objective_guid")) else "missing"
