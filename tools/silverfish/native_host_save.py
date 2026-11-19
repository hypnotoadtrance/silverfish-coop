"""Pair an immutable, isolated Unreal save generation with a co-op checkpoint."""

from hashlib import file_digest
import os
from pathlib import Path
import re
import uuid

from .campaign import CampaignFormatError
from .native_gold import valid_guid
from .travel import BOROUGH


SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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
    bags = party.get("native_backpacks", {})
    if type(bags) is not dict or any(type(bag) is not dict for bag in bags.values()):
        raise CampaignFormatError("The campaign's stock backpack records are invalid")
    wire_quest = party.get("native_wire_quest_reward")
    if wire_quest is not None:
        if (type(wire_quest) is not dict or type(wire_quest.get("escrow")) is not dict
                or wire_quest["escrow"].get("map") != BOROUGH or not valid_guid(wire_quest.get("weapon"))
                or not valid_guid(wire_quest.get("ammo")) or wire_quest["weapon"] == wire_quest["ammo"]
                or type(wire_quest.get("weapon_mag")) is not int or not 0 <= wire_quest["weapon_mag"] <= 256
                or type(wire_quest.get("pending")) is not dict or not wire_quest["pending"]):
            raise CampaignFormatError("The campaign's stock Copper Wire reward record is invalid")
        used = {wire_quest["weapon"], wire_quest["ammo"]}
        for character, items in wire_quest["pending"].items():
            if (character not in checkpoint["characters"] or type(items) is not list or len(items) != 2
                    or any(not valid_guid(item) or item in used for item in items) or items[0] == items[1]):
                raise CampaignFormatError("The campaign's pending Copper Wire recipient or reward identity is invalid")
            used.update(items)
    return ([roll["escrow"] for roll in rolls.values() if "escrow" in roll]
            + [bag["escrow"] for bag in bags.values() if "escrow" in bag]
            + ([wire_quest["escrow"]] if wire_quest else []))


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
