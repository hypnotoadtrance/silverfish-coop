"""Observe stock CrossRoads encounters on a headless loopback party."""

import argparse
import asyncio
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save
from tools.silverfish.campaign import CampaignStore


MAP = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
LEVEL = MAP + ".CrossRoads_P:PersistentLevel."
SPAWNER = re.compile(r"AI_Spawner_Squad_BP_C " + re.escape(LEVEL) + r"AI_Spawner_Squad_BP_C_(\d+)\Z")
MARKERS = frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24,
                     26, 27, 28, 30, 31, 32, 34, 35, 37, 38, 40, 41))
DATA = {
    "husk": ({"stock_initial", "stock_refresh"}, "NPC_Zomb_01_C",
             {"Husks_Scattered": 4, "Husks_SwarmSmall": 3, "Husks_SwarmLarge": 5}),
    "flayer": ({"stock_antfarm"}, "NPC_Flayer_C", {"Flayers": 2}),
}
SQUAD_PREFIX = "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/"
FLAYER_REPLAY_SHA = "8f17247a7b18f3e91ba33ec6c4bd72d711935a2217667a5933e8713084e93c95"
FLAYER_REPLAY_SPAWN = [13850.0, -200.0, 132.14999494537]
FLAYER_GEOMETRY_REFUSAL = "stock_flayer_approach_no_verified_position "


def seed_flayer_replay(source: Path, destination: Path) -> dict:
    """Carry over only the verified stock frame and saved CrossRoads host spawn."""
    saved = json.loads((source / "save" / "campaign.json").read_text(encoding="utf-8"))
    old_world = saved["world"]
    expected_host = {"map": MAP, "location": FLAYER_REPLAY_SPAWN, "health": 250.0}
    frame = native_host_save.verify(source / "game-user", old_world)
    if (not frame or frame["map"] != MAP or frame["sha256"] != FLAYER_REPLAY_SHA
            or old_world.get("host") != expected_host):
        raise ValueError("Flayer replay requires the verified d474f2cf stock frame and CrossRoads spawn")
    content = native_host_save.slot_path(source / "game-user", frame["slot"]).read_bytes()
    if sha256(content).hexdigest() != FLAYER_REPLAY_SHA:
        raise ValueError("The immutable Flayer replay frame changed while being read")

    destination.mkdir(parents=True, exist_ok=False)
    profile = destination / "game-user"
    native_host_save.directory(profile).mkdir(parents=True)
    with native_host_save.slot_path(profile, frame["slot"]).open("xb") as stream:
        stream.write(content)
    copied = native_host_save.record(profile, frame["slot"], MAP, flush=True)
    with CampaignStore(destination / "save",
                       verify_checkpoint=lambda state: native_host_save.verify_checkpoint(profile, state)) as campaign:
        campaign.save_world({"map": MAP, "host": expected_host, "native_host_save": copied})
        created = campaign.status()
        if created["campaign_id"] == saved["campaign_id"] or created["characters"] or created["guest_count"]:
            raise RuntimeError("The staged Flayer replay did not create fresh campaign identities")
    return {"frame": copied, "spawn": expected_host, "campaign_id": created["campaign_id"]}


def verify_flayer_restore(run: Path, server: dict, seed: dict) -> dict:
    events = (json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines())
    observed, = (event for event in events if event.get("event") == "native_host_save_restored"
                 and event.get("generation") == server.get("generation"))
    pawn = server["game"]["pawns"]["local"]
    if (server.get("campaign", {}).get("campaign_id") != seed["campaign_id"]
            or (observed.get("state"), observed.get("map"), observed.get("slot"), observed.get("pawn"))
            != ("restored", MAP, seed["frame"]["slot"], pawn.get("name"))
            or math.dist(pawn["location"], seed["spawn"]["location"]) > 2
            or pawn.get("health") != seed["spawn"]["health"]):
        raise AssertionError({"restore": observed, "host_pawn": pawn, "replay": seed})
    return {"receipt": observed, "pawn": pawn}


