"""Read and move stock items in the requesting character's private stash."""

import asyncio
from contextlib import suppress
import math
import re
import time
import uuid

from . import native_host_save, native_inventory, party, runtime
from .native_gold import valid_guid


LIST = "native-stash-list"
CENSUS = "native-stash-census"
LIVE_CENSUS = "native_stash_live_census"
LIVE_MOVE = "native_stash_live_move"
LIVE_REORDER = "native_stash_live_reorder"
MOVE = "native-stash-move"
MOVED = "native_stash_move"
RESULT = "native_stash"
SETTING = "native_stash_setting"
SITES = "native_stash_sites"
VIEW = "native_stash_view"
VIEW_FIXTURES = {"native-stash-view-fixture", "native-stash-view-click-fixture"}
_TARGET = re.compile(r"([0-9A-F]{32})(?:\|(0|[1-9][0-9]{0,3}))?\Z")
_MOVE_TARGET = re.compile(r"([0-9A-F]{32})\|(deposit|withdraw|reorder)\|([0-9A-F]{32})(?:\|([0-9A-F]{32}|-))?\Z")
_VIEW_CARD = re.compile(r"(?:(?:drag|cancel|return|close)-)?(?:stash|backpack):([0-9A-F]{32})\Z")
_VIEW_REORDER_CARD = re.compile(r"reorder-stash:([0-9A-F]{32}):(0|[1-9][0-9]{0,3})\Z")
_PATH = re.compile(r"([A-Za-z0-9_-]+_C) (/Game/Classes/Maps/[A-Za-z0-9_/-]+)\.([A-Za-z0-9_-]+):PersistentLevel\.([A-Za-z0-9_-]+)\Z")
_ITEM_CLASS = re.compile(r"/Game/[A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+_C\Z")


def valid_site(area, name):
    match = _PATH.fullmatch(name)
    return (match is not None and match[2] == area and match[3] == area.rsplit("/", 1)[-1]
            and match[4].startswith(match[1] + "_") and match[4][len(match[1]) + 1:].isdigit())


def valid_view_fixture(action, target):
    if not isinstance(target, str) or action not in VIEW_FIXTURES:
        return False
    if action == "native-stash-view-fixture":
        return valid_guid(target)
    match = _VIEW_CARD.fullmatch(target)
    if reorder := _VIEW_REORDER_CARD.fullmatch(target):
        return valid_guid(reorder[1]) and int(reorder[2]) < 4096
    return target == "close" or match is not None and valid_guid(match[1])


def valid_reorder_anchor(item, before):
    return before == "-" or before != item and valid_guid(before)


def _scope(host, character, site):
    game = host.last_game
    area, zone = game.get("map"), host.zone_epoch
    combined = game.get(SETTING)
    if (not host.native_zones or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or type(combined) is not bool
            or site not in game.get(SITES, {}) or host.party.get("wipe") or character in host.party["pending_spawns"]
            or not (player := host.party["players"].get(character)) or player["downed"]):
        raise party.RuleError("native_stash_scope_unavailable")
    if character == "host":
        pawn = game.get("pawns", {}).get("local", {}).get("name")
    else:
        guest = host.native_recipients().get(character, {})
        pawn = guest.get("pawn") if guest.get("connected") else None
    if not pawn:
        raise party.RuleError("native_stash_participant_unavailable")
    return area, zone, combined, pawn


async def _frame(host, bridge, character, site, scope):
    area, _, _, _ = scope
    revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
    state = host.store.status()
    frame = native_inventory.saved_frame(host, state, character)
    if (not frame or frame.get("map") != area or state.get("revision") != revision
            or _scope(host, character, site) != scope):
        raise party.RuleError("native_stash_scope_changed")
    if character == "host":
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    else:
        await asyncio.to_thread(native_host_save.guest_record, host.native_profile, frame, character)
    return frame


