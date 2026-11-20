"""Verify the scoped stock Stone Maze tape exchange and durable shared quest settlement."""

import argparse
import asyncio
import json
import os
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
from tools.silverfish import native_prototype_drill as drill
from tools.silverfish import native_wire_board
from tools.silverfish.travel import CISTERN, CROSSROADS, DEAD_BRIDGE, RUIN_FIELDS, SCIENTIST_SHOP, SCI_TUNNEL


TO_MAZE = ((RUIN_FIELDS, "Market_Flood"), (CROSSROADS, "CR_RF"), (DEAD_BRIDGE, "DB_CR"), (tape.STONE_MAZE, "DB_SM"))
TO_SCIENTIST = ((DEAD_BRIDGE, "DB_SM"), (CROSSROADS, "DB_CR"), (RUIN_FIELDS, "CR_RF"), (SCIENTIST_SHOP, "Market_Flood"))
TO_DRILL_HEAD = ((RUIN_FIELDS, "Market_Flood"), (CROSSROADS, "CR_RF"), (CISTERN, "CR_Cstrn"), (SCI_TUNNEL, "Cistern_Sci"))
FROM_DRILL_HEAD = ((CISTERN, "Cistern_Sci"), (CROSSROADS, "CR_Cstrn"), (RUIN_FIELDS, "CR_RF"), (SCIENTIST_SHOP, "Market_Flood"))


