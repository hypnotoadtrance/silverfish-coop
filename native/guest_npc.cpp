#include "guest_npc.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>

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
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01");
    constexpr auto spawner_path = STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01:PersistentLevel.AI_Spawner_Single_BP_C_4");
    constexpr auto spawner_class = STR("/Game/Classes/ScriptingActors/AI_Spawner_Single_BP.AI_Spawner_Single_BP_C");
    constexpr auto spawn_power = STR("/Game/Classes/ScriptingActors/AI_Spawner_Single_BP.AI_Spawner_Single_BP_C:Send_PowerSignal");
    constexpr auto bandit_class = STR("/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C");
    constexpr auto ai_controller_class = STR("/Game/Classes/NPCs/NPC_Assets/MasterNPC_AI_Controler_BP.MasterNPC_AI_Controler_BP_C");
    constexpr auto ai_begin = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:ReceiveBeginPlay");
    constexpr auto ai_initialize = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:InitializeAI");
    constexpr auto ai_perception = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Proscess_TargetPerception");
    constexpr auto ai_relation = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:IdentifyRelation");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_damage = STR("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage");
    constexpr auto pawn_reputation = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Calc_Reputation");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto factions = STR("/Game/Classes/Libraries/Enums_Lib/Factions_Enum.Factions_Enum");
    constexpr auto relations = STR("/Game/Classes/Libraries/Enums_Lib/FactionRelations_Enum.FactionRelations_Enum");
    constexpr int simulated = 1;
    constexpr int authority = 3;
    constexpr int max_clients = 4;
    constexpr std::uint8_t freelancer = 8;
    constexpr std::uint8_t stranger = 12;
    constexpr std::uint8_t aggressive = 5;
    constexpr double spawn_radius_squared = 300.0 * 300.0;

    struct SpawnArgs
    {
        bool powered{};
        Unreal::UObject* instigator{};
    };

    struct Session
    {
        Unreal::UWorld* world{};
        Unreal::UObject* pawn{};
        Unreal::UObject* driver{};
        bool host{};
    };

    struct Participant
    {
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr save;
        int relation{-1};
        std::int64_t hits{};
        double health_lost{};
    };

    struct Scope
    {
        std::uint64_t generation{};
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr npc;
        Unreal::FWeakObjectPtr permitted_spawner;
        Unreal::FWeakObjectPtr permitted_pawn;
        bool attempted{};
        bool hostile{};
        std::array<Participant, 2> participants{};
        const Unreal::FFrame* damage_frame{};
        double damage_health_before{};
        std::int64_t spawner_denials{};
        std::int64_t replica_ai_denials{};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 3> hooks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Scope scope;

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_NPC_TEST") && env("SILVERFISH_COOP_HEADLESS"); }
    bool hostile_requested()
    {
        return env("SILVERFISH_COOP_NATIVE_NPC_HOSTILE") && env("SILVERFISH_COOP_NATIVE_FIXTURES");
    }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* member = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return member && member->GetArrayDim() == 1 ? member : nullptr;
    }

    template <typename T>
    T* input(Unreal::UFunction* function, const Unreal::TCHAR* name, std::size_t offset)
    {
        auto* member = field<T>(function, name);
        return member && member->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
            !member->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) &&
            member->GetOffset_Internal() == static_cast<int>(offset) ? member : nullptr;
    }

    Unreal::UObject* read_object(Unreal::FObjectProperty* member, const void* container)
    {
        return member && container ? member->GetObjectPropertyValue(member->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    Unreal::UObject* object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        return read_object(field<Unreal::FObjectProperty>(owner, name), owner);
    }

    bool flag(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* member = field<Unreal::FBoolProperty>(owner, name);
        return member && member->GetPropertyValueInContainer(owner);
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(Unreal::UObject* object, const Unreal::TCHAR* class_path)
    {
        return live(object) && object->GetClassPrivate() == find<Unreal::UClass>(class_path);
    }

    int role(Unreal::UObject* actor, const Unreal::TCHAR* name = STR("Role"))
    {
        auto* member = field<Unreal::FByteProperty>(actor, name);
        return member ? member->GetPropertyValueInContainer(actor) : -1;
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    Unreal::UWorld* actor_world(Unreal::UObject* actor)
    {
        return live(actor) ? static_cast<Unreal::AActor*>(actor)->GetWorld() : nullptr;
    }

    bool in_persistent_level(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        auto* level = object(world, STR("PersistentLevel"));
        return level && actor_world(actor) == world && static_cast<Unreal::AActor*>(actor)->GetLevel() == level;
    }

    Unreal::UObject* network_driver(Unreal::UWorld* world)
    {
        auto* driver = object(world, STR("NetDriver"));
        return live(driver) && world == object(driver, STR("World")) &&
            field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) ? driver : nullptr;
    }

    Unreal::UObject* sole_local_player(Unreal::UObject* instance)
    {
        auto* members = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* member = members ? Unreal::CastField<Unreal::FObjectProperty>(members->GetInner()) : nullptr;
        if (!member) return nullptr;
        Unreal::FScriptArrayHelper players(members, members->ContainerPtrToValuePtr<void>(instance));
        return players.Num() == 1 ? member->GetObjectPropertyValue(players.GetRawPtr()) : nullptr;
    }

    Session session(Address local_pawn)
    {
        Session result;
        auto* world = find<Unreal::UWorld>(world_path);
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* local_player = sole_local_player(instance);
        auto* controller = object(local_player, STR("PlayerController"));
        auto* pawn = object(controller, STR("Pawn"));
        auto* driver = network_driver(world);
        if (!local_pawn || !live(world) || !exact(instance, instance_class) || !live(local_player) || !driver ||
            !exact(controller, controller_class) || !exact(pawn, pawn_class) || address(pawn) != local_pawn ||
            object(pawn, STR("Controller")) != controller || object(controller, STR("Player")) != local_player ||
            actor_world(pawn) != world || actor_world(controller) != world) return result;

        auto* server = object(driver, STR("ServerConnection"));
        const bool host = !server;
        if (host ? role(pawn) != authority || role(controller) != authority : !live(server)) return result;
        return {world, pawn, driver, host};
    }

    Unreal::UObject* watched_pawn(const Session& current, Address expected)
    {
        if (!current.host) return nullptr;
        auto* members = field<Unreal::FArrayProperty>(current.driver, STR("ClientConnections"));
        auto* member = members ? Unreal::CastField<Unreal::FObjectProperty>(members->GetInner()) : nullptr;
        if (!member) return nullptr;
        Unreal::FScriptArrayHelper clients(members, members->ContainerPtrToValuePtr<void>(current.driver));
        if (clients.Num() < 1 || clients.Num() > max_clients || (!expected && clients.Num() != 1)) return nullptr;
        Unreal::UObject* match{};
        for (int index = 0; index < clients.Num(); ++index)
        {
            auto* connection = member->GetObjectPropertyValue(clients.GetRawPtr(index));
            auto* controller = object(connection, STR("PlayerController"));
            auto* pawn = object(controller, STR("Pawn"));
            if (expected && address(pawn) != expected) continue;
            if (match || !live(connection) || !exact(controller, controller_class) || !exact(pawn, pawn_class) ||
                role(pawn) != authority || actor_world(pawn) != current.world ||
                object(pawn, STR("Controller")) != controller || object(controller, STR("Player")) != connection) return nullptr;
            match = pawn;
        }
        return match;
    }

    bool is_stock_stranger(Unreal::UObject* pawn, Unreal::UObject* instance)
    {
        auto* disguise = field<Unreal::FByteProperty>(pawn, STR("Disguise"));
        auto* query = find<Unreal::UFunction>(pawn_reputation);
        auto* asking = input<Unreal::FByteProperty>(query, STR("AskingFaction"), 0);
        auto* output = field<Unreal::FByteProperty>(query, STR("Reputation"));
        if (!disguise || disguise->GetPropertyValueInContainer(pawn) != 0 || object(pawn, STR("As Game Instance FP")) != instance ||
            !asking || !output || output->GetOffset_Internal() != 1 ||
            !output->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm) || query->GetNumParms() != 3 ||
            query->GetParmsSize() != 3 || query->GetReturnProperty()) return false;
        std::array<std::uint8_t, 3> parameters{freelancer};
        pawn->ProcessEvent(query, parameters.data());
        return output->GetPropertyValueInContainer(parameters.data()) == stranger;
    }

    bool bind_participants(const Session& current, std::array<Participant, 2>& parties)
    {
        auto* local = current.pawn;
        auto* guest = watched_pawn(current, 0);
        auto* instance = object(current.world, STR("OwningGameInstance"));
        auto* stock = object(instance, STR("SaveSlot_Ref"));
        auto* detached = object(guest, STR("Save"));
        if (!guest || !exact(stock, save_class) || !exact(detached, save_class) || detached == stock ||
            object(local, STR("Save")) != stock || !is_stock_stranger(local, instance) || !is_stock_stranger(guest, instance)) return false;
        parties[0].pawn = local;
        parties[0].controller = object(local, STR("Controller"));
        parties[0].save = stock;
        parties[1].pawn = guest;
        parties[1].controller = object(guest, STR("Controller"));
        parties[1].save = detached;
        return true;
    }

    Participant* participant(Unreal::UObject* actor)
    {
        if (!scope.hostile || scope.generation != generation.load(std::memory_order_acquire) || !actor) return nullptr;
        for (auto& member : scope.participants)
        {
            if (member.pawn.Get() != actor) continue;
            auto* controller = member.controller.Get();
            auto* save = member.save.Get();
            if (!controller || !save || object(actor, STR("Controller")) != controller || object(controller, STR("Pawn")) != actor ||
                object(actor, STR("Save")) != save || role(actor) != authority || actor_world(actor) != scope.world.Get()) return nullptr;
            return &member;
        }
        return nullptr;
    }

    Scope& for_world(Unreal::UWorld* world)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation != epoch || scope.world.Get() != world)
        {
            scope = Scope{};
            scope.generation = epoch;
            scope.world = world;
        }
        return scope;
    }

    Unreal::UObject* placed_spawner(Unreal::UWorld* world)
    {
        auto* source = find<Unreal::UObject>(spawner_path);
        auto* bandit = find<Unreal::UClass>(bandit_class);
        auto* arrow = object(source, STR("Arrow"));
        return exact(source, spawner_class) && bandit && in_persistent_level(source, world) &&
            object(source, STR("AI_Class")) == bandit && live(arrow) && arrow->GetOuterPrivate() == source ? source : nullptr;
    }

    bool near_spawner(Unreal::UObject* actor, Unreal::UObject* source)
    {
        auto location = static_cast<Unreal::AActor*>(actor)->K2_GetActorLocation();
        auto birth = static_cast<Unreal::AActor*>(source)->K2_GetActorLocation();
        const auto dx = location.GetX() - birth.GetX();
        const auto dy = location.GetY() - birth.GetY();
        const auto dz = location.GetZ() - birth.GetZ();
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(squared) && squared <= spawn_radius_squared;
    }

    struct Bandits
    {
        int authority_count{};
        int simulated_count{};
        int other_count{};
        Unreal::UObject* last{};
    };

    Bandits bandits_in(Unreal::UWorld* world)
    {
        Bandits counts;
        auto* type = find<Unreal::UClass>(bandit_class);
        if (!type) return counts;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* actor, int, int) {
            if (actor->GetClassPrivate() != type || !in_persistent_level(actor, world) ||
                flag(actor, STR("bActorIsBeingDestroyed"))) return RC::LoopAction::Continue;
            const auto network_role = role(actor);
            if (network_role == authority) ++counts.authority_count;
            else if (network_role == simulated) ++counts.simulated_count;
            else ++counts.other_count;
            counts.last = actor;
            return RC::LoopAction::Continue;
        });
        return counts;
    }

    void intercept(Iteration& iteration, Unreal::UObject* receiver, Unreal::UFunction* function, const void* args)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) ||
            !receiver || !function || iteration.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName spawner_name{STR("AI_Spawner_Single_BP_C")};
        static const Unreal::FName bandit_name{STR("NPC_Bandit_C")};
        static const Unreal::FName power_name{STR("Send_PowerSignal")};
        static const Unreal::FName begin_name{STR("ReceiveBeginPlay")};
        static const Unreal::FName init_name{STR("InitializeAI")};
        static const Unreal::FName perceive_name{STR("Proscess_TargetPerception")};
        const auto function_name = function->GetFName();
        auto* type = receiver->GetClassPrivate();
        if (!type) return;
        const auto receiver_type = type->GetFName();
        if (receiver_type == spawner_name && function_name == power_name && receiver->GetPathName() == spawner_path &&
            function == find<Unreal::UFunction>(spawn_power))
        {
            auto* world = actor_world(receiver);
            if (!world || world->GetPathName() != world_path) return;
            auto& current = for_world(world);
            auto* powered = input<Unreal::FBoolProperty>(function, STR("Powered"), offsetof(SpawnArgs, powered));
            auto* instigator = input<Unreal::FObjectProperty>(function, STR("Instigator"), offsetof(SpawnArgs, instigator));
            if (current.permitted_spawner.Get() == receiver && current.permitted_pawn.Get() && powered && instigator && args &&
                powered->GetPropertyValueInContainer(args) && read_object(instigator, args) == current.permitted_pawn.Get()) return;
            ++current.spawner_denials;
            iteration.PreventOriginalFunctionCall();
            return;
        }

        if (receiver_type != bandit_name || (function_name != begin_name && function_name != init_name &&
            function_name != perceive_name) || !exact(receiver, bandit_class) || role(receiver) != simulated) return;
        auto* world = actor_world(receiver);
        if (!world || world->GetPathName() != world_path || !in_persistent_level(receiver, world) ||
            !live(object(network_driver(world), STR("ServerConnection")))) return;
        const auto function_path = function_name == begin_name ? ai_begin : function_name == init_name ? ai_initialize : ai_perception;
        if (function != find<Unreal::UFunction>(function_path)) return;

        auto& current = for_world(world);
        if (!current.attempted)
        {
            auto* source = placed_spawner(world);
            if (!source || !near_spawner(receiver, source)) return;
            current.npc = receiver;
            current.attempted = true;
        }
        if (current.npc.Get() == receiver && function_name != begin_name)
        {
            ++current.replica_ai_denials;
            iteration.PreventOriginalFunctionCall();
        }
    }

    bool arm_stock_hostility(Unreal::UObject* npc)
    {
        auto* faction = field<Unreal::FByteProperty>(npc, STR("Faction"));
        auto* responses = field<Unreal::FMapProperty>(npc, STR("FactionResponce_Override"));
        auto* key = responses ? Unreal::CastField<Unreal::FByteProperty>(responses->GetKeyProp()) : nullptr;
        auto* value = responses ? Unreal::CastField<Unreal::FByteProperty>(responses->GetValueProp()) : nullptr;
        if (!faction || faction->GetPropertyValueInContainer(npc) != freelancer || !key || !value ||
            !key->GetEnum().Get() || key->GetEnum().Get()->GetPathName() != factions ||
            !value->GetEnum().Get() || value->GetEnum().Get()->GetPathName() != relations ||
            key->GetSize() != sizeof(stranger) || value->GetSize() != sizeof(aggressive)) return false;
        auto* map = responses->ContainerPtrToValuePtr<Unreal::FScriptMap>(npc);
        if (map->Num() > 16) return false;

        const auto layout = Unreal::FScriptMap::GetScriptLayout(1, 1, 1, 1);
        const auto hash = [key](const void* entry) { return key->GetValueTypeHash(entry); };
        const auto equal = [key](const void* left, const void* right) { return key->Identical(left, right); };
        if (auto* existing = map->FindValue(&stranger, layout, hash, equal)) return *existing == aggressive;

        // Add one ordinary stock faction response to this spawned actor's map. No player state or target is written.
        const auto before = map->Num();
        map->FindOrAdd(&stranger, layout, hash, equal, [](void* target_key, void* target_value) {
            *static_cast<std::uint8_t*>(target_key) = stranger;
            *static_cast<std::uint8_t*>(target_value) = aggressive;
        });
        auto* current = map->FindValue(&stranger, layout, hash, equal);
        return map->Num() == before + 1 && current && *current == aggressive;
    }

    void observe_damage_pre(Unreal::UObject* receiver, Unreal::FFrame& frame)
    {
        if (!Unreal::IsInGameThreadRaw() || !scope.hostile || scope.damage_frame) return;
        static const Unreal::FName deal_damage{STR("DealDamage")};
        if (!frame.Node() || frame.Node()->GetFName() != deal_damage || frame.Node() != find<Unreal::UFunction>(pawn_damage)) return;
        auto* member = participant(receiver);
        auto* instigator = input<Unreal::FObjectProperty>(frame.Node(), STR("Instigator"), 0x150);
        auto* bandit = scope.npc.Get();
        if (!member || !bandit || read_object(instigator, frame.Locals()) != bandit ||
            actor_world(bandit) != scope.world.Get() || role(bandit) != authority) return;
        auto* save = member->save.Get();
        auto* health = field<Unreal::FDoubleProperty>(save, STR("PlayerHealth"));
        const auto before = health ? health->GetPropertyValueInContainer(save) : -1.0;
        if (std::isfinite(before) && before > 0.0)
        {
            scope.damage_frame = &frame;
            scope.damage_health_before = before;
        }
    }

    void observe_stock_combat(Iteration& iteration, Unreal::UObject* receiver, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || !scope.hostile) return;
        if (scope.damage_frame == &frame)
        {
            scope.damage_frame = nullptr;
            auto* member = participant(receiver);
            if (!member || iteration.OriginalFunctionCallPrevented()) return;
            auto* save = member->save.Get();
            auto* health = field<Unreal::FDoubleProperty>(save, STR("PlayerHealth"));
            const auto after = health ? health->GetPropertyValueInContainer(save) : -1.0;
            if (std::isfinite(after) && after >= 0.0 && after < scope.damage_health_before)
            {
                ++member->hits;
                member->health_lost += scope.damage_health_before - after;
            }
            return;
        }
        if (receiver != scope.npc.Get() || frame.Node() != find<Unreal::UFunction>(ai_relation) ||
            iteration.OriginalFunctionCallPrevented()) return;
        auto* enemy = input<Unreal::FObjectProperty>(frame.Node(), STR("Enemy"), 0);
        auto* member = participant(read_object(enemy, frame.Locals()));
        auto* relation = field<Unreal::FByteProperty>(frame.Node(), STR("Relationship"));
        if (!member || !relation || !relation->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm) ||
            relation->GetOffset_Internal() != 9 || relation->HasAnyPropertyFlags(Unreal::CPF_ReturnParm)) return;
        auto* output = frame.OutParms();
        for (int index = 0; output && index < 4; ++index, output = output->NextOutParm)
        {
            if (output->Property != relation || !output->PropAddr) continue;
            const auto observed = relation->GetPropertyValue(output->PropAddr);
            if (observed <= 6) member->relation = observed;
            return;
        }
    }

    struct Replication
    {
        Unreal::UFunction* replicate{};
        Unreal::UFunction* movement{};
        Unreal::UFunction* update{};
        Unreal::FBoolProperty* replicate_value{};
        Unreal::FBoolProperty* movement_value{};

        bool read()
        {
            replicate = find<Unreal::UFunction>(STR("/Script/Engine.Actor:SetReplicates"));
            movement = find<Unreal::UFunction>(STR("/Script/Engine.Actor:SetReplicateMovement"));
            update = find<Unreal::UFunction>(STR("/Script/Engine.Actor:ForceNetUpdate"));
            replicate_value = bool_argument(replicate, STR("bInReplicates"));
            movement_value = bool_argument(movement, STR("bInReplicateMovement"));
            return replicate_value && movement_value && native_void(update, 0, 0);
        }

        void apply(Unreal::UObject* npc) const
        {
            unsigned char parameters{};
            if (!flag(npc, STR("bReplicates")))
            {
                replicate_value->SetPropertyValueInContainer(&parameters, true);
                npc->ProcessEvent(replicate, &parameters);
            }
            if (!flag(npc, STR("bReplicateMovement")))
            {
                movement_value->SetPropertyValueInContainer(&parameters, true);
                npc->ProcessEvent(movement, &parameters);
            }
            npc->ProcessEvent(update, nullptr);
        }

    private:
        static bool native_void(Unreal::UFunction* function, int count, int size)
        {
            return function && function->HasAnyFunctionFlags(Unreal::FUNC_Native) && function->GetNumParms() == count &&
                function->GetParmsSize() == size && !function->GetReturnProperty();
        }

        static Unreal::FBoolProperty* bool_argument(Unreal::UFunction* function, const Unreal::TCHAR* name)
        {
            return native_void(function, 1, 1) ? input<Unreal::FBoolProperty>(function, name, 0) : nullptr;
        }
    };

    void lua_name(const Lua& lua, Unreal::UObject* actor)
    {
        if (live(actor)) lua.set_string(RC::to_utf8_string(actor->GetFullName()));
        else lua.set_string("");
    }

    int spawn_result(const Lua& lua, bool ok, std::string_view status, Unreal::UObject* npc = nullptr)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        lua_name(lua, npc);
        return 3;
    }

    int spawn_bandit(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) ||
            !env("SILVERFISH_COOP_NATIVE_FIXTURES") || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_integer(2)) return spawn_result(lua, false, "bandit_fixture_arguments_required");
        const auto pawn_address = lua.get_integer();
        const auto source_address = lua.get_integer();
        if (pawn_address <= 0 || source_address <= 0) return spawn_result(lua, false, "bandit_fixture_arguments_required");
        const auto current = session(static_cast<Address>(pawn_address));
        if (!current.world || !current.host) return spawn_result(lua, false, "authoritative_playtest_host_required");
        auto* source = placed_spawner(current.world);
        if (!source || address(source) != static_cast<Address>(source_address) || role(source) != authority)
            return spawn_result(lua, false, "exact_bandit_spawner_required");

        auto& staged = for_world(current.world);
        if (staged.attempted) return spawn_result(lua, false, "bandit_fixture_already_attempted", staged.npc.Get());
        if (bandits_in(current.world).last) return spawn_result(lua, false, "bandit_fixture_requires_empty_baseline");
        const bool hostile = hostile_requested();
        if (hostile && !bind_participants(current, staged.participants))
            return spawn_result(lua, false, "bandit_hostility_requires_nondisguised_stock_stranger_pair");

        auto* power = find<Unreal::UFunction>(spawn_power);
        auto* powered = input<Unreal::FBoolProperty>(power, STR("Powered"), offsetof(SpawnArgs, powered));
        auto* instigator = input<Unreal::FObjectProperty>(power, STR("Instigator"), offsetof(SpawnArgs, instigator));
        auto* type = find<Unreal::UClass>(bandit_class);
        Replication replication;
        if (!powered || !instigator || power->GetNumParms() != 2 || power->GetParmsSize() != sizeof(SpawnArgs) ||
            power->GetReturnProperty() || power->GetOuterPrivate() != source->GetClassPrivate() || !type ||
            !field<Unreal::FBoolProperty>(type, STR("bReplicates")) || !field<Unreal::FBoolProperty>(type, STR("bReplicateMovement")) ||
            !replication.read()) return spawn_result(lua, false, "stock_bandit_spawner_schema_unavailable");

        SpawnArgs args{};
        powered->SetPropertyValueInContainer(&args, true);
        instigator->SetObjectPropertyValue(instigator->ContainerPtrToValuePtr<void>(&args), current.pawn);
        staged.attempted = true;
        staged.permitted_spawner = source;
        staged.permitted_pawn = current.pawn;
        source->ProcessEvent(power, &args);
        staged.permitted_spawner.Reset();
        staged.permitted_pawn.Reset();

        const auto actors = bandits_in(current.world);
        auto* npc = actors.last;
        const auto after = session(static_cast<Address>(pawn_address));
        if (after.world != current.world || after.pawn != current.pawn || after.driver != current.driver || !after.host ||
            !npc || actors.authority_count != 1 || actors.simulated_count || actors.other_count || !near_spawner(npc, source))
            return spawn_result(lua, false, "mutation_unverified_bandit_spawn");

        staged.npc = npc;
        if (hostile)
        {
            if (!arm_stock_hostility(npc)) return spawn_result(lua, false, "mutation_unverified_bandit_hostility");
            staged.hostile = true;
        }
        replication.apply(npc);
        if (staged.npc.Get() != npc || !exact(npc, bandit_class) || role(npc) != authority ||
            !flag(npc, STR("bReplicates")) || !flag(npc, STR("bReplicateMovement")))
            return spawn_result(lua, false, "mutation_unverified_bandit_replication");
        return spawn_result(lua, true, "stock_bandit_spawned", npc);
    }

    struct Perception
    {
        bool available{};
        bool local{};
        bool watched{};
    };

    Perception read_sight(Unreal::UObject* bandit, Unreal::UObject* local, Unreal::UObject* watched)
    {
        Perception result;
        auto* receiver = object(bandit, STR("AIPerception"));
        auto* sight = find<Unreal::UClass>(STR("/Script/AIModule.AISense_Sight"));
        auto* query = find<Unreal::UFunction>(STR("/Script/AIModule.AIPerceptionComponent:GetCurrentlyPerceivedActors"));
        auto* sense = input<Unreal::FClassProperty>(query, STR("SenseToUse"), 0);
        auto* out = field<Unreal::FArrayProperty>(query, STR("OutActors"));
        auto* element = out ? Unreal::CastField<Unreal::FObjectProperty>(out->GetInner()) : nullptr;
        constexpr std::size_t capacity = 64;
        if (!exact(receiver, STR("/Script/AIModule.AIPerceptionComponent")) || !sight || !sense || !out || !element ||
            !query->HasAnyFunctionFlags(Unreal::FUNC_Native) || query->GetNumParms() != 2 || query->GetReturnProperty() ||
            !out->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm) || out->GetOffset_Internal() != sizeof(void*) ||
            out->GetOffset_Internal() + out->GetSize() > query->GetParmsSize() || query->GetParmsSize() > capacity)
            return result;

        alignas(std::max_align_t) std::array<std::byte, capacity> parameters{};
        auto* buffer = parameters.data();
        out->InitializeValue_InContainer(buffer);
        sense->SetObjectPropertyValue(sense->ContainerPtrToValuePtr<void>(buffer), sight);
        receiver->ProcessEvent(query, buffer);
        Unreal::FScriptArrayHelper sensed(out, out->ContainerPtrToValuePtr<void>(buffer));
        result.available = sensed.Num() <= 512;
        for (int index = 0; result.available && index < sensed.Num(); ++index)
        {
            auto* actor = element->GetObjectPropertyValue(sensed.GetRawPtr(index));
            result.local = result.local || actor == local;
            result.watched = result.watched || (watched && actor == watched);
        }
        out->DestroyValue_InContainer(buffer);
        return result;
    }

    int read_bandit(const Lua& lua)
    {
        Session current;
        Unreal::UObject* watched{};
        std::string_view status{"playtest_bandit_scope_required"};
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 2 &&
            lua.is_integer(1) && lua.is_integer(2))
        {
            const auto local_address = lua.get_integer();
            const auto watched_address = lua.get_integer();
            if (local_address > 0 && watched_address >= 0) current = session(static_cast<Address>(local_address));
            if (current.world && watched_address > 0)
            {
                watched = watched_pawn(current, static_cast<Address>(watched_address));
                if (!watched)
                {
                    current = Session{};
                    status = "exact_watched_guest_required";
                }
            }
        }

        Bandits actors;
        auto* staged = current.world ? &for_world(current.world) : nullptr;
        auto* npc = staged ? staged->npc.Get() : nullptr;
        if (staged)
        {
            actors = bandits_in(current.world);
            if (actors.other_count || actors.authority_count > (current.host ? 1 : 0) ||
                actors.simulated_count > (current.host ? 0 : 1)) status = "duplicate_or_wrong_role_bandit";
            else if (actors.last && actors.last != npc) status = "unmanaged_bandit";
            else if (npc && !actors.last) status = "bandit_no_longer_live";
            else if (npc) status = current.host ? "stock_bandit_authority" : "stock_bandit_replica";
            else status = staged->attempted ? "bandit_no_longer_live" : "waiting_for_stock_bandit";
        }

        auto* controller = object(npc, STR("Controller"));
        if (current.host && npc && (!exact(controller, ai_controller_class) ||
            object(controller, STR("Pawn")) != npc || role(controller) != authority)) status = "bandit_stock_controller_unverified";
        auto* type = current.world ? find<Unreal::UClass>(bandit_class) : nullptr;
        auto* defaults = type ? type->GetClassDefaultObject().Get() : nullptr;
        std::array<double, 3> position{};
        if (npc)
        {
            auto location = static_cast<Unreal::AActor*>(npc)->K2_GetActorLocation();
            position = {location.GetX(), location.GetY(), location.GetZ()};
        }
        const auto perception = current.host && npc ? read_sight(npc, current.pawn, watched) : Perception{};

        lua.set_bool(status == "stock_bandit_authority" || status == "stock_bandit_replica" || status == "waiting_for_stock_bandit");
        lua.set_string(status);
        lua.set_integer(actors.authority_count);
        lua.set_integer(actors.simulated_count);
        lua_name(lua, npc);
        lua.set_integer(role(npc));
        lua.set_integer(role(npc, STR("RemoteRole")));
        lua.set_bool(flag(npc, STR("bReplicates")));
        lua.set_bool(flag(npc, STR("bReplicateMovement")));
        lua_name(lua, controller);
        lua.set_bool(flag(npc, STR("Initialized")));
        lua.set_bool(flag(npc, STR("Alive")));
        lua_name(lua, object(npc, STR("CombatTarget")));
        lua_name(lua, object(npc, STR("SpottedActor")));
        lua.set_bool(perception.available);
        lua.set_bool(perception.local);
        lua.set_bool(perception.watched);
        for (const auto coordinate : position) lua.set_number(coordinate);
        lua.set_bool(flag(defaults, STR("bReplicates")));
        lua.set_bool(flag(defaults, STR("bReplicateMovement")));
        lua.set_integer(staged ? staged->spawner_denials : 0);
        lua.set_integer(staged ? staged->replica_ai_denials : 0);
        return 24;
    }

    int read_combat(const Lua& lua)
    {
        const Participant empty;
        std::string_view status{"hostile_bandit_scope_required"};
        const Participant* local = &empty;
        const Participant* guest = &empty;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 2 &&
            lua.is_integer(1) && lua.is_integer(2))
        {
            const auto local_address = lua.get_integer();
            const auto guest_address = lua.get_integer();
            const auto current = local_address > 0 ? session(static_cast<Address>(local_address)) : Session{};
            if (current.host && current.world == scope.world.Get() && !scope.hostile)
                status = "hostile_bandit_waiting";
            if (current.host && current.world == scope.world.Get() && guest_address > 0)
            {
                auto* remote = watched_pawn(current, static_cast<Address>(guest_address));
                auto* first = participant(current.pawn);
                auto* second = participant(remote);
                if (first == &scope.participants[0] && second == &scope.participants[1] && scope.npc.Get())
                {
                    local = first;
                    guest = second;
                    status = "stock_stranger_aggressive";
                }
            }
        }
        lua.set_bool(status == "stock_stranger_aggressive");
        lua.set_string(status);
        lua.set_integer(local->relation);
        lua.set_integer(guest->relation);
        lua.set_integer(local->hits);
        lua.set_integer(guest->hits);
        lua.set_number(local->health_lost);
        lua.set_number(guest->health_lost);
        return 8;
    }
}

