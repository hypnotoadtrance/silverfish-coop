"""Verify stock Crossroads GravityWell placement and host-owned lift with muted peers."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_campaign_npc_smoke import MAP


@contextmanager
def probe_mod(work):
    folder = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishGravityProbe"
    script, enabled = folder / "Scripts/main.lua", folder / "enabled.txt"
    script.parent.mkdir(parents=True)
    try:
        script.write_bytes((Path(__file__).with_name("fixtures") / "gravity_field_probe.lua").read_bytes())
        enabled.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        enabled.unlink(missing_ok=True)
        script.parent.rmdir()
        folder.rmdir()


def gravity(view):
    return [actor for actor in view.get("game", {}).get("native_anomaly_positions", []) if actor["kind"] == "gravity"]


def row(raw):
    point = []
    for item in filter(None, raw.split(";")):
        x, y, z, role, collision, tick, fx = item.split("|")
        point.append({"location": [float(x), float(y), float(z)], "role": int(role),
                      "collision": collision == "true", "tick": tick == "true", "idle_effect": fx == "true"})
    return point


async def verify(work):
    profile = "gravity-proof-" + uuid.uuid4().hex[:8]
    host, guest = [work / role / profile / "run" for role in ("campaigns", "clients")]
    host.mkdir(parents=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    flags = subprocess.CREATE_NO_WINDOW
    processes = []

    async def start(run, *args):
        with (host / f"{args[0]}.log").open("wb") as log:
            child = await asyncio.create_subprocess_exec(*cli, *args, stdout=log, stderr=log,
                creationflags=flags, env={**os.environ, "SILVERFISH_COOP_RENDER_CAPTURE": "guest"})
        processes.append((child, run))

    async def command(*args):
        child = await asyncio.create_subprocess_exec(*cli, *args, stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.PIPE, creationflags=flags)
        stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
        if child.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
        return json.loads(stdout)

    async def probe(mailbox, operation):
        reply = mailbox / "gravity-reply"
        reply.unlink(missing_ok=True)
        (mailbox / "gravity-probe").write_text(f"{operation}|{uuid.uuid4().hex}\n", encoding="ascii", newline="")
        async with asyncio.timeout(6):
            while True:
                try:
                    code, message = reply.read_text().rstrip().split("|", 1)
                except (OSError, ValueError):
                    await asyncio.sleep(.05)
                    continue
                if code != "ok":
                    raise RuntimeError(f"{operation}: {message}")
                return message

    with probe_mod(work):
        try:
            await start(host, "host", "--campaign", profile, "--port", "0", "--headless", "--fixtures",
                        "--native-zones", "--native-events", "--native-guest", "--start-zone", MAP)
            await wait(host, lambda view: view.get("state") == "hosting" and view.get("game", {}).get("map") == MAP)
            host_box, = (host / "game-bridge").iterdir()
            director = await probe(host_box, "roll")
            await wait(host, lambda view: gravity(view))
            invite = await command("invite", "--campaign", profile, "--name", "Gravity observer", "--character", "Observer")
            await start(guest, "join", "--profile", profile, "--invite", invite["invite_file"], "--headless", "--native-zones")
            guest_state = await wait(guest, lambda view: view.get("state") == "connected" and view.get("gameplay_fresh"))
            guest_box, = (guest / "game-bridge").iterdir()
            old = guest_state["game"]["native_anomaly_field"]["suppressed"]
            attempted = await probe(guest_box, "roll")
            before, after = map(int, attempted.split("|"))
            assert before == after, attempted
            await wait(guest, lambda view: view.get("game", {}).get("native_anomaly_field", {}).get("suppressed", 0) > old)
            await probe(host_box, "approach")
            host_wells = row(await probe(host_box, "view"))
            async with asyncio.timeout(12):
                while not (guest_wells := row(await probe(guest_box, "view"))):
                    await asyncio.sleep(.15)
            assert host_wells and guest_wells and all(well["role"] == 3 and well["tick"] and well["collision"]
                                                       for well in host_wells)
            for well in guest_wells:
                assert well["role"] == 1 and well["idle_effect"] and not well["tick"] and not well["collision"], well
                assert any(math.dist(well["location"], source["location"]) < 3 for source in host_wells), well
            local_effects = await probe(guest_box, "guestguard")
            assert local_effects == "0|0"
            launch_origin = await probe(host_box, "lift")
            async with asyncio.timeout(6):
                while not (host_box / "gravity-launch").exists():
                    await asyncio.sleep(.1)
            pawn, force = (host_box / "gravity-launch").read_text().strip().split("|")
            host_state = await wait(host, lambda view: view.get("game", {}).get("pawns", {}).get("guest", {}).get("name") == pawn)
            assert float(force) > 0 and not (host_box / "gravity-host-launch").exists()
            async with asyncio.timeout(6):
                while True:
                    authority = list(map(float, (await probe(host_box, "position")).split("|")))
                    recipient = list(map(float, (await probe(guest_box, "position")).split("|")))
                    if math.dist(authority, recipient) < 75:
                        break
            result = {"stock_director_counts": director, "guest_duplicate_counts": attempted,
                      "host_wells": host_wells, "guest_wells": guest_wells, "guest_local_effect_arrays": local_effects,
                      "guest_host_pawn": pawn, "host_launch_velocity_z": float(force),
                      "guest_position_on_host": authority, "guest_local_position": recipient,
                      "host_health": host_state["game"]["pawns"]["local"]["health"],
                      "launch_origin": list(map(float, launch_origin.split("|")))}
            evidence = host / "live-gravity-field.json"
            evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(evidence, flush=True)
        finally:
            for child, run in reversed(processes):
                if run.is_dir():
                    (run / "stop").touch()
                try:
                    await asyncio.wait_for(child.wait(), 8)
                except TimeoutError:
                    child.kill()
                    await child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    asyncio.run(verify(parser.parse_args().work.resolve()))
