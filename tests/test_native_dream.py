import unittest

from tools.silverfish import native_dream, party
from tools.silverfish.travel import OBSERVATORY


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

        active = native_dream.arrived(charged, 100)
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
