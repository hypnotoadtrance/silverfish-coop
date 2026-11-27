"""Relay bounded stock automatic and burst fire on the resident input bridge."""

import asyncio
from dataclasses import dataclass
import re
import time
import uuid

from . import native_inventory, party
from .guest_weapon_profiles import HELD_FIRE_PROJECTILE_COUNTS, MAX_HELD_FIRE_ROUNDS, STOCK_HELD_FIRE_MAGAZINES
from .local_bridge import LocalCommandNotSubmitted
from .native_gold import valid_guid
from .native_guest_weapon import valid_shot_view
from .travel import OBSERVATORY


HOLD_KINDS = STOCK_HELD_FIRE_MAGAZINES.keys()
FIXTURE = "native-guest-smg-hold-fixture"
KEY = "native_guest_smg_hold"
ALL = "native_guest_smg_holds"
ACTION = "native-guest-held-fire"
AIM_ACTION = "native-guest-held-aim"
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
            or not 0 < serial < 2**53 or not 0 <= after <= before <= MAX_HELD_FIRE_ROUNDS or before == 0
            or rounds != before - after or projectiles not in {rounds * count for count in HELD_FIRE_PROJECTILE_COUNTS}
            or not (status in LIVE or status in FINISHED or status.startswith(DIRTY))):
        raise ValueError("Invalid stock held-fire status")
    return {"map": area, "pawn": pawn, "guid": guid, "serial": serial, "status": status,
            "before": before, "after": after, "rounds": rounds, "projectiles": projectiles}


@dataclass(frozen=True)
class Stroke:
    area: str
    pawn: str
    guid: str
    token: str
    serial: int
    deferred: bool = False

    def receipt(self, snapshot: dict) -> dict:
        frame = snapshot.get(ALL, {}).get(self.pawn, {})
        if (snapshot.get("map"), frame.get("map"), frame.get("guid"), frame.get("serial")) != (
            self.area, self.area, self.guid, self.serial
        ) or frame.get("status", "").startswith(DIRTY):
            raise RuntimeError("Stock held fire lost exact ammo and projectile readback")
        return frame


