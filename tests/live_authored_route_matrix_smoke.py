"""Exercise a continuous authored route chain with guest and distant-host vetoes."""

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.travel import GOLD_NEXUS


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


async def verify(work, vanilla, routes):
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

    async def paired(area, previous_epoch=None):
        joined = await wait(run, lambda state: state.get("state") == "hosting"
                            and state.get("game", {}).get("map") == area
                            and state.get("zone_epoch") != previous_epoch and state.get("link_connected")
                            and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound", timeout=55)
        await wait(client_run, lambda state: state.get("state") == "connected" and state.get("gameplay_fresh")
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
                    if observed[field] != "other":
                        leg[label] = observed
                        if observed[field] not in expected:
                            raise RuntimeError(f"{route.actor} {label} returned {observed[field]}, expected {expected}")
                        return lane
                raise RuntimeError(f"All tested approaches to {route.actor} were obstructed")

            guest_lane = await near_probe("guest", "guest_veto", ("guest_denied",) if door else ("wall",))
            await act(position, target="guest", exit_position="far", **options)
            host_lane = await near_probe("host", "distance_veto", ("host_out_of_range",) if door else ("wall",), guest_lane)
            await near_probe("host", "group_probe", ("host_opening", "travel_requested") if door else ("clear",),
                             host_lane, group=True)
            current = await paired(route.destination, current["zone_epoch"])
            journal = (run / "events.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
            event, = [entry for line in journal if line.endswith("\n") and
                      (entry := json.loads(line)).get("event") == "zone_travel_ready"
                      and entry.get("generation") == current["generation"] and entry.get("epoch") == current["zone_epoch"]]
            arrival = event["authored_arrival"]
            assert arrival.get("actor", "").endswith("." + route.arrival_actor) or (
                arrival.get("actor", "").endswith(":" + route.arrival_actor)
            ), arrival
            leg["arrival"] = arrival
            leg["destination_epoch"] = current["zone_epoch"]
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
        evidence["original_saves_verified"] = saved_hashes(vanilla) == originals
        (run / "live-authored-route-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original Saved directory changed during the run"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--route", action="append", required=True, metavar="MAP@ENTRANCE[@ACTOR]")
    config = parser.parse_args()
    try:
        selected = select_routes(config.route)
    except ValueError as error:
        parser.error(str(error))
    asyncio.run(verify(config.work.resolve(), config.vanilla_saved.resolve(), selected))
