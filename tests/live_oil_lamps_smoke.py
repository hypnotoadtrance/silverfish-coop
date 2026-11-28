"""Check stock oil and gas lamps on one muted portable pair."""

import argparse
import asyncio
from contextlib import ExitStack
from math import dist
from pathlib import Path
import time

import live_portable_title_smoke as suite
from portable_dead_drop import allow_resident_contract_xp
from portable_simmi_jammer import accept_crossroads_dead_drop
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_guest_drop, native_loose_pickup, native_loot, native_stock_switch
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def lamp_id(area, number):
    return f"Prop_Lantern_01_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.Prop_Lantern_01_C_{number}"


async def verify_oil_lamps(host, guest, pawn, *, playtest_batch=False, lamps_only=False):
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

    async def verify_observer_open(area, suffix):
        crate, = [name for name, map_name in native_loot.CRATES.items()
                  if map_name == area and name.endswith(":PersistentLevel." + suffix)]
        host_frame = await suite.wait_resident_status(host_run, lambda f: f.get("game", {}).get("map") == area)
        host_pawn = host_frame["game"]["pawns"]["local"]["name"]
        approached = await host.bridge.command("native-loot-approach", pawn, crate)
        position, = [row["location"] for row in approached["pawns"].values() if row["name"] == pawn]
        await suite.wait_resident_status(guest_run, lambda frame:
            (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
            and dist(point, position) < 60, timeout=5)
        await host.bridge.command("native-loot-approach", host_pawn, crate)
        assert await probe(guest, "watch-lid", crate) == "watching"
        result = await suite.submit(host_run, "native-loot-open", target=crate)
        assert result["ok"], result
        async with asyncio.timeout(5):
            while (motion := await probe(guest, "lid-result", crate)) == "pending":
                await asyncio.sleep(.05)
        samples, safe, angle = motion.split(";")
        assert int(samples) >= 3 and safe == "true", motion
        return {"actor": crate, "intermediate_angles": int(samples), "collision_free_copy": True,
                "final_angle": float(angle)}

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
            assert await probe(host, "close-lighter", actor) == "false"
        assert await probe(guest, "visual-drift", actor) == "visual drift"
        async with asyncio.timeout(5):
            while (repaired := await probe(guest, "state", actor)).split(";")[:2] != ["true", "true"]:
                await asyncio.sleep(.05)
        place = tuple(map(float, (await probe(host, "stage", actor)).split(",")))
        await suite.wait_resident_status(guest_run, lambda frame:
            (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
            and dist(point, place) < 60, timeout=5)
        started = time.time()
        await probe(guest, "guest-toggle", actor)
        _, guest_extinguished = await verify_state(actor, False)
        assert (await probe(guest, "use-lighter", actor)).split(";")[0] == "true"
        try:
            replica, guest_lit = await verify_state(actor, True)
        finally:
            assert await probe(guest, "lower-lighter", actor) == "true;false"
            assert await probe(guest, "close-lighter", actor) == "false"
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
        assert await probe(host, "host-toggle", actor) == "false"
        _, host_extinguished = await verify_state(actor, False)
        assert suite.campaign_save_hashes(host.campaign) == checkpoint
        evidence[actor] = dict(host_lit=host_lit, guest_lit_after_visual_repair=repaired, guest_extinguished=guest_extinguished,
                               guest_lit=guest_lit, guest_lit_after_refresh=guest_lit_after_refresh,
                               host_extinguished=host_extinguished, receipts=receipts)

    async def verify_equip(item, inventory_source):
        seeded = await host.bridge.command("fixture-stock-take-ammo-seed", pawn, item)
        guid = seeded["ack"]["message"]
        async with asyncio.timeout(10):
            while True:
                before = await guest.bridge.command("fixture-stock-take-ammo-backpack", guid, update_status=False)
                if before["ack"]["message"] != "waiting":
                    break
                await asyncio.sleep(.1)
        selected = await suite.submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=guid)
        assert selected["ok"], (item, selected)
        await asyncio.sleep(.4)
        inventory = (await suite.submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
        stored, = [row for row in inventory if row["guid"] == guid]
        assert stored["source"] == inventory_source, stored
        return stored["class_path"], guid

    async def visit_crossroads():
        await enter_crossroads(host, pawn)
        await suite.wait_resident_status(host_run, lambda frame:
            frame.get("game", {}).get("map") == CROSSROADS and not frame.get("awaiting_zone_guest")
            and frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        await suite.wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS)

    async def verify_crossroads_lamp():
        adjacent = lamp_id(CROSSROADS, 3)
        await verify_state(adjacent, False)
        await verify_one(lamp_id(CROSSROADS, 1))
        await verify_state(adjacent, False)
        return adjacent

    debt_evidence = []
    for balance in ("3000000", "2000000"):
        assert await suite.portable_probe_command(host.bridge, "portable-oil-lamps", f"debt-set\t{balance}") == balance
        async with asyncio.timeout(5):
            while await suite.portable_probe_command(guest.bridge, "portable-oil-lamps", "debt-read\t0") != balance:
                await asyncio.sleep(.08)
        debt_evidence.append(int(balance))

    gas_lamps = [
        f"Prop_GassLight_{variant}_C {OBSERVATORY}.{OBSERVATORY.rsplit('/', 1)[1]}"
        f":PersistentLevel.Prop_GassLight_{variant}_C_1" for variant in ("01", "02")
    ]
    checkpoint = suite.campaign_save_hashes(host.campaign)
    gas_evidence = {}
    for actor, other in zip(gas_lamps, reversed(gas_lamps), strict=True):
        _, initial = await verify_state(actor, False)
        assert await probe(host, "host-toggle", actor) == "true"
        _, host_lit = await verify_state(actor, True)
        await verify_state(other, False)
        assert await probe(guest, "visual-drift", actor) == "visual drift"
        async with asyncio.timeout(5):
            while (repaired := await probe(guest, "state", actor)).split(";")[:2] != ["true", "true"]:
                await asyncio.sleep(.05)
        # The gas lamps sit against furniture. Exercise the guest's bound command
        # without moving the camera to a fixture-specific approach.
        off_receipt = await host.bridge.command(native_stock_switch.ACTION, actor, pawn, update_status=False)
        assert off_receipt["ack"]["message"] == "stock switch interaction dispatched"
        _, off = await verify_state(actor, False)
        on_receipt = await host.bridge.command(native_stock_switch.ACTION, actor, pawn, update_status=False)
        assert on_receipt["ack"]["message"] == "stock switch interaction dispatched"
        _, on = await verify_state(actor, True)
        assert await probe(host, "host-toggle", actor) == "false"
        _, host_off = await verify_state(actor, False)
        gas_evidence[actor] = dict(initial=initial, host_lit=host_lit, guest_visual_repair=repaired,
                                   guest_off=off, guest_on=on, host_off=host_off)
    assert suite.campaign_save_hashes(host.campaign) == checkpoint
    print("Shared debt and both Observatory gas lamp states verified", flush=True)
    if not playtest_batch:
        await verify_one(lamp_id(OBSERVATORY, 1))
    if lamps_only:
        await visit_crossroads()
        adjacent = await verify_crossroads_lamp()
        return {"gas_lamps": gas_evidence, "lamps": evidence, "other_lamp_stayed_unlit": adjacent}
    cabinet = await verify_observer_open(OBSERVATORY, "LootCrate_Small_BP_C_1")
    print("Guest watched the stock cabinet move through intermediate angles", flush=True)
    async def reputation(seat, action="read"):
        return await suite.portable_probe_command(seat.bridge, "portable-oil-lamps", f"reputation\t{action}")

    async def expect_guest_reputation(expected):
        async with asyncio.timeout(12):
            while await reputation(guest) != expected:
                await asyncio.sleep(.15)

    baseline = await reputation(host, "seed")
    await expect_guest_reputation(baseline)
    cap_path, cap_guid = await verify_equip("fur-cap", "helmet")
    assert await reputation(guest) == baseline
    host_cap = await reputation(host, "wear")
    assert host_cap != baseline
    await expect_guest_reputation(host_cap)
    flashbang_path, _ = await verify_equip("flashbang", "equipment")
    gear = {"fur-cap": cap_path, "flashbang": flashbang_path}

    async def expect_disguise(expected):
        async with asyncio.timeout(10):
            while (await reputation(guest, "disguise"), await reputation(host, "peer_disguise")) != (expected, expected):
                await asyncio.sleep(.15)
        assert await reputation(host, "disguise") == "0"

    await expect_disguise("0")
    await verify_equip("conscript-uniform", "armor")
    await expect_disguise("0")
    await verify_equip("standard-infantry-helmet", "helmet")
    await expect_disguise("4")
    restored = await suite.submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=cap_guid)
    assert restored["ok"], restored
    await expect_disguise("0")
    print("Fur Cap and Flashbang stayed equipped after reconciliation", flush=True)
    simmi = await accept_crossroads_dead_drop(host, guest, suite.portable_probe_command, by_host=True)
    print("Both journals received Simmi's Crossroads Dead Drop", flush=True)
    await visit_crossroads()
    unloaded = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.SilverfishUnloadedLootFixture_C_1"
    for _ in range(3):
        check = await host.bridge.command(native_loose_pickup.CHECK, unloaded)
        assert check[native_loose_pickup.CHECK_KEY] == {unloaded: False}, check.get("ack")
    print("Crossroads host repeatedly refused an unloaded loot source", flush=True)
    adjacent = None if playtest_batch else await verify_crossroads_lamp()
    latch = await suite.verify_stock_crossroads_latch(host, guest, suite.portable_probe_command)
    dumpster = await verify_observer_open(CROSSROADS, "Loot_Dumpster_BP_C_1")
    return {"debt": debt_evidence, "gear": gear, "shared_reputation": {"baseline": baseline, "host_cap": host_cap},
            "guest_disguise": [0, 4, 0],
            "simmi": simmi, "unloaded_source_refused": unloaded, "cabinet": cabinet, "dumpster": dumpster, "gas_lamps": gas_evidence, "lamps": evidence,
            "other_lamp_stayed_unlit": adjacent, "latch": latch}


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
            device = game.get(native_stock_switch.KEY, {}).get(name)
            if device is not None:
                return native_stock_switch.state_payload(device)

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
    assert await probe(host, "reader", actor) == str(int(original["powered"]))
    assert await probe(host, "host", actor) == "host pressed"
    await suite.wait_resident_status(host_run, lambda frame:
        (found := row(frame, actor)) and found["powered"] != original["powered"])
    await asyncio.sleep(.6)  # The stock lever is still moving when its power state first changes.
    on_host = await observe(host_run, actor)
    assert on_host["powered"] != original["powered"]
    assert await load(guest) == actor
    try:
        assert await observe(guest_run, actor, equal=on_host) == on_host
    except RuntimeError as error:
        host_now = row(await suite.wait_resident_status(host_run, lambda frame: row(frame, actor)), actor)
        guest_now = row(await suite.wait_resident_status(guest_run, lambda frame: row(frame, actor)), actor)
        raise AssertionError(f"Late streamed switch expected {on_host}, host {host_now}, guest {guest_now}") from error
    assert await probe(guest, "reader", actor) == str(int(on_host["powered"]))

    assert await probe(guest, "hide") == "hide"
    await suite.wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == OBSERVATORY
                                     and row(frame, actor) is None)
    assert await probe(guest, "reader", actor) == "refused"
    assert await probe(host, "host", actor) == "host pressed"
    restored = await observe(host_run, actor, equal=original)
    assert await probe(guest, "show") == "show"
    assert await observe(guest_run, actor, equal=restored) == restored
    assert await probe(guest, "reader", actor) == str(int(restored["powered"]))

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
    try:
        async with asyncio.timeout(6):
            while not any(event.get("time_unix", 0) >= pressed
                          and event.get("native_stock_switch_interaction", {}).get("actor") == actor
                          for event in await suite.resident_events(guest_run, frame)):
                await asyncio.sleep(.08)
    except TimeoutError as error:
        events = [event for event in await suite.resident_events(guest_run, frame)
                  if event.get("time_unix", 0) >= pressed
                  and ("interaction" in event.get("event", "") or "switch" in event.get("event", ""))]
        raise AssertionError(f"Streamed guest switch changed both views without a receipt. "
                             f"Input fixture returned {guest_press}. Guest events {events}") from error
    assert await probe(host, "return-guest", actor) == "guest returned"
    assert await probe(guest, "unload") == "unload"
    assert await probe(host, "unload") == "unload"
    for run in (guest_run, host_run):
        await suite.wait_resident_status(run, lambda frame: frame.get("game", {}).get("map") == OBSERVATORY
                                         and row(frame, actor) is None)
    for seat in (host, guest):
        assert await probe(seat, "reader", actor) == "refused"
    assert suite.campaign_save_hashes(host.campaign) == checkpoint
    return {"actor": actor, "linked_lights": len(original["lights"]), "late_load": on_host,
            "reshown": restored, "guest_input": guest_press, "guest_press": on_host,
            "native_reader_verified": True, "unloaded_on_both": True, "checkpoint_unchanged": True}


