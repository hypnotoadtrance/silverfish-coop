#include "campaign_npc.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
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

    constexpr auto world_package = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto director_class = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C");
    constexpr auto director_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.DynaimicSpawnControler_BP_C_1");
    constexpr auto initial_path = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:Spawn_InitialActors");
    constexpr auto refresh_path = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:RefreshEnemies");
    constexpr auto spawner_class = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C");
    constexpr auto spawner_prefix = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.AI_Spawner_Squad_BP_C_");
    constexpr auto spawn_path = STR("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnSquadFromData");
    constexpr auto data_class = STR("/Game/Classes/Libraries/DataAssets/Squads/SquadData.SquadData_C");
    constexpr auto npc_class = STR("/Game/Classes/NPCs/NPC_Zomb_01.NPC_Zomb_01_C");
    constexpr auto flayer_class = STR("/Game/Classes/NPCs/NPC_Flayer.NPC_Flayer_C");
    constexpr auto flayer_squad = STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Flayers.Flayers");
    constexpr auto npc_controller = STR("/Game/Classes/NPCs/NPC_Assets/MasterNPC_AI_Controler_BP.MasterNPC_AI_Controler_BP_C");
    constexpr int max_flayers = 2;
    constexpr auto npc_base = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto initialize_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:InitializeAI");
    constexpr auto perceive_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Proscess_TargetPerception");
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
    constexpr std::array squad_assets{
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_Scattered.Husks_Scattered"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmSmall.Husks_SwarmSmall"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmLarge.Husks_SwarmLarge"),
    };
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
    };

    struct PendingClientLoad
    {
        U::FWeakObjectPtr driver, connection;
        U::FWorldContext* context{};
    } pending_client_load;

    bool server_connection(U::UObject* connection, U::UObject* driver)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(driver) && live(connection) && type && connection->IsA(type) && object(connection, STR("Driver")) == driver;
    }

    Network network(U::UWorld* world, bool during_load = false)
    {
        if (!live(world) || world->GetPathName() != world_path) return {};
        auto* instance = object(world, STR("OwningGameInstance"));
        if (!exact(instance, instance_class)) return {};
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (live(driver) && object(driver, STR("World")) == world && connection)
        {
            auto* server = get_object(connection, driver);
            if (server_connection(server, driver)) return {world, driver, instance, Seat::Client};
            if (server) return {};
            if (!during_load || driver != pending_client_load.driver.Get()) return {world, driver, instance, Seat::Host};
        }
        if (!during_load || !pending_client_load.context) return {};
        auto* pending = pending_client_load.driver.Get();
        if ((driver && driver != pending) || !server_connection(pending_client_load.connection.Get(), pending)) return {};
        return {world, pending, instance, Seat::Client};
    }

    bool exact_director(U::UObject* actor, const Network& active)
    {
        return exact(actor, director_class) && actor->GetPathName() == director_path && in_level(actor, active.world) &&
            object(actor, STR("As Game Instance FP")) == active.instance;
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

    bool placed_spawner(U::UObject* actor, U::UWorld* world)
    {
        if (!exact(actor, spawner_class) || !in_level(actor, world)) return false;
        auto* arrow = object(actor, STR("Arrow_Leader"));
        if (!exact(arrow, STR("/Script/Engine.ArrowComponent")) || arrow->GetOuterPrivate() != actor) return false;
        const std::wstring_view prefix{spawner_prefix};
        const auto path = actor->GetPathName();
        if (!std::wstring_view{path}.starts_with(prefix)) return false;
        const auto suffix = std::wstring_view{path}.substr(prefix.size());
        return std::ranges::any_of(marker_numbers, [suffix](int number) { return suffix == std::to_wstring(number); });
    }

    int data_index(U::UObject* data)
    {
        if (!exact(data, data_class)) return -1;
        const auto path = data->GetPathName();
        const auto match = std::ranges::find_if(squad_assets, [&path](const auto candidate) { return path == candidate; });
        return match == squad_assets.end() ? -1 : static_cast<int>(match - squad_assets.begin());
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

    struct Flayers
    {
        std::int64_t calls{}, receipts{}, spawn_denials{}, ai_denials{};
        Observation latest;
        ClientBirths client_births;
        Counts counts;
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
        Clock::time_point next_census{};
        Counts counts;
    } scope;

    struct Attempt
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr world, director, spawner, data;
        Handles before, births;
        SpawnSchema schema{};
        int max_count{};
        bool initial{};
        std::string_view birth_failure{};
    } attempt;

    struct FlayerLoad
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr world, antfarm, marker, squad, data;
        Handles before, units;
    } flayer_load;

    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID,
        Hook::ERROR_ID};
    bool operate{};
    bool publish_flayers{};
    bool veto_flayer_spawns{};

    void update_scope(const Network& active)
    {
        if (scope.world.Get() == active.world && scope.driver.Get() == active.driver && scope.seat == active.seat) return;
        scope = {};
        attempt = {};
        flayer_load = {};
        scope.world = active.world;
        scope.driver = active.driver;
        scope.seat = active.seat;
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
        if (!world || parent.Object() != spawner || !exact(actor, npc_class) ||
            !in_level(actor, world) || contains(attempt.before, actor) || !bool_field(actor, STR("Wander"), true))
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
        attempt.births.emplace_back(actor);
    }

    void inspect_saved_squad(Iteration& call, U::UObject* receiver, U::FFrame& frame)
    {
        auto* function = frame.Node();
        if (function != find<U::UFunction>(load_squad_path) || !exact(receiver, spawner_class)) return;
        auto* caller = frame.PreviousFrame();
        if (!caller || !caller->Node()) return;
        auto* antfarm = caller->Object();
        if (!exact(antfarm, antfarm_class) || !std::ranges::any_of(saved_squad_sources, [caller](const auto path) {
            return caller->Node() == find<U::UFunction>(path);
        })) return;

        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
        if (!active.world || !placed_spawner(receiver, active.world) || !in_level(antfarm, active.world) ||
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
        const bool flayers = exact(data, data_class) && data->GetPathName() == flayer_squad;
        if (!bool_field(data, STR("ForceWander"), true) || (flayers
            ? caller->Node() == find<U::UFunction>(saved_squad_sources.back()) : data_index(data) < 0)) return;
        if (flayers) ++scope.flayers.calls;

        receipt.stage = "stock_saved_squad_members_unverified";
        U::FScriptArrayHelper units(units_property, units_property->ContainerPtrToValuePtr<void>(squad));
        const auto unit_count = units.Num();
        const auto maximum = flayers ? static_cast<std::size_t>(max_flayers) : max_scene_npcs;
        if (unit_count <= 0 || static_cast<std::size_t>(unit_count) > maximum) return;
        Handles qualified_units;
        for (int i = 0; i < unit_count; ++i)
        {
            auto* unit = get_object(unit_element, units.GetRawPtr(i));
            if (!exact(unit, antfarm_unit_class) || object(unit, STR("Controller")) != antfarm) return;
            const auto* soft = unit_class->ContainerPtrToValuePtr<U::FSoftObjectPtr>(unit);
            const auto& path = soft->ToSoftObjectPath();
            const auto asset = path.GetAssetPathString();
            if (!path.GetSubPathString().IsEmpty() || std::wstring_view{*asset} != (flayers ? flayer_class : npc_class)) return;
            if (flayers)
            {
                auto* npc = field<U::FObjectProperty>(unit, STR("Npc"));
                if (!npc || npc->GetPropertyClass().Get() != base || live(get_object(npc, unit)) || contains(qualified_units, unit)) return;
                qualified_units.emplace_back(unit);
            }
        }

        receipt.stage = "stock_saved_squad_observed";
        if (flayers && active.seat == Seat::Host && !flayer_load.frame && exact(object(active.instance, STR("SaveSlot_Ref")), save_class))
        {
            Actors before;
            if (!scene_npcs(active.world, flayer_class, before)) return;
            Handles original;
            for (auto* actor : before) original.emplace_back(actor);
            flayer_load = {&frame, active.world, antfarm, receiver, squad, data, std::move(original), std::move(qualified_units)};
        }
        if (active.seat != Seat::Client || !(flayers ? veto_flayer_spawns : operate)) return;
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented())
        {
            ++receipt.denials;
            receipt.stage = "stock_saved_squad_denied";
            if (flayers)
            {
                auto& result = scope.flayers;
                ++result.spawn_denials;
                if (network(active.world).seat != Seat::Client) ++result.client_births.early_spawn_denials;
                result.latest = {receiver, data, {}, "stock_client_flayer_load_denied", 0};
            }
        }
    }

    bool stock_npc_authority(U::UObject* actor, const U::TCHAR* type, U::UWorld* world)
    {
        return exact(actor, type) && in_level(actor, world) && byte_field(actor, STR("Faction")) == 6 &&
            byte_field(actor, STR("Role")) == 3;
    }

    bool stock_npc_controller(U::UObject* actor, U::UWorld* world)
    {
        auto* controller = object(actor, STR("Controller"));
        return exact(controller, npc_controller) && static_cast<U::AActor*>(controller)->GetWorld() == world &&
            object(controller, STR("Pawn")) == actor && byte_field(controller, STR("Role")) == 3;
    }

    void observe_flayer_load(Iteration& call, U::UObject* receiver, U::FFrame& frame)
    {
        const auto observed = std::move(flayer_load);
        flayer_load = {};
        if (call.OriginalFunctionCallPrevented() || frame.Node() != find<U::UFunction>(load_squad_path)) return;
        const auto active = network(U::Cast<U::UWorld>(observed.world.Get()));
        auto* stock = observed.antfarm.Get();
        auto* squad = observed.squad.Get();
        auto* data = observed.data.Get();
        if (active.seat != Seat::Host || !placed_spawner(receiver, active.world) || !in_level(stock, active.world) ||
            object(stock, STR("GameInstance")) != active.instance || !exact(object(active.instance, STR("SaveSlot_Ref")), save_class) ||
            !exact(squad, antfarm_squad_class) || object(squad, STR("Controller")) != stock || object(squad, STR("Data")) != data ||
            !exact(data, data_class) || data->GetPathName() != flayer_squad) return;

        Observation next{receiver, data, {}, "stock_flayer_links_unverified", 0};
        Actors cohort;
        const auto proof = [&] {
            for (const auto& handle : observed.units)
            {
                auto* unit = handle.Get();
                if (!exact(unit, antfarm_unit_class) || object(unit, STR("Controller")) != stock) return false;
                auto* actor = object(unit, STR("Npc"));
                if (!live(actor)) continue;
                if (!stock_npc_authority(actor, flayer_class, active.world) || !bool_field(actor, STR("Wander"), true) ||
                    !stock_npc_controller(actor, active.world) || contains(observed.before, actor) ||
                    std::ranges::find(cohort, actor) != cohort.end()) return false;
                cohort.push_back(actor);
            }
            Actors newborn;
            if (cohort.empty() || !scene_npcs(active.world, flayer_class, newborn)) return false;
            std::erase_if(newborn, [&](auto* actor) { return contains(observed.before, actor); });
            return newborn.size() == cohort.size() && std::ranges::is_permutation(cohort, newborn);
        };
        if (proof())
        {
            ++scope.flayers.receipts;
            for (auto* actor : cohort) next.cohort.emplace_back(actor);
            next.stage = "stock_antfarm";
            if (publish_flayers && Replication{}.apply(cohort)) next.replicated = static_cast<int>(cohort.size());
        }
        if (!next.cohort.empty() || scope.flayers.latest.cohort.empty()) scope.flayers.latest = std::move(next);
    }

    void prevent_replica_brain(Iteration& call, U::UObject* receiver, U::UFunction* function)
    {
        if ((!operate && !publish_flayers) || !function || !receiver || call.OriginalFunctionCallPrevented()) return;
        static const U::FName init_name{STR("InitializeAI")};
        static const U::FName perceive_name{STR("Proscess_TargetPerception")};
        const auto name = function->GetFName();
        if ((name != init_name && name != perceive_name) ||
            function != find<U::UFunction>(name == init_name ? initialize_path : perceive_path) ||
            function->GetReturnProperty() || byte_field(receiver, STR("Role")) != 1) return;
        const bool flayers = exact(receiver, flayer_class);
        if (flayers ? !publish_flayers : (!operate || !exact(receiver, npc_class))) return;
        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld());
        if (active.seat != Seat::Client || !in_level(receiver, active.world)) return;
        update_scope(active);
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented()) ++(flayers ? scope.flayers.ai_denials : scope.ai_denials);
    }

    void prevent_client_spawn(Iteration& call, U::UObject* receiver, U::FFrame& frame, const Network& active)
    {
        if (!placed_spawner(receiver, active.world)) return;
        update_scope(active);
        SpawnSchema schema;
        if (const auto error = schema.bind_base(frame.Node()); !error.empty())
        {
            scope.latest = {receiver, {}, {}, error, 0};
            return;
        }
        auto* data = get_object(schema.data, frame.Locals());
        if (data_index(data) < 0 || !bool_field(data, STR("ForceWander"), true)) return;
        ++scope.natural_calls;
        scope.latest = {receiver, data, {}, "stock_client_husk_spawn_observed", 0};
        if (!operate) return;
        auto* output = schema.output_address(frame);
        if (!output)
        {
            scope.latest.stage = "stock_client_husk_output_unverified";
            return;
        }
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented())
        {
            schema.count->SetPropertyValue(output, 0);
            ++scope.spawn_denials;
            if (network(active.world).seat != Seat::Client) ++scope.client_births.early_spawn_denials;
            scope.latest.stage = "stock_client_husk_spawn_denied";
        }
    }

    void on_pre(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready() || !receiver || !frame.Node() || call.OriginalFunctionCallPrevented()) return;
        static const U::FName spawn_name{STR("SpawnSquadFromData")};
        static const U::FName load_name{STR("LoadSquad")};
        static const U::FName init_name{STR("InitializeAI")};
        const auto name = frame.Node()->GetFName();

        if (name == init_name && attempt.frame && frame.PreviousFrame() == attempt.frame &&
            frame.Node() == find<U::UFunction>(initialize_path))
        {
            // ForceWander Husk assets initialize each newly finished actor directly and skip the squad roster append.
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
            const bool flayers = exact(receiver, flayer_class);
            if (flayers || exact(receiver, npc_class))
            {
                const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld(), true);
                if (active.seat == Seat::Client && in_level(receiver, active.world))
                {
                    update_scope(active);
                    auto& births = flayers ? scope.flayers.client_births : scope.client_births;
                    ++births.local_initializations;
                    births.last_actor = receiver;
                    auto* parent = frame.PreviousFrame();
                    births.last_caller = parent ? parent->Object() : nullptr;
                    births.last_script = parent ? parent->Node() : nullptr;
                }
            }
        }
        prevent_replica_brain(call, receiver, frame.Node());

        if (name != spawn_name || !exact(receiver, spawner_class)) return;
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
        if (!exact_director(director, active) || !placed_spawner(receiver, active.world) ||
            byte_field(receiver, STR("Role")) != 3 || byte_field(receiver, STR("Usage")) != (initial ? 0 : 3)) return;
        update_scope(active);
        SpawnSchema schema;
        if (const auto error = schema.bind_base(frame.Node()); !error.empty())
        {
            if (scope.latest.cohort.empty()) scope.latest = {receiver, {}, {}, error, 0};
            return;
        }
        ++scope.natural_calls;
        auto* data = get_object(schema.data, frame.Locals());
        const auto source = data_index(data);
        if (source < 0 || !bool_field(data, STR("ForceWander"), true)) return;


        if (const auto error = schema.bind_birth(frame.Node()); !error.empty())
        {
            if (scope.latest.cohort.empty()) scope.latest = {receiver, data, {}, error, 0};
            return;
        }
        if (!local_pawn(director, active)) return;
        Actors before;
        if (!scene_npcs(active.world, npc_class, before)) return;
        Handles baseline;
        baseline.reserve(before.size());
        for (auto* existing : before) baseline.emplace_back(existing);
        attempt = {&frame, active.world, director, receiver, data, std::move(baseline), {}, schema,
            maximum_squad_sizes[static_cast<std::size_t>(source)], initial, {}};
    }

    void on_post(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready()) return;
        if (flayer_load.frame == &frame && flayer_load.marker.Get() == receiver)
        {
            observe_flayer_load(call, receiver, frame);
            return;
        }
        if (attempt.frame != &frame || attempt.spawner.Get() != receiver) return;
        const auto observed = std::move(attempt);
        attempt = {};
        if (call.OriginalFunctionCallPrevented()) return;
        const auto active = network(U::Cast<U::UWorld>(observed.world.Get()));
        auto* director = observed.director.Get();
        auto* data = observed.data.Get();
        const auto& schema = observed.schema;
        if (active.seat != Seat::Host || !exact_director(director, active) || !local_pawn(director, active) ||
            !placed_spawner(receiver, active.world) || frame.Node() != find<U::UFunction>(spawn_path) ||
            data != get_object(schema.data, frame.Locals()) || data_index(data) < 0) return;

        Observation next{receiver, data, {}, "stock_count_unavailable", 0};
        Actors after;
        const auto* output = schema.output_address(frame);
        const auto count = output ? schema.count->GetPropertyValue(output) : -1;
        if (count != observed.max_count)
        {
            if (output) next.stage = "stock_count_unexpected";
        }
        else if (!scene_npcs(active.world, npc_class, after))
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
            if (scope.latest.cohort.empty()) scope.latest = std::move(next);
            return;
        }
        if (!std::ranges::all_of(after, [&](U::UObject* actor) {
            return stock_npc_authority(actor, npc_class, active.world);
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
            ++scope.receipts;
            for (auto* actor : after) next.cohort.emplace_back(actor);
            next.stage = observed.initial ? "stock_initial" : "stock_refresh";
            if (operate && Replication{}.apply(after)) next.replicated = static_cast<int>(after.size());
        }
        // Retain the latest successful stock receipt across an occupied or failed marker.
        if (!next.cohort.empty() || scope.latest.cohort.empty()) scope.latest = std::move(next);
    }

    void census(const Network& active)
    {
        const auto now = Clock::now();
        if (now < scope.next_census) return;
        scope.next_census = now + std::chrono::milliseconds(750);
        scope.counts = {};
        scope.flayers.counts = {};
        auto* husk = find<U::UClass>(npc_class);
        auto* flayer = find<U::UClass>(flayer_class);
        auto* antfarm = find<U::UClass>(antfarm_class);
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object_ptr, int, int) {
            if (!live(object_ptr)) return RC::LoopAction::Continue;
            auto* type = object_ptr->GetClassPrivate();
            if ((type == husk || type == flayer) && in_level(object_ptr, active.world))
            {
                auto& counts = type == flayer ? scope.flayers.counts : scope.counts;
                const auto role = byte_field(object_ptr, STR("Role"));
                if (role == 3)
                {
                    ++counts.authority;
                    if (bool_field(object_ptr, STR("bReplicates"), true) &&
                        bool_field(object_ptr, STR("bReplicateMovement"), true) && byte_field(object_ptr, STR("RemoteRole")) == 1)
                        ++counts.authority_network_ready;
                }
                else if (role == 1)
                {
                    ++counts.simulated;
                    if (live(object(object_ptr, STR("Controller")))) ++counts.simulated_controllers;
                }
                else ++counts.other;
            }
            else if (type == antfarm && static_cast<U::AActor*>(object_ptr)->GetWorld() == active.world &&
                     object(object_ptr, STR("GameInstance")) == active.instance && live(object(object_ptr, STR("LoadedCell"))))
            {
                ++scope.counts.antfarm_initialized;
                ++scope.flayers.counts.antfarm_initialized;
            }
            return RC::LoopAction::Continue;
        });
    }

    void push_name(const Lua& lua, U::UObject* item)
    {
        if (live(item)) lua.set_string(RC::to_utf8_string(item->GetFullName()));
        else lua.set_string("");
    }

    int report(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!hook_ready() || (arguments != 2 && arguments != 3) || !lua.is_integer(1) || !lua.is_bool(2) ||
            (arguments == 3 && !lua.is_string(3))) return 0;
        const auto expected = static_cast<std::uintptr_t>(lua.get_integer());
        const auto host = lua.get_bool();
        const bool flayers = arguments == 3 && lua.get_string() == "flayer";
        if (arguments == 3 && !flayers) return 0;
        const auto active = network(find<U::UWorld>(world_path));
        auto* director = find<U::UObject>(director_path);
        if (active.seat != (host ? Seat::Host : Seat::Client) || !exact_director(director, active) ||
            !expected || reinterpret_cast<std::uintptr_t>(local_pawn(director, active)) != expected) return 0;
        update_scope(active);
        census(active);
        const auto& latest = flayers ? scope.flayers.latest : scope.latest;
        const auto& counts = flayers ? scope.flayers.counts : scope.counts;
        const auto& births = flayers ? scope.flayers.client_births : scope.client_births;
        lua.set_integer(flayers ? scope.flayers.receipts : scope.receipts);
        lua.set_string(latest.stage);
        push_name(lua, latest.spawner.Get());
        push_name(lua, latest.data.Get());
        lua.set_integer(static_cast<std::int64_t>(latest.cohort.size()));
        lua.set_integer(latest.replicated);
        lua.set_integer(flayers ? scope.flayers.spawn_denials : scope.spawn_denials);
        lua.set_integer(flayers ? scope.flayers.ai_denials : scope.ai_denials);
        lua.set_integer(flayers ? scope.flayers.calls : scope.natural_calls);
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
        lua.set_integer(scope.saved_squad.calls);
        lua.set_integer(scope.saved_squad.denials);
        lua.set_string(scope.saved_squad.stage);
        push_name(lua, scope.saved_squad.spawner.Get());
        push_name(lua, scope.saved_squad.data.Get());
        lua.set_integer(counts.authority_network_ready);
        lua.set_integer(births.local_begins);
        lua.set_integer(births.local_initializations);
        lua.set_integer(births.early_spawn_denials);
        push_name(lua, births.last_actor.Get());
        push_name(lua, births.last_caller.Get());
        push_name(lua, births.last_script.Get());
        return 27;
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
        publish_flayers = env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_PUBLISH");
        veto_flayer_spawns = env("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_FLAYER_VETO");
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            on_pre, {false, false, STR("SilverfishCoopNative"), STR("Qualify natural CrossRoads Husk squads")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            on_post, {false, false, STR("SilverfishCoopNative"), STR("Observe natural CrossRoads Husk squads")});
        callbacks[2] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* receiver, U::UFunction* function, void*) {
                if (hook_ready()) prevent_replica_brain(call, receiver, function);
            }, {false, false, STR("SilverfishCoopNative"), STR("Guard stock CrossRoads Husk replica brains")});
        callbacks[3] = Hook::RegisterProcessInternalPreCallback(
            [](Iteration& call, U::UObject* receiver, U::FFrame& frame, void*) {
                if (!hook_ready() || (!operate && !veto_flayer_spawns) || !receiver || !frame.Node() ||
                    call.OriginalFunctionCallPrevented()) return;
                static const U::FName spawn_name{STR("SpawnSquadFromData")};
                static const U::FName load_name{STR("LoadSquad")};
                const auto name = frame.Node()->GetFName();
                if ((name != load_name && (name != spawn_name || !operate)) || !exact(receiver, spawner_class)) return;
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
                const std::wstring_view package{*url.Map};
                if (package != world_package && package != world_path) return;
                // The engine can start map Blueprints before publishing World.NetDriver.
                auto* pending_object = reinterpret_cast<U::UObject*>(pending);
                if (!exact(pending_object, STR("/Script/Engine.PendingNetGame"))) return;
                auto* driver = object(pending_object, STR("NetDriver"));
                auto* connection = object(driver, STR("ServerConnection"));
                if (server_connection(connection, driver)) pending_client_load = {driver, connection, &context};
            }, {false, true, STR("SilverfishCoopNative"), STR("Bind early connected CrossRoads load")});
        callbacks[5] = Hook::RegisterLoadMapPostCallback(
            [](Hook::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL, U::UPendingNetGame*, U::FString&) {
                if (hook_ready() && pending_client_load.context == &context) pending_client_load = {};
            }, {false, true, STR("SilverfishCoopNative"), STR("Release early CrossRoads load binding")});
        callbacks[6] = Hook::RegisterBeginPlayPreCallback(
            [](Iteration&, U::AActor* candidate) {
                if (!hook_ready()) return;
                const bool flayers = exact(candidate, flayer_class);
                if ((!flayers && !exact(candidate, npc_class)) || byte_field(candidate, STR("Role")) != 3) return;
                const auto active = network(candidate->GetWorld(), true);
                if (active.seat != Seat::Client || !in_level(candidate, active.world)) return;
                update_scope(active);
                auto& births = flayers ? scope.flayers.client_births : scope.client_births;
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
        if (U::IsInGameThreadRaw()) pending_client_load = {};
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
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw())
        {
            scope = {};
            attempt = {};
            flayer_load = {};
        }
    }
}
