"""Observe one genuine stock lockpick attempt in an isolated Cistern run."""

import re
import time

from . import native_authored_pickup as pickups, party
from .native_gold import valid_guid
from .travel import CISTERN


TAG = "native-cistern-lockpick"
KEY = "native_cistern_lockpick"
FIXTURE = TAG + "-fixture"
OUTCOMES = {"unlocked", "retry", "spent"}
_PAWN = re.compile(re.escape(f"Player_FP_C {CISTERN}.Cistern_P:PersistentLevel.Player_FP_C_") + r"[0-9]+")


def parse(fields):
    if len(fields) != 7 or fields[:2] != [TAG, CISTERN]:
        raise ValueError("Invalid Cistern lockpick report")
    pawn, guid, state, before, after = fields[2:]
    if (len(pawn) > 512 or not _PAWN.fullmatch(pawn) or not valid_guid(guid)
            or state not in OUTCOMES | {"pending", "fault"} or before not in {"1", "2"}
            or after not in {"-", "0", "1", "2"}):
        raise ValueError("Invalid stock lockpick identity or state")
    before = int(before)
    after = None if after == "-" else int(after)
    if (state == "pending" and after is not None or state in OUTCOMES and after not in {before, before - 1}
            or state == "retry" and (not after or after == before) or state == "spent" and after != 0):
        raise ValueError("Invalid stock lockpick charge result")
    return {"map": CISTERN, "pawn": pawn, "guid": guid, "state": state,
            "charges_before": before, "charges_after": after}


async def perform(host, bridge, guid):
    game = host.last_game
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    proof = host.party.get(pickups.STATE_KEY, {}).get(pickups.CROSSROADS_LOCKPICK, {})
    if (not host.fixtures or not host.native_guest or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party.get("wipe") or not pawn or not _PAWN.fullmatch(pawn)
            or (game.get("state"), game.get("map")) != ("hosting", CISTERN)
            or not valid_guid(guid) or (proof.get("owner"), proof.get("guid")) != ("host", guid)):
        raise party.RuleError("The isolated Cistern lockpick requires the exact host and owned stock pick")

    required = frozenset(host.native_recipients())
    await host.checkpoint_native(bridge, require_guest_frames=required)
    host.native_checkpoint_unsafe = True

    def complete(frame):
        report = frame.get(KEY, {})
        return ((frame.get("state"), frame.get("map"), report.get("pawn"), report.get("guid"))
                == ("hosting", CISTERN, pawn, guid) and report.get("state") in OUTCOMES | {"fault"})

    try:
        try:
            snapshot = await bridge.command(FIXTURE, guid, timeout=6)
        except RuntimeError as error:
            if str(error).startswith("stock_cistern_lockpick_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if not complete(snapshot):
            snapshot = await bridge.wait_for(complete, timeout=8)
        report = snapshot[KEY]
        if report["state"] == "fault":
            raise RuntimeError("The stock Cistern lockpick attempt could not be verified")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = await host.checkpoint_native(bridge, require_guest_frames=required)
        host.native_checkpoint_unsafe = False
        host.control.event("stock_cistern_lockpick_attempt", revision=revision, **report)
        return {KEY: report, "revision": revision}
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
