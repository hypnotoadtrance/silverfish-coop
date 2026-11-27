import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from tools.silverfish.link import ClientLink
from tools.silverfish.native_guest_clock import WORLD_START
from tools.silverfish.native_zone_rejoin import EmptyZoneRetry
from tools.silverfish.runtime import MENU
from tools.silverfish.service import Client, Host


CITY = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"


class EmptyZoneRetryTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.retry = EmptyZoneRetry()
        self.joining = {"map": MENU, "state": "joining", "mode": "client", "server_connection": False,
                        "remote_clients": 0, "ack": {"result": "accepted", "message": "client travel requested"}}
        self.ready = dict(self.joining, state="ready", mode="idle")
        self.client = SimpleNamespace(
            host_session="session", zone_epoch="city-epoch", expected_zone=CITY, needs_travel=True,
            stopping=False, suspended=False, server_transitioning=False, native_return_reservation=None,
            gameplay_seen=1, last_game=self.joining, session_stop_reason=None,
            transport=SimpleNamespace(game_access=True, game_packets_sent=0, game_packets_received=0,
                                      game_local_datagrams=0, session_end=None),
            bridge=SimpleNamespace(snapshot=Mock(return_value=self.joining),
                                   process=SimpleNamespace(poll=lambda: None)),
            control=SimpleNamespace(event=Mock()),
            refresh_gameplay=AsyncMock(side_effect=self.refresh),
            leave_for_rejoin=AsyncMock(side_effect=self.leave),
            open_game_link=AsyncMock(),
        )
        self.client.check_host_end = Client.check_host_end.__get__(self.client)
        self.retry.note_transition("session", "city-epoch", CITY)

    def refresh(self):
        self.client.gameplay_seen += 1

    def leave(self, frame):
        self.assertEqual(frame, self.joining)
        self.client.last_game = self.ready

    async def test_confirmed_empty_join_rotates_local_endpoint_only_once(self):
        client = self.client
        client.transport.game_local_datagrams = 1
        self.assertTrue(await self.retry.after_timeout(client, "city-epoch"))
        self.retry.note_transition("session", "city-epoch", CITY)
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.refresh_gameplay.assert_awaited_once_with()
        client.leave_for_rejoin.assert_awaited_once_with(self.joining)
        client.open_game_link.assert_awaited_once_with(renew_endpoint=True)
        client.control.event.assert_called_once_with(
            "native_zone_empty_join_retry", zone_epoch="city-epoch", map=CITY,
            local_datagrams=1, sent=0, received=0,
        )

    async def test_same_visit_title_ejection_retries_one_empty_join_on_a_fresh_endpoint(self):
        client = self.client
        scope = "c" * 32
        ejected = dict(self.ready, state="disconnected", mode="client", save_scope=scope)
        self.joining["save_scope"], self.ready["save_scope"] = scope, scope
        client.invite = {"character_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "native_zones": True}
        client.game_visit = (client.host_session, client.zone_epoch, CITY)
        client.allowed_maps = {CITY}
        client.needs_travel = False
        client.empty_zone_retry = self.retry
        client.bridge.local_bridge = object()
        client.bridge.snapshot.return_value = ejected
        self.retry.clear()

        async def leave(frame):
            self.assertIn(frame, (ejected, self.joining))
            client.last_game = self.ready

        async def join(*, timeout=18, renew_endpoint):
            if renew_endpoint:
                client.last_game = {"map": CITY, "state": "connected"}
                client.needs_travel = False
                return
            client.needs_travel = True
            client.bridge.snapshot.return_value = self.joining
            raise TimeoutError

        client.leave_for_rejoin.side_effect = leave
        client.open_game_link.side_effect = join
        await Client.reconcile_game(client)

        self.assertEqual(client.last_game["map"], CITY)
        self.assertFalse(client.needs_travel)
        self.assertEqual(client.open_game_link.await_count, 2)
        client.open_game_link.assert_awaited_with(renew_endpoint=True)
        client.control.event.assert_called_once_with(
            "native_guest_title_empty_join_retry", zone_epoch="city-epoch", map=CITY,
            local_datagrams=0, sent=0, received=0,
        )
        self.assertFalse(await self.retry.after_timeout(client, client.zone_epoch))

    async def test_same_visit_recovery_refuses_a_changed_private_title_scope(self):
        client = self.client
        scope = "c" * 32
        client.invite = {"character_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "native_zones": True}
        client.game_visit = (client.host_session, client.zone_epoch, CITY)
        client.allowed_maps = {CITY}
        client.needs_travel = False
        self.retry.clear()

        ejected = dict(self.ready, state="disconnected", mode="client", save_scope="d" * 32)
        self.retry.note_title_ejection(client, ejected)
        client.needs_travel = True
        self.joining["save_scope"] = scope
        self.assertFalse(await self.retry.after_timeout(client, client.zone_epoch))

        client.needs_travel = False
        self.retry.note_title_ejection(client, dict(ejected, save_scope=scope))
        client.needs_travel = True
        self.joining["save_scope"] = "d" * 32
        self.assertFalse(await self.retry.after_timeout(client, client.zone_epoch))
        client.refresh_gameplay.assert_not_awaited()
        client.open_game_link.assert_not_awaited()

    async def test_resident_reconcile_yields_to_a_released_or_replaced_save_scope(self):
        client = self.client
        client.invite = {"character_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "native_zones": True}
        client.game_visit = (client.host_session, client.zone_epoch, CITY)
        client.allowed_maps = {CITY}
        client.needs_travel = False
        client.empty_zone_retry = self.retry
        client.bridge.local_bridge = object()
        client.leave_for_rejoin.side_effect = None

        for state in (
            dict(self.ready, state="disconnected", mode="client", save_scope="d" * 32),
            dict(self.ready, save_scope=None),
            dict(self.ready, map=BOROUGH, save_scope=None),
            self.ready,
        ):
            with self.subTest(state=state):
                client.bridge.snapshot.return_value = state
                await Client.reconcile_game(client)
                self.assertIs(client.last_game, state)

        client.leave_for_rejoin.assert_not_awaited()
        client.open_game_link.assert_not_awaited()

    async def test_timeout_without_exact_empty_title_does_not_rotate(self):
        client = self.client
        self.assertFalse(await self.retry.after_timeout(client, "initial-or-old-epoch"))
        client.native_return_reservation = 3
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.native_return_reservation = None
        client.transport.game_packets_sent = 1
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.transport.game_packets_sent = 0
        for fields in ({"map": CITY}, {"server_connection": True}, {"ack": {}}, {"state": "ready"}):
            with self.subTest(fields=fields):
                client.bridge.snapshot.return_value = dict(self.joining, **fields)
                self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.refresh_gameplay.assert_not_awaited()
        client.leave_for_rejoin.assert_not_awaited()
        client.open_game_link.assert_not_awaited()

    async def test_retry_requires_fresh_authenticated_same_epoch(self):
        client = self.client
        client.refresh_gameplay.side_effect = lambda: None
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))

        def advance_destination():
            self.refresh()
            client.zone_epoch, client.expected_zone = "borough-epoch", BOROUGH
            self.retry.note_transition("session", "borough-epoch", BOROUGH)

        client.refresh_gameplay.side_effect = advance_destination
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.leave_for_rejoin.assert_not_awaited()
        client.open_game_link.assert_not_awaited()
        client.refresh_gameplay.side_effect = self.refresh
        self.assertTrue(await self.retry.after_timeout(client, "borough-epoch"))

    async def test_late_game_packets_during_title_reset_prevent_rotation(self):
        client = self.client

        def leave_with_late_packet(frame):
            self.leave(frame)
            client.transport.game_packets_received = 1

        client.leave_for_rejoin.side_effect = leave_with_late_packet
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.open_game_link.assert_not_awaited()

    async def test_authenticated_end_during_title_reset_prevents_endpoint_rotation(self):
        client = self.client

        def leave_after_end(frame):
            self.leave(frame)
            client.transport.session_end = (client.host_session, 17)

        client.leave_for_rejoin.side_effect = leave_after_end
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        self.assertTrue(client.stopping)
        client.open_game_link.assert_not_awaited()
        client.control.event.assert_called_once_with("host_session_ended", revision=17)

    async def test_a_failed_rotation_does_not_repeat(self):
        client = self.client
        client.open_game_link.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            await self.retry.after_timeout(client, "city-epoch")
        self.assertFalse(await self.retry.after_timeout(client, "city-epoch"))
        client.open_game_link.assert_awaited_once()

    def test_pre_filter_local_telemetry_resets_with_attempt_counts(self):
        transport = ClientLink()
        transport._from_game(b"fixture packet before admission", ("127.0.0.1", 32123))
        self.assertEqual((transport.game_local_datagrams, transport.game_packets_sent, transport.game_packets_received),
                         (1, 0, 0))
        transport.reset_game_endpoint()
        self.assertEqual((transport.game_local_datagrams, transport.game_packets_sent, transport.game_packets_received),
                         (0, 0, 0))


class ReservedReturnTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.session, self.epoch, self.scope = "a" * 32, "b" * 32, "c" * 32
        self.character = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        self.title = dict(map=MENU, state="ready", mode="idle", save_scope=self.scope)
        self.frame = self.title
        self.bridge = SimpleNamespace(local_bridge=object(), snapshot=lambda: self.frame,
                                      process=SimpleNamespace(poll=lambda: None))
        self.transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True, session_end=None,
                                         request=AsyncMock(return_value={"ok": True}))
        self.client = Client(SimpleNamespace(event=Mock()), self.transport,
                             {"character_id": self.character, "native_zones": True}, self.bridge,
                             allowed_maps=frozenset({CITY, BOROUGH}))
        self.client.game_visit = (self.session, "e" * 32, BOROUGH)
        self.client.host_session, self.client.zone_epoch, self.client.expected_zone = self.session, self.epoch, CITY
        self.client.needs_travel = True
        self.client.native_return_reservation = 7
        self.client.native_return_retry_allowed = True
        self.client.open_game_link = AsyncMock(side_effect=[TimeoutError, None])
        self.client.leave_for_rejoin = AsyncMock()
        self.client.wait_game_link = AsyncMock()
        self.client.refresh_gameplay = AsyncMock()

    async def test_reserved_timeout_does_not_leave_or_rearm_a_released_title_scope(self):
        def release_scope():
            self.frame = dict(self.title, save_scope=None)

        self.client.refresh_gameplay.side_effect = release_scope
        await self.client.reconcile_game()

        self.client.leave_for_rejoin.assert_awaited_once_with(self.title, CITY)
        self.transport.request.assert_not_awaited()
        self.client.open_game_link.assert_awaited_once_with(timeout=10, renew_endpoint=True)
        self.assertTrue(self.client.needs_travel)

    async def test_authentic_host_end_during_stock_leave_prevents_initial_rejoin(self):
        async def leave_after_end(*args):
            self.transport.session_end = (self.session, 17)

        self.client.leave_for_rejoin.side_effect = leave_after_end
        await self.client.reconcile_game()

        self.client.leave_for_rejoin.assert_awaited_once_with(self.title, CITY)
        self.client.open_game_link.assert_not_awaited()
        self.transport.request.assert_not_awaited()
        self.assertTrue(self.client.stopping)
        self.assertEqual(self.client.session_stop_reason, "host_session_ended")

    async def test_reserved_timeout_does_not_accept_a_different_guest_on_the_same_map(self):
        def replace_scope():
            self.frame = dict(map=CITY, state="connected", mode="client", save_scope="d" * 32)

        self.client.refresh_gameplay.side_effect = replace_scope
        await self.client.reconcile_game()

        self.assertTrue(self.client.needs_travel)
        self.assertNotEqual(self.client.last_game, self.frame)
        self.client.wait_game_link.assert_not_awaited()
        self.transport.request.assert_not_awaited()

    async def test_reserved_timeout_records_the_exact_guest_who_connected_as_it_expired(self):
        def connect():
            self.frame = dict(map=CITY, state="connected", mode="client", save_scope=self.scope)

        self.client.refresh_gameplay.side_effect = connect
        self.client.loading_zone = CITY
        self.client.source_fade = (self.client.game_visit, CITY, time.monotonic())
        await self.client.reconcile_game()

        self.assertFalse(self.client.needs_travel)
        self.assertEqual(self.client.last_game, self.frame)
        self.assertEqual(self.client.game_visit, (self.session, self.epoch, CITY))
        self.assertIsNone(self.client.loading_zone)
        self.assertIsNone(self.client.source_fade)
        self.transport.request.assert_not_awaited()

    async def test_reserved_timeout_rechecks_visit_after_stock_leave_and_host_retry_reply(self):
        async def leave(frame, cover=None):
            if cover is None:
                self.frame = dict(self.title, save_scope=None)

        self.client.leave_for_rejoin.side_effect = leave
        await self.client.reconcile_game()
        self.transport.request.assert_not_awaited()
        self.client.open_game_link.assert_awaited_once_with(timeout=10, renew_endpoint=True)

        self.frame = self.title
        self.client.leave_for_rejoin.side_effect = None
        self.client.open_game_link.reset_mock(side_effect=True)
        self.client.open_game_link.side_effect = [TimeoutError, None]

        def replace_reservation(*args, **kwargs):
            self.client.native_return_reservation = 8
            return {"ok": True}

        self.transport.request.side_effect = replace_reservation
        await self.client.reconcile_game()

        self.transport.request.assert_awaited_once_with({"action": "retry-native-return", "zone_epoch": self.epoch,
                                                        "reservation": 7}, timeout=6)
        self.client.open_game_link.assert_awaited_once_with(timeout=10, renew_endpoint=True)
        self.assertTrue(self.client.native_return_retry_allowed)

    async def test_reserved_timeout_rearms_a_qualified_reservation_once(self):
        await self.client.reconcile_game()

        self.transport.request.assert_awaited_once_with({"action": "retry-native-return", "zone_epoch": self.epoch,
                                                        "reservation": 7}, timeout=6)
        self.assertEqual([call.kwargs for call in self.client.open_game_link.await_args_list],
                         [{"timeout": 10, "renew_endpoint": True}, {"timeout": 12, "renew_endpoint": True}])
        self.assertFalse(self.client.native_return_retry_allowed)


