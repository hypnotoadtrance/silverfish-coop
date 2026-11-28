"""Measure packaged Direct IP traffic from one host and three guests in Depot."""

import argparse
import asyncio
from itertools import combinations
import math
import os
from pathlib import Path
import shutil
import time
import uuid

from live_multi_smoke import saved_hashes
from live_portable_title_smoke import discover_mailbox, launch_portable, qualify_release, stock_save_hashes
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, bound_pawn, offered
from portable_guest_world_start import wait_resident_status
from tools.silverfish import embedded_startup, native_host_save, runtime, service
from tools.silverfish.control import current_status, submit
from tools.silverfish.steam_network_report import LINK_COUNTERS
from tools.silverfish.travel import RAIL_YARD


WINDOW_SECONDS = 1.0
MIN_WINDOW_SECONDS = .75
MAX_WINDOW_SECONDS = 1.5
MOVEMENT = ((1, 0), (0, 1), (-1, 0), (0, -1))


def source_checkpoint(campaign, profile, revision):
    primary = runtime.read_json(campaign / "save/campaign.json")
    checkpoint = service.menu_host_checkpoint(campaign, profile=profile, require_scoped=True)
    if checkpoint != primary or primary.get("revision") != revision or primary["world"].get("map") != RAIL_YARD:
        raise ValueError("Use a valid primary Depot checkpoint with the requested revision")
    storage = native_host_save.checkpoint_profile(profile, primary)
    return primary, [native_host_save.slot_path(storage, row["slot"]) for row in native_host_save.references(primary)]


def copy_checkpoint(source, checkpoint, slots, campaign, profile):
    storage = native_host_save.checkpoint_profile(profile, checkpoint)
    native_host_save.directory(storage).mkdir(parents=True)
    for slot in slots:
        shutil.copy2(slot, native_host_save.slot_path(storage, slot.stem))
    (campaign / "save").mkdir(parents=True)
    shutil.copy2(source / "save/campaign.json", campaign / "save/campaign.json")
    if service.menu_host_checkpoint(campaign, profile=profile, require_scoped=True) != checkpoint:
        raise RuntimeError("The copied Depot checkpoint did not pass native save verification")


async def host_depot(seat):
    form = "native-title-host-form-fixture"
    await seat.bridge.command(form, "open")
    listing = seat.bridge.directory / "title-host-campaigns.tsv"
    async with asyncio.timeout(12):
        while not listing.is_file():
            await asyncio.sleep(.1)
    await seat.bridge.command(form, "browse")
    selected = await seat.bridge.command(form, "saved-state")
    if not selected["ack"]["message"].endswith(":" + seat.campaign.name):
        raise RuntimeError("The host form selected another campaign")
    await seat.bridge.command(form, "choose-saved")
    await seat.bridge.command(form, "port", str(seat.port))
    await seat.bridge.command(form, "submit")
    return await wait_resident_status(seat.campaign / "run", lambda frame: (
        frame.get("state") == "hosting" and frame.get("game", {}).get("map") == RAIL_YARD
        and frame.get("pid") == seat.bridge.process.pid and frame.get("local_bridge") == "memory"))


async def join_depot(host, guest, number):
    async def join(*args):
        return await guest.bridge.command("native-title-join-form-fixture", *args)

    async def screen(*states):
        async with asyncio.timeout(22):
            while True:
                row = (await join("observe"))["ack"]["message"].removeprefix("title_join_form_state=")
                if row in states:
                    return row
                await asyncio.sleep(.1)

    def characters():
        checkpoint = runtime.read_json(host.campaign / "save/campaign.json")
        return checkpoint["characters"].keys()

    prior = set(characters())
    await join("open")
    await join("address", "127.0.0.1")
    await join("port", str(host.port))
    await join("no_password")
    if await screen("trust_host", "catalog;count=0") == "trust_host":
        await guest.bridge.command("native-title-button-fixture", "trust")
        await screen("catalog;count=0")
    await join("create", f"Depot scout {number}")
    await screen("catalog;count=1")
    async with asyncio.timeout(12):
        while not (created := set(characters()) - prior):
            await asyncio.sleep(.1)
    character, = created
    await join("select", character)
    await join("submit")
    await wait_resident_status(guest.client / "run", lambda frame: (
        frame.get("state") == "connected" and frame.get("gameplay_fresh") is True
        and frame.get("game", {}).get("map") == RAIL_YARD and frame.get("local_bridge") == "memory"
        and frame.get("character_id") == character and frame.get("pid") == guest.bridge.process.pid))
    await wait_resident_status(host.campaign / "run", lambda frame: bound_pawn(frame, character))
    return character


