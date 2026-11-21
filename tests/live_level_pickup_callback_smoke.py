"""Verify the placed Dead End report and Scav Bar Gold Bag stock pickup callbacks."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_authored_pickup as pickup, native_host_save
from tools.silverfish.control import submit


async def verify(work, original, source):
    area, actor, item_class = pickup.SOURCES[source]
    profile = f"stock-level-pickup-{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", profile), ("--profile", profile)
    hrun = work / "campaigns" / profile / "run"
    grun = work / "clients" / profile / "run"
    hrun.mkdir(parents=True)
    print(hrun, flush=True)
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(original)
    assert originals
    processes = []

    async def start(run, label, *args):
        with (hrun / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((run, process))

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, err = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, (out.decode(), err.decode())
        return json.loads(out)

    async def action(run, opcode, **kwargs):
        result = await submit(run, opcode, **kwargs)
        assert result.get("ok"), result
        return result

    def state(frame):
        return frame.get("game", {}).get(pickup.SOURCES_KEY, {}).get(source, {}).get("state")

    def claim(frame):
        return frame.get("gameplay", {}).get(pickup.STATE_KEY, {}).get(source)

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    try:
        await start(hrun, "host", "host", *host, "--port", "0", *host_flags, "--start-zone", area)
        await wait(hrun, lambda frame: frame.get("state") == "hosting" and frame.get("game", {}).get("map") == area)
        invitation = await command("invite", *host, "--name", "Stock pickup tester", "--character", "Stock pickup tester")
        await start(grun, "guest", "join", *guest, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        await wait(grun, lambda frame: frame.get("state") == "connected" and frame.get("game", {}).get("map") == area)
        await wait(hrun, lambda frame: frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        if source == pickup.GOLD_BAG:
            for run in (hrun, grun):
                await wait(run, lambda frame: state(frame) != "available")
            await action(hrun, pickup.GOLD_BAG_FIXTURE)
        for run in (hrun, grun):
            await wait(run, lambda frame: state(frame) == "available")
        await action(hrun, pickup.APPROACH, target="guest", pickup_source=source)
        press = await action(grun, pickup.STOCK_FIXTURE, pickup_source=source)
        interaction = press["native_authored_pickup_interaction"]
        assert (interaction["source"], interaction["entry"]) == (source, "stock"), interaction
        settled = claim(await wait(hrun, lambda frame: claim(frame)))
        expected = "gold_bag_conversation" if source == pickup.GOLD_BAG else "officer_reaction"
        assert (settled["map"], settled["actor"], settled["class_path"], settled["owner"], settled["effect"]) == (
            area, actor, item_class, invitation["character_id"], expected), settled
        await wait(grun, lambda frame: claim(frame) == settled and state(frame) != "available")
        events = [json.loads(line) for line in (hrun / "events.jsonl").read_text().splitlines()]
        event, = [row for row in events if row["event"] == "native_authored_pickup_committed" and row["source"] == source]
        repeated = await action(grun, pickup.ACTION, target=source, operation_id=event["operation_id"])
        assert repeated["native_authored_pickup_claim"] == settled
        denied = await submit(hrun, pickup.ACTION, target=source)
        assert denied.get("error") == "native_authored_pickup_already_claimed", denied
        inventory = (await action(grun, "native-inventory-list"))["native_inventory"]["items"]
        row, = [row for row in inventory if row["guid"] == settled["guid"]]
        assert row["class_path"] == item_class
        saved = json.loads((hrun.parent / "save" / "campaign.json").read_bytes())
        assert saved["world"]["party"][pickup.STATE_KEY][source] == settled
        native_host_save.verify_checkpoint(hrun.parent / "game-user", saved)
        proof = dict(interaction=interaction, pickup=settled, backpack=row, duplicate=denied["error"],
                     original_files_verified=len(originals))
        (hrun / "stock-pickup-callback.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print(json.dumps(proof), flush=True)
    finally:
        for run, process in reversed(processes):
            if process.returncode is None:
                try:
                    await submit(run, "stop", timeout=15)
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, TimeoutError, ValueError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        assert saved_hashes(original) == originals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--source", choices=pickup.EFFECTS, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved, args.source))
