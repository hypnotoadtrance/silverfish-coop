"""Record stock photo objectives and settle the Dead Bridge photography contract."""

import re
import time
import uuid

from . import native_computer_board, native_flood_jammer, native_loot, native_quest_catalog, native_rewards, native_subway_valve, native_wire_quest, party
from .native_gold import valid_guid
from .travel import BOROUGH, DEAD_BRIDGE

QUEST_ID = "Photo_DB_Turrets"
PREFLIGHT = "native-turret-photo-preflight"
CAPTURE = "native-turret-photo-capture"
CAPTURE_FIXTURE = "native-turret-photo-capture-fixture"
SHUTTER = "native-turret-photo-shutter"
STOCK_SHUTTER = "native-stock-photo-shutter"
STOCK_SCOPE = "stock-photo"
STOCK_READY = "native_stock_photo_ready"
STOCK_PROOF = "native_stock_photo"
XP_FIXTURE = "native-turret-photo-xp-fixture"
BOARD_LIST = "native-bounty-board-list"
HOST_INPUT_FIXTURE = "native-host-camera-input-fixture"
HOST_AIM_FIXTURE = "native-host-camera-aim-fixture"
HOST_EQUIP_FIXTURE = "native-host-camera-equip-fixture"
ACTIONS = {PREFLIGHT: "preflight", "native-turret-photo-start": "start", CAPTURE: "capture", CAPTURE_FIXTURE: "capture",
           "native-turret-photo-turn-in": "turn_in", XP_FIXTURE: "xp_fixture", HOST_AIM_FIXTURE: "aim_fixture"}
TARGETS = {"turret-13", "turret-15"}
BOARDS = {area: native_computer_board.BOARDS[area] for area in native_computer_board.BOROUGH_SCIENTIST_BOARDS}
CATALOG_BOARDS = native_computer_board.BOARDS
SUPPORTED = {native_wire_quest.QUEST_ID, QUEST_ID, *native_computer_board.CONTRACTS,
             native_flood_jammer.FLOOD.quest, native_flood_jammer.BLACK_LAKE.quest}
EXPERIMENTAL = {native_flood_jammer.LENNY.quest, native_computer_board.MOLASSES.quest_id,
                native_computer_board.SAVLO_KEYCARD.quest_id, native_subway_valve.QUEST,
                native_computer_board.MISSING_CONTACT.quest_id, native_flood_jammer.SPRATT.quest,
                native_flood_jammer.CROSSROADS_JAMMER.quest}


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
    area = host.last_game.get("map")
    if area not in BOARDS:
        raise party.RuleError("native_turret_photo_board_unavailable")
    pawn = _pawn(host, character, area)
    return {"native_turret_photo": await _command(host, bridge, PREFLIGHT, pawn, area, BOARDS[area])}


