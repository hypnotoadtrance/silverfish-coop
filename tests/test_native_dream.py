import unittest

from tools.silverfish import native_dream, party
from tools.silverfish.travel import BLACK_OCEAN, DEAD_BRIDGE, OBSERVATORY, PYROCLAST, SLATE, THE_ROOK


class StardustVisitTests(unittest.TestCase):
    def test_committed_stack_visit_timing_and_early_return_are_durable(self):
        original = party.new_party()
        guid = "AB" * 16
        charged = native_dream.begin(original, guid, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")
        self.assertIsNone(native_dream.visit(original))
        trip = native_dream.visit(charged)
        self.assertEqual(trip["phase"], "departing")
        self.assertIsNone(trip["deadline"])
        with self.assertRaisesRegex(ValueError, "visit_in_progress"):
            native_dream.begin(charged, guid, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")

        with self.assertRaisesRegex(ValueError, "commit its effects"):
            native_dream.arrived(charged, 100)
        committed = native_dream.effects_applied(charged)
        with self.assertRaisesRegex(ValueError, "must be pending"):
            native_dream.effects_applied(committed)
        active = native_dream.arrived(committed, 100)
        self.assertFalse(native_dream.return_due(active, 195.99))
        self.assertTrue(native_dream.return_due(active, 196))
        accelerated = native_dream.accelerate(active, 150)
        self.assertEqual(native_dream.visit(active)["deadline"], 193)
        self.assertIs(native_dream.accelerate(accelerated, 151), accelerated)
        self.assertFalse(native_dream.return_due(accelerated, 152.99))
        self.assertTrue(native_dream.return_due(accelerated, 153))

        returning = native_dream.returning(accelerated)
        self.assertTrue(native_dream.return_due(returning, 100))
        returned = native_dream.finished(returning)
        self.assertIsNone(native_dream.visit(returned))
        self.assertEqual(returned["last_stardust_visit"], {
            "id": trip["id"], "guid": guid, "remaining": 1, "return_map": OBSERVATORY,
        })

    def test_stack_recovery_requires_qualified_metadata(self):
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, "", OBSERVATORY, "")

    def test_fruiting_duration_and_route_are_typed(self):
        for flag, destination, expected in (("Tarot_Rook", THE_ROOK, 463), ("Tarot_Obelisk", PYROCLAST, 643)):
            with self.subTest(flag=flag):
                charged = native_dream.begin(party.new_party(), "AB" * 16, 2, "stock-fruiting-default-v1",
                                             OBSERVATORY, "", source=DEAD_BRIDGE, kind="fruiting-stardust", tarot=flag)
                active = native_dream.arrived(native_dream.effects_applied(charged), 100)
                self.assertEqual(native_dream.visit(active)["destination"], destination)
                self.assertEqual(native_dream.visit(active)["deadline"], expected)
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "",
                               kind="fruiting-stardust", tarot="Tarot_Rook")
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "",
                               tarot="Tarot_Nothing")

    def test_stock_level_script_timers_and_fades(self):
        for flag, destination, deadline in (("Tarot_Night", SLATE, 280), ("Tarot_BlackStag", BLACK_OCEAN, 260)):
            with self.subTest(flag=flag):
                committed = native_dream.begin(party.new_party(), "AB" * 16, 1, "", OBSERVATORY, "",
                                               kind="fruiting-stardust", tarot=flag)
                active = native_dream.arrived(native_dream.effects_applied(committed), 100)
                self.assertEqual(native_dream.visit(active)["destination"], destination)
                self.assertEqual(native_dream.visit(active)["deadline"], deadline)
                self.assertFalse(native_dream.return_due(active, deadline + 1.99))
                self.assertTrue(native_dream.return_due(active, deadline + 2))

    def test_old_exchange_departure_can_finish_without_replaying_new_effects(self):
        legacy = native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")
        legacy["stardust_visit"] = {key: legacy["stardust_visit"][key] for key in native_dream.LEGACY_KEYS}
        self.assertEqual(native_dream.visit(legacy)["effects"], "legacy")
        active = native_dream.arrived(legacy, 100)
        self.assertEqual(native_dream.visit(active)["deadline"], 193)
