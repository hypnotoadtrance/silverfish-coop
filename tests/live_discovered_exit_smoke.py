"""Verify one registered and one runtime-discovered exit on an isolated pair."""

import argparse
import asyncio
import math
from pathlib import Path

import live_portable_title_smoke as suite
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def route_between(source, destination):
    route, = (row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination))
    return route


async def wait_for_party(host_run, guest_run, destination):
    reached = await suite.wait_resident_status(host_run, lambda frame: (
        frame.get("game", {}).get("map") == destination
        and frame["game"].get("native_guest_save", {}).get("state") == "bound"
        and not frame.get("awaiting_zone_guest")), timeout=20)
    await suite.wait_resident_status(guest_run, lambda frame: (
        frame.get("game", {}).get("map") == destination and frame.get("zone_epoch") == reached["zone_epoch"]), timeout=10)
    return reached


async def live_exit(host, guest, pawn, probe, **options):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    for source, destination in ((OBSERVATORY, CROSSROADS), (CROSSROADS, OBSERVATORY)):
        route = route_between(source, destination)
        for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
        reached = await wait_for_party(host_run, guest_run, destination)
        events = await suite.resident_events(host_run, reached)
        settled, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == route.actor
                    and row["source"] == source)
        assert settled["result"]["ok"] and not settled["discovered"], settled
        pawn = reached["game"]["native_guest_save"]["pawn"]

    spawned = await live_discovered_exit(host, guest, pawn, probe, streamed=False)
    reached = await wait_for_party(host_run, guest_run, CROSSROADS)
    route = route_between(CROSSROADS, OBSERVATORY)
    for participant, position in ((reached["game"]["native_guest_save"]["pawn"], "near"),
                                  ("local", "near"), ("local", "inside")):
        await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
    reached = await wait_for_party(host_run, guest_run, OBSERVATORY)
    streamed = await live_discovered_exit(host, guest, reached["game"]["native_guest_save"]["pawn"], probe, streamed=True)
    return {"persistent_exit": spawned, "streamed_exit": streamed}


async def live_discovered_exit(host, guest, pawn, probe, *, streamed):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    route = route_between(OBSERVATORY, CROSSROADS)
    for participant in (pawn, "local"):
        await host.bridge.command("native-exit-position", participant, "near", route.entrance, route.actor)
    if streamed:
        assert (await probe(host.bridge, "portable-discovered-exit", "load")).strip() == "loading"
        for _ in range(15):
            actor = (await probe(host.bridge, "portable-discovered-exit", "spawnstream")).strip()
            if actor != "waiting":
                break
            await asyncio.sleep(0.2)
        assert "CoopStreamExitProbe" in actor, actor
    else:
        actor = (await probe(host.bridge, "portable-discovered-exit", "spawn")).strip()
    actual_name = actor.rsplit(".", 1)[-1]
    assert actual_name != route.actor and not any(row.source == OBSERVATORY and row.actor == actual_name
                                                  for row in AUTHORED_ROUTES)
    start = await suite.wait_resident_status(host_run, lambda frame: (
        frame.get("game", {}).get("map") == OBSERVATORY and not frame["game"]["native_zone_wall"]["host_open"]))
    # The native census runs once a second. Give both role scopes a chance to load the cloned shape.
    await asyncio.sleep(2)
    assert (await probe(host.bridge, "portable-discovered-exit", "alone")).strip() == "wall"
    refused = await suite.wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == OBSERVATORY)
    assert refused["zone_epoch"] == start["zone_epoch"]
    assert (await probe(host.bridge, "portable-discovered-exit", "regroup")).strip() == "near"
    await suite.wait_resident_status(host_run, lambda frame: (
        frame.get("game", {}).get("map") == OBSERVATORY and frame["game"]["native_zone_wall"]["host_open"]))
    assert (await probe(host.bridge, "portable-discovered-exit", "cross")).strip() == "entered"

    reached = await wait_for_party(host_run, guest_run, CROSSROADS)
    events = await suite.resident_events(host_run, reached)
    departure, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == actual_name)
    step = 19 if streamed else 17
    assert departure["discovered"] is True and departure["exit_crossing"] > 0 and departure["travel_time_step"] == step
    tag = "Mod-Stream-Arrival" if streamed else route.entrance
    assert departure["destination"] == CROSSROADS and departure["entrance"] == tag and departure["result"]["ok"]
    arrival, = (row["authored_arrival"] for row in events if row["event"] == "zone_travel_ready"
                and row["epoch"] == reached["zone_epoch"])
    assert arrival["placement"] == ("streamed-arrow" if streamed else "arrow"), arrival
    if streamed:
        assert "CoopStreamArrivalProbe" in arrival["actor"], arrival
        players = reached["game"]["pawns"]
        assert math.dist(players["local"]["location"], arrival["location"]) <= 200, reached
        assert math.dist(players["guest"]["location"], players["local"]["location"]) <= 250, reached
    fallback = await host.bridge.command("native-authored-arrival", "Missing-Mod-Entrance", "overlap", "-")
    assert fallback["native_authored_arrival"]["placement"] == "stock-spawn:entrance-unavailable"
    assert fallback["state"] == "hosting" and fallback["map"] == CROSSROADS
    assert all(not (seat.bridge.directory / "native-authored-exit-events").exists() for seat in (host, guest))
    return {"actor_path": actor, "stock_exit_discovery": departure, "arrival": arrival, "discovered_wall_blocked_and_opened": True,
            "admitted_crossing_survived_guest_movement": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    work, first, second, saves = (path.resolve() for path in (args.work, args.first_game, args.second_game, args.vanilla_saved))
    work.mkdir(parents=True, exist_ok=True)
    suite.verify_weather_travel = live_exit
    with suite.installed_probe(first, mod="SilverfishPortableExitDiscoveryProbe", fixture="portable_discovered_exit_probe.lua"):
        asyncio.run(suite.verify(work, first, second, saves, stock_weather_travel=True, stock_placed_item=True))
