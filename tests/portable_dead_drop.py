"""Run the stock randomized Dead Drops through the installed title."""

import asyncio
from contextlib import contextmanager
import json
import math

from live_multi_smoke import campaign_save_hashes, saved_hashes
from portable_guest_world_start import wait_resident_status
from tools.silverfish import native_authored_pickup, native_dead_drop, native_host_save, native_wire_board
from tools.silverfish.authored_loot import same_pose
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_computer_board import BOARDS
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, LAKE, OBSERVATORY


METRO_WANDERER_SEED = 418508
METRO_STATIC_SEED = 874453998
METRO_SEED_REQUEST = "portable-metro-placement-seed.txt"


@contextmanager
def arm_resident_metro_seed(bridge):
    request = bridge.directory / METRO_SEED_REQUEST
    with request.open("x", encoding="ascii") as stream:
        stream.write(str(METRO_WANDERER_SEED))
    try:
        yield
    finally:
        request.unlink()


def resident_metro_seed_overlay(script):
    needle = "    local planned = starter(seed, slot)\n"
    replacement = rf'''    local planned = starter(seed, slot)
    local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
    if os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
        and mailbox and mailbox:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then
        local request = io.open(mailbox .. "/{METRO_SEED_REQUEST}", "rb")
        if request then
            local requested = request:read(7)
            request:close()
            require_exact(requested == "{METRO_WANDERER_SEED}", "portable_metro_seed_request_invalid")
            -- Preserve the worker's instance seed and all normal stock startup receipts.
            planned.world_seed = starter({METRO_WANDERER_SEED}, slot).world_seed
            require_exact(planned.world_seed == {METRO_STATIC_SEED}, "portable_metro_seed_derivation_changed")
        end
    end
'''
    assert script.count(needle) == 1
    return script.replace(needle, replacement)


def observe_dead_drop_replies(script):
    needle = '''        SilverfishPublishLocalBridgeReply(pending.ticket,
            native_authored_pickup.dead_drop_reply(fields, state, current_phase, status_row))'''
    replacement = '''        local reply = native_authored_pickup.dead_drop_reply(fields, state, current_phase, status_row)
        SilverfishPublishLocalBridgeReply(pending.ticket, reply)
        local log = io.open(bridge .. "/dead-drop-replies.tsv", "a")
        if log then
            log:write(fields[2], "\\t", fields[3] or "-", "\\t", #reply, "\\n")
            log:close()
        end'''
    assert script.count(needle) == 1
    return script.replace(needle, replacement)


def allow_resident_dead_drop_fixtures(script):
    gate = 'os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
    setting = f"local host_fixtures = {gate}"
    assert script.count(setting) == 1
    return script.replace(setting, f'{setting} or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"')


def allow_resident_contract_xp(script):
    gate = 'os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"'
    assert script.count(gate) == 1
    return script.replace(gate, f'({gate} and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1")')


