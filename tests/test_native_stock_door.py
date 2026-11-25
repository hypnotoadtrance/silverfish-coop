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

    async def test_native_settlement_matches_dispatch_and_preserves_the_world_snapshot(self):
        dispatch = "c" * 32
        opening = {**self.closed, "open": True}
        snapshot = {**self.host.last_game, "sequence": 11, door.KEY: {self.actor: opening},
                    "ack": {"id": dispatch, "message": "stock hinged door interaction dispatched open|0|10|0|0|0|0"}}
        self.bridge.command.return_value = snapshot
        completion = asyncio.get_running_loop().create_future()
        self.assertIsNone(await self.interact(completion))

        def record(ticket, sequence, yaw="10"):
            pose = ("native-stock-door", CROSSROADS, self.actor, "0", "1", "0", "0", yaw, *("0",) * 10, "1")
            fields = ("stock-door-settled", ticket, str(sequence), "350", *pose)
            return "\t".join(fields).encode()

        self.actions.settle(self.host, [record("d" * 32, 12), record(dispatch, 10), record(dispatch, 12, yaw="2")])
        self.assertFalse(completion.done())
        self.actions.settle(self.host, [record(dispatch, 12)])
        response = completion.result()
        self.assertEqual(response[door.SETTLEMENT], {"source": "memory_input", "host_dispatch_to_settle_ms": 350})
        self.assertEqual(response[door.DELTA]["sequence"], 12)
        self.assertIs(response[door.RESULT]["open"], False)
        self.assertEqual(response[door.DELTA]["doors"][self.actor]["yaw"], 10)
        self.assertIs(self.host.last_game, snapshot)
        self.assertEqual((await self.interact())[door.DELTA], response[door.DELTA])

    async def test_compact_dispatch_still_waits_for_exact_target_transform(self):
        dispatch, host_pawn = "c" * 32, "host pawn"
        game = self.host.last_game
        game.update(sequence=11, pawns={"local": {"name": host_pawn}})
        reply = {"state": "hosting", "map": CROSSROADS, "sequence": 11, door.LOCAL_SCOPE: host_pawn,
                 "ack": {"id": dispatch, "message": "stock hinged door interaction dispatched closed|0|90|0|0|0|0"}}
        self.bridge.command.return_value = reply
        completion = asyncio.get_running_loop().create_future()
        self.assertIsNone(await self.interact(completion))
        self.assertIs(self.host.last_game, game)
        self.actions.settle(self.host)
        self.assertFalse(completion.done())
        row = ("native-stock-door", CROSSROADS, self.actor, "0", "1", "1", "0", "90", *("0",) * 10, "1")
        fields = ("stock-door-settled", dispatch, "12", "350", *row)
        self.actions.settle(self.host, ["\t".join(fields).encode()])
        self.assertIs((await completion)[door.RESULT]["open"], True)
        self.assertIs(self.host.last_game, game)

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

    def test_same_host_frame_applies_both_door_receipts(self):
        other = self.actor.replace("_1719", "_1720")
        delta = {"map": CROSSROADS, "epoch": self.epoch, "sequence": 9}
        gameplay = {door.KEY: {**delta, "sequence": 8, "doors": {other: self.closed}}}
        self.assertIsNotNone(door.accept_delta(gameplay, {**delta, "doors": {self.actor: self.closed}},
                                               CROSSROADS, self.epoch, self.actor))
        opened = dict(self.closed, open=True, yaw=90)
        self.assertIsNotNone(door.accept_delta(gameplay, {**delta, "doors": {other: opened}},
                                               CROSSROADS, self.epoch, other))
        self.assertEqual(gameplay[door.KEY]["doors"], {self.actor: self.closed, other: opened})
