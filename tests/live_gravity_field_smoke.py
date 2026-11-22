"""Verify stock Crossroads GravityWell or Pyrogeist with muted peers."""

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

from live_ai_smoke import status, wait
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


def family_actors(view, family):
    return [actor for actor in view.get("game", {}).get("native_anomaly_positions", []) if actor["kind"] == family]


def row(raw):
    point = []
    for item in filter(None, raw.split(";")):
        x, y, z, role, collision, tick, fx, *attachment = item.split("|")
        observation = {"location": [float(x), float(y), float(z)], "role": int(role),
                       "collision": collision == "true", "tick": tick == "true", "idle_effect": fx == "true"}
        if attachment:
            parent, yaw, asset, replicates = attachment
            observation.update(attachment=parent == "true", yaw=float(yaw), asset=asset,
                               replicates=replicates == "true")
        point.append(observation)
    return point


def blocked_triggers(view):
    return view.get("game", {}).get("native_anomaly_field", {}).get("blocked_triggers", 0)


async def verify_pyrogeist(host, guest, host_box, guest_box, probe):
    before_body = blocked_triggers(status(guest))
    assert await probe(guest_box, "guestguard") == "0"
    body_guard = await wait(guest, lambda view: blocked_triggers(view) >= before_body + 4, timeout=5)
    await asyncio.sleep(1.6)
    assert not await probe(guest_box, "jets") and not (guest_box / "pyrogeist-damage").exists()
    before = float(await probe(host_box, "enter"))
    async with asyncio.timeout(12):
        while not (host_jets := row(await probe(host_box, "jets"))) or not any(jet["replicates"] for jet in host_jets):
            await asyncio.sleep(.08)
        assert all(jet["role"] == 3 and jet["collision"] and jet["attachment"]
                   for jet in host_jets), host_jets
        while not (guest_jets := row(await probe(guest_box, "jets"))) or not any(
                jet["attachment"] and jet["idle_effect"] and not jet["tick"] for jet in guest_jets):
            await asyncio.sleep(.08)
    host_jets = row(await probe(host_box, "jets"))
    assert host_jets and all(jet["replicates"] for jet in host_jets), host_jets
    for jet in guest_jets:
        assert (jet["role"] == 1 and not jet["collision"] and not jet["tick"]
                and jet["idle_effect"] and jet["attachment"]), jet
        assert any(math.dist(jet["location"], source["location"]) < 150 and
                   abs((jet["yaw"] - source["yaw"] + 180) % 360 - 180) < 4 for source in host_jets), jet
    before_jet = blocked_triggers(status(guest))
    attempted_hits = int(await probe(guest_box, "jetguard"))
    jet_guard = await wait(guest, lambda view: blocked_triggers(view) >= before_jet + attempted_hits, timeout=5)
    async with asyncio.timeout(7):
        while await probe(host_box, "jets") or await probe(guest_box, "jets"):
            await asyncio.sleep(.1)
    assert not (guest_box / "pyrogeist-damage").exists()
    hits = [line.split("|") for line in (host_box / "pyrogeist-damage").read_text().splitlines()]
    assert hits and all(seat == "guest" and role == "3" and 30 <= float(fire) <= 60
                        for seat, _, role, fire, _ in hits), hits
    after = await wait(host, lambda view: view.get("game", {}).get("pawns", {}).get("guest", {}).get("health", before) < before)
    remote = after["game"]["pawns"]["guest"]["health"]
    return {"host_jets": host_jets, "guest_jets": guest_jets, "retired_on_both": True,
            "guest_native_vetoes_after_body_probe": blocked_triggers(body_guard) - before_body,
            "guest_native_vetoes_after_hit_probe": blocked_triggers(jet_guard) - before_jet,
            "stock_authority_damage": hits, "guest_health_before": before, "guest_health_on_host": remote,
            "host_health": after["game"]["pawns"]["local"]["health"]}


async def verify(work, family):
    profile = f"{family}-proof-" + uuid.uuid4().hex[:8]
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
            for attempt in range(6):
                director = await probe(host_box, "roll")
                try:
                    await wait(host, lambda view: family_actors(view, family), timeout=3)
                    break
                except RuntimeError:
                    if attempt == 5:
                        raise
            anchor = list(map(float, (await probe(host_box, "prepare")).split("|")))
            await wait(host, lambda view: math.dist(
                view.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), anchor) < 1500)
            invite = await command("invite", "--campaign", profile, "--name", "Anomaly observer", "--character", "Observer")
            await start(guest, "join", "--profile", profile, "--invite", invite["invite_file"], "--headless", "--native-zones")
            guest_state = await wait(guest, lambda view: view.get("state") == "connected" and view.get("gameplay_fresh"))
            guest_box, = (guest / "game-bridge").iterdir()
            old = guest_state["game"]["native_anomaly_field"]["suppressed"]
            attempted = await probe(guest_box, "roll")
            before, after = map(int, attempted.split("|"))
            assert before == after, attempted
            await wait(guest, lambda view: view.get("game", {}).get("native_anomaly_field", {}).get("suppressed", 0) > old)
            tolerance = 150 if family == "pyrogeist" else 3
            async with asyncio.timeout(12):
                while True:
                    host_view, guest_view = await asyncio.gather(probe(host_box, "view"), probe(guest_box, "view"))
                    host_wells, guest_wells = row(host_view), row(guest_view)
                    matching = [well for well in guest_wells if any(
                        math.dist(well["location"], source["location"]) < tolerance for source in host_wells)]
                    if family == "pyrogeist":
                        matching = [well for well in matching if math.dist(well["location"], anchor) < 160]
                    if matching:
                        guest_wells = matching
                        break
                    await asyncio.sleep(.15)
            assert host_wells and guest_wells and all(well["role"] == 3 and well["tick"] and well["collision"]
                                                       for well in host_wells)
            for well in guest_wells:
                assert well["role"] == 1 and well["idle_effect"] and not well["tick"] and not well["collision"], well
            common = {"stock_director_counts": director, "guest_duplicate_counts": attempted,
                      "host_actors": host_wells, "guest_actors": guest_wells}
            if family == "pyrogeist":
                common.update(await verify_pyrogeist(host, guest, host_box, guest_box, probe))
                evidence = host / "live-pyrogeist-field.json"
                evidence.write_text(json.dumps(common, indent=2), encoding="utf-8")
                print(evidence, flush=True)
                return
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
            result = {**common, "guest_local_effect_arrays": local_effects,
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
    parser.add_argument("--family", choices=("gravity", "pyrogeist"), default="gravity")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.family))
