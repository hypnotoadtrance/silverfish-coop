"""Exercise the exact ShopKeep eye redemption with two guest backpack stacks."""

from live_dynamic_board_smoke import verify_eye_board
from portable_guest_world_start import wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
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
    proof = await verify_eye_board(host_run, guest_run, host.bridge.directory, character,
                                  wait_status=wait_resident_status)
    return {"stock_eye_stacks": dict(proof, zone_visits=visits)}
