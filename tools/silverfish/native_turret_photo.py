"""Record two distinct stock photo targets and settle the authored contract once."""

import time

from . import native_loot, native_rewards, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BOROUGH, DEAD_BRIDGE

QUEST_ID = "Photo_DB_Turrets"
PREFLIGHT = "native-turret-photo-preflight"
CAPTURE = "native-turret-photo-capture"
XP_FIXTURE = "native-turret-photo-xp-fixture"
ACTIONS = {PREFLIGHT: "preflight", "native-turret-photo-start": "start", CAPTURE: "capture",
           "native-turret-photo-turn-in": "turn_in", XP_FIXTURE: "xp_fixture"}
TARGETS = {"turret-13", "turret-15"}


def _pawn(host, character, area):
    player = host.party["players"].get(character)
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"] or not player or player["downed"]
            or character in host.party["pending_spawns"] or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != area or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_turret_photo_actor_unavailable")
    pawn = native_loot._scope(host, character)
    if not pawn:
        raise party.RuleError("native_turret_photo_actor_unavailable")
    return pawn


async def _command(host, bridge, action, pawn, area, target, *arguments):
    try:
        snapshot = await bridge.command(action, pawn, *arguments, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("stock_turret_photo_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get("native_turret_photo", {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in ("action", "map", "pawn", "quest", "target"))) != (
            "hosting", area, ACTIONS[action], area, pawn, QUEST_ID, target):
        raise RuntimeError("The stock photo report did not verify the exact player and target")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, character):
    pawn = _pawn(host, character, BOROUGH)
    return {"native_turret_photo": await _command(host, bridge, PREFLIGHT, pawn, BOROUGH, native_wire_quest.BOARD)}


async def xp_fixture(host, bridge):
    pawn = _pawn(host, "host", BOROUGH)
    if not host.fixtures or host.party.get("native_quests"):
        raise party.RuleError("native_turret_photo_xp_fixture_unavailable")
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        proof = await _command(host, bridge, XP_FIXTURE, pawn, BOROUGH, native_wire_quest.BOARD)
        if ((proof["step_before"], proof["step_after"], proof["xp_before"], proof["xp_after"]) != (None, None, 0, 4)
                or proof["cash_before"] != proof["cash_after"] or proof["rep_before"] != proof["rep_after"]):
            raise RuntimeError("The exact staged photo eligibility did not verify the stock XP change")
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = False
        return {"native_turret_photo": proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise


async def advance(host, bridge, character, action, target=None):
    area = DEAD_BRIDGE if action == CAPTURE else BOROUGH
    pawn = _pawn(host, character, area)
    current = host.party.get("native_quests", {}).get(QUEST_ID, {})
    photos = current.get("photos", {})
    step = current.get("step")
    if action == CAPTURE:
        if not isinstance(target, str) or target not in TARGETS or step not in (1, 2, 3, -10):
            raise party.RuleError("native_turret_photo_requires_expected_target")
        if target in photos:
            return {"effect": "native_turret_photo_already_captured", "quest_id": QUEST_ID,
                    "photo": photos[target], "step": step}
        if step != 1 + len(photos) or step not in (1, 2):
            raise party.RuleError("native_turret_photo_untracked_progress")
        arguments = (target, str(step))
        expected = step + 1
    else:
        target = native_wire_quest.BOARD
        if action == "native-turret-photo-start" and step is not None:
            return {"effect": "native_quest_present", **current}
        if action == "native-turret-photo-turn-in" and step == -10:
            return {"effect": "native_quest_already_settled", **current}
        if action == "native-turret-photo-turn-in" and (step != 3 or set(photos) != TARGETS):
            raise party.RuleError("native_turret_photo_objectives_incomplete")
        arguments = ()
        expected = 1 if action == "native-turret-photo-start" else -10

    host.transitioning = True
    try:
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, action, pawn, area, target, *arguments)
        cash = proof["cash_after"] - proof["cash_before"]
        xp = proof["xp_after"] - proof["xp_before"]
        if (proof["step_before"], proof["step_after"], cash, xp) != (
                step, expected, 700 if expected == -10 else 0, 1 if expected == -10 else 0):
            raise RuntimeError("Stock photo progress or shared balances did not match the committed transition")
        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST_ID, "step": expected, "actor": character, "photos": dict(photos)}
        if action == CAPTURE:
            if not valid_guid(proof.get("camera")) or proof.get("visibility") is None or not .75 <= proof["visibility"] <= 1:
                raise RuntimeError("The stock photo did not verify the guest's exact camera and framing")
            record["photos"][target] = dict(target=target, camera=proof["camera"], visibility=proof["visibility"], actor=character)
        if expected == -10:
            members = sorted(set(host.roles().values()) & candidate["players"].keys())
            for member in members:
                player = candidate["players"][member]
                player["quest_xp"] = player.get("quest_xp", 0) + xp
                if member != "host":
                    native_rewards.queue(player, xp)
            candidate["economy"]["wallet"] = proof["cash_after"]
            candidate["economy"]["native_wallet_active"] = True
            record.update(recipients=members, cash=cash, xp=xp, wallet=proof["cash_after"], commune_rep=proof["rep_after"])
        candidate.setdefault("native_quests", {})[QUEST_ID] = record
        revision = await host.checkpoint_native(bridge, candidate)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_turret_photo_committed", action=ACTIONS[action], target=target, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, "native_turret_photo": proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
