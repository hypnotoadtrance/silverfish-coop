"""Save a single host draw from the stock Red Cube destination list."""

from secrets import choice

from . import party
from .native_gold import valid_guid
from .travel import AUTHORED_MAPS, EXCHANGE, RED_DESTINATIONS


ARRIVAL = "Red_Tes"
SOURCES = AUTHORED_MAPS - {EXCHANGE}


def draw() -> str:
    return choice(RED_DESTINATIONS)


def ledger(state: dict) -> dict:
    saved = state.get("red_transport", {"pending": None, "spent": {}})
    if (type(saved) is not dict or set(saved) != {"pending", "spent"} or type(saved["spent"]) is not dict
            or any(not valid_guid(guid) or receipt not in ("committed", "arrived")
                   for guid, receipt in saved["spent"].items())):
        raise ValueError("Invalid Red Cube travel ledger")
    pending = saved["pending"]
    committed = [guid for guid, receipt in saved["spent"].items() if receipt == "committed"]
    if (pending is None and committed) or (pending is not None and (
        type(pending) is not dict or set(pending) != {"guid", "source", "destination"}
        or not valid_guid(pending["guid"]) or committed != [pending["guid"]]
        or not isinstance(pending["source"], str) or pending["source"] not in SOURCES
        or pending["destination"] not in RED_DESTINATIONS
    )):
        raise ValueError("Invalid committed Red Cube draw")
    return saved


def begin(state: dict, guid: str, source: str, destination: str) -> dict:
    existing = ledger(state)
    if existing["pending"] or guid in existing["spent"]:
        raise ValueError("The Red Cube already has a committed draw")
    candidate = party.load_party(state)
    updated = candidate.setdefault("red_transport", {"pending": None, "spent": {}})
    updated["pending"] = {"guid": guid, "source": source, "destination": destination}
    updated["spent"][guid] = "committed"
    ledger(candidate)
    return candidate


def arrive(state: dict, guid: str, destination: str) -> dict:
    pending = ledger(state)["pending"]
    if not pending or guid != pending["guid"] or destination != pending["destination"]:
        raise ValueError("Red Cube arrival does not match the committed draw")
    candidate = party.load_party(state)
    updated = candidate["red_transport"]
    updated["pending"] = None
    updated["spent"][guid] = "arrived"
    return candidate
