"""Observe the stock Hand-Off briefcase through its real quest reveal."""

import asyncio
import json
from pathlib import Path

from live_multi_smoke import campaign_save_hashes, saved_hashes
from tools.silverfish import native_host_save
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


OVERWORLD = "/Game/Classes/Maps/Maps_OverWorld/"
SMUGGLER = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
ROUTE = (OBSERVATORY, CROSSROADS, OVERWORLD + "DeadBridge_P", OVERWORLD + "StoneMaze_P",
         OVERWORLD + "PineRidge_P", SMUGGLER)
COMMAND = "fixture-stock-quest-placement"


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
                await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
        reached = await wait_for_party(host_run, guest_run, destination)
        assert reached["zone_epoch"] != before_epoch, reached["zone_epoch"]
        passages.append({"map": destination, "entrance": route.entrance, "epoch": reached["zone_epoch"]})

    def save_fingerprints():
        manifest = json.loads((host.campaign / "save/campaign.json").read_text(encoding="utf-8"))
        stock = native_host_save.directory(native_host_save.checkpoint_profile(host.profile, manifest))
        return campaign_save_hashes(host.campaign), saved_hashes(stock)

    async def command(seat, operation):
        return (await seat.bridge.command(COMMAND, operation))["ack"]["message"]

    async def observe(expected):
        async with asyncio.timeout(20):
            while True:
                message = await command(host, "snapshot")
                if message.startswith(expected):
                    step, present, hidden, collision, *position = message.split("|")
                    return {"step": int(step), "observed": present == "true", "hidden": hidden == "true",
                            "collision": collision == "true", "stock_position": list(map(float, position))}
                await asyncio.sleep(.15)

    before = await observe("0|false|")
    assert await command(guest, "guest") == "guest scope rejected"
    saved = save_fingerprints()
    assert await command(host, "activate") == "stock quest activated"
    after = await observe("1|true|false|true|")
    assert save_fingerprints() == saved, "Observing the quest placement changed a campaign save"
    return {"route": passages, "dormant": before, "active": after, "guest_scope_rejected": True,
            "campaign_files_unchanged": True}
