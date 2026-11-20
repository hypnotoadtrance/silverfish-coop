"""Verify one scoped, durable native guest stock 9mm drop and reclaim."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record, native_guest_stack_drop as probe
from tools.silverfish import native_host_save


async def verify(work: Path, originals_path: Path):
    name = "manual-stack-drop-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run = campaign / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    host = ("--campaign", name)
    owner, other = ("--profile", name), ("--profile", name + "-other")
    clients = {target: work / "clients" / target[1] / "run" for target in (owner, other)}
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--max-guests", "2",
             "--start-zone", record.OBSERVATORY)
    originals = saved_hashes(originals_path)
    evidence, jobs = {}, []

    async def command(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, error = await asyncio.wait_for(process.communicate(), 50)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if (process.returncode == 0) != success or not output:
            raise RuntimeError(f"{arguments[:4]} returned {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def action(target, verb, *arguments, success=True):
        return await command("action", *target, verb, *arguments, success=success)

    async def start(label, kind, target, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *target, *arguments, cwd=REPO, stdout=log,
                                                            stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((target, process))
        return process

    async def stop(target, process):
        if process.returncode is None:
            await action(target, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0

    async def bound(count):
        return await wait(run, lambda state: state.get("state") == "hosting"
                          and state.get("game", {}).get("remote_clients") == count
                          and state["game"].get(probe.CAPABILITY) == 1
                          and len(state["game"].get("native_guest_save_members", {})) == count
                          and all(row.get("state") == "bound" for row in state["game"]["native_guest_save_members"].values()))

    async def inventory(target, character):
        page = (await action(target, "native-inventory-list"))["native_inventory"]
        assert page["owner"] == character and page["offset"] == 0 and page["next_offset"] is None, page
        return page["items"]

    def saved():
        checkpoint = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(campaign / "game-user", checkpoint)
        return checkpoint["world"]["party"][record.STATE]

    def location():
        now = status(run)
        pawn = now.get("gameplay", {}).get("native_pawns", {}).get("guest")
        return next((row["location"] for row in now.get("game", {}).get("pawns", {}).values()
                     if row.get("name") == pawn and record.valid_position(row.get("location"))), None)

    async def move(anchor, *, away):
        for direction in range(6):
            current = location()
            if not current:
                await asyncio.sleep(0.35)
                continue
            distance = math.dist(current, anchor)
            if (away and distance > 320) or (not away and distance < 170):
                return current
            dx, dy = anchor[0] - current[0], anchor[1] - current[1]
            length = math.hypot(dx, dy)
            if away:
                dx, dy = ((-dx, -dy) if length > 5 and direction < 3 else ((1, 0), (0, 1), (-1, 0))[direction % 3])
                length = math.hypot(dx, dy)
            if length <= 5:
                break
            duration = 700 if away else max(100, min(700, int((length - 65) * 2)))
            await action(owner, "walk", "--x", str(dx / length), "--y", str(dy / length), "--milliseconds", str(duration))
            await asyncio.sleep(duration / 1000 + 0.35)
        raise RuntimeError(f"The stock guest did not move {'outside' if away else 'inside'} reclaim range: {location()}")

    try:
        server = await start("host", "host", host, "--port", "0", *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get(probe.CAPABILITY) == 1)
        invite = await command("invite", *host, "--name", "Drop owner", "--character", "Drop owner")
        owner_id = invite["character_id"]
        player = await start("owner", "join", owner, "--invite", invite["invite_file"], "--headless", "--native-zones")
        initial = await bound(1)
        spare = await command("invite", *host, "--name", "Non-owner", "--character", "Non-owner")
        outsider = await start("non-owner", "join", other, "--invite", spare["invite_file"], "--headless", "--native-zones")
        await bound(2)
        for target in clients:
            await wait(clients[target], lambda state: state.get("state") == "connected")

        source, attempt, claim_id = uuid.uuid4().hex.upper(), uuid.uuid4().hex, uuid.uuid4().hex
        host_items, other_items = await inventory(host, "host"), await inventory(other, spare["character_id"])
        await action(host, "native-guest-weapon-seed-ammo", "--target", "guest", "--item-guid", source)
        initial_items = await inventory(owner, owner_id)
        typed, = (item for item in initial_items if item["guid"] == source)
        assert (typed["source"], typed["class_path"], typed["quantity"]) == ("backpack", record.ITEM, 9), typed
        request = (drop.DROP, "--target", f"{source}|9", "--operation-id", attempt)
        dropped = (await action(owner, *request))[drop.RESULT]
        evidence["drop"] = dropped
        assert (dropped["state"], dropped["drop_id"], dropped["owner"], dropped["guid"], dropped["quantity"]) == (
            "dropped", attempt, owner_id, source, 9), dropped
        assert record.valid_position(dropped["location"])
        assert (await action(owner, *request))[drop.RESULT] == dropped
        assert all(item["guid"] != source for item in await inventory(owner, owner_id))
        committed = evidence["durable_escrow"] = saved()[attempt]
        assert "escrow" in committed and committed["claim_id"] is None and "actor" not in committed, committed
        assert (await action(other, drop.RECLAIM, "--target", attempt, success=False))["error"] == "native_guest_stack_drop_not_owned"
        assert await inventory(other, spare["character_id"]) == other_items
        assert await inventory(host, "host") == host_items

        await stop(host, server)
        await stop(owner, player)
        await stop(other, outsider)
        await start("host-restart", "host", host, "--port", str(initial["endpoint"]["port"]), *flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != initial["generation"])
        renewed = await action(host, "refresh-invite", "--target", invite["guest_id"])
        await start("owner-restart", "join", owner, "--invite", renewed["invite_file"], "--headless", "--native-zones")
        await bound(1)
        owner_state = await wait(clients[owner], lambda state: state.get("state") == "connected"
                                 and state.get("character_id") == owner_id and attempt in state.get("gameplay", {}).get(record.STATE, {}))
        assert owner_state["gameplay"][record.STATE][attempt]["guid"] == source
        assert saved()[attempt] == committed
        assert all(item["guid"] != source for item in await inventory(owner, owner_id))
        assert (await action(owner, *request))[drop.RESULT] == dropped

        anchor = dropped["location"]
        evidence["out_of_range_position"] = await move(anchor, away=True)
        refused = await action(owner, drop.RECLAIM, "--target", attempt, success=False)
        assert refused["error"] == "native_guest_stack_drop_out_of_range", refused
        evidence["reclaim_position"] = await move(anchor, away=False)
        recover = (drop.RECLAIM, "--target", attempt, "--operation-id", claim_id)
        reclaimed = (await action(owner, *recover))[drop.RESULT]
        evidence["reclaim"] = reclaimed
        assert reclaimed == {**dropped, "state": "reclaimed"}, reclaimed
        assert saved()[attempt] == {key: value if key != "claim_id" else claim_id for key, value in committed.items() if key != "escrow"}
        assert await inventory(owner, owner_id) == initial_items and await inventory(host, "host") == host_items
        assert (await action(owner, *recover))[drop.RESULT] == reclaimed
        assert (await action(owner, drop.RECLAIM, "--target", attempt, success=False))["error"] == "native_guest_stack_drop_already_reclaimed"
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for target, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(target, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["original_saves_verified"] = saved_hashes(originals_path) == originals
        (run / "live-guest-stack-drop-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    settings = parser.parse_args()
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved.resolve()))
