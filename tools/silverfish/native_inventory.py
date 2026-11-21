"""Read pages from the native save frame of one authenticated character."""

import asyncio
from contextlib import suppress
import re
import time
import uuid

from . import native_host_save, party
from .guest_weapon_profiles import FORTY_FIVE_BALL, NINE_MM_BALL, TWELVE_GAUGE_BUCK
from .native_gold import valid_guid


PAGE_SIZE = 16
MAX_ITEMS = 8194
SOURCES = {"backpack", "equipment", "armor", "helmet"}
SPLIT = "native-inventory-split"
SPLIT_KEY = "native_inventory_split"
SPLIT_CAPABILITY = "native_inventory_split_capability"
SPLIT_CLASSES = frozenset(ammo.item_path for ammo in (NINE_MM_BALL, FORTY_FIVE_BALL, TWELVE_GAUGE_BUCK))
_SPLIT_TARGET = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,9})\|([1-9][0-9]{0,9})")
SPLIT_VIEW = "native_guest_inventory_split_view"
SORT = "native-inventory-sort"
SORT_CAPABILITY = "native_inventory_sort_capability"
SORT_KEY = "native_inventory_sort"
STORE_EQUIPMENT = "native-inventory-store-equipment"
STORE_CAPABILITY = "native_inventory_store_equipment_capability"
STORE_KEY = "native_inventory_store_equipment"
_STOCK_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_./-]+_C\Z")


def equipment_receipt(fields, area):
    _, world, pawn, item, slot, item_class, displaced = fields
    if (world != area or not valid_guid(item) or slot not in "01234567" or len(slot) != 1
            or len(item_class) > 256 or not _STOCK_CLASS.fullmatch(item_class)
            or (displaced != "-" and (not valid_guid(displaced) or displaced == item))):
        raise ValueError("Invalid stock equipment slot report")
    return dict(map=world, pawn=pawn, guid=item, slot=int(slot), class_path=item_class, displaced=displaced)


def valid_page_offset(offset: int) -> bool:
    return type(offset) is int and 0 <= offset <= MAX_ITEMS and offset % PAGE_SIZE == 0


def displayed_offset(game: dict, sequence: int, area: str, pawn: str) -> int | None:
    view = game.get("native_inventory_view", {})
    offset = view.get("offset")
    if ((game.get("state"), game.get("map"), game.get("pawns", {}).get("local", {}).get("name"))
            == ("connected", area, pawn)
            and (view.get("state"), view.get("sequence"), view.get("map"), view.get("pawn"))
            == ("rendered", sequence, area, pawn) and valid_page_offset(offset)):
        return offset
    return None


async def read_view_page(client, operation: str, offset: int) -> dict:
    page = (await client.gameplay_action("native-inventory-list", operation, str(offset)))["native_inventory"]
    total, items = page["total"], page["items"]
    if (page["owner"] != client.invite["character_id"] or type(page["offset"]) is not int or page["offset"] != offset
            or type(total) is not int or not offset <= total <= MAX_ITEMS
            or offset > 0 and offset == total
            or type(items) is not list or len(items) != min(PAGE_SIZE, total - offset)):
        raise ValueError("The host did not return this guest's exact stock inventory page")
    return page


def split_target(value):
    match = _SPLIT_TARGET.fullmatch(value) if isinstance(value, str) else None
    if match and valid_guid(match[1]):
        amount, expected = int(match[2]), int(match[3])
        if 1 <= amount < expected <= 2**31 - 1:
            return match[1], amount, expected
    return None


def splittable(row):
    quantity = row.get("quantity")
    return (row.get("source") == "backpack" and row.get("class_path") in SPLIT_CLASSES
            and row.get("stackable") is True and row.get("important") is False
            and type(quantity) is int and 1 < quantity < 2**31)


