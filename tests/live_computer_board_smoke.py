"""Verify the Observatory Computer Board pickup, Borough offer, and Scientist redemption."""

import argparse
import asyncio
from hashlib import sha256
import json
import math
from pathlib import Path
from struct import unpack_from
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import CROSSROADS, OBSERVATORY, saved_hashes
from live_turret_photo_smoke import BOROUGH, CITY, RAIL, RUINS, SCIENTIST

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_borough_barter as barter


QUEST = "Clt_ComputerBoard"
PICKUP = "observatory-comp-board"
ITEM = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
TO_BOROUGH = ((CROSSROADS, "Obsv_Etr"), (RAIL, "Depot_Cross_Rail"), (CITY, "Rail_City"), (BOROUGH, "City_Borough"))
TO_SCIENTIST = ((CITY, "City_Borough"), (RAIL, "Rail_City"), (RUINS, "Ry_Rf"), (SCIENTIST, "Market_Flood"))
REPUTATION = {"2": ("Rep_Commune", 50), "4": ("Rep_State", -5), "5": ("Rep_Coup", -5)}


def stock_flags(save_root, frame, area):
    slot = frame["slot"]
    assert slot.startswith("SFCoop_") and len(slot) == 39 and uuid.UUID(hex=slot[7:]).hex == slot[7:], frame
    contents = (save_root / f"{slot}.sav").read_bytes()
    assert (frame["map"], frame["size"], frame["sha256"]) == (area, len(contents), sha256(contents).hexdigest()), frame
    assert contents.startswith(b"GVAS\x03\0\0\0"), frame
    # Both package versions and the fixed FEngineVersion fields precede its branch string.
    cursor = 26

    def integer():
        nonlocal cursor
        value = unpack_from("<i", contents, cursor)[0]
        cursor += 4
        return value

    def string():
        nonlocal cursor
        units = integer()
        if units == 0:
            return ""
        width = 1 if units > 0 else 2
        end = cursor + abs(units) * width
        assert end <= len(contents) and contents[end - width:end] == bytes(width), frame
        value = contents[cursor:end - width].decode("utf-8" if width == 1 else "utf-16-le")
        cursor = end
        return value

    assert string() == "++UE5+Release-5.4" and integer() == 3, frame
    versions = integer()
    assert 0 <= versions <= (len(contents) - cursor) // 20, frame
    cursor += 20 * versions
    assert string() == "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C" and contents[cursor] == 0, frame
    cursor += 1
    if string() != "Flags":
        assert b"\x06\0\0\0Flags\0\x0c\0\0\0MapProperty\0" not in contents, frame
        return {}
    assert (string(), integer(), string(), integer(), string(), integer()) == (
        "MapProperty", 2, "NameProperty", 0, "IntProperty", 0,
    ), frame
    size = integer()
    assert contents[cursor] == 0, frame
    cursor += 1
    end = cursor + size
    assert size >= 8 and end <= len(contents) and integer() == 0, frame
    count = integer()
    assert 0 <= count <= (end - cursor) // 9, frame
    flags = {string(): integer() for _ in range(count)}
    assert cursor == end and len(flags) == count, frame
    return flags


