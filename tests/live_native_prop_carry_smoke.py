"""Verify guest Interact, carry and stock throw of a real crate junk prop."""

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
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
TYPEWRITER = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Prop_TypeWriter_C_3"
TRACE_COLUMNS = ("seconds", "turn_fraction", "body_x", "body_y", "body_z", "body_pitch", "body_yaw", "body_roll",
                 "velocity_x", "velocity_y", "velocity_z", "target_x", "target_y", "target_z",
                 "target_pitch", "target_yaw", "target_roll")
LANDING_COLUMNS = ("seconds_from_stock_release", "x_cm", "y_cm", "z_cm", "pitch_deg", "yaw_deg", "roll_deg",
                   "vx_cm_per_s", "vy_cm_per_s", "vz_cm_per_s", "wx_deg_per_s", "wy_deg_per_s", "wz_deg_per_s",
                   "awake", "held")
FIXED_THROW = [48.0, 34.5585]
MATCHED_TURN = -70
FIXED_DROP_VIEW = [-30.0, -35.4415]
SETTLED_DISTANCE_CM = 10
HOLD_COLUMNS = ("body_x_cm", "body_y_cm", "body_z_cm", "body_pitch_deg", "body_yaw_deg", "body_roll_deg",
                "target_x_cm", "target_y_cm", "target_z_cm", "target_pitch_deg", "target_yaw_deg", "target_roll_deg",
                "body_speed_cm_per_s", "body_spin_deg_per_s")


@contextmanager
def staged_typewriter_probe(work, name, fixture):
    mods = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods"
    if mods.resolve(strict=True) != mods or "steamapps" in {part.casefold() for part in mods.parts}:
        raise ValueError("Use a private staged mod directory for the grip trace")
    mod = mods / name
    scripts = mod / "Scripts"
    source, activation = scripts / "main.lua", mod / "enabled.txt"
    mod.mkdir()
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / fixture, source)
        activation.touch()
        yield
    finally:
        if mod.resolve() != mod or scripts.resolve() != scripts:
            raise RuntimeError("The private grip probe path changed during the test")
        activation.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        mod.rmdir()


def grip_trace_metrics(samples):
    def target_offset(sample):
        x, y, z = (body - target for body, target in zip(sample[2:5], sample[11:14], strict=True))
        yaw = math.radians(sample[15])
        sine, cosine = math.sin(yaw), math.cos(yaw)
        return cosine * x + sine * y, -sine * x + cosine * y, z

    stationary = [sample for sample in samples if sample[1] == 0]
    turning = [sample for sample in samples if sample[1] > 0]
    if min(len(stationary), len(turning)) < 8:
        raise ValueError("The grip trace did not record both motion phases")
    rest_offset = tuple(map(median, zip(*(target_offset(row) for row in stationary[-8:]), strict=True)))
    metrics = {}
    for name, rows in (("stationary", stationary), ("turning", turning)):
        steps = []
        turns = []
        intervals = []
        for first, second in zip(rows, rows[1:]):
            interval = second[0] - first[0]
            intervals.append(interval)
            steps.append(math.dist(first[2:5], second[2:5]))
            turns.append(max(abs((end - start + 180) % 360 - 180)
                             for start, end in zip(first[5:8], second[5:8], strict=True)))
        metrics[name] = {
            "samples": len(rows), "max_sample_interval_ms": max(intervals) * 1000,
            "max_position_step_cm": max(steps), "max_rotation_axis_step_deg": max(turns),
            "max_position_step_cm_per_s": max(step / dt for step, dt in zip(steps, intervals, strict=True)),
            "max_rotation_axis_step_deg_per_s": max(turn / dt for turn, dt in zip(turns, intervals, strict=True)),
            "max_target_offset_drift_cm": max(math.dist(target_offset(row), rest_offset) for row in rows),
        }
    return metrics


