"""Settle a native inventory drop or pickup and retain it for the next save."""

import asyncio
from contextlib import suppress
import math
import re
import time
import uuid

from . import native_guest_drop_record as record
from . import native_guest_consumable
from . import native_guest_stack_drop as probe
from . import native_host_save, native_inventory, native_inventory_death, native_loot, native_pickup_audio, party
from .native_gold import valid_guid


DROP = "native-guest-stack-drop"
RECLAIM = record.PICKUP_ACTION
HOST_DROP = "native-host-stack-drop"
HOST_RECLAIM = "native-host-stack-reclaim"
ACTIONS = (DROP, RECLAIM)
RESULT = "native_guest_stack_drop"
STOCK_DROP_FIXTURE = "native-inventory-stock-drop-fixture"
STOCK_USE_FIXTURE = "native-inventory-stock-use-fixture"
STOCK_SORT_FIXTURE = "native-inventory-stock-sort-fixture"
STOCK_SPLIT_FIXTURE = "native-inventory-stock-split-fixture"
STOCK_EQUIP_FIXTURE = "native-inventory-stock-equip-fixture"
STOCK_FIXTURES = {STOCK_DROP_FIXTURE: "drop", STOCK_USE_FIXTURE: "use",
                  STOCK_SORT_FIXTURE: "sort", STOCK_SPLIT_FIXTURE: "split", STOCK_EQUIP_FIXTURE: "equip"}
_SELECTION = re.compile(r"([0-9A-F]{32})\|([1-9][0-9]{0,2})\Z")


def visible_drops(state: dict, area: str | None) -> dict:
    return {drop_id: {**record.visible(row, drop_id), **({"transform": row["transform"]} if "transform" in row else {})}
            for drop_id, row in state.get(record.STATE, {}).items()
            if row["map"] == area and row["claim_id"] is None}


def _result(row: dict, drop_id: str, action: str) -> dict:
    result = record.visible(row, drop_id)
    if action == RECLAIM:
        result.update(state="reclaimed", claimed_by=record.claimed_by(row))
        if "stack_merge" in row:
            result.update(inventory_guid=row["inventory_guid"], stack_merge=dict(row["stack_merge"]))
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


def _position(row: dict):
    return row.get("transform", {}).get("position", row["location"])


def _near(host, character: str, row: dict) -> bool:
    origin = host.authoritative_pawns(host.roles()).get(character, {})
    return (origin.get("map") == row["map"] and record.valid_position(origin.get("location"))
            and math.dist(origin["location"], _position(row)) <= party.INTERACT_RANGE)


def _qualified_item(row: dict, guid: str, count: int, item: str) -> bool:
    stackable = row.get("stackable")
    return ((row.get("guid"), row.get("quantity"), row.get("class_path"), row.get("source"))
            == (guid, count, item, "backpack") and row.get("important") is False
            and type(stackable) is bool and stackable == record.STACKABLE.get(item, stackable)
            and (stackable or count == 1))


def droppable(row: dict, *, generic=False) -> bool:
    guid, count, item = row.get("guid"), row.get("quantity"), row.get("class_path")
    return (valid_guid(guid) and record.valid_quantity(item, count)
            and (row.get("droppable") is True if generic else item in record.STACKABLE)
            and _qualified_item(row, guid, count, item))


def view_identity(client, sequence: int, area: str, pawn: str) -> tuple:
    return client.host_session, client.zone_epoch, client.stack_drop_projection.admission, sequence, area, pawn


