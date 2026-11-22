"""Harvest two stock patch mushrooms through Interact in a muted Direct IP pair."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.silverfish import native_mushrooms as mushrooms, native_world_loot as loot
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def verify(work: Path, unchanged: Path, area: str):
    name = f"mushroom-{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", name), ("--profile", name)
    hrun, grun = work / "campaigns" / name / "run", work / "clients" / name / "run"
    hrun.mkdir(parents=True)
    source_saves = saved_hashes(unchanged)
    assert source_saves
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    print(hrun, flush=True)

    async def command(*argv):
        process = await asyncio.create_subprocess_exec(*cli, *argv, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, err = await asyncio.wait_for(process.communicate(), 35)
        reply = json.loads(out) if out else {}
        if not reply.get("ok"):
            raise RuntimeError(f"{argv}: {reply or err.decode(errors='replace')}")
        return reply

    async def start(label, controller, *argv):
        with (hrun / f"{label}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*cli, *argv, cwd=ROOT, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))
        return process

    async def stop(process, controller):
        if process.returncode is not None:
            return
        try:
            await command("action", *controller, "stop")
            await asyncio.wait_for(process.wait(), 10)
        except (RuntimeError, asyncio.TimeoutError):
            if process.returncode is None:
                process.terminate()
                await process.wait()

    def active(status):
        return status.get("game", {}).get(mushrooms.KEY, {})

    def rendered(status):
        return status.get("game", {}).get(loot.KEY, {})

    def receipt():
        for line in (hrun / "events.jsonl").read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("event") == "native_mushroom_claimed" and entry.get("id") == guest_pick:
                return entry
        return None

    try:
        host_flags = ("--port", "0", "--headless", "--fixtures", "--native-events",
                      "--native-guest", "--native-zones", "--native-expanded-zones")
        host_process = await start("host", host, "host", *host, *host_flags, "--start-zone", area)
        await wait(hrun, lambda state: state.get("state") == "hosting" and state.get("game", {}).get(mushrooms.CAPABILITY) == 1)
        await command("action", *host, mushrooms.LOCATE)
        first = await wait(hrun, lambda state: state.get("state") == "hosting" and len(active(state)) >= 3)
        invitation = await command("invite", *host, "--name", "Mushroom runner", "--character", "Mushroom runner")
        guest_process = await start("guest", guest, "join", *guest, "--invite", invitation["invite_file"], "--headless")
        initial_guest = await wait(grun, lambda state: state.get("state") == "connected" and
                                   state.get("game", {}).get(mushrooms.SUPPRESSION, 0) > 0 and
                                   len(rendered(state).keys() & active(first).keys()) >= 3)
        await wait(hrun, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        candidates = list(rendered(initial_guest).keys() & active(first).keys())
        host_pick, guest_pick = sorted(candidates)[:2]

        await command("action", *host, mushrooms.APPROACH, "--target", f"host@{host_pick}")
        result = await command("action", *host, mushrooms.HARVEST, "--target", f"host@{host_pick}")
        assert host_pick not in result[mushrooms.KEY]
        await wait(grun, lambda state: host_pick not in rendered(state) and guest_pick in rendered(state))

        await command("action", *host, mushrooms.APPROACH, "--target", f"guest@{guest_pick}")
        await wait(grun, lambda state: guest_pick in rendered(state))
        await command("action", *guest, loot.FIXTURE, "--target", guest_pick)
        await wait(hrun, lambda state: guest_pick not in active(state) and bool(receipt()))
        await wait(grun, lambda state: guest_pick not in rendered(state))
        transfer = receipt()
        source = active(first)[guest_pick]
        assert transfer["patch_guid"] == source["patch_guid"] and transfer["revision"] > 0, transfer
        replay = await command("action", *guest, mushrooms.CLAIM, "--target", guest_pick,
                               "--operation-id", transfer["operation_id"])
        assert replay[mushrooms.PROOF]["guid"] == transfer["guid"], replay

        await stop(guest_process, guest)
        await stop(host_process, host)
        await start("host-reload", host, "host", *host, *host_flags)
        await wait(hrun, lambda state: state.get("state") == "hosting" and state.get("generation") != first["generation"]
                   and state.get("game", {}).get(mushrooms.CAPABILITY) == 1)
        restored = (await command("action", *host, mushrooms.LOCATE))[mushrooms.KEY]
        preserved = restored.keys() & (set(candidates) - {host_pick, guest_pick})
        assert preserved and host_pick not in restored and guest_pick not in restored, restored
        (hrun / "mushroom-verification.json").write_text(json.dumps({
            "host_harvest": active(first)[host_pick],
            "guest_harvest": transfer,
            "guest_generation_suppressed": initial_guest["game"][mushrooms.SUPPRESSION],
            "preserved_after_host_reload": sorted(preserved),
            "original_files_verified": len(source_saves),
        }, indent=2), encoding="utf-8")
    finally:
        for process, controller in reversed(processes):
            await stop(process, controller)
        assert saved_hashes(unchanged) == source_saves


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--area", choices=("crossroads", "observatory"), default="crossroads")
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve(),
                       CROSSROADS if options.area == "crossroads" else OBSERVATORY))
