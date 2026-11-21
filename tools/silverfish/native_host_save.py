"""Pair an immutable, isolated Unreal save generation with a co-op checkpoint."""

from dataclasses import dataclass
from hashlib import file_digest
import math
import os
from pathlib import Path
import re
import stat
import uuid

from .campaign import CampaignFormatError
from .authored_loot import CRATES, loot_capacity, valid_item_transform, valid_lid_pitch, valid_loot_trait_calls
from .enemy_loot_profiles import BY_MAP as ENEMY_LOOT_ZONES, SHELLS, for_roll as enemy_loot_profile
from .native_gold import valid_guid
from .native_guest_drop_record import active_frames as stack_drop_frames
from .native_host_dig_live import escrow_frames as dig_escrow_frames
from .native_tape_profiles import (
    BY_MAP as TAPE_PROFILES, DRILL, INSTRUCTIONS, STONE, drill_download_unlocked, instructions_download_unlocked,
)
from .travel import BLACK_MARKET, BOROUGH, DEAD_BRIDGE, SCIENTIST_SHOP


SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
STOCK_START_MODES = ("Mercy", "Respawn_WtPenalty", "Decimation")
SCOPE_FIELD = "native_save_scope"
CQC_DROP_SOURCES = ENEMY_LOOT_ZONES[DEAD_BRIDGE].actors
CQC_DROP_SOURCE = CQC_DROP_SOURCES[0]
CQC_SHELL_ACTOR = ENEMY_LOOT_ZONES[DEAD_BRIDGE].shell_prefix


def cqc_retirement_record(roll, slot):
    proof = {"schema": 1, "epoch": roll["epoch"], "bandit": roll["bandit"], "actor": roll["actor"],
             "guid": roll["guid"], "slot": slot}
    if "profile" in roll:
        proof.update(schema=2, profile=roll["profile"])
    return proof


def cqc_retired(roll):
    proof = roll.get("retirement")
    slot = proof.get("slot") if type(proof) is dict else None
    if (not enemy_loot_profile(roll) or not isinstance(slot, str) or not SLOT.fullmatch(slot) or type(proof.get("schema")) is not int
            or any(key not in roll for key in ("epoch", "actor", "guid"))):
        return False
    frame = roll.get("escrow")
    return (proof == cqc_retirement_record(roll, slot)
            and (roll.get("claimed_by") is not None or (isinstance(frame, dict) and frame.get("slot") == slot)))


def new_slot() -> str:
    return "SFCoop_" + uuid.uuid4().hex


@dataclass(frozen=True)
class SaveScope:
    profile: Path
    id: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-f0-9]{32}", self.id) or self.id == "0" * 32:
            raise CampaignFormatError("The co-op save scope ID is invalid")


Profile = Path | SaveScope


def checkpoint_profile(profile: Path, checkpoint: dict) -> Profile:
    world = checkpoint["world"]
    if SCOPE_FIELD not in world:
        return profile
    campaign_id = checkpoint.get("campaign_id")
    try:
        expected = uuid.UUID(campaign_id).hex if isinstance(campaign_id, str) else None
    except ValueError:
        expected = None
    if expected is None or world[SCOPE_FIELD] != expected:
        raise CampaignFormatError("The save scope does not belong to this campaign")
    return SaveScope(profile, expected)


def directory(profile: Profile) -> Path:
    scope = profile if isinstance(profile, SaveScope) else None
    if scope:
        profile = scope.profile
    root = profile.resolve()
    base = root / "Saved" / "SaveGames"
    requested = base / "SilverfishCoop" / scope.id if scope else base
    saves = requested.resolve()
    if scope and (profile.absolute() != root or saves != requested):
        raise CampaignFormatError("The co-op save scope points outside its generation directory")
    if not saves.is_relative_to(root):
        raise CampaignFormatError("The staged native save directory escapes its game profile")
    return saves


def slot_path(profile: Profile, slot: str) -> Path:
    if not isinstance(slot, str) or not SLOT.fullmatch(slot):
        raise CampaignFormatError("Invalid co-op Unreal save slot")
    saves = directory(profile)
    path = saves / f"{slot}.sav"
    if path.resolve() != path:
        raise CampaignFormatError("The co-op Unreal save slot points outside its generation path")
    return path


def record(profile: Profile, slot: str, map_name: str, *, flush: bool = False,
           expected_size: int | None = None) -> dict:
    path = slot_path(profile, slot)
    try:
        with path.open("r+b" if flush else "rb") as stream:
            if flush:
                os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
            size = info.st_size
            if not stat.S_ISREG(info.st_mode):
                raise CampaignFormatError("The Unreal save generation has an unexpected file type")
            if expected_size is not None and size != expected_size:
                raise CampaignFormatError("The Unreal save generation does not match its campaign checkpoint")
            if size < 16 or stream.read(4) != b"GVAS":
                raise CampaignFormatError(f"The Unreal save generation {slot} has an invalid header")
            stream.seek(0)
            digest = file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise CampaignFormatError(f"Cannot read the isolated Unreal save generation {slot}") from error
    return {"slot": slot, "map": map_name, "size": size, "sha256": digest}


