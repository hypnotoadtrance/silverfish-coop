"""Verify stock shovel input and its physical results on both co-op peers."""

import asyncio
import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import (
    native_board_contract,
    native_computer_board as board,
    native_guest_weapon,
    native_host_dig,
    native_host_dig_live as dig,
    native_loose_pickup as loose,
    native_stock_enemy_drops,
    native_wire_board,
    native_world_loot,
    runtime,
)
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import current_status, submit
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, LAKE

SPADE = CROSSROADS + ".CrossRoads_P:PersistentLevel.MeleeWep_Spade_C_3"
NUGGET = "/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C"


async def stock_inventory(run, *, guest=False):
    items, offset, total = [], 0, None
    while offset is not None:
        page_request = {"target": str(offset)} if guest else {"amount": offset}
        reply = await submit(run, "native-inventory-list", **page_request)
        assert reply["ok"], reply
        page = reply["native_inventory"]
        assert page["offset"] == offset and (total is None or page["total"] == total), page
        total = page["total"]
        items.extend(page["items"])
        offset = page["next_offset"]
    assert len(items) == total and len({row["guid"] for row in items}) == total, items
    return items


def sealed_packages(items):
    return [row for row in items if row["class_path"] == native_host_dig.PACKAGE_CLASS]


async def verify_stock_shovel_reconnect(host, guest, evidence):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
                               and frame.get("gameplay_fresh") is True
                               and frame.get("character_id") == evidence["collector"]
                               and frame.get("game", {}).get("map") == CROSSROADS, timeout=20)
    packages = sealed_packages(await stock_inventory(guest_run, guest=True))
    assert packages == [evidence["guest_inventory_item"]], packages
    evidence["same_guest_inventory_item_after_reconnect"] = True

    character = evidence["collector"]

    async def action(run, command, **fields):
        reply = await submit(run, command, **fields)
        assert reply["ok"], reply
        return reply

    for source, destination in ((CROSSROADS, LAKE), (LAKE, BLACK_MARKET)):
        route, = [row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination)]
        prior = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == source
            and frame["game"].get("native_zone", {}).get("state") == "ready"
            and frame["game"].get("native_guest_save", {}).get("state") == "bound")
        pawn = prior["game"]["native_guest_save"]["pawn"]
        if route.kind == "door":
            for actor in (pawn, "local"):
                await host.bridge.command("native-door-position", actor, "near", route.entrance)
            await host.bridge.command("native-door-probe", "local", route.entrance)
        else:
            for actor, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                await host.bridge.command("native-exit-position", actor, placement, route.entrance, route.actor)
        arrived = await wait_resident_status(host_run, lambda frame:
            frame.get("game", {}).get("map") == destination and not frame.get("awaiting_zone_guest")
            and frame.get("zone_epoch") != prior["zone_epoch"]
            and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
            and frame.get("character_id") == character and frame.get("game", {}).get("map") == destination,
            timeout=35)

    contract = board.MISSING_CONTACT
    board_actor = board.BOARDS[BLACK_MARKET]

    async def open_board():
        await action(guest_run, native_wire_board.STOCK_F_FIXTURE, target=board_actor)
        await wait_resident_status(guest_run, lambda frame:
            (entry := frame.get("game", {}).get("native_wire_board_stock", {})).get("map") == BLACK_MARKET
            and entry.get("board") == board_actor and entry.get("state") == "visible")

    async def press(verb, state):
        pressed = await submit(guest_run, native_wire_board.STOCK_FIXTURE, target=f"{contract.quest_id}|{verb}")
        if not pressed["ok"]:
            catalog = await submit(guest_run, native_board_contract.CATALOG, target=board_actor)
            raise AssertionError((verb, pressed, catalog))
        result = pressed["native_wire_board_stock"]
        assert (result["board"], result["quest"], result["state"]) == (board_actor, contract.quest_id, state), result

    def quest(frame):
        return frame.get("gameplay", {}).get("native_quests", {}).get(contract.quest_id, {})

    async def step(value):
        frame = await wait_resident_status(host_run, lambda frame: quest(frame).get("step") == value)
        return quest(frame)

    saves = campaign_save_hashes(host.campaign)
    before = await wait_resident_status(host_run, lambda frame: type(frame.get("game", {}).get("native_wallet")) is int)
    expected_wallet = before["game"]["native_wallet"] + contract.cash
    await host.bridge.command(contract.command("approach"), arrived["game"]["native_guest_save"]["pawn"])
    await open_board()
    await press("start", "starting")
    accepted = await step(1)
    await wait_resident_status(guest_run, lambda frame: not frame.get("game", {}).get("native_wire_board_stock"))
    await open_board()
    await press("turnin", "redeeming")
    completed = await step(-10)
    assert (accepted["actor"], completed["actor"], completed["objective_owner"],
            completed[contract.consumed_field], completed["cash"], completed["xp"]) == (
                character, character, character, packages[0]["guid"], 450, 1), completed
    assert set(completed["recipients"]) == {"host", character}, completed
    assert completed["wallet"] == expected_wallet, completed
    await wait_resident_status(guest_run, lambda frame: quest(frame) == completed
                               and frame.get("game", {}).get("native_wallet") == expected_wallet)
    assert not sealed_packages(await stock_inventory(guest_run, guest=True))
    replay = await action(guest_run, "native-quest-turnin", target=contract.quest_id)
    assert replay["effect"] == "native_quest_already_settled" and replay["wallet"] == expected_wallet, replay
    assert campaign_save_hashes(host.campaign) == saves, "The board wrote a checkpoint during quest settlement"
    evidence["stock_board_turn_in"] = completed


