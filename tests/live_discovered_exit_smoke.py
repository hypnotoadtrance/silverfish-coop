"""Verify one registered and one runtime-discovered exit on an isolated pair."""

import argparse
import asyncio
import math
from contextlib import ExitStack
from functools import partial
from pathlib import Path

import live_portable_title_smoke as suite
from live_dynamic_board_smoke import probe as eye_probe, verify_eye_board
from live_unified_title_smoke import start_pair
from portable_stock_shovel import stock_inventory
from tools.silverfish import native_guest_drop as drop, native_host_stack_drop as host_drop, native_world_loot as loot
from tools.silverfish.control import submit
from tools.silverfish.authored_routes import AUTHORED_ROUTES, MAPS
from tools.silverfish.travel import AUTHORED_MAPS
from tools.silverfish.travel import CISTERN, CROSSROADS, OBSERVATORY


UNLISTED_SHOP = "/Game/Classes/Maps/Maps_SafeZones/Shop_Default"
UNLISTED_DEMO = "/Game/PolygonHorrorAsylum/Maps/Overview"


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


async def live_exit(host, guest, pawn, probe, *, unlisted_only=False, save_in_shop=False, with_door=False,
                    unlisted_map=UNLISTED_SHOP, mounted_items=False, mounted_quest=False,
                    defer_host_travel=False, **options):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    unlisted_world = "/Game/Classes/Maps/Maps_OverWorld/Campus_04_P"
    shop = unlisted_map
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
    if with_door:
        evidence["discovered_and_registered_door"] = await live_discovered_door(host, guest, reached, probe)
        reached = await wait_for_party(host_run, guest_run, CROSSROADS)
    route = route_between(CROSSROADS, OBSERVATORY)
    for participant in (reached["game"]["native_guest_save"]["pawn"], "local"):
        await host.bridge.command("native-exit-position", participant, "near", route.entrance, route.actor)
    outbound = await cross_unlisted(host, guest, probe, "demo" if shop == UNLISTED_DEMO else "shop", CROSSROADS, shop)
    if defer_host_travel:
        reads = (host.bridge.directory / "portable-listener-handoff.txt").read_text(encoding="ascii")
        pending, interaction, maintenance = reads.strip().split("|")
        assert pending == "true" and int(interaction) > 0 and int(maintenance) > 0, reads
        evidence["deferred_listener_handoff"] = {"paused_input_polls": int(interaction),
                                                "paused_maintenance_polls": int(maintenance)}
    try:
        if mounted_items:
            evidence["mounted_items"] = await live_mounted_items(host, guest, shop)
        if mounted_quest:
            assert shop == UNLISTED_DEMO
            evidence["mounted_quest"] = await live_mounted_quest(host, guest)
    finally:
        if not save_in_shop:
            returned = await cross_unlisted(host, guest, probe, "return", shop, CROSSROADS)
    if save_in_shop:
        return dict(evidence, entered_unlisted_map=outbound)
    return dict(evidence, unlisted_map_round_trip=[outbound, returned])


def defer_listener_open(source):
    marker = '    statics:OpenLevel(context, FName(level), true, options)'
    hold = r'''
    if level == "/Game/PolygonHorrorAsylum/Maps/Overview" and options == "listen" then
        local proof = {input = 0, maintenance = 0}
        status_publication.deferred_listener_fixture = proof
        ExecuteInGameThreadWithDelay(200, function()
            local file = assert(io.open(bridge .. "/portable-listener-handoff.txt", "wb"))
            file:write(tostring(SilverfishNativeListenTravelPending()), "|", proof.input, "|", proof.maintenance, "\n")
            file:close()
            status_publication.deferred_listener_fixture = nil
            statics:OpenLevel(context, FName(level), true, options)
        end)
        return
    end
'''
    assert source.count(marker) == 1
    source = source.replace(marker, hold + marker)
    checks = (("        ", "input"), ("    ", "maintenance"))
    for indent, counter in checks:
        line = f"\n{indent}if status_publication.listen_departing() then return end\n"
        instrumented = f"""
{indent}if status_publication.listen_departing() then
{indent}    local proof = status_publication.deferred_listener_fixture
{indent}    if proof then proof.{counter} = proof.{counter} + 1 end
{indent}    return
{indent}end
"""
        assert source.count(line) == 1
        source = source.replace(line, instrumented)
    return source


