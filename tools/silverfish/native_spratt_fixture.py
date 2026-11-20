"""Position the exact enrolled guest for a stock Spratt shooting smoke."""

import time

from . import native_spratt, party
from .native_gold import valid_guid


AIM = "native-spratt-aim"
KEY = "native_spratt_aim"
REVIVE = "native-spratt-revive-approach"
REVIVE_KEY = "native_spratt_revive_approach"


def _scope(host, actor):
    game = host.last_game
    watched = game.get(native_spratt.KEY, {})
    target = watched.get("target") or ""
    member = host.native_recipients().get(actor)
    if (not host.fixtures or not host.native_guest or not host.native_zones
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe or host.party["wipe"]
            or (game.get("state"), game.get("map")) != ("hosting", native_spratt.AREA)
            or time.monotonic() - host.last_seen > 3 or not member or not member["connected"]
            or (watched.get("spawner"), watched.get("state"), watched.get("replicated")) != (native_spratt.SPAWNER, "watching", True)
            or not native_spratt.TARGET.fullmatch(target)):
        raise party.RuleError("Choose an enrolled guest and the watched stock Spratt")
    return member["pawn"], target


async def _command(host, bridge, *fields):
    try:
        return await bridge.command(*fields, timeout=7)
    except RuntimeError as error:
        if str(error).startswith("mutation_unverified_"):
            host.native_checkpoint_unsafe = host.stopping = True
        raise


async def aim(host, bridge, actor, guid):
    if not valid_guid(guid):
        raise party.RuleError("Choose an exact owned SG-71 A")
    pawn, target = _scope(host, actor)
    snapshot = await _command(host, bridge, AIM, pawn, guid, target)
    proof = snapshot.get(KEY, {})
    if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid"), proof.get("target"))
            != ("hosting", native_spratt.AREA, native_spratt.AREA, pawn, guid, target)):
        raise RuntimeError("The scoped Spratt aim did not verify the exact guest, stock target and held shotgun")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {KEY: proof}


async def revive(host, bridge, actor):
    pawn, target = _scope(host, actor)
    players = host.party["players"]
    if players.get("host", {}).get("downed") is not False or players.get(actor, {}).get("downed") is not True:
        raise party.RuleError("The staged Spratt rescue requires a standing host and the exact downed guest")
    snapshot = await _command(host, bridge, REVIVE, pawn, target)
    observed = snapshot.get(native_spratt.KEY, {})
    if ((snapshot.get("state"), snapshot.get("map"), observed.get("target")) != ("hosting", native_spratt.AREA, target)
            or pawn not in {entry.get("name") for entry in snapshot.get("pawns", {}).values()}):
        raise RuntimeError("The stock Spratt rescue lost its exact game and guest scope")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {REVIVE_KEY: {"map": native_spratt.AREA, "pawn": pawn, "target": target}}
