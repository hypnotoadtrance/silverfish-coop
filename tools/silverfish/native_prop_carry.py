"""Carry stock replicated props using the holder's native grip target."""

import asyncio
from collections import deque
import math
import re
import time
import uuid

from . import native_loot, party

ACTION = "native-prop-carry"
AIM = "native-prop-carry-aim"
FIXTURE = "native-prop-carry-fixture"
BARRELS = "native_authored_physics_barrels"
BARREL_ROW = "native-authored-physics-barrel"
PLACED_PROPS = "native_placed_physics_props"
PLACED_PROP_ROW = "native-authored-physics-prop"
INPUT = "native_prop_carry_input"
TARGET = "native_prop_carry_target"
PROOF = "native_prop_carry"
_CLASS = re.compile(r"/Game/[A-Za-z0-9_./-]+\.[A-Za-z0-9_]+_C\Z")
_BONE = re.compile(r"[A-Za-z0-9_ .-]{1,64}\Z")
_EDGES = {"grab", "drop", "throw"}
_ORDER_LIMIT = 2**53


def _serial(value):
    result = int(value)
    if not 0 < result < _ORDER_LIMIT:
        raise ValueError("Invalid physics carry sequence")
    return result


def _numbers(fields, limits):
    values = tuple(float(value) for value in fields)
    if len(values) != len(limits) or not all(math.isfinite(value) and abs(value) <= limit
                                             for value, limit in zip(values, limits)):
        raise ValueError("Invalid physics carry coordinates")
    return values


def _class(value):
    return isinstance(value, str) and len(value) <= 200 and _CLASS.fullmatch(value)


def _token(value):
    return isinstance(value, str) and party.REQUEST_ID.fullmatch(value)


def _corpse(fields):
    if not fields:
        return {}
    number, bone = fields
    identifier = int(number)
    if not 0 < identifier <= 65535 or bone == "None" or not _BONE.fullmatch(bone):
        raise ValueError("Invalid corpse movement marker or skeletal body")
    return dict(corpse=identifier, bone=bone)


def _identity(fields, area):
    if len(fields) != 1:
        return _corpse(fields)
    path = fields[0]
    prefix = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel."
    if len(path) > 320 or not path.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", path[len(prefix):]):
        raise ValueError("Invalid placed physics carry target")
    return {"placement": path}


def parse(fields, phase, area):
    if len(fields) < 2 or fields[1] != area:
        raise ValueError("Physics carry changed maps")
    if fields[0] == ACTION:
        if len(fields) not in (7, 9) or phase != "hosting" or not _token(fields[2]) or fields[4] not in _EDGES:
            raise ValueError("Invalid physics carry receipt")
        return dict(lease=fields[2], sequence=_serial(fields[3]), edge=fields[4], pawn=fields[5], actor=fields[6],
                    **_corpse(fields[7:]))
    if fields[0] == FIXTURE:
        if len(fields) not in (11, 15) or phase != "hosting" or not _class(fields[2]) or fields[10] not in {"0", "1"}:
            raise ValueError("Invalid stock physics fixture")
        motion = _numbers(fields[4:10], (1e7,) * 6)
        report = dict(class_path=fields[2], actor=fields[3], position=motion[:3], velocity=motion[3:], held=fields[10] == "1")
        if len(fields) == 15:
            report.update(sample=_serial(fields[11]), grip=_numbers(fields[12:], (1e7,) * 3))
        return report
    if fields[0] == BARREL_ROW:
        actor = re.fullmatch(r"(Prop_(?:PlasticBarrel|FuelDrum))_C /Game/[A-Za-z0-9_./]+:PersistentLevel\.([A-Za-z0-9_]+)",
                             fields[2]) if len(fields) == 4 and phase == "hosting" else None
        if not actor:
            raise ValueError("Invalid authored physics barrel")
        stem, instance = actor.groups()
        names = (stem, "Prop_FeulDrum") if stem == "Prop_FuelDrum" else (stem,)
        if not any(re.fullmatch(rf"(?:{name}_GEN_VARIABLE_{name}_C_CAT_|{name}_C_)[0-9]{{1,4}}", instance) for name in names):
            raise ValueError("Invalid authored physics barrel instance")
        mass, = _numbers(fields[3:], (10000,))
        if mass <= 0:
            raise ValueError("Invalid authored physics barrel mass")
        return dict(actor=fields[2], mass=mass)
    if fields[0] == PLACED_PROP_ROW:
        if len(fields) != 5 or phase != "hosting" or not area or not _class(fields[3]):
            raise ValueError("Invalid placed stock physics prop")
        name = fields[3].rsplit(".", 1)[-1]
        level = area.rsplit("/", 1)[-1]
        prefix = f"{name} {area}.{level}:PersistentLevel."
        mass, = _numbers(fields[4:], (10000,))
        if not fields[2].startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", fields[2][len(prefix):]) or mass <= 0:
            raise ValueError("Invalid placed stock physics prop identity")
        return dict(actor=fields[2], class_path=fields[3], mass=mass)
    if phase != "connected":
        raise ValueError("Physics carry input belongs to the guest")
    if fields[0] == "native-prop-carry-input":
        if (len(fields) not in (11, 12, 13) or len(fields) > 11 and fields[4] != "grab"
                or fields[4] not in _EDGES or not _class(fields[5])):
            raise ValueError("Invalid stock physics carry input")
        return dict(sequence=_serial(fields[2]), pawn=fields[3], edge=fields[4], class_path=fields[5],
                    pose=_numbers(fields[6:11], (1e7, 1e7, 1e7, 89.9, 180)), **_identity(fields[11:], area))
    if len(fields) != 9:
        raise ValueError("Invalid stock physics grip")
    return dict(grab=_serial(fields[2]), pose=_numbers(fields[3:], (1e7, 1e7, 1e7, 360, 360, 360)))