async def live_mounted_quest(host, guest):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    reached = await suite.wait_resident_status(guest_run, lambda frame:
        frame.get("state") == "connected" and frame.get("game", {}).get("map") == UNLISTED_DEMO)

    async def stream(seat):
        assert await eye_probe(seat.bridge.directory, "stream") == "loading"
        async with asyncio.timeout(15):
            while (board := await eye_probe(seat.bridge.directory, "placeboard")) == "waiting":
                await asyncio.sleep(.1)
        return board

    board, guest_board = await asyncio.gather(stream(host), stream(guest))
    assert board == guest_board and board.startswith("stream:") and "CoopEyeBoardSublevel" in board, (board, guest_board)
    return await verify_eye_board(host_run, guest_run, host.bridge.directory, reached["character_id"],
                                  wait_status=suite.wait_resident_status, mounted_board=board,
                                  guest_bridge=guest.bridge.directory)


async def live_mounted_items(host, guest, area):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    before = suite.campaign_save_hashes(host.campaign), suite.stock_save_hashes(host.profile)
    await guest.bridge.wait_for(lambda frame: frame.get("map") == area
                                and frame.get("native_inventory_view_binding") == "ready")
    items = await stock_inventory(guest_run, guest=True)
    item = next(row for row in items if not row["stackable"] and drop.droppable(row, generic=True))
    guid = item["guid"]
    debit = await submit(guest_run, drop.STOCK_DROP_FIXTURE, target=guid)
    assert debit["ok"], debit
    dropped = debit[drop.RESULT]
    assert (dropped["guid"], dropped["class_path"], dropped["quantity"]) == (guid, item["class_path"], 1)
    assert guid not in {row["guid"] for row in await stock_inventory(guest_run, guest=True)}

    async def collect(seat, run, dropped):
        identity = loot.player_drop_identity(dropped["drop_id"])
        visible = []
        for viewer in (host, guest):
            frame = await viewer.bridge.wait_for(lambda frame: identity in frame.get(loot.KEY, {}), timeout=12)
            assert frame[loot.KEY][identity]["map"] == area
            visible.append(frame[loot.KEY][identity]["actor"])
        authority = await host.bridge.wait_for(lambda frame: frame.get("map") == area
                                               and frame.get("native_guest_save", {}).get("state") == "bound")
        pawn = (authority["native_guest_save"]["pawn"] if seat is guest
                else authority["pawns"]["local"]["name"])
        await host.bridge.command(loot.APPROACH, identity, pawn)
        pressed = await seat.bridge.command(loot.FIXTURE, identity)
        assert pressed["ack"]["message"] == "stock world loot interaction intercepted", pressed["ack"]
        for viewer in (host, guest):
            await viewer.bridge.wait_for(lambda frame: frame.get("map") == area
                                         and identity not in frame.get(loot.KEY, {}), timeout=12)
        recovered, = (row for row in await stock_inventory(run, guest=seat is guest) if row["guid"] == guid)
        assert (recovered["source"], recovered["class_path"], recovered["quantity"]) == ("backpack", item["class_path"], 1)
        return {"item": recovered, "both_actor_paths": visible}

    host_pickup = await collect(host, host_run, dropped)
    await host.bridge.wait_for(lambda frame: frame.get(host_drop.SCOPE))
    await host.bridge.command(host_drop.FIXTURE, guid, "1", "context_menu")
    host_frame = await suite.wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == area)
    async with asyncio.timeout(8):
        while True:
            matches = [row for row in await suite.resident_events(host_run, host_frame)
                       if row["event"] == "native_host_stock_drop" and row.get(drop.RESULT, {}).get("guid") == guid]
            if matches:
                host_dropped, = matches
                break
            await asyncio.sleep(.1)
    assert host_dropped["entry"] == "context_menu" and not host_dropped["error"], host_dropped
    await host.bridge.command(host_drop.CLOSE_FIXTURE, guid)
    guest_pickup = await collect(guest, guest_run, host_dropped[drop.RESULT])
    assert before == (suite.campaign_save_hashes(host.campaign), suite.stock_save_hashes(host.profile))
    return {"guest_drop": dropped, "host_pickup": host_pickup, "host_drop": host_dropped[drop.RESULT],
            "guest_pickup": guest_pickup, "checkpoint_unchanged": True}


