"""Verify exact guest Bomber Jacket equip, refusal, and detached restoration."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish.native_guest_armor import ACTION, CLASS_PATH, DUST_FILTER_PATH, FIXTURE, HELMET_ACTION, effects, helmet_effects
from tools.silverfish.travel import OBSERVATORY, RAIL_YARD


async def verify(work, vanilla_saved, *, stock_input=False, dust_filter=False):
    name = ("stock-guest-helmet-" if dust_filter else "stock-guest-armor-") + uuid.uuid4().hex[:8]
    area = RAIL_YARD if dust_filter else OBSERVATORY
    host = ("--campaign", name)
    client = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla_saved)
    evidence, jobs = {}, []

    async def ask(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(process.communicate(), 30)
        if not output or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def act(owner, name, *arguments, success=True):
        return await ask("action", *owner, name, *arguments, success=success)

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await act(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def enroll():
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("game", {}).get("map") == area)
        return await wait(run, lambda s: s.get("state") == "hosting" and s.get("game", {}).get("map") == area
                          and s.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def inventory(seat, identity):
        page = (await act(seat, "native-inventory-list"))["native_inventory"]
        assert page["owner"] == identity and page["next_offset"] is None, page
        rows = {row["guid"]: row for row in page["items"]}
        assert len(rows) == page["total"], page
        return rows

    def jacket(rows, guid, source):
        row = rows.get(guid)
        assert row and (row["class_path"], row["source"], row["quantity"], row["stackable"]) == (
            CLASS_PATH, source, 1, False
        ), rows
        return row

    async def seed():
        guid = uuid.uuid4().hex.upper()
        result = (await act(host, FIXTURE, "--target", "guest", "--item-guid", guid))["native_guest_armor"]
        assert result["guid"] == guid and result["status"] == "seeded", result
        return guid

    async def equip_from_overlay(guid, key):
        await act(client, "native-inventory-view-fixture")
        rendered = await wait(client_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        selected = rendered["game"].get("native_guest_inventory_equip_view", {})
        for _ in range(rendered["game"]["native_inventory_view"]["count"]):
            if selected.get("guid") == guid:
                break
            response = await act(client, "native-inventory-view-key-fixture", "--target", "down")
            selected = response.get("native_guest_inventory_equip_view", {})
        assert selected.get("guid") == guid, selected
        for expected in ("confirming", "equipped"):
            await act(client, "native-inventory-view-key-fixture", "--target", "confirm")
            await wait(client_run, lambda state: state.get("game", {}).get("native_guest_inventory_equip_view", {}).get(
                "state") == expected)
        journal = (await ask("events", *client, "--tail", "30"))["events"]
        event, = [row for row in journal if row["event"] == "native_guest_inventory_equipped" and row.get(key, {}).get("guid") == guid]
        await act(client, "native-inventory-view-key-fixture", "--target", "back")
        return event

    def projection(state, guid, *, head=False, torso=None):
        key = "native_guest_helmet_projection" if head else "native_guest_armor_projection"
        validate = helmet_effects if head else effects
        return next((row for row in state.get("game", {}).get(key, {}).values()
                     if row.get("ok") and row.get("guid") == guid and row.get("map") == area
                     and row.get("status") in {"reapplied", "already_applied"}
                     and (not head or row.get("torso_profile") == torso) and validate(row)), None)

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    client_flags = ("--headless", "--native-zones")
    if dust_filter:
        host_flags += ("--native-expanded-zones",)
        client_flags += ("--native-guest-inventory-view",)
    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", area, *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await ask("invite", *host, "--name", "Armor tester", "--character", "Coat")
        character = invitation["character_id"]
        guest = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        first = await enroll()
        host_before = await inventory(host, "host")
        initial = await inventory(client, character)
        assert all(row["source"] not in ("armor", "helmet") for row in initial.values()), initial
        evidence["initial_guest_inventory"] = initial

        if dust_filter:
            step = await act(host, pickup.APPROACH, "--target", "guest", "--pickup-source", pickup.DUST_FILTER)
            assert step["actor"] == pickup.SOURCES[pickup.DUST_FILTER][1], step
            source_position = (-8423.943, -30074.585, 264.038)

            def nearby(state, role, center, radius):
                game = state.get("game", {})
                pawn = game.get("pawns", {}).get(role, {})
                position = pawn.get("location")
                source = game.get(pickup.SOURCES_KEY, {}).get(pickup.DUST_FILTER, {})
                return (game.get("map") == area and source.get("state") == "available" and position
                        and len(position) == 3 and math.dist(position, center) <= radius)

            approached = await wait(run, lambda state: nearby(state, "guest", source_position, 240))
            authoritative_position = approached["game"]["pawns"]["guest"]["location"]
            replicated = await wait(client_run, lambda state: nearby(state, "local", authoritative_position, 25))
            replica = replicated["game"]["pawns"]["local"]
            pressed = (await act(client, pickup.STOCK_FIXTURE, "--pickup-source",
                                 pickup.DUST_FILTER))["native_authored_pickup_interaction"]
            assert (pressed["source"], pressed["map"], pressed["entry"], pressed["pawn"]) == (
                pickup.DUST_FILTER, area, "stock", replica["name"],
            ), pressed
            evidence["stock_approach"] = {"authority": authoritative_position, "replica": replica["location"]}

            def claim(state):
                return state.get("campaign", {}).get("world", {}).get("party", {}).get(pickup.STATE_KEY, {}).get(pickup.DUST_FILTER)

            helmet = claim(await wait(run, claim))
            assert (helmet["owner"], helmet["class_path"]) == (character, DUST_FILTER_PATH), helmet
            helmet_guid = helmet["guid"]
            back = (await inventory(client, character))[helmet_guid]
            assert (back["source"], back["quantity"], back["stackable"]) == ("backpack", 1, False), back
            wrong = await act(client, HELMET_ACTION, "--target", "A" * 32, success=False)
            assert wrong["error"] == "native_guest_armor_exact_owned_row_required", wrong
            input_receipt = await equip_from_overlay(helmet_guid, "native_guest_helmet")
            head_applied = input_receipt["native_guest_helmet"]
            assert helmet_effects(head_applied) and head_applied["torso_profile"] is None, head_applied
            replay = (HELMET_ACTION, "--target", helmet_guid, "--operation-id", input_receipt["operation_id"])
            assert (await act(client, *replay))["native_guest_helmet"] == head_applied

            body_guid = await seed()
            body_operation = (ACTION, "--target", body_guid, "--operation-id", uuid.uuid4().hex)
            body_applied = (await act(client, *body_operation))["native_guest_armor"]
            assert body_applied["status"] == "equipped" and effects(body_applied), body_applied
            both_status = await wait(run, lambda state: projection(state, helmet_guid, head=True, torso="bomber"))
            both = projection(both_status, helmet_guid, head=True, torso="bomber")
            saved = await inventory(client, character)
            assert (saved[helmet_guid]["source"], saved[body_guid]["source"]) == ("helmet", "armor"), saved
            assert set(saved) == set(initial) | {helmet_guid, body_guid} and await inventory(host, "host") == host_before
            evidence.update(stock_pickup=pressed, helmet_alone=head_applied, body=body_applied, combined=both)

            await stop(host, authority)
            await stop(client, guest)
            await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
            await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
            renewed = await act(host, "refresh-invite", "--target", invitation["guest_id"])
            assert renewed["character_id"] == character
            await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
            await enroll()
            restored = await wait(run, lambda state: projection(state, helmet_guid, head=True, torso="bomber")
                                  and projection(state, body_guid))
            assert await inventory(client, character) == saved and await inventory(host, "host") == host_before
            assert (await act(client, *replay))["native_guest_helmet"] == head_applied
            assert (await act(client, *body_operation))["native_guest_armor"] == body_applied
            evidence.update(verified=True, restored_helmet=projection(restored, helmet_guid, head=True, torso="bomber"),
                            restored_body=projection(restored, body_guid))
            return

        guid = await seed()
        backpack = await inventory(client, character)
        evidence["backpack_row"] = jacket(backpack, guid, "backpack")
        assert {key: row for key, row in backpack.items() if key != guid} == initial, backpack
        wrong = await act(client, ACTION, "--target", "A" * 32, success=False)
        assert wrong["error"] == "native_guest_armor_exact_owned_row_required", wrong
        if stock_input:
            await act(client, "native-inventory-view-fixture")
            await wait(client_run, lambda state: state.get("game", {}).get("native_guest_inventory_equip_view", {}).get(
                "guid") == guid)
            for expected in ("confirming", "equipped"):
                await act(client, "native-inventory-view-key-fixture", "--target", "confirm")
                await wait(client_run, lambda state: state.get("game", {}).get("native_guest_inventory_equip_view", {}).get(
                    "state") == expected)
            journal = (await ask("events", *client, "--tail", "20"))["events"]
            identified, = [event for event in journal if event["event"] == "native_guest_inventory_equipped"
                           and event.get("native_guest_armor", {}).get("guid") == guid]
            operation = identified["operation_id"]
            evidence["stock_inventory_input"] = identified
        else:
            operation = uuid.uuid4().hex
        equip = (ACTION, "--target", guid, "--operation-id", operation)
        receipt = (await act(client, *equip))["native_guest_armor"]
        assert (receipt["status"], receipt["guid"], receipt["class_path"]) == ("equipped", guid, CLASS_PATH), receipt
        assert effects(receipt), receipt
        worn = await inventory(client, character)
        evidence["equipped_row"] = jacket(worn, guid, "armor")
        assert {key: row for key, row in worn.items() if key != guid} == initial, worn
        assert (await act(client, *equip))["native_guest_armor"] == receipt
        evidence["equip"] = receipt

        other = await seed()
        occupied = await act(client, ACTION, "--target", other, success=False)
        assert occupied["error"] == "native_guest_armor_slot_occupied", occupied
        saved = await inventory(client, character)
        assert jacket(saved, guid, "armor") == worn[guid]
        evidence["blocked_second_jacket"] = jacket(saved, other, "backpack")
        assert await inventory(host, "host") == host_before

        await stop(host, authority)
        await stop(client, guest)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
        renewed = await act(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        await enroll()

        def restored(s):
            return next((row for row in s.get("game", {}).get("native_guest_armor_projection", {}).values()
                         if row.get("ok") and row.get("guid") == guid and row.get("map") == OBSERVATORY
                         and row.get("status") in {"reapplied", "already_applied"} and effects(row)), None)

        restored_status = await wait(run, restored)
        evidence["restored_projection"] = restored(restored_status)
        assert await inventory(client, character) == saved
        assert await inventory(host, "host") == host_before
        assert (await act(client, *equip))["native_guest_armor"] == receipt
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
        kind = "helmet" if dust_filter else "armor"
        (run / f"live-guest-{kind}-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player directory changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--stock-input", action="store_true")
    parser.add_argument("--dust-filter", action="store_true", help="verify the exact railyard helmet through stock inventory input")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), stock_input=args.stock_input, dust_filter=args.dust_filter))
