"""Pair an immutable, isolated Unreal save generation with a co-op checkpoint."""

from hashlib import file_digest
import math
import os
from pathlib import Path
import re
import uuid

from .campaign import CampaignFormatError
from .native_gold import valid_guid
from .native_tape_profiles import BY_MAP as TAPE_PROFILES, STONE
from .travel import BLACK_MARKET, BOROUGH, DEAD_BRIDGE, SCIENTIST_SHOP


SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
STOCK_START_MODES = ("Mercy", "Respawn_WtPenalty", "Decimation")
CQC_DROP_SOURCE = f"NPC_State_Infantry_CQC_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"
CQC_DROP_SOURCES = (CQC_DROP_SOURCE, CQC_DROP_SOURCE.removesuffix("_0") + "_1")
CQC_SHELL_ACTOR = f"Ammo_ShotgunShells_BP_C {DEAD_BRIDGE}.DeadBridge_P:PersistentLevel.Ammo_ShotgunShells_BP_C_"


def cqc_retirement_record(roll, slot):
    return {"schema": 1, "epoch": roll["epoch"], "bandit": roll["bandit"], "actor": roll["actor"],
            "guid": roll["guid"], "slot": slot}


def cqc_retired(roll):
    proof = roll.get("retirement")
    slot = proof.get("slot") if type(proof) is dict else None
    if (roll.get("map") != DEAD_BRIDGE or roll.get("bandit") not in CQC_DROP_SOURCES
            or not isinstance(slot, str) or not SLOT.fullmatch(slot) or type(proof.get("schema")) is not int
            or any(key not in roll for key in ("epoch", "actor", "guid"))):
        return False
    frame = roll.get("escrow")
    return (proof == cqc_retirement_record(roll, slot)
            and (roll.get("claimed_by") is not None or (isinstance(frame, dict) and frame.get("slot") == slot)))


def new_slot() -> str:
    return "SFCoop_" + uuid.uuid4().hex


def directory(profile: Path) -> Path:
    root = profile.resolve()
    saves = (root / "Saved" / "SaveGames").resolve()
    if not saves.is_relative_to(root):
        raise CampaignFormatError("The staged native save directory escapes its game profile")
    return saves


def slot_path(profile: Path, slot: str) -> Path:
    if not isinstance(slot, str) or not SLOT.fullmatch(slot):
        raise CampaignFormatError("Invalid co-op Unreal save slot")
    saves = directory(profile)
    path = saves / f"{slot}.sav"
    if path.resolve() != path:
        raise CampaignFormatError("The co-op Unreal save slot points outside its generation path")
    return path


def record(profile: Path, slot: str, map_name: str, *, flush: bool = False) -> dict:
    path = slot_path(profile, slot)
    try:
        with path.open("r+b" if flush else "rb") as stream:
            if flush:
                os.fsync(stream.fileno())
            size = os.fstat(stream.fileno()).st_size
            if size < 16 or stream.read(4) != b"GVAS":
                raise CampaignFormatError(f"The Unreal save generation {slot} has an invalid header")
            stream.seek(0)
            digest = file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise CampaignFormatError(f"Cannot read the isolated Unreal save generation {slot}") from error
    return {"slot": slot, "map": map_name, "size": size, "sha256": digest}


def verify(profile: Path, world: dict) -> dict | None:
    saved = world.get("native_host_save")
    if saved is None:
        return None
    verify_record(profile, saved)
    if saved["map"] != world.get("map"):
        raise CampaignFormatError("The host save generation is for a different campaign map")
    return saved


def verify_record(profile: Path, saved: dict) -> None:
    core_keys = {"slot", "map", "size", "sha256"}
    if (type(saved) is not dict or set(saved) - {"eather_remainder"} != core_keys
            or not isinstance(saved["map"], str) or not saved["map"].startswith("/Game/Classes/Maps/")
            or type(saved["size"]) is not int or not isinstance(saved["sha256"], str)
            or not SHA256.fullmatch(saved["sha256"])):
        raise CampaignFormatError("The campaign's Unreal save reference is invalid")
    if "eather_remainder" in saved:
        carry = saved["eather_remainder"]
        if type(carry) not in (int, float) or not 0 <= carry < 1:
            raise CampaignFormatError("The campaign's stock Eather carry is invalid")
    if record(profile, saved["slot"], saved["map"]) != {key: saved[key] for key in core_keys}:
        raise CampaignFormatError("The Unreal save generation does not match its campaign checkpoint")


