"""Verify exact guest stock gear equip, refusal, and detached restoration."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status as read_status, wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish import native_authored_pickup as pickup
from tools.silverfish import native_dust_cabinet as cabinet
from tools.silverfish.native_guest_armor import (
    ACTION, BASIC_HELMET_PATH, CLASS_PATH, CONSCRIPT_UNIFORM_PATH, DISGUISE_PROBE, DUST_FILTER_PATH, FIXTURE,
    GOGGLES_PATH, HELMET_ACTION, SEED_PROFILES, STANDARD_INFANTRY_HELMET_PATH, effects, helmet_effects,
)
from tools.silverfish.travel import DEAD_BRIDGE, OBSERVATORY, RAIL_YARD


async def verify(work, vanilla_saved, *, stock_input=False, dust_filter=False, gear_profiles=False, state_disguise=False):
    dust_filter = dust_filter or gear_profiles
    kind = "disguise" if state_disguise else "gear" if gear_profiles else "helmet" if dust_filter else "armor"
    name = f"stock-guest-{kind}-" + uuid.uuid4().hex[:8]
    area = DEAD_BRIDGE if state_disguise else RAIL_YARD if dust_filter else OBSERVATORY
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

    def gear_row(rows, guid, source, class_path=CLASS_PATH):
        row = rows.get(guid)
        assert row and (row["class_path"], row["source"], row["quantity"], row["stackable"]) == (
            class_path, source, 1, False
        ), rows
        return row

    async def seed(profile=None):
        guid = uuid.uuid4().hex.upper()
        extra = ("--kind", profile) if profile else ()
        result = (await act(host, FIXTURE, "--target", "guest", "--item-guid", guid, *extra))["native_guest_armor"]
        if profile:
            assert (result["profile"], result["class_path"]) == (profile, SEED_PROFILES[profile].class_path), result
            gear_row(await inventory(client, character), guid, "backpack", result["class_path"])
            evidence.setdefault("headless_stock_gear_seeds", {})[profile] = result
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
    if dust_filter or state_disguise:
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

        if state_disguise:
            async def recognition():
                return (await act(host, DISGUISE_PROBE, "--target", "guest"))["native_guest_disguise"]

            before = await recognition()
            assert before["host"]["configured"] == before["guest"]["configured"] == 0, before
            uniform = await seed("conscript-uniform")
            helmet = await seed("standard-infantry-helmet")
            goggles = await seed("goggles")
            await equip_from_overlay(uniform, "native_guest_armor")
            armor_only = await recognition()
            assert armor_only["guest"]["configured"] == 0, armor_only
            await equip_from_overlay(helmet, "native_guest_helmet")
            active = await recognition()
            assert active["host"] == before["host"] and active["guest"]["configured"] == 4, active
            assert (active["guest"]["recognized"], active["guest"]["compromised"]) == (4, False), active
            assert active["guest"]["relation"] != active["host"]["relation"], active
            assert active["npc"] == armor_only["npc"] == before["npc"] and active["pawn"] == before["pawn"]
            saved = await inventory(client, character)
            gear_row(saved, uniform, "armor", CONSCRIPT_UNIFORM_PATH)
            gear_row(saved, helmet, "helmet", STANDARD_INFANTRY_HELMET_PATH)
            assert await inventory(host, "host") == host_before
            evidence["stock_recognition"] = {"before": before, "armor_only": armor_only, "active": active}

            await act(client, "drop-link")
            await wait(run, lambda s: any(
                lease.get("character_id") == character and lease.get("state") == "reserved"
                for lease in s.get("campaign", {}).get("leases", ())
            ))
            reconnected = await act(client, "reconnect")
            assert reconnected["resumed"] and reconnected["link_connected"], reconnected
            await enroll()
            resumed = await recognition()
            assert resumed == active, resumed
            evidence["stock_recognition"]["brief_reconnect"] = resumed

            await stop(host, authority)
            await stop(client, guest)
            await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
            await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
            renewed = await act(host, "refresh-invite", "--target", invitation["guest_id"])
            await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
            await enroll()
            await wait(run, lambda s: projection(s, helmet, head=True, torso="conscript-uniform"))
            restored = await recognition()
            assert (restored["guest"], restored["host"]) == (active["guest"], active["host"]), restored
            assert restored["npc"] == active["npc"] and restored["pawn"] != active["pawn"], restored
            assert await inventory(client, character) == saved and await inventory(host, "host") == host_before
            await equip_from_overlay(goggles, "native_guest_helmet")
            broken = await recognition()
            assert (broken["guest"], broken["host"]) == (before["guest"], before["host"]), broken
            assert (broken["npc"], broken["pawn"]) == (restored["npc"], restored["pawn"]), broken
            swapped = await inventory(client, character)
            gear_row(swapped, helmet, "backpack", STANDARD_INFANTRY_HELMET_PATH)
            gear_row(swapped, goggles, "helmet", GOGGLES_PATH)
            assert gear_row(swapped, uniform, "armor", CONSCRIPT_UNIFORM_PATH) == saved[uniform]
            evidence["stock_recognition"].update(restored=restored, incompatible_helmet=broken)
            evidence["verified"] = True
            return

        if dust_filter:
            await wait(run, lambda state: cabinet.observed(state.get("game", {}), "closed") != {})
            await wait(client_run, lambda state: cabinet.observed(state.get("game", {}), "closed") != {})
            blocked = await act(client, pickup.ACTION, "--target", pickup.DUST_FILTER, success=False)
            assert blocked["error"] == "native_authored_pickup_dust_cabinet_closed", blocked
            approach = (await act(host, cabinet.DUST.guest_approach, "--target", "guest"))[cabinet.GUEST_APPROACH_KEY]
            closed = await wait(client_run, lambda state: cabinet.observed(state.get("game", {}), "closed")
                                and math.dist(state["game"]["pawns"]["local"]["location"], approach["location"]) <= 25)
            veto = (await act(client, cabinet.DUST.guest_fixture))[cabinet.GUEST_VETO_KEY]
            assert (veto["map"], veto["actor"], veto["pawn"], veto["state"], veto["epoch"], veto["entry"]) == (
                area, cabinet.ACTOR, closed["game"][cabinet.KEY]["pawn"], "closed", first["zone_epoch"],
                "stock_started_consumed"), veto
            assert cabinet.observed(read_status(run).get("game", {}), "closed"), veto
            opened = (await act(host, cabinet.FIXTURE))[cabinet.KEY]
            assert (opened["state"], opened["actor"], opened["map"]) == ("opened", cabinet.ACTOR, area), opened
            clear = await wait(client_run, lambda state: cabinet.observed(
                state.get("game", {}), "projected").get("epoch") == first["zone_epoch"])
            replica = clear["game"][cabinet.KEY]
            assert opened["doors"] == replica["doors"] == "open", (opened, replica)
            evidence["stock_cabinet"] = {"guest_stock_veto": veto, "authority": opened, "replica": replica}
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

            torso = "hunter-jacket" if gear_profiles else "bomber"
            body_guid = await seed(torso if gear_profiles else None)
            if gear_profiles:
                body_input = await equip_from_overlay(body_guid, "native_guest_armor")
                body_operation_id, body_applied = body_input["operation_id"], body_input["native_guest_armor"]
            else:
                body_operation_id = uuid.uuid4().hex
                body_applied = (await act(client, ACTION, "--target", body_guid, "--operation-id",
                                          body_operation_id))["native_guest_armor"]
            body_operation = (ACTION, "--target", body_guid, "--operation-id", body_operation_id)
            assert body_applied["status"] == "equipped" and effects(body_applied), body_applied
            both_status = await wait(run, lambda state: projection(state, helmet_guid, head=True, torso=torso))
            both = projection(both_status, helmet_guid, head=True, torso=torso)
            saved = await inventory(client, character)
            assert (saved[helmet_guid]["source"], saved[body_guid]["source"]) == ("helmet", "armor"), saved
            assert set(saved) == set(initial) | {helmet_guid, body_guid} and await inventory(host, "host") == host_before
            evidence.update(stock_pickup=pressed, helmet_alone=head_applied, body=body_applied, combined=both)

            worn_helmet = helmet_guid
            swaps = []
            if gear_profiles:
                assert head_applied["voice_filter"] is True and both["voice_filter"] is True, (head_applied, both)
                body_row = gear_row(saved, body_guid, "armor", SEED_PROFILES[torso].class_path)
                for profile, previous_path in (("basic-helmet", DUST_FILTER_PATH), ("goggles", BASIC_HELMET_PATH)):
                    new_helmet = await seed(profile)
                    input_event = await equip_from_overlay(new_helmet, "native_guest_helmet")
                    proof = input_event["native_guest_helmet"]
                    operation = (HELMET_ACTION, "--target", new_helmet, "--operation-id", input_event["operation_id"])
                    assert (proof["profile"], proof["status"], proof["replaced_guid"], proof["replaced_class_path"],
                            proof["torso_profile"], proof["voice_filter"]) == (
                        profile, "swapped", worn_helmet, previous_path, torso, False), proof
                    assert helmet_effects(proof), proof
                    saved = await inventory(client, character)
                    gear_row(saved, new_helmet, "helmet", SEED_PROFILES[profile].class_path)
                    gear_row(saved, worn_helmet, "backpack", previous_path)
                    assert gear_row(saved, body_guid, "armor", SEED_PROFILES[torso].class_path) == body_row, saved
                    assert (await act(client, *operation))["native_guest_helmet"] == proof
                    swaps.append((operation, proof))
                    worn_helmet = new_helmet
                owned_gear = {helmet_guid, body_guid, *(proof["guid"] for _, proof in swaps)}
                assert {guid: row for guid, row in saved.items() if guid not in owned_gear} == initial, saved
                assert set(saved) == set(initial) | owned_gear and await inventory(host, "host") == host_before
                evidence["helmet_swaps"] = [proof for _, proof in swaps]
                evidence["guest_gear_before_restart"] = {guid: saved[guid] for guid in owned_gear}

            await stop(host, authority)
            await stop(client, guest)
            await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
            await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
            renewed = await act(host, "refresh-invite", "--target", invitation["guest_id"])
            assert renewed["character_id"] == character
            await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
            await enroll()
            restored = await wait(run, lambda state: projection(state, worn_helmet, head=True, torso=torso)
                                  and projection(state, body_guid))
            assert await inventory(client, character) == saved and await inventory(host, "host") == host_before
            assert (await act(client, *replay))["native_guest_helmet"] == head_applied
            assert (await act(client, *body_operation))["native_guest_armor"] == body_applied
            for operation, proof in swaps:
                assert (await act(client, *operation))["native_guest_helmet"] == proof
            restored_helmet = projection(restored, worn_helmet, head=True, torso=torso)
            if gear_profiles:
                assert (restored_helmet["class_path"], restored_helmet["voice_filter"]) == (GOGGLES_PATH, False), restored_helmet
                assert await inventory(client, character) == saved
            evidence.update(verified=True, restored_helmet=restored_helmet,
                            restored_body=projection(restored, body_guid))
            return

        guid = await seed()
        backpack = await inventory(client, character)
        evidence["backpack_row"] = gear_row(backpack, guid, "backpack")
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
        evidence["equipped_row"] = gear_row(worn, guid, "armor")
        assert {key: row for key, row in worn.items() if key != guid} == initial, worn
        assert (await act(client, *equip))["native_guest_armor"] == receipt
        evidence["equip"] = receipt

        other = await seed()
        occupied = await act(client, ACTION, "--target", other, success=False)
        assert occupied["error"] == "native_guest_armor_slot_occupied", occupied
        saved = await inventory(client, character)
        assert gear_row(saved, guid, "armor") == worn[guid]
        evidence["blocked_second_jacket"] = gear_row(saved, other, "backpack")
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
        (run / f"live-guest-{kind}-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player directory changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--stock-input", action="store_true")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--dust-filter", action="store_true", help="verify the exact railyard helmet through stock inventory input")
    scenario.add_argument("--gear-profiles", action="store_true",
                          help="verify stock Rail Yard Dust Filter pickup, headless gear seeds, helmet swaps and restart")
    scenario.add_argument("--state-disguise", action="store_true",
                          help="verify guest-only State gear recognition against a stock Dead Bridge NPC and restart")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), stock_input=args.stock_input, dust_filter=args.dust_filter,
                       gear_profiles=args.gear_profiles, state_disguise=args.state_disguise))
