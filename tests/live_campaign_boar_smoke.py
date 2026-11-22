"""Prove qualified Crossroads stock NPCs have one authority and no duplicate guest spawn."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_campaign_npc_smoke import MAP


@contextmanager
def staged_probe(work, combat):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main = scripts / "main.lua"
    fixture = scripts / "campaign_boar_probe.lua"
    pristine = {main: main.read_bytes()}
    if combat:
        for name in ("native_campaign_npc.lua", "native_guest_weapon.lua"):
            file = scripts / name
            pristine[file] = file.read_bytes()
    with fixture.open("xb") as stream:
        stream.write((Path(__file__).with_name("fixtures") / fixture.name).read_bytes())
    try:
        main.write_bytes(pristine[main] + b'\nrequire("campaign_boar_probe")\n')
        if combat:
            scoped = scripts / "native_campaign_npc.lua"
            scoped.write_bytes(pristine[scoped].replace(b"return campaign_npc",
                b"campaign_npc.boar_test_stock_aim = flayer_projectile_aim\nreturn campaign_npc"))
            weapons = scripts / "native_guest_weapon.lua"
            weapons.write_bytes(pristine[weapons].replace(b"if action == \"fire\" and shot_fixture then", b'''
    if action == "fire" and fixtures and headless and os.getenv("SILVERFISH_COOP_BOAR_PROBE_SHOT") == "1" then
        restore, refusal = require("campaign_boar_probe").before_fire(state, phase, name)
    elseif action == "fire" and shot_fixture then'''))
        yield
    finally:
        for destination, source in pristine.items():
            destination.write_bytes(source)
        fixture.unlink(missing_ok=True)


def census(mailbox):
    try:
        last = (mailbox / "boar-census").read_text().splitlines()[-1].split("|")
        return {key: int(last[index]) for key, index in (
            ("receipts", 0), ("count", 4), ("replicated", 5), ("denials", 6), ("brain_denials", 7),
            ("calls", 8), ("authority", 9), ("simulated", 10), ("other", 11), ("simulated_controllers", 12),
            ("authority_network_ready", 20), ("local_begins", 21), ("local_initializations", 22),
        )} | {"stage": last[1], "marker": last[2], "data": last[3], "actors": last[14].rstrip(";").split(";")}
    except (OSError, IndexError, ValueError):
        return {}


async def proof(work, combat=False, squad="single", production=False, corpse=False):
    profile = "boar-proof-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / profile / "run"
    guest = work / "clients" / profile / "run"
    host.mkdir(parents=True)
    runtime_copy = host / "runtime-copy"
    shutil.copytree(Path(__file__).resolve().parents[1] / "tools", runtime_copy / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    cli = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    environment = {**os.environ, "SILVERFISH_COOP_BOAR_SQUAD": squad}
    for flag in ("CAMPAIGN_NPC", "BOAR_FIXTURE", "CROSSROADS_ROSTER"):
        environment.pop("SILVERFISH_COOP_NATIVE_" + flag, None)
    if not production:
        environment.update(SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC="1", SILVERFISH_COOP_NATIVE_BOAR_FIXTURE="1")
    if combat:
        environment["SILVERFISH_COOP_BOAR_PROBE_SHOT"] = "1"
    processes = []

    async def start(run, *args):
        with (host / f"{args[0]}.txt").open("wb") as log:
            child = await asyncio.create_subprocess_exec(*cli, *args, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW, env=environment, cwd=runtime_copy)
        processes.append((child, run))

    async def output(*args):
        child = await asyncio.create_subprocess_exec(*cli, *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW, env=environment, cwd=runtime_copy)
        stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
        if child.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
        return json.loads(stdout)

    async def probe(mailbox, command):
        path = mailbox / "boar-reply"
        path.unlink(missing_ok=True)
        (mailbox / "boar-probe").write_text(command + "\n", newline="", encoding="ascii")
        async with asyncio.timeout(10):
            while True:
                try:
                    status, result = path.read_text().rstrip().split("|", 1)
                except (OSError, ValueError):
                    await asyncio.sleep(.05)
                    continue
                if status != "ok":
                    raise RuntimeError(f"{command}: {result}")
                return result

    stock_asset = {"single": "WildLife/Boar_Single", "group": "WildLife/Boar_SmallGroup",
                   "husks": "Monsters/Husks_Scattered"}[squad]
    family = "husk" if squad == "husks" else "boar"

    def host_ready(value):
        return (value.get("receipts", 0) > 0 and value.get("replicated", 0) > 0
                and value.get("stage") in ("stock_initial", "stock_refresh")
                and stock_asset in value.get("data", "")
                and value.get("authority_network_ready", 0) >= value["replicated"])

    def guest_ready(value, count):
        return (value.get("simulated", 0) == count and value.get("authority", -1) == 0
                and value.get("simulated_controllers", -1) == 0 and value.get("other", -1) == 0
                and value.get("local_begins", -1) == 0 and value.get("local_initializations", -1) == 0)

    with staged_probe(work, combat):
        try:
            await start(host, "host", "--campaign", profile, "--port", "0", "--headless", "--fixtures",
                        "--native-zones", "--native-events", "--native-guest", "--start-zone", MAP)
            await wait(host, lambda row: row.get("state") == "hosting")
            invitation = await output("invite", "--campaign", profile, "--name", "Boar observer", "--character", "Boar observer")
            await start(guest, "join", "--profile", profile, "--invite", invitation["invite_file"], "--headless",
                        "--native-zones", *(("--native-guest-primary-input",) if combat else ()),
                        *(("--native-guest-inventory-view",) if production else ()))
            await wait(guest, lambda row: row.get("state") == "connected" and row.get("gameplay_fresh"))
            host_box, guest_box = [next((side / "game-bridge").iterdir()) for side in (host, guest)]
            before = census(host_box)
            refreshes = 0
            while not host_ready(before) and refreshes < 7:
                refreshes = int(await probe(host_box, f"refresh|{refreshes + 1}"))
                before = census(host_box)
                await asyncio.sleep(.95)
                before = census(host_box)
            assert host_ready(before), (before, census(guest_box))
            guest_pre = census(guest_box)
            marker = before["marker"].split(" ", 1)[1]
            result = await probe(guest_box, f"veto|{marker}")
            assert result == ("stock refresh" if production else "0"), result
            async with asyncio.timeout(8):
                while census(guest_box).get("denials", 0) <= guest_pre["denials"]:
                    await asyncio.sleep(.15)
            target = await probe(host_box, "approach")
            async with asyncio.timeout(12):
                while not guest_ready(census(guest_box), before["authority_network_ready"]):
                    await asyncio.sleep(.15)
            guest_post = census(guest_box)
            assert guest_ready(guest_post, before["authority_network_ready"]), guest_post
            assert guest_post["stage"] == f"stock_client_{family}_spawn_denied", guest_post
            def positions(raw, role):
                actors = []
                for entry in raw.split(";"):
                    name, observed_role, x, y, z, alive, health = entry.split("|")
                    assert int(observed_role) == role and alive == "true", entry
                    if role == 3:
                        assert float(health) > 0, entry
                    actors.append({"name": name, "location": [float(x), float(y), float(z)], "health": float(health)})
                return actors

            host_actors = positions(await probe(host_box, "position"), 3)
            guest_actors = positions(await probe(guest_box, "position"), 1)
            assert len(host_actors) == len(guest_actors) == before["authority_network_ready"]
            assert all(min(math.dist(peer["location"], actor["location"]) for peer in guest_actors) < 850
                       for actor in host_actors), (host_actors, guest_actors)
            report = {"target": target, "host": before, "guest": guest_pre, "after_veto": guest_post,
                      "fixture_output": result, "refreshes": refreshes, "host_stock": host_actors,
                      "guest_simulated": guest_actors}
            if production:
                expected = before["authority_network_ready"]
                count_field = "husks" if squad == "husks" else "boars"
                roster = (await wait(host, lambda row: row.get("game", {}).get(
                    "native_campaign_npc_host_roster", {}).get(count_field) == expected))["game"]["native_campaign_npc_host_roster"]
                report["production_host_roster"] = roster
                if squad == "single":
                    report["local_replica_damage"] = await probe(guest_box, "localdamage")
            result_path = host / "live-boar-encounter.json"
            result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(result_path, flush=True)
            if combat:
                guid = uuid.uuid4().hex.upper()
                seeded = await output("action", "--campaign", profile, "native-guest-weapon-seed-light-pistol",
                                      "--target", "guest", "--item-guid", guid)
                equipped = await output("action", "--profile", profile, "native-guest-weapon-equip", "--target", guid)
                assert seeded.get("ok") and equipped.get("ok"), (seeded, equipped)
                await wait(guest, lambda row: row.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
                source = (await wait(host, lambda row: row.get("game", {}).get("pawns", {}).get(
                    "guest", {}).get("name")))["game"]["pawns"]["guest"]["name"]
                damage_file = host_box / "boar-damage"
                witness = []
                for action in ("arm", "armlethal"):
                    damage_file.unlink(missing_ok=True)
                    starting = float(await probe(host_box, action + "|" + target.split(" ", 1)[1]))
                    shot = await output("action", "--profile", profile, "native-guest-primary-input-fixture")
                    assert shot.get("ok"), shot
                    async with asyncio.timeout(12):
                        while not damage_file.is_file():
                            await asyncio.sleep(.15)
                    victim, shooter, reported, after, alive = damage_file.read_text().strip().split("|")
                    assert (victim, shooter, float(reported)) == (target, source, starting)
                    if action == "arm":
                        assert 0 < float(after) < starting and alive == "true", (starting, after, alive)
                    else:
                        assert float(after) <= 0 and alive == "false", (starting, after, alive)
                    witness.append({"victim": victim, "guest_shooter": shooter, "health_before": starting,
                                    "health_after": float(after), "alive": alive == "true", "primary_input": shot})
                await asyncio.sleep(1)
                assert await probe(guest_box, "death") == "1"
                (host / "live-boar-stock-combat.json").write_text(json.dumps(witness, indent=2), encoding="utf-8")
                if corpse:
                    async def position(box, action):
                        return [float(value) for value in (await probe(box, action)).split("|")]

                    initial = await position(host_box, "corpseposition")
                    initial_guest = await position(guest_box, "corpseposition")
                    await position(host_box, "corpsemove")
                    await asyncio.sleep(2)
                    moved = await position(host_box, "corpseposition")
                    moved_guest = await position(guest_box, "corpseposition|moved")
                    assert math.dist(initial, moved) > 200 and math.dist(initial_guest, moved_guest) > 200, (
                        initial, initial_guest, moved, moved_guest)
                    assert math.dist(moved[:2], moved_guest[:2]) < 30, (moved, moved_guest)
                    assert abs(moved[2] - moved_guest[2]) < 60, (moved, moved_guest)
                    (host / "live-boar-corpse-movement.json").write_text(json.dumps(
                        {"host_before": initial, "guest_before": initial_guest,
                         "host_after": moved, "guest_after": moved_guest}, indent=2), encoding="utf-8")
        finally:
            for process, run in reversed(processes):
                if run.is_dir():
                    (run / "stop").touch()
                try:
                    await asyncio.wait_for(process.wait(), 8)
                except TimeoutError:
                    process.kill()
                    await process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--combat", action="store_true")
    parser.add_argument("--squad", choices=("single", "group", "husks"), default="single")
    parser.add_argument("--production-roster", action="store_true")
    parser.add_argument("--corpse", action="store_true")
    options = parser.parse_args()
    if options.squad == "husks" and not options.production_roster:
        parser.error("The Husk trial exercises the production roster")
    if options.squad != "single" and options.combat:
        parser.error("The stock pistol trial requires one boar")
    if options.corpse and not options.combat:
        parser.error("--corpse requires --combat")
    asyncio.run(proof(options.work.resolve(), combat=options.combat, squad=options.squad,
                      production=options.production_roster, corpse=options.corpse))
