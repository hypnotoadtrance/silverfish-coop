"""Verify original primary and reload inputs with exact stock 9mm weapons."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from tools.silverfish.guest_weapon_profiles import FIREARMS


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
WEAPONS = {profile.kind: profile for profile in FIREARMS if profile.ball_magazine and profile.headless_seed}
DEFAULT_WEAPONS = ("light-pistol-suppressed", "smg-standard")


async def verify(work, vanilla_saved, kinds=DEFAULT_WEAPONS):
    name = "stock-9mm-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    client = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    evidence = {"weapons": {}}
    jobs = []

    async def ask(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        raw, errors = await asyncio.wait_for(process.communicate(), 25)
        if not raw or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def action(owner, name, *arguments):
        return await ask("action", *owner, name, *arguments)

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                         creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, proc))
        return proc

    async def enroll():
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("game", {}).get("map") == OBSERVATORY)
        return await wait(run, lambda s: s.get("state") == "hosting" and s.get("game", {}).get("map") == OBSERVATORY
                          and s.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def receipt(event, sequence):
        for _ in range(35):
            rows = (await ask("events", *client, "--tail", "40"))["events"]
            match = next((row for row in rows if row.get("sequence") == sequence
                          and row["event"] in (event, event + "_rejected")), None)
            if match:
                assert match["event"] == event, match
                return match["native_guest_weapon"]
            await asyncio.sleep(.2)
        raise RuntimeError(f"The stock input did not produce {event} for sequence {sequence}")

    async def stop(owner, proc):
        if proc.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(proc.wait(), 15) == 0, proc.returncode

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    client_flags = ("--headless", "--native-zones", "--native-guest-primary-input", "--native-guest-reload-input")
    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await ask("invite", *host, "--name", "Stock 9mm tester", "--character", "Gunsmith")
        guest = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        first = await enroll()
        for kind in kinds:
            profile = WEAPONS[kind]
            capacity = profile.ball_magazine.capacity
            guid = uuid.uuid4().hex.upper()
            seeded = (await action(host, "native-guest-weapon-seed", "--kind", kind, "--item-guid", guid))["native_guest_weapon"]
            assert seeded["guid"] == guid and seeded["stored_ammo"] == 7, seeded
            equipped = (await action(client, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
            assert (equipped["guid"], equipped["stored_ammo"], equipped["actor_ammo"]) == (guid, 7, 7), equipped
            await wait(client_run, lambda s: (scope := s.get("game", {}).get("native_guest_primary_scope", {}))
                       and (scope["guid"], scope["kind"]) == (guid, kind))
            shots = []
            for loaded in range(7, 0, -1):
                press = (await action(client, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
                assert press["guid"] == guid and press["stock_wrappers"] == 2, press
                shot = await receipt("native_guest_primary_input", press["sequence"])
                assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (guid, loaded, loaded - 1, 1), shot
                shots.append({"press": press, "stock_shot": shot})
            empty = (await action(client, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
            assert (empty["stored_ammo"], empty["actor_ammo"]) == (0, 0), empty
            refused = await ask("action", *client, "native-guest-weapon-fire", "--target", guid, success=False)
            assert refused["error"] == "guest_weapon_preloaded_magazine_required", refused

            reserve_guid = uuid.uuid4().hex.upper()
            reserve = (await action(host, "native-guest-weapon-seed-ammo", "--item-guid", reserve_guid))["native_guest_weapon"]
            assert reserve["guid"] == reserve_guid, reserve
            reload = (await action(client, "native-guest-reload-input-fixture"))["native_guest_reload_fixture"]
            refilled = await receipt("native_guest_reload_input", reload["sequence"])
            assert (refilled["guid"], refilled["before"]) == (guid, 0), refilled
            assert 0 < refilled["after"] <= capacity, refilled
            assert refilled["after"] == refilled["reserve_before"] - refilled["reserve_after"], refilled
            evidence["weapons"][kind] = {"class_path": profile.path,
                                         "guid": guid, "scope": kind, "shots": shots, "empty": empty,
                                         "stock_reload": refilled, "reserve_guid": reserve_guid}
        await stop(host, authority)
        await stop(client, guest)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        await enroll()
        for item in evidence["weapons"].values():
            saved = (await action(client, "native-guest-weapon-inspect", "--target", item["guid"]))["native_guest_weapon"]
            assert (saved["guid"], saved["stored_ammo"]) == (item["guid"], item["stock_reload"]["after"]), saved
            item["stock_restart"] = saved
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
        if originals is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / "live-stock-9mm-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if originals is not None:
            assert evidence["original_saves_verified"], "The original single-player directory changed during the test"


if __name__ == "__main__":
    cli_parser = argparse.ArgumentParser(description=__doc__)
    cli_parser.add_argument("--work", type=Path, required=True)
    cli_parser.add_argument("--vanilla-saved", type=Path)
    cli_parser.add_argument("--weapon", action="append", choices=sorted(WEAPONS),
                            help="verify this authored weapon profile, repeat for multiple profiles")
    settings = cli_parser.parse_args()
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved,
                       tuple(dict.fromkeys(settings.weapon)) if settings.weapon else DEFAULT_WEAPONS))
