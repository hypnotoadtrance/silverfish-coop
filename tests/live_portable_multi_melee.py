"""Exercise overlapping stock melee from two guests in one isolated campaign."""

import argparse
import asyncio
from contextlib import ExitStack
from itertools import combinations
import math
from pathlib import Path
import shutil
import time
import uuid

from live_portable_title_smoke import (
    discover_mailbox, equip_stock_guest_melee, host_npc_combat_overlay, launch_portable, portable_probe_command,
    qualify_release, stock_melee_edge, stock_melee_edge_receipts, stock_save_hashes,
)
from live_same_process_listener_smoke import choose_ports
from live_unified_title_smoke import Seat, bound_pawn, offered, start_pair
from live_multi_smoke import campaign_save_hashes, saved_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import embedded_startup, native_guest_melee as melee, runtime, service
from tools.silverfish.campaign import CampaignStore


async def command(seat, text):
    return await portable_probe_command(seat.bridge, "portable-npc-override", text)


async def join_second(host, guest):
    host_run = host.campaign / "run"

    def saved_players():
        checkpoint = CampaignStore.read_existing(host.campaign / "save", verify_world=service.verify_travel_checkpoint)
        return set(checkpoint["characters"])

    async def join(*args):
        return await guest.bridge.command("native-title-join-form-fixture", *args)

    async def ready(allow_trust):
        async with asyncio.timeout(22):
            while True:
                row = (await join("observe"))["ack"]["message"].removeprefix("title_join_form_state=")
                if row.startswith("catalog;") or allow_trust and row == "trust_host":
                    return row
                await asyncio.sleep(.10)

    prior = saved_players()
    await join("open")
    await join("address", "127.0.0.1")
    await join("port", str(host.port))
    await join("no_password")
    if await ready(True) == "trust_host":
        await guest.bridge.command("native-title-button-fixture", "trust")
    await ready(False)
    await join("create", "Second melee scout")
    await ready(False)
    async with asyncio.timeout(12):
        while not (created := saved_players() - prior):
            await asyncio.sleep(.08)
    character, = created
    await join("select", character)
    await join("submit")
    connected = await wait_resident_status(guest.client / "run", lambda f: f.get("state") == "connected" and
                                           f.get("character_id") == character and f.get("gameplay_fresh"), timeout=30)
    assert connected["pid"] == guest.bridge.process.pid, connected
    host_state = await wait_resident_status(host_run, lambda f: bound_pawn(f, character), timeout=25)
    return bound_pawn(host_state, character)


async def fight(host, first, second, first_pawn):
    second_pawn = await join_second(host, second)
    assert first_pawn != second_pawn
    guests = first, second
    host_run = host.campaign / "run"
    runs = [guest.client / "run" for guest in guests]
    frames = [await equip_stock_guest_melee(host, guest, f"multi-melee-equip\t{pawn}", "old-pipe")
              for guest, pawn in zip(guests, (first_pawn, second_pawn), strict=True)]
    loadouts = [(frame["game"]["native_guest_primary_scope"]["guid"], frame) for frame in frames]
    checkpoint = campaign_save_hashes(host.campaign)
    center, *positions = (await command(host, f"multi-melee-stage\t{first_pawn}|{second_pawn}")).split(";")

    for seat, run, position in zip(guests, runs, positions, strict=True):
        destination = tuple(map(float, position.split(",")))
        await wait_resident_status(run, lambda f: (point := f["game"].get("pawns", {}).get("local", {}).get("location"))
                                   and math.dist(point, destination) < 40, timeout=10)

    async def visible(seat):
        async with asyncio.timeout(8):
            while (state := await command(seat, f"multi-melee-target\t{center}")) == "waiting":
                await asyncio.sleep(.08)
        return state.split(";")[0]

    references = await asyncio.gather(*(visible(seat) for seat in (host, *guests)))
    target, = set(references)
    assert target.startswith("npc:") and (await command(host, f"multi-melee-target\t{center}")).endswith(";300.000")
    began = time.time()
    try:
        assert await asyncio.gather(*(command(seat, f"face-melee\t{center}") for seat in guests)) == ["ready", "ready"]
        presses = await asyncio.gather(*(stock_melee_edge(run, guid, "press")
                                         for run, (guid, _) in zip(runs, loadouts, strict=True)))
        first_edges = await asyncio.gather(*(stock_melee_edge_receipts(run, frame, edge)
            for run, (_, frame), edge in zip(runs, loadouts, presses, strict=True)))
        serials = {str(receipt[0]["serial"]) for receipt in first_edges}
        assert len(serials) == 2, first_edges

        def held(f):
            rows = f["game"].get(melee.INPUTS_KEY, {})
            return serials <= rows.keys() and all(rows[s]["presses"] == 1 and rows[s]["releases"] == 0 for s in serials)

        await wait_resident_status(host_run, held, timeout=8)
        releases = await asyncio.gather(*(stock_melee_edge(run, guid, "release")
                                          for run, (guid, _) in zip(runs, loadouts, strict=True)))
        edges = await asyncio.gather(*(stock_melee_edge_receipts(run, frame, edge)
            for run, (_, frame), edge in zip(runs, loadouts, releases, strict=True)))

        async def report(run, frame):
            async with asyncio.timeout(8):
                while True:
                    rows = [row for row in await resident_events(run, frame) if row["time_unix"] >= began and
                            row["event"] in (melee.CONTACT_RESULT, melee.CONTACT_RESULT + "_rejected")]
                    if rows:
                        row, = rows
                        assert row["event"] == melee.CONTACT_RESULT, row
                        return row[melee.CONTACT_RESULT]
                    await asyncio.sleep(.05)

        results = await asyncio.gather(*(report(run, frame) for run, (_, frame) in zip(runs, loadouts, strict=True)))
    finally:
        await asyncio.gather(*(command(seat, "clear-melee-aim") for seat in guests))
    assert {result["target"] for result in results} == {target}, results
    assert {result["pawn"] for result in results} == {first_pawn, second_pawn}, results
    initial, final = sorted(results, key=lambda result: -result["before"])
    assert initial["before"] == 300 and initial["after"] == final["before"] > final["after"], results
    for result in results:
        assert result["damage"] > 0 and math.isclose(result["before"] - result["after"], result["damage"], abs_tol=.001)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"shared_target": target, "overlap_serials": sorted(serials), "releases": edges, "contacts": results}


