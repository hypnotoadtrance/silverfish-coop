"""Keep a verified standing party member eligible for wipe decisions during restart."""

from dataclasses import dataclass
import math
import time
import uuid

from .campaign import DEFAULT_RECONNECT_GRACE
from .native_host_save import SLOT


KEY = "restart_wipe_guard"


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _identity(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _settling(world):
    party = world.get("party") or {}
    return bool(party.get("wipe") or "host" in party.get("pending_spawns", {}))


def _downed(world):
    return (world.get("party") or {}).get("players", {}).get("host", {}).get("downed") is True


def _standing(world, character):
    return (world.get("party") or {}).get("players", {}).get(character, {}).get("downed") is False


def _frame(state, character, area):
    frame = state.get("native_guest_save") or {}
    if not isinstance(frame, dict):
        return {}
    slot = frame.get("slot")
    if (frame.get("character_id") == character and frame.get("map") == state.get("map") == area
            and isinstance(slot, str) and SLOT.fullmatch(slot)
            and _finite(state.get("health")) and state["health"] > 0):
        return frame
    return {}


@dataclass(frozen=True)
class Ticket:
    guest_id: str
    character_id: str
    slot: str
    expires: float

    def row(self):
        return {"guest_id": self.guest_id, "character_id": self.character_id, "slot": self.slot,
                "expires": self.expires}


def _saved(status, now):
    world = status["world"]
    record = world.get(KEY)
    host = world.get("native_host_save") or {}
    if (_settling(world) or not isinstance(record, dict) or not isinstance(record.get("map"), str)
            or not isinstance(host, dict) or record["map"] != world.get("map")
            or host.get("map") != record["map"] or not isinstance(host.get("slot"), str) or not SLOT.fullmatch(host["slot"])):
        return {}
    rows = record.get("tickets")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        return {}
    characters = {entry["character_id"]: entry for entry in status.get("characters", ())}
    tickets, guests, seen = {}, set(), set()
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"guest_id", "character_id", "slot", "expires"}:
            return {}
        guest, character, slot, expires = (row[key] for key in ("guest_id", "character_id", "slot", "expires"))
        if (not _identity(guest) or not _identity(character) or guest in guests or character in seen
                or not isinstance(slot, str) or not SLOT.fullmatch(slot)
                or not _finite(expires) or expires > now + DEFAULT_RECONNECT_GRACE):
            return {}
        guests.add(guest)
        seen.add(character)
        saved = characters.get(character, {})
        if (saved.get("guest_id") != guest or not _standing(world, character)
                or _frame(saved.get("state", {}), character, record["map"]).get("slot") != slot):
            continue
        if expires > now:
            tickets[character] = Ticket(guest, character, slot, expires)
    return tickets


class RestartWipeGuard:
    def __init__(self, status, *, wall=time.time, clock=time.monotonic):
        self.wall, self.clock = wall, clock
        self.area = status["world"].get("map")
        now, current = self.wall(), self.clock()
        self.waiting = _saved(status, now) if _downed(status["world"]) else {}
        self.deadlines = {character: current + ticket.expires - now for character, ticket in self.waiting.items()}

    def pending(self, world, attached=()):
        """Return only restart tickets; the live roster is maintained elsewhere."""
        area = world.get("map")
        if (area is not None and area != self.area) or _settling(world) or not _downed(world):
            self.waiting.clear()
        now, current = self.wall(), self.clock()
        self.waiting = {character: ticket for character, ticket in self.waiting.items()
                        if character not in attached and _standing(world, character)
                        and now < ticket.expires and current < self.deadlines[character]}
        return self.waiting

    def wipe_roster(self, world, roles, attached):
        return tuple(dict.fromkeys((*roles.values(), *self.pending(world, attached))))

    def admission_refusal(self, world, attached, leases, capacity, guest, character):
        reserved = {ticket.guest_id: ticket.character_id for ticket in self.pending(world, attached).values()}
        if guest in reserved:
            return "slot_reserved" if reserved[guest] != character else None
        occupied = {lease["guest_id"] for lease in leases}
        if reserved and guest not in occupied and len(occupied | reserved.keys()) >= capacity:
            return "slot_reserved"
        return None

    def checkpoint(self, status, world, entered, attached, *, paired, captures, changes, unleased):
        """Carry waiting deadlines forward and issue new ones only with paired frames."""
        if _settling(world):
            world.pop(KEY, None)
            return
        now = self.wall()
        area = world["map"]
        pending = self.pending(world, attached)
        prior = _saved(status, now) if status["world"].get("map") == area else {}
        planned = {frame["character_id"]: state for state in changes.values()
                   if isinstance(frame := state.get("native_guest_save"), dict) and "character_id" in frame}
        replacements = planned | unleased
        tickets = {character: ticket for character, ticket in prior.items()
                   if (character in entered or character in pending) and _standing(world, character)
                   and (character not in replacements
                        or _frame(replacements[character], character, area).get("slot") == ticket.slot)}
        # The logical host-down checkpoint can land before the next paired native capture.
        if paired:
            for lease in status["leases"]:
                character, guest = lease["character_id"], lease["guest_id"]
                if character not in entered or character not in attached or not _standing(world, character):
                    continue
                frame = _frame(planned.get(character, {}), character, area)
                remaining = (DEFAULT_RECONNECT_GRACE if lease["state"] == "active" else lease["grace_remaining"])
                if frame and frame == captures.get(character) and _finite(remaining) and 0 < remaining <= DEFAULT_RECONNECT_GRACE:
                    tickets[character] = Ticket(guest, character, frame["slot"], now + remaining)
        if tickets:
            world[KEY] = {"map": area, "tickets": [tickets[character].row() for character in sorted(tickets)]}
        else:
            world.pop(KEY, None)
