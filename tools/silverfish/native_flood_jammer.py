"""Verify authored signal jammer objectives and settle their stock bounties once."""

from dataclasses import dataclass
import time

from . import native_loot, native_rewards, native_spratt, native_wire_quest, party
from .authored_loot import valid_item_transform
from .native_gold import valid_guid
from .travel import BLACK_MARKET, BOROUGH, LAKE, RAIL_YARD


@dataclass(frozen=True)
class StockKillContract:
    quest: str
    key: str
    board_map: str
    board: str
    objective_map: str
    cash: int
    factions: frozenset[str]
    fixture_xp: int | None = None

    def command(self, verb=""):
        base = self.key.replace("_", "-")
        return f"{base}-{verb}" if verb else base


@dataclass(frozen=True)
class JammerProfile(StockKillContract):
    @property
    def jammer(self):
        return self._actor("SignalJammer_BP_C")

    @property
    def trigger(self):
        return self._actor("Quest_Trigger_BP_C")

    def _actor(self, kind):
        level = self.objective_map.rsplit("/", 1)[1]
        return f"{kind} {self.objective_map}.{level}:PersistentLevel.{kind}_1"


FLOOD_PLAINS = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
FLOOD = JammerProfile("Jmr_FloodPlains", "native_flood_jammer", BOROUGH, native_wire_quest.BOARD, FLOOD_PLAINS, 350, frozenset({"2", "5"}))
BLACK_MARKET_BOARD = f"BountyBoard_BP_C {BLACK_MARKET}.Shop_BlackMarket_01:PersistentLevel.BountyBoard_BP_C_0"
BLACK_LAKE = JammerProfile("Jmr_Lake", "native_lake_jammer", BLACK_MARKET, BLACK_MARKET_BOARD, LAKE, 400, frozenset({"5", "8"}))
LENNY = StockKillContract("Asn_Lenny_01", "native_lenny", BLACK_MARKET, BLACK_MARKET_BOARD, RAIL_YARD, 2500, frozenset({"5", "8"}))
SPRATT = StockKillContract(native_spratt.QUEST, "native_spratt", BLACK_MARKET, BLACK_MARKET_BOARD,
                           native_spratt.AREA, 5000, frozenset({"5", "8"}), fixture_xp=7)
CONTRACTS = {spec.quest: spec for spec in (FLOOD, BLACK_LAKE, LENNY, SPRATT)}
OBJECTIVES = {spec.objective_map: spec for spec in (FLOOD, BLACK_LAKE)}
COMMANDS = {
    spec.command(verb): spec
    for spec in CONTRACTS.values() for verb in ("preflight", "approach", "start", "turn-in")
}
COMMANDS.update((spec.command("aim"), spec) for spec in OBJECTIVES.values())
PREFLIGHTS = frozenset(spec.command("preflight") for spec in CONTRACTS.values())
APPROACHES = frozenset(spec.command("approach") for spec in CONTRACTS.values())
AIMS = frozenset(spec.command("aim") for spec in OBJECTIVES.values())
XP_FIXTURES = {spec.command("xp-fixture"): spec for spec in CONTRACTS.values() if spec.fixture_xp is not None}

QUEST_ID, JAMMER, TRIGGER = FLOOD.quest, FLOOD.jammer, FLOOD.trigger
PREFLIGHT, APPROACH, AIM, START, TURN_IN = map(FLOOD.command, ("preflight", "approach", "aim", "start", "turn-in"))
STEPS = {None, -10, -7, 1, 2}
POSE = "native_jammer_pose"


def _pawn(host, actor, spec):
    player = host.party["players"].get(actor)
    if (not host.native_profile or not host.native_zones or not host.native_guest
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") != spec.board_map
            or time.monotonic() - host.last_seen > 3 or not player or player["downed"]
            or actor in host.party["pending_spawns"]):
        raise party.RuleError(f"{spec.key}_board_unavailable")
    pawn = native_loot._scope(host, actor)
    if not pawn:
        raise party.RuleError(f"{spec.key}_actor_unavailable")
    return pawn


