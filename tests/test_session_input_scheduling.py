import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, Mock, call, patch
import uuid

from tools.silverfish import (
    native_flashlight, native_loot, native_player_avatar, native_stock_door, native_world_loot, runtime, service,
)
from tools.silverfish.travel import OBSERVATORY, PLAYTEST_MAPS


class SessionInputSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def guest(self, request):
        control = Mock()
        transport = SimpleNamespace(address=("127.0.0.1", 7777), request=request, session_end=None)
        client = service.Client(control, transport, {"character_id": "guest"}, Mock())
        self.addAsyncCleanup(client.cancel_gameplay_snapshot)
        return client

    async def test_slow_snapshot_does_not_hold_guest_input_loop_or_start_duplicate(self):
        release = asyncio.Event()

        async def request(payload, *, timeout):
            await release.wait()
            return {"ok": True, "gameplay": {"players": {}}, "zone_epoch": "new", "zone": "CrossRoads"}

        transport_request = AsyncMock(side_effect=request)
        client = self.guest(transport_request)
        client.zone_epoch = "old"
        await asyncio.wait_for(client.refresh_gameplay(project=False, wait=False), .5)
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False, wait=False)
        await asyncio.sleep(0)
        transport_request.assert_awaited_once_with({"action": "snapshot"}, timeout=2)
        self.assertFalse(client.gameplay_snapshot_ready())
        self.assertEqual(client.zone_epoch, "old")
        release.set()
        await client.refresh_gameplay(project=False)
        self.assertTrue(client.needs_travel)
        self.assertEqual(client.zone_epoch, "new")
        self.assertIsNone(client.snapshot_request)

    async def test_snapshot_age_includes_time_waiting_for_the_gameplay_loop(self):
        client = self.guest(AsyncMock(return_value={
            "ok": True, "gameplay": {"players": {}}, "zone_epoch": "epoch", "zone": "CrossRoads",
        }))
        clock = Mock(return_value=100.0)
        with patch.object(service, "time", SimpleNamespace(monotonic=clock)):
            await client.refresh_gameplay(project=False, wait=False)
            await asyncio.sleep(0)
            self.assertTrue(client.gameplay_snapshot_ready())
            clock.return_value = 102.5
            await client.refresh_gameplay(project=False, wait=False)

        self.assertEqual(client.gameplay, {"players": {}})
        self.assertEqual(client.gameplay_seen, 100.0)

    async def test_queued_stock_interactions_dispatch_before_delayed_flashlight_projection(self):
        session, epoch, operation = "a" * 32, "b" * 32, "c" * 32
        pawn = f"Player_FP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_0"
        door = f"Door_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Door_BP_C_1"
        crate, = (name for name, area in native_loot.CRATES.items() if area == OBSERVATORY
                  and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1"))
        source = {"map": OBSERVATORY, "epoch": epoch, "player": 257, "instance": 2, "switch": 1,
                  "power": True, "pose": [1, 2, 3, 0, 35, 0, .5, .6, .7], "profile": native_flashlight.OFFHAND}
        state = {"state": "connected", "map": OBSERVATORY, "pawns": {"local": {"name": pawn}},
                 native_world_loot.CAPABILITY: native_world_loot.SHARED_PICKUPS_VERSION}
        request = AsyncMock(return_value={"ok": True})
        client = self.guest(request)
        client.transport.connected = client.transport.game_access = True
        client.transport.take_weapon_shots = client.transport.take_loot_motion = lambda: []
        client.invite["native_loot_input"] = True
        client.host_session, client.zone_epoch, client.expected_zone = session, epoch, OBSERVATORY
        client.next_snapshot = float("inf")
        ready = asyncio.get_running_loop().create_future()
        ready.set_result(({"ok": True, "gameplay": {native_flashlight.KEY: source},
                           "zone_epoch": epoch, "zone": OBSERVATORY}, service.time.monotonic()))
        client.snapshot_request = ready
        identity = uuid.UUID(int=1)
        client.bridge.input_namespace = identity
        client.bridge.local_bridge = None
        client.bridge.process.poll.return_value = None
        client.bridge.snapshot.return_value = state
        client.bridge.native_crate_inputs.return_value = [f"loot-crate-open\t{OBSERVATORY}\t{crate}\t{pawn}\t12".encode()]
        client.bridge.interactions.return_value = [(operation, native_stock_door.TARGET_PREFIX + door)]
        client.bridge.native_inventory_view_requests.return_value = []
        client.bridge.native_inventory_equip_requests.return_value = []
        client.bridge.native_cooked_meals.return_value = []
        client.control.requests.return_value = []
        client.control.gameplay_status_due.return_value = False
        client.world_loot.sync = AsyncMock(return_value=state)
        client.project_gameplay = AsyncMock()
        outcome = native_flashlight.parse_view(
            [native_flashlight.VIEW_TAG, epoch, "257", "2", "1", "1o", "1", "-", "0", "35", "0"], "connected")
        marked, allow_reply = asyncio.Event(), asyncio.Event()

        async def delayed_view(action, *_arguments, **_kwargs):
            self.assertEqual(action, native_flashlight.VIEW_TAG)
            marked.set()
            await allow_reply.wait()
            return {**state, native_flashlight.VIEW: outcome}

        async def stop(*_arguments, **_kwargs):
            client.stopping = True

        client.bridge.command = AsyncMock(side_effect=delayed_view)
        client.bridge.wait_for_interaction = AsyncMock(side_effect=stop)
        task = asyncio.create_task(client.run_session())
        try:
            await asyncio.wait_for(marked.wait(), 1)
            crate_operation = uuid.uuid5(identity, f"{session}:{epoch}:guest:crate:12:{crate}").hex
            self.assertEqual([entry.args[0] for entry in request.await_args_list], [
                {"action": "native-loot-open", "operation_id": crate_operation, "target": crate},
                {"action": native_stock_door.ACTION, "operation_id": operation, "target": f"{epoch}@{door}"},
            ])
            client.control.event.assert_any_call("native_stock_crate_interaction", sequence=12,
                                                 operation_id=crate_operation, crate=crate, input_source="memory_input")
            client.control.event.assert_any_call("local_interaction", operation_id=operation, elapsed_ms=ANY)
            self.assertIsNone(client.snapshot_request)
        finally:
            allow_reply.set()
            await asyncio.wait_for(task, 1)
        client.bridge.command.assert_awaited_once()

    async def test_lid_restoration_keeps_its_turn_during_other_projection_backoff(self):
        client = self.guest(AsyncMock())
        client.invite["native_loot_input"] = True
        client.last_game = {"state": "connected", "map": "CrossRoads"}
        attempts = []

        async def other_projection(*_args, **_kwargs):
            attempts.append("other")
            raise RuntimeError("other projection unavailable")

        async def lids(_client):
            attempts.append("lids")

        with (patch.object(service, "project_players", side_effect=other_projection) as other,
              patch.object(client.crate_lids, "sync", side_effect=lids) as restore):
            await client.project_gameplay()
            await client.project_gameplay()

        self.assertEqual(attempts, ["other", "lids", "lids"])
        self.assertEqual(other.await_count, 1)
        self.assertEqual(restore.await_args_list, [call(client), call(client)])

    async def test_aim_edges_request_immediately_and_unchanged_aim_keeps_the_snapshot_cadence(self):
        zone = "CrossRoads"
        aim = native_player_avatar.LOCAL_AIM
        reply = {"ok": True, "gameplay": {}, "zone_epoch": "epoch", "zone": zone}
        request = AsyncMock(return_value=reply)
        client = self.guest(request)
        client.zone_epoch, client.expected_zone = "epoch", zone
        client.last_game = {"state": "connected", "map": zone, aim: False, "player_local_sprint": False}
        clock = Mock(return_value=100.0)

        with patch.object(service, "time", SimpleNamespace(monotonic=clock)):
            await client.refresh_gameplay(project=False)
            clock.return_value = 100.1
            client.last_game[aim] = True
            await client.refresh_gameplay(project=False)
            await client.refresh_gameplay(project=False)
            self.assertEqual(request.await_count, 2)
            clock.return_value = 100.2
            client.last_game[aim] = False
            await client.refresh_gameplay(project=False)
            clock.return_value = 100.69
            await client.refresh_gameplay(project=False)
            self.assertEqual(request.await_count, 3)
            clock.return_value = 100.71
            await client.refresh_gameplay(project=False)

        self.assertEqual(request.await_args_list, [
            call({"action": "snapshot", "avatar_aim": held, "sprinting": False, "zone_epoch": "epoch"}, timeout=2)
            for held in (False, True, False, False)
        ])

    async def test_snapshot_handoff_coalesces_aim_and_sprint_edges_while_waiting(self):
        zone = "CrossRoads"
        aim = native_player_avatar.LOCAL_AIM
        started, release = asyncio.Event(), asyncio.Event()
        reply = {"ok": True, "gameplay": {}, "zone_epoch": "epoch", "zone": zone}

        async def request(payload, *, timeout):
            if not started.is_set():
                started.set()
                await release.wait()
            return reply

        transport_request = AsyncMock(side_effect=request)
        client = self.guest(transport_request)
        client.zone_epoch, client.expected_zone = "epoch", zone
        client.last_game = {"state": "connected", "map": zone, aim: True, "player_local_sprint": False}
        waiting = asyncio.create_task(client.refresh_gameplay(project=False))
        self.addAsyncCleanup(self.cancel_wait, waiting)
        await asyncio.wait_for(started.wait(), .5)
        first = client.snapshot_request
        client.last_game.update({aim: False, "player_local_sprint": False})
        await client.refresh_gameplay(project=False, wait=False)
        client.last_game.update({aim: False, "player_local_sprint": True})
        await client.refresh_gameplay(project=False, wait=False)
        self.assertIs(client.snapshot_request, first)
        self.assertEqual(transport_request.await_count, 1)
        release.set()
        await asyncio.wait_for(waiting, .5)
        self.assertIsNotNone(client.snapshot_request)
        self.assertIsNot(client.snapshot_request, first)
        await client.refresh_gameplay(project=False)
        self.assertEqual(transport_request.await_args_list, [
            call({"action": "snapshot", "avatar_aim": aimed, "sprinting": sprinting, "zone_epoch": "epoch"}, timeout=2)
            for aimed, sprinting in ((True, False), (False, True))
        ])
        self.assertIsNone(client.snapshot_request)

    async def test_aim_edge_waits_for_the_exact_joined_map_and_resends_in_the_new_epoch(self):
        source, destination = "Observatory", "CrossRoads"
        aim = native_player_avatar.LOCAL_AIM
        reply = {"ok": True, "gameplay": {}, "zone_epoch": "new", "zone": destination}
        request = AsyncMock(return_value=reply)
        client = self.guest(request)
        client.zone_epoch, client.expected_zone = "old", source
        client.last_game = {"state": "connected", "map": source, aim: True}
        clock = Mock(return_value=100.0)

        with patch.object(service, "time", SimpleNamespace(monotonic=clock)):
            await client.refresh_gameplay(project=False, wait=False)
            await asyncio.sleep(0)
            client.last_game[aim] = False
            await client.refresh_gameplay(project=False, wait=False)
            self.assertTrue(client.needs_travel)
            self.assertIsNone(client.snapshot_request)
            self.assertEqual(request.await_count, 1)

            client.needs_travel = False
            clock.return_value = 100.6
            await client.refresh_gameplay(project=False)
            client.last_game.update(map=destination)
            await client.refresh_gameplay(project=False)

        self.assertEqual(request.await_args_list, [
            call({"action": "snapshot", "avatar_aim": True, "zone_epoch": "old"}, timeout=2),
            call({"action": "snapshot"}, timeout=2),
            call({"action": "snapshot", "avatar_aim": False, "zone_epoch": "new"}, timeout=2),
        ])

    async def test_cancellation_discards_a_completed_snapshot_from_old_session(self):
        request = AsyncMock(return_value={"ok": True, "gameplay": {"old": True}, "zone_epoch": "retired"})
        client = self.guest(request)
        await client.refresh_gameplay(project=False, wait=False)
        await asyncio.sleep(0)
        self.assertTrue(client.gameplay_snapshot_ready())
        await client.cancel_gameplay_snapshot()
        await client.refresh_gameplay(project=False, wait=False)
        self.assertEqual(client.gameplay, {})
        self.assertIsNone(client.snapshot_request)

    async def test_zone_loading_hint_is_idempotent_until_abort_or_departure(self):
        source, destination = sorted(PLAYTEST_MAPS)
        reply = {"ok": True, "gameplay": {}, "zone_epoch": "current", "zone": source,
                 "travel_in_progress": True, "loading_zone": destination}
        request = AsyncMock(side_effect=[
            {**reply, "loading_zone": []}, reply, reply,
            {**reply, "loading_zone": None, "travel_in_progress": False}, reply,
            {**reply, "loading_zone": None, "zone_epoch": "next", "zone": destination},
        ])
        client = self.guest(request)
        client.host_session, client.zone_epoch, client.expected_zone = "session", "current", source
        client.last_game = {"state": "connected", "map": source, "native_zone_loading_support": True}
        client.bridge.command = AsyncMock(return_value={
            "map": source,
            "native_zone_loading_cover": destination,
            "native_zone_loading_cue": {"destination": destination, "seat": "guest", "dispatched": True},
        })
        for _ in range(6):
            client.next_snapshot = 0
            await client.refresh_gameplay(project=False)
        self.assertEqual(client.bridge.command.await_args_list, [
            call("native-zone-loading-start", destination, source, timeout=2),
            call("native-zone-loading-clear", destination, timeout=2),
            call("native-zone-loading-start", destination, source, timeout=2),
        ])
        self.assertEqual(client.loading_zone, destination)
        self.assertTrue(client.needs_travel)
        presentations = [event for event in client.control.event.call_args_list
                         if event.args[0] == "zone_transition_presentation"]
        self.assertEqual(presentations, [call("zone_transition_presentation", destination=destination,
                                              seat="guest", cue=True, covered=True)] * 2)

    async def test_changed_visit_holds_only_the_remaining_guest_source_fade(self):
        source, destination = sorted(PLAYTEST_MAPS)
        session, prior, next_visit = "a" * 32, "b" * 32, "c" * 32
        source_visit = session, prior, source
        source_frame = {"state": "disconnected", "mode": "client", "map": source, "native_zone_loading_support": True}
        for scenario, started, expected_seconds in (
            ("late", None, 1.0), ("early", 9.25, .25), ("elapsed", 8.0, None),
            ("refused", None, None), ("unknown route", None, None), ("wrong world", None, None),
            ("initial", None, None), ("same visit", None, None), ("new host", None, None),
            ("cancelled", None, 1.0),
        ):
            with self.subTest(scenario=scenario):
                reply = {"ok": True, "gameplay": {}, "zone_epoch": next_visit, "zone": destination,
                         "zone_fade_from": source}
                if scenario == "unknown route":
                    del reply["zone_fade_from"]
                if scenario == "same visit":
                    reply.update(zone_epoch=prior, zone=source)
                client = self.guest(AsyncMock(return_value=reply))
                client.host_session = "d" * 32 if scenario == "new host" else session
                client.zone_epoch, client.expected_zone = prior, source
                client.game_visit = None if scenario == "initial" else source_visit
                state = dict(source_frame, map=runtime.MENU) if scenario == "wrong world" else source_frame
                client.bridge.snapshot.return_value = state
                client.last_game = state
                response = {"map": source, "native_zone_loading_cover": destination}
                client.bridge.command = AsyncMock(return_value=response,
                                                   side_effect=RuntimeError if scenario == "refused" else None)
                if started is not None:
                    client.source_fade = source_visit, destination, started
                    client.loading_zone = destination
                client.leave_for_rejoin, client.open_game_link = AsyncMock(), AsyncMock()
                await client.refresh_gameplay(project=False)

                async def wait_for_fade(_seconds):
                    client.leave_for_rejoin.assert_not_awaited()
                    if scenario == "cancelled":
                        client.stopping = True

                with (patch.object(service, "time", SimpleNamespace(monotonic=lambda: 10.0)),
                      patch.object(asyncio, "sleep", new_callable=AsyncMock, side_effect=wait_for_fade) as delay):
                    await client.reconcile_game()
                if scenario in ("late", "refused", "cancelled"):
                    client.bridge.command.assert_awaited_once_with("native-zone-loading-start", destination, source, timeout=2)
                else:
                    client.bridge.command.assert_not_awaited()
                if expected_seconds is None:
                    delay.assert_not_awaited()
                else:
                    delay.assert_awaited_once_with(expected_seconds)
                if scenario == "cancelled":
                    client.leave_for_rejoin.assert_not_awaited()
                    client.open_game_link.assert_not_awaited()
                else:
                    expected_leave = (state,) if scenario in ("initial", "same visit", "new host") else (state, destination)
                    client.leave_for_rejoin.assert_awaited_once_with(*expected_leave)
                    client.open_game_link.assert_awaited_once()

    async def test_zone_loading_retries_an_unconfirmed_cover_in_the_disconnecting_source(self):
        source, destination = sorted(PLAYTEST_MAPS)
        client = self.guest(AsyncMock())
        client.host_session, client.zone_epoch, client.expected_zone = "session", "current", source
        client.server_transitioning = True
        client.last_game = {"state": "connected", "mode": "client", "map": source,
                            "native_zone_loading_support": True}
        client.bridge.command = AsyncMock(side_effect=[{"map": source}, {
            "map": source,
            "native_zone_loading_cover": destination,
            "native_zone_loading_cue": {"destination": destination, "seat": "guest", "dispatched": True},
        }])
        await client.sync_zone_loading(destination)
        self.assertIsNone(client.source_fade)
        self.assertEqual(client.loading_zone, destination)
        client.last_game["state"] = "disconnected"
        await client.sync_zone_loading(destination)
        await client.sync_zone_loading(destination)
        self.assertEqual(client.source_fade[:2], (("session", "current", source), destination))
        self.assertEqual(client.bridge.command.await_args_list, [
            call("native-zone-loading-start", destination, source, timeout=2),
            call("native-zone-loading-start", destination, source, timeout=2),
        ])

    async def test_stock_door_receipt_projects_before_the_next_world_snapshot(self):
        door = native_stock_door
        area = "/Game/Classes/Maps/Observatory_P"
        actor = f"Door_BP_C {area}.Observatory_P:PersistentLevel.Door_BP_C_1"
        epoch = "c" * 32
        closed = dict(locked=False, broken=False, open=False, pitch=0, yaw=0, roll=0, x=0, y=0, z=0)
        opened = dict(closed, open=True, yaw=90)
        delta = {"map": area, "epoch": epoch, "sequence": 10, "doors": {actor: opened}}
        request = AsyncMock(return_value={"ok": True, door.DELTA: delta})
        client = self.guest(request)
        client.transport.connected = True
        client.zone_epoch = epoch
        client.last_game = {"state": "connected", "map": area, door.KEY: {actor: closed}}
        client.bridge.command = AsyncMock(return_value={**client.last_game, door.KEY: {actor: opened}})
        await client.gameplay_action(door.ACTION, "a" * 32, actor)
        client.bridge.command.assert_awaited_once_with(door.TAG + "-project", area, f"{actor}|0|0|1|0|90|0|0|0|0", timeout=4)
        self.assertEqual(client.last_game[door.KEY][actor], opened)
        await client.gameplay_action(door.ACTION, "a" * 32, actor)
        self.assertEqual(client.bridge.command.await_count, 1)

    async def test_bridge_idle_wait_wakes_for_a_host_request_without_local_input(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock())
            requests = asyncio.Queue()
            wait = asyncio.create_task(bridge.wait_for_interaction(5, wake_if=lambda: not requests.empty()))
            await asyncio.sleep(0)
            requests.put_nowait("guest interaction")
            await asyncio.wait_for(wait, .5)
            self.assertEqual(requests.get_nowait(), "guest interaction")

    async def test_guest_idle_wait_wakes_for_supported_gameplay_events(self):
        streams = (
            ("native-guest-primary-events", "primary_input_offset"),
            ("native-guest-held-fire-events", "held_fire_offset"),
            ("native-inventory-view-events", "inventory_view_offset"),
            ("native-guest-inventory-equip-events", "inventory_equip_offset"),
            ("native-shop-view-events", "shop_view_offset"),
            ("native-wire-board-events", "wire_board_offset"),
            ("native-dialogue-events", "dialogue_offset"),
            ("native-mosby-station-events", "mosby_station_offset"),
        )
        for filename, offset in streams:
            with self.subTest(filename=filename), TemporaryDirectory() as temporary:
                bridge = runtime.Bridge(Path(temporary), Mock())
                path = bridge.directory / filename
                path.write_bytes(b"consumed\n")
                setattr(bridge, offset, path.stat().st_size)
                waiting = asyncio.create_task(bridge.wait_for_interaction(5, guest=True))
                self.addAsyncCleanup(self.cancel_wait, waiting)
                await asyncio.sleep(.05)
                self.assertFalse(waiting.done())
                with path.open("ab") as stream:
                    stream.write(b"new press\n")
                await asyncio.wait_for(waiting, .20)

    async def test_bridge_does_not_spin_when_a_record_has_not_been_consumed(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock())
            path = bridge.directory / "input.tsv"
            path.write_bytes(b"interact\t12")
            await asyncio.wait_for(bridge.wait_for_interaction(5), .2)
            self.assertEqual(bridge.interactions(), [])
            wait = asyncio.create_task(bridge.wait_for_interaction(5))
            self.addAsyncCleanup(self.cancel_wait, wait)
            await asyncio.sleep(.06)
            self.assertFalse(wait.done())
            with path.open("ab") as stream:
                stream.write(b"\n")
            await asyncio.wait_for(wait, .2)
            self.assertEqual(len(bridge.interactions()), 1)

    async def test_bridge_wakes_when_game_publishes_status_backed_interactions(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock())
            path = bridge.directory / "status.tsv"
            frame = "schema\t1\nseq\t{}\nsession\tclient\tconnected\tObservatory\t1\t0\n"
            path.write_text(frame.format(1), encoding="utf-8")
            self.assertEqual(bridge.snapshot()["sequence"], 1)
            wait = asyncio.create_task(bridge.wait_for_interaction(5, guest=True))
            self.addAsyncCleanup(self.cancel_wait, wait)
            await asyncio.sleep(.06)
            self.assertFalse(wait.done())
            temp = path.with_suffix(".tmp")
            temp.write_text(frame.format(2), encoding="utf-8")
            temp.replace(path)
            await asyncio.wait_for(wait, .2)
            self.assertEqual(bridge.snapshot()["sequence"], 2)

    async def cancel_wait(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
