#include "flood_jammer.hpp"
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

    constexpr auto jammer_type = STR("/Game/Classes/ScriptingActors/Quest_Objectives/SignalJammer_BP.SignalJammer_BP_C");
    constexpr auto trigger_type = STR("/Game/Classes/ScriptingActors/Quest_Trigger_BP.Quest_Trigger_BP_C");
    constexpr auto base_type = STR("/Game/Classes/ScriptingActors/Simple_DestroyTarget_BP.Simple_DestroyTarget_BP_C");
    constexpr auto projectile_type = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto pawn_type = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_type = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_type = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_type = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto table_path = STR("/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT");
    constexpr auto quest_type = STR("/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct");
    constexpr auto quest_step = STR("Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A");
    constexpr int absent = (std::numeric_limits<int>::min)();
    constexpr int map_limit = 8192;
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

    bool trigger_cache_matches(U::UObject* trigger, U::UObject* instance, bool allow_empty)
    {
        auto* property = field<U::FObjectProperty>(trigger, STR("As Game Instance FP"));
        if (!property || !instance) return false;
        auto* cached = property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(trigger));
        return cached == instance || (allow_empty && !cached);
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

    U::UObject* sole_object(U::UObject* target, const U::TCHAR* name)
    {
        auto* property = field<U::FArrayProperty>(target, name);
        auto* inner = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!inner) return nullptr;
        U::FScriptArrayHelper array(property, property->ContainerPtrToValuePtr<void>(target));
        return array.Num() == 1 ? inner->GetObjectPropertyValue(array.GetRawPtr(0)) : nullptr;
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

    struct JammerProfile
    {
        const U::TCHAR *world, *jammer, *trigger, *quest;
        int salt, faction, advantage;
    };

    constexpr std::array jammer_profiles{
        JammerProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.SignalJammer_BP_C_1"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P:PersistentLevel.Quest_Trigger_BP_C_1"),
            STR("Jmr_FloodPlains"), 112725360, 2, 75},
        JammerProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P:PersistentLevel.SignalJammer_BP_C_1"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P:PersistentLevel.Quest_Trigger_BP_C_1"),
            STR("Jmr_Lake"), 343288032, 8, 50},
    };

    struct Snapshot { int step{absent}, cash{}, xp{}; };
    struct Proof
    {
        std::string_view state{"watching"}, role{"-"};
        std::string pawn{}, projectile{};
        Snapshot before{}, after{};
        bool observed{};
    };
    struct Guest { Address pawn{}, save{}; };
    struct Scope
    {
        const JammerProfile* profile{};
        U::FWeakObjectPtr world{}, level{}, instance{}, driver{}, host{}, controller{}, player{}, save{};
        U::FWeakObjectPtr jammer{}, trigger{};
        U::UFunction *signal{}, *graph{}, *core{}, *deal{};
        U::FFrame* frame{};
        U::FName quest{}, cash{}, xp{};
        U::UScriptStruct* quest_struct{};
        U::FIntProperty* step{};
        U::FBoolProperty *powered{}, *broken{};
        U::FObjectProperty *signal_instigator{}, *damage_instigator{};
        U::FDoubleProperty* health{};
        std::array<Guest, 4> guests{};
        Clock::time_point until{};
        Proof proof{};
    };

    Scope scope;
    std::array callbacks{H::ERROR_ID, H::ERROR_ID};
    std::atomic<bool> installed{};

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_EVENTS"); }
    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }
    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool current(const Scope& candidate)
    {
        auto* world = candidate.world.Get();
        auto* driver = candidate.driver.Get();
        auto* instance = candidate.instance.Get();
        auto* controller = candidate.controller.Get();
        auto* player = candidate.player.Get();
        auto* host = candidate.host.Get();
        auto* save = candidate.save.Get();
        return candidate.profile && live(world) && live(driver) && live(instance) && live(player) && live(save) &&
            authority(host, world) && authority(controller, world) &&
            object(world, STR("PersistentLevel")) == candidate.level.Get() &&
            object(world, STR("NetDriver")) == driver && object(world, STR("OwningGameInstance")) == instance &&
            object(driver, STR("World")) == world && field<U::FObjectProperty>(driver, STR("ServerConnection")) &&
            !object(driver, STR("ServerConnection")) && sole_object(instance, STR("LocalPlayers")) == player &&
            object(controller, STR("Player")) == player && object(controller, STR("Pawn")) == host &&
            object(host, STR("Controller")) == controller && object(instance, STR("As Player FP")) == host &&
            object(instance, STR("SaveSlot_Ref")) == save && object(host, STR("Save")) == save;
    }

    bool take_snapshot(const Scope& candidate, Snapshot& out)
    {
        auto* save = candidate.save.Get();
        const auto read_flag = [save](const U::FName& key, int& into) {
            return map_member<U::FNameProperty, U::FIntProperty>(save, STR("Flags"), key,
                [&](auto* value, const void* data, int) {
                    into = data ? value->GetPropertyValue(data) : 0;
                    return true;
                });
        };
        return map_member<U::FNameProperty, U::FStructProperty>(save, STR("ActiveQuests"), candidate.quest,
            [&](auto* value, const void* data, int) {
                if (value->GetStruct().Get() != candidate.quest_struct) return false;
                out.step = data ? candidate.step->GetPropertyValueInContainer(data) : absent;
                return out.step != absent || !data;
            }) && read_flag(candidate.cash, out.cash) && read_flag(candidate.xp, out.xp);
    }

    bool trigger_recipe(const Scope& candidate)
    {
        auto* trigger = candidate.trigger.Get();
        auto* tracked = field<U::FStructProperty>(trigger, STR("TrackedQuest"));
        auto* definition = tracked ? tracked->GetStruct().Get() : nullptr;
        auto* table = field<U::FObjectProperty>(definition, STR("DataTable"));
        auto* row = field<U::FNameProperty>(definition, STR("RowName"));
        const void* handle = tracked ? tracked->ContainerPtrToValuePtr<void>(trigger) : nullptr;
        auto* stock_table = find<U::UObject>(table_path);
        if (!handle || !table || !row || !stock_table || row->GetPropertyValueInContainer(handle) != candidate.quest ||
            table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(handle)) != stock_table ||
            !equals<U::FBoolProperty>(trigger, STR("ForceComplete"), true) ||
            !equals<U::FIntProperty>(trigger, STR("Add_QuestStep"), 1) ||
            !equals<U::FIntProperty>(trigger, STR("Target_QuestStep"), -1) ||
            !equals<U::FIntProperty>(trigger, STR("XP_IfForced"), 1)) return false;

        const auto effect = [trigger](const U::TCHAR* map, int key, int delta, int size) {
            return map_member<U::FByteProperty, U::FIntProperty>(trigger, map, key,
                [=](auto* value, const void* data, int count) {
                    return count == size && data && value->GetPropertyValue(data) == delta;
                });
        };
        return candidate.profile && effect(STR("Rep_OnComplete"), candidate.profile->faction, 10, 1) &&
            effect(STR("Adv_OnComplete"), candidate.profile->faction, candidate.profile->advantage, 2) &&
            effect(STR("Adv_OnComplete"), 5, -10, 2) &&
            map_member<U::FNameProperty, U::FIntProperty>(trigger, STR("Set_SaveFlags"), candidate.quest,
                [](auto*, const void*, int count) { return count == 0; });
    }

    bool bind(Address host_address, Address save_address, Scope& next)
    {
        U::UWorld* world = nullptr;
        for (const auto& profile : jammer_profiles)
        {
            auto* possible = find<U::UWorld>(profile.world);
            if (!live(possible)) continue;
            auto* possible_instance = object(possible, STR("OwningGameInstance"));
            auto* possible_host = object(possible_instance, STR("As Player FP"));
            if (address(possible_host) != host_address || !exact(possible_host, pawn_type) ||
                !authority(possible_host, possible)) continue;
            if (world) return false;
            world = possible;
            next.profile = &profile;
        }
        if (!world) return false;
        auto* level = object(world, STR("PersistentLevel"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        auto* host = object(instance, STR("As Player FP"));
        auto* save = object(instance, STR("SaveSlot_Ref"));
        auto* controller = object(host, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        if (!live(world) || !live(level) || address(host) != host_address || address(save) != save_address ||
            !exact(instance, instance_type) || !exact(save, save_type) || !exact(host, pawn_type) ||
            !exact(controller, controller_type)) return false;
        next.world = world;
        next.level = level;
        next.instance = instance;
        next.driver = driver;
        next.host = host;
        next.controller = controller;
        next.player = player;
        next.save = save;
        return current(next);
    }

    template <typename T>
    T* input(U::UFunction* function, const U::TCHAR* name)
    {
        auto* value = field<T>(function, name);
        return value && value->HasAnyPropertyFlags(U::CPF_Parm) &&
            !value->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) && value->GetOffset_Internal() >= 0 &&
            value->GetOffset_Internal() + value->GetSize() <= function->GetParmsSize() ? value : nullptr;
    }

    std::string_view actors(Scope& next)
    {
        auto* world = next.world.Get();
        auto* level = next.level.Get();
        if (!next.profile) return "stock_jammer_profile_unavailable";
        auto* jammer = find<U::UObject>(next.profile->jammer);
        auto* trigger = find<U::UObject>(next.profile->trigger);
        if (!exact(jammer, jammer_type) || !exact(trigger, trigger_type) || !authority(jammer, world) ||
            !authority(trigger, world) || static_cast<U::AActor*>(jammer)->GetLevel() != level ||
            static_cast<U::AActor*>(trigger)->GetLevel() != level ||
            sole_object(jammer, STR("Activate_OnDeath")) != trigger ||
            !equals<U::FIntProperty>(jammer, STR("Salt"), next.profile->salt)) return "stock_exact_jammer_or_trigger_unavailable";
        // The powered stock signal fills the trigger's GameInstance cache before accessing the quest.
        if (!trigger_cache_matches(trigger, next.instance.Get(), true)) return "stock_jammer_trigger_cache_unqualified";

        auto* base = find<U::UClass>(base_type);
        if (!base || !jammer->IsA(base)) return "stock_jammer_damage_schema_unqualified";
        next.signal = trigger->GetFunctionByNameInChain(U::FName{STR("Send_PowerSignal")});
        next.graph = base->GetFunctionByNameInChain(U::FName{STR("ExecuteUbergraph_Simple_DestroyTarget_BP")});
        next.core = base->GetFunctionByNameInChain(U::FName{STR("Core_Destroyed")});
        next.deal = base->GetFunctionByNameInChain(U::FName{STR("DealDamage")});
        next.powered = input<U::FBoolProperty>(next.signal, STR("Powered"));
        next.broken = input<U::FBoolProperty>(next.core, STR("Broken"));
        next.signal_instigator = input<U::FObjectProperty>(next.signal, STR("Instigator"));
        next.damage_instigator = input<U::FObjectProperty>(next.deal, STR("Instigator"));
        next.health = field<U::FDoubleProperty>(jammer, STR("Health"));
        if (!next.signal || next.signal->GetNumParms() != 2 || !next.graph || !next.core || !next.deal ||
            !next.powered || !next.broken || !next.signal_instigator || !next.damage_instigator || !next.health ||
            jammer->GetFunctionByNameInChain(U::FName{STR("DealDamage")}) != next.deal ||
            jammer->GetFunctionByNameInChain(U::FName{STR("Core_Destroyed")}) != next.core) return "stock_jammer_damage_schema_unqualified";

        next.quest = U::FName{next.profile->quest, U::FNAME_Find};
        next.cash = U::FName{STR("Player_Cash"), U::FNAME_Find};
        next.xp = U::FName{STR("Player_Xp"), U::FNAME_Find};
        next.quest_struct = find<U::UScriptStruct>(quest_type);
        next.step = field<U::FIntProperty>(next.quest_struct, quest_step);
        next.jammer = jammer;
        next.trigger = trigger;
        Snapshot probe;
        if (next.quest.IsNone() || next.cash.IsNone() || next.xp.IsNone() || !next.step ||
            !trigger_recipe(next) || !take_snapshot(next, probe)) return "stock_jammer_trigger_recipe_unqualified";
        return {};
    }

    void attribute(U::FFrame& deal, Proof& proof)
    {
        auto* original = deal.PreviousFrame();
        auto* projectile = original ? original->Object() : nullptr;
        auto* base = find<U::UClass>(projectile_type);
        auto* calc = base ? base->GetFunctionByNameInChain(U::FName{STR("CalcDmg")}) : nullptr;
        auto* world = static_cast<U::UWorld*>(scope.world.Get());
        if (!base || !calc || !projectile || !projectile->IsA(base) || original->Node() != calc ||
            !authority(projectile, world) || !deal.Locals()) return;
        auto* pawn = scope.damage_instigator->GetObjectPropertyValue(
            scope.damage_instigator->ContainerPtrToValuePtr<void>(deal.Locals()));
        if (!pawn || object(projectile, STR("Owner")) != pawn || object(projectile, STR("Instigator")) != pawn) return;

        if (pawn == scope.host.Get()) proof.role = "host";
        else for (const auto& guest : scope.guests)
        {
            if (address(pawn) != guest.pawn) continue;
            silverfish::shared_loot::EscrowOwner owner;
            if (silverfish::shared_loot::qualify_escrow_owner_in_world(world, guest.pawn, guest.save,
                                                                      address(scope.save.Get()), owner) &&
                owner.pawn == pawn && owner.save != owner.stock) proof.role = "guest";
            break;
        }
        if (proof.role == "-") return;
        proof.pawn = RC::to_utf8_string(pawn->GetFullName());
        proof.projectile = RC::to_utf8_string(projectile->GetFullName());
    }

    void before(Iteration& iteration, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available() || !receiver || receiver != scope.trigger.Get() || frame.Object() != receiver ||
            frame.Node() != scope.signal || scope.frame || scope.proof.observed || Clock::now() >= scope.until ||
            iteration.OriginalFunctionCallPrevented() || !current(scope)) return;

        auto* jammer = scope.jammer.Get();
        auto* graph = frame.PreviousFrame();
        auto* core = graph ? graph->PreviousFrame() : nullptr;
        auto* deal = core ? core->PreviousFrame() : nullptr;
        if (!frame.Locals() || !graph || !core || !deal || !core->Locals() || graph->Node() != scope.graph ||
            core->Node() != scope.core || deal->Node() != scope.deal || graph->Object() != jammer ||
            core->Object() != jammer || deal->Object() != jammer || !authority(jammer, scope.world.Get()) ||
            !scope.powered->GetPropertyValueInContainer(frame.Locals()) ||
            scope.signal_instigator->GetObjectPropertyValue(scope.signal_instigator->ContainerPtrToValuePtr<void>(frame.Locals())) ||
            !scope.broken->GetPropertyValueInContainer(core->Locals())) return;

        const double hp = scope.health->GetPropertyValueInContainer(jammer);
        Proof next;
        if (!std::isfinite(hp) || hp > 0 || sole_object(jammer, STR("Activate_OnDeath")) != receiver ||
            !trigger_cache_matches(receiver, scope.instance.Get(), true) || !take_snapshot(scope, next.before)) return;
        attribute(*deal, next);
        scope.proof = std::move(next);
        scope.frame = &frame;
    }

    void after(Iteration& iteration, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available() || scope.frame != &frame) return;
        scope.frame = nullptr;
        if (iteration.OriginalFunctionCallPrevented()) return;
        auto& proof = scope.proof;
        proof.observed = true;
        if (!current(scope) || receiver != scope.trigger.Get() ||
            !trigger_cache_matches(receiver, scope.instance.Get(), false) || !take_snapshot(scope, proof.after))
        {
            proof.state = "mutation_unverified_stock_jammer_objective_readback";
            return;
        }
        const auto first = proof.before;
        const auto second = proof.after;
        if (second.cash == first.cash && first.step == 1 && second.step == 2 && second.xp == first.xp)
            proof.state = "stock_objective_verified";
        else if (second.cash == first.cash && first.step == absent && second.step == -7 &&
                 static_cast<std::int64_t>(second.xp) == static_cast<std::int64_t>(first.xp) + 1)
            proof.state = "stock_force_complete_verified";
        else if (second.cash == first.cash && (first.step == 2 || first.step == -7 || first.step == -10) &&
                 first.step == second.step && first.xp == second.xp)
            proof.state = "stock_jammer_no_progress";
        else proof.state = "mutation_unverified_stock_jammer_objective_transition";
    }

    int reply(const Lua& lua, bool result, std::string_view reason)
    {
        lua.set_bool(result);
        lua.set_string(reason);
        return 2;
    }

    int watch(const Lua& lua)
    {
        if (!available()) return reply(lua, false, "stock_jammer_observer_unavailable");
        const auto argc = lua.get_stack_size();
        if (argc < 2 || argc > 10 || argc % 2 != 0) return reply(lua, false, "stock_jammer_scope_arguments");
        std::array<Address, 10> addresses{};
        for (int index = 0; index < argc; ++index)
        {
            if (!lua.is_integer(1)) return reply(lua, false, "stock_jammer_scope_arguments");
            const auto value = lua.get_integer();
            if (value <= 0) return reply(lua, false, "stock_jammer_scope_arguments");
            addresses[static_cast<std::size_t>(index)] = static_cast<Address>(value);
        }
        if (address(scope.host.Get()) != addresses[0] || address(scope.save.Get()) != addresses[1] || !current(scope))
        {
            scope = {};
            Scope next;
            if (!bind(addresses[0], addresses[1], next)) return reply(lua, false, "stock_jammer_authority_unqualified");
            if (const auto reason = actors(next); !reason.empty()) return reply(lua, false, reason);
            scope = std::move(next);
        }
        scope.guests = {};
        for (int index = 2; index < argc; index += 2)
            scope.guests[static_cast<std::size_t>((index - 2) / 2)] = {addresses[index], addresses[index + 1]};
        scope.until = Clock::now() + lease;
        return reply(lua, true, scope.proof.state);
    }

    int read(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        if (requested <= 0 || static_cast<Address>(requested) != address(scope.host.Get()) || !current(scope)) return 0;
        const auto& proof = scope.proof;
        const auto step = [&](int value) {
            if (value == absent) lua.set_string("-");
            else lua.set_integer(value);
        };
        lua.set_string(proof.state);
        step(proof.before.step);
        step(proof.after.step);
        if (proof.observed) { lua.set_integer(proof.before.xp); lua.set_integer(proof.after.xp); }
        else { lua.set_string("-"); lua.set_string("-"); }
        lua.set_string(proof.role);
        lua.set_string(proof.pawn.empty() ? "-" : proof.pawn);
        lua.set_string(proof.projectile.empty() ? "-" : proof.projectile);
        return 8;
    }

    int clear(const Lua&)
    {
        if (U::IsInGameThreadRaw()) scope = {};
        return 0;
    }
}

namespace silverfish::native_flood_jammer
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(
            before, {false, false, owner, STR("Observe exact authored jammer signal")});
        callbacks[1] = H::RegisterProcessLocalScriptFunctionPostCallback(
            after, {false, false, owner, STR("Read exact authored jammer quest transition")});
        if (callbacks[0] == H::ERROR_ID || callbacks[1] == H::ERROR_ID) uninstall_hooks();
        else installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (U::IsInGameThreadRaw()) scope = {};
        for (auto& callback : callbacks)
        {
            if (callback != H::ERROR_ID) H::UnregisterCallback(callback);
            callback = H::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishWatchFloodJammer", watch);
        lua.register_function("SilverfishReadFloodJammer", read);
        lua.register_function("SilverfishClearFloodJammer", clear);
    }
}
