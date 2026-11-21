import asyncio
from datetime import datetime
import os
from pathlib import Path
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

from tools.silverfish import embedded_startup, native_host_save
from tools.silverfish.campaign import CampaignFormatError, CampaignStore
from tools.silverfish.title_handoff import Claim, TitleHandoff
from tools.silverfish.title_host_campaigns import FILE, CampaignEntry, TitleHostCampaigns, campaign_entries, saved_campaign
from tools.silverfish.travel import CROSSROADS, OBSERVATORY
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
        _, latest = self.checkpoint("Zulu", area=OBSERVATORY)
        first, older = self.checkpoint("alpha")
        old_time, new_time = 1_700_000_000, 1_701_000_000
        os.utime(older, (old_time, old_time))
        os.utime(latest, (new_time, new_time))
        self.checkpoint("OtherProfile", profile=self.root / "other-profile")
        self.checkpoint("Unscoped", scoped=False)
        self.checkpoint("NoSlot", native=False)
        self.checkpoint("UnqualifiedMap", area="/Game/Classes/Maps/Unknown")
        _, bad_slot = self.checkpoint("BadSlot")
        bad_slot.write_bytes(b"GVAS" + bytes([1]) * 40)
        bad_name, _ = self.checkpoint("_bad")
        objects = first / "save" / "campaign.json", bad_name / "save" / "campaign.json"
        before = [(path.stat().st_mtime_ns, path.read_bytes()) for path in objects]
        entries = campaign_entries(self.campaign, self.saved)
        self.assertEqual([(entry.name, entry.map_label) for entry in entries],
                         [("Zulu", "Observatory"), ("alpha", "Crossroads")])
        self.assertEqual([entry.saved_at for entry in entries], [
            datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S") for stamp in (new_time, old_time)
        ])
        self.assertEqual(saved_campaign(self.campaign, self.saved, "alpha"), (first, CROSSROADS))
        self.assertEqual(before, [(path.stat().st_mtime_ns, path.read_bytes()) for path in objects])

    def test_only_matching_profile_for_campaign_owned_game_user(self):
        own_profile = self.campaign / "game-user"
        self.checkpoint("Default", profile=own_profile)
        self.checkpoint("Sibling", profile=own_profile)
        self.assertEqual([entry.name for entry in campaign_entries(self.campaign, own_profile / "Saved")],
                         ["Default"])
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
        def entry(_campaign, _saved, name):
            return CampaignEntry(name, "Crossroads", "2025-01-01 00:00:00", int(name.removeprefix("Campaign")))

        with patch("tools.silverfish.title_host_campaigns._entry", side_effect=entry):
            entries = campaign_entries(self.campaign, self.saved)
        self.assertEqual([entry.name for entry in entries], [f"Campaign{number:02}" for number in range(64, 0, -1)])

    def test_unified_blank_creator_reserves_unique_games_and_keeps_explicit_names(self):
        directory, _ = self.checkpoint("Default")
        existing = directory / "save" / "campaign.json"
        before = existing.read_bytes()
        handoff = TitleHandoff(self.root)
        self.assertTrue(handoff.refresh(ready(), {"host": object()}))
        generation = handoff.generation
        request_id = "e" * 32
        handoff.request_path.write_bytes(f"4\t{generation}\t{request_id}\thost\t\t\t\t\tcreator\n".encode("ascii"))
        self.assertIsNone(handoff.poll(ready(2)))
        claim = handoff.poll(ready(3))
        self.assertTrue(claim.stock_creator)
        self.assertEqual(claim.host_settings, ("", "", ""))

        plan = embedded_startup._resident_host_plan(
            self.root, self.campaign, self.root, self.saved, True, "127.0.0.1", 7777, automatic_campaigns=True,
        )

        def selected_path(choice):
            with self.assertRaises(FileNotFoundError) as missing:
                plan.resolve_claim(choice).read_status()
            return Path(missing.exception.filename).parents[1]

        first, second = selected_path(claim), selected_path(claim)
        for selected in (first, second):
            self.assertRegex(selected.name, r"^campaign-[0-9a-f]{32}$")
            self.assertEqual(selected.parent, directory.parent)
            self.assertTrue(selected.is_dir())
        self.assertNotEqual(first, second)
        named = Claim("host", request_id, generation, object(), host_settings=("legacy-fixture", "", ""),
                      stock_creator=True)
        self.assertEqual(selected_path(named), directory.parent / "legacy-fixture")
        self.assertEqual(existing.read_bytes(), before)
        self.assertEqual([entry.name for entry in campaign_entries(self.campaign, self.saved)], ["Default"])

    def test_reservation_retries_a_collision_without_touching_the_existing_run(self):
        collision, free = uuid.UUID(int=1), uuid.UUID(int=2)
        existing = self.campaign.parent / f"campaign-{collision.hex}"
        existing.mkdir()
        preserved = existing / "sentinel"
        preserved.write_bytes(b"existing")
        with patch.object(embedded_startup.uuid, "uuid4", side_effect=[collision, free]):
            selected = embedded_startup._reserve_campaign(self.root, self.campaign.parent)
        self.assertEqual(selected, self.campaign.parent / f"campaign-{free.hex}")
        self.assertTrue(selected.is_dir())
        self.assertEqual(preserved.read_bytes(), b"existing")

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
        values = iter(((CampaignEntry("stale", "Observatory", "2025-01-01 00:00:00", 1),), (
            CampaignEntry("New", "Crossroads", "2025-01-01 00:04:00", 4),
            CampaignEntry("Current", "Dead Bridge", "2025-01-01 00:03:00", 3),
        )))
        old, current = "1" * 32, "2" * 32

        def scan():
            result = next(values)
            if result[0].name == "stale":
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
        self.assertEqual(catalogue.path.read_bytes(), (
            f"2\t{current}\t2\nNew\tCrossroads\t2025-01-01 00:04:00\n"
            "Current\tDead Bridge\t2025-01-01 00:03:00\n"
        ).encode("ascii"))
        self.assertTrue(catalogue.contains(current, "Current"))
        self.assertFalse(catalogue.contains(current, "current"))
        self.assertFalse(catalogue.contains(old, "stale"))
        catalogue.poll(None)
        self.assertFalse(catalogue.path.exists())
