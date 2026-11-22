import unittest

from tools.silverfish import native_flare
from tools.silverfish.travel import OBSERVATORY


class StockFlareReceiptTests(unittest.TestCase):
    def test_each_stock_class_verifies_only_its_own_projectile(self):
        for item, projectile in native_flare.CLASSES.items():
            with self.subTest(item=item):
                path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.{projectile}_25"
                row = [native_flare.ACTION, OBSERVATORY, "guest", "A" * 32, "0", "2", "1", item, path]
                proof = native_flare.parse(row, "hosting", OBSERVATORY)
                self.assertEqual((proof["before"], proof["after"], proof["projectile"]), (2, 1, path))
                row[-1] = path.replace(projectile, "Unrecognized_Projectile_C")
                with self.assertRaises(ValueError):
                    native_flare.parse(row, "hosting", OBSERVATORY)

    def test_receipt_requires_exactly_one_consumed_unit_and_current_map(self):
        item, projectile = next(iter(native_flare.CLASSES.items()))
        path = f"{OBSERVATORY}.Observitory_P:PersistentLevel.{projectile}_25"
        row = [native_flare.ACTION, OBSERVATORY, "guest", "B" * 32, "0", "2", "0", item, path]
        with self.assertRaises(ValueError):
            native_flare.parse(row, "hosting", OBSERVATORY)
        row[6] = "1"
        with self.assertRaises(ValueError):
            native_flare.parse(row, "hosting", "/Game/AnotherMap")
