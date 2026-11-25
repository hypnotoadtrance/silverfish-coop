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
from zipfile import ZipFile

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes
from portable_guest_world_start import resident_events, wait_resident_status

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.silverfish import native_host_save, native_loot, native_world_loot, runtime
from tools.silverfish.authored_loot import GUARANTEED_CRATES, full_name
from tools.silverfish.control import submit
from tools.silverfish.service import menu_host_checkpoint
from tools.silverfish.title_host_campaigns import saved_campaign
from tools.silverfish.travel import OBSERVATORY


PRIVATE_RUNTIME = next((parent / "Runtime" for parent in (ROOT.parent, ROOT.parent.parent)
                        if (parent / "Runtime").is_dir()), ROOT.parent / "Runtime")
CABINET = full_name(OBSERVATORY, "LootCabnet_Small_BP_C_3")
SEED = 441206
PROFILE_MODULE = "cabinet_pickup_profile.lua"
JOINT_PRESENTATION = "native-world-loot-present-host-claim"
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


RESIDENT_PROBES = {
    "native_authored_pickup.lua": {
        "    if #preview.meshes == 0 then previews[target] = nil end": (
            "    if #preview.meshes == 0 then previews[target] = nil end\n"
            "    if binding.host and previews[target] and actor:GetActorEnableCollision() and valid(actor.Box)"
            " and actor.Box:GetCollisionEnabled() ~= 0 and not preview.meshes[1]:IsVisible() then\n"
            "        cabinet_probe.mark('local-mesh-hidden', target:match('^world%-loot:%x+:(%x+):'))\n"
            "    end"
        ),
    },
    "native_guest_drop_projection.lua": {
        "return pickup_fixture.press_world_loot(state, pawn, actor, actor.Box, function()": (
            "return pickup_fixture.press_world_loot(state, pawn, actor, actor.Box, function()\n"
            "            cabinet_probe.mark('stock-f', id)"
        ),
    },
    "players.lua": {
        "and SilverfishEnqueueLocalBridgeInput(record) then": (
            "and SilverfishEnqueueLocalBridgeInput(record) then\n"
            "        cabinet_probe.mark('input-memory', target and target:match('^world%-loot:%x+:(%x+):'))"
        ),
    },
}


CURRENT_MAIN_PROBES = {
    "accepted, message = pcall(run_command, fields, state, current_phase)": (
        "accepted, message = pcall(require('cabinet_pickup_profile').timed, 'command:' .. fields[2], "
        "run_command, fields, state, current_phase)"
    ),
    "pcall(tick, fields, pending, periodic)": (
        "pcall(require('cabinet_pickup_profile').timed, 'bridge:' .. (fields and fields[2] or 'idle'), "
        "tick, fields, pending, periodic)"
    ),
}


def source_with_probe(name: str, before: bytes, *, combined: bool = False) -> bytes:
    text = before.decode("utf-8")
    anchors = PROBES.get(name) or RESIDENT_PROBES[name]
    if name == "main.lua" and "local function tick(fields, pending, periodic)" in text:
        anchors = CURRENT_MAIN_PROBES
    if combined and name == "main.lua":
        publish = "SilverfishPublishLocalBridgeReply(pending.ticket, frame)"
        anchors = {**anchors, publish: publish + "\n            "
                   "require('cabinet_pickup_profile').acknowledge(pending.fields, acknowledged_result, acknowledged_message)"}
        if "pickup-presentation-ack" in text:
            invoke = "pcall(pickup_presentation or native_weapon_fire_view.command,"
            short_reply = 'status_row(tag, fields[1], accepted and "accepted" or "error", message or "") .. "\\n")'
            anchors.update({
                invoke: "pcall(require('cabinet_pickup_profile').timed, 'command:' .. fields[2], "
                        "pickup_presentation or native_weapon_fire_view.command,",
                short_reply: short_reply + "\n        "
                    "require('cabinet_pickup_profile').acknowledge(fields, accepted and 'accepted' or 'error', message or '')"
                    "\n        require('cabinet_pickup_profile').mark('presentation-short', fields[4])",
            })
    for old, new in anchors.items():
        if text.count(old) != 1:
            raise ValueError(f"The cabinet timing anchor has changed in {name}: {old}")
        text = text.replace(old, new)
    # main.lua already uses the Lua chunk's local-variable budget.
    prefix = "local cabinet_probe = require('cabinet_pickup_profile')\n" if "cabinet_probe." in text else ""
    return (prefix + text).encode("utf-8")


