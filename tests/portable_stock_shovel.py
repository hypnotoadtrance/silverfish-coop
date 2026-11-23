"""Verify a guest collects a real shovel and produces one stock hole on both peers."""

import asyncio
import math

from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_guest_weapon, native_host_dig_live as dig, native_loose_pickup as loose
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS

SPADE = CROSSROADS + ".CrossRoads_P:PersistentLevel.MeleeWep_Spade_C_3"


async def verify_stock_shovel(host, guest, pawn):
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

    assert await observe(host, "holes") == await observe(guest, "holes") == "none"
    staged = tuple(map(float, (await observe(host, "aim", pawn, "find")).split(",")))
    await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("pawns", {}).get("local", {}).get("location")
                                     and math.dist(f["game"]["pawns"]["local"]["location"], staged) <= 40, timeout=8)
    contacts = await asyncio.gather(observe(host, "aim", pawn), observe(guest, "aim"))
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
            holes = await asyncio.gather(observe(host, "holes"), observe(guest, "holes"))
            if holes[0] != "none" and holes[1] != "none":
                break
            await asyncio.sleep(.1)
    host_hole, guest_hole = (row.split(",") for row in holes)
    assert host_hole[0] == "3" and guest_hole[0] == "1", holes
    assert math.dist(tuple(map(float, host_hole[1:])), tuple(map(float, guest_hole[1:]))) < 5, holes
    return {"stock_shovel": {"guid": item["guid"], "holes": holes, "contact": contacts,
                             "dispatch": dispatch[dig.GUEST_RESULT]}}
