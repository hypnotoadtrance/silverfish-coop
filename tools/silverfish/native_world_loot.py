"""Project host-committed stock loot into nearby peers' worlds."""

import math
import re
import time
import uuid
from dataclasses import dataclass
from itertools import chain

from . import native_enemy_shells, native_guest_drop_record, native_loot, native_stock_enemy_drops, party
from .authored_loot import valid_item_transform
from .native_gold import valid_guid
from .native_guest_drop_record import valid_position


KEY = "native_world_loot_items"
ROW = "native-world-loot-item"
ERROR_KEY = "native_world_loot_projection_errors"
ERROR_ROW = "native-world-loot-projection-error"
FRAME = "native-world-loot-frame"
RETIRE = "native-world-loot-retire"
FIXTURE = "native-world-loot-fixture"
APPROACH = "native-world-loot-approach"
CAPABILITY = "native_world_loot_version"
SHARED_PICKUPS_VERSION = 2
CRATE_CLAIM = "native-loot-claim"
ENEMY_CLAIM = "native-enemy-loot-claim"
_TARGET = re.compile(r"world-loot:([0-9a-f]{32}):([0-9a-f]{32})\Z")
_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_./-]+\.([A-Za-z0-9_-]+_C)\Z")
_AREA = re.compile(r"/Game/Classes/Maps/[A-Za-z0-9_/]+/([A-Za-z0-9_-]+)\Z")
_MAX_VISIBLE = 64
_VISIBILITY_RADIUS = 1800


def target_parts(target):
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    return match.groups() if match else None


def pawn_matches(area, pawn):
    match = _AREA.fullmatch(area) if isinstance(area, str) else None
    if not match or not isinstance(pawn, str):
        return False
    prefix = f"Player_FP_C {area}.{match[1]}:PersistentLevel.Player_FP_C_"
    return pawn.startswith(prefix) and re.fullmatch(r"[0-9]{1,10}", pawn[len(prefix):]) is not None


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


def parse_projection_error(fields, phase, area):
    if len(fields) != 6 or phase not in {"hosting", "connected"}:
        raise ValueError("Unexpected stock world loot projection error")
    _, world, epoch, identity, item_class, reason = fields
    if (world != area or not _CLASS.fullmatch(item_class) or len(item_class) > 256
            or not party.REQUEST_ID.fullmatch(epoch) or not party.REQUEST_ID.fullmatch(identity)
            or not reason.startswith("stock_drop_projection_") or len(reason) > 256):
        raise ValueError("Invalid stock world loot projection error")
    return dict(map=area, epoch=epoch, id=identity, class_path=item_class, reason=reason)


@dataclass(frozen=True)
class Item:
    identity: str
    class_path: str
    transform: dict
    claim_action: str
    claim_target: str
    ground_snap: bool = False


def _crate_items(rolls, area):
    for crate, roll in native_loot.published(rolls, area).items():
        roll_id = roll.get("id")
        if not isinstance(roll_id, str) or not party.REQUEST_ID.fullmatch(roll_id):
            continue
        for row in roll["items"]:
            guid = row.get("guid")
            if valid_guid(guid) and not row.get("claimed_by"):
                identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll_id}:{guid}").hex
                yield Item(identity, row.get("class_path"), row.get("transform"), CRATE_CLAIM, f"{guid}@{crate}")


def _enemy_items(frame, rolls, area, zone):
    if (not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, zone)
            or not native_enemy_shells.valid_epoch(zone)):
        return
    shells = frame.get("shells")
    if not isinstance(shells, dict) or len(shells) > native_enemy_shells.LIMIT:
        return
    for guid, snapshot in shells.items():
        row = rolls.get(guid)
        if (not valid_guid(guid) or not isinstance(snapshot, list) or len(snapshot) != 4
                or not isinstance(row, dict) or row.get("claimed_by")
                or (row.get("map"), row.get("guid"), row.get("quantity"), row.get("position"))
                != (area, guid, snapshot[0], snapshot[1:])):
            continue
        identity = uuid.uuid5(uuid.NAMESPACE_OID, f"enemy-loot:{guid}").hex
        transform = {"position": row["position"], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        yield Item(identity, row.get("class_path"), transform, ENEMY_CLAIM, f"{native_enemy_shells.PREFIX}{zone}:{guid}")


def player_drop_identity(drop_id):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"player-drop:{drop_id}").hex


