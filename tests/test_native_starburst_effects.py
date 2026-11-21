import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_starburst_effects import Playback


class StarburstPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_spatial_cues_play_once_per_authenticated_visit(self):
        playback = Playback()
        epoch = "a" * 32
        game = {"state": "connected", "map": "Crossroads", "pawns": {"local": {"location": [0, 0, 0]}}}
        frame = {"map": game["map"], "epoch": epoch, "cues": [
            [1, "charge", [100, 0, 0], 1],
            [1, "charge", [100, 0, 0], 1],
            [2, "charge", [4100, 0, 0], 1],
            [3, "blast", [4100, 0, 0], 1],
            [4, "blast", [0, 0, 0], float("inf")],
            [5, "unknown", [0, 0, 0], 1],
        ]}
        snapshot = {**game, "ack": {"message": "1:1,3:1,3:1,7:0"}}
        bridge = Mock(command=AsyncMock(return_value=snapshot))
        control = Mock()
        self.assertIs(await playback.sync(bridge, control, game, frame, epoch, "host"), snapshot)
        bridge.command.assert_awaited_once_with(
            "native-starburst-play-cues", game["map"], epoch, "1|charge|100|0|0|1", "3|blast|4100|0|0|1", timeout=1.5,
        )
        self.assertEqual([call.kwargs["serial"] for call in control.event.call_args_list], [1, 3])
        self.assertTrue(all(call.kwargs["particles"] for call in control.event.call_args_list))
        await playback.sync(bridge, control, game, frame, epoch, "host")
        await playback.sync(bridge, control, game, frame, "b" * 32, "host")
        self.assertEqual(bridge.command.await_count, 1)
