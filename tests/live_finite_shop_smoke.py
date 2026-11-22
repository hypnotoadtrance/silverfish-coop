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
BLANK_CLASS = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
MAPS = {"observatory": OBSERVATORY,
        "scav-bar": "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
        "black-market": "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
        "scientist": "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01",
        "gunsmith": GUNSMITH}
CAMERA = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
SCIENTIST_ACTOR = f"Shop_BP_C {MAPS['scientist']}.Shop_Scientist_01:PersistentLevel.Shop_BP_C_2"


async def verify(work: Path, vanilla_saved: Path | None, shop_view: bool, sell: bool, merchant: str, *,
                 shop_pages=False, scientist_blank=False, stock_screen=False) -> None:
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
    verify_balances = merchant == "gunsmith" or scientist_blank
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

    async def stock_ready(wallet):
        result = await wait(guest_run, lambda state: (panel := state.get("game", {}).get("native_shop_view", {})).get("state") == "ready"
                            and panel.get("stock") is True and panel.get("wallet") == wallet)
        return result["game"]["native_shop_view"]

    async def saved_balances(character):
        await act(host_target, "checkpoint")
        saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        host_frame = saved["world"]["native_host_save"]
        guest_frame = saved["characters"][character]["state"]["native_guest_save"]
        assert guest_frame["character_id"] == character and guest_frame["slot"] != host_frame["slot"], guest_frame
        saves = campaign / "game-user" / "Saved" / "SaveGames"
        balances = {}
        for owner, frame in (("host", host_frame), ("guest", guest_frame)):
            flags = stock_flags(saves, frame, MAPS[merchant])
            faction = {key: value for key, value in flags.items() if key.startswith(("Rep_", "Adv_"))}
            if scientist_blank:
                faction.setdefault("Rep_Commune", 0)
            balances[owner] = {"cash": flags.get("Player_Cash", 0), "xp": flags.get("Player_Xp", 0), "faction": faction}
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
        current = await rendered_page(identity, listing, 0, 0, wallet)
        proof = {"status": "verified" if total > 8 else "one_page", "total": total, "initial": current, "navigation": []}
        destination = listing["offers"].index(offer) // 8 * 8
        if total > 8:
            steps = [("page-down", 8), ("page-up", 0)]
            steps.extend(("page-down", offset) for offset in range(8, destination + 1, 8))
            for key, offset in steps:
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
        guest_flags = ("--native-guest-inventory-view",) if stock_screen else ()
        await spawn(guest_run, "guest.txt", guest_target, "join", *guest_target, "--headless", "--native-zones",
                    "--invite", invite["invite_file"], *guest_flags)
        await wait(guest_run, lambda state: state.get("state") == "connected")
        paired = await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        generation = paired["generation"]
        approach = await act(host_target, "native-finite-shop-approach")
        await act(host_target, "native-finite-shop-wallet-fixture", "--amount", "100000")
        listing = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        assert listing["wallet_before"] == listing["wallet_after"] == 100_000 and listing["offers"], listing
        assert listing["map"] == MAPS[merchant] and listing["actor"] == approach["actor"], listing
        if merchant == "scientist":
            assert listing["actor"] == SCIENTIST_ACTOR and sum(row["class_path"] == CAMERA for row in listing["offers"]) == 1, listing
            item_class = BLANK_CLASS if scientist_blank else CAMERA
            offer, = [row for row in listing["offers"] if row["class_path"] == item_class]
            if scientist_blank:
                assert (offer["quantity"], offer["value"]) == (1, 30) and 0 < offer["price"] <= 75, offer
        elif merchant == "gunsmith":
            assert listing["actor"] == GUNSMITH_ACTOR, listing
            offer, = [row for row in listing["offers"] if row["class_path"] == BALL_AMMO and row["source"] == "shelf"]
            assert (offer["quantity"], offer["value"]) == (200, 600) and offer["price"] > 0, offer
        else:
            offer = listing["offers"][0] if shop_view else next(
                row for row in listing["offers"] if row["price"] > 0 and "/Weapons/" not in row["class_path"]
                and row["source"] == "limited")
        reusable = offer["source"] == "shelf"
        if verify_balances:
            before_balances = await saved_balances(invite["character_id"])
            assert before_balances["host"]["cash"] == listing["wallet_before"], before_balances
        guid, price = offer["guid"], offer["price"]
        stale = await act(guest_target, "native-finite-shop-buy", "--target", f"{guid}@{price + 1}", expected=False)
        assert stale["error"] == "native_finite_shop_quote_changed", stale
        if stock_screen:
            # Inspect the guest's native SaveSlot_Ref before the shop could copy the balance itself.
            projected_wallet = await wait(guest_run, lambda state: state.get("game", {}).get("native_wallet") == 100_000
                                          and state.get("gameplay", {}).get("shared_wallet") == 100_000
                                          and not state.get("game", {}).get("native_shop_view", {}).get("stock"))
            assert projected_wallet["game"]["native_wallet"] == 100_000
        operation_id = uuid.uuid4().hex
        if shop_view:
            opened = (await act(guest_target, "native-shop-view-fixture", "--target", "open"))["native_shop_view"]
            assert (opened["state"], opened["map"], opened["actor"]) == ("pending", MAPS[merchant], listing["actor"]), opened
            if stock_screen:
                view = await stock_ready(100_000)
                assert view["total"] == view["visible"] == listing["count"], view
                await act(host_target, "native-finite-shop-wallet-fixture", "--amount", "0")
                assert (await stock_ready(0))["sequence"] == opened["sequence"]
                await act(host_target, "native-finite-shop-wallet-fixture", "--amount", "100000")
                assert (await stock_ready(100_000))["sequence"] == opened["sequence"]
                submit = (await act(guest_target, "native-shop-view-fixture", "--target",
                                    f"stock:{offer['source']}:{guid}"))["native_shop_view"]
                assert (submit["guid"], submit["price"]) == (guid, price), submit
            elif shop_pages:
                page_scope, current_page, page_proof = await check_shop_pages(opened, listing, offer)
                view = current_page["view"]
            else:
                visible = await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") == "ready")
                view = visible["game"]["native_shop_view"]
            if not stock_screen:
                offer_index = listing["offers"].index(offer) - (current_page["page"]["offset"] if shop_pages else 0)
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
                page_proof["after_purchase"] = await rendered_page(page_scope, listing, current_page["page"]["offset"],
                                                                  current_page["page"]["render_event"], 100_000 - price)
            elif stock_screen:
                await stock_ready(100_000 - price)
            else:
                await wait(guest_run, lambda state: (panel := state.get("game", {}).get("native_shop_view", {})).get("state") == "ready"
                           and panel.get("total") == listing["count"] - (0 if reusable else 1))
            await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == 100_000 - price)
            records = (await command("events", *guest_target, "--tail", "50"))["events"]
            awarded, = [row for row in records if row["event"] == "native_shop_view_purchased"]
            purchase, operation_id = awarded["native_finite_purchase"], awarded["operation_id"]
            if shop_pages:
                renders = [row for row in records if row["event"] == "native_shop_view_rendered" and row["sequence"] == opened["sequence"]]
                offsets = [0, *(row["page"]["offset"] for row in page_proof["navigation"]), current_page["page"]["offset"]]
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
        if verify_balances:
            assert inventory["owner"] == invite["character_id"] and acquired["source"] == "backpack", inventory
            assert acquired["stackable"] is (not scientist_blank), acquired
            assert stock["offers"] == listing["offers"] and all(row["guid"] != acquire_guid for row in stock["offers"]), stock
            retained_offer, = [row for row in stock["offers"] if row["guid"] == guid]
            assert purchase["merchant_cash_after"] == listing["merchant_cash_before"] + price, purchase
            assert stock["merchant_cash_before"] == stock["merchant_cash_after"] == purchase["merchant_cash_after"], stock
            linked = await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == expected_wallet)
            assert linked["character_id"] == invite["character_id"], linked
            paid_balances = await saved_balances(invite["character_id"])
            expected_balances = {**before_balances, "host": {**before_balances["host"], "cash": expected_wallet}}
            assert paid_balances == expected_balances, (before_balances, paid_balances)
        if scientist_blank:
            await act(host_target, "native-finite-shop-approach", "--target", "host")
            host_listing = (await act(host_target, "native-finite-shop-list"))["native_finite_shop"]
            assert host_listing["actor"] == SCIENTIST_ACTOR and host_listing["offers"] == listing["offers"], host_listing
            denied_host = await act(host_target, "native-finite-shop-buy", "--target", f"{guid}@{price}", expected=False)
            assert denied_host["error"] == "native_scientist_purchase_profile_unavailable", denied_host
        if sell:
            quote = (await act(guest_target, "native-finite-shop-sale-quote", "--target", guid))["native_finite_sale_quote"]
            quote_row, = quote["offers"]
            sale_price = quote_row["price"]
            assert quote_row == dict(offer, source="backpack", price=offer["value"]) and quote["wallet_before"] == expected_wallet, quote
            wrong_price = await act(guest_target, "native-finite-shop-sell", "--target", f"{guid}@{sale_price + 1}", expected=False)
            assert wrong_price["error"] == "native_finite_shop_quote_changed", wrong_price
            sale_id = uuid.uuid4().hex
            if stock_screen:
                submitted_sale = (await act(guest_target, "native-shop-view-fixture", "--target",
                                            f"stock:backpack:{guid}"))["native_shop_view"]
                assert (submitted_sale["state"], submitted_sale["price"]) == ("selling", sale_price), submitted_sale
                await stock_ready(expected_wallet + sale_price)
                events = (await command("events", *guest_target, "--tail", "50"))["events"]
                sold, = [row for row in events if row["event"] == "native_shop_view_sold"]
                sale_id, sale = sold["operation_id"], sold["native_finite_sale"]
            sale_command = ("native-finite-shop-sell", "--target", f"{guid}@{sale_price}", "--operation-id", sale_id)
            if not stock_screen:
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
        if verify_balances:
            rebound = await wait(guest_run, lambda state: state.get("state") == "connected"
                                 and state.get("host_session") == returned["generation"]
                                 and state.get("character_id") == invite["character_id"]
                                 and state.get("gameplay", {}).get("shared_wallet") == expected_wallet)
        await act(host_target, "native-finite-shop-approach")
        after = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        if merchant == "scientist":
            assert len([row for row in after["offers"] if row["class_path"] == CAMERA]) == 1, after
            if scientist_blank:
                restarted_offer, = [row for row in after["offers"] if row["class_path"] == BLANK_CLASS]
                assert (restarted_offer["quantity"], restarted_offer["value"]) == (1, 30), restarted_offer
                assert restarted_offer["guid"] != acquire_guid and after["merchant_cash_before"] == purchase["merchant_cash_after"], after
        elif merchant == "gunsmith":
            assert after["actor"] == GUNSMITH_ACTOR and after["map"] == GUNSMITH, after
            restarted_offer, = [row for row in after["offers"] if row["class_path"] == BALL_AMMO and row["source"] == "shelf"]
            assert (restarted_offer["quantity"], restarted_offer["value"]) == (200, 600), restarted_offer
            assert restarted_offer["guid"] != acquire_guid, restarted_offer
            assert after["merchant_cash_before"] == purchase["merchant_cash_after"], after
        elif not sell and not reusable:
            assert guid not in {row["guid"] for row in after["offers"]}, after
        assert (await act(guest_target, *operation))["native_finite_purchase"] == purchase
        persisted = (await act(guest_target, "native-inventory-list"))["native_inventory"]
        assert [row for row in persisted["items"] if row["guid"] == acquire_guid] == ([] if sell else [acquired]), persisted
        proof = {"purchase": purchase, "inventory_row": acquired, "after_restart_offer_count": after["count"],
                 "stock_merchant_input": shop_view, "stock_shop_screen": stock_screen}
        if stock_screen:
            proof["guest_wallet_before_shop"] = projected_wallet["game"]["native_wallet"]
        if verify_balances:
            assert rebound["game"]["map"] == MAPS[merchant] and persisted["owner"] == invite["character_id"], (rebound, persisted)
            restored_balances = await saved_balances(invite["character_id"])
            assert restored_balances == paid_balances, (restored_balances, paid_balances)
            lane = "scientist_blank" if scientist_blank else "gunsmith"
            proof[lane] = {"actor": listing["actor"], "shelf_offer": offer, "shelf_offer_after_buy": retained_offer,
                           "shelf_offer_after_restart": restarted_offer, "port": int(port), "character": invite["character_id"],
                           "restart_generation": returned["generation"], "merchant_cash_after_restart": after["merchant_cash_before"],
                           "native_balances_before": before_balances, "native_balances_after": paid_balances,
                           "native_balances_restored": restored_balances}
            if scientist_blank:
                proof[lane]["matching_host_catalog"] = host_listing
                proof[lane]["host_blank_purchase_error"] = denied_host["error"]
            if shop_view:
                proof[lane]["stock_interact"] = opened
                proof[lane]["overlay_selected_quote"] = view
            if shop_pages:
                proof["gunsmith"]["shop_pages"] = page_proof
        if merchant == "scientist" and not scientist_blank:
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
            buyback, = [row for row in after["offers"] if row["guid"] == guid]
            assert buyback == dict(quote_row, source="buyback"), buyback
            reclaim_id = uuid.uuid4().hex
            if stock_screen:
                await act(guest_target, "native-shop-view-fixture", "--target", "open")
                await stock_ready(expected_wallet)
                await act(guest_target, "native-shop-view-fixture", "--target", f"stock:buyback:{guid}")
                await stock_ready(expected_wallet - sale_price)
                events = (await command("events", *guest_target, "--tail", "50"))["events"]
                receipt = next(row for row in reversed(events) if row["event"] == "native_shop_view_purchased")
                reclaim_id, reclaimed = receipt["operation_id"], receipt["native_finite_purchase"]
            buyback_command = ("native-finite-shop-buy", "--target", f"{guid}@{sale_price}", "--operation-id", reclaim_id)
            if not stock_screen:
                reclaimed = (await act(guest_target, *buyback_command))["native_finite_purchase"]
            assert reclaimed["offers"] == [buyback] and reclaimed["wallet_after"] == expected_wallet - sale_price, reclaimed
            assert reclaimed["merchant_cash_after"] == purchase["merchant_cash_after"], reclaimed
            assert (await act(guest_target, *buyback_command))["native_finite_purchase"] == reclaimed
            new_stock = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
            assert not any(row["guid"] == guid for row in new_stock["offers"]), new_stock
            restored = (await act(guest_target, "native-inventory-list"))["native_inventory"]
            assert [row for row in restored["items"] if row["guid"] == guid] == [acquired], restored
            proof.update(sale=sale, sale_quote=quote, buyback_after_restart=reclaimed)
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
    options.add_argument("--stock-screen", action="store_true", help="trade using the stock ShopScreen")
    options.add_argument("--shop-pages", action="store_true", help="verify authenticated Gunsmith overlay page navigation when stock spans two pages")
    options.add_argument("--scientist-blank", action="store_true", help="purchase the Scientist's exact guest Blank Data Tape shelf offer")
    options.add_argument("--sell", action="store_true")
    options.add_argument("--merchant", choices=MAPS, default="observatory")
    args = options.parse_args()
    if args.merchant == "scientist" and args.sell:
        options.error("The Scientist smoke checks reusable stock purchases")
    if args.scientist_blank and args.merchant != "scientist":
        options.error("--scientist-blank requires --merchant scientist")
    if args.merchant == "gunsmith" and args.sell:
        options.error("The Gunsmith smoke checks 9mm retention through restart")
    if args.shop_pages and args.merchant != "gunsmith":
        options.error("--shop-pages checks the Gunsmith stock profile")
    if args.shop_pages and args.stock_screen:
        options.error("Overlay page navigation and the stock screen use separate fixture paths")
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved, args.shop_view or args.shop_pages or args.stock_screen,
                       args.sell, args.merchant, shop_pages=args.shop_pages, scientist_blank=args.scientist_blank,
                       stock_screen=args.stock_screen))
