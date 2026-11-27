"""Persist and transfer physical items from verified stock spawns."""

import asyncio
import json
import math
import re
import time
import uuid

from . import native_pickup_audio, party
from .authored_loot import valid_item_transform
from .content_paths import item_class_name, named_actor
from .native_gold import valid_guid
from .native_map_preflight import valid_campaign_package
from .travel import BLACK_MARKET


TAG = "native-stock-enemy-drop"
KEY = "native_stock_enemy_drops"
FIXTURE = "native-stock-enemy-escrow-fixture"
BROKER_KEY_FIXTURE = "native-stock-broker-key-fixture"
BROKER_CUE_TAG = "native-stock-broker-cue-removed"
BROKER_CUE = "native_stock_broker_cue_removed"
PROOF_TAG = "native-stock-enemy-escrow"
PROOF_KEY = "native_stock_enemy_escrow"
STATE = "native_stock_enemy_loot"
BANK = "native-stock-enemy-loot-bank"
BANKED = "native-stock-enemy-loot-banked"
BANK_KEY = "native_stock_enemy_loot_banked"
MEMORY_CAPABILITY = "native_stock_enemy_memory"
CLAIM = "native-stock-enemy-loot-claim"
CLAIMED = "native-stock-enemy-loot-claimed"
CLAIM_KEY = "native_stock_enemy_loot_claim"
_ACTOR_PATH = re.compile(r"(/[A-Za-z0-9_/]+)\.([A-Za-z0-9_]+):PersistentLevel\.[A-Za-z0-9_-]+")
_ACTOR_TYPE = re.compile(r"[A-Za-z0-9_-]+_C")
_SERIAL = re.compile(r"[1-9][0-9]{0,15}")
MOTION_INTERVAL = .08
MOTION_TTL = .75
MOTION_LIMIT = 32
_MEMORY_BANK_TURN_SECONDS = .012
_BANK_REFUSALS_BEFORE_RETIREMENT = frozenset({
    "stock_enemy_escrow_actor_unavailable", "stock_enemy_escrow_actor_unverified",
    "stock_enemy_escrow_host_not_ready", "stock_enemy_escrow_drop_settling",
    "stock_enemy_escrow_scope_unavailable", "stock_enemy_escrow_transform_unavailable",
    "stock_enemy_escrow_fixture_save_unavailable", "stock_enemy_escrow_capture_unavailable",
    "native_loot_escrow_slot_unavailable", "native_loot_escrow_could_not_create_save",
    "native_loot_inventory_schema_unavailable", "native_loot_save_attachment_schema_unavailable",
    "native_loot_escrow_save_attached_to_owner", "native_loot_live_stock_escrow_save_missing",
    "native_loot_escrow_save_outer_unqualified", "native_loot_blank_escrow_required",
    "native_loot_stock_actor_conversion_unverified", "native_loot_escrow_append_unverified",
    "native_loot_escrow_payload_unverified",
})


def _actor_path(area, path):
    match = _ACTOR_PATH.fullmatch(path) if isinstance(path, str) and len(path) <= 512 else None
    if not match or not valid_campaign_package(match[1]):
        return False
    # The game-thread observer already qualified visible sublevels against area.
    # Their World export names can differ from the asset package leaf.
    return match[1] != area or match[2] == area.rsplit("/", 1)[-1]


def _typed_actor(area, name, expected_type=None):
    if not isinstance(name, str) or len(name) > 512:
        return False
    actor_type, _, path = name.partition(" ")
    return ((actor_type == expected_type if expected_type else _ACTOR_TYPE.fullmatch(actor_type) is not None)
            and _actor_path(area, path))


