import asyncio
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
        self.actions = door.Interactions()

    async def interact(self, future=None):
        if future is None:
            future = asyncio.get_running_loop().create_future()
        return await self.actions.dispatch(self.host, self.bridge, "guest", f"{self.epoch}@{self.actor}", self.operation, future)

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

    async def test_pending_motion_releases_dispatch_and_deduplicates_until_authoritative_frame(self):
        reply = {**self.host.last_game, "sequence": 11,
                 "ack": {"message": "stock hinged door interaction dispatched closed"}}
        self.bridge.command.return_value = reply
        opened = {**self.closed, "open": True, "yaw": 90}
        frame = {**reply, "sequence": 12, door.KEY: {self.actor: opened}}

        pending, duplicate = (asyncio.get_running_loop().create_future() for _ in range(2))
        self.assertIsNone(await self.interact(pending))
        self.assertIsNone(await self.interact(duplicate))
        for unfinished in (dict(frame, sequence=10), reply):
            self.host.last_game = unfinished
            self.actions.settle(self.host)
            self.assertFalse(pending.done())
        self.host.last_game = frame
        self.actions.settle(self.host)
        self.assertIs((await pending)[door.RESULT]["open"], True)
        self.assertEqual(await pending, await duplicate)
        self.bridge.command.assert_awaited_once()
        self.bridge.wait_for.assert_not_awaited()

    async def test_travel_refuses_pending_motion_before_it_can_confirm_in_another_visit(self):
        self.bridge.command.return_value = {**self.host.last_game, "sequence": 11,
                                            "ack": {"message": "stock hinged door interaction dispatched closed"}}
        pending = asyncio.get_running_loop().create_future()
        self.assertIsNone(await self.interact(pending))
        self.host.zone_epoch = "c" * 32
        self.actions.settle(self.host)
        self.assertEqual(await pending, {"ok": False, "error": "stock_door_scope_unavailable"})
        self.assertFalse(self.host.party["receipts"]["guest"])
