"""Associate one authenticated guest at a time with an observed Unreal connection."""

from dataclasses import dataclass
import re


_DRIVER_ID = re.compile(r"d[1-9][0-9]{0,12}\Z")
_CONNECTION_ID = re.compile(r"c[1-9][0-9]{0,12}\Z")
_MAX_CONNECTIONS = 16


@dataclass(frozen=True)
class Assignment:
    guest_id: str
    character_id: str
    driver: str
    connection_id: str
    connection: str
    controller: str
    pawn: str


@dataclass(frozen=True)
class Reservation:
    id: int
    guest_id: str
    character_id: str
    driver: str


@dataclass(frozen=True)
class EnrollmentResult:
    state: str
    reason: str
    reservation: Reservation | None = None
    assignment: Assignment | None = None
    joining: tuple[str, str] | None = None


@dataclass(frozen=True)
class _Connection:
    state: str
    name: str
    controller: str | None
    pawn: str | None


@dataclass(frozen=True)
class _Census:
    sequence: int
    driver: str
    map: str
    connections: dict[str, _Connection]


@dataclass
class _Pending:
    reservation: Reservation
    start_sequence: int
    candidate: tuple[str, _Connection] | None = None


def _text(value: object, *, limit: int = 512) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def _name(value: object, class_name: str) -> bool:
    return _text(value) and value.startswith(class_name + " ") and "Default__" not in value


def _census(game: dict | None) -> _Census:
    if (not isinstance(game, dict) or game.get("mode") != "host" or game.get("state") != "hosting"
            or game.get("server_connection") is not False):
        raise ValueError("census_unavailable")
    roster = game.get("native_roster")
    if not isinstance(roster, dict) or roster.get("state") != "qualified":
        reason = roster.get("reason") if isinstance(roster, dict) else None
        if reason in ("connection_identity_ambiguous", "participant_identity_ambiguous"):
            raise ValueError("census_duplicate_identity")
        if reason in ("connection_count_unqualified", "connection_membership_unqualified"):
            raise ValueError("census_invalid")
        raise ValueError("census_unavailable")

    sequence, map_name = game.get("sequence"), game.get("map")
    driver, count, rows = roster.get("driver"), roster.get("count"), roster.get("connections")
    if (type(sequence) is not int or sequence < 1 or not _text(map_name)
            or not map_name.startswith("/Game/Classes/Maps/")
            or not isinstance(driver, str) or not _DRIVER_ID.fullmatch(driver)
            or type(count) is not int or not 0 <= count <= _MAX_CONNECTIONS
            or type(game.get("remote_clients")) is not int or game["remote_clients"] != count
            or not isinstance(rows, list) or len(rows) != count):
        raise ValueError("census_invalid")

    connections = {}
    names, controllers, pawns = set(), set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("census_invalid")
        key, state, connection = row.get("id"), row.get("state"), row.get("connection")
        controller, pawn = row.get("controller"), row.get("pawn")
        if (not isinstance(key, str) or not _CONNECTION_ID.fullmatch(key)
                or state not in ("ready", "joining") or not _text(connection) or connection == "-"
                or (controller is not None and not _name(controller, "PlayerControler_FP_C"))
                or (pawn is not None and not _name(pawn, "Player_FP_C"))
                or (state == "ready" and (controller is None or pawn is None))
                or (state == "joining" and pawn is not None)):
            raise ValueError("census_invalid")
        if key in connections or connection in names or controller in controllers or pawn in pawns:
            raise ValueError("census_duplicate_identity")
        names.add(connection)
        if controller is not None:
            controllers.add(controller)
        if pawn is not None:
            pawns.add(pawn)
        connections[key] = _Connection(state, connection, controller, pawn)
    return _Census(sequence, driver, map_name, connections)


