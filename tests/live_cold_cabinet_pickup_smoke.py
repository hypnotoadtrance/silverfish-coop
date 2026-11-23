"""Profile the first stock pickup of a committed Observatory cabinet roll."""

import argparse
import asyncio
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
import time
import uuid

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.silverfish import native_host_save, native_loot, native_world_loot, runtime
from tools.silverfish.authored_loot import full_name
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY


PRIVATE_RUNTIME = ROOT.parent / "Runtime"
CABINET = full_name(OBSERVATORY, "LootCabnet_Small_BP_C_3")
SEED = 441206
PROFILE_MODULE = "cabinet_pickup_profile.lua"
PROBES = {
    "native_loot_actions.lua": {
        "pcall(loot.claim_context, state, phase, pawn, crate, point)": (
            "pcall(cabinet_probe.timed, 'claim-context', loot.claim_context, state, phase, pawn, crate, point)"
        ),
        "pool = valid_object(api) and api:LoadGameFromSlot(slot, 0)": (
            "pool = valid_object(api) and cabinet_probe.timed('escrow-load', api.LoadGameFromSlot, api, slot, 0)"
        ),
        "local ok, status, amount, before, after, inventory_guid = SilverfishClaimNativeLootEscrow(": (
            "local ok, status, amount, before, after, inventory_guid = "
            "cabinet_probe.timed('claim-native', SilverfishClaimNativeLootEscrow, "
        ),
    },
    "main.lua": {
        "pcall(run_command, fields, state, current_phase)": (
            "pcall(require('cabinet_pickup_profile').timed, 'command:' .. fields[2], run_command, fields, state, current_phase)"
        ),
        "pcall(tick, fields, pending)": (
            "pcall(require('cabinet_pickup_profile').timed, 'bridge:' .. (fields and fields[2] or 'idle'), tick, fields, pending)"
        ),
    },
}


def source_with_probe(name: str, before: bytes) -> bytes:
    text = before.decode("utf-8")
    for old, new in PROBES[name].items():
        if text.count(old) != 1:
            raise ValueError(f"The cabinet timing anchor has changed in {name}: {old}")
        text = text.replace(old, new)
    # main.lua already uses the Lua chunk's local-variable budget.
    prefix = "" if name == "main.lua" else "local cabinet_probe = require('cabinet_pickup_profile')\n"
    return (prefix + text).encode("utf-8")


@contextmanager
def instrument(work: Path):
    """Temporarily add probes to the exact staged scripts, leaving the native binary alone."""
    stage = work / "stage"
    scripts = stage / runtime.SHIPPING.parent / runtime.MOD_SCRIPTS
    manifest = runtime.stage_manifest(stage)
    helper = scripts / PROFILE_MODULE
    paths = [*(scripts / name for name in PROBES), manifest]
    if stage.resolve(strict=True) != stage or not work.is_relative_to(PRIVATE_RUNTIME.resolve(strict=True)):
        raise ValueError("Use a private diagnostic stage in the project Runtime directory")
    for path in paths:
        if path.resolve(strict=True) != path or not path.is_file() or path.stat().st_nlink != 1 or not path.is_relative_to(stage):
            raise ValueError(f"The profiling overlay requires an unshared file under the stage: {path}")
    if os.path.lexists(helper):
        raise ValueError("The stage already has a cabinet timing overlay")
    runtime.verify_installed_lua(work)
    originals = {path: path.read_bytes() for path in paths}
    updates = {path: source_with_probe(path.name, originals[path]) for path in paths[:-1]}
    updates[helper] = (Path(__file__).with_name("fixtures") / PROFILE_MODULE).read_bytes()
    metadata = json.loads(originals[manifest])
    if "lua_files" in metadata:
        metadata["lua_files"].update({path.name: sha256(data.replace(b"\r\n", b"\n")).hexdigest()
                                      for path, data in updates.items()})
        updates[manifest] = (json.dumps(metadata, indent=2) + "\n").encode()
    try:
        for path, data in updates.items():
            path.write_bytes(data)
        runtime.verify_installed_lua(work)
        yield
    finally:
        for path, data in originals.items():
            if path in updates:
                path.write_bytes(data)
        helper.unlink(missing_ok=True)
        runtime.verify_installed_lua(work)