def parse(fields, state, area):
    if (len(fields) not in (9, 11, 20) or fields[:2] != [TAG, area] or state != "hosting"
            or not valid_campaign_package(area) or not all(_SERIAL.fullmatch(value) and int(value) < 2**53 for value in fields[2:4])
            or fields[8] not in ("0", "1")):
        raise ValueError("Invalid stock enemy drop observation")
    _, _, sequence, roll, source, killer, actor, class_path, live = fields[:9]
    motion, first_height = fields[9:11] or ("settled", "-")
    height = None if first_height == "-" else float(first_height)
    if motion not in ("gone", "moving", "settled") or (height is not None and not abs(height) <= 1e7):
        raise ValueError("Invalid stock enemy drop motion")
    item_class = item_class_name(class_path)
    if (not item_class or not _typed_actor(area, source)
            or (killer != "-" and not named_actor(area, killer, "Player_FP_C"))
            or not _typed_actor(area, actor, item_class)):
        raise ValueError("Invalid stock enemy, killer, or item identity")
    report = dict(sequence=int(sequence), roll=int(roll), map=area, source=source,
                  killer=None if killer == "-" else killer, actor=actor, class_path=class_path, live_at_capture=live == "1",
                  motion=motion, initial_height=height)
    if len(fields) == 20:
        axes = list(map(float, fields[11:]))
        pose = dict(position=axes[:3], rotation=axes[3:6], scale=axes[6:])
        if motion == "gone" or live != "1" or not valid_item_transform(pose):
            raise ValueError("Invalid moving stock enemy drop transform")
        report["transform"] = pose
    return report


def parse_escrow(fields, game):
    if len(fields) != 7 or fields[1] != game.get("map") or game.get("state") != "hosting":
        raise ValueError("Invalid stock enemy drop escrow frame")
    _, area, sequence, actor, class_path, guid, ammo = fields
    observation = game.get(KEY, {}).get(actor, {})
    if (not _SERIAL.fullmatch(sequence) or observation.get("sequence") != int(sequence)
            or observation.get("class_path") != class_path or not valid_guid(guid)
            or not re.fullmatch(r"-?[0-9]{1,10}", ammo) or not -2**31 <= int(ammo) < 2**31):
        raise ValueError("Invalid stock enemy drop escrow payload")
    return dict(map=area, sequence=int(sequence), actor=actor, class_path=class_path, guid=guid, ammo=int(ammo))


async def escrow_fixture(bridge, game, actor):
    drop = game.get(KEY, {}).get(actor) if isinstance(actor, str) else None
    if not drop or game.get("state") != "hosting":
        raise ValueError("Select an observed stock NPC item from the current world")
    reply = await bridge.command(FIXTURE, str(drop["sequence"]), f"SFCoop_{uuid.uuid4().hex}", timeout=4)
    proof = reply.get(PROOF_KEY, {})
    if (proof.get("map"), proof.get("sequence"), proof.get("actor"), proof.get("class_path")) != (
            drop["map"], drop["sequence"], drop["actor"], drop["class_path"]):
        raise RuntimeError("The stock NPC drop escrow did not verify")
    return reply, {PROOF_KEY: proof}


def parse_bank(fields, game):
    if (len(fields) not in (17, 18) or not re.fullmatch(r"SFCoop_[0-9a-f]{32}", fields[7])
            or len(fields) == 18 and fields[17] != "memory"):
        raise ValueError("Invalid banked stock enemy drop frame")
    report = parse_escrow(fields[:7], game)
    axes = list(map(float, fields[8:17]))
    transform = dict(position=axes[:3], rotation=axes[3:6], scale=axes[6:9])
    if not valid_item_transform(transform):
        raise ValueError("Invalid banked stock enemy drop transform")
    return dict(report, slot=fields[7], transform=transform, memory=len(fields) == 18)


def parse_claim(fields, game):
    from .native_loot import parse_stack_merge

    if len(fields) not in (9, 10) or fields[1] != game.get("map") or game.get("state") != "hosting":
        raise ValueError("Invalid stock enemy loot claim frame")
    _, area, pawn, slot, guid, status, *values = fields
    if (not re.fullmatch(r"SFCoop_[0-9a-f]{32}", slot) or not valid_guid(guid)
            or not all(re.fullmatch(r"[0-9]{1,10}", value) for value in values[:3])):
        raise ValueError("Invalid stock enemy loot claim payload")
    proof = dict(map=area, pawn=pawn, slot=slot, guid=guid, status=status)
    if status == "merged" and len(values) == 4:
        return dict(proof, merge=parse_stack_merge((guid, values[3], *values[:3])))
    if len(values) != 3 or status not in ("claimed", "already_claimed", "cash_credited"):
        raise ValueError("Invalid stock enemy loot claim status")
    amount, before, after = map(int, values)
    if status == "cash_credited":
        if not 0 < amount == after - before or not 0 <= before < after < 2**31:
            raise ValueError("Invalid stock enemy loot cash credit")
    elif (amount, before, after) != (0, 0, 0):
        raise ValueError("Unexpected cash in stock enemy item transfer")
    return dict(proof, amount=amount, before=before, after=after)


