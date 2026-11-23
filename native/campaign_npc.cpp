#include "campaign_npc.hpp"
#include "guest_sickness.hpp"
#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/SoftObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using SicknessSnapshot = silverfish::native_guest_sickness::SicknessSnapshot;

    constexpr auto world_package = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto director_class = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C");
    constexpr auto director_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.DynaimicSpawnControler_BP_C_1");
    constexpr auto initial_path = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:Spawn_InitialActors");
    constexpr auto refresh_path = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:RefreshEnemies");
    constexpr auto living_count_path = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:Calc_LivingNPCs");
    constexpr auto marker_check_path = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnIsValid");
    constexpr auto pool_path = STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/CrossRoadsSquadPool.CrossRoadsSquadPool");
    constexpr auto pool_class = STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/AmbientSquadPoolData.AmbientSquadPoolData_C");
    constexpr auto spawner_class = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C");
    constexpr auto spawn_path = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnSquadFromData");
    constexpr auto data_class = STR("/Game/Classes/Libraries/DataAssets/Squads/SquadData.SquadData_C");
    constexpr auto npc_class = STR("/Game/Classes/NPCs/NPC_Zomb_01.NPC_Zomb_01_C");
    constexpr auto flayer_class = STR("/Game/Classes/NPCs/NPC_Flayer.NPC_Flayer_C");
    constexpr auto flayer_squad = STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Flayers.Flayers");
    constexpr auto boar_class = STR("/Game/Classes/NPCs/NPC_WildBoar.NPC_WildBoar_C");
    constexpr std::array boar_squads{
        STR("/Game/Classes/Libraries/DataAssets/Squads/WildLife/Boar_Single.Boar_Single"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/WildLife/Boar_SmallGroup.Boar_SmallGroup"),
    };
    bool operate_boars{};
    bool production_roster{};
    constexpr auto not_deer_class = STR("/Game/Classes/NPCs/NPC_NotDeer.NPC_NotDeer_C");
    constexpr auto not_deer_squad = STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/NotDeer.NotDeer");
    constexpr auto npc_controller = STR("/Game/Classes/NPCs/NPC_Assets/MasterNPC_AI_Controler_BP.MasterNPC_AI_Controler_BP_C");
    constexpr int max_flayers = 2;
    constexpr auto npc_base = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto initialize_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:InitializeAI");
    constexpr auto perceive_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Proscess_TargetPerception");
    constexpr auto melee_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:ResolveMelee");
    constexpr auto player_damage_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage");
    constexpr auto drone_base = STR("/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C");
    constexpr auto drone_damage_path = STR("/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C:DealDamage");
    constexpr auto drone_die_path = STR("/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C:Die");
    constexpr auto turret_base = STR("/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C");
    constexpr auto turret_damage_path = STR("/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C:DealDamage");
    constexpr auto turret_die_path = STR("/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C:Die");
    constexpr auto turret_fire_path = STR("/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C:FireRound");
    struct TurretInactiveTargets
    {
        std::uintptr_t world{};
        std::array<std::uintptr_t, 5> pawns{};
    };
    TurretInactiveTargets turret_inactive;

    constexpr auto npc_die_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Die");
    constexpr auto npc_damage_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage");
    constexpr auto npc_death_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:ResolveDeath");
    constexpr auto projectile_base = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto projectile_calc = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C:CalcDmg");
    constexpr auto light_projectile = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Projectile_Pistol_Light.Projectile_Pistol_Light_C");
    constexpr auto combat_watch_time = std::chrono::seconds(45);
    constexpr int flayer_infection = 8;
    constexpr int flayer_infection_reads = 2;
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto antfarm_class = STR("/Game/Classes/AntFarm/AntfarmController.AntfarmController_C");
    constexpr auto antfarm_squad_class = STR("/Game/Classes/AntFarm/AntfarmSquad.AntfarmSquad_C");
    constexpr auto antfarm_unit_class = STR("/Game/Classes/AntFarm/AntfarmUnit.AntfarmUnit_C");
    constexpr auto load_squad_path = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:LoadSquad");
    constexpr std::array saved_squad_sources{
        STR("/Game/Classes/AntFarm/AntfarmController.AntfarmController_C:LoadActiveCell"),
        STR("/Game/Classes/AntFarm/AntfarmController.AntfarmController_C:LoadSquad"),
        STR("/Game/Classes/AntFarm/AntfarmController.AntfarmController_C:SpawnRadioMission"),
    };
    constexpr std::array marker_numbers{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24,
        26, 27, 28, 30, 31, 32, 34, 35, 37, 38, 40, 41};
    constexpr std::array dead_bridge_markers{1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 15, 16, 17, 19, 20};
    constexpr std::array squad_assets{
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_Scattered.Husks_Scattered"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmSmall.Husks_SwarmSmall"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmLarge.Husks_SwarmLarge"),
    };
    constexpr std::array ruin_fields_squads{squad_assets[0], squad_assets[1], boar_squads[0]};

    struct TypedDirectorProfile
    {
        std::size_t initial_markers;
        std::size_t reinforcement_markers;
        const U::TCHAR* clock;
        bool keep_legacy_families{};
    };

    struct DirectorProfile
    {
        const U::TCHAR* package;
        const U::TCHAR* world;
        const U::TCHAR* director;
        const U::TCHAR* pool;
        const U::TCHAR* marker_prefix;
        std::span<const int> markers;
        const TypedDirectorProfile* typed{};
        std::span<const U::TCHAR* const> directed_squads{};
    };

    constexpr DirectorProfile crossroads{world_package, world_path, director_path, pool_path,
        STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.AI_Spawner_Squad_BP_C_"),
        marker_numbers};
    constexpr DirectorProfile dead_bridge{
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P:PersistentLevel.DynaimicSpawnControler_BP_C_0"),
        STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/DeadBridgeSquadPool.DeadBridgeSquadPool"),
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P:PersistentLevel.AI_Spawner_Squad_BP_C_"),
        dead_bridge_markers};
    // Keep the already-qualified Ruin marker cap and its legacy Husk/boar receipts.
    constexpr TypedDirectorProfile ruin_schema{29, 29,
        STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.DayCycle_SunControler_BP_C_0"), true};
    constexpr TypedDirectorProfile forest_schema{13, 3,
        STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P:PersistentLevel.DayCycle_SunControler_BP_C_0")};
    constexpr DirectorProfile ruin_fields{
        STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.DynaimicSpawnControler_BP_C_0"),
        STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/FloodPlainsSquadPool.FloodPlainsSquadPool"),
        STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.AI_Spawner_Squad_BP_C_"),
        {}, &ruin_schema, ruin_fields_squads};
    constexpr DirectorProfile forest{
        STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P:PersistentLevel.DynaimicSpawnControler_BP_C_0"),
        STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/GoldWoodSquadPool.GoldWoodSquadPool"),
        STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P:PersistentLevel.AI_Spawner_Squad_BP_C_"),
        {}, &forest_schema};
    constexpr auto combined_pool_path = STR("/Game/Classes/Libraries/DataAssets/AmbientSpawnPools/AmbientSquadPoolData.AmbientSquadPoolData_C:GetCombinedPool");
    constexpr auto director_clock_class = STR("/Game/Classes/Actors/DayCycle_SunControler_BP.DayCycle_SunControler_BP_C");
    constexpr std::array ambient_directors{&crossroads, &dead_bridge, &ruin_fields, &forest};
    constexpr std::array maximum_squad_sizes{4, 3, 5};
    constexpr std::size_t max_scene_npcs = 128;

    template<class T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* result = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return result && result->GetArrayDim() == 1 ? result : nullptr;
    }

    U::UObject* get_object(U::FObjectProperty* field, const void* container)
    {
        return field && container ? field->GetObjectPropertyValue(field->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return get_object(field<U::FObjectProperty>(owner, name), owner);
    }

    bool bool_field(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    int byte_field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FByteProperty>(owner, name);
        return property ? property->GetPropertyValueInContainer(owner) : -1;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* path)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(path);
    }

    enum class NpcFamily { Unknown, Husk, Flayer, NotDeer, Boar, Ambient };

    struct AntfarmProfile
    {
        NpcFamily family;
        const U::TCHAR* npc;
        const U::TCHAR* data;
        int faction;
        int maximum;
        std::string_view unverified, denied;
    };

    constexpr AntfarmProfile flayer_profile{NpcFamily::Flayer, flayer_class, flayer_squad, 6, max_flayers,
        "stock_flayer_links_unverified", "stock_client_flayer_load_denied"};
    constexpr AntfarmProfile not_deer_profile{NpcFamily::NotDeer, not_deer_class, not_deer_squad, 7, 1,
        "stock_not_deer_links_unverified", "stock_client_not_deer_load_denied"};

    NpcFamily npc_family(U::UObject* actor)
    {
        auto* type = actor ? actor->GetClassPrivate() : nullptr;
        if (!type) return NpcFamily::Unknown;
        static const U::FName husk{STR("NPC_Zomb_01_C")};
        static const U::FName flayer{STR("NPC_Flayer_C")};
        static const U::FName not_deer{STR("NPC_NotDeer_C")};
        const auto name = type->GetFName();
        if (operate_boars)
        {
            static const U::FName boar{STR("NPC_WildBoar_C")};
            if (name == boar && exact(actor, boar_class)) return NpcFamily::Boar;
        }
        if (name == husk && exact(actor, npc_class)) return NpcFamily::Husk;
        if (name == flayer && exact(actor, flayer_class)) return NpcFamily::Flayer;
        if (name == not_deer && exact(actor, not_deer_class)) return NpcFamily::NotDeer;
        return NpcFamily::Unknown;
    }

    bool squad_spawner(U::UObject* actor)
    {
        static const U::FName type{STR("AI_Spawner_Squad_BP_C")};
        auto* actor_type = actor ? actor->GetClassPrivate() : nullptr;
        return actor_type && actor_type->GetFName() == type && exact(actor, spawner_class);
    }

    bool in_level(U::UObject* actor, U::UWorld* world)
    {
        return live(actor) && static_cast<U::AActor*>(actor)->GetWorld() == world &&
            static_cast<U::AActor*>(actor)->GetLevel() == object(world, STR("PersistentLevel")) &&
            bool_field(actor, STR("bActorIsBeingDestroyed"), false);
    }

    bool env(const char* variable)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), variable) == 0 && size == value.size() && value[0] == '1';
    }

    enum class Seat { Inactive, Host, Client };

    struct Network
    {
        U::UWorld* world{};
        U::UObject* driver{};
        U::UObject* instance{};
        Seat seat{Seat::Inactive};
        const DirectorProfile* director{};
    };

    struct PendingClientLoad
    {
        U::FWeakObjectPtr driver, connection;
        U::FWorldContext* context{};
        const DirectorProfile* director{};
    } pending_client_load;

    bool server_connection(U::UObject* connection, U::UObject* driver)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(driver) && live(connection) && type && connection->IsA(type) && object(connection, STR("Driver")) == driver;
    }

    const DirectorProfile* director_for(std::wstring_view path)
    {
        for (const auto* profile : ambient_directors)
        {
            if ((profile == &crossroads || production_roster) && (path == profile->package || path == profile->world))
                return profile;
        }
        return nullptr;
    }

    Network network(U::UWorld* world, bool during_load = false)
    {
        const auto* profile = live(world) ? director_for(world->GetPathName()) : nullptr;
        if (!profile) return {};
        auto* instance = object(world, STR("OwningGameInstance"));
        if (!exact(instance, instance_class)) return {};
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (live(driver) && object(driver, STR("World")) == world && connection)
        {
            auto* server = get_object(connection, driver);
            if (server_connection(server, driver)) return {world, driver, instance, Seat::Client, profile};
            if (server) return {};
            if (!during_load || driver != pending_client_load.driver.Get()) return {world, driver, instance, Seat::Host, profile};
        }
        if (!during_load || !pending_client_load.context || pending_client_load.director != profile) return {};
        auto* pending = pending_client_load.driver.Get();
        if ((driver && driver != pending) || !server_connection(pending_client_load.connection.Get(), pending)) return {};
        return {world, pending, instance, Seat::Client, profile};
    }

    bool exact_director(U::UObject* actor, const Network& active)
    {
        if (!active.director || !exact(actor, director_class) || actor->GetPathName() != active.director->director ||
            !in_level(actor, active.world) || object(actor, STR("As Game Instance FP")) != active.instance) return false;
        if (active.director == &crossroads) return true;
        auto* pool = object(actor, STR("AmbientSquadPool"));
        if (active.director->typed && !exact(pool, pool_class)) return false;
        return live(pool) && pool->GetPathName() == active.director->pool;
    }

    U::UObject* local_pawn(U::UObject* director, const Network& active)
    {
        auto* pawn = object(director, STR("As Player FP"));
        auto* controller = object(pawn, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!exact(pawn, pawn_class) || !exact(controller, controller_class) || !in_level(pawn, active.world) ||
            static_cast<U::AActor*>(controller)->GetWorld() != active.world || object(controller, STR("Pawn")) != pawn ||
            !live(player) || !local_type || !player->IsA(local_type) || object(player, STR("PlayerController")) != controller)
            return nullptr;
        if (active.seat == Seat::Host)
        {
            auto* canonical = object(active.instance, STR("SaveSlot_Ref"));
            if (byte_field(pawn, STR("Role")) != 3 || byte_field(controller, STR("Role")) != 3 ||
                !exact(canonical, save_class) || object(pawn, STR("Save")) != canonical) return nullptr;
        }
        else if (byte_field(pawn, STR("Role")) != 2)
        {
            return nullptr;
        }
        return pawn;
    }

    bool stock_spawner(U::UObject* actor, U::UWorld* world)
    {
        if (!squad_spawner(actor) || !in_level(actor, world)) return false;
        auto* arrow = object(actor, STR("Arrow_Leader"));
        return exact(arrow, STR("/Script/Engine.ArrowComponent")) && arrow->GetOuterPrivate() == actor;
    }

    bool placed_spawner(U::UObject* actor, U::UWorld* world)
    {
        if (!stock_spawner(actor, world)) return false;
        const auto* profile = director_for(world->GetPathName());
        if (!profile) return false;
        const std::wstring_view prefix{profile->marker_prefix};
        const auto path = actor->GetPathName();
        if (!std::wstring_view{path}.starts_with(prefix)) return false;
        const auto suffix = std::wstring_view{path}.substr(prefix.size());
        return std::ranges::any_of(profile->markers, [suffix](int number) { return suffix == std::to_wstring(number); });
    }

    bool director_marker(U::UObject* actor, U::UObject* director, const Network& active, bool initial)
    {
        const auto& profile = *active.director;
        if (!profile.typed) return placed_spawner(actor, active.world);
        const auto marker_limit = initial ? profile.typed->initial_markers : profile.typed->reinforcement_markers;
        if (marker_limit == 0) return false;
        if (!stock_spawner(actor, active.world) || byte_field(actor, STR("Usage")) != (initial ? 0 : 3)) return false;
        const std::wstring_view prefix{profile.marker_prefix};
        const auto path = actor->GetPathName();
        if (!std::wstring_view{path}.starts_with(prefix)) return false;
        const auto suffix = std::wstring_view{path}.substr(prefix.size());
        if (suffix.empty() || !std::ranges::all_of(suffix, [](auto character) { return character >= L'0' && character <= L'9'; }))
            return false;

        // ConfigureSpawns fills both arrays before the director calls its stock ambient spawner.
        auto* array = field<U::FArrayProperty>(director, initial ? STR("Spawns_Initial") : STR("Spawns_Reinforcments"));
        auto* entry = array ? U::CastField<U::FObjectProperty>(array->GetInner()) : nullptr;
        if (!entry || entry->GetPropertyClass().Get() != actor->GetClassPrivate()) return false;
        U::FScriptArrayHelper rows(array, array->ContainerPtrToValuePtr<void>(director));
        if (rows.Num() <= 0 || static_cast<std::size_t>(rows.Num()) > marker_limit) return false;
        int matches = 0;
        for (int index = 0; index < rows.Num(); ++index)
        {
            if (get_object(entry, rows.GetRawPtr(index)) == actor) ++matches;
        }
        return matches == 1;
    }

    bool directed_squad(U::UObject* data, const Network& active)
    {
        const auto allowed = active.director->directed_squads;
        if (allowed.empty()) return true;
        if (!exact(data, data_class)) return false;
        const auto path = data->GetPathName();
        return std::ranges::any_of(allowed, [&path](const auto asset) { return path == asset; });
    }

    int data_index(U::UObject* data)
    {
        if (!exact(data, data_class)) return -1;
        const auto path = data->GetPathName();
        const auto match = std::ranges::find_if(squad_assets, [&path](const auto candidate) { return path == candidate; });
        return match == squad_assets.end() ? -1 : static_cast<int>(match - squad_assets.begin());
    }

    int boar_squad_size(U::UObject* data)
    {
        if (!operate_boars || !exact(data, data_class)) return 0;
        const auto path = data->GetPathName();
        if (std::ranges::none_of(boar_squads, [&path](const auto candidate) { return path == candidate; })) return 0;

        auto* base = find<U::UClass>(npc_base);
        auto* leader = field<U::FSoftClassProperty>(data, STR("Leader"));
        auto* followers = field<U::FArrayProperty>(data, STR("Followers"));
        auto* follower = followers ? U::CastField<U::FSoftClassProperty>(followers->GetInner()) : nullptr;
        if (!base || !leader || !follower || leader->GetMetaClass().Get() != base || follower->GetMetaClass().Get() != base)
            return 0;

        const auto is_boar = [](const U::FSoftObjectPtr* soft) {
            const auto& path = soft->ToSoftObjectPath();
            const auto asset = path.GetAssetPathString();
            return path.GetSubPathString().IsEmpty() && std::wstring_view{*asset} == boar_class;
        };
        if (!is_boar(leader->ContainerPtrToValuePtr<U::FSoftObjectPtr>(data))) return 0;
        U::FScriptArrayHelper rows(followers, followers->ContainerPtrToValuePtr<void>(data));
        if (rows.Num() < 0 || rows.Num() > 4) return 0;
        for (int i = 0; i < rows.Num(); ++i)
        {
            if (!is_boar(follower->ContainerPtrToValuePtr<U::FSoftObjectPtr>(rows.GetRawPtr(i)))) return 0;
        }
        return 1 + rows.Num();
    }

    // The qualified stock pools contain up to one leader and five followers.
    constexpr std::size_t typed_squad_cap = 6;

    struct TypedSquad
    {
        std::array<std::wstring, typed_squad_cap> classes;
        std::array<bool, typed_squad_cap> matched{};
        std::size_t size{};
        bool all_classes_loaded{true};

        bool match(std::wstring_view path, bool leader)
        {
            if (leader)
            {
                if (matched[0] || path != classes[0]) return false;
                matched[0] = true;
                return true;
            }
            for (std::size_t index = 1; index < size; ++index)
            {
                if (!matched[index] && path == classes[index])
                {
                    matched[index] = true;
                    return true;
                }
            }
            return false;
        }
    };

    std::optional<bool> typed_pool_contains(U::UObject* pool, U::UObject* data, const U::TCHAR* name)
    {
        auto* map = field<U::FMapProperty>(pool, name);
        auto* key = map ? U::CastField<U::FObjectProperty>(map->GetKeyProp()) : nullptr;
        auto* value = map ? U::CastField<U::FDoubleProperty>(map->GetValueProp()) : nullptr;
        auto* value_float = map && !value ? U::CastField<U::FFloatProperty>(map->GetValueProp()) : nullptr;
        if (!key || key->GetPropertyClass().Get() != find<U::UClass>(data_class) || (!value && !value_float))
            return std::nullopt;
        const auto* rows = map->ContainerPtrToValuePtr<U::FScriptMap>(pool);
        const auto& layout = map->GetMapLayout();
        constexpr int max_pool_slots = 32;
        if (rows->Num() < 0 || rows->Num() > max_pool_slots || rows->GetMaxIndex() > max_pool_slots)
            return std::nullopt;
        for (int index = 0; index < rows->GetMaxIndex(); ++index)
        {
            if (!rows->IsValidIndex(index)) continue;
            const auto* entry = static_cast<const std::uint8_t*>(rows->GetData(index, layout));
            if (key->GetObjectPropertyValue(entry) != data) continue;
            const auto* weight_property = entry + layout.ValueOffset;
            const double weight = value ? value->GetPropertyValue(weight_property) : value_float->GetPropertyValue(weight_property);
            return std::isfinite(weight) && weight > 0.0;
        }
        return false;
    }

    bool typed_pool_asset(const DirectorProfile& profile, U::UObject* director, U::UObject* data)
    {
        auto* pool = object(director, STR("AmbientSquadPool"));
        if (!exact(pool, pool_class) || pool->GetPathName() != profile.pool || !exact(data, data_class))
            return false;
        const auto day = typed_pool_contains(pool, data, STR("DayTimeSpawns"));
        const auto night = typed_pool_contains(pool, data, STR("NightTimeSpawns"));
        return day && night && (*day || *night);
    }

    std::string_view typed_squad_shape(const DirectorProfile& profile, U::UObject* director,
                                       U::UObject* data, TypedSquad& squad)
    {
        if (!typed_pool_asset(profile, director, data)) return "stock_ambient_pool_asset_unqualified";
        auto* base = find<U::UClass>(npc_base);
        auto* leader = field<U::FSoftClassProperty>(data, STR("Leader"));
        auto* followers = field<U::FArrayProperty>(data, STR("Followers"));
        auto* follower = followers ? U::CastField<U::FSoftClassProperty>(followers->GetInner()) : nullptr;
        if (!base || !leader || !follower || leader->GetMetaClass().Get() != base || follower->GetMetaClass().Get() != base)
            return "stock_ambient_squad_class_schema_unqualified";
        U::FScriptArrayHelper rows(followers, followers->ContainerPtrToValuePtr<void>(data));
        if (rows.Num() < 0 || static_cast<std::size_t>(rows.Num()) >= typed_squad_cap)
            return "stock_ambient_squad_size_unqualified";

        const auto add = [&](const U::FSoftObjectPtr& soft) {
            const auto& reference = soft.ToSoftObjectPath();
            const auto asset_path = reference.GetAssetPathString();
            const std::wstring_view path{*asset_path};
            constexpr std::wstring_view root{STR("/Game/Classes/NPCs/")};
            if (!reference.GetSubPathString().IsEmpty() || !path.starts_with(root) || path.size() > 256)
                return false;
            const auto separator = path.find_last_of(L'/');
            const auto dot = path.find(L'.', separator + 1);
            if (dot == std::wstring_view::npos || dot == separator + 1) return false;
            const auto asset = path.substr(separator + 1, dot - separator - 1);
            const auto generated_class = path.substr(dot + 1);
            if (generated_class.size() != asset.size() + 2 || !generated_class.starts_with(asset) ||
                !generated_class.ends_with(STR("_C")) ||
                !std::ranges::all_of(path.substr(root.size(), dot - root.size()), [](auto character) {
                    return (character >= L'a' && character <= L'z') || (character >= L'A' && character <= L'Z') ||
                        (character >= L'0' && character <= L'9') || character == L'_' || character == L'/';
                })) return false;
            if (auto* resolved = soft.Get())
            {
                auto* klass = U::Cast<U::UClass>(resolved);
                if (!live(klass) || klass == base || !klass->IsChildOf(base) || klass->GetPathName() != path) return false;
            }
            else squad.all_classes_loaded = false;
            squad.classes[squad.size++].assign(path);
            return true;
        };
        if (!add(*leader->ContainerPtrToValuePtr<U::FSoftObjectPtr>(data))) return "stock_ambient_leader_class_unqualified";
        for (int index = 0; index < rows.Num(); ++index)
            if (!add(*follower->ContainerPtrToValuePtr<U::FSoftObjectPtr>(rows.GetRawPtr(index))))
                return "stock_ambient_follower_class_unqualified";
        return {};
    }

    NpcFamily directed_family(U::UObject* data, const Network& active)
    {
        const auto* typed = active.director->typed;
        if (typed && !typed->keep_legacy_families) return NpcFamily::Ambient;
        if (boar_squad_size(data) > 0) return NpcFamily::Boar;
        if (data_index(data) >= 0 && bool_field(data, STR("ForceWander"), true)) return NpcFamily::Husk;
        return typed ? NpcFamily::Ambient : NpcFamily::Unknown;
    }

    struct SpawnSchema
    {
        U::FObjectProperty* data{};
        U::FIntProperty* count{};
        U::FObjectProperty* member{};
        U::FObjectProperty* finished_member{};
        U::FObjectProperty* finished_leader{};

        std::string_view bind_base(U::UFunction* function)
        {
            if (!function || function != find<U::UFunction>(spawn_path) ||
                function->GetOuterPrivate() != find<U::UClass>(spawner_class) || function->GetNumParms() != 3 ||
                function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
                return "stock_spawn_function_schema_unverified";
            data = field<U::FObjectProperty>(function, STR("Data"));
            count = field<U::FIntProperty>(function, STR("NpcsCount"));
            if (!data || data->GetOffset_Internal() != 0 || !data->HasAnyPropertyFlags(U::CPF_Parm) ||
                data->HasAnyPropertyFlags(U::CPF_OutParm) || data->GetPropertyClass().Get() != find<U::UClass>(data_class))
                return "stock_spawn_data_schema_unverified";
            if (!count || !count->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm) ||
                count->HasAnyPropertyFlags(U::CPF_ReturnParm) || count->GetOffset_Internal() != 0x18 ||
                count->GetOffset_Internal() + count->GetSize() > function->GetParmsSize())
                return "stock_spawn_count_schema_unverified";
            return {};
        }

        std::string_view bind_birth(U::UFunction* function)
        {
            auto* base = find<U::UClass>(npc_base);
            if (!base) return "stock_husk_birth_class_unavailable";
            member = actor_local(function, STR("Member"), 0x20, base);
            finished_member = actor_local(function, STR("CallFunc_FinishSpawningActor_ReturnValue"), 0x148, base);
            finished_leader = actor_local(function, STR("CallFunc_FinishSpawningActor_ReturnValue_1"), 0x170, base);
            if (!member) return "stock_husk_member_local_unverified";
            if (!finished_member) return "stock_husk_follower_local_unverified";
            if (!finished_leader) return "stock_husk_leader_local_unverified";
            return {};
        }

        static U::FObjectProperty* actor_local(U::UFunction* function, const U::TCHAR* name, int offset, U::UClass* base)
        {
            // UE4SS's name-chain lookup ignores the FName number and aliases
            // the two FinishSpawningActor locals. Match the actual field name.
            U::FObjectProperty* match{};
            for (auto* property : U::TFieldRange<U::FProperty>(function, U::EFieldIterationFlags::None))
            {
                if (property->GetOffset_Internal() != offset || property->GetFName().ToString() != name) continue;
                auto* local = U::CastField<U::FObjectProperty>(property);
                if (match || !local || local->GetPropertyClass().Get() != base || local->GetArrayDim() != 1 ||
                    local->GetSize() != sizeof(void*) ||
                    local->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm)) return nullptr;
                match = local;
            }
            return match;
        }

        std::uint8_t* output_address(U::FFrame& frame) const
        {
            std::uint8_t* value{};
            auto* next = frame.OutParms();
            for (int i = 0; next && i < frame.Node()->GetNumParms(); ++i, next = next->NextOutParm)
            {
                if (next->Property != count) continue;
                if (value || !next->PropAddr) return nullptr;
                value = next->PropAddr;
            }
            return next ? nullptr : value;
        }
    };

    using Actors = std::vector<U::UObject*>;
    using Handles = std::vector<U::FWeakObjectPtr>;

    bool scene_npcs(U::UWorld* world, const U::TCHAR* npc, Actors& found)
    {
        auto* type = find<U::UClass>(npc);
        // The first stock call may still need to load the soft NPC class.
        if (!type) return true;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* actor, int, int) {
            if (actor->GetClassPrivate() == type && in_level(actor, world)) found.push_back(actor);
            return found.size() > max_scene_npcs ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        return found.size() <= max_scene_npcs;
    }

    U::FObjectProperty* leader_property(U::UObject* spawner)
    {
        auto* property = field<U::FObjectProperty>(spawner, STR("SquadLeader_ref"));
        auto* base = find<U::UClass>(npc_base);
        return base && property && property->GetPropertyClass().Get() == base ? property : nullptr;
    }

    bool contains(const Handles& handles, U::UObject* object)
    {
        return std::ranges::any_of(handles, [object](const auto& handle) { return handle.Get() == object; });
    }

    struct Replication
    {
        U::UFunction* replication = find<U::UFunction>(STR("/Script/Engine.Actor:SetReplicates"));
        U::UFunction* movement = find<U::UFunction>(STR("/Script/Engine.Actor:SetReplicateMovement"));
        U::UFunction* update = find<U::UFunction>(STR("/Script/Engine.Actor:ForceNetUpdate"));

        static U::FBoolProperty* argument(U::UFunction* function, const U::TCHAR* name)
        {
            if (!function || !function->HasAnyFunctionFlags(U::FUNC_Native) || function->GetNumParms() != 1 ||
                function->GetParmsSize() != 1 || function->GetReturnProperty()) return nullptr;
            auto* param = field<U::FBoolProperty>(function, name);
            return param && param->HasAnyPropertyFlags(U::CPF_Parm) && !param->HasAnyPropertyFlags(U::CPF_OutParm) &&
                param->GetOffset_Internal() == 0 ? param : nullptr;
        }

        bool apply(const Actors& actors) const
        {
            auto* set_replication = argument(replication, STR("bInReplicates"));
            auto* set_movement = argument(movement, STR("bInReplicateMovement"));
            if (!set_replication || !set_movement || !update || !update->HasAnyFunctionFlags(U::FUNC_Native) ||
                update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty()) return false;
            if (!std::ranges::all_of(actors, [](U::UObject* npc) {
                return field<U::FBoolProperty>(npc, STR("bReplicates")) && field<U::FBoolProperty>(npc, STR("bReplicateMovement"));
            })) return false;
            for (auto* npc : actors)
            {
                unsigned char param{};
                if (!bool_field(npc, STR("bReplicates"), true) || byte_field(npc, STR("RemoteRole")) != 1)
                {
                    set_replication->SetPropertyValueInContainer(&param, true);
                    npc->ProcessEvent(replication, &param);
                }
                if (!bool_field(npc, STR("bReplicateMovement"), true))
                {
                    set_movement->SetPropertyValueInContainer(&param, true);
                    npc->ProcessEvent(movement, &param);
                }
                npc->ProcessEvent(update, nullptr);
            }
            return std::ranges::all_of(actors, [](U::UObject* npc) {
                return bool_field(npc, STR("bReplicates"), true) && bool_field(npc, STR("bReplicateMovement"), true) &&
                    byte_field(npc, STR("RemoteRole")) == 1;
            });
        }
    };

    struct Counts
    {
        int authority{}, simulated{}, other{}, simulated_controllers{}, antfarm_initialized{};
        int authority_network_ready{};
    };

    struct Observation
    {
        U::FWeakObjectPtr spawner, data;
        std::vector<U::FWeakObjectPtr> cohort;
        std::string_view stage{"awaiting_stock_cohort"};
        int replicated{};
    };

    struct SavedSquad
    {
        std::int64_t calls{}, denials{};
        std::string_view stage{"awaiting_stock_saved_squad"};
        U::FWeakObjectPtr spawner, data;
    };

    struct ClientBirths
    {
        std::int64_t local_begins{}, local_initializations{}, early_spawn_denials{};
        U::FWeakObjectPtr last_actor, last_caller, last_script;
    };

    struct AntfarmCohort
    {
        std::int64_t calls{}, receipts{}, spawn_denials{}, ai_denials{};
        Observation latest;
        ClientBirths client_births;
        Counts counts;
    };

    struct Flayers : AntfarmCohort
    {
        struct ProjectileHit
        {
            std::int64_t serial{};
            std::string shooter, victim, projectile;
            double before{}, after{}, damage{};
        };
        static constexpr std::size_t hit_capacity = 16;
        std::array<ProjectileHit, hit_capacity> hits{};
        std::int64_t hit_serial{};
        std::string_view hit_status{"awaiting_stock_damage"};
    };

    struct RefreshObservation
    {
        U::FWeakObjectPtr marker, data;
        bool used{}, running{};
        int calls{};
    };

    struct RosterMember
    {
        U::FWeakObjectPtr actor;
        NpcFamily family;
    };

    struct Scope
    {
        U::FWeakObjectPtr world, driver;
        Seat seat{Seat::Inactive};
        std::int64_t natural_calls{}, receipts{}, spawn_denials{}, ai_denials{};
        Observation latest;
        SavedSquad saved_squad;
        ClientBirths client_births;
        Flayers flayers;
        AntfarmCohort not_deer;
        AntfarmCohort boars, ambient;
        std::vector<RosterMember> roster;
        SavedSquad not_deer_saved;
        U::FWeakObjectPtr not_deer_replica;
        RefreshObservation refresh;
        Clock::time_point next_census{};
        Counts counts;
    } scope;

    struct DirectorDecision
    {
        int stock_day{-1}, is_day{-1}, only_track_time{-1};
        std::optional<std::int64_t> game_time_ticks;
    };

    struct DirectorDraw
    {
        U::FWeakObjectPtr marker, asset;
        DirectorDecision decision;
        std::string_view stage;
    };

    struct DirectorPhase
    {
        // Keep the first pool calculation, first draw, and first supported draw per stock phase.
        std::optional<DirectorDecision> first_pool;
        DirectorDecision current_pool;
        U::FFrame* director_frame{};
        DirectorDraw first_draw, first_supported_draw;
    };

    struct DirectorDiagnostic
    {
        U::FWeakObjectPtr world, driver;
        Seat seat{Seat::Inactive};
        std::array<DirectorPhase, 2> phases;
    } director_diagnostic;

    struct Attempt
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr world, director, spawner, data;
        Handles before, births;
        SpawnSchema schema{};
        int max_count{};
        NpcFamily family{NpcFamily::Husk};
        bool initial{};
        std::string_view birth_failure{};
        DirectorDraw* director_draw{};
        std::optional<TypedSquad> typed_squad;
    } attempt;

    struct AntfarmLoad
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr world, antfarm, marker, squad, data;
        Handles before, units;
        const AntfarmProfile* profile{};
    } antfarm_load;

    struct CombatWatch
    {
        U::FWeakObjectPtr world, host, guest, host_save, guest_save;
        Handles cohort;
        Clock::time_point until;
        NpcFamily family{NpcFamily::Husk};
        const U::FFrame* damage_frame{};
        const U::FFrame* melee_frame{};
        U::FWeakObjectPtr pending_attacker, attacker;
        double host_at_arm{}, host_before{}, guest_before{}, host_at_hit{}, guest_at_hit{}, guest_after{};
        double pending_guest_after{};
        bool pending_health_debit{};
        SicknessSnapshot sickness_at_arm{}, pending_sickness{}, sickness_before{}, sickness_after{};
        int attempts{}, guest_hits{}, host_hits{};
        int damage_calls{};
        std::string_view damage_status{"waiting"}, swing_status{"waiting"};
        SicknessSnapshot swing_after{};
    } combat_watch;

    struct PendingFlayerHit
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr victim, shooter, detached, canonical, projectile;
        double before{};
        U::FDoubleProperty* health{};
        U::FDoubleProperty* damage{};
        U::FBoolProperty* lethal{};
    } flayer_hit;

    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID,
        Hook::ERROR_ID};
    bool operate{};
    bool publish_flayers{};
    bool veto_flayer_spawns{};
    bool operate_not_deer{};
    bool watch_combat{};
    bool watch_flayer_projectiles{};
    bool refresh_fixture{};
    bool watch_director{};

    void update_scope(const Network& active)
    {
        if (scope.world.Get() == active.world && scope.driver.Get() == active.driver && scope.seat == active.seat) return;
        scope = {};
        attempt = {};
        antfarm_load = {};
        combat_watch = {};
        flayer_hit = {};
        scope.world = active.world;
        scope.driver = active.driver;
        scope.seat = active.seat;
    }

    DirectorPhase* director_phase(const Network& active, U::FFrame& caller)
    {
        if (!watch_director || active.seat == Seat::Inactive || !active.director->typed) return nullptr;
        const bool initial = caller.Node() == find<U::UFunction>(initial_path);
        if (!initial && caller.Node() != find<U::UFunction>(refresh_path)) return nullptr;
        if (!exact_director(caller.Object(), active)) return nullptr;
        if (director_diagnostic.world.Get() != active.world || director_diagnostic.driver.Get() != active.driver ||
            director_diagnostic.seat != active.seat)
        {
            director_diagnostic = {};
            director_diagnostic.world = active.world;
            director_diagnostic.driver = active.driver;
            director_diagnostic.seat = active.seat;
        }
        return &director_diagnostic.phases[initial ? 0 : 1];
    }

    void observe_director_pool(U::UObject* receiver, U::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        if (!caller || !exact(caller->Object(), director_class)) return;
        const auto active = network(static_cast<U::AActor*>(caller->Object())->GetWorld(), true);
        auto* phase = director_phase(active, *caller);
        if (!phase || object(caller->Object(), STR("AmbientSquadPool")) != receiver ||
            frame.Node() != find<U::UFunction>(combined_pool_path)) return;

        DirectorDecision decision;
        auto* argument = field<U::FBoolProperty>(frame.Node(), STR("IsDayTime"));
        if (argument && frame.Locals() && argument->HasAnyPropertyFlags(U::CPF_Parm) &&
            !argument->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm))
            decision.stock_day = argument->GetPropertyValueInContainer(frame.Locals());
        auto* clock = object(caller->Object(), STR("DayControler_Ref"));
        if (exact(clock, director_clock_class) && clock->GetPathName() == active.director->typed->clock && in_level(clock, active.world))
        {
            if (auto* is_day = field<U::FBoolProperty>(clock, STR("IsDay")))
                decision.is_day = is_day->GetPropertyValueInContainer(clock);
            if (auto* track = field<U::FBoolProperty>(clock, STR("OnlyTrackTime")))
                decision.only_track_time = track->GetPropertyValueInContainer(clock);
            if (object(clock, STR("Game Instance")) == active.instance)
            {
                auto* save = object(active.instance, STR("SaveSlot_Ref"));
                auto* time = exact(save, save_class) ? field<U::FStructProperty>(save, STR("GameTime")) : nullptr;
                auto* timespan = find<U::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
                if (time && timespan && time->GetStruct().Get() == timespan && time->GetSize() == sizeof(std::int64_t))
                {
                    std::int64_t ticks{};
                    std::memcpy(&ticks, time->ContainerPtrToValuePtr<void>(save), sizeof(ticks));
                    decision.game_time_ticks = ticks;
                }
            }
        }
        if (!phase->first_pool) phase->first_pool = decision;
        phase->director_frame = caller;
        phase->current_pool = decision;
    }

    DirectorDraw* observe_director_draw(const Network& active, U::FFrame& frame, U::UObject* marker, U::UObject* data)
    {
        auto* caller = frame.PreviousFrame();
        auto* phase = watch_director && caller ? director_phase(active, *caller) : nullptr;
        if (!phase) return nullptr;
        DirectorDraw* draw{};
        if (phase->first_draw.stage.empty()) draw = &phase->first_draw;
        else if (!directed_squad(phase->first_draw.asset.Get(), active) && phase->first_supported_draw.stage.empty() &&
                 directed_squad(data, active)) draw = &phase->first_supported_draw;
        if (draw)
            *draw = {marker, data, phase->director_frame == caller ? phase->current_pool : DirectorDecision{}, "stock_draw_observed"};
        return draw;
    }

    void director_stage(DirectorDraw* draw, std::string_view stage)
    {
        if (draw) draw->stage = stage;
    }

    AntfarmCohort* antfarm_cohort(NpcFamily family)
    {
        if (family == NpcFamily::Flayer) return &scope.flayers;
        if (family == NpcFamily::NotDeer && operate_not_deer) return &scope.not_deer;
        if (family == NpcFamily::Boar && operate_boars) return &scope.boars;
        if (family == NpcFamily::Ambient) return &scope.ambient;
        return nullptr;
    }

    ClientBirths& births_for(NpcFamily family)
    {
        auto* cohort = antfarm_cohort(family);
        return cohort ? cohort->client_births : scope.client_births;
    }

    bool publishes(NpcFamily family)
    {
        if (family == NpcFamily::Flayer) return publish_flayers;
        if (family == NpcFamily::NotDeer) return operate_not_deer;
        if (family == NpcFamily::Boar) return operate_boars;
        return family == NpcFamily::Husk && (operate || production_roster);
    }

    bool roster_family(const Network& active, NpcFamily family)
    {
        if (active.director == &crossroads) return true;
        if (active.director->typed && !active.director->typed->keep_legacy_families) return false;
        return family == NpcFamily::Husk || family == NpcFamily::Boar;
    }

    bool hook_ready()
    {
        return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire);
    }

    void observe_birth(U::UObject* actor, U::FFrame& parent)
    {
        auto* spawner = attempt.spawner.Get();
        auto* world = U::Cast<U::UWorld>(attempt.world.Get());
        if (!attempt.birth_failure.empty()) return;
        auto* actor_class = actor ? actor->GetClassPrivate() : nullptr;
        auto* base = attempt.typed_squad ? find<U::UClass>(npc_base) : nullptr;
        const bool expected_class = attempt.typed_squad
            ? actor_class && base && actor_class != base && actor_class->IsChildOf(base)
            : npc_family(actor) == attempt.family;
        if (!world || parent.Object() != spawner || !expected_class ||
            !in_level(actor, world) || contains(attempt.before, actor) ||
            (attempt.family == NpcFamily::Husk && !bool_field(actor, STR("Wander"), true)))
        {
            attempt.birth_failure = "stock_husk_birth_context_unverified";
            return;
        }
        const auto& schema = attempt.schema;
        auto* locals = parent.Locals();
        const bool leader = get_object(leader_property(spawner), spawner) == actor &&
            get_object(schema.finished_leader, locals) == actor;
        const bool follower = get_object(schema.member, locals) == actor && get_object(schema.finished_member, locals) == actor;
        if (leader == follower)
        {
            attempt.birth_failure = "stock_husk_birth_locals_unmatched";
            return;
        }
        if (contains(attempt.births, actor) || attempt.births.size() >= static_cast<std::size_t>(attempt.max_count))
        {
            attempt.birth_failure = "stock_husk_birth_count_unverified";
            return;
        }
        if (attempt.typed_squad && !attempt.typed_squad->match(actor_class->GetPathName(), leader))
        {
            attempt.birth_failure = "stock_ambient_birth_class_unqualified";
            return;
        }
        attempt.births.emplace_back(actor);
    }

    void inspect_saved_squad(Iteration& call, U::UObject* receiver, U::FFrame& frame)
    {
        auto* function = frame.Node();
        if (!squad_spawner(receiver) || function != find<U::UFunction>(load_squad_path)) return;
        auto* caller = frame.PreviousFrame();
        if (!caller || !caller->Node()) return;
        auto* antfarm = caller->Object();
        if (!exact(antfarm, antfarm_class) || !std::ranges::any_of(saved_squad_sources, [caller](const auto path) {
            return caller->Node() == find<U::UFunction>(path);
        })) return;

        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
        if (active.director != &crossroads || !placed_spawner(receiver, active.world) || !in_level(antfarm, active.world) ||
            object(antfarm, STR("GameInstance")) != active.instance ||
            (active.seat == Seat::Host && byte_field(receiver, STR("Role")) != 3)) return;
        update_scope(active);
        auto& receipt = scope.saved_squad;
        ++receipt.calls;
        receipt.spawner = receiver;
        receipt.data.Reset();
        receipt.stage = "stock_saved_squad_schema_unverified";
        // PatrolPoints is a read-only reference input in the cooked function. It has no output to initialize.
        auto* squad_type = find<U::UClass>(antfarm_squad_class);
        auto* unit_type = find<U::UClass>(antfarm_unit_class);
        auto* squad_property = field<U::FObjectProperty>(function, STR("Squad"));
        auto* patrol = field<U::FArrayProperty>(function, STR("PatrolPoints"));
        auto* data_property = field<U::FObjectProperty>(squad_type, STR("Data"));
        auto* units_property = field<U::FArrayProperty>(squad_type, STR("Units"));
        auto* unit_element = units_property ? U::CastField<U::FObjectProperty>(units_property->GetInner()) : nullptr;
        auto* unit_class = field<U::FSoftClassProperty>(unit_type, STR("NpcClass"));
        auto* base = find<U::UClass>(npc_base);
        if (function->GetOuterPrivate() != receiver->GetClassPrivate() || function->GetNumParms() != 2 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !frame.Locals() || !squad_type || !unit_type || !squad_property ||
            squad_property->GetPropertyClass().Get() != squad_type || squad_property->GetOffset_Internal() != 0 ||
            !squad_property->HasAnyPropertyFlags(U::CPF_Parm) || squad_property->HasAnyPropertyFlags(U::CPF_OutParm) ||
            !patrol || patrol->GetOffset_Internal() != static_cast<int>(sizeof(void*)) ||
            !patrol->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReferenceParm) ||
            !patrol->HasAnyPropertyFlags(U::CPF_BlueprintReadOnly) ||
            patrol->GetOffset_Internal() + patrol->GetSize() > function->GetParmsSize() ||
            !data_property || !unit_element || unit_element->GetPropertyClass().Get() != unit_type ||
            !unit_class || !base || unit_class->GetMetaClass().Get() != base) return;

        auto* squad = get_object(squad_property, frame.Locals());
        receipt.stage = "stock_saved_squad_ownership_unverified";
        if (!exact(squad, antfarm_squad_class) || object(squad, STR("Controller")) != antfarm) return;
        auto* data = get_object(data_property, squad);
        receipt.data = data;
        receipt.stage = "stock_saved_squad_other_data";
        const AntfarmProfile* profile{};
        if (exact(data, data_class))
        {
            const auto path = data->GetPathName();
            if (path == flayer_profile.data) profile = &flayer_profile;
            else if (operate_not_deer && path == not_deer_profile.data) profile = &not_deer_profile;
        }
        if (!bool_field(data, STR("ForceWander"), true) || (profile
            ? caller->Node() == find<U::UFunction>(saved_squad_sources.back()) : data_index(data) < 0)) return;
        const bool not_deer = profile == &not_deer_profile;
        if (not_deer && (!bool_field(data, STR("FollowLeader"), false) || !bool_field(data, STR("ReportAgro"), false)))
            return;
        auto* cohort = profile ? antfarm_cohort(profile->family) : nullptr;
        if (cohort) ++cohort->calls;
        auto& qualified = not_deer ? scope.not_deer_saved : receipt;
        if (not_deer)
        {
            ++qualified.calls;
            qualified.spawner = receiver;
            qualified.data = data;
        }

        qualified.stage = "stock_saved_squad_members_unverified";
        U::FScriptArrayHelper units(units_property, units_property->ContainerPtrToValuePtr<void>(squad));
        const auto unit_count = units.Num();
        const auto maximum = profile ? static_cast<std::size_t>(profile->maximum) : max_scene_npcs;
        if (unit_count <= 0 || static_cast<std::size_t>(unit_count) > maximum) return;
        Handles qualified_units;
        for (int i = 0; i < unit_count; ++i)
        {
            auto* unit = get_object(unit_element, units.GetRawPtr(i));
            if (!exact(unit, antfarm_unit_class) || object(unit, STR("Controller")) != antfarm) return;
            const auto* soft = unit_class->ContainerPtrToValuePtr<U::FSoftObjectPtr>(unit);
            const auto& path = soft->ToSoftObjectPath();
            const auto asset = path.GetAssetPathString();
            if (!path.GetSubPathString().IsEmpty() || std::wstring_view{*asset} != (profile ? profile->npc : npc_class)) return;
            if (profile)
            {
                auto* npc = field<U::FObjectProperty>(unit, STR("Npc"));
                if (!npc || npc->GetPropertyClass().Get() != base || live(get_object(npc, unit)) || contains(qualified_units, unit)) return;
                qualified_units.emplace_back(unit);
            }
        }

        qualified.stage = "stock_saved_squad_observed";
        if (profile && active.seat == Seat::Host && !antfarm_load.frame &&
            exact(object(active.instance, STR("SaveSlot_Ref")), save_class))
        {
            Actors before;
            if (!scene_npcs(active.world, profile->npc, before)) return;
            Handles original;
            for (auto* actor : before) original.emplace_back(actor);
            antfarm_load = {&frame, active.world, antfarm, receiver, squad, data,
                std::move(original), std::move(qualified_units), profile};
        }
        if (active.seat != Seat::Client || !(not_deer ? operate_not_deer : profile ? veto_flayer_spawns : operate)) return;
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented())
        {
            ++qualified.denials;
            qualified.stage = "stock_saved_squad_denied";
            if (cohort)
            {
                ++cohort->spawn_denials;
                if (network(active.world).seat != Seat::Client) ++cohort->client_births.early_spawn_denials;
                cohort->latest = {receiver, data, {}, profile->denied, 0};
            }
        }
    }

    bool stock_npc_authority(U::UObject* actor, const U::TCHAR* type, U::UWorld* world, int faction = 6)
    {
        return exact(actor, type) && in_level(actor, world) && byte_field(actor, STR("Faction")) == faction &&
            byte_field(actor, STR("Role")) == 3;
    }

    bool stock_npc_controller(U::UObject* actor, U::UWorld* world)
    {
        auto* controller = object(actor, STR("Controller"));
        return exact(controller, npc_controller) && static_cast<U::AActor*>(controller)->GetWorld() == world &&
            object(controller, STR("Pawn")) == actor && byte_field(controller, STR("Role")) == 3;
    }

    U::UObject* one_remote_pawn(const Network& active)
    {
        auto* clients = field<U::FArrayProperty>(active.driver, STR("ClientConnections"));
        auto* element = clients ? U::CastField<U::FObjectProperty>(clients->GetInner()) : nullptr;
        if (!element) return nullptr;
        U::FScriptArrayHelper rows(clients, clients->ContainerPtrToValuePtr<void>(active.driver));
        if (rows.Num() != 1) return nullptr;
        auto* connection = get_object(element, rows.GetRawPtr());
        auto* control = object(connection, STR("PlayerController"));
        auto* pawn = object(control, STR("Pawn"));
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        if (!live(connection) || !type || !connection->IsA(type) || object(connection, STR("Driver")) != active.driver ||
            !exact(control, controller_class) || object(connection, STR("OwningActor")) != control ||
            object(control, STR("Player")) != connection || !exact(pawn, pawn_class) ||
            object(pawn, STR("Controller")) != control || object(pawn, STR("As Game Instance FP")) != active.instance ||
            !in_level(pawn, active.world) || !in_level(control, active.world) || byte_field(pawn, STR("Role")) != 3 ||
            byte_field(control, STR("Role")) != 3) return nullptr;
        return pawn;
    }

    bool read_health(U::UObject* save, double& result)
    {
        auto* property = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
        if (!property) return false;
        result = property->GetPropertyValueInContainer(save);
        return std::isfinite(result);
    }

    bool combat_current()
    {
        if (Clock::now() >= combat_watch.until) return false;
        const auto active = network(U::Cast<U::UWorld>(combat_watch.world.Get()));
        auto* director = find<U::UObject>(director_path);
        auto* host = combat_watch.host.Get();
        auto* guest = combat_watch.guest.Get();
        auto* canonical = combat_watch.host_save.Get();
        auto* detached = combat_watch.guest_save.Get();
        return host && guest && canonical && detached && canonical != detached && active.seat == Seat::Host &&
            exact_director(director, active) && local_pawn(director, active) == host && one_remote_pawn(active) == guest &&
            object(active.instance, STR("SaveSlot_Ref")) == canonical && object(guest, STR("Save")) == detached;
    }

    bool stock_npc_for_combat(U::UObject* actor, U::UWorld* world, NpcFamily family)
    {
        if (family == NpcFamily::Unknown) return false;
        auto* infection = field<U::FIntProperty>(actor, STR("Melee_Infection"));
        const bool flayer = family == NpcFamily::Flayer;
        const auto* profile = family == NpcFamily::NotDeer ? &not_deer_profile : flayer ? &flayer_profile : nullptr;
        return stock_npc_authority(actor, profile ? profile->npc : npc_class, world, profile ? profile->faction : 6) &&
            stock_npc_controller(actor, world) &&
            bool_field(actor, STR("Alive"), true) && infection &&
            infection->GetPropertyValueInContainer(actor) == (flayer ? flayer_infection : 0);
    }

    bool published_flayer(const Network& active, U::UObject* actor)
    {
        const auto& cohort = scope.flayers.latest;
        return publish_flayers && veto_flayer_spawns && active.seat == Seat::Host && scope.seat == Seat::Host &&
            scope.world.Get() == active.world && scope.driver.Get() == active.driver && cohort.stage == "stock_antfarm" &&
            cohort.replicated > 0 && cohort.cohort.size() == static_cast<std::size_t>(cohort.replicated) &&
            contains(cohort.cohort, actor) && stock_npc_for_combat(actor, active.world, NpcFamily::Flayer) &&
            bool_field(actor, STR("bReplicates"), true) && byte_field(actor, STR("RemoteRole")) == 1;
    }

    template <class T>
    T* hit_field(U::UFunction* function, const U::TCHAR* name, int offset, bool out = false)
    {
        auto* value = field<T>(function, name);
        return value && value->HasAnyPropertyFlags(U::CPF_Parm) && value->HasAnyPropertyFlags(U::CPF_OutParm) == out &&
            !value->HasAnyPropertyFlags(U::CPF_ReturnParm) && value->GetOffset_Internal() == offset &&
            offset + value->GetSize() <= function->GetParmsSize() ? value : nullptr;
    }

    void* hit_output(U::FFrame& frame, U::FProperty* field)
    {
        auto* parameter = frame.OutParms();
        void* value{};
        for (int remaining = frame.Node()->GetNumParms(); parameter && remaining > 0; --remaining, parameter = parameter->NextOutParm)
        {
            if (parameter->Property != field) continue;
            if (value) return nullptr;
            value = parameter->PropAddr;
        }
        return parameter ? nullptr : value;
    }

    void before_flayer_projectile(U::UObject* victim, U::FFrame& frame)
    {
        if (flayer_hit.frame || !publish_flayers || !veto_flayer_spawns || npc_family(victim) != NpcFamily::Flayer) return;
        const auto active = network(static_cast<U::AActor*>(victim)->GetWorld());
        if (!published_flayer(active, victim)) return;
        auto& status = scope.flayers.hit_status;
        auto* caller = frame.PreviousFrame();
        auto* projectile = caller ? caller->Object() : nullptr;
        if (!exact(projectile, light_projectile))
        {
            status = "outside_stock_light_projectile";
            return;
        }
        auto* base = find<U::UClass>(projectile_base);
        auto* calc = find<U::UFunction>(projectile_calc);
        auto* function = frame.Node();
        status = "stock_damage_frame_unqualified";
        if (!frame.Locals() || function != find<U::UFunction>(npc_damage_path) || function->GetNumParms() != 7 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !base || !projectile->IsA(base) || !calc || calc->GetOuterPrivate() != base || calc->GetNumParms() != 1 ||
            calc->GetReturnProperty() || calc->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            caller->Node() != calc || !in_level(projectile, active.world) || byte_field(projectile, STR("Role")) != 3) return;
        status = "stock_damage_abi_unqualified";
        auto* instigator = hit_field<U::FObjectProperty>(function, STR("Instigator"), 0x150);
        auto* hit = hit_field<U::FStructProperty>(function, STR("Hit"), 0x58);
        auto* hit_type = find<U::UScriptStruct>(STR("/Script/Engine.HitResult"));
        auto* component_field = field<U::FWeakObjectProperty>(hit_type, STR("Component"));
        auto* damage = hit_field<U::FDoubleProperty>(function, STR("TotalDamage"), 0x178, true);
        auto* lethal = hit_field<U::FBoolProperty>(function, STR("IsLethal"), 0x180, true);
        if (!instigator || !hit || !hit_type || hit->GetStruct().Get() != hit_type || !component_field ||
            component_field->GetOffset_Internal() != 0xd8 || !damage || !lethal) return;
        auto* component = component_field->GetPropertyValueInContainer(hit->ContainerPtrToValuePtr<void>(frame.Locals())).Get();
        auto* shooter = get_object(instigator, frame.Locals());
        auto* detached = object(shooter, STR("Save"));
        auto* canonical = object(active.instance, STR("SaveSlot_Ref"));
        auto* health = field<U::FDoubleProperty>(victim, STR("Health"));
        auto* maximum = field<U::FDoubleProperty>(victim, STR("Max_Health"));
        status = "stock_damage_component_unqualified";
        if (!live(component) || component->GetOuterPrivate() != victim) return;
        status = "stock_damage_owner_unqualified";
        if (!exact(shooter, pawn_class) ||
            object(projectile, STR("Owner")) != shooter || object(projectile, STR("Instigator")) != shooter ||
            !silverfish::native_guest_regen::qualifies_detached_guest(shooter, detached, canonical)) return;
        status = "stock_damage_health_unqualified";
        if (!health || !maximum || maximum->GetPropertyValueInContainer(victim) != 180.0) return;
        const auto before = health->GetPropertyValueInContainer(victim);
        if (!std::isfinite(before) || before <= 0 || before > 180.0) return;
        status = "awaiting_stock_damage_output";
        flayer_hit = {&frame, victim, shooter, detached, canonical, projectile, before, health, damage, lethal};
    }

    void after_flayer_projectile(Iteration& call, U::UObject* victim, U::FFrame& frame)
    {
        const auto hit = flayer_hit;
        flayer_hit = {};
        auto* shooter = hit.shooter.Get();
        auto* projectile = hit.projectile.Get();
        const auto active = network(U::Cast<U::UWorld>(scope.world.Get()));
        auto& status = scope.flayers.hit_status;
        status = "stock_damage_post_identity_unqualified";
        if (call.OriginalFunctionCallPrevented() || !shooter || !projectile || victim != hit.victim.Get() ||
            !published_flayer(active, victim) || object(victim, STR("LastDamagedMe")) != shooter ||
            object(projectile, STR("Owner")) != shooter || object(projectile, STR("Instigator")) != shooter ||
            !silverfish::native_guest_regen::qualifies_detached_guest(shooter, hit.detached.Get(), hit.canonical.Get())) return;
        const auto* damage = hit_output(frame, hit.damage);
        const auto* lethal = hit_output(frame, hit.lethal);
        const auto after = hit.health->GetPropertyValueInContainer(victim);
        const auto amount = damage ? hit.damage->GetPropertyValue(damage) : -1.0;
        status = "stock_damage_hp_delta_unqualified";
        if (!lethal || hit.lethal->GetPropertyValue(lethal) || !std::isfinite(after) || !std::isfinite(amount) ||
            after <= 0 || amount <= 0 || std::abs((hit.before - after) - amount) > 1e-6) return;
        if (scope.flayers.hit_serial >= (std::int64_t{1} << 53) - 1) return;
        const auto serial = ++scope.flayers.hit_serial;
        auto& result = scope.flayers.hits[static_cast<std::size_t>(serial - 1) % Flayers::hit_capacity];
        result = {serial, RC::to_utf8_string(shooter->GetFullName()), RC::to_utf8_string(victim->GetFullName()),
            RC::to_utf8_string(projectile->GetFullName()), hit.before, after, amount};
        status = "stock_damage_verified";
    }

    bool current_sickness(SicknessSnapshot& result)
    {
        return silverfish::native_guest_sickness::read_exact(combat_watch.guest.Get(), combat_watch.guest_save.Get(),
            combat_watch.host_save.Get(), result);
    }

    bool same_stock_sickness(const SicknessSnapshot& first, const SicknessSnapshot& second)
    {
        return first.host_found == second.host_found && first.host_sickness == second.host_sickness;
    }

    void before_stock_melee(U::UObject* receiver, U::FFrame& frame)
    {
        if (!watch_combat || combat_watch.damage_frame || combat_watch.melee_frame ||
            (receiver != combat_watch.guest.Get() && receiver != combat_watch.host.Get()) || !combat_current()) return;
        ++combat_watch.damage_calls;
        auto* parent = frame.PreviousFrame();
        auto* attacker = parent ? parent->Object() : nullptr;
        if (!attacker || !contains(combat_watch.cohort, attacker))
        {
            combat_watch.damage_status = "outside_stock_cohort";
            return;
        }
        if (parent->Node() != find<U::UFunction>(melee_path))
        {
            combat_watch.damage_status = "outside_resolve_melee";
            return;
        }
        auto* function = frame.Node();
        auto* instigator = field<U::FObjectProperty>(function, STR("Instigator"));
        if (function != find<U::UFunction>(player_damage_path) || function->GetNumParms() != 7 || function->GetReturnProperty() ||
            !instigator || !instigator->HasAnyPropertyFlags(U::CPF_Parm) || instigator->HasAnyPropertyFlags(U::CPF_OutParm) ||
            instigator->GetOffset_Internal() != 0x150 || get_object(instigator, frame.Locals()) != attacker)
        {
            combat_watch.damage_status = "stock_damage_parameters_unverified";
            return;
        }
        if (!stock_npc_for_combat(attacker, U::Cast<U::UWorld>(combat_watch.world.Get()), combat_watch.family) ||
            object(attacker, STR("CombatTarget")) != receiver ||
            !read_health(combat_watch.host_save.Get(), combat_watch.host_before) ||
            !read_health(combat_watch.guest_save.Get(), combat_watch.guest_before))
        {
            combat_watch.damage_status = "stock_combat_target_unverified";
            return;
        }
        if (combat_watch.family == NpcFamily::Flayer && receiver == combat_watch.guest.Get())
        {
            if (!current_sickness(combat_watch.pending_sickness) ||
                !same_stock_sickness(combat_watch.pending_sickness, combat_watch.sickness_at_arm))
            {
                combat_watch.damage_status = "stock_sickness_scope_unverified";
                return;
            }
            combat_watch.melee_frame = parent;
            combat_watch.pending_health_debit = false;
        }
        ++combat_watch.attempts;
        combat_watch.damage_status = "stock_damage_qualified";
        combat_watch.damage_frame = &frame;
        combat_watch.pending_attacker = attacker;
    }

    void after_stock_melee(Iteration& call, U::UObject* receiver)
    {
        combat_watch.damage_frame = nullptr;
        auto* attacker = combat_watch.pending_attacker.Get();
        if (!combat_watch.melee_frame) combat_watch.pending_attacker.Reset();
        double current_host{}, current_guest{};
        if (call.OriginalFunctionCallPrevented() || !attacker || !combat_current() ||
            !read_health(combat_watch.host_save.Get(), current_host) || !read_health(combat_watch.guest_save.Get(), current_guest)) return;
        if (current_host < combat_watch.host_before) ++combat_watch.host_hits;
        if (receiver != combat_watch.guest.Get() || current_guest >= combat_watch.guest_before ||
            current_host != combat_watch.host_before || current_host != combat_watch.host_at_arm) return;
        if (combat_watch.melee_frame)
        {
            combat_watch.pending_guest_after = current_guest;
            combat_watch.pending_health_debit = true;
            return;
        }
        if (combat_watch.guest_hits++ == 0)
        {
            combat_watch.attacker = attacker;
            combat_watch.host_at_hit = current_host;
            combat_watch.guest_at_hit = combat_watch.guest_before;
            combat_watch.guest_after = current_guest;
        }
    }

    void after_flayer_swing(Iteration& call, U::UObject* actor)
    {
        combat_watch.melee_frame = nullptr;
        const bool measured = combat_watch.pending_health_debit;
        const bool exact_attacker = combat_watch.pending_attacker.Get() == actor;
        combat_watch.pending_health_debit = false;
        combat_watch.pending_attacker.Reset();
        combat_watch.swing_status = "stock_health_debit_unverified";
        if (!measured || !exact_attacker || call.OriginalFunctionCallPrevented() || !combat_current()) return;
        SicknessSnapshot after;
        double host{}, guest{};
        combat_watch.swing_status = "stock_health_after_swing_unverified";
        if (!current_sickness(after) || !read_health(combat_watch.host_save.Get(), host) ||
            !read_health(combat_watch.guest_save.Get(), guest) || host != combat_watch.host_at_arm ||
            guest != combat_watch.pending_guest_after) return;
        const auto& before = combat_watch.pending_sickness;
        combat_watch.swing_after = after;
        const auto delta = static_cast<std::int64_t>(after.guest_sickness) - before.guest_sickness;
        combat_watch.swing_status = "stock_infection_delta_unverified";
        // The stock ResolveMelee path in this build yields two scoped reads and one audited write.
        if (!same_stock_sickness(before, after) || !after.guest_found || delta < 1 || delta > flayer_infection ||
            after.infect_reads - before.infect_reads != flayer_infection_reads || after.infect_writes - before.infect_writes != 1) return;
        combat_watch.swing_status = "stock_infection_verified";
        if (combat_watch.guest_hits++ == 0)
        {
            combat_watch.attacker = actor;
            combat_watch.host_at_hit = host;
            combat_watch.guest_at_hit = combat_watch.guest_before;
            combat_watch.guest_after = guest;
            combat_watch.sickness_before = before;
            combat_watch.sickness_after = after;
        }
    }

    void observe_antfarm_load(Iteration& call, U::UObject* receiver, U::FFrame& frame)
    {
        const auto observed = std::move(antfarm_load);
        antfarm_load = {};
        if (call.OriginalFunctionCallPrevented() || frame.Node() != find<U::UFunction>(load_squad_path)) return;
        const auto active = network(U::Cast<U::UWorld>(observed.world.Get()));
        const auto* profile = observed.profile;
        auto* published = profile ? antfarm_cohort(profile->family) : nullptr;
        auto* stock = observed.antfarm.Get();
        auto* squad = observed.squad.Get();
        auto* data = observed.data.Get();
        if (!published || active.seat != Seat::Host || !placed_spawner(receiver, active.world) || !in_level(stock, active.world) ||
            object(stock, STR("GameInstance")) != active.instance || !exact(object(active.instance, STR("SaveSlot_Ref")), save_class) ||
            !exact(squad, antfarm_squad_class) || object(squad, STR("Controller")) != stock || object(squad, STR("Data")) != data ||
            !exact(data, data_class) || data->GetPathName() != profile->data) return;

        Observation next{receiver, data, {}, profile->unverified, 0};
        Actors cohort;
        const auto proof = [&] {
            for (const auto& handle : observed.units)
            {
                auto* unit = handle.Get();
                if (!exact(unit, antfarm_unit_class) || object(unit, STR("Controller")) != stock) return false;
                auto* actor = object(unit, STR("Npc"));
                if (!live(actor)) continue;
                if (!stock_npc_authority(actor, profile->npc, active.world, profile->faction) ||
                    !bool_field(actor, STR("Wander"), true) ||
                    !stock_npc_controller(actor, active.world) || contains(observed.before, actor) ||
                    std::ranges::find(cohort, actor) != cohort.end()) return false;
                cohort.push_back(actor);
            }
            Actors newborn;
            if (cohort.empty() || !scene_npcs(active.world, profile->npc, newborn)) return false;
            std::erase_if(newborn, [&](auto* actor) { return contains(observed.before, actor); });
            return newborn.size() == cohort.size() && std::ranges::is_permutation(cohort, newborn);
        };
        if (proof())
        {
            ++published->receipts;
            for (auto* actor : cohort) next.cohort.emplace_back(actor);
            next.stage = "stock_antfarm";
            if (publishes(profile->family) && Replication{}.apply(cohort)) next.replicated = static_cast<int>(cohort.size());
        }
        if (!next.cohort.empty() || published->latest.cohort.empty()) published->latest = std::move(next);
    }

    int set_turret_inactive(const Lua& lua)
    {
        if (!hook_ready() || !production_roster) return 0;
        turret_inactive = {};
        const auto count = lua.get_stack_size();
        if (count < 2 || count > 6) return 0;
        for (int i = 1; i <= count; ++i)
            if (!lua.is_integer(i)) return 0;
        turret_inactive.world = static_cast<std::uintptr_t>(lua.get_integer());
        for (int i = 1; i < count; ++i)
            turret_inactive.pawns[static_cast<std::size_t>(i - 1)] = static_cast<std::uintptr_t>(lua.get_integer());
        return 0;
    }

    bool guard_turret_fire(Iteration& call, U::UObject* receiver, U::UFunction* function)
    {
        static const U::FName fire{STR("FireRound")};
        if (!production_roster || !receiver || !function || function->GetFName() != fire ||
            call.OriginalFunctionCallPrevented() || !live(receiver)) return false;
        auto* base = find<U::UClass>(turret_base);
        if (!base || !receiver->GetClassPrivate()->IsChildOf(base) || function != find<U::UFunction>(turret_fire_path))
            return false;
        auto* world = static_cast<U::AActor*>(receiver)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        if (!in_level(receiver, world) || !exact(object(world, STR("OwningGameInstance")), instance_class) ||
            object(driver, STR("World")) != world) return false;
        const auto role = byte_field(receiver, STR("Role"));
        auto* connection = object(driver, STR("ServerConnection"));
        if (role == 1 && bool_field(receiver, STR("bReplicates"), true) && server_connection(connection, driver))
        {
            call.PreventOriginalFunctionCall();
            return call.OriginalFunctionCallPrevented();
        }
        if (role != 3 || connection ||
            turret_inactive.world != reinterpret_cast<std::uintptr_t>(world)) return false;

        auto* property = field<U::FArrayProperty>(receiver, STR("Targets"));
        auto* item = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!item || item->GetOffset_Internal() != 0) return false;
        U::FScriptArrayHelper targets(property, property->ContainerPtrToValuePtr<void>(receiver));
        // Keep the stock order of viable targets, including enemies and allies of the turret.
        // Blueprint FireRound will use the next entry and perform its own range and LOS checks.
        for (int i = targets.Num() - 1; i >= 0; --i)
        {
            auto* target = item->GetObjectPropertyValue(targets.GetRawPtr(i));
            if (target && std::ranges::find(turret_inactive.pawns, reinterpret_cast<std::uintptr_t>(target)) !=
                          turret_inactive.pawns.end()) targets.RemoveValues(i, 1);
        }
        if (targets.Num() != 0) return false;
        call.PreventOriginalFunctionCall();
        return call.OriginalFunctionCallPrevented();
    }

    bool prevent_roster_damage(Iteration& call, U::UObject* receiver, U::UFunction* function,
                               U::FFrame* frame, void* parameters)
    {
        if (!production_roster || !receiver || !function || call.OriginalFunctionCallPrevented()) return false;
        static const U::FName damage_name{STR("DealDamage")}, death_name{STR("ResolveDeath")}, die_name{STR("Die")};
        const auto name = function->GetFName();
        const bool damage = name == damage_name;
        if ((!damage && name != death_name && name != die_name)
            || function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)
            || !live(receiver) || byte_field(receiver, STR("Role")) != 1 ||
            !bool_field(receiver, STR("bReplicates"), true) || (frame && frame->Object() != receiver)) return false;

        const auto* owner = U::Cast<U::UClass>(function->GetOuterPrivate());
        const auto* type = receiver->GetClassPrivate();
        if (!owner || !type || !type->IsChildOf(owner)) return false;

        const U::UFunction* declaration{};
        if (const auto* base = find<U::UClass>(npc_base); base && owner->IsChildOf(base))
            declaration = find<U::UFunction>(damage ? npc_damage_path : name == die_name ? npc_die_path : npc_death_path);
        else if (name != death_name)
        {
            // The flying stock drone and turret families have separate Blueprint roots.
            if (const auto* drone = find<U::UClass>(drone_base); drone && owner->IsChildOf(drone))
                declaration = find<U::UFunction>(damage ? drone_damage_path : drone_die_path);
            else if (const auto* turret = find<U::UClass>(turret_base); turret && owner->IsChildOf(turret))
                declaration = find<U::UFunction>(damage ? turret_damage_path : turret_die_path);
        }
        if (!declaration) return false;
        // Some stock NPCs override the damage or death graph. Follow its
        // reflected parent function so an unrelated Blueprint event stays unaffected.
        const U::UStruct* inherited = function;
        while (inherited && inherited != declaration) inherited = inherited->GetSuperStruct();
        if (!inherited) return false;

        auto* total = damage ? hit_field<U::FDoubleProperty>(function, STR("TotalDamage"), 0x178, true) : nullptr;
        auto* lethal = damage ? hit_field<U::FBoolProperty>(function, STR("IsLethal"), 0x180, true) : nullptr;
        if (damage && (!total || !lethal)) return false;
        auto* world = static_cast<U::AActor*>(receiver)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        // A stock simulated NPC only exists here because the host published it.
        // The director profile limits spawn capture, not damage on existing replicas.
        if (!in_level(receiver, world) || !exact(object(world, STR("OwningGameInstance")), instance_class) ||
            object(driver, STR("World")) != world || !server_connection(object(driver, STR("ServerConnection")), driver))
            return false;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented()) return false;
        if (!damage) return true;
        if (frame)
        {
            if (auto* output = hit_output(*frame, total);
                output && reinterpret_cast<std::uintptr_t>(output) % alignof(double) == 0)
                total->SetPropertyValue(output, 0.0);
            if (auto* output = hit_output(*frame, lethal)) lethal->SetPropertyValue(output, false);
        }
        else if (parameters)
        {
            total->SetPropertyValueInContainer(parameters, 0.0);
            lethal->SetPropertyValueInContainer(parameters, false);
        }
        return true;
    }

    void prevent_replica_brain(Iteration& call, U::UObject* receiver, U::UFunction* function)
    {
        if ((!operate && !publish_flayers && !operate_not_deer && !operate_boars && !production_roster) || !function || !receiver ||
            call.OriginalFunctionCallPrevented()) return;
        static const U::FName init_name{STR("InitializeAI")};
        static const U::FName perceive_name{STR("Proscess_TargetPerception")};
        const auto name = function->GetFName();
        if ((name != init_name && name != perceive_name) ||
            function != find<U::UFunction>(name == init_name ? initialize_path : perceive_path) ||
            function->GetReturnProperty() || byte_field(receiver, STR("Role")) != 1) return;
        const auto family = npc_family(receiver);
        if (!publishes(family) || (family == NpcFamily::NotDeer && byte_field(receiver, STR("Faction")) != 7)) return;
        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld());
        if (active.seat != Seat::Client || !roster_family(active, family) || !in_level(receiver, active.world)) return;
        update_scope(active);
        call.PreventOriginalFunctionCall();
        auto* cohort = antfarm_cohort(family);
        if (call.OriginalFunctionCallPrevented()) ++(cohort ? cohort->ai_denials : scope.ai_denials);
    }

    void prevent_client_spawn(Iteration& call, U::UObject* receiver, U::FFrame& frame, const Network& active)
    {
        const bool uses_typed_squads = active.director->typed != nullptr;
        if (!uses_typed_squads && !placed_spawner(receiver, active.world)) return;
        update_scope(active);
        SpawnSchema schema;
        if (const auto error = schema.bind_base(frame.Node()); !error.empty())
        {
            scope.latest = {receiver, {}, {}, error, 0};
            return;
        }
        auto* data = get_object(schema.data, frame.Locals());
        auto* draw = observe_director_draw(active, frame, receiver, data);
        if (!uses_typed_squads && !directed_squad(data, active)) return;
        const auto family = directed_family(data, active);
        if (family == NpcFamily::Unknown) return;
        auto* caller = frame.PreviousFrame();
        bool initial{};
        if (production_roster || uses_typed_squads)
        {
            if (!caller || !exact_director(caller->Object(), active)) return;
            const auto usage = byte_field(receiver, STR("Usage"));
            initial = usage == 0 && caller->Node() == find<U::UFunction>(initial_path);
            const bool refresh = usage == 3 && caller->Node() == find<U::UFunction>(refresh_path);
            if (!initial && !refresh)
            {
                director_stage(draw, "director_usage_unqualified");
                return;
            }
            if (uses_typed_squads && !director_marker(receiver, caller->Object(), active, initial))
            {
                director_stage(draw, "director_marker_unqualified");
                return;
            }
        }
        auto* cohort = antfarm_cohort(family);
        auto& latest = cohort ? cohort->latest : scope.latest;
        bool declared_unloaded{};
        if (uses_typed_squads)
        {
            TypedSquad squad;
            if (const auto error = typed_squad_shape(*active.director, caller->Object(), data, squad); !error.empty())
            {
                director_stage(draw, error);
                return;
            }
            // This exact cooked director and pool provide a typed NPC declaration. Vetoing its local
            // guest spawn requires no loaded class; publishing still requires a verified stock birth.
            declared_unloaded = !squad.all_classes_loaded;
        }
        const bool ambient = family == NpcFamily::Ambient;
        const bool boar = family == NpcFamily::Boar;
        ++(cohort ? cohort->calls : scope.natural_calls);
        latest = {receiver, data, {}, ambient ? "stock_client_ambient_spawn_observed" :
            boar ? "stock_client_boar_spawn_observed" : "stock_client_husk_spawn_observed", 0};
        if (!boar && !operate && !production_roster)
        {
            director_stage(draw, "suppression_disabled");
            return;
        }
        auto* output = schema.output_address(frame);
        if (!output)
        {
            latest.stage = ambient ? "stock_client_ambient_output_unverified" :
                boar ? "stock_client_boar_output_unverified" : "stock_client_husk_output_unverified";
            director_stage(draw, "stock_count_output_unqualified");
            return;
        }
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented())
        {
            schema.count->SetPropertyValue(output, 0);
            director_stage(draw, declared_unloaded ? "client_spawn_denied_declared_unloaded" : "client_spawn_denied");
            ++(cohort ? cohort->spawn_denials : scope.spawn_denials);
            if (network(active.world).seat != Seat::Client)
                ++births_for(family).early_spawn_denials;
            latest.stage = ambient ? (initial ? "stock_client_ambient_initial_spawn_denied" : "stock_client_ambient_refresh_spawn_denied") :
                boar ? "stock_client_boar_spawn_denied" : "stock_client_husk_spawn_denied";
        }
        else director_stage(draw, "client_spawn_not_denied");
    }

    void on_pre(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready() || !receiver || !frame.Node() || call.OriginalFunctionCallPrevented()) return;
        static const U::FName spawn_name{STR("SpawnSquadFromData")};
        static const U::FName load_name{STR("LoadSquad")};
        static const U::FName init_name{STR("InitializeAI")};
        static const U::FName perceive_name{STR("Proscess_TargetPerception")};
        static const U::FName damage_name{STR("DealDamage")};
        const auto name = frame.Node()->GetFName();
        if (watch_director)
        {
            static const U::FName pool_name{STR("GetCombinedPool")};
            if (name == pool_name) observe_director_pool(receiver, frame);
        }
        if (guard_turret_fire(call, receiver, frame.Node()) ||
            prevent_roster_damage(call, receiver, frame.Node(), &frame, nullptr)) return;
        if (name == damage_name)
        {
            if (watch_combat) before_stock_melee(receiver, frame);
            if (watch_flayer_projectiles) before_flayer_projectile(receiver, frame);
            return;
        }
        if (name != spawn_name && name != load_name && name != init_name && name != perceive_name) return;

        if (name == init_name && attempt.frame && frame.PreviousFrame() == attempt.frame &&
            frame.Node() == find<U::UFunction>(initialize_path))
        {
            // The stock spawn frame identifies the particular newly finished leader or follower.
            observe_birth(receiver, *attempt.frame);
            return;
        }

        if (name == load_name)
        {
            inspect_saved_squad(call, receiver, frame);
            return;
        }
        if (name == init_name && frame.Node() == find<U::UFunction>(initialize_path) && byte_field(receiver, STR("Role")) == 3)
        {
            const auto family = npc_family(receiver);
            if (family != NpcFamily::Unknown && (family != NpcFamily::NotDeer || operate_not_deer))
            {
                const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
                if (active.seat == Seat::Client && roster_family(active, family) && in_level(receiver, active.world))
                {
                    update_scope(active);
                    auto& births = births_for(family);
                    ++births.local_initializations;
                    births.last_actor = receiver;
                    auto* parent = frame.PreviousFrame();
                    births.last_caller = parent ? parent->Object() : nullptr;
                    births.last_script = parent ? parent->Node() : nullptr;
                }
            }
        }
        prevent_replica_brain(call, receiver, frame.Node());

        if (name != spawn_name || !squad_spawner(receiver)) return;
        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
        if (active.seat == Seat::Client)
        {
            prevent_client_spawn(call, receiver, frame, active);
            return;
        }
        if (active.seat != Seat::Host || attempt.frame) return;
        auto* caller = frame.PreviousFrame();
        if (!caller) return;
        const auto initial = caller->Node() == find<U::UFunction>(initial_path);
        const auto refresh = caller->Node() == find<U::UFunction>(refresh_path);
        if (!initial && !refresh) return;
        auto* director = caller->Object();
        if (!exact_director(director, active)) return;
        if (!director_marker(receiver, director, active, initial) ||
            byte_field(receiver, STR("Role")) != 3 || byte_field(receiver, STR("Usage")) != (initial ? 0 : 3))
        {
            if (watch_director && active.director->typed)
            {
                SpawnSchema refused;
                if (refused.bind_base(frame.Node()).empty())
                    director_stage(observe_director_draw(active, frame, receiver, get_object(refused.data, frame.Locals())),
                               "host_marker_or_authority_unqualified");
            }
            return;
        }
        update_scope(active);
        SpawnSchema schema;
        if (const auto error = schema.bind_base(frame.Node()); !error.empty())
        {
            if (scope.latest.cohort.empty()) scope.latest = {receiver, {}, {}, error, 0};
            return;
        }
        ++scope.natural_calls;
        auto* data = get_object(schema.data, frame.Locals());
        auto* draw = observe_director_draw(active, frame, receiver, data);
        std::optional<TypedSquad> typed_squad;
        if (active.director->typed)
        {
            typed_squad.emplace();
            if (const auto error = typed_squad_shape(*active.director, director, data, *typed_squad); !error.empty())
            {
                director_stage(draw, error);
                return;
            }
        }
        else if (!directed_squad(data, active)) return;
        if (refresh && scope.refresh.running)
        {
            ++scope.refresh.calls;
            scope.refresh.marker = receiver;
            if (exact(data, data_class)) scope.refresh.data = data;
        }
        const auto family = directed_family(data, active);
        if (family == NpcFamily::Unknown) return;
        auto* cohort = antfarm_cohort(family);
        auto& latest = cohort ? cohort->latest : scope.latest;
        if (cohort) ++cohort->calls;
        if (const auto error = schema.bind_birth(frame.Node()); !error.empty())
        {
            if (latest.cohort.empty()) latest = {receiver, data, {}, error, 0};
            director_stage(draw, error);
            return;
        }
        if (!local_pawn(director, active))
        {
            director_stage(draw, "host_canonical_pawn_unqualified");
            return;
        }
        const bool boar = family == NpcFamily::Boar;
        Handles baseline;
        if (!typed_squad)
        {
            Actors before;
            if (!scene_npcs(active.world, boar ? boar_class : npc_class, before))
            {
                director_stage(draw, "pre_spawn_census_unavailable");
                return;
            }
            baseline.reserve(before.size());
            for (auto* existing : before) baseline.emplace_back(existing);
        }
        const auto maximum = typed_squad ? static_cast<int>(typed_squad->size) :
            boar ? boar_squad_size(data) : maximum_squad_sizes[static_cast<std::size_t>(data_index(data))];
        attempt = {&frame, active.world, director, receiver, data, std::move(baseline), {}, schema,
            maximum, family, initial, {}, draw, std::move(typed_squad)};
        director_stage(draw, "awaiting_stock_spawn_result");
    }

    void prune_roster(U::UWorld* world)
    {
        std::erase_if(scope.roster, [world](const RosterMember& member) {
            return !in_level(member.actor.Get(), world);
        });
    }

    void on_post(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready()) return;
        if (watch_director)
            for (auto& phase : director_diagnostic.phases)
                if (phase.director_frame == &frame) phase.director_frame = nullptr;
        if (flayer_hit.frame == &frame)
        {
            after_flayer_projectile(call, receiver, frame);
            return;
        }
        if (combat_watch.damage_frame == &frame)
        {
            after_stock_melee(call, receiver);
            return;
        }
        if (combat_watch.melee_frame == &frame)
        {
            after_flayer_swing(call, receiver);
            return;
        }
        if (antfarm_load.frame == &frame && antfarm_load.marker.Get() == receiver)
        {
            observe_antfarm_load(call, receiver, frame);
            return;
        }
        if (attempt.frame != &frame || attempt.spawner.Get() != receiver) return;
        const auto observed = std::move(attempt);
        attempt = {};
        auto* draw = director_diagnostic.world.Get() == observed.world.Get() ? observed.director_draw : nullptr;
        if (call.OriginalFunctionCallPrevented())
        {
            director_stage(draw, "stock_spawn_prevented");
            return;
        }
        const auto active = network(U::Cast<U::UWorld>(observed.world.Get()));
        auto* director = observed.director.Get();
        auto* data = observed.data.Get();
        const auto& schema = observed.schema;
        const bool boar = observed.family == NpcFamily::Boar;
        auto* cohort = antfarm_cohort(observed.family);
        auto& latest = cohort ? cohort->latest : scope.latest;
        if (active.seat != Seat::Host || !exact_director(director, active) || !local_pawn(director, active) ||
            !director_marker(receiver, director, active, observed.initial) || frame.Node() != find<U::UFunction>(spawn_path) ||
            data != get_object(schema.data, frame.Locals()) ||
            (observed.typed_squad ? !typed_pool_asset(*active.director, director, data) : !directed_squad(data, active)) ||
            directed_family(data, active) != observed.family ||
            (!observed.typed_squad && boar && boar_squad_size(data) != observed.max_count))
        {
            director_stage(draw, "post_spawn_scope_unqualified");
            return;
        }

        Observation next{receiver, data, {}, "stock_count_unavailable", 0};
        Actors after;
        const auto* output = schema.output_address(frame);
        const auto count = output ? schema.count->GetPropertyValue(output) : -1;
        if (count != observed.max_count)
        {
            if (output) next.stage = "stock_count_unexpected";
        }
        else if (observed.typed_squad)
        {
            if (!observed.births.empty() && observed.birth_failure.empty())
                for (const auto& unit : observed.births) after.push_back(unit.Get());
            else next.stage = observed.birth_failure.empty() ? "stock_ambient_births_unverified" : observed.birth_failure;
        }
        else if (!scene_npcs(active.world, boar ? boar_class : npc_class, after))
        {
            next.stage = "stock_husk_census_unavailable";
            after.clear();
        }
        else
        {
            std::erase_if(after, [&](auto* actor) { return contains(observed.before, actor); });
            if (after.empty() || !observed.birth_failure.empty() || after.size() != observed.births.size() ||
                static_cast<int>(after.size()) > count || !std::ranges::all_of(after, [&](auto* actor) {
                    return contains(observed.births, actor);
                }))
            {
                next.stage = observed.birth_failure.empty() ? "stock_husk_births_unverified" : observed.birth_failure;
                after.clear();
            }
        }
        if (after.empty())
        {
            director_stage(draw, next.stage);
            if (latest.cohort.empty()) latest = std::move(next);
            return;
        }
        if (!std::ranges::all_of(after, [&](U::UObject* actor) {
            if (observed.family == NpcFamily::Ambient)
                return in_level(actor, active.world) && byte_field(actor, STR("Role")) == 3;
            return boar ? in_level(actor, active.world) && exact(actor, boar_class) && byte_field(actor, STR("Role")) == 3
                        : stock_npc_authority(actor, npc_class, active.world);
        }))
        {
            next.stage = "stock_husk_authority_unverified";
        }
        else if (!std::ranges::all_of(after, [&](U::UObject* actor) {
            return stock_npc_controller(actor, active.world);
        }))
        {
            next.stage = "stock_husk_controller_unverified";
        }
        else
        {
            ++(cohort ? cohort->receipts : scope.receipts);
            for (auto* actor : after) next.cohort.emplace_back(actor);
            next.stage = observed.initial ? "stock_initial" : "stock_refresh";
            if ((boar || operate || production_roster) && Replication{}.apply(after))
            {
                next.replicated = static_cast<int>(after.size());
                if (production_roster)
                {
                    prune_roster(active.world);
                    for (auto* actor : after)
                    {
                        if (scope.roster.size() == max_scene_npcs) break;
                        scope.roster.push_back({actor, observed.family});
                    }
                }
            }
        }
        if (draw)
            director_stage(draw, next.cohort.empty() ? next.stage
                : next.replicated > 0 ? "host_cohort_published" : "host_cohort_not_replicated");
        // Retain the latest successful stock receipt across an occupied or failed marker.
        if (!next.cohort.empty() || latest.cohort.empty()) latest = std::move(next);
    }

    void census(const Network& active)
    {
        const auto now = Clock::now();
        if (now < scope.next_census) return;
        scope.next_census = now + std::chrono::milliseconds(750);
        scope.counts = {};
        scope.flayers.counts = {};
        scope.not_deer.counts = {};
        scope.boars.counts = {};
        scope.ambient.counts = {};
        scope.not_deer_replica.Reset();
        if (active.director->typed && active.seat == Seat::Host)
        {
            prune_roster(active.world);
            for (const auto& member : scope.roster)
            {
                if (member.family != NpcFamily::Ambient) continue;
                auto* actor = member.actor.Get();
                if (byte_field(actor, STR("Role")) != 3) continue;
                ++scope.ambient.counts.authority;
                if (bool_field(actor, STR("bReplicates"), true) &&
                    bool_field(actor, STR("bReplicateMovement"), true) && byte_field(actor, STR("RemoteRole")) == 1)
                    ++scope.ambient.counts.authority_network_ready;
            }
        }
        auto* antfarm = find<U::UClass>(antfarm_class);

        if (auto* base = find<U::UClass>(npc_base))
        {
            std::vector<U::AActor*> npcs;
            U::UGameplayStatics::GetAllActorsOfClass(active.world, base, npcs);
            for (auto* actor : npcs)
            {
                // Identify loaded classes from the actors. Looking up an unloaded optional class can scan the object array.
                const auto family = npc_family(actor);
                if (family == NpcFamily::Unknown || !roster_family(active, family) || (family == NpcFamily::NotDeer && !operate_not_deer) ||
                    !in_level(actor, active.world)) continue;
                auto* cohort = antfarm_cohort(family);
                auto& counts = cohort ? cohort->counts : scope.counts;
                if (family == NpcFamily::NotDeer && byte_field(actor, STR("Faction")) != not_deer_profile.faction)
                {
                    ++counts.other;
                    continue;
                }
                const auto role = byte_field(actor, STR("Role"));
                if (role == 3)
                {
                    ++counts.authority;
                    if (bool_field(actor, STR("bReplicates"), true) &&
                        bool_field(actor, STR("bReplicateMovement"), true) && byte_field(actor, STR("RemoteRole")) == 1)
                        ++counts.authority_network_ready;
                }
                else if (role == 1)
                {
                    ++counts.simulated;
                    if (live(object(actor, STR("Controller")))) ++counts.simulated_controllers;
                    if (family == NpcFamily::NotDeer)
                    {
                        if (counts.simulated == 1) scope.not_deer_replica = actor;
                        else scope.not_deer_replica.Reset();
                    }
                }
                else ++counts.other;
            }
        }

        if (antfarm)
        {
            std::vector<U::AActor*> farms;
            U::UGameplayStatics::GetAllActorsOfClass(active.world, antfarm, farms);
            for (auto* actor : farms)
            {
                if (!live(actor) || actor->GetClassPrivate() != antfarm || actor->GetWorld() != active.world ||
                    object(actor, STR("GameInstance")) != active.instance || !live(object(actor, STR("LoadedCell"))))
                    continue;
                ++scope.counts.antfarm_initialized;
                ++scope.flayers.counts.antfarm_initialized;
                if (operate_not_deer) ++scope.not_deer.counts.antfarm_initialized;
            }
        }
    }

    void push_name(const Lua& lua, U::UObject* item)
    {
        if (live(item)) lua.set_string(RC::to_utf8_string(item->GetFullName()));
        else lua.set_string("");
    }

    int report(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!hook_ready() || arguments < 2 || arguments > 4 || !lua.is_integer(1) || !lua.is_bool(2) ||
            (arguments >= 3 && !lua.is_string(3)) || (arguments == 4 && !lua.is_string(4))) return 0;
        const auto expected = static_cast<std::uintptr_t>(lua.get_integer());
        const auto host = lua.get_bool();
        const std::string selection = arguments >= 3 ? std::string(lua.get_string()) : std::string{};
        const bool not_deer = selection == "notdeer" && operate_not_deer;
        const auto* family = antfarm_cohort(not_deer ? NpcFamily::NotDeer :
            selection == "flayer" ? NpcFamily::Flayer : selection == "boar" ? NpcFamily::Boar :
            selection == "ambient" ? NpcFamily::Ambient : NpcFamily::Unknown);
        if (arguments >= 3 && selection != "husk" && !family) return 0;
        const auto* profile = arguments == 4 ? director_for(RC::ensure_str(lua.get_string())) : &crossroads;
        if (!profile || (selection == "ambient" && !profile->typed) ||
            (profile != &crossroads && selection != "husk" && selection != "boar" && selection != "ambient")) return 0;
        const auto active = network(find<U::UWorld>(profile->world));
        auto* director = find<U::UObject>(profile->director);
        if (active.seat != (host ? Seat::Host : Seat::Client) || !exact_director(director, active) ||
            !expected || reinterpret_cast<std::uintptr_t>(local_pawn(director, active)) != expected) return 0;
        update_scope(active);
        census(active);
        const auto& latest = family ? family->latest : scope.latest;
        const auto& counts = family ? family->counts : scope.counts;
        const auto& births = family ? family->client_births : scope.client_births;
        const auto& saved = not_deer ? scope.not_deer_saved : scope.saved_squad;
        lua.set_integer(family ? family->receipts : scope.receipts);
        lua.set_string(latest.stage);
        push_name(lua, latest.spawner.Get());
        push_name(lua, latest.data.Get());
        lua.set_integer(static_cast<std::int64_t>(latest.cohort.size()));
        lua.set_integer(latest.replicated);
        lua.set_integer(family ? family->spawn_denials : scope.spawn_denials);
        lua.set_integer(family ? family->ai_denials : scope.ai_denials);
        lua.set_integer(family ? family->calls : scope.natural_calls);
        lua.set_integer(counts.authority);
        lua.set_integer(counts.simulated);
        lua.set_integer(counts.other);
        lua.set_integer(counts.simulated_controllers);
        lua.set_integer(counts.antfarm_initialized);
        std::string roster;
        for (const auto& pointer : latest.cohort)
        {
            if (auto* actor = pointer.Get(); live(actor) && in_level(actor, active.world))
            {
                roster.append(RC::to_utf8_string(actor->GetFullName()));
                roster.push_back('\n');
            }
        }
        lua.set_string(roster);
        lua.set_integer(saved.calls);
        lua.set_integer(saved.denials);
        lua.set_string(saved.stage);
        push_name(lua, saved.spawner.Get());
        push_name(lua, saved.data.Get());
        lua.set_integer(counts.authority_network_ready);
        lua.set_integer(births.local_begins);
        lua.set_integer(births.local_initializations);
        lua.set_integer(births.early_spawn_denials);
        push_name(lua, births.last_actor.Get());
        push_name(lua, births.last_caller.Get());
        push_name(lua, births.last_script.Get());
        if (not_deer)
        {
            auto* replica = scope.not_deer_replica.Get();
            if (host || !exact(replica, not_deer_class) || !in_level(replica, active.world) ||
                byte_field(replica, STR("Faction")) != not_deer_profile.faction ||
                byte_field(replica, STR("Role")) != 1 || live(object(replica, STR("Controller"))))
                replica = nullptr;
            push_name(lua, replica);
            return 28;
        }
        return 27;
    }

    int read_director_diagnostic(const Lua& lua)
    {
        if (!hook_ready() || !watch_director || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_bool(2) || !lua.is_string(3)) return 0;
        const auto expected = static_cast<std::uintptr_t>(lua.get_integer());
        const bool host = lua.get_bool();
        const auto map = lua.get_string();
        const auto* profile = director_for(RC::ensure_str(map));
        if (!profile || !profile->typed) return 0;
        const auto active = network(find<U::UWorld>(profile->world));
        auto* director = find<U::UObject>(profile->director);
        if (!expected || active.seat != (host ? Seat::Host : Seat::Client) || !exact_director(director, active) ||
            reinterpret_cast<std::uintptr_t>(local_pawn(director, active)) != expected ||
            director_diagnostic.world.Get() != active.world || director_diagnostic.driver.Get() != active.driver ||
            director_diagnostic.seat != active.seat) return 0;

        const auto object_name = [](U::UObject* object) {
            return live(object) ? RC::to_utf8_string(object->GetFullName()) : std::string{"-"};
        };
        std::string rows;
        for (std::size_t index = 0; index < director_diagnostic.phases.size(); ++index)
        {
            const auto& phase = director_diagnostic.phases[index];
            const auto append = [&](std::string_view kind, const DirectorDecision& decision, std::string_view stage,
                                    U::UObject* marker, U::UObject* asset) {
                rows.append(index == 0 ? "initial\t" : "refresh\t");
                rows.append(kind).append("\t");
                for (const auto value : {decision.stock_day, decision.is_day, decision.only_track_time})
                    rows.append(value < 0 ? "-" : value == 0 ? "0" : "1").append("\t");
                rows.append(decision.game_time_ticks ? std::to_string(*decision.game_time_ticks) : "-").append("\t");
                rows.append(stage).append("\t").append(object_name(marker)).append("\t").append(object_name(asset)).append("\n");
            };
            if (phase.first_pool) append("pool", *phase.first_pool, "stock_pool_decision", nullptr, nullptr);
            if (!phase.first_draw.stage.empty())
                append("draw", phase.first_draw.decision, phase.first_draw.stage,
                       phase.first_draw.marker.Get(), phase.first_draw.asset.Get());
            if (!phase.first_supported_draw.stage.empty())
                append("supported", phase.first_supported_draw.decision, phase.first_supported_draw.stage,
                       phase.first_supported_draw.marker.Get(), phase.first_supported_draw.asset.Get());
        }
        lua.set_string(rows);
        return 1;
    }

    int report_host_roster(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!hook_ready() || !production_roster || (arguments != 1 && arguments != 2) || !lua.is_integer(1) ||
            (arguments == 2 && !lua.is_string(2))) return 0;
        const auto expected = static_cast<std::uintptr_t>(lua.get_integer());
        const auto* profile = arguments == 2 ? director_for(RC::ensure_str(lua.get_string())) : &crossroads;
        if (!profile) return 0;
        const auto active = network(find<U::UWorld>(profile->world));
        auto* director = find<U::UObject>(profile->director);
        if (!expected || active.seat != Seat::Host || !exact_director(director, active) ||
            reinterpret_cast<std::uintptr_t>(local_pawn(director, active)) != expected) return 0;
        update_scope(active);
        prune_roster(active.world);
        int live_boars{}, live_husks{}, defeated{};
        for (const auto& member : scope.roster)
        {
            auto* actor = member.actor.Get();
            if (bool_field(actor, STR("Alive"), false)) ++defeated;
            else if (member.family == NpcFamily::Boar) ++live_boars;
            else if (member.family == NpcFamily::Husk) ++live_husks;
        }
        lua.set_integer(live_husks);
        lua.set_integer(live_boars);
        lua.set_integer(defeated);
        return 3;
    }

    bool stock_script(U::UFunction* function, U::UClass* owner, int parameters, int size)
    {
        return function && owner && function->GetOuterPrivate() == owner && function->GetNumParms() == parameters &&
            function->GetParmsSize() == size && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net);
    }

    struct RefreshSchema
    {
        U::UFunction* count = find<U::UFunction>(living_count_path);
        U::UFunction* check = find<U::UFunction>(marker_check_path);
        U::UFunction* refresh = find<U::UFunction>(refresh_path);
        U::FIntProperty* count_result = field<U::FIntProperty>(count, STR("NPCs"));
        U::FBoolProperty* radius = field<U::FBoolProperty>(check, STR("CheckRadius"));
        U::FBoolProperty* eligible{};

        bool bind(U::UObject* director)
        {
            auto* type = director->GetClassPrivate();
            if (!stock_script(count, type, 1, sizeof(std::int32_t)) || !stock_script(refresh, type, 0, 0) ||
                !stock_script(check, find<U::UClass>(spawner_class), 2, 2) || !count_result ||
                !count_result->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm) || count_result->GetOffset_Internal() != 0 ||
                !radius || !radius->HasAnyPropertyFlags(U::CPF_Parm) || radius->HasAnyPropertyFlags(U::CPF_OutParm) ||
                !radius->IsNativeBool() || radius->GetOffset_Internal() != 0) return false;
            for (auto* parameter : U::TFieldRange<U::FProperty>(check, U::EFieldIterationFlags::IncludeDeprecated))
            {
                if (parameter == radius || !parameter->HasAnyPropertyFlags(U::CPF_Parm)) continue;
                if (eligible || parameter->HasAnyPropertyFlags(U::CPF_ReturnParm)) return false;
                eligible = U::CastField<U::FBoolProperty>(parameter);
                if (!eligible || !eligible->HasAnyPropertyFlags(U::CPF_OutParm) || eligible->GetArrayDim() != 1 ||
                    !eligible->IsNativeBool() || eligible->GetOffset_Internal() != 1) return false;
            }
            return eligible != nullptr;
        }
    };

    int stock_refresh(const Lua& lua)
    {
        std::int32_t population = -1, capacity = -1;
        int markers = -1, eligible = -1;
        std::int64_t previous = -1, current = -1;
        RefreshObservation observed;
        const auto reply = [&](std::string_view status) {
            lua.set_string(status);
            lua.set_integer(population);
            lua.set_integer(capacity);
            lua.set_integer(markers);
            lua.set_integer(eligible);
            lua.set_integer(observed.calls);
            push_name(lua, observed.marker.Get());
            push_name(lua, observed.data.Get());
            lua.set_integer(previous);
            lua.set_integer(current);
            return 10;
        };
        if (!hook_ready() || !refresh_fixture || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2))
            return reply("refresh_scope_unavailable");
        const auto address = lua.get_integer();
        const bool invoke = lua.get_bool();
        const auto active = network(find<U::UWorld>(world_path));
        auto* director = find<U::UObject>(director_path);
        if (active.seat != Seat::Host || !exact_director(director, active) || byte_field(director, STR("Role")) != 3)
            return reply("refresh_scope_unavailable");
        auto* host = local_pawn(director, active);
        auto* antfarm = object(director, STR("Antfarm"));
        auto* pool = object(director, STR("AmbientSquadPool"));
        if (address <= 0 || !host || reinterpret_cast<std::uintptr_t>(host) != static_cast<std::uintptr_t>(address) ||
            !one_remote_pawn(active) || !exact(antfarm, antfarm_class) || !in_level(antfarm, active.world) ||
            object(antfarm, STR("GameInstance")) != active.instance || !live(object(antfarm, STR("LoadedCell"))) ||
            !live(pool) || pool->GetPathName() != pool_path) return reply("refresh_scope_unavailable");
        update_scope(active);
        previous = current = scope.receipts;

        RefreshSchema schema;
        auto* limit = field<U::FIntProperty>(director, STR("MaxNPCs"));
        auto* list = field<U::FArrayProperty>(director, STR("Spawns_Reinforcments"));
        auto* entry = list ? U::CastField<U::FObjectProperty>(list->GetInner()) : nullptr;
        if (!limit || !entry || !schema.bind(director)) return reply("refresh_schema_unavailable");
        capacity = limit->GetPropertyValueInContainer(director);
        if (capacity != 12) return reply("refresh_capacity_unverified");
        director->ProcessEvent(schema.count, &population);
        if (population < 0 || population > 4096) return reply("refresh_population_unverified");

        U::FScriptArrayHelper rows(list, list->ContainerPtrToValuePtr<void>(director));
        markers = rows.Num();
        if (markers < 0 || static_cast<std::size_t>(markers) > marker_numbers.size()) return reply("refresh_markers_unverified");
        Actors candidates;
        for (int index = 0; index < markers; ++index)
        {
            auto* marker = get_object(entry, rows.GetRawPtr(index));
            if (!placed_spawner(marker, active.world) || byte_field(marker, STR("Role")) != 3 ||
                byte_field(marker, STR("Usage")) != 3 || std::ranges::find(candidates, marker) != candidates.end())
                return reply("refresh_markers_unverified");
            candidates.push_back(marker);
        }
        eligible = 0;
        for (auto* marker : candidates)
        {
            std::array<unsigned char, 2> parameters{};
            schema.radius->SetPropertyValueInContainer(parameters.data(), true);
            marker->ProcessEvent(schema.check, parameters.data());
            eligible += schema.eligible->GetPropertyValueInContainer(parameters.data()) ? 1 : 0;
        }
        if (population >= capacity) return reply("population_cap");
        if (eligible == 0) return reply("no_eligible_marker");
        if (!invoke) return reply("eligible");
        if (scope.refresh.used) return reply("refresh_already_used");
        scope.refresh = {};
        scope.refresh.used = scope.refresh.running = true;
        director->ProcessEvent(schema.refresh, nullptr);
        scope.refresh.running = false;
        observed = scope.refresh;
        current = scope.receipts;
        if (current > previous) return reply("stock_husk");
        if (observed.calls == 0) return reply("no_stock_attempt");
        auto* data = observed.data.Get();
        if (!data) return reply("stock_selection_unverified");
        return reply(data_index(data) < 0 ? "stock_other_squad" : "stock_husk_unverified");
    }

    int arm_combat(const Lua& lua)
    {
        const auto reply = [&](bool ok) { lua.set_bool(ok); return 1; };
        const auto arguments = lua.get_stack_size();
        if (!hook_ready() || !watch_combat || (arguments != 4 && arguments != 5) || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_integer(4) || (arguments == 5 && !lua.is_string(5))) return reply(false);
        const auto host_address = lua.get_integer();
        const auto guest_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        const auto source_address = lua.get_integer();
        const auto selected = arguments == 5 ? lua.get_string() : std::string_view{};
        const bool flayer = selected == "flayer" && publish_flayers && veto_flayer_spawns;
        const bool not_deer = selected == "notdeer" && operate_not_deer;
        if (arguments == 5 && !flayer && !not_deer) return reply(false);
        const auto* profile = not_deer ? &not_deer_profile : flayer ? &flayer_profile : nullptr;
        const auto family = profile ? profile->family : NpcFamily::Husk;
        const auto active = network(find<U::UWorld>(world_path));
        auto* director = find<U::UObject>(director_path);
        if (active.seat != Seat::Host || !exact_director(director, active)) return reply(false);
        update_scope(active);
        auto* host = local_pawn(director, active);
        auto* guest = one_remote_pawn(active);
        auto* canonical = object(active.instance, STR("SaveSlot_Ref"));
        auto* detached = object(guest, STR("Save"));
        const auto matches = [](auto* candidate, auto address) {
            return candidate && address > 0 && reinterpret_cast<std::uintptr_t>(candidate) == static_cast<std::uintptr_t>(address);
        };
        double host_health{}, guest_health{};
        SicknessSnapshot initial_sickness;
        const auto* antfarm = profile ? antfarm_cohort(family) : nullptr;
        if (profile && !antfarm) return reply(false);
        const auto& registered = antfarm ? antfarm->latest : scope.latest;
        auto* data = registered.data.Get();
        const auto qualified_stock = profile
            ? registered.stage == "stock_antfarm" && exact(data, data_class) && data->GetPathName() == profile->data
            : data_index(data) >= 0;
        if (!matches(host, host_address) || !matches(guest, guest_address) || !matches(detached, save_address) ||
            !exact(detached, save_class) || detached == canonical || !read_health(canonical, host_health) || host_health <= 0 ||
            !read_health(detached, guest_health) || guest_health <= 0 || registered.replicated <= 0 ||
            registered.cohort.size() != static_cast<std::size_t>(registered.replicated) ||
            !qualified_stock || !placed_spawner(registered.spawner.Get(), active.world) ||
            (flayer && !silverfish::native_guest_sickness::read_exact(guest, detached, canonical, initial_sickness))) return reply(false);
        bool includes_source = false;
        for (const auto& handle : registered.cohort)
        {
            auto* actor = handle.Get();
            if (!stock_npc_for_combat(actor, active.world, family)) return reply(false);
            includes_source |= matches(actor, source_address);
        }
        if (!includes_source) return reply(false);
        combat_watch = {};
        combat_watch.world = active.world;
        combat_watch.host = host;
        combat_watch.guest = guest;
        combat_watch.host_save = canonical;
        combat_watch.guest_save = detached;
        combat_watch.cohort = registered.cohort;
        combat_watch.family = family;
        combat_watch.host_at_arm = host_health;
        combat_watch.sickness_at_arm = initial_sickness;
        combat_watch.until = Clock::now() + combat_watch_time;
        return reply(true);
    }

    int read_flayer_projectile(const Lua& lua)
    {
        if (!hook_ready() || !publish_flayers || !veto_flayer_spawns || lua.get_stack_size() != 1 || !lua.is_integer() ||
            scope.seat != Seat::Host || !scope.world.Get()) return 0;
        const auto age = lua.get_integer();
        if (age == -1)
        {
            lua.set_string(scope.flayers.hit_status);
            return 1;
        }
        if (age < 0 || age >= static_cast<std::int64_t>(Flayers::hit_capacity) || age >= scope.flayers.hit_serial) return 0;
        const auto serial = scope.flayers.hit_serial - age;
        const auto& hit = scope.flayers.hits[static_cast<std::size_t>(serial - 1) % Flayers::hit_capacity];
        if (hit.serial != serial) return 0;
        lua.set_integer(serial);
        lua.set_string(hit.shooter);
        lua.set_string(hit.victim);
        lua.set_string(hit.projectile);
        lua.set_number(hit.before);
        lua.set_number(hit.after);
        lua.set_number(hit.damage);
        return 7;
    }

    int read_combat(const Lua& lua)
    {
        if (!hook_ready() || !watch_combat || lua.get_stack_size() != 0 || !combat_current()) return 0;
        auto* guest = combat_watch.guest.Get();
        auto* host = combat_watch.host.Get();
        U::UObject* acquired{};
        for (const auto& handle : combat_watch.cohort)
        {
            if (auto* actor = handle.Get(); live(actor) && object(actor, STR("CombatTarget")) == guest)
            {
                acquired = actor;
                break;
            }
        }
        double host_now{}, guest_now{};
        if (!read_health(combat_watch.host_save.Get(), host_now) || !read_health(combat_watch.guest_save.Get(), guest_now)) return 0;
        lua.set_string(combat_watch.guest_hits ? "stock_guest_health_debit" : "watching_stock_melee");
        push_name(lua, host);
        push_name(lua, guest);
        push_name(lua, combat_watch.attacker.Get());
        push_name(lua, acquired);
        lua.set_integer(combat_watch.attempts);
        lua.set_integer(combat_watch.guest_hits);
        lua.set_integer(combat_watch.host_hits);
        lua.set_number(combat_watch.host_at_arm);
        lua.set_number(combat_watch.host_at_hit);
        lua.set_number(host_now);
        lua.set_number(combat_watch.guest_at_hit);
        lua.set_number(combat_watch.guest_after);
        lua.set_number(guest_now);
        return 14;
    }

    int read_combat_diagnostic(const Lua& lua)
    {
        if (!hook_ready() || !watch_combat || lua.get_stack_size() != 0 || !combat_watch.world.Get()) return 0;
        lua.set_string(combat_current() ? "active" : "unavailable");
        lua.set_integer(combat_watch.damage_calls);
        lua.set_integer(combat_watch.attempts);
        lua.set_string(combat_watch.damage_status.data());
        lua.set_string(combat_watch.swing_status.data());
        const auto& before = combat_watch.pending_sickness;
        const auto& after = combat_watch.swing_after;
        lua.set_integer(after.guest_sickness - static_cast<std::int64_t>(before.guest_sickness));
        lua.set_integer(after.host_sickness - static_cast<std::int64_t>(before.host_sickness));
        lua.set_integer(after.infect_reads - static_cast<std::int64_t>(before.infect_reads));
        lua.set_integer(after.infect_writes - static_cast<std::int64_t>(before.infect_writes));
        return 9;
    }

    int read_combat_sickness(const Lua& lua)
    {
        if (!hook_ready() || !watch_combat || lua.get_stack_size() != 0 || combat_watch.family != NpcFamily::Flayer ||
            combat_watch.guest_hits <= 0 || !combat_current()) return 0;
        SicknessSnapshot current;
        if (!current_sickness(current) || !same_stock_sickness(combat_watch.sickness_at_arm, current)) return 0;
        const auto& before = combat_watch.sickness_before;
        const auto& after = combat_watch.sickness_after;
        push_name(lua, combat_watch.host.Get());
        push_name(lua, combat_watch.guest.Get());
        push_name(lua, combat_watch.attacker.Get());
        lua.set_integer(before.guest_sickness);
        lua.set_integer(after.guest_sickness);
        lua.set_integer(before.host_sickness);
        lua.set_integer(after.host_sickness);
        lua.set_integer(current.host_sickness);
        lua.set_integer(before.infect_reads);
        lua.set_integer(after.infect_reads);
        lua.set_integer(before.infect_writes);
        lua.set_integer(after.infect_writes);
        lua.set_integer(flayer_infection);
        return 13;
    }

    int clear_combat(const Lua&)
    {
        if (watch_combat && U::IsInGameThreadRaw()) combat_watch = {};
        return 0;
    }

    int reset(const Lua&)
    {
        silverfish::campaign_npc::clear_scope();
        return 0;
    }
}

