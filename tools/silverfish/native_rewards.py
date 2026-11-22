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
            if character in recipients and recipients[character]["connected"] and player.get(PENDING, 0) > 0]


async def project(host, bridge, candidate: dict, recipients: list[tuple[str, str, int]]) -> frozenset[str]:
    """Apply the planned XP and return the detached saves the commit must contain."""
    source = host.last_game.get("map")
    for character, pawn, amount in recipients:
        snapshot = await bridge.command("native-guest-quest-xp", pawn, str(amount), timeout=5)
        proof = snapshot.get("native_guest_xp", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != source or proof.get("map") != source
                or proof.get("pawn") != pawn or proof.get("after", -1) - proof.get("before", -1) != amount):
            raise RuntimeError("The detached guest quest XP did not survive native readback")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        candidate["players"][character].pop(PENDING)
    return frozenset(character for character, _, _ in recipients)


async def settle(host, bridge) -> None:
    recipients = pending_recipients(host, host.party)
    if not recipients:
        return
    defer_save = host.last_game.get("native_guest_save_retention") is True
    if defer_save:
        from .native_inventory import retain_guest_save

        for character, pawn, _amount in recipients:
            if not await retain_guest_save(host, bridge, character, pawn):
                return
    candidate = party.load_party(host.party)
    host.native_checkpoint_unsafe = True
    try:
        applied = await project(host, bridge, candidate, recipients)
        if not defer_save:
            await host.checkpoint_native(bridge, candidate, require_guest_frames=applied)
    except (OSError, RuntimeError, ValueError):
        host.stopping = True
        raise
    host.party = candidate
    host.native_checkpoint_unsafe = False
