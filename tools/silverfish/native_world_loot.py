"""Project host-committed stock loot into nearby peers' worlds."""

import math
import re
import time
import uuid
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import chain

from . import (native_enemy_shells, native_guest_drop_record, native_host_dig_live, native_loot,
               native_mushrooms, native_stock_enemy_drops, party)
from .authored_loot import valid_item_transform
from .content_paths import item_class_name, named_actor
from .native_gold import valid_guid
from .native_guest_drop_record import valid_position


KEY = "native_world_loot_items"
ROW = "native-world-loot-item"
ERROR_KEY = "native_world_loot_projection_errors"
ERROR_ROW = "native-world-loot-projection-error"
PHYSICS_ROW = "native-world-loot-physics"
PHYSICS = "native_world_loot_physics"
MOVING = "native_world_loot_moving"
FRAME = "native-world-loot-frame"
FRAME_REPLY = "native-world-loot-frame-reply"
RETIRE = "native-world-loot-retire"
PRESENT_CLAIM = "native-world-loot-present-claim"
PRESENTATION = "native_world_loot_pickup_presentation"
FIXTURE = "native-world-loot-fixture"
APPROACH = "native-world-loot-approach"
CAPABILITY = "native_world_loot_version"
SHARED_PICKUPS_VERSION = 2
CRATE_CLAIM = "native-loot-claim"
ENEMY_CLAIM = "native-enemy-loot-claim"
_TARGET = re.compile(r"world-loot:([0-9a-f]{32}):([0-9a-f]{32})(?::([1-9][0-9]{0,15}))?\Z")
_MAX_VISIBLE = 64
_VISIBILITY_RADIUS = 1800
_FALL_UNAVAILABLE = "stock_drop_projection_fall_unavailable"


def target_parts(target):
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    if not match:
        return None
    epoch, identity, sequence = match.groups()
    if sequence is None:
        return epoch, identity
    if (press := int(sequence)) < 2**53:
        return epoch, identity, press
    return None


def pawn_matches(area, pawn):
    return named_actor(area, pawn, "Player_FP_C")


