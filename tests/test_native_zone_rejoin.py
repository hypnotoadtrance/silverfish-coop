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
            gameplay_seen=1, last_game=self.joining,
            transport=SimpleNamespace(game_access=True, game_packets_sent=0, game_packets_received=0,
                                      game_local_datagrams=0),
            bridge=SimpleNamespace(snapshot=Mock(return_value=self.joining),
                                   process=SimpleNamespace(poll=lambda: None)),
            control=SimpleNamespace(event=Mock()),
            refresh_gameplay=AsyncMock(side_effect=self.refresh),
            leave_for_rejoin=AsyncMock(side_effect=self.leave),
            open_game_link=AsyncMock(),
        )
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


class ZoneRejoinIntegrationTests(IsolatedAsyncioTestCase):
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
        transport = SimpleNamespace(address=("127.0.0.1", 31000), game_access=True,
                                    reset_game_endpoint=Mock(), request=AsyncMock(return_value=reply))
        client = Client(SimpleNamespace(event=Mock()), transport, {"character_id": character, "native_zones": True},
                        bridge, allowed_maps=frozenset({CITY, BOROUGH}))
        client.host_session, client.zone_epoch, client.expected_zone = session, visit, CITY
        expected = ("join", "127.0.0.1:31000", session, visit, CITY)

        await client.open_game_link()
        bridge.command.assert_awaited_once_with(*expected, "-123", "113312523")
        transport.request.assert_awaited_once_with({"action": "snapshot", "zone_epoch": visit, WORLD_START: True},
                                                   timeout=1)

        invalid = (old_snapshot, {"ok": False, "error": "unknown_action"},
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
        await client.open_game_link()
        transport.request.assert_not_awaited()
        bridge.command.assert_awaited_once_with(*expected)

    def test_host_omits_world_start_from_a_stale_scene_for_the_same_authenticated_visit(self):
        session, visit = "a" * 32, "b" * 32
        game = {"state": "hosting", "map": CITY, "server_connection": False,
                "native_scene": {"active_map": CITY, "instance_seed": -123.0, "game_seconds": 113312.523}}
        host = SimpleNamespace(stopping=False, transitioning=False, native_checkpoint_unsafe=False, native_guest=False,
                               native_zones=True, zone_epoch=visit, allowed_maps={CITY}, last_game=game,
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
                                 process=SimpleNamespace(poll=lambda: None))
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
