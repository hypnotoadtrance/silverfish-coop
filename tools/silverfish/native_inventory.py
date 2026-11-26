"""Read pages from the native save frame of one authenticated character."""

import asyncio
from contextlib import suppress
import re
import time
import uuid

from . import native_host_save, party
from .guest_weapon_profiles import (
    AMMO_BY_PATH, BOLT_ACTION_AMMO, BOLT_ACTION_UNLOAD_CAPACITY, BOLT_ACTION_UNLOADS,
    MAX_TAKE_AMMO_ROUNDS, TAKE_AMMO_MAGAZINES,
    compatible_magazine_round,
)
from .native_gold import valid_guid


PAGE_SIZE = 16
MAX_ITEMS = 8194
SOURCES = {"backpack", "equipment", "armor", "helmet"}
SPLIT = "native-inventory-split"
SPLIT_KEY = "native_inventory_split"
SPLIT_CAPABILITY = "native_inventory_split_capability"
SPLIT_PREVIEW_CAPABILITY = "native_inventory_split_preview_capability"
TAKE_AMMO = "native-inventory-take-ammo"
TAKE_AMMO_KEY = "native_inventory_take_ammo"
TAKE_AMMO_CAPABILITY = "native_inventory_take_ammo_capability"
TAKE_AMMO_PREVIEW_CAPABILITY = "native_inventory_take_ammo_preview_capability"
_TAKE_AMMO_TARGET = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,9})")
_SPLIT_TARGET = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,9})\|([1-9][0-9]{0,9})")
SPLIT_VIEW = "native_guest_inventory_split_view"
SORT = "native-inventory-sort"
SORT_CAPABILITY = "native_inventory_sort_capability"
SORT_KEY = "native_inventory_sort"
STORE_EQUIPMENT = "native-inventory-store-equipment"
LOCKPICKS = frozenset({
    "/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C",
    "/Game/Classes/Equipment/Eqp_LockPick_Hev.Eqp_LockPick_Hev_C",
})
STORE_CAPABILITY = "native_inventory_store_equipment_capability"
STORE_KEY = "native_inventory_store_equipment"
UNEQUIP = "native-inventory-unequip"
UNEQUIP_CAPABILITY = "native_inventory_unequip_capability"
UNEQUIP_WORN_CAPABILITY = "native_inventory_unequip_worn_capability"
UNEQUIP_KEY = "native_inventory_unequip"
_STORE_TARGET = re.compile(r"([0-9A-F]{32})(?:\|([0-7])(?:\|([0-9a-f]{32}))?)?\Z")
_UNEQUIP_TARGET = re.compile(r"([0-9A-F]{32})\|([0-9])(?:\|([0-9a-f]{32}))?\Z")
_STOCK_CLASS = re.compile(r"/Game/Classes/[A-Za-z0-9_./-]+_C\Z")


def _equipment_identity(fields, area, slots="01234567"):
    _, world, pawn, item, slot, item_class = fields[:6]
    if (world != area or not valid_guid(item) or slot not in slots or len(slot) != 1
            or len(item_class) > 256 or not _STOCK_CLASS.fullmatch(item_class)):
        raise ValueError("Invalid stock equipment slot report")
    return dict(map=world, pawn=pawn, guid=item, slot=int(slot), class_path=item_class)


def equipment_receipt(fields, area):
    row = _equipment_identity(fields, area)
    displaced = fields[6]
    if displaced != "-" and (not valid_guid(displaced) or displaced == row["guid"]):
        raise ValueError("Invalid displaced stock equipment item")
    return dict(row, displaced=displaced)


def unequip_receipt(fields, area):
    row = _equipment_identity(fields, area, "0123456789")
    value = fields[6]
    if not re.fullmatch(r"-1|0|[1-9][0-9]{0,9}", value) or int(value) > 2**31 - 1:
        raise ValueError("Invalid returned stock equipment ammo")
    return dict(row, state="unequipped", ammo=int(value))


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


def split_class_path(path):
    return isinstance(path, str) and len(path) <= 256 and ".." not in path and _STOCK_CLASS.fullmatch(path) is not None