namespace silverfish::campaign_npc
{
    void install_hooks()
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || !env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC") ||
            installed.load(std::memory_order_acquire)) return;
        operate = env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE");
        production_roster = env("SILVERFISH_COOP_NATIVE_CROSSROADS_ROSTER");
        publish_flayers = production_roster || env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_PUBLISH");
        veto_flayer_spawns = production_roster || env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_VETO");
        operate_not_deer = env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_NOTDEER");
        operate_boars = production_roster || (env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_BOAR_FIXTURE"));
        const bool headless_fixture = env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_FIXTURES");
        watch_flayer_projectiles = headless_fixture && publish_flayers && veto_flayer_spawns;
        watch_combat = headless_fixture && env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_COMBAT");
        refresh_fixture = headless_fixture && env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH");
        watch_director = production_roster && env("SILVERFISH_COOP_HEADLESS") &&
            (env("SILVERFISH_COOP_RUIN_NPC_DIAGNOSTIC") || env("SILVERFISH_COOP_FOREST_NPC_DIAGNOSTIC"));
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            on_pre, {false, false, STR("SilverfishCoopNative"), STR("Qualify natural CrossRoads Husk squads")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            on_post, {false, false, STR("SilverfishCoopNative"), STR("Observe natural CrossRoads Husk squads")});
        callbacks[2] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* receiver, U::UFunction* function, void* parameters) {
                if (hook_ready() && !guard_turret_fire(call, receiver, function) &&
                    !prevent_roster_damage(call, receiver, function, nullptr, parameters))
                    prevent_replica_brain(call, receiver, function);
            }, {false, false, STR("SilverfishCoopNative"), STR("Guard stock CrossRoads Husk replica brains")});
        callbacks[3] = Hook::RegisterProcessInternalPreCallback(
            [](Iteration& call, U::UObject* receiver, U::FFrame& frame, void*) {
                if (!hook_ready() || (!operate && !veto_flayer_spawns && !operate_not_deer && !operate_boars) || !receiver || !frame.Node() ||
                    call.OriginalFunctionCallPrevented()) return;
                if (guard_turret_fire(call, receiver, frame.Node()) ||
                    prevent_roster_damage(call, receiver, frame.Node(), &frame, nullptr)) return;
                static const U::FName spawn_name{STR("SpawnSquadFromData")};
                static const U::FName load_name{STR("LoadSquad")};
                const auto name = frame.Node()->GetFName();
                if ((name != load_name && (name != spawn_name || (!operate && !operate_boars))) || !squad_spawner(receiver)) return;
                const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
                if (active.seat != Seat::Client) return;
                if (name == spawn_name) prevent_client_spawn(call, receiver, frame, active);
                else inspect_saved_squad(call, receiver, frame);
            }, {false, false, STR("SilverfishCoopNative"), STR("Guard reflected CrossRoads client spawns")});
        callbacks[4] = Hook::RegisterLoadMapPreCallback(
            [](Hook::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL url, U::UPendingNetGame* pending, U::FString&) {
                if (!hook_ready()) return;
                pending_client_load = {};
                if (!pending || url.Map.IsEmpty()) return;
                const auto* profile = director_for(*url.Map);
                if (!profile) return;
                // The engine can start map Blueprints before publishing World.NetDriver.
                auto* pending_object = reinterpret_cast<U::UObject*>(pending);
                if (!exact(pending_object, STR("/Script/Engine.PendingNetGame"))) return;
                auto* driver = object(pending_object, STR("NetDriver"));
                auto* connection = object(driver, STR("ServerConnection"));
                if (server_connection(connection, driver)) pending_client_load = {driver, connection, &context, profile};
            }, {false, true, STR("SilverfishCoopNative"), STR("Bind early connected CrossRoads load")});
        callbacks[5] = Hook::RegisterLoadMapPostCallback(
            [](Hook::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL, U::UPendingNetGame*, U::FString&) {
                if (hook_ready() && pending_client_load.context == &context) pending_client_load = {};
            }, {false, true, STR("SilverfishCoopNative"), STR("Release early CrossRoads load binding")});
        callbacks[6] = Hook::RegisterBeginPlayPreCallback(
            [](Iteration&, U::AActor* candidate) {
                if (!hook_ready()) return;
                const auto family = npc_family(candidate);
                if (family == NpcFamily::Unknown || (family == NpcFamily::NotDeer && !operate_not_deer) ||
                    byte_field(candidate, STR("Role")) != 3) return;
                const auto active = network(candidate->GetWorld(), true);
                if (active.seat != Seat::Client || !roster_family(active, family) || !in_level(candidate, active.world)) return;
                update_scope(active);
                auto& births = births_for(family);
                ++births.local_begins;
                births.last_actor = candidate;
                births.last_caller.Reset();
                births.last_script.Reset();
            }, {false, false, STR("SilverfishCoopNative"), STR("Observe local CrossRoads Husk births")});
        if (callbacks[4] == Hook::ERROR_ID || callbacks[5] == Hook::ERROR_ID)
        {
            for (const int index : {4, 5})
            {
                if (callbacks[index] != Hook::ERROR_ID) Hook::UnregisterCallback(callbacks[index]);
                callbacks[index] = Hook::ERROR_ID;
            }
        }
        const bool ok = std::all_of(callbacks.begin(), callbacks.begin() + 3,
            [](auto callback) { return callback != Hook::ERROR_ID; });
        if (!ok) uninstall_hooks();
        installed.store(ok, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
        if (U::IsInGameThreadRaw())
        {
            pending_client_load = {};
            director_diagnostic = {};
        }
        installed.store(false, std::memory_order_release);
        for (auto& callback : callbacks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishReadCampaignNpc", report);
        lua.register_function("SilverfishClearCampaignNpc", reset);
        if (production_roster)
        {
            lua.register_function("SilverfishReadCampaignNpcHostRoster", report_host_roster);
            lua.register_function("SilverfishSetTurretInactiveTargets", set_turret_inactive);
        }
        if (watch_director) lua.register_function("SilverfishReadAmbientDirectorDiagnostic", read_director_diagnostic);
        if (watch_flayer_projectiles)
            lua.register_function("SilverfishReadCampaignFlayerProjectile", read_flayer_projectile);
        if (refresh_fixture) lua.register_function("SilverfishStockCampaignNpcRefresh", stock_refresh);
        if (watch_combat)
        {
            lua.register_function("SilverfishArmCampaignNpcCombat", arm_combat);
            lua.register_function("SilverfishReadCampaignNpcCombat", read_combat);
            lua.register_function("SilverfishReadCampaignNpcCombatDiagnostic", read_combat_diagnostic);
            lua.register_function("SilverfishReadCampaignNpcCombatSickness", read_combat_sickness);
            lua.register_function("SilverfishClearCampaignNpcCombat", clear_combat);
        }
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw())
        {
            scope = {};
            attempt = {};
            antfarm_load = {};
            combat_watch = {};
            flayer_hit = {};
            turret_inactive = {};
        }
    }
}