def active_frames(data, members):
    records = data.get(STATE, {})
    if not isinstance(records, dict):
        raise ValueError("Invalid stock enemy loot ledger")
    owners = set(members) | {None, "host"}
    frames = []
    for guid, row in records.items():
        if not valid_guid(guid) or not isinstance(row, dict) or row.get("guid") != guid:
            raise ValueError("Invalid stock enemy loot identity")
        area, class_path, source = (row.get(key) for key in ("map", "class_path", "source"))
        actor = row.get("actor")
        visual_id = row.get("visual_id")
        ammo, killer, claimant = (row.get(key) for key in ("ammo", "killer", "claimed_by"))
        item_class = item_class_name(class_path)
        if (not valid_campaign_package(area) or not item_class
                or not _actor_path(area, source) or not _typed_actor(area, actor, item_class)
                or (visual_id is not None and (not isinstance(visual_id, str) or not party.REQUEST_ID.fullmatch(visual_id)))
                or not valid_item_transform(row.get("transform")) or type(ammo) is not int or not -2**31 <= ammo < 2**31
                or any((value is not None and not isinstance(value, str)) or value not in owners
                       for value in (killer, claimant))):
            raise ValueError("Invalid stock enemy loot payload")
        if claimant is None:
            if not isinstance(frame := row.get("escrow"), dict) or frame.get("map") != area:
                raise ValueError("Unclaimed stock enemy loot has no save generation")
            frames.append(frame)
        elif "escrow" in row:
            raise ValueError("Claimed stock enemy loot still references escrow")
    return frames


def published(data, game, *, pinned=()):
    from .native_guest_drop_record import valid_position
    from .native_world_loot import stock_enemy_identity

    points = [row["location"] for row in game.get("pawns", {}).values() if valid_position(row.get("location"))]
    area = game.get("map")
    visible = {}
    for guid, row in data.get(STATE, {}).items():
        if (row["map"] == area and row["claimed_by"] is None
                and (stock_enemy_identity(guid, row.get("visual_id")) in pinned
                     or any(math.dist(point, row["transform"]["position"]) <= 2000 for point in points))):
            visible[guid] = {**{key: row[key] for key in ("map", "class_path", "transform", "ammo")},
                             "settled": row.get("settled", False)}
            if identity := row.get("visual_id"):
                visible[guid]["visual_id"] = identity
    return visible


def motion_identity(zone, actor):
    return uuid.uuid5(uuid.NAMESPACE_OID, f"stock-enemy-motion:{zone}:{actor}").hex


def uses_shell_view(game, drop):
    from . import native_enemy_loot

    return (drop["class_path"] == native_enemy_loot.SHELLS
            and native_enemy_loot.source_for(game, drop["source"]).get("ammo_actor") == drop["actor"])


def read_resident_motion(raw, game):
    area = game.get("map")
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if not raw or len(raw) > 64 * 1024 or not area or not pawn:
        return None
    try:
        scope, *rows = raw.decode("utf-8").splitlines()
        if scope.split("\t") != ["stock-loot-motion-v1", area, pawn] or len(rows) > MOTION_LIMIT:
            return None
        observations = [parse(row.split("\t"), game.get("state"), area) for row in rows]
    except (ValueError, UnicodeError):
        return None
    return {row["actor"]: row for row in observations}


def moving_frame(game, zone, processed, *, observed=None):
    from .native_guest_drop_record import valid_position

    if game.get("state") != "hosting" or not isinstance(zone, str) or not party.REQUEST_ID.fullmatch(zone):
        return None
    area = game.get("map")
    points = [row["location"] for row in game.get("pawns", {}).values() if valid_position(row.get("location"))]
    moving = {}
    observations = game.get(KEY, {}) if observed is None else observed
    for drop in observations.values():
        pose = drop.get("transform")
        if (drop["map"] == area and drop["sequence"] not in processed and pose and not uses_shell_view(game, drop)
                and any(math.dist(point, pose["position"]) <= 2000 for point in points)):
            moving[motion_identity(zone, drop["actor"])] = {"class_path": drop["class_path"], "transform": pose}
            if len(moving) == MOTION_LIMIT:
                break
    return {"map": area, "epoch": zone, "items": moving} if moving else None


