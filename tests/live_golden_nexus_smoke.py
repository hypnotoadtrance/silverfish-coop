"""Exercise one named Golden Nexus pad in a staged loopback co-op campaign."""

import argparse
import asyncio
from dataclasses import asdict
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import CROSSROADS, GOLD_NEXUS


PADS = {route.actor: route for route in AUTHORED_ROUTES if route.source == GOLD_NEXUS}
DEFAULT_PAD = "Level_Exit_BP_C_179"
DUPLICATE_TAG = "Nexus_CrossRoads"
DIAGONAL = 1000 / math.sqrt(2)
PAD_CENTERS = {
    "Level_Exit_BP_C_178": (0, -1000),
    "Level_Exit_BP_C_179": (DIAGONAL, -DIAGONAL),
    "Level_Exit_BP_C_180": (1000, 0),
    "Level_Exit_BP_C_181": (DIAGONAL, DIAGONAL),
    "Level_Exit_BP_C_182": (-DIAGONAL, DIAGONAL),
    "Level_Exit_BP_C_183": (-1000, 0),
    "Level_Exit_BP_C_184": (-DIAGONAL, -DIAGONAL),
}


def crypt_approach(state, pad_actor):
    game = state.get("game", {})
    if game.get("map") != GOLD_NEXUS:
        return None
    center_x, center_y = PAD_CENTERS[pad_actor]
    inward_x, inward_y = -center_x / 1000, -center_y / 1000
    pawns = game.get("pawns", {})
    coordinates = {}
    for side, alias in ((-1, "local"), (1, "guest")):
        point = pawns.get(alias, {}).get("location", ())
        if len(point) != 3 or not all(type(value) in (float, int) and math.isfinite(value) for value in point):
            return None
        dx, dy = point[0] - center_x, point[1] - center_y
        depth = dx * inward_x + dy * inward_y
        lane = dx * inward_y - dy * inward_x
        if not -150 <= depth < -20 or not 0 < side * lane < 75:
            return None
        coordinates[alias] = {"location": point, "depth_cm": depth, "lane_cm": lane}
    return coordinates


