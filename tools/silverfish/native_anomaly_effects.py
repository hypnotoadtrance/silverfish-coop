"""Project stock anomaly cues and passive sensor state to guests."""

from collections import deque
import math
import re
import time

from .native_guest_drop_record import valid_id, valid_position


KEY = "native_anomaly_cues"
VORTEX_SENSORS = "native_vortex_sensors"
ELECTRODE_PHASES = "native_electrode_phases"
ANGLER_COLORS = "native_angler_colors"
ANGLER_MAP = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
OBSERVER_OPACITY = "native_observer_opacity"
CANISTERS = "native_explosive_canisters"
FIXED_EXPLOSIVES = "native_fixed_explosives"
ACID_POOLS = "native_acid_pools"
ACID_POOL_VIEW = "native_acid_pool_view"
FIXED_CUES = ("powerbox", "fixedtank", "fixedfire")
OBSERVER_CUES = ("observersparks", "observerred", "observergreen", "observerblue", "observerpurple", "observergold")
OTHER_PROP_BREAKS = ("ceramic", "platestand", "glass", "paper", "cardboard")
PROP_BREAK_CUES = ("bottle", *OTHER_PROP_BREAKS)
KINDS = {"charge": 4000, "blast": 500100, "vortex": 500100, "electrode": 8100, "canister": 500100, "wandering": 500100,
         **dict.fromkeys(PROP_BREAK_CUES, 8000),
         **dict.fromkeys(("fueldrum", "pipebomb", *FIXED_CUES, *OBSERVER_CUES), 500100)}
CUE_EVENTS = {"pipebomb": "native_pipe_bomb_effect_played", "vortex": "native_vortex_effect_played", "electrode": "native_electrode_effect_played",
              "wandering": "native_wandering_teleport_effect_played", "bottle": "native_bottle_break_played",
              "canister": "native_canister_effect_played", "fueldrum": "native_fuel_drum_effect_played",
              **dict.fromkeys(OTHER_PROP_BREAKS, "native_prop_break_played"),
              **dict.fromkeys(FIXED_CUES, "native_fixed_explosive_effect_played"),
              **dict.fromkeys(OBSERVER_CUES, "native_observer_absorb_played")}
MAX_CUES = 12
MAX_SENSORS = 256
MAX_ANGLER_LAMPS = 32
_MAX_ACID_POOLS = 64
_MAX_DYNAMIC_ACID_SIZE = 4
_ACID_STATE = re.compile(r"([A-Za-z0-9_]{1,96}):[afe]((?::[-.0-9,]+){3}(?::[0-9]{1,2}\.[0-9]{3})?)?")
_ACID_VECTOR = re.compile(r"-?[0-9]{1,8}\.[0-9]{3}(?:,-?[0-9]{1,8}\.[0-9]{3}){2}")


def valid_acid_pools(states):
    if not isinstance(states, list) or len(states) > _MAX_ACID_POOLS:
        return False
    previous = ""
    for state in states:
        match = _ACID_STATE.fullmatch(state) if isinstance(state, str) else None
        if not match or match[1] <= previous:
            return False
        identity, transform = match.groups()
        if identity.startswith("spawned_") != (transform is not None):
            return False
        if transform:
            fields = transform[1:].split(":")
            if len(fields) == 4 and not .001 <= float(fields.pop()) <= _MAX_DYNAMIC_ACID_SIZE:
                return False
            for vector, minimum, maximum in zip(
                fields, (-1e7, -360, .001), (1e7, 360, 100), strict=True,
            ):
                if not _ACID_VECTOR.fullmatch(vector) or not all(
                    minimum <= float(value) <= maximum for value in vector.split(",")
                ):
                    return False
        previous = identity
    return True


class AcidPoolPlayback:
    def __init__(self):
        self.applied = None
        self.retry = None

    async def project(self, bridge, game, frame, epoch):
        area = game.get("map")
        view = game.get(ACID_POOL_VIEW)
        if game.get("state") != "connected" or not isinstance(view, dict) or view.get("map") != area:
            self.applied = None
            self.retry = None
            return game
        if not isinstance(frame, dict) or not valid_id(epoch) or (frame.get("map"), frame.get("epoch")) != (area, epoch):
            return game
        states = frame.get("actors")
        if not valid_acid_pools(states):
            return game
        fingerprint = (area, epoch, tuple(states))
        if self.applied == fingerprint and view.get("epoch") == epoch:
            return game
        if self.retry and self.retry[0] == fingerprint and time.monotonic() < self.retry[1]:
            return game
        try:
            updated = await bridge.command("native-acid-pools-apply", area, epoch, *(states or ["-"]), timeout=1.5)
        except (OSError, ValueError, RuntimeError):
            self.retry = (fingerprint, time.monotonic() + 1)
            return game
        if updated.get(ACID_POOL_VIEW) == {"map": area, "epoch": epoch}:
            self.applied = fingerprint
            self.retry = None
            return updated
        self.retry = (fingerprint, time.monotonic() + 1)
        return game


