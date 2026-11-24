"""One unmodified warehouse worker pursues a firing, moving guest."""

import asyncio
import math
import time

from tools.silverfish.control import submit
from portable_guest_world_start import resident_events


MINIMUM_CLEARANCE_CM = 175
NATIVE_TAP_LIFETIME = 1.5  # native/guest_primary_input.cpp receipt_lifetime
RETREAT_MILLISECONDS = 650


def coordinates(value):
    return tuple(map(float, value.split(",")))


def aim_error(current, desired):
    return max(abs((axis - needed + 180) % 360 - 180) for axis, needed in zip(current, desired, strict=True))


def view_frame(fields):
    seat, target, desired, control, camera, muzzle, blocked = fields.split(";")
    seat, target = coordinates(seat), coordinates(target)
    return {"seat": seat, "target": target, "clearance_cm": math.dist(seat[:2], target[:2]), "desired": coordinates(desired),
            "controller": coordinates(control), "camera": coordinates(camera), "muzzle": coordinates(muzzle),
            "blocked": blocked == "true"}


def combat_frame(fields):
    alive, credited, initial, health, target, guest_hits, host_hits, before, after, observer, separation = fields.split(";")
    return {"alive": alive == "true", "credited": credited == "true", "initial_health": float(initial),
            "health": float(health), "target": target, "guest_hits": int(guest_hits), "host_hits": int(host_hits),
            "guest_health_at_start": float(before), "guest_health": float(after),
            "host_observer": coordinates(observer), "worker_observer_distance_cm": float(separation)}


def tap_publication(path, captured, kind):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text and not text.endswith("\n"):
        return None
    prefix = ["native-guest-primary-intent", str(captured["sequence"])]
    for line in text.splitlines():
        fields = line.split("\t")
        if fields[:2] == prefix:
            assert fields[2:] == [captured["map"], captured["pawn"], captured["guid"], kind], fields
            return True
    return False


