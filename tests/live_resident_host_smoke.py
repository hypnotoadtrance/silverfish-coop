"""Host and join real co-op sessions in the original staged title processes."""

import argparse
import asyncio
from hashlib import sha256
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import embedded_startup, embedded_title_plan, native_host_save, native_title_entry as title, runtime
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import select_run, submit
from tools.silverfish.service import (
    MENU_HOST_OPTIONS, _host_stage_options, guest_engine_options, load_invite, verify_travel_checkpoint,
)
from tools.silverfish.title_handoff import _isolated_stock_title
from tools.silverfish.title_session import STATUS_FILE
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.udp_ownership import owns_udp_port
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_native_runtime_rejoin_smoke import _events
from live_same_process_listener_smoke import choose_ports
from live_stock_pause_exit_smoke import paired_checkpoint, stock_pause


async def verify(work: Path, original: Path, *, direct: bool = False) -> None:
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
                             title_fixture=True, pause_fixture=True, resident_title=True, **options)
    bridge = runtime.Bridge(mailbox, process)
    guest_process = None
    evidence = {"verified": False, "title_pid": process.pid, "direct_ip": direct, "sessions": []}

    async def offer(game_bridge, game_profile, action):
        frame = await game_bridge.wait_for(lambda game: _isolated_stock_title(game, game_profile / "Saved")
                                          and game["save_scope"] is None
                                          and game.get(title.PANEL_KEY, {}).get("state") == "ready")
        assert frame[title.PANEL_KEY]["actions"] == [action]
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

    async def stop_host(port, *, pause_exit=False):
        if pause_exit:
            await stock_pause(bridge, "open")
            await stock_pause(bridge, "save_and_quit")
        else:
            assert (await submit(host_run, "stop"))["ok"]
        stopped = await wait(host_run, lambda value: value.get("state") == "stopped")
        await offer(bridge, profile, "host")
        assert stopped["pid"] == process.pid and process.poll() is None and not owns_udp_port(process.pid, port)
        return CampaignStore.read_existing(
            campaign / "save", verify_world=verify_travel_checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data),
        )

    try:
        await offer(bridge, profile, "host")
        if direct:
            for operation in ("open", "next", "next", "next", "password", "submit"):
                await bridge.command("native-title-host-form-fixture", operation)
        else:
            await bridge.command("native-title-panel-press-fixture", "host")
        first, first_attempt = await hosting(default_port)
        scope = first["game"]["save_scope"]
        assert scope == uuid.UUID(first["campaign"]["campaign_id"]).hex
        guest_nonce = uuid.uuid4().hex
        guest_mailbox = guest_run / "game-bridge" / guest_nonce
        guest_profile = guest / "game-user"
        guest_mailbox.mkdir(parents=True)
        if direct:
            # Start with the standard full native stage before the operator knows
            # the host address. The resident worker checks the authenticated host.
            guest_options = guest_engine_options(work, {
                "native_zones": True, "native_zone_walls": True, "expanded_zones": True,
                "native_guest_controls": True, "native_wipe_view": True, "native_loot_input": True,
                "native_broker_input": True, "native_mosby_station": True, "native_mindcore_view": True,
            })
            startup = {"schema": 1, "direct": True}
        else:
            invitation = await submit(host_run, "invite", name="Local guest", character="Resident Scout")
            character = invitation["character_id"]
            source = Path(invitation["invite_file"])
            guest_options = guest_engine_options(work, load_invite(source, runtime.staged_build(work)))
            raw_invitation = source.read_bytes()
            (guest_mailbox / embedded_startup.INVITE_FILE).write_bytes(raw_invitation)
            startup = {"schema": 1, "selection": {
                "invite_sha256": sha256(raw_invitation).hexdigest(), "character_id": character}}
        runtime.write_json(guest_mailbox / embedded_startup.RESIDENT_JOIN_FILE, startup)
        guest_process = runtime.launch(work, guest_profile, guest_mailbox, True, embedded_engine=True,
                                       embedded_startup=guest_nonce, resident_title=True, title_fixture=True,
                                       pause_fixture=True, resident_direct_join=direct, **guest_options)
        guest_bridge = runtime.Bridge(guest_mailbox, guest_process)

        async def form(operation, *args):
            return await guest_bridge.command("native-title-join-form-fixture", operation, *args)

        async def form_state(expected):
            async with asyncio.timeout(22):
                while True:
                    frame = await form("observe")
                    if frame["ack"]["message"] == "title_join_form_state=" + expected:
                        return
                    await asyncio.sleep(.1)

        async def open_direct():
            await form("open")
            await form("address", "127.0.0.1")
            await form("port", str(default_port))

        await offer(guest_bridge, guest_profile, "join")
        if direct:
            await open_direct()
            await form("no_password")
            await form_state("password;error=direct_password_required")
            await form("wrong_password")
            await form_state("password;error=direct_wrong_password")
            await form("password")
            await form_state("catalog;count=0")
            await form("create", "Resident Scout")
            await form_state("catalog;count=1")
            current = CampaignStore.read_existing(campaign / "save", verify_world=verify_travel_checkpoint)
            character, = current["characters"]
            await form("select", character)
            await form_state("confirm")
            await form("submit")
            assert not (guest_mailbox / embedded_startup.INVITE_FILE).exists()
            assert not (guest_mailbox / "title-join-imports").exists()
            assert not tuple((host_run / "invites").glob("*.invite.json"))
            assert not (guest_mailbox / "title-join-request.tsv").exists()
            assert not (mailbox / "title-handoff-request.tsv").exists()
            evidence["direct_title"] = {"required_password_refused": True, "wrong_password_refused": True,
                                        "created_owned_character": character, "used_invitation": False}
        else:
            await guest_bridge.command("native-title-panel-press-fixture", "join")
        joined = await wait(guest_run, lambda value: value.get("state") == "connected")
        guest_scope = uuid.UUID(character).hex
        assert joined["pid"] == guest_process.pid and joined["game"]["save_scope"] == guest_scope
        paired = await wait(host_run, lambda value: value.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        pawn = paired["game"]["native_guest_save"]["pawn"]
        assert (await submit(guest_run, "drop-link"))["ok"]
        await wait(host_run, lambda value: any(
            row.get("character_id") == character and row.get("state") == "reserved"
            for row in value.get("campaign", {}).get("leases", ())))
        assert (await submit(guest_run, "reconnect"))["resumed"]
        rejoined = await wait(host_run, lambda value: any(
            row.get("character_id") == character and row.get("state") == "active"
            for row in value.get("campaign", {}).get("leases", ())))
        assert rejoined["game"]["native_guest_save"]["pawn"] == pawn
        for operation in ("open", "prompt", "confirm"):
            await stock_pause(guest_bridge, operation)
        guest_stopped = await wait(guest_run, lambda value: value.get("state") == "stopped")
        assert guest_stopped["pid"] == guest_process.pid and guest_stopped["stop_reason"] == "guest_left"
        await offer(guest_bridge, guest_profile, "join")
        await wait(host_run, lambda value: value.get("state") == "hosting" and not value["campaign"]["leases"])
        detached = paired_checkpoint(campaign, character)["guest_save"]["slot"]
        if direct:
            await open_direct()
            await form("password")
            await form_state("catalog;count=1")
            await form("select", character)
            await form("submit")
        else:
            await guest_bridge.command("native-title-panel-press-fixture", "join")
        joined_again = await wait(guest_run, lambda value: value.get("state") == "connected"
                                  and value.get("generation") != joined["generation"])
        assert joined_again["pid"] == guest_process.pid and joined_again["game"]["save_scope"] == guest_scope
        assert joined_again["character_id"] == character and guest_process.poll() is None
        await wait(host_run, lambda value: any(row.get("character_id") == character and row.get("state") == "active"
                                              for row in value.get("campaign", {}).get("leases", ())))
        assert any(row["event"] == "native_guest_save_restored" and row["slot"] == detached
                   and row["character_id"] == character for row in _events(host_run))
        checkpoint = await stop_host(default_port, pause_exit=True)
        guest_final = await wait(guest_run, lambda value: value.get("state") == "stopped")
        assert guest_final["pid"] == guest_process.pid and guest_final["stop_reason"] == "host_session_ended"
        await offer(guest_bridge, guest_profile, "join")
        assert guest_process.poll() is None
        evidence["guest"] = {"title_pid": guest_process.pid, "joined_twice": True,
                             "reconnected_to_original_pawn": True, "restored_slot": detached,
                             "scope_released_after_host_stop": True}
        save = checkpoint["world"]["native_host_save"]
        assert checkpoint["characters"][character]["state"]["native_guest_save"]
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
        if guest_process is not None and guest_process.poll() is None:
            await runtime.stop_game(guest_process)
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
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True), direct=args.direct))
