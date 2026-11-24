"""Verify one stock observer BlendSpace survives the Dock guest's grounded gait."""

import asyncio
import math

from tools.silverfish.control import submit


def kinematics(fields):
    x, y, z, speed, sprint, crouched, aim, grounded = fields
    return dict(position=tuple(map(float, (x, y, z))), speed=float(speed), sprint=sprint == "1",
                crouched=crouched == "1", aim=aim == "1", grounded=grounded == "1")


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
        x, y, lane_length = map(float, (await command(guest, "prepare")).split(";"))
        seeded = True
        duration = 850 if lane_length >= 1350 else 680
        await stage_motion("walk", duration)
        walk = await wait_pair("read", lambda view, owner:
            view["speed"] > 30 and owner["speed"] > 30 and not view["sprint"] and not owner["sprint"]
            and view["inputs"] == [1] and displaced(view, owner, baseline), baseline)
        await stage_motion("sprint", duration)
        run = await wait_pair("read", lambda view, owner:
            view["speed"] > 30 and owner["speed"] > 30 and view["sprint"] and owner["sprint"]
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
                                    "lane_cm": lane_length, "movement_window_ms": duration}}