async def approach_stock_flayer(members, approach, attempts: list[dict]) -> str:
    """Use the original cohort and allow one interval for stock movement."""
    for index, member in enumerate((*members, *members)):
        if index == len(members):
            await asyncio.sleep(1.25)
        reply = await approach(member)
        attempts.append({"target": member, "reply": reply})
        if reply.get("ok") is True:
            return member
        if not str(reply.get("error", "")).startswith(FLAYER_GEOMETRY_REFUSAL):
            raise RuntimeError(f"The staged stock Flayer approach failed: {reply}")
    raise RuntimeError(f"No verified stock melee position for the original Flayer cohort: {attempts}")


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
        network = [row for row in rows if row[0] == "native-campaign-npc-network"]
        if network:
            net, = network
            if len(net) != 10 or net[1:3] != [MAP, viewer]:
                return None
            counts = tuple(map(int, net[3:7]))
            if (any(count < 0 for count in counts) or counts[0] > result["authority"]
                    or counts[3] > result["spawn_denials"]):
                return None
            result["network"] = {
                **dict(zip(("authority_network_ready", "local_authority_begins", "local_authority_initializes",
                            "early_spawn_denials"), counts)),
                **dict(zip(("last_actor", "last_caller", "last_script"), net[7:])),
            }
        saved_squads = [row for row in rows if row[0] == "native-campaign-npc-saved-squads"]
        if saved_squads:
            saved, = saved_squads
            if len(saved) != 8 or saved[1:3] != [MAP, viewer]:
                return None
            result["saved_squads"] = {"calls": int(saved[3]), "denials": int(saved[4]), "stage": saved[5],
                                      "spawner": saved[6], "data": saved[7]}
        approach = [row for row in rows if row[0] == "native-campaign-npc-approach"]
        if approach and viewer == "host":
            move, = approach
            if len(move) != 7 or move[1] != MAP:
                return None
            distances = list(map(float, move[5:7]))
            if not all(math.isfinite(distance) and 0 < distance <= 4000 for distance in distances):
                return None
            result["approach"] = {"target": move[2], "host_pawn": move[3], "guest_pawn": move[4],
                                  "host_distance_cm": distances[0], "guest_distance_cm": distances[1]}
        impacts = [row for row in rows if row[0] == "native-campaign-npc-approach-impact"]
        if impacts and viewer == "host":
            impact, = impacts
            geometry, = [row for row in rows if row[0] == "native-campaign-npc-approach-geometry"]
            move = result.get("approach", {})
            if (len(impact) != 6 or len(geometry) != 8 or not move
                    or impact[1:3] != geometry[1:3] or impact[1:3] != [MAP, move["target"]]):
                return None
            sweep = float(geometry[3])
            floors, placements = map(int, geometry[4:6])
            time_of_impact, hit_distance, penetration_depth = map(float, impact[3:])
            guest_path = move["guest_pawn"].partition(" ")[2]
            component_path = geometry[7].partition(" ")[2]
            if (sweep != 350 or not 0 < placements <= floors or geometry[6] != "guest_hit"
                    or not guest_path or not component_path.startswith(guest_path + ".")
                    or not 0 < time_of_impact <= 1 or not 1 <= hit_distance <= sweep - 25
                    or penetration_depth != 0):
                return None
            result["approach_impact"] = {"target": impact[2], "component": geometry[7], "time": time_of_impact,
                                         "distance_cm": hit_distance, "penetration_depth_cm": penetration_depth}
        refresh = [row for row in rows if row[0] == "native-campaign-npc-refresh"]
        if refresh and viewer == "host":
            entry, = refresh
            if len(entry) != 13 or entry[1] != MAP or entry[2] not in {"inspect", "refresh"}:
                return None
            population, cap, markers, eligible, calls = map(int, entry[4:9])
            before, after = map(int, entry[11:])
            if min(population, cap, markers, eligible, before) < -1 or not 0 <= calls <= 1 or after < before:
                return None
            if (markers >= 0 and eligible > markers) or (entry[2] == "inspect" and (calls != 0 or before != after)):
                return None
            result["stock_refresh"] = {"mode": entry[2], "state": entry[3], "population": population, "capacity": cap,
                                       "markers": markers, "eligible_markers": eligible, "stock_calls": calls,
                                       "spawner": entry[9], "squad_data": entry[10],
                                       "husk_receipts_before": before, "husk_receipts_after": after}
        combat = [row for row in rows if row[0] == "native-campaign-npc-combat"]
        if combat and viewer == "host":
            attack, = combat
            if len(attack) != 16 or attack[1] != MAP:
                return None
            attempts, guest_hits, host_hits = map(int, attack[7:10])
            health = tuple(map(float, attack[10:]))
            if min(attempts, guest_hits, host_hits) < 0 or not all(map(math.isfinite, health)):
                return None
            result["combat"] = {"state": attack[2], "host_pawn": attack[3], "guest_pawn": attack[4],
                                "attacker": attack[5], "currently_targeting_guest": attack[6],
                                "attempts": attempts, "guest_hits": guest_hits, "host_hits": host_hits,
                                **dict(zip(("host_baseline", "host_at_hit", "host_current", "guest_before",
                                            "guest_after", "guest_current"), health))}
        diagnostics = [row for row in rows if row[0] == "native-campaign-npc-combat-diagnostic"]
        if diagnostics and viewer == "host":
            row, = diagnostics
            if len(row) != 7 or row[1] != MAP or row[2] not in {"active", "unavailable"}:
                return None
            calls, attempts = map(int, row[3:5])
            if not 0 <= attempts <= calls:
                return None
            result["combat_diagnostic"] = {"scope": row[2], "calls": calls, "attempts": attempts,
                                           "damage": row[5], "swing": row[6]}
        infection = [row for row in rows if row[0] == "native-campaign-npc-melee-sickness"]
        if infection and viewer == "host":
            hit, = infection
            if len(hit) != 15 or hit[1] != MAP or not all(hit[2:5]):
                return None
            integers = tuple(map(int, hit[5:]))
            if any(value < 0 for value in integers[5:9]) or integers[-1] != 8:
                return None
            fields = ("guest_before", "guest_after", "host_before", "host_after", "host_current",
                      "reads_before", "reads_after", "writes_before", "writes_after", "stock_dose")
            result["melee_sickness"] = {"host_pawn": hit[2], "guest_pawn": hit[3], "attacker": hit[4],
                                        **dict(zip(fields, integers, strict=True))}
        return result
    except (IndexError, OSError, ValueError):
        return None


