"""Settle a scoped native guest stack drop or reclaim in one paired checkpoint."""

import asyncio
import math
import re
import time

from . import native_guest_drop_record as record
from . import native_guest_stack_drop as probe, native_host_save, native_inventory, native_inventory_death, party
from .native_gold import valid_guid


DROP = "native-guest-stack-drop"
RECLAIM = "native-guest-stack-reclaim"
ACTIONS = (DROP, RECLAIM)
RESULT = "native_guest_stack_drop"
_SELECTION = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,2})\Z")


def visible_drops(state: dict, owner: str, area: str | None) -> dict:
    return {drop_id: record.visible(row, drop_id) for drop_id, row in state.get(record.STATE, {}).items()
            if row["owner"] == owner and row["map"] == area and row["claim_id"] is None}


def _result(row: dict, drop_id: str, action: str) -> dict:
    return {RESULT: {**record.visible(row, drop_id), "state": "reclaimed" if action == RECLAIM else "dropped"}}


def _owner(host, character: str) -> str:
    game = host.last_game
    if (not host.fixtures or not host.native_guest or not host.native_zones or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or (game.get("state"), game.get("map"), game.get(probe.CAPABILITY)) != ("hosting", record.OBSERVATORY, 1)
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_stack_drop_fixture_unavailable")
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn")
    player = host.party["players"].get(character)
    if (not owner.get("connected") or not pawn or not player or player["downed"] or host.party["wipe"]
            or character in host.party["pending_spawns"]):
        raise party.RuleError("native_guest_stack_drop_exact_standing_guest_required")
    if host.smg_holds is not None and host.smg_holds.strokes:
        raise party.RuleError("native_guest_stack_drop_combat_busy")
    return pawn


def _near(host, character: str, row: dict) -> bool:
    origin = host.authoritative_pawns(host.roles()).get(character, {})
    return (origin.get("map") == row["map"] and record.valid_position(origin.get("location"))
            and math.dist(origin["location"], row["location"]) <= party.INTERACT_RANGE)


def _qualified_item(row: dict, guid: str, count: int) -> bool:
    return (row.get("guid"), row.get("quantity"), row.get("class_path"), row.get("source"),
            row.get("stackable"), row.get("important")) == (guid, count, record.ITEM, "backpack", True, False)


async def _escrow_item(host, bridge, frame: dict, guid: str, count: int) -> None:
    items = (await native_inventory.read_complete(host, bridge, frame))["items"]
    if len(items) != 1 or not _qualified_item(items[0], guid, count):
        raise RuntimeError("The isolated stack drop escrow does not contain the exact single typed row")


async def _settle(host, bridge, character: str, action: str, request: str, drop_id: str, operation: str,
                  source: dict, prepared: dict | None, candidate: dict) -> dict:
    pawn = _owner(host, character)
    row = candidate[record.STATE][drop_id]
    if not _near(host, character, row):
        raise party.RuleError("native_guest_stack_drop_out_of_range")
    if action == DROP:
        await asyncio.to_thread(native_host_save.guest_record, host.native_profile, source, character)
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, prepared)
    else:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, source)
    verb, expected = ("debit", "applied") if action == DROP else ("reclaim", "recovered")
    replacement = prepared["slot"] if prepared else None
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(probe.SETTLE, verb, pawn, drop_id, source["slot"], replacement or "-",
                                            *(str(axis) for axis in row["location"]), timeout=7)
        except RuntimeError as error:
            if str(error).startswith(probe.REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(probe.SETTLE_KEY, {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", record.OBSERVATORY)
                or tuple(proof.get(key) for key in ("map", "operation", "pawn", "drop_id", "source", "replacement")) != (
                    record.OBSERVATORY, verb, pawn, drop_id, source["slot"], replacement)
                or type(proof.get("ok")) is not bool):
            raise RuntimeError("The native stack drop settlement did not verify its exact owner and frames")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        status = proof.get("status", "")
        if not proof["ok"] and proof.get("count") == 0 and status.startswith("native_inventory_wipe_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(status)
        if not proof["ok"] or status != expected or proof.get("count") != 1:
            raise RuntimeError(status or "The native typed stack drop mutation was unverified")
        result = _result(row, drop_id, action)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": [action, request, None], "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_stack_drop_committed", phase=verb, revision=revision, **result[RESULT])
    return result


async def perform(host, bridge, character: str, action: str, target: str | None, operation: str) -> dict:
    if action not in ACTIONS or not record.valid_id(operation):
        raise party.RuleError("invalid_operation_id")
    _owner(host, character)
    drops = host.party.get(record.STATE, {})
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != [action, target, None]:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    # Durable operation identities remain replayable after the ordinary recent receipt cache rolls over.
    for drop_id, row in drops.items():
        if row["owner"] != character:
            continue
        if drop_id == operation or row["claim_id"] == operation:
            old_action = DROP if drop_id == operation else RECLAIM
            old_target = f"{row['guid']}|{row['quantity']}" if old_action == DROP else drop_id
            if (action, target) != (old_action, old_target):
                raise party.RuleError("operation_id_conflict")
            return _result(row, drop_id, old_action)

    if action == RECLAIM:
        original = drops.get(target) if record.valid_id(target) else None
        if not original or original["owner"] != character:
            raise party.RuleError("native_guest_stack_drop_not_owned")
        if original["claim_id"] is not None:
            raise party.RuleError("native_guest_stack_drop_already_reclaimed")
        if not _near(host, character, original):
            raise party.RuleError("native_guest_stack_drop_out_of_range")
        escrow = original["escrow"]
        await host.checkpoint_native(bridge, require_guest_frames=frozenset({character}))
        await _escrow_item(host, bridge, escrow, original["guid"], original["quantity"])
        candidate = party.load_party(host.party)
        row = candidate[record.STATE][target]
        row["claim_id"] = operation
        row.pop("escrow")
        return await _settle(host, bridge, character, action, target, target, operation, escrow, None, candidate)

    selected = _SELECTION.fullmatch(target) if isinstance(target, str) else None
    if not selected or not valid_guid(selected[1]) or int(selected[2]) > record.MAX_QUANTITY:
        raise party.RuleError("native_guest_stack_drop_exact_guid_and_quantity_required")
    guid, count = selected[1], int(selected[2])
    if operation in drops:
        raise party.RuleError("operation_id_conflict")
    active = [row for row in drops.values() if row["claim_id"] is None]
    if (len(drops) >= record.MAX_DROPS or len(active) >= record.MAX_ACTIVE
            or any(row["owner"] == character or row["guid"] == guid for row in active)):
        raise party.RuleError("native_guest_stack_drop_capacity")
    birth = (await probe.perform(host, bridge, character, guid, count))[probe.KEY]
    location = birth.get("location")
    campaign = host.store.status()
    source = native_inventory.saved_frame(host, campaign, character)
    if (not record.valid_position(location) or not isinstance(source, dict) or source.get("slot") != birth["slot"]
            or campaign.get("revision") != birth["revision"]):
        raise RuntimeError("The stock stack drop did not verify its placement and exact frozen owner frame")
    items = (await native_inventory.read_complete(host, bridge, source))["items"]
    dropped = next((item for item in items if item["guid"] == guid), None)
    if not dropped or not _qualified_item(dropped, guid, count):
        raise party.RuleError("native_guest_stack_drop_stock_9mm_row_unqualified")
    prepared = await native_inventory_death.prepare(host, bridge, {
        "source": source, "kept": [item["guid"] for item in items if item["guid"] != guid], "dropped": [guid], "discarded": []})
    escrow = prepared["bag"]
    await _escrow_item(host, bridge, escrow, guid, count)
    candidate = party.load_party(host.party)
    candidate.setdefault(record.STATE, {})[operation] = dict(owner=character, guid=guid, quantity=count,
        class_path=record.ITEM, map=record.OBSERVATORY, location=location, claim_id=None, escrow=escrow)
    return await _settle(host, bridge, character, action, target, operation, operation, source, prepared["replacement"], candidate)
