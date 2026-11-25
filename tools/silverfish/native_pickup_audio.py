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
        if valid_position(location):
            cash = amount if stock_cash_amount(class_path, amount) else None
            self.cues.append((time.monotonic() + CUE_LIFETIME, game.get("map"), epoch,
                              [operation, class_path, [round(axis, 2) for axis in location]], cash))

    def frame(self, game, epoch, *, operation=None):
        area = game.get("map")
        now = time.monotonic()
        while self.cues and self.cues[0][0] <= now:
            self.cues.popleft()
        cues, cash = [], {}
        for _, source, visit, cue, amount in self.cues:
            if (source, visit) == (area, epoch) and (operation is None or cue[0] == operation):
                cues.append(cue)
                if amount is not None:
                    cash[cue[0]] = amount
        if cues:
            frame = {"map": area, "epoch": epoch, "cues": cues}
            if cash:
                frame["cash"] = cash
            return frame


class Playback:
    def __init__(self):
        self.scope = None
        self.seen = deque(maxlen=2 * MAX_CUES)

    def prepare(self, game, frame, epoch, generation):
        if not isinstance(frame, dict) or game.get("state") not in {"hosting", "connected"}:
            return [], {}
        area = game.get("map")
        if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not valid_id(epoch):
            return [], {}
        scope = generation, epoch
        if scope != self.scope:
            self.scope = scope
            self.seen.clear()
        origin = game.get("pawns", {}).get("local", {}).get("location")
        cues = frame.get("cues")
        if not isinstance(cues, list) or len(cues) > MAX_CUES or not valid_position(origin):
            return [], {}
        cash = frame.get("cash", {}) if game["state"] == "connected" else {}
        if not isinstance(cash, dict):
            cash = {}
        args, asked = [], {}
        for cue in cues:
            if not isinstance(cue, list) or len(cue) != 3:
                continue
            operation, class_path, location = cue
            if (not valid_id(operation) or operation in self.seen or operation in asked or not valid_class(class_path)
                    or not valid_position(location)):
                continue
            amount = cash.get(operation)
            if amount is not None and not stock_cash_amount(class_path, amount):
                continue
            if amount is not None or math.dist(origin, location) <= AUDIBLE_RADIUS:
                asked[operation] = amount
                encoded = "|".join((operation, class_path, *map(str, location)))
                args.append(encoded if amount is None else f"{encoded}|{amount}")
            else:
                self.seen.append(operation)
        return args, asked

    def acknowledge(self, control, area, asked, message):
        for operation in sorted(asked.keys() & set(message.split(","))):
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
        try:
            snapshot = await bridge.command(COMMAND, area, epoch, *args, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_pickup_sound_unavailable", reason=str(error))
            return game
        self.acknowledge(control, area, asked, snapshot.get("ack", {}).get("message", ""))
        return snapshot
