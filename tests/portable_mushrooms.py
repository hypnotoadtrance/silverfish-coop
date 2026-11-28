"""Harvest stock Crossroads mushrooms in a running portable campaign."""

import math
from pathlib import Path

from live_multi_smoke import campaign_save_hashes
from live_native_mushrooms_smoke import stock_regrowth
from portable_guest_world_start import resident_events, wait_resident_status
from portable_stock_shovel import stock_inventory
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_mushrooms as mushrooms, native_world_loot as loot
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def instrument_mushroom_sampler(source):
    anchor = "function mushrooms.command(fields, state, phase)"
    assert source.count(anchor) == 1
    fixture = (Path(__file__).with_name("fixtures") / "mushroom_sampler_probe.lua").read_text(encoding="utf-8")
    return source.replace(anchor, fixture + "\n" + anchor +
                          '\n    if fields[2] == "native-mushroom-compare-fixture" then'
                          '\n        return compare_mushroom_samplers(fields, state, phase)\n    end')


async def verify_stock_mushrooms(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    initial = await wait_resident_status(host_run, lambda frame: frame["game"].get("native_zone", {}).get("state") == "ready")
    if initial["game"]["map"] != CROSSROADS:
        assert initial["game"]["map"] == OBSERVATORY, initial["game"]["map"]
        await enter_crossroads(host, pawn)

    arrival = await wait_resident_status(host_run, lambda frame: frame["game"].get("map") == CROSSROADS
                                         and not frame.get("awaiting_zone_guest")
                                         and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
    guest_position = arrival["game"]["pawns"]["guest"]["location"]

    def same_world(frame):
        game = frame.get("game", {})
        local = game.get("pawns", {}).get("local", {}).get("location")
        return (frame.get("state") == "connected" and game.get("map") == CROSSROADS
                and frame.get("zone_epoch") == arrival["zone_epoch"]
                and game.get(mushrooms.SUPPRESSION, 0) > 0 and local
                and math.dist(local, guest_position) < 250)

    joined = await wait_resident_status(guest_run, same_world, timeout=35)

    checkpoint = campaign_save_hashes(host.campaign)
    located = await host.bridge.command(mushrooms.LOCATE, timeout=8)
    rows = located[mushrooms.KEY]
    assert len(rows) >= 2, rows
    host_pick = next(iter(rows))
    guest_pick = next((key for key, row in rows.items() if key != host_pick and row["patch"] == rows[host_pick]["patch"]), None)
    assert guest_pick, f"No second mushroom near the first stock patch ({rows[host_pick]['patch']})"
    comparison = await host.bridge.command("native-mushroom-compare-fixture", rows[host_pick]["patch"], timeout=8)
    children, reads, row_bytes, direct, native_ms, raw_ms, lua_ms, native_kib, raw_kib, lua_kib = (
        comparison["ack"]["message"].removeprefix("mushroom samplers ").split())
    sampler = {"children_matched": int(children), "reads_per_path": int(reads), "row_bytes_per_pass": int(row_bytes),
               "native_ms": float(native_ms), "lua_ms": float(lua_ms),
               "native_heap_growth_kib": float(native_kib), "lua_heap_growth_kib": float(lua_kib),
               "different_world_refused": True, "lua_fallback_fields_matched": True}
    if direct == "1":
        sampler.update(prior_native_plus_lua_ms=float(raw_ms), prior_native_plus_lua_heap_growth_kib=float(raw_kib),
                       direct_row_bytes_matched=True, different_map_refused=True)

    async def host_fixture(action, key, target):
        source = rows[key]
        return await host.bridge.command(action, source["patch"], source["patch_guid"], target, timeout=8)

    host_pawn = arrival["game"]["pawns"]["local"]["name"]
    guest_pawn = arrival["game"]["native_guest_save"]["pawn"]
    # Only nearby loose loot is projected, so move the guest to this patch first.
    await host_fixture(mushrooms.APPROACH, guest_pick, guest_pawn)
    await wait_resident_status(guest_run, lambda frame: {host_pick, guest_pick} <= frame["game"].get(loot.KEY, {}).keys())
    taken = await host_fixture(mushrooms.HARVEST, host_pick, host_pawn)
    assert host_pick not in taken.get(mushrooms.KEY, {}), taken
    await wait_resident_status(guest_run, lambda frame: host_pick not in frame["game"].get(loot.KEY, {})
                               and guest_pick in frame["game"].get(loot.KEY, {}))

    await host_fixture(mushrooms.APPROACH, guest_pick, guest_pawn)
    taken = await submit(guest_run, loot.FIXTURE, target=guest_pick)
    assert taken["ok"], taken
    harvested = await wait_resident_status(host_run, lambda frame: guest_pick not in frame["game"].get(mushrooms.KEY, {}))
    await wait_resident_status(guest_run, lambda frame: guest_pick not in frame["game"].get(loot.KEY, {}))
    claim, = [event for event in await resident_events(host_run, arrival)
              if event["event"] == "native_mushroom_claimed" and event["id"] == guest_pick]
    assert claim["patch_guid"] == rows[guest_pick]["patch_guid"] and claim["revision"] is None, claim
    assert any(item["guid"] == claim.get("inventory_guid", claim["guid"]) and item["class_path"] == claim["class_path"]
               for item in await stock_inventory(guest_run, guest=True)), claim

    patch = claim["patch"]
    removed = {host_pick, guest_pick}
    harvested_at = harvested["game"]["native_scene"]["game_seconds"]
    await wait_resident_status(host_run, lambda frame: frame["game"].get("native_scene", {}).get("game_seconds", 0)
                               >= harvested_at + 2, timeout=12)
    growth = await stock_regrowth(host.bridge.directory, patch)
    count = growth["children_after"] - growth["children_before"]

    def fresh_children(frame):
        return {key for key, item in frame["game"].get(mushrooms.KEY, {}).items()
                if item["patch"] == patch and key not in rows}

    regrown = await wait_resident_status(host_run, lambda frame: len(fresh_children(frame)) >= count, timeout=12)
    new_ids = fresh_children(regrown)
    assert not removed & regrown["game"][mushrooms.KEY].keys()
    await wait_resident_status(guest_run, lambda frame: new_ids <= frame["game"].get(loot.KEY, {}).keys()
                               and not removed & frame["game"].get(loot.KEY, {}).keys(), timeout=12)
    growth["replicated_new_ids"] = sorted(new_ids)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_mushrooms": {"host_pick": host_pick, "guest_claim": claim,
                               "guest_stock_spawn_suppression": joined["game"][mushrooms.SUPPRESSION],
                               "guest_local_pawn": joined["game"]["pawns"]["local"]["name"],
                               "both_removed_for_both_players": True, "stock_regrowth": growth,
                               "native_sampler_comparison": sampler,
                               "checkpoint_unchanged": True}}