async def _command(host, bridge, action, pawn, spec):
    try:
        snapshot = await bridge.command(action, pawn, timeout=8)
    except RuntimeError as error:
        if str(error).startswith(f"stock_{spec.key.removeprefix('native_')}_"):
            host.native_checkpoint_unsafe = False
            raise party.RuleError(str(error)) from error
        raise
    proof = snapshot.get(spec.key, {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in ("operation", "map", "pawn", "board", "quest"))) != (
            "hosting", spec.board_map, action, spec.board_map, pawn, spec.board, spec.quest
    ) or proof.get("reputation", {}).keys() != spec.factions:
        raise RuntimeError("The stock jammer board did not report the exact authored contract")
    if (any(key not in proof or proof[key] not in STEPS for key in ("step_before", "step_after"))
            or any(type(proof.get(key)) is not int for key in ("cash_before", "cash_after", "xp_before", "xp_after"))
            or any(type(row.get(field)) is not int for row in proof["reputation"].values() for field in ("before", "after"))):
        raise RuntimeError("The stock jammer board did not report quest and wallet readback")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, actor, spec=FLOOD):
    pawn = _pawn(host, actor, spec)
    proof = await _command(host, bridge, spec.command("preflight"), pawn, spec)
    if (any(proof[f"{field}_before"] != proof[f"{field}_after"] for field in ("step", "cash", "xp"))
            or any(row["before"] != row["after"] for row in proof["reputation"].values())):
        raise RuntimeError("The stock jammer preflight changed campaign state")
    if spec is SPRATT:
        proof = dict(proof, objective_qualified=_objective_ready(host.party.get("native_quests", {}).get(spec.quest, {}), spec))
    return {spec.key: proof}


async def approach(host, bridge, actor, spec=FLOOD):
    pawn = _pawn(host, actor, spec)
    if not host.fixtures:
        raise party.RuleError(f"{spec.key}_approach_unavailable")
    snapshot = await bridge.command(spec.command("approach"), pawn, timeout=5)
    if (snapshot.get("state"), snapshot.get("map")) != ("hosting", spec.board_map):
        raise RuntimeError("The stock jammer board approach left the qualified world")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {f"{spec.key}_approach": {"pawn": pawn, "board": spec.board}}


async def xp_fixture(host, bridge, spec):
    pawn = _pawn(host, "host", spec)
    if not host.fixtures or spec.fixture_xp is None or host.party.get("native_quests"):
        raise party.RuleError(f"{spec.key}_xp_fixture_unavailable")
    await host.checkpoint_native(bridge)
    host.native_checkpoint_unsafe = True
    try:
        proof = await _command(host, bridge, spec.command("xp-fixture"), pawn, spec)
        if ((proof["step_before"], proof["step_after"], proof["xp_before"], proof["xp_after"])
                != (None, None, 0, spec.fixture_xp) or proof["cash_before"] != proof["cash_after"]
                or any(row["before"] != row["after"] for row in proof["reputation"].values())):
            raise RuntimeError("The stock Spratt fixture did not verify XP-only eligibility")
        revision = await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = False
        return {spec.key: proof, "revision": revision}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise


async def aim_fixture(host, bridge, actor, guid, spec=FLOOD):
    member = host.native_recipients().get(actor)
    if (not host.fixtures or not host.native_guest or not host.native_zones or not valid_guid(guid)
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.last_game.get("map") != spec.objective_map or not member or not member["connected"]):
        raise party.RuleError("Choose the exact enrolled guest and their owned stock pistol in the authored objective zone")
    pawn = member["pawn"]
    try:
        snapshot = await bridge.command(spec.command("aim"), pawn, guid, timeout=6)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    proof = snapshot.get(f"{spec.key}_aim", {})
    if (snapshot.get("state"), snapshot.get("map"), *(proof.get(key) for key in ("map", "pawn", "jammer", "guid"))) != (
        "hosting", spec.objective_map, spec.objective_map, pawn, spec.jammer, guid
    ):
        raise RuntimeError("The stock jammer aim did not verify the exact equipped pistol and target")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {f"{spec.key}_aim": proof}


def _credit(host, candidate, amount):
    recipients = sorted(set(host.roles().values()) & candidate["players"].keys())
    for member in recipients:
        player = candidate["players"][member]
        player["quest_xp"] = player.get("quest_xp", 0) + amount
        if member != "host":
            native_rewards.queue(player, amount)
    return recipients


