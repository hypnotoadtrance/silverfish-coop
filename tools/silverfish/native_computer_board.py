"""Start qualified stock collection contracts and redeem their exact objectives."""

import time
from dataclasses import dataclass

from . import native_authored_pickup, native_loot, native_rewards, native_wire_quest, party
from .native_gold import valid_guid
from .native_tape_profiles import PUMP, TapeProfile
from .travel import BLACK_MARKET, BOROUGH, OBSERVATORY, SCAV_BAR, SCIENTIST_SHOP, SHOPKEEP


QUEST_ID = "Clt_ComputerBoard"
PREFLIGHT = "native-computer-board-preflight"
START = "native-computer-board-start"
APPROACH = "native-computer-board-approach"
TURN_IN = "native-computer-board-turn-in"
BOARDS = {
    BLACK_MARKET: f"BountyBoard_BP_C {BLACK_MARKET}.Shop_BlackMarket_01:PersistentLevel.BountyBoard_BP_C_0",
    BOROUGH: native_wire_quest.BOARD,
    OBSERVATORY: f"BountyBoard_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.BountyBoard_BP_C_0",
    SHOPKEEP: f"BountyBoard_BP_C {SHOPKEEP}.Shop_ShopKeep_P:PersistentLevel.BountyBoard_BP_C_1",
    SCIENTIST_SHOP: f"BountyBoard_BP_C {SCIENTIST_SHOP}.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2",
    SCAV_BAR: f"BountyBoard_BP_C {SCAV_BAR}.Shop_ScavBar_P:PersistentLevel.BountyBoard_BP_C_2",
}
FACTIONS = {"2", "4", "5"}
STEPS = {None, 1, 2, -10}
BOROUGH_SCIENTIST_BOARDS = frozenset({BOROUGH, SCIENTIST_SHOP})


@dataclass(frozen=True)
class CollectionContract:
    quest_id: str
    prefix: str
    title: str
    redeem_map: str
    wrong_board: str
    cash: int
    factions: frozenset[str]
    consumed_field: str
    pickup_source: str | None = None
    board_maps: frozenset[str] = BOROUGH_SCIENTIST_BOARDS
    fixture_xp: int | None = None
    exchange: TapeProfile | None = None

    @property
    def key(self) -> str:
        return self.prefix.replace("-", "_")

    def command(self, action: str) -> str:
        return f"{self.prefix}-{action}"


COMPUTER_BOARD = CollectionContract(QUEST_ID, "native-computer-board", "Computer Board", SCIENTIST_SHOP,
                                    "scientist_redemption_required", 800, frozenset(FACTIONS), "consumed_board")
COUP_REPORT = CollectionContract("Clt_CoupReport_01", "native-coup-report", "Northern Passage", BOROUGH,
                                 "borough_redemption_required", 1200, frozenset({"2", "5"}), "consumed_note",
                                 native_authored_pickup.COUP_REPORT)
DEPOT_DOCUMENTS = CollectionContract("DepotDocs", "native-depot-documents", "Depot Documents", SCAV_BAR,
                                     "scav_bar_redemption_required", 1300, frozenset({"8"}), "consumed_documents",
                                     native_authored_pickup.DEPOT_DOCUMENTS, frozenset({SCAV_BAR}))
BRANDY = CollectionContract("Clt_Brandy", "native-brandy", "Fine Vintage", BLACK_MARKET,
                            "black_market_redemption_required", 900, frozenset({"8"}), "consumed_brandy",
                            native_authored_pickup.CITY_BRANDY, frozenset({BLACK_MARKET}))
STOLEN_BAG = CollectionContract("Clt_StolenBag", "native-stolen-bag", "Stolen Goods", BLACK_MARKET,
                                "black_market_redemption_required", 1200, frozenset({"5", "8"}), "consumed_bag",
                                native_authored_pickup.STOLEN_BAG, frozenset({BLACK_MARKET}), fixture_xp=5)
POCKET_WATCH = CollectionContract("Clt_Watch", "native-pocket-watch", "Pocket Watch", SCAV_BAR,
                                  "scav_bar_redemption_required", 1000, frozenset(), "consumed_watch",
                                  native_authored_pickup.POCKET_WATCH, frozenset({SCAV_BAR}))
SAVLO_RING = CollectionContract("Clct_EvilEye", "native-savlo-ring", "Savlo's Ring", SCAV_BAR,
                                "scav_bar_redemption_required", 20000, frozenset({"5"}), "consumed_ring",
                                native_authored_pickup.SAVLO_RING, frozenset({SCAV_BAR}), fixture_xp=10)
PUMP_ROOM = CollectionContract("Clt_EatherPumpTape", "native-pump-room", "Pump Room", BLACK_MARKET,
                                "black_market_redemption_required", 3200, frozenset({"8", "3"}), "consumed_tape",
                                board_maps=frozenset({BLACK_MARKET}), fixture_xp=8, exchange=PUMP)
MOLASSES = CollectionContract("Clt_Molasis", "native-molasses", "Get Molasses", SHOPKEEP,
                              "shopkeep_redemption_required", 30, frozenset(), "consumed_molasses",
                              native_authored_pickup.MOLASSES, frozenset({SHOPKEEP}))