async def collect_grip_trace(run):
    mailbox, = (run / "game-bridge").iterdir()
    reply = mailbox / "prop-grip-trace-reply.tsv"
    pending = mailbox / "prop-grip-trace-request.pending"
    pending.write_text("start\n", encoding="ascii", newline="")
    pending.replace(mailbox / "prop-grip-trace-request")
    async with asyncio.timeout(8):
        while True:
            try:
                text = reply.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                text = ""
            lines = text.splitlines()
            if text.endswith("\n") and lines:
                header = lines[0].split("\t")
                if header[0] == "error":
                    raise RuntimeError(f"The stock grip trace failed: {header[1:]}")
                if len(header) != 4 or header[:2] != ["ok", TYPEWRITER] or header[3] not in {"70", "-70"}:
                    raise ValueError(f"Unexpected stock grip trace header: {header}")
                count, turn_degrees = map(int, header[2:])
                if not 16 <= count <= 96:
                    raise ValueError(f"The stock grip trace reported {count} samples")
                if len(lines) == count + 1:
                    break
            await asyncio.sleep(.015)
    samples = [tuple(map(float, row.split("\t"))) for row in lines[1:]]
    if (any(len(row) != len(TRACE_COLUMNS) or not all(math.isfinite(value) for value in row)
            or not 0 <= row[1] <= 1 for row in samples)
            or any(second[0] <= first[0] or second[1] < first[1] for first, second in zip(samples, samples[1:]))
            or samples[0][1] != 0 or samples[-1][1] != 1):
        raise ValueError("The stock grip trace has invalid or unordered samples")
    return {"actor": TYPEWRITER, "turn_degrees": turn_degrees, "columns": TRACE_COLUMNS,
            "metrics": grip_trace_metrics(samples), "samples": samples}


def probe_mailbox(run):
    mailbox, = (run / "game-bridge").iterdir()
    return mailbox


async def landing_command(run, operation):
    mailbox = probe_mailbox(run)
    reply = mailbox / "typewriter-landing-reply.tsv"
    pending = mailbox / "typewriter-landing-command.pending"
    nonce = uuid.uuid4().hex
    pending.write_text(f"{nonce}\t{operation}\n", encoding="ascii", newline="")
    async with asyncio.timeout(4):
        while True:
            try:
                pending.replace(mailbox / "typewriter-landing-command.tsv")
                break
            except PermissionError:
                await asyncio.sleep(.02)
        while True:
            try:
                raw = reply.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                raw = ""
            if raw.endswith("\n"):
                ticket, status, *body = raw.rstrip("\r\n").split("\t")
                if ticket == nonce:
                    if status != "ok":
                        raise RuntimeError(f"Matched Typewriter {operation}: {' '.join(body)}")
                    return [float(value) for value in body if value]
            await asyncio.sleep(.02)


def rotations_close(first, second, degrees):
    return all(abs((left - right + 180) % 360 - 180) <= degrees for left, right in zip(first, second, strict=True))


def matched_start_close(first, second, distance, degrees):
    return (math.dist(first[:3], second[:3]) <= distance
            and math.dist(first[3:6], second[3:6]) <= distance
            and rotations_close(first[6:9], second[6:9], degrees))


def matched_drop_start_close(first, second, distance, degrees):
    return (math.dist(first[:3], second[:3]) <= distance and math.dist(first[3:5], second[3:5]) <= distance
            and abs(first[5] - second[5]) <= SETTLED_DISTANCE_CM
            and rotations_close(first[6:9], second[6:9], degrees))


def matched_drop_support(samples, staged_floor):
    return (all(len(sample) == 14 and sample[13] >= .99 and abs(sample[10] - staged_floor) <= 1
                and -2 <= sample[5] - sample[10] <= SETTLED_DISTANCE_CM for sample in samples)
            and math.dist(samples[0][11:14], samples[1][11:14]) <= .02)