def splittable(row):
    quantity = row.get("quantity")
    return (row.get("source") == "backpack" and split_class_path(row.get("class_path"))
            and row.get("stackable") is True and row.get("important") is False
            and type(quantity) is int and 1 < quantity < 2**31)


def take_ammo_target(value):
    match = _TAKE_AMMO_TARGET.fullmatch(value) if isinstance(value, str) else None
    if match and valid_guid(match[1]) and 1 <= (rounds := int(match[2])) <= MAX_TAKE_AMMO_ROUNDS:
        return match[1], rounds, rounds
    return None


def valid_take_ammo_result(weapon, ammo, rounds):
    if not isinstance(weapon, str) or not isinstance(ammo, str) or type(rounds) is not int or rounds < 1:
        return False
    if weapon in BOLT_ACTION_UNLOADS:
        return ammo in BOLT_ACTION_AMMO and rounds <= BOLT_ACTION_UNLOAD_CAPACITY
    magazine = TAKE_AMMO_MAGAZINES.get(weapon)
    round_type = AMMO_BY_PATH.get(ammo)
    return (magazine is not None and round_type is not None and rounds <= magazine.capacity
            and compatible_magazine_round(magazine.ammo, round_type))


async def retain_guest_save(host, bridge, character: str, pawn: str) -> bool:
    """Keep the exact working inventory until a paired save is requested."""
    if host.last_game.get("native_guest_save_retention") is not True:
        return False
    retained = host.pending_guest_saves.get(character)
    if retained and retained[0] == pawn:
        return True
    area = host.last_game["map"]
    snapshot = await bridge.command("native-guest-save-retain", pawn, character, timeout=3)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise party.RuleError("native_guest_save_scope_changed")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    host.queue_guest_save(character, pawn)
    return True


async def split_stack(host, bridge, character: str, target, operation: str, *, unload=False) -> dict:
    action, key, capability = ((TAKE_AMMO, TAKE_AMMO_KEY, TAKE_AMMO_CAPABILITY) if unload
                               else (SPLIT, SPLIT_KEY, SPLIT_CAPABILITY))
    prefix = key
    preview_guid = None
    parse_target = take_ammo_target if unload else split_target
    selection = parse_target(target)
    if not selection and isinstance(target, str):
        base, _, created = target.rpartition("|")
        if valid_guid(created) and (parsed := parse_target(base)) and created != parsed[0]:
            selection, preview_guid = parsed, created
    if not selection or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError(f"{prefix}_requires_guid_amount_and_expected_quantity")
    game = host.last_game
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get(capability) != 1
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError(f"{prefix}_unavailable")
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if not owner or not owner["connected"] or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [action, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError(f"{prefix}_refused_downed_or_respawning")
    source, take, expected = selection
    area, pawn = game["map"], owner["pawn"]
    campaign = host.store.status()["campaign_id"]
    created = preview_guid or uuid.uuid5(uuid.NAMESPACE_OID, f"{action}:{campaign}:{character}:{operation}").hex.upper()
    amounts = (str(expected),) if unload else (str(take), str(expected))
    deferred = await retain_guest_save(host, bridge, character, pawn)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action, pawn, source, created, *amounts, timeout=6)
        except RuntimeError as error:
            if str(error).startswith(f"{prefix}_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(key, {})
        ammo_class = proof.get("class_path")
        qualified = (valid_take_ammo_result(proof.get("weapon_class"), ammo_class, expected)
                     if unload else split_class_path(ammo_class))
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", area)
                or tuple(proof.get(key) for key in ("map", "pawn", "source_guid", "new_guid", "quantity_before",
                                                   "remaining", "split")) != (area, pawn, source, created, expected, expected - take, take)
                or not qualified):
            raise RuntimeError("The native inventory split did not verify both exact stock stacks")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: {"state": "unloaded" if unload else "split", "owner": character, **proof}}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    event = f"native_guest_inventory_{'take_ammo' if unload else 'split'}_{'accepted' if deferred else 'committed'}"
    host.control.event(event, operation_id=operation, revision=revision, **result[key])
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
    deferred = await retain_guest_save(host, bridge, character, pawn)
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
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    event = "native_guest_inventory_sort_accepted" if deferred else "native_guest_inventory_sort_committed"
    host.control.event(event, operation_id=operation, revision=revision, **result[SORT_KEY])
    return result


