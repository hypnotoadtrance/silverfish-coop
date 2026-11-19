import tempfile
import unittest
from pathlib import Path

from tools.silverfish import native_host_save
from tools.silverfish.campaign import CampaignFormatError, CampaignStore
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


class NativeHostSaveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.profile = self.root / "game-user"
        native_host_save.directory(self.profile).mkdir(parents=True)

    def frame(self, map_name):
        slot = native_host_save.new_slot()
        native_host_save.slot_path(self.profile, slot).write_bytes(b"GVAS" + bytes(32))
        return native_host_save.record(self.profile, slot, map_name)

    def test_corrupt_unreal_payload_recovers_the_entire_previous_checkpoint(self):
        previous = self.frame(CROSSROADS)
        current = self.frame(OBSERVATORY)
        with CampaignStore(self.root / "save") as store:
            store.checkpoint({"map": CROSSROADS, "native_host_save": previous, "reward": 1}, {})
            store.checkpoint({"map": OBSERVATORY, "native_host_save": current, "reward": 2}, {})
        native_host_save.slot_path(self.profile, current["slot"]).write_bytes(b"GVAS" + bytes(36))

        with CampaignStore(self.root / "save", verify_world=lambda world: native_host_save.verify(self.profile, world)) as store:
            self.assertTrue(store.recovered)
            self.assertEqual(store.status()["world"], {"map": CROSSROADS, "native_host_save": previous, "reward": 1})

    def test_cleanup_keeps_referenced_generations_and_ignores_other_saves(self):
        previous = self.frame(CROSSROADS)
        current = self.frame(CROSSROADS)
        orphan = self.frame(CROSSROADS)
        user_slot = native_host_save.directory(self.profile) / "Config.sav"
        user_slot.write_text("configuration", encoding="ascii")
        with self.assertRaises(CampaignFormatError):
            native_host_save.slot_path(self.profile, r"..\Config")

        native_host_save.prune(self.profile, {previous["slot"], current["slot"]})
        self.assertFalse(native_host_save.slot_path(self.profile, orphan["slot"]).exists())
        self.assertTrue(native_host_save.slot_path(self.profile, previous["slot"]).is_file())
        self.assertTrue(native_host_save.slot_path(self.profile, current["slot"]).is_file())
        self.assertEqual(user_slot.read_text(encoding="ascii"), "configuration")

    def test_child_frame_cannot_reference_host_or_a_different_character(self):
        host = self.frame(CROSSROADS)
        guest = self.frame(OBSERVATORY)
        character = "39a5fe21-f076-4112-bc50-d13c917d225b"
        state = {"native_guest_save": dict(guest, character_id=character)}
        checkpoint = {"world": {"map": CROSSROADS, "native_host_save": host},
                      "characters": {character: {"state": state}}}
        native_host_save.verify_checkpoint(self.profile, checkpoint)
        state["native_guest_save"] = dict(host, character_id=character)
        with self.assertRaisesRegex(CampaignFormatError, "same Unreal save generation"):
            native_host_save.verify_checkpoint(self.profile, checkpoint)
        state["native_guest_save"] = dict(guest, character_id="different")
        with self.assertRaisesRegex(CampaignFormatError, "does not belong"):
            native_host_save.verify_checkpoint(self.profile, checkpoint)
