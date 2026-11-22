"""Pick up stock crate loot without a guest and save it at the Observatory rest site."""

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

from tools.silverfish import native_host_save, native_loot, native_world_loot
from tools.silverfish.authored_loot import full_name
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY


async def verify(work: Path, canary: Path) -> None:
    name = "host-crate-save-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run = campaign / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    original = saved_hashes(canary)
    assert original, "The isolated solo save canary has no files"
    saved = campaign / "save/campaign.json"
    crate = full_name(OBSERVATORY, "LootCrate_Small_BP_C_1")
    command = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work), "host", "--campaign", name,
               "--start-zone", OBSERVATORY, "--headless", "--fixtures", "--native-events", "--native-guest",
               "--native-zones", "--native-expanded-zones")
    proof, processes = {"verified": False}, []

    def checkpoint():
        return json.loads(saved.read_text(encoding="utf-8"))

    def item_claim(current, guid):
        rolls = current.get(native_loot.STATE_KEY, {})
        return next((item.get("claimed_by") for item in rolls.get(crate, {}).get("items", ()) if item["guid"] == guid), None)

    async def local(action, **arguments):
        result = await submit(run, action, **arguments)
        assert result["ok"], (action, result)
        return result

    async def start(port, label, previous=None):
        log = run / f"{label}.log"
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(*command, "--port", str(port), cwd=REPO, stdout=output,
                                                           stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(process)
        def ready(state):
            if process.returncode is not None:
                raise RuntimeError(log.read_text(encoding="utf-8", errors="replace"))
            return (state.get("state") == "hosting" and state.get("generation") != previous
                    and state.get("game", {}).get("map") == OBSERVATORY)

        game = await wait(run, ready, timeout=60)
        return process, game

    async def stop(process):
        if process.returncode is None:
            await local("stop")
        assert await asyncio.wait_for(process.wait(), 20) == 0

    try:
        process, started = await start(0, "host")
        assert not started.get("link_connected")
        before = checkpoint()
        await local("native-loot-approach", target=f"host@{crate}")
        await local("native-loot-crate-fixture", target=crate)
        opened = await wait(run, lambda state: crate in state.get("gameplay", {}).get(native_loot.STATE_KEY, {}))
        assert checkpoint() == before
        proof.update(revision_before_open=before["revision"], revision_after_open=checkpoint()["revision"])
        roll = opened["gameplay"][native_loot.STATE_KEY][crate]
        item = next(row for row in roll["items"] if row["class_path"] not in native_loot.CASH_RANGES)
        guid = item["guid"]
        identity = native_world_loot.crate_identity(roll["id"], guid)
        await wait(run, lambda state: identity in state.get("game", {}).get(native_world_loot.KEY, {}))
        await local(native_world_loot.APPROACH, target=f"host@{identity}")
        await local(native_world_loot.FIXTURE, target=identity)
        live = await wait(run, lambda state: state.get("native_inventory_checkpoint_pending") is True
                          and item_claim(state.get("gameplay", {}), guid) == "host"
                          and identity not in state.get("game", {}).get(native_world_loot.KEY, {}))
        assert checkpoint() == before and not live.get("link_connected")
        inventory = (await local("native-inventory-list"))["native_inventory"]
        granted, = [row for row in inventory["items"] if row["guid"] == guid]
        assert granted["class_path"] == item["class_path"], granted
        proof.update(revision_before_pickup=before["revision"], revision_after_pickup=checkpoint()["revision"], item=granted)

        await local("native-rest-approach")
        assert checkpoint() == before
        await local("native-rest", amount=0)
        after = checkpoint()
        assert after["revision"] == before["revision"] + 1
        assert item_claim(after["world"]["party"], guid) == "host"
        native_host_save.verify_checkpoint(campaign / "game-user", after)
        await wait(run, lambda state: state.get("native_inventory_checkpoint_pending") is False)
        proof["revision_after_rest"] = after["revision"]

        await stop(process)
        _, restarted = await start(started["endpoint"]["port"], "restart", started["generation"])
        assert item_claim(restarted["gameplay"], guid) == "host"
        restored = (await local("native-inventory-list"))["native_inventory"]
        assert granted in restored["items"], restored
        assert identity not in restarted.get("game", {}).get(native_world_loot.KEY, {})
        proof["verified"] = True
    finally:
        for process in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(process)
                except (AssertionError, OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 15)
        proof["original_saves_verified"] = saved_hashes(canary) == original
        (run / "live-host-crate-save-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"], "The isolated solo save canary changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
