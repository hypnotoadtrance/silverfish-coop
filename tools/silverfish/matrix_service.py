"""Coordinate an exact reusable Regression Matrix charge and party transfer."""

import math
import time
import uuid

from . import native_matrix
from .native_gold import valid_guid
from .runtime import write_text


RESULTS = {
    "record": {"recorded"}, "inspect": {"present"}, "qualify": {"qualified"}, "charge": {"charged"},
    "restore-remainder": {"restored"}, "place": {"placed"}, "seed": {"seeded", "present"},
    "equip": {"equipped"}, "fuel": {"fueled", "present"},
    "probe": {"matrix_record_queued", "matrix_activate_queued"},
}


def _native_number(value: float) -> str:
    return format(value, ".17g")


def _native_eather(snapshot: dict) -> tuple[str, ...]:
    exact = native_matrix.stock_eather(snapshot)
    arguments = [_native_number(exact["remainder"])]
    for guid, canister in exact["canisters"].items():
        arguments.extend((guid, str(canister["ammo"]), str(canister["value"])))
    return tuple(arguments)


async def _command(bridge, game: dict, operation: str, guid: str, *arguments: str) -> tuple[dict, dict]:
    source = game.get("map")
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if game.get("state") != "hosting" or source not in native_matrix.MAPS or not pawn:
        raise RuntimeError("The authoritative Matrix host is unavailable")
    write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
    snapshot = await bridge.command(f"native-matrix-{operation}", source, pawn, guid, *arguments, timeout=6)
    proof = snapshot.get("native_matrix", {})
    if (snapshot.get("state") != "hosting" or snapshot.get("map") != source
            or proof.get("source") != source or proof.get("pawn") != pawn or proof.get("guid") != guid
            or proof.get("operation") != operation or proof.get("result") not in RESULTS[operation]):
        raise RuntimeError("The native Matrix command was not verified")
    return snapshot, proof


async def control_action(host, bridge, operation: str, request: dict) -> dict:
    guid = request.get("target")
    if not valid_guid(guid) or not host.matrix_supported:
        raise ValueError("A staged Matrix and an exact item GUID are required")
    arguments = ()
    if operation != "inspect" and not host.fixtures:
        raise ValueError("This Matrix action requires the isolated fixture host")
    if operation == "fuel":
        canister = request.get("item_guid")
        if not valid_guid(canister) or canister == guid:
            raise ValueError("Supply a distinct exact canister GUID")
        arguments = (canister,)
    elif operation == "probe":
        verb = request.get("entrance")
        if verb not in ("record", "activate"):
            raise ValueError("Choose a Matrix record or activate input")
        arguments = (verb,)
    snapshot, proof = await _command(bridge, host.last_game, operation, guid, *arguments)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_matrix": proof, "anchor": native_matrix.anchor(host.party, guid)}


async def place(bridge, game: dict, guid: str, anchor: dict) -> dict:
    arguments = tuple(_native_number(axis) for axis in anchor["position"] + anchor["rotation"])
    snapshot, proof = await _command(bridge, game, "place", guid, *arguments)
    pose = proof.get("anchor", {})
    if (not pose or snapshot["map"] != anchor["map"]
            or math.dist(pose["position"], anchor["position"]) > 250):
        raise RuntimeError("The stock Matrix anchor placement could not be verified")
    return snapshot


