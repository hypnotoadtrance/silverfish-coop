"""Prove that a native downed guest travels, reloads, and revives as the same character."""

import argparse
import asyncio
import json
import math
from pathlib import Path
from struct import pack, unpack_from
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes
from live_guest_sickness_smoke import tagged_name


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save


SOURCE = "/Game/Classes/Maps/PlaytestMap_01"
DESTINATION = "/Game/Classes/Maps/PlaytestMap_02"
HOST_FLAGS = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
HEALTH_TAG = tagged_name("PlayerHealth") + tagged_name("DoubleProperty") + pack("<iiB", 0, 8, 0)


def saved_health(payload: bytes) -> float:
    """Read the explicitly saved stock PlayerHealth DoubleProperty."""
    before, tag, after = payload.partition(HEALTH_TAG)
    if not before.startswith(b"GVAS") or not tag or len(after) < 8 or HEALTH_TAG in after:
        raise AssertionError("The native guest GVAS does not contain one typed health value")
    value, = unpack_from("<d", after)
    if not math.isfinite(value):
        raise AssertionError("The native guest GVAS health is not finite")
    return value


def exact_binding(server: dict, area: str, character: str) -> dict:
    game = server.get("game", {})
    pawns = game.get("pawns", {})
    guest, host = pawns.get("guest", {}), pawns.get("local", {})
    players = server.get("gameplay", {}).get("players", {})
    roster = game.get("native_roster", {})
    connections = roster.get("connections", [])
    bound = game.get("native_guest_save_members", {}).get(guest.get("name"), game.get("native_guest_save", {}))
    if (server.get("role") != "host" or server.get("state") != "hosting" or not server.get("link_connected")
            or server.get("awaiting_zone_guest") or (game.get("state"), game.get("map")) != ("hosting", area)
            or game.get("remote_clients") != 1 or set(pawns) != {"local", "guest"} or set(players) != {"host", "guest"}
            or players["guest"].get("character_id") != character or players["host"].get("character_id") != "host"
            or roster.get("state") != "qualified" or not roster.get("driver") or len(connections) != 1
            or not guest.get("name") or not guest.get("controller") or guest["name"] == host.get("name")
            or (bound.get("state"), bound.get("pawn")) != ("bound", guest["name"])):
        return {}
    connection = connections[0]
    if ((connection.get("state"), connection.get("pawn"), connection.get("controller"))
            != ("ready", guest["name"], guest["controller"]) or not connection.get("id")):
        return {}
    return {"pawn": guest["name"], "controller": guest["controller"], "driver": roster["driver"],
            "connection": connection["id"], "host_session": server["generation"], "zone_epoch": server["zone_epoch"]}


def server_downed(server: dict) -> bool:
    game = server.get("game", {})
    public = server.get("gameplay", {})
    players = public.get("players", {})
    guest = players.get("guest", {})
    effects = game.get("player_effects", {}).get("guest", {})
    return (guest.get("downed") is True and guest.get("health") == 0 and players.get("host", {}).get("downed") is False
            and public.get("wipe") is None and effects.get("state") == "downed"
            and effects.get("crouched") is True and effects.get("jump_velocity") == 0
            and game.get("native_dead_flags", {}).get("guest") is False)


def client_projection(client: dict) -> dict:
    game = client["game"]
    player = client["gameplay"]["players"]["guest"]
    return {"pawn": game["pawns"]["local"]["name"], "effects": game["player_effects"]["local"],
            "ledger_health": player["health"], "ledger_downed": player["downed"]}


def verify_frame(profile: Path, campaign: dict, character: str, native_hp: float) -> dict:
    guest, = (row for row in campaign["characters"] if row["character_id"] == character)
    world, state = campaign["world"], guest["state"]
    player = world["party"]["players"][character]
    frame = native_host_save.guest_record(profile, state["native_guest_save"], character)
    canonical = native_host_save.verify(profile, world)
    assert canonical and frame["slot"] != canonical["slot"], (canonical, frame)
    assert (world["map"], state["map"], frame["map"]) == (DESTINATION,) * 3, (state, frame)
    assert player["downed"] is True and player["health"] == 0 and world["party"]["wipe"] is None, player
    assert state["health"] == native_hp, state
    payload = native_host_save.slot_path(profile, frame["slot"]).read_bytes()
    assert saved_health(payload) == native_hp, frame
    return {"native": frame, "gvas_health": native_hp, "ledger_health": player["health"], "downed": player["downed"]}


