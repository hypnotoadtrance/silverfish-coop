"""Local JSON control and telemetry for the launcher and AI tools."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import msvcrt
import os
from pathlib import Path
import re
import time
import uuid

from .runtime import read_json, write_json

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_RECENT_EVENTS = 64
_EVENT_BYTES = 8_192
_resident_snapshots: dict[Path, dict] = {}


def select_run(work: Path, kind: str, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError("Use a profile or campaign name made from letters, numbers, _ and -")
    root = work / kind
    directory = (root / name).resolve()
    if directory.parent != root.resolve():
        raise ValueError("Profile or campaign path escapes the workspace")
    return directory


class Control:
    def __init__(self, run: Path, *, resident: bool = False):
        self.run = run.absolute()
        self.generation = uuid.uuid4().hex
        self.started = time.time()
        self._gameplay_status_key = None
        self._next_gameplay_status = 0.0
        self._resident = resident
        self._published_phase = None
        self._recent_events: deque[str] | None = deque(maxlen=_RECENT_EVENTS) if resident else None
        run.mkdir(parents=True, exist_ok=True)
        self._lock = (run / "control.lock").open("a+b")
        try:
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"\0")
                self._lock.flush()
            self._lock.seek(0)
            msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._lock.close()
            raise RuntimeError("This profile or campaign is already running") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._resident:
            _resident_snapshots.pop(self.run, None)
        self._lock.close()

    def gameplay_status_due(self, *key) -> bool:
        """Coalesce ordinary diagnostic frames and publish lifecycle changes immediately."""
        now = time.monotonic()
        if key == self._gameplay_status_key and now < self._next_gameplay_status:
            return False
        self._gameplay_status_key = key
        self._next_gameplay_status = now + .25
        return True

    def status(self, **fields) -> None:
        if self._resident:
            fields.update(event_source="memory", status_source="memory")
            if fields.get("active") is False:
                fields["recent_events"] = self._event_tail(_RECENT_EVENTS)
        frame = {
            "schema": 1, "generation": self.generation,
            "pid": os.getpid(), "started_unix": self.started, "updated_unix": time.time(),
            **fields,
        }
        phase = None
        if self._resident:
            _resident_snapshots[self.run] = frame
            phase = fields.get("active"), fields.get("role"), fields.get("state")
            if phase == self._published_phase:
                return
        write_json(self.run / "status.json", frame)
        self._published_phase = phase

    def event(self, event: str, **fields) -> None:
        base = {
            "schema": 1, "time_unix": time.time(), "generation": self.generation,
            "event": event,
        }
        encoded = json.dumps({**base, **fields}, ensure_ascii=True, allow_nan=False)
        if self._recent_events is not None:
            if len(encoded) > _EVENT_BYTES:
                encoded = json.dumps({**base, "details_omitted": True}, ensure_ascii=True)
            self._recent_events.append(encoded)
            return
        with (self.run / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")

    def _event_tail(self, limit: int) -> list[dict]:
        assert self._recent_events is not None
        return [json.loads(line) for line in list(self._recent_events)[-limit:]]

    def requests(self):
        pending = self.run / "requests"
        if not pending.is_dir():
            return
        for path in sorted(pending.glob("*.json"))[:16]:
            if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
                continue
            try:
                data = read_json(path, limit=8192)
            except (OSError, ValueError):
                self.respond(path, False, error="invalid_request")
                continue
            if data.get("generation") != self.generation:
                self.respond(path, False, error="session_changed")
                continue
            if data.get("action") == "status-current" and self._resident:
                self.respond(path, True, status=status(self.run))
                continue
            if data.get("action") == "events-recent" and self._recent_events is not None:
                limit = data.get("tail")
                if type(limit) is int and limit > 0:
                    self.respond(path, True, events=self._event_tail(limit))
                else:
                    self.respond(path, False, error="invalid_event_limit")
                continue
            yield path, data

    def respond(self, path: Path, ok: bool, **result) -> None:
        write_json(self.run / "responses" / path.name, {"id": path.stem, "ok": ok, **result})
        path.unlink(missing_ok=True)


def status(run: Path) -> dict:
    frame = _resident_snapshots.get(run.absolute())
    result = frame.copy() if frame is not None else read_json(run / "status.json")
    result["stale"] = time.time() - result["updated_unix"] > 12
    return result


def _controller_lock_held(run: Path) -> bool:
    try:
        stream = (run / "control.lock").open("r+b")
    except FileNotFoundError:
        return False
    with stream:
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except PermissionError:
            return True
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    return False


async def current_status(run: Path) -> dict:
    result = status(run)
    if (result.get("status_source") == "memory" and result.get("active") is True
            and result.get("state") in {"hosting", "connected", "waiting_for_host"}
            and run.absolute() not in _resident_snapshots):
        if not _controller_lock_held(run):
            result["stale"] = True
            return result
        reply = await submit(run, "status-current")
        if reply.get("ok") is not True:
            raise RuntimeError("The active controller could not return its current status")
        return reply["status"]
    return result


async def submit(run: Path, action: str, **arguments) -> dict:
    current = status(run)
    stale = current["stale"]
    if current.get("status_source") == "memory" and run.absolute() not in _resident_snapshots:
        stale = not _controller_lock_held(run)
    if stale or not current.get("active"):
        raise RuntimeError("No active controller for the chosen profile or campaign")
    request_id = uuid.uuid4().hex
    name = f"{request_id}.json"
    pending = run / "requests" / name
    write_json(pending, {
        "generation": current["generation"], "action": action, **arguments,
    })
    output = run / "responses" / name
    limit = {"status-current": 1_048_576, "events-recent": 1_048_576,
             "native-quest-catalog": 1_048_576, "native-quest-list": 32768,
             "native-dialogue-open": 32768, "native-dialogue-choose": 32768,
             "native-finite-shop-list": 131072, "native-inventory-list": 16384,
             "native-wipe-items": 16384}.get(action, 8192)
    query = action in {"status-current", "events-recent"}
    try:
        for _ in range(15 if query else 200):
            if output.is_file():
                return read_json(output, limit=limit)
            await asyncio.sleep(0.2)
        raise TimeoutError(f"Command response timed out (id {request_id})")
    finally:
        if query:
            for path in (pending, output):
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    pass
