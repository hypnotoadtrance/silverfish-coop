"""Dig up the Missing Contact package and open it through the stock guest inventory."""

import argparse
import asyncio
from functools import partial
from pathlib import Path
import time

import live_portable_title_smoke as suite
from portable_personal_consumable import stock_inventory_open
from portable_stock_shovel import stock_inventory
from tools.silverfish import native_guest_consumable as consumable, native_host_dig_live as dig

ARTIFACTS = {
    "/Game/Classes/Items/Artifacts/Art_BrusselsSprout.Art_BrusselsSprout_C",
    "/Game/Classes/Items/Artifacts/Art_DreamDust.Art_DreamDust_C",
}


async def verify_open(host, guest, guid, saves, *, tracked=True):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    quest_id = consumable.MISSING_CONTACT_QUEST
    key = consumable.RESULT[consumable.MISSING_CONTACT]
    owner = await suite.wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected")
    host_before = await stock_inventory(host_run)
    guest_before = await stock_inventory(guest_run, guest=True)
    assert any(item["guid"] == guid for item in guest_before)

    def quest(frame):
        return frame.get("gameplay", {}).get("native_quests", {}).get(quest_id, {})

    async def stock_use(*gesture):
        result = await guest.bridge.command("native-inventory-stock-use-click-fixture", guid, *gesture, timeout=5)
        return result["ack"]["message"]

    await stock_inventory_open(guest_run, True)
    expected_step = "1" if tracked else "-"
    async with asyncio.timeout(12):
        while await stock_use("sealed-journal") != expected_step:
            await asyncio.sleep(.1)
    started = time.time()
    if tracked:
        assert await stock_use("sealed-context") == "verified native quest-item warning"
        assert await stock_use("sealed-cancel") == "verified canceled stock package use"
        assert await stock_use("sealed-journal") == "1"
        assert guest_before == await stock_inventory(guest_run, guest=True)
        assert suite.campaign_save_hashes(host.campaign) == saves
        assert await stock_use("sealed-drag") == "verified native quest-item warning"
        assert await stock_use("sealed-confirm") == "verified confirmed stock package use"
    else:
        assert await stock_use() == "verified stock use click"

    async with asyncio.timeout(12):
        while True:
            events = [entry for entry in await suite.resident_events(guest_run, owner) if entry["time_unix"] >= started]
            rejected = [entry for entry in events if entry["event"] == "native_stock_inventory_rejected"]
            assert not rejected, rejected
            settled = [entry for entry in events if entry.get(key, {}).get("guid") == guid]
            if settled:
                event, = settled
                break
            await asyncio.sleep(.1)

    receipt = event[key]
    assert (receipt["step_before"], receipt["step"], receipt["wallet_before"] - receipt["wallet"],
            receipt["reputation_before"] - receipt["reputation"], receipt["xp"]) == (
                (1, -7, 50, 180, 1) if tracked else (None, -7, 0, 0, 1)), receipt
    for run in (host_run, guest_run):
        await suite.wait_resident_status(run, lambda frame:
            quest(frame).get("step") == -7 and frame["game"].get("native_wallet") == receipt["wallet"])
    async with asyncio.timeout(12):
        while await stock_use("sealed-journal") != "-7":
            await asyncio.sleep(.1)

    guest_after = await stock_inventory(guest_run, guest=True)
    assert not any(item["guid"] == guid for item in guest_after)
    assert {item["class_path"] for item in guest_after if item["guid"] in receipt["awarded_guids"]} == ARTIFACTS
    assert await stock_inventory(host_run) == host_before
    replay = await suite.submit(guest_run, consumable.MISSING_CONTACT, target=f"{guid}|{int(tracked)}",
                                operation_id=event["operation_id"])
    assert replay["ok"] and replay[key] == receipt, replay
    assert await stock_inventory(guest_run, guest=True) == guest_after
    await stock_inventory_open(guest_run, False)
    assert suite.campaign_save_hashes(host.campaign) == saves, "Opening the package wrote an early save"
    saved = await suite.submit(host_run, "checkpoint")
    assert saved["ok"], saved
    campaign = suite.runtime.read_json(host.campaign / "save" / "campaign.json")
    custody = campaign["world"]["party"][dig.CUSTODY]
    assert custody["state"] == "opened" and custody["package_guid"] == guid, custody
    return {"receipt": receipt, "canceled_prompt_preserved_state": tracked, "guest_stock_journal_step": -7,
            "sole_opener_received_artifacts": True, "checkpoint_custody": custody}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--first-game", type=Path, required=True)
    parser.add_argument("--second-game", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(suite.verify(args.work.resolve(), args.first_game.resolve(), args.second_game.resolve(),
                             args.vanilla_saved.resolve(), stock_shovel=True, stock_shovel_quest=True,
                             missing_contact_open_check=verify_open,
                             missing_contact_untracked_check=partial(verify_open, tracked=False)))
