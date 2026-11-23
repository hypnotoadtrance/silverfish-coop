"""Verify the stock dig lease in one private, muted resident host."""

import argparse
import asyncio
from itertools import combinations
from pathlib import Path
import re
import shutil
import time
from uuid import UUID, uuid4

from live_multi_smoke import saved_hashes
from live_portable_title_smoke import discover_mailbox, fingerprint, launch_portable, qualify_release
from live_same_process_listener_smoke import choose_ports
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, offered
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import embedded_startup, native_host_dig_live as dig, native_host_save as stock, runtime, service
from tools.silverfish.travel import CROSSROADS


FORM = "native-title-host-form-fixture"


def fingerprints(paths):
    return {str(path): {"sha256": fingerprint(path), "mtime_ns": path.stat().st_mtime_ns} for path in paths}


def copy_crossroads(source, campaign, profile, *, escrow_recovery=False):
    saved = service.menu_host_checkpoint(source)
    if not saved or saved["world"].get("map") != CROSSROADS:
        raise ValueError("Use a closed Crossroads campaign checkpoint")
    loose = dig.escrow_frames(saved["world"]["party"])
    if escrow_recovery and len(loose) != 1:
        raise ValueError("Use a checkpoint with one genuine loose dig escrow for --escrow-recovery")
    if not escrow_recovery and loose:
        raise ValueError("Use --escrow-recovery when the checkpoint holds a loose dig escrow")
    scope = UUID(saved["campaign_id"]).hex
    source_storage = stock.checkpoint_profile(source / "game-user", saved)
    target_storage = stock.SaveScope(profile, scope)
    stock.directory(target_storage).mkdir(parents=True)
    originals = {frame["slot"]: stock.slot_path(source_storage, frame["slot"]) for frame in stock.references(saved)}
    source_files = (source / "save" / "campaign.json", *originals.values())
    before = fingerprints(source_files)
    for slot, origin in originals.items():
        shutil.copy2(origin, stock.slot_path(target_storage, slot))
    scoped = {**saved, "world": {**saved["world"], stock.SCOPE_FIELD: scope}}
    runtime.write_json(campaign / "save" / "campaign.json", scoped)
    assert service.menu_host_checkpoint(campaign, profile=profile, require_scoped=True) == scoped
    return scoped, before


