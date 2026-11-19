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
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, RUIN_FIELDS, SCIENTIST_SHOP


TO_MAZE = ((RUIN_FIELDS, "Market_Flood"), (CROSSROADS, "CR_RF"), (DEAD_BRIDGE, "DB_CR"), (tape.STONE_MAZE, "DB_SM"))
TO_SCIENTIST = ((DEAD_BRIDGE, "DB_SM"), (CROSSROADS, "DB_CR"), (RUIN_FIELDS, "CR_RF"), (SCIENTIST_SHOP, "Market_Flood"))


async def exercise(work: Path, vanilla_saved: Path | None, *, stock_pickup=False):
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
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved, stock_pickup=args.stock_pickup))
