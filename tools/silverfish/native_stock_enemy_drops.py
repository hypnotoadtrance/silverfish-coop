"""Persist and transfer physical items from verified stock NPC loot rolls."""

import asyncio
import math
import re
import time
import uuid

from . import native_pickup_audio, party
from .authored_loot import valid_item_transform
from .native_gold import valid_guid
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
CLAIM = "native-stock-enemy-loot-claim"
CLAIMED = "native-stock-enemy-loot-claimed"
CLAIM_KEY = "native_stock_enemy_loot_claim"
_MAP = re.compile(r"/Game/Classes/Maps(?:/[A-Za-z0-9_]+)+")
_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_/]+\.([A-Za-z0-9_]+_C)")
_SERIAL = re.compile(r"[1-9][0-9]{0,15}")


def parse(fields, state, area):
    if (len(fields) not in (9, 11) or fields[:2] != [TAG, area] or state != "hosting" or not isinstance(area, str)
            or not _MAP.fullmatch(area) or not all(_SERIAL.fullmatch(value) and int(value) < 2**53 for value in fields[2:4])
            or fields[8] not in ("0", "1")):
        raise ValueError("Invalid stock enemy drop observation")
    _, _, sequence, roll, source, killer, actor, class_path, live = fields[:9]
    motion, first_height = fields[9:] or ("settled", "-")
    height = None if first_height == "-" else float(first_height)
    if motion not in ("gone", "moving", "settled") or (height is not None and not abs(height) <= 1e7):
        raise ValueError("Invalid stock enemy drop motion")
    prefix = re.escape(f" {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.")
    item_class = _CLASS.fullmatch(class_path) if len(class_path) <= 256 else None
    if (not item_class or any(len(name) > 512 for name in (source, killer, actor))
            or not re.fullmatch(r"[A-Za-z0-9_]+_C" + prefix + r"[A-Za-z0-9_]+", source)
            or (killer != "-" and not re.fullmatch(r"Player_FP_C" + prefix + r"Player_FP_C_[0-9]+", killer))
            or not re.fullmatch(re.escape(item_class[1]) + prefix + r"[A-Za-z0-9_]+", actor)):
        raise ValueError("Invalid stock enemy, killer, or item identity")
    return dict(sequence=int(sequence), roll=int(roll), map=area, source=source,
                killer=None if killer == "-" else killer, actor=actor, class_path=class_path, live_at_capture=live == "1",
                motion=motion, initial_height=height)


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
    if len(fields) != 17 or not re.fullmatch(r"SFCoop_[0-9a-f]{32}", fields[7]):
        raise ValueError("Invalid banked stock enemy drop frame")
    report = parse_escrow(fields[:7], game)
    axes = list(map(float, fields[8:]))
    transform = dict(position=axes[:3], rotation=axes[3:6], scale=axes[6:9])
    if not valid_item_transform(transform):
        raise ValueError("Invalid banked stock enemy drop transform")
    return dict(report, slot=fields[7], transform=transform)


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
        ammo, killer, claimant = (row.get(key) for key in ("ammo", "killer", "claimed_by"))
        if (not isinstance(area, str) or not _MAP.fullmatch(area)
                or not isinstance(class_path, str) or len(class_path) > 256 or not _CLASS.fullmatch(class_path)
                or not isinstance(source, str) or len(source) > 512
                or not isinstance(actor, str) or len(actor) > 512
                or not valid_item_transform(row.get("transform")) or type(ammo) is not int or not -2**31 <= ammo < 2**31
                or any((value is not None and not isinstance(value, str)) or value not in owners
                       for value in (killer, claimant))):
            raise ValueError("Invalid stock enemy loot payload")
        level = re.escape(f"{area}.{area.rsplit('/', 1)[1]}:PersistentLevel.")
        if (not re.fullmatch(level + r"[A-Za-z0-9_]+", source)
                or not re.fullmatch(re.escape(class_path.rsplit(".", 1)[1]) + " " + level + r"[A-Za-z0-9_]+", actor)):
            raise ValueError("Invalid stock enemy loot source")
        if claimant is None:
            if not isinstance(frame := row.get("escrow"), dict) or frame.get("map") != area:
                raise ValueError("Unclaimed stock enemy loot has no save generation")
            frames.append(frame)
        elif "escrow" in row:
            raise ValueError("Claimed stock enemy loot still references escrow")
    return frames


