"""Share stock switch positions, linked lights, and world prop power."""

import re
import time

from . import native_stock_door, party
from .native_map_preflight import valid_campaign_package


TAG = "native-stock-switch"
KEY = "native_stock_switches"
OBSERVED = "observed_at"
CENSUS = "native_stock_switch_census"
CENSUS_TAG = "native-stock-switch-census"
ACTION = TAG + "-interact"
IGNITE = TAG + "-ignite"
REPORT = TAG + "-report"
PROJECT = TAG + "-project"
RELEASE_LOCAL = TAG + "-release-local"
ACTIONS = (ACTION, IGNITE, REPORT)
TARGET = "stock-switch:"
PROP_TARGET = "stock-prop-switch:"
IGNITION_TARGET = "stock-switch-ignite:"
OUTCOME_TARGET = "stock-switch-state:"
_ACTOR = re.compile(r"[A-Za-z0-9_-]+_C (/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_-]+")
_LAMP_STATES = {"intact", "broken", "removed"}


def actor_package(actor):
    match = _ACTOR.fullmatch(actor) if isinstance(actor, str) and len(actor) <= 384 else None
    if match and valid_campaign_package(match[1]):
        return match[1]
    return None


def target_actor(value):
    if isinstance(value, str):
        prefix, _, actor = value.partition(":")
        if prefix in (TARGET[:-1], PROP_TARGET[:-1]) and actor_package(actor):
            return actor
    return None


def input_target(value):
    if isinstance(value, str) and value.startswith(IGNITION_TARGET):
        actor = value[len(IGNITION_TARGET):]
        if actor_package(actor):
            return IGNITE, actor
    if actor := target_actor(value):
        return ACTION, actor
    return None


def local_outcome(value):
    if isinstance(value, str) and value.startswith(OUTCOME_TARGET):
        serial, _, rest = value[len(OUTCOME_TARGET):].partition(":")
        power, _, actor = rest.partition(":")
        if re.fullmatch(r"[0-9]{1,16}", serial) and 0 < int(serial) < 2**53 and power in ("0", "1") and actor_package(actor):
            return actor, power, serial
    return None


def independent_actor(target):
    if outcome := local_outcome(target):
        return outcome[0]
    if isinstance(target, str) and target.startswith((PROP_TARGET, IGNITION_TARGET)):
        if switch := input_target(target):
            return switch[1]
    return None


def _version(value):
    return type(value) is int and 0 <= value < 2**53


def parse_version(value):
    if not re.fullmatch(r"[0-9]{1,16}", value) or not _version(version := int(value)):
        raise ValueError("Invalid stock switch observation version")
    return version


def parse(fields, phase, area):
    observed = None
    if len(fields) > 9 and fields[-1].startswith("v="):
        observed = parse_version(fields[-1][2:])
        if phase != "hosting" or not observed:
            raise ValueError("Invalid stock switch observation scope")
        fields = fields[:-1]
    if (len(fields) not in (9, 10) or fields[1] != area or not actor_package(fields[2])
            or phase not in ("hosting", "connected") or fields[3] not in ("0", "1")):
        raise ValueError("Invalid stock switch frame")
    angles = [round(float(value), 2) for value in fields[4:7]]
    if not all(-360 <= value <= 360 for value in angles):
        raise ValueError("Invalid stock switch rotation")
    lights = {}
    for link in fields[8].split(";") if fields[8] != "-" else ():
        name, power, visible = link.split("|")
        if (not actor_package(name) or name in lights
                or power not in ("0", "1") or visible not in ("0", "1")):
            raise ValueError("Invalid linked light state")
        lights[name] = [power == "1", visible == "1"]
    state = {"powered": fields[3] == "1", "angles": angles, "panel": fields[7], "lights": lights}
    if len(fields) == 10:
        kind, _, extra = fields[9].partition("=")
        if kind == "door" and not lights and fields[7] == "-" and actor_package(extra):
            state["door"] = extra
        elif fields[9] in _LAMP_STATES:
            state["lamp"] = fields[9]
        else:
            raise ValueError("Invalid stock switch extension")
    if observed is not None:
        state[OBSERVED] = observed
    return state


