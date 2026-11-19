"""Coordinate the stock Flood Control valve, board, and personal rewards."""

import asyncio
import time
import uuid

from . import native_host_save, native_rewards, party
from .native_gold import valid_guid
from .native_wire_quest import BOARD
from .travel import BOROUGH


QUEST = "Sub_Valve"
TAG = "native-subway-valve"
KEY = "native_subway_valve"
SUBWAY = "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P"
ROOT = f"{SUBWAY}.Subway_01_P:PersistentLevel."
VALVE = f"ValveSwitch_BP_C {ROOT}ValveSwitch_BP_C_1"
TRIGGER = f"Quest_Trigger_BP_C {ROOT}Quest_Trigger_BP_C_1"
ARTIFACT = "/Game/Classes/Items/Artifacts/Art_AcidResist.Art_AcidResist_C"
HELMET = "/Game/Classes/Armor_Items/Helm_HazOp.Helm_HazOp_C"
PREFLIGHT, APPROACH, START, TURN_IN, XP_FIXTURE, ACTIVATE, FINALIZE, PROJECT, CAPTURE, GRANT, POSITION, PRESS = (
    f"{TAG}-{verb}" for verb in ("preflight", "approach", "start", "turn-in", "xp-fixture", "activate", "finalize", "project",
                                 "reward-capture", "reward-grant", "position-fixture", "input-fixture")
)
BOARD_KEY, OBJECTIVE, SCENE, REWARD = (KEY + "_" + part for part in ("board", "objective", "scene", "reward"))
FIXTURE_KEY = KEY + "_fixture"
PENDING = KEY + "_rewards"
INPUT = "subway-valve"
FACTIONS = {"2", "4", "5"}


def valid_epoch(value) -> bool:
    return isinstance(value, str) and value != "0" * 32 and party.REQUEST_ID.fullmatch(value) is not None


def _step(value):
    if value == "-":
        return None
    result = int(value)
    if result not in (1, 2, -10):
        raise ValueError("Invalid stock Flood Control quest step")
    return result


def _pawn_name(value, area):
    expected = f"Player_FP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.Player_FP_C_"
    return isinstance(value, str) and value.startswith(expected) and value[len(expected):].isascii() and value[len(expected):].isdigit()


def parse_board(fields, phase, area):
    operation, map_name, pawn, board = fields[1:5]
    art, mask = fields[12:14]
    if (len(fields) != 14 or phase != "hosting" or area != BOROUGH or (map_name, board) != (BOROUGH, BOARD)
            or not _pawn_name(pawn, area) or operation not in ("preflight", "start", "turn-in", "xp-fixture")
            or (operation == "turn-in" and (not valid_guid(art) or not valid_guid(mask) or art == mask))
            or (operation != "turn-in" and (art, mask) != ("-", "-"))):
        raise ValueError("Invalid native Flood Control board proof")
    step_before, step_after = map(_step, fields[5:7])
    flag, cash_before, cash_after, xp_before, xp_after = map(int, fields[7:12])
    if flag not in (0, 1) or min(cash_before, cash_after, xp_before, xp_after) < 0:
        raise ValueError("Invalid Flood Control board balances")
    return dict(operation=operation, quest=QUEST, map=area, pawn=pawn, board=board, step_before=step_before,
                step_after=step_after, flag=flag, cash_before=cash_before, cash_after=cash_after, xp_before=xp_before,
                xp_after=xp_after, bundle=[art, mask] if operation == "turn-in" else None, reputation={})


def parse_objective(fields, phase, area):
    if len(fields) != 18 or phase != "hosting" or area != SUBWAY or tuple(fields[1:4]) != (SUBWAY, VALVE, TRIGGER):
        raise ValueError("Invalid Flood Control objective scope")
    pawn, epoch = fields[4:6]
    rows = tuple(map(int, fields[6:]))
    if (not _pawn_name(pawn, area) or not valid_epoch(epoch) or rows[:4] != (1, 2, 0, 1)
            or rows[4] != rows[5] or rows[6] != rows[7] or rows[9] < rows[8] + 20 or rows[11] - rows[10] != 200):
        raise ValueError("Invalid stock Flood Control objective transition")
    return dict(map=area, valve=VALVE, trigger=TRIGGER, pawn=pawn, epoch=epoch, step_after=2,
                flag=1, commune_before=rows[8], commune_after=rows[9], advantage_before=rows[10], advantage_after=rows[11])


