"""Harvest stock Crossroads mushrooms in a running portable campaign."""

import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from portable_stock_shovel import stock_inventory
from portable_weather_travel import enter_crossroads
from tools.silverfish import native_mushrooms as mushrooms, native_world_loot as loot
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def verify_stock_mushrooms(host, guest, pawn):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    initial = await wait_resident_status(host_run, lambda frame: frame["game"].get("native_zone", {}).get("state") == "ready")
    if initial["game"]["map"] != CROSSROADS:
        assert initial["game"]["map"] == OBSERVATORY, initial["game"]["map"]
        await enter_crossroads(host, pawn)

    arrival = await wait_resident_status(host_run, lambda frame: frame["game"].get("map") == CROSSROADS
                                         and not frame.get("awaiting_zone_guest"), timeout=60)
    host_position = arrival["game"]["pawns"]["local"]["location"]

    def same_world(frame):
        game = frame.get("game", {})
        remote = game.get("pawns", {}).get("guest", {}).get("location")
        return (frame.get("state") == "connected" and frame.get("zone_epoch") == arrival["zone_epoch"]
                and game.get(mushrooms.SUPPRESSION, 0) > 0 and remote
                and math.dist(remote, host_position) < 250)

    joined = await wait_resident_status(guest_run, same_world, timeout=35)

    checkpoint = campaign_save_hashes(host.campaign)
    located = await submit(host_run, mushrooms.LOCATE)
    assert located["ok"], located
    rows = located[mushrooms.KEY]
    assert len(rows) >= 2, rows
    host_pick = next(iter(rows))
    guest_pick = next((key for key, row in rows.items() if key != host_pick and row["patch"] == rows[host_pick]["patch"]), None)
    assert guest_pick, f"No second mushroom near the first stock patch ({rows[host_pick]['patch']})"
    await wait_resident_status(guest_run, lambda frame: {host_pick, guest_pick} <= frame["game"].get(loot.KEY, {}).keys())

    async def approach(role, key):
        result = await submit(host_run, mushrooms.APPROACH, target=f"{role}@{key}")
        assert result["ok"], result

    await approach("host", host_pick)
    taken = await submit(host_run, mushrooms.HARVEST, target=f"host@{host_pick}")
    assert taken["ok"] and host_pick not in taken[mushrooms.KEY], taken
    await wait_resident_status(guest_run, lambda frame: host_pick not in frame["game"].get(loot.KEY, {})
                               and guest_pick in frame["game"].get(loot.KEY, {}))

    await approach("guest", guest_pick)
    taken = await submit(guest_run, loot.FIXTURE, target=guest_pick)
    assert taken["ok"], taken
    await wait_resident_status(host_run, lambda frame: guest_pick not in frame["game"].get(mushrooms.KEY, {}))
    await wait_resident_status(guest_run, lambda frame: guest_pick not in frame["game"].get(loot.KEY, {}))
    claim, = [event for event in await resident_events(host_run, arrival)
              if event["event"] == "native_mushroom_claimed" and event["id"] == guest_pick]
    assert claim["patch_guid"] == rows[guest_pick]["patch_guid"] and claim["revision"] is None, claim
    assert any(item["guid"] == claim.get("inventory_guid", claim["guid"]) and item["class_path"] == claim["class_path"]
               for item in await stock_inventory(guest_run, guest=True)), claim
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_mushrooms": {"host_pick": host_pick, "guest_claim": claim,
                               "guest_stock_spawn_suppression": joined["game"][mushrooms.SUPPRESSION],
                               "guest_roster_host": joined["game"]["pawns"]["guest"]["name"],
                               "both_removed_for_both_players": True, "checkpoint_unchanged": True}}
