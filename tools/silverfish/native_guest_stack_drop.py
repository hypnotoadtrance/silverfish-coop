"""Verify and retire the exact stock actor birth for a qualified native drop."""

import asyncio
import re
import time

from . import native_guest_drop_record, native_host_save, native_inventory, native_loot, party
from .local_bridge import LocalCommandNotSubmitted
from .native_gold import valid_guid
from .travel import OBSERVATORY


ACTION = "native-guest-stack-drop-probe"
BIRTH = "native-guest-stack-drop-birth"
KEY = "native_guest_stack_drop_probe"
CAPABILITY = KEY + "_capability"
MEMORY_CAPABILITY = "native_guest_stack_drop_memory"
SETTLE = "native-guest-stack-drop-settle"
SETTLE_KEY = "native_guest_stack_drop_settle"
REPLY = "native-guest-stack-drop-reply"
REPLY_BYTES = "native_guest_stack_drop_reply_bytes"
REPLY_RECOVERED = "native_guest_stack_drop_reply_recovered"
SUCCESS = "stock_spawn_verified_and_retired"
ORIGIN_DEBITED = "stock_drop_origin_debited"
REFUSAL = "native_guest_stack_drop_"
UNSAFE = "mutation_unverified_native_guest_stack_drop_"
_COMPACT_REPLY_WAIT = 1.0
_STATUS = re.compile(r"[a-z0-9_]{1,100}\Z")


def standing_pawn(host, character: str) -> str:
    game = host.last_game
    if character == "host":
        local = game.get("pawns", {}).get("local", {})
        health, pawn = local.get("health"), local.get("name")
        eligible = type(health) in (int, float) and 0 < health < float("inf")
        refusal = "native_guest_stack_drop_exact_standing_host_required"
    else:
        guest = host.native_recipients().get(character, {})
        pawn, eligible = guest.get("pawn"), guest.get("connected")
        refusal = "native_guest_stack_drop_exact_standing_guest_required"
    player = host.party["players"].get(character)
    if (not eligible or not native_guest_drop_record.pawn_matches(game.get("map"), pawn)
            or not player or player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]):
        raise party.RuleError(refusal)
    return pawn


async def verify_owner_frame(profile, saved: dict, character: str) -> dict:
    if character == "host":
        if saved.get("character_id") is not None:
            raise RuntimeError("The drop requires the canonical host inventory frame")
        await asyncio.to_thread(native_host_save.verify_record, profile, saved)
        return saved
    return await asyncio.to_thread(native_host_save.guest_record, profile, saved, character)


def parse(fields: list[str], state: str, area: str) -> dict:
    if fields[:1] == [SETTLE]:
        return parse_settlement(fields, state, area)
    if (len(fields) not in (10, 13, 14) or fields[0] not in (ACTION, BIRTH) or state != "hosting"
            or not native_guest_drop_record.supported_map(area) or (fields[0] == ACTION and area != OBSERVATORY)
            or fields[1] != area):
        raise ValueError("Invalid isolated stock stack drop report")
    map_name, pawn, slot, guid, raw_count, flag, status, actor, new_guid = fields[1:10]
    if (not native_guest_drop_record.pawn_matches(area, pawn) or not native_host_save.SLOT.fullmatch(slot) or not valid_guid(guid)
            or not re.fullmatch(r"[1-9][0-9]{0,2}", raw_count) or int(raw_count) > 200
            or flag not in ("0", "1") or not _STATUS.fullmatch(status)):
        raise ValueError("Invalid stock stack drop identity or quantity")
    ok = flag == "1"
    adopted = status == ORIGIN_DEBITED
    if adopted and (not ok or fields[0] != BIRTH or len(fields) != 14 or (actor, new_guid) != ("-", "-")):
        raise ValueError("The reported stock drop origin was not verified")
    item = (fields[13] if len(fields) == 14 and (adopted or native_guest_drop_record.actor_matches(area, actor, fields[13]))
            else native_guest_drop_record.actor_class_path(area, actor) if len(fields) != 14 else None)
    if ok and (not native_guest_drop_record.valid_quantity(item, int(raw_count))
               or not adopted and (status != SUCCESS or not valid_guid(new_guid) or new_guid == guid)):
        raise ValueError("The stock actor or stock-converted GUID was not verified")
    if not ok and (not status.startswith((REFUSAL, UNSAFE)) or (actor, new_guid) != ("-", "-")):
        raise ValueError("Invalid isolated stock stack drop refusal")
    proof = dict(map=map_name, pawn=pawn, slot=slot, guid=guid, quantity=int(raw_count), ok=ok, status=status,
                 actor=actor if ok and not adopted else None, new_guid=new_guid if ok and not adopted else None)
    if ok and (fields[0] == BIRTH or len(fields) == 14):
        proof["class_path"] = item
    if len(fields) >= 13:
        location = list(map(float, fields[10:13]))
        if not ok or not native_guest_drop_record.valid_position(location):
            raise ValueError("Invalid exact stack drop placement")
        proof["location"] = location
    return proof


