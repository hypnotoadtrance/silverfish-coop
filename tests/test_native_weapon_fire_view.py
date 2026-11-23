import asyncio
import ctypes
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from tools.silverfish.local_bridge import LocalBridge, WEAPON_CUES_LIMIT
from tools.silverfish.native_weapon_fire_view import Playback, _captured, relay


class ItemUseCueTests(unittest.IsolatedAsyncioTestCase):
    item = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
    epoch = "1" * 32
    area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"

    def test_accepts_stock_item_use_and_discards_malformed_cues(self):
        mailbox = (f"{self.area}\n1|consume|{self.item}|121|-59|2.05|257\n"
                   f"2|consume|{self.item}|nan|0|0\n"
                   "3|consume|/Script/Engine.PlayerController|0|0|0\n"
                   f"4|consume|{self.item}|0|0|0|-1\n")
        self.assertEqual(_captured(mailbox), (self.area, [[1, "consume", self.item, 121.0, -59.0, 2.05, 257]]))

    async def test_relayed_use_is_played_once_in_its_visit(self):
        game = {"map": self.area}
        bridge = SimpleNamespace(command=AsyncMock(return_value={"ack": {"message": "1:consume,1:consume_visual"}}))
        control = SimpleNamespace(event=Mock())
        projection = Playback()
        message = json.dumps([self.area, self.epoch, [1, "consume", self.item, 121, -59, 2.05, 257]])
        await projection.sync(bridge, control, game, [message], "2" * 32)
        bridge.command.assert_not_called()
        await projection.sync(bridge, control, game, [message], self.epoch)
        await projection.sync(bridge, control, game, [message], self.epoch)
        bridge.command.assert_awaited_once_with("native-weapon-play-shots", self.area, self.epoch,
                                               f"1|consume|{self.item}|121|-59|2.05|257", timeout=1.5)
        self.assertEqual(control.event.call_count, 2)
        control.event.assert_any_call("native_avatar_use_sound_played", serial=1, item_class=self.item)
        control.event.assert_any_call("native_avatar_use_visual_played", serial=1, item_class=self.item, player_id=257)

    async def test_dry_fire_has_only_a_deduplicated_sound_receipt(self):
        mailbox = f"{self.area}\n3|dry|121|-59|2.05\n4|dry|inf|0|0\n"
        area, cues = _captured(mailbox)
        self.assertEqual(cues, [[3, "dry", 121.0, -59.0, 2.05]])
        bridge = SimpleNamespace(command=AsyncMock(return_value={"ack": {"message": "3:dry,3:1:1:1:1"}}))
        control = SimpleNamespace(event=Mock())
        observer = Playback()
        payload = [json.dumps([area, self.epoch, cues[0]])]
        await observer.sync(bridge, control, {"map": area}, payload, self.epoch)
        await observer.sync(bridge, control, {"map": area}, payload, self.epoch)
        bridge.command.assert_awaited_once_with("native-weapon-play-shots", area, self.epoch,
                                               "3|dry|121.00|-59.00|2.05", timeout=1.5)
        control.event.assert_called_once_with("native_weapon_dry_fire_played", serial=3)

    async def test_resident_relay_reads_only_its_memory_queue_and_refuses_the_wrong_map(self):
        host = self.host()
        path = MagicMock(spec=Path)
        mailbox = Mock(local_weapon_cues_available=True)
        reads = iter((self.frame(1, self.area + "_Old"), self.frame(2), self.frame(2), self.frame(3), None))

        def take():
            data = next(reads)
            if data is None:
                host.stopping = True
            return data

        mailbox.take_local_weapon_cues.side_effect = take
        transport = Mock()
        await asyncio.wait_for(relay(host, SimpleNamespace(directory=path, local_bridge=mailbox), transport), 1)
        path.__truediv__.return_value.stat.assert_not_called()
        forwarded = [json.loads(call.args[0]) for call in transport.send_weapon_shot.call_args_list]
        self.assertEqual(forwarded, [[self.area, self.epoch, [serial, "consume", self.item, 121, -59, 2.05, 257]]
                                     for serial in (2, 3)])
        allowed = transport.send_weapon_shot.call_args.args[1]
        self.assertTrue(allowed("connected"))
        self.assertFalse(allowed("offline"))

    async def test_missing_memory_capability_uses_the_standalone_capture(self):
        host = self.host()
        directory = Path(self.enterContext(TemporaryDirectory()))
        (directory / "native-weapon-shots").write_bytes(self.frame(1))
        mailbox = Mock(local_weapon_cues_available=False)
        transport = Mock()

        def forward(*_):
            host.stopping = True

        transport.send_weapon_shot.side_effect = forward
        await asyncio.wait_for(relay(host, SimpleNamespace(directory=directory, local_bridge=mailbox), transport), 1)
        mailbox.take_local_weapon_cues.assert_not_called()
        self.assertEqual(json.loads(transport.send_weapon_shot.call_args.args[0]),
                         [self.area, self.epoch, [1, "consume", self.item, 121, -59, 2.05, 257]])

    def test_memory_queue_requires_both_native_entry_points(self):
        library = Mock()
        del library.SilverfishBridgeEnableLocalWeaponCuesV1
        bridge = LocalBridge(library)
        bridge.enable_local_weapon_cues()
        self.assertFalse(bridge.local_weapon_cues_available)
        self.assertIsNone(bridge.take_local_weapon_cues())

        library = Mock()
        library.SilverfishBridgeEnableLocalWeaponCuesV1.return_value = 1
        bridge = LocalBridge(library)
        self.assertFalse(bridge.local_weapon_cues_available)
        bridge.enable_local_weapon_cues()
        self.assertTrue(bridge.local_weapon_cues_available)
        frame = self.frame(1)

        def copy(buffer, capacity):
            self.assertEqual(capacity, WEAPON_CUES_LIMIT)
            ctypes.memmove(buffer, frame, len(frame))
            return len(frame)

        library.SilverfishBridgeTakeLocalWeaponCuesV1.side_effect = copy
        self.assertEqual(bridge.take_local_weapon_cues(), frame)
        library.SilverfishBridgeTakeLocalWeaponCuesV1.side_effect = None
        library.SilverfishBridgeTakeLocalWeaponCuesV1.return_value = WEAPON_CUES_LIMIT + 1
        self.assertIsNone(bridge.take_local_weapon_cues())
        bridge.close()
        self.assertFalse(bridge.local_weapon_cues_available)

    def frame(self, serial, area=None):
        return f"{area or self.area}\n{serial}|consume|{self.item}|121|-59|2.05|257\n".encode("ascii")

    def host(self):
        return SimpleNamespace(stopping=False, transitioning=False, zone_epoch=self.epoch,
                               last_game={"state": "hosting", "map": self.area},
                               native_recipients=lambda: {"seat": {"connected": True}},
                               authenticated_guest=lambda guest: ("guest", "seat") if guest == "connected" else None)
