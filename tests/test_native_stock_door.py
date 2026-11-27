import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_stock_door as door, party, runtime
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

    def test_status_reconstructs_door_identity_and_geometry(self):
        bridge = runtime.Bridge(None, None)
        header = f"schema\t1\nseq\t11\nsession\tclient\tconnected\t{CROSSROADS}\t0\t1\n"
        pose = "0\t1\t1\t0\t90\t0\t0\t0\t0\t1\t2\t3\t4\t5\t6\t1\n"
        full = f"{door.TAG}\t{CROSSROADS}\t{self.actor}\t{pose}"
        compact = f"{door.STATE_TAG}\tDoor_Wood_04_BP_C\tDoor_Wood_04_BP_C_1719\t{pose}"
        self.assertLess(len(compact), len(full))
        decoded = bridge._parse_status(header + compact)
        self.assertEqual(decoded, bridge._parse_status(header + full))
        self.assertEqual(decoded[door.KEY][self.actor]["knob_location"], [4, 5, 6])
        self.assertIsNone(bridge._parse_status(header + full + compact))
        self.assertIsNone(bridge._parse_status(header + compact.replace("1719", "../1719")))
        host_header = header.replace("client\tconnected", "host\thosting")
        stamped = compact.rstrip("\n") + "\t9\n"
        self.assertEqual(bridge._parse_status(host_header + stamped)[door.KEY][self.actor][door.OBSERVED], 9)
        self.assertIsNone(bridge._parse_status(header + stamped))
        self.assertIsNone(bridge._parse_status(host_header + stamped.replace("\t9\n", "\t0\n")))

    async def test_streamed_identity_routes_in_active_visit(self):
        actor = "stream:Door_C /WorkshopPack/Rooms/Lab_copy.Lab:PersistentLevel.Door_C_11"
        self.actor = actor
        game = self.host.last_game
        game[door.KEY] = {actor: self.closed}
        self.bridge.command.return_value = {
            **game, "sequence": 11, "ack": {"message": "stock hinged door interaction dispatched open"},
        }
        self.assertEqual((await self.interact())[door.RESULT]["actor"], actor)
        self.assertEqual(door.target_actor("stock-door:" + actor), actor)
        self.assertFalse(door.matches_area(actor.removeprefix("stream:"), CROSSROADS))
        self.assertFalse(door.matches_area(actor, "/WorkshopPack/Rooms/Lab_copy"))
        self.assertFalse(door.matches_area(actor.replace("Lab:Persistent", "../Lab:Persistent"), CROSSROADS))
        self.assertEqual(door.actor_map(
            "Door_C /WorkshopPack/Maps/Lab.Lab:PersistentLevel.Door_C_11"), "/WorkshopPack/Maps/Lab")

    async def test_close_is_acknowledged_even_when_periodic_sample_already_says_closed(self):
        self.bridge.command.return_value = {
            **self.host.last_game, "sequence": 11,
            "ack": {"message": "stock hinged door interaction dispatched open"},
        }
        reply = await self.interact()
        self.assertIs(reply[door.RESULT]["open"], False)
        self.assertEqual(reply[door.DELTA]["sequence"], 11)
        self.assertEqual(await self.interact(), reply)
        self.host.last_game = {"state": "opening", "map": CROSSROADS}
        self.host.native_recipients = lambda: {}
        self.assertEqual(await self.interact(), reply)
        completed = self.operation
        self.operation = "c" * 32
        with self.assertRaisesRegex(party.RuleError, "^stock_door_scope_unavailable$"):
            await self.interact()
        self.operation = completed
        self.host.zone_epoch = "d" * 32
        with self.assertRaisesRegex(party.RuleError, "^stock_door_scope_unavailable$"):
            await self.interact()
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
        published = self.actions.world_frame(self.host.last_game, self.epoch)
        self.assertEqual(published, response[door.DELTA])
        other = self.actor.replace("_1719", "_1720")
        same_frame = {**snapshot, "sequence": 12, door.KEY: {self.actor: opening, other: self.closed}}
        self.assertEqual(self.actions.world_frame(same_frame, self.epoch)["doors"],
                         {self.actor: response[door.DELTA]["doors"][self.actor], other: self.closed})
        newer = {**same_frame, "sequence": 13}
        self.assertEqual(self.actions.world_frame(newer, self.epoch), door.frame(newer, self.epoch))
        self.assertFalse(self.actions.confirmed)

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

    async def test_new_world_frame_preserves_confirmation_over_a_cached_door_reading(self):
        dispatch, host_pawn = "c" * 32, "host pawn"
        other = self.actor.replace("_1719", "_1720")
        game = self.host.last_game
        game.update(sequence=11, pawns={"local": {"name": host_pawn}})
        self.bridge.command.return_value = {
            "state": "hosting", "map": CROSSROADS, "sequence": 11, door.LOCAL_SCOPE: host_pawn,
            "ack": {"id": dispatch, "message": "stock hinged door interaction dispatched closed|0|90|0|0|0|0"},
        }
        pending = asyncio.get_running_loop().create_future()
        self.assertIsNone(await self.interact(pending))
        cached = {**self.closed, door.OBSERVED: 10}
        game.update(sequence=15, native_stock_doors={self.actor: cached, other: dict(self.closed, open=True, yaw=45)})
        row = (door.TAG, CROSSROADS, self.actor, "0", "1", "1", "0", "90", *("0",) * 10, "1", "12")
        record = ("stock-door-settled", dispatch, "12", "350", *row)
        self.actions.settle(self.host, ["\t".join(record).encode()])
        self.assertTrue(pending.done())
        receipt = pending.result()[door.DELTA]["doors"][self.actor]
        self.assertTrue(receipt["open"])
        for world_sequence in (15, 22):
            game["sequence"] = world_sequence
            frame = self.actions.world_frame(game, self.epoch)
            self.assertEqual(frame["doors"][self.actor], receipt)
            self.assertEqual(frame["doors"][other]["yaw"], 45)
            self.assertEqual(frame["doors"][self.actor][door.OBSERVED], 12)
        game[door.KEY][self.actor] = {**cached, door.OBSERVED: 22}
        self.assertFalse(self.actions.world_frame(game, self.epoch)["doors"][self.actor]["open"])
        self.assertFalse(self.actions.confirmed)

    def test_guest_accepts_matching_door_receipt_after_newer_unrelated_world_update(self):
        view = door.GuestView()
        other = self.actor.replace("_1719", "_1720")
        before = dict(self.closed, observed_at=10)
        opened = dict(before, open=True, yaw=90, observed_at=12)
        scope = {"map": CROSSROADS, "epoch": self.epoch}
        game = {door.KEY: {**scope, "sequence": 20,
                          "doors": {self.actor: before, other: dict(self.closed, open=True, observed_at=20)}}}
        view.reconcile(game, CROSSROADS, self.epoch)
        receipt = {**scope, "sequence": 12, "doors": {self.actor: opened}}
        self.assertEqual(view.accept(game, receipt, CROSSROADS, self.epoch, self.actor), receipt)
        stale = {**scope, "sequence": 21, "doors": {self.actor: before, other: dict(self.closed, observed_at=21)}}
        game = {door.KEY: stale}
        view.reconcile(game, CROSSROADS, self.epoch)
        self.assertEqual(game[door.KEY]["doors"][self.actor], opened)
        self.assertEqual(game[door.KEY]["doors"][other], stale["doors"][other])
        self.assertEqual(game[door.KEY]["sequence"], 21)
        game = {door.KEY: {**stale, "sequence": 23, "doors": {self.actor: dict(before, observed_at=23)}}}
        view.reconcile(game, CROSSROADS, self.epoch)
        self.assertFalse(game[door.KEY]["doors"][self.actor]["open"])
        self.assertIsNone(view.accept(game, receipt, CROSSROADS, self.epoch, self.actor))
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

    def test_receipts_are_ordered_per_door_and_reconciled_against_snapshots(self):
        other = self.actor.replace("_1719", "_1720")
        visit = {"map": CROSSROADS, "epoch": self.epoch}
        view = door.GuestView()
        baseline = {**visit, "sequence": 8, "doors": {self.actor: self.closed, other: self.closed}}
        gameplay = {door.KEY: baseline}
        view.reconcile(gameplay, CROSSROADS, self.epoch)
        opened = dict(self.closed, open=True, yaw=90)
        newer = {**visit, "sequence": 10, "doors": {self.actor: opened}}
        earlier = {**visit, "sequence": 9, "doors": {other: opened}}
        self.assertEqual(view.accept(gameplay, newer, CROSSROADS, self.epoch, self.actor), newer)
        self.assertEqual(view.accept(gameplay, earlier, CROSSROADS, self.epoch, other), earlier)
        self.assertEqual(gameplay[door.KEY]["doors"], {self.actor: opened, other: opened})
        self.assertIsNone(view.accept(gameplay, {**earlier, "doors": {self.actor: self.closed}},
                                      CROSSROADS, self.epoch, self.actor))

        snapshot = {**baseline, "sequence": 9, "doors": {self.actor: self.closed, other: opened}}
        gameplay = {door.KEY: snapshot}
        view.reconcile(gameplay, CROSSROADS, self.epoch)
        self.assertEqual(gameplay[door.KEY]["doors"], {self.actor: opened, other: opened})
        self.assertEqual(snapshot["doors"][self.actor], self.closed)
        self.assertNotIn(other, view.receipts)

        snapshot = {**baseline, "sequence": 11}
        gameplay = {door.KEY: snapshot}
        view.reconcile(gameplay, CROSSROADS, self.epoch)
        self.assertEqual(gameplay[door.KEY], snapshot)
        self.assertIsNone(view.accept(gameplay, newer, CROSSROADS, self.epoch, self.actor))

    async def test_compact_projection_applies_only_verified_doors_and_keeps_other_world_state(self):
        other = self.actor.replace("_1719", "_1720")
        untouched = self.actor.replace("_1719", "_1721")
        pawn = "Player_FP_C local guest"
        opened = dict(self.closed, open=True, yaw=90)
        game = {"state": "connected", "map": CROSSROADS, "sequence": 8, "pawns": {"local": {"name": pawn}},
                "native_wallet": 20, door.KEY: {self.actor: self.closed, other: self.closed, untouched: self.closed}}
        frame = {"map": CROSSROADS, "epoch": self.epoch, "sequence": 9, "doors": {self.actor: opened, other: opened}}
        bridge = runtime.Bridge(None, None)
        bridge._local_status = game
        response = dict(sequence=9, pawn=pawn, actor=self.actor)

        async def command(payload, timeout):
            nonce, action, area, first, second = payload.decode().strip().split("\t")
            self.assertEqual((action, area), (door.PROJECT, CROSSROADS))
            self.assertTrue(first.startswith(self.actor + "|") and second.startswith(other + "|"))
            return (f"native-stock-interaction-reply\t1\t{nonce}\tconnected\t{area}\t{response['sequence']}\t"
                    f"{response['pawn']}\taccepted\tstock door flags and hinge transform synchronized|2\n"
                    f"{door.TAG}\t{area}\t{response['actor']}\t0\t1\t1\t0\t90\t0\t0\t0\t0\t1\t2\t3\t4\t5\t6\t3\n")

        bridge.local_bridge = SimpleNamespace(command=command)
        updated = await door.project(bridge, game, frame, self.epoch)
        self.assertIs(bridge._local_status, game)
        self.assertIs(updated["pawns"], game["pawns"])
        self.assertEqual(updated["native_wallet"], 20)
        self.assertEqual(updated["sequence"], 8)
        self.assertTrue(door.matches(updated[door.KEY][self.actor], opened))
        self.assertEqual(updated[door.KEY][self.actor]["knob_location"], [4, 5, 6])
        self.assertIs(updated[door.KEY][other], self.closed)
        self.assertIs(updated[door.KEY][untouched], self.closed)
        response["pawn"] = "departed pawn"
        self.assertIs(await door.project(bridge, game, frame, self.epoch), game)
        response.update(pawn=pawn, sequence=7)
        self.assertIs(await door.project(bridge, game, frame, self.epoch), game)
        response.update(sequence=9, actor=untouched)
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            await door.project(bridge, game, frame, self.epoch)
