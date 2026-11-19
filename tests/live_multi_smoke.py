"""Exercise the staged host and two independent guests on one Windows PC."""

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
OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"


def saved_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = file_digest(stream, "sha256").hexdigest()
    return result


async def validate(work: Path, vanilla_saved: Path | None, *, rest_wipe: bool = False, rest_ui: bool = False,
                   rest_guest_denial: bool = False) -> None:
    campaign = "party-" + uuid.uuid4().hex[:8]
    profiles = [f"{campaign}-{index}" for index in (1, 2)]
    run = work / "campaigns" / campaign / "run"
    clients = [work / "clients" / profile / "run" for profile in profiles]
    base = [sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work)]
    launched = []
    baseline = saved_hashes(vanilla_saved) if vanilla_saved else None
    evidence = {}

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(*base, *arguments, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is False:
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

    try:
        await start("host.txt", ("--campaign", campaign),
                    ("host", "--campaign", campaign, "--port", "0", "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--max-guests", "2", "--start-zone", CROSSROADS))
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

        before = await wait(run, lambda status: bound(status, 2) and status["game"]["native_roster"]["count"] == 2)
        exact_pawns = before["gameplay"]["native_pawns"]
        assert len(set(exact_pawns.values())) == 3 and players(before)["guest2"]["character_id"] == identities[1], before
        evidence["native_pawns"] = exact_pawns

        await command("action", "--campaign", campaign, "native-guest-damage", "--target", "guest2")
        down = await wait(run, lambda status: players(status).get("guest2", {}).get("downed", False)
                          and (native_body(status, exact_pawns["guest2"]).get("health") or 0) <= 0)
        assert not players(down)["host"]["downed"] and not players(down)["guest"]["downed"], down
        await command("action", "--profile", profiles[0], "interact", "--target", identities[1])
        revived = await wait(run, lambda status: not players(status).get("guest2", {}).get("downed", True)
                             and native_body(status, exact_pawns["guest2"]).get("health") == 25.0)
        evidence["guest2_revived_by_guest1"] = native_body(revived, exact_pawns["guest2"])["health"]

        await command("action", "--profile", profiles[0], "drop-link")
        await wait(clients[0], lambda status: not status.get("link_connected", True))
        await command("action", "--profile", profiles[0], "reconnect")
        resumed = await wait(run, lambda status: bound(status, 2))
        assert resumed["gameplay"]["native_pawns"] == exact_pawns, resumed
        evidence["resume_retained_both_pawns"] = True

        async def position(role, where):
            return await command("action", "--campaign", campaign, "native-exit-position",
                                 "--target", role, "--exit-position", where, "--entrance", "Obsv_Etr")

        await position("guest", "near")
        await position("guest2", "far")
        await position("host", "inside")
        refused = await wait(run, lambda status: status.get("game", {}).get("native_zone", {}).get("decision") == "too_far")
        assert refused["game"]["map"] == CROSSROADS and refused["zone_epoch"] == before["zone_epoch"], refused
        await position("host", "near")
        await position("guest2", "near")
        await wait(run, lambda status: bound(status, 2) and all(
            math.dist(native_body(status, exact_pawns["host"])["location"], native_body(status, exact_pawns[role])["location"]) <= 1500
            for role in ("guest", "guest2")))
        await position("host", "inside")
        arrived = await wait(run, lambda status: bound(status, 2) and status["game"]["map"] == OBSERVATORY
                              and not status.get("awaiting_zone_guest"))
        for client in clients:
            await wait(client, lambda status: status.get("state") == "connected" and status["game"]["map"] == OBSERVATORY)
        assert arrived["zone_epoch"] != before["zone_epoch"] and all(
            players(arrived)[role]["character_id"] == identities[index] for index, role in enumerate(("guest", "guest2"))
        ), arrived
        evidence["both_guests_required_and_travelled"] = {"map": OBSERVATORY, "identities": identities}

        await command("action", "--campaign", campaign, "checkpoint")
        saved = await wait(run, lambda status: all(
            entry["state"].get("native_guest_save", {}).get("map") == OBSERVATORY
            for entry in status.get("campaign", {}).get("characters", [])))
        characters = saved["campaign"]["characters"]
        frames = {entry["character_id"]: entry["state"]["native_guest_save"] for entry in characters}
        assert set(frames) == set(identities) and len({record["slot"] for record in frames.values()}) == 2, frames
        evidence["separate_native_guest_frames"] = frames
        if rest_guest_denial:
            def armed(status):
                return status.get("game", {}).get("native_rest_guard", {}).get("armed") is True

            host_guard = await wait(run, lambda status: armed(status)
                                    and status["game"].get("native_rest_hook", {}).get("fresh") is True)
            client_guards = [await wait(client, armed) for client in clients]
            prior = host_guard["game"]["native_rest_guard"]["guest_denials"]
            denied = []
            for index, role in enumerate(("guest", "guest2")):
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
            approach = await command("action", "--campaign", campaign, "native-rest-approach")
            assert approach["native_rest"]["eligible"], approach
            if rest_ui:
                await wait(run, lambda status: status.get("game", {}).get("native_rest_hook", {}).get("fresh") is True)
                confirmation = await command("action", "--campaign", campaign, "native-rest-ui-fixture", "--amount", "1")
                assert confirmation["native_rest_hook"]["status"] == "selected", confirmation
                await wait(run, lambda status: status.get("campaign", {}).get("world", {}).get("native_rest", {})
                           .get("bed") == "SavePoint_BP_C_3-Observitory_P-")
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
            assert slept["bed"] == "SavePoint_BP_C_3-Observitory_P-" and slept["after"] - slept["before"] == 3600, slept
            for role in ("host", "guest", "guest2"):
                await position(role, "near")
            await position("host", "inside")
            await wait(run, lambda status: bound(status, 2) and status["game"]["map"] == CROSSROADS
                       and not status.get("awaiting_zone_guest"))
            for role in ("guest", "guest2"):
                await command("action", "--campaign", campaign, "native-guest-damage", "--target", role)
                await wait(run, lambda status: players(status).get(role, {}).get("downed"))
            await command("action", "--campaign", campaign, "native-host-damage", "--target", "host")
            returned = await wait(run, lambda status: bound(status, 2) and status["game"]["map"] == OBSERVATORY
                                  and not status["gameplay"].get("wipe") and not status["gameplay"].get("pending_spawns")
                                  and all(native_body(status, pawn).get("health") == 250.0
                                          for pawn in status["gameplay"]["native_pawns"].values()))
            for client in clients:
                await wait(client, lambda status: status.get("state") == "connected" and status["game"]["map"] == OBSERVATORY)
            uses_rest = returned["game"].get("native_blue_arrival") or next(
                row for row in (await command("events", "--campaign", campaign, "--tail", "100"))["events"]
                if row["event"] == "party_respawn_committed")
            assert math.dist(uses_rest["location"], slept["location"]) < 400, uses_rest
            assert all(players(returned)[role]["character_id"] == identities[index]
                       for index, role in enumerate(("guest", "guest2"))), returned
            evidence["stock_rest_and_native_wipe"] = {"rest": slept, "respawn": uses_rest, "max_health": 250}
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
            assert baseline == saved_hashes(vanilla_saved), "Original singleplayer save files changed"
            evidence["original_files_verified"] = len(baseline)
        print(run, flush=True)
    (run / "live-multi-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--native-rest-wipe", action="store_true", help="rest, leave, and wipe back to the exact stock bed")
    options.add_argument("--native-rest-ui", action="store_true", help="use the stock sleep screen during the rest-wipe check")
    options.add_argument("--native-rest-guest-denial", action="store_true",
                         help="verify authority and client stock interaction denial for each guest")
    args = options.parse_args()
    if args.native_rest_ui and not args.native_rest_wipe:
        options.error("--native-rest-ui requires --native-rest-wipe")
    asyncio.run(validate(args.work.resolve(), args.vanilla_saved, rest_wipe=args.native_rest_wipe,
                         rest_ui=args.native_rest_ui, rest_guest_denial=args.native_rest_guest_denial))