def parse_settlement(fields: list[str], state: str, area: str) -> dict:
    if len(fields) not in (10, 15) or state != "hosting" or not native_guest_drop_record.supported_map(area) or fields[1] != area:
        raise ValueError("Invalid isolated stack drop settlement")
    _, operation, pawn, drop_id, source, replacement, flag, status, count = fields[1:10]
    if (operation not in ("debit", "reclaim") or not native_guest_drop_record.pawn_matches(area, pawn) or not native_guest_drop_record.valid_id(drop_id)
            or not native_host_save.SLOT.fullmatch(source) or source == replacement
            or (operation == "debit" and not native_host_save.SLOT.fullmatch(replacement))
            or (operation == "reclaim" and replacement != "-") or flag not in ("0", "1") or count != flag
            or not _STATUS.fullmatch(status)):
        raise ValueError("Invalid isolated stock stack settlement identity")
    proof = dict(map=area, operation=operation, pawn=pawn, drop_id=drop_id, source=source,
                 replacement=replacement if operation == "debit" else None, ok=flag == "1", status=status, count=int(count))
    if status == "merged":
        if len(fields) != 15 or operation != "reclaim" or not proof["ok"]:
            raise ValueError("Invalid isolated dropped stack merge settlement")
        proof["merge"] = native_loot.parse_stack_merge(fields[10:])
    elif len(fields) == 15:
        raise ValueError("Unexpected isolated dropped stack merge settlement")
    return proof


def parse_memory_reply(raw, request):
    rows = raw.splitlines() if len(raw) <= 4096 and raw.endswith("\n") else []
    action = request[1]
    birth = action == BIRTH
    readback = request[-1] == "readback"
    if not rows or len(request) - readback not in ((8, 11) if birth else (15,)):
        raise RuntimeError("The local stock drop receipt was malformed")
    area = request[6 if birth else 10]
    header = rows[0].split("\t")
    if len(header) != 8 or header[:6] != [REPLY, "1", request[0], action, "hosting", area]:
        raise RuntimeError("The local stock drop receipt did not match the request")
    if header[6] == "error" and len(rows) == 1:
        raise RuntimeError(header[7] or "The game declined the stock drop command")
    if header[6] != "accepted" or len(rows) != 2:
        raise RuntimeError("The local stock drop receipt was incomplete")
    row = rows[1].split("\t")
    identity_end = 6 if birth else 7
    if row[0] != action or row[2:identity_end] != list(request[2:identity_end]):
        raise RuntimeError("The local stock drop outcome belongs to another transaction")
    try:
        proof = parse(row, "hosting", area)
    except ValueError as error:
        raise RuntimeError("The local stock drop result did not verify") from error
    result = {"state": "hosting", "map": area, KEY if birth else SETTLE_KEY: proof,
              REPLY_BYTES: len(raw.encode("utf-8"))}
    if readback:
        result[REPLY_RECOVERED] = True
    return result


async def memory_command(bridge, action, *arguments, timeout):
    try:
        return await bridge.command(action, *arguments, timeout=_COMPACT_REPLY_WAIT, update_status=False)
    except TimeoutError:
        try:
            # The original command may have committed. Ask the game for its
            # recorded result without running the stock mutation again.
            return await bridge.command(action, *arguments, "readback", timeout=timeout, update_status=False)
        except LocalCommandNotSubmitted as error:
            raise RuntimeError("The game could not verify the timed-out drop command") from error


