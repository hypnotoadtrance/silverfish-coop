"""Verify local stock crate rolls and exact escrow transfer on a private pair."""

import argparse
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path

import live_portable_title_smoke as suite
from tools.silverfish import native_inventory_grant, native_loot, native_world_loot
from tools.silverfish.travel import OBSERVATORY


def install_fixture(game):
    fixture = Path(__file__).with_name("fixtures").joinpath("portable_guest_crate_transfer.lua").read_text(encoding="utf-8")

    def inject(source):
        entry = "function actions.command(fields, state, phase, status_row)\n"
        if source.count(entry) != 1:
            raise ValueError("The isolated replica lacks the stock crate command")
        return source.replace(entry, entry + fixture, 1)

    return suite.portable_script_overlay(game, "native_loot_actions.lua", inject)


async def verify_transfer(host, guest, _pawn):
    checkpoint = suite.campaign_save_hashes(host.campaign)
    evidence = {}
    for suffix, guaranteed in (("LootCrate_Small_BP_C_1", True), ("LootCabnet_Small_BP_C_1", False)):
        crate, = (name for name, area in native_loot.CRATES.items()
                  if area == OBSERVATORY and name.endswith(":PersistentLevel." + suffix))

        async def command(seat, mode, *fields):
            frame = await seat.bridge.command("fixture-stock-crate-transfer", mode, crate, *fields, update_status=False)
            return frame["ack"]["message"]

        assert await command(host, "observe") == "host crate unopened"
        if guaranteed:
            assert await command(guest, "give-trait") == "permanent"
        count, lucky, source, calls = (await command(guest, "roll")).split("|")
        assert (lucky, source) == ("1", "permanent")
        if guaranteed:
            assert int(count) > 0
        else:
            assert int(calls) > 0
        rows = []
        for index in range(1, int(count) + 1):
            record = await command(guest, "read", str(index))
            guid, item_class, text = record.split("@@fixture-escrow@@")
            assert native_inventory_grant.parse_item("@".join((guid, item_class, text))) == (guid, item_class, text, 0)
            rows.extend((guid, item_class, text))

        if guaranteed:
            try:
                await command(host, "import", *rows[:3], "0" * 32, rows[1], rows[2])
            except RuntimeError as error:
                assert "native_loot_import_payload_unqualified" in str(error), error
            else:
                raise AssertionError("The host imported an invalid row after a valid one")
        assert await command(host, "observe") == "host crate unopened"
        assert await command(host, "import", *rows) == f"host imported {count} typed rows"
        assert await command(host, "observe") == "host crate unopened"
        evidence[crate] = {"typed_item_classes": rows[1::3], "trait": source, "stock_trait_queries": int(calls)}
    assert suite.campaign_save_hashes(host.campaign) == checkpoint
    return {"crates": evidence, "checkpoint_unchanged": True}


