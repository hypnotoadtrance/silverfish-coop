"""Run the isolated stock 9mm spawn and retirement canary in Observatory."""

import asyncio
import re
import time

from . import native_host_save, native_inventory, party
from .native_gold import valid_guid
from .travel import OBSERVATORY


ACTION = "native-guest-stack-drop-probe"
KEY = "native_guest_stack_drop_probe"
CAPABILITY = KEY + "_capability"
SUCCESS = "stock_spawn_verified_and_retired"
REFUSAL = "native_guest_stack_drop_"
UNSAFE = "mutation_unverified_native_guest_stack_drop_"
_LEVEL = OBSERVATORY + ".Observitory_P:PersistentLevel."
_PAWN = re.compile(r"Player_FP_C " + re.escape(_LEVEL) + r"Player_FP_C_[0-9]{1,10}\Z")
_ACTOR = re.compile(r"Ammo_Pistol_BP_C " + re.escape(_LEVEL) + r"Ammo_Pistol_BP_C_[0-9]{1,10}\Z")
_STATUS = re.compile(r"[a-z0-9_]{1,100}\Z")


def parse(fields: list[str], state: str, area: str) -> dict:
    if (len(fields) != 10 or fields[0] != ACTION or state != "hosting" or area != OBSERVATORY
            or fields[1] != area):
        raise ValueError("Invalid isolated stock stack drop report")
    map_name, pawn, slot, guid, raw_count, flag, status, actor, new_guid = fields[1:]
    if (not _PAWN.fullmatch(pawn) or not native_host_save.SLOT.fullmatch(slot) or not valid_guid(guid)
            or not re.fullmatch(r"[1-9][0-9]{0,2}", raw_count) or int(raw_count) > 200
            or flag not in ("0", "1") or not _STATUS.fullmatch(status)):
        raise ValueError("Invalid stock stack drop identity or quantity")
    ok = flag == "1"
    if ok and (status != SUCCESS or not _ACTOR.fullmatch(actor) or not valid_guid(new_guid) or new_guid == guid):
        raise ValueError("The stock actor or stock-converted GUID was not verified")
    if not ok and (not status.startswith((REFUSAL, UNSAFE)) or (actor, new_guid) != ("-", "-")):
        raise ValueError("Invalid isolated stock stack drop refusal")
    return dict(map=map_name, pawn=pawn, slot=slot, guid=guid, quantity=int(raw_count), ok=ok, status=status,
                actor=actor if ok else None, new_guid=new_guid if ok else None)


async def perform(host, bridge, character: str, guid: str, count: int) -> dict:
    if not valid_guid(guid) or type(count) is not int or not 1 <= count <= 200:
        raise party.RuleError("native_guest_stack_drop_exact_guid_and_quantity_required")
    game = host.last_game
    if (not host.fixtures or not host.native_guest or not host.native_zones or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or (game.get("state"), game.get("map"), game.get(CAPABILITY)) != ("hosting", OBSERVATORY, 1)
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_stack_drop_fixture_unavailable")
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn")
    player = host.party["players"].get(character)
    if (not owner.get("connected") or not isinstance(pawn, str) or not _PAWN.fullmatch(pawn) or not player
            or player["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]):
        raise party.RuleError("native_guest_stack_drop_exact_standing_guest_required")

    revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({character}))
    campaign = host.store.status()
    saved = native_inventory.saved_frame(host, campaign, character)
    if not isinstance(saved, dict) or saved.get("map") != OBSERVATORY or campaign.get("revision") != revision:
        raise RuntimeError("The isolated stack drop requires the guest's fresh Observatory frame")
    frame = await asyncio.to_thread(native_host_save.guest_record, host.native_profile, saved, character)
    slot = frame["slot"]
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(ACTION, pawn, slot, guid, str(count), timeout=6)
        except RuntimeError as error:
            if str(error).startswith(REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(KEY, {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("slot"),
             proof.get("guid"), proof.get("quantity")) != ("hosting", OBSERVATORY, OBSERVATORY, pawn, slot, guid, count)
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
        if (proof.get("status") != SUCCESS or not isinstance(actor, str) or not _ACTOR.fullmatch(actor)
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