async def stock_menu_fixture(client, target, operation: str, mode: str) -> dict:
    game = client.last_game
    area, epoch = game.get("map"), client.zone_epoch
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    sorting = mode == "sort"
    splitting = mode == "split"
    split = native_inventory.split_target(target) if splitting else None
    if splitting and split is None:
        raise ValueError("Select an exact stock stack, split amount, and current quantity")
    capability = {"sort": native_inventory.SORT_CAPABILITY, "split": native_inventory.SPLIT_CAPABILITY,
                  "equip": native_inventory.STORE_CAPABILITY}.get(mode, record.CAPABILITY)
    selection = "-" if sorting else split[0] if splitting else target
    valid_selection = target is None if sorting else valid_guid(selection)
    if (game.get("state") != "connected" or not game.get("save_scope") or not pawn
            or not client.transport.game_access or client.needs_travel or client.server_transitioning
            or client.gameplay.get(capability) != 1 or not valid_selection
            or not record.supported_map(area) or not record.valid_id(epoch)):
        raise ValueError("The selected stock backpack action is unavailable")
    arguments = (str(split[1]),) if splitting else ()
    snapshot = await client.bridge.command("native-inventory-view-fixture", f"stock-{mode}", epoch, selection, *arguments, timeout=5)
    match = re.fullmatch(r"stock guest (sort|split|equip|context_menu|use-[a-z-]+) (-|[0-9A-F]{32})"
                         r" (0|[1-9][0-9]{0,9}) ([1-9][0-9]{0,15})(?: ([1-9][0-9]{0,9}))?",
                         snapshot.get("ack", {}).get("message", ""))
    if (not match or match[2] != selection or (not splitting and int(match[3]) > record.MAX_QUANTITY)
            or (snapshot.get("state"), snapshot.get("map"), snapshot.get("pawns", {}).get("local", {}).get("name"))
            != ("connected", area, pawn) or client.zone_epoch != epoch):
        raise RuntimeError("The stock screen did not verify the selected backpack action")
    client.last_game = snapshot
    entry, guid, count, sequence, expected = match.groups()
    return await settle_stock_action(client, area, epoch, operation, mode, entry, guid, int(count),
                                     sequence, expected, split)


