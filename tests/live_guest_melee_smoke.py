"""Verify real Old Pipe pickup, equip, and one fenced stock animated guest swing."""

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

from tools.silverfish.native_authored_pickup import ACTION, APPROACH, OLD_PIPE, SOURCES, SOURCES_KEY
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.native_guest_melee import CANARY, CANARY_KEY, EQUIP, EQUIP_KEY
from tools.silverfish.travel import CROSSROADS


async def verify(work: Path, vanilla_saved: Path | None) -> None:
    name = "stock-old-pipe-" + uuid.uuid4().hex[:8]
    host, guest = ("--campaign", name), ("--profile", name)
    host_run, guest_run = work / "campaigns" / name / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    processes, proof = [], {}

    async def ask(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            raw, errors = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not raw or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments[:4]}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def action(owner, operation, *arguments, expected=True):
        return await ask("action", *owner, operation, *arguments, expected=expected)

    async def launch(kind, owner, *arguments):
        with (host_run / f"{kind}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO, stdout=log,
                                                            stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((owner, process))

    async def inventory_item(character, guid):
        offset = 0
        for _ in range(17):
            page = (await action(guest, "native-inventory-list", "--target", str(offset)))["native_inventory"]
            assert (page["owner"], page["offset"]) == (character, offset), page
            for item in page["items"]:
                if item["guid"] == guid:
                    return item
            next_offset = page["next_offset"]
            if next_offset is None:
                return None
            assert next_offset > offset, page
            offset = next_offset
        raise RuntimeError("The staged inventory exceeded its configured page limit")

    def available(status):
        row = status.get("game", {}).get(SOURCES_KEY, {}).get(OLD_PIPE, {})
        return tuple(row.get(key) for key in ("map", "actor", "class_path", "state")) == (*SOURCES[OLD_PIPE], "available")

    try:
        await launch("host", host, "--port", "0", "--start-zone", CROSSROADS, "--headless", "--fixtures",
                     "--native-events", "--native-guest", "--native-zones", "--native-guest-melee-canary")
        ready = await wait(host_run, lambda state: state.get("state") == "hosting" and available(state), timeout=65)
        proof["placed_source"] = ready["game"][SOURCES_KEY][OLD_PIPE]
        invitation = await ask("invite", *host, "--name", "Stock Old Pipe tester", "--character", "Surface witness")
        await launch("join", guest, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        character = invitation["character_id"]
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("character_id") == character and state.get("game", {}).get("map") == CROSSROADS)
        await wait(host_run, lambda state: state.get("state") == "hosting" and available(state)
                   and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

        await action(host, APPROACH, "--target", "guest", "--pickup-source", OLD_PIPE)
        claim = (await action(guest, ACTION, "--target", OLD_PIPE))["native_authored_pickup_claim"]
        assert (claim["source"], claim["map"], claim["actor"], claim["class_path"], claim["owner"]) == (
            OLD_PIPE, *SOURCES[OLD_PIPE], character
        ), claim
        guid = claim["guid"]
        assert valid_guid(guid), claim
        before = await inventory_item(character, guid)
        assert before and before["source"] == "backpack" and before["class_path"] == SOURCES[OLD_PIPE][2], before
        proof.update(claim=claim, backpack=before)

        equipped = (await action(host, EQUIP, "--target", "guest", "--item-guid", guid))[EQUIP_KEY]
        assert (equipped["map"], equipped["guid"], equipped["status"], equipped["ok"]) == (
            CROSSROADS, guid, "old_pipe_equipped", True
        ), equipped
        held = await inventory_item(character, guid)
        assert held and held["source"] == "equipment" and held["class_path"] == before["class_path"], held
        proof.update(stock_equipped=equipped, equipment=held)

        operation = uuid.uuid4().hex
        args = ("--target", "guest", "--item-guid", guid, "--operation-id", operation)
        observed = (await action(host, CANARY, *args))[CANARY_KEY]
        assert tuple(observed[key] for key in ("map", "pawn", "guid", "status", "presses", "releases", "contacts",
                                               "qualified", "settled", "passive_free")) == (
            CROSSROADS, equipped["pawn"], guid, "surface_verified", 1, 1, 1, 1, True, True
        ), observed
        assert (await action(host, CANARY, *args))[CANARY_KEY] == observed
        after = await inventory_item(character, guid)
        assert after == held, (held, after)
        proof.update(stock_animation=observed, operation_replayed=True, unchanged_equipment=after, verified=True)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(processes):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        if originals is not None:
            proof["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (host_run / "live-old-pipe-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if originals is not None:
            assert proof["original_saves_verified"], "The original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve() if options.vanilla_saved else None))
