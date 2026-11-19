"""Verify the stock Flood Plains jammer contract through a headless guest's real weapon input."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes
from live_turret_photo_smoke import BOROUGH, CITY, RAIL, RUINS


REPO = Path(__file__).resolve().parents[1]
QUEST = "Jmr_FloodPlains"
BOARD = f"BountyBoard_BP_C {BOROUGH}.Borough:PersistentLevel.BountyBoard_BP_C_3"
LEVEL = f"{RUINS}.RuinFeilds_P:PersistentLevel."
JAMMER = f"SignalJammer_BP_C {LEVEL}SignalJammer_BP_C_1"
TRIGGER = f"Quest_Trigger_BP_C {LEVEL}Quest_Trigger_BP_C_1"
TO_FLOOD = ((CITY, "City_Borough"), (RAIL, "Rail_City"), (RUINS, "Ry_Rf"))
TO_BOARD = ((RAIL, "Ry_Rf"), (CITY, "Rail_City"), (BOROUGH, "City_Borough"))


def quest_record(campaign):
    return campaign.get("world", {}).get("party", {}).get("native_quests", {}).get(QUEST, {})


async def exercise(work, vanilla_saved, *, client_projection=False):
    name = "flood-jammer-" + uuid.uuid4().hex[:8]
    host, client = ("--campaign", name), ("--profile", name)
    campaign = work / "campaigns" / name
    run, client_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    if vanilla_saved is not None and not vanilla_saved.is_dir():
        raise ValueError(f"Original Saved folder does not exist: {vanilla_saved}")
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    client_flags = ("--headless", "--native-zones", "--native-guest-primary-input")
    evidence = {"routes": [], "shots": [], "client_projection_requested": client_projection}
    processes = []
    character = None

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, verb, *arguments, expected=True):
        return await command("action", *owner, verb, *arguments, expected=expected)

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 18) == 0, process.returncode

    async def enrolled(area, previous=None):
        def bound(state):
            game = state.get("game", {})
            return (state.get("state") == "hosting" and game.get("map") == area
                    and (state.get("generation"), state.get("zone_epoch")) != previous
                    and game.get("native_guest_save", {}).get("state") == "bound"
                    and any(lease["character_id"] == character and lease["state"] == "active"
                            for lease in state.get("campaign", {}).get("leases", ())))

        authority = await wait(run, bound, timeout=75)
        await wait(client_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and state.get("character_id") == character and state.get("host_session") == authority["generation"]
                   and state.get("zone_epoch") == authority["zone_epoch"] and state.get("game", {}).get("map") == area,
                   timeout=75)
        return authority

    async def cross(current, area, tag):
        route = {"from": current["game"]["map"], "to": area, "entrance": tag}
        evidence["pending_route"] = route
        for role in ("host", "guest"):
            await action(host, "native-exit-position", "--target", role, "--exit-position", "near", "--entrance", tag)
        await action(host, "native-exit-position", "--target", "host", "--exit-position", "inside", "--entrance", tag)
        arrived = await enrolled(area, (current["generation"], current["zone_epoch"]))
        evidence["routes"].append({**route, "zone_epoch": arrived["zone_epoch"]})
        del evidence["pending_route"]
        return arrived

    async def board():
        approached = (await action(host, "native-flood-jammer-approach", "--target", "guest"))["native_flood_jammer_approach"]
        readback = (await action(client, "native-flood-jammer-preflight"))["native_flood_jammer"]
        assert (readback["map"], readback["board"], readback["quest"], readback["pawn"]) == (
            BOROUGH, BOARD, QUEST, approached["pawn"],
        ), readback
        return readback

    def read_saved():
        return json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))

    async def balances():
        await action(host, "checkpoint")
        saved = read_saved()
        stock = saved["world"]["native_host_save"]
        guest = saved["characters"][character]["state"]["native_guest_save"]
        assert guest["character_id"] == character and guest["slot"] != stock["slot"], (stock, guest)
        root = campaign / "game-user" / "Saved" / "SaveGames"
        result = {}
        for owner, frame in (("host", stock), ("guest", guest)):
            flags = stock_flags(root, frame, BOROUGH)
            result[owner] = {key: flags.get(key, 0) for key in ("Player_Cash", "Player_Xp")}
        return result

    async def projected(*states):
        key = "native_flood_jammer_projection"
        client_state = await wait(client_run, lambda frame: (view := frame.get("game", {}).get(key, {}))
                                  and (view.get("map"), view.get("actor")) == (RUINS, JAMMER)
                                  and view.get("state") in states, timeout=20)
        return client_state["game"][key]

    async def fire(guid, magazine):
        aim = (await action(host, "native-flood-jammer-aim", "--target", "guest", "--item-guid", guid))["native_flood_jammer_aim"]
        assert (aim["map"], aim["jammer"], aim["guid"]) == (RUINS, JAMMER, guid), aim
        assert 75 <= aim["distance"] <= 550 and aim["component"].split(" ", 1)[-1].startswith(LEVEL + "SignalJammer_BP_C_1."), aim
        press = (await action(client, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
        attempt = {"aim": aim, "press": press}
        evidence["shots"].append(attempt)
        assert (press["map"], press["guid"], press["stock_wrappers"]) == (RUINS, guid, 2), press
        for _ in range(36):
            events = (await command("events", *client, "--tail", "35"))["events"]
            event = next((row for row in events if row.get("sequence") == press["sequence"]
                          and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")), None)
            if event:
                attempt["receipt"] = event
                assert event["event"] == "native_guest_primary_input", event
                shot = event["native_guest_weapon"]
                assert (shot["map"], shot["pawn"], shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                    RUINS, aim["pawn"], guid, magazine, magazine - 1, 1,
                ), shot
                return shot
            await asyncio.sleep(.2)
        raise RuntimeError(f"No authoritative stock primary-input receipt for sequence {press['sequence']}")

    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", BOROUGH, *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == BOROUGH, timeout=75)
        invitation = await command("invite", *host, "--name", "Jammer guest", "--character", "Signal Hunter")
        character = invitation["character_id"]
        visitor = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        current = await enrolled(BOROUGH)
        port = current["endpoint"]["port"]

        offer = await board()
        evidence["stock_offer"] = offer
        assert offer["step_before"] is None and offer["step_after"] is None, offer
        accepted = await action(client, "native-quest-start", "--target", QUEST)
        evidence["stock_acceptance"] = accepted
        assert (accepted["effect"], accepted["quest_id"], accepted["step"], accepted["actor"]) == (
            "native_quest_changed", QUEST, 1, character,
        ), accepted
        assert quest_record(read_saved())["step"] == 1
        premature = await action(client, "native-quest-turnin", "--target", QUEST, expected=False)
        assert premature["error"] == "native_flood_jammer_objective_incomplete", premature
        evidence["early_turn_in"] = premature["error"]

        guid = uuid.uuid4().hex.upper()
        seeded = (await action(host, "native-guest-weapon-seed-light-pistol", "--target", "guest", "--item-guid", guid))[
            "native_guest_weapon"]
        evidence["stock_pistol_seed"] = seeded
        assert (seeded["guid"], seeded["status"], seeded["stored_ammo"]) == (guid, "backpack", 7), seeded
        for area, tag in TO_FLOOD:
            current = await cross(current, area, tag)

        watcher = await wait(run, lambda state: state.get("game", {}).get("native_flood_jammer_objective", {}).get("state") == "watching")
        watched = watcher["game"]["native_flood_jammer_objective"]
        assert (watched["map"], watched["jammer"], watched["trigger"]) == (RUINS, JAMMER, TRIGGER), watched
        evidence["stock_watch"] = watched
        if client_projection:
            visible = await projected("present")
            evidence["client_projection_before"] = visible
            assert visible["audio"] == "-", visible
        equipped = (await action(client, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
        evidence["stock_pistol_equip"] = equipped
        assert (equipped["map"], equipped["status"], equipped["guid"], equipped["actor_ammo"], equipped["stored_ammo"]) == (
            RUINS, "equipped", guid, 7, 7,
        ), equipped
        await wait(client_run, lambda state: (scope := state.get("game", {}).get("native_guest_primary_scope", {}))
                   and (scope["map"], scope["guid"], scope["kind"]) == (RUINS, guid, "light-pistol"))

        magazine = 7
        for attempt in range(3):
            shot = await fire(guid, magazine)
            magazine = shot["after"]
            try:
                observed = await wait(run, lambda state: quest_record(state.get("campaign", {})).get("step") == 2, timeout=10)
                break
            except RuntimeError:
                last_observer = status(run).get("game", {}).get("native_flood_jammer_objective", {})
                evidence["shots"][-1]["last_objective"] = last_observer
                if attempt == 2 or last_observer.get("state") != "watching":
                    raise
        completed = quest_record(observed["campaign"])
        assert quest_record(read_saved()) == completed, completed
        proof = completed["objective"]
        evidence["durable_objective"] = completed
        assert completed["actor"] == character and (
            proof["map"], proof["jammer"], proof["trigger"], proof["state"], proof["step_before"], proof["step_after"],
            proof["shooter_role"], proof["shooter"],
        ) == (RUINS, JAMMER, TRIGGER, "stock_objective_verified", 1, 2, "guest", shot["pawn"]), completed
        assert proof["xp_before"] == proof["xp_after"] and proof["projectile"].split(" ", 1)[-1].startswith(LEVEL), proof
        if client_projection:
            concealed = await projected("hidden", "absent")
            evidence["client_projection_after"] = concealed
            assert (concealed["state"], concealed["audio"]) in {("hidden", "muted"), ("absent", "none")}, concealed

        for area, tag in TO_BOARD:
            current = await cross(current, area, tag)
        ready = await board()
        before = await balances()
        evidence["stock_redemption_ready"], evidence["native_balances_before"] = ready, before
        assert ready["step_after"] == 2 and ready["xp_after"] == offer["xp_after"], ready
        assert ready["reputation"]["8"]["after"] == offer["reputation"]["8"]["after"] + 10, (offer, ready)
        assert ready["reputation"]["5"]["after"] == offer["reputation"]["5"]["after"], (offer, ready)
        settled = await action(client, "native-quest-turnin", "--target", QUEST)
        evidence["stock_turn_in"] = settled
        assert (settled["effect"], settled["step"], settled["cash"], settled["xp"], settled["wallet"], set(settled["recipients"])) == (
            "native_quest_changed", -10, 350, 1, before["host"]["Player_Cash"] + 350, {"host", character},
        ), settled
        assert settled["objective"] == proof, settled
        reputation = settled["native_flood_jammer"]["reputation"]
        assert (reputation["8"]["after"] - reputation["8"]["before"], reputation["5"]["after"] - reputation["5"]["before"]) == (50, -40), reputation

        def reward_materialized(state):
            players = state.get("campaign", {}).get("world", {}).get("party", {}).get("players", {})
            return all(players.get(owner, {}).get("quest_xp") == 1 and not players[owner].get("native_quest_xp_pending")
                       for owner in ("host", character))

        await wait(run, reward_materialized)
        paid = await balances()
        expected = {owner: {"Player_Cash": values["Player_Cash"] + (350 if owner == "host" else 0),
                            "Player_Xp": values["Player_Xp"] + 1} for owner, values in before.items()}
        assert paid == expected, (before, paid)
        evidence["native_balances_paid"] = paid
        repeated = await action(client, "native-quest-turnin", "--target", QUEST)
        assert (repeated["effect"], repeated["wallet"], repeated["objective"]) == (
            "native_quest_already_settled", settled["wallet"], proof,
        ), repeated
        evidence["same_session_replay"] = repeated["effect"]

        await stop(host, authority)
        await stop(client, visitor)
        await launch(host, "host-restart", "host", *host, "--port", str(port), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != current["generation"], timeout=75)
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invitation["guest_id"], character), renewed
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        restarted = await enrolled(BOROUGH, (current["generation"], current["zone_epoch"]))
        assert restarted["endpoint"]["port"] == port, restarted["endpoint"]
        restored = await board()
        assert (restored["step_after"], restored["cash_after"], restored["xp_after"]) == (
            -10, paid["host"]["Player_Cash"], paid["host"]["Player_Xp"],
        ), restored
        assert {key: row["after"] for key, row in restored["reputation"].items()} == settled["reputation"], restored
        restored_pistol = (await action(client, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
        assert (restored_pistol["guid"], restored_pistol["stored_ammo"]) == (guid, magazine), restored_pistol
        replay = await action(client, "native-quest-turnin", "--target", QUEST)
        assert (replay["effect"], replay["wallet"], replay["objective"]) == (
            "native_quest_already_settled", settled["wallet"], proof,
        ), replay
        assert await balances() == paid
        evidence.update(same_port_restart={"port": port, "generation": restarted["generation"], "character": character,
                                          "quest": restored, "pistol": restored_pistol, "replay": replay["effect"]}, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
            if not evidence["original_saves_verified"]:
                evidence.update(verified=False, failure="The original Saved directory changed")
        (run / "live-flood-jammer-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original Saved directory changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    parser.add_argument("--client-projection", action="store_true", help="require the exact client-local jammer to disappear after the stock kill")
    arguments = parser.parse_args()
    asyncio.run(exercise(arguments.work.resolve(), arguments.vanilla_saved, client_projection=arguments.client_projection))