def stock_cohort(report: dict | None, replicate: bool, family: str = "husk") -> bool:
    stages, actor, squads = DATA[family]
    if not report or report["stage"] not in stages:
        return False
    source = SPAWNER.fullmatch(report["spawner"])
    members = report["members"]
    maximum = next((size for name, size in squads.items() if report["data"] == f"{SQUAD_PREFIX}{name}.{name}"), 0)
    return bool(source and int(source.group(1)) in MARKERS
                and 0 < report["cohort"] <= maximum
                and report["natural_calls"] >= report["receipts"] > 0
                and len(set(members)) == len(members) == report["cohort"]
                and all(name.startswith(f"{actor} {LEVEL}{actor}_") for name in members)
                and report["authority"] >= report["cohort"]
                and report["simulated"] == report["other"] == report["spawn_denials"] == report["ai_denials"] == 0
                and report["replicated"] == (report["cohort"] if replicate else 0)
                and (not replicate or report.get("network", {}).get("authority_network_ready", 0) >= report["cohort"]))


def client_replica(report: dict | None, cohort: int, require_denial: bool) -> bool:
    return bool(report and report["receipts"] == report["replicated"] == 0
                and report["cohort"] == 0 and not report["members"]
                and report["simulated"] >= cohort > 0
                and report["authority"] == report["other"] == report["simulated_controllers"] == 0
                and report.get("network", {}).get("local_authority_begins") == 0
                and report["network"]["local_authority_initializes"] == 0
                and (report["spawn_denials"] > 0 or not require_denial))