class Host:
    def __init__(self, service):
        self.service = service
        self.holders = {}
        self.poses = {}
        self.epoch = None

    def _pawn(self, character):
        service = self.service
        player = service.party["players"].get(character, {})
        if (service.transitioning or service.stopping or service.native_checkpoint_unsafe or service.party.get("wipe") or
                service.last_game.get("state") != "hosting" or not player or player.get("downed")
                or character in service.party.get("pending_spawns", {})):
            return None
        return native_loot._scope(service, character)

    def _reset(self):
        if self.epoch != self.service.zone_epoch:
            self.holders.clear()
            self.poses.clear()
            self.epoch = self.service.zone_epoch

    async def operate(self, bridge, character, target):
        self._reset()
        fields = target.split("@") if isinstance(target, str) and len(target) <= 700 else []
        if len(fields) not in (6, 10, 11, 12):
            raise party.RuleError("native_prop_carry_invalid_action")
        epoch, lease, edge, raw_sequence, *payload = fields
        if epoch != self.epoch or not _token(lease) or edge not in _EDGES:
            raise party.RuleError("native_prop_carry_invalid_action")
        number = _serial(raw_sequence)
        pitch, yaw = _numbers(payload[:2], (89.9, 180))
        pawn = self._pawn(character)
        holder = self.holders.get(character)
        if not pawn:
            raise party.RuleError("native_prop_carry_guest_unavailable")
        if edge == "grab":
            if len(payload) not in (6, 7, 8) or not _class(payload[2]) or holder and time.monotonic() < holder["deadline"]:
                raise party.RuleError("native_prop_carry_guest_hands_busy")
            coordinates = _numbers(payload[3:6], (1e7, 1e7, 1e7))
            identity = _identity(payload[6:], self.service.last_game["map"])
            args = (payload[2], *(str(axis) for axis in coordinates), *payload[6:])
        elif len(payload) == 2 and holder and (holder["lease"], holder["pawn"]) == (lease, pawn) and number > holder["sequence"]:
            args = ()
            identity = holder["identity"]
        else:
            raise party.RuleError("native_prop_carry_wrong_holder")
        snapshot = await bridge.command(ACTION, lease, pawn, edge, str(number), str(pitch), str(yaw), *args, timeout=4)
        proof = snapshot.get(PROOF, {})
        if (tuple(proof.get(key) for key in ("lease", "sequence", "edge", "pawn")) != (lease, number, edge, pawn)
                or any(proof.get(key) != identity.get(key) for key in ("corpse", "bone"))
                or ("placement" in identity and proof.get("actor", "").partition(" ")[2] != identity["placement"])):
            raise RuntimeError("The stock physics carry did not return the exact action")
        self.service.last_game, self.service.last_seen = snapshot, time.monotonic()
        if edge == "grab":
            self.holders[character] = dict(lease=lease, pawn=pawn, sequence=number, sample=0,
                                           deadline=time.monotonic() + 1.4, identity=identity)
        else:
            self.holders.pop(character, None)
            self.poses.pop(lease, None)
        self.service.control.event("native_prop_carry_action", **proof)
        return {PROOF: proof}

    def aim(self, bridge, character, target):
        from .runtime import write_text

        self._reset()
        fields = target.split("@") if isinstance(target, str) and len(target) <= 180 else []
        if len(fields) != 9 or fields[0] != self.epoch:
            raise party.RuleError("native_prop_carry_invalid_pose")
        _, lease, raw_sequence, *coordinates = fields
        holder = self.holders.get(character)
        if (not holder or holder["lease"] != lease or holder["pawn"] != self._pawn(character)
                or time.monotonic() >= holder["deadline"]):
            raise party.RuleError("native_prop_carry_wrong_holder")
        sequence = _serial(raw_sequence)
        if sequence <= holder["sample"]:
            raise party.RuleError("native_prop_carry_stale_pose")
        position = _numbers(coordinates, (1e7, 1e7, 1e7, 360, 360, 360))
        bodies = self.service.last_game.get("pawns", {})
        origin = next((body.get("location") for body in bodies.values() if body.get("name") == holder["pawn"]), None)
        if not origin or len(origin) != 3 or math.dist(origin, position[:3]) > 375:
            raise party.RuleError("native_prop_carry_pose_out_of_reach")
        now = time.monotonic()
        holder["sample"], holder["deadline"] = sequence, now + 1.4
        self.poses[lease] = "\t".join((lease, str(sequence), *(str(axis) for axis in position)))
        allowed = {entry["lease"] for entry in self.holders.values() if entry["deadline"] > now}
        self.poses = {key: value for key, value in self.poses.items() if key in allowed}
        write_text(bridge.directory / "native-prop-carry-poses", "\n".join(self.poses.values()) + "\n", newline="\n")
        return {"ok": True}


