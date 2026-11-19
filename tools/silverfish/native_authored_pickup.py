"""Commit a qualified placed pickup to one stock inventory."""

import asyncio
import time

from . import native_host_save, party
from .native_gold import valid_guid
from .travel import CITY, OBSERVATORY, RAIL_YARD, SCIENTIST_SHOP


SOURCE = "observatory-comp-board"
ACTOR = f"Item_CompBoard_C {OBSERVATORY}.Observitory_P:PersistentLevel.Item_CompBoard_C_2"
ITEM_CLASS = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
CITY_WIRE = "city-copper-wire"
CITY_BRANDY = "city-paladin-brandy"
DEAD_END = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"
COUP_REPORT = "dead-end-coup-report"
DEPOT_DOCUMENTS = "railyard-depot-documents"
SCIENTIST_BLANK_TAPE = "scientist-blank-data-tape"
SOURCES = {
    SOURCE: (OBSERVATORY, ACTOR, ITEM_CLASS),
    CITY_WIRE: (CITY, f"Item_CopperWire_C {CITY}.City_01_P:PersistentLevel.Item_CopperWire_C_3",
                "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"),
    CITY_BRANDY: (CITY, f"Item_VintageWine_C {CITY}.City_01_P:PersistentLevel.Item_VintageWine_C_1",
                  "/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C"),
    COUP_REPORT: (DEAD_END, f"Note_DeadEnd_01_C {DEAD_END}.DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1",
                  "/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C"),
    DEPOT_DOCUMENTS: (RAIL_YARD, f"Documents_DepotOffice_BP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Documents_DepotOffice_BP_C_1",
                      "/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C"),
    SCIENTIST_BLANK_TAPE: (SCIENTIST_SHOP, f"DataTape_Blank_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.DataTape_Blank_C_3",
                           "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
}
MAP_SOURCES: dict[str, list[str]] = {}
for source, (area, _, _) in SOURCES.items():
    MAP_SOURCES.setdefault(area, []).append(source)
ACTION = "native-authored-pickup-claim"
STATE_KEY = "native_authored_pickups"
SOURCES_KEY = "native_authored_pickup_sources"
INTERACTIONS_KEY = "native_authored_pickup_interactions"
APPROACH = "native-authored-pickup-approach"
FIXTURE = "native-authored-pickup-fixture"
STOCK_FIXTURE = "native-authored-pickup-stock-fixture"
FIXTURE_ENTRIES = {FIXTURE: "fixture", STOCK_FIXTURE: "stock"}


def select_source(area: str | None, requested: str | None) -> str:
    sources = MAP_SOURCES.get(area, ())
    if requested is None and len(sources) == 1:
        return sources[0]
    if isinstance(requested, str) and requested in sources:
        return requested
    raise party.RuleError("authored_pickup_exact_source_required")


def _displayed(game: dict, source: str) -> dict:
    area, actor, item_class = SOURCES[source]
    row = game.get(SOURCES_KEY, {}).get(source, {})
    if (row.get("source"), row.get("map"), row.get("actor"), row.get("class_path")) == (source, area, actor, item_class):
        return row
    return {}


def fixture_proof(game: dict, source: str, entry: str = "fixture") -> dict:
    row = game.get(INTERACTIONS_KEY, {}).get(source, {})
    if (row.get("entry"), row.get("map"), row.get("source"), row.get("pawn")) != (
            entry, SOURCES[source][0], source, game.get("pawns", {}).get("local", {}).get("name")):
        raise RuntimeError("The stock pickup fixture did not report the selected source and pawn")
    return {"native_authored_pickup_interaction": row}


async def project(service, bridge, pickups: dict) -> None:
    area, phase = service.last_game.get("map"), service.last_game.get("state")
    for source in MAP_SOURCES.get(area, ()):
        if source not in pickups or _displayed(service.last_game, source).get("state") != "available":
            continue
        snapshot = await bridge.command("native-authored-pickup-project", "claimed", source, timeout=5)
        if (snapshot.get("map"), snapshot.get("state"), _displayed(snapshot, source).get("state")) != (area, phase, "claimed"):
            raise RuntimeError("The game did not verify the exact consumed placed pickup projection")
        service.last_game = snapshot


async def claim(host, bridge, character: str, target: str | None, operation_id: str) -> dict:
    if not isinstance(target, str) or target not in SOURCES or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_authored_pickup_request")
    area, actor, item_class = SOURCES[target]
    if (not host.native_zones or host.native_profile is None or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or host.last_game.get("map") != area or time.monotonic() - host.last_seen > 3):
        raise party.RuleError("native_authored_pickup_unavailable")
    fingerprint = [ACTION, target, None]
    for receipt in host.party["receipts"].get(character, []):
        if receipt["id"] == operation_id:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    if target in host.party.get(STATE_KEY, {}):
        raise party.RuleError("native_authored_pickup_already_claimed")
    if _displayed(host.last_game, target).get("state") != "available":
        raise party.RuleError("native_authored_pickup_unavailable")
    if (host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    if character == "host":
        pawn = host.last_game.get("pawns", {}).get("local", {}).get("name")
    else:
        member = host.native_recipients().get(character) or {}
        pawn = member.get("pawn") if member.get("connected") else None
    if not pawn:
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    await host.checkpoint_native(bridge)
    slot = native_host_save.new_slot()
    host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(ACTION, pawn, slot, target, timeout=7)
        report = snapshot.get("native_authored_pickup_claim", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or tuple(report.get(key) for key in ("source", "map", "pawn", "slot")) != (target, area, pawn, slot)):
            raise RuntimeError("The authored pickup proof belongs to a different pawn or generation")
        if not report["ok"] and not report["applied"]:
            host.native_checkpoint_unsafe = False
            raise party.RuleError(report["status"])
        if not report["ok"] or not report["applied"] or report["status"] != "claimed" or not valid_guid(report["guid"]):
            raise RuntimeError(f"The authored pickup did not verify ({report['status']})")
        await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        row = {"source": target, "map": area, "actor": actor, "class_path": item_class, "guid": report["guid"],
               "owner": character}
        result = {"native_authored_pickup_claim": row}
        candidate = party.load_party(host.party)
        candidate.setdefault(STATE_KEY, {})[target] = row
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation_id, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character} - {"host"}))
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
    host.control.event("native_authored_pickup_committed", revision=revision, **row)
    return result