async def activate(host, bridge, transport, request: native_matrix.MatrixRequest) -> dict:
    if (not host.native_zones or host.native_profile is None or not host.matrix_supported
            or host.transitioning or host.stopping or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != request.source or request.source not in native_matrix.MAPS
            or host.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
        return {"ok": False, "error": "matrix_scope_unavailable"}
    if host.party["players"]["host"]["downed"]:
        return {"ok": False, "error": "actor_downed"}
    if host.item_travel_pending():
        return {"ok": False, "error": "item_travel_pending"}

    host.transitioning = True
    try:
        if request.action == "record":
            snapshot, proof = await _command(bridge, host.last_game, "record", request.guid)
            anchor = proof.get("anchor")
            if not anchor:
                raise RuntimeError("The native Matrix did not report its stock anchor transform")
            host.last_game, host.last_seen = snapshot, time.monotonic()
            candidate = native_matrix.record(host.party, request.guid, request.source,
                                             anchor["position"], anchor["rotation"])
            if candidate is not host.party:
                await host.checkpoint_native(bridge, candidate)
                host.party = candidate
            return {"ok": True, "anchor": native_matrix.anchor(host.party, request.guid)}

        anchor = native_matrix.anchor(host.party, request.guid)
        if not anchor:
            return {"ok": False, "error": "matrix_anchor_required"}
        destination = anchor["map"]
        if refusal := host.travel_refusal(destination, host.roles(), staying=destination == request.source):
            return refusal
        snapshot, proof = await _command(bridge, host.last_game, "qualify", request.guid)
        quote = proof.get("quote", {})
        before = proof.get("eather", {}).get("before")
        if (not before or quote.get("nominal_cost") != native_matrix.NOMINAL_COST
                or not 0 <= quote.get("charged", -1) <= native_matrix.NOMINAL_COST
                or quote.get("available", -1) < native_matrix.NOMINAL_COST
                or not 0 <= quote.get("after_remainder", -1) < 1):
            raise RuntimeError("The native Matrix did not verify a stock Eather quote")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if refusal := host.travel_refusal(destination, host.roles(), staying=destination == request.source):
            return refusal
        operation_id = uuid.uuid4().hex.upper()
        proposal = {"nominal_cost": native_matrix.NOMINAL_COST, "charged": quote["charged"], "before": before, "after": None}
        candidate = native_matrix.begin(host.party, operation_id, request.guid, request.source, anchor["revision"], proposal)
        await host.checkpoint_native(bridge, candidate)
        host.party = candidate
        return await resume(host, bridge, transport, recovery=False)
    finally:
        host.transitioning = False


async def resume(host, bridge, transport, *, recovery: bool) -> dict:
    trip = native_matrix.pending(host.party)
    if (not trip or not host.matrix_supported or host.native_profile is None
            or host.last_game.get("map") != trip["source"]):
        raise RuntimeError("The pending Matrix trip has no qualified stock host")
    guid, destination, operation_id = trip["guid"], trip["destination"], trip["id"]
    anchor = native_matrix.anchor(host.party, guid)
    host.native_checkpoint_unsafe = True
    try:
        if trip["phase"] == "planned":
            snapshot, proof = await _command(bridge, host.last_game, "charge", guid, *_native_eather(trip["debit"]["before"]))
            receipt = {**proof.get("charge", {}), **proof.get("eather", {})}
            candidate = native_matrix.commit_native_debit(host.party, operation_id, receipt)
            host.last_game, host.last_seen = snapshot, time.monotonic()
            await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            trip = native_matrix.pending(host.party)
        else:
            after = trip["debit"]["after"]
            snapshot, proof = await _command(bridge, host.last_game, "restore-remainder", guid, *_native_eather(after))
            if native_matrix.debit_status(host.party, operation_id, proof.get("eather", {}).get("current")) != "after":
                raise RuntimeError("The restored Matrix charge could not be verified")
            host.last_game, host.last_seen = snapshot, time.monotonic()

        if destination == trip["source"]:
            observed = await place(bridge, host.last_game, guid, anchor)
            await host.place_party_in_zone(bridge, host.roles(), observed)
            candidate = native_matrix.arrive(host.party, operation_id, destination)
            revision = await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            result = {"ok": True, "map": destination, "entrance": "Matrix", "in_zone": True,
                      "revision": revision, "travel_epoch": host.zone_epoch}
        else:
            result = await host.switch_zone(bridge, transport, destination, "Matrix", host.roles(), matrix_plan=trip)
        host.native_checkpoint_unsafe = False
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.control.event("native_matrix_recovered" if recovery else "native_matrix_completed",
                       id=operation_id, guid=guid, charged=trip["debit"]["charged"], **result)
    return result
