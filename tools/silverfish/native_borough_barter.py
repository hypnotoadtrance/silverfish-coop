"""Exchange one exact guest-owned Copper Wire for Mama's stock AP ammunition."""

import asyncio
import time

from . import native_dialogue, party
from .native_gold import valid_guid
from .travel import BOROUGH


ACTION = "native-borough-mama-trade"
POST = "TradePost_BP_C " + native_dialogue.MAMA_LEVEL + "TradePost_BP_C_1080"
COPPER = "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"
AMMO = "/Game/Classes/Items/Ammo_Intr_AP_BP.Ammo_Intr_AP_BP_C"
AMMO_COUNT = 25


def parse(fields: list[str]) -> dict:
    if len(fields) != 14:
        raise ValueError("Invalid Native Borough barter report size")
    (tag, area, pawn, post, state, payment, give_class, give_count, reward, receive_class,
     receive_count, wallet_before, wallet_after, reputation) = fields
    if ((tag, area, post, state, give_class, give_count, receive_class, receive_count, reputation) != (
            "native-borough-barter", BOROUGH, POST, "exchanged", COPPER, "1", AMMO, str(AMMO_COUNT), "0")
            or not (pawn.startswith("Player_FP_C " + native_dialogue.MAMA_LEVEL) and len(pawn) <= 512)
            or not valid_guid(payment) or not valid_guid(reward) or payment == reward or wallet_before != wallet_after):
        raise ValueError("Invalid Native Borough barter identity or trade")
    wallet = int(wallet_before)
    if not -(2**31) <= wallet < 2**31 or wallet_before != str(wallet):
        raise ValueError("Invalid Native Borough barter wallet")
    return {"map": area, "pawn": pawn, "post": post, "state": state, "wallet_before": wallet, "wallet_after": wallet,
            "reputation_change": 0, "give": {"guid": payment, "class_path": COPPER, "quantity": 1},
            "receive": {"guid": reward, "class_path": AMMO, "quantity": AMMO_COUNT}}


async def trade(host, bridge, character: str, payment: str, operation: str) -> dict:
    if not valid_guid(payment) or not native_dialogue.valid_token(operation):
        raise party.RuleError("native_borough_barter_requires_owned_copper_guid")
    sessions = host.dialogue_sessions
    if not sessions or BOROUGH not in sessions.maps:
        raise party.RuleError("native_borough_barter_unavailable")
    scope = sessions.member_scope(host, character)
    if scope[2] != BOROUGH:
        raise party.RuleError("native_borough_barter_unavailable")
    fingerprint = [ACTION, payment, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] != operation:
            continue
        if receipt["request"] != fingerprint:
            raise party.RuleError("operation_id_conflict")
        return receipt["result"]

    await host.checkpoint_native(bridge)
    if sessions.member_scope(host, character) != scope:
        raise party.RuleError("native_borough_barter_scope_changed")
    pawn = scope[3]
    host.native_checkpoint_unsafe = True
    try:
        try:
            current = await bridge.command(ACTION, pawn, payment, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("borough_barter_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = current.get("native_borough_barter", {})
        if ((current.get("state"), current.get("map"), proof.get("pawn"), proof.get("give", {}).get("guid")) !=
                ("hosting", BOROUGH, pawn, payment)):
            raise RuntimeError("The Borough barter did not verify the exact guest and Copper Wire")
        host.last_game, host.last_seen = current, time.monotonic()
        proposed = party.load_party(host.party)
        proposed["players"][character].get("native_inventory", {}).pop(payment, None)
        result = {"native_borough_barter": proof}
        receipts = proposed["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, proposed, require_guest_frames=frozenset({character}))
        host.party = proposed
        host.native_pickup.forget_committed_row(character, payment)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.control.event("native_borough_barter_committed", character_id=character, revision=revision, **proof)
    return result
