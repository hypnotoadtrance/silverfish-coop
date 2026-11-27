"""Use the stock shovel and preserve the Missing Contact package in the host save."""

import asyncio
import math
import re
import time
import uuid

from . import native_host_dig as stock, party
from .native_downed_markers import character_id, location
from .native_gold import valid_guid
from .travel import BLACK_MARKET, CROSSROADS


TAG = "native-host-dig-live"
KEY = "native_host_dig_live"
DIAGNOSTIC = TAG + "-diagnostic"
DIAGNOSTIC_KEY = KEY + "_diagnostic"
CUSTODY = "native_host_dig_custody"
HANDOFF = "native-missing-contact-handoff"
COLLECT = "native-missing-contact-collect"
PACKAGE_FRAME = "native_missing_contact_package"
GUEST_ACTION = "native-guest-dig"
GUEST_KIND = "spade"
GUEST_INTENT = "native_guest_spade_intent"
GUEST_RESULT = "native_guest_dig"
COMPACT_SCOPE = "native_guest_dig_local_scope"
HEARTBEAT = TAG + "-heartbeat"
WAITING = "stock_live_dig_checkpoint_pending"
_HANDOFF_MAPS = {BLACK_MARKET, CROSSROADS}
_HANDOFF_RANGE = 300
_HANDOFF_REFUSAL = "stock_missing_contact_handoff_"
_COLLECT_REFUSAL = "stock_missing_contact_collect_"
_STATES = {"ready", "refused", "contact", "unclaimed", "escrowed", "collecting", "collected", "owned", "failed"}
_FRAME_SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
_FAULT = re.compile(r"stock_[a-z0-9_]{1,80}\Z")
_STATUS = re.compile(r"[a-z][a-z0-9_]{0,80}|-")
_ANGLE = r"-?[0-9]{1,3}\.[0-9]{5}"
_CAMERA_AIM = re.compile(rf"{_ANGLE},{_ANGLE},{_ANGLE}")
_CUSTODY_FIELDS = {"state", "map", "source", "flag", "flag_value", "spade_guid", "contact", "material", "package_guid"}


def camera_aim(value):
    if not isinstance(value, str) or not _CAMERA_AIM.fullmatch(value):
        return None
    angles = tuple(map(float, value.split(",")))
    return angles if all(abs(angle) <= 360 for angle in angles) else None


def request_target(value, epoch):
    from . import native_guest_melee

    guid, separator, aim = value.partition("@") if isinstance(value, str) else (value, "", "")
    scoped = native_guest_melee.request_target(guid, epoch)
    if not separator:
        return scoped
    if camera_aim(aim) is None:
        raise ValueError("stock_guest_dig_camera_unqualified")
    return f"{scoped}@{aim}"


async def guest_dig(host, bridge, character, target, operation):
    from . import native_guest_melee

    if isinstance(target, str) and target.count("@") >= 2:
        identity, _, aim = target.rpartition("@")
        if camera_aim(aim) is None:
            raise party.RuleError("stock_guest_dig_camera_unqualified")
    else:
        identity, aim = target, None
    guid = native_guest_melee.current_guid(identity, host.zone_epoch)
    if not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation):
        raise party.RuleError("stock_guest_dig_operation_unqualified")
    game = host.last_game
    area = game.get("map")
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn") if owner.get("connected") else None
    player = host.party["players"].get(character, {})
    if (not host.native_host_dig_live or not host.native_guest_controls or not native_guest_melee.ordinary_map(area)
            or game.get("state") != "hosting" or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen > 3 or not pawn or player.get("downed") is not False
            or character in host.party["pending_spawns"] or host.party["wipe"] is not None):
        raise party.RuleError("stock_guest_dig_unavailable")
    fingerprint = [GUEST_ACTION, target, None]
    receipts = host.party["receipts"].setdefault(character, [])
    for receipt in receipts:
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    equipped = game.get("native_guest_equipped_weapons", {}).get(pawn, {})
    if (equipped.get("kind"), equipped.get("guid")) != (GUEST_KIND, guid):
        raise party.RuleError("stock_guest_dig_equipment_changed")
    options = {"update_status": False} if getattr(bridge, "local_bridge", None) is not None else {}
    try:
        args = (aim,) if aim is not None else ()
        snapshot = await bridge.command(GUEST_ACTION, pawn, guid, *args, timeout=3, **options)
    except RuntimeError as error:
        if str(error).startswith("stock_guest_dig_"):
            raise party.RuleError(str(error)) from error
        raise
    if (snapshot.get("map"), snapshot.get("state"), snapshot.get("ack", {}).get("message")) != (
            area, "hosting", "stock_guest_dig_dispatched"):
        raise RuntimeError("The stock guest shovel dispatch did not verify")
    if COMPACT_SCOPE in snapshot:
        if snapshot[COMPACT_SCOPE] != pawn:
            raise RuntimeError("The stock guest shovel reply belonged to another pawn")
    else:
        host.last_game = snapshot
    host.last_seen = time.monotonic()
    result = {GUEST_RESULT: {"map": area, "pawn": pawn, "guid": guid, "state": "dispatched"}}
    if aim is not None:
        result[GUEST_RESULT]["aim_source"] = "guest_camera"
    receipts.append({"id": operation, "request": fingerprint, "result": result})
    del receipts[:-party.RECENT_ACTIONS]
    return result


