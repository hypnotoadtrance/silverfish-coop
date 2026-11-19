"""Exercise bounded stock SMG fire in an isolated headless campaign."""

import asyncio
from dataclasses import dataclass
import re
import time
import uuid

from . import party
from .native_gold import valid_guid
from .travel import OBSERVATORY


FIXTURE = "native-guest-smg-hold-fixture"
KEY = "native_guest_smg_hold"
ALL = "native_guest_smg_holds"
ACTION = "native-guest-held-fire"
LEASE_MS = 1800
LIVE = {"active", "closing"}
FINISHED = {"released", "expired", "empty", "ineligible"}
DIRTY = "mutation_unverified_guest_smg_hold_"
NATIVE_MAP = re.compile(r"/Game/Classes/Maps/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+")
TOKEN = re.compile(r"[0-9a-f]{32}")
LOCAL = "native_guest_held_fire"
LOCAL_FIXTURE = "native-guest-held-fire-fixture"


def parse(row: list[str]) -> dict:
    _, area, pawn, guid, serial, status, before, after, rounds, projectiles = row
    serial, before, after, rounds, projectiles = map(int, (serial, before, after, rounds, projectiles))
    if (not NATIVE_MAP.fullmatch(area) or not pawn or not valid_guid(guid)
            or not 0 < serial < 2**53 or not 0 <= after <= before <= 30 or before == 0
            or rounds != before - after or projectiles != rounds
            or not (status in LIVE or status in FINISHED or status.startswith(DIRTY))):
        raise ValueError("Invalid stock SMG hold status")
    return {"map": area, "pawn": pawn, "guid": guid, "serial": serial, "status": status,
            "before": before, "after": after, "rounds": rounds, "projectiles": projectiles}


@dataclass(frozen=True)
class Stroke:
    area: str
    pawn: str
    guid: str
    token: str
    serial: int

    def receipt(self, snapshot: dict) -> dict:
        frame = snapshot.get(ALL, {}).get(self.pawn, {})
        if (snapshot.get("map"), frame.get("map"), frame.get("guid"), frame.get("serial")) != (
            self.area, self.area, self.guid, self.serial
        ) or frame.get("status", "").startswith(DIRTY):
            raise RuntimeError("Stock held fire lost exact ammo and projectile readback")
        return frame


class Authority:
    """Fence each guest’s stock trigger and checkpoint verified terminal receipts."""

    def __init__(self, host):
        self.host = host
        self.strokes: dict[str, Stroke] = {}
        self.used: dict[str, None] = {}

    async def _command(self, bridge, action: str, *arguments: str) -> dict:
        snapshot = await bridge.command(f"native-guest-smg-hold-{action}", *arguments, timeout=2)
        self.host.last_game, self.host.last_seen = snapshot, time.monotonic()
        return snapshot

    def _eligible(self, character: str, area: str, pawn: str, guid: str) -> bool:
        host = self.host
        member = host.native_recipients().get(character, {})
        equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
        return bool(not host.stopping and not host.transitioning and not host.native_checkpoint_unsafe
                    and not host.party["wipe"] and not host.party["players"].get(character, {}).get("downed")
                    and member.get("connected") and member.get("pawn") == pawn
                    and (host.last_game.get("state"), host.last_game.get("map")) == ("hosting", area)
                    and area in host.allowed_maps and (equipped.get("kind"), equipped.get("guid")) == ("smg-standard", guid))

    def _fail_closed(self):
        self.host.native_checkpoint_unsafe = self.host.stopping = True

    async def request(self, bridge, character: str, target: str | None) -> dict:
        parts = target.split("@") if isinstance(target, str) else []
        if len(parts) != 4:
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        epoch, guid, token, edge = parts
        if (epoch != self.host.zone_epoch or not valid_guid(guid) or not TOKEN.fullmatch(token)
                or edge not in {"press", "renew", "release", "cancel"}):
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        current = self.strokes.get(character)
        if edge == "press":
            if current and (current.guid, current.token) == (guid, token):
                return {KEY: current.receipt(self.host.last_game)}
            area = self.host.last_game.get("map")
            pawn = self.host.native_recipients().get(character, {}).get("pawn")
            if not current and token not in self.used and pawn and self._eligible(character, area, pawn, guid):
                return {KEY: await self._start(bridge, character, area, pawn, guid, token)}
            raise party.RuleError("native_guest_held_fire_scope_unavailable")
        if not current or (current.guid, current.token) != (guid, token):
            if edge in {"release", "cancel"}:
                return {KEY: {"status": "inactive"}}
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        renew = edge == "renew" and self._eligible(character, current.area, current.pawn, guid)
        try:
            snapshot = await self._command(bridge, "renew" if renew else "stop", str(current.serial),
                                           *((str(LEASE_MS),) if renew else ()))
            return {KEY: current.receipt(snapshot)}
        except (OSError, RuntimeError, ValueError):
            self._fail_closed()
            raise


    async def _start(self, bridge, character, area, pawn, guid, token) -> dict:
        try:
            snapshot = await self._command(bridge, "start", pawn, guid, str(LEASE_MS))
        except RuntimeError as error:
            if str(error) in {"native_guest_smg_hold_ineligible", "native_guest_smg_hold_busy",
                              "native_guest_smg_hold_exact_owner_required"}:
                raise party.RuleError(str(error)) from error
            self._fail_closed()
            raise
        except (OSError, ValueError):
            self._fail_closed()
            raise
        try:
            frame = snapshot.get(ALL, {}).get(pawn, {})
            stroke = Stroke(area, pawn, guid, token, frame["serial"])
            proof = stroke.receipt(snapshot)
            if proof["rounds"] < 1:
                raise RuntimeError("Stock held fire did not produce a verified first round")
        except (KeyError, RuntimeError):
            self._fail_closed()
            raise
        self.strokes[character] = stroke
        self.used[token] = None
        if len(self.used) > 256:
            self.used.pop(next(iter(self.used)))
        return proof

    async def settle(self, bridge, *, stop_all: bool = False) -> None:
        if not self.strokes or self.host.native_checkpoint_unsafe:
            return
        finished = {}
        try:
            for character, stroke in self.strokes.items():
                frame = stroke.receipt(self.host.last_game)
                if frame["status"] in LIVE and (stop_all or not self._eligible(character, stroke.area, stroke.pawn, stroke.guid)):
                    frame = stroke.receipt(await self._command(bridge, "stop", str(stroke.serial)))
                if frame["status"] in FINISHED:
                    finished[character] = frame
                elif stop_all:
                    raise RuntimeError("Stock held fire has an unclosed trigger at a campaign checkpoint")
            if not finished:
                return
            revision = await self.host.checkpoint_native(bridge, require_guest_frames=frozenset(finished))
            for character, frame in finished.items():
                del self.strokes[character]
                self.host.control.event("native_guest_smg_hold_committed",
                                        character_id=character, revision=revision, **frame)
        except (OSError, RuntimeError, ValueError):
            self._fail_closed()
            raise