def stock_guest_hit(report: dict, host: str, guest: str, members: list[str]) -> bool:
    attack = report.get("combat", {})
    return (attack.get("state") == "stock_guest_health_debit" and attack["host_pawn"] == host
            and attack["guest_pawn"] == guest and attack["attacker"] in members
            and attack["attempts"] >= attack["guest_hits"] > 0 and attack["host_hits"] == 0
            and attack["host_baseline"] == attack["host_at_hit"] == attack["host_current"] > 0
            and attack["guest_after"] < attack["guest_before"]
            and attack["guest_current"] <= max(1, attack["guest_after"]))


def healthy_guest_sickness(server: dict, pawn: str) -> dict:
    rows = server.get("game", {}).get("native_guest_sickness", {})
    row = rows.get(pawn, {})
    counters = ("infect_reads", "infect_writes", "slow_reads", "slow_writes")
    return row if (set(rows) == {pawn} and row.get("state") in {"watching", "redirected_infection", "redirected_sickness_tick"}
                   and all(type(row.get(key)) is int and row[key] >= 0 for key in counters)
                   and all(type(row.get(key)) is int for key in ("guest_sickness", "host_sickness"))) else {}


def stock_flayer_infection(server: dict, report: dict) -> bool:
    damage, infection = report.get("combat", {}), report.get("melee_sickness", {})
    if not infection or not damage:
        return False
    scoped = healthy_guest_sickness(server, infection["guest_pawn"])
    exact = ("host_pawn", "guest_pawn", "attacker")
    return bool(scoped and all(infection[key] == damage[key] for key in exact)
                and 1 <= infection["guest_after"] - infection["guest_before"] <= infection["stock_dose"] == 8
                and infection["host_before"] == infection["host_after"] == infection["host_current"] == scoped["host_sickness"]
                and scoped["infect_reads"] >= infection["reads_after"] == infection["reads_before"] + 1
                and scoped["infect_writes"] >= infection["writes_after"] == infection["writes_before"] + 1)


