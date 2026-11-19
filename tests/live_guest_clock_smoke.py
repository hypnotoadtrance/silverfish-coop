"""Check the guest's stock clock before and after Observatory rest and optional Cistern travel."""

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


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.authored_world_view import WORLD_VIEW_PROFILES
from tools.silverfish.travel import CISTERN, CROSSROADS, OBSERVATORY


BED = "SavePoint_BP_C_3-Observitory_P-"
BED_ACTOR = f"SavePoint_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.SavePoint_BP_C_3"
HOUR_MS = 3_600_000


async def exercise(work, vanilla_saved, *, time_only=False):
    name = "guest-clock-" + uuid.uuid4().hex[:8]
    campaign, profile = ("--campaign", name), ("--profile", name)
    run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    run.mkdir(parents=True, exist_ok=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    evidence = {"time_only_requested": time_only}

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 35)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or not output:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def launch(kind, owner, *arguments):
        with (run / f"{kind}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO,
                                                            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))

    async def host_in(area, previous=None):
        return await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == area
                          and state.get("zone_epoch") != previous and state.get("game", {}).get("remote_clients") == 1
                          and not state.get("awaiting_zone_guest")
                          and state["game"].get("native_guest_save", {}).get("state") == "bound"
                          and state["game"].get("native_zone", {}).get("state") == "ready", timeout=60)

    async def read_clock(host, character, *, after_sequence=0, minimum_ms=0):
        area, epoch, session = host["game"]["map"], host["zone_epoch"], host["generation"]

        def current_guest(state):
            game = state.get("game", {})
            clock = game.get("native_guest_clock", {})
            host_scene = state.get("gameplay", {}).get("host_world", {}).get("native_scene", {})
            pawn = game.get("pawns", {}).get("local", {}).get("name")
            seconds = host_scene.get("game_seconds")
            return (state.get("state") == "connected" and state.get("link_connected") is True
                    and game.get("state") == "connected" and game.get("server_connection") is True
                    and (state.get("host_session"), state.get("zone_epoch"), state.get("character_id")) == (session, epoch, character)
                    and pawn and (game.get("map"), clock.get("map"), clock.get("pawn"), clock.get("epoch")) == (area, area, pawn, epoch)
                    and host_scene.get("active_map") == area and type(seconds) in (float, int)
                    and clock.get("sequence", 0) > after_sequence and clock.get("milliseconds", -1) >= minimum_ms
                    and abs(clock["milliseconds"] - round(seconds * 1000)) <= 60_000)

        state = await wait(guest_run, current_guest, timeout=25)
        reply = await command("action", *profile, "native-guest-clock-fixture")
        clock, widget = (reply[key] for key in ("native_guest_clock", "native_guest_clock_fixture"))
        pawn = state["game"]["pawns"]["local"]["name"]
        for record in (clock, widget):
            assert (record["map"], record["pawn"], record["epoch"]) == (area, pawn, epoch), record
        items = ("sequence", "milliseconds", "calls")
        assert tuple(clock[key] for key in items) == tuple(widget[key] for key in items), (clock, widget)
        assert clock["state"] == "projected" and type(clock["calls"]) is int and clock["calls"] >= 1, clock
        assert clock["sequence"] >= state["game"]["native_guest_clock"]["sequence"], clock
        assert clock["milliseconds"] >= minimum_ms and type(clock["original_client_milliseconds"]) is int, clock
        minutes = clock["milliseconds"] // 60_000
        day, minute_of_day = divmod(minutes, 1440)
        hour, minute = divmod(minute_of_day, 60)
        assert widget["text"].endswith(f"DAY {day}  {hour:02d}:{minute:02d}"), widget
        return {"clock": clock, "widget": widget, "character_id": character, "host_session": session,
                "host_scene": state["gameplay"]["host_world"]["native_scene"]}

    async def cross(entrance, *, door=False):
        position = "native-door-position" if door else "native-exit-position"
        for role in ("host", "guest"):
            await command("action", *campaign, position, "--target", role, "--exit-position", "near", "--entrance", entrance)
        if door:
            await command("action", *campaign, "native-door-probe", "--target", "host", "--entrance", entrance)
        else:
            await command("action", *campaign, position, "--target", "host", "--exit-position", "inside", "--entrance", entrance)

    try:
        await launch("host", campaign, "--port", "0", "--headless", "--fixtures", "--native-events", "--native-guest",
                     "--native-zones", "--native-expanded-zones", "--start-zone", OBSERVATORY)
        await wait(run, lambda state: state.get("state") == "hosting")
        invitation = await command("invite", *campaign, "--name", "Clock guest", "--character", "Time observer")
        character = invitation["character_id"]
        await launch("join", profile, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        host = await host_in(OBSERVATORY)
        approach = (await command("action", *campaign, "native-rest-approach"))["native_rest"]
        assert (approach["map"], approach["actor"], approach["bed"], approach["eligible"]) == (OBSERVATORY, BED_ACTOR, BED, True), approach
        evidence["observatory_before"] = before = await read_clock(host, character)
        slept = (await command("action", *campaign, "native-rest", "--amount", "1"))["native_rest"]
        evidence["stock_rest"] = slept
        assert (slept["map"], slept["actor"], slept["bed"], slept["hours"]) == (OBSERVATORY, BED_ACTOR, BED, 1), slept
        assert math.isclose(slept["after"] - slept["before"], 3600, rel_tol=0, abs_tol=.25), slept
        evidence["observatory_after"] = after = await read_clock(host, character, after_sequence=before["clock"]["sequence"],
                                                                  minimum_ms=round(slept["after"] * 1000) - 1000)
        first, latest = before["clock"], after["clock"]
        assert first["pawn"] == latest["pawn"] and first["epoch"] == latest["epoch"], (first, latest)
        projected_change = latest["milliseconds"] - first["milliseconds"]
        client_change = latest["original_client_milliseconds"] - first["original_client_milliseconds"]
        evidence["clock_delta_milliseconds"] = {"host_projection": projected_change, "original_client": client_change}
        assert projected_change >= HOUR_MS - 1000 and abs(client_change) < 300_000, (first, latest)
        assert latest["original_client_milliseconds"] != latest["milliseconds"], latest

        if time_only:
            assert CISTERN not in WORLD_VIEW_PROFILES
            await cross("Obsv_Etr")
            crossroads = await host_in(CROSSROADS, host["zone_epoch"])
            await cross("CR_Cstrn", door=True)
            cistern = await host_in(CISTERN, crossroads["zone_epoch"])
            assert host["generation"] == crossroads["generation"] == cistern["generation"]
            assert len({host["zone_epoch"], crossroads["zone_epoch"], cistern["zone_epoch"]}) == 3
            clock_only = await read_clock(cistern, character, after_sequence=latest["sequence"])
            assert clock_only["clock"]["pawn"] != latest["pawn"], clock_only
            assert not cistern["game"].get("native_world_view"), cistern["game"]
            events = (await command("events", *campaign, "--tail", "50"))["events"]
            travelled = [(row["source"], row["destination"], row["entrance"]) for row in events if row["event"] == "zone_travel_ready"]
            assert travelled == [(OBSERVATORY, CROSSROADS, "Obsv_Etr"), (CROSSROADS, CISTERN, "CR_Cstrn")], travelled
            evidence.update(time_only_cistern=clock_only, stock_routes=travelled)
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        (run / "live-guest-clock-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", required=True, type=Path, help="prepared staged workspace")
    options.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    options.add_argument("--time-only", action="store_true", help="also take the stock CrossRoads door to Cistern")
    options = options.parse_args()
    if options.vanilla_saved and not options.vanilla_saved.is_dir():
        raise ValueError("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(exercise(options.work.resolve(), options.vanilla_saved, time_only=options.time_only))
