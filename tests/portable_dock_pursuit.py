"""One unmodified warehouse worker pursues a firing, moving guest."""

import asyncio
from contextlib import suppress
import math
import time

from tools.silverfish.control import current_status, submit
from tools.silverfish.native_guest_weapon import valid_shot_direction
from portable_guest_world_start import resident_events


MINIMUM_CLEARANCE_CM = 175
NATIVE_TAP_LIFETIME = 1.5  # native/guest_primary_input.cpp receipt_lifetime
RETREAT_MILLISECONDS = 650


def coordinates(value):
    return tuple(map(float, value.split(",")))


def aim_error(current, desired):
    return max(abs((axis - needed + 180) % 360 - 180) for axis, needed in zip(current, desired, strict=True))


def view_frame(fields):
    seat, target, desired, control, camera, muzzle, rounds, blockers = fields.split(";")
    seat, target = coordinates(seat), coordinates(target)
    return {"seat": seat, "target": target, "clearance_cm": math.dist(seat[:2], target[:2]), "desired": coordinates(desired),
            "controller": coordinates(control), "camera": coordinates(camera), "muzzle": coordinates(muzzle),
            "rounds": int(rounds), "blockers": blockers.split(",") if blockers else []}


def combat_frame(fields):
    alive, credited, initial, health, target, guest_hits, host_hits, before, after, observer, separation = fields.split(";")
    return {"alive": alive == "true", "credited": credited == "true", "initial_health": float(initial),
            "health": float(health), "target": target, "guest_hits": int(guest_hits), "host_hits": int(host_hits),
            "guest_health_at_start": float(before), "guest_health": float(after),
            "host_observer": coordinates(observer), "worker_observer_distance_cm": float(separation)}


def tap_publication(game, captured, kind):
    scope = game.get("native_guest_primary_scope", {})
    identity = ("map", "pawn", "guid")
    if scope.get("kind") != kind or any(scope.get(key) != captured[key] for key in identity):
        return None
    previous = game.get("native_guest_primary_publication", {}).get("tap")
    if not previous or previous["sequence"] < captured["sequence"]:
        return False
    if previous["sequence"] != captured["sequence"]:
        return None
    assert previous["lane"] == "memory" and all(previous[key] == captured[key] for key in identity), previous
    return True


