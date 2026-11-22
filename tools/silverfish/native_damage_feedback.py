"""Deliver stock hit feedback only to the guest who took authoritative damage."""

from .native_guest_drop_record import valid_id

KEY = "native_guest_hurt"
COMMAND = "native-guest-hurt-play"
LIMIT = 12
PSYCHE = 16


def valid_cue(cue):
    if not isinstance(cue, list) or len(cue) != 6:
        return False
    serial, amount, x, y, z, mask = cue
    return (type(serial) is int and 0 < serial < 2**53
            and type(amount) in (int, float) and 1 <= amount <= 1000
            and all(type(value) in (int, float) and abs(value) <= 1 for value in (x, y, z))
            and type(mask) is int and (0 <= mask < PSYCHE
                                      or mask == PSYCHE and amount <= 10 and x == y == z == 0))


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
        self.last = 0

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
            self.scope, self.last = scope, 0
        latest = {}
        for cue in cues:
            if valid_cue(cue) and cue[0] > self.last:
                psyche = cue[-1] == PSYCHE
                if psyche not in latest or cue[0] > latest[psyche][0]:
                    latest[psyche] = cue
        for cue in sorted(latest.values(), key=lambda value: value[0]):
            self.last = cue[0]
            try:
                game = await bridge.command(COMMAND, area, epoch, identity, *map(str, cue), timeout=1.5)
            except (OSError, RuntimeError) as error:
                control.event("native_guest_hurt_unavailable", reason=str(error))
                continue
            if game.get("ack", {}).get("message") == "guest hurt feedback played":
                control.event("native_guest_hurt_played", serial=self.last, damage=cue[1], sound_mask=cue[-1])
        return game
