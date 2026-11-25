"""Share stock hinged door locks and collision poses in the current world."""

import asyncio
from dataclasses import dataclass
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
SETTLEMENT = "native_stock_door_settlement"
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
    elif (sequence > previous.get("sequence", 0)
          or sequence == previous.get("sequence") and previous["doors"].get(actor) != doors[actor]):
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


@dataclass
class DoorMotion:
    area: str
    epoch: str
    actor: str
    character: str
    operation: str
    fingerprint: list
    sequence: int
    opens: bool
    deadline: float
    replies: list[asyncio.Future]
    dispatch_id: str | None = None
    destination: dict | None = None

    def settled(self, game):
        row = game.get(KEY, {}).get(self.actor, {})
        return ((game.get("state"), game.get("map")) == ("hosting", self.area)
                and game.get("sequence", 0) >= self.sequence and row.get("locked") is False
                and row.get("open") is self.opens
                and (self.destination is None or matches(row, row | self.destination)))

    def result(self, host, game, *, settle_ms=None):
        result = {RESULT: {"actor": self.actor, "open": self.opens}, DELTA: frame(game, self.epoch, self.actor)}
        if settle_ms is not None:
            result[SETTLEMENT] = {"source": "memory_input", "host_dispatch_to_settle_ms": settle_ms}
        receipts = host.party["receipts"].setdefault(self.character, [])
        receipts.append({"id": self.operation, "request": self.fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        return result

    def respond(self, reply):
        for future in self.replies:
            if not future.done():
                future.set_result(reply)


class Interactions:
    """Dispatch on the host loop and settle from ordinary world snapshots."""

    def __init__(self):
        self.pending = {}

    async def dispatch(self, host, bridge, character, requested, operation, future):
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
        for motion in self.pending.values():
            if (motion.character, motion.operation) == (character, operation):
                if motion.fingerprint != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                motion.replies[:] = [reply for reply in motion.replies if not reply.done()]
                if len(motion.replies) >= 8:
                    raise party.RuleError("stock_door_in_motion")
                motion.replies.append(future)
                return None
        if actor in self.pending or len(self.pending) >= 8:
            raise party.RuleError("stock_door_in_motion")
        motion, reply = await _start_motion(host, bridge, character, member["pawn"], area, epoch, actor, operation, fingerprint, future)
        if motion.settled(reply):
            return motion.result(host, reply)
        self.pending[actor] = motion

    def settle(self, host, records=()):
        if not self.pending:
            return
        now, game = time.monotonic(), host.last_game
        completed = {}
        by_dispatch = {motion.dispatch_id: motion for motion in self.pending.values() if motion.dispatch_id}
        for record in records:
            fields = record.decode("utf-8", errors="replace").split("\t")
            if len(fields) != 23 or fields[0] != "stock-door-settled" or not fields[2].isascii() or not fields[2].isdigit():
                continue
            motion = by_dispatch.get(fields[1])
            if motion is None or fields[6] != motion.actor or len(fields[2]) > 16:
                continue
            sequence = int(fields[2])
            if not max(motion.sequence, game.get("sequence", 0)) <= sequence < 2**53:
                continue
            try:
                elapsed = float(fields[3])
                observed = parse(fields[4:], "hosting", motion.area)
            except ValueError:
                continue
            sample = {"state": "hosting", "map": motion.area, "sequence": sequence, KEY: {motion.actor: observed}}
            if 0 <= elapsed <= 2000 and motion.settled(sample):
                completed[motion.actor] = sample, elapsed
        for actor, motion in tuple(self.pending.items()):
            in_visit = ((host.zone_epoch, game.get("state"), game.get("map")) == (motion.epoch, "hosting", motion.area)
                        and not (host.stopping or host.transitioning or host.party["wipe"]))
            if in_visit and actor in completed:
                sample, elapsed = completed[actor]
                response = {"ok": True, **motion.result(host, sample, settle_ms=elapsed)}
            elif in_visit and motion.settled(game):
                # The door moved even if its guest disconnected. Keep its receipt for a rejoin.
                response = {"ok": True, **motion.result(host, game)}
            elif not in_visit:
                response = {"ok": False, "error": "stock_door_scope_unavailable"}
            elif motion.deadline <= now:
                response = {"ok": False, "error": "stock_door_motion_unverified"}
            else:
                continue
            del self.pending[actor]
            motion.respond(response)

    def close(self):
        for motion in self.pending.values():
            motion.respond({"ok": False, "error": "stock_door_scope_unavailable"})
        self.pending.clear()


async def _start_motion(host, bridge, character, pawn, area, epoch, actor, operation, fingerprint, future):
    try:
        reply = await bridge.command(TAG + "-guest-interact", actor, pawn, timeout=4)
    except RuntimeError as error:
        if str(error) == "stock_door_locked" or str(error).startswith("stock_door_guest_"):
            raise party.RuleError(str(error)) from error
        raise

    message, separator, end_pose = reply.get("ack", {}).get("message", "").partition("|")
    prefix, _, before = message.rpartition(" ")
    axes = end_pose.split("|") if separator else []
    if separator:
        try:
            pose = [float(value) for value in axes]
        except ValueError as error:
            raise RuntimeError("The host did not report a valid stock door destination") from error
        if len(pose) != 6 or any(not -limit <= value <= limit for value, limit in zip(pose, (360,) * 3 + (10000,) * 3)):
            raise RuntimeError("The host did not report a valid stock door destination")
        destination = dict(zip((*_ANGLES, *_OFFSET), pose, strict=True))
    else:
        destination = None
    sequence = reply.get("sequence")
    if (prefix != "stock hinged door interaction dispatched" or before not in {"open", "closed"}
            or (reply.get("state"), reply.get("map")) != ("hosting", area)
            or type(sequence) is not int or sequence <= 0):
        raise RuntimeError("The host did not report the door state at dispatch")
    now = time.monotonic()
    host.last_game, host.last_seen = reply, now
    return DoorMotion(area, epoch, actor, character, operation, fingerprint, sequence,
                      before == "closed", now + 2, [future], reply.get("ack", {}).get("id"), destination), reply


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
