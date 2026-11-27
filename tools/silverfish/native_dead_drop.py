"""Share the host-selected placement of the active stock Dead Drop bags."""

from dataclasses import dataclass
import re
import time

from .authored_loot import same_pose, valid_item_transform
from .travel import CITY, CROSSROADS, RUIN_FIELDS


@dataclass(frozen=True)
class DeadDrop:
    map: str
    quest: str
    source: str
    bag_asset: str | None = None

    @property
    def world(self):
        return f"{self.map}.{self.map.rsplit('/', 1)[1]}"

    @property
    def class_path(self):
        bag = self.bag_asset or f"Bag_{self.quest}"
        return f"/Game/Classes/Items/Key_Items/{bag}.{bag}_C"

    @property
    def actor(self):
        name = self.class_path.rsplit(".", 1)[1]
        return f"{name} {self.world}:PersistentLevel.{name}_1"

    def matches_pawn(self, pawn: str) -> bool:
        return isinstance(pawn, str) and re.fullmatch(
            re.escape(f"Player_FP_C {self.world}:PersistentLevel.Player_FP_C_") + r"[0-9]+", pawn) is not None

    def frame(self, epoch: str, transform: dict) -> dict:
        return {"map": self.map, "quest": self.quest, "actor": self.actor, "step": 1,
                "epoch": epoch, "transform": transform}


CROSSROADS_DROP = DeadDrop(CROSSROADS, "DeadDrop_CrossRoads", "crossroads-dead-drop-bag")
METRO_DROP = DeadDrop(CITY, "DeadDrop_Metro", "metro-dead-drop-bag")
FLOOD_TOWN_DROP = DeadDrop(RUIN_FIELDS, "DeadDrop_FloodTown", "flood-town-dead-drop-bag", "Bag_DeadDrop_FloodVillage")
BY_MAP = {drop.map: drop for drop in (CROSSROADS_DROP, METRO_DROP, FLOOD_TOWN_DROP)}
BY_SOURCE = {drop.source: drop for drop in BY_MAP.values()}
MAP, QUEST, SOURCE = CROSSROADS_DROP.map, CROSSROADS_DROP.quest, CROSSROADS_DROP.source
CLASS, WORLD, ACTOR = CROSSROADS_DROP.class_path, CROSSROADS_DROP.world, CROSSROADS_DROP.actor
TAG = "native-dead-drop"
PROJECT = "native-dead-drop-project"
CLEAR = "native-dead-drop-clear"
REPLY = "native-dead-drop-reply"
REPLY_BYTES = "native_dead_drop_reply_bytes"
KEY = "native_dead_drop"
FRAME = "native_dead_drop_frame"
PICKUPS = "native_authored_pickups"
REFRESH_SECONDS = 0.65
FRAME_MAX_AGE = 1.5
_EPOCH = re.compile(r"[0-9a-f]{32}")
_STATES = {"hosting": {"absent", "hidden", "stock-active", "claimed"},
           "connected": {"absent", "hidden", "projected"}}


def valid_epoch(value: object) -> bool:
    return isinstance(value, str) and value != "0" * 32 and _EPOCH.fullmatch(value) is not None


def parse(fields: list[str], phase: str | None, area: str | None) -> dict:
    if len(fields) not in (6, 15):
        raise ValueError("Invalid Dead Drop report size")
    tag, state, reported_map, actor, pawn, epoch, *axes = fields
    drop = BY_MAP.get(area)
    if (tag != TAG or not drop or reported_map != area or actor != drop.actor or not drop.matches_pawn(pawn)
            or state not in _STATES.get(phase, ())
            or (not valid_epoch(epoch) if state == "projected" else epoch != "-")):
        raise ValueError("Invalid Dead Drop identity")
    report = {"state": state, "map": area, "actor": actor, "pawn": pawn,
              "epoch": epoch if state == "projected" else None}
    if state in ("stock-active", "projected"):
        values = [float(axis) for axis in axes]
        pose = {"position": values[:3], "rotation": values[3:6], "scale": values[6:]}
        if not valid_item_transform(pose):
            raise ValueError("Invalid Dead Drop transform")
        report["transform"] = pose
    elif axes:
        raise ValueError("Unexpected inactive Dead Drop transform")
    return report


