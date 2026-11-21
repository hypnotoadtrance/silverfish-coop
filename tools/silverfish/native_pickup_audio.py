"""Play short-lived stock pickup cues after the host commits the item claim."""

from collections import deque
import math
import time

from .native_guest_drop_record import valid_class, valid_id, valid_position


KEY = "native_pickup_sounds"
COMMAND = "native-sound-pickup"
MAX_CUES = 16
AUDIBLE_RADIUS = 1800
CUE_LIFETIME = 2.5


def committed(host, pawn, operation, class_path):
    queue = getattr(host, "pickup_audio", None)
    if queue is not None:
        queue.append(host.last_game, host.zone_epoch, pawn, operation, class_path)


class PickupAudio:
    def __init__(self):
        self.cues = deque(maxlen=MAX_CUES)

    def append(self, game, epoch, pawn, operation, class_path):
        if game.get("state") != "hosting" or not valid_id(epoch) or not valid_id(operation) or not valid_class(class_path):
            return
        location = next((row.get("location") for row in game.get("pawns", {}).values() if row.get("name") == pawn), None)
        if valid_position(location):
            self.cues.append((time.monotonic() + CUE_LIFETIME, game.get("map"), epoch,
                              [operation, class_path, [round(axis, 2) for axis in location]]))

    def frame(self, game, epoch, *, operation=None):
        area = game.get("map")
        now = time.monotonic()
        while self.cues and self.cues[0][0] <= now:
            self.cues.popleft()
        cues = [cue for _, source, visit, cue in self.cues
                if (source, visit) == (area, epoch) and (operation is None or cue[0] == operation)]
        return {"map": area, "epoch": epoch, "cues": cues} if cues else None


class Playback:
    def __init__(self):
        self.scope = None
        self.seen = deque(maxlen=2 * MAX_CUES)

    async def sync(self, bridge, control, game, frame, epoch, generation):
        if not isinstance(frame, dict) or game.get("state") not in {"hosting", "connected"}:
            return game
        area = game.get("map")
        if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not valid_id(epoch):
            return game
        scope = generation, epoch
        if scope != self.scope:
            self.scope = scope
            self.seen.clear()
        origin = game.get("pawns", {}).get("local", {}).get("location")
        cues = frame.get("cues")
        if not isinstance(cues, list) or len(cues) > MAX_CUES or not valid_position(origin):
            return game
        args, asked = [], set()
        for cue in cues:
            if not isinstance(cue, list) or len(cue) != 3:
                continue
            operation, class_path, location = cue
            if (not valid_id(operation) or operation in self.seen or operation in asked or not valid_class(class_path)
                    or not valid_position(location)):
                continue
            if math.dist(origin, location) <= AUDIBLE_RADIUS:
                asked.add(operation)
                args.append("|".join((operation, class_path, *map(str, location))))
            else:
                self.seen.append(operation)
        if not args:
            return game
        try:
            snapshot = await bridge.command(COMMAND, area, epoch, *args, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_pickup_sound_unavailable", reason=str(error))
            return game
        played = snapshot.get("ack", {}).get("message", "").split(",")
        for operation in sorted(asked.intersection(played)):
            self.seen.append(operation)
            control.event("native_pickup_sound_played", operation_id=operation, map=area)
        return snapshot