def _objective_ready(record, spec):
    if record.get("step") != 2 or "objective" not in record:
        return False
    if spec is not SPRATT:
        return True
    proof = record["objective"]
    if not isinstance(proof, dict) or not isinstance(proof.get("target"), str):
        return False
    return (record.get("quest_id") == spec.quest and native_spratt.TARGET.fullmatch(proof["target"]) is not None
            and (proof.get("map"), proof.get("spawner"), proof.get("state"), proof.get("step_before"),
                 proof.get("step_after"), proof.get("callback_count")) ==
                (spec.objective_map, native_spratt.SPAWNER, native_spratt.VERIFIED, 1, 2, 1)
            and all(type(proof.get(field)) is int for field in ("cash_before", "cash_after", "xp_before", "xp_after"))
            and proof["cash_before"] == proof["cash_after"] and proof["xp_before"] == proof["xp_after"])


async def act(host, bridge, actor, action):
    spec = COMMANDS.get(action)
    if not spec or action not in (spec.command("start"), spec.command("turn-in")):
        raise party.RuleError("Choose a supported stock jammer operation")
    pawn = _pawn(host, actor, spec)
    current = host.party.get("native_quests", {}).get(spec.quest, {})
    starting = action == spec.command("start")
    if starting and current:
        return {"effect": "native_quest_present", **current}
    if not starting and current.get("step") == -10:
        return {"effect": "native_quest_already_settled", **current}
    expected = (None, 1, 0, 0) if starting else (2, -10, spec.cash, 1)
    if not starting and not _objective_ready(current, spec):
        raise party.RuleError(f"{spec.key}_objective_incomplete")
    host.transitioning = True
    try:
        before = await _command(host, bridge, spec.command("preflight"), pawn, spec)
        if before["step_after"] != expected[0]:
            raise party.RuleError(f"{spec.key}_stock_step_changed")
        if spec is SPRATT and starting and before["xp_after"] < spec.fixture_xp:
            raise party.RuleError("stock_spratt_quest_not_offered")
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        proof = await _command(host, bridge, action, pawn, spec)
        cash = proof["cash_after"] - proof["cash_before"]
        xp = proof["xp_after"] - proof["xp_before"]
        if ((proof["step_before"], proof["step_after"], cash, xp) != expected
                or any(proof[f"{field}_before"] != before[f"{field}_after"] for field in ("cash", "xp"))
                or any(row["before"] != before["reputation"][faction]["after"]
                       or starting and row["before"] != row["after"]
                       for faction, row in proof["reputation"].items())):
            raise RuntimeError("The stock jammer board transition did not verify")
        candidate = party.load_party(host.party)
        record = {**current, "quest_id": spec.quest, "step": proof["step_after"], "actor": actor}
        if not starting:
            recipients = _credit(host, candidate, xp)
            candidate["economy"].update(wallet=proof["cash_after"], native_wallet_active=True)
            record.update(recipients=recipients, cash=cash, xp=xp, wallet=proof["cash_after"],
                          reputation={key: row["after"] for key, row in proof["reputation"].items()})
        candidate.setdefault("native_quests", {})[spec.quest] = record
        revision = await host.checkpoint_native(bridge, candidate)
        host.party, host.native_checkpoint_unsafe = candidate, False
        host.control.event(f"{spec.key}_board_committed", operation=action, revision=revision, **record)
        return {"effect": "native_quest_changed", **record, spec.key: proof}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False


async def observe(host, bridge):
    snapshot = host.last_game
    spec = OBJECTIVES.get(snapshot.get("map"))
    if (not spec or not host.native_guest or not host.native_zones or not host.native_profile or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe
            or snapshot.get("state") != "hosting"):
        return
    proof = snapshot.get(f"{spec.key}_objective", {})
    if (proof.get("map"), proof.get("jammer"), proof.get("trigger")) != (spec.objective_map, spec.jammer, spec.trigger):
        return
    if proof.get("state", "").startswith("mutation_unverified_"):
        host.native_checkpoint_unsafe = host.stopping = True
        raise RuntimeError("The stock jammer changed game state without a verified objective readback")
    expected = {"stock_objective_verified": (1, 2, 0), "stock_force_complete_verified": (None, -7, 1)}.get(proof.get("state"))
    if not expected:
        return
    current = host.party.get("native_quests", {}).get(spec.quest, {})
    if current.get("step") != expected[0]:
        return
    if (any(type(proof.get(key)) is not int for key in ("xp_before", "xp_after"))
            or (proof.get("step_before"), proof.get("step_after"), proof["xp_after"] - proof["xp_before"]) != expected):
        host.native_checkpoint_unsafe = True
        raise RuntimeError("The authored jammer signal diverged from its verified stock progress")
    shooter = proof["shooter"]
    if proof["shooter_role"] == "host":
        if shooter != snapshot.get("pawns", {}).get("local", {}).get("name"):
            return
        actor = "host"
    elif proof["shooter_role"] == "guest":
        actor = next((owner for owner, member in host.native_recipients().items() if member["pawn"] == shooter), None)
        if not actor:
            return
    else:
        actor = "world"
    candidate = party.load_party(host.party)
    record = {**current, "quest_id": spec.quest, "step": expected[1], "actor": actor, "objective": proof}
    if expected[2]:
        record.update(recipients=_credit(host, candidate, expected[2]), xp=expected[2], cash=0)
    candidate.setdefault("native_quests", {})[spec.quest] = record
    host.native_checkpoint_unsafe = True
    try:
        revision = await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party, host.native_checkpoint_unsafe = candidate, False
    host.control.event(f"{spec.key}_objective_committed", revision=revision, **record)


