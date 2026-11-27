import re

from .native_guest_drop_record import valid_id
from .native_map_preflight import valid_campaign_package

KEY = "native_destructible_roster"
_MAX_ACTORS = 512
_MAX_ROSTER_BYTES = 30 * 1024
_ACTOR_ID = re.compile(r"[A-Za-z0-9_]{1,96}")
_STREAM_ID = re.compile(r"(/[A-Za-z0-9_/]+)\.[A-Za-z0-9_]+:PersistentLevel(?:\.[A-Za-z0-9_-]+)?")


def valid_roster(ids):
    if (not isinstance(ids, list) or len(ids) > _MAX_ACTORS
            or any(not isinstance(actor, str) or len(actor) > 384 for actor in ids)
            or sum(len(actor) + 1 for actor in ids) > _MAX_ROSTER_BYTES + 1
            or any(left >= right for left, right in zip(ids, ids[1:]))):
        return False
    scopes = set()
    for actor in ids:
        if _ACTOR_ID.fullmatch(actor):
            continue
        match = _STREAM_ID.fullmatch(actor)
        if not match or not valid_campaign_package(match[1]):
            return False
        if actor.endswith(":PersistentLevel"):
            scopes.add(actor)
        elif actor.rsplit(".", 1)[0] not in scopes:
            return False
    return True


class Playback:
    def __init__(self):
        self.applied = None

    async def project(self, bridge, game, frame, epoch):
        area = game.get("map")
        view = game.get("native_destructible_view")
        if game.get("state") != "connected" or not isinstance(view, dict) or view.get("map") != area:
            self.applied = None
            return game
        if not isinstance(frame, dict) or not valid_id(epoch) or (frame.get("map"), frame.get("epoch")) != (area, epoch):
            return game
        ids = frame.get("actors")
        if not valid_roster(ids):
            return game
        fingerprint = (area, epoch, tuple(ids))
        if view.get("epoch") == epoch and self.applied == fingerprint:
            return game
        updated = await bridge.command("native-destructible-apply", area, epoch, *(ids or ["-"]), timeout=1.5)
        if updated.get("native_destructible_view") == {"map": area, "epoch": epoch}:
            self.applied = fingerprint
        return updated
