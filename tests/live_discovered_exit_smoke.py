"""Verify one registered and one runtime-discovered exit on an isolated pair."""

import argparse
import asyncio
import math
from functools import partial
from pathlib import Path

import live_portable_title_smoke as suite
from live_unified_title_smoke import start_pair
from tools.silverfish.authored_routes import AUTHORED_ROUTES, MAPS
from tools.silverfish.travel import AUTHORED_MAPS
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


UNLISTED_SHOP = "/Game/Classes/Maps/Maps_SafeZones/Shop_Default"


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


async def live_exit(host, guest, pawn, probe, *, unlisted_only=False, save_in_shop=False, **options):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    unlisted_world = "/Game/Classes/Maps/Maps_OverWorld/Campus_04_P"
    shop = UNLISTED_SHOP
    assert not {unlisted_world, shop} & (MAPS | AUTHORED_MAPS)
    lookups = {
        OBSERVATORY: "world",
        unlisted_world: "world",
        shop: "world",
        "/Game/Textures/ScifiWorlds_Texture_Custom_01": "non-world",
        "/Game/CoopFixture/AbsentMap": "missing",
        "/Game/../AbsentMap": "invalid-package",
    }
    observed = []
    for seat in (host, guest):
        verdicts = {}
        for path in lookups:
            found = await seat.bridge.command("native-map-package", path)
            verdicts[path] = found["ack"]["message"].removeprefix("map-package:")
        observed.append(verdicts)
    assert all(verdicts == lookups for verdicts in observed), observed

    stock_routes = ((OBSERVATORY, CROSSROADS),) if unlisted_only else ((OBSERVATORY, CROSSROADS), (CROSSROADS, OBSERVATORY))
    for source, destination in stock_routes:
        route = route_between(source, destination)
        for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
        reached = await wait_for_party(host_run, guest_run, destination)
        events = await suite.resident_events(host_run, reached)
        settled, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == route.actor
                    and row["source"] == source)
        assert settled["result"]["ok"] and not settled["discovered"], settled
        pawn = reached["game"]["native_guest_save"]["pawn"]

    evidence = {"map_package_verdicts": lookups}
    if not unlisted_only:
        evidence["persistent_exit"] = await live_discovered_exit(host, guest, pawn, probe, streamed=False)
        reached = await wait_for_party(host_run, guest_run, CROSSROADS)
        route = route_between(CROSSROADS, OBSERVATORY)
        for participant, position in ((reached["game"]["native_guest_save"]["pawn"], "near"),
                                      ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
        reached = await wait_for_party(host_run, guest_run, OBSERVATORY)
        evidence["streamed_exit"] = await live_discovered_exit(
            host, guest, reached["game"]["native_guest_save"]["pawn"], probe, streamed=True)
    reached = await wait_for_party(host_run, guest_run, CROSSROADS)
    route = route_between(CROSSROADS, OBSERVATORY)
    for participant in (reached["game"]["native_guest_save"]["pawn"], "local"):
        await host.bridge.command("native-exit-position", participant, "near", route.entrance, route.actor)
    outbound = await cross_unlisted(host, guest, probe, "shop", CROSSROADS, shop)
    if save_in_shop:
        return dict(evidence, entered_unlisted_map=outbound)
    returned = await cross_unlisted(host, guest, probe, "return", shop, CROSSROADS)
    return dict(evidence, unlisted_map_round_trip=[outbound, returned])


async def cross_unlisted(host, guest, probe, action, source, destination):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    actor = (await probe(host.bridge, "portable-discovered-exit", action)).strip().rsplit(".", 1)[-1]
    await asyncio.sleep(1.2)
    assert (await probe(host.bridge, "portable-discovered-exit", "regroup")).strip() == "near"
    await suite.wait_resident_status(host_run, lambda frame: (
        frame.get("game", {}).get("map") == source and frame["game"]["native_zone_wall"]["host_open"]))
    assert (await probe(host.bridge, "portable-discovered-exit", "enter")).strip() == "entered"
    reached = await wait_for_party(host_run, guest_run, destination)
    events = await suite.resident_events(host_run, reached)
    departure, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == actor
                  and row["source"] == source)
    assert departure["discovered"] and departure["destination"] == destination and departure["result"]["ok"], departure
    return departure


async def check_unlisted_resume(host, guest, character_name, **kwargs):
    save = {}

    async def capture_checkpoint(_host, _status, saved, character):
        assert saved["world"]["map"] == UNLISTED_SHOP
        save.update(host_slot=saved["world"]["native_host_save"]["slot"],
                    guest_slot=saved["characters"][character]["state"]["native_guest_save"]["slot"],
                    revision=saved["revision"])

    async def verify_reloaded(host, guest, pawn):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        active = await wait_for_party(host_run, guest_run, UNLISTED_SHOP)
        events = await suite.resident_events(host_run, active)
        restored_host, = (event for event in events if event["event"] == "native_host_save_restored")
        restored_guest, = (event for event in events if event["event"] == "native_guest_save_restored"
                           and event["pawn"] == pawn)
        assert restored_host["map"] == restored_guest["map"] == UNLISTED_SHOP
        assert (restored_host["slot"], restored_guest["slot"]) == (save["host_slot"], save["guest_slot"])
        result = await cross_unlisted(host, guest, suite.portable_probe_command, "return", UNLISTED_SHOP, CROSSROADS)
        return {"restored_host": restored_host, "restored_guest": restored_guest, "return_to_crossroads": result}

    first = await start_pair(host, guest, character_name, **kwargs, after_exit=capture_checkpoint)
    second = await start_pair(host, guest, character_name, load_existing=True, resume_character=first["character_id"],
                              initial_map=UNLISTED_SHOP, wait_status=suite.wait_resident_status, on_join=verify_reloaded)
    assert second["campaign_id"] == first["campaign_id"] and second["character_id"] == first["character_id"]
    assert second["revision"] > save["revision"] == first["revision"]
    assert (second["host_pid"], second["guest_pid"]) == (first["host_pid"], first["guest_pid"])
    return dict(first, unlisted_campaign_reopen=second)


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
    parser.add_argument("--unlisted-only", action="store_true")
    parser.add_argument("--resume-unlisted", action="store_true")
    args = parser.parse_args()
    work, first, second, saves = (path.resolve() for path in (args.work, args.first_game, args.second_game, args.vanilla_saved))
    work.mkdir(parents=True, exist_ok=True)
    suite.verify_weather_travel = partial(live_exit, unlisted_only=args.unlisted_only or args.resume_unlisted,
                                        save_in_shop=args.resume_unlisted)
    if args.resume_unlisted:
        suite.start_pair = check_unlisted_resume
    with suite.installed_probe(first, mod="SilverfishPortableExitDiscoveryProbe", fixture="portable_discovered_exit_probe.lua"):
        asyncio.run(suite.verify(work, first, second, saves, stock_weather_travel=True,
                                 stock_placed_item=not args.resume_unlisted))
