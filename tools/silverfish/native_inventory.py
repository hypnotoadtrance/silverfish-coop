"""Read pages from the native save frame of one authenticated character."""

import asyncio
import time

from . import native_host_save, party
from .native_gold import valid_guid


PAGE_SIZE = 16
MAX_ITEMS = 8194
SOURCES = {"backpack", "equipment", "armor", "helmet"}


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
    await host.checkpoint_native(bridge)
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
