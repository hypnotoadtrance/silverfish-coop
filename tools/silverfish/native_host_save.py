"""Pair an immutable, isolated Unreal save generation with a co-op checkpoint."""

from hashlib import file_digest
import os
from pathlib import Path
import re
import uuid

from .campaign import CampaignFormatError


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
    return host + guest


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


def prune(profile: Path, keep: set[str]) -> None:
    for path in directory(profile).glob("SFCoop_*.sav"):
        if SLOT.fullmatch(path.stem) and path.stem not in keep and path.resolve() == path:
            path.unlink(missing_ok=True)
