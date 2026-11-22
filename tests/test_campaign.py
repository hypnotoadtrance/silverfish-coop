import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import uuid

from tools.silverfish import native_host_save
from tools.silverfish.campaign import (
    AuthenticationError,
    CampaignFormatError,
    CampaignStore,
    CampaignVersionError,
    CharacterError,
    LeaseError,
    StoreBusyError,
)
from tools.silverfish.travel import CROSSROADS


class CampaignStoreTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.directory = self.root / "Coop" / "Campaign"
        self.now = 1000.0

    def open_store(self, **validators):
        return CampaignStore(self.directory, clock=lambda: self.now, **validators)

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

    def test_wipe_can_checkpoint_an_unleased_character_without_overwriting_a_live_lease(self):
        with self.open_store() as store:
            active_identity, active = self.create_guest(store, "Active")
            offline_identity, offline = self.create_guest(store, "Offline")
            active_id, offline_id = active["character_id"], offline["character_id"]
            lease = store.activate(character_id=active_id, **active_identity)
            revision = store.checkpoint({"wipe_committed": True}, {lease["lease_id"]: {"health": 100}},
                                        unleased_states={offline_id: {"health": 0, "inventory": []}})
            with self.assertRaises(LeaseError):
                store.checkpoint({}, {}, unleased_states={active_id: {"health": 0}})
            self.assertEqual(store.status()["revision"], revision)
            self.assertEqual(store.status()["world"], {"wipe_committed": True})
            self.assertEqual(store.list_characters(**offline_identity)[0]["state"], {"health": 0, "inventory": []})

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

    def test_missing_native_escrow_cannot_replace_committed_generations(self):
        profile = self.root / "game-user"
        native_host_save.directory(profile).mkdir(parents=True)
        slot = native_host_save.new_slot()
        native_host_save.slot_path(profile, slot).write_bytes(b"GVAS" + bytes(64))
        frame = native_host_save.record(profile, slot, CROSSROADS)
        world = {"map": CROSSROADS, "native_host_save": frame, "party": {}}

        def verify(checkpoint):
            native_host_save.verify_checkpoint(profile, checkpoint)

        with self.open_store(verify_checkpoint=verify) as store:
            store.save_world(dict(world, day=1))
            revision = store.save_world(dict(world, day=2))
            primary = store.path.read_bytes()
            previous = store.previous_path.read_bytes()
            escrow = dict(frame, slot=native_host_save.new_slot())
            invalid_party = {"native_loot_rolls": {"crate": {"escrow": escrow}}}
            with self.assertRaisesRegex(CampaignFormatError, "Cannot read the isolated Unreal save generation"):
                store.save_world(dict(world, day=3, party=invalid_party))
            self.assertEqual(store.path.read_bytes(), primary)
            self.assertEqual(store.previous_path.read_bytes(), previous)
            self.assertEqual(store.status()["revision"], revision)
            store.issue_invitation("Scout", "Wanderer")

        with self.open_store(verify_checkpoint=verify) as store:
            self.assertEqual(store.status()["world"], dict(world, day=2))
            self.assertEqual(store.status()["revision"], revision + 1)
            self.assertFalse(store.recovered)

    def test_scoped_checkpoint_reads_only_its_campaign_generation(self):
        profile = self.root / "game-user"
        native_host_save.directory(profile).mkdir(parents=True)
        slot = native_host_save.new_slot()
        legacy = native_host_save.slot_path(profile, slot)
        legacy.write_bytes(b"GVAS" + bytes(64))
        legacy_frame = native_host_save.record(profile, slot, CROSSROADS)
        with self.open_store(verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data)) as store:
            store.save_world({"map": CROSSROADS, "native_host_save": legacy_frame})
            scope = native_host_save.SaveScope(profile, uuid.UUID(store.status()["campaign_id"]).hex)
            native_host_save.directory(scope).mkdir(parents=True)
            native_host_save.slot_path(scope, slot).write_bytes(b"GVAS" + bytes(range(64)))
            world = {"map": CROSSROADS, native_host_save.SCOPE_FIELD: scope.id,
                     "native_host_save": native_host_save.record(scope, slot, CROSSROADS)}
            store.save_world(world)
            self.assertEqual(native_host_save.record(profile, slot, CROSSROADS), legacy_frame)
            with self.assertRaisesRegex(CampaignFormatError, "does not match"):
                store.save_world(dict(world, native_host_save=legacy_frame))
            with self.assertRaisesRegex(CampaignFormatError, "does not belong"):
                store.save_world(dict(world, **{native_host_save.SCOPE_FIELD: uuid.uuid4().hex}))
        with self.open_store(verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data)) as store:
            self.assertEqual(store.status()["world"], world)

    def test_checks_world_and_character_state_but_skips_verifiers_for_identity_only_writes(self):
        verify_world = Mock()
        verify_checkpoint = Mock()
        with self.open_store(verify_world=verify_world, verify_checkpoint=verify_checkpoint) as store:
            store.save_world({"day": 1})
            verify_world.assert_called_once_with({"day": 1})
            verify_checkpoint.assert_called_once()

            identity = store.issue_guest("Scout")
            store.issue_invitation("Medic", "Wanderer")
            store.create_linked_character(identity["guest_id"], "Character", "a" * 32)
            store.create_character(name="Empty", **identity)
            self.assertEqual(verify_checkpoint.call_count, 1)
            self.assertEqual(verify_world.call_count, 1)

            character = store.create_character(name="With state", state={"health": 37}, **identity)
            self.assertEqual(verify_checkpoint.call_count, 2)
            self.assertEqual(verify_checkpoint.call_args.args[0]["characters"][character["character_id"]]["state"],
                             {"health": 37})

    def test_linked_character_creation_appearance_survives_checkpoint_and_retry(self):
        appearance = "-|-|-|-|-|-|0.12500,0.50000,0.25000,1.00000|-|-|3"
        with self.open_store() as store:
            identity = store.issue_guest("Scout")
            guest_id = identity["guest_id"]
            operation = "7" * 32
            created = store.create_linked_character(guest_id, "Scout", operation, appearance=appearance)
            lease = store.activate(character_id=created["character_id"], **identity)
            store.save_character(lease["lease_id"], {"health": 37})
            self.assertEqual(store.create_linked_character(guest_id, "Scout", operation, appearance=appearance), created)
            with self.assertRaises(CharacterError):
                store.create_linked_character(guest_id, "Scout", operation, appearance=appearance[:-1] + "4")
            with self.assertRaises(CharacterError):
                store.create_linked_character(guest_id, "Scout", "8" * 32, appearance={"item": "invalid"})
        with self.open_store() as store:
            character, = store.list_characters(**identity)
            self.assertEqual(character["state"], {"health": 37, "creator_appearance": appearance})

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


class AsyncCampaignCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.store = self.enterContext(CampaignStore(Path(scratch) / "campaign"))
        self.entered = threading.Event()
        self.resume = threading.Event()

        real_write = self.store._write

        def pause_first_primary(path, data):
            if path == self.store.path and data["revision"] == 1:
                self.entered.set()
                if not self.resume.wait(5):
                    raise TimeoutError("test did not release the campaign writer")
            real_write(path, data)

        self.enterContext(patch.object(self.store, "_write", side_effect=pause_first_primary))

    async def test_reads_continue_until_an_ordered_checkpoint_publishes(self):
        first = asyncio.create_task(self.store.checkpoint_async({"visit": 1}, {}))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
            second = asyncio.create_task(self.store.checkpoint_async({"visit": 2}, {}))
            loop_pulse = asyncio.Event()
            asyncio.get_running_loop().call_soon(loop_pulse.set)
            await asyncio.wait_for(loop_pulse.wait(), timeout=1)
            status = self.store.status()
            self.assertEqual((status["revision"], status["world"]), (0, {}))
            self.assertFalse(first.done())
            self.assertFalse(second.done())
        finally:
            self.resume.set()
        self.assertEqual(await first, 1)
        self.assertEqual(await second, 2)
        self.assertEqual(self.store.status()["world"], {"visit": 2})
        self.assertEqual(json.loads(self.store.previous_path.read_bytes())["world"], {"visit": 1})

    async def test_cancellation_drains_commit_before_allowing_another_write(self):
        first = asyncio.create_task(self.store.checkpoint_async({"visit": 1}, {}))
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 3))
            first.cancel()
            waiting = asyncio.create_task(self.store.wait_for_checkpoint())
            await asyncio.sleep(0)
            self.assertFalse(first.done())
            self.assertFalse(waiting.done())
        finally:
            self.resume.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await waiting
        self.assertEqual(self.store.status()["world"], {"visit": 1})
        self.assertEqual(await self.store.checkpoint_async({"visit": 2}, {}), 2)


if __name__ == "__main__":
    unittest.main()