async def verify_dock_pursuit(host, guest, frame, command):
    guest_run = guest.client / "run"
    tap_path = guest.bridge.directory / "native-guest-primary-events"
    weapon_kind = frame["game"]["native_guest_primary_scope"]["kind"]
    origin, guest_spawn = (await command(host, "dock-pursuit-stage")).split(";")
    history = []
    last_view = None

    async def observe():
        return combat_frame(await command(host, "dock-pursuit-sample"))

    async def wait_for(check, description, seconds):
        reading = None
        try:
            async with asyncio.timeout(seconds):
                while True:
                    reading = await check()
                    if reading:
                        return reading
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError((description, reading, last_view, history)) from error

    async def trigger(remaining, attempt):
        started = time.time()
        command_result = await submit(guest_run, "native-guest-primary-input-fixture")
        if not command_result["ok"]:
            attempt.update(outcome="refused_before_capture", reason=command_result["error"])
            assert ("temporarily blocked by the equipped player's state" in attempt["reason"]
                    or "(native_guest_primary_fixture_scope_unavailable)" in attempt["reason"]), (command_result, history)
            return None
        captured = command_result["native_guest_primary_fixture"]
        sequence = captured["sequence"]
        attempt.update(sequence=sequence, outcome="captured")
        published = tap_publication(tap_path, captured, weapon_kind)
        deadline = time.monotonic() + NATIVE_TAP_LIFETIME
        while published is not True and time.monotonic() < deadline:
            await asyncio.sleep(.04)
            published = tap_publication(tap_path, captured, weapon_kind)
        if published is not True:
            # The game-thread read fences an emit already in progress when the tap expired.
            await command(guest, "dock-pursuit-view")
            published = tap_publication(tap_path, captured, weapon_kind)
            assert published is not None, ("Could not read the guest's complete outbound tap log", history)
            if not published:
                attempt["outcome"] = "expired_before_publication"
                return None
        attempt["outcome"] = "published"

        async def receipt():
            return next((row for row in await resident_events(guest_run, frame)
                         if row.get("sequence") == sequence and row["time_unix"] >= started
                         and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")), None)

        result = await wait_for(receipt, "Published guest shot did not receive an authoritative receipt", 8)
        if result["event"] != "native_guest_primary_input":
            attempt.update(outcome="host_rejected", reason=result.get("reason"))
        assert result["event"] == "native_guest_primary_input", (result, history)
        shot = result["native_guest_weapon"]
        assert (shot["before"], shot["after"], shot["projectiles"]) == (remaining, remaining - 1, 1), (shot, history)
        attempt["outcome"] = "fired"
        return shot

    async def retreat(aim, side):
        x, y, _ = (seat - target for seat, target in zip(aim["seat"], aim["target"], strict=True))
        distance = math.hypot(x, y)
        assert distance > 1, ("Guest and worker occupy the same point", aim)
        # Include lateral travel so the next shot requires a different aim while the worker gives chase.
        x, y = (x - side * .5 * y) / distance, (y + side * .5 * x) / distance
        scale = math.hypot(x, y)
        result = await submit(guest_run, "walk", x=x / scale, y=y / scale, milliseconds=RETREAT_MILLISECONDS, continuous=True)
        assert result["ok"] and result["movement_scheduled"], (result, history)

    async def firing_window(side):
        next_retreat = 0

        async def find_window():
            nonlocal next_retreat, last_view
            local = await command(guest, "dock-pursuit-aim")
            if local == "absent":
                return None
            last_view = local_frame = view_frame(local)
            if side and time.monotonic() >= next_retreat:
                await retreat(local_frame, side)
                next_retreat = time.monotonic() + .75 * RETREAT_MILLISECONDS / 1000
                return None
            if (local_frame["clearance_cm"] < MINIMUM_CLEARANCE_CM or local_frame["blocked"]
                    or aim_error(local_frame["camera"], local_frame["desired"]) > 8):
                return None
            authority = await command(host, "dock-pursuit-view")
            return {"guest": local_frame, "host": view_frame(authority)} if authority != "absent" else None

        return await wait_for(find_window, "No clear and unblocked firing lane during the warehouse pursuit", 7)

    async def fire_round(remaining, side):
        record = {"attempts": []}
        history.append(record)
        for attempt_index in range(2):
            view = await firing_window(side or attempt_index)
            record.update(view)
            attempt = {"clearance_cm": round(view["guest"]["clearance_cm"], 1)}
            record["attempts"].append(attempt)
            shot = await trigger(remaining, attempt)
            if shot:
                record["round"] = shot
                return record
        raise AssertionError(("Two stock trigger attempts ended before the guest published a shot", history))

    try:
        baseline = await observe()
        assert baseline["alive"] and baseline["health"] == baseline["initial_health"] == 120, baseline
        assert baseline["target"] == "-" and not baseline["credited"], baseline

        # Leave the host at the stock Dock arrival. Stage the guest once and then use guest input only.
        assert await command(guest, f"dock-natural-arrive\t{guest_spawn}") == "ready"

        async def arrived():
            worker = await command(guest, "dock-natural-position")
            seat = await command(guest, "dock-natural-seat")
            return (worker != "absent" and math.dist(coordinates(worker), coordinates(origin)) < 350
                    and math.dist(coordinates(seat), coordinates(guest_spawn)) < 100)

        await wait_for(arrived, "The guest did not reach the initial warehouse firing lane", 6)
        initial_view = await fire_round(7, 0)

        async def under_attack():
            result = await observe()
            initial_view["combat"] = result
            if (result["alive"] and result["credited"] and result["target"] == "guest" and result["guest_hits"] > 0
                    and result["health"] < baseline["health"] and result["guest_health"] < baseline["guest_health"]):
                return result

        retaliation = await wait_for(under_attack, "The original worker did not retaliate against the local shooter", 15)
        assert retaliation["host_hits"] == 0, history
        current = retaliation
        moved = False
        pursued_while_moving = False
        for remaining in range(6, 0, -1):
            view = await fire_round(remaining, 1 if remaining % 2 == 0 else -1)
            moved = moved or all(math.dist(history[0][seat]["seat"], view[seat]["seat"]) > 45 for seat in ("host", "guest"))
            before = current["health"]
            deadline = time.monotonic() + 1.5
            while True:
                current = await observe()
                if current["health"] < before or not current["alive"] or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(.05)
            view["combat"] = current
            assert current["host_hits"] == 0 and current["guest_health"] > 0, (baseline, history)
            pursued_while_moving = pursued_while_moving or (moved and current["alive"] and current["target"] == "guest")
            if not current["alive"]:
                break

        assert moved, ("The host never observed the guest walk during pursuit", history)
        assert pursued_while_moving, ("The stock worker did not stay on the moving guest during the fight", history)
        assert len(history) >= 3 and not current["alive"] and current["credited"], (
            "The guest did not kill the original worker after sustained fire under pressure", history)

        async def shared_death():
            states = await asyncio.gather(*(command(seat, "dock-natural-state") for seat in (host, guest)))
            return states if all(state.startswith("false;true;1;") for state in states) else None

        death = await wait_for(shared_death, "The exact worker death/ragdoll did not reach both seats", 10)
        return {"baseline": baseline, "retaliation": retaliation, "rounds": history, "both_peers_dead_and_ragdolled": death}
    finally:
        assert await command(host, "dock-pursuit-stop") == "stopped"
