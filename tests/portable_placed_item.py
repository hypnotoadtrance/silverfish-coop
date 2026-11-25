"""Exercise a stock Crossroads item's carry and pickup on a resident pair."""

import asyncio
import math

from portable_guest_world_start import resident_events, wait_resident_status
from portable_pickup_preview import PREVIEW_FIXTURE, read_preview
from portable_stock_shovel import stock_inventory
from tools.silverfish import native_loose_pickup as loose, native_prop_carry as carry
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def placed_item_overlay(game, overlay):
    def enable_probe(source):
        gate = 'local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        anchor = '    if mode == "sample" and #fields == 4 then'
        assert source.count(gate) == source.count(anchor) == 1
        source = source.replace(gate, gate + '\n    or headless and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"')
        return source.replace(anchor, """    if mode == "placed-visibility" and #fields == 4 then
        local actor = StaticFindObject(fields[4])
        if not valid(actor) or actor:IsActorBeingDestroyed() then return "absent" end
        assert(item_box(actor) and placed_item(actor))
        return actor.bHidden == false and actor:GetActorEnableCollision() and "visible" or "hidden"
    end
""" + anchor)

    return overlay(game, "native_prop_carry.lua", enable_probe)



async def verify_stock_placed_item(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    initial = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if initial["game"]["map"] != CROSSROADS:
        assert initial["game"]["map"] == OBSERVATORY, initial["game"]["map"]
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obsv_Etr", "Level_Exit_BP_C_1")
    arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
                                         and not frame.get("awaiting_zone_guest"), timeout=60)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
        and frame.get("gameplay_fresh") and frame.get("game", {}).get("map") == CROSSROADS
        and frame.get("zone_epoch") == arrival["zone_epoch"], timeout=35)

    def eligible(frame):
        return [item for item in frame.get("game", {}).get(carry.PLACED_PROPS, {}).values()
                if item["class_path"].startswith("/Game/Classes/")
                and loose.actor_map(item["actor"].split(" ", 1)[-1]) == CROSSROADS]

    census = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound"
                                        and eligible(frame), timeout=20)
    selected = min(eligible(census), key=lambda item: (item["mass"], item["actor"]))
    name = selected["actor"]
    _, path = name.split(" ", 1)
    item_class = selected["class_path"]
    current_pawn = census["game"]["native_guest_save"]["pawn"]

    async def fixture(seat, action, target="", *, timeout=6):
        return (await seat.bridge.command(carry.FIXTURE, action, target, timeout=timeout))["ack"]["message"]

    async def sample(seat):
        return tuple(float(value) for value in (await fixture(seat, "sample", path, timeout=1.5)).split("|")[:3])

    assert await fixture(host, "select", path) == "selected the enrolled authored physics prop"
    await fixture(host, "approach", current_pawn)

    async def shared_pose():
        async with asyncio.timeout(10):
            while True:
                observations = await asyncio.gather(sample(host), sample(guest), return_exceptions=True)
                for observation in observations:
                    if isinstance(observation, Exception) and not (
                        isinstance(observation, TimeoutError)
                        or "native_prop_carry_fixture_sample_unavailable" in str(observation)
                    ):
                        raise observation
                authority, replica = observations
                if isinstance(authority, tuple) and isinstance(replica, tuple) and math.dist(authority, replica) <= 10:
                    return authority, replica
                # These reads are safe to repeat if the resident command lane is busy.
                await asyncio.sleep(.12)

    _, position = await shared_pose()
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
        and (point := frame["game"].get("pawns", {}).get("local", {}).get("location")) is not None
        and None not in point and math.dist(point, position) < 225, timeout=8)
    assert await fixture(host, "placed-visibility", path) == await fixture(guest, "placed-visibility", path) == "visible"

    def moved(frame, edge, lease=None):
        game = frame.get("game", {})
        event, item = game.get(carry.PROOF, {}), game.get(carry.FIXTURE, {})
        return (event.get("actor") == name and event.get("edge") == edge
                and (lease is None or event.get("lease") == lease) and item.get("actor") == name)

    grabbed = await submit(guest_run, carry.FIXTURE, target=f"pickup@{item_class}|{name}")
    assert grabbed["ok"], grabbed
    held = await wait_resident_status(host_run, lambda frame: moved(frame, "grab")
                                     and frame["game"][carry.FIXTURE].get("held"), timeout=10)
    lease = held["game"][carry.PROOF]["lease"]
    before = held["game"][carry.FIXTURE]["position"]
    turned = await submit(guest_run, carry.FIXTURE, target="turn")
    assert turned["ok"], turned
    motion = await wait_resident_status(host_run, lambda frame: moved(frame, "grab", lease)
        and frame["game"][carry.FIXTURE].get("held") and math.dist(frame["game"][carry.FIXTURE]["position"], before) > 25,
        timeout=10)
    dropped = await submit(guest_run, carry.FIXTURE, target="drop")
    assert dropped["ok"], dropped
    await wait_resident_status(host_run, lambda frame: moved(frame, "drop", lease)
                               and not frame["game"][carry.FIXTURE].get("held"), timeout=10)
    host_pose, guest_pose = await shared_pose()

    await host.bridge.command(loose.APPROACH, path, current_pawn, timeout=5)
    target = f"loose:{path}"

    baseline = await stock_inventory(guest_run, guest=True)
    await host.bridge.command(PREVIEW_FIXTURE, "reject", path, timeout=4)
    refused = await submit(guest_run, loose.FIXTURE, target=target)
    assert refused["ok"], refused
    async with asyncio.timeout(5):
        while True:
            try:
                rejected_preview = await read_preview(guest, target)
            except RuntimeError as error:
                if "preview probe scope unavailable" not in str(error):
                    raise
            else:
                if rejected_preview[2] == 1:
                    break
            await asyncio.sleep(.05)
    assert rejected_preview[0] > 0 and rejected_preview[1] == 1 and 0 <= rejected_preview[3] < 100, rejected_preview
    assert await stock_inventory(guest_run, guest=True) == baseline
    assert await fixture(guest, "placed-visibility", path) == "visible"
    assert await fixture(host, "placed-visibility", path) == "visible"

    taken = await submit(guest_run, loose.FIXTURE, target=target)
    assert taken["ok"], taken
    async with asyncio.timeout(8):
        while True:
            receipts = [event for event in await resident_events(host_run, arrival)
                        if event.get("event") == "native_loose_pickup_claimed" and event.get("actor") == path]
            if receipts:
                applied, = receipts
                break
            await asyncio.sleep(.08)
    assert applied["owner"] == guest_frame["character_id"], applied
    inventory, = [row for row in await stock_inventory(guest_run, guest=True) if row["guid"] == applied["guid"]]
    assert inventory["class_path"] == item_class and inventory["source"] == "backpack", inventory
    repeated = await submit(host_run, loose.CLAIM, target=target)
    assert not repeated["ok"] and repeated["error"] == "loose_pickup_already_claimed", repeated
    visibility = await asyncio.gather(fixture(host, "placed-visibility", path), fixture(guest, "placed-visibility", path))
    assert all(value in ("absent", "hidden") for value in visibility), visibility
    accepted_preview = await read_preview(guest, target)
    assert accepted_preview[0] > 0 and accepted_preview[1:3] == (1, 0) and 0 <= accepted_preview[3] < 100, accepted_preview
    return {"stock_placed_item": {
        "actor": name, "held_movement_cm": math.dist(motion["game"][carry.FIXTURE]["position"], before),
        "host_pose": host_pose, "guest_pose": guest_pose, "claim": applied, "backpack": inventory,
        "visibility": dict(zip(("host", "guest"), visibility, strict=True)),
        "local_preview": {"refused_meshes": rejected_preview[0], "accepted_meshes": accepted_preview[0],
                          "refused_ms": rejected_preview[3], "accepted_ms": accepted_preview[3],
                          "collision_preserved": True, "refusal_restored_mesh": True},
    }}
