"""Exercise the exact ShopKeep eye redemption with two guest backpack stacks."""

from live_dynamic_board_smoke import verify_eye_board
from portable_guest_world_start import wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, NORTHEAST_RAIL, OBSERVATORY, SHOPKEEP


async def verify_stock_eye_stacks(host, guest, _pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    joined = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
                                        and frame.get("game", {}).get("map") == OBSERVATORY)
    character = joined["character_id"]

    async def place(command, **fields):
        reply = await submit(host_run, command, **fields)
        assert reply["ok"], reply
        return reply

    visits = []
    areas = (OBSERVATORY, CROSSROADS, NORTHEAST_RAIL, SHOPKEEP)
    for source, destination in zip(areas, areas[1:]):
        route, = [row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination)]
        if route.kind == "door":
            for owner in ("host", character):
                await place("native-door-position", target=owner, exit_position="near", entrance=route.entrance)
            entry = await place("native-door-probe", target="host", entrance=route.entrance)
            assert entry["native_door"]["decision"] in {"host_opening", "travel_requested"}, entry
        else:
            fields = {"entrance": route.entrance, "exit_actor": route.actor}
            for owner in ("host", character):
                await place("native-exit-position", target=owner, exit_position="near", **fields)
            await place("native-exit-position", target="host", exit_position="inside", **fields)
        arrival = await wait_resident_status(host_run, lambda frame:
            frame.get("game", {}).get("map") == destination and not frame.get("awaiting_zone_guest")
            and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
            and frame.get("character_id") == character and frame.get("game", {}).get("map") == destination
            and frame.get("zone_epoch") == arrival["zone_epoch"], timeout=35)
        visits.append(arrival["zone_epoch"])

    assert len(set(visits)) == len(visits)
    proof = await verify_eye_board(host_run, guest_run, host.bridge.directory, character,
                                  wait_status=wait_resident_status)
    return {"stock_eye_stacks": dict(proof, zone_visits=visits)}