async def exercise(work, first, second, saved, streamed_switch=False, crossroads_profile=False, playtest_batch=False,
                   lamps_only=False):
    with ExitStack() as fixtures:
        for game in (first, second):
            fixtures.enter_context(suite.installed_probe(game, mod="SilverfishPortableOilLampProbe",
                                                         fixture="portable_oil_lamp_probe.lua"))
            if not lamps_only:
                fixtures.enter_context(suite.installed_probe(game, mod="SilverfishPortableSimmiJobsProbe",
                                                             fixture="simmi_jobs_probe.lua"))
                fixtures.enter_context(suite.stock_board_approach_overlay(game))
                fixtures.enter_context(suite.portable_script_overlay(game, "native_computer_board.lua",
                                                                     allow_resident_contract_xp))
        async def power_checks(host, guest, pawn):
            return await verify_oil_lamps(host, guest, pawn, playtest_batch=playtest_batch, lamps_only=lamps_only)
        await suite.verify(work, first, second, saved,
                           crossroads_profile=crossroads_profile, stock_interaction_feedback=True, stock_placed_item=playtest_batch,
                           oil_lamps_check=power_checks,
                           streamed_switch_check=verify_streamed_switch if streamed_switch else None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--streamed-switch", action="store_true")
    parser.add_argument("--crossroads-profile", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--playtest-batch", action="store_true", help="Skip the separately verified stock oil-lantern route")
    scope.add_argument("--lamps-only", action="store_true", help="Check lamps on both maps without the gear and quest checks")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                         args.vanilla_saved.resolve(), args.streamed_switch, args.crossroads_profile, args.playtest_batch,
                         args.lamps_only))
