"""Run isolated headless checks of the stock Core and console."""

import asyncio
import re
import time

from . import native_mindcore as mindcore, party
from .native_mosby_tape import _scope


INSTALL = "native-mindcore-install-fixture"
DECK_LOAD = "native-mindcore-deck-load-fixture"
APPROACH = "native-mindcore-approach-fixture"
CARRY = "native-mindcore-carry-fixture"
CARRY_TAG = "native-mindcore-carry"
CARRY_KEY = "native_mindcore_carry"
CARRY_DIAGNOSTIC = "native-mindcore-carry-diagnostic"
CARRY_DIAGNOSTIC_KEY = "native_mindcore_carry_diagnostic"
POSE_TAG = "native-mindcore-core-pose"
POSE_KEY = "native_mindcore_core_pose"
_CARRY_RESULT = {"grab": "held", "step": "carried", "release": "released"}
_MOVEMENT = re.compile(r"(?:0|[1-9][0-9]{0,5})\.[0-9]{1,3}")
_COORDINATE = re.compile(r"-?(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,6})?")


def parse_carry(fields: list[str]) -> dict:
    if len(fields) != 7:
        raise ValueError("Invalid stock Data Silo Core carry report")
    tag, area, pawn, core, state, pawn_movement, core_movement = fields
    if ((tag, area, core) != (CARRY_TAG, mindcore.MAP, mindcore.CORE)
            or not pawn.startswith(f"Player_FP_C {mindcore.LEVEL}") or len(pawn) > 512
            or state not in (*_CARRY_RESULT.values(), "carrying")
            or not all(_MOVEMENT.fullmatch(value) for value in (pawn_movement, core_movement))):
        raise ValueError("Invalid stock Data Silo Core carry report")
    return dict(map=area, pawn=pawn, core=core, state=state,
                pawn_movement_uu=float(pawn_movement), core_movement_uu=float(core_movement))


def parse_carry_diagnostic(fields: list[str]) -> dict:
    if (len(fields) != 4 or tuple(fields[:2]) != (CARRY_DIAGNOSTIC, mindcore.MAP)
            or fields[2] not in _CARRY_RESULT or not 1 <= len(fields[3]) <= 512 or not fields[3].isprintable()):
        raise ValueError("Invalid stock Data Silo Core diagnostic")
    return {"action": fields[2], "reason": fields[3]}


def parse_pose(fields: list[str], role: str) -> dict:
    if (len(fields) != 12 or tuple(fields[:4]) != (POSE_TAG, mindcore.MAP, role, mindcore.CORE)
            or not all(_COORDINATE.fullmatch(value) for value in fields[4:7])
            or not all(value in ("0", "1") for value in fields[7:11])
            or fields[11] not in ("0", "1", "2", "3")):
        raise ValueError("Invalid stock Data Silo Core pose report")
    x, y, z = map(float, fields[4:7])
    return dict(map=mindcore.MAP, role=role, core=mindcore.CORE, position=[x, y, z],
                simulating=fields[7] == "1", collision=fields[8] == "1", replicates=fields[9] == "1",
                replicate_movement=fields[10] == "1", local_role=int(fields[11]))

def _same_participants(host, owner, guest_pawn, host_pawn):
    exact = host.native_recipients()
    member = exact.get(owner, {})
    return (len(exact) == 1 and member.get("connected") is True and member.get("pawn") == guest_pawn
            and host.last_game.get("pawns", {}).get("local", {}).get("name") == host_pawn)


