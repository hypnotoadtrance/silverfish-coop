"""Verify one stock host-deployed LandMine in an isolated campaign."""

import math
import time

from . import party
from .native_gold import valid_guid
from .travel import OBSERVATORY


KEY = "native_host_landmine"
PREPARE = "native-host-landmine-prepare-fixture"
DEPLOY = "native-host-landmine-deploy-fixture"
INSPECT = "native-host-landmine-inspect-fixture"
ACTIONS = (PREPARE, DEPLOY, INSPECT)


def parse(fields: list[str]) -> dict | None:
    if len(fields) != 17 or fields[0] != "native-host-landmine" or fields[13] not in ("0", "1"):
        return None
    location = None
    if fields[14:] != ["-", "-", "-"]:
        location = list(map(float, fields[14:]))
        if not all(math.isfinite(value) and abs(value) < 1e8 for value in location):
            return None
    return dict(zip(("map", "pawn", "state", "guid", "actor", "projectile"), fields[1:7]),
                **{key: None if value == "-" else int(value) for key, value in zip(
                    ("presses", "releases", "actors", "saved", "remaining", "guest_remaining"), fields[7:13])},
                same_transform=fields[13] == "1", position=location)


async def perform(host, bridge, action: str, target: str | None, operation_id: str) -> dict:
    if (not host.native_guest or not host.native_zones or not host.fixtures or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != OBSERVATORY
            or time.monotonic() - host.last_seen > 3 or host.party["wipe"] is not None):
        raise party.RuleError("stock_host_landmine_fixture_unavailable")
    pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    peers = host.native_recipients()
    if (not pawn or len(peers) != 1 or not all(seat.get("connected") for seat in peers.values())
            or any(host.party["players"].get(member, {}).get("downed") for member in ("host", *peers))):
        raise party.RuleError("stock_host_landmine_exact_party_required")
    if (action not in ACTIONS or (action == DEPLOY and not valid_guid(target))
            or (action != DEPLOY and target is not None) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("stock_host_landmine_fixture_arguments")

    def frame(snapshot, state=None):
        row = snapshot.get(KEY, {})
        if (snapshot.get("state"), snapshot.get("map"), row.get("map"), row.get("pawn")) != (
                "hosting", OBSERVATORY, OBSERVATORY, pawn) or (state and row.get("state") != state):
            return {}
        if action == DEPLOY and row.get("guid") != target:
            return {}
        return row

    if action == INSPECT:
        snapshot = await bridge.command(INSPECT, pawn, timeout=5)
        report = frame(snapshot, "inspected")
        if not report:
            raise RuntimeError("The stock LandMine census did not match the canonical host")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return {KEY: report}

    fingerprint = [action, target, None]
    for receipt in host.party["receipts"].get("host", []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if action == DEPLOY and not any(r["request"][0] == PREPARE and r["result"].get(KEY, {}).get("guid") == target
                                     for r in host.party["receipts"].get("host", [])):
        raise party.RuleError("stock_host_landmine_exact_prepared_item_required")

    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            first_action = PREPARE if action == PREPARE else "native-host-landmine-press-fixture"
            snapshot = await bridge.command(first_action, pawn, timeout=3)
        except RuntimeError as error:
            if str(error).startswith("stock_host_landmine_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if action == PREPARE:
            report = frame(snapshot, "equipped")
            if not report or not valid_guid(report["guid"]) or (
                    report["remaining"], report["guest_remaining"], report["actors"], report["saved"]) != (1, 0, 0, 0):
                raise RuntimeError("The stock LandMine seed and held row did not verify")
        else:
            proof = frame(snapshot)
            if proof.get("state") not in ("pressed", "primed") or proof.get("presses") != 1:
                raise RuntimeError("The stock LandMine primary press did not verify")
            if proof["state"] != "primed":
                await bridge.wait_for(lambda packet: bool(frame(packet, "primed")), timeout=1.75)
            snapshot = await bridge.command("native-host-landmine-release-fixture", pawn, timeout=3)
            release = frame(snapshot)
            if release.get("state") not in ("released", "placed") or release.get("releases") != 1 or release.get("projectile") == "-":
                raise RuntimeError("The stock LandMine primary release and exact projectile did not verify")
            if release["state"] != "placed":
                await bridge.wait_for(lambda packet: bool(frame(packet, "placed")), timeout=6)
            snapshot = await bridge.command("native-host-landmine-persist-fixture", pawn, timeout=5)
            report = frame(snapshot, "persisted")
            if not report or (report["presses"], report["releases"], report["actors"], report["saved"],
                              report["remaining"], report["guest_remaining"], report["same_transform"]) != (1, 1, 1, 1, 0, 0, True):
                raise RuntimeError("The canonical LandMine deployment and typed world frame did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {KEY: report}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault("host", [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(peers))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
