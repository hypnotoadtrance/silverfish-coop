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
        game = {"state": "hosting", "map": self.area, flashlight.GUEST_AUTHORITY: {"258": "pawn-a", "259": "pawn-b"}}
        players = {"a": {"downed": False}, "b": {"downed": False}}
        members = {"a": {"pawn": "pawn-a", "connected": True}, "b": {"pawn": "pawn-b", "connected": True}}
        host = SimpleNamespace(last_game=game, zone_epoch=self.epoch, transitioning=False, last_seen=10,
                               party={"players": players}, native_recipients=lambda: members, guest_flashlights={})
        with patch.object(flashlight.time, "monotonic", return_value=10):
            flashlight.receive_guest(host, "a", "unbound", [5, 0, True])
            flashlight.receive_guest(host, "a", "pawn-a", [259, 5, 0, True])
            self.assertIsNone(flashlight.peer_frame(host))
            flashlight.receive_guest(host, "a", "pawn-a", [5, 1, True])
            flashlight.receive_guest(host, "b", "pawn-b", [9, 0, False])
            flashlight.receive_guest(host, "a", "pawn-a", [5, 0, False])
            flashlight.receive_guest(host, "a", "pawn-a", [5, 1, False])
            self.assertEqual(flashlight.peer_frame(host)["sources"], [[258, 5, 1, True], [259, 9, 0, False]])
            self.assertEqual(flashlight.peer_frame(host, exclude="a")["sources"], [[259, 9, 0, False]])
            players["a"]["downed"] = True
            self.assertEqual(flashlight.peer_frame(host)["sources"], [[259, 9, 0, False]])
            members["b"]["pawn"] = "new-pawn"
            self.assertIsNone(flashlight.peer_frame(host))
            self.assertEqual(host.guest_flashlights, {})

    def test_source_is_exactly_local_and_temporary(self):
        pawn = "Player_FP_C /Game/Crossroads.Player_42"
        source = flashlight.parse_guest_source([flashlight.GUEST_SOURCE_TAG, pawn, "8", "1", "1"], "connected")
        game = {"pawns": {"local": {"name": pawn}}, flashlight.GUEST_SOURCE: source}
        self.assertEqual(flashlight.guest_update(game, self.epoch), (self.epoch, (8, 1, True)))
        game["pawns"]["local"]["name"] += "_replacement"
        self.assertIsNone(flashlight.guest_update(game, self.epoch))

        host = SimpleNamespace(last_game={"state": "hosting", "map": self.area, flashlight.GUEST_AUTHORITY: {"258": pawn}},
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
        incoming = {"map": self.area, "epoch": self.epoch, "sources": [[258, 3, 1, True], [259, 4, 2, False]]}
        one = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "258", "3", "1", "1", "1", "1"], "hosting")
        two = flashlight.parse_view([flashlight.GUEST_VIEW_TAG, self.epoch, "259", "4", "2", "0", "0", "-"], "hosting")
        applied = dict(game, **{flashlight.GUEST_VIEW: {"258": one, "259": two}})
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[applied, game]))

        self.assertIs(await flashlight.project_peers(bridge, game, incoming, self.epoch), applied)
        bridge.command.assert_awaited_once_with(flashlight.GUEST_VIEW_TAG, self.area, self.epoch,
                                               "258", "3", "1", "1", "259", "4", "2", "0", timeout=2)
        self.assertIs(await flashlight.project_peers(bridge, applied, incoming, self.epoch), applied)
        duplicate = dict(incoming, sources=[[258, 3, 1, True], [258, 4, 2, False]])
        self.assertIs(await flashlight.project_peers(bridge, applied, duplicate, self.epoch), applied)
        self.assertIs(await flashlight.project_peers(bridge, applied, None, self.epoch), game)
        self.assertIs(await flashlight.project_peers(bridge, game, None, self.epoch), game)
        self.assertEqual(bridge.command.await_count, 2)
