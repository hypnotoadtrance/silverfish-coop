"""Carry stock replicated props using the holder's native grip target."""

import asyncio
from collections import deque
import math
import os
import re
from struct import Struct
import time
import uuid

from . import native_loot, party

ACTION = "native-prop-carry"
AIM = "native-prop-carry-aim"
POSE_DATAGRAM = "native_prop_pose_datagram"
RELEASE_POSE = "native_prop_release_pose"
FIXTURE = "native-prop-carry-fixture"
BARRELS = "native_authored_physics_barrels"
BARREL_ROW = "native-authored-physics-barrel"
PLACED_PROPS = "native_placed_physics_props"
PLACED_PROP_ROW = "native-authored-physics-prop"
INPUT = "native_prop_carry_input"
TARGET = "native_prop_carry_target"
PROOF = "native_prop_carry"
HELD_ROW = "native-prop-carry-held"
HELD = "native_prop_carry_held"
FILTER_ROW = "native-prop-carry-filter"
FILTER = "native_prop_carry_filter"
FILTER_READY_ROW = "native-prop-carry-filter-ready"
FILTER_READY = "native_prop_carry_filter_ready"
_CLASS = re.compile(r"/Game/[A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+_C\Z")
_PLACED_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
_BONE = re.compile(r"[A-Za-z0-9_ .-]{1,64}\Z")
_EDGES = {"grab", "drop", "throw"}
_ORDER_LIMIT = 2**53
_POSE_LIMITS = (1e7, 1e7, 1e7, 360, 360, 360)
_RELEASE_LIMITS = (*_POSE_LIMITS, 6000, 6000, 6000, 7200, 7200, 7200)
_POSE_FRAME = Struct("!16s16sQiiihhh")


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


def pack_pose(epoch, lease, sequence, position):
    return _POSE_FRAME.pack(bytes.fromhex(epoch), bytes.fromhex(lease), _serial(sequence),
                            *(round(axis * 10) for axis in _numbers(position, _POSE_LIMITS)))


def unpack_pose(frame):
    if len(frame) != _POSE_FRAME.size:
        raise ValueError("Invalid physics carry frame length")
    epoch, lease, sequence, *position = _POSE_FRAME.unpack(frame)
    return epoch.hex(), lease.hex(), _serial(sequence), _numbers((axis / 10 for axis in position), _POSE_LIMITS)


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
    if path.startswith("loot:") and _token(path[5:]):
        return {"loot_id": path[5:]}
    prefix = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel."
    if len(path) > 320 or not path.startswith(prefix) or not _PLACED_NAME.fullmatch(path[len(prefix):]):
        raise ValueError("Invalid placed physics carry target")
    return {"placement": path}


