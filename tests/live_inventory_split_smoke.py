"""Verify a native guest ammo split, stock inventory controls, and durable replay."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish.guest_weapon_profiles import NINE_MM_BALL


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"


async def verify(work: Path, vanilla_saved: Path, sort_backpack: bool = False):
    name = ("inventory-sort-" if sort_backpack else "inventory-split-") + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    guest = ("--profile", name)
    run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    guest_flags = ("--headless", "--native-zones", "--native-guest-inventory-view")
    originals = saved_hashes(vanilla_saved)
    evidence, jobs = {}, []

    async def ask(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        raw, errors = await asyncio.wait_for(process.communicate(), 30)
        if not raw or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments[:4]}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await ask("action", *owner, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def enrolled():
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == OBSERVATORY)
        return await wait(run, lambda state: state.get("state") == "hosting"
                          and state.get("game", {}).get("map") == OBSERVATORY
                          and state["game"].get("native_guest_save", {}).get("state") == "bound"
                          and state["game"].get("native_inventory_split_capability") == 1)

    async def page(owner, expected_owner):
        inventory = (await ask("action", *owner, "native-inventory-list"))["native_inventory"]
        assert (inventory["owner"], inventory["offset"], inventory["next_offset"]) == (expected_owner, 0, None), inventory
        return inventory["items"]

    def quantities(rows, tracked):
        ammo = [row for row in rows if row["guid"] in tracked]
        assert all(row["source"] == "backpack" and row["class_path"] == NINE_MM_BALL.item_path
                   and row["stackable"] is True and row["important"] is False for row in ammo), ammo
        exact = {row["guid"]: row["quantity"] for row in ammo}
        assert exact == tracked, (exact, tracked)
        return exact

    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting")
        invitation = await ask("invite", *host, "--name", "Stock inventory tester", "--character", "Ammunition")
        character = invitation["character_id"]
        client = await launch(guest, "guest", "join", *guest, "--invite", invitation["invite_file"], *guest_flags)
        first = await enrolled()
        host_before = await page(host, "host")
        source = uuid.uuid4().hex.upper()
        if sort_backpack:
            weapon = uuid.uuid4().hex.upper()
            seeded_weapon = await ask("action", *host, "native-guest-weapon-seed", "--item-guid", weapon)
            assert seeded_weapon["native_guest_weapon"]["guid"] == weapon, seeded_weapon
        seeded = await ask("action", *host, "native-guest-weapon-seed-ammo", "--item-guid", source)
        assert seeded["native_guest_weapon"]["guid"] == source, seeded
        rows = await page(guest, character)
        quantities(rows, {source: 9})
        if sort_backpack:
            assert [row["guid"] for row in rows] == [weapon, source], rows
            sort_request = ("action", *guest, "native-inventory-sort", "--operation-id", uuid.uuid4().hex)
            sorted_receipt = (await ask(*sort_request))["native_inventory_sort"]
            assert (sorted_receipt["owner"], sorted_receipt["state"], sorted_receipt["count"]) == (character, "sorted", 2), sorted_receipt
            reordered = await page(guest, character)
            assert [row["guid"] for row in reordered] == [source, weapon], reordered
            before = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in rows}
            after = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in reordered}
            assert before == after and await page(host, "host") == host_before
            assert (await ask(*sort_request))["native_inventory_sort"] == sorted_receipt
            stable = (await ask("action", *guest, "native-inventory-sort"))["native_inventory_sort"]
            assert (stable["state"], stable["count"]) == ("already_sorted", 2) and await page(guest, character) == reordered
            evidence["stock_category_sort"] = {"before": rows, "after": reordered, "receipt": sorted_receipt}
        split = ("action", *guest, "native-inventory-split", "--target")
        refused = await ask(*split, f"{source}|4|8", success=False)
        assert refused["error"] == "native_inventory_split_refused_stale_quantity", refused
        quantities(await page(guest, character), {source: 9})
        request = (*split, f"{source}|4|9", "--operation-id", uuid.uuid4().hex)
        receipt = (await ask(*request))["native_inventory_split"]
        created = receipt["new_guid"]
        assert (receipt["owner"], receipt["source_guid"], receipt["remaining"], receipt["split"]) == (character, source, 5, 4), receipt
        assert created != source and (await ask(*request))["native_inventory_split"] == receipt
        conflict = await ask(*split, f"{source}|2|5", "--operation-id", request[-1], success=False)
        assert conflict["error"] == "operation_id_conflict", conflict
        rows = await page(guest, character)
        quantities(rows, {source: 5, created: 4})
        evidence.update(structured=receipt, stale_refusal=refused["error"], conflicting_replay=conflict["error"])

        await ask("action", *guest, "native-inventory-view-fixture")
        await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        key = ("action", *guest, "native-inventory-view-key-fixture", "--target")
        index = next(index for index, row in enumerate(rows) if row["guid"] == source)
        for _ in range(index):
            await ask(*key, "down")
        confirmed = (await ask(*key, "confirm"))["native_guest_inventory_split_view"]
        assert (confirmed["state"], confirmed["guid"], confirmed["count"]) == ("confirming", source, 5), confirmed
        await ask(*key, "back")
        quantities(await page(guest, character), {source: 5, created: 4})
        assert (await ask(*key, "confirm"))["native_guest_inventory_split_view"] == confirmed
        requested = (await ask(*key, "confirm"))["native_guest_inventory_split_view"]
        assert requested == dict(confirmed, state="splitting"), requested
        await wait(guest_run, lambda state: state.get("game", {}).get("native_guest_inventory_split_view") ==
                   dict(requested, state="split"))
        events = (await ask("events", *guest, "--tail", "100"))["events"]
        applied = next(row for row in events if row.get("event") == "native_guest_inventory_stack_split"
                       and row.get("view_sequence") == requested["sequence"])
        ui_created = applied["new_guid"]
        assert ui_created not in {source, created}, applied
        assert (applied["owner"], applied["source_guid"], applied["remaining"], applied["split"]) == (character, source, 3, 2), applied
        expected = {source: 3, created: 4, ui_created: 2}
        quantities(await page(guest, character), expected)
        await ask(*key, "back")
        assert await page(host, "host") == host_before
        evidence["stock_inventory_controls"] = applied
        evidence["host_inventory_unchanged"] = True
        if sort_backpack:
            latest_inventory = await page(guest, character)

        await stop(host, authority)
        await stop(guest, client)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != first["generation"])
        renewed = await ask("action", *host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(guest, "guest-restart", "join", *guest, "--invite", renewed["invite_file"], *guest_flags)
        await enrolled()
        ui_replay = (*split, f"{source}|2|5", "--operation-id", applied["operation_id"])
        assert (await ask(*ui_replay))["native_inventory_split"]["new_guid"] == ui_created
        assert (await ask(*request))["native_inventory_split"] == receipt
        evidence["restart_quantities"] = quantities(await page(guest, character), expected)
        if sort_backpack:
            assert await page(guest, character) == latest_inventory
            assert (await ask(*sort_request))["native_inventory_sort"] == sorted_receipt
            evidence["stock_category_sort"]["replayed_after_restart"] = True
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / "live-inventory-split-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed during the test"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--sort-backpack", action="store_true", help="verify stock sort on a mixed backpack before splitting")
    settings = parser.parse_args()
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved.resolve(), settings.sort_backpack))
