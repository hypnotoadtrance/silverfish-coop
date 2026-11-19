"""Commit one host-authoritative roll from the qualified stock CrossRoads crate."""

import asyncio
import time

from . import native_host_save, party
from .native_gold import valid_guid
from .travel import CROSSROADS


CRATE = f"LootCrate_Small_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.LootCrate_Small_BP_C_3"
STATE_KEY = "native_loot_rolls"


def public_roll(roll: dict) -> dict:
    return {key: value for key, value in roll.items() if key != "escrow"}


def _scope(host, character: str) -> str:
    if character == "host":
        return host.last_game.get("pawns", {}).get("local", {}).get("name", "")
    record = host.native_recipients().get(character, {})
    return record.get("pawn", "") if record.get("connected") else ""


async def perform(host, bridge, character: str, action: str, target: str, operation_id: str) -> dict:
    if (not host.native_zones or not host.fixtures or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != CROSSROADS
            or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_loot_scope_unavailable")
    target_valid = valid_guid(target) if action == "claim" else target == CRATE
    if not target_valid or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("native_loot_requires_exact_crate_and_operation")
    pawn = _scope(host, character)
    if not pawn or character not in host.party["players"]:
        raise party.RuleError("native_loot_opener_unavailable")
    saved = host.party.get(STATE_KEY, {}).get(CRATE)
    if saved and action in ("open", "inspect"):
        return {"native_loot_roll": public_roll(saved)}
    if host.party["players"][character]["downed"] or host.party["wipe"] or character in host.party["pending_spawns"]:
        raise party.RuleError("native_loot_opener_unavailable")

    arguments = ()
    fingerprint = ["native-loot-claim", target, None]
    if action == "claim":
        for entry in host.party["receipts"].get(character, []):
            if entry["id"] == operation_id:
                if entry["request"] != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                return entry["result"]
        item = next((row for row in saved.get("items", ()) if row.get("guid") == target), None) if saved else None
        if not item or not saved.get("escrow"):
            raise party.RuleError("native_loot_item_unavailable")
        if item.get("claimed_by"):
            raise party.RuleError("native_loot_already_claimed")
        escrow = saved["escrow"]
        if escrow.get("map") != CROSSROADS:
            raise RuntimeError("The crate escrow belongs to a different map")
        await asyncio.to_thread(native_host_save.verify_record, host.native_profile, escrow)
        arguments = (target, escrow["slot"])
    elif action == "open":
        arguments = (native_host_save.new_slot(),)
    mutates = action in ("open", "seed", "claim")
    if mutates:
        host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(f"native-loot-{action}", pawn, CRATE, *arguments, timeout=6)
        proof = snapshot.get("native_loot", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != CROSSROADS
                or (proof.get("action"), proof.get("map"), proof.get("pawn"), proof.get("crate"))
                != (action, CROSSROADS, pawn, CRATE)):
            raise RuntimeError("The native loot proof did not match the exact participant and stock crate")
        if not proof["ok"]:
            if mutates and proof["invoked"]:
                raise RuntimeError(proof["status"] or "mutation_unverified_native_loot")
            if mutates:
                host.native_checkpoint_unsafe = False
            raise party.RuleError(proof["status"] or "native_loot_refused")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if action == "open":
            items = proof["items"]
            if (not proof["invoked"] or not proof["looted"] or proof["trait_calls"] < 1
                    or proof["item_count"] != len(items) or len(items) > 2
                    or len({item["actor"] for item in items}) != len(items)
                    or any(not valid_guid(item.get("guid")) for item in items)
                    or len({item["guid"] for item in items}) != len(items)
                    or proof["escrow_slot"] != arguments[0]):
                raise RuntimeError("The exact stock crate roll did not verify")
            frame = await asyncio.to_thread(native_host_save.record, host.native_profile, arguments[0], CROSSROADS,
                                            flush=True)
            roll = {"id": operation_id, "map": CROSSROADS, "crate": CRATE, "opener": character,
                    "lucky_looter": proof["lucky_looter"], "trait_source": proof["trait_source"],
                    "trait_calls": proof["trait_calls"], "items": items, "escrow": frame}
            candidate = party.load_party(host.party)
            candidate.setdefault(STATE_KEY, {})[CRATE] = roll
            revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
            host.control.event("native_loot_roll_committed", revision=revision, **public_roll(roll))
            return {"native_loot_roll": public_roll(roll)}
        if action == "claim":
            if (proof["status"] not in ("claimed", "already_claimed") or not proof["invoked"]
                    or proof["escrow_slot"] != arguments[1]):
                raise RuntimeError("The exact stock crate item transfer did not verify")
            candidate = party.load_party(host.party)
            claimed = next(row for row in candidate[STATE_KEY][CRATE]["items"] if row["guid"] == target)
            claimed["claimed_by"] = character
            result = {"native_loot_claim": claimed}
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": operation_id, "request": fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
            revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
            host.control.event("native_loot_claim_committed", revision=revision, **claimed)
            return result
        if action == "seed":
            if not proof["lucky_looter"]:
                raise RuntimeError("The exact stock guest Lucky Looter fixture did not verify")
            await host.checkpoint_native(bridge)
            host.native_checkpoint_unsafe = False
        return {"native_loot": proof}
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
