"""Read back the authored Data Silo lift and its stock movement bases."""

import math
import re
import time

from . import native_mindcore as core, party
from .native_mindcore_view import valid_pawn


TAG = "native-datasilo-lift"
KEY = "native_datasilo_lift"
RIDER_TAG = "native-datasilo-lift-rider"
RIDER_KEY = "native_datasilo_lift_riders"
FIXTURE = "native-datasilo-lift-fixture"
STAGE_RIDER = "native-datasilo-lift-stage-rider"
ACTOR = f"RailLift_BP_C {core.LEVEL}RailLift_BP_C_1"
DECK = f"StaticMeshComponent {core.LEVEL}RailLift_BP_C_1.SM_Prop_Elevator_01"
STOPS = {"lower": 0.0, "upper": 2400.0}
STATES = {"active", "guarded", "awaiting_replica", "replication_unverified", "guard_unverified"}


def _position(fields):
    point = [float(field) for field in fields]
    if any(not math.isfinite(axis) or abs(axis) >= 1e7 for axis in point):
        raise ValueError("Invalid stock lift position")
    return point


def parse_lift(fields, role):
    if len(fields) != 20 or fields[:3] != [TAG, core.MAP, role] or fields[4] != ACTOR:
        raise ValueError("Invalid stock lift identity")
    state, net_role, replication, movement, deck, button, stop = fields[3], *fields[5:11]
    if (state not in STATES or net_role not in {"0", "1", "2", "3"}
            or replication not in {"0", "1"} or movement not in {"0", "1"}
            or deck not in {"0", "1", "2", "3", "4", "5"} or button not in {"0", "1", "2", "3", "4", "5"}
            or stop not in (*STOPS, "moving")):
        raise ValueError("Invalid stock lift state")
    root, deck_position, button_position = (_position(fields[i:i + 3]) for i in (11, 14, 17))
    return {"actor": ACTOR, "role": role, "state": state, "net_role": int(net_role),
            "replicates": replication == "1", "replicate_movement": movement == "1",
            "deck_collision": int(deck), "button_collision": int(button), "stop": stop,
            "root_position": root, "deck_position": deck_position, "button_position": button_position}


def parse_rider(fields):
    if (len(fields) != 8 or fields[:2] != [RIDER_TAG, core.MAP] or not re.fullmatch(r"local|guest[2-4]?", fields[2])
            or not valid_pawn(fields[3]) or fields[4] != DECK):
        raise ValueError("Invalid stock lift rider")
    return fields[2], {"pawn": fields[3], "base": DECK, "position": _position(fields[5:8])}


async def exercise_fixture(host, bridge, target):
    game = host.last_game
    before = game.get(KEY, {})
    if (not host.fixtures or host.transitioning or host.stopping or game.get("map") != core.MAP
            or before.get("state") != "active" or before.get("stop") not in STOPS
            or target not in {"power", "button"}):
        raise party.RuleError("The isolated stock Data Silo lift fixture is unavailable")
    frame = await bridge.command(FIXTURE, target, timeout=5)
    if (frame.get("map"), frame.get(KEY, {}).get("actor")) != (core.MAP, ACTOR):
        raise RuntimeError("The exact Data Silo lift did not acknowledge the stock request")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {KEY: frame[KEY], "input": target, "source_stop": before["stop"]}


async def stage_rider(host, bridge, target):
    game = host.last_game
    lift = game.get(KEY, {})
    seats = {
        "host": ("host", [-1672.0, -49.0, 160.0]),
        "guest": ("guest", [-1672.0, 49.0, 160.0]),
        # Land inside the south-west rail under stock gravity without starting inside the loose Core.
        "host-clear-corner": ("host", [-1698.0, -80.0, 250.0]),
        "guest-clear-corner": ("guest", [-1698.0, 80.0, 250.0]),
    }
    if (not host.fixtures or host.stopping or host.transitioning or game.get("map") != core.MAP
            or lift.get("state") != "active" or lift.get("stop") != "lower"
            or target not in seats):
        raise party.RuleError("The isolated lower lift approach is unavailable")
    role, location = seats[target]
    alias, binding = host.role_pawn(role)
    pawn = game.get("pawns", {}).get(alias, {}).get("name")
    if not alias or not valid_pawn(pawn):
        raise party.RuleError("The isolated lift approach requires the exact active pawn")
    frame = await bridge.restore(alias, {"location": location}, binding=binding)
    restored = frame.get("pawns", {}).get(alias, {})
    if (frame.get("map"), restored.get("name")) != (core.MAP, pawn):
        raise RuntimeError("The isolated lift approach lost the exact active pawn")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {"pawn": pawn, "position": restored["location"]}
