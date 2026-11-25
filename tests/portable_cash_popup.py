"""Verify a committed pickup reaches both seats' stock cash widget."""

import asyncio

from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish.travel import CROSSROADS


def cash_pickup_overlay(game, overlay):
    gate = 'if phase ~= "hosting" or not fixtures or not host_fixtures then\n            error("The stock loose pickup positioning fixture is unavailable", 0)'

    def enable(script):
        assert script.count(gate) == 1
        return script.replace(gate, gate.replace("not host_fixtures", "not (host_fixtures or title_fixtures)"))

    return overlay(game, "native_authored_pickup.lua", enable)


async def verify_stock_cash_popup(host, guest, pawn, command, save_hashes):
    source = "crossroads-small-stack-1"
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    start = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
        and frame["game"].get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == "available")
    amount = start["game"][pickup.SOURCES_KEY][source]["wallet_value"]
    balance = start["game"]["native_wallet"]
    assert pickup.valid_cash_amount(source, amount)
    checkpoint = save_hashes(host.campaign)

    async def notice(seat, request):
        return (await command(seat.bridge, "portable-cash-popup", request)).strip()

    assert await asyncio.gather(*(notice(seat, "arm") for seat in (host, guest))) == ["armed", "armed"]
    await host.bridge.command(pickup.APPROACH, pawn, source, timeout=6)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
        and frame["game"].get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == "available")
    pressed = await guest.bridge.command(pickup.STOCK_FIXTURE, source, timeout=6)
    assert pressed["native_authored_pickup_interaction"]["source"] == source, pressed.get("ack")
    settled = await wait_resident_status(host_run, lambda frame: frame.get("gameplay", {})
        .get(pickup.STATE_KEY, {}).get(source, {}).get("wallet_credit", {}).get("after") == balance + amount)
    await wait_resident_status(guest_run, lambda frame: frame.get("gameplay", {}).get("shared_wallet") == balance + amount
        and frame.get("game", {}).get("native_wallet") == balance + amount)

    expected = f"RECEIVED ${amount}!"
    readings = {}
    async with asyncio.timeout(8):
        while True:
            readings = dict(zip(("host", "guest"), await asyncio.gather(*(notice(seat, "read") for seat in (host, guest)))))
            if set(readings.values()) == {expected}:
                break
            await asyncio.sleep(.08)
    await asyncio.sleep(.8)
    assert await asyncio.gather(*(notice(seat, "read") for seat in (host, guest))) == [expected, expected]
    notices = [event for event in await resident_events(guest_run, guest_frame)
               if event.get("event") == "native_cash_popup_queued"]
    committed = [event for event in await resident_events(host_run, start)
                 if event.get("event") == "native_authored_pickup_committed" and event.get("source") == source]
    event, = committed
    receipt, = [notice for notice in notices if notice.get("operation_id") == event["operation_id"]]
    assert receipt.get("amount") == amount
    assert save_hashes(host.campaign) == checkpoint
    return {"stock_cash_popup": {"source": source, "amount": amount, "before": balance,
            "after": balance + amount, "views": readings, "campaign_checkpoint_unchanged": True,
            "credit": settled["gameplay"][pickup.STATE_KEY][source]["wallet_credit"]}}