def parse_projection_reply(raw: str, request: tuple[str, ...]) -> dict:
    rows = [line.split("\t") for line in raw.splitlines()] if len(raw) <= 2048 and raw.endswith("\n") else []
    if (not rows or len(rows[0]) != 7 or rows[0][:4] != [REPLY, "1", request[0], "connected"]
            or rows[0][4] not in BY_MAP):
        raise RuntimeError("The Dead Drop reply did not match the guest command")
    _, _, nonce, phase, area, result, message = rows[0]
    if result != "accepted":
        raise RuntimeError(message or "The Dead Drop command was refused")
    try:
        if len(rows) != 2:
            raise ValueError("Missing or repeated Dead Drop actor")
        proof = parse(rows[1], phase, area)
        if request[1] == PROJECT:
            if (proof["state"], proof["epoch"], proof["pawn"]) != ("projected", request[2], request[3]):
                raise ValueError("Unexpected Dead Drop projection")
        elif proof["state"] not in ("hidden", "absent"):
            raise ValueError("Unexpected Dead Drop retirement")
    except (IndexError, ValueError) as error:
        raise RuntimeError("The Dead Drop command did not verify its actor") from error
    return {"state": phase, "map": area, "pawns": {"local": {"name": proof["pawn"]}}, KEY: proof,
            "ack": {"id": nonce, "result": result, "message": message}, REPLY_BYTES: len(raw.encode("utf-8"))}


def observed(game: dict, state: str) -> dict:
    drop = BY_MAP.get(game.get("map"))
    report = game.get(KEY, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if (drop and (report.get("state"), report.get("map"), report.get("actor"), report.get("pawn"))
            == (state, drop.map, drop.actor, pawn) and drop.matches_pawn(pawn)):
        return report
    return {}


def active(party: dict, drop: DeadDrop) -> bool:
    quest = party.get("native_quests", {}).get(drop.quest, {})
    step = quest.get("step")
    return (quest.get("quest_id") == drop.quest and type(step) is int and step == 1
            and drop.source not in party.get(PICKUPS, {}))


def frame(host) -> dict:
    drop = BY_MAP.get(host.last_game.get("map"))
    report = observed(host.last_game, "stock-active")
    transform = report.get("transform")
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or not drop or not active(host.party, drop) or not valid_epoch(host.zone_epoch)
            or time.monotonic() - host.last_seen >= FRAME_MAX_AGE or host.last_game.get("state") != "hosting"
            or not valid_item_transform(transform)):
        return {}
    return drop.frame(host.zone_epoch, transform)


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    drop = BY_MAP.get(game.get("map"))
    if game.get("state") != "connected" or not drop:
        return
    projected = observed(game, "projected")
    now = time.monotonic()
    frame = client.gameplay.get(FRAME)
    transform = frame.get("transform") if isinstance(frame, dict) else None
    allowed = (valid_epoch(epoch) and client.host_session and not client.needs_travel and not client.server_transitioning
               and now - client.gameplay_seen < FRAME_MAX_AGE and active(client.gameplay, drop)
               and valid_item_transform(transform) and type(frame.get("step")) is int
               and frame == drop.frame(epoch, transform))
    if not allowed:
        if projected:
            snapshot = await client.bridge.command(CLEAR, timeout=3)
            proof = observed(snapshot, "hidden") or observed(snapshot, "absent")
            if not proof or proof["pawn"] != projected["pawn"]:
                raise RuntimeError("Dead Drop did not retire to its stock hidden pose")
            if client.last_game is game:
                client.last_game = {**game, KEY: proof}
        return
    current = projected or observed(game, "hidden")
    if not current:
        return
    identity = (client.host_session, epoch, current["pawn"])
    if projected and projected["epoch"] == epoch and same_pose(projected["transform"], transform):
        prior_identity, prior_time = client.dead_drop_projection
        if identity == prior_identity and now - prior_time < REFRESH_SECONDS:
            return
    axes = (*transform["position"], *transform["rotation"], *transform["scale"])
    snapshot = await client.bridge.command(PROJECT, epoch, current["pawn"],
                                           *(str(axis) for axis in axes), timeout=3)
    proof = observed(snapshot, "projected")
    if (not proof or (proof["epoch"], proof["pawn"]) != (epoch, current["pawn"])
            or not same_pose(proof["transform"], transform)):
        raise RuntimeError("Dead Drop did not verify the local stock actor and epoch")
    if client.last_game is game:
        client.last_game = {**game, KEY: proof}
    client.dead_drop_projection = (identity, now)