@contextmanager
def instrument(work: Path, *, release_archive: Path | None = None, combined: bool = False):
    """Temporarily add probes to private scripts and restore their original bytes."""
    stage = work if release_archive else work / "stage"
    probe_names = PROBES | RESIDENT_PROBES if release_archive else PROBES
    scripts = stage / runtime.SHIPPING.parent / runtime.MOD_SCRIPTS
    manifest = runtime.stage_manifest(stage)
    helper = scripts / PROFILE_MODULE
    paths = [*(scripts / name for name in probe_names), manifest]
    if stage.resolve(strict=True) != stage or not work.is_relative_to(PRIVATE_RUNTIME.resolve(strict=True)):
        raise ValueError("Use a private diagnostic stage in the project Runtime directory")
    for path in paths:
        if path.resolve(strict=True) != path or not path.is_file() or path.stat().st_nlink != 1 or not path.is_relative_to(stage):
            raise ValueError(f"The profiling overlay requires an unshared file under the stage: {path}")
    if os.path.lexists(helper):
        raise ValueError("The stage already has a cabinet timing overlay")

    def verify_lua():
        with runtime.installed_content(work, stage):
            runtime.verify_installed_lua(work)

    verify_lua()
    originals = {path: path.read_bytes() for path in paths}
    if release_archive:
        with ZipFile(release_archive) as release:
            for path, data in originals.items():
                if release.read(path.relative_to(stage).as_posix()) != data:
                    raise ValueError(f"The portable source differs from the pinned archive: {path}")
    updates = {path: source_with_probe(path.name, originals[path], combined=combined) for path in paths[:-1]}
    updates[helper] = (Path(__file__).with_name("fixtures") / PROFILE_MODULE).read_bytes()
    metadata = json.loads(originals[manifest])
    if "lua_files" in metadata:
        metadata["lua_files"].update({path.name: sha256(data.replace(b"\r\n", b"\n")).hexdigest()
                                      for path, data in updates.items()})
        updates[manifest] = (json.dumps(metadata, indent=2) + "\n").encode()
    try:
        for path, data in updates.items():
            path.write_bytes(data)
        verify_lua()
        yield
    finally:
        for path, data in originals.items():
            path.write_bytes(data)
        helper.unlink(missing_ok=True)
        verify_lua()


