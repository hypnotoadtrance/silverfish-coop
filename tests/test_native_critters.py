import unittest

from tools.silverfish import native_critters as critters


class CritterReceiptTests(unittest.TestCase):
    def test_visible_stream_pickup_receipt_keeps_the_campaign_and_exact_actor(self):
        area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        world = f"{area}.CrossRoads_P"
        pawn = f"Player_FP_C {world}:PersistentLevel.Player_FP_C_17"
        name = "Critter_Frog_BP_C "
        source = f"{name}/CommunityMaps/CoopMarsh_1.Marsh:PersistentLevel.Critter_Frog_BP_C_1"
        item = "/Game/Classes/Items/Item_LiveFrog.Item_LiveFrog_C"
        fields = [critters.ROW, area, "7", pawn, f"stream:{source}", item, "A" * 32,
                  "claimed", "-", "0", "0", "0"]
        result = critters.parse(fields, "hosting", area)
        self.assertEqual((result["map"], result["actor"]), (area, f"stream:{source}"))

        local = f"{name}{world}:PersistentLevel.Critter_Frog_BP_C_1"
        fields[4] = local
        self.assertEqual(critters.parse(fields, "hosting", area)["actor"], local)
        for invalid in (source, f"stream:{local}", f"stream:{source.replace('/CommunityMaps/', '/Game/../')}"):
            with self.subTest(actor=invalid), self.assertRaises(ValueError):
                fields[4] = invalid
                critters.parse(fields, "hosting", area)