async def inspect(host, bridge, character, target):
    selection = _TARGET.fullmatch(target) if isinstance(target, str) else None
    if not selection or not valid_guid(selection[1]) or int(selection[2] or 0) > 4096:
        raise party.RuleError("native_stash_exact_site_required")
    site, offset = selection[1], int(selection[2] or 0)
    scope = _scope(host, character, site)
    area, _, combined, pawn = scope
    live = host.last_game.get(LIVE_CENSUS) is True
    slot = "-" if live else (await _frame(host, bridge, character, site, scope))["slot"]
    snapshot = await bridge.command(CENSUS, pawn, slot, site, str(offset), timeout=5)
    report = snapshot.get("native_stash_census", {})
    if ((snapshot.get("state"), snapshot.get("map"), snapshot.get(SETTING)) != ("hosting", area, combined)
            or tuple(report.get(key) for key in ("map", "pawn", "slot", "site", "offset", "combined")) !=
            (area, pawn, slot, site, offset, combined) or _scope(host, character, site) != scope):
        raise RuntimeError("The exact character and rest-site stash did not verify")
    items, total = report["items"], report["total"]
    if (len(items) != report["count"] or len(items) != min(native_inventory.PAGE_SIZE, max(0, total - offset))
            or len({item["guid"] for item in items}) != len(items)
            or any(item["source"] != "stash" or not valid_guid(item["guid"]) or item["quantity"] < 1
                   or not 0 <= item["index"] < 4096 or not item["class_path"].startswith("/Game/")
                   or live and (type(item.get("ammo")) is not int or not -2**31 <= item["ammo"] < 2**31
                                or item.get("stackable") and item["ammo"] != item["quantity"]) for item in items)):
        raise RuntimeError("The isolated stock stash page is incomplete")
    return {RESULT: dict(owner=character, site=site, combined=combined, offset=offset, total=total, items=items,
                         next_offset=offset + len(items) if offset + len(items) < total else None)}


