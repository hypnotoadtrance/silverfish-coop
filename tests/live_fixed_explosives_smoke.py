"""Verify an authored Dry Dock power box across a stock shot and Direct IP join."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_ai_smoke import status, wait
from live_guest_held_fire_smoke import events, wait_event
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import FIREARMS, TWELVE_GAUGE_BUCK
from tools.silverfish.native_anomaly_effects import FIXED_EXPLOSIVES

AREA = "/Game/Classes/Maps/Maps_Dungeons/DryDock_P"
TARGET = f"{AREA}.DryDock_P:PersistentLevel.Prop_Explosive_PowerBox_C_5"
WORKING = "/Game/Assets/StaticMeshes/Mesh_Misc/SM_Powerbox_Working.SM_Powerbox_Working"
BROKEN = "/Game/Assets/StaticMeshes/Mesh_Misc/SM_Powerbox_Broken.SM_Powerbox_Broken"
CUE = "native_fixed_explosive_effect_played"
SHOT = "native_weapon_shot_played"
PISTOL = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"


async def verify(work, vanilla):
    name = f"fixed-explosive-{uuid.uuid4().hex[:8]}"
    host = work / "campaigns" / name / "run"
    guest, late = [work / "clients" / f"{name}-{seat}" / "run" for seat in ("watch", "late")]
    host.mkdir(parents=True)
    print(host, flush=True)
    original = saved_hashes(vanilla)
    assert original
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    jobs = []
    proof = {"verified": False, "target": TARGET, "rounds_fired": 0}

    def prop(frame):
        return frame.get("game", {}).get(FIXED_EXPLOSIVES, {}).get(TARGET, {})

    def broken(frame):
        value = prop(frame)
        return value.get("destroyed") and value.get("damaged_mesh") and value.get("mesh") == BROKEN

    def visible_weapon(frame):
        return any(row.get("weapon_meshes") and not row.get("diagnostic")
                   for row in frame.get("game", {}).get("native_player_avatars", {}).values())

    def recoil(frame, weapon, count):
        matched = [row for row in frame.get("game", {}).get("native_avatar_recoil", {}).values()
                   if (row["weapon"], row["count"], row["restored"]) == (weapon, count, True)
                   and max(row["kick_cm"], row["rotation_degrees"]) > .1]
        return matched[0] if len(matched) == 1 else None

    async def launch(run, role, *args):
        env = dict(os.environ)
        env["SILVERFISH_COOP_RENDER_CAPTURE"] = "host" if role == "host" else "guest"
        with (host / f"{role}.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *args, "--headless", "--native-zones",
                                                           stdout=log, stderr=log, env=env, cwd=REPO,
                                                           creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((run, process))
        return process

    async def join(run, role):
        invitation = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", role,
                                                         "--character", role, stdout=asyncio.subprocess.PIPE,
                                                         cwd=REPO, creationflags=subprocess.CREATE_NO_WINDOW)
        payload, _ = await asyncio.wait_for(invitation.communicate(), 20)
        assert invitation.returncode == 0
        return await launch(run, role, "join", "--profile", run.parent.name, "--invite", json.loads(payload)["invite_file"],
                            "--native-guest-primary-input")

    async def action(run, verb, **args):
        result = await submit(run, verb, **args)
        if not result["ok"]:
            raise RuntimeError(f"{verb}: {result.get('error')}")
        return result

    try:
        await launch(host, "host", "host", "--campaign", name, "--port", "0", "--start-zone", AREA,
                     "--native-guest", "--native-events", "--native-expanded-zones", "--fixtures")
        initial = await wait(host, lambda frame: prop(frame).get("mesh") == WORKING)
        proof["working_host"] = prop(initial)
        watcher = await join(guest, "watch")
        peer = await wait(guest, lambda frame: prop(frame).get("mesh") == WORKING)
        proof["working_guest"] = prop(peer)
        before = peer["game"].get("native_fixed_explosive_blocked", 0)
        await action(guest, "native-fixed-explosive-guest-guard-fixture", target=TARGET)
        guarded = await wait(guest, lambda frame: frame.get("game", {}).get("native_fixed_explosive_blocked", 0) >= before + 2)
        assert prop(guarded) == prop(peer)
        proof["blocked_guest_mutations"] = guarded["game"]["native_fixed_explosive_blocked"] - before
        bound = await wait(host, lambda frame: frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        guest_health = bound["game"]["pawns"]["guest"]["health"]
        await action(host, "native-fixed-explosive-prepare-fixture", target=TARGET)
        await wait(guest, visible_weapon)
        shot_times = []
        for _ in range(7):
            shot_times.append(time.time())
            await action(host, "native-fixed-explosive-shoot-fixture", target=TARGET)
            proof["rounds_fired"] += 1
            await asyncio.sleep(.4)
            if broken(status(host)):
                break
        destroyed = await wait(host, broken, timeout=10)
        witnessed = await wait(guest, lambda frame: broken(frame) and events(guest, CUE), timeout=12)
        await wait(guest, lambda _: len(events(guest, SHOT)) >= proof["rounds_fired"], timeout=8)
        shots = events(guest, SHOT)
        assert len(shots) == len({shot["serial"] for shot in shots}) == proof["rounds_fired"], shots
        assert all(shot["weapon"] == PISTOL and shot["muzzle_flash"] and shot["stock_sound"] and shot["recoil"]
                   for shot in shots), shots
        count = proof["rounds_fired"]
        rendered = await wait(guest, lambda frame: recoil(frame, PISTOL, count), timeout=8)
        proof["pistol_recoil"] = recoil(rendered, PISTOL, count)
        cue, = events(guest, CUE)
        assert cue["cue"] == "powerbox" and cue["particles"], cue
        assert destroyed["game"]["native_fixed_explosive_capture"] == 1
        assert destroyed["game"]["pawns"]["guest"]["health"] == guest_health and not destroyed["gameplay"]["wipe"]
        proof.update(host=prop(destroyed), guest=prop(witnessed), cue=cue, shots=shots, guest_health_preserved=guest_health,
                     host_fixture_to_guest_milliseconds=[round((shot["time_unix"] - stamp) * 1000)
                                                        for shot, stamp in zip(shots, shot_times, strict=True)])

        # A stock guest shotgun fires multiple pellets but relays one cosmetic cue for the round.
        weapon = next(profile for profile in FIREARMS if profile.kind == "sg-71-a")
        guid = uuid.uuid4().hex.upper()
        await action(host, "native-guest-weapon-seed", target="guest", kind=weapon.kind, item_guid=guid)
        await action(guest, "native-guest-weapon-equip", target=guid)
        await wait(guest, lambda frame: frame.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        await wait(host, visible_weapon)
        trigger = (await action(guest, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
        executed = await wait_event(guest, "native_guest_primary_input", lambda row: row["sequence"] == trigger["sequence"])
        spent = executed["native_guest_weapon"]
        assert spent["before"] - spent["after"] == 1 and spent["projectiles"] == TWELVE_GAUGE_BUCK.projectile_count, spent
        observed = await wait_event(guest, SHOT, lambda row: row["weapon"] == weapon.path)
        assert observed["muzzle_flash"] and observed["stock_sound"] and not observed["recoil"], observed
        rendered = await wait(host, lambda frame: recoil(frame, weapon.path, 1), timeout=8)
        proof["shotgun_recoil"] = recoil(rendered, weapon.path, 1)
        def bolt_cycle(frame):
            return next((row for row in frame.get("game", {}).get("native_avatar_cycle", {}).values()
                         if row["weapon"] == weapon.path and row["count"] == 1
                         and not row["active"] and row["travel_cm"] > .1), None)
        proof["shotgun_cycle"] = bolt_cycle(await wait(host, bolt_cycle, timeout=8))
        proof["guest_shot"] = {"stock": spent, "effect": observed}
        assert len(events(guest, SHOT)) == proof["rounds_fired"] + 1

        await action(guest, "leave-session")
        await asyncio.wait_for(watcher.wait(), 15)
        await join(late, "late")
        late_frame = await wait(late, broken)
        assert not events(late, CUE) and not events(late, SHOT)
        proof["late_join"] = prop(late_frame)
        proof["verified"] = True
    except BaseException as failure:
        proof.update(failure=str(failure)[:400], host_final=prop(status(host)), guest_final=prop(status(guest)))
        raise
    finally:
        for run, process in reversed(jobs):
            if process.returncode is not None:
                continue
            try:
                await submit(run, "stop", timeout=12)
                await asyncio.wait_for(process.wait(), 12)
            except (OSError, RuntimeError, TimeoutError):
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
        proof["original_saves_verified"] = saved_hashes(vanilla) == original
        proof["verified"] &= proof["original_saves_verified"]
        (host / "live-fixed-explosives-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