class Guest:
    def __init__(self, service):
        self.service = service
        self.epoch = None
        self.lease = self.grab = None
        self.seen = self.sample = 0
        self.next_pose = 0
        self.last_pose = None
        self.pose_heartbeat = 0
        self.pose_error = None
        self.edges = deque(maxlen=6)
        self.action = self.pose = None

    async def _operate(self, row):
        service, edge, number = self.service, row["edge"], row["sequence"]
        if edge != "grab" and not self.lease:
            return
        operation = uuid.uuid5(service.bridge.input_namespace, f"prop:{self.epoch}:{number}").hex
        lease = operation if edge == "grab" else self.lease
        x, y, z, pitch, yaw = row["pose"]
        suffix = (row["class_path"], str(x), str(y), str(z)) if edge == "grab" else ()
        if edge == "grab" and "corpse" in row:
            suffix += (str(row["corpse"]), row["bone"])
        elif edge == "grab" and "placement" in row:
            suffix += (row["placement"],)
        target = "@".join((self.epoch, lease, edge, str(number), str(pitch), str(yaw), *suffix))
        try:
            returned = await service.gameplay_action(ACTION, operation, target)
            if returned.get(PROOF, {}).get("sequence") != number:
                raise ValueError("The host action did not match the stock carry")
            self.lease, self.grab = (lease, number) if edge == "grab" else (None, None)
            self.last_pose = None
        except (OSError, ValueError, RuntimeError) as error:
            self.lease = self.grab = None
            service.control.event("native_prop_carry_refused", edge=edge, reason=str(error))
            if edge == "grab" and ("corpse" in row or "placement" in row):
                try:
                    await service.bridge.command("native-prop-carry-refuse", str(number), timeout=2)
                except (OSError, ValueError, RuntimeError) as failure:
                    service.control.event("native_prop_carry_local_release_failed", reason=str(failure))

    async def _pose(self, target):
        request = dict(action=AIM, operation_id=uuid.uuid4().hex, target=target)
        try:
            reply = await self.service.transport.request(request, timeout=.7)
            reason = None if reply.get("ok") else reply.get("error", "native_prop_carry_pose_refused")
        except (OSError, ValueError, RuntimeError) as error:
            reason = str(error)
        if reason != self.pose_error:
            self.pose_error = reason
            if reason:
                self.service.control.event("native_prop_carry_pose_refused", reason=reason)

    def poll(self, active):
        service = self.service
        game = service.last_game
        epoch = service.zone_epoch if active and game.get("state") == "connected" else None
        if epoch != self.epoch:
            self.close()
            self.epoch = epoch
            self.seen = max((row["sequence"] for row in game.get(INPUT, ())), default=0)
        if not self.epoch:
            return
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        for row in game.get(INPUT, ()):
            if row["sequence"] > self.seen and row["pawn"] == pawn:
                self.seen = row["sequence"]
                self.edges.append(row)
        if self.edges and (self.action is None or self.action.done()):
            self.action = asyncio.create_task(self._operate(self.edges.popleft()))
        now = time.monotonic()
        grip = game.get(TARGET, {})
        if (self.lease and grip.get("grab") == self.grab and now >= self.next_pose
                and (self.pose is None or self.pose.done())):
            position = tuple(f"{axis:.1f}" for axis in grip["pose"])
            if position == self.last_pose and now < self.pose_heartbeat:
                return
            self.next_pose, self.sample = now + .1, self.sample + 1
            self.last_pose, self.pose_heartbeat = position, now + .45
            target = "@".join((self.epoch, self.lease, str(self.sample), *position))
            self.pose = asyncio.create_task(self._pose(target))

    def close(self):
        for task in (self.action, self.pose):
            if task and not task.done():
                task.cancel()
        self.action = self.pose = None
        self.lease = self.grab = None
        self.last_pose = self.pose_error = None
        self.edges.clear()
