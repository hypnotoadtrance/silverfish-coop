"""Share stock hinged door locks and collision poses in the current world."""

import math
import re
import time

from . import native_inventory, party
from .native_gold import valid_guid


TAG = "native-stock-door"
KEY = "native_stock_doors"
ACTION = "native-stock-door-interact"
RESULT = "native_stock_door_interaction"
DELTA = "native_stock_door_delta"
FIXTURE = "native-stock-door-fixture"
INPUT_FIXTURE = "native-stock-door-input-fixture"
STAGE_FIXTURE = "native-stock-door-stage-fixture"
TRACE_FIXTURE = "native-stock-door-trace"
TRACE_RESULT = "native_stock_door_trace"
PRY_RESULT = "native_stock_door_pry"
PRY_ACTIONS = {"blowtorch": "native-guest-door-blowtorch", "crowbar": "native-guest-door-crowbar"}
TARGET_PREFIX = "stock-door:"
_ACTOR = re.compile(r"[A-Za-z0-9_]+ (/Game/Classes/Maps/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_]+")
_FLAGS = ("locked", "broken", "open")
_ANGLES = ("pitch", "yaw", "roll")
_OFFSET = ("x", "y", "z")


def actor_map(actor):
    match = _ACTOR.fullmatch(actor) if isinstance(actor, str) and len(actor) <= 384 else None
    if match and match[1].rsplit("/", 1)[-1] == match[2]:
        return match[1]
    return None


def target_actor(target):
    if isinstance(target, str) and target.startswith(TARGET_PREFIX):
        actor = target[len(TARGET_PREFIX):]
        if actor_map(actor):
            return actor
    return None


def parse(fields, phase, area):
    if (len(fields) != 19 or fields[:2] != [TAG, area] or actor_map(fields[2]) != area
            or phase not in ("hosting", "connected") or any(flag not in ("0", "1") for flag in fields[3:6])
            or fields[18] not in ("0", "1", "2", "3", "4", "5")):
        raise ValueError("Invalid stock hinged door frame")
    angles = list(map(float, fields[6:9]))
    offsets = list(map(float, fields[9:12]))
    geometry = list(map(float, fields[12:18]))
    if (any(not -360 <= axis <= 360 for axis in angles) or any(not -10000 <= axis <= 10000 for axis in offsets)
            or any(not -1e7 <= axis <= 1e7 for axis in geometry)):
        raise ValueError("Invalid stock door pose")
    return (dict(zip(_FLAGS, (flag == "1" for flag in fields[3:6]))) | dict(zip(_ANGLES, angles))
            | dict(zip(_OFFSET, offsets))
            | {"panel_location": geometry[:3], "knob_location": geometry[3:], "collision": int(fields[18])})


def parse_pry(fields, phase, area):
    if len(fields) != 8 or fields[1] != area or phase != "hosting":
        raise ValueError("Invalid stock door pry scope")
    _, _, actor, pawn, guid, kind, before, after = fields
    if (actor_map(actor) != area or not pawn.startswith(f"Player_FP_C {area}.") or not valid_guid(guid)
            or kind not in PRY_ACTIONS):
        raise ValueError("Invalid stock door pry receipt")
    before, after = int(before), int(after)
    if ((kind == "crowbar" and (before, after) != (-1, -1))
            or (kind == "blowtorch" and not (1 <= before <= 3 and after == before - 1))):
        raise ValueError("Invalid stock door pry tool cost")
    return dict(map=area, actor=actor, pawn=pawn, guid=guid, kind=kind, before=before, after=after)


def frame(game, epoch, actor=None):
    if game.get("state") != "hosting" or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
        return {}
    doors = game.get(KEY, {})
    if actor is not None:
        doors = {actor: doors[actor]} if actor in doors else {}
    fields = (*_FLAGS, *_ANGLES, *_OFFSET)
    return {"map": game["map"], "epoch": epoch, "sequence": game.get("sequence", 0),
            "doors": {name: {key: row[key] for key in fields} for name, row in doors.items()}} if doors else {}


