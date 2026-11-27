import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

from test_title_handoff import ready
from tools.silverfish import native_title_entry, runtime, service, title_host_campaigns, title_session
from tools.silverfish.control import Control
from tools.silverfish.title_handoff import DESCRIPTOR_FILE, REQUEST_FILE
from tools.silverfish.title_session import STATUS_FILE, SessionPlan, TitleSession, host_plan, join_plan
from tools.silverfish.travel import OBSERVATORY


class TitleBridge:
    wait_for = runtime.Bridge.wait_for

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

    def supervise(self, *, control=None, **plans):
        session = TitleSession(self.bridge, plans, retire_title=self.bridge.retire, poll_interval=.005, control=control)
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        return session, task

    def read_control(self, control):
        try:
            return runtime.read_json(control.run / "status.json")
        except FileNotFoundError:
            return {}

    def send_control(self, control, command, generation, **fields):
        name = uuid.uuid4().hex + ".json"
        runtime.write_json(control.run / "requests" / name, {
            "generation": control.generation, "action": command, "handoff_generation": generation, **fields,
        })
        return control.run / "responses" / name

    async def control_offer(self, control):
        return await self.wait(lambda: self.read_control(control).get("handoff_generation"))

    async def control_reply(self, response):
        await self.wait(response.is_file)
        return runtime.read_json(response)

    async def test_join_choice_does_not_wait_for_an_abandoned_manage_characters_read(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        saved.mkdir(parents=True)
        host, join = Child("host"), Child("join")
        stock_snapshot = self.bridge.snapshot

        def snapshot():
            data = stock_snapshot()
            data["save_scope"] = None
            data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["host", "join"])
            data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=data["sequence"], saved_directory=str(saved))
            return data

        self.bridge.snapshot = snapshot
        started, released, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        account = "76561198000000001"
        entry = title_host_campaigns.CampaignEntry("Camp", "Observatory", "2025-01-01 00:00:00", 1,
                                                   str(uuid.uuid4()), 7)

        async def avatars(accounts):
            self.assertEqual(accounts, {account})
            started.set()
            try:
                await released.wait()
            finally:
                finished.set()
            return {}

        session = TitleSession(self.bridge, {"host": host.plan(), "join": join.plan()}, resident=True,
                               on_title_ready=Mock(), title_saved_directory=saved, poll_interval=.005,
                               host_campaign_scan=lambda: (entry,), profile_avatars=avatars,
                               host_campaign_manage=lambda *_: ((str(uuid.uuid4()), "Scout", "Tester", account),))
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        catalogue = self.directory / title_host_campaigns.FILE
        await self.wait(catalogue.is_file)
        generation = self.generation()
        request = self.directory / title_host_campaigns.REQUEST
        request.write_bytes(f"1\t{generation}\t{'a' * 32}\tcharacters\tCamp\t{entry.campaign_id}\t7\t-\n".encode("ascii"))
        await self.wait(started.is_set)
        self.request("join", generation, request_id="b" * 32)
        await self.wait(join.started.is_set)
        self.assertFalse(finished.is_set())
        self.assertFalse((self.directory / title_host_campaigns.RESPONSE).exists())
        await self.stop(task)
        self.assertTrue(finished.is_set())
        self.assertFalse((self.directory / title_host_campaigns.RESPONSE).exists())

    async def test_control_select_waits_for_same_advancing_title_and_reports_accepted_then_ready(self):
        control = self.enterContext(Control(self.directory / "control"))
        child = Child("host")
        _, task = self.supervise(control=control, host=child.plan())
        generation = await self.control_offer(control)
        self.assertEqual(self.read_control(control)["generation"], control.generation)
        self.assertEqual(self.read_control(control)["actions"], ["host"])
        denied = self.send_control(control, "title-select", generation, role="join")
        self.assertEqual((await self.control_reply(denied))["error"], "title_unadvertised_action")
        malformed = self.send_control(control, "title-select", generation, role="host", invite="confidential")
        self.assertEqual((await self.control_reply(malformed))["error"], "title_invalid_request")
        self.bridge.frozen = True
        selection = self.send_control(control, "title-select", generation, role="host")
        await self.wait(self.request_path.is_file)
        self.assertFalse(selection.exists() or child.started.is_set())
        self.bridge.frozen = False
        reply = await self.control_reply(selection)
        self.assertEqual(reply["state"], "accepted")
        self.assertEqual(reply["attempt"], dict(action="host", generation=generation, request_id=selection.stem))
        await child.started.wait()
        self.assertTrue(self.bridge.alive)
        child.ready()
        await self.wait(lambda: self.read_control(control).get("state") == "ready")
        self.assertFalse(self.bridge.alive)
        late = self.send_control(control, "title-cancel", generation)
        self.assertEqual((await self.control_reply(late))["error"], "title_launch_not_pending")
        child.finish.set()
        await task
        self.assertFalse(self.read_control(control)["active"])
        self.assertEqual(self.read_control(control)["state"], "stopped")
        events = (control.run / "events.jsonl").read_text()
        self.assertIn('"event": "title_selection_accepted"', events)
        self.assertIn('"event": "title_ready"', events)
        self.assertIn('"event": "title_stopped"', events)
        self.assertNotIn("confidential", events)

    async def test_physical_choice_and_control_cannot_race_to_start_two_children(self):
        control = self.enterContext(Control(self.directory / "control"))
        host, guest = Child("host"), Child("join")
        _, task = self.supervise(control=control, host=host.plan(), join=guest.plan())
        generation = await self.control_offer(control)
        self.bridge.frozen = True
        self.request("host", generation)
        contender = self.send_control(control, "title-select", generation, role="join")
        self.assertEqual((await self.control_reply(contender))["error"], "title_selection_pending")
        self.bridge.frozen = False
        await asyncio.wait_for(host.started.wait(), 1)
        self.assertEqual((host.calls, guest.calls), (1, 0))
        host.ready()
        await self.wait(lambda: self.read_control(control).get("state") == "ready")
        host.finish.set()
        await task

    async def test_control_cancel_is_bound_to_pending_attempt_and_waits_for_owned_cleanup(self):
        control = self.enterContext(Control(self.directory / "control"))
        child = Child("host")
        child.can_cleanup.clear()
        self.addCleanup(child.can_cleanup.set)
        _, task = self.supervise(control=control, host=child.plan())
        generation = await self.control_offer(control)
        selected = await self.control_reply(self.send_control(control, "title-select", generation, role="host"))
        self.assertTrue(selected["ok"])
        await child.started.wait()
        try:
            bad = self.send_control(control, "title-cancel", "b" * 32)
            self.assertEqual((await self.control_reply(bad))["error"], "title_stale_generation")
            cancellation = await self.control_reply(self.send_control(control, "title-cancel", generation))
            self.assertEqual((cancellation["state"], cancellation["attempt"]), ("accepted", selected["attempt"]))
            await child.cleanup.wait()
            self.assertTrue(self.bridge.alive)
            self.assertIsNone(self.generation())
            self.assertFalse(task.done())
        finally:
            child.can_cleanup.set()
        newer = await self.control_offer(control)
        self.assertNotEqual(generation, newer)
        self.assertEqual(self.read_control(control)["last_failure"]["code"], "start_cancelled")
        replay = self.send_control(control, "title-select", generation, role="host")
        self.assertEqual((await self.control_reply(replay))["error"], "title_stale_generation")
        self.assertEqual(child.calls, 1)

    async def test_physical_settings_resolve_before_baseline_and_invalid_choices_are_redacted(self):
        control = self.enterContext(Control(self.directory / "control"))
        child = Child("host")
        old_start, old_status = AsyncMock(), Mock()

        def resolve(claim):
            if claim.host_settings[0] != "verified_campaign":
                raise ValueError("confidential invalid setting")
            return child.plan()

        _, task = self.supervise(control=control, host=SessionPlan(old_start, old_status, lambda _: True, resolve_claim=resolve))
        generation = await self.control_offer(control)
        self.request_path.write_bytes(f"2\t{generation}\t{'a' * 32}\thost\tinvalid\t\t\n".encode())
        current = await self.wait(lambda: (now := self.read_control(control).get("handoff_generation")) and now != generation and now)
        self.assertTrue(self.bridge.alive)
        self.assertEqual(self.read_control(control)["last_failure"]["code"], "title_settings_invalid")
        self.request_path.write_bytes(f"2\t{current}\t{'c' * 32}\thost\tverified_campaign\t127.0.0.1\t7777\n".encode())
        await asyncio.wait_for(child.started.wait(), 1)
        old_status.assert_not_called()
        old_start.assert_not_awaited()
        self.assertNotIn("verified_campaign", str(self.read_control(control)))
        self.assertNotIn("confidential", (control.run / "events.jsonl").read_text())
        child.ready()
        await self.wait(lambda: self.read_control(control).get("state") == "ready")
        child.finish.set()
        await task

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

    async def test_notifies_original_once_after_advancing_frames_from_the_isolated_panel(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        saved.mkdir(parents=True)
        original = self.bridge.snapshot
        actions = ["join"]
        renders = 0

        def snapshot():
            nonlocal renders
            renders += 1
            data = original()
            data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=actions)
            data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=data["sequence"], saved_directory=str(saved))
            return data

        self.bridge.snapshot = snapshot
        notify = Mock()
        session = TitleSession(self.bridge, {"host": Child("host").plan()}, on_title_ready=notify,
                               title_saved_directory=saved, poll_interval=.005)
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        await self.wait(lambda: renders >= 2)
        notify.assert_not_called()
        self.bridge.frozen = True
        actions = ["host"]
        await self.wait(lambda: renders >= 6)
        notify.assert_not_called()
        self.bridge.frozen = False
        await self.wait(lambda: notify.called)
        self.assertEqual(self.status()["state"], "offered")
        await self.wait(lambda: renders >= 9)
        notify.assert_called_once_with()

    async def test_resident_stop_waits_for_the_file_title_to_report_released_save_scope(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        saved.mkdir(parents=True)
        child = Child("host")
        original = self.bridge.snapshot
        waiting_for_title = asyncio.Event()
        released = asyncio.Event()

        def snapshot():
            data = original()
            if child.state is not None and not released.is_set():
                if child.state["state"] == "stopped":
                    waiting_for_title.set()
                data.update(child.state.get("game", {}))
                data["save_scope"] = "campaign"
                return data
            data["save_scope"] = None
            data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["host"])
            data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=data["sequence"], saved_directory=str(saved))
            return data

        self.bridge.snapshot = snapshot
        session = TitleSession(self.bridge, {"host": child.plan()}, resident=True,
                               on_title_ready=Mock(), title_saved_directory=saved, poll_interval=.005)
        task = asyncio.create_task(session.run())
        self.addAsyncCleanup(self.stop, task)
        offer = await self.wait(self.generation)
        self.request("host", offer)
        await child.started.wait()
        child.ready()
        await self.wait(lambda: self.status().get("state") == "ready")
        child.finish.set()
        await asyncio.wait_for(waiting_for_title.wait(), 1)
        self.assertFalse(task.done())
        released.set()
        await self.wait(lambda: self.generation() and self.generation() != offer)
        self.assertEqual(self.status()["state"], "offered")
        self.assertIsNone(self.status()["last_failure"])

    async def test_failed_ready_host_reoffers_campaign_only_when_cleanup_is_verified(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        saved.mkdir(parents=True)
        for cleanup_failed in (False, True):
            with self.subTest(cleanup_failed=cleanup_failed):
                child = Child("host")
                lease = "campaign"

                async def start():
                    nonlocal lease
                    child.publish("starting")
                    child.started.set()
                    await child.finish.wait()
                    lease = None
                    child.publish("failed", stop_reason="host_returned_to_title", cleanup_failed=cleanup_failed)
                    raise RuntimeError("The active co-op host returned to the title")

                def snapshot():
                    data = TitleBridge.snapshot(self.bridge)
                    if child.state and child.state.get("game"):
                        data.update(child.state["game"])
                        data["save_scope"] = lease
                        return data
                    data["save_scope"] = lease if child.state else None
                    data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["host"])
                    data[native_title_entry.STARTUP_KEY] = dict(
                        map=data["map"], sequence=data["sequence"], saved_directory=str(saved))
                    return data

                self.bridge.snapshot = snapshot
                plan = SessionPlan(start, lambda: child.state, lambda status: True)
                session = TitleSession(self.bridge, {"host": plan}, resident=True,
                                       on_title_ready=Mock(), title_saved_directory=saved, poll_interval=.005)
                task = asyncio.create_task(session.run())
                self.addAsyncCleanup(self.stop, task)
                offer = await self.wait(self.generation)
                self.request("host", offer)
                await child.started.wait()
                child.ready()
                await self.wait(lambda: self.status().get("state") == "ready")
                child.finish.set()
                if cleanup_failed:
                    await asyncio.wait_for(task, 1)
                    self.assertFalse(self.descriptor_path.exists())
                    self.assertEqual(self.status()["last_failure"]["code"], "cleanup_unverified")
                else:
                    await self.wait(lambda: self.generation() and self.generation() != offer)
                    self.assertFalse(task.done())
                    self.assertIn("\trecover-host\n", self.descriptor_path.read_text(encoding="ascii"))
                    self.assertEqual(self.status()["last_failure"]["code"], "session_failed")
                    await self.stop(task)

    async def test_resident_host_uses_its_live_bridge_while_the_title_file_stays_stale(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        saved.mkdir(parents=True)
        old_title = self.bridge.snapshot
        child = Child("host")
        read_game = Mock(return_value=None)
        reading_release = asyncio.Event()
        released = asyncio.Event()
        elapsed = 0.0
        clock = Mock(wraps=time)
        clock.monotonic.side_effect = lambda: elapsed

        def file_snapshot():
            # Non-headless resident sessions stop mirroring gameplay to the old title file.
            data = old_title()
            data["save_scope"] = None
            if child.cleanup.is_set() and not released.is_set():
                reading_release.set()
                data["save_scope"] = "campaign"
            data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["host"])
            data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=data["sequence"], saved_directory=str(saved))
            return data

        self.bridge.snapshot = file_snapshot
        plan = SessionPlan(child.start, lambda: child.state, lambda _: True, read_game=read_game)
        with patch.object(title_session, "time", clock):
            session = TitleSession(self.bridge, {"host": plan}, resident=True, title_saved_directory=saved,
                                   on_title_ready=Mock(), poll_interval=.005, startup_timeout=1, stall_timeout=.5)
            task = asyncio.create_task(session.run())
            self.addAsyncCleanup(self.stop, task)
            offer = await self.wait(self.generation)
            self.request("host", offer)
            await child.started.wait()
            self.bridge.frozen = True
            child.ready()
            await self.wait(lambda: self.status().get("progress") == "hosting")
            live = child.state["game"]
            for update in ({"sequence": 18}, {"map": "/Game/DifferentMap"}, {"mode": "client"}):
                read_game.reset_mock()
                read_game.return_value = {**live, **update}
                await self.wait(lambda: read_game.called)
                self.assertEqual(self.status()["state"], "starting")

            read_game.return_value = {**live, "sequence": 20}
            await self.wait(lambda: self.status().get("state") == "ready")
            elapsed = 2.0
            await asyncio.sleep(0)
            self.assertFalse(child.cleanup.is_set())
            self.assertFalse(task.done())
            self.assertFalse(session.cancel_start())
            self.assertEqual(self.bridge.snapshot()["map"], native_title_entry.MAP)

            child.finish.set()
            await asyncio.wait_for(reading_release.wait(), 1)
            self.assertIsNone(self.generation())
            released.set()
            self.bridge.frozen = False
            await self.wait(lambda: self.generation() and self.generation() != offer)
            self.assertIsNone(self.status()["last_failure"])

    async def test_refuses_a_stock_title_that_reports_the_wrong_effective_save_directory(self):
        saved = self.directory.resolve() / "game-user" / "Saved"
        foreign = self.directory.resolve() / "another-profile" / "Saved"
        for path in (saved, foreign):
            path.mkdir(parents=True)
        original = self.bridge.snapshot

        def snapshot():
            data = original()
            data[native_title_entry.PANEL_KEY] = dict(state="ready", mounted=True, actions=["host"])
            data[native_title_entry.STARTUP_KEY] = dict(map=data["map"], sequence=data["sequence"], saved_directory=str(foreign))
            return data

        self.bridge.snapshot = snapshot
        notify = Mock()
        session = TitleSession(self.bridge, {"host": Child("host").plan()}, on_title_ready=notify,
                               title_saved_directory=saved, poll_interval=.005)
        with self.assertRaisesRegex(ValueError, "effective Unreal save directory"):
            await asyncio.wait_for(session.run(), 1)
        self.assertFalse(self.descriptor_path.exists())
        notify.assert_not_called()

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

    async def test_verified_runtime_retirement_wait_extends_deadlines_and_stays_cancellable(self):
        control = self.enterContext(Control(self.directory / "control"))
        replacement, stalled = Child("join"), Child("join")
        attempts = iter((replacement, stalled))
        plan = SessionPlan(
            replacement.start,
            lambda: replacement.state,
            lambda _: True,
            resolve_claim=lambda _: next(attempts).plan(),
        )
        elapsed = 0.0
        clock = Mock(wraps=time)
        clock.monotonic.side_effect = lambda: elapsed

        async def advance(seconds):
            nonlocal elapsed
            frame = self.bridge.sequence
            elapsed += seconds
            await self.wait(lambda: self.bridge.sequence > frame)

        with patch.object(title_session, "time", clock):
            session = TitleSession(self.bridge, {"join": plan}, retire_title=self.bridge.retire,
                                   poll_interval=.005, startup_timeout=1, stall_timeout=.3, control=control)
            task = asyncio.create_task(session.run())
            self.addAsyncCleanup(self.stop, task)
            first = await self.control_offer(control)
            selected = await self.control_reply(self.send_control(control, "title-select", first, role="join"))
            self.assertTrue(selected["ok"])
            await replacement.started.wait()
            replacement.publish("waiting_for_host", wait_reason=service.NATIVE_RUNTIME_RETIRING)
            await self.wait(lambda: self.read_control(control).get("progress") == "waiting_for_host")

            # Fresh, verified waiting frames continue beyond both normal deadlines.
            for _ in range(7):
                replacement.publish("waiting_for_host", wait_reason=service.NATIVE_RUNTIME_RETIRING)
                await advance(.2)
            self.assertGreater(elapsed, 1)
            self.assertTrue(self.bridge.alive)
            self.assertFalse(replacement.cleanup.is_set())
            self.assertEqual(self.read_control(control)["progress"], "waiting_for_host")

            cancelled = await self.control_reply(self.send_control(control, "title-cancel", first))
            self.assertEqual((cancelled["state"], cancelled["attempt"]), ("accepted", selected["attempt"]))
            await replacement.cleanup.wait()
            second = await self.control_offer(control)
            self.assertNotEqual(first, second)
            self.assertEqual(self.read_control(control)["last_failure"]["code"], "start_cancelled")

            another = await self.control_reply(self.send_control(control, "title-select", second, role="join"))
            self.assertTrue(another["ok"])
            await stalled.started.wait()
            stalled.publish("waiting_for_host", wait_reason="ordinary_wait")
            await self.wait(lambda: self.read_control(control).get("progress") == "waiting_for_host")
            await advance(.2)
            stalled.publish("waiting_for_host", wait_reason="ordinary_wait")
            await advance(.2)
            await stalled.cleanup.wait()
            self.assertNotEqual(second, await self.control_offer(control))
            self.assertEqual(self.read_control(control)["last_failure"]["code"], "startup_stalled")
            self.assertTrue(self.bridge.alive)

    async def test_embedded_attempt_waits_for_the_owned_game_pid_to_accept_this_claim(self):
        child = Child("host")
        claimed = []
        accepted = asyncio.Event()
        game_pid = os.getpid() + 100

        async def start(claim):
            claimed.append(claim)
            await child.start()

        def producer(claim):
            if claimed == [claim] and accepted.is_set():
                return game_pid

        plan = SessionPlan(start, lambda: child.state, lambda _state: True, producer)
        _, task = self.supervise(host=plan)
        generation = await self.wait(self.generation)
        self.request("host", generation)
        await child.started.wait()
        self.assertEqual((claimed[0].generation, claimed[0].request_id), (generation, "a" * 32))
        child.ready()
        child.state["pid"] = game_pid
        await asyncio.sleep(.025)
        self.assertTrue(self.bridge.alive)
        child.state["pid"] = os.getpid()
        accepted.set()
        await asyncio.sleep(.025)
        self.assertTrue(self.bridge.alive)
        child.state["pid"] = game_pid
        await self.wait(lambda: self.status().get("state") == "ready")
        self.assertFalse(self.bridge.alive)
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

    async def test_only_the_verified_orderly_stop_requests_a_fresh_title(self):
        child = Child("host")

        async def start():
            await child.start()
            child.publish("stopped", stop_reason="host_session_ended")
            return "host_session_ended"

        session, task = self.supervise(host=SessionPlan(start, lambda: child.state, lambda state: True))
        self.request("host", await self.wait(self.generation))
        await child.started.wait()
        child.ready()
        await self.wait(lambda: self.status().get("state") == "ready")
        self.assertFalse(session.return_to_title)
        child.finish.set()
        await task
        self.assertTrue(session.return_to_title)

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
