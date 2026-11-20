#include "authored_pickup.hpp"
#include "guest_regen.hpp"
#include "guest_weapon_registry.hpp"
#include "interaction_input.hpp"
#include "stock_cash.hpp"
#include "quest_rewards.hpp"
#include "stock_wallet.hpp"

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
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
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
        bool dead_drop{};
        bool stock_bandage{};

        bool stacked_supply() const { return stock_ammo || stock_bandage; }
    };

    constexpr Profile dead_drop_profile{
        .source = "crossroads-dead-drop-bag",
        .world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
        .actor = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Bag_DeadDrop_CrossRoads_C_1"),
        .item_class = STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C"),
        // World pose of the stock authority actor after the active quest leaves it in place.
        .x = -15818.69, .y = 15743.97, .z = 381.31,
        .item_type = 1, .dead_drop = true,
    };

    constexpr std::string_view drill_head_source = "scientist-tunnel-drill-head";
    constexpr std::string_view lab_blank_tape_source = "lab-e206-blank-data-tape";

    constexpr std::array profiles{
        dead_drop_profile,
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
            "savlo-estate-evil-eye-ring",
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P:PersistentLevel.Art_EvilEyeRing_C_1"),
            STR("/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C"),
            125.08236158222627, 2704.0395701013076, 474.9005913197349, 0,
        },
        Profile{
            "borough-mre",
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.Item_MRE_C_1"),
            STR("/Game/Classes/Items/Item_MRE.Item_MRE_C"),
            73.05513264620163, 2566.409056792115, 307.29826126640774, 1, 1, true,
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
        Profile{
            "city-canned-peaches",
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_CannedPeaches_C_1"),
            STR("/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"),
            -670.2554344659196, 4487.914807500511, 41.21247972722806, 1, 1, true,
        },
#include "authored_cash_profiles.inc"
#include "authored_supply_profiles.inc"
    };
    static_assert(profiles.front().dead_drop);
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
    constexpr auto stack_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.FL_Items_C:StackItem_Stats");
    constexpr auto stack_library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.Default__FL_Items_C");
    constexpr auto tape_station_class = STR("/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C");
    constexpr auto blank_tape_path = STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C");
    struct TapeExchangeProfile
    {
        const U::TCHAR* world;
        const U::TCHAR* station;
        const U::TCHAR* product;
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
    };
    constexpr TapeExchangeProfile drill_test_results_tape{
        STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
        STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"),
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
    };

    std::array<Scope, profiles.size()> scopes{};
    std::uint64_t sequence{};
    Scope* fixture_scope{};
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
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

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

    Scope* select_source(const Lua& lua)
    {
        if (!lua.is_string()) return nullptr;
        const auto source = lua.get_string();
        if (source == "crossroads-old-pipe" && !(env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS"))) return nullptr;
        const auto match = std::ranges::find_if(profiles, [&source](const Profile& profile) { return profile.source == source; });
        return match == profiles.end() ? nullptr : &scopes[static_cast<std::size_t>(match - profiles.begin())];
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

    const char* source_fault(U::UObject* actor, U::UObject* world, const Profile& profile, bool require_placement = true)
    {
        if (!exact(actor, profile.item_class) || actor->GetPathName() != profile.actor ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return "authored_pickup_stock_actor_identity_unqualified";
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
        if (profile.stock_ammo && !stock_ammo_matches(actor, profile))
            return "authored_pickup_stock_ammo_profile_unqualified";
        if (const auto* fault = profile.stock_bandage ? stock_bandage_fault(actor, profile) : nullptr) return fault;
        if (profile.stock_cash && !loose_cash_value(actor)) return "authored_pickup_stock_cash_profile_unqualified";
        // Physics-enabled items can settle below their authored placement under vanilla simulation.
        // A claimed item may fall when collision is disabled. Its exact scoped object stays hidden.
        if (profile.physics && !require_placement) return nullptr;
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

    const char* placed_drop_fault(U::UObject* actor, U::UObject* world)
    {
        const auto& profile = dead_drop_profile;
        if (!live(actor)) return "actor_not_live";
        if (!exact(actor, profile.item_class) || actor->GetPathName() != profile.actor) return "actor_identity";
        if (!actor->HasAnyFlags(U::RF_WasLoaded)) return "actor_not_loaded";
        if (!boolean(actor, STR("bActorIsBeingDestroyed"), false)) return "actor_being_destroyed";
        if (!live(world) || world->GetPathName() != profile.world) return "world_identity";
        auto* item = static_cast<U::AActor*>(actor);
        if (item->GetWorld() != world || item->GetLevel() != object(world, STR("PersistentLevel"))) return "persistent_level";
        return nullptr;
    }

    bool placed_dead_drop(U::UObject* actor, U::UObject* world)
    {
        return placed_drop_fault(actor, world) == nullptr;
    }

    const char* drop_identity_fault(U::UObject* actor, U::UObject* world)
    {
        if (const auto* fault = placed_drop_fault(actor, world)) return fault;
        if (!boolean(actor, STR("bReplicates"), false)) return "replication";
        if (!boolean(actor, STR("Stacks"), false)) return "stacking";
        if (!source_box(actor)) return "root_box";
        auto* quest = field<U::FNameProperty>(actor, STR("Quest_ID"));
        const U::FName key{STR("DeadDrop_CrossRoads"), U::FNAME_Find};
        if (!quest || key.IsNone() || quest->GetPropertyValueInContainer(actor) != key) return "quest_id";
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!mode || mode->GetPropertyValueInContainer(actor) != 0) return "respawn_mode";
        if (!boolean(actor, STR("DespawnIfPlayerOwns"), true)) return "owned_despawn";
        if (!boolean(actor, STR("Physics"), false)) return "physics";
        // Both peers load this exact root at world yaw -150; the export contains its relative rotation.
        auto facing = static_cast<U::AActor*>(actor)->K2_GetActorRotation();
        if (!(std::abs(std::remainder(facing.GetPitch(), 360.0)) <= 0.5 &&
              std::abs(std::remainder(facing.GetYaw() + 150.0, 360.0)) <= 0.5 &&
              std::abs(std::remainder(facing.GetRoll(), 360.0)) <= 0.5)) return "rotation";
        return nullptr;
    }

    bool dead_drop_identity(U::UObject* actor, U::UObject* world)
    {
        return drop_identity_fault(actor, world) == nullptr;
    }

    bool hidden_drop(U::AActor* actor)
    {
        auto point = actor->K2_GetActorLocation();
        return boolean(actor, STR("bHidden"), true) &&
            std::abs(point.GetX()) <= 1.0 && std::abs(point.GetY()) <= 1.0 && std::abs(point.GetZ() - 9999.0) <= 1.0;
    }

    bool present_drop(const Scope& state)
    {
        auto* actor = static_cast<U::AActor*>(state.actor.Get());
        return dead_drop_identity(actor, state.world.Get()) && state.box.Get() == source_box(actor) &&
            exact_source(actor, state.world.Get(), dead_drop_profile) &&
            boolean(actor, STR("bHidden"), false) && actor->GetActorEnableCollision();
    }

    bool drop_gate(const Scope& state)
    {
        if (!state.profile || !state.profile->dead_drop) return true;
        if (!local_scope(state)) return false;
        if (state.host)
        {
            auto* save = object(object(state.world.Get(), STR("OwningGameInstance")), STR("SaveSlot_Ref"));
            return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") &&
                drop_settled_actor.Get() == state.actor.Get() && Clock::now() >= drop_settles_at &&
                object(state.pawn.Get(), STR("Save")) == save &&
                silverfish::native_quest_rewards::active_crossroads_dead_drop(save);
        }
        const auto& bound = drop_projection.scope;
        return Clock::now() < drop_projection.expires && state.actor.Get() == bound.actor.Get() &&
            state.world.Get() == bound.world.Get() && state.pawn.Get() == bound.pawn.Get() &&
            state.controller.Get() == bound.controller.Get() && state.driver.Get() == bound.driver.Get();
    }

    bool drop_fence(U::UObject* actor)
    {
        if (!exact(actor, dead_drop_profile.item_class) || actor->GetPathName() != dead_drop_profile.actor) return false;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (!placed_dead_drop(actor, world)) return false;
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
        scopes.front().observed = {};
        auto* actor = static_cast<U::AActor*>(drop_projection.scope.actor.Get());
        auto* world = drop_projection.scope.world.Get();
        if (placed_dead_drop(actor, world))
        {
            actor->SetActorHiddenInGame(true);
            U::FHitResult ignored{};
            if (!hidden_drop(actor))
                actor->K2_SetActorLocation(U::FVector{0.0, 0.0, 9999.0}, false, ignored, true);
            if (!hidden_drop(actor)) return false;
        }
        drop_projection = {};
        return true;
    }

    Scope* actor_scope(U::UObject* actor)
    {
        if (!actor) return nullptr;
        const auto match = std::ranges::find_if(scopes, [actor](const Scope& scope) { return scope.actor.Get() == actor; });
        if (match == scopes.end() || !local_scope(*match)) return nullptr;
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

    bool set_visibility(Scope& scope, bool claimed)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (!local_scope(scope) || (scope.profile->dead_drop && (!scope.host || !claimed || !drop_gate(scope))) ||
            !exact_source(actor, scope.world.Get(), *scope.profile, !scope.claimed || !claimed))
            return false;
        const bool hidden = claimed || scope.original_hidden;
        const bool collision = !claimed && scope.original_collision;
        actor->SetActorHiddenInGame(hidden);
        actor->SetActorEnableCollision(collision);
        if (actor->GetActorEnableCollision() != collision || !boolean(actor, STR("bHidden"), hidden)) return false;
        scope.claimed = claimed;
        if (claimed) scope.observed = {};
        return true;
    }

    void clear(Scope& scope)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (scope.claimed && scope.profile && !scope.profile->dead_drop &&
            live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false))
        {
            actor->SetActorHiddenInGame(scope.original_hidden);
            actor->SetActorEnableCollision(scope.original_collision);
        }
        if (fixture_scope == &scope) fixture_scope = nullptr;
        scope = {};
    }

    void clear()
    {
        settle_drop_projection();
        drop_settled_actor.Reset();
        drop_settles_at = {};
        for (auto& scope : scopes) clear(scope);
    }

    bool admit_input(const Scope& scope, U::UObject* pawn, U::UObject* hit)
    {
        return pawn && pawn == scope.pawn.Get() && hit && hit == scope.box.Get() && available(scope) &&
            boolean(pawn, STR("Dead"), false) && in_range(pawn, scope.actor.Get());
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

    void intercept(Iteration& call, U::UObject* context, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!ready() || !context || !function || call.OriginalFunctionCallPrevented()) return;
        static const U::FName interact_name{STR("SendInteractionToActor")};
        static const U::FName put_name{STR("PutInInventory")};
        static const U::FName pickup_name{STR("PickUp_Object")};
        static const U::FName add_name{STR("AddItemToInventory")};
        const auto name = function->GetFName();
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

        Guid identity(const void* value) const
        {
            const auto* data = guid->ContainerPtrToValuePtr<void>(value);
            Guid result{};
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return result;
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

        bool matches_actor(const void* value, U::UObject* actor) const
        {
            auto* actor_payload = field<U::FStructProperty>(actor, STR("DataStruct"));
            return authored(value, actor->GetClassPrivate()) && actor_payload && actor_payload->GetStruct().Get() == payload->GetStruct().Get() &&
                matches<U::FIntProperty>(value, actor, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"), STR("Ammo")) &&
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

    bool stock_stack_matches(const RowSchema& rows, U::UObject* pawn,
                             const void* previous, const void* expected, int difference)
    {
        auto* fn = find<U::UFunction>(stack_path);
        auto* library = find<U::UObject>(stack_library);
        auto* input = parameter<U::FStructProperty>(fn, STR("InventoryItem_Struct"), 0, true);
        auto* delta = parameter<U::FIntProperty>(fn, STR("Difference"), 0x50);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), 0x58);
        auto* output = parameter<U::FStructProperty>(fn, STR("StackedStruct"), 0x60, true);
        if (!fn || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) ||
            fn->GetOuterPrivate() != library->GetClassPrivate() || fn->GetNumParms() != 4 ||
            fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !input || !delta || !context || !output ||
            !input->HasAnyPropertyFlags(U::CPF_ReferenceParm) || !input->HasAnyPropertyFlags(U::CPF_ConstParm) ||
            output->HasAnyPropertyFlags(U::CPF_ReferenceParm) || input->GetSize() != 0x50 || output->GetSize() != 0x50 ||
            input->GetStruct().Get() != rows.type || output->GetStruct().Get() != rows.type ||
            context->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/CoreUObject.Object"))) return false;

        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        auto* storage = buffer.data();
        input->InitializeValue_InContainer(storage);
        output->InitializeValue_InContainer(storage);
        auto release = [input, output](std::byte* data) {
            output->DestroyValue_InContainer(data);
            input->DestroyValue_InContainer(data);
        };
        const std::unique_ptr<std::byte, decltype(release)> lifetime(storage, release);
        input->CopyCompleteValue(input->ContainerPtrToValuePtr<void>(storage), previous);
        delta->SetPropertyValueInContainer(storage, difference);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(storage), pawn);
        library->ProcessEvent(fn, storage);
        return rows.identical(output->ContainerPtrToValuePtr<void>(storage), expected);
    }

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
        const bool stock_agrees = stock_stack_matches(rows, pawn, selected, staged_row, difference);
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
        return exact(station, tape_station_class) && role(station) == 3 &&
            boolean(station, STR("bActorIsBeingDestroyed"), false) && exact(box, STR("/Script/Engine.BoxComponent")) &&
            box->GetOuterPrivate() == station && static_cast<U::AActor*>(station)->GetWorld() == world &&
            static_cast<U::AActor*>(station)->GetLevel() == object(world, STR("PersistentLevel")) && in_range(pawn, station) &&
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
                        &Profile == &eather_pump_tape ? "eather_pump" : "drill_test_results", reason.substr(at + 10)));
            }
            return result(lua, false, reason);
        };
        if (!ready() || !env("SILVERFISH_COOP_NATIVE_ZONE") ||
            (&Profile == &drill_test_results_tape && !env("SILVERFISH_COOP_NATIVE_GUEST_SAVE")) || lua.get_stack_size() != 5 ||
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
        if (!world || !tape_station_reachable(world, pawn, canonical, Profile) ||
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

    int set_scope(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_bool(2))
            return result(lua, false, "authored_pickup_scope_arguments");
        const auto ptr = static_cast<Address>(lua.get_integer());
        const auto host = lua.get_bool();
        auto* selected = select_source(lua);
        if (!selected) return result(lua, false, "authored_pickup_source_unknown");
        auto& scope = *selected;
        if (address(scope.pawn.Get()) == ptr && host == scope.host && local_scope(scope))
            return result(lua, true, "authored_pickup_scope_bound");
        clear(scope);
        const auto& profile = profiles[static_cast<std::size_t>(selected - scopes.data())];
        auto* actor = find<U::AActor>(profile.actor);
        // Stock permanent actors can be absent on revisits. Skip the pawn scan in that case.
        if (profile.stock_cash && !live(actor))
            return result(lua, false, "authored_pickup_stock_actor_identity_unqualified");
        auto* pawn = live_address(ptr, find<U::UClass>(pawn_path));
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        if (!live(world) || world->GetPathName() != profile.world)
            return result(lua, false, "authored_pickup_source_world_unavailable");
        if (const auto* fault = source_fault(actor, world, profile))
        {
            if (live(actor) && std::string_view(fault) == "authored_pickup_stock_placement_unqualified")
            {
                auto point = actor->K2_GetActorLocation();
                return result(lua, false, std::format("{}:{:.1f},{:.1f},{:.1f}", fault, point.GetX(), point.GetY(), point.GetZ()));
            }
            return result(lua, false, fault);
        }
        auto* box = source_box(actor);
        if (!box) return result(lua, false, "authored_pickup_stock_root_box_unqualified");
        if (!interaction(profile.stock_cash)) return result(lua, false, "authored_pickup_stock_interaction_unqualified");
        if (!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false))
            return result(lua, false, "authored_pickup_stock_visibility_unqualified");
        Scope next{world, pawn, actor, box, object(pawn, STR("Controller")), object(world, STR("NetDriver")),
            &profile, host, false, actor->GetActorEnableCollision(), boolean(actor, STR("bHidden"), true)};
        if (!local_scope(next)) return result(lua, false, "authored_pickup_local_controller_unavailable");
        if (profile.dead_drop && (!present_drop(next) || !drop_gate(next)))
            return result(lua, false, "authored_pickup_dead_drop_inactive");
        scope = next;
        return result(lua, true, "authored_pickup_scope_bound");
    }

    int take_press(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_integer()) return 0;
        const auto pawn = static_cast<Address>(lua.get_integer());
        const auto now = Clock::now();
        Scope* selected{};
        for (auto& scope : scopes)
        {
            if (!scope.observed.input_pending || now >= scope.observed.expires || address(scope.pawn.Get()) != pawn ||
                !available(scope)) continue;
            if (!selected || scope.observed.sequence < selected->observed.sequence) selected = &scope;
        }
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

    int status(const Lua& lua)
    {
        auto* scope = ready() && lua.get_stack_size() == 1 ? select_source(lua) : nullptr;
        const bool active = scope && local_scope(*scope) && (scope->claimed || drop_gate(*scope)) &&
            exact_source(scope->actor.Get(), scope->world.Get(), *scope->profile, !scope->claimed) &&
            (scope->claimed || !scope->profile->dead_drop || present_drop(*scope));
        const bool emitted = active && Clock::now() < scope->observed.expires;
        lua.set_bool(active);
        lua.set_bool(active && scope->claimed);
        lua.set_integer(emitted ? static_cast<std::int64_t>(scope->observed.sequence) : 0);
        lua.set_bool(emitted && scope->observed.fixture);
        lua.set_integer(active && scope->host && scope->profile->stock_cash ? loose_cash_value(scope->actor.Get()) : 0);
        return 5;
    }

    Scope dead_drop_seat(Address address_value, bool host)
    {
        auto* pawn = live_address(address_value, find<U::UClass>(pawn_path));
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        auto* actor = find<U::AActor>(dead_drop_profile.actor);
        Scope candidate{world, pawn, actor, source_box(actor), object(pawn, STR("Controller")),
            object(world, STR("NetDriver")), &dead_drop_profile, host};
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
        std::string_view shown;
        std::string_view epoch = "-";
        const auto* fault = actor ? drop_identity_fault(actor, state.world.Get()) : nullptr;
        if (!actor) shown = "absent";
        else if (fault) return drop_diagnostic(lua, fault, actor);
        else if (hidden_drop(actor)) shown = "hidden";
        else if (host)
        {
            if (drop_settled_actor.Get() != actor)
            {
                drop_settled_actor = actor;
                drop_settles_at = now + drop_settle;
            }
            if (now < drop_settles_at) return 0;
            const auto& bound = scopes.front();
            if (bound.actor.Get() == actor && bound.claimed && boolean(actor, STR("bHidden"), true)) shown = "claimed";
            else if (!drop_gate(state)) return drop_diagnostic(lua, "canonical_quest_inactive", actor);
            else if (const auto* fault = source_fault(actor, state.world.Get(), dead_drop_profile))
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
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer() || !lua.is_string(2))
            return result(lua, false, "dead_drop_projection_arguments");
        const auto address_value = static_cast<Address>(lua.get_integer());
        const std::string epoch{lua.get_string()};
        auto state = dead_drop_seat(address_value, false);
        auto* actor = static_cast<U::AActor*>(state.actor.Get());
        if (!valid_drop_epoch(epoch) || !state.profile || !dead_drop_identity(actor, state.world.Get()))
            return result(lua, false, "dead_drop_projection_scope_unavailable");
        const auto now = Clock::now();
        if (drop_projection.epoch == epoch && drop_gate(state) && present_drop(state))
        {
            drop_projection.expires = now + drop_lease;
            return result(lua, true, "renewed");
        }
        if (!settle_drop_projection() || !hidden_drop(actor) || !actor->GetActorEnableCollision())
            return result(lua, false, "dead_drop_projection_stock_pose_unavailable");
        const auto& source = dead_drop_profile;
        U::FHitResult ignored{};
        drop_projection = {state, epoch, {}};
        const bool moved = actor->K2_SetActorLocation(U::FVector{source.x, source.y, source.z}, false, ignored, true);
        if (moved) actor->SetActorHiddenInGame(false);
        if (!moved || !present_drop(state))
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
        const auto flag = cash_flag(actor, scope->world.Get());
        if (!amount || flag.empty() || !wallet.bind(instance) || object(pawn, STR("As Game Instance FP")) != instance ||
            !mark || mark->GetOuterPrivate() != find<U::UClass>(base_class) || mark->GetNumParms() != 0 ||
            mark->GetParmsSize() != 0 || mark->GetReturnProperty() || mark->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
            return result(lua, false, "authored_cash_stock_schema_unqualified");
        const auto key = RC::to_wstring(flag);
        const auto before = wallet.amount(instance);
        if (wallet.flag_value(instance, key.c_str()).second)
            return result(lua, false, "authored_cash_stock_flag_already_recorded");
        if (before < 0 || before > (std::numeric_limits<int>::max)() - amount)
            return result(lua, false, "authored_cash_stock_wallet_out_of_range");

        // Preserve the cooked permanent actor flag and stock wallet function in the same guarded turn.
        actor->ProcessEvent(mark, nullptr);
        if (wallet.flag_value(instance, key.c_str()) != std::pair{1, true} || wallet.amount(instance) != before ||
            object(instance, STR("SaveSlot_Ref")) != canonical || !authorized(*scope, pawn, target))
            return result(lua, false, "mutation_unverified_authored_cash_flag_readback");
        wallet.change(instance, amount, false);
        const auto after = wallet.amount(instance);
        if (after != before + amount || wallet.flag_value(instance, key.c_str()) != std::pair{1, true} ||
            object(instance, STR("SaveSlot_Ref")) != canonical || !authorized(*scope, pawn, target) || !set_visibility(*scope, true))
            return result(lua, false, "mutation_unverified_authored_cash_settlement");
        result(lua, true, "cash_credited");
        lua.set_integer(amount);
        lua.set_integer(before);
        lua.set_integer(after);
        lua.set_string(flag);
        return 6;
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
                intercept(call, context, fn, parms, nullptr);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* context, U::FFrame& frame, void*) {
                intercept(call, context, frame.Node(), frame.Locals(), &frame);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup script")});
        const bool ok = std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; });
        if (!ok) uninstall_hooks();
        installed.store(ok, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
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
        lua.register_function("SilverfishTakeNativeAuthoredPickupPress", take_press);
        lua.register_function("SilverfishProjectNativeAuthoredPickup", project);
        lua.register_function("SilverfishReadNativeAuthoredPickup", status);
        lua.register_function("SilverfishObserveNativeCrossroadsDeadDrop", observe_drop);
        lua.register_function("SilverfishProjectNativeCrossroadsDeadDrop", project_drop);
        lua.register_function("SilverfishClearNativeCrossroadsDeadDrop", clear_drop);
        lua.register_function("SilverfishCaptureNativeAuthoredPickup", capture);
        lua.register_function("SilverfishClaimNativeAuthoredCash", claim_cash);
        lua.register_function("SilverfishVerifyNativeAuthoredPickup", verify_escrow);
        lua.register_function("SilverfishMergeNativeAuthoredSupply", merge_supply);
        lua.register_function("SilverfishExchangeStoneMazeTape", exchange_tape<stone_maze_tape>);
        lua.register_function("SilverfishExchangeEatherPumpTape", exchange_tape<eather_pump_tape>);
        lua.register_function("SilverfishExchangeDrillTestResultsTape", exchange_tape<drill_test_results_tape>);
        lua.register_function("SilverfishClearNativeAuthoredPickup", reset);
        if (headless()) lua.register_function("SilverfishProbeNativeAuthoredPickup", probe);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) clear();
    }
}