async def live_discovered_door(host, guest, crossed, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    route = route_between(CROSSROADS, CISTERN)
    for participant in (crossed["game"]["native_guest_save"]["pawn"], "local"):
        await host.bridge.command("native-door-position", participant, "near", route.entrance)
    path = (await probe(host.bridge, "portable-discovered-exit", "doorSpawn")).strip()
    actor = path.rsplit(".", 1)[-1]
    assert actor != route.actor and not any(row.source == CROSSROADS and row.actor == actor for row in AUTHORED_ROUTES)

    for action, decision in (("doorGuest", "guest_denied"), ("doorHost", "host_locked"),
                             ("doorFar", "host_out_of_range")):
        await probe(host.bridge, "portable-discovered-exit", action)
        frame = await suite.wait_resident_status(host_run, lambda frame: (
            frame["game"].get("native_door", {}).get("decision") == decision), timeout=5)
        assert frame["zone_epoch"] == crossed["zone_epoch"]
    await probe(host.bridge, "portable-discovered-exit", "doorRegroup")
    await probe(host.bridge, "portable-discovered-exit", "doorHost")
    reached = await wait_for_party(host_run, guest_run, CISTERN)
    events = await suite.resident_events(host_run, reached)
    departure, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == actor)
    assert departure["discovered"] and departure["exit_crossing"] > 0 and departure["travel_time_step"] == 23, departure
    assert departure["destination"] == CISTERN and departure["entrance"] == route.entrance and departure["result"]["ok"]
    arrival, = (row["authored_arrival"] for row in events if row["event"] == "zone_travel_ready"
                and row["epoch"] == reached["zone_epoch"])
    assert arrival["placement"] == "arrow" and arrival["actor"].endswith("." + route.arrival_actor), arrival
    registered = route_between(CISTERN, CROSSROADS)
    for participant in (reached["game"]["native_guest_save"]["pawn"], "local"):
        await host.bridge.command("native-door-position", participant, "near", registered.entrance)
    await host.bridge.command("native-door-probe", "local", registered.entrance)
    returned = await wait_for_party(host_run, guest_run, CROSSROADS)
    events = await suite.resident_events(host_run, returned)
    reverse, = (row for row in events if row["event"] == "native_authored_zone_request"
                and row["source"] == CISTERN and row["actor"] == registered.actor)
    assert reverse["result"]["ok"] and not reverse["discovered"], reverse
    return {"departure": departure, "arrival": arrival, "registered_return": reverse,
            "guest_denied": True, "locked_denied": True, "out_of_range_denied": True}


async def cross_unlisted(host, guest, probe, action, source, destination):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    actor = (await probe(host.bridge, "portable-discovered-exit", action)).strip().rsplit(".", 1)[-1]
    await asyncio.sleep(1.2)
    assert (await probe(host.bridge, "portable-discovered-exit", "regroup")).strip() == "near"
    await suite.wait_resident_status(host_run, lambda frame: (
        frame.get("game", {}).get("map") == source and frame["game"]["native_zone_wall"]["host_open"]))
    assert (await probe(host.bridge, "portable-discovered-exit", "enter")).strip() == "entered"
    try:
        reached = await wait_for_party(host_run, guest_run, destination)
    except RuntimeError as error:
        for seat in (host, guest):
            frame = seat.bridge.snapshot() or {}
            print(f"Travel diagnostic for {seat.profile.name}: "
                  f"{frame.get('state')}, {frame.get('map')}, {frame.get('diagnostic')}, {seat.bridge.status_error}",
                  flush=True)
        raise AssertionError(f"The party did not settle in {destination}") from error
    events = await suite.resident_events(host_run, reached)
    departure, = (row for row in events if row["event"] == "native_authored_zone_request" and row["actor"] == actor
                  and row["source"] == source)
    assert departure["discovered"] and departure["destination"] == destination and departure["result"]["ok"], departure
    return departure


