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

    def test_stock_interact_waits_for_an_exact_typed_equipped_guid(self):
        fields = self.row()
        fields[3:5] = ["acquiring", "-"]
        fields[6:15] = ["0", "-", "-", "-", "-", "-", "-", "-", "-"]
        pending = dig.parse(fields, "hosting", CROSSROADS)
        self.assertEqual((pending["state"], pending["guid"], pending["stock_f"]), ("acquiring", None, 6))
        fields[4] = "A" * 32
        with self.assertRaises(ValueError):
            dig.parse(fields, "hosting", CROSSROADS)
        fields[3:5] = ["failed", "-"]
        fields[16] = "mutation_unverified_stock_host_dig_stock_spade_equip_readback:pickup=retired,rows=0"
        failed = dig.parse(fields, "hosting", CROSSROADS)
        self.assertEqual((failed["state"], failed["guid"], failed["fault"]), ("failed", None, fields[16]))

    def test_placed_spade_retirement_needs_the_mode_specific_native_flag(self):
        fields = self.row()
        fields[3] = "equipped"
        fields[6:15] = ["0", "-", "-", "-", "-", "-", "-", "-", "-"]
        retired = dig.parse(fields + ["destroyed", "2", "1"], "hosting", CROSSROADS)
        self.assertEqual(retired["spade_source"], {"fate": "destroyed", "mode": 2, "flag": dig.SPADE_FLAG, "flag_value": 1})
        self.assertIsNone(dig.parse(fields + ["missing", "0", "-"], "hosting", CROSSROADS)["spade_source"]["flag_value"])
        for fate, mode, present in (("live", "2", "1"), ("dormant", "2", "-"), ("destroyed", "0", "1")):
            with self.subTest(fate=fate, mode=mode, present=present), self.assertRaises(ValueError):
                dig.parse(fields + [fate, mode, present], "hosting", CROSSROADS)
