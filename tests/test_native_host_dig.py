import unittest

from tools.silverfish import native_host_dig as dig
from tools.silverfish.travel import CROSSROADS


class StockHostDigTests(unittest.TestCase):
    def row(self):
        x, y, z = map(str, dig.SITE)
        return [dig.TAG, CROSSROADS, f"Player_FP_C {dig.WORLD}:PersistentLevel.Player_FP_C_91",
                "collected", "A" * 32, dig.FLAG, "1", f"Package_Item_FarmHouse_C {dig.WORLD}:PersistentLevel.Package_Item_FarmHouse_C_3",
                "B" * 32, x, y, z, "PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/PM_Dirt.PM_Dirt",
                "1", "0", "6", "-"]

    def test_one_exact_package_and_authoritative_hole_with_or_without_random_drop(self):
        fields = self.row()
        report = dig.parse(fields, "hosting", CROSSROADS)
        self.assertEqual((report["source"], report["package_guid"], report["holes"], report["incidental"]),
                         (dig.SOURCE, "B" * 32, 1, 0))
        fields[14] = "1"
        self.assertEqual(dig.parse(fields, "hosting", CROSSROADS)["incidental"], 1)

    def test_refuses_forged_actor_flag_plane_or_stateless_projection(self):
        for field, changed in ((5, "BurriedItem_BP_C_1-Beach_01_P-"), (7, "-"), (10, "90000"),
                               (11, "nan"), (12, "PM_Dirt"), (13, "2"), (14, "2"), (15, "0")):
            row = self.row()
            row[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                dig.parse(row, "hosting", CROSSROADS)
        with self.assertRaises(ValueError):
            dig.parse(self.row(), "connected", CROSSROADS)
