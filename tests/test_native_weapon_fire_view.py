import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish.native_weapon_fire_view import Playback, _captured


class ItemUseCueTests(unittest.IsolatedAsyncioTestCase):
    item = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
    epoch = "1" * 32
    area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"

    def test_accepts_stock_item_use_and_discards_malformed_cues(self):
        mailbox = (f"{self.area}\n1|consume|{self.item}|121|-59|2.05\n"
                   f"2|consume|{self.item}|nan|0|0\n"
                   "3|consume|/Script/Engine.PlayerController|0|0|0\n")
        self.assertEqual(_captured(mailbox), (self.area, [[1, "consume", self.item, 121.0, -59.0, 2.05]]))

    async def test_relayed_use_is_played_once_in_its_visit(self):
        game = {"map": self.area}
        bridge = SimpleNamespace(command=AsyncMock(return_value={"ack": {"message": "1:consume"}}))
        control = SimpleNamespace(event=Mock())
        projection = Playback()
        message = json.dumps([self.area, self.epoch, [1, "consume", self.item, 121, -59, 2.05]])
        await projection.sync(bridge, control, game, [message], "2" * 32)
        bridge.command.assert_not_called()
        await projection.sync(bridge, control, game, [message], self.epoch)
        await projection.sync(bridge, control, game, [message], self.epoch)
        bridge.command.assert_awaited_once_with("native-weapon-play-shots", self.area, self.epoch,
                                               f"1|consume|{self.item}|121|-59|2.05", timeout=1.5)
        control.event.assert_called_once_with("native_avatar_use_sound_played", serial=1, item_class=self.item)
