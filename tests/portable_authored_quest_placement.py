"""Reveal and claim the stock Hand-Off briefcase through ordinary interaction."""

import asyncio
import json
import math
from pathlib import Path

from live_multi_smoke import campaign_save_hashes, saved_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from portable_stock_shovel import stock_inventory
from tools.silverfish import native_host_save, native_loose_pickup as loose
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


OVERWORLD = "/Game/Classes/Maps/Maps_OverWorld/"
SMUGGLER = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
ROUTE = (OBSERVATORY, CROSSROADS, OVERWORLD + "DeadBridge_P", OVERWORLD + "StoneMaze_P",
         OVERWORLD + "PineRidge_P", SMUGGLER)
COMMAND = "fixture-stock-quest-placement"
QUEST = "Clct_HandOff_01"
SOURCE = SMUGGLER + ".Shop_Smuggler_P:PersistentLevel.Case_HandOff_01_C_1"
ITEM_CLASS = "/Game/Classes/Items/Key_Items/Case_HandOff_01.Case_HandOff_01_C"
PLACEMENTS = "native_authored_quest_placements"


def apply_quest_placement_probe(script: str) -> str:
    marker = "function pickup.command(fields, state, phase)\n"
    fixture = Path(__file__).with_name("fixtures") / "portable_authored_quest_placement_probe.lua"
    assert script.count(marker) == 1, "The replica lacks the authored pickup command"
    return script.replace(marker, marker + fixture.read_text(encoding="utf-8") + "\n", 1)


