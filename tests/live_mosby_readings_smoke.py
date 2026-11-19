"""Verify the scoped stock Stone Maze tape exchange and durable shared quest settlement."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_authored_pickup as pickup, native_dialogue as dialogue, native_mosby_tape as tape
from tools.silverfish import native_guest_armor as armor
from tools.silverfish import native_mosby_station_view as station_view
from tools.silverfish import native_wire_board
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, RUIN_FIELDS, SCIENTIST_SHOP


TO_MAZE = ((RUIN_FIELDS, "Market_Flood"), (CROSSROADS, "CR_RF"), (DEAD_BRIDGE, "DB_CR"), (tape.STONE_MAZE, "DB_SM"))
TO_SCIENTIST = ((DEAD_BRIDGE, "DB_SM"), (CROSSROADS, "DB_CR"), (RUIN_FIELDS, "CR_RF"), (SCIENTIST_SHOP, "Market_Flood"))


async def exercise(work: Path, vanilla_saved: Path | None, *, stock_pickup=False, stock_station=False, stock_board=False, equip_reward=False):
    name = "stone-maze-readings-" + uuid.uuid4().hex[:8]
    authority, client = ("--campaign", name), ("--profile", name)
    campaign = work / "campaigns" / name
    run, client_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    guest_flags = ("--headless", "--native-zones")
    if equip_reward:
        guest_flags += ("--native-guest-inventory-view",)
    processes, evidence = [], {"routes": []}

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 48)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        result = json.loads(output) if output else {}
        if not result or (process.returncode == 0) != expected:
            label = arguments[3] if len(arguments) > 3 else arguments[0]
            raise RuntimeError(f"Unexpected {label} response: {result.get('error', '-')}")
        return result

    async def action(owner, verb, *arguments, expected=True):
        return await command("action", *owner, verb, *arguments, expected=expected)

    async def launch(kind, owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def stop(process, owner):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0

    async def enrolled(area, previous=None):
        def shared_scope(state):
            epoch = (state.get("generation"), state.get("zone_epoch"))
            game = state.get("game", {})
            return (state.get("state") == "hosting" and game.get("map") == area and epoch != previous
                    and game.get("native_guest_save", {}).get("state") == "bound"
                    and any(row["character_id"] == character and row["state"] == "active"
                            for row in state.get("campaign", {}).get("leases", ())))

        host = await wait(run, shared_scope, timeout=75)
        await wait(client_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"), state.get("game", {}).get("map"))
                   == (character, host["generation"], host["zone_epoch"], area), timeout=75)
        return host

    async def cross(host, destination, portal):
        source = host["game"]["map"]
        door = portal == "Market_Flood"
        recipe = "native-door-position" if door else "native-exit-position"
        evidence["pending_route"] = {"source": source, "destination": destination, "entrance": portal}
        for role in ("host", "guest"):
            await action(authority, recipe, "--target", role, "--exit-position", "near", "--entrance", portal)
        if door:
            await action(authority, "native-door-probe", "--target", "host", "--entrance", portal)
        else:
            await action(authority, recipe, "--target", "host", "--exit-position", "inside", "--entrance", portal)
        arrived = await enrolled(destination, (host["generation"], host["zone_epoch"]))
        evidence["routes"].append({**evidence.pop("pending_route"), "zone_epoch": arrived["zone_epoch"]})
        return arrived

    async def inventory(owner, expected_owner):
        items, offset = {}, 0
        while offset is not None:
            selection = ("--amount" if owner == authority else "--target", str(offset)) if offset else ()
            page = (await action(owner, "native-inventory-list", *selection))["native_inventory"]
            assert (page["owner"], page["offset"]) == (expected_owner, offset), page
            for row in page["items"]:
                assert row["guid"] not in items, row
                items[row["guid"]] = (row["class_path"], row["source"], row["quantity"])
            offset = page["next_offset"]
            assert offset is None or offset == len(items) <= 4096
        return items

    def saved():
        return json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))

    def flags(data):
        root = campaign / "game-user" / "Saved" / "SaveGames"
        frames = {"host": data["world"]["native_host_save"], "guest": data["characters"][character]["state"]["native_guest_save"]}
        assert frames["host"]["slot"] != frames["guest"]["slot"]
        return {role: stock_flags(root, frame, SCIENTIST_SHOP) for role, frame in frames.items()}

    async def identify_board():
        placed = (await action(authority, tape.APPROACH, "--target", "guest"))["native_mosby_tape_approach"]
        report = (await action(client, tape.READINGS_PREFLIGHT))["native_mosby_readings"]
        assert (report["board"], report["pawn"]) == (tape.BOARD, placed["pawn"]), report
        return report

    async def exercise_reward_armor(reward_guid, original_inventory):
        backpack = (armor.HAZOP_CLASS_PATH, "backpack", 1)
        worn = (armor.HAZOP_CLASS_PATH, "armor", 1)
        assert original_inventory[reward_guid] == backpack
        assert not any(row[1] in ("armor", "helmet") for row in original_inventory.values()), original_inventory
        first = (await action(client, armor.ACTION, "--target", reward_guid))["native_guest_armor"]
        assert (first["guid"], first["class_path"], first["status"], first["minimum"]) == (reward_guid, armor.HAZOP_CLASS_PATH, "equipped", 32.0)
        assert armor.effects(first) and await inventory(client, character) == {**original_inventory, reward_guid: worn}

        jacket_guid = uuid.uuid4().hex.upper()
        seeded = (await action(authority, armor.FIXTURE, "--target", "guest", "--item-guid", jacket_guid))["native_guest_armor"]
        assert (seeded["guid"], seeded["status"], seeded["class_path"]) == (jacket_guid, "seeded", armor.CLASS_PATH)
        changed = (await action(client, armor.ACTION, "--target", jacket_guid))["native_guest_armor"]
        assert (changed["guid"], changed["replaced_guid"], changed["status"]) == (jacket_guid, reward_guid, "swapped")
        assert armor.effects(changed) and await inventory(client, character) == {
            **original_inventory, jacket_guid: (armor.CLASS_PATH, "armor", 1),
        }

        await action(client, "native-inventory-view-fixture")
        rendered = await wait(client_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "rendered")
        pane = rendered["game"]["native_inventory_view"]
        selection = rendered["game"]["native_guest_inventory_equip_view"]
        for _ in range(pane["count"]):
            if selection["guid"] == reward_guid:
                break
            selection = (await action(client, "native-inventory-view-key-fixture", "--target", "down"))["native_guest_inventory_equip_view"]
        assert (selection["guid"], selection["state"], selection["sequence"]) == (reward_guid, "selecting", pane["sequence"]), selection
        confirmation = (await action(client, "native-inventory-view-key-fixture", "--target", "confirm"))["native_guest_inventory_equip_view"]
        assert (confirmation["guid"], confirmation["state"], confirmation["sequence"]) == (reward_guid, "confirming", pane["sequence"])
        await action(client, "native-inventory-view-key-fixture", "--target", "confirm")
        await wait(client_run, lambda state: (row := state.get("game", {}).get("native_guest_inventory_equip_view", {})).get("state") == "equipped"
                   and (row.get("guid"), row.get("sequence")) == (reward_guid, pane["sequence"]))
        journal = (await command("events", *client, "--tail", "60"))["events"]
        receipt, = [event for event in journal if event.get("event") == "native_guest_inventory_equipped"
                    and event.get("view_sequence") == pane["sequence"] and event.get("native_guest_armor", {}).get("guid") == reward_guid]
        final = receipt["native_guest_armor"]
        assert (final["status"], final["class_path"], final["replaced_guid"], final["replaced_class_path"], final["minimum"]) == (
            "swapped", armor.HAZOP_CLASS_PATH, jacket_guid, armor.CLASS_PATH, 32.0), final
        assert armor.effects(final)
        exact_items = {**original_inventory, reward_guid: worn, jacket_guid: (armor.CLASS_PATH, "backpack", 1)}
        assert await inventory(client, character) == exact_items
        evidence["armor_equip"] = {"earned": reward_guid, "first_equip": first, "swap_to_bomber": changed, "stock_swap_back": receipt}
        return exact_items

    try:
        host_process = await launch("host", authority, "host", "--port", "0", "--start-zone", SCIENTIST_SHOP, *host_flags)
        await wait(run, lambda frame: frame.get("state") == "hosting" and frame.get("game", {}).get("map") == SCIENTIST_SHOP, timeout=75)
        invite = await command("invite", *authority, "--name", "Research runner", "--character", "Tape Scout")
        character = invite["character_id"]
        guest_process = await launch("join", client, "guest", "--invite", invite["invite_file"], *guest_flags)
        current = await enrolled(SCIENTIST_SHOP)
        port = current["endpoint"]["port"]
        await action(authority, dialogue.APPROACH, "--target", "guest")
        scene = (await action(client, dialogue.OPEN, "--target", dialogue.MOSBY))["native_dialogue"]
        for ordinal in (4, 3, 1, 5, 1, 6, 2):
            scene = (await action(client, dialogue.CHOOSE, "--target", f"{scene['token']}@{ordinal}"))["native_dialogue"]
        assert (scene["effect"], scene["progress"]["quest_step"]) == ("stone_maze_issued", 1)
        early = await identify_board()
        assert early["step_after"] == 1 and early["objective_guid"] is None
        refused = await action(client, "native-quest-turnin", "--target", tape.MOSBY_QUEST, expected=False)
        assert refused["error"] == "native_mosby_readings_owned_tape_required", refused

        source = pickup.SCIENTIST_BLANK_TAPE
        await action(authority, pickup.APPROACH, "--target", "guest", "--pickup-source", source)
        await wait(client_run, lambda status: status.get("game", {}).get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == "available")
        if stock_pickup:
            pulse = (await action(client, pickup.STOCK_FIXTURE, "--pickup-source", source))["native_authored_pickup_interaction"]
            assert pulse["source"] == source and pulse["entry"] == "stock"
            claim_status = await wait(run, lambda status: source in status.get("gameplay", {}).get(pickup.STATE_KEY, {}))
            claimed = claim_status["gameplay"][pickup.STATE_KEY][source]
        else:
            await action(client, "native-authored-pickup-fixture", "--pickup-source", source)
            claimed = (await action(client, pickup.ACTION, "--target", source))["native_authored_pickup_claim"]
        blank = claimed["guid"]
        assert (claimed["owner"], claimed["class_path"]) == (character, tape.BLANK_CLASS), claimed
        assert (await inventory(client, character))[blank] == (tape.BLANK_CLASS, "backpack", 1)
        assert blank not in await inventory(authority, "host")
        evidence["blank"] = claimed

        for destination, portal in TO_MAZE:
            current = await cross(current, destination, portal)
        approach = (await action(authority, tape.APPROACH, "--target", "guest"))["native_mosby_tape_approach"]
        before = (await action(client, tape.PREFLIGHT, "--target", blank))["native_mosby_tape"]
        assert (before["station"], before["pawn"], before["blank_guid"]) == (tape.STATION, approach["pawn"], blank), before
        if stock_station:
            def stationary(status):
                return status.get("game", {}).get(station_view.KEY, {})

            await wait(client_run, lambda status: stationary(status).get("state") in ("armed", "focused"), timeout=15)
            attempts = []
            for variant in (None, 1, 3, 4, 5, 6, 7, 8):
                if variant is not None:
                    try:
                        await action(authority, tape.APPROACH, "--target", "guest", "--amount", str(variant))
                    except RuntimeError as error:
                        if "approach_no_safe_floor" not in str(error):
                            raise
                        attempts.append({"variant": variant, "refusal": str(error)})
                        continue
                try:
                    checked = (await action(client, station_view.FIXTURE))[station_view.KEY]
                    break
                except RuntimeError as error:
                    if "fixture_stock_focus_unavailable" not in str(error):
                        raise
                    attempts.append({"variant": variant, "refusal": str(error)})
            else:
                evidence["stock_station_approach_attempts"] = attempts
                raise RuntimeError("No verified floor position could focus the stock Stone Maze interaction Box")
            assert checked["state"] == "checking" and checked["sequence"] == checked["open"] and checked["blank_guid"] is None
            shown = stationary(await wait(client_run, lambda status: stationary(status).get("state") == "offer", timeout=15))
            assert (shown["sequence"], shown["blank_guid"], shown["station"]) == (checked["sequence"], blank, tape.STATION)
            assert blank in await inventory(client, character)
            pending = (await action(client, station_view.FIXTURE))[station_view.KEY]
            assert (pending["state"], pending["open"], pending["blank_guid"]) == ("trading", checked["open"], blank)
            exchanged = stationary(await wait(client_run, lambda status: stationary(status).get("state") == "exchanged", timeout=26))
            assert (exchanged["sequence"], exchanged["blank_guid"]) == (pending["sequence"], blank)
            receipt = saved()["world"]["party"][tape.EXCHANGES][blank]
            assert receipt["filled_guid"] == exchanged["filled_guid"]
            converted, operation = {"effect": "stone_maze_tape_exchanged", **receipt}, receipt["operation_id"]
            evidence["stock_station"] = {"offer": shown, "trade": exchanged}
        else:
            operation = uuid.uuid4().hex
            converted = await action(client, tape.CONVERT, "--target", blank, "--operation-id", operation)
        assert converted["effect"] == "stone_maze_tape_exchanged", converted
        product = converted["filled_guid"]
        assert product != blank and converted["owner"] == character
        owned = await inventory(client, character)
        assert blank not in owned and owned[product] == (tape.READINGS_CLASS, "backpack", 1)
        replayed = await action(client, tape.CONVERT, "--target", blank, "--operation-id", operation)
        assert (replayed["effect"], replayed["filled_guid"]) == ("stone_maze_tape_already_exchanged", product), replayed
        wrong = await action(client, "native-quest-turnin", "--target", tape.MOSBY_QUEST, expected=False)
        assert wrong["error"] == "native_mosby_tape_exact_zone_unavailable", wrong
        evidence.update(stock_offer=before, exchange=converted, wrong_zone=wrong["error"])

        for destination, portal in TO_SCIENTIST:
            current = await cross(current, destination, portal)
        board = await identify_board()
        assert board["objective_guid"] == product and board["objective_owner"] == "guest", board
        await action(authority, "checkpoint")
        first_flags = flags(saved())
        if stock_board:
            def panel(status):
                return status.get("game", {}).get("native_wire_board", {})

            def matches(readback):
                return (readback.get("map"), readback.get("board"), readback.get("quest")) == (
                    SCIENTIST_SHOP, tape.BOARD, tape.MOSBY_QUEST)

            await wait(client_run, lambda status: panel(status).get("state") in ("armed", "focused"), timeout=15)
            started = (await action(client, native_wire_board.FIXTURE, "--target", tape.MOSBY_QUEST))["native_wire_board"]
            assert matches(started) and started["state"] == "loading" and started["open"] == started["sequence"]
            reviewed = panel(await wait(client_run, lambda state: matches(panel(state)) and (panel(state).get("state"), panel(state).get("sequence"))
                                        == ("ready", started["sequence"]), timeout=16))
            assert product in await inventory(client, character) and flags(saved()) == first_flags
            confirming = (await action(client, native_wire_board.FIXTURE, "--target", tape.MOSBY_QUEST))["native_wire_board"]
            assert matches(confirming) and confirming["state"] == "redeeming" and confirming["open"] == reviewed["open"]
            assert confirming["sequence"] > started["sequence"]
            granted = panel(await wait(client_run, lambda state: matches(panel(state)) and (panel(state).get("state"), panel(state).get("sequence"))
                                       == ("completed", confirming["sequence"]), timeout=32))
            completed = {"effect": "native_quest_changed", **saved()["world"]["party"]["native_quests"][tape.MOSBY_QUEST]}
            evidence["stock_board"] = {"review": reviewed, "turn_in": granted}
        else:
            completed = await action(client, "native-quest-turnin", "--target", tape.MOSBY_QUEST)
        assert (completed["effect"], completed["consumed_tape"], completed["step"], completed["cash"], completed["xp"]) == (
            "native_quest_changed", product, -10, 7500, 3), completed
        assert set(completed["armor"]) == {"host", character} and len(set(completed["armor"].values())) == 2
        host_armor, guest_armor = completed["armor"]["host"], completed["armor"][character]
        host_items, guest_items = await inventory(authority, "host"), await inventory(client, character)
        new_armor = (tape.ARMOR_CLASS, "backpack", 1)
        assert product not in guest_items and product not in host_items and guest_armor not in host_items and host_armor not in guest_items
        assert host_items[host_armor] == guest_items[guest_armor] == new_armor
        gained = flags(saved())
        for role in ("host", "guest"):
            assert gained[role].get("Player_Xp", 0) - first_flags[role].get("Player_Xp", 0) == 3
            assert gained[role].get("Player_Cash", 0) - first_flags[role].get("Player_Cash", 0) == (7500 if role == "host" else 0)
        assert gained["host"].get("Mosby_SM_Readings") == 1 and tape.REWARDS not in saved()["world"]["party"]
        replay = await action(client, "native-quest-turnin", "--target", tape.MOSBY_QUEST)
        assert (replay["effect"], replay["armor"]) == ("native_quest_already_settled", completed["armor"])
        evidence["completed"] = completed
        standing = json.loads((run / "status.json").read_text(encoding="utf-8"))
        if standing["gameplay"]["players"]["host"]["downed"]:
            await action(authority, tape.APPROACH, "--target", "host")
            revival = await action(client, "interact", "--target", "host")
            assert (revival["effect"], revival["target"], revival["health"]) == ("revive", "host", 25.0), revival
            await wait(run, lambda state: state.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is False)
            evidence["revived_host_before_restart"] = revival
        if equip_reward:
            guest_items = await exercise_reward_armor(guest_armor, guest_items)
            assert await inventory(authority, "host") == host_items and flags(saved()) == gained

        await stop(host_process, authority)
        await stop(guest_process, client)
        await launch("host", authority, "host-restart", "--port", str(port), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != current["generation"], timeout=75)
        renewed = await action(authority, "refresh-invite", "--target", invite["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invite["guest_id"], character)
        await launch("join", client, "guest-restart", "--invite", renewed["invite_file"], *guest_flags)
        restarted = await enrolled(SCIENTIST_SHOP, (current["generation"], current["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port
        assert await inventory(authority, "host") == host_items and await inventory(client, character) == guest_items
        if equip_reward:
            def native_hazop(state):
                return next((row for row in state.get("game", {}).get("native_guest_armor_projection", {}).values()
                             if row.get("ok") and (row.get("guid"), row.get("class_path"), row.get("minimum")) == (
                                 guest_armor, armor.HAZOP_CLASS_PATH, 32.0)
                             and row.get("status") in {"reapplied", "already_applied"} and armor.effects(row)), None)

            evidence["armor_equip"]["restored_projection"] = native_hazop(await wait(run, native_hazop))
        restored = await identify_board()
        assert (restored["step_after"], restored["flag_after"], restored["objective_guid"]) == (-10, 1, None), restored
        assert tuple(restored[key] for key in ("cash_after", "xp_after")) == (gained["host"]["Player_Cash"], gained["host"]["Player_Xp"])
        evidence.update(restart={"port": port, "generation": restarted["generation"], "quest": restored}, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(process, owner)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if originals is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / "live-mosby-readings-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if originals is not None:
            assert evidence["original_saves_verified"], "The original Saved directory changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    parser.add_argument("--stock-pickup", action="store_true", help="exercise the stock Interact fixture if installed for Scientist tape")
    parser.add_argument("--stock-station", action="store_true", help="exercise two stock station presses and the exact authenticated offer")
    parser.add_argument("--stock-board", action="store_true", help="review and turn in the exact Scientist contract with stock Interact")
    parser.add_argument("--equip-reward", action="store_true", help="equip the earned HazOp gear and verify both exact swaps and restart")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved, stock_pickup=args.stock_pickup,
                         stock_station=args.stock_station, stock_board=args.stock_board, equip_reward=args.equip_reward))