def parse(fields, phase, area):
    if len(fields) < 2 or fields[1] != area:
        raise ValueError("Physics carry changed maps")
    if fields[0] == ACTION:
        if len(fields) not in (7, 8, 9) or phase != "hosting" or not _token(fields[2]) or fields[4] not in _EDGES:
            raise ValueError("Invalid physics carry receipt")
        releases_with_pose = len(fields) == 8 and fields[4] in ("drop", "throw") and fields[7] == "release-pose"
        identity = {"release_pose_applied": True} if releases_with_pose else _identity(fields[7:], area)
        if "placement" in identity:
            raise ValueError("Unexpected placed item in physics carry receipt")
        return dict(lease=fields[2], sequence=_serial(fields[3]), edge=fields[4], pawn=fields[5], actor=fields[6],
                    **identity)
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
        if not fields[2].startswith(prefix) or not _PLACED_NAME.fullmatch(fields[2][len(prefix):]) or mass <= 0:
            raise ValueError("Invalid placed stock physics prop identity")
        return dict(actor=fields[2], class_path=fields[3], mass=mass)
    if fields[0] == HELD_ROW:
        if len(fields) != 7 or phase != "hosting" or not _token(fields[2]):
            raise ValueError("Invalid host stock grip confirmation")
        remaining = int(fields[6])
        if not 1 <= remaining <= 1500:
            raise ValueError("Invalid host stock grip expiry")
        return dict(lease=fields[2], serial=_serial(fields[3]), pawn=fields[4], actor=fields[5], remaining=remaining)
    if fields[0] == FILTER_ROW:
        if (len(fields) != 6 or phase != "connected" or not _token(fields[2])
                or fields[3] not in ("0", "1")):
            raise ValueError("Invalid guest stock grip filter counters")
        suppressed, passed = map(int, fields[4:])
        if min(suppressed, passed) < 0:
            raise ValueError("Invalid guest stock grip filter count")
        return dict(lease=fields[2], active=fields[3] == "1", suppressed=suppressed, passed=passed)
    if fields[0] == FILTER_READY_ROW:
        if len(fields) != 2 or phase != "connected":
            raise ValueError("Invalid stock grip filter availability")
        return True
    if phase != "connected":
        raise ValueError("Physics carry input belongs to the guest")
    if fields[0] == "native-prop-carry-input":
        if len(fields) < 11 or fields[4] not in _EDGES or not _class(fields[5]):
            raise ValueError("Invalid stock physics carry input")
        extra = fields[11:]
        if fields[4] == "grab" and len(extra) <= 2:
            details = _identity(extra, area)
        elif fields[4] != "grab" and len(extra) in (0, len(_RELEASE_LIMITS)):
            details = {"release_pose": _numbers(extra, _RELEASE_LIMITS)} if extra else {}
        else:
            raise ValueError("Invalid stock physics carry input")
        return dict(sequence=_serial(fields[2]), pawn=fields[3], edge=fields[4], class_path=fields[5],
                    pose=_numbers(fields[6:11], (1e7, 1e7, 1e7, 89.9, 180)), **details)
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

    def grip_confirmation(self, character):
        self._reset()
        game = self.service.last_game
        now = time.monotonic()
        holder = self.holders.get(character)
        if (not holder or holder.get("closing") or holder["pawn"] != self._pawn(character)
                or not 0 <= now - self.service.last_seen < .5):
            return None
        lease = holder["lease"]
        live = game.get(HELD, {}).get(lease)
        if (not live or live["serial"] != holder["sequence"] or live["pawn"] != holder["pawn"]
                or live["actor"].partition(" ")[2] != holder["identity"].get("placement")):
            return None
        remaining = int(min(live["remaining"] - 1000 * (now - self.service.last_seen),
                            1000 * (holder["deadline"] - now), 900))
        if remaining <= 0:
            return None
        return dict(epoch=self.epoch, lease=lease, serial=live["serial"], actor=live["actor"],
                    remaining=remaining, version=game["sequence"])

    async def operate(self, bridge, character, target):
        self._reset()
        fields = target.split("@") if isinstance(target, str) and len(target) <= 700 else []
        if len(fields) not in (6, 10, 11, 12, 6 + len(_RELEASE_LIMITS)):
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
        elif (len(payload) in (2, 2 + len(_RELEASE_LIMITS)) and holder
              and (holder["lease"], holder["pawn"]) == (lease, pawn) and number > holder["sequence"]):
            identity = holder["identity"]
            sample = payload[2:]
            args = ()
            if sample and not ("corpse" in identity or "loot_id" in identity):
                args = tuple(str(value) for value in _numbers(sample, _RELEASE_LIMITS))
            holder["closing"] = True
        else:
            raise party.RuleError("native_prop_carry_wrong_holder")
        snapshot = await bridge.command(ACTION, lease, pawn, edge, str(number), str(pitch), str(yaw), *args, timeout=4)
        proof = snapshot.get(PROOF, {})
        if (tuple(proof.get(key) for key in ("lease", "sequence", "edge", "pawn")) != (lease, number, edge, pawn)
                or any(proof.get(key) != identity.get(key) for key in ("corpse", "bone", "loot_id"))
                or (proof.get("release_pose_applied") and (edge == "grab" or not args))
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
        fields = target.split("@") if isinstance(target, str) and len(target) <= 180 else []
        if len(fields) != 9:
            raise party.RuleError("native_prop_carry_invalid_pose")
        epoch, lease, raw_sequence, *coordinates = fields
        self.apply_pose(bridge, character, epoch, lease, _serial(raw_sequence), _numbers(coordinates, _POSE_LIMITS))
        return {"ok": True}

    def apply_pose(self, bridge, character, epoch, lease, sequence, position):
        from .runtime import write_text

        self._reset()
        if epoch != self.epoch:
            raise party.RuleError("native_prop_carry_invalid_pose")
        now = time.monotonic()
        holder = self.holders.get(character)
        if (not holder or holder.get("closing") or holder["lease"] != lease or holder["pawn"] != self._pawn(character)
                or now >= holder["deadline"]):
            raise party.RuleError("native_prop_carry_wrong_holder")
        if sequence <= holder["sample"]:
            raise party.RuleError("native_prop_carry_stale_pose")
        # The game thread verifies reach against the live pawn before moving the handle.
        # The worker's periodic pawn snapshot can trail the actual carrier while running.
        allowed = {entry["lease"] for entry in self.holders.values() if entry["deadline"] > now}
        poses = {key: value for key, value in self.poses.items() if key in allowed}
        poses[lease] = "\t".join((lease, str(sequence), *(str(axis) for axis in position)))
        frame = "\n".join(poses.values()) + "\n"
        if bridge.local_bridge is None:
            # External test workers have no process-local bridge.
            write_text(bridge.directory / "native-prop-carry-poses", frame, newline="\n")
        elif not bridge.local_bridge.publish_prop_poses(frame):
            raise RuntimeError("The game could not queue the local prop poses")
        self.poses = poses
        holder["sample"], holder["deadline"] = sequence, now + 1.4


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
        filter_setting = os.getenv("SILVERFISH_COOP_PROP_GRIP_FILTER")
        self.filter_enabled = filter_setting != "0"
        self.filter_file_fixture = filter_setting == "1"
        self.filter_actor = self.filter_job = None
        self.filter_version = 0

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
        elif edge == "grab" and "loot_id" in row:
            suffix += ("loot:" + row["loot_id"],)
        elif edge != "grab" and service.gameplay.get(RELEASE_POSE) is True and "release_pose" in row:
            suffix = tuple(str(value) for value in row["release_pose"])
        target = "@".join((self.epoch, lease, edge, str(number), str(pitch), str(yaw), *suffix))
        try:
            returned = await service.gameplay_action(ACTION, operation, target)
            proof = returned.get(PROOF, {})
            if proof.get("sequence") != number:
                raise ValueError("The host action did not match the stock carry")
            self.lease, self.grab = (lease, number) if edge == "grab" else (None, None)
            self.filter_actor = None
            self.filter_version = 0
            if self.filter_enabled and edge == "grab" and row.get("placement"):
                actor = proof.get("actor", "")
                if proof.get("lease") == lease and proof.get("edge") == "grab" and actor.partition(" ")[2] == row["placement"]:
                    self.filter_actor = actor
            self.last_pose = None
        except (OSError, ValueError, RuntimeError) as error:
            self.lease = self.grab = None
            self.filter_actor = None
            service.control.event("native_prop_carry_refused", edge=edge, reason=str(error))
            if edge == "grab" and any(key in row for key in ("corpse", "placement", "loot_id")):
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

    async def _arm_filter(self, confirmation, seen):
        remaining = int(confirmation["remaining"] - 1000 * (time.monotonic() - seen))
        if self.lease != confirmation["lease"] or remaining < 150:
            return
        try:
            await self.service.bridge.command("native-prop-carry-arm-filter", self.lease, str(self.grab),
                                              self.filter_actor, str(remaining), timeout=.5)
        except (OSError, ValueError, RuntimeError):
            # Stock movement stays enabled when the local grip or fresh lease cannot be proved.
            pass

    def _poll_filter(self, grip):
        # Keep periodic lease renewals on the in-process bridge during ordinary play.
        if (not self.service.last_game.get(FILTER_READY)
                or (self.service.bridge.local_bridge is None and not self.filter_file_fixture) or not self.filter_actor
                or not self.lease or grip.get("grab") != self.grab
                or self.filter_job is not None and not self.filter_job.done()):
            return
        confirmation = self.service.gameplay.get(FILTER)
        if not isinstance(confirmation, dict):
            return
        if any(confirmation.get(key) != value for key, value in (
            ("epoch", self.epoch), ("lease", self.lease), ("serial", self.grab), ("actor", self.filter_actor),
        )):
            return
        version, remaining = confirmation.get("version"), confirmation.get("remaining")
        if type(version) is not int or version <= self.filter_version or type(remaining) is not int or not 0 < remaining <= 900:
            return
        self.filter_version = version
        self.filter_job = asyncio.create_task(self._arm_filter(confirmation, self.service.gameplay_seen))

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
        if self.filter_enabled:
            self._poll_filter(grip)
        if (self.lease and grip.get("grab") == self.grab and now >= self.next_pose
                and (self.pose is None or self.pose.done())):
            position = tuple(f"{axis:.1f}" for axis in grip["pose"])
            if position == self.last_pose and now < self.pose_heartbeat:
                return
            self.next_pose, self.sample = now + .1, self.sample + 1
            self.last_pose, self.pose_heartbeat = position, now + .45
            if service.gameplay.get(POSE_DATAGRAM) is True:
                service.transport.send_prop_pose(pack_pose(self.epoch, self.lease, self.sample, position))
            else:
                target = "@".join((self.epoch, self.lease, str(self.sample), *position))
                self.pose = asyncio.create_task(self._pose(target))

    def close(self):
        for task in (self.action, self.pose, self.filter_job):
            if task and not task.done():
                task.cancel()
        self.action = self.pose = None
        self.lease = self.grab = None
        self.filter_actor = self.filter_job = None
        self.filter_version = 0
        self.last_pose = self.pose_error = None
        self.edges.clear()