SAVLO_KEYCARD = CollectionContract("Clct_SavloKey", "native-savlo-keycard", "Hall Pass", OBSERVATORY,
                                   "observatory_redemption_required", 50000, frozenset({"8"}), "consumed_keycard",
                                   native_authored_pickup.SAVLO_KEYCARD, frozenset({OBSERVATORY}), fixture_xp=14)
CONTRACTS = {spec.quest_id: spec for spec in (
    COMPUTER_BOARD, COUP_REPORT, DEPOT_DOCUMENTS, BRANDY, STOLEN_BAG, POCKET_WATCH, SAVLO_RING, PUMP_ROOM, MOLASSES, SAVLO_KEYCARD,
)}
PREFIXES = {spec.prefix: spec for spec in CONTRACTS.values()}
PREFLIGHTS = {spec.command("preflight"): spec for spec in CONTRACTS.values()}
AUTHORED_APPROACHES = {spec.command("approach"): spec for spec in CONTRACTS.values() if spec.pickup_source or spec.exchange}
XP_FIXTURES = {spec.command("xp-fixture"): spec for spec in CONTRACTS.values() if spec.fixture_xp is not None}


def _scope(host, actor: str, spec: CollectionContract, *, turning_in: bool = False) -> tuple[str, str]:
    player = host.party["players"].get(actor)
    area = host.last_game.get("map")
    available_boards = BOARDS if turning_in else spec.board_maps
    if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning
            or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or area not in available_boards
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError(f"{spec.key}_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError(f"{spec.key}_interactor_unavailable")
    return pawn, area


async def _command(host, bridge, spec: CollectionContract, action: str, pawn: str, area: str, *arguments: str) -> dict:
    verb = spec.command(action)
    refusal = "stock_" + spec.key.removeprefix("native_") + "_"
    try:
        snapshot = await bridge.command(verb, pawn, *arguments, timeout=8 if action == "turn-in" else 5)
    except RuntimeError as error:
        if str(error).startswith(refusal):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get(spec.key, {})
    if not isinstance(proof, dict) or (snapshot.get("state"), snapshot.get("map"), *(proof.get(field) for field in
            ("operation", "map", "pawn", "board", "quest", "can_turn_in"))) != (
            "hosting", area, verb, area, pawn, BOARDS[area], spec.quest_id, area == spec.redeem_map):
        raise RuntimeError(f"The stock {spec.title} report did not match the exact board and player")
    reputation = proof.get("reputation", {})
    if (not isinstance(reputation, dict) or reputation.keys() != spec.factions
            or type(proof.get("can_turn_in")) is not bool
            or any(not isinstance(row, dict) or type(row.get("before")) is not int or type(row.get("after")) is not int
                   for row in reputation.values())
            or any(field not in proof or proof[field] is not None and
                   (type(proof[field]) is not int or proof[field] not in STEPS) for field in ("step_before", "step_after"))
            or any(type(proof.get(field)) is not int for field in ("cash_before", "cash_after", "xp_before", "xp_after"))):
        raise RuntimeError(f"The stock {spec.title} report omitted the authoritative quest balances")
    guid, owner = proof.get("objective_guid"), proof.get("objective_owner")
    if ((guid not in (None, "-") or owner not in (None, "-")) and (not valid_guid(guid) or owner not in ("host", "guest"))
            or action == "preflight" and (any(proof[f"{field}_before"] != proof[f"{field}_after"] for field in ("step", "cash", "xp"))
                                      or any(row["before"] != row["after"] for row in reputation.values()))):
        raise RuntimeError(f"The stock {spec.title} report did not preserve preflight state and item custody")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


def _objective_owned(host, actor: str, spec: CollectionContract, proof: dict) -> bool:
    guid = proof.get("objective_guid")
    if not valid_guid(guid) or proof.get("objective_owner") != ("host" if actor == "host" else "guest"):
        return False
    if profile := spec.exchange:
        return any(
            (trade.get("map"), trade.get("station"), trade.get("class_path"), trade.get("filled_guid"), trade.get("owner"))
            == (profile.map, profile.station, profile.product, guid, actor)
            for trade in host.party.get(profile.ledger, {}).values()
        )
    if spec.pickup_source is None:
        return True
    pickup = host.party.get(native_authored_pickup.STATE_KEY, {}).get(spec.pickup_source, {})
    area, placed_actor, item_class = native_authored_pickup.SOURCES[spec.pickup_source]
    return tuple(pickup.get(key) for key in ("source", "map", "actor", "class_path", "guid", "owner")) == (
        spec.pickup_source, area, placed_actor, item_class, guid, actor)


async def preflight(host, bridge, actor: str, *, spec: CollectionContract = COMPUTER_BOARD) -> dict:
    pawn, area = _scope(host, actor, spec)
    proof = await _command(host, bridge, spec, "preflight", pawn, area)
    if spec.pickup_source or spec.exchange:
        proof = dict(proof, objective_qualified=_objective_owned(host, actor, spec, proof))
    return {spec.key: proof}


async def approach(host, bridge, actor: str, *, spec: CollectionContract = COMPUTER_BOARD) -> dict:
    pawn, area = _scope(host, actor, spec)
    if not host.fixtures:
        raise party.RuleError(f"{spec.key}_approach_unavailable")
    snapshot = await bridge.command(spec.command("approach"), pawn, timeout=5)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", area):
        raise RuntimeError(f"The {spec.title} approach left the qualified listen world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {f"{spec.key}_approach": {"pawn": pawn, "board": BOARDS[area]}}


async def xp_fixture(host, bridge, spec: CollectionContract) -> dict:
    pawn, area = _scope(host, "host", spec)
    if not host.fixtures or spec.fixture_xp is None or host.party.get("native_quests"):
        raise party.RuleError(f"{spec.key}_xp_fixture_unavailable")
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        proof = await _command(host, bridge, spec, "xp-fixture", pawn, area)
        if ((proof["step_before"], proof["step_after"], proof["xp_before"], proof["xp_after"])
                != (None, None, 0, spec.fixture_xp) or proof["cash_before"] != proof["cash_after"]
                or any(row["before"] != row["after"] for row in proof["reputation"].values())):
            raise RuntimeError(f"The stock {spec.title} fixture did not verify XP-only eligibility")
        revision = await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = False
        return {spec.key: proof, "revision": revision}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise


async def start(host, bridge, actor: str, *, spec: CollectionContract = COMPUTER_BOARD) -> dict:
    return await _commit(host, bridge, actor, spec, turning_in=False)


async def turn_in(host, bridge, actor: str, *, spec: CollectionContract = COMPUTER_BOARD) -> dict:
    return await _commit(host, bridge, actor, spec, turning_in=True)


async def _commit(host, bridge, actor: str, spec: CollectionContract, *, turning_in: bool) -> dict:
    pawn, area = _scope(host, actor, spec, turning_in=turning_in)
    refusal = "stock_" + spec.key.removeprefix("native_") + "_"
    if turning_in and area != spec.redeem_map:
        raise party.RuleError(refusal + spec.wrong_board)
    receipt = host.party.get("native_quests", {}).get(spec.quest_id, {})
    if turning_in and receipt.get("step") == -10:
        return {"effect": "native_quest_already_settled", **receipt}

    host.transitioning = True
    try:
        before = await _command(host, bridge, spec, "preflight", pawn, area)
        step = before["step_after"]
        if not turning_in and step is not None:
            return {"effect": "native_quest_present", "quest_id": spec.quest_id, "step": step}
        if turning_in and step == -10:
            return {"effect": "native_quest_already_completed", "quest_id": spec.quest_id, "step": step}

        arguments = ()
        if turning_in:
            if step not in (1, 2):
                raise party.RuleError(refusal + "quest_not_active")
            guid = before.get("objective_guid")
            owner = "host" if actor == "host" else "guest"
            if not _objective_owned(host, actor, spec, before):
                raise party.RuleError(refusal + "objective_required")
            arguments = (guid, owner)

        await host.checkpoint_native(bridge)
        recipients = sorted(set(host.roles().values()) & host.party["players"].keys()) if turning_in else []
        if turning_in and actor not in recipients:
            raise party.RuleError(f"{spec.key}_interactor_unavailable")
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, spec, "turn-in" if turning_in else "start", pawn, area, *arguments)
        cash = proof["cash_after"] - proof["cash_before"]
        xp = proof["xp_after"] - proof["xp_before"]
        expected_step = -10 if turning_in else 1
        reputation = proof["reputation"]
        if ((proof["step_before"], proof["step_after"], cash, xp) !=
                (step, expected_step, spec.cash if turning_in else 0, 1 if turning_in else 0)
                or proof["cash_before"] != before["cash_after"] or proof["xp_before"] != before["xp_after"]
                or any(row["before"] != before["reputation"][faction]["after"]
                       or not turning_in and row["before"] != row["after"] for faction, row in reputation.items())
                or turning_in and (proof.get("objective_guid"), proof.get("objective_owner")) != arguments):
            raise RuntimeError(f"The stock {spec.title} transition did not reconcile with its authoritative preflight")

        candidate = party.load_party(host.party)
        record = {"quest_id": spec.quest_id, "step": expected_step, "actor": actor}
        if turning_in:
            for character in recipients:
                player = candidate["players"][character]
                player["quest_xp"] = player.get("quest_xp", 0) + xp
                if character != "host":
                    native_rewards.queue(player, xp)
            candidate["economy"].update(wallet=proof["cash_after"], native_wallet_active=True)
            record.update(recipients=recipients, cash=cash, xp=xp, wallet=proof["cash_after"],
                          reputation={faction: row["after"] for faction, row in reputation.items()},
                          objective_owner=actor)
            record[spec.consumed_field] = arguments[0]
        candidate.setdefault("native_quests", {})[spec.quest_id] = record
        required = frozenset({actor}) if turning_in and actor != "host" else frozenset()
        revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=required)
        host.party = candidate
        host.native_checkpoint_unsafe = False
        event = spec.key + ("_completed" if turning_in else "_started")
        host.control.event(event, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, spec.key: proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
