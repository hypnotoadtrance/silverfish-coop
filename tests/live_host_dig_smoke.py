"""Observe a real stock CrossRoads spade dig and prove its isolated save."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_dig as dig, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS


async def verify(work: Path, vanilla_saved: Path) -> None:
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    name = "stock-host-dig-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run, guest_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_args = ("--campaign", name, "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    guest_args = ("--profile", name, "--headless", "--native-zones")
    evidence, jobs = {"verified": False}, []

    async def action(directory, verb, *, accepted=True, **request):
        value = await submit(directory, verb, **request)
        assert value.get("ok") is accepted, (verb, value)
        return value

    async def launch(directory, label, *args):
        with (run / f"{label}.txt").open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=output, stderr=output,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((directory, process))
        return process

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
            await asyncio.wait_for(process.wait(), 20)
        assert process.returncode == 0

    async def paired(character, prior=None):
        host = await wait(run, lambda doc: doc.get("state") == "hosting" and doc.get("generation") != prior
                          and doc.get("link_connected") is True and doc.get("game", {}).get("map") == CROSSROADS
                          and doc["game"].get("native_guest_save", {}).get("state") == "bound", timeout=75)
        await wait(guest_run, lambda doc: doc.get("state") == "connected" and doc.get("link_connected") is True
                   and doc.get("character_id") == character and doc.get("host_session") == host["generation"]
                   and doc.get("zone_epoch") == host["zone_epoch"] and doc.get("game", {}).get("map") == CROSSROADS, timeout=75)
        return host

    def flags(character):
        save = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        profile = campaign / "game-user"
        native_host_save.verify_checkpoint(profile, save)
        authority = save["world"]["native_host_save"]
        detached = save["characters"][character]["state"]["native_guest_save"]
        assert authority["slot"] != detached["slot"], (authority, detached)
        directory = native_host_save.directory(profile)
        return stock_flags(directory, authority, CROSSROADS), stock_flags(directory, detached, CROSSROADS)

    async def rows(directory):
        inventory = (await action(directory, "native-inventory-list"))["native_inventory"]
        assert inventory["next_offset"] is None
        return {row["guid"]: row for row in inventory["items"]}

    try:
        authority = await launch(run, "host", "host", *host_args, "--port", "0", "--start-zone", CROSSROADS)
        await wait(run, lambda payload: payload.get("state") == "hosting")
        invitation = await action(run, "invite", name="Stock dig observer", character="Observer")
        peer = await launch(guest_run, "guest", "join", *guest_args, "--invite", invitation["invite_file"])
        initial = await paired(invitation["character_id"])
        await action(run, "checkpoint")
        baseline = flags(invitation["character_id"])
        guest_items = await rows(guest_run)
        assert all(dig.FLAG not in player for player in baseline), baseline

        acquired = (await action(run, dig.ACQUIRE))[dig.KEY]
        assert acquired["state"] == "equipped" and acquired["stock_f"] > 0 and acquired["flag_value"] == 0, acquired
        tool = acquired["guid"]
        assert (await rows(run))[tool]["class_path"] == dig.SPADE_CLASS
        evidence["stock_spade"] = acquired

        operation = uuid.uuid4().hex
        found = (await action(run, dig.DIG, target=tool, operation_id=operation))[dig.KEY]
        assert (found["state"], found["source"], found["flag"], found["flag_value"], found["holes"]) == (
            "collected", dig.SOURCE, dig.FLAG, 1, 1), found
        assert found["incidental"] in (0, 1) and found["stock_f"] != acquired["stock_f"], found
        native_rows = await rows(run)
        package = native_rows[found["package_guid"]]
        assert (package["class_path"], package["source"], package["quantity"], package["stackable"]) == (
            dig.PACKAGE_CLASS, "backpack", 1, False), package
        assert await rows(guest_run) == guest_items
        settled = flags(invitation["character_id"])
        assert settled == (dict(baseline[0], **{dig.FLAG: 1}), baseline[1]), (settled, baseline)
        evidence["stock_dig"] = found
        evidence["package"] = package

        await stop(run, authority)
        await stop(guest_run, peer)
        await launch(run, "host-restart", "host", *host_args, "--port", str(initial["endpoint"]["port"]))
        await wait(run, lambda payload: payload.get("state") == "hosting" and payload.get("generation") != initial["generation"])
        renewed = await action(run, "refresh-invite", target=invitation["guest_id"])
        await launch(guest_run, "guest-restart", "join", *guest_args, "--invite", renewed["invite_file"])
        restarted = await paired(invitation["character_id"], initial["generation"])
        restored = (await action(run, dig.INSPECT))[dig.KEY]
        assert (restored["state"], restored["package_guid"], restored["guid"], restored["flag_value"]) == (
            "restored", found["package_guid"], tool, 1), restored
        assert restarted["endpoint"]["port"] == initial["endpoint"]["port"]
        assert (await action(run, dig.DIG, target=tool, operation_id=operation))[dig.KEY] == found
        denied = await action(run, dig.DIG, target=tool, accepted=False)
        assert denied["error"] == "stock_host_dig_source_already_secured", denied
        assert await rows(guest_run) == guest_items
        assert (await rows(run))[found["package_guid"]] == package
        evidence.update(verified=True, restart=restored)
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is not None:
                continue
            try:
                await stop(directory, process)
            except (OSError, RuntimeError, AssertionError, TimeoutError):
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (run / "live-stock-host-dig-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