def summarize(trace: Path, *, resident: bool = False, combined_operation: str | None = None) -> dict:
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
    if len(native) != 1 or len(loads) > 1 or (not resident and len(loads) != 1):
        raise ValueError(f"Unexpected escrow load or native claim count: {len(loads)} loads and {len(native)} claims")
    claim, = [row for row in entries if row["label"] == "command:native-loot-claim"]
    enclosing, = [row for row in entries if row["label"] == "bridge:native-loot-claim"]
    if not all(enclosing["start"] <= row["start"] <= row["stop"] <= enclosing["stop"] for row in (*loads, *native, claim)):
        raise ValueError("The cold escrow and claim must occur in the same bridge command")
    press, = [row for row in entries if row["label"] == f"command:{native_world_loot.FIXTURE}"]
    if press["stop"] > enclosing["start"]:
        raise ValueError("The stock input must precede the claim")
    presentation = {}
    for stage, command in (("retire", native_world_loot.RETIRE), ("sound", "native-sound-pickup")):
        matching = [row for row in entries if row["label"] == f"bridge:{command}" and row["start"] >= enclosing["stop"]]
        if len(matching) == 1:
            presentation[stage] = round((matching[0]["stop"] - press["stop"]) * 1000, 3)
    nearby = [row["elapsed_ms"] for row in frames
              if row["start"] < enclosing["stop"] + .12 and row["stop"] > enclosing["start"] - .12]
    idle = [row["elapsed_ms"] for row in frames if row["stop"] < enclosing["start"] - .2]
    if not nearby or not idle:
        raise ValueError("Missing baseline or claim frame samples")
    summary = dict(escrow_load_ms=loads[0]["elapsed_ms"] if loads else None, native_claim_ms=native[0]["elapsed_ms"],
                   command_ms=claim["elapsed_ms"], bridge_tick_ms=enclosing["elapsed_ms"],
                   nearby_frames_ms=nearby, max_nearby_frame_ms=max(nearby), idle_frame_median_ms=median(idle),
                   frame_count=len(frames))
    if not resident:
        summary.update(input_fixture_end_to_claim_ms=round((claim["stop"] - press["stop"]) * 1000, 3),
                       input_fixture_end_to_presentation_ms=presentation)
    if resident:
        stock, = [row for row in entries if row["label"] == "stock-f"]
        queued, = [row for row in entries if row["label"] == "input-memory"]
        def presentation_command(name):
            tick, = [row for row in entries if row["label"] == "bridge:" + name]
            command, = [row for row in entries if row["label"] == "command:" + name]
            if not tick["start"] <= command["start"] <= command["stop"] <= tick["stop"]:
                raise ValueError("A presentation command escaped its bridge tick")
            return tick, command

        retire_name, sound_name = "native-world-loot-retire", "native-sound-pickup"
        if combined_operation is None:
            retire, retire_command = presentation_command(retire_name)
            sound, sound_command = presentation_command(sound_name)
            presentation_edges = (retire["start"], retire["stop"], sound["start"])
        else:
            joint, joint_command = presentation_command(JOINT_PRESENTATION)
            acknowledgments = [row for row in entries if row["label"].startswith("presentation-ack:")]
            wanted = "presentation-ack:accepted:retired|" + combined_operation
            if len(acknowledgments) != 1 or acknowledgments[0]["label"] != wanted:
                raise ValueError(f"The joint pickup ACK did not prove both effects: {acknowledgments}")
            ack = acknowledgments[0]
            if (not joint_command["stop"] <= ack["start"] <= joint["stop"]
                    or any(row["label"] in {"bridge:" + retire_name, "bridge:" + sound_name} for row in entries)):
                raise ValueError("The joint pickup ACK was outside its command or used separate fallback commands")
            retire = sound = joint
            presentation_edges = (joint["start"], joint_command["stop"], ack["start"])
        edges = (stock["start"], queued["start"], enclosing["start"], enclosing["stop"], *presentation_edges)
        if tuple(sorted(edges)) != edges:
            raise ValueError("The stock press, in-process input, claim, retirement and sound are out of order")

        def ms(stop, start):
            return round((stop - start) * 1000, 3)

        presentation = [row["elapsed_ms"] for row in frames
                        if row["start"] < sound["stop"] + .06 and row["stop"] > stock["start"] - .06]
        if not presentation:
            raise ValueError("Missing frame samples around the measured presentation")
        previews = [row for row in entries if row["label"] == "local-mesh-hidden"]
        if previews:
            preview, = previews
            if not stock["start"] <= preview["start"] <= enclosing["start"]:
                raise ValueError("The local pickup preview did not precede the inventory transfer")
            summary["stock_f_to_preview_ms"] = ms(preview["start"], stock["start"])
        summary.update(stock_f_to_input_ms=ms(queued["start"], stock["start"]),
                       input_to_claim_ms=ms(enclosing["start"], queued["start"]),
                       input_to_claim_command_end_ms=ms(claim["stop"], queued["start"]),
                       stock_f_to_claim_command_end_ms=ms(claim["stop"], stock["start"]),
                       stock_f_to_claim_tick_end_ms=ms(enclosing["stop"], stock["start"]),
                       claim_to_retire_ms=ms(retire["start"], enclosing["stop"]),
                       max_presentation_frame_ms=max(presentation), stock_f_to_sound_ms=ms(sound["stop"], stock["start"]))
        if combined_operation is None:
            summary.update(retire_to_sound_ms=ms(sound["start"], retire["stop"]),
                           retire_command_ms=retire_command["elapsed_ms"], retire_tick_ms=retire["elapsed_ms"],
                           sound_command_ms=sound_command["elapsed_ms"], sound_tick_ms=sound["elapsed_ms"])
        else:
            summary.update(joint_command_ms=joint_command["elapsed_ms"], joint_tick_ms=joint["elapsed_ms"],
                           stock_f_to_joint_ack_ms=ms(ack["start"], stock["start"]),
                           joint_ack=ack["label"].removeprefix("presentation-ack:"),
                           joint_short_receipt=any(row["label"] == "presentation-short" for row in entries))
    return summary


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


