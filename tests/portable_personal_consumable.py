"""Verify an unlisted consumable through the normal stock inventory and reconnect."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_guest_consumable
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid


BLUE_MUSHROOM = "/Game/Classes/Items/Item_BlueMushroom.Item_BlueMushroom_C"
FALLBACK_RECEIPT = "native_guest_personal_consumable"


async def verify_stock_personal_consumable(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    saves = campaign_save_hashes(host.campaign)
    assert BLUE_MUSHROOM not in native_guest_consumable.COMMANDS

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
    blue, mre = (await command(host, "seed-personal-compatibility")).split(";")
    assert blue != mre and all(map(valid_guid, (blue, mre)))
    baseline, native_guest = (await command(host, "host-medical-vitals")).split(";")
    assert float(native_guest.split(",")[1]) == 110
    items = await inventory(guest_run)
    assert (items[blue]["class_path"], items[blue]["source"], items[blue]["quantity"]) == (BLUE_MUSHROOM, "backpack", 2)
    assert items[mre]["class_path"] == native_guest_consumable.MRE_CLASS and items[mre]["quantity"] == 1

    frame = await wait_resident_status(guest_run, lambda row: row.get("gameplay_fresh") and row.get("character_id"))
    async with asyncio.timeout(10):
        while not any(event["event"] == "native_guest_ui_projected" and event["time_unix"] >= seeded_at
                      and event["items"] == len(items) for event in await resident_events(guest_run, frame)):
            await asyncio.sleep(.08)
    await action(guest_run, "native-inventory-view-fixture")
    await wait_resident_status(guest_run, lambda row: row.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")

    async def use(guid, result_key):
        started = time.time()
        pressed = await action(guest_run, "native-inventory-stock-use-click-fixture", target=guid)
        assert pressed["stock_action"]["guid"] == guid, pressed
        async with asyncio.timeout(14):
            while True:
                events = [item for item in await resident_events(guest_run, frame) if item["time_unix"] >= started]
                rejected = [item for item in events if item["event"] == "native_stock_inventory_rejected"]
                assert not rejected, rejected
                matching = [item for item in events if item["event"] == "native_stock_inventory_action"
                            and item.get(result_key, {}).get("guid") == guid]
                if matching:
                    result, = matching
                    return result[result_key]
                await asyncio.sleep(.10)

    async def projected_vitals(food, regen):
        async with asyncio.timeout(10):
            while True:
                current_food, current_regen = map(float, (await command(guest, "guest-personal-vitals")).split(","))
                if abs(current_food - food) <= .6 and math.isclose(current_regen, regen, abs_tol=.02):
                    return current_food, current_regen
                await asyncio.sleep(.08)

    _, _, food_before, _ = map(float, (await command(host, "host-read")).split(","))
    fallback = await use(blue, FALLBACK_RECEIPT)
    assert (fallback["guid"], fallback["class_path"], fallback["quantity_before"], fallback["quantity_after"]) == (
        blue, BLUE_MUSHROOM, 2, 1), fallback
    food, regen = fallback["vitals"]["food"], fallback["vitals"]["regen"]
    assert math.isclose(food - food_before, 5, abs_tol=1) and math.isclose(regen, 110, abs_tol=.02), fallback
    canonical, detached = (await command(host, "host-medical-vitals")).split(";")
    assert canonical == baseline and math.isclose(float(detached.split(",")[1]), regen, abs_tol=.02), (canonical, detached)
    _, _, current_food, _ = map(float, (await command(host, "host-read")).split(","))
    assert abs(current_food - food) < .6, (current_food, food)
    await projected_vitals(food, regen)
    after = await inventory(guest_run)
    assert after[blue]["quantity"] == 1 and after[mre]["quantity"] == 1, after
    assert await inventory(host_run) == starting_host
    assert campaign_save_hashes(host.campaign) == saves, "Fallback item use wrote an early checkpoint"

    registered = await use(mre, native_guest_consumable.RESULT[native_guest_consumable.MRE])
    assert registered["guid"] == mre and registered["quantity_after"] == 0, registered
    food, regen = registered["food_after"], registered["regen_after"]
    assert regen > 110
    await projected_vitals(food, regen)
    after = await inventory(guest_run)
    assert mre not in after and after[blue]["quantity"] == 1
    assert await inventory(host_run) == starting_host
    assert campaign_save_hashes(host.campaign) == saves, "Normal item use wrote an early checkpoint"
    await action(guest_run, "native-inventory-view-fixture")
    await wait_resident_status(guest_run, lambda row: row.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"stock_personal_consumable": {
        "pawn": pawn, "character_id": frame["character_id"], "guid": blue, "remaining": 1, "food": food, "regen": regen,
        "fallback": fallback, "ordinary_recipe": registered, "original_host_state_unchanged": True,
        "deferred_save": True,
    }}


async def verify_personal_consumable_reconnect(guest, proof, probe):
    run = guest.client / "run"
    await wait_resident_status(run, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh")
                               and row.get("character_id") == proof["character_id"])
    page = await submit(run, "native-inventory-list")
    assert page["ok"], page
    item, = [row for row in page["native_inventory"]["items"] if row["guid"] == proof["guid"]]
    assert item["quantity"] == proof["remaining"] and item["class_path"] == BLUE_MUSHROOM, item
    async with asyncio.timeout(8):
        while True:
            sample = await probe(guest.bridge, "portable-guest-food", "guest-personal-vitals")
            food, regen = map(float, sample.split(","))
            if abs(food - proof["food"]) <= 1.5 and math.isclose(regen, proof["regen"], abs_tol=.02):
                break
            await asyncio.sleep(.08)
    proof["verified_after_reconnect"] = True