async def store_equipment(host, bridge, character: str, target, operation: str, *, unequip=False) -> dict:
    action, capability, key = (UNEQUIP, UNEQUIP_CAPABILITY, UNEQUIP_KEY) if unequip else (
        STORE_EQUIPMENT, STORE_CAPABILITY, STORE_KEY)
    suffix = "unequip" if unequip else "equip"
    pattern = _UNEQUIP_TARGET if unequip else _STORE_TARGET
    selection = pattern.fullmatch(target) if isinstance(target, str) else None
    if (not selection or not valid_guid(selection[1])
            or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError(f"native_inventory_{suffix}_requires_item_and_operation")
    item, requested_slot = selection.group(1, 2)
    reported = selection[3] is not None
    if unequip and int(requested_slot) >= 8:
        capability = UNEQUIP_WORN_CAPABILITY
    if reported and selection[3] != host.zone_epoch:
        raise party.RuleError(f"native_inventory_{suffix}_visit_expired")
    game = host.last_game
    if (not host.native_guest or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get(capability) != 1
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError(f"native_inventory_{suffix}_unavailable")
    owner = host.native_recipients().get(character, {})
    player = host.party["players"].get(character)
    if not owner.get("connected") or not owner.get("pawn") or not player:
        raise party.RuleError("guest_not_active")
    fingerprint = [action, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if host.party["wipe"] or character in host.party["pending_spawns"] or (not reported and player["downed"]):
        raise party.RuleError(f"native_inventory_{suffix}_refused_downed_or_respawning")

    area, pawn = game["map"], owner["pawn"]
    deferred = await retain_guest_save(host, bridge, character, pawn)
    host.native_checkpoint_unsafe = True
    try:
        try:
            arguments = (pawn, item) if requested_slot is None else (pawn, item, requested_slot)
            if reported:
                arguments += ("reported",)
            snapshot = await bridge.command(action, *arguments, timeout=6)
        except RuntimeError as error:
            if str(error).startswith(f"native_inventory_{suffix}_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(key, {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid"))
                != ("hosting", area, area, pawn, item)
                or requested_slot is not None and proof.get("slot") != int(requested_slot)
                or unequip and (proof.get("state") != "unequipped" or type(proof.get("ammo")) is not int)):
            raise RuntimeError("The host did not verify the guest's stock equipment move")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: dict(proof, owner=character)}
        if reported:
            result[key]["reported"] = True
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    event = f"native_guest_inventory_{suffix}_{'accepted' if deferred else 'committed'}"
    host.control.event(event, operation_id=operation, revision=revision, **result[key])
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
            or area != client.last_game.get("map") or not split_class_path(item_class)
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
    return _verified_page(host, snapshot, slot, offset)


def _verified_page(host, snapshot: dict, slot: str, offset: int, *, pawn: str | None = None) -> dict:
    report = snapshot.get("native_inventory_census", {})
    total, count, items = (report.get(key) for key in ("total", "count", "items"))
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != host.last_game.get("map")
            or report.get("map") != snapshot.get("map") or report.get("slot") != slot or report.get("offset") != offset
            or report.get("pawn") != pawn
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
    if character == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
        role = "host"
    else:
        pawn = host.native_recipients().get(character, {}).get("pawn")
        role = "guest"
    if not pawn or host.last_game.get("native_live_inventory_census") is not True:
        raise party.RuleError("native_inventory_scope_unavailable")
    snapshot = await bridge.command("native-inventory-live-census", pawn, str(offset), role, timeout=6)
    return {"native_inventory": {"owner": character, **_verified_page(host, snapshot, "-", offset, pawn=pawn)}}


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
