"""Compare stock trap fields across a muted Direct IP join and reconnect."""

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

from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS


async def verify(work, vanilla):
    name = f"anomaly-field-{uuid.uuid4().hex[:8]}"
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    print(host, flush=True)
    original = saved_hashes(vanilla)
    if not original:
        raise ValueError("Choose a populated copied singleplayer Saved folder")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    flags = ("--native-zones", "--headless")
    jobs = []
    proof = {"verified": False}

    async def launch(run, label, *arguments):
        with (host / f"{label}.log").open("wb") as log:
            job = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log, cwd=REPO,
                                                       creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((run, job))

    def scene(status):
        return status.get("game", {}).get("native_anomaly_field", {})

    def positions(status):
        return status.get("game", {}).get("native_anomaly_positions", [])

    def projected(status, trap):
        return next((row for row in positions(status) if row["kind"] == trap["kind"]
                     and math.dist(row["location"], trap["location"]) < 3), None)

    try:
        await launch(host, "host", "host", "--campaign", name, "--port", "0", "--start-zone", CROSSROADS,
                     "--native-guest", "--native-events", "--native-expanded-zones", "--fixtures", *flags)
        initial = await wait(host, lambda row: row.get("state") == "hosting" and scene(row))
        job = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", "Field guest",
                                                   "--character", "Surveyor", stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE, cwd=REPO,
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(job.communicate(), 30)
        if job.returncode:
            raise RuntimeError(errors.decode(errors="replace"))
        invite = json.loads(output)

        # Generate the stock fields before admission to verify hot join receives their placement.
        traps = {}
        for kind in ("starburst", "burner"):
            earlier = {tuple(entry["location"]) for entry in positions(initial) if entry["kind"] == kind}
            result = await submit(host, "native-anomaly-field-fixture", target=kind)
            assert result["ok"], result
            initial = await wait(host, lambda row: any(entry["kind"] == kind and tuple(entry["location"]) not in earlier
                                                     for entry in positions(row)))
            trap, = [entry for entry in positions(initial) if entry["kind"] == kind and tuple(entry["location"]) not in earlier]
            assert trap["collision"], trap
            traps[kind] = trap
        proof["host"] = traps

        await launch(guest, "guest", "join", "--profile", name, "--invite", invite["invite_file"], *flags)
        def replicas_visible(row):
            return row.get("state") == "connected" and all(projected(row, trap) for trap in traps.values())

        peer = await wait(guest, replicas_visible)
        replicas = {kind: projected(peer, trap) for kind, trap in traps.items()}
        assert all(not replica["collision"] for replica in replicas.values()), replicas
        proof["guest"] = replicas
        prior = scene(peer)["suppressed"]
        for kind in traps:
            result = await submit(guest, "native-anomaly-field-fixture", target=kind)
            assert result["ok"], result
        vetoed = await wait(guest, lambda row: scene(row).get("suppressed", 0) >= prior + len(traps))
        assert scene(vetoed)["live"] == scene(peer)["live"], vetoed
        proof["local_spawns_suppressed"] = scene(vetoed)["suppressed"] - prior
        assert (await submit(guest, "drop-link"))["ok"]
        await wait(guest, lambda row: row.get("reconnect_suspended") and not row.get("link_connected"))
        resumed = await submit(guest, "reconnect", timeout=35)
        assert resumed["ok"] and resumed["resumed"], resumed
        rejoined = await wait(guest, replicas_visible)
        assert all(not projected(rejoined, trap)["collision"] for trap in traps.values()), rejoined
        proof["reconnect"] = True
        proof["verified"] = True
    except BaseException as error:
        proof["failure"] = str(error) or type(error).__name__
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
        (host / "live-anomaly-field-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"], "Original singleplayer saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve()))
