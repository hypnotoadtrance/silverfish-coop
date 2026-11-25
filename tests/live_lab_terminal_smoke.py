"""Exercise the Lab E206 terminal on a muted portable host and connected guest."""

import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path

import live_portable_title_smoke as suite

from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import CROSSROADS, LAB_E206, OBSERVATORY, RAIL_YARD


async def verify_lab_terminal(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    routes = []
    areas = (OBSERVATORY, CROSSROADS, RAIL_YARD, LAB_E206)
    for source, destination in zip(areas, areas[1:]):
        route, = (route for route in AUTHORED_ROUTES
                  if (route.source, route.destination) == (source, destination))
        await suite.wait_resident_status(host_run, lambda frame:
            frame["game"]["map"] == source and frame["game"].get("native_zone", {}).get("state") == "ready")
        if route.kind == "door":
            for name in (pawn, "local"):
                await host.bridge.command("native-door-position", name, "near", route.entrance)
            await host.bridge.command("native-door-probe", "local", route.entrance)
        else:
            for name, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                await host.bridge.command("native-exit-position", name, position, route.entrance, route.actor)
        arrived = await suite.wait_resident_status(host_run, lambda frame:
            frame["game"]["map"] == destination and not frame.get("awaiting_zone_guest")
            and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=65)
        pawn = arrived["game"]["native_guest_save"]["pawn"]
        await suite.wait_resident_status(guest_run, lambda frame:
            frame.get("zone_epoch") == arrived["zone_epoch"] and frame["game"]["map"] == destination, timeout=45)
        routes.append({"destination": destination, "entrance": route.entrance})

    async def terminal(action):
        return await suite.portable_probe_command(guest.bridge, "portable-lab-terminal", action)

    actor, widget = (await terminal("open")).split(";")
    command, before, after = (await terminal("submit")).split(";")
    assert command and int(after) > int(before)
    await asyncio.sleep(2)
    assert await terminal("status") == "confirmed"
    assert guest.bridge.process.poll() is None and host.bridge.process.poll() is None
    return dict(routes=routes, actor=actor, widget=widget, command=command, saved_text_bytes=[int(before), int(after)])


async def exercise(work, first, second, saved):
    with ExitStack() as scopes:
        for game in (first, second):
            scopes.enter_context(suite.tutorial_dock_zone_overlay(game))
        scopes.enter_context(suite.installed_probe(second, mod="SilverfishPortableLabTerminalProbe",
                                                   fixture="portable_lab_terminal_probe.lua"))
        await suite.verify(work, first, second, saved, stock_cooked_meal=True,
                           lab_terminal_check=verify_lab_terminal)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve()))
