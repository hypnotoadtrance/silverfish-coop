"""Verify ShopKeep reception, an ordinary cash purchase and isolated restart."""

import argparse
import asyncio
import json
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
from tools.silverfish import native_dialogue as dialogue, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.travel import NORTHEAST_RAIL, SHOPKEEP


CONSTANT_OFFERS = {
    "/Game/Classes/Items/Item_MedSalve.Item_MedSalve_C",
    "/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C",
}


async def verify(work: Path, original: Path):
    name = "shopkeep-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    host_run, guest_run = root / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    if not original.is_dir():
        raise ValueError(f"The original Saved directory does not exist: {original}")
    originals = saved_hashes(original)
    team = ("--campaign", name)
    player = ("--profile", name)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones",
                  "--native-expanded-zones", "--native-shop-view")
    guest_flags = ("--headless", "--native-zones")
    jobs = []
    evidence = {}

    async def launch(directory, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((directory, process))
        return process

    async def initial_invite():
        process = await asyncio.create_subprocess_exec(
            *cli, "invite", *team, "--name", "Loopback ShopKeep customer", "--character", "Reception customer",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result, error = await asyncio.wait_for(process.communicate(), 20)
        if process.returncode or not result:
            raise RuntimeError(error.decode(errors="replace"))
        return json.loads(result)

    async def action(directory, verb, *, expected=True, **arguments):
        reply = await submit(directory, verb, **arguments)
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{verb}: {reply}")
        return reply

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def paired(area, previous_epoch=None):
        state = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("link_connected")
                           and state.get("zone_epoch") != previous_epoch and state.get("game", {}).get("map") == area
                           and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=75)
        await wait(guest_run, lambda state_local: state_local.get("state") == "connected"
                   and (state_local.get("host_session"), state_local.get("zone_epoch"), state_local.get("character_id"),
                        state_local.get("game", {}).get("map")) == (state["generation"], state["zone_epoch"], character, area),
                   timeout=75)
        return state

    async def reception(ordinal, entry, key):
        await action(host_run, dialogue.APPROACH, target="guest")
        preopen = await action(guest_run, "native-finite-shop-list", expected=False)
        assert preopen["error"] == "finite_shop_refused_shopkeep_stock_dialogue_required", preopen
        await wait(guest_run, lambda state: state.get("game", {}).get("native_dialogue_view", {}).get("state") == "armed")
        pressed = (await action(guest_run, dialogue.FIXTURE, target="open"))["native_dialogue_view"]
        opened = await wait_event(guest_run, "native_dialogue_interaction",
                                  lambda event: event.get("generation") == key and event.get("sequence") == pressed["sequence"])
        assert (opened["profile"], opened["row"], opened["effect"]) == (
            dialogue.SHOPKEEP_PROFILE, entry, "opened-first" if entry == dialogue.SHOPKEEP_FIRST else "opened-repeat"
        ), opened
        unrelated = await action(guest_run, dialogue.FIXTURE, target="4", expected=False)
        assert unrelated["ok"] is False, unrelated
        chosen = (await action(guest_run, dialogue.FIXTURE, target=ordinal))["native_dialogue_view"]
        selected = await wait_event(guest_run, "native_dialogue_interaction", lambda event: event.get("generation") == key
                                    and event.get("sequence") == chosen["sequence"] and event.get("effect") == "cash-granted")
        view = await page(chosen["sequence"], offset=0)
        assert view["actor"] == dialogue.SHOPKEEP_COUNTER and view["map"] == SHOPKEEP, view
        return {"opened": opened, "cash_response": selected, "merchant": view}

    async def page(sequence, *, offset):
        state = await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") == "ready"
                           and state["game"]["native_shop_view"]["sequence"] == sequence
                           and state["game"].get("native_shop_view_page", {}).get("offset") == offset)
        return state["game"]["native_shop_view"]

    async def inventory(directory, **arguments):
        view = (await action(directory, "native-inventory-list", **arguments))["native_inventory"]
        assert view["next_offset"] is None, view
        return {row["guid"]: row for row in view["items"]}

    try:
        authority = await launch(host_run, "host", "host", *team, "--port", "0", "--start-zone", NORTHEAST_RAIL, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await initial_invite()
        character = invitation["character_id"]
        guest = await launch(guest_run, "guest", "join", *player, "--invite", invitation["invite_file"], *guest_flags)
        before = await paired(NORTHEAST_RAIL)
        for role in ("guest", "host"):
            await action(host_run, "native-door-position", target=role, entrance="ShopKeep", exit_position="near")
        await action(host_run, "native-door-probe", target="host", entrance="ShopKeep")
        entered = await paired(SHOPKEEP, before["zone_epoch"])
        assert entered["generation"] == before["generation"], entered
        evidence["stock_route"] = {"source": NORTHEAST_RAIL, "arrival": SHOPKEEP, "entry": "ShopKeep", "character": character}
        await action(host_run, "native-finite-shop-wallet-fixture", amount=100_000)
        generation = (await wait(guest_run, lambda state: bool(state.get("generation"))))["generation"]
        opened = await reception("1", dialogue.SHOPKEEP_FIRST, generation)
        evidence["stock_reception"] = opened
        listing = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        assert (listing["map"], listing["actor"], listing["wallet_before"], listing["wallet_after"]) == (
            SHOPKEEP, dialogue.SHOPKEEP_COUNTER, 100_000, 100_000), listing
        assert 5000 <= listing["merchant_cash_before"] <= 35_000, listing
        selection = next(index for index, offer in enumerate(listing["offers"]) if offer["class_path"] in CONSTANT_OFFERS)
        offer = listing["offers"][selection]
        sequence = opened["merchant"]["sequence"]
        offset = (selection // 8) * 8
        for current_offset in range(8, offset + 1, 8):
            await action(guest_run, "native-shop-view-fixture", target="page-down")
            await page(sequence, offset=current_offset)
        for _ in range(selection - offset):
            await action(guest_run, "native-shop-view-fixture", target="down")
        current = await page(sequence, offset=offset)
        assert (current["guid"], current["price"]) == (offer["guid"], offer["price"]), current
        confirm = (await action(guest_run, "native-shop-view-fixture", target="enter"))["native_shop_view"]
        assert confirm["state"] == "confirming", confirm
        submitted = (await action(guest_run, "native-shop-view-fixture", target="enter"))["native_shop_view"]
        assert submitted["state"] == "buying", submitted
        purchase = (await wait_event(guest_run, "native_shop_view_purchased", lambda event: event.get(
            "native_finite_purchase", {}).get("offers") == [offer]))["native_finite_purchase"]
        assert (purchase["wallet_after"], purchase["merchant_cash_after"]) == (
            listing["wallet_before"] - offer["price"], listing["merchant_cash_before"] + offer["price"]), purchase
        row = (await inventory(guest_run))[offer["guid"]]
        assert (row["source"], row["class_path"], row["quantity"]) == ("backpack", offer["class_path"], offer["quantity"]), row
        assert offer["guid"] not in await inventory(host_run), purchase
        evidence["finite_purchase"] = {"quote": offer, "receipt": purchase, "owned_item": row}

        await stop(host_run, authority)
        await stop(guest_run, guest)
        checkpoint = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        host_frame = native_host_save.verify(root / "game-user", checkpoint["world"])
        guest_frame = native_host_save.guest_record(root / "game-user", checkpoint["characters"][character]["state"]["native_guest_save"],
                                                  character)
        assert host_frame and host_frame["map"] == SHOPKEEP and guest_frame["slot"] != host_frame["slot"], (host_frame, guest_frame)
        stock = stock_flags(native_host_save.directory(root / "game-user"), host_frame, SHOPKEEP)
        assert (stock.get("Meet_ShopKeep"), stock.get("Player_Cash")) == (1, purchase["wallet_after"]), stock
        await launch(host_run, "restart-host", "host", *team, "--port", str(entered["endpoint"]["port"]), *host_flags)
        restarted = await wait(host_run, lambda state: state.get("state") == "hosting"
                               and state.get("generation") != entered["generation"] and state.get("game", {}).get("map") == SHOPKEEP)
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        assert restarted["endpoint"]["port"] == entered["endpoint"]["port"] and renewed["character_id"] == character, restarted
        await launch(guest_run, "restart-guest", "join", *player, "--invite", renewed["invite_file"], *guest_flags)
        await paired(SHOPKEEP, entered["zone_epoch"])
        new_generation = (await wait(guest_run, lambda state: state.get("generation") != generation
                                     and state.get("state") == "connected"))["generation"]
        reopened = await reception("2", dialogue.SHOPKEEP_ROOT, new_generation)
        after = (await action(guest_run, "native-finite-shop-list"))["native_finite_shop"]
        assert after["wallet_before"] == purchase["wallet_after"] and after["merchant_cash_before"] == purchase["merchant_cash_after"], after
        assert all(item["guid"] != offer["guid"] for item in after["offers"]) and (await inventory(guest_run))[offer["guid"]] == row, after
        evidence.update(verified=True, restart={"stock_flag": stock.get("Meet_ShopKeep"), "merchant": reopened,
                                                "wallet": after["wallet_before"], "merchant_cash": after["merchant_cash_before"]})
    except BaseException as error:
        evidence.update(verified=False, failure=f"{type(error).__name__}: {error}")
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (RuntimeError, AssertionError, OSError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 15)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["original_file_count"] = len(originals)
        (host_run / "live-shopkeep-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player files changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
