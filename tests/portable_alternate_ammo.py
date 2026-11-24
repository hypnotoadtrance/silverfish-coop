"""Verify that the guest's stock .45 alternate ammo survives a reload, shot, and unload."""

import asyncio
import re

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_inventory
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import FORTY_FIVE_BALL, FORTY_FIVE_HOLLOW_POINT


async def verify_alternate_ammo(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    alternate = FORTY_FIVE_HOLLOW_POINT.item_path

    async def command(seat, action):
        return await probe(seat.bridge, "portable-dry-fire", action)

    async def read(seat, action, expected):
        last = None
        try:
            async with asyncio.timeout(10):
                while True:
                    last = await command(seat, action)
                    if last == expected:
                        return
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"{action} returned {last!r}, expected {expected!r}") from error

    async def stock_input(kind, frame, guid):
        event = f"native_guest_{kind}_input"
        result = await submit(guest_run, f"native-guest-{kind}-input-fixture")
        assert result["ok"], result
        press = result[f"native_guest_{kind}_fixture"]
        async with asyncio.timeout(12):
            while True:
                for row in await resident_events(guest_run, frame):
                    if row.get("sequence") == press["sequence"] and row["event"] in (event, event + "_rejected"):
                        assert row["event"] == event and row["native_guest_weapon"]["guid"] == guid, row
                        return row["native_guest_weapon"]
                await asyncio.sleep(.08)

    try:
        seats = host, guest
        assert await asyncio.gather(*(command(seat, "arm-alternate") for seat in seats)) == ["armed", "armed"]
        host_reserve = await command(host, "reserve")
        guid = await command(host, "seed-peer-empty")
        assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
        await read(guest, "projected", guid)
        assert await command(guest, "equip") == "selected"
        await read(host, "peer-round", FORTY_FIVE_BALL.item_path)
        await read(guest, "round", FORTY_FIVE_BALL.item_path)
        frame = await wait_resident_status(guest_run, lambda view: view["state"] == "connected"
            and view.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        reserve = await command(host, "seed-reserve")
        assert re.fullmatch(r"[0-9A-F]{32},18", reserve), reserve
        await read(guest, "reserve", reserve)
        reloaded = await stock_input("reload", frame, guid)
        assert (reloaded["before"], reloaded["after"], reloaded["reserve_before"], reloaded["reserve_after"]) == (
            0, 8, 18, 10), reloaded
        await asyncio.gather(read(host, "peer-round", alternate), read(guest, "round", alternate))
        await asyncio.gather(read(host, "peer-ready", f"{guid},8,8"), read(guest, "ready", f"{guid},8,8"))

        assert await command(guest, "face-away") == "turned"
        shot = await stock_input("primary", frame, guid)
        assert (shot["before"], shot["after"], shot["projectiles"]) == (8, 7, 1), shot
        await asyncio.gather(read(host, "peer-ready", f"{guid},7,7"), read(guest, "ready", f"{guid},7,7"))

        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await wait_resident_status(guest_run, lambda view: view.get("game", {}).get(
            "native_inventory_view", {}).get("state") == "stock")
        pressed = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=f"{guid}|take-ammo")
        assert pressed["ok"] and pressed["stock_action"]["guid"] == guid, pressed
        unloaded = None
        async with asyncio.timeout(12):
            while unloaded is None:
                for entry in await resident_events(guest_run, frame):
                    result = entry.get(native_inventory.TAKE_AMMO_KEY, {})
                    if result.get("source_guid") == guid:
                        unloaded = result
                        break
                await asyncio.sleep(.08)
        assert (unloaded["class_path"], unloaded["split"], unloaded["remaining"]) == (alternate, 7, 0), unloaded
        await asyncio.gather(read(host, "peer-ready", f"{guid},0,0"), read(guest, "ready", f"{guid},0,0"))
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        assert await command(host, "reserve") == host_reserve and campaign_save_hashes(host.campaign) == checkpoint
        return {"stock_alternate_ammo": {"pawn": pawn, "class_path": alternate,
                                         "reload": reloaded, "shot": shot, "take_ammo": unloaded}}
    finally:
        await asyncio.gather(command(host, "disarm"), command(guest, "disarm"), return_exceptions=True)
