"""Route the authored Observatory lantern's stock interaction and light state."""

import re

from . import native_loot, party
from .travel import OBSERVATORY


TAG = "native-observatory-lantern"
KEY = "native_observatory_lantern"
ACTION = "native-observatory-lantern-light"
EXTINGUISH = "native-observatory-lantern-extinguish"
FIXTURE = "native-observatory-lantern-fixture"
ACTOR = f"Prop_Lantern_01_C {OBSERVATORY}.Observitory_P:PersistentLevel.Prop_Lantern_01_C_1"
_INTENT = re.compile(r"observatory-lantern:(-?[0-9]{1,3}\.[0-9]{2}):(-?[0-9]{1,3}\.[0-9]{2})\Z")


def target_angles(target):
    match = _INTENT.fullmatch(target) if isinstance(target, str) else None
    if match and abs(float(match[1])) <= 89.9 and abs(float(match[2])) <= 180:
        return match.groups()
    return None


async def extinguish(host, bridge, character, requested):
    epoch, _, target = requested.partition("@") if isinstance(requested, str) else ("", "", "")
    angles = target_angles(target)
    player = host.party["players"].get(character, {})
    pawn = native_loot._scope(host, character)
    if (not angles or not pawn or not player or player.get("downed") or host.party.get("wipe") or host.transitioning
            or host.stopping or epoch != host.zone_epoch or host.last_game.get("map") != OBSERVATORY):
        raise party.RuleError("native_observatory_lantern_extinguish_unavailable")
    snapshot = await bridge.command(EXTINGUISH, pawn, timeout=4)
    result = frame(snapshot, epoch)
    if not result or result["lit"] is not False:
        raise RuntimeError("The host could not verify the stock lantern interaction")
    host.last_game = snapshot
    return {KEY: result}


def parse(fields, phase, area):
    if (len(fields) != 9 or phase not in ("hosting", "connected") or area != OBSERVATORY
            or fields[:3] != [TAG, OBSERVATORY, ACTOR] or any(value not in ("0", "1") for value in fields[3:6])):
        raise ValueError("Invalid Observatory lantern report")
    position = list(map(float, fields[6:]))
    if any(not -1e7 <= axis <= 1e7 for axis in position):
        raise ValueError("Invalid Observatory lantern position")
    lit, held, movement = (value == "1" for value in fields[3:6])
    return dict(map=area, actor=ACTOR, lit=lit, held=held, movement=movement, position=position)


def frame(game, epoch):
    lantern = game.get(KEY, {})
    if ((game.get("state"), game.get("map"), lantern.get("actor")) == ("hosting", OBSERVATORY, ACTOR)
            and lantern.get("map") == OBSERVATORY and type(lantern.get("lit")) is bool
            and isinstance(epoch, str) and party.REQUEST_ID.fullmatch(epoch)):
        return dict(map=OBSERVATORY, actor=ACTOR, epoch=epoch, lit=lantern["lit"])


async def project(bridge, game, state, epoch):
    local = game.get(KEY, {})
    if (not isinstance(state, dict) or (state.get("map"), state.get("actor"), state.get("epoch")) != (OBSERVATORY, ACTOR, epoch)
            or (game.get("map"), game.get("state"), local.get("map"), local.get("actor"))
            != (OBSERVATORY, "connected", OBSERVATORY, ACTOR)
            or type(lit := state.get("lit")) is not bool or local.get("lit") is lit):
        return game
    snapshot = await bridge.command(ACTION, OBSERVATORY, ACTOR, "1" if lit else "0", timeout=3)
    report = snapshot.get(KEY, {})
    if ((snapshot.get("map"), snapshot.get("state"), report.get("map"), report.get("actor"))
            != (OBSERVATORY, "connected", OBSERVATORY, ACTOR) or report.get("lit") is not lit):
        raise RuntimeError("The Observatory lantern did not match the host's light state")
    return snapshot
