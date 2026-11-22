"""Validate the durable ledger for shared native stock inventory drops."""

import math
import re

from .authored_routes import MAPS as NORMAL_CAMPAIGN_MAPS
from .guest_weapon_profiles import MAX_TAKE_AMMO_ROUNDS, NINE_MM_BALL, STOCK_AMMO
from .native_guest_consumable import MEALS, RESULT as CONSUMABLE_RESULTS
from .native_gold import valid_guid
from .party import REQUEST_ID
from .travel import AUTHORED_MAPS, CROSSROADS, OBSERVATORY


STATE = "native_guest_stack_drops"
PICKUP_ACTION = "native-guest-stack-reclaim"
CAPABILITY = "native_guest_stack_drop_capability"
GENERIC_CAPABILITY = "native_generic_inventory_drop"
VIEW = "native_guest_inventory_drop_view"
VIEW_ROW = "native-guest-inventory-drop-view"
VIEW_RESULT = "native-inventory-view-drop-result"
STOCK_ACTION_ROW = "native-inventory-stock-action"
STOCK_ACTION_KEY = "native_inventory_stock_action"
ITEM = NINE_MM_BALL.item_path
LIGHT_PISTOL = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
STACKABLE = {
    **dict.fromkeys((ammo.item_path for ammo in STOCK_AMMO), True),
    **dict.fromkeys((food.class_path for food in MEALS.values()), True),
    LIGHT_PISTOL: False,
}
MAPS = {area: area.rsplit("/", 1)[1] for area in NORMAL_CAMPAIGN_MAPS | AUTHORED_MAPS}
MAX_DROPS = 128
MAX_ACTIVE = 4
MAX_QUANTITY = 200
_RECORD_FIELDS = {"owner", "guid", "quantity", "class_path", "map", "location", "claim_id"}
_MERGE_FIELDS = {"inventory_guid", "stack_merge"}
_CLASS = re.compile(r"/Game/Classes/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.\1_C\Z")


def valid_id(value) -> bool:
    return isinstance(value, str) and REQUEST_ID.fullmatch(value) is not None


