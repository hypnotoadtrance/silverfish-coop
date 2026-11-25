"""Reuse acknowledged gameplay sections across successive control snapshots."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import re
import uuid


BASE = "gameplay_base"
REVISION = "gameplay_revision"
DELTA = "gameplay_delta"
_TOKEN = re.compile(r"[0-9a-f]{32}")
_MAX_PEERS = 16


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


@dataclass(frozen=True)
class _Snapshot:
    revision: str
    scope: tuple
    gameplay: dict


class HostSnapshots:
    def __init__(self):
        self._peers: OrderedDict[tuple[str, str], _Snapshot] = OrderedDict()

    def encode(self, guest: str, character: str, base: object, reply: dict) -> dict:
        if base != "" and not _revision(base):
            # Advertise support without keeping a baseline for a legacy guest.
            reply[REVISION] = ""
            return reply
        current = reply["gameplay"]
        scope = _scope(reply)
        key = guest, character
        previous = self._peers.get(key)
        cached = None
        if previous and base == previous.revision and scope == previous.scope:
            old = previous.gameplay
            changed = {key: value for key, value in current.items()
                       if key not in old or not _same_value(old[key], value)}
            removed = sorted(old.keys() - current.keys())
            if len(changed) + len(removed) < len(current):
                cached = {key: value for key, value in old.items() if key in current}
                cached.update(deepcopy(changed))
                reply[BASE] = previous.revision
                reply[DELTA] = {"changed": changed, "removed": removed}
                del reply["gameplay"]
        revision = reply[REVISION] = uuid.uuid4().hex
        self._peers[key] = _Snapshot(revision, scope, deepcopy(current) if cached is None else cached)
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

    def read(self, reply: dict) -> dict:
        revision = reply.get(REVISION)
        scope = _scope(reply)
        if DELTA in reply:
            previous = self._confirmed
            patch = reply[DELTA]
            changed = patch.get("changed") if isinstance(patch, dict) else None
            removed = patch.get("removed") if isinstance(patch, dict) else None
            if (not previous or previous.revision != reply.get(BASE) or previous.scope != scope
                    or not _revision(revision) or "gameplay" in reply or not isinstance(changed, dict)
                    or not isinstance(removed, list) or any(not isinstance(key, str) for key in removed)
                    or not set(removed) <= previous.gameplay.keys() - changed.keys()):
                self.clear()
                raise RuntimeError("The gameplay snapshot delta has no matching baseline")
            gameplay = previous.gameplay.copy()
            for key in removed:
                gameplay.pop(key, None)
            gameplay.update(changed)
        else:
            gameplay = reply.get("gameplay")
            if not isinstance(gameplay, dict):
                self.clear()
                raise RuntimeError("The gameplay snapshot is unavailable")

        if _revision(revision):
            self._confirmed = _Snapshot(revision, scope, gameplay)
            self.base = revision
            # Local optimistic actions can edit the displayed view before the next snapshot.
            return deepcopy(gameplay)
        self.clear()
        if revision == "":
            self.base = ""
        return gameplay