async def relay_motion(host, bridge, transport):
    previous = {}
    sequence = 0
    scope = None
    observed = None
    observed_at = 0.0
    while not host.stopping:
        await asyncio.sleep(MOTION_INTERVAL)
        now = time.monotonic()
        game = host.last_game
        current_scope = (host.zone_epoch, game.get("map"), game.get("pawns", {}).get("local", {}).get("name"))
        if current_scope != scope:
            scope, observed = current_scope, None
            previous.clear()
        if host.transitioning or now - host.last_seen > 1:
            previous.clear()
            observed = None
            continue
        if bridge.local_bridge is not None:
            latest = read_resident_motion(bridge.local_bridge.take_stock_loot_motion(), game)
            if latest is not None:
                observed, observed_at = latest, now
        frame = moving_frame(game, host.zone_epoch, host.processed_stock_enemy_drops,
                             observed=observed if now - observed_at < .25 else None)
        if carried := host.world_loot.moving:
            frame = frame or {"map": host.last_game.get("map"), "epoch": host.zone_epoch, "items": {}}
            frame["items"] = dict(list({**carried, **frame["items"]}.items())[:MOTION_LIMIT])
        if not frame:
            previous.clear()
            continue
        seats = {character for character, seat in host.native_recipients().items() if seat["connected"]}
        if not seats:
            previous.clear()
            continue

        def allowed(guest_id):
            member = host.authenticated_guest(guest_id)
            return bool(member and member[1] in seats)

        items = frame["items"]
        previous = {identity: state for identity, state in previous.items() if identity in items}
        for identity, item in items.items():
            transform = item["transform"]
            pose = [*(round(value, 1) for field in ("position", "rotation") for value in transform[field]),
                    *transform["scale"]]
            prior, last = previous.get(identity, (None, 0.0))
            if pose == prior and now - last < .3:
                continue
            sequence += 1
            packet = [frame["map"], frame["epoch"], sequence, identity, item["class_path"], pose]
            if identity in carried:
                packet.append("carry")
            data = json.dumps(packet,
                              separators=(",", ":")).encode("ascii")
            transport.send_loot_motion(data, allowed)
            previous[identity] = pose, now


class MotionPlayback:
    def __init__(self):
        self.scope = None
        self.items = {}

    def frame(self, packets, area, epoch, *, active, now):
        scope = (area, epoch) if active else None
        if scope != self.scope:
            self.scope = scope
            self.items.clear()
        if scope is None:
            return None
        self.items = {identity: state for identity, state in self.items.items() if now - state[0] < MOTION_TTL}
        for received, packet in packets:
            if now - received >= MOTION_TTL:
                continue
            try:
                frame = json.loads(packet)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if (not isinstance(frame, list) or len(frame) not in (6, 7) or tuple(frame[:2]) != scope
                    or (len(frame) == 7 and frame[6] != "carry")):
                continue
            _, _, serial, identity, class_path, axes = frame[:6]
            if (type(serial) is not int or not 0 < serial < 2**53 or not isinstance(identity, str)
                    or not party.REQUEST_ID.fullmatch(identity) or not item_class_name(class_path)
                    or not isinstance(axes, list) or len(axes) != 9):
                continue
            last = self.items.get(identity)
            if (last and serial <= last[1]) or (not last and len(self.items) >= MOTION_LIMIT):
                continue
            pose = {"position": axes[:3], "rotation": axes[3:6], "scale": axes[6:]}
            if valid_item_transform(pose):
                row = {"class_path": class_path, "transform": pose}
                if len(frame) == 7:
                    row["carried"] = True
                self.items[identity] = received, serial, row
        return {"map": area, "epoch": epoch, "items": {identity: state[2] for identity, state in self.items.items()}}


def broker_cue_frame(game, epoch):
    if game.get("map") == BLACK_MARKET and game.get(BROKER_CUE) is True:
        return {"map": BLACK_MARKET, "epoch": epoch}
    return None


async def project_broker_cue(bridge, game, frame, epoch):
    if (game.get("state") != "connected" or game.get("map") != BLACK_MARKET or game.get(BROKER_CUE) is True
            or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch)
            or frame != {"map": BLACK_MARKET, "epoch": epoch}):
        return game
    result = await bridge.command("native-stock-broker-cue-remove", timeout=3)
    if (result.get("state"), result.get("map"), result.get(BROKER_CUE)) != ("connected", BLACK_MARKET, True):
        raise RuntimeError("Stock Broker dialogue collision removal did not verify")
    return result


