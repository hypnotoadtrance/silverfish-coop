"""Compare stock trap fields across a muted Direct IP join and reconnect."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, LAB_E206

PLACED = {
    "lab-e206": (LAB_E206, "burner", "BurnerTrap_BP_C_1", 2),
    "perdition": ("/Game/Classes/Maps/Maps_Dungeons/Perdition_01_P", "starburst", "Starburst_BP_C_5", 5),
    "funnel": ("/Game/Classes/Maps/Maps_Dungeons/Funnel_01_P", "vortex", "VortexTrap_BP_C_9", 12),
}


async def verify(work, vanilla, activate, placed, generated_vortex):
    name = f"anomaly-field-{uuid.uuid4().hex[:8]}"
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    print(host, flush=True)
    original = saved_hashes(vanilla)
    if not original:
        raise ValueError("Choose a populated copied singleplayer Saved folder")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    flags = ("--native-zones", "--headless")
    area = PLACED[placed][0] if placed else CROSSROADS
    jobs = []
    proof = {"verified": False}

    async def launch(run, label, *arguments):
        env = dict(os.environ)
        if activate and label == "guest":
            env["SILVERFISH_COOP_RENDER_CAPTURE"] = "guest"
        with (host / f"{label}.log").open("wb") as log:
            job = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log, cwd=REPO,
                                                       creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        jobs.append((run, job))

    def scene(status):
        return status.get("game", {}).get("native_anomaly_field", {})

    def positions(status):
        return status.get("game", {}).get("native_anomaly_positions", [])

    def placed_traps(status):
        return status.get("game", {}).get("native_placed_anomalies", {})

    def sensors(status):
        return status.get("game", {}).get("native_vortex_sensors", [])

    def vortex_sensor(status):
        return next((sensor for sensor in sensors(status) if math.dist(sensor["root"], traps["vortex"]["location"]) < 3), None)

    def projected(status, trap):
        return next((row for row in positions(status) if row["kind"] == trap["kind"]
                     and math.dist(row["location"], trap["location"]) < 3), None)

    def jet(status, position, reach):
        return next((row for row in status.get("game", {}).get("native_burner_jets", [])
                     if math.dist(row["location"], position) < reach), None)

    def played_cues(kind):
        log = guest / "events.jsonl"
        if not log.exists():
            return []
        text = log.read_text(encoding="utf-8")
        if kind == "starburst":
            x, y, z = traps[kind]["location"]
            # Stock Starburst cues originate at its sphere, 200 units above the actor root.
            source = [x, y, z + 200]
        else:
            source = proof["host_vortex_sensor"]["visual"]
        return [event for line in text[:text.rfind("\n")].splitlines()
                if (event := json.loads(line)).get("event") == f"native_{kind}_effect_played"
                and math.dist(event["location"], source) < 5]

    try:
        await launch(host, "host", "host", "--campaign", name, "--port", "0", "--start-zone", area,
                     "--native-guest", "--native-events", "--native-expanded-zones", "--fixtures", *flags)
        initial = await wait(host, lambda row: row.get("state") == "hosting" and scene(row))
        job = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", "Field guest",
                                                   "--character", "Surveyor", stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE, cwd=REPO,
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(job.communicate(), 30)
        if job.returncode:
            raise RuntimeError(errors.decode(errors="replace"))
        invite = json.loads(output)

        traps = {}
        targets = {}
        authored = {}
        if placed:
            _, kind, name_in_level, expected = PLACED[placed]
            target = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{name_in_level}"
            initial = await wait(host, lambda row: sum(trap["kind"] == kind for trap in placed_traps(row).values()) == expected
                                 and target in placed_traps(row))
            authored = placed_traps(initial)
            traps[kind] = authored[target]
            targets[kind] = target
            assert all(trap["collision"] and trap["role"] == 3 for trap in authored.values()), authored
            proof["placed_host_paths"] = authored
        else:
            # Generate the stock fields before admission to verify hot join receives their placement.
            for kind in (("vortex",) if generated_vortex else ("starburst", "burner")):
                earlier = {tuple(entry["location"]) for entry in positions(initial) if entry["kind"] == kind}
                result = await submit(host, "native-anomaly-field-fixture", target=kind)
                assert result["ok"], result
                initial = await wait(host, lambda row: any(entry["kind"] == kind and tuple(entry["location"]) not in earlier
                                                         for entry in positions(row)))
                trap, = [entry for entry in positions(initial) if entry["kind"] == kind and tuple(entry["location"]) not in earlier]
                assert trap["collision"], trap
                traps[kind], targets[kind] = trap, kind
        proof["host"] = traps
        placed_sensors = {}
        if "vortex" in traps:
            sensor_frame = await wait(host, vortex_sensor)
            proof["host_vortex_sensor"] = vortex_sensor(sensor_frame)
            placed_sensors = {sensor["path"]: sensor for sensor in sensors(sensor_frame) if sensor["path"]}
            assert placed_sensors.keys() == {path for path, trap in authored.items() if trap["kind"] == "vortex"}

        await launch(guest, "guest", "join", "--profile", name, "--invite", invite["invite_file"], *flags)
        def replicas_visible(row):
            return (row.get("state") == "connected" and all(projected(row, trap) for trap in traps.values())
                    and placed_traps(row).keys() == authored.keys())

        def same_sensor(sensor, expected):
            return (sensor and math.dist(sensor["relative"], expected["relative"]) < .05
                    and math.dist(sensor["center"], expected["center"]) < 3)

        def matching_vortex(row):
            if placed_sensors:
                guest_sensors = {sensor["path"]: sensor for sensor in sensors(row) if sensor["path"]}
                return all(same_sensor(guest_sensors.get(path), expected) for path, expected in placed_sensors.items())
            return same_sensor(vortex_sensor(row), proof["host_vortex_sensor"])

        peer = await wait(guest, replicas_visible)
        if "vortex" in traps:
            peer = await wait(guest, matching_vortex)
            proof["guest_vortex_sensor"] = vortex_sensor(peer)
            proof["matched_vortex_sensors"] = len(placed_sensors) or 1
        replicas = {kind: projected(peer, trap) for kind, trap in traps.items()}
        assert all(not replica["collision"] for replica in replicas.values()), replicas
        proof["guest"] = replicas
        if placed:
            copies = placed_traps(peer)
            for path, original_trap in authored.items():
                copy = copies[path]
                assert copy["kind"] == original_trap["kind"] and not copy["collision"], copy
                assert math.dist(copy["location"], original_trap["location"]) < 3, copy
                assert sum(math.dist(row["location"], copy["location"]) < 3 for row in positions(peer)) == 1, copy
            proof["placed_guest_paths"] = copies
            counter, fixture = "blocked_triggers", "native-placed-anomaly-guest-fixture"
        else:
            counter, fixture = "suppressed", "native-anomaly-field-fixture"
        prior = scene(peer)[counter]
        for target in targets.values():
            result = await submit(guest, fixture, target=target)
            assert result["ok"], result
        vetoed = await wait(guest, lambda row: scene(row).get(counter, 0) >= prior + len(traps))
        assert scene(vetoed)["live"] == scene(peer)["live"], vetoed
        proof[counter] = scene(vetoed)[counter] - prior
        assert (await submit(guest, "drop-link"))["ok"]
        await wait(guest, lambda row: row.get("reconnect_suspended") and not row.get("link_connected"))
        resumed = await submit(guest, "reconnect", timeout=35)
        assert resumed["ok"] and resumed["resumed"], resumed
        rejoined = await wait(guest, replicas_visible)
        if "vortex" in traps:
            rejoined = await wait(guest, matching_vortex)
        assert all(not projected(rejoined, trap)["collision"] for trap in traps.values()), rejoined
        proof["reconnect"] = True
        if activate:
            first = await wait(host, lambda row: row.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            health = first["game"]["pawns"]["local"]["health"]
            guest_health = first["game"]["pawns"]["guest"]["health"]
            assert health > 0 and guest_health > 0, first["game"]["pawns"]
            for kind, target in targets.items():
                triggered = await submit(host, "native-anomaly-trigger-fixture", target=target)
                assert triggered["ok"], triggered
                if kind == "starburst":
                    await wait(guest, lambda _: {event["cue"] for event in played_cues(kind)} == {"charge", "blast"}, timeout=14)
                    cues = played_cues(kind)
                    assert len(cues) == 2 and all(event["particles"] for event in cues), cues
                    proof["starburst_cues"] = cues
                elif kind == "vortex":
                    await wait(guest, lambda _: played_cues(kind), timeout=14)
                    cue, = played_cues(kind)
                    assert cue["particles"] and cue["animated"], cue
                    proof["vortex_cue"] = cue
                else:
                    burning = await wait(host, lambda row: jet(row, traps[kind]["location"], 250), timeout=16)
                    host_jet = jet(burning, traps[kind]["location"], 250)
                    viewed = await wait(guest, lambda row: (jet(row, host_jet["location"], 3) or {}).get("particles_active"), timeout=10)
                    guest_jet = jet(viewed, host_jet["location"], 3)
                    assert host_jet["collision"] and not guest_jet["collision"] and guest_jet["particles_active"], (host_jet, guest_jet)
                    await wait(guest, lambda row: row.get("state") == "connected" and not jet(row, host_jet["location"], 3), timeout=15)
                    await wait(host, lambda row: not jet(row, host_jet["location"], 3), timeout=8)
                    proof["burner_jet"] = {"host": host_jet, "guest": guest_jet, "expired_on_both": True}
                struck = await wait(host, lambda row: row.get("game", {}).get("player_effects", {}).get("guest", {}).get("state") == "downed"
                                    or (row.get("game", {}).get("pawns", {}).get("guest", {}).get("health") or guest_health) < guest_health,
                                    timeout=8)
                assert struck["game"]["pawns"]["local"]["health"] == health and not struck["gameplay"]["wipe"], struck["game"]["pawns"]
                proof[f"{kind}_guest_effect"] = {
                    "state": struck["game"]["player_effects"].get("guest", {}).get("state"),
                    "health_before": guest_health,
                    "health_after": struck["game"]["pawns"]["guest"]["health"],
                }
                if placed:
                    nearby = await wait(guest, replicas_visible)
                    copy = placed_traps(nearby)[target]
                    assert not copy["collision"] and math.dist(copy["location"], traps[kind]["location"]) < 3, copy
                    assert sum(math.dist(row["location"], copy["location"]) < 3 for row in positions(nearby)) == 1, copy
                    proof["placed_active_guest"] = copy
            after = await wait(host, lambda row: row.get("game", {}).get("pawns", {}).get("local", {}).get("health") is not None)
            assert after["game"]["pawns"]["local"]["health"] == health, after["game"]["pawns"]
            proof["host_health_preserved"] = health
        proof["verified"] = True
    except BaseException as error:
        proof["failure"] = str(error) or type(error).__name__
        raise
    finally:
        for run, job in reversed(jobs):
            if job.returncode is not None:
                continue
            try:
                await submit(run, "stop", timeout=12)
                await asyncio.wait_for(job.wait(), 12)
            except (OSError, RuntimeError, TimeoutError):
                if job.returncode is None:
                    job.terminate()
                    await job.wait()
        proof["original_saves_verified"] = saved_hashes(vanilla) == original
        proof["verified"] &= proof["original_saves_verified"]
        (host / "live-anomaly-field-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"], "Original singleplayer saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--placed", choices=PLACED)
    selection.add_argument("--generated-vortex", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), args.activate, args.placed, args.generated_vortex))
