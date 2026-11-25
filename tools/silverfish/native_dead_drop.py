"""Share the host-selected placement of the active stock Crossroads Dead Drop."""

import re
import time

from .authored_loot import same_pose, valid_item_transform
from .travel import CROSSROADS


MAP = CROSSROADS
QUEST = "DeadDrop_CrossRoads"
SOURCE = "crossroads-dead-drop-bag"
CLASS = "/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C"
WORLD = f"{MAP}.CrossRoads_P"
ACTOR = f"Bag_DeadDrop_CrossRoads_C {WORLD}:PersistentLevel.Bag_DeadDrop_CrossRoads_C_1"
TAG = "native-dead-drop"
KEY = "native_dead_drop"
FRAME = "native_dead_drop_frame"
PICKUPS = "native_authored_pickups"
REFRESH_SECONDS = 0.65
FRAME_MAX_AGE = 1.5
_PAWN = re.compile(re.escape(f"Player_FP_C {WORLD}:PersistentLevel.Player_FP_C_") + r"[0-9]+")
_EPOCH = re.compile(r"[0-9a-f]{32}")
_STATES = {"hosting": {"absent", "hidden", "stock-active", "claimed"},
           "connected": {"absent", "hidden", "projected"}}


def valid_epoch(value: object) -> bool:
    return isinstance(value, str) and value != "0" * 32 and _EPOCH.fullmatch(value) is not None


def parse(fields: list[str], phase: str | None, area: str | None) -> dict:
    if len(fields) not in (6, 15):
        raise ValueError("Invalid CrossRoads Dead Drop report size")
    tag, state, reported_map, actor, pawn, epoch, *axes = fields
    if (tag != TAG or area != MAP or reported_map != area or actor != ACTOR or _PAWN.fullmatch(pawn) is None
            or state not in _STATES.get(phase, ())
            or (not valid_epoch(epoch) if state == "projected" else epoch != "-")):
        raise ValueError("Invalid CrossRoads Dead Drop identity")
    report = {"state": state, "map": area, "actor": actor, "pawn": pawn,
              "epoch": epoch if state == "projected" else None}
    if state in ("stock-active", "projected"):
        values = [float(axis) for axis in axes]
        pose = {"position": values[:3], "rotation": values[3:6], "scale": values[6:]}
        if not valid_item_transform(pose):
            raise ValueError("Invalid CrossRoads Dead Drop transform")
        report["transform"] = pose
    elif axes:
        raise ValueError("Unexpected inactive CrossRoads Dead Drop transform")
    return report


def observed(game: dict, state: str) -> dict:
    report = game.get(KEY, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if (game.get("map") == MAP and (report.get("state"), report.get("map"), report.get("actor"), report.get("pawn"))
            == (state, MAP, ACTOR, pawn) and isinstance(pawn, str) and _PAWN.fullmatch(pawn)):
        return report
    return {}


def active(party: dict) -> bool:
    quest = party.get("native_quests", {}).get(QUEST, {})
    step = quest.get("step")
    return quest.get("quest_id") == QUEST and type(step) is int and step == 1 and SOURCE not in party.get(PICKUPS, {})


def frame(host) -> dict:
    report = observed(host.last_game, "stock-active")
    transform = report.get("transform")
    if (not host.native_guest or not host.native_zones or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or not active(host.party) or not valid_epoch(host.zone_epoch)
            or time.monotonic() - host.last_seen >= FRAME_MAX_AGE or host.last_game.get("state") != "hosting"
            or not valid_item_transform(transform)):
        return {}
    return {"map": MAP, "quest": QUEST, "actor": ACTOR, "step": 1, "epoch": host.zone_epoch, "transform": transform}


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    if game.get("state") != "connected" or game.get("map") != MAP:
        return
    projected = observed(game, "projected")
    now = time.monotonic()
    frame = client.gameplay.get(FRAME)
    transform = frame.get("transform") if isinstance(frame, dict) else None
    allowed = (valid_epoch(epoch) and client.host_session and not client.needs_travel and not client.server_transitioning
               and now - client.gameplay_seen < FRAME_MAX_AGE and active(client.gameplay)
               and valid_item_transform(transform) and type(frame.get("step")) is int
               and frame == {"map": MAP, "quest": QUEST, "actor": ACTOR, "step": 1, "epoch": epoch,
                             "transform": transform})
    if not allowed:
        if projected:
            snapshot = await client.bridge.command("native-dead-drop-clear", timeout=3)
            if not observed(snapshot, "hidden") and not observed(snapshot, "absent"):
                raise RuntimeError("CrossRoads Dead Drop did not retire to its stock hidden pose")
            client.last_game = snapshot
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
    snapshot = await client.bridge.command("native-dead-drop-project", epoch, current["pawn"],
                                           *(str(axis) for axis in axes), timeout=3)
    proof = observed(snapshot, "projected")
    if (not proof or (proof["epoch"], proof["pawn"]) != (epoch, current["pawn"])
            or not same_pose(proof["transform"], transform)):
        raise RuntimeError("CrossRoads Dead Drop did not verify the local stock actor and epoch")
    client.last_game = snapshot
    client.dead_drop_projection = (identity, now)
