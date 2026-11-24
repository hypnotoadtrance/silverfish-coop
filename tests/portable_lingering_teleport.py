"""Verify authored Travel spawners and their stock Lingering teleporter in a paired campaign."""

import asyncio
import math
import time
from pathlib import Path

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def lingering_overlay(game, portable_script_overlay):
    fixture = (Path(__file__).with_name("fixtures") / "portable_lingering_teleport_probe.lua").read_text(encoding="utf-8")
    definitions = "function fields.command(args, state, phase)\n"
    publication = "    local rows, live = {}, 0\n"

    def add_fixture(script):
        if script.count(definitions) != 1 or script.count(publication) != 1:
            raise ValueError("The replica lacks the stock anomaly field publication")
        return (script.replace(definitions, fixture + "\n" + definitions, 1)
                .replace(publication, "    poll_lingering_fixture(state, phase)\n" + publication, 1))

    return portable_script_overlay(game, "native_anomaly_fields.lua", add_fixture)


async def verify_stock_lingering_teleport(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    ready = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if ready["game"]["map"] != CROSSROADS:
        assert ready["game"]["map"] == OBSERVATORY, ready["game"]["map"]
        for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, position, "Obsv_Etr", "Level_Exit_BP_C_1")
    await wait_resident_status(host_run, lambda f: f.get("game", {}).get("map") == CROSSROADS
                               and not f.get("awaiting_zone_guest")
                               and f["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
    guest_frame = await wait_resident_status(guest_run, lambda f: f.get("state") == "connected"
                                            and f.get("game", {}).get("map") == CROSSROADS, timeout=35)

    async def operate(seat, action, point=""):
        return await probe(seat.bridge, "portable-lingering", f"{action}\t{point}")

    def location(value):
        return [float(axis) for axis in value.split(",")]

    async def near(run, player, point):
        def arrived(frame):
            observed = frame.get("game", {}).get("pawns", {}).get(player, {}).get("location")
            return observed and math.dist(observed, point) < 150

        return await wait_resident_status(run, arrived, timeout=12)

    checkpoint = campaign_save_hashes(host.campaign)
    assert await operate(guest, "guest-spawn-attempt") == "guest-spawn-suppressed"
    spawned = await operate(host, "prepare")
    assert spawned in {"natural", "stock-spawner"}, spawned
    try:
        async with asyncio.timeout(12):
            while (staged := await operate(host, "stage")) == "waiting":
                await asyncio.sleep(.2)
    except TimeoutError as error:
        raise AssertionError(f"The {spawned} Travel teleporter did not reach the native host roster") from error
    source, guest_start = staged.split(";")
    assert source.startswith(CROSSROADS + ".CrossRoads_P:PersistentLevel.Artifact_Spawner_BP_C_"), source
    guest_start = location(guest_start)
    try:
        await near(guest_run, "local", guest_start)
        async with asyncio.timeout(21):
            initial = location(await operate(host, "inspect"))
            while True:
                authority = await operate(host, "inspect")
                projection = await operate(guest, "inspect", authority)
                if projection != "waiting":
                    host_move = math.dist(initial, location(authority))
                    guest_move = math.dist(initial, location(projection))
                    if min(host_move, guest_move) > 75:
                        break
                await asyncio.sleep(.24)
        before = await wait_resident_status(guest_run, lambda f: f["game"].get("native_anomaly_field"))
        blocked = before["game"]["native_anomaly_field"]["blocked_triggers"]
        overlap_location = location(await operate(guest, "guest-overlap"))
        await wait_resident_status(guest_run, lambda f: f["game"].get("native_anomaly_field", {})
                                   .get("blocked_triggers", 0) > blocked, timeout=6)
        await near(guest_run, "local", guest_start)
        started = time.time() - .01
        destination = location(await operate(host, "host-overlap"))
        assert math.dist(guest_start, destination) > 200, (guest_start, destination)
        await asyncio.gather(near(host_run, "guest", destination), near(guest_run, "local", destination))
        async with asyncio.timeout(8):
            while True:
                # NullRHI can dispatch the stock effect without allocating a Niagara component.
                cues = [row for row in await resident_events(guest_run, guest_frame)
                        if row["event"] == "native_wandering_teleport_effect_played"
                        and row["time_unix"] >= started and abs(row["volume"] - .6) < .001]
                departure = [cue for cue in cues if math.dist(cue["location"], guest_start) < 150]
                arrival = [cue for cue in cues if math.dist(cue["location"], destination) < 150]
                if departure and arrival:
                    break
                await asyncio.sleep(.15)
        assert len(departure) == len(arrival) == 1, cues
        assert departure[0]["serial"] < arrival[0]["serial"], cues
    finally:
        assert await operate(host, "restore") == "restored"
    assert campaign_save_hashes(host.campaign) == checkpoint, "A stock anomaly interaction caused an early checkpoint"
    return {"stock_lingering_teleport": {"source": source, "spawn": spawned, "host_motion_cm": host_move, "guest_motion_cm": guest_move,
                                       "guest_overlap_blocked": True, "guest_overlap_location": overlap_location,
                                       "guest_destination": destination,
                                       "arrival_and_departure": [departure[0], arrival[0]], "checkpoint_unchanged": True}}
