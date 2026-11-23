"""Verify a guest collects a real shovel and produces one stock hole on both peers."""

import asyncio
import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import (
    native_guest_weapon,
    native_host_dig_live as dig,
    native_loose_pickup as loose,
    native_stock_enemy_drops,
    native_world_loot,
)
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS

SPADE = CROSSROADS + ".CrossRoads_P:PersistentLevel.MeleeWep_Spade_C_3"
NUGGET = "/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C"


async def verify_stock_shovel(host, guest, pawn, *, loot=False):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    await wait_resident_status(host_run, lambda f: f.get("game", {}).get("native_zone", {}).get("state") == "ready")
    for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
        await host.bridge.command("native-exit-position", participant, placement, "Obsv_Etr", "Level_Exit_BP_C_1")
    arrived = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("map") == CROSSROADS
                                        and not f.get("awaiting_zone_guest")
                                        and f["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
    await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("map") == CROSSROADS
                                     and f.get("state") == "connected", timeout=35)
    pawn = arrived["game"]["native_guest_save"]["pawn"]
    await host.bridge.command(loose.APPROACH, SPADE, pawn)
    target = f"loose:{SPADE}"
    tapped = await submit(guest_run, loose.FIXTURE, target=target)
    assert tapped["ok"], tapped
    await wait_resident_status(guest_run, lambda f: SPADE in f.get("gameplay", {}).get(loose.STATE, []), timeout=12)
    inventory = await submit(guest_run, "native-inventory-list")
    assert inventory["ok"], inventory
    item, = [entry for entry in inventory["native_inventory"]["items"] if entry["class_path"] == native_guest_weapon.SPADE_PATH]
    equipped = await submit(guest_run, "native-guest-weapon-equip", target=item["guid"])
    assert equipped["ok"] and equipped["native_guest_weapon"]["kind"] == "spade", equipped
    current = await wait_resident_status(
        guest_run, lambda frame: frame.get("game", {}).get("native_guest_primary_scope", {}).get("kind") == "spade",
        timeout=10,
    )

    async def observe(seat, action, *args):
        report = await seat.bridge.command("fixture-spade-" + action, *args, update_status=False)
        return report["ack"]["message"]

    async def dig_once(stock_choice=None, *, advance=False):
        previous = await asyncio.gather(observe(host, "holes"), observe(guest, "holes"))
        previous = [set(text.split(";")) - {"none"} for text in previous]
        staged = tuple(map(float, (await observe(host, "aim", pawn, "next" if advance else "find")).split(",")))
        await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("pawns", {}).get("local", {}).get("location")
                                         and math.dist(f["game"]["pawns"]["local"]["location"], staged) <= 40, timeout=8)
        contacts = await asyncio.gather(observe(host, "aim", pawn), observe(guest, "aim"))
        if stock_choice:
            await observe(host, "loot", stock_choice)
        tap = await submit(guest_run, "native-guest-primary-input-fixture")
        assert tap["ok"], tap
        sequence = tap["native_guest_primary_fixture"]["sequence"]
        async with asyncio.timeout(8):
            while True:
                events = [row for row in await resident_events(guest_run, current)
                          if row.get("sequence") == sequence and row["event"].startswith("native_guest_dig_input")]
                if events:
                    break
                await asyncio.sleep(.08)
        dispatch, = events
        assert dispatch["event"] == "native_guest_dig_input", dispatch
        async with asyncio.timeout(10):
            while True:
                observed = await asyncio.gather(observe(host, "holes"), observe(guest, "holes"))
                additions = [set(text.split(";")) - base - {"none"} for text, base in zip(observed, previous)]
                if all(additions):
                    break
                await asyncio.sleep(.1)
        (host_hole,), (guest_hole,) = additions
        assert host_hole.startswith("3,") and guest_hole.startswith("1,"), additions
        assert math.dist(tuple(map(float, host_hole.split(",")[1:])), tuple(map(float, guest_hole.split(",")[1:]))) < 5
        return {"guid": item["guid"], "holes": [host_hole, guest_hole], "contact": contacts, "dispatch": dispatch[dig.GUEST_RESULT]}

    if not loot:
        return {"stock_shovel": await dig_once()}

    checkpoint = campaign_save_hashes(host.campaign)
    host_status = await wait_resident_status(host_run, lambda f: f.get("state") == "hosting")
    assert host_status["game"][native_stock_enemy_drops.MEMORY_CAPABILITY]

    def nuggets(frame):
        return [entry for entry in frame.get("game", {}).get(native_stock_enemy_drops.KEY, {}).values()
                if entry["source"].startswith("DigHole_BP_C ") and entry["class_path"] == NUGGET]

    dig_result = await dig_once("item")
    found = await wait_resident_status(host_run, nuggets, timeout=8)
    nugget, = nuggets(found)
    async with asyncio.timeout(12):
        while True:
            rewards = [row for row in await resident_events(host_run, found)
                       if row["event"] == "native_stock_enemy_loot_banked" and row.get("actor") == nugget["actor"]]
            if rewards:
                break
            await asyncio.sleep(.1)
    reward, = rewards
    assert reward["revision"] is None and checkpoint == campaign_save_hashes(host.campaign), reward
    identity = native_world_loot.stock_enemy_identity(reward["guid"], reward.get("visual_id"))

    def projected(status):
        return status.get("game", {}).get(native_world_loot.KEY, {}).get(identity)

    for run in (host_run, guest_run):
        await wait_resident_status(run, projected, timeout=12)
    await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    arrived = await wait_resident_status(guest_run, lambda f: math.dist(f["game"]["pawns"]["local"]["location"],
                                                                        reward["transform"]["position"]) < 180, timeout=7)
    guest_actor = projected(arrived)["actor"].split(" ", 1)[1]
    press = await submit(guest_run, native_world_loot.FIXTURE, target=identity)
    if not press["ok"]:
        sight = await observe(guest, "sight", guest_actor)
        raise AssertionError((press, sight))
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda f: not projected(f), timeout=12)
    after = await submit(guest_run, "native-inventory-list")
    assert after["ok"] and any(entry["guid"] == reward["guid"] for entry in after["native_inventory"]["items"]), after
    rival = await submit(host_run, native_stock_enemy_drops.CLAIM, target=reward["guid"])
    assert rival.get("error") == "stock_enemy_loot_already_claimed", rival
    assert checkpoint == campaign_save_hashes(host.campaign), "Dig or pickup wrote a campaign checkpoint"

    def parse_props(sample):
        entries = (entry.split(",") for entry in sample.split(";") if entry)
        return [(role, name, moving, tuple(map(float, position))) for role, name, moving, *position in entries]

    prior_props = {name for _, name, _, _ in parse_props(await observe(host, "props"))}
    await dig_once("prop", advance=True)
    async with asyncio.timeout(8):
        while True:
            host_props = [row for row in parse_props(await observe(host, "props")) if row[1] not in prior_props]
            if host_props:
                break
            await asyncio.sleep(.15)
    (role, prop, _, _), = host_props
    assert role == "3", host_props
    guest_props = []
    try:
        async with asyncio.timeout(8):
            while True:
                host_props = parse_props(await observe(host, "props"))
                guest_props = parse_props(await observe(guest, "props"))
                source, = [row for row in host_props if row[1] == prop]
                replicas = [row for row in guest_props if row[0] == "1" and math.dist(row[3], source[3]) < 45]
                if source[2] == "true" and replicas:
                    break
                await asyncio.sleep(.15)
    except TimeoutError as error:
        raise AssertionError(f"The stock can did not replicate its physics pose. Host {host_props}. Guest {guest_props}.") from error
    replica, = replicas
    return {"stock_shovel": {**dig_result, "unearthed_item": reward, "unearthed_prop": [source, replica],
                             "duplicate_claim": rival["error"]}}
