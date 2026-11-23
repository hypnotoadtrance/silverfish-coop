"""Observe stock campaign encounters on a headless loopback party."""

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

from live_multi_smoke import OBSERVATORY, saved_hashes
from live_guest_held_fire_smoke import events


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save
from tools.silverfish.campaign import CampaignStore
from ruin_npc_probe import (
    RUIN, RUIN_LEVEL, capture, compare, installed_probe, read_director_trace, request as ruin_probe,
    stock_refresh as probe_ruin_refresh,
)


MAP = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
LEVEL = MAP + ".CrossRoads_P:PersistentLevel."
SPAWNER = re.compile(r"AI_Spawner_Squad_BP_C " + re.escape(LEVEL) + r"AI_Spawner_Squad_BP_C_(\d+)\Z")
DEAD_BRIDGE = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
DEAD_BRIDGE_LEVEL = DEAD_BRIDGE + ".DeadBridge_P:PersistentLevel."
DEAD_BRIDGE_SPAWNER = re.compile(r"AI_Spawner_Squad_BP_C " + re.escape(DEAD_BRIDGE_LEVEL) + r"AI_Spawner_Squad_BP_C_(\d+)\Z")
DEAD_BRIDGE_MARKERS = frozenset((1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 15, 16, 17, 19, 20))
RUIN_SPAWNER = re.compile(r"AI_Spawner_Squad_BP_C " + re.escape(RUIN_LEVEL) + r"AI_Spawner_Squad_BP_C_(\d+)\Z")
RUIN_MARKERS = frozenset((*range(24), 25, 26, 28, 30, 34))
WANDERER_SEED = 964155  # Existing stock authored-route seed, without a forced Ruin squad roll.
RUIN_ROUTES = ((OBSERVATORY, MAP, "Obsv_Etr", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_2"),
               (MAP, RUIN, "CR_RF", "Level_Exit_BP_C_273", "LevelExit_Spawn_BP_C_1"))
RUIN_SQUADS = {"husk": ("NPC_Zomb_01_C", "Monsters", {"Husks_Scattered": 4, "Husks_SwarmSmall": 3}),
               "boar": ("NPC_WildBoar_C", "WildLife", {"Boar_Single": 1})}
DEAD_BRIDGE_HUSK_SEED = 982360
MARKERS = frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24,
                     26, 27, 28, 30, 31, 32, 34, 35, 37, 38, 40, 41))
DATA = {
    "husk": ({"stock_initial", "stock_refresh"}, "NPC_Zomb_01_C",
             {"Husks_Scattered": 4, "Husks_SwarmSmall": 3, "Husks_SwarmLarge": 5}),
    "flayer": ({"stock_antfarm"}, "NPC_Flayer_C", {"Flayers": 2}),
    "notdeer": ({"stock_antfarm"}, "NPC_NotDeer_C", {"NotDeer": 1}),
}
SQUAD_PREFIX = "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/"
NOT_DEER_REPLICA = re.compile(r"NPC_NotDeer_C " + re.escape(LEVEL) + r"NPC_NotDeer_C_[0-9]{1,10}\Z")
FLAYER_REPLAY_SHA = "8f17247a7b18f3e91ba33ec6c4bd72d711935a2217667a5933e8713084e93c95"
FLAYER_REPLAY_SPAWN = [13850.0, -200.0, 132.14999494537]
NOT_DEER_REPLAY_SHA = "ede8b0f6f1fbfb138ce2e9efb85fe3db77ff4f920207b68f6f464ec6bd116cf2"
NOT_DEER_REPLAY_SPAWN = [13850.0, -200.0, 132.14999820909]
NOT_DEER_SAVED_MARKER = "PersistentLevel.AI_Spawner_Squad_BP_C_40"
CELL_PATH = re.compile(rb"/Game/Classes/AntFarm/AntfarmData/Cells(?:Overworld|Dungeons|SafeZones)/[A-Za-z0-9_]+\0")
FLAYER_GEOMETRY_REFUSAL = "stock_flayer_approach_no_verified_position "
FLAYER_SHOT_REFUSAL = "stock_flayer_shot_no_stock_lane "


def verify_not_deer_saved_cell(content: bytes) -> dict:
    cells = list(CELL_PATH.finditer(content))
    crossroads, = [index for index, match in enumerate(cells) if match[0].endswith(b"/CrossRoadsData\0")]
    cell = content[cells[crossroads].start():cells[crossroads + 1].start()]
    squads = cell.split(b"SquadId_2_699B8DEB421ADB46A48403863511BC91\0")
    not_deer, = [squad for squad in squads[1:]
                if b"/Game/Classes/Libraries/DataAssets/Squads/Monsters/NotDeer\0" in squad]
    wanted = (b"Factions_Enum::NewEnumerator3\0", b"AntfarmUnitSave\0", b"/Game/Classes/NPCs/NPC_NotDeer\0", b"NPC_NotDeer_C\0",
              b"LastKnownLocation_22_7F5EE2E8461E909EDF92CCAAB6AA5FEC\0", MAP.encode("ascii") + b"\0",
              NOT_DEER_SAVED_MARKER.encode("ascii") + b"\0", b"DestinationLevel_37_E7348C5D4D5E30EC44EA24BEA733F46C\0")
    remaining = not_deer
    for field in wanted:
        _, found, remaining = remaining.partition(field)
        if not found:
            raise ValueError("The pinned CrossRoads cell lacks its typed stock NotDeer unit or stock last-known marker")
    return {"cell": "CrossRoadsData", "squad": f"{SQUAD_PREFIX}NotDeer.NotDeer", "saved_marker": NOT_DEER_SAVED_MARKER}


