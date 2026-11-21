"""Settle a native inventory drop or pickup in one paired checkpoint."""

import asyncio
from contextlib import suppress
import math
import re
import time
import uuid

from . import native_guest_drop_record as record
from . import native_guest_stack_drop as probe, native_host_save, native_inventory, native_inventory_death, party
from .native_gold import valid_guid


DROP = "native-guest-stack-drop"
RECLAIM = record.PICKUP_ACTION
HOST_DROP = "native-host-stack-drop"
HOST_RECLAIM = "native-host-stack-reclaim"
ACTIONS = (DROP, RECLAIM)
RESULT = "native_guest_stack_drop"
_SELECTION = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,2})\Z")


def visible_drops(state: dict, area: str | None) -> dict:
    return {drop_id: record.visible(row, drop_id) for drop_id, row in state.get(record.STATE, {}).items()
            if row["map"] == area and row["claim_id"] is None}


def _result(row: dict, drop_id: str, action: str) -> dict:
    result = record.visible(row, drop_id)
    if action == RECLAIM:
        result.update(state="reclaimed", claimed_by=record.claimed_by(row))
    else:
        result["state"] = "dropped"
    return {RESULT: result}


def _participant(host, character: str) -> str:
    game = host.last_game
    area = game.get("map")
    if (not host.native_guest or not host.native_zones or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or not record.supported_map(area)
            or (game.get("state"), game.get(record.CAPABILITY)) != ("hosting", 1)
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_stack_drop_fixture_unavailable")
    if host.smg_holds is not None and host.smg_holds.strokes:
        raise party.RuleError("native_guest_stack_drop_combat_busy")
    return probe.standing_pawn(host, character)


def _near(host, character: str, row: dict) -> bool:
    origin = host.authoritative_pawns(host.roles()).get(character, {})
    return (origin.get("map") == row["map"] and record.valid_position(origin.get("location"))
            and math.dist(origin["location"], row["location"]) <= party.INTERACT_RANGE)


def _qualified_item(row: dict, guid: str, count: int, item: str) -> bool:
    return (row.get("guid"), row.get("quantity"), row.get("class_path"), row.get("source"),
            row.get("stackable"), row.get("important")) == (guid, count, item, "backpack", record.STACKABLE[item], False)


def droppable(row: dict) -> bool:
    guid, count, item = row.get("guid"), row.get("quantity"), row.get("class_path")
    return valid_guid(guid) and record.valid_quantity(item, count) and _qualified_item(row, guid, count, item)


def view_identity(client, sequence: int, area: str, pawn: str) -> tuple:
    return client.host_session, client.zone_epoch, client.stack_drop_projection.admission, sequence, area, pawn


async def service_view(client, active: bool) -> None:
    view = client.last_game.get(record.VIEW, {})
    sequence = view.get("sequence")
    if view.get("state") != "dropping" or type(sequence) is not int or not client.last_inventory_drop < sequence < 2**53:
        return
    client.last_inventory_drop = sequence
    area, pawn, guid, item, count = (view.get(key) for key in ("map", "pawn", "guid", "class_path", "count"))
    identity = view_identity(client, sequence, area, pawn)
    operation = uuid.uuid5(client.bridge.input_namespace, ":".join(map(str, (*identity, DROP, guid, count)))).hex
    result = "refused"
    try:
        if (not active or client.stopping or client.suspended or not client.transport.game_access
                or client.needs_travel or client.server_transitioning or not client.host_session or not client.zone_epoch
                or time.monotonic() - client.gameplay_seen >= 3 or client.gameplay.get(record.CAPABILITY) != 1
                or not record.supported_map(area) or area != client.last_game.get("map")
                or not pawn or pawn != client.last_game.get("pawns", {}).get("local", {}).get("name")
                or client.inventory_drop_scope != identity or not valid_guid(guid) or not record.valid_quantity(item, count)):
            raise ValueError("The stock inventory drop confirmation expired")
        owner = client.invite["character_id"]
        offset = native_inventory.displayed_offset(client.last_game, sequence, area, pawn)
        if offset is None:
            raise ValueError("The stock inventory drop confirmation expired")
        page = await native_inventory.read_view_page(client, operation, offset)
        if not any(_qualified_item(row, guid, count, item) for row in page["items"]):
            raise ValueError("The selected item is no longer on your authenticated backpack page")
        current = client.bridge.snapshot() or {}
        if (view_identity(client, sequence, area, pawn) != identity or not client.transport.game_access
                or client.suspended or client.needs_travel or client.server_transitioning
                or client.inventory_drop_scope != identity or current.get(record.VIEW) != view
                or native_inventory.displayed_offset(current, sequence, area, pawn) != offset):
            raise ValueError("The stock inventory drop confirmation expired")
        receipt = (await client.gameplay_action(DROP, operation, f"{guid}|{count}"))[RESULT]
        if (tuple(receipt.get(key) for key in ("state", "drop_id", "owner", "guid", "quantity", "class_path", "map")) != (
                "dropped", operation, owner, guid, count, item, area)
                or not record.valid_position(receipt.get("location"))):
            raise RuntimeError("The host did not verify the exact dropped inventory item")
        result = "dropped"
        client.control.event("native_guest_inventory_stack_dropped", operation_id=operation, view_sequence=sequence, **receipt)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        client.control.event("native_guest_inventory_stack_drop_rejected", operation_id=operation, view_sequence=sequence,
                             reason=str(error))
    with suppress(OSError, RuntimeError, ValueError):
        client.last_game = await client.bridge.command(record.VIEW_RESULT, str(sequence), area, guid, result, timeout=2)


async def _escrow_item(host, bridge, frame: dict, guid: str, count: int, item: str) -> None:
    items = (await native_inventory.read_complete(host, bridge, frame))["items"]
    if len(items) != 1 or not _qualified_item(items[0], guid, count, item):
        raise RuntimeError("The isolated stack drop escrow does not contain the exact single typed row")


async def _settle(host, bridge, character: str, action: str, request: str, drop_id: str, operation: str,
                  source: dict, prepared: dict | None, candidate: dict, scope: tuple[str, str, str]) -> dict:
    area, epoch, pawn = scope
    row = candidate[record.STATE][drop_id]
    if row["map"] != area or source.get("map") != area or (prepared is not None and prepared.get("map") != area):
        raise RuntimeError("The stack drop transaction frames do not belong to the exact source map")
    if action == DROP:
        await probe.verify_owner_frame(host.native_profile, source, character)
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, prepared)
    else:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, source)
    if host.last_game.get("map") != area or host.zone_epoch != epoch or _participant(host, character) != pawn:
        raise party.RuleError("native_guest_stack_drop_scope_changed")
    if not _near(host, character, row):
        raise party.RuleError("native_guest_stack_drop_out_of_range")
    verb, expected = ("debit", "applied") if action == DROP else ("reclaim", "recovered")
    replacement = prepared["slot"] if prepared else None
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(probe.SETTLE, verb, pawn, drop_id, source["slot"], replacement or "-",
                                            *(str(axis) for axis in row["location"]), area, timeout=7)
        except RuntimeError as error:
            if str(error).startswith(probe.REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(probe.SETTLE_KEY, {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", area)
                or tuple(proof.get(key) for key in ("map", "operation", "pawn", "drop_id", "source", "replacement")) != (
                    area, verb, pawn, drop_id, source["slot"], replacement)
                or type(proof.get("ok")) is not bool):
            raise RuntimeError("The native stack drop settlement did not verify its exact recipient and frames")
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
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_stack_drop_committed", phase=verb, revision=revision, **result[RESULT])
    return result


async def perform(host, bridge, character: str, action: str, target: str | None, operation: str) -> dict:
    from .native_world_loot import CAPABILITY, PLAYER_DROPS_VERSION

    if action not in ACTIONS or not record.valid_id(operation):
        raise party.RuleError("invalid_operation_id")
    pawn = _participant(host, character)
    area, epoch = host.last_game["map"], host.zone_epoch
    scope = area, epoch, pawn
    drops = host.party.get(record.STATE, {})
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != [action, target, None]:
                raise party.RuleError("operation_id_conflict")
            if action == RECLAIM:
                row = drops.get(target) if record.valid_id(target) else None
                if not row or row["claim_id"] != operation or record.claimed_by(row) != character:
                    raise party.RuleError("operation_id_conflict")
                return _result(row, target, RECLAIM)
            return receipt["result"]
    # Durable operation identities remain replayable after the ordinary recent receipt cache rolls over.
    for drop_id, row in drops.items():
        debit = row["owner"] == character and drop_id == operation
        reclaim = row["claim_id"] == operation and record.claimed_by(row) == character
        if debit or reclaim:
            old_action = DROP if debit else RECLAIM
            old_target = f"{row['guid']}|{row['quantity']}" if old_action == DROP else drop_id
            if (action, target) != (old_action, old_target):
                raise party.RuleError("operation_id_conflict")
            return _result(row, drop_id, old_action)

    if action == RECLAIM:
        original = drops.get(target) if record.valid_id(target) else None
        if not original:
            raise party.RuleError("native_guest_stack_drop_unavailable")
        if original["claim_id"] is not None:
            raise party.RuleError("native_guest_stack_drop_already_reclaimed")
        if not _near(host, character, original):
            raise party.RuleError("native_guest_stack_drop_out_of_range")
        escrow = original["escrow"]
        await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
        await _escrow_item(host, bridge, escrow, original["guid"], original["quantity"], original["class_path"])
        candidate = party.load_party(host.party)
        row = candidate[record.STATE][target]
        row.update(claim_id=operation, claimed_by=character)
        row.pop("escrow")
        return await _settle(host, bridge, character, action, target, target, operation, escrow, None, candidate, scope)

    selected = _SELECTION.fullmatch(target) if isinstance(target, str) else None
    if not selected or not valid_guid(selected[1]) or int(selected[2]) > record.MAX_QUANTITY:
        raise party.RuleError("native_guest_stack_drop_exact_guid_and_quantity_required")
    guid, count = selected[1], int(selected[2])
    if operation in drops:
        raise party.RuleError("operation_id_conflict")
    active = [row for row in drops.values() if row["claim_id"] is None]
    multiple = host.last_game.get(CAPABILITY) == PLAYER_DROPS_VERSION
    if (len(drops) >= record.MAX_DROPS or len(active) >= record.MAX_ACTIVE
            or any(row["guid"] == guid or (not multiple and row["owner"] == character) for row in active)):
        raise party.RuleError("native_guest_stack_drop_capacity")
    birth = (await probe.perform(host, bridge, character, guid, count, fixture=False))[probe.KEY]
    location, item = birth.get("location"), birth.get("class_path")
    campaign = host.store.status()
    source = native_inventory.saved_frame(host, campaign, character)
    if (not record.valid_position(location) or not record.valid_quantity(item, count)
            or (birth.get("map"), birth.get("pawn")) != (area, pawn)
            or not isinstance(source, dict) or (source.get("map"), source.get("slot")) != (area, birth["slot"])
            or campaign.get("revision") != birth["revision"]):
        raise RuntimeError("The stock stack drop did not verify its placement and exact frozen owner frame")
    items = (await native_inventory.read_complete(host, bridge, source))["items"]
    dropped = next((item for item in items if item["guid"] == guid), None)
    if not dropped or not _qualified_item(dropped, guid, count, item):
        raise party.RuleError("native_guest_stack_drop_stock_item_row_unqualified")
    prepared = await native_inventory_death.prepare(host, bridge, {
        "source": source, "kept": [item["guid"] for item in items if item["guid"] != guid], "dropped": [guid], "discarded": []})
    escrow = prepared["bag"]
    await _escrow_item(host, bridge, escrow, guid, count, item)
    candidate = party.load_party(host.party)
    candidate.setdefault(record.STATE, {})[operation] = dict(owner=character, guid=guid, quantity=count,
        class_path=item, map=area, location=location, claim_id=None, escrow=escrow)
    return await _settle(host, bridge, character, action, target, operation, operation, source,
                         prepared["replacement"], candidate, scope)
