"""Check stock mail opening and its authoritative world drops on a muted pair."""

import argparse
import asyncio
from pathlib import Path
import time

import live_portable_title_smoke as suite
from portable_personal_consumable import stock_inventory_open, use_stock_item
from tools.silverfish import native_guest_consumable as consumable


async def verify_mail_packages(host, guest, pawn):
    host_run = host.campaign / "run"
    guest_run = guest.client / "run"
    saves = suite.campaign_save_hashes(host.campaign)

    async def probe(seat, action):
        return await suite.portable_probe_command(seat.bridge, "portable-guest-food", action)

    async def inventory(run):
        reply = await suite.submit(run, "native-inventory-list")
        assert reply["ok"] and reply["native_inventory"]["next_offset"] is None, reply
        return {row["guid"]: row for row in reply["native_inventory"]["items"]}

    async def props(seat, expected_authority):
        value = await probe(seat, "sample-mail")
        result = {}
        if value != "-":
            for part in value.split(";"):
                kind, name, authority = part.split(",")
                if authority == expected_authority:
                    result[name] = kind
        return result

    host_pack = await inventory(host_run)
    seeded = time.time()
    guids = (await probe(host, "seed-mail")).split(";")
    guest_pack = await inventory(guest_run)
    assert len(guids) == 2 and {guest_pack[guid]["class_path"] for guid in guids} == consumable.MAIL_CLASSES
    frame = await suite.wait_resident_status(guest_run, lambda state: state.get("gameplay_fresh"))
    async with asyncio.timeout(10):
        while not any(row["event"] == "native_guest_ui_projected" and row["time_unix"] >= seeded
                      and row["items"] == len(guest_pack) for row in await suite.resident_events(guest_run, frame)):
            await asyncio.sleep(.08)

    evidence = {}
    key = consumable.RESULT[consumable.MAIL_PACKAGE]
    for guid, drag in zip(guids, (False, True), strict=True):
        host_before, guest_before = await asyncio.gather(props(host, "true"), props(guest, "false"))
        started = time.time()
        receipt = await use_stock_item(guest_run, frame, guid, key, drag=drag)
        assert (receipt["pawn"], receipt["class_path"], receipt["quantity_after"]) == (
            pawn, guest_pack[guid]["class_path"], 0), receipt
        await stock_inventory_open(guest_run, False)
        async with asyncio.timeout(10):
            while True:
                host_after, guest_after = await asyncio.gather(props(host, "true"), props(guest, "false"))
                host_new = sorted(kind for name, kind in host_after.items() if name not in host_before)
                guest_new = sorted(kind for name, kind in guest_after.items() if name not in guest_before)
                if host_new and host_new == guest_new:
                    break
                await asyncio.sleep(.1)

        event, = [row for row in await suite.resident_events(guest_run, frame)
                  if row["time_unix"] >= started and row.get(key) == receipt]
        replay = await suite.submit(guest_run, consumable.MAIL_PACKAGE, target=guid, operation_id=event["operation_id"])
        assert replay["ok"] and replay[key] == receipt, replay
        assert guid not in await inventory(guest_run)
        assert await inventory(host_run) == host_pack
        assert suite.campaign_save_hashes(host.campaign) == saves, "Opening a mail package wrote an early save"
        evidence[guid] = {"gesture": "drag" if drag else "context", "receipt": receipt, "shared_objects": host_new}
    return {"stock_mail_packages": evidence, "deferred_save": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(suite.verify(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                             args.vanilla_saved.resolve(), stock_food=True, mail_packages_check=verify_mail_packages))