def seed_antfarm_replay(source: Path, destination: Path, family: str) -> dict:
    """Carry over only the verified stock frame and saved CrossRoads host spawn."""
    digest, spawn = {"flayer": (FLAYER_REPLAY_SHA, FLAYER_REPLAY_SPAWN),
                     "notdeer": (NOT_DEER_REPLAY_SHA, NOT_DEER_REPLAY_SPAWN)}[family]
    saved = json.loads((source / "save" / "campaign.json").read_text(encoding="utf-8"))
    old_world = saved["world"]
    expected_host = {"map": MAP, "location": spawn, "health": 250.0}
    frame = native_host_save.verify(source / "game-user", old_world)
    if (not frame or frame["map"] != MAP or frame["sha256"] != digest
            or old_world.get("host") != expected_host):
        name = "validation-ec15ee08" if family == "notdeer" else "validation-d474f2cf"
        raise ValueError(f"Antfarm replay requires the verified {name} stock frame and CrossRoads spawn")
    content = native_host_save.slot_path(source / "game-user", frame["slot"]).read_bytes()
    if sha256(content).hexdigest() != digest:
        raise ValueError("The immutable Antfarm replay frame changed while being read")
    source_cell = verify_not_deer_saved_cell(content) if family == "notdeer" else None

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
            raise RuntimeError("The staged Antfarm replay did not create fresh campaign identities")
    evidence = {"frame": copied, "spawn": expected_host, "campaign_id": created["campaign_id"]}
    if source_cell:
        evidence["source_cell"] = source_cell
    return evidence


def verify_antfarm_restore(run: Path, server: dict, seed: dict) -> dict:
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


def read_npcs(run: Path, viewer: str, area: str = MAP, *, topic="native-campaign-npc") -> dict | None:
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
        if schema != ["schema", "1"] or session[1:4] != [viewer, phase, area]:
            return None
        receipt, = [row for row in rows if row[0] == topic]
        census, = [row for row in rows if row[0] == topic + "-census"]
        if len(receipt) != 10 or len(census) != 10 or receipt[1:3] != [area, viewer] or census[1:3] != [area, viewer]:
            return None
        members = [row[3] for row in rows if row[0] == topic + "-member"
                   and len(row) == 4 and row[1:3] == [area, viewer]]
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
        not_deer = [row for row in rows if row[0] == "native-campaign-npc-notdeer"]
        if not_deer:
            family, = not_deer
            if (len(family) != 4 or family[1:3] != [MAP, viewer] or family[3] != "-" and
                    (viewer != "client" or result["simulated"] != 1 or not NOT_DEER_REPLICA.fullmatch(family[3]))):
                return None
            result.update(family="notdeer", replica=None if family[3] == "-" else family[3])
        network = [row for row in rows if row[0] == topic + "-network"]
        if network:
            net, = network
            if len(net) != 10 or net[1:3] != [area, viewer]:
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
            if len(saved) != 8 or saved[1:3] != [area, viewer]:
                return None
            result["saved_squads"] = {"calls": int(saved[3]), "denials": int(saved[4]), "stage": saved[5],
                                      "spawner": saved[6], "data": saved[7]}
        approach = [row for row in rows if row[0] == "native-campaign-npc-approach"]
        if approach and viewer == "host":
            move, = approach
            if len(move) != 7 or move[1] != area:
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
            expected_sweep = {"NPC_Flayer_C": 350, "NPC_NotDeer_C": 300}.get(move["target"].partition(" ")[0])
            if (sweep != expected_sweep or not 0 < placements <= floors or geometry[6] != "guest_hit"
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
            if len(row) != 11 or row[1] != MAP or row[2] not in {"active", "unavailable"}:
                return None
            calls, attempts = map(int, row[3:5])
            if not 0 <= attempts <= calls:
                return None
            result["combat_diagnostic"] = {"scope": row[2], "calls": calls, "attempts": attempts,
                                           "damage": row[5], "swing": row[6]}
            if row[6] in {"stock_infection_delta_unverified", "stock_infection_verified"}:
                result["combat_diagnostic"]["infection_delta"] = dict(zip(
                    ("guest", "host", "reads", "writes"), map(int, row[7:]), strict=True))
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


def stock_cohort(report: dict | None, replicate: bool, family: str = "husk", area: str = MAP) -> bool:
    stages, actor, squads = DATA[family]
    if not report or report["stage"] not in stages:
        return False
    pattern, markers, level = (DEAD_BRIDGE_SPAWNER, DEAD_BRIDGE_MARKERS, DEAD_BRIDGE_LEVEL) if area == DEAD_BRIDGE else (
        SPAWNER, MARKERS, LEVEL)
    source = pattern.fullmatch(report["spawner"])
    members = report["members"]
    maximum = next((size for name, size in squads.items() if report["data"] == f"{SQUAD_PREFIX}{name}.{name}"), 0)
    return bool(source and int(source.group(1)) in markers
                and 0 < report["cohort"] <= maximum
                and report["natural_calls"] >= report["receipts"] > 0
                and len(set(members)) == len(members) == report["cohort"]
                and all(name.startswith(f"{actor} {level}{actor}_") for name in members)
                and report["authority"] >= report["cohort"]
                and report["simulated"] == report["other"] == report["spawn_denials"] == report["ai_denials"] == 0
                and report["replicated"] == (report["cohort"] if replicate else 0)
                and (not replicate or report.get("network", {}).get("authority_network_ready", 0) >= report["cohort"])
                and (family != "notdeer" or (report.get("family") == "notdeer" and report["authority"] == 1
                     and report.get("saved_squads", {}).get("data") == f"{SQUAD_PREFIX}NotDeer.NotDeer")))


def ruin_ambient_receipt(report, *, denial=False):
    if not report:
        return False
    marker = RUIN_SPAWNER.fullmatch(report["spawner"])
    if not marker or int(marker.group(1)) not in RUIN_MARKERS or not re.fullmatch(
            r"SquadData_C /Game/Classes/Libraries/DataAssets/Squads/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.\1",
            report["data"]):
        return False
    if denial:
        return report["stage"] in ("stock_client_ambient_initial_spawn_denied",
                                   "stock_client_ambient_refresh_spawn_denied") and report["spawn_denials"] > 0
    members = report["members"]
    return (report["stage"] in ("stock_initial", "stock_refresh")
            and 0 < report["replicated"] == report["cohort"] <= 6
            and len(set(members)) == len(members) == report["cohort"]
            and all(re.fullmatch(r"(NPC_[A-Za-z0-9_]+_C) " + re.escape(RUIN_LEVEL) + r"\1_[0-9]+", name)
                    for name in members)
            and report["natural_calls"] >= report["receipts"] > 0
            and report["authority"] >= report["network"]["authority_network_ready"] >= report["cohort"]
            and report["simulated"] == report["other"] == report["spawn_denials"] == report["ai_denials"] == 0)


def ruin_stock_receipt(report, family, *, denial=False):
    if not report:
        return False
    source = RUIN_SPAWNER.fullmatch(report["spawner"])
    actor, directory, squads = RUIN_SQUADS[family]
    maximum = next((size for name, size in squads.items() if report["data"] ==
                    f"SquadData_C /Game/Classes/Libraries/DataAssets/Squads/{directory}/{name}.{name}"), 0)
    if not source or int(source.group(1)) not in RUIN_MARKERS or not maximum:
        return False
    if denial:
        return report["stage"] == f"stock_client_{family}_spawn_denied" and report["spawn_denials"] > 0
    members = report["members"]
    return (report["stage"] in ("stock_initial", "stock_refresh")
            and 0 < report["replicated"] == report["cohort"] <= maximum
            and len(set(members)) == len(members) == report["cohort"]
            and all(re.fullmatch(re.escape(f"{actor} {RUIN_LEVEL}{actor}_") + r"[0-9]+", member) for member in members)
            and report["natural_calls"] >= report["receipts"] > 0
            and report["authority"] >= report["network"]["authority_network_ready"] >= report["cohort"]
            and report["simulated"] == report["other"] == report["spawn_denials"] == report["ai_denials"] == 0)


def client_replica(report: dict | None, cohort: int, require_denial: bool, family: str = "husk") -> bool:
    saved = report.get("saved_squads", {}) if report else {}
    not_deer_spawn_scope_ok = False
    if report and family == "notdeer":
        attempts = report["natural_calls"]
        if attempts:
            not_deer_spawn_scope_ok = (
                attempts == report["spawn_denials"] == saved.get("calls") == saved.get("denials")
                and report["stage"] == "stock_client_not_deer_load_denied"
                and saved.get("data") == f"{SQUAD_PREFIX}NotDeer.NotDeer"
            )
        else:
            not_deer_spawn_scope_ok = (
                not require_denial and report["spawn_denials"] == saved.get("calls") == saved.get("denials") == 0
                and report["stage"] == "awaiting_stock_cohort"
            )
    return bool(report and report["receipts"] == report["replicated"] == 0
                and report["cohort"] == 0 and not report["members"]
                and report["simulated"] >= cohort > 0
                and report["authority"] == report["other"] == report["simulated_controllers"] == 0
                and report.get("network", {}).get("local_authority_begins") == 0
                and report["network"]["local_authority_initializes"] == 0
                and (report["spawn_denials"] > 0 or not require_denial)
                and (family != "notdeer" or (report.get("family") == "notdeer" and report["simulated"] == 1
                     and report.get("replica") and not_deer_spawn_scope_ok)))


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
                and scoped["infect_reads"] >= infection["reads_after"] == infection["reads_before"] + 2
                and scoped["infect_writes"] >= infection["writes_after"] == infection["writes_before"] + 1)