async def verify_dock_pursuit(host, guest, frame, command):
    guest_run = guest.client / "run"
    scope = frame["game"]["native_guest_primary_scope"]
    weapon_kind = scope["kind"]
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
        async def published_in_status():
            status = await current_status(guest_run)
            return tap_publication(status.get("game", {}), captured, weapon_kind)

        published = await published_in_status()
        deadline = time.monotonic() + NATIVE_TAP_LIFETIME
        while published is not True and time.monotonic() < deadline:
            await asyncio.sleep(.04)
            published = await published_in_status()
        if published is not True:
            # Sample on the game thread after the native tap lifetime has elapsed.
            observed = await submit(guest_run, "native-guest-primary-publication-fixture")
            assert observed["ok"], (observed, history)
            published = tap_publication(observed, captured, weapon_kind)
            assert published is not None, ("Could not verify the stock tap's publication state", history)
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
        direction = result.get("native_guest_trigger_direction")
        assert valid_shot_direction(direction), ("The shot did not carry the stock trigger view", result)
        attempt["trigger_direction"] = direction
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
            if (local_frame["clearance_cm"] < MINIMUM_CLEARANCE_CM or local_frame["blockers"]
                    or aim_error(local_frame["camera"], local_frame["desired"]) > 8):
                return None
            return local_frame

        return await wait_for(find_window, "No clear and unblocked firing lane during the warehouse pursuit", 7)

    async def fire_round(remaining, side):
        record = {"attempts": []}
        history.append(record)
        for attempt_index in range(2):
            view = await firing_window(side or attempt_index)
            record["guest"] = view
            if remaining is None:
                remaining = view["rounds"]
                assert remaining > 0, ("The stock pistol must be loaded before the fight", view)
            attempt = {"clearance_cm": round(view["clearance_cm"], 1)}
            record["attempts"].append(attempt)
            # Read the other seat without spending the guest's firing window waiting for its result.
            shot, authority = await asyncio.gather(trigger(remaining, attempt), command(host, "dock-pursuit-view"))
            assert authority != "absent", ("The host lost the warehouse worker before the shot was witnessed", history)
            record["host"] = view_frame(authority)
            if shot:
                record["round"] = shot
                return record
        raise AssertionError(("Two stock trigger attempts ended before the guest published a shot", history))

    async def reload_pistol(rounds):
        assert 0 < rounds < 8, ("The guest needs a partially spent stock magazine", history)
        await firing_window(1)
        before = await observe()
        assert before["alive"] and before["target"] == "guest" and before["host_hits"] == 0, before
        started = time.time()
        result = await submit(guest_run, "native-guest-reload-input-fixture")
        assert result["ok"] and result["native_guest_reload_fixture"]["stock_wrappers"] == 2, result
        sequence = result["native_guest_reload_fixture"]["sequence"]

        async def receipt():
            return next((row for row in await resident_events(guest_run, frame)
                         if row.get("sequence") == sequence and row["time_unix"] >= started
                         and row["event"] in ("native_guest_reload_input", "native_guest_reload_input_rejected")), None)

        reply = await wait_for(receipt, "The stock warehouse reload did not receive a receipt", 8)
        assert reply["event"] == "native_guest_reload_input", reply
        reload = reply["native_guest_weapon"]
        assert (reload["guid"], reload["before"], reload["after"], reload["reserve_before"], reload["reserve_after"]) == (
            scope["guid"], rounds, 8, 8, rounds), reload

        async def both_magazines():
            local, authority = await asyncio.gather(command(guest, "dock-pursuit-aim"), command(host, "dock-pursuit-view"))
            return (local != "absent" and authority != "absent"
                    and view_frame(local)["rounds"] == view_frame(authority)["rounds"] == reload["after"])

        await wait_for(both_magazines, "The guest and host did not agree on the reloaded magazine", 6)
        after = await observe()
        assert after["alive"] and after["target"] == "guest" and after["host_hits"] == 0, after
        return {"receipt": reload, "before": before, "after": after}

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
        initial_view = await fire_round(None, 0)

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
        reload = None
        remaining = initial_view["round"]["after"]
        while current["alive"]:
            if reload is None and len(history) >= 2:
                reload = await reload_pistol(remaining)
                remaining = reload["receipt"]["after"]
            if remaining == 0:
                break
            view = await fire_round(remaining, 1 if remaining % 2 == 0 else -1)
            remaining = view["round"]["after"]
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

        assert moved, ("The host never observed the guest walk during pursuit", history)
        assert pursued_while_moving, ("The stock worker did not stay on the moving guest during the fight", history)
        launches = await command(host, "dock-pursuit-launches")
        assert reload and len(history) >= 3 and not current["alive"] and current["credited"], (
            "The guest did not kill the original worker after sustained fire under pressure", history,
            {"muzzle_target_camera_angles_at_launch": launches})

        async def shared_death():
            states = await asyncio.gather(*(command(seat, "dock-natural-state") for seat in (host, guest)))
            return states if all(state.startswith("false;true;1;") for state in states) else None

        death = await wait_for(shared_death, "The exact worker death/ragdoll did not reach both seats", 10)
        return {"baseline": baseline, "retaliation": retaliation, "rounds": history, "reload": reload,
                "muzzle_target_camera_angles_at_launch": launches, "both_peers_dead_and_ragdolled": death}
    except Exception as error:
        with suppress(RuntimeError, TimeoutError):
            launches = await command(host, "dock-pursuit-launches")
            error.add_note(f"Muzzle, target, and camera angles at stock launch: {launches}")
        raise
    finally:
        try:
            assert await command(guest, "dock-pursuit-guest-stop") == "stopped"
        finally:
            assert await command(host, "dock-pursuit-stop") == "stopped"
