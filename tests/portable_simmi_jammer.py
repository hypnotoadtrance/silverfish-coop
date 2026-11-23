"""Run both stock Simmi jammer jobs from the installed Observatory title."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from tools.silverfish import native_inventory
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import current_status, submit
from tools.silverfish.native_flood_jammer import (
    BLACK_LAKE, CROSSROADS_JAMMER, OBSERVATORY_BOUNTY_BOARD, POSE, SIMMI_BOARD, same_pose,
)
from tools.silverfish.travel import CROSSROADS, LAKE, OBSERVATORY


async def verify_stock_simmi_jammers(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    peers = host, guest
    journal_rows = (CROSSROADS_JAMMER.quest, BLACK_LAKE.quest)

    async def command(seat, operation):
        return await probe(seat.bridge, "simmi-jobs", operation)

    async def action(seat, operation, **fields):
        result = await submit(host_run if seat is host else guest_run, operation, **fields)
        assert result["ok"], (operation, fields, result)
        return result

    async def press_primary():
        async with asyncio.timeout(7):
            while True:
                result = await submit(guest_run, "native-guest-primary-input-fixture")
                if result.get("ok"):
                    return result["native_guest_primary_fixture"]["sequence"]
                assert result.get("error") == "Stock primary input is temporarily blocked by the equipped player's state", result
                await asyncio.sleep(.15)

    async def state(seat, predicate, timeout=12):
        run = host_run if seat is host else guest_run
        deadline = time.monotonic() + timeout
        frame = {}
        while time.monotonic() < deadline:
            try:
                frame = await current_status(run)
            except (FileNotFoundError, RuntimeError):
                await asyncio.sleep(.2)
                continue
            if predicate(frame):
                return frame
            if frame.get("state") == "failed":
                break
            await asyncio.sleep(.2)
        game = frame.get("game", {})
        observed = {key: game.get(key) for key in (
            "map", "state", "native_guest_save", "native_guest_equipment_selection",
            "native_guest_primary_scope", "native_guest_equipped_weapons",
        )}
        observed["quests"] = frame.get("gameplay", {}).get("native_quests")
        raise RuntimeError(f"{run.parent.name} did not reach the expected resident state: {observed}")

    async def quest_on(seat, spec, step):
        def match(frame):
            return frame.get("gameplay", {}).get("native_quests", {}).get(spec.quest, {}).get("step") == step

        return (await state(seat, match))["gameplay"]["native_quests"][spec.quest]

    async def journal(expected):
        async with asyncio.timeout(9):
            while True:
                values = await asyncio.gather(*(command(seat, "journal") for seat in peers))
                if values == [expected] * 2:
                    return dict(zip(journal_rows, expected.split(",")))
                await asyncio.sleep(.14)

    async def movement_arrived(seat, location):
        def arrived(frame):
            point = frame.get("game", {}).get("pawns", {}).get("local", {}).get("location")
            return point and math.dist(point, location) <= 25

        await state(seat, arrived)

    async def board(seat, *, physical=False):
        assert not physical or seat is guest
        approach = "board" if physical else "host" if seat is host else "guest"
        position = tuple(map(float, (await command(host, approach)).split(",")))
        await movement_arrived(seat, position)
        name = OBSERVATORY_BOUNTY_BOARD if physical else SIMMI_BOARD
        if physical:
            await action(guest, "native-wire-board-stock-f-fixture", target=name)
        else:
            identity = await command(seat, "talk")
        await state(seat, lambda frame: (
            (menu := frame.get("game", {}).get("native_wire_board_stock", {}))
            and (menu.get("board"), menu.get("state")) == (name, "visible")
        ))
        actual, menu, choices = (await command(seat, "read")).split(";")
        assert actual == name and (physical or identity == menu), (actual, menu)
        return set(filter(None, choices.split(",")))

    async def close(seat, *, physical=False):
        assert await command(seat, "close" if physical else "closesimmi") == "closed"
        await state(seat, lambda frame: not frame.get("game", {}).get("native_wire_board_stock"))

    async def accept(seat, spec):
        if seat is host:
            assert spec == BLACK_LAKE and await command(host, "acceptlake") == "clicked"
            return
        result = await action(seat, "native-wire-board-stock-fixture", target=f"{spec.quest}|start")
        proof = result["native_wire_board_stock"]
        assert (proof["board"], proof["quest"], proof["state"]) == (SIMMI_BOARD, spec.quest, "starting"), proof

    async def cross(source, destination, tag):
        nonlocal pawn
        route, = (route for route in AUTHORED_ROUTES
                  if (source, destination, tag) == (route.source, route.destination, route.entrance))
        assert route.kind == "overlap", route
        before = await state(host, lambda f: f["game"]["map"] == source
                             and f["game"].get("native_zone", {}).get("state") == "ready")
        for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, position, tag, route.actor)
        arrived = await state(host, lambda f: f["game"]["map"] == destination
                              and f["zone_epoch"] != before["zone_epoch"] and not f.get("awaiting_zone_guest")
                              and f["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await state(guest, lambda f: f["game"]["map"] == destination and f.get("state") == "connected"
                    and f.get("zone_epoch") == arrived["zone_epoch"], timeout=35)
        pawn = arrived["game"]["native_guest_save"]["pawn"]
        return arrived["zone_epoch"]

    checkpoint = campaign_save_hashes(host.campaign)
    offers = await board(guest, physical=True)
    assert set(journal_rows) <= offers, offers
    await close(guest, physical=True)
    for seat, spec in ((guest, CROSSROADS_JAMMER), (host, BLACK_LAKE)):
        assert spec.quest in await board(seat)
        await accept(seat, spec)
        await asyncio.gather(*(quest_on(peer, spec, 1) for peer in peers))
        await close(seat)
    accepted_journal = await journal("1,1")
    remaining = await board(guest, physical=True)
    assert remaining == offers - set(journal_rows), (offers, remaining)
    await close(guest, physical=True)
    assert campaign_save_hashes(host.campaign) == checkpoint, "Opening or accepting a bounty wrote a save"

    guid = await command(host, "seedpistol")
    stored = await action(guest, native_inventory.STORE_EQUIPMENT, target=guid)
    assert stored[native_inventory.STORE_KEY]["guid"] == guid, stored
    async def observe_jammer(spec):
        watched = spec.key + "_objective"
        await state(host, lambda f: (
            (target := f["game"].get(watched, {})).get("state") == "watching"
            and target.get("jammer") == spec.jammer and target.get("trigger") == spec.trigger
        ))
        authority = (await state(host, lambda f: f["game"].get(POSE, {}).get("actor") == spec.jammer))["game"][POSE]
        guest_pose = (await state(guest, lambda f: (
            (pose := f["game"].get(POSE, {})).get("actor") == spec.jammer
            and same_pose(authority["transform"], pose["transform"])
        )))["game"][POSE]
        return {"host_pose": authority, "guest_pose": guest_pose}

    async def confirm_weapon(ammo):
        await state(guest, lambda f: f["game"].get("native_guest_primary_scope", {}).get("guid") == guid)
        weapon = (await action(guest, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert weapon["status"] == "equipped" and weapon["actor_ammo"] == ammo, weapon
        return weapon

    outbound = await cross(OBSERVATORY, CROSSROADS, "Obsv_Etr")
    crossroads = await observe_jammer(CROSSROADS_JAMMER)
    async with asyncio.timeout(8):
        while await command(guest, "selectpistol") != "equipped":
            await asyncio.sleep(.14)
    before_travel = await confirm_weapon(7)
    lake_out = await cross(CROSSROADS, LAKE, "Lake_Cave")
    lake = await observe_jammer(BLACK_LAKE)
    # The guest never presses a quick slot again. The restored weapon must be
    # selected in the host's detached pawn and available to the stock input.
    restored = await confirm_weapon(7)
    assert restored["pawn"] != before_travel["pawn"], restored
    checkpoint = campaign_save_hashes(host.campaign)
    shot = await press_primary()
    await state(guest, lambda f: f.get("gameplay", {}).get("native_equipped_weapon", {}).get("rounds") == 6)
    assert campaign_save_hashes(host.campaign) == checkpoint, "Firing the restored weapon wrote a save"
    lake_return = await cross(LAKE, CROSSROADS, "Lake_Cave")
    final_visit = await cross(CROSSROADS, OBSERVATORY, "Obsv_Etr")
    await confirm_weapon(6)
    return {"stock_simmi_jammers": {
        "offered": sorted(offers), "remaining": sorted(remaining), "initial_journals": accepted_journal,
        "crossroads": crossroads, "lake": lake, "visits": [outbound, lake_out, lake_return, final_visit],
        "weapon_before_travel": before_travel, "weapon_after_travel": restored,
        "restored_weapon_primary_sequence": shot, "campaign_checkpoint_unchanged_during_shot": True,
    }}
