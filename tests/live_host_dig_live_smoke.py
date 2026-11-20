"""Verify ordinary stock dig escrow through an isolated same-port host restart."""

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

from tools.silverfish import native_host_dig as stock, native_host_dig_live as dig
from tools.silverfish import native_host_dig_live_fixture as fixture, native_host_save
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS


async def verify(work: Path, vanilla_saved: Path) -> None:
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    name = "stock-host-dig-live-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    host_run, guest_run = campaign / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_args = ("host", "--campaign", name, "--headless", "--fixtures", "--native-events",
                 "--native-guest", "--native-zones", "--native-host-dig-live-fixtures")
    guest_args = ("join", "--profile", name, "--headless", "--native-zones")
    evidence, jobs = {"verified": False}, []

    async def action(directory, verb, *, accepted=True, **fields):
        result = await submit(directory, verb, **fields)
        assert result.get("ok") is accepted, (verb, result)
        return result

    async def launch(directory, label, args, *extra):
        with (host_run / f"{label}.txt").open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, *args, *extra, cwd=REPO, stdout=output,
                                                            stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((directory, process))
        return process

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
            await asyncio.wait_for(process.wait(), 20)
        assert process.returncode == 0, process.returncode

    async def paired(character, phase):
        host = await wait(host_run, lambda report: report.get("state") == "hosting"
                          and report.get("link_connected") is True and report["game"].get("map") == CROSSROADS
                          and report["game"].get("native_guest_save", {}).get("state") == "bound"
                          and (phase is None or report["game"].get(dig.KEY, {}).get("state") == phase), timeout=75)
        await wait(guest_run, lambda report: report.get("state") == "connected" and report.get("link_connected") is True
                   and report.get("character_id") == character and report.get("host_session") == host["generation"]
                   and report.get("zone_epoch") == host["zone_epoch"] and report["game"].get("map") == CROSSROADS,
                   timeout=75)
        return host

    def checkpoint(character):
        document = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        profile = campaign / "game-user"
        native_host_save.verify_checkpoint(profile, document)
        host = document["world"]["native_host_save"]
        guest = document["characters"][character]["state"]["native_guest_save"]
        directory = native_host_save.directory(profile)
        assert host["slot"] != guest["slot"]
        return document["world"]["party"], stock_flags(directory, host, CROSSROADS), stock_flags(directory, guest, CROSSROADS)

    async def inventory(directory):
        items = (await action(directory, "native-inventory-list"))["native_inventory"]
        assert items["next_offset"] is None
        return {row["guid"]: row for row in items["items"]}

    async def restart_host(port, previous, label):
        process = await launch(host_run, label, host_args, "--port", str(port))
        await wait(host_run, lambda report: report.get("state") == "hosting" and report.get("generation") != previous)
        renewed = await action(host_run, "refresh-invite", target=invitation["guest_id"])
        guest = await launch(guest_run, label + "-guest", guest_args, "--invite", renewed["invite_file"])
        return process, guest

    async def input_for(target):
        return await action(host_run, fixture.ACTION, target=target)

    try:
        first = await launch(host_run, "host", host_args, "--port", "0", "--start-zone", CROSSROADS)
        await wait(host_run, lambda report: report.get("state") == "hosting")
        invitation = await action(host_run, "invite", name="Stock custody observer", character="Observer")
        character = invitation["character_id"]
        guest1 = await launch(guest_run, "guest", guest_args, "--invite", invitation["invite_file"])
        initial = await paired(character, "ready")
        port = initial["endpoint"]["port"]
        await action(host_run, "checkpoint")
        baseline, original_flags, guest_flags = checkpoint(character)
        independent_items = await inventory(guest_run)
        assert dig.CUSTODY not in baseline and stock.FLAG not in original_flags, original_flags

        tool = (await input_for("acquire"))[stock.KEY]
        assert tool["state"] == "equipped" and tool["stock_f"] > 0 and tool["spade_source"], tool
        assert (await inventory(host_run))[tool["guid"]]["class_path"] == stock.SPADE_CLASS
        evidence["stock_spade"] = tool
        loose_result = await input_for("press")
        report, escrow = loose_result[dig.KEY], loose_result[dig.CUSTODY]
        assert (report["state"], report["spade_guid"], report["escrow_guid"], report["package_guid"]) == (
            "escrowed", tool["guid"], escrow["package_guid"], None), loose_result
        assert report["incidental"] in (0, 1) and report["serial"] > 0 and report["actor"]
        assert escrow["state"] == "escrowed" and escrow["flag_value"] == 1
        ledger, buried_flags, guest_after = checkpoint(character)
        assert ledger[dig.CUSTODY] == escrow and buried_flags[stock.FLAG] == 1 and guest_after == guest_flags
        assert buried_flags.get("Artifact_Value", 0) == original_flags.get("Artifact_Value", 0)
        assert all(row["class_path"] != stock.PACKAGE_CLASS for row in (await inventory(host_run)).values())
        assert await inventory(guest_run) == independent_items
        frame = dig.escrow_frames(ledger)
        assert frame == [escrow["escrow"]] and frame[0]["slot"] == report["slot"]
        evidence.update(ordinary_dig=report, loose_escrow=escrow)
        await stop(host_run, first)
        await stop(guest_run, guest1)

        second, guest2 = await restart_host(port, initial["generation"], "host-recover-loose")
        restarted = await paired(character, "escrowed")
        restored = restarted["game"][dig.KEY]
        assert restarted["endpoint"]["port"] == port and restored["actor"] and restored["package_guid"] is None
        assert (restored["escrow_guid"], restored["spade_guid"], restored["contact"], restored["material"]) == (
            escrow["package_guid"], tool["guid"], escrow["contact"], escrow["material"]), restored
        assert await inventory(guest_run) == independent_items
        evidence["restored_loose"] = restored

        result = await input_for("collect")
        collected, owned = result[dig.KEY], result[dig.CUSTODY]
        assert collected["state"] == "owned" and collected["actor"] == restored["actor"] and owned["state"] == "owned"
        assert collected["package_guid"] == owned["package_guid"] and collected["escrow_guid"] == escrow["package_guid"]
        package = (await inventory(host_run))[owned["package_guid"]]
        assert (package["class_path"], package["source"], package["quantity"], package["stackable"]) == (
            stock.PACKAGE_CLASS, "backpack", 1, False), package
        denied = await action(host_run, fixture.ACTION, accepted=False, target="collect")
        assert denied["error"] == "stock_live_dig_fixture_custody_unavailable", denied
        post, collected_flags, guest_after = checkpoint(character)
        actor_flag = f"{restored['actor'].rsplit('.', 1)[-1]}-CrossRoads_P-"
        assert post[dig.CUSTODY] == owned and not dig.escrow_frames(post) and guest_after == guest_flags
        assert collected_flags[stock.FLAG] == collected_flags[actor_flag] == 1
        assert collected_flags["Artifact_Value"] == original_flags.get("Artifact_Value", 0) + 50
        assert await inventory(guest_run) == independent_items
        evidence.update(ordinary_collect=collected, owned=owned, stock_package=package, denied_duplicate=denied)
        await stop(host_run, second)
        await stop(guest_run, guest2)

        await restart_host(port, restarted["generation"], "host-recover-owned")
        ended = await paired(character, "ready")
        final, final_flags, final_guest = checkpoint(character)
        assert ended["endpoint"]["port"] == port and ended["game"][dig.KEY]["actor"] is None
        assert final[dig.CUSTODY] == owned and final_flags == collected_flags and final_guest == guest_flags
        assert await inventory(guest_run) == independent_items
        all_items = await inventory(host_run)
        assert [row for row in all_items.values() if row["class_path"] == stock.PACKAGE_CLASS] == [package]
        evidence.update(verified=True, restored_owned=final[dig.CUSTODY])
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (OSError, RuntimeError, AssertionError, TimeoutError):
                    process.terminate()
                    await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (host_run / "live-stock-host-dig-custody-verification.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
