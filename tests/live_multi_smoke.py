"""Exercise the staged host and independent guests on one Windows PC."""

import argparse
import asyncio
from hashlib import file_digest
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid


CROSSROADS = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
DEAD_BRIDGE = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"


def saved_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = file_digest(stream, "sha256").hexdigest()
    return result


def campaign_save_hashes(root: Path) -> dict[str, str]:
    result = saved_hashes(root / "game-user" / "Saved" / "SaveGames")
    for path in (root / "save").glob("campaign*.json"):
        with path.open("rb") as stream:
            result[str(path.relative_to(root))] = file_digest(stream, "sha256").hexdigest()
    return result


async def validate(work: Path, vanilla_saved: Path | None, *, rest_wipe: bool = False, rest_ui: bool = False,
                   rest_guest_denial: bool = False, guest_count: int = 2, downed_markers: bool = False,
                   dead_bridge_rest: bool = False) -> None:
    campaign = "party-" + uuid.uuid4().hex[:8]
    rest_map = DEAD_BRIDGE if dead_bridge_rest else OBSERVATORY
    rest_bed = "SavePoint_BP_C_1-DeadBridge_P-" if dead_bridge_rest else "SavePoint_BP_C_3-Observitory_P-"
    entrance = "DB_CR" if dead_bridge_rest else "Obsv_Etr"
    guest_roles = ("guest", *(f"guest{index}" for index in range(2, guest_count + 1)))
    profiles = [f"{campaign}-{index}" for index in range(1, guest_count + 1)]
    run = work / "campaigns" / campaign / "run"
    clients = [work / "clients" / profile / "run" for profile in profiles]
    base = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    launched = []
    baseline = saved_hashes(vanilla_saved) if vanilla_saved else None
    evidence = {}

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*base, *arguments, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok", True) is not expected:
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def start(log_name, target, arguments):
        run.mkdir(parents=True, exist_ok=True)
        with (run / log_name).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *arguments, stdout=stream, stderr=stream,
                                                           creationflags=subprocess.CREATE_NO_WINDOW)
        launched.append((process, target))

    async def wait(directory, condition, timeout=38):
        end = time.monotonic() + timeout
        state = {}
        while time.monotonic() < end:
            try:
                state = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                await asyncio.sleep(0.2)
                continue
            if condition(state):
                return state
            if state.get("state") == "failed":
                break
            await asyncio.sleep(0.2)
        raise RuntimeError(f"{directory.name} did not reach the expected game state: {state}")

    def players(state):
        return state.get("gameplay", {}).get("players", {})

    def bound(state, count):
        game = state.get("game", {})
        saves = game.get("native_guest_save_members", {})
        return game.get("remote_clients") == count and len(saves) == count and all(
            row["state"] == "bound" for row in saves.values()) and len(players(state)) == count + 1

    def native_body(state, name):
        return next((pawn for pawn in state.get("game", {}).get("pawns", {}).values()
                     if pawn["name"] == name), {})

    def host_marker(state, name, visible):
        game = state.get("game", {})
        slot = next((alias for alias, pawn in game.get("pawns", {}).items() if pawn.get("name") == name), None)
        return bool(slot and game.get("player_visuals", {}).get(slot, {}).get("marker") is visible)

    async def guest_markers(character, host, target_id, location=None, *, target_downed=False):
        def projected(state):
            game = state.get("game", {})
            frame = game.get("native_downed_markers", {})
            area = host["game"]["map"]
            if (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"),
                game.get("map"), frame.get("epoch"), frame.get("map")) != (
                    character, host["generation"], host["zone_epoch"], area, host["zone_epoch"], area):
                return False
            expected = target_downed and character != target_id
            markers = frame.get("markers")
            if (not isinstance(markers, dict) or set(markers) != ({target_id} if expected else set())
                    or frame.get("count") != len(markers)):
                return False
            if expected:
                point = markers[target_id]
                return (isinstance(point, list) and len(point) == 3 and all(math.isfinite(value) for value in point)
                        and math.dist(point, location) <= 90)
            if character == target_id:
                local = game.get("player_effects", {}).get("local", {})
                if target_downed:
                    return local.get("state") == "downed" and local.get("crouched") is True and local.get("jump_velocity") == 0
                return local.get("state") == "standing"
            return True

        state = await wait(clients[identities.index(character)], projected, timeout=20)
        return state["game"]["native_downed_markers"]

    try:
        await start("host.txt", ("--campaign", campaign),
                    ("host", "--campaign", campaign, "--port", "0", "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--max-guests", str(guest_count), "--start-zone", CROSSROADS))
        await wait(run, lambda status: status.get("state") == "hosting")
        identities = []
        for index, profile in enumerate(profiles):
            invitation = await command("invite", "--campaign", campaign, "--name", f"Loopback guest {index + 1}",
                                       "--character", f"Loopback character {index + 1}")
            identities.append(invitation["character_id"])
            await start(f"guest-{index + 1}.txt", ("--profile", profile),
                        ("join", "--profile", profile, "--invite", invitation["invite_file"], "--headless", "--native-zones"))
            await wait(clients[index], lambda status: status.get("state") == "connected")
            await wait(run, lambda status: bound(status, index + 1))

        before = await wait(run, lambda status: bound(status, guest_count)
                            and status["game"]["native_roster"]["count"] == guest_count)
        exact_pawns = before["gameplay"]["native_pawns"]
        assert len(set(exact_pawns.values())) == guest_count + 1 and all(
            players(before)[role]["character_id"] == character for role, character in zip(guest_roles, identities)), before
        evidence["native_pawns"] = exact_pawns

        await command("action", "--campaign", campaign, "native-guest-damage", "--target", "guest2")
        down = await wait(run, lambda status: players(status).get("guest2", {}).get("downed", False)
                          and (native_body(status, exact_pawns["guest2"]).get("health") or 0) <= 0)
        assert all(not player["downed"] for role, player in players(down).items() if role != "guest2"), down
        if downed_markers:
            down = await wait(run, lambda status: host_marker(status, exact_pawns["guest2"], True), timeout=15)
            location = native_body(down, exact_pawns["guest2"])["location"]
            projected = await asyncio.gather(*(guest_markers(character, down, identities[1], location, target_downed=True)
                                               for character in identities))
            evidence["downed_markers"] = {
                "target": identities[1], "authority_position": location, "host_marker": True,
                "guest_frames": dict(zip(identities, projected, strict=True)),
            }
        await command("action", "--profile", profiles[0], "interact", "--target", identities[1])
        revived = await wait(run, lambda status: not players(status).get("guest2", {}).get("downed", True)
                             and native_body(status, exact_pawns["guest2"]).get("health") == 25.0)
        evidence["guest2_revived_by_guest1"] = native_body(revived, exact_pawns["guest2"])["health"]
        if downed_markers:
            revived = await wait(run, lambda status: host_marker(status, exact_pawns["guest2"], False), timeout=15)
            cleared = await asyncio.gather(*(guest_markers(character, revived, identities[1]) for character in identities))
            evidence["downed_markers"].update(host_cleared=True, guest_frames_after_revive=dict(zip(identities, cleared, strict=True)))

        await command("action", "--profile", profiles[0], "drop-link")
        await wait(clients[0], lambda status: not status.get("link_connected", True))
        await command("action", "--profile", profiles[0], "reconnect")
        resumed = await wait(run, lambda status: bound(status, guest_count))
        assert resumed["gameplay"]["native_pawns"] == exact_pawns, resumed
        evidence["resume_retained_all_pawns"] = True

        async def position(role, where):
            return await command("action", "--campaign", campaign, "native-exit-position",
                                 "--target", role, "--exit-position", where, "--entrance", entrance)

        for role in guest_roles[:-1]:
            await position(role, "near")
        await position(guest_roles[-1], "far")
        await position("host", "inside")
        refused = await wait(run, lambda status: status.get("game", {}).get("native_zone", {}).get("decision") == "too_far")
        assert refused["game"]["map"] == CROSSROADS and refused["zone_epoch"] == before["zone_epoch"], refused
        await position("host", "near")
        await position(guest_roles[-1], "near")
        await wait(run, lambda status: bound(status, guest_count) and all(
            math.dist(native_body(status, exact_pawns["host"])["location"], native_body(status, exact_pawns[role])["location"]) <= 1500
            for role in guest_roles))
        await position("host", "inside")
        arrived = await wait(run, lambda status: bound(status, guest_count) and status["game"]["map"] == rest_map
                              and not status.get("awaiting_zone_guest"))
        for client in clients:
            await wait(client, lambda status: status.get("state") == "connected" and status["game"]["map"] == rest_map)
        assert arrived["zone_epoch"] != before["zone_epoch"] and all(
            players(arrived)[role]["character_id"] == character for role, character in zip(guest_roles, identities)
        ), arrived
        evidence["all_guests_required_and_travelled"] = {"map": rest_map, "identities": identities}

        await command("action", "--campaign", campaign, "checkpoint")
        saved = await wait(run, lambda status: all(
            entry["state"].get("native_guest_save", {}).get("map") == rest_map
            for entry in status.get("campaign", {}).get("characters", [])))
        characters = saved["campaign"]["characters"]
        frames = {entry["character_id"]: entry["state"]["native_guest_save"] for entry in characters}
        assert set(frames) == set(identities) and len({record["slot"] for record in frames.values()}) == guest_count, frames
        evidence["separate_native_guest_frames"] = frames
        if rest_guest_denial:
            def armed(status):
                return status.get("game", {}).get("native_rest_guard", {}).get("armed") is True

            host_guard = await wait(run, lambda status: armed(status)
                                    and status["game"].get("native_rest_hook", {}).get("fresh") is True)
            client_guards = [await wait(client, armed) for client in clients]
            prior = host_guard["game"]["native_rest_guard"]["guest_denials"]
            denied = []
            for index, role in enumerate(guest_roles):
                server = (await command("action", "--campaign", campaign,
                                        "native-rest-guest-interaction-fixture", "--target", role))["native_rest_guard"]
                local = (await command("action", "--profile", profiles[index],
                                       "native-rest-guest-interaction-fixture"))["native_rest_guard"]
                assert server["armed"] and server["status"] == "guest_interaction_denied", server
                assert local["armed"] and local["status"] == "guest_interaction_denied", local
                assert server["guest_denials"] == prior + index + 1, server
                assert local["guest_denials"] == client_guards[index]["game"]["native_rest_guard"]["guest_denials"] + 1, local
                denied.append({"role": role, "authority": server, "client": local})
            events = (await command("events", "--campaign", campaign, "--tail", "100"))["events"]
            assert all(event["event"] != "native_rest_checkpoint" for event in events), events
            evidence["stock_rest_guest_interaction_denied"] = denied
        if rest_wipe:
            if dead_bridge_rest:
                rest_owner = ("action", "--campaign", campaign)
                fixture = (*rest_owner, "native-rest-dead-bridge-rep-fixture", "--target")
                await wait(run, lambda status: status.get("game", {}).get("native_rest_hook", {}).get("fresh") is True)
                initial_rest = (await command(*rest_owner, "native-rest-approach"))["native_rest"]
                assert (initial_rest["bed"], initial_rest["points"], initial_rest["eligible"]) == (rest_bed, -600, False)
                fallback = (await wait(run, lambda status: status.get("game", {}).get("native_blue_arrival", {})
                                       .get("placement") == "alternate"))["game"]["native_blue_arrival"]
                assert fallback["bed"] == rest_bed and fallback["actor"].endswith(".SavePoint_BP_C_1"), fallback
                raised = (await command(*fixture, "eligible"))["native_rest"]
                assert raised["points"] == 300, raised
                near = (await command(*rest_owner, "native-rest-approach"))["native_rest"]
                assert near["eligible"] and near["distance"] < near["range"], near
                hostile = await command(*fixture, "hostile")
                refusal = hostile["native_rest"]
                assert hostile["stock_hostile_callback_verified"] and refusal["reason"] == "reputation_too_low"
                assert refusal["points"] == -600 and refusal["distance"] < refusal["range"], refusal
                denied = await command(*rest_owner, "native-rest", "--amount", "1", expected=False)
                assert denied["error"] == "reputation_too_low", denied
                ready = (await command(*fixture, "eligible"))["native_rest"]
                assert ready["eligible"] and ready["points"] == 300, ready
                evidence["stock_faction_bed_gate"] = {"alternate": fallback, "hostile": refusal, "eligible": ready}
            approach = await command("action", "--campaign", campaign, "native-rest-approach")
            assert approach["native_rest"]["eligible"], approach
            if rest_ui:
                await wait(run, lambda status: status.get("game", {}).get("native_rest_hook", {}).get("fresh") is True)
                confirmation = await command("action", "--campaign", campaign, "native-rest-ui-fixture", "--amount", "1")
                assert confirmation["native_rest_hook"]["status"] == "selected", confirmation
                await wait(run, lambda status: status.get("campaign", {}).get("world", {}).get("native_rest", {})
                           .get("bed") == rest_bed)
                sleeps = [event for event in (await command("events", "--campaign", campaign, "--tail", "100"))["events"]
                          if event["event"] == "native_rest_checkpoint"]
                assert len(sleeps) == 1, sleeps
                slept = sleeps[0]
                evidence["stock_sleep_screen_confirmation"] = confirmation["native_rest_hook"]
            else:
                rest_args = ("action", "--campaign", campaign, "native-rest", "--amount", "1",
                             "--operation-id", uuid.uuid4().hex)
                slept = (await command(*rest_args))["native_rest"]
                assert (await command(*rest_args))["native_rest"] == slept
            assert slept["bed"] == rest_bed and math.isclose(
                slept["after"] - slept["before"], 3600, abs_tol=0.05), slept
            for role in ("host", *guest_roles):
                await position(role, "near")
            await position("host", "inside")
            await wait(run, lambda status: bound(status, guest_count) and status["game"]["map"] == CROSSROADS
                       and not status.get("awaiting_zone_guest"))
            for role in guest_roles:
                await command("action", "--campaign", campaign, "native-guest-damage", "--target", role)
                await wait(run, lambda status: players(status).get(role, {}).get("downed"))
            await command("action", "--campaign", campaign, "native-host-damage", "--target", "host")
            returned = await wait(run, lambda status: bound(status, guest_count) and status["game"]["map"] == rest_map
                                  and not status["gameplay"].get("wipe") and not status["gameplay"].get("pending_spawns")
                                  and all(native_body(status, pawn).get("health") == 250.0
                                          for pawn in status["gameplay"]["native_pawns"].values()), timeout=85)
            for client in clients:
                await wait(client, lambda status: status.get("state") == "connected" and status["game"]["map"] == rest_map)
            uses_rest = returned["game"].get("native_blue_arrival") or next(
                row for row in (await command("events", "--campaign", campaign, "--tail", "100"))["events"]
                if row["event"] == "party_respawn_committed")
            assert math.dist(uses_rest["location"], slept["location"]) < 400, uses_rest
            if dead_bridge_rest:
                assert (uses_rest["bed"], uses_rest["placement"], uses_rest["points"]) == (rest_bed, "arrow", 300), uses_rest
            assert all(players(returned)[role]["character_id"] == character
                       for role, character in zip(guest_roles, identities)), returned
            evidence["stock_rest_and_native_wipe"] = {"rest": slept, "respawn": uses_rest, "max_health": 250}
        evidence["verified"] = True
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for process, target in reversed(launched):
            if process.returncode is None:
                try:
                    await command("action", *target, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            evidence["original_saves_unchanged"] = baseline == saved_hashes(vanilla_saved)
            if evidence["original_saves_unchanged"]:
                evidence["original_files_verified"] = len(baseline)
        print(run, flush=True)
        (run / "live-multi-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if vanilla_saved:
            assert evidence["original_saves_unchanged"], "Original singleplayer save files changed"
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--guests", type=int, choices=range(2, 5), default=2,
                         help="number of separate loopback guest processes")
    options.add_argument("--native-rest-wipe", action="store_true", help="rest, leave, and wipe back to the exact stock bed")
    options.add_argument("--native-rest-ui", action="store_true", help="use the stock sleep screen during the rest-wipe check")
    options.add_argument("--native-rest-dead-bridge", action="store_true",
                         help="verify hostile and reputation-eligible Dead Bridge stock sleep and party respawn")
    options.add_argument("--native-rest-guest-denial", action="store_true",
                         help="verify authority and client stock interaction denial for each guest")
    options.add_argument("--native-downed-markers", action="store_true",
                         help="verify exact same-map markers for every observer and cleanup on revival")
    args = options.parse_args()
    if args.native_rest_ui and not (args.native_rest_wipe or args.native_rest_dead_bridge):
        options.error("--native-rest-ui requires --native-rest-wipe")
    asyncio.run(validate(args.work.resolve(), args.vanilla_saved, rest_wipe=args.native_rest_wipe or args.native_rest_dead_bridge,
                         rest_ui=args.native_rest_ui or args.native_rest_dead_bridge,
                         rest_guest_denial=args.native_rest_guest_denial, guest_count=args.guests,
                         downed_markers=args.native_downed_markers, dead_bridge_rest=args.native_rest_dead_bridge))
