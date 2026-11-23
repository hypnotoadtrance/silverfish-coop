"""Shoot stock propane canisters or a placed fuel drum in a muted Direct IP pair."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_guest_held_fire_smoke import events
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish.control import submit
from tools.silverfish.native_anomaly_effects import CANISTERS
from tools.silverfish.travel import CISTERN, CROSSROADS


async def verify(work, vanilla, tall, standing=False, fuel_drum=False):
    family = "fuel-drum" if fuel_drum else "standing-fuel-tank" if standing else "explosive-canister"
    name = f"{family}-{uuid.uuid4().hex[:8]}"
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    print(host, flush=True)
    original = saved_hashes(vanilla)
    assert original
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    if standing:
        area, actor = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P", "Prop_Standing_FuelTank_C_1"
    elif fuel_drum:
        area, actor = CROSSROADS, "Prop_FeulDrum_C_33"
    else:
        area, actor = (CISTERN, "Prop_PropaneTank_Tall_C_3") if tall else (CROSSROADS, "Prop_PropaneTank_Small_C_12")
    target = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{actor}"
    if standing:
        target = "Prop_Standing_FuelTank_C " + target
    jobs, proof = [], {"target": target, "verified": False, "shots": 0}
    cue_event = "native_fuel_drum_effect_played" if fuel_drum else "native_canister_effect_played"
    capture_key = "native_fuel_drum_capture" if fuel_drum else "native_canister_capture"

    def canister(frame):
        key = "native_standing_fuel_tanks" if standing else CANISTERS
        return frame.get("game", {}).get(key, {}).get(target, {})

    async def launch(run, role, *args):
        env = dict(os.environ)
        if role == "guest":
            env["SILVERFISH_COOP_RENDER_CAPTURE"] = "guest"
        with (host / f"{role}.log").open("wb") as log:
            job = await asyncio.create_subprocess_exec(*cli, *args, "--headless", "--native-zones",
                                                       stdout=log, stderr=log, cwd=REPO, env=env,
                                                       creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((run, job))

    async def action(run, verb, **args):
        result = await submit(run, verb, **args)
        if not result["ok"]:
            raise RuntimeError(f"{verb}: {result.get('error')}")
        return result

    try:
        await launch(host, "host", "host", "--campaign", name, "--port", "0", "--start-zone", area,
                     "--native-guest", "--native-events", "--native-expanded-zones", "--fixtures")
        initial = await wait(host, lambda row: (canister(row).get("movement") is True if standing
                                                else canister(row).get("phase") == 0))
        proof["host_initial"] = canister(initial)
        if standing:
            await action(host, "native-standing-fuel-tank-tip-fixture", target=f"prepare@{target}")
        invite_cmd = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", "Canister observer",
                                                         "--character", "Scout", stdout=asyncio.subprocess.PIPE,
                                                         cwd=REPO, creationflags=subprocess.CREATE_NO_WINDOW)
        invite_text, _ = await asyncio.wait_for(invite_cmd.communicate(), 20)
        assert invite_cmd.returncode == 0
        invite = json.loads(invite_text)
        await launch(guest, "guest", "join", "--profile", name, "--invite", invite["invite_file"])
        peer = await wait(guest, lambda row: (canister(row).get("movement") is True if standing
                                              else canister(row).get("phase") == 0))
        if standing:
            def angle_distance(first, second):
                return max(abs((a - b + 180) % 360 - 180) for a, b in zip(first, second, strict=True))
            origin = canister(initial)["rotation"]
            assert angle_distance(origin, canister(peer)["rotation"]) < 2, (origin, canister(peer))
            await action(host, "native-standing-fuel-tank-tip-fixture", target=f"tip@{target}")
            def synced_rotation(row):
                host_prop, guest_prop = canister(status(host)), canister(row)
                if (host_prop and guest_prop and angle_distance(origin, host_prop["rotation"]) > 25
                        and angle_distance(host_prop["rotation"], guest_prop["rotation"]) < 3):
                    proof["tipped"] = {"host": host_prop, "guest": guest_prop}
                    return True
                return False
            await wait(guest, synced_rotation, timeout=6)
            proof["verified"] = True
            return
        prior = peer["game"].get("native_canister_blocked", 0)
        await action(guest, "native-canister-guest-guard-fixture", target=target)
        guarded = await wait(guest, lambda row: row.get("game", {}).get("native_canister_blocked", 0) >= prior + 2)
        assert canister(guarded) == canister(peer), canister(guarded)
        proof["guest_mutations_blocked"] = guarded["game"]["native_canister_blocked"] - prior
        initial = await wait(host, lambda row: row.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        guest_health = initial["game"]["pawns"]["guest"]["health"]
        await action(host, "native-canister-prepare-fixture", target=target)
        if tall or fuel_drum:
            ready = await wait(host, lambda row: canister(row).get("location"))
            host_origin = canister(ready)["location"]
            await action(host, "native-canister-shoot-fixture", target=target)
            proof["shots"] += 1
        else:
            await action(host, "native-canister-ignite-fixture", target=target)
        burning = await wait(guest, lambda row: canister(row).get("phase") == 1 and canister(row).get("particles_active"),
                             timeout=8)
        proof["guest_stock_fire"] = canister(burning)
        if fuel_drum:
            proof["host_stock_fire"] = canister(await wait(host, lambda row: canister(row).get("phase") == 1))
            assert proof["host_stock_fire"]["health"] < proof["host_initial"]["health"]
        if tall:
            def synced_motion(row):
                host_prop, guest_prop = canister(status(host)), canister(row)
                host_location, guest_location = host_prop.get("location"), guest_prop.get("location")
                if host_location and guest_location and math.dist(host_location, host_origin) > 50:
                    error = math.dist(host_location, guest_location)
                    if error < 50:
                        proof["moving_canister"] = {"host": host_location, "guest": guest_location, "gap_cm": error}
                        return True
                return False

            await wait(guest, synced_motion, timeout=6)
        if not (tall or fuel_drum):
            for _ in range(5):
                await action(host, "native-canister-shoot-fixture", target=target)
                proof["shots"] += 1
                await asyncio.sleep(.35)
                if canister(status(host)).get("phase") == 2:
                    break
        finished = await wait(host, lambda row: canister(row).get("phase") == 2, timeout=15)
        assert finished["game"].get(capture_key) == 1
        assert finished["game"]["pawns"]["guest"]["health"] == guest_health and not finished["gameplay"]["wipe"]
        proof["host_explosions"] = 1
        await wait(guest, lambda row: canister(row).get("phase") == 2 and events(guest, cue_event))
        cue, = events(guest, cue_event)
        assert cue["particles"], cue
        proof["guest_stock_blast"] = cue
        await action(guest, "drop-link")
        await wait(guest, lambda row: row.get("reconnect_suspended") and not row.get("link_connected"))
        assert (await action(guest, "reconnect"))["resumed"]
        await wait(guest, lambda row: row.get("link_connected") and canister(row).get("phase") == 2)
        assert len(events(guest, cue_event)) == 1
        proof["reconnect_removed"] = True
        proof["guest_health_preserved"] = guest_health
        proof["verified"] = True
    except BaseException as error:
        proof["failure"] = str(error)[:350]
        proof["host_final"] = canister(status(host))
        proof["guest_final"] = canister(status(guest))
        raise
    finally:
        for run, job in reversed(jobs):
            if job.returncode is not None:
                continue
            try:
                await submit(run, "stop", timeout=12)
                await asyncio.wait_for(job.wait(), 12)
            except (OSError, RuntimeError, TimeoutError):
                if job.returncode is None:
                    job.terminate()
                    await job.wait()
        proof["original_saves_verified"] = saved_hashes(vanilla) == original
        proof["verified"] &= proof["original_saves_verified"]
        (host / "live-explosive-canister-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--tall", action="store_true")
    kind.add_argument("--standing", action="store_true", help="verify an authored standing fuel tank's rotation")
    kind.add_argument("--fuel-drum", action="store_true", help="verify the authored Crossroads drum from one stock shot")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True),
                       args.tall, args.standing, args.fuel_drum))