async def validate(work: Path, vanilla_saved: Path | None, *, replicate: bool, timeout: float,
                   require_client_denial: bool, approach: bool = False, family: str = "husk",
                   flayer_veto: bool = False, join_only: bool = False, combat: bool = False,
                   stock_refresh: bool = False, flayer_replay: Path | None = None) -> None:
    if os.name != "nt":
        raise OSError("The live canary requires the staged Windows game")
    name = "campaign-npc-" + uuid.uuid4().hex[:8]
    campaign_root = work / "campaigns" / name
    prepared = seed_flayer_replay(flayer_replay, campaign_root) if flayer_replay else None
    host = campaign_root / "run"
    guest = work / "clients" / name / "run"
    host.mkdir(parents=True)
    guest.mkdir(parents=True)
    print(host, flush=True)
    primary, secondary = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    environment = dict(os.environ, SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC="1")
    for flag in ("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE", "SILVERFISH_COOP_NATIVE_NPC_TEST",
                 "SILVERFISH_COOP_NATIVE_NPC_HOSTILE", "SILVERFISH_COOP_NATIVE_FIXTURES",
                 "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW", "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_PUBLISH",
                 "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_VETO", "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_COMBAT",
                 "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH"):
        environment.pop(flag, None)
    if replicate:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE"] = "1"
    if combat:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_COMBAT"] = "1"
    if stock_refresh:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH"] = "1"
    if family == "flayer":
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW"] = family
        if replicate:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_PUBLISH"] = "1"
        if flayer_veto:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_VETO"] = "1"
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    launched = []
    flayer_combat = combat and family == "flayer"
    evidence = {"map": MAP, "family": family, "mode": "replicated" if replicate else "observed",
                "verification_scope": "join" if join_only else "combat" if combat else "encounter", "approach_requested": approach,
                "flayer_publication": family == "flayer" and replicate, "flayer_spawn_veto": flayer_veto,
                "stock_refresh_requested": stock_refresh, "detached_sickness_required": flayer_combat}
    if prepared:
        evidence["flayer_replay"] = {"prepared": prepared}
    last_frames = {"host": None, "guest": None}
    client_history = {"frames": 0, "network_frames": 0, "max_local_authority": 0,
                      "max_local_begins": 0, "max_local_initializations": 0,
                      "max_spawn_denials": 0, "max_early_denials": 0}
    evidence["client_history"] = client_history

    def frames():
        current = {"host": read_npcs(host, "host"), "guest": read_npcs(guest, "client")}
        for viewer, frame in current.items():
            if frame is None or (last_frames[viewer] and frame["sequence"] <= last_frames[viewer]["sequence"]):
                continue
            last_frames[viewer] = frame
            if viewer == "host":
                if stock_cohort(frame, replicate, family):
                    evidence.setdefault("first_host_cohort", frame)
                if diagnostic := frame.get("combat_diagnostic"):
                    evidence.setdefault("first_combat_diagnostic", diagnostic)
                    evidence["last_combat_diagnostic"] = diagnostic
                    if diagnostic["calls"]:
                        evidence.setdefault("first_stock_damage_diagnostic", diagnostic)
                continue
            client_history["frames"] += 1
            client_history["max_local_authority"] = max(client_history["max_local_authority"], frame["authority"])
            client_history["max_spawn_denials"] = max(client_history["max_spawn_denials"], frame["spawn_denials"])
            if network := frame.get("network"):
                client_history["network_frames"] += 1
                for maximum, key in (("max_local_begins", "local_authority_begins"),
                                     ("max_local_initializations", "local_authority_initializes"),
                                     ("max_early_denials", "early_spawn_denials")):
                    client_history[maximum] = max(client_history[maximum], network[key])
            if (client_history["max_local_authority"] or client_history["max_local_begins"]
                    or client_history["max_local_initializations"]):
                evidence.setdefault("first_client_local_birth_frame", frame)
        return current

    async def command(*arguments, allow_flayer_geometry_refusal=False):
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
        reply = json.loads(output) if output and (not process.returncode or allow_flayer_geometry_refusal) else {}
        if (allow_flayer_geometry_refusal and reply.get("ok") is False
                and str(reply.get("error", "")).startswith(FLAYER_GEOMETRY_REFUSAL)):
            return reply
        if process.returncode:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
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
                        **frames()}
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
        if not paired(server, client) or not stock_cohort(host_npcs, replicate, family) or not guest_npcs:
            return False
        if replicate:
            return (client_replica(guest_npcs, host_npcs["cohort"], require_client_denial)
                    and "first_client_local_birth_frame" not in evidence)
        return guest_npcs["replicated"] == guest_npcs["spawn_denials"] == guest_npcs["ai_denials"] == 0

    def approach_pair(server, *, standing=True):
        game = server.get("game", {})
        pawns = game.get("pawns", {})
        host_pawn, guest_pawn = pawns.get("local", {}), pawns.get("guest", {})
        host_name, guest_name = host_pawn.get("name"), guest_pawn.get("name")
        if not host_name or not guest_name or host_name == guest_name:
            return None
        scope = game.get("native_guest_save_members", {}).get(guest_name) or game.get("native_guest_save", {})
        connections = game.get("native_roster", {}).get("connections", [])
        participant = server.get("gameplay", {}).get("players", {}).get("guest", {})
        if (scope.get("state") != "bound" or scope.get("pawn") != guest_name or len(connections) != 1
                or connections[0].get("state") != "ready" or connections[0].get("pawn") != guest_name
                or connections[0].get("controller") != guest_pawn.get("controller")
                or participant.get("character_id") != invitation["character_id"]
                or (standing and participant.get("downed") is not False)):
            return None
        return host_name, guest_name

    try:
        fixture_flags = ("--fixtures",) if approach else ()
        if flayer_combat:
            fixture_flags += ("--native-guest-sickness",)
        await start("host", host, primary, "--port", "0", "--headless", "--native-zones",
                    "--native-events", "--native-guest", "--start-zone", MAP, *fixture_flags)
        initial, _, _ = await wait("The CrossRoads host did not start",
                                   lambda server, _, __: server.get("state") == "hosting", 45)
        if prepared:
            evidence["flayer_replay"]["adopted"] = verify_flayer_restore(host, initial, prepared)
        invitation = await command("invite", *primary, "--name", "Encounter canary guest", "--character", "Encounter observer")
        await start("join", guest, secondary, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        if join_only:
            def initial_join(server, client, snapshot):
                return (paired(server, client) and snapshot["guest"] is not None
                        and stock_cohort(snapshot["host"], replicate, family)
                        and client.get("character_id") == invitation["character_id"])

            _, _, joined = await wait(f"The guest did not finish joining the stock {family} world", initial_join)
            evidence.update({"verified": True, "host": joined["host"], "guest": joined["guest"]})
            return
        if stock_refresh:
            await wait("No exact enrolled pair for the stock director inspection",
                       lambda server, client, _: paired(server, client) and approach_pair(server), 35)

            async def director_report(mode):
                await command("action", *primary, "native-campaign-npc-refresh", "--target", mode)
                _, _, snapshot = await wait(f"Stock director did not report {mode}", lambda _, __, frame:
                    (frame["host"] or {}).get("stock_refresh", {}).get("mode") == mode, 6)
                return snapshot["host"]

            inspection = await director_report("inspect")
            evidence["stock_refresh_inspection"] = inspection["stock_refresh"]
            if inspection["stock_refresh"]["state"] == "eligible" and not stock_cohort(inspection, replicate, family):
                refreshed = await director_report("refresh")
                evidence["stock_refresh_result"] = refreshed["stock_refresh"]
        if approach:
            def ready(server, client, snapshot):
                pair = approach_pair(server)
                return (paired(server, client) and stock_cohort(snapshot["host"], True, family) and pair
                        and (not flayer_combat or healthy_guest_sickness(server, pair[1])))

            before, _, natural = await wait(f"No stock {family} cohort and enrolled pair appeared", ready)
            evidence["before_approach"] = {"host": natural["host"], "guest": natural["guest"]}
            host_pawn, guest_pawn = approach_pair(before)
            members = natural["host"]["members"]
            if flayer_combat:
                async def stock_approach(member):
                    return await command("action", *primary, "native-campaign-npc-approach", "--target", member,
                                         allow_flayer_geometry_refusal=True)

                attempts = evidence["flayer_approach_attempts"] = []
                target = await approach_stock_flayer(members, stock_approach, attempts)
            else:
                target = members[0]
                await command("action", *primary, "native-campaign-npc-approach", "--target", target)

            def approached(server, client, snapshot):
                move = (snapshot["host"] or {}).get("approach", {})
                return (proved(server, client, snapshot) and move.get("target") == target
                        and (move.get("host_pawn"), move.get("guest_pawn")) == (host_pawn, guest_pawn)
                        and approach_pair(server, standing=not combat) == (host_pawn, guest_pawn)
                        and target in snapshot["host"]["members"]
                        and (not combat or stock_guest_hit(snapshot["host"], host_pawn, guest_pawn, natural["host"]["members"]))
                        and (not flayer_combat or (snapshot["host"].get("approach_impact", {}).get("target") == target
                                                  and stock_flayer_infection(server, snapshot["host"]))))

            label = (f"No stock {family} attack verified detached guest combat" if combat
                     else "The approached cohort did not yield stock client replicas")
            server, client, snapshot = await wait(label, approached, 38 if combat else 45)
            evidence["approach"] = snapshot["host"]["approach"]
            if combat:
                evidence["stock_combat"] = snapshot["host"]["combat"]
            if flayer_combat:
                evidence["flayer_approach_impact"] = snapshot["host"]["approach_impact"]
                evidence["stock_melee_sickness"] = snapshot["host"]["melee_sickness"]
        else:
            server, client, snapshot = await wait(f"No fixture-free stock {family} proof appeared", proved)
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
        frames()
        evidence["failure_frames"] = last_frames
        connection = {}
        for viewer, run in (("host", host), ("guest", guest)):
            current = status(run)
            game = current.get("game", {})
            connection[viewer] = {"service_state": current.get("state"), "link_connected": current.get("link_connected"),
                                  **{field: game.get(field) for field in ("state", "sequence", "map",
                                                                          "remote_clients", "server_connection")}}
        evidence["failure_connections"] = connection
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
    parser.add_argument("--cohort", choices=tuple(DATA), default="husk", help="stock encounter family to verify")
    parser.add_argument("--flayer-veto", action="store_true", help="also enable the experimental guest Flayer spawn veto")
    parser.add_argument("--flayer-replay", type=Path, help="replay the verified d474f2cf CrossRoads campaign directory")
    parser.add_argument("--combat", action="store_true", help="observe a stock melee attack against the exact detached guest")
    parser.add_argument("--stock-refresh", action="store_true",
                        help="inspect stock Husk reinforcement readiness and permit one eligible stock director refresh")
    parser.add_argument("--join-only", action="store_true", help="verify stock cohort start and guest map admission only")
    parser.add_argument("--replicate", action="store_true", help="enable the exact native encounter cohort replication canary")
    parser.add_argument("--approach", action="store_true",
                        help="bring the paired pawns near a naturally spawned stock cohort after verification")
    parser.add_argument("--require-client-denial", action="store_true", help="also require a naturally attempted client spawn veto")
    parser.add_argument("--timeout", type=float, default=125.0, help="observation window, including the stock 90-second refresh")
    options = parser.parse_args()
    if options.combat or options.stock_refresh:
        if options.join_only:
            parser.error("--combat and --stock-refresh require full encounter verification")
        options.replicate = options.approach = True
    if options.stock_refresh and options.cohort != "husk":
        parser.error("--stock-refresh requires the Husk encounter test")
    if options.combat and options.cohort == "flayer":
        options.flayer_veto = True
    if (options.require_client_denial or options.approach) and not options.replicate:
        parser.error("--require-client-denial and --approach require --replicate")
    if options.flayer_veto and options.cohort != "flayer":
        parser.error("--flayer-veto requires --cohort flayer")
    if options.flayer_replay and (options.cohort != "flayer" or not options.flayer_replay.is_dir()):
        parser.error("--flayer-replay requires --cohort flayer and the verified d474f2cf campaign directory")
    if options.require_client_denial and options.cohort == "flayer" and not options.flayer_veto:
        parser.error("--require-client-denial for Flayers requires --flayer-veto")
    if options.join_only and (options.approach or options.require_client_denial):
        parser.error("--join-only excludes encounter approach and spawn denial proof")
    if not 15 <= options.timeout <= 360:
        parser.error("--timeout must be between 15 and 360 seconds")
    if options.vanilla_saved and not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing directory")
    asyncio.run(validate(options.work.resolve(), options.vanilla_saved, replicate=options.replicate,
                         timeout=options.timeout, require_client_denial=options.require_client_denial,
                         approach=options.approach, family=options.cohort, flayer_veto=options.flayer_veto,
                         join_only=options.join_only, combat=options.combat, stock_refresh=options.stock_refresh,
                         flayer_replay=options.flayer_replay))
