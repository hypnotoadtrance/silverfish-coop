"""Verify an observer masks a stock pistol mesh added after hand projection."""

import asyncio
import time


def observer_report(response):
    stock, stock_frame, hand_frame, stock_owner_only, late_part, late_owner_only = map(int, response.split(";"))
    return dict(stock=stock, stock_frame=stock_frame, hand_frame=hand_frame, stock_owner_only=stock_owner_only,
                late_part=late_part, late_owner_only=late_owner_only)


def owner_report(response):
    stock, frame, owner_only, hidden, visible, mesh_count = map(int, response.split(";"))
    return dict(stock=stock, frame=frame, owner_only=owner_only, hidden=hidden, visible=visible, mesh_count=mesh_count)


async def verify_stock_held_mask(host, guest, probe):
    async def command(seat, action):
        async with asyncio.timeout(6):
            while (reply := await probe(seat.bridge, "portable-held-mask", action)).strip() == "pending":
                await asyncio.sleep(.05)
        return reply.strip()

    original, owner = await asyncio.gather(command(host, "read"), command(guest, "owner"))
    original, owner = observer_report(original), owner_report(owner)
    assert original["stock_owner_only"] == 1 and original["late_part"] == 0, original
    assert owner["stock"] and owner["frame"] and owner["visible"] == 1 and owner["hidden"] == 0, owner
    held = ("stock", "stock_frame", "hand_frame", "stock_owner_only")

    def original_held(report):
        assert all(report[key] == original[key] for key in held), (original, report)

    try:
        added = observer_report(await command(host, "add"))
        started = time.perf_counter()
        original_held(added)
        assert added["late_part"] and added["late_owner_only"] == 0, added
        async with asyncio.timeout(4):
            while True:
                masked = observer_report(await command(host, "read"))
                original_held(masked)
                assert masked["late_part"] == added["late_part"], (added, masked)
                if masked["late_owner_only"] == 1:
                    break
                await asyncio.sleep(.05)
        mask_seconds = time.perf_counter() - started
        unaffected = owner_report(await command(guest, "owner"))
        assert unaffected == owner, (owner, unaffected)
    finally:
        cleared = observer_report(await command(host, "clear"))

    original_held(cleared)
    assert cleared["late_part"] == 0 and cleared["late_owner_only"] == 0, cleared
    assert owner_report(await command(guest, "owner")) == owner
    return {"stock_held_mask": {"observer_original": original, "observed_late": added, "observer_masked": masked,
                                "guest_owner_unchanged": unaffected, "observer_cleared": cleared,
                                "mask_seconds": round(mask_seconds, 3)}}
