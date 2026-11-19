import unittest
from unittest.mock import patch

from tools.silverfish import native_green, party
from tools.silverfish.travel import BLACK_MARKET, BOROUGH, CROSSROADS, DEAD_BRIDGE, FOREST, OBSERVATORY, SCIENTIST_SHOP


class GreenCubeTests(unittest.TestCase):
    def test_every_stock_candidate_is_preserved_by_relationship(self):
        self.assertEqual(native_green.candidates({2: 0, 8: 1, 4: 2, 5: 3}),
                         (*native_green.ALWAYS, BOROUGH, BLACK_MARKET, OBSERVATORY, SCIENTIST_SHOP, DEAD_BRIDGE))
        self.assertEqual(native_green.candidates({2: 3, 8: 4, 4: 5, 5: 2}), (*native_green.ALWAYS, FOREST))
        self.assertEqual(native_green.candidates({2: 3, 8: 3, 4: 3, 5: 3}), native_green.ALWAYS)
        with self.assertRaisesRegex(ValueError, "exact stock faction relationship"):
            native_green.candidates({2: 3, 8: 4, 4: 5})

    def test_draw_is_committed_to_the_exact_green_cube(self):
        eligible = native_green.candidates({2: 1, 8: 3, 4: 3, 5: 3})
        with patch("tools.silverfish.native_green.choice", return_value=SCIENTIST_SHOP) as choose:
            destination = native_green.draw(eligible)
        choose.assert_called_once_with(eligible)
        guid = "BB" * 16
        charged = native_green.begin(party.new_party(), guid, CROSSROADS, destination)
        self.assertEqual(native_green.ledger(charged)["pending"]["destination"], SCIENTIST_SHOP)
        restored = native_green.arrive(charged, guid, SCIENTIST_SHOP)
        self.assertEqual(native_green.ledger(restored), {"pending": None, "spent": {guid: "arrived"}})