def copy_resident_checkpoint(source: Path, campaign: Path, profile: Path):
    """Copy only the verified cabinet generation into this test title's private save namespace."""
    private = PRIVATE_RUNTIME.resolve(strict=True)
    if (not all(path.is_relative_to(private) for path in (source, campaign, profile))
            or source.is_relative_to(campaign) or campaign.is_relative_to(source)
            or source.is_relative_to(profile) or profile.is_relative_to(source)
            or any(path.resolve() != path for path in (source, campaign, profile))
            or source.parent.name != "campaigns" or campaign.parent.name != "campaigns"):
        raise ValueError("Use separate private source and resident campaign locations")
    before = campaign_save_hashes(source)
    saved, roll, item = durable_cabinet(source, SEED)
    if saved["characters"] or saved.get("leases") or native_host_save.SCOPE_FIELD in saved["world"]:
        raise ValueError("The timing source must be the standalone campaign with no guest saves")
    scope = uuid.UUID(saved["campaign_id"]).hex
    scoped = {**saved, "world": {**saved["world"], native_host_save.SCOPE_FIELD: scope}}
    source_storage = native_host_save.checkpoint_profile(source / "game-user", saved)
    target_storage = native_host_save.checkpoint_profile(profile, scoped)
    target_directory = native_host_save.directory(target_storage)
    target_directory.mkdir(parents=True, exist_ok=False)
    (campaign / "save").mkdir(parents=True, exist_ok=False)
    for reference in native_host_save.references(saved):
        slot = reference["slot"]
        original = native_host_save.slot_path(source_storage, slot)
        target = native_host_save.slot_path(target_storage, slot)
        shutil.copyfile(original, target)
    runtime.write_json(campaign / "save/campaign.json", scoped)
    assert menu_host_checkpoint(campaign, profile=profile, require_scoped=True) == scoped
    assert saved_campaign(campaign, profile / "Saved", campaign.name) == (campaign, OBSERVATORY)
    assert campaign_save_hashes(source) == before
    return saved, roll, item


def host_command(work: Path, campaign: str, *, fresh_seed: int | None = None) -> list[str]:
    args = [sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work), "host",
            "--campaign", campaign, "--port", "0", "--headless", "--fixtures", "--native-events",
            "--native-guest", "--native-zones", "--native-expanded-zones"]
    if fresh_seed is not None:
        args.extend(("--start-zone", OBSERVATORY, "--native-start-preset", "wanderer",
                     "--native-world-seed", str(fresh_seed)))
    return args


async def arm_probe(mailbox: Path, identity: str | None = None) -> Path:
    nonce = uuid.uuid4().hex[:16]
    request = (f"{nonce}\t{identity}" if identity else nonce) + "\n"
    (mailbox / "measure-cabinet-cold").write_text(request, encoding="ascii", newline="")
    armed = mailbox / f"cabinet-cold-{nonce}.armed"
    async with asyncio.timeout(8):
        while not armed.is_file() or armed.read_bytes() != request.encode("ascii"):
            await asyncio.sleep(.05)
    await asyncio.sleep(.60)
    trace = mailbox / f"cabinet-cold-{nonce}.tsv"
    assert not trace.exists(), "Cabinet probe expired before the pickup"
    return trace


