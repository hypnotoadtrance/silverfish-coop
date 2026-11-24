"""Verify one stock observer BlendSpace survives the Dock guest's grounded gait."""

import asyncio
import math

from tools.silverfish.control import submit


def kinematics(fields):
    x, y, z, speed, sprint, crouched, aim, grounded, speed_cap, stock_walk, stock_sprint, modifier, food = fields
    return dict(position=tuple(map(float, (x, y, z))), speed=float(speed), sprint=sprint == "1",
                crouched=crouched == "1", aim=aim == "1", grounded=grounded == "1", speed_cap=float(speed_cap),
                stock_walk=float(stock_walk), stock_sprint=float(stock_sprint), modifier=float(modifier),
                food=float(food))


def observed(response):
    pawn, weapon, hand, body, instance, asset, phase, hand_height, inputs, *motion = response.split(";")
    return dict(**kinematics(motion), identity=tuple(map(int, (pawn, weapon, hand, body, instance, asset))),
                phase=float(phase), hand_height=float(hand_height),
                inputs=[] if inputs == "-" else list(map(float, inputs.split(","))))


async def verify_stock_gait(host, guest, probe):
    async def command(seat, action):
        return (await probe(seat.bridge, "portable-gait", action)).strip()

    def grounded(report):
        return report["grounded"] and not report["aim"] and not report["crouched"]

    def expected_speed(seat, sprinting):
        speed = seat["stock_walk"]
        if sprinting:
            # Stock DetermineMoveSpeed for the default character without Nudist.
            minimum = speed + 100
            food_fraction = min(1.0, max(0.0, (seat["food"] - 1) / 74))
            speed = minimum + food_fraction * (seat["stock_sprint"] - minimum)
        return speed * seat["modifier"]

    def settled_pace(view, owner, sprinting):
        return (all(seat["sprint"] == sprinting
                    and math.isclose(seat["speed_cap"], expected_speed(seat, sprinting), abs_tol=2)
                    and math.isclose(seat["speed"], seat["speed_cap"], rel_tol=.06)
                    for seat in (view, owner))
                and abs(view["speed"] - owner["speed"]) < 45)

    async def wait_pair(action, accept, previous=None):
        last = None
        try:
            async with asyncio.timeout(6):
                while True:
                    report, local = await asyncio.gather(command(host, action), command(guest, "owner"))
                    if report != "pending" and local != "pending":
                        view = observed(report)
                        *motion, weapon, part, asset = local.split(";")
                        owner = dict(**kinematics(motion), weapon=tuple(map(int, (weapon, part, asset))))
                        last = view, owner
                        assert all(view["identity"]) and all(owner["weapon"]) and view["hand_height"] > 0, last
                        if previous:
                            assert view["identity"] == previous["observer"]["identity"], (
                                "The equipped hand or animation node changed", previous, view)
                            assert owner["weapon"] == previous["owner"]["weapon"], ("The owning frame changed", previous, owner)
                        if grounded(view) and grounded(owner) and accept(view, owner):
                            return {"observer": view, "owner": owner}
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(("No live grounded gait witness", action, last)) from error

    async def stage_motion(action, duration):
        assert grounded(kinematics((await command(guest, action)).split(";")))
        move = await submit(guest.client / "run", "walk", x=x, y=y, milliseconds=duration, continuous=True)
        assert move["ok"] and move["movement_scheduled"], move

    seeded = False
    def displaced(view, owner, previous):
        return all(math.dist(current["position"], old["position"]) > 12 for current, old in
                   ((view, previous["observer"]), (owner, previous["owner"])))

    try:
        baseline = await wait_pair("arm", lambda view, owner:
            view["speed"] < 20 and owner["speed"] < 20 and not view["sprint"] and not owner["sprint"])
        host_baseline = kinematics((await command(host, "hostspeed")).split(";"))
        for sample in (host_baseline, baseline["owner"], baseline["observer"]):
            assert math.isclose(sample["modifier"], 1.0, abs_tol=.001), sample
            assert (sample["stock_walk"], sample["stock_sprint"]) == (650, 1100), sample
        x, y, lane_length = map(float, (await command(guest, "prepare")).split(";"))
        seeded = True
        duration = 850 if lane_length >= 1350 else 680
        await stage_motion("walk", duration)
        walk = await wait_pair("read", lambda view, owner:
            settled_pace(view, owner, sprinting=False)
            and view["inputs"] == [1] and displaced(view, owner, baseline), baseline)
        await stage_motion("sprint", duration)
        run = await wait_pair("read", lambda view, owner:
            settled_pace(view, owner, sprinting=True)
            and view["inputs"] == [1, 2] and displaced(view, owner, walk), baseline)
        # The resident movement action expires. Observe ordinary braking to idle before restoring fixture input.
        released = await wait_pair("read", lambda view, owner:
            view["speed"] < 20 and owner["speed"] < 20 and view["inputs"] == [1, 2, 0], baseline)
        assert abs(walk["observer"]["phase"] - run["observer"]["phase"]) > .001, (walk, run)
    finally:
        if seeded:
            assert await command(guest, "finish") == "restored"
        assert await command(host, "disarm") == "disarmed"

    return {"stock_grounded_gait": {"baseline": baseline, "walk": walk, "run": run, "idle": released,
                                    "host_stock_pace": host_baseline,
                                    "lane_cm": lane_length, "movement_window_ms": duration}}
