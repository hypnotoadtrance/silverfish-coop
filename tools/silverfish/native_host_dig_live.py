"""Commit exact stock Missing Contact package custody with the native host save."""

import asyncio
import math
import re
import time

from . import native_host_dig as stock, party
from .native_gold import valid_guid
from .travel import CROSSROADS


TAG = "native-host-dig-live"
KEY = "native_host_dig_live"
DIAGNOSTIC = TAG + "-diagnostic"
DIAGNOSTIC_KEY = KEY + "_diagnostic"
CUSTODY = "native_host_dig_custody"
HEARTBEAT = TAG + "-heartbeat"
WAITING = "stock_live_dig_checkpoint_pending"
_STATES = {"ready", "contact", "unclaimed", "escrowed", "collecting", "collected", "owned", "failed"}
_FRAME_SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
_FAULT = re.compile(r"stock_[a-z0-9_]{1,80}\Z")
_STATUS = re.compile(r"[a-z][a-z0-9_]{0,80}|-")
_CUSTODY_FIELDS = {"state", "map", "source", "flag", "flag_value", "spade_guid", "contact", "material", "package_guid"}


def _valid_contact(contact, material) -> bool:
    return (type(contact) is list and len(contact) == 3 and material in stock._MATERIALS
            and all(type(axis) in (int, float) and math.isfinite(axis) and abs(axis) <= 10_000_000 for axis in contact)
            and math.dist(contact, stock.SITE) <= 45.1)


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) != 16:
        raise ValueError("Invalid live stock dig report size")
    tag, reported, pawn, state, serial, spade, actor, package, x, y, z, material, incidental, escrow, slot, fault = fields
    contact = None if x == y == z == "-" else list(map(float, (x, y, z)))
    if (tag != TAG or (phase, area, reported) != ("hosting", CROSSROADS, CROSSROADS)
            or len(pawn) > 360 or not stock._PAWN.fullmatch(pawn) or state not in _STATES
            or not re.fullmatch(r"0|[1-9][0-9]{0,15}", serial) or int(serial) >= 2**53
            or (state == "ready") != (serial == "0")
            or spade != "-" and not valid_guid(spade) or state not in ("ready", "failed") and spade == "-"
            or actor != "-" and (len(actor) > 360 or not stock._PACKAGE.fullmatch(actor))
            or package != "-" and not valid_guid(package) or escrow != "-" and not valid_guid(escrow)
            or contact is None and material != "-" or contact is not None and not _valid_contact(contact, material)
            or incidental not in ("0", "1") or (state == "failed") != bool(_FAULT.fullmatch(fault))
            or state != "failed" and fault != "-"
            or slot != "-" and (escrow == "-" or not _FRAME_SLOT.fullmatch(slot))):
        raise ValueError("Invalid live stock dig report")
    if (state == "ready" and (spade, actor, package, material, incidental, escrow, slot) != ("-", "-", "-", "-", "0", "-", "-")
            or state in ("unclaimed", "escrowed", "collecting", "collected", "owned") and (actor == "-" or contact is None)
            or state in ("collected", "owned") and package == "-"
            or state == "escrowed" and escrow == "-"):
        raise ValueError("Unqualified live stock dig custody")
    return dict(map=area, pawn=pawn, state=state, serial=int(serial), spade_guid=None if spade == "-" else spade,
                actor=None if actor == "-" else actor, package_guid=None if package == "-" else package,
                contact=contact, material=None if material == "-" else material, incidental=int(incidental),
                escrow_guid=None if escrow == "-" else escrow, slot=None if slot == "-" else slot,
                fault=None if fault == "-" else fault)


def parse_diagnostic(fields: list[str], phase: str, area: str) -> dict | None:
    if (len(fields) != 8 or (phase, area, fields[1]) != ("hosting", CROSSROADS, CROSSROADS)
            or (fields[2] != "-" and (len(fields[2]) > 360 or not stock._PAWN.fullmatch(fields[2])))
            or any(not _STATUS.fullmatch(value) for value in fields[3:])):
        return None
    return dict(zip(("map", "pawn", "context", "heartbeat", "quest", "arm", "read"), fields[1:]))


def escrow_frames(data: dict) -> list[dict]:
    custody = data.get(CUSTODY)
    if custody is None:
        return []
    if type(custody) is not dict or custody.get("state") not in ("escrowed", "owned"):
        raise ValueError("Invalid stock sealed package custody")
    state = custody["state"]
    fields = _CUSTODY_FIELDS | ({"escrow"} if state == "escrowed" else set())
    if (set(custody) != fields or (custody["map"], custody["source"], custody["flag"]) != (CROSSROADS, stock.SOURCE, stock.FLAG)
            or type(custody["flag_value"]) is not int or custody["flag_value"] != 1
            or not valid_guid(custody["spade_guid"]) or not valid_guid(custody["package_guid"])
            or not isinstance(custody["material"], str) or not _valid_contact(custody["contact"], custody["material"])):
        raise ValueError("Invalid stock sealed package identity")
    if state == "owned":
        return []
    frame = custody["escrow"]
    if type(frame) is not dict or frame.get("map") != CROSSROADS or not isinstance(frame.get("slot"), str) or not _FRAME_SLOT.fullmatch(frame["slot"]):
        raise ValueError("Invalid stock sealed package escrow frame")
    return [frame]


def _report(host, expected_pawn=None) -> dict:
    game = host.last_game
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    report = game.get(KEY, {})
    return (report if (game.get("state"), game.get("map"), report.get("map"), report.get("pawn")) ==
            ("hosting", CROSSROADS, CROSSROADS, pawn) and (expected_pawn is None or pawn == expected_pawn) else {})