def guest_record(profile: Path, saved: dict, character_id: str) -> dict:
    if type(saved) is not dict or saved.get("character_id") != character_id:
        raise CampaignFormatError("The Unreal save generation does not belong to this character")
    frame = {key: value for key, value in saved.items() if key != "character_id"}
    verify_record(profile, frame)
    return frame


def references(checkpoint: dict) -> list[dict]:
    world = checkpoint["world"]
    host = [world["native_host_save"]] if world.get("native_host_save") else []
    guest = [record["state"]["native_guest_save"] for record in checkpoint["characters"].values()
             if record["state"].get("native_guest_save")]
    return host + guest + _escrow_frames(checkpoint) + list(_wipe_frames(world).values())


def _wipe_frames(world: dict) -> dict:
    party = world.get("party") or {}
    draft = (party.get("wipe") or {}).get("native")
    return {} if draft is None else draft["frames"]


def _escrow_frames(checkpoint: dict) -> list[dict]:
    party = checkpoint["world"].get("party") or {}
    if type(party) is not dict:
        raise CampaignFormatError("The campaign's native party records are invalid")
    rolls = party.get("native_loot_rolls", {})
    if type(rolls) is not dict or any(type(roll) is not dict for roll in rolls.values()):
        raise CampaignFormatError("The campaign's native loot records are invalid")
    enemy_rolls = party.get("native_enemy_loot_rolls", {})
    if type(enemy_rolls) is not dict:
        raise CampaignFormatError("The campaign's stock enemy drops are invalid")
    members = {"host", *checkpoint["characters"]}
    cqc_visits = set()
    cqc_drops = set()
    for guid, roll in enemy_rolls.items():
        if (not valid_guid(guid) or type(roll) is not dict or roll.get("guid") != guid
                or (roll.get("map") != "/Game/Classes/Maps/PlaytestMap_01"
                    and (roll.get("map") != DEAD_BRIDGE or roll.get("bandit") not in CQC_DROP_SOURCES))
                or roll.get("class_path") != "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"
                or type(roll.get("quantity")) is not int or not 3 <= roll["quantity"] <= 9
                or not isinstance(roll.get("position"), list) or len(roll["position"]) != 3
                or any(type(axis) not in (int, float) or not math.isfinite(axis) or abs(axis) > 10000000 for axis in roll["position"])
                or roll.get("killer") not in members
                or roll.get("claimed_by") is not None and (roll["claimed_by"] not in members or "escrow" in roll)
                or roll.get("claimed_by") is None and (type(roll.get("escrow")) is not dict
                                                       or roll["escrow"].get("map") != roll["map"])):
            raise CampaignFormatError("A stock enemy drop has an invalid escrow or owner")
        if roll.get("map") == DEAD_BRIDGE:
            epoch, actor = roll.get("epoch"), roll.get("actor")
            if (not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{32}", epoch)
                    or not isinstance(actor, str) or not actor.startswith(CQC_SHELL_ACTOR)
                    or not re.fullmatch(r"[0-9]+", actor.removeprefix(CQC_SHELL_ACTOR))
                    or (epoch, roll["bandit"]) in cqc_visits or (epoch, actor) in cqc_drops):
                raise CampaignFormatError("A placed CQC has an invalid or duplicate roll in this visit")
            cqc_visits.add((epoch, roll["bandit"]))
            cqc_drops.add((epoch, actor))
        if "retirement" in roll and not cqc_retired(roll):
            raise CampaignFormatError("A stock enemy drop has an invalid retirement receipt")
    bags = party.get("native_backpacks", {})
    if type(bags) is not dict or any(type(bag) is not dict for bag in bags.values()):
        raise CampaignFormatError("The campaign's stock backpack records are invalid")
    quests = []
    for key, area in (("native_wire_quest_reward", BOROUGH), ("native_meet_broker_reward", BLACK_MARKET)):
        reward = party.get(key)
        if reward is None:
            continue
        if (type(reward) is not dict or type(reward.get("escrow")) is not dict
                or reward["escrow"].get("map") != area or not valid_guid(reward.get("weapon"))
                or not valid_guid(reward.get("ammo")) or reward["weapon"] == reward["ammo"]
                or type(reward.get("weapon_mag")) is not int or not 0 <= reward["weapon_mag"] <= 256
                or type(reward.get("pending")) is not dict or not reward["pending"]):
            raise CampaignFormatError("The campaign's stock quest reward record is invalid")
        used = {reward["weapon"], reward["ammo"]}
        for character, items in reward["pending"].items():
            if (character not in checkpoint["characters"] or type(items) is not list or len(items) != 2
                    or any(not valid_guid(item) or item in used for item in items) or items[0] == items[1]):
                raise CampaignFormatError("The campaign's pending stock quest recipient or reward identity is invalid")
            used.update(items)
        quests.append(reward["escrow"])
    blanks, products, operations = set(), set(), set()
    for profile in TAPE_PROFILES.values():
        exchanges = party.get(profile.ledger, {})
        if type(exchanges) is not dict:
            raise CampaignFormatError("The campaign's stock station exchanges are invalid")
        for blank, trade in exchanges.items():
            if (not valid_guid(blank) or blank in blanks or type(trade) is not dict or trade.get("blank_guid") != blank
                    or not isinstance(trade.get("owner"), str) or trade["owner"] not in checkpoint["characters"]
                    or not valid_guid(trade.get("filled_guid")) or trade["filled_guid"] in products
                    or not isinstance(trade.get("operation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", trade["operation_id"])
                    or (trade["owner"], trade["operation_id"]) in operations):
                raise CampaignFormatError("A stock station exchange has an invalid owner or tape identity")
            if profile != STONE:
                expected = uuid.uuid5(uuid.NAMESPACE_URL, f"{profile.namespace}:{trade['owner']}:{blank}").hex.upper()
                if (trade.get("map"), trade.get("station"), trade.get("class_path"), trade["filled_guid"]) != (
                        profile.map, profile.station, profile.product, expected):
                    raise CampaignFormatError("A stock station exchange does not match its exact product and station")
            blanks.add(blank)
            products.add(trade["filled_guid"])
            operations.add((trade["owner"], trade["operation_id"]))
    if blanks & products:
        raise CampaignFormatError("A stock tape is both an exchange input and a product")
    mosby = party.get("native_mosby_rewards")
    if mosby is not None:
        if (type(mosby) is not dict or type(mosby.get("escrow")) is not dict or mosby["escrow"].get("map") != SCIENTIST_SHOP
                or not valid_guid(mosby.get("template_guid")) or type(mosby.get("pending")) is not dict or not mosby["pending"]):
            raise CampaignFormatError("The campaign's Stone Maze HazOp Gear escrow is invalid")
        used = {mosby["template_guid"]}
        for character, armor in mosby["pending"].items():
            if character not in checkpoint["characters"] or not valid_guid(armor) or armor in used:
                raise CampaignFormatError("A pending Stone Maze reward has an invalid recipient or armor identity")
            used.add(armor)
        quests.append(mosby["escrow"])
    return ([roll["escrow"] for roll in rolls.values() if "escrow" in roll]
            + [roll["escrow"] for roll in enemy_rolls.values() if "escrow" in roll]
            + [bag["escrow"] for bag in bags.values() if "escrow" in bag]
            + quests)


def verify_checkpoint(profile: Path, checkpoint: dict) -> None:
    host = verify(profile, checkpoint["world"])
    slots = {host["slot"]} if host else set()
    for character_id, character in checkpoint["characters"].items():
        saved = character["state"].get("native_guest_save")
        if saved is None:
            continue
        frame = guest_record(profile, saved, character_id)
        if frame["slot"] in slots:
            raise CampaignFormatError("Two campaign owners reference the same Unreal save generation")
        slots.add(frame["slot"])
    for frame in _escrow_frames(checkpoint):
        verify_record(profile, frame)
        if frame["slot"] in slots:
            raise CampaignFormatError("A stock escrow shares another Unreal save generation")
        slots.add(frame["slot"])
    for character, frame in _wipe_frames(checkpoint["world"]).items():
        if character == "host":
            verify_record(profile, frame)
        elif character in checkpoint["characters"]:
            guest_record(profile, frame, character)
        else:
            raise CampaignFormatError("The stock wipe generation has no campaign owner")


def prune(profile: Path, keep: set[str]) -> None:
    for path in directory(profile).glob("SFCoop_*.sav"):
        if SLOT.fullmatch(path.stem) and path.stem not in keep and path.resolve() == path:
            path.unlink(missing_ok=True)