async def verify(work, games, original):
    roots = work, original, *games
    assert not any("steamapps" in {part.casefold() for part in path.parts} for path in roots)
    assert not any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))
    source = saved_hashes(original)
    revisions = [qualify_release(game, None) for game in games]
    assert len({release.manifest_sha256 for release in revisions}) == 1
    work.mkdir()
    case = work / f"portable-title-{uuid.uuid4().hex[:8]}"
    print(case, flush=True)
    seats, processes, proof = [], [], {"verified": False}
    with ExitStack() as probes:
        try:
            ports = (*choose_ports(), 0)
            for name, game, release, port in zip(("first", "second", "third"), games, revisions, ports, strict=True):
                probes.enter_context(host_npc_combat_overlay(game))
                workspace = case / name
                title = workspace / release.title_directory
                profile = title / "game-user"
                saves = profile / "Saved/SaveGames"
                saves.mkdir(parents=True)
                for saved in (original / "SaveGames").glob("*.sav"):
                    shutil.copy2(saved, saves / saved.name)
                baseline = stock_save_hashes(profile)
                baseline.pop("Config.sav")
                process = launch_portable(game, profile)
                processes.append(process)
                mailbox = await discover_mailbox(title / "run/game-bridge", process)
                assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
                seat = Seat(profile, workspace / "campaigns" / f"title-host-settings-{uuid.uuid4().hex[:8]}",
                            workspace / "clients" / title.name, port, runtime.Bridge(mailbox, process))
                seats.append((seat, baseline))
                await offered(seat)
            host, first, second = (seat for seat, _ in seats)

            async def check(host, guest, pawn):
                proof["melee"] = await fight(host, guest, second, pawn)
                return {}

            proof["session"] = await start_pair(host, first, "First melee scout", on_join=check,
                wait_status=wait_resident_status, host_exit_actions=("prompt", "confirm"))
            await offered(second)
            for seat, baseline in seats:
                observed = stock_save_hashes(seat.profile)
                observed.pop("Config.sav")
                assert observed == baseline
            assert saved_hashes(original) == source
            proof["verified"] = True
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    await runtime.stop_game(process)
            proof["game_executable_launches"] = len(processes)
            proof["processes_closed"] = len(processes) == 3 and all(process.poll() is not None for process in processes)
            proof["original_saves_verified"] = saved_hashes(original) == source
            proof["runtime_manifest_sha256"] = revisions[0].manifest_sha256
            runtime.write_json(work / "shared-melee.json", proof)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--game", type=Path, nargs=3, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(), tuple(game.resolve(strict=True) for game in args.game),
                       args.vanilla_saved.resolve(strict=True)))
