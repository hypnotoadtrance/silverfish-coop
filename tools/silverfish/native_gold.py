"""Persist a qualified Golden Cube debit and its destination in one checkpoint."""

from dataclasses import dataclass
import re

from . import party
from .travel import CROSSROADS, GOLD_NEXUS

ARRIVAL = "PlayerStart_1"
INVENTORY_ENTRIES = frozenset({"action_use", "quick_move", "use_held_item"})
_GUID = re.compile(r"[0-9A-F]{32}\Z")


@dataclass(frozen=True)
class GoldRequest:
    sequence: int
    source: str
    pawn: str
    guid: str
    entry: str
    location: tuple[float, float, float]


def valid_guid(guid: object) -> bool:
    return isinstance(guid, str) and bool(_GUID.fullmatch(guid)) and guid != "0" * 32


def ledger(state: dict) -> dict:
    value = state.get("gold_transport", {"pending": None, "spent": {}})
    if (type(value) is not dict or set(value) != {"pending", "spent"} or type(value["spent"]) is not dict
            or any(not valid_guid(guid) or receipt not in ("committed", "arrived")
                   for guid, receipt in value["spent"].items())):
        raise ValueError("Invalid Golden Cube travel ledger")
    pending = value["pending"]
    committed = [guid for guid, receipt in value["spent"].items() if receipt == "committed"]
    if (pending is None and committed) or (pending is not None and (
        type(pending) is not dict or set(pending) != {"guid", "source", "destination", "entrance"}
        or not valid_guid(pending["guid"]) or committed != [pending["guid"]]
        or pending["source"] != CROSSROADS or pending["destination"] != GOLD_NEXUS or pending["entrance"] != ARRIVAL
    )):
        raise ValueError("Invalid pending Golden Cube travel")
    return value


def begin(state: dict, guid: str) -> dict:
    current = ledger(state)
    if not valid_guid(guid):
        raise ValueError("Invalid Golden Cube GUID")
    if guid in current["spent"]:
        raise ValueError("gold_already_spent")
    if current["pending"]:
        raise ValueError("gold_travel_pending")
    candidate = party.load_party(state)
    updated = candidate.setdefault("gold_transport", {"pending": None, "spent": {}})
    updated["pending"] = {"guid": guid, "source": CROSSROADS, "destination": GOLD_NEXUS, "entrance": ARRIVAL}
    updated["spent"][guid] = "committed"
    return candidate


def arrive(state: dict, guid: str, destination: str) -> dict:
    pending = ledger(state)["pending"]
    if not pending or pending["guid"] != guid or destination != pending["destination"]:
        raise ValueError("Golden Cube arrival does not match its committed travel")
    candidate = party.load_party(state)
    updated = candidate["gold_transport"]
    updated["pending"] = None
    updated["spent"][guid] = "arrived"
    return candidate
