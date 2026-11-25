"""Exercise a stock Crossroads item's carry and pickup on a resident pair."""

import asyncio
from contextlib import contextmanager
import math

from portable_guest_world_start import resident_events, wait_resident_status
from portable_stock_shovel import stock_inventory
from tools.silverfish import native_loose_pickup as loose, native_prop_carry as carry
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


PREVIEW_FIXTURE = "native-loose-pickup-preview-fixture"


@contextmanager
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

    with overlay(game, "native_prop_carry.lua", enable_probe), overlay(
        game, "native_authored_pickup.lua", instrument_preview
    ):
        yield


def instrument_preview(source):
    def insert(before, after):
        nonlocal source
        assert source.count(before) == 1, before
        source = source.replace(before, after)

    anchor = "local previews, mesh_class = {}, nil"
    insert(anchor, anchor + "\nlocal preview_probe = {}")
    anchor = '    if action == "native-loose-pickup-cancel-preview" then'
    insert(anchor, """    if action == "native-loose-pickup-preview-fixture" and fixtures and title_fixtures and #fields == 4 then
        local mode, target = fields[3], fields[4]
        if phase == "hosting" and mode == "reject" then
            preview_probe.reject = target
            return "next matching placed pickup will be refused"
        end
        if phase == "connected" and mode == "read" and preview_probe.target == target then
            return ("%d|%d|%d|%.1f"):format(preview_probe.meshes, preview_probe.collision_kept and 1 or 0,
                preview_probe.rolled_back and 1 or 0, preview_probe.latency or -1)
        end
        error("placed pickup preview probe scope unavailable", 0)
    end
""" + anchor)
    anchor = '        restore_preview(fields[3])\n        return "loose pickup preview released"'
    insert(anchor, """        local prior = previews[fields[3]]
        restore_preview(fields[3])
        if prior and preview_probe.target == fields[3] then
            preview_probe.rolled_back = true
            for _, mesh in ipairs(prior.meshes) do
                if not valid(mesh) or not mesh:IsVisible() then preview_probe.rolled_back = false end
            end
        end
        return "loose pickup preview released"
""".rstrip())
    anchor = '    if action == "native-loose-pickup-claim" then'
    insert(anchor, anchor + """
        if phase == "hosting" and preview_probe.reject and fields[5] == preview_probe.reject then
            preview_probe.reject = nil
            error("loose_pickup_source_unavailable", 0)
        end""")
    anchor = '        return stock_input_fixture.press_world_loot(state, pawn, actor, actor.Box, function()'
    insert(anchor, anchor + "\n            preview_probe.pressed = os.clock()")
    anchor = '    local preview = {actor = actor, meshes = {}, expires = os.clock() + 1.5}'
    insert(anchor, '    local collision = actor:GetActorEnableCollision()\n' + anchor)
    anchor = '    if #preview.meshes == 0 then previews[target] = nil end'
    insert(anchor, """    local hidden = 0
    for _, mesh in ipairs(preview.meshes) do
        if not mesh:IsVisible() then hidden = hidden + 1 end
    end
    preview_probe.target, preview_probe.meshes = target, hidden
    preview_probe.collision_kept = actor.bHidden == false and actor:GetActorEnableCollision() == collision and collision
    preview_probe.rolled_back = false
    preview_probe.latency = preview_probe.pressed and (os.clock() - preview_probe.pressed) * 1000
""" + anchor)
    return source


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

    async def fixture(seat, action, target=""):
        return (await seat.bridge.command(carry.FIXTURE, action, target, timeout=6))["ack"]["message"]

    async def sample(seat):
        return tuple(float(value) for value in (await fixture(seat, "sample", path)).split("|")[:3])

    assert await fixture(host, "select", path) == "selected the enrolled authored physics prop"
    await fixture(host, "approach", current_pawn)

    async def shared_pose():
        async with asyncio.timeout(10):
            while True:
                try:
                    authority, replica = await asyncio.gather(sample(host), sample(guest))
                except RuntimeError as error:
                    if "native_prop_carry_fixture_sample_unavailable" not in str(error):
                        raise
                else:
                    if math.dist(authority, replica) <= 10:
                        return authority, replica
                await asyncio.sleep(.12)

    await shared_pose()
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

    async def preview():
        reply = await guest.bridge.command(PREVIEW_FIXTURE, "read", target, timeout=4, update_status=False)
        meshes, collision, restored, latency = reply["ack"]["message"].split("|")
        return int(meshes), int(collision), int(restored), float(latency)

    baseline = await stock_inventory(guest_run, guest=True)
    await host.bridge.command(PREVIEW_FIXTURE, "reject", path, timeout=4)
    refused = await submit(guest_run, loose.FIXTURE, target=target)
    assert refused["ok"], refused
    async with asyncio.timeout(5):
        while True:
            try:
                rejected_preview = await preview()
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
    accepted_preview = await preview()
    assert accepted_preview[0] > 0 and accepted_preview[1:3] == (1, 0) and 0 <= accepted_preview[3] < 100, accepted_preview
    return {"stock_placed_item": {
        "actor": name, "held_movement_cm": math.dist(motion["game"][carry.FIXTURE]["position"], before),
        "host_pose": host_pose, "guest_pose": guest_pose, "claim": applied, "backpack": inventory,
        "visibility": dict(zip(("host", "guest"), visibility, strict=True)),
        "local_preview": {"refused_meshes": rejected_preview[0], "accepted_meshes": accepted_preview[0],
                          "refused_ms": rejected_preview[3], "accepted_ms": accepted_preview[3],
                          "collision_preserved": True, "refusal_restored_mesh": True},
    }}