async def exercise(work: Path, vanilla_saved: Path | None) -> None:
    name = "downed-travel-" + uuid.uuid4().hex[:8]
    host_path, client_path = work / "campaigns" / name, work / "clients" / name
    host_run, client_run = host_path / "run", client_path / "run"
    host_run.mkdir(parents=True)
    client_run.mkdir(parents=True)
    print(host_run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    primary, secondary = ("--campaign", name), ("--profile", name)
    processes = []
    evidence = {}

    async def command(*arguments, accepted=True):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != accepted:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        result = json.loads(output)
        assert result.get("ok") is accepted, result
        return result

    async def action(owner, operation, *arguments, accepted=True):
        return await command("action", *owner, operation, *arguments, accepted=accepted)

    async def start(kind, owner, label, *arguments):
        with (host_run / f"{label}.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *owner, *arguments, cwd=REPO, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        processes.append((process, owner))
        return process

    async def client_in(area, identity, *, downed):
        def current(client):
            game = client.get("game", {})
            local = game.get("pawns", {}).get("local", {})
            progress = client.get("gameplay", {})
            player = progress.get("players", {}).get("guest", {})
            effects = game.get("player_effects", {}).get("local", {})
            return (client.get("role") == "client" and client.get("state") == "connected" and client.get("link_connected") is True
                    and client.get("gameplay_fresh") is True and game.get("server_connection") is True
                    and (game.get("map"), client.get("host_session"), client.get("zone_epoch"), client.get("character_id"))
                    == (area, identity["host_session"], identity["zone_epoch"], character)
                    and local.get("name") and player.get("character_id") == character and player.get("downed") is downed
                    and effects.get("state") == ("downed" if downed else "standing")
                    and (not downed or effects.get("crouched") is True and effects.get("jump_velocity") == 0))
        return await wait(client_run, current, timeout=60)

    try:
        server = await start("host", primary, "host", "--port", "0", *HOST_FLAGS)
        initial = await wait(host_run, lambda state: state.get("state") == "hosting"
                             and state.get("game", {}).get("map") == SOURCE)
        port, generation = initial["endpoint"]["port"], initial["generation"]
        campaign_id = initial["campaign"]["campaign_id"]
        invitation = await command("invite", *primary, "--name", "Travel tester", "--character", "Downed traveller")
        character = invitation["character_id"]
        await start("join", secondary, "guest", "--headless", "--native-zones", "--invite", invitation["invite_file"])
        source = await wait(host_run, lambda state: exact_binding(state, SOURCE, character)
                            and state["game"].get("native_death_hook", {}).get("state") == "ready")
        identity = exact_binding(source, SOURCE, character)
        assert identity["host_session"] == generation, identity
        await client_in(SOURCE, identity, downed=False)
        pawns = source["game"]["pawns"]
        assert math.dist(pawns["local"]["location"], pawns["guest"]["location"]) <= 1500, pawns
        assert source["gameplay"]["players"]["guest"]["downed"] is False, source["gameplay"]
        host_hp = pawns["local"]["health"]
        evidence["source_binding"] = identity

        wrong = await action(primary, "native-guest-damage", "--target", identity["pawn"], accepted=False)
        assert wrong["error"] == "Choose a current host or guest role for the native death probe", wrong
        fired = (await action(primary, "native-guest-damage", "--target", "guest"))["native_damage_canary"]
        assert fired["slot"] == "guest" and fired["guest_before"] > 0 >= fired["guest_after"], fired
        assert fired["host_before"] == fired["host_after"] == host_hp and fired["binding_restored"] is False, fired
        downed = await wait(host_run, lambda state: exact_binding(state, SOURCE, character) == identity
                            and server_downed(state)
                            and state["game"].get("native_death_hook", {}).get("sequence") == fired["sequence"])
        assert downed["game"]["pawns"]["guest"]["health"] <= 0, downed["game"]["pawns"]
        source_client = await client_in(SOURCE, identity, downed=True)
        evidence["stock_lethal"] = fired
        evidence["source_projection"] = client_projection(source_client)

        denied = await action(secondary, "travel", "--target", DESTINATION, accepted=False)
        assert denied["error"] == "Action unavailable on the client", denied
        assert status(host_run)["zone_epoch"] == identity["zone_epoch"], denied
        evidence["refusals"] = {"unqualified_pawn_target": wrong["error"], "guest_travel": denied["error"]}
        travel = await action(primary, "travel", "--target", DESTINATION)
        assert travel["map"] == DESTINATION and travel["travel_epoch"] != identity["zone_epoch"], travel
        arrived = await wait(host_run, lambda state: exact_binding(state, DESTINATION, character)
                             and state["generation"] == generation and state["zone_epoch"] == travel["travel_epoch"]
                             and server_downed(state), timeout=60)
        destination = exact_binding(arrived, DESTINATION, character)
        assert destination["pawn"] != identity["pawn"] and destination["driver"] != identity["driver"], destination
        assert arrived["game"]["pawns"]["guest"]["health"] == 1, arrived["game"]["pawns"]
        assert arrived["game"]["pawns"]["local"]["health"] == host_hp, arrived["game"]["pawns"]
        assert arrived["game"]["native_death_hook"]["sequence"] == fired["sequence"], arrived["game"]["native_death_hook"]
        traveled_client = await client_in(DESTINATION, destination, downed=True)
        evidence["travel"] = {"result": travel, "binding": destination, "guest_native_health": 1,
                              "client_projection": client_projection(traveled_client)}

        await action(primary, "checkpoint")
        await action(primary, "stop")
        assert await asyncio.wait_for(server.wait(), 25) == 0, server.returncode
        saved = status(host_run)["campaign"]
        assert saved["campaign_id"] == campaign_id, saved
        evidence["checkpoint"] = before = verify_frame(host_path / "game-user", saved, character, 1.0)

        await start("host", primary, "host-restart", "--port", str(port), *HOST_FLAGS)
        loaded = await wait(host_run, lambda state: exact_binding(state, DESTINATION, character)
                            and state["generation"] != generation and server_downed(state), timeout=60)
        binding = exact_binding(loaded, DESTINATION, character)
        assert loaded["campaign"]["campaign_id"] == campaign_id, loaded["campaign"]
        assert loaded["game"]["pawns"]["guest"]["health"] == 1, loaded["game"]["pawns"]
        assert loaded["game"]["pawns"]["local"]["health"] == host_hp, loaded["game"]["pawns"]
        reloaded_client = await client_in(DESTINATION, binding, downed=True)
        events = (await command("events", *primary, "--tail", "100"))["events"]
        restored, = (event for event in events if event.get("event") == "native_guest_save_restored"
                      and event.get("generation") == binding["host_session"] and event.get("character_id") == character
                      and event.get("pawn") == binding["pawn"])
        assert (restored["slot"], restored["map"]) == (before["native"]["slot"], DESTINATION), restored
        evidence["restart"] = {"binding": binding, "restore_receipt": restored, "guest_native_health": 1,
                               "client_projection": client_projection(reloaded_client)}

        revived = await action(primary, "interact", "--target", character)
        assert (revived["effect"], revived["target"]) == ("revive", character), revived
        maximum = loaded["game"]["pawns"]["guest"]["max_health"]
        expected = max(1, maximum * .10)
        standing = await wait(host_run, lambda state: exact_binding(state, DESTINATION, character) == binding
                              and state["gameplay"]["players"]["guest"]["downed"] is False
                              and state["game"]["player_effects"].get("guest", {}).get("state") == "standing"
                              and state["game"]["pawns"]["guest"]["health"] == expected)
        revived_client = await client_in(DESTINATION, binding, downed=False)
        assert standing["game"]["pawns"]["local"]["health"] == host_hp, standing["game"]["pawns"]
        evidence["revive"] = {"result": revived, "pawn": binding["pawn"], "guest_native_health": expected,
                              "client_projection": client_projection(revived_client)}
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await action(owner, "stop")
                        await asyncio.wait_for(process.wait(), 20)
                    except (OSError, RuntimeError, TimeoutError):
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
            if vanilla_saved:
                evidence["vanilla_unchanged"] = saved_hashes(vanilla_saved) == original
                assert evidence["vanilla_unchanged"], "The original Saved directory changed during the smoke"
        finally:
            (host_run / "live-downed-travel-verification.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path)
    arguments = parser.parse_args()
    asyncio.run(exercise(arguments.work.resolve(), arguments.vanilla_saved))
