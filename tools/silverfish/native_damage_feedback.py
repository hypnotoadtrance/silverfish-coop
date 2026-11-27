"""Deliver stock hit feedback only to the guest who took authoritative damage."""

from .native_guest_drop_record import valid_id

KEY = "native_guest_hurt"
COMMAND = "native-guest-hurt-play"
LIMIT = 12
STATUS_LIMIT = 4 * LIMIT
PSYCHE = 16
FLASH = 32
HIT_OFFSET_LIMIT = 1e9


def valid_cue(cue):
    if not isinstance(cue, list) or len(cue) != 6:
        return False
    serial, amount, x, y, z, mask = cue
    return (type(serial) is int and 0 < serial < 2**53
            and type(amount) in (int, float) and 1 <= amount <= 1000
            and all(type(value) in (int, float) and abs(value) <= HIT_OFFSET_LIMIT for value in (x, y, z))
            and type(mask) is int and (0 <= mask < PSYCHE
                                      or mask == PSYCHE and amount <= 10 and x == y == z == 0
                                      or mask == FLASH and amount <= 10 and x in (0, 1) and y == z == 0))


def parse(fields):
    serial, pawn, amount, x, y, z, mask = fields[1:]
    cue = [int(serial), float(amount), float(x), float(y), float(z), int(mask)]
    if 12 < len(pawn) <= 512 and pawn.startswith("Player_FP_C /Game/") and valid_cue(cue):
        return pawn, cue


def frame(game, epoch, pawn):
    if game.get("state") != "hosting" or not valid_id(epoch):
        return None
    cues = [cue for victim, cue in game.get(KEY, ()) if victim == pawn and valid_cue(cue)]
    return {"map": game["map"], "epoch": epoch, "cues": cues[-LIMIT:]} if cues else None


class Playback:
    def __init__(self):
        self.scope = None
        self.last = {}

    async def sync(self, bridge, control, game, incoming, epoch):
        if game.get("state") != "connected" or not valid_id(epoch) or not isinstance(incoming, dict):
            return game
        area = game.get("map")
        identity = game.get("pawns", {}).get("local", {}).get("name")
        if not identity or (incoming.get("map"), incoming.get("epoch")) != (area, epoch):
            return game
        cues = incoming.get("cues")
        if not isinstance(cues, list) or len(cues) > LIMIT:
            return game
        scope = area, epoch, identity
        if self.scope != scope:
            self.scope, self.last = scope, {}
        latest = {}
        physical_mask = 0
        hurt_voice = False
        for cue in cues:
            if not valid_cue(cue):
                continue
            kind = cue[-1] if cue[-1] >= PSYCHE else 0
            if cue[0] <= self.last.get(kind, 0):
                continue
            if not kind:
                physical_mask |= cue[-1]
                hurt_voice |= cue[1] >= 40
            if kind not in latest or cue[0] > latest[kind][0]:
                latest[kind] = cue
        for kind, cue in sorted(latest.items(), key=lambda item: item[1][0]):
            serial, amount, x, y, z, mask = cue
            if not kind:
                mask = physical_mask
            play_voice = int(not kind and hurt_voice)
            try:
                game = await bridge.command(COMMAND, area, epoch, identity,
                                            *map(str, (serial, amount, x, y, z, mask, play_voice)), timeout=1.5)
            except (OSError, RuntimeError) as error:
                control.event("native_guest_hurt_unavailable", reason=str(error))
                continue
            message = game.get("ack", {}).get("message")
            if message in {"guest hurt feedback played", "guest hurt feedback already played",
                           "guest flashbang feedback played"}:
                self.last[kind] = serial
            if message == "guest flashbang feedback played":
                control.event("native_guest_flashbang_feedback_played", serial=serial, duration=amount,
                              looking_at_it=bool(x))
            if message == "guest hurt feedback played":
                control.event("native_guest_hurt_played", serial=serial, damage=amount, sound_mask=mask,
                              hit_direction=[x, y, z])
        return game