async def verify_streamed_transfer(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    before = suite.campaign_save_hashes(host.campaign)

    async def command(seat, mode):
        frame = await seat.bridge.command("fixture-streamed-crate", mode, update_status=False)
        return frame["ack"]["message"]

    async def wait_actor(seat):
        async with asyncio.timeout(15):
            while (value := await command(seat, "observe")) == "absent":
                await asyncio.sleep(.08)
        return value.split(";")

    for seat in (host, guest):
        assert await command(seat, "load") == "loading"
    (crate, host_open), (guest_crate, guest_open) = await asyncio.gather(wait_actor(host), wait_actor(guest))
    assert crate == guest_crate and host_open == guest_open == "false"
    assert native_loot.crate_area(crate) == OBSERVATORY and crate not in native_loot.CRATES
    approach = await host.bridge.command("native-loot-approach", pawn, crate, timeout=5)
    position, = [row["location"] for row in approach["pawns"].values() if row["name"] == pawn]
    await suite.wait(guest_run, lambda frame:
        (point := frame["game"]["pawns"]["local"].get("location"))
        and sum((a - b) ** 2 for a, b in zip(point, position)) < 50**2)
    assert await command(guest, "interact") == "pressed"

    async def committed(run):
        frame = await suite.wait(run, lambda state:
            state["game"].get("native_loot_crates", {}).get(crate, {}).get("state") == "exhausted"
            and crate in state.get("gameplay", {}).get(native_loot.STATE_KEY, {}))
        return frame["gameplay"][native_loot.STATE_KEY][crate], frame

    (roll, host_frame), (guest_roll, _) = await asyncio.gather(committed(host_run), committed(guest_run))
    assert roll == guest_roll and roll["opener"] != "host" and roll["trait_source"] == "permanent"
    published = [row for row in await suite.resident_events(host_run, host_frame)
                 if row["event"] == "native_loot_roll_accepted" and row["id"] == roll["id"]]
    assert len(published) == 1 and published[0]["origin"] == "player", published
    identities = {native_world_loot.crate_identity(roll["id"], row["guid"]) for row in roll["items"]}
    if identities:
        await suite.wait(guest_run, lambda frame: identities <= frame["game"].get(native_world_loot.KEY, {}).keys())

    assert await command(guest, "unload") == "unloading"
    async with asyncio.timeout(12):
        while await command(guest, "loaded") != "false":
            await asyncio.sleep(.08)
    await suite.wait(guest_run, lambda frame: crate not in frame["game"].get("native_loot_crates", {})
                     and not (identities & frame["game"].get(native_world_loot.KEY, {}).keys()))
    assert await command(guest, "reload") == "loading"
    returned, _ = await committed(guest_run)
    assert returned == roll and await wait_actor(guest) == [crate, "true"]
    if identities:
        await suite.wait(guest_run, lambda frame: identities <= frame["game"].get(native_world_loot.KEY, {}).keys())
    assert suite.campaign_save_hashes(host.campaign) == before
    return {"crate": crate, "items": len(identities), "same_roll_after_reload": True, "checkpoint_unchanged": True}


async def verify_crates(host, guest, pawn):
    return {"persistent": await verify_transfer(host, guest, pawn),
            "streamed": await verify_streamed_transfer(host, guest, pawn),
            "runtime": await verify_runtime_transfer(host, guest, pawn)}


async def verify_runtime_transfer(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    before = suite.campaign_save_hashes(host.campaign)

    async def command(seat, mode, *args):
        frame = await seat.bridge.command("fixture-runtime-crate", mode, *args, update_status=False)
        return frame["ack"]["message"]

    host_name = await command(host, "spawn")
    async with asyncio.timeout(12):
        while (crate := await command(host, "identity")) == "pending":
            await asyncio.sleep(.08)
    assert native_loot.runtime_crate(crate) and native_loot.crate_area(crate) == OBSERVATORY, crate
    approached = await host.bridge.command("native-loot-approach", pawn, crate, timeout=5)
    position, = [row["location"] for row in approached["pawns"].values() if row["name"] == pawn]
    await suite.wait(guest_run, lambda frame:
        (point := frame["game"]["pawns"]["local"].get("location"))
        and sum((a - b) ** 2 for a, b in zip(point, position)) < 50**2)
    await suite.wait(guest_run, lambda frame:
        frame["game"].get("native_loot_crates", {}).get(crate, {}).get("state") == "ready")
    guest_name, authority, opened = (await command(guest, "observe", crate)).split(";")
    assert authority == opened == "false"
    assert await command(host, "observe", crate) == f"{host_name};true;false"
    assert await command(guest, "interact", crate) == "pressed"
    frames = await asyncio.gather(*(suite.wait(run, lambda frame:
        frame["game"].get("native_loot_crates", {}).get(crate, {}).get("state") == "exhausted"
        and crate in frame.get("gameplay", {}).get(native_loot.STATE_KEY, {})) for run in (host_run, guest_run)))
    roll, peer = (frame["gameplay"][native_loot.STATE_KEY][crate] for frame in frames)
    assert roll == peer and roll["opener"] != "host"
    accepted = [row for row in await suite.resident_events(host_run, frames[0])
                if row["event"] == "native_loot_roll_accepted" and row["id"] == roll["id"]]
    assert len(accepted) == 1 and accepted[0].get("origin") == "player", accepted
    pickup = await suite.claim_stock_crate_item(host, guest, pawn, crate, roll, *frames) if roll["items"] else None
    assert suite.campaign_save_hashes(host.campaign) == before
    saved = await suite.submit(host_run, "checkpoint")
    assert saved["ok"], saved
    checkpoint = json.loads((host.campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
    assert crate not in checkpoint["world"]["party"].get(native_loot.STATE_KEY, {})
    assert (await command(guest, "observe", crate)).endswith(";false;true")
    saved_hashes = suite.campaign_save_hashes(host.campaign)
    assert await command(host, "destroy", crate) == "destroyed"
    gone = await asyncio.gather(*(suite.wait(run, lambda frame:
        crate not in frame["game"].get("native_loot_crates", {})
        and crate not in frame.get("gameplay", {}).get(native_loot.STATE_KEY, {})) for run in (host_run, guest_run)))
    events = await suite.resident_events(host_run, gone[0])
    assert any(row["event"] == "native_runtime_loot_retired" and row["roll_id"] == roll["id"] for row in events)
    assert suite.campaign_save_hashes(host.campaign) == saved_hashes
    return {"crate": crate, "host_actor": host_name, "guest_actor": guest_name, "items": len(roll["items"]),
            "pickup": pickup, "checkpoint_unchanged_before_save": True, "runtime_roll_excluded_from_checkpoint": True,
            "destroyed_on_both_seats": True, "destroyed_roll_released_without_save": True}


async def exercise(work, first, second, saved):
    with ExitStack() as fixtures:
        for game in (first, second):
            fixtures.enter_context(install_fixture(game))
        await suite.verify(work, first, second, saved, crate_transfer_check=verify_crates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work, args.first_game, args.second_game, args.vanilla_saved))
