"""Run the staged stock Missing Contact dig and verify the canonical save."""

import math
import re
import time

from . import party
from .native_gold import valid_guid
from .travel import CROSSROADS


KEY = "native_host_dig"
TAG = "native-host-dig"
ACQUIRE = "native-host-dig-acquire-fixture"
DIG = "native-host-dig-fixture"
INSPECT = "native-host-dig-inspect-fixture"
ACTIONS = (ACQUIRE, DIG, INSPECT)
WORLD = f"{CROSSROADS}.CrossRoads_P"
SOURCE = f"BurriedItem_BP_C {WORLD}:PersistentLevel.BurriedItem_BP_C_1"
FLAG = "BurriedItem_BP_C_1-CrossRoads_P-"
SPADE_FLAG = "MeleeWep_Spade_C_3-CrossRoads_P-"
PACKAGE_CLASS = "/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C"
SPADE_CLASS = "/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C"
SITE = (1665.2142939210705, 5811.305953931523, 339.15110679941967)
_PAWN = re.compile(re.escape(f"Player_FP_C {WORLD}:PersistentLevel.Player_FP_C_") + r"[0-9]+")
_PACKAGE = re.compile(re.escape(f"Package_Item_FarmHouse_C {WORLD}:PersistentLevel.Package_Item_FarmHouse_C_") + r"[0-9]+")
_MATERIALS = frozenset(f"PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/PM_{kind}.PM_{kind}"
                       for kind in ("Dirt", "Debris", "Grass", "MossyDirt", "Mud", "Sand"))
_PHASES = {"acquiring", "equipped", "pressed", "dug", "collected", "restored", "failed"}


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) not in (17, 20):
        raise ValueError("Invalid stock host dig report size")
    tag, reported, pawn, state, tool, flag, flag_value, actor, package, x, y, z, material, holes, incidental, press, fault = fields[:17]
    unidentified_tool = (state == "acquiring" or state == "failed" and press != "-")
    if (tag != TAG or (phase, area, reported) != ("hosting", CROSSROADS, CROSSROADS) or _PAWN.fullmatch(pawn) is None
            or state not in _PHASES or (tool != "-" if unidentified_tool else not valid_guid(tool))
            or flag != FLAG or flag_value not in ("0", "1")
            or actor != "-" and _PACKAGE.fullmatch(actor) is None or package != "-" and not valid_guid(package)
            or holes not in ("-", "1") or incidental not in ("-", "0", "1")
            or press != "-" and (not press.isascii() or not press.isdigit() or len(press) > 16 or not 0 < int(press) < 2**53)):
        raise ValueError("Invalid stock host dig identity")
    coordinates = (x, y, z)
    contact = None if coordinates == ("-", "-", "-") else list(map(float, coordinates))
    if contact is not None and (not all(math.isfinite(axis) for axis in contact) or math.dist(contact, SITE) > 45.1):
        raise ValueError("Invalid stock host dig contact")
    if ((contact is None) != (material == "-") or contact is not None and material not in _MATERIALS
            or (state == "failed") != (fault != "-")
            or state in ("dug", "collected") and (actor == "-" or flag_value != "1" or holes != "1" or incidental == "-" or contact is None)
            or state in ("collected", "restored") and (package == "-" or flag_value != "1")
            or state in ("acquiring", "equipped", "collected") and press == "-"
            or (state in ("acquiring", "equipped", "pressed") or unidentified_tool)
               and (flag_value, actor, package, holes, incidental) != ("0", "-", "-", "-", "-")
            or unidentified_tool and contact is not None):
        raise ValueError("Invalid stock host dig readback")
    report = {"map": area, "pawn": pawn, "state": state, "guid": None if unidentified_tool else tool,
              "flag": flag, "flag_value": int(flag_value),
              "source": SOURCE, "actor": None if actor == "-" else actor, "package_guid": None if package == "-" else package,
              "contact": contact, "material": None if material == "-" else material,
              "holes": None if holes == "-" else int(holes), "incidental": None if incidental == "-" else int(incidental),
              "stock_f": None if press == "-" else int(press), "fault": None if fault == "-" else fault}
    if len(fields) == 20 and (state == "equipped" or fields[17:] != ["-", "-", "-"]):
        fate, mode, present = fields[17:]
        if (state != "equipped" or fate not in ("missing", "destroyed", "dormant") or mode not in ("0", "1", "2")
                or present != ("1" if mode == "2" else "-")):
            raise ValueError("Invalid stock placed spade retirement")
        report["spade_source"] = {"fate": fate, "mode": int(mode), "flag": SPADE_FLAG,
                                  "flag_value": None if present == "-" else 1}
    return report


