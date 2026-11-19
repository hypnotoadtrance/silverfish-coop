import unittest
from unittest.mock import patch

from tools.silverfish import native_red, party
from tools.silverfish.travel import AUTHORED_EXITS, CROSSROADS, RED_DESTINATIONS, SCI_TUNNEL, CISTERN


class RedCubeTests(unittest.TestCase):
    def test_stock_draw_is_saved_before_arrival(self):
        guid = "AD" * 16
        with patch("tools.silverfish.native_red.choice", return_value=SCI_TUNNEL) as choose:
            destination = native_red.draw()
        choose.assert_called_once_with(RED_DESTINATIONS)
        original = party.new_party()
        charged = native_red.begin(original, guid, CROSSROADS, destination)
        self.assertIsNone(native_red.ledger(original)["pending"])
        self.assertEqual(native_red.ledger(charged)["pending"]["destination"], SCI_TUNNEL)
        with self.assertRaisesRegex(ValueError, "committed draw"):
            native_red.arrive(charged, guid, CISTERN)
        arrived = native_red.arrive(charged, guid, SCI_TUNNEL)
        self.assertEqual(native_red.ledger(arrived), {"pending": None, "spent": {guid: "arrived"}})
        with self.assertRaisesRegex(ValueError, "already has a committed draw"):
            native_red.begin(arrived, guid, CROSSROADS, CISTERN)

    def test_every_stock_destination_has_a_qualified_path_back(self):
        self.assertEqual(len(set(RED_DESTINATIONS)), 9)
        self.assertNotIn((CISTERN, SCI_TUNNEL), AUTHORED_EXITS)
        reachable = {CROSSROADS}
        while True:
            expanded = reachable | {source for source, destination in AUTHORED_EXITS if destination in reachable}
            if expanded == reachable:
                break
            reachable = expanded
        self.assertTrue(set(RED_DESTINATIONS).issubset(reachable))
