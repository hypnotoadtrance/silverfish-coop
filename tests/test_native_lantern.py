import unittest

from tools.silverfish import native_lantern as lantern


class ObservatoryLanternTests(unittest.TestCase):
    def test_reads_stock_physics_telemetry_from_both_seats(self):
        row = [lantern.TAG, lantern.OBSERVATORY, lantern.ACTOR, "1", "1", "0", "1", "2", "3"]
        for phase in ("hosting", "connected"):
            self.assertEqual(lantern.parse(row, phase, lantern.OBSERVATORY), {
                "map": lantern.OBSERVATORY,
                "actor": lantern.ACTOR,
                "lit": True,
                "held": True,
                "movement": False,
                "position": [1.0, 2.0, 3.0],
            })

    def test_rejects_foreign_actor_and_nonfinite_position(self):
        row = [lantern.TAG, lantern.OBSERVATORY, lantern.ACTOR, "1", "0", "1", "1", "2", "3"]
        for index, value in ((2, "other lamp"), (8, "NaN")):
            invalid = row.copy()
            invalid[index] = value
            with self.assertRaises(ValueError):
                lantern.parse(invalid, "connected", lantern.OBSERVATORY)
