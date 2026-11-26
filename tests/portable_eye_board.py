"""Exercise single- and multi-item deposits at the stock ShopKeep board."""

import asyncio

from live_dynamic_board_smoke import BOARD, probe, verify_eye_board
from portable_guest_world_start import wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish import native_board_contract, native_wire_board
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CROSSROADS, NORTHEAST_RAIL, OBSERVATORY, SHOPKEEP


async def verify_stock_eye_stacks(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    joined = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
                                        and frame.get("game", {}).get("map") == OBSERVATORY)
    character = joined["character_id"]

    visits = []
    areas = (OBSERVATORY, CROSSROADS, NORTHEAST_RAIL, SHOPKEEP)
    for source, destination in zip(areas, areas[1:]):
        route, = [row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination)]
        await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == source
                                   and frame["game"].get("native_zone", {}).get("state") == "ready")
        if route.kind == "door":
            for participant in (pawn, "local"):
                await host.bridge.command("native-door-position", participant, "near", route.entrance)
            entry = await host.bridge.command("native-door-probe", "local", route.entrance)
            assert entry["native_door"]["decision"] in {"host_opening", "travel_requested"}, entry
        else:
            for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                await host.bridge.command("native-exit-position", participant, placement, route.entrance, route.actor)
        arrival = await wait_resident_status(host_run, lambda frame:
            frame.get("game", {}).get("map") == destination and not frame.get("awaiting_zone_guest")
            and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
            and frame.get("character_id") == character and frame.get("game", {}).get("map") == destination
            and frame.get("zone_epoch") == arrival["zone_epoch"], timeout=35)
        pawn = arrival["game"]["native_guest_save"]["pawn"]
        visits.append(arrival["zone_epoch"])

    assert len(set(visits)) == len(visits)
    merchant = f"Shop_BP_C {SHOPKEEP}.Shop_ShopKeep_P:PersistentLevel.Shop_BP_C_1"
    def stock_shop(frame):
        game = frame.get("game", {})
        if game.get("map") == SHOPKEEP:
            return next((row for row in game.get("native_shops", ())
                         if row.get("actor") == merchant and row.get("state") in {"ok", "partial"}), None)

    observed = stock_shop(await wait_resident_status(host_run, stock_shop))
    proof = await verify_eye_board(host_run, guest_run, host.bridge.directory, character,
                                  wait_status=wait_resident_status)
    multiple = await verify_multi_deposit(host, guest, character, visits[-1])
    return {"stock_eye_stacks": dict(proof, zone_visits=visits, stock_shop_observation=observed),
            "stock_multi_deposit": multiple}


async def verify_multi_deposit(host, guest, character, visit):
    from live_multi_smoke import campaign_save_hashes

    quest = "Clct_Armor_Simmi"
    selection = native_board_contract.menu_target(visit, BOARD, quest)
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    async def inventory(directory):
        reply = await submit(directory, "native-inventory-list")
        assert reply["ok"], reply
        return {item["guid"]: item for item in reply["native_inventory"]["items"]}

    async def command(operation):
        return await probe(host.bridge.directory, operation)

    async def catalog():
        result = await submit(guest_run, native_board_contract.CATALOG, target=BOARD)
        assert result["ok"], result
        return result[native_board_contract.CATALOG_KEY]["turnins"]

    assert await command("multiquest") == "1"
    first, host_first, first_class = (await command("multifirst")).split(";")
    known = await inventory(guest_run)
    assert known[first]["class_path"] == first_class
    before = await wait_resident_status(host_run, lambda frame: type(frame["game"].get("native_wallet")) is int)
    wallet = before["game"]["native_wallet"]
    assert quest not in await catalog()
    refused = await submit(guest_run, "native-quest-turnin", target=selection)
    assert not refused["ok"] and refused["error"] == "stock_board_contract_missing_objective", refused
    assert await inventory(guest_run) == known
    assert await probe(host.bridge.directory, "multijournal") == "1"

    second, host_second, second_class = (await command("multisecond")).split(";")
    assert len({first, second, host_first, host_second}) == 4 and all(map(valid_guid, (first, second, host_first, host_second)))
    assert first_class != second_class
    guest_pack, host_pack = await inventory(guest_run), await inventory(host_run)
    assert guest_pack[second]["class_path"] == second_class
    assert (host_pack[host_first]["class_path"], host_pack[host_second]["class_path"]) == (first_class, second_class)
    assert quest in await catalog()
    opened = await submit(guest_run, native_wire_board.STOCK_F_FIXTURE, target=BOARD)
    assert opened["ok"], opened
    await wait_resident_status(guest_run, lambda frame: (menu := frame["game"].get(native_board_contract.MENU_KEY))
                               and menu["board"] == BOARD and menu["state"] == "visible")
    pressed = await submit(guest_run, native_wire_board.STOCK_FIXTURE, target=f"{quest}|turnin")
    assert pressed["ok"], pressed
    completed = await wait_resident_status(host_run, lambda frame:
        frame.get("gameplay", {}).get("native_quests", {}).get(quest, {}).get("step") == -10)
    payout = completed["gameplay"]["native_quests"][quest]
    assert (payout["cash"], payout["xp"], payout["wallet"], payout["actor"]) == (8000, 1, wallet + 8000, character), payout
    assert [(item["objective_guid"], item["consumed_guid"]) for item in payout["objective_deposits"]] == [
        (first, first), (second, second)], payout
    assert not {first, second} & (await inventory(guest_run)).keys()
    assert await inventory(host_run) == host_pack
    await wait_resident_status(guest_run, lambda frame: frame["game"].get("native_wallet") == wallet + 8000)
    async with asyncio.timeout(8):
        while True:
            journals = await asyncio.gather(*(probe(seat.bridge.directory, "multijournal") for seat in (host, guest)))
            if journals == ["-10", "-10"]:
                break
            await asyncio.sleep(.1)
    repeat = await submit(guest_run, "native-quest-turnin", target=selection)
    assert repeat["ok"] and repeat["effect"] == "native_quest_already_settled" and repeat["wallet"] == payout["wallet"]
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"missing_second_objective_refused": True, "classes": [first_class, second_class],
            "receipt": payout, "journals": journals, "saved_early": False}