def valid_angler_colors(row):
    return (isinstance(row, list) and len(row) == 2 and valid_position(row[0])
            and isinstance(row[1], list) and len(row[1]) == 6
            and all(type(channel) in (int, float) and 0 <= channel <= 1 for channel in row[1]))


def valid_observer_opacity(row):
    return (isinstance(row, list) and len(row) == 3 and valid_position(row[0])
            and all(type(value) in (int, float) and 0 <= value <= 1 for value in row[1:]))


def nearby_stock_frame(game, epoch, pawn, key, validator):
    if game.get("state") != "hosting" or game.get("map") != ANGLER_MAP or not valid_id(epoch):
        return None
    target = next((entry.get("location") for entry in game.get("pawns", {}).values() if entry.get("name") == pawn), None)
    if not valid_position(target):
        return None
    nearby = [row for row in game.get(key, ()) if validator(row) and math.dist(row[0], target) <= 15500]
    if not nearby:
        return None
    return {"map": ANGLER_MAP, "epoch": epoch, "actors": nearby[:MAX_ANGLER_LAMPS]}


def angler_frame(game, epoch, pawn):
    frame = nearby_stock_frame(game, epoch, pawn, ANGLER_COLORS, valid_angler_colors)
    return {"map": frame["map"], "epoch": frame["epoch"], "lights": frame["actors"]} if frame else None


def observer_frame(game, epoch, pawn):
    return nearby_stock_frame(game, epoch, pawn, OBSERVER_OPACITY, valid_observer_opacity)