def parse(fields, phase, area):
    if (len(fields) not in {8, 9} or phase not in {"hosting", "connected"}
            or (len(fields) == 9 and (phase != "connected" or fields[8] != "moving"))):
        raise ValueError("Unexpected stock world loot report")
    _, world, epoch, identity, pawn, actor, press, item_class = fields[:8]
    class_name = item_class_name(item_class)
    if (world != area or not pawn_matches(area, pawn) or not class_name
            or not party.REQUEST_ID.fullmatch(epoch) or not party.REQUEST_ID.fullmatch(identity)
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", press) or int(press) >= 2**53):
        raise ValueError("Invalid stock world loot report")
    if not named_actor(area, actor, class_name):
        raise ValueError("Invalid projected stock loot actor")
    report = dict(map=area, epoch=epoch, id=identity, pawn=pawn, actor=actor, class_path=item_class, sequence=int(press))
    if len(fields) == 9:
        report["moving"] = True
    return report


def parse_projection_error(fields, phase, area):
    if len(fields) != 6 or phase not in {"hosting", "connected"}:
        raise ValueError("Unexpected stock world loot projection error")
    _, world, epoch, identity, item_class, reason = fields
    if (world != area or not item_class_name(item_class)
            or not party.REQUEST_ID.fullmatch(epoch) or not party.REQUEST_ID.fullmatch(identity)
            or not reason.startswith("stock_drop_projection_") or len(reason) > 256):
        raise ValueError("Invalid stock world loot projection error")
    return dict(map=area, epoch=epoch, id=identity, class_path=item_class, reason=reason)


def parse_physics(fields, phase, area):
    _, world, epoch, identity, item_class, state, *values = fields
    if (phase != "hosting" or world != area or len(values) != 9
            or state not in {"moving", "settled"} or not item_class_name(item_class)
            or not party.REQUEST_ID.fullmatch(epoch) or not party.REQUEST_ID.fullmatch(identity)):
        raise ValueError("Invalid stock world loot physics report")
    pose = list(map(float, values))
    transform = dict(position=pose[:3], rotation=pose[3:6], scale=pose[6:])
    if not valid_item_transform(transform):
        raise ValueError("Invalid stock world loot physics transform")
    return dict(id=identity, epoch=epoch, class_path=item_class, state=state, transform=transform)


def parse_frame_reply(raw: str, request: tuple[str, ...]) -> dict:
    rows = [line.split("\t") for line in raw.splitlines()] if len(raw) <= 96_000 and raw.endswith("\n") else []
    if not rows or len(rows[0]) != 9 or len(rows) > 1 + 2 * _MAX_VISIBLE or len(request) < 4:
        raise RuntimeError("The stock world loot reply was malformed")
    tag, version, nonce, phase, area, pawn, epoch, status, message = rows[0]
    if (tag, version, nonce, area, epoch) != (FRAME_REPLY, "1", request[0], *request[2:4]):
        raise RuntimeError("The stock world loot reply did not match the request")
    if status != "accepted":
        raise RuntimeError(message or "The stock world loot frame was refused")
    if phase not in {"hosting", "connected"} or not pawn_matches(area, pawn):
        raise RuntimeError("The stock world loot reply did not match the game seat")
    readers = {ROW: (KEY, parse), ERROR_ROW: (ERROR_KEY, parse_projection_error), PHYSICS_ROW: (PHYSICS, parse_physics)}
    observed = {KEY: {}, ERROR_KEY: {}, PHYSICS: {}}
    try:
        expected = {identity: item_class for identity, item_class, _ in (value.split("|", 2) for value in request[4:])}
        for line in rows[1:]:
            key, read = readers[line[0]]
            item = read(line, phase, area)
            identity = item["id"]
            if (item["epoch"] != epoch or expected.get(identity) != item["class_path"]
                    or identity in observed[key] or item.get("pawn", pawn) != pawn):
                raise ValueError("Unexpected actor in the stock world loot reply")
            observed[key][identity] = item
        if observed[KEY].keys() & observed[ERROR_KEY].keys() or observed[PHYSICS].keys() - observed[KEY].keys():
            raise ValueError("Incompatible actor readbacks in the stock world loot reply")
    except (KeyError, ValueError) as error:
        raise RuntimeError("The stock world loot reply contained invalid actors") from error
    return {FRAME_REPLY: (phase, area, pawn), **observed}


@dataclass(frozen=True)
class Item:
    identity: str
    class_path: str
    transform: dict
    claim_action: str | None
    claim_target: str | None
    stack_count: int
    ground_snap: bool = False
    carried_motion: bool = False

    @property
    def carryable(self):
        return self.claim_action in {CRATE_CLAIM, native_guest_drop_record.PICKUP_ACTION, native_stock_enemy_drops.CLAIM}


@lru_cache(maxsize=4096)
def crate_identity(roll_id, guid):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"{roll_id}:{guid}").hex


def _crate_items(rolls, area, observed):
    for crate, roll in native_loot.rolls_in_area(rolls, area).items():
        if "|" in crate and observed.get(crate, {}).get("state") != "exhausted":
            continue
        roll_id = roll.get("id")
        if not isinstance(roll_id, str) or not party.REQUEST_ID.fullmatch(roll_id):
            continue
        for row in roll["items"]:
            guid = row.get("guid")
            if valid_guid(guid) and not row.get("claimed_by"):
                identity = crate_identity(roll_id, guid)
                yield Item(identity, row.get("class_path"), row.get("transform"), CRATE_CLAIM, f"{guid}@{crate}",
                           _display_count(row.get("ammo")))


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
        yield Item(identity, row.get("class_path"), transform, ENEMY_CLAIM, f"{native_enemy_shells.PREFIX}{zone}:{guid}",
                   _display_count(row.get("quantity")))


def player_drop_identity(drop_id):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"player-drop:{drop_id}").hex


def item_is_moving(host, identity):
    game = host.last_game
    world_loot = getattr(host, "world_loot", None)
    return (game.get(PHYSICS, {}).get(identity, {}).get("state") == "moving"
            or world_loot is not None and identity in world_loot.moving_ids(game.get("map"), host.zone_epoch))


