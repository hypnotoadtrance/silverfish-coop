"""Verify typed extraction penalties and owner-only recovery on a staged Observatory host."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"


async def validate(work: Path, vanilla_saved: Path | None) -> None:
    name = "stock-wipe-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True, exist_ok=True)
    guest.mkdir(parents=True, exist_ok=True)
    primary = ("--campaign", name)
    secondary = ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    processes = []

    async def start(log, owner, *arguments):
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=output, stderr=output,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 42)
        if not output or bool(process.returncode) == expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, operation, *arguments, expected=True):
        return await command("action", *owner, operation, *arguments, expected=expected)

    async def wait(directory, predicate):
        deadline = time.monotonic() + 40
        last = None
        while time.monotonic() < deadline:
            try:
                last = json.loads((directory / "status.json").read_text(encoding="utf-8"))
                if predicate(last):
                    return last
                if last.get("state") == "failed":
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(.15)
        raise RuntimeError(f"{directory} did not reach the expected state: {last}")

    try:
        await start(host / "host.txt", primary, "host", *primary, "--port", "0", "--headless", "--fixtures",
                    "--fixture-keep-slots", "1", "--fixture-backpacks", "--native-events", "--native-guest",
                    "--native-zones", "--start-zone", OBSERVATORY)
        await wait(host, lambda status: status.get("state") == "hosting"
                   and status["game"].get("native_rest_hook", {}).get("fresh"))
        await action(primary, "native-rest-approach")
        rest = (await action(primary, "native-rest", "--amount", "0"))["native_rest"]
        invitation = await command("invite", *primary, "--name", "Native wipe guest", "--character", "Native wipe character")
        await start(guest / "guest.txt", secondary, "join", *secondary, "--headless", "--native-zones",
                    "--invite", invitation["invite_file"])
        await wait(guest, lambda status: status.get("state") == "connected")
        await wait(host, lambda status: status.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        character = invitation["character_id"]
        gun, reserve = (uuid.uuid4().hex.upper() for _ in range(2))
        await action(primary, "native-guest-weapon-seed", "--item-guid", gun)
        await action(secondary, "native-guest-weapon-equip", "--target", gun)
        await action(primary, "native-guest-weapon-seed-ammo", "--item-guid", reserve)
        await action(primary, "native-guest-damage", "--target", "guest")
        await action(primary, "native-host-damage", "--target", "host")
        await wait(host, lambda status: status.get("gameplay", {}).get("wipe", {}).get("keep_slots") == 1
                   if status.get("gameplay", {}).get("wipe") else False)
        draft = None
        for attempt in range(20):
            frame = await action(secondary, "native-wipe-items", expected=None)
            if frame.get("ok"):
                draft = frame["native_wipe_items"]
                break
            await asyncio.sleep(.2)
        assert draft and {item["guid"] for item in draft["items"]} == {gun, reserve}, draft
        await action(secondary, "keep", "--keep", f"{gun}=1")
        await action(primary, "keep")
        revived = await wait(host, lambda status: status.get("state") == "hosting"
                             and status.get("gameplay", {}).get("native_backpacks")
                             and status["gameplay"].get("wipe") is None and not status["gameplay"].get("pending_spawns"))
        bag_id, = revived["gameplay"]["native_backpacks"]
        bag = revived["gameplay"]["native_backpacks"][bag_id]
        assert bag["owner_id"] == character and bag["count"] == 1 and bag["position"]["map"] == OBSERVATORY, bag
        denied = await action(primary, "native-wipe-recover", "--target", bag_id, expected=False)
        assert denied["error"] == "native_wipe_bag_not_owned", denied
        after_wipe = (await action(secondary, "native-inventory-list"))["native_inventory"]
        assert [row["guid"] for row in after_wipe["items"]] == [gun], after_wipe
        operation = uuid.uuid4().hex
        recover = ("native-wipe-recover", "--target", bag_id, "--operation-id", operation)
        recovered = await action(secondary, *recover)
        assert recovered["count"] == 1 and (await action(secondary, *recover))["native_bag"] == bag_id, recovered
        after_recovery = (await action(secondary, "native-inventory-list"))["native_inventory"]
        assert {row["guid"] for row in after_recovery["items"]} == {gun, reserve}, after_recovery
        proof = {"rest": rest, "retained": after_wipe, "backpack": bag, "recovered": after_recovery}
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            assert saved_hashes(vanilla_saved) == original
        print(host, flush=True)
    (host / "live-stock-wipe-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--work", required=True, type=Path)
    arguments.add_argument("--vanilla-saved", type=Path)
    options = arguments.parse_args()
    asyncio.run(validate(options.work.resolve(), options.vanilla_saved))
