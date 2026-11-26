"""Share stock switch positions, linked lights, and world prop power."""

import re
import time

from . import native_stock_door, party
from .native_map_preflight import valid_campaign_package


TAG = "native-stock-switch"
KEY = "native_stock_switches"
ACTION = TAG + "-interact"
IGNITE = TAG + "-ignite"
PROJECT = TAG + "-project"
ACTIONS = (ACTION, IGNITE)
TARGET = "stock-switch:"
IGNITION_TARGET = "stock-switch-ignite:"
_ACTOR = re.compile(r"[A-Za-z0-9_-]+_C (/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_-]+")
_LAMP_STATES = {"intact", "broken", "removed"}


def actor_map(actor):
    match = _ACTOR.fullmatch(actor) if isinstance(actor, str) and len(actor) <= 384 else None
    if match and valid_campaign_package(match[1]) and match[1].rsplit("/", 1)[-1] == match[2]:
        return match[1]
    return None


def target_actor(value):
    if isinstance(value, str) and value.startswith(TARGET) and actor_map(actor := value[len(TARGET):]):
        return actor
    return None


def input_target(value):
    if isinstance(value, str) and value.startswith(IGNITION_TARGET):
        actor = value[len(IGNITION_TARGET):]
        if actor_map(actor):
            return IGNITE, actor
    if actor := target_actor(value):
        return ACTION, actor
    return None


def parse(fields, phase, area):
    if (len(fields) not in (9, 10) or fields[1] != area or actor_map(fields[2]) != area
            or phase not in ("hosting", "connected") or fields[3] not in ("0", "1")):
        raise ValueError("Invalid stock switch frame")
    angles = [round(float(value), 2) for value in fields[4:7]]
    if not all(-360 <= value <= 360 for value in angles):
        raise ValueError("Invalid stock switch rotation")
    lights = {}
    for link in fields[8].split(";") if fields[8] != "-" else ():
        name, power, visible = link.split("|")
        if (actor_map(name) != area or name in lights
                or power not in ("0", "1") or visible not in ("0", "1")):
            raise ValueError("Invalid linked light state")
        lights[name] = [power == "1", visible == "1"]
    state = {"powered": fields[3] == "1", "angles": angles, "panel": fields[7], "lights": lights}
    if len(fields) == 10:
        if fields[9] not in _LAMP_STATES:
            raise ValueError("Invalid stock oil lamp state")
        state["lamp"] = fields[9]
    return state


async def interact(host, bridge, character, requested, operation, action=ACTION):
    epoch, _, actor = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    member = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    row = game.get(KEY, {}).get(actor)
    if (action not in ACTIONS or action == IGNITE and (not row or row.get("lamp") != "intact")
            or not epoch or epoch != host.zone_epoch or actor_map(actor) != game.get("map")
            or row is None or game.get("state") != "hosting" or host.transitioning
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
        snapshot = await bridge.command(action, actor, member["pawn"], timeout=3)
    except RuntimeError as error:
        if str(error).startswith("stock_switch_"):
            raise party.RuleError(str(error)) from error
        raise
    row = snapshot.get(KEY, {}).get(actor)
    if ((snapshot.get("state"), snapshot.get("map"), snapshot.get("ack", {}).get("message"))
            != ("hosting", game["map"], "stock switch interaction dispatched") or row is None):
        raise RuntimeError("The stock switch interaction did not verify")
    if native_stock_door.LOCAL_SCOPE in snapshot:
        if host.zone_epoch != epoch or not native_stock_door.matches_local_scope(host.last_game, snapshot):
            raise party.RuleError("stock_switch_scope_unavailable")
        if host.last_game.get("sequence", 0) <= snapshot["sequence"]:
            host.last_game = {**host.last_game, KEY: {**host.last_game.get(KEY, {}), actor: row}}
    else:
        host.last_game = snapshot
        host.last_seen = time.monotonic()
    result = {"native_stock_switch_interaction": {"actor": actor, "powered": row["powered"]},
              KEY: {"map": game["map"], "epoch": epoch, "switches": {actor: row}}}
    receipts.append({"id": operation, "request": fingerprint, "result": result})
    del receipts[:-party.RECENT_ACTIONS]
    return result


async def apply_interaction(client, frame, actor):
    area, epoch = client.last_game.get("map"), client.zone_epoch
    if (not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch)
            or actor_map(actor) != area or not epoch or client.needs_travel or client.server_transitioning
            or not isinstance(rows := frame.get("switches"), dict) or rows.keys() != {actor}):
        return
    await client.cancel_gameplay_snapshot()
    cached = client.gameplay.get(KEY, {})
    if (cached.get("map"), cached.get("epoch")) == (area, epoch):
        client.gameplay[KEY] = {**frame, "switches": {**cached["switches"], **rows}}
    else:
        client.gameplay[KEY] = frame
    client.last_game = await project(client.bridge, client.last_game, frame, client.zone_epoch)


async def project(bridge, game, frame, epoch):
    area = game.get("map")
    if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch) or not epoch:
        return game
    local = game.get(KEY, {})
    rows = frame.get("switches")
    if game.get("state") != "connected" or not local or not isinstance(rows, dict):
        return game
    for actor, target in rows.items():
        if (actor_map(actor) != area or actor not in local or not isinstance(target, dict)
                or target == local[actor] or type(target.get("powered")) is not bool):
            continue
        angles, lights = target.get("angles"), target.get("lights")
        if (not isinstance(angles, list) or len(angles) != 3
                or not all(type(value) in (int, float) and -360 <= value <= 360 for value in angles)
                or not isinstance(lights, dict)):
            continue
        known = local[actor]["lights"]
        bulbs = [f"{name}|{int(state[0])}|{int(state[1])}" for name, state in lights.items()
                 if name in known and isinstance(state, list) and len(state) == 2 and all(type(bit) is bool for bit in state)]
        if "lamp" in target or "lamp" in local[actor]:
            lamp = target.get("lamp")
            if not isinstance(lamp, str) or lamp not in _LAMP_STATES or "lamp" not in local[actor]:
                continue
            bulbs = [lamp]
        updated = await bridge.command(PROJECT, actor, area, str(int(target["powered"])),
                                       *map(str, angles), *bulbs, timeout=2)
        if (updated.get("state"), updated.get("map")) != ("connected", area):
            return game
        if native_stock_door.LOCAL_SCOPE in updated:
            if not native_stock_door.matches_local_scope(game, updated):
                return game
            if game.get("sequence", 0) <= updated["sequence"]:
                game = {**game, KEY: {**game.get(KEY, {}), actor: updated[KEY][actor]}}
        else:
            game = updated
    return game
