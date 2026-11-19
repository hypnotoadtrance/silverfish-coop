"""Verify the stock Pine Fence door, Smuggler cash purchase, and guest MRE use."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_guest_held_fire_smoke import wait_event
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_guest_consumable as meals, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.native_finite_shop import MERCHANTS, SMUGGLER


PINE_RIDGE = "/Game/Classes/Maps/Maps_OverWorld/PineRidge_P"
DOOR = "Pine_Fence"
COUNTER = (-2792.382, 7455.544, -260.829)


async def verify(work: Path, original: Path):
    name = "smuggler-shop-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    before_files = saved_hashes(original)
    host, guest = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--native-shop-view", "--native-guest-mre")
    guest_flags = ("--headless", "--native-zones", "--native-guest-inventory-view")
    processes, evidence = [], {}

    async def launch(directory, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((directory, process))
        return process

    async def initial_invite():
        process = await asyncio.create_subprocess_exec(
            *cli, "invite", *host, "--name", "Smuggler customer", "--character", "Stock shopper",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, errors = await asyncio.wait_for(process.communicate(), 20)
        if process.returncode or not output:
            raise RuntimeError(errors.decode(errors="replace"))
        return json.loads(output)

    async def action(directory, verb, **kwargs):
        reply = await submit(directory, verb, **kwargs)
        if not reply.get("ok"):
            raise RuntimeError(f"{verb}: {reply}")
        return reply

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def paired(area, previous_epoch=None):
        state = await wait(host_run, lambda s: s.get("state") == "hosting" and s.get("link_connected")
                           and s.get("zone_epoch") != previous_epoch and s.get("game", {}).get("map") == area
                           and s["game"].get("native_guest_save", {}).get("state") == "bound", timeout=75)
        await wait(guest_run, lambda s: s.get("state") == "connected" and
                   (s.get("host_session"), s.get("zone_epoch"), s.get("character_id"), s.get("game", {}).get("map")) ==
                   (state["generation"], state["zone_epoch"], character, area), timeout=75)
        return state

    async def checkpoint_flags():
        await action(host_run, "checkpoint")
        saved = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        profile = root / "game-user"
        h = native_host_save.verify(profile, saved["world"])
        g = native_host_save.guest_record(profile, saved["characters"][character]["state"]["native_guest_save"], character)
        assert h and g and h["slot"] != g["slot"], (h, g)
        return tuple(stock_flags(native_host_save.directory(profile), frame, SMUGGLER) for frame in (h, g))

    async def approach():
        arranged = await action(host_run, "native-finite-shop-approach", target="guest")
        assert arranged["actor"] == MERCHANTS[SMUGGLER], arranged

        def near_counter(s):
            position = s.get("game", {}).get("pawns", {}).get("guest", {}).get("location")
            return position and math.dist(position, COUNTER) < 250

        authority = await wait(host_run, near_counter)
        exact = authority["game"]["pawns"]["guest"]

        def reproduced(s):
            game = s.get("game", {})
            position = game.get("pawns", {}).get("local", {}).get("location")
            return game.get("map") == SMUGGLER and position and math.dist(position, exact["location"]) <= 25

        return await wait(guest_run, reproduced)

    def offer_menu(s, sequence, offset):
        game = s.get("game", {})
        view, page = game.get("native_shop_view", {}), game.get("native_shop_view_page", {})
        return (view.get("state"), view.get("map"), view.get("actor"), view.get("sequence"), page.get("offset")) == (
            "ready", SMUGGLER, MERCHANTS[SMUGGLER], sequence, offset)

    async def buy_stock():
        listing = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        assert listing["map"] == SMUGGLER and listing["actor"] == MERCHANTS[SMUGGLER], listing
        assert 25_000 <= listing["merchant_cash_before"] <= 65_000, listing
        (index, offer), = [(i, item) for i, item in enumerate(listing["offers"]) if item["class_path"] == meals.MRE_CLASS]
        assert 1 <= offer["quantity"] <= 4 and offer["value"] == 25 * offer["quantity"], offer
        assert offer["price"] == 5 * offer["value"], offer
        opened = (await action(guest_run, "native-shop-view-fixture", target="open"))["native_shop_view"]
        sequence = opened["sequence"]
        await wait(guest_run, lambda s: offer_menu(s, sequence, 0))
        for offset in range(8, (index // 8 + 1) * 8, 8):
            await action(guest_run, "native-shop-view-fixture", target="page-down")
            await wait(guest_run, lambda s: offer_menu(s, sequence, offset))
        for _ in range(index % 8):
            await action(guest_run, "native-shop-view-fixture", target="down")
        confirming = (await action(guest_run, "native-shop-view-fixture", target="enter"))["native_shop_view"]
        assert (confirming["state"], confirming["guid"], confirming["price"]) == ("confirming", offer["guid"], offer["price"])
        await action(guest_run, "native-shop-view-fixture", target="enter")
        event = await wait_event(guest_run, "native_shop_view_purchased",
                                 lambda row: row.get("native_finite_purchase", {}).get("offers") == [offer])
        receipt = event["native_finite_purchase"]
        assert (receipt["wallet_after"], receipt["merchant_cash_after"]) == (
            listing["wallet_before"] - offer["price"], listing["merchant_cash_before"] + offer["price"]), receipt
        await action(guest_run, "native-shop-view-fixture", target="back")
        return offer, receipt, opened

    async def inventory(directory):
        page = (await action(directory, "native-inventory-list"))["native_inventory"]
        assert page["next_offset"] is None, page
        return {row["guid"]: row for row in page["items"]}

    async def eat_stock(offer):
        await action(guest_run, "native-inventory-view-fixture")
        shown = await wait(guest_run, lambda s: s.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        selected = shown["game"].get("native_guest_inventory_use_view", {})
        key = "native-inventory-view-key-fixture"
        for _ in range(shown["game"]["native_inventory_view"]["count"]):
            if selected.get("guid") == offer["guid"]:
                break
            selected = (await action(guest_run, key, target="down")).get("native_guest_inventory_use_view", {})
        assert (selected.get("state"), selected.get("guid")) == ("selecting", offer["guid"]), selected
        accepted = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert accepted["state"] == "confirming" and accepted["guid"] == offer["guid"], accepted
        using = (await action(guest_run, key, target="confirm"))["native_guest_inventory_use_view"]
        assert using["state"] == "using" and using["guid"] == offer["guid"], using
        result = await wait_event(guest_run, "native_guest_inventory_used", lambda r: r.get("view_sequence") == using["sequence"])
        proof = result["native_guest_mre"]
        assert (proof["map"], proof["guid"], proof["class_path"]) == (SMUGGLER, offer["guid"], meals.MRE_CLASS)
        assert (proof["quantity_before"], proof["quantity_after"]) == (offer["quantity"], offer["quantity"] - 1), proof
        assert proof["food_after"] == min(200, proof["food_before"] + 30), proof
        assert proof["host_intox_before"] == proof["host_intox_after"], proof
        await wait(guest_run, lambda s: s.get("game", {}).get("native_guest_inventory_use_view") == dict(using, state="used"))
        await action(guest_run, key, target="back")
        return result

    try:
        server = await launch(host_run, "host", "host", *host, "--port", "0", "--start-zone", PINE_RIDGE, *host_flags)
        await wait(host_run, lambda s: s.get("state") == "hosting")
        invite = await initial_invite()
        character = invite["character_id"]
        player = await launch(guest_run, "guest", "join", *guest, "--invite", invite["invite_file"], *guest_flags)
        start = await paired(PINE_RIDGE)
        for who in ("guest", "host"):
            await action(host_run, "native-door-position", target=who, entrance=DOOR, exit_position="near")
        await action(host_run, "native-door-probe", target="host", entrance=DOOR)
        entered = await paired(SMUGGLER, start["zone_epoch"])
        assert entered["generation"] == start["generation"], entered
        evidence["stock_door"] = {"source": PINE_RIDGE, "target": SMUGGLER, "entrance": DOOR, "epoch": entered["zone_epoch"]}

        await action(host_run, "native-finite-shop-wallet-fixture", amount=100_000)
        await approach()
        initial_flags = await checkpoint_flags()
        initial_host_inventory = await inventory(host_run)
        offer, purchase, opened = await buy_stock()
        bought = (await inventory(guest_run))[offer["guid"]]
        assert (bought["source"], bought["quantity"], bought["class_path"]) == ("backpack", offer["quantity"], meals.MRE_CLASS)
        assert offer["guid"] not in await inventory(host_run)
        used = await eat_stock(offer)
        remainder = (await inventory(guest_run)).get(offer["guid"])
        assert (remainder["quantity"] if remainder else 0) == offer["quantity"] - 1, remainder
        settled_flags = await checkpoint_flags()
        assert (initial_flags[0]["Player_Cash"], settled_flags[0]["Player_Cash"]) == (100_000, purchase["wallet_after"])
        assert initial_flags[1].get("Player_Cash", 0) == settled_flags[1].get("Player_Cash", 0)
        for initial, after in zip(initial_flags, settled_flags, strict=True):
            assert {key: value for key, value in after.items() if key.startswith(("Rep_", "Adv_"))} == {
                key: value for key, value in initial.items() if key.startswith(("Rep_", "Adv_"))}
        assert await inventory(host_run) == initial_host_inventory
        evidence["purchase"] = {"stock_f": opened, "offer": offer, "settled": purchase}
        evidence["consumption"] = used

        await stop(guest_run, player)
        await stop(host_run, server)
        await launch(host_run, "restart-host", "host", *host, "--port", str(entered["endpoint"]["port"]), *host_flags)
        restored = await wait(host_run, lambda s: s.get("state") == "hosting" and s.get("generation") != entered["generation"])
        renewed = await action(host_run, "refresh-invite", target=invite["guest_id"])
        assert (restored["endpoint"]["port"], renewed["character_id"]) == (entered["endpoint"]["port"], character)
        await launch(guest_run, "restart-guest", "join", *guest, "--invite", renewed["invite_file"], *guest_flags)
        await paired(SMUGGLER, entered["zone_epoch"])
        await approach()
        afterward = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        assert (afterward["wallet_before"], afterward["merchant_cash_before"]) == (
            purchase["wallet_after"], purchase["merchant_cash_after"]), afterward
        assert offer["guid"] not in {item["guid"] for item in afterward["offers"]}
        assert (await inventory(guest_run)).get(offer["guid"]) == remainder
        replay = await action(guest_run, meals.MRE, target=offer["guid"], operation_id=used["operation_id"])
        assert replay["native_guest_mre"] == used["native_guest_mre"], replay
        evidence.update(verified=True, restart={"wallet": afterward["wallet_before"],
                        "merchant_cash": afterward["merchant_cash_before"], "remaining": remainder})
    except BaseException as error:
        evidence.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, process in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (RuntimeError, AssertionError, OSError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 15)
        evidence["original_saves_verified"] = saved_hashes(original) == before_files
        evidence["original_file_count"] = len(before_files)
        (host_run / "live-smuggler-shop-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original single-player saves changed during the fixture"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
