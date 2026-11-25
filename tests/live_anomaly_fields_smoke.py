"""Compare stock trap fields across a muted Direct IP join and reconnect."""

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
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish.control import submit
from tools.silverfish.travel import CISTERN, CROSSROADS, EAST_WING, LAB_E206

PLACED = {
    "stone-maze": ("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P", "spinning-ball",
                   "SpinningBall_Anomaly_C_1", 1),
    "cistern-gravity": (CISTERN, "gravity", "GravityAnomaly_BP_C_1", 1),
    "lab-e206": (LAB_E206, "burner", "BurnerTrap_BP_C_1", 2),
    "perdition": ("/Game/Classes/Maps/Maps_Dungeons/Perdition_01_P", "starburst", "Starburst_BP_C_5", 5),
    "funnel": ("/Game/Classes/Maps/Maps_Dungeons/Funnel_01_P", "vortex", "VortexTrap_BP_C_9", 12),
    "lab-east-wing": (EAST_WING, "electrode", "Electrode_BP_C_9", 11),
}


async def verify_glass_web(host, guest, positions):
    def strands(status):
        return [row for row in positions(status) if row["kind"] == "glass-thread"]

    def meshes(status):
        return status.get("game", {}).get("native_glass_thread_meshes", [])

    def matching(reference, remote):
        if len(reference) != len(remote):
            return False
        unmatched = remote.copy()
        for wanted in reference:
            for index, found in enumerate(unmatched):
                if (found["main"] == wanted["main"] and math.dist(found["anchor"], wanted["anchor"]) < 2
                        and math.dist(found["location"], wanted["location"]) < (2 if wanted["main"] else .1)
                        and math.dist(found["scale"], wanted["scale"]) < .05
                        and all(abs((a - b + 180) % 360 - 180) < (1.5 if wanted["main"] else .1)
                                for a, b in zip(found["rotation"], wanted["rotation"]))):
                    unmatched.pop(index)
                    break
            else:
                return False
        return True

    def by_strand(shapes):
        result = {}
        for shape in shapes:
            anchor = tuple(round(value) for value in shape["anchor"])
            result.setdefault(anchor, []).append(shape)
        return result

    initial = {}
    work = (await wait(host, lambda state: (work := state.get("game", {}).get("native_glass_thread_work", {}))
                       and work["deferred_batches"] > 0 and not work["pending"]))["game"]["native_glass_thread_work"]
    assert work["batches"] > 1 and 0 < work["peak"] <= 8, work

    def geometry_matches(client):
        nonlocal initial
        observed = status(host)
        host_shapes = by_strand(meshes(observed))
        guest_shapes = by_strand(meshes(client))
        if any(not row["main"] for row in meshes(client)) and all(
            matching(host_shapes.get(anchor, []), shapes) for anchor, shapes in guest_shapes.items()
        ):
            initial = observed
            return True
        return False

    try:
        passive = await wait(guest, geometry_matches)
    except RuntimeError:
        raise AssertionError("The guest did not receive the host geometry for the relevant Glass Threads") from None
    host_strands = strands(initial)
    guest_strands = strands(passive)
    assert host_strands and all(row["collision"] for row in host_strands), host_strands
    assert guest_strands and all(not row["collision"] and any(
        math.dist(row["location"], host_strand["location"]) < 2 for host_strand in host_strands
    ) for row in guest_strands), guest_strands
    hazards = [row["location"] for row in positions(initial) if row["kind"] != "glass-thread"]
    branches = {tuple(row["anchor"]) for row in meshes(passive) if not row["main"]}
    candidates = sorted((row["location"] for row in host_strands
                         if tuple(round(value) for value in row["location"]) in branches),
                        key=lambda point: min(math.dist(point, hazard) for hazard in hazards), reverse=True)
    # Sampling across the web avoids spending the entire trial on strands around one wall.
    candidates = candidates[::max(1, len(candidates) // 10)]

    def events():
        path = guest / "events.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in lines[:lines.rfind("\n")].splitlines()]

    def present(state, point):
        return any(math.dist(row["location"], point) < 2 for row in strands(state))

    baseline = await wait(host, lambda state: (pawns := state.get("game", {}).get("pawns", {}))
                          and all(pawns.get(seat, {}).get("health") for seat in ("local", "guest")))
    health = baseline["game"]["pawns"]
    observed = []

    # Other pawn components can start the stock break without taking damage.
    for point in candidates[:10]:
        local_view, peer_view = status(host), status(guest)
        if not present(local_view, point) or not present(peer_view, point):
            continue
        replica_point = next(row["location"] for row in strands(peer_view) if math.dist(row["location"], point) < 2)
        shapes = [row for row in meshes(peer_view) if math.dist(row["anchor"], point) < 2]
        if not any(row["main"] for row in shapes):
            continue
        guest_key = ",".join(f"{value:.0f}" for value in replica_point)
        previous = peer_view["game"]["native_anomaly_field"]["blocked_triggers"]
        refused = await submit(guest, "native-placed-anomaly-guest-fixture", target="glass-thread|" + guest_key)
        # A strand may leave relevancy while a previous contact is still settling.
        if not refused["ok"] and refused["error"] == "The stock Glass Thread is unavailable":
            continue
        assert refused["ok"] and refused["native_anomaly_field"]["blocked_triggers"] == previous + 1, refused
        still_passive = await wait(guest, lambda state: state["game"]["native_anomaly_field"]["blocked_triggers"] > previous)
        assert matching(shapes, [row for row in meshes(still_passive) if math.dist(row["anchor"], point) < 2])

        event_start = len(events())
        host_key = ",".join(f"{value:.0f}" for value in point)
        entered = await submit(host, "native-anomaly-trigger-fixture", target="glass-thread-contact|" + host_key)
        if not entered["ok"] and entered["error"] in {"The stock Glass Thread is unavailable",
                                                     "Unable to stage a stock guest overlap with the Glass Thread"}:
            continue
        assert entered["ok"], entered
        await wait(host, lambda state: strands(state) and not present(state, point), timeout=6)

        def snap_events():
            return [row for row in events()[event_start:] if row["event"] == "native_glass_thread_snap_played"
                    and math.dist(row["location"], point) < 2]

        await wait(guest, lambda state: strands(state) and not present(state, point) and snap_events(), timeout=8)
        cue, = snap_events()
        assert cue["particles"] and abs(cue["length"] - 100 * next(
            row["scale"][0] for row in shapes if row["main"])) < 1, cue
        assert math.dist(cue["break_point"], point) < cue["length"], cue
        observed.append({"destroyed_meshes": len(shapes), "snap": cue})

        def received_cut(state):
            host_hp = status(host).get("game", {}).get("pawns", {}).get("guest", {}).get("health")
            guest_hp = state.get("game", {}).get("pawns", {}).get("local", {}).get("health") or 0
            return (host_hp is not None and 0 < guest_hp < health["guest"]["health"]
                    and abs(guest_hp - host_hp) < 1
                    and any(row["event"] == "native_guest_hurt_played" and row["damage"] == 25
                            and abs(cue["time_unix"] - row["time_unix"]) < 1
                            for row in events()[event_start:]))

        try:
            synced = await wait(guest, received_cut, timeout=5)
        except RuntimeError:
            continue
        assert status(host)["game"]["pawns"]["local"]["health"] == health["local"]["health"]
        assert all(not row["collision"] for row in strands(synced)), strands(synced)
        return {"host_strands": len(host_strands), "guest_relevant_strands": len(guest_strands),
                "guest_relevant_meshes": len(meshes(passive)), "guest_passive": True,
                "contacts": observed, "mesh_publication": work,
                "guest_health": [health["guest"]["health"], synced["game"]["pawns"]["local"]["health"]]}
    raise AssertionError(f"No contact produced the stock 25-point cut after {len(observed)} verified snaps")


async def verify(work, vanilla, activate, placed, generated, glass_web=False):
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
    area = PLACED[placed][0] if placed else CROSSROADS
    jobs = []
    proof = {"verified": False}

    async def launch(run, label, *arguments):
        env = dict(os.environ)
        if (activate or glass_web) and placed != "stone-maze" and label == "guest":
            env["SILVERFISH_COOP_RENDER_CAPTURE"] = "guest"
        with (host / f"{label}.log").open("wb") as log:
            job = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log, cwd=REPO,
                                                       creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        jobs.append((run, job))

    def scene(status):
        return status.get("game", {}).get("native_anomaly_field", {})

    def positions(status):
        return status.get("game", {}).get("native_anomaly_positions", [])

    def placed_traps(status):
        return status.get("game", {}).get("native_placed_anomalies", {})

    def sensors(status):
        return status.get("game", {}).get("native_vortex_sensors", [])

    def vortex_sensor(status):
        return next((sensor for sensor in sensors(status) if math.dist(sensor["root"], traps["vortex"]["location"]) < 3), None)

    def electrode_phase(status, phase, *, rendered=False):
        return next((row for row in status.get("game", {}).get("native_electrode_phases", [])
                     if row["phase"] == phase and math.dist(row["location"], traps["electrode"]["location"]) < 3
                     and (not rendered or row["particles_active"])), None)

    def projected(status, trap):
        return next((row for row in positions(status) if row["kind"] == trap["kind"]
                     and math.dist(row["location"], trap["location"]) < 3), None)

    def jet(status, position, reach):
        return next((row for row in status.get("game", {}).get("native_burner_jets", [])
                     if math.dist(row["location"], position) < reach), None)

    def played_cues(kind):
        log = guest / "events.jsonl"
        if not log.exists():
            return []
        text = log.read_text(encoding="utf-8")
        if kind == "starburst":
            x, y, z = traps[kind]["location"]
            # Stock Starburst cues originate at its sphere, 200 units above the actor root.
            source = [x, y, z + 200]
        elif kind == "vortex":
            source = proof["host_vortex_sensor"]["visual"]
        else:
            source = proof["host_electrode_armed"]["center"]
        return [event for line in text[:text.rfind("\n")].splitlines()
                if (event := json.loads(line)).get("event") == f"native_{kind}_effect_played"
                and math.dist(event["location"], source) < 5]

    try:
        recovery = ("--native-guest-regen",) if placed == "stone-maze" and activate else ()
        await launch(host, "host", "host", "--campaign", name, "--port", "0", "--start-zone", area,
                     "--native-guest", "--native-events", "--native-expanded-zones", "--fixtures", *recovery, *flags)
        initial = await wait(host, lambda row: row.get("state") == "hosting" and scene(row))
        job = await asyncio.create_subprocess_exec(*cli, "invite", "--campaign", name, "--name", "Field guest",
                                                   "--character", "Surveyor", stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE, cwd=REPO,
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(job.communicate(), 30)
        if job.returncode:
            raise RuntimeError(errors.decode(errors="replace"))
        invite = json.loads(output)

        traps = {}
        targets = {}
        authored = {}
        if placed:
            _, kind, name_in_level, expected = PLACED[placed]
            target = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{name_in_level}"
            initial = await wait(host, lambda row: sum(trap["kind"] == kind for trap in placed_traps(row).values()) == expected
                                 and target in placed_traps(row))
            authored = placed_traps(initial)
            traps[kind] = authored[target]
            targets[kind] = target
            assert all(trap["collision"] and trap["role"] == 3 for trap in authored.values()), authored
            proof["placed_host_paths"] = authored
        else:
            # Generate the stock fields before admission to verify hot join receives their placement.
            for kind in ((generated,) if generated else ("starburst", "burner")):
                earlier = {tuple(entry["location"]) for entry in positions(initial) if entry["kind"] == kind}
                result = await submit(host, "native-anomaly-field-fixture", target=kind)
                assert result["ok"], result
                initial = await wait(host, lambda row: any(entry["kind"] == kind and tuple(entry["location"]) not in earlier
                                                         for entry in positions(row)))
                trap, = [entry for entry in positions(initial) if entry["kind"] == kind and tuple(entry["location"]) not in earlier]
                assert trap["collision"], trap
                traps[kind], targets[kind] = trap, kind
        proof["host"] = traps
        placed_sensors = {}
        if "vortex" in traps:
            sensor_frame = await wait(host, vortex_sensor)
            proof["host_vortex_sensor"] = vortex_sensor(sensor_frame)
            placed_sensors = {sensor["path"]: sensor for sensor in sensors(sensor_frame) if sensor["path"]}
            assert placed_sensors.keys() == {path for path, trap in authored.items() if trap["kind"] == "vortex"}

        await launch(guest, "guest", "join", "--profile", name, "--invite", invite["invite_file"], *flags)
        def replicas_visible(row):
            return (row.get("state") == "connected" and all(projected(row, trap) for trap in traps.values())
                    and placed_traps(row).keys() == authored.keys())

        def same_sensor(sensor, expected):
            return (sensor and math.dist(sensor["relative"], expected["relative"]) < .05
                    and math.dist(sensor["center"], expected["center"]) < 3)

        def matching_vortex(row):
            if placed_sensors:
                guest_sensors = {sensor["path"]: sensor for sensor in sensors(row) if sensor["path"]}
                return all(same_sensor(guest_sensors.get(path), expected) for path, expected in placed_sensors.items())
            return same_sensor(vortex_sensor(row), proof["host_vortex_sensor"])

        peer = await wait(guest, replicas_visible)
        if "vortex" in traps:
            peer = await wait(guest, matching_vortex)
            proof["guest_vortex_sensor"] = vortex_sensor(peer)
            proof["matched_vortex_sensors"] = len(placed_sensors) or 1
        replicas = {kind: projected(peer, trap) for kind, trap in traps.items()}
        assert all(not replica["collision"] for replica in replicas.values()), replicas
        proof["guest"] = replicas
        if placed:
            copies = placed_traps(peer)
            for path, original_trap in authored.items():
                copy = copies[path]
                assert copy["kind"] == original_trap["kind"] and not copy["collision"], copy
                assert math.dist(copy["location"], original_trap["location"]) < 3, copy
                assert sum(math.dist(row["location"], copy["location"]) < 3 for row in positions(peer)) == 1, copy
            proof["placed_guest_paths"] = copies
            counter, fixture = "blocked_triggers", "native-placed-anomaly-guest-fixture"
        else:
            counter, fixture = "suppressed", "native-anomaly-field-fixture"
        prior = scene(peer)[counter]
        for target in targets.values():
            result = await submit(guest, fixture, target=target)
            assert result["ok"], result
        expected_vetoes = 3 if placed and "electrode" in traps else 2 if "spinning-ball" in traps else len(traps)
        vetoed = await wait(guest, lambda row: scene(row).get(counter, 0) >= prior + expected_vetoes)
        if "spinning-ball" in traps:
            # Stock field actors in Stone Maze can arrive after the placed trap.
            assert sum(item["kind"] == "spinning-ball" for item in positions(vetoed)) == 1
            assert vetoed["game"]["pawns"]["local"]["health"] == peer["game"]["pawns"]["local"]["health"]
        else:
            assert scene(vetoed)["live"] == scene(peer)["live"], scene(vetoed)
        proof[counter] = scene(vetoed)[counter] - prior
        assert (await submit(guest, "drop-link"))["ok"]
        await wait(guest, lambda row: row.get("reconnect_suspended") and not row.get("link_connected"))
        resumed = await submit(guest, "reconnect", timeout=35)
        assert resumed["ok"] and resumed["resumed"], resumed
        rejoined = await wait(guest, replicas_visible)
        if "vortex" in traps:
            rejoined = await wait(guest, matching_vortex)
        assert all(not projected(rejoined, trap)["collision"] for trap in traps.values()), rejoined
        proof["reconnect"] = True
        if glass_web:
            proof["glass_web"] = await verify_glass_web(host, guest, positions)
        if activate:
            first = await wait(host, lambda row: row.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            health = first["game"]["pawns"]["local"]["health"]
            guest_health = first["game"]["pawns"]["guest"]["health"]
            assert health > 0 and guest_health > 0, first["game"]["pawns"]
            for kind, target in targets.items():
                if kind == "spinning-ball":
                    def rads(frame, seat):
                        game = frame.get("game", {})
                        pawn = game.get("pawns", {}).get(seat, {}).get("name")
                        return game.get("native_guest_vitals", {}).get(pawn, {}).get("rads", -1)

                    baseline = await wait(host, lambda row: rads(row, "guest") >= 0)
                    starting_rads = rads(baseline, "guest")
                    trial = proof["spinning_ball_guest_radiation"] = {"before": starting_rads}
                    enter = await submit(host, "native-anomaly-trigger-fixture", target=f"spinning-enter|{target}")
                    assert enter["ok"], enter
                    radiated = await wait(host, lambda row: rads(row, "guest") > starting_rads, timeout=10)
                    received = await wait(guest, lambda row: rads(row, "local") > starting_rads, timeout=8)
                    trial.update(host_guest_rads=rads(radiated, "guest"), projected_guest_rads=rads(received, "local"))
                    leave = await submit(host, "native-anomaly-trigger-fixture", target=f"spinning-leave|{target}")
                    assert leave["ok"], leave
                    trial["left_stock_overlap"] = True
                    local_tick = await submit(guest, fixture, target=f"spinning-localtick|{target}")
                    assert local_tick["ok"], local_tick
                    trial["guest_tick_vetoed"] = True
                    exited = await wait(host, lambda row: rads(row, "guest") > 0, timeout=5)
                    exit_rads = rads(exited, "guest")
                    trial["exit_rads"] = exit_rads
                    recovered = await wait(host, lambda row: 0 <= rads(row, "guest") < exit_rads, timeout=8)
                    dose = rads(recovered, "guest")
                    trial.update(recovery_rads=dose, damaged_health=recovered["game"]["pawns"]["guest"]["health"])
                    assert trial["damaged_health"] < guest_health, trial
                    cleared = await wait(host, lambda row: rads(row, "guest") == 0, timeout=22)
                    final_health = cleared["game"]["pawns"]["guest"]["health"]
                    synced = await wait(guest, lambda row: rads(row, "local") == 0 and math.isclose(
                        row["game"]["pawns"]["local"]["health"], final_health, abs_tol=.15), timeout=10)
                    trial.update(cleared_rads=rads(synced, "local"), cleared_health=final_health)
                    continue
                if kind == "electrode":
                    armed = await wait(host, lambda row: electrode_phase(row, 1))
                    proof["host_electrode_armed"] = electrode_phase(armed, 1)
                    shown = await wait(guest, lambda row: electrode_phase(row, 1, rendered=True))
                    proof["guest_electrode_armed"] = electrode_phase(shown, 1, rendered=True)
                triggered = await submit(host, "native-anomaly-trigger-fixture", target=target)
                assert triggered["ok"], triggered
                if kind == "starburst":
                    await wait(guest, lambda _: {event["cue"] for event in played_cues(kind)} == {"charge", "blast"}, timeout=14)
                    cues = played_cues(kind)
                    assert len(cues) == 2 and all(event["particles"] for event in cues), cues
                    proof["starburst_cues"] = cues
                elif kind == "vortex":
                    await wait(guest, lambda _: played_cues(kind), timeout=14)
                    cue, = played_cues(kind)
                    assert cue["particles"] and cue["animated"], cue
                    proof["vortex_cue"] = cue
                elif kind == "electrode":
                    await wait(guest, lambda _: len(played_cues(kind)) >= 3, timeout=14)
                    cues = played_cues(kind)
                    assert len(cues) == 3 and all(event["particles"] for event in cues), cues
                    proof["electrode_cues"] = cues
                else:
                    burning = await wait(host, lambda row: jet(row, traps[kind]["location"], 250), timeout=16)
                    host_jet = jet(burning, traps[kind]["location"], 250)
                    viewed = await wait(guest, lambda row: (jet(row, host_jet["location"], 3) or {}).get("particles_active"), timeout=10)
                    guest_jet = jet(viewed, host_jet["location"], 3)
                    assert host_jet["collision"] and not guest_jet["collision"] and guest_jet["particles_active"], (host_jet, guest_jet)
                    await wait(guest, lambda row: row.get("state") == "connected" and not jet(row, host_jet["location"], 3), timeout=15)
                    await wait(host, lambda row: not jet(row, host_jet["location"], 3), timeout=8)
                    proof["burner_jet"] = {"host": host_jet, "guest": guest_jet, "expired_on_both": True}
                struck = await wait(host, lambda row: row.get("game", {}).get("player_effects", {}).get("guest", {}).get("state") == "downed"
                                    or (row.get("game", {}).get("pawns", {}).get("guest", {}).get("health") or guest_health) < guest_health,
                                    timeout=8)
                assert struck["game"]["pawns"]["local"]["health"] == health and not struck["gameplay"]["wipe"], struck["game"]["pawns"]
                proof[f"{kind}_guest_effect"] = {
                    "state": struck["game"]["player_effects"].get("guest", {}).get("state"),
                    "health_before": guest_health,
                    "health_after": struck["game"]["pawns"]["guest"]["health"],
                }
                if placed:
                    nearby = await wait(guest, replicas_visible)
                    copy = placed_traps(nearby)[target]
                    assert not copy["collision"] and math.dist(copy["location"], traps[kind]["location"]) < 3, copy
                    assert sum(math.dist(row["location"], copy["location"]) < 3 for row in positions(nearby)) == 1, copy
                    proof["placed_active_guest"] = copy
                if kind == "electrode":
                    await wait(guest, lambda row: electrode_phase(row, 1, rendered=True), timeout=16)
                    proof["electrode_rearmed"] = True
                    assert (await submit(host, "native-electrode-retire-fixture", target=target))["ok"]
                    await wait(host, lambda row: not projected(row, traps[kind]))
                    await wait(guest, lambda row: not projected(row, traps[kind]))
                    if placed:
                        await wait(host, lambda row: electrode_phase(row, 2))
                        assert (await submit(guest, "drop-link"))["ok"]
                        await wait(guest, lambda row: row.get("reconnect_suspended") and not row.get("link_connected"))
                        assert (await submit(guest, "reconnect", timeout=35))["resumed"]
                        await wait(guest, lambda row: row.get("state") == "connected"
                                   and placed_traps(row).keys() == authored.keys() - {target})
                    proof["electrode_retired"] = True
            after = await wait(host, lambda row: row.get("game", {}).get("pawns", {}).get("local", {}).get("health") is not None)
            assert after["game"]["pawns"]["local"]["health"] == health, after["game"]["pawns"]
            proof["host_health_preserved"] = health
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
    parser.add_argument("--activate", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--placed", choices=PLACED)
    selection.add_argument("--glass-web", action="store_true", help="verify the Funnel's stock Glass Webs and Vortices")
    selection.add_argument("--generated-vortex", action="store_true")
    selection.add_argument("--generated-electrode", action="store_true")
    args = parser.parse_args()
    generated = "vortex" if args.generated_vortex else "electrode" if args.generated_electrode else None
    placed = "funnel" if args.glass_web else args.placed
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved.resolve(), args.activate, placed, generated, args.glass_web))