async def wait_matched_start(host, guest, staged, *, drop=False):
    if len(staged) != 10 or not all(math.isfinite(value) for value in staged):
        raise RuntimeError(f"Matched Typewriter stage did not report the fixed stock origins and floor: {staged}")
    previous, samples = None, []
    try:
        async with asyncio.timeout(8):
            while True:
                operation = "readydrop" if drop else "ready"
                samples = await asyncio.gather(landing_command(host, operation), landing_command(guest, operation))
                placed = all(
                    len(sample) == (14 if drop else 10) and all(math.isfinite(value) for value in sample)
                    and math.dist(sample[:2], staged[:2]) <= 2 and abs(sample[2] - staged[2]) <= 12
                    and math.dist(sample[3:5], staged[3:5]) <= 8 and staged[9] - 2 <= sample[5] <= staged[5] - 4
                    and 0 <= sample[9] <= 3
                    for sample in samples
                )
                if drop:
                    placed = (placed and matched_drop_support(samples, staged[9])
                              and matched_drop_start_close(*samples, distance=3, degrees=5))
                else:
                    placed = placed and matched_start_close(*samples, distance=3, degrees=5)
                if placed:
                    if previous is not None and all(
                        matched_start_close(before, after, distance=1, degrees=3)
                        for before, after in zip(previous, samples, strict=True)
                    ):
                        result = dict(staged_guest_xyz=staged[:3], staged_prop_xyz=staged[3:6], staged_host_xyz=staged[6:9],
                                      floor_z=staged[9], observed_seats=dict(zip(("host", "guest"), (s[:10] for s in samples))))
                        if drop:
                            result["observed_support_z_and_normal"] = dict(zip(("host", "guest"), (s[10:] for s in samples)))
                        return result
                    previous = samples
                else:
                    previous = None
                await asyncio.sleep(.10)
    except TimeoutError as exc:
        raise RuntimeError("Matched Typewriter did not settle in the same stock starting position on both seats. "
                           f"Stage {staged}; authority and guest samples {samples}") from exc


def matched_hold_close(first, second, distance, degrees):
    return (math.dist(first[:3], second[:3]) <= distance
            and math.dist(first[6:9], second[6:9]) <= distance
            and rotations_close(first[3:6], second[3:6], degrees)
            and rotations_close(first[9:12], second[9:12], degrees))


def stationary_hold(sample):
    return (len(sample) == len(HOLD_COLUMNS) and all(math.isfinite(value) for value in sample)
            and 0 <= sample[12] <= 3 and 0 <= sample[13] <= 6
            and math.dist(sample[:3], sample[6:9]) <= 65)


async def wait_stationary_hold(host, guest):
    samples, previous = [], None
    settled = 0
    try:
        async with asyncio.timeout(8):
            while True:
                samples = await asyncio.gather(landing_command(host, "hold"), landing_command(guest, "hold"))
                if all(map(stationary_hold, samples)) and matched_hold_close(*samples, distance=5, degrees=8):
                    still = previous is not None and all(
                        matched_hold_close(before, after, distance=1, degrees=2)
                        for before, after in zip(previous, samples, strict=True)
                    )
                    settled = settled + 1 if still else 0
                    if settled >= 2:
                        return dict(zip(("host", "guest"), samples, strict=True))
                    previous = samples
                else:
                    settled, previous = 0, None
                await asyncio.sleep(.10)
    except TimeoutError as exc:
        raise RuntimeError(f"Matched Typewriter did not reach a shared stationary stock grip: {samples}") from exc


def load_matched_baseline(path, *, drop=False):
    baseline = json.loads(path.read_text(encoding="utf-8"))
    samples = baseline["matched_start"]["observed_seats"]
    edge = "drop" if drop else "throw"
    receipt = baseline[f"{edge}_receipt"]
    if (not baseline["initial"]["actor"].endswith(TYPEWRITER) or "local_grip_filter" in baseline
        or receipt.get("edge") != edge or receipt["release_pose_applied"] is not True
        or set(samples) != {"host", "guest"} or not all(
            len(sample) == 10 and all(math.isfinite(value) for value in sample) for sample in samples.values()
        )):
        raise ValueError(f"expected a successful unfiltered Typewriter {edge} with both starting samples")
    if drop:
        held = baseline["matched_hold"]["observed_seats"]
        if set(held) != {"host", "guest"} or not all(map(stationary_hold, held.values())):
            raise ValueError("expected both stationary held poses from the unfiltered drop")
        floor = baseline["matched_start"]["floor_z"]
        if not math.isfinite(floor) or not all(-2 <= row[5] - floor <= SETTLED_DISTANCE_CM for row in samples.values()):
            raise ValueError("expected the matched baseline Typewriter to be staged at stock floor")
        return {"start": samples, "held": held, "floor_z": floor}
    if baseline["throw_distance_cm"] <= 70:
        raise ValueError("the unfiltered throw did not pass the stock travel gate")
    return {"start": samples}


