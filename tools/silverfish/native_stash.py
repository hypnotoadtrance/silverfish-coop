"""Read a rest-site stash from the requesting character's native save."""

import asyncio
import re

from . import native_host_save, native_inventory, party
from .native_gold import valid_guid


LIST = "native-stash-list"
CENSUS = "native-stash-census"
RESULT = "native_stash"
SETTING = "native_stash_setting"
SITES = "native_stash_sites"
_TARGET = re.compile(r"([0-9A-F]{32})(?:\|(0|[1-9][0-9]{0,3}))?\Z")
_PATH = re.compile(r"([A-Za-z0-9_-]+_C) (/Game/Classes/Maps/[A-Za-z0-9_/-]+)\.([A-Za-z0-9_-]+):PersistentLevel\.([A-Za-z0-9_-]+)\Z")


def valid_site(area, name):
    match = _PATH.fullmatch(name)
    return (match is not None and match[2] == area and match[3] == area.rsplit("/", 1)[-1]
            and match[4].startswith(match[1] + "_") and match[4][len(match[1]) + 1:].isdigit())


async def inspect(host, bridge, character, target):
    selection = _TARGET.fullmatch(target) if isinstance(target, str) else None
    if not selection or not valid_guid(selection[1]) or int(selection[2] or 0) > 4096:
        raise party.RuleError("native_stash_exact_site_required")
    site, offset = selection[1], int(selection[2] or 0)
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

    revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
    state = host.store.status()
    frame = native_inventory.saved_frame(host, state, character)
    if (not frame or frame.get("map") != area or state.get("revision") != revision or host.zone_epoch != zone
            or host.last_game.get("map") != area or host.last_game.get(SETTING) != combined):
        raise party.RuleError("native_stash_scope_changed")
    if character == "host":
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    else:
        await asyncio.to_thread(native_host_save.guest_record, host.native_profile, frame, character)
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
