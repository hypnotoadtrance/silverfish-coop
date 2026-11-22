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


def read_projectiles(frame):
    rows = []
    for record in filter(None, frame.split("|")):
        role, x, y, z, lit, intensity, red, green, blue, flame, fuse, lifespan = map(float, record.split(","))
        rows.append(dict(role=int(role), position=[x, y, z], lit=bool(lit), intensity=intensity,
                         rgb=[red, green, blue], flame=bool(flame), fuse=fuse, lifespan=lifespan))
    return rows


def has_color(rgb, color):
    red, green, blue = rgb
    if color == "red":
        return red > max(green, blue)
    if color == "blue":
        return blue > max(green, red)
    return min(red, green) > blue


async def verify(work, canary, colors, expiry, late_join):
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
                        "--native-guest", "--native-zones", "--start-zone", OBSERVATORY, "--max-guests", "2")
            await wait(hrun, lambda row: row.get("state") == "hosting")
            invitation = await command("invite", *host, "--name", "Flare thrower", "--character", "Flare thrower")
            await start(grun, "guest", "join", *guest, "--invite", invitation["invite_file"], "--headless",
                        "--native-guest-inventory-view")
            await wait(grun, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh"))
            await wait(hrun, lambda row: row.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            hbox, gbox = [next((run / "game-bridge").iterdir()) for run in (hrun, grun)]
            if expiry:
                (hbox / "flare-short-fuse").touch()
            records = {}
            evidence = hrun / "guest-flare-verification.json"
            for color in colors:
                kind = "stock-flare" if color == "red" else f"stock-flare-{color}"
                for box in (hbox, gbox):
                    assert await probe(box, "color", color) == color
                guid = uuid.uuid4().hex.upper()
                await command("action", *host, "native-guest-weapon-seed", "--target", "guest",
                              "--kind", kind, "--item-guid", guid)
                stored = await command("action", *guest, native_inventory.STORE_EQUIPMENT, "--target", guid)
                slot = stored[native_inventory.STORE_KEY]["slot"]
                assert await probe(hbox, "world") == await probe(gbox, "world") == ""
                async with asyncio.timeout(8):
                    while await probe(gbox, "slot", slot) != "ready":
                        await asyncio.sleep(.1)
                assert await probe(gbox, "equip", slot) == "2"
                await wait(grun, lambda row: row.get("game", {}).get("native_guest_equipment_selection", {}).get("guid") == guid)
                await asyncio.sleep(.8)
                assert await probe(gbox, "press") == "press"
                await asyncio.sleep(.2)
                assert await probe(gbox, "release") == "release"
                result = await wait(hrun, lambda row: row.get("game", {}).get(native_flare.PROOF, {}).get("guid") == guid)
                proof = result["game"][native_flare.PROOF]
                assert (proof["before"], proof["after"], proof["toss"]) == (2, 1, "0")
                async with asyncio.timeout(10):
                    while True:
                        rows = [json.loads(line) for line in (hrun / "events.jsonl").read_text().splitlines()]
                        commit = next((row for row in rows if row["event"] == "native_guest_flare_accepted"
                                       and row["guid"] == guid), None)
                        if commit:
                            break
                        await asyncio.sleep(.1)
                host_shot = await probe(hbox, "world")
                async with asyncio.timeout(6):
                    while not (guest_shot := await probe(gbox, "world")):
                        await asyncio.sleep(.1)
                assert host_shot.startswith("3,") and guest_shot.startswith("1,") and "|" not in host_shot + guest_shot
                async with asyncio.timeout(6):
                    while await probe(gbox, "ammo") != "1":
                        await asyncio.sleep(.1)
                assert await probe(gbox, "tossstart") == "tossstart"
                await asyncio.sleep(.2)
                assert await probe(gbox, "tossrelease") == "tossrelease"
                terminal = await wait(hrun, lambda row: row.get("game", {}).get(native_flare.PROOF, {}).get("after") == 0)
                last = terminal["game"][native_flare.PROOF]
                assert (last["guid"], last["before"], last["toss"]) == (guid, 1, "1")
                assert last["projectile"] != proof["projectile"]
                async with asyncio.timeout(6):
                    while True:
                        host_final, guest_final = await probe(hbox, "world"), await probe(gbox, "world")
                        if host_final.count("|") == guest_final.count("|") == 1 and await probe(gbox, "ammo") == "gone":
                            break
                        await asyncio.sleep(.1)
                final_commit = next(row for row in (json.loads(line) for line in (hrun / "events.jsonl").read_text().splitlines())
                                    if row["event"] == "native_guest_flare_accepted" and row["after"] == 0 and row["guid"] == guid)
                assert commit["revision"] is None and final_commit["revision"] is None
                await asyncio.sleep(4.2)  # A replica's unmodified stock fuse is three seconds.
                host_final, guest_final = await probe(hbox, "world"), await probe(gbox, "world")
                host_lights, guest_lights = read_projectiles(host_final), read_projectiles(guest_final)
                assert len(host_lights) == len(guest_lights) == 2, (color, host_lights, guest_lights)
                for shot in (*host_lights, *guest_lights):
                    assert shot["lit"] and shot["intensity"] > 0, (color, shot)
                    assert has_color(shot["rgb"], color), (color, shot)
                assert all(shot["role"] == 3 for shot in host_lights)
                assert all(shot["role"] == 1 for shot in guest_lights)
                assert sorted((shot["rgb"], shot["intensity"]) for shot in host_lights) == sorted(
                    (shot["rgb"], shot["intensity"]) for shot in guest_lights)
                records[color] = {"stock_slot": slot, "receipt": proof, "throws_pending_save": True,
                    "host_projectile": host_shot, "guest_projectile": guest_shot, "last_unit": last,
                    "host_lights": host_lights, "guest_lights": guest_lights}
                evidence.write_text(json.dumps(records, indent=2), encoding="utf-8")
            if expiry:
                max_fuse = max(shot["fuse"] for run in records.values() for shot in run["host_lights"])
                async with asyncio.timeout(max(12, max_fuse + 10)):
                    pending = set(colors)
                    while pending:
                        for color in tuple(pending):
                            expired = {}
                            for role, box in (("host", hbox), ("guest", gbox)):
                                await probe(box, "color", color)
                                rows = read_projectiles(await probe(box, "world"))
                                if not rows:
                                    expired[role] = rows
                            if len(expired) == 2:
                                pending.remove(color)
                                records[color]["after_stock_fuse"] = expired
                        if pending:
                            await asyncio.sleep(1)
                evidence.write_text(json.dumps(records, indent=2), encoding="utf-8")
            if late_join:
                late = name + "-late"
                run = work / "clients" / late / "run"
                invite = await command("invite", *host, "--name", "Late observer", "--character", "Late observer")
                await start(run, "late", "join", "--profile", late, "--invite", invite["invite_file"], "--headless",
                            "--native-guest-inventory-view")
                await wait(run, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh"))
                await wait(hrun, lambda row: row.get("game", {}).get("remote_clients") == 2)
                late_box = next((run / "game-bridge").iterdir())
                for color in colors:
                    await probe(late_box, "color", color)
                    seen = read_projectiles(await probe(late_box, "world"))
                    records[color]["late_observer"] = seen
                evidence.write_text(json.dumps(records, indent=2), encoding="utf-8")
                for color in colors:
                    assert all(not shot["lit"] and shot["intensity"] == 0
                               for shot in records[color]["late_observer"]), (color, records[color]["late_observer"])
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
    parser.add_argument("--color", action="append", choices=("red", "blue", "yellow"), help="test each selected stock color")
    parser.add_argument("--expiry", action="store_true", help="observe both seats after the stock fuse burns out")
    parser.add_argument("--late-join", action="store_true", help="join a fresh observer after both flares burn out")
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve(),
                       tuple(dict.fromkeys(options.color or ("red",))), options.expiry or options.late_join, options.late_join))
