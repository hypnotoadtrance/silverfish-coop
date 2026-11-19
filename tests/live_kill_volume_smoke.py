"""Cross the real CrossRoads KillVolume and revive the relocated party pawn."""

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

from tools.silverfish import native_kill_volume as volume
from tools.silverfish.travel import CROSSROADS


async def verify(work, vanilla, both):
    name = "stock-kill-volume-" + uuid.uuid4().hex[:8]
    authority, client = ("--campaign", name), ("--profile", name)
    run, guest = work / "campaigns" / name / "run", work / "clients" / name / "run"
    original = saved_hashes(vanilla)
    if not original:
        raise ValueError("Choose the populated original single-player Saved directory")
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    setup = ("--headless", "--native-zones")
    host_flags = (*setup, "--fixtures", "--native-events", "--native-guest", "--native-expanded-zones")
    evidence, jobs = {"verified": False}, []

    async def command(*arguments):
        child = await asyncio.create_subprocess_exec(*cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, errors = await asyncio.wait_for(child.communicate(), 35)
        except TimeoutError:
            child.kill()
            await child.wait()
            raise
        if child.returncode or not output:
            raise RuntimeError(f"{arguments[:3]}: {output.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(output)

    async def action(owner, verb, *args):
        return await command("action", *owner, verb, *args)

    async def launch(mode, owner, label, *args):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, mode, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))
        return process

    async def stop(owner, process):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 20) == 0, process.returncode

    async def ready(character, prior=None):
        frame = await wait(run, lambda value: value.get("state") == "hosting" and value.get("generation") != prior
            and value["game"].get("native_guest_save", {}).get("state") == "bound"
            and value["game"].get(volume.KEY, {}).get("state") == "armed", timeout=65)
        peer = await wait(guest, lambda value: value.get("state") == "connected" and value.get("host_session") == frame["generation"]
            and value.get("zone_epoch") == frame["zone_epoch"] and value.get("character_id") == character
            and value["game"].get(volume.KEY, {}).get("state") == "armed", timeout=65)
        return frame, peer

    async def fall_and_revive(role, first, opposing_control, character):
        victim_slot, helper_slot = ("local", "guest") if role == "host" else ("guest", "local")
        victim = first["game"]["pawns"][victim_slot]
        helper = first["game"]["pawns"][helper_slot]
        driver = first["game"]["native_roster"]
        baseline = first["game"][volume.KEY]
        previous = first["game"].get(volume.RECOVERED, {}).get(victim["name"], {}).get("sequence", 0)
        drop = await action(authority, volume.FIXTURE, "--target", role)
        assert (drop.get("pawn"), drop.get("volume"), drop.get("state")) == (victim["name"], volume.VOLUME, "falling"), drop
        down = await wait(run, lambda value: value.get("game", {}).get("player_effects", {}).get(victim_slot, {}).get("state") == "downed"
            and value["game"].get(volume.RECOVERED, {}).get(victim["name"], {}).get("sequence", 0) > previous)
        report = down["game"][volume.KEY]
        recovery = down["game"][volume.RECOVERED][victim["name"]]
        assert (down["game"]["map"], down["zone_epoch"], down["generation"], down["game"]["native_roster"]) == (
            CROSSROADS, first["zone_epoch"], first["generation"], driver), down["game"]
        assert report["overlaps_vetoed"] > baseline["overlaps_vetoed"] and report["fault"] is None and down["link_connected"], report
        assert recovery["placement"] == "teammate" and math.dist(recovery["location"], helper["location"]) <= 250, recovery
        assert down["game"]["pawns"][victim_slot]["health"] == 0 and down["game"]["native_dead_flags"][victim_slot] is False
        await wait(guest, lambda value: value.get("state") == "connected" and value.get("link_connected") and value.get("character_id") == character
            and value.get("zone_epoch") == first["zone_epoch"]
            and value.get("game", {}).get("player_effects", {}).get("local" if role == "guest" else "guest", {}).get("state") == "downed")
        press = await action(opposing_control, "native-interaction-fixture")
        assert press["native_interaction_fixture"], press
        revived = await wait(run, lambda value: value.get("game", {}).get("player_effects", {}).get(victim_slot, {}).get("state") == "standing"
            and value["game"]["pawns"][victim_slot]["health"] > 0 and value.get("zone_epoch") == first["zone_epoch"])
        actual = revived["game"]["pawns"][victim_slot]
        assert actual["name"] == victim["name"] and abs(actual["health"] - victim["max_health"] * .10) < 1, actual
        assert revived["game"]["pawns"][helper_slot]["health"] == helper["health"]
        evidence[role] = {"recovery": recovery, "guard": report, "revived_health": actual["health"], "stock_interact": True}
        return revived

    try:
        host_proc = await launch("host", authority, "host", "--port", "0", "--start-zone", CROSSROADS, *host_flags)
        await wait(run, lambda value: value.get("state") == "hosting")
        invitation = await command("invite", *authority, "--name", "Void test peer", "--character", "Surveyor")
        character = invitation["character_id"]
        guest_proc = await launch("join", client, "guest", "--invite", invitation["invite_file"], *setup)
        first, _ = await ready(character)
        revived = await fall_and_revive("guest", first, authority, character)
        if both:
            revived = await fall_and_revive("host", revived, client, character)
        await action(authority, "checkpoint")
        await stop(authority, host_proc)
        await stop(client, guest_proc)
        port = first["endpoint"]["port"]
        await launch("host", authority, "host-restart", "--port", str(port), *host_flags)
        await wait(run, lambda value: value.get("state") == "hosting" and value.get("generation") != first["generation"])
        fresh = await action(authority, "refresh-invite", "--target", invitation["guest_id"])
        await launch("join", client, "guest-restart", "--invite", fresh["invite_file"], *setup)
        after, _ = await ready(character, first["generation"])
        assert after["endpoint"]["port"] == port and after["zone_epoch"] != revived["zone_epoch"]
        assert after["game"]["map"] == CROSSROADS and not after["game"].get(volume.RECOVERED)
        assert after["game"][volume.KEY]["overlaps_vetoed"] == 0 and not after["game"]["native_dead_flags"]["guest"]
        evidence.update(restart=True, verified=True)
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
                        process.kill()
                    await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla) == original
        evidence["verified"] &= evidence["original_saves_verified"]
        (run / "live-kill-volume-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--both", action="store_true", help="verify host fall and guest revival too")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), args.both))