def _matches(prior, report, guid_field):
    return all(prior[key] == report.get(key) for key in ("spade_guid", "contact", "material")) and (
        prior["package_guid"] == report.get(guid_field))


async def _command(host, bridge, verb, *arguments):
    host.last_game = await bridge.command(f"{TAG}-{verb}", *arguments, timeout=6)
    host.last_seen = time.monotonic()


async def _acknowledge(host, bridge, report, custody):
    state, guid, pawn = custody["state"], custody["package_guid"], report["pawn"]
    await _command(host, bridge, "ack", pawn, str(report["serial"]), state, guid)
    actual = _report(host, pawn)
    guid_field = "package_guid" if state == "owned" else "escrow_guid"
    running = {"escrowed", "collecting", "collected"} if state == "escrowed" else {"owned"}
    if (actual.get("serial") != report["serial"] or actual.get("state") not in running
            or not _matches(custody, actual, guid_field)):
        raise RuntimeError("The stock sealed package did not acknowledge its paired checkpoint")
    return actual["state"] != state


async def _commit(host, bridge, report, state, guid, frame=None) -> bool:
    pivot = dict(state=state, map=CROSSROADS, source=stock.SOURCE, flag=stock.FLAG, flag_value=1,
                 spade_guid=report["spade_guid"], contact=report["contact"], material=report["material"], package_guid=guid)
    if frame is not None:
        pivot["escrow"] = frame
    candidate = party.load_party(host.party)
    candidate[CUSTODY] = pivot
    token = f"dig@{report['serial']}@{state}@{guid}"
    try:
        revision = await host.checkpoint_native(bridge, candidate, dig_token=token)
    except RuntimeError as error:
        if str(error) != WAITING:
            raise
        # The next fresh report may move a loose package into the canonical backpack.
        return True
    host.party = candidate
    host.control.event("native_host_dig_custody_committed", revision=revision, state=state, map=CROSSROADS,
                       package_guid=guid, source=stock.SOURCE, serial=report["serial"])
    return await _acknowledge(host, bridge, report, pivot)


async def settle(host, bridge) -> bool:
    """Return whether host operations must wait for the exact stock transition."""
    if not host.native_host_dig_live or host.last_game.get("map") != CROSSROADS:
        return False
    report = _report(host)
    state = report.get("state")
    prior = host.party.get(CUSTODY)
    if state is None:
        return bool(prior and prior["state"] == "escrowed")
    if state == "failed":
        raise RuntimeError(report["fault"])
    if state in ("contact", "collecting"):
        return True
    if state == "ready":
        if not prior or prior["state"] == "owned":
            return False
        pawn = report["pawn"]
        await _command(host, bridge, "restore", pawn, prior["escrow"]["slot"], prior["package_guid"], prior["spade_guid"],
                       *(str(axis) for axis in prior["contact"]), prior["material"])
        actual = _report(host, pawn)
        if (actual.get("state") != "escrowed" or not actual.get("actor") or not _matches(prior, actual, "escrow_guid")):
            raise RuntimeError("The exact sealed package could not be restored to its stock dig site")
        host.control.event("native_host_dig_escrow_restored", map=CROSSROADS, actor=actual["actor"],
                           package_guid=prior["package_guid"], serial=actual["serial"])
        return False
    if state in ("escrowed", "owned"):
        if not prior or prior["state"] != state or not _matches(prior, report, "escrow_guid" if state == "escrowed" else "package_guid"):
            raise RuntimeError("Live stock dig custody does not match its committed generation")
        return False
    if state == "unclaimed":
        if prior:
            if prior["state"] != "escrowed" or not _matches(prior, report, "escrow_guid"):
                raise RuntimeError("A second live sealed package conflicts with the committed custody")
            return await _acknowledge(host, bridge, report, prior)
        from . import native_host_save

        slot, pawn = native_host_save.new_slot(), report["pawn"]
        try:
            await _command(host, bridge, "escrow", pawn, str(report["serial"]), slot)
        except RuntimeError as error:
            if str(error) != "stock_live_dig_unclaimed_escrow_unavailable":
                raise
            snapshot = bridge.snapshot()
            if snapshot is None:
                raise
            host.last_game, host.last_seen = snapshot, time.monotonic()
            current = _report(host, pawn)
            if current.get("serial") == report["serial"] and current.get("state") in ("collecting", "collected"):
                return True
            raise
        actual = _report(host, pawn)
        if actual.get("serial") != report["serial"] or actual.get("actor") != report["actor"]:
            raise RuntimeError("The exact loose sealed package changed during escrow capture")
        if actual.get("state") in ("collecting", "collected"):
            return True
        if actual.get("state") != "unclaimed" or actual.get("slot") != slot or not valid_guid(actual.get("escrow_guid")):
            raise RuntimeError("The stock sealed package escrow did not verify")
        frame = await asyncio.to_thread(native_host_save.record, host.native_profile, slot, CROSSROADS, flush=True)
        return await _commit(host, bridge, actual, "escrowed", actual["escrow_guid"], frame)
    if prior and prior["state"] == "owned":
        if not _matches(prior, report, "package_guid"):
            raise RuntimeError("The exact owned stock package changed before acknowledgment")
        return await _acknowledge(host, bridge, report, prior)
    if prior and not _matches(prior, report, "escrow_guid"):
        raise RuntimeError("The collected stock package does not match the previous escrow")
    return await _commit(host, bridge, report, "owned", report["package_guid"])
