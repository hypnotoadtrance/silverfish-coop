"""Start real Direct IP host and guest sessions through the staged title panel."""

import argparse
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import embedded_startup, embedded_title_plan, native_host_save, native_title_entry as title, runtime
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import select_run, submit
from tools.silverfish.native_roster import _census
from tools.silverfish.service import NATIVE_RUNTIME_RETIRING, verify_travel_checkpoint
from tools.silverfish.title_session import STATUS_FILE, TitleSession, host_plan, join_plan
from tools.silverfish.travel import OBSERVATORY
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from live_native_runtime_rejoin_smoke import (
    ObserverContinuity, _client_pawn, _events, _identity, _member, _same_visit, _saved_guest,
)
from live_reconnect_grace_smoke import lease, status


async def title_choice(work, case, action, plan, processes, tasks, *, timeout=65, observe=None):
    home = case / action
    profile = home / "game-user"
    process = runtime.launch(work, profile, home / "game-bridge", headless=True, title_fixture=True,
                             inspect_title_profile=True)
    processes.append(process)
    bridge = runtime.Bridge(home / "game-bridge", process)
    stock = await bridge.wait_for(lambda game: game.get(title.KEY, {}).get("state") == "ready", timeout=30)
    retired, isolated_ready = [], []
    title_ready = asyncio.Event()

    def report_title_ready():
        snapshot = bridge.snapshot()
        assert process.poll() is None and snapshot[title.KEY] == stock[title.KEY]
        receipt = snapshot[title.STARTUP_KEY]
        assert Path(receipt["saved_directory"]).resolve(strict=True) == (profile / "Saved").resolve(strict=True)
        isolated_ready.append(receipt)
        title_ready.set()

    async def retire_title():
        assert process.poll() is None
        assert bridge.snapshot()[title.KEY] == stock[title.KEY]
        retired.append(plan.read_status()["state"])
        await runtime.stop_game(process)

    supervisor = TitleSession(bridge, {action: plan}, retire_title=retire_title, on_title_ready=report_title_ready,
                              title_saved_directory=profile / "Saved")
    tasks.append(asyncio.create_task(supervisor.run()))
    await asyncio.wait_for(title_ready.wait(), timeout=8)
    shown = await bridge.wait_for(lambda game: game.get(title.PANEL_KEY, {}).get("state") == "ready", timeout=8)
    panel = shown[title.PANEL_KEY]
    assert panel["actions"] == [action] and panel["mounted"], panel
    different = "join" if action == "host" else "host"
    try:
        await bridge.command("native-title-panel-press-fixture", different, timeout=4)
    except RuntimeError as error:
        assert str(error) == "title_action_not_offered", error
    else:
        raise AssertionError("An unoffered title action was accepted")
    accepted = await bridge.command("native-title-panel-press-fixture", action, timeout=4)
    assert accepted["ack"]["message"] == "qualified staged title control dispatched", accepted["ack"]
    expected = "hosting" if action == "host" else "connected"
    async with asyncio.timeout(timeout):
        while True:
            report = runtime.read_json(bridge.directory / STATUS_FILE)
            assert not report.get("last_failure"), report
            if observe is not None:
                observe(report, process)
            if report.get("state") == "ready":
                assert report["attempt"]["action"] == action and report["progress"] == expected, report
                assert retired == [expected] and process.poll() is not None, retired
                assert len(isolated_ready) == 1
                return {"stock_title": stock[title.KEY], "panel": panel, "action": action, "handoff": report["progress"],
                        "attempt": report["attempt"], "isolated_title_ready": isolated_ready[0]}
            await asyncio.sleep(.1)