def state_payload(row):
    if OBSERVED in row:
        return {field: value for field, value in row.items() if field != OBSERVED}
    return row


def world_frame(game, epoch):
    rows, census = game.get(KEY, {}), game.get(CENSUS)
    if not rows and census is None:
        return {}
    frame = {"map": game["map"], "epoch": epoch, "switches": rows}
    if census is not None:
        frame["sequence"] = census
    return frame


class GuestView:
    def __init__(self):
        self.scope = None
        self.receipts = {}

    def _visit(self, area, epoch):
        if (area, epoch) != self.scope:
            self.scope = area, epoch
            self.receipts.clear()

    def accept(self, gameplay, frame, area, epoch, actor):
        self._visit(area, epoch)
        row = frame["switches"][actor]
        observed = row.get(OBSERVED)
        current = gameplay.get(KEY, {})
        if (current.get("map"), current.get("epoch")) != self.scope:
            current = {"map": area, "epoch": epoch, "switches": {}}
        rows = current["switches"]
        known = rows.get(actor)
        if _version(observed):
            if known is not None and known.get(OBSERVED, 0) >= observed:
                # A local input can still need its exact acknowledgement.
                return {**frame, "switches": {actor: known}}
            if known is None and current.get("sequence", -1) >= observed:
                return None
            self.receipts[actor] = row
        gameplay[KEY] = {**current, "switches": rows | {actor: row}}
        return frame

    def reconcile(self, gameplay, area, epoch):
        self._visit(area, epoch)
        if not self.receipts:
            return
        frame = gameplay.get(KEY, {"map": area, "epoch": epoch, "switches": {}})
        if (frame.get("map"), frame.get("epoch")) != self.scope:
            return
        rows = frame["switches"]
        for actor, row in tuple(self.receipts.items()):
            current = rows.get(actor)
            observed = current.get(OBSERVED, 0) if current is not None else frame.get("sequence", -1)
            if observed >= row[OBSERVED]:
                del self.receipts[actor]
        if self.receipts:
            # The wire decoder still needs the snapshot as sent for its next delta.
            gameplay[KEY] = {**frame, "switches": rows | self.receipts}


def merge_readback(game, actor, row):
    updated = {**game, KEY: {**game.get(KEY, {}), actor: row}}
    door = row.get("door")
    if door:
        name = door if native_stock_door.actor_map(door) == game["map"] else native_stock_door.STREAM_PREFIX + door
        hinges = game.get(native_stock_door.KEY, {})
        if name in hinges:
            updated[native_stock_door.KEY] = {**hinges, name: {**hinges[name], "locked": row["powered"]}}
    return updated


async def interact(host, bridge, character, requested, operation, action=ACTION):
    epoch, _, actor = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    power = None
    message = "stock switch interaction dispatched"
    if action == REPORT:
        actor, _, power = actor.rpartition("@")
        if power not in ("0", "1"):
            raise party.RuleError("stock_switch_state_unavailable")
        message = "stock switch report applied"
    game = host.last_game
    member = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    row = game.get(KEY, {}).get(actor)
    if (action not in ACTIONS or action == IGNITE and (not row or row.get("lamp") != "intact")
            or not epoch or epoch != host.zone_epoch or not actor_package(actor)
            or game.get("state") != "hosting" or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or not member.get("connected")
            or not player or player["downed"] or host.party["wipe"]
            or character in host.party["pending_spawns"] or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_switch_scope_unavailable")
    fingerprint = [action, requested, None]
    receipts = host.party["receipts"].setdefault(character, [])
    for receipt in receipts:
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    try:
        arguments = (actor, member["pawn"]) if power is None else (actor, member["pawn"], power)
        snapshot = await bridge.command(action, *arguments, timeout=3)
    except RuntimeError as error:
        if str(error).startswith("stock_switch_"):
            raise party.RuleError(str(error)) from error
        raise
    row = snapshot.get(KEY, {}).get(actor)
    acknowledgement = snapshot.get("ack", {}).get("message")
    pending = action == ACTION and row and "door" in row and acknowledgement == "stock latch interaction dispatched"
    if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", game["map"]) or row is None
            or (not pending and acknowledgement != message)
            or (power is not None and row.get("powered") is not (power == "1"))):
        raise RuntimeError("The stock switch interaction did not verify")
    if native_stock_door.LOCAL_SCOPE in snapshot:
        if host.zone_epoch != epoch or not native_stock_door.matches_local_scope(host.last_game, snapshot):
            raise party.RuleError("stock_switch_scope_unavailable")
        if host.last_game.get("sequence", 0) <= snapshot["sequence"]:
            host.last_game = merge_readback(host.last_game, actor, row)
    else:
        host.last_game = snapshot
        host.last_seen = time.monotonic()
    interaction = {"actor": actor, "powered": row["powered"]}
    result = {"native_stock_switch_interaction": interaction}
    if pending:
        interaction["pending"] = True
    else:
        result[KEY] = {"map": game["map"], "epoch": epoch, "switches": {actor: row}}
    if action == REPORT:
        interaction["origin"] = "player"
    receipts.append({"id": operation, "request": fingerprint, "result": result})
    del receipts[:-party.RECENT_ACTIONS]
    return result


