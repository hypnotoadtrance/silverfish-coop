"""Host-owned actions for the staged Signal Jammer quest."""

from . import native_broker, native_wire_quest, native_rewards, native_turret_photo, party
from .travel import CROSSROADS

QUEST_ID = "Jmr_CrossRoads"
STARTED = 1
READY = 2
COMPLETED = -10
REWARD_CASH = 200
REWARD_XP = 1


def _stock_step(snapshot: dict) -> int | None:
    quest = snapshot.get("native_quest", {})
    if quest.get("id") == QUEST_ID and quest.get("reward_cash") == str(REWARD_CASH):
        try:
            return int(quest["step"])
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _record(host, actor: str, step: int, eligible: set[str]) -> dict:
    candidate = party.load_party(host.party)
    record = {"quest_id": QUEST_ID, "step": step, "actor": actor}
    if step == COMPLETED:
        recipients = sorted(eligible & candidate["players"].keys())
        for character in recipients:
            player = candidate["players"][character]
            player["quest_xp"] = player.get("quest_xp", 0) + REWARD_XP
            if character != "host":
                native_rewards.queue(player, REWARD_XP)
        candidate["economy"]["wallet"] += REWARD_CASH
        record.update(recipients=recipients, xp=REWARD_XP, cash=REWARD_CASH,
                      wallet=candidate["economy"]["wallet"])
    candidate.setdefault("native_quests", {})[QUEST_ID] = record
    host.checkpoint(candidate)
    host.party = candidate
    return record


async def action(host, bridge, actor: str, verb: str, target: str | None) -> dict:
    if target == native_wire_quest.QUEST_ID and verb == "native-quest-start":
        return await native_wire_quest.start(host, bridge, actor)
    if target == native_wire_quest.QUEST_ID and verb == "native-quest-turnin":
        return await native_wire_quest.turn_in(host, bridge, actor)
    if target == native_turret_photo.QUEST_ID and verb in ("native-quest-start", "native-quest-turnin"):
        action = "native-turret-photo-start" if verb == "native-quest-start" else "native-turret-photo-turn-in"
        return await native_turret_photo.advance(host, bridge, actor, action)
    if isinstance(target, str) and target in native_broker.QUESTS:
        return await native_broker.action(host, bridge, actor, verb, quest_id=target)
    roles = host.roles()
    player = host.party["players"].get(actor)
    if (not host.fixtures or not player or actor not in host.authoritative_pawns(roles)
            or host.party["wipe"] or host.transitioning):
        raise party.RuleError("native_quest_unavailable")
    if player["downed"]:
        raise party.RuleError("actor_downed")
    if target != QUEST_ID:
        raise party.RuleError("unknown_native_quest")

    current = host.party.get("native_quests", {}).get(QUEST_ID)
    old_step = current["step"] if current else None
    if verb == "native-quest-start":
        step = old_step if current else STARTED
        if current:
            return {"effect": "native_quest_present", **current}
        command = ("native-quest-project", str(step))
    elif verb == "native-quest-objective" and actor == "host":
        if old_step not in (STARTED, READY):
            raise party.RuleError("native_quest_not_active")
        step = READY
        command = (("native-quest-trigger",) if host.last_game["map"] == CROSSROADS and old_step != READY
                   else ("native-quest-project", str(step)))
    elif verb == "native-quest-turnin":
        if old_step == COMPLETED:
            return {"effect": "native_quest_already_settled", **current}
        if old_step != READY:
            raise party.RuleError("native_quest_objective_incomplete")
        step = COMPLETED
        command = ("native-quest-turn-in",)
    else:
        raise party.RuleError("native_quest_action_unavailable")

    snapshot = await bridge.command(*command, timeout=4)
    if _stock_step(snapshot) != step:
        raise RuntimeError("Stock quest read-back differs from the requested state")
    return {"effect": "native_quest_changed", **_record(host, actor, step, set(roles.values()))}


async def project(host, bridge) -> None:
    quest = host.party.get("native_quests", {}).get(QUEST_ID)
    observed = _stock_step(host.last_game)
    if quest and observed != quest["step"]:
        if (quest["step"], observed) in ((STARTED, READY), (READY, COMPLETED)):
            _record(host, "world", observed, set(host.roles().values()))
        else:
            await bridge.command("native-quest-project", str(quest["step"]), timeout=4)