def _public(row):
    return {key: value for key, value in row.items() if key != "escrow"}


def _available(host):
    game = host.last_game
    return (host.native_profile is not None and game.get("state") == "hosting"
            and game.get("native_world_loot_version", 0) >= 1 and not host.stopping
            and not host.transitioning and not host.native_checkpoint_unsafe and not host.party["wipe"])


async def observe(host, bridge, *, interaction_ready=None):
    from . import native_host_save, native_loot

    if not _available(host):
        return
    rows = host.last_game.get(KEY, {}).values()
    processed = host.processed_stock_enemy_drops
    processed.intersection_update(row["sequence"] for row in rows)
    observed = sorted((row for row in rows if row["sequence"] not in processed and row["motion"] != "moving"),
                      key=lambda row: row["sequence"])
    if not observed:
        return
    memory = host.last_game.get(MEMORY_CAPABILITY) is True
    candidate = None if memory else party.load_party(host.party)
    records = {} if memory else candidate.setdefault(STATE, {})
    committed = []
    deadline = time.monotonic() + _MEMORY_BANK_TURN_SECONDS
    try:
        for drop in observed[:8]:
            if memory and committed and (time.monotonic() >= deadline or interaction_ready and interaction_ready()):
                # Finish this batch before yielding to an interaction. The next pass
                # can still bank the remaining drops in stock spawn order.
                break
            seq, area, actor = drop["sequence"], drop["map"], drop["actor"]
            if (not drop["live_at_capture"] or drop["motion"] == "gone" or uses_shell_view(host.last_game, drop)
                    or (not memory and drop["source"].startswith(("DigHole_BP_C ", "Bolt_Projectile_BP_C ", "BurriedItem_BP_C ",
                                                                 "Package_Mail_C ", "Package_Mail_Small_C ")))):
                processed.add(seq)
                continue
            slot = native_host_save.new_slot()
            host.native_checkpoint_unsafe = True
            try:
                options = ("memory-escrow",) if memory else ()
                snapshot = await bridge.command(BANK, str(seq), slot, *options, timeout=7)
            except RuntimeError as error:
                reason = str(error)
                # Native capture only reads the world drop and writes to detached scratch escrow.
                # The Lua command releases that escrow on a refusal before hiding or destroying the actor.
                if reason not in _BANK_REFUSALS_BEFORE_RETIREMENT:
                    raise
                host.native_checkpoint_unsafe = bool(committed)
                if reason in {"stock_enemy_escrow_host_not_ready", "stock_enemy_escrow_scope_unavailable"}:
                    break
                if reason == "stock_enemy_escrow_drop_settling":
                    continue
                processed.add(seq)
                host.control.event("native_stock_enemy_loot_capture_refused", sequence=seq, actor=actor, reason=reason)
                continue
            proof = snapshot.get(BANK_KEY, {})
            guid = proof.get("guid")
            if ((proof.get("map"), proof.get("sequence"), proof.get("actor"), proof.get("class_path"), proof.get("slot"))
                    != (area, seq, actor, drop["class_path"], slot) or not valid_guid(guid) or guid in records
                    or memory and guid in host.party.get(STATE, {})):
                raise RuntimeError("The physical stock enemy item retirement did not verify")
            if bool(proof.get("memory")) != memory:
                raise RuntimeError("The stock enemy item did not verify its requested escrow mode")
            frame = ({"slot": slot, "map": area, "pending": True} if memory else
                     await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True))
            killer = next((character for character in host.roles().values()
                           if drop["killer"] and native_loot._scope(host, character) == drop["killer"]), None)
            record = {key: proof[key] for key in ("map", "guid", "class_path", "ammo", "transform")}
            records[guid] = dict(record, source=drop["source"].partition(" ")[2], actor=actor, killer=killer,
                                 claimed_by=None, settled=drop["initial_height"] is not None, escrow=frame)
            if drop.get("transform"):
                records[guid]["visual_id"] = motion_identity(host.zone_epoch, actor)
            host.last_game, host.last_seen = snapshot, time.monotonic()
            processed.add(seq)
            committed.append((drop, records[guid]))
        if committed:
            revision = None
            if memory:
                candidate = native_loot.copy_pending_loot(host.party, STATE)
                candidate.setdefault(STATE, {}).update(records)
                host.pending_host_save = bool(host.pending_host_save)
            else:
                revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
            for drop, record in committed:
                host.control.event("native_stock_enemy_loot_banked", revision=revision, sequence=drop["sequence"],
                                   **_public(record))
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True


