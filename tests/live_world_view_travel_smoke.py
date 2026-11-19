"""Verify authored sun and weather projection across three staged loopback maps."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


OVERWORLD = "/Game/Classes/Maps/Maps_OverWorld/"
CROSSROADS = OVERWORLD + "CrossRoads_P"
DEAD_BRIDGE = OVERWORLD + "DeadBridge_P"
CISTERN = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
DAY_ACTORS = {
    CROSSROADS: "DayCycle_SunControler_BP_C " + CROSSROADS
    + ".CrossRoads_P:PersistentLevel.DayCycle_SunControler_BP_C_1",
    DEAD_BRIDGE: "DayCycle_SunControler_BP_C " + DEAD_BRIDGE
    + ".DeadBridge_P:PersistentLevel.DayCycle_SunControler_BP_C_0",
}
WEATHER_FIELDS = ("fog_density", "fog_falloff", "cloud_bottom_km", "cloud_height_km", "cloud_density_offset")


async def validate(work: Path, vanilla_saved: Path | None) -> None:
    name = "world-view-" + uuid.uuid4().hex[:8]
    host_run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    host_run.mkdir(parents=True, exist_ok=True)
    cli = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    authority = ("action", "--campaign", name)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    processes = []
    evidence = {}

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is False:
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def start(kind, target, *arguments):
        with (host_run / (kind + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *target, *arguments, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        processes.append((process, target))

    def status(directory):
        try:
            return json.loads((directory / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    async def wait(directory, condition, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = status(directory)
            if condition(state):
                return state
            if state.get("state") == "failed":
                break
            await asyncio.sleep(0.2)
        raise RuntimeError(f"{directory} did not reach the expected world view state: {status(directory)}")

    def host_ready(state, map_name):
        game = state.get("game", {})
        return (state.get("state") == "hosting" and game.get("map") == map_name
                and game.get("remote_clients") == 1 and not state.get("awaiting_zone_guest")
                and game.get("native_zone", {}).get("state") == "ready")

    def client_ready(state, map_name):
        return (state.get("state") == "connected" and state.get("link_connected")
                and state.get("gameplay_fresh") is True
                and state.get("game", {}).get("map") == map_name)

    async def projection(map_name, previous_epoch=None):
        def matches(state):
            local = state.get("game", {}).get("native_world_view", {})
            sample = state.get("gameplay", {}).get("host_world", {}).get("native_world_view", {})
            return (client_ready(state, map_name) and state.get("zone_epoch") != previous_epoch
                    and local.get("map") == sample.get("map") == map_name
                    and local.get("actor") == sample.get("actor") == DAY_ACTORS[map_name]
                    and local.get("epoch") == state.get("zone_epoch") and local.get("mode") == "applied"
                    and sample.get("mode") == "sample" and local.get("sequence") == sample.get("sequence")
                    and local.get("save_unchanged") is True and local.get("weather_verified") is True
                    and local.get("sky_verified") is True)

        client = await wait(client_run, matches)
        local = client["game"]["native_world_view"]
        sample = client["gameplay"]["host_world"]["native_world_view"]
        assert local["clock_text"] not in ("", "-") and local["game_seconds"] == sample["game_seconds"], local
        for keys in (("sun_x", "sun_y", "sun_z"), ("sky_r", "sky_g", "sky_b")):
            assert math.dist([local[key] for key in keys], [sample[key] for key in keys]) <= 0.025, local
        assert local["sun_visible"] == sample["sun_visible"], local
        assert abs(local["sun_intensity"] - sample["sun_intensity"]) <= 0.025, local
        assert all(abs(local[key] - sample[key]) <= 0.0001 for key in WEATHER_FIELDS), local
        return client, local

    async def cross_stock_exit(source):
        await wait(host_run, lambda state: host_ready(state, source))
        for role, position in (("host", "near"), ("guest", "near"), ("host", "inside")):
            await command(*authority, "native-exit-position", "--target", role, "--exit-position", position)

    try:
        await start("host", ("--campaign", name), "--port", "0", "--headless", "--fixtures",
                    "--native-zones", "--start-zone", CROSSROADS)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invite = await command("invite", "--campaign", name, "--name", "World view guest", "--character", "Sky observer")
        await start("join", ("--profile", name), "--invite", invite["invite_file"], "--headless", "--native-zones")
        initial, evidence["crossroads"] = await projection(CROSSROADS)

        await cross_stock_exit(CROSSROADS)
        bridge, evidence["dead_bridge"] = await projection(DEAD_BRIDGE, initial["zone_epoch"])
        await cross_stock_exit(DEAD_BRIDGE)
        returned, evidence["crossroads_return"] = await projection(CROSSROADS, bridge["zone_epoch"])
        await wait(host_run, lambda state: host_ready(state, CROSSROADS))
        for role in ("host", "guest"):
            await command(*authority, "native-door-position", "--target", role, "--exit-position", "near")
        await command(*authority, "native-door-probe", "--target", "host")
        cistern = await wait(client_run, lambda state: client_ready(state, CISTERN)
                             and state.get("zone_epoch") != returned["zone_epoch"])
        cistern_host = await wait(host_run, lambda state: state.get("game", {}).get("map") == CISTERN
                                  and state["game"].get("remote_clients") == 1 and not state.get("awaiting_zone_guest"))
        assert all(client["host_session"] == initial["host_session"] and client["character_id"] == invite["character_id"]
                   for client in (initial, bridge, returned, cistern)), cistern

        # Poll through the previous three-second visual lease and require newer frames
        # from both pumps, so a frozen status cannot satisfy the negative assertion.
        started = (cistern_host["updated_unix"], cistern["updated_unix"])
        deadline = time.monotonic() + 4
        host, client = {}, {}
        while time.monotonic() < deadline:
            host, client = status(host_run), status(client_run)
            for state in (host, client):
                if state:
                    assert state["game"]["map"] == CISTERN and "native_world_view" not in state["game"], state
            assert "native_world_view" not in client.get("gameplay", {}).get("host_world", {}), client
            await asyncio.sleep(0.2)
        assert client_ready(client, CISTERN) and host["game"]["remote_clients"] == 1
        assert all(row["updated_unix"] > initial_time for row, initial_time in zip((host, client), started))
        evidence["cistern_no_world_projection"] = {"map": CISTERN, "epoch": client["zone_epoch"], "seconds_observed": 4}
    finally:
        for process, target in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *target, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            assert original == saved_hashes(vanilla_saved), "Original singleplayer save files changed"
            evidence["original_files_verified"] = len(original)
        print(host_run, flush=True)

    (host_run / "live-world-view-travel-verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path)
    arguments = parser.parse_args()
    asyncio.run(validate(arguments.work.resolve(), arguments.vanilla_saved))
