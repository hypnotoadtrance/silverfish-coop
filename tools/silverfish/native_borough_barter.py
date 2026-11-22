"""Commit exact guest-owned Borough barter and Observatory workbench exchanges."""

import asyncio
import time

from . import native_dialogue, party
from .native_gold import valid_guid
from .travel import BOROUGH, OBSERVATORY


ACTION = "native-borough-mama-trade"
POST = "TradePost_BP_C " + native_dialogue.MAMA_LEVEL + "TradePost_BP_C_1080"
COPPER = "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"
AMMO = "/Game/Classes/Items/Ammo_Intr_AP_BP.Ammo_Intr_AP_BP_C"
AMMO_COUNT = 25
CRAFT_ACTION = "native-crafting-bench"
CRAFT_PREFLIGHT = "native-crafting-bench-preflight"
OFFER_TAG = "native-crafting-bench-offer"
OFFER_KEY = "native_crafting_bench_offer"
CRAFT_APPROACH = "native-crafting-bench-approach-fixture"
CRAFT_SCRAP_FIXTURE = "native-crafting-bench-scrap-fixture"
BENCH = "CraftingBench_BP_C " + native_dialogue.LEVEL + "StaticMeshActor_51"
BOARD = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
SCRAP = "/Game/Classes/Items/Item_ElectronicScrap.Item_ElectronicScrap_C"


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


def parse_crafting(fields: list[str]) -> dict:
    if len(fields) != 15:
        raise ValueError("Invalid native Crafting Bench report size")
    (tag, area, pawn, bench, state, payment, give_class, give_count, reward, receive_class,
     receive_count, wallet_before, wallet_after, reputation, armor_guid) = fields
    if ((tag, area, bench, state, give_class, give_count, receive_class, receive_count, reputation) != (
            CRAFT_ACTION, OBSERVATORY, BENCH, "exchanged", BOARD, "1", SCRAP, "1", "0")
            or not (pawn.startswith("Player_FP_C " + native_dialogue.LEVEL) and len(pawn) <= 512)
            or any(not valid_guid(guid) for guid in (payment, reward, armor_guid))
            or len({payment, reward, armor_guid}) != 3 or wallet_before != wallet_after):
        raise ValueError("Invalid native Crafting Bench identity or recipe")
    wallet = int(wallet_before)
    if not -(2**31) <= wallet < 2**31 or wallet_before != str(wallet):
        raise ValueError("Invalid native Crafting Bench wallet")
    return {"map": area, "pawn": pawn, "bench": bench, "state": state, "armor_guid": armor_guid,
            "wallet_before": wallet, "wallet_after": wallet, "reputation_change": 0,
            "give": {"guid": payment, "class_path": BOARD, "quantity": 1},
            "receive": {"guid": reward, "class_path": SCRAP, "quantity": 1}}


def parse_offer(fields: list[str]) -> dict:
    tag, area, pawn, bench, state, board, armor = fields
    if ((tag, area, bench) != (OFFER_TAG, OBSERVATORY, BENCH)
            or not pawn.startswith("Player_FP_C " + native_dialogue.LEVEL) or len(pawn) > 512
            or not valid_guid(armor) or armor == board
            or not (state == "offer" and valid_guid(board) or state == "missing" and board == "-")):
        raise ValueError("Invalid stock workbench offer")
    return dict(map=area, pawn=pawn, bench=bench, state=state, board_guid=None if board == "-" else board, armor_guid=armor)


async def preflight(host, bridge, character: str) -> dict:
    sessions = host.dialogue_sessions
    if not sessions or OBSERVATORY not in sessions.maps:
        raise party.RuleError("native_crafting_bench_unavailable")
    scope = sessions.member_scope(host, character)
    if scope[2] != OBSERVATORY:
        raise party.RuleError("native_crafting_bench_unavailable")
    try:
        current = await bridge.command(CRAFT_PREFLIGHT, scope[3], timeout=5)
    except RuntimeError as error:
        if str(error).startswith("borough_barter_refused_"):
            raise party.RuleError(str(error)) from error
        raise
    proof = current.get(OFFER_KEY, {})
    if (current.get("state"), current.get("map"), proof.get("pawn")) != ("hosting", OBSERVATORY, scope[3]):
        raise RuntimeError("The workbench did not verify the exact guest offer")
    host.last_game, host.last_seen = current, time.monotonic()
    return {OFFER_KEY: proof}


async def trade(host, bridge, character: str, payment: str, operation: str, *, action: str = ACTION) -> dict:
    crafting = action == CRAFT_ACTION
    area, proof_key = (OBSERVATORY, "native_crafting_bench") if crafting else (BOROUGH, "native_borough_barter")
    if not valid_guid(payment) or not native_dialogue.valid_token(operation):
        item = "computer_board" if crafting else "copper"
        raise party.RuleError(f"{proof_key}_requires_owned_{item}_guid")
    sessions = host.dialogue_sessions
    if not sessions or area not in sessions.maps:
        raise party.RuleError(f"{proof_key}_unavailable")
    scope = sessions.member_scope(host, character)
    if scope[2] != area:
        raise party.RuleError(f"{proof_key}_unavailable")
    fingerprint = [action, payment, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] != operation:
            continue
        if receipt["request"] != fingerprint:
            raise party.RuleError("operation_id_conflict")
        return receipt["result"]

    from .native_inventory import retain_guest_save

    defer_save = await retain_guest_save(host, bridge, character, scope[3])
    if not defer_save:
        await host.checkpoint_native(bridge)
    if sessions.member_scope(host, character) != scope:
        raise party.RuleError(f"{proof_key}_scope_changed")
    pawn = scope[3]
    host.native_checkpoint_unsafe = True
    try:
        try:
            current = await bridge.command(action, pawn, payment, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("borough_barter_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = current.get(proof_key, {})
        if ((current.get("state"), current.get("map"), proof.get("pawn"), proof.get("give", {}).get("guid")) !=
                ("hosting", area, pawn, payment)):
            raise RuntimeError(f"{proof_key} did not verify the exact guest and consumed item")
        host.last_game, host.last_seen = current, time.monotonic()
        proposed = party.load_party(host.party)
        proposed["players"][character].get("native_inventory", {}).pop(payment, None)
        result = {proof_key: proof}
        receipts = proposed["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = None
        if not defer_save:
            revision = await host.checkpoint_native(bridge, proposed, require_guest_frames=frozenset({character}))
        host.party = proposed
        host.native_pickup.forget_committed_row(character, payment)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.control.event(f"{proof_key}_committed", character_id=character, revision=revision, **proof)
    return result