class RosterEnrollment:
    """Track one live bridge. The caller gates every unpaired Unreal relay during enrollment."""

    def __init__(self) -> None:
        self._latest: _Census | None = None
        self._driver_retired = False
        self._assignments: dict[str, Assignment] = {}
        self._pending: _Pending | None = None
        self._next_id = 0

    @property
    def assignments(self) -> dict[str, Assignment]:
        return self._assignments.copy()

    @property
    def pending(self) -> Reservation | None:
        return self._pending.reservation if self._pending else None

    def _abort(self, reason: str) -> EnrollmentResult:
        reservation = self.pending
        self._pending = None
        return EnrollmentResult("aborted" if reservation else "blocked", reason, reservation)

    def _current(self, game: dict | None) -> _Census | EnrollmentResult:
        try:
            current = _census(game)
        except ValueError as error:
            reason = str(error)
            if reason != "census_unavailable":
                return self._abort(reason)
            return EnrollmentResult("blocked", reason, self.pending)

        previous = self._latest
        if previous is not None:
            if current.sequence < previous.sequence:
                return EnrollmentResult("blocked", "stale_census", self.pending)
            if current.sequence == previous.sequence and current != previous:
                return self._abort("census_sequence_conflict")
            if current.driver == previous.driver:
                if self._driver_retired:
                    return EnrollmentResult("blocked", "driver_retired")
                if current.map != previous.map:
                    return self._abort("driver_map_mismatch")
            else:
                if int(current.driver[1:]) <= int(previous.driver[1:]):
                    return self._abort("stale_driver")
                expected = self._driver_retired
                changed = self._abort("driver_changed")
                self._assignments.clear()
                self._driver_retired = False
                self._latest = current
                if not expected:
                    return changed

        self._latest = current
        return current

    @staticmethod
    def _binding_problem(census: _Census, binding: Assignment) -> str | None:
        connection = census.connections.get(binding.connection_id)
        if connection is None:
            return "known_connection_missing"
        if connection != _Connection("ready", binding.connection, binding.controller, binding.pawn):
            return "known_connection_changed"
        return None

    def _baseline(self, census: _Census) -> tuple[set[str], str | None]:
        known = set()
        for binding in self._assignments.values():
            if problem := self._binding_problem(census, binding):
                return known, problem
            known.add(binding.connection_id)
        return known, None

    def reserve(self, guest_id: str, character_id: str, game: dict | None) -> EnrollmentResult:
        """Reserve the sole new game route, or validate a reconnect to the same native pawn."""
        if not _text(guest_id, limit=128) or not _text(character_id, limit=128):
            raise ValueError("Enrollment requires authenticated guest and character identifiers")
        current = self._current(game)
        if isinstance(current, EnrollmentResult):
            return current
        binding = self._assignments.get(guest_id)
        if binding is not None:
            if binding.character_id != character_id:
                return EnrollmentResult("blocked", "character_change_requires_release")
            if problem := self._binding_problem(current, binding):
                return EnrollmentResult("blocked", problem)
            return EnrollmentResult("bound", "retained_native_connection", assignment=binding)

        if any(assignment.character_id == character_id for assignment in self._assignments.values()):
            return EnrollmentResult("blocked", "character_already_enrolled")
        if self._pending:
            active = self._pending.reservation
            if (active.guest_id, active.character_id) == (guest_id, character_id):
                return EnrollmentResult("pending", "reservation_retained", active)
            return EnrollmentResult("blocked", "enrollment_in_progress")

        known, problem = self._baseline(current)
        if problem:
            return EnrollmentResult("blocked", problem)
        if current.connections.keys() - known:
            return EnrollmentResult("blocked", "unassigned_connections")
        self._next_id += 1
        reservation = Reservation(self._next_id, guest_id, character_id, current.driver)
        self._pending = _Pending(reservation, current.sequence)
        return EnrollmentResult("reserved", "ready_for_relay", reservation)

    def observe(self, game: dict | None, *, expired_guests: frozenset[str] = frozenset()) -> EnrollmentResult:
        """Bind only the sole new ready connection with the original baseline intact."""
        current = self._current(game)
        if isinstance(current, EnrollmentResult):
            return current
        for guest_id in expired_guests:
            binding = self._assignments.get(guest_id)
            if binding and binding.connection_id not in current.connections:
                del self._assignments[guest_id]
        pending = self._pending
        known, problem = self._baseline(current)
        if problem:
            return self._abort(problem)
        newcomers = current.connections.keys() - known
        if pending is None:
            if newcomers:
                return EnrollmentResult("blocked", "unassigned_connections")
            return EnrollmentResult("idle", "known_connections_verified")
        reservation = pending.reservation
        if len(newcomers) > 1:
            return self._abort("ambiguous_new_connections")
        if not newcomers or current.sequence <= pending.start_sequence:
            if pending.candidate:
                return self._abort("new_connection_missing")
            return EnrollmentResult("pending", "awaiting_new_connection", reservation)

        key = next(iter(newcomers))
        connection = current.connections[key]
        if pending.candidate:
            prior_key, prior = pending.candidate
            if (key != prior_key or connection.name != prior.name
                    or (prior.controller is not None and connection.controller != prior.controller)):
                return self._abort("new_connection_changed")
        if connection.controller is None or connection.pawn is None:
            pending.candidate = key, connection
            joining = (connection.name, connection.controller) if connection.controller else None
            return EnrollmentResult("pending", "awaiting_new_pawn", reservation, joining=joining)

        binding = Assignment(reservation.guest_id, reservation.character_id, current.driver, key,
                             connection.name, connection.controller, connection.pawn)
        self._assignments[binding.guest_id] = binding
        self._pending = None
        return EnrollmentResult("bound", "new_connection_verified", reservation, binding)

    def cancel(self, reservation_id: int) -> EnrollmentResult:
        active = self.pending
        if active is None or type(reservation_id) is not int or active.id != reservation_id:
            return EnrollmentResult("blocked", "unknown_reservation")
        return self._abort("reservation_cancelled")

    def retire(self, guest_id: str, game: dict | None) -> EnrollmentResult:
        """Release a known assignment only after a qualified census proves it has left."""
        current = self._current(game)
        if isinstance(current, EnrollmentResult):
            return current
        if self._pending:
            return EnrollmentResult("blocked", "enrollment_in_progress")
        binding = self._assignments.get(guest_id)
        if binding is None:
            return EnrollmentResult("blocked", "unknown_guest")
        if binding.connection_id in current.connections:
            return EnrollmentResult("blocked", "connection_still_present")
        del self._assignments[guest_id]
        return EnrollmentResult("released", "connection_departed", assignment=binding)

    def begin_travel(self) -> EnrollmentResult:
        """Clear native assignments and refuse the outgoing driver until a new one appears."""
        self._driver_retired = self._latest is not None
        self._assignments.clear()
        if self._pending:
            return self._abort("driver_retired")
        return EnrollmentResult("idle", "driver_retired")
