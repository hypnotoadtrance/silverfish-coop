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


async def exercise(work, first, second, saved, *, capture=False):
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
        immediate = await probe(guest, "press")
        earlier = await probe(host, "remote")
        mounted = earlier
        until = time.monotonic() + 1.5
        while not mounted.startswith("true,5,") and time.monotonic() < until:
            mounted = await probe(host, "remote")
            await asyncio.sleep(.025)
        climb_start = await probe(guest, "climb") if mounted.startswith("true,5,") else immediate
        await asyncio.sleep(.75)
        samples, events = (await probe(guest, "report")).split(";")
        final_host, final_guest = await asyncio.gather(probe(host, "remote"), probe(guest, "local"))
        trial = dict(actor=ladder, initial=placed, press=immediate, host_after_press=earlier, host_mounted=mounted,
                     climb_start=climb_start,
                     host_final=final_host, guest_final=final_guest, samples=samples.split("|"), events=events.split("|"))
        if not capture:
            assert immediate.startswith("true,5,"), trial
            assert final_host.startswith("true,5,") and final_guest.startswith("true,5,"), trial
            assert position(final_guest)[2] - position(climb_start)[2] > 10, trial
            assert dist(position(final_host), position(final_guest)) < 32, trial
            guest_released = await probe(guest, "dismount")
            assert guest_released.startswith("false,"), guest_released
            async with asyncio.timeout(4):
                while (remote := await probe(host, "remote")).startswith("true,"):
                    await asyncio.sleep(.05)
            trial["release"] = {"host": remote, "guest": guest_released}
        evidence["stock_ladder"] = trial
        return evidence

    suite.verify_stock_dock_gate = ladder_case
    try:
        with ExitStack() as scopes:
            for game in (first, second):
                scopes.enter_context(suite.installed_probe(game, mod="SilverfishPortableLadderProbe",
                                                          fixture="portable_ladder_probe.lua"))
            await suite.verify(work, first, second, saved, stock_dock_gate=True)
    finally:
        suite.verify_stock_dock_gate = stock_gate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--capture", action="store_true", help="sample a baseline without requiring a synchronized climb")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve(), capture=args.capture))