async def exercise(work, vanilla_saved, pad):
    name = "gold-pad-" + uuid.uuid4().hex[:8]
    authority, visitor = ("--campaign", name), ("--profile", name)
    run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    run.mkdir(parents=True, exist_ok=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    endpoint = ("--entrance", pad.entrance, "--exit-actor", pad.actor)
    processes = []
    evidence = {"pad": asdict(pad)}

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 30)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def launch(mode, owner, *arguments):
        with (run / f"{mode}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, mode, *owner, *arguments, cwd=REPO,
                                                            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))

    async def event(event_name, match):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
            except (FileNotFoundError, PermissionError):
                lines = []
            for line in reversed(lines):
                if line.endswith("\n"):
                    row = json.loads(line)
                    if row["event"] == event_name and match(row):
                        return row
            await asyncio.sleep(.15)
        raise TimeoutError(f"The Golden Nexus test did not receive {event_name}")

    async def enrolled(area, character, previous=None):
        def anchored(state):
            game = state.get("game", {})
            return (state.get("state") == "hosting" and game.get("map") == area
                    and bool(state.get("zone_epoch")) and state.get("zone_epoch") != previous
                    and game.get("remote_clients") == 1 and not state.get("awaiting_zone_guest")
                    and game.get("native_guest_save", {}).get("state") == "bound"
                    and game.get("native_zone", {}).get("state") == "ready")

        host = await wait(run, anchored, timeout=60)
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and state.get("host_session") == host["generation"] and state.get("zone_epoch") == host["zone_epoch"]
                   and state.get("character_id") == character and state.get("game", {}).get("map") == area, timeout=60)
        return host

    async def cube(operation, guid, area, state):
        result = (await command("action", *authority, f"native-gold-{operation}", "--target", guid))["native_gold"]
        assert (result["operation"], result["guid"], result["map"], result["state"]) == (operation, guid, area, state), result
        return result

    async def sweep(role, outcome):
        result = await command("action", *authority, "native-exit-wall-probe", "--target", role, *endpoint)
        wall = result["native_zone_wall"]
        assert result["exit_actor"] == pad.actor and wall["outcome"] == outcome, result
        return wall

    async def capture_arrival():
        # The following checkpoint or guest restore clears this one-command bridge receipt.
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            for path in (run / "game-bridge").glob("*/status.tsv"):
                try:
                    rows = path.read_text(encoding="utf-8").splitlines()
                except (OSError, ValueError):
                    continue
                for row in rows:
                    tag, *fields = row.split("\t")
                    if tag == "native-authored-arrival" and len(fields) == 6:
                        destination, actor, placement, *location = fields
                        return {"destination": destination, "actor": actor, "placement": placement,
                                "location": [float(value) for value in location]}
            await asyncio.sleep(.025)
        raise TimeoutError("The stock pad did not expose its exact authored arrival receipt")

    def gold_ledger(state):
        return state["campaign"]["world"]["party"].get("gold_transport")

    try:
        await launch("host", authority, "--port", "0", "--headless", "--fixtures", "--native-events", "--native-guest",
                     "--native-zones", "--native-expanded-zones", "--native-zone-walls", "--start-zone", CROSSROADS)
        await wait(run, lambda state: state.get("state") == "hosting")
        invite = await command("invite", *authority, "--name", "Golden Nexus guest", "--character", "Nexus traveler")
        character = invite["character_id"]
        await launch("join", visitor, "--headless", "--native-zones", "--native-zone-walls", "--invite", invite["invite_file"])
        start = await enrolled(CROSSROADS, character)
        await wait(run, lambda state: state.get("game", {}).get("native_transport", {}).get("state") == "ready")
        guid = uuid.uuid4().hex.upper()
        seed = await cube("seed", guid, CROSSROADS, "seeded")
        pressed = await cube("action-probe", guid, CROSSROADS, "requested")
        assert seed["pawn"] == pressed["pawn"] == start["game"]["pawns"]["local"]["name"], pressed
        ingress = await event("native_gold_request", lambda row: row.get("guid") == guid)
        evidence["stock_cube_ingress"] = ingress
        assert (ingress["source"], ingress["entry"]) == (CROSSROADS, "action_use"), ingress
        assert tuple(ingress["result"][key] for key in ("ok", "map", "entrance")) == (True, GOLD_NEXUS, "PlayerStart_1"), ingress
        nexus = await enrolled(GOLD_NEXUS, character, start["zone_epoch"])
        assert nexus["generation"] == start["generation"] and nexus["zone_epoch"] == ingress["result"]["travel_epoch"], ingress
        spent = {"pending": None, "spent": {guid: "arrived"}}
        assert gold_ledger(nexus) == spent, nexus
        assert math.dist(nexus["game"]["pawns"]["local"]["location"], (0, 1050, 150)) <= 350, nexus
        evidence["cube_after_ingress"] = await cube("inspect", guid, GOLD_NEXUS, "absent")

        # Refuse an ambiguous request at the PlayerStart before approaching any pad.
        for operation, extra in (("native-exit-position", ("--exit-position", "near")), ("native-exit-wall-probe", ())):
            rejected = await command("action", *authority, operation, "--target", "guest", *extra,
                                     "--entrance", DUPLICATE_TAG, expected=False)
            assert "multiple stock exits" in rejected["error"] and "--exit-actor" in rejected["error"], rejected
            evidence.setdefault("duplicate_tag_refusals", {})[operation] = rejected["error"]

        for role in ("host", "guest"):
            placed = await command("action", *authority, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
            assert (placed["exit_actor"], placed["entrance"], placed["target"]) == (pad.actor, pad.entrance, role), placed
        approach = await wait(run, lambda state: crypt_approach(state, pad.actor), timeout=8)
        evidence["stock_crypt_approach"] = crypt_approach(approach, pad.actor)
        await wait(run, lambda state: (wall := state.get("game", {}).get("native_zone_wall", {})).get("host_open") is True
                   and pad.actor + " (" not in wall.get("failure", ""), timeout=12)
        evidence["guest_wall"] = await sweep("guest", "wall")
        assert evidence["guest_wall"]["slot"] == "guest", evidence["guest_wall"]
        arrival_receipt = asyncio.create_task(capture_arrival())
        try:
            evidence["host_wall"] = await sweep("host", "clear")
            marker = await arrival_receipt
        finally:
            if not arrival_receipt.done():
                arrival_receipt.cancel()
            await asyncio.gather(arrival_receipt, return_exceptions=True)

        evidence["stock_arrival"] = marker
        destination_name = pad.destination.rsplit("/", 1)[1]
        full_name = f"LevelExit_Spawn_BP_C {pad.destination}.{destination_name}:PersistentLevel.{pad.arrival_actor}"
        assert (marker["destination"], marker["actor"], marker["placement"]) == (pad.destination, full_name, "arrow"), marker
        assert len(marker["location"]) == 3 and all(math.isfinite(value) for value in marker["location"]), marker

        arrived = await enrolled(pad.destination, character, nexus["zone_epoch"])
        egress = await event("native_authored_zone_request", lambda row: row.get("actor") == pad.actor and row.get("source") == GOLD_NEXUS)
        evidence["stock_pad_egress"] = egress
        assert tuple(egress[key] for key in ("destination", "entrance", "kind", "travel_time_step")) == (
            pad.destination, pad.entrance, "overlap", 1,
        ), egress
        assert egress["result"]["ok"] is True and egress["result"]["travel_epoch"] == arrived["zone_epoch"], egress
        assert gold_ledger(arrived) == spent and arrived["generation"] == start["generation"], arrived
        evidence["cube_after_egress"] = await cube("inspect", guid, pad.destination, "absent")
        evidence.update(character_id=character, verified=True)
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
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        (run / "live-golden-nexus-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", required=True, type=Path, help="prepared staged workspace")
    options.add_argument("--vanilla-saved", required=True, type=Path, help="original Saved directory to hash before and after")
    options.add_argument("--exit-actor", choices=sorted(PADS), default=DEFAULT_PAD, help="exact stock pad (default is NorthEast Rail)")
    args = options.parse_args()
    if not args.vanilla_saved.is_dir():
        options.error("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), PADS[args.exit_actor]))
