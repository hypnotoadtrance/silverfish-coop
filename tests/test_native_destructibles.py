import unittest
from unittest.mock import AsyncMock

from tools.silverfish.native_destructibles import Playback, valid_roster
from tools.silverfish.travel import OBSERVATORY


class DestructiblesTests(unittest.IsolatedAsyncioTestCase):
    def test_stream_actor_needs_its_exact_scope_marker(self):
        level = "/CoopPack/Maps/Dock.RedDock:PersistentLevel"
        actor = level + ".Plank_C_1"
        self.assertTrue(valid_roster(["Plank_C_1"]))
        self.assertTrue(valid_roster([level, actor, "Plank_C_1"]))
        self.assertTrue(valid_roster([level, "Plank_C_1"]))
        self.assertFalse(valid_roster([actor, "Plank_C_1"]))
        self.assertFalse(valid_roster([level, level.replace("RedDock", "OtherDock") + ".Plank_C_1"]))
        self.assertFalse(valid_roster([level, level]))

    async def test_projection_reapplies_after_local_stream_visibility_changes(self):
        epoch = "a" * 32
        game = {"state": "connected", "map": OBSERVATORY,
                "native_destructible_view": {"map": OBSERVATORY, "epoch": "-"}}
        frame = {"map": OBSERVATORY, "epoch": epoch, "actors": ["/CoopPack/Maps/Dock.RedDock:PersistentLevel"]}
        applied = dict(game, native_destructible_view={"map": OBSERVATORY, "epoch": epoch})
        bridge = AsyncMock()
        bridge.command.return_value = applied

        playback = Playback()
        self.assertIs(await playback.project(bridge, game, frame, epoch), applied)
        self.assertIs(await playback.project(bridge, applied, frame, epoch), applied)
        bridge.command.assert_awaited_once()
        self.assertIs(await playback.project(bridge, game, frame, epoch), applied)
        self.assertEqual(bridge.command.await_count, 2)
