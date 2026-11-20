"""Run only the isolated headless stock Core overlap and console approach."""

import asyncio
import time

from . import native_mindcore as mindcore, party
from .native_mosby_tape import _scope


INSTALL = "native-mindcore-install-fixture"
APPROACH = "native-mindcore-approach-fixture"


def _same_participants(host, owner, guest_pawn, host_pawn):
    exact = host.native_recipients()
    member = exact.get(owner, {})
    return (len(exact) == 1 and member.get("connected") is True and member.get("pawn") == guest_pawn
            and host.last_game.get("pawns", {}).get("local", {}).get("name") == host_pawn)


async def perform(host, bridge, action, target, variant=None):
    if not host.fixtures or action not in (INSTALL, APPROACH) or target is not None:
        raise party.RuleError("native_mindcore_fixture_refused_arguments")
    seats = host.native_recipients()
    if len(seats) != 1:
        raise party.RuleError("native_mindcore_fixture_refused_exact_pair")
    owner, = seats
    host_pawn = _scope(host, "host", mindcore.MAP)
    guest_pawn = _scope(host, owner, mindcore.MAP, guest_only=True)
    if not _same_participants(host, owner, guest_pawn, host_pawn):
        raise party.RuleError("native_mindcore_fixture_refused_exact_pair")
    if variant is not None and (action != APPROACH or type(variant) is not int or not 1 <= variant <= 12):
        raise party.RuleError("native_mindcore_fixture_refused_invalid_console_approach")
    if action == APPROACH:
        selector = () if variant is None else (str(variant),)
        snapshot = await bridge.command(APPROACH, guest_pawn, *selector, timeout=7)
        scene = snapshot.get(mindcore.SCENE)
        if not scene or snapshot.get("state") != "hosting" or snapshot.get("map") != mindcore.MAP:
            raise RuntimeError("The isolated console approach lost its exact stock world")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The isolated console approach lost the exact pair")
        return {mindcore.SCENE: scene, "pawn": guest_pawn}

    def dormant(snapshot):
        scene = snapshot.get(mindcore.SCENE, {})
        return (snapshot.get("state"), snapshot.get("map"), scene.get("status"), scene.get("installed")) == (
            "hosting", mindcore.MAP, 0, False)

    if not dormant(host.last_game) or mindcore.LEDGER in host.party:
        raise party.RuleError("native_mindcore_fixture_refused_console_used")
    host.transitioning = True
    try:
        await host.checkpoint_native(bridge, require_guest_frames=frozenset({owner}))
        if not dormant(host.last_game) or not _same_participants(host, owner, guest_pawn, host_pawn):
            raise party.RuleError("native_mindcore_fixture_refused_changed_scope")
        host.native_checkpoint_unsafe = True
        try:
            snapshot = await bridge.command(INSTALL, host_pawn, timeout=7)
        except RuntimeError as error:
            if str(error).startswith(("native_mindcore_fixture_refused_", "native_mindcore_refused_")):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if not mindcore._ready(snapshot, 1):
            snapshot = await bridge.wait_for(lambda game: mindcore._ready(game, 1), timeout=12)
        host.last_game, host.last_seen = snapshot, time.monotonic()
        if not _same_participants(host, owner, guest_pawn, host_pawn):
            raise RuntimeError("The exact pair changed during the stock Core overlap")
        revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({owner}))
        host.native_checkpoint_unsafe = False
        scene = snapshot[mindcore.SCENE]
        host.control.event("native_mindcore_fixture_installed", revision=revision, pawn=host_pawn, **scene)
        return {mindcore.SCENE: scene, "pawn": host_pawn, "revision": revision}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