class Authority:
    """Fence each guest’s stock trigger and verify its terminal receipt."""

    def __init__(self, host):
        self.host = host
        self.bridge = None
        self.lock = asyncio.Lock()
        self.edge_task = None
        self.closing = False
        self.strokes: dict[str, Stroke] = {}
        self.frames: dict[int, dict] = {}
        self.used: dict[str, None] = {}
        self.pending_stops: set[str] = set()

    async def authenticated(self, guest_id: str, target: str | None) -> dict:
        await self.lock.acquire()
        edge = None
        try:
            identity = self.host.authenticated_guest(guest_id)
            parts = target.split("@", 4) if isinstance(target, str) else []
            ending = len(parts) == 4 and parts[3] in {"release", "cancel"}
            if (not identity or not self.bridge or self.closing or self.host.stopping
                    or self.host.native_checkpoint_unsafe and not ending):
                return {"ok": False, "error": "native_guest_held_fire_scope_unavailable"}
            # A lost or timed-out reply must leave the host's native receipt readable.
            # The task owns the edge lock until the game has acknowledged the command.
            self.edge_task = edge = asyncio.create_task(self._execute_edge(identity[1], target))
        finally:
            if edge is None:
                self.lock.release()
        return await asyncio.shield(edge)

    async def _execute_edge(self, character: str, target: str | None) -> dict:
        try:
            return {"ok": True, **await self.request(character, target)}
        except asyncio.CancelledError:
            self._fail_closed()
            raise
        except (OSError, RuntimeError, ValueError) as error:
            return {"ok": False, "error": str(error)}
        finally:
            self.edge_task = None
            self.lock.release()

    async def close(self) -> None:
        self.closing = True
        if self.edge_task is not None:
            await asyncio.shield(self.edge_task)

    async def aim(self, guest_id: str, target: str | None) -> dict:
        """Replace the aim for an active stroke without taking the trigger edge lock."""
        identity = self.host.authenticated_guest(guest_id)
        parts = target.split("@", 4) if isinstance(target, str) else []
        if self.closing or not identity or not self.bridge or self.bridge.local_bridge is None or len(parts) != 5:
            return {"ok": False, "error": "native_guest_held_aim_unavailable"}
        epoch, guid, token, sequence, view = parts
        character = identity[1]
        stroke = self.strokes.get(character)
        if (epoch != self.host.zone_epoch or not stroke or (guid, token) != (stroke.guid, stroke.token)
                or not sequence.isascii() or not sequence.isdigit() or not 1 <= len(sequence) <= 16
                or not 0 < int(sequence) < 2**53 or not valid_shot_view(view)
                or not self._eligible(character, stroke.area, stroke.pawn, guid)):
            return {"ok": False, "error": "native_guest_held_aim_unavailable"}
        try:
            await self.bridge.held_fire_command("aim", str(stroke.serial), sequence, view)
        except (OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "native_guest_held_aim_unavailable"}
        return {"ok": True}

    def _update(self, stroke: Stroke, snapshot: dict) -> dict:
        proof = stroke.receipt(snapshot)
        self.frames[stroke.serial] = proof
        return proof

    def _observed(self, stroke: Stroke) -> dict:
        prior = self.frames[stroke.serial]
        snapshot = self.host.last_game
        current = snapshot.get(ALL, {}).get(stroke.pawn, {})
        if (prior["status"] in LIVE and current.get("serial") == stroke.serial
                and current.get("rounds", -1) >= prior["rounds"]):
            return self._update(stroke, snapshot)
        return prior

    def _eligible(self, character: str, area: str, pawn: str, guid: str) -> bool:
        host = self.host
        member = host.native_recipients().get(character, {})
        equipped = host.last_game.get("native_guest_equipped_weapons", {}).get(pawn, {})
        return bool(not host.stopping and not host.transitioning and not host.native_checkpoint_unsafe
                    and not host.party["wipe"] and not host.party["players"].get(character, {}).get("downed")
                    and member.get("connected") and member.get("pawn") == pawn
                    and (host.last_game.get("state"), host.last_game.get("map")) == ("hosting", area)
                    and area in host.allowed_maps and equipped.get("kind") in HOLD_KINDS and equipped.get("guid") == guid)

    def _fail_closed(self):
        self.host.native_checkpoint_unsafe = self.host.stopping = True
        if not self.host.session_stop_reason:
            self.host.session_stop_reason = "native_guest_held_fire_verification_failed"

    async def request(self, character: str, target: str | None) -> dict:
        parts = target.split("@", 4) if isinstance(target, str) else []
        if len(parts) not in (4, 5):
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        epoch, guid, token, edge, *view = parts
        if (epoch != self.host.zone_epoch or not valid_guid(guid) or not TOKEN.fullmatch(token)
                or edge not in {"press", "renew", "release", "cancel"}
                or view and (edge != "press" or not valid_shot_view(view[0]))):
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        current = self.strokes.get(character)
        if edge == "press":
            if current and (current.guid, current.token) == (guid, token):
                try:
                    return {KEY: self._observed(current)}
                except RuntimeError:
                    self._fail_closed()
                    raise
            area = self.host.last_game.get("map")
            pawn = self.host.native_recipients().get(character, {}).get("pawn")
            melee_input = getattr(self.host, "melee_input", None)
            if (not current and token not in self.used and pawn and not (melee_input and melee_input.scope)
                    and self._eligible(character, area, pawn, guid)):
                return {KEY: await self._start(character, area, pawn, guid, token, *view)}
            raise party.RuleError("native_guest_held_fire_scope_unavailable")
        if not current or (current.guid, current.token) != (guid, token):
            if edge in {"release", "cancel"} or token in self.used:
                return {KEY: {"status": "inactive"}}
            raise party.RuleError("native_guest_held_fire_exact_stroke_required")
        renew = (edge == "renew" and character not in self.pending_stops
                 and self._eligible(character, current.area, current.pawn, guid))
        try:
            proof = self._observed(current)
            if proof["status"] in FINISHED:
                return {KEY: proof}
            snapshot = await self.bridge.held_fire_command("renew" if renew else "stop", str(current.serial),
                                                           *((str(LEASE_MS),) if renew else ()))
            return {KEY: self._update(current, snapshot)}
        except LocalCommandNotSubmitted as error:
            if not renew:
                self.pending_stops.add(character)
            raise party.RuleError(str(error)) from error
        except (OSError, RuntimeError, ValueError):
            self._fail_closed()
            raise


    async def _start(self, character, area, pawn, guid, token, *view) -> dict:
        deferred = await native_inventory.retain_guest_save(self.host, self.bridge, character, pawn)
        if self.closing or not self._eligible(character, area, pawn, guid):
            raise party.RuleError("native_guest_held_fire_scope_unavailable")
        try:
            snapshot = await self.bridge.held_fire_command("start", pawn, guid, str(LEASE_MS), *view)
        except LocalCommandNotSubmitted as error:
            raise party.RuleError(str(error)) from error
        except RuntimeError as error:
            if str(error) in {"authoritative_guest_pawn_required", "detached_guest_save_required",
                              "authenticated_guest_detached_save_required", "native_guest_smg_hold_ineligible",
                              "native_guest_smg_hold_busy", "native_guest_smg_hold_exact_owner_required",
                              "native_guest_smg_hold_exact_weapon_required", "native_guest_smg_hold_scope_unavailable",
                              "native_guest_smg_hold_fast_scope_unavailable", "guest_weapon_view_unqualified"}:
                raise party.RuleError(str(error)) from error
            self._fail_closed()
            raise
        except (OSError, ValueError):
            self._fail_closed()
            raise
        try:
            frame = snapshot.get(ALL, {}).get(pawn, {})
            stroke = Stroke(area, pawn, guid, token, frame["serial"], deferred=deferred)
            proof = self._update(stroke, snapshot)
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
        if not stop_all and (not self.strokes or self.host.native_checkpoint_unsafe):
            return
        finished = {}
        try:
            async with self.lock:
                if self.host.native_checkpoint_unsafe:
                    if stop_all:
                        raise RuntimeError("Stock held fire is unverified at a campaign checkpoint")
                    return
                for character, stroke in self.strokes.items():
                    frame = self._observed(stroke)
                    if frame["status"] in LIVE and (stop_all or character in self.pending_stops
                                                     or not self._eligible(character, stroke.area, stroke.pawn, stroke.guid)):
                        try:
                            snapshot = await self.bridge.held_fire_command("stop", str(stroke.serial))
                        except LocalCommandNotSubmitted:
                            if stop_all:
                                raise
                            continue
                        frame = self._update(stroke, snapshot)
                    if frame["status"] in FINISHED:
                        finished[character] = frame
                    elif stop_all:
                        raise RuntimeError("Stock held fire has an unclosed trigger at a campaign checkpoint")
            if not finished:
                return
            durable = frozenset(character for character in finished if not self.strokes[character].deferred)
            revision = await self.host.checkpoint_native(bridge, require_guest_frames=durable) if durable else None
            for character, frame in finished.items():
                del self.strokes[character]
                del self.frames[frame["serial"]]
                self.pending_stops.discard(character)
                event = "native_guest_smg_hold_committed" if character in durable else "native_guest_smg_hold_accepted"
                self.host.control.event(event, character_id=character, revision=revision, **frame)
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
        self.aim_task = None
        self.aim_sequence = 0
        self.last_aim = None
        self.pending_aim = None

    async def _send(self, stroke: LocalStroke, edge: str, sequence: int, *view: str) -> dict:
        client = self.client
        operation = uuid.uuid5(client.bridge.input_namespace, f"held:{stroke.token}:{edge}:{sequence}").hex
        try:
            if edge == "tap":
                target = "@".join((stroke.guid, stroke.epoch, *view)) if view else stroke.guid
                reply = await client.gameplay_action("native-guest-weapon-fire", operation, target)
            else:
                target = "@".join((stroke.epoch, stroke.guid, stroke.token, edge, *view))
                reply = await client.gameplay_action(ACTION, operation, target)
            if edge != "renew":
                client.control.event("native_guest_held_fire_input", edge=edge, stroke=stroke.number,
                                     operation_id=operation, **reply)
            return reply.get(KEY, {})
        except (OSError, ValueError, RuntimeError) as error:
            client.control.event("native_guest_held_fire_input_rejected", edge=edge, stroke=stroke.number,
                                 operation_id=operation, reason=str(error))
            return {}

    async def _send_aim(self, stroke: LocalStroke, sequence: int, view: str) -> None:
        operation = uuid.uuid5(self.client.bridge.input_namespace, f"held-aim:{stroke.token}:{sequence}").hex
        target = "@".join((stroke.epoch, stroke.guid, stroke.token, str(sequence), view))
        try:
            reply = await self.client.transport.request(
                {"action": AIM_ACTION, "operation_id": operation, "target": target}, timeout=.5)
        except (OSError, RuntimeError, ValueError):
            return
        if reply.get("ok") and self.stroke == stroke:
            self.last_aim = view

    def _refresh_aim(self) -> None:
        if self.client.bridge.local_bridge is None:
            return
        sample = self.client.bridge.take_guest_held_aim()
        if sample and self.stroke.matches(*sample[:4]):
            self.pending_aim = sample[4]
        if not self.pending_aim or self.pending_aim == self.last_aim or self.aim_task and not self.aim_task.done():
            return
        self.aim_sequence += 1
        self.aim_task = asyncio.create_task(self._send_aim(self.stroke, self.aim_sequence, self.pending_aim))

    def _eligible(self, active: bool, game: dict):
        client = self.client
        party_game = client.gameplay
        member = party_game.get("players", {}).get(client.viewer_role, {})
        weapon = party_game.get("native_equipped_weapon", {})
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        guid = weapon.get("guid")
        area = game.get("map")
        if (active and not client.stopping and client.host_session and client.zone_epoch
                and game.get("state") == "connected"
                and member.get("character_id") == client.invite["character_id"] and member.get("downed") is False
                and party_game.get("wipe") is None and pawn and valid_guid(guid)
                and weapon.get("map") == area and weapon.get("kind") in HOLD_KINDS):
            return area, pawn, guid

    async def run(self) -> None:
        client = self.client
        try:
            while not client.stopping:
                game = client.bridge.snapshot() or client.last_game
                active = (client.transport.connected and game.get("state") == "connected"
                          and not client.needs_travel and not client.server_transitioning)
                await self.service(active, game)
                await asyncio.sleep(.075)
        finally:
            if self.aim_task is not None:
                self.aim_task.cancel()
                await asyncio.gather(self.aim_task, return_exceptions=True)

    async def service(self, active: bool, game: dict | None = None) -> None:
        client = self.client
        if game is None:
            game = client.last_game
        eligible = self._eligible(active, game)
        if self.stroke and (not eligible or (client.host_session, client.zone_epoch, *eligible) != (
            self.stroke.session, self.stroke.epoch, self.stroke.area, self.stroke.pawn, self.stroke.guid
        )):
            abandoned, self.stroke = self.stroke, None
            if client.host_session == abandoned.session and client.zone_epoch == abandoned.epoch:
                await self._send(abandoned, "cancel", self.last_sequence)
        events = [row for row in client.bridge.guest_held_fire_requests() if row[0] > self.last_sequence]
        if events:
            self.last_sequence = events[-1][0]
        terminals = {row[1:4] + row[5:6]: row[4] for row in events if row[4] != "press"}
        frame = game.get(LOCAL, {})
        held = tuple(frame.get(key) for key in ("map", "pawn", "guid", "stroke"))
        started = False
        for sequence, area, pawn, guid, edge, number, *view in events:
            if edge == "press" and not self.stroke and eligible == (area, pawn, guid):
                token = uuid.uuid5(client.bridge.input_namespace, f"{client.host_session}:{client.zone_epoch}:{number}").hex
                stroke = LocalStroke(client.host_session, client.zone_epoch, area, pawn, guid, number, token)
                terminal = terminals.get((area, pawn, guid, number))
                if terminal == "release":
                    await self._send(stroke, "tap", sequence, *view)
                elif terminal is None:
                    result = await self._send(stroke, "press", sequence, *view)
                    self.stroke = stroke if result.get("status") in LIVE else None
                    started = self.stroke is not None
                    self.last_aim = view[0] if view else None
                    self.pending_aim = None
                    self.renew_at = time.monotonic() + .55
            elif edge == "press":
                client.control.event("native_guest_held_fire_scope_refused", stroke=number, map=area, pawn=pawn,
                                     guid=guid, eligibility=eligible is not None, previous_stroke=self.stroke is not None)
            elif edge != "press" and self.stroke and self.stroke.matches(area, pawn, guid, number):
                terminal, self.stroke = self.stroke, None
                await self._send(terminal, edge, sequence)
        if not self.stroke or started:
            return
        if frame.get("state") != "held" or not self.stroke.matches(*held):
            abandoned, self.stroke = self.stroke, None
            await self._send(abandoned, "cancel", self.last_sequence)
            return
        self._refresh_aim()
        if (frame.get("proof") in {"mapped-key", "headless-fixture"} and time.monotonic() >= self.renew_at
              and time.monotonic() - client.gameplay_seen < 3):
            frame = await self._send(self.stroke, "renew", self.last_sequence)
            self.renew_at = time.monotonic() + .55
            if frame.get("status") in FINISHED | {"inactive"}:
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