async def verify_authored_quest_placement(host, guest, pawn):
    from live_discovered_exit_smoke import route_between, wait_for_party

    host_run, guest_run = host.campaign / "run", guest.client / "run"
    reached = await wait_for_party(host_run, guest_run, OBSERVATORY)
    assert reached["game"]["native_guest_save"]["pawn"] == pawn, reached["game"]["native_guest_save"]
    passages = []
    near_retry = None
    for source, destination in zip(ROUTE, ROUTE[1:]):
        route = route_between(source, destination)
        pawn = reached["game"]["native_guest_save"]["pawn"]
        before_epoch = reached["zone_epoch"]
        if route.kind == "door":
            for participant in (pawn, "local"):
                await host.bridge.command("native-door-position", participant, "near", route.entrance)
            opened = await host.bridge.command("native-door-probe", "local", route.entrance)
            assert opened["native_door"]["decision"] in {"host_opening", "travel_requested"}, opened["native_door"]
        else:
            assert route.kind == "overlap", route
            for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                arguments = (participant, position, route.entrance, route.actor)
                try:
                    await host.bridge.command("native-exit-position", *arguments)
                except TimeoutError:
                    if position != "near" or near_retry is not None:
                        raise
                    await wait_resident_status(host_run, lambda frame: (
                        frame.get("zone_epoch") == before_epoch and frame.get("game", {}).get("map") == source
                        and frame["game"].get("state") == "hosting"
                        and frame["game"].get("native_guest_save", {}).get("pawn") == pawn
                        and not frame.get("awaiting_zone_guest")
                    ), timeout=4)
                    near_retry = {"map": source, "epoch": before_epoch, "entrance": route.entrance,
                                  "participant": participant}
                    await host.bridge.command("native-exit-position", *arguments)
        reached = await wait_for_party(host_run, guest_run, destination)
        epoch = reached["zone_epoch"]
        assert epoch != before_epoch, epoch
        travel, = [entry for entry in await resident_events(host_run, reached)
                   if entry["event"] == "native_authored_zone_request"
                   and (entry["source"], entry["destination"], entry["entrance"], entry["actor"], entry["kind"])
                   == (source, destination, route.entrance, route.actor, route.kind)
                   and entry.get("result", {}).get("travel_epoch") == epoch]
        assert travel["result"]["ok"] and travel["discovered"] is False, travel
        passages.append({"map": destination, "entrance": route.entrance, "source_epoch": before_epoch,
                         "epoch": epoch, "revision": travel["result"]["revision"]})

    epoch = reached["zone_epoch"]
    pawn = reached["game"]["native_guest_save"]["pawn"]
    target = "loose:" + SOURCE

    def same_visit(frame):
        return frame.get("zone_epoch") == epoch and frame.get("game", {}).get("map") == SMUGGLER

    def placements(frame):
        return frame.get("gameplay", {}).get(PLACEMENTS, {})

    def placed(frame):
        return placements(frame).get("items", {}).get(SOURCE, {})

    def loose_state(frame):
        return frame["game"].get(loose.KEY, {}).get(SOURCE, {}).get("state")

    def save_fingerprints():
        manifest = json.loads((host.campaign / "save/campaign.json").read_text(encoding="utf-8"))
        stock = native_host_save.directory(native_host_save.checkpoint_profile(host.profile, manifest))
        return campaign_save_hashes(host.campaign), saved_hashes(stock), saved_hashes(native_host_save.directory(guest.profile))

    async def command(seat, operation, *args):
        return (await seat.bridge.command(COMMAND, operation, *args))["ack"]["message"]

    def parse_stock_state(message):
        if message == "absent":
            return {"absent": True}
        hidden, collision, *values = message.split("|")
        pose = list(map(float, values))
        assert len(pose) == 9, message
        return {"hidden": hidden == "true", "collision": collision == "true",
                "stock_position": pose[:3], "transform": {"position": pose[:3], "rotation": pose[3:6], "scale": pose[6:]}}

    async def observe(expected):
        async with asyncio.timeout(20):
            while True:
                message = await command(host, "snapshot")
                if message.startswith(expected):
                    step, present, state = message.split("|", 2)
                    return {"step": int(step), "observed": present == "true", **parse_stock_state(state)}
                await asyncio.sleep(.15)

    before = await observe("0|false|")
    assert await command(guest, "guest") == "guest scope rejected"
    guest_before = parse_stock_state(await command(guest, "stock-state"))
    assert guest_before.get("absent") or guest_before["hidden"], guest_before
    saved = save_fingerprints()
    assert await command(host, "activate") == "stock quest activated"
    after = await observe("1|true|false|true|")
    assert save_fingerprints() == saved, "Observing the quest placement changed a campaign save"

    def projected(frame):
        return same_visit(frame) and frame.get("state") == "connected" and placed(frame)

    guest_frame = await wait_resident_status(guest_run, projected, timeout=12)
    assert (placements(guest_frame)["map"], placements(guest_frame)["epoch"]) == (SMUGGLER, epoch)
    active = placed(guest_frame)
    assert (active["quest"], active["class_path"]) == (QUEST, ITEM_CLASS), active
    async with asyncio.timeout(8):
        while True:
            guest_visible = parse_stock_state(await command(guest, "stock-state"))
            if not guest_visible.get("absent") and not guest_visible["hidden"] and guest_visible["collision"]:
                break
            await asyncio.sleep(.1)
    for component in ("position", "rotation", "scale"):
        assert math.dist(guest_visible["transform"][component], after["transform"][component]) < .03, guest_visible
        assert math.dist(active["transform"][component], after["transform"][component]) < .03, active

    host_inventory, guest_inventory = await asyncio.gather(
        stock_inventory(host_run), stock_inventory(guest_run, guest=True))
    assert all(item["class_path"] != ITEM_CLASS for item in host_inventory + guest_inventory)
    approached = await host.bridge.command(loose.APPROACH, SOURCE, pawn, timeout=5)
    assert approached["ack"]["message"] == "shared level pickup approach verified", approached["ack"]
    await wait_resident_status(guest_run, lambda frame: projected(frame) and loose_state(frame) == "available" and math.dist(
        frame["game"]["pawns"]["local"]["location"], guest_visible["stock_position"]) < 225)
    pressed = await guest.bridge.command(loose.FIXTURE, SOURCE, timeout=5)
    assert pressed["ack"]["message"] == "stock_started_consumed", pressed["ack"]

    claimed = await wait_resident_status(guest_run, lambda frame: same_visit(frame)
        and SOURCE in frame.get("gameplay", {}).get(loose.STATE, []) and not placed(frame))
    assert claimed["character_id"] == guest_frame["character_id"]
    async with asyncio.timeout(8):
        while True:
            events = [row for row in await resident_events(host_run, reached)
                      if row["event"] == "native_loose_pickup_claimed" and row.get("actor") == SOURCE]
            if events:
                claim, = events
                break
            await asyncio.sleep(.08)
    assert (claim["map"], claim["quest"], claim["class_path"], claim["mode"], claim["owner"], claim["epoch"]) == (
        SMUGGLER, QUEST, ITEM_CLASS, 2, guest_frame["character_id"], epoch), claim
    assert claim["escrow"] == "memory" and claim["revision"] is None and valid_guid(claim["guid"]), claim

    async with asyncio.timeout(8):
        while True:
            retired = [parse_stock_state(await command(seat, "stock-state")) for seat in (host, guest)]
            if all(view.get("absent") or
                   (view["hidden"] and (not view["collision"]
                                        or math.dist(after["stock_position"], view["stock_position"]) > 225))
                   for view in retired):
                break
            await asyncio.sleep(.08)
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda frame: same_visit(frame) and loose_state(frame) != "available")
    assert await command(host, "quest-step") == "1", "Receiving the sealed case turned in its quest"

    guest_result = await stock_inventory(guest_run, guest=True)
    old_items = {row["guid"]: row for row in guest_inventory}
    new_items = {row["guid"]: row for row in guest_result}
    item = new_items.pop(claim["guid"])
    assert (item["class_path"], item["quantity"]) == (ITEM_CLASS, 1), item
    assert new_items == old_items and await stock_inventory(host_run) == host_inventory

    replay = await submit(guest_run, loose.CLAIM, target=target, operation_id=claim["operation_id"])
    assert replay["ok"], replay
    assert all(replay[loose.PROOF][key] == claim[key]
               for key in ("map", "quest", "actor", "class_path", "guid", "mode", "owner", "epoch")), replay
    duplicates = await asyncio.gather(*(submit(run, loose.CLAIM, target=target) for run in (guest_run, host_run)))
    assert all(not reply["ok"] and reply["error"] == "loose_pickup_already_claimed" for reply in duplicates), duplicates
    assert await stock_inventory(guest_run, guest=True) == guest_result
    assert await stock_inventory(host_run) == host_inventory
    assert [row for row in await resident_events(host_run, reached)
            if row["event"] == "native_loose_pickup_claimed" and row.get("actor") == SOURCE] == [claim]
    inventory_view = await submit(guest_run, "native-inventory-view-fixture")
    assert inventory_view["ok"], inventory_view
    await wait_resident_status(guest_run, lambda frame: same_visit(frame)
        and frame["game"].get("native_inventory_view", {}).get("state") == "stock")
    card = await command(guest, "guest-item", claim["guid"])
    assert card == "stock backpack shows the exact sealed case", card
    closed = await submit(guest_run, "native-inventory-view-fixture")
    assert closed["ok"], closed
    assert save_fingerprints() == saved, "Collecting or replaying the quest placement wrote campaign files"
    return {"route": passages, "near_retry": near_retry, "dormant": before, "active": after,
            "guest_scope_rejected": True, "guest_projection": guest_visible, "stock_interact": pressed["ack"]["message"],
            "claim": claim, "guarded_actor_views": retired, "guest_item": item, "stock_guest_card": card,
            "host_inventory_unchanged": True,
            "replay_verified": True, "duplicate_refusals": [reply["error"] for reply in duplicates],
            "campaign_files_unchanged": True}
