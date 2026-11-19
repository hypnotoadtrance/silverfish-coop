"""Verify ordinary stock host LandMine deployment and world restoration."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_landmine as mine, native_host_save
from tools.silverfish.native_gold import valid_guid
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def verify(work: Path, vanilla: Path) -> None:
    name = "stock-host-landmine-" + uuid.uuid4().hex[:8]
    campaign, profile = ("--campaign", name), ("--profile", name)
    directory = work / "campaigns" / name
    logs = directory / "run"
    guest_logs = work / "clients" / name / "run"
    originals = saved_hashes(vanilla)
    if not originals:
        raise ValueError("Choose the populated original single-player Saved directory")
    logs.mkdir(parents=True)
    print(logs, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    guest_flags = ("--headless", "--native-zones")
    state, jobs = {}, []

    async def command(*arguments):
        child = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            raw, errors = await asyncio.wait_for(child.communicate(), 45)
        except TimeoutError:
            child.kill()
            await child.wait()
            raise
        if child.returncode or not raw:
            raise RuntimeError(f"{arguments[:4]}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def action(owner, verb, *arguments):
        return await command("action", *owner, verb, *arguments)

    async def launch(kind, owner, name, *arguments):
        with (logs / f"{name}.txt").open("wb") as output:
            child = await asyncio.create_subprocess_exec(*cli, kind, *owner, *arguments, cwd=REPO, stdout=output,
                stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, child))
        return child

    async def stop(owner, child):
        if child.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(child.wait(), 18) == 0, child.returncode

    async def paired(map_path, character, generation=None, epoch=None):
        host = await wait(logs, lambda packet: packet.get("state") == "hosting" and packet.get("generation") != generation
            and packet.get("zone_epoch") != epoch and packet.get("game", {}).get("map") == map_path
            and packet["game"].get("native_guest_save", {}).get("state") == "bound", timeout=68)
        guest = await wait(guest_logs, lambda packet: packet.get("state") == "connected" and packet.get("link_connected")
            and packet.get("character_id") == character and packet.get("host_session") == host["generation"]
            and packet.get("zone_epoch") == host["zone_epoch"] and packet.get("game", {}).get("map") == map_path, timeout=68)
        return host, guest

    async def cross():
        for role, position in (("host", "near"), ("guest", "near"), ("host", "inside")):
            await action(campaign, "native-exit-position", "--target", role, "--exit-position", position,
                         "--entrance", "Obsv_Etr")

    def verified_save():
        data = json.loads((directory / "save" / "campaign.json").read_text(encoding="utf-8"))
        native_host_save.verify_checkpoint(directory / "game-user", data)
        return data

    async def restored(previous):
        receipt = (await action(campaign, mine.INSPECT))[mine.KEY]
        assert (receipt["state"], receipt["saved"], receipt["actors"], receipt["remaining"], receipt["guest_remaining"],
                receipt["same_transform"]) == ("inspected", 1, 1, 0, 0, True), receipt
        assert math.dist(receipt["position"], previous["position"]) <= 10, (receipt, previous)
        assert receipt["actor"].startswith("Eqp_LandMine_C " + OBSERVATORY + ".Observitory_P:PersistentLevel."), receipt
        return receipt

    try:
        host = await launch("host", campaign, "host", "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(logs, lambda packet: packet.get("state") == "hosting")
        invite = await command("invite", *campaign, "--name", "World state tester", "--character", "Observer")
        guest = await launch("join", profile, "guest", "--invite", invite["invite_file"], *guest_flags)
        first, _ = await paired(OBSERVATORY, invite["character_id"])

        equipped = (await action(campaign, mine.PREPARE))[mine.KEY]
        assert equipped["state"] == "equipped" and valid_guid(equipped["guid"]) and equipped["remaining"] == 1, equipped
        state["stock_equipment"] = equipped
        operation = uuid.uuid4().hex
        requested = ("--target", equipped["guid"], "--operation-id", operation)
        deployed = (await action(campaign, mine.DEPLOY, *requested))[mine.KEY]
        assert (deployed["state"], deployed["presses"], deployed["releases"], deployed["actors"], deployed["saved"],
                deployed["remaining"], deployed["guest_remaining"], deployed["same_transform"]) == (
                "persisted", 1, 1, 1, 1, 0, 0, True), deployed
        assert deployed["projectile"].startswith("LandMine_Projectile_BP_C " + OBSERVATORY), deployed
        assert (await action(campaign, mine.DEPLOY, *requested))[mine.KEY] == deployed
        assert any(receipt["id"] == operation and receipt["result"][mine.KEY] == deployed
                   for receipt in verified_save()["world"]["party"]["receipts"]["host"])
        state.update(deployed=deployed, idempotent_deploy=True)

        await cross()
        field, _ = await paired(CROSSROADS, invite["character_id"], epoch=first["zone_epoch"])
        await cross()
        returned, _ = await paired(OBSERVATORY, invite["character_id"], epoch=field["zone_epoch"])
        state["stock_travel_return"] = await restored(deployed)

        await stop(campaign, host)
        await stop(profile, guest)
        verified_save()
        port = first["endpoint"]["port"]
        await launch("host", campaign, "host-restart", "--port", str(port), *host_flags)
        await wait(logs, lambda packet: packet.get("state") == "hosting" and packet.get("generation") != returned["generation"])
        refreshed = await action(campaign, "refresh-invite", "--target", invite["guest_id"])
        await launch("join", profile, "guest-restart", "--invite", refreshed["invite_file"], *guest_flags)
        after, _ = await paired(OBSERVATORY, invite["character_id"], generation=returned["generation"])
        assert after["endpoint"]["port"] == port, after
        state.update(same_port_restart=await restored(deployed), verified=True)
    except BaseException as error:
        state.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (RuntimeError, OSError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        state["original_saves_verified"] = saved_hashes(vanilla) == originals
        (logs / "live-host-landmine-verification.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(state), flush=True)
        assert state["original_saves_verified"], "The original single-player save files changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
