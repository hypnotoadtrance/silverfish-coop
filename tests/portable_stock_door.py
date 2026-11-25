"""Exercise a stock hinged door in one portable Crossroads campaign."""

import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_stock_door as door
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


WOOD_DOOR = f"Door_Wood_04_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Door_Wood_04_BP_C_1719"


async def verify_stock_crossroads_door(host, guest, pawn):
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
    await host.bridge.command(door.FIXTURE, "pry", WOOD_DOOR)
    opened = await wait_resident_status(host_run, lambda frame:
        observed(frame).get("open") is True and observed(frame).get("locked") is False)
    await wait_resident_status(guest_run, lambda frame: door.matches(observed(frame), observed(opened)))

    async def toggle(expected_open):
        attempts = []
        current = observed(await wait_resident_status(host_run, lambda frame:
            observed(frame).get("open") is not expected_open and bool(observed(frame))))
        x, y, z = current["knob_location"]
        for dx, dy in ((110, 0), (0, 110), (-110, 0), (0, -110)):
            moved = await host.bridge.restore("guest", {"location": [x + dx, y + dy, z]})
            assert moved["pawns"]["guest"]["name"] == arrival["game"]["native_guest_save"]["pawn"]
            point = moved["pawns"]["guest"]["location"]
            await wait_resident_status(guest_run, lambda frame:
                math.dist(frame["game"].get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), point) < 55)
            started = time.time()
            try:
                await guest.bridge.command(door.INPUT_FIXTURE, WOOD_DOOR)
            except RuntimeError as error:
                if "stock_fixture_focus_unavailable" not in str(error):
                    raise
                attempts.append(str(error))
            else:
                break
        else:
            raise RuntimeError(f"No stock approach reached the Crossroads hinge knob: {attempts}")

        authority = observed(await wait_resident_status(host_run, lambda frame:
            observed(frame).get("open") is expected_open and observed(frame).get("locked") is False))
        replica = await wait_resident_status(guest_run, lambda frame: door.matches(observed(frame), authority))
        actions = [event for event in await resident_events(guest_run, replica)
                   if event.get("time_unix", 0) >= started and event.get("event") == "local_interaction"
                   and event.get(door.RESULT, {}).get("actor") == WOOD_DOOR]
        receipt, = actions
        assert receipt[door.RESULT]["open"] is expected_open
        motion, = [event for event in await resident_events(guest_run, replica)
                   if event.get("time_unix", 0) >= started and event.get("event") == "native_stock_door_motion_played"
                   and event.get("actor") == WOOD_DOOR and event.get("opens") is expected_open]
        assert motion["time_unix"] < receipt["time_unix"], (motion, receipt)
        measured = await guest.bridge.command(door.FIXTURE, "motion", WOOD_DOOR, update_status=False)
        first, preview, collision_kept = map(float, measured["ack"]["message"].split("|"))
        assert 0 <= preview < first and collision_kept == 1, measured
        assert await panel(host, query) is not expected_open
        assert await panel(guest, query) is not expected_open
        return receipt, first, preview

    closed, close_motion, close_preview = await toggle(False)
    reopened, reopen_motion, reopen_preview = await toggle(True)
    replay = await submit(guest_run, door.ACTION, operation_id=closed["operation_id"], target=WOOD_DOOR)
    assert replay.get("ok") and replay[door.RESULT] == closed[door.RESULT]
    assert not await panel(host, query) and not await panel(guest, query)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_crossroads_door": {"actor": WOOD_DOOR, "authoritative_lock_refusal": True,
                                     "close": closed[door.RESULT], "open": reopened[door.RESULT],
                                     "guest_confirm_ms": [closed["elapsed_ms"], reopened["elapsed_ms"]],
                                     "guest_movement_start_ms": [close_motion, reopen_motion],
                                     "guest_preview_start_ms": [close_preview, reopen_preview],
                                     "close_replay_preserved_open_door": True,
                                     "collision_verified_both_seats": True, "campaign_unchanged": True}}
