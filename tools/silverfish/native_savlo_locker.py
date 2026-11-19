"""Observe the exact stock secure locker covering the Trench Hall Pass keycard."""

import re
import time


TRENCH = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
ACTOR = f"LootLocker_Secure_BP_C {TRENCH}.Trench_01_P:PersistentLevel.LootLocker_Secure_BP_C_16"
SOURCE = "trench-savlo-keycard"
TAG = "native-savlo-locker"
KEY = "native_savlo_locker"
FIXTURE = f"{TAG}-stock-fixture"
FRAME = f"{KEY}_frame"
_PAWN = re.compile(re.escape(f"Player_FP_C {TRENCH}.Trench_01_P:PersistentLevel.Player_FP_C_") + r"[0-9]+")
_EPOCH = re.compile(r"[0-9a-f]{32}")
_PHASE_STATES = {"hosting": {"locked", "unlocked", "opening", "opened"},
                 "connected": {"locked", "unlocked", "stock_opened", "projecting", "projected"}}


def valid_epoch(value) -> bool:
    return isinstance(value, str) and value != "0" * 32 and _EPOCH.fullmatch(value) is not None


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) != 7:
        raise ValueError("Invalid Trench secure locker report size")
    tag, state, reported_area, actor, pawn, epoch, locked = fields
    projected = state in {"projecting", "projected"}
    if (tag != TAG or area != TRENCH or reported_area != area or actor != ACTOR or _PAWN.fullmatch(pawn) is None
            or state not in _PHASE_STATES.get(phase, ()) or locked not in {"0", "1"}
            or (state == "locked" and locked != "1") or (state == "unlocked" and locked != "0")
            or (not valid_epoch(epoch) if projected else epoch != "-")):
        raise ValueError("Invalid Trench secure locker scope")
    return {"state": state, "map": area, "actor": actor, "pawn": pawn, "locked": locked == "1",
            "epoch": epoch if projected else None}


def observed(game: dict, state: str) -> dict:
    if game.get("map") != TRENCH:
        return {}
    report = game.get(KEY, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if ((report.get("map"), report.get("actor"), report.get("state"), report.get("pawn")) == (TRENCH, ACTOR, state, pawn)
            and isinstance(pawn, str) and _PAWN.fullmatch(pawn)):
        return report
    return {}


def frame(host) -> dict:
    game = host.last_game
    if (host.native_zones and not host.stopping and not host.transitioning
            and game.get("state") == "hosting" and observed(game, "opened") and valid_epoch(host.zone_epoch)
            and time.monotonic() - host.last_seen < 2.5):
        return {"map": TRENCH, "actor": ACTOR, "epoch": host.zone_epoch, "state": "opened"}
    return {}


async def press_fixture(host, bridge) -> dict:
    game = host.last_game
    current = observed(game, "locked") or observed(game, "unlocked")
    if (not host.fixtures or not host.native_zones or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or game.get("state") != "hosting" or time.monotonic() - host.last_seen >= 2.5 or not current
            or host.party.get("players", {}).get("host", {}).get("downed")):
        raise ValueError("The headless stock secure locker requires the exact closed actor and a standing host")
    result = await bridge.command(FIXTURE, current["pawn"], current["state"], timeout=6)
    if current["state"] == "locked":
        proof = observed(result, "unlocked")
        if not proof and observed(result, "locked"):
            raise ValueError("stock_savlo_locker_key_required")
    else:
        if result.get("state") != "hosting" or result.get("map") != TRENCH:
            raise RuntimeError("The stock secure locker opener left the exact authority world")
        result = await bridge.wait_for(lambda snapshot: snapshot.get("state") == "hosting"
                                       and observed(snapshot, "opened").get("pawn") == current["pawn"], 6)
        proof = observed(result, "opened")
    if result.get("state") != "hosting" or proof.get("pawn") != current["pawn"] or proof.get("locked") is not False:
        raise RuntimeError("The stock secure locker input did not verify the exact host and unlocked state")
    host.last_game, host.last_seen = result, time.monotonic()
    host.control.event("stock_savlo_locker_interaction", **proof)
    return {KEY: proof}


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    if (game.get("state") != "connected" or not valid_epoch(epoch) or client.needs_travel or client.server_transitioning
            or not client.host_session or time.monotonic() - client.gameplay_seen >= 2.5
            or client.gameplay.get(FRAME) != {"map": TRENCH, "actor": ACTOR, "epoch": epoch, "state": "opened"}):
        return
    current = observed(game, "locked") or observed(game, "unlocked")
    if not current:
        return
    result = await client.bridge.command(f"{TAG}-project", epoch, ACTOR, current["pawn"], timeout=3)
    proof = observed(result, "projecting") or observed(result, "projected")
    if (result.get("state") != "connected" or
            (proof.get("pawn"), proof.get("epoch"), proof.get("locked")) != (current["pawn"], epoch, current["locked"])):
        raise RuntimeError("The exact local secure locker projection did not verify")
    client.last_game = result
