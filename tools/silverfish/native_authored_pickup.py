"""Commit a qualified placed pickup to its stock inventory or the party wallet."""

import asyncio
import time

from . import native_black_box, native_dead_drop, native_dust_cabinet, native_host_save, native_savlo_locker, party
from .authored_cash import BY_SOURCE as STOCK_CASH
from .authored_supply import BY_SOURCE as STOCK_SUPPLIES
from .native_dialogue import PROTOTYPE_QUEST
from .native_gold import valid_guid
from .travel import BOROUGH, CISTERN, CITY, CROSSROADS, FOREST, LAB_E206, NORTHEAST_RAIL, OBSERVATORY, RAIL_YARD, SCIENTIST_SHOP, SCI_TUNNEL


SOURCE = "observatory-comp-board"
ACTOR = f"Item_CompBoard_C {OBSERVATORY}.Observitory_P:PersistentLevel.Item_CompBoard_C_2"
ITEM_CLASS = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"
CITY_WIRE = "city-copper-wire"
CITY_BRANDY = "city-paladin-brandy"
CITY_PEACHES = "city-canned-peaches"
DEAD_END = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"
COUP_REPORT = "dead-end-coup-report"
DEPOT_DOCUMENTS = "railyard-depot-documents"
SCIENTIST_BLANK_TAPE = "scientist-blank-data-tape"
LAB_E206_BLANK_TAPE = "lab-e206-blank-data-tape"
PROTOTYPE_DRILL = "scientist-tunnel-drill-head"
POCKET_WATCH = "crossroads-pocket-watch"
OLD_PIPE = "crossroads-old-pipe"
SAVLO_ESTATE = "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P"
SAVLO_RING = "savlo-estate-evil-eye-ring"
BOROUGH_MRE = "borough-mre"
DUST_FILTER = "railyard-dust-filter"
MOLASSES = "northeast-rail-molasses"
SAVLO_KEYCARD = "trench-savlo-keycard"
CISTERN_OLD_NFTA_KEYCARD = "cistern-old-nfta-keycard"
TRENCH = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
FORT_LETO = "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P"
FORT_LETO_LOCKER_KEY = "fort-leto-locker-key-543"
STOLEN_BAG = "forest-brokers-artifacts-bag"
DEAD_DROP = native_dead_drop.SOURCE
SOURCES = {
    SOURCE: (OBSERVATORY, ACTOR, ITEM_CLASS),
    CITY_WIRE: (CITY, f"Item_CopperWire_C {CITY}.City_01_P:PersistentLevel.Item_CopperWire_C_3",
                "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"),
    CITY_BRANDY: (CITY, f"Item_VintageWine_C {CITY}.City_01_P:PersistentLevel.Item_VintageWine_C_1",
                  "/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C"),
    CITY_PEACHES: (CITY, f"Item_CannedPeaches_C {CITY}.City_01_P:PersistentLevel.Item_CannedPeaches_C_1",
                   "/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"),
    COUP_REPORT: (DEAD_END, f"Note_DeadEnd_01_C {DEAD_END}.DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1",
                  "/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C"),
    DEPOT_DOCUMENTS: (RAIL_YARD, f"Documents_DepotOffice_BP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Documents_DepotOffice_BP_C_1",
                      "/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C"),
    SCIENTIST_BLANK_TAPE: (SCIENTIST_SHOP, f"DataTape_Blank_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.DataTape_Blank_C_3",
                           "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
    LAB_E206_BLANK_TAPE: (LAB_E206, f"DataTape_Blank_C {LAB_E206}.Lab_E206_P:PersistentLevel.DataTape_Blank_C_1",
                         "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
    PROTOTYPE_DRILL: (SCI_TUNNEL, f"Art_DrillHead_C {SCI_TUNNEL}.Sci_Tunnel_01_P:PersistentLevel.Art_DrillHead_C_1",
                      "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C"),
    POCKET_WATCH: (CROSSROADS, f"Item_PocketWatch_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Item_PocketWatch_C_1",
                   "/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C"),
    OLD_PIPE: (CROSSROADS, f"MeleeWep_OldPipe_C {CROSSROADS}.CrossRoads_P:PersistentLevel.MeleeWep_OldPipe_C_1",
               "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"),
    SAVLO_RING: (SAVLO_ESTATE, f"Art_EvilEyeRing_C {SAVLO_ESTATE}.SavloEstate_P:PersistentLevel.Art_EvilEyeRing_C_1",
                 "/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C"),
    BOROUGH_MRE: (BOROUGH, f"Item_MRE_C {BOROUGH}.Borough:PersistentLevel.Item_MRE_C_1",
                  "/Game/Classes/Items/Item_MRE.Item_MRE_C"),
    DUST_FILTER: (RAIL_YARD, f"Helm_DustFilter_C {RAIL_YARD}.RailYard_P:PersistentLevel.Helm_DustFilter_C_1",
                  "/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"),
    MOLASSES: (NORTHEAST_RAIL, f"Item_Molasis_C {NORTHEAST_RAIL}.NorthEast_Rail_P:PersistentLevel.Item_Molasis_C_1",
               "/Game/Classes/Items/Item_Molasis.Item_Molasis_C"),
    SAVLO_KEYCARD: (TRENCH, f"KeyCard_Savlo_BP_C {TRENCH}.Trench_01_P:PersistentLevel.KeyCard_Savlo_BP_C_1",
                    "/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C"),
    CISTERN_OLD_NFTA_KEYCARD: (CISTERN, f"KeyCard_NFTA_Old_BP_C {CISTERN}.Cistern_P:PersistentLevel.KeyCard_Refinery_Sec_BP_C_1",
                             "/Game/Classes/Items/Key_Items/KeyCard_NFTA_Old_BP.KeyCard_NFTA_Old_BP_C"),
    FORT_LETO_LOCKER_KEY: (FORT_LETO, f"LockerKey_543_C {FORT_LETO}.FortLeto_01_P:PersistentLevel.LockerKey_543_C_1",
                          "/Game/Classes/Items/Key_Items/LockerKey_543.LockerKey_543_C"),
    DEAD_DROP: (native_dead_drop.MAP, native_dead_drop.ACTOR, native_dead_drop.CLASS),
    native_black_box.SOURCE: (native_black_box.MAP, native_black_box.ACTOR, native_black_box.CLASS),
    STOLEN_BAG: (FOREST, f"Brokers_ArtifactsBag_C {FOREST}.Forest_01_P:PersistentLevel.Brokers_ArtifactsBag_C_1",
                 "/Game/Classes/Items/Key_Items/Brokers_ArtifactsBag.Brokers_ArtifactsBag_C"),
}
SOURCES.update({source: (row.area, row.actor, row.class_path) for source, row in STOCK_SUPPLIES.items()})
SOURCES.update({source: (row.area, row.actor, row.item.class_path) for source, row in STOCK_CASH.items()})
MAP_SOURCES: dict[str, list[str]] = {}
for source, (area, _, _) in SOURCES.items():
    MAP_SOURCES.setdefault(area, []).append(source)
ACTION = "native-authored-pickup-claim"
STATE_KEY = "native_authored_pickups"
SOURCES_KEY = "native_authored_pickup_sources"
INTERACTIONS_KEY = "native_authored_pickup_interactions"
MERGE_TAG = "native-authored-supply-merge"
MERGE_KEY = "native_authored_supply_merge"
CASH_TAG = "native-authored-cash-credit"
CASH_KEY = "native_authored_cash_credit"
PROTOTYPE_TAG = "native-authored-prototype-drill"
PROTOTYPE_KEY = "native_authored_prototype_drill"
APPROACH = "native-authored-pickup-approach"
FIXTURE = "native-authored-pickup-fixture"
STOCK_FIXTURE = "native-authored-pickup-stock-fixture"
FIXTURE_ENTRIES = {FIXTURE: "fixture", STOCK_FIXTURE: "stock"}


def select_source(area: str | None, requested: str | None) -> str:
    sources = MAP_SOURCES.get(area, ())
    if requested is None:
        defaults = [source for source in sources if source not in (DEAD_DROP, OLD_PIPE, PROTOTYPE_DRILL)
                    and source not in STOCK_SUPPLIES and source not in STOCK_CASH]
        if len(defaults) == 1:
            return defaults[0]
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
        if source == DEAD_DROP and phase == "connected":
            continue
        if source not in pickups or _displayed(service.last_game, source).get("state") != "available":
            continue
        snapshot = await bridge.command("native-authored-pickup-project", "claimed", source, timeout=5)
        if (snapshot.get("map"), snapshot.get("state"), _displayed(snapshot, source).get("state")) != (area, phase, "claimed"):
            raise RuntimeError("The game did not verify the exact consumed placed pickup projection")
        service.last_game = snapshot


def _needs_epoch(source: str) -> bool:
    return source in STOCK_SUPPLIES or source in STOCK_CASH or source in (DEAD_DROP, PROTOTYPE_DRILL, LAB_E206_BLANK_TAPE)


def request_target(target: str | None, epoch: str | None) -> str | None:
    if isinstance(target, str) and _needs_epoch(target):
        if not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch):
            raise party.RuleError("native_authored_pickup_stale_zone")
        return f"{target}@{epoch}"
    return target


def _current_pawn(host, character: str) -> str | None:
    if character == "host":
        return host.last_game.get("pawns", {}).get("local", {}).get("name")
    member = host.native_recipients().get(character) or {}
    return member.get("pawn") if member.get("connected") else None


def _matches_merge(claim: dict, merge: dict) -> bool:
    name, slot = merge.get("source"), merge.get("slot")
    source = STOCK_SUPPLIES.get(name) if isinstance(name, str) else None
    before, after = merge.get("before"), merge.get("after")
    return (source is not None and source.area == merge.get("map") and claim.get("ok") is True
            and claim.get("applied") is True and claim.get("status") == "merged"
            and all(merge.get(key) == claim.get(key) for key in ("source", "map", "pawn", "slot"))
            and isinstance(merge.get("pawn"), str) and bool(merge["pawn"])
            and isinstance(slot, str) and native_host_save.SLOT.fullmatch(slot) is not None
            and valid_guid(merge.get("source_guid")) and merge["source_guid"] == claim.get("guid")
            and valid_guid(merge.get("stack_guid")) and merge["stack_guid"] != merge["source_guid"]
            and type(before) is int and type(after) is int and 0 < before < after < 2**31
            and after - before == source.units)


def parse_merge(fields: list[str], snapshot: dict) -> dict:
    if len(fields) != 9 or snapshot.get("state") != "hosting":
        raise ValueError("Invalid authored supply merge report")
    source, area, pawn, slot, source_guid, stack_guid, before, after = fields[1:]
    report = {"source": source, "map": area, "pawn": pawn, "slot": slot, "source_guid": source_guid,
              "stack_guid": stack_guid, "before": int(before), "after": int(after)}
    if snapshot.get("map") != area or not _matches_merge(snapshot.get("native_authored_pickup_claim", {}), report):
        raise ValueError("The authored supply merge does not match its typed source and owner")
    return report


def valid_cash_amount(source: str, amount: object) -> bool:
    placement = STOCK_CASH.get(source)
    return placement is not None and placement.accepts(amount)


def _matches_cash(claim: dict, credit: dict) -> bool:
    name = credit.get("source")
    placement = STOCK_CASH.get(name) if isinstance(name, str) else None
    amount, before, after = (credit.get(key) for key in ("amount", "before", "after"))
    slot = credit.get("slot")
    return (placement is not None and credit.get("map") == placement.area
            and claim.get("ok") is True and claim.get("applied") is True
            and claim.get("status") == "cash_credited" and claim.get("guid") == "-"
            and all(credit.get(key) == claim.get(key) for key in ("source", "map", "pawn", "slot"))
            and isinstance(credit.get("pawn"), str) and bool(credit["pawn"])
            and isinstance(slot, str) and native_host_save.SLOT.fullmatch(slot) is not None
            and credit.get("stock_flag") == placement.stock_flag and placement.accepts(amount)
            and type(before) is int and type(after) is int and 0 <= before < after < 2**31 and after - before == amount)


def parse_cash(fields: list[str], snapshot: dict) -> dict:
    if len(fields) != 9 or (snapshot.get("state"), snapshot.get("map")) != ("hosting", fields[2]):
        raise ValueError("Invalid exact authored cash credit")
    source, area, pawn, slot, amount, before, after, flag = fields[1:]
    credit = {"source": source, "map": area, "pawn": pawn, "slot": slot, "amount": int(amount),
              "before": int(before), "after": int(after), "stock_flag": flag}
    if not _matches_cash(snapshot.get("native_authored_pickup_claim", {}), credit):
        raise ValueError("The authored cash credit does not match its exact source, wallet delta and stock save flag")
    return credit


def _matches_prototype(claim: dict, proof: dict) -> bool:
    before, after = proof.get("topic_before"), proof.get("topic_after")
    pawn, slot = proof.get("pawn"), proof.get("slot")
    return ((proof.get("source"), proof.get("map"), proof.get("quest"), proof.get("step")) ==
            (PROTOTYPE_DRILL, SCI_TUNNEL, PROTOTYPE_QUEST, 1)
            and claim.get("ok") is True and claim.get("applied") is True and claim.get("status") == "claimed"
            and all(claim.get(key) == proof.get(key) for key in ("source", "map", "pawn", "slot", "guid"))
            and isinstance(pawn, str) and pawn.startswith("Player_FP_C ") and len(pawn) <= 512
            and isinstance(slot, str) and native_host_save.SLOT.fullmatch(slot) is not None
            and type(proof.get("step")) is int and valid_guid(proof.get("guid"))
            and type(before) is int and type(after) is int and 1 <= before == after < 2**31)


def parse_prototype(fields: list[str], snapshot: dict) -> dict:
    if len(fields) != 10:
        raise ValueError("Invalid authored Prototype Drill progress")
    (_, source, area, pawn, slot, guid, quest, step, topic_before, topic_after) = fields
    proof = dict(source=source, map=area, pawn=pawn, slot=slot, guid=guid, quest=quest, step=int(step),
                 topic_before=int(topic_before), topic_after=int(topic_after))
    if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", SCI_TUNNEL)
            or not _matches_prototype(snapshot.get("native_authored_pickup_claim", {}), proof)):
        raise ValueError("Invalid authored Prototype Drill progress")
    return proof