def accept_delta(gameplay, delta, area, epoch, actor):
    if (not isinstance(delta, dict) or (delta.get("map"), delta.get("epoch")) != (area, epoch)
            or not isinstance(doors := delta.get("doors"), dict) or set(doors) != {actor}
            or actor_map(actor) != area or type(sequence := delta.get("sequence")) is not int or sequence <= 0):
        return
    previous = gameplay.get(KEY, {})
    if (previous.get("map"), previous.get("epoch")) != (area, epoch):
        gameplay[KEY] = delta
    elif sequence > previous.get("sequence", 0):
        gameplay[KEY] = dict(delta, doors=previous["doors"] | doors)
    else:
        return
    return delta


def matches(report, desired):
    return (all(report.get(flag) is desired[flag] for flag in _FLAGS)
            and all(abs((report.get(key, math.inf) - desired[key] + 180) % 360 - 180) <= .05 for key in _ANGLES)
            and all(abs(report.get(key, math.inf) - desired[key]) <= .05 for key in _OFFSET))


async def project(bridge, game, frame, epoch):
    area = game.get("map")
    if (not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, epoch)
            or game.get("state") != "connected"):
        return game
    local = game.get(KEY, {})
    doors = frame.get("doors")
    if not local or not isinstance(doors, dict) or len(doors) > 240:
        return game
    batch = []
    for name, expected in doors.items():
        if (name not in local or actor_map(name) != area or not isinstance(expected, dict)
                or any(type(expected.get(flag)) is not bool for flag in _FLAGS)
                or any(type(expected.get(axis)) not in (float, int) or not -360 <= expected[axis] <= 360 for axis in _ANGLES)
                or any(type(expected.get(axis)) not in (float, int) or not -10000 <= expected[axis] <= 10000 for axis in _OFFSET)
                or matches(local[name], expected)):
            continue
        batch.append((name, expected))
        if len(batch) == 24:
            break
    if not batch:
        return game
    args = ("|".join((name, *("1" if row[flag] else "0" for flag in _FLAGS),
                       *(str(row[key]) for key in (*_ANGLES, *_OFFSET)))) for name, row in batch)
    result = await bridge.command(TAG + "-project", area, *args, timeout=4)
    after = result.get(KEY, {})
    message = result.get("ack", {}).get("message", "")
    suffix = message.split("|")[1:] if message.startswith("stock door flags and hinge transform synchronized|") else []
    deferred = {int(index) for index in suffix if index.isascii() and index.isdigit() and len(index) <= 2}
    if len(deferred) != len(suffix) or any(not 1 <= index <= len(batch) for index in deferred):
        raise RuntimeError("Stock hinged door projection returned invalid pending movement")
    if (result.get("state"), result.get("map")) != ("connected", area) or any(
            index not in deferred and not matches(after.get(name, {}), row)
            for index, (name, row) in enumerate(batch, 1)):
        raise RuntimeError("Stock hinged door projection did not match the host")
    return result


