"""Validate the scoped durable owner-claim ledger for native stock 9mm stacks."""

import math
import re

from .guest_weapon_profiles import NINE_MM_BALL
from .native_gold import valid_guid
from .party import REQUEST_ID
from .travel import OBSERVATORY


STATE = "native_guest_stack_drops"
CAPABILITY = "native_guest_stack_drop_capability"
VIEW = "native_guest_inventory_drop_view"
VIEW_ROW = "native-guest-inventory-drop-view"
VIEW_RESULT = "native-inventory-view-drop-result"
ITEM = NINE_MM_BALL.item_path
MAX_DROPS = 128
MAX_ACTIVE = 4
MAX_QUANTITY = 200
_RECORD_FIELDS = {"owner", "guid", "quantity", "class_path", "map", "location", "claim_id"}


def valid_id(value) -> bool:
    return isinstance(value, str) and REQUEST_ID.fullmatch(value) is not None


def valid_position(value) -> bool:
    return (type(value) is list and len(value) == 3
            and all(type(axis) in (int, float) and abs(axis) <= 1e7 and math.isfinite(axis) for axis in value))


_PAWN = re.compile(r"Player_FP_C " + re.escape(f"{OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_")
                   + r"[0-9]{1,10}\Z")


def parse_view(fields: list[str], state: str, area: str) -> dict:
    if len(fields) != 8 or (state, area) != ("connected", OBSERVATORY):
        raise ValueError("Unexpected stock inventory drop view")
    _, number, reported_area, pawn, guid, item, phase, quantity = fields
    if (reported_area != area or not _PAWN.fullmatch(pawn) or not valid_guid(guid) or item != ITEM
            or phase not in {"confirming", "dropping", "dropped", "refused"}
            or not re.fullmatch(r"[1-9][0-9]{0,15}", number) or int(number) >= 2**53
            or not re.fullmatch(r"[1-9][0-9]{0,2}", quantity) or int(quantity) > MAX_QUANTITY):
        raise ValueError("Invalid stock inventory drop view")
    return dict(sequence=int(number), map=area, pawn=pawn, guid=guid, class_path=item, state=phase, count=int(quantity))


def active_frames(data: dict, members) -> list[dict]:
    """Return only unclaimed escrow generations and validate durable operation identities."""
    drops = data.get(STATE, {})
    if type(drops) is not dict or len(drops) > MAX_DROPS:
        raise ValueError("Invalid stock stack drop ledger")
    slots, operations, active_guids, active_owners = set(), set(), set(), set()
    frames = []
    for drop_id, row in drops.items():
        if not valid_id(drop_id) or type(row) is not dict or not isinstance(row.get("owner"), str) or row["owner"] not in members:
            raise ValueError("Invalid stock stack drop identity or owner")
        owner, claim_id = row["owner"], row.get("claim_id")
        if ((owner, drop_id) in operations or (claim_id is not None and not valid_id(claim_id))
                or set(row) != (_RECORD_FIELDS | ({"escrow"} if claim_id is None else set()))
                or not valid_guid(row.get("guid")) or type(row.get("quantity")) is not int
                or not 1 <= row["quantity"] <= MAX_QUANTITY or row.get("class_path") != ITEM
                or row.get("map") != OBSERVATORY or not valid_position(row.get("location"))):
            raise ValueError("Invalid stock stack drop payload")
        operations.add((owner, drop_id))
        if claim_id is not None:
            if (owner, claim_id) in operations:
                raise ValueError("Repeated stock stack drop operation identity")
            operations.add((owner, claim_id))
            continue
        frame = row["escrow"]
        if (len(frames) >= MAX_ACTIVE or type(frame) is not dict or frame.get("map") != OBSERVATORY
                or not isinstance(frame.get("slot"), str) or frame["slot"] in slots
                or owner in active_owners or row["guid"] in active_guids):
            raise ValueError("Invalid or duplicate stock stack drop escrow")
        slots.add(frame["slot"])
        active_guids.add(row["guid"])
        active_owners.add(owner)
        frames.append(frame)
    return frames


def visible(row: dict, drop_id: str) -> dict:
    return {"drop_id": drop_id, **{key: row[key] for key in ("owner", "guid", "quantity", "class_path", "map")},
            "location": list(row["location"])}
