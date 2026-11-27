"""Run the three stock Simmi jammer jobs from the installed Observatory title."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events
from tools.silverfish import native_inventory
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import current_status, submit
from tools.silverfish.native_flood_jammer import (
    BLACK_LAKE, CROSSROADS_JAMMER, FLOOD, OBSERVATORY_BOUNTY_BOARD, POSE, SIMMI_BOARD, same_pose,
)
from tools.silverfish.travel import CROSSROADS, LAKE, OBSERVATORY


def instrument_jammer_sampler(source):
    anchor = '        if ok and result == "pose" then\n'
    if source.count(anchor) != 1:
        raise ValueError("The native jammer pose anchor changed")
    return source.replace(anchor, anchor + """            local key = "SilverfishPortableJammerSampler:" .. profile.tag
            if not ModRef:GetSharedVariable(key) then
                local measured, evidence = pcall(function()
                    local address = current.world:GetAddress()
                    local hosting = phase == "hosting"
                    local function native_sample() return native_projection(profile.objective_world, address, hosting) end
                    local function reflected_sample()
                        local actor = assert(live_actor(current))
                        return assert(pose(actor))
                    end
                    local stock = reflected_sample()
                    local observed = {x, y, z, pitch, yaw, roll, sx, sy, sz}
                    for index = 1, 9 do
                        assert(stock[index] == observed[index], "pose field " .. index .. " differs")
                    end
                    assert(native_projection(profile.objective_world, address + 1, hosting) == nil, "wrong world accepted")
                    assert(native_sample() == "pose", "binding did not recover after the mismatched world")
                    local rounds = 512
                    local function measure(sample)
                        local started = os.clock()
                        for index = 1, rounds do sample() end
                        return (os.clock() - started) * 1000
                    end
                    local native_first, reflected_first = measure(native_sample), measure(reflected_sample)
                    local reflected_second, native_second = measure(reflected_sample), measure(native_sample)
                    return ("%d,%.3f,%.3f"):format(rounds * 2,
                        native_first + native_second, reflected_first + reflected_second)
                end)
                ModRef:SetSharedVariable(key, measured and evidence or "failed: " .. tostring(evidence))
            end