async def exercise(work: Path, vanilla_saved: Path | None, *, stock_pickup=False, stock_station=False, stock_board=False,
                   equip_reward=False, prototype_dialogue=False, prototype_pickup=False, prototype_chamber_staged=False,
                   prototype_redeem=False):
    if prototype_redeem:
        prototype_pickup = prototype_chamber_staged = True
    name = "stone-maze-readings-" + uuid.uuid4().hex[:8]
    authority, client = ("--campaign", name), ("--profile", name)
    campaign = work / "campaigns" / name
    run, client_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    if prototype_dialogue:
        host_flags += ("--max-guests", "2")
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
        environment = os.environ.copy()
        chamber_fixture = "SILVERFISH_COOP_DRILL_HEAD_CHAMBER_FIXTURE"
        environment.pop(chamber_fixture, None)
        if kind == "host" and prototype_chamber_staged:
            environment[chamber_fixture] = "1"
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                             env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
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
        door = portal in ("Market_Flood", "CR_Cstrn", "Cistern_Sci")
        recipe = "native-door-position" if door else "native-exit-position"
        evidence["pending_route"] = {"source": source, "destination": destination, "entrance": portal}
        for role in ("host", "guest"):
            await action(authority, recipe, "--target", role, "--exit-position", "near", "--entrance", portal)
        if door:
            if (source, destination) == (CISTERN, SCI_TUNNEL):
                locked = (await action(authority, "native-door-probe", "--target", "host", "--entrance", portal))["native_door"]
                assert locked["decision"] == "host_locked", locked
                attempts = []
                for number in (10, 0, 11, 10, 7, 1, 0, 3, 8, 11):
                    press = (await action(authority, "native-cistern-keypad", "--amount", str(number)))["native_cistern_keypad"]
                    attempts.append(press)
                    changed = len(attempts) == 10
                    assert (press["locked"], press["unlocked"]) == (not changed, changed), press
                    await asyncio.sleep(.25)
                evidence["prototype_pickup"]["stock_keypad"] = attempts
            await action(authority, "native-door-probe", "--target", "host", "--entrance", portal)
        else:
            await action(authority, recipe, "--target", "host", "--exit-position", "inside", "--entrance", portal)
        arrived = await enrolled(destination, (host["generation"], host["zone_epoch"]))
        evidence["routes"].append({**evidence.pop("pending_route"), "zone_epoch": arrived["zone_epoch"]})
        return arrived

    async def restart_pair(host_process, guest_process, previous, area, *, label="restart"):
        await stop(host_process, authority)
        await stop(guest_process, client)
        next_host = await launch("host", authority, "host-" + label, "--port", str(port), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != previous["generation"], timeout=75)
        renewed = await action(authority, "refresh-invite", "--target", invite["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invite["guest_id"], character)
        next_guest = await launch("join", client, "guest-" + label, "--invite", renewed["invite_file"], *guest_flags)
        frame = await enrolled(area, (previous["generation"], previous["zone_epoch"]))
        assert frame["endpoint"]["port"] == port
        return next_host, next_guest, frame

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

    def flags(data, area=SCIENTIST_SHOP):
        root = campaign / "game-user" / "Saved" / "SaveGames"
        frames = {"host": data["world"]["native_host_save"], "guest": data["characters"][character]["state"]["native_guest_save"]}
        assert frames["host"]["slot"] != frames["guest"]["slot"]
        return {role: stock_flags(root, frame, area) for role, frame in frames.items()}

    async def identify_board():
        placed = (await action(authority, tape.APPROACH, "--target", "guest"))["native_mosby_tape_approach"]
        report = (await action(client, tape.READINGS_PREFLIGHT))["native_mosby_readings"]
        assert (report["board"], report["pawn"]) == (tape.BOARD, placed["pawn"]), report
        return report

    async def accept_prototype():
        sibling_name = name + "-sibling"
        sibling, sibling_run = ("--profile", sibling_name), work / "clients" / sibling_name / "run"
        second = await command("invite", *authority, "--name", "Research reader", "--character", "Drill Scout")
        assert second["character_id"] != character
        sibling_process = await launch("join", sibling, "guest-sibling", "--invite", second["invite_file"], *guest_flags)
        await wait(sibling_run, lambda state: (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"))
                   == (second["character_id"], current["generation"], current["zone_epoch"])
                   and state.get("state") == "connected", timeout=75)
        await wait(run, lambda state: state.get("gameplay", {}).get("players", {}).get("guest2", {}).get("character_id")
                   == second["character_id"] and (
                       state.get("game", {}).get("native_guest_save_members", {}).get(
                           state["gameplay"].get("native_pawns", {}).get("guest2"), {}).get("state")) == "bound", timeout=75)

        def view(state):
            return state.get("game", {}).get("native_dialogue_view", {})

        async def press(target, row):
            pending = (await action(client, dialogue.FIXTURE, "--target", target))["native_dialogue_view"]
            outcome = ("open", row) if row else ("closed", "-")
            observed = view(await wait(client_run, lambda status: (
                view(status).get("state"), view(status).get("row"), view(status).get("sequence"), view(status).get("pawn"))
                == (*outcome, pending["sequence"], pending["pawn"]), timeout=25))
            evidence["prototype_dialogue"]["stock_presses"].append(
                {key: observed[key] for key in ("sequence", "row", "state")})
            return observed

        evidence["prototype_dialogue"] = {"stock_presses": []}
        await action(authority, dialogue.APPROACH, "--target", "guest2")
        await action(authority, dialogue.APPROACH, "--target", "guest")
        await wait(client_run, lambda state: view(state).get("state") == "armed", timeout=25)
        await press("open", dialogue.READINGS)
        observed = flags(saved())["host"]
        assert observed == {**gained["host"], "Mosby_SM_Readings": 2, "Topic_MicroDrill": 1}
        first_offer = await press("1", dialogue.EQUIPMENT)
        second_offer = (await action(sibling, dialogue.OPEN, "--target", dialogue.MOSBY))["native_dialogue"]
        assert second_offer["row"] == dialogue.GREET and second_offer["progress"]["prototype_step"] is None
        for ordinal, row in ((4, dialogue.QUESTIONS), (7, dialogue.EQUIPMENT)):
            second_offer = (await action(sibling, dialogue.CHOOSE, "--target", f"{second_offer['token']}@{ordinal}"))["native_dialogue"]
            assert second_offer["row"] == row, second_offer
        borrowed = await action(sibling, dialogue.CHOOSE, "--target", f"{first_offer['token']}@2", expected=False)
        assert borrowed["error"] == "native_dialogue_choice_expired"

        await press("2", None)
        record = {"quest_id": dialogue.PROTOTYPE_QUEST, "step": 1, "actor": character}
        await wait(run, lambda state: state.get("gameplay", {}).get("native_quests", {}).get(dialogue.PROTOTYPE_QUEST) == record)
        replay = (await action(sibling, dialogue.CHOOSE, "--target", f"{second_offer['token']}@2"))["native_dialogue"]
        assert (replay["state"], replay["effect"], replay["progress"]["prototype_step"]) == (
            "closed", "prototype_drill_already_present", 1)
        assert flags(saved())["host"] == observed and flags(saved())["guest"] == gained["guest"]
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        effects = [(event["effect"], event["character"]) for event in events
                   if event.get("event") == "native_dialogue_committed"
                   and event.get("effect") in ("readings_opened", "prototype_drill_issued")]
        assert effects == [("readings_opened", character), ("prototype_drill_issued", character)]
        evidence["prototype_dialogue"].update(issued=record, sibling=replay["effect"], committed=effects)
        await stop(sibling_process, sibling)

    async def collect_prototype(initial, host_process, guest_process, host_items, guest_items):
        await action(authority, dialogue.APPROACH, "--target", "guest")
        scene = (await action(client, dialogue.OPEN, "--target", dialogue.MOSBY))["native_dialogue"]
        assert (scene["row"], scene["effect"], scene["progress"]["topic_micro_drill"]) == (
            dialogue.READINGS, "readings_opened", 1), scene
        for ordinal in (1, 2):
            scene = (await action(client, dialogue.CHOOSE, "--target", f"{scene['token']}@{ordinal}"))["native_dialogue"]
        assert (scene["effect"], scene["progress"]["prototype_step"]) == ("prototype_drill_issued", 1), scene
        evidence["prototype_pickup"] = {
            "issued": scene["progress"],
            "scope": "headless_chamber_staged_stock_input" if prototype_chamber_staged else "stock_keypad_and_map_entry",
        }
        current = initial
        for destination, portal in TO_DRILL_HEAD:
            current = await cross(current, destination, portal)

        source = pickup.PROTOTYPE_DRILL
        _, actor, item_class = pickup.SOURCES[source]
        await action(authority, "checkpoint")
        before = flags(saved(), SCI_TUNNEL)
        assert before["host"].get("Topic_MicroDrill") == 1

        def projected(state, word):
            return state.get("game", {}).get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == word

        if not prototype_chamber_staged:
            refused = await action(authority, pickup.APPROACH, "--target", "guest", "--pickup-source", source, expected=False)
            assert refused["error"] == f"authored_pickup_fixture_{source}_requires_local_catwalk", refused
            assert await inventory(authority, "host") == host_items and await inventory(client, character) == guest_items
            await wait(run, lambda state: projected(state, "available"))
            await wait(client_run, lambda state: projected(state, "available"))
            evidence["prototype_pickup"]["far_approach_refusal"] = refused["error"]
            return

        approach = await action(authority, pickup.APPROACH, "--target", "guest", "--pickup-source", source)
        await wait(run, lambda state: projected(state, "available"))
        await wait(client_run, lambda state: projected(state, "available"))
        press = (await action(client, pickup.STOCK_FIXTURE, "--pickup-source", source))["native_authored_pickup_interaction"]
        assert (press["source"], press["map"], press["entry"]) == (source, SCI_TUNNEL, "stock"), press
        committed = await wait(run, lambda state: source in state.get("gameplay", {}).get(pickup.STATE_KEY, {})
                               and projected(state, "claimed"))
        claimed = committed["gameplay"][pickup.STATE_KEY][source]
        assert (claimed["actor"], claimed["class_path"], claimed["owner"], claimed["topic_micro_drill"]) == (
            actor, item_class, character, 1), claimed
        await wait(client_run, lambda state: projected(state, "claimed"))
        denied = await action(authority, pickup.ACTION, "--target", source, expected=False)
        assert denied["error"] == "native_authored_pickup_already_claimed", denied
        expected_items = {**guest_items, claimed["guid"]: (item_class, "backpack", 1)}
        assert await inventory(authority, "host") == host_items and await inventory(client, character) == expected_items
        after = flags(saved(), SCI_TUNNEL)
        for role in ("host", "guest"):
            for key in ("Player_Cash", "Player_Xp", "Topic_MicroDrill", "Mosby_DrillTest"):
                assert after[role].get(key) == before[role].get(key), (before, after)
        evidence["prototype_pickup"].update(stock_press=press, headless_chamber_approach=approach, claim=claimed,
                                            rival=denied["error"], stock_flags=after)

        next_host, next_guest, restored = await restart_pair(host_process, guest_process, current, SCI_TUNNEL, label="drill-restart")
        await wait(run, lambda state: projected(state, "claimed"))
        await wait(client_run, lambda state: projected(state, "claimed"))
        assert await inventory(authority, "host") == host_items and await inventory(client, character) == expected_items
        durable = saved()
        assert durable["world"]["party"][pickup.STATE_KEY][source] == claimed
        assert durable["world"]["party"]["native_quests"][pickup.PROTOTYPE_QUEST]["step"] == 1
        assert flags(durable, SCI_TUNNEL)["host"].get("Topic_MicroDrill") == 1
        evidence["prototype_pickup"]["restart"] = {"port": port, "epoch": restored["zone_epoch"], "guid": claimed["guid"]}
        if prototype_redeem:
            await redeem_prototype(restored, next_host, next_guest, claimed, host_items, expected_items)

    async def redeem_prototype(current, host_process, guest_process, claimed, host_items, guest_items):
        proof = evidence["prototype_redemption"] = {"objective_guid": claimed["guid"]}
        for destination, portal in FROM_DRILL_HEAD:
            current = await cross(current, destination, portal)
        assert await inventory(authority, "host") == host_items and await inventory(client, character) == guest_items
        placed = (await action(authority, tape.APPROACH, "--target", "guest"))["native_mosby_tape_approach"]
        assert placed["target"] == drill.BOARD
        await action(authority, "checkpoint")
        before = flags(saved())
        assert before["host"].get("Mosby_DrillTest", 0) == 0

        def panel(status):
            return status.get("game", {}).get("native_wire_board", {})

        def matches(row):
            return (row.get("map"), row.get("board"), row.get("quest")) == (SCIENTIST_SHOP, drill.BOARD, drill.QUEST_ID)

        async def press():
            return (await action(client, native_wire_board.FIXTURE, "--target", drill.QUEST_ID))["native_wire_board"]

        async def rendered(state, request):
            def matching(status):
                row = panel(status)
                return row if matches(row) and (row.get("state"), row.get("sequence"), row.get("open")) == (
                    state, request["sequence"], request["open"]) else None

            return matching(await wait(client_run, matching, timeout=34 if state == "completed" else 16))

        def ready_stock(status, step, flag):
            row = status.get("game", {}).get(drill.KEY, {})
            return ((row.get("operation"), row.get("map"), row.get("pawn"), row.get("board"), row.get("quest_id"),
                     row.get("step_after"), row.get("flag_after")) ==
                    (drill.PREFLIGHT, SCIENTIST_SHOP, placed["pawn"], drill.BOARD, drill.QUEST_ID, step, flag))

        await wait(client_run, lambda status: panel(status).get("state") in ("armed", "focused"), timeout=15)
        started = await press()
        assert matches(started) and started["state"] == "loading" and started["open"] == started["sequence"]
        offer = await rendered("ready", started)
        offered = (await wait(run, lambda status: ready_stock(status, 1, 0)))["game"][drill.KEY]
        assert (offered["objective_guid"], offered["objective_owner"], offered["station"]) == (
            claimed["guid"], "guest", drill.STATION), offered
        assert offered["eligible"] is True and offered["drill_tested_after"] is False and offered["tape_after"] is False
        assert await inventory(client, character) == guest_items and flags(saved()) == before
        pending = await press()
        assert matches(pending) and pending["state"] == "redeeming" and pending["open"] == offer["open"]
        assert pending["sequence"] > offer["sequence"]
        proof["stock_board"] = {"review": offer, "turn_in": await rendered("completed", pending), "stock_preflight": offered}
        durable = saved()
        gained = flags(durable)
        record = durable["world"]["party"]["native_quests"][drill.QUEST_ID]
        assert (record["actor"], record["step"], record["cash"], record["xp"], record["consumed_drill_head"]) == (
            character, -10, 10000, 3, claimed["guid"]), record
        assert set(record["recipients"]) == {"host", character} == set(record["armor"])
        assert len(set(record["armor"].values())) == 2
        assert record["test_results"] == {"station": drill.STATION, "class_path": drill.TAPE_CLASS}
        assert drill.REWARDS not in durable["world"]["party"]
        proof["receipt"] = record

        suit = (drill.ARMOR_CLASS, "backpack", 1)
        expected_host = {**host_items, record["armor"]["host"]: suit}
        expected_guest = {guid: row for guid, row in guest_items.items() if guid != claimed["guid"]}
        expected_guest[record["armor"][character]] = suit
        assert await inventory(authority, "host") == expected_host
        assert await inventory(client, character) == expected_guest
        for role in ("host", "guest"):
            assert gained[role].get("Player_Xp", 0) == before[role].get("Player_Xp", 0) + drill.XP
            assert gained[role].get("Player_Cash", 0) == before[role].get("Player_Cash", 0) + (drill.CASH if role == "host" else 0)
        assert gained["host"].get("Mosby_DrillTest") == 1
        for label, reputation in record["reputation"].items():
            rep, advantage = f"Rep_{label.title()}", f"Adv_{label.title()}"
            assert (before["host"].get(rep, 0), gained["host"].get(rep, 0)) == (reputation["before"], reputation["after"])
            assert before["host"].get(advantage) == gained["host"].get(advantage)
        assert record["wallet"] == gained["host"]["Player_Cash"]
        await wait(client_run, lambda status: status.get("gameplay", {}).get("economy", {}).get("wallet") == record["wallet"])
        proof["stock_flags"] = gained

        async def replay():
            receipt = await action(client, "native-quest-turnin", "--target", drill.QUEST_ID)
            assert receipt == {"effect": "native_quest_already_settled", **record}, receipt
            observed = (await wait(run, lambda status: ready_stock(status, -10, 1)))["game"][drill.KEY]
            assert (observed["station"], observed["objective_guid"], observed["armor_guid"]) == (drill.STATION, None, None)
            assert observed["tape_after"] is True and observed["drill_tested_after"] is True and observed["eligible"] is False
            assert observed["cash_after"] == gained["host"]["Player_Cash"] and observed["xp_after"] == gained["host"]["Player_Xp"]
            return observed

        proof["replay"] = await replay()
        _, _, restarted = await restart_pair(host_process, guest_process, current, SCIENTIST_SHOP, label="drill-redemption-restart")
        assert await inventory(authority, "host") == expected_host and await inventory(client, character) == expected_guest
        placed = (await action(authority, tape.APPROACH, "--target", "guest"))["native_mosby_tape_approach"]
        restored = await replay()
        assert flags(saved()) == gained
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        completions = [row for row in events if row["event"] == "native_prototype_drill_completed"]
        assert len(completions) == 1 and completions[0]["consumed_drill_head"] == claimed["guid"]
        proof["restart"] = {"port": port, "generation": restarted["generation"], "restored": restored}

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
        if prototype_dialogue:
            questions = (await action(client, dialogue.CHOOSE, "--target", f"{scene['token']}@4"))["native_dialogue"]
            refused = await action(client, dialogue.CHOOSE, "--target", f"{questions['token']}@7", expected=False)
            assert refused["error"] == "native_dialogue_choice_unavailable"
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
        if prototype_dialogue:
            await accept_prototype()

        host_process, guest_process, restarted = await restart_pair(host_process, guest_process, current, SCIENTIST_SHOP)
        assert await inventory(authority, "host") == host_items and await inventory(client, character) == guest_items
        if equip_reward:
            def native_hazop(state):
                return next((row for row in state.get("game", {}).get("native_guest_armor_projection", {}).values()
                             if row.get("ok") and (row.get("guid"), row.get("class_path"), row.get("minimum")) == (
                                 guest_armor, armor.HAZOP_CLASS_PATH, 32.0)
                             and row.get("status") in {"reapplied", "already_applied"} and armor.effects(row)), None)

            evidence["armor_equip"]["restored_projection"] = native_hazop(await wait(run, native_hazop))
        restored = await identify_board()
        assert (restored["step_after"], restored["flag_after"], restored["objective_guid"]) == (
            -10, 2 if prototype_dialogue else 1, None), restored
        assert tuple(restored[key] for key in ("cash_after", "xp_after")) == (gained["host"]["Player_Cash"], gained["host"]["Player_Xp"])
        if prototype_dialogue:
            await action(authority, dialogue.APPROACH, "--target", "guest")
            recovered = (await action(client, dialogue.OPEN, "--target", dialogue.MOSBY))["native_dialogue"]
            progress = recovered["progress"]
            assert (recovered["row"], progress["quest_step"], progress["stone_maze_readings"],
                    progress["topic_micro_drill"], progress["prototype_step"]) == (dialogue.GREET, -10, 2, 1, 1), recovered
            assert saved()["world"]["party"]["native_quests"][dialogue.PROTOTYPE_QUEST] == evidence["prototype_dialogue"]["issued"]
            evidence["prototype_dialogue"]["restarted"] = progress
        if prototype_pickup:
            await collect_prototype(restarted, host_process, guest_process, host_items, guest_items)
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
    prototype = parser.add_mutually_exclusive_group()
    prototype.add_argument("--prototype-dialogue", action="store_true", help="accept the authentic Prototype Drill follow-up through Interact")
    prototype.add_argument("--prototype-pickup", action="store_true", help="issue Prototype Drill and verify the stock Sci Tunnel keypad and entry")
    prototype.add_argument("--prototype-redeem", action="store_true",
                           help="redeem the exact Drill Head with stock Interact after a staged chamber pickup and stock return route")
    parser.add_argument("--prototype-chamber-staged", action="store_true",
                        help="stage the guest on the exact local catwalk after stock entry to verify Drill Head Interact and custody")
    args = parser.parse_args()
    if args.prototype_chamber_staged and not (args.prototype_pickup or args.prototype_redeem):
        parser.error("--prototype-chamber-staged requires --prototype-pickup or --prototype-redeem")
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved, stock_pickup=args.stock_pickup,
                         stock_station=args.stock_station, stock_board=args.stock_board, equip_reward=args.equip_reward,
                         prototype_dialogue=args.prototype_dialogue, prototype_pickup=args.prototype_pickup,
                         prototype_chamber_staged=args.prototype_chamber_staged, prototype_redeem=args.prototype_redeem))
