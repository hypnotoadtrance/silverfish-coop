#include "lenny_quest.hpp"
#include "shared_loot.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <string>
#include <string_view>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = H::TCallbackIterationData<void>;
    using Address = std::uintptr_t;
    using Clock = std::chrono::steady_clock;

    struct Profile
    {
        const U::TCHAR* world_package;
        const U::TCHAR* world;
        const U::TCHAR* spawner;
        const U::TCHAR* target;
        const U::TCHAR* quest;
        const U::TCHAR* death_flag;
        std::string_view unverified_death;
    };
    constexpr Profile lenny{
        STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.Quest_AssassinTargetSpawn_BP_C_1"),
        STR("/Game/Classes/NPCs/NPC_Characters/NPC_Lenny.NPC_Lenny_C"),
        STR("Asn_Lenny_01"), STR("LennyDead"), "mutation_unverified_stock_lenny_death",
    };
    constexpr Profile spratt{
        STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P"),
        STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P"),
        STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.Quest_AssassinTargetSpawn_BP_C_0"),
        STR("/Game/Classes/NPCs/NPC_Characters/NPC_Spratt.NPC_Spratt_C"),
        STR("Asn_Spratt"), nullptr, "mutation_unverified_stock_spratt_death",
    };
    constexpr std::array profiles{&lenny, &spratt};
    constexpr auto spawner_type = STR("/Game/Classes/ScriptingActors/Quest_AssassinTargetSpawn_BP.Quest_AssassinTargetSpawn_BP_C");
    constexpr auto pawn_type = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_type = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto npc_type = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto projectile_type = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto instance_type = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_type = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto table_path = STR("/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT");
    constexpr auto quest_type = STR("/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct");
    constexpr auto quest_step = STR("Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A");
    constexpr int map_limit = 8192;
    constexpr int absent = (std::numeric_limits<int>::min)();
    constexpr auto lease = std::chrono::milliseconds(2500);

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* target, const U::TCHAR* name)
    {
        auto* property = target ? U::CastField<T>(target->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* target, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(target, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(target)) : nullptr;
    }

    template <typename T, typename Value>
    bool equals(U::UObject* target, const U::TCHAR* name, Value expected)
    {
        auto* property = field<T>(target, name);
        return property && property->GetPropertyValueInContainer(target) == expected;
    }

    bool live(U::UObject* target)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return target && !target->HasAnyFlags(excluded) && !target->IsUnreachable();
    }

    bool exact(U::UObject* target, const U::TCHAR* type)
    {
        return live(target) && target->GetClassPrivate() == find<U::UClass>(type);
    }

    bool authority(U::UObject* target, U::UObject* world)
    {
        return live(target) && equals<U::FByteProperty>(target, STR("Role"), 3) &&
            equals<U::FBoolProperty>(target, STR("bActorIsBeingDestroyed"), false) &&
            static_cast<U::AActor*>(target)->GetWorld() == world;
    }

    template <typename K, typename V, typename Key, typename Fn>
    bool map_member(U::UObject* receiver, const U::TCHAR* name, const Key& wanted, Fn&& use)
    {
        auto* property = field<U::FMapProperty>(receiver, name);
        auto* key = property ? U::CastField<K>(property->GetKeyProp()) : nullptr;
        auto* value = property ? U::CastField<V>(property->GetValueProp()) : nullptr;
        if (!key || !value || key->GetOffset_Internal() != 0) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(),
                                                          value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        const auto* map = property->ContainerPtrToValuePtr<U::FScriptMap>(receiver);
        if (map->Num() < 0 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > map_limit) return false;
        const void* found = nullptr;
        for (int index = 0; index < map->GetMaxIndex(); ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = map->GetData(index, layout);
            if (!entry) return false;
            if (key->GetPropertyValue(entry) != wanted) continue;
            if (found) return false;
            found = value->template ContainerPtrToValuePtr<void>(entry);
        }
        return use(value, found, map->Num());
    }

    struct Network
    {
        U::UWorld* world{};
        U::UObject *instance{}, *driver{};
        bool client{};
    };
    struct Pending
    {
        U::FWeakObjectPtr driver{}, connection{};
        const U::FWorldContext* context{};
        const Profile* profile{};
    } pending;
    struct Guest { Address pawn{}, save{}; };
    struct Progress { int step{absent}, flag{}, cash{}, xp{}; };
    struct Attribution
    {
        U::FWeakObjectPtr pawn{}, projectile{};
        std::string_view role{"-"};
        std::string name{}, shot{};
    };
    struct Scope
    {
        const Profile* profile{};
        U::FWeakObjectPtr world{}, instance{}, driver{}, host{}, save{}, spawner{}, target{};
        U::UFunction *deal{}, *resolve{}, *calc{}, *target_killed{};
        U::FObjectProperty *instigator{}, *event_target{};
        U::FDoubleProperty* health{};
        U::UScriptStruct* quest_struct{};
        U::FIntProperty* step{};
        U::FName quest{}, flag{}, cash{}, xp{};
        std::array<Guest, 4> guests{};
        Clock::time_point until{};
        U::FFrame *damage_frame{}, *death_frame{};
        Attribution current_shot{}, lethal{}, shooter{};
        Progress before{}, after{};
        std::string actor{};
        std::string_view status{"watching"};
        int target_killed_calls{};
        bool observed{}, replicated{};
    } scope;

    std::array callbacks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};
    std::atomic<bool> installed{};

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == 2 && value[0] == '1';
    }

    bool watch_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_EVENTS") &&
            env("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }
    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool server_connection(U::UObject* connection, U::UObject* driver)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(driver) && live(connection) && type && connection->IsA(type) && object(connection, STR("Driver")) == driver;
    }

    Network network(U::UWorld* world, const Profile& profile, bool allow_pending = false)
    {
        if (!live(world) || world->GetPathName() != profile.world) return {};
        auto* instance = object(world, STR("OwningGameInstance"));
        if (!exact(instance, instance_type)) return {};
        auto* driver = object(world, STR("NetDriver"));
        if (live(driver) && object(driver, STR("World")) == world && field<U::FObjectProperty>(driver, STR("ServerConnection")))
        {
            auto* server = object(driver, STR("ServerConnection"));
            if (server_connection(server, driver)) return {world, instance, driver, true};
            if (server) return {};
            if (!allow_pending || driver != pending.driver.Get()) return {world, instance, driver, false};
        }
        if (!allow_pending || !pending.context || pending.profile != &profile) return {};
        auto* pending_driver = pending.driver.Get();
        if ((driver && driver != pending_driver) || !server_connection(pending.connection.Get(), pending_driver)) return {};
        return {world, instance, pending_driver, true};
    }

    bool in_level(U::UObject* actor, U::UWorld* world)
    {
        return live(actor) && world && static_cast<U::AActor*>(actor)->GetWorld() == world &&
            static_cast<U::AActor*>(actor)->GetLevel() == object(world, STR("PersistentLevel"));
    }

    bool spawner_recipe(U::UObject* spawner, U::UWorld* world, const Profile& profile)
    {
        if (!exact(spawner, spawner_type) || spawner->GetPathName() != profile.spawner || !in_level(spawner, world)) return false;
        auto* class_property = field<U::FClassProperty>(spawner, STR("AI_Class"));
        auto* npc = find<U::UClass>(profile.target);
        auto* arrow = object(spawner, STR("Arrow"));
        auto* quest = field<U::FStructProperty>(spawner, STR("Quest"));
        auto* row_type = quest ? quest->GetStruct().Get() : nullptr;
        auto* row = field<U::FNameProperty>(row_type, STR("RowName"));
        auto* table = field<U::FObjectProperty>(row_type, STR("DataTable"));
        const auto row_data = quest ? quest->ContainerPtrToValuePtr<void>(spawner) : nullptr;
        const U::FName wanted{profile.quest, U::FNAME_Find};
        const auto dead = profile.death_flag ? U::FName{profile.death_flag, U::FNAME_Find} : U::FName{};
        if (&profile == &spratt)
        {
            const auto empty_array = [spawner](const U::TCHAR* name) {
                auto* property = field<U::FArrayProperty>(spawner, name);
                return property && U::FScriptArrayHelper(property, property->ContainerPtrToValuePtr<void>(spawner)).Num() == 0;
            };
            if (!empty_array(STR("ActorsToActivate_OnSpawn")) || !empty_array(STR("ActorsToActivate_OnKill")) ||
                !empty_array(STR("SquadMembers_Class")) ||
                !equals<U::FBoolProperty>(spawner, STR("Clear_NearAnomalies"), true) ||
                !equals<U::FBoolProperty>(spawner, STR("No Collision Fail"), true)) return false;
        }
        return class_property && npc && class_property->GetObjectPropertyValue(class_property->ContainerPtrToValuePtr<void>(spawner)) == npc &&
            exact(arrow, STR("/Script/Engine.ArrowComponent")) && arrow->GetOuterPrivate() == spawner &&
            row_data && row && table && !wanted.IsNone() && (!profile.death_flag || !dead.IsNone()) &&
            row->GetPropertyValueInContainer(row_data) == wanted &&
            table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(row_data)) == find<U::UObject>(table_path) &&
            equals<U::FIntProperty>(spawner, STR("SpawnOn_Step"), 1) &&
            equals<U::FIntProperty>(spawner, STR("AddStepsOnKill"), 1) &&
            equals<U::FBoolProperty>(spawner, STR("Spawn_BeforeQuest"), false) &&
            map_member<U::FNameProperty, U::FIntProperty>(spawner, STR("AddFlags_OnDeath"), dead,
                [&](auto* value, const void* data, int size) {
                    return profile.death_flag ? size == 1 && data && value->GetPropertyValue(data) == 1 : size == 0;
                });
    }

    U::UFunction* script(U::UClass* type, const U::TCHAR* name, int args)
    {
        auto* function = type ? type->GetFunctionByNameInChain(U::FName{name}) : nullptr;
        return function && function->GetOuterPrivate() == type && function->GetNumParms() == args &&
            !function->GetReturnProperty() && !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    bool current(const Scope& candidate)
    {
        if (!candidate.profile) return false;
        auto* world = static_cast<U::UWorld*>(candidate.world.Get());
        const auto net = network(world, *candidate.profile);
        auto* host = candidate.host.Get();
        auto* save = candidate.save.Get();
        return net.world && !net.client && net.instance == candidate.instance.Get() && net.driver == candidate.driver.Get() &&
            exact(host, pawn_type) && authority(host, world) && authority(candidate.spawner.Get(), world) && exact(save, save_type) &&
            object(net.instance, STR("As Player FP")) == host && object(net.instance, STR("SaveSlot_Ref")) == save &&
            object(host, STR("Save")) == save;
    }

    bool progress(const Scope& candidate, Progress& result)
    {
        auto* save = candidate.save.Get();
        const auto flag = [save](const U::FName& key, int& into) {
            return map_member<U::FNameProperty, U::FIntProperty>(save, STR("Flags"), key,
                [&](auto* value, const void* data, int) {
                    into = data ? value->GetPropertyValue(data) : 0;
                    return true;
                });
        };
        return map_member<U::FNameProperty, U::FStructProperty>(save, STR("ActiveQuests"), candidate.quest,
            [&](auto* property, const void* data, int) {
                if (property->GetStruct().Get() != candidate.quest_struct) return false;
                result.step = data ? candidate.step->GetPropertyValueInContainer(data) : absent;
                return !data || result.step != absent;
            }) && (candidate.flag.IsNone() || flag(candidate.flag, result.flag)) &&
            flag(candidate.cash, result.cash) && flag(candidate.xp, result.xp);
    }

    bool schema(Scope& next, U::UObject* target)
    {
        auto* base = find<U::UClass>(npc_type);
        auto* projectile = find<U::UClass>(projectile_type);
        next.deal = script(base, STR("DealDamage"), 7);
        next.resolve = script(base, STR("ResolveDeath"), 0);
        next.calc = script(projectile, STR("CalcDmg"), 1);
        next.instigator = field<U::FObjectProperty>(next.deal, STR("Instigator"));
        next.health = field<U::FDoubleProperty>(target, STR("Health"));
        next.quest_struct = find<U::UScriptStruct>(quest_type);
        next.step = field<U::FIntProperty>(next.quest_struct, quest_step);
        next.quest = U::FName{next.profile->quest, U::FNAME_Find};
        if (next.profile->death_flag) next.flag = U::FName{next.profile->death_flag, U::FNAME_Find};
        next.cash = U::FName{STR("Player_Cash"), U::FNAME_Find};
        next.xp = U::FName{STR("Player_Xp"), U::FNAME_Find};
        if (next.profile == &spratt)
        {
            next.target_killed = script(next.spawner.Get()->GetClassPrivate(), STR("Event_Target_Killed"), 1);
            next.event_target = field<U::FObjectProperty>(next.target_killed, STR("NPC_Ref"));
            if (!next.target_killed || next.target_killed->GetParmsSize() != sizeof(void*) || !next.event_target ||
                next.event_target->GetOffset_Internal() != 0 || next.event_target->GetPropertyClass().Get() != base ||
                !next.event_target->HasAnyPropertyFlags(U::CPF_Parm) ||
                next.event_target->HasAnyPropertyFlags(U::CPF_OutParm)) return false;
        }
        return base && target->IsA(base) && next.deal && next.resolve && next.calc && next.instigator && next.health &&
            next.step && !next.quest.IsNone() && (!next.profile->death_flag || !next.flag.IsNone()) &&
            !next.cash.IsNone() && !next.xp.IsNone() &&
            target->GetFunctionByNameInChain(U::FName{STR("ResolveDeath")}) == next.resolve &&
            target->GetFunctionByNameInChain(U::FName{STR("DealDamage")}) == next.deal &&
            next.instigator->HasAnyPropertyFlags(U::CPF_Parm) && !next.instigator->HasAnyPropertyFlags(U::CPF_OutParm) &&
            next.instigator->GetOffset_Internal() >= 0 &&
            next.instigator->GetOffset_Internal() + next.instigator->GetSize() <= next.deal->GetParmsSize();
    }

    bool publish(U::UObject* target)
    {
        auto* replication = find<U::UFunction>(STR("/Script/Engine.Actor:SetReplicates"));
        auto* movement = find<U::UFunction>(STR("/Script/Engine.Actor:SetReplicateMovement"));
        auto* update = find<U::UFunction>(STR("/Script/Engine.Actor:ForceNetUpdate"));
        const auto argument = [](U::UFunction* fn, const U::TCHAR* name) -> U::FBoolProperty* {
            if (!fn || !fn->HasAnyFunctionFlags(U::FUNC_Native) || fn->GetNumParms() != 1 ||
                fn->GetParmsSize() != 1 || fn->GetReturnProperty()) return nullptr;
            auto* field_value = field<U::FBoolProperty>(fn, name);
            return field_value && field_value->GetOffset_Internal() == 0 &&
                field_value->HasAnyPropertyFlags(U::CPF_Parm) && !field_value->HasAnyPropertyFlags(U::CPF_OutParm) ? field_value : nullptr;
        };
        auto* set_replication = argument(replication, STR("bInReplicates"));
        auto* set_movement = argument(movement, STR("bInReplicateMovement"));
        if (!set_replication || !set_movement || !update || !update->HasAnyFunctionFlags(U::FUNC_Native) ||
            update->GetNumParms() || update->GetParmsSize() || update->GetReturnProperty() ||
            !field<U::FBoolProperty>(target, STR("bReplicates")) || !field<U::FBoolProperty>(target, STR("bReplicateMovement"))) return false;
        unsigned char param{};
        if (!equals<U::FBoolProperty>(target, STR("bReplicates"), true) || !equals<U::FByteProperty>(target, STR("RemoteRole"), 1))
        {
            set_replication->SetPropertyValueInContainer(&param, true);
            target->ProcessEvent(replication, &param);
        }
        if (!equals<U::FBoolProperty>(target, STR("bReplicateMovement"), true))
        {
            set_movement->SetPropertyValueInContainer(&param, true);
            target->ProcessEvent(movement, &param);
        }
        target->ProcessEvent(update, nullptr);
        return equals<U::FBoolProperty>(target, STR("bReplicates"), true) &&
            equals<U::FBoolProperty>(target, STR("bReplicateMovement"), true) &&
            equals<U::FByteProperty>(target, STR("RemoteRole"), 1);
    }

    void client_guard(Iteration& call, U::UObject* receiver, U::UFunction* fn)
    {
        if (!available() || !receiver || !fn || call.OriginalFunctionCallPrevented()) return;
        static const U::FName begin{STR("ReceiveBeginPlay")}, initialize{STR("InitializeAI")},
            perception{STR("Proscess_TargetPerception")};
        const auto name = fn->GetFName();
        auto* type = fn->GetOuterPrivate();
        if (!type) return;
        if (name == begin)
        {
            static const U::FName stock_spawner{STR("Quest_AssassinTargetSpawn_BP_C")};
            if (type->GetFName() != stock_spawner || script(receiver->GetClassPrivate(), STR("ReceiveBeginPlay"), 0) != fn) return;
            const auto path = receiver->GetPathName();
            for (const auto* profile : profiles)
            {
                if (path != profile->spawner) continue;
                const auto net = network(static_cast<U::AActor*>(receiver)->GetWorld(), *profile, true);
                if (net.world && net.client && spawner_recipe(receiver, net.world, *profile)) call.PreventOriginalFunctionCall();
                return;
            }
        }
        else if (name == initialize || name == perception)
        {
            static const U::FName lenny_name{STR("NPC_Lenny_C")}, spratt_name{STR("NPC_Spratt_C")};
            const auto actor_name = receiver->GetClassPrivate()->GetFName();
            const auto* profile = actor_name == lenny_name ? &lenny : actor_name == spratt_name ? &spratt : nullptr;
            if (!profile || !exact(receiver, profile->target) ||
                !equals<U::FByteProperty>(receiver, STR("Role"), 1) ||
                receiver->GetFunctionByNameInChain(name) != fn || fn->GetReturnProperty() ||
                fn->GetOuterPrivate() != find<U::UClass>(npc_type)) return;
            const auto net = network(static_cast<U::AActor*>(receiver)->GetWorld(), *profile);
            if (net.world && net.client && in_level(receiver, net.world)) call.PreventOriginalFunctionCall();
        }
    }

    Attribution shot(U::FFrame& damage)
    {
        auto* prior = damage.PreviousFrame();
        auto* projectile = prior ? prior->Object() : nullptr;
        auto* world = static_cast<U::UWorld*>(scope.world.Get());
        auto* base = find<U::UClass>(projectile_type);
        if (!base || !projectile || prior->Node() != scope.calc || !projectile->IsA(base) ||
            !authority(projectile, world) || !damage.Locals()) return {};
        auto* pawn = scope.instigator->GetObjectPropertyValue(scope.instigator->ContainerPtrToValuePtr<void>(damage.Locals()));
        if (!pawn || object(projectile, STR("Owner")) != pawn || object(projectile, STR("Instigator")) != pawn) return {};
        std::string_view role{"-"};
        if (pawn == scope.host.Get()) role = "host";
        else for (const auto& guest : scope.guests)
        {
            if (address(pawn) != guest.pawn) continue;
            silverfish::shared_loot::EscrowOwner owner;
            if (silverfish::shared_loot::qualify_escrow_owner_in_world(world, guest.pawn, guest.save, address(scope.save.Get()), owner) &&
                owner.pawn == pawn && owner.save != owner.stock) role = "guest";
            break;
        }
        if (role == "-") return {};
        return {U::FWeakObjectPtr{pawn}, U::FWeakObjectPtr{projectile}, role,
            RC::to_utf8_string(pawn->GetFullName()), RC::to_utf8_string(projectile->GetFullName())};
    }

    void before(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        client_guard(call, receiver, frame.Node());
        if (!available() || !receiver || frame.Object() != receiver ||
            call.OriginalFunctionCallPrevented() || scope.observed || Clock::now() >= scope.until) return;
        auto* node = frame.Node();
        if (receiver != scope.target.Get()) return;
        if ((node != scope.deal && node != scope.resolve) || !current(scope) ||
            object(scope.spawner.Get(), STR("TargetNPC")) != receiver || !authority(receiver, scope.world.Get())) return;
        const double hp = scope.health->GetPropertyValueInContainer(receiver);
        if (!std::isfinite(hp)) return;
        if (node == scope.deal && !scope.damage_frame && hp > 0 && equals<U::FBoolProperty>(receiver, STR("Alive"), true))
        {
            scope.damage_frame = &frame;
            scope.current_shot = shot(frame);
        }
        else if (node == scope.resolve && !scope.death_frame && hp <= 0 && progress(scope, scope.before))
        {
            scope.death_frame = &frame;
            scope.target_killed_calls = 0;
            auto* last = object(receiver, STR("LastDamagedMe"));
            if (last && scope.damage_frame && last == scope.current_shot.pawn.Get()) scope.shooter = scope.current_shot;
            else if (last && last == scope.lethal.pawn.Get()) scope.shooter = scope.lethal;
        }
    }

    void after(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available() || !receiver || receiver != scope.target.Get()) return;
        if (&frame == scope.damage_frame)
        {
            scope.damage_frame = nullptr;
            if (!call.OriginalFunctionCallPrevented() && current(scope) && scope.health->GetPropertyValueInContainer(receiver) <= 0 &&
                scope.current_shot.pawn.Get() && object(receiver, STR("LastDamagedMe")) == scope.current_shot.pawn.Get())
                scope.lethal = scope.current_shot;
            scope.current_shot = {};
        }
        if (&frame != scope.death_frame) return;
        scope.death_frame = nullptr;
        if (call.OriginalFunctionCallPrevented()) return;
        scope.observed = true;
        scope.status = scope.profile->unverified_death;
        if (!current(scope) || !progress(scope, scope.after) || !equals<U::FBoolProperty>(receiver, STR("Alive"), false) ||
            scope.after.cash != scope.before.cash || scope.after.xp != scope.before.xp) return;
        const bool stock_handler = scope.profile == &spratt ? scope.target_killed_calls == 1 : scope.after.flag == 1;
        if (scope.before.step == 1 && scope.before.flag == 0 && scope.after.step == 2 && stock_handler)
            scope.status = "stock_objective_verified";
    }

    template<const Profile& profile>
    int watch(const Lua& lua)
    {
        const auto argc = lua.get_stack_size();
        if (!available() || !watch_enabled() || argc < 2 || argc > 10 || argc % 2) return 0;
        std::array<Address, 10> addresses{};
        for (int i = 0; i < argc; ++i)
        {
            if (!lua.is_integer(1)) return 0;
            const auto value = lua.get_integer();
            if (value <= 0) return 0;
            addresses[static_cast<std::size_t>(i)] = static_cast<Address>(value);
        }
        const auto respond = [&](std::string_view diagnostic) {
            lua.set_string(diagnostic);
            return 1;
        };
        const auto refuse = [&](std::string_view lenny_reason, std::string_view spratt_reason) {
            return respond(&profile == &spratt ? spratt_reason : lenny_reason);
        };
        auto* world = find<U::UWorld>(profile.world);
        const auto net = network(world, profile);
        if (!net.world || net.client) return refuse("stock_lenny_scope_unqualified", "stock_spratt_scope_unqualified");
        auto* save = object(net.instance, STR("SaveSlot_Ref"));
        auto* host = object(net.instance, STR("As Player FP"));
        auto* controller = object(host, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!exact(save, save_type) || address(save) != addresses[1] || !exact(host, pawn_type) ||
            !authority(host, world) || address(host) != addresses[0] || object(host, STR("Save")) != save ||
            !exact(controller, controller_type) || !authority(controller, world) ||
            object(controller, STR("Pawn")) != host || !live(player) || !local_type || !player->IsA(local_type) ||
            object(player, STR("PlayerController")) != controller)
            return refuse("stock_lenny_scope_unqualified", "stock_spratt_scope_unqualified");

        if (scope.profile != &profile || scope.host.Get() != host || scope.save.Get() != save || !current(scope))
        {
            scope = {};
            auto* spawner = find<U::UObject>(profile.spawner);
            if (!spawner_recipe(spawner, world, profile) || !authority(spawner, world) ||
                script(spawner->GetClassPrivate(), STR("Event_Target_Killed"), 1) == nullptr)
                return refuse("stock_lenny_spawner_unqualified", "stock_spratt_spawner_unqualified");
            auto* target = object(spawner, STR("TargetNPC"));
            if (!live(target)) return refuse("stock_lenny_target_unavailable", "stock_spratt_target_unavailable");
            if (!exact(target, profile.target) || !authority(target, world) || !in_level(target, world) ||
                !equals<U::FDoubleProperty>(target, STR("Max_Health"), 300.0) ||
                !equals<U::FBoolProperty>(target, STR("Alive"), true) ||
                object(spawner, STR("As Game Instance FP")) != net.instance)
                return refuse("stock_lenny_target_unqualified", "stock_spratt_target_unqualified");
            Scope next;
            next.profile = &profile;
            next.world = world;
            next.instance = net.instance;
            next.driver = net.driver;
            next.host = host;
            next.save = save;
            next.spawner = spawner;
            next.target = target;
            if (!schema(next, target)) return refuse("stock_lenny_target_schema_unqualified", "stock_spratt_target_schema_unqualified");
            Progress initial;
            if (!progress(next, initial) || initial.step != 1 || initial.flag != 0)
                return refuse("stock_lenny_stock_progress_unqualified", "stock_spratt_stock_progress_unqualified");
            next.actor = RC::to_utf8_string(target->GetFullName());
            next.replicated = publish(target);
            scope = std::move(next);
        }
        scope.until = Clock::now() + lease;
        scope.guests = {};
        for (int i = 2; i < argc; i += 2)
            scope.guests[static_cast<std::size_t>((i - 2) / 2)] = {addresses[i], addresses[i + 1]};

        const auto step = [&](int value) {
            if (!scope.observed || value == absent) lua.set_string("-");
            else lua.set_integer(value);
        };
        const auto stock = [&](int value) {
            if (!scope.observed) lua.set_string("-");
            else lua.set_integer(value);
        };
        lua.set_string(scope.status);
        lua.set_string(scope.actor);
        lua.set_bool(scope.replicated);
        step(scope.before.step);
        step(scope.after.step);
        stock(scope.before.flag);
        stock(scope.after.flag);
        stock(scope.before.xp);
        stock(scope.after.xp);
        lua.set_string(scope.shooter.role);
        lua.set_string(scope.shooter.name.empty() ? "-" : scope.shooter.name);
        lua.set_string(scope.shooter.shot.empty() ? "-" : scope.shooter.shot);
        if (auto* target = scope.target.Get(); live(target)) lua.set_number(scope.health->GetPropertyValueInContainer(target));
        else lua.set_string("-");
        if (&profile == &spratt)
        {
            stock(scope.before.cash);
            stock(scope.after.cash);
            stock(scope.target_killed_calls);
            return 16;
        }
        return 13;
    }

    template<const Profile& profile>
    int clear(const Lua&)
    {
        if (U::IsInGameThreadRaw() && scope.profile == &profile) scope = {};
        return 0;
    }
}

