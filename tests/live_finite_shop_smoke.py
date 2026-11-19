"""Verify one stock merchant purchase and the paired stock saves."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
GUNSMITH = "/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P"
GUNSMITH_ACTOR = f"Shop_BP_C {GUNSMITH}.Shop_Gunsmith_P:PersistentLevel.Shop_BP_C_1"
BALL_AMMO = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
MAPS = {"observatory": OBSERVATORY,
        "scav-bar": "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
        "black-market": "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
        "scientist": "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01",
        "gunsmith": GUNSMITH}
CAMERA = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"


async def verify(work: Path, vanilla_saved: Path | None, shop_view: bool, sell: bool, merchant: str, *, shop_pages=False) -> None:
    name = "finite-shop-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    host_run = campaign / "run"
    guest_run = work / "clients" / name / "run"
    host_target = ("--campaign", name)
    guest_target = ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    launch_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    if merchant in ("scav-bar", "scientist", "gunsmith"):
        launch_flags += ("--native-expanded-zones",)
    if shop_view:
        launch_flags += ("--native-shop-view",)
    if vanilla_saved is not None and not vanilla_saved.is_dir():
        raise ValueError(f"Original Saved folder does not exist: {vanilla_saved}")
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    processes = []
    reusable = merchant in ("scientist", "gunsmith")
    proof = None

    async def spawn(directory, filename, owner, *arguments):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / filename).open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=output, stderr=output,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if not output or (expected is not None and (process.returncode == 0) != expected):
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def act(owner, action, *args, expected=True):
        return await command("action", *owner, action, *args, expected=expected)

    async def wait(directory, predicate):
        deadline, status = time.monotonic() + 40, {}
        while time.monotonic() < deadline:
            try:
                status = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                await asyncio.sleep(.2)
                continue
            if predicate(status):
                return status
            if status.get("state") == "failed":
                break
            await asyncio.sleep(.2)
        raise RuntimeError(f"{directory} did not reach the expected finite merchant state: {status.get('state')}")

    async def saved_balances(character):
        await act(host_target, "checkpoint")
        saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        host_frame = saved["world"]["native_host_save"]
        guest_frame = saved["characters"][character]["state"]["native_guest_save"]
        assert guest_frame["character_id"] == character and guest_frame["slot"] != host_frame["slot"], guest_frame
        saves = campaign / "game-user" / "Saved" / "SaveGames"
        balances = {}
        for owner, frame in (("host", host_frame), ("guest", guest_frame)):
            flags = stock_flags(saves, frame, GUNSMITH)
            balances[owner] = {"cash": flags.get("Player_Cash", 0), "xp": flags.get("Player_Xp", 0),
                               "faction": {key: value for key, value in flags.items() if key.startswith(("Rep_", "Adv_"))}}
        return balances

    async def rendered_page(identity, listing, offset, previous_event, wallet):
        def rendered(state):
            game = state.get("game", {})
            view, page = game.get("native_shop_view", {}), game.get("native_shop_view_page", {})
            return (state.get("state") == "connected" and view.get("state") == "ready"
                    and all(view.get(key) == value and page.get(key) == value for key, value in identity.items())
                    and page.get("offset") == page.get("wanted_offset") == offset and page.get("pending_event") == 0
                    and type(page.get("render_event")) is int and page["render_event"] > previous_event)

        state = await wait(guest_run, rendered)
        game = state["game"]
        view, page = game["native_shop_view"], game["native_shop_view_page"]
        row = listing["offers"][offset]
        assert (view["total"], view["visible"], view["selection"], view["guid"], view["price"], view["wallet"]) == (
            listing["count"], min(8, listing["count"] - offset), 1, row["guid"], row["price"], wallet,
        ), (page, view, row)
        return {"page": page, "view": view}

    async def check_shop_pages(opened, listing, offer):
        identity = {key: opened[key] for key in ("map", "actor", "pawn", "sequence")}
        wallet, total = listing["wallet_before"], listing["count"]
        assert listing["offers"][0] == offer, listing
        current = await rendered_page(identity, listing, 0, 0, wallet)
        proof = {"status": "verified" if total > 8 else "one_page", "total": total, "initial": current, "navigation": []}
        if total > 8:
            for key, offset in (("page-down", 8), ("page-up", 0)):
                press = (await act(guest_target, "native-shop-view-fixture", "--target", key))["native_shop_view"]
                assert press["state"] == "paging" and all(press.get(field) == value for field, value in identity.items()), press
                current = await rendered_page(identity, listing, offset, current["page"]["render_event"], wallet)
                proof["navigation"].append({"input": key, "press": press, "render": current})
        return identity, current, proof

    try:
        host = await spawn(host_run, "host-first.txt", host_target, "host", *host_target, "--port", "0",
                           *launch_flags, "--start-zone", MAPS[merchant])
        initial = await wait(host_run, lambda state: state.get("state") == "hosting"
                             and state.get("game", {}).get("map") == MAPS[merchant])
        port = str(initial["endpoint"]["port"])
        invite = await command("invite", *host_target, "--name", "Merchant guest", "--character", "Merchant character")
        await spawn(guest_run, "guest.txt", guest_target, "join", *guest_target, "--headless", "--native-zones",
                    "--invite", invite["invite_file"])
        await wait(guest_run, lambda state: state.get("state") == "connected")
        paired = await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        generation = paired["generation"]
        approach = await act(host_target, "native-finite-shop-approach")
        await act(host_target, "native-finite-shop-wallet-fixture", "--amount", "100000")
        listing = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        assert listing["wallet_before"] == listing["wallet_after"] == 100_000 and listing["offers"], listing
        assert listing["map"] == MAPS[merchant] and listing["actor"] == approach["actor"], listing
        if merchant == "scientist":
            offer, = [row for row in listing["offers"] if row["class_path"] == CAMERA]
        elif merchant == "gunsmith":
            assert listing["actor"] == GUNSMITH_ACTOR, listing
            offer, = [row for row in listing["offers"] if row["class_path"] == BALL_AMMO]
            assert (offer["quantity"], offer["value"]) == (200, 600) and offer["price"] > 0, offer
            before_balances = await saved_balances(invite["character_id"])
            assert before_balances["host"]["cash"] == listing["wallet_before"], before_balances
        else:
            offer = listing["offers"][0] if shop_view else next(
                row for row in listing["offers"] if row["price"] > 0 and "/Weapons/" not in row["class_path"])
        guid, price = offer["guid"], offer["price"]
        stale = await act(guest_target, "native-finite-shop-buy", "--target", f"{guid}@{price + 1}", expected=False)
        assert stale["error"] == "native_finite_shop_quote_changed", stale
        operation_id = uuid.uuid4().hex
        if shop_view:
            opened = (await act(guest_target, "native-shop-view-fixture", "--target", "open"))["native_shop_view"]
            assert (opened["state"], opened["map"], opened["actor"]) == ("pending", MAPS[merchant], listing["actor"]), opened
            if shop_pages:
                page_scope, current_page, page_proof = await check_shop_pages(opened, listing, offer)
                view = current_page["view"]
            else:
                visible = await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") == "ready")
                view = visible["game"]["native_shop_view"]
            offer_index = listing["offers"].index(offer)
            assert offer_index < view["visible"], (offer, view)
            for _ in range(offer_index):
                view = (await act(guest_target, "native-shop-view-fixture", "--target", "down"))["native_shop_view"]
            assert (view["map"], view["actor"], view["sequence"], view["guid"], view["price"]) == (
                MAPS[merchant], listing["actor"], opened["sequence"], guid, price), view
            confirm = (await act(guest_target, "native-shop-view-fixture", "--target", "enter"))["native_shop_view"]
            assert confirm["state"] == "confirming", confirm
            submit = (await act(guest_target, "native-shop-view-fixture", "--target", "enter"))["native_shop_view"]
            assert submit["state"] == "buying", submit
            if shop_pages:
                page_proof["after_purchase"] = await rendered_page(page_scope, listing, 0, current_page["page"]["render_event"], 100_000 - price)
            else:
                await wait(guest_run, lambda state: (panel := state.get("game", {}).get("native_shop_view", {})).get("state") == "ready"
                           and panel.get("total") == listing["count"] - (0 if reusable else 1))
            await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == 100_000 - price)
            records = (await command("events", *guest_target, "--tail", "50"))["events"]
            awarded, = [row for row in records if row["event"] == "native_shop_view_purchased"]
            purchase, operation_id = awarded["native_finite_purchase"], awarded["operation_id"]
            if shop_pages:
                renders = [row for row in records if row["event"] == "native_shop_view_rendered" and row["sequence"] == opened["sequence"]]
                offsets = [0, 8, 0, 0] if listing["count"] > 8 else [0, 0]
                assert [row["offset"] for row in renders] == offsets and all(row["total"] == listing["count"] for row in renders), renders
                assert renders[-1]["operation_id"] == operation_id, renders
                page_proof["authenticated_renders"] = renders
        operation = ("native-finite-shop-buy", "--target", f"{guid}@{price}", "--operation-id", operation_id)
        if not shop_view:
            purchase = (await act(guest_target, *operation))["native_finite_purchase"]
        if reusable:
            assert purchase["offer_guid"] == guid and purchase["stock_source"] == "stock_shelf", purchase
            acquire_guid = purchase["offers"][0]["guid"]
            assert acquire_guid != guid, purchase
        else:
            acquire_guid = guid
        assert purchase["wallet_after"] == 100_000 - price and purchase["offers"] == [dict(offer, guid=acquire_guid)], purchase
        assert (await act(guest_target, *operation))["native_finite_purchase"] == purchase
        stock = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        assert (guid in {row["guid"] for row in stock["offers"]}) == reusable, stock
        inventory = (await act(guest_target, "native-inventory-list"))["native_inventory"]
        acquired, = [row for row in inventory["items"] if row["guid"] == acquire_guid]
        assert acquired["class_path"] == offer["class_path"] and acquired["quantity"] == offer["quantity"], acquired
        expected_wallet = purchase["wallet_after"]
        if merchant == "gunsmith":
            assert inventory["owner"] == invite["character_id"] and acquired["source"] == "backpack" and acquired["stackable"] is True, inventory
            assert stock["offers"] == listing["offers"] and all(row["guid"] != acquire_guid for row in stock["offers"]), stock
            retained_offer, = [row for row in stock["offers"] if row["guid"] == guid]
            assert purchase["merchant_cash_after"] == listing["merchant_cash_before"] + price, purchase
            assert stock["merchant_cash_before"] == stock["merchant_cash_after"] == purchase["merchant_cash_after"], stock
            linked = await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == expected_wallet)
            assert linked["character_id"] == invite["character_id"], linked
            paid_balances = await saved_balances(invite["character_id"])
            expected_balances = {**before_balances, "host": {**before_balances["host"], "cash": expected_wallet}}
            assert paid_balances == expected_balances, (before_balances, paid_balances)
        if sell:
            quote = (await act(guest_target, "native-finite-shop-sale-quote", "--target", guid))["native_finite_sale_quote"]
            quote_row, = quote["offers"]
            sale_price = quote_row["price"]
            assert quote_row == dict(offer, price=offer["value"]) and quote["wallet_before"] == expected_wallet, quote
            wrong_price = await act(guest_target, "native-finite-shop-sell", "--target", f"{guid}@{sale_price + 1}", expected=False)
            assert wrong_price["error"] == "native_finite_shop_quote_changed", wrong_price
            sale_command = ("native-finite-shop-sell", "--target", f"{guid}@{sale_price}", "--operation-id", uuid.uuid4().hex)
            sale = (await act(guest_target, *sale_command))["native_finite_sale"]
            expected_wallet += sale_price
            assert sale["offers"] == [quote_row] and sale["wallet_after"] == expected_wallet, sale
            assert sale["merchant_cash_after"] == quote["merchant_cash_before"] - sale_price, sale
            assert (await act(guest_target, *sale_command))["native_finite_sale"] == sale
            unsold = (await act(guest_target, "native-inventory-list"))["native_inventory"]
            assert not any(row["guid"] == guid for row in unsold["items"]), unsold

        await act(host_target, "stop")
        assert await asyncio.wait_for(host.wait(), 12) == 0, host.returncode
        await spawn(host_run, "host-restart.txt", host_target, "host", *host_target, "--port", port, *launch_flags)
        returned = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != generation
                              and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        assert returned["game"]["native_wallet"] == expected_wallet and returned["endpoint"]["port"] == int(port), returned
        if merchant == "gunsmith":
            rebound = await wait(guest_run, lambda state: state.get("state") == "connected"
                                 and state.get("host_session") == returned["generation"]
                                 and state.get("character_id") == invite["character_id"]
                                 and state.get("gameplay", {}).get("shared_wallet") == expected_wallet)
        await act(host_target, "native-finite-shop-approach")
        after = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        if merchant == "scientist":
            assert len([row for row in after["offers"] if row["class_path"] == CAMERA]) == 1, after
        elif merchant == "gunsmith":
            assert after["actor"] == GUNSMITH_ACTOR and after["map"] == GUNSMITH, after
            restarted_offer, = [row for row in after["offers"] if row["class_path"] == BALL_AMMO]
            assert (restarted_offer["quantity"], restarted_offer["value"]) == (200, 600), restarted_offer
            assert restarted_offer["guid"] != acquire_guid, restarted_offer
            assert after["merchant_cash_before"] == purchase["merchant_cash_after"], after
        else:
            assert guid not in {row["guid"] for row in after["offers"]}, after
        assert (await act(guest_target, *operation))["native_finite_purchase"] == purchase
        persisted = (await act(guest_target, "native-inventory-list"))["native_inventory"]
        assert [row for row in persisted["items"] if row["guid"] == acquire_guid] == ([] if sell else [acquired]), persisted
        proof = {"purchase": purchase, "inventory_row": acquired, "after_restart_offer_count": after["count"],
                 "stock_merchant_input": shop_view}
        if merchant == "gunsmith":
            assert rebound["game"]["map"] == GUNSMITH and persisted["owner"] == invite["character_id"], (rebound, persisted)
            restored_balances = await saved_balances(invite["character_id"])
            assert restored_balances == paid_balances, (restored_balances, paid_balances)
            proof["gunsmith"] = {"actor": GUNSMITH_ACTOR, "shelf_offer": offer, "shelf_offer_after_buy": retained_offer,
                                 "shelf_offer_after_restart": restarted_offer, "port": int(port), "character": invite["character_id"],
                                 "restart_generation": returned["generation"], "merchant_cash_after_restart": after["merchant_cash_before"],
                                 "native_balances_before": before_balances, "native_balances_after": paid_balances,
                                 "native_balances_restored": restored_balances}
            if shop_view:
                proof["gunsmith"]["stock_interact"] = opened
                proof["gunsmith"]["overlay_selected_quote"] = view
            if shop_pages:
                proof["gunsmith"]["shop_pages"] = page_proof
        if merchant == "scientist":
            equipped = await act(guest_target, "native-guest-weapon-equip", "--target", acquire_guid)
            report = equipped["native_guest_weapon"]
            assert (report["status"], report["guid"], report["kind"], report["class_path"]) == (
                "camera_equipped", acquire_guid, "camera", CAMERA), equipped
            inventory = (await act(guest_target, "native-inventory-list"))["native_inventory"]
            assert any(row["guid"] == acquire_guid and row["source"] == "equipment" for row in inventory["items"]), inventory
            proof["stock_camera_equipped"] = report
        if sell:
            assert (await act(guest_target, *sale_command))["native_finite_sale"] == sale
            assert after["merchant_cash_before"] == sale["merchant_cash_after"], after
            denied = await act(guest_target, "native-finite-shop-sale-quote", "--target", guid, expected=False)
            assert denied["error"].startswith("finite_shop_refused_"), denied
            proof.update(sale=sale, sale_quote=quote)
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await act(owner, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            original_verified = original == saved_hashes(vanilla_saved)
            if proof is not None:
                proof["original_saves_verified"] = original_verified
            assert original_verified, "The original Saved directory changed during the smoke"
        print(host_run, flush=True)
    (host_run / "live-finite-shop-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", required=True, type=Path)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--shop-view", "--shop-input", action="store_true")
    options.add_argument("--shop-pages", action="store_true", help="verify authenticated Gunsmith overlay page navigation when stock spans two pages")
    options.add_argument("--sell", action="store_true")
    options.add_argument("--merchant", choices=MAPS, default="observatory")
    args = options.parse_args()
    if args.merchant == "scientist" and args.sell:
        options.error("The Scientist smoke checks camera acquisition and equip")
    if args.merchant == "gunsmith" and args.sell:
        options.error("The Gunsmith smoke checks 9mm retention through restart")
    if args.shop_pages and args.merchant != "gunsmith":
        options.error("--shop-pages checks the Gunsmith stock profile")
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved, args.shop_view or args.shop_pages, args.sell, args.merchant,
                       shop_pages=args.shop_pages))
