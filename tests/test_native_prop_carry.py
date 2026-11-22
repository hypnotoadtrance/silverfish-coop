import unittest

from tools.silverfish import native_prop_carry as carry


class CorpseCarryPayloadTests(unittest.TestCase):
    def setUp(self):
        self.area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        self.edge = ["native-prop-carry-input", self.area, "1", "guest", "grab",
                     "/Game/Classes/NPCs/NPC_WildBoar.NPC_WildBoar_C", "10", "20", "30", "-40", "10"]

    def test_stock_prop_and_exact_corpse_inputs_share_the_lease_format(self):
        self.assertNotIn("corpse", carry.parse(self.edge, "connected", self.area))
        result = carry.parse([*self.edge, "65535", "leg_f_R"], "connected", self.area)
        self.assertEqual((result["corpse"], result["bone"], result["pose"][0]), (65535, "leg_f_R", 10))
        receipt = [carry.ACTION, self.area, "a" * 32, "2", "drop", "guest", "boar", "65535", "leg_f_R"]
        result = carry.parse(receipt, "hosting", self.area)
        self.assertEqual((result["corpse"], result["bone"], result["edge"]), (65535, "leg_f_R", "drop"))

    def test_corpse_input_requires_a_numbered_marker_and_named_body(self):
        for marker, bone in (("0", "pelvis"), ("65536", "pelvis"), ("1", "None"), ("1", "pelvis@extra")):
            with self.subTest(marker=marker, bone=bone), self.assertRaises(ValueError):
                carry.parse([*self.edge, marker, bone], "connected", self.area)
        self.edge[4] = "drop"
        with self.assertRaises(ValueError):
            carry.parse([*self.edge, "1", "pelvis"], "connected", self.area)

    def test_authored_fuel_drums_accept_the_cooked_instance_name(self):
        actor = f"Prop_FuelDrum_C {self.area}.CrossRoads_P:PersistentLevel.Prop_FeulDrum_C_33"
        self.assertEqual(carry.parse([carry.BARREL_ROW, self.area, actor, "31"], "hosting", self.area)["actor"], actor)
        with self.assertRaises(ValueError):
            carry.parse([carry.BARREL_ROW, self.area, actor.replace("_33", "_2147482112"), "31"], "hosting", self.area)

    def test_placed_grabs_are_bound_to_an_actor_in_the_current_map(self):
        path = f"{self.area}.CrossRoads_P:PersistentLevel.Item_CannedSoup_C_1"
        fields = [*self.edge, path]
        fields[5] = "/Game/Classes/Items/Item_CannedSoup.Item_CannedSoup_C"
        self.assertEqual(carry.parse(fields, "connected", self.area)["placement"], path)
        with self.assertRaises(ValueError):
            carry.parse([*fields[:-1], path.replace("CrossRoads_P", "Observitory_P")], "connected", self.area)