async def finish_probe(trace: Path) -> None:
    async with asyncio.timeout(15):
        while not trace.is_file() or not trace.read_bytes().endswith(b"\n"):
            await asyncio.sleep(.10)

async def collect_probe_garbage(mailbox: Path) -> None:
    nonce = uuid.uuid4().hex[:16]
    (mailbox / "measure-cabinet-cold").write_text(f"{nonce}\tgc\n", encoding="ascii", newline="")
    receipt = mailbox / f"cabinet-cold-{nonce}.gc"
    async with asyncio.timeout(8):
        while not receipt.is_file() or receipt.read_bytes() != b"collected\n":
            await asyncio.sleep(.05)


async def verify_resident_pickup(host, guest, guest_pawn: str, source: tuple[dict, dict, dict], *,
                                 combined: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def memory(state):
        return state.get("local_bridge") == state.get("local_status_source") == "memory"

    host_state, _ = await asyncio.gather(wait_resident_status(host_run, memory), wait_resident_status(guest_run, memory))
    game = host_state["game"]
    host_pawn = game["pawns"]["local"]["name"]
    assert game["map"] == OBSERVATORY
    if combined:
        assert game.get("native_world_loot_host_presentation") is True, "The packed worker lacks the joint pickup capability"

    async def submit_ok(run, action, **arguments):
        result = await submit(run, action, **arguments)
        assert result["ok"], result
        return result

    saved, roll, item = source
    assert game["save_scope"] == uuid.UUID(saved["campaign_id"]).hex
    assert game["native_scene"]["instance_seed"] == SEED
    assert host_state["gameplay"][native_loot.STATE_KEY][CABINET]["id"] == roll["id"]

    restored, = [row for row in await resident_events(host_run, host_state) if row["event"] == "native_host_save_restored"]
    assert (restored["state"], restored["slot"], restored["map"]) == (
        "restored", saved["world"]["native_host_save"]["slot"], OBSERVATORY)

    def save_fingerprints():
        stock = {name: digest for name, digest in saved_hashes(host.profile / "Saved/SaveGames").items()
                 if Path(name).name.startswith("SFCoop_")}
        return campaign_save_hashes(host.campaign), stock

    async def pick_up(crate, current_roll, current_item, *, move_guest=True):
        guid = current_item["guid"]
        identity = native_world_loot.crate_identity(current_roll["id"], guid)

        def visible(state, within):
            frame = state.get("game", {})
            position = frame.get("pawns", {}).get("local", {}).get("location")
            return (memory(state) and frame.get("map") == OBSERVATORY and position
                    and math.dist(position, current_item["transform"]["position"]) <= within
                    and frame.get(native_world_loot.KEY, {}).get(identity, {}).get("class_path") == current_item["class_path"])

        await wait_resident_status(host_run, lambda state: visible(state, 350))
        for pawn in ((guest_pawn, host_pawn) if move_guest else (host_pawn,)):
            # The fixture uses the compatibility status file. A resident command
            # can replace its acknowledgment before the test reads it.
            for attempt in range(3):
                try:
                    approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=1.5)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
            assert approached["ack"]["message"] == "stock loot height and line of sight verified"
        await asyncio.gather(wait_resident_status(host_run, lambda state: visible(state, 250)),
                             wait_resident_status(guest_run, lambda state: visible(state, 250)))
        before = save_fingerprints()
        assert all(before)

        trace = await arm_probe(host.bridge.directory, identity)
        fired = await host.bridge.command(native_world_loot.FIXTURE, identity, timeout=5)
        assert fired["ack"]["message"] == "stock world loot interaction intercepted", fired["ack"]

        def settled(state):
            rows = state.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate, {}).get("items", ())
            return identity not in state.get("game", {}).get(native_world_loot.KEY, {}) and any(
                row["guid"] == guid and row.get("claimed_by") == "host" for row in rows)

        await asyncio.gather(wait_resident_status(host_run, settled), wait_resident_status(guest_run, settled))
        await finish_probe(trace)
        records = await resident_events(host_run, host_state)
        claim, = [row for row in records if row["event"] == "native_loot_claim_accepted" and row.get("guid") == guid]
        receipt = claim["operation_id"]
        played, = [row for row in records if row["event"] == "native_pickup_sound_played" and row.get("operation_id") == receipt]
        inventory = (await submit_ok(host_run, "native-inventory-list"))["native_inventory"]
        if current_item["class_path"] in native_loot.CASH_RANGES:
            credit = claim["wallet_credit"]
            assert credit["after"] - credit["before"] == credit["amount"] > 0
            assert all(row["guid"] != guid for row in inventory["items"])

            def wallet_credited(state):
                return (state.get("game") or {}).get("native_wallet") == credit["after"]

            await asyncio.gather(wait_resident_status(host_run, wallet_credited),
                                 wait_resident_status(guest_run, wallet_credited))
            received = {"wallet_credit": credit}
        else:
            row, = [row for row in inventory["items"] if row["guid"] == claim.get("inventory_guid", guid)]
            assert row["class_path"] == current_item["class_path"]
            if row["stackable"]:
                assert row["quantity"] == claim.get("stack_merge", {}).get("after", current_item["ammo"])
            received = {"inventory": row}
        assert save_fingerprints() == before
        timings = summarize(trace, resident=True, combined_operation=receipt if combined else None)
        return {"cabinet": crate, "stock_class": current_item["class_path"], "guid": guid, "projection": identity,
                "operation": receipt, **received, "native_claim_unix": claim["time_unix"],
                "sound_receipt_unix": played["time_unix"], "timings": timings, "trace": str(trace),
                "both_projected_views_retired": True, "saves_unchanged_after_pickup": True}

    pickup = await pick_up(CABINET, roll, item)
    assert pickup["timings"]["escrow_load_ms"] is not None, "The measurement did not exercise the copied cold escrow"
    cached_replays = None
    after_checkpoint = []
    if combined:
        # This checkpoint has one item. Reissue its exact native claim to exercise
        # the same escrow lookup after Unreal collects unreferenced objects.
        guid = item["guid"]
        assert pickup["inventory"]["guid"] == guid, pickup
        inventory = (await submit_ok(host_run, "native-inventory-list"))["native_inventory"]
        before = save_fingerprints()
        await collect_probe_garbage(host.bridge.directory)
        replay_trace = await arm_probe(host.bridge.directory)
        for _ in range(2):
            replay = await host.bridge.command("native-loot-claim", host_pawn, CABINET, guid, roll["escrow"]["slot"],
                                               *(str(axis) for axis in item["transform"]["position"]), timeout=5)
            details = replay["native_loot"]
            assert details["ok"] and details["status"] == "already_claimed", details
        await finish_probe(replay_trace)
        labels = [line.split("\t", 1)[0] for line in replay_trace.read_text(encoding="utf-8").splitlines()]
        after = (await submit_ok(host_run, "native-inventory-list"))["native_inventory"]
        assert after == inventory
        assert save_fingerprints() == before
        cached_replays = {"native_claims": labels.count("claim-native"),
                          "escrow_disk_loads": labels.count("escrow-load"), "engine_gc_verified": True,
                          "trace": str(replay_trace)}
        # Checkpoint a real in-memory roll while the party is still in the map.
        crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == OBSERVATORY
                  and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1")]
        await host.bridge.command("native-loot-approach", host_pawn, crate, timeout=5)
        before_open = save_fingerprints()
        opened = (await submit_ok(host_run, "native-loot-open", target=crate))["native_loot_roll"]
        candidates = opened["items"]
        assert len(candidates) == 2 and save_fingerprints() == before_open, opened
        checkpoint = await submit_ok(host_run, "checkpoint")
        committed = menu_host_checkpoint(host.campaign, profile=host.profile, require_scoped=True)
        assert committed["revision"] == checkpoint["revision"] > saved["revision"]
        durable = committed["world"]["party"][native_loot.STATE_KEY][crate]
        assert durable["id"] == opened["id"] and durable["escrow"].get("pending") is not True, durable
        assert all(row in durable["items"] for row in candidates), durable
        await collect_probe_garbage(host.bridge.directory)
        for index, candidate in enumerate(candidates):
            checkpoint_pickup = await pick_up(crate, durable, candidate, move_guest=index == 0)
            assert checkpoint_pickup["timings"]["escrow_load_ms"] is None, checkpoint_pickup
            checkpoint_pickup.update(checkpoint_revision=checkpoint["revision"], engine_gc_verified=True)
            after_checkpoint.append(checkpoint_pickup)
        assert cached_replays["native_claims"] == 2 and cached_replays["escrow_disk_loads"] == 0, [
            label for label in labels if label.startswith(("claim", "escrow"))
        ]
    return {**pickup,
            "restored_source_revision": saved["revision"], "restored_scope": game["save_scope"],
            "restored_host_slot": restored["slot"], "restored_seed": SEED, "escrow_slot": roll["escrow"]["slot"],
            "local_bridge": "memory", "cached_replays": cached_replays, "after_checkpoint": after_checkpoint}


