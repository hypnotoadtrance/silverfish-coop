"""Local JSON control and telemetry for the launcher and AI tools."""

from __future__ import annotations

import asyncio
import json
import msvcrt
import os
from pathlib import Path
import re
import time
import uuid

from .runtime import read_json, write_json

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


def select_run(work: Path, kind: str, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError("Use a profile or campaign name made from letters, numbers, _ and -")
    root = work / kind
    directory = (root / name).resolve()
    if directory.parent != root.resolve():
        raise ValueError("Profile or campaign path escapes the workspace")
    return directory


class Control:
    def __init__(self, run: Path):
        self.run = run
        self.generation = uuid.uuid4().hex
        self.started = time.time()
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
        self._lock.close()

    def status(self, **fields) -> None:
        write_json(self.run / "status.json", {
            "schema": 1, "generation": self.generation,
            "pid": os.getpid(), "started_unix": self.started, "updated_unix": time.time(),
            **fields,
        })

    def event(self, event: str, **fields) -> None:
        encoded = json.dumps({
            "schema": 1, "time_unix": time.time(), "generation": self.generation,
            "event": event, **fields,
        }, ensure_ascii=True, allow_nan=False)
        with (self.run / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")

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
            yield path, data

    def respond(self, path: Path, ok: bool, **result) -> None:
        write_json(self.run / "responses" / path.name, {"id": path.stem, "ok": ok, **result})
        path.unlink(missing_ok=True)


def status(run: Path) -> dict:
    result = read_json(run / "status.json")
    result["stale"] = time.time() - result["updated_unix"] > 12
    return result


async def submit(run: Path, action: str, **arguments) -> dict:
    current = status(run)
    if current["stale"] or not current.get("active"):
        raise RuntimeError("No active controller for the chosen profile or campaign")
    request_id = uuid.uuid4().hex
    name = f"{request_id}.json"
    write_json(run / "requests" / name, {
        "generation": current["generation"], "action": action, **arguments,
    })
    output = run / "responses" / name
    limit = {"native-quest-catalog": 1_048_576, "native-inventory-list": 16384, "native-wipe-items": 16384}.get(action, 8192)
    for _ in range(200):
        if output.is_file():
            return read_json(output, limit=limit)
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Command response timed out (id {request_id})")