async def settle_stock_action(client, area, epoch, operation, mode, entry, guid, quantity, sequence, expected, split):
    sorting, splitting, unloading = mode == "sort", mode == "split", mode == "unload"
    accepted = False
    finish = ("native-inventory-stock-drop-finish", epoch, sequence)
    try:
        if sorting:
            if entry != "sort" or quantity != 0:
                raise ValueError("The native action did not request a backpack sort")
            key = native_inventory.SORT_KEY
            receipt = (await client.gameplay_action(native_inventory.SORT, operation, None)).get(key)
            if (not isinstance(receipt, dict) or receipt.get("owner") != client.invite["character_id"]
                    or receipt.get("map") != area or receipt.get("state") not in ("sorted", "already_sorted")
                    or type(count := receipt.get("count")) is not int or not 0 <= count <= 4096):
                raise RuntimeError("The host did not verify sorting the guest's stock backpack")
            committed = ("sorted",)
        elif splitting or unloading:
            if entry != ("take-ammo" if unloading else "split") or expected is None or (guid, quantity, int(expected)) != split:
                raise ValueError("The stock stack changed before the selected split")
            key = native_inventory.TAKE_AMMO_KEY if unloading else native_inventory.SPLIT_KEY
            action = native_inventory.TAKE_AMMO if unloading else native_inventory.SPLIT
            target = f"{guid}|{quantity}" if unloading else f"{guid}|{quantity}|{expected}"
            receipt = (await client.gameplay_action(action, operation, target)).get(key)
            remaining = split[2] - quantity
            if (not isinstance(receipt, dict)
                    or tuple(receipt.get(field) for field in (
                        "state", "owner", "map", "source_guid", "quantity_before", "split", "remaining",
                    )) != ("unloaded" if unloading else "split", client.invite["character_id"], area, guid, split[2], quantity, remaining)
                    or (not native_inventory.valid_take_ammo_result(receipt.get("weapon_class"), receipt.get("class_path"), quantity)
                        if unloading
                        else receipt.get("class_path") not in native_inventory.SPLIT_CLASSES)
                    or not valid_guid(created := receipt.get("new_guid")) or created == guid):
                raise RuntimeError("The host did not verify both resulting stock stacks")
            committed = (str(remaining), created)
        elif mode == "equip":
            if entry != "equip" or quantity != 1:
                raise ValueError("The native action did not select one equipment item")
            requested_slot = int(expected or 0) - 1
            if not -1 <= requested_slot < 8:
                raise ValueError("The stock equipment destination is invalid")
            target = guid if requested_slot == -1 else f"{guid}|{requested_slot}"
            key = native_inventory.STORE_KEY
            receipt = (await client.gameplay_action(native_inventory.STORE_EQUIPMENT, operation, target)).get(key)
            if (not isinstance(receipt, dict)
                    or (receipt.get("owner"), receipt.get("map"), receipt.get("guid")) != (client.invite["character_id"], area, guid)
                    or type(slot := receipt.get("slot")) is not int or not 0 <= slot < 8
                    or requested_slot != -1 and slot != requested_slot
                    or ((displaced := receipt.get("displaced")) != "-" and (not valid_guid(displaced) or displaced == guid))):
                raise RuntimeError("The host did not verify the saved stock equipment slot")
            committed = ("equipped", str(slot), displaced)
        elif mode == "unequip":
            slot = int(expected or 0) - 1
            if (entry != "unequip" or quantity != 1 or not 0 <= slot < 8
                    or client.gameplay.get(native_inventory.UNEQUIP_CAPABILITY) != 1):
                raise ValueError("The selected quick slot has no host-authoritative Unequip action")
            key = native_inventory.UNEQUIP_KEY
            receipt = (await client.gameplay_action(native_inventory.UNEQUIP, operation, f"{guid}|{slot}")).get(key)
            if (not isinstance(receipt, dict) or type(receipt.get("slot")) is not int
                    or tuple(receipt.get(field) for field in ("state", "owner", "map", "guid", "slot"))
                    != ("unequipped", client.invite["character_id"], area, guid, slot)
                    or type(rounds := receipt.get("ammo")) is not int or not -1 <= rounds < 2**31):
                raise RuntimeError("The host did not verify returning the stock equipment to the backpack")
            committed = ("unequipped", str(slot), str(rounds))
        elif mode == "use":
            action = f"native-guest-{entry.removeprefix('use-')}"
            meal = native_guest_consumable.MEALS.get(action)
            if (not entry.startswith("use-") or quantity != 1 or action not in native_guest_consumable.RESULT
                    or meal is not None and not client.invite.get("native_guest_mre")):
                raise ValueError("The selected stock item has no host-authoritative Use action")
            key = native_guest_consumable.RESULT[action]
            receipt = (await client.gameplay_action(action, operation, guid)).get(key)
            if not isinstance(receipt, dict):
                raise RuntimeError("The host did not return a stock consumable receipt")
            remaining, before = receipt.get("quantity_after"), receipt.get("quantity_before")
            if (tuple(receipt.get(field) for field in ("state", "map", "guid")) != ("used", area, guid)
                    or type(remaining) is not int or type(before) is not int or remaining < 0 or remaining != before - 1
                    or meal is not None and receipt.get("class_path") != meal.class_path
                    or action == native_guest_consumable.STARTER_BAG
                    and (before != 1 or receipt.get("class_path") not in native_guest_consumable.STARTER_BAGS.values()
                         or not native_guest_consumable.starter_awards(guid, receipt.get("awarded_guids")))):
                raise RuntimeError("The host did not verify consumption of the selected stock item")
            committed = (str(remaining),)
        else:
            if entry not in ("context_menu", "ctrl_click") or quantity < 1:
                raise ValueError("The native action did not select a backpack drop")
            key, remaining = RESULT, 0
            receipt = (await client.gameplay_action(DROP, operation, f"{guid}|{quantity}")).get(key)
            if (not isinstance(receipt, dict)
                    or tuple(receipt.get(field) for field in ("state", "drop_id", "owner", "guid", "quantity", "map"))
                    != ("dropped", operation, client.invite["character_id"], guid, quantity, area)
                    or not record.valid_quantity(receipt.get("class_path"), quantity)
                    or not record.valid_position(receipt.get("location"))):
                raise RuntimeError("The host did not settle the stock guest backpack drop")
            committed = (str(remaining),)
        await client.guest_ui.refresh(client)
        accepted = True
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"The stock guest backpack could not refresh: {error}") from error
    finally:
        if not accepted:
            with suppress(OSError, RuntimeError, ValueError):
                await client.bridge.command(*finish, "refused", timeout=3)
    client.last_game = await client.bridge.command(*finish, *committed, timeout=5)
    return {key: receipt, "stock_inventory_proof": client.last_game.get("ack", {}).get("message")}


