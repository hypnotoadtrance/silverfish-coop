"""Verify exact guest Bomber Jacket equip, refusal, and detached restoration."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish.native_guest_armor import ACTION, CLASS_PATH, FIXTURE, effects
from tools.silverfish.travel import OBSERVATORY


async def verify(work, vanilla_saved):
    name = "stock-guest-armor-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    client = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla_saved)
    evidence, jobs = {}, []

    async def ask(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(process.communicate(), 30)
        if not output or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def act(owner, name, *arguments, success=True):
        return await ask("action", *owner, name, *arguments, success=success)

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await act(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    async def enroll():
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("game", {}).get("map") == OBSERVATORY)
        return await wait(run, lambda s: s.get("state") == "hosting" and s.get("game", {}).get("map") == OBSERVATORY
                          and s.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def inventory(seat, identity):
        page = (await act(seat, "native-inventory-list"))["native_inventory"]
        assert page["owner"] == identity and page["next_offset"] is None, page
        rows = {row["guid"]: row for row in page["items"]}
        assert len(rows) == page["total"], page
        return rows

    def jacket(rows, guid, source):
        row = rows.get(guid)
        assert row and (row["class_path"], row["source"], row["quantity"], row["stackable"]) == (
            CLASS_PATH, source, 1, False
        ), rows
        return row

    async def seed():
        guid = uuid.uuid4().hex.upper()
        result = (await act(host, FIXTURE, "--target", "guest", "--item-guid", guid))["native_guest_armor"]
        assert result["guid"] == guid and result["status"] == "seeded", result
        return guid

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    client_flags = ("--headless", "--native-zones")
    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await ask("invite", *host, "--name", "Armor tester", "--character", "Coat")
        character = invitation["character_id"]
        guest = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        first = await enroll()
        host_before = await inventory(host, "host")
        initial = await inventory(client, character)
        assert all(row["source"] not in ("armor", "helmet") for row in initial.values()), initial
        evidence["initial_guest_inventory"] = initial

        guid = await seed()
        backpack = await inventory(client, character)
        evidence["backpack_row"] = jacket(backpack, guid, "backpack")
        assert {key: row for key, row in backpack.items() if key != guid} == initial, backpack
        wrong = await act(client, ACTION, "--target", "A" * 32, success=False)
        assert wrong["error"] == "native_guest_armor_exact_owned_row_required", wrong
        operation = uuid.uuid4().hex
        equip = (ACTION, "--target", guid, "--operation-id", operation)
        receipt = (await act(client, *equip))["native_guest_armor"]
        assert (receipt["status"], receipt["guid"], receipt["class_path"]) == ("equipped", guid, CLASS_PATH), receipt
        assert effects(receipt), receipt
        worn = await inventory(client, character)
        evidence["equipped_row"] = jacket(worn, guid, "armor")
        assert {key: row for key, row in worn.items() if key != guid} == initial, worn
        assert (await act(client, *equip))["native_guest_armor"] == receipt
        evidence["equip"] = receipt

        other = await seed()
        occupied = await act(client, ACTION, "--target", other, success=False)
        assert occupied["error"] == "native_guest_armor_slot_occupied", occupied
        saved = await inventory(client, character)
        assert jacket(saved, guid, "armor") == worn[guid]
        evidence["blocked_second_jacket"] = jacket(saved, other, "backpack")
        assert await inventory(host, "host") == host_before

        await stop(host, authority)
        await stop(client, guest)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
        renewed = await act(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        await enroll()

        def restored(s):
            return next((row for row in s.get("game", {}).get("native_guest_armor_projection", {}).values()
                         if row.get("ok") and row.get("guid") == guid and row.get("map") == OBSERVATORY
                         and row.get("status") in {"reapplied", "already_applied"} and effects(row)), None)

        restored_status = await wait(run, restored)
        evidence["restored_projection"] = restored(restored_status)
        assert await inventory(client, character) == saved
        assert await inventory(host, "host") == host_before
        assert (await act(client, *equip))["native_guest_armor"] == receipt
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / "live-guest-armor-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "The original single-player directory changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
