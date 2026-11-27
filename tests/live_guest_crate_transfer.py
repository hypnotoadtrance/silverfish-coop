"""Verify local stock crate rolls and exact escrow transfer on a private pair."""

import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path

import live_portable_title_smoke as suite
from tools.silverfish import native_inventory_grant, native_loot
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


async def exercise(work, first, second, saved):
    with ExitStack() as fixtures:
        for game in (first, second):
            fixtures.enter_context(install_fixture(game))
        await suite.verify(work, first, second, saved, crate_transfer_check=verify_transfer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work, args.first_game, args.second_game, args.vanilla_saved))