def _valid_contact(contact, material) -> bool:
    return (type(contact) is list and len(contact) == 3 and material in stock._MATERIALS
            and all(type(axis) in (int, float) and math.isfinite(axis) and abs(axis) <= 10_000_000 for axis in contact)
            and math.dist(contact, stock.SITE) <= 45.1)


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) not in (16, 17):
        raise ValueError("Invalid live stock dig report size")
    tag, reported, pawn, state, serial, spade, actor, package, x, y, z, material, incidental, escrow, slot, fault = fields[:16]
    collector = fields[16] if len(fields) == 17 else "-"
    contact = None if x == y == z == "-" else list(map(float, (x, y, z)))
    pre_contact = state in ("ready", "refused")
    if (tag != TAG or (phase, area, reported) != ("hosting", CROSSROADS, CROSSROADS)
            or len(pawn) > 360 or not stock._PAWN.fullmatch(pawn) or state not in _STATES
            or not re.fullmatch(r"0|[1-9][0-9]{0,15}", serial) or int(serial) >= 2**53
            or pre_contact != (serial == "0")
            or spade != "-" and not valid_guid(spade) or not pre_contact and state != "failed" and spade == "-"
            or actor != "-" and (len(actor) > 360 or not stock._PACKAGE.fullmatch(actor))
            or package != "-" and not valid_guid(package) or escrow != "-" and not valid_guid(escrow)
            or contact is None and material != "-" or contact is not None and not _valid_contact(contact, material)
            or incidental not in ("0", "1") or (state in ("refused", "failed")) != bool(_FAULT.fullmatch(fault))
            or state not in ("refused", "failed") and fault != "-"
            or slot != "-" and (escrow == "-" or not _FRAME_SLOT.fullmatch(slot))
            or collector != "-" and (len(collector) > 360 or collector == pawn or not stock._PAWN.fullmatch(collector)
                                       or state not in ("collecting", "collected", "owned", "failed"))):
        raise ValueError("Invalid live stock dig report")
    if (pre_contact and (spade, actor, package, material, incidental, escrow, slot) != ("-", "-", "-", "-", "0", "-", "-")
            or state in ("unclaimed", "escrowed", "collecting", "collected", "owned") and (actor == "-" or contact is None)
            or state in ("collected", "owned") and package == "-"
            or state == "escrowed" and escrow == "-"):
        raise ValueError("Unqualified live stock dig custody")
    report = dict(map=area, pawn=pawn, state=state, serial=int(serial), spade_guid=None if spade == "-" else spade,
                  actor=None if actor == "-" else actor, package_guid=None if package == "-" else package,
                  contact=contact, material=None if material == "-" else material, incidental=int(incidental),
                  escrow_guid=None if escrow == "-" else escrow, slot=None if slot == "-" else slot,
                  fault=None if fault == "-" else fault)
    if collector != "-":
        report["collector"] = collector
    return report


def parse_diagnostic(fields: list[str], phase: str, area: str) -> dict | None:
    if (len(fields) != 8 or (phase, area, fields[1]) != ("hosting", CROSSROADS, CROSSROADS)
            or (fields[2] != "-" and (len(fields[2]) > 360 or not stock._PAWN.fullmatch(fields[2])))
            or any(not _STATUS.fullmatch(value) for value in fields[3:])):
        return None
    return dict(zip(("map", "pawn", "context", "heartbeat", "quest", "arm", "read"), fields[1:]))


