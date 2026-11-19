"""Follow the authored Flood Plains jammer and settle the stock bounty once."""

import time

from . import native_loot, native_rewards, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BOROUGH


QUEST_ID = "Jmr_FloodPlains"
FLOOD_PLAINS = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
_LEVEL = FLOOD_PLAINS + ".RuinFeilds_P:PersistentLevel."
JAMMER = f"SignalJammer_BP_C {_LEVEL}SignalJammer_BP_C_1"
TRIGGER = f"Quest_Trigger_BP_C {_LEVEL}Quest_Trigger_BP_C_1"
PREFLIGHT = "native-flood-jammer-preflight"
APPROACH = "native-flood-jammer-approach"
AIM = "native-flood-jammer-aim"
START = "native-flood-jammer-start"
TURN_IN = "native-flood-jammer-turn-in"
STEPS = {None, -10, -7, 1, 2}


def _pawn(host, actor):
    player = host.party["players"].get(actor)
    if (not host.native_profile or not host.native_zones or not host.native_guest
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != BOROUGH
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError("native_flood_jammer_board_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError("native_flood_jammer_actor_unavailable")
    return pawn


async def _command(host, bridge, action, pawn):
    try:
        snapshot = await bridge.command(action, pawn, timeout=8)
    except RuntimeError as error:
        if str(error).startswith("stock_flood_jammer_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get("native_flood_jammer", {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in ("operation", "map", "pawn", "board", "quest"))) != (
            "hosting", BOROUGH, action, BOROUGH, pawn, native_wire_quest.BOARD, QUEST_ID
    ) or proof.get("reputation", {}).keys() != {"8", "5"}:
        raise RuntimeError("The stock jammer board did not report the exact authored contract")
    if (any(key not in proof or proof[key] not in STEPS for key in ("step_before", "step_after"))
            or any(type(proof.get(key)) is not int for key in ("cash_before", "cash_after", "xp_before", "xp_after"))
            or any(type(row.get(field)) is not int for row in proof["reputation"].values() for field in ("before", "after"))):
        raise RuntimeError("The stock jammer board did not report quest and wallet readback")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor):
    pawn = _pawn(host, actor)
    proof = await _command(host, bridge, PREFLIGHT, pawn)
    if (any(proof[f"{field}_before"] != proof[f"{field}_after"] for field in ("step", "cash", "xp"))
            or any(row["before"] != row["after"] for row in proof["reputation"].values())):
        raise RuntimeError("The stock jammer preflight changed campaign state")
    return {"native_flood_jammer": proof}


async def approach(host, bridge, actor):
    pawn = _pawn(host, actor)
    if not host.fixtures:
        raise party.RuleError("native_flood_jammer_approach_unavailable")
    snapshot = await bridge.command(APPROACH, pawn, timeout=5)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", BOROUGH):
        raise RuntimeError("The stock jammer board approach left the qualified world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_flood_jammer_approach": {"pawn": pawn, "board": native_wire_quest.BOARD}}


async def aim_fixture(host, bridge, actor, guid):
    member = host.native_recipients().get(actor)
    if (not host.fixtures or not host.native_guest or not host.native_zones or not valid_guid(guid)
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("map") != FLOOD_PLAINS or not member or not member["connected"]):
        raise party.RuleError("Choose the exact enrolled guest and their owned stock pistol in Flood Plains")
    pawn = member["pawn"]
    try:
        snapshot = await bridge.command(AIM, pawn, guid, timeout=6)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    proof = snapshot.get("native_flood_jammer_aim", {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in ("map", "pawn", "jammer", "guid"))) != (
        "hosting", FLOOD_PLAINS, FLOOD_PLAINS, pawn, JAMMER, guid
    ):
        raise RuntimeError("The stock jammer aim did not verify the exact equipped pistol and target")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_flood_jammer_aim": proof}


def _credit(host, candidate, amount):
    recipients = sorted(set(host.roles().values()) & candidate["players"].keys())
    for member in recipients:
        player = candidate["players"][member]
        player["quest_xp"] = player.get("quest_xp", 0) + amount
        if member != "host":
            native_rewards.queue(player, amount)
    return recipients


async def act(host, bridge, actor, action):
    pawn = _pawn(host, actor)
    current = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if action == START and current:
        return {"effect": "native_quest_present", **current}
    if action == TURN_IN and current.get("step") == -10:
        return {"effect": "native_quest_already_settled", **current}
    expected = (None, 1, 0, 0) if action == START else (2, -10, 350, 1)
    if action == TURN_IN and (current.get("step") != 2 or "objective" not in current):
        raise party.RuleError("native_flood_jammer_objective_incomplete")
    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn)
        if before["step_after"] != expected[0]:
            raise party.RuleError("native_flood_jammer_stock_step_changed")
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, action, pawn)
        cash = proof["cash_after"] - proof["cash_before"]
        xp = proof["xp_after"] - proof["xp_before"]
        if ((proof["step_before"], proof["step_after"], cash, xp) != expected
                or any(proof[f"{field}_before"] != before[f"{field}_after"] for field in ("cash", "xp"))
                or any(row["before"] != before["reputation"][faction]["after"]
                       or action == START and row["before"] != row["after"]
                       for faction, row in proof["reputation"].items())):
            raise RuntimeError("The stock jammer board transition did not verify")
        candidate = party.load_party(host.party)
        record = {**current, "quest_id": QUEST_ID, "step": proof["step_after"], "actor": actor}
        if action == TURN_IN:
            recipients = _credit(host, candidate, xp)
            candidate["economy"].update(wallet=proof["cash_after"], native_wallet_active=True)
            record.update(recipients=recipients, cash=cash, xp=xp, wallet=proof["cash_after"],
                          reputation={key: row["after"] for key, row in proof["reputation"].items()})
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate)
        host.party, host.native_checkpoint_unsafe = candidate, False
        host.control.event("native_flood_jammer_board_committed", operation=action, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, "native_flood_jammer": proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def observe(host, bridge):
    snapshot = host.last_game
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe
            or snapshot.get("state") != "hosting" or snapshot.get("map") != FLOOD_PLAINS):
        return
    proof = snapshot.get("native_flood_jammer_objective", {})
    if (proof.get("map"), proof.get("jammer"), proof.get("trigger")) != (FLOOD_PLAINS, JAMMER, TRIGGER):
        return
    if proof.get("state", "").startswith("mutation_unverified_"):
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError("The stock jammer changed game state without a verified objective readback")
    expected = {"stock_objective_verified": (1, 2, 0), "stock_force_complete_verified": (None, -7, 1)}.get(proof.get("state"))
    if not expected:
        return
    current = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if current.get("step") != expected[0]:
        return
    if (any(type(proof.get(key)) is not int for key in ("xp_before", "xp_after"))
            or (proof.get("step_before"), proof.get("step_after"), proof["xp_after"] - proof["xp_before"]) != expected):
        host.native_checkpoint_unsafe = True
        raise RuntimeError("The authored jammer signal diverged from its verified stock progress")
    shooter = proof["shooter"]
    if proof["shooter_role"] == "host":
        if shooter != snapshot.get("pawns", {}).get("local", {}).get("name"):
            return
        actor = "host"
    elif proof["shooter_role"] == "guest":
        actor = next((owner for owner, member in host.native_recipients().items() if member["pawn"] == shooter), None)
        if not actor:
            return
    else:
        actor = "world"
    candidate = party.load_party(host.party)
    record = {**current, "quest_id": QUEST_ID, "step": expected[1], "actor": actor, "objective": proof}
    if expected[2]:
        record.update(recipients=_credit(host, candidate, expected[2]), xp=expected[2], cash=0)
    candidate.setdefault("native_quests", {})[QUEST_ID] = record
    host.native_checkpoint_unsafe = True
    try:
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_flood_jammer_objective_committed", revision=revision, **record)


async def project_client(client):
    game = client.last_game
    local = game.get("native_flood_jammer_projection", {})
    record = client.gameplay.get("native_quests", {}).get(QUEST_ID, {})
    objective = record.get("objective", {})
    if (game.get("state") != "connected" or game.get("map") != FLOOD_PLAINS
            or (local.get("map"), local.get("actor"), local.get("state")) != (FLOOD_PLAINS, JAMMER, "present")
            or record.get("step") not in (2, -7, -10)
            or (objective.get("map"), objective.get("jammer"), objective.get("trigger")) != (FLOOD_PLAINS, JAMMER, TRIGGER)
            or objective.get("state") not in ("stock_objective_verified", "stock_force_complete_verified")):
        return
    snapshot = await client.bridge.command("native-flood-jammer-project", "destroyed", timeout=3)
    proof = snapshot.get("native_flood_jammer_projection", {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("actor")) != (
        "connected", FLOOD_PLAINS, FLOOD_PLAINS, JAMMER
    ) or proof.get("state") not in ("hidden", "absent"):
        raise RuntimeError("The guest did not verify the destroyed stock jammer")
    client.last_game = snapshot
    client.control.event("native_flood_jammer_projected", **proof)
