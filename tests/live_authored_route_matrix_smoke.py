"""Exercise a continuous authored route chain with guest and distant-host vetoes."""

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from hashlib import sha256
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

from tools.silverfish import runtime
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.native_rest import site
from tools.silverfish.travel import CROSSROADS, GOLD_NEXUS, OBSERVATORY


def select_routes(selectors):
    routes = []
    for selector in selectors:
        parts = selector.split("@")
        if len(parts) not in (2, 3):
            raise ValueError("Use MAP@ENTRANCE or MAP@ENTRANCE@ACTOR")
        candidates = [route for route in AUTHORED_ROUTES
                      if route.source.rsplit("/", 1)[-1] == parts[0] and route.entrance == parts[1]
                      and (len(parts) == 2 or route.actor == parts[2])]
        if len(candidates) != 1:
            raise ValueError(f"{selector} must identify one stock exit")
        routes.append(candidates[0])
    if not routes or routes[0].source == GOLD_NEXUS:
        raise ValueError("Choose a route from a normal authored campaign zone")
    if any(first.destination != second.source for first, second in zip(routes, routes[1:])):
        raise ValueError("Each exit must begin in the previous destination")
    return routes



@contextmanager
def staged_probe(work, name):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    fixture = scripts / f"{name}.lua"
    main = scripts / "main.lua"
    original = main.read_bytes()
    manifest = runtime.stage_manifest(work / "stage")
    manifest_bytes = manifest.read_bytes()
    metadata = json.loads(manifest_bytes)
    with fixture.open("xb") as handle:
        handle.write((Path(__file__).with_name("fixtures") / fixture.name).read_bytes())
    try:
        main.write_bytes(original + f'\nrequire("{name}")\n'.encode("ascii"))
        if isinstance(inventory := metadata.get("lua_files"), dict):
            inventory.update({path.name: sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                              for path in (main, fixture)})
            manifest.write_text(json.dumps(metadata), encoding="utf-8")
        yield
    finally:
        manifest.write_bytes(manifest_bytes)
        main.write_bytes(original)
        fixture.unlink()


async def probe_command(run, name, action, argument):
    mailbox, = (run / "game-bridge").iterdir()
    ticket = uuid.uuid4().hex
    runtime.write_text(mailbox / f"{name}-command.tsv", f"{ticket}\t{action}\t{argument}")
    async with asyncio.timeout(8):
        while True:
            try:
                row = (mailbox / f"{name}-reply.tsv").read_text(encoding="utf-8").split("\t", 2)
            except (FileNotFoundError, PermissionError):
                row = []
            if len(row) == 3 and row[0] == ticket:
                if row[1] != "ok":
                    raise RuntimeError(f"{name} {action} failed: {row[2]}")
                return row[2]
            await asyncio.sleep(.05)


