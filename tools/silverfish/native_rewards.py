"""Project personal quest XP into each guest's verified detached native save."""

import time

from . import party


PENDING = "native_quest_xp_pending"


def queue(player: dict, amount: int) -> None:
    player[PENDING] = player.get(PENDING, 0) + amount


def pending_recipients(host, candidate: dict) -> list[tuple[str, str, int]]:
    if not host.native_guest or not host.last_game.get("native_guest_xp_ready"):
        return []
    recipients = host.native_recipients()
    return [(character, recipients[character]["pawn"], player[PENDING])
            for character, player in candidate["players"].items()
            if character in recipients and player.get(PENDING, 0) > 0]


async def project(host, bridge, candidate: dict, recipients: list[tuple[str, str, int]]) -> None:
    source = host.last_game.get("map")
    for character, pawn, amount in recipients:
        snapshot = await bridge.command("native-guest-quest-xp", pawn, str(amount), timeout=5)
        proof = snapshot.get("native_guest_xp", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != source or proof.get("map") != source
                or proof.get("pawn") != pawn or proof.get("after", -1) - proof.get("before", -1) != amount):
            raise RuntimeError("The detached guest quest XP did not survive native readback")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate["players"][character].pop(PENDING)


async def settle(host, bridge) -> None:
    recipients = pending_recipients(host, host.party)
    if not recipients:
        return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        await project(host, bridge, candidate, recipients)
        await host.checkpoint_native(bridge, candidate)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
