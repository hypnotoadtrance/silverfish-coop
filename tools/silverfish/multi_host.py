"""Apply independent authenticated guest leases to the staged game host."""

import asyncio
import contextlib
import math
import time

from . import link, native_host_save, party
from .party_members import PartyMemberError, PartyMembers
from .service import GUEST_EXPIRY_CAPTURE_ATTEMPTS, Host, POSITION_MAX_AGE, PROTOCOL, _pawn, guest_arrival_positions
from .travel import SLATE


RETURN_HANDSHAKE_TIMEOUT = 35.0


class MultiHost(Host):
    def __init__(self, *args, max_guests: int, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.enrollment or not self.native_profile or not self.persist_native_guest:
            raise ValueError("Multi-guest hosting requires staged exact native enrollment and detached saves")
        self.members = PartyMembers(self.store, max_guests=max_guests, enrollment=self.enrollment)
        self.relay = None
        self.returning_guests: list[str] = []
        self.return_handshake: tuple[int, float] | None = None
        self.return_census_floor: int | None = None
        self.return_waiting: int | None = None
        self.return_retried: set[str] = set()
        self.return_retry_request: tuple[int, int, asyncio.Future] | None = None
        self.retirement_processed: set[str] = set()

    def _return_blocked(self) -> bool:
        # The new listener can open before the durable wipe and host respawn are complete.
        if self.transitioning or self.returning_guests and (
            self.party["wipe"] is not None or "host" in self.party["pending_spawns"]
        ):
            return True
        if self.return_census_floor is not None:
            return self.last_game.get("sequence", 0) <= self.return_census_floor
        return False

    def _update_native_relay(self) -> None:
        if self.relay is None:
            return
        allowed = set(self.enrollment.assignments)
        pending = self.enrollment.pending
        if pending and not self._return_blocked():
            allowed.add(pending.guest_id)
            if pending.guest_id not in self.returning_guests:
                self.return_handshake = None
            elif self.return_handshake is None or self.return_handshake[0] != pending.id:
                self.return_handshake = (pending.id, time.monotonic() + RETURN_HANDSHAKE_TIMEOUT)
        else:
            self.return_handshake = None
        self.relay.restrict_game_routes(allowed)

    def admit(self, request: dict) -> dict:
        if request.get("catalog") is True:
            return super().admit(request)
        if self.stopping or self.last_game.get("state") != "hosting":
            return {"accepted": False, "reason": "host_unavailable"}
        if self._return_blocked():
            return {"accepted": False, "reason": "travel_in_progress"}
        if request["protocol_version"] != PROTOCOL or request["game_build"] != self.game_build:
            return {"accepted": False, "reason": "incompatible_game_or_protocol"}
        if self.party["wipe"] and request["character_id"] not in self.party["wipe"]["roster"]:
            return {"accepted": False, "reason": "wipe_in_progress"}
        try:
            admitted = self.members.admit(request["guest_id"], request["credential"], request["character_id"], self.last_game)
        except PartyMemberError as error:
            reason = error.reason
            if reason not in ("party_full", "slot_cleanup_pending", "guest_or_character_unavailable"):
                reason = "native_roster_unavailable"
            return {"accepted": False, "reason": reason}
        self._update_native_relay()
        member = admitted.member
        self.control.event("guest_link_authenticated", guest_id=member.guest_id, character_id=member.character_id,
                           role=member.role, resumed=admitted.resumed)
        return {"accepted": True, "resumed": admitted.resumed, "character_id": member.character_id,
                "session_id": self.control.generation, "zone_epoch": self.zone_epoch, "zone": self.last_game["map"]}

    def disconnected(self, guest_id: str) -> None:
        if self.stopping:
            return
        member = self.members.disconnect(guest_id)
        if member:
            self.control.event("guest_link_lost", guest_id=guest_id, grace_seconds=self.members.grace_seconds)
            if not self.transitioning and self.last_game.get("state") == "hosting":
                with contextlib.suppress(OSError, RuntimeError):
                    self.checkpoint()

    def authenticated_guest(self, guest_id: str) -> tuple[str, str] | None:
        member = self.members.get(guest_id)
        if member and member.disconnected_at is None:
            return member.role, member.character_id
        return None

    def snapshot_for_guest(self, guest_id: str, role: str) -> dict:
        result = super().snapshot_for_guest(guest_id, role)
        if guest_id in self.returning_guests:
            pending = self.enrollment.pending
            result["travel_in_progress"] = self._return_blocked() or not pending or pending.guest_id != guest_id
            if not result["travel_in_progress"]:
                result["native_return_reservation"] = pending.id
                result["native_return_retry_allowed"] = self.return_waiting == pending.id
        return result

    def guest_request(self, guest_id: str, request: dict):
        if request.get("action") == "retry-native-return":
            return self._request_return_retry(guest_id, request)
        return super().guest_request(guest_id, request)

    async def _request_return_retry(self, guest_id: str, request: dict) -> dict:
        pending = self.enrollment.pending
        if (set(request) != {"action", "zone_epoch", "reservation"} or request["zone_epoch"] != self.zone_epoch
                or type(request["reservation"]) is not int or not pending or pending.guest_id != guest_id
                or pending.id != request["reservation"] or not self.authenticated_guest(guest_id)
                or self.return_waiting != pending.id or self._return_blocked() or self.return_retry_request):
            return {"ok": False, "error": "native_return_retry_unavailable"}
        future = asyncio.get_running_loop().create_future()
        queued = (pending.id, self.last_game["sequence"], future)
        self.return_retry_request = queued
        try:
            return await asyncio.wait_for(future, timeout=5)
        except TimeoutError:
            return {"ok": False, "error": "native_return_retry_census_timeout"}
        finally:
            if self.return_retry_request is queued:
                self.return_retry_request = None

    def roles(self) -> dict[str, str]:
        return self.members.roles()

    def role_pawn(self, role: str) -> tuple[str, object | None]:
        if role == "host":
            return "local", None
        observation = self.members.match_pawns(self.last_game).get(role)
        return (observation.alias, observation.binding) if observation else ("", None)

    def native_recipients(self) -> dict[str, dict]:
        scopes = self.last_game.get("native_guest_save_members", {})
        participants = self.members.match_pawns(self.last_game)
        result = {}
        for member in self.members.members:
            observed = participants.get(member.role)
            if member.ready and observed and scopes.get(observed.binding.pawn, {}).get("state") == "bound":
                result[member.character_id] = {
                    "role": member.role, "alias": observed.alias, "pawn": observed.binding.pawn,
                    "connected": member.disconnected_at is None,
                }
        return result

    def public_gameplay(self) -> dict:
        view = super().public_gameplay()
        names = {role: entry.binding.pawn for role, entry in self.members.match_pawns(self.last_game).items()}
        if lead := self.last_game.get("pawns", {}).get("local", {}).get("name"):
            names["host"] = lead
        view["native_pawns"] = names
        return view

    def authoritative_pawns(self, roles: dict[str, str]) -> dict[str, dict]:
        if time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            return {}
        result = {}
        lead = _pawn(self.last_game, "local")
        if lead and None not in lead["location"]:
            result["host"] = lead
        for role, observation in self.members.match_pawns(self.last_game).items():
            if role in roles:
                result[roles[role]] = _pawn(self.last_game, observation.alias)
        return result

    def travelling_guests(self) -> dict[str, dict]:
        return {role: _pawn(self.last_game, observation.alias)
                for role, observation in self.members.match_pawns(self.last_game).items()}

    def begin_zone_guests(self, transport) -> None:
        self.members.begin_travel()
        self.return_handshake = None
        self.return_census_floor = None
        self.return_waiting = None
        self.return_retried.clear()
        if transport:
            transport.restrict_game_routes(set())

    async def reopen_zone_guests(self, transport, roles: dict[str, str], prior: dict[str, dict]) -> None:
        if transport:
            await transport.refresh_game_routes()
        self.returning_guests.clear()
        states = {entry["character_id"]: entry["state"] for entry in self.store.status()["characters"]}
        for member in self.members.members:
            member.restore = {}
            member.spawn_near_host = True
            if member.role in roles:
                state = states[member.character_id]
                frame = state.get("native_guest_save")
                if frame:
                    member.restore["native_guest_save"] = frame
                health = prior.get(member.role, {}).get("health")
                if health is not None:
                    member.restore["health"] = health
                self.returning_guests.append(member.guest_id)
        self._reserve_next_return()

    def _reserve_next_return(self) -> None:
        self.returning_guests = [identifier for identifier in self.returning_guests
                                if (member := self.members.get(identifier)) is not None and not member.ready]
        if not self.enrollment.pending and not self._return_blocked():
            self.return_census_floor = None
            for identifier in self.returning_guests:
                member = self.members.get(identifier)
                if member.disconnected_at is None and member.binding is None:
                    result = self.members.reserve_returning(identifier, self.last_game)
                    if result.state == "reserved" and result.reservation and identifier not in self.return_retried:
                        self.return_waiting = result.reservation.id
                    break
        first = self.members.get(self.returning_guests[0]) if self.returning_guests else None
        self.awaiting_zone_guest = first.character_id if first else None
        self._update_native_relay()

    async def _expire_return_handshake(self, result, transport) -> None:
        # Retire the route only when the qualified census still shows no new connection.
        # A visible native connection must finish or depart before another guest can enroll.
        reservation, deadline = result.reservation, self.return_handshake
        if (result.state != "pending" or result.reason != "awaiting_new_connection" or not reservation
                or reservation.guest_id not in self.returning_guests
                or deadline is None or deadline[0] != reservation.id or time.monotonic() < deadline[1]):
            return
        identifier = reservation.guest_id
        self.enrollment.cancel(reservation.id)
        self.return_census_floor = self.last_game["sequence"]
        self.returning_guests.remove(identifier)
        self.returning_guests.append(identifier)
        self._update_native_relay()
        self.control.event("native_guest_return_timed_out", guest_id=identifier, driver=reservation.driver)
        await transport.abort_guest_route(identifier)

    async def _refresh_pending_return(self, result, transport) -> None:
        reservation = result.reservation
        self.return_waiting = None
        if (result.state == "pending" and result.reason == "awaiting_new_connection" and reservation
                and reservation.guest_id in self.returning_guests and reservation.guest_id not in self.return_retried
                and not self._return_blocked() and not self.stopping
                and time.monotonic() - self.last_seen <= POSITION_MAX_AGE):
            self.return_waiting = reservation.id
        queued = self.return_retry_request
        if not queued:
            return
        identifier, prior_sequence, future = queued
        if future.done():
            return
        if self.return_waiting != identifier:
            future.set_result({"ok": False, "error": "native_return_retry_unavailable"})
        elif self.last_game["sequence"] > prior_sequence:
            assert reservation is not None
            try:
                await transport.refresh_guest_game_route(reservation.guest_id)
            except OSError:
                if not future.done():
                    future.set_result({"ok": False, "error": "native_return_retry_route_unavailable"})
                return
            self.return_retried.add(reservation.guest_id)
            self.return_waiting = None
            self.control.event("native_guest_return_route_refreshed", guest_id=reservation.guest_id,
                               driver=reservation.driver, reservation=identifier, zone_epoch=self.zone_epoch)
            if not future.done():
                future.set_result({"ok": True})

    def should_keep_native_scope(self) -> bool:
        return True

    def game_link_connected(self, transport) -> bool:
        return any(transport.connected(member.guest_id) for member in self.members.members)

    def checkpoint_guest_changes(self, status: dict, guest_records: dict[str, dict]) -> dict[str, dict]:
        leases = {lease["character_id"] for lease in status["leases"]}
        frames = {entry["character_id"]: entry["state"].get("native_guest_save") for entry in status["characters"]}
        members = {member.role: member for member in self.members.members}
        changes = {}
        for role, observation in self.members.match_pawns(self.last_game).items():
            member = members[role]
            if not member.ready or member.character_id not in leases:
                continue
            state = _pawn(self.last_game, observation.alias)
            frame = guest_records.get(member.character_id) or frames.get(member.character_id)
            if frame:
                if frame["character_id"] != member.character_id:
                    raise RuntimeError("The native save belongs to a different guest")
                state["native_guest_save"] = frame
            changes[member.lease_id] = state
        return changes

    async def capture_guest_records(self, bridge) -> dict[str, dict]:
        records = {}
        participants = self.members.match_pawns(self.last_game)
        for member in self.members.members:
            observation = participants.get(member.role)
            if not member.ready or not observation:
                continue
            pawn = observation.binding.pawn
            scope = self.last_game.get("native_guest_save_members", {}).get(pawn)
            if not scope or scope["state"] != "bound":
                raise RuntimeError("An enrolled native guest has no independent save scope")
            records[member.character_id] = await self.capture_guest_generation(bridge, pawn, member.character_id)
        return records

    async def _place_member(self, bridge, member, observation, *, near_host: bool):
        area = self.last_game["map"]
        if area == SLATE and self.dream_isolated(self.last_game):
            return await bridge.command("native-dream-place-guest", observation.binding.pawn, timeout=5)
        saved = member.restore
        if not near_host and saved.get("map") == area and saved.get("location"):
            with contextlib.suppress(RuntimeError, TimeoutError):
                restored = await bridge.restore(member.role, dict(saved, health=None), binding=observation.binding)
                if restored:
                    return restored
        lead = _pawn(self.last_game, "local")
        if not lead or None in lead["location"]:
            raise RuntimeError("The host has no safe guest arrival position")
        if math.dist(lead["location"], observation.pawn["location"]) <= party.INTERACT_RANGE:
            return self.last_game
        recovery = self.last_game.get("native_roster_spawn", {})
        arrived = recovery.get("location", [])
        binding = observation.binding
        if (tuple(recovery.get(field) for field in ("map", "connection", "controller", "pawn"))
                == (area, binding.connection, binding.controller, binding.pawn)
                and len(arrived) == 3 and None not in arrived
                and math.dist(arrived, observation.pawn["location"]) <= 120
                and math.dist(lead["location"], observation.pawn["location"]) <= 1200):
            return self.last_game
        first = int(member.role.removeprefix("guest") or "1") - 1
        failure = "no placement acknowledgment"
        for candidate in guest_arrival_positions(lead["location"], first):
            try:
                restored = await bridge.restore(member.role, {"location": candidate}, binding=observation.binding)
                if restored:
                    placed = self.members.match_pawns(restored).get(member.role)
                    if placed and math.dist(lead["location"], placed.pawn["location"]) <= party.INTERACT_RANGE:
                        return restored
            except (RuntimeError, TimeoutError) as error:
                failure = str(error)
        raise RuntimeError(f"No safe spawn is available for the exact guest ({failure})")

    async def _attach_member(self, bridge, member, observation) -> None:
        binding = observation.binding
        saved = member.restore
        health = saved.get("health")
        request = (binding.pawn, binding.controller, binding.connection, member.character_id,
                   str(health) if type(health) in (int, float) and math.isfinite(health) else "-")
        if frame := saved.get("native_guest_save"):
            generation = native_host_save.guest_record(self.native_profile, frame, member.character_id)
            request += (generation["slot"],)
        snapshot = await bridge.command("native-guest-bind-exact", *request, timeout=7)
        scope = snapshot.get("native_guest_save_members", {}).get(binding.pawn, {})
        if scope.get("state") != "bound":
            raise RuntimeError("The game did not detach the exact authenticated guest save")
        if frame:
            proof = snapshot.get("native_guest_save_member_frames", {}).get(binding.pawn, {})
            if proof.get("state") != "restored" or proof.get("slot") != frame["slot"]:
                raise RuntimeError("The game did not restore the authenticated guest save generation")
        self.last_game, self.last_seen = snapshot, time.monotonic()
        snapshot = await self._place_member(bridge, member, observation, near_host=member.spawn_near_host)
        self.last_game, self.last_seen = snapshot, time.monotonic()
        self.members.mark_ready(member.guest_id, binding)
        party.add_player(self.party, member.character_id)
        self.control.event("guest_pawn_ready", character_id=member.character_id, role=member.role, pawn=binding.pawn)

    async def observe(self, bridge, transport) -> None:
        self.relay = transport
        snapshot = bridge.snapshot()
        if snapshot is not None and snapshot.get("sequence") != self.last_game.get("sequence"):
            self.last_game, self.last_seen = snapshot, time.monotonic()
        party.add_player(self.party, "host")
        result = self.members.observe(self.last_game)
        if await self.recover_joining_pawn(bridge, transport, result):
            result = self.members.observe(self.last_game)
        await self._refresh_pending_return(result, transport)
        await self._expire_return_handshake(result, transport)
        if result.state == "bound":
            binding = result.assignment
            self.control.event("native_guest_enrolled", guest_id=binding.guest_id, driver=binding.driver,
                               connection_id=binding.connection_id, pawn=binding.pawn)
        elif result.reason == "known_connection_missing":
            for member in self.members.members:
                if member.binding:
                    departed = self.members.forget_departed(member.guest_id, self.last_game)
                    if departed.state == "released" and member.disconnected_at is None:
                        member.spawn_near_host = True
                        if member.guest_id not in self.returning_guests:
                            self.returning_guests.append(member.guest_id)
        elif result.state == "aborted" and result.reservation:
            identifier = result.reservation.guest_id
            self.control.event("native_guest_enrollment_aborted", guest_id=identifier, reason=result.reason)
            await transport.abort_guest_route(identifier)
        await self._retire_expired_guests(bridge, transport)
        participants = self.members.match_pawns(self.last_game)
        for member in self.members.members:
            observation = participants.get(member.role)
            if observation and not member.ready:
                await self._attach_member(bridge, member, observation)
        self._reserve_next_return()

    async def _retire_expired_guests(self, bridge, transport) -> None:
        expired = self.members.expired()
        still_bound = self.members.match_pawns(self.last_game, include_expired=True) if expired else {}
        candidates = [member for member in expired if member.ready and member.binding
                      and member.lease_id not in self.retirement_processed]
        departing = {member.character_id: row.binding.pawn for member in candidates
                     if (row := still_bound.get(member.role))}
        committed = await self.checkpoint_departing_guests(bridge, departing)
        awaiting = {}
        for member in candidates:
            attempts = self.expiry_capture_attempts.get(member.lease_id, 0) + 1
            if member.character_id not in committed and attempts < GUEST_EXPIRY_CAPTURE_ATTEMPTS:
                awaiting[member.lease_id] = attempts
        self.expiry_capture_attempts = awaiting
        self.retirement_processed = {member.lease_id for member in expired if member.lease_id not in awaiting}
        for member in self.members.expire(self.last_game, awaiting_checkpoint=frozenset(awaiting)):
            if member.binding:
                with contextlib.suppress(RuntimeError, TimeoutError):
                    await bridge.command("native-guest-clear", member.binding.pawn, timeout=3)
            with contextlib.suppress(link.LinkError):
                await transport.release_guest(member.guest_id)

    def deliver_native_deaths(self, bridge) -> None:
        self.pending_native_deaths.extend(bridge.native_deaths())
        if time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            return
        actors = {row["pawn"]: character for character, row in self.native_recipients().items()}
        if lead := self.last_game.get("pawns", {}).get("local", {}).get("name"):
            actors[lead] = "host"
        pending = []
        for slot, operation, pawn in self.pending_native_deaths:
            actor = actors.get(pawn)
            if not actor:
                self.control.event("native_death_rejected", operation_id=operation, reason="pawn_identity_changed")
                continue
            try:
                self.act("host", operation, "native-down", actor, 1000)
            except OSError:
                pending.append((slot, operation, pawn))
            except (RuntimeError, ValueError) as error:
                self.control.event("native_death_rejected", operation_id=operation, reason=str(error))
        self.pending_native_deaths = pending

    async def place_party_in_zone(self, bridge, roles: dict[str, str], arrival: dict) -> None:
        self.last_game, self.last_seen = arrival, time.monotonic()
        for member in self.members.members:
            if member.role not in roles:
                continue
            observation = self.members.match_pawns(self.last_game).get(member.role)
            if not observation:
                raise RuntimeError("A party member could not be verified for same-map placement")
            snapshot = await self._place_member(bridge, member, observation, near_host=True)
            self.last_game, self.last_seen = snapshot, time.monotonic()
