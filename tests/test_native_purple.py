import unittest
from unittest.mock import patch

from tools.silverfish import native_purple, party
from tools.silverfish.travel import AUTHORED_EXITS, CROSSROADS, REFINERY


class PurpleCubeTests(unittest.TestCase):
    def test_every_cooked_level_is_available_to_the_uniform_draw(self):
        self.assertEqual(len(set(native_purple.DESTINATIONS)), 15)
        self.assertEqual(native_purple.ARRIVAL, "Bed")
        self.assertIn(CROSSROADS, native_purple.DESTINATIONS)
        with patch.object(native_purple, "choice", return_value=REFINERY) as choose:
            self.assertEqual(native_purple.draw(), REFINERY)
        choose.assert_called_once_with(native_purple.DESTINATIONS)

    def test_same_map_draw_is_durable_and_cannot_be_reused(self):
        guid = "DC" * 16
        original = party.new_party()
        committed = native_purple.begin(original, guid, CROSSROADS, CROSSROADS)
        self.assertEqual(native_purple.ledger(original), {"pending": None, "spent": {}})
        self.assertEqual(native_purple.ledger(committed), {
            "pending": {"guid": guid, "source": CROSSROADS, "destination": CROSSROADS},
            "spent": {guid: "committed"},
        })
        with self.assertRaisesRegex(ValueError, "committed draw"):
            native_purple.arrive(committed, guid, REFINERY)
        with self.assertRaisesRegex(ValueError, "committed draw"):
            native_purple.arrive(committed, "AB" * 16, CROSSROADS)
        settled = native_purple.arrive(committed, guid, CROSSROADS)
        self.assertEqual(native_purple.ledger(settled), {"pending": None, "spent": {guid: "arrived"}})
        with self.assertRaisesRegex(ValueError, "already has a committed draw"):
            native_purple.begin(settled, guid, CROSSROADS, REFINERY)

    def test_every_destination_has_an_authored_path_back(self):
        reachable = {CROSSROADS}
        while True:
            expanded = reachable | {source for source, destination in AUTHORED_EXITS if destination in reachable}
            if expanded == reachable:
                break
            reachable = expanded
        self.assertTrue(set(native_purple.DESTINATIONS).issubset(reachable))
