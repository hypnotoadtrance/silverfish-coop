"""Check oil lamp state and stock ignition on one muted portable pair."""

import argparse
import asyncio
from contextlib import ExitStack
from math import dist
from pathlib import Path
import time

import live_portable_title_smoke as suite
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_stock_switch
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def lamp_id(area, number):
    return f"Prop_Lantern_01_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.Prop_Lantern_01_C_{number}"


async def verify_oil_lamps(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    evidence = {}

    async def probe(seat, action, actor):
        request = f"{action}\t{actor.rsplit(':', 1)[1].split('.')[-1]}"
        return await suite.portable_probe_command(seat.bridge, "portable-oil-lamps", request)

    async def verify_state(actor, lit):
        def state_matches(frame):
            return frame.get("game", {}).get(native_stock_switch.KEY, {}).get(actor, {}).get("powered") is lit

        _, replica = await asyncio.gather(
            suite.wait_resident_status(host_run, state_matches, timeout=5),
            suite.wait_resident_status(guest_run, state_matches, timeout=5),
        )
        readings = await asyncio.gather(*(probe(seat, "state", actor) for seat in (host, guest)))
        expected = str(lit).lower()
        assert all(reading.split(";")[:2] == [expected, expected] for reading in readings), readings
        assert readings[1].split(";")[3] == "false", readings
        return replica, readings

    async def verify_one(actor):
        checkpoint = suite.campaign_save_hashes(host.campaign)
        await verify_state(actor, False)
        assert await probe(host, "seed-lighter", actor) == "selected"
        async with asyncio.timeout(8):
            while await probe(guest, "select-lighter", actor) == "waiting":
                await asyncio.sleep(.08)
        await probe(host, "stage-host", actor)
        assert (await probe(host, "use-lighter", actor)).split(";")[0] == "true"
        try:
            _, host_lit = await verify_state(actor, True)
        finally:
            assert await probe(host, "lower-lighter", actor) == "true;false"
        assert await probe(guest, "visual-drift", actor) == "visual drift"
        async with asyncio.timeout(5):
            while (repaired := await probe(guest, "state", actor)).split(";")[:3] != ["true"] * 3:
                await asyncio.sleep(.05)
        place = tuple(map(float, (await probe(host, "stage", actor)).split(",")))
        await suite.wait_resident_status(guest_run, lambda frame:
            (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
            and dist(point, place) < 60, timeout=5)
        started = time.time()
        await probe(guest, "guest-off", actor)
        _, guest_extinguished = await verify_state(actor, False)
        assert (await probe(guest, "use-lighter", actor)).split(";")[0] == "true"
        try:
            replica, guest_lit = await verify_state(actor, True)
        finally:
            assert await probe(guest, "lower-lighter", actor) == "true;false"
        # A compact ignition receipt and a later full status can disagree for
        # the same lamp. Check the native state after another publication.
        await asyncio.sleep(.3)
        replica, guest_lit_after_refresh = await verify_state(actor, True)
        receipts = [row for row in await suite.resident_events(guest_run, replica)
                    if row.get("time_unix", 0) >= started
                    and row.get("native_stock_switch_interaction", {}).get("actor") == actor]
        power = [row["native_stock_switch_interaction"]["powered"] for row in receipts]
        # A held lighter can emit another ignition callback before the reply.
        assert len(power) >= 2 and power[0] is False and all(value is True for value in power[1:]), receipts
        assert await probe(host, "host-off", actor) == "false"
        _, host_extinguished = await verify_state(actor, False)
        assert suite.campaign_save_hashes(host.campaign) == checkpoint
        evidence[actor] = dict(host_lit=host_lit, guest_lit_after_visual_repair=repaired, guest_extinguished=guest_extinguished,
                               guest_lit=guest_lit, guest_lit_after_refresh=guest_lit_after_refresh,
                               host_extinguished=host_extinguished, receipts=receipts)

    await verify_one(lamp_id(OBSERVATORY, 1))
    await enter_crossroads(host, pawn)
    await suite.wait_resident_status(host_run, lambda frame:
        frame.get("game", {}).get("map") == CROSSROADS and not frame.get("awaiting_zone_guest")
        and frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
    await suite.wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS)
    adjacent = lamp_id(CROSSROADS, 3)
    await verify_state(adjacent, False)
    await verify_one(lamp_id(CROSSROADS, 1))
    await verify_state(adjacent, False)
    return {"lamps": evidence, "other_lamp_stayed_unlit": adjacent}


async def exercise(work, first, second, saved):
    with ExitStack() as fixtures:
        for game in (first, second):
            fixtures.enter_context(suite.installed_probe(game, mod="SilverfishPortableOilLampProbe",
                                                         fixture="portable_oil_lamp_probe.lua"))
        await suite.verify(work, first, second, saved, stock_warehouse_switch=True,
                           oil_lamps_check=verify_oil_lamps)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve()))
