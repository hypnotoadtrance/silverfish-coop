"""Verify stock weather and observe FogWalkers in muted loopback replicas."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_world_view_travel_smoke import CROSSROADS
from live_multi_smoke import saved_hashes


@contextmanager
def staged_probe(work):
    probe = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishWeatherProbe"
    script = probe / "Scripts/main.lua"
    enabled = probe / "enabled.txt"
    script.parent.mkdir(parents=True)
    try:
        shutil.copyfile(Path(__file__).with_name("fixtures") / "weather_projection_probe.lua", script)
        enabled.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        enabled.unlink(missing_ok=True)
        script.parent.rmdir()
        probe.rmdir()


def walker_rows(lines, *, request=None):
    result = {}
    for line in lines:
        if request is not None:
            observed, _, line = line.partition("|")
            if observed != request:
                continue
        (name, role, authority, replicated, alive, x, y, z, remote, dormancy, movement, always, owner_only,
         owner_relevancy, cull, frequency, minimum_frequency) = line.split("|")
        assert name.startswith("NPC_FogWalker_C /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.")
        assert all(flag in ("true", "false") for flag in (authority, replicated, alive, movement, always, owner_only, owner_relevancy))
        result[name] = {"role": int(role), "remote_role": int(remote), "authority": authority == "true",
                        "replicated": replicated == "true", "alive": alive == "true", "position": [float(x), float(y), float(z)],
                        "movement_replicated": movement == "true", "dormancy": dormancy, "always_relevant": always == "true",
                        "only_relevant_to_owner": owner_only == "true", "use_owner_relevancy": owner_relevancy == "true",
                        "net_cull_distance_squared": float(cull), "net_update_frequency": float(frequency),
                        "min_net_update_frequency": float(minimum_frequency)}
    return result


async def validate(work, the_fog=False, nullrhi=False, fog_walkers=False, vanilla_saved=None):
    name = "weather-proof-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    cli = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    flags = subprocess.CREATE_NO_WINDOW
    processes = []
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    if vanilla_saved and not original:
        raise ValueError("The requested original-save canary is empty")

    async def spawn(run, *args):
        env = dict(os.environ)
        if fog_walkers:
            env["SILVERFISH_COOP_FOG_WALKERS_PROBE"] = "1"
        if nullrhi:
            env.pop("SILVERFISH_COOP_RENDER_CAPTURE", None)
        else:
            env["SILVERFISH_COOP_RENDER_CAPTURE"] = "guest"
        with (host / f"{args[0]}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*cli, *args, stdout=stream,
                stderr=stream, creationflags=flags, env=env)
        processes.append((process, run))

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*cli, *args, stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.PIPE, creationflags=flags)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
        return json.loads(stdout)

    def lines(path):
        try:
            return path.read_text().splitlines()
        except OSError:
            return []

    async def probe(run, operation):
        box, = (run / "game-bridge").iterdir()
        (box / "weather-probe").write_text(operation + "\n", encoding="ascii", newline="")
        async with asyncio.timeout(10):
            while True:
                for row in reversed(lines(box / "weather-probe-reply")):
                    received, *fields = row.split("|")
                    if fields and received == operation:
                        result, *data = fields
                        if result != "ok":
                            raise RuntimeError(row)
                        return data
                await asyncio.sleep(.1)

    def applied(row, intensity):
        frame = row.get("game", {}).get("native_world_view", {})
        skipped = nullrhi and intensity > 0
        return (row.get("state") == "connected" and frame.get("mode") == "applied"
                and frame.get("rain_verified") is (not skipped) and bool(frame.get("rain_skipped_nullrhi")) is skipped
                and frame.get("save_unchanged") is True and frame.get("weather_verified") is True
                and math.isclose(frame.get("rain_intensity", -1), intensity, abs_tol=.002))

    async def stop(process, run):
        if run.is_dir():
            (run / "stop").touch()
        try:
            await asyncio.wait_for(process.wait(), 8)
        except TimeoutError:
            process.kill()
            await process.wait()

    with staged_probe(work):
        try:
            await spawn(host, "host", "--campaign", name, "--port", "0", "--headless", "--fixtures",
                        "--native-zones", "--start-zone", CROSSROADS)
            await wait(host, lambda row: row.get("state") == "hosting")
            invite = await command("invite", "--campaign", name, "--name", "Weather guest", "--character", "Observer")
            await spawn(guest, "join", "--profile", name, "--invite", invite["invite_file"], "--headless", "--native-zones")
            baseline = await wait(guest, lambda row: applied(row, 0))
            guest_box, = (guest / "game-bridge").iterdir()
            if the_fog or fog_walkers:
                host_box, = (host / "game-bridge").iterdir()
                before = {}
                if fog_walkers:
                    for seat, run, mailbox in (("host", host, host_box), ("guest", guest, guest_box)):
                        report = await probe(run, "fog-roster-before")
                        storms, total = map(int, report[:2])
                        before[seat] = walker_rows(lines(mailbox / "fog-walkers-roster"), request="fog-roster-before")
                        assert storms == 0 and total == len(before[seat]), (seat, storms, total, before[seat])
                density, siren, duration = await probe(host, "the-fog-walkers" if fog_walkers else "the-fog")
                assert math.isclose(float(density), 1.2, abs_tol=.0001) and float(duration) == 44

                def has_fog(row, expected, mode):
                    frame = row.get("game", {}).get("native_world_view", {})
                    return (frame.get("mode") == mode and (mode == "sample" or frame.get("weather_verified") is True)
                            and isinstance(frame.get("fog_density"), (int, float))
                            and math.isclose(frame["fog_density"], expected, abs_tol=.002))

                peak_host = await wait(host, lambda row: has_fog(row, float(density), "sample"), timeout=35)
                peak_guest = await wait(guest, lambda row: has_fog(row, float(density), "applied"))
                assert peak_guest["game"]["native_world_view"]["save_unchanged"] is True
                if fog_walkers:
                    assert peak_guest["game"]["native_world_view"]["sky_verified"] is True
                async with asyncio.timeout(8):
                    while not lines(guest_box / "guest-siren"):
                        await asyncio.sleep(.1)
                await asyncio.sleep(2)
                horn, = lines(guest_box / "guest-siren")
                source, cue, remaining = horn.split("|")
                assert source == siren and "StormSiren_03_Cue" in cue and 0 < float(remaining) <= float(duration), horn
                roster = None
                if fog_walkers:
                    selected, *placement = await probe(host, "fog-roster-approach")
                    coordinates = list(map(float, placement))
                    assert len(coordinates) == 9, placement
                    marker_position, placed_host, placed_guest = coordinates[:3], coordinates[3:6], coordinates[6:]

                    def guest_near_placement(row):
                        point = row.get("game", {}).get("pawns", {}).get("local", {}).get("location")
                        return point and math.dist(point, placed_guest) <= 150

                    near = await wait(guest, guest_near_placement, timeout=8)
                    guest_arrival = near["game"]["pawns"]["local"]["location"]
                    await asyncio.sleep(3)
                    during, storm_counts, local_positions = {}, {}, {}
                    for seat, run, mailbox in (("host", host, host_box), ("guest", guest, guest_box)):
                        report = await probe(run, "fog-roster-during")
                        storms, total = map(int, report[:2])
                        during[seat] = walker_rows(lines(mailbox / "fog-walkers-roster"), request="fog-roster-during")
                        assert total == len(during[seat])
                        storm_counts[seat] = storms
                        local_positions[seat] = list(map(float, report[2:]))
                    assert math.dist(local_positions["guest"], marker_position) <= 4200
                    assert math.dist(local_positions["host"], marker_position) <= 4200
                    new_host = {name: row for name, row in during["host"].items() if name not in before["host"]}
                    new_guest = {name: row for name, row in during["guest"].items() if name not in before["guest"]}
                    members = walker_rows(lines(host_box / "fog-walkers-members"))
                    distances = {name: math.dist(row["position"], local_positions["guest"]) for name, row in new_host.items()}
                    within_cull = {name: distance**2 <= new_host[name]["net_cull_distance_squared"]
                                   for name, distance in distances.items()}
                    roster = {"host_stock_calls": lines(host_box / "fog-walkers-calls"),
                              "host_marker_references": members, "host_new_walkers": new_host,
                              "guest_stock_calls": lines(guest_box / "fog-walkers-calls"), "guest_new_walkers": new_guest,
                              "stock_storm_counts": storm_counts, "matching_paths": sorted(new_host.keys() & new_guest),
                              "host_stock_members_replicating": sum(row["replicated"] for row in new_host.values()),
                              "geometry": {"marker": selected, "marker_position": marker_position, "placed_host": placed_host,
                                           "placed_guest": placed_guest, "guest_arrival": guest_arrival,
                                           "roster_sample_players": local_positions, "guest_to_walkers_cm": distances,
                                           "guest_within_stock_cull_distance": within_cull}}
                restored, = await probe(host, "clear-fog")
                clear = await wait(guest, lambda row: has_fog(row, float(restored), "applied"))
                assert float(restored) < 1.2
                target = host / ("live-fog-walkers-observation.json" if fog_walkers else "live-the-fog-projection.json")
                evidence = {"stock_warning_seconds": float(duration), "guest_siren_once": horn,
                            "host_fog": peak_host["game"]["native_world_view"], "guest_fog": peak_guest["game"]["native_world_view"],
                            "guest_clear": clear["game"]["native_world_view"]}
                if roster is not None:
                    evidence["fog_walkers"] = roster
                target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                if roster is not None:
                    invocation, = roster["host_stock_calls"]
                    marker, asset, count, authority, selected = invocation.split("|")
                    assert marker.startswith("AI_Spawner_Squad_BP_C /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.")
                    assert asset == "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/FogWalkers.FogWalkers"
                    assert (authority, selected) == ("true", "true") and marker == roster["geometry"]["marker"], invocation
                    assert int(count) == len(new_host) == 4, roster
                    # This squad does not follow its leader. The marker keeps only the leader reference.
                    assert len(members) == 1 and members.keys() <= new_host.keys(), roster
                    assert all(member["authority"] and member["role"] == 3 for member in new_host.values()), roster
                    assert all(roster["geometry"]["guest_within_stock_cull_distance"].values()), roster
                    assert storm_counts == {"host": 1, "guest": 0} and not roster["guest_stock_calls"], roster
                    assert all(not row["authority"] and row["role"] == 1 for row in new_guest.values()), roster
                print(target)
                return
            amount, siren, replicated = await probe(host, "wet")
            assert float(amount) > 0 and replicated == "false", (amount, siren, replicated)
            wet = await wait(guest, lambda row: applied(row, float(amount)))
            wet_actors = await probe(guest, "check")
            assert wet_actors == ["0", "0" if nullrhi else "1"], wet_actors
            if nullrhi:
                clock = await wait(guest, lambda row: (
                    row.get("game", {}).get("native_guest_clock", {}).get("epoch") == wet["zone_epoch"]
                    and row.get("game", {}).get("native_world_view", {}).get("rain_skipped_nullrhi") is True))
                assert clock["game"]["native_guest_clock"]["map"] == CROSSROADS
            async with asyncio.timeout(8):
                while not lines(guest_box / "guest-siren"):
                    await asyncio.sleep(.1)
            await asyncio.sleep(2)
            visual = lines(guest_box / "guest-rain")
            if nullrhi:
                assert not visual, visual
            else:
                assert any(math.isclose(float(value), float(amount), abs_tol=.002) for value in visual), visual
            horn, = lines(guest_box / "guest-siren")
            assert siren == horn.split("|")[0] and "StormSiren_03_Cue" in horn, horn
            assert await probe(host, "dry") == ["0.0"]
            dry = await wait(guest, lambda row: applied(row, 0))
            assert await probe(guest, "check-dry") == ["0", "0"]
            evidence = {
                "baseline": baseline["game"]["native_world_view"],
                "wet": wet["game"]["native_world_view"], "dry": dry["game"]["native_world_view"],
                "native_niagara_intensity": visual, "guest_stock_rain_actors": int(wet_actors[0]),
                "guest_siren_once": horn, "native_weather_events": [
                    json.loads(line) for line in lines(guest / "events.jsonl") if "native_weather_siren_" in line
                ],
            }
            if nullrhi:
                evidence["guest_clock_during_wet_rain"] = clock["game"]["native_guest_clock"]
            target = host / "live-weather-projection.json"
            target.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(target)
        finally:
            for job, run in reversed(processes):
                await stop(job, run)
            if original is not None:
                assert saved_hashes(vanilla_saved) == original, "The original save tree changed during the private observation"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True)
    scenario = ap.add_mutually_exclusive_group()
    scenario.add_argument("--the-fog", action="store_true", help="exercise the stock Fog atmosphere and warning")
    scenario.add_argument("--fog-walkers", action="store_true", help="observe one exact stock FogWalkers squad and weather")
    ap.add_argument("--vanilla-saved", type=Path, help="hash an original save tree before and after the private trial")
    scenario.add_argument("--nullrhi", action="store_true", help="verify wet rain skips particles with the stock clock and warning active")
    args = ap.parse_args()
    asyncio.run(validate(args.work.resolve(), args.the_fog, args.nullrhi, args.fog_walkers, args.vanilla_saved))