def parse_scene(fields, phase, area):
    if len(fields) not in (4, 5, 9) or area != SUBWAY or fields[1] != SUBWAY or fields[2] not in ("present", "settling", "closed"):
        raise ValueError("Invalid Flood Control scene")
    state, owner = fields[2:4]
    epoch = fields[4] if len(fields) >= 5 else "-"
    status = dict(map=area, state=state, pawn=None, epoch=None if epoch == "-" else epoch)
    if phase == "connected" and len(fields) in (5, 9) and _pawn_name(owner, area) and (epoch == "-" or valid_epoch(epoch)):
        status["pawn"] = owner
    elif phase != "hosting" or len(fields) not in (4, 9) or owner != "host" or epoch != "-":
        raise ValueError("Invalid Flood Control local scene scope")
    if len(fields) == 9:
        box, sludge, audio, powered = fields[5:9]
        actors = ("present", "retired", "absent")
        derived = ("present" if box == sludge == audio == "present" else
                   "closed" if box == "absent" and sludge != "present" and audio != "present" else "settling")
        if box not in ("present", "absent") or sludge not in actors or audio not in actors or powered not in ("0", "1") or derived != state:
            raise ValueError("Invalid Flood Control presentation proof")
        status.update(box=box, sludge=sludge, audio=audio, powered=powered == "1")
    return status


def parse_reward(fields, phase, area):
    if len(fields) != 9 or phase != "hosting":
        raise ValueError("Invalid Flood Control reward")
    report_map, pawn, state, slot, art, mask, assigned_art, assigned_mask = fields[1:]
    if (report_map != area or not _pawn_name(pawn, area) or state not in ("captured", "granted", "already_granted")
            or not native_host_save.SLOT.fullmatch(slot) or any(not valid_guid(item) for item in fields[5:])
            or art == mask or assigned_art == assigned_mask
            or (state == "captured" and (area != BOROUGH or (art, mask) != (assigned_art, assigned_mask)))
            or (state != "captured" and {art, mask} & {assigned_art, assigned_mask})):
        raise ValueError("Invalid exact Flood Control reward identity")
    return dict(map=area, pawn=pawn, state=state, slot=slot, template=[art, mask], assigned=[assigned_art, assigned_mask])


def parse_fixture(fields, phase, area):
    if (len(fields) != 4 or phase != "hosting" or area != SUBWAY or fields[1] != SUBWAY
            or not _pawn_name(fields[2], area) or fields[3] not in ("positioned", "stock_input")):
        raise ValueError("Invalid stock valve fixture")
    return dict(map=area, pawn=fields[2], state=fields[3])


def _owner(host, character, area):
    game = host.last_game
    candidate = host.party["players"].get(character)
    if (not host.native_zones or not host.native_guest or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get("map") != area
            or time.monotonic() - host.last_seen >= 3 or not candidate or candidate["downed"]
            or host.party["wipe"] or character in host.party["pending_spawns"]):
        raise party.RuleError("stock_subway_valve_authority_scope")
    if character == "host":
        pawn = game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(character, {})
        pawn = member.get("pawn") if member.get("connected") else None
    if not _pawn_name(pawn, area):
        raise party.RuleError("stock_subway_valve_actor_scope")
    return pawn


