"""Relay the active stock weather siren and validate rendered rain snapshots."""

import math
import re
import time

KEY = "native_weather_sirens"
RAIN_FIELDS = ("rain_intensity", "rain_x", "rain_y", "rain_z")
MAX_SIRENS = 4
_SOURCE = re.compile(r"/Game/[A-Za-z0-9_/.:]{1,378}")


def valid_rain(values):
    return len(values) == len(RAIN_FIELDS) and all(
        type(value) in (int, float) and low <= value <= high
        for value, (low, high) in zip(values, ((0, 20), (-1.01, 1.01), (-1.01, 1.01), (0.99, 1.01)))
    )


def valid_siren(cue):
    if not isinstance(cue, list) or len(cue) != 4:
        return False
    serial, source, sound, duration = cue
    return (type(serial) is int and 0 < serial < 2**53
            and isinstance(source, str) and _SOURCE.fullmatch(source) is not None
            and type(sound) is int and 1 <= sound <= 4
            and type(duration) in (int, float) and math.isfinite(duration))


def parse_siren(fields):
    _, serial, source, cue, expiry = fields
    row = [int(serial), source, int(cue), float(expiry)]
    if not valid_siren(row):
        raise ValueError("Unqualified weather siren")
    return row


def siren_frame(game, area):
    now = time.time()
    cues = game.get(KEY, ())
    if game.get("state") != "hosting" or game.get("map") != area:
        return []
    return [[serial, source, sound, round(expiry - now, 3)]
            for serial, source, sound, expiry in cues if 0.2 < expiry - now <= 120]


class SirenPlayback:
    def __init__(self):
        self.scope = None
        self.last = 0

    async def sync(self, bridge, control, game, incoming, epoch):
        view = game.get("native_world_view", {})
        if (game.get("state") != "connected" or view.get("mode") != "applied"
                or view.get("epoch") != epoch or view.get("map") != game.get("map")
                or not isinstance(incoming, list) or not 0 < len(incoming) <= MAX_SIRENS):
            return game
        scope = game["map"], epoch
        if self.scope != scope:
            self.scope, self.last = scope, 0
        for serial, source, cue, remaining in sorted(
            (row for row in incoming if valid_siren(row) and 0.2 <= row[-1] <= 120), key=lambda row: row[0]
        ):
            if serial <= self.last:
                continue
            self.last = serial
            try:
                result = await bridge.command("native-weather-siren-play", epoch, str(serial), source,
                                              str(cue), str(remaining), timeout=1.5)
            except (OSError, RuntimeError) as error:
                control.event("native_weather_siren_unavailable", reason=str(error))
                continue
            if result.get("ack", {}).get("message") == "stock weather siren played":
                control.event("native_weather_siren_played", cue=cue, source=source, serial=serial)
            game = result
        return game