def escrow_frames(data: dict, members=None) -> list[dict]:
    custody = data.get(CUSTODY)
    if custody is None:
        return []
    if type(custody) is not dict or custody.get("state") not in ("escrowed", "owned", "opened"):
        raise ValueError("Invalid stock sealed package custody")
    state = custody["state"]
    fields = _CUSTODY_FIELDS | ({"escrow"} if state == "escrowed" else set())
    if state in ("owned", "opened") and "owner" in custody:
        owner = custody["owner"]
        if (not character_id(owner) or owner == "host" or owner not in data.get("players", {})
                or members is not None and owner not in members):
            raise ValueError("Invalid stock sealed package owner")
        fields.add("owner")
    if (set(custody) != fields or (custody["map"], custody["source"], custody["flag"]) != (CROSSROADS, stock.SOURCE, stock.FLAG)
            or type(custody["flag_value"]) is not int or custody["flag_value"] != 1
            or not valid_guid(custody["spade_guid"]) or not valid_guid(custody["package_guid"])
            or not isinstance(custody["material"], str) or not _valid_contact(custody["contact"], custody["material"])):
        raise ValueError("Invalid stock sealed package identity")
    if state in ("owned", "opened"):
        return []
    frame = custody["escrow"]
    if type(frame) is not dict or frame.get("map") != CROSSROADS or not isinstance(frame.get("slot"), str) or not _FRAME_SLOT.fullmatch(frame["slot"]):
        raise ValueError("Invalid stock sealed package escrow frame")
    return [frame]


def _handoff_pawns(host, character):
    game = host.last_game
    guest = host.native_recipients().get(character, {})
    players = host.party["players"]
    pawns = game.get("pawns", {})
    sender = pawns.get("local", {})
    recipient = pawns.get(guest.get("alias"), {})
    if (host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or game.get("state") != "hosting" or game.get("map") not in _HANDOFF_MAPS
            or time.monotonic() - host.last_seen > 3 or not guest.get("connected")
            or any(not players.get(actor) or players[actor]["downed"] or actor in host.party["pending_spawns"]
                   for actor in ("host", character))
            or not sender.get("name") or not guest.get("pawn") or recipient.get("name") != guest["pawn"]):
        raise party.RuleError(_HANDOFF_REFUSAL + "participant_unavailable")
    return sender, recipient


def _handoff_near(sender, recipient):
    first, second = sender.get("location"), recipient.get("location")
    return location(first) and location(second) and math.dist(first, second) <= _HANDOFF_RANGE


async def handoff(host, bridge, target, *, journal_intent=None):
    """Commit the exact sealed package to one enrolled guest's backpack."""
    from .native_computer_board import MISSING_CONTACT

    if (not isinstance(target, str) or not host.native_host_dig_live or not host.persist_native_guest
            or not host.native_profile or host.transitioning):
        raise party.RuleError(_HANDOFF_REFUSAL + "unavailable")
    character = host.roles().get(target, target)
    if not character_id(character) or character == "host":
        raise party.RuleError(_HANDOFF_REFUSAL + "participant_unavailable")
    receipt = host.party.get("native_quests", {}).get(MISSING_CONTACT.quest_id, {})
    if receipt.get("step") == -10:
        raise party.RuleError(_HANDOFF_REFUSAL + "quest_already_completed")
    custody = host.party.get(CUSTODY, {})
    escrow_frames(host.party)
    if custody.get("state") != "owned":
        raise party.RuleError(_HANDOFF_REFUSAL + "package_unavailable")
    sender, recipient = _handoff_pawns(host, character)
    owner = custody.get("owner", "host")
    if owner == character:
        return {CUSTODY: custody}
    if owner != "host":
        raise party.RuleError(_HANDOFF_REFUSAL + "wrong_owner")
    if not _handoff_near(sender, recipient):
        raise party.RuleError(_HANDOFF_REFUSAL + "out_of_range")

    host.transitioning = True
    required = frozenset({character})
    area, guid = host.last_game["map"], custody["package_guid"]
    epoch = host.zone_epoch
    names = sender["name"], recipient["name"]
    try:
        await host.checkpoint_native(bridge, require_guest_frames=required)
        current = _handoff_pawns(host, character)
        if (host.last_game["map"], host.zone_epoch) != (area, epoch) or tuple(pawn["name"] for pawn in current) != names:
            raise party.RuleError(_HANDOFF_REFUSAL + "participant_changed")
        if journal_intent is not None:
            from .native_package_handoff_journal import INTENT

            if host.last_game.get(INTENT) != journal_intent:
                raise party.RuleError(_HANDOFF_REFUSAL + "journal_changed")
        host.native_checkpoint_unsafe = True
        try:
            snapshot = await bridge.command(HANDOFF, *names, character, guid, timeout=5)
        except RuntimeError as error:
            if str(error).startswith(_HANDOFF_REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if (snapshot.get("state"), snapshot.get("map"), snapshot.get("ack", {}).get("message")) != (
                "hosting", area, f"stock sealed package handed off {guid}"):
            raise RuntimeError("The stock sealed package handoff did not verify")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate = party.load_party(host.party)
        owned = candidate[CUSTODY]
        owned["owner"] = character
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=required)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_missing_contact_handoff", revision=revision, owner=character,
                           package_guid=guid, map=area)
        return {CUSTODY: owned}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


