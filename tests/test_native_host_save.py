import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_host_save
from tools.silverfish.campaign import CampaignFormatError, CampaignStore
from tools.silverfish.service import Host
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


class PairedCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = object.__new__(Host)
        self.host.native_profile = root / "game-user"
        self.host.pending_host_save = None
        self.host.pending_guest_saves = {}
        self.host.party = {}
        native_host_save.directory(self.host.native_profile).mkdir(parents=True)
        self.host.allowed_maps = {CROSSROADS}
        self.host.last_game = {"state": "hosting", "map": CROSSROADS,
                               "pawns": {"local": {"name": "host-pawn"}}, "native_paired_save_capture": True}
        self.host.guest_capture_targets = Mock(return_value={"first": "first-pawn", "second": "second-pawn"})
        self.host.commit_checkpoint = AsyncMock(return_value=19)
        self.host.control = Mock()
        self.host.store = SimpleNamespace(path=root / "campaign.json", previous_path=root / "previous.json")
        self.missing_proof = self.missing_file = False
        self.bridge = SimpleNamespace(command=AsyncMock(side_effect=self.capture))

    async def capture(self, action, host_slot, dig_token, first, first_slot, second, second_slot, *, timeout):
        typed = self.host.pending_host_save or self.host.pending_guest_saves
        expected = "native-host-save-capture-typed" if typed else "native-host-save-capture"
        self.assertEqual(action, expected)
        self.assertIn(dig_token, ("-", "dig-token"))
        self.assertEqual((first, second), ("first-pawn", "second-pawn"))
        self.assertEqual(len({host_slot, first_slot, second_slot}), 3)

        def frame(pawn, slot):
            if not self.missing_file or pawn != second:
                native_host_save.slot_path(self.host.native_profile, slot).write_bytes(b"GVAS" + bytes(32))
            return {"state": "captured", "map": CROSSROADS, "pawn": pawn, "slot": slot}

        members = {first: frame(first, first_slot), second: frame(second, second_slot)}
        return {**self.host.last_game, "native_host_save": frame("host-pawn", host_slot) | {"eather_remainder": .25},
                "native_guest_save_member_frames": members if not self.missing_proof else {first: members[first]}}

    async def test_checkpoints_exact_members_in_one_bridge_command(self):
        result = await self.host.checkpoint_native(self.bridge, require_guest_frames=frozenset({"second"}), dig_token="dig-token")
        self.assertEqual(result, 19)
        self.bridge.command.assert_awaited_once()
        self.assertEqual(self.bridge.command.await_args.args[2], "dig-token")
        recorded = self.host.commit_checkpoint.await_args.kwargs
        self.assertEqual(recorded["native_record"]["eather_remainder"], .25)
        self.assertEqual(recorded["require_guest_frames"], {"second"})
        self.assertEqual({key: frame["character_id"] for key, frame in recorded["guest_records"].items()},
                         {"first": "first", "second": "second"})

    async def test_partial_capture_never_publishes_campaign(self):
        self.missing_proof = True
        with self.assertRaisesRegex(RuntimeError, "exact detached guest save"):
            await self.host.checkpoint_native(self.bridge)
        self.host.commit_checkpoint.assert_not_awaited()

    async def test_ordinary_party_update_cannot_save_a_half_persisted_pickup(self):
        self.host.pending_host_save = True
        self.host.store.revision = 18
        self.host.store.checkpoint = Mock()
        candidate = {"queued": True}
        self.assertEqual(self.host.checkpoint(candidate), 18)
        self.host.store.checkpoint.assert_not_called()
        self.host.commit_checkpoint.side_effect = OSError("failed write")
        with self.assertRaisesRegex(OSError, "failed write"):
            await self.host.checkpoint_native(self.bridge, candidate)
        self.assertTrue(self.host.pending_host_save)
        self.host.commit_checkpoint.side_effect = None
        self.assertEqual(await self.host.checkpoint_native(self.bridge, candidate), 19)
        self.assertIsNone(self.host.pending_host_save)
        self.assertIs(self.host.commit_checkpoint.await_args.args[0], candidate)

    async def test_missing_generation_never_publishes_campaign(self):
        self.missing_file = True
        with self.assertRaisesRegex(CampaignFormatError, "Cannot read the isolated Unreal save"):
            await self.host.checkpoint_native(self.bridge)
        self.host.commit_checkpoint.assert_not_awaited()

    async def test_explicit_save_captures_a_retained_inventory_after_its_lease_expires(self):
        guest = "departed-character"
        pawn = "departed-pawn"
        self.host.pending_guest_saves[guest] = (pawn, {"map": CROSSROADS, "location": [1, 2, 3], "health": 20})
        self.host.store.status = Mock(return_value={"leases": []})
        self.host.guest_lease_ids = Mock(return_value={})

        async def command(action, *arguments, **options):
            if action == "native-guest-save-release-retained":
                self.host.commit_checkpoint.assert_awaited_once()
                self.assertEqual(arguments[0], guest)
                return self.host.last_game
            if action == "native-guest-save-capture-retained":
                character, slot = arguments
                self.assertEqual(character, guest)
                native_host_save.slot_path(self.host.native_profile, slot).write_bytes(b"GVAS" + bytes(32))
                proof = {"map": CROSSROADS, "pawn": pawn, "slot": slot, "health": 19}
                return {**self.host.last_game, "native_guest_save_retained_frames": {guest: proof}}
            return await self.capture(action, *arguments, **options)

        self.bridge.command.side_effect = command
        self.assertEqual(await self.host.checkpoint_native(self.bridge), 19)
        recorded = self.host.commit_checkpoint.await_args.kwargs
        self.assertEqual(recorded["require_guest_frames"], {guest})
        state = recorded["unleased_states"][guest]
        self.assertEqual((state["health"], state["native_guest_save"]["character_id"]), (19, guest))
        self.assertFalse(self.host.pending_guest_saves)

    async def test_checkpoint_pruning_preserves_both_revisions_and_failure_is_deferred(self):
        profile = self.host.native_profile

        def generation():
            slot = native_host_save.new_slot()
            path = native_host_save.slot_path(profile, slot)
            path.write_bytes(b"GVAS" + bytes(32))
            return path, native_host_save.record(profile, slot, CROSSROADS)

        prior_path, prior = generation()
        next_path, next_frame = generation()
        orphan, _ = generation()
        store = self.enterContext(CampaignStore(self.host.store.path.parent / "campaign"))
        store.checkpoint({"native_host_save": prior}, {})
        self.host.store = store
        self.host.checkpoint_contents = Mock(return_value=({"native_host_save": next_frame}, {}))
        self.assertEqual(await Host.commit_checkpoint(self.host, None, native_record=next_frame), 2)
        self.assertTrue(prior_path.is_file())
        self.assertTrue(next_path.is_file())
        self.assertFalse(orphan.exists())

        with patch.object(native_host_save, "prune", side_effect=PermissionError("busy")):
            self.assertEqual(await Host.commit_checkpoint(self.host, None, native_record=next_frame), 3)
        self.assertEqual(store.status()["revision"], 3)
        self.host.control.event.assert_called_once_with("native_host_save_prune_deferred", reason="busy")
