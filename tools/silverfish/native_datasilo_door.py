"""Read back the authored Data Silo door and its stock sliding collision panel."""

import math
import time

from . import native_mindcore as core, party
from .native_mindcore_view import valid_pawn


TAG = "native-datasilo-door"
KEY = "native_datasilo_door"
FIXTURE = TAG + "-fixture"
STAGE_PAWN = TAG + "-stage-pawn"
ACTOR = f"Door_Metal_Sliding_01_BP_C {core.LEVEL}Door_Metal_Sliding_01_BP_C_1"
STATES = {"active", "guarded", "awaiting_replica", "replication_unverified", "guard_unverified"}
COLLISION_MODES = {"0", "1", "2", "3", "4", "5"}


def _position(fields):
    point = list(map(float, fields))
    if any(not math.isfinite(axis) or abs(axis) >= 1e7 for axis in point):
        raise ValueError("Invalid stock door position")
    return point


def parse(fields, role):
    if len(fields) != 22 or fields[:3] != [TAG, core.MAP, role] or fields[4] != ACTOR:
        raise ValueError("Invalid upper Data Silo door identity")
    state = fields[3]
    net_role, replicated, arrow, panel, knob, pose, locked, opened = fields[5:13]
    flags = {"0", "1"} if role == "host" else {"-"}
    if (state not in STATES or net_role not in {"0", "1", "2", "3"} or replicated not in {"0", "1"}
            or arrow not in {"0", "1"} or panel not in COLLISION_MODES or knob not in COLLISION_MODES
            or pose not in {"closed", "moving", "open"} or locked not in flags or opened not in flags):
        raise ValueError("Invalid upper Data Silo door state")
    relative, arrow_location, panel_location = (_position(fields[index:index + 3]) for index in (13, 16, 19))
    return {"actor": ACTOR, "role": role, "state": state, "net_role": int(net_role), "replicates": replicated == "1",
            "arrow_replicates": arrow == "1", "panel_collision": int(panel), "knob_collision": int(knob),
            "pose": pose, "locked": locked == "1" if role == "host" else None,
            "open": opened == "1" if role == "host" else None, "arrow_relative": relative,
            "arrow_location": arrow_location, "panel_location": panel_location}


def _authority(host):
    game = host.last_game
    door = game.get(KEY, {})
    if (not host.fixtures or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or (game.get("state"), game.get("map"), door.get("state")) != ("hosting", core.MAP, "active")):
        raise party.RuleError("The isolated upper Data Silo door fixture requires exact authority")
    return game, door


async def exercise_fixture(host, bridge, target):
    _, before = _authority(host)
    if target not in {"power", "button"} or before["pose"] == "moving":
        raise party.RuleError("Select a stock upper door input at a settled pose")
    frame = await bridge.command(FIXTURE, target, timeout=5)
    if (frame.get("map"), frame.get(KEY, {}).get("actor")) != (core.MAP, ACTOR):
        raise RuntimeError("The exact Data Silo door did not acknowledge the stock request")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {KEY: frame[KEY], "input": target, "source_pose": before["pose"]}


async def stage_pawn(host, bridge, target):
    game, door = _authority(host)
    if target not in {"host", "guest"} or door["pose"] not in {"closed", "open"}:
        raise party.RuleError("Select an exact upper door approach pawn")
    alias, binding = host.role_pawn(target)
    pawn = game.get("pawns", {}).get(alias, {}).get("name")
    if not alias or not valid_pawn(pawn):
        raise party.RuleError("The upper door approach lost its exact active pawn")
    # The fixture places only the pawn above the authored south catwalk floor.
    location = [43.0, 4800.0, 6735.0] if target == "host" else [0.0, 4880.0, 6735.0]
    frame = await bridge.restore(alias, {"location": location}, binding=binding)
    moved = frame.get("pawns", {}).get(alias, {})
    if ((frame.get("map"), moved.get("name")) != (core.MAP, pawn)
            or math.dist(moved.get("location", ()), location) > 65):
        raise RuntimeError("The isolated upper door approach did not reach the authored floor")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {"pawn": pawn, "position": moved["location"]}
