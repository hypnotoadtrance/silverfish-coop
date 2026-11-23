"""Harvest two stock patch mushrooms through Interact in a muted Direct IP pair."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.silverfish import native_mushrooms as mushrooms, native_world_loot as loot, runtime
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def stock_regrowth(run: Path, patch: str) -> dict:
    bridge, = (run / "game-bridge").iterdir()
    ticket = uuid.uuid4().hex
    request = bridge / "mushroom-regrow-command.tsv"
    reply = bridge / "mushroom-regrow-reply.tsv"
    runtime.write_text(request, f"{ticket}\t{patch}\n", newline="")
    async with asyncio.timeout(8):
        while True:
            try:
                lines = reply.read_text(encoding="ascii").splitlines()
            except (FileNotFoundError, PermissionError):
                lines = []
            if len(lines) == 3 and lines[0] == ticket and lines[-1] == "end":
                result, *fields = lines[1].split("\t")
                assert result == "grown", lines[1]
                before, after, minimum, maximum = fields
                return {"children_before": int(before), "children_after": int(after),
                        "stock_days_to_grow": [float(minimum), float(maximum)], "fixture_days_to_grow": 1 / 86400}
            await asyncio.sleep(0.10)


async def verify(work: Path, unchanged: Path, area: str, *, regrowth: bool = False):
    name = f"mushroom-{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", name), ("--profile", name)
    hrun, grun = work / "campaigns" / name / "run", work / "clients" / name / "run"
    hrun.mkdir(parents=True)
    source_saves = saved_hashes(unchanged)
    assert source_saves
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    environment = dict(os.environ)
    if regrowth:
        environment["SILVERFISH_COOP_MUSHROOM_REGROWTH_PROBE"] = "1"
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
                creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
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

    def game_seconds(status):
        return status.get("game", {}).get("native_scene", {}).get("game_seconds") or 0

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
        disk_before = campaign_save_hashes(hrun.parent) if regrowth else None
        if regrowth:
            assert disk_before

        await command("action", *host, mushrooms.APPROACH, "--target", f"host@{host_pick}")
        result = await command("action", *host, mushrooms.HARVEST, "--target", f"host@{host_pick}")
        assert host_pick not in result[mushrooms.KEY]
        await wait(grun, lambda state: host_pick not in rendered(state) and guest_pick in rendered(state))

        await command("action", *host, mushrooms.APPROACH, "--target", f"guest@{guest_pick}")
        await wait(grun, lambda state: guest_pick in rendered(state))
        await command("action", *guest, loot.FIXTURE, "--target", guest_pick)
        harvested = await wait(hrun, lambda state: guest_pick not in active(state) and bool(receipt()))
        await wait(grun, lambda state: guest_pick not in rendered(state))
        transfer = receipt()
        source = active(first)[guest_pick]
        assert transfer["patch_guid"] == source["patch_guid"] and transfer["revision"] is None, transfer
        replay = await command("action", *guest, mushrooms.CLAIM, "--target", guest_pick,
                               "--operation-id", transfer["operation_id"])
        assert replay[mushrooms.PROOF]["guid"] == transfer["guid"], replay

        new_rows = {}
        growth = None
        if regrowth:
            patch = source["patch"]
            removed = {host_pick, guest_pick}
            known = {*active(first), *active(harvested)}
            original_pawn = initial_guest["game"]["pawns"]["local"]["name"]
            assert game_seconds(harvested) > 0, harvested["game"].get("native_scene")
            dropped = await command("action", *guest, "drop-link")
            assert dropped["link_connected"] is False, dropped
            await wait(hrun, lambda state: state.get("link_connected") is False
                       and game_seconds(state) >= game_seconds(harvested) + 2)
            growth = await stock_regrowth(hrun, patch)

            def new_patch_rows(state):
                return {key: row for key, row in active(state).items()
                        if row["patch"] == patch and key not in known}

            grown = await wait(hrun, lambda state: bool(new_patch_rows(state))
                               and not removed & active(state).keys())
            new_rows = new_patch_rows(grown)
            assert len(new_rows) == growth["children_after"] - growth["children_before"], (new_rows, growth)
            rejoined = await command("action", *guest, "reconnect")
            assert rejoined["resumed"] and rejoined["link_connected"], rejoined
            projected = await wait(grun, lambda state: state.get("link_connected") is True
                                   and new_rows.keys() <= rendered(state).keys()
                                   and not removed & rendered(state).keys())
            assert projected["game"]["pawns"]["local"]["name"] == original_pawn
            assert campaign_save_hashes(hrun.parent) == disk_before
            growth.update(patch=patch, new_rows=new_rows, resumed_same_pawn=True, campaign_files_unchanged=True)

        await stop(guest_process, guest)
        await stop(host_process, host)
        await start("host-reload", host, "host", *host, *host_flags)
        await wait(hrun, lambda state: state.get("state") == "hosting" and state.get("generation") != first["generation"]
                   and state.get("game", {}).get(mushrooms.CAPABILITY) == 1)
        restored = (await command("action", *host, mushrooms.LOCATE))[mushrooms.KEY]
        preserved = restored.keys() & (set(candidates) - {host_pick, guest_pick})
        assert preserved and host_pick not in restored and guest_pick not in restored, restored
        if growth:
            assert new_rows.keys() <= restored.keys(), restored
            growth["new_ids_preserved_after_host_reload"] = sorted(new_rows)
        (hrun / "mushroom-verification.json").write_text(json.dumps({
            "host_harvest": active(first)[host_pick],
            "guest_harvest": transfer,
            "guest_generation_suppressed": initial_guest["game"][mushrooms.SUPPRESSION],
            "preserved_after_host_reload": sorted(preserved),
            "original_files_verified": len(source_saves),
            "stock_regrowth": growth,
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
    parser.add_argument("--regrowth", action="store_true", help="exercise Crossroads stock regrowth during a guest link outage")
    options = parser.parse_args()
    work = options.work.resolve(strict=True)
    area = CROSSROADS if options.area == "crossroads" else OBSERVATORY
    if options.regrowth:
        from live_stock_creator_smoke import installed_probe

        stage = work / "stage"
        if area != CROSSROADS or "steamapps" in {part.casefold() for part in work.parts} or stage.resolve() != stage:
            parser.error("--regrowth requires a private Crossroads stage")
        with installed_probe(stage, mod="SilverfishMushroomRegrowthProbe", fixture="mushroom_regrowth_probe.lua"):
            asyncio.run(verify(work, options.vanilla_saved.resolve(), area, regrowth=True))
    else:
        asyncio.run(verify(work, options.vanilla_saved.resolve(), area))
