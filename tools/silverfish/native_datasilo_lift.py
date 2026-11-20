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
ROUTE_TARGETS = {
    "route-west-landing": [-1990, 64, 2400],
    "route-east-stairs": [2217.16, -2277.82, 2410],
    "route-upper-stairs": [1550, 2150, 4510],
    "route-control-room": [-534, -1, 5410],
    "route-catwalk": [0, 4810, 6610],
}


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
            or not isinstance(target, str) or target not in {"power", "button", *ROUTE_TARGETS}):
        raise party.RuleError("The isolated stock Data Silo lift fixture is unavailable")
    frame = await bridge.command(FIXTURE, target, timeout=5)
    if (frame.get("map"), frame.get(KEY, {}).get("actor")) != (core.MAP, ACTOR):
        raise RuntimeError("The exact Data Silo lift did not acknowledge the stock request")
    host.last_game, host.last_seen = frame, time.monotonic()
    if target in ROUTE_TARGETS:
        reply = frame["ack"].get("message", "").split(" ", 2)
        if len(reply) != 3 or reply[0] != target or reply[1] not in {"partial", "complete"}:
            raise RuntimeError("Invalid Data Silo path reply")
        coordinates = [position.split(",") for position in reply[2].split(";")]
        if not 2 <= len(coordinates) <= 100 or any(len(position) != 3 for position in coordinates):
            raise RuntimeError("Invalid Data Silo path points")
        points = [_position(position) for position in coordinates]
        if (math.dist(points[0], [-2132.16, 1053.62, 2400]) > 220
                or reply[1] == "complete" and math.dist(points[-1], ROUTE_TARGETS[target]) > 220):
            raise RuntimeError("The stock path snapped outside the requested walkthrough")
        return {KEY: frame[KEY], "target": target, "complete": reply[1] == "complete", "points": points}
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
        "host-east-corridor": ("host", [2816.0, -1335.0, 2810.0]),
        "guest-east-corridor": ("guest", [2829.0, -1065.0, 2810.0]),
        "host-north-landing": ("host", [1768.0, 2334.0, 3410.0]),
        "guest-north-landing": ("guest", [1655.0, 2022.0, 3410.0]),
    }
    if (not host.fixtures or host.stopping or host.transitioning or game.get("map") != core.MAP
            or lift.get("state") != "active" or lift.get("stop") != "lower"
            or not isinstance(target, str) or target not in seats):
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
    if target.endswith(("-east-corridor", "-north-landing")) and math.dist(restored["location"], location) > 55:
        raise RuntimeError("The restored pawn did not land at the verified stair approach")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {"pawn": pawn, "position": restored["location"]}