async def verify(work, vanilla, routes, check_regroup=False, sleep_and_wipe=False, insomnia=False,
                 check_network_budget=False, check_transition_presentation=False, fast_boundary=False):
    name = "authored-route-" + uuid.uuid4().hex[:8]
    campaign = ("--campaign", name)
    profile = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla)
    processes = []
    evidence = {"legs": []}

    async def call(*args):
        job = await asyncio.create_subprocess_exec(
            *cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output, errors = await asyncio.wait_for(job.communicate(), 25)
        if job.returncode:
            raise RuntimeError(errors.decode(errors="replace") or output.decode(errors="replace"))
        return json.loads(output)

    async def launch(owner, label, *args):
        with (run / (label + ".txt")).open("wb") as log:
            job = await asyncio.create_subprocess_exec(
                *cli, *args, cwd=REPO, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        processes.append((owner, job))

    async def act(action, **details):
        result = await submit(run, action, **details)
        if result.get("ok") is not True:
            raise RuntimeError(f"{action} refused with {result.get('error')}")
        return result

    def events(event, generation):
        journal = (run / "events.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
        return [entry for line in journal if line.endswith("\n") and
                (entry := json.loads(line)).get("event") == event and entry.get("generation") == generation]

    async def paired(area, previous_epoch=None):
        joined = await wait(run, lambda state: state.get("state") == "hosting"
                            and state.get("game", {}).get("map") == area
                            and state.get("zone_epoch") != previous_epoch and state.get("link_connected")
                            and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await wait(client_run, lambda state: state.get("state") == "connected" and state.get("gameplay_fresh")
                   and state.get("character_id") == invite["character_id"]
                   and state.get("host_session") == joined["generation"]
                   and state.get("zone_epoch") == joined["zone_epoch"] and state.get("game", {}).get("map") == area,
                   timeout=55)
        return joined

    try:
        await launch(run, "host", "host", *campaign, "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--native-expanded-zones", "--native-zone-walls",
                     "--start-zone", routes[0].source, "--port", "0")
        await wait(run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == routes[0].source)
        invite = await call("invite", *campaign, "--name", "Stock route observer", "--character", "Pathfinder")
        await launch(client_run, "client", "join", *profile, "--headless", "--invite", invite["invite_file"])
        current = await paired(routes[0].source)
        if check_network_budget:
            rated = await wait(run, lambda frame: len(frame.get("game", {}).get("native_replication_rates", {})) == 2)
            rates = rated["game"]["native_replication_rates"]
            assert all(row["cap_hz"] == min(row["stock_hz"], 24) for row in rates.values()), rates
            evidence["native_replication_rates"] = rates
        if sleep_and_wipe:
            _, bed, actor = site(routes[0].source)
            await wait(run, lambda state: state.get("game", {}).get("native_rest_hook", {}).get("fresh") is True)
            refusal = (await act("native-rest-guest-interaction-fixture", target="guest"))["native_rest_guard"]
            assert refusal["status"] == "guest_interaction_denied", refusal
            approach = (await act("native-rest-approach"))["native_rest"]
            assert (approach["bed"], approach["actor"], approach["eligible"]) == (bed, actor, True), approach
            if insomnia:
                selection = (await act("native-rest-insomnia-fixture"))["native_rest_hook"]
                assert selection["status"] == "selected_insomnia", selection
            else:
                selection = (await act("native-rest-ui-fixture", amount=1))["native_rest_hook"]
                assert selection["status"] == "selected", selection
            await wait(run, lambda state: state.get("campaign", {}).get("world", {}).get("native_rest", {}).get("bed") == bed)
            sleep, = events("native_rest_checkpoint", current["generation"])
            hours = sleep["hours"]
            assert (1 <= hours <= 10 and sleep["insomnia"] is True) if insomnia else hours == 1, sleep
            assert sleep["bed"] == bed and abs(sleep["after"] - sleep["before"] - hours * 3600) < .25, sleep
            evidence["stock_rest"] = {"guest_refusal": refusal, "selection": selection, "sleep": sleep}
        for route in routes:
            door = route.kind == "door"
            options = {"entrance": route.entrance}
            if not door:
                options["exit_actor"] = route.actor
            position = "native-door-position" if door else "native-exit-position"
            probe = "native-door-probe" if door else "native-exit-wall-probe"
            key, field = ("native_door", "decision") if door else ("native_zone_wall", "outcome")
            leg = {"profile": asdict(route), "source_epoch": current["zone_epoch"]}
            evidence["legs"].append(leg)

            async def near_probe(target, label, expected, preferred=None, group=False):
                attempts = leg.setdefault("approach_attempts", {}).setdefault(label, [])
                alternatives = [None] if door or route.source == GOLD_NEXUS else [None, *range(1, 9)]
                for lane in [preferred, *(lane for lane in alternatives if lane != preferred)]:
                    attempt = {"lane": lane}
                    attempts.append(attempt)
                    approach = {**options, "exit_position": "near"}
                    if lane is not None:
                        approach["amount"] = lane
                    for member in (target, "guest") if group else (target,):
                        placed = await submit(run, position, target=member, **approach)
                        if placed.get("ok") is not True:
                            error = placed.get("error", "")
                            if not error.endswith("No verified stock floor at the requested expanded overlap position"):
                                raise RuntimeError(f"{position} refused with {error}")
                            attempt["placement_failure"] = member
                            break
                    if "placement_failure" in attempt:
                        continue
                    if group and not door:
                        await wait(run, lambda state: state.get("game", {}).get("native_zone_wall", {}).get("host_open"))
                    observed = (await act(probe, target=target, **options))[key]
                    attempt["probe"] = observed
                    if group and not door and observed[field] == "other":
                        # A stock overlap can start travel before a subsequent sweep hit.
                        for _ in range(30):
                            crossed = next((entry for entry in events("native_authored_zone_request", current["generation"])
                                            if entry["source"] == route.source and entry["destination"] == route.destination
                                            and entry["actor"] == route.actor and entry["entrance"] == route.entrance
                                            and entry["result"]["ok"] is True
                                            and entry["result"]["travel_epoch"] != current["zone_epoch"]), None)
                            if crossed:
                                leg["overlap_travel"] = crossed
                                leg[label] = observed
                                return lane
                            await asyncio.sleep(0.2)
                    if observed[field] != "other":
                        leg[label] = observed
                        if observed[field] not in expected:
                            raise RuntimeError(f"{route.actor} {label} returned {observed[field]}, expected {expected}")
                        return lane
                raise RuntimeError(f"All tested approaches to {route.actor} were obstructed")

            if fast_boundary:
                await act(position, target="host", exit_position="near", **options)
                pawn = current["game"]["pawns"]["guest"]["name"]
                placed = await probe_command(run, "fast-stock-exit", "place", pawn)
                assert 1470 <= float(placed) <= 1490, placed
                leg["boundary"] = {"guest": pawn, "placed_cm": float(placed)}
                wall_ready = await wait(run, lambda state: state["zone_epoch"] == current["zone_epoch"]
                                        and state.get("game", {}).get("native_zone_wall", {}).get("host_open"))
                before, beyond, crossed, swept = (await probe_command(run, "fast-stock-exit", "cross", pawn)).split(",", 3)
                leg["boundary"].update(admitted_cm=float(before), before_overlap_cm=float(beyond),
                                       after_sweep_cm=float(crossed), stock_sweep=swept,
                                       source_sequence=wall_ready["game"]["sequence"])
                assert 1465 <= float(before) < 1500 < min(float(beyond), float(crossed)), leg["boundary"]
            else:
                guest_lane = await near_probe("guest", "guest_veto", ("guest_denied",) if door else ("wall",))
                distant = await submit(run, position, target="guest", exit_position="far", **options)
                if door and not distant.get("ok") and distant.get("error", "").endswith(
                        "No verified stock floor near the named transition door"):
                    # An enclosed corridor may only offer walkable ground beside its other stock door.
                    for anchor in AUTHORED_ROUTES:
                        if anchor.source != route.source or anchor.kind != "door" or anchor.entrance == route.entrance:
                            continue
                        placement = await submit(run, position, target="guest", exit_position="near", entrance=anchor.entrance)
                        if placement.get("ok"):
                            leg["distant_door_anchor"] = anchor.actor
                            distant = placement
                            break
                if not distant.get("ok"):
                    raise RuntimeError(f"{position} refused with {distant.get('error')}")
                host_lane = await near_probe("host", "distance_veto", ("host_out_of_range",) if door else ("wall",), guest_lane)
                if check_regroup:
                    named = await wait(run, lambda state: any(
                        member["role"] == "guest" and member["name"] == "Pathfinder" and member["distance_m"] > 15
                        for member in state.get("game", {}).get("native_zone_regroup", {}).get("blockers", [])), timeout=12)
                    leg["regroup"] = named["game"]["native_zone_regroup"]
                    assert leg["regroup"]["map"] == route.source and leg["regroup"]["display"] == "headless", leg["regroup"]
                await near_probe("host", "group_probe", ("host_opening", "travel_requested") if door else ("clear",),
                                 host_lane, group=True)
            current = await paired(route.destination, current["zone_epoch"])
            event, = (entry for entry in events("zone_travel_ready", current["generation"])
                      if entry.get("epoch") == current["zone_epoch"])
            arrival = event["authored_arrival"]
            assert arrival.get("actor", "").endswith("." + route.arrival_actor) or (
                arrival.get("actor", "").endswith(":" + route.arrival_actor)
            ), arrival
            leg["arrival"] = arrival
            leg["destination_epoch"] = current["zone_epoch"]
            if fast_boundary:
                request, = [entry for entry in events("native_authored_zone_request", current["generation"])
                            if (entry["source"], entry["destination"], entry["entrance"], entry["actor"]) ==
                               (route.source, route.destination, route.entrance, route.actor)]
                assert type(request.get("exit_crossing")) is int and request["exit_crossing"] > 0, request
                assert request["result"]["ok"] is True and request["result"]["travel_epoch"] == current["zone_epoch"], request
                leg["boundary"]["native_crossing"] = request
                leg["boundary"]["party_arrived"] = True
        if check_network_budget or check_transition_presentation:
            journal = (client_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
            covered = [entry for line in journal if (entry := json.loads(line)).get("event") == "native_zone_loading_transition"]
            assert [entry["destination"] for entry in covered] == [route.destination for route in routes], covered
            assert all(entry["covered"] for entry in covered), covered
            evidence["loading_cover"] = covered
        if check_transition_presentation:
            expected = [route.destination for route in routes]
            for seat, owner in (("host", run), ("guest", client_run)):
                journal = (owner / "events.jsonl").read_text(encoding="utf-8").splitlines()
                presented = [entry for line in journal
                             if (entry := json.loads(line)).get("event") == "zone_transition_presentation"]
                assert [entry["destination"] for entry in presented] == expected, presented
                assert all(entry["covered"] and entry["cue"] for entry in presented), presented
                await wait(owner, lambda state: state.get("game", {}).get("map") == expected[-1]
                           and "native_zone_loading_cover" not in state["game"], timeout=10)
                evidence.setdefault("transition_presentation", {})[seat] = presented
        if sleep_and_wipe:
            await act("native-guest-damage", target="guest")
            await wait(run, lambda state: state.get("gameplay", {}).get("players", {}).get("guest", {}).get("downed"))
            await act("native-host-damage", target="host")
            returned = await paired(routes[0].source, current["zone_epoch"])
            healthy = await wait(run, lambda state: state.get("zone_epoch") == returned["zone_epoch"]
                                 and not state.get("gameplay", {}).get("wipe") and not state["gameplay"]["pending_spawns"]
                                 and all(state["game"]["pawns"].get(role, {}).get("health") == 250.0
                                         for role in ("local", "guest")), timeout=55)
            respawn, = events("party_respawn_committed", returned["generation"])
            assert respawn["stock_rest"] and math.dist(respawn["location"], sleep["location"]) < 400, respawn
            assert healthy["gameplay"]["players"]["guest"]["character_id"] == invite["character_id"], healthy
            evidence["stock_rest"]["respawn"] = respawn
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(processes):
            if process.returncode is None:
                try:
                    await submit(owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["workers_closed"] = all(process.returncode is not None for _, process in processes)
        evidence["original_saves_verified"] = saved_hashes(vanilla) == originals
        (run / "live-authored-route-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original Saved directory changed during the run"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--route", action="append", required=True, metavar="MAP@ENTRANCE[@ACTOR]")
    parser.add_argument("--verify-regroup-message", action="store_true")
    parser.add_argument("--fast-boundary", action="store_true", help="verify the exact Observatory 15 m approach crossing")
    parser.add_argument("--verify-network-budget", action="store_true")
    parser.add_argument("--verify-transition-presentation", action="store_true")
    parser.add_argument("--sleep-and-wipe", action="store_true",
                        help="sleep at the starting map's stock bed and return there after a wipe")
    parser.add_argument("--stock-insomnia", action="store_true", help="verify the stock Insomnia roll at a neutral bed")
    config = parser.parse_args()
    try:
        selected = select_routes(config.route)
        if config.sleep_and_wipe and (site(selected[0].source) is None or selected[-1].destination == selected[0].source):
            raise ValueError("Choose a starting map with a stock bed and finish in a different map")
        if config.fast_boundary and (len(selected) != 1 or (selected[0].source, selected[0].destination,
                selected[0].actor, selected[0].entrance) != (OBSERVATORY, CROSSROADS, "Level_Exit_BP_C_1", "Obsv_Etr")):
            raise ValueError("Choose only Observitory_P@Obsv_Etr@Level_Exit_BP_C_1 for --fast-boundary")
        if config.stock_insomnia and not config.sleep_and_wipe:
            raise ValueError("--stock-insomnia requires --sleep-and-wipe at a neutral bed")
    except ValueError as error:
        parser.error(str(error))
    with staged_probe(config.work.resolve(), "fast_stock_exit_probe") if config.fast_boundary else nullcontext():
        asyncio.run(verify(config.work.resolve(), config.vanilla_saved.resolve(), selected,
                           config.verify_regroup_message, config.sleep_and_wipe, config.stock_insomnia,
                           config.verify_network_budget, config.verify_transition_presentation, config.fast_boundary))
