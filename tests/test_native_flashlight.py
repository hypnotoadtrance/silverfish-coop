from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tools.silverfish import native_flashlight as flashlight, native_player_avatar


class FlashlightViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        self.epoch = "a" * 32
        self.axes = ["1", "2", "3", "0", "-15", "0", "0.5", "0.6", "0.7"]

    def test_only_the_authoritative_host_source_is_published(self):
        source = flashlight.parse_source([flashlight.SOURCE_TAG, "257", "1", "2", "1", *self.axes], "hosting")
        game = {"state": "hosting", "map": self.area, "pawns": {"local": {"name": "host"}},
                native_player_avatar.KEY: {"257": {"pawn": "host"}}, flashlight.KEY: source}
        host = SimpleNamespace(last_game=game, zone_epoch=self.epoch, transitioning=False,
                               party={"players": {"host": {"downed": False}}})
        self.assertEqual(flashlight.frame(host), dict(source, map=self.area, epoch=self.epoch))
        game[native_player_avatar.KEY]["257"]["pawn"] = "other"
        self.assertIsNone(flashlight.frame(host))
        game[native_player_avatar.KEY]["257"]["pawn"] = "host"
        host.party["players"]["host"]["downed"] = True
        self.assertIsNone(flashlight.frame(host))
        with self.assertRaises(ValueError):
            flashlight.parse_source([flashlight.SOURCE_TAG, "257", "1", "2", "1", *self.axes], "connected")
        with self.assertRaises(ValueError):
            flashlight.parse_source([flashlight.SOURCE_TAG, "257", "1", "2", "1", *self.axes[:-1], "inf"], "hosting")

    async def test_power_changes_and_clear_go_to_the_bridge_once(self):
        game = {"state": "connected", "map": self.area}
        source = flashlight.parse_source([flashlight.SOURCE_TAG, "257", "9", "2", "1", *self.axes], "hosting")
        lit = flashlight.parse_view([flashlight.VIEW_TAG, self.epoch, "257", "9", "2", "1", "1", "2"], "connected")
        unlit = flashlight.parse_view([flashlight.VIEW_TAG, self.epoch, "257", "9", "3", "0", "0", "3"], "connected")
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[dict(game, **{flashlight.VIEW: lit}),
                                                                 dict(game, **{flashlight.VIEW: unlit}), game]))
        incoming = dict(source, map=self.area, epoch=self.epoch)
        result = await flashlight.project(bridge, game, incoming, self.epoch)
        self.assertEqual(result[flashlight.VIEW], lit)
        self.assertIs(await flashlight.project(bridge, result, incoming, self.epoch), result)
        self.assertEqual(bridge.command.await_count, 1)
        self.assertIs(await flashlight.project(bridge, result, dict(incoming, epoch="b" * 32), self.epoch), result)
        result = await flashlight.project(bridge, result, dict(incoming, power=False, switch=3), self.epoch)
        self.assertEqual(result[flashlight.VIEW], unlit)
        self.assertIs(await flashlight.project(bridge, result, None, self.epoch), game)
        self.assertIs(await flashlight.project(bridge, game, None, self.epoch), game)
        self.assertEqual(bridge.command.await_count, 3)
        bridge.command.assert_awaited_with(flashlight.VIEW_TAG, self.area, self.epoch, timeout=2)

    def test_guest_reports_are_bound_to_current_owned_flashlights(self):
        light_classes = {"258": {"pawn": "pawn-a", "profile": None}, "259": {"pawn": "pawn-b", "profile": flashlight.HEAVY}}
        game = {"state": "hosting", "map": self.area, flashlight.GUEST_AUTHORITY: light_classes}
        players = {"a": {"downed": False}, "b": {"downed": False}}
        members = {"a": {"pawn": "pawn-a", "connected": True}, "b": {"pawn": "pawn-b", "connected": True}}
        host = SimpleNamespace(last_game=game, zone_epoch=self.epoch, transitioning=False, last_seen=10,
                               party={"players": players}, native_recipients=lambda: members, guest_flashlights={})
        with patch.object(flashlight.time, "monotonic", return_value=10):
            flashlight.receive_guest(host, "a", "unbound", [5, 0, True])
            flashlight.receive_guest(host, "a", "pawn-a", [259, 5, 0, True])
            flashlight.receive_guest(host, "a", "pawn-a", [5, 0, True, flashlight.HEAVY])
            flashlight.receive_guest(host, "b", "pawn-b", [9, 0, False])
            self.assertIsNone(flashlight.peer_frame(host))
            flashlight.receive_guest(host, "a", "pawn-a", [5, 1, True])
            flashlight.receive_guest(host, "b", "pawn-b", [9, 0, False, flashlight.HEAVY])
            flashlight.receive_guest(host, "a", "pawn-a", [5, 0, False])
            flashlight.receive_guest(host, "a", "pawn-a", [5, 1, False])
            self.assertEqual(flashlight.peer_frame(host)["sources"], [[258, 5, 1, True]])
            self.assertEqual(flashlight.peer_frame(host)["heavy_sources"], [[259, 9, 0, False]])
            self.assertEqual(flashlight.peer_frame(host, exclude="a")["sources"], [])
            players["a"]["downed"] = True
            self.assertEqual(flashlight.peer_frame(host)["heavy_sources"], [[259, 9, 0, False]])
            light_classes["259"]["profile"] = None
            flashlight.receive_guest(host, "b", "pawn-b", [9, 1, True, flashlight.HEAVY])
            self.assertIsNone(flashlight.peer_frame(host))
            self.assertEqual(host.guest_flashlights, {})

    def test_source_is_exactly_local_and_temporary(self):
        pawn = "Player_FP_C /Game/Crossroads.Player_42"
        source = flashlight.parse_guest_source([flashlight.GUEST_SOURCE_TAG, pawn, "8", "1", "1"], "connected")
        game = {"pawns": {"local": {"name": pawn}}, flashlight.GUEST_SOURCE: source}
        self.assertEqual(flashlight.guest_update(game, self.epoch), (self.epoch, (8, 1, True)))
        game["pawns"]["local"]["name"] += "_replacement"
        self.assertIsNone(flashlight.guest_update(game, self.epoch))

        _, authority = flashlight.parse_guest_authority([flashlight.GUEST_AUTHORITY_TAG, "258", pawn], "hosting")
        host = SimpleNamespace(last_game={"state": "hosting", "map": self.area, flashlight.GUEST_AUTHORITY: {"258": authority}},
                               zone_epoch=self.epoch, transitioning=False, last_seen=10,
                               party={"players": {"guest": {"downed": False}}},
                               native_recipients=lambda: {"guest": {"pawn": pawn, "connected": True}},
                               guest_flashlights={})
        with patch.object(flashlight.time, "monotonic", return_value=10):
            flashlight.receive_guest(host, "guest", pawn, [8, 1, True])
            self.assertEqual(flashlight.peer_frame(host)["sources"], [[258, 8, 1, True]])
        host.last_seen = 11.6
        with patch.object(flashlight.time, "monotonic", return_value=11.6):
            self.assertIsNone(flashlight.peer_frame(host))
            self.assertEqual(host.guest_flashlights, {})

    async def test_guest_sources_share_a_scoped_batch_and_clear_without_repeat(self):
        game = {"state": "hosting", "map": self.area}
        incoming = {"map": self.area, "epoch": self.epoch, "sources": [[258, 3, 1, True]], "heavy_sources": [[259, 4, 2, False]]}
        one = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "258", "3", "1", "1", "1", "1"], "hosting")
        two = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "259", "4", "2", "0h", "0", "-"], "hosting")
        applied = dict(game, **{flashlight.GUEST_VIEW: {"258": one, "259": two}})
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[applied, game]))

        self.assertIs(await flashlight.project_peers(bridge, game, incoming, self.epoch), applied)
        bridge.command.assert_awaited_once_with(flashlight.GUEST_VIEW_TAG, self.area, self.epoch,
                                               "258", "3", "1", "1", "259", "4", "2", "0h", timeout=2)
        self.assertIs(await flashlight.project_peers(bridge, applied, incoming, self.epoch), applied)
        duplicate = dict(incoming, heavy_sources=[[258, 4, 2, False]])
        self.assertIs(await flashlight.project_peers(bridge, applied, duplicate, self.epoch), applied)
        self.assertIs(await flashlight.project_peers(bridge, applied, None, self.epoch), game)
        self.assertIs(await flashlight.project_peers(bridge, game, None, self.epoch), game)
        self.assertEqual(bridge.command.await_count, 2)

    async def test_heavy_host_profile_remains_explicit_through_the_receipt(self):
        source = flashlight.parse_source([flashlight.SOURCE_TAG, "257", "9", "2", "1h", *self.axes], "hosting")
        assert source["profile"] == flashlight.HEAVY
        lit = flashlight.parse_view([flashlight.VIEW_TAG, self.epoch, "257", "9", "2", "1h", "1", "2"], "connected")
        game = {"state": "connected", "map": self.area}
        applied = dict(game, **{flashlight.VIEW: lit})
        bridge = SimpleNamespace(command=AsyncMock(return_value=applied))
        incoming = dict(source, map=self.area, epoch=self.epoch)
        self.assertIs(await flashlight.project(bridge, game, incoming, self.epoch), applied)
        bridge.command.assert_awaited_once_with(flashlight.VIEW_TAG, self.area, self.epoch, "257", "9", "2", "1h",
                                               *(f"{float(x):.5f}" for x in self.axes), timeout=2)
        self.assertIs(await flashlight.project(bridge, applied, incoming, self.epoch), applied)
        self.assertIs(await flashlight.project(bridge, applied, dict(incoming, profile="other"), self.epoch), applied)

    async def test_offhand_direction_refreshes_once_without_changing_stock_switch(self):
        game = {"state": "connected", "map": self.area}
        source = flashlight.parse_source([flashlight.SOURCE_TAG, "257", "9", "0", "1o", *self.axes], "hosting")
        initial = dict(source, map=self.area, epoch=self.epoch)
        def rendered(direction):
            values = [flashlight.VIEW_TAG, self.epoch, "257", "9", "0", "1o", "1", "-", *direction]
            return dict(game, **{flashlight.VIEW: flashlight.parse_view(values, "connected")})

        changed = dict(initial, pose=[1, 2, 3, 12.5, 179.5, -10.1, .5, .6, .7])
        first, next_aim = rendered(self.axes[3:6]), rendered(["12.5", "179.5", "-10.1"])
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[first, next_aim, game]))
        self.assertIs(await flashlight.project(bridge, game, initial, self.epoch), first)
        self.assertIs(await flashlight.project(bridge, first, initial, self.epoch), first)
        self.assertIs(await flashlight.project(bridge, first, changed, self.epoch), next_aim)
        bridge.command.assert_awaited_with(flashlight.VIEW_TAG, self.area, self.epoch, "257", "9", "0", "1o",
                                           *(f"{value:.5f}" for value in changed["pose"]), timeout=2)
        self.assertIs(await flashlight.project(bridge, next_aim, None, self.epoch), game)
        with self.assertRaises(ValueError):
            flashlight.parse_guest_source([flashlight.GUEST_SOURCE_TAG, "Player_FP_C Guest", "9", "0", "1o"], "connected")
        with self.assertRaises(ValueError):
            flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "257", "9", "0", "1o", "1", "-"], "hosting")

    def test_guest_offhand_requires_matching_authoritative_loadout_and_keeps_aim_off_the_edge(self):
        pawn = "Player_FP_C /Game/Crossroads.Player_42"
        _, authority = flashlight.parse_guest_authority([flashlight.GUEST_AUTHORITY_TAG, "258", pawn], "hosting")
        local = {"pawns": {"local": {"name": pawn}}, flashlight.GUEST_SOURCE: flashlight.parse_guest_source(
            [flashlight.GUEST_SOURCE_TAG, pawn, "9", "0", "1o", "12.5", "20", "0"], "connected")}
        first = flashlight.guest_update(local, self.epoch)
        local[flashlight.GUEST_SOURCE]["sample"][4] = 22.5
        aimed = flashlight.guest_update(local, self.epoch)
        self.assertNotEqual(first, aimed)
        self.assertEqual(flashlight.guest_edge(first), flashlight.guest_edge(aimed))
        self.assertNotEqual(flashlight.guest_edge(aimed), flashlight.guest_edge(None))

        sources = {"258": authority}
        host = SimpleNamespace(last_game={"state": "hosting", "map": self.area, flashlight.GUEST_AUTHORITY: sources},
                               zone_epoch=self.epoch, transitioning=False, last_seen=10,
                               party={"players": {"guest": {"downed": False}}},
                               native_recipients=lambda: {"guest": {"pawn": pawn, "connected": True}},
                               guest_flashlights={})
        with patch.object(flashlight.time, "monotonic", return_value=10):
            flashlight.receive_guest(host, "guest", pawn, aimed[1])
            self.assertIsNone(flashlight.peer_frame(host))
            _, sources["258"] = flashlight.parse_guest_authority([flashlight.GUEST_AUTHORITY_TAG, "258", pawn, "offhand"],
                                                                "hosting")
            flashlight.receive_guest(host, "guest", pawn, [*aimed[1][:-1], 181])
            self.assertIsNone(flashlight.peer_frame(host))
            flashlight.receive_guest(host, "guest", pawn, aimed[1])
            self.assertEqual(flashlight.peer_frame(host)["offhand_sources"], [[258, 9, 0, True, 22.5, 20., 0.]])
            self.assertIsNone(flashlight.peer_frame(host, exclude="guest"))
            sources.clear()
            self.assertIsNone(flashlight.peer_frame(host))

    async def test_guest_offhand_aim_and_regular_beam_share_the_existing_observer_batch(self):
        game = {"state": "hosting", "map": self.area}
        frame = {"map": self.area, "epoch": self.epoch, "sources": [[258, 3, 1, True]],
                 "offhand_sources": [[259, 4, 0, True, 12.5, 20, 0]]}
        regular = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "258", "3", "1", "1", "1", "1"], "hosting")
        offhand = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "259", "4", "0", "1o", "1", "-",
                                         "12.5", "20", "0"], "hosting")
        applied = dict(game, **{flashlight.GUEST_VIEW: {"258": regular, "259": offhand}})
        bridge = SimpleNamespace(command=AsyncMock(return_value=applied))
        self.assertIs(await flashlight.project_peers(bridge, game, frame, self.epoch), applied)
        bridge.command.assert_awaited_once_with(flashlight.GUEST_VIEW_TAG, self.area, self.epoch,
                                               "258", "3", "1", "1", "259", "4", "0", "1o", "12.5", "20", "0", timeout=2)
        self.assertIs(await flashlight.project_peers(bridge, applied, frame, self.epoch), applied)
        duplicate = dict(frame, offhand_sources=[[258, 4, 0, True, 12.5, 20, 0]])
        self.assertIs(await flashlight.project_peers(bridge, applied, duplicate, self.epoch), applied)
