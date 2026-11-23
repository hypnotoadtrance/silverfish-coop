"""Verify stock rain parameters and one storm warning in muted loopback replicas."""

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


async def validate(work, the_fog=False, nullrhi=False):
    name = "weather-proof-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    cli = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    flags = subprocess.CREATE_NO_WINDOW
    processes = []

    async def spawn(run, *args):
        env = dict(os.environ)
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
            if the_fog:
                density, siren, duration = await probe(host, "the-fog")
                assert math.isclose(float(density), 1.2, abs_tol=.0001) and float(duration) == 44

                def has_fog(row, expected, mode):
                    frame = row.get("game", {}).get("native_world_view", {})
                    return (frame.get("mode") == mode and (mode == "sample" or frame.get("weather_verified") is True)
                            and isinstance(frame.get("fog_density"), (int, float))
                            and math.isclose(frame["fog_density"], expected, abs_tol=.002))

                peak_host = await wait(host, lambda row: has_fog(row, float(density), "sample"), timeout=35)
                peak_guest = await wait(guest, lambda row: has_fog(row, float(density), "applied"))
                assert peak_guest["game"]["native_world_view"]["save_unchanged"] is True
                async with asyncio.timeout(8):
                    while not lines(guest_box / "guest-siren"):
                        await asyncio.sleep(.1)
                await asyncio.sleep(2)
                horn, = lines(guest_box / "guest-siren")
                source, cue, remaining = horn.split("|")
                assert source == siren and "StormSiren_03_Cue" in cue and 0 < float(remaining) <= float(duration), horn
                restored, = await probe(host, "clear-fog")
                clear = await wait(guest, lambda row: has_fog(row, float(restored), "applied"))
                assert float(restored) < 1.2
                target = host / "live-the-fog-projection.json"
                target.write_text(json.dumps({
                    "stock_warning_seconds": float(duration), "guest_siren_once": horn,
                    "host_fog": peak_host["game"]["native_world_view"],
                    "guest_fog": peak_guest["game"]["native_world_view"],
                    "guest_clear": clear["game"]["native_world_view"],
                }, indent=2), encoding="utf-8")
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True)
    scenario = ap.add_mutually_exclusive_group()
    scenario.add_argument("--the-fog", action="store_true", help="exercise the real stock Fog storm and its warning")
    scenario.add_argument("--nullrhi", action="store_true", help="verify wet rain skips particles with the stock clock and warning active")
    args = ap.parse_args()
    asyncio.run(validate(args.work.resolve(), args.the_fog, args.nullrhi))
