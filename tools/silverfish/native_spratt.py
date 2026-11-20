"""Record the stock Cistern assassination objective from its authored death callback."""

import math
import re
import time

from . import party
from .travel import CISTERN


QUEST = "Asn_Spratt"
AREA = CISTERN
LEVEL = f"{AREA}.Cistern_P:PersistentLevel."
SPAWNER = f"Quest_AssassinTargetSpawn_BP_C {LEVEL}Quest_AssassinTargetSpawn_BP_C_0"
KEY = "native_spratt_objective"
CLIENT_KEY = "native_spratt_client"
VERIFIED = "stock_objective_verified"
UNVERIFIED = "mutation_unverified_stock_spratt_death"
TARGET = re.compile(re.escape(f"NPC_Spratt_C {LEVEL}NPC_Spratt_C_") + r"[0-9]{1,10}\Z")
COUNTERS = ("step_before", "step_after", "xp_before", "xp_after", "cash_before", "cash_after")
REFUSALS = {"stock_spratt_scope_unqualified", "stock_spratt_spawner_unqualified", "stock_spratt_target_unavailable",
            "stock_spratt_target_unqualified", "stock_spratt_target_schema_unqualified",
            "stock_spratt_stock_progress_unqualified",
            "stock_spratt_schema_npc_base", "stock_spratt_schema_projectile_base", "stock_spratt_schema_deal_damage",
            "stock_spratt_schema_deal_with_armor", "stock_spratt_schema_armor_instigator",
            "stock_spratt_schema_resolve_death", "stock_spratt_schema_projectile_calc", "stock_spratt_schema_instigator",
            "stock_spratt_schema_health", "stock_spratt_schema_quest_step", "stock_spratt_schema_save_keys",
            "stock_spratt_schema_death_event", "stock_spratt_schema_event_parameter",
            "stock_spratt_schema_event_parameter_layout", "stock_spratt_schema_event_parameter_class"}


def parse_objective(fields, *, area, phase):
    if (area != AREA or phase != "hosting" or len(fields) != 17 or fields[1:3] != [AREA, SPAWNER]
            or fields[5] not in ("0", "1") or fields[12] not in ("host", "guest", "-")):
        return None
    target, status = fields[3:5]
    if status in REFUSALS:
        return {"map": AREA, "spawner": SPAWNER, "state": status} if target == "-" else None
    if status not in ("watching", VERIFIED, UNVERIFIED) or not TARGET.fullmatch(target):
        return None
    proof = {name: None if value == "-" else int(value) for name, value in zip(COUNTERS, fields[6:12])}
    health = None if fields[15] == "-" else float(fields[15])
    if health is not None and not math.isfinite(health):
        return None
    return {**proof, "map": AREA, "spawner": SPAWNER, "target": target, "state": status, "replicated": fields[5] == "1",
            "shooter_role": fields[12], "shooter": fields[13], "shot": fields[14], "health": health,
            "callback_count": None if fields[16] == "-" else int(fields[16])}


def parse_client(fields, *, area, phase):
    if area != AREA or phase != "connected" or len(fields) != 5 or fields[1:3] != [AREA, SPAWNER]:
        return None
    authority, simulated = map(int, fields[3:])
    if not 0 <= authority <= 32 or not 0 <= simulated <= 32:
        return None
    return {"map": AREA, "spawner": SPAWNER, "authority": authority, "simulated": simulated}


def credited_character(host, game, proof):
    role, shooter = proof["shooter_role"], proof["shooter"]
    if role == "-" and shooter == proof["shot"] == "-":
        return "world"
    if shooter == "-" or proof["shot"] == "-":
        return None
    if role == "host" and shooter == game.get("pawns", {}).get("local", {}).get("name"):
        return "host"
    if role == "guest":
        matches = [character for character, member in host.native_recipients().items()
                   if member.get("pawn") == shooter and character in host.party["players"]]
        if len(matches) == 1:
            return matches[0]
    return None


async def observe(host, bridge):
    game = host.last_game
    if (not host.native_profile or not host.native_guest or not host.native_zones or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or (game.get("state"), game.get("map")) != ("hosting", AREA)
            or time.monotonic() - host.last_seen > 3):
        return
    proof = game.get(KEY, {})
    if ((proof.get("map"), proof.get("spawner")) != (AREA, SPAWNER)
            or not TARGET.fullmatch(proof.get("target") or "") or proof.get("state") not in (VERIFIED, UNVERIFIED)):
        return
    previous = host.party.get("native_quests", {}).get(QUEST, {})
    objective = {key: value for key, value in proof.items() if key != "health"}
    if previous.get("step") in (2, -10) and previous.get("objective") == objective:
        return
    actor = credited_character(host, game, proof)
    if (proof["state"] != VERIFIED or (proof["step_before"], proof["step_after"], proof["callback_count"]) != (1, 2, 1)
            or any(type(proof[name]) is not int or proof[name] < 0 for name in COUNTERS)
            or proof["xp_before"] != proof["xp_after"] or proof["cash_before"] != proof["cash_after"]
            or previous.get("step") not in (None, 1) or not actor):
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError("The stock Spratt death did not verify the exact quest transition and ownership")
    candidate = party.load_party(host.party)
    record = {**previous, "quest_id": QUEST, "step": 2, "actor": actor, "objective": objective}
    candidate.setdefault("native_quests", {})[QUEST] = record
    host.native_checkpoint_unsafe = True
    try:
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_spratt_objective_committed", revision=revision, **record)
