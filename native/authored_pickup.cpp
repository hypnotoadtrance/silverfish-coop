#include "authored_pickup.hpp"
#include "content_paths.hpp"
#include "world_path.hpp"
#include "guest_drop_projection.hpp"
#include "guest_regen.hpp"
#include "guest_weapon_registry.hpp"
#include "interaction_input.hpp"
#include "loot_escrow.hpp"
#include "mindcore.hpp"
#include "stock_cash.hpp"
#include "quest_rewards.hpp"
#include "stock_wallet.hpp"
#include "zone_exit.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <ranges>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    struct Profile
    {
        std::string_view source;
        const U::TCHAR* world;
        const U::TCHAR* actor;
        const U::TCHAR* item_class;
        double x, y, z;
        int item_type;
        std::optional<int> stack_units{};
        bool physics{};
        const silverfish::guest_weapon_profiles::StockAmmo* stock_ammo{};
        double stack_weight{};
        int stack_value{};
        bool stock_cash{};
        const U::TCHAR* dead_drop{};
        bool stock_bandage{};
        bool discovered{};

        bool stacked_supply() const { return stock_ammo || stock_bandage; }
    };

    constexpr Profile dead_drop_profile{
        .source = "crossroads-dead-drop-bag",
        .world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
        .actor = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Bag_DeadDrop_CrossRoads_C_1"),
        .item_class = STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C"),
        .item_type = 1, .dead_drop = STR("DeadDrop_CrossRoads"),
    };

    constexpr Profile metro_drop_profile{
        .source = "metro-dead-drop-bag",
        .world = STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
        .actor = STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Bag_DeadDrop_Metro_C_1"),
        .item_class = STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_Metro.Bag_DeadDrop_Metro_C"),
        .item_type = 1, .dead_drop = STR("DeadDrop_Metro"),
    };

    constexpr Profile flood_town_drop_profile{
        .source = "flood-town-dead-drop-bag",
        .world = STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"),
        .actor = STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.Bag_DeadDrop_FloodVillage_C_1"),
        .item_class = STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_FloodVillage.Bag_DeadDrop_FloodVillage_C"),
        .item_type = 1, .dead_drop = STR("DeadDrop_FloodTown"),
    };

    constexpr std::string_view drill_head_source = "scientist-tunnel-drill-head";
    constexpr std::string_view lab_blank_tape_source = "lab-e206-blank-data-tape";
    constexpr std::string_view crossroads_lockpick_source = "crossroads-lockpick";

    constexpr std::array profiles{
        dead_drop_profile,
        metro_drop_profile,
        flood_town_drop_profile,
        Profile{
            "observatory-comp-board",
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Item_CompBoard_C_2"),
            STR("/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"),
            -5680.476689, -849.040347, 128.496287, 11,
        },
        Profile{
            "city-copper-wire",
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_CopperWire_C_3"),
            STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"),
            1299.7165908306, 3418.79597614179, 1239.49197257439, 11,
        },
        Profile{
            "dead-end-coup-report",
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1"),
            STR("/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C"),
            63519.2477725694, 36406.1978875462, 849.5514682383, 9,
        },
        Profile{
            "scav-bar-gold-bag",
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P:PersistentLevel.Bag_GoldRush_C_1"),
            STR("/Game/Classes/Items/Key_Items/Bag_GoldRush.Bag_GoldRush_C"),
            -6300.0, -2150.0, -179.9237181486, 1,
        },
        Profile{
            "railyard-depot-documents",
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.Documents_DepotOffice_BP_C_1"),
            STR("/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C"),
            3297.7888314760, -24282.0923970261, 990.5693125502, 9,
        },
        Profile{
            "city-paladin-brandy",
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_VintageWine_C_1"),
            STR("/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C"),
            -571.3226728526, -8316.4123004860, -199.9998970174, 1,
        },
        Profile{
            "scientist-blank-data-tape",
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.DataTape_Blank_C_3"),
            STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
            7580.6051850203, 5202.1434900782, -642.6426753447, 9,
        },
        Profile{
            .source = lab_blank_tape_source,
            .world = STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P"),
            .actor = STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P:PersistentLevel.DataTape_Blank_C_1"),
            .item_class = STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
            .x = 9789.311919064377, .y = 465.9586029091134, .z = 677.8869114598496,
            .item_type = 9, .physics = true,
        },
        Profile{
            drill_head_source,
            STR("/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P.Sci_Tunnel_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P.Sci_Tunnel_01_P:PersistentLevel.Art_DrillHead_C_1"),
            STR("/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C"),
            57699.99999938153, -10649.999999703614, -3036.2649490925587, 0,
        },
        Profile{
            "crossroads-pocket-watch",
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Item_PocketWatch_C_1"),
            STR("/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C"),
            9338.318763111682, 8669.320108298598, 391.93632416768173, 11, 1, true,
        },
        Profile{
            "crossroads-old-pipe",
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.MeleeWep_OldPipe_C_1"),
            STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"),
            -5145.994939851458, 1711.4458331043368, 31.917839181893726, 2,
        },
        Profile{
            .source = crossroads_lockpick_source,
            .world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
            .actor = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Eqp_LockPick_C_1"),
            .item_class = STR("/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C"),
            .x = -17194.406526906052, .y = -2545.1588886940604, .z = -61.61132574006076,
            .item_type = 5, .stack_units = 2, .stack_weight = 0.04, .stack_value = 14,
        },
        Profile{
            "savlo-estate-evil-eye-ring",
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P:PersistentLevel.Art_EvilEyeRing_C_1"),
            STR("/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C"),
            125.08236158222627, 2704.0395701013076, 474.9005913197349, 0,
        },
        Profile{
            "railyard-dust-filter",
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.Helm_DustFilter_C_1"),
            STR("/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"),
            -8423.943177033869, -30074.58542281168, 264.03804398884085, 6,
        },
        Profile{
            "northeast-rail-molasses",
            STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P.NorthEast_Rail_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P.NorthEast_Rail_P:PersistentLevel.Item_Molasis_C_1"),
            STR("/Game/Classes/Items/Item_Molasis.Item_Molasis_C"),
            -16087.482435820171, 5321.0489255090415, 982.4550416480851, 0, std::nullopt, true,
        },
        Profile{
            "trench-savlo-keycard",
            STR("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P.Trench_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P.Trench_01_P:PersistentLevel.KeyCard_Savlo_BP_C_1"),
            STR("/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C"),
            30714.073646980298, -137718.5051940477, 2043.691163490359, 0,
        },
        Profile{
            "cistern-old-nfta-keycard",
            STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.KeyCard_Refinery_Sec_BP_C_1"),
            STR("/Game/Classes/Items/Key_Items/KeyCard_NFTA_Old_BP.KeyCard_NFTA_Old_BP_C"),
            5092.807753005069, 11380.813008214138, -2153.364919590843, 0,
        },
        Profile{
            "fort-leto-locker-key-543",
            STR("/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P.FortLeto_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P.FortLeto_01_P:PersistentLevel.LockerKey_543_C_1"),
            STR("/Game/Classes/Items/Key_Items/LockerKey_543.LockerKey_543_C"),
            -2411.2730396058255, -28557.413414035567, 542.1560096044024, 0,
        },
        Profile{
            "gorge-insurrectionist-recorder",
            STR("/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P.Gorge_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P.Gorge_01_P:PersistentLevel.Item_InniRadio_01_C_3"),
            STR("/Game/Classes/Items/Key_Items/Item_InniRadio_01.Item_InniRadio_01_C"),
            3656.771352378591, 2466.248841434704, 3590.8622264942896, 11,
        },
        Profile{
            "forest-brokers-artifacts-bag",
            STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P:PersistentLevel.Brokers_ArtifactsBag_C_1"),
            STR("/Game/Classes/Items/Key_Items/Brokers_ArtifactsBag.Brokers_ArtifactsBag_C"),
            -8042.335612722819, 25100.7873570329, 898.6314940223217, 1,
        },
#include "authored_cash_profiles.inc"
#include "authored_supply_profiles.inc"
    };
    constexpr auto base_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto interact_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto money_base = STR("/Game/Classes/Items/Item_Money_Master.Item_Money_Master_C");
    constexpr auto money_interact = STR("/Game/Classes/Items/Item_Money_Master.Item_Money_Master_C:SendInteractionToActor");
    constexpr auto resolve_save_mode = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:ResolveSaveMode");
    constexpr auto code_library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C");
    constexpr auto stock_actor_id = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C:Calc_UniqueID");
    constexpr auto secure_locker_class = STR("/Game/Classes/Actors/Lootables/LootLocker_Secure_BP.LootLocker_Secure_BP_C");
    constexpr auto secure_locker_input =
        STR("/Game/Classes/Actors/Lootables/LootLocker_Secure_BP.LootLocker_Secure_BP_C:SendInteractionToActor");
    constexpr auto trench_savlo_locker =
        STR("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P.Trench_01_P:PersistentLevel.LootLocker_Secure_BP_C_16");
    struct CabinetInput
    {
        std::string_view source;
        const U::TCHAR* actor;
        const U::TCHAR* actor_class;
        const U::TCHAR* callback;
        int salt;
    };
    constexpr std::array cabinet_inputs{
        CabinetInput{
            "railyard-dust-filter",
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.LootCabnet_Small_BP_C_36"),
            STR("/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C"),
            STR("/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C:SendInteractionToActor"),
            -1540208128,
        },
        CabinetInput{
            "northeast-rail-molasses",
            STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P.NorthEast_Rail_P:PersistentLevel.LootCabnet_Large_BP_C_4"),
            STR("/Game/Classes/Actors/Lootables/LootCabnet_Large_BP.LootCabnet_Large_BP_C"),
            STR("/Game/Classes/Actors/Lootables/LootCabnet_Large_BP.LootCabnet_Large_BP_C:SendInteractionToActor"),
            -1439313920,
        },
    };
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_script = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto inventory_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto default_item_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeDefaultItem");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto tape_station_class = STR("/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C");
    constexpr auto blank_tape_path = STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C");
    struct TapeExchangeProfile
    {
        const U::TCHAR* world;
        const U::TCHAR* station;
        const U::TCHAR* product;
        std::string_view reason_prefix{"stone_maze"};
    };
    constexpr TapeExchangeProfile stone_maze_tape{
        STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"),
    };
    constexpr TapeExchangeProfile eather_pump_tape{
        STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P"),
        STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C"),
        "eather_pump",
    };
    constexpr TapeExchangeProfile drill_test_results_tape{
        STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
        STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"),
        "drill_test_results",
    };
    constexpr TapeExchangeProfile drill_instructions_tape{
        STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P"),
        STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_DrillInstructions.DataTape_DrillInstructions_C"),
        "drill_instructions",
    };
    constexpr std::array guid_members{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr std::size_t parameter_capacity = 256;
    constexpr double maximum_range_squared = 250.0 * 250.0;

    template <class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    template <class T> T* parameter(U::UFunction* fn, const U::TCHAR* name, int offset, bool output = false)
    {
        auto* property = field<T>(fn, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == output && !property->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= fn->GetParmsSize() ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(owner, name), owner);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    int role(U::UObject* actor)
    {
        auto* property = field<U::FByteProperty>(actor, STR("Role"));
        return property ? property->GetPropertyValueInContainer(actor) : -1;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool env(const char* variable)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), variable) == 0 && size == value.size() && value[0] == '1';
    }

    bool headless() { return env("SILVERFISH_COOP_HEADLESS"); }

    struct Observation
    {
        Clock::time_point expires{};
        std::uint64_t sequence{};
        bool fixture{}, input_pending{};
    };

    struct Scope
    {
        U::FWeakObjectPtr world, pawn, actor, box, controller, driver;
        const Profile* profile{};
        bool host{}, claimed{}, original_collision{}, original_hidden{};
        Observation observed{};
        Guid merged_source{};
        std::optional<U::FVector> hidden_at;
        bool restore_simulation{};
    };

    std::array<Scope, profiles.size()> scopes{};
    struct LooseSource
    {
        RC::StringType world, actor, item_class;
        std::string item_class_utf8;
        Profile profile{};
        Scope scope{};
        U::UObject* indexed_actor{};
        std::optional<U::FVector> static_position;
        bool carry_candidate{}, carry_enrolled{};
    };
    std::map<std::string, LooseSource, std::less<>> loose_sources;
    std::map<U::UObject*, LooseSource*> loose_by_actor;
    std::set<std::string, std::less<>> loose_carry_pending;
    std::vector<U::FWeakObjectPtr> loose_candidates;
    std::size_t next_loose_candidate{};
    std::set<std::string, std::less<>> loose_hidden;
    std::uint64_t loose_generation{};
    U::FWeakObjectPtr loose_pawn{}, loose_world{};
    bool loose_host{};
    Clock::time_point loose_rescan{};
    constexpr auto loose_scan_interval = std::chrono::seconds(5);
    constexpr auto loose_scan_budget = std::chrono::microseconds(1500);
    constexpr std::size_t loose_scan_limit = 64;

    std::uint64_t sequence{};
    Scope* fixture_scope{};
    using DropPose = std::array<double, 9>;
    struct DropProjection
    {
        Scope scope{};
        std::string epoch;
        Clock::time_point expires{};
    };
    DropProjection drop_projection{};
    U::FWeakObjectPtr drop_settled_actor{};
    Clock::time_point drop_settles_at{};
    constexpr auto drop_lease = std::chrono::milliseconds(1800);
    constexpr auto drop_settle = std::chrono::milliseconds(750);
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID,
        Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};

    bool ready() { return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire); }

    bool local_player(U::UObject* controller, U::UObject* player)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        return live(player) && type && player->IsA(type) && object(player, STR("PlayerController")) == controller;
    }

    bool connection(U::UObject* driver, U::UObject* player)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(player) && type && player->IsA(type) && object(player, STR("Driver")) == driver;
    }

    bool local_scope(const Scope& state)
    {
        auto* world = state.world.Get();
        auto* pawn = state.pawn.Get();
        auto* controller = state.controller.Get();
        auto* driver = state.driver.Get();
        if (!live(world) || !state.profile || world->GetPathName() != state.profile->world ||
            !exact(pawn, pawn_path) || !exact(controller, controller_path) || !live(driver) ||
            object(world, STR("NetDriver")) != driver || object(driver, STR("World")) != world ||
            !exact(object(world, STR("OwningGameInstance")), instance_path) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            object(pawn, STR("Controller")) != controller || object(controller, STR("Pawn")) != pawn ||
            !local_player(controller, object(controller, STR("Player"))) || role(pawn) != (state.host ? 3 : 2)) return false;
        auto* server_prop = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        auto* server = read(server_prop, driver);
        return server_prop && (state.host ? !server && role(controller) == 3 : connection(driver, server));
    }

    Scope* select_source(std::string_view source, bool authored_only = false)
    {
        if (source == "crossroads-old-pipe" && !(env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS"))) return nullptr;
        const auto match = std::ranges::find_if(profiles, [&source](const Profile& profile) { return profile.source == source; });
        if (match != profiles.end()) return &scopes[static_cast<std::size_t>(match - profiles.begin())];
        if (authored_only) return nullptr;
        const auto loose = loose_sources.find(source);
        return loose == loose_sources.end() ? nullptr : &loose->second.scope;
    }

    Scope* select_source(const Lua& lua)
    {
        return lua.is_string() ? select_source(lua.get_string()) : nullptr;
    }

    bool stock_ammo_matches(U::UObject* actor, const Profile& profile)
    {
        const auto& ammo = *profile.stock_ammo;
        auto* handle_type = find<U::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* handle = field<U::FStructProperty>(actor, STR("AmmoType"));
        auto* caliber = field<U::FByteProperty>(actor, STR("Caliber"));
        auto* table = field<U::FObjectProperty>(handle_type, STR("DataTable"));
        auto* name = field<U::FNameProperty>(handle_type, STR("RowName"));
        auto* expected_table = find<U::UObject>(STR("/Game/Classes/Libraries/DataTables/AmmoTypes_DT.AmmoTypes_DT"));
        auto* weight = field<U::FDoubleProperty>(actor, STR("Weight"));
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        const U::FName expected{ammo.row, U::FNAME_Find};
        if (!actor->HasAnyFlags(U::RF_WasLoaded) || actor->GetClassPrivate() != find<U::UClass>(ammo.item) ||
            !handle_type || !handle || handle->GetStruct().Get() != handle_type ||
            !caliber || caliber->GetPropertyValueInContainer(actor) != ammo.caliber ||
            !table || !name || !expected_table || expected.IsNone() || !weight || !value ||
            std::abs(weight->GetPropertyValueInContainer(actor) - profile.stack_weight) > 1e-9 ||
            !std::isfinite(weight->GetPropertyValueInContainer(actor)) ||
            value->GetPropertyValueInContainer(actor) != profile.stack_value) return false;
        const auto* row = handle->ContainerPtrToValuePtr<void>(actor);
        return read(table, row) == expected_table && name->GetPropertyValueInContainer(row) == expected;
    }

    const char* stock_bandage_fault(U::UObject* actor, const Profile& profile)
    {
        auto* type = find<U::UClass>(STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C"));
        if (!type || actor->GetClassPrivate() != type || !actor->HasAnyFlags(U::RF_WasLoaded))
            return "authored_pickup_stock_bandage_identity_unqualified";
        auto* defaults = type->GetClassDefaultObject().Get();
        constexpr auto destroyed = static_cast<U::EObjectFlags>(U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        if (!defaults || defaults->GetClassPrivate() != type || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            defaults->HasAnyFlags(destroyed) || defaults->IsUnreachable())
            return "authored_pickup_stock_bandage_defaults_unqualified";
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!mode || mode->GetPropertyValueInContainer(actor) != 2 || mode->GetPropertyValueInContainer(defaults) != 2 ||
            !boolean(actor, STR("DespawnIfPlayerOwns"), false) || !boolean(actor, STR("bReplicates"), false))
            return "authored_pickup_stock_bandage_persistence_unqualified";
        auto* units = field<U::FIntProperty>(actor, STR("Ammo"));
        auto* weight = field<U::FDoubleProperty>(actor, STR("Weight"));
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        const auto stock_weight = [&](U::UObject* item) {
            return std::abs(weight->GetPropertyValueInContainer(item) - profile.stack_weight) <= 1e-9;
        };
        if (!units || units->GetPropertyValueInContainer(defaults) != 1 || !weight ||
            !stock_weight(actor) || !stock_weight(defaults) || !value ||
            value->GetPropertyValueInContainer(actor) != profile.stack_value ||
            value->GetPropertyValueInContainer(defaults) != profile.stack_value)
            return "authored_pickup_stock_bandage_payload_unqualified";
        auto* picked_up = field<U::FMulticastInlineDelegateProperty>(actor, STR("Item_PickedUp"));
        if (!picked_up || picked_up->GetSize() != sizeof(U::FMulticastScriptDelegate) ||
            picked_up->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(actor)->IsBound())
            return "authored_pickup_stock_bandage_delegate_unqualified";
        return nullptr;
    }

    int loose_cash_value(U::UObject* actor)
    {
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!value || value->GetArrayDim() != 1 || !mode || mode->GetArrayDim() != 1 ||
            mode->GetPropertyValueInContainer(actor) != 2 || !actor->HasAnyFlags(U::RF_WasLoaded) ||
            !boolean(actor, STR("bReplicates"), false)) return 0;
        const auto amount = value->GetPropertyValueInContainer(actor);
        auto* type = actor->GetClassPrivate();
        return silverfish::stock_cash::is_family(type) &&
            silverfish::stock_cash::qualified_value(type, amount, actor) ? amount : 0;
    }

    bool drill_head_payload(U::UObject* actor)
    {
        auto* weight = field<U::FDoubleProperty>(actor, STR("Weight"));
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        auto* units = field<U::FIntProperty>(actor, STR("Ammo"));
        auto* effects = field<U::FArrayProperty>(actor, STR("PassiveEffects"));
        auto* name = effects ? U::CastField<U::FStrProperty>(effects->GetInner()) : nullptr;
        auto* pickup = field<U::FMulticastInlineDelegateProperty>(actor, STR("Item_PickedUp"));
        if (!actor->HasAnyFlags(U::RF_WasLoaded) || !weight || weight->GetPropertyValueInContainer(actor) != 10.0 ||
            !value || value->GetPropertyValueInContainer(actor) != 32000 || !units || units->GetPropertyValueInContainer(actor) != 0 ||
            !name || !pickup || pickup->GetSize() != sizeof(U::FMulticastScriptDelegate) ||
            pickup->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(actor)->IsBound()) return false;
        U::FScriptArrayHelper passives(effects, effects->ContainerPtrToValuePtr<void>(actor));
        if (passives.Num() != 1) return false;
        const auto passive = name->GetPropertyValue(passives.GetRawPtr(0));
        return passive.Len() == 14 && std::wstring_view{*passive} == STR("EatherAffinity");
    }

    bool lab_blank_tape_payload(U::UObject* actor)
    {
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        auto* units = field<U::FIntProperty>(actor, STR("Ammo"));
        auto* weight = field<U::FDoubleProperty>(actor, STR("Weight"));
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        auto* handle = field<U::FStructProperty>(actor, STR("TableRowName"));
        auto* handle_type = find<U::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table = field<U::FObjectProperty>(handle_type, STR("DataTable"));
        auto* row = field<U::FNameProperty>(handle_type, STR("RowName"));
        auto* stock_table = find<U::UObject>(STR("/Game/Classes/Libraries/DataTables/DataTapes_DT.DataTapes_DT"));
        const U::FName blank{STR("EmptyTape"), U::FNAME_Find};
        auto* pickup = field<U::FMulticastInlineDelegateProperty>(actor, STR("Item_PickedUp"));
        if (!actor->HasAnyFlags(U::RF_WasLoaded) || !mode || mode->GetPropertyValueInContainer(actor) != 2 ||
            !boolean(actor, STR("DespawnIfPlayerOwns"), false) || !boolean(actor, STR("bReplicates"), false) ||
            !units || units->GetPropertyValueInContainer(actor) != 0 || !weight ||
            !(std::abs(weight->GetPropertyValueInContainer(actor) - 0.3) <= 1e-9) ||
            !value || value->GetPropertyValueInContainer(actor) != 30 ||
            !handle_type || !handle || handle->GetStruct().Get() != handle_type ||
            !table || !row || !stock_table || blank.IsNone() || !pickup ||
            pickup->GetSize() != sizeof(U::FMulticastScriptDelegate) ||
            pickup->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(actor)->IsBound()) return false;
        const auto* payload = handle->ContainerPtrToValuePtr<void>(actor);
        return read(table, payload) == stock_table && row->GetPropertyValueInContainer(payload) == blank;
    }

    U::FByteProperty* loose_respawn_mode(U::UObject* actor)
    {
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        return mode && mode->GetPropertyValueInContainer(actor) <= 2 ? mode : nullptr;
    }

    bool carryable_placed_item(U::UObject* actor)
    {
        return actor && actor->HasAnyFlags(U::RF_WasLoaded) && boolean(actor, STR("Physics"), true)
            && boolean(actor, STR("bNetLoadOnClient"), true);
    }

    bool remotely_hidden_loose(U::UObject* actor)
    {
        return role(actor) == 1 && carryable_placed_item(actor)
            && boolean(actor, STR("bReplicateMovement"), true) && boolean(actor, STR("bHidden"), true);
    }

    const char* generic_payload_fault(U::UObject* actor, bool cash)
    {
        auto* base = find<U::UClass>(base_class);
        if (!base || !actor->IsA(base) || !actor->HasAnyFlags(U::RF_WasLoaded)) return "not_placed_stock_item";
        if (!boolean(actor, STR("bReplicates"), false) &&
            (cash || !carryable_placed_item(actor) || !boolean(actor, STR("bReplicateMovement"), true)))
            return "replicated_item";
        auto* held = field<U::FBoolProperty>(actor, STR("HeldByPlayer"));
        if ((held && held->GetPropertyValueInContainer(actor)) || exact(object(actor, STR("Owner")), pawn_path))
            return "held_item";
        auto* item_type = field<U::FByteProperty>(actor, STR("ItemType"));
        if (!item_type || item_type->GetPropertyValueInContainer(actor) > 11 || !loose_respawn_mode(actor))
            return "unqualified_item_type_or_respawn";
        auto* pickup = field<U::FMulticastInlineDelegateProperty>(actor, STR("Item_PickedUp"));
        if (!pickup || pickup->GetSize() != sizeof(U::FMulticastScriptDelegate)
            || pickup->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(actor)->IsBound())
            return "custom_pickup_delegate";
        auto* type = actor->GetClassPrivate();
        if (type->GetFunctionByNameInChain(U::FName{STR("SendInteractionToActor")}) !=
                find<U::UFunction>(cash ? money_interact : interact_path)
            || type->GetFunctionByNameInChain(U::FName{STR("ResolveSaveMode")}) != find<U::UFunction>(resolve_save_mode)
            || (!cash && type->GetFunctionByNameInChain(U::FName{STR("PutInInventory")}) !=
                find<U::UFunction>(STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:PutInInventory"))))
            return "custom_pickup_function";
        return cash && loose_cash_value(actor) <= 0 ? "unqualified_stock_cash" : nullptr;
    }

    bool in_loose_level(U::AActor* actor, U::UObject* world)
    {
        if (!live(actor) || !live(world)) return false;
        if (actor->GetLevel() == object(world, STR("PersistentLevel"))) return actor->GetWorld() == world;
        return silverfish::native_zone::visible_actor_level(actor, static_cast<U::UWorld*>(world));
    }

    // Stream instances may use a different package leaf and World export name.
    // Keep the owning campaign in the claim key so the same sublevel can be reused in another map.
    std::string_view loose_actor_path(std::string_view source, std::string_view world)
    {
        constexpr std::string_view level_marker{":PersistentLevel."};
        if (source.size() > 568) return {};
        const auto separator = source.find('|');
        const auto path = separator == source.npos ? source : source.substr(separator + 1);
        const auto level = path.find(level_marker);
        const auto owner = path.substr(0, level);
        const auto dot = owner.find('.');
        if (level == path.npos || dot == owner.npos || path.size() > 384) return {};
        const auto token = [](std::string_view value, bool actor = false) {
            return !value.empty() && std::ranges::all_of(value, [actor](char c) {
                return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                    c == '_' || (actor && c == '-');
            });
        };
        if (!token(path.substr(level + level_marker.size()), true)) return {};
        if (separator == source.npos) return owner == world ? path : std::string_view{};
        const auto map = world.substr(0, world.find('.'));
        const auto package = owner.substr(0, dot);
        return source.substr(0, separator) == map && package != map && silverfish::world_path::package(package) &&
            token(owner.substr(dot + 1)) ? path : std::string_view{};
    }

    std::string loose_source_key(RC::StringViewType path, RC::StringViewType world, bool streamed)
    {
        std::string source;
        if (streamed) source = RC::to_utf8_string(world.substr(0, world.find(STR('.')))) + '|';
        source += RC::to_utf8_string(path);
        return source;
    }

    const char* source_fault(U::UObject* actor, U::UObject* world, const Profile& profile, bool require_placement = true)
    {
        if (!exact(actor, profile.item_class) || actor->GetPathName() != profile.actor ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return "authored_pickup_stock_actor_identity_unqualified";
        if (profile.discovered)
        {
            auto* item = static_cast<U::AActor*>(actor);
            return in_loose_level(item, world)
                && !generic_payload_fault(actor, profile.stock_cash) ? nullptr : "loose_pickup_stock_actor_changed";
        }
        if (!boolean(actor, STR("Physics"), profile.physics)) return "authored_pickup_stock_physics_unqualified";
        if (!boolean(actor, STR("Stacks"), profile.stock_cash || profile.stack_units.has_value()))
            return "authored_pickup_stock_stacking_unqualified";
        if (profile.stack_units)
        {
            auto* count = field<U::FIntProperty>(actor, STR("Ammo"));
            if (!count || count->GetPropertyValueInContainer(actor) != *profile.stack_units)
                return "authored_pickup_stock_amount_unqualified";
        }
        auto* item = static_cast<U::AActor*>(actor);
        auto* category = field<U::FByteProperty>(actor, STR("ItemType"));
        if (item->GetWorld() != world || item->GetLevel() != object(world, STR("PersistentLevel")))
            return "authored_pickup_stock_world_unqualified";
        if (!category || category->GetPropertyValueInContainer(actor) != profile.item_type)
            return "authored_pickup_stock_category_unqualified";
        if (profile.source == drill_head_source && !drill_head_payload(actor))
            return "authored_pickup_stock_drill_head_payload_unqualified";
        if (profile.source == lab_blank_tape_source && !lab_blank_tape_payload(actor))
            return "authored_pickup_stock_lab_blank_tape_payload_unqualified";
        if (profile.source == crossroads_lockpick_source)
        {
            auto* weight = field<U::FDoubleProperty>(actor, STR("Weight"));
            auto* value = field<U::FIntProperty>(actor, STR("Value"));
            if (!actor->HasAnyFlags(U::RF_WasLoaded) || !weight || !value ||
                !(std::abs(weight->GetPropertyValueInContainer(actor) - profile.stack_weight) <= 1e-9) ||
                value->GetPropertyValueInContainer(actor) != profile.stack_value)
                return "authored_pickup_stock_lockpick_payload_unqualified";
        }
        if (profile.stock_ammo && !stock_ammo_matches(actor, profile))
            return "authored_pickup_stock_ammo_profile_unqualified";
        if (const auto* fault = profile.stock_bandage ? stock_bandage_fault(actor, profile) : nullptr) return fault;
        if (profile.stock_cash && !loose_cash_value(actor)) return "authored_pickup_stock_cash_profile_unqualified";
        // Physics-enabled items can settle below their authored placement under vanilla simulation.
        // A claimed item may fall when collision is disabled. Its exact scoped object stays hidden.
        // The stock randomizer chooses the Dead Drop transform on each map load.
        if (profile.dead_drop || (profile.physics && !require_placement)) return nullptr;
        auto position = item->K2_GetActorLocation();
        const auto dx = position.GetX() - profile.x;
        const auto dy = position.GetY() - profile.y;
        const auto dz = position.GetZ() - profile.z;
        const auto horizontal = dx * dx + dy * dy;
        const bool located = profile.physics ?
            horizontal <= 75.0 * 75.0 && dz >= -100.0 && dz <= 12.0 :
            horizontal + dz * dz <= 25.0;
        return located ? nullptr : "authored_pickup_stock_placement_unqualified";
    }

    bool exact_source(U::UObject* actor, U::UObject* world, const Profile& profile, bool require_placement = true)
    {
        return source_fault(actor, world, profile, require_placement) == nullptr;
    }

    U::UObject* source_box(U::UObject* actor)
    {
        auto* value = object(actor, STR("Box"));
        return exact(value, STR("/Script/Engine.BoxComponent")) && value->GetOuterPrivate() == actor &&
            value == object(actor, STR("RootComponent")) ? value : nullptr;
    }

    const Profile* dead_drop_in_world(U::UObject* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        const auto match = std::ranges::find_if(profiles, [&](const auto& profile) {
            return profile.dead_drop && path == profile.world;
        });
        return match == profiles.end() ? nullptr : &*match;
    }

    const char* placed_drop_fault(U::UObject* actor, U::UObject* world, const Profile& profile)
    {
        if (!live(actor)) return "actor_not_live";
        if (actor->GetClassPrivate()->GetPathName() != profile.item_class || actor->GetPathName() != profile.actor)
            return "actor_identity";
        if (!actor->HasAnyFlags(U::RF_WasLoaded)) return "actor_not_loaded";
        if (!boolean(actor, STR("bActorIsBeingDestroyed"), false)) return "actor_being_destroyed";
        if (!live(world) || world->GetPathName() != profile.world) return "world_identity";
        auto* item = static_cast<U::AActor*>(actor);
        if (item->GetWorld() != world || item->GetLevel() != object(world, STR("PersistentLevel"))) return "persistent_level";
        return nullptr;
    }

    const char* drop_identity_fault(U::UObject* actor, U::UObject* world, const Profile& profile)
    {
        if (const auto* fault = placed_drop_fault(actor, world, profile)) return fault;
        if (!boolean(actor, STR("bReplicates"), false)) return "replication";
        if (!boolean(actor, STR("Stacks"), false)) return "stacking";
        if (!source_box(actor)) return "root_box";
        auto* quest = field<U::FNameProperty>(actor, STR("Quest_ID"));
        const U::FName key{profile.dead_drop, U::FNAME_Find};
        if (!quest || key.IsNone() || quest->GetPropertyValueInContainer(actor) != key) return "quest_id";
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!mode || mode->GetPropertyValueInContainer(actor) != 0) return "respawn_mode";
        if (!boolean(actor, STR("DespawnIfPlayerOwns"), true)) return "owned_despawn";
        if (!boolean(actor, STR("Physics"), false)) return "physics";
        return nullptr;
    }

    bool dead_drop_identity(const Scope& state)
    {
        return state.profile && !drop_identity_fault(state.actor.Get(), state.world.Get(), *state.profile);
    }

    bool valid_drop_pose(const DropPose& pose)
    {
        for (std::size_t axis = 0; axis < pose.size(); ++axis)
        {
            const double limit = axis < 3 ? 1e7 : axis < 6 ? 360.0 : 100.0;
            if (!std::isfinite(pose[axis]) || std::abs(pose[axis]) > limit || (axis >= 6 && pose[axis] == 0))
                return false;
        }
        return std::abs(pose[0]) > 1.0 || std::abs(pose[1]) > 1.0 || std::abs(pose[2] - 9999.0) > 1.0;
    }

    DropPose read_drop_pose(U::AActor* actor)
    {
        const auto point = actor->K2_GetActorLocation();
        const auto angle = actor->K2_GetActorRotation();
        const auto scale = actor->GetActorScale3D();
        return {point.X(), point.Y(), point.Z(), angle.GetPitch(), angle.GetYaw(), angle.GetRoll(),
            scale.X(), scale.Y(), scale.Z()};
    }

    bool same_drop_pose(const DropPose& actual, const DropPose& expected)
    {
        if (!valid_drop_pose(actual) || !valid_drop_pose(expected)) return false;
        for (std::size_t axis = 0; axis < actual.size(); ++axis)
        {
            const double delta = actual[axis] - expected[axis];
            const double difference = axis >= 3 && axis < 6 ? std::remainder(delta, 360.0) : delta;
            if (std::abs(difference) > (axis < 6 ? .1 : .005)) return false;
        }
        return true;
    }

    bool hidden_drop(U::AActor* actor)
    {
        return boolean(actor, STR("bHidden"), true);
    }

    bool present_drop(const Scope& state)
    {
        auto* actor = static_cast<U::AActor*>(state.actor.Get());
        return dead_drop_identity(state) && state.box.Get() == source_box(actor) &&
            exact_source(actor, state.world.Get(), *state.profile) &&
            boolean(actor, STR("bHidden"), false) && actor->GetActorEnableCollision();
    }

    bool drop_gate(const Scope& state)
    {
        if (!state.profile || !state.profile->dead_drop) return true;
        if (!local_scope(state)) return false;
        if (state.host)
        {
            auto* save = object(object(state.world.Get(), STR("OwningGameInstance")), STR("SaveSlot_Ref"));
            auto* actor = state.actor.Get();
            return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && actor &&
                drop_settled_actor.Get() == actor && Clock::now() >= drop_settles_at &&
                object(state.pawn.Get(), STR("Save")) == save &&
                silverfish::native_quest_rewards::active_dead_drop(save, actor);
        }
        const auto& bound = drop_projection.scope;
        return Clock::now() < drop_projection.expires && state.actor.Get() == bound.actor.Get() &&
            state.world.Get() == bound.world.Get() && state.pawn.Get() == bound.pawn.Get() &&
            state.controller.Get() == bound.controller.Get() && state.driver.Get() == bound.driver.Get();
    }

    bool drop_fence(U::UObject* actor)
    {
        auto* type = live(actor) ? actor->GetClassPrivate() : nullptr;
        if (!type) return false;
        constexpr auto count = std::ranges::count_if(profiles, [](const Profile& profile) { return profile.dead_drop != nullptr; });
        static const auto classes = [] {
            std::array<std::pair<U::FName, const Profile*>, count> result{};
            std::size_t index = 0;
            for (const auto& profile : profiles)
            {
                if (!profile.dead_drop) continue;
                const RC::StringViewType path{profile.item_class};
                const auto name = path.substr(path.find_last_of(STR('.')) + 1);
                result[index++] = {U::FName{name.data(), U::FNAME_Add}, &profile};
            }
            return result;
        }();
        const auto match = std::ranges::find(classes, type->GetFName(), [](const auto& entry) { return entry.first; });
        if (match == classes.end() || type->GetPathName() != match->second->item_class) return false;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (placed_drop_fault(actor, world, *match->second)) return false;
        auto* driver = object(world, STR("NetDriver"));
        auto* field_server = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        auto* server = read(field_server, driver);
        return live(driver) && object(driver, STR("World")) == world &&
            exact(object(world, STR("OwningGameInstance")), instance_path) &&
            field_server && (server ? connection(driver, server) : env("SILVERFISH_COOP_NATIVE_GUEST_SAVE"));
    }

    bool settle_drop_projection()
    {
        drop_projection.expires = {};
        auto* actor = static_cast<U::AActor*>(drop_projection.scope.actor.Get());
        auto* world = drop_projection.scope.world.Get();
        const auto* profile = drop_projection.scope.profile;
        if (profile) scopes[profile - profiles.data()].observed = {};
        if (profile && !placed_drop_fault(actor, world, *profile))
        {
            actor->SetActorHiddenInGame(true);
            U::FHitResult ignored{};
            actor->K2_SetActorLocation(U::FVector{0.0, 0.0, 9999.0}, false, ignored, true);
            if (!hidden_drop(actor)) return false;
        }
        drop_projection = {};
        return true;
    }

    Scope* loose_actor_scope(U::UObject* actor);

    Scope* actor_scope(U::UObject* actor)
    {
        if (!actor) return nullptr;
        const auto match = std::ranges::find_if(scopes, [actor](const Scope& scope) { return scope.actor.Get() == actor; });
        if (match == scopes.end()) return loose_actor_scope(actor);
        if (!local_scope(*match)) return nullptr;
        if ((match->profile->stacked_supply() || match->profile->stock_cash || match->profile->dead_drop) &&
            (!exact_source(actor, match->world.Get(), *match->profile, !match->claimed) ||
             source_box(actor) != match->box.Get())) return nullptr;
        return &*match;
    }

    bool in_range(U::UObject* pawn, U::UObject* source)
    {
        auto player = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        auto item = static_cast<U::AActor*>(source)->K2_GetActorLocation();
        const auto dx = player.GetX() - item.GetX();
        const auto dy = player.GetY() - item.GetY();
        const auto dz = player.GetZ() - item.GetZ();
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(squared) && squared <= maximum_range_squared;
    }

    U::UFunction* interaction(bool cash = false)
    {
        auto* function = find<U::UFunction>(cash ? money_interact : interact_path);
        auto* passed_player = parameter<U::FObjectProperty>(function, STR("Player"), 0);
        auto* hit = parameter<U::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        if (!function || function->GetOuterPrivate() != find<U::UClass>(cash ? money_base : base_class) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !passed_player || passed_player->GetPropertyClass().Get() != find<U::UClass>(pawn_path) ||
            !hit || hit->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.SceneComponent"))) return nullptr;
        return function;
    }

    bool available(const Scope& scope)
    {
        auto* actor = scope.actor.Get();
        return !scope.claimed && local_scope(scope) && drop_gate(scope) &&
            (!scope.profile->dead_drop || present_drop(scope)) && exact_source(actor, scope.world.Get(), *scope.profile) &&
            source_box(actor) == scope.box.Get() && static_cast<U::AActor*>(actor)->GetActorEnableCollision() &&
            boolean(actor, STR("bHidden"), false);
    }

    bool restore_loose_location(Scope& scope, U::AActor* actor)
    {
        if (!scope.hidden_at) return true;
        auto saved = *scope.hidden_at;
        auto current = actor->K2_GetActorLocation();
        const auto dx = current.GetX() - saved.GetX(), dy = current.GetY() - saved.GetY(), dz = current.GetZ() - saved.GetZ();
        U::FHitResult ignored{};
        if (dx * dx + dy * dy + dz * dz > 1.0 && !actor->K2_SetActorLocation(saved, false, ignored, true))
            return false;
        scope.hidden_at.reset();
        return true;
    }

    std::optional<bool> loose_simulating(const Scope& scope)
    {
        static auto* query = find<U::UFunction>(STR("/Script/Engine.SceneComponent:IsAnySimulatingPhysics"));
        static auto* result = field<U::FBoolProperty>(query, STR("ReturnValue"));
        auto* box = scope.box.Get();
        if (!live(box) || !result || query->GetNumParms() != 1 || query->GetParmsSize() != 1 ||
            result->GetOffset_Internal() != 0 || !result->HasAnyPropertyFlags(U::CPF_ReturnParm) ||
            !query->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        std::uint8_t value{};
        box->ProcessEvent(query, &value);
        return result->GetPropertyValueInContainer(&value);
    }

    bool set_loose_simulation(Scope& scope, bool simulate)
    {
        static auto* function = find<U::UFunction>(STR("/Script/Engine.PrimitiveComponent:SetSimulatePhysics"));
        static auto* enabled = parameter<U::FBoolProperty>(function, STR("bSimulate"), 0);
        auto* box = scope.box.Get();
        if (!live(box) || !enabled || function->GetNumParms() != 1 || function->GetParmsSize() != 1 ||
            !function->HasAnyFunctionFlags(U::FUNC_Native)) return false;
        std::uint8_t value{};
        enabled->SetPropertyValueInContainer(&value, simulate);
        box->ProcessEvent(function, &value);
        return loose_simulating(scope) == simulate;
    }

    bool set_visibility(Scope& scope, bool claimed)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (!local_scope(scope) || (scope.profile->dead_drop && (!scope.host || !claimed || !drop_gate(scope))) ||
            !exact_source(actor, scope.world.Get(), *scope.profile, !scope.claimed || !claimed))
            return false;
        if (claimed && !scope.claimed && scope.profile->discovered)
        {
            if (carryable_placed_item(actor))
            {
                const auto simulating = loose_simulating(scope);
                if (!simulating || (*simulating && !set_loose_simulation(scope, false))) return false;
                scope.restore_simulation = *simulating;
            }
            if (!scope.host || scope.restore_simulation) scope.hidden_at = actor->K2_GetActorLocation();
        }
        if (!claimed && !restore_loose_location(scope, actor)) return false;
        const bool hidden = claimed || scope.original_hidden;
        const bool collision = !claimed && scope.original_collision;
        actor->SetActorHiddenInGame(hidden);
        actor->SetActorEnableCollision(collision);
        if (actor->GetActorEnableCollision() != collision || !boolean(actor, STR("bHidden"), hidden)) return false;
        if (!claimed && scope.restore_simulation)
        {
            if (!set_loose_simulation(scope, true)) return false;
            scope.restore_simulation = false;
        }
        scope.claimed = claimed;
        if (claimed)
        {
            scope.observed = {};
            if (scope.host && scope.profile->discovered && scope.profile->source.contains('|'))
                loose_hidden.emplace(scope.profile->source);
        }
        return true;
    }

    void clear(Scope& scope)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (scope.claimed && scope.profile && !scope.profile->dead_drop &&
            live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false))
        {
            restore_loose_location(scope, actor);
            actor->SetActorHiddenInGame(scope.original_hidden);
            actor->SetActorEnableCollision(scope.original_collision);
            if (scope.restore_simulation) set_loose_simulation(scope, true);
        }
        if (fixture_scope == &scope) fixture_scope = nullptr;
        scope = {};
    }

    void clear_loose()
    {
        for (auto& [_, loose] : loose_sources) clear(loose.scope);
        loose_sources.clear();
        loose_by_actor.clear();
        loose_candidates.clear();
        loose_carry_pending.clear();
        next_loose_candidate = 0;
        loose_hidden.clear();
        loose_pawn.Reset();
        loose_world.Reset();
        loose_rescan = {};
    }

    void clear()
    {
        settle_drop_projection();
        drop_settled_actor.Reset();
        drop_settles_at = {};
        for (auto& scope : scopes) clear(scope);
        clear_loose();
    }

    bool admit_input(const Scope& scope, U::UObject* pawn, U::UObject* hit)
    {
        return pawn && pawn == scope.pawn.Get() && hit && hit == scope.box.Get() && available(scope) &&
            boolean(pawn, STR("Dead"), false) && in_range(pawn, scope.actor.Get());
    }

    struct PickupListener
    {
        std::string_view source;
        const U::TCHAR* script_class;
        const U::TCHAR* callback_name;
        const U::TCHAR* receiver;
        const U::TCHAR* receiver_class;
        bool gold_bag;
    };

    constexpr std::array pickup_listeners{
        PickupListener{"dead-end-coup-report",
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P_C"), STR("PickedUp_Note"),
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P:PersistentLevel.NPC_CoupOfficer_02_C_1"),
            STR("/Game/Classes/NPCs/NPC_Characters/NPC_CoupOfficer_02.NPC_CoupOfficer_02_C"), false},
        PickupListener{"scav-bar-gold-bag",
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P_C"), STR("PickedUp_Bag"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P:PersistentLevel.Dialogue_Controler_BP_C_1"),
            STR("/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C"), true},
    };

    struct PickupBroadcast
    {
        U::UObject* script{};
        U::UFunction* listener{};
        U::UObject* receiver{};
        U::UObject* host{};
        bool gold_bag{}, entered{}, reaction{}, ping{};
    };

    PickupBroadcast* active_pickup_broadcast{};

    void observe_pickup_broadcast(U::UObject* target, U::UFunction* function, const void* args)
    {
        auto* broadcast = active_pickup_broadcast;
        if (!broadcast) return;
        if (target == broadcast->script && function == broadcast->listener) broadcast->entered = true;
        if (!broadcast->entered || !args) return;
        const auto is = [&](const U::TCHAR* name, U::UObject* expected) {
            return read(field<U::FObjectProperty>(function, name), args) == expected;
        };
        if (broadcast->gold_bag && target == find<U::UObject>(code_library) &&
            function == find<U::UFunction>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C:GlobalPing")))
        {
            auto* key = field<U::FStrProperty>(function, STR("Ping_ID"));
            if (!key || !is(STR("Sending_Actor"), broadcast->host) || !is(STR("__WorldContext"), broadcast->script)) return;
            const auto id = key->GetPropertyValue(key->ContainerPtrToValuePtr<void>(args));
            broadcast->ping |= std::basic_string_view<U::TCHAR>{*id} == STR("PickedUp_GoldBag");
        }
        if (target != broadcast->receiver) return;
        if (broadcast->gold_bag)
        {
            auto* powered = field<U::FBoolProperty>(function, STR("Powered"));
            if (function == find<U::UFunction>(STR("/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C:Send_PowerSignal"))
                && powered && powered->GetPropertyValueInContainer(args) && is(STR("Instigator"), broadcast->host))
                broadcast->reaction = true;
        }
        else
        {
            auto* level = field<U::FIntProperty>(function, STR("Aggro_Level"));
            if (function == find<U::UFunction>(STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Set Agro on Target"))
                && level && level->GetPropertyValueInContainer(args) == 1 && is(STR("AgroSource"), broadcast->host))
                broadcast->reaction = true;
        }
    }

    U::UObject* cabinet_client_pawn(U::UObject* actor, U::UFunction* function, const void* args)
    {
        if (!args || !live(actor)) return nullptr;
        const auto path = actor->GetPathName();
        const auto entry = std::ranges::find_if(cabinet_inputs, [&](const CabinetInput& value) {
            return path == value.actor;
        });
        if (entry == cabinet_inputs.end() || !env("SILVERFISH_COOP_NATIVE_INTERACT_INPUT") || !exact(actor, entry->actor_class) ||
            function != find<U::UFunction>(entry->callback) || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !actor->HasAnyFlags(U::RF_WasLoaded) || !boolean(actor, STR("bReplicates"), false) ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return nullptr;

        auto* salt = field<U::FIntProperty>(actor, STR("SpawnSalt"));
        auto* player = parameter<U::FObjectProperty>(function, STR("Player"), 0);
        auto* hit = parameter<U::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        auto* box = object(actor, STR("Box"));
        if (!salt || salt->GetPropertyValueInContainer(actor) != entry->salt || !player || !hit ||
            player->GetPropertyClass().Get() != find<U::UClass>(pawn_path) ||
            hit->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.SceneComponent")) ||
            !exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != actor ||
            read(hit, args) != box) return nullptr;
        auto* pawn = read(player, args);
        auto* placed = static_cast<U::AActor*>(actor);
        for (const auto& scope : scopes)
        {
            auto* world = scope.world.Get();
            if (scope.profile && scope.profile->source == entry->source && !scope.host && scope.pawn.Get() == pawn &&
                local_scope(scope) && placed->GetWorld() == world && placed->GetLevel() == object(world, STR("PersistentLevel")))
                return pawn;
        }
        return nullptr;
    }

    void intercept_floor_pickup(Iteration& call, U::UObject* screen, U::UFunction* function, const void* args);

    void intercept(Iteration& call, U::UObject* context, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!ready() || !context || !function || call.OriginalFunctionCallPrevented()) return;
        observe_pickup_broadcast(context, function, args);
        static const U::FName interact_name{STR("SendInteractionToActor")};
        static const U::FName put_name{STR("PutInInventory")};
        static const U::FName pickup_name{STR("PickUp_Object")};
        static const U::FName add_name{STR("AddItemToInventory")};
        static const U::FName remove_name{STR("RemoveItem")};
        const auto name = function->GetFName();
        if (name == remove_name)
        {
            intercept_floor_pickup(call, context, function, args);
            return;
        }
        if (name != interact_name && name != put_name && name != pickup_name && name != add_name) return;
        if (name == interact_name)
        {
            if (auto* pawn = cabinet_client_pawn(context, function, args))
            {
                call.PreventOriginalFunctionCall();
                if (call.OriginalFunctionCallPrevented()) silverfish::native_interaction_input::consume_current_press(pawn);
                return;
            }
        }
        if (name == interact_name && function == find<U::UFunction>(secure_locker_input) &&
            exact(context, secure_locker_class) && context->GetPathName() == trench_savlo_locker)
        {
            for (const auto& scope : scopes)
            {
                if (scope.profile && scope.profile->source == "trench-savlo-keycard" && !scope.host && local_scope(scope) &&
                    static_cast<U::AActor*>(context)->GetWorld() == scope.world.Get())
                {
                    call.PreventOriginalFunctionCall();
                    return;
                }
            }
        }
        if (name == add_name)
        {
            if (function != find<U::UFunction>(add_path)) return;
            auto* item = read(parameter<U::FObjectProperty>(function, STR("Item"), 0), args);
            const auto* scope = actor_scope(item);
            if ((scope && context == object(scope->world.Get(), STR("OwningGameInstance"))) ||
                (drop_fence(item) && context == object(static_cast<U::AActor*>(item)->GetWorld(), STR("OwningGameInstance"))))
                call.PreventOriginalFunctionCall();
            return;
        }
        auto* scope = actor_scope(context);
        if (!scope)
        {
            // Stock waits half a second before hiding a dormant bag. Veto its exact
            // placed pickup callbacks even before Lua has a qualified input lease.
            if (function->GetOuterPrivate() == find<U::UClass>(base_class) && drop_fence(context))
                call.PreventOriginalFunctionCall();
            return;
        }
        const bool cash_callback = scope->profile->stock_cash && name == interact_name && function == interaction(true);
        if (!cash_callback && function->GetOuterPrivate() != find<U::UClass>(base_class)) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || name != interact_name || function != interaction(scope->profile->stock_cash)) return;

        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), args);
        auto* hit = read(field<U::FObjectProperty>(function, STR("HitComponent")), args);
        if (!admit_input(*scope, pawn, hit)) return;
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        const bool fixture = !frame && fixture_scope == scope;
        const bool valid_caller = !frame ||
            (caller && caller->Object() == pawn && caller->Node() == find<U::UFunction>(pawn_script));
        const bool stock = !fixture && valid_caller && object(pawn, STR("InteractionTarget_Component")) == hit &&
            boolean(pawn, STR("Holding_Interact"), true) &&
            silverfish::native_interaction_input::consume_current_press(pawn);
        if (fixture || stock) scope->observed = {Clock::now() + std::chrono::seconds(4), ++sequence, fixture, stock};
    }

    U::UObject* live_address(Address pointer, U::UClass* type)
    {
        U::UObject* result{};
        if (!pointer || !type) return nullptr;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != pointer) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == type) result = candidate;
            return RC::LoopAction::Break;
        });
        return result;
    }

    U::UObject* local_pickup_pawn(Address pointer, bool host)
    {
        // Loose pickup discovery already qualified this local seat. Callers still check their live scope.
        auto* type = find<U::UClass>(pawn_path);
        auto* pawn = loose_pawn.Get();
        auto* world = loose_world.Get();
        if (loose_host == host && address(pawn) == pointer && live(pawn) && live(world) &&
            pawn->GetClassPrivate() == type && static_cast<U::AActor*>(pawn)->GetWorld() == world)
            return pawn;
        return live_address(pointer, type);
    }

    bool authorized(const Scope& scope, U::UObject* pawn, U::UObject* target)
    {
        if (!scope.host || !available(scope) || !exact(pawn, pawn_path) || role(pawn) != 3 ||
            !exact(target, save_path) || object(pawn, STR("Save")) != target || !boolean(pawn, STR("Dead"), false) ||
            role(scope.actor.Get()) != 3 || !in_range(pawn, scope.actor.Get())) return false;
        auto* world = scope.world.Get();
        auto* controller = object(pawn, STR("Controller"));
        auto* canonical = object(object(world, STR("OwningGameInstance")), STR("SaveSlot_Ref"));
        if (!exact(canonical, save_path) || !exact(controller, controller_path) || role(controller) != 3 ||
            object(controller, STR("Pawn")) != pawn || static_cast<U::AActor*>(pawn)->GetWorld() != world ||
            static_cast<U::AActor*>(controller)->GetWorld() != world) return false;
        if (pawn == scope.pawn.Get()) return target == canonical && controller == scope.controller.Get();
        auto* player = object(controller, STR("Player"));
        return target != canonical && connection(scope.driver.Get(), player) && object(player, STR("PlayerController")) == controller;
    }

    struct RowSchema
    {
        U::UClass* save = find<U::UClass>(save_path);
        U::UScriptStruct* type = find<U::UScriptStruct>(inventory_path);
        U::FArrayProperty* array = field<U::FArrayProperty>(save, STR("PlayerBackpack"));
        U::FStructProperty* row = array ? U::CastField<U::FStructProperty>(array->GetInner()) : nullptr;
        U::FClassProperty* klass = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FStructProperty* payload = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        U::FStructProperty* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        U::FIntProperty* ammo = field<U::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        std::array<U::FIntProperty*, 4> words{};

        bool bind()
        {
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            if (!type || !row || row->GetStruct().Get() != type || !guid || !guid_type || guid->GetStruct().Get() != guid_type ||
                !klass || klass->GetMetaClass().Get() != find<U::UClass>(base_class) || !payload ||
                payload->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"))) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                words[index] = field<U::FIntProperty>(guid_type, guid_members[index]);
                if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * 4)) return false;
            }
            return true;
        }

        static bool same(U::FProperty* property, const void* first, const void* second)
        {
            return property->Identical(property->ContainerPtrToValuePtr<void>(first),
                                       property->ContainerPtrToValuePtr<void>(second));
        }

        bool identical(const void* first, const void* second) const
        {
            return row->Identical(first, second) && same(payload, first, second);
        }

        Guid read_guid(const void* data) const
        {
            Guid result{};
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return result;
        }

        Guid identity(const void* value) const
        {
            return read_guid(guid->ContainerPtrToValuePtr<void>(value));
        }

        void set_identity(void* value, const Guid& identity) const
        {
            auto* data = guid->ContainerPtrToValuePtr<void>(value);
            for (std::size_t index = 0; index < words.size(); ++index)
                words[index]->SetPropertyValueInContainer(data, std::bit_cast<std::int32_t>(identity[index]));
        }

        bool authored(const void* value, U::UClass* item_class) const
        {
            return item_class && read(klass, value) == item_class && identity(value) != Guid{};
        }

        template<class T>
        bool matches(const void* value, U::UObject* actor, const U::TCHAR* row_name, const U::TCHAR* actor_name) const
        {
            auto* left = field<T>(type, row_name);
            auto* right = field<T>(actor, actor_name);
            return left && right && left->GetPropertyValueInContainer(value) == right->GetPropertyValueInContainer(actor);
        }

        template<class T>
        bool copy_actor_field(void* row_value, U::UObject* actor, const U::TCHAR* row_name, const U::TCHAR* actor_name) const
        {
            auto* target = field<T>(type, row_name);
            auto* source = field<T>(actor, actor_name);
            if (!target || !source) return false;
            target->SetPropertyValueInContainer(row_value, source->GetPropertyValueInContainer(actor));
            return true;
        }

        bool matches_actor(const void* value, U::UObject* actor, std::optional<int> display_count = {}) const
        {
            auto* actor_payload = field<U::FStructProperty>(actor, STR("DataStruct"));
            return authored(value, actor->GetClassPrivate()) && actor_payload && actor_payload->GetStruct().Get() == payload->GetStruct().Get() &&
                (display_count ? ammo && ammo->GetPropertyValueInContainer(value) == *display_count :
                    matches<U::FIntProperty>(value, actor, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"), STR("Ammo"))) &&
                matches<U::FDoubleProperty>(value, actor, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"), STR("Weight")) &&
                matches<U::FIntProperty>(value, actor, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"), STR("Value")) &&
                matches<U::FNameProperty>(value, actor, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"), STR("DataTag")) &&
                matches<U::FBoolProperty>(value, actor, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"), STR("NeverDrop")) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(value), actor_payload->ContainerPtrToValuePtr<void>(actor));
        }
    };

    U::UObject* isolated(Address pointer, const RowSchema& rows)
    {
        auto* save = live_address(pointer, rows.save);
        if (!save || !save->GetOuterPrivate() || save->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient")) return nullptr;
        auto* instance = find<U::UClass>(instance_path);
        auto* pawn = find<U::UClass>(pawn_path);
        if (!instance || !pawn) return nullptr;
        bool attached = false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object_ptr, int, int) {
            if (!live(object_ptr)) return RC::LoopAction::Continue;
            const auto* kind = object_ptr->GetClassPrivate();
            attached = (kind == pawn && object(object_ptr, STR("Save")) == save) ||
                (kind == instance && object(object_ptr, STR("SaveSlot_Ref")) == save);
            return attached ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        return attached ? nullptr : save;
    }

    struct FloorSelection
    {
        U::UObject* actor{};
        bool matching_row{};
    };

    constexpr auto inventory_screen = STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C");

    bool make_projected_floor_row(const RowSchema& rows, void* item, U::UObject* actor, std::optional<int> count)
    {
        auto* library = find<U::UObject>(STR("/Script/Engine.Default__KismetGuidLibrary"));
        auto* fn = find<U::UFunction>(STR("/Script/Engine.KismetGuidLibrary:NewGuid"));
        auto* guid_output = fn ? U::CastField<U::FStructProperty>(fn->GetReturnProperty()) : nullptr;
        auto* payload = field<U::FStructProperty>(actor, STR("DataStruct"));
        if (!library || !guid_output || !payload || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            !fn->HasAnyFunctionFlags(U::FUNC_Native) || fn->GetNumParms() != 1 || fn->GetParmsSize() != sizeof(Guid) ||
            guid_output->GetOffset_Internal() != 0 || guid_output->GetStruct().Get() != rows.guid->GetStruct().Get() ||
            payload->GetStruct().Get() != rows.payload->GetStruct().Get()) return false;
        Guid key{};
        library->ProcessEvent(fn, key.data());
        if (key == Guid{}) return false;

        rows.set_identity(item, key);
        rows.klass->SetObjectPropertyValue(rows.klass->ContainerPtrToValuePtr<void>(item), actor->GetClassPrivate());
        rows.ammo->SetPropertyValueInContainer(item, count.value_or(0));
        rows.payload->CopyCompleteValue(rows.payload->ContainerPtrToValuePtr<void>(item), payload->ContainerPtrToValuePtr<void>(actor));
        return rows.copy_actor_field<U::FDoubleProperty>(item, actor, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"), STR("Weight")) &&
            rows.copy_actor_field<U::FIntProperty>(item, actor, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"), STR("Value")) &&
            rows.copy_actor_field<U::FNameProperty>(item, actor, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"), STR("DataTag")) &&
            rows.copy_actor_field<U::FBoolProperty>(item, actor, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"), STR("NeverDrop")) &&
            rows.matches_actor(item, actor, count);
    }

    void complete_floor_projection(U::UObject* screen, U::UFunction* function)
    {
        static const U::FName find_items{STR("FindFloorItems")};
        if (!function || function->GetFName() != find_items || !ready() || !live(screen) ||
            !exact(screen, inventory_screen) || function->GetOuterPrivate() != screen->GetClassPrivate() ||
            function->GetNumParms() != 0 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* pawn = object(screen, STR("PlayerFP"));
        if (!pawn || silverfish::native_interaction_input::current_local_pawn(address(pawn)) != pawn) return;
        auto* items_property = field<U::FArrayProperty>(screen, STR("FloorItems"));
        auto* actors_property = field<U::FArrayProperty>(screen, STR("FloorActors"));
        auto* item_property = items_property ? U::CastField<U::FStructProperty>(items_property->GetInner()) : nullptr;
        auto* actor_property = actors_property ? U::CastField<U::FObjectProperty>(actors_property->GetInner()) : nullptr;
        if (!item_property || !actor_property) return;
        U::FScriptArrayHelper items(items_property, items_property->ContainerPtrToValuePtr<void>(screen));
        U::FScriptArrayHelper actors(actors_property, actors_property->ContainerPtrToValuePtr<void>(screen));
        if (items.Num() != actors.Num() || items.Num() > 2048) return;
        RowSchema rows;
        if (item_property->GetStruct().Get() != rows.type || !rows.ammo || !rows.bind()) return;
        std::vector<U::UObject*> shown;
        shown.reserve(actors.Num());
        for (int i = 0; i < items.Num(); ++i)
        {
            auto* actor = actor_property->GetObjectPropertyValue(actors.GetRawPtr(i));
            shown.push_back(actor);
            const auto count = silverfish::native_guest_drop_projection::nearby_stack_count(actor, pawn);
            auto* item = items.GetRawPtr(i);
            if (count && rows.matches_actor(item, actor))
                rows.ammo->SetPropertyValueInContainer(item, *count);
        }
        // Some projected weapons do not enter stock FindFloorItems because their display
        // mesh has no collision. Add only in-reach projections with clear stock visibility.
        for (auto* actor : silverfish::native_guest_drop_projection::visible_nearby_actors(pawn, shown))
        {
            if (items.Num() >= 2048) break;
            const auto count = silverfish::native_guest_drop_projection::nearby_stack_count(actor, pawn);
            const auto index = items.AddValue();
            if (make_projected_floor_row(rows, items.GetRawPtr(index), actor, count))
                actor_property->SetObjectPropertyValue(actors.GetRawPtr(actors.AddValue()), actor);
            else
                items.RemoveValues(index, 1);
        }
    }

    FloorSelection nearby_selection(U::UObject* screen, const RowSchema& rows, const Guid& wanted)
    {
        if (wanted == Guid{}) return {};

        auto* items_property = field<U::FArrayProperty>(screen, STR("FloorItems"));
        auto* actors_property = field<U::FArrayProperty>(screen, STR("FloorActors"));
        auto* item_property = items_property ? U::CastField<U::FStructProperty>(items_property->GetInner()) : nullptr;
        auto* actor_property = actors_property ? U::CastField<U::FObjectProperty>(actors_property->GetInner()) : nullptr;
        if (!item_property || item_property->GetStruct().Get() != rows.type || !actor_property) return {};
        U::FScriptArrayHelper items(items_property, items_property->ContainerPtrToValuePtr<void>(screen));
        U::FScriptArrayHelper actors(actors_property, actors_property->ContainerPtrToValuePtr<void>(screen));
        if (items.Num() != actors.Num() || items.Num() > 2048) return {};

        for (int i = 0; i < items.Num(); ++i)
        {
            if (rows.identity(items.GetRawPtr(i)) != wanted) continue;
            auto* actor = actor_property->GetObjectPropertyValue(actors.GetRawPtr(i));
            const auto count = silverfish::native_guest_drop_projection::nearby_stack_count(
                actor, object(screen, STR("PlayerFP")));
            return {actor, live(actor) && rows.matches_actor(items.GetRawPtr(i), actor, count)};
        }
        return {};
    }

    U::UObject* nearby_viewer(U::UObject* screen)
    {
        auto* pawn = object(screen, STR("PlayerFP"));
        auto* controller = object(pawn, STR("Controller"));
        auto* property = field<U::FArrayProperty>(controller, STR("MenuStack"));
        auto* element = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!element) return nullptr;
        U::FScriptArrayHelper stack(property, property->ContainerPtrToValuePtr<void>(controller));
        return stack.Num() && element->GetObjectPropertyValue(stack.GetRawPtr(stack.Num() - 1)) == screen ? pawn : nullptr;
    }

    Scope* loose_floor_scope(U::UObject* actor, U::UObject* pawn)
    {
        auto* scope = actor_scope(actor);
        return scope && scope->pawn.Get() == pawn && scope->profile->discovered && !scope->profile->stock_cash
            ? scope : nullptr;
    }

    bool enqueue_nearby(Scope& scope, U::UObject* pawn)
    {
        if (!admit_input(scope, pawn, scope.box.Get())) return false;
        scope.observed = {Clock::now() + std::chrono::seconds(4), ++sequence, false, true};
        return true;
    }

    void intercept_floor_pickup(Iteration& call, U::UObject* screen, U::UFunction* function, const void* args)
    {
        constexpr auto remove_path = STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C:RemoveItem");
        if (!args || !live(screen) || !exact(screen, inventory_screen) || function != find<U::UFunction>(remove_path) ||
            function->GetOuterPrivate() != screen->GetClassPrivate() || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;

        auto* key = parameter<U::FStructProperty>(function, STR("ItemGuid"), 0);
        auto* inventory = parameter<U::FIntProperty>(function, STR("Inventory"), 0x10);
        if (!key || key->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid")) ||
            !inventory || inventory->GetPropertyValueInContainer(args) != 0) return;

        RowSchema rows;
        if (!rows.bind()) return;
        const auto selected = nearby_selection(screen, rows, rows.read_guid(key->ContainerPtrToValuePtr<void>(args)));
        const bool projected = silverfish::native_guest_drop_projection::owns_actor(selected.actor);
        auto* pawn = nearby_viewer(screen);
        auto* scope = !projected && pawn ? loose_floor_scope(selected.actor, pawn) : nullptr;
        if (!projected && !scope) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || !selected.matching_row || !pawn) return;
        if (projected) silverfish::native_guest_drop_projection::queue_nearby_pickup(selected.actor, pawn);
        else enqueue_nearby(*scope, pawn);
    }

    const char* convert(const RowSchema& rows, U::UObject* pawn, U::UObject* actor, U::UObject* scratch, Guid& guid)
    {
        auto* fn = find<U::UFunction>(converter_path);
        auto* library = find<U::UObject>(library_path);
        auto* item = parameter<U::FObjectProperty>(fn, STR("ItemActor"), 0);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), sizeof(void*));
        auto* output = parameter<U::FStructProperty>(fn, STR("Item"), 2 * sizeof(void*), true);
        if (!fn || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            fn->GetNumParms() != 3 || fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !item ||
            item->GetPropertyClass().Get() != find<U::UClass>(base_class) || !context || !context->GetPropertyClass().Get() ||
            !pawn->IsA(context->GetPropertyClass().Get()) || !output || output->GetStruct().Get() != rows.type)
            return "stock_item_conversion_schema_unavailable";
        U::FScriptArrayHelper array(rows.array, rows.array->ContainerPtrToValuePtr<void>(scratch));
        if (array.Num() != 0) return "empty_pickup_escrow_required";

        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        auto* storage = buffer.data();
        output->InitializeValue_InContainer(storage);
        const auto destroy = [output](std::byte* memory) { output->DestroyValue_InContainer(memory); };
        const std::unique_ptr<std::byte, decltype(destroy)> lifetime(storage, destroy);
        item->SetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(storage), actor);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(storage), pawn);
        library->ProcessEvent(fn, storage);
        const auto* generated = output->ContainerPtrToValuePtr<void>(storage);
        if (array.Num() != 0 || !rows.matches_actor(generated, actor)) return "stock_item_row_unverified";
        const auto index = array.AddValue();
        if (index != 0 || array.Num() != 1) return "stock_item_escrow_append_unverified";
        auto* row = array.GetRawPtr(0);
        rows.row->CopyCompleteValue(row, generated);
        if (!rows.identical(row, generated))
        {
            array.RemoveValues(0, 1);
            return "stock_item_escrow_payload_unverified";
        }
        guid = rows.identity(row);
        return "captured";
    }

    int result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    bool parse_guid(std::string_view text, Guid& identity)
    {
        if (text.size() != 32 || text.find_first_not_of("0123456789ABCDEF") != std::string_view::npos) return false;
        for (std::size_t index = 0; index < identity.size(); ++index)
        {
            const auto* part = text.data() + index * 8;
            const auto parsed = std::from_chars(part, part + 8, identity[index], 16);
            if (parsed.ec != std::errc{} || parsed.ptr != part + 8) return false;
        }
        return identity != Guid{};
    }

    struct TapeStores
    {
        const RowSchema& rows;
        U::FArrayProperty* equipment = field<U::FArrayProperty>(rows.save, STR("PlayerEquipment"));
        U::FStructProperty* armor = field<U::FStructProperty>(rows.save, STR("PlayerArmor"));
        U::FStructProperty* helmet = field<U::FStructProperty>(rows.save, STR("PlayerHelmet"));

        bool valid() const
        {
            auto* equipment_row = equipment ? U::CastField<U::FStructProperty>(equipment->GetInner()) : nullptr;
            return equipment_row && equipment_row->GetStruct().Get() == rows.type &&
                armor && armor->GetStruct().Get() == rows.type && helmet && helmet->GetStruct().Get() == rows.type;
        }

        struct Found
        {
            int count{};
            void* backpack_row{};
        };

        bool scan(U::UObject* save, const Guid& id, Found& found) const
        {
            found = {};
            for (auto* property : {rows.array, equipment})
            {
                U::FScriptArrayHelper items(property, property->ContainerPtrToValuePtr<void>(save));
                if (items.Num() < 0 || items.Num() > 1024) return false;
                for (int index = 0; index < items.Num(); ++index)
                {
                    auto* row = items.GetRawPtr(index);
                    if (rows.identity(row) != id) continue;
                    ++found.count;
                    if (property == rows.array) found.backpack_row = row;
                }
            }
            for (auto* property : {armor, helmet})
                if (rows.identity(property->ContainerPtrToValuePtr<void>(save)) == id) ++found.count;
            return true;
        }
    };

    constexpr int max_supply_backpack = 1024;

    struct FreeValue
    {
        U::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using StackValue = std::unique_ptr<void, FreeValue>;

    StackValue copy_value(U::FProperty* property, const void* original)
    {
        StackValue value(property->AllocateAndInitializeValue(), {property});
        if (value) property->CopyCompleteValue(value.get(), original);
        return value;
    }

    bool same_backpack(const RowSchema& rows, const void* first, const void* second)
    {
        U::FScriptArrayHelper a(rows.array, first);
        U::FScriptArrayHelper b(rows.array, second);
        if (a.Num() < 0 || a.Num() > max_supply_backpack || a.Num() != b.Num() ||
            (a.Num() && a.GetRawPtr(0) == b.GetRawPtr(0))) return false;
        for (int index = 0; index < a.Num(); ++index)
            if (!rows.identical(a.GetRawPtr(index), b.GetRawPtr(index))) return false;
        return true;
    }

    struct StockStackValues
    {
        U::FIntProperty* quantity{};
        U::FIntProperty* value{};
        U::FDoubleProperty* weight{};
        int unit_value{};
        double unit_weight{};

        bool bind(const RowSchema& rows, U::UClass* type, const Profile& profile)
        {
            auto* defaults = type->GetClassDefaultObject().Get();
            auto* stock_value = field<U::FIntProperty>(defaults, STR("Value"));
            auto* stock_weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
            quantity = field<U::FIntProperty>(rows.type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
            value = field<U::FIntProperty>(rows.type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
            weight = field<U::FDoubleProperty>(rows.type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
            if (!stock_value || !stock_weight || !quantity || !value || !weight ||
                !defaults->HasAnyFlags(U::RF_ClassDefaultObject) || defaults->GetClassPrivate() != type ||
                !boolean(defaults, STR("Stacks"), true)) return false;
            unit_value = stock_value->GetPropertyValueInContainer(defaults);
            unit_weight = stock_weight->GetPropertyValueInContainer(defaults);
            return unit_value > 0 && unit_weight > 0 && std::isfinite(unit_weight) &&
                units_safe(*profile.stack_units) && unit_value * *profile.stack_units == profile.stack_value &&
                std::abs(unit_weight * *profile.stack_units - profile.stack_weight) <= 1e-9;
        }

        bool units_safe(int units) const
        {
            return units > 0 && units <= (std::numeric_limits<int>::max)() / unit_value &&
                std::isfinite(units * unit_weight);
        }

        bool matches(const void* row, int units) const
        {
            return units_safe(units) && quantity->GetPropertyValueInContainer(row) == units &&
                value->GetPropertyValueInContainer(row) == units * unit_value &&
                std::abs(weight->GetPropertyValueInContainer(row) - units * unit_weight) <= 1e-9;
        }

        void write(void* row, int units) const
        {
            quantity->SetPropertyValueInContainer(row, units);
            value->SetPropertyValueInContainer(row, units * unit_value);
            weight->SetPropertyValueInContainer(row, units * unit_weight);
        }
    };

    int merge_supply(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!ready() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5)) return fail("authored_supply_merge_arguments");
        const auto pawn_address = static_cast<Address>(lua.get_integer());
        const auto save_address = static_cast<Address>(lua.get_integer());
        const auto escrow_address = static_cast<Address>(lua.get_integer());
        auto* scope = select_source(lua);
        Guid source_id{};
        if (!parse_guid(lua.get_string(), source_id)) return fail("authored_supply_merge_source_guid_unqualified");
        RowSchema rows;
        TapeStores stores{rows};
        auto* pawn = live_address(pawn_address, find<U::UClass>(pawn_path));
        auto* target = object(pawn, STR("Save"));
        if (!scope || !scope->profile || !scope->profile->stacked_supply() || !scope->profile->stack_units ||
            address(target) != save_address || !authorized(*scope, pawn, target))
            return fail("authored_supply_merge_authority_unavailable");
        const auto& profile = *scope->profile;
        if (scope->merged_source != Guid{}) return fail("mutation_unverified_authored_supply_merge_reentered");
        auto* world = scope->world.Get();
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* canonical = object(instance, STR("SaveSlot_Ref"));
        const auto owner_current = [&] {
            return authorized(*scope, pawn, target) && object(world, STR("OwningGameInstance")) == instance &&
                object(instance, STR("SaveSlot_Ref")) == canonical &&
                (target == canonical || silverfish::native_guest_regen::qualifies_detached_guest(pawn, target, canonical));
        };
        if (!owner_current() || !rows.bind() || !stores.valid())
            return fail("authored_supply_merge_inventory_unqualified");
        auto* escrow = isolated(escrow_address, rows);
        auto* actor = scope->actor.Get();
        if (!escrow || escrow == target || !actor) return fail("authored_supply_merge_escrow_unavailable");
        auto* type = actor->GetClassPrivate();
        auto* original_data = rows.array->ContainerPtrToValuePtr<void>(target);
        auto* escrow_data = rows.array->ContainerPtrToValuePtr<void>(escrow);
        U::FScriptArrayHelper carried(rows.array, original_data);
        U::FScriptArrayHelper source(rows.array, escrow_data);
        if (carried.Num() < 0 || carried.Num() > max_supply_backpack || source.Num() != 1 ||
            (carried.Num() && source.GetRawPtr(0) == carried.GetRawPtr(0)) ||
            rows.identity(source.GetRawPtr(0)) != source_id || !rows.matches_actor(source.GetRawPtr(0), actor))
            return fail("authored_supply_merge_escrow_unqualified");

        int index = 0;
        while (index < carried.Num() && read(rows.klass, carried.GetRawPtr(index)) != type) ++index;
        if (index == carried.Num())
        {
            U::FScriptArrayHelper equipment(stores.equipment, stores.equipment->ContainerPtrToValuePtr<void>(target));
            if (equipment.Num() < 0 || equipment.Num() > max_supply_backpack)
                return fail("authored_supply_merge_inventory_unqualified");
            for (int slot = 0; slot < equipment.Num(); ++slot)
                if (read(rows.klass, equipment.GetRawPtr(slot)) == type)
                    return fail("authored_supply_merge_equipped_stack_unqualified");
            return result(lua, true, "append");
        }

        auto* selected = carried.GetRawPtr(index);
        const auto stack_id = rows.identity(selected);
        TapeStores::Found incoming, owned;
        if (stack_id == Guid{} || stack_id == source_id ||
            !stores.scan(target, source_id, incoming) || incoming.count != 0 ||
            !stores.scan(target, stack_id, owned) || owned.count != 1 || owned.backpack_row != selected)
            return fail("authored_supply_merge_guid_conflict");
        StockStackValues stats;
        if (!stats.bind(rows, type, profile)) return fail("authored_supply_merge_stock_values_unqualified");
        const int before = stats.quantity->GetPropertyValueInContainer(selected);
        const int difference = *profile.stack_units;
        if (!stats.matches(selected, before) || !stats.matches(source.GetRawPtr(0), difference) ||
            before > (std::numeric_limits<int>::max)() - difference || !stats.units_safe(before + difference))
            return fail("authored_supply_merge_stack_size_unqualified");
        const int after = before + difference;

        std::array<U::UObject*, 4> protected_saves{};
        std::size_t count = 0;
        if (target != canonical) protected_saves[count++] = canonical;
        bool distinct = true;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (candidate == pawn || !exact(candidate, pawn_path) || role(candidate) != 3 ||
                static_cast<U::AActor*>(candidate)->GetWorld() != world) return RC::LoopAction::Continue;
            auto* other = object(candidate, STR("Save"));
            if (!exact(other, save_path) || other == target || other == escrow)
            {
                distinct = false;
                return RC::LoopAction::Break;
            }
            if (std::ranges::find(protected_saves, other) != protected_saves.end()) return RC::LoopAction::Continue;
            if (count == protected_saves.size())
            {
                distinct = false;
                return RC::LoopAction::Break;
            }
            protected_saves[count++] = other;
            return RC::LoopAction::Continue;
        });
        if (!distinct) return fail("authored_supply_merge_save_alias");

        std::array<StackValue, 4> frozen_saves{};
        for (std::size_t position = 0; position < count; ++position)
        {
            auto* other = protected_saves[position];
            auto* other_data = rows.array->ContainerPtrToValuePtr<void>(other);
            U::FScriptArrayHelper other_rows(rows.array, other_data);
            TapeStores::Found other_id;
            if (!stores.scan(other, stack_id, other_id) || other_id.count ||
                !stores.scan(other, source_id, other_id) || other_id.count ||
                other_rows.Num() < 0 || other_rows.Num() > max_supply_backpack ||
                (other_rows.Num() && (other_rows.GetRawPtr(0) == carried.GetRawPtr(0) ||
                                      other_rows.GetRawPtr(0) == source.GetRawPtr(0))))
                return fail("authored_supply_merge_other_owner_conflict");
            frozen_saves[position] = copy_value(rows.array, other_data);
            if (!frozen_saves[position] || !same_backpack(rows, frozen_saves[position].get(), other_data))
                return fail("authored_supply_merge_snapshot_unavailable");
        }
        auto frozen = copy_value(rows.array, original_data);
        auto proposed = copy_value(rows.array, original_data);
        auto frozen_escrow = copy_value(rows.array, escrow_data);
        auto* equipment = stores.equipment->ContainerPtrToValuePtr<void>(target);
        auto frozen_equipment = copy_value(stores.equipment, equipment);
        auto* armor = stores.armor->ContainerPtrToValuePtr<void>(target);
        auto frozen_armor = copy_value(stores.armor, armor);
        auto* helmet = stores.helmet->ContainerPtrToValuePtr<void>(target);
        auto frozen_helmet = copy_value(stores.helmet, helmet);
        if (!frozen || !proposed || !frozen_escrow || !frozen_equipment || !frozen_armor || !frozen_helmet ||
            !same_backpack(rows, frozen.get(), original_data) || !same_backpack(rows, proposed.get(), original_data))
            return fail("authored_supply_merge_snapshot_unavailable");
        const auto unchanged = [&] {
            return same_backpack(rows, frozen_escrow.get(), escrow_data) &&
                stores.equipment->Identical(frozen_equipment.get(), equipment) &&
                rows.identical(frozen_armor.get(), armor) && rows.identical(frozen_helmet.get(), helmet) &&
                [&] {
                    for (std::size_t position = 0; position < count; ++position)
                        if (!same_backpack(rows, frozen_saves[position].get(),
                                           rows.array->ContainerPtrToValuePtr<void>(protected_saves[position]))) return false;
                    return true;
                }();
        };
        if (!unchanged()) return fail("authored_supply_merge_snapshot_unavailable");
        U::FScriptArrayHelper staged(rows.array, proposed.get());
        auto* staged_row = staged.GetRawPtr(index);
        stats.write(staged_row, after);
        const bool stock_agrees = silverfish::loot_escrow::stock_stack_matches(pawn, rows.row, selected, staged_row, difference);
        if (!unchanged() || !same_backpack(rows, frozen.get(), original_data))
            return fail("mutation_unverified_authored_supply_stock_calculation");
        if (!stock_agrees || !owner_current()) return fail("authored_supply_merge_stock_calculation_unqualified");
        rows.array->CopyCompleteValue(original_data, proposed.get());
        if (!owner_current() || !unchanged() || !same_backpack(rows, proposed.get(), original_data))
        {
            if (owner_current())
            {
                rows.array->CopyCompleteValue(original_data, frozen.get());
                if (unchanged() && same_backpack(rows, frozen.get(), original_data))
                    return fail("authored_supply_merge_rolled_back");
            }
            return fail("mutation_unverified_authored_supply_merge");
        }
        scope->merged_source = source_id;
        result(lua, true, "merged");
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", stack_id[0], stack_id[1], stack_id[2], stack_id[3]));
        lua.set_integer(before);
        lua.set_integer(after);
        return 5;
    }

    U::UWorld* tape_owner_world(U::UObject* pawn, U::UObject* target, U::UObject* canonical,
                              const TapeExchangeProfile& profile)
    {
        if (!pawn || !target || !canonical || !boolean(pawn, STR("Dead"), false) ||
            !boolean(pawn, STR("bActorIsBeingDestroyed"), false) || role(pawn) != 3 || object(pawn, STR("Save")) != target)
            return nullptr;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        auto* controller = object(pawn, STR("Controller"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        if (!live(world) || world->GetPathName() != profile.world ||
            !exact(instance, instance_path) || object(instance, STR("SaveSlot_Ref")) != canonical ||
            !exact(controller, controller_path) || !boolean(controller, STR("bActorIsBeingDestroyed"), false) ||
            role(controller) != 3 || object(controller, STR("Pawn")) != pawn ||
            static_cast<U::AActor*>(controller)->GetWorld() != world || !live(driver) || object(driver, STR("World")) != world ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")))
            return nullptr;
        auto* player = object(controller, STR("Player"));
        const bool owned = target == canonical ? local_player(controller, player) :
            connection(driver, player) && object(player, STR("PlayerController")) == controller &&
            object(player, STR("OwningActor")) == controller;
        return owned ? world : nullptr;
    }

    bool drill_station_unlocked(U::UObject* canonical, U::UObject* station)
    {
        const U::FName flag{STR("Mosby_DrillTest"), U::FNAME_Find};
        auto* flags = field<U::FMapProperty>(canonical, STR("Flags"));
        auto* key = flags ? U::CastField<U::FNameProperty>(flags->GetKeyProp()) : nullptr;
        auto* value = flags ? U::CastField<U::FIntProperty>(flags->GetValueProp()) : nullptr;
        if (flag.IsNone() || !key || !value || key->GetOffset_Internal() != 0 ||
            key->GetSize() != sizeof(U::FName) || value->GetSize() != sizeof(std::int32_t) ||
            !key->HasAnyPropertyFlags(U::CPF_HasGetValueTypeHash)) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        auto* map = flags->ContainerPtrToValuePtr<U::FScriptMap>(canonical);
        if (!map || map->Num() < 1 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > 4096) return false;
        const auto* found = map->FindValue(&flag, layout, [&](const void* raw) { return key->GetValueTypeHash(raw); },
            [&](const void* a, const void* b) { return key->Identical(a, b); });
        if (!found) return false;
        const auto stage = value->GetPropertyValue(found);
        if (stage < 1 || stage > 3) return false;

        auto* product = find<U::UClass>(drill_test_results_tape.product);
        auto* database = field<U::FArrayProperty>(station, STR("Tapes_DataBase"));
        auto* entry = database ? U::CastField<U::FClassProperty>(database->GetInner()) : nullptr;
        if (!product || !entry || !entry->GetMetaClass().Get() || !product->IsChildOf(entry->GetMetaClass().Get())) return false;
        U::FScriptArrayHelper items(database, database->ContainerPtrToValuePtr<void>(station));
        if (items.Num() < 1 || items.Num() > 16) return false;
        int matches = 0;
        for (int index = 0; index < items.Num(); ++index)
            if (entry->GetObjectPropertyValue(items.GetRawPtr(index)) == product) ++matches;
        return matches == 1;
    }

    bool tape_station_reachable(U::UWorld* world, U::UObject* pawn, U::UObject* canonical, const TapeExchangeProfile& profile)
    {
        auto* station = find<U::UObject>(profile.station);
        auto* box = object(station, STR("Box"));
        auto* access = &profile == &drill_instructions_tape
            ? silverfish::native_mindcore::qualified_instructions_cradle(world, canonical, station) : station;
        return exact(station, tape_station_class) && role(station) == 3 &&
            boolean(station, STR("bActorIsBeingDestroyed"), false) && exact(box, STR("/Script/Engine.BoxComponent")) &&
            box->GetOuterPrivate() == station && static_cast<U::AActor*>(station)->GetWorld() == world &&
            static_cast<U::AActor*>(station)->GetLevel() == object(world, STR("PersistentLevel")) && access && in_range(pawn, access) &&
            (&profile != &drill_test_results_tape || drill_station_unlocked(canonical, station));
    }

    bool exclusive_tape_ids(const TapeStores& stores, U::UWorld* world, U::UObject* pawn, U::UObject* target,
                            U::UObject* canonical, const Guid& blank, const Guid& filled)
    {
        const auto free = [&](U::UObject* save) {
            TapeStores::Found left, right;
            return stores.scan(save, blank, left) && stores.scan(save, filled, right) &&
                left.count == 0 && right.count == 0;
        };
        if (target != canonical && !free(canonical)) return false;
        auto* pawn_type = pawn->GetClassPrivate();
        bool unique = true;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* other, int, int) {
            if (other == pawn || !live(other) || other->GetClassPrivate() != pawn_type || role(other) != 3 ||
                static_cast<U::AActor*>(other)->GetWorld() != world) return RC::LoopAction::Continue;
            auto* controller = object(other, STR("Controller"));
            if (!exact(controller, controller_path) || object(controller, STR("Pawn")) != other)
                return RC::LoopAction::Continue;
            auto* save = object(other, STR("Save"));
            unique = save != target && (!exact(save, save_path) || save == canonical || free(save));
            return unique ? RC::LoopAction::Continue : RC::LoopAction::Break;
        });
        return unique;
    }

    template<const TapeExchangeProfile& Profile>
    int exchange_tape(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) {
            if constexpr (&Profile != &stone_maze_tape)
            {
                if (const auto at = reason.find("stone_maze"); at != std::string_view::npos)
                    return result(lua, false, std::format("{}{}{}", reason.substr(0, at),
                        Profile.reason_prefix, reason.substr(at + 10)));
            }
            return result(lua, false, reason);
        };
        if (!ready() || !env("SILVERFISH_COOP_NATIVE_ZONE") ||
            ((&Profile == &drill_test_results_tape || &Profile == &drill_instructions_tape) &&
                !env("SILVERFISH_COOP_NATIVE_GUEST_SAVE")) || lua.get_stack_size() != 5 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) ||
            !lua.is_string(4) || !lua.is_string(5)) return fail("stone_maze_tape_arguments");
        const auto pawn_address = lua.get_integer();
        const auto target_address = lua.get_integer();
        const auto canonical_address = lua.get_integer();
        Guid blank, filled;
        if (pawn_address <= 0 || target_address <= 0 || canonical_address <= 0 || !parse_guid(lua.get_string(), blank))
            return fail("stone_maze_tape_arguments");
        const auto supplied_guid = lua.get_string();
        if (!parse_guid(supplied_guid, filled) || blank == filled) return fail("stone_maze_tape_arguments");

        RowSchema rows;
        TapeStores stores{rows};
        if (!rows.bind() || !stores.valid()) return fail("stone_maze_tape_inventory_schema_unavailable");
        auto* pawn = live_address(static_cast<Address>(pawn_address), find<U::UClass>(pawn_path));
        auto* target = live_address(static_cast<Address>(target_address), rows.save);
        auto* canonical = live_address(static_cast<Address>(canonical_address), rows.save);
        auto* world = tape_owner_world(pawn, target, canonical, Profile);
        if (!world || (&Profile == &drill_instructions_tape && target == canonical) ||
            !tape_station_reachable(world, pawn, canonical, Profile) ||
            !exclusive_tape_ids(stores, world, pawn, target, canonical, blank, filled))
            return fail("stone_maze_exact_tape_owner_unavailable");

        auto* blank_class = find<U::UClass>(blank_tape_path);
        auto* filled_class = find<U::UClass>(Profile.product);
        auto* base = find<U::UClass>(base_class);
        auto* filled_cdo = filled_class ? filled_class->GetClassDefaultObject().Get() : nullptr;
        auto* blank_cdo = blank_class ? blank_class->GetClassDefaultObject().Get() : nullptr;
        auto* quantity = field<U::FIntProperty>(rows.type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        const auto stock_tape = [&](U::UObject* cdo, U::UClass* type) {
            auto* category = field<U::FByteProperty>(cdo, STR("ItemType"));
            return cdo && base && cdo->HasAnyFlags(U::RF_ClassDefaultObject) && cdo->GetClassPrivate() == type &&
                type->IsChildOf(base) && category && category->GetPropertyValueInContainer(cdo) == 9 &&
                boolean(cdo, STR("Stacks"), false);
        };
        TapeStores::Found old, replacement;
        if (!quantity || !stock_tape(blank_cdo, blank_class) || !stock_tape(filled_cdo, filled_class) ||
            !stores.scan(target, blank, old) || !stores.scan(target, filled, replacement))
            return fail("stock_stone_maze_tapes_unavailable");

        const auto success = [&](std::string_view status) {
            result(lua, true, status);
            lua.set_string(supplied_guid);
            return 3;
        };
        if (old.count == 0 && replacement.count == 1 && replacement.backpack_row &&
            rows.matches_actor(replacement.backpack_row, filled_cdo)) return success("already_exchanged");
        if (old.count != 1 || !old.backpack_row || !rows.authored(old.backpack_row, blank_class) ||
            quantity->GetPropertyValueInContainer(old.backpack_row) != 0 || replacement.count != 0)
            return fail("stone_maze_exact_blank_tape_required");

        auto* fn = find<U::UFunction>(default_item_path);
        auto* library = find<U::UObject>(library_path);
        auto* type = parameter<U::FClassProperty>(fn, STR("Item_Class"), 0);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), sizeof(void*));
        auto* output = parameter<U::FStructProperty>(fn, STR("Item"), 2 * sizeof(void*), true);
        if (!fn || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            fn->GetNumParms() != 3 || fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !type || type->GetMetaClass().Get() != base ||
            !context || !context->GetPropertyClass().Get() || !pawn->IsA(context->GetPropertyClass().Get()) ||
            !output || output->GetStruct().Get() != rows.type) return result(lua, false, "stock_tape_conversion_schema_unavailable");
        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        output->InitializeValue_InContainer(buffer.data());
        const auto release_output = [output](std::byte* memory) { output->DestroyValue_InContainer(memory); };
        const std::unique_ptr<std::byte, decltype(release_output)> initialized(buffer.data(), release_output);
        type->SetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(buffer.data()), filled_class);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(buffer.data()), pawn);
        library->ProcessEvent(fn, buffer.data());
        auto* generated = output->ContainerPtrToValuePtr<void>(buffer.data());
        if (!rows.matches_actor(generated, filled_cdo) || tape_owner_world(pawn, target, canonical, Profile) != world ||
            !tape_station_reachable(world, pawn, canonical, Profile) ||
            !stores.scan(target, blank, old) || old.count != 1 || !old.backpack_row ||
            !rows.authored(old.backpack_row, blank_class) || quantity->GetPropertyValueInContainer(old.backpack_row) != 0 ||
            !stores.scan(target, filled, replacement) || replacement.count != 0)
            return result(lua, false, "stock_filled_tape_unverified");
        rows.set_identity(generated, filled);

        const auto destroy_row = [&rows](void* value) { rows.row->DestroyAndFreeValue(value); };
        const std::unique_ptr<void, decltype(destroy_row)> original(rows.row->AllocateAndInitializeValue(), destroy_row);
        if (!original) return result(lua, false, "stock_blank_tape_backup_unavailable");
        auto* slot = old.backpack_row;
        rows.row->CopyCompleteValue(original.get(), slot);
        if (!rows.identical(original.get(), slot)) return result(lua, false, "stock_blank_tape_backup_unavailable");
        rows.row->CopyCompleteValue(slot, generated);
        if (stores.scan(target, blank, old) && old.count == 0 && stores.scan(target, filled, replacement) &&
            replacement.count == 1 && replacement.backpack_row == slot && rows.identical(slot, generated))
            return success("exchanged");
        rows.row->CopyCompleteValue(slot, original.get());
        return result(lua, false, rows.identical(slot, original.get()) ? "stock_filled_tape_copy_rolled_back" :
            "mutation_unverified_stone_maze_tape_copy");
    }

    bool authored_path(std::basic_string_view<U::TCHAR> path)
    {
        if (path == STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.MeleeWep_Spade_C_3") &&
            (env("SILVERFISH_COOP_NATIVE_FIXTURES") || env("SILVERFISH_COOP_NATIVE_HOST_DIG_LIVE_FIXTURES")) &&
            env("SILVERFISH_COOP_HEADLESS"))
            return true;
        return std::ranges::any_of(profiles, [path](const Profile& profile) {
            if (profile.source == "crossroads-old-pipe" && !(env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") &&
                env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS"))) return false;
            return path == profile.actor;
        });
    }

    enum class RespawnSeat { Inactive, Host, Guest };

    struct RespawnLoad
    {
        U::FWorldContext* context{};
        std::wstring package;
        U::FWeakObjectPtr driver, server;
        RespawnSeat seat{};
    } respawn_load;

    U::FWeakObjectPtr respawn_world;
    std::map<std::string, RC::StringType, std::less<>> pending_host_respawns;
    std::set<std::string> host_respawn_hidden;

    bool established_server(U::UObject* driver, U::UObject* connection)
    {
        return live(driver) && live(connection) && connection == object(driver, STR("ServerConnection")) &&
            object(connection, STR("Driver")) == driver;
    }

    void begin_respawn_load(U::FWorldContext& context, const U::FURL& url, U::UPendingNetGame* pending)
    {
        respawn_load = {};
        respawn_world.Reset();
        pending_host_respawns.clear();
        host_respawn_hidden.clear();
        if (url.Map.IsEmpty() || !silverfish::world_path::package(RC::StringViewType{*url.Map})) return;
        if (pending)
        {
            auto* request = reinterpret_cast<U::UObject*>(pending);
            if (!exact(request, STR("/Script/Engine.PendingNetGame"))) return;
            auto* driver = object(request, STR("NetDriver"));
            auto* server = object(driver, STR("ServerConnection"));
            if (established_server(driver, server))
                respawn_load = {&context, *url.Map, driver, server, RespawnSeat::Guest};
        }
        else if (std::ranges::any_of(url.Op, [](const auto& option) { return std::wstring_view{*option} == STR("listen"); }))
        {
            respawn_load = {&context, *url.Map, {}, {}, RespawnSeat::Host};
        }
    }

    RespawnSeat respawn_seat(U::UObject* world, std::wstring_view path)
    {
        auto* driver = object(world, STR("NetDriver"));
        const auto package = path.substr(0, path.rfind(L'.'));
        if (respawn_load.context && respawn_load.package == package)
        {
            if (respawn_load.seat == RespawnSeat::Host) return RespawnSeat::Host;
            auto* pending_driver = respawn_load.driver.Get();
            if ((!driver || driver == pending_driver) && established_server(pending_driver, respawn_load.server.Get()))
                return RespawnSeat::Guest;
        }
        if (!live(driver) || object(driver, STR("World")) != world) return RespawnSeat::Inactive;
        auto* server = object(driver, STR("ServerConnection"));
        if (established_server(driver, server)) return RespawnSeat::Guest;
        return server ? RespawnSeat::Inactive : RespawnSeat::Host;
    }

    void begin_loose_respawn(Iteration& call, U::UObject* context, U::UFunction* function)
    {
        static const U::FName respawn_name{STR("HandleRespawn")};
        if (!ready() || !function || function->GetFName() != respawn_name ||
            call.OriginalFunctionCallPrevented() || !live(context) || !context->HasAnyFlags(U::RF_WasLoaded)) return;
        auto* base = find<U::UClass>(base_class);
        if (!base || function->GetOuterPrivate() != base || function->GetNumParms() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !context->IsA(base)) return;
        auto* actor = static_cast<U::AActor*>(context);
        auto* mode = loose_respawn_mode(actor);
        if (!mode || (mode->GetPropertyValueInContainer(actor) == 0 &&
            !boolean(actor, STR("DespawnIfPlayerOwns"), true))) return;
        U::UObject* world = actor->GetWorld();
        if (auto* active = loose_world.Get(); active != world && in_loose_level(actor, active)) world = active;
        if (!in_loose_level(actor, world) || !exact(object(world, STR("OwningGameInstance")), instance_path)) return;
        const auto world_path = world->GetPathName();
        if (!silverfish::world_path::object(RC::StringViewType{world_path})) return;
        const auto seat = respawn_seat(world, world_path);
        if (seat == RespawnSeat::Inactive) return;
        const auto path = actor->GetPathName();
        const auto source = loose_source_key(path, world_path, actor->GetLevel() != object(world, STR("PersistentLevel")));
        const auto scope_path = RC::to_utf8_string(world_path);
        const auto item = actor->GetClassPrivate()->GetPathName();
        if (authored_path(path) || loose_actor_path(source, scope_path).empty() || !source_box(actor) ||
            !silverfish::content_paths::item_class(std::basic_string_view{item.data(), item.size()}) ||
            generic_payload_fault(actor, silverfish::stock_cash::is_family(actor->GetClassPrivate()))) return;

        if (source.contains('|') && mode->GetPropertyValueInContainer(actor) != 0)
        {
            // Stock placement keys use the World name and actor leaf, which can be identical
            // for a persistent item and a streamed copy. Apply that copy's co-op ledger instead.
            call.PreventOriginalFunctionCall();
            return;
        }
        if (seat == RespawnSeat::Guest)
        {
            // This stock function only removes placed items. Let the host resolve its
            // ownership and save flags before projecting availability to this guest.
            call.PreventOriginalFunctionCall();
            return;
        }
        if (respawn_world.Get() != world)
        {
            respawn_world = world;
            pending_host_respawns.clear();
            host_respawn_hidden.clear();
        }
        pending_host_respawns.emplace(source, path);
    }

    int read_host_respawn_hidden(const Lua& lua)
    {
        if (!ready() || !loose_host || lua.get_stack_size() != 0) return 0;
        auto* world = loose_world.Get();
        if (!live(world) || respawn_world.Get() != world) return 0;
        // Read after the engine has finished placing actors and retiring any that
        // stock removed during BeginPlay. This also catches pending-kill actors.
        for (const auto& [key, path] : pending_host_respawns)
        {
            auto* actor = find<U::UObject>(path.c_str());
            if (!live(actor) || !boolean(actor, STR("bActorIsBeingDestroyed"), false))
                host_respawn_hidden.insert(key);
            else host_respawn_hidden.erase(key);
        }
        pending_host_respawns.clear();
        std::string paths;
        for (const auto& path : host_respawn_hidden) paths.append(path).append(1, '\n');
        lua.set_string(paths);
        return 1;
    }

    void apply_loose_view(std::string_view path, Scope& scope)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        const bool hidden = loose_hidden.contains(path);
        if (!live(actor) || (scope.host && !path.contains('|') && !hidden)) return;
        if (scope.claimed != hidden ||
            (hidden && (actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), true))))
            set_visibility(scope, hidden);
    }

    LooseSource* enrolled_loose_actor(U::UObject* actor)
    {
        auto found = loose_by_actor.find(actor);
        return found != loose_by_actor.end() && found->second->scope.actor.Get() == actor ? found->second : nullptr;
    }

    bool immutable_loose_root(U::UObject* actor, U::UObject* box)
    {
        if (!boolean(actor, STR("Physics"), false) || object(box, STR("AttachParent"))) return false;
        if (auto* value = field<U::FByteProperty>(box, STR("Mobility")))
            return value->GetPropertyValueInContainer(box) == 0; // EComponentMobility::Static
        auto* mobility = field<U::FEnumProperty>(box, STR("Mobility"));
        auto* byte = mobility ? U::CastField<U::FByteProperty>(mobility->GetUnderlyingProperty()) : nullptr;
        return byte && byte->GetPropertyValue(mobility->ContainerPtrToValuePtr<void>(box)) == 0;
    }

    LooseSource* enroll_loose_actor(U::AActor* actor, U::UClass* base, const Scope& seat)
    {
        auto* world = seat.world.Get();
        if (!live(actor) || !actor->IsA(base) || !in_loose_level(actor, world) ||
            !actor->HasAnyFlags(U::RF_WasLoaded) || !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return nullptr;
        if (auto* existing = enrolled_loose_actor(actor))
        {
            if (existing->carry_candidate && !existing->carry_enrolled)
                loose_carry_pending.emplace(existing->profile.source);
            return existing;
        }
        auto* box = source_box(actor);
        if (!box) return nullptr;
        const auto path = actor->GetPathName();
        const auto source = loose_source_key(path, seat.profile->world, actor->GetLevel() != object(world, STR("PersistentLevel")));
        if (loose_actor_path(source, RC::to_utf8_string(seat.profile->world)).empty()) return nullptr;
        if ((!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false)) &&
            (seat.host || (!loose_hidden.contains(source) && !remotely_hidden_loose(actor)))) return nullptr;
        const auto class_path = actor->GetClassPrivate()->GetPathName();
        if (authored_path(path) ||
            !silverfish::content_paths::item_class(std::basic_string_view{class_path.data(), class_path.size()})) return nullptr;
        const bool cash = silverfish::stock_cash::is_family(actor->GetClassPrivate());
        if (generic_payload_fault(actor, cash)) return nullptr;

        auto [entry, _] = loose_sources.try_emplace(source);
        auto& loose = entry->second;
        if (auto stale = loose_by_actor.find(loose.indexed_actor); stale != loose_by_actor.end() && stale->second == &loose)
            loose_by_actor.erase(stale);
        loose.world = seat.profile->world;
        loose.actor = path;
        loose.item_class = class_path;
        loose.item_class_utf8 = RC::to_utf8_string(class_path);
        loose.profile = {.source = entry->first, .world = loose.world.c_str(), .actor = loose.actor.c_str(),
            .item_class = loose.item_class.c_str(), .stock_cash = cash, .discovered = true};
        loose.scope = {seat.world.Get(), seat.pawn.Get(), actor, box, seat.controller.Get(), seat.driver.Get(), &loose.profile,
            seat.host, false, true, false};
        loose.indexed_actor = actor;
        loose_by_actor.insert_or_assign(actor, &loose);
        loose.static_position = immutable_loose_root(actor, box) ? std::optional{actor->K2_GetActorLocation()} : std::nullopt;
        loose.carry_candidate = seat.host && !cash && carryable_placed_item(actor);
        loose.carry_enrolled = false;
        if (loose.carry_candidate) loose_carry_pending.emplace(entry->first);
        apply_loose_view(source, loose.scope);
        return &loose;
    }

    Scope* loose_actor_scope(U::UObject* actor)
    {
        auto* world = loose_world.Get();
        if (!world || !live(actor) || !actor->HasAnyFlags(U::RF_WasLoaded)) return nullptr;
        auto* loose = enrolled_loose_actor(actor);
        if (!loose)
        {
            // A player can reach an authored item before its part of the
            // discovery sweep runs. Qualify that exact stock actor on demand.
            auto* base = find<U::UClass>(base_class);
            auto* pawn = loose_pawn.Get();
            if (!base || !pawn || !actor->IsA(base)) return nullptr;
            const auto world_path = world->GetPathName();
            const Profile seat_profile{.world = world_path.c_str()};
            const Scope seat{world, pawn, nullptr, nullptr, object(pawn, STR("Controller")),
                object(world, STR("NetDriver")), &seat_profile, loose_host};
            if (!local_scope(seat)) return nullptr;
            loose = enroll_loose_actor(static_cast<U::AActor*>(actor), base, seat);
        }
        if (!loose) return nullptr;
        auto& scope = loose->scope;
        return local_scope(scope) && exact_source(actor, scope.world.Get(), *scope.profile) &&
            source_box(actor) == scope.box.Get() ? &scope : nullptr;
    }

    int set_guest_loose_view(const Lua& lua)
    {
        const auto count = lua.get_stack_size();
        if (!ready() || count < 3 || count > 66 || !lua.is_integer(1) || !lua.is_bool(2))
            return result(lua, false, "loose_pickup_projection_arguments");
        const auto generation = lua.get_integer();
        const bool hidden = lua.get_bool();
        auto* world = loose_world.Get();
        auto* pawn = loose_pawn.Get();
        if (generation <= 0 || static_cast<std::uint64_t>(generation) != loose_generation ||
            !live(world) || !live(pawn))
            return result(lua, false, "loose_pickup_projection_scope_changed");
        const auto world_path = world->GetPathName();
        const Profile seat_profile{.world = world_path.c_str()};
        const Scope seat{world, pawn, nullptr, nullptr, object(pawn, STR("Controller")),
            object(world, STR("NetDriver")), &seat_profile, loose_host};
        if (!local_scope(seat)) return result(lua, false, "loose_pickup_projection_scope_changed");

        const auto area = RC::to_utf8_string(world_path);
        std::vector<std::string> paths;
        paths.reserve(count - 2);
        for (int index = 3; index <= count; ++index)
        {
            if (!lua.is_string()) return result(lua, false, "loose_pickup_projection_arguments");
            std::string path{lua.get_string()};
            if (loose_actor_path(path, area).empty())
                return result(lua, false, "loose_pickup_projection_source");
            paths.push_back(std::move(path));
        }

        for (const auto& path : paths)
        {
            bool was_hidden{};
            if (hidden) loose_hidden.insert(path);
            else was_hidden = loose_hidden.erase(path) != 0;
            if (auto found = loose_sources.find(path); found != loose_sources.end())
            {
                auto& scope = found->second.scope;
                if (was_hidden && scope.host && !path.contains('|') && scope.claimed)
                    set_visibility(scope, false);
                else
                    apply_loose_view(path, scope);
            }
        }
        return result(lua, true, "loose_pickup_projection_applied");
    }

    int discover_loose(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2)) return 0;
        const auto requested = static_cast<Address>(lua.get_integer());
        auto* pawn = address(loose_pawn.Get()) == requested ? loose_pawn.Get() : live_address(requested, find<U::UClass>(pawn_path));
        const bool host = lua.get_bool();
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        if (!live(world)) return 0;
        const auto world_path = world->GetPathName();
        if (!silverfish::world_path::object(RC::StringViewType{world_path})) return 0;
        const Profile seat_profile{.world = world_path.c_str()};
        Scope seat{world, pawn, nullptr, nullptr, object(pawn, STR("Controller")),
            object(world, STR("NetDriver")), &seat_profile, host};
        if (!local_scope(seat)) return 0;
        if (loose_pawn.Get() != pawn || loose_world.Get() != world || loose_host != host)
        {
            clear_loose();
            ++loose_generation;
            loose_pawn = pawn;
            loose_world = world;
            loose_host = host;
        }
        const auto now = Clock::now();
        auto* base = find<U::UClass>(base_class);
        if (!base) return 0;
        if (next_loose_candidate == loose_candidates.size() && now >= loose_rescan)
        {
            loose_rescan = now + loose_scan_interval;
            loose_candidates.clear();
            next_loose_candidate = 0;
            std::vector<U::AActor*> actors;
            U::UGameplayStatics::GetAllActorsOfClass(world, base, actors);
            loose_candidates.reserve(actors.size());
            for (auto* actor : actors)
                if (live(actor) && actor->HasAnyFlags(U::RF_WasLoaded))
                    loose_candidates.emplace_back(actor);
        }
        const auto cutoff = Clock::now() + loose_scan_budget;
        const auto limit = std::min(loose_candidates.size(), next_loose_candidate + loose_scan_limit);
        while (next_loose_candidate < limit)
        {
            auto* actor = static_cast<U::AActor*>(loose_candidates[next_loose_candidate++].Get());
            enroll_loose_actor(actor, base, seat);
            if (Clock::now() >= cutoff) break;
        }

        std::string carry_paths;
        for (int count = 0; count < 8 && !loose_carry_pending.empty(); ++count)
        {
            auto next = loose_carry_pending.begin();
            if (const auto found = loose_sources.find(*next); found != loose_sources.end())
                carry_paths += RC::to_utf8_string(found->second.actor);
            carry_paths += '\n';
            loose_carry_pending.erase(next);
        }

        lua.set_integer(static_cast<std::int64_t>(loose_sources.size()));
        lua.set_string(carry_paths);
        lua.set_integer(static_cast<std::int64_t>(loose_generation));
        return 3;
    }

    int acknowledge_loose_carry(const Lua& lua)
    {
        if (!ready() || !loose_host || lua.get_stack_size() != 2 || !lua.is_string() || !lua.is_integer(2)) return 0;
        const auto pointer = static_cast<Address>(lua.get_integer(2));
        const auto path = RC::to_wstring(lua.get_string());
        auto* actor = find<U::UObject>(path.c_str());
        if (address(actor) == pointer && live(actor))
        {
            auto* loose = enrolled_loose_actor(actor);
            if (loose && loose->carry_candidate && local_scope(loose->scope) &&
                boolean(actor, STR("bReplicates"), true) && boolean(actor, STR("bReplicateMovement"), true))
                loose->carry_enrolled = true;
        }
        return 0;
    }

    int describe_loose(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto requested = lua.get_string();
        auto* world = loose_world.Get();
        if (!live(world)) return 0;
        const auto raw = loose_actor_path(requested, RC::to_utf8_string(world->GetPathName()));
        if (raw.empty()) return 0;
        const auto found = loose_sources.find(requested);
        auto* scope = found != loose_sources.end() ? &found->second.scope : nullptr;
        if (!scope || !live(scope->actor.Get()))
        {
            const auto path = RC::to_wstring(raw);
            scope = loose_actor_scope(find<U::UObject>(path.c_str()));
        }
        if (!scope || !scope->profile || scope->profile->source != requested || !scope->profile->discovered) return 0;
        auto* actor = scope->actor.Get();
        if (scope->claimed ? !local_scope(*scope) || !in_loose_level(static_cast<U::AActor*>(actor), world) : !available(*scope))
            return 0;
        auto* mode = loose_respawn_mode(actor);
        if (!mode) return 0;
        lua.set_string(RC::to_utf8_string(scope->profile->item_class));
        lua.set_integer(mode->GetPropertyValueInContainer(actor));
        lua.set_bool(scope->claimed);
        lua.set_bool(scope->profile->stock_cash);
        return 4;
    }

    int diagnose_loose(const Lua& lua)
    {
        if (!ready() || !headless() || lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        auto* world = loose_world.Get();
        if (!live(world)) return 0;
        const auto raw = loose_actor_path(lua.get_string(), RC::to_utf8_string(world->GetPathName()));
        if (raw.empty()) return 0;
        const auto path = RC::to_wstring(raw);
        auto* source = find<U::UObject>(path.c_str());
        auto* actor_type = find<U::UClass>(STR("/Script/Engine.Actor"));
        const auto report = [&](const char* fault) { lua.set_string(fault); return 1; };
        if (!live(source) || !actor_type || !source->IsA(actor_type) || !boolean(source, STR("bActorIsBeingDestroyed"), false))
            return report("actor_absent");
        auto* actor = static_cast<U::AActor*>(source);
        if (!in_loose_level(actor, world))
            return report("different_level");
        if (authored_path(path)) return report("dedicated_pickup_route");
        if (!source_box(actor)) return report("unqualified_root");
        if (!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false)) return report("hidden_or_disabled");
        if (const auto* reason = generic_payload_fault(actor, silverfish::stock_cash::is_family(actor->GetClassPrivate())))
            return report(reason);
        return report("stock_item_available");
    }

    int nearby_loose_status_rows(const Lua& lua)
    {
        auto* pawn = loose_pawn.Get();
        if (!ready() || !live(pawn) || lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto map = lua.get_string();
        auto center = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        struct Nearby
        {
            double distance;
            const LooseSource* source;
            U::FVector position;
        };
        std::vector<Nearby> nearby;
        for (auto& [path, loose] : loose_sources)
        {
            auto& scope = loose.scope;
            auto* actor = static_cast<U::AActor*>(scope.actor.Get());
            if (!live(actor) || (path.contains('|') && !boolean(actor->GetLevel(), STR("bIsVisible"), true))) continue;
            // The ledger stays active even when the source is outside the reported nearby list.
            apply_loose_view(path, scope);
            auto at = scope.hidden_at ? *scope.hidden_at :
                loose.static_position ? *loose.static_position : actor->K2_GetActorLocation();
            const auto dx = at.GetX() - center.GetX(), dy = at.GetY() - center.GetY(), dz = at.GetZ() - center.GetZ();
            const auto distance = dx * dx + dy * dy + dz * dz;
            if (std::isfinite(distance) && distance <= 1800.0 * 1800.0)
                nearby.push_back({distance, &loose, at});
        }
        std::ranges::sort(nearby, {}, &Nearby::distance);
        std::string rows;
        int remaining = 48;
        for (auto& [_, source, at] : nearby)
        {
            const auto& scope = source->scope;
            auto* actor = scope.actor.Get();
            auto* mode = loose_respawn_mode(actor);
            if (!mode) continue;
            if (remaining-- == 0) break;
            // Unreal can deliver the host's hidden flag ahead of the pickup ledger.
            // Keep the registered replica eligible for the local visibility update.
            const bool awaiting_claim = !scope.host && remotely_hidden_loose(actor);
            if (!scope.claimed && !available(scope) && !awaiting_claim) continue;
            if (!rows.empty()) rows.push_back('\n');
            std::format_to(std::back_inserter(rows), "native-loose-pickup\t{}\t{}\t{}\t{}\t{}\t{:.3f}\t{:.3f}\t{:.3f}",
                map, scope.profile->source, source->item_class_utf8, mode->GetPropertyValueInContainer(actor),
                scope.claimed ? "claimed" : "available", at.GetX(), at.GetY(), at.GetZ());
        }
        lua.set_string(rows);
        return 1;
    }

    Scope pickup_seat(Address pointer, bool host)
    {
        auto* pawn = local_pickup_pawn(pointer, host);
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        return {world, pawn, nullptr, nullptr, object(pawn, STR("Controller")), object(world, STR("NetDriver")), nullptr, host};
    }

    std::string bind_source(Scope& scope, Scope seat, const Profile& profile, U::AActor* actor)
    {
        if (const auto* fault = source_fault(actor, seat.world.Get(), profile))
        {
            if (live(actor) && std::string_view(fault) == "authored_pickup_stock_placement_unqualified")
            {
                auto point = actor->K2_GetActorLocation();
                return std::format("{}:{:.1f},{:.1f},{:.1f}", fault, point.GetX(), point.GetY(), point.GetZ());
            }
            return fault;
        }
        auto* box = source_box(actor);
        if (!box) return "authored_pickup_stock_root_box_unqualified";
        if (!interaction(profile.stock_cash)) return "authored_pickup_stock_interaction_unqualified";
        if (!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false))
            return "authored_pickup_stock_visibility_unqualified";
        seat.profile = &profile;
        seat.actor = actor;
        seat.box = box;
        seat.original_collision = true;
        seat.original_hidden = false;
        if (profile.dead_drop && (!present_drop(seat) || !drop_gate(seat)))
            return "authored_pickup_dead_drop_inactive";
        scope = seat;
        return {};
    }

    int set_scope(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_bool(2))
            return result(lua, false, "authored_pickup_scope_arguments");
        const auto ptr = static_cast<Address>(lua.get_integer());
        const auto host = lua.get_bool();
        auto* selected = select_source(lua);
        if (!selected || (selected->profile && selected->profile->discovered))
            return result(lua, false, "authored_pickup_source_unknown");
        auto& scope = *selected;
        if (address(scope.pawn.Get()) == ptr && host == scope.host && local_scope(scope))
            return result(lua, true, "authored_pickup_scope_bound");
        clear(scope);
        const auto& profile = profiles[static_cast<std::size_t>(selected - scopes.data())];
        auto* actor = find<U::AActor>(profile.actor);
        // An absent stock actor cannot bind. Revisit it when it respawns.
        if (!live(actor))
            return result(lua, false, "authored_pickup_stock_actor_identity_unqualified");
        auto seat = pickup_seat(ptr, host);
        auto* world = seat.world.Get();
        if (!live(world) || world->GetPathName() != profile.world)
            return result(lua, false, "authored_pickup_source_world_unavailable");
        seat.profile = &profile;
        if (!local_scope(seat)) return result(lua, false, "authored_pickup_local_controller_unavailable");
        const auto fault = bind_source(scope, seat, profile, actor);
        return result(lua, fault.empty(), fault.empty() ? "authored_pickup_scope_bound" : fault);
    }

    int take_press(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_integer()) return 0;
        const auto pawn = static_cast<Address>(lua.get_integer());
        const auto now = Clock::now();
        Scope* selected{};
        const auto consider = [&](Scope& scope) {
            if (!scope.observed.input_pending || now >= scope.observed.expires || address(scope.pawn.Get()) != pawn ||
                !available(scope)) return;
            if (!selected || scope.observed.sequence < selected->observed.sequence) selected = &scope;
        };
        for (auto& scope : scopes) consider(scope);
        for (auto& [_, loose] : loose_sources) consider(loose.scope);
        if (!selected) return 0;
        selected->observed.input_pending = false;
        lua.set_string(selected->profile->source);
        return 1;
    }

    int project(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_bool()) return result(lua, false, "authored_pickup_project_arguments");
        const auto claimed = lua.get_bool();
        auto* scope = select_source(lua);
        const bool verified = scope && set_visibility(*scope, claimed);
        return result(lua, verified, verified ? "authored_pickup_projected" : "authored_pickup_projection_unverified");
    }

    void push_status(const Lua& lua, Scope* scope)
    {
        const bool active = scope && (scope->claimed || drop_gate(*scope)) &&
            exact_source(scope->actor.Get(), scope->world.Get(), *scope->profile, !scope->claimed) &&
            (scope->claimed || !scope->profile->dead_drop || present_drop(*scope));
        const bool emitted = active && Clock::now() < scope->observed.expires;
        lua.set_bool(active);
        lua.set_bool(active && scope->claimed);
        lua.set_integer(emitted ? static_cast<std::int64_t>(scope->observed.sequence) : 0);
        lua.set_bool(emitted && scope->observed.fixture);
        lua.set_integer(active && scope->host && scope->profile->stock_cash ? loose_cash_value(scope->actor.Get()) : 0);
    }

    int status(const Lua& lua)
    {
        auto* scope = ready() && lua.get_stack_size() == 1 ? select_source(lua) : nullptr;
        push_status(lua, scope && local_scope(*scope) ? scope : nullptr);
        return 5;
    }

    int sample_authored(const Lua& lua)
    {
        const auto count = lua.get_stack_size() - 2;
        if (!ready() || count <= 0 || static_cast<std::size_t>(count) > scopes.size() ||
            !lua.is_integer(1) || !lua.is_bool(2) || !lua_checkstack(lua.get_lua_state(), count * 6)) return 0;
        const auto pointer = static_cast<Address>(lua.get_integer());
        const bool host = lua.get_bool();
        std::array<Scope*, profiles.size()> requested{};
        for (int index = 0; index < count; ++index)
        {
            if (!lua.is_string()) return 0;
            requested[index] = select_source(lua.get_string(), true);
        }
        auto seat = pickup_seat(pointer, host);
        auto* world = seat.world.Get();
        if (!live(world)) return 0;
        const auto path = world->GetPathName();
        const Profile seat_profile{.world = path.c_str()};
        seat.profile = &seat_profile;
        if (!local_scope(seat)) return 0;
        // Every row observes the same verified seat in this game-thread call.
        for (int index = 0; index < count; ++index)
        {
            auto* scope = requested[index];
            std::string fault;
            if (!scope)
                fault = "authored_pickup_source_unknown";
            else
            {
                const auto& profile = profiles[static_cast<std::size_t>(scope - scopes.data())];
                if (path != profile.world)
                    fault = "authored_pickup_source_world_unavailable";
                else if (scope->world.Get() != world || scope->pawn.Get() != seat.pawn.Get() ||
                    scope->controller.Get() != seat.controller.Get() || scope->driver.Get() != seat.driver.Get() ||
                    scope->host != host || scope->profile != &profile)
                {
                    clear(*scope);
                    fault = bind_source(*scope, seat, profile, find<U::AActor>(profile.actor));
                }
            }
            lua.set_string(fault);
            push_status(lua, fault.empty() ? scope : nullptr);
        }
        return count * 6;
    }

    Scope dead_drop_seat(Address address_value, bool host)
    {
        auto* pawn = local_pickup_pawn(address_value, host);
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        const auto* profile = dead_drop_in_world(world);
        if (!profile) return {};
        auto* actor = find<U::AActor>(profile->actor);
        Scope candidate{world, pawn, actor, source_box(actor), object(pawn, STR("Controller")),
            object(world, STR("NetDriver")), profile, host};
        return local_scope(candidate) ? candidate : Scope{};
    }

    int drop_diagnostic(const Lua& lua, std::string_view reason, U::AActor* actor = nullptr)
    {
        lua.set_string("diagnostic");
        lua.set_string("-");
        if (!live(actor))
        {
            lua.set_string(reason);
        }
        else
        {
            auto point = actor->K2_GetActorLocation();
            auto facing = actor->K2_GetActorRotation();
            auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
            const int mode_value = mode ? mode->GetPropertyValueInContainer(actor) : -1;
            lua.set_string(std::format("{}:position={:.2f},{:.2f},{:.2f}:rotation={:.2f},{:.2f},{:.2f}:hidden={}:collision={}:mode={}",
                reason, point.GetX(), point.GetY(), point.GetZ(), facing.GetPitch(), facing.GetYaw(), facing.GetRoll(),
                boolean(actor, STR("bHidden"), true), actor->GetActorEnableCollision(), mode_value));
        }
        return 3;
    }

    int observe_drop(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer() || !lua.is_bool(2)) return 0;
        const auto address_value = static_cast<Address>(lua.get_integer());
        const bool host = lua.get_bool();
        auto state = dead_drop_seat(address_value, host);
        if (!state.profile) return drop_diagnostic(lua, "local_seat");
        const auto now = Clock::now();
        auto* actor = static_cast<U::AActor*>(state.actor.Get());
        if (!host && drop_projection.scope.profile &&
            (!drop_gate(state) || !present_drop(state)) && !settle_drop_projection())
            return drop_diagnostic(lua, "projection_retirement", actor);
        if (!host && actor && !drop_gate(state) && !hidden_drop(actor) && dead_drop_identity(state))
        {
            // Vanilla may show its independently placed copy before the host's view arrives.
            drop_projection.scope = state;
            if (!settle_drop_projection()) return drop_diagnostic(lua, "stock_pose_retirement", actor);
        }
        std::string_view shown;
        std::string_view epoch = "-";
        const auto* fault = actor ? drop_identity_fault(actor, state.world.Get(), *state.profile) : nullptr;
        const auto& bound = scopes[state.profile - profiles.data()];
        if (!actor) shown = "absent";
        else if (fault) return drop_diagnostic(lua, fault, actor);
        else if (host && bound.actor.Get() == actor && bound.claimed)
            shown = "claimed";
        else if (hidden_drop(actor)) shown = "hidden";
        else if (host)
        {
            if (drop_settled_actor.Get() != actor)
            {
                drop_settled_actor = actor;
                drop_settles_at = now + drop_settle;
            }
            if (now < drop_settles_at) return 0;
            if (!drop_gate(state)) return drop_diagnostic(lua, "canonical_quest_inactive", actor);
            else if (const auto* fault = source_fault(actor, state.world.Get(), *state.profile))
                return drop_diagnostic(lua, fault, actor);
            else if (!present_drop(state)) return drop_diagnostic(lua, "stock_world_visibility_or_box", actor);
            else shown = "stock-active";
        }
        else if (drop_gate(state) && present_drop(state))
        {
            shown = "projected";
            epoch = drop_projection.epoch;
        }
        if (shown.empty()) return drop_diagnostic(lua, host ? "stock_pose_or_quest_inactive" : "client_stock_pose", actor);
        if (shown == "stock-active" || shown == "projected")
        {
            const auto pose = read_drop_pose(actor);
            if (!valid_drop_pose(pose)) return drop_diagnostic(lua, "stock_objective_transform_unavailable", actor);
            lua.set_string(shown);
            lua.set_string(epoch);
            for (const auto axis : pose) lua.set_number(axis);
            return 11;
        }
        lua.set_string(shown);
        lua.set_string(epoch);
        return 2;
    }

    bool valid_drop_epoch(std::string_view epoch)
    {
        return epoch.size() == 32 && epoch != "00000000000000000000000000000000" &&
            std::ranges::all_of(epoch, [](char digit) { return (digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f'); });
    }

    int project_drop(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 11 || !lua.is_integer() || !lua.is_string(2))
            return result(lua, false, "dead_drop_projection_arguments");
        const auto address_value = static_cast<Address>(lua.get_integer());
        const std::string epoch{lua.get_string()};
        DropPose pose{};
        for (auto& axis : pose)
        {
            if (!lua.is_number()) return result(lua, false, "dead_drop_projection_arguments");
            axis = lua.get_number();
        }
        if (!valid_drop_pose(pose)) return result(lua, false, "dead_drop_projection_transform_unavailable");
        auto state = dead_drop_seat(address_value, false);
        auto* actor = static_cast<U::AActor*>(state.actor.Get());
        if (!valid_drop_epoch(epoch) || !dead_drop_identity(state))
            return result(lua, false, "dead_drop_projection_scope_unavailable");
        const auto now = Clock::now();
        const bool renewing = drop_projection.epoch == epoch && drop_gate(state) && present_drop(state);
        if (renewing && same_drop_pose(read_drop_pose(actor), pose))
        {
            drop_projection.expires = now + drop_lease;
            return result(lua, true, "renewed");
        }
        if ((!renewing && (!settle_drop_projection() || !hidden_drop(actor))) || !actor->GetActorEnableCollision())
            return result(lua, false, "dead_drop_projection_stock_pose_unavailable");
        drop_projection = {state, epoch, {}};
        const bool moved = silverfish::native_actor_pose::set_location_rotation(
            actor, {pose[0], pose[1], pose[2]}, {pose[3], pose[4], pose[5]});
        if (moved)
        {
            actor->SetActorScale3D(U::FVector{pose[6], pose[7], pose[8]});
            actor->SetActorHiddenInGame(false);
        }
        if (!moved || !present_drop(state) || !same_drop_pose(read_drop_pose(actor), pose))
        {
            const bool restored = settle_drop_projection();
            return result(lua, false, restored ? "dead_drop_projection_not_applied" : "mutation_unverified_dead_drop_projection");
        }
        drop_projection.expires = now + drop_lease;
        return result(lua, true, "projected");
    }

    int clear_drop(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 0) return result(lua, false, "dead_drop_clear_unavailable");
        const bool cleared = settle_drop_projection();
        return result(lua, cleared, cleared ? "cleared" : "mutation_unverified_dead_drop_projection_reset");
    }

    std::string cash_flag(U::UObject* actor, U::UObject* world)
    {
        auto* library = find<U::UObject>(code_library);
        auto* function = find<U::UFunction>(stock_actor_id);
        auto* item = parameter<U::FObjectProperty>(function, STR("Actor"), 0);
        auto* world_context = parameter<U::FObjectProperty>(function, STR("__WorldContext"), sizeof(void*));
        auto* identity = parameter<U::FStrProperty>(function, STR("ID"), 2 * sizeof(void*), true);
        alignas(std::max_align_t) std::array<std::byte, 32> buffer{};
        if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 3 ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetReturnProperty() ||
            function->GetParmsSize() != buffer.size() || !item || !world_context || !identity ||
            item->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.Actor"))) return {};
        identity->InitializeValue_InContainer(buffer.data());
        const auto destroy = [&identity](std::byte* memory) { identity->DestroyValue_InContainer(memory); };
        const std::unique_ptr<std::byte, decltype(destroy)> initialized(buffer.data(), destroy);
        item->SetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(buffer.data()), actor);
        world_context->SetObjectPropertyValue(world_context->ContainerPtrToValuePtr<void>(buffer.data()), actor);
        library->ProcessEvent(function, buffer.data());
        const auto& key = identity->GetPropertyValue(identity->ContainerPtrToValuePtr<void>(buffer.data()));
        const auto expected = actor->GetName() + STR("-") + world->GetName() + STR("-");
        if (key.Len() != static_cast<int>(expected.size()) || expected != *key) return {};
        return RC::to_utf8_string(expected);
    }

    int claim_cash(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "authored_cash_claim_arguments");
        const auto pawn_address = lua.get_integer();
        const auto target_address = lua.get_integer();
        auto* scope = select_source(lua);
        auto* pawn = pawn_address > 0 ? live_address(static_cast<Address>(pawn_address), find<U::UClass>(pawn_path)) : nullptr;
        auto* target = object(pawn, STR("Save"));
        if (!scope || !scope->profile || !scope->profile->stock_cash || target_address <= 0 ||
            address(target) != static_cast<Address>(target_address) || !authorized(*scope, pawn, target))
            return result(lua, false, "authored_cash_exact_claimant_unavailable");
        auto* actor = scope->actor.Get();
        auto* instance = object(scope->world.Get(), STR("OwningGameInstance"));
        auto* canonical = object(instance, STR("SaveSlot_Ref"));
        auto* mark = find<U::UFunction>(resolve_save_mode);
        silverfish::stock_wallet::Wallet wallet;
        const auto amount = loose_cash_value(actor);
        const bool streamed = scope->profile->source.contains('|');
        const auto flag = streamed ? std::string{"-"} : cash_flag(actor, scope->world.Get());
        if (!amount || flag.empty() || !wallet.bind(instance) || object(pawn, STR("As Game Instance FP")) != instance ||
            !mark || mark->GetOuterPrivate() != find<U::UClass>(base_class) || mark->GetNumParms() != 0 ||
            mark->GetParmsSize() != 0 || mark->GetReturnProperty() || mark->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
            return result(lua, false, "authored_cash_stock_schema_unqualified");
        const auto key = RC::to_wstring(flag);
        const auto before = wallet.amount(instance);
        if (!streamed && wallet.flag_value(instance, key.c_str()).second)
            return result(lua, false, "authored_cash_stock_flag_already_recorded");
        if (before < 0 || before > (std::numeric_limits<int>::max)() - amount)
            return result(lua, false, "authored_cash_stock_wallet_out_of_range");

        // Persistent items keep the cooked save flag. Streamed copies use their own co-op claim.
        if (!streamed)
        {
            actor->ProcessEvent(mark, nullptr);
            if (wallet.flag_value(instance, key.c_str()) != std::pair{1, true} || wallet.amount(instance) != before ||
                object(instance, STR("SaveSlot_Ref")) != canonical || !authorized(*scope, pawn, target))
                return result(lua, false, "mutation_unverified_authored_cash_flag_readback");
        }
        wallet.change(instance, amount, false);
        const auto after = wallet.amount(instance);
        if (after != before + amount || (!streamed && wallet.flag_value(instance, key.c_str()) != std::pair{1, true}) ||
            object(instance, STR("SaveSlot_Ref")) != canonical || !authorized(*scope, pawn, target) || !set_visibility(*scope, true))
            return result(lua, false, "mutation_unverified_authored_cash_settlement");
        result(lua, true, "cash_credited");
        lua.set_integer(amount);
        lua.set_integer(before);
        lua.set_integer(after);
        lua.set_string(flag);
        return 6;
    }

    int stock_pickup_callback(const Lua& lua)
    {
        if (!ready() || active_pickup_broadcast || lua.get_stack_size() != 4 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_bool(3))
            return result(lua, false, "authored_pickup_callback_arguments_unqualified");
        const auto pawn_address = static_cast<Address>(lua.get_integer());
        const auto save_address = static_cast<Address>(lua.get_integer());
        const bool broadcast = lua.get_bool();
        auto* scope = select_source(lua);
        auto* pawn = live_address(pawn_address, find<U::UClass>(pawn_path));
        auto* save = object(pawn, STR("Save"));
        if (!scope || !scope->profile || !save_address || address(save) != save_address || !authorized(*scope, pawn, save))
            return result(lua, false, "authored_pickup_callback_authority_unqualified");
        const auto match = std::ranges::find(pickup_listeners, scope->profile->source, &PickupListener::source);
        if (match == pickup_listeners.end()) return result(lua, false, "authored_pickup_callback_source_unknown");

        auto* world = scope->world.Get();
        auto* level = object(world, STR("PersistentLevel"));
        auto* script = object(level, STR("LevelScriptActor"));
        auto* script_class = find<U::UClass>(match->script_class);
        if (!live(script) || !script_class || script->GetClassPrivate() != script_class ||
            role(script) != 3 || static_cast<U::AActor*>(script)->GetWorld() != world ||
            static_cast<U::AActor*>(script)->GetLevel() != level)
            return result(lua, false, "authored_pickup_level_script_unqualified");
        const U::FName listener_name{match->callback_name, U::FNAME_Find};
        auto* listener = script_class->GetFunctionByNameInChain(listener_name);
        auto* signature = find<U::UFunction>(STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:Item_PickedUp__DelegateSignature"));
        auto* property = field<U::FMulticastInlineDelegateProperty>(scope->actor.Get(), STR("Item_PickedUp"));
        if (listener_name.IsNone() || !listener || listener->GetOuterPrivate() != script_class ||
            listener->GetNumParms() != 0 || listener->GetReturnProperty() ||
            listener->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !signature || signature->GetNumParms() != 0 ||
            !property || property->GetSize() != sizeof(U::FMulticastScriptDelegate) ||
            property->GetSignatureFunction().Get() != signature)
            return result(lua, false, "authored_pickup_callback_layout_unqualified");
        const auto* delegate = property->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(scope->actor.Get());
        if (delegate->Num() != 1 || !delegate->Contains(script, listener_name))
            return result(lua, false, "authored_pickup_exact_callback_unavailable");

        auto* receiver = find<U::UObject>(match->receiver);
        if (live(receiver) && (!exact(receiver, match->receiver_class) || role(receiver) != 3 ||
            static_cast<U::AActor*>(receiver)->GetWorld() != world || static_cast<U::AActor*>(receiver)->GetLevel() != level))
            return result(lua, false, "authored_pickup_callback_receiver_unqualified");
        if (match->gold_bag && !live(receiver))
            return result(lua, false, "authored_pickup_callback_receiver_unavailable");
        if (!broadcast) return result(lua, true, "verified");

        PickupBroadcast receipt{script, listener, live(receiver) ? receiver : nullptr, scope->pawn.Get(), match->gold_bag};
        active_pickup_broadcast = &receipt;
        delegate->ProcessMulticastDelegate<U::UObject>(nullptr);
        active_pickup_broadcast = nullptr;
        if (!receipt.entered || (receipt.receiver && !receipt.reaction) || (receipt.gold_bag && !receipt.ping))
            return result(lua, false, "mutation_unverified_authored_pickup_callback");
        return result(lua, true, receipt.gold_bag ? "gold_bag_conversation" :
            receipt.receiver ? "officer_reaction" : "officer_absent");
    }

    int capture(const Lua& lua)
    {
        std::array<Address, 3> supplied{};
        if (!ready() || lua.get_stack_size() != supplied.size() + 1)
            return result(lua, false, "authored_pickup_capture_arguments");
        for (auto& pointer : supplied)
        {
            if (!lua.is_integer()) return result(lua, false, "authored_pickup_capture_arguments");
            pointer = static_cast<Address>(lua.get_integer());
            if (!pointer) return result(lua, false, "authored_pickup_capture_arguments");
        }
        const auto* scope = select_source(lua);
        RowSchema rows;
        auto* pawn = live_address(supplied[0], find<U::UClass>(pawn_path));
        auto* target = object(pawn, STR("Save"));
        if (!scope || scope->profile->stock_cash || address(target) != supplied[1] || !authorized(*scope, pawn, target))
            return result(lua, false, "authored_pickup_exact_claimant_unavailable");
        if (!rows.bind()) return result(lua, false, "authored_pickup_inventory_schema_unavailable");
        if (scope->profile->stack_units && !scope->profile->stacked_supply())
        {
            auto* equipment = field<U::FArrayProperty>(target, STR("PlayerEquipment"));
            auto* equipment_row = equipment ? U::CastField<U::FStructProperty>(equipment->GetInner()) : nullptr;
            if (!equipment_row || equipment_row->GetStruct().Get() != rows.type)
                return result(lua, false, "authored_pickup_inventory_schema_unavailable");
            auto* item_class = scope->actor.Get()->GetClassPrivate();
            for (auto* property : {rows.array, equipment})
            {
                U::FScriptArrayHelper items(property, property->ContainerPtrToValuePtr<void>(target));
                if (items.Num() < 0 || items.Num() > 1024)
                    return result(lua, false, "authored_pickup_inventory_schema_unavailable");
                for (int index = 0; index < items.Num(); ++index)
                    if (read(rows.klass, items.GetRawPtr(index)) == item_class)
                        return result(lua, false, "authored_pickup_stock_stack_merge_unqualified");
            }
        }
        auto* scratch = isolated(supplied[2], rows);
        if (!scratch || scratch == target) return result(lua, false, "authored_pickup_isolated_escrow_required");
        Guid guid{};
        const std::string_view detail = convert(rows, pawn, scope->actor.Get(), scratch, guid);
        if (detail != "captured") return result(lua, false, detail);
        result(lua, true, detail);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        return 3;
    }

    int verify_escrow(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "authored_pickup_verify_arguments");
        const auto initial = static_cast<Address>(lua.get_integer());
        const auto reloaded = static_cast<Address>(lua.get_integer());
        const auto* scope = select_source(lua);
        RowSchema rows;
        if (!scope || scope->profile->stock_cash || initial == reloaded || !available(*scope) || !scope->host || !rows.bind())
            return result(lua, false, "authored_pickup_verify_schema_unavailable");
        auto* item_class = find<U::UClass>(scope->profile->item_class);
        auto* first = isolated(initial, rows);
        auto* second = isolated(reloaded, rows);
        if (!first || !second) return result(lua, false, "authored_pickup_isolated_escrow_required");
        U::FScriptArrayHelper a(rows.array, rows.array->ContainerPtrToValuePtr<void>(first));
        U::FScriptArrayHelper b(rows.array, rows.array->ContainerPtrToValuePtr<void>(second));
        const bool ok = a.Num() == 1 && b.Num() == 1 && a.GetRawPtr(0) != b.GetRawPtr(0) &&
            rows.authored(a.GetRawPtr(0), item_class) && rows.identical(a.GetRawPtr(0), b.GetRawPtr(0));
        return result(lua, ok, ok ? "identical" : "authored_pickup_escrow_payload_changed");
    }

    int probe(const Lua& lua)
    {
        auto* scope = ready() && lua.get_stack_size() == 1 ? select_source(lua) : nullptr;
        if (!scope || fixture_scope || !headless() || (scope->host && !env("SILVERFISH_COOP_NATIVE_FIXTURES")) ||
            !admit_input(*scope, scope->pawn.Get(), scope->box.Get()))
            return result(lua, false, "authored_pickup_fixture_unavailable");
        auto* fn = interaction(scope && scope->profile && scope->profile->stock_cash);
        if (!fn) return result(lua, false, "authored_pickup_fixture_schema_unavailable");
        auto* actor = scope->actor.Get();
        const auto before = sequence;
        std::array arguments{scope->pawn.Get(), scope->box.Get()};
        fixture_scope = scope;
        actor->ProcessEvent(fn, arguments.data());
        fixture_scope = nullptr;
        const bool ok = sequence == before + 1 && scope->observed.fixture && available(*scope);
        return result(lua, ok, ok ? "authored_pickup_stock_event_vetoed" : "authored_pickup_fixture_unverified");
    }

    int reset(const Lua&)
    {
        if (U::IsInGameThreadRaw()) clear();
        return 0;
    }
}