async def claim(host, bridge, character: str, target: str | None, operation_id: str) -> dict:
    if not isinstance(target, str) or not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_authored_pickup_request")
    target, has_epoch, epoch = target.partition("@")
    is_supply = target in STOCK_SUPPLIES
    is_cash = target in STOCK_CASH
    bound_epoch = _needs_epoch(target)
    if target not in SOURCES or (has_epoch and not bound_epoch):
        raise party.RuleError("invalid_authored_pickup_request")
    if bound_epoch:
        if character == "host" and not has_epoch:
            epoch = host.zone_epoch
        if not isinstance(epoch, str) or not party.REQUEST_ID.fullmatch(epoch) or epoch != host.zone_epoch:
            raise party.RuleError("native_authored_pickup_stale_zone")
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
    if target == DEAD_DROP and not native_dead_drop.frame(host):
        raise party.RuleError("authored_pickup_dead_drop_inactive")
    if target == SAVLO_KEYCARD and not native_savlo_locker.observed(host.last_game, "opened"):
        raise party.RuleError("native_authored_pickup_savlo_locker_closed")
    blocker = native_dust_cabinet.SOURCES.get(target)
    if blocker and not native_dust_cabinet.observed(host.last_game, "opened"):
        raise party.RuleError(f"native_authored_pickup_{blocker.kind}_cabinet_closed")
    if _displayed(host.last_game, target).get("state") != "available":
        raise party.RuleError("native_authored_pickup_unavailable")
    if (host.party["wipe"] is not None or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed")):
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    pawn = _current_pawn(host, character)
    if not pawn:
        raise party.RuleError("native_authored_pickup_owner_unavailable")
    await host.checkpoint_native(bridge)
    if bound_epoch:
        if epoch != host.zone_epoch or host.transitioning or host.stopping or host.native_checkpoint_unsafe or (
                host.last_game.get("state"), host.last_game.get("map")) != ("hosting", area):
            raise party.RuleError("native_authored_pickup_stale_zone")
        if (target in host.party.get(STATE_KEY, {}) or _displayed(host.last_game, target).get("state") != "available"
                or _current_pawn(host, character) != pawn or host.party["wipe"] is not None
                or character in host.party["pending_spawns"] or host.party["players"].get(character, {}).get("downed")):
            raise party.RuleError("native_authored_pickup_owner_or_source_changed")
    if target == DEAD_DROP and not native_dead_drop.frame(host):
        raise party.RuleError("authored_pickup_dead_drop_inactive")
    amount = _displayed(host.last_game, target).get("wallet_value") if is_cash else None
    if is_cash and not valid_cash_amount(target, amount):
        raise party.RuleError("authored_cash_stock_value_unavailable")
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
        credit = snapshot.get(CASH_KEY)
        if is_cash:
            if (not isinstance(credit, dict) or not _matches_cash(report, credit)
                    or credit["amount"] != amount
                    or snapshot.get("native_wallet") != credit["after"]):
                raise RuntimeError("The exact authored cash flag and wallet credit did not verify")
        elif (credit is not None or not report["ok"] or not report["applied"] or report["status"] not in ("claimed", "merged")
                or not valid_guid(report["guid"])):
            raise RuntimeError(f"The authored pickup did not verify ({report['status']})")
        prototype = snapshot.get(PROTOTYPE_KEY)
        if target == PROTOTYPE_DRILL and (not isinstance(prototype, dict) or not _matches_prototype(report, prototype)):
            raise RuntimeError("The exact Prototype Drill quest and preserved stock topic did not verify")
        if target != PROTOTYPE_DRILL and prototype is not None:
            raise RuntimeError("Another authored pickup reported an unexpected Prototype Drill change")
        merge = snapshot.get(MERGE_KEY)
        if report["status"] == "merged":
            if not is_supply or not isinstance(merge, dict) or not _matches_merge(report, merge):
                raise RuntimeError("The authored supply merge did not verify its stock stack and quantity")
        elif merge is not None:
            raise RuntimeError("The authored pickup append reported an unexpected stack merge")
        if not is_cash:
            await asyncio.to_thread(native_host_save.record, host.native_profile, slot, area, flush=True)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        row = {"source": target, "map": area, "actor": actor, "class_path": item_class, "owner": character}
        if is_cash:
            row["wallet_credit"] = {key: credit[key] for key in ("amount", "before", "after", "stock_flag")}
        else:
            row["guid"] = report["guid"]
        if prototype is not None:
            row["topic_micro_drill"] = prototype["topic_after"]
        if merge is not None:
            row.update(guid=merge["stack_guid"], source_guid=report["guid"],
                       stack_before=merge["before"], stack_after=merge["after"])
        result = {"native_authored_pickup_claim": row}
        candidate = party.load_party(host.party)
        if is_cash:
            candidate["economy"].update(wallet=credit["after"], native_wallet_active=True)
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
