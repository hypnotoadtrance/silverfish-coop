"""Durable departure, timed visit, and return to an authoritative rest point."""

import math
import uuid
from dataclasses import dataclass

from . import native_blue, party
from .native_gold import valid_guid
from .travel import BLACK_OCEAN, CROSSROADS, EXCHANGE, PYROCLAST, SLATE, THE_ROOK


ARRIVAL = "PlayerStart_1"
COUNTDOWN = "Dream_CountDown_BP_C_1"
MAX_STACK = 2_147_483_647
STOCK_PAYLOAD = "stock-default-v1"
BASE_SECONDS = {"stardust": 90, "fruiting-stardust": 180}
STACK_PAYLOADS = {"stardust": STOCK_PAYLOAD, "fruiting-stardust": "stock-fruiting-default-v1"}
TAROT_DESTINATIONS = {
    "": EXCHANGE, "Tarot_Obelisk": PYROCLAST, "Tarot_Night": SLATE, "Tarot_BlackStag": BLACK_OCEAN,
    "Tarot_Rook": THE_ROOK,
}
LEGACY_KEYS = {
    "id", "guid", "before", "payload", "phase", "deadline", "source", "destination", "return_map", "return_bed",
}


@dataclass(frozen=True)
class DreamProfile:
    actor: str
    multiplier: int
    startup: int
    fade: int
    fixed_seconds: int = 0

    def duration(self, kind: str) -> int:
        return self.fixed_seconds + self.multiplier * BASE_SECONDS[kind]


PROFILES = {
    EXCHANGE: DreamProfile(COUNTDOWN, 1, 3, 3),
    THE_ROOK: DreamProfile(COUNTDOWN, 2, 3, 3),
    PYROCLAST: DreamProfile(COUNTDOWN, 3, 3, 3),
    SLATE: DreamProfile("Slate_P_C_0", 1, 0, 2),
    BLACK_OCEAN: DreamProfile("BlackOcean_P_C_0", 0, 0, 2, fixed_seconds=160),
}


def visit(state: dict) -> dict | None:
    saved = state.get("stardust_visit")
    if saved is None:
        return None
    if type(saved) is not dict:
        raise ValueError("Invalid saved Stardust visit")
    if set(saved) == LEGACY_KEYS and saved["source"] == CROSSROADS and saved["destination"] == EXCHANGE:
        saved = dict(saved, kind="stardust", tarot="", effects="legacy")
    if set(saved) != LEGACY_KEYS | {"kind", "tarot", "effects"}:
        raise ValueError("Invalid saved Stardust visit")
    phase = saved["phase"]
    deadline = saved["deadline"]
    before = saved["before"]
    if (not valid_guid(saved["id"]) or not valid_guid(saved["guid"]) or type(before) is not int
            or not 1 <= before <= MAX_STACK
            or not isinstance(saved["kind"], str) or saved["kind"] not in BASE_SECONDS
            or saved["payload"] not in ("", STACK_PAYLOADS[saved["kind"]])
            or (before > 1 and saved["payload"] != STACK_PAYLOADS[saved["kind"]])
            or not isinstance(saved["tarot"], str) or saved["tarot"] not in TAROT_DESTINATIONS
            or saved["destination"] != TAROT_DESTINATIONS[saved["tarot"]]
            or not native_blue.valid_rest(saved["source"], saved["return_map"], saved["return_bed"])
            or saved["source"] in PROFILES
            or saved["effects"] not in ("legacy", "pending", "applied")
            or (saved["effects"] == "legacy" and (saved["kind"] != "stardust" or saved["tarot"] != ""
                                                     or saved["source"] != CROSSROADS))
            or (saved["effects"] == "pending" and phase != "departing")
            or phase not in ("departing", "visiting", "returning")
            or (phase == "departing" and deadline is not None)
            or (phase != "departing" and (type(deadline) not in (int, float) or not math.isfinite(deadline)
                                           or deadline <= 0))):
        raise ValueError("Invalid saved Stardust visit")
    return saved


def begin(state: dict, guid: str, before: int, payload: str, return_map: str, return_bed: str, *,
          source: str = CROSSROADS, kind: str = "stardust", tarot: str = "") -> dict:
    if visit(state):
        raise ValueError("stardust_visit_in_progress")
    proposed = party.load_party(state)
    proposed["stardust_visit"] = {
        "id": uuid.uuid4().hex.upper(), "guid": guid, "before": before, "payload": payload,
        "source": source, "destination": TAROT_DESTINATIONS.get(tarot), "return_map": return_map,
        "return_bed": return_bed, "phase": "departing", "deadline": None,
        "kind": kind, "tarot": tarot, "effects": "pending",
    }
    visit(proposed)
    return proposed


def effects_applied(state: dict) -> dict:
    active = visit(state)
    if not active or active["phase"] != "departing" or active["effects"] != "pending":
        raise ValueError("Stardust effects must be pending before they can be committed")
    proposed = party.load_party(state)
    proposed["stardust_visit"] = dict(active, effects="applied")
    return proposed


def arrived(state: dict, now: float) -> dict:
    active = visit(state)
    if not active or active["phase"] != "departing" or active["effects"] == "pending":
        raise ValueError("A Stardust visit must depart and commit its effects before it arrives")
    proposed = party.load_party(state)
    profile = PROFILES[active["destination"]]
    deadline = now + profile.startup + profile.duration(active["kind"])
    proposed["stardust_visit"] = dict(active, phase="visiting", deadline=deadline)
    return proposed


def return_due(state: dict, now: float) -> bool:
    active = visit(state)
    return bool(active and (active["phase"] == "returning" or (
        active["phase"] == "visiting" and now >= active["deadline"] + PROFILES[active["destination"]].fade
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
