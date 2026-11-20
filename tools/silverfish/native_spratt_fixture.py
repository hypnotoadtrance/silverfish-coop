"""Position the exact enrolled guest for a stock Spratt shooting smoke."""

import time

from . import native_spratt, party
from .native_gold import valid_guid


AIM = "native-spratt-aim"
KEY = "native_spratt_aim"


async def aim(host, bridge, actor, guid):
    game = host.last_game
    watched = game.get(native_spratt.KEY, {})
    target = watched.get("target") or ""
    member = host.native_recipients().get(actor)
    if (not host.fixtures or not host.native_guest or not host.native_zones or not valid_guid(guid)
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or (game.get("state"), game.get("map")) != ("hosting", native_spratt.AREA)
            or time.monotonic() - host.last_seen > 3 or not member or not member["connected"]
            or (watched.get("spawner"), watched.get("state"), watched.get("replicated")) != (native_spratt.SPAWNER, "watching", True)
            or not native_spratt.TARGET.fullmatch(target)):
        raise party.RuleError("Choose an enrolled guest with an owned SG-71 A and the watched stock Spratt")
    pawn = member["pawn"]
    try:
        snapshot = await bridge.command(AIM, pawn, guid, target, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise
    proof = snapshot.get(KEY, {})
    if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid"), proof.get("target"))
            != ("hosting", native_spratt.AREA, native_spratt.AREA, pawn, guid, target)):
        raise RuntimeError("The scoped Spratt aim did not verify the exact guest, stock target and held shotgun")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {KEY: proof}