async def check_unlisted_resume(host, guest, character_name, *, unlisted_map=UNLISTED_SHOP, **kwargs):
    save = {}

    async def capture_checkpoint(_host, _status, saved, character):
        assert saved["world"]["map"] == unlisted_map
        save.update(host_slot=saved["world"]["native_host_save"]["slot"],
                    guest_slot=saved["characters"][character]["state"]["native_guest_save"]["slot"],
                    revision=saved["revision"])

    async def verify_reloaded(host, guest, pawn):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        active = await wait_for_party(host_run, guest_run, unlisted_map)
        for seat in (host, guest):
            await seat.bridge.wait_for(lambda game: game.get("map") == unlisted_map
                                       and game.get("stock_pause_exit", {}).get("state") == "armed", timeout=8)
        events = await suite.resident_events(host_run, active)
        restored_host, = (event for event in events if event["event"] == "native_host_save_restored")
        restored_guest, = (event for event in events if event["event"] == "native_guest_save_restored"
                           and event["pawn"] == pawn)
        assert restored_host["map"] == restored_guest["map"] == unlisted_map
        assert (restored_host["slot"], restored_guest["slot"]) == (save["host_slot"], save["guest_slot"])
        result = await cross_unlisted(host, guest, suite.portable_probe_command, "return", unlisted_map, CROSSROADS)
        return {"restored_host": restored_host, "restored_guest": restored_guest, "return_to_crossroads": result}

    first = await start_pair(host, guest, character_name, **kwargs, after_exit=capture_checkpoint)
    second = await start_pair(host, guest, character_name, load_existing=True, resume_character=first["character_id"],
                              initial_map=unlisted_map, wait_status=suite.wait_resident_status, on_join=verify_reloaded,
                              host_exit_actions=("prompt", "confirm"))
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
    parser.add_argument("--discovered-door", action="store_true")
    parser.add_argument("--outside-tree", action="store_true")
    parser.add_argument("--mounted-items", action="store_true")
    parser.add_argument("--mounted-quest", action="store_true")
    parser.add_argument("--defer-host-travel", action="store_true")
    args = parser.parse_args()
    if args.mounted_quest and not args.outside_tree:
        parser.error("--mounted-quest requires --outside-tree")
    if args.defer_host_travel and not args.outside_tree:
        parser.error("--defer-host-travel requires --outside-tree")
    work, first, second, saves = (path.resolve() for path in (args.work, args.first_game, args.second_game, args.vanilla_saved))
    work.mkdir(parents=True, exist_ok=True)
    unlisted = UNLISTED_DEMO if args.outside_tree else UNLISTED_SHOP
    suite.verify_weather_travel = partial(live_exit, unlisted_only=args.unlisted_only or args.resume_unlisted,
                                        save_in_shop=args.resume_unlisted, with_door=args.discovered_door,
                                        unlisted_map=unlisted, mounted_items=args.mounted_items,
                                        mounted_quest=args.mounted_quest, defer_host_travel=args.defer_host_travel)
    if args.resume_unlisted:
        suite.start_pair = partial(check_unlisted_resume, unlisted_map=unlisted)
    if args.mounted_items:
        previous = suite.start_pair

        async def with_stock_kit(*arguments, **options):
            options["create_character"] = suite.create_stock_drop_character
            return await previous(*arguments, **options)

        suite.start_pair = with_stock_kit
    with ExitStack() as probes:
        probes.enter_context(suite.installed_probe(
            first, mod="SilverfishPortableExitDiscoveryProbe", fixture="portable_discovered_exit_probe.lua"))
        if args.mounted_items:
            for game in (first, second):
                probes.enter_context(suite.installed_probe(game))
        if args.defer_host_travel:
            for game in (first, second):
                probes.enter_context(suite.portable_main_overlay(game, defer_listener_open))
        asyncio.run(suite.verify(work, first, second, saves, stock_weather_travel=True,
                                 stock_placed_item=not args.resume_unlisted, stock_eye_stacks=args.mounted_quest))
