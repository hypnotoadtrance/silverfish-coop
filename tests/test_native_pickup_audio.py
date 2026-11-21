import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish.native_pickup_audio import COMMAND, PickupAudio, Playback
from tools.silverfish.travel import OBSERVATORY


class NativePickupAudioTests(unittest.IsolatedAsyncioTestCase):
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
