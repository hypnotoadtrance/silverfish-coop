#include "zone_exit.hpp"
#include "authored_exit_registry.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/SoftObjectPtr.hpp>
#include <Unreal/UAssetRegistryHelpers.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    namespace Authored = silverfish::authored_exits;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto exit_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C");
    constexpr auto overlap_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:BndEvt__Level_Exit_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto activate_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:ActivateLevel");
    constexpr auto prep_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Prep_OpenNextLevel");
    constexpr auto load_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:LoadNextLevel");
    constexpr auto load_save_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Load_GameState");
    constexpr auto door_path = STR("/Game/Classes/Actors/Dungeon_TransitionDoor_BP.Dungeon_TransitionDoor_BP_C:SendInteractionToActor");
    constexpr auto door_power_path = STR("/Game/Classes/Actors/Dungeon_TransitionDoor_BP.Dungeon_TransitionDoor_BP_C:Send_PowerSignal");
    constexpr auto exit_power_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:Send_PowerSignal");
    constexpr auto crossroads_world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto cistern_world = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P");
    constexpr auto railway_world = STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P");
    constexpr auto ruins_world = STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P");
    constexpr auto northeast_world = STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P.NorthEast_Rail_P");
    constexpr auto slums_world = STR("/Game/Classes/Maps/Maps_OverWorld/Slums_P.Slums_P");
    constexpr auto city_world = STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P");
    constexpr auto lake_world = STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P");
    constexpr auto exchange_world = STR("/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P.Exchange_01_P");
    constexpr auto rook_world = STR("/Game/Classes/Maps/Dreams/TheRook_P.TheRook_P");
    constexpr auto pyroclast_world = STR("/Game/Classes/Maps/Dreams/Pyroclast_P.Pyroclast_P");
    constexpr auto slate_world = STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P");
    constexpr auto black_ocean_world = STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P");
    constexpr auto slate_trigger_path = STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P:PersistentLevel.TriggerBox_1");
    constexpr auto slate_overlap_path = STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P_C:BndEvt__Slate_P_TriggerBox_1_K2Node_ActorBoundEvent_0_ActorBeginOverlapSignature__DelegateSignature");
    constexpr auto black_stone_path = STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P:PersistentLevel.Art_BlackStone_C_1");
    constexpr auto black_stone_reference = STR("Art_BlackStone_C_1_ExecuteUbergraph_BlackOcean_P_RefProperty");
    constexpr auto actor_overlap_path = STR("/Script/Engine.Actor:IsOverlappingActor");
    constexpr auto dream_power_path = STR("/Game/Classes/Actors/Dream_CountDown_BP.Dream_CountDown_BP_C:Send_PowerSignal");
    constexpr auto dream_script_path = STR("/Game/Classes/Actors/Dream_CountDown_BP.Dream_CountDown_BP_C:ExecuteUbergraph_Dream_CountDown_BP");
    constexpr int countdown_timed_return = 588;
    constexpr int countdown_signal_return = 15;
    constexpr auto ttl = std::chrono::seconds(2);
    constexpr auto approach_ttl = std::chrono::milliseconds(850);
    constexpr double approach_displacement_squared = 800.0 * 800.0;
    constexpr double max_distance_squared = 1500.0 * 1500.0;
    constexpr double max_interaction_squared = 350.0 * 350.0;
    constexpr std::size_t max_remote_players = 4;

    struct CountdownRoute
    {
        RC::StringViewType world;
        const RC::CharType* actor;
        bool accepts_world_power;
        bool accepts_host_power;
    };

    constexpr auto countdown_routes = std::array{
        CountdownRoute{exchange_world,
            STR("/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P.Exchange_01_P:PersistentLevel.Dream_CountDown_BP_C_1"), true, true},
        CountdownRoute{rook_world, STR("/Game/Classes/Maps/Dreams/TheRook_P.TheRook_P:PersistentLevel.Dream_CountDown_BP_C_1"),
            false, true},
        CountdownRoute{pyroclast_world, STR("/Game/Classes/Maps/Dreams/Pyroclast_P.Pyroclast_P:PersistentLevel.Dream_CountDown_BP_C_1"),
            false, false},
    };

    struct ScriptedDreamRoute
    {
        RC::StringViewType world;
        const RC::CharType* actor;
        const RC::CharType* script;
        const RC::CharType* early_event;
        std::span<const int> blocked_entries;
    };

    constexpr std::array slate_return_entries{15, 923};
    constexpr std::array black_ocean_return_entries{70, 715, 992};
    constexpr std::array scripted_dream_routes{
        ScriptedDreamRoute{slate_world,
            STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P:PersistentLevel.Slate_P_C_0"),
            STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P_C:ExecuteUbergraph_Slate_P"),
            slate_overlap_path, slate_return_entries},
        ScriptedDreamRoute{black_ocean_world,
            STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P:PersistentLevel.BlackOcean_P_C_0"),
            STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P_C:ExecuteUbergraph_BlackOcean_P"),
            STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P_C:PickedUpStone"), black_ocean_return_entries},
    };

    struct ActorOverlapParams
    {
        Unreal::UObject* other{};
        bool overlapping{};
    };

    struct DoorRoute
    {
        RC::StringViewType actor;
        RC::StringViewType destination;
        RC::StringViewType entrance;
    };

    struct Route
    {
        RC::StringViewType world;
        RC::StringViewType exit;
        RC::StringViewType destination;
        RC::StringViewType entrance;
        DoorRoute door{};
        std::array<RC::StringViewType, 2> alternate_exits{};
        RC::StringViewType secondary_exit{};
        RC::StringViewType secondary_destination{};
        RC::StringViewType secondary_entrance{};
    };

    constexpr std::array routes{
        Route{STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01"), STR("Level_Exit_BP_C_0"), {}, {}},
        Route{STR("/Game/Classes/Maps/PlaytestMap_02.PlaytestMap_02"), STR("Level_Exit_BP_C_0"), {}, {}},
        Route{crossroads_world, STR("Level_Exit_BP_C_275"),
              STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"), STR("DB_CR"),
              {STR("Dungeon_TransitionDoor_BP_C_1"), cistern_world, STR("CR_Cstrn")}, {}, STR("Level_Exit_BP_C_2"),
              STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"), STR("Obsv_Etr")},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"), STR("Level_Exit_BP_C_3"),
              crossroads_world, STR("DB_CR")},
        Route{cistern_world, {}, {}, {}, {STR("Dungeon_TransitionDoor_BP_C_1"), crossroads_world, STR("CR_Cstrn")}},
        Route{STR("/Game/Classes/Maps/Dreams/GoldNexus_P.GoldNexus_P"), STR("Level_Exit_BP_C_178"),
              crossroads_world, STR("Nexus_CrossRoads"), {},
              {STR("Level_Exit_BP_C_181"), STR("Level_Exit_BP_C_182")}},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"), STR("Level_Exit_BP_C_1"),
              crossroads_world, STR("Obsv_Etr")},
        Route{exchange_world, {}, {}, {}},
        Route{rook_world, {}, {}, {}},
        Route{pyroclast_world, {}, {}, {}},
        Route{slate_world, {}, {}, {}},
        Route{black_ocean_world, {}, {}, {}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_1"), railway_world, STR("RY_Lab206")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P.Lab_EastWing_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), ruins_world, STR("RgEast_Flood")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P.OldPipes_01_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), northeast_world, STR("NE_Pipe")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P.Slums_Tunnel_01_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_1"), slums_world, STR("Slums_GreenLine")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Sci_Tunnel_01_P.Sci_Tunnel_01_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), cistern_world, STR("Cistern_Sci")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_1"), city_world, STR("Subway")}},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), railway_world, STR("Rail_Utility")}},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/TrashPit_P.TrashPit_P"), STR("Level_Exit_BP_C_169"),
              northeast_world, STR("M35_Maw")},
        Route{railway_world, STR("Level_Exit_BP_C_0"), crossroads_world, STR("Depot_Cross_Rail")},
        Route{ruins_world, STR("Level_Exit_BP_C_1"), crossroads_world, STR("CR_RF")},
        Route{northeast_world, STR("Level_Exit_BP_C_269"), crossroads_world, STR("Cross_NE")},
        Route{slums_world, STR("Level_Exit_BP_C_0"), railway_world, STR("Slum_Depot")},
        Route{city_world, STR("Level_Exit_BP_C_1"), railway_world, STR("Rail_City")},
        Route{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_2"), slums_world, STR("Bar_Slums")}},
        Route{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), northeast_world, STR("ShopKeep")}},
        Route{STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"), STR("Level_Exit_BP_C_1"),
              city_world, STR("City_Borough")},
        Route{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_0"), lake_world, STR("BlackMarket")}},
        Route{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"), {}, {}, {},
              {STR("Dungeon_TransitionDoor_BP_C_2"), ruins_world, STR("Market_Flood")}},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P"), STR("Level_Exit_BP_C_1"),
              crossroads_world, STR("CR_Forest")},
        Route{lake_world, STR("Level_Exit_BP_C_2"), crossroads_world, STR("Lake_Cave")},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/Beach_01_P.Beach_01_P"), STR("Level_Exit_BP_C_0"),
              ruins_world, STR("Flood_Beach")},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/Docks_01_P.Docks_01_P"), STR("Level_Exit_BP_C_2"),
              city_world, STR("Metro_Dock")},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P.Refinery_01_P"), STR("Level_Exit_BP_C_242"),
              slums_world, STR("Slum_Refine")},
    };

    std::atomic<std::uint64_t> generation{};
    std::atomic<bool> installed{};
    std::array<Hook::GlobalCallbackId, 4> callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};

    struct RemotePlayer
    {
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr connection;
    };

    struct GameRoster
    {
        std::array<RemotePlayer, max_remote_players> remotes;
        std::size_t count{};
        Unreal::FWeakObjectPtr driver;
        Unreal::FObjectProperty* world_driver{};
        Unreal::FObjectProperty* server_connection{};
        Unreal::FArrayProperty* client_connections{};
        Unreal::FObjectProperty* connection_entry{};
        Unreal::FObjectProperty* pawn_controller{};
        Unreal::FObjectProperty* controller_player{};
        Unreal::FObjectProperty* controller_pawn{};

        [[nodiscard]] std::span<const RemotePlayer> players() const { return {remotes.data(), count}; }
    };

    struct Point
    {
        double x{}, y{}, z{};

        [[nodiscard]] bool finite() const { return std::isfinite(x) && std::isfinite(y) && std::isfinite(z); }
        [[nodiscard]] Point operator-(const Point& other) const { return {x - other.x, y - other.y, z - other.z}; }
        [[nodiscard]] double dot(const Point& other) const { return x * other.x + y * other.y + z * other.z; }
    };

    struct ExitApproach
    {
        Unreal::FWeakObjectPtr actor;
        Point position;
        Clock::time_point expires{};
    };

    struct DiscoveredExit
    {
        RC::StringType destination;
        RC::StringType entrance;
        std::int32_t time_step{};
        bool operator==(const DiscoveredExit&) const = default;
    };

    struct ExitCrossing
    {
        Unreal::FWeakObjectPtr actor;
        Unreal::FWeakObjectPtr box;
        Unreal::FWeakObjectPtr door;
        const Authored::Route* route{};
        DiscoveredExit discovered;
        std::int64_t ticket{};
        bool published{};
    };

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool host{};
        bool accepting{};
        bool request_latched{};
        Unreal::FWeakObjectPtr local;
        GameRoster roster;
        Unreal::FWeakObjectPtr fixture;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr dream;
        Unreal::FWeakObjectPtr dream_trigger;
        Unreal::UClass* pawn_class{};
        Unreal::UClass* exit_class{};
        Unreal::UFunction* overlap{};
        Unreal::UFunction* activate{};
        Unreal::UFunction* prep{};
        Unreal::UFunction* load{};
        Unreal::UFunction* load_save{};
        Unreal::UFunction* door{};
        Unreal::UFunction* door_power{};
        Unreal::UFunction* exit_power{};
        Unreal::UFunction* dream_power{};
        Unreal::UFunction* dream_script{};
        Unreal::UFunction* dream_early_event{};
        Unreal::UFunction* actor_overlap{};
        Unreal::FObjectProperty* door_player{};
        Unreal::FObjectProperty* door_hit{};
        Unreal::FObjectProperty* door_instigator{};
        Unreal::FObjectProperty* exit_instigator{};
        Unreal::FBoolProperty* exit_powered{};
        Unreal::FBoolProperty* dream_powered{};
        Unreal::FObjectProperty* dream_instigator{};
        Unreal::FIntProperty* dream_entry{};
        Unreal::FObjectProperty* dream_overlapped_actor{};
        Unreal::FObjectProperty* dream_other_actor{};
        Unreal::FObjectProperty* overlap_actor{};
        Unreal::FObjectProperty* overlap_component{};
        Unreal::FObjectProperty* fixture_box{};
        Unreal::FSoftObjectProperty* next_level{};
        Unreal::FStrProperty* entrance_tag{};
        Unreal::FByteProperty* role{};
        const Route* route{};
        RC::StringType authored_world;
        const Authored::Route* authored_request{};
        ExitApproach approach;
        ExitCrossing crossing;
        Unreal::FWeakObjectPtr probing_exit;
        Unreal::FWeakObjectPtr probing_box;
        const CountdownRoute* countdown{};
        const ScriptedDreamRoute* scripted_dream{};
        std::string_view event;
        std::string_view door_event;
        std::string_view door_detail;
        Unreal::FWeakObjectPtr pending_door;
        std::optional<DiscoveredExit> pending_door_route;
        Unreal::FWeakObjectPtr pending_child;
        Clock::time_point pending_expires{};
        bool dream_return_requested{};
    };

    thread_local Scope scope;
    thread_local std::int64_t crossing_sequence{};

    bool env_flag(const char* name)
    {
        const auto* value = std::getenv(name);
        return value && std::string_view{value} == "1";
    }

    bool enabled()
    {
        return env_flag("SILVERFISH_COOP_NATIVE_ZONE");
    }

    RC::StringViewType expanded_world(RC::StringViewType map)
    {
        if (!env_flag("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES")) return {};
        const auto outgoing = Authored::for_world(map);
        if (!outgoing.empty()) return outgoing.front().source_world;
        const auto incoming = std::ranges::find(Authored::routes, map, &Authored::Route::destination_world);
        return incoming != Authored::routes.end() ? incoming->destination_world : RC::StringViewType{};
    }

    template <typename Property>
    Property* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        if (!object) return nullptr;
        auto* property = Unreal::CastField<Property>(object->GetPropertyByNameInChain(name));
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Unreal::UObject* read_object(Unreal::FObjectProperty* property, void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    Unreal::UWorld* actor_world(Unreal::UObject* object)
    {
        return object ? static_cast<Unreal::AActor*>(object)->GetWorld() : nullptr;
    }

    Address address(const Unreal::UObject* object)
    {
        return reinterpret_cast<Address>(object);
    }

    bool live_object(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject | Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    template <typename Object>
    Object* find(const RC::CharType* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<Object*>(nullptr, nullptr, path);
    }

    const char* game_roster_failure(const Scope& current)
    {
        const auto& roster = current.roster;
        auto* world = current.world.Get();
        auto* driver = roster.driver.Get();
        if (!world || !live_object(driver) || read_object(roster.world_driver, world) != driver)
            return "Waiting for the current world network driver";
        if ((read_object(roster.server_connection, driver) != nullptr) == current.host)
            return "The network driver role does not match the co-op session";
        if (!current.host) return nullptr;

        const auto players = roster.players();
        Unreal::FScriptArrayHelper clients(roster.client_connections, roster.client_connections->ContainerPtrToValuePtr<void>(driver));
        if (clients.Num() != static_cast<int>(players.size())) return "The connected Unreal roster differs from the co-op roster";
        std::array<Unreal::UObject*, max_remote_players> connections{};
        for (int index = 0; index < clients.Num(); ++index)
        {
            auto* connection = read_object(roster.connection_entry, clients.GetRawPtr(index));
            if (!live_object(connection) || std::find(connections.begin(), connections.end(), connection) != connections.end())
            {
                return "An Unreal client connection is unavailable or duplicated";
            }
            connections[static_cast<std::size_t>(index)] = connection;
        }
        for (const auto& remote : players)
        {
            auto* pawn = remote.pawn.Get();
            auto* controller = remote.controller.Get();
            auto* connection = remote.connection.Get();
            if (!live_object(pawn) || !live_object(controller) || !live_object(connection) || actor_world(pawn) != world ||
                current.role->GetPropertyValueInContainer(pawn) != 3 ||
                read_object(roster.pawn_controller, pawn) != controller || read_object(roster.controller_pawn, controller) != pawn ||
                read_object(roster.controller_player, controller) != connection)
            {
                return "A remote pawn no longer matches its controller and client connection";
            }
            const auto match = std::find(connections.begin(), connections.end(), connection);
            if (match == connections.end()) return "A remote pawn's connection is absent from the current driver";
            *match = nullptr;
        }
        return nullptr;
    }

    const char* bind_game_roster(Scope& scope)
    {
        auto& roster = scope.roster;
        auto* world = scope.world.Get();
        roster.world_driver = field<Unreal::FObjectProperty>(world, STR("NetDriver"));
        auto* driver = read_object(roster.world_driver, world);
        if (!live_object(driver)) return "Waiting for the current world network driver";
        roster.driver = driver;
        roster.server_connection = field<Unreal::FObjectProperty>(driver, STR("ServerConnection"));
        roster.client_connections = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        if (!roster.server_connection || !roster.client_connections) return "The network driver connection fields are unavailable";
        roster.connection_entry = Unreal::CastField<Unreal::FObjectProperty>(roster.client_connections->GetInner());
        if (!roster.connection_entry) return "The Unreal client connection array is unavailable";
        if (!roster.count) return game_roster_failure(scope);

        auto* player_controller = find<Unreal::UClass>(STR("/Script/Engine.PlayerController"));
        auto* net_connection = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        roster.pawn_controller = field<Unreal::FObjectProperty>(scope.pawn_class, STR("Controller"));
        roster.controller_player = field<Unreal::FObjectProperty>(player_controller, STR("Player"));
        roster.controller_pawn = field<Unreal::FObjectProperty>(player_controller, STR("Pawn"));
        if (!net_connection || !roster.pawn_controller || !roster.controller_player || !roster.controller_pawn)
            return "The remote pawn's possession fields are unavailable";
        for (auto& remote : std::span{roster.remotes}.first(roster.count))
        {
            auto* pawn = remote.pawn.Get();
            auto* controller = read_object(roster.pawn_controller, pawn);
            auto* connection = read_object(roster.controller_player, controller);
            if (!live_object(controller) || !controller->IsA(player_controller) || !live_object(connection) ||
                !connection->IsA(net_connection))
            {
                return "Waiting for a remote pawn's network controller";
            }
            remote.controller = controller;
            remote.connection = connection;
        }
        return game_roster_failure(scope);
    }

    bool is_local_pawn(Unreal::UObject* pawn)
    {
        auto* controller = read_object(field<Unreal::FObjectProperty>(pawn, STR("Controller")), pawn);
        auto* player = read_object(field<Unreal::FObjectProperty>(controller, STR("Player")), controller);
        auto* local_player = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        return player && local_player && player->IsA(local_player);
    }

    double distance_squared(Unreal::UObject* one, Unreal::UObject* two)
    {
        auto first = static_cast<Unreal::AActor*>(one)->K2_GetActorLocation();
        auto second = static_cast<Unreal::AActor*>(two)->K2_GetActorLocation();
        const auto dx = first.GetX() - second.GetX();
        const auto dy = first.GetY() - second.GetY();
        const auto dz = first.GetZ() - second.GetZ();
        return dx * dx + dy * dy + dz * dz;
    }

    Point actor_position(Unreal::UObject* actor)
    {
        auto at = static_cast<Unreal::AActor*>(actor)->K2_GetActorLocation();
        return {at.GetX(), at.GetY(), at.GetZ()};
    }

    bool allows_distance_from(const Scope& current, const Point& at)
    {
        if (!at.finite()) return false;
        for (const auto& player : current.roster.players())
        {
            auto* remote = player.pawn.Get();
            if (!remote || actor_world(remote) != current.world.Get() || current.role->GetPropertyValueInContainer(remote) != 3)
            {
                return false;
            }
            const auto delta = actor_position(remote) - at;
            const auto squared = delta.dot(delta);
            if (!std::isfinite(squared) || squared > max_distance_squared) return false;
        }
        return true;
    }

    bool allows_distance(const Scope& current, Unreal::UObject* local)
    {
        return allows_distance_from(current, actor_position(local));
    }

    bool matches_target(Unreal::UObject* exit, Unreal::FSoftObjectProperty* level, Unreal::FStrProperty* entrance_tag,
                        RC::StringViewType target, RC::StringViewType tag)
    {
        if (!level || !entrance_tag || level->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/Engine.World")))
        {
            return false;
        }
        // UE4SS stores the largest supported soft pointer layout in C++. The
        // accessor resolves the reflected engine layout, so do not copy it.
        const auto* soft = level->ContainerPtrToValuePtr<Unreal::FSoftObjectPtr>(exit);
        const auto& path = soft->ToSoftObjectPath();
        const auto destination = path.GetAssetPathString();
        const auto& entrance = entrance_tag->GetPropertyValueInContainer(exit);
        return path.GetSubPathString().IsEmpty() && RC::StringViewType{*destination} == target && RC::StringViewType{*entrance} == tag;
    }

    bool is_secondary_exit(const Scope& current, Unreal::UObject* exit)
    {
        return !current.route->secondary_exit.empty() && exit->GetName() == current.route->secondary_exit;
    }

    bool matches_authored_route(const Scope& current, Unreal::UObject* exit, bool secondary)
    {
        const auto destination = secondary ? current.route->secondary_destination : current.route->destination;
        const auto entrance = secondary ? current.route->secondary_entrance : current.route->entrance;
        return destination.empty() || matches_target(exit, current.next_level, current.entrance_tag, destination, entrance);
    }

    bool in_persistent_level(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        auto* level = read_object(field<Unreal::FObjectProperty>(world, STR("PersistentLevel")), world);
        return level && static_cast<Unreal::AActor*>(actor)->GetLevel() == level;
    }

    bool active_exit_level(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        if (!live_object(actor) || !live_object(world)) return false;
        auto* level = static_cast<Unreal::AActor*>(actor)->GetLevel();
        if (!live_object(level)) return false;
        if (level == read_object(field<Unreal::FObjectProperty>(world, STR("PersistentLevel")), world)) return true;
        auto* visible = field<Unreal::FBoolProperty>(level, STR("bIsVisible"));
        auto* subworld = level->GetOuterPrivate();
        auto* package = live_object(subworld) ? subworld->GetOuterPrivate() : nullptr;
        if (!visible || !visible->GetPropertyValueInContainer(level) || !live_object(package)) return false;

        auto* statics = find<Unreal::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
        auto* lookup = find<Unreal::UFunction>(STR("/Script/Engine.GameplayStatics:GetStreamingLevel"));
        auto* loaded = find<Unreal::UFunction>(STR("/Script/Engine.LevelStreaming:GetLoadedLevel"));
        if (!statics || !lookup || !loaded) return false;
        struct
        {
            Unreal::UObject* context;
            Unreal::FName package;
            Unreal::UObject* result{};
        } args{world, package->GetFName()};
        Unreal::UObject* result{};
        if (lookup->GetNumParms() != 3 || lookup->GetParmsSize() != sizeof(args) ||
            !lookup->GetReturnProperty() || lookup->GetReturnProperty()->GetOffset_Internal() != offsetof(decltype(args), result) ||
            !field<Unreal::FObjectProperty>(lookup, STR("WorldContextObject")) ||
            !field<Unreal::FNameProperty>(lookup, STR("PackageName")) ||
            loaded->GetNumParms() != 1 || loaded->GetParmsSize() != sizeof(result) ||
            !loaded->GetReturnProperty() || loaded->GetReturnProperty()->GetOffset_Internal() != 0) return false;
        statics->ProcessEvent(lookup, &args);
        if (!live_object(args.result)) return false;
        args.result->ProcessEvent(loaded, &result);
        return result == level;
    }

    bool exit_token(RC::StringViewType text)
    {
        return !text.empty() && std::ranges::all_of(text, [](RC::CharType c) {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
        });
    }

    bool package_path(RC::StringViewType package)
    {
        if (package.size() > 240 || !package.starts_with(STR("/"))) return false;
        package.remove_prefix(1);
        if (package.find(STR('/')) == package.npos) return false;
        while (true)
        {
            const auto split = package.find(STR('/'));
            if (!exit_token(package.substr(0, split))) return false;
            if (split == package.npos) return true;
            package.remove_prefix(split + 1);
        }
    }

    bool campaign_map_package(RC::StringViewType package)
    {
        return package != STR("/Game/Classes/Maps/MainMenu_P") && silverfish::world_path::package(package);
    }

    std::string_view map_package_status(RC::StringViewType package)
    {
        if (!package_path(package)) return "invalid-package";
        const auto separator = package.rfind(STR('/'));
        const auto asset_name = package.substr(separator + 1);
        RC::StringType object{package};
        object += STR('.');
        object += asset_name;
        auto* lookup = find<Unreal::UFunction>(STR("/Script/AssetRegistry.AssetRegistry:GetAssetByObjectPath"));
        auto* asset_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.AssetData"));
        auto* class_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.TopLevelAssetPath"));
        auto* result = lookup ? Unreal::CastField<Unreal::FStructProperty>(lookup->GetReturnProperty()) : nullptr;
        auto* object_path = field<Unreal::FNameProperty>(lookup, STR("ObjectPath"));
        auto* disk_only = field<Unreal::FBoolProperty>(lookup, STR("bIncludeOnlyOnDiskAssets"));
        auto* found_package = field<Unreal::FNameProperty>(asset_type, STR("PackageName"));
        auto* found_asset = field<Unreal::FNameProperty>(asset_type, STR("AssetName"));
        auto* found_class = field<Unreal::FStructProperty>(asset_type, STR("AssetClassPath"));
        alignas(16) std::array<std::byte, 384> storage{};
        if (!lookup || !result || !object_path || !disk_only ||
            lookup->GetNumParms() != 3 || lookup->GetParmsSize() > storage.size() ||
            result->GetOffset_Internal() + result->GetSize() > lookup->GetParmsSize()) return "registry-lookup-schema";
        if (!asset_type || result->GetStruct().Get() != asset_type || !class_type ||
            !found_package || !found_asset || !found_class || found_class->GetStruct().Get() != class_type ||
            found_class->GetSize() != sizeof(Unreal::FTopLevelAssetPath)) return "registry-asset-schema";
        auto* registry = Unreal::UAssetRegistryHelpers::GetAssetRegistry().ObjectPointer;
        auto* registry_class = find<Unreal::UClass>(STR("/Script/AssetRegistry.AssetRegistryImpl"));
        // The engine registry may be exposed through its class default object.
        if (!registry || !registry_class || !registry->IsA(registry_class) || registry->IsUnreachable() ||
            registry->HasAnyFlags(static_cast<Unreal::EObjectFlags>(Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed))) return "registry-unavailable";

        result->InitializeValue_InContainer(storage.data());
        const auto release = [result](std::byte* bytes) { result->DestroyValue_InContainer(bytes); };
        const std::unique_ptr<std::byte, decltype(release)> cleanup{storage.data(), release};
        *object_path->ContainerPtrToValuePtr<Unreal::FName>(storage.data()) = Unreal::FName(object.c_str());
        disk_only->SetPropertyValueInContainer(storage.data(), true);
        registry->ProcessEvent(lookup, storage.data());

        const auto* metadata = result->ContainerPtrToValuePtr<void>(storage.data());
        const auto& resolved_package = *found_package->ContainerPtrToValuePtr<Unreal::FName>(metadata);
        if (resolved_package.IsNone()) return "missing";
        const auto& resolved_asset = *found_asset->ContainerPtrToValuePtr<Unreal::FName>(metadata);
        if (resolved_package.ToString() != package || resolved_asset.ToString() != asset_name) return "missing";
        const auto& asset_class = *found_class->ContainerPtrToValuePtr<Unreal::FTopLevelAssetPath>(metadata);
        return asset_class.GetPackageName().ToString() == STR("/Script/Engine") &&
            asset_class.GetAssetName().ToString() == STR("World") ? "world" : "non-world";
    }

    int lua_inspect_map_package(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_string(1)) return 0;
        const auto status = map_package_status(RC::to_wstring(lua.get_string()));
        lua.set_string(std::string{status});
        return 1;
    }

    int lua_snapshot_world_actor_paths(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 || !lua.is_integer(1) ||
            !lua.is_string(2) || !lua.is_string(3)) return 0;
        const auto address_expected = static_cast<Address>(lua.get_integer());
        const auto world_path = RC::to_wstring(lua.get_string());
        const auto kind = lua.get_string();
        const auto* class_path = kind == "destructible"
            ? STR("/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP.Static_DestructableObject_Master_BP_C")
            : kind == "physics-prop" ? STR("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C") : nullptr;
        if (!class_path || !silverfish::world_path::object(RC::StringViewType{world_path})) return 0;
        auto* world = find<Unreal::UWorld>(world_path.c_str());
        auto* base = find<Unreal::UClass>(class_path);
        if (!live_object(world) || address(world) != address_expected || world->GetPathName() != world_path || !base) return 0;

        std::vector<Unreal::AActor*> actors;
        Unreal::UGameplayStatics::GetAllActorsOfClass(world, base, actors);
        std::string paths;
        for (auto* actor : actors)
        {
            if (!live_object(actor)) continue;
            const auto path = RC::to_utf8_string(actor->GetPathName());
            if (path.empty() || path.find_first_of("\r\n\t") != std::string::npos) continue;
            paths.append(path);
            paths.push_back('\n');
        }
        lua.set_string(paths);
        return 1;
    }

    bool uses_stock_function(Unreal::UObject* actor, Unreal::UFunction* stock)
    {
        return stock && actor->GetClassPrivate()->GetFunctionByNameInChain(stock->GetFName()) == stock;
    }

    bool has_route_actor_name(Unreal::UObject* actor)
    {
        const auto name = actor->GetName();
        return name.size() <= 96 && exit_token(name);
    }

    std::optional<DiscoveredExit> read_discovered_route(Unreal::UObject* exit)
    {
        auto* level = field<Unreal::FSoftObjectProperty>(exit, STR("NextLevel"));
        auto* tag = field<Unreal::FStrProperty>(exit, STR("Entrance Tag"));
        auto* duration = field<Unreal::FDoubleProperty>(exit, STR("Travel_TimeStep"));
        if (!level || !tag || !duration || level->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/Engine.World")))
            return {};
        const auto& path = level->ContainerPtrToValuePtr<Unreal::FSoftObjectPtr>(exit)->ToSoftObjectPath();
        if (!path.GetSubPathString().IsEmpty()) return {};
        const auto asset = path.GetAssetPathString();
        const RC::StringViewType object{*asset};
        const RC::StringViewType entrance{*tag->GetPropertyValueInContainer(exit)};
        const auto split = object.find(STR('.'));
        const auto slash = object.rfind(STR('/'));
        if (split == object.npos || slash >= split || !campaign_map_package(object.substr(0, split)) ||
            object.substr(slash + 1, split - slash - 1) != object.substr(split + 1) ||
            !exit_token(object.substr(split + 1)) || object == scope.authored_world ||
            entrance.size() > 64 || entrance == STR("None") || !exit_token(entrance)) return {};
        const double time = duration->GetPropertyValueInContainer(exit);
        if (!std::isfinite(time) || time < 0 || time > 2147483647 || std::trunc(time) != time) return {};
        return DiscoveredExit{RC::StringType{object.substr(0, split)}, RC::StringType{entrance}, static_cast<std::int32_t>(time)};
    }

    std::optional<DiscoveredExit> discover_exit(Unreal::UObject* exit, Unreal::UObject* box, Unreal::UWorld* world)
    {
        if (scope.authored_world.empty() || !scope.exit_class || !live_object(exit) || !live_object(box) ||
            !exit->GetClassPrivate()->IsChildOf(scope.exit_class) || !active_exit_level(exit, world) ||
            box->GetOuterPrivate() != exit || box != read_object(field<Unreal::FObjectProperty>(exit, STR("Box")), exit) ||
            box->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.BoxComponent")) ||
            !has_route_actor_name(exit) || !uses_stock_function(exit, scope.overlap) ||
            !uses_stock_function(exit, scope.activate) || !uses_stock_function(exit, scope.exit_power)) return {};
        return read_discovered_route(exit);
    }

    const Authored::Route* expanded_route(const Scope& current, Unreal::UObject* actor)
    {
        auto* world = static_cast<Unreal::UWorld*>(current.world.Get());
        return !current.authored_world.empty() && live_object(actor) && world && in_persistent_level(actor, world)
            ? Authored::find(current.authored_world, actor->GetName()) : nullptr;
    }

    bool is_expanded_exit(const Scope& current, Unreal::UObject* actor, Unreal::UWorld* world, const Authored::Route& route)
    {
        return route.mode == Authored::Mode::overlap && live_object(actor) && actor->GetClassPrivate() == current.exit_class &&
               in_persistent_level(actor, world);
    }

    bool matches_expanded_exit(Unreal::UObject* actor, const Authored::Route& route)
    {
        if (!matches_target(actor, field<Unreal::FSoftObjectProperty>(actor, STR("NextLevel")),
                            field<Unreal::FStrProperty>(actor, STR("Entrance Tag")), route.destination_world, route.entrance))
        {
            return false;
        }
        if (!route.travel_time_step) return true;
        auto* time_step = field<Unreal::FDoubleProperty>(actor, STR("Travel_TimeStep"));
        return time_step && time_step->GetPropertyValueInContainer(actor) == *route.travel_time_step;
    }

    bool is_route_exit(const Scope& current, Unreal::UObject* actor, Unreal::UWorld* world)
    {
        if (!current.route || actor->GetClassPrivate() != current.exit_class || !in_persistent_level(actor, world))
        {
            return false;
        }
        const auto name = actor->GetName();
        if (name == current.route->exit || (!current.route->secondary_exit.empty() && name == current.route->secondary_exit))
        {
            return true;
        }
        for (const auto alternate : current.route->alternate_exits)
        {
            if (!alternate.empty() && name == alternate) return true;
        }
        return false;
    }

    bool bind_scripted_dream(Scope& next, const ScriptedDreamRoute& route, Unreal::UWorld* world)
    {
        auto* dream = find<Unreal::UObject>(route.actor);
        auto* level = read_object(field<Unreal::FObjectProperty>(world, STR("PersistentLevel")), world);
        auto* level_script_class = find<Unreal::UClass>(STR("/Script/Engine.LevelScriptActor"));
        if (!live_object(dream) || !level_script_class || !dream->IsA(level_script_class) ||
            read_object(field<Unreal::FObjectProperty>(level, STR("LevelScriptActor")), level) != dream ||
            actor_world(dream) != world || !in_persistent_level(dream, world)) return false;

        next.load_save = find<Unreal::UFunction>(load_save_path);
        next.dream_script = find<Unreal::UFunction>(route.script);
        next.dream_entry = field<Unreal::FIntProperty>(next.dream_script, STR("EntryPoint"));
        next.dream_early_event = find<Unreal::UFunction>(route.early_event);
        if (!next.load_save || !next.dream_script || !next.dream_entry || !next.dream_early_event ||
            next.load_save->GetOuterPrivate() != next.instance.Get()->GetClassPrivate() ||
            next.dream_script->GetOuterPrivate() != dream->GetClassPrivate() ||
            next.dream_script->GetNumParms() != 1 || next.dream_script->GetReturnProperty() ||
            next.dream_entry->GetOffset_Internal() != 0 || next.dream_script->GetParmsSize() != sizeof(int) ||
            next.dream_early_event->GetOuterPrivate() != dream->GetClassPrivate() ||
            next.dream_early_event->GetNumParms() != 2 || next.dream_early_event->GetReturnProperty()) return false;

        if (route.world == slate_world)
        {
            auto* trigger = find<Unreal::UObject>(slate_trigger_path);
            auto* trigger_class = find<Unreal::UClass>(STR("/Script/Engine.TriggerBox"));
            next.dream_overlapped_actor = field<Unreal::FObjectProperty>(next.dream_early_event, STR("OverlappedActor"));
            next.dream_other_actor = field<Unreal::FObjectProperty>(next.dream_early_event, STR("OtherActor"));
            next.actor_overlap = find<Unreal::UFunction>(actor_overlap_path);
            auto* overlap_other = field<Unreal::FObjectProperty>(next.actor_overlap, STR("Other"));
            auto* overlap_result = field<Unreal::FBoolProperty>(next.actor_overlap, STR("ReturnValue"));
            if (!live_object(trigger) || trigger->GetClassPrivate() != trigger_class ||
                actor_world(trigger) != world || !in_persistent_level(trigger, world) ||
                !next.dream_overlapped_actor || !next.dream_other_actor || !next.actor_overlap ||
                !overlap_other || !overlap_result || next.actor_overlap->GetNumParms() != 2 ||
                !next.actor_overlap->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
                next.actor_overlap->GetReturnProperty() != overlap_result ||
                overlap_other->GetOffset_Internal() != offsetof(ActorOverlapParams, other) ||
                overlap_result->GetOffset_Internal() != offsetof(ActorOverlapParams, overlapping) ||
                next.actor_overlap->GetParmsSize() < offsetof(ActorOverlapParams, overlapping) + sizeof(bool) ||
                next.actor_overlap->GetParmsSize() > sizeof(ActorOverlapParams)) return false;
            next.dream_trigger = trigger;
        }
        else
        {
            auto* source = field<Unreal::FObjectProperty>(dream, black_stone_reference);
            auto* stone = read_object(source, dream);
            auto* stone_class = find<Unreal::UClass>(STR("/Game/Classes/Items/Artifacts/Art_BlackStone.Art_BlackStone_C"));
            if (!source || !field<Unreal::FObjectProperty>(next.dream_early_event, STR("Actor")) ||
                (stone && (stone->GetClassPrivate() != stone_class || stone->GetPathName() != black_stone_path ||
                           (live_object(stone) && (actor_world(stone) != world || !in_persistent_level(stone, world))))))
                return false;
            // The placed stone can disappear on pickup. The timer guard must survive that destruction.
            next.dream_trigger = stone;
        }

        next.scripted_dream = &route;
        next.dream = dream;
        return true;
    }

    void revoke_crossing()
    {
        if (!scope.crossing.ticket) return;
        scope.crossing = {};
        scope.authored_request = nullptr;
        scope.request_latched = false;
        scope.event = "request_unavailable";
    }

    const char* configure(Address local_address, Address exit_address, bool host, bool accepting, std::span<const Address> remote_addresses)
    {
        if (!local_address || remote_addresses.size() > max_remote_players || (!host && !remote_addresses.empty()))
            return "The native zone roster does not match the session role";
        std::array<Address, max_remote_players> sorted{};
        std::copy(remote_addresses.begin(), remote_addresses.end(), sorted.begin());
        const auto roster = std::span{sorted}.first(remote_addresses.size());
        std::sort(roster.begin(), roster.end());
        if (std::find(roster.begin(), roster.end(), Address{}) != roster.end() ||
            std::find(roster.begin(), roster.end(), local_address) != roster.end() ||
            std::adjacent_find(roster.begin(), roster.end()) != roster.end()) return "The native zone roster contains invalid pawn addresses";

        const auto current_generation = generation.load(std::memory_order_acquire);
        if (scope.generation == current_generation && address(scope.local.Get()) == local_address &&
            address(scope.fixture.Get()) == exit_address && scope.host == host && actor_world(scope.local.Get()) == scope.world.Get() &&
            std::ranges::equal(scope.roster.players(), roster,
                [](const RemotePlayer& remote, Address expected) { return address(remote.pawn.Get()) == expected; }))
        {
            if (const auto* failure = game_roster_failure(scope))
            {
                scope.accepting = false;
                scope.approach = {};
                revoke_crossing();
                return failure;
            }
            if (!is_local_pawn(scope.local.Get()))
            {
                scope.accepting = false;
                scope.approach = {};
                revoke_crossing();
                return "Waiting for the local player to possess the zone pawn";
            }
            scope.expires = Clock::now() + ttl;
            scope.accepting = accepting;
            return nullptr;
        }

        // Rebinding a visit or roster must not preserve permission from the old scope,
        // even while the new one waits for its pawns.
        scope.approach = {};
        revoke_crossing();
        Scope next;
        next.pawn_class = find<Unreal::UClass>(pawn_path);
        next.exit_class = find<Unreal::UClass>(exit_path);
        next.roster.count = roster.size();
        if (!next.pawn_class || (exit_address && !next.exit_class)) return "The native zone actor classes are unavailable";
        std::size_t found_remotes{};
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            const auto candidate = address(object);
            if (candidate == local_address && object->GetClassPrivate() == next.pawn_class && live_object(object)) next.local = object;
            if (candidate == exit_address && object->GetClassPrivate() == next.exit_class && live_object(object)) next.fixture = object;
            const auto remote = std::find(roster.begin(), roster.end(), candidate);
            if (remote != roster.end() && object->GetClassPrivate() == next.pawn_class && live_object(object))
            {
                next.roster.remotes[static_cast<std::size_t>(remote - roster.begin())].pawn = object;
                ++found_remotes;
            }
            return next.local.Get() && found_remotes == roster.size() && (!exit_address || next.fixture.Get())
                ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        auto* local = next.local.Get();
        auto* fixture = next.fixture.Get();
        auto* world = actor_world(local);
        if (!world || !is_local_pawn(local)) return "Waiting for the local player to possess the zone pawn";
        if (found_remotes != roster.size()) return "Waiting for the remote zone pawns";
        if (exit_address && !fixture) return "The cached exit no longer identifies a live level exit";
        if (exit_address && actor_world(fixture) != world) return "Waiting for the exit in the active world";
        const auto map = world->GetPathName();
        for (const auto& route : routes)
        {
            if (map == route.world)
            {
                next.route = &route;
                break;
            }
        }
        next.world = world;
        next.authored_world.assign(expanded_world(map));
        if (!next.route && next.authored_world.empty())
        {
            const auto package = RC::StringViewType{map}.substr(0, map.rfind(STR('.')));
            if (!env_flag("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") || !campaign_map_package(package) ||
                (scope.world.Get() != world && map_package_status(package) != "world"))
                return "This world has no qualified co-op routes";
            next.authored_world = map;
        }
        if (fixture)
        {
            const auto* authored = expanded_route(next, fixture);
            if (authored ? !is_expanded_exit(next, fixture, world, *authored) : !is_route_exit(next, fixture, world))
                return "The placed exit differs from its authored route";
        }

        next.instance = read_object(field<Unreal::FObjectProperty>(world, STR("OwningGameInstance")), world);
        next.host = host;
        next.role = field<Unreal::FByteProperty>(next.pawn_class, STR("Role"));
        next.overlap = find<Unreal::UFunction>(overlap_path);
        next.activate = find<Unreal::UFunction>(activate_path);
        next.prep = find<Unreal::UFunction>(prep_path);
        next.load = find<Unreal::UFunction>(load_path);
        next.door = find<Unreal::UFunction>(door_path);
        next.door_power = find<Unreal::UFunction>(door_power_path);
        next.exit_power = find<Unreal::UFunction>(exit_power_path);
        next.door_player = field<Unreal::FObjectProperty>(next.door, STR("Player"));
        next.door_hit = field<Unreal::FObjectProperty>(next.door, STR("HitComponent"));
        next.door_instigator = field<Unreal::FObjectProperty>(next.door_power, STR("Instigator"));
        next.exit_instigator = field<Unreal::FObjectProperty>(next.exit_power, STR("Instigator"));
        next.exit_powered = field<Unreal::FBoolProperty>(next.exit_power, STR("Powered"));
        next.overlap_actor = field<Unreal::FObjectProperty>(next.overlap, STR("OtherActor"));
        next.overlap_component = field<Unreal::FObjectProperty>(next.overlap, STR("OverlappedComponent"));
        next.fixture_box = field<Unreal::FObjectProperty>(fixture, STR("Box"));
        next.next_level = field<Unreal::FSoftObjectProperty>(fixture, STR("NextLevel"));
        next.entrance_tag = field<Unreal::FStrProperty>(fixture, STR("Entrance Tag"));
        if (!next.instance.Get() || !next.role || !next.prep || !next.load)
            return "The native travel functions or game instance are unavailable";
        if (const auto* failure = bind_game_roster(next)) return failure;
        const auto countdown = std::find_if(countdown_routes.begin(), countdown_routes.end(),
            [&](const CountdownRoute& candidate) { return map == candidate.world; });
        if (countdown != countdown_routes.end())
        {
            auto* dream = find<Unreal::UObject>(countdown->actor);
            next.countdown = &*countdown;
            next.load_save = find<Unreal::UFunction>(load_save_path);
            next.dream_power = find<Unreal::UFunction>(dream_power_path);
            next.dream_script = find<Unreal::UFunction>(dream_script_path);
            next.dream_powered = field<Unreal::FBoolProperty>(next.dream_power, STR("Powered"));
            next.dream_instigator = field<Unreal::FObjectProperty>(next.dream_power, STR("Instigator"));
            next.dream_entry = field<Unreal::FIntProperty>(next.dream_script, STR("EntryPoint"));
            if (!dream || !next.load_save || !next.dream_power || !next.dream_script || !next.dream_powered || !next.dream_instigator ||
                !next.dream_entry || dream->GetClassPrivate() != next.dream_power->GetOuterPrivate() ||
                dream->GetClassPrivate() != next.dream_script->GetOuterPrivate() || actor_world(dream) != world ||
                !in_persistent_level(dream, world)) return "The placed dream return is unavailable";
            next.dream = dream;
            next.dream_return_requested = scope.dream.Get() == dream && scope.dream_return_requested;
        }
        else
        {
            const auto scripted = std::find_if(scripted_dream_routes.begin(), scripted_dream_routes.end(),
                [&](const ScriptedDreamRoute& candidate) { return map == candidate.world; });
            if (scripted != scripted_dream_routes.end())
            {
                if (!bind_scripted_dream(next, *scripted, world)) return "The scripted dream return is unavailable";
                next.dream_return_requested = scope.generation == current_generation && scope.local.Get() == local &&
                    scope.dream.Get() == next.dream.Get() && scope.dream_return_requested;
            }
            else if (!next.exit_class || !next.overlap_actor || !next.overlap_component || (fixture && !next.fixture_box) ||
                     !next.activate || !next.exit_power) return "The native exit interaction functions are unavailable";
        }
        next.generation = current_generation;
        next.expires = Clock::now() + ttl;
        next.accepting = accepting;
        scope = next;
        return nullptr;
    }

    bool active_host(Unreal::UObject* local)
    {
        return live_object(local) && scope.host && scope.accepting && Clock::now() < scope.expires &&
               actor_world(local) == scope.world.Get() && !game_roster_failure(scope) && is_local_pawn(local) &&
               scope.role->GetPropertyValueInContainer(local) == 3;
    }

    bool host_is_ready(Unreal::UObject* local)
    {
        return !scope.request_latched && active_host(local);
    }

    Unreal::UObject* matching_door_child(Unreal::UObject* door, const Authored::Route* authored)
    {
        if (!live_object(door) || (authored && authored->mode != Authored::Mode::door)) return nullptr;
        const auto route = authored ? DoorRoute{authored->source_actor, authored->destination_world, authored->entrance}
                                    : scope.route ? scope.route->door : DoorRoute{};
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        if (route.actor.empty() || door->GetName() != route.actor || !scope.door ||
            door->GetClassPrivate() != scope.door->GetOuterPrivate() || actor_world(door) != world ||
            !in_persistent_level(door, world)) return nullptr;

        auto* component = read_object(field<Unreal::FObjectProperty>(door, STR("Level_Exit_BP")), door);
        if (authored && (!live_object(component) || component->GetOuterPrivate() != door ||
            component->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.ChildActorComponent")))) return nullptr;
        auto* child = read_object(field<Unreal::FObjectProperty>(component, STR("ChildActor")), component);
        if (!live_object(child) || child->GetClassPrivate() != scope.exit_class || actor_world(child) != world ||
            (authored && !in_persistent_level(child, world)) ||
            child != read_object(field<Unreal::FObjectProperty>(door, STR("As Level Exit BP")), door)) return nullptr;
        if (!matches_target(door, field<Unreal::FSoftObjectProperty>(door, STR("Next Level")),
                            field<Unreal::FStrProperty>(door, STR("Entrance Tag")), route.destination, route.entrance) ||
            !matches_target(child, field<Unreal::FSoftObjectProperty>(child, STR("NextLevel")),
                            field<Unreal::FStrProperty>(child, STR("Entrance Tag")), route.destination, route.entrance)) return nullptr;
        return child;
    }

    Unreal::UObject* discover_door_child(Unreal::UObject* door, Unreal::UWorld* world)
    {
        if (scope.authored_world.empty() || !active_exit_level(door, world) ||
            !has_route_actor_name(door) || !uses_stock_function(door, scope.door) ||
            !uses_stock_function(door, scope.door_power)) return nullptr;
        auto* component = read_object(field<Unreal::FObjectProperty>(door, STR("Level_Exit_BP")), door);
        if (!live_object(component) || component->GetOuterPrivate() != door ||
            component->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.ChildActorComponent"))) return nullptr;
        auto* child = read_object(field<Unreal::FObjectProperty>(component, STR("ChildActor")), component);
        if (!live_object(child) || !scope.exit_class || !child->IsA(scope.exit_class) ||
            child != read_object(field<Unreal::FObjectProperty>(door, STR("As Level Exit BP")), door) ||
            static_cast<Unreal::AActor*>(child)->GetLevel() != static_cast<Unreal::AActor*>(door)->GetLevel() ||
            !uses_stock_function(child, scope.exit_power) || !uses_stock_function(child, scope.activate)) return nullptr;
        return child;
    }

    std::optional<DiscoveredExit> discover_door_route(Unreal::UObject* door, Unreal::UObject* child)
    {
        if (!child) return {};
        auto route = read_discovered_route(child);
        if (!route) return {};
        auto destination = route->destination + STR('.') + route->destination.substr(route->destination.rfind(STR('/')) + 1);
        if (!matches_target(door, field<Unreal::FSoftObjectProperty>(door, STR("Next Level")),
                            field<Unreal::FStrProperty>(door, STR("Entrance Tag")), destination, route->entrance)) return {};
        return route;
    }

    bool approaching(Unreal::UObject* actor, const Point& at)
    {
        const auto delta = at - scope.approach.position;
        return scope.approach.actor.Get() == actor && Clock::now() < scope.approach.expires &&
            delta.finite() && delta.dot(delta) <= approach_displacement_squared;
    }

    bool crossing_current(Unreal::UObject* local)
    {
        const auto& crossing = scope.crossing;
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        auto* exit = crossing.actor.Get();
        // The exact stock overlap commits the route. Keep that decision while the
        // source checkpoints, provided the world, pawn, exit and live roster still match.
        if (!scope.request_latched || !crossing.ticket || !world || !scope.host ||
            !live_object(local) || actor_world(local) != world || !is_local_pawn(local) ||
            scope.role->GetPropertyValueInContainer(local) != 3 || game_roster_failure(scope)) return false;
        if (auto* door = crossing.door.Get())
        {
            auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
            return locked && !locked->GetPropertyValueInContainer(door) && allows_distance(scope, local) &&
                exit && discover_door_child(door, world) == exit && discover_door_route(door, exit) == crossing.discovered;
        }
        return crossing.route
            ? is_expanded_exit(scope, exit, world, *crossing.route) && matches_expanded_exit(exit, *crossing.route)
            : discover_exit(exit, crossing.box.Get(), world) == crossing.discovered;
    }

    Unreal::UObject* exit_by_name(std::string_view name)
    {
        if (name.empty() || name.size() > 512 || scope.authored_world.empty()) return nullptr;
        const auto key = RC::to_wstring(name);
        RC::StringType path;
        if (exit_token(key))
        {
            path = scope.authored_world;
            path += STR(":PersistentLevel.");
            path += key;
        }
        else
        {
            constexpr RC::StringViewType level_separator{STR(":PersistentLevel.")};
            const auto level = key.find(level_separator);
            if (!key.starts_with(STR("/")) || level == key.npos ||
                !exit_token(RC::StringViewType{key}.substr(level + level_separator.size()))) return nullptr;
            path = key;
        }
        auto* actor = find<Unreal::UObject>(path.c_str());
        return live_object(actor) && scope.exit_class && actor->IsA(scope.exit_class) && actor->GetPathName() == path
            ? actor : nullptr;
    }

    std::optional<DiscoveredExit> unregistered_exit(Unreal::UObject* exit, Unreal::UWorld* world)
    {
        if (!live_object(exit) || expanded_route(scope, exit) || is_route_exit(scope, exit, world)) return {};
        return discover_exit(exit, read_object(field<Unreal::FObjectProperty>(exit, STR("Box")), exit), world);
    }

    int lua_discovered_overlap(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire) ||
            Clock::now() >= scope.expires || lua.get_stack_size() != 2 || !lua.is_string(1) || !lua.is_integer(2)) return 0;
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        auto* exit = exit_by_name(lua.get_string());
        if (!world || !exit || address(exit) != static_cast<Address>(lua.get_integer())) return 0;
        const auto route = unregistered_exit(exit, world);
        if (!route) return 0;
        lua.set_string(RC::to_string(route->destination));
        lua.set_string(RC::to_string(route->entrance));
        return 2;
    }

    int lua_exit_approach(const Lua& lua)
    {
        bool allowed{};
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 1 && lua.is_string(1))
        {
            const auto name = lua.get_string();
            auto* local = scope.local.Get();
            if (scope.crossing.ticket && !scope.authored_request && !crossing_current(local)) revoke_crossing();
            auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
            if (!name.empty() && name.size() <= 512 && world && !scope.authored_world.empty())
            {
                const auto actor_name = RC::to_wstring(name);
                const auto same_exit = [&](Unreal::UObject* exit) {
                    return exit && (exit_token(actor_name)
                        ? exit->GetName() == actor_name && in_persistent_level(exit, world)
                        : exit->GetPathName() == actor_name);
                };
                auto* exit = scope.crossing.actor.Get();
                if (same_exit(exit))
                {
                    allowed = scope.authored_request || crossing_current(local);
                }
                else if (host_is_ready(local))
                {
                    const auto position = actor_position(local);
                    exit = scope.approach.actor.Get();
                    if (!allows_distance_from(scope, position))
                    {
                        allowed = same_exit(exit) && approaching(exit, position);
                    }
                    else
                    {
                        if (!same_exit(exit)) exit = exit_by_name(name);
                        const auto* route = expanded_route(scope, exit);
                        const bool qualified = route
                            ? is_expanded_exit(scope, exit, world, *route) && matches_expanded_exit(exit, *route)
                            : unregistered_exit(exit, world).has_value();
                        if (qualified)
                        {
                            scope.approach.actor = exit;
                            scope.approach.position = position;
                            scope.approach.expires = Clock::now() + approach_ttl;
                            allowed = true;
                        }
                    }
                }
            }
            if (!allowed) scope.approach = {};
        }
        lua.set_bool(allowed);
        return 1;
    }

    int lua_exit_crossing(const Lua& lua)
    {
        bool accepted{};
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 3 && lua.is_integer(1) && lua.is_string(2) && lua.is_integer(3))
        {
            const auto ticket = lua.get_integer();
            const auto destination = lua.get_string();
            const auto step = lua.get_integer();
            const auto& crossing = scope.crossing;
            const RC::StringViewType expected = crossing.route ? crossing.route->destination_package : crossing.discovered.destination;
            const auto time = crossing.route ? crossing.route->travel_time_step.value_or(0) : crossing.discovered.time_step;
            const bool matching = ticket > 0 && ticket == crossing.ticket &&
                destination == RC::to_utf8_string(expected) && step == time;
            if (matching && crossing.published)
            {
                accepted = crossing_current(scope.local.Get());
                if (!accepted) revoke_crossing();
            }
        }
        lua.set_bool(accepted);
        return 1;
    }

    void queue_request(const Authored::Route* authored, std::string_view legacy_event)
    {
        scope.request_latched = true;
        scope.authored_request = authored;
        scope.event = authored ? std::string_view{} : legacy_event;
    }

    void handle_door_interaction(Iteration& iteration, Unreal::UObject* door, void* locals)
    {
        auto* player = read_object(scope.door_player, locals);
        auto* local = scope.local.Get();
        if (!scope.host || !player || player != local)
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "guest_denied";
            return;
        }
        const auto* authored = expanded_route(scope, door);
        auto* child = matching_door_child(door, authored);
        std::optional<DiscoveredExit> discovered;
        if (!child && !authored)
        {
            child = discover_door_child(door, static_cast<Unreal::UWorld*>(scope.world.Get()));
            discovered = discover_door_route(door, child);
            if (!discovered) child = nullptr;
        }
        auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
        auto* hit = read_object(scope.door_hit, locals);
        if (!child || !locked || !hit || (hit != read_object(field<Unreal::FObjectProperty>(door, STR("Box")), door) &&
            hit != read_object(field<Unreal::FObjectProperty>(door, STR("SK_DungeonDoor_01")), door)))
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "unsupported_door";
            return;
        }
        if (!host_is_ready(local) || !(distance_squared(local, door) <= max_interaction_squared) || !allows_distance(scope, local))
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "host_out_of_range";
            return;
        }
        if (locked->GetPropertyValueInContainer(door))
        {
            scope.door_event = "host_locked";
            return;
        }

        // The stock unlocked branch starts its animation, then powers this
        // exact child after 0.75 seconds. That child owns the travel handoff.
        scope.pending_door = door;
        scope.pending_door_route = std::move(discovered);
        scope.pending_child = child;
        scope.pending_expires = Clock::now() + ttl;
        scope.door_event = "host_opening";
    }

    void handle_exit_power(Iteration& iteration, Unreal::UObject* context, void* locals)
    {
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented() || context != scope.pending_child.Get()) return;
        auto* door = scope.pending_door.Get();
        auto* local = scope.local.Get();
        auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
        const auto* authored = expanded_route(scope, door);
        const auto discovered = std::exchange(scope.pending_door_route, std::nullopt);
        scope.pending_door.Reset();
        scope.pending_child.Reset();
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        auto* child = discovered ? discover_door_child(door, world) : matching_door_child(door, authored);
        std::string_view detail;
        if (!door || !locked || !locals || !scope.exit_powered || !scope.exit_instigator)
            detail = "stock_power_context";
        else if (!scope.exit_powered->GetPropertyValueInContainer(locals))
            detail = "stock_power_disabled";
        else if (read_object(scope.exit_instigator, locals))
            detail = "stock_power_instigator";
        else if (locked->GetPropertyValueInContainer(door))
            detail = "stock_door_locked";
        else if (child != context)
            detail = "stock_door_child_changed";
        else if (discovered && (discover_door_route(door, child) != discovered ||
            crossing_sequence >= (std::int64_t{1} << 53) - 1))
            detail = "stock_door_route_changed";
        else if (Clock::now() >= scope.pending_expires)
            detail = "stock_power_late";
        else if (!host_is_ready(local))
            detail = "host_not_ready";
        else if (!allows_distance(scope, local))
            detail = "party_out_of_range";
        if (!detail.empty())
        {
            scope.door_event = "handoff_denied";
            scope.door_detail = detail;
            return;
        }
        if (discovered)
        {
            scope.crossing.actor = context;
            scope.crossing.door = door;
            scope.crossing.discovered = *discovered;
            scope.crossing.ticket = ++crossing_sequence;
            scope.request_latched = true;
        }
        else
        {
            queue_request(authored, "door_request");
        }
        scope.door_event = "travel_requested";
    }

    void handle_scripted_dream(Iteration& iteration, Unreal::UFunction* callee, void* locals)
    {
        if (callee == scope.dream_script)
        {
            if (locals && std::ranges::find(scope.scripted_dream->blocked_entries,
                                           scope.dream_entry->GetPropertyValueInContainer(locals)) !=
                          scope.scripted_dream->blocked_entries.end())
                iteration.PreventOriginalFunctionCall();
            return;
        }

        iteration.PreventOriginalFunctionCall();
        // Black Ocean's OnEndPlay callback supplies a stone, not a verified collector.
        if (!iteration.OriginalFunctionCallPrevented() || !scope.actor_overlap || !locals) return;
        auto* local = scope.local.Get();
        auto* trigger = scope.dream_trigger.Get();
        if (!live_object(trigger) || actor_world(trigger) != scope.world.Get() ||
            read_object(scope.dream_overlapped_actor, locals) != trigger ||
            read_object(scope.dream_other_actor, locals) != local || !host_is_ready(local)) return;

        ActorOverlapParams params{local};
        trigger->ProcessEvent(scope.actor_overlap, &params);
        if (params.overlapping) scope.dream_return_requested = true;
    }

    void finish_exit_probe(Iteration&, Unreal::UObject* actor, Unreal::UFunction* function, void*)
    {
        if (actor && function == scope.overlap && Unreal::IsInGameThreadRaw() && actor == scope.probing_exit.Get())
        {
            scope.probing_exit.Reset();
            scope.probing_box.Reset();
        }
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void* locals)
    {
        if (!Unreal::IsInGameThreadRaw() || !context || !callee || !installed.load(std::memory_order_acquire) ||
            scope.generation != generation.load(std::memory_order_acquire)) return;
        if (scope.scripted_dream && scope.dream.Get() == context &&
            (callee == scope.dream_script || callee == scope.dream_early_event))
        {
            if (!iteration.OriginalFunctionCallPrevented()) handle_scripted_dream(iteration, callee, locals);
            return;
        }
        if (scope.countdown && scope.dream.Get() == context &&
            (callee == scope.dream_power || callee == scope.dream_script) && locals &&
            !iteration.OriginalFunctionCallPrevented())
        {
            if (callee == scope.dream_power)
            {
                const auto powered = scope.dream_powered->GetPropertyValueInContainer(locals);
                auto* instigator = read_object(scope.dream_instigator, locals);
                auto* local = scope.local.Get();
                iteration.PreventOriginalFunctionCall();
                const bool host_power = scope.countdown->accepts_host_power && instigator == local;
                const bool world_power = scope.countdown->accepts_world_power &&
                    (!instigator || (instigator->GetClassPrivate() != scope.pawn_class &&
                                    actor_world(instigator) == scope.world.Get()));
                if (powered && iteration.OriginalFunctionCallPrevented() && host_is_ready(local) &&
                    (host_power || world_power))
                {
                    scope.dream_return_requested = true;
                }
            }
            else
            {
                const auto entry = scope.dream_entry->GetPropertyValueInContainer(locals);
                if (entry == countdown_timed_return || entry == countdown_signal_return) iteration.PreventOriginalFunctionCall();
            }
            return;
        }
        const bool is_overlap = callee == scope.overlap;
        const bool is_instance = callee == scope.prep || callee == scope.load || callee == scope.load_save;
        if (!is_overlap && !is_instance && callee != scope.activate && callee != scope.door &&
            callee != scope.exit_power && callee != scope.door_power) return;
        auto* world = scope.world.Get();
        if (!world || iteration.OriginalFunctionCallPrevented()) return;
        if (is_instance)
        {
            if (scope.instance.Get() != context) return;
        }
        else if (callee == scope.door || callee == scope.door_power ||
                 ((is_overlap || callee == scope.activate || callee == scope.exit_power) &&
                  scope.exit_class && context->IsA(scope.exit_class)))
        {
            if (!active_exit_level(context, static_cast<Unreal::UWorld*>(world))) return;
        }
        else if (actor_world(context) != world) return;

        if (callee == scope.door)
        {
            handle_door_interaction(iteration, context, locals);
            return;
        }
        if (callee == scope.exit_power)
        {
            handle_exit_power(iteration, context, locals);
            return;
        }
        if (callee == scope.door_power)
        {
            auto* instigator = read_object(scope.door_instigator, locals);
            if (instigator && instigator->GetClassPrivate() == scope.pawn_class &&
                (!scope.host || instigator != scope.local.Get()))
            {
                iteration.PreventOriginalFunctionCall();
                scope.door_event = "guest_denied";
            }
            return;
        }

        // Let the stock overlap evaluate its own conditions on a new exit. Only a
        // nested, stock ActivateLevel can commit it; every direct load stays blocked.
        auto* local_pawn = scope.local.Get();
        if (callee == scope.activate && context == scope.probing_exit.Get())
        {
            iteration.PreventOriginalFunctionCall();
            auto* box = scope.probing_box.Get();
            scope.probing_exit.Reset();
            scope.probing_box.Reset();
            if (!iteration.OriginalFunctionCallPrevented() || !host_is_ready(local_pawn) ||
                (!approaching(context, actor_position(local_pawn)) && !allows_distance(scope, local_pawn)) ||
                crossing_sequence >= (std::int64_t{1} << 53) - 1) return;
            if (auto route = discover_exit(context, box, static_cast<Unreal::UWorld*>(world)))
            {
                scope.crossing.actor = context;
                scope.crossing.box = box;
                scope.crossing.discovered = std::move(*route);
                scope.crossing.ticket = ++crossing_sequence;
                scope.request_latched = true;
            }
            return;
        }

        auto* other = is_overlap ? read_object(scope.overlap_actor, locals) : nullptr;
        if (other && other->GetClassPrivate() != scope.pawn_class) return;
        if (is_overlap && other && other == local_pawn && host_is_ready(other) &&
            !expanded_route(scope, context) && !is_route_exit(scope, context, static_cast<Unreal::UWorld*>(world)))
        {
            auto* box = read_object(scope.overlap_component, locals);
            if (discover_exit(context, box, static_cast<Unreal::UWorld*>(world)))
            {
                if (approaching(context, actor_position(other)) || allows_distance(scope, other))
                {
                    scope.probing_exit = context;
                    scope.probing_box = box;
                    return;
                }
                iteration.PreventOriginalFunctionCall();
                scope.event = "too_far";
                return;
            }
        }
        iteration.PreventOriginalFunctionCall();
        if (!is_overlap || !iteration.OriginalFunctionCallPrevented() || scope.request_latched || !other) return;
        auto* local = scope.local.Get();
        if (!scope.host || other != local)
        {
            scope.event = "guest_denied";
            return;
        }
        const auto* authored = expanded_route(scope, context);
        auto* exit_world = static_cast<Unreal::UWorld*>(world);
        const bool qualified = authored ? is_expanded_exit(scope, context, exit_world, *authored)
                                       : scope.fixture.Get() && is_route_exit(scope, context, exit_world);
        auto* component = read_object(scope.overlap_component, locals);
        auto* box = authored ? field<Unreal::FObjectProperty>(context, STR("Box")) : scope.fixture_box;
        if (!qualified || !component || component != read_object(box, context) ||
            (authored && (!live_object(component) || component->GetOuterPrivate() != context ||
                component->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.BoxComponent")))))
        {
            scope.event = "unsupported_exit";
            return;
        }
        if (!host_is_ready(local))
        {
            scope.event = "host_not_ready";
            return;
        }
        const auto crossed = actor_position(local);
        const bool crossed_open_wall = authored && approaching(context, crossed);
        if (!crossed_open_wall && !allows_distance(scope, local))
        {
            scope.event = "too_far";
            return;
        }
        const bool secondary = !authored && is_secondary_exit(scope, context);
        if (authored ? !matches_expanded_exit(context, *authored) : !matches_authored_route(scope, context, secondary))
        {
            scope.event = "destination_mismatch";
            return;
        }
        if (authored)
        {
            if (crossing_sequence >= (std::int64_t{1} << 53) - 1) return;
            scope.crossing.actor = context;
            scope.crossing.route = authored;
            scope.crossing.ticket = ++crossing_sequence;
        }
        scope.approach = {};
        queue_request(authored, secondary ? "secondary_request" : "request");
    }

    int lua_set_scope(const Lua& lua)
    {
        const char* failure = "The native zone hook is unavailable or received invalid arguments";
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 5 &&
            lua.is_integer(1) && lua.is_integer(2) && lua.is_integer(3) && lua.is_bool(4) && lua.is_bool(5))
        {
            const auto local = lua.get_integer();
            const auto remote = lua.get_integer();
            const auto fixture = lua.get_integer();
            const auto host = lua.get_bool();
            const auto accepting = lua.get_bool();
            const auto remote_address = static_cast<Address>(remote);
            const auto roster = remote > 0 ? std::span<const Address>{&remote_address, 1} : std::span<const Address>{};
            if (local > 0 && remote >= 0 && fixture >= 0)
                failure = configure(static_cast<Address>(local), static_cast<Address>(fixture), host, accepting, roster);
        }
        lua.set_bool(!failure);
        if (!failure) return 1;
        lua.set_string(failure);
        return 2;
    }

    int lua_set_roster(const Lua& lua)
    {
        const auto count = lua.get_stack_size();
        const char* failure = "The native zone hook is unavailable or received invalid arguments";
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            count >= 4 && count <= 4 + static_cast<int>(max_remote_players) &&
            lua.is_integer(1) && lua.is_integer(2) && lua.is_bool(3) && lua.is_bool(4))
        {
            bool valid = true;
            for (int index = 5; index <= count; ++index)
            {
                if (!lua.is_integer(index)) valid = false;
            }
            if (valid)
            {
                const auto local = lua.get_integer();
                const auto fixture = lua.get_integer();
                const auto host = lua.get_bool();
                const auto accepting = lua.get_bool();
                std::array<Address, max_remote_players> addresses{};
                const auto remotes = std::span{addresses}.first(static_cast<std::size_t>(count - 4));
                for (auto& remote : remotes)
                {
                    const auto value = lua.get_integer();
                    if (value <= 0) valid = false;
                    remote = static_cast<Address>(value);
                }
                if (valid && local > 0 && fixture >= 0)
                    failure = configure(static_cast<Address>(local), static_cast<Address>(fixture), host, accepting, remotes);
            }
        }
        lua.set_bool(!failure);
        if (!failure) return 1;
        lua.set_string(failure);
        return 2;
    }

    int lua_clear(const Lua&)
    {
        silverfish::native_zone::clear_scope();
        return 0;
    }

    int lua_pause(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire))
        {
            scope.accepting = false;
            scope.approach = {};
        }
        return 0;
    }

    int lua_take_event(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire) || scope.event.empty())
        {
            lua.set_nil();
            return 1;
        }
        lua.set_string(scope.event);
        scope.event = {};
        return 1;
    }

    int lua_take_authored_route(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire)) return 0;
        const auto* route = scope.authored_request;
        const auto& crossing = scope.crossing;
        const bool discovered = crossing.ticket && !crossing.route && !crossing.published;
        if (!route && !discovered) return 0;
        scope.authored_request = nullptr;
        auto* local = scope.local.Get();
        if (scope.crossing.ticket ? !crossing_current(local) : !active_host(local) || !allows_distance(scope, local))
        {
            scope.request_latched = false;
            scope.crossing = {};
            scope.event = "request_unavailable";
            return 0;
        }
        scope.crossing.published = scope.crossing.ticket != 0;
        const RC::StringViewType source = discovered ? RC::StringViewType{scope.authored_world} : route->source_world;
        lua.set_string(RC::to_string(source.substr(0, source.rfind(STR('.')))));
        if (discovered)
        {
            lua.set_string(RC::to_string(crossing.discovered.destination));
            lua.set_string(RC::to_string(crossing.discovered.entrance));
            auto* door = crossing.door.Get();
            lua.set_string(RC::to_string((door ? door : crossing.actor.Get())->GetName()));
            lua.set_string(door ? "discovered-door" : "discovered-overlap");
            lua.set_integer(crossing.discovered.time_step);
            lua.set_integer(crossing.ticket);
            return 7;
        }
        lua.set_string(RC::to_string(route->destination_package));
        lua.set_string(RC::to_string(route->entrance));
        lua.set_string(RC::to_string(route->source_actor));
        lua.set_string(route->mode == Authored::Mode::door ? "door" : "overlap");
        if (route->travel_time_step) lua.set_integer(*route->travel_time_step);
        else lua.set_nil();
        if (scope.crossing.ticket) lua.set_integer(scope.crossing.ticket);
        else lua.set_nil();
        return 7;
    }

    int lua_take_door_event(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire) || scope.door_event.empty())
        {
            lua.set_nil();
            return 1;
        }
        lua.set_string(scope.door_event);
        if (scope.door_event == "handoff_denied" && !scope.door_detail.empty()) lua.set_string(scope.door_detail);
        else lua.set_nil();
        scope.door_event = {};
        scope.door_detail = {};
        return 2;
    }

    int lua_release_request(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire))
        {
            scope.request_latched = false;
            scope.authored_request = nullptr;
            scope.approach = {};
            scope.crossing = {};
        }
        return 0;
    }

    int lua_inspect_exchange(const Lua& lua)
    {
        const bool match = Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            scope.generation == generation.load(std::memory_order_acquire) && live_object(scope.dream.Get()) &&
            (!scope.actor_overlap || live_object(scope.dream_trigger.Get())) &&
            lua.get_stack_size() == 1 && lua.is_integer(1) &&
            address(scope.dream.Get()) == static_cast<Address>(lua.get_integer()) && Clock::now() < scope.expires;
        lua.set_bool(match);
        lua.set_string(match ? "native_countdown_guard_ready" : "native_countdown_guard_unavailable");
        return 2;
    }

    int lua_take_exchange_return(const Lua& lua)
    {
        bool requested = false;
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire) &&
            scope.dream.Get() && host_is_ready(scope.local.Get()))
        {
            requested = scope.dream_return_requested;
            scope.dream_return_requested = false;
        }
        lua.set_bool(requested);
        return 1;
    }
}

