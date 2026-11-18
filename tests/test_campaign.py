import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.silverfish.campaign import (
    AuthenticationError,
    CampaignFormatError,
    CampaignStore,
    CampaignVersionError,
    CharacterError,
    LeaseError,
    StoreBusyError,
)


class CampaignStoreTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.directory = self.root / "Coop" / "Campaign"
        self.now = 1000.0

    def open_store(self):
        return CampaignStore(self.directory, clock=lambda: self.now)

    @staticmethod
    def create_guest(store, name="Guest"):
        identity = store.issue_guest(name)
        character = store.create_character(name=f"{name} Character", state={"health": 100}, **identity)
        return identity, character

    def test_campaign_save_does_not_touch_single_player_and_survives_restart(self):
        vanilla = self.root / "SaveGames" / "Existing.sav"
        vanilla.parent.mkdir()
        vanilla.write_bytes(b"single-player")
        with self.open_store() as store:
            identity, character = self.create_guest(store)
            lease = store.activate(character_id=character["character_id"], **identity)
            revision = store.checkpoint({"world_seed": 142}, {lease["lease_id"]: {"health": 37}})
            campaign_id = store.status()["campaign_id"]
            self.assertEqual(json.loads(store.previous_path.read_bytes())["revision"], revision - 1)
            self.assertNotIn(identity["credential"], store.path.read_text())
            self.assertNotIn("credential", json.dumps(store.status()))
            self.assertNotIn(lease["lease_id"], json.dumps(store.status()))
        with self.open_store() as store:
            status = store.status()
            self.assertEqual(status["campaign_id"], campaign_id)
            self.assertEqual(status["revision"], revision)
            self.assertEqual(status["world"], {"world_seed": 142})
            self.assertEqual(status["leases"], [])
            self.assertEqual(store.list_characters(**identity)[0]["state"], {"health": 37})
        self.assertEqual(vanilla.read_bytes(), b"single-player")

    def test_character_selection_requires_owner_and_exclusive_lease(self):
        with self.open_store() as store:
            first, character = self.create_guest(store, "First")
            second, _ = self.create_guest(store, "Second")
            alternate = store.create_character(name="Alternate", **first)
            self.assertEqual(len(store.list_characters(**first)), 2)
            self.assertEqual(len(store.list_characters(**second)), 1)
            with self.assertRaises(AuthenticationError):
                store.list_characters(first["guest_id"], "A" * 43)
            with self.assertRaises(CharacterError):
                store.activate(character_id=character["character_id"], **second)
            lease = store.activate(character_id=character["character_id"], **first)
            with self.assertRaises(LeaseError):
                store.activate(character_id=character["character_id"], **first)
            store.disconnect(lease["lease_id"])
            with self.assertRaises(LeaseError):
                store.activate(character_id=alternate["character_id"], **first)
            store.release(lease["lease_id"])
            self.assertFalse(store.activate(character_id=alternate["character_id"], **first)["resumed"])

    def test_invitation_creates_guest_and_character_in_one_revision(self):
        with self.open_store() as store:
            before = store.path.read_bytes()
            with self.assertRaises(CampaignFormatError):
                store.issue_invitation("Valid guest", "\t")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.status()["guest_count"], 0)
            self.assertEqual(store.status()["characters"], [])
            self.assertEqual(store.status()["revision"], 0)

            invitation = store.issue_invitation("Valid guest", "First character")
            self.assertEqual(store.status()["revision"], 1)
            characters = store.list_characters(invitation["guest_id"], invitation["credential"])
            self.assertEqual(characters[0]["character_id"], invitation["character_id"])

    def test_reconnect_reclaims_same_lease_and_preserves_updated_state(self):
        with self.open_store() as store:
            identity, character = self.create_guest(store)
            original = store.activate(character_id=character["character_id"], **identity)
            store.disconnect(original["lease_id"])
            store.save_character(original["lease_id"], {"health": 0, "downed": True})
            self.now += 30
            self.assertEqual(store.status()["leases"][0]["grace_remaining"], 60)
            restored = store.activate(character_id=character["character_id"], **identity)
            self.assertTrue(restored["resumed"])
            self.assertEqual(restored["lease_id"], original["lease_id"])
            self.assertEqual(restored["character"]["state"], {"health": 0, "downed": True})
            self.assertEqual(store.status()["leases"][0]["state"], "active")

    def test_expired_reservation_keeps_save_without_extending_grace(self):
        with self.open_store() as store:
            identity, character = self.create_guest(store)
            original = store.activate(character_id=character["character_id"], **identity)
            store.save_character(original["lease_id"], {"health": 12})
            store.disconnect(original["lease_id"])
            self.now += 89
            store.disconnect(original["lease_id"])
            self.now += 1
            self.assertEqual(store.status()["leases"], [])
            with self.assertRaises(LeaseError):
                store.save_character(original["lease_id"], {"health": 99})
            restored = store.activate(character_id=character["character_id"], **identity)
            self.assertFalse(restored["resumed"])
            self.assertNotEqual(restored["lease_id"], original["lease_id"])
            self.assertEqual(restored["character"]["state"], {"health": 12})

    def test_invalid_checkpoint_leaves_world_characters_and_revision_unchanged(self):
        with self.open_store() as store:
            identity, character = self.create_guest(store)
            lease = store.activate(character_id=character["character_id"], **identity)
            before = store.path.read_bytes()
            with self.assertRaises(CampaignFormatError):
                store.checkpoint({"weather": "rain"}, {lease["lease_id"]: {"health": float("nan")}})
            with self.assertRaises(LeaseError):
                store.checkpoint({"weather": "rain"}, {lease["lease_id"]: {"health": 10}, "expired": {}})
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.status()["world"], {})
            self.assertEqual(store.list_characters(**identity)[0]["state"], {"health": 100})

    def test_failed_primary_replace_leaves_committed_revision(self):
        with self.open_store() as store:
            store.save_world({"day": 1})
            before = store.path.read_bytes()
            replace = os.replace

            def reject_primary(source, target):
                if target == store.path:
                    raise OSError("simulated storage failure")
                return replace(source, target)

            with patch("tools.silverfish.campaign.os.replace", side_effect=reject_primary):
                with self.assertRaises(OSError):
                    store.save_world({"day": 2})
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.status()["world"], {"day": 1})
            self.assertFalse(list(self.directory.glob("*.tmp")))
        with self.open_store() as store:
            self.assertEqual(store.status()["world"], {"day": 1})

    def test_corrupt_primary_recovers_previous_but_unsupported_schema_is_preserved(self):
        with self.open_store() as store:
            store.save_world({"day": 1})
            store.save_world({"day": 2})
            store.path.write_text("{corrupt", encoding="utf-8")
        with self.open_store() as store:
            self.assertTrue(store.status()["recovered"])
            self.assertEqual(store.status()["world"], {"day": 1})
            saved = json.loads(store.path.read_bytes())
            saved["schema_version"] += 1
            future_save = json.dumps(saved).encode()
            store.path.write_bytes(future_save)
        with self.assertRaises(CampaignVersionError):
            self.open_store()
        self.assertEqual((self.directory / "campaign.json").read_bytes(), future_save)

    def test_second_writer_cannot_open_same_save(self):
        with self.open_store():
            with self.assertRaises(StoreBusyError):
                self.open_store()
        with self.open_store() as store:
            self.assertEqual(store.status()["revision"], 0)


if __name__ == "__main__":
    unittest.main()