async def split_stack(host, bridge, character: str, target, operation: str) -> dict:
    selection = split_target(target)
    if not selection or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_inventory_split_requires_guid_amount_and_expected_quantity")
    game = host.last_game
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get(SPLIT_CAPABILITY) != 1
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_inventory_split_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner["connected"] or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [SPLIT, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_inventory_split_refused_downed_or_respawning")
    source, take, expected = selection
    area, pawn = game["map"], owner["pawn"]
    campaign = host.store.status()["campaign_id"]
    created = uuid.uuid5(uuid.NAMESPACE_OID, f"{SPLIT}:{campaign}:{character}:{operation}").hex.upper()
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(SPLIT, pawn, source, created, str(take), str(expected), timeout=6)
        except RuntimeError as error:
            if str(error).startswith("native_inventory_split_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(SPLIT_KEY, {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", area)
                or tuple(proof.get(key) for key in ("map", "pawn", "source_guid", "new_guid", "quantity_before",
                                                   "remaining", "split")) != (area, pawn, source, created, expected, expected - take, take)
                or proof.get("class_path") not in SPLIT_CLASSES):
            raise RuntimeError("The native inventory split did not verify both exact stock stacks")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {SPLIT_KEY: {"state": "split", "owner": character, **proof}}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_inventory_split_committed", operation_id=operation, revision=revision, **result[SPLIT_KEY])
    return result


