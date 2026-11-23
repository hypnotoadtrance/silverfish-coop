"""Switch both staged stock title processes between Host and Join."""

import argparse
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import embedded_startup, embedded_title_plan, native_host_save, native_title_entry, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import submit
from tools.silverfish.title_handoff import _isolated_stock_title
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.udp_ownership import owns_udp_port, verify_loopback_socket
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_stock_pause_exit_smoke import stock_pause


@dataclass
class Seat:
    profile: Path
    campaign: Path
    client: Path
    port: int
    bridge: runtime.Bridge


async def offered(seat):
    return await seat.bridge.wait_for(lambda game: _isolated_stock_title(game, seat.profile / "Saved")
                                      and game["save_scope"] is None
                                      and game.get(native_title_entry.PANEL_KEY, {}).get("state") == "ready"
                                      and game[native_title_entry.PANEL_KEY]["actions"] == ["host", "join"])


async def start_pair(host, guest, character_name, *, on_join=None, create_character=None,
                     before_join=None, after_reconnect=None, wait_status=wait,
                     host_exit_actions=("save_and_quit",), after_exit=None):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_pid, guest_pid = host.bridge.process.pid, guest.bridge.process.pid
    host_form = "native-title-host-form-fixture"
    await host.bridge.command(host_form, "open")
    await host.bridge.command(host_form, "campaign", host.campaign.name)
    await host.bridge.command(host_form, "port", str(host.port))
    await host.bridge.command(host_form, "submit")
    hosted = await wait_status(host_run, lambda frame: frame.get("state") == "hosting")
    assert hosted["pid"] == host_pid
    verify_loopback_socket(host_pid, host.port)
    assert hosted["game"]["save_scope"] == uuid.UUID(hosted["campaign"]["campaign_id"]).hex

    async def join(*args):
        return await guest.bridge.command("native-title-join-form-fixture", *args)

    async def screen(expected):
        async with asyncio.timeout(22):
            while (await join("observe"))["ack"]["message"] != "title_join_form_state=" + expected:
                await asyncio.sleep(.1)

    await join("open")
    await join("address", "127.0.0.1")
    await join("port", str(host.port))
    await join("no_password")
    await screen("trust_host")
    await guest.bridge.command("native-title-button-fixture", "trust")
    await screen("catalog;count=0")
    if create_character is None:
        await join("create", character_name)
    else:
        await create_character(guest, join, character_name)
    await screen("catalog;count=1")
    checkpoint = CampaignStore.read_existing(host.campaign / "save", verify_world=service.verify_travel_checkpoint)
    character, = checkpoint["characters"]
    await join("select", character)
    if before_join:
        await before_join(host, guest, character)
    await join("submit")
    joined = await wait_status(guest_run, lambda frame: frame.get("state") == "connected" and frame.get("gameplay_fresh") is True)
    assert joined["pid"] == guest_pid and joined["character_id"] == character and joined["game"]["map"] == OBSERVATORY
    assert joined["game"]["save_scope"] == uuid.UUID(character).hex
    verify_loopback_socket(guest_pid, joined["unreal_local_port"])

    paired = await wait_status(host_run, lambda frame: frame.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
    pawn = paired["game"]["native_guest_save"]["pawn"]
    gameplay = await on_join(host, guest, pawn) if on_join else {}
    assert (await submit(guest_run, "drop-link"))["ok"]
    await wait_status(host_run, lambda frame: any(row["character_id"] == character and row["state"] == "reserved"
                                                for row in frame.get("campaign", {}).get("leases", ())))
    reconnect_started = time.time()
    assert (await submit(guest_run, "reconnect"))["resumed"]
    recovered = await wait_status(host_run, lambda frame: any(row["character_id"] == character and row["state"] == "active"
                                                             for row in frame.get("campaign", {}).get("leases", ())))
    assert recovered["game"]["native_guest_save"]["pawn"] == pawn
    if after_reconnect:
        await after_reconnect(guest, reconnect_started)

    await host.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
    await stock_pause(host.bridge, "open")
    for action in host_exit_actions:
        await stock_pause(host.bridge, action)
    stopped = {}
    for run in (host_run, guest_run):
        stopped[run] = await wait_status(run, lambda frame: frame.get("state") == "stopped")
        assert stopped[run]["stop_reason"] == "host_session_ended"
    await asyncio.gather(offered(host), offered(guest))
    assert not owns_udp_port(host_pid, host.port) and not owns_udp_port(guest_pid, joined["unreal_local_port"])

    saved = CampaignStore.read_existing(
        host.campaign / "save", verify_world=service.verify_travel_checkpoint,
        verify_checkpoint=lambda data: native_host_save.verify_checkpoint(host.profile, data),
    )
    assert saved["characters"][character]["state"]["native_guest_save"]
    if after_exit:
        await after_exit(host, stopped[host_run], saved, character)
    return {"host_pid": host_pid, "guest_pid": guest_pid, "campaign_id": saved["campaign_id"],
            "revision": saved["revision"], "character_id": character, "same_pawn_after_reconnect": True,
            "title_return_and_sockets_closed": True, **gameplay}


async def verify(work, original, *, lean=False):
    before = saved_hashes(original)
    if ("steamapps" in {part.casefold() for part in work.parts} or work.is_relative_to(original)
            or original.is_relative_to(work) or not before):
        raise ValueError("Use the qualified private game replica and the original save folder")
    if lean:
        from live_portable_title_smoke import qualify_release

        qualify_release(work / "stage", None)
    elif not embedded_title_plan.installed(work):
        raise ValueError("The private game replica is missing the staged embedded runtime")
    case = work / "title-probes" / ("unified-" + uuid.uuid4().hex[:8])
    case.mkdir(parents=True)
    print(case, flush=True)
    seats, baselines = [], []
    proof = {"verified": False, "sessions": []}

    try:
        options = service.unified_title_options(work)
        for port in choose_ports():
            name = "title-host-settings-" + uuid.uuid4().hex[:8]
            title = work / "titles" / name
            profile, nonce = title / "game-user", uuid.uuid4().hex
            mailbox = title / "run" / "game-bridge" / nonce
            mailbox.mkdir(parents=True)
            runtime.write_json(mailbox / embedded_startup.UNIFIED_FILE,
                               {"schema": 1, "campaign": name, "bind": "127.0.0.1", "port": port})
            stock = profile / "Saved" / "SaveGames"
            stock.mkdir(parents=True)
            for file in (original / "SaveGames").glob("*.sav"):
                shutil.copy2(file, stock / file.name)
            files = stock_save_hashes(profile)
            assert "Config.sav" in files and len(files) >= 2
            del files["Config.sav"]
            process = runtime.launch(work, profile, mailbox, True, embedded_engine=True,
                                     embedded_startup=nonce, resident_title=True, resident_direct_join=True,
                                     unified_title=True, title_fixture=True, pause_fixture=True, **options)
            seat = Seat(profile, work / "campaigns" / name, work / "clients" / name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            baselines.append((files, await stock_profile(seat.bridge)))

        first, second = seats
        if lean:
            from avatar_lean_probe import verify_lean

            proof["sessions"].append(await start_pair(first, second, "Lean test scout", on_join=verify_lean))
        else:
            proof["sessions"].append(await start_pair(first, second, "Second seat scout"))
            proof["sessions"].append(await start_pair(second, first, "First seat scout"))
            assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
            assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
            assert len({row["campaign_id"] for row in proof["sessions"]}) == 2
        for seat, (files, preferences) in zip(seats, baselines, strict=True):
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == files and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        proof.update(verified=True, solo_slots_and_settings_preserved=True, both_titles_reused=not lean)
    finally:
        for seat in reversed(seats):
            if seat.bridge.process.poll() is None:
                await runtime.stop_game(seat.bridge.process)
        proof["original_saves_verified"] = saved_hashes(original) == before
        proof["verified"] &= proof["original_saves_verified"]
        runtime.write_json(case / "unified-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--lean", action="store_true", help="verify stock lean input and peer presentation in one muted pair")
    args = parser.parse_args()
    work, original = args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)
    if args.lean:
        from avatar_lean_probe import installed_probe

        os.environ.pop("SILVERFISH_COOP_AVATAR_LEAN", None)
        os.environ["SILVERFISH_COOP_AVATAR_LEAN_PROBE"] = "1"
        with installed_probe(work / "stage"):
            asyncio.run(verify(work, original, lean=True))
    else:
        asyncio.run(verify(work, original))
