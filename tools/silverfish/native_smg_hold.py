"""Exercise bounded stock SMG fire in an isolated headless campaign."""

import asyncio
import time

from . import party
from .native_gold import valid_guid
from .travel import OBSERVATORY


FIXTURE = "native-guest-smg-hold-fixture"
KEY = "native_guest_smg_hold"
LIVE = {"active", "closing"}
FINISHED = {"released", "expired", "empty", "ineligible"}
DIRTY = "mutation_unverified_guest_smg_hold_"


def parse(row: list[str]) -> dict:
    _, area, pawn, guid, serial, status, before, after, rounds, projectiles = row
    serial, before, after, rounds, projectiles = map(int, (serial, before, after, rounds, projectiles))
    if (area != OBSERVATORY or not pawn or not valid_guid(guid)
            or not 0 < serial < 2**53 or not 0 <= after <= before <= 30 or before == 0
            or rounds != before - after or projectiles != rounds
            or not (status in LIVE or status in FINISHED or status.startswith(DIRTY))):
        raise ValueError("Invalid stock SMG hold status")
    return {"map": area, "pawn": pawn, "guid": guid, "serial": serial, "status": status,
            "before": before, "after": after, "rounds": rounds, "projectiles": projectiles}


async def exercise(host, bridge, role, guid, duration, release_after) -> dict:
    if (not isinstance(role, str) or type(duration) is not int or not 250 <= duration <= 2400 or not valid_guid(guid)
            or release_after is not None and (type(release_after) is not int or not 2 <= release_after <= 10)):
        raise party.RuleError("native_guest_smg_hold_fixture_arguments")
    character = host.roles().get(role)
    member = host.native_recipients().get(character, {})
    pawn = member.get("pawn")
    equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
    if (not host.fixtures or not host.native_guest or not host.native_profile or not pawn or not member.get("connected")
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]
            or host.party["players"].get(character, {}).get("downed")
            or (host.last_game.get("state"), host.last_game.get("map")) != ("hosting", OBSERVATORY)
            or (equipped.get("kind"), equipped.get("guid")) != ("smg-standard", guid)):
        raise party.RuleError("native_guest_smg_hold_fixture_unavailable")

    async def proof(action, *arguments, serial=None):
        snapshot = await bridge.command(action, *arguments, timeout=2)
        value = snapshot.get(KEY, {})
        if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", OBSERVATORY)
                or (value.get("map"), value.get("pawn"), value.get("guid")) != (OBSERVATORY, pawn, guid)
                or serial is not None and value.get("serial") != serial
                or value.get("status", "").startswith(DIRTY)):
            raise RuntimeError("The stock SMG hold did not verify ammo and projectile ownership")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return value

    host.transitioning = True
    try:
        await host.checkpoint_native(bridge)
        host.native_checkpoint_unsafe = True
        try:
            started = await proof("native-guest-smg-hold-start", pawn, guid, str(duration))
        except RuntimeError as error:
            if str(error).startswith(("native_guest_smg_hold_", "stock_guest_weapon_")):
                host.native_checkpoint_unsafe = False
            raise
        serial = started["serial"]
        final = started
        released = False
        deadline = time.monotonic() + duration / 1000 + 3
        while final["status"] in LIVE and time.monotonic() < deadline:
            operation = "native-guest-smg-hold-observe"
            if release_after is not None and not released and final["status"] == "active" and final["rounds"] >= release_after:
                operation = "native-guest-smg-hold-stop"
                released = True
            final = await proof(operation, str(serial), serial=serial)
        expected = "released" if release_after is not None else "expired"
        if (final["status"] != expected or final["rounds"] < (release_after or 2)
                or final["before"] != started["before"] or final["after"] >= started["before"]):
            raise RuntimeError(f"The stock SMG hold did not verify {expected}: {final}")
        await asyncio.sleep(.2)
        stable = await proof("native-guest-smg-hold-observe", str(serial), serial=serial)
        if stable != final:
            raise RuntimeError("The stock SMG fired again after the verified end of the hold")
        revision = await host.checkpoint_native(bridge, require_guest_frames=frozenset({character}))
        host.native_checkpoint_unsafe = False
        host.control.event("native_guest_smg_hold_verified", character_id=character, revision=revision, **final)
        return {KEY: final}
    except (OSError, RuntimeError, ValueError, KeyError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