async def offers(host, bridge, character, offset):
    if offset is not None and (not isinstance(offset, str) or not offset.isascii() or not offset.isdigit()
                               or len(offset) > 3 or int(offset) > 128):
        raise party.RuleError("native_bounty_board_offset_invalid")
    area = host.last_game.get("map")
    if area not in CATALOG_BOARDS:
        raise party.RuleError("native_bounty_board_unavailable")
    pawn = _pawn(host, character, area)
    snapshot = await bridge.command(BOARD_LIST, pawn, timeout=5)
    proof = snapshot.get("native_bounty_board", {})
    catalog = snapshot.get("native_bounty_offers", {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("board")) != (
            "hosting", area, area, pawn, CATALOG_BOARDS[area]
    ) or "board" not in catalog or catalog.get("sequence") != snapshot.get("sequence"):
        raise RuntimeError("The native board did not verify the current stock offers")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    rows = native_quest_catalog.page(catalog, f"board@{int(offset or 0)}", board=True)
    for quest in rows["quests"]:
        quest["co_op"] = ("experimental" if quest["id"] in EXPERIMENTAL else
                          "verified" if quest["id"] in SUPPORTED else "unqualified")
    return {"native_bounty_board": {**proof, **rows}}


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


async def shutter(host, bridge, character, camera, operation):
    pawn = _pawn(host, character, DEAD_BRIDGE)
    if not valid_guid(camera) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("native_turret_photo_shutter_invalid")
    request = [SHUTTER, camera, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != request:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if character == "host":
        equipped = host.last_game.get("native_host_camera_scope", {})
        valid_selection = (equipped.get("map"), equipped.get("pawn"), equipped.get("guid"), equipped.get("mode")) == (
            DEAD_BRIDGE, pawn, camera, "stock_held"
        )
    else:
        equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
        valid_selection = (equipped.get("kind"), equipped.get("guid")) == ("camera", camera)
    if not valid_selection:
        raise party.RuleError("native_turret_photo_exact_camera_unavailable")
    quest = host.party.get("native_quests", {}).get(QUEST_ID, {})
    if quest.get("step") not in (1, 2):
        raise party.RuleError("native_turret_photo_no_active_capture")
    now = time.monotonic()
    if now < host.camera_ready_at.get(character, 0):
        raise party.RuleError("native_turret_photo_shutter_cooling_down")
    host.camera_ready_at[character] = now + 3
    for target in sorted(TARGETS - quest.get("photos", {}).keys()):
        try:
            return await advance(host, bridge, character, CAPTURE, target, expected_camera=camera,
                                 shutter_request=(operation, request))
        except party.RuleError as error:
            if str(error) not in {"stock_turret_photo_target_out_of_range", "stock_turret_photo_target_not_in_stock_frame"}:
                raise
    raise party.RuleError("native_turret_photo_no_target_in_frame")


async def stock_shutter(host, bridge, character, target, operation):
    area = host.last_game.get("map")
    pawn = _pawn(host, character, area)
    parts = target.split("@", 2) if isinstance(target, str) else []
    if len(parts) != 3:
        raise party.RuleError("native_stock_photo_shutter_invalid")
    camera, visit, view = parts
    if (character == "host" or visit != host.zone_epoch or area not in host.allowed_maps
            or len(view) > 180 or not re.fullmatch(r"camera:(?:-?[0-9]+\.[0-9]+,){6}-?[0-9]+\.[0-9]+", view)
            or not valid_guid(camera) or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("native_stock_photo_shutter_invalid")
    request = [STOCK_SHUTTER, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != request:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
    if (equipped.get("kind"), equipped.get("guid")) != ("camera", camera) or host.last_game.get(STOCK_READY) != area:
        raise party.RuleError("native_stock_photo_camera_unavailable")
    now = time.monotonic()
    if now < host.camera_ready_at.get(character, 0):
        raise party.RuleError("native_stock_photo_shutter_cooling_down")
    host.camera_ready_at[character] = now + 3

    host.transitioning = host.stock_photo_in_progress = True
    try:
        host.native_checkpoint_unsafe = True
        try:
            snapshot = await bridge.command(STOCK_SHUTTER, pawn, camera, view, timeout=3)
        except RuntimeError as error:
            if str(error).startswith(("stock_photo_", "stock_turret_photo_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(STOCK_PROOF, {})
        quest = proof.get("quest")
        before, after, score = (proof.get(key) for key in ("step_before", "step_after", "visibility"))
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("camera"))
                != ("hosting", area, area, pawn, camera)
                or not isinstance(quest, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,96}", quest) or quest == QUEST_ID
                or type(before) is not int or type(after) is not int or not 1 <= before < after or after - before > 16
                or type(score) not in (int, float) or not 0 < score <= 1):
            raise RuntimeError("The stock field photo did not verify the exact guest camera and quest progress")
        candidate = party.load_party(host.party)
        record = {"quest_id": quest, "step": after, "actor": character}
        candidate.setdefault("native_quests", {})[quest] = record
        result = {"effect": "native_quest_changed", **record, STOCK_PROOF: proof, "shutter_view": view}
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": request, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.last_game, host.last_seen, host.party = snapshot, time.monotonic(), candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_stock_photo_committed", **result)
        return result
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = host.stock_photo_in_progress = False


async def aim_host_fixture(host, bridge, target):
    pawn = _pawn(host, "host", DEAD_BRIDGE)
    quest = host.party.get("native_quests", {}).get(QUEST_ID, {})
    step = quest.get("step")
    scope = host.last_game.get("native_host_camera_scope", {})
    if (not host.fixtures or not host.host_camera_input or step not in (1, 2)
            or not isinstance(target, str) or target not in TARGETS - quest.get("photos", {}).keys()
            or scope.get("pawn") != pawn or not valid_guid(scope.get("guid"))):
        raise party.RuleError("native_turret_photo_host_fixture_unavailable")
    try:
        proof = await _command(host, bridge, HOST_AIM_FIXTURE, pawn, DEAD_BRIDGE, target, target, str(step))
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = True
            host.stopping = True
        raise
    if (proof["camera"] != scope["guid"] or proof["step_before"] != step or proof["step_after"] != step
            or proof["visibility"] is not None):
        raise RuntimeError("Headless camera placement did not verify the exact host and unchanged contract")
    return {"native_turret_photo": proof}


class HostCameraInput:
    def __init__(self):
        self.next_scope = 0.0
        self.latest = 0

    async def sync(self, host, bridge):
        events = bridge.native_host_camera_requests()
        try:
            pawn = _pawn(host, "host", DEAD_BRIDGE)
        except party.RuleError:
            return
        active = host.party.get("native_quests", {}).get(QUEST_ID, {}).get("step") in (1, 2)
        ready = host.last_game.get("native_host_camera_ready", {})
        guid = ready.get("guid")
        if not active or (ready.get("map"), ready.get("pawn")) != (DEAD_BRIDGE, pawn) or not valid_guid(guid):
            return
        for sequence, area, event_pawn, camera in events:
            if sequence <= self.latest or (area, event_pawn, camera) != (DEAD_BRIDGE, pawn, guid):
                continue
            self.latest = sequence
            operation = uuid.uuid5(bridge.input_namespace, f"{host.zone_epoch}:{pawn}:camera:{sequence}").hex
            try:
                result = await shutter(host, bridge, "host", guid, operation)
                host.control.event("native_host_camera_shutter", sequence=sequence, operation_id=operation,
                                   step=result["step"], guid=guid)
            except (OSError, RuntimeError, ValueError) as error:
                host.control.event("native_host_camera_shutter_rejected", sequence=sequence, operation_id=operation,
                                   reason=str(error))
            if host.stopping or host.party.get("native_quests", {}).get(QUEST_ID, {}).get("step") not in (1, 2):
                return
        now = time.monotonic()
        if now < self.next_scope:
            return
        self.next_scope = now + .65
        try:
            snapshot = await bridge.command("native-host-camera-scope", DEAD_BRIDGE, pawn, guid, QUEST_ID, timeout=2)
        except (OSError, RuntimeError) as error:
            host.control.event("native_host_camera_scope_rejected", pawn=pawn, reason=str(error))
            return
        proof = snapshot.get("native_host_camera_scope", {})
        if (proof.get("map"), proof.get("pawn"), proof.get("guid")) == (DEAD_BRIDGE, pawn, guid):
            host.last_game, host.last_seen = snapshot, time.monotonic()


async def advance(host, bridge, character, action, target=None, *, expected_camera=None, shutter_request=None):
    capturing = action in (CAPTURE, CAPTURE_FIXTURE)
    area = DEAD_BRIDGE if capturing else host.last_game.get("map")
    if not capturing and area not in BOARDS:
        raise party.RuleError("native_turret_photo_board_unavailable")
    pawn = _pawn(host, character, area)
    current = host.party.get("native_quests", {}).get(QUEST_ID, {})
    photos = current.get("photos", {})
    step = current.get("step")
    if capturing:
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
        target = BOARDS[area]
        if action == "native-turret-photo-turn-in" and area != BOROUGH:
            raise party.RuleError("stock_turret_photo_borough_board_required")
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
        if action == CAPTURE_FIXTURE:
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
        if capturing:
            if (not valid_guid(proof.get("camera")) or expected_camera is not None and proof.get("camera") != expected_camera
                    or proof.get("visibility") is None or not .75 <= proof["visibility"] <= 1):
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
        result = {"effect": "native_quest_changed", **record, "native_turret_photo": proof}
        if shutter_request:
            identifier, request = shutter_request
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": identifier, "request": request, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if action == CAPTURE_FIXTURE:
            revision = await host.checkpoint_native(bridge, candidate)
        else:
            host.pending_host_save = bool(host.pending_host_save)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_turret_photo_committed", action=ACTIONS[action], target=target, revision=revision, **record)
        return result
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
