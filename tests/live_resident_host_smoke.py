"""Start and resume real co-op hosting in the same running stock title process."""

import argparse
import asyncio
from contextlib import suppress
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import embedded_startup, embedded_title_plan, native_host_save, native_title_entry as title, runtime
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import select_run, submit
from tools.silverfish.service import MENU_HOST_OPTIONS, _host_stage_options, verify_travel_checkpoint
from tools.silverfish.title_handoff import _isolated_stock_title
from tools.silverfish.title_session import STATUS_FILE
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.udp_ownership import owns_udp_port
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_native_runtime_rejoin_smoke import _events
from live_same_process_listener_smoke import choose_ports
from live_title_handoff_smoke import title_choice


async def verify(work: Path, original: Path) -> None:
    originals = saved_hashes(original)
    if ("steamapps" in {part.casefold() for part in work.parts} or work.is_relative_to(original)
            or original.is_relative_to(work) or not originals or not embedded_title_plan.installed(work)):
        raise ValueError("Use a separate replica with the matching embedded package and original saves for comparison")
    name = "title-host-settings-" + uuid.uuid4().hex[:8]
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    campaign = select_run(work, "campaigns", name)
    guest = select_run(work, "clients", name)
    profile = campaign / "game-user"
    nonce = uuid.uuid4().hex
    host_run, guest_run = campaign / "run", guest / "run"
    mailbox = host_run / "game-bridge" / nonce
    mailbox.mkdir(parents=True)
    default_port, edited_port = choose_ports()
    runtime.write_json(mailbox / embedded_startup.RESIDENT_FILE, {"schema": 1, "bind": "127.0.0.1", "port": default_port})
    options = MENU_HOST_OPTIONS | _host_stage_options(work, True, True)
    options["host_dig_live"] = runtime.staged_script(work, "native_host_dig_live.lua", "SilverfishRenewNativeHostDigScope")
    process = runtime.launch(work, profile, mailbox, True, embedded_engine=True, embedded_startup=nonce,
                             title_fixture=True, resident_title=True, **options)
    bridge = runtime.Bridge(mailbox, process)
    children, tasks = [], []
    evidence = {"verified": False, "title_pid": process.pid, "sessions": []}

    async def offer():
        frame = await bridge.wait_for(lambda game: _isolated_stock_title(game, profile / "Saved")
                                     and game["save_scope"] is None
                                     and game.get(title.PANEL_KEY, {}).get("state") == "ready")
        assert frame[title.PANEL_KEY]["actions"] == ["host"]
        return frame

    async def hosting(port):
        state = await wait(host_run, lambda value: value.get("state") == "hosting"
                           and value.get("endpoint", {}).get("port") == port)
        assert state["pid"] == process.pid and process.poll() is None
        assert state["game"]["map"] == OBSERVATORY and owns_udp_port(process.pid, port)
        async with asyncio.timeout(10):
            while True:
                status = runtime.read_json(mailbox / STATUS_FILE)
                assert status["last_failure"] is None, status
                if status["state"] == "ready":
                    return state, status["attempt"]
                await asyncio.sleep(.1)

    async def stop_host(port):
        assert (await submit(host_run, "stop"))["ok"]
        stopped = await wait(host_run, lambda value: value.get("state") == "stopped")
        await offer()
        assert stopped["pid"] == process.pid and process.poll() is None and not owns_udp_port(process.pid, port)
        return CampaignStore.read_existing(
            campaign / "save", verify_world=verify_travel_checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data),
        )

    try:
        await offer()
        await bridge.command("native-title-panel-press-fixture", "host")
        first, first_attempt = await hosting(default_port)
        scope = first["game"]["save_scope"]
        assert scope == uuid.UUID(first["campaign"]["campaign_id"]).hex
        invitation = await submit(host_run, "invite", name="Local guest", character="Resident Scout")
        guest_plan = embedded_title_plan.guest_plan(work, guest, Path(invitation["invite_file"]), None, headless=True)
        await title_choice(work, case, "join", guest_plan, children, tasks)
        await wait(guest_run, lambda value: value.get("state") == "connected")
        paired = await wait(host_run, lambda value: value.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        pawn = paired["game"]["native_guest_save"]["pawn"]
        assert (await submit(guest_run, "drop-link"))["ok"]
        await wait(host_run, lambda value: any(
            row.get("character_id") == invitation["character_id"] and row.get("state") == "reserved"
            for row in value.get("campaign", {}).get("leases", ())))
        assert (await submit(guest_run, "reconnect"))["resumed"]
        rejoined = await wait(host_run, lambda value: any(
            row.get("character_id") == invitation["character_id"] and row.get("state") == "active"
            for row in value.get("campaign", {}).get("leases", ())))
        assert rejoined["game"]["native_guest_save"]["pawn"] == pawn
        assert (await submit(guest_run, "stop"))["ok"]
        await asyncio.wait_for(tasks[-1], 15)
        checkpoint = await stop_host(default_port)
        save = checkpoint["world"]["native_host_save"]
        assert checkpoint["characters"][invitation["character_id"]]["state"]["native_guest_save"]
        evidence["sessions"].append({"pid": first["pid"], "port": default_port, "revision": checkpoint["revision"],
                                     "guest_reconnected": True, "scope_released": True})

        await bridge.command("native-title-host-form-fixture", "open")
        await bridge.command("native-title-host-form-fixture", "campaign", name)
        await bridge.command("native-title-host-form-fixture", "port", str(edited_port))
        await bridge.command("native-title-host-form-fixture", "submit")
        second, second_attempt = await hosting(edited_port)
        assert first_attempt != second_attempt and first["generation"] != second["generation"]
        assert second["game"]["save_scope"] == scope
        assert any(row["event"] == "native_host_save_restored" and row["slot"] == save["slot"]
                   and row["generation"] == second["generation"] for row in _events(host_run))
        final = await stop_host(edited_port)
        assert final["revision"] > checkpoint["revision"] and final["world"][native_host_save.SCOPE_FIELD] == scope
        evidence["sessions"].append({"pid": second["pid"], "port": edited_port, "revision": final["revision"],
                                     "restored_slot": save["slot"], "scope_released": True})
        evidence["verified"] = True
    finally:
        for task in reversed(tasks):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, OSError, RuntimeError, ValueError, TimeoutError):
                    await task
        for child in reversed(children):
            if child.poll() is None:
                await runtime.stop_game(child)
        if process.poll() is None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] &= evidence["original_saves_verified"]
        runtime.write_json(case / "resident-host-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