def traffic_window(first, last, *, expected_state, session, visit):
    before, before_seen = first
    after, after_seen = last
    for frame in (before, after):
        if (frame.get("game", {}).get("map") != RAIL_YARD or frame.get("state") != expected_state
                or frame.get("host_session", frame.get("generation")) != session or frame.get("zone_epoch") != visit
                or frame.get("link_connected") is not True or frame.get("awaiting_zone_guest") or frame.get("stale")
                or (expected_state == "hosting" and frame["game"].get("remote_clients") != 3)
                or (expected_state == "connected" and frame.get("gameplay_fresh") is not True)):
            raise RuntimeError("The party changed visits or disconnected during the Depot measurement")
    if before["generation"] != after["generation"]:
        raise RuntimeError("A controller restarted during the Depot measurement")
    elapsed = after["updated_unix"] - before["updated_unix"]
    row = {"source_start_unix": before["updated_unix"], "source_end_unix": after["updated_unix"],
           "seconds": round(elapsed, 6), "first_sample_age_ms": round((before_seen - before["updated_unix"]) * 1000, 1),
           "last_sample_age_ms": round((after_seen - after["updated_unix"]) * 1000, 1)}
    if not MIN_WINDOW_SECONDS <= elapsed <= MAX_WINDOW_SECONDS:
        return {**row, "excluded": "status_interval_outside_window"}
    old, new = before["transport"], after["transport"]
    if any(new[key] < old[key] for key in LINK_COUNTERS if key in old and key in new):
        raise RuntimeError("The transport counters reset during the Depot measurement")
    activity = {key: new[key] - old[key] for key in LINK_COUNTERS if key in old and key in new}
    return {**row, "quic_send_bps": round(8 * activity["quic_payload_bytes_sent"] / elapsed),
            "quic_receive_bps": round(8 * activity["quic_payload_bytes_received"] / elapsed),
            "keepalive_rtt_us": new.get("keepalive_rtt_us"), "link_activity": activity}


