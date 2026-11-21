"""Project qualified, short-lived stock Starburst cues to nearby guests."""

from collections import deque
import math

from .native_guest_drop_record import valid_id, valid_position


KEY = "native_starburst_cues"
KINDS = {"charge": 4000, "blast": 500100}
MAX_CUES = 12


class Playback:
    def __init__(self):
        self.scope = None
        self.seen = deque(maxlen=4 * MAX_CUES)

    async def sync(self, bridge, control, game, frame, epoch, generation):
        if not isinstance(frame, dict) or not valid_id(epoch) or game.get("state") != "connected":
            return game
        area = game.get("map")
        if (frame.get("map"), frame.get("epoch")) != (area, epoch):
            return game
        origin = game.get("pawns", {}).get("local", {}).get("location")
        cues = frame.get("cues")
        if not valid_position(origin) or not isinstance(cues, list) or len(cues) > MAX_CUES:
            return game
        scope = generation, epoch
        if self.scope != scope:
            self.scope = scope
            self.seen.clear()
        arguments, selected = [], {}
        for cue in cues:
            if not isinstance(cue, list) or len(cue) != 4:
                continue
            serial, kind, position, scale = cue
            if (type(serial) is not int or not 0 < serial < 10**15 or serial in self.seen or serial in selected
                    or type(kind) is not str or kind not in KINDS or not valid_position(position)
                    or type(scale) not in {int, float} or not 0 < scale <= 10):
                continue
            if math.dist(origin, position) > KINDS[kind]:
                self.seen.append(serial)
                continue
            selected[serial] = cue
            arguments.append("|".join(map(str, (serial, kind, *position, scale))))
        if not arguments:
            return game
        try:
            snapshot = await bridge.command("native-starburst-play-cues", area, epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_starburst_effect_unavailable", reason=str(error))
            return game
        self.seen.extend(selected)
        for receipt in snapshot.get("ack", {}).get("message", "").split(","):
            number, _, particles = receipt.partition(":")
            if number.isascii() and number.isdecimal() and len(number) <= 15 and particles in {"0", "1"}:
                serial = int(number)
                if serial in selected:
                    _, kind, position, _ = selected.pop(serial)
                    control.event("native_starburst_effect_played", cue=kind, serial=serial, location=position,
                                  map=area, particles=particles == "1")
        return snapshot