async def verify_saved_pickup(host, stopped, saved, source):
    original, roll, item = source
    committed = saved["world"]["party"][native_loot.STATE_KEY][CABINET]
    pickup, = [row for row in committed["items"] if row["guid"] == item["guid"]]
    assert committed["id"] == roll["id"] and pickup == {**item, "claimed_by": "host"}, committed
    assert saved["revision"] > original["revision"]
    events = await resident_events(host.campaign / "run", stopped)
    exit_event, = [row for row in events if row["event"] == "stock_pause_exit" and row.get("reason") == "save_and_quit"]
    assert exit_event["result"] == "ok", exit_event
    return {"revision": saved["revision"], "claimed_guid": pickup["guid"], "claimed_by": pickup["claimed_by"]}


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

    async def start(campaign, *, fresh_seed=None):
        control = campaign / "run"
        control.mkdir(parents=True, exist_ok=True)
        log = control / "cabinet-worker.log"
        with log.open("wb") as output:
            args = host_command(work, campaign.name, fresh_seed=fresh_seed)
            process = await asyncio.create_subprocess_exec(*args, cwd=ROOT,
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
            process = await start(prepare, fresh_seed=seed)
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
        process = await start(resumed)
        assert checkpoint(resumed) == saved
        identity = native_world_loot.crate_identity(roll["id"], item["guid"])
        await wait(run, lambda state: identity in state.get("game", {}).get(native_world_loot.KEY, {}))
        await local(run, native_world_loot.APPROACH, target=f"host@{identity}")
        before_pickup = campaign_save_hashes(resumed)
        mailbox, = (run / "game-bridge").iterdir()
        trace = await arm_probe(mailbox)
        proof["probe_armed"] = True
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
        await finish_probe(trace)
        proof["timings"] = summarize(trace)
        # The normal stop below explicitly saves. Compare the pickup against the live checkpoint first.
        proof["unchanged_after_pickup"] = campaign_save_hashes(resumed) == before_pickup
        proof["source_preserved"] = campaign_save_hashes(source) == before_source
        assert proof["unchanged_after_pickup"] and proof["source_preserved"]
        revision = await stop(run, process)
        proof.update(shutdown_revision=revision, coordinator_exit_code=process.returncode)
        assert revision is not None and checkpoint(resumed)["revision"] == revision > saved["revision"]
        proof["source_preserved"] = campaign_save_hashes(source) == before_source
        assert proof["source_preserved"]
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