def parse_stock_action(fields, game):
    _, area, pawn, epoch, sequence, entry, guid, count, expected = fields
    if ((game.get("state"), game.get("map"), game.get("pawns", {}).get("local", {}).get("name"))
            != ("connected", area, pawn) or not valid_id(epoch)
            or not re.fullmatch(r"[1-9][0-9]{0,15}", sequence) or int(sequence) >= 2**53
            or not all(re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", value) for value in (count, expected))):
        raise ValueError("Invalid stock backpack request scope")
    amount, before = int(count), int(expected)
    mode = {"sort": "sort", "split": "split", "take-ammo": "unload", "equip": "equip",
            "context_menu": "drop", "ctrl_click": "drop"}.get(entry)
    if mode is None and entry.startswith("use-") and f"native-guest-{entry[4:]}" in CONSUMABLE_RESULTS:
        mode = "use"
    if (mode is None or (guid != "-" and not valid_guid(guid)) or (guid == "-") != (mode == "sort")
            or (mode == "sort" and amount != 0) or (mode in ("use", "equip") and amount != 1)
            or (mode == "drop" and not 1 <= amount <= MAX_QUANTITY)
            or (mode == "split" and not 0 < amount < before < 2**31)
            or (mode == "unload" and not 1 <= amount == before <= MAX_TAKE_AMMO_ROUNDS)
            or (mode not in ("split", "unload") and before != 0)):
        raise ValueError("Invalid stock backpack operation")
    return dict(area=area, pawn=pawn, epoch=epoch, sequence=int(sequence), entry=entry, guid=guid,
                count=amount, expected=before, mode=mode)


def valid_position(value) -> bool:
    return (type(value) is list and len(value) == 3
            and all(type(axis) in (int, float) and abs(axis) <= 1e7 and math.isfinite(axis) for axis in value))


def supported_map(area) -> bool:
    return isinstance(area, str) and area in MAPS


def _paths(actor_class: str) -> dict:
    return {area: re.compile(re.escape(f"{actor_class} {area}.{leaf}:PersistentLevel.{actor_class}_")
                             + r"[0-9]{1,10}\Z") for area, leaf in MAPS.items()}


_PAWN_PATHS = _paths("Player_FP_C")
_ACTOR_PATHS = {path: _paths(path.rsplit(".", 1)[1]) for path in STACKABLE}


def pawn_matches(area, name) -> bool:
    return supported_map(area) and isinstance(name, str) and _PAWN_PATHS[area].fullmatch(name) is not None


def actor_class_path(area, name) -> str | None:
    if supported_map(area) and isinstance(name, str):
        return next((path for path, names in _ACTOR_PATHS.items() if names[area].fullmatch(name)), None)


def actor_matches(area, name, class_path=None) -> bool:
    if class_path is None:
        return actor_class_path(area, name) is not None
    if not supported_map(area) or not isinstance(name, str) or not valid_class(class_path):
        return False
    actor = class_path.rsplit(".", 1)[1]
    prefix = f"{actor} {area}.{MAPS[area]}:PersistentLevel.{actor}_"
    return name.startswith(prefix) and re.fullmatch(r"[0-9]{1,10}", name[len(prefix):]) is not None


def valid_class(path) -> bool:
    return isinstance(path, str) and len(path) <= 256 and _CLASS.fullmatch(path) is not None


def valid_quantity(path, count) -> bool:
    if not valid_class(path) or type(count) is not int:
        return False
    return 1 <= count <= (MAX_QUANTITY if STACKABLE.get(path, True) else 1)


def parse_view(fields: list[str], state: str, area: str) -> dict:
    if len(fields) != 8 or state != "connected" or not supported_map(area):
        raise ValueError("Unexpected stock inventory drop view")
    _, number, reported_area, pawn, guid, item, phase, quantity = fields
    if (reported_area != area or not pawn_matches(area, pawn) or not valid_guid(guid)
            or phase not in {"confirming", "dropping", "dropped", "refused"}
            or not re.fullmatch(r"[1-9][0-9]{0,15}", number) or int(number) >= 2**53
            or not re.fullmatch(r"[1-9][0-9]{0,2}", quantity) or not valid_quantity(item, int(quantity))):
        raise ValueError("Invalid stock inventory drop view")
    return dict(sequence=int(number), map=area, pawn=pawn, guid=guid, class_path=item, state=phase, count=int(quantity))


def claimed_by(row: dict) -> str | None:
    if row["claim_id"] is None:
        return None
    # Earlier campaign saves only allowed the original dropper to reclaim.
    return row.get("claimed_by", row["owner"])


def active_frames(data: dict, members) -> list[dict]:
    """Return only unclaimed escrow generations and validate durable operation identities."""
    drops = data.get(STATE, {})
    if type(drops) is not dict or len(drops) > MAX_DROPS:
        raise ValueError("Invalid stock stack drop ledger")
    slots, operations, active_guids = set(), set(), set()
    frames = []
    for drop_id, row in drops.items():
        if (not valid_id(drop_id) or type(row) is not dict or not isinstance(row.get("owner"), str)
                or (row["owner"] != "host" and row["owner"] not in members)):
            raise ValueError("Invalid stock stack drop identity or owner")
        owner, claim_id = row["owner"], row.get("claim_id")
        fields = _RECORD_FIELDS | ({"escrow"} if claim_id is None else set())
        if claim_id is not None and "claimed_by" in row:
            fields |= {"claimed_by"}
        if claim_id is not None and _MERGE_FIELDS.intersection(row):
            fields |= _MERGE_FIELDS
        if ((owner, drop_id) in operations or (claim_id is not None and not valid_id(claim_id))
                or row.keys() != fields
                or not valid_guid(row.get("guid")) or not valid_quantity(row.get("class_path"), row.get("quantity"))
                or not supported_map(row.get("map")) or not valid_position(row.get("location"))):
            raise ValueError("Invalid stock stack drop payload")
        operations.add((owner, drop_id))
        if claim_id is not None:
            if "stack_merge" in row:
                from . import native_loot

                merge, destination = row["stack_merge"], row["inventory_guid"]
                if (type(merge) is not dict or merge.keys() != {"amount", "before", "after"}
                        or "claimed_by" not in row or STACKABLE.get(row["class_path"]) is False):
                    raise ValueError("Invalid stock stack drop merge")
                try:
                    native_loot.verified_stack_merge(
                        dict(merge, guid=row["guid"], inventory_guid=destination), row["guid"], row["quantity"],
                    )
                except RuntimeError as error:
                    raise ValueError("Invalid stock stack drop merge") from error
            claimant = claimed_by(row)
            if not isinstance(claimant, str) or (claimant != "host" and claimant not in members):
                raise ValueError("Invalid stock stack drop claimant")
            if (claimant, claim_id) in operations:
                raise ValueError("Repeated stock stack drop operation identity")
            operations.add((claimant, claim_id))
            continue
        frame = row["escrow"]
        if (len(frames) >= MAX_ACTIVE or type(frame) is not dict or frame.get("map") != row["map"]
                or not isinstance(frame.get("slot"), str) or frame["slot"] in slots
                or row["guid"] in active_guids):
            raise ValueError("Invalid or duplicate stock stack drop escrow")
        slots.add(frame["slot"])
        active_guids.add(row["guid"])
        frames.append(frame)
    return frames


def visible(row: dict, drop_id: str) -> dict:
    return {"drop_id": drop_id, **{key: row[key] for key in ("owner", "guid", "quantity", "class_path", "map")},
            "location": list(row["location"])}
