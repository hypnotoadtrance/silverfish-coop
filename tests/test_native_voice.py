import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_voice import COMMAND, Playback, VoiceQueue
from tools.silverfish.travel import OBSERVATORY


class NativeVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_uses_the_registered_speaker_and_playback_range(self):
        epoch = "a" * 32
        game = {
            "state": "hosting", "map": OBSERVATORY,
            "pawns": {"local": {"name": "host", "location": [0, 0, 0]},
                      "guest": {"name": "scout", "location": [120, 200, 42]}},
            "native_voice_speakers": {"host": ["Masc_01", 1.1], "scout": ["Fem_02", 0.9]},
            "native_voice_events": [[1, "hurt", "host"]],
        }
        queue = VoiceQueue()
        queue.observed(game, epoch)
        queue.received(game, epoch, "scout", [[7, "cough"], [7, "cough"], [8, "/Game/Anything"]])
        host_view = queue.frame(game, epoch, "host", host=True)
        nonce, kind, voice, pitch, point = host_view["cues"][0]
        self.assertEqual((kind, voice, pitch, point), ("cough", "Fem_02", 0.9, [120, 200, 42]))
        self.assertEqual(len(host_view["cues"]), 1)
        self.assertEqual([cue[1] for cue in queue.frame(game, epoch, "scout")["cues"]], ["hurt"])

        bridge = Mock()
        bridge.command = AsyncMock(return_value={**game, "ack": {"message": nonce}})
        control = Mock()
        listener = Playback()
        await listener.sync(bridge, control, game, host_view, epoch)
        await listener.sync(bridge, control, game, host_view, epoch)
        bridge.command.assert_awaited_once_with(COMMAND, OBSERVATORY, epoch,
                                                f"{nonce}|cough|Fem_02|0.9|120|200|42", timeout=1.5)
        bridge.command.reset_mock()
        game["pawns"]["local"]["location"] = [5000, 0, 0]
        await Playback().sync(bridge, control, game, host_view, epoch)
        bridge.command.assert_not_awaited()
