"""Exercise stock hinged doors and their guest previews in portable co-op."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_stock_door as door
from tools.silverfish.authored_loot import full_name
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


DOCK = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
DOCK_GATE = f"Door_Bars_01_BP_C {DOCK}.Tut_Dock_P:PersistentLevel.Door_Bars_01_BP_C_1"
WOOD_DOOR = f"Door_Wood_04_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Door_Wood_04_BP_C_1719"


async def toggle_door(host, guest, actor, pawn, expected_open, *, inspect_during_motion=None):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def observed(frame):
        return frame.get("game", {}).get(door.KEY, {}).get(actor, {})

    attempts = []
    current = observed(await wait_resident_status(host_run, lambda frame:
        observed(frame).get("open") is not expected_open and bool(observed(frame))))
    x, y, z = current["knob_location"]
    for dx, dy in ((110, 0), (0, 110), (-110, 0), (0, -110)):
        moved = await host.bridge.restore("guest", {"location": [x + dx, y + dy, z]})
        assert moved["pawns"]["guest"]["name"] == pawn
        point = moved["pawns"]["guest"]["location"]
        guest_view = await wait_resident_status(guest_run, lambda frame:
            math.dist(frame["game"].get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), point) < 55)
        started = time.time()
        try:
            await guest.bridge.command(door.INPUT_FIXTURE, actor)
        except RuntimeError as error:
            if "stock_fixture_focus_unavailable" not in str(error):
                raise
            attempts.append(str(error))
        else:
            break
    else:
        raise RuntimeError(f"No stock approach reached the stock hinge knob: {attempts}")

    inspected = None
    if inspect_during_motion:
        async with asyncio.timeout(3):
            while True:
                events = await resident_events(guest_run, guest_view)
                if any(event.get("time_unix", 0) >= started and event.get("event") == "native_stock_door_motion_played"
                       and event.get("actor") == actor and event.get("opens") is expected_open for event in events):
                    break
                await asyncio.sleep(.015)
        probe_start = time.monotonic()
        inspection = await submit(guest_run, "native-loot-inspect", target=inspect_during_motion)
        assert inspection["ok"], inspection
        inspected = {"actor": inspect_during_motion, "completed_at": time.time(),
                     "roundtrip_ms": round((time.monotonic() - probe_start) * 1000)}

    authority = observed(await wait_resident_status(host_run, lambda frame:
        observed(frame).get("open") is expected_open and observed(frame).get("locked") is False))
    replica = await wait_resident_status(guest_run, lambda frame: door.matches(observed(frame), authority))
    actions = [event for event in await resident_events(guest_run, replica)
               if event.get("time_unix", 0) >= started and event.get("event") == "local_interaction"
               and event.get(door.RESULT, {}).get("actor") == actor]
    receipt, = actions
    assert receipt[door.RESULT]["open"] is expected_open
    if inspected:
        assert inspected["completed_at"] < receipt["time_unix"], (inspected, receipt)
        receipt["independent_inspection"] = inspected
    motion, = [event for event in await resident_events(guest_run, replica)
               if event.get("time_unix", 0) >= started and event.get("event") == "native_stock_door_motion_played"
               and event.get("actor") == actor and event.get("opens") is expected_open]
    assert motion["time_unix"] < receipt["time_unix"], (motion, receipt)
    measured = await guest.bridge.command(door.FIXTURE, "motion", actor, update_status=False)
    first, preview, collision_kept = map(float, measured["ack"]["message"].split("|"))
    assert 0 <= preview < first and collision_kept == 1, measured
    return receipt, first, preview


async def verify_stock_crossroads_door(host, guest, pawn, *, lockpick=None):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def observed(frame):
        return frame.get("game", {}).get(door.KEY, {}).get(WOOD_DOOR, {})

    ready = await wait_resident_status(host_run, lambda frame:
        frame["game"].get("native_zone", {}).get("state") == "ready")
    if ready["game"]["map"] == OBSERVATORY:
        await enter_crossroads(host, pawn)
    arrival = await wait_resident_status(host_run, lambda frame:
        frame["game"].get("map") == CROSSROADS and not frame.get("awaiting_zone_guest")
        and frame["game"].get("native_guest_save", {}).get("state") == "bound"
        and observed(frame).get("locked") is True, timeout=60)
    epoch = arrival["zone_epoch"]
    pawn = arrival["game"]["native_guest_save"]["pawn"]
    initial = observed(arrival)
    await wait_resident_status(guest_run, lambda frame:
        frame.get("zone_epoch") == epoch and door.matches(observed(frame), initial))

    hx, hy, _ = initial["panel_location"]
    kx, ky, kz = initial["knob_location"]
    length = math.hypot(kx - hx, ky - hy)
    assert 40 < length < 200, initial
    x, y = (hx + kx) / 2, (hy + ky) / 2
    dx, dy = 70 * (ky - hy) / length, -70 * (kx - hx) / length
    points = (x + dx, y + dy, kz, x - dx, y - dy, kz)

    async def panel(seat, query):
        report = await seat.bridge.command(door.TRACE_FIXTURE, WOOD_DOOR, query, *map(str, points))
        row = report[door.TRACE_RESULT]
        assert row["actor"] == WOOD_DOOR and row["query"] == int(query)
        return row["panel"]

    query = "0" if await panel(host, "0") else "2"
    assert await panel(host, query) and await panel(guest, query)
    checkpoint = campaign_save_hashes(host.campaign)
    refused = await submit(guest_run, door.ACTION, target=WOOD_DOOR)
    assert refused.get("error") == "stock_door_locked", refused
    if lockpick:
        evidence = await lockpick(host, guest, pawn, hinged=WOOD_DOOR)
        await toggle_door(host, guest, WOOD_DOOR, pawn, True)
    else:
        evidence = {}
        await host.bridge.command(door.FIXTURE, "pry", WOOD_DOOR)
    opened = await wait_resident_status(host_run, lambda frame:
        observed(frame).get("open") is True and observed(frame).get("locked") is False)
    await wait_resident_status(guest_run, lambda frame: door.matches(observed(frame), observed(opened)))

    crate = full_name(CROSSROADS, "LootCrate_Small_BP_C_9")
    closed, close_motion, close_preview = await toggle_door(host, guest, WOOD_DOOR, pawn, False, inspect_during_motion=crate)
    assert await panel(host, query) and await panel(guest, query)
    reopened, reopen_motion, reopen_preview = await toggle_door(host, guest, WOOD_DOOR, pawn, True)
    assert not await panel(host, query) and not await panel(guest, query)
    replay = await submit(guest_run, door.ACTION, operation_id=closed["operation_id"], target=WOOD_DOOR)
    assert replay.get("ok") and replay[door.RESULT] == closed[door.RESULT]
    assert not await panel(host, query) and not await panel(guest, query)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {**evidence, "stock_crossroads_door": {"actor": WOOD_DOOR, "authoritative_lock_refusal": True,
                                     "close": closed[door.RESULT], "open": reopened[door.RESULT],
                                     "guest_confirm_ms": [closed["elapsed_ms"], reopened["elapsed_ms"]],
                                     "guest_movement_start_ms": [close_motion, reopen_motion],
                                     "guest_preview_start_ms": [close_preview, reopen_preview],
                                     "independent_inspection": closed["independent_inspection"],
                                     "close_replay_preserved_open_door": True,
                                     "collision_verified_both_seats": True, "campaign_unchanged": True}}


async def verify_stock_dock_gate(host, guest):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def observed(frame):
        return frame.get("game", {}).get(door.KEY, {}).get(DOCK_GATE, {})

    arrival = await wait_resident_status(host_run, lambda frame:
        frame["game"].get("map") == DOCK and not frame.get("awaiting_zone_guest")
        and frame["game"].get("native_guest_save", {}).get("state") == "bound"
        and observed(frame).get("locked") is False, timeout=30)
    pawn = arrival["game"]["native_guest_save"]["pawn"]
    initial = observed(arrival)
    await wait_resident_status(guest_run, lambda frame:
        frame.get("zone_epoch") == arrival["zone_epoch"] and door.matches(observed(frame), initial))
    checkpoint = campaign_save_hashes(host.campaign)
    poses = []
    for expected_open in (not initial["open"], initial["open"]):
        receipt, motion, preview = await toggle_door(host, guest, DOCK_GATE, pawn, expected_open)
        poses.append({"open": expected_open, "motion_ms": motion, "preview_ms": preview,
                      "confirmation_ms": receipt["elapsed_ms"]})
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_dock_gate": {"actor": DOCK_GATE, "poses": poses, "campaign_unchanged": True}}