def verify(profile: Profile, world: dict) -> dict | None:
    saved = world.get("native_host_save")
    if saved is None:
        return None
    verify_record(profile, saved)
    if saved["map"] != world.get("map"):
        raise CampaignFormatError("The host save generation is for a different campaign map")
    return saved


def verify_record(profile: Profile, saved: dict) -> None:
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
    if record(profile, saved["slot"], saved["map"], expected_size=saved["size"]) != {key: saved[key] for key in core_keys}:
        raise CampaignFormatError("The Unreal save generation does not match its campaign checkpoint")


def guest_record(profile: Profile, saved: dict, character_id: str) -> dict:
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
    from . import native_mindcore, native_prototype_drill

    party = checkpoint["world"].get("party") or {}
    if type(party) is not dict:
        raise CampaignFormatError("The campaign's native party records are invalid")
    rolls = party.get("native_loot_rolls", {})
    if type(rolls) is not dict or any(type(roll) is not dict for roll in rolls.values()):
        raise CampaignFormatError("The campaign's native loot records are invalid")
    retired = party.get("native_loot_retired_rolls", {})
    if (type(retired) is not dict or any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key)
            or not isinstance(crate, str) or crate not in CRATES for key, crate in retired.items())
            or any(isinstance(roll.get("id"), str) and roll["id"] in retired for roll in rolls.values())):
        raise CampaignFormatError("The campaign's retired crate roll identities are invalid")
    enemy_rolls = party.get("native_enemy_loot_rolls", {})
    if type(enemy_rolls) is not dict:
        raise CampaignFormatError("The campaign's stock enemy drops are invalid")
    members = {"host", *checkpoint["characters"]}
    for container, roll in rolls.items():
        items = roll.get("items")
        capacity = loot_capacity(container) or 2
        if isinstance(items, list) and len(items) > capacity:
            raise CampaignFormatError("A stock container exceeds its exact escrow capacity")
        if isinstance(items, list) and any(type(item) is dict and "transform" in item
                                           and not valid_item_transform(item["transform"]) for item in items):
            raise CampaignFormatError("The stock loot actor transform is invalid")
        if not valid_lid_pitch(container, roll.get("lid_pitch")):
            raise CampaignFormatError("The stock loot lid pose is invalid")
        if capacity == 2:
            continue
        calls = roll.get("trait_calls")
        if (not isinstance(items, list) or (roll.get("crate"), roll.get("map")) != (container, CRATES[container])
                or type(roll.get("escrow")) is not dict or roll["escrow"].get("map") != CRATES[container]
                or not isinstance(roll.get("opener"), str) or roll["opener"] not in members
                or not valid_loot_trait_calls(container, calls)
                or any(type(item) is not dict or not valid_guid(item.get("guid"))
                       or not isinstance(item.get("actor"), str) or not item["actor"]
                       or not isinstance(item.get("class_path"), str) or not item["class_path"].startswith("/Game/")
                       or item.get("claimed_by") is not None and (not isinstance(item["claimed_by"], str)
                                                                  or item["claimed_by"] not in members) for item in items)
                or len({item["guid"] for item in items}) != len(items)
                or len({item["actor"] for item in items}) != len(items)):
            raise CampaignFormatError("The stock container escrow has an invalid identity or item")
    cqc_visits = set()
    cqc_drops = set()
    for guid, roll in enemy_rolls.items():
        profile = enemy_loot_profile(roll) if type(roll) is dict else None
        if (not valid_guid(guid) or type(roll) is not dict or roll.get("guid") != guid
                or (not profile and (roll.get("map") != "/Game/Classes/Maps/PlaytestMap_01" or "profile" in roll))
                or roll.get("class_path") != SHELLS
                or type(roll.get("quantity")) is not int or not 3 <= roll["quantity"] <= 9
                or not isinstance(roll.get("position"), list) or len(roll["position"]) != 3
                or any(type(axis) not in (int, float) or not math.isfinite(axis) or abs(axis) > 10000000 for axis in roll["position"])
                or roll.get("killer") not in members
                or roll.get("claimed_by") is not None and (roll["claimed_by"] not in members or "escrow" in roll)
                or roll.get("claimed_by") is None and (type(roll.get("escrow")) is not dict
                                                       or roll["escrow"].get("map") != roll["map"])):
            raise CampaignFormatError("A stock enemy drop has an invalid escrow or owner")
        if profile:
            epoch, actor = roll.get("epoch"), roll.get("actor")
            if (not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{32}", epoch)
                    or not profile.owns_shell(actor)
                    or (profile.map, epoch, roll["bandit"]) in cqc_visits or (profile.map, epoch, actor) in cqc_drops):
                raise CampaignFormatError("A placed CQC has an invalid or duplicate roll in this visit")
            cqc_visits.add((profile.map, epoch, roll["bandit"]))
            cqc_drops.add((profile.map, epoch, actor))
        if "retirement" in roll and not cqc_retired(roll):
            raise CampaignFormatError("A stock enemy drop has an invalid retirement receipt")
    bags = party.get("native_backpacks", {})
    if type(bags) is not dict or any(type(bag) is not dict for bag in bags.values()):
        raise CampaignFormatError("The campaign's stock backpack records are invalid")
    try:
        manual_drops = stack_drop_frames(party, checkpoint["characters"])
        dug_package = dig_escrow_frames(party, checkpoint["characters"])
    except ValueError as error:
        raise CampaignFormatError(str(error)) from error
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
        if profile == DRILL and exchanges and not drill_download_unlocked(party):
            raise CampaignFormatError("A Drill Test Results exchange has no durable Prototype Drill completion")
        if profile == INSTRUCTIONS and exchanges and not instructions_download_unlocked(party):
            raise CampaignFormatError("A Drill Instructions exchange has no durable MindCore compilation")
        for blank, trade in exchanges.items():
            owner = trade.get("owner") if type(trade) is dict else None
            if (not valid_guid(blank) or blank in blanks or type(trade) is not dict or trade.get("blank_guid") != blank
                    or not isinstance(owner, str) or not (owner in checkpoint["characters"] or profile.allows_host and owner == "host")
                    or not valid_guid(trade.get("filled_guid")) or trade["filled_guid"] in products
                    or not isinstance(trade.get("operation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", trade["operation_id"])
                    or (owner, trade["operation_id"]) in operations):
                raise CampaignFormatError("A stock station exchange has an invalid owner or tape identity")
            if profile == DRILL and (type(trade.get("mosby_drill_test")) is not int or not 1 <= trade["mosby_drill_test"] <= 3):
                raise CampaignFormatError("A Drill Test Results exchange has no verified stock stage")
            if profile == INSTRUCTIONS and (type(trade.get("console_status")) is not int or trade["console_status"] != 2):
                raise CampaignFormatError("A Drill Instructions exchange has no verified compiled console stage")
            if profile != STONE:
                expected = uuid.uuid5(uuid.NAMESPACE_URL, f"{profile.namespace}:{owner}:{blank}").hex.upper()
                if (trade.get("map"), trade.get("station"), trade.get("class_path"), trade["filled_guid"]) != (
                        profile.map, profile.station, profile.product, expected):
                    raise CampaignFormatError("A stock station exchange does not match its exact product and station")
            blanks.add(blank)
            products.add(trade["filled_guid"])
            operations.add((owner, trade["operation_id"]))
    if blanks & products:
        raise CampaignFormatError("A stock tape is both an exchange input and a product")
    if not native_mindcore.valid_receipt(party, checkpoint["characters"]):
        raise CampaignFormatError("The Data Silo upload has no exact preserved Results tape owner")
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
    quests.extend(native_prototype_drill.escrow_frames(checkpoint))
    valve = party.get("native_subway_valve_rewards")
    if valve is not None:
        if (type(valve) is not dict or type(valve.get("escrow")) is not dict or valve["escrow"].get("map") != BOROUGH
                or type(valve.get("template")) is not list or len(valve["template"]) != 2
                or any(not valid_guid(guid) for guid in valve["template"]) or len(set(valve["template"])) != 2
                or type(valve.get("pending")) is not dict or not valve["pending"]):
            raise CampaignFormatError("The campaign's Flood Control reward bundle is invalid")
        used = set(valve["template"])
        for character, bundle in valve["pending"].items():
            if (character not in checkpoint["characters"] or type(bundle) is not list or len(bundle) != 2
                    or any(not valid_guid(guid) or guid in used for guid in bundle) or bundle[0] == bundle[1]):
                raise CampaignFormatError("A pending Flood Control bundle has an invalid owner or item GUID")
            used.update(bundle)
        quests.append(valve["escrow"])
    return ([roll["escrow"] for roll in rolls.values() if "escrow" in roll]
            + [roll["escrow"] for roll in enemy_rolls.values() if "escrow" in roll]
            + [bag["escrow"] for bag in bags.values() if "escrow" in bag]
            + manual_drops
            + dug_package
            + quests)


def verify_checkpoint(profile: Path, checkpoint: dict) -> None:
    profile = checkpoint_profile(profile, checkpoint)
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


def prune(profile: Profile, keep: set[str]) -> None:
    for path in directory(profile).glob("SFCoop_*.sav"):
        if SLOT.fullmatch(path.stem) and path.stem not in keep and path.resolve() == path:
            path.unlink(missing_ok=True)
