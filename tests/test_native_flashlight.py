from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

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