async def validate(work: Path, vanilla_saved: Path | None, *, replicate: bool, timeout: float,
                   require_client_denial: bool, approach: bool = False, family: str = "husk",
                   flayer_veto: bool = False, join_only: bool = False, combat: bool = False,
                   stock_refresh: bool = False, antfarm_replay: Path | None = None,
                   projectile_tap: bool = False, flayer_defaults: bool = False, dead_bridge: bool = False,
                   ruin_fields: bool = False, private_canaries: Path | None = None, ruin_diagnostic: bool = False,
                   ruin_ambient: bool = False, ruin_refresh: bool = False) -> None:
    if os.name != "nt":
        raise OSError("The live canary requires the staged Windows game")
    name = ("campaign-npc-ruin-" if ruin_fields else "campaign-npc-") + uuid.uuid4().hex[:8]
    area = RUIN if ruin_fields else DEAD_BRIDGE if dead_bridge else MAP
    campaign_root = work / "campaigns" / name
    prepared = seed_antfarm_replay(antfarm_replay, campaign_root, family) if antfarm_replay else None
    if dead_bridge:
        # The audited seed lets the stock director choose a Husk squad without a spawn fixture.
        with CampaignStore(campaign_root / "save") as campaign:
            campaign.save_world({"map": area, "native_observation": {"native_scene": {"instance_seed": DEAD_BRIDGE_HUSK_SEED}}})
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
                 "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH", "SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_NOTDEER"):
        environment.pop(flag, None)
    if replicate and family != "notdeer":
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE"] = "1"
    if combat:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_COMBAT"] = "1"
    if stock_refresh:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH"] = "1"
    if family == "flayer" and not flayer_defaults:
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW"] = family
        if replicate:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_PUBLISH"] = "1"
        if flayer_veto:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_VETO"] = "1"
    default_not_deer = family == "notdeer" and not combat
    if default_not_deer or flayer_defaults or dead_bridge or ruin_fields:
        environment.pop("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC")
        environment.pop("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE", None)
        if dead_bridge:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW"] = "husk"
        if flayer_defaults and combat:
            environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW"] = "flayer"
    elif family == "notdeer":
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW"] = family
        environment["SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_NOTDEER"] = "1"
    if ruin_fields:
        environment["SILVERFISH_COOP_RUIN_NPC_PROBE"] = "1"
    environment.pop("SILVERFISH_COOP_RUIN_NPC_DIAGNOSTIC", None)
    if ruin_diagnostic:
        environment["SILVERFISH_COOP_RUIN_NPC_DIAGNOSTIC"] = "1"
    environment.pop("SILVERFISH_COOP_RUIN_NPC_REFRESH_PROBE", None)
    if ruin_refresh:
        environment["SILVERFISH_COOP_RUIN_NPC_REFRESH_PROBE"] = "1"
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    canaries = saved_hashes(private_canaries) if private_canaries else None
    if ruin_fields and (not original or not canaries):
        raise ValueError("The Ruin proof requires nonempty original and private canary save baselines")
    launched = []
    flayer_combat = combat and family == "flayer"
    sweep_combat = combat and family in ("flayer", "notdeer")
    evidence = {"map": area, "family": family, "mode": "replicated" if replicate else "observed",
                "verification_scope": ("join" if join_only else "combat" if combat
                                       else "projectile_tap" if projectile_tap else "encounter"), "approach_requested": approach,
                "flayer_publication": family == "flayer" and replicate, "flayer_spawn_veto": flayer_veto,
                "stock_refresh_requested": stock_refresh, "detached_sickness_required": flayer_combat,
                "stock_not_deer_launch_defaults": default_not_deer, "stock_flayer_launch_defaults": flayer_defaults}
    replay_key = family + "_replay"
    if prepared:
        evidence[replay_key] = {"prepared": prepared}
    last_frames = {"host": None, "guest": None}
    client_history = {"frames": 0, "network_frames": 0, "max_local_authority": 0,
                      "max_local_begins": 0, "max_local_initializations": 0,
                      "max_spawn_denials": 0, "max_early_denials": 0}
    evidence["client_history"] = client_history

    def frames():
        current = {"host": read_npcs(host, "host", area), "guest": read_npcs(guest, "client", area)}
        for viewer, frame in current.items():
            if frame is None or (last_frames[viewer] and frame["sequence"] <= last_frames[viewer]["sequence"]):
                continue
            last_frames[viewer] = frame
            if viewer == "host":
                if stock_cohort(frame, replicate, family, area):
                    evidence.setdefault("first_host_cohort", frame)
                if diagnostic := frame.get("combat_diagnostic"):
                    evidence.setdefault("first_combat_diagnostic", diagnostic)
                    evidence["last_combat_diagnostic"] = diagnostic
                    if diagnostic["calls"]:
                        evidence.setdefault("first_stock_damage_diagnostic", diagnostic)
                        pawn = frame.get("combat", {}).get("guest_pawn")
                        if pawn and (scoped := healthy_guest_sickness(status(host), pawn)):
                            evidence["last_detached_sickness"] = scoped
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
                and str(reply.get("error", "")).startswith((FLAYER_GEOMETRY_REFUSAL, FLAYER_SHOT_REFUSAL))):
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

    def paired(server, client, map_name=area):
        return (server.get("state") == "hosting" and client.get("state") == "connected"
                and server.get("game", {}).get("remote_clients") == 1 and client.get("link_connected") is True
                and server.get("game", {}).get("map") == client.get("game", {}).get("map") == map_name
                and server.get("generation") == client.get("host_session")
                and bool(server.get("zone_epoch")) and server["zone_epoch"] == client.get("zone_epoch"))

    def proved(server, client, snapshot):
        host_npcs, guest_npcs = snapshot["host"], snapshot["guest"]
        if not paired(server, client) or not stock_cohort(host_npcs, replicate, family, area) or not guest_npcs:
            return False
        if replicate:
            return (client_replica(guest_npcs, host_npcs["cohort"], require_client_denial, family)
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

    async def ordinary_flayer_tap(server, natural):
        host_pawn, guest_pawn = approach_pair(server)
        members = natural["host"]["members"]
        guid = uuid.uuid4().hex.upper()
        seeded = (await command("action", *primary, "native-guest-weapon-seed-light-pistol",
                                "--target", "guest", "--item-guid", guid))["native_guest_weapon"]
        equipment = (await command("action", *secondary, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
        evidence["stock_light_pistol"] = {"fixture_item": seeded, "ordinary_equip": equipment}
        assert (seeded["guid"], seeded["status"], seeded["stored_ammo"]) == (guid, "backpack", 7), seeded
        assert (equipment["map"], equipment["pawn"], equipment["guid"], equipment["status"],
                equipment["actor_ammo"], equipment["stored_ammo"]) == (MAP, guest_pawn, guid, "equipped", 7, 7), equipment

        def armed(server, client, snapshot):
            scope = client.get("game", {}).get("native_guest_primary_scope", {})
            pawn = client.get("game", {}).get("pawns", {}).get("local", {}).get("name")
            return (paired(server, client) and stock_cohort(snapshot["host"], True, family)
                    and approach_pair(server) == (host_pawn, guest_pawn) and pawn
                    and (scope.get("map"), scope.get("pawn"), scope.get("guid"), scope.get("kind")) ==
                        (MAP, pawn, guid, "light-pistol"))

        _, client, _ = await wait("No ordinary primary mapping for the equipped, detached pistol", armed, 15)
        evidence["stock_light_pistol"]["primary_scope"] = client["game"]["native_guest_primary_scope"]
        client_pawn = client["game"]["pawns"]["local"]["name"]
        client_generation = client["generation"]
        attempts = evidence["ordinary_projectile_taps"] = []
        magazine = 7
        for target in (*members, *members):
            reply = await command("action", *primary, "native-campaign-npc-approach", "--target", target,
                                  "--amount", "1", allow_flayer_geometry_refusal=True)
            if not reply.get("ok"):
                attempts.append({"target": target, "refusal": reply["error"]})
                continue
            aim = reply["native_campaign_flayer_shot_aim"]
            assert (aim["map"], aim["victim"], aim["shooter"], aim["state"], aim["reach"]) == (
                MAP, target, guest_pawn, "armed", None), aim
            start = max((hit["sequence"] for hit in status(host).get("game", {}).get(
                "native_campaign_flayer_projectile_hits", [])), default=0)
            press = (await command("action", *secondary, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
            attempt = {"aim": aim, "stock_input": press}
            attempts.append(attempt)
            assert (press["map"], press["pawn"], press["guid"], press["stock_wrappers"]) == (MAP, client_pawn, guid, 2), press
            event = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                candidates = events(guest, "native_guest_primary_input") + events(guest, "native_guest_primary_input_rejected")
                event = next((row for row in reversed(candidates) if row.get("sequence") == press["sequence"]
                              and row.get("generation") == client_generation), None)
                if event:
                    break
                await asyncio.sleep(0.1)
            if not event:
                raise RuntimeError(f"No ordinary stock primary receipt for sequence {press['sequence']}")
            if event["event"] == "native_guest_primary_input_rejected":
                attempt["refusal"] = event.get("reason", "missing reason")
                if str(attempt["refusal"]).startswith(FLAYER_SHOT_REFUSAL):
                    continue
                raise RuntimeError(f"The staged stock Flayer pistol tap was refused: {attempt['refusal']}")
            shot = attempt["authoritative_shot"] = event["native_guest_weapon"]
            assert (shot["map"], shot["pawn"], shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                MAP, guest_pawn, guid, magazine, magazine - 1, 1), shot
            magazine -= 1

            def hit_from_tap(server):
                hits = server.get("game", {}).get("native_campaign_flayer_projectile_hits", [])
                return next((hit for hit in hits if hit["sequence"] > start and hit["map"] == MAP
                             and hit["shooter"] == guest_pawn and hit["victim"] == target), None)

            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                current, viewer = status(host), status(guest)
                placement = current.get("game", {}).get("native_campaign_flayer_shot_aim", {})
                if (placement.get("state"), placement.get("victim"), placement.get("shooter")) == ("applied", target, guest_pawn):
                    attempt["shooting_pose"] = placement
                if diagnostic := current.get("game", {}).get("native_campaign_flayer_projectile_status"):
                    attempt["observer"] = diagnostic
                if hit := hit_from_tap(current):
                    assert 0 < hit["after"] < hit["before"] <= 180, hit
                    assert math.isclose(hit["damage"], hit["before"] - hit["after"], abs_tol=1e-6), hit
                    snapshot = frames()
                    if proved(current, viewer, snapshot):
                        assert target in snapshot["host"]["members"], snapshot
                        assert "shooting_pose" in attempt, attempt
                        evidence["stock_flayer_guest_projectile_hit"] = hit
                        return current, viewer, snapshot
                await asyncio.sleep(0.1)
            attempt["outcome"] = "no_exact_nonlethal_hit_observed"
            assert "shooting_pose" in attempt, attempt
        raise RuntimeError("No ordinary guest pistol tap verified the exact stock Flayer HP debit")

    async def verify_ruin(invitation):
        def pair_in(map_name, epoch=None):
            return lambda server, client, _: (paired(server, client, map_name)
                                              and server["zone_epoch"] != epoch and client.get("gameplay_fresh")
                                              and client.get("character_id") == invitation["character_id"]
                                              and server.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

        server, _, _ = await wait("No enrolled pair in the stock Wanderer start", pair_in(OBSERVATORY), 55)
        route_evidence = evidence["authored_route"] = []
        for source, destination, entrance, actor, arrival_actor in RUIN_ROUTES:
            assert server["game"]["map"] == source, server["game"]
            epoch = server["zone_epoch"]
            endpoint = ("--entrance", entrance, "--exit-actor", actor)
            for role in ("host", "guest"):
                await command("action", *primary, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
            await command("action", *primary, "native-exit-position", "--target", "host", "--exit-position", "inside", *endpoint)
            server, _, _ = await wait(f"The party did not enter {destination} through {actor}", pair_in(destination, epoch), 55)
            generation = server["generation"]
            travel, = (row for row in events(host, "native_authored_zone_request") if row.get("generation") == generation
                       and (row["source"], row["destination"], row["actor"], row["entrance"], row["result"].get("ok"))
                       == (source, destination, actor, entrance, True))
            ready, = (row for row in events(host, "zone_travel_ready")
                      if row.get("generation") == generation and row.get("epoch") == server["zone_epoch"])
            assert ready["authored_arrival"]["actor"].endswith("." + arrival_actor), ready
            route_evidence.append({"travel": travel, "arrival": ready["authored_arrival"], "epoch": server["zone_epoch"]})

        scene = server["game"]["native_scene"]
        seconds = scene["game_seconds"]
        assert scene["instance_seed"] == WANDERER_SEED and 7 * 3600 <= seconds % 86400 < 17 * 3600, scene
        evidence["stock_daytime"] = {"instance_seed": scene["instance_seed"], "game_seconds": seconds}
        fixture = None
        if ruin_refresh:
            fixture = evidence["ruin_refresh_fixture"] = {"anchors": {}, "requests": {}}
            for role in ("host", "guest"):
                fixture["anchors"][role] = await command(
                    "action", *primary, "native-door-position", "--target", role, "--exit-position", "near",
                    "--entrance", "Market_Flood"
                )
            await wait("No same-epoch pair at the stock Market anchor", pair_in(RUIN), 10)
            for run, seat in ((host, "host"), (guest, "client")):
                assert "refresh" not in read_director_trace(run, seat), "A stock refresh already ran before the scoped fixture"
            fixture["native_before"] = {
                "host": read_npcs(host, "host", RUIN, topic="native-campaign-npc-ambient") or {},
                "client": read_npcs(guest, "client", RUIN, topic="native-campaign-npc-ambient") or {},
            }
            fixture["guest_local_workers_before"] = sorted(
                name for name, row in (await capture(guest, "client")).items()
                if row["class"] == "NPC_Survivor_Worker_C" and row["role"] == 3
            )
            for run, seat in ((guest, "client"), (host, "host")):
                fixture["requests"][seat] = await probe_ruin_refresh(run, seat)
        denials = evidence["observed_client_denials"] = {}
        latest = evidence["ruin_frames"] = {}
        deadline = time.monotonic() + min(timeout, 28)
        limitation = ("No captured stock reinforcement receipt for the scoped fixture" if fixture
                      else "No captured natural supported director receipt")
        topics = (("ambient", "native-campaign-npc-ambient"),) if ruin_ambient else (
            ("husk", "native-campaign-npc"), ("boar", "native-campaign-npc-boar"))
        while time.monotonic() < deadline:
            frames()  # Preserve the existing Husk birth/initialization diagnostic.
            if ruin_diagnostic:
                trace = evidence.setdefault("ruin_director_decisions", {})
                for run, viewer in ((host, "host"), (guest, "client")):
                    if rows := read_director_trace(run, viewer):
                        trace[viewer] = rows
            qualified = {}
            for family, topic in topics:
                owner, observer = (read_npcs(run, viewer, RUIN, topic=topic)
                                   for run, viewer in ((host, "host"), (guest, "client")))
                latest[family] = {"host": owner, "guest": observer}
                if not owner or not observer:
                    continue
                if family == "ambient":
                    host_matches = not fixture or (
                        owner["stage"] == "stock_refresh"
                        and owner["data"] == fixture["requests"]["host"]["asset"]
                        and owner["spawner"] == fixture["requests"]["host"]["marker"]
                        and owner["receipts"] > fixture["native_before"]["host"].get("receipts", 0))
                    guest_matches = not fixture or (
                        observer["data"] == fixture["requests"]["client"]["asset"]
                        and observer["spawner"] == fixture["requests"]["client"]["marker"])
                    if guest_matches and ruin_ambient_receipt(observer, denial=True) and (not fixture or (
                            observer["stage"] == "stock_client_ambient_refresh_spawn_denied"
                            and observer["spawn_denials"] > fixture["native_before"]["client"].get("spawn_denials", 0))):
                        denials[family] = observer
                    if fixture and guest_matches and observer["stage"] == "stock_ruin_guest_class_unloaded":
                        fixture["guest_outcome"] = observer["stage"]
                    if host_matches and ruin_ambient_receipt(owner):
                        qualified[family] = set(owner["members"])
                    continue
                network = observer["network"]
                assert (observer["authority"], observer["other"], observer["simulated_controllers"],
                        network["local_authority_begins"], network["local_authority_initializes"]) == (0, 0, 0, 0, 0), observer
                if ruin_stock_receipt(observer, family, denial=True):
                    denials[family] = observer
                if ruin_stock_receipt(owner, family) and client_replica(observer, owner["cohort"], False, family):
                    qualified[family] = set(owner["members"])
            if not qualified:
                await asyncio.sleep(.2)
                continue
            owner, observer = await asyncio.gather(capture(host, "host"), capture(guest, "client"))
            evidence["ruin_actor_census"] = {"host": owner, "guest": observer}
            required = set().union(*qualified.values())
            try:
                if ruin_ambient:
                    matched = compare(owner, observer, required, classes={name.partition(" ")[0] for name in required})
                else:
                    matched = compare(owner, observer, required)
            except ValueError as mismatch:
                limitation = str(mismatch)
                await asyncio.sleep(.2)
                continue
            evidence["ruin_roster"] = matched
            evidence["ruin_cohorts"] = {family: sorted(members) for family, members in qualified.items()}
            if fixture:
                fixture["host_mixed_cohort_verified"] = {owner[name]["class"] for name in required} == {
                    "NPC_MinorStag_C", "NPC_Doe_C"
                }
                fixture["guest_local_workers_added"] = sorted(
                    name for name, row in observer.items()
                    if row["class"] == "NPC_Survivor_Worker_C" and row["role"] == 3
                    and name not in fixture["guest_local_workers_before"]
                )
                if denials:
                    assert not fixture["guest_local_workers_added"], fixture
                    fixture["guest_outcome"] = "scoped stock Worker reinforcement denied"
                    evidence["client_spawn_ownership"] = fixture["guest_outcome"]
                if fixture.get("guest_outcome") == "stock_ruin_guest_class_unloaded":
                    raise RuntimeError("The stock Worker was selected but its unloaded class left the stock spawn active")
                if denials and fixture["host_mixed_cohort_verified"]:
                    member = next(name for name in sorted(required) if name.startswith("NPC_Doe_C "))
                    replica = matched["matched"][member]["replica"]
                    before, = await ruin_probe(host, f"health\t{member}")
                    attack, = await ruin_probe(guest, f"damage\t{replica}")
                    after, = await ruin_probe(host, f"health\t{member}")
                    assert before == after and attack.split("\t")[:2] == ["client", replica], (before, attack, after)
                    fixture["guest_damage_and_death_veto"] = {"host": before, "guest": attack}
                    return
                limitation = "No mixed stag/doe cohort and matching Worker veto both verified"
            elif denials:
                evidence["client_spawn_ownership"] = "observed exact native stock veto"
                return
            else:
                limitation = "No supported client stock veto receipt observed in the bounded window"
            await asyncio.sleep(.2)
        if "ruin_roster" not in evidence:
            raise RuntimeError(limitation)
        evidence["client_spawn_ownership"] = limitation
        if require_client_denial or fixture:
            raise RuntimeError(limitation)

    try:
        fixture_flags = ("--fixtures",) if approach or projectile_tap else ()
        if flayer_combat and not flayer_defaults:
            fixture_flags += ("--native-guest-sickness",)
        if dead_bridge:
            fixture_flags += ("--native-authored-enemy-loot",)
        start_flags = ("--start-zone", area)
        if ruin_fields:
            start_flags = ("--native-start-preset", "wanderer", "--native-world-seed", str(WANDERER_SEED),
                           "--native-expanded-zones", "--native-zone-walls", "--fixtures")
        await start("host", host, primary, "--port", "0", "--headless", "--native-zones",
                    "--native-events", "--native-guest", *start_flags, *fixture_flags)
        initial, _, _ = await wait(f"The host did not start {area}",
                                   lambda server, _, __: server.get("state") == "hosting", 45)
        if prepared:
            evidence[replay_key]["adopted"] = verify_antfarm_restore(host, initial, prepared)
        invitation = await command("invite", *primary, "--name", "Encounter canary guest", "--character", "Encounter observer")
        client_flags = ("--native-guest-primary-input",) if projectile_tap else ()
        await start("join", guest, secondary, "--headless", "--native-zones", "--invite", invitation["invite_file"], *client_flags)
        if ruin_fields:
            await verify_ruin(invitation)
            evidence["verified"] = True
            return
        if join_only:
            def initial_join(server, client, snapshot):
                return (paired(server, client) and snapshot["guest"] is not None
                        and stock_cohort(snapshot["host"], replicate, family, area)
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
        if approach or projectile_tap:
            def ready(server, client, snapshot):
                pair = approach_pair(server)
                return (paired(server, client) and stock_cohort(snapshot["host"], True, family, area) and pair
                        and (not flayer_combat or healthy_guest_sickness(server, pair[1])))

            before, _, natural = await wait(f"No stock {family} cohort and enrolled pair appeared", ready)
            evidence["before_approach"] = {"host": natural["host"], "guest": natural["guest"]}
        if projectile_tap:
            server, client, snapshot = await ordinary_flayer_tap(before, natural)
        elif approach:
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
                        and (not sweep_combat or snapshot["host"].get("approach_impact", {}).get("target") == target)
                        and (not flayer_combat or stock_flayer_infection(server, snapshot["host"])))

            label = (f"No stock {family} attack verified detached guest combat" if combat
                     else "The approached cohort did not yield stock client replicas")
            server, client, snapshot = await wait(label, approached, 38 if combat else 45)
            evidence["approach"] = snapshot["host"]["approach"]
            if combat:
                evidence["stock_combat"] = snapshot["host"]["combat"]
            if flayer_combat:
                evidence["flayer_approach_impact"] = snapshot["host"]["approach_impact"]
                evidence["stock_melee_sickness"] = snapshot["host"]["melee_sickness"]
            elif sweep_combat:
                evidence["stock_approach_impact"] = snapshot["host"]["approach_impact"]
        else:
            server, client, snapshot = await wait(f"No fixture-free stock {family} proof appeared", proved)
        if client.get("character_id") != invitation["character_id"]:
            raise AssertionError("The authenticated guest did not activate its invited character")
        evidence.update({"host": snapshot["host"], "guest": snapshot["guest"], "zone_epoch": server["zone_epoch"]})
        if dead_bridge:
            watch = server.get("game", {}).get("native_enemy_loot", {})
            assert watch.get("state") == "watching" and watch.get("map") == area, watch
            assert watch.get("bandit", "").startswith(f"NPC_State_Infantry_CQC_C {DEAD_BRIDGE_LEVEL}"), watch
            evidence["stock_cqc_loot_watch"] = watch
            original_pawns = before["game"]["pawns"]

            async def return_to_entry():
                reply = await command("action", *primary, "native-campaign-npc-approach", "--target", target, "--amount", "2")
                returned = reply["native_campaign_npc_return"]
                for role in ("local", "guest"):
                    assert returned[role]["name"] == original_pawns[role]["name"], returned
                    assert math.dist(returned[role]["location"], original_pawns[role]["location"]) <= 60, returned
                return returned

            evidence["returned_before_reconnect"] = await return_to_entry()

        if replicate:
            dropped = await command("action", *secondary, "drop-link")
            if dropped.get("link_connected") is not False:
                raise AssertionError("The guest's Direct IP link did not drop")
            resumed = await command("action", *secondary, "reconnect")
            if resumed.get("link_connected") is not True or resumed.get("resumed") is not True:
                raise AssertionError("The guest's Direct IP link did not resume")

            def after_reconnect(server, client, current):
                encounter_ready = (paired(server, client) and stock_cohort(current["host"], True, family, area)
                                   and current["guest"] is not None and approach_pair(server) == (host_pawn, guest_pawn)
                                   and current["guest"]["authority"] == 0 and "first_client_local_birth_frame" not in evidence
                                   if dead_bridge else proved(server, client, current))
                return (encounter_ready and server["zone_epoch"] == evidence["zone_epoch"]
                        and client.get("character_id") == invitation["character_id"]
                        and all(current[viewer]["sequence"] > snapshot[viewer]["sequence"] for viewer in ("host", "guest"))
                        and set(snapshot["host"]["members"]) == set(current["host"]["members"])
                        and (family != "notdeer" or snapshot["guest"]["replica"] == current["guest"]["replica"]))

            _, _, rejoined = await wait("The stock cohort did not survive the guest reconnect", after_reconnect, 24)
            evidence["reconnected"] = {"host": rejoined["host"], "guest": rejoined["guest"]}
            if dead_bridge:
                await command("action", *primary, "native-campaign-npc-approach", "--target", target)
                _, _, visible = await wait("The same stock cohort did not become visible again after reconnect",
                                           lambda server, client, current: approached(server, client, current)
                                           and after_reconnect(server, client, current), 12)
                evidence["visible_after_reconnect"] = {"host": visible["host"], "guest": visible["guest"]}
                evidence["returned_after_reconnect"] = await return_to_entry()
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
        if private_canaries:
            evidence["private_canaries_verified"] = saved_hashes(private_canaries) == canaries
            if not evidence["private_canaries_verified"]:
                evidence["verified"] = False
        (host / "live-campaign-npc-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if vanilla_saved and not evidence["original_saves_verified"]:
            raise AssertionError("Original singleplayer save files changed")
        if private_canaries and not evidence["private_canaries_verified"]:
            raise AssertionError("Private save canaries changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="root of an already prepared stage")
    parser.add_argument("--vanilla-saved", type=Path, help="optional original Saved directory to hash")
    parser.add_argument("--cohort", choices=tuple(DATA), default="husk", help="stock encounter family to verify")
    parser.add_argument("--ruin-fields", action="store_true", help="observe the real daytime Ruin Fields stock director")
    parser.add_argument("--ruin-ambient", action="store_true", help="verify a stock Ruin squad outside the legacy Husk and boar topics")
    parser.add_argument("--ruin-stock-refresh", action="store_true",
                        help="exercise one scoped authored reinforcement per seat at the Ruin Market door")
    parser.add_argument("--ruin-diagnostic", action="store_true", help="capture exact stock Ruin pool inputs and first draw outcomes")
    parser.add_argument("--private-canaries", type=Path, help="private SaveGames to hash before and after the Ruin proof")
    parser.add_argument("--dead-bridge", action="store_true", help="verify the stock Dead Bridge Husk director with production defaults")
    parser.add_argument("--flayer-defaults", action="store_true", help="verify stock Flayer combat and reconnect under ordinary campaign defaults")
    parser.add_argument("--flayer-veto", action="store_true", help="also enable the experimental guest Flayer spawn veto")
    parser.add_argument("--flayer-replay", type=Path, help="replay the verified d474f2cf CrossRoads campaign directory")
    parser.add_argument("--notdeer-replay", type=Path, help="replay the original stock CrossRoads Antfarm frame from validation-ec15ee08")
    parser.add_argument("--combat", action="store_true", help="observe a stock melee attack against the exact detached guest")
    parser.add_argument("--projectile-tap", action="store_true",
                        help="verify an ordinary guest Light Pistol tap against the exact restored stock Flayer cohort")
    parser.add_argument("--stock-refresh", action="store_true",
                        help="inspect stock Husk reinforcement readiness and permit one eligible stock director refresh")
    parser.add_argument("--join-only", action="store_true", help="verify stock cohort start and guest map admission only")
    parser.add_argument("--replicate", action="store_true", help="enable the exact native encounter cohort replication canary")
    parser.add_argument("--approach", action="store_true",
                        help="bring the paired pawns near a naturally spawned stock cohort after verification")
    parser.add_argument("--require-client-denial", action="store_true", help="also require a naturally attempted client spawn veto")
    parser.add_argument("--timeout", type=float, default=125.0, help="observation window, including the stock 90-second refresh")
    options = parser.parse_args()
    if options.ruin_fields:
        if (options.cohort != "husk" or not options.vanilla_saved or not options.private_canaries
                or not options.private_canaries.is_dir()
                or any((options.dead_bridge, options.approach, options.combat, options.stock_refresh, options.join_only,
                        options.flayer_replay, options.notdeer_replay, options.projectile_tap, options.flayer_defaults,
                        options.flayer_veto))):
            parser.error("--ruin-fields verifies Husk/boar stock cohorts and requires original and private canary paths")
        options.replicate = True
        if options.ruin_stock_refresh:
            options.ruin_ambient = options.ruin_diagnostic = True
    elif options.private_canaries or options.ruin_diagnostic or options.ruin_ambient or options.ruin_stock_refresh:
        parser.error("Ruin diagnostics, refresh and private canaries require --ruin-fields")
    if options.dead_bridge:
        if options.cohort != "husk" or any((options.approach, options.combat, options.stock_refresh, options.join_only,
                                            options.flayer_replay, options.notdeer_replay, options.projectile_tap)):
            parser.error("--dead-bridge verifies Husk publication, client spawn ownership, and reconnect")
        options.replicate = options.approach = True
    if options.cohort == "notdeer":
        if (not options.notdeer_replay or not options.notdeer_replay.is_dir() or (options.approach and not options.combat)
                or options.join_only or options.stock_refresh or options.projectile_tap or options.flayer_replay):
            parser.error("NotDeer requires --notdeer-replay and supports publication, reconnect, and optional --combat")
        options.replicate = True
    elif options.notdeer_replay:
        parser.error("--notdeer-replay requires --cohort notdeer")
    if options.flayer_defaults:
        if (options.cohort != "flayer" or not options.flayer_replay
                or options.flayer_veto or options.approach or options.projectile_tap or options.join_only):
            parser.error("--flayer-defaults requires --cohort flayer and --flayer-replay")
        options.combat = True
    if options.combat or options.stock_refresh:
        if options.join_only:
            parser.error("--combat and --stock-refresh require full encounter verification")
        options.replicate = options.approach = True
    if options.stock_refresh and options.cohort != "husk":
        parser.error("--stock-refresh requires the Husk encounter test")
    if options.combat and options.cohort == "flayer":
        options.flayer_veto = True
    if options.projectile_tap:
        if (options.cohort != "flayer" or not options.flayer_replay
                or options.combat or options.join_only or options.approach):
            parser.error("--projectile-tap requires --cohort flayer and --flayer-replay, and excludes other approach modes")
        options.replicate = options.flayer_veto = True
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
    run = validate(options.work.resolve(), options.vanilla_saved, replicate=options.replicate,
                   timeout=options.timeout, require_client_denial=options.require_client_denial,
                   approach=options.approach, family=options.cohort, flayer_veto=options.flayer_veto,
                   join_only=options.join_only, combat=options.combat, stock_refresh=options.stock_refresh,
                   antfarm_replay=options.flayer_replay or options.notdeer_replay,
                   projectile_tap=options.projectile_tap, flayer_defaults=options.flayer_defaults,
                   dead_bridge=options.dead_bridge, ruin_fields=options.ruin_fields,
                   private_canaries=options.private_canaries, ruin_diagnostic=options.ruin_diagnostic,
                   ruin_ambient=options.ruin_ambient, ruin_refresh=options.ruin_stock_refresh)
    if options.ruin_fields:
        with installed_probe(options.work.resolve()):
            asyncio.run(run)
    else:
        asyncio.run(run)
