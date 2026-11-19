"""Verify one stock merchant purchase and the paired stock saves."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
MAPS = {"observatory": OBSERVATORY,
        "scav-bar": "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
        "black-market": "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
        "scientist": "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"}
CAMERA = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"


async def verify(work: Path, vanilla_saved: Path | None, shop_view: bool, sell: bool, merchant: str) -> None:
    name = "finite-shop-" + uuid.uuid4().hex[:8]
    host_run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    host_target = ("--campaign", name)
    guest_target = ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    launch_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    if merchant in ("scav-bar", "scientist"):
        launch_flags += ("--native-expanded-zones",)
    if shop_view:
        launch_flags += ("--native-shop-view",)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    processes = []

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

    try:
        host = await spawn(host_run, "host-first.txt", host_target, "host", *host_target, "--port", "0",
                           *launch_flags, "--start-zone", MAPS[merchant])
        initial = await wait(host_run, lambda state: state.get("state") == "hosting")
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
            visible = await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") == "ready")
            view = visible["game"]["native_shop_view"]
            for _ in range(listing["offers"].index(offer)):
                view = (await act(guest_target, "native-shop-view-fixture", "--target", "down"))["native_shop_view"]
            assert (view["map"], view["actor"], view["sequence"], view["guid"], view["price"]) == (
                MAPS[merchant], listing["actor"], opened["sequence"], guid, price), view
            confirm = (await act(guest_target, "native-shop-view-fixture", "--target", "enter"))["native_shop_view"]
            assert confirm["state"] == "confirming", confirm
            submit = (await act(guest_target, "native-shop-view-fixture", "--target", "enter"))["native_shop_view"]
            assert submit["state"] == "buying", submit
            if merchant != "scientist":
                await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("total", -1) == listing["count"] - 1)
            await wait(guest_run, lambda state: state.get("gameplay", {}).get("shared_wallet") == 100_000 - price)
            records = (await command("events", *guest_target, "--tail", "50"))["events"]
            awarded, = [row for row in records if row["event"] == "native_shop_view_purchased"]
            purchase, operation_id = awarded["native_finite_purchase"], awarded["operation_id"]
        operation = ("native-finite-shop-buy", "--target", f"{guid}@{price}", "--operation-id", operation_id)
        if not shop_view:
            purchase = (await act(guest_target, *operation))["native_finite_purchase"]
        if merchant == "scientist":
            assert purchase["offer_guid"] == guid and purchase["stock_source"] == "stock_shelf", purchase
            acquire_guid = purchase["offers"][0]["guid"]
            assert acquire_guid != guid, purchase
        else:
            acquire_guid = guid
        assert purchase["wallet_after"] == 100_000 - price and purchase["offers"] == [dict(offer, guid=acquire_guid)], purchase
        assert (await act(guest_target, *operation))["native_finite_purchase"] == purchase
        stock = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        assert (guid in {row["guid"] for row in stock["offers"]}) == (merchant == "scientist"), stock
        inventory = (await act(guest_target, "native-inventory-list"))["native_inventory"]
        acquired, = [row for row in inventory["items"] if row["guid"] == acquire_guid]
        assert acquired["class_path"] == offer["class_path"] and acquired["quantity"] == offer["quantity"], acquired
        expected_wallet = purchase["wallet_after"]
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
        await asyncio.wait_for(host.wait(), 12)
        await spawn(host_run, "host-restart.txt", host_target, "host", *host_target, "--port", port, *launch_flags)
        returned = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != generation
                              and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        assert returned["game"]["native_wallet"] == expected_wallet, returned["game"]
        await act(host_target, "native-finite-shop-approach")
        after = (await act(guest_target, "native-finite-shop-list"))["native_finite_shop"]
        if merchant == "scientist":
            assert len([row for row in after["offers"] if row["class_path"] == CAMERA]) == 1, after
        else:
            assert guid not in {row["guid"] for row in after["offers"]}, after
        assert (await act(guest_target, *operation))["native_finite_purchase"] == purchase
        persisted = (await act(guest_target, "native-inventory-list"))["native_inventory"]
        assert [row for row in persisted["items"] if row["guid"] == acquire_guid] == ([] if sell else [acquired]), persisted
        proof = {"purchase": purchase, "inventory_row": acquired, "after_restart_offer_count": after["count"],
                 "stock_merchant_input": shop_view}
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
        if vanilla_saved:
            assert original == saved_hashes(vanilla_saved)
        print(host_run, flush=True)
    (host_run / "live-finite-shop-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", required=True, type=Path)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--shop-view", action="store_true")
    options.add_argument("--sell", action="store_true")
    options.add_argument("--merchant", choices=MAPS, default="observatory")
    args = options.parse_args()
    if args.merchant == "scientist" and args.sell:
        options.error("The Scientist smoke checks camera acquisition and equip")
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved, args.shop_view, args.sell, args.merchant))