async def verify_dead_drop(host, guest, contract, drop, *, via=(LAKE,), cash=None):
    generic_board = drop.quest != contract.quest_id
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def save_fingerprints():
        manifest = json.loads((host.campaign / "save/campaign.json").read_text(encoding="utf-8"))
        stock = native_host_save.directory(native_host_save.checkpoint_profile(host.profile, manifest))
        return campaign_save_hashes(host.campaign), saved_hashes(stock)

    async def action(run, verb, **kwargs):
        reply = await submit(run, verb, **kwargs)
        assert reply["ok"], (verb, kwargs, reply)
        return reply

    async def seats(area):
        authority = await wait_resident_status(host_run, lambda frame: frame["game"]["map"] == area
            and not frame.get("awaiting_zone_guest") and frame["game"].get("native_zone", {}).get("state") == "ready"
            and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        visitor = await wait_resident_status(guest_run, lambda frame: frame["game"]["map"] == area
            and frame.get("state") == "connected"
            and frame.get("zone_epoch") == authority["zone_epoch"])
        return authority, visitor

    async def cross(source, destination):
        authority, _ = await seats(source)
        pawn = authority["game"]["native_guest_save"]["pawn"]
        route, = (row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination))
        if route.kind == "door":
            for participant in (pawn, "local"):
                await host.bridge.command("native-door-position", participant, "near", route.entrance)
            await host.bridge.command("native-door-probe", "local", route.entrance)
        else:
            for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                await host.bridge.command("native-exit-position", participant, placement, route.entrance, route.actor)
        arrived, _ = await seats(destination)
        assert arrived["zone_epoch"] != authority["zone_epoch"]

    async def select(area, verb):
        authority, _ = await seats(area)
        pawn = authority["game"]["native_guest_save"]["pawn"]
        board, epoch = BOARDS[area], authority["zone_epoch"]
        placement = (await host.bridge.command(contract.command("approach"), pawn))["pawns"]["guest"]
        assert placement["name"] == pawn and len(placement["location"]) == 3, placement

        def at_board(frame, status_key, states):
            game = frame.get("game", {})
            local = game.get("pawns", {}).get("local", {})
            status = game.get(status_key, {})
            return (frame.get("state") == "connected" and frame.get("zone_epoch") == epoch
                    and game.get("map") == area and local.get("name") == status.get("pawn")
                    and status.get("board") == board and status.get("state") in states
                    and (position := local.get("location")) is not None
                    and math.dist(position, placement["location"]) <= 25)

        await wait_resident_status(guest_run, lambda frame:
            at_board(frame, "native_wire_board", ("armed", "focused")), timeout=8)
        await action(guest_run, native_wire_board.STOCK_F_FIXTURE, target=board)
        await wait_resident_status(guest_run, lambda frame:
            at_board(frame, "native_wire_board_stock", ("visible",)), timeout=14)
        if not generic_board:
            preflight = (await action(guest_run, contract.command("preflight")))[contract.key]
            assert preflight["board"] == board
        await action(guest_run, native_wire_board.STOCK_FIXTURE, target=f"{drop.quest}|{verb}")

    paths = (OBSERVATORY, CROSSROADS, LAKE, BLACK_MARKET)
    if drop.map == CROSSROADS:
        start = OBSERVATORY
    else:
        for source, destination in zip(paths, paths[1:]):
            await cross(source, destination)
        start = BLACK_MARKET
    authority, _ = await seats(start)
    host_pawn = authority["game"]["pawns"]["local"]["name"]
    await host.bridge.command(contract.command("approach"), host_pawn)
    await host.bridge.command(contract.command("xp-fixture"), host_pawn)
    before = save_fingerprints()
    await select(start, "start")
    accepted = await wait_resident_status(host_run, lambda frame:
        frame["gameplay"].get("native_quests", {}).get(drop.quest, {}).get("step") == 1)
    assert save_fingerprints() == before
    if start == OBSERVATORY:
        await cross(OBSERVATORY, CROSSROADS)
    else:
        route = (BLACK_MARKET, *via, drop.map)
        for source, destination in zip(route, route[1:]):
            await cross(source, destination)

    authority, _ = await seats(drop.map)
    epoch = authority["zone_epoch"]
    sample = await wait_resident_status(host_run, lambda frame:
        frame["gameplay"].get(native_dead_drop.FRAME, {}).get("epoch") == epoch)
    pose = sample["gameplay"][native_dead_drop.FRAME]["transform"]
    seed = sample["game"].get("static_world_seed", {}).get("value")
    if drop is native_dead_drop.METRO_DROP:
        assert seed == METRO_STATIC_SEED, sample["game"].get("static_world_seed")

    async def projected():
        frame = await wait_resident_status(guest_run, lambda frame:
            native_dead_drop.observed(frame["game"], "projected").get("epoch") == epoch)
        observed = frame["game"][native_dead_drop.KEY]
        assert same_pose(pose, observed["transform"]), observed
        return observed

    first = await projected()
    # The original placement must survive beyond the native 1.8 second lease.
    await asyncio.sleep(2.1)
    assert (await projected())["pawn"] == first["pawn"]
    replies = guest.bridge.directory / "dead-drop-replies.tsv"
    samples = [int(size) for verb, visit, size in (line.split("\t") for line in replies.read_text().splitlines())
               if verb == native_dead_drop.PROJECT and visit == epoch]
    assert len(samples) >= 2 and all(0 < size <= 800 for size in samples), samples

    await action(guest_run, "drop-link")
    await wait_resident_status(guest_run, lambda frame: not frame["link_connected"]
        and native_dead_drop.observed(frame["game"], "hidden"), timeout=8)
    resumed = await action(guest_run, "reconnect")
    assert resumed["resumed"] and resumed["game_connected"], resumed
    assert (await projected())["pawn"] == first["pawn"]

    failed_poses = []
    for attempt in range(3):
        authority, _ = await seats(drop.map)
        pawn = authority["game"]["native_guest_save"]["pawn"]
        try:
            await host.bridge.command(native_authored_pickup.APPROACH, pawn, drop.source)
        except RuntimeError as error:
            if "_no_clear_sight:" not in str(error) or attempt == 2 or drop is native_dead_drop.METRO_DROP:
                raise
            failed_poses.append({"pose": pose, "reason": str(error)})
            await cross(drop.map, via[-1])
            await cross(via[-1], drop.map)
            authority, _ = await seats(drop.map)
            epoch = authority["zone_epoch"]
            sample = await wait_resident_status(host_run, lambda frame:
                frame["gameplay"].get(native_dead_drop.FRAME, {}).get("epoch") == epoch)
            pose = sample["gameplay"][native_dead_drop.FRAME]["transform"]
            await projected()
        else:
            break
    nearby = await wait_resident_status(host_run, lambda frame:
        (point := frame["game"]["pawns"].get("guest", {}).get("location"))
        and math.dist(point, pose["position"]) <= 240)
    point = nearby["game"]["pawns"]["guest"]["location"]
    await wait_resident_status(guest_run, lambda frame:
        (location := frame["game"]["pawns"]["local"].get("location")) and math.dist(location, point) <= 25
        and frame["game"].get("native_authored_pickup_sources", {}).get(drop.source, {}).get("state") == "available")
    before = save_fingerprints()
    await action(guest_run, native_authored_pickup.STOCK_FIXTURE, pickup_source=drop.source)
    collected = await wait_resident_status(host_run, lambda frame:
        drop.source in frame["gameplay"].get(native_dead_drop.PICKUPS, {}))
    claim = collected["gameplay"][native_dead_drop.PICKUPS][drop.source]
    await wait_resident_status(guest_run, lambda frame:
        native_dead_drop.observed(frame["game"], "hidden") or native_dead_drop.observed(frame["game"], "absent"))
    assert save_fingerprints() == before
    route = (drop.map, *reversed(via), BLACK_MARKET)
    for source, destination in zip(route, route[1:]):
        await cross(source, destination)

    before = save_fingerprints()
    await select(BLACK_MARKET, "turnin")
    finished = await wait_resident_status(host_run, lambda frame:
        frame["gameplay"].get("native_quests", {}).get(drop.quest, {}).get("step") == -10)
    result = finished["gameplay"]["native_quests"][drop.quest]
    consumed = result["consumed_guid" if generic_board else contract.consumed_field]
    assert (consumed, result["cash"], result["xp"]) == (claim["guid"], contract.cash if cash is None else cash, 1)
    assert result["actor"] == accepted["gameplay"]["native_quests"][drop.quest]["actor"] == claim["owner"]
    assert save_fingerprints() == before
    key = drop.source if generic_board else contract.key
    return {key: {"epoch": epoch, "pose": pose, "static_placement_seed": seed, "claim": claim["guid"], "cash": result["cash"],
                  "board": BOARDS[BLACK_MARKET], "project_reply_bytes": samples, "fixture_sight_refusals": failed_poses,
                  "renewed_and_reconnected": True, "no_save_during_turnin": True, "no_save_during_pickup": True}}
