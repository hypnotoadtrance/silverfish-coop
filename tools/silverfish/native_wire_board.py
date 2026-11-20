"""Route stock board presses through the authenticated contract service."""

from dataclasses import dataclass
import time
import uuid

from . import native_computer_board, native_flood_jammer, native_mosby_tape, native_subway_valve, native_turret_photo, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BLACK_MARKET, BOROUGH, OBSERVATORY, SCAV_BAR, SCIENTIST_SHOP, SHOPKEEP

HEARTBEAT = "native-wire-board-heartbeat"
FIXTURE = "native-wire-board-fixture"
WAITING = {"view": "loading", "start": "starting", "turnin": "redeeming"}
QUEST = native_wire_quest.QUEST_ID
BOARD = native_wire_quest.BOARD
BOARDS = native_computer_board.BOARDS
MAPS = frozenset(BOARDS)
COUP_REPORT = native_computer_board.COUP_REPORT
DEPOT_DOCUMENTS = native_computer_board.DEPOT_DOCUMENTS
LAKE_JAMMER = native_flood_jammer.BLACK_LAKE
LENNY = native_flood_jammer.LENNY
QUESTS = {
    SHOPKEEP: frozenset({native_computer_board.MOLASSES.quest_id}),
    OBSERVATORY: frozenset({native_computer_board.SAVLO_KEYCARD.quest_id, native_computer_board.DEAD_DROP.quest_id,
                            native_computer_board.BLACK_BOX.quest_id}),
    BOROUGH: frozenset({QUEST, native_computer_board.QUEST_ID, native_turret_photo.QUEST_ID, COUP_REPORT.quest_id,
                        native_subway_valve.QUEST}),
    SCIENTIST_SHOP: frozenset({native_computer_board.QUEST_ID, native_mosby_tape.MOSBY_QUEST}),
    SCAV_BAR: frozenset({DEPOT_DOCUMENTS.quest_id, native_computer_board.POCKET_WATCH.quest_id,
                         native_computer_board.SAVLO_RING.quest_id}),
    BLACK_MARKET: frozenset({native_computer_board.BRANDY.quest_id, native_computer_board.PUMP_ROOM.quest_id,
                            native_computer_board.STOLEN_BAG.quest_id, native_computer_board.DEAD_DROP.quest_id,
                            native_computer_board.BLACK_BOX.quest_id, native_computer_board.MISSING_CONTACT.quest_id,
                            LAKE_JAMMER.quest, LENNY.quest}),
}
PREFLIGHTS = {
    native_subway_valve.QUEST: (native_subway_valve.PREFLIGHT, native_subway_valve.BOARD_KEY),
    LAKE_JAMMER.quest: (LAKE_JAMMER.command("preflight"), LAKE_JAMMER.key),
    LENNY.quest: (LENNY.command("preflight"), LENNY.key),
    QUEST: (native_wire_quest.PREFLIGHT, "native_wire_quest"),
    native_turret_photo.QUEST_ID: (native_turret_photo.PREFLIGHT, "native_turret_photo"),
    native_mosby_tape.MOSBY_QUEST: (native_mosby_tape.READINGS_PREFLIGHT, "native_mosby_readings"),
    **{spec.quest_id: (spec.command("preflight"), spec.key) for spec in native_computer_board.CONTRACTS.values()},
}
HIDDEN_OFFERS = {"stock_turret_photo_quest_not_offered", "stock_lake_jammer_quest_not_offered", "stock_lenny_quest_not_offered",
                 "stock_subway_valve_quest_not_offered",
                 *{f"stock_{spec.key.removeprefix('native_')}_quest_not_offered" for spec in native_computer_board.CONTRACTS.values()}}


def fixture_selection(area, target):
    if target is None:
        return ()
    if not isinstance(target, str) or target not in QUESTS.get(area, ()):
        raise party.RuleError("Choose a supported stock contract on the current board")
    return (target,)


@dataclass(frozen=True)
class Confirmation:
    identity: tuple
    state: str
    expires: float


class WireBoardView:
    def __init__(self, bridge, control):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.opened = None
        self.pending = None

    async def sync(self, snapshot, epoch, active, action):
        events = self.bridge.native_wire_board_requests()
        now = time.monotonic()
        if not active or not epoch or snapshot.get("map") not in MAPS:
            self.opened = self.pending = None
            return snapshot
        if events:
            self.pending = (epoch, now, events[-1])
        if self.pending and (self.pending[0] != epoch or now - self.pending[1] > 2):
            self.pending = None
        if not self.pending:
            return snapshot
        pawn = snapshot.get("pawns", {}).get("local", {}).get("name")
        shown = snapshot.get("native_wire_board", {})
        sequence, opened, kind, area, actor, board, quest = self.pending[2]
        if (sequence <= self.latest or area != snapshot.get("map") or actor != pawn or board != BOARDS.get(area)
                or quest not in QUESTS[area]
                or tuple(shown.get(name) for name in ("map", "pawn", "board", "quest", "state", "sequence", "open"))
                != (area, actor, board, quest, WAITING[kind], sequence, opened)):
            return snapshot
        self.pending = None
        self.latest = sequence
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:wire-board:{sequence}").hex
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
        field = "id" if quest == QUEST else "quest_id" if quest == native_mosby_tape.MOSBY_QUEST else "quest"
        expected_quest = proof.get(field)
        expected_board = proof.get("target") if quest == native_turret_photo.QUEST_ID else proof.get("board")
        # The authenticated service verifies the authority pawn. Its Unreal name can differ from the receiving client's name.
        if (proof.get("map"), expected_board, expected_quest) != (area, board, quest):
            raise RuntimeError("The host board report did not identify the selected stock contract")
        if quest == QUEST:
            return self._wire_status(proof)
        step = proof.get("step_after")
        last = 3 if quest == native_turret_photo.QUEST_ID else 2
        if quest == LAKE_JAMMER.quest and step == -7:
            return "completed"
        if "step_after" not in proof or step not in (None, -10, *range(1, last + 1)):
            raise RuntimeError("The host board report did not verify the selected stock quest step")
        if quest == native_mosby_tape.MOSBY_QUEST:
            if step is None:
                return "not_offered"
            if step == -10:
                return "completed"
            return "ready" if valid_guid(proof.get("objective_guid")) and proof.get("objective_owner") in ("host", "guest") else "missing"
        if step is None:
            return "offer"
        if step == -10:
            return "completed"
        if quest in (native_turret_photo.QUEST_ID, LAKE_JAMMER.quest, LENNY.quest, native_subway_valve.QUEST):
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
