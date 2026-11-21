import asyncio
from pathlib import Path
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

from tools.silverfish import embedded_startup, native_host_save
from tools.silverfish.campaign import CampaignFormatError, CampaignStore
from tools.silverfish.title_handoff import TitleHandoff
from tools.silverfish.title_host_campaigns import FILE, TitleHostCampaigns, campaign_names, saved_campaign
from tools.silverfish.travel import CROSSROADS
from test_title_handoff import ready


class SavedCampaignTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.campaign = self.root / "campaigns" / "Default"
        self.campaign.parent.mkdir()
        self.profile = self.root / "title-profile"
        self.saved = self.profile / "Saved"
        self.saved.mkdir(parents=True)

    def checkpoint(self, name, *, profile=None, scoped=True, native=True, area=CROSSROADS):
        directory = self.campaign.parent / name
        with CampaignStore(directory / "save") as store:
            scope = uuid.UUID(store.status()["campaign_id"]).hex
            world = {"map": area}
            storage = profile or self.profile
            if scoped:
                world[native_host_save.SCOPE_FIELD] = scope
                storage = native_host_save.SaveScope(storage, scope)
            slot = None
            if native:
                native_host_save.directory(storage).mkdir(parents=True, exist_ok=True)
                slot = native_host_save.slot_path(storage, native_host_save.new_slot())
                slot.write_bytes(b"GVAS" + bytes(40))
                world["native_host_save"] = native_host_save.record(storage, slot.stem, area)
            store.save_world(world)
        return directory, slot

    def test_only_current_profile_and_readable_exact_native_checkpoints(self):
        self.checkpoint("Zulu")
        first, _ = self.checkpoint("alpha")
        self.checkpoint("OtherProfile", profile=self.root / "other-profile")
        self.checkpoint("Unscoped", scoped=False)
        self.checkpoint("NoSlot", native=False)
        self.checkpoint("UnqualifiedMap", area="/Game/Classes/Maps/Unknown")
        _, bad_slot = self.checkpoint("BadSlot")
        bad_slot.write_bytes(b"GVAS" + bytes([1]) * 40)
        bad_name, _ = self.checkpoint("_bad")
        objects = first / "save" / "campaign.json", bad_name / "save" / "campaign.json"
        before = [(path.stat().st_mtime_ns, path.read_bytes()) for path in objects]
        self.assertEqual(campaign_names(self.campaign, self.saved), ("alpha", "Zulu"))
        self.assertEqual(saved_campaign(self.campaign, self.saved, "alpha"), (first, CROSSROADS))
        self.assertEqual(before, [(path.stat().st_mtime_ns, path.read_bytes()) for path in objects])

    def test_only_matching_profile_for_campaign_owned_game_user(self):
        own_profile = self.campaign / "game-user"
        self.checkpoint("Default", profile=own_profile)
        self.checkpoint("Sibling", profile=own_profile)
        self.assertEqual(campaign_names(self.campaign, own_profile / "Saved"), ("Default",))
        with self.assertRaisesRegex(ValueError, "different campaign profile"):
            saved_campaign(self.campaign, own_profile / "Saved", "Sibling")

    def test_resolve_v5_requalifies_the_selected_saved_campaign(self):
        directory, slot = self.checkpoint("Selected")
        handoff = TitleHandoff(self.root)
        self.assertTrue(handoff.refresh(ready(), {"host": object()}))
        generation = handoff.generation
        prefix = f"5\t{generation}\t{'d' * 32}\thost\t"
        handoff.request_path.write_text(prefix + "\t\t\tpassword\tresume\n", encoding="ascii")
        self.assertIsNone(handoff.poll(ready(2)))
        self.assertEqual(handoff.last_refusal, "malformed_request")
        handoff.request_path.write_text(prefix + "Selected\t\t\tpassword\tresume\n", encoding="ascii")
        self.assertIsNone(handoff.poll(ready(3)))
        claim = handoff.poll(ready(4))
        self.assertTrue(claim.stock_resume)
        self.assertEqual(claim.host_settings, ("Selected", "", ""))
        self.assertNotIn("password", repr(claim))
        plan = embedded_startup._resident_host_plan(self.root, self.campaign, self.root, self.saved, True, "127.0.0.1", 7777)
        chosen = plan.resolve_claim(claim)
        with self.assertRaises(FileNotFoundError) as missing:
            chosen.read_status()
        self.assertEqual(Path(missing.exception.filename), directory / "run" / "status.json")
        slot.unlink()
        with self.assertRaises(CampaignFormatError):
            plan.resolve_claim(claim)
        self.assertTrue((directory / "save" / "campaign.json").exists())

    def test_catalogue_caps_the_number_of_displayed_campaigns(self):
        for number in range(65):
            (self.campaign.parent / f"Campaign{number:02}").mkdir()
        with patch("tools.silverfish.title_host_campaigns.saved_campaign"):
            names = campaign_names(self.campaign, self.saved)
        self.assertEqual(names, tuple(f"Campaign{number:02}" for number in range(64)))

    def test_require_existing_does_not_create_a_replacement_at_locked_startup(self):
        directory = self.campaign / "save"
        with self.assertRaisesRegex(CampaignFormatError, "checkpoint is missing"):
            CampaignStore(directory, require_existing=True)
        self.assertFalse((directory / "campaign.json").exists())
        with CampaignStore(directory) as store:
            self.assertEqual(store.status()["revision"], 0)


class PublishedCampaignsTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_worker_result_does_not_publish_and_new_generation_refreshes(self):
        bridge = Path(self.enterContext(TemporaryDirectory())).resolve()
        blocked = threading.Event()
        self.addCleanup(blocked.set)
        entered = threading.Event()
        values = iter((("stale",), ("New", "Current")))
        old, current = "1" * 32, "2" * 32

        def scan():
            result = next(values)
            if result == ("stale",):
                entered.set()
                blocked.wait(timeout=2)
            return result

        (bridge / FILE).write_text(f"1\t{old}\t1\nstale\n", encoding="ascii")
        catalogue = TitleHostCampaigns(bridge, scan)
        self.addCleanup(catalogue.close)
        self.assertFalse(catalogue.path.exists())
        catalogue.poll(old)
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.005)
        self.assertTrue(entered.is_set())
        catalogue.poll(current)
        self.assertFalse(catalogue.path.exists())
        blocked.set()
        for _ in range(100):
            catalogue.poll(current)
            if catalogue.path.exists():
                break
            await asyncio.sleep(.005)
        self.assertEqual(catalogue.path.read_bytes(), f"1\t{current}\t2\nNew\nCurrent\n".encode("ascii"))
        self.assertTrue(catalogue.contains(current, "Current"))
        self.assertFalse(catalogue.contains(current, "current"))
        self.assertFalse(catalogue.contains(old, "stale"))
        catalogue.poll(None)
        self.assertFalse(catalogue.path.exists())
