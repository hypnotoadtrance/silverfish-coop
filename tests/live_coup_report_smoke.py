"""Verify the placed Dead End note and its stock Borough contract on loopback."""

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

from tools.silverfish import native_host_save
from tools.silverfish.native_authored_pickup import COUP_REPORT, DEAD_END, SOURCES


QUEST = "Clt_CoupReport_01"
BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"
BOARD = f"BountyBoard_BP_C {BOROUGH}.Borough:PersistentLevel.BountyBoard_BP_C_3"
STEPS = (
    ("NorthEast_Rail_P", "M35_End", "Level_Exit_BP_C_194"),
    ("CrossRoads_P", "Cross_NE", "Level_Exit_BP_C_269"),
    ("RailYard_P", "Depot_Cross_Rail", "Level_Exit_BP_C_1"),
    ("City_01_P", "Rail_City", "Level_Exit_BP_C_220"),
    ("Borough", "City_Borough", "Level_Exit_BP_C_388"),
)


async def exercise(work, vanilla_saved, *, board_input=False):
    name = "coup-report-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    run, client_run = root / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved)
    if not original:
        raise ValueError("Supply the original single-player Saved directory")
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host, guest = ("--campaign", name), ("--profile", name)
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    evidence = {"routes": [], "stock_board_input": board_input, "verified": False}
    jobs = []

    async def command(*args, accepted=True):
        proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 45)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if not out or (proc.returncode == 0) != accepted:
            raise RuntimeError(f"{args}: {out.decode(errors='replace')} {err.decode(errors='replace')}")
        return json.loads(out)

    async def action(owner, verb, *args, accepted=True):
        return await command("action", *owner, verb, *args, accepted=accepted)

    async def launch(owner, label, *args):
        with (run / f"{label}.txt").open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, proc))
        return proc

    async def stop(owner, proc):
        if proc.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(proc.wait(), 20) == 0

    async def paired(area, prior=None):
        authority = await wait(run, lambda s: s.get("state") == "hosting" and s.get("link_connected") is True
                               and s.get("game", {}).get("map") == area
                               and s["game"].get("native_guest_save", {}).get("state") == "bound"
                               and (s.get("generation"), s.get("zone_epoch")) != prior, timeout=70)
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("character_id") == character
                   and s.get("host_session") == authority["generation"] and s.get("zone_epoch") == authority["zone_epoch"]
                   and s.get("game", {}).get("map") == area, timeout=70)
        return authority

    async def inventory(owner):
        page = (await action(owner, "native-inventory-list"))["native_inventory"]
        assert page["next_offset"] is None, page
        return {row["guid"]: row for row in page["items"]}

    async def stock_balances():
        await action(host, "checkpoint")
        save = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        owner = save["world"]["native_host_save"]
        member = save["characters"][character]["state"]["native_guest_save"]
        assert owner["slot"] != member["slot"] and member["character_id"] == character, member
        directory = native_host_save.directory(root / "game-user")
        wanted = ("Player_Cash", "Player_Xp", "Rep_Commune", "Rep_Coup")
        balances = {}
        for role, frame in (("host", owner), ("guest", member)):
            flags = stock_flags(directory, frame, BOROUGH)
            balances[role] = {key: flags.get(key, 0) for key in wanted}
        return balances

    async def board_state(*states):
        saved = await wait(client_run, lambda s: (row := s.get("game", {}).get("native_wire_board", {}))
                           and row.get("map") == BOROUGH and row.get("board") == BOARD and row.get("state") in states)
        return saved["game"]["native_wire_board"]

    async def board_press(expected):
        pressed = (await action(guest, "native-wire-board-fixture", "--target", QUEST))["native_wire_board"]
        assert (pressed["map"], pressed["board"], pressed["quest"], pressed["state"]) == (BOROUGH, BOARD, QUEST, expected), pressed
        evidence.setdefault("board_presses", []).append(pressed)

    async def committed_quest(step):
        def record(state):
            return state.get("campaign", {}).get("world", {}).get("party", {}).get("native_quests", {}).get(QUEST, {})

        saved = await wait(run, lambda state: record(state).get("step") == step)
        return record(saved)

    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", DEAD_END, *host_flags)
        ready = await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await command("invite", *host, "--name", "Dead End note tester", "--character", "Courier")
        character = invitation["character_id"]
        peer = await launch(guest, "guest", "join", *guest, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        current = await paired(DEAD_END)
        await action(host, "native-authored-pickup-approach", "--target", "guest")
        interaction = (await action(guest, "native-authored-pickup-fixture"))["native_authored_pickup_interaction"]
        assert interaction["source"] == COUP_REPORT and interaction["entry"] == "fixture", interaction
        claim = (await action(guest, "native-authored-pickup-claim", "--target", COUP_REPORT))["native_authored_pickup_claim"]
        guid = claim["guid"]
        row = (await inventory(guest))[guid]
        assert (row["class_path"], row["quantity"], row["source"]) == (SOURCES[COUP_REPORT][2], 1, "backpack"), row
        assert guid not in await inventory(host)
        evidence["stock_pickup"] = {"interaction": interaction, "claim": claim, "inventory": row}

        for world, entrance, actor in STEPS:
            destination = BOROUGH if world == "Borough" else f"/Game/Classes/Maps/Maps_OverWorld/{world}"
            evidence["pending_route"] = (current["game"]["map"], destination, entrance, actor)
            endpoint = ("--entrance", entrance, "--exit-actor", actor)
            for role in ("host", "guest"):
                await action(host, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
            await action(host, "native-exit-position", "--target", "host", "--exit-position", "inside", *endpoint)
            current = await paired(destination, (current["generation"], current["zone_epoch"]))
            evidence["routes"].append(evidence.pop("pending_route"))

        await action(host, "native-coup-report-approach", "--target", character)
        refused = await action(guest, "native-quest-start", "--target", QUEST, accepted=False)
        assert "not_offered" in refused["error"], refused
        evidence["before_host_xp"] = refused["error"]
        await action(host, "native-turret-photo-xp-fixture")
        offered = (await action(guest, "native-coup-report-preflight"))["native_coup_report"]
        assert offered["board"] == BOARD and offered["step_after"] is None, offered
        if board_input:
            await board_state("armed", "focused")
            await board_press("loading")
            await board_state("offer")
            await board_press("starting")
            accepted = await committed_quest(1)
        else:
            accepted = await action(guest, "native-quest-start", "--target", QUEST)
        assert (accepted["quest_id"], accepted["step"], accepted["actor"]) == (QUEST, 1, character), accepted
        active = (await action(guest, "native-coup-report-preflight"))["native_coup_report"]
        assert (active["step_after"], active["objective_guid"], active["objective_owner"]) == (1, guid, "guest"), active
        reputation = active["reputation"]
        evidence["effective_stock_reputation_before"] = {faction: row["before"] for faction, row in reputation.items()}
        before = await stock_balances()
        evidence["stock_before"] = before
        if board_input:
            await board_state("ready")
            await board_press("redeeming")
            redeemed = await committed_quest(-10)
        else:
            redeemed = await action(guest, "native-quest-turnin", "--target", QUEST)
        assert (redeemed["step"], redeemed["cash"], redeemed["xp"]) == (-10, 1200, 1), redeemed
        assert set(redeemed["recipients"]) == {"host", character}, redeemed
        assert (redeemed["consumed_note"], redeemed["objective_owner"]) == (guid, character), redeemed
        assert guid not in await inventory(guest) and guid not in await inventory(host)
        after = await stock_balances()
        assert after["host"] == dict(before["host"], Player_Cash=before["host"]["Player_Cash"] + 1200,
                                     Player_Xp=before["host"]["Player_Xp"] + 1, Rep_Commune=reputation["2"]["before"] + 60,
                                     Rep_Coup=reputation["5"]["before"] - 100), after
        assert after["guest"] == dict(before["guest"], Player_Xp=before["guest"]["Player_Xp"] + 1), after
        evidence.update(stock_turn_in=redeemed, stock_after=after)
        assert (await action(guest, "native-quest-turnin", "--target", QUEST))["effect"] == "native_quest_already_settled"

        await stop(host, authority)
        await stop(guest, peer)
        await launch(host, "host-restart", "host", *host, "--port", str(ready["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != ready["generation"])
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(guest, "guest-restart", "join", *guest, "--headless", "--native-zones", "--invite", renewed["invite_file"])
        restarted = await paired(BOROUGH)
        assert guid not in await inventory(guest) and await stock_balances() == after
        assert (await action(guest, "native-quest-turnin", "--target", QUEST))["effect"] == "native_quest_already_settled"
        evidence.update(verified=True, restarted_generation=restarted["generation"])
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for owner, proc in reversed(jobs):
            if proc.returncode is None:
                try:
                    await stop(owner, proc)
                except (OSError, RuntimeError, TimeoutError):
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (run / "live-coup-report-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--board-input", action="store_true", help="use the original guest Interact binding at the stock board")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=args.board_input))
