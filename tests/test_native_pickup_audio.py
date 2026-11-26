import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish import native_world_loot as loot
from tools.silverfish.native_pickup_audio import COMMAND, PickupAudio, Playback
from tools.silverfish.travel import OBSERVATORY


class NativePickupAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_reply_and_snapshot_share_one_in_flight_cash_notice(self):
        from tools.silverfish.authored_cash import SMALL_STACK

        epoch, operation = "a" * 32, "b" * 32
        path = SMALL_STACK.class_path
        game = {"state": "connected", "map": OBSERVATORY}
        frame = {"map": OBSERVATORY, "epoch": epoch, "cues": [[operation, path, None]], "cash": {operation: 45}}
        dispatched, accepted = asyncio.Event(), asyncio.Event()

        async def local_command(*args, **kwargs):
            dispatched.set()
            await accepted.wait()
            return {"ack": {"message": operation}}

        bridge, control, playback = Mock(command=AsyncMock(side_effect=local_command)), Mock(), Playback()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(playback.sync(bridge, control, game, frame, epoch, "host"))
            await dispatched.wait()
            duplicate = tasks.create_task(playback.sync(bridge, control, game, frame, epoch, "host"))
            await asyncio.sleep(0)
            accepted.set()
        self.assertIs(duplicate.result(), game)
        bridge.command.assert_awaited_once_with(COMMAND, OBSERVATORY, epoch, f"{operation}|{path}|cash|45", timeout=1.5)
        control.event.assert_called_once_with("native_cash_popup_queued", operation_id=operation, map=OBSERVATORY, amount=45)

    async def test_committed_cash_reuses_the_stock_widget_at_any_distance_on_the_guest(self):
        from tools.silverfish.authored_cash import SMALL_STACK

        epoch, operation = "a" * 32, "b" * 32
        host = {"state": "hosting", "map": OBSERVATORY,
                "pawns": {"local": {"name": "host pawn", "location": [0, 0, 0]}}}
        game = {"state": "connected", "map": OBSERVATORY,
                "pawns": {"local": {"name": "guest pawn", "location": [10000, 0, 0]}}}
        notices = PickupAudio()
        path = SMALL_STACK.class_path
        notices.append(host, epoch, "host pawn", operation, path, amount=45)
        frame = notices.frame(host, epoch, operation=operation)
        self.assertEqual(frame["cash"], {operation: 45})
        sender = Playback()
        self.assertEqual(sender.prepare(host, frame, epoch, "host")[0], [f"{operation}|{path}|0|0|0"])

        bridge = Mock(command=AsyncMock(return_value={**game, "ack": {"message": operation}}))
        control, receiver = Mock(), Playback()
        await receiver.sync(bridge, control, game, {**frame, "cash": {operation: 200}}, epoch, "host")
        bridge.command.assert_not_awaited()
        for _ in range(2):
            await receiver.sync(bridge, control, game, frame, epoch, "host")
        bridge.command.assert_awaited_once_with(
            COMMAND, OBSERVATORY, epoch, f"{operation}|{path}|0|0|0|45", timeout=1.5)
        control.event.assert_called_once_with("native_cash_popup_queued", operation_id=operation, map=OBSERVATORY, amount=45)

    async def test_cash_notice_survives_missing_positions_and_the_sound_window(self):
        from tools.silverfish.authored_cash import SMALL_STACK

        epoch, positioned, unpositioned, sound = (letter * 32 for letter in "abcd")
        path = SMALL_STACK.class_path
        host = {"state": "hosting", "map": OBSERVATORY,
                "pawns": {"local": {"name": "host pawn", "location": [0, 0, 0]}}}
        guest = {"state": "connected", "map": OBSERVATORY}
        cues = PickupAudio()
        with patch("tools.silverfish.native_pickup_audio.time.monotonic") as clock:
            clock.return_value = 100
            cues.append(host, epoch, "host pawn", positioned, path, amount=45)
            cues.append(host, epoch, "guest pawn", unpositioned, path, amount=40)
            cues.append(host, epoch, "host pawn", sound, path)
            self.assertEqual(Playback().prepare(guest, cues.frame(host, epoch), epoch, "host")[0],
                             [f"{positioned}|{path}|cash|45", f"{unpositioned}|{path}|cash|40"])
            clock.return_value = 103
            frame = cues.frame(host, epoch)
            self.assertEqual(frame["cues"], [[positioned, path, None], [unpositioned, path, None]])
            self.assertEqual(Playback().prepare(host, frame, epoch, "host"), ([], {}))
            self.assertEqual(Playback().prepare(guest, frame, "e" * 32, "host"), ([], {}))
            clock.return_value = 109
            self.assertIsNone(cues.frame(host, epoch))

        guest = {**host, "state": "connected"}
        bridge = Mock(command=AsyncMock(return_value={"ack": {"message": f"{positioned},{unpositioned}"}}))
        control, receiver = Mock(), Playback()
        for _ in range(2):
            self.assertIs(await receiver.sync(bridge, control, guest, frame, epoch, "host"), guest)
        bridge.command.assert_awaited_once_with(
            COMMAND, OBSERVATORY, epoch, f"{positioned}|{path}|cash|45", f"{unpositioned}|{path}|cash|40",
            timeout=1.5)
        self.assertEqual(control.event.call_args_list, [
            call("native_cash_popup_queued", operation_id=positioned, map=OBSERVATORY, amount=45),
            call("native_cash_popup_queued", operation_id=unpositioned, map=OBSERVATORY, amount=40),
        ])

    async def test_committed_sound_uses_claimant_position_and_is_not_replayed(self):
        epoch = "a" * 32
        claim = "b" * 32
        item = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
        host = {"state": "hosting", "map": OBSERVATORY, "pawns": {
            "local": {"name": "host pawn", "location": [100, 200, 42]},
            "guest": {"name": "exact guest pawn", "location": [250, 210, 44]},
        }}
        cues = PickupAudio()
        cues.append(host, epoch, "not a participant", claim, item)
        self.assertIsNone(cues.frame(host, epoch))
        cues.append(host, epoch, "exact guest pawn", claim, item)
        frame = cues.frame(host, epoch)
        self.assertEqual(frame["cues"], [[claim, item, [250, 210, 44]]])
        self.assertEqual(cues.frame(host, epoch, operation=claim), frame)
        self.assertIsNone(cues.frame(host, epoch, operation="c" * 32))
        self.assertIsNone(cues.frame(host, "c" * 32))

        game = {**host, "state": "connected"}
        bridge, control = Mock(), Mock()
        bridge.command = AsyncMock(return_value={**game, "ack": {"message": claim}})
        receiver = Playback()
        await receiver.sync(bridge, control, game, frame, epoch, "host-session")
        await receiver.sync(bridge, control, game, frame, epoch, "host-session")
        bridge.command.assert_awaited_once_with(
            COMMAND, OBSERVATORY, epoch, f"{claim}|{item}|250|210|44", timeout=1.5)
        control.event.assert_called_once_with("native_pickup_sound_played", operation_id=claim, map=OBSERVATORY)

        bridge.command.reset_mock()
        distant = {**game, "pawns": {"local": {"location": [5000, 210, 44]}}}
        await Playback().sync(bridge, control, distant, frame, epoch, "host-session")
        bridge.command.assert_not_awaited()

        with patch("tools.silverfish.native_pickup_audio.time.monotonic", return_value=10**12):
            self.assertIsNone(cues.frame(host, epoch))

    async def test_unacknowledged_sound_can_retry(self):
        epoch, claim = "a" * 32, "b" * 32
        item = "/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"
        frame = {"map": OBSERVATORY, "epoch": epoch, "cues": [[claim, item, [0, 0, 0]]]}
        game = {"state": "connected", "map": OBSERVATORY,
                "pawns": {"local": {"location": [0, 0, 0]}}}
        bridge, control = Mock(), Mock()
        bridge.command = AsyncMock(side_effect=[TimeoutError(), {**game, "ack": {"message": claim}}])
        receiver = Playback()
        for _ in range(3):
            await receiver.sync(bridge, control, game, frame, epoch, "host-session")
        self.assertEqual(bridge.command.await_count, 2)
        self.assertEqual(control.event.call_args.args[0], "native_pickup_sound_played")


class HostPickupPresentationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.projection_epoch = "a" * 32
        self.identity = "b" * 32
        self.audio_epoch = "c" * 32
        self.operation = "d" * 32
        self.guid = "A" * 32
        self.item = "/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"
        self.crate = "the selected stock container"
        self.game = {"state": "hosting", "map": OBSERVATORY, loot.HOST_PRESENTATION: True,
                     "pawns": {"local": {"name": "host", "location": [0, 0, 0]}}}
        self.cues, self.playback, self.control = PickupAudio(), Playback(), Mock(generation="host-session")
        self.cues.append(self.game, self.audio_epoch, "host", self.operation, self.item)
        self.host = Mock(last_game=self.game, pickup_audio=self.cues, pickup_playback=self.playback,
                         control=self.control, zone_epoch=self.audio_epoch)
        self.projection = loot.Projection()
        self.projection.epoch = self.projection_epoch
        self.projection.items[self.identity] = loot.Item(
            self.identity, self.item, {}, loot.CRATE_CLAIM, f"{self.guid}@{self.crate}", 1)
        self.result = {"native_loot_claim": {"claimed_by": "host", "class_path": self.item, "guid": self.guid},
                       "native_loot_crate": self.crate}
        self.bridge = Mock(local_bridge=object(), command=AsyncMock())
        self.args = f"{self.operation}|{self.item}|0|0|0"
        self.played = call("native_pickup_sound_played", operation_id=self.operation, map=OBSERVATORY)
        self.batch = call(loot.PRESENT_HOST_CLAIM, self.projection_epoch, self.identity, OBSERVATORY,
                          self.audio_epoch, self.args, timeout=3)

    async def retire(self):
        self.host.last_game = await self.projection.present_host_claim(
            self.host, self.bridge, (self.projection_epoch, self.identity), self.operation, self.result)
        self.assertNotIn(self.identity, self.projection.items)
        self.assertIn(self.identity, self.projection.retired)

    async def pending_sound(self):
        game = self.host.last_game
        await self.playback.sync(self.bridge, self.control, game, self.cues.frame(game, self.audio_epoch),
                                 self.audio_epoch, self.control.generation)

    async def test_host_batch_preserves_network_cue_and_avoids_local_replay(self):
        self.bridge.command.return_value = {**self.game, "ack": {"message": f"retired|{self.operation}"}}
        await self.retire()
        await self.pending_sound()
        self.assertEqual(self.bridge.command.await_args_list, [self.batch])
        self.assertEqual(self.control.event.call_args_list, [self.played])
        self.assertEqual(self.cues.frame(self.game, self.audio_epoch)["cues"][0][0], self.operation)

    async def test_short_retirement_receipts_remove_only_the_verified_item(self):
        other = "c" * 32
        game = dict(self.game, **{loot.KEY: {self.identity: {}, other: {}}})
        self.host.last_game = game
        self.bridge.command.return_value = {"ack": {"message": f"retired|{self.operation}"}}
        await self.retire()
        self.assertEqual(self.host.last_game, dict(game, **{loot.KEY: {other: {}}}))
        self.assertIn(self.identity, game[loot.KEY])
        self.assertEqual(self.control.event.call_args_list, [self.played])

        self.bridge.command.return_value = {"ack": {"message": "stock world loot retired"}}
        result = await self.projection.retire_claim(self.bridge, game, (self.projection_epoch, self.identity))
        self.assertEqual(result, self.host.last_game)
        self.bridge.command.return_value = {"ack": {"message": "unexpected proof"}}
        with self.assertRaisesRegex(RuntimeError, "did not verify retirement"):
            await self.projection.retire_claim(self.bridge, game, (self.projection_epoch, self.identity))

    async def test_retirement_failure_still_acknowledges_stock_sound(self):
        self.bridge.command.return_value = {
            **self.game, loot.KEY: {self.identity: {}},
            "ack": {"message": f"world_loot_retirement_pending|{self.operation}"}}
        await self.retire()
        await self.pending_sound()
        self.assertEqual(self.bridge.command.await_args_list, [self.batch])
        self.assertEqual(self.control.event.call_args_list, [
            self.played, call("native_world_loot_retirement_deferred", reason="world_loot_retirement_pending")])

    async def test_missing_loaded_sound_uses_regular_playback(self):
        self.bridge.command.side_effect = [
            {**self.game, "ack": {"message": "retired|"}},
            {**self.game, "ack": {"message": self.operation}},
        ]
        await self.retire()
        await self.pending_sound()
        self.assertEqual(self.bridge.command.await_args_list, [
            self.batch, call(COMMAND, OBSERVATORY, self.audio_epoch, self.args, timeout=1.5)])
        self.assertEqual(self.control.event.call_args_list, [self.played])

    async def test_unavailable_bridge_batch_keeps_the_normal_retirement(self):
        self.bridge.local_bridge = None
        self.bridge.command.return_value = self.game
        await self.retire()
        self.bridge.command.assert_awaited_once_with(loot.RETIRE, self.projection_epoch, self.identity, timeout=3)
        self.control.event.assert_not_called()

    async def test_unconfirmed_batch_leaves_retirement_and_sound_recoverable(self):
        self.bridge.command.side_effect = [TimeoutError("receipt timed out"),
                                           {**self.game, "ack": {"message": self.operation}}]
        await self.retire()
        await self.pending_sound()
        self.assertEqual(self.bridge.command.await_args_list, [
            self.batch, call(COMMAND, OBSERVATORY, self.audio_epoch, self.args, timeout=1.5)])
        self.assertEqual(self.control.event.call_args_list, [
            call("native_world_loot_retirement_deferred", reason="receipt timed out"), self.played])

    async def test_older_lua_keeps_the_normal_retirement(self):
        del self.game[loot.HOST_PRESENTATION]
        self.bridge.command.return_value = self.game
        await self.retire()
        self.bridge.command.assert_awaited_once_with(loot.RETIRE, self.projection_epoch, self.identity, timeout=3)
        self.control.event.assert_not_called()
