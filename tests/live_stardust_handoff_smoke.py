"""Exercise a stock guest Stardust drop, host pickup, and party travel."""

import argparse
import asyncio
import re
from pathlib import Path

from live_multi_smoke import campaign_save_hashes
from live_portable_title_smoke import verify
from portable_guest_world_start import resident_events, wait_resident_status
from portable_personal_consumable import stock_inventory_open
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_guest_drop, native_world_loot
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, EXCHANGE, OBSERVATORY


async def verify_handoff(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def ready(run, map_path, role):
        return await wait_resident_status(run, lambda row:
            row["game"].get("map") == map_path and row.get("state") == role
            and not row.get("awaiting_zone_guest")
            and (role == "connected" or row["game"].get("native_guest_save", {}).get("state") == "bound"))

    async def inventory(run, guid):
        response = await submit(run, "native-inventory-list")
        assert response["ok"], response
        return [row for row in response["native_inventory"]["items"] if row["guid"] == guid]

    async def stock_action(seat, action, *args):
        result = await seat.bridge.command(f"fixture-stock-stardust-{action}", *args)
        return result["ack"]["message"]

    async def event(run, frame, name, matches):
        async with asyncio.timeout(25):
            while True:
                for row in await resident_events(run, frame):
                    if row.get("event") == name and matches(row):
                        return row
                await asyncio.sleep(.12)

    await enter_crossroads(host, pawn)
    frame = await ready(host_run, CROSSROADS, "hosting")
    guest_frame = await ready(guest_run, CROSSROADS, "connected")
    before = campaign_save_hashes(host.campaign)
    guid = await stock_action(host, "seed", frame["game"]["native_guest_save"]["pawn"])
    assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
    seeded, = await inventory(guest_run, guid)
    assert seeded["quantity"] == 2, seeded
    async with asyncio.timeout(10):
        while await stock_action(guest, "projected", guid) != "present":
            await asyncio.sleep(.2)
    await stock_inventory_open(guest_run, True)
    print(f"Stardust stack seeded and projected {guid}", flush=True)
    guest_use = await stock_action(guest, "use", guid)
    assert guest_use == "pressed", guest_use
    refused = await guest.bridge.wait_for(lambda status:
        status.get("native_transport", {}).get("reason") == "host_activation_required", timeout=5)
    assert (await inventory(guest_run, guid))[0]["quantity"] == 2
    assert await stock_action(guest, "drop", guid) == "pressed"
    dropped = (await event(guest_run, guest_frame, "native_stock_inventory_action", lambda row:
        row.get(native_guest_drop.RESULT, {}).get("guid") == guid))[native_guest_drop.RESULT]
    await stock_inventory_open(guest_run, False)
    identity = native_world_loot.player_drop_identity(dropped["drop_id"])
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda row: identity in row["game"].get(native_world_loot.KEY, {}))
    await host.bridge.command(native_world_loot.APPROACH, identity, frame["game"]["pawns"]["local"]["name"])
    await host.bridge.command(native_world_loot.FIXTURE, identity)
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda row: identity not in row["game"].get(native_world_loot.KEY, {}))
    assert await inventory(guest_run, guid) == []
    recovered, = await inventory(host_run, guid)
    assert (recovered["source"], recovered["quantity"]) == ("backpack", 2), recovered
    assert campaign_save_hashes(host.campaign) == before
    route = (await host.bridge.command("native-blue-route", frame["game"]["pawns"]["local"]["name"]))["native_blue_route"]
    assert (route["destination"], route["bed"]) == (OBSERVATORY, ""), route
    print(f"Host recovered guest Stardust {recovered}", flush=True)
    assert await stock_action(host, "use", guid) == "pressed"
    try:
        used = await event(host_run, frame, "native_stardust_request", lambda row: row.get("guid") == guid)
    except TimeoutError:
        raise RuntimeError(f"Host Use did not request a trip. {host.bridge.snapshot().get('native_transport')}") from None
    assert used["result"]["ok"] and used["result"]["map"] == EXCHANGE, used
    effects = await event(host_run, frame, "native_dream_effects_committed", lambda row: row.get("guid") == guid)
    assert effects["after"] - effects["before"] == 10, effects
    assert not (host.bridge.directory / "native-stardust-events").exists()
    for run, role in ((host_run, "hosting"), (guest_run, "connected")):
        await ready(run, EXCHANGE, role)
    remaining, = await inventory(host_run, guid)
    assert remaining["quantity"] == 1, remaining
    await host.bridge.wait_for(lambda row: row.get("native_dream_isolation", {}).get("state") == "ready")
    await host.bridge.command("native-dream-power-fixture")
    for run, role in ((host_run, "hosting"), (guest_run, "connected")):
        await ready(run, OBSERVATORY, role)
    return {"stock_stardust_handoff": {"guest_denial": refused["native_transport"], "drop": dropped,
                                      "host_pickup": recovered, "travel": used, "remainder": remaining,
                                      "effects": effects, "blue_route": route, "return_map": OBSERVATORY}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                       args.vanilla_saved.resolve(), stardust_handoff_check=verify_handoff))