namespace silverfish::native_lenny_quest
{
    void install_hooks()
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || installed.load(std::memory_order_acquire)) return;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(
            before, {false, false, owner, STR("Observe exact stock Lenny damage and client spawn")});
        callbacks[1] = H::RegisterProcessLocalScriptFunctionPostCallback(
            after, {false, false, owner, STR("Verify exact stock Lenny objective")});
        callbacks[2] = H::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* fn, void* arguments) {
                client_guard(call, actor, fn);
                if (available() && scope.profile == &spratt && scope.death_frame && arguments &&
                    actor == scope.spawner.Get() && fn == scope.target_killed && !call.OriginalFunctionCallPrevented() &&
                    scope.event_target->GetObjectPropertyValue(scope.event_target->ContainerPtrToValuePtr<void>(arguments))
                        == scope.target.Get()) ++scope.target_killed_calls;
            },
            {false, false, owner, STR("Guard stock assassination client birth and observe Spratt's exact callback")});
        callbacks[3] = H::RegisterLoadMapPreCallback(
            [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
                U::FURL url, U::UPendingNetGame* game, U::FString&) {
                if (!available()) return;
                pending = {};
                if (!game || url.Map.IsEmpty()) return;
                const std::wstring_view package{*url.Map};
                const Profile* profile = nullptr;
                for (const auto* candidate : profiles)
                    if (package == candidate->world_package || package == candidate->world) profile = candidate;
                if (!profile) return;
                auto* object_game = reinterpret_cast<U::UObject*>(game);
                if (!exact(object_game, STR("/Script/Engine.PendingNetGame"))) return;
                auto* driver = object(object_game, STR("NetDriver"));
                auto* server = object(driver, STR("ServerConnection"));
                if (server_connection(server, driver)) pending = {driver, server, &context, profile};
            }, {false, true, owner, STR("Scope the early stock assassination client load")});
        callbacks[4] = H::RegisterLoadMapPostCallback(
            [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
                U::FURL, U::UPendingNetGame*, U::FString&) {
                if (available() && pending.context == &context) pending = {};
            }, {false, true, owner, STR("Clear the early stock assassination client load scope")});
        for (const auto callback : callbacks)
        {
            if (callback != H::ERROR_ID) continue;
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (U::IsInGameThreadRaw()) { scope = {}; pending = {}; }
        for (auto& callback : callbacks)
        {
            if (callback != H::ERROR_ID) H::UnregisterCallback(callback);
            callback = H::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishWatchLennyQuest", watch<lenny>);
        lua.register_function("SilverfishClearLennyQuest", clear<lenny>);
        lua.register_function("SilverfishWatchSprattQuest", watch<spratt>);
        lua.register_function("SilverfishClearSprattQuest", clear<spratt>);
    }
}