async def perform(host, bridge, action, target, variant=None):
    if not host.fixtures or action not in (INSTALL, APPROACH, CARRY, DECK_LOAD):
        raise party.RuleError("native_mindcore_fixture_refused_arguments")
    invalid_target = (not isinstance(target, str) or target not in _CARRY_RESULT) if action == CARRY else target is not None
    if action == DECK_LOAD and target in ("east-corridor", "north-landing"):
        invalid_target = False
    if invalid_target:
        raise party.RuleError("native_mindcore_fixture_refused_arguments")
    seats = host.native_recipients()
    if len(seats) != 1:
        raise party.RuleError("native_mindcore_fixture_refused_exact_pair")
    owner, = seats
    host_pawn = _scope(host, "host", mindcore.MAP)
    guest_pawn = _scope(host, owner, mindcore.MAP, guest_only=True)
    if not _same_participants(host, owner, guest_pawn, host_pawn):
        raise party.RuleError("native_mindcore_fixture_refused_exact_pair")
    if variant is not None and (action != APPROACH or type(variant) is not int or not 1 <= variant <= 12):
        raise party.RuleError("native_mindcore_fixture_refused_invalid_console_approach")
    if action == APPROACH:
        selector = () if variant is None else (str(variant),)
        snapshot = await bridge.command(APPROACH, guest_pawn, *selector, timeout=7)
        scene = snapshot.get(mindcore.SCENE)
        if not scene or snapshot.get("state") != "hosting" or snapshot.get("map") != mindcore.MAP:
            raise RuntimeError("The isolated console approach lost its exact stock world")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The isolated console approach lost the exact pair")
        return {mindcore.SCENE: scene, "pawn": guest_pawn}

    def dormant(snapshot):
        scene = snapshot.get(mindcore.SCENE, {})
        return (snapshot.get("state"), snapshot.get("map"), scene.get("status"), scene.get("installed")) == (
            "hosting", mindcore.MAP, 0, False)

    if not dormant(host.last_game) or mindcore.LEDGER in host.party:
        raise party.RuleError("native_mindcore_fixture_refused_console_used")
    if action == DECK_LOAD:
        location = () if target is None else (target,)
        snapshot = await bridge.command(action, host_pawn, *location, timeout=7)
        pose = snapshot.get(POSE_KEY, {})
        if not dormant(snapshot) or pose.get("core") != mindcore.CORE or not pose.get("simulating"):
            raise RuntimeError("The isolated lift load lost the exact loose stock Core")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The exact pair changed during the isolated lift load")
        return {POSE_KEY: pose, mindcore.SCENE: snapshot[mindcore.SCENE]}
    if action == CARRY:
        def expected(snapshot):
            report = snapshot.get(CARRY_KEY, {})
            diagnostic = snapshot.get(CARRY_DIAGNOSTIC_KEY, {})
            if dormant(snapshot) and diagnostic.get("action") == target:
                raise RuntimeError(diagnostic["reason"])
            return (dormant(snapshot) and report.get("pawn") == host_pawn
                    and report.get("state") == _CARRY_RESULT[target])

        snapshot = await bridge.command(CARRY, host_pawn, target, timeout=7)
        if not expected(snapshot):
            sequence = snapshot["sequence"]
            snapshot = await bridge.wait_for(lambda state: state["sequence"] > sequence and expected(state), timeout=9)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The exact pair changed during the stock Core carry")
        report = snapshot[CARRY_KEY]
        host.control.event("native_mindcore_stock_carry_fixture", **report)
        return {CARRY_KEY: report, mindcore.SCENE: snapshot[mindcore.SCENE]}

    host.transitioning = True
    try:
        await host.checkpoint_native(bridge, require_guest_frames=frozenset({owner}))
        if not dormant(host.last_game) or not _same_participants(host, owner, guest_pawn, host_pawn):
            raise party.RuleError("native_mindcore_fixture_refused_changed_scope")
        host.native_checkpoint_unsafe = True
        try:
            snapshot = await bridge.command(INSTALL, host_pawn, timeout=7)
        except RuntimeError as error:
            if str(error).startswith(("native_mindcore_fixture_refused_", "native_mindcore_refused_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if not mindcore._ready(snapshot, 1):
            snapshot = await bridge.wait_for(lambda game: mindcore._ready(game, 1), timeout=12)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The exact pair changed during the stock Core overlap")
        revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({owner}))
        host.native_checkpoint_unsafe = False
        scene = snapshot[mindcore.SCENE]
        host.control.event("native_mindcore_fixture_installed", revision=revision, pawn=host_pawn, **scene)
        return {mindcore.SCENE: scene, "pawn": host_pawn, "revision": revision}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
