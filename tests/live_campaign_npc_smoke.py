"""Observe natural CrossRoads Husk squads on a fresh, fixture-free headless loopback party."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
MAP = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
LEVEL = MAP + ".CrossRoads_P:PersistentLevel."
SPAWNER = re.compile(r"AI_Spawner_Squad_BP_C " + re.escape(LEVEL) + r"AI_Spawner_Squad_BP_C_(\d+)\Z")
MARKERS = frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24,
                     26, 27, 28, 30, 31, 32, 34, 35, 37, 38, 40, 41))
DATA = {
    f"SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/{name}.{name}": size
    for name, size in (("Husks_Scattered", 4), ("Husks_SwarmSmall", 3), ("Husks_SwarmLarge", 5))
}


def status(run: Path) -> dict:
    try:
        return json.loads((run / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_npcs(run: Path, viewer: str) -> dict | None:
    """Read the qualified native receipt and census from one game frame."""
    paths = tuple((run / "game-bridge").glob("*/status.tsv"))
    if len(paths) != 1:
        return None
    phase = "hosting" if viewer == "host" else "connected"
    try:
        rows = [line.split("\t") for line in paths[0].read_text(encoding="utf-8").splitlines()]
        schema, = [row for row in rows if row[0] == "schema"]
        sequence, = [row for row in rows if row[0] == "seq"]
        session, = [row for row in rows if row[0] == "session"]
        if schema != ["schema", "1"] or session[1:4] != [viewer, phase, MAP]:
            return None
        receipt, = [row for row in rows if row[0] == "native-campaign-npc"]
        census, = [row for row in rows if row[0] == "native-campaign-npc-census"]
        if len(receipt) != 10 or len(census) != 10 or receipt[1:3] != [MAP, viewer] or census[1:3] != [MAP, viewer]:
            return None
        members = [row[3] for row in rows if row[0] == "native-campaign-npc-member"
                   and len(row) == 4 and row[1:3] == [MAP, viewer]]
        result = {
            "sequence": int(sequence[1]),
            "receipts": int(receipt[3]),
            "stage": receipt[4],
            "spawner": receipt[5],
            "data": receipt[6],
            "cohort": int(receipt[7]),
            "replicated": int(receipt[8]),
            "spawn_denials": int(receipt[9]),
            "members": members,
        }
        counters = ("natural_calls", "authority", "simulated", "other", "simulated_controllers", "ai_denials",
                    "antfarm_initialized")
        result.update(zip(counters, map(int, census[3:]), strict=True))
        return result
    except (IndexError, OSError, ValueError):
        return None


def stock_cohort(report: dict | None, replicate: bool) -> bool:
    if not report or report["stage"] not in {"stock_initial", "stock_refresh"}:
        return False
    source = SPAWNER.fullmatch(report["spawner"])
    members = report["members"]
    return bool(source and int(source.group(1)) in MARKERS and report["data"] in DATA
                and 0 < report["cohort"] <= DATA[report["data"]]
                and report["natural_calls"] >= report["receipts"] > 0
                and len(set(members)) == len(members) == report["cohort"]
                and all(name.startswith("NPC_Zomb_01_C " + LEVEL) for name in members)
                and report["authority"] >= report["cohort"]
                and report["simulated"] == report["other"] == report["spawn_denials"] == report["ai_denials"] == 0
                and report["replicated"] == (report["cohort"] if replicate else 0))


def client_replica(report: dict | None, cohort: int, require_denial: bool) -> bool:
    return bool(report and report["receipts"] == report["replicated"] == 0
                and report["cohort"] == 0 and not report["members"]
                and report["simulated"] >= cohort > 0
                and report["authority"] == report["other"] == report["simulated_controllers"] == 0
                and (report["spawn_denials"] > 0 or not require_denial))


async def validate(work: Path, vanilla_saved: Path | None, *, replicate: bool, timeout: float,
                   require_client_denial: bool) -> None:
    if os.name != "nt":
        raise OSError("The live canary requires the staged Windows game")
    name = "campaign-npc-" + uuid.uuid4().hex[:8]
    host = work / "campaigns" / name / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    guest.mkdir(parents=True)
    print(host, flush=True)
    primary, secondary = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ, SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC="1")
    for flag in ("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE", "SILVERFISH_COOP_NATIVE_NPC_TEST",
                 "SILVERFISH_COOP_NATIVE_NPC_HOSTILE", "SILVERFISH_COOP_NATIVE_FIXTURES"):
        environment.pop(flag, None)
    if replicate:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE"] = "1"
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    launched = []
    evidence = {"map": MAP, "mode": "replicated" if replicate else "observed"}

    async def command(*arguments):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, cwd=REPO, env=environment, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if not reply.get("ok"):
            raise RuntimeError(f"{arguments} failed: {reply.get('error', 'missing success response')}")
        return reply

    async def start(kind, run, target, *arguments):
        with (run / (kind + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *target, *arguments, cwd=REPO, env=environment,
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        launched.append((process, target))

    async def wait(label, predicate, seconds=timeout):
        end = time.monotonic() + seconds
        snapshot = {}
        while time.monotonic() < end:
            server, client = status(host), status(guest)
            snapshot = {"host_state": server.get("state"), "guest_state": client.get("state"),
                        "host": read_npcs(host, "host"), "guest": read_npcs(guest, "client")}
            if predicate(server, client, snapshot):
                return server, client, snapshot
            if (any(process.returncode is not None for process, _ in launched)
                    or "failed" in (snapshot["host_state"], snapshot["guest_state"])):
                break
            await asyncio.sleep(0.2)
        raise RuntimeError(f"{label}: {json.dumps(snapshot)}")

    def paired(server, client):
        return (server.get("state") == "hosting" and client.get("state") == "connected"
                and server.get("game", {}).get("remote_clients") == 1 and client.get("link_connected") is True
                and server.get("game", {}).get("map") == client.get("game", {}).get("map") == MAP
                and server.get("generation") == client.get("host_session")
                and bool(server.get("zone_epoch")) and server["zone_epoch"] == client.get("zone_epoch"))

    def proved(server, client, snapshot):
        host_npcs, guest_npcs = snapshot["host"], snapshot["guest"]
        if not paired(server, client) or not stock_cohort(host_npcs, replicate) or not guest_npcs:
            return False
        if replicate:
            return client_replica(guest_npcs, host_npcs["cohort"], require_client_denial)
        return guest_npcs["replicated"] == guest_npcs["spawn_denials"] == guest_npcs["ai_denials"] == 0

    try:
        await start("host", host, primary, "--port", "0", "--headless", "--native-zones",
                    "--native-events", "--native-guest", "--start-zone", MAP)
        await wait("The CrossRoads host did not start", lambda server, _, __: server.get("state") == "hosting", 45)
        invitation = await command("invite", *primary, "--name", "Husk canary guest", "--character", "Husk observer")
        await start("join", guest, secondary, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        server, client, snapshot = await wait("No fixture-free stock Husk proof appeared", proved)
        if client.get("character_id") != invitation["character_id"]:
            raise AssertionError("The authenticated guest did not activate its invited character")
        evidence.update({"host": snapshot["host"], "guest": snapshot["guest"], "zone_epoch": server["zone_epoch"]})

        if replicate:
            dropped = await command("action", *secondary, "drop-link")
            if dropped.get("link_connected") is not False:
                raise AssertionError("The guest's Direct IP link did not drop")
            resumed = await command("action", *secondary, "reconnect")
            if resumed.get("link_connected") is not True or resumed.get("resumed") is not True:
                raise AssertionError("The guest's Direct IP link did not resume")

            def after_reconnect(server, client, current):
                return (proved(server, client, current) and server["zone_epoch"] == evidence["zone_epoch"]
                        and client.get("character_id") == invitation["character_id"]
                        and all(current[viewer]["sequence"] > snapshot[viewer]["sequence"] for viewer in ("host", "guest"))
                        and set(snapshot["host"]["members"]) == set(current["host"]["members"]))

            _, _, rejoined = await wait("The stock cohort did not survive the guest reconnect", after_reconnect, 24)
            evidence["reconnected"] = {"host": rejoined["host"], "guest": rejoined["guest"]}
        evidence["verified"] = True
    except BaseException as failure:
        evidence["verified"] = False
        evidence["failure"] = str(failure)
        evidence["failure_frames"] = {"host": read_npcs(host, "host"), "guest": read_npcs(guest, "client")}
        raise
    finally:
        for process, target in reversed(launched):
            if process.returncode is not None:
                continue
            try:
                await command("action", *target, "stop")
                await asyncio.wait_for(process.wait(), 12)
            except (OSError, RuntimeError, TimeoutError):
                if process.returncode is None:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await asyncio.wait_for(killer.wait(), 12)
                    await asyncio.wait_for(process.wait(), 12)
        if vanilla_saved:
            unchanged = saved_hashes(vanilla_saved) == original
            evidence["original_saves_verified"] = unchanged
            if not unchanged:
                evidence["verified"] = False
        (host / "live-campaign-npc-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if vanilla_saved and not evidence["original_saves_verified"]:
            raise AssertionError("Original singleplayer save files changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="root of an already prepared stage")
    parser.add_argument("--vanilla-saved", type=Path, help="optional original Saved directory to hash")
    parser.add_argument("--replicate", action="store_true", help="enable the exact native Husk cohort replication canary")
    parser.add_argument("--require-client-denial", action="store_true", help="also require a naturally attempted client Husk spawn veto")
    parser.add_argument("--timeout", type=float, default=125.0, help="observation window, including the stock 90-second refresh")
    options = parser.parse_args()
    if options.require_client_denial and not options.replicate:
        parser.error("--require-client-denial requires --replicate")
    if not 15 <= options.timeout <= 360:
        parser.error("--timeout must be between 15 and 360 seconds")
    if options.vanilla_saved and not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing directory")
    asyncio.run(validate(options.work.resolve(), options.vanilla_saved, replicate=options.replicate,
                         timeout=options.timeout, require_client_denial=options.require_client_denial))