def _player_items(drops, area):
    if not isinstance(drops, dict):
        return
    for drop_id, row in drops.items():
        if (not native_guest_drop_record.valid_id(drop_id) or not isinstance(row, dict)
                or (row.get("drop_id"), row.get("map")) != (drop_id, area)
                or row.get("claimed_by") or not valid_guid(row.get("guid"))
                or not native_guest_drop_record.valid_quantity(row.get("class_path"), row.get("quantity"))):
            continue
        transform = row.get("transform") or {"position": row.get("location"), "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        yield Item(player_drop_identity(drop_id), row["class_path"], transform,
                   native_guest_drop_record.PICKUP_ACTION, drop_id, row["quantity"], ground_snap="transform" not in row)


def stock_enemy_identity(guid, visual_id=None):
    return visual_id or uuid.uuid5(uuid.NAMESPACE_OID, f"stock-enemy-loot:{guid}").hex


def _stock_enemy_items(drops, area):
    if not isinstance(drops, dict):
        return
    for guid, row in drops.items():
        if valid_guid(guid) and isinstance(row, dict) and row.get("map") == area:
            identity = stock_enemy_identity(guid, row.get("visual_id"))
            if not isinstance(identity, str) or not party.REQUEST_ID.fullmatch(identity):
                continue
            yield Item(identity, row.get("class_path"), row.get("transform"),
                       native_stock_enemy_drops.CLAIM, guid, _display_count(row.get("ammo")),
                       ground_snap=not row.get("settled"))


def _moving_enemy_items(frame, area, zone):
    if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, zone):
        return
    items = frame.get("items")
    if not isinstance(items, dict) or len(items) > native_stock_enemy_drops.MOTION_LIMIT:
        return
    for identity, row in items.items():
        if isinstance(identity, str) and party.REQUEST_ID.fullmatch(identity) and isinstance(row, dict):
            yield Item(identity, row.get("class_path"), row.get("transform"), None, None, 0,
                       carried_motion=row.get("carried") is True)


def _display_count(value):
    return value if type(value) is int and 0 < value < 2**31 else 0


def _mushroom_items(frame, area, zone):
    for identity, row in native_mushrooms.visible(frame, area, zone):
        yield Item(identity, row.get("class_path"), row.get("transform"), native_mushrooms.CLAIM,
                   identity, _display_count(row.get("amount")))