async def sort_backpack(host, bridge, character: str, target, operation: str) -> dict:
    if target is not None or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_inventory_sort_requires_operation_id")
    game = host.last_game
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get(SORT_CAPABILITY) != 1
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_inventory_sort_unavailable")
    owner = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    if not owner.get("connected") or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [SORT, None, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_inventory_sort_refused_downed_or_respawning")

    area, pawn = game["map"], owner["pawn"]
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(SORT, pawn, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("native_inventory_sort_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(SORT_KEY, {})
        count = proof.get("count")
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn")) !=
                ("hosting", area, area, pawn) or proof.get("state") not in ("sorted", "already_sorted")
                or type(count) is not int or not 0 <= count <= 4096):
            raise RuntimeError("The stock backpack sort did not verify the exact guest inventory")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {SORT_KEY: {"owner": character, **proof}}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_inventory_sort_committed", operation_id=operation, revision=revision, **result[SORT_KEY])
    return result


async def store_equipment(host, bridge, character: str, item, operation: str) -> dict:
    if not valid_guid(item) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_inventory_equip_requires_item_and_operation")
    game = host.last_game
    if (not host.native_guest or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get(STORE_CAPABILITY) != 1
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_inventory_equip_unavailable")
    owner = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    if not owner.get("connected") or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [STORE_EQUIPMENT, item, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_inventory_equip_refused_downed_or_respawning")

    area, pawn = game["map"], owner["pawn"]
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(STORE_EQUIPMENT, pawn, item, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("native_inventory_equip_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(STORE_KEY, {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid"))
                != ("hosting", area, area, pawn, item)):
            raise RuntimeError("The host did not verify the guest's stock equipment move")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {STORE_KEY: dict(proof, owner=character)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_inventory_equip_committed", operation_id=operation, revision=revision, **result[STORE_KEY])
    return result


async def service_split_view(client, active: bool) -> None:
    view = client.last_game.get(SPLIT_VIEW, {})
    sequence = view.get("sequence")
    if view.get("state") != "splitting" or type(sequence) is not int or not client.last_inventory_split < sequence < 2**53:
        return
    client.last_inventory_split = sequence
    area, pawn, source, expected, item_class = (view.get(key) for key in ("map", "pawn", "guid", "count", "class_path"))
    if (not active or client.stopping or not client.host_session or not client.zone_epoch
            or client.gameplay.get(SPLIT_CAPABILITY) != 1 or time.monotonic() - client.gameplay_seen >= 3
            or area != client.last_game.get("map") or item_class not in SPLIT_CLASSES
            or not pawn or pawn != client.last_game.get("pawns", {}).get("local", {}).get("name")
            or not valid_guid(source) or type(expected) is not int or not 1 < expected < 2**31):
        return
    epoch = client.host_session, client.zone_epoch
    operation = uuid.uuid5(client.bridge.input_namespace, f"{epoch[0]}:{epoch[1]}:{pawn}:inventory-split:{sequence}").hex
    outcome = "refused"
    try:
        offset = displayed_offset(client.last_game, sequence, area, pawn)
        if offset is None:
            raise ValueError("The inventory split confirmation expired")
        page = await read_view_page(client, operation, offset)
        if not any(row["guid"] == source and splittable(row) and (row["class_path"], row["quantity"]) ==
                   (item_class, expected) for row in page["items"]):
            raise ValueError("The selected stack is no longer on the authenticated inventory page")
        current = client.bridge.snapshot() or {}
        if ((client.host_session, client.zone_epoch) != epoch or current.get(SPLIT_VIEW) != view
                or displayed_offset(current, sequence, area, pawn) != offset):
            raise ValueError("The inventory split confirmation expired")
        take = expected // 2
        response = await client.gameplay_action(SPLIT, operation, f"{source}|{take}|{expected}")
        proof = response[SPLIT_KEY]
        if (tuple(proof.get(key) for key in ("state", "owner", "map", "class_path", "source_guid", "quantity_before",
                                           "split", "remaining")) != (
                "split", client.invite["character_id"], area, item_class, source, expected, take, expected - take
        ) or not valid_guid(proof.get("new_guid")) or proof["new_guid"] == source):
            raise ValueError("The host did not verify the selected stack split")
        outcome = "split"
        client.control.event("native_guest_inventory_stack_split", operation_id=operation, view_sequence=sequence, **proof)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        client.control.event("native_guest_inventory_split_rejected", operation_id=operation, view_sequence=sequence,
                             reason=str(error))
    with suppress(OSError, RuntimeError):
        client.last_game = await client.bridge.command("native-inventory-view-split-result", str(sequence), area, source,
                                                       outcome, timeout=2)


async def read_page(host, bridge, frame: dict, offset: int) -> dict:
    character = frame.get("character_id")
    if character is None:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    else:
        await asyncio.to_thread(native_host_save.guest_record, host.native_profile, frame, character)
    slot = frame["slot"]
    snapshot = await bridge.command("native-inventory-census", slot, str(offset), timeout=6)
    report = snapshot.get("native_inventory_census", {})
    total, count, items = (report.get(key) for key in ("total", "count", "items"))
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != host.last_game.get("map")
            or report.get("map") != snapshot.get("map") or report.get("slot") != slot or report.get("offset") != offset
            or type(total) is not int or not 0 <= total <= MAX_ITEMS or type(items) is not list
            or count != len(items) or count != min(PAGE_SIZE, max(0, total - offset))):
        raise RuntimeError("The isolated stock inventory census returned an incomplete page")
    for item in items:
        if (not valid_guid(item.get("guid")) or item.get("source") not in SOURCES
                or type(item.get("index")) is not int or not 0 <= item["index"] < 4096
                or type(item.get("quantity")) is not int or item["quantity"] < 1
                or not isinstance(item.get("class_path"), str) or not item["class_path"].startswith("/Game/")
                or any(type(item.get(key)) is not bool for key in ("stackable", "important", "cannot_keep"))):
            raise RuntimeError("The stock inventory census contains an invalid row")
    if len({item["guid"] for item in items}) != count:
        raise RuntimeError("The stock inventory census contains duplicate identities")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"offset": offset, "total": total, "items": items,
            "next_offset": offset + count if offset + count < total else None}


async def inspect(host, bridge, character: str, offset: int) -> dict:
    if type(offset) is not int or not 0 <= offset <= MAX_ITEMS:
        raise party.RuleError("invalid_inventory_offset")
    if (not host.native_zones or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or character not in host.roles().values()):
        raise party.RuleError("native_inventory_scope_unavailable")
    if character != "host" and character not in host.native_recipients():
        raise party.RuleError("native_inventory_scope_unavailable")
    await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
    frame = saved_frame(host, host.store.status(), character)
    if not frame or frame["map"] != host.last_game.get("map"):
        raise RuntimeError("The exact character inventory frame is unavailable")
    return {"native_inventory": {"owner": character, **await read_page(host, bridge, frame, offset)}}


def saved_frame(host, campaign: dict, character: str) -> dict | None:
    if character == "host":
        return campaign["world"].get("native_host_save")
    return next((item["state"].get("native_guest_save") for item in campaign["characters"]
                 if item["character_id"] == character), None)


async def read_complete(host, bridge, frame: dict) -> dict:
    items, offset, total = [], 0, None
    while offset is not None:
        page = await read_page(host, bridge, frame, offset)
        if total is not None and page["total"] != total:
            raise RuntimeError("The isolated stock inventory changed during readback")
        total = page["total"]
        items.extend(page["items"])
        offset = page["next_offset"]
    if len(items) != total or len({item["guid"] for item in items}) != total:
        raise RuntimeError("The isolated stock inventory census is incomplete")
    return {"slot": frame["slot"], "sha256": frame["sha256"], "complete": True, "items": items}
