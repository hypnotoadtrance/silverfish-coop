from collections import Counter
import unittest

from tools.silverfish.authored_container_placements import TWO_SLOT_PLACEMENTS
from tools.silverfish.authored_loot import (CRATES, CRATE_PROFILES, GUARANTEED_PLACEMENTS, OFFICE_DESK,
                                           OVERRIDDEN_PLACEMENTS, PLACEMENTS, SMALL_CABINET, full_name)
from tools.silverfish.generate_authored_loot_registry import outputs, survey
from tools.silverfish.travel import OBSERVATORY


DOCKS = "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"
FISH = "DataTableRowHandle(FishCannery)"
PERMANENT = "RespawnSetting_Enum::NewEnumerator2"


class GuaranteedCrateProfilesTests(unittest.TestCase):
    def test_append_exact_settings_without_changing_prior_native_indices(self):
        existing = tuple((*placement, None) for placement in PLACEMENTS) + OVERRIDDEN_PLACEMENTS
        self.assertEqual(len(existing), 342)
        self.assertEqual(CRATE_PROFILES[:342], existing)
        additional = {full_name(area, actor, family) for family, area, actor, *_ in TWO_SLOT_PLACEMENTS}
        self.assertEqual(set(CRATES) - {full_name(*row[:2]) for row in CRATE_PROFILES},
                         {SMALL_CABINET, OFFICE_DESK} | additional)
        self.assertEqual(Counter(row[4] for row in GUARANTEED_PLACEMENTS), {None: 2, 2: 34})
        fish = [row for row in GUARANTEED_PLACEMENTS if row[0] == DOCKS]
        self.assertEqual(len(fish), 14)
        self.assertEqual({row[2:] for row in fish}, {(-421338496, "FishCannery", 2)})
        self.assertEqual(len({full_name(*row[:2]) for row in fish}), 14)
        self.assertIn((OBSERVATORY, "LootCrate_Small_BP_C_1", -616840064, None, 2), GUARANTEED_PLACEMENTS)
        rendered = outputs()
        native = rendered["native/authored_loot_registry.hpp"]
        lua = rendered["mod/SilverfishCoop/Scripts/authored_loot_registry.lua"]
        self.assertIn('LootCrate_Small_BP_C_1"), -616840064, nullptr, true, 2}', native)
        self.assertIn('LootCrate_Small_BP_C_1", salt = -616840064, guarantee_spawn = true, replenish_mode = 2', lua)
        self.assertIn('LootCrate_Small_BP_C_1", salt = -1175450944, guarantee_spawn = true },', lua)

    def test_survey_keeps_unsupported_side_effects_and_wrong_stock_rows_excluded(self):
        raw = [r"PACKAGE Maps_OverWorld\Docks_01_P.umap"]
        prefix = "Loot_Spawner1=Loot_Spawner1; Loot_Spawner=Loot_Spawner; SpawnSalt=-17; RootComponent=DefaultSceneRoot"
        guaranteed = "GuaranteeSpawn=True"
        rows = [
            guaranteed,
            f"{guaranteed}; Replenish_Mode={PERMANENT}; Loot_Override={FISH}",
            f"{guaranteed}; Replenish_Mode={PERMANENT}; Ping_IfOpened=True",
            f"{guaranteed}; Replenish_Mode=RespawnSetting_Enum::NewEnumerator1",
            f"Replenish_Mode={PERMANENT}",
            f"Loot_Override={FISH}",
            f"{guaranteed}; IgnoreKarma=True",
            f"{guaranteed}; Loot_Override=DataTableRowHandle(Unregistered)",
            f"{guaranteed}; SpawnChance_Item=1",
        ]
        for index, suffix in enumerate(rows):
            raw.append(f"LootCrate_Small_BP_C LootCrate_Small_BP_C_{index} {prefix}; {suffix}")
        _, placed, qualified = survey("\n".join(raw))
        self.assertEqual(len(placed), len(rows))
        self.assertEqual(qualified, {(DOCKS, "LootCrate_Small_BP_C_0"): (-17, None, None),
                                     (DOCKS, "LootCrate_Small_BP_C_1"): (-17, "FishCannery", 2)})