async def _call(host, bridge, command, area, *args):
    try:
        snapshot = await bridge.command(command, *args, timeout=8)
    except RuntimeError as error:
        if str(error).startswith(("stock_subway_valve_", "stock_wire_quest_")):
            raise party.RuleError(str(error)) from error
        raise
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise RuntimeError("The Flood Control command left the qualified authority world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return snapshot


async def _board(host, bridge, pawn, action):
    snapshot = await _call(host, bridge, action, BOROUGH, pawn)
    proof = snapshot.get(BOARD_KEY, {})
    if (proof.get("pawn"), proof.get("operation"), set(proof.get("reputation", {}))) != (
            pawn, action.removeprefix(TAG + "-"), FACTIONS):
        raise RuntimeError("The Borough board proof did not identify this exact Flood Control action")
    return proof


async def preflight(host, bridge, character, target=None):
    if target is not None:
        raise party.RuleError("stock_subway_valve_has_fixed_board")
    return {BOARD_KEY: await _board(host, bridge, _owner(host, character, BOROUGH), PREFLIGHT)}


async def fixture(host, bridge, character, action):
    if not host.fixtures or action not in (APPROACH, POSITION, PRESS):
        raise party.RuleError("stock_subway_valve_fixture_disabled")
    area = BOROUGH if action == APPROACH else SUBWAY
    pawn = _owner(host, character, area)
    if action == PRESS and character != "host":
        raise party.RuleError("stock_subway_valve_fixture_requires_local_host")
    try:
        snapshot = await _call(host, bridge, action, area, pawn)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    if action == APPROACH:
        return {"map": area, "pawn": pawn, "board": BOARD}
    proof = snapshot.get(FIXTURE_KEY, {})
    if (proof.get("pawn"), proof.get("state")) != (pawn, "positioned" if action == POSITION else "stock_input"):
        raise RuntimeError("The exact stock valve input fixture did not verify")
    return {FIXTURE_KEY: proof}


async def activate(host, bridge, character, epoch):
    pawn = _owner(host, character, SUBWAY)
    if not valid_epoch(epoch) or epoch != host.zone_epoch:
        raise party.RuleError("stock_subway_valve_stale_visit")
    current = host.party.get("native_quests", {}).get(QUEST, {})
    if current.get("step") in (2, -10):
        return {"effect": "native_subway_valve_already_closed", **current}
    scene = host.last_game.get(SCENE, {})
    if current.get("step") != 1 or scene.get("state") != "present":
        raise party.RuleError("stock_subway_valve_active_objective_required")
    await host.checkpoint_native(bridge)
    if _owner(host, character, SUBWAY) != pawn or host.zone_epoch != epoch:
        raise party.RuleError("stock_subway_valve_owner_changed")
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await _call(host, bridge, ACTIVATE, SUBWAY, pawn, epoch)
        except party.RuleError:
            host.native_checkpoint_unsafe = False
            raise
        objective = snapshot.get(OBJECTIVE, {})
        if (objective.get("pawn"), objective.get("epoch"), objective.get("flag")) != (pawn, epoch, 1):
            raise RuntimeError("The stock valve did not verify the qualified pawn, visit and closed flag")
        def verified(row):
            return (row.get("state") == "hosting" and row.get("map") == SUBWAY
                    and row.get(OBJECTIVE) == objective and row.get(SCENE, {}).get("state") == "closed")

        closed = snapshot
        if not verified(snapshot):
            await asyncio.sleep(1)  # Preserve the exact stock audio component's one-second FadeOut.
            try:
                closed = await _call(host, bridge, FINALIZE, SUBWAY, pawn, epoch)
                if closed.get("ack", {}).get("message") != "exact Flood Control authority presentation requested":
                    raise RuntimeError("The stock Flood Control scene finalization did not acknowledge its exact transaction")
                host.control.event("native_subway_valve_presentation_requested", pawn=pawn, epoch=epoch, scene=closed.get(SCENE, {}))
                if not verified(closed):
                    closed = await bridge.wait_for(verified, 5)
            except (party.RuleError, TimeoutError) as error:
                latest = bridge.snapshot() or host.last_game
                scene = latest.get(SCENE, {})
                proof = ", ".join(f"{key}={scene.get(key, 'unknown')}" for key in ("state", "box", "sludge", "audio"))
                raise RuntimeError(f"mutation_unverified_stock_subway_valve_scene: {error} ({proof})") from error
        host.last_game, host.last_seen = closed, time.monotonic()
        candidate = party.load_party(host.party)
        record = {"quest_id": QUEST, "step": 2, "actor": character, "objective": objective}
        candidate["native_quests"][QUEST] = record
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_subway_valve_closed", revision=revision, **record)
    return {"effect": "native_quest_changed", **record}


async def _reward(host, bridge, action, pawn, slot, bundle, assigned=None):
    area = host.last_game["map"]
    extra = (pawn, *assigned) if assigned else ()
    snapshot = await _call(host, bridge, action, area, slot, *bundle, *extra)
    proof = snapshot.get(REWARD, {})
    states = ("granted", "already_granted") if assigned else ("captured",)
    if ((proof.get("pawn"), proof.get("slot"), proof.get("template"), proof.get("assigned")) != (pawn, slot, bundle, assigned or bundle)
            or proof.get("state") not in states):
        raise RuntimeError("The exact Flood Control reward bundle did not survive native readback")


async def _deliver(host, bridge, candidate):
    stage = candidate.get(PENDING)
    if not stage or not host.last_game.get("native_guest_xp_ready"):
        return frozenset()
    authenticated = host.native_recipients()
    deliver = [actor for actor in stage["pending"] if authenticated.get(actor, {}).get("connected")]
    if not deliver:
        return frozenset()
    frame = stage["escrow"]
    await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    for actor in deliver:
        await _reward(host, bridge, GRANT, authenticated[actor]["pawn"], frame["slot"], stage["template"], stage["pending"][actor])
        native_rewards.queue(candidate["players"][actor], 1)
        del stage["pending"][actor]
    if not stage["pending"]:
        del candidate[PENDING]
    recipients = [row for row in native_rewards.pending_recipients(host, candidate) if row[0] in deliver]
    applied = await native_rewards.project(host, bridge, candidate, recipients)
    if set(deliver) != set(applied):
        raise RuntimeError("Flood Control item recipients did not also receive their exact stock XP")
    return applied


async def board_action(host, bridge, character, action):
    pawn = _owner(host, character, BOROUGH)
    current = host.party.get("native_quests", {}).get(QUEST, {})
    if action == START and current:
        return {"effect": "native_quest_present", **current}
    if action == TURN_IN and current.get("step") == -10:
        return {"effect": "native_quest_already_settled", **current}
    if action == TURN_IN and (current.get("step") != 2 or current.get("objective", {}).get("flag") != 1):
        raise party.RuleError("stock_subway_valve_quest_objective_not_complete")
    if action == XP_FIXTURE and (character != "host" or not host.fixtures):
        raise party.RuleError("stock_subway_valve_xp_fixture_unavailable")
    await host.checkpoint_native(bridge)
    if _owner(host, character, BOROUGH) != pawn:
        raise party.RuleError("stock_subway_valve_actor_scope")
    host.native_checkpoint_unsafe = True
    try:
        try:
            proof = await _board(host, bridge, pawn, action)
        except party.RuleError:
            host.native_checkpoint_unsafe = False
            raise
        expected = {START: (None, 1, 0, 0), TURN_IN: (2, -10, 1, 1), XP_FIXTURE: (None, None, 0, 7)}[action]
        if ((proof["step_before"], proof["step_after"], proof["flag"], proof["xp_after"] - proof["xp_before"]) != expected
                or proof["cash_before"] != proof["cash_after"]):
            raise RuntimeError("The stock Flood Control board did not verify the exact transition and balance")
        candidate = party.load_party(host.party)
        delivered = frozenset()
        record = {**current, "quest_id": QUEST, "step": proof["step_after"], "actor": character}
        if action == TURN_IN:
            recipients = sorted(set(host.roles().values()) & candidate["players"].keys())
            bundle = proof["bundle"]
            assert bundle
            slot = native_host_save.new_slot()
            host_pawn = host.last_game["pawns"]["local"]["name"]
            await _reward(host, bridge, CAPTURE, host_pawn, slot, bundle)
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, BOROUGH, flush=True)
            assigned = {name: [uuid.uuid5(uuid.UUID(hex=template), f"flood-control:{name}").hex.upper() for template in bundle]
                        for name in recipients if name != "host"}
            if assigned:
                candidate[PENDING] = {"escrow": frame, "template": bundle, "pending": assigned.copy()}
                delivered = await _deliver(host, bridge, candidate)
            for name in recipients:
                player = candidate["players"][name]
                player["quest_xp"] = player.get("quest_xp", 0) + 1
            candidate["economy"]["wallet"] = proof["cash_after"]
            record.update(recipients=recipients, xp=1, cash=0, wallet=proof["cash_after"], rewards={"host": bundle, **assigned},
                          reputation=proof["reputation"])
        if action != XP_FIXTURE:
            candidate.setdefault("native_quests", {})[QUEST] = record
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=delivered)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event("native_subway_valve_board_committed", operation=action, revision=revision, **record)
    return {BOARD_KEY: proof} if action == XP_FIXTURE else {"effect": "native_quest_changed", **record}