async def verify(work: Path, original: Path, *, embedded: bool = False, abrupt_rejoin: bool = False,
                 multiparty: bool = False) -> None:
    if os.name != "nt":
        raise OSError("The co-op title handoff requires a staged Windows game")
    if (abrupt_rejoin or multiparty) and not embedded:
        raise ValueError("The selected title scenario requires the matched embedded worker")
    if abrupt_rejoin and multiparty:
        raise ValueError("Select one embedded title scenario")
    if embedded and not embedded_title_plan.installed(work):
        raise ValueError("The matched staged embedded runtime package is missing")
    initial = saved_hashes(original)
    if not initial or work.is_relative_to(original) or original.is_relative_to(work):
        raise ValueError("The original Saved directory must exist outside the isolated workspace")
    name = "title-handoff-" + uuid.uuid4().hex[:8]
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    campaign, profile = select_run(work, "campaigns", name), select_run(work, "clients", name)
    host_run, guest_run = campaign / "run", profile / "run"
    second_profile = select_run(work, "clients", name + "-second") if multiparty else None
    guest_profiles = {profile / "game-user": profile}
    if second_profile is not None:
        guest_profiles[second_profile / "game-user"] = second_profile
    processes, tasks = [], []
    evidence = {"verified": False, "build": runtime.staged_build(work), "embedded": embedded}
    guest_games = []
    launch = runtime.launch

    def track_guest(workspace, userdir, mailbox, headless, *args, **kwargs):
        owned_profile = guest_profiles.get(userdir)
        if owned_profile is None:
            return launch(workspace, userdir, mailbox, headless, *args, **kwargs)
        from tools.silverfish.win_job import GameProcess

        if (workspace != work or headless is not True or kwargs.get("embedded_engine") is not True
                or mailbox.parent != owned_profile / "run" / "game-bridge" or mailbox.name != kwargs.get("embedded_startup")):
            raise RuntimeError("The test may only own the headless embedded game for this guest profile")
        process = launch(workspace, userdir, mailbox, headless, *args, **kwargs)
        if not isinstance(process, GameProcess):
            raise RuntimeError("The guest must have a privately owned game process")
        guest_games.append((process, mailbox))
        return process

    async def choose_guest(title_case, plan, **kwargs):
        if abrupt_rejoin or multiparty:
            with patch.object(runtime, "launch", side_effect=track_guest):
                return await title_choice(work, title_case, "join", plan, processes, tasks, **kwargs)
        return await title_choice(work, title_case, "join", plan, processes, tasks, **kwargs)

    def owned_guest(session, handoff, character, directory=profile):
        matches = [(process, mailbox) for process, mailbox in guest_games
                   if process.pid == session.get("pid") and mailbox.parent == directory / "run" / "game-bridge"]
        if len(matches) != 1:
            raise RuntimeError("The Join status does not identify one owned embedded process")
        process, mailbox = matches[0]
        request = embedded_startup.parse_request((mailbox / embedded_startup.CLAIM_FILE).read_bytes())
        attempt = handoff["attempt"]
        if (process.poll() is not None or process.pid != session.get("pid") or process in processes
                or process.pid == evidence["host"]["worker_pid"] or request.role != "client"
                or request.profile != directory.name or request.pid != process.pid or request.game_build != evidence["build"]
                or not isinstance(request.selection, embedded_startup.JoinSelection)
                or request.selection.character_id != character
                or (request.title_generation, request.title_request_id) != (attempt["generation"], attempt["request_id"])
                or not embedded_startup.accepted(mailbox, request)):
            raise RuntimeError("The accepted embedded Join receipt does not identify the exact owned guest process")
        return process, request

    async def add_second_guest(hosting, initial_guest, recovered_host, recovered_guest, first_seat, first_character):
        host = {"generation": hosting["generation"], "epoch": hosting["zone_epoch"], "endpoint": hosting["endpoint"],
                "pawn": hosting["game"]["pawns"]["local"]["name"]}
        first_game, first_request = owned_guest(recovered_guest, evidence["guest_title"], first_character)
        first_local = _client_pawn(initial_guest, hosting, first_character)
        proof = evidence["multiparty"] = {"host_start": "fresh_wanderer", "first_guest_continuity": {}}
        monitor = ObserverContinuity(host, first_character, first_seat, first_local, initial_guest["generation"],
                                     first_game, proof["first_guest_continuity"])
        monitor.check(recovered_host, recovered_guest)
        first_authentications = sum(event.get("event") == "client_link_authenticated" for event in _events(guest_run))

        invitation = await submit(host_run, "invite", name="Second title guest", character="Second Title Scout")
        if invitation.get("ok") is not True or invitation["character_id"] == first_character:
            raise RuntimeError("The second title guest does not have a separate host-owned character")
        second_character = invitation["character_id"]
        second_run = second_profile / "run"
        plan = embedded_title_plan.guest_plan(work, second_profile, Path(invitation["invite_file"]),
                                             second_character, headless=True)
        proof["second_title"] = await choose_guest(
            case / "second", plan, observe=lambda *_: monitor.check(status(host_run), status(guest_run)))
        second = await wait(second_run, lambda peer: _client_pawn(peer, recovered_host, second_character), timeout=15)
        second_local = _client_pawn(second, recovered_host, second_character)
        second_game, second_request = owned_guest(second, proof["second_title"], second_character, second_profile)
        if (len(guest_games) != 2 or second_game.pid == first_game.pid or second["generation"] == initial_guest["generation"]
                or second_request.selection.invite_sha256 == first_request.selection.invite_sha256):
            raise RuntimeError("The second guest did not adopt a separate invitation in an independent embedded process")

        def both_bound(server):
            stable = monitor.check(server, status(guest_run))
            other = _member(server, second_character)
            game = server.get("game", {})
            return (stable and other and other["role"] == "guest2" and first_seat["role"] == "guest"
                    and server.get("pid") == hosting["pid"] and game.get("remote_clients") == 2
                    and other["driver"] == first_seat["driver"]
                    and all(other[key] != first_seat[key] for key in ("connection_id", "connection", "controller", "pawn"))
                    and lease(server, first_character).get("state") == lease(server, second_character).get("state") == "active"
                    and _client_pawn(status(second_run), server, second_character) == second_local)

        authority = await wait(host_run, both_bound, timeout=15)
        other = _member(authority, second_character)
        first_owner = lease(authority, first_character).get("guest_id")
        second_owner = lease(authority, second_character).get("guest_id")
        if not first_owner or second_owner != invitation["guest_id"] or first_owner == second_owner:
            raise RuntimeError("The host did not keep the two guest characters in separate ownership scopes")
        proof["first"] = {"character_id": first_character, "native": first_seat, "pid": first_game.pid,
                          "local_pawn": first_local, "generation": initial_guest["generation"], "owner": first_owner}
        proof["second"] = {"character_id": second_character, "native": other, "pid": second_game.pid,
                           "local_pawn": second_local, "generation": second["generation"], "owner": second_owner}
        checkpoint = await submit(host_run, "checkpoint")
        if checkpoint.get("ok") is not True or checkpoint.get("revision", 0) < 1:
            raise RuntimeError("The packaged multiplayer party could not commit a checkpoint")
        saved = CampaignStore.read_existing(
            campaign / "save", verify_world=verify_travel_checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(campaign / "game-user", data),
        )
        if saved is None or saved["revision"] < checkpoint["revision"]:
            raise RuntimeError("The shared multiplayer checkpoint was unavailable on disk")
        frames = {}
        for character in (first_character, second_character):
            frame = saved["characters"].get(character, {}).get("state", {}).get("native_guest_save", {})
            if (frame.get("character_id"), frame.get("map")) != (character, OBSERVATORY):
                raise RuntimeError("The combined checkpoint did not bind each native save to its own character")
            frames[character] = frame
        if len({frame["slot"] for frame in frames.values()}) != 2:
            raise RuntimeError("The two embedded guests shared a stock save generation")
        proof["checkpoint"] = {"requested_revision": checkpoint["revision"], "read_revision": saved["revision"],
                               "distinct_guest_saves": frames}
        after_checkpoint = await wait(host_run, both_bound, timeout=15)
        if _identity(_member(after_checkpoint, second_character)) != _identity(other):
            raise RuntimeError("The second embedded guest changed native identity during checkpoint")
        final_authentications = sum(event.get("event") == "client_link_authenticated" for event in _events(guest_run))
        proof["first_guest_authentications"] = [first_authentications, final_authentications]
        if first_authentications != final_authentications or second_game.poll() is not None:
            raise RuntimeError("The first guest reauthenticated or the second game exited while joining the party")
        assert (await submit(second_run, "stop"))["ok"]
        await asyncio.wait_for(tasks[-1], 15)
        return checkpoint

    async def restart_abruptly(invitation, invite_file, hosting, initial_joined, recovered_host, recovered_guest, seat):
        character, guest_id = invitation["character_id"], invitation["guest_id"]
        host = {"generation": hosting["generation"], "epoch": hosting["zone_epoch"], "endpoint": hosting["endpoint"],
                "pawn": hosting["game"]["pawns"]["local"]["name"]}
        local_pawn = _client_pawn(initial_joined, hosting, character)
        resumed_seat = _member(recovered_host, character)
        if (not seat or not resumed_seat or _identity(resumed_seat) != _identity(seat) or not local_pawn
                or not _same_visit(recovered_host, host) or recovered_guest["generation"] != initial_joined["generation"]
                or _client_pawn(recovered_guest, recovered_host, character) != local_pawn):
            raise RuntimeError("The short relay drop did not preserve the original embedded guest and its exact native seat")
        old_game, old_request = owned_guest(recovered_guest, evidence["guest_title"], character)
        before = _saved_guest(campaign, character)
        proof = evidence["abrupt_rejoin"] = {
            "initial": {"pid": old_game.pid, "native": seat, "local_pawn": local_pawn,
                        "generation": recovered_guest["generation"], "checkpoint": before},
            "same_runtime_drop_retained_native_seat": True,
        }
        if len(guest_games) != 1:
            raise RuntimeError("The original Join did not start exactly one owned guest")
        event_floor = len(_events(host_run))
        started = time.monotonic()
        old_game.kill()  # Only this owned game is terminated. Its embedded worker exits with it.
        code = await asyncio.to_thread(old_game.wait, timeout=8)
        if code == 0:
            raise RuntimeError("The owned guest did not exit abruptly")
        proof["exit_code"] = code
        await asyncio.wait_for(asyncio.shield(tasks[-1]), timeout=15)
        final_title = runtime.read_json(case / "join" / "game-bridge" / STATUS_FILE)
        if (final_title.get("state") != "stopped" or final_title.get("attempt") != evidence["guest_title"]["attempt"]
                or (final_title.get("last_failure") or {}).get("code") != "session_failed"):
            raise RuntimeError("The original title supervisor did not observe its embedded child failure")

        def old_reserved(server):
            current = _member(server, character)
            return (_same_visit(server, host) and current and _identity(current) == _identity(seat)
                    and lease(server, character).get("state") == "reserved")

        await wait(host_run, old_reserved, timeout=15)
        proof["reserved_old_connection"] = seat["connection_id"]

        def observe_wait(panel, title_game):
            peer = status(guest_run)
            if (panel.get("progress"), peer.get("state"), peer.get("wait_reason")) != (
                    "waiting_for_host", "waiting_for_host", NATIVE_RUNTIME_RETIRING):
                return
            if (len(guest_games) != 2 or title_game.poll() is not None or guest_games[-1][0].poll() is not None
                    or peer.get("pid") != guest_games[-1][0].pid or peer.get("generation") == recovered_guest["generation"]
                    or peer.get("character_id") != character or peer.get("active") is not True):
                raise RuntimeError("The replacement wait did not belong to the exact new embedded guest and live title")
            proof.setdefault("waiting_for_retirement", {key: peer[key] for key in ("state", "wait_reason", "pid", "generation")})

        replacement = embedded_title_plan.guest_plan(work, profile, invite_file, character, headless=True)
        handoff = await choose_guest(case / "replacement", replacement, timeout=165, observe=observe_wait)
        proof["replacement_title"] = handoff
        if "waiting_for_retirement" not in proof:
            raise RuntimeError("The new title never reported the qualified native retirement wait")
        fresh_guest = await wait(guest_run, lambda peer: peer.get("generation") != recovered_guest["generation"]
                                 and _client_pawn(peer, recovered_host, character), timeout=12)
        fresh_game, fresh_request = owned_guest(fresh_guest, handoff, character)
        if (fresh_game is old_game or fresh_game.pid == old_game.pid or fresh_request.bridge_id == old_request.bridge_id
                or fresh_request.selection != old_request.selection or fresh_request.title_generation == old_request.title_generation):
            raise RuntimeError("The replacement did not open a fresh embedded game with the same invitation and character")

        def fresh_seat(server):
            current = _member(server, character)
            if (not _same_visit(server, host) or not current or current["driver"] != seat["driver"]
                    or server.get("pid") != hosting["pid"] or lease(server, character).get("state") != "active"
                    or current["connection_id"] == seat["connection_id"] or current["pawn"] == seat["pawn"]):
                return False
            census = _census(server["game"])
            return (seat["connection_id"] not in census.connections
                    and all(row.pawn != seat["pawn"] for row in census.connections.values()))

        authority = await wait(host_run, fresh_seat, timeout=12)
        fresh = _member(authority, character)
        if fresh["health"] != seat["health"]:
            raise RuntimeError("The replacement pawn did not restore the same character's native health")
        events = iter(_events(host_run)[event_floor:])

        def receipt(kind, **fields):
            for event in events:
                if event.get("event") == kind and all(event.get(key) == value for key, value in fields.items()):
                    return event
            raise RuntimeError(f"The host did not record the ordered {kind} receipt for the exact replacement")

        identity = {"guest_id": guest_id, "character_id": character, "old_connection_id": seat["connection_id"]}
        pending = receipt("guest_runtime_replacement_pending", **identity)
        expiry = receipt("native_guest_expiry_checkpointed", character_id=character)
        retired = receipt("guest_runtime_retired", **identity)
        enrolled = receipt("native_guest_enrolled", guest_id=guest_id, driver=fresh["driver"],
                           connection_id=fresh["connection_id"], pawn=fresh["pawn"])
        restored = receipt("native_guest_save_restored", character_id=character, state="restored",
                           pawn=fresh["pawn"], slot=expiry["slot"], map=OBSERVATORY)
        after = await submit(host_run, "checkpoint")
        assert after["ok"] is True
        persisted = _saved_guest(campaign, character)
        if persisted["revision"] <= expiry["revision"] or expiry["revision"] <= before["revision"]:
            raise RuntimeError("The restored embedded guest did not commit a newer independent native save")
        proof.update(elapsed_seconds=round(time.monotonic() - started, 3),
                     fresh={"pid": fresh_game.pid, "native": fresh, "local_pawn": _client_pawn(fresh_guest, authority, character),
                            "generation": fresh_guest["generation"], "checkpoint": persisted},
                     receipts=[pending, expiry, retired, enrolled, restored])

    try:
        port = runtime.spare_udp_port()
        if embedded:
            host = embedded_title_plan.host_plan(work, campaign, "127.0.0.1", port, "wanderer", OBSERVATORY, headless=True)
        else:
            host = host_plan(work, campaign, "127.0.0.1", port, headless=True,
                             native_events=True, native_guest=True, native_zones=True, start_zone=OBSERVATORY)
        evidence["host_title"] = await title_choice(work, case, "host", host, processes, tasks)
        hosting = await wait(host_run, lambda state: state.get("state") == "hosting"
                             and state.get("game", {}).get("map") == OBSERVATORY)
        assert hosting["endpoint"] == {"address": "127.0.0.1", "port": port}, hosting["endpoint"]
        evidence["host"] = {"map": OBSERVATORY, "port": port, "title_survived_until_host_ready": True}
        if embedded:
            assert hosting["pid"] != os.getpid()
            evidence["host"]["worker_pid"] = hosting["pid"]

        invitation = await submit(host_run, "invite", name="Title guest", character="Title Scout")
        assert invitation["ok"] is True
        invite_file = Path(invitation["invite_file"])
        guest = (embedded_title_plan.guest_plan(work, profile, invite_file, None, headless=True) if embedded else
                 join_plan(work, profile, invite_file, headless=True))
        evidence["guest_title"] = await choose_guest(case, guest)
        guest_task = tasks[-1]
        joined = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected")
                            and state.get("game", {}).get("map") == OBSERVATORY)
        paired = await wait(host_run, lambda state: state.get("game", {}).get("remote_clients") == 1
                            and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound"
                            and (not (abrupt_rejoin or multiparty) or _member(state, invitation["character_id"])))
        assert joined["character_id"] == invitation["character_id"]
        evidence["guest"] = {"map": OBSERVATORY, "character_id": joined["character_id"],
                             "native_save": paired["game"]["native_guest_save"]["state"],
                             "title_survived_until_guest_ready": True}
        if embedded:
            assert joined["pid"] not in (os.getpid(), hosting["pid"])
            evidence["guest"]["worker_pid"] = joined["pid"]
        pawn = paired["game"]["native_guest_save"]["pawn"]
        seat = _member(paired, invitation["character_id"]) if abrupt_rejoin or multiparty else None
        dropped = await submit(guest_run, "drop-link")
        assert dropped["ok"] and dropped.get("link_connected") is False, dropped
        reserved = await wait(host_run, lambda state: any(
            row.get("character_id") == invitation["character_id"] and row.get("state") == "reserved"
            for row in state.get("campaign", {}).get("leases", ())))
        if seat:
            held = _member(reserved, invitation["character_id"])
            if not held or _identity(held) != _identity(seat):
                raise RuntimeError("The short link drop did not reserve the original embedded guest's native connection")
        resumed = await submit(guest_run, "reconnect")
        assert resumed["ok"] and resumed.get("resumed") is True and resumed.get("link_connected") is True, resumed
        recovered_guest = await wait(guest_run, lambda state: state.get("state") == "connected"
                                     and state.get("link_connected") is True)
        recovered_host = await wait(host_run, lambda state:
                                    state.get("game", {}).get("sequence", 0) > reserved["game"]["sequence"]
                                    and state["game"].get("native_guest_save", {}).get("pawn") == pawn
                                    and any(row.get("character_id") == invitation["character_id"] and row.get("state") == "active"
                                            for row in state.get("campaign", {}).get("leases", ())))
        assert recovered_guest["character_id"] == invitation["character_id"] and recovered_host["endpoint"]["port"] == port
        evidence["reconnect"] = {"character_id": recovered_guest["character_id"], "pawn": pawn, "port": port}
        if multiparty:
            checkpoint = await add_second_guest(hosting, joined, recovered_host, recovered_guest, seat,
                                                invitation["character_id"])
        else:
            checkpoint = await submit(host_run, "checkpoint")
        assert checkpoint["ok"] is True and checkpoint.get("revision", 0) > 0, checkpoint
        evidence["revision"] = checkpoint["revision"]
        if abrupt_rejoin:
            await restart_abruptly(invitation, invite_file, hosting, joined, recovered_host, recovered_guest, seat)
            guest_task = tasks[-1]
        assert (await submit(guest_run, "stop"))["ok"]
        await asyncio.wait_for(guest_task, 15)
        assert (await submit(host_run, "stop"))["ok"]
        await asyncio.wait_for(tasks[0], 15)
        evidence["verified"] = True
    except BaseException as error:
        evidence["failure"] = str(error) or type(error).__name__
        raise
    finally:
        try:
            for process in reversed(processes):
                if process.poll() is None:
                    await runtime.stop_game(process)
            for task in reversed(tasks):
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError, OSError, RuntimeError, TimeoutError, ValueError):
                    await task
        finally:
            evidence["original_saves_verified"] = saved_hashes(original) == initial
            evidence["original_save_count"] = len(initial)
            evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
            (case / "live-title-handoff-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            assert evidence["original_saves_verified"], "The original stock save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--embedded", action="store_true")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--abrupt-rejoin", action="store_true", help="kill and relaunch only the owned embedded guest")
    scenario.add_argument("--multiparty", action="store_true", help="join two embedded guests in a fresh Observatory campaign")
    options = parser.parse_args()
    if (options.abrupt_rejoin or options.multiparty) and not options.embedded:
        parser.error("--abrupt-rejoin and --multiparty require --embedded")
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True),
                       embedded=options.embedded, abrupt_rejoin=options.abrupt_rejoin, multiparty=options.multiparty))