@dataclass(frozen=True)
class LocalStroke:
    session: str
    epoch: str
    area: str
    pawn: str
    guid: str
    number: int
    token: str

    def matches(self, area, pawn, guid, number) -> bool:
        return (self.area, self.pawn, self.guid, self.number) == (area, pawn, guid, number)


class GuestInput:
    def __init__(self, client):
        self.client = client
        self.last_sequence = 0
        self.stroke = None
        self.renew_at = 0.0

    async def _send(self, stroke: LocalStroke, edge: str, sequence: int) -> dict:
        client = self.client
        operation = uuid.uuid5(client.bridge.input_namespace, f"held:{stroke.token}:{edge}:{sequence}").hex
        try:
            if edge == "tap":
                reply = await client.gameplay_action("native-guest-weapon-fire", operation, stroke.guid)
            else:
                target = "@".join((stroke.epoch, stroke.guid, stroke.token, edge))
                reply = await client.gameplay_action(ACTION, operation, target)
            if edge != "renew":
                client.control.event("native_guest_held_fire_input", edge=edge, stroke=stroke.number,
                                     operation_id=operation, **reply)
            return reply.get(KEY, {})
        except (OSError, ValueError, RuntimeError) as error:
            client.control.event("native_guest_held_fire_input_rejected", edge=edge, stroke=stroke.number,
                                 operation_id=operation, reason=str(error))
            return {}

    def _eligible(self, active: bool):
        client = self.client
        party_game = client.gameplay
        member = party_game.get("players", {}).get(client.viewer_role, {})
        weapon = party_game.get("native_equipped_weapon", {})
        game = client.last_game
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        guid = weapon.get("guid")
        area = game.get("map")
        if (active and not client.stopping and client.host_session and client.zone_epoch
                and time.monotonic() - client.gameplay_seen < 2 and game.get("state") == "connected"
                and member.get("character_id") == client.invite["character_id"] and member.get("downed") is False
                and party_game.get("wipe") is None and pawn and valid_guid(guid)
                and (weapon.get("map"), weapon.get("kind")) == (area, "smg-standard")):
            return area, pawn, guid

    async def service(self, active: bool) -> None:
        client = self.client
        eligible = self._eligible(active)
        if self.stroke and (not eligible or (client.host_session, client.zone_epoch, *eligible) != (
            self.stroke.session, self.stroke.epoch, self.stroke.area, self.stroke.pawn, self.stroke.guid
        )):
            abandoned, self.stroke = self.stroke, None
            if client.host_session == abandoned.session and client.zone_epoch == abandoned.epoch:
                await self._send(abandoned, "cancel", self.last_sequence)
        events = [row for row in client.bridge.guest_held_fire_requests() if row[0] > self.last_sequence]
        if events:
            self.last_sequence = events[-1][0]
        terminals = {row[1:4] + row[5:] for row in events if row[4] == "release"}
        frame = client.last_game.get(LOCAL, {})
        held = tuple(frame.get(key) for key in ("map", "pawn", "guid", "stroke"))
        for sequence, area, pawn, guid, edge, number in events:
            if edge == "press" and not self.stroke and eligible == (area, pawn, guid):
                token = uuid.uuid5(client.bridge.input_namespace, f"{client.host_session}:{client.zone_epoch}:{number}").hex
                stroke = LocalStroke(client.host_session, client.zone_epoch, area, pawn, guid, number, token)
                if (area, pawn, guid, number) in terminals:
                    await self._send(stroke, "tap", sequence)
                elif frame.get("state") == "held" and stroke.matches(*held):
                    result = await self._send(stroke, "press", sequence)
                    self.stroke = stroke if result.get("status") in LIVE else None
                    self.renew_at = time.monotonic() + .55
            elif edge != "press" and self.stroke and self.stroke.matches(area, pawn, guid, number):
                terminal, self.stroke = self.stroke, None
                await self._send(terminal, edge, sequence)
        if not self.stroke:
            return
        if frame.get("state") != "held" or not self.stroke.matches(*held):
            abandoned, self.stroke = self.stroke, None
            await self._send(abandoned, "cancel", self.last_sequence)
        elif frame.get("proof") in {"mapped-key", "headless-fixture"} and time.monotonic() >= self.renew_at:
            frame = await self._send(self.stroke, "renew", self.last_sequence)
            self.renew_at = time.monotonic() + .55
            if frame.get("status") in FINISHED:
                self.stroke = None


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
