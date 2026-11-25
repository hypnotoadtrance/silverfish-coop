from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_door as door, party
from tools.silverfish.travel import CROSSROADS


class StockDoorActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = f"Door_Wood_04_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Door_Wood_04_BP_C_1719"
        self.epoch, self.operation = "a" * 32, "b" * 32
        self.closed = dict(locked=False, broken=True, open=False, pitch=0, yaw=0, roll=0, x=0, y=0, z=0)
        self.host = SimpleNamespace(
            zone_epoch=self.epoch, stopping=False, transitioning=False, native_checkpoint_unsafe=False,
            last_game={"state": "hosting", "map": CROSSROADS, "sequence": 8, door.KEY: {self.actor: self.closed}},
            native_recipients=lambda: {"guest": {"connected": True, "pawn": "guest pawn"}},
            party={"wipe": None, "players": {"guest": {"downed": False}}, "receipts": {}},
        )
        self.bridge = SimpleNamespace(command=AsyncMock(), wait_for=AsyncMock())

    async def interact(self):
        return await door.interact(self.host, self.bridge, "guest", f"{self.epoch}@{self.actor}", self.operation)

    async def test_close_is_acknowledged_even_when_periodic_sample_already_says_closed(self):
        self.bridge.command.return_value = {
            **self.host.last_game, "sequence": 11,
            "ack": {"message": "stock hinged door interaction dispatched open"},
        }
        reply = await self.interact()
        self.assertIs(reply[door.RESULT]["open"], False)
        self.assertEqual(reply[door.DELTA]["sequence"], 11)
        self.assertEqual(await self.interact(), reply)
        self.bridge.command.assert_awaited_once_with(door.TAG + "-guest-interact", self.actor, "guest pawn", timeout=4)
        self.bridge.wait_for.assert_not_awaited()

    async def test_locked_refusal_comes_from_live_door_even_before_census_is_ready(self):
        self.host.last_game.pop(door.KEY)
        self.bridge.command.side_effect = RuntimeError("stock_door_locked")
        with self.assertRaisesRegex(party.RuleError, "^stock_door_locked$"):
            await self.interact()
        self.bridge.command.assert_awaited_once()
        self.assertEqual(self.host.party["receipts"]["guest"], [])

    async def test_readback_wait_does_not_accept_a_pre_dispatch_frame(self):
        reply = {**self.host.last_game, "sequence": 11,
                 "ack": {"message": "stock hinged door interaction dispatched closed"}}
        self.bridge.command.return_value = reply
        opened = {**self.closed, "open": True, "yaw": 90}
        frame = {**reply, "sequence": 12, door.KEY: {self.actor: opened}}

        async def readback(predicate, timeout, *, interval):
            self.assertLess(interval, .05)
            self.assertFalse(predicate(dict(frame, sequence=10)))
            self.assertFalse(predicate(reply))
            self.assertTrue(predicate(frame))
            return frame

        self.bridge.wait_for.side_effect = readback
        self.assertIs((await self.interact())[door.RESULT]["open"], True)