namespace silverfish::native_authored_pickup
{
    bool queue_nearby_pickup(U::UObject* screen, U::UObject* pawn, const Guid& guid)
    {
        if (!ready() || !live(screen) ||
            !exact(screen, inventory_screen)) return false;
        RowSchema rows;
        if (!rows.bind()) return false;
        const auto selected = nearby_selection(screen, rows, guid);
        if (!selected.matching_row || nearby_viewer(screen) != pawn) return false;
        if (silverfish::native_guest_drop_projection::owns_actor(selected.actor))
            return silverfish::native_guest_drop_projection::queue_nearby_pickup(selected.actor, pawn);
        auto* scope = loose_floor_scope(selected.actor, pawn);
        return scope && !scope->host && enqueue_nearby(*scope, pawn);
    }

    U::UWorld* qualify_mindcore_results(std::uintptr_t pawn, std::uintptr_t detached, std::uintptr_t canonical,
                                       std::string_view guid)
    {
        constexpr TapeExchangeProfile silo{
            STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P"), nullptr, drill_test_results_tape.product};
        if (!ready() || !env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || detached == canonical) return nullptr;
        Guid identity;
        if (!parse_guid(guid, identity)) return nullptr;
        RowSchema rows;
        TapeStores stores{rows};
        if (!rows.bind() || !stores.valid()) return nullptr;
        auto* owner = live_address(pawn, find<U::UClass>(pawn_path));
        auto* saved = live_address(detached, rows.save);
        auto* stock = live_address(canonical, rows.save);
        auto* world = tape_owner_world(owner, saved, stock, silo);
        auto* tape = find<U::UClass>(silo.product);
        auto* cdo = tape ? tape->GetClassDefaultObject().Get() : nullptr;
        auto* raw_ammo = field<U::FIntProperty>(rows.type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        TapeStores::Found owned;
        if (!world || !tape || !cdo || !raw_ammo || !stores.scan(saved, identity, owned) || owned.count != 1 ||
            !owned.backpack_row || !rows.matches_actor(owned.backpack_row, cdo) ||
            raw_ammo->GetPropertyValueInContainer(owned.backpack_row) != 0 ||
            !exclusive_tape_ids(stores, world, owner, saved, stock, identity, identity)) return nullptr;
        return world;
    }

    void install_hooks()
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* context, U::UFunction* fn, void* parms) {
                begin_loose_respawn(call, context, fn);
                intercept(call, context, fn, parms, nullptr);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* context, U::FFrame& frame, void*) {
                begin_loose_respawn(call, context, frame.Node());
                intercept(call, context, frame.Node(), frame.Locals(), &frame);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup script")});
        callbacks[2] = Hook::RegisterProcessEventPostCallback(
            [](Iteration& call, U::UObject* context, U::UFunction* fn, void*) {
                if (!call.OriginalFunctionCallPrevented()) complete_floor_projection(context, fn);
            }, {false, false, STR("SilverfishCoopNative"), STR("Display host loot stack counts in the stock inventory")});
        callbacks[3] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            [](Iteration& call, U::UObject* context, U::FFrame& frame, void*) {
                if (!call.OriginalFunctionCallPrevented()) complete_floor_projection(context, frame.Node());
            }, {false, false, STR("SilverfishCoopNative"), STR("Display host loot stack counts in the stock floor query")});
        callbacks[4] = Hook::RegisterProcessInternalPreCallback(
            [](Iteration& call, U::UObject* context, U::FFrame& frame, void*) {
                begin_loose_respawn(call, context, frame.Node());
            }, {false, false, STR("SilverfishCoopNative"), STR("Use host availability for placed stock pickups")});
        callbacks[5] = Hook::RegisterLoadMapPreCallback(
            [](Hook::TCallbackIterationData<bool>& call, U::UEngine*, U::FWorldContext& context,
               U::FURL url, U::UPendingNetGame* pending, U::FString&) {
                if (ready() && !call.OriginalFunctionCallPrevented()) begin_respawn_load(context, url, pending);
            }, {false, true, STR("SilverfishCoopNative"), STR("Qualify placed pickups during network map loading")});
        callbacks[6] = Hook::RegisterLoadMapPostCallback(
            [](Hook::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL, U::UPendingNetGame*, U::FString&) {
                if (ready() && respawn_load.context == &context) respawn_load = {};
            }, {false, true, STR("SilverfishCoopNative"), STR("Release the placed pickup map-load scope")});
        const bool ok = std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; });
        if (!ok) uninstall_hooks();
        installed.store(ok, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
        respawn_load = {};
        respawn_world.Reset();
        pending_host_respawns.clear();
        host_respawn_hidden.clear();
        installed.store(false, std::memory_order_release);
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeAuthoredPickupScope", set_scope);
        lua.register_function("SilverfishSampleNativeAuthoredPickups", sample_authored);
        lua.register_function("SilverfishDiscoverLoosePickups", discover_loose);
        lua.register_function("SilverfishReadHostLooseRespawnHidden", read_host_respawn_hidden);
        lua.register_function("SilverfishAcknowledgeLooseCarry", acknowledge_loose_carry);
        lua.register_function("SilverfishSetGuestLoosePickupView", set_guest_loose_view);
        lua.register_function("SilverfishDescribeLoosePickup", describe_loose);
        lua.register_function("SilverfishNearbyLoosePickupStatusRows", nearby_loose_status_rows);
        lua.register_function("SilverfishTakeNativeAuthoredPickupPress", take_press);
        lua.register_function("SilverfishProjectNativeAuthoredPickup", project);
        lua.register_function("SilverfishReadNativeAuthoredPickup", status);
        lua.register_function("SilverfishObserveNativeDeadDrop", observe_drop);
        lua.register_function("SilverfishProjectNativeDeadDrop", project_drop);
        lua.register_function("SilverfishClearNativeDeadDrop", clear_drop);
        lua.register_function("SilverfishStockAuthoredPickupCallback", stock_pickup_callback);
        lua.register_function("SilverfishCaptureNativeAuthoredPickup", capture);
        lua.register_function("SilverfishClaimNativeAuthoredCash", claim_cash);
        lua.register_function("SilverfishVerifyNativeAuthoredPickup", verify_escrow);
        lua.register_function("SilverfishMergeNativeAuthoredSupply", merge_supply);
        lua.register_function("SilverfishExchangeStoneMazeTape", exchange_tape<stone_maze_tape>);
        lua.register_function("SilverfishExchangeEatherPumpTape", exchange_tape<eather_pump_tape>);
        lua.register_function("SilverfishExchangeDrillTestResultsTape", exchange_tape<drill_test_results_tape>);
        lua.register_function("SilverfishExchangeDrillInstructionsTape", exchange_tape<drill_instructions_tape>);
        lua.register_function("SilverfishClearNativeAuthoredPickup", reset);
        if (headless())
        {
            lua.register_function("SilverfishProbeNativeAuthoredPickup", probe);
            lua.register_function("SilverfishDiagnoseLoosePickup", diagnose_loose);
        }
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) clear();
    }
}
