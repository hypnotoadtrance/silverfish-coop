"""Run one fenced stock Old Pipe animation in a staged headless co-op."""

import time

from . import native_authored_pickup, party
from .native_gold import valid_guid
from .travel import CROSSROADS


EQUIP = "native-guest-melee-canary-equip"
CANARY = "native-guest-melee-canary"
EQUIP_KEY = "native_guest_melee_equip"
CANARY_KEY = "native_guest_melee_canary"


def parse(fields: list[str]) -> tuple[str, dict] | None:
    if not fields:
        return None
    if fields[0] == "native-guest-melee-equip" and len(fields) == 6 and fields[4] in ("0", "1"):
        return EQUIP_KEY, dict(zip(("map", "pawn", "guid"), fields[1:4]), ok=fields[4] == "1", status=fields[5])
    if fields[0] == CANARY and len(fields) == 12 and fields[10] in ("0", "1") and fields[11] in ("0", "1"):
        return CANARY_KEY, dict(zip(("map", "pawn", "guid"), fields[1:4]), serial=int(fields[4]), status=fields[5],
                                presses=int(fields[6]), releases=int(fields[7]), contacts=int(fields[8]),
                                qualified=int(fields[9]), settled=fields[10] == "1", passive_free=fields[11] == "1")
    return None


async def perform(host, bridge, action: str, character: str, guid: str, operation_id: str) -> dict:
    if (not host.native_guest or not host.fixtures or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != CROSSROADS or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_guest_melee_fixture_unavailable")
    if (action not in (EQUIP, CANARY) or not isinstance(character, str) or not valid_guid(guid) or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("native_guest_melee_exact_guid_required")
    fingerprint = [action, guid, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    owner = host.native_recipients().get(character) or {}
    pawn = owner.get("pawn") if owner.get("connected") else None
    if (not pawn or host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_guest_melee_exact_owner_required")
    claimed = host.party.get(native_authored_pickup.STATE_KEY, {}).get(native_authored_pickup.OLD_PIPE, {})
    if (claimed.get("owner"), claimed.get("guid")) != (character, guid):
        raise party.RuleError("native_guest_melee_exact_authored_claim_required")

    def exact(snapshot: dict, key: str) -> dict:
        proof = snapshot.get(key, {})
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid")) == (
                "hosting", CROSSROADS, CROSSROADS, pawn, guid):
            return proof
        return {}

    key = EQUIP_KEY if action == EQUIP else CANARY_KEY
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(action if action == EQUIP else CANARY + "-start", pawn, guid, timeout=5)
        except RuntimeError as error:
            if str(error).startswith("native_guest_melee_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = exact(snapshot, key)
        if not proof:
            raise RuntimeError("The Old Pipe receipt does not match the exact guest and GUID")
        if action == EQUIP:
            if proof.get("ok") is not True:
                status = proof.get("status", "")
                if not status or status.startswith("mutation_unverified_"):
                    raise RuntimeError(status or "The stock Old Pipe equip could not be verified")
                host.native_checkpoint_unsafe = False
                raise party.RuleError(status)
            if proof.get("status") != "old_pipe_equipped":
                raise RuntimeError("The exact stock Old Pipe was not equipped")
        else:
            serial = proof.get("serial")
            if type(serial) is not int or serial < 1:
                raise RuntimeError("The stock Old Pipe animation did not report a bound serial")
            if proof.get("status") == "pending":
                def terminal(frame):
                    current = exact(frame, key)
                    return current.get("serial") == serial and current.get("status") != "pending"

                snapshot = await bridge.wait_for(terminal, timeout=5)
                proof = exact(snapshot, key)
            if (proof.get("status"), proof.get("presses"), proof.get("releases"), proof.get("contacts"),
                    proof.get("qualified"), proof.get("settled"), proof.get("passive_free")) != (
                    "surface_verified", 1, 1, 1, 1, True, True):
                raise RuntimeError(proof.get("status") or "The fenced stock animation did not settle")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {key: proof}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError, TimeoutError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
