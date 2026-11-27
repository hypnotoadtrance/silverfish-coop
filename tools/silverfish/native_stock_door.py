"""Share stock hinged door locks and collision poses in the current world."""

import asyncio
from dataclasses import dataclass
import math
import re
import time

from . import native_inventory, party
from .native_gold import valid_guid
from .native_map_preflight import valid_campaign_package


TAG = "native-stock-door"
STATE_TAG = TAG + "-state"
KEY = "native_stock_doors"
OBSERVED = "observed_at"
ACTION = "native-stock-door-interact"
DISPATCH = TAG + "-guest-interact"
PROJECT = TAG + "-project"
LOCAL_SCOPE = "native_stock_interaction_local_scope"
RESULT = "native_stock_door_interaction"
DELTA = "native_stock_door_delta"
FIXTURE = "native-stock-door-fixture"
INPUT_FIXTURE = "native-stock-door-input-fixture"
STAGE_FIXTURE = "native-stock-door-stage-fixture"
TRACE_FIXTURE = "native-stock-door-trace"
TRACE_RESULT = "native_stock_door_trace"
SETTLEMENT = "native_stock_door_settlement"
UNCHANGED = "stock hinged door outcome unchanged"
PRY_RESULT = "native_stock_door_pry"
PRY_ACTIONS = {"blowtorch": "native-guest-door-blowtorch", "crowbar": "native-guest-door-crowbar"}
TARGET_PREFIX = "stock-door:"
STREAM_PREFIX = "stream:"
_ACTOR = re.compile(r"[A-Za-z0-9_-]+ (/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_-]+")
_FLAGS = ("locked", "broken", "open")
_ANGLES = ("pitch", "yaw", "roll")
_OFFSET = ("x", "y", "z")


def actor_map(actor):
    match = _ACTOR.fullmatch(actor) if isinstance(actor, str) and len(actor) <= 384 else None
    if match and valid_campaign_package(match[1]) and match[1].rsplit("/", 1)[-1] == match[2]:
        return match[1]
    return None


def streamed_package(actor):
    if isinstance(actor, str) and len(actor) <= 384 and actor.startswith(STREAM_PREFIX):
        match = _ACTOR.fullmatch(actor.removeprefix(STREAM_PREFIX))
        if match and valid_campaign_package(match[1]):
            return match[1]
    return None


def matches_area(actor, area):
    if not valid_campaign_package(area):
        return False
    sublevel = streamed_package(actor)
    # The game thread verifies that the exact streamed actor is visible on this seat.
    return sublevel != area if sublevel else actor_map(actor) == area


def target_actor(target):
    if isinstance(target, str) and target.startswith(TARGET_PREFIX):
        actor, separator, direction = target[len(TARGET_PREFIX):].partition("|")
        if (not separator or direction in {"open", "closed"}) and (actor_map(actor) or streamed_package(actor)):
            return actor
    return None


def matches_local_scope(game, receipt):
    return (game.get("state"), game.get("map"), game.get("pawns", {}).get("local", {}).get("name")) == (
        receipt.get("state"), receipt.get("map"), receipt[LOCAL_SCOPE])


def parse(fields, phase, area):
    if (len(fields) not in (19, 20) or fields[:2] != [TAG, area] or not matches_area(fields[2], area)
            or phase not in ("hosting", "connected") or any(flag not in ("0", "1") for flag in fields[3:6])
            or fields[18] not in ("0", "1", "2", "3", "4", "5")):
        raise ValueError("Invalid stock hinged door frame")
    angles = list(map(float, fields[6:9]))
    offsets = list(map(float, fields[9:12]))
    geometry = list(map(float, fields[12:18]))
    if (any(not -360 <= axis <= 360 for axis in angles) or any(not -10000 <= axis <= 10000 for axis in offsets)
            or any(not -1e7 <= axis <= 1e7 for axis in geometry)):
        raise ValueError("Invalid stock door pose")
    reading = (dict(zip(_FLAGS, (flag == "1" for flag in fields[3:6]))) | dict(zip(_ANGLES, angles))
               | dict(zip(_OFFSET, offsets))
               | {"panel_location": geometry[:3], "knob_location": geometry[3:], "collision": int(fields[18])})
    if len(fields) == 20:
        if phase != "hosting" or not re.fullmatch(r"[1-9][0-9]{0,15}", fields[19]) or int(fields[19]) >= 2**53:
            raise ValueError("Invalid stock door observation version")
        reading[OBSERVED] = int(fields[19])
    return reading


def parse_state(fields, phase, area):
    if fields[0] == STATE_TAG:
        if len(fields) not in (19, 20) or not isinstance(area, str):
            raise ValueError("Invalid stock hinged door state")
        _, kind, object_name, *values = fields
        actor = f"{kind} {area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{object_name}"
        fields = [TAG, area, actor, *values]
    report = parse(fields, phase, area)
    return fields[2], report


