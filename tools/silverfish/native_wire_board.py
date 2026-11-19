"""Route two current stock board presses through the authenticated contract service."""

import time
import uuid

from . import native_wire_quest, party
from .native_gold import valid_guid
from .travel import BOROUGH

HEARTBEAT = "native-wire-board-heartbeat"
FIXTURE = "native-wire-board-fixture"
WAITING = {"view": "loading", "start": "starting", "turnin": "redeeming"}
QUEST = native_wire_quest.QUEST_ID
BOARD = native_wire_quest.BOARD


class WireBoardView:
    def __init__(self, bridge, control):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.opened = None
        self.pending = None

    async def sync(self, snapshot, epoch, active, action):
        events = self.bridge.native_wire_board_requests()
        now = time.monotonic()
        if not active or not epoch or snapshot.get("map") != BOROUGH:
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
        if (sequence <= self.latest or (area, actor, board, quest) != (BOROUGH, pawn, BOARD, QUEST)
                or tuple(shown.get(name) for name in ("map", "pawn", "board", "quest", "state", "sequence", "open"))
                != (area, actor, board, quest, WAITING[kind], sequence, opened)):
            return snapshot
        self.pending = None
        self.latest = sequence
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:wire-board:{sequence}").hex
        try:
            state = await self._perform(epoch, pawn, kind, opened, sequence, operation, action)
            self.control.event("native_wire_board_interaction", sequence=sequence, operation_id=operation,
                               kind=kind, state=state)
        except (OSError, RuntimeError, ValueError) as error:
            state = "unavailable"
            self.control.event("native_wire_board_interaction_rejected", sequence=sequence, operation_id=operation,
                               kind=kind, reason=str(error))
        try:
            rendered = await self.bridge.command("native-wire-board-render", str(sequence), area, pawn, state, timeout=2)
            report = rendered.get("native_wire_board", {})
            if (report.get("pawn"), report.get("sequence"), report.get("state")) != (pawn, sequence, state):
                raise RuntimeError("The stock board view did not verify the current response")
            self.opened = (epoch, pawn, opened, state, time.monotonic() + 30)
            return rendered
        except (OSError, RuntimeError, ValueError) as error:
            self.opened = None
            self.control.event("native_wire_board_render_rejected", operation_id=operation, reason=str(error))
        return snapshot

    async def _perform(self, epoch, pawn, kind, opened, sequence, operation, action):
        previous = self.opened or (None, None, None, None, 0)
        self.opened = None
        if kind == "view":
            if opened != sequence:
                raise party.RuleError("native_wire_board_stale_view")
        elif (not opened < sequence or previous[:3] != (epoch, pawn, opened)
              or previous[3] != ("offer" if kind == "start" else "ready") or time.monotonic() >= previous[4]):
            raise party.RuleError("native_wire_board_stale_confirmation")
        if kind == "start":
            await action("native-quest-start", operation, QUEST)
        elif kind == "turnin":
            result = await action("native-quest-turnin", operation, QUEST)
            if result.get("quest_id") == QUEST and result.get("step") == -10:
                return "completed"
        proof = (await action(native_wire_quest.PREFLIGHT, operation, None)).get("native_wire_quest", {})
        if proof.get("state") not in native_wire_quest.STEPS:
            raise RuntimeError("The host did not verify the stock Copper Stop preflight")
        if proof.get("state") == "missing":
            return "offer"
        if proof.get("state") == "completed":
            return "completed"
        return "ready" if valid_guid(proof.get("objective_guid")) else "missing"