namespace silverfish::native_zone
{
    bool visible_actor_level(Unreal::AActor* actor, Unreal::UWorld* world)
    {
        return active_exit_level(actor, world);
    }

    void install_hooks()
    {
        if (!enabled()) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(intercept,
            {false, false, owner, STR("Stock level exit event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*) {
                intercept(iteration, context, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Stock level exit script")});
        callbacks[2] = Hook::RegisterProcessEventPostCallback(finish_exit_probe,
            {false, false, owner, STR("Release the stock exit overlap probe")});
        callbacks[3] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            [](Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*) {
                finish_exit_probe(iteration, context, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Release the stock exit script probe")});
        installed.store(std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; }), std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeZoneScope", lua_set_scope);
        lua.register_function("SilverfishSetNativeZoneRoster", lua_set_roster);
        lua.register_function("SilverfishClearNativeZoneScope", lua_clear);
        lua.register_function("SilverfishPauseNativeZoneScope", lua_pause);
        lua.register_function("SilverfishSetNativeExitApproach", lua_exit_approach);
        lua.register_function("SilverfishInspectDiscoveredOverlap", lua_discovered_overlap);
        lua.register_function("SilverfishInspectMapPackage", lua_inspect_map_package);
        lua.register_function("SilverfishSnapshotNativeWorldActorPaths", lua_snapshot_world_actor_paths);
        lua.register_function("SilverfishCheckNativeExitCrossing", lua_exit_crossing);
        lua.register_function("SilverfishTakeNativeZoneEvent", lua_take_event);
        lua.register_function("SilverfishTakeNativeAuthoredRoute", lua_take_authored_route);
        lua.register_function("SilverfishTakeNativeDoorEvent", lua_take_door_event);
        lua.register_function("SilverfishReleaseNativeZoneRequest", lua_release_request);
        lua.register_function("SilverfishInspectNativeExchangeCountdown", lua_inspect_exchange);
        lua.register_function("SilverfishTakeNativeExchangeReturn", lua_take_exchange_return);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_release);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
