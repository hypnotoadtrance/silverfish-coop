"""Host and join real co-op sessions in the original staged title processes."""

import argparse
import asyncio
from hashlib import file_digest, sha256
from pathlib import Path
import shutil
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
from tools.silverfish.udp_ownership import owns_udp_port, verify_loopback_socket
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_native_runtime_rejoin_smoke import _events
from live_same_process_listener_smoke import choose_ports
from live_stock_pause_exit_smoke import paired_checkpoint, stock_pause


def stock_save_hashes(profile: Path) -> dict[str, str]:
    fingerprints = {}
    for path in (profile / "Saved" / "SaveGames").glob("*.sav"):
        with path.open("rb") as stream:
            fingerprints[path.name] = file_digest(stream, "sha256").hexdigest()
    return fingerprints


async def stock_profile(bridge):
    frame = await bridge.command("native-title-stock-profile-fixture")
    return frame["ack"]["message"]


async def verify(work: Path, original: Path, *, direct: bool = False, title_profiles: bool = False) -> None:
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
    profile = work / "titles" / f"{name}-host" / "game-user" if title_profiles else campaign / "game-user"
    guest_profile = work / "titles" / f"{name}-guest" / "game-user" if title_profiles else guest / "game-user"
    solo_baselines = {}
    if title_profiles:
        for seat in (profile, guest_profile):
            saves = seat / "Saved" / "SaveGames"
            saves.mkdir(parents=True)
            for stock_save in (original / "SaveGames").glob("*.sav"):
                shutil.copy2(stock_save, saves / stock_save.name)
            solo_baselines[seat] = stock_save_hashes(seat)
            assert "Config.sav" in solo_baselines[seat] and len(solo_baselines[seat]) > 1
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
    evidence = {"verified": False, "title_pid": process.pid, "direct_ip": direct,
                "independent_title_profiles": title_profiles, "sessions": []}

    async def offer(game_bridge, game_profile, action):
        frame = await game_bridge.wait_for(lambda game: _isolated_stock_title(game, game_profile / "Saved")
                                          and game["save_scope"] is None
                                          and game.get(title.PANEL_KEY, {}).get("state") == "ready")
        assert frame[title.PANEL_KEY]["actions"] == [action]
        return frame

    async def hosting(port, run=host_run):
        state = await wait(run, lambda value: value.get("state") == "hosting"
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

    async def stop_host(port, *, pause_exit=False, owner=campaign):
        run = owner / "run"
        if pause_exit:
            await stock_pause(bridge, "open")
            await stock_pause(bridge, "save_and_quit")
        else:
            assert (await submit(run, "stop"))["ok"]
        stopped = await wait(run, lambda value: value.get("state") == "stopped")
        await offer(bridge, profile, "host")
        assert stopped["pid"] == process.pid and process.poll() is None and not owns_udp_port(process.pid, port)
        return CampaignStore.read_existing(
            owner / "save", verify_world=verify_travel_checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data),
        )

    try:
        await offer(bridge, profile, "host")
        if title_profiles:
            stock_host = await stock_profile(bridge)
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
        assert "-multihome" not in guest_process.args.casefold() and "-port=" not in guest_process.args.casefold()
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

        async def open_direct(port=default_port):
            await form("open")
            await form("address", "127.0.0.1")
            await form("port", str(port))

        await offer(guest_bridge, guest_profile, "join")
        if title_profiles:
            stock_guest = await stock_profile(guest_bridge)
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
        first_guest_port = joined["unreal_local_port"]
        verify_loopback_socket(guest_process.pid, first_guest_port)
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
        verify_loopback_socket(guest_process.pid, first_guest_port)
        await guest_bridge.wait_for(lambda frame: frame.get("stock_pause_exit", {}).get("state") == "armed")
        for operation in ("open", "prompt", "confirm"):
            await stock_pause(guest_bridge, operation)
        guest_stopped = await wait(guest_run, lambda value: value.get("state") == "stopped")
        assert guest_stopped["pid"] == guest_process.pid and guest_stopped["stop_reason"] == "guest_left"
        await offer(guest_bridge, guest_profile, "join")
        assert not owns_udp_port(guest_process.pid, first_guest_port)
        await wait(host_run, lambda value: value.get("state") == "hosting" and not value["campaign"]["leases"])
        detached = paired_checkpoint(campaign, character, profile=profile)["guest_save"]["slot"]
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
        second_guest_port = joined_again["unreal_local_port"]
        verify_loopback_socket(guest_process.pid, second_guest_port)
        await wait(host_run, lambda value: any(row.get("character_id") == character and row.get("state") == "active"
                                              for row in value.get("campaign", {}).get("leases", ())))
        assert any(row["event"] == "native_guest_save_restored" and row["slot"] == detached
                   and row["character_id"] == character for row in _events(host_run))
        checkpoint = await stop_host(default_port, pause_exit=True)
        guest_final = await wait(guest_run, lambda value: value.get("state") == "stopped")
        assert guest_final["pid"] == guest_process.pid and guest_final["stop_reason"] == "host_session_ended"
        await offer(guest_bridge, guest_profile, "join")
        assert guest_process.poll() is None and not owns_udp_port(guest_process.pid, second_guest_port)
        evidence["guest"] = {"title_pid": guest_process.pid, "joined_twice": True,
                             "reconnected_to_original_pawn": True, "restored_slot": detached,
                             "runtime_loopback_ports": [first_guest_port, second_guest_port],
                             "loopback_ports_released": True, "no_startup_bind_or_port": True,
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
        if direct:
            await open_direct(edited_port)
            await form("no_password")
            await form_state("catalog;count=1")
            await form("select", character)
            await form("submit")
            open_join = await wait(guest_run, lambda value: value.get("state") == "connected"
                                   and value.get("generation") != joined_again["generation"])
            assert open_join["character_id"] == character and open_join["pid"] == guest_process.pid
            evidence["direct_title"]["passwordless_rejoin_on_edited_port"] = True
        final = await stop_host(edited_port)
        assert final["revision"] > checkpoint["revision"] and final["world"][native_host_save.SCOPE_FIELD] == scope
        evidence["sessions"].append({"pid": second["pid"], "port": edited_port, "revision": final["revision"],
                                     "restored_slot": save["slot"], "scope_released": True})
        if title_profiles:
            other_name = "title-host-settings-" + uuid.uuid4().hex[:8]
            other = select_run(work, "campaigns", other_name)
            await bridge.command("native-title-host-form-fixture", "open")
            await bridge.command("native-title-host-form-fixture", "campaign", other_name)
            await bridge.command("native-title-host-form-fixture", "port", str(edited_port))
            await bridge.command("native-title-host-form-fixture", "submit")
            other_host, _ = await hosting(edited_port, other / "run")
            other_scope_id = other_host["game"]["save_scope"]
            assert other_scope_id != scope
            other_checkpoint = await stop_host(edited_port, owner=other)
            assert other_checkpoint["world"][native_host_save.SCOPE_FIELD] == other_scope_id
            assert CampaignStore.read_existing(campaign / "save", verify_checkpoint=lambda data:
                                               native_host_save.verify_checkpoint(profile, data)) == final

            host_scope = native_host_save.checkpoint_profile(profile, final)
            guest_scope_directory = native_host_save.directory(native_host_save.SaveScope(guest_profile, guest_scope))
            assert native_host_save.directory(host_scope).is_dir() and guest_scope_directory.is_dir()
            assert not any((owner / "game-user").exists() for owner in (campaign, guest, other))
            await offer(guest_bridge, guest_profile, "join")
            assert await stock_profile(bridge) == stock_host and await stock_profile(guest_bridge) == stock_guest
            evidence["separate_title_and_ledger"] = True
            evidence["separate_host_campaigns"] = [scope, other_scope_id]
            evidence["stock_preferences_and_continue_preserved"] = True
        evidence["verified"] = True
    finally:
        if guest_process is not None and guest_process.poll() is None:
            await runtime.stop_game(guest_process)
        if process.poll() is None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        if title_profiles:
            evidence["stock_slots_preserved"] = {}
            evidence["stock_config_rewritten"] = {}
            for seat, baseline in solo_baselines.items():
                observed = stock_save_hashes(seat)
                evidence["stock_config_rewritten"][seat.parent.name] = (
                    observed.pop("Config.sav", None) != baseline.pop("Config.sav"))
                evidence["stock_slots_preserved"][seat.parent.name] = observed == baseline
            evidence["verified"] &= all(evidence["stock_slots_preserved"].values())
        evidence["verified"] &= evidence["original_saves_verified"]
        runtime.write_json(case / "resident-host-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original player saves changed"
        if title_profiles:
            assert all(evidence["stock_slots_preserved"].values()), "The isolated stock slots or settings changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--title-profiles", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True),
                       direct=args.direct, title_profiles=args.title_profiles))