async def claim(host, bridge, character, guid, operation):
    from . import native_host_save, native_loot
    from .native_world_loot import item_is_moving, stock_enemy_identity

    player = host.party["players"].get(character)
    pawn = native_loot._scope(host, character)
    if (not _available(host) or not player or not pawn or not valid_guid(guid) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("stock_enemy_loot_claim_scope_unavailable")
    fingerprint = [CLAIM, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    roll = host.party.get(STATE, {}).get(guid)
    if not roll or roll["map"] != host.last_game.get("map"):
        raise party.RuleError("stock_enemy_loot_item_unavailable")
    if roll["claimed_by"] is not None:
        raise party.RuleError("stock_enemy_loot_already_claimed")
    if item_is_moving(host, stock_enemy_identity(guid, roll.get("visual_id"))):
        raise party.RuleError("native_world_loot_item_in_motion")
    if player["downed"] or character in host.party["pending_spawns"]:
        raise party.RuleError("stock_enemy_loot_claimant_unavailable")

    frame = roll["escrow"]
    deferred = host.last_game.get(MEMORY_CAPABILITY) is True
    if deferred and character != "host":
        from .native_inventory import retain_guest_save

        if not await retain_guest_save(host, bridge, character, pawn):
            raise party.RuleError("native_guest_save_retention_unavailable")
    if not frame.get("pending"):
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(CLAIM, pawn, frame["slot"], guid,
                                            *(str(axis) for axis in roll["transform"]["position"]),
                                            stock_enemy_identity(guid, roll.get("visual_id")),
                                            *(() if roll.get("settled") else ("legacy-ground",)),
                                            timeout=7)
        except RuntimeError as error:
            reason = str(error)
            # Refusals before mutation leave the actor and the authoritative item available.
            if reason in {"stock_enemy_loot_out_of_reach_or_obstructed", "stock_enemy_loot_claimant_unavailable",
                          "stock_enemy_loot_claim_scope_unavailable", "stock_enemy_loot_escrow_unavailable",
                          "native_loot_exact_claimant_unavailable", "native_loot_escrow_item_unavailable",
                          "native_loot_claimant_backpack_full", "native_loot_cash_wallet_out_of_range"}:
                host.native_checkpoint_unsafe = False
                host.control.event("native_stock_enemy_loot_claim_refused", operation_id=operation, guid=guid, reason=reason)
                raise party.RuleError(reason) from error
            raise
        proof = snapshot.get(CLAIM_KEY, {})
        if tuple(proof.get(key) for key in ("map", "pawn", "slot", "guid")) != (roll["map"], pawn, frame["slot"], guid):
            raise RuntimeError("The stock enemy loot transfer did not match the exact claimant")
        candidate = party.load_party(host.party)
        committed = candidate[STATE][guid]
        committed["claimed_by"] = character
        committed.pop("escrow")
        merge = {}
        if roll["class_path"] in native_loot.CASH_RANGES:
            credit = native_loot._verified_cash_credit(
                {"status": proof.get("status"), "cash": proof}, roll, snapshot.get("native_wallet"))
            candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
            committed["wallet_credit"] = credit
        elif proof.get("status") == "merged":
            merge = native_loot.verified_stack_merge(proof.get("merge", {}), guid, roll["ammo"])
        elif proof.get("status") not in ("claimed", "already_claimed"):
            raise RuntimeError("The stock enemy item transfer did not verify")
        result = {CLAIM_KEY: {**_public(committed), **merge}}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = None
        if deferred:
            if character == "host":
                host.pending_host_save = bool(host.pending_host_save or merge)
        else:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                    typed_inventory=bool(merge))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_stock_enemy_loot_claimed", revision=revision, operation_id=operation, **result[CLAIM_KEY])
        native_pickup_audio.committed(host, pawn, operation, committed["class_path"], credit=committed.get("wallet_credit"))
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