def parse_pry(fields, phase, area):
    if len(fields) != 8 or fields[1] != area or phase != "hosting":
        raise ValueError("Invalid stock door pry scope")
    _, _, actor, pawn, guid, kind, before, after = fields
    if (not matches_area(actor, area) or not pawn.startswith(f"Player_FP_C {area}.") or not valid_guid(guid)
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
    fields = (*_FLAGS, *_ANGLES, *_OFFSET, OBSERVED)
    return {"map": game["map"], "epoch": epoch, "sequence": game.get("sequence", 0),
            "doors": {name: {key: row[key] for key in fields if key != OBSERVED or key in row}
                      for name, row in doors.items()}} if doors else {}


class GuestView:
    def __init__(self):
        self.scope = None
        self.snapshot_sequence = 0
        self.receipts = {}

    def _visit(self, area, epoch):
        if (area, epoch) != self.scope:
            self.scope = area, epoch
            self.snapshot_sequence = 0
            self.receipts.clear()

    def accept(self, gameplay, delta, area, epoch, actor):
        if (not isinstance(delta, dict) or (delta.get("map"), delta.get("epoch")) != (area, epoch)
                or not isinstance(doors := delta.get("doors"), dict) or doors.keys() != {actor}
                or not matches_area(actor, area) or type(sequence := delta.get("sequence")) is not int or sequence <= 0):
            return
        settled_at = doors[actor].get(OBSERVED, sequence)
        if type(settled_at) is not int or not 0 < settled_at < 2**53:
            return
        self._visit(area, epoch)
        previous = gameplay.get(KEY, {})
        current = (previous.get("map"), previous.get("epoch")) == self.scope
        rows = previous["doors"] if current else {}
        version = self.receipts.get(actor, (0, None))[0]
        observed = rows.get(actor, {}).get(OBSERVED, self.snapshot_sequence)
        if settled_at < max(observed, version) or settled_at == version and rows.get(actor) == doors[actor]:
            return
        self.receipts[actor] = settled_at, doors[actor]
        gameplay[KEY] = dict(delta, sequence=max(sequence, previous["sequence"]) if current else sequence,
                             doors=rows | doors)
        return delta

    def reconcile(self, gameplay, area, epoch):
        self._visit(area, epoch)
        snapshot = gameplay.get(KEY)
        if snapshot is None and self.receipts:
            # An in-flight snapshot can predate the host's first sample of this door.
            snapshot = {"map": area, "epoch": epoch, "sequence": 0, "doors": {}}
        if (not isinstance(snapshot, dict) or (snapshot.get("map"), snapshot.get("epoch")) != self.scope
                or type(sequence := snapshot.get("sequence")) is not int):
            return
        self.snapshot_sequence = max(self.snapshot_sequence, sequence)
        rows = snapshot["doors"]
        for actor, (settled_at, confirmed) in tuple(self.receipts.items()):
            row = rows.get(actor, {})
            if (observation_covers(row, settled_at, confirmed) if OBSERVED in row else sequence >= settled_at):
                del self.receipts[actor]
        if self.receipts:
            latest = max(sequence, *(version for version, _ in self.receipts.values()))
            kept = {actor: row for actor, (_, row) in self.receipts.items()}
            # Keep the decoded wire baseline intact for the next server delta.
            gameplay[KEY] = dict(snapshot, sequence=latest, doors=rows | kept)


def matches(report, desired):
    return (all(report.get(flag) is desired[flag] for flag in _FLAGS)
            and all(abs((report.get(key, math.inf) - desired[key] + 180) % 360 - 180) <= .05 for key in _ANGLES)
            and all(abs(report.get(key, math.inf) - desired[key]) <= .05 for key in _OFFSET))


def observation_covers(row, settled_at, confirmed):
    observed = row[OBSERVED]
    return observed > settled_at or (observed == settled_at and matches(row, confirmed))


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
        if (name not in local or not matches_area(name, area) or not isinstance(expected, dict)
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
    result = await bridge.command(PROJECT, area, *args, timeout=4)
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
    if LOCAL_SCOPE in result:
        if set(after) != {name for index, (name, _) in enumerate(batch, 1) if index not in deferred}:
            raise RuntimeError("Stock hinged door projection receipt did not match the requested actors")
        if not matches_local_scope(game, result) or result["sequence"] < game.get("sequence", 0):
            return game
        return {**game, KEY: local | after} if after else game
    return result


@dataclass
class DoorRequest:
    fingerprint: list
    replies: list[asyncio.Future]

    def respond(self, reply):
        for future in self.replies:
            if not future.done():
                future.set_result(reply)


@dataclass
class DoorMotion:
    area: str
    epoch: str
    actor: str
    sequence: int
    opens: bool
    deadline: float
    requests: dict[tuple[str, str], DoorRequest]
    dispatch_id: str | None = None
    destination: dict | None = None

    def settled(self, game):
        row = game.get(KEY, {}).get(self.actor, {})
        return ((game.get("state"), game.get("map")) == ("hosting", self.area)
                and row.get(OBSERVED, game.get("sequence", 0)) >= self.sequence and row.get("locked") is False
                and row.get("open") is self.opens
                and (self.destination is None or matches(row, row | self.destination)))

    def result(self, host, game, *, settle_ms=None):
        result = {RESULT: {"actor": self.actor, "open": self.opens}, DELTA: frame(game, self.epoch, self.actor)}
        if settle_ms is not None:
            result[SETTLEMENT] = {"source": "memory_input", "host_dispatch_to_settle_ms": settle_ms}
        for (character, operation), request in self.requests.items():
            receipts = host.party["receipts"].setdefault(character, [])
            receipts.append({"id": operation, "request": request.fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
        return result

    def respond(self, reply):
        for request in self.requests.values():
            request.respond(reply)


class Interactions:
    """Dispatch on the host loop and settle from ordinary world snapshots."""

    def __init__(self):
        self.pending = {}
        self.confirmed_scope = None
        self.confirmed = {}
        self.published = {}
        self.published_source = None

    def _confirm_scope(self, area, epoch):
        scope = area, epoch
        if scope != self.confirmed_scope:
            self.confirmed_scope = scope
            self.confirmed.clear()
            self.published = {}
            self.published_source = None

    def current_rows(self, game, epoch):
        area = game.get("map")
        self._confirm_scope(area, epoch)
        sequence, doors = game.get("sequence", 0), game.get(KEY, {})
        if self.confirmed and game.get("state") == "hosting":
            for actor, (settled_at, confirmed) in tuple(self.confirmed.items()):
                observed = doors.get(actor, {})
                if (observation_covers(observed, settled_at, confirmed) if OBSERVED in observed else sequence > settled_at):
                    del self.confirmed[actor]
            if self.confirmed:
                # A later world frame may still reuse an earlier observation of this door.
                sequence = max(sequence, *(version for version, _ in self.confirmed.values()))
                doors = doors | {actor: row for actor, (_, row) in self.confirmed.items()}
        return sequence, doors

    def world_frame(self, game, epoch):
        sequence, doors = self.current_rows(game, epoch)
        if game.get("state") != "hosting":
            return {}
        if self.published_source is not None:
            source_sequence, source_rows = self.published_source
            if source_sequence == sequence and source_rows is doors:
                return self.published
        candidate = frame({**game, KEY: doors, "sequence": sequence}, epoch)
        if not candidate and self.published:
            # An empty census supersedes previously visible doors in this visit.
            candidate = {**self.published, "sequence": sequence, "doors": {}}
        if candidate.get("doors") != self.published.get("doors"):
            self.published = candidate
        self.published_source = sequence, doors
        return self.published

    async def dispatch(self, host, bridge, character, requested, operation, future):
        epoch, _, target = requested.partition("@") if isinstance(requested, str) else ("", "", "")
        actor, separator, direction = target.partition("|")
        if separator and direction not in {"open", "closed"}:
            raise party.RuleError("stock_door_scope_unavailable")
        game = host.last_game
        area = game.get("map")
        player = host.party["players"].get(character)
        if (not epoch or epoch != host.zone_epoch or not matches_area(actor, area) or not player
                or host.transitioning or host.stopping or not party.REQUEST_ID.fullmatch(operation)):
            raise party.RuleError("stock_door_scope_unavailable")
        fingerprint = [ACTION, requested, None]
        receipts = host.party["receipts"].setdefault(character, [])
        for receipt in receipts:
            if receipt["id"] == operation:
                if receipt["request"] != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                return receipt["result"]
        # A completed receipt needs the same visit and request. A new mutation
        # also needs a live bound pawn and a stable host snapshot.
        member = host.native_recipients().get(character, {})
        if ((game.get("state"), game.get("map")) != ("hosting", area) or host.native_checkpoint_unsafe
                or not member.get("connected") or host.party["wipe"] or player["downed"]):
            raise party.RuleError("stock_door_scope_unavailable")
        key = character, operation
        for motion in self.pending.values():
            if duplicate := motion.requests.get(key):
                if duplicate.fingerprint != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                duplicate.replies[:] = [reply for reply in duplicate.replies if not reply.done()]
                if len(duplicate.replies) >= 8:
                    raise party.RuleError("stock_door_in_motion")
                duplicate.replies.append(future)
                return None
        requests = {key: DoorRequest(fingerprint, [future])}
        if motion := self.pending.get(actor):
            if not separator or motion.opens != (direction == "open") or len(motion.requests) >= 8:
                raise party.RuleError("stock_door_in_motion")
            motion.requests.update(requests)
            return None
        if len(self.pending) >= 8:
            raise party.RuleError("stock_door_in_motion")
        motion, reply = await _start_motion(host, bridge, member["pawn"], area, epoch, actor, direction, requests)
        if motion.settled(reply):
            if reply.get("ack", {}).get("message") == UNCHANGED:
                self._confirm_scope(area, epoch)
                observed = reply[KEY][actor]
                self.confirmed[actor] = observed.get(OBSERVED, reply["sequence"]), observed
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
            if len(fields) not in (23, 24) or fields[0] != "stock-door-settled" or not fields[2].isascii() or not fields[2].isdigit():
                continue
            motion = by_dispatch.get(fields[1])
            if motion is None or fields[6] != motion.actor or len(fields[2]) > 16:
                continue
            sequence = int(fields[2])
            current = game.get(KEY, {}).get(motion.actor, {})
            if not max(motion.sequence, current.get(OBSERVED, game.get("sequence", 0))) <= sequence < 2**53:
                continue
            try:
                elapsed = float(fields[3])
                observed = parse(fields[4:], "hosting", motion.area)
            except ValueError:
                continue
            sample = {"state": "hosting", "map": motion.area, "sequence": sequence, KEY: {motion.actor: observed}}
            if observed.get(OBSERVED, sequence) == sequence and 0 <= elapsed <= 2000 and motion.settled(sample):
                completed[motion.actor] = sample, elapsed
        for actor, motion in tuple(self.pending.items()):
            in_visit = ((host.zone_epoch, game.get("state"), game.get("map")) == (motion.epoch, "hosting", motion.area)
                        and not (host.stopping or host.transitioning or host.party["wipe"]))
            if in_visit and actor in completed:
                sample, elapsed = completed[actor]
                self._confirm_scope(motion.area, motion.epoch)
                self.confirmed[actor] = sample["sequence"], sample[KEY][actor]
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
        self.confirmed.clear()
        self.published = {}
        self.published_source = None


async def _start_motion(host, bridge, pawn, area, epoch, actor, direction, requests):
    try:
        reply = await bridge.command(DISPATCH, actor, pawn, *(direction,) if direction else (), timeout=4)
    except RuntimeError as error:
        if str(error) in {"stock_door_locked", "stock_door_in_motion"} or str(error).startswith("stock_door_guest_"):
            raise party.RuleError(str(error)) from error
        raise

    message, separator, end_pose = reply.get("ack", {}).get("message", "").partition("|")
    sequence = reply.get("sequence")
    if ((reply.get("state"), reply.get("map")) != ("hosting", area) or type(sequence) is not int or sequence <= 0):
        raise RuntimeError("The host did not report the door state at dispatch")
    if LOCAL_SCOPE in reply and (host.zone_epoch != epoch or not matches_local_scope(host.last_game, reply)):
        raise party.RuleError("stock_door_scope_unavailable")
    now = time.monotonic()
    if message == UNCHANGED:
        reading = reply.get(KEY, {}).get(actor, {})
        settled_at = reading.get(OBSERVED, sequence)
        if (not direction or separator or reading.get("open") is not (direction == "open")
                or reading.get("locked") is not False or type(settled_at) is not int or not 0 < settled_at < 2**53):
            raise RuntimeError("The host did not confirm the reported stock door outcome")
        # The periodic command path reuses the exact door's standing state version.
        reply = dict(reply, sequence=max(sequence, settled_at))
        return DoorMotion(area, epoch, actor, settled_at, direction == "open", now, requests), reply
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
    if (prefix != "stock hinged door interaction dispatched" or before not in {"open", "closed"}
            or direction and before == direction):
        raise RuntimeError("The host did not report the door state at dispatch")
    if LOCAL_SCOPE in reply:
        # The next published sample or native target-transform event confirms the motion.
        sequence += 1
    else:
        host.last_game, host.last_seen = reply, now
    return DoorMotion(area, epoch, actor, sequence, before == "closed", now + 2, requests,
                      reply.get("ack", {}).get("id"), destination), reply


async def pry(host, bridge, character, requested, operation, kind):
    parts = requested.split("@", 2) if isinstance(requested, str) else []
    epoch, guid, actor = parts if len(parts) == 3 else (None, None, None)
    game = host.last_game
    area = game.get("map")
    member = host.native_recipients().get(character, {})
    row = game.get(KEY, {}).get(actor)
    if (kind not in PRY_ACTIONS or not epoch or epoch != host.zone_epoch or not valid_guid(guid) or not matches_area(actor, area)
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
