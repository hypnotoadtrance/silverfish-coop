"""Coordinate the exact stock Lenny assassination receipt."""

import re
import time

from . import native_flood_jammer, party
from .native_gold import valid_guid

SPEC = native_flood_jammer.LENNY
AIM = SPEC.command("aim")
REVIVE_APPROACH = SPEC.command("revive-approach")
KEY = SPEC.key
AREA = SPEC.objective_map
LEVEL = f"{AREA}.RailYard_P:PersistentLevel."
SPAWNER = f"Quest_AssassinTargetSpawn_BP_C {LEVEL}Quest_AssassinTargetSpawn_BP_C_1"
_TARGET = re.compile(re.escape(f"NPC_Lenny_C {LEVEL}NPC_Lenny_C_") + r"[0-9]+\Z")


def exact_target(name):
    return isinstance(name, str) and _TARGET.fullmatch(name) is not None


async def aim_fixture(host, bridge, actor, guid):
    member = host.native_recipients().get(actor)
    if (not host.fixtures or not host.native_guest or not host.native_zones or not valid_guid(guid)
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("map") != AREA or not member or not member["connected"]):
        raise party.RuleError("Choose an enrolled guest with an owned SG-71 A near the Rail Yard target")
    pawn = member["pawn"]
    try:
        snapshot = await bridge.command(AIM, pawn, guid, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    proof = snapshot.get(f"{KEY}_aim", {})
    objective = snapshot.get(f"{KEY}_objective", {})
    if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid")) !=
            ("hosting", AREA, AREA, pawn, guid) or proof.get("target") != objective.get("target")
            or not exact_target(proof.get("target")) or objective.get("spawner") != SPAWNER):
        raise RuntimeError("The stock aim did not identify the exact owned shotgun and Lenny actor")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {f"{KEY}_aim": proof}


async def revive_approach(host, bridge, actor):
    member = host.native_recipients().get(actor)
    game = host.last_game
    players = host.party["players"]
    objective = game.get(f"{KEY}_objective", {})
    if (not host.fixtures or not host.native_guest or not host.native_zones or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or game.get("map") != AREA or objective.get("state") != "watching"
            or objective.get("spawner") != SPAWNER or not exact_target(objective.get("target"))
            or not member or not member["connected"] or players.get(actor, {}).get("downed") is not True
            or players.get("host", {}).get("downed") is not False):
        raise party.RuleError("The staged Lenny rescue needs a standing host and an enrolled downed guest")
    pawn = member["pawn"]
    try:
        snapshot = await bridge.command(REVIVE_APPROACH, pawn, timeout=6)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    if snapshot.get("state") != "hosting" or snapshot.get("map") != AREA:
        raise RuntimeError("The staged Lenny rescue lost its exact stock map")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {f"{KEY}_revive_approach": {"map": AREA, "pawn": pawn, "target": objective["target"]}}


async def observe(host, bridge):
    snapshot = host.last_game
    if (not host.native_profile or not host.native_guest or not host.native_zones or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or snapshot.get("state") != "hosting" or snapshot.get("map") != AREA):
        return
    proof = snapshot.get(f"{KEY}_objective", {})
    if (proof.get("map"), proof.get("spawner")) != (AREA, SPAWNER) or not exact_target(proof.get("target")):
        return
    state = proof.get("state", "")
    if state.startswith("mutation_unverified_"):
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError("The stock Lenny death changed the game without a verified quest transition")
    if state != "stock_objective_verified":
        return
    previous = host.party.get("native_quests", {}).get(SPEC.quest, {})
    # HP can disappear when Unreal retires the corpse.
    objective = {key: value for key, value in proof.items() if key != "health"}
    if previous.get("step") in (2, -10) and previous.get("objective") == objective:
        return
    if (previous.get("step") != 1 or proof.get("step_before") != 1 or proof.get("step_after") != 2
            or proof.get("flag_before") != 0 or proof.get("flag_after") != 1
            or type(proof.get("xp_before")) is not int or proof["xp_before"] != proof.get("xp_after")):
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError("The exact Lenny death disagreed with the campaign quest step")
    shooter = proof.get("shooter")
    role = proof.get("shooter_role")
    if role == "host":
        if shooter != snapshot.get("pawns", {}).get("local", {}).get("name"):
            return
        actor = "host"
    elif role == "guest":
        actor = next((owner for owner, member in host.native_recipients().items() if member["pawn"] == shooter), None)
        if not actor:
            return
    else:
        actor = "world"
    candidate = party.load_party(host.party)
    record = {**previous, "quest_id": SPEC.quest, "step": 2, "actor": actor, "objective": objective}
    candidate["native_quests"][SPEC.quest] = record
    host.native_checkpoint_unsafe = True
    try:
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event(f"{KEY}_objective_committed", revision=revision, **record)
