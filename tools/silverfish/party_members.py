"""Authenticated party members and their exact native Unreal bindings."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
import time
from typing import TypedDict

from .campaign import DEFAULT_RECONNECT_GRACE, CampaignError, CampaignStore
from .native_roster import Assignment, EnrollmentResult, RosterEnrollment, _census


_GUEST_ALIAS = re.compile(r"guest(?:[2-9]|[1-9][0-9]+)?\Z")


class PartyMemberError(ValueError):
    """A roster operation stopped before granting game access."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class PartyMember:
    guest_id: str
    character_id: str
    lease_id: str
    role: str
    restore: dict
    spawn_near_host: bool
    disconnected_at: float | None = None
    grace_until: float | None = None
    binding: Assignment | None = None
    entered: bool = False
    ready: bool = False

    def in_grace(self, now: float) -> bool:
        return self.grace_until is None or now < self.grace_until


@dataclass(frozen=True)
class Admission:
    member: PartyMember
    resumed: bool
    enrollment: EnrollmentResult


class PawnTelemetry(TypedDict):
    name: str
    controller: str
    location: list[float]
    health: float | None
    max_health: float | None


@dataclass(frozen=True)
class PawnObservation:
    role: str
    alias: str
    binding: Assignment
    pawn: PawnTelemetry


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class PartyMembers:
    """Keep independent guest leases while proving at most one new native connection.

    Use this table on the host's event-loop thread. The caller still gates
    Unreal UDP routes, releases expired endpoints, captures save state, and
    performs game-thread operations on the matched native identities.
    """

    def __init__(
        self,
        store: CampaignStore,
        *,
        max_guests: int,
        grace_seconds: float = DEFAULT_RECONNECT_GRACE,
        clock=time.monotonic,
        enrollment: RosterEnrollment | None = None,
    ):
        if type(max_guests) is not int or max_guests < 1:
            raise ValueError("A party requires a positive guest capacity")
        if not _finite(grace_seconds) or grace_seconds < 0:
            raise ValueError("Reconnect grace must be finite and nonnegative")
        self.store = store
        self.max_guests = max_guests
        self.grace_seconds = grace_seconds
        self.clock = clock
        self.enrollment = RosterEnrollment() if enrollment is None else enrollment
        self._members: dict[str, PartyMember] = {}
        self._last_census = None

    @property
    def members(self) -> tuple[PartyMember, ...]:
        """Current records, including reservations waiting for relay cleanup."""
        return tuple(self._members.values())

    def get(self, guest_id: str) -> PartyMember | None:
        return self._members.get(guest_id)

    def _next_role(self) -> str:
        taken = {member.role for member in self.members}
        for index in range(1, self.max_guests + 1):
            role = "guest" if index == 1 else f"guest{index}"
            if role not in taken:
                return role
        raise PartyMemberError("party_full")

    def _sync_bindings(self) -> None:
        assignments = self.enrollment.assignments
        for member in self.members:
            bound = assignments.get(member.guest_id)
            if bound != member.binding:
                member.binding = bound
                member.ready = False

    def admit(self, guest_id: str, credential: str, character_id: str, game: dict | None) -> Admission:
        """Authenticate, reserve the native route if necessary, then activate the lease."""
        try:
            characters = self.store.list_characters(guest_id, credential)
        except CampaignError as error:
            raise PartyMemberError("guest_or_character_unavailable") from error
        if all(saved["character_id"] != character_id for saved in characters):
            raise PartyMemberError("guest_or_character_unavailable")

        previous = self.get(guest_id)
        if previous is not None:
            if not previous.in_grace(self.clock()):
                raise PartyMemberError("slot_cleanup_pending")
            if previous.character_id != character_id or previous.disconnected_at is None:
                raise PartyMemberError("guest_or_character_unavailable")
            role = previous.role
        else:
            role = self._next_role()

        enrollment = self.enrollment.reserve(guest_id, character_id, game)
        self._sync_bindings()
        if enrollment.state not in ("reserved", "pending", "bound"):
            raise PartyMemberError(enrollment.reason)
        try:
            lease = self.store.activate(guest_id, credential, character_id, grace_seconds=self.grace_seconds)
        except CampaignError as error:
            if enrollment.state == "reserved":
                self.enrollment.cancel(enrollment.reservation.id)
            raise PartyMemberError("guest_or_character_unavailable") from error

        if previous is not None and lease["resumed"]:
            member = previous
            member.disconnected_at = None
            member.grace_until = None
            if member.binding is None:
                member.restore = deepcopy(lease["character"]["state"])
        else:
            member = PartyMember(
                guest_id, character_id, lease["lease_id"], role,
                deepcopy(lease["character"]["state"]), spawn_near_host=True,
            )
            self._members[guest_id] = member
        self._sync_bindings()
        return Admission(member, lease["resumed"], enrollment)

    def disconnect(self, guest_id: str) -> PartyMember | None:
        """Reserve this guest only. Repeated link notices keep the original deadline."""
        member = self.get(guest_id)
        if member is None or member.disconnected_at is not None:
            return None
        now = self.clock()
        self.store.disconnect(member.lease_id, grace_seconds=self.grace_seconds)
        member.disconnected_at = now
        member.grace_until = now + self.grace_seconds
        return member

    def expired(self) -> tuple[PartyMember, ...]:
        now = self.clock()
        return tuple(member for member in self.members if not member.in_grace(now))

    def forget_departed(self, guest_id: str, game: dict | None) -> EnrollmentResult:
        """Clear an exact native connection only after the census proves departure.

        The lease, role, and last saved character state stay owned by the member
        until grace expires or the caller explicitly ends the session.
        """
        result = self.enrollment.retire(guest_id, game)
        self._sync_bindings()
        return result

    def expire(self, game: dict | None) -> tuple[PartyMember, ...]:
        """Return expired guests for idempotent relay release.

        Keep a slot allocated while the old native connection remains present,
        then discard its live record after the qualified census proves departure.
        """
        expired = self.expired()
        for member in expired:
            pending = self.enrollment.pending
            if pending and pending.guest_id == member.guest_id:
                self.enrollment.cancel(pending.id)
            if member.binding and self.forget_departed(member.guest_id, game).state != "released":
                continue
            del self._members[member.guest_id]
        return expired

    def begin_travel(self) -> EnrollmentResult:
        """Invalidate bindings to the old world while keeping each entered participant active."""
        result = self.enrollment.begin_travel()
        self._sync_bindings()
        return result

    def reserve_returning(self, guest_id: str, game: dict | None) -> EnrollmentResult:
        """Reserve one already-admitted member's route on the new native driver."""
        member = self.get(guest_id)
        if member is None or not member.in_grace(self.clock()):
            return EnrollmentResult("blocked", "guest_or_character_unavailable")
        result = self.enrollment.reserve(guest_id, member.character_id, game)
        self._sync_bindings()
        return result

    def observe(self, game: dict | None) -> EnrollmentResult:
        result = self.enrollment.observe(game)
        self._sync_bindings()
        if result.state in ("idle", "bound", "pending"):
            self._last_census = _census(game)
        return result

    def match_pawns(self, game: dict | None, *, include_expired: bool = False) -> dict[str, PawnObservation]:
        """Match public aliases to stable roles using this frame's complete native census."""
        try:
            census = _census(game)
        except ValueError:
            return {}
        previous = self._last_census
        if previous is not None:
            if census.sequence < previous.sequence or (census.sequence == previous.sequence and census != previous):
                return {}
        rows = game.get("pawns")
        if not isinstance(rows, dict):
            return {}

        remote_rows = {}
        for alias, raw in rows.items():
            if not isinstance(alias, str) or not _GUEST_ALIAS.fullmatch(alias) or not isinstance(raw, dict):
                continue
            name, controller = raw.get("name"), raw.get("controller")
            if isinstance(name, str) and isinstance(controller, str):
                remote_rows.setdefault((name, controller), []).append((alias, raw))

        result = {}
        now = self.clock()
        for member in self.members:
            binding = member.binding
            if (binding is None or (not include_expired and not member.in_grace(now)) or binding.driver != census.driver
                    or RosterEnrollment._binding_problem(census, binding)):
                continue
            matches = remote_rows.get((binding.pawn, binding.controller), [])
            if len(matches) != 1:
                continue
            alias, raw = matches[0]
            location = raw.get("location")
            health, max_health = raw.get("health"), raw.get("max_health")
            if (not isinstance(location, (list, tuple)) or len(location) != 3
                    or not all(_finite(axis) for axis in location)
                    or (health is not None and not _finite(health))
                    or (max_health is not None and not _finite(max_health))):
                continue
            pawn = PawnTelemetry(name=binding.pawn, controller=binding.controller, location=list(location),
                                 health=health, max_health=max_health)
            result[member.role] = PawnObservation(member.role, alias, binding, pawn)
        return result

    def mark_ready(self, guest_id: str, binding: Assignment) -> None:
        """Commit that this exact pawn has completed restore and placement."""
        member = self.get(guest_id)
        if member is None or binding is None or member.binding != binding or not member.in_grace(self.clock()):
            raise PartyMemberError("native_binding_changed")
        member.entered = True
        member.ready = True
        member.restore = {}
        member.spawn_near_host = False

    def roles(self) -> dict[str, str]:
        """Return the roster for travel, wipes, and reward eligibility."""
        now = self.clock()
        roles = {"host": "host"}
        roles.update((member.role, member.character_id) for member in self.members
                     if member.entered and member.in_grace(now))
        return roles
