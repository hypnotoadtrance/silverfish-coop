"""Check the stock offer and authenticated acceptance of Dead Bridge turret photography."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"
QUEST = "Photo_DB_Turrets"


async def validate(work: Path, vanilla_saved: Path | None):
    profile = "turret-photo-" + uuid.uuid4().hex[:8]
    host = ("--campaign", profile)
    guest = ("--profile", profile)
    run = work / "campaigns" / profile / "run"
    guest_run = work / "clients" / profile / "run"
    run.mkdir(parents=True, exist_ok=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    evidence = {}

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(process.communicate(), 35)
        result = json.loads(output)
        if result.get("ok", True) != expected or bool(process.returncode) == expected:
            raise RuntimeError(f"{arguments}: {result} {errors.decode(errors='replace')}")
        return result

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def enroll():
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == BOROUGH)
        await wait(run, lambda state: state.get("state") == "hosting"
                   and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        await command("action", *host, "native-wire-quest-approach", "--target", "guest")

    common = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    try:
        authority = await launch(host, "host", "host", *host, "--start-zone", BOROUGH, "--port", "0", *common)
        ready = await wait(run, lambda state: state.get("state") == "hosting")
        port = ready["endpoint"]["port"]
        invite = await command("invite", *host, "--name", "Dead Bridge photographer", "--character", "Photographer")
        await launch(guest, "guest", "join", *guest, "--headless", "--native-zones", "--invite", invite["invite_file"])
        await enroll()
        refused = await command("action", *guest, "native-turret-photo-preflight", expected=False)
        assert refused["error"] == "stock_turret_photo_quest_not_offered", refused
        evidence["stock_offer_at_zero_xp"] = refused["error"]
        staged = (await command("action", *host, "native-turret-photo-xp-fixture"))["native_turret_photo"]
        assert (staged["xp_before"], staged["xp_after"]) == (0, 4), staged
        evidence["stock_xp_fixture"] = staged
        offered = (await command("action", *guest, "native-turret-photo-preflight"))["native_turret_photo"]
        assert (offered["step_before"], offered["step_after"], offered["xp_after"]) == (None, None, 4), offered
        evidence["stock_board_offer"] = offered
        accepted = await command("action", *guest, "native-quest-start", "--target", QUEST)
        assert (accepted["quest_id"], accepted["step"], accepted["actor"], accepted["photos"]) == (
            QUEST, 1, invite["character_id"], {}
        ), accepted
        evidence["stock_acceptance"] = accepted
        early = await command("action", *guest, "native-quest-turnin", "--target", QUEST, expected=False)
        assert early["error"] == "native_turret_photo_objectives_incomplete", early
        evidence["incomplete_turn_in_denied"] = early["error"]
        await command("action", *host, "stop")
        await asyncio.wait_for(authority.wait(), 15)
        await launch(host, "host-restart", "host", *host, "--port", str(port), *common)
        await enroll()
        restored = (await command("action", *guest, "native-turret-photo-preflight"))["native_turret_photo"]
        assert (restored["step_before"], restored["step_after"], restored["xp_after"]) == (1, 1, 4), restored
        evidence.update(stock_restart=restored, offer_acceptance_verified=True)
    except BaseException as error:
        evidence.update(offer_acceptance_verified=False, failure=str(error))
        raise
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        (run / "live-turret-photo-offer.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    arguments = options.parse_args()
    asyncio.run(validate(arguments.work.resolve(), arguments.vanilla_saved))
