"""Verify exact native rejoin after the staged guest executable exits abruptly."""

import argparse
import asyncio
from contextlib import suppress
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
import uuid

from live_multi_smoke import saved_hashes
from live_reconnect_grace_smoke import lease, status


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish import native_host_save, party, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import submit
from tools.silverfish.native_roster import _census
from tools.silverfish.travel import OBSERVATORY


REPORT = "live-native-runtime-rejoin-verification.json"
OBSERVER_FRAME_GAP = 3


def _member(server, character):
    """Find one bound native seat by its campaign character, independent of slot labels."""
    game = server.get("game", {})
    world = server.get("gameplay", {})
    roles = [role for role, player in world.get("players", {}).items() if player.get("character_id") == character]
    if len(roles) != 1:
        return None
    role = roles[0]
    pawns = game.get("pawns", {})
    name = world.get("native_pawns", {}).get(role) or pawns.get(role, {}).get("name")
    if not name:
        return None
    matches = [row for row in pawns.values() if row.get("name") == name]
    if len(matches) != 1:
        return None
    pawn = matches[0]
    scope = game.get("native_guest_save_members", {}).get(name) or game.get("native_guest_save", {})
    if scope.get("state") != "bound" or scope.get("pawn") != name:
        return None
    try:
        census = _census(game)
    except ValueError:
        return None
    for connection_id, row in census.connections.items():
        if row.state == "ready" and (row.pawn, row.controller) == (name, pawn["controller"]):
            return {"role": role, "driver": census.driver, "connection_id": connection_id, "connection": row.name,
                    "controller": row.controller, "pawn": name, "position": pawn["location"], "health": pawn["health"]}
    return None


def _identity(seat):
    return tuple(seat[key] for key in ("driver", "connection_id", "connection", "controller", "pawn"))


def _same_visit(server, host):
    return (server.get("state"), server.get("generation"), server.get("endpoint"), server.get("zone_epoch"),
            server.get("game", {}).get("map"), server.get("game", {}).get("pawns", {}).get("local", {}).get("name")) == (
                "hosting", host["generation"], host["endpoint"], host["epoch"], OBSERVATORY, host["pawn"])


def _client_pawn(client, server, character):
    if ((client.get("state"), client.get("link_connected"), client.get("character_id"),
         client.get("host_session"), client.get("zone_epoch"), client.get("game", {}).get("map"))
            != ("connected", True, character, server.get("generation"), server.get("zone_epoch"), OBSERVATORY)):
        return None
    return client.get("game", {}).get("pawns", {}).get("local", {}).get("name")