async def measure(runs, session, visit, windows, *, moving=False):
    async def read(run):
        return await current_status(run), time.time()

    async def sample():
        return dict(zip(runs, await asyncio.gather(*(read(run) for run in runs.values())), strict=True))

    previous = await sample()
    records = {seat: [] for seat in runs}
    start_positions = {}
    max_displacement = {}
    if moving:
        start_positions = {seat: frame["game"]["pawns"]["local"]["location"] for seat, (frame, _) in previous.items()
                           if seat != "host"}
        max_displacement = dict.fromkeys(start_positions, 0.0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    for index in range(windows):
        if moving:
            x, y = MOVEMENT[index % len(MOVEMENT)]
            async with asyncio.timeout(4):
                replies = await asyncio.gather(*(submit(run, "walk", x=x, y=y, milliseconds=850, continuous=True)
                                                 for seat, run in runs.items() if seat != "host"))
            if not all(reply.get("ok") and reply.get("movement_scheduled") for reply in replies):
                raise RuntimeError(f"A guest could not start the stock movement window: {replies}")
        await asyncio.sleep(max(0, started + (index + 1) * WINDOW_SECONDS - loop.time()))
        current = await sample()
        for seat in runs:
            row = traffic_window(previous[seat], current[seat], expected_state="hosting" if seat == "host" else "connected",
                                 session=session, visit=visit)
            records[seat].append(row)
        for seat, start in start_positions.items():
            point = current[seat][0]["game"]["pawns"]["local"]["location"]
            distance = math.dist(start, point)
            if math.isfinite(distance):
                max_displacement[seat] = max(max_displacement[seat], distance)
        previous = current
    result = {}
    for seat, rows in records.items():
        valid = [row for row in rows if "excluded" not in row]
        if not valid:
            raise RuntimeError(f"{seat} did not produce a bounded Depot traffic window: {rows}")
        result[seat] = {"peak_send_interval": max(valid, key=lambda row: row["quic_send_bps"]),
                        "peak_receive_interval": max(valid, key=lambda row: row["quic_receive_bps"]), "windows": rows}
        if seat in max_displacement:
            result[seat]["max_displacement_cm"] = round(max_displacement[seat], 2)
    return result


async def verify(work, games, original, source, source_profile, revision, windows):
    roots = (work, *games, original, source, source_profile)
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in root.parts} for root in roots)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use separate private replica, checkpoint, profile, and workspace directories")
    releases = [qualify_release(game, None) for game in games]
    if len({release.manifest_sha256 for release in releases}) != 1:
        raise ValueError("Apply the same portable release to all four private replicas")
    checkpoint, slots = source_checkpoint(source, source_profile, revision)
    checkpoint_files = [source / "save/campaign.json", *slots]
    source_hashes = [runtime._file_sha256(path) for path in checkpoint_files]
    original_hashes = saved_hashes(original)
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if len(stock_slots) < 2 or not any(path.name == "Config.sav" for path in stock_slots):
        raise ValueError("Supply the private stock baseline containing Config.sav and a single-player slot")
    case = work / f"portable-depot-traffic-{uuid.uuid4().hex[:8]}"
    case.mkdir(parents=True)
    print(case, flush=True)
    processes, seats, baselines = [], [], []
    proof = {"verified": False, "measurement": "direct_ip_quic_payload_bytes", "map": RAIL_YARD,
             "source_checkpoint_revision": revision, "runtime_manifest_sha256": releases[0].manifest_sha256,
             "requested_window_seconds": WINDOW_SECONDS, "window_bounds_seconds": [MIN_WINDOW_SECONDS, MAX_WINDOW_SECONDS]}
    try:
        host_port = runtime.spare_udp_port()
        for index, (game, release) in enumerate(zip(games, releases, strict=True)):
            workspace = case / f"seat-{index + 1}"
            title = workspace / release.title_directory
            profile = title / "game-user"
            stock = profile / "Saved/SaveGames"
            stock.mkdir(parents=True)
            for slot in stock_slots:
                shutil.copy2(slot, stock / slot.name)
            baselines.append(stock_save_hashes(profile))
            campaign = workspace / "campaigns/depot-traffic"
            if index == 0:
                copy_checkpoint(source, checkpoint, slots, campaign, profile)
            process = launch_portable(game, profile)
            processes.append(process)
            mailbox = await discover_mailbox(title / "run/game-bridge", process)
            if runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) != release.selection:
                raise RuntimeError("A replica did not start its qualified portable runtime")
            seat = Seat(profile, campaign, workspace / "clients" / title.name,
                        host_port if index == 0 else 0, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)

        host, *guests = seats
        await host_depot(host)
        characters = [await join_depot(host, guest, number) for number, guest in enumerate(guests, 1)]
        runs = {"host": host.campaign / "run", **{f"guest{number}": guest.client / "run"
                for number, guest in enumerate(guests, 1)}}
        settled = await wait_resident_status(runs["host"], lambda frame: (
            frame.get("game", {}).get("remote_clients") == len(guests)
            and len({bound_pawn(frame, character) for character in characters} - {None}) == len(guests)))
        session, visit = settled["generation"], settled["zone_epoch"]
        await asyncio.sleep(2)
        proof["idle"] = await measure(runs, session, visit, windows)
        proof["guests_walking"] = await measure(runs, session, visit, windows, moving=True)
        if any(proof["guests_walking"][seat]["max_displacement_cm"] < 40 for seat in runs if seat != "host"):
            raise RuntimeError("The walking phase did not observe at least 40 cm of movement from each guest")
        await host.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
        for action in ("open", "prompt", "confirm"):
            await stock_pause(host.bridge, action)
        stopped = await asyncio.gather(*(wait_resident_status(run, lambda frame: frame.get("state") == "stopped")
                                         for run in runs.values()))
        if any(frame.get("stop_reason") != "host_session_ended" for frame in stopped):
            raise RuntimeError("A seat stopped before the host's normal Save and Quit")
        await asyncio.gather(*(offered(seat) for seat in seats))
        for seat, baseline in zip(seats, baselines, strict=True):
            observed = stock_save_hashes(seat.profile)
            if {key: value for key, value in observed.items() if key != "Config.sav"} != {
                    key: value for key, value in baseline.items() if key != "Config.sav"}:
                raise RuntimeError("A private stock single-player slot changed")
        proof["verified"] = True
    except Exception as error:
        proof["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        cleanup = await asyncio.gather(*(runtime.stop_game(process) for process in processes if process.poll() is None),
                                       return_exceptions=True)
        proof["game_executable_launches"] = len(processes)
        proof["processes_closed"] = all(process.poll() is not None for process in processes)
        proof["source_checkpoint_preserved"] = source_hashes == [runtime._file_sha256(path) for path in checkpoint_files]
        proof["original_saves_preserved"] = original_hashes == saved_hashes(original)
        proof["verified"] = (proof["verified"] and proof["processes_closed"] and not any(cleanup)
                             and proof["source_checkpoint_preserved"] and proof["original_saves_preserved"])
        runtime.write_json(case / "portable-depot-traffic-verification.json", proof)
    if not proof["verified"]:
        raise RuntimeError("The Depot measurement or cleanup did not verify")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--game", type=Path, nargs=4, required=True, metavar="REPLICA")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="private stock Saved directory")
    parser.add_argument("--checkpoint", type=Path, required=True, help="private campaign directory containing save/campaign.json")
    parser.add_argument("--checkpoint-profile", type=Path, required=True, help="private game-user directory for its native saves")
    parser.add_argument("--checkpoint-revision", type=int, required=True)
    parser.add_argument("--windows", type=int, choices=range(4, 21), default=8, help="one-second samples per phase")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), tuple(game.resolve(strict=True) for game in args.game),
                       args.vanilla_saved.resolve(strict=True), args.checkpoint.resolve(strict=True),
                       args.checkpoint_profile.resolve(strict=True), args.checkpoint_revision, args.windows))
