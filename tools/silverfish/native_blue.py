"""Commit a Blue Cube use and the host's resolved stock rest destination."""

from . import party
from .native_gold import valid_guid
from .travel import AUTHORED_MAPS, DEAD_BRIDGE, OBSERVATORY

BED_IDS = {DEAD_BRIDGE: "SavePoint_BP_C_1-DeadBridge_P-", OBSERVATORY: "SavePoint_BP_C_3-Observitory_P-"}


def valid_rest(source: str, destination: str, bed: str) -> bool:
    if (not isinstance(source, str) or source not in AUTHORED_MAPS
            or not isinstance(destination, str) or destination not in BED_IDS or not isinstance(bed, str)):
        return False
    if not bed:
        return destination == OBSERVATORY
    return BED_IDS[destination] == bed


def saved_rest(level: object, bed: object) -> dict | None:
    for destination, expected in BED_IDS.items():
        if level in (destination, destination.rsplit("/", 1)[1]) and bed == expected:
            return {"destination": destination, "bed": expected}
    return None


def ledger(state: dict) -> dict:
    value = state.get("blue_transport", {"pending": None, "spent": {}})
    if (type(value) is not dict or set(value) != {"pending", "spent"} or type(value["spent"]) is not dict
            or any(not valid_guid(guid) or receipt not in ("committed", "arrived")
                   for guid, receipt in value["spent"].items())):
        raise ValueError("Invalid Blue Cube travel ledger")
    pending = value["pending"]
    committed = [guid for guid, receipt in value["spent"].items() if receipt == "committed"]
    if (pending is None and committed) or (pending is not None and (
        type(pending) is not dict or set(pending) != {"guid", "source", "destination", "bed"}
        or not valid_guid(pending["guid"]) or committed != [pending["guid"]]
        or not valid_rest(pending["source"], pending["destination"], pending["bed"])
    )):
        raise ValueError("Invalid pending Blue Cube travel")
    return value


def begin(state: dict, guid: str, source: str, destination: str, bed: str) -> dict:
    current = ledger(state)
    if not valid_guid(guid) or not valid_rest(source, destination, bed):
        raise ValueError("Invalid Blue Cube itinerary")
    if guid in current["spent"]:
        raise ValueError("blue_already_spent")
    if current["pending"]:
        raise ValueError("blue_travel_pending")
    candidate = party.load_party(state)
    updated = candidate.setdefault("blue_transport", {"pending": None, "spent": {}})
    updated["pending"] = {"guid": guid, "source": source, "destination": destination, "bed": bed}
    updated["spent"][guid] = "committed"
    return candidate


def arrive(state: dict, guid: str, destination: str) -> dict:
    pending = ledger(state)["pending"]
    if not pending or pending["guid"] != guid or destination != pending["destination"]:
        raise ValueError("Blue Cube arrival does not match its committed travel")
    candidate = party.load_party(state)
    updated = candidate["blue_transport"]
    updated["pending"] = None
    updated["spent"][guid] = "arrived"
    return candidate