def summarize(trace: Path) -> dict:
    entries = []
    for line in trace.read_text(encoding="utf-8").splitlines():
        label, *measurements = line.split("\t")
        start, stop, elapsed = map(float, measurements)
        if not label or not all(math.isfinite(value) for value in (start, stop, elapsed)) or stop < start or elapsed < 0:
            raise ValueError("Invalid cabinet timing row")
        entries.append(dict(label=label, start=start, stop=stop, elapsed_ms=elapsed))
    frames = [row for row in entries if row["label"] == "frame"]
    native = [row for row in entries if row["label"] == "claim-native"]
    loads = [row for row in entries if row["label"] == "escrow-load"]
    if len(native) != 1 or len(loads) != 1:
        raise ValueError(f"Expected one cold escrow load and one native claim, saw {len(loads)} and {len(native)}")
    claim, = [row for row in entries if row["label"] == "command:native-loot-claim"]
    enclosing, = [row for row in entries if row["label"] == "bridge:native-loot-claim"]
    if not all(enclosing["start"] <= row["start"] <= row["stop"] <= enclosing["stop"] for row in (*loads, *native, claim)):
        raise ValueError("The cold escrow and claim must occur in the same bridge command")
    nearby = [row["elapsed_ms"] for row in frames
              if row["start"] < enclosing["stop"] + .12 and row["stop"] > enclosing["start"] - .12]
    idle = [row["elapsed_ms"] for row in frames if row["stop"] < enclosing["start"] - .2]
    if not nearby or not idle:
        raise ValueError("Missing baseline or claim frame samples")
    return dict(escrow_load_ms=loads[0]["elapsed_ms"], native_claim_ms=native[0]["elapsed_ms"],
                command_ms=claim["elapsed_ms"], bridge_tick_ms=enclosing["elapsed_ms"],
                nearby_frames_ms=nearby, max_nearby_frame_ms=max(nearby), idle_frame_median_ms=median(idle),
                frame_count=len(frames))


def checkpoint(campaign: Path) -> dict:
    path = campaign / "save/campaign.json"
    if path.resolve(strict=True) != path:
        raise ValueError("The cabinet checkpoint must be a regular file in its private campaign")
    return runtime.read_json(path)


def durable_cabinet(campaign: Path, seed: int):
    saved = checkpoint(campaign)
    world = saved["world"]
    if (world["map"] != OBSERVATORY
            or world.get("native_observation", {}).get("native_scene", {}).get("instance_seed") != seed):
        raise ValueError(f"The copied checkpoint must be on Observatory with seed {seed}")
    roll = world["party"].get(native_loot.STATE_KEY, {}).get(CABINET, {})
    escrow = roll.get("escrow")
    available = [item for item in roll.get("items", ()) if not item.get("claimed_by")
                 and item["class_path"] not in native_loot.CASH_RANGES]
    if not escrow or escrow.get("pending") is True or not available:
        raise ValueError(f"A committed, opened {CABINET} must still contain a physical item")
    profile = campaign / "game-user"
    native_host_save.verify_checkpoint(profile, saved)
    native_host_save.verify_record(native_host_save.checkpoint_profile(profile, saved), escrow)
    return saved, roll, available[0]


def copy_checkpoint(source: Path, destination: Path, seed: int):
    saved, roll, item = durable_cabinet(source, seed)
    before = campaign_save_hashes(source)
    src_profile = native_host_save.checkpoint_profile(source / "game-user", saved)
    dst_profile = native_host_save.checkpoint_profile(destination / "game-user", saved)
    for slot in {row["slot"] for row in native_host_save.references(saved)}:
        old = native_host_save.slot_path(src_profile, slot).resolve(strict=True)
        new = native_host_save.slot_path(dst_profile, slot)
        if not old.is_relative_to(source) or not new.is_relative_to(destination):
            raise ValueError("A copied save generation points outside its private campaign")
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old, new)
    (destination / "save").mkdir()
    shutil.copyfile(source / "save/campaign.json", destination / "save/campaign.json")
    assert durable_cabinet(destination, seed) == (saved, roll, item)
    assert campaign_save_hashes(source) == before
    return before, saved, roll, item


