"""Round-trip scoped host checkpoints through the Steam Auto-Cloud backup format."""

from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
import uuid
from zipfile import ZipFile

from tools.silverfish import direct_ip, native_host_save, runtime, service
from tools.silverfish.campaign import CampaignStore, DELETED
from tools.silverfish.steam_cloud import CloudBackups, deleted_campaign
from tools.silverfish.title_host_campaigns import campaign_entries, manage_campaign
from tools.silverfish.travel import CROSSROADS


class CloudBackupTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.first, self.second = (CloudBackups(root / seat / "campaigns", root / seat / "game-user")
                                  for seat in ("first", "second"))
        for device in (self.first, self.second):
            device.directory.mkdir(parents=True)
            (device.root / "20260101-101010.sav").write_bytes(b"unchanged solo slot")
        self.identity = direct_ip._new_identity_record()
        runtime.write_json(self.first.campaigns.parent / direct_ip.WORKSPACE_IDENTITY, self.identity)
        self.campaign = self.first.campaigns / "original-name"
        with CampaignStore(self.campaign / "save") as store:
            self.campaign_hex = uuid.UUID(store.status()["campaign_id"]).hex
            self.world = {"map": CROSSROADS, native_host_save.SCOPE_FIELD: self.campaign_hex,
                          "native_host_save": self.generation(self.first, b"host world")}
            self.guest = store.issue_guest("Guest")
            self.character = store.create_character(name="Scout", **self.guest)["character_id"]
            self.guest_frame = dict(self.generation(self.first, b"guest inventory and stash"),
                                    character_id=self.character)
            store.checkpoint(self.world, {}, unleased_states={
                self.character: {"native_guest_save": self.guest_frame},
            })
            self.assertNotIn(self.guest["credential"], store.path.read_text())
        self.restored = self.second.campaigns / f"campaign-{self.campaign_hex}"
        self.backup = self.first.directory / f"SFCoopBackup-{self.campaign_hex}.sav"
        self.download = self.second.directory / self.backup.name

    def generation(self, device, data):
        storage = native_host_save.SaveScope(device.profile, self.campaign_hex)
        native_host_save.directory(storage).mkdir(parents=True, exist_ok=True)
        path = native_host_save.slot_path(storage, native_host_save.new_slot())
        path.write_bytes(b"GVAS" + data + bytes(16))
        return native_host_save.record(storage, path.stem, CROSSROADS)

    def checkpoint(self, device, campaign):
        return service.menu_host_checkpoint(campaign, profile=device.profile, require_scoped=True)

    def test_snapshot_contains_host_and_guest_and_restores_through_normal_save_reader(self):
        self.generation(self.first, b"unreferenced generation")
        self.assertTrue(self.first.publish(self.campaign))
        self.assertFalse(self.first.publish(self.campaign))
        with ZipFile(self.backup) as archive:
            self.assertEqual(set(archive.namelist()), {
                "campaign.json", direct_ip.RESTORED_IDENTITY,
                self.world["native_host_save"]["slot"] + ".sav", self.guest_frame["slot"] + ".sav",
            })
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        self.assertEqual(self.checkpoint(self.second, self.restored), self.checkpoint(self.first, self.campaign))
        destination_cert = direct_ip.resident_host_identity(self.second.campaigns.parent, self.restored, 1).certificate_pem
        self.assertEqual(destination_cert, self.identity["certificate"])
        with CampaignStore(self.restored / "save", require_existing=True) as store:
            self.assertEqual(store.list_characters(**self.guest)[0]["character_id"], self.character)
        self.assertEqual([path.name for path in self.second.root.glob("*.sav")], ["20260101-101010.sav"])
        self.assertEqual((self.second.root / "20260101-101010.sav").read_bytes(), b"unchanged solo slot")

    def test_older_backup_never_overwrites_higher_local_revision_even_if_native_data_is_missing(self):
        self.first.publish(self.campaign)
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        with CampaignStore(self.restored / "save") as store:
            store.save_world(dict(self.world, local_flag=True))
            primary = store.path
            previous = store.previous_path
            before = primary.read_bytes(), previous.read_bytes()
        storage = native_host_save.SaveScope(self.second.profile, self.campaign_hex)
        native_host_save.slot_path(storage, self.world["native_host_save"]["slot"]).unlink()
        self.second.restore()
        self.assertEqual((primary.read_bytes(), previous.read_bytes()), before)

    def test_deletion_revision_survives_an_older_cloud_backup(self):
        self.assertTrue(self.first.publish(self.campaign))
        old = self.backup.read_bytes()
        self.download.write_bytes(old)
        self.second.restore()
        saved = self.first.profile / "Saved"
        entry, = campaign_entries(self.campaign, saved)
        manage_campaign(self.campaign, saved, entry, "delete-character", self.character, self.first)
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        deleted = self.checkpoint(self.second, self.restored)
        self.assertEqual(deleted["characters"][self.character]["state"], {DELETED: True})
        with ZipFile(self.backup) as archive:
            self.assertNotIn(self.guest_frame["slot"] + ".sav", archive.namelist())
        self.download.write_bytes(old)
        self.second.restore()
        self.assertEqual(self.checkpoint(self.second, self.restored), deleted)

        entry, = campaign_entries(self.campaign, saved)
        manage_campaign(self.campaign, saved, entry, "delete-save", None, self.first)
        with ZipFile(self.backup) as archive:
            self.assertEqual(archive.namelist(), ["deleted-campaign"])
        self.backup.with_suffix(".tmp").write_bytes(old)
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        self.assertTrue(deleted_campaign(self.second.campaigns, self.campaign_hex))
        shutil.copyfile(self.backup.with_suffix(".tmp"), self.backup)
        self.first.restore()
        self.assertIsNone(self.checkpoint(self.first, self.campaign))
        self.assertEqual(campaign_entries(self.restored, self.second.profile / "Saved"), ())
        self.assertEqual((self.second.root / "20260101-101010.sav").read_bytes(), b"unchanged solo slot")

    def test_newer_backup_updates_same_campaign_and_keeps_previous_checkpoint(self):
        self.first.publish(self.campaign)
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        previous = self.checkpoint(self.second, self.restored)
        renamed = self.restored.with_name("a-local-campaign-name")
        self.restored.rename(renamed)
        with CampaignStore(self.campaign / "save") as store:
            store.save_world(dict(self.world, native_host_save=self.generation(self.first, b"new world")))
        self.assertTrue(self.first.publish(self.campaign))
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        self.assertFalse(self.restored.exists())
        self.assertEqual(self.checkpoint(self.second, renamed), self.checkpoint(self.first, self.campaign))
        saved_previous = CampaignStore.decode((renamed / "save/campaign.previous.json").read_bytes())
        self.assertEqual(saved_previous, previous)

    def test_local_publisher_does_not_replace_newer_download(self):
        self.first.publish(self.campaign)
        shutil.copyfile(self.backup, self.download)
        self.second.restore()
        with CampaignStore(self.restored / "save") as store:
            store.save_world(dict(self.world, new_flag=True))
        self.second.publish(self.restored)
        downloaded = self.download.read_bytes()
        self.backup.write_bytes(downloaded)
        self.assertFalse(self.first.publish(self.campaign))
        self.assertEqual(self.backup.read_bytes(), downloaded)

    def test_corrupt_archive_cannot_publish_a_checkpoint(self):
        self.first.publish(self.campaign)
        target = self.guest_frame["slot"] + ".sav"
        with ZipFile(self.backup) as good, ZipFile(self.download, "w") as damaged:
            for name in good.namelist():
                data = good.read(name)
                damaged.writestr(name, data[:-1] + b"\x01" if name == target else data)
        with self.assertLogs("tools.silverfish.steam_cloud", "WARNING"):
            self.second.restore()
        self.assertFalse((self.restored / "save/campaign.json").exists())
        self.assertFalse((self.restored / "save/campaign.previous.json").exists())