""")


async def verify_stock_simmi_jammers(host, guest, pawn, probe, *, flood_objective=True):
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
        observed["jammer_pose"] = game.get(POSE)
        raise RuntimeError(f"{run.parent.name} did not reach the expected resident state: {observed}")

    async def quest_on(seat, spec, step):
        def match(frame):
            return frame.get("gameplay", {}).get("native_quests", {}).get(spec.quest, {}).get("step") == step

        return (await state(seat, match))["gameplay"]["native_quests"][spec.quest]

    async def journal(expected, flood_step):
        async with asyncio.timeout(9):
            while True:
                values = await asyncio.gather(*(command(seat, "journal") for seat in peers))
                flood = await asyncio.gather(*(command(seat, "floodjournal") for seat in peers))
                if values == [expected] * 2 and flood == [str(flood_step)] * 2:
                    return {**dict(zip(journal_rows, expected.split(","))), FLOOD.quest: str(flood_step)}
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

    travel_receipts = []

    async def cross(source, destination, tag):
        nonlocal pawn
        route, = (route for route in AUTHORED_ROUTES
                  if (source, destination, tag) == (route.source, route.destination, route.entrance))
        assert route.kind == "overlap", route
        before = await state(host, lambda f: f["game"]["map"] == source
                             and f["game"].get("native_zone", {}).get("state") == "ready")
        fixture_timeout = None
        for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            for attempt in range(2):
                try:
                    await host.bridge.command("native-exit-position", participant, position, tag, route.actor)
                    break
                except TimeoutError as error:
                    current = await current_status(host_run)
                    if (attempt or position == "inside" or current["zone_epoch"] != before["zone_epoch"]
                            or current["game"]["map"] != source
                            or current["game"].get("native_zone", {}).get("state") != "ready"):
                        fixture_timeout = error
                        break
            if fixture_timeout:
                break
        arrived = await state(host, lambda f: f["game"]["map"] == destination
                              and f["zone_epoch"] != before["zone_epoch"] and not f.get("awaiting_zone_guest")
                              and f["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await state(guest, lambda f: f["game"]["map"] == destination and f.get("state") == "connected"
                    and f.get("zone_epoch") == arrived["zone_epoch"], timeout=35)
        if fixture_timeout:
            # A real overlap can unload the source before its test-only positioning receipt is read.
            proof = next((row for row in await resident_events(host_run, arrived)
                          if row["event"] == "native_authored_zone_request"
                          and (row["source"], row["destination"], row["entrance"], row["actor"]) ==
                          (source, destination, tag, route.actor)
                          and row["result"].get("ok") is True
                          and row["result"].get("travel_epoch") == arrived["zone_epoch"]), None)
            if not proof:
                raise fixture_timeout
            travel_receipts.append({"source": source, "destination": destination, "epoch": arrived["zone_epoch"]})
        pawn = arrived["game"]["native_guest_save"]["pawn"]
        return arrived["zone_epoch"]

    checkpoint = campaign_save_hashes(host.campaign)
    offers = await board(guest, physical=True)
    expected_offers = {*journal_rows, FLOOD.quest}
    assert expected_offers <= offers, offers
    await close(guest, physical=True)
    for seat, spec in ((guest, FLOOD), (guest, CROSSROADS_JAMMER), (host, BLACK_LAKE)):
        assert spec.quest in await board(seat)
        await accept(seat, spec)
        await asyncio.gather(*(quest_on(peer, spec, 1) for peer in peers))
        await close(seat)
    accepted_journal = await journal("1,1", 1)
    remaining = await board(guest, physical=True)
    assert remaining == offers - expected_offers, (offers, remaining)
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
        samples = {}
        for role, seat in (("host", host), ("guest", guest)):
            raw = await command(seat, "sampler")
            calls, native_ms, reflected_ms = map(float, raw.split(","))
            samples[role] = {"calls_per_path": int(calls), "native_ms": native_ms, "lua_ms": reflected_ms,
                             "all_pose_fields_matched": True, "wrong_world_refused": True}
        return {"host_pose": authority, "guest_pose": guest_pose, "sampler": samples}

    async def confirm_weapon(ammo):
        await state(guest, lambda f: f["game"].get("native_guest_primary_scope", {}).get("guid") == guid)
        weapon = (await action(guest, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert weapon["status"] == "equipped" and weapon["actor_ammo"] == ammo, weapon
        return weapon

    async def destroy_jammer(spec, magazine):
        before = campaign_save_hashes(host.campaign)
        guest_frame = await state(guest, lambda frame: frame.get("state") == "connected")
        shots = []
        for _ in range(3):
            aimed = await host.bridge.command(spec.command("aim"), pawn, guid, timeout=6)
            aim = aimed[f"{spec.key}_aim"]
            assert (aim["map"], aim["pawn"], aim["jammer"], aim["guid"]) == (
                spec.objective_map, pawn, spec.jammer, guid), aim
            await movement_arrived(guest, aimed["pawns"]["guest"]["location"])
            async with asyncio.timeout(5):
                while await command(guest, "aimjammer") != "the guest crosshair is on the jammer":
                    await asyncio.sleep(.1)
            started = time.time()
            sequence = await press_primary()
            async with asyncio.timeout(7):
                while True:
                    result = next((row for row in await resident_events(guest_run, guest_frame)
                                   if row.get("sequence") == sequence and row["time_unix"] >= started
                                   and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")), None)
                    if result:
                        break
                    await asyncio.sleep(.1)
            assert result["event"] == "native_guest_primary_input", result
            shot = result["native_guest_weapon"]
            assert (shot["before"], shot["after"], shot["projectiles"]) == (magazine, magazine - 1, 1), shot
            magazine = shot["after"]
            shots.append({"aim": aim, "shot": shot})
            try:
                async with asyncio.timeout(2.5):
                    objective = await quest_on(host, spec, 2)
                break
            except TimeoutError:
                continue
        else:
            raise AssertionError(f"The stock guest shots did not complete {spec.quest}: {shots}")
        assert objective["actor"] == guest_frame["character_id"], objective
        assert objective["objective"]["shooter_role"] == "guest" and objective["objective"]["shooter"] == pawn, objective
        assert await quest_on(guest, spec, 2) == objective
        await state(guest, lambda frame: frame.get("game", {}).get(f"{spec.key}_projection", {}).get("state")
                    in ("hidden", "absent"))
        assert campaign_save_hashes(host.campaign) == before, "Breaking a jammer wrote a save"
        return magazine, {"shots": shots, "objective": objective}

    outbound = await cross(OBSERVATORY, CROSSROADS, "Obsv_Etr")
    crossroads = await observe_jammer(CROSSROADS_JAMMER)
    async with asyncio.timeout(8):
        while await command(guest, "selectpistol") != "equipped":
            await asyncio.sleep(.14)
    before_travel = await confirm_weapon(7)
    magazine, flood_step = 7, 1
    flood_visits, flood_evidence = [], {}
    if flood_objective:
        flood_visits.append(await cross(CROSSROADS, FLOOD.objective_map, "CR_RF"))
        flood = await observe_jammer(FLOOD)
        await confirm_weapon(magazine)
        magazine, flood_completion = await destroy_jammer(FLOOD, magazine)
        flood_visits.append(await cross(FLOOD.objective_map, CROSSROADS, "CR_RF"))
        await confirm_weapon(magazine)
        flood_step = 2
        flood_evidence = {"flood_plains": flood, "flood_plains_completion": flood_completion}
    lake_out = await cross(CROSSROADS, LAKE, "Lake_Cave")
    lake = await observe_jammer(BLACK_LAKE)
    # The guest never presses a quick slot again. The restored weapon must be
    # selected in the host's detached pawn and available to the stock input.
    restored = await confirm_weapon(magazine)
    assert restored["pawn"] != before_travel["pawn"], restored
    magazine, lake_completion = await destroy_jammer(BLACK_LAKE, magazine)
    await journal("1,2", flood_step)
    lake_return = await cross(LAKE, CROSSROADS, "Lake_Cave")
    await observe_jammer(CROSSROADS_JAMMER)
    await confirm_weapon(magazine)
    magazine, crossroads_completion = await destroy_jammer(CROSSROADS_JAMMER, magazine)
    final_visit = await cross(CROSSROADS, OBSERVATORY, "Obsv_Etr")
    await confirm_weapon(magazine)
    completed_journal = await journal("2,2", flood_step)

    payout_checkpoint = campaign_save_hashes(host.campaign)
    wallet = (await state(host, lambda frame: type(frame.get("game", {}).get("native_wallet")) is int))["game"]["native_wallet"]
    guest_id = (await state(guest, lambda frame: frame.get("state") == "connected"))["character_id"]
    payouts = []
    turnins = [(host, CROSSROADS_JAMMER, "host"), (guest, BLACK_LAKE, guest_id)]
    if flood_objective:
        turnins.append((guest, FLOOD, guest_id))
    for seat, spec, owner in turnins:
        await board(seat)
        actual_board, _, ready = (await command(seat, "ready")).split(";")
        assert actual_board == SIMMI_BOARD and spec.quest in ready.split(","), ready
        if seat is host:
            assert await command(host, "turnincrossroads") == "clicked"
        else:
            pressed = await action(guest, "native-wire-board-stock-fixture", target=f"{spec.quest}|turnin")
            assert pressed["native_wire_board_stock"]["state"] == "redeeming", pressed
        paid, guest_paid = await asyncio.gather(*(quest_on(peer, spec, -10) for peer in peers))
        wallet += spec.cash
        assert paid == guest_paid and paid["actor"] == owner and set(paid["recipients"]) == {"host", guest_id}, paid
        assert (paid["cash"], paid["xp"], paid["wallet"]) == (spec.cash, 1, wallet), paid
        await asyncio.gather(*(state(peer, lambda frame: frame.get("game", {}).get("native_wallet") == wallet) for peer in peers))
        await close(seat)
        repeated = await action(guest, "native-quest-turnin", target=spec.quest)
        assert repeated["effect"] == "native_quest_already_settled" and repeated["wallet"] == wallet, repeated
        payouts.append(paid)
    final_journal = await journal("-10,-10", -10 if flood_objective else flood_step)
    await state(host, lambda frame: all(row.get("quest_xp") == len(turnins) and not row.get("native_quest_xp_pending")
                     for row in (frame.get("gameplay", {}).get("players", {}).get(role, {}) for role in ("host", "guest"))))
    assert campaign_save_hashes(host.campaign) == payout_checkpoint, "Stock turn-in wrote a save before the normal checkpoint"
    return {"stock_simmi_jammers": {
        "offered": sorted(offers), "remaining": sorted(remaining), "initial_journals": accepted_journal,
        "crossroads": crossroads, "lake": lake, **flood_evidence,
        "visits": [outbound, *flood_visits, lake_out, lake_return, final_visit],
        "visits_verified_after_fixture_timeout": travel_receipts,
        "weapon_before_travel": before_travel, "weapon_after_travel": restored,
        "objective_journals": completed_journal, "paid_journals": final_journal,
        "lake_completion": lake_completion, "crossroads_completion": crossroads_completion,
        "payouts": payouts, "shared_wallet": wallet, "no_gameplay_checkpoints": True,
    }}