async def project_angler_colors(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or (game.get("state"), game.get("map")) != ("connected", ANGLER_MAP):
        return game
    lights = frame.get("lights")
    if (frame.get("map"), frame.get("epoch")) != (ANGLER_MAP, epoch) or not isinstance(lights, list) or len(lights) > MAX_ANGLER_LAMPS:
        return game
    local = [row for row in game.get(ANGLER_COLORS, []) if valid_angler_colors(row)]
    arguments = []
    for row in lights:
        if not valid_angler_colors(row):
            continue
        position, colors = row
        matches = [candidate for origin, candidate in local if math.dist(origin, position) < 180]
        if len(matches) == 1 and any(abs(a - b) > .006 for a, b in zip(matches[0], colors, strict=True)):
            arguments.append("|".join(f"{value:.5f}" for value in [*position, *colors]))
    if not arguments:
        return game
    return await bridge.command("native-angler-apply-colors", ANGLER_MAP, epoch, *arguments, timeout=1.5)


async def project_observer_opacity(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or (game.get("state"), game.get("map")) != ("connected", ANGLER_MAP):
        return game
    actors = frame.get("actors")
    if (frame.get("map"), frame.get("epoch")) != (ANGLER_MAP, epoch) or not isinstance(actors, list) or len(actors) > MAX_ANGLER_LAMPS:
        return game
    local = [entry["location"] for entry in game.get("native_anomaly_positions", ()) if entry.get("kind") == "observer"
             and valid_position(entry.get("location"))]
    projected = [row for row in game.get(OBSERVER_OPACITY, ()) if valid_observer_opacity(row)]
    arguments = []
    for row in actors:
        if not valid_observer_opacity(row):
            continue
        point, opacity, target = row
        if sum(math.dist(candidate, point) < 180 for candidate in local) != 1:
            continue
        matches = [entry for entry in projected if math.dist(entry[0], point) < 180]
        if len(matches) == 1 and abs(matches[0][2] - target) <= .001 and abs(matches[0][1] - opacity) <= .18:
            continue
        arguments.append("|".join(f"{value:.5f}" for value in [*point, opacity, target]))
    if not arguments:
        return game
    return await bridge.command("native-observer-apply-opacity", ANGLER_MAP, epoch, *arguments, timeout=1.5)


async def project_sensors(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or game.get("state") != "connected":
        return game
    area = game.get("map")
    placements = frame.get("sensors")
    if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not isinstance(placements, list) or len(placements) > MAX_SENSORS:
        return game
    local = {sensor["path"]: sensor["relative"] for sensor in game.get(VORTEX_SENSORS, []) if sensor["path"]}
    arguments = []
    for row in placements:
        if not isinstance(row, list) or len(row) != 2:
            continue
        path, position = row
        if not isinstance(path, str) or path not in local or not valid_position(position):
            continue
        if all(abs(axis) <= 4096 for axis in position) and math.dist(local[path], position) > .05:
            arguments.append("|".join((path, *map(str, position))))
            if len(arguments) == 32:
                break
    if not arguments:
        return game
    return await bridge.command("native-vortex-apply-sensors", area, epoch, *arguments, timeout=1.5)


async def project_electrodes(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or game.get("state") != "connected":
        return game
    area = game.get("map")
    phases = frame.get("electrodes")
    if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not isinstance(phases, list) or len(phases) > MAX_SENSORS:
        return game
    local = game.get(ELECTRODE_PHASES, [])
    placed = {row["path"]: row for row in local if row["path"]}
    generated = [row for row in local if row["path"] is None]
    arguments = []
    for row in phases:
        if not isinstance(row, list) or len(row) != 3:
            continue
        path, position, mode = row
        if not valid_position(position) or type(mode) is not int or mode not in {0, 1, 2}:
            continue
        if isinstance(path, str):
            candidate = placed.get(path)
        elif path is None and mode != 2:
            matches = [entry for entry in generated if math.dist(entry["location"], position) < 3]
            candidate = matches[0] if len(matches) == 1 else None
        else:
            continue
        if candidate and candidate["phase"] != mode:
            arguments.append("|".join(map(str, (path or "-", *position, mode))))
            if len(arguments) == 32:
                break
    if not arguments:
        return game
    return await bridge.command("native-electrode-apply-phase", area, epoch, *arguments, timeout=1.5)


async def project_canisters(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or game.get("state") != "connected":
        return game
    area = game.get("map")
    phases = frame.get("props")
    if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not isinstance(phases, dict) or len(phases) > MAX_SENSORS:
        return game
    local = game.get(CANISTERS, {})
    arguments = []
    for path, mode in phases.items():
        if path in local and type(mode) is int and 0 <= mode <= 2 and local[path]["phase"] != mode:
            arguments.append(f"{path}|{mode}")
            if len(arguments) == 32:
                break
    if not arguments:
        return game
    return await bridge.command("native-canister-apply", area, epoch, *arguments, timeout=1.5)


async def project_fixed_explosives(bridge, game, frame, epoch):
    if not isinstance(frame, dict) or not valid_id(epoch) or game.get("state") != "connected":
        return game
    area = game.get("map")
    props = frame.get("props")
    if (frame.get("map"), frame.get("epoch")) != (area, epoch) or not isinstance(props, dict) or len(props) > MAX_SENSORS:
        return game
    local = game.get(FIXED_EXPLOSIVES, {})
    arguments = []
    for path, destroyed in props.items():
        prop = local.get(path)
        if prop and type(destroyed) is bool and (prop["destroyed"] != destroyed or prop["damaged_mesh"] != destroyed):
            arguments.append(f"{path}|{int(destroyed)}")
            if len(arguments) == 32:
                break
    if not arguments:
        return game
    return await bridge.command("native-fixed-explosives-apply", area, epoch, *arguments, timeout=1.5)


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
            if not isinstance(cue, list) or len(cue) not in {4, 5}:
                continue
            serial, kind, position, scale = cue[:4]
            volume = cue[4] if len(cue) == 5 else 1
            if (type(serial) is not int or not 0 < serial < 10**15 or serial in self.seen or serial in selected
                    or type(kind) is not str or kind not in KINDS or not valid_position(position)
                    or type(scale) not in {int, float} or not 0 < scale <= 10
                    or type(volume) not in {int, float} or not 0 <= volume <= 10
                    or kind in PROP_BREAK_CUES and (scale, volume) != (1, 1)):
                continue
            if math.dist(origin, position) > KINDS[kind]:
                self.seen.append(serial)
                continue
            selected[serial] = kind, position, scale, volume
            arguments.append("|".join(map(str, (serial, kind, *position, scale, volume))))
        if not arguments:
            return game
        try:
            snapshot = await bridge.command("native-anomaly-play-cues", area, epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_anomaly_effect_unavailable", reason=str(error))
            return game
        for receipt in snapshot.get("ack", {}).get("message", "").split(","):
            number, *result = receipt.split(":")
            if not number.isascii() or not number.isdecimal() or len(number) > 15:
                continue
            serial = int(number)
            if serial not in selected:
                continue
            if result == ["already"]:
                self.seen.append(serial)
                selected.pop(serial)
            elif len(result) == 2 and set(result) <= {"0", "1"}:
                self.seen.append(serial)
                kind, position, scale, volume = selected.pop(serial)
                particles, animated = result
                control.event(CUE_EVENTS.get(kind, "native_starburst_effect_played"),
                              cue=kind, serial=serial, location=position, map=area, particles=particles == "1",
                              animated=animated == "1", scale=scale, volume=volume)
        return snapshot
