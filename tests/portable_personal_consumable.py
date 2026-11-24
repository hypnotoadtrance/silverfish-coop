"""Verify an unlisted consumable through the normal stock inventory and reconnect."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_guest_consumable
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid


LIVE_WETA = "/Game/Classes/Items/Item_LiveWeta.Item_LiveWeta_C"
FALLBACK_RECEIPT = "native_guest_personal_consumable"


async def stock_inventory_open(run, opened):
    def view(frame):
        return frame.get("game", {}).get("native_inventory_view", {}).get("state", "closed")

    desired = "stock" if opened else "closed"
    current = await wait_resident_status(run, lambda frame: view(frame) in {"stock", "closed"})
    if view(current) != desired:
        changed = await submit(run, "native-inventory-view-fixture")
        assert changed["ok"], changed
        await wait_resident_status(run, lambda frame: view(frame) == desired)


async def use_stock_item(run, frame, guid, result_key, *, drag=False):
    await stock_inventory_open(run, True)
    target = f"{guid}|drag" if drag else guid
    started = time.time()
    clicked = await submit(run, "native-inventory-stock-use-click-fixture", target=target)
    assert clicked["ok"] and clicked["stock_action"]["guid"] == guid, (guid, result_key, drag, clicked)
    async with asyncio.timeout(14):
        while True:
            events = [item for item in await resident_events(run, frame) if item["time_unix"] >= started]
            rejected = [item for item in events if item["event"] == "native_stock_inventory_rejected"]
            assert not rejected, rejected
            matching = [item for item in events if item["event"] == "native_stock_inventory_action"
                        and item.get(result_key, {}).get("guid") == guid]
            if matching:
                result, = matching
                return result[result_key]
            await asyncio.sleep(.10)


async def verify_stock_personal_consumable(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    saves = campaign_save_hashes(host.campaign)
    assert LIVE_WETA not in native_guest_consumable.COMMANDS

    async def command(seat, action):
        return await probe(seat.bridge, "portable-guest-food", action)

    async def action(run, verb, **fields):
        reply = await submit(run, verb, **fields)
        assert reply["ok"], (verb, reply)
        return reply

    async def inventory(run):
        page = (await action(run, "native-inventory-list"))["native_inventory"]
        assert page["next_offset"] is None, page
        return {row["guid"]: row for row in page["items"]}

    starting_host = await inventory(host_run)
    seeded_at = time.time()
    weta, spare, mre = (await command(host, "seed-personal-compatibility")).split(";")
    assert len({weta, spare, mre}) == 3 and all(map(valid_guid, (weta, spare, mre)))
    baseline, native_guest = (await command(host, "host-medical-vitals")).split(";")
    assert float(native_guest.split(",")[1]) == 110
    items = await inventory(guest_run)
    assert all((items[guid]["class_path"], items[guid]["source"], items[guid]["quantity"]) == (LIVE_WETA, "backpack", 1)
               for guid in (weta, spare))
    assert items[mre]["class_path"] == native_guest_consumable.MRE_CLASS and items[mre]["quantity"] == 1

    frame = await wait_resident_status(guest_run, lambda row: row.get("gameplay_fresh") and row.get("character_id"))
    async with asyncio.timeout(10):
        while not any(event["event"] == "native_guest_ui_projected" and event["time_unix"] >= seeded_at
                      and event["items"] == len(items) for event in await resident_events(guest_run, frame)):
            await asyncio.sleep(.08)

    async def projected_vitals(food, regen, fatigue, intox):
        async with asyncio.timeout(10):
            while True:
                current_food, current_regen, tired, saved_intox, pawn_intox = map(
                    float, (await command(guest, "guest-personal-vitals")).split(","))
                if (abs(current_food - food) <= .6 and math.isclose(current_regen, regen, abs_tol=.02)
                        and tired == fatigue and saved_intox == pawn_intox == intox):
                    return
                await asyncio.sleep(.08)

    registered = await use_stock_item(guest_run, frame, mre, native_guest_consumable.RESULT[native_guest_consumable.MRE])
    assert registered["guid"] == mre and registered["quantity_after"] == 0, registered
    expected_regen = registered["regen_after"]
    assert expected_regen > 110
    canonical_flags, guest_flags = (await command(host, "host-personal-flags")).split(";")
    fatigue_before, intox_before = map(int, guest_flags.split(","))
    await projected_vitals(registered["food_after"], expected_regen, fatigue_before, intox_before)
    assert campaign_save_hashes(host.campaign) == saves, "Normal item use wrote an early checkpoint"

    _, _, food_before, _ = map(float, (await command(host, "host-read")).split(","))
    assert await command(guest, "modded-weta") == "patched"
    try:
        fallback = await use_stock_item(guest_run, frame, weta, FALLBACK_RECEIPT)
    finally:
        fixture = await command(guest, "restore-weta")
    assert fixture == "restored:1", fixture
    assert (fallback["guid"], fallback["class_path"], fallback["quantity_before"], fallback["quantity_after"]) == (
        weta, LIVE_WETA, 1, 0), fallback
    food, regen = fallback["vitals"]["food"], fallback["vitals"]["regen"]
    fatigue, intox = fallback["vitals"]["fatigue"], fallback["vitals"]["intox"]
    assert math.isclose(food - food_before, 7, abs_tol=1) and math.isclose(regen, expected_regen, abs_tol=.02), fallback
    assert (fatigue, intox) == (fatigue_before + 5, intox_before - 2), fallback
    canonical, detached = (await command(host, "host-medical-vitals")).split(";")
    assert canonical == baseline and math.isclose(float(detached.split(",")[1]), regen, abs_tol=.02), (canonical, detached)
    _, _, current_food, _ = map(float, (await command(host, "host-read")).split(","))
    assert abs(current_food - food) < .6, (current_food, food)
    assert (await command(host, "host-personal-flags")).split(";") == [canonical_flags, f"{fatigue},{intox}"]
    await projected_vitals(food, regen, fatigue, intox)
    after = await inventory(guest_run)
    assert weta not in after and mre not in after and after[spare]["quantity"] == 1, after
    assert await inventory(host_run) == starting_host
    assert campaign_save_hashes(host.campaign) == saves, "Fallback item use wrote an early checkpoint"
    await stock_inventory_open(guest_run, False)
    return {"stock_personal_consumable": {
        "pawn": pawn, "character_id": frame["character_id"], "guid": spare, "remaining": 1, "food": food, "regen": regen,
        "fatigue": fatigue, "intox": intox,
        "fallback": fallback, "ordinary_recipe": registered, "original_host_state_unchanged": True,
        "deferred_save": True,
    }}


async def verify_personal_consumable_reconnect(host, guest, proof, probe):
    run = guest.client / "run"
    frame = await wait_resident_status(run, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh")
                                       and row.get("character_id") == proof["character_id"])
    page = await submit(run, "native-inventory-list")
    assert page["ok"], page
    item, = [row for row in page["native_inventory"]["items"] if row["guid"] == proof["guid"]]
    assert item["quantity"] == proof["remaining"] and item["class_path"] == LIVE_WETA, item
    async with asyncio.timeout(8):
        while True:
            sample = await probe(guest.bridge, "portable-guest-food", "guest-personal-vitals")
            food, regen, fatigue, intox, pawn_intox = map(float, sample.split(","))
            if (abs(food - proof["food"]) <= 1.5 and math.isclose(regen, proof["regen"], abs_tol=.02)
                    and fatigue == proof["fatigue"] and intox == pawn_intox == proof["intox"]):
                break
            await asyncio.sleep(.08)
    proof["verified_after_reconnect"] = True
    saves = campaign_save_hashes(host.campaign)
    dragged = await use_stock_item(run, frame, proof["guid"], FALLBACK_RECEIPT, drag=True)
    assert (dragged["guid"], dragged["class_path"], dragged["quantity_before"], dragged["quantity_after"]) == (
        proof["guid"], LIVE_WETA, 1, 0), dragged
    assert math.isclose(dragged["vitals"]["food"] - food, 7, abs_tol=1), dragged
    remaining = await submit(run, "native-inventory-list")
    assert remaining["ok"] and all(row["guid"] != proof["guid"] for row in remaining["native_inventory"]["items"]), remaining
    assert campaign_save_hashes(host.campaign) == saves, "Stock drag caused an early checkpoint"
    await stock_inventory_open(run, False)
    proof["stock_drag_after_reconnect"] = dragged