def _report(host, expected_pawn=None) -> dict:
    game = host.last_game
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    report = game.get(KEY, {})
    return (report if (game.get("state"), game.get("map"), report.get("map"), report.get("pawn")) ==
            ("hosting", CROSSROADS, CROSSROADS, pawn) and (expected_pawn is None or pawn == expected_pawn) else {})


def _matches(prior, report, guid_field):
    return all(prior[key] == report.get(key) for key in ("spade_guid", "contact", "material")) and (
        prior["package_guid"] == report.get(guid_field))


def checkpoint_pending(host) -> bool:
    if not getattr(host, "native_host_dig_live", False) or host.last_game.get("map") != CROSSROADS:
        return False
    report, prior = _report(host), host.party.get(CUSTODY, {})
    state, saved = report.get("state"), prior.get("state")
    if saved == "opened":
        return False
    if state in ("escrowed", "owned"):
        return saved != state or not _matches(prior, report, "escrow_guid" if state == "escrowed" else "package_guid")
    return (state in ("contact", "unclaimed", "collecting", "collected", "failed")
            or state in (None, "ready", "refused") and saved == "escrowed")


class DigSettlement:
    def __init__(self):
        self.issue = None
        self.retry_at = 0.0

    async def update(self, host, bridge) -> bool:
        report = _report(host)
        key = (host.zone_epoch, report.get("serial"), report.get("state"), report.get("fault"))
        now = time.monotonic()
        prior = self.issue
        if prior and prior[0] == key and now < self.retry_at:
            return checkpoint_pending(host)
        self.issue = None
        try:
            waiting = await settle(host, bridge)
        except (OSError, ValueError, RuntimeError) as error:
            self.issue = (key, str(error))
            self.retry_at = now + 1
            if self.issue != prior:
                host.control.event("native_host_dig_settlement_deferred", map=CROSSROADS,
                                   state=report.get("state"), serial=report.get("serial"), reason=str(error))
            return checkpoint_pending(host)
        if report.get("state") == "refused":
            self.issue = (key, report["fault"])
            self.retry_at = now + 1
            if self.issue != prior:
                host.control.event("native_host_dig_contact_refused", map=CROSSROADS, reason=report["fault"])
        return waiting


def advance_dropped_package_owner(data: dict, drop: dict, recipient: str) -> None:
    """Follow the exact quest package through a normal player drop and reclaim."""
    if drop.get("class_path") != stock.PACKAGE_CLASS:
        return
    custody = data.get(CUSTODY, {})
    if ((custody.get("state"), custody.get("package_guid"), custody.get("owner", "host"))
            != ("owned", drop["guid"], drop["owner"])):
        return
    if recipient == "host":
        custody.pop("owner", None)
    else:
        custody["owner"] = recipient


def may_open_package(data: dict, guid: str, opener: str) -> bool:
    custody = data.get(CUSTODY, {})
    return custody.get("package_guid") != guid or (custody.get("state"), custody.get("owner", "host")) == ("owned", opener)


def opened_package(data: dict, guid: str, opener: str) -> None:
    custody = data.get(CUSTODY, {})
    if custody.get("package_guid") == guid and may_open_package(data, guid, opener):
        custody["state"] = "opened"


def package_identity(guid: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_OID, f"native-missing-contact-package:{guid}").hex


def package_frame(host):
    """Replicate the loose package only after its capture and source checkpoint."""
    report = _report(host)
    custody = host.party.get(CUSTODY, {})
    if (report.get("state") != "escrowed" or custody.get("state") != "escrowed"
            or not _matches(custody, report, "escrow_guid")):
        return None
    return {"map": CROSSROADS, "epoch": host.zone_epoch, "guid": custody["package_guid"],
            "contact": custody["contact"], "material": custody["material"]}


