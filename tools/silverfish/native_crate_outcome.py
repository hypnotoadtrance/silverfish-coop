"""Adopt a player's stock container result without running another roll."""

import json
import time
from hashlib import sha256

from . import native_host_save, native_inventory_grant, native_loot, party
from .authored_loot import valid_item_transform
from .content_paths import item_class_name


LOCAL_ROLL = "native-loot-roll-local"
ADOPT = "native-loot-adopt-outcome"
REPORT = "native-loot-report-outcome"
REPLY = "native-loot-outcome-reply"
MAX_REPORT_BYTES = 24 * 1024
TRAITS = {"temporary", "permanent", "backpack", "armor", "helmet", "equipment"}


class Refused(RuntimeError):
    """The local bridge returned an explicit refusal for the exact command."""


def supported(crate, area):
    return (isinstance(crate, str) and isinstance(area, str) and native_loot.CRATES.get(crate) == area
            and not native_loot.lockable_container(crate, area) and not native_loot.random_lid(crate)
            and not crate.startswith("ElectronicLocker_01_BP_C "))


def report_body(epoch, crate, outcome):
    return json.dumps({"epoch": epoch, "crate": crate, **outcome}, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def decode_report(target, area):
    if not isinstance(target, str) or len(target.encode("utf-8")) > MAX_REPORT_BYTES:
        raise party.RuleError("native_loot_outcome_size")
    try:
        report = json.loads(target)
        if not isinstance(report, dict) or report.keys() != {
            "epoch", "crate", "lucky_looter", "trait_source", "trait_calls", "items", "typed",
        }:
            raise ValueError("Unexpected stock crate result")
        crate, epoch = report["crate"], report["epoch"]
        if not supported(crate, area) or not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
            raise ValueError("Unqualified stock crate result")
        lucky, source, calls = report["lucky_looter"], report["trait_source"], report["trait_calls"]
        if (type(lucky) is not bool or not isinstance(source, str) or (source not in TRAITS if lucky else source != "none")
                or not native_loot.valid_loot_trait_calls(crate, calls)):
            raise ValueError("Invalid trait results")
        items, typed = report["items"], report["typed"]
        if not isinstance(items, list) or not isinstance(typed, list) or len(items) != len(typed) or len(items) > native_loot.loot_capacity(crate):
            raise ValueError("Invalid item count")
        ids, actors = set(), set()
        for item, text in zip(items, typed, strict=True):
            if not isinstance(item, dict) or item.keys() != {"actor", "guid", "class_path", "ammo", "transform"}:
                raise ValueError("Invalid stock item result")
            actor, guid, item_class, ammo, transform = (item[key] for key in ("actor", "guid", "class_path", "ammo", "transform"))
            if (not native_loot.valid_guid(guid) or guid in ids or not item_class_name(item_class, 256)
                    or not isinstance(actor, str) or len(actor) > 512 or actor in actors
                    or not actor.startswith(item_class.rsplit(".", 1)[1] + " " + area + ".")
                    or type(ammo) is not int or not -1 <= ammo < 2**31 or not valid_item_transform(transform)
                    or not isinstance(text, str) or not text.startswith("(")):
                raise ValueError("Unqualified stock item result")
            native_inventory_grant.parse_item(f"{guid}@{item_class}@{text}")
            ids.add(guid)
            actors.add(actor)
        return report
    except (ValueError, TypeError, RecursionError, OverflowError) as error:
        raise party.RuleError("native_loot_outcome_invalid") from error


def observed_roll(crate, area, character, operation, outcome):
    return {"id": operation, "map": area, "crate": crate, "opener": character,
            **{key: outcome[key] for key in ("lucky_looter", "trait_source", "trait_calls", "items")}}


async def accept(host, bridge, character, target, operation):
    area = host.last_game.get("map")
    outcome = decode_report(target, area)
    crate = outcome["crate"]
    pawn = native_loot._scope(host, character)
    if (outcome["epoch"] != host.zone_epoch or character == "host" or not pawn or not host.native_loot_input
            or bridge.local_bridge is None or not host.native_profile or host.last_game.get("state") != "hosting"
            or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation) or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party["wipe"] or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed") is not False):
        raise party.RuleError("native_loot_outcome_scope_unavailable")
    fingerprint = [REPORT, crate, sha256(target.encode("utf-8")).hexdigest()]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    rolls = host.party.get(native_loot.STATE_KEY, {})
    if operation in host.party.get(native_loot.RETIRED, {}):
        raise party.RuleError("native_loot_roll_replenished")
    if any(roll["id"] == operation and key != crate for key, roll in rolls.items()):
        raise party.RuleError("operation_id_conflict")
    if existing := rolls.get(crate):
        if crate in native_loot._replenished(host.last_game, rolls):
            raise party.RuleError("native_loot_replenishment_pending")
        return {"native_loot_roll": native_loot.public_roll(existing)}
    slot = native_host_save.new_slot()
    arguments = tuple(value for item, text in zip(outcome["items"], outcome["typed"], strict=True)
                      for value in (item["guid"], item["class_path"], text))
    host.native_checkpoint_unsafe = True
    try:
        try:
            proof = await bridge.command(ADOPT, pawn, crate, slot, *arguments, timeout=2, update_status=False)
        except TimeoutError:
            proof = await bridge.command(ADOPT, pawn, crate, slot, *arguments, timeout=2, update_status=False)
    except Refused as error:
        if not str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = False
        raise party.RuleError(str(error)) from error
    if (proof.get("map"), proof.get("state"), proof.get("crate"), proof.get("scope"), proof.get("message")) != (
            area, "hosting", crate, slot, "adopted"):
        raise RuntimeError("The stock crate outcome adoption did not verify")
    roll = observed_roll(crate, area, character, operation, outcome)
    candidate = native_loot.copy_pending_loot(host.party, native_loot.STATE_KEY, character=character)
    candidate.setdefault(native_loot.STATE_KEY, {})[crate] = {**roll, "escrow": {"slot": slot, "map": area, "pending": True}}
    result = {"native_loot_roll": roll}
    receipts = candidate["receipts"][character]
    receipts.append({"id": operation, "request": fingerprint, "result": result})
    del receipts[:-party.RECENT_ACTIONS]
    host.party = candidate
    host.last_seen = time.monotonic()
    host.native_checkpoint_unsafe = False
    host.control.event("native_loot_roll_accepted", **roll, origin="player")
    return result
