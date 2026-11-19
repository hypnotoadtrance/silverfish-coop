"""Exercise the actual Playtest Bandit on a temporary, headless loopback party."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
MAP = "/Game/Classes/Maps/PlaytestMap_01"


def status(directory: Path) -> dict:
    try:
        return json.loads((directory / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def bandit(state: dict) -> dict:
    return state.get("game", {}).get("native_bandit", {})


async def wait(directory: Path, predicate, *, timeout=42) -> dict:
    deadline = time.monotonic() + timeout
    current = {}
    while time.monotonic() < deadline:
        current = status(directory)
        if predicate(current):
            return current
        if current.get("state") == "failed":
            break
        await asyncio.sleep(0.2)
    raise RuntimeError(f"{directory.parent.name} did not reach the expected fixture state: {current}")


async def exercise(work: Path, subject: str, *, hostile: bool, require_combat: bool, require_movement: bool,
                   require_damage: bool) -> dict:
    profile = f"bandit-{subject}-{uuid.uuid4().hex[:8]}"
    run = work / "campaigns" / profile / "run"
    guest_run = work / "clients" / profile / "run"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ, SILVERFISH_COOP_NATIVE_NPC_TEST="1")
    environment.pop("SILVERFISH_COOP_NATIVE_NPC_HOSTILE", None)
    if hostile:
        environment["SILVERFISH_COOP_NATIVE_NPC_HOSTILE"] = "1"
    processes = []
    print(run, flush=True)

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if not reply.get("ok"):
            raise RuntimeError(f"{arguments}: {reply}")
        return reply

    async def start(log_name, controller, *arguments):
        run.mkdir(parents=True, exist_ok=True)
        with (run / log_name).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, env=environment,
                stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))

    host = ("--campaign", profile)
    client = ("--profile", profile)
    evidence = {"subject": subject, "hostile": hostile}
    try:
        await start("host.txt", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                    "--native-guest", "--native-zones", "--start-zone", MAP)
        await wait(run, lambda state: state.get("state") == "hosting"
                   and bandit(state).get("status") == "waiting_for_stock_bandit")
        invitation = await command("invite", *host, "--name", "Loopback Bandit observer", "--character", "Bandit observer")
        await start("client.txt", client, "join", *client, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and bandit(state).get("status") == "waiting_for_stock_bandit")
        paired = await wait(run, lambda state: bandit(state).get("watched_pawn") and
                            state.get("game", {}).get("native_roster", {}).get("count") == 1)
        pair_report = bandit(paired)
        exact_pawn = pair_report["local_pawn" if subject == "host" else "watched_pawn"]
        if not exact_pawn:
            raise AssertionError(pair_report)

        denied = (await command("action", *client, "native-bandit-client-spawner-probe"))["native_bandit"]
        assert denied["spawner_denials"] > 0 and denied["authority_count"] == denied["simulated_count"] == 0, denied
        evidence["stock_client_spawn_denied"] = denied

        await command("action", *host, "native-bandit-position", "--target", subject)
        await command("action", *host, "native-bandit-spawn")
        server_spawn = bandit(await wait(run, lambda state: bandit(state).get("status") == "stock_bandit_authority"
                                             and bandit(state).get("initialized")))
        replica_spawn = bandit(await wait(guest_run, lambda state: bandit(state).get("status") == "stock_bandit_replica"
                                              and bandit(state).get("replica_ai_denials", 0) > 0))
        assert server_spawn["role"] == 3 and server_spawn["authority_count"] == 1 and server_spawn["simulated_count"] == 0, server_spawn
        assert server_spawn["replicates"] and server_spawn["replicate_movement"] and server_spawn["alive"], server_spawn
        assert server_spawn["controller"].startswith("MasterNPC_AI_Controler_BP_C "), server_spawn
        assert replica_spawn["role"] == 1 and replica_spawn["authority_count"] == 0 and replica_spawn["simulated_count"] == 1, replica_spawn
        assert not replica_spawn["initialized"] and replica_spawn["controller"] is None, replica_spawn
        if hostile:
            assert server_spawn.get("combat", {}).get("armed") is True, server_spawn
        evidence["authority"] = server_spawn
        evidence["simulated_proxy"] = replica_spawn

        sight_field = "perceives_local" if subject == "host" else "perceives_watched"
        sight_state = bandit(await wait(run, lambda state: bandit(state).get("perception_available")
                                         and bandit(state).get(sight_field)
                                         and (not (hostile or require_combat)
                                              or bandit(state).get("combat_target") == exact_pawn)))
        assert sight_state["local_pawn" if subject == "host" else "watched_pawn"] == exact_pawn, sight_state
        evidence["stock_sight"] = {"exact_subject": exact_pawn, "observed": sight_state,
                                   "combat_target_matched": sight_state["combat_target"] == exact_pawn}

        movement, damage = None, None
        other = "guest" if subject == "host" else "host"
        movement_deadline = time.monotonic() + 8
        while time.monotonic() < movement_deadline:
            server = bandit(status(run))
            replica = bandit(status(guest_run))
            if server.get("npc") == server_spawn["npc"] and replica.get("npc") == replica_spawn["npc"]:
                if not movement and (math.dist(server["location"], server_spawn["location"]) >= 60
                    and math.dist(replica["location"], replica_spawn["location"]) >= 60
                    and math.dist(server["location"], replica["location"]) <= 400):
                    movement = {"authority_location": server["location"], "replica_location": replica["location"]}
                if hostile and not damage:
                    combat = server.get("combat", {})
                    if combat.get(f"{subject}_hits", 0) > 0 and combat.get(f"{subject}_health_lost", 0) > 0:
                        damage = {**combat, "other_player_untouched": combat.get(f"{other}_hits") == 0}
                if movement and (not hostile or damage):
                    break
            await asyncio.sleep(0.2)
        evidence["replicated_stock_movement"] = movement
        if hostile:
            evidence["stock_attack_damage"] = damage
        if require_movement and not movement:
            raise AssertionError("The Bandit did not show enough stock movement to verify replica motion")
        if require_damage and not (damage and damage["other_player_untouched"]):
            raise AssertionError("The Bandit did not damage the exposed pawn independently of the other pawn")
    finally:
        for process, controller in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *controller, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if evidence:
            (run / "live-ai-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)
    return evidence


async def main(args) -> None:
    subjects = ("host", "guest") if args.subject == "both" else (args.subject,)
    for subject in subjects:
        await exercise(args.work.resolve(), subject, hostile=args.hostile, require_combat=args.require_combat,
                       require_movement=args.require_movement, require_damage=args.require_damage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--subject", choices=("host", "guest", "both"), default="both")
    parser.add_argument("--hostile", action="store_true", help="use the stock aggressive faction response for the staged Bandit")
    parser.add_argument("--require-combat", action="store_true")
    parser.add_argument("--require-movement", action="store_true")
    parser.add_argument("--require-damage", action="store_true")
    args = parser.parse_args()
    if args.require_damage and not args.hostile:
        parser.error("--require-damage requires --hostile")
    asyncio.run(main(args))
