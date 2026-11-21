"""Open a stock secure locker with its physical key and test both Interact steps."""

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

from tools.silverfish import native_authored_pickup as pickup, native_host_save, native_loot as loot, native_world_loot
from tools.silverfish.authored_loot import full_name
from tools.silverfish.control import submit


async def verify(work, original):
    source = pickup.FORT_LETO_LOCKER_KEY
    area, _, _ = pickup.SOURCES[source]
    candidates = [full_name(area, f"LootLocker_Secure_BP_C_{number}") for number in (10, 14, 6)]
    crate = None
    profile = f"stock-secure-locker-{uuid.uuid4().hex[:8]}"
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

    async def stop_all():
        for run, process in reversed(processes):
            if process.returncode is None:
                try:
                    await submit(run, "stop", timeout=15)
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, TimeoutError, ValueError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        processes.clear()

    def section(frame, name):
        return frame.get("game", {}).get(name, {})

    def ready(frame):
        return section(frame, "native_loot_crates").get(crate, {}).get("state") == "ready"

    def lock(frame):
        return section(frame, loot.VEHICLE_LOCKS).get(crate)

    def locked_target(frame):
        locks = section(frame, loot.VEHICLE_LOCKS)
        crates = section(frame, "native_loot_crates")
        return next((target for target in candidates if locks.get(target) is True
                     and crates.get(target, {}).get("state") == "ready"), None)

    def roll(frame):
        return frame.get("gameplay", {}).get(loot.STATE_KEY, {}).get(crate)

    def claimed_here(frame, guid, character):
        if frame.get("state") == "hosting" and section(frame, "map") != area:
            raise RuntimeError("The party left Fort Leto before the locker pickup completed")
        return any(row["guid"] == guid and row.get("claimed_by") == character for row in (roll(frame) or {}).get("items", ()))

    def event(run, name, **keys):
        path = run / "events.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.endswith("}"):
                    row = json.loads(line)
                    if row.get("event") == name and all(row.get(key) == value for key, value in keys.items()):
                        return row

    def checkpoint():
        saved = json.loads((hrun.parent / "save" / "campaign.json").read_bytes())
        native_host_save.verify_checkpoint(hrun.parent / "game-user", saved)
        return saved["world"]["party"]

    async def inventory(run):
        return (await action(run, "native-inventory-list"))["native_inventory"]["items"]

    async def join(invite, label, authority):
        await start(grun, label, "join", *guest, "--invite", invite["invite_file"], "--headless", "--native-zones")
        await wait(grun, lambda frame: frame.get("state") == "connected" and frame.get("host_session") == authority["generation"]
                   and frame.get("character_id") == invite["character_id"] and ready(frame) and lock(frame) is lock(authority))
        await wait(hrun, lambda frame: section(frame, "native_guest_save").get("state") == "bound")

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    try:
        await start(hrun, "host", "host", *host, "--port", "0", *host_flags, "--start-zone", area)
        authority = await wait(hrun, lambda frame: frame.get("state") == "hosting" and locked_target(frame))
        crate = locked_target(authority)
        port = authority["endpoint"]["port"]
        invitation = await command("invite", *host, "--name", "Secure locker tester", "--character", "Secure locker tester")
        character = invitation["character_id"]
        await join(invitation, "guest", authority)

        await action(hrun, "native-loot-approach", target=crate)
        press = (await action(grun, "native-loot-crate-fixture", target=crate))["native_loot_crate_interaction"]
        await wait(grun, lambda _: event(grun, "native_stock_crate_interaction_rejected", sequence=press["sequence"],
                                        reason="stock_vehicle_locked", crate=crate))
        await wait(grun, lambda frame: section(frame, pickup.SOURCES_KEY).get(source, {}).get("state") == "available")
        await action(hrun, pickup.APPROACH, target="guest", pickup_source=source)
        key_press = await action(grun, pickup.STOCK_FIXTURE, pickup_source=source)
        assert key_press["native_authored_pickup_interaction"]["entry"] == "stock"
        await wait(hrun, lambda frame: source in frame.get("gameplay", {}).get(pickup.STATE_KEY, {}))
        key_record = checkpoint()[pickup.STATE_KEY][source]
        assert key_record["owner"] == character
        key_item, = [row for row in await inventory(grun) if row["guid"] == key_record["guid"]]
        assert all(row["class_path"] != key_item["class_path"] for row in await inventory(hrun))

        await action(hrun, "native-loot-approach", target=crate)
        press = (await action(grun, "native-loot-crate-fixture", target=crate))["native_loot_crate_interaction"]
        await wait(grun, lambda _: event(grun, "native_stock_crate_interaction", sequence=press["sequence"], crate=crate))
        unlocked = event(grun, "native_stock_crate_interaction", sequence=press["sequence"], crate=crate)
        expected = {"map": area, "crate": crate, "opener": character, "locked": False}
        assert unlocked[loot.UNLOCK_RESULT] == expected
        for run in (hrun, grun):
            await wait(run, lambda frame: ready(frame) and lock(frame) is False and roll(frame) is None)
        operation = unlocked["operation_id"]
        assert checkpoint()[loot.KEY_UNLOCKS][crate] == {"id": operation, "opener": character}
        assert (await action(grun, "native-loot-open", target=crate, operation_id=operation))[loot.UNLOCK_RESULT] == expected
        different = next(target for target in candidates if target != crate)
        conflict = await submit(grun, "native-loot-open", target=different, operation_id=operation)
        assert conflict.get("error") == "operation_id_conflict", conflict

        await stop_all()
        await start(hrun, "host-restart", "host", *host, "--port", str(port), *host_flags)
        resumed = await wait(hrun, lambda frame: frame.get("generation") != authority["generation"] and frame.get("state") == "hosting"
                             and ready(frame) and lock(frame) is False and roll(frame) is None)
        renewed = await command("action", *host, "refresh-invite", "--target", invitation["guest_id"])
        await join(renewed, "guest-restart", resumed)
        assert renewed["character_id"] == character and resumed["endpoint"]["port"] == port
        assert key_item in await inventory(grun)
        assert (await action(grun, "native-loot-open", target=crate, operation_id=operation))[loot.UNLOCK_RESULT] == expected

        await action(hrun, "native-loot-approach", target=f"host@{crate}")
        await action(hrun, "native-loot-crate-fixture", target=crate)
        opened = roll(await wait(hrun, lambda frame: roll(frame)))
        await wait(grun, lambda frame: roll(frame) == opened)
        assert opened["opener"] == "host" and opened["items"], opened
        assert crate not in checkpoint().get(loot.KEY_UNLOCKS, {})
        assert (await action(grun, "native-loot-open", target=crate, operation_id=operation))[loot.UNLOCK_RESULT] == expected

        selected = next(item for item in opened["items"] if item["class_path"] not in loot.CASH_RANGES)
        guid = selected["guid"]
        identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{opened['id']}:{guid}").hex
        await action(hrun, native_world_loot.APPROACH, target=f"guest@{identity}")
        for run in (hrun, grun):
            visible = await wait(run, lambda frame: identity in section(frame, native_world_loot.KEY))
            assert section(visible, native_world_loot.KEY)[identity]["class_path"] == selected["class_path"]
        await action(grun, native_world_loot.FIXTURE, target=identity)
        await wait(hrun, lambda frame: claimed_here(frame, guid, character))
        await wait(grun, lambda frame: identity not in section(frame, native_world_loot.KEY))
        collected, = [row for row in await inventory(grun) if row["guid"] == guid]
        assert collected["class_path"] == selected["class_path"] and collected["source"] == "backpack"
        assert key_item in await inventory(grun)
        proof = {"unlock": expected, "restart_port": port, "roll": opened, "claimed": collected,
                 "retained_key": key_item, "original_files_verified": len(originals)}
        checkpoint()
        (hrun / "secure-locker-verification.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print(json.dumps(proof), flush=True)
    finally:
        await stop_all()
        assert saved_hashes(original) == originals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
