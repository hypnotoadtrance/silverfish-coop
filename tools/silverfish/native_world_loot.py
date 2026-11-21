"""Project host-committed stock loot into nearby peers' worlds."""

import math
import re
import time
import uuid

from . import native_loot, party
from .authored_loot import valid_item_transform
from .native_gold import valid_guid
from .native_guest_drop_record import pawn_matches, valid_position


KEY = "native_world_loot_items"
ROW = "native-world-loot-item"
FRAME = "native-world-loot-frame"
FIXTURE = "native-world-loot-fixture"
_TARGET = re.compile(r"world-loot:([0-9a-f]{32}):([0-9a-f]{32})\Z")
_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_./-]+\.([A-Za-z0-9_-]+_C)\Z")
_MAX_VISIBLE = 64
_VISIBILITY_RADIUS = 1800


def target_parts(target):
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    return match.groups() if match else None


def parse(fields, phase, area):
    if len(fields) != 8 or phase not in {"hosting", "connected"}:
        raise ValueError("Unexpected stock world loot report")
    _, world, epoch, identity, pawn, actor, press, item_class = fields
    match = _CLASS.fullmatch(item_class)
    if (world != area or not pawn_matches(area, pawn) or not match or len(item_class) > 256
            or not party.REQUEST_ID.fullmatch(epoch) or not party.REQUEST_ID.fullmatch(identity)
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", press) or int(press) >= 2**53):
        raise ValueError("Invalid stock world loot report")
    class_name = match[1]
    prefix = f"{class_name} {area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{class_name}_"
    if not actor.startswith(prefix) or not re.fullmatch(r"[0-9]{1,10}", actor[len(prefix):]):
        raise ValueError("Invalid projected stock loot actor")
    return dict(map=area, epoch=epoch, id=identity, pawn=pawn, actor=actor,
                class_path=item_class, sequence=int(press))


def nearby(game, rolls):
    area = game.get("map")
    pawn = game.get("pawns", {}).get("local", {})
    origin = pawn.get("location")
    if not pawn_matches(area, pawn.get("name")) or not valid_position(origin):
        return {}
    visible = []
    for crate, roll in native_loot.published(rolls, area).items():
        roll_id = roll.get("id")
        if not isinstance(roll_id, str) or not party.REQUEST_ID.fullmatch(roll_id):
            continue
        for row in roll["items"]:
            if row.get("claimed_by"):
                continue
            guid, item, transform = row.get("guid"), row.get("class_path"), row.get("transform")
            if (not valid_guid(guid) or not isinstance(item, str) or len(item) > 256
                    or not _CLASS.fullmatch(item) or not valid_item_transform(transform)):
                continue
            distance = math.dist(origin, transform["position"])
            if distance > _VISIBILITY_RADIUS:
                continue
            identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll_id}:{guid}").hex
            visible.append((distance, identity, crate, row))
    return {identity: (crate, row) for _, identity, crate, row in sorted(visible)[:_MAX_VISIBLE]}


class Projection:
    def __init__(self):
        self.epoch = None
        self.previous = None
        self.next_sync = 0.0
        self.last_error = None

    async def sync(self, bridge, game, rolls, generation, zone, *, active):
        phase = game.get("state")
        if phase not in {"hosting", "connected"} or not isinstance(zone, str):
            self.previous = None
            return game
        area = game.get("map")
        epoch = uuid.uuid5(uuid.NAMESPACE_OID, f"{generation}:{zone}:{area}:world-loot").hex
        rows = nearby(game, rolls) if active else {}
        args = tuple("|".join((identity, row["class_path"], *(str(axis)
            for field in ("position", "rotation", "scale") for axis in row["transform"][field])))
            for identity, (_, row) in rows.items())
        signature = (epoch, args)
        now = time.monotonic()
        if not args and not game.get(KEY) and self.previous in (None, signature):
            return game
        if signature == self.previous and now < self.next_sync:
            return game
        self.next_sync = now + .65
        self.previous = signature
        snapshot = await bridge.command(FRAME, area, epoch, *args, timeout=3)
        self.epoch = epoch
        self.last_error = None
        return snapshot

    def claim_target(self, game, rolls, target):
        epoch, identity = target
        projected = game.get(KEY, {}).get(identity, {})
        crate, row = nearby(game, rolls).get(identity, (None, None))
        if (epoch != self.epoch or not row or projected.get("epoch") != epoch
                or projected.get("sequence", 0) <= 0 or projected.get("class_path") != row["class_path"]):
            raise party.RuleError("native_world_loot_intent_stale")
        return f"{row['guid']}@{crate}"

    async def fixture(self, bridge, game, identity):
        shown = game.get(KEY, {}).get(identity, {})
        if not shown or shown.get("epoch") != self.epoch:
            raise party.RuleError("native_world_loot_fixture_scope_unavailable")
        snapshot = await bridge.command(FIXTURE, identity, timeout=4)
        if snapshot.get(KEY, {}).get(identity) != {**shown, "sequence": shown["sequence"] + 1}:
            raise RuntimeError("The stock world item did not intercept the headless Interact press")
        return snapshot