def published(data, game):
    from .native_guest_drop_record import valid_position

    points = [row["location"] for row in game.get("pawns", {}).values() if valid_position(row.get("location"))]
    area = game.get("map")
    visible = {}
    for guid, row in data.get(STATE, {}).items():
        if (row["map"] == area and row["claimed_by"] is None
                and any(math.dist(point, row["transform"]["position"]) <= 2000 for point in points)):
            visible[guid] = {**{key: row[key] for key in ("map", "class_path", "transform", "ammo")},
                             "settled": row.get("settled", False)}
    return visible


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


async def observe(host, bridge):
    from . import native_enemy_loot, native_host_save, native_loot

    if not _available(host):
        return
    rows = host.last_game.get(KEY, {}).values()
    processed = host.processed_stock_enemy_drops
    processed.intersection_update(row["sequence"] for row in rows)
    observed = sorted((row for row in rows if row["sequence"] not in processed and row["motion"] != "moving"),
                      key=lambda row: row["sequence"])
    if not observed:
        return
    candidate = party.load_party(host.party)
    records = candidate.setdefault(STATE, {})
    committed = []
    try:
        for drop in observed[:8]:
            seq, area, actor = drop["sequence"], drop["map"], drop["actor"]
            if (not drop["live_at_capture"] or drop["motion"] == "gone" or
                    drop["class_path"] == native_enemy_loot.SHELLS
                    and native_enemy_loot.source_for(host.last_game, drop["source"]).get("ammo_actor") == actor):
                processed.add(seq)
                continue
            slot = native_host_save.new_slot()
            host.native_checkpoint_unsafe = True
            try:
                snapshot = await bridge.command(BANK, str(seq), slot, timeout=7)
            except RuntimeError as error:
                reason = str(error)
                if reason not in {"stock_enemy_escrow_actor_unavailable", "stock_enemy_escrow_actor_unverified",
                                  "stock_enemy_escrow_host_not_ready", "stock_enemy_escrow_drop_settling"}:
                    raise
                host.native_checkpoint_unsafe = bool(committed)
                if reason == "stock_enemy_escrow_host_not_ready":
                    break
                if reason == "stock_enemy_escrow_drop_settling":
                    continue
                processed.add(seq)
                host.control.event("native_stock_enemy_loot_capture_refused", sequence=seq, actor=actor, reason=reason)
                continue
            proof = snapshot.get(BANK_KEY, {})
            guid = proof.get("guid")
            if ((proof.get("map"), proof.get("sequence"), proof.get("actor"), proof.get("class_path"), proof.get("slot"))
                    != (area, seq, actor, drop["class_path"], slot) or not valid_guid(guid) or guid in records):
                raise RuntimeError("The physical stock enemy item retirement did not verify")
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
            killer = next((character for character in host.roles().values()
                           if drop["killer"] and native_loot._scope(host, character) == drop["killer"]), None)
            record = {key: proof[key] for key in ("map", "guid", "class_path", "ammo", "transform")}
            records[guid] = dict(record, source=drop["source"].partition(" ")[2], actor=actor, killer=killer,
                                 claimed_by=None, settled=drop["initial_height"] is not None, escrow=frame)
            host.last_game, host.last_seen = snapshot, time.monotonic()
            processed.add(seq)
            committed.append((drop, records[guid]))
        if committed:
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
    from .native_world_loot import stock_enemy_identity

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
    if player["downed"] or character in host.party["pending_spawns"]:
        raise party.RuleError("stock_enemy_loot_claimant_unavailable")

    frame = roll["escrow"]
    await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(CLAIM, pawn, frame["slot"], guid,
                                            *(str(axis) for axis in roll["transform"]["position"]),
                                            stock_enemy_identity(guid), *(() if roll.get("settled") else ("legacy-ground",)),
                                            timeout=7)
        except RuntimeError as error:
            if str(error) in {"stock_enemy_loot_out_of_reach_or_obstructed", "stock_enemy_loot_claimant_unavailable",
                              "native_loot_claimant_backpack_full", "native_loot_cash_wallet_out_of_range"}:
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
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
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                typed_inventory=bool(merge))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_stock_enemy_loot_claimed", revision=revision, operation_id=operation, **result[CLAIM_KEY])
        native_pickup_audio.committed(host, pawn, operation, committed["class_path"])
        return result
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