class ObserverContinuity:
    """Keep stock, relay and campaign observations separate at the pending seat boundary."""

    def __init__(self, host, character, seat, local_pawn, generation, process, evidence):
        self.host, self.character = host, character
        self.seat, self.local_pawn = seat, local_pawn
        self.generation, self.process = generation, process
        self.evidence, self.gap, self.frames = evidence, None, {}
        evidence["gaps"] = []

    def check(self, server, peer):
        now, unix = time.monotonic(), time.time()
        game, local = server.get("game", {}), peer.get("game", {})
        world = server.get("gameplay", {})
        roster = game.get("native_roster", {})
        seat = self.seat
        row = next((entry for entry in roster.get("connections", []) if entry.get("id") == seat["connection_id"]), {})
        scope = game.get("native_guest_save_members", {}).get(seat["pawn"]) or game.get("native_guest_save", {})
        roles = [role for role, player in world.get("players", {}).items() if player.get("character_id") == self.character]
        projected = world.get("native_pawns", {}).get(seat["role"])
        aliases = {role: pawn.get("controller") for role, pawn in game.get("pawns", {}).items()
                   if pawn.get("name") == seat["pawn"]}
        pawn = local.get("pawns", {}).get("local", {})
        failure, missing, stale = [], [], []

        def require(name, value, expected):
            if value is None:
                missing.append(name)
            elif value != expected:
                failure.append(name)

        def ages(source, status, frame):
            stamp, seq = status.get("updated_unix"), frame.get("sequence")
            age = max(0, unix - stamp) if type(stamp) in (int, float) else None
            if age is None or age >= OBSERVER_FRAME_GAP:
                missing.append(source + "_status_stale")
                if age is not None:
                    stale.append(age)
            last_seq, last_at = self.frames.get(source, (0, now))
            if type(seq) is int and seq > last_seq:
                self.frames[source] = (seq, now)
                last_seq, last_at = seq, now
            frame_age = now - last_at
            if not seq or seq != last_seq or frame_age >= OBSERVER_FRAME_GAP:
                missing.append(source + "_sequence_stale")
                if seq == last_seq:
                    stale.append(frame_age)
            return {"sequence": seq, "sequence_age_seconds": round(frame_age, 3),
                    "status_age_seconds": round(age, 3) if age is not None else None}

        exit_code = self.process.poll()
        if exit_code is not None:
            failure.append("observer_process_exited")
        require("host_generation", server.get("generation"), self.host["generation"])
        require("host_route", server.get("endpoint"), self.host["endpoint"])
        require("host_epoch", server.get("zone_epoch"), self.host["epoch"])
        require("host_map", game.get("map"), OBSERVATORY)
        require("host_pawn", game.get("pawns", {}).get("local", {}).get("name"), self.host["pawn"])
        require("observer_generation", peer.get("generation"), self.generation)
        require("observer_quic", peer.get("link_connected"), True)
        require("observer_character", peer.get("character_id"), self.character)
        require("observer_host_session", peer.get("host_session"), self.host["generation"])
        require("observer_epoch", peer.get("zone_epoch"), self.host["epoch"])
        require("observer_map", local.get("map"), OBSERVATORY)
        require("observer_pawn", pawn.get("name"), self.local_pawn)
        if (peer.get("state"), local.get("state")) != ("connected", "connected"):
            missing.append("observer_connected_phase")
        if roles != [seat["role"]] or projected is None or len(aliases) != 1:
            missing.append("observer_native_projection")
        if len(aliases) == 1 and next(iter(aliases.values())) != seat["controller"]:
            missing.append("observer_native_alias_controller")
        require("observer_projected_pawn", projected, seat["pawn"])
        if (scope.get("state"), scope.get("pawn")) != ("bound", seat["pawn"]):
            missing.append("observer_detached_scope")

        census_error = None
        try:
            census = _census(game)
        except ValueError as error:
            census_error = str(error)
            missing.append("native_census")
        else:
            require("native_driver", census.driver, seat["driver"])
            connection = census.connections.get(seat["connection_id"])
            if not connection or connection.state != "ready":
                missing.append("observer_native_connection")
            if connection:
                require("observer_native_connection_name", connection.name, seat["connection"])
                require("observer_native_controller", connection.controller, seat["controller"])
                require("observer_native_pawn", connection.pawn, seat["pawn"])
            if any(key != seat["connection_id"] and other.state == "ready"
                   and (other.pawn == seat["pawn"] or other.controller == seat["controller"])
                   for key, other in census.connections.items()):
                failure.append("observer_pawn_reassigned")

        frame = {
            "sampled_unix": unix,
            "host": {**ages("host", server, game), "phase": server.get("state"), "map": game.get("map"),
                     "generation": server.get("generation"), "zone_epoch": server.get("zone_epoch"),
                     "roster_state": roster.get("state"), "roster_count": roster.get("count"),
                     "roster_reason": roster.get("reason"), "census_error": census_error,
                     "remote_clients": game.get("remote_clients"), "driver": roster.get("driver"),
                     "seat": {key: row.get(key) for key in ("id", "state", "connection", "controller", "pawn")},
                     "detached_scope": {key: scope.get(key) for key in ("state", "pawn")}, "projected_roles": roles,
                     "projected_pawn": projected, "observed_aliases": aliases,
                     "lease_state": lease(server, self.character).get("state")},
            "observer": {**ages("observer", peer, local), "phase": peer.get("state"),
                         "stock_phase": local.get("state"), "map": local.get("map"), "pawn": pawn.get("name"),
                         "controller": pawn.get("controller"), "game_pid": self.process.pid, "exit_code": exit_code,
                         "quic_connected": peer.get("link_connected"), "coordinator": peer.get("generation"),
                         "host_session": peer.get("host_session"), "zone_epoch": peer.get("zone_epoch")},
            "mismatches": failure, "unavailable": missing,
        }
        self.evidence["latest"] = frame
        if not missing and not failure:
            self.evidence.setdefault("first_exact", frame)
            self.evidence["latest_exact"] = frame
            if self.gap:
                started, record = self.gap
                record["duration_seconds"] = round(now - started, 3)
                record["recovered"] = True
                self.gap = None
            return True

        if self.gap is None:
            record = {"first": frame, "recovered": False}
            self.evidence["gaps"].append(record)
            self.gap = now, record
        started, record = self.gap
        duration = max(now - started, *stale) if stale else now - started
        record.update(latest=frame, duration_seconds=round(duration, 3))
        if failure:
            raise RuntimeError("The observer changed identity or lost its route: " + ", ".join(failure))
        if "first_exact" not in self.evidence or duration >= OBSERVER_FRAME_GAP:
            raise RuntimeError("The observer's exact telemetry was unavailable: " + ", ".join(missing))
        return False