async def perform(host, bridge, action: str, target: str | None, operation_id: str) -> dict:
    if (not host.native_guest or not host.native_zones or not host.fixtures or host.native_profile is None
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]
            or (host.last_game.get("state"), host.last_game.get("map")) != ("hosting", CROSSROADS)
            or time.monotonic() - host.last_seen >= 3):
        raise party.RuleError("stock_host_dig_fixture_unavailable")
    pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    peers = host.native_recipients()
    if (not isinstance(pawn, str) or _PAWN.fullmatch(pawn) is None or len(peers) != 1
            or not all(seat.get("connected") for seat in peers.values())
            or any(host.party["players"].get(actor, {}).get("downed") for actor in ("host", *peers))):
        raise party.RuleError("stock_host_dig_exact_party_required")
    if (action not in ACTIONS or (not valid_guid(target) if action == DIG else target is not None)
            or not isinstance(operation_id, str) or party.REQUEST_ID.fullmatch(operation_id) is None):
        raise party.RuleError("stock_host_dig_fixture_arguments")

    def frame(snapshot):
        row = snapshot.get(KEY, {})
        if (snapshot.get("state"), snapshot.get("map"), row.get("map"), row.get("pawn")) != (
                "hosting", CROSSROADS, CROSSROADS, pawn):
            return {}
        return row

    if action == INSPECT:
        snapshot = await bridge.command(action, pawn, timeout=5)
        report = frame(snapshot)
        prior = host.party.get(KEY, {})
        if (report.get("state") != "restored" or report.get("flag_value") != 1
                or tuple(report.get(field) for field in ("guid", "package_guid")) !=
                tuple(prior.get(field) for field in ("guid", "package_guid"))):
            raise RuntimeError("The exact stock dig checkpoint did not restore")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return {KEY: report}

    fingerprint = [action, target, None]
    receipts = host.party.get("receipts", {}).get("host", [])
    for saved in receipts:
        if saved["id"] == operation_id:
            if saved["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return saved["result"]
    if host.party.get(KEY):
        raise party.RuleError("stock_host_dig_source_already_secured")
    if action == DIG and not any(saved["request"][0] == ACQUIRE and saved["result"].get(KEY, {}).get("guid") == target
                                 for saved in receipts):
        raise party.RuleError("stock_host_dig_exact_acquired_spade_required")

    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = (await bridge.command(action, pawn, timeout=6) if action == ACQUIRE
                        else await bridge.command("native-host-dig-press-fixture", pawn, target, timeout=6))
        except RuntimeError as error:
            if str(error).startswith("stock_host_dig_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        report = frame(snapshot)
        if action == ACQUIRE:
            if report.get("state") == "acquiring":
                press = report.get("stock_f")
                snapshot = await bridge.wait_for(lambda sample: frame(sample).get("state") in ("equipped", "failed")
                                                and frame(sample).get("stock_f") == press, timeout=5)
                report = frame(snapshot)
            if report.get("state") == "failed":
                raise RuntimeError(report.get("fault"))
            if report.get("state") != "equipped" or not valid_guid(report.get("guid")) or not report.get("spade_source"):
                raise RuntimeError("Stock Interact did not equip the exact placed spade")
        else:
            if report.get("state") != "pressed" or report.get("guid") != target:
                raise RuntimeError("The stock primary Started edge did not verify")
            settled = await bridge.wait_for(lambda sample: frame(sample).get("state") in ("dug", "failed"), timeout=5)
            dug = frame(settled)
            if (dug.get("state"), dug.get("guid"), dug.get("flag_value"), dug.get("holes")) != ("dug", target, 1, 1):
                raise RuntimeError(f"The stock buried-item callback did not settle ({dug.get('fault')})")
            snapshot = await bridge.command("native-host-dig-collect-fixture", pawn, timeout=6)
            report = frame(snapshot)
            if (report.get("state") != "collected" or not valid_guid(report.get("package_guid"))
                    or report.get("guid") != target or report.get("actor") != dug.get("actor")
                    or report.get("contact") != dug.get("contact") or report.get("incidental") != dug.get("incidental")):
                raise RuntimeError("The sealed stock package did not reach the exact canonical backpack")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {KEY: report}
        candidate = party.load_party(host.party)
        if action == DIG:
            candidate[KEY] = report
        history = candidate["receipts"].setdefault("host", [])
        history.append({"id": operation_id, "request": fingerprint, "result": result})
        del history[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset(peers))
        host.party = candidate
        host.native_checkpoint_unsafe = False
        host.control.event("native_host_dig_verified", revision=revision, operation_id=operation_id, **report)
        return result
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
