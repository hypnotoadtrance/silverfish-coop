"""Route two stock station presses through the authenticated exact-tape exchange."""

from dataclasses import dataclass
import time
import uuid

from . import native_mosby_tape as tape, party
from .native_gold import valid_guid


KEY = "native_mosby_station_view"
HEARTBEAT = "native-mosby-station-heartbeat"
FIXTURE = "native-mosby-station-fixture"
RENDER = "native-mosby-station-render"
WAITING = {"view": "checking", "trade": "trading"}
STATES = {"waiting", "armed", "focused", "checking", "trading", "offer", "missing", "exchanged", "unavailable"}


def parse(fields: list[str]) -> dict:
    _, area, pawn, station, state, seq, open_seq, blank, product = fields
    sequence, opened = int(seq), int(open_seq)
    if (area != tape.STONE_MAZE or station != tape.STATION or not pawn.startswith("Player_FP_C ") or len(pawn) > 512
            or state not in STATES or not 0 <= opened <= sequence < 2**53 or (not valid_guid(blank) and blank != "-")
            or (not valid_guid(product) and product != "-") or product != "-" and (state != "exchanged" or product == blank)
            or state in ("trading", "exchanged") and not 0 < opened < sequence
            or state in ("checking", "offer", "missing") and not (sequence and opened == sequence)
            or state in ("offer", "trading", "exchanged") and not valid_guid(blank)
            or state == "exchanged" and not valid_guid(product)
            or state in ("checking", "missing", "unavailable") and (not sequence or blank != "-")
            or state in ("waiting", "armed", "focused") and (sequence or blank != "-" or product != "-")):
        raise ValueError("Invalid local Stone Maze station input proof")
    return dict(map=area, pawn=pawn, station=station, state=state, sequence=sequence, open=opened,
                blank_guid=None if blank == "-" else blank, filled_guid=None if product == "-" else product)


def parse_intent(fields):
    if len(fields) != 7 or fields[0] != "mosby-station":
        return
    _, raw_sequence, raw_open, kind, pawn, station, blank = fields
    if (not all(value.isascii() and value.isdigit() and len(value) <= 16 for value in (raw_sequence, raw_open))
            or not pawn.startswith("Player_FP_C ") or len(pawn) > 512 or station != tape.STATION):
        return
    sequence, opened = int(raw_sequence), int(raw_open)
    if (0 < opened <= sequence < 2**53 and (kind == "view" and opened == sequence and blank == "-"
                                            or kind == "trade" and opened < sequence and valid_guid(blank))):
        return sequence, opened, kind, pawn, None if blank == "-" else blank


@dataclass(frozen=True)
class Offer:
    identity: tuple[str, str, int]
    blank: str
    expires: float


class StationView:
    def __init__(self, bridge, control):
        self.bridge, self.control = bridge, control
        self.pending = None
        self.offer = None
        self.latest = 0

    async def sync(self, snapshot, epoch, active, action):
        events = self.bridge.native_mosby_station_requests()
        if not active or not epoch or snapshot.get("map") != tape.STONE_MAZE:
            self.offer = self.pending = None
            return snapshot
        now = time.monotonic()
        if events:
            self.pending = (epoch, now, events[-1])
        if not self.pending:
            return snapshot
        if epoch != self.pending[0] or now - self.pending[1] > 2:
            self.pending = None
            return snapshot
        sequence, opened, kind, pawn, blank = self.pending[2]
        state = snapshot.get(KEY, {})
        if (sequence <= self.latest or pawn != snapshot.get("pawns", {}).get("local", {}).get("name")
                or tuple(state.get(field) for field in ("pawn", "station", "state", "sequence", "open", "blank_guid")) != (
                    pawn, tape.STATION, WAITING[kind], sequence, opened, blank)):
            return snapshot
        self.pending = None
        self.latest = sequence
        previous, self.offer = self.offer, None
        identity = epoch, pawn, opened
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:stone-maze-station:{sequence}").hex
        product = None
        try:
            if kind == "view":
                proof = (await action(tape.PREFLIGHT, operation, None)).get("native_mosby_tape", {})
                if (tuple(proof.get(field) for field in ("operation", "map", "station", "state", "filled_guid")) != (
                        tape.PREFLIGHT, tape.STONE_MAZE, tape.STATION, "offer", None)
                        or proof.get("give") != {"class_path": tape.BLANK_CLASS, "quantity": 1, "cash": 0}
                        or proof.get("receive") != {"class_path": tape.READINGS_CLASS, "quantity": 1, "cash": 0}):
                    raise RuntimeError("The host did not verify the exact stock tape offer")
                blank = proof.get("blank_guid")
                if blank is not None and not valid_guid(blank):
                    raise RuntimeError("The host did not verify an owned blank tape GUID")
                outcome = "offer" if blank else "missing"
            else:
                if not previous or previous.identity != identity or previous.blank != blank or previous.expires <= now:
                    raise party.RuleError("native_mosby_station_stale_confirmation")
                converted = await action(tape.CONVERT, operation, blank)
                product = converted.get("filled_guid")
                if (converted.get("effect") not in ("stone_maze_tape_exchanged", "stone_maze_tape_already_exchanged")
                        or converted.get("blank_guid") != blank or not valid_guid(product) or product == blank):
                    raise RuntimeError("The host did not verify the exchange of the exact selected blank tape")
                outcome = "exchanged"
        except (ValueError, OSError, RuntimeError) as error:
            outcome, blank, product = "unavailable", None, None
            self.control.event("native_mosby_station_input_rejected", sequence=sequence, operation_id=operation, kind=kind, reason=str(error))
        try:
            rendered = await self.bridge.command(RENDER, str(sequence), str(opened), outcome, blank or "-", product or "-", timeout=3)
            proof = rendered.get(KEY, {})
            if tuple(proof.get(field) for field in ("map", "pawn", "station", "state", "sequence", "open", "blank_guid", "filled_guid")) != (
                    tape.STONE_MAZE, pawn, tape.STATION, outcome, sequence, opened, blank, product):
                raise RuntimeError("The player did not verify the exact stock station response")
            if outcome == "offer":
                self.offer = Offer(identity, blank, time.monotonic() + 30)
            self.control.event("native_mosby_station_input", sequence=sequence, operation_id=operation, kind=kind, state=outcome)
            return rendered
        except (ValueError, OSError, RuntimeError) as error:
            self.control.event("native_mosby_station_render_rejected", sequence=sequence, operation_id=operation, reason=str(error))
        return snapshot