async def service_stock_view(client, active: bool) -> None:
    if not active or client.stopping or client.suspended:
        return
    view = client.last_game.get(record.STOCK_ACTION_KEY)
    if (not view or view["epoch"] != client.zone_epoch or not client.transport.game_access
            or view["sequence"] <= client.last_stock_inventory_action):
        return
    client.last_stock_inventory_action = view["sequence"]
    area, epoch, sequence, mode, guid, count, expected = (
        view[key] for key in ("area", "epoch", "sequence", "mode", "guid", "count", "expected")
    )
    operation = uuid.uuid5(client.bridge.input_namespace,
                           f"{client.host_session}:{epoch}:{view['pawn']}:stock-inventory:{sequence}").hex
    split = (guid, count, expected) if mode in ("split", "unload") else None
    try:
        result = await settle_stock_action(client, area, epoch, operation, mode, view["entry"],
                                           guid, count, str(sequence), str(expected), split)
        client.control.event("native_stock_inventory_action", operation_id=operation, **result)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        client.control.event("native_stock_inventory_rejected", operation_id=operation, reason=str(error))


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
        generic = client.gameplay.get(record.GENERIC_CAPABILITY) == 1
        if not any(row.get("guid") == guid and row.get("class_path") == item and row.get("quantity") == count
                   and droppable(row, generic=generic) for row in page["items"]):
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
                  source: dict, prepared: dict | None, candidate: dict, scope: tuple[str, str, str],
                  *, defer: bool = False) -> dict:
    area, epoch, pawn = scope
    row = candidate[record.STATE][drop_id]
    if row["map"] != area or source.get("map") != area or (prepared is not None and prepared.get("map") != area):
        raise RuntimeError("The stack drop transaction frames do not belong to the exact source map")
    if action == DROP:
        await probe.verify_owner_frame(host.native_profile, source, character)
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, prepared)
    elif source.get("pending") is not True:
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, source)
    if host.last_game.get("map") != area or host.zone_epoch != epoch or _participant(host, character) != pawn:
        raise party.RuleError("native_guest_stack_drop_scope_changed")
    if not _near(host, character, row):
        raise party.RuleError("native_guest_stack_drop_out_of_range")
    verb, expected = ("debit", "applied") if action == DROP else ("reclaim", "recovered")
    replacement = prepared["slot"] if prepared else None
    if defer:
        mode = "pending" if source.get("pending") is True else "memory"
        merge_source = (row["guid"], str(row["quantity"]), row["class_path"], mode)
    else:
        merge_source = (row["guid"],) if action == RECLAIM and host.last_game.get("native_paired_save_capture") is True else ()
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(probe.SETTLE, verb, pawn, drop_id, source["slot"], replacement or "-",
                                            *(str(axis) for axis in _position(row)), area, *merge_source, timeout=7)
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
        if not proof["ok"] and proof.get("count") == 0 and status.startswith(("native_inventory_wipe_", "native_loot_")):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(status)
        merged = status == "merged"
        if not proof["ok"] or proof.get("count") != 1 or (status != expected and not (merge_source and merged)):
            raise RuntimeError(status or "The native typed stack drop mutation was unverified")
        if merged:
            row.update(native_loot.verified_stack_merge(proof.get("merge", {}), row["guid"], row["quantity"]))
        result = _result(row, drop_id, action)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": [action, request, None], "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if defer:
            if character == "host":
                host.pending_host_save = True
        else:
            capture = {"typed_inventory": True} if merged else {}
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}),
                                                    **capture)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_guest_stack_drop_committed", phase=verb, revision=revision, **result[RESULT])
    if action == RECLAIM:
        native_pickup_audio.committed(host, pawn, operation, row["class_path"])
    return result


