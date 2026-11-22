"""Verify guest Interact, carry and stock throw of a real crate junk prop."""

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.silverfish import native_lantern as lantern, native_loose_pickup as loose, native_prop_carry as carry
from tools.silverfish.control import submit
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, OBSERVATORY

CRATE = f"{OBSERVATORY}.Observitory_P:PersistentLevel.LootCrate_Small_BP_C_1"


async def exercise(work, untouched, kind="crate"):
    token = f"{kind}-carry-{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", token), ("--profile", token)
    hrun, grun = work / "campaigns" / token / "run", work / "clients" / token / "run"
    hrun.mkdir(parents=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    original = saved_hashes(untouched)
    processes = []
    print(hrun, flush=True)

    async def command(*argv):
        process = await asyncio.create_subprocess_exec(*cli, *argv, cwd=ROOT, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, err = await asyncio.wait_for(process.communicate(), 30)
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
        if process.returncode is None:
            try:
                await command("action", *controller, "stop")
                await asyncio.wait_for(process.wait(), 10)
            except (RuntimeError, asyncio.TimeoutError):
                if process.returncode is None:
                    process.terminate()
                    await process.wait()

    async def fixture(controller, target):
        return await command("action", *controller, carry.FIXTURE, "--target", target)

    def prop(response):
        return response.get("game", {}).get(carry.FIXTURE, {})

    def proof(response):
        return response.get("game", {}).get(carry.PROOF, {})

    def lamp(response):
        return response.get("game", {}).get(lantern.KEY, {}).get("lit")

    async def verify_fuel_drum(initial):
        actor_path = initial["actor"].split(" ", 1)[1]
        await fixture(host, "approach-placed@guest")

        async def sample(controller):
            result = await fixture(controller, "sample@" + actor_path)
            return [float(value) for value in result["sample"].split("|")]

        def angle(first, second):
            return abs((first - second + 180) % 360 - 180)

        before = await sample(host)
        async with asyncio.timeout(10):
            while math.dist((await sample(guest))[:3], before[:3]) >= 8:
                await asyncio.sleep(.2)
        await fixture(host, "tip@" + actor_path)
        authority = replica = None
        try:
            async with asyncio.timeout(14):
                while True:
                    authority, replica = await asyncio.gather(sample(host), sample(guest))
                    tipped = max(angle(authority[index], before[index]) for index in (3, 5)) > 22
                    matched = math.dist(authority[:3], replica[:3]) < 10 and all(
                        angle(first, second) < 9 for first, second in zip(authority[3:], replica[3:], strict=True))
                    if tipped and matched:
                        return {"actor": initial["actor"], "before": before, "host": authority, "guest": replica,
                                "original_files_verified": len(original)}
                    await asyncio.sleep(.2)
        except TimeoutError as error:
            raise RuntimeError(f"Fuel drum did not match after tipping. Before {before}, host {authority}, guest {replica}") from error

    try:
        assert original
        fuel_drum = kind in ("fuel-drum", "fuel-drum-grab")
        area = CROSSROADS if fuel_drum or kind in ("placed-prop", "placed-item") else DEAD_BRIDGE if kind == "barrel" else OBSERVATORY
        await start("host", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                    "--native-guest", "--native-zones", "--native-expanded-zones", "--start-zone", area)
        barrel_class = "Prop_FuelDrum_C " if fuel_drum else "Prop_PlasticBarrel_C " if kind == "barrel" else None
        placed_name = f"Prop_TypeWriter_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Prop_TypeWriter_C_3"

        def barrels_of_class(state):
            return [item for item in state.get("game", {}).get(carry.BARRELS, {}).values()
                    if barrel_class and item["actor"].startswith(barrel_class)]

        def enrolled(state):
            if kind == "placed-prop":
                return placed_name in state.get("game", {}).get(carry.PLACED_PROPS, {})
            if kind == "placed-item":
                return any(item["class_path"].startswith("/Game/Classes/") for item in
                           state.get("game", {}).get(carry.PLACED_PROPS, {}).values())
            if fuel_drum:
                return any(item["actor"].endswith(".Prop_FeulDrum_C_33") for item in barrels_of_class(state))
            return not barrel_class or barrels_of_class(state)

        initial_world = await wait(hrun, lambda state: state.get("state") == "hosting" and enrolled(state))
        invite = await command("invite", *host, "--name", "Crate carrier", "--character", "Crate carrier")
        guest_process = await start("guest", guest, "join", *guest, "--invite", invite["invite_file"], "--headless")
        await wait(grun, lambda state: state.get("state") == "connected")
        await wait(hrun, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        if kind == "crate":
            wanted = f"spawn@{CRATE}"
        elif kind == "lantern":
            wanted = "select@" + lantern.ACTOR.split(" ", 1)[1]
        elif kind in ("placed-prop", "placed-item"):
            if kind == "placed-item":
                selected = min((item for item in initial_world["game"][carry.PLACED_PROPS].values()
                                if item["class_path"].startswith("/Game/Classes/")),
                               key=lambda item: (item["mass"], item["actor"]))
                placed_name = selected["actor"]
            wanted = "select@" + placed_name.split(" ", 1)[1]
        else:
            barrels = barrels_of_class(initial_world)
            selected = (next(item for item in barrels if item["actor"].endswith(".Prop_FeulDrum_C_33"))
                        if fuel_drum else min(barrels, key=lambda item: item["mass"]))
            wanted = "select@" + selected["actor"].split(" ", 1)[1]
        initial = (await fixture(host, wanted))[carry.FIXTURE]
        if kind == "fuel-drum":
            evidence = await verify_fuel_drum(initial)
            (hrun / "fuel-drum-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return
        await fixture(host, "approach-placed@guest" if fuel_drum else "approach@guest")
        await asyncio.sleep(.65)
        if kind == "lantern":
            await command("action", *host, lantern.FIXTURE, "--target", "ignite")
            await wait(grun, lambda state: lamp(state) is True)
            await fixture(guest, f"lamp-use@{initial['class_path']}")
            await wait(hrun, lambda state: lamp(state) is False)
            await wait(grun, lambda state: lamp(state) is False)
            await command("action", *host, lantern.FIXTURE, "--target", "ignite")
            await wait(grun, lambda state: lamp(state) is True)
        input_mode = "grab" if kind == "crate" or fuel_drum else "pickup"
        target = initial["class_path"]
        if kind == "lantern":
            target = f"{target}|1|{initial['actor']}"
        elif kind != "crate":
            target = f"{target}|{initial['actor']}"
        await fixture(guest, f"{input_mode}@{target}")
        grabbed = await wait(hrun, lambda state: prop(state).get("held") and prop(state).get("grip")
                             and proof(state).get("edge") == "grab")
        assert proof(grabbed)["actor"] == initial["actor"], proof(grabbed)
        await fixture(guest, "turn")
        moved = await wait(hrun, lambda state: prop(state).get("held") and prop(state).get("grip")
                           and math.dist(prop(state)["grip"], prop(grabbed)["grip"]) > 50
                           and math.dist(prop(state)["position"], prop(grabbed)["position"]) > 25)
        if kind == "lantern":
            await stop(guest_process, guest)
            released = await wait(hrun, lambda state: prop(state).get("actor") == initial["actor"]
                                  and prop(state).get("held") is False and lamp(state) is True)
            effect = dict(released_after_disconnect=prop(released), normal_interact_extinguished=True,
                          stock_fire_relit=True, remained_lit_after_disconnect=True)
        else:
            await fixture(guest, "throw@up" if kind in ("placed-prop", "placed-item") else "throw")
            thrown = await wait(hrun, lambda state: proof(state).get("edge") == "throw" and not prop(state).get("held", True))
            moved_again = await wait(hrun, lambda state: prop(state).get("actor") == initial["actor"] and
                                     math.dist(prop(state)["position"], prop(moved)["position"]) > 70)
            effect = dict(thrown=prop(thrown), landed=prop(moved_again), throw_receipt=proof(thrown))
            if kind in ("placed-prop", "placed-item"):
                path = placed_name.split(" ", 1)[1]
                async with asyncio.timeout(8):
                    while True:
                        authority, replica = await asyncio.gather(fixture(host, f"sample@{path}"), fixture(guest, f"sample@{path}"))
                        host_pose, guest_pose = ([float(value) for value in row["sample"].split("|")] for row in (authority, replica))
                        if math.dist(host_pose[:3], guest_pose[:3]) <= 10:
                            effect.update(host_pose=host_pose, guest_pose=guest_pose)
                            break
                        await asyncio.sleep(.15)
            if kind == "placed-item":
                target = "loose:" + path
                for role in ("host", "guest"):
                    await command("action", *host, loose.APPROACH, "--target", f"{role}@{target}")
                await command("action", *guest, loose.FIXTURE, "--target", target)
                for run in (hrun, grun):
                    await wait(run, lambda state: state.get("game", {}).get(loose.KEY, {}).get(path, {}).get("state") == "claimed")
                events = [json.loads(line) for line in (hrun / "events.jsonl").read_text().splitlines()]
                applied, = [event for event in events if event.get("event") == "native_loose_pickup_claimed" and event.get("actor") == path]
                assert applied["owner"] == invite["character_id"], applied
                inventory = (await command("action", *guest, "native-inventory-list"))["native_inventory"]["items"]
                item, = [row for row in inventory if row["guid"] == applied["guid"]]
                assert item["class_path"] == initial["class_path"], item
                repeated = await submit(hrun, loose.CLAIM, target=target)
                assert not repeated["ok"] and repeated["error"] == "loose_pickup_already_claimed", repeated
                effect.update(stock_pickup=applied, backpack=item, competing_claim=repeated["error"])
        (hrun / "prop-carry-verification.json").write_text(json.dumps(dict(
            initial=initial, grabbed=prop(grabbed), moved=prop(moved),
            grab_receipt=proof(grabbed), canary_files=len(original), **effect,
        ), indent=2), encoding="utf-8")
    finally:
        for process, controller in reversed(processes):
            await stop(process, controller)
        assert saved_hashes(untouched) == original


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    parser.add_argument("--kind", choices=("crate", "lantern", "barrel", "fuel-drum", "fuel-drum-grab", "placed-prop", "placed-item"),
                        default="crate")
    options = parser.parse_args()
    asyncio.run(exercise(options.work.resolve(), options.vanilla_saved.resolve(), options.kind))