async def exercise(work: Path, canary: Path, seed: int, source: Path | None):
    root = work / "campaigns"
    run_id = uuid.uuid4().hex[:8]
    prepare = root / f"cold-cabinet-source-{run_id}"
    resumed = root / f"cold-cabinet-resume-{run_id}"
    run = resumed / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    original = saved_hashes(canary)
    assert original
    proof = {"cabinet": CABINET, "seed": seed, "verified": False}
    processes = []
    base = [sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work), "host", "--port", "0",
            "--start-zone", OBSERVATORY, "--headless", "--fixtures", "--native-events", "--native-guest",
            "--native-zones", "--native-expanded-zones", "--native-start-preset", "wanderer",
            "--native-world-seed", str(seed)]

    async def start(campaign):
        control = campaign / "run"
        control.mkdir(parents=True, exist_ok=True)
        log = control / "cabinet-worker.log"
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(*base, "--campaign", campaign.name, cwd=ROOT,
                env=dict(os.environ, SILVERFISH_COOP_RENDER_CAPTURE="host"),
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((control, process))

        def active(state):
            if process.returncode is not None:
                raise RuntimeError(log.read_text(encoding="utf-8", errors="replace"))
            return state.get("state") == "hosting" and state.get("game", {}).get("map") == OBSERVATORY

        started = await wait(control, active, timeout=60)
        assert not started.get("link_connected")
        return process

    async def local(control, action, **arguments):
        result = await submit(control, action, **arguments)
        assert result["ok"], result
        return result

    async def stop(control, process):
        result = None
        if process.returncode is None:
            result = await local(control, "stop")
            await asyncio.wait_for(process.wait(), 20)
        assert process.returncode == 0
        return result["revision"] if result is not None else None

    try:
        if source is None:
            process = await start(prepare)
            control = prepare / "run"
            before_open = campaign_save_hashes(prepare)
            initial_revision = checkpoint(prepare)["revision"]
            await local(control, "native-loot-approach", target=f"host@{CABINET}")
            await local(control, "native-loot-crate-fixture", target=CABINET)
            await wait(control, lambda state: CABINET in state.get("gameplay", {}).get(native_loot.STATE_KEY, {}))
            assert campaign_save_hashes(prepare) == before_open, "The stock open wrote a save before quitting"
            # The standalone host's normal save-and-stop request uses checkpoint_native.
            revision = await stop(control, process)
            committed, _, _ = durable_cabinet(prepare, seed)
            assert revision == committed["revision"] and revision > initial_revision
            source = prepare
        before_source, saved, roll, item = copy_checkpoint(source, resumed, seed)
        await start(resumed)
        assert checkpoint(resumed)["revision"] == saved["revision"]
        identity = native_world_loot.crate_identity(roll["id"], item["guid"])
        await wait(run, lambda state: identity in state.get("game", {}).get(native_world_loot.KEY, {}))
        await local(run, native_world_loot.APPROACH, target=f"host@{identity}")
        before_pickup = campaign_save_hashes(resumed)
        mailbox, = (run / "game-bridge").iterdir()
        nonce = uuid.uuid4().hex[:16]
        (mailbox / "measure-cabinet-cold").write_text(nonce + "\n", encoding="ascii")
        await asyncio.sleep(.75)
        started_at = time.perf_counter()
        await local(run, native_world_loot.FIXTURE, target=identity)

        def claimed(state):
            items = state.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(CABINET, {}).get("items", ())
            return identity not in state.get("game", {}).get(native_world_loot.KEY, {}) and any(
                row["guid"] == item["guid"] and row.get("claimed_by") == "host" for row in items)

        await wait(run, claimed)
        proof.update(source_campaign=str(source), revision=saved["revision"], escrow_slot=roll["escrow"]["slot"],
                     class_path=item["class_path"], guid=item["guid"],
                     fixture_to_settled_ms=1000 * (time.perf_counter() - started_at))
        trace = mailbox / f"cabinet-cold-{nonce}.tsv"
        async with asyncio.timeout(15):
            while not trace.is_file() or not trace.read_bytes().endswith(b"\n"):
                await asyncio.sleep(.10)
        proof["timings"] = summarize(trace)
        proof["unchanged_after_pickup"] = campaign_save_hashes(resumed) == before_pickup
        proof["source_preserved"] = campaign_save_hashes(source) == before_source
        assert proof["unchanged_after_pickup"] and proof["source_preserved"]
        proof["verified"] = True
    finally:
        for control, process in reversed(processes):
            if process.returncode is None:
                try:
                    await stop(control, process)
                except (AssertionError, OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 15)
        proof["original_saves_verified"] = saved_hashes(canary) == original
        (run / "cabinet-cold-profile.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Private Runtime workspace with an unmodified diagnostic stage")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="Private stock save canary")
    parser.add_argument("--checkpoint", type=Path, help="Closed, private campaign with the durable unclaimed cabinet roll")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    work, canary = (path.resolve(strict=True) for path in (args.work, args.vanilla_saved))
    source = args.checkpoint.resolve(strict=True) if args.checkpoint else None
    allowed = PRIVATE_RUNTIME.resolve(strict=True)
    if (not work.is_relative_to(allowed) or not canary.is_relative_to(allowed) or canary.is_relative_to(work)
            or source is not None and (not source.is_relative_to(allowed) or source.parent.name != "campaigns")):
        parser.error("Use only the project's private Runtime stage, canary, and campaign directories")
    with instrument(work):
        asyncio.run(exercise(work, canary, args.seed, source))