async def exercise(work, vanilla_saved, *, stock_board_input=False, crafting_bench=False):
    name = "computer-board-" + uuid.uuid4().hex[:8]
    campaign, profile = ("--campaign", name), ("--profile", name)
    campaign_dir = work / "campaigns" / name
    host_run = campaign_dir / "run"
    guest_run = work / "clients" / name / "run"
    host_run.mkdir(parents=True, exist_ok=True)
    print(host_run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    processes = []
    proof = {"stock_routes": [], "stock_board_input": stock_board_input, "crafting_bench": crafting_bench}
    character = None

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, operation, *arguments, expected=True):
        return await command("action", *owner, operation, *arguments, expected=expected)

    async def launch(owner, label, *arguments):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def enrolled(area, previous=None):
        def bound(state):
            game = state.get("game", {})
            return (state.get("state") == "hosting" and game.get("map") == area
                    and (state.get("generation"), state.get("zone_epoch")) != previous
                    and game.get("native_guest_save", {}).get("state") == "bound"
                    and any(row["character_id"] == character and row["state"] == "active"
                            for row in state.get("campaign", {}).get("leases", ())))

        host = await wait(host_run, bound, timeout=75)
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and state.get("character_id") == character and state.get("host_session") == host["generation"]
                   and state.get("zone_epoch") == host["zone_epoch"] and state.get("game", {}).get("map") == area, timeout=75)
        return host

    async def cross(host, destination, entrance):
        door = entrance == "Market_Flood"
        route = {"from": host["game"]["map"], "to": destination, "entrance": entrance, "kind": "door" if door else "overlap"}
        proof["pending_route"] = route
        position = "native-door-position" if door else "native-exit-position"
        for role in ("host", "guest"):
            await action(campaign, position, "--target", role, "--exit-position", "near", "--entrance", entrance)
        if door:
            await action(campaign, "native-door-probe", "--target", "host", "--entrance", entrance)
        else:
            await action(campaign, position, "--target", "host", "--exit-position", "inside", "--entrance", entrance)
        arrived = await enrolled(destination, (host["generation"], host["zone_epoch"]))
        proof["stock_routes"].append({**route, "zone_epoch": arrived["zone_epoch"]})
        del proof["pending_route"]
        return arrived

    async def inventory(owner, expected_character):
        rows, offset, total = {}, 0, None
        while offset is not None:
            options = (("--amount", str(offset)) if owner == campaign else ("--target", str(offset))) if offset else ()
            page = (await action(owner, "native-inventory-list", *options))["native_inventory"]
            assert page["owner"] == expected_character and page["offset"] == offset, page
            assert total in (None, page["total"]), page
            total = page["total"]
            for item in page["items"]:
                assert item["guid"] not in rows, item
                rows[item["guid"]] = {key: value for key, value in item.items() if key != "index"}
            offset = page["next_offset"]
            assert offset is None or offset == len(rows), page
        assert len(rows) == total, rows
        return rows

    async def balances():
        await action(campaign, "checkpoint")
        saved = json.loads((campaign_dir / "save" / "campaign.json").read_text(encoding="utf-8"))
        stock = saved["world"]["native_host_save"]
        detached = saved["characters"][character]["state"]["native_guest_save"]
        assert detached["character_id"] == character and detached["slot"] != stock["slot"], detached
        save_root = campaign_dir / "game-user" / "Saved" / "SaveGames"
        flags = {owner: stock_flags(save_root, frame, SCIENTIST) for owner, frame in (("host", stock), ("guest", detached))}
        return {owner: {key: values.get(key, 0) for key in ("Player_Cash", "Player_Xp", *(row[0] for row in REPUTATION.values()))}
                for owner, values in flags.items()}

    async def board():
        await action(campaign, "native-computer-board-approach", "--target", "guest")
        return (await action(profile, "native-computer-board-preflight"))["native_computer_board"]

    async def catalog(qualified, *, offered):
        offset, entries = 0, {}
        for _ in range(17):
            page = (await action(profile, "native-bounty-board-list", "--target", str(offset)))["native_bounty_board"]
            assert tuple(page[key] for key in ("map", "board", "pawn")) == tuple(
                qualified[key] for key in ("map", "board", "pawn")
            ), page
            assert (page["source"], page["offset"]) == ("board", offset) and page["state"] in {"ready", "partial"}, page
            assert all(row["id"] not in entries and row["state"] == "offered" for row in page["quests"]), page
            entries.update((row["id"], row) for row in page["quests"])
            offset = page["next_offset"]
            if offset is None:
                break
            assert offset == len(entries), page
        else:
            raise RuntimeError("The stock board offer catalog exceeded its page limit")
        assert len(entries) == page["reported"] and (QUEST in entries) is offered, page
        if offered:
            row = entries[QUEST]
            assert (row["cash"], row["xp"], row["item_count"], row["co_op"]) == (800, 1, 0, "verified"), row
        report = {key: page[key] for key in ("map", "board", "pawn", "reported", "state")}
        report.update(offer=entries.get(QUEST), offer_ids=sorted(entries))
        proof.setdefault("stock_board_catalogs", []).append(report)

    async def press_board(qualified, kind, rendered):
        def local_board(state):
            return state.get("game", {}).get("native_wire_board", {})

        area, actor = qualified["map"], qualified["board"]
        await wait(guest_run, lambda state: (row := local_board(state)).get("map") == area and row.get("board") == actor
                   and row.get("state") in {"armed", "focused", "offer", "ready", "elsewhere", "completed"}, timeout=12)
        waiting = {"view": "loading", "start": "starting", "turnin": "redeeming"}[kind]
        pressed = (await action(profile, "native-wire-board-fixture", "--target", QUEST))["native_wire_board"]
        assert (pressed["map"], pressed["board"], pressed["quest"], pressed["state"]) == (area, actor, QUEST, waiting), pressed
        identity = ("map", "board", "quest", "pawn", "sequence", "open")
        shown = await wait(guest_run, lambda state: (row := local_board(state)).get("state") != waiting
                           and all(row.get(key) == pressed[key] for key in identity), timeout=15)
        result = local_board(shown)
        events = (await command("events", *profile, "--tail", "40"))["events"]
        receipts = [row for row in events if row.get("generation") == shown["generation"] and row.get("sequence") == pressed["sequence"]
                    and row["event"] in {"native_wire_board_interaction", "native_wire_board_interaction_rejected"}]
        proof.setdefault("stock_board_presses", []).append({"press": pressed, "render": result, "receipts": receipts})
        assert len(receipts) == 1 and result["state"] == rendered, (result, receipts)
        event = receipts[0]
        assert (event["event"], event["board"], event["quest"], event["kind"], event["state"]) == (
            "native_wire_board_interaction", actor, QUEST, kind, rendered,
        ), event
        return result

    async def quest_checkpoint(step):
        def record(state):
            return state.get("campaign", {}).get("world", {}).get("party", {}).get("native_quests", {}).get(QUEST, {})

        saved = await wait(host_run, lambda state: record(state).get("step") == step, timeout=15)
        return record(saved)

    async def stop(process, owner):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def craft_board(guid):
        target = (-5796.37, 669.75)
        for _ in range(10):
            state = json.loads((guest_run / "status.json").read_text(encoding="utf-8"))
            location = state["game"]["pawns"]["local"]["location"]
            proof["crafting_approach"] = location
            distance = math.dist(target, location[:2])
            if distance <= 140:
                break
            dx, dy = ((goal - actual) / distance for goal, actual in zip(target, location[:2], strict=True))
            duration = max(130, min(1800, round((distance - 60) * 1.5)))
            await action(profile, "walk", "--x", str(dx), "--y", str(dy), "--milliseconds", str(duration), "--continuous")
            await asyncio.sleep(duration / 1000 + .55)
        else:
            raise AssertionError(f"The guest did not walk into stock workbench range: {location}")

        refused = await action(profile, barter.CRAFT_ACTION, "--target", guid, expected=False)
        assert refused["error"] == "borough_barter_refused_detached_worn_crafting", refused
        assert guid in await inventory(profile, character)
        armor = uuid.uuid4().hex.upper()
        await action(campaign, "native-guest-armor-fixture", "--target", "guest", "--item-guid", armor, "--kind", "hazard-suit")
        await action(profile, "native-guest-armor-equip", "--target", armor)
        host_before = await inventory(campaign, "host")
        operation = uuid.uuid4().hex
        request = (barter.CRAFT_ACTION, "--target", guid, "--operation-id", operation)
        exchanged = (await action(profile, *request))["native_crafting_bench"]
        assert (exchanged["armor_guid"], exchanged["give"]["guid"]) == (armor, guid), exchanged
        assert exchanged["wallet_before"] == exchanged["wallet_after"], exchanged
        received = exchanged["receive"]["guid"]
        items = await inventory(profile, character)
        assert guid not in items and (items[received]["class_path"], items[received]["quantity"]) == (barter.SCRAP, 1), items
        assert await inventory(campaign, "host") == host_before
        assert (await action(profile, *request))["native_crafting_bench"] == exchanged
        proof.update(exchange=exchanged, repeat_verified=True, verified=True)

    try:
        authority = await launch(campaign, "host", "host", *campaign, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == OBSERVATORY)
        invite = await command("invite", *campaign, "--name", "Computer Board guest", "--character", "Courier")
        character = invite["character_id"]
        visitor = await launch(profile, "guest", "join", *profile, "--headless", "--native-zones", "--invite", invite["invite_file"])
        host = await enrolled(OBSERVATORY)

        await action(campaign, "native-authored-pickup-approach", "--target", "guest")
        await wait(guest_run, lambda state: state.get("game", {}).get("native_authored_pickup", {}).get("state") == "available")
        entry = (await action(profile, "native-authored-pickup-fixture"))["native_authored_pickup_interaction"]
        assert (entry["source"], entry["map"], entry["entry"]) == (PICKUP, OBSERVATORY, "fixture"), entry
        claimed = (await action(profile, "native-authored-pickup-claim", "--target", PICKUP))["native_authored_pickup_claim"]
        assert (claimed["owner"], claimed["class_path"]) == (character, ITEM), claimed
        guid = claimed["guid"]
        row = (await inventory(profile, character))[guid]
        assert (row["class_path"], row["source"], row["quantity"], row["stackable"]) == (ITEM, "backpack", 1, False), row
        proof["stock_pickup"] = {"interaction": entry, "claim": claimed, "inventory": row}

        if crafting_bench:
            await craft_board(guid)
            return

        for destination, entrance in TO_BOROUGH:
            host = await cross(host, destination, entrance)
        offered = await board()
        proof["stock_offer"] = offered
        assert (offered["map"], offered["step_after"], offered["xp_after"], offered["can_turn_in"]) == (BOROUGH, None, 0, False), offered
        assert offered["board"].endswith("BountyBoard_BP_C_3"), offered
        if stock_board_input:
            await catalog(offered, offered=True)
            selected = await press_board(offered, "view", "offer")
            confirmed = await press_board(offered, "start", "elsewhere")
            assert selected["sequence"] == selected["open"] == confirmed["open"] < confirmed["sequence"], confirmed
            accepted = await quest_checkpoint(1)
        else:
            accepted = await action(profile, "native-quest-start", "--target", QUEST)
        proof["stock_acceptance"] = accepted
        assert (accepted["quest_id"], accepted["step"], accepted["actor"]) == (QUEST, 1, character), accepted
        active = (await action(profile, "native-computer-board-preflight"))["native_computer_board"]
        proof["borough_active"] = active
        assert (active["objective_guid"], active["objective_owner"], active["step_after"]) == (guid, "guest", 1), active
        refused = await action(profile, "native-quest-turnin", "--target", QUEST, expected=False)
        assert refused["error"] == "stock_computer_board_scientist_redemption_required", refused
        proof["borough_turnin_refusal"] = refused["error"]

        for destination, entrance in TO_SCIENTIST:
            host = await cross(host, destination, entrance)
        redeemable = await board()
        proof["scientist_preflight"] = redeemable
        assert (redeemable["map"], redeemable["objective_guid"], redeemable["objective_owner"], redeemable["can_turn_in"]) == (
            SCIENTIST, guid, "guest", True,
        ), redeemable
        assert redeemable["board"].endswith("BountyBoard_BP_C_2"), redeemable
        if stock_board_input:
            await catalog(redeemable, offered=False)
        host_inventory, guest_inventory = await inventory(campaign, "host"), await inventory(profile, character)
        assert guid not in host_inventory and guest_inventory.pop(guid) == row, (host_inventory, guest_inventory)
        before = await balances()
        proof["native_flags_before"] = before

        if stock_board_input:
            selected = await press_board(redeemable, "view", "ready")
            confirmed = await press_board(redeemable, "turnin", "completed")
            assert selected["sequence"] == selected["open"] == confirmed["open"] < confirmed["sequence"], confirmed
            settled = await quest_checkpoint(-10)
        else:
            settled = await action(profile, "native-quest-turnin", "--target", QUEST)
            assert settled["effect"] == "native_quest_changed", settled
        proof["stock_turn_in"] = settled
        assert (settled["step"], settled["cash"], settled["xp"]) == (-10, 800, 1), settled
        assert (settled["consumed_board"], settled["objective_owner"], settled["wallet"], set(settled["recipients"])) == (
            guid, character, before["host"]["Player_Cash"] + 800, {"host", character},
        ), settled
        def xp_settled(state):
            party = state.get("campaign", {}).get("world", {}).get("party", {})
            player = party.get("players", {}).get(character, {})
            return player.get("quest_xp") == 1 and not player.get("native_quest_xp_pending")

        await wait(host_run, xp_settled)
        expected = {owner: values.copy() for owner, values in before.items()}
        expected["host"]["Player_Cash"] += 800
        for owner in expected:
            expected[owner]["Player_Xp"] += 1
        for faction, (flag, delta) in REPUTATION.items():
            expected["host"][flag] = redeemable["reputation"][faction]["before"] + delta
            assert settled["reputation"][faction] == expected["host"][flag], settled
        paid = await balances()
        proof["native_flags_paid"] = paid
        assert paid == expected, (before, paid, settled)
        assert await inventory(campaign, "host") == host_inventory and await inventory(profile, character) == guest_inventory
        repeated = await action(profile, "native-quest-turnin", "--target", QUEST)
        assert (repeated["effect"], repeated["wallet"], repeated["consumed_board"]) == (
            "native_quest_already_settled", settled["wallet"], guid,
        ), repeated
        proof["repeat_turn_in"] = repeated["effect"]

        await stop(authority, campaign)
        await stop(visitor, profile)
        await launch(campaign, "host-restart", "host", *campaign, "--port", "0", *host_flags)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != host["generation"])
        renewed = await action(campaign, "refresh-invite", "--target", invite["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invite["guest_id"], character), renewed
        await launch(profile, "guest-restart", "join", *profile, "--headless", "--native-zones", "--invite", renewed["invite_file"])
        restarted = await enrolled(SCIENTIST, (host["generation"], host["zone_epoch"]))
        restored = await board()
        assert (restored["step_after"], restored["objective_guid"], restored["cash_after"], restored["xp_after"]) == (
            -10, "-", paid["host"]["Player_Cash"], paid["host"]["Player_Xp"],
        ), restored
        assert await inventory(campaign, "host") == host_inventory and await inventory(profile, character) == guest_inventory
        restored_flags = await balances()
        assert restored_flags == paid, (paid, restored_flags)
        proof.update(stock_restart=restored, restart_generation=restarted["generation"], native_flags_restored=restored_flags, verified=True)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
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
        if original is not None:
            proof["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        (host_run / "live-computer-board-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if original is not None:
            assert proof["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    options.add_argument("--vanilla-saved", type=Path, help="optional original Saved directory to hash before and after")
    scenario = options.add_mutually_exclusive_group()
    scenario.add_argument("--stock-board-input", action="store_true", help="use the stock Interact path at both bounty boards")
    scenario.add_argument("--crafting-bench", action="store_true", help="exchange the stock pickup at the Observatory workbench")
    arguments = options.parse_args()
    asyncio.run(exercise(arguments.work.resolve(), arguments.vanilla_saved, stock_board_input=arguments.stock_board_input,
                         crafting_bench=arguments.crafting_bench))
