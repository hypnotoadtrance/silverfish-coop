import unittest

from tools.silverfish import native_blue, native_gold, party
from tools.silverfish.service import pending_cube
from tools.silverfish.travel import BOROUGH, CROSSROADS, DEAD_BRIDGE, OBSERVATORY, PLAYTEST_MAPS


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

    def test_exact_stock_bed_ids_cover_normal_routes_only(self):
        self.assertEqual(len(native_blue.BED_IDS), 11)
        for destination, bed in native_blue.BED_IDS.items():
            level = destination.rsplit("/", 1)[-1]
            with self.subTest(level=level):
                self.assertEqual(native_blue.saved_rest(level, bed), {"destination": destination, "bed": bed})
                self.assertEqual(native_blue.saved_rest(destination, bed), {"destination": destination, "bed": bed})
                self.assertTrue(native_blue.valid_rest(CROSSROADS, destination, bed))
                self.assertFalse(native_blue.valid_rest(CROSSROADS, destination, bed[:-1]))
                self.assertIsNone(native_blue.saved_rest(level.lower(), bed))
        for test_map in PLAYTEST_MAPS:
            level = test_map.rsplit("/", 1)[-1]
            bed = f"SavePoint_BP_C_1-{level}-"
            self.assertIsNone(native_blue.saved_rest(test_map, bed))
            self.assertFalse(native_blue.valid_rest(test_map, BOROUGH, native_blue.BED_IDS[BOROUGH]))
        self.assertIsNone(native_blue.saved_rest("Slums_P", "BP_Prop_Bed_01_C_1-Slums_P-"))
        self.assertIsNone(native_blue.saved_rest("Borough", native_blue.BED_IDS[DEAD_BRIDGE]))

    def test_expanded_source_and_exact_rest_survive_pending_blue_debit(self):
        source = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"
        guid = "A1" * 16
        bed = native_blue.BED_IDS[BOROUGH]
        charged = native_blue.begin(party.new_party(), guid, source, BOROUGH, bed)
        self.assertEqual(native_blue.ledger(charged)["pending"], {
            "guid": guid, "source": source, "destination": BOROUGH, "bed": bed,
        })
        restored = native_blue.arrive(charged, guid, BOROUGH)
        self.assertEqual(native_blue.ledger(restored)["spent"], {guid: "arrived"})