async def settle(host, bridge):
    if (not host.party.get(PENDING) or host.last_game.get("state") != "hosting" or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party["wipe"] or host.last_game.get("map") not in host.allowed_maps):
        return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        delivered = await _deliver(host, bridge, candidate)
        if delivered:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=delivered)
            host.party = candidate
            host.control.event("native_subway_valve_rewards_delivered", revision=revision, recipients=sorted(delivered))
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        host.stopping = True
        raise
    host.native_checkpoint_unsafe = False


async def project_client(client):
    game, epoch = client.last_game, client.zone_epoch
    local = game.get(SCENE, {})
    party_view = client.gameplay.get("native_quests", {}).get(QUEST, {})
    proof = party_view.get("objective", {})
    if (game.get("state") != "connected" or game.get("map") != SUBWAY or not client.host_session or not valid_epoch(epoch)
            or client.needs_travel or client.server_transitioning or time.monotonic() - client.gameplay_seen >= 2.5
            or party_view.get("step") not in (2, -10) or (proof.get("map"), proof.get("valve"), proof.get("trigger"), proof.get("flag"))
            != (SUBWAY, VALVE, TRIGGER, 1) or local.get("epoch") == epoch
            or not _pawn_name(local.get("pawn"), SUBWAY) or local["pawn"] != game.get("pawns", {}).get("local", {}).get("name")):
        return
    snapshot = await client.bridge.command(PROJECT, epoch, local["pawn"], timeout=3)
    verified = snapshot.get(SCENE, {})
    if ((snapshot.get("state"), snapshot.get("map"), verified.get("epoch"), verified.get("pawn"))
            != ("connected", SUBWAY, epoch, local["pawn"])):
        raise RuntimeError("The guest Flood Control closure did not verify this local pawn and visit")
    client.last_game = snapshot