class ZoneRejoinIntegrationTests(IsolatedAsyncioTestCase):
    async def test_host_end_during_world_start_request_prevents_unreal_join(self):
        session, epoch, scope = "a" * 32, "b" * 32, "c" * 32
        title = {"state": "ready", "map": MENU, "save_scope": scope}
        bridge = SimpleNamespace(local_bridge=object(), snapshot=lambda: title, command=AsyncMock(),
                                 wait_for=AsyncMock(return_value={"state": "connected", "map": CITY}))
        transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True, session_end=None,
                                    reset_game_endpoint=Mock(), request=AsyncMock())
        client = Client(SimpleNamespace(event=Mock()), transport,
                        {"character_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "native_zones": True},
                        bridge, allowed_maps=frozenset({CITY}))
        client.host_session, client.zone_epoch, client.expected_zone = client.game_visit = (session, epoch, CITY)

        def end_after_snapshot(*args, **kwargs):
            transport.session_end = (session, 17)
            return {"ok": False}

        transport.request.side_effect = end_after_snapshot
        await client.open_game_link()

        transport.request.assert_awaited_once()
        self.assertTrue(client.stopping)
        bridge.command.assert_not_awaited()
        bridge.wait_for.assert_not_awaited()

    async def test_host_end_stops_waiting_for_the_world_while_native_join_is_pending(self):
        session, epoch, scope = "a" * 32, "b" * 32, "c" * 32
        joining = {"state": "joining", "map": MENU, "save_scope": scope}
        bridge = SimpleNamespace(local_bridge=object(), snapshot=lambda: joining, wait_for=AsyncMock())
        transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True, session_end=None)
        client = Client(SimpleNamespace(event=Mock()), transport,
                        {"character_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "native_zones": True}, bridge)
        client.host_session, client.zone_epoch, client.expected_zone = client.game_visit = (session, epoch, CITY)
        client.last_game = joining
        client.needs_travel = True

        def observe_joining(condition, *, timeout):
            transport.session_end = ("d" * 32, 17)
            self.assertFalse(condition(joining))
            transport.session_end = (session, 17)
            self.assertTrue(condition(joining))
            return joining

        bridge.wait_for.side_effect = observe_joining
        await client.wait_game_link(18)

        self.assertTrue(client.stopping)
        self.assertTrue(client.needs_travel)
        self.assertIs(client.last_game, joining)
        client.control.event.assert_called_once_with("host_session_ended", revision=17)

    async def test_resident_rejoin_rechecks_save_scope_after_requesting_world_start(self):
        session, visit, scope = "a" * 32, "b" * 32, "c" * 32
        character = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        game = {"state": "ready", "map": MENU, "save_scope": scope}
        bridge = SimpleNamespace(local_bridge=object(), snapshot=lambda: dict(game),
                                 command=AsyncMock(return_value={"state": "joining"}),
                                 wait_for=AsyncMock(return_value={"state": "connected", "map": CITY}))

        async def change_scope(*args, **kwargs):
            game["save_scope"] = None
            return {"ok": False}

        transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True, session_end=None,
                                    reset_game_endpoint=Mock(), request=AsyncMock(side_effect=change_scope))
        client = Client(SimpleNamespace(event=Mock()), transport, {"character_id": character, "native_zones": True},
                        bridge, allowed_maps=frozenset({CITY}))
        client.host_session, client.zone_epoch, client.expected_zone = client.game_visit = (session, visit, CITY)

        with self.assertRaisesRegex(RuntimeError, "private guest save scope changed"):
            await client.open_game_link()
        transport.request.assert_awaited_once()
        bridge.command.assert_not_awaited()
        bridge.wait_for.assert_not_awaited()

    async def test_embedded_join_uses_only_a_matching_authenticated_destination_time(self):
        session, visit, scope = "a" * 32, "b" * 32, "c" * 32
        character = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        game = {"state": "ready", "map": MENU, "save_scope": scope}
        bridge = SimpleNamespace(local_bridge=object(), snapshot=lambda: game,
                                 command=AsyncMock(return_value={"state": "joining"}),
                                 wait_for=AsyncMock(return_value={"state": "connected", "map": CITY}))
        startup = {"session": session, "epoch": visit, "map": CITY, "seed": -123, "milliseconds": 113312523}
        old_snapshot = {"ok": True, "travel_in_progress": False, "zone_epoch": visit, "zone": CITY,
                        "gameplay": {"host_world": {"native_scene": {
                            "active_map": CITY, "instance_seed": -123.0, "game_seconds": 113312.523,
                        }}}}
        reply = {**old_snapshot, WORLD_START: startup}
        transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True, session_end=None,
                                    reset_game_endpoint=Mock(), request=AsyncMock(return_value=reply))
        client = Client(SimpleNamespace(event=Mock()), transport, {"character_id": character, "native_zones": True},
                        bridge, allowed_maps=frozenset({CITY, BOROUGH}))
        client.host_session, client.zone_epoch, client.expected_zone = session, visit, CITY
        expected = ("join", "127.0.0.1:31000", session, visit, CITY)

        await client.open_game_link()
        bridge.command.assert_awaited_once_with(*expected, "-123", "113312523")
        transport.request.assert_awaited_once_with({"action": "snapshot", "zone_epoch": visit, WORLD_START: True},
                                                   timeout=1)

        for seed, argument in ((217526, "217526"), (None, "-"), (-2147483648, "-2147483648")):
            bridge.command.reset_mock()
            transport.request.return_value = {**reply, WORLD_START: {**startup, "static_world_seed": seed}}
            await client.open_game_link()
            bridge.command.assert_awaited_once_with(*expected, "-123", "113312523", argument)

        invalid = (old_snapshot, {**reply, WORLD_START: {**startup, "static_world_seed": True}},
                   {**reply, WORLD_START: {**startup, "static_world_seed": 2147483648}}, {"ok": False, "error": "unknown_action"},
                   {**reply, "zone_epoch": "d" * 32}, {**reply, "zone": BOROUGH}, {**reply, "travel_in_progress": True},
                   {**reply, WORLD_START: {**startup, "session": "d" * 32}},
                   {**reply, WORLD_START: {**startup, "epoch": "d" * 32}},
                   {**reply, WORLD_START: {**startup, "map": BOROUGH}})
        for response in invalid:
            with self.subTest(response=response):
                bridge.command.reset_mock()
                transport.request.return_value = response
                await client.open_game_link()
                bridge.command.assert_awaited_once_with(*expected)

        bridge.command.reset_mock()
        transport.request.reset_mock()
        game["save_scope"] = "e" * 32
        with self.assertRaisesRegex(RuntimeError, "private guest save scope changed"):
            await client.open_game_link()
        transport.request.assert_not_awaited()
        bridge.command.assert_not_awaited()

    def test_host_omits_world_start_from_a_stale_scene_for_the_same_authenticated_visit(self):
        session, visit = "a" * 32, "b" * 32
        game = {"state": "hosting", "map": CITY, "server_connection": False,
                "native_scene": {"active_map": CITY, "instance_seed": -123.0, "game_seconds": 113312.523}}
        host = SimpleNamespace(stopping=False, transitioning=False, native_checkpoint_unsafe=False, native_guest=False,
                               native_zones=True, zone_epoch=visit, allowed_maps={CITY}, last_game=game, map_preflight=None,
                               last_seen=time.monotonic() - 10, control=SimpleNamespace(generation=session),
                               authenticated_guest=Mock(return_value=("guest", "guest-character")),
                               snapshot_for_guest=lambda *_args: {"ok": True, "travel_in_progress": False,
                                                                   "zone": CITY, "zone_epoch": visit})
        host.guest_world_start_frame = Host.guest_world_start_frame.__get__(host)
        query = {"action": "snapshot", "zone_epoch": visit, WORLD_START: True}
        self.assertNotIn(WORLD_START, Host.guest_request(host, "authenticated-guest", query))
        host.last_seen = time.monotonic()
        self.assertEqual(Host.guest_request(host, "authenticated-guest", query)[WORLD_START],
                         {"session": session, "epoch": visit, "map": CITY, "seed": -123, "milliseconds": 113312523})
        self.assertNotIn(WORLD_START, Host.guest_request(host, "authenticated-guest", {"action": "snapshot"}))
        game["static_world_seed"] = {"map": CITY, "value": -56}
        self.assertEqual(Host.guest_request(host, "authenticated-guest", query)[WORLD_START]["static_world_seed"], -56)
        game["static_world_seed"]["value"] = None
        self.assertIsNone(Host.guest_request(host, "authenticated-guest", query)[WORLD_START]["static_world_seed"])
        game["static_world_seed"]["map"] = BOROUGH
        self.assertNotIn("static_world_seed", Host.guest_request(host, "authenticated-guest", query)[WORLD_START])

    async def test_client_zone_change_recovers_zero_packet_attempt_at_fresh_local_port(self):
        game = {"map": BOROUGH, "state": "connected", "mode": "client", "server_connection": True,
                "remote_clients": 0, "sequence": 1}
        commands = []
        address = "127.0.0.1"
        transport = SimpleNamespace(
            address=(address, 31000), connected=True, game_access=True,
            game_packets_sent=0, game_packets_received=0, game_local_datagrams=0,
            request=AsyncMock(return_value={"ok": True, "gameplay": {}, "zone_epoch": "city-epoch", "zone": CITY}),
            reset_game_endpoint=Mock(), session_end=None,
        )

        async def renew():
            transport.address = (address, 31001)

        transport.renew_game_endpoint = AsyncMock(side_effect=renew)

        async def command(*args):
            commands.append(args)
            action = args[0]
            acknowledged = game["sequence"] + 1
            if action == "leave":
                game.update(map=MENU, state="ready", mode="idle", server_connection=False, sequence=acknowledged + 1)
            else:
                joined = args[1] == f"{address}:31001"
                game.update(map=CITY if joined else MENU, state="connected" if joined else "joining", mode="client",
                            server_connection=joined, sequence=acknowledged + 1,
                            ack={"result": "accepted", "message": "client travel requested"})
            return dict(game, sequence=acknowledged)

        async def wait_for(predicate, *, timeout):
            if not predicate(game):
                raise TimeoutError
            return dict(game)

        control = SimpleNamespace(event=Mock())
        bridge = SimpleNamespace(snapshot=lambda: dict(game), command=command, wait_for=wait_for,
                                 process=SimpleNamespace(poll=lambda: None), local_bridge=None, wake_guest_update=Mock())
        client = Client(control, transport, {"character_id": "exact-guest"}, bridge, allowed_maps=frozenset({CITY, BOROUGH}))
        client.host_session, client.zone_epoch, client.expected_zone = "same-session", "borough-epoch", BOROUGH
        client.next_projection = float("inf")
        await client.refresh_gameplay()
        # A real join's 18-second timeout leaves the next control snapshot eligible.
        client.next_snapshot = 0
        await client.reconcile_game()
        self.assertEqual(commands, [("leave",), ("join", f"{address}:31000"),
                                    ("leave",), ("join", f"{address}:31001")])
        self.assertEqual((client.host_session, client.zone_epoch, client.last_game["map"], client.needs_travel),
                         ("same-session", "city-epoch", CITY, False))
        transport.renew_game_endpoint.assert_awaited_once_with()
        self.assertTrue(all(call.args == ({"action": "snapshot"},) for call in transport.request.await_args_list))
        names = [call.args[0] for call in control.event.call_args_list]
        self.assertEqual(names, ["zone_rejoin_requested", "native_game_join_timeout", "native_zone_empty_join_retry"])
