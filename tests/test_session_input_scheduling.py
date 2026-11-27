import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, AsyncMock, Mock, call, patch
import uuid

from tools.silverfish import (
    gameplay_snapshot, native_flashlight, native_loot, native_player_avatar, native_stock_door, native_world_loot,
    runtime, service,
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

    async def test_snapshot_delta_preserves_host_state_across_cancelled_replies(self):
        gameplay = {"host_world": {"clock": 8}, "actors": {"door": False}, "players": {}, "cue": "old"}
        gameplay[native_stock_door.KEY] = {"map": OBSERVATORY, "epoch": "epoch", "sequence": 10, "doors": {
            f"{OBSERVATORY}.Observitory_P:PersistentLevel.Door_Wood_C_{index}": {"open": False, "yaw": index * 15}
            for index in range(128)
        }}
        host = SimpleNamespace(stopping=False, native_guest=False, transitioning=False, zone_epoch="epoch",
                               map_preflight=None, gameplay_snapshots=gameplay_snapshot.HostSnapshots(),
                               authenticated_guest=Mock(return_value=("guest", "character")))
        host.snapshot_for_guest = lambda *_: {
            "ok": True, "gameplay": dict(gameplay), "zone_epoch": host.zone_epoch, "zone": OBSERVATORY,
            "viewer_role": "guest", "travel_in_progress": False,
        }
        replies = []
        lose_next = False

        async def request(payload, *, timeout):
            nonlocal lose_next
            result = service.Host.guest_request(host, "peer", payload)
            replies.append(result)
            if lose_next:
                lose_next = False
                raise TimeoutError
            return result

        client = self.guest(AsyncMock(side_effect=request))

        async def refresh():
            client.next_snapshot = 0
            await client.refresh_gameplay(project=False)

        await refresh()
        self.assertEqual(replies[-1][gameplay_snapshot.REVISION], "")
        await refresh()
        client.gameplay["actors"]["door"] = True  # Optimistic local projection.
        gameplay["host_world"]["clock"] = 9
        del gameplay["cue"]
        await refresh()
        self.assertEqual(replies[-1][gameplay_snapshot.DELTA],
                         {"changed": {"host_world": {"clock": 9}}, "removed": ["cue"]})
        self.assertEqual(client.gameplay, gameplay)
        self.assertFalse(client.gameplay["actors"]["door"])

        base = client.gameplay_snapshots.base
        for clock in (10, 11):
            gameplay["host_world"]["clock"] = clock
            lose_next = True
            await refresh()
            self.assertEqual(replies[-1][gameplay_snapshot.BASE], base)
            self.assertEqual(client.gameplay_snapshots.base, base)

        await refresh()
        self.assertEqual(replies[-1][gameplay_snapshot.BASE], base)
        self.assertIn(gameplay_snapshot.DELTA, replies[-1])
        self.assertEqual(client.gameplay, gameplay)
        recovered_size = len(service.link._encode_response(replies[-1], snapshot=True))

        client.gameplay_snapshots.base = "f" * 32
        await refresh()
        self.assertIn("gameplay", replies[-1])
        self.assertEqual(client.gameplay, gameplay)
        refresh_size = len(service.link._encode_response(replies[-1], snapshot=True))
        self.assertLess(recovered_size, refresh_size / 2)

        host.zone_epoch = "new"
        await refresh()
        self.assertIn("gameplay", replies[-1])
        self.assertTrue(client.needs_travel)

    async def test_snapshot_sends_only_changed_actor_rows_to_a_capable_client(self):
        area = OBSERVATORY
        doors = {f"{area}.Observitory_P:PersistentLevel.Door_Wood_C_{index}": {"open": False, "yaw": index * 15}
                 for index in range(18)}
        changed, removed, *_ = doors
        added = f"{area}.Observitory_P:PersistentLevel.Door_Wood_C_40"
        world = {
            "native_scene": {"game_seconds": 8},
            "native_rules": {"difficulty": 2},
            "native_reputation": {"WesternUnion": 10},
            "native_seed": {"seed": 745},
            "native_weather_ambience": {"active": True},
        }
        gameplay = {"host_world": world, "players": {}, native_stock_door.KEY: {
            "map": area, "epoch": "epoch", "sequence": 10, "doors": doors,
        }}
        host = SimpleNamespace(stopping=False, native_guest=False, transitioning=False, zone_epoch="epoch",
                               map_preflight=None, gameplay_snapshots=gameplay_snapshot.HostSnapshots(),
                               authenticated_guest=Mock(return_value=("guest", "character")))
        host.snapshot_for_guest = lambda *_: {
            "ok": True, "gameplay": dict(gameplay), "zone_epoch": host.zone_epoch, "zone": area,
            "viewer_role": "guest", "travel_in_progress": False,
        }
        requests, replies = [], []

        async def request(payload, *, timeout):
            requests.append(payload)
            reply = deepcopy(service.Host.guest_request(host, "peer", payload))
            replies.append(reply)
            return reply

        client = self.guest(AsyncMock(side_effect=request))

        async def refresh():
            client.next_snapshot = 0
            await client.refresh_gameplay(project=False)

        await refresh()
        self.assertNotIn(gameplay_snapshot.ROW_UPDATES, requests[-1])
        self.assertNotIn(gameplay_snapshot.WORLD_UPDATES, requests[-1])
        await refresh()
        self.assertIs(requests[-1][gameplay_snapshot.ROW_UPDATES], True)
        self.assertIs(requests[-1][gameplay_snapshot.WORLD_UPDATES], True)
        legacy = service.Host.guest_request(host, "legacy", {"action": "snapshot", gameplay_snapshot.BASE: ""})
        actor_only = service.Host.guest_request(host, "actor-only", {"action": "snapshot", gameplay_snapshot.BASE: ""})
        client.gameplay[native_stock_door.KEY]["doors"][changed]["yaw"] = 123
        untouched = next(key for key in doors if key not in {changed, removed})
        client.gameplay[native_stock_door.KEY]["doors"][untouched]["yaw"] = -999
        doors[changed]["open"] = True
        doors.pop(removed)
        doors[added] = {"open": False, "yaw": 90}
        gameplay[native_stock_door.KEY]["sequence"] = 12
        world["native_scene"]["game_seconds"] = 9
        del world["native_weather_ambience"]
        await refresh()
        patch = replies[-1][gameplay_snapshot.DELTA]
        self.assertEqual(patch[gameplay_snapshot.ROW_CHANGES], {
            native_stock_door.KEY: {
                "changed": {changed: doors[changed], added: doors[added]}, "removed": [removed], "sequence": 12,
            },
            gameplay_snapshot.WORLD_SECTION: {
                "changed": {"native_scene": {"game_seconds": 9}}, "removed": ["native_weather_ambience"],
            },
        })
        self.assertEqual(client.gameplay, gameplay)
        self.assertNotIn(native_stock_door.KEY, patch["changed"])
        self.assertNotIn("host_world", patch["changed"])

        actor_only = service.Host.guest_request(host, "actor-only", {
            "action": "snapshot", gameplay_snapshot.BASE: actor_only[gameplay_snapshot.REVISION],
            gameplay_snapshot.ROW_UPDATES: True,
        })
        self.assertEqual(actor_only[gameplay_snapshot.DELTA]["changed"]["host_world"], world)
        self.assertNotIn("host_world", actor_only[gameplay_snapshot.DELTA][gameplay_snapshot.ROW_CHANGES])

        legacy = service.Host.guest_request(host, "legacy", {
            "action": "snapshot", gameplay_snapshot.BASE: legacy[gameplay_snapshot.REVISION],
        })
        self.assertNotIn(gameplay_snapshot.ROW_CHANGES, legacy[gameplay_snapshot.DELTA])
        self.assertEqual(legacy[gameplay_snapshot.DELTA]["changed"][native_stock_door.KEY]["doors"], doors)
        stats = service.link._TransportStatistics()
        compact_wire = service.link._encode_response(replies[-1], snapshot=True, stats=stats)
        actor_wire = service.link._encode_response(actor_only, snapshot=True, stats=stats)
        self.assertLess(len(compact_wire), len(actor_wire))
        self.assertLess(len(compact_wire), len(service.link._encode_response(legacy, snapshot=True)))
        self.assertEqual(stats.snapshot_world_delta_responses, 1)

    async def test_quest_placement_updates_compact_only_negotiated_rows(self):
        loose = service.native_loose_pickup
        epoch = "a" * 32
        world = f"{OBSERVATORY}.Observitory_P"
        sources = [f"{world}:PersistentLevel.Case_Test_C_{index}" for index in range(loose.QUEST_LIMIT)]
        items = {
            source: {"quest": f"Mod.Collect-{index}", "class_path": "/ModContent/Items/Case_Test.Case_Test_C",
                     "transform": {"position": [index * 125.6, 15.0, 17.0],
                                   "rotation": [0, index, 0], "scale": [1, 1, 1]}}
            for index, source in enumerate(sources)
        }
        gameplay = {"players": {}, loose.QUEST_FRAME: {"map": OBSERVATORY, "epoch": epoch, "items": items}}
        host = SimpleNamespace(stopping=False, native_guest=False, transitioning=False, zone_epoch=epoch,
                               map_preflight=None, gameplay_snapshots=gameplay_snapshot.HostSnapshots(),
                               authenticated_guest=Mock(return_value=("guest", "character")))
        host.snapshot_for_guest = lambda *_: {
            "ok": True, "gameplay": gameplay.copy(), "zone_epoch": host.zone_epoch, "zone": OBSERVATORY,
            "viewer_role": "guest", "travel_in_progress": False,
        }
        requests, frames, replies = [], [], []

        async def request(payload, *, timeout):
            requests.append(payload)
            reply = service.Host.guest_request(host, "new", payload)
            frames.append(service.link._encode_response(reply, snapshot=True))
            replies.append(service.link._decode_response(frames[-1])["result"])
            return replies[-1]

        guest = self.guest(AsyncMock(side_effect=request))

        async def refresh():
            guest.next_snapshot = 0
            await guest.refresh_gameplay(project=False)

        await refresh()
        self.assertNotIn(gameplay_snapshot.PLACEMENT_UPDATES, requests[-1])
        await refresh()
        self.assertTrue(requests[-1][gameplay_snapshot.PLACEMENT_UPDATES])
        legacy_request = {"action": "snapshot", gameplay_snapshot.BASE: "", gameplay_snapshot.ROW_UPDATES: True,
                          gameplay_snapshot.WORLD_UPDATES: True}
        legacy_baseline = service.Host.guest_request(host, "old", legacy_request)
        removed, moved = sources[:2]
        del items[removed]
        items[moved]["transform"]["position"][2] += 85.5
        await refresh()
        delta = replies[-1][gameplay_snapshot.DELTA]
        self.assertEqual(delta[gameplay_snapshot.ROW_CHANGES][loose.QUEST_FRAME],
                         {"changed": {moved: items[moved]}, "removed": [removed]})
        self.assertNotIn(loose.QUEST_FRAME, delta["changed"])
        self.assertEqual(guest.gameplay, gameplay)

        legacy_request[gameplay_snapshot.BASE] = legacy_baseline[gameplay_snapshot.REVISION]
        legacy_reply = service.Host.guest_request(host, "old", legacy_request)
        self.assertEqual(legacy_reply[gameplay_snapshot.DELTA]["changed"][loose.QUEST_FRAME]["items"], items)
        self.assertLess(len(frames[-1]), len(service.link._encode_response(legacy_reply, snapshot=True)) / 3)

        # No census is distinct from an empty census. A temporary omission
        # must neither leave a stale delta baseline nor retire every item.
        section = gameplay.pop(loose.QUEST_FRAME)
        await refresh()
        self.assertNotIn(loose.QUEST_FRAME, guest.gameplay)
        gameplay[loose.QUEST_FRAME] = section
        await refresh()
        self.assertEqual(guest.gameplay, gameplay)
        self.assertEqual(replies[-1][gameplay_snapshot.DELTA]["changed"][loose.QUEST_FRAME], section)

    def test_snapshot_rejects_a_world_delta_without_the_host_advertising_it(self):
        client = gameplay_snapshot.ClientSnapshots()
        client.read({"gameplay": {"host_world": {"native_scene": {"game_seconds": 8}}},
                     gameplay_snapshot.REVISION: "a" * 32, gameplay_snapshot.ROW_UPDATES: True})
        with self.assertRaisesRegex(RuntimeError, "no matching baseline"):
            client.read({
                gameplay_snapshot.BASE: "a" * 32, gameplay_snapshot.REVISION: "b" * 32,
                gameplay_snapshot.ROW_UPDATES: True, gameplay_snapshot.WORLD_UPDATES: True,
                gameplay_snapshot.DELTA: {"changed": {}, "removed": [], gameplay_snapshot.ROW_CHANGES: {
                    "host_world": {"changed": {"native_scene": {"game_seconds": 9}}, "removed": []},
                }},
            })
        self.assertIsNone(client.base)
        self.assertFalse(client.row_updates)
        self.assertFalse(client.world_updates)

    async def test_idle_scalar_updates_preserve_vitals_weather_and_door_ordering(self):
        snapshot = gameplay_snapshot
        epoch = "a" * 32
        clock = {"map": OBSERVATORY, "mode": "sample", "epoch": epoch, "sequence": 35,
                 "game_seconds": 123.0, "sun_intensity": 290.12, "sky_r": 0.2961, "wind": 2.5, "rain": 0}
        health = {"map": OBSERVATORY, "epoch": epoch, "owner": "verified-guest-character-id", "health": 88.75,
                  "food": 124.756, "maximum": 250, "stim": 0, "rads": 0, "regen": 190.14}
        actor = f"{OBSERVATORY}.Observitory_P:PersistentLevel.Door_Wood_C_10"
        doors = {"map": OBSERVATORY, "epoch": epoch, "sequence": 28,
                 "doors": {actor: {"open": False, "locked": True, "broken": False, "yaw": 90.4}}}
        gameplay = {"players": {}, "host_world": {"native_world_view": clock, "native_seed": {"value": 42}},
                    "native_guest_health": health, native_stock_door.KEY: doors}
        host = SimpleNamespace(stopping=False, native_guest=False, transitioning=False, zone_epoch=epoch,
                               map_preflight=None, gameplay_snapshots=snapshot.HostSnapshots(),
                               authenticated_guest=Mock(return_value=("guest", "character")))
        host.snapshot_for_guest = lambda *_: {"ok": True, "gameplay": gameplay.copy(), "zone_epoch": epoch,
                                             "zone": OBSERVATORY, "viewer_role": "guest", "travel_in_progress": False}
        requests, replies = [], []

        async def request(payload, *, timeout):
            requests.append(payload)
            replies.append(service.Host.guest_request(host, "new", payload))
            return service.link._decode_response(service.link._encode_response(replies[-1], snapshot=True))["result"]

        client = self.guest(AsyncMock(side_effect=request))
        await client.refresh_gameplay(project=False)
        self.assertNotIn(snapshot.FIELD_UPDATES, requests[-1])
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False)
        older = {"action": "snapshot", snapshot.BASE: "", snapshot.ROW_UPDATES: True, snapshot.WORLD_UPDATES: True}
        baseline = service.Host.guest_request(host, "old", older)
        health["food"] = 123.942
        health.pop("stim")
        clock.update(sequence=65, game_seconds=124.113)
        doors["sequence"] = 59
        client.gameplay["native_guest_health"]["health"] = 0
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False)
        self.assertIs(requests[-1][snapshot.FIELD_UPDATES], True)
        result = replies[-1]
        delta = result[snapshot.DELTA]
        self.assertEqual(delta[snapshot.FIELD_CHANGES], {
            "native_guest_health": {"changed": {"food": 123.942}, "removed": ["stim"]},
            "native_world_view": {"changed": {"sequence": 65, "game_seconds": 124.113}, "removed": []},
        })
        self.assertEqual(delta[snapshot.ROW_CHANGES][native_stock_door.KEY], {"changed": {}, "removed": [], "sequence": 59})
        self.assertEqual(client.gameplay, gameplay)
        old_reply = service.Host.guest_request(host, "old", {**older, snapshot.BASE: baseline[snapshot.REVISION]})
        self.assertNotIn(snapshot.FIELD_CHANGES, old_reply[snapshot.DELTA])
        stats = service.link._TransportStatistics()
        reduced = service.link._encode_response(result, snapshot=True, stats=stats)
        self.assertLess(len(reduced), len(service.link._encode_response(old_reply, snapshot=True)))
        self.assertEqual(stats.snapshot_field_delta_responses, 1)

        # A sibling change must survive even when the nested clock itself is a field patch.
        gameplay["host_world"]["native_seed"] = {"value": 44}
        clock["wind"] = 0
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False)
        self.assertEqual(client.gameplay, gameplay)

    def test_snapshot_rejects_a_field_update_that_overlaps_its_world_row(self):
        snapshot = gameplay_snapshot
        client = snapshot.ClientSnapshots()
        client.read({"gameplay": {"host_world": {"native_world_view": {"game_seconds": 8}}},
                     snapshot.REVISION: "a" * 32, snapshot.ROW_UPDATES: True, snapshot.WORLD_UPDATES: True,
                     snapshot.FIELD_UPDATES: True})
        with self.assertRaisesRegex(RuntimeError, "no matching baseline"):
            client.read({snapshot.BASE: "a" * 32, snapshot.REVISION: "b" * 32,
                         snapshot.DELTA: {"changed": {}, "removed": [],
                                          snapshot.ROW_CHANGES: {"host_world": {"changed": {}, "removed": ["native_world_view"]}},
                                          snapshot.FIELD_CHANGES: {"native_world_view": {"changed": {"game_seconds": 9}, "removed": []}}}})
        self.assertIsNone(client.base)

    def test_snapshot_rejects_actor_row_changes_that_overlap_a_section_replacement(self):
        section = native_loot.STATE_KEY
        gameplay = {section: {"crate": {"items": []}}}
        client = gameplay_snapshot.ClientSnapshots()
        client.read({"gameplay": gameplay, gameplay_snapshot.REVISION: "a" * 32, gameplay_snapshot.ROW_UPDATES: True})
        with self.assertRaisesRegex(RuntimeError, "no matching baseline"):
            client.read({gameplay_snapshot.BASE: "a" * 32, gameplay_snapshot.REVISION: "b" * 32,
                         gameplay_snapshot.DELTA: {"changed": gameplay, "removed": [], gameplay_snapshot.ROW_CHANGES: {
                             section: {"changed": {}, "removed": ["crate"]},
                         }}})
        self.assertIsNone(client.base)
        self.assertFalse(client.row_updates)

    async def test_refused_projected_pickups_release_only_their_local_preview(self):
        client = self.guest(AsyncMock())
        epoch, item, operation = "a" * 32, "b" * 32, "c" * 32
        scope = ("session", epoch, OBSERVATORY, "guest pawn")
        client.stock_interaction_scope = Mock(return_value=scope)
        client.apply_stock_interaction = AsyncMock(side_effect=ValueError("item_already_claimed"))
        client.bridge.command = AsyncMock()
        for target in (f"world-loot:{epoch}:{item}:2", f"stack-drop:{epoch}:{item}"):
            with self.subTest(target=target):
                client.bridge.command.reset_mock()
                await client.dispatch_stock_interaction(scope, operation, target)
                client.bridge.command.assert_awaited_once_with("native-pickup-cancel-preview", target,
                                                               timeout=.75, update_status=False)

    async def test_pickup_dispatches_while_stock_door_is_waiting_for_its_final_pose(self):
        client = self.guest(AsyncMock())
        actor = f"Door_BP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Door_BP_C_1"
        scope, started, settle = ("session", "epoch", OBSERVATORY, "pawn"), asyncio.Event(), asyncio.Event()
        client.stock_interaction_scope = Mock(return_value=scope)

        async def interaction(_scope, operation, _target):
            if operation == "door":
                started.set()
                await settle.wait()
            return {}

        client.apply_stock_interaction = AsyncMock(side_effect=interaction)
        await client.dispatch_stock_interaction(scope, "door", native_stock_door.TARGET_PREFIX + actor)
        await client.dispatch_stock_interaction(scope, "pick", None)
        await asyncio.wait_for(started.wait(), .5)
        self.assertFalse(client.stock_door_requests[actor].done())
        client.apply_stock_interaction.assert_any_await(scope, "pick", None)
        settle.set()
        await client.stock_door_requests[actor]

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

    async def test_crate_reply_wakes_world_loot_and_drops_a_projection_from_the_previous_visit(self):
        crate = next(name for name, area in native_loot.CRATES.items() if area == OBSERVATORY)
        roll = {"id": "a" * 32, "map": OBSERVATORY, "crate": crate, "items": []}
        client = self.guest(AsyncMock(return_value={"ok": True, "native_loot_roll": roll}))
        client.host_session, client.zone_epoch = "session", "old-visit"
        client.expected_zone = OBSERVATORY
        state = {"state": "connected", "map": OBSERVATORY, "sequence": 1, native_world_loot.CAPABILITY: 2}
        client.last_game = state
        client.gameplay_seen = service.time.monotonic()
        client.transport.connected = client.transport.game_access = True
        client.transport.take_loot_motion = lambda: []
        started, finish = asyncio.Event(), asyncio.Event()

        async def project(_bridge, _game, rolls, _generation, zone, **options):
            self.assertEqual((rolls[crate], zone, options["active"]), (roll, "old-visit", True))
            started.set()
            await finish.wait()
            client.stopping = True
            return {**state, "sequence": 3}

        client.world_loot.sync = AsyncMock(side_effect=project)
        task = asyncio.create_task(client.run_world_loot())
        try:
            await client.gameplay_action("native-loot-open", roll["id"], crate)
            await asyncio.wait_for(started.wait(), .5)
            client.zone_epoch = "new-visit"
            current = {**state, "sequence": 2}
            client.last_game = current
            finish.set()
            await asyncio.wait_for(task, .5)
            self.assertIs(client.last_game, current)
            client.world_loot.sync.assert_awaited_once()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_legacy_lid_restoration_keeps_its_turn_during_other_projection_backoff(self):
        client = self.guest(AsyncMock())
        client.bridge.local_bridge = None
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

    async def test_older_world_snapshot_cannot_reverse_a_confirmed_door_pose(self):
        door = native_stock_door
        area, epoch = OBSERVATORY, "c" * 32
        actor = f"Door_BP_C {area}.Observitory_P:PersistentLevel.Door_BP_C_1"
        closed = dict(locked=False, broken=False, open=False, pitch=0, yaw=0, roll=0, x=0, y=0, z=0)
        opened = dict(closed, open=True, yaw=90)
        older = {"map": area, "epoch": epoch, "sequence": 9, "doors": {actor: closed}}
        confirmed = {**older, "sequence": 10, "doors": {actor: opened}}
        request = AsyncMock(return_value={"ok": True, door.DELTA: confirmed})
        client = self.guest(request)
        client.transport.connected = True
        client.zone_epoch = epoch
        client.last_game = {"state": "connected", "map": area, door.KEY: {actor: closed}}
        client.bridge.command = AsyncMock(return_value={**client.last_game, door.KEY: {actor: opened}})
        await client.gameplay_action(door.ACTION, "a" * 32, actor)
        request.return_value = {"ok": True, "zone": area, "zone_epoch": epoch, gameplay_snapshot.REVISION: "d" * 32,
                                "gameplay": {door.KEY: older, "host_world": {"clock": 9}}}
        await client.refresh_gameplay(project=False)
        self.assertEqual(client.gameplay[door.KEY], confirmed)
        self.assertEqual(client.gameplay["host_world"], {"clock": 9})
        request.return_value = {**request.return_value, gameplay_snapshot.BASE: "d" * 32, gameplay_snapshot.REVISION: "e" * 32,
                                gameplay_snapshot.DELTA: {"changed": {door.KEY: {**older, "sequence": 11}}, "removed": []}}
        del request.return_value["gameplay"]
        client.next_snapshot = 0
        await client.refresh_gameplay(project=False)
        self.assertEqual(client.gameplay[door.KEY], {**older, "sequence": 11})

    async def test_bridge_idle_wait_wakes_for_a_host_request_without_local_input(self):
        with TemporaryDirectory() as temporary:
            bridge = runtime.Bridge(Path(temporary), Mock())
            host = service.Host.__new__(service.Host)
            host.native_requests = asyncio.Queue()
            host.native_request_ready = asyncio.Event()
            wait = asyncio.create_task(bridge.wait_for_interaction(5, wake_event=host.native_request_ready))
            self.addAsyncCleanup(self.cancel_wait, wait)
            await asyncio.sleep(0)
            first = asyncio.create_task(host.enqueue_native_request("guest", {"action": "first"}))
            second = asyncio.create_task(host.enqueue_native_request("guest", {"action": "second"}))
            for task in first, second:
                self.addAsyncCleanup(self.cancel_wait, task)
            await asyncio.wait_for(wait, .5)
            self.assertEqual(host.native_requests.qsize(), 2)
            # A disconnected guest cancels its request before the game can dispatch it.
            await self.cancel_wait(first)
            await self.cancel_wait(second)
            await host.service_guest_native_requests(bridge)
            self.assertTrue(host.native_request_ready.is_set())
            await host.service_guest_native_requests(bridge)
            self.assertFalse(host.native_request_ready.is_set())

    async def test_host_batches_ready_guests_in_fifo_without_overlapping_claims(self):
        host = service.Host.__new__(service.Host)
        host.native_requests = asyncio.Queue()
        host.native_request_ready = asyncio.Event()
        host.stopping = host.native_checkpoint_unsafe = host.transitioning = False
        host.party = {"wipe": None}
        host.zone_epoch = "visit"
        host.last_game = {"map": OBSERVATORY, "state": "hosting"}
        host.authenticated_guest = lambda guest: ("guest", f"character-{guest}")
        host.guest_leave_pending = Mock(return_value=False)
        host.pickup_audio = Mock()
        host.pickup_audio.frame.return_value = None
        bridge = Mock()
        order = []

        async def claim(owner, game, character, target, operation):
            self.assertIs(owner, host)
            self.assertIs(game, bridge)
            self.assertEqual(target, "same-world-item")
            self.assertEqual(operation, character.removeprefix("character-"))
            order.append(f"begin-{operation}")
            await asyncio.sleep(0)
            order.append(f"finish-{operation}")
            if len(order) != 2:
                raise service.party.RuleError("already_claimed")
            return {"claimed_by": character}

        def enqueue(guest):
            return asyncio.create_task(host.enqueue_native_request(guest, {
                "action": service.native_loose_pickup.CLAIM, "target": "same-world-item", "operation_id": guest,
            }))

        with patch.object(service.native_loose_pickup, "claim", side_effect=claim), patch.object(
                service, "time", SimpleNamespace(monotonic=lambda: 100.0)), patch.object(service, "_TIMED_INTERACTIONS", frozenset()):
            tasks = [enqueue(str(index)) for index in range(4)]
            for task in tasks:
                self.addAsyncCleanup(self.cancel_wait, task)
            await asyncio.sleep(0)
            await host.service_guest_native_batch(bridge)
            self.assertEqual(order, ["begin-0", "finish-0", "begin-1", "finish-1", "begin-2", "finish-2"])
            self.assertTrue(host.native_request_ready.is_set())
            self.assertEqual(host.native_requests.qsize(), 1)
            await host.service_guest_native_batch(bridge)
            self.assertFalse(host.native_request_ready.is_set())
            self.assertEqual(order[-2:], ["begin-3", "finish-3"])
            self.assertEqual(await asyncio.gather(*tasks), [
                {"ok": True, "claimed_by": "character-0"}, *([{"ok": False, "error": "already_claimed"}] * 3),
            ])

    async def test_host_batch_yields_after_slow_action_or_scope_change(self):
        for boundary in ("time", "visit", "unsafe", "wipe"):
            with self.subTest(boundary=boundary):
                clock = Mock(return_value=100)
                host = service.Host.__new__(service.Host)
                host.native_requests = Mock()
                host.native_requests.empty.return_value = False
                host.last_game = {"map": OBSERVATORY, "state": "hosting"}
                host.zone_epoch = "visit"
                host.party = {"wipe": None}
                host.stopping = host.native_checkpoint_unsafe = host.transitioning = False

                async def dispatch(_bridge):
                    if boundary == "time":
                        clock.return_value = 100.030
                    elif boundary == "visit":
                        host.zone_epoch = "new-visit"
                    elif boundary == "unsafe":
                        host.native_checkpoint_unsafe = True
                    else:
                        host.party["wipe"] = {"id": "active-wipe"}

                bridge = Mock()
                host.service_guest_native_requests = AsyncMock(side_effect=dispatch)
                with patch.object(service, "time", SimpleNamespace(monotonic=clock)):
                    await host.service_guest_native_batch(bridge)
                host.service_guest_native_requests.assert_awaited_once_with(bridge)

    async def test_guest_idle_wait_wakes_for_supported_gameplay_events(self):
        streams = (
            ("native-guest-primary-events", "primary_input_offset"),
            ("native-guest-held-fire-events", "held_fire_offset"),
            ("native-inventory-view-events", "inventory_view_offset"),
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

    async def test_world_change_requests_one_early_snapshot_with_current_delta_base(self):
        epoch = "a" * 32
        transport = Mock(spec=service.link.ClientLink)
        transport.address = ("127.0.0.1", 7777)
        transport.session_end = None
        release = asyncio.Event()

        async def snapshot(payload, *, timeout):
            await release.wait()
            return {"ok": True, "gameplay": {"players": {}}, "zone_epoch": epoch, "zone": OBSERVATORY}

        transport.request = AsyncMock(side_effect=snapshot)
        transport.gameplay_changed.side_effect = lambda visit: visit == epoch
        transport.consume_gameplay_change.side_effect = lambda: setattr(transport.gameplay_changed, "side_effect", lambda _: False)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        bridge = runtime.Bridge(Path(temporary.name), Mock())
        client = service.Client(Mock(), transport, {"character_id": "guest"}, bridge)
        self.addAsyncCleanup(client.cancel_gameplay_snapshot)
        client.zone_epoch, client.expected_zone = epoch, OBSERVATORY
        client.last_game = {"state": "connected", "map": OBSERVATORY}
        client.gameplay_snapshots.base = "b" * 32
        client.last_snapshot_started, client.next_snapshot = 100.0, 100.5
        clock = Mock(return_value=100.04)

        with patch.object(service, "time", SimpleNamespace(monotonic=clock)):
            await client.refresh_gameplay(project=False, wait=False)
            self.assertIsNone(client.snapshot_request)
            client.wake_for_gameplay_change()
            self.assertFalse(bridge._guest_update_wakeup.is_set())
            clock.return_value = 100.15
            client.zone_epoch = "c" * 32
            client.wake_for_gameplay_change()
            self.assertFalse(bridge._guest_update_wakeup.is_set())
            client.zone_epoch = epoch
            self.assertTrue(client.gameplay_change_ready())
            waiter = asyncio.create_task(bridge.wait_for_interaction(5, guest=True))
            await asyncio.sleep(0)
            client.wake_for_gameplay_change()
            await asyncio.wait_for(waiter, .2)
            await client.refresh_gameplay(project=False, wait=False)
            await client.refresh_gameplay(project=False, wait=False)
            await asyncio.sleep(0)
            transport.request.assert_awaited_once_with({"action": "snapshot", gameplay_snapshot.BASE: "b" * 32}, timeout=2)
            transport.consume_gameplay_change.assert_called_once_with()
            waiter = asyncio.create_task(bridge.wait_for_interaction(5, guest=True))
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(waiter, .2)
            await client.refresh_gameplay(project=False)
            self.assertFalse(client.gameplay_change_ready())
            self.assertEqual(client.next_snapshot, 100.65)

    def test_host_coalesces_interaction_and_loot_motion_changes_in_their_visit(self):
        host = service.Host.__new__(service.Host)
        host.stopping = host.transitioning = host.native_checkpoint_unsafe = False
        host.gameplay_change_scope = host.gameplay_change_state = None
        host.game_link_connected = Mock(return_value=True)
        host.zone_epoch = "a" * 32
        host.stock_doors = native_stock_door.Interactions()
        host.world_loot = service.native_world_loot.Projection()
        host.guest_wallet_receipts = {}
        doors = native_stock_door.KEY
        switches = service.native_stock_switch.KEY
        host.last_game = {"state": "hosting", "map": OBSERVATORY, "sequence": 4,
                          doors: {"door": {"open": False, "locked": False, "broken": False}},
                          switches: {"switch": {"powered": False, "lights": {}}}}
        host.party = {}
        transport = Mock(spec=service.link.HostLink)
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_not_called()
        host.stock_doors.confirmed_scope = OBSERVATORY, host.zone_epoch
        host.stock_doors.confirmed["door"] = 5, host.last_game[doors]["door"] | {"open": True}
        host.last_game = {**host.last_game, switches: {"switch": {"powered": True, "lights": {}}}}
        host.party[native_loot.STATE_KEY] = {"crate": {"id": "roll"}}
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_called_once_with("a" * 32)
        host.publish_gameplay_change(transport)
        host.world_loot.note_player_drop((OBSERVATORY, host.zone_epoch, "host pawn"), "d" * 32)
        host.publish_gameplay_change(transport)
        self.assertEqual(transport.send_gameplay_change.call_count, 2)
        host.publish_gameplay_change(transport)
        host.zone_epoch = "b" * 32
        host.publish_gameplay_change(transport)
        self.assertEqual(transport.send_gameplay_change.call_count, 2)

    def test_host_notifies_for_loose_pickups_and_quest_visibility_edges(self):
        host = service.Host.__new__(service.Host)
        host.stopping = host.transitioning = host.native_checkpoint_unsafe = False
        host.gameplay_change_scope = host.gameplay_change_state = None
        host.game_link_connected = Mock(return_value=True)
        host.zone_epoch = "a" * 32
        host.stock_doors = native_stock_door.Interactions()
        host.world_loot = service.native_world_loot.Projection()
        host.guest_wallet_receipts = {}
        host.party = {}
        loose = service.native_loose_pickup
        stock = {"state": "hosting", "map": OBSERVATORY}
        host.last_game = {**stock, loose.QUEST_SOURCES: {}}
        transport = Mock(spec=service.link.HostLink)

        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_not_called()
        host.party[loose.STATE] = {"bandage": {"mode": 1}}
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_called_once_with(host.zone_epoch)
        transport.reset_mock()

        host.last_game = {**stock, loose.QUEST_SOURCES: {"case": {"transform": [10, 10, 10]}}}
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_called_once_with(host.zone_epoch)
        transport.reset_mock()

        host.last_game = {**stock, loose.QUEST_SOURCES: {"case": {"transform": [20, 20, 20]}}}
        host.publish_gameplay_change(transport)
        host.last_game = stock
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_not_called()

        host.last_game = {**stock, loose.QUEST_SOURCES: {}}
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_called_once_with(host.zone_epoch)
        transport.reset_mock()
        host.last_game = {**stock, loose.RESPAWN_HIDDEN: ["earlier stock bandage"]}
        host.publish_gameplay_change(transport)
        transport.send_gameplay_change.assert_called_once_with(host.zone_epoch)
