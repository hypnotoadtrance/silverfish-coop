"""Verify routed guest inventory split, sort, or manual drop."""

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

from tools.silverfish.guest_weapon_profiles import FIREARMS, NINE_MM_BALL, STOCK_AMMO
from tools.silverfish import native_guest_stack_drop as stack_drop
from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as drop_record, native_host_save
from tools.silverfish import native_guest_drop_projection as drop_projection


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
DROP_AMMO = {ammo.symbol: ammo for ammo in STOCK_AMMO}


async def verify(work: Path, vanilla_saved: Path, sort_backpack: bool = False, stock_drop_probe: bool = False,
                 manual_drop: bool = False, *, crossroads_drop: bool = False, drop_map=None, ammo=NINE_MM_BALL):
    area = drop_map or (drop_record.CROSSROADS if crossroads_drop else OBSERVATORY)
    manual_drop = manual_drop or crossroads_drop or drop_map is not None
    expected_quantity = ammo.fixture_units
    mode = ("campaign-stack-drop" if drop_map else "crossroads-stack-drop" if crossroads_drop
            else "inventory-manual-drop" if manual_drop
            else "stock-stack-drop" if stock_drop_probe else "inventory-sort" if sort_backpack else "inventory-split")
    name = mode + "-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    guest = ("--profile", name)
    run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    if drop_map:
        host_flags += ("--native-expanded-zones",)
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
                   and state.get("game", {}).get("map") == area
                   and (not sort_backpack or (state.get("gameplay_fresh") and
                        state.get("gameplay", {}).get("native_inventory_sort_capability") == 1)))
        return await wait(run, lambda state: state.get("state") == "hosting"
                          and state.get("game", {}).get("map") == area
                          and state["game"].get("native_guest_save", {}).get("state") == "bound"
                          and state["game"].get(stack_drop.CAPABILITY if stock_drop_probe
                                                else "native_inventory_split_capability") == 1)

    async def page(owner, expected_owner):
        inventory = (await ask("action", *owner, "native-inventory-list"))["native_inventory"]
        assert (inventory["owner"], inventory["offset"], inventory["next_offset"]) == (expected_owner, 0, None), inventory
        return inventory["items"]

    def quantities(rows, tracked):
        ammo = [row for row in rows if row["guid"] in tracked]
        assert all(row["source"] == "backpack" and row["class_path"] == ammo.item_path
                   and row["stackable"] is True and row["important"] is False for row in ammo), ammo
        exact = {row["guid"]: row["quantity"] for row in ammo}
        assert exact == tracked, (exact, tracked)
        return exact

    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", area, *host_flags)
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
        if ammo != NINE_MM_BALL:
            weapon = uuid.uuid4().hex.upper()
            kind = next(profile.kind for profile in FIREARMS if profile.headless_seed and profile.magazine
                        and profile.magazine.ammo == ammo)
            await ask("action", *host, "native-guest-weapon-seed", "--kind", kind, "--item-guid", weapon)
            await ask("action", *guest, "native-guest-weapon-equip", "--target", weapon)
        seeded = await ask("action", *host, "native-guest-weapon-seed-ammo", "--item-guid", source)
        assert seeded["native_guest_weapon"]["guid"] == source, seeded
        rows = await page(guest, character)
        quantities(rows, {source: expected_quantity})
        if manual_drop:
            await stop(host, authority)
            await stop(guest, client)
            normal_flags = tuple(flag for flag in host_flags if flag != "--fixtures")
            await launch(host, "host-manual-drop", "host", *host, "--port", str(first["endpoint"]["port"]), *normal_flags)
            normal = await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != first["generation"]
                                and state.get("game", {}).get(drop_record.CAPABILITY) == 1)
            assert stack_drop.CAPABILITY not in normal["game"], normal["game"]
            renewed = await ask("action", *host, "refresh-invite", "--target", invitation["guest_id"])
            await launch(guest, "guest-manual-drop", "join", *guest, "--invite", renewed["invite_file"], *guest_flags)
            await enrolled()
            await wait(guest_run, lambda state: state.get("gameplay_fresh")
                       and state.get("gameplay", {}).get(drop_record.CAPABILITY) == 1)
            assert await page(guest, character) == rows
            denied = await ask("action", *host, stack_drop.ACTION, "--target", "guest", "--item-guid", source,
                               "--amount", str(expected_quantity), success=False)
            assert denied["error"] == "native_guest_stack_drop_fixture_unavailable", denied
            evidence["diagnostic_probe_refused_without_fixture_scope"] = True

            await ask("action", *guest, "native-inventory-view-fixture")
            await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
            key = ("action", *guest, "native-inventory-view-key-fixture", "--target")
            for _ in range(next(index for index, row in enumerate(rows) if row["guid"] == source)):
                await ask(*key, "down")
            confirmation = (await ask(*key, "drop"))[drop_record.VIEW]
            assert (confirmation["state"], confirmation["guid"], confirmation["class_path"], confirmation["count"]) == (
                "confirming", source, ammo.item_path, expected_quantity), confirmation
            assert (await ask(*key, "back"))[drop_record.VIEW] == {}
            assert await page(guest, character) == rows and await page(host, "host") == host_before
            evidence["cancelled_confirmation"] = confirmation

            assert (await ask(*key, "drop"))[drop_record.VIEW] == confirmation
            pending = (await ask(*key, "confirm"))[drop_record.VIEW]
            assert pending == {**confirmation, "state": "dropping"}, pending
            final = await wait(guest_run, lambda state: state.get("game", {}).get(drop_record.VIEW) == {**pending, "state": "dropped"})
            evidence["rendered_drop_result"] = final["game"][drop_record.VIEW]
            events = (await ask("events", *guest, "--tail", "100"))["events"]
            commits = [event for event in events if event["event"] == "native_guest_inventory_stack_dropped"
                       and event["view_sequence"] == pending["sequence"]]
            assert len(commits) == 1, commits
            committed, = commits
            assert (committed["state"], committed["owner"], committed["guid"], committed["quantity"]) == (
                "dropped", character, source, expected_quantity), committed
            operation = committed["operation_id"]
            assert committed["drop_id"] == operation
            evidence["inventory_drop"] = committed

            campaign = run.parent
            checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
            native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
            escrow = checkpoint["world"]["party"][drop_record.STATE][operation]
            assert (escrow["guid"], escrow["quantity"], escrow["map"], escrow["claim_id"]) == (
                source, expected_quantity, area, None)
            assert escrow["escrow"]["map"] == area
            assert await page(guest, character) == [row for row in rows if row["guid"] != source]
            replay = (await ask("action", *guest, drop.DROP, "--target", f"{source}|{expected_quantity}",
                                "--operation-id", operation))[drop.RESULT]
            assert replay == {key: committed[key] for key in replay}, replay
            assert await page(host, "host") == host_before
            if drop_map:
                await stop(*jobs[-1])
                await stop(*jobs[-2])
                await launch(host, "host-reclaim", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
                await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != normal["generation"])
                renewed = await ask("action", *host, "refresh-invite", "--target", invitation["guest_id"])
                await launch(guest, "guest-reclaim", "join", *guest, "--invite", renewed["invite_file"], *guest_flags)
                await enrolled()
                frame = await wait(run, lambda state: state.get("game", {}).get(drop_projection.KEY, {}).get("drop_id")
                                   == operation)
                before = frame["game"][drop_projection.KEY]
                assert drop_record.actor_class_path(area, before["actor"]) == ammo.item_path, before
                press = (await ask("action", *host, drop_projection.FIXTURE, "--target", operation))[drop_projection.KEY]
                assert press == {**before, "sequence": before["sequence"] + 1}, (before, press)
                settled = await wait(run, lambda state: state.get("campaign", {}).get("world", {}).get("party", {})
                                     .get(drop_record.STATE, {}).get(operation, {}).get("claim_id") is not None)
                tombstone = settled["campaign"]["world"]["party"][drop_record.STATE][operation]
                assert tombstone["claimed_by"] == "host" and "escrow" not in tombstone
                quantities(await page(host, "host"), {source: expected_quantity})
                assert source not in {row["guid"] for row in await page(guest, character)}
                rejected = await ask("action", *guest, drop.RECLAIM, "--target", operation, success=False)
                assert rejected["error"] == "native_guest_stack_drop_already_reclaimed", rejected
                evidence["host_stock_interaction_after_restart"] = press
                evidence["reclaimed"] = tombstone
            if crossroads_drop:
                await ask(*key, "back")
                observed = await wait(guest_run, lambda state: state.get("gameplay_fresh")
                                      and state.get("game", {}).get(drop_projection.KEY, {}).get("drop_id") == operation)
                before = observed["game"][drop_projection.KEY]
                assert before["map"] == area and drop_record.actor_matches(area, before["actor"]), before
                press = (await ask("action", *guest, drop_projection.FIXTURE, "--target", operation))[drop_projection.KEY]
                assert press == {**before, "sequence": before["sequence"] + 1}, (before, press)
                evidence["crossroads_stock_interaction"] = press
                await wait(guest_run, lambda state: state.get("gameplay_fresh")
                           and state.get("gameplay", {}).get(drop_record.STATE) == {}
                           and drop_projection.KEY not in state.get("game", {}))
                events = (await ask("events", *guest, "--tail", "100"))["events"]
                claims = [event for event in events if event["event"] == "local_interaction"
                          and event.get(drop.RESULT, {}).get("drop_id") == operation]
                assert len(claims) == 1, claims
                claim, = claims
                assert claim[drop.RESULT] == {**replay, "state": "reclaimed", "claimed_by": character}, claim
                checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
                native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
                tombstone = checkpoint["world"]["party"][drop_record.STATE][operation]
                assert tombstone == {**{key: value for key, value in escrow.items() if key != "escrow"},
                                     "claim_id": claim["operation_id"]}
                assert await page(guest, character) == rows and await page(host, "host") == host_before
                evidence["crossroads_reclaim"] = claim
            evidence["verified"] = True
            return
        if stock_drop_probe:
            arguments = (stack_drop.ACTION, "--target", "guest", "--item-guid", source, "--amount")
            denied = await ask("action", *guest, *arguments, "9", success=False)
            stale = await ask("action", *host, *arguments, "8", success=False)
            evidence.update(guest_request_refused=denied["error"], stale_quantity_refused=stale["error"])
            assert stale["error"] == "native_guest_stack_drop_stock_9mm_row_unqualified", stale
            assert await page(guest, character) == rows and await page(host, "host") == host_before

            receipt = (await ask("action", *host, *arguments, "9"))[stack_drop.KEY]
            evidence["native_stock_spawn"] = receipt
            assert (receipt["owner"], receipt["map"], receipt["guid"], receipt["quantity"], receipt["ok"], receipt["status"]) == (
                character, OBSERVATORY, source, 9, True, stack_drop.SUCCESS), receipt
            assert receipt["new_guid"] != source and all(row["guid"] != receipt["new_guid"] for row in rows), receipt
            assert await page(guest, character) == rows and await page(host, "host") == host_before
            resumed = await ask("action", *guest, "drop-link")
            assert resumed.get("link_connected") is False, resumed
            resumed = await ask("action", *guest, "reconnect")
            assert resumed.get("resumed") is True, resumed
            await enrolled()
            assert await page(guest, character) == rows and await page(host, "host") == host_before
            evidence.update(unchanged_guest_inventory=rows, unchanged_host_inventory=host_before,
                            resumed_exact_inventory=True, verified=True)
            return
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
        report = ("live-campaign-stack-drop-verification.json" if drop_map else
                  "live-crossroads-stack-drop-verification.json" if crossroads_drop else
                  "live-inventory-manual-drop-verification.json" if manual_drop else
                  "live-stock-stack-drop-verification.json" if stock_drop_probe else "live-inventory-split-verification.json")
        (run / report).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed during the test"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sort-backpack", action="store_true", help="verify stock sort on a mixed backpack before splitting")
    modes.add_argument("--stock-drop-probe", action="store_true", help="verify one stock 9mm actor born and retired in the same tick")
    modes.add_argument("--manual-drop", action="store_true", help="qualify regular co-op inventory Drop after isolated seeding")
    modes.add_argument("--crossroads-drop", action="store_true", help="qualify normal 9mm Drop and stock reclaim in CrossRoads")
    modes.add_argument("--campaign-drop-map", help="verify a guest drop and host pickup after restart on this campaign map")
    parser.add_argument("--ammo", choices=DROP_AMMO, default=NINE_MM_BALL.symbol)
    settings = parser.parse_args()
    if ((settings.campaign_drop_map and not drop_record.supported_map(settings.campaign_drop_map))
            or (settings.ammo != NINE_MM_BALL.symbol and not settings.campaign_drop_map)):
        parser.error("Use an authored --campaign-drop-map for the selected ammunition")
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved.resolve(), settings.sort_backpack,
                       settings.stock_drop_probe, settings.manual_drop, crossroads_drop=settings.crossroads_drop,
                       drop_map=settings.campaign_drop_map, ammo=DROP_AMMO[settings.ammo]))
