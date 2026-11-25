"""Share stock switch positions, linked lights, and radio power."""

import time

from . import native_stock_door, party


TAG = "native-stock-switch"
KEY = "native_stock_switches"
ACTION = TAG + "-interact"
TARGET = "stock-switch:"
_CLASSES = {"CircutBox_BP_C", "LightSwitch_BP_C", "LightSwitch_02_BP_C", "Prop_Radio01_C"}


def actor_map(actor):
    if isinstance(actor, str) and actor.partition(" ")[0] in _CLASSES:
        return native_stock_door.actor_map(actor)
    return None


def target_actor(value):
    if isinstance(value, str) and value.startswith(TARGET) and actor_map(actor := value[len(TARGET):]):
        return actor
    return None


def parse(fields, phase, area):
    if (len(fields) != 9 or fields[1] != area or actor_map(fields[2]) != area
            or phase not in ("hosting", "connected") or fields[3] not in ("0", "1")):
        raise ValueError("Invalid stock switch frame")
    angles = [round(float(value), 2) for value in fields[4:7]]
    if not all(-360 <= value <= 360 for value in angles):
        raise ValueError("Invalid stock switch rotation")
    lights = {}
    for link in fields[8].split(";") if fields[8] != "-" else ():
        name, power, visible = link.split("|")
        if (native_stock_door.actor_map(name) != area or name in lights
                or power not in ("0", "1") or visible not in ("0", "1")):
            raise ValueError("Invalid linked light state")
        lights[name] = [power == "1", visible == "1"]
    return {"powered": fields[3] == "1", "angles": angles, "panel": fields[7], "lights": lights}


async def interact(host, bridge, character, requested, operation):
    epoch, _, actor = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    member = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    if (not epoch or epoch != host.zone_epoch or actor_map(actor) != game.get("map")
            or actor not in game.get(KEY, {}) or game.get("state") != "hosting" or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or not member.get("connected")
            or not player or player["downed"] or host.party["wipe"]
            or character in host.party["pending_spawns"] or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_switch_scope_unavailable")
    fingerprint = [ACTION, requested, None]
    receipts = host.party["receipts"].setdefault(character, [])
    for receipt in receipts:
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    try:
        snapshot = await bridge.command(ACTION, actor, member["pawn"], timeout=3)
    except RuntimeError as error:
        if str(error).startswith("stock_switch_"):
            raise party.RuleError(str(error)) from error
        raise
    row = snapshot.get(KEY, {}).get(actor)
    if ((snapshot.get("state"), snapshot.get("map"), snapshot.get("ack", {}).get("message"))
            != ("hosting", game["map"], "stock switch interaction dispatched") or row is None):
        raise RuntimeError("The stock switch interaction did not verify")
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
        updated = await bridge.command(TAG + "-project", actor, area, str(int(target["powered"])),
                                       *map(str, angles), *bulbs, timeout=2)
        if (updated.get("state"), updated.get("map")) != ("connected", area):
            return game
        game = updated
    return game
