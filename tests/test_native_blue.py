import unittest

from tools.silverfish import native_blue, native_gold, party
from tools.silverfish.service import pending_cube
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, OBSERVATORY


class BlueCubeTests(unittest.TestCase):
    def test_exact_bed_and_fallback_have_distinct_durable_itineraries(self):
        guid = "A1" * 16
        rest = native_blue.saved_rest("DeadBridge_P", "SavePoint_BP_C_1-DeadBridge_P-")
        self.assertEqual(rest, {"destination": DEAD_BRIDGE, "bed": native_blue.BED_IDS[DEAD_BRIDGE]})
        self.assertIsNone(native_blue.saved_rest("DeadBridge_P", "SavePoint_BP_C_2-DeadBridge_P-"))
        self.assertFalse(native_blue.valid_rest(CROSSROADS, DEAD_BRIDGE, ""))
        self.assertTrue(native_blue.valid_rest(CROSSROADS, OBSERVATORY, ""))

        original = party.new_party()
        charged = native_blue.begin(original, guid, OBSERVATORY, **rest)
        self.assertNotIn("blue_transport", original)
        self.assertEqual(pending_cube(charged), ("blue", {
            "guid": guid, "source": OBSERVATORY, **rest,
        }))
        with self.assertRaisesRegex(ValueError, "two pending"):
            pending_cube(native_gold.begin(charged, "B2" * 16))
        with self.assertRaisesRegex(ValueError, "does not match"):
            native_blue.arrive(charged, guid, OBSERVATORY)
        arrived = native_blue.arrive(charged, guid, DEAD_BRIDGE)
        self.assertIsNone(pending_cube(arrived))
        self.assertEqual(native_blue.ledger(arrived)["spent"], {guid: "arrived"})
