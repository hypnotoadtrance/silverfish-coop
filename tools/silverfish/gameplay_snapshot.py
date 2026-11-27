"""Reuse acknowledged gameplay sections across successive control snapshots."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import re
import uuid


BASE = "gameplay_base"
REVISION = "gameplay_revision"
DELTA = "gameplay_delta"
ROW_UPDATES = "gameplay_row_updates"
WORLD_UPDATES = "gameplay_world_updates"
PLACEMENT_UPDATES = "gameplay_placement_updates"
FIELD_UPDATES = "gameplay_field_updates"
ROW_CHANGES = "rows"
FIELD_CHANGES = "fields"
WORLD_SECTION = "host_world"
_PLACEMENT_SECTION = "native_authored_quest_placements"
_TOKEN = re.compile(r"[0-9a-f]{32}")
_MAX_PEERS = 16
_TABLES = {
    WORLD_SECTION: None,
    _PLACEMENT_SECTION: "items",
    "native_loot_rolls": None,
    "native_stock_enemy_loot": None,
    "native_stock_doors": "doors",
    "native_stock_switches": "switches",
    "native_explosive_canisters": "props",
}
_SEQUENCED_TABLES = {"native_stock_doors", "native_stock_switches"}
_FIELD_PATHS = {
    "native_guest_health": ("native_guest_health", None),
    "native_world_view": (WORLD_SECTION, "native_world_view"),
}


def _revision(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _scope(reply: dict) -> tuple:
    return tuple(reply.get(key) for key in ("zone_epoch", "zone", "viewer_role", "travel_in_progress"))


def _same_value(first, second) -> bool:
    if first is second:
        return True
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same_value(value, second[key]) for key, value in first.items())
    if isinstance(first, (list, tuple)):
        return len(first) == len(second) and all(_same_value(a, b) for a, b in zip(first, second))
    return first == second


def _copy_frame(value):
    """Detach mutable wire containers without copying immutable leaves."""
    kind = type(value)
    if kind is dict:
        return {key: _copy_frame(child) for key, child in value.items()}
    if kind is list:
        return [_copy_frame(child) for child in value]
    if kind is tuple:
        return tuple(_copy_frame(child) for child in value)
    if kind in (str, int, float, bool) or value is None:
        return value
    return deepcopy(value)


def _difference(before: dict, after: dict) -> tuple[dict, list[str]]:
    return ({key: value for key, value in after.items() if key not in before or not _same_value(before[key], value)},
            sorted(before.keys() - after.keys()))


def _table(section, field):
    if isinstance(section, dict):
        rows = section if field is None else section.get(field)
        if isinstance(rows, dict):
            return rows
    return None


def _row_patch(section, before, after):
    field = _TABLES[section]
    old, new = _table(before, field), _table(after, field)
    if old is None or new is None:
        return None
    metadata = {}
    if field is not None:
        if before.keys() != after.keys():
            return None
        for key in after.keys() - {field}:
            if _same_value(before[key], after[key]):
                continue
            if key != "sequence" or section not in _SEQUENCED_TABLES or not _sequence(after[key]):
                return None
            metadata[key] = after[key]
    changed, removed = _difference(old, new)
    count = len(changed) + len(removed)
    if metadata and not count or 0 < count <= len(new) // 2 and len(new) >= 4:
        return {"changed": changed, "removed": removed, **metadata}
    return None


def _sequence(value):
    return type(value) is int and 0 <= value < 2**53


def _valid_patch(previous: dict, changed, removed) -> bool:
    return (isinstance(changed, dict) and isinstance(removed, list)
            and all(isinstance(key, str) for key in changed)
            and all(isinstance(key, str) for key in removed)
            and len(removed) == len(set(removed))
            and set(removed) <= previous.keys() - changed.keys())


def _apply(previous: dict, changed: dict, removed: list[str]) -> dict:
    updated = previous.copy()
    for key in removed:
        updated.pop(key)
    updated.update(changed)
    return updated


def _apply_rows(gameplay: dict, sections: dict) -> None:
    for section, patch in sections.items():
        field = _TABLES[section]
        previous = gameplay[section]
        updated = _apply(_table(previous, field), patch["changed"], patch["removed"])
        if field is None:
            gameplay[section] = updated
        else:
            gameplay[section] = {**previous, field: updated}
            if "sequence" in patch:
                gameplay[section]["sequence"] = patch["sequence"]


def _extract_fields(old, changed, rows):
    patches = {}
    for key, (section, field) in _FIELD_PATHS.items():
        if section not in old or (section not in changed and section not in rows):
            continue
        source = rows[section]["changed"] if section in rows else changed
        source_key = field if section in rows else section
        before = _table(old[section], field)
        after = source.get(source_key) if section in rows else _table(source.get(source_key), field)
        if not isinstance(before, dict) or not isinstance(after, dict) or len(after) < 6:
            continue
        updates, removed = _difference(before, after)
        if not 0 < len(updates) + len(removed) <= len(after) // 2:
            continue
        if field is not None and section not in rows:
            # Keep the rest of the world section if it changed in the same reply.
            siblings, retired = _difference(old[section], changed[section])
            del siblings[field]
            if siblings or retired:
                rows[section] = {"changed": siblings, "removed": retired}
        del source[source_key]
        patches[key] = {"changed": updates, "removed": removed}
        if section in rows and not rows[section]["changed"] and not rows[section]["removed"]:
            del rows[section]
    return patches


def _apply_fields(gameplay: dict, fields: dict) -> None:
    for key, patch in fields.items():
        section, field = _FIELD_PATHS[key]
        previous = gameplay[section]
        updated = _apply(_table(previous, field), patch["changed"], patch["removed"])
        gameplay[section] = updated if field is None else {**previous, field: updated}


@dataclass(frozen=True)
class _Snapshot:
    revision: str
    scope: tuple
    gameplay: dict


class HostSnapshots:
    def __init__(self):
        self._peers: OrderedDict[tuple[str, str], tuple[_Snapshot, ...]] = OrderedDict()

    def encode(self, guest: str, character: str, base: object, reply: dict, *,
               row_updates=False, world_updates=False, placement_updates=False, field_updates=False) -> dict:
        reply[ROW_UPDATES] = True
        reply[WORLD_UPDATES] = True
        reply[PLACEMENT_UPDATES] = True
        reply[FIELD_UPDATES] = True
        if base != "" and not _revision(base):
            # Advertise support without keeping a baseline for a legacy guest.
            reply[REVISION] = ""
            return reply
        current = reply["gameplay"]
        scope = _scope(reply)
        key = guest, character
        previous = next((snapshot for snapshot in self._peers.get(key, ())
                         if snapshot.revision == base and snapshot.scope == scope), None)
        cached = None
        if previous:
            old = previous.gameplay
            changed, removed = _difference(old, current)
            rows = {}
            if row_updates:
                for section in sorted(changed.keys() & old.keys() & _TABLES.keys()):
                    if section == WORLD_SECTION and not world_updates:
                        continue
                    if section == _PLACEMENT_SECTION and not placement_updates:
                        continue
                    if patch := _row_patch(section, old[section], changed[section]):
                        rows[section] = patch
                        del changed[section]
            fields = _extract_fields(old, changed, rows) if field_updates and row_updates and world_updates else {}
            if rows or fields or len(changed) + len(removed) < len(current):
                cached = _apply(old, _copy_frame(changed), removed)
                _apply_rows(cached, _copy_frame(rows))
                _apply_fields(cached, _copy_frame(fields))
                reply[BASE] = previous.revision
                reply[DELTA] = {"changed": changed, "removed": removed}
                if rows:
                    reply[DELTA][ROW_CHANGES] = rows
                if fields:
                    reply[DELTA][FIELD_CHANGES] = fields
                del reply["gameplay"]
        revision = reply[REVISION] = uuid.uuid4().hex
        latest = _Snapshot(revision, scope, _copy_frame(current) if cached is None else cached)
        # Interactions can cancel an in-flight reply. Keep the acknowledged
        # base available until the guest confirms a newer one.
        self._peers[key] = (latest, previous) if previous else (latest,)
        self._peers.move_to_end(key)
        if len(self._peers) > _MAX_PEERS:
            self._peers.popitem(last=False)
        return reply


class ClientSnapshots:
    def __init__(self):
        self.clear()

    def clear(self) -> None:
        self.base: str | None = None
        self._confirmed: _Snapshot | None = None
        self.row_updates = False
        self.world_updates = False
        self.placement_updates = False
        self.field_updates = False

    def _delta(self, reply, scope, revision):
        previous = self._confirmed
        patch = reply[DELTA]
        if (not previous or previous.revision != reply.get(BASE) or previous.scope != scope
                or not _revision(revision) or "gameplay" in reply or not isinstance(patch, dict)):
            return None
        base = previous.gameplay
        changed, removed, sections = patch.get("changed"), patch.get("removed"), patch.get(ROW_CHANGES, {})
        fields = patch.get(FIELD_CHANGES, {})
        if not _valid_patch(base, changed, removed) or not isinstance(sections, dict) or not isinstance(fields, dict):
            return None
        if sections and (not self.row_updates or not sections.keys() <= _TABLES.keys() & base.keys()
                         or WORLD_SECTION in sections and not self.world_updates
                         or _PLACEMENT_SECTION in sections and not self.placement_updates
                         or sections.keys() & (changed.keys() | set(removed))):
            return None
        for section, rows in sections.items():
            previous_section = base[section]
            table = _table(previous_section, _TABLES[section])
            if table is None or not isinstance(rows, dict) or not _valid_patch(table, rows.get("changed"), rows.get("removed")):
                return None
            if "sequence" in rows and (section not in _SEQUENCED_TABLES or "sequence" not in previous_section
                                       or not _sequence(rows["sequence"])):
                return None
        if fields and (not self.field_updates or not fields.keys() <= _FIELD_PATHS.keys()):
            return None
        for key, update in fields.items():
            section, field = _FIELD_PATHS[key]
            table = _table(base.get(section), field)
            neighbors = sections.get(section, {})
            if (section in changed or section in removed or table is None
                    or field in neighbors.get("changed", {}) or field in neighbors.get("removed", [])
                    or not isinstance(update, dict) or not _valid_patch(table, update.get("changed"), update.get("removed"))):
                return None
        gameplay = _apply(base, changed, removed)
        _apply_rows(gameplay, sections)
        _apply_fields(gameplay, fields)
        return gameplay

    def read(self, reply: dict) -> dict:
        revision = reply.get(REVISION)
        scope = _scope(reply)
        if DELTA in reply:
            gameplay = self._delta(reply, scope, revision)
            if gameplay is None:
                self.clear()
                raise RuntimeError("The gameplay snapshot delta has no matching baseline")
        else:
            gameplay = reply.get("gameplay")
            if not isinstance(gameplay, dict):
                self.clear()
                raise RuntimeError("The gameplay snapshot is unavailable")

        if _revision(revision):
            self._confirmed = _Snapshot(revision, scope, gameplay)
            self.base = revision
            self.row_updates = reply.get(ROW_UPDATES) is True
            self.world_updates = self.row_updates and reply.get(WORLD_UPDATES) is True
            self.placement_updates = self.row_updates and reply.get(PLACEMENT_UPDATES) is True
            self.field_updates = self.world_updates and reply.get(FIELD_UPDATES) is True
            # Local optimistic actions can edit the displayed view before the next snapshot.
            return _copy_frame(gameplay)
        self.clear()
        if revision == "":
            self.base = ""
            self.row_updates = reply.get(ROW_UPDATES) is True
            self.world_updates = self.row_updates and reply.get(WORLD_UPDATES) is True
            self.placement_updates = self.row_updates and reply.get(PLACEMENT_UPDATES) is True
            self.field_updates = self.world_updates and reply.get(FIELD_UPDATES) is True
        return gameplay