async def move(host, bridge, character, target, operation):
    selected = _MOVE_TARGET.fullmatch(target) if isinstance(target, str) else None
    if not selected or not valid_guid(selected[1]) or not valid_guid(selected[3]):
        raise party.RuleError("native_stash_exact_site_and_item_required")
    site, direction, item, before = selected.groups()
    if (direction == "reorder" and (before is None or not valid_reorder_anchor(item, before))
            or direction != "reorder" and before is not None):
        raise party.RuleError("native_stash_exact_site_and_item_required")
    if not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("invalid_operation_id")
    request = [MOVE, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != request:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if host.smg_holds is not None and host.smg_holds.strokes:
        raise party.RuleError("native_stash_move_combat_busy")
    scope = _scope(host, character, site)
    area, _, combined, pawn = scope
    live = host.last_game.get(LIVE_MOVE) is True
    if before is not None and (not live or character == "host" or host.last_game.get(LIVE_REORDER) is not True):
        raise party.RuleError("native_stash_move_unavailable")
    if live and character != "host":
        if not await native_inventory.retain_guest_save(host, bridge, character, pawn):
            raise party.RuleError("native_guest_save_retention_unavailable")
        if _scope(host, character, site) != scope:
            raise party.RuleError("native_stash_scope_changed")
    slot = "-" if live else (await _frame(host, bridge, character, site, scope))["slot"]
    host.native_checkpoint_unsafe = True
    try:
        try:
            extra = () if before is None else (before,)
            snapshot = await bridge.command(MOVE, pawn, slot, site, item, direction, str(int(combined)), *extra, timeout=6)
        except RuntimeError as error:
            if str(error).startswith(("native_stash_move_", "native_stash_exact_site_",
                                      "native_stash_participant_", "native_stash_census_save_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(MOVED, {})
        if ((snapshot.get("state"), snapshot.get("map"), snapshot.get(SETTING)) != ("hosting", area, combined)
                or tuple(proof.get(key) for key in ("map", "pawn", "slot", "site", "guid", "direction", "combined")) !=
                (area, pawn, slot, site, item, direction, combined)
                or proof.get("before") != before or not proof.get("class_path")
                or type(proof.get("quantity")) is not int or proof["quantity"] < 1
                or before is not None and proof["quantity"] != 1):
            raise RuntimeError("The authoritative stash transfer did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        response = {MOVED: dict(owner=character, site=site, combined=combined, direction=direction, guid=item,
                                class_path=proof["class_path"], quantity=proof["quantity"])}
        if before is not None:
            response[MOVED]["before"] = before
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": request, "result": response})
        del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if not live:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
        elif character == "host":
            host.pending_host_save = True
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_stash_item_moved", revision=revision, operation_id=operation, **response[MOVED])
    return response


def parse_view(fields, area, phase):
    _, state, sequence, view_area, pawn, site, mode, offset, item, *anchor = fields
    sequence, offset = int(sequence), int(offset)
    if (phase != "connected" or view_area != area or not 1 <= sequence < 2**53 or offset != 0
            or not valid_guid(site) or (item != "-" and not valid_guid(item))
            or not 0 < len(pawn) <= 512 or mode not in {"stash", "backpack"}
            or state not in {"pending", "rendered", "unavailable"}):
        raise ValueError("Invalid stock stash view frame")
    result = dict(state=state, sequence=sequence, map=view_area, pawn=pawn, site=site,
                  mode=mode, offset=offset, item=None if item == "-" else item)
    if anchor:
        before, = anchor
        if mode != "stash" or not result["item"] or not valid_reorder_anchor(item, before):
            raise ValueError("Invalid stash reorder request")
        result["before"] = before
    return result


async def _stock_rows(client, site, operation):
    offset, contents, seen, identity = 0, [], set(), None
    while True:
        page = (await client.gameplay_action(LIST, uuid.uuid5(operation, str(offset)).hex, f"{site}|{offset}"))[RESULT]
        rows, total, combined = page["items"], page["total"], page["combined"]
        if (page["owner"] != client.invite["character_id"] or page["site"] != site or page["offset"] != offset
                or type(combined) is not bool or type(total) is not int or not 0 <= offset <= total <= 4096
                or type(rows) is not list or len(rows) != min(native_inventory.PAGE_SIZE, total - offset)
                or identity is not None and identity != (total, combined)):
            raise ValueError("The host did not return the guest's exact stash")
        identity = total, combined
        for row in rows:
            guid, path, ammo = row["guid"], row["class_path"], row["ammo"]
            weight, value, protected = row["weight"], row["value"], row["never_drop"]
            if (row["source"] != "stash" or not valid_guid(guid) or guid in seen or not _ITEM_CLASS.fullmatch(path)
                    or len(path) > 512 or type(ammo) is not int or not -2**31 <= ammo < 2**31
                    or type(weight) not in {int, float} or not math.isfinite(weight) or not 0 <= weight <= 100000
                    or type(value) is not int or not -2**31 <= value < 2**31 or type(protected) is not bool):
                raise ValueError("The host returned an invalid stock stash item")
            seen.add(guid)
            contents.append(f"{guid}\t{path}\t{ammo}\t{weight}\t{value}\t{int(protected)}\n")
        offset += len(rows)
        if offset == total:
            if page["next_offset"] is not None:
                raise ValueError("The host returned an unexpected stash continuation")
            return contents
        if page["next_offset"] != offset:
            raise ValueError("The host returned a noncontiguous stash page")


async def service_view(client, active):
    view = client.last_game.get(VIEW)
    if not view:
        return
    if not active or not client.transport.game_access or client.stopping or client.gameplay.get("wipe"):
        with suppress(OSError, RuntimeError, ValueError):
            client.last_game = await client.bridge.command("native-stash-view-close", timeout=2)
        return
    if (view["state"] != "pending" or client.last_game.get("map") != view["map"]
            or client.last_game.get("pawns", {}).get("local", {}).get("name") != view["pawn"]
            or not client.host_session or not client.zone_epoch or time.monotonic() - client.gameplay_seen >= 3
            or client.gameplay.get(SITES, {}).get(view["site"], {}).get("map") != view["map"]):
        return
    scope = client.host_session, client.zone_epoch
    operation = uuid.uuid5(client.bridge.input_namespace, f"{scope}:stash:{view['sequence']}")
    arguments = str(view["sequence"]), view["map"], view["site"]
    nonce = uuid.uuid4().hex
    payload = client.bridge.directory / f"native-stash-screen-{nonce}.tsv"
    try:
        if item := view["item"]:
            direction = "deposit" if view["mode"] == "backpack" else "withdraw"
            before = view.get("before")
            if before is not None:
                direction = "reorder"
            target = f"{view['site']}|{direction}|{item}"
            if before is not None:
                target += f"|{before}"
            result = (await client.gameplay_action(MOVE, operation.hex, target))[MOVED]
            if ((result.get("owner"), result.get("site"), result.get("direction"), result.get("guid"),
                 result.get("before")) != (client.invite["character_id"], view["site"], direction, item, before)):
                raise RuntimeError("The stock stash transfer did not verify")
            client.control.event("native_stash_view_item_moved", operation_id=operation.hex, **result)
        rows = await _stock_rows(client, view["site"], operation)
        await client.guest_ui.refresh(client)
        if scope != (client.host_session, client.zone_epoch):
            raise RuntimeError("The stock stash request left its native world")
        runtime.write_text(payload, "".join(rows), newline="")
        snapshot = await client.bridge.command("native-stash-view-render", *arguments, nonce, str(len(rows)), timeout=5)
        if snapshot.get(VIEW) != {**view, "state": "rendered"}:
            raise RuntimeError("The in-game stash screen did not render the verified page")
        client.last_game = snapshot
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
        client.control.event("native_stash_view_unavailable", operation_id=operation.hex, reason=str(error))
        with suppress(OSError, RuntimeError, ValueError):
            client.last_game = await client.bridge.command("native-stash-view-unavailable", *arguments, timeout=3)
    finally:
        with suppress(FileNotFoundError, PermissionError):
            payload.unlink()
