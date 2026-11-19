"""Durable departure, timed visit, and return to an authoritative rest point."""

import math
import uuid

from . import native_blue, party
from .native_gold import valid_guid
from .travel import CROSSROADS, EXCHANGE


ARRIVAL = "PlayerStart_1"
VISIT_SECONDS = 93
RETURN_FADE_SECONDS = 3
MAX_STACK = 2_147_483_647
STOCK_PAYLOAD = "stock-default-v1"


def visit(state: dict) -> dict | None:
    saved = state.get("stardust_visit")
    if saved is None:
        return None
    if type(saved) is not dict or set(saved) != {
        "id", "guid", "before", "payload", "phase", "deadline", "source", "destination", "return_map", "return_bed",
    }:
        raise ValueError("Invalid saved Stardust visit")
    phase = saved["phase"]
    deadline = saved["deadline"]
    before = saved["before"]
    if (not valid_guid(saved["id"]) or not valid_guid(saved["guid"]) or type(before) is not int
            or not 1 <= before <= MAX_STACK or saved["payload"] not in ("", STOCK_PAYLOAD)
            or (before > 1 and saved["payload"] != STOCK_PAYLOAD)
            or saved["source"] != CROSSROADS or saved["destination"] != EXCHANGE
            or not native_blue.valid_rest(CROSSROADS, saved["return_map"], saved["return_bed"])
            or phase not in ("departing", "visiting", "returning")
            or (phase == "departing" and deadline is not None)
            or (phase != "departing" and (type(deadline) not in (int, float) or not math.isfinite(deadline)
                                           or deadline <= 0))):
        raise ValueError("Invalid saved Stardust visit")
    return saved


def begin(state: dict, guid: str, before: int, payload: str, return_map: str, return_bed: str) -> dict:
    if visit(state):
        raise ValueError("stardust_visit_in_progress")
    proposed = party.load_party(state)
    proposed["stardust_visit"] = {
        "id": uuid.uuid4().hex.upper(), "guid": guid, "before": before, "payload": payload,
        "source": CROSSROADS, "destination": EXCHANGE, "return_map": return_map, "return_bed": return_bed,
        "phase": "departing", "deadline": None,
    }
    visit(proposed)
    return proposed


def arrived(state: dict, now: float) -> dict:
    active = visit(state)
    if not active or active["phase"] != "departing":
        raise ValueError("A Stardust visit must depart before it arrives")
    proposed = party.load_party(state)
    proposed["stardust_visit"].update(phase="visiting", deadline=now + VISIT_SECONDS)
    return proposed


def return_due(state: dict, now: float) -> bool:
    active = visit(state)
    return bool(active and (active["phase"] == "returning" or (
        active["phase"] == "visiting" and now >= active["deadline"] + RETURN_FADE_SECONDS
    )))


def accelerate(state: dict, now: float) -> dict:
    active = visit(state)
    if not active or active["phase"] != "visiting" or now >= active["deadline"]:
        return state
    proposed = party.load_party(state)
    proposed["stardust_visit"]["deadline"] = now
    return proposed


def returning(state: dict) -> dict:
    active = visit(state)
    if not active or active["phase"] != "visiting":
        raise ValueError("A Stardust visit must be active before its return")
    proposed = party.load_party(state)
    proposed["stardust_visit"]["phase"] = "returning"
    return proposed


def finished(state: dict) -> dict:
    active = visit(state)
    if not active or active["phase"] != "returning":
        raise ValueError("A Stardust return must be committed before its arrival")
    proposed = party.load_party(state)
    proposed.pop("stardust_visit")
    proposed["last_stardust_visit"] = {
        "id": active["id"], "guid": active["guid"], "remaining": active["before"] - 1, "return_map": active["return_map"],
    }
    return proposed
