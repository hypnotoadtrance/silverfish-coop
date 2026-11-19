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
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
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
    constexpr auto npc_base = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto initialize_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:InitializeAI");
    constexpr auto perceive_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Proscess_TargetPerception");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto antfarm_class = STR("/Game/Classes/AntFarm/AntfarmController.AntfarmController_C");
    constexpr std::array marker_numbers{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24,
        26, 27, 28, 30, 31, 32, 34, 35, 37, 38, 40, 41};
    constexpr std::array squad_assets{
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_Scattered.Husks_Scattered"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmSmall.Husks_SwarmSmall"),
        STR("/Game/Classes/Libraries/DataAssets/Squads/Monsters/Husks_SwarmLarge.Husks_SwarmLarge"),
    };
    constexpr std::array maximum_squad_sizes{4, 3, 5};
    constexpr std::size_t max_scene_husks = 128;

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

    Network network(U::UWorld* world)
    {
        if (!live(world) || world->GetPathName() != world_path) return {};
        auto* driver = object(world, STR("NetDriver"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* prop = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (!live(driver) || object(driver, STR("World")) != world || !prop || !exact(instance, instance_class)) return {};
        auto* server = get_object(prop, driver);
        auto* connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        if (!server) return {world, driver, instance, Seat::Host};
        if (!live(server) || !connection || !server->IsA(connection) || object(server, STR("Driver")) != driver) return {};
        return {world, driver, instance, Seat::Client};
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
        if (!exact(actor, spawner_class) || !in_level(actor, world) || byte_field(actor, STR("Role")) != 3) return false;
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

    bool scene_husks(U::UWorld* world, Actors& found)
    {
        auto* type = find<U::UClass>(npc_class);
        // The first stock call may still need to load the soft NPC class.
        if (!type) return true;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* actor, int, int) {
            if (actor->GetClassPrivate() == type && in_level(actor, world)) found.push_back(actor);
            return found.size() > max_scene_husks ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        return found.size() <= max_scene_husks;
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
                if (!bool_field(npc, STR("bReplicates"), true))
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
                return bool_field(npc, STR("bReplicates"), true) && bool_field(npc, STR("bReplicateMovement"), true);
            });
        }
    };

    struct Counts
    {
        int authority{}, simulated{}, other{}, simulated_controllers{}, antfarm_initialized{};
    };

    struct Observation
    {
        U::FWeakObjectPtr spawner, data;
        std::vector<U::FWeakObjectPtr> cohort;
        std::string_view stage{"awaiting_stock_cohort"};
        int replicated{};
    };

    struct Scope
    {
        U::FWeakObjectPtr world, driver;
        Seat seat{Seat::Inactive};
        std::int64_t natural_calls{}, receipts{}, spawn_denials{}, ai_denials{};
        Observation latest;
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

    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    bool operate{};

    void update_scope(const Network& active)
    {
        if (scope.world.Get() == active.world && scope.driver.Get() == active.driver && scope.seat == active.seat) return;
        scope = {};
        attempt = {};
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

    void on_pre(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready() || !receiver || !frame.Node() || call.OriginalFunctionCallPrevented()) return;
        static const U::FName spawn_name{STR("SpawnSquadFromData")};
        static const U::FName init_name{STR("InitializeAI")};
        static const U::FName perceive_name{STR("Proscess_TargetPerception")};
        const auto name = frame.Node()->GetFName();

        if (name == init_name && attempt.frame && frame.PreviousFrame() == attempt.frame &&
            frame.Node() == find<U::UFunction>(initialize_path))
        {
            // ForceWander Husk assets initialize each newly finished actor directly and skip the squad roster append.
            observe_birth(receiver, *attempt.frame);
            return;
        }

        if (operate && (name == init_name || name == perceive_name) && exact(receiver, npc_class) &&
            byte_field(receiver, STR("Role")) == 1 &&
            frame.Node() == find<U::UFunction>(name == init_name ? initialize_path : perceive_path))
        {
            const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld());
            if (active.seat == Seat::Client && in_level(receiver, active.world))
            {
                update_scope(active);
                call.PreventOriginalFunctionCall();
                if (call.OriginalFunctionCallPrevented()) ++scope.ai_denials;
            }
            return;
        }

        if (name != spawn_name || !exact(receiver, spawner_class) || attempt.frame) return;
        auto* caller = frame.PreviousFrame();
        if (!caller) return;
        const auto initial = caller->Node() == find<U::UFunction>(initial_path);
        const auto refresh = caller->Node() == find<U::UFunction>(refresh_path);
        if (!initial && !refresh) return;
        auto* director = caller->Object();
        const auto active = network(static_cast<U::AActor*>(receiver)->GetWorld());
        if (!active.world || !exact_director(director, active) || !placed_spawner(receiver, active.world) ||
            byte_field(receiver, STR("Usage")) != (initial ? 0 : 3)) return;
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

        if (active.seat == Seat::Client)
        {
            if (auto* output = operate ? schema.output_address(frame) : nullptr)
            {
                call.PreventOriginalFunctionCall();
                if (call.OriginalFunctionCallPrevented())
                {
                    schema.count->SetPropertyValue(output, 0);
                    ++scope.spawn_denials;
                }
            }
            return;
        }
        if (!local_pawn(director, active)) return;
        Actors before;
        if (!scene_husks(active.world, before)) return;
        Handles baseline;
        baseline.reserve(before.size());
        for (auto* existing : before) baseline.emplace_back(existing);
        attempt = {&frame, active.world, director, receiver, data, std::move(baseline), {}, schema,
            maximum_squad_sizes[static_cast<std::size_t>(source)], initial, {}};
    }

    void on_post(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!hook_ready() || attempt.frame != &frame || attempt.spawner.Get() != receiver) return;
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
        else if (!scene_husks(active.world, after))
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
            return exact(actor, npc_class) && in_level(actor, active.world) && byte_field(actor, STR("Faction")) == 6 &&
                byte_field(actor, STR("Role")) == 3;
        }))
        {
            next.stage = "stock_husk_authority_unverified";
        }
        else if (!std::ranges::all_of(after, [&](U::UObject* actor) {
            auto* controller = object(actor, STR("Controller"));
            return exact(controller, STR("/Game/Classes/NPCs/NPC_Assets/MasterNPC_AI_Controler_BP.MasterNPC_AI_Controler_BP_C")) &&
                static_cast<U::AActor*>(controller)->GetWorld() == active.world && object(controller, STR("Pawn")) == actor &&
                byte_field(controller, STR("Role")) == 3;
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
        auto* husk = find<U::UClass>(npc_class);
        auto* antfarm = find<U::UClass>(antfarm_class);
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object_ptr, int, int) {
            if (!live(object_ptr)) return RC::LoopAction::Continue;
            auto* type = object_ptr->GetClassPrivate();
            if (type == husk && in_level(object_ptr, active.world))
            {
                const auto role = byte_field(object_ptr, STR("Role"));
                if (role == 3) ++scope.counts.authority;
                else if (role == 1)
                {
                    ++scope.counts.simulated;
                    if (live(object(object_ptr, STR("Controller")))) ++scope.counts.simulated_controllers;
                }
                else ++scope.counts.other;
            }
            else if (type == antfarm && static_cast<U::AActor*>(object_ptr)->GetWorld() == active.world &&
                     object(object_ptr, STR("GameInstance")) == active.instance && live(object(object_ptr, STR("LoadedCell"))))
            {
                ++scope.counts.antfarm_initialized;
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
        if (!hook_ready() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2)) return 0;
        const auto expected = static_cast<std::uintptr_t>(lua.get_integer());
        const auto host = lua.get_bool();
        const auto active = network(find<U::UWorld>(world_path));
        auto* director = find<U::UObject>(director_path);
        if (active.seat != (host ? Seat::Host : Seat::Client) || !exact_director(director, active) ||
            !expected || reinterpret_cast<std::uintptr_t>(local_pawn(director, active)) != expected) return 0;
        update_scope(active);
        census(active);
        lua.set_integer(scope.receipts);
        lua.set_string(scope.latest.stage);
        push_name(lua, scope.latest.spawner.Get());
        push_name(lua, scope.latest.data.Get());
        lua.set_integer(static_cast<std::int64_t>(scope.latest.cohort.size()));
        lua.set_integer(scope.latest.replicated);
        lua.set_integer(scope.spawn_denials);
        lua.set_integer(scope.ai_denials);
        lua.set_integer(scope.natural_calls);
        lua.set_integer(scope.counts.authority);
        lua.set_integer(scope.counts.simulated);
        lua.set_integer(scope.counts.other);
        lua.set_integer(scope.counts.simulated_controllers);
        lua.set_integer(scope.counts.antfarm_initialized);
        std::string roster;
        for (const auto& pointer : scope.latest.cohort)
        {
            if (auto* actor = pointer.Get(); live(actor) && in_level(actor, active.world))
            {
                roster.append(RC::to_utf8_string(actor->GetFullName()));
                roster.push_back('\n');
            }
        }
        lua.set_string(roster);
        return 15;
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
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            on_pre, {false, false, STR("SilverfishCoopNative"), STR("Qualify natural CrossRoads Husk squads")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            on_post, {false, false, STR("SilverfishCoopNative"), STR("Observe natural CrossRoads Husk squads")});
        const bool ok = std::ranges::all_of(callbacks, [](auto callback) { return callback != Hook::ERROR_ID; });
        if (!ok) uninstall_hooks();
        installed.store(ok, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
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
        }
    }
}