async def perform(host, bridge, character: str, guid: str, count: int, *, fixture: bool = True,
                  memory_slot: str | None = None, origin: list[float] | None = None) -> dict:
    if not valid_guid(guid) or type(count) is not int or not 1 <= count <= 200:
        raise party.RuleError("native_guest_stack_drop_exact_guid_and_quantity_required")
    game = host.last_game
    area, epoch = game.get("map"), host.zone_epoch
    capability = CAPABILITY if fixture else native_guest_drop_record.CAPABILITY
    if ((fixture and (not host.fixtures or area != OBSERVATORY or character == "host"))
            or not host.native_guest or not host.native_zones
            or host.native_profile is None or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or not native_guest_drop_record.supported_map(area) or (game.get("state"), game.get(capability)) != ("hosting", 1)
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_stack_drop_fixture_unavailable")
    pawn = standing_pawn(host, character)

    if origin is not None and (memory_slot is None or character == "host"
                               or not native_guest_drop_record.valid_position(origin)):
        raise party.RuleError("native_guest_stack_drop_reported_origin_unavailable")
    if memory_slot is not None:
        if fixture or game.get(MEMORY_CAPABILITY) is not True or not native_host_save.SLOT.fullmatch(memory_slot):
            raise party.RuleError("native_guest_stack_drop_memory_unavailable")
        frame, revision, slot = None, None, memory_slot
    else:
        revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({character} - {"host"}))
        campaign = host.store.status()
        saved = native_inventory.saved_frame(host, campaign, character)
        if not isinstance(saved, dict) or saved.get("map") != area or campaign.get("revision") != revision:
            raise RuntimeError("The isolated stack drop requires the dropper's fresh source-map frame")
        frame = await verify_owner_frame(host.native_profile, saved, character)
        slot = frame["slot"]
    if (host.transitioning or host.last_game.get("map") != area or host.zone_epoch != epoch
            or standing_pawn(host, character) != pawn):
        raise party.RuleError("native_guest_stack_drop_scope_changed")
    arguments = (pawn, slot, guid, str(count)) if fixture else (pawn, slot, guid, str(count), area)
    if memory_slot is not None:
        arguments += ("memory",)
        if origin is not None:
            arguments += tuple(map(str, origin))
    host.native_checkpoint_unsafe = True
    try:
        try:
            compact = memory_slot is not None and getattr(bridge, "local_bridge", None) is not None
            command = ACTION if fixture else BIRTH
            if compact:
                snapshot = await memory_command(bridge, command, *arguments, timeout=6)
            else:
                snapshot = await bridge.command(command, *arguments, timeout=6)
        except LocalCommandNotSubmitted as error:
            host.native_checkpoint_unsafe = False
            raise party.RuleError("native_guest_stack_drop_bridge_busy") from error
        except RuntimeError as error:
            if str(error).startswith(REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(KEY, {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot"),
             proof.get("guid"), proof.get("quantity")) != ("hosting", area, area, pawn, slot, guid, count)
                or type(proof.get("ok")) is not bool):
            raise RuntimeError("The stock stack drop returned a mismatched actor or frozen frame")
        if REPLY_BYTES not in snapshot:
            host.last_game = snapshot
        host.last_seen = time.monotonic()
        if not proof["ok"]:
            reason = proof.get("status")
            if isinstance(reason, str) and reason.startswith(REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(reason)
            raise RuntimeError(reason or "The stock stack drop actor retirement was unverified")
        actor = proof.get("actor")
        item = proof.get("class_path") or (native_guest_drop_record.actor_class_path(area, actor) if fixture else None)
        if origin is not None:
            confirmed = (proof.get("status") == ORIGIN_DEBITED and proof.get("location") == origin
                         and actor is None and proof.get("new_guid") is None)
        else:
            confirmed = (proof.get("status") == SUCCESS and native_guest_drop_record.actor_matches(area, actor, item)
                         and valid_guid(proof.get("new_guid")) and proof["new_guid"] != guid
                         and (memory_slot is None or native_guest_drop_record.valid_position(proof.get("location"))))
        if not confirmed or not native_guest_drop_record.valid_quantity(item, count):
            raise RuntimeError("The stock stack drop actor and typed conversion did not verify")
        if frame:
            await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise

    host.native_checkpoint_unsafe = False
    result = {**proof, "owner": character, "revision": revision}
    if REPLY_BYTES in snapshot:
        result["bridge_reply_bytes"] = snapshot[REPLY_BYTES]
    if snapshot.get(REPLY_RECOVERED):
        result["bridge_reply_recovered"] = True
    host.control.event("native_guest_stack_drop_probed", **result)
    return {KEY: result}
