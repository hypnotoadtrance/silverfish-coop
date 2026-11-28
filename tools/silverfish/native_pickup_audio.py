"""Play stock pickup sounds and cash notices from committed host claims."""

from collections import deque
import math
import time

from .authored_cash import CASH_RANGES
from .native_guest_drop_record import valid_class, valid_id, valid_position


KEY = "native_pickup_sounds"
COMMAND = "native-sound-pickup"
MAX_CUES = 16
AUDIBLE_RADIUS = 1800
CUE_LIFETIME = 2.5
CASH_NOTICE_LIFETIME = 8


def stock_cash_amount(class_path, amount):
    bounds = CASH_RANGES.get(class_path)
    return type(amount) is int and bounds is not None and bounds[0] <= amount <= bounds[1]


def committed(host, pawn, operation, class_path, *, credit=None):
    queue = getattr(host, "pickup_audio", None)
    if queue is not None:
        queue.append(host.last_game, host.zone_epoch, pawn, operation, class_path,
                     amount=credit["amount"] if credit else None)


class PickupAudio:
    def __init__(self):
        self.cues = deque(maxlen=MAX_CUES)

    def append(self, game, epoch, pawn, operation, class_path, *, amount=None):
        if game.get("state") != "hosting" or not valid_id(epoch) or not valid_id(operation) or not valid_class(class_path):
            return
        location = next((row.get("location") for row in game.get("pawns", {}).values() if row.get("name") == pawn), None)
        location = [round(axis, 2) for axis in location] if valid_position(location) else None
        cash = amount if stock_cash_amount(class_path, amount) else None
        if location is not None or cash is not None:
            self.cues.append((time.monotonic(), game.get("map"), epoch, operation, class_path, location, cash))

    def frame(self, game, epoch, *, operation=None):
        area = game.get("map")
        now = time.monotonic()
        while self.cues and self.cues[0][0] + CASH_NOTICE_LIFETIME <= now:
            self.cues.popleft()
        cues, cash = [], {}
        for created, source, visit, identity, class_path, location, amount in self.cues:
            if (source, visit) != (area, epoch) or (operation is not None and identity != operation):
                continue
            if created + CUE_LIFETIME <= now:
                location = None
            if location is not None or amount is not None:
                cues.append([identity, class_path, location])
            if amount is not None:
                cash[identity] = amount
        if cues:
            frame = {"map": area, "epoch": epoch, "cues": cues}
            if cash:
                frame["cash"] = cash
            return frame


class Playback:
    def __init__(self):
        self.scope = None
        self.seen = deque(maxlen=2 * MAX_CUES)
        self.pending = set()

    def _set_scope(self, generation, area, epoch):
        scope = generation, area, epoch
        if scope != self.scope:
            self.scope = scope
            self.seen.clear()
            self.pending = set()

    def remember_preview(self, control, area, epoch, generation, operation):
        """Remember a stock cue played by this seat before reporting the claim."""
        self._set_scope(generation, area, epoch)
        self.acknowledge(control, area, {operation: None}, operation)

    def prepare(self, game, frame, epoch, generation):
        if not isinstance(frame, dict) or game.get("state") not in {"hosting", "connected"}:
            return [], {}
        area = game.get("map")
        if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not valid_id(epoch):
            return [], {}
        self._set_scope(generation, area, epoch)
        origin = game.get("pawns", {}).get("local", {}).get("location")
        listener_position_available = valid_position(origin)
        cues = frame.get("cues")
        if not isinstance(cues, list) or len(cues) > MAX_CUES:
            return [], {}
        cash = frame.get("cash", {}) if game["state"] == "connected" else {}
        if not isinstance(cash, dict):
            cash = {}
        args, asked = [], {}
        for cue in cues:
            if not isinstance(cue, list) or len(cue) != 3:
                continue
            operation, class_path, location = cue
            if (not valid_id(operation) or operation in self.seen or operation in self.pending
                    or operation in asked or not valid_class(class_path)):
                continue
            amount = cash.get(operation)
            if amount is not None and not stock_cash_amount(class_path, amount):
                continue
            if not listener_position_available or not valid_position(location):
                if amount is not None:
                    asked[operation] = amount
                    args.append(f"{operation}|{class_path}|cash|{amount}")
            elif amount is not None or math.dist(origin, location) <= AUDIBLE_RADIUS:
                asked[operation] = amount
                encoded = "|".join((operation, class_path, *map(str, location)))
                args.append(encoded if amount is None else f"{encoded}|{amount}")
            else:
                self.seen.append(operation)
        return args, asked

    def acknowledge(self, control, area, asked, message):
        for operation in sorted(asked.keys() & set(message.split(","))):
            if operation in self.seen:
                continue
            self.seen.append(operation)
            if asked[operation] is None:
                control.event("native_pickup_sound_played", operation_id=operation, map=area)
            else:
                control.event("native_cash_popup_queued", operation_id=operation, map=area, amount=asked[operation])

    async def sync(self, bridge, control, game, frame, epoch, generation):
        args, asked = self.prepare(game, frame, epoch, generation)
        if not args:
            return game
        area = game["map"]
        # A claim reply and a world snapshot can carry the same cue while the
        # first local command is still awaiting its game-thread receipt.
        pending = self.pending
        pending.update(asked)
        try:
            snapshot = await bridge.command(COMMAND, area, epoch, *args, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_pickup_sound_unavailable", reason=str(error))
            return game
        finally:
            pending.difference_update(asked)
        if pending is not self.pending:
            return game
        self.acknowledge(control, area, asked, snapshot.get("ack", {}).get("message", ""))
        return snapshot if "state" in snapshot else game