namespace silverfish::native_guest_npc
{
    void install_hooks()
    {
        if (!enabled() || hooks[0] != Hook::ERROR_ID) return;
        hooks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::UFunction* function, void* args) {
                intercept(iteration, object, function, args);
            }, {false, false, owner, STR("Stock Playtest Bandit event scope")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*) {
                intercept(iteration, object, frame.Node(), frame.Locals());
                if (!iteration.OriginalFunctionCallPrevented()) observe_damage_pre(object, frame);
            }, {false, false, owner, STR("Stock Playtest Bandit script scope")});
        const bool hostile = hostile_requested();
        if (hostile)
            hooks[2] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
                observe_stock_combat, {false, false, owner, STR("Observe stock Bandit targeting and damage")});
        const bool ready = hooks[0] != Hook::ERROR_ID && hooks[1] != Hook::ERROR_ID && (!hostile || hooks[2] != Hook::ERROR_ID);
        if (!ready) uninstall_hooks();
        installed.store(ready, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& id : hooks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeBandit", read_bandit);
        if (env("SILVERFISH_COOP_NATIVE_FIXTURES")) lua.register_function("SilverfishSpawnNativeBandit", spawn_bandit);
        if (hostile_requested()) lua.register_function("SilverfishReadNativeBanditCombat", read_combat);
    }

    void clear_scope() { generation.fetch_add(1, std::memory_order_acq_rel); }
}
