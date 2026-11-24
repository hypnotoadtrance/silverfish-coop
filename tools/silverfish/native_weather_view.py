"""Relay stock weather events and validate rendered rain snapshots."""

from collections import deque
import math
import re
import time

from .native_guest_drop_record import valid_id, valid_position

KEY = "native_weather_sirens"
LIGHTNING = "native_weather_lightning"
LIGHTNING_TAG = "native-weather-lightning"
LIGHTNING_PLAY = "native-weather-lightning-play"
LIGHTNING_PLAYED_TAG = "native-weather-lightning-played"
LIGHTNING_PLAYBACK = "native_weather_lightning_playback"
LIGHTNING_COMPONENTS = ("flash_registered", "bolt_component", "blast_component", "audio_component")
MAX_LIGHTNING = 4
LIGHTNING_LIFETIME = 3.0
LIGHTNING_MIN_LIFE = 0.08
LOCAL_APPLY = "native-world-view-apply-local"
LOCAL_CAPABILITY = "native_world_view_apply_local"
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


def valid_lightning(row):
    if not isinstance(row, dict):
        return False
    serial, remaining, point, angles = (row.get(key) for key in ("serial", "ttl", "position", "rotation"))
    return (type(serial) is int and 0 < serial < 2**31
            and type(remaining) in (int, float) and 0 < remaining <= LIGHTNING_LIFETIME
            and valid_position(point) and type(angles) is list and len(angles) == 3
            and all(type(axis) in (int, float) and -360 <= axis <= 360 for axis in angles))


def parse_lightning(fields):
    _, serial, expiry, *coordinates = fields
    point = list(map(float, coordinates[:3]))
    angles = list(map(float, coordinates[3:]))
    row = {"serial": int(serial), "ttl": float(expiry), "position": point, "rotation": angles}
    if not valid_lightning(row):
        raise ValueError("Unqualified stock lightning strike")
    return row


def parse_lightning_playback(fields):
    _, epoch, serial, *report = fields
    activation = report[6:]
    if not valid_id(epoch) or len(activation) != len(LIGHTNING_COMPONENTS) or any(
            value not in ("0", "1") for value in activation):
        raise ValueError("Unqualified stock lightning playback")
    proof = parse_lightning([LIGHTNING_TAG, serial, "1", *report[:6]])
    del proof["ttl"]
    proof["epoch"] = epoch
    proof.update(zip(LIGHTNING_COMPONENTS, (value == "1" for value in activation), strict=True))
    return proof


def lightning_frame(game, area, snapshot_age):
    if game.get("state") != "hosting" or game.get("map") != area:
        return []
    return [{**row, "ttl": row["ttl"] - snapshot_age}
            for row in game.get(LIGHTNING, ()) if row["ttl"] - snapshot_age >= LIGHTNING_MIN_LIFE]


class LightningPlayback:
    def __init__(self):
        self.scope = None
        self.played = deque(maxlen=64)

    async def sync(self, bridge, control, game, incoming, epoch, snapshot_age=0.0):
        world = game.get("native_world_view", {})
        area = game.get("map")
        if (not valid_id(epoch) or game.get("state") != "connected"
                or (world.get("mode"), world.get("map"), world.get("epoch")) != ("applied", area, epoch)
                or not isinstance(incoming, list) or not 0 < len(incoming) <= MAX_LIGHTNING):
            return game
        scope = area, epoch
        if scope != self.scope:
            self.scope = scope
            self.played.clear()
        started = time.monotonic()
        for row in incoming:
            if not valid_lightning(row):
                continue
            serial, point, angles = row["serial"], row["position"], row["rotation"]
            remaining = row["ttl"] - snapshot_age - (time.monotonic() - started)
            if serial in self.played or remaining < LIGHTNING_MIN_LIFE:
                continue
            try:
                result = await bridge.command(LIGHTNING_PLAY, epoch, str(serial),
                                              *map(str, (*point, *angles)), timeout=1.5)
            except (OSError, RuntimeError) as error:
                control.event("native_weather_lightning_unavailable", serial=serial, reason=str(error))
                continue
            if (result.get("state"), result.get("map")) != ("connected", area):
                return result
            receipt = result.get("ack", {}).get("message")
            if receipt in {"played", "duplicate"}:
                self.played.append(serial)
                if receipt == "played":
                    proof = result.get(LIGHTNING_PLAYBACK, {})
                    verified = ((proof.get("epoch"), proof.get("serial")) == (epoch, serial)
                                and proof.get("position") == point and proof.get("rotation") == angles
                                and all(type(proof.get(key)) is bool for key in LIGHTNING_COMPONENTS))
                    components = {key: proof[key] for key in LIGHTNING_COMPONENTS} if verified else {}
                    control.event("native_weather_lightning_played", serial=serial, position=point,
                                  rotation=angles, **components)
            game = result
        return game


class SirenPlayback:
    def __init__(self):
        self.scope = None
        self.last_by_source = {}
        self.retry_by_source = {}

    async def sync(self, bridge, control, game, incoming, epoch, snapshot_age=0.0):
        view = game.get("native_world_view", {})
        if (game.get("state") != "connected" or view.get("mode") != "applied"
                or view.get("epoch") != epoch or view.get("map") != game.get("map")
                or not isinstance(incoming, list) or not 0 < len(incoming) <= MAX_SIRENS):
            return game
        scope = game["map"], epoch
        if self.scope != scope:
            self.scope = scope
            self.last_by_source.clear()
            self.retry_by_source.clear()
        latest = {}
        for row in incoming:
            if valid_siren(row) and (row[1] not in latest or row[0] > latest[row[1]][0]):
                latest[row[1]] = row
        started = time.monotonic()
        for serial, source, cue, remaining in latest.values():
            if not 0.2 <= remaining <= 120:
                continue
            now = time.monotonic()
            remaining -= snapshot_age + now - started
            if serial <= self.last_by_source.get(source, 0) or remaining < 0.2:
                continue
            retry_serial, retry_at, delay = self.retry_by_source.get(source, (0, 0, 0.0))
            if retry_serial == serial and now < retry_at:
                continue
            try:
                result = await bridge.command("native-weather-siren-play", epoch, str(serial), source,
                                              str(cue), str(round(remaining, 3)), timeout=1.5)
            except (OSError, RuntimeError) as error:
                delay = min(delay * 2, 4) if retry_serial == serial else 0.5
                self.retry_by_source[source] = serial, time.monotonic() + delay, delay
                control.event("native_weather_siren_unavailable", source=source, serial=serial, reason=str(error))
                continue
            if (result.get("state"), result.get("map")) != ("connected", scope[0]):
                return result
            receipt = result.get("ack", {}).get("message")
            if receipt in {"stock weather siren played", "weather siren already played"}:
                self.last_by_source[source] = serial
                self.retry_by_source.pop(source, None)
                if receipt == "stock weather siren played":
                    control.event("native_weather_siren_played", cue=cue, source=source, serial=serial)
            game = result
        return game