def _quest_package(frame, area, zone):
    if not isinstance(frame, dict) or (frame.get("map"), frame.get("epoch")) != (area, zone):
        return
    guid, contact = frame.get("guid"), frame.get("contact")
    dig = native_host_dig_live
    if area != dig.CROSSROADS or not valid_guid(guid) or not dig._valid_contact(contact, frame.get("material")):
        return
    x, y, z = contact
    transform = {"position": [x, y, z + 10], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
    yield Item(dig.package_identity(guid), dig.stock.PACKAGE_CLASS, transform, dig.COLLECT, guid, 0)


def nearby(game, rolls, zone, *, enemy_frame=None, enemy_rolls=None, player_drops=None, stock_enemy_drops=None,
           mushroom_frame=None, enemy_motion=None, carried=None, quest_package=None, pinned=()):
    area = game.get("map")
    pawn = game.get("pawns", {}).get("local", {})
    origin = pawn.get("location")
    if not pawn_matches(area, pawn.get("name")) or not valid_position(origin):
        return {}
    other_origins = tuple(row["location"] for name, row in game.get("pawns", {}).items()
                          if game.get("state") == "hosting" and name != "local" and valid_position(row.get("location")))
    visible = {}
    moves = {item.identity: item for item in _moving_enemy_items(enemy_motion, area, zone)}
    carried_ids = {key for key in carried if isinstance(key, str) and party.REQUEST_ID.fullmatch(key)} if (
        isinstance(carried, list) and len(carried) <= _MAX_VISIBLE) else set()
    for item in chain(_crate_items(rolls, area, game.get("native_loot_crates", {})),
                      _enemy_items(enemy_frame, enemy_rolls or {}, area, zone),
                      _player_items(player_drops or {}, area), _stock_enemy_items(stock_enemy_drops, area),
                      _mushroom_items(mushroom_frame, area, zone), _quest_package(quest_package, area, zone), moves.values()):
        if item.carryable and item.identity in carried_ids:
            latest = moves.get(item.identity)
            transform = latest.transform if latest and latest.carried_motion and latest.class_path == item.class_path else item.transform
            item = replace(item, transform=transform, claim_action=None, claim_target=None, ground_snap=False)
        elif item.carried_motion:
            # Such a frame needs a matching stock ledger item from this campaign.
            continue
        position = item.transform.get("position") if type(item.transform) is dict else None
        if item.identity in visible or not valid_position(position):
            continue
        distance = math.dist(origin, position)
        if item.carryable and distance > _VISIBILITY_RADIUS and other_origins:
            distance = min(math.dist(other, position) for other in other_origins)
        held = item.identity in pinned
        if not held and distance > _VISIBILITY_RADIUS:
            continue
        if item_class_name(item.class_path) and valid_item_transform(item.transform):
            visible[item.identity] = (not held, distance, item)
    ordered = sorted(visible.values(), key=lambda entry: (entry[0], entry[1], entry[2].identity))
    return {item.identity: item for _, _, item in ordered[:_MAX_VISIBLE]}


class Projection:
    def __init__(self):
        self.epoch = None
        self.seat = None
        self.presses = {}
        self.previous = None
        self.next_sync = 0.0
        self.last_error = None
        self.items = {}
        self.retired = set()
        self.moving = {}
        self.settled = {}
        self._fall_scope = None
        self._falling = {}

    def note_player_drop(self, scope, drop_id):
        """Simulate a newly committed drop on the host for this visit only."""
        area, zone, _ = scope
        if not (native_guest_drop_record.supported_map(area) and native_guest_drop_record.valid_id(zone)
                and native_guest_drop_record.valid_id(drop_id)):
            return
        if self._fall_scope != (area, zone):
            self._falling.clear()
            self._fall_scope = area, zone
        self._falling[player_drop_identity(drop_id)] = drop_id

    def moving_ids(self, area, zone):
        pending = self._falling if self._fall_scope == (area, zone) else ()
        return self.moving.keys() | pending

    def observe_physics(self, state, game):
        latest = {}
        for identity, physics in game.get(PHYSICS, {}).items():
            item = self.items.get(identity)
            if not item or not item.carryable or physics["epoch"] != self.epoch or physics["class_path"] != item.class_path:
                continue
            if physics["state"] == "moving":
                latest[identity] = physics
                self.settled.pop(identity, None)
            elif self.settled.get(identity) != physics["transform"]:
                if self._settle(state, identity, item, physics["transform"]):
                    self.settled[identity] = physics["transform"]
                    self._falling.pop(identity, None)
        self.moving = latest

    @staticmethod
    def _settle(state, identity, item, transform):
        target = item.claim_target
        if item.claim_action == CRATE_CLAIM:
            guid, crate = target.split("@", 1)
            roll = state.get(native_loot.STATE_KEY, {}).get(crate, {})
            if crate_identity(roll.get("id"), guid) != identity:
                return False
            row = next((row for row in roll.get("items", ()) if row["guid"] == guid), None)
        elif item.claim_action == native_stock_enemy_drops.CLAIM:
            row = state.get(native_stock_enemy_drops.STATE, {}).get(target)
            if row and stock_enemy_identity(target, row.get("visual_id")) != identity:
                return False
        else:
            row = state.get(native_guest_drop_record.STATE, {}).get(target)
        if not row or row.get("claimed_by") or row.get("claim_id") or row["class_path"] != item.class_path:
            return False
        row["transform"] = transform
        if item.claim_action == native_stock_enemy_drops.CLAIM:
            row["settled"] = True
        return True

    async def sync(self, bridge, game, rolls, generation, zone, *, active, enemy_frame=None, enemy_rolls=None,
                   player_drops=None, stock_enemy_drops=None, mushroom_frame=None, enemy_motion=None, carried=None,
                   quest_package=None):
        phase = game.get("state")
        if phase not in {"hosting", "connected"} or not isinstance(zone, str):
            self.previous = None
            self.seat = None
            self.items = {}
            self.moving.clear()
            self._falling.clear()
            self._fall_scope = None
            return game
        area = game.get("map")
        epoch = uuid.uuid5(uuid.NAMESPACE_OID, f"{generation}:{zone}:{area}:world-loot").hex
        if epoch != self.epoch:
            self.retired.clear()
            self.presses.clear()
            self.moving.clear()
            self.settled.clear()
        if phase != "hosting" or self._fall_scope != (area, zone):
            self._falling.clear()
            self._fall_scope = None
        elif self._falling:
            drops = player_drops or {}
            self._falling = {identity: drop_id for identity, drop_id in self._falling.items()
                             if drop_id in drops and "transform" not in drops[drop_id]}
        self.items = nearby(game, rolls, zone, enemy_frame=enemy_frame, enemy_rolls=enemy_rolls,
                            player_drops=player_drops, stock_enemy_drops=stock_enemy_drops,
                            mushroom_frame=mushroom_frame, enemy_motion=enemy_motion, carried=carried,
                            quest_package=quest_package if phase == "connected" else None,
                            pinned=self.moving_ids(area, zone)) if active else {}
        for identity in self.retired:
            self.items.pop(identity, None)
        def encode(identity, item):
            if item.claim_action is None:
                mode = ("moving",)
            elif identity in self._falling:
                mode = ("fall",)
            elif item.carryable:
                mode = ("carry-ground" if item.ground_snap else "carry",)
            else:
                mode = ("ground",) if item.ground_snap else ()
            pose = (str(axis) for field in ("position", "rotation", "scale") for axis in item.transform[field])
            return "|".join((identity, item.class_path, *mode, *pose, str(item.stack_count)))

        args = tuple(encode(identity, item) for identity, item in sorted(self.items.items()))
        seat = (phase, area, game.get("pawns", {}).get("local", {}).get("name"))
        signature = (epoch, seat, args)
        now = time.monotonic()
        if not args and not game.get(KEY) and self.previous in (None, signature):
            return game
        if signature == self.previous and now < self.next_sync:
            return game
        self.next_sync = now + .65
        self.previous = signature
        snapshot = await bridge.command(FRAME, area, epoch, *args, timeout=3)
        if FRAME_REPLY in snapshot:
            if snapshot[FRAME_REPLY] != seat:
                raise RuntimeError("The stock world loot reply belonged to a different game seat")
            merged = game.copy()
            for key in (KEY, ERROR_KEY, PHYSICS):
                if snapshot[key]:
                    merged[key] = snapshot[key]
                else:
                    merged.pop(key, None)
            snapshot = merged
        for identity, error in snapshot.get(ERROR_KEY, {}).items():
            if error["epoch"] == epoch and error["reason"] == _FALL_UNAVAILABLE:
                self._falling.pop(identity, None)
        self.epoch = epoch
        self.seat = seat
        self.last_error = None
        return snapshot

    def claim_target(self, game, target):
        epoch, identity = target[:2]
        item = self.items.get(identity)
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        seat = (game.get("state"), game.get("map"), pawn)
        if epoch != self.epoch or not item or item.claim_action is None or not pawn or seat != self.seat:
            raise party.RuleError("native_world_loot_intent_stale")
        if identity in self.moving or identity in self._falling:
            raise party.RuleError("native_world_loot_item_in_motion")
        if len(target) == 3:
            # The game thread sends the native receipt with the press. The next
            # status snapshot can arrive after the input stream wakes the worker.
            sequence = target[2]
            if sequence <= self.presses.get(identity, 0):
                raise party.RuleError("native_world_loot_intent_stale")
            self.presses[identity] = sequence
        else:
            projected = game.get(KEY, {}).get(identity, {})
            if (projected.get("epoch") != epoch or (projected.get("map"), projected.get("pawn")) != seat[1:]
                    or projected.get("sequence", 0) <= 0 or projected.get("class_path") != item.class_path):
                raise party.RuleError("native_world_loot_intent_stale")
        return item.claim_action, item.claim_target

    def _remember_retirement(self, identity):
        self.retired.add(identity)
        self.items.pop(identity, None)
        self._falling.pop(identity, None)

    @staticmethod
    def _after_retirement(game, snapshot, identity):
        if "state" in snapshot:
            return snapshot
        return {**game, KEY: {key: item for key, item in game.get(KEY, {}).items() if key != identity}}

    async def retire_claim(self, bridge, game, target):
        epoch, identity = target[:2]
        if epoch != self.epoch:
            return game
        self._remember_retirement(identity)
        snapshot = await bridge.command(RETIRE, epoch, identity, timeout=3)
        if "state" not in snapshot and snapshot.get("ack", {}).get("message") != "stock world loot retired":
            raise RuntimeError("The claimed stock world item did not verify retirement")
        if identity in snapshot.get(KEY, {}):
            raise RuntimeError("The claimed stock world item did not retire")
        return self._after_retirement(game, snapshot, identity)

    async def present_host_claim(self, host, bridge, target, operation, result):
        game = host.last_game
        identity = target[1]
        item = self.items.get(identity)
        claim = result.get("native_loot_claim", {})
        if (not item or item.claim_action != CRATE_CLAIM
                or claim.get("claimed_by") != "host" or claim.get("class_path") != item.class_path
                or f"{claim.get('guid')}@{result.get('native_loot_crate')}" != item.claim_target):
            return await self.retire_claim(bridge, game, target)

        cue = host.pickup_audio.frame(game, host.zone_epoch, operation=operation)
        presented = await self.present_claim(host, bridge, target, operation, cue, generation=host.control.generation)
        return await self.retire_claim(bridge, game, target) if presented is None else presented

    async def present_claim(self, seat, bridge, target, operation, cue, *, generation, inventory=(), source=None):
        game, playback, control, audio_epoch = seat.last_game, seat.pickup_playback, seat.control, seat.zone_epoch
        epoch, identity = target[:2]
        # A snapshot can remove the replica while this guest's claim reply is still in flight.
        item = self.items.get(identity)
        if item is None and inventory and source is not None and source.identity == identity:
            item = source
        if (bridge.local_bridge is None or game.get(PRESENTATION) is not True or epoch != self.epoch
                or not item or identity in self.moving or (not isinstance(cue, dict) and not inventory)):
            return None
        args, asked = playback.prepare(game, cue, audio_epoch, generation) if isinstance(cue, dict) else ((), {})
        audible = len(args) == 1 and asked.keys() == {operation} and args[0].split("|", 2)[1] == item.class_path
        if not audible and not inventory:
            return None
        if inventory and inventory[1] != item.class_path:
            return None

        area, pending = game["map"], playback.pending
        if audible:
            pending.add(operation)
        self._remember_retirement(identity)
        try:
            snapshot = await bridge.command(PRESENT_CLAIM, epoch, identity, area, audio_epoch,
                                            args[0] if audible else "-", *inventory, timeout=3)
        except (OSError, RuntimeError, TimeoutError) as error:
            control.event("native_world_loot_retirement_deferred", reason=str(error))
            return game
        finally:
            pending.discard(operation)

        status, separator, heard = snapshot.get("ack", {}).get("message", "").partition("|")
        heard, _, inventory_result = heard.partition("|")
        if separator and audible and pending is playback.pending:
            playback.acknowledge(control, area, asked, heard)
        if not separator or status != "retired" or identity in snapshot.get(KEY, {}):
            reason = status if status.startswith("world_loot_retirement_") else "The claimed stock world item did not retire"
            control.event("native_world_loot_retirement_deferred", reason=reason)
            projected = snapshot if "state" in snapshot else game
        else:
            projected = self._after_retirement(game, snapshot, identity)
        if inventory and inventory_result in ("added", "present", "merged"):
            return {**projected, "pickup_inventory_result": inventory_result}
        return projected

    async def fixture(self, bridge, game, identity):
        shown = game.get(KEY, {}).get(identity, {})
        if not shown or shown.get("epoch") != self.epoch:
            raise party.RuleError("native_world_loot_fixture_scope_unavailable")
        snapshot = await bridge.command(FIXTURE, identity, timeout=4)
        observed = snapshot.get(KEY, {}).get(identity, {})
        if observed.get("sequence", 0) <= shown["sequence"] or observed != {**shown, "sequence": observed["sequence"]}:
            raise RuntimeError("The stock world item did not intercept the headless Interact press")
        return snapshot
