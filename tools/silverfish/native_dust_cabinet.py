"""Exact RailYard cabinet proof for the authored Dust Filter pickup."""

import re
import time

from .travel import RAIL_YARD


ACTOR = f"LootCabnet_Small_BP_C {RAIL_YARD}.RailYard_P:PersistentLevel.LootCabnet_Small_BP_C_36"
KEY = "native_dust_cabinet"
FIXTURE = "native-dust-cabinet-stock-fixture"
PROJECT = "native-dust-cabinet-project"
FRAME_KEY = "native_dust_cabinet_frame"
_EPOCH = re.compile(r"[0-9a-f]{32}")
_PAWN = re.compile(re.escape(f"Player_FP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Player_FP_C_") + r"[0-9]+")
_STATES = {"hosting": {"closed", "opened"}, "connected": {"closed", "projecting", "projected"}}


def valid_epoch(epoch) -> bool:
    return isinstance(epoch, str) and epoch != "0" * 32 and _EPOCH.fullmatch(epoch) is not None


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) not in (6, 7):
        raise ValueError("Invalid stock Dust Filter cabinet report size")
    tag, state, report_map, actor, pawn, epoch = fields[:6]
    doors = fields[6] if len(fields) == 7 else None
    projected = state in ("projecting", "projected")
    if (tag != "native-dust-cabinet" or report_map != area or area != RAIL_YARD or actor != ACTOR
            or len(pawn) > 512 or _PAWN.fullmatch(pawn) is None or state not in _STATES.get(phase, ())
            or (not valid_epoch(epoch) if projected else epoch != "-")
            or (doors is not None and doors not in (("closed",) if state == "closed" else ("open", "unverified")))):
        raise ValueError("Invalid stock Dust Filter cabinet scope")
    return {"state": state, "map": area, "actor": actor, "pawn": pawn, "epoch": epoch if projected else None,
            "doors": doors}


def observed(game: dict, state: str) -> dict:
    report = game.get(KEY, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if ((game.get("map"), report.get("map"), report.get("actor"), report.get("state"), report.get("pawn")) ==
            (RAIL_YARD, RAIL_YARD, ACTOR, state, pawn) and isinstance(pawn, str) and _PAWN.fullmatch(pawn)):
        return report
    return {}


def frame(host) -> dict:
    if (host.fixtures and host.native_zones and not host.stopping and not host.transitioning
            and host.last_game.get("state") == "hosting" and observed(host.last_game, "opened")
            and valid_epoch(host.zone_epoch) and time.monotonic() - host.last_seen < 2.5):
        return {"map": RAIL_YARD, "actor": ACTOR, "epoch": host.zone_epoch, "state": "opened"}
    return {}


async def open_fixture(host, bridge) -> dict:
    game = host.last_game
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if (not host.fixtures or not host.native_zones or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or game.get("map") != RAIL_YARD
            or not isinstance(pawn, str) or _PAWN.fullmatch(pawn) is None or time.monotonic() - host.last_seen >= 2.5):
        raise ValueError("The headless host Dust Filter cabinet fixture is unavailable")
    if host.party.get("players", {}).get("host", {}).get("downed") or observed(game, "opened"):
        raise ValueError("The stock Dust Filter cabinet requires a standing host and an unopened cabinet")
    await bridge.command(FIXTURE, pawn, timeout=5)
    snapshot = await bridge.wait_for(lambda s: s.get("state") == "hosting" and observed(s, "opened").get("pawn") == pawn, 5)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    proof = observed(snapshot, "opened")
    host.control.event("stock_dust_cabinet_opened", **proof)
    return {KEY: proof}


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    report = client.gameplay.get(FRAME_KEY)
    if (not valid_epoch(epoch) or client.gameplay.get("test_fixtures") is not True
            or client.needs_travel or client.server_transitioning or not client.host_session
            or time.monotonic() - client.gameplay_seen >= 2.5 or game.get("state") != "connected"
            or report != {"map": RAIL_YARD, "actor": ACTOR, "epoch": epoch, "state": "opened"}):
        return
    current = observed(game, "closed")
    if not current:
        return
    result = await client.bridge.command(PROJECT, epoch, ACTOR, current["pawn"], timeout=3)
    accepted = observed(result, "projecting") or observed(result, "projected")
    if result.get("state") != "connected" or (accepted.get("pawn"), accepted.get("epoch")) != (current["pawn"], epoch):
        raise RuntimeError("The quiet stock cabinet projection did not verify the exact local visit")
    client.last_game = result