async def _retain_drop_inventory(host, bridge, character: str, pawn: str):
    if character != "host" and not await native_inventory.retain_guest_save(host, bridge, character, pawn):
        raise party.RuleError("native_guest_save_retention_unavailable")


async def perform(host, bridge, character: str, action: str, target: str | None, operation: str) -> dict:
    from .native_world_loot import CAPABILITY, SHARED_PICKUPS_VERSION

    if action not in ACTIONS or not record.valid_id(operation):
        raise party.RuleError("invalid_operation_id")
    pawn = _participant(host, character)
    area, epoch = host.last_game["map"], host.zone_epoch
    scope = area, epoch, pawn
    defer = host.last_game.get(probe.MEMORY_CAPABILITY) is True
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
        from .native_world_loot import item_is_moving, player_drop_identity

        if item_is_moving(host, player_drop_identity(target)):
            raise party.RuleError("native_world_loot_item_in_motion")
        if not _near(host, character, original):
            raise party.RuleError("native_guest_stack_drop_out_of_range")
        escrow = original["escrow"]
        if defer:
            await _retain_drop_inventory(host, bridge, character, pawn)
        else:
            await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
            await _escrow_item(host, bridge, escrow, original["guid"], original["quantity"], original["class_path"])
        candidate = party.load_party(host.party)
        row = candidate[record.STATE][target]
        row.update(claim_id=operation, claimed_by=character)
        row.pop("escrow")
        return await _settle(host, bridge, character, action, target, target, operation, escrow, None, candidate, scope, defer=defer)

    selected = _SELECTION.fullmatch(target) if isinstance(target, str) else None
    if not selected or not valid_guid(selected[1]) or int(selected[2]) > record.MAX_QUANTITY:
        raise party.RuleError("native_guest_stack_drop_exact_guid_and_quantity_required")
    guid, count = selected[1], int(selected[2])
    if operation in drops:
        raise party.RuleError("operation_id_conflict")
    active = [row for row in drops.values() if row["claim_id"] is None]
    multiple = host.last_game.get(CAPABILITY, 0) >= SHARED_PICKUPS_VERSION
    if (len(drops) >= record.MAX_DROPS or len(active) >= record.MAX_ACTIVE
            or any(row["guid"] == guid or (not multiple and row["owner"] == character) for row in active)):
        raise party.RuleError("native_guest_stack_drop_capacity")
    if defer:
        slot = native_host_save.new_slot()
        await _retain_drop_inventory(host, bridge, character, pawn)
        if host.zone_epoch != epoch or host.last_game.get("map") != area or _participant(host, character) != pawn:
            raise party.RuleError("native_guest_stack_drop_scope_changed")
        candidate = party.load_party(host.party)
        birth = (await probe.perform(host, bridge, character, guid, count, fixture=False, memory_slot=slot))[probe.KEY]
        row = dict(owner=character, guid=guid, quantity=count, class_path=birth["class_path"], map=area,
                   location=birth["location"], claim_id=None, escrow={"slot": slot, "map": area, "pending": True})
        candidate.setdefault(record.STATE, {})[operation] = row
        result = _result(row, operation, action)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": [action, target, None], "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.party = candidate
        if character == "host":
            host.pending_host_save = True
        host.control.event("native_guest_stack_drop_committed", phase="debit", revision=None, **result[RESULT])
        return result
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
