"""Trace a guest stock camera shutter to the authored Bronze Quarry objective."""

import asyncio
import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_inventory, native_turret_photo
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, OBSERVATORY

OVERWORLD = "/Game/Classes/Maps/Maps_OverWorld/"
QUARRY = OVERWORLD + "Quarry_01_P"
QUEST = "Photo_BrnzQuarry"


async def verify_quarry_photo(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def command(seat, action):
        return await probe(seat.bridge, "portable-quarry-photo", action)

    guid = await command(host, "seed")
    assert valid_guid(guid), guid
    moved = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=guid)
    assert moved["ok"], moved
    async with asyncio.timeout(8):
        while await command(guest, "equip\t" + guid) != "equipped":
            await asyncio.sleep(.15)

    maps = (OBSERVATORY, CROSSROADS, DEAD_BRIDGE, OVERWORLD + "StoneMaze_P", OVERWORLD + "PineRidge_P", QUARRY)
    for source, destination in zip(maps, maps[1:]):
        route, = (row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination))
        await wait_resident_status(host_run, lambda frame: frame["game"]["map"] == source
                                   and frame["game"].get("native_zone", {}).get("state") == "ready")
        assert route.kind == "overlap", route
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, route.entrance, route.actor)
        arrival = await wait_resident_status(host_run, lambda frame: frame["game"]["map"] == destination
                                             and not frame.get("awaiting_zone_guest")
                                             and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        pawn = arrival["game"]["native_guest_save"]["pawn"]
        await wait_resident_status(guest_run, lambda frame: frame.get("zone_epoch") == arrival["zone_epoch"]
                                   and frame.get("game", {}).get("map") == destination, timeout=35)

    assert await command(host, "journal") == "1"
    checkpoint = campaign_save_hashes(host.campaign)
    point = tuple(map(float, (await command(host, "position")).split(",")))
    await wait_resident_status(guest_run, lambda frame: (
        (position := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
        and math.dist(position, point) < 30))
    frame = await wait_resident_status(guest_run, lambda row: (
        row.get("gameplay", {}).get("native_equipped_weapon", {}).get("quest") == native_turret_photo.STOCK_SCOPE
        and row["game"].get("native_guest_primary_scope", {}).get("guid") == guid))

    async def shutter():
        press = await submit(guest_run, "native-guest-primary-input-fixture")
        assert press["ok"], press
        seq = press["native_guest_primary_fixture"]["sequence"]
        async with asyncio.timeout(8):
            while True:
                for row in await resident_events(guest_run, frame):
                    if row.get("sequence") == seq and row["event"] in ("native_guest_camera_shutter", "native_guest_camera_shutter_rejected"):
                        return row
                await asyncio.sleep(.1)

    await command(guest, "miss")
    await asyncio.sleep(.35)
    missed = await shutter()
    assert missed.get("reason") == "stock_photo_no_objective_in_frame", missed
    assert await command(host, "journal") == "1"
    await command(guest, "aim")
    await asyncio.sleep(3.1)
    capture = await shutter()
    photo = capture[native_turret_photo.STOCK_PROOF]
    assert (photo["map"], photo["quest"], photo["camera"], photo["step_before"], photo["step_after"]) == (
        QUARRY, QUEST, guid, 1, 2), photo
    assert photo["target"].endswith("StaticMeshActor_1147") and photo["visibility"] >= .75, photo
    async with asyncio.timeout(10):
        while await asyncio.gather(command(host, "journal"), command(guest, "journal")) != ["2", "2"]:
            await asyncio.sleep(.2)
    replay = await submit(guest_run, native_turret_photo.STOCK_SHUTTER, target=f"{guid}@{frame['zone_epoch']}",
                          operation_id=capture["operation_id"])
    assert replay["ok"] and replay[native_turret_photo.STOCK_PROOF] == photo, replay
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_quarry_photo": {"miss_refused": missed["reason"], "capture": photo, "same_journal_step": 2,
                                   "replay_preserved_progress": True, "checkpoint_unchanged_after_travel": True}}
