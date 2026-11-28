"""Verify the stock Dock ladder and gate on one muted portable host and guest."""

import argparse
import asyncio
from contextlib import ExitStack
from math import dist
from pathlib import Path
import time

import live_portable_title_smoke as suite


def position(sample):
    return tuple(map(float, sample.split(",")[2:]))


async def exercise(work, first, second, saved, *, capture=False, crossroads_profile=False):
    stock_gate = suite.verify_stock_dock_gate

    async def ladder_case(host, guest):
        evidence = await stock_gate(host, guest)

        async def probe(seat, action):
            return await suite.portable_probe_command(seat.bridge, "portable-ladder", action)

        _, staged = await asyncio.gather(probe(host, "arm"), probe(guest, "arm"))
        ladder, state = staged.split(";")
        _, _, xyz = state.split(",", 2)
        placed = await probe(host, f"place\t{xyz}")
        async with asyncio.timeout(5):
            while dist(position(placed), position(await probe(guest, "local"))) > 30:
                await asyncio.sleep(.05)
        started = time.time()
        immediate = await probe(guest, "press")
        earlier = await probe(host, "remote")
        mounted = earlier
        until = time.monotonic() + 1.5
        while not mounted.startswith("true,5,") and time.monotonic() < until:
            mounted = await probe(host, "remote")
            await asyncio.sleep(.025)
        climb_start = await probe(guest, "climb") if mounted.startswith("true,5,") else immediate
        await asyncio.sleep(1.1)
        samples, events = (part.split("|") for part in (await probe(guest, "report")).split(";"))
        final_host, final_guest = await asyncio.gather(probe(host, "remote"), probe(guest, "local"))
        trial = dict(actor=ladder, initial=placed, press=immediate, host_after_press=earlier, host_mounted=mounted,
                     climb_start=climb_start,
                     host_final=final_host, guest_final=final_guest, samples=samples, events=events)
        if not capture:
            assert immediate.startswith("true,5,"), trial
            assert mounted.startswith("true,5,"), trial
            climbing = [position(sample.partition(",")[2]) for sample in samples if ",true,5," in sample]
            trial["climb_cm"] = round(max(point[2] for point in climbing) - position(climb_start)[2], 2)
            assert trial["climb_cm"] > 60, trial
            assert any("Dismount_Top" in event for event in events), trial
            assert final_host.startswith("false,") and final_guest.startswith("false,"), trial
            assert dist(position(final_host), position(final_guest)) < 32, trial
            run = guest.client / "run"
            frame = await suite.wait_resident_status(run, lambda state: state.get("state") == "connected")
            receipts = [event for event in await suite.resident_events(run, frame)
                        if event.get("event") == "local_interaction" and event["time_unix"] >= started
                        and "native_ladder" in event]
            assert [event["native_ladder"]["state"] for event in receipts] == ["mounted", "released"], receipts
            trial["receipts"] = receipts
        evidence["stock_ladder"] = trial
        return evidence

    suite.verify_stock_dock_gate = ladder_case
    try:
        with ExitStack() as scopes:
            for game in (first, second):
                scopes.enter_context(suite.installed_probe(game, mod="SilverfishPortableLadderProbe",
                                                          fixture="portable_ladder_probe.lua"))
            await suite.verify(work, first, second, saved, stock_dock_gate=True,
                               crossroads_profile=crossroads_profile, rendered=crossroads_profile,
                               crossroads_host_maintenance_trial=crossroads_profile)
    finally:
        suite.verify_stock_dock_gate = stock_gate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--capture", action="store_true", help="sample a baseline without requiring a synchronized climb")
    parser.add_argument("--crossroads-profile", action="store_true",
                        help="group the rendered Crossroads maintenance comparison and ladder check")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve(), capture=args.capture, crossroads_profile=args.crossroads_profile))

