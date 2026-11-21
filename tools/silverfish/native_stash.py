"""Read and move stock items in the requesting character's private stash."""

import asyncio
import re

from . import native_host_save, native_inventory, party
from .native_gold import valid_guid


LIST = "native-stash-list"
CENSUS = "native-stash-census"
MOVE = "native-stash-move"
MOVED = "native_stash_move"
RESULT = "native_stash"
SETTING = "native_stash_setting"
SITES = "native_stash_sites"
_TARGET = re.compile(r"([0-9A-F]{32})(?:\|(0|[1-9][0-9]{0,3}))?\Z")
_MOVE_TARGET = re.compile(r"([0-9A-F]{32})\|(deposit|withdraw)\|([0-9A-F]{32})\Z")
_PATH = re.compile(r"([A-Za-z0-9_-]+_C) (/Game/Classes/Maps/[A-Za-z0-9_/-]+)\.([A-Za-z0-9_-]+):PersistentLevel\.([A-Za-z0-9_-]+)\Z")


def valid_site(area, name):
    match = _PATH.fullmatch(name)
    return (match is not None and match[2] == area and match[3] == area.rsplit("/", 1)[-1]
            and match[4].startswith(match[1] + "_") and match[4][len(match[1]) + 1:].isdigit())


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
    frame = await _frame(host, bridge, character, site, scope)
    snapshot = await bridge.command(CENSUS, pawn, frame["slot"], site, str(offset), timeout=5)
    report = snapshot.get("native_stash_census", {})
    if ((snapshot.get("state"), snapshot.get("map"), snapshot.get(SETTING)) != ("hosting", area, combined)
            or tuple(report.get(key) for key in ("map", "pawn", "slot", "site", "offset", "combined")) !=
            (area, pawn, frame["slot"], site, offset, combined)):
        raise RuntimeError("The exact character and rest-site stash did not verify")
    items, total = report["items"], report["total"]
    if (len(items) != report["count"] or len(items) != min(native_inventory.PAGE_SIZE, max(0, total - offset))
            or len({item["guid"] for item in items}) != len(items)
            or any(item["source"] != "stash" or not valid_guid(item["guid"]) or item["quantity"] < 1
                   or not 0 <= item["index"] < 4096 or not item["class_path"].startswith("/Game/") for item in items)):
        raise RuntimeError("The isolated stock stash page is incomplete")
    return {RESULT: dict(owner=character, site=site, combined=combined, offset=offset, total=total, items=items,
                         next_offset=offset + len(items) if offset + len(items) < total else None)}


async def move(host, bridge, character, target, operation):
    selected = _MOVE_TARGET.fullmatch(target) if isinstance(target, str) else None
    if not selected or not valid_guid(selected[1]) or not valid_guid(selected[3]):
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
    site, direction, item = selected.groups()
    scope = _scope(host, character, site)
    area, _, combined, pawn = scope
    frame = await _frame(host, bridge, character, site, scope)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(MOVE, pawn, frame["slot"], site, item, direction, str(int(combined)), timeout=6)
        except RuntimeError as error:
            if str(error).startswith(("native_stash_move_", "native_stash_exact_site_",
                                      "native_stash_participant_", "native_stash_census_save_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(MOVED, {})
        if ((snapshot.get("state"), snapshot.get("map"), snapshot.get(SETTING)) != ("hosting", area, combined)
                or tuple(proof.get(key) for key in ("map", "pawn", "slot", "site", "guid", "direction", "combined")) !=
                (area, pawn, frame["slot"], site, item, direction, combined)
                or not proof.get("class_path") or type(proof.get("quantity")) is not int or proof["quantity"] < 1):
            raise RuntimeError("The authoritative stash transfer did not verify")
        host.last_game = snapshot
        response = {MOVED: dict(owner=character, site=site, combined=combined, direction=direction, guid=item,
                                class_path=proof["class_path"], quantity=proof["quantity"])}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": request, "result": response})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_stash_item_moved", revision=revision, operation_id=operation, **response[MOVED])
    return response
