"""Supervise an isolated staged Host or Join handoff from the stock title."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
import math
import os
from pathlib import Path
import re
import time
import uuid

from . import native_title_entry, runtime
from .control import Control
from .title_handoff import ACTIONS, TOKEN, Claim, TitleHandoff, _isolated_stock_title, _stock_title


STATUS_FILE = "title-session-status.json"
_ROLE = {"host": "host", "join": "client"}
_GAME_PHASE = {"host": "hosting", "join": "connected"}
_PROGRESS = {"starting", "waiting_for_host", "hosting", "connected", "reconnecting"}
_TITLE_ACTIVE = {"waiting_title", "offered", "starting", "ready", "failed"}
_TITLE_EVENTS = {"ready", "stopped", "failed", "cleanup_unverified"}


@dataclass(frozen=True)
class SessionPlan:
    """Trusted starter and its private status reader and extra readiness checks.

    The starter owns the child resources until it exits, including on cancellation.
    Its progress uses the existing Control status envelope.
    """

    start: Callable[..., Awaitable[None]]
    read_status: Callable[[], dict | None]
    ready: Callable[[dict], bool]
    producer_pid: Callable[[Claim], int | None] | None = None
    resolve_claim: Callable[[Claim], "SessionPlan"] | None = None


def _read_plan_status(plan: SessionPlan) -> dict:
    try:
        status = plan.read_status()
    except (OSError, ValueError):
        return {}
    return status if isinstance(status, dict) else {}


def _is_attempt(status: dict, baseline: str | None, started: float, action: str, producer_pid: int | None) -> bool:
    generation = status.get("generation")
    child_started, updated = status.get("started_unix"), status.get("updated_unix")
    return (
        type(producer_pid) is int and producer_pid > 0 and status.get("schema") == 1
        and type(status.get("pid")) is int and status["pid"] == producer_pid
        and status.get("role") == _ROLE[action]
        and isinstance(generation, str) and generation != baseline and re.fullmatch(r"[a-f0-9]{32}", generation)
        and type(child_started) in (int, float) and type(updated) in (int, float)
        and math.isfinite(child_started) and math.isfinite(updated)
        and started <= child_started <= updated <= time.time()
    )


def _ready(status: dict, action: str) -> bool:
    game = status.get("game")
    if not isinstance(game, dict) or status.get("active") is not True:
        return False
    phase = _GAME_PHASE[action]
    area = game.get("map")
    return (
        (status.get("state"), game.get("state"), game.get("mode")) == (phase, phase, _ROLE[action])
        and isinstance(area, str) and area.startswith("/Game/") and area != native_title_entry.MAP
        and game.get("server_connection") is (action == "join")
        and (action == "host" or status.get("link_connected") is True)
    )


def host_plan(work: Path, campaign: Path, bind: str, port: int, *, headless: bool = False,
              expected_map: str | None = None, **options) -> SessionPlan:
    """Bind a known campaign and a concrete public port to the offered Host action."""
    from .service import host_game

    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("The title Host action requires a concrete UDP port")
    area = expected_map or options.get("start_zone")
    return SessionPlan(
        partial(host_game, work, campaign, bind, port, headless, **options),
        partial(runtime.read_json, campaign / "run" / "status.json"),
        lambda status: status.get("endpoint") == {"address": bind, "port": port}
        and (area is None or status["game"]["map"] == area),
    )


def join_plan(work: Path, profile: Path, invitation: Path, *, headless: bool = False,
              character_id: str | None = None, **options) -> SessionPlan:
    """Bind a validated invitation and exact selected character to the Join action."""
    from .service import join_game, load_invite

    build = runtime.staged_build(work)
    invite = load_invite(invitation, build)
    selected = invite["character_id"] if character_id is None else character_id
    if not isinstance(selected, str) or str(uuid.UUID(selected)) != selected:
        raise ValueError("The title Join action requires an exact character ID")

    async def start():
        if load_invite(invitation, runtime.staged_build(work)) != invite:
            raise ValueError("The selected invitation changed")
        await join_game(work, profile, invitation, headless, selected, **options)

    return SessionPlan(start, partial(runtime.read_json, profile / "run" / "status.json"),
                       lambda status: status.get("character_id") == selected
                       and status.get("gameplay_fresh") is True)


class TitleSession:
    """Own a staged title offer, then the verified child session for its lifetime."""

    def __init__(self, bridge: runtime.Bridge, plans: Mapping[str, SessionPlan], *,
                 retire_title=None, on_title_ready: Callable[[], None] | None = None,
                 title_saved_directory: Path | None = None, poll_interval: float = .1,
                 startup_timeout: float = 120, stall_timeout: float = 60, control: Control | None = None):
        self._plans = dict(plans)
        if any(action not in ACTIONS or not isinstance(plan, SessionPlan) for action, plan in self._plans.items()):
            raise ValueError("Expected supervisor plans for the Host and Join actions")
        if not (math.isfinite(poll_interval) and math.isfinite(startup_timeout) and math.isfinite(stall_timeout)
                and 0 < poll_interval < min(startup_timeout, stall_timeout)):
            raise ValueError("Expected bounded title supervision intervals")
        if (on_title_ready is None) != (title_saved_directory is None):
            raise ValueError("The title handoff requires an isolated save directory and readiness callback")
        self._bridge = bridge
        self._status_path = bridge.directory / STATUS_FILE
        self._retire_title = retire_title or partial(runtime.stop_game, bridge.process)
        self._on_title_ready = on_title_ready
        self._title_saved_directory = title_saved_directory
        self._poll_interval = poll_interval
        self._startup_timeout = startup_timeout
        self._stall_timeout = stall_timeout
        self._handoff = None
        self._published = None
        self._last_failure = None
        self._starting = False
        self._cancel_requested = False
        self._control = control
        self._control_published_at = None
        self._control_generation = None
        self._local_request = None

    def cancel_start(self) -> bool:
        """Cancel only the pending start, allowing a new offer after child cleanup."""
        if not self._starting:
            return False
        self._cancel_requested = True
        return True

    def _publish(self, state: str, **fields) -> None:
        status = dict(schema=1, state=state, last_failure=self._last_failure, **fields)
        changed = status != self._published
        if changed:
            runtime.write_json(self._status_path, dict(status, updated_unix=time.time()))
            self._published = status
        self._publish_control(force=changed)
        if self._control is not None and changed and state in _TITLE_EVENTS:
            self._control.event("title_" + state, **fields, last_failure=self._last_failure)

    def _publish_control(self, *, force=False) -> None:
        if self._control is None or self._published is None:
            return
        now = time.monotonic()
        state = self._published["state"]
        generation = self._handoff.generation if state == "offered" else None
        if (force or generation != self._control_generation or self._control_published_at is None
                or now - self._control_published_at >= 2):
            self._control.status(role="title", active=state in _TITLE_ACTIVE, state=state, handoff_generation=generation,
                                 actions=self._published.get("actions", []), attempt=self._published.get("attempt"),
                                 progress=self._published.get("progress"), last_failure=self._last_failure)
            self._control_published_at, self._control_generation = now, generation

    def _poll_control(self, snapshot=None, *, attempt=None) -> None:
        if self._control is None:
            return
        self._publish_control()
        for path, request in self._control.requests():
            if self._local_request is not None and path == self._local_request[0]:
                continue
            command, generation = request.get("action"), request.get("handoff_generation")
            keys = {"generation", "action", "handoff_generation"}
            if command == "title-select":
                keys.add("role")
            if (command not in ("title-select", "title-cancel") or request.keys() != keys
                    or not isinstance(generation, str) or not TOKEN.fullmatch(generation) or generation == "0" * 32):
                self._control.respond(path, False, error="title_invalid_request")
            elif command == "title-select":
                role = request["role"]
                if role not in ACTIONS:
                    self._control.respond(path, False, error="title_invalid_request")
                    continue
                try:
                    error = self._handoff.queue_local(snapshot, generation, path.stem, role)
                except OSError:
                    error = "mailbox_unavailable"
                if error:
                    self._control.respond(path, False, error="title_" + error)
                else:
                    self._local_request = path, generation, role
            elif not self._starting or attempt is None:
                self._control.respond(path, False, error="title_launch_not_pending")
            elif generation != attempt["generation"]:
                self._control.respond(path, False, error="title_stale_generation")
            elif self._cancel_requested:
                self._control.respond(path, False, error="title_cancel_pending")
            else:
                self.cancel_start()
                self._control.respond(path, True, state="accepted", attempt=attempt)
                self._control.event("title_cancel_accepted", attempt=attempt)

    def _selected(self, claim: Claim | None) -> None:
        if self._control is None:
            return
        source = "panel"
        if self._local_request is not None:
            path, generation, role = self._local_request
            error = self._handoff.last_refusal
            if claim is not None and (claim.request_id, claim.generation, claim.action) == (path.stem, generation, role):
                source = "control"
                attempt = dict(action=claim.action, request_id=claim.request_id, generation=claim.generation)
                self._control.respond(path, True, state="accepted", attempt=attempt)
            elif claim is not None or error or self._handoff.generation != generation:
                self._control.respond(path, False, error="title_" + (error or "handoff_consumed"))
            else:
                return
            self._local_request = None
        if claim is not None:
            self._control.event("title_selection_accepted", source=source,
                                attempt=dict(action=claim.action, request_id=claim.request_id, generation=claim.generation))

    async def run(self) -> None:
        if self._handoff is not None:
            raise RuntimeError("This title supervisor is already running")
        self._handoff = TitleHandoff(self._bridge.directory)
        initial_frame = None
        title_deadline = time.monotonic() + min(self._startup_timeout, 45)
        actions = [action for action in ACTIONS if action in self._plans]
        try:
            while self._bridge.process.poll() is None:
                snapshot = self._bridge.snapshot()
                offered = self._handoff.refresh(snapshot, self._plans)
                if self._on_title_ready is not None:
                    panel = snapshot.get(native_title_entry.PANEL_KEY, {}) if snapshot else {}
                    frame = _isolated_stock_title(snapshot, self._title_saved_directory) if offered else None
                    if not isinstance(panel, dict) or (panel.get("state"), panel.get("mounted"), panel.get("actions")) != (
                        "ready", True, actions,
                    ):
                        frame = None
                    if frame and initial_frame and frame[1] == initial_frame[1] and frame[0] > initial_frame[0]:
                        self._publish("offered", actions=actions)
                        self._on_title_ready()
                        self._on_title_ready = None
                    else:
                        initial_frame = frame
                        if time.monotonic() >= title_deadline:
                            raise TimeoutError("The isolated co-op title did not become ready")
                        if self._control is not None:
                            self._publish("waiting_title", actions=[])
                            self._poll_control()
                        await asyncio.sleep(self._poll_interval)
                        continue
                self._poll_control(snapshot)
                claim = self._handoff.poll(snapshot)
                self._selected(claim)
                if claim is None:
                    self._publish("offered" if offered else "waiting_title", actions=actions if offered else [])
                    await asyncio.sleep(self._poll_interval)
                    continue
                if await self._launch(claim, _stock_title(snapshot)):
                    return
                self._handoff = TitleHandoff(self._bridge.directory)
            self._publish("title_exited")
            if self._on_title_ready is not None:
                raise RuntimeError("The game exited before the isolated co-op title was ready")
        except asyncio.CancelledError:
            self._publish("cancelled")
            raise
        finally:
            if self._local_request is not None:
                self._control.respond(self._local_request[0], False, error="title_stock_title_unready")
                self._local_request = None
            self._handoff.close()

    async def _launch(self, claim: Claim, original_title: tuple) -> bool:
        plan = claim.binding
        attempt = {key: getattr(claim, key) for key in ("action", "request_id", "generation")}
        if plan.resolve_claim is not None:
            try:
                plan = plan.resolve_claim(claim)
                if not isinstance(plan, SessionPlan):
                    raise ValueError("Expected a resolved title session plan")
            except (OSError, TypeError, ValueError, RuntimeError):
                self._last_failure = dict(attempt, code="title_settings_invalid")
                self._publish("failed", attempt=attempt)
                return False
        baseline = _read_plan_status(plan).get("generation")
        started_wall, started = time.time(), time.monotonic()
        last_progress, deadline = started, started + self._startup_timeout
        progress, ready_at, child_generation = None, None, None
        error = "child_exited_before_ready"
        self._starting = True
        self._cancel_requested = False
        self._publish("starting", attempt=attempt, progress="launching")

        def producer_pid():
            return os.getpid() if plan.producer_pid is None else plan.producer_pid(claim)

        async def start():
            if plan.producer_pid is None:
                await plan.start()
            else:
                await plan.start(claim)

        task = asyncio.create_task(start())
        retired = False
        try:
            while not task.done():
                self._poll_control(attempt=attempt)
                if self._cancel_requested:
                    error = "start_cancelled"
                    break
                if self._bridge.process.poll() is not None:
                    error = "title_exited"
                    break
                snapshot = self._bridge.snapshot()
                title = _stock_title(snapshot)
                if snapshot is not None and (title is None or title[1] != original_title[1]):
                    error = "stock_title_changed"
                    break
                now = time.monotonic()
                if now >= deadline or now - last_progress >= self._stall_timeout:
                    error = "startup_timeout" if now >= deadline else "startup_stalled"
                    break
                status = _read_plan_status(plan)
                verified = _is_attempt(status, baseline, started_wall, claim.action, producer_pid())
                if verified:
                    if child_generation is not None and status["generation"] != child_generation:
                        error = "child_generation_changed"
                        break
                    child_generation = status["generation"]
                if verified and status.get("active") is True and status.get("state") in _PROGRESS:
                    game = status.get("game")
                    observed = status["generation"], status["state"], game.get("sequence") if isinstance(game, dict) else None
                    if progress != observed:
                        progress, last_progress = observed, now
                        self._publish("starting", attempt=attempt, progress=status["state"])
                if (verified and time.time() - status["updated_unix"] <= 12
                        and _ready(status, claim.action) and plan.ready(status)):
                    if ready_at is None:
                        ready_at = title[0] if title else original_title[0]
                    if title and title[0] > max(ready_at, original_title[0]):
                        self._starting = False
                        await self._retire_title()
                        retired = True
                        self._last_failure = None
                        self._publish("ready", attempt=attempt, progress=_GAME_PHASE[claim.action])
                        while self._control is not None and not task.done():
                            self._poll_control()
                            await asyncio.wait((task,), timeout=self._poll_interval)
                        await task
                        self._publish("stopped", attempt=attempt)
                        return True
                else:
                    ready_at = None
                await asyncio.sleep(self._poll_interval)
            if task.cancelled():
                error = "start_cancelled"
            elif task.done() and task.exception() is not None:
                error = "launch_failed"
        except (OSError, RuntimeError, ValueError):
            if retired:
                self._last_failure = dict(attempt, code="session_failed")
                self._publish("stopped", attempt=attempt)
                return True
            error = "title_retire_failed" if not self._starting else "readiness_failed"
        finally:
            self._starting = False
            if not task.done():
                task.cancel()
            cleanup = asyncio.gather(task, return_exceptions=True)
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            if interrupted:
                raise asyncio.CancelledError
        status = _read_plan_status(plan)
        current = _is_attempt(status, baseline, started_wall, claim.action, producer_pid())
        cleanup_proven = current and status.get("active") is False and status["generation"] == child_generation
        unsafe = (current and status.get("active") is True) or (child_generation is not None and not cleanup_proven)
        self._last_failure = dict(attempt, code="cleanup_unverified" if unsafe else error)
        self._publish("cleanup_unverified" if unsafe else "failed", attempt=attempt)
        return unsafe
