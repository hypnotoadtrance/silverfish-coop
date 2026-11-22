"""Prove an equipped guest flare is thrown once through the real stock use path."""

import argparse
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import OBSERVATORY, saved_hashes

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.silverfish import native_flare, native_inventory


@contextmanager
def staged_probe(work):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main, probe = scripts / "main.lua", scripts / "guest_flare_probe.lua"
    original = main.read_bytes()
    with probe.open("xb") as stream:
        stream.write((Path(__file__).with_name("fixtures") / probe.name).read_bytes())
    try:
        main.write_bytes(original + b'\nrequire("guest_flare_probe")\n')
        yield
    finally:
        main.write_bytes(original)
        probe.unlink()


async def verify(work, canary):
    name = "flare-proof-" + uuid.uuid4().hex[:8]
    host, guest = ("--campaign", name), ("--profile", name)
    hrun, grun = work / "campaigns" / name / "run", work / "clients" / name / "run"
    hrun.mkdir(parents=True)
    baseline = saved_hashes(canary)
    assert baseline
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    games = []

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(process.communicate(), 30)
        reply = json.loads(output) if output else {}
        if process.returncode or not reply.get("ok"):
            raise RuntimeError((args, reply or errors.decode(errors="replace")))
        return reply

    async def start(run, label, *args):
        with (hrun / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *args, cwd=ROOT, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
        games.append((run, process))

    async def probe(mailbox, verb, value=""):
        requested, reply = mailbox / "flare-probe", mailbox / "flare-reply"
        reply.unlink(missing_ok=True)
        requested.write_text(f"{verb}|{value}|{uuid.uuid4().hex[:8]}\n", encoding="ascii")
        async with asyncio.timeout(8):
            while True:
                try:
                    ok, payload = reply.read_text().split("|", 1)
                except (OSError, ValueError):
                    await asyncio.sleep(.05)
                    continue
                if ok != "ok":
                    raise RuntimeError((verb, payload))
                return payload

    with staged_probe(work):
        try:
            await start(hrun, "host", "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                        "--native-guest", "--native-zones", "--start-zone", OBSERVATORY)
            await wait(hrun, lambda row: row.get("state") == "hosting")
            invitation = await command("invite", *host, "--name", "Flare thrower", "--character", "Flare thrower")
            await start(grun, "guest", "join", *guest, "--invite", invitation["invite_file"], "--headless",
                        "--native-guest-inventory-view")
            await wait(grun, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh"))
            await wait(hrun, lambda row: row.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            hbox, gbox = [next((run / "game-bridge").iterdir()) for run in (hrun, grun)]
            guid = uuid.uuid4().hex.upper()
            await command("action", *host, "native-guest-weapon-seed", "--target", "guest",
                          "--kind", "stock-flare", "--item-guid", guid)
            stored = await command("action", *guest, native_inventory.STORE_EQUIPMENT, "--target", guid)
            slot = stored[native_inventory.STORE_KEY]["slot"]
            assert await probe(hbox, "world") == await probe(gbox, "world") == ""
            await asyncio.sleep(.8)
            assert await probe(gbox, "equip", slot) == "2"
            await wait(grun, lambda row: row.get("game", {}).get("native_guest_equipment_selection", {}).get("guid") == guid)
            await asyncio.sleep(.8)
            assert await probe(gbox, "press") == "press"
            await asyncio.sleep(.2)
            assert await probe(gbox, "release") == "release"
            result = await wait(hrun, lambda row: row.get("game", {}).get(native_flare.PROOF, {}).get("guid") == guid)
            proof = result["game"][native_flare.PROOF]
            assert (proof["before"], proof["after"], proof["toss"]) == (2, 1, "0")
            host_shot = await probe(hbox, "world")
            async with asyncio.timeout(6):
                while not (guest_shot := await probe(gbox, "world")):
                    await asyncio.sleep(.1)
            assert host_shot.startswith("3,") and guest_shot.startswith("1,") and "|" not in host_shot + guest_shot
            evidence = hrun / "guest-flare-verification.json"
            evidence.write_text(json.dumps({"stock_slot": slot, "receipt": proof, "host_projectile": host_shot,
                "guest_projectile": guest_shot}, indent=2), encoding="utf-8")
            print(evidence, flush=True)
        finally:
            for run, process in reversed(games):
                if run.is_dir():
                    (run / "stop").touch()
                try:
                    await asyncio.wait_for(process.wait(), 8)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            assert saved_hashes(canary) == baseline


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