def _pose_matches_scope(pose, spec):
    return (isinstance(pose, dict) and (pose.get("map"), pose.get("actor")) == (spec.objective_map, spec.jammer)
            and valid_item_transform(pose.get("transform")))


def parse_pose(fields, phase, area, spec):
    if (len(fields) != 12 or not isinstance(spec, JammerProfile)
            or phase not in ("hosting", "connected") or area != spec.objective_map):
        raise ValueError("Invalid authored jammer pose scope")
    _, target_map, actor, *axes = fields
    values = list(map(float, axes))
    pose = dict(map=target_map, actor=actor,
                transform=dict(position=values[:3], rotation=values[3:6], scale=values[6:]))
    if not _pose_matches_scope(pose, spec):
        raise ValueError("Invalid authored jammer pose")
    return pose


def pose_frame(game, epoch):
    spec = OBJECTIVES.get(game.get("map"))
    pose = game.get(POSE)
    if (spec and game.get("state") == "hosting" and _pose_matches_scope(pose, spec)
            and isinstance(epoch, str) and party.REQUEST_ID.fullmatch(epoch)):
        return {**pose, "epoch": epoch}


def same_pose(first, second):
    return (
        all(abs(a - b) <= .1 for a, b in zip(first["position"], second["position"], strict=True))
        and all(abs((a - b + 180) % 360 - 180) <= .1 for a, b in zip(first["rotation"], second["rotation"], strict=True))
        and all(abs(a - b) <= .005 for a, b in zip(first["scale"], second["scale"], strict=True))
    )


async def _project_pose(client, game, spec):
    desired, local = client.gameplay.get(POSE), game.get(POSE)
    if (not _pose_matches_scope(desired, spec) or desired.get("epoch") != client.zone_epoch
            or not _pose_matches_scope(local, spec) or same_pose(local["transform"], desired["transform"])):
        return
    transform = desired["transform"]
    axes = (*transform["position"], *transform["rotation"], *transform["scale"])
    snapshot = await client.bridge.command(spec.command("project"), "pose", *(str(axis) for axis in axes), timeout=3)
    report = snapshot.get(POSE)
    if ((snapshot.get("state"), snapshot.get("map")) != ("connected", spec.objective_map)
            or not _pose_matches_scope(report, spec) or not same_pose(report["transform"], transform)):
        raise RuntimeError("The guest did not verify the authored jammer's host pose")
    client.last_game = snapshot


async def project_client(client):
    game = client.last_game
    spec = OBJECTIVES.get(game.get("map"))
    if not spec or game.get("state") != "connected":
        return
    key = f"{spec.key}_projection"
    local = game.get(key, {})
    record = client.gameplay.get("native_quests", {}).get(spec.quest, {})
    objective = record.get("objective", {})
    if (local.get("map"), local.get("actor"), local.get("state")) != (spec.objective_map, spec.jammer, "present"):
        return
    destroyed = (record.get("step") in (2, -7, -10)
                 and (objective.get("map"), objective.get("jammer"), objective.get("trigger"))
                 == (spec.objective_map, spec.jammer, spec.trigger)
                 and objective.get("state") in ("stock_objective_verified", "stock_force_complete_verified"))
    if not destroyed:
        return await _project_pose(client, game, spec)
    snapshot = await client.bridge.command(spec.command("project"), "destroyed", timeout=3)
    proof = snapshot.get(key, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("actor")) != (
        "connected", spec.objective_map, spec.objective_map, spec.jammer
    ) or proof.get("state") not in ("hidden", "absent"):
        raise RuntimeError("The guest did not verify the destroyed stock jammer")
    client.last_game = snapshot
    client.control.event(f"{spec.key}_projected", **proof)
