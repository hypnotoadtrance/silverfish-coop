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
            while (repaired := await probe(guest, "state", actor)).split(";")[:2] != ["true", "true"]:
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


async def verify_streamed_switch(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = suite.campaign_save_hashes(host.campaign)

    async def probe(seat, mode, actor=""):
        result = await seat.bridge.command("fixture-streamed-switch", mode, pawn, actor)
        return result["ack"]["message"]

    async def load(seat):
        assert await probe(seat, "load") == "loading"
        async with asyncio.timeout(18):
            while (actor := await probe(seat, "discover")) == "waiting":
                await asyncio.sleep(.12)
        return actor

    def row(frame, name):
        game = frame.get("game", {})
        if game.get("map") == OBSERVATORY:
            return game.get(native_stock_switch.KEY, {}).get(name)

    async def observe(run, name, *, equal=None):
        frame = await suite.wait_resident_status(run, lambda result: (
            (state := row(result, name)) and (equal is None or state == equal)), timeout=12)
        return row(frame, name)

    actor = await load(host)
    assert "/CoopSwitchSublevel." in actor and actor.endswith(":PersistentLevel.CircutBox_BP_C_1"), actor
    try:
        original = await observe(host_run, actor)
    except RuntimeError as error:
        raise AssertionError(await probe(host, "diagnose", actor)) from error
    assert original["lights"], original
    assert await probe(host, "host", actor) == "host pressed"
    on_host = row(await suite.wait_resident_status(host_run, lambda frame:
        (found := row(frame, actor)) and found["powered"] != original["powered"]), actor)
    assert await load(guest) == actor
    assert await observe(guest_run, actor, equal=on_host) == on_host

    assert await probe(guest, "hide") == "hide"
    await suite.wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == OBSERVATORY
                                     and row(frame, actor) is None)
    assert await probe(host, "host", actor) == "host pressed"
    restored = await observe(host_run, actor, equal=original)
    assert await probe(guest, "show") == "show"
    assert await observe(guest_run, actor, equal=restored) == restored

    position = tuple(map(float, (await probe(host, "stage", actor)).split(",")))
    await suite.wait_resident_status(guest_run, lambda frame: (
        (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
        and dist(point, position) < 80))
    pressed = time.time()
    guest_press = await probe(guest, "guest", actor)
    assert guest_press in ("guest pressed", "guest changed power locally"), guest_press
    assert await observe(host_run, actor, equal=on_host) == on_host
    assert await observe(guest_run, actor, equal=on_host) == on_host
    frame = await suite.wait_resident_status(guest_run, lambda current: row(current, actor) == on_host)
    async with asyncio.timeout(6):
        while not any(event.get("time_unix", 0) >= pressed
                      and event.get("native_stock_switch_interaction", {}).get("actor") == actor
                      for event in await suite.resident_events(guest_run, frame)):
            await asyncio.sleep(.08)
    assert await probe(host, "return-guest", actor) == "guest returned"
    assert await probe(guest, "unload") == "unload"
    assert await probe(host, "unload") == "unload"
    for run in (guest_run, host_run):
        await suite.wait_resident_status(run, lambda frame: frame.get("game", {}).get("map") == OBSERVATORY
                                         and row(frame, actor) is None)
    assert suite.campaign_save_hashes(host.campaign) == checkpoint
    return {"actor": actor, "linked_lights": len(original["lights"]), "late_load": on_host,
            "reshown": restored, "guest_input": guest_press, "guest_press": on_host,
            "unloaded_on_both": True, "checkpoint_unchanged": True}


async def exercise(work, first, second, saved, streamed_switch=False):
    with ExitStack() as fixtures:
        for game in (first, second):
            fixtures.enter_context(suite.installed_probe(game, mod="SilverfishPortableOilLampProbe",
                                                         fixture="portable_oil_lamp_probe.lua"))
        await suite.verify(work, first, second, saved, stock_warehouse_switch=True,
                           oil_lamps_check=verify_oil_lamps,
                           streamed_switch_check=verify_streamed_switch if streamed_switch else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--streamed-switch", action="store_true")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve(), args.streamed_switch))