async def interact(host, bridge, character, requested, operation):
    epoch, _, actor = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    area = game.get("map")
    member = host.native_recipients().get(character, {})
    if (not epoch or epoch != host.zone_epoch or not area or actor_map(actor) != area
            or game.get("state") != "hosting" or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or not member.get("connected") or host.party["wipe"] or host.party["players"][character]["downed"]
            or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_door_scope_unavailable")
    fingerprint = [ACTION, requested, None]
    receipts = host.party["receipts"].setdefault(character, [])
    for receipt in receipts:
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    try:
        reply = await bridge.command(TAG + "-guest-interact", actor, member["pawn"], timeout=4)
    except RuntimeError as error:
        if str(error) == "stock_door_locked" or str(error).startswith("stock_door_guest_"):
            raise party.RuleError(str(error)) from error
        raise

    prefix, _, before = reply.get("ack", {}).get("message", "").rpartition(" ")
    sequence = reply.get("sequence")
    if (prefix != "stock hinged door interaction dispatched" or before not in {"open", "closed"}
            or (reply.get("state"), reply.get("map")) != ("hosting", area)
            or type(sequence) is not int or sequence <= 0):
        raise RuntimeError("The host did not report the door state at dispatch")
    expected_open = before == "closed"

    def toggled(snapshot):
        observed = snapshot.get(KEY, {}).get(actor, {})
        return ((snapshot.get("state"), snapshot.get("map")) == ("hosting", area)
                and snapshot.get("sequence", 0) >= sequence and observed.get("locked") is False
                and observed.get("open") is expected_open)

    host.last_game = reply if toggled(reply) else await bridge.wait_for(toggled, timeout=2, interval=.02)
    host.last_seen = time.monotonic()
    result = {RESULT: {"actor": actor, "open": host.last_game[KEY][actor]["open"]},
              DELTA: frame(host.last_game, epoch, actor)}
    receipts.append({"id": operation, "request": fingerprint, "result": result})
    del receipts[:-party.RECENT_ACTIONS]
    return result


async def pry(host, bridge, character, requested, operation, kind):
    parts = requested.split("@", 2) if isinstance(requested, str) else []
    epoch, guid, actor = parts if len(parts) == 3 else (None, None, None)
    game = host.last_game
    area = game.get("map")
    member = host.native_recipients().get(character, {})
    row = game.get(KEY, {}).get(actor)
    if (kind not in PRY_ACTIONS or not epoch or epoch != host.zone_epoch or not valid_guid(guid) or actor_map(actor) != area
            or not row or not host.native_loot_input or game.get("state") != "hosting" or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or not member.get("connected") or host.party["wipe"]
            or character in host.party["pending_spawns"] or host.party["players"][character]["downed"]
            or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_door_pry_scope_unavailable")

    fingerprint = [PRY_ACTIONS[kind], f"{guid}@{actor}", None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if not row["locked"] or row["broken"]:
        raise party.RuleError("stock_door_not_locked")
    if host.smg_holds is not None and host.smg_holds.strokes:
        raise party.RuleError("stock_door_pry_busy")

    deferred = await native_inventory.retain_guest_save(host, bridge, character, member["pawn"])
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(TAG + "-guest-pry", actor, member["pawn"], guid, kind, timeout=4)
        except RuntimeError as error:
            if str(error).startswith(("stock_door_pry_", f"stock_{kind}_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(PRY_RESULT, {})
        if tuple(proof.get(key) for key in ("map", "actor", "pawn", "guid", "kind")) != (area, actor, member["pawn"], guid, kind):
            raise RuntimeError("The stock door pry did not report the exact guest and tool")

        def unlocked(frame):
            observed = frame.get(KEY, {}).get(actor, {})
            return ((frame.get("state"), frame.get("map")) == ("hosting", area) and observed.get("locked") is False
                    and observed.get("broken") is True)

        host.last_game = snapshot if unlocked(snapshot) else await bridge.wait_for(unlocked, timeout=4)
        host.last_seen = time.monotonic()
        result = {PRY_RESULT: dict(proof, locked=False, broken=True), DELTA: frame(host.last_game, epoch, actor)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        if not deferred:
            await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True


async def stage(host, bridge, requested, side):
    role, _, actor = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    game = host.last_game
    row = game.get(KEY, {}).get(actor)
    if (not row or role not in ("host", "guest") or side not in (0, 1, 2, 3)
            or game.get("state") != "hosting" or host.transitioning or host.native_checkpoint_unsafe):
        raise party.RuleError("Select a stock door, paired player, and approach side")
    slot, binding = host.role_pawn(role)
    if not slot:
        raise party.RuleError("The paired stock door fixture pawn is unavailable")
    x, y, z = row["knob_location"]
    dx, dy = ((110, 0), (0, 110), (-110, 0), (0, -110))[side]
    report = await bridge.restore(slot, {"location": [x + dx, y + dy, z]}, binding=binding)
    host.last_game, host.last_seen = report, time.monotonic()
    return {"position": report["pawns"][slot]["location"]}


async def trace_fixture(bridge, game, target):
    parts = target.split("@") if isinstance(target, str) else []
    if len(parts) != 4 or parts[0] not in game.get(KEY, {}) or parts[1] not in ("0", "2"):
        raise ValueError("Select an exact stock door and trace endpoints")
    actor, query, start, end = parts
    coordinates = (*start.split(","), *end.split(","))
    if len(start.split(",")) != 3 or len(end.split(",")) != 3 or any(
            not -1e7 <= float(axis) <= 1e7 for axis in coordinates):
        raise ValueError("Select two finite world points around the stock door")
    frame = await bridge.command(TRACE_FIXTURE, actor, query, *coordinates, timeout=4)
    proof = frame.get(TRACE_RESULT, {})
    if (proof.get("actor"), proof.get("query")) != (actor, int(query)):
        raise RuntimeError("The exact stock door trace did not verify")
    return frame, {TRACE_RESULT: proof}