async def collect(host, bridge, character, target, operation):
    from . import native_inventory, native_pickup_audio

    epoch, _, guid = target.partition("@") if isinstance(target, str) else ("", "", "")
    if (epoch != host.zone_epoch or not valid_guid(guid) or not isinstance(operation, str)
            or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError(_COLLECT_REFUSAL + "scope_changed")
    fingerprint = [COLLECT, target, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    report = _report(host)
    custodian = host.native_recipients().get(character, {})
    pawn = custodian.get("pawn") if custodian.get("connected") else None
    if (not host.native_host_dig_live or not host.persist_native_guest or not host.native_profile
            or not pawn or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen > 3 or host.party.get("wipe")
            or host.party["players"].get(character, {}).get("downed") is not False
            or character in host.party["pending_spawns"]):
        raise party.RuleError(_COLLECT_REFUSAL + "unavailable")
    frame = package_frame(host)
    if not frame or frame["guid"] != guid:
        raise party.RuleError(_COLLECT_REFUSAL + "package_unavailable")
    if not await native_inventory.retain_guest_save(host, bridge, character, pawn):
        raise party.RuleError(_COLLECT_REFUSAL + "guest_save_unavailable")

    def terminal(snapshot):
        live = snapshot.get(KEY, {})
        return (snapshot.get("state"), snapshot.get("map"), live.get("serial")) == ("hosting", CROSSROADS, report["serial"]) and (
            live.get("state") in ("collected", "failed", "escrowed"))

    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(COLLECT, pawn, str(report["serial"]), guid, timeout=5)
        except RuntimeError as error:
            if str(error).startswith(_COLLECT_REFUSAL):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if snapshot.get("ack", {}).get("message") != "stock sealed package collect requested":
            raise RuntimeError("The stock sealed package collection did not dispatch")
        if not terminal(snapshot):
            dispatched = snapshot["sequence"]
            snapshot = await bridge.wait_for(lambda sample: sample["sequence"] > dispatched and terminal(sample), timeout=5)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        actual = _report(host, report["pawn"])
        if actual.get("state") == "failed":
            raise RuntimeError(actual["fault"])
        if (actual.get("state") == "escrowed" and actual.get("actor") == report["actor"]
                and actual.get("collector") is None and actual.get("package_guid") is None
                and _matches(host.party[CUSTODY], actual, "escrow_guid")):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(_COLLECT_REFUSAL + "interaction_refused")
        if (actual.get("collector") != pawn or actual.get("state") != "collected" or actual.get("actor") != report["actor"]
                or not _matches(host.party[CUSTODY], actual, "escrow_guid") or not valid_guid(actual.get("package_guid"))):
            raise RuntimeError("The stock sealed package did not reach the authenticated guest inventory")
        receipt = {"id": operation, "request": fingerprint}
        if await _commit(host, bridge, actual, "owned", actual["package_guid"], owner=character, receipt=receipt):
            raise RuntimeError("The guest's sealed package checkpoint did not settle")
        host.native_checkpoint_unsafe = False
        native_pickup_audio.committed(host, pawn, operation, stock.PACKAGE_CLASS)
        return {CUSTODY: host.party[CUSTODY]}
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True


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


async def _commit(host, bridge, report, state, guid, frame=None, *, owner=None, receipt=None) -> bool:
    pivot = dict(state=state, map=CROSSROADS, source=stock.SOURCE, flag=stock.FLAG, flag_value=1,
                 spade_guid=report["spade_guid"], contact=report["contact"], material=report["material"], package_guid=guid)
    if frame is not None:
        pivot["escrow"] = frame
    candidate = party.load_party(host.party)
    candidate[CUSTODY] = pivot
    if owner is not None:
        pivot["owner"] = owner
    if receipt is not None:
        rows = candidate["receipts"].setdefault(owner, [])
        rows.append(dict(receipt, result={CUSTODY: pivot}))
        del rows[:-party.RECENT_ACTIONS]
    token = f"dig@{report['serial']}@{state}@{guid}"
    try:
        # Keep the buried-source flag and its exact item owner in the same durable generation.
        options = {"require_guest_frames": frozenset({owner})} if owner is not None else {}
        revision = await host.checkpoint_native(bridge, candidate, dig_token=token, **options)
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
    if prior and prior["state"] == "opened":
        # The resident shovel receipt outlives its inventory item in this visit.
        return False
    if state is None:
        return bool(prior and prior["state"] == "escrowed")
    if state == "failed":
        raise RuntimeError(report["fault"])
    if state in ("contact", "collecting"):
        return True
    if state in ("ready", "refused"):
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
    if report.get("collector"):
        raise RuntimeError("A guest's sealed package collection did not reach the coordinated checkpoint")
    return await _commit(host, bridge, report, "owned", report["package_guid"])
