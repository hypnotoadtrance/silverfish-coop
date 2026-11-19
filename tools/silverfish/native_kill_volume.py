"""Validate recovery from the authored CrossRoads KillVolume."""

import math
import time

from . import party
from .travel import CROSSROADS


KEY = "native_kill_volume"
RECOVERED = KEY + "_recovered"
FIXTURE = "native-kill-volume-fall-fixture"
PENDING = "stock_kill_volume_recovery_in_progress"
VOLUME = f"KillVolume_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.KillVolume_BP_C_0"
PAWN = f"Player_FP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_"
FAULTS = {"ambiguous_authority_pawn", "crossing_owner_unavailable", "fixture_drop_rollback_unverified",
          "fixture_stock_crossing_unobserved", "recovered_downed_projection_unverified", "recovery_bridge_unavailable",
          "safe_floor_unavailable", "stock_volume_geometry_unavailable", "unqualified_crossing", "unsafe_recovery_readback",
          "unverified_downed_event"}


def _pawn_name(value):
    return isinstance(value, str) and value.startswith(PAWN) and value[len(PAWN):].isascii() and value[len(PAWN):].isdigit()


def parse_state(fields, phase, area):
    if len(fields) != 7 or (area, *fields[1:3]) != (CROSSROADS, CROSSROADS, VOLUME) or phase not in ("hosting", "connected"):
        raise ValueError("Invalid CrossRoads KillVolume state scope")
    state, overlaps, reloads, fault = fields[3:7]
    if (state not in ("arming", "armed", "pending", "fault") or phase != "hosting" and state in ("pending", "fault")
            or (fault not in FAULTS if state == "fault" else fault != "-") or not all(
                field.isascii() and field.isdigit() and len(field) < 13 for field in (overlaps, reloads))):
        raise ValueError("Invalid CrossRoads KillVolume state")
    return {"map": area, "volume": VOLUME, "state": state, "overlaps_vetoed": int(overlaps),
            "reloads_vetoed": int(reloads), "fault": None if fault == "-" else fault}


def parse_recovered(fields, phase, report):
    if (len(fields) != 7 or phase != "hosting" or not report or report["state"] == "arming" or not _pawn_name(fields[1])
            or not fields[2].isascii() or not fields[2].isdigit() or not 0 < len(fields[2]) < 13
            or fields[3] not in ("teammate", "last-ground", "player-start")):
        raise ValueError("Invalid CrossRoads KillVolume recovery scope")
    location = [float(component) for component in fields[4:7]]
    if int(fields[2]) <= 0 or not all(math.isfinite(value) and abs(value) < 1e8 for value in location) or location[2] <= -22074:
        raise ValueError("Invalid CrossRoads KillVolume recovery position")
    return {"pawn": fields[1], "sequence": int(fields[2]), "placement": fields[3], "location": location}


def pending(game):
    return game.get(KEY, {}).get("state") == "pending"


def fence(host):
    report = host.last_game.get(KEY, {})
    if report.get("state") == "fault":
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError(f"stock_kill_volume_{report['fault']}")


async def fixture(host, bridge, target):
    report = host.last_game.get(KEY, {})
    role = target or "guest"
    character = host.roles().get(role)
    if (not host.native_guest or not host.expanded_zones or host.stopping or host.transitioning
            or host.last_game.get("map") != CROSSROADS or host.last_game.get("state") != "hosting"
            or report.get("state") != "armed" or time.monotonic() - host.last_seen >= 3 or not character):
        raise party.RuleError("stock_kill_volume_fixture_unavailable")
    if character == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        recipient = host.native_recipients().get(character, {})
        pawn = recipient.get("pawn") if recipient.get("connected") else None
    if not _pawn_name(pawn) or host.party["players"].get(character, {}).get("downed", True):
        raise party.RuleError("stock_kill_volume_fixture_standing_pawn_required")
    snapshot = await bridge.command(FIXTURE, pawn, timeout=5)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    fence(host)
    if not pending(snapshot):
        raise RuntimeError("The exact KillVolume fixture did not verify an outside-to-inside drop")
    return {"pawn": pawn, "volume": VOLUME, "state": "falling"}
