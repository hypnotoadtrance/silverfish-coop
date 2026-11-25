"""Carry ladder mount and release edges to the matching listen-host pawn."""

import re
import time

from . import party


ACTION = "native-ladder-state"
_TARGET = re.compile(r"ladder:(mount|release):(-?[0-9]{1,8}),(-?[0-9]{1,8}),(-?[0-9]{1,8})")


def target_parts(target):
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    if match and all(abs(int(value)) <= 10_000_000 for value in match.groups()[1:]):
        return match.groups()
    return None


async def apply(host, bridge, character, target):
    epoch, _, payload = (target or "").partition("@")
    edge = target_parts(payload)
    owner = host.native_recipients().get(character)
    player = host.party["players"].get(character)
    if (not edge or not host.native_zones or not host.native_guest or not owner or not owner["connected"]
            or not player or (edge[0] == "mount" and player["downed"])
            or host.stopping or host.transitioning or host.party["wipe"]
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen >= 2
            or not epoch or epoch != host.zone_epoch):
        raise party.RuleError("ladder_scope_unavailable")
    try:
        snapshot = await bridge.command(ACTION, owner["pawn"], *edge, timeout=2, update_status=False)
    except (OSError, RuntimeError, TimeoutError) as error:
        raise party.RuleError(str(error)) from error
    expected = "mounted" if edge[0] == "mount" else "released"
    if snapshot.get("ack", {}).get("message") != expected:
        raise party.RuleError("ladder_receipt_unavailable")
    return {"native_ladder": {"state": expected}}