async def collect_landing(run, seat):
    reply = probe_mailbox(run) / "typewriter-landing-trace.tsv"
    async with asyncio.timeout(14):
        while True:
            try:
                raw = reply.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                raw = ""
            lines = raw.splitlines()
            if raw.endswith("\n") and lines:
                header = lines[0].split("\t")
                if header[0] == "error":
                    raise RuntimeError(f"Matched Typewriter {seat} trace: {' '.join(header[1:])}")
                if len(header) != 4 or header[:3] != ["ok", TYPEWRITER, seat]:
                    raise ValueError(f"Unexpected landing trace header: {header}")
                count = int(header[3])
                if not 20 <= count <= 230:
                    raise ValueError(f"Invalid landing trace count: {count}")
                if len(lines) == count + 2:
                    break
            await asyncio.sleep(.05)
    floor = None if lines[1] == "-" else tuple(map(float, lines[1].split("\t")))
    samples = [tuple(map(float, line.split("\t"))) for line in lines[2:]]
    if (floor is not None and (len(floor) != 6 or not all(math.isfinite(value) for value in floor))
            or any(len(row) != len(LANDING_COLUMNS) or not all(math.isfinite(value) for value in row)
                   or any(value not in (0, 1) for value in row[-2:]) for row in samples)
            or samples[0][0] != 0 or samples[0][-1] != 1 or samples[-1][0] < 8
            or any(second[0] <= first[0] for first, second in zip(samples, samples[1:]))
            or not any(row[-1] == 0 for row in samples)):
        raise ValueError(f"Invalid matched Typewriter {seat} body observations")
    return {"floor_probe_xyz_and_normal": floor, "samples": samples}


def drop_trace_metrics(samples):
    early = [row for row in samples if row[0] <= .5]
    if len(early) < 6 or early[-1][0] < .4 or not any(row[-1] == 0 for row in early):
        raise ValueError("the stock drop trace did not sample the first half-second")
    last_active = max(index for index, row in enumerate(samples) if row[-2] or row[-1])
    if last_active == len(samples) - 1:
        raise ValueError("the stock body did not settle asleep after the drop")
    return {
        "first_half_second_samples": len(early),
        "first_unheld_seconds": next(row[0] for row in early if row[-1] == 0),
        "max_horizontal_displacement_cm": max(math.dist(row[1:3], early[0][1:3]) for row in early),
        "max_horizontal_step_cm": max(math.dist(first[1:3], second[1:3]) for first, second in zip(early, early[1:])),
        "fall_from_released_center_cm": early[0][3] - min(row[3] for row in early),
        "peak_downward_speed_cm_per_s": max(-row[9] for row in early),
        "seconds_to_sleep": samples[last_active + 1][0],
    }