def _events(run):
    with (run / "events.jsonl").open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.endswith("\n")]


async def _command(run, verb, **fields):
    reply = await submit(run, verb, **fields)
    if reply.get("ok") is not True:
        raise RuntimeError(f"{verb} failed: {reply.get('error')}")
    return reply


def _saved_guest(campaign, character):
    saved = CampaignStore.read_existing(
        campaign / "save", verify_world=service.verify_travel_checkpoint,
        verify_checkpoint=lambda data: native_host_save.verify_checkpoint(campaign / "game-user", data),
    )
    if saved is None:
        raise RuntimeError("No verified staged checkpoint was committed")
    record = saved["characters"][character]["state"]["native_guest_save"]
    if (record.get("character_id"), record.get("map")) != (character, OBSERVATORY):
        raise RuntimeError("The staged stock checkpoint does not contain the exact guest")
    return {"revision": saved["revision"], "frame": record}


async def validate(work, original_saved, *, with_observer=False):
    if os.name != "nt":
        raise OSError("The live native reconnect test requires the staged Windows game")
    from tools.silverfish.win_job import GameProcess

    before = saved_hashes(original_saved)
    if not before:
        raise ValueError("Supply the original single-player Saved directory")
    if original_saved.is_relative_to(work) or work.is_relative_to(original_saved):
        raise ValueError("Use an isolated game stage outside the original save tree")
    stage_binary = (work / "stage" / runtime.SHIPPING).resolve(strict=True)
    if not stage_binary.is_relative_to(work / "stage"):
        raise ValueError("The executable is outside the isolated stage")

    label = "native-runtime-rejoin-" + uuid.uuid4().hex[:8]
    campaign, guest = work / "campaigns" / label, work / "clients" / label
    observer = guest.with_name(label + "-observer")
    run, guest_run, observer_run = (profile / "run" for profile in (campaign, guest, observer))
    run.mkdir(parents=True)
    print(run, flush=True)
    allowed_profiles = {campaign / "game-user", guest / "game-user"}
    if with_observer:
        allowed_profiles.add(observer / "game-user")
    proof, jobs = {"verified": False, "original_files": len(before), "with_observer": with_observer}, []
    launched = {}
    launch = service.launch

    def track(workspace, profile, mailbox, headless, *args, **kwargs):
        if (workspace != work or profile not in allowed_profiles or headless is not True
                or mailbox.parent != profile.parent / "run/game-bridge"
                or not re.fullmatch(r"[0-9a-f]{32}", mailbox.name)):
            raise RuntimeError("The runtime rejoin test can launch only its own staged headless games")
        process = launch(workspace, profile, mailbox, headless, *args, **kwargs)
        if not isinstance(process, GameProcess):
            raise RuntimeError("The staged game lacks its privately owned Windows job")
        launched.setdefault(profile.parent, []).append((process, mailbox))
        return process

    def start(directory, worker):
        task = asyncio.create_task(worker)
        jobs.append((directory, task))
        return task

    async def wait_for(label, predicate, *, timeout=35):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            authority, peer = status(run), status(guest_run)
            found = predicate(authority, peer)
            if found:
                return found
            if authority.get("state") == "failed":
                break
            await asyncio.sleep(.12)
        roster = authority.get("game", {}).get("native_roster", {})
        rows = [{"id": row.get("id"), "state": row.get("state")} for row in roster.get("connections", ())]
        raise RuntimeError(f"{label}: host={authority.get('state')} guest={peer.get('state')} "
                           f"host_connections={rows}, guest_game={peer.get('game', {}).get('state')}")

    async def invite(name):
        response = await _command(run, "invite", name=name, character=name + " scout")
        invitation = Path(response["invite_file"])
        if invitation.parent != run / "invites" or invitation.stem.split(".")[0] != response["guest_id"]:
            raise RuntimeError("The staged invitation is outside this isolated campaign")
        return response, invitation

    async def inventory(role):
        items = (await _command(run, "native-inventory-list", target=role))["native_inventory"]["items"]
        return {item["guid"]: {key: item.get(key) for key in ("class_path", "source", "quantity")} for item in items}

    with patch.object(service, "launch", side_effect=track):
        try:
            start(run, service.host_game(work, campaign, "127.0.0.1", 0, True, fixtures=True,
                                        native_events=True, native_guest=True, native_zones=True,
                                        guest_sickness=True, start_zone=OBSERVATORY,
                                        max_guests=2 if with_observer else 1))
            initial = await wait_for("The isolated Observatory host did not start",
                                     lambda server, _: server if server.get("state") == "hosting"
                                     and server.get("game", {}).get("map") == OBSERVATORY else None)
            host = {"generation": initial["generation"], "endpoint": initial["endpoint"], "epoch": initial["zone_epoch"],
                    "pawn": initial["game"]["pawns"]["local"]["name"]}
            host_game, _ = launched[campaign][0]
            proof["host"] = {**host, "game_pid": host_game.pid}

            entry, invitation = await invite("Runtime rejoin guest")
            character, guest_id = entry["character_id"], entry["guest_id"]
            guest_job = start(guest_run, service.join_game(work, guest, invitation, True))
            continuity = None

            def paired(server, peer):
                if not _same_visit(server, host):
                    return None
                seat = _member(server, character)
                local = _client_pawn(peer, server, character)
                if not seat or not local or lease(server, character).get("state") != "active":
                    return None
                return seat, local, peer["generation"]

            seat, local, guest_generation = await wait_for("The original guest did not bind to its native pawn", paired)
            old_game, old_bridge = launched[guest][0]
            if with_observer:
                second, second_invitation = await invite("Runtime rejoin observer")
                second_character = second["character_id"]
                start(observer_run, service.join_game(work, observer, second_invitation, True))

                def bound_observer(server, _):
                    peer = status(observer_run)
                    member = _member(server, second_character)
                    local_pawn = _client_pawn(peer, server, second_character)
                    if (member and local_pawn and _same_visit(server, host)
                            and server.get("gameplay", {}).get("native_pawns", {}).get(member["role"]) == member["pawn"]
                            and peer.get("game", {}).get("state") == "connected"):
                        return member, local_pawn, peer["generation"], server, peer
                    return None

                observer_seat, observer_pawn, observer_generation, server, peer = await wait_for(
                    "The unrelated observer did not bind", bound_observer)
                observer_game, _ = launched[observer][0]
                proof["observer_continuity"] = {}
                continuity = ObserverContinuity(host, second_character, observer_seat, observer_pawn,
                                                observer_generation, observer_game, proof["observer_continuity"])
                continuity.check(server, peer)
                observer_authentications = sum(event.get("event") == "client_link_authenticated"
                                               for event in _events(observer_run))

            def check_observer(server):
                return continuity is None or continuity.check(server, status(observer_run))

            guid = uuid.uuid4().hex.upper()
            await _command(run, "native-guest-weapon-seed", target=seat["role"], item_guid=guid)
            held_items = await inventory(seat["role"])
            assert guid in held_items
            proof["initial_guest"] = {"character": character, "game_pid": old_game.pid, "native": seat,
                                      "local_pawn": local, "coordinator": guest_generation,
                                      "item_guid": guid, "item": held_items[guid]}

            await _command(guest_run, "drop-link")

            def paused(server, peer):
                check_observer(server)
                if (_same_visit(server, host) and lease(server, character).get("state") == "reserved"
                        and peer.get("reconnect_suspended") is True):
                    return _member(server, character)
                return None

            held = await wait_for("The short relay outage did not reserve its original native connection",
                                  paused, timeout=12)
            assert _identity(held) == _identity(seat) and old_game.poll() is None
            reconnected = await _command(guest_run, "reconnect")
            assert reconnected.get("resumed") is True
            def same_runtime(server, peer):
                check_observer(server)
                return paired(server, peer)

            resumed, resumed_local, resumed_generation = await wait_for("The same runtime did not reconnect", same_runtime)
            assert (_identity(resumed), resumed_local, resumed_generation) == (_identity(seat), local, guest_generation)
            assert old_game.poll() is None and await inventory(resumed["role"]) == held_items
            proof["same_runtime_reconnect"] = {"retained_exact_connection": True, "retained_exact_inventory": True}
            await _command(run, "checkpoint")
            before_checkpoint = _saved_guest(campaign, character)
            proof["initial_guest"]["checkpoint"] = before_checkpoint["revision"]
            # Stock infection changes the detached guest save outside the inventory commands.
            # Keep the value safely above zero because stock sickness can decay while the pawn is reserved.
            sickness = 0
            for _ in range(4):
                infected = await _command(run, "native-guest-sickness-fixture", target=resumed["role"], amount=100)
                assert infected["native_guest_sickness_fixture"]["pawn"] == seat["pawn"]
                sickness = infected["native_guest_sickness"]["guest_sickness"]
                if sickness >= 12:
                    break
            assert sickness >= 12 and _saved_guest(campaign, character) == before_checkpoint

            assert old_game is not host_game and (not with_observer or old_game is not observer_game)
            assert old_game.poll() is None and old_bridge.parent == guest_run / "game-bridge"
            check_observer(status(run))
            event_floor = len(_events(run))
            interrupted_at = time.monotonic()
            # Terminate the exact owned process while its restored co-op link is active.
            old_game.kill()
            exit_code = await asyncio.to_thread(old_game.wait, timeout=8)
            assert exit_code != 0
            try:
                await asyncio.wait_for(asyncio.shield(guest_job), 20)
            except RuntimeError as failure:
                if str(failure) not in (f"Client game exited with code {exit_code}",
                                       f"Staged game exited with code {exit_code}"):
                    raise

            def reserved_old(server, peer):
                if not _same_visit(server, host):
                    return None
                check_observer(server)
                if lease(server, character).get("state") != "reserved" or peer.get("active") is not False:
                    return None
                current = _member(server, character)
                return current if current and _identity(current) == _identity(seat) else None

            old_still_bound = await wait_for(
                "The real process exit did not leave the exact old stock connection in reconnect grace",
                reserved_old, timeout=12)
            assert _identity(old_still_bound) == _identity(seat)
            proof["old_runtime_exited"] = {"exited_game_pid": old_game.pid, "exit_code": exit_code,
                                           "reserved_old_connection": old_still_bound["connection_id"]}

            start(guest_run, service.join_game(work, guest, invitation, True))
            waiting_for_retirement = False

            def replacement(server, peer):
                nonlocal waiting_for_retirement
                check_observer(server)
                if (peer.get("state"), peer.get("wait_reason")) == ("waiting_for_host", "native_runtime_retiring"):
                    if peer.get("character_id") != character:
                        raise RuntimeError("The waiting replacement names the wrong campaign character")
                    waiting_for_retirement = True
                result = paired(server, peer)
                if not result or result[2] == guest_generation:
                    return None
                current, _, _ = result
                live_sickness = server["game"].get("native_guest_sickness", {}).get(current["pawn"], {})
                if "guest_sickness" not in live_sickness:
                    return None
                if (current["connection_id"] == seat["connection_id"] or current["pawn"] == seat["pawn"]
                        or current["driver"] != seat["driver"]):
                    return None
                census = _census(server["game"])
                if seat["connection_id"] in census.connections or any(row.pawn == seat["pawn"] for row in census.connections.values()):
                    return None
                return result

            fresh, fresh_local, fresh_generation = await wait_for(
                "The replacement runtime did not retire the old stock connection and bind a fresh pawn",
                replacement, timeout=145)
            fresh_game, fresh_bridge = launched[guest][-1]
            assert len(launched[guest]) == 2 and fresh_game is not old_game and fresh_game.pid != old_game.pid
            assert fresh_game.poll() is None and fresh_bridge != old_bridge and host_game.poll() is None
            elapsed = time.monotonic() - interrupted_at
            if not waiting_for_retirement:
                raise RuntimeError("The new guest did not visibly wait for safe native retirement")
            lead = status(run)["game"]["pawns"]["local"]["location"]
            safe_position = (math.dist(fresh["position"], seat["position"]) <= 130
                             or math.dist(fresh["position"], lead) <= party.INTERACT_RANGE)
            if fresh["health"] != seat["health"] or not safe_position:
                raise RuntimeError("The fresh pawn did not restore the same character's health at a qualified place")
            assert await inventory(fresh["role"]) == held_items
            restored_sickness = status(run)["game"]["native_guest_sickness"][fresh["pawn"]]["guest_sickness"]
            assert 0 < restored_sickness <= sickness, (sickness, restored_sickness)
            assert _saved_guest(campaign, character) == before_checkpoint
            events = _events(run)[event_floor:]

            def event_index(kind, after, **expected):
                for index, event in enumerate(events):
                    if (index > after and event.get("event") == kind
                            and all(event.get(key) == value for key, value in expected.items())):
                        return index
                raise RuntimeError(f"The host did not record an ordered {kind} for the exact replacement")

            pending = event_index("guest_runtime_replacement_pending", -1, guest_id=guest_id)
            saved = event_index("guest_disconnect_save_retained", -1, character_id=character, pawn=seat["pawn"])
            retired = event_index("guest_runtime_retired", pending, guest_id=guest_id)
            assert saved < retired
            for event in (events[pending], events[retired]):
                connection = event.get("old_connection_id", event.get("connection_id"))
                if connection is not None and connection != seat["connection_id"]:
                    raise RuntimeError("The replacement retirement receipt names the wrong old native connection")
            enrolled = event_index("native_guest_enrolled", retired, guest_id=guest_id,
                                   connection_id=fresh["connection_id"], pawn=fresh["pawn"])
            event_index("guest_pawn_ready", enrolled, character_id=character, pawn=fresh["pawn"])
            await _command(run, "checkpoint")
            final_checkpoint = _saved_guest(campaign, character)
            assert final_checkpoint["revision"] > before_checkpoint["revision"]
            proof["new_runtime"] = {"native": fresh, "local_pawn": fresh_local, "coordinator": fresh_generation,
                                    "game_pid": fresh_game.pid, "elapsed_seconds": round(elapsed, 3),
                                    "prior_connection_absent": True, "same_inventory": True,
                                    "unsaved_sickness": sickness, "restored_sickness": restored_sickness,
                                    "checkpoint_unchanged_until_explicit_save": True,
                                    "waited_for_native_retirement": waiting_for_retirement,
                                    "retirement_before_enrollment": True,
                                    "checkpoint": final_checkpoint["revision"]}
            if with_observer:
                await wait_for("The observer was unverified after the final checkpoint",
                               lambda server, _: check_observer(server), timeout=OBSERVER_FRAME_GAP + 1)
                final_authentications = sum(event.get("event") == "client_link_authenticated"
                                            for event in _events(observer_run))
                proof["observer_continuity"]["authentications"] = [observer_authentications, final_authentications]
                if final_authentications != observer_authentications:
                    raise RuntimeError("The observer authenticated again during the recovery test")
                proof["observer_retained"] = {"native": observer_seat, "game_pid": observer_game.pid,
                                              "local_pawn": observer_pawn, "coordinator": observer_generation}
            proof["verified"] = True
        except BaseException as error:
            proof["failure"] = str(error)
            raise
        finally:
            for directory, job in reversed(jobs):
                if not job.done():
                    with suppress(OSError, RuntimeError, TimeoutError):
                        await asyncio.wait_for(_command(directory, "stop"), 10)
                        await asyncio.wait_for(asyncio.shield(job), 12)
                if not job.done():
                    job.cancel()
                with suppress(asyncio.CancelledError, OSError, ValueError, RuntimeError, TimeoutError, AssertionError):
                    await job
            proof["original_saves_verified"] = saved_hashes(original_saved) == before
            proof["verified"] = proof["verified"] and proof["original_saves_verified"]
            (run / REPORT).write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
            assert proof["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    parser.add_argument("--with-observer", action="store_true", help="Keep an unrelated second guest continuously connected")
    options = parser.parse_args()
    asyncio.run(validate(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True),
                         with_observer=options.with_observer))
