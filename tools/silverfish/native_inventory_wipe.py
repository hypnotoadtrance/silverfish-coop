"""Freeze exact native inventory generations and plan a stock-style wipe.

This module reads immutable GVAS references and produces partition plans. A
game-thread caller must compare the compact census against the complete typed
source inventory before it prepares replacement frames or applies a plan.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid

from . import native_host_save
from .campaign import CampaignFormatError
from .native_gold import valid_guid


SCHEMA = 1
BLACK_STONE = "/Game/Classes/Items/Artifacts/Art_BlackStone.Art_BlackStone_C"
ARRAY_SOURCES = ("backpack", "equipment")
SOURCES = (*ARRAY_SOURCES, "armor", "helmet")
SOURCE_ORDER = {name: index for index, name in enumerate(SOURCES)}
MAX_ARRAY_ROWS = 4096  # Matches the native guest item scanner.
MAX_ITEMS = len(ARRAY_SOURCES) * MAX_ARRAY_ROWS + 2
MAX_QUANTITY = 2_147_483_647
_WIPE_ID = re.compile(r"[0-9a-f]{32}\Z")
_CLASS_PATH = re.compile(r"/Game/[\w/%-]+\.[\w%-]+_C\Z", re.ASCII)
_ITEM_FIELDS = {"guid", "source", "index", "class_path", "quantity", "stackable", "important", "cannot_keep"}
_DRAFT_FIELDS = {"schema", "id", "roster", "frames", "inventories", "settings", "sha256"}


class InventoryWipeError(ValueError):
    """A native inventory plan was refused before any write."""


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _actor_id(value: object) -> bool:
    if value == "host":
        return True
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _settings(keep_slots, drop_bone_pile, lose_slot_per_death) -> dict:
    if keep_slots is not None and (type(keep_slots) is not int or not 0 <= keep_slots <= 10):
        raise InventoryWipeError("invalid_keep_slots")
    if type(drop_bone_pile) is not bool or type(lose_slot_per_death) is not bool:
        raise InventoryWipeError("invalid_native_death_settings")
    return {"keep_slots": keep_slots, "drop_bone_pile": drop_bone_pile, "lose_slot_per_death": lose_slot_per_death}


def _items(rows: object) -> list[dict]:
    if type(rows) is not list or len(rows) > MAX_ITEMS:
        raise InventoryWipeError("invalid_native_inventory")
    result = []
    positions, guids = set(), set()
    for item in rows:
        if type(item) is not dict or set(item) != _ITEM_FIELDS:
            raise InventoryWipeError("invalid_native_item")
        guid, source, index, path = item["guid"], item["source"], item["index"], item["class_path"]
        if (
            not valid_guid(guid)
            or not isinstance(source, str) or source not in SOURCES
            or type(index) is not int
            or not 0 <= index < (MAX_ARRAY_ROWS if source in ARRAY_SOURCES else 1)
            or not isinstance(path, str) or len(path) > 512 or not _CLASS_PATH.fullmatch(path)
        ):
            raise InventoryWipeError("invalid_native_item_identity")
        if any(type(item[key]) is not bool for key in ("stackable", "important", "cannot_keep")):
            raise InventoryWipeError("invalid_native_item_policy")
        quantity = item["quantity"]
        if type(quantity) is not int or not 1 <= quantity <= (MAX_QUANTITY if item["stackable"] else 1):
            raise InventoryWipeError("invalid_native_item_quantity")
        if (source, index) in positions or guid in guids:
            raise InventoryWipeError("ambiguous_native_item_identity")
        guids.add(guid)
        positions.add((source, index))
        result.append(dict(item))
    return sorted(result, key=lambda item: (SOURCE_ORDER[item["source"]], item["index"]))


def _qualify(profile, wipe_id, roster, frames, inventories, settings):
    if not isinstance(wipe_id, str) or not _WIPE_ID.fullmatch(wipe_id) or wipe_id == "0" * 32:
        raise InventoryWipeError("invalid_wipe_id")
    if (
        type(roster) not in (list, tuple) or "host" not in roster
        or not all(_actor_id(actor) for actor in roster) or len(set(roster)) != len(roster)
    ):
        raise InventoryWipeError("invalid_native_wipe_roster")
    if type(frames) is not dict or type(inventories) is not dict or set(roster) != set(frames) or set(roster) != set(inventories):
        raise InventoryWipeError("native_wipe_roster_mismatch")

    owners = sorted(roster)
    frozen_frames, frozen_censuses, slots = {}, {}, set()
    for actor in owners:
        frame = frames[actor]
        census = inventories[actor]
        if type(frame) is not dict or not isinstance(frame.get("slot"), str):
            raise InventoryWipeError("invalid_native_frame")
        if frame["slot"] in slots:
            raise InventoryWipeError("shared_native_frame")
        slots.add(frame["slot"])
        if (
            type(census) is not dict or set(census) != {"slot", "sha256", "complete", "items"}
            or census["complete"] is not True
            or census["slot"] != frame["slot"] or census["sha256"] != frame.get("sha256")
        ):
            raise InventoryWipeError("native_inventory_frame_mismatch")
        frozen_frames[actor] = dict(frame)
        frozen_censuses[actor] = {**census, "items": _items(census["items"])}
        try:
            if actor == "host":
                native_host_save.verify_record(profile, frame)
            else:
                native_host_save.guest_record(profile, frame, actor)
        except CampaignFormatError as error:
            raise InventoryWipeError("invalid_native_frame") from error
    return {
        "schema": SCHEMA, "id": wipe_id, "roster": owners,
        "frames": frozen_frames, "inventories": frozen_censuses, "settings": settings,
    }


def freeze(
    profile: Path,
    wipe_id: str,
    roster: list[str] | tuple[str, ...],
    frames: dict,
    inventories: dict,
    *,
    keep_slots: int | None,
    drop_bone_pile: bool,
    lose_slot_per_death: bool = False,
) -> dict:
    """Create a JSON-safe draft tied to complete, separately owned stock save frames.

    Supply the active campaign's native save references as frames. Census
    inventories are keyed by character ID (or host), and each contains
    slot, sha256, complete=True and items. Item indices are zero-based and are
    always zero for the scalar armor and helmet fields. Quantity counts a stock
    stack. Non-stackable rows use quantity=1 even when they carry ammo or charges.
    A None keep_slots setting turns inventory penalties off.
    """
    settings = _settings(keep_slots, drop_bone_pile, lose_slot_per_death)
    body = _qualify(profile, wipe_id, roster, frames, inventories, settings)
    return {**body, "sha256": _digest(body)}


def _read_frozen(profile, draft):
    if type(draft) is not dict or set(draft) != _DRAFT_FIELDS or type(draft["schema"]) is not int or draft["schema"] != SCHEMA:
        raise InventoryWipeError("invalid_native_wipe_draft")
    if not isinstance(draft["sha256"], str) or not native_host_save.SHA256.fullmatch(draft["sha256"]):
        raise InventoryWipeError("invalid_native_wipe_draft")
    settings = draft["settings"]
    if type(settings) is not dict or set(settings) != {"keep_slots", "drop_bone_pile", "lose_slot_per_death"}:
        raise InventoryWipeError("invalid_native_death_settings")
    settings = _settings(**settings)
    body = _qualify(profile, draft["id"], draft["roster"], draft["frames"], draft["inventories"], settings)
    if _digest(body) != draft["sha256"]:
        raise InventoryWipeError("native_wipe_draft_changed")
    return body


def _choice(guids):
    if type(guids) not in (list, tuple) or not all(valid_guid(guid) for guid in guids) or len(set(guids)) != len(guids):
        raise InventoryWipeError("invalid_native_wipe_choice")
    return set(guids)


def _partition(items, chosen, keep_slots, drop_bone_pile):
    if keep_slots is None:
        if chosen:
            raise InventoryWipeError("native_wipe_selection_unavailable")
        return {"kept": [item["guid"] for item in items], "dropped": [], "discarded": [], "forced": [], "preserve_layout": True}

    by_guid = {item["guid"]: item for item in items}
    if chosen - by_guid.keys():
        raise InventoryWipeError("native_wipe_item_unavailable")
    forced = [item["guid"] for item in items if item["source"] == "backpack" and item["class_path"] == BLACK_STONE]
    if len(forced) > 1:
        raise InventoryWipeError("ambiguous_black_stone")
    if chosen.intersection(forced):
        raise InventoryWipeError("black_stone_keep_slot_locked")
    excluded = {item["guid"] for item in items if item["source"] == "backpack" and item["cannot_keep"]} - set(forced)
    if chosen.intersection(excluded):
        raise InventoryWipeError("native_wipe_item_cannot_be_kept")
    if len(chosen) > max(0, keep_slots - len(forced)):
        raise InventoryWipeError("too_many_native_keep_slots")

    retained = chosen.union(forced)
    kept, dropped, discarded = [], [], []
    for item in items:
        guid = item["guid"]
        if guid in retained:
            kept.append(guid)
        elif guid not in excluded and (drop_bone_pile or item["important"]):
            dropped.append(guid)
        else:
            discarded.append(guid)
    return {"kept": kept, "dropped": dropped, "discarded": discarded, "forced": forced, "preserve_layout": False}


def qualify_choice(draft: dict, actor: str, retained: dict | None) -> dict:
    if actor not in draft["roster"] or type(retained) is not dict or any(type(count) is not int or count != 1
                                                                         for count in retained.values()):
        raise InventoryWipeError("invalid_native_wipe_choice")
    settings = draft["settings"]
    _partition(draft["inventories"][actor]["items"], _choice(list(retained)), settings["keep_slots"],
               settings["drop_bone_pile"])
    return dict(retained)


def plan(profile: Path, draft: dict, choices: dict[str, list[str]]) -> dict:
    """Build repeatable per-owner partitions for whole physical inventory rows.

    Choices are character ID to selected GUID lists. Missing choices mean no
    discretionary selection after the caller closes the selection window. The
    selected GUIDs apply to the full stack or equipment row. The native caller
    must still verify every typed source row and prepare the resulting frames.
    """
    frozen = _read_frozen(profile, draft)
    if type(choices) is not dict or set(choices) - set(frozen["roster"]):
        raise InventoryWipeError("native_wipe_choice_not_owned")
    rules = frozen["settings"]
    keep_slots = rules["keep_slots"]
    next_slots = max(0, keep_slots - 1) if keep_slots is not None and rules["lose_slot_per_death"] else keep_slots
    owners, bags, counts, normalized_choices = {}, {}, {}, {}
    for actor in frozen["roster"]:
        chosen = _choice(choices.get(actor, []))
        normalized_choices[actor] = sorted(chosen)
        rows = frozen["inventories"][actor]["items"]
        partition = _partition(rows, chosen, keep_slots, rules["drop_bone_pile"])
        frame = frozen["frames"][actor]
        owners[actor] = {"source": frame, **partition}
        counts[actor] = {key: len(partition[key]) for key in ("kept", "dropped", "discarded", "forced")}
        if partition["dropped"]:
            bag_id = uuid.uuid5(uuid.UUID(hex=frozen["id"]), actor).hex
            bags[bag_id] = {"id": bag_id, "owner_id": actor, "source": dict(frame), "guids": list(partition["dropped"])}

    plan_id = _digest({"draft": draft["sha256"], "choices": normalized_choices})
    receipt = {"wipe_id": frozen["id"], "plan_id": plan_id, "keep_slots": keep_slots,
               "next_keep_slots": next_slots, "owners": counts}
    return {"schema": SCHEMA, "id": frozen["id"], "owners": owners, "bags": bags, "receipt": receipt}


def bag_for(prepared: dict, bag_id: str, requester: str) -> dict:
    """Resolve a bag against a host-authored plan and authenticated requester."""
    if not isinstance(bag_id, str) or not _WIPE_ID.fullmatch(bag_id) or not _actor_id(requester):
        raise InventoryWipeError("native_wipe_bag_not_owned")
    bag = prepared["bags"].get(bag_id)
    if not bag or bag["owner_id"] != requester:
        raise InventoryWipeError("native_wipe_bag_not_owned")
    return deepcopy(bag)
