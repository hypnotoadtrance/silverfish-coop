"""Keep a downed stock Observatory host in place until its standing saved guest returns."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_ai_smoke import status, wait
from live_downed_input_smoke import input_proof
from live_downed_travel_smoke import HOST_FLAGS, REPO, exact_binding, saved_health
from live_multi_smoke import saved_hashes
from live_reconnect_grace_smoke import manifest_digest

from tools.silverfish import native_host_save
from tools.silverfish.campaign import DEFAULT_RECONNECT_GRACE
from tools.silverfish.party import INTERACT_RANGE
from tools.silverfish.restart_wipe_guard import KEY
from tools.silverfish.travel import OBSERVATORY


HOLD_SECONDS = 4.0


def stock_body(state, alias="local"):
    return state.get("game", {}).get("pawns", {}).get(alias, {})


def downed_host(state, pawn):
    game = state.get("game", {})
    public = state.get("gameplay", {})
    effect = game.get("player_effects", {}).get("local", {})
    body = stock_body(state)
    health = body.get("health")
    return (
        state.get("state") == "hosting" and game.get("map") == OBSERVATORY and body.get("name") == pawn
        and type(health) in (int, float) and math.isfinite(health) and health < body.get("max_health", 0)
        and public.get("wipe") is None
        and public.get("players", {}).get("host", {}).get("downed") is True
        and game.get("native_dead_flags", {}).get("local") is False
        and effect.get("state") == "downed" and effect.get("crouched") is True and effect.get("jump_velocity") == 0
        and bool(input_proof(game, pawn, downed=True, inventory="clear")
                 or input_proof(game, pawn, downed=True, inventory="closed"))
    )


def exact_pair(server, character, *, host_downed):
    if not exact_binding(server, OBSERVATORY, character):
        return False
    players = server["gameplay"]["players"]
    leases = server["campaign"]["leases"]
    host, guest = stock_body(server), stock_body(server, "guest")
    return (
        server["gameplay"]["wipe"] is None and players["host"]["downed"] is host_downed
        and players["guest"]["downed"] is False and type(guest.get("health")) in (int, float) and guest["health"] > 0
        and all(type(axis) in (int, float) for axis in (*host["location"], *guest["location"]))
        and any(row["character_id"] == character and row["state"] == "active" for row in leases)
        and math.dist(host["location"], guest["location"]) < INTERACT_RANGE
    )


def current_guest(client, server, character, *, host_downed):
    return (
        client.get("state") == "connected" and client.get("link_connected") is True
        and client.get("gameplay_fresh") is True and client.get("character_id") == character
        and client.get("host_session") == server["generation"] and client.get("zone_epoch") == server["zone_epoch"]
        and client.get("game", {}).get("map") == OBSERVATORY
        and client.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is host_downed
    )


async def exercise(work: Path, vanilla_saved: Path) -> None:
    name = "restart-wipe-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    host_run, guest_run = campaign / "run", work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("The original Saved directory must contain files")
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_owner, guest_owner = ("--campaign", name), ("--profile", name)
    guest_flags = ("--headless", "--native-zones")
    processes = []
    evidence = {"map": OBSERVATORY, "original_files": len(originals), "original_manifest_sha256": manifest_digest(originals)}

    async def command(*args):
        process = await asyncio.create_subprocess_exec(
            *cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        result = json.loads(output) if output else {}
        if process.returncode or (args[0] != "events" and not result.get("ok")):
            label = args[3] if args[0] == "action" else args[0]
            raise RuntimeError(f"{label} failed: {result.get('error') or error.decode(errors='replace')}")
        return result

    async def action(owner, verb, *args):
        return await command("action", *owner, verb, *args)

    async def launch(kind, owner, label, *args):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        processes.append((process, owner))
        return process

    async def stop(process, owner):
        if process.returncode is None:
            await action(owner, "stop")
        if await asyncio.wait_for(process.wait(), 18) != 0:
            raise RuntimeError(f"The staged {owner[0]} did not stop cleanly")

    async def events(owner):
        return (await command("events", *owner, "--tail", "100"))["events"]

    try:
        authority = await launch("host", host_owner, "host", "--port", "0", "--start-zone", OBSERVATORY, *HOST_FLAGS)
        await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("game", {}).get("map") == OBSERVATORY,
                   timeout=70)
        invitation = await command("invite", *host_owner, "--name", "Observatory medic", "--character", "Returning medic")
        character, guest_id = invitation["character_id"], invitation["guest_id"]
        evidence["character_id"] = character
        guest = await launch("join", guest_owner, "guest", "--invite", invitation["invite_file"], *guest_flags)
        baseline = await wait(host_run, lambda state: exact_pair(state, character, host_downed=False)
                              and state["game"].get("native_death_hook", {}).get("state") == "ready", timeout=60)
        await wait(guest_run, lambda state: current_guest(state, baseline, character, host_downed=False))
        initial_host, initial_guest = stock_body(baseline), stock_body(baseline, "guest")
        port, generation = baseline["endpoint"]["port"], baseline["generation"]
        assert initial_host["health"] == initial_host["max_health"] == 250, initial_host

        damage = (await action(host_owner, "native-host-damage", "--target", "host"))["native_damage_canary"]
        # The probe labels the damaged slot as guest_before/after and the other pawn as host_before/after.
        assert (damage["slot"] == "local" and damage["guest_before"] == initial_host["health"]
                and damage["guest_after"] <= 0 and damage["binding_restored"] is False
                and damage["host_before"] == damage["host_after"] == initial_guest["health"]), damage
        down = await wait(host_run, lambda state: exact_pair(state, character, host_downed=True)
                          and downed_host(state, initial_host["name"]))
        await wait(guest_run, lambda state: current_guest(state, down, character, host_downed=True))
        evidence["lethal_stock_damage"] = damage
        evidence["before_stop"] = {"host": stock_body(down), "guest": stock_body(down, "guest")}

        # Keep the authenticated pawn available for the host's final paired checkpoint.
        await stop(authority, host_owner)
        await stop(guest, guest_owner)
        saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        world = saved["world"]
        game_profile = campaign / "game-user"
        host_frame = native_host_save.verify(game_profile, world)
        guest_saved = saved["characters"][character]["state"]
        guest_frame = native_host_save.guest_record(game_profile, guest_saved["native_guest_save"], character)
        assert host_frame and (host_frame["map"], guest_frame["map"], world["map"]) == (OBSERVATORY,) * 3
        assert host_frame["slot"] != guest_frame["slot"]
        assert world["party"]["players"]["host"]["downed"] is True and world["party"]["players"]["host"]["health"] == 0
        assert world["party"]["wipe"] is None
        assert world["party"]["players"][character]["downed"] is False
        host_hp = saved_health(native_host_save.slot_path(game_profile, host_frame["slot"]).read_bytes())
        assert host_hp < initial_host["max_health"], host_hp
        guest_payload = native_host_save.slot_path(game_profile, guest_frame["slot"]).read_bytes()
        if b"PlayerHealth\x00" in guest_payload:
            guest_hp, guest_hp_encoding = saved_health(guest_payload), "tagged_double"
        else:
            # Stock SaveGame omits unmodified defaults. Capture reloaded and verified PlayerHealth in Unreal.
            guest_hp, guest_hp_encoding = guest_saved["health"], "stock_default_omitted"
            assert guest_hp == initial_guest["max_health"] == 250, guest_saved
        assert guest_hp == guest_saved["health"] == initial_guest["health"], guest_saved
        ticket, = world[KEY]["tickets"]
        assert world[KEY]["map"] == OBSERVATORY and (ticket["guest_id"], ticket["character_id"], ticket["slot"]) == (
            guest_id, character, guest_frame["slot"],
        ), world[KEY]
        assert 0 < ticket["expires"] - time.time() <= DEFAULT_RECONNECT_GRACE, ticket
        evidence["paired_checkpoint"] = {"revision": saved["revision"], "host": host_frame, "host_health": host_hp,
                                         "guest": guest_frame, "guest_health": guest_hp,
                                         "guest_health_encoding": guest_hp_encoding, "ticket": ticket}

        await launch("host", host_owner, "host-restart", "--port", str(port), *HOST_FLAGS)
        restarted = await wait(host_run, lambda state: state.get("state") == "hosting" and state.get("generation") != generation,
                               timeout=70)
        restarted_pawn = stock_body(restarted).get("name")
        restart_generation = restarted["generation"]
        assert restarted["endpoint"]["port"] == port and restarted_pawn, restarted.get("endpoint")
        restored_host, = (row for row in await events(host_owner)
                          if row.get("event") == "native_host_save_restored" and row.get("slot") == host_frame["slot"])
        assert (restored_host["state"], restored_host["map"], restored_host["pawn"]) == (
            "restored", OBSERVATORY, restarted_pawn,
        ), restored_host

        def unattended(state):
            if state.get("generation") != restart_generation or state.get("state") != "hosting":
                return False
            campaign_status, public = state["campaign"], state["gameplay"]
            assert (state["game"]["map"], stock_body(state).get("name")) == (OBSERVATORY, restarted_pawn)
            assert set(public["players"]) == {"host"} and not campaign_status["leases"], campaign_status["leases"]
            assert state.get("link_connected") is False and state["game"]["remote_clients"] == 0
            assert public["players"]["host"]["downed"] is True and public["wipe"] is None, public
            assert state["game"]["native_death_hook"]["sequence"] == 0
            assert campaign_status["world"][KEY] == world[KEY], campaign_status["world"].get(KEY)
            return downed_host(state, restarted_pawn)

        hold_start = time.monotonic()
        held = set()

        def observed_hold(state):
            if unattended(state):
                held.add(state["game"]["sequence"])
            return len(held) >= 4 and time.monotonic() - hold_start >= HOLD_SECONDS and unattended(state)

        await wait(host_run, observed_hold, timeout=16)
        evidence["unattended_restart"] = {"generation": restart_generation, "port": port, "restore": restored_host,
                                          "seconds": round(time.monotonic() - hold_start, 3), "frames": sorted(held)}
        assert time.time() < ticket["expires"], ticket

        refreshed = await action(host_owner, "refresh-invite", "--target", guest_id)
        assert (refreshed["guest_id"], refreshed["character_id"]) == (guest_id, character), refreshed
        await launch("join", guest_owner, "guest-return", "--invite", refreshed["invite_file"], *guest_flags)
        reunited = await wait(host_run, lambda state: state.get("generation") == restart_generation
                             and exact_pair(state, character, host_downed=True) and downed_host(state, restarted_pawn),
                             timeout=min(50, max(1, ticket["expires"] - time.time())))
        await wait(guest_run, lambda state: current_guest(state, reunited, character, host_downed=True))
        binding = exact_binding(reunited, OBSERVATORY, character)
        restored_guest, = (row for row in await events(host_owner) if row.get("event") == "native_guest_save_restored"
                           and row.get("character_id") == character and row.get("slot") == guest_frame["slot"])
        assert (restored_guest["state"], restored_guest["map"], restored_guest["pawn"]) == (
            "restored", OBSERVATORY, binding["pawn"],
        ), restored_guest
        assert stock_body(reunited, "guest")["health"] == guest_hp
        evidence["return"] = {"scope": binding, "restore": restored_guest, "guest": stock_body(reunited, "guest")}

        interaction = await action(guest_owner, "native-interaction-fixture")
        assert interaction["native_interaction_fixture"] is True, interaction
        health = initial_host["max_health"] * 0.1

        def revived(state):
            body = stock_body(state)
            game = state.get("game", {})
            return (
                exact_pair(state, character, host_downed=False)
                and game.get("native_host_revived_health") == health
                and body.get("name") == restarted_pawn and health <= body["health"] <= body["max_health"]
                and bool(input_proof(game, restarted_pawn, downed=False, inventory="clear"))
            )

        standing = await wait(host_run, revived, timeout=18)
        await wait(guest_run, lambda state: current_guest(state, standing, character, host_downed=False))
        assert exact_binding(standing, OBSERVATORY, character) == binding
        assert stock_body(standing, "guest")["health"] == guest_hp
        assert standing["game"]["native_death_hook"]["sequence"] == 0
        revive, = (row for row in await events(guest_owner)
                   if (row.get("event"), row.get("effect"), row.get("target")) == ("local_interaction", "revive", "host"))
        assert revive["health"] == health == max(1, reunited["gameplay"]["players"]["host"]["max_health"] * 0.1), revive
        assert not any(row.get("event") == "party_respawn_committed" for row in await events(host_owner))
        evidence["stock_revive"] = {"receipt": revive, "native_health": health, "host": stock_body(standing),
                                    "guest": stock_body(standing, "guest")}
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error) or type(error).__name__)
        state = status(host_run)
        game = state.get("game", {})
        evidence["last_observation"] = {
            "host": state.get("state"), "guest": status(guest_run).get("state"), "map": game.get("map"),
            "pawn": game.get("pawns"), "effects": game.get("player_effects"), "native_health": game.get("native_host_revived_health"),
            "gameplay": state.get("gameplay"), "ticket": state.get("campaign", {}).get("world", {}).get(KEY),
        }
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await stop(process, owner)
                    except (OSError, RuntimeError, TimeoutError):
                        if process.returncode is None:
                            cleanup = await asyncio.create_subprocess_exec(
                                "taskkill.exe", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            await asyncio.wait_for(cleanup.wait(), 10)
                            await asyncio.wait_for(process.wait(), 10)
        finally:
            unchanged = saved_hashes(vanilla_saved) == originals
            evidence["original_saves_verified"] = unchanged
            evidence["verified"] = unchanged and evidence.get("verified", False)
            (host_run / "live-restart-wipe-guard-verification.json").write_text(json.dumps(evidence, indent=2) + "\n",
                                                                               encoding="utf-8")
            print(json.dumps(evidence), flush=True)
        assert unchanged, "Original single-player Saved files changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated Windows stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, required=True, help="original Saved directory to hash before and after")
    options = parser.parse_args()
    if not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(exercise(options.work.resolve(), options.vanilla_saved.resolve()))
