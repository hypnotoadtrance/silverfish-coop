import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_spratt as spratt, party, runtime


TARGET = f"NPC_Spratt_C {spratt.LEVEL}NPC_Spratt_C_2147483491"
PAWN = "exact detached guest pawn"
OWNER = "guest"


class NativeSprattTests(unittest.IsolatedAsyncioTestCase):
    def proof(self, directory, *, target=TARGET, callback="1", map_name=spratt.AREA):
        rows = (
            ("schema", "1"),
            ("session", "host", "hosting", map_name, "1", "0"),
            ("native-spratt-objective", spratt.AREA, spratt.SPAWNER, target, spratt.VERIFIED, "1",
             "1", "2", "7", "7", "23", "23", "guest", PAWN, "exact stock pellet", "0", callback),
        )
        bridge = runtime.Bridge(directory, None)
        (directory / "status.tsv").write_text("".join("\t".join(row) + "\n" for row in rows), encoding="utf-8")
        return bridge.snapshot()

    def host(self, game, *, enrolled=True):
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, OWNER)
        return SimpleNamespace(party=state, last_game=game, last_seen=time.monotonic(),
                               native_profile=True, native_guest=True, native_zones=True, stopping=False,
                               transitioning=False, native_checkpoint_unsafe=False, checkpoint_native=AsyncMock(return_value=4),
                               native_recipients=lambda: {OWNER: {"pawn": PAWN}} if enrolled else {}, control=Mock())

    async def test_exact_stock_callback_only_adopts_objective_once(self):
        with tempfile.TemporaryDirectory() as temp:
            game = self.proof(Path(temp))
        host = self.host(game)
        bridge = object()
        await spratt.observe(host, bridge)
        entry = host.party["native_quests"][spratt.QUEST]
        self.assertEqual((entry["step"], entry["actor"]), (2, OWNER))
        self.assertNotIn("cash", entry)
        self.assertEqual(host.party["economy"]["wallet"], 0)
        await spratt.observe(host, bridge)
        host.checkpoint_native.assert_awaited_once_with(bridge, host.party)

    async def test_missing_callback_or_unauthenticated_shooter_blocks_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            for callback, enrolled in (("0", True), ("2", True), ("1", False)):
                with self.subTest(callback=callback, enrolled=enrolled):
                    host = self.host(self.proof(Path(temp), callback=callback), enrolled=enrolled)
                    with self.assertRaisesRegex(RuntimeError, "exact quest transition and ownership"):
                        await spratt.observe(host, object())
                    self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
                    host.checkpoint_native.assert_not_awaited()

    async def test_unrelated_actor_and_wrong_map_do_not_produce_objective(self):
        with tempfile.TemporaryDirectory() as temp:
            for game in (self.proof(Path(temp), target=TARGET.replace("NPC_Spratt", "NPC_Lenny")),
                         self.proof(Path(temp), map_name="/Wrong")):
                self.assertNotIn(spratt.KEY, game)
                host = self.host(game)
                await spratt.observe(host, object())
                host.checkpoint_native.assert_not_awaited()
