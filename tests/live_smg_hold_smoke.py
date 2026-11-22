"""Verify sustained stock SMG fire and release on a staged headless host."""

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

from tools.silverfish.guest_weapon_profiles import FIREARMS
from tools.silverfish.native_smg_hold import FIXTURE, KEY
from tools.silverfish.travel import OBSERVATORY


async def verify(work, vanilla_saved):
    name = "smg-hold-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    client = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    proof = {}
    jobs = []

    async def call(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        raw, errors = await asyncio.wait_for(process.communicate(), 25)
        if not raw or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        jobs.append((owner, process))
        return process

    async def enroll():
        await wait(client_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == OBSERVATORY)
        return await wait(run, lambda state: state.get("state") == "hosting"
                          and state.get("game", {}).get("map") == OBSERVATORY
                          and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def stop(owner, process):
        if process.returncode is None:
            await call("action", *owner, "stop")
        assert await asyncio.wait_for(process.wait(), 15) == 0, process.returncode

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    client_flags = ("--headless", "--native-zones")
    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting")
        invite = await call("invite", *host, "--name", "Sustained SMG tester", "--character", "Gunsmith")
        guest = await launch(client, "guest", "join", *client, "--invite", invite["invite_file"], *client_flags)
        first = await enroll()
        guid = uuid.uuid4().hex.upper()
        seeded = await call("action", *host, "native-guest-weapon-seed", "--kind", "smg-standard", "--item-guid", guid)
        assert seeded["native_guest_weapon"]["stored_ammo"] == 7, seeded
        equipped = await call("action", *client, "native-guest-weapon-equip", "--target", guid)
        assert equipped["native_guest_weapon"]["actor_ammo"] == 7, equipped
        pawn = equipped["native_guest_weapon"]["pawn"]
        smg = next(profile.path for profile in FIREARMS if profile.kind == "smg-standard")
        await wait(run, lambda state: state.get("game", {}).get("native_player_avatars", {}).get(pawn, {}).get("weapon_meshes"))

        async def recoil_after(rounds):
            def settled(state):
                row = state.get("game", {}).get("native_avatar_recoil", {}).get(pawn, {})
                return (row.get("weapon"), row.get("count"), row.get("restored")) == (smg, rounds, True)
            state = await wait(run, settled, timeout=8)
            row = state["game"]["native_avatar_recoil"][pawn]
            assert not row["active"] and max(row["kick_cm"], row["rotation_degrees"]) > .1, row
            return row

        for loaded in (16, 25):
            reserve = uuid.uuid4().hex.upper()
            await call("action", *host, "native-guest-weapon-seed-ammo", "--item-guid", reserve)
            outcome = (await call("action", *client, "native-guest-weapon-reload", "--target", guid))["native_guest_weapon"]
            assert (outcome["guid"], outcome["after"], outcome["reserve_after"]) == (guid, loaded, 0), outcome

        other = uuid.uuid4().hex.upper()
        refused = await call("action", *host, FIXTURE, "--target", "guest", "--item-guid", other,
                             "--milliseconds", "2400", success=False)
        assert refused["error"] == "native_guest_smg_hold_fixture_unavailable", refused
        proof["wrong_guid"] = refused["error"]

        release = (await call("action", *host, FIXTURE, "--target", "guest", "--item-guid", guid,
                              "--milliseconds", "2400", "--amount", "2"))[KEY]
        assert (release["guid"], release["status"], release["before"]) == (guid, "released", 25), release
        assert release["rounds"] == release["projectiles"] == 25 - release["after"] >= 2, release
        proof["release"] = release
        proof["release_recoil"] = await recoil_after(release["rounds"])
        expiry = (await call("action", *host, FIXTURE, "--target", "guest", "--item-guid", guid,
                             "--milliseconds", "650"))[KEY]
        assert (expiry["guid"], expiry["status"], expiry["before"]) == (guid, "expired", release["after"]), expiry
        assert expiry["rounds"] == expiry["projectiles"] == release["after"] - expiry["after"] >= 2, expiry
        assert expiry["serial"] != release["serial"] and expiry["pawn"] == release["pawn"], expiry
        proof["expiry"] = expiry
        proof["expiry_recoil"] = await recoil_after(release["rounds"] + expiry["rounds"])

        await stop(host, authority)
        await stop(client, guest)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("generation") != first["generation"])
        invite = await call("action", *host, "refresh-invite", "--target", invite["guest_id"])
        await launch(client, "guest-restart", "join", *client, "--invite", invite["invite_file"], *client_flags)
        await enroll()
        saved = (await call("action", *client, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
        assert (saved["guid"], saved["stored_ammo"]) == (guid, expiry["after"]), saved
        proof.update(verified=True, restart=saved)
    except BaseException as error:
        proof.update(verified=False, failure=str(error))
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
            proof["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / "live-smg-hold-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if originals is not None:
            assert proof["original_saves_verified"], "The original single-player directory changed during the test"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved))
