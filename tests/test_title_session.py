import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from test_title_handoff import ready
from tools.silverfish import runtime, service
from tools.silverfish.title_handoff import DESCRIPTOR_FILE, REQUEST_FILE
from tools.silverfish.title_session import STATUS_FILE, SessionPlan, TitleSession, host_plan, join_plan
from tools.silverfish.travel import OBSERVATORY


class TitleBridge:
    def __init__(self, directory):
        self.directory = directory
        self.process = self
        self.alive = True
        self.sequence = 1
        self.frozen = False
        self.widget = None

    def poll(self):
        return None if self.alive else 0

    def snapshot(self):
        if not self.frozen:
            self.sequence += 1
        result = ready(self.sequence)
        if self.widget:
            result["native_title_entry"]["widget"] = self.widget
        return result

    async def retire(self):
        self.alive = False


class Child:
    def __init__(self, action):
        self.action = action
        self.role = "host" if action == "host" else "client"
        self.phase = "hosting" if action == "host" else "connected"
        self.state = None
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.cleanup = asyncio.Event()
        self.can_cleanup = asyncio.Event()
        self.can_cleanup.set()
        self.generation = uuid.uuid4().hex
        self.calls = 0

    def publish(self, phase, **extra):
        started = self.state["started_unix"] if self.state else time.time()
        self.state = dict(schema=1, generation=self.generation, pid=os.getpid(), role=self.role,
                          started_unix=started, updated_unix=time.time(), state=phase,
                          active=phase not in ("failed", "stopped"), **extra)

    def ready(self, **extra):
        self.publish(self.phase, game=dict(state=self.phase, mode=self.role, map=OBSERVATORY, sequence=19,
                                           server_connection=self.action == "join"),
                     link_connected=self.action == "join", **extra)

    async def start(self):
        self.calls += 1
        self.publish("starting")
        self.started.set()
        try:
            await self.finish.wait()
        finally:
            self.cleanup.set()
            await self.can_cleanup.wait()
            self.publish("stopped")

    def plan(self, ready_check=lambda state: True):
        return SessionPlan(self.start, lambda: self.state, ready_check)


class TitleSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.bridge = TitleBridge(self.directory)
        self.descriptor_path = self.directory / DESCRIPTOR_FILE
        self.request_path = self.directory / REQUEST_FILE

    async def wait(self, test):
        async with asyncio.timeout(2):
            while not (result := test()):
                await asyncio.sleep(.005)
        return result

    def status(self):
        try:
            return runtime.read_json(self.directory / STATUS_FILE)
        except FileNotFoundError:
            return {}

    def generation(self):
        try:
            return self.descriptor_path.read_text(encoding="ascii").split("\t")[1]
        except FileNotFoundError:
            return None

    def request(self, action, generation, request_id="a" * 32):
        self.request_path.write_bytes(f"1\t{generation}\t{request_id}\t{action}\n".encode("ascii"))

    async def stop(self, task):
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def supervise(self, **plans):
        session = TitleSession(self.bridge, plans, retire_title=self.bridge.retire, poll_interval=.005)
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        return session, task

    async def test_retires_title_only_after_exact_child_and_advancing_original_title(self):
        child = Child("host")
        _, task = self.supervise(host=child.plan())
        self.request("host", await self.wait(self.generation))
        await child.started.wait()
        await self.wait(lambda: self.status().get("progress") == "starting")
        self.assertTrue(self.bridge.alive)
        self.assertFalse(self.descriptor_path.exists())
        self.bridge.frozen = True
        child.ready()
        await self.wait(lambda: self.status().get("progress") == "hosting")
        await asyncio.sleep(.025)
        self.assertTrue(self.bridge.alive)
        self.bridge.frozen = False
        await self.wait(lambda: self.status().get("state") == "ready")
        self.assertFalse(self.bridge.alive)
        self.assertFalse(task.done())
        child.finish.set()
        await task
        self.assertEqual(self.status()["state"], "stopped")
        self.assertEqual(child.calls, 1)

    async def test_launch_failure_keeps_title_and_supplies_fresh_generation(self):
        calls = 0

        async def fail():
            nonlocal calls
            calls += 1
            raise RuntimeError("private invitation C:\\private\\invite secret")

        _, task = self.supervise(join=SessionPlan(fail, lambda: None, lambda state: True))
        previous = await self.wait(self.generation)
        self.request("join", previous)
        await self.wait(lambda: self.generation() and self.generation() != previous)
        status = self.status()
        self.assertEqual(status["last_failure"], {
            "action": "join", "code": "launch_failed", "generation": previous, "request_id": "a" * 32,
        })
        self.assertNotIn("private", str(status))
        self.assertTrue(self.bridge.alive)
        self.request("join", previous)
        await self.wait(lambda: not self.request_path.exists())
        self.assertEqual(calls, 1)
        self.assertFalse(task.done())

    async def test_attempt_cancellation_waits_for_owned_cleanup_and_reoffers(self):
        child = Child("host")
        child.can_cleanup.clear()
        self.addCleanup(child.can_cleanup.set)
        session, task = self.supervise(host=child.plan())
        previous = await self.wait(self.generation)
        try:
            self.request("host", previous)
            await child.started.wait()
            await self.wait(lambda: self.status().get("progress") == "starting")
            self.assertTrue(session.cancel_start())
            await child.cleanup.wait()
            self.assertFalse(self.descriptor_path.exists())
            self.assertTrue(self.bridge.alive)
        finally:
            child.can_cleanup.set()
        await self.wait(lambda: self.generation() and self.generation() != previous)
        self.assertEqual(self.status()["last_failure"]["code"], "start_cancelled")
        self.assertFalse(session.cancel_start())
        self.assertFalse(task.done())

    async def test_supervisor_cancellation_during_teardown_leaves_title_alive(self):
        child = Child("host")
        child.can_cleanup.clear()
        self.addCleanup(child.can_cleanup.set)
        session = TitleSession(self.bridge, {"host": child.plan()}, retire_title=self.bridge.retire, poll_interval=.005)
        task = asyncio.create_task(session.run())
        try:
            self.request("host", await self.wait(self.generation))
            await child.started.wait()
            self.assertTrue(session.cancel_start())
            await child.cleanup.wait()
            task.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
        finally:
            child.can_cleanup.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(child.state["state"], "stopped")
        self.assertEqual(self.status()["state"], "cancelled")
        self.assertTrue(self.bridge.alive)
        self.assertFalse(self.descriptor_path.exists())

    async def test_join_waits_for_current_link_and_exact_character(self):
        child = Child("join")
        selected = str(uuid.uuid4())
        _, task = self.supervise(join=child.plan(lambda state: state.get("character_id") == selected))
        self.request("join", await self.wait(self.generation))
        await child.started.wait()
        child.publish("waiting_for_host")
        await self.wait(lambda: self.status().get("progress") == "waiting_for_host")
        child.ready(character_id=str(uuid.uuid4()))
        await self.wait(lambda: self.status().get("progress") == "connected")
        await asyncio.sleep(.02)
        self.assertTrue(self.bridge.alive)
        child.ready(character_id=selected)
        await self.wait(lambda: not self.bridge.alive)
        child.finish.set()
        await task

    async def test_never_uses_prior_controller_file_and_times_out_without_progress(self):
        child = Child("host")
        child.ready()
        session = TitleSession(self.bridge, {"host": SessionPlan(child.finish.wait, lambda: child.state,
                                                                lambda state: True)},
                               retire_title=self.bridge.retire, poll_interval=.005, stall_timeout=.06)
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        previous = await self.wait(self.generation)
        self.request("host", previous)
        await self.wait(lambda: self.generation() and self.generation() != previous)
        self.assertEqual(self.status()["last_failure"]["code"], "startup_stalled")
        self.assertTrue(self.bridge.alive)

    async def test_stock_title_change_cancels_child_without_retiring_the_game(self):
        child = Child("host")
        _, task = self.supervise(host=child.plan())
        previous = await self.wait(self.generation)
        self.request("host", previous)
        await child.started.wait()
        self.bridge.widget = "MainMenu_Widget_C /Engine/Transient.NewMenu"
        child.ready()
        await self.wait(lambda: self.generation() and self.generation() != previous)
        self.assertTrue(child.cleanup.is_set())
        self.assertEqual(self.status()["last_failure"]["code"], "stock_title_changed")
        self.assertTrue(self.bridge.alive)
        self.assertFalse(task.done())

    async def test_active_status_without_child_cleanup_blocks_second_launch(self):
        child = Child("host")

        async def fail():
            child.publish("starting")
            await asyncio.sleep(.02)
            raise OSError()

        _, task = self.supervise(host=SessionPlan(fail, lambda: child.state, lambda state: True))
        self.request("host", await self.wait(self.generation))
        await task
        self.assertEqual(self.status()["last_failure"]["code"], "cleanup_unverified")
        self.assertTrue(self.bridge.alive)
        self.assertFalse(self.descriptor_path.exists())

    async def test_host_plan_passes_only_bound_configuration_and_checks_public_endpoint(self):
        work, campaign = self.directory, self.directory / "campaign"
        with patch.object(service, "host_game", new_callable=AsyncMock) as launch:
            plan = host_plan(work, campaign, "127.0.0.1", 27014, headless=True, start_zone=OBSERVATORY,
                             native_events=True, native_guest=True, native_zones=True)
            await plan.start()
        launch.assert_awaited_once_with(work, campaign, "127.0.0.1", 27014, True, start_zone=OBSERVATORY,
                                        native_events=True, native_guest=True, native_zones=True)
        matching = {"endpoint": {"address": "127.0.0.1", "port": 27014}, "game": {"map": OBSERVATORY}}
        self.assertTrue(plan.ready(matching))
        self.assertFalse(plan.ready({**matching, "endpoint": {**matching["endpoint"], "port": 27015}}))

    async def test_join_plan_checks_bound_invite_again_and_requires_current_gameplay(self):
        work, profile, invitation = self.directory, self.directory / "profile", self.directory / "private.json"
        character = str(uuid.uuid4())
        invite = {"credential": "private", "character_id": character}
        with (patch.object(runtime, "staged_build", return_value="installed-build"),
              patch.object(service, "load_invite", return_value=invite) as read,
              patch.object(service, "join_game", new_callable=AsyncMock) as launch):
            plan = join_plan(work, profile, invitation, headless=True)
            await plan.start()
            launch.assert_awaited_once_with(work, profile, invitation, True, character)
            read.return_value = {**invite, "credential": "changed"}
            with self.assertRaisesRegex(ValueError, "invitation changed"):
                await plan.start()
        self.assertTrue(plan.ready({"character_id": character, "gameplay_fresh": True}))
        self.assertFalse(plan.ready({"character_id": character, "gameplay_fresh": False}))