def _player_items(drops, area):
    if not isinstance(drops, dict):
        return
    for drop_id, row in drops.items():
        if (not native_guest_drop_record.valid_id(drop_id) or not isinstance(row, dict)
                or (row.get("drop_id"), row.get("map")) != (drop_id, area)
                or row.get("claimed_by") or not valid_guid(row.get("guid"))
                or not native_guest_drop_record.valid_quantity(row.get("class_path"), row.get("quantity"))):
            continue
        transform = {"position": row.get("location"), "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        yield Item(player_drop_identity(drop_id), row["class_path"], transform,
                   native_guest_drop_record.PICKUP_ACTION, drop_id, True)


def stock_enemy_identity(guid):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"stock-enemy-loot:{guid}").hex


def _stock_enemy_items(drops, area):
    if not isinstance(drops, dict):
        return
    for guid, row in drops.items():
        if valid_guid(guid) and isinstance(row, dict) and row.get("map") == area:
            yield Item(stock_enemy_identity(guid), row.get("class_path"), row.get("transform"), native_stock_enemy_drops.CLAIM, guid)


def nearby(game, rolls, zone, *, enemy_frame=None, enemy_rolls=None, player_drops=None, stock_enemy_drops=None):
    area = game.get("map")
    pawn = game.get("pawns", {}).get("local", {})
    origin = pawn.get("location")
    if not pawn_matches(area, pawn.get("name")) or not valid_position(origin):
        return {}
    visible = []
    for item in chain(_crate_items(rolls, area), _enemy_items(enemy_frame, enemy_rolls or {}, area, zone),
                      _player_items(player_drops or {}, area), _stock_enemy_items(stock_enemy_drops, area)):
        if (not isinstance(item.class_path, str) or len(item.class_path) > 256
                or not _CLASS.fullmatch(item.class_path) or not valid_item_transform(item.transform)):
            continue
        distance = math.dist(origin, item.transform["position"])
        if distance <= _VISIBILITY_RADIUS:
            visible.append((distance, item))
    visible.sort(key=lambda entry: (entry[0], entry[1].identity))
    return {item.identity: item for _, item in visible[:_MAX_VISIBLE]}


class Projection:
    def __init__(self):
        self.epoch = None
        self.previous = None
        self.next_sync = 0.0
        self.last_error = None
        self.items = {}
        self.retired = set()

    async def sync(self, bridge, game, rolls, generation, zone, *, active, enemy_frame=None, enemy_rolls=None,
                   player_drops=None, stock_enemy_drops=None):
        phase = game.get("state")
        if phase not in {"hosting", "connected"} or not isinstance(zone, str):
            self.previous = None
            self.items = {}
            return game
        area = game.get("map")
        epoch = uuid.uuid5(uuid.NAMESPACE_OID, f"{generation}:{zone}:{area}:world-loot").hex
        if epoch != self.epoch:
            self.retired.clear()
        self.items = nearby(game, rolls, zone, enemy_frame=enemy_frame, enemy_rolls=enemy_rolls,
                            player_drops=player_drops, stock_enemy_drops=stock_enemy_drops) if active else {}
        for identity in self.retired:
            self.items.pop(identity, None)
        args = tuple("|".join((identity, item.class_path, *(("ground",) if item.ground_snap else ()), *(str(axis)
            for field in ("position", "rotation", "scale") for axis in item.transform[field])))
            for identity, item in sorted(self.items.items()))
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

    def claim_target(self, game, target):
        epoch, identity = target
        projected = game.get(KEY, {}).get(identity, {})
        item = self.items.get(identity)
        if (epoch != self.epoch or not item or projected.get("epoch") != epoch
                or projected.get("map") != game.get("map")
                or projected.get("pawn") != game.get("pawns", {}).get("local", {}).get("name")
                or projected.get("sequence", 0) <= 0 or projected.get("class_path") != item.class_path):
            raise party.RuleError("native_world_loot_intent_stale")
        return item.claim_action, item.claim_target

    async def retire_claim(self, bridge, game, target):
        epoch, identity = target
        if epoch != self.epoch:
            return game
        self.retired.add(identity)
        self.items.pop(identity, None)
        snapshot = await bridge.command(RETIRE, epoch, identity, timeout=3)
        if identity in snapshot.get(KEY, {}):
            raise RuntimeError("The claimed stock world item did not retire")
        return snapshot

    async def fixture(self, bridge, game, identity):
        shown = game.get(KEY, {}).get(identity, {})
        if not shown or shown.get("epoch") != self.epoch:
            raise party.RuleError("native_world_loot_fixture_scope_unavailable")
        snapshot = await bridge.command(FIXTURE, identity, timeout=4)
        observed = snapshot.get(KEY, {}).get(identity, {})
        if observed.get("sequence", 0) <= shown["sequence"] or observed != {**shown, "sequence": observed["sequence"]}:
            raise RuntimeError("The stock world item did not intercept the headless Interact press")
        return snapshot