def solo_armed(status, scope, custody=None):
    game = status.get("game", {})
    diagnostic = game.get(dig.DIAGNOSTIC_KEY, {})
    dig_state = game.get(dig.KEY, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    phase = "escrowed" if custody else "ready"
    serial = dig_state.get("serial")
    if custody:
        actor = dig_state.get("actor")
        prefix = f"Package_Item_FarmHouse_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Package_Item_FarmHouse_C_"
        matches_custody = (type(serial) is int and serial > 0 and isinstance(actor, str) and actor.startswith(prefix)
                           and actor[len(prefix):].isdigit() and dig_state.get("package_guid") is None
                           and dig._matches(custody, dig_state, "escrow_guid")
                           and dig_state.get("slot") in (None, custody["escrow"]["slot"]))
    else:
        matches_custody = serial == 0
    return (status.get("state") == "hosting" and status.get("local_bridge") == "memory"
            and status.get("local_status_source") == "memory" and status.get("link_connected") is False
            and game.get("remote_clients") == 0 and game.get("map") == CROSSROADS and game.get("save_scope") == scope
            and pawn and (diagnostic.get("map"), diagnostic.get("pawn"), diagnostic.get("context"),
                          diagnostic.get("heartbeat"), diagnostic.get("arm"), diagnostic.get("read")) ==
                (CROSSROADS, pawn, "qualified", "fresh", "armed", phase)
            and (dig_state.get("map"), dig_state.get("pawn"), dig_state.get("state")) == (CROSSROADS, pawn, phase)
            and matches_custody)


async def observe_lease(run, heartbeat, initial, scope, custody=None):
    current, samples = initial, 1
    started = time.monotonic()
    while time.monotonic() - started < 3.2:
        assert not heartbeat.exists(), "The installed host wrote a dig heartbeat file"
        sequence = current["game"]["sequence"]
        current = await wait_resident_status(
            run, lambda frame: frame.get("game", {}).get("sequence", 0) > sequence, timeout=3)
        assert solo_armed(current, scope, custody) and current["zone_epoch"] == initial["zone_epoch"], current
        if custody:
            first, last = initial["game"][dig.KEY], current["game"][dig.KEY]
            assert (last["serial"], last["actor"]) == (first["serial"], first["actor"]), last
        samples += 1
    assert not heartbeat.exists(), "The installed host wrote a dig heartbeat file"
    return {"seconds": round(time.monotonic() - started, 3), "native_frames": samples,
            "first_sequence": initial["game"]["sequence"], "last_sequence": current["game"]["sequence"],
            "diagnostic": current["game"][dig.DIAGNOSTIC_KEY]}


async def verify_startup_restore(run, hosted, custody):
    events = [event for event in await resident_events(run, hosted) if event.get("generation") == hosted["generation"]]
    restore_index, = [index for index, event in enumerate(events) if event["event"] == "native_host_dig_escrow_restored"]
    listen_index, = [index for index, event in enumerate(events) if event["event"] == "listening"]
    restore, report = events[restore_index], hosted["game"][dig.KEY]
    assert restore_index < listen_index, events
    assert (restore["map"], restore["actor"], restore["serial"], restore["package_guid"]) == (
        CROSSROADS, report["actor"], report["serial"], custody["package_guid"]), (restore, report)
    return {"persisted_custody": custody, "restored_actor": report, "restore_before_listener": restore}


async def verify(work, game, source, canary, manifest, *, escrow_recovery=False):
    roots = work, game, source, canary
    private = Path(__file__).resolve().parents[2] / "Runtime"
    if (not all(path.is_relative_to(private) and path.is_dir() for path in roots)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use distinct private workspace, installed replica, checkpoint and save canary directories")
    release = qualify_release(game, None)
    assert release.manifest_sha256 == manifest, "The replica does not match the requested runtime manifest"
    addon = fingerprint(game / runtime.SHIPPING.parent / runtime.CPP_ADDON_DLL)
    assert addon == runtime.read_json(game / runtime.PORTABLE_STAGE_MANIFEST)["cpp_death_lab"]["addon"]
    canary_before = saved_hashes(canary)
    assert canary_before, "An existing private save canary is required"
    case = work / f"resident-dig-lease-{uuid4().hex[:8]}"
    case.mkdir()
    print(case, flush=True)
    profile = case / release.title_directory / "game-user"
    campaign = case / "campaigns" / "dig-lease"
    saved, source_before = copy_crossroads(source, campaign, profile, escrow_recovery=escrow_recovery)
    scope = saved["world"][stock.SCOPE_FIELD]
    storage = stock.SaveScope(profile, scope)
    required_slots = tuple(stock.slot_path(storage, frame["slot"]) for frame in stock.references(saved))
    pinned = fingerprints(required_slots)
    custody = saved["world"]["party"][dig.CUSTODY] if escrow_recovery else None
    proof = {"verified": False, "runtime_manifest": manifest, "native_addon_sha256": addon,
             "campaign": saved["campaign_id"], "revision_before": saved["revision"]}
    process = None
    try:
        process = launch_portable(game, profile)
        mailbox = await discover_mailbox(profile.parent / "run" / "game-bridge", process)
        assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
        port, _ = choose_ports()
        seat = Seat(profile, campaign, case / "clients" / "local", port, runtime.Bridge(mailbox, process))
        await offered(seat)

        async def form(*fields):
            return await seat.bridge.command(FORM, *fields)

        await form("open")
        async with asyncio.timeout(12):
            while not (mailbox / "title-host-campaigns.tsv").is_file():
                await asyncio.sleep(.1)
        await form("browse")
        assert (await form("saved-state"))["ack"]["message"] == "title_host_saved=1:dig-lease"
        await form("choose-saved")
        await form("port", str(port))
        heartbeat = mailbox / dig.HEARTBEAT

        def armed_without_file(frame):
            assert not heartbeat.exists(), "The installed host created a dig heartbeat file during startup"
            return solo_armed(frame, scope, custody)

        assert not heartbeat.exists()
        await form("submit")
        run = campaign / "run"
        hosted = await wait_resident_status(run, armed_without_file)
        assert hosted["pid"] == process.pid and hosted["campaign"]["campaign_id"] == saved["campaign_id"]

        def persisted():
            return fingerprints((*sorted((campaign / "save").glob("campaign*.json")),
                                 *sorted((profile / "Saved" / "SaveGames").rglob("*.sav"))))

        before = persisted()
        assert pinned.keys() <= before.keys(), before
        if custody:
            assert fingerprints(required_slots) == pinned, "The startup changed the copied native save generations"
            current = service.menu_host_checkpoint(campaign, profile=profile, require_scoped=True)
            # The cold native report can omit the slot once it has loaded the saved scratch object.
            # Verify that exact slot and its payload through the qualified persisted generation.
            assert current["world"]["party"][dig.CUSTODY] == custody
            proof["escrow_recovery"] = await verify_startup_restore(run, hosted, custody)
            slot = stock.slot_path(storage, custody["escrow"]["slot"])
            copied = pinned[str(slot)]
            assert copied["sha256"] == custody["escrow"]["sha256"] and slot.stat().st_size == custody["escrow"]["size"]
            proof["escrow_recovery"]["copied_escrow_file"] = copied
        proof["solo_lease"] = await observe_lease(run, heartbeat, hosted, scope, custody)
        after = persisted()
        proof["save_files"] = {"at_readiness": before, "after_renewal": after}
        assert before == after, "The solo renewal changed a campaign or stock save file"

        # This copied field save has no stock Save and Quit option at its current location.
        await seat.bridge.wait_for(lambda frame: frame.get("stock_pause_exit", {}).get("state") == "armed")
        await stock_pause(seat.bridge, "open")
        await stock_pause(seat.bridge, "prompt")
        await stock_pause(seat.bridge, "confirm")
        stopped = await wait_resident_status(run, lambda frame: frame.get("state") == "stopped")
        assert stopped["stop_reason"] == "host_session_ended"
        await offered(seat)
        proof["verified"] = True
    except Exception as error:
        proof["failure"] = str(error) or type(error).__name__
        raise
    finally:
        if process is not None and process.poll() is None:
            await runtime.stop_game(process)
        preserved = {"original_save_canary": saved_hashes(canary) == canary_before,
                     "source_checkpoint": fingerprints(Path(path) for path in source_before) == source_before,
                     "portable_activation": all(fingerprint(path) == digest for path, digest in release.activation.items()),
                     "process_closed": process is None or process.poll() is not None}
        proof["preserved"] = preserved
        proof["verified"] &= all(preserved.values())
        runtime.write_json(case / "resident-dig-lease-verification.json", proof)
        assert all(preserved.values()), preserved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Separate private Runtime parent for the test results")
    parser.add_argument("--game", type=Path, required=True, help="Private clean replica with the exact portable archive applied")
    parser.add_argument("--checkpoint", type=Path, required=True, help="Existing closed Crossroads campaign")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="Existing private stock save canary")
    parser.add_argument("--manifest", required=True, help="Expected installed versioned runtime manifest SHA-256")
    parser.add_argument("--escrow-recovery", action="store_true",
                        help="verify one saved loose Missing Contact package restores before the resident listener")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.manifest):
        parser.error("--manifest must be a SHA-256")
    asyncio.run(verify(*(path.resolve(strict=True) for path in
                         (args.work, args.game, args.checkpoint, args.vanilla_saved)), args.manifest,
                       escrow_recovery=args.escrow_recovery))
