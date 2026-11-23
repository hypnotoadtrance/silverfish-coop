#pragma once

#include <array>
#include <string_view>

#include <String/StringType.hpp>

namespace silverfish::native_guest_consumable
{
    struct Consumable
    {
        const RC::CharType* path;
        int value;
        double weight, regen_gain, food;
        bool can_cook{};
        int infection{};
        std::string_view entry{};
        int fatigue{};
        bool medicine{};
        double instant{}, stim{};
        bool antibiotic{};
    };

    inline constexpr std::array consumables{
        Consumable{STR("/Game/Classes/Items/Item_MRE.Item_MRE_C"), 25, 0.65, 10.0, 30.0, false, 0, "use-mre"},
        Consumable{STR("/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"), 8, 0.4, 0.0, 12.0, false, 0, "use-canned-peaches"},
        Consumable{STR("/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"), 14, 0.4, 0.0, 14.0, true, 0, "use-canned-beans"},
        Consumable{STR("/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"), 1, 0.05, 0.0, 3.5, true, 0, "use-brown-mushroom"},
        Consumable{STR("/Game/Classes/Items/Item_CannedBeef.Item_CannedBeef_C"), 30, 0.4, 0.0, 16.0, true, 8, "use-canned-beef"},
        Consumable{STR("/Game/Classes/Items/Item_HoneyEgg.Item_HoneyEgg_C"), 6, 0.05, 0.0, 5.0, true, 0, "use-honey-egg", 5},
        Consumable{STR("/Game/Classes/Items/Item_Leftovers.Item_Leftovers_C"), 5, 0.2, 0.0, 18.0, false, 0, "use-leftovers"},
        Consumable{STR("/Game/Classes/Items/Item_CannedSoup.Item_CannedSoup_C"), 12, 0.4, 0.0, 10.0, true, 3, "use-canned-soup"},
        Consumable{STR("/Game/Classes/Items/Item_CannedTuna.Item_CannedTuna_C"), 9, 0.14, 0.0, 6.0, false, 4, "use-canned-tuna"},
        Consumable{STR("/Game/Classes/Items/Item_DeadBeetle.Item_DeadBeetle_C"), 2, 0.05, 0.0, 3.0, true, 3, "use-dead-beetle"},
        Consumable{STR("/Game/Classes/Items/Item_DogMeat.Item_DogMeat_C"), 3, 0.12, 0.0, 5.0, true, 40, "use-dog-meat"},
        Consumable{STR("/Game/Classes/Items/Item_MilkBites.Item_MilkBites_C"), 3, 0.06, 0.0, 4.0, false, 0, "use-milk-bites"},
        Consumable{STR("/Game/Classes/Items/Item_PorkRibs.Item_PorkRibs_C"), 70, 0.5, 0.0, 22.0, true, 25, "use-pork-ribs"},
        Consumable{STR("/Game/Classes/Items/Item_Sausage.Item_Sausage_C"), 7, 0.15, 0.0, 10.0, true, 20, "use-sausage"},
        Consumable{STR("/Game/Classes/Items/Item_Venison.Item_Venison_C"), 40, 0.15, 0.0, 13.0, true, 25, "use-venison"},
        Consumable{STR("/Game/Classes/Items/Item_DirtyBandage.Item_DirtyBandage_C"), 3, 0.05, 45.0, 0.0, false, 5, "use-dirty-bandage", 0, true},
        Consumable{STR("/Game/Classes/Items/Item_Gauze.Item_Gauze_C"), 30, 0.06, 60.0, 0.0, false, 0, "use-gauze", 0, true, 25.0},
        Consumable{STR("/Game/Classes/Items/Item_MedSalve.Item_MedSalve_C"), 40, 0.05, 30.0, 0.0, false, -5, "use-medical-salve", 0, true, 20.0, 3.0},
        Consumable{STR("/Game/Classes/Items/Item_MedKit_Large.Item_MedKit_Large_C"), 100, 0.23, 100.0, 0.0, false, -5, "use-large-medkit", 0, true, 50.0, 4.0},
        Consumable{STR("/Game/Classes/Items/Item_StimPills.Item_StimPills_C"), 15, 0.01, 0.0, 0.0, false, -5, "use-stim-pills", 0, true, 0.0, 4.0},
        Consumable{STR("/Game/Classes/Items/Item_Antibiotics.Item_Antibiotics_C"), 100, 0.01, 0.0, 0.0, false, -100, "use-antibiotics", 0, true, 0.0, 0.0, true},
    };
}
