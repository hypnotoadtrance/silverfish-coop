"""Verify and retire the exact stock actor birth for a qualified native drop."""

import asyncio
import re
import time

from . import native_guest_drop_record, native_host_save, native_inventory, party
from .native_gold import valid_guid
from .travel import OBSERVATORY


ACTION = "native-guest-stack-drop-probe"
BIRTH = "native-guest-stack-drop-birth"
KEY = "native_guest_stack_drop_probe"
CAPABILITY = KEY + "_capability"
SETTLE = "native-guest-stack-drop-settle"
SETTLE_KEY = "native_guest_stack_drop_settle"
SUCCESS = "stock_spawn_verified_and_retired"
REFUSAL = "native_guest_stack_drop_"
UNSAFE = "mutation_unverified_native_guest_stack_drop_"
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
    if (len(fields) not in (10, 13) or fields[0] not in (ACTION, BIRTH) or state != "hosting"
            or not native_guest_drop_record.supported_map(area) or (fields[0] == ACTION and area != OBSERVATORY)
            or fields[1] != area):
        raise ValueError("Invalid isolated stock stack drop report")
    map_name, pawn, slot, guid, raw_count, flag, status, actor, new_guid = fields[1:10]
    if (not native_guest_drop_record.pawn_matches(area, pawn) or not native_host_save.SLOT.fullmatch(slot) or not valid_guid(guid)
            or not re.fullmatch(r"[1-9][0-9]{0,2}", raw_count) or int(raw_count) > 200
            or flag not in ("0", "1") or not _STATUS.fullmatch(status)):
        raise ValueError("Invalid stock stack drop identity or quantity")
    ok = flag == "1"
    item = native_guest_drop_record.actor_class_path(area, actor) if ok else None
    if ok and (status != SUCCESS or not native_guest_drop_record.valid_quantity(item, int(raw_count))
               or not valid_guid(new_guid) or new_guid == guid):
        raise ValueError("The stock actor or stock-converted GUID was not verified")
    if not ok and (not status.startswith((REFUSAL, UNSAFE)) or (actor, new_guid) != ("-", "-")):
        raise ValueError("Invalid isolated stock stack drop refusal")
    proof = dict(map=map_name, pawn=pawn, slot=slot, guid=guid, quantity=int(raw_count), ok=ok, status=status,
                 actor=actor if ok else None, new_guid=new_guid if ok else None)
    if ok and fields[0] == BIRTH:
        proof["class_path"] = item
    if len(fields) == 13:
        location = list(map(float, fields[10:]))
        if not ok or not native_guest_drop_record.valid_position(location):
            raise ValueError("Invalid exact stack drop placement")
        proof["location"] = location
    return proof


def parse_settlement(fields: list[str], state: str, area: str) -> dict:
    if len(fields) != 10 or state != "hosting" or not native_guest_drop_record.supported_map(area) or fields[1] != area:
        raise ValueError("Invalid isolated stack drop settlement")
    _, operation, pawn, drop_id, source, replacement, flag, status, count = fields[1:]
    if (operation not in ("debit", "reclaim") or not native_guest_drop_record.pawn_matches(area, pawn) or not native_guest_drop_record.valid_id(drop_id)
            or not native_host_save.SLOT.fullmatch(source) or source == replacement
            or (operation == "debit" and not native_host_save.SLOT.fullmatch(replacement))
            or (operation == "reclaim" and replacement != "-") or flag not in ("0", "1") or count != flag
            or not _STATUS.fullmatch(status)):
        raise ValueError("Invalid isolated stock stack settlement identity")
    return dict(map=area, operation=operation, pawn=pawn, drop_id=drop_id, source=source,
                replacement=replacement if operation == "debit" else None, ok=flag == "1", status=status, count=int(count))


async def perform(host, bridge, character: str, guid: str, count: int, *, fixture: bool = True) -> dict:
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
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(ACTION if fixture else BIRTH, *arguments, timeout=6)
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
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not proof["ok"]:
            reason = proof.get("status")
            if isinstance(reason, str) and reason.startswith(REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(reason)
            raise RuntimeError(reason or "The stock stack drop actor retirement was unverified")
        actor = proof.get("actor")
        item = native_guest_drop_record.actor_class_path(area, actor)
        if (proof.get("status") != SUCCESS or not native_guest_drop_record.valid_quantity(item, count)
                or not fixture and item != proof.get("class_path")
                or not valid_guid(proof.get("new_guid")) or proof["new_guid"] == guid):
            raise RuntimeError("The stock stack drop actor and typed conversion did not verify")
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, frame)
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise

    host.native_checkpoint_unsafe = False
    result = {**proof, "owner": character, "revision": revision}
    host.control.event("native_guest_stack_drop_probed", **result)
    return {KEY: result}
