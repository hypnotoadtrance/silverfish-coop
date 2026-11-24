"""One unmodified warehouse worker pursues a firing, moving guest."""

import asyncio
import math
import time

from tools.silverfish.control import submit
from portable_guest_world_start import resident_events


def coordinates(value):
    return tuple(map(float, value.split(",")))


def aim_error(current, desired):
    return max(abs((axis - needed + 180) % 360 - 180) for axis, needed in zip(current, desired, strict=True))


def view_frame(fields):
    seat, target, desired, control, camera, muzzle, blocked = fields.split(";")
    return {"seat": coordinates(seat), "target": coordinates(target), "desired": coordinates(desired),
            "controller": coordinates(control), "camera": coordinates(camera), "muzzle": coordinates(muzzle),
            "blocked": blocked == "true"}


def combat_frame(fields):
    alive, credited, initial, health, target, guest_hits, host_hits, before, after, observer, separation = fields.split(";")
    return {"alive": alive == "true", "credited": credited == "true", "initial_health": float(initial),
            "health": float(health), "target": target, "guest_hits": int(guest_hits), "host_hits": int(host_hits),
            "guest_health_at_start": float(before), "guest_health": float(after),
            "host_observer": coordinates(observer), "worker_observer_distance_cm": float(separation)}


async def verify_dock_pursuit(host, guest, frame, command):
    guest_run = guest.client / "run"
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

    async def trigger(remaining):
        started = time.time()
        command_result = await submit(guest_run, "native-guest-primary-input-fixture")
        assert command_result["ok"], (command_result, history)
        sequence = command_result["native_guest_primary_fixture"]["sequence"]

        async def receipt():
            return next((row for row in await resident_events(guest_run, frame)
                         if row.get("sequence") == sequence and row["time_unix"] >= started
                         and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")), None)

        result = await wait_for(receipt, "No authoritative round receipt during pursuit", 8)
        assert result["event"] == "native_guest_primary_input", (result, history)
        shot = result["native_guest_weapon"]
        assert (shot["before"], shot["after"], shot["projectiles"]) == (remaining, remaining - 1, 1), (shot, history)
        return shot

    async def firing_view():
        nonlocal last_view
        local = await command(guest, "dock-pursuit-aim")
        if local == "absent":
            return None
        local_frame = view_frame(local)
        last_view = local_frame
        if local_frame["blocked"] or aim_error(local_frame["camera"], local_frame["desired"]) > 8:
            return None
        authority = await command(host, "dock-pursuit-view")
        return {"guest": local_frame, "host": view_frame(authority)} if authority != "absent" else None

    async def retreat(aim, side):
        x, y, _ = (seat - target for seat, target in zip(aim["seat"], aim["target"], strict=True))
        distance = math.hypot(x, y)
        assert distance > 1, ("Guest and worker occupy the same point", aim)
        # Include lateral travel so the next shot requires a different aim while the worker gives chase.
        x, y = (x - side * .5 * y) / distance, (y + side * .5 * x) / distance
        scale = math.hypot(x, y)
        result = await submit(guest_run, "walk", x=x / scale, y=y / scale, milliseconds=650, continuous=True)
        assert result["ok"] and result["movement_scheduled"], (result, history)

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
        initial_view = await wait_for(firing_view, "The guest did not get a stock firing view", 5)
        history.append(initial_view)
        initial_view["round"] = await trigger(7)

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
            previous = history[-1]
            await retreat(previous["guest"], 1 if remaining % 2 == 0 else -1)
            view = await wait_for(firing_view, "No unblocked guest firing view while retreating on foot", 5)
            history.append(view)
            moved = moved or all(math.dist(history[0][seat]["seat"], view[seat]["seat"]) > 45 for seat in ("host", "guest"))
            before = current["health"]
            view["round"] = await trigger(remaining)
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