async def apply_interaction(client, frame, actor, *, sequence=None):
    area, epoch = client.last_game.get("map"), client.zone_epoch
    if (not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch)
            or not actor_package(actor) or not epoch or client.needs_travel or client.server_transitioning
            or not isinstance(rows := frame.get("switches"), dict) or rows.keys() != {actor}):
        return
    frame = client.stock_switch_view.accept(client.gameplay, frame, area, epoch, actor)
    if frame is None:
        return
    game = client.last_game
    projected = await project(client.bridge, game, frame, epoch, sequence=sequence, exact_actor=actor)
    if client.last_game is game and client.zone_epoch == epoch and not client.needs_travel and not client.server_transitioning:
        client.last_game = projected


async def project(bridge, game, frame, epoch, *, sequence=None, exact_actor=None):
    area = game.get("map")
    if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch) or not epoch:
        return game
    local = game.get(KEY, {})
    rows = frame.get("switches")
    if game.get("state") != "connected" or not isinstance(rows, dict):
        return game
    for actor, target in rows.items():
        observed = local.get(actor)
        if (not actor_package(actor) or observed is None and actor != exact_actor or not isinstance(target, dict)
                or type(target.get("powered")) is not bool):
            continue
        version = target.get(OBSERVED)
        if (sequence is None and state_payload(target) == observed
                and not (actor == exact_actor and _version(version))):
            continue
        angles, lights = target.get("angles"), target.get("lights")
        if (not isinstance(angles, list) or len(angles) != 3
                or not all(type(value) in (int, float) and -360 <= value <= 360 for value in angles)
                or not isinstance(lights, dict)):
            continue
        bulbs = [f"{name}|{int(state[0])}|{int(state[1])}" for name, state in lights.items()
                 if actor_package(name) and isinstance(state, list) and len(state) == 2 and all(type(bit) is bool for bit in state)]
        if "lamp" in target or (observed and "lamp" in observed):
            lamp = target.get("lamp")
            if not isinstance(lamp, str) or lamp not in _LAMP_STATES or observed and "lamp" not in observed:
                continue
            bulbs = [lamp]
        elif "door" in target:
            if not actor_package(target["door"]) or observed and observed.get("door") != target["door"]:
                continue
            bulbs = ["door=" + target["door"]]
        if _version(version):
            bulbs.append(f"v={version}")
        if sequence is not None:
            bulbs.append(f"ack={sequence}")
        try:
            updated = await bridge.command(PROJECT, actor, area, str(int(target["powered"])),
                                           *map(str, angles), *bulbs, timeout=2)
        except RuntimeError as error:
            if str(error) != "stock_switch_unavailable":
                raise
            # A streamed level can hide after the guest census and before this command runs.
            continue
        if (updated.get("state"), updated.get("map")) != ("connected", area):
            return game
        if native_stock_door.LOCAL_SCOPE in updated:
            if not native_stock_door.matches_local_scope(game, updated):
                return game
            if game.get("sequence", 0) <= updated["sequence"]:
                game = merge_readback(game, actor, updated[KEY][actor])
        else:
            game = updated
    return game
