"""Persist a stock destination draw before debiting its exact consumable GUID."""

from . import party
from .native_gold import valid_guid
from .travel import AUTHORED_MAPS, DREAM_MAPS


SOURCES = AUTHORED_MAPS - DREAM_MAPS


class CubeDraw:
    def __init__(self, cube: str, destinations: tuple[str, ...]):
        self.key = f"{cube}_transport"
        self.label = f"{cube.title()} Cube"
        self.destinations = destinations

    def ledger(self, state: dict) -> dict:
        saved = state.get(self.key, {"pending": None, "spent": {}})
        if (type(saved) is not dict or set(saved) != {"pending", "spent"} or type(saved["spent"]) is not dict
                or any(not valid_guid(guid) or receipt not in ("committed", "arrived")
                       for guid, receipt in saved["spent"].items())):
            raise ValueError(f"Invalid {self.label} travel ledger")
        pending = saved["pending"]
        committed = [guid for guid, receipt in saved["spent"].items() if receipt == "committed"]
        if (pending is None and committed) or (pending is not None and (
            type(pending) is not dict or set(pending) != {"guid", "source", "destination"}
            or not valid_guid(pending["guid"]) or committed != [pending["guid"]]
            or not isinstance(pending["source"], str) or pending["source"] not in SOURCES
            or pending["destination"] not in self.destinations
        )):
            raise ValueError(f"Invalid committed {self.label} draw")
        return saved

    def begin(self, state: dict, guid: str, source: str, destination: str) -> dict:
        existing = self.ledger(state)
        if not valid_guid(guid):
            raise ValueError(f"Invalid {self.label} GUID")
        if existing["pending"] or guid in existing["spent"]:
            raise ValueError(f"The {self.label} already has a committed draw")
        candidate = party.load_party(state)
        updated = candidate.setdefault(self.key, {"pending": None, "spent": {}})
        updated["pending"] = {"guid": guid, "source": source, "destination": destination}
        updated["spent"][guid] = "committed"
        self.ledger(candidate)
        return candidate

    def arrive(self, state: dict, guid: str, destination: str) -> dict:
        pending = self.ledger(state)["pending"]
        if not pending or guid != pending["guid"] or destination != pending["destination"]:
            raise ValueError(f"{self.label} arrival does not match the committed draw")
        candidate = party.load_party(state)
        updated = candidate[self.key]
        updated["pending"] = None
        updated["spent"][guid] = "arrived"
        return candidate