async def save_matched_landing(host, guest, staged, turn_degrees, aim, projected_floor, grip_filter, *, drop=None):
    captured = await asyncio.gather(collect_landing(host, "host"), collect_landing(guest, "guest"),
                                    return_exceptions=True)
    seats = {seat: ({"error": repr(result)} if isinstance(result, BaseException) else result)
             for seat, result in zip(("host", "guest"), captured, strict=True)}
    outcome = {"actor": TYPEWRITER, "grip_filter": grip_filter, "matched_start": staged, "columns": LANDING_COLUMNS,
               **seats}
    if drop is None:
        outcome.update(fixed_throw_pitch_yaw=FIXED_THROW, observed_throw_pitch_yaw=aim, turn_degrees=turn_degrees,
                       projected_floor_xyz_and_normal=projected_floor)
    else:
        outcome.update(release_action="drop", matched_hold=drop)
    (host / "prop-landing-trace.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    return seats


async def exercise(work, untouched, kind="crate", *, require_release_pose=False, trace_local_grip=False,
                   grip_filter=False, matched_landing=False, matched_drop=False, matched_reference=None):
    trace_tag = "trace-matched-" if matched_landing else "trace-" if trace_local_grip else ""
    token = f"{kind}-carry-{trace_tag}{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", token), ("--profile", token)
    hrun, grun = work / "campaigns" / token / "run", work / "clients" / token / "run"
    hrun.mkdir(parents=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    original = saved_hashes(untouched)
    processes = []
    landing_capture = None
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
        env = None
        if trace_local_grip or matched_drop:
            env = dict(os.environ, SILVERFISH_COOP_PROP_GRIP_FILTER="1" if grip_filter else "0")
        with (hrun / f"{label}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*cli, *argv, cwd=ROOT, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW, env=env)
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
        placed_name = f"Prop_TypeWriter_C {TYPEWRITER}"

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
        staged = None
        if matched_landing:
            staged = await wait_matched_start(hrun, grun, await landing_command(hrun, "stage"), drop=matched_drop)
            print(f"Matched Typewriter start {staged}", flush=True)
            if matched_reference is not None:
                compare_start = matched_drop_start_close if matched_drop else matched_start_close
                if (matched_drop and abs(staged["floor_z"] - matched_reference["floor_z"]) > 1) or not all(
                    compare_start(baseline, staged["observed_seats"][seat], distance=5, degrees=10)
                    for seat, baseline in matched_reference["start"].items()
                ):
                    raise RuntimeError(f"Matched Typewriter start changed between runs: {matched_reference} vs {staged}")
        else:
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
        carry_lease = proof(grabbed)["lease"]

        def filter_state(state):
            counters = state.get("game", {}).get(carry.FILTER, {})
            return counters if counters.get("lease") == carry_lease else {}

        if grip_filter:
            await wait(grun, lambda state: filter_state(state).get("active"))
        def moved_while_held(state):
            held_prop = prop(state)
            return (held_prop.get("held") and held_prop.get("grip")
                    and math.dist(held_prop["grip"], prop(grabbed)["grip"]) > 50
                    and math.dist(held_prop["position"], prop(grabbed)["position"]) > 25)

        async def trace_and_throw():
            nonlocal landing_capture
            samples = await collect_grip_trace(grun)
            try:
                if matched_landing:
                    turn_degrees = samples["turn_degrees"]
                    if turn_degrees != MATCHED_TURN:
                        raise RuntimeError(f"Matched Typewriter used the wrong turn: {turn_degrees}")
                    aimed_throw = await landing_command(grun, "throw")
                    aim, projected_floor = aimed_throw[:2], aimed_throw[2:]
                    if len(aimed_throw) != 8 or not all(math.isfinite(value) for value in aimed_throw) or any(
                        not math.isclose(actual, expected, abs_tol=.05)
                        for actual, expected in zip(aim, FIXED_THROW, strict=True)
                    ):
                        raise RuntimeError(f"Matched Typewriter returned an invalid throw aim or floor: {aimed_throw}")
                    landing_capture = asyncio.create_task(save_matched_landing(
                        hrun, grun, staged, turn_degrees, aim, projected_floor, grip_filter))
                else:
                    await fixture(guest, "throw@up")
            finally:
                (hrun / "prop-grip-trace.json").write_text(json.dumps(samples, indent=2) + "\n", encoding="utf-8")
            return samples

        trace, matched_hold = None, None
        if matched_drop:
            view = await landing_command(grun, "dropaim")
            if len(view) != 2 or not rotations_close(view, FIXED_DROP_VIEW, degrees=.05):
                raise RuntimeError(f"Matched Typewriter changed its fixed drop view: {view}")
            await asyncio.gather(landing_command(hrun, "armdrop"), landing_command(grun, "armdrop"))
            before_filter = None
            if grip_filter:
                state = await wait(grun, lambda state: (report := filter_state(state))
                                   and report["active"] and report["suppressed"] > 0)
                before_filter = filter_state(state)
            held = await wait_stationary_hold(hrun, grun)
            if matched_reference is not None and not all(
                matched_hold_close(baseline, held[seat], distance=5, degrees=8)
                for seat, baseline in matched_reference["held"].items()
            ):
                raise RuntimeError(f"Matched Typewriter held pose changed between runs: {matched_reference['held']} vs {held}")
            dropped_from = await landing_command(grun, "drop")
            matched_hold = dict(columns=HOLD_COLUMNS, fixed_view_pitch_yaw=view, observed_seats=held,
                                stock_drop_readback=dropped_from, filter_while_held=before_filter)
            landing_capture = asyncio.create_task(save_matched_landing(
                hrun, grun, staged, None, None, None, grip_filter, drop=matched_hold))
            if not stationary_hold(dropped_from) or not matched_hold_close(
                dropped_from, held["guest"], distance=2, degrees=3
            ):
                raise RuntimeError(f"Matched Typewriter moved before the stock drop: {held['guest']} vs {dropped_from}")
        elif trace_local_grip:
            if matched_landing:
                await asyncio.gather(landing_command(hrun, "arm"), landing_command(grun, "arm"))
            async with asyncio.TaskGroup() as tasks:
                collecting = tasks.create_task(trace_and_throw())
                observing = tasks.create_task(wait(hrun, moved_while_held))
            trace, moved = collecting.result(), observing.result()
        else:
            await fixture(guest, "turn")
            moved = await wait(hrun, moved_while_held)
        if kind == "lantern":
            await stop(guest_process, guest)
            released = await wait(hrun, lambda state: prop(state).get("actor") == initial["actor"]
                                  and prop(state).get("held") is False and lamp(state) is True)
            effect = dict(released_after_disconnect=prop(released), normal_interact_extinguished=True,
                          stock_fire_relit=True, remained_lit_after_disconnect=True)
        else:
            if matched_drop:
                dropped = await wait(hrun, lambda state: proof(state).get("edge") == "drop"
                                     and proof(state).get("actor") == initial["actor"]
                                     and proof(state).get("lease") == carry_lease and not prop(state).get("held", True))
                assert proof(dropped).get("release_pose_applied") is True, proof(dropped)
                effect = dict(dropped=prop(dropped), drop_receipt=proof(dropped), matched_hold=matched_hold)
            else:
                if trace is None:
                    throw_origin = prop(moved)["position"]
                    throw_mode = "throw@up" if kind in ("placed-prop", "placed-item") else "throw"
                    await fixture(guest, throw_mode)
                else:
                    throw_origin = trace["samples"][-1][2:5]
                thrown = await wait(hrun, lambda state: proof(state).get("edge") == "throw" and not prop(state).get("held", True))
                moved_again = await wait(hrun, lambda state: prop(state).get("actor") == initial["actor"] and
                                         math.dist(prop(state)["position"], throw_origin) > 70)
                effect = dict(thrown=prop(thrown), landed=prop(moved_again), throw_receipt=proof(thrown),
                              throw_origin=throw_origin, throw_distance_cm=math.dist(prop(moved_again)["position"], throw_origin))
                if require_release_pose:
                    assert proof(thrown).get("release_pose_applied") is True, proof(thrown)
            if kind in ("placed-prop", "placed-item"):
                path = placed_name.split(" ", 1)[1]
                previous = None
                settled = 0
                async with asyncio.timeout(8):
                    while True:
                        authority, replica = await asyncio.gather(fixture(host, f"sample@{path}"), fixture(guest, f"sample@{path}"))
                        host_pose, guest_pose = ([float(value) for value in row["sample"].split("|")] for row in (authority, replica))
                        close = math.dist(host_pose[:3], guest_pose[:3]) <= SETTLED_DISTANCE_CM
                        if require_release_pose:
                            still = previous is not None and all(math.dist(now[:3], then[:3]) <= 2 for now, then in
                                                                 zip((host_pose, guest_pose), previous, strict=True))
                            settled = settled + 1 if close and still else 0
                            close = settled >= 2
                            previous = host_pose, guest_pose
                        if close:
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
        if trace is not None:
            effect["local_grip_trace"] = trace["metrics"]
        if grip_filter:
            counters = await wait(grun, lambda state: (report := filter_state(state))
                                  and not report["active"] and report["suppressed"] > 0 and report["passed"] > 0)
            effect["local_grip_filter"] = filter_state(counters)
        if landing_capture is not None:
            landing = await landing_capture
            if any("error" in side for side in landing.values()):
                raise RuntimeError(f"A matched landing trace was incomplete: {landing}")
            if matched_drop:
                for seat, data in landing.items():
                    release = data["samples"][0]
                    if (math.dist(release[1:4], dropped_from[:3]) > .5
                        or not rotations_close(release[4:7], dropped_from[3:6], degrees=1)):
                        raise RuntimeError(f"Matched Typewriter {seat} did not release from the measured stock hold: {release}")
                drops = {seat: drop_trace_metrics(data["samples"]) for seat, data in landing.items()}
                if drops["host"]["fall_from_released_center_cm"] <= 30 or drops["host"]["peak_downward_speed_cm_per_s"] <= 50:
                    raise RuntimeError(f"The stock authority did not fall freely after the drop: {drops['host']}")
                effect["stock_drop_observations"] = drops
        if staged is not None:
            effect["matched_start"] = staged
        (hrun / "prop-carry-verification.json").write_text(json.dumps(dict(
            initial=initial, grabbed=prop(grabbed), grab_receipt=proof(grabbed), canary_files=len(original),
            **({} if matched_drop else {"moved": prop(moved)}), **effect,
        ), indent=2), encoding="utf-8")
    finally:
        try:
            if landing_capture is not None and not landing_capture.done():
                await landing_capture
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
    parser.add_argument("--require-release-pose", action="store_true", help="require the rigid-body handoff and settled agreement")
    parser.add_argument("--trace-local-grip", action="store_true", help="measure the local Typewriter grip during one hold and turn")
    parser.add_argument("--grip-filter", action="store_true", help="measure the experimental host-confirmed grip filter")
    matched = parser.add_mutually_exclusive_group()
    matched.add_argument("--matched-landing", action="store_true", help="sample both stock bodies after a fixed Typewriter throw")
    matched.add_argument("--matched-drop", action="store_true", help="compare one settled stock Typewriter drop on both seats")
    parser.add_argument("--match-baseline", type=Path, help="successful matched baseline prop-carry-verification.json")
    options = parser.parse_args()
    if (options.require_release_pose or options.trace_local_grip) and options.kind != "placed-prop":
        parser.error("--require-release-pose and --trace-local-grip use the placed rigid-prop fixture")
    if options.grip_filter and not (options.require_release_pose and (options.trace_local_grip or options.matched_drop)):
        parser.error("--grip-filter requires --require-release-pose and a grip trace or matched drop")
    if options.matched_landing and not (options.require_release_pose and options.trace_local_grip):
        parser.error("--matched-landing requires --trace-local-grip and --require-release-pose")
    if options.matched_drop and (not options.require_release_pose or options.trace_local_grip):
        parser.error("--matched-drop requires --require-release-pose without the turning grip trace")
    matched_reference = None
    matched_landing = options.matched_landing or options.matched_drop
    if matched_landing and options.grip_filter:
        if options.match_baseline is None:
            parser.error("the filtered matched run requires --match-baseline from the successful stock run")
        try:
            matched_reference = load_matched_baseline(options.match_baseline, drop=options.matched_drop)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            parser.error(f"invalid matched baseline {options.match_baseline}: {exc}")
    elif options.match_baseline is not None:
        parser.error("--match-baseline requires a matched test and --grip-filter")
    work = options.work.resolve()
    with ExitStack() as probes:
        if options.trace_local_grip:
            probes.enter_context(staged_typewriter_probe(work, "SilverfishTypewriterGripProbe", "typewriter_grip_probe.lua"))
        if matched_landing:
            probes.enter_context(staged_typewriter_probe(work, "SilverfishTypewriterLandingProbe", "typewriter_landing_probe.lua"))
        asyncio.run(exercise(work, options.vanilla_saved.resolve(), options.kind,
                             require_release_pose=options.require_release_pose, trace_local_grip=options.trace_local_grip,
                             grip_filter=options.grip_filter, matched_landing=matched_landing, matched_drop=options.matched_drop,
                             matched_reference=matched_reference))