async def verify_stock_shovel(host, guest, pawn, *, loot=False, quest=False, host_quest=False):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    current = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if current["game"].get("map") != CROSSROADS:
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obsv_Etr", "Level_Exit_BP_C_1")
    arrived = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("map") == CROSSROADS
                                        and not f.get("awaiting_zone_guest")
                                        and f["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
    await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("map") == CROSSROADS
                                     and f.get("state") == "connected", timeout=35)
    pawn = arrived["game"]["native_guest_save"]["pawn"]
    await host.bridge.command(loose.APPROACH, SPADE, pawn)
    target = f"loose:{SPADE}"
    tapped = await submit(guest_run, loose.FIXTURE, target=target)
    assert tapped["ok"], tapped
    await wait_resident_status(guest_run, lambda f: SPADE in f.get("gameplay", {}).get(loose.STATE, []), timeout=12)
    inventory = await submit(guest_run, "native-inventory-list")
    assert inventory["ok"], inventory
    item, = [entry for entry in inventory["native_inventory"]["items"] if entry["class_path"] == native_guest_weapon.SPADE_PATH]
    equipped = await submit(guest_run, "native-guest-weapon-equip", target=item["guid"])
    assert equipped["ok"] and equipped["native_guest_weapon"]["kind"] == "spade", equipped
    current = await wait_resident_status(
        guest_run, lambda frame: frame.get("game", {}).get("native_guest_primary_scope", {}).get("kind") == "spade",
        timeout=10,
    )

    async def observe(seat, action, *args):
        report = await seat.bridge.command("fixture-spade-" + action, *args, update_status=False)
        return report["ack"]["message"]

    async def dig_once(stock_choice=None, *, advance=False, host_dig=False, quest_dig=False):
        previous = await asyncio.gather(observe(host, "holes"), observe(guest, "holes"))
        previous = [set(text.split(";")) - {"none"} for text in previous]
        digger = "local" if host_dig else pawn
        if quest_dig:
            staged, site = (await observe(host, "quest-approach", digger)).split(";", 1)
        else:
            staged = await observe(host, "aim", digger, "next" if advance else "find")
        # Keep the observing guest inside the stock actor's replication range.
        if host_dig:
            if quest_dig:
                staged, _ = (await observe(host, "quest-approach", pawn, "observer")).split(";", 1)
            else:
                staged = await observe(host, "aim", pawn, "watch-host")
        expected = tuple(map(float, staged.split(",")))
        await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("pawns", {}).get("local", {}).get("location")
                                         and math.dist(f["game"]["pawns"]["local"]["location"], expected) <= 40, timeout=8)
        if quest_dig:
            contacts = [await observe(host, "quest-aim", digger, site)]
            if not host_dig:
                contacts.append(await observe(guest, "quest-aim", site))
            assert all(math.dist(tuple(map(float, point.split(","))), native_host_dig.SITE) <= 45 for point in contacts)
        elif host_dig:
            contacts = [await observe(host, "aim", digger)]
        else:
            contacts = await asyncio.gather(observe(host, "aim", digger), observe(guest, "aim"))
        if stock_choice:
            await observe(host, "loot", stock_choice)
        if host_dig:
            dispatch = await observe(host, "host-press", digger, *(["keep-aim"] if quest_dig else []))
            assert dispatch == ("pressed" if quest_dig else "pressed and released"), dispatch
        else:
            tap = await submit(guest_run, "native-guest-primary-input-fixture")
            assert tap["ok"], tap
            sequence = tap["native_guest_primary_fixture"]["sequence"]
            async with asyncio.timeout(8):
                while True:
                    events = [row for row in await resident_events(guest_run, current)
                              if row.get("sequence") == sequence and row["event"].startswith("native_guest_dig_input")]
                    if events:
                        break
                    await asyncio.sleep(.08)
            event, = events
            assert event["event"] == "native_guest_dig_input", event
            dispatch = event[dig.GUEST_RESULT]
        if quest_dig:
            # The buried-item branch can emit its package without leaving an ordinary terrain hole.
            return {"contact": contacts, "dispatch": dispatch, "guid": item["guid"]}
        additions = []
        try:
            async with asyncio.timeout(10):
                while True:
                    observed = await asyncio.gather(observe(host, "holes"), observe(guest, "holes"))
                    additions = [set(text.split(";")) - base - {"none"} for text, base in zip(observed, previous)]
                    if all(additions):
                        break
                    await asyncio.sleep(.1)
        except TimeoutError as error:
            raise AssertionError(f"No paired stock hole for {digger} at {contacts}. Observed additions {additions}.") from error
        (host_hole,), (guest_hole,) = additions
        assert host_hole.startswith("3,") and guest_hole.startswith("1,"), additions
        assert math.dist(tuple(map(float, host_hole.split(",")[1:])), tuple(map(float, guest_hole.split(",")[1:]))) < 5
        result = {"holes": [host_hole, guest_hole], "contact": contacts, "dispatch": dispatch}
        if not host_dig:
            result["guid"] = item["guid"]
        return result

    async def verify_host_dig():
        checkpoint = campaign_save_hashes(host.campaign)
        assert await observe(host, "host-equip", "local") == "equipped"
        result = await dig_once(advance=True, host_dig=True)
        assert checkpoint == campaign_save_hashes(host.campaign), "An ordinary host dig wrote a checkpoint"
        return result

    if quest:
        ordinary = await dig_once()
        host_dig = await verify_host_dig()
        result = await dig_once(quest_dig=True, host_dig=host_quest)
        digger_guid = item["guid"]
        if host_quest:
            equipped_spade, = [item for item in await stock_inventory(host_run)
                               if item["class_path"] == native_guest_weapon.SPADE_PATH]
            digger_guid = equipped_spade["guid"]
        result["digger"] = "host" if host_quest else "guest"
        try:
            settled = await wait_resident_status(
                host_run, lambda f: f.get("game", {}).get(dig.KEY, {}).get("state") == "escrowed", timeout=10)
        except RuntimeError as error:
            game = (await current_status(host_run)).get("game", {})
            packages = await asyncio.gather(*(observe(seat, "quest-packages") for seat in (host, guest)))
            raise AssertionError({"dig": game.get(dig.KEY), "diagnostic": game.get(dig.DIAGNOSTIC_KEY),
                                  "stock_packages": packages, "input": result}) from error
        finally:
            if host_quest:
                assert await observe(host, "host-release", "local") == "released"
        package = settled["game"][dig.KEY]
        saved = runtime.read_json(host.campaign / "save" / "campaign.json")
        custody = saved["world"]["party"][dig.CUSTODY]
        assert (package["spade_guid"], custody["spade_guid"]) == (digger_guid, digger_guid)
        assert (custody["source"], custody["flag_value"], custody["package_guid"]) == (
            native_host_dig.SOURCE, 1, package["escrow_guid"])
        identity = dig.package_identity(custody["package_guid"])

        def projected(frame):
            return frame.get("game", {}).get(native_world_loot.KEY, {}).get(identity)

        shown = await wait_resident_status(guest_run, projected, timeout=12)
        replica = projected(shown)
        raw_packages = await asyncio.gather(*(observe(seat, "quest-packages") for seat in (host, guest)))
        assert raw_packages == [package["actor"], replica["actor"]], raw_packages
        assert (replica["map"], replica["class_path"]) == (CROSSROADS, native_host_dig.PACKAGE_CLASS), replica
        host_before = await stock_inventory(host_run)
        guest_before = await stock_inventory(guest_run, guest=True)
        assert not sealed_packages(host_before) and not sealed_packages(guest_before)
        press = await submit(guest_run, native_world_loot.FIXTURE, target=identity)
        if not press["ok"]:
            sight = await observe(guest, "sight", replica["actor"].split(" ", 1)[1])
            raise AssertionError((press, sight))
        assert press[native_world_loot.KEY][identity]["sequence"] > replica["sequence"], press
        collected = await wait_resident_status(host_run, lambda f: f.get("game", {}).get(dig.KEY, {}).get("state") == "owned")
        owned = collected["game"][dig.KEY]
        assert (owned["escrow_guid"], owned["spade_guid"], owned["collector"]) == (
            package["escrow_guid"], digger_guid, pawn), owned
        saved = runtime.read_json(host.campaign / "save" / "campaign.json")
        owner = saved["world"]["party"][dig.CUSTODY]
        collector = shown["character_id"]
        assert (owner["state"], owner["package_guid"], owner["owner"]) == ("owned", owned["package_guid"], collector), owner
        await wait_resident_status(guest_run, lambda frame: not projected(frame), timeout=10)
        repeated = await submit(guest_run, native_world_loot.FIXTURE, target=identity)
        assert not repeated["ok"], repeated
        host_after = await stock_inventory(host_run)
        guest_after = await stock_inventory(guest_run, guest=True)
        assert host_after == host_before, (host_before, host_after)
        guest_package, = sealed_packages(guest_after)
        assert (guest_package["guid"], guest_package["source"], guest_package["quantity"]) == (
            owned["package_guid"], "backpack", 1), guest_package
        check = "stock_missing_contact_host_dig" if host_quest else "stock_missing_contact_guest_dig"
        return {"stock_shovel": ordinary, "stock_host_shovel": host_dig,
                check: {**result, "package": package, "custody": custody,
                                                     "world_actors_before_pickup": raw_packages, "guest_pickup": owned,
                                                     "collector": collector, "guest_inventory_item": guest_package,
                                                     "host_inventory_preserved": True, "second_pickup_refused": True}}

    if not loot:
        return {"stock_shovel": await dig_once(), "stock_host_shovel": await verify_host_dig()}

    checkpoint = campaign_save_hashes(host.campaign)
    host_status = await wait_resident_status(host_run, lambda f: f.get("state") == "hosting")
    assert host_status["game"][native_stock_enemy_drops.MEMORY_CAPABILITY]

    def nuggets(frame):
        return [entry for entry in frame.get("game", {}).get(native_stock_enemy_drops.KEY, {}).values()
                if entry["source"].startswith("DigHole_BP_C ") and entry["class_path"] == NUGGET]

    dig_result = await dig_once("item")
    found = await wait_resident_status(host_run, nuggets, timeout=8)
    nugget, = nuggets(found)
    async with asyncio.timeout(12):
        while True:
            rewards = [row for row in await resident_events(host_run, found)
                       if row["event"] == "native_stock_enemy_loot_banked" and row.get("actor") == nugget["actor"]]
            if rewards:
                break
            await asyncio.sleep(.1)
    reward, = rewards
    assert reward["revision"] is None and checkpoint == campaign_save_hashes(host.campaign), reward
    identity = native_world_loot.stock_enemy_identity(reward["guid"], reward.get("visual_id"))

    def projected(status):
        return status.get("game", {}).get(native_world_loot.KEY, {}).get(identity)

    for run in (host_run, guest_run):
        await wait_resident_status(run, projected, timeout=12)
    await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    arrived = await wait_resident_status(guest_run, lambda f: math.dist(f["game"]["pawns"]["local"]["location"],
                                                                        reward["transform"]["position"]) < 180, timeout=7)
    guest_actor = projected(arrived)["actor"].split(" ", 1)[1]
    press = await submit(guest_run, native_world_loot.FIXTURE, target=identity)
    if not press["ok"]:
        sight = await observe(guest, "sight", guest_actor)
        raise AssertionError((press, sight))
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda f: not projected(f), timeout=12)
    after = await submit(guest_run, "native-inventory-list")
    assert after["ok"] and any(entry["guid"] == reward["guid"] for entry in after["native_inventory"]["items"]), after
    rival = await submit(host_run, native_stock_enemy_drops.CLAIM, target=reward["guid"])
    assert rival.get("error") == "stock_enemy_loot_already_claimed", rival
    assert checkpoint == campaign_save_hashes(host.campaign), "Dig or pickup wrote a campaign checkpoint"

    def parse_props(sample):
        entries = (entry.split(",") for entry in sample.split(";") if entry)
        return [(role, name, moving, tuple(map(float, position))) for role, name, moving, *position in entries]

    prior_props = {name for _, name, _, _ in parse_props(await observe(host, "props"))}
    await dig_once("prop", advance=True)
    async with asyncio.timeout(8):
        while True:
            host_props = [row for row in parse_props(await observe(host, "props")) if row[1] not in prior_props]
            if host_props:
                break
            await asyncio.sleep(.15)
    (role, prop, _, _), = host_props
    assert role == "3", host_props
    guest_props = []
    try:
        async with asyncio.timeout(8):
            while True:
                host_props = parse_props(await observe(host, "props"))
                guest_props = parse_props(await observe(guest, "props"))
                source, = [row for row in host_props if row[1] == prop]
                replicas = [row for row in guest_props if row[0] == "1" and math.dist(row[3], source[3]) < 45]
                if source[2] == "true" and replicas:
                    break
                await asyncio.sleep(.15)
    except TimeoutError as error:
        raise AssertionError(f"The stock can did not replicate its physics pose. Host {host_props}. Guest {guest_props}.") from error
    replica, = replicas
    host_result = await verify_host_dig()
    buried_source = await observe(host, "buried-item", "local")

    def buried_reward(frame):
        return next((entry for entry in frame.get("game", {}).get(native_stock_enemy_drops.KEY, {}).values()
                     if entry["source"] == buried_source and entry["class_path"] == NUGGET), None)

    dug = buried_reward(await wait_resident_status(host_run, buried_reward, timeout=8))

    def banked(events):
        return next((entry for entry in events if entry["event"] == "native_stock_enemy_loot_banked"
                     and entry["actor"] == dug["actor"]), None)

    async with asyncio.timeout(10):
        while not (bank := banked(await resident_events(host_run, host_status))):
            await asyncio.sleep(.08)
    buried_id = native_world_loot.stock_enemy_identity(bank["guid"], bank.get("visual_id"))
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda f: buried_id in f["game"].get(native_world_loot.KEY, {}), timeout=12)
    await host.bridge.command(native_world_loot.APPROACH, buried_id, pawn, timeout=5)
    await wait_resident_status(guest_run, lambda f: math.dist(
        f["game"]["pawns"]["local"]["location"], bank["transform"]["position"]) < 180, timeout=7)
    before = sum(row["quantity"] for row in await stock_inventory(guest_run, guest=True) if row["class_path"] == NUGGET)
    picked = await submit(guest_run, native_world_loot.FIXTURE, target=buried_id)
    assert picked["ok"], picked
    await wait_resident_status(guest_run, lambda f: buried_id not in f["game"].get(native_world_loot.KEY, {}), timeout=10)
    after = sum(row["quantity"] for row in await stock_inventory(guest_run, guest=True) if row["class_path"] == NUGGET)
    assert after == before + 1, (before, after, picked)
    assert bank["revision"] is None and checkpoint == campaign_save_hashes(host.campaign), bank
    return {"stock_shovel": {**dig_result, "unearthed_item": reward, "unearthed_prop": [source, replica],
                             "duplicate_claim": rival["error"], "buried_actor_reward": bank},
            "stock_host_shovel": host_result}
