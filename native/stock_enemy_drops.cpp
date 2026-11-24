#include "stock_enemy_drops.hpp"
#include "enemy_loot.hpp"
#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>
#include <utility>
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
    namespace H = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = H::TCallbackIterationData<void>;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto npc_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto stock_spawn = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:SpawnLoot");
    constexpr auto stock_graph = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:ExecuteUbergraph_NPC_Master_BP");
    constexpr auto spawn_item = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:SpawnItemActor");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto actor_path = STR("/Script/Engine.Actor");
    constexpr auto prop_path = STR("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C");
    constexpr auto hole_path = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C");
    constexpr auto hole_graph = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C:ExecuteUbergraph_DigHole_BP");
    constexpr auto hole_begin = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C:ReceiveBeginPlay");
    constexpr auto bolt_path = STR("/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C");
    constexpr auto bolt_projectile = STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Bolt_Projectile_BP.Bolt_Projectile_BP_C");
    constexpr auto bolt_graph = STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Bolt_Projectile_BP.Bolt_Projectile_BP_C:ExecuteUbergraph_Bolt_Projectile_BP");
    constexpr std::size_t max_roll = 48;
    constexpr std::size_t max_recent_drops = 128;
    constexpr auto max_sequence = (std::uint64_t{1} << 53) - 1;

    struct Observation
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr npc, world, killer_pawn, killer_save;
        std::array<U::FObjectProperty*, 5> deferred{};
        std::vector<U::FWeakObjectPtr> items;
        std::string killer;
        bool overflow{};
    };

    struct Drop
    {
        U::FWeakObjectPtr actor;
        std::uint64_t sequence{}, roll{};
        std::string npc, killer, actor_name, class_path;
        bool physics_prop{};
    };

    Observation observing;
    struct BoltObservation
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr projectile, world;
        U::FObjectProperty* deferred{};
    } bolt_observing;
    U::FWeakObjectPtr reported_world;
    std::deque<Drop> recent;
    std::uint64_t sequence{}, roll{};
    std::atomic<bool> installed{};
    std::array<H::GlobalCallbackId, 3> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};

    struct DigRollFixture
    {
        U::FWeakObjectPtr world;
        U::FFrame* frame{};
        std::uint8_t* operand{};
    } dig_fixture;

    bool dig_fixture_enabled()
    {
        for (const auto* name : {"SILVERFISH_COOP_HEADLESS", "SILVERFISH_COOP_TITLE_FIXTURES"})
        {
            std::array<char, 2> value{};
            std::size_t length{};
            if (getenv_s(&length, value.data(), value.size(), name) || length != value.size() || value[0] != '1')
                return false;
        }
        return true;
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t length{};
        return getenv_s(&length, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_LOOT") == 0 &&
            length == value.size() && value[0] == '1';
    }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }
    Address address(const U::UObject* object) { return reinterpret_cast<Address>(object); }

    template <class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <class T> T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* value = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    U::UObject* object(U::UObject* receiver, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(receiver, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(receiver)) : nullptr;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->IsUnreachable() && !value->HasAnyFlags(excluded);
    }

    bool authority_in_level(U::AActor* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        auto* destroying = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            destroying && !destroying->GetPropertyValueInContainer(actor) &&
            actor->GetWorld() == world && actor->GetLevel() == object(world, STR("PersistentLevel"));
    }

    U::UObject* listen_instance(U::UWorld* world)
    {
        if (!live(world)) return nullptr;
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        return live(instance) && instance->GetClassPrivate() == find<U::UClass>(instance_path) &&
            live(driver) && object(driver, STR("World")) == world &&
            field<U::FObjectProperty>(driver, STR("ServerConnection")) && !object(driver, STR("ServerConnection")) ?
            instance : nullptr;
    }

    bool item_in_world(U::UObject* value, U::UWorld* world)
    {
        auto* item = find<U::UClass>(item_path);
        return live(value) && item && value->IsA(item) && authority_in_level(static_cast<U::AActor*>(value), world);
    }

    bool stock_dig_frame(U::UObject* receiver, U::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        return live(receiver) && receiver->GetClassPrivate() == find<U::UClass>(hole_path) &&
            frame.Object() == receiver && frame.Node() == find<U::UFunction>(hole_graph) && caller &&
            caller->Object() == receiver && caller->Node() == find<U::UFunction>(hole_begin);
    }

    int arm_dig_fixture(const Lua& lua)
    {
        if (!available() || !dig_fixture_enabled() || dig_fixture.frame || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return 0;
        auto* world = find<U::UWorld>(STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"));
        const bool ready = world && address(world) == static_cast<Address>(lua.get_integer()) && listen_instance(world);
        if (ready) dig_fixture.world = world;
        lua.set_bool(ready);
        return 1;
    }

    void force_fixture_roll(U::UObject* receiver, U::FFrame& frame)
    {
        auto* world = static_cast<U::UWorld*>(dig_fixture.world.Get());
        if (!world || dig_fixture.frame || !stock_dig_frame(receiver, frame) ||
            !authority_in_level(static_cast<U::AActor*>(receiver), world) || !listen_instance(world)) return;
        auto& script = frame.Node()->GetScript();
        if (script.Num() < 195) return;
        constexpr std::array<std::uint8_t, 6> stock{0x1e, 0xcd, 0xcc, 0x4c, 0x3d, 0x16};
        auto* end = script.GetData() + 195;
        auto* match = std::search(script.GetData() + 170, end, stock.begin(), stock.end());
        if (match == end) return;
        // Test one real stock roll without waiting for the normal five-percent chance.
        constexpr float always = 1.0f;
        std::memcpy(match + 1, &always, sizeof(always));
        dig_fixture.frame = &frame;
        dig_fixture.operand = match + 1;
    }

    void restore_fixture_roll(U::FFrame& frame)
    {
        if (dig_fixture.frame != &frame) return;
        constexpr float stock_chance = 0.05f;
        std::memcpy(dig_fixture.operand, &stock_chance, sizeof(stock_chance));
        dig_fixture = {};
    }

    bool begin_roll(U::UWorld* world)
    {
        if (sequence > max_sequence - max_roll || roll >= max_sequence) return false;
        if (reported_world.Get() != world)
        {
            recent.clear();
            reported_world = world;
        }
        ++roll;
        return true;
    }

    void record_drop(U::UObject* actor, const std::string& source, const std::string& killer, bool physics_prop = false)
    {
        if (recent.size() == max_recent_drops) recent.pop_front();
        recent.push_back({actor, ++sequence, roll, source, killer, RC::to_utf8_string(actor->GetFullName()),
            RC::to_utf8_string(actor->GetClassPrivate()->GetPathName()), physics_prop});
    }

    U::UObject* finished_stock_spawn(U::FFrame& frame)
    {
        auto* function = frame.Node();
        if (!function || !frame.Locals()) return nullptr;
        const auto spawn_local = [&](const U::TCHAR* name) -> U::UObject* {
            auto* local = field<U::FObjectProperty>(function, name);
            if (!local || local->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm)) return nullptr;
            return local->GetObjectPropertyValue(local->ContainerPtrToValuePtr<void>(frame.Locals()));
        };
        auto* actor = spawn_local(STR("CallFunc_FinishSpawningActor_ReturnValue"));
        if (!live(actor) || actor != spawn_local(STR("CallFunc_BeginDeferredActorSpawnFromClass_ReturnValue")) ||
            actor->HasAnyFlags(U::RF_WasLoaded)) return nullptr;
        return actor;
    }

    bool already_observed(U::UWorld* world, U::UObject* actor)
    {
        return reported_world.Get() == world &&
            std::ranges::any_of(recent, [actor](const auto& row) { return row.actor.Get() == actor; });
    }

    void record_unearthed(U::UObject* receiver, U::FFrame& frame)
    {
        if (!stock_dig_frame(receiver, frame)) return;
        auto* world = static_cast<U::AActor*>(receiver)->GetWorld();
        if (!listen_instance(world) || !authority_in_level(static_cast<U::AActor*>(receiver), world)) return;

        auto* actor = finished_stock_spawn(frame);
        if (!actor) return;
        auto* prop = find<U::UClass>(prop_path);
        const bool physics_prop = prop && actor->IsA(prop);
        if (physics_prop ? !authority_in_level(static_cast<U::AActor*>(actor), world) : !item_in_world(actor, world)) return;
        if (!already_observed(world, actor) && begin_roll(world))
            record_drop(actor, RC::to_utf8_string(receiver->GetFullName()), {}, physics_prop);
    }

    void observe_landing_bolt(U::UObject* receiver, U::FFrame& frame)
    {
        if (bolt_observing.frame || !frame.Locals() || !live(receiver) ||
            receiver->GetClassPrivate() != find<U::UClass>(bolt_projectile) ||
            frame.Object() != receiver || frame.Node() != find<U::UFunction>(bolt_graph)) return;
        auto* projectile = static_cast<U::AActor*>(receiver);
        auto* world = projectile->GetWorld();
        auto* deferred = field<U::FObjectProperty>(frame.Node(), STR("CallFunc_BeginDeferredActorSpawnFromClass_ReturnValue"));
        if (deferred && !deferred->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm)
            && listen_instance(world) && authority_in_level(projectile, world))
            bolt_observing = {&frame, projectile, world, deferred};
    }

    void record_landed_bolt(U::AActor* actor)
    {
        const auto& scope = bolt_observing;
        auto* projectile = scope.projectile.Get();
        auto* world = static_cast<U::UWorld*>(scope.world.Get());
        if (!scope.frame || !live(projectile) || !live(actor) || actor->GetClassPrivate() != find<U::UClass>(bolt_path) ||
            actor->HasAnyFlags(U::RF_WasLoaded) || !item_in_world(actor, world)) return;
        auto* locals = scope.frame->Locals();
        if (!locals || scope.deferred->GetObjectPropertyValue(scope.deferred->ContainerPtrToValuePtr<void>(locals)) != actor)
            return;
        auto* held = field<U::FBoolProperty>(actor, STR("HeldByPlayer"));
        auto* respawn = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!held || held->GetPropertyValueInContainer(actor) || !respawn || respawn->GetPropertyValueInContainer(actor) != 0)
            return;
        if (!already_observed(world, actor) && begin_roll(world))
            record_drop(actor, RC::to_utf8_string(projectile->GetFullName()), {});
    }

    void collect(U::UObject* value)
    {
        if (!observing.frame || !item_in_world(value, static_cast<U::UWorld*>(observing.world.Get())) ||
            std::ranges::any_of(observing.items, [value](const auto& entry) { return entry.Get() == value; })) return;
        if (observing.items.size() == max_roll) observing.overflow = true;
        else observing.items.emplace_back(value);
    }

    void capture_begin(Iteration& call, U::AActor* actor)
    {
        if (!available() || call.OriginalFunctionCallPrevented()) return;
        if (bolt_observing.frame) record_landed_bolt(actor);
        if (!observing.frame) return;
        auto* locals = observing.frame->Locals();
        if (locals && std::ranges::any_of(observing.deferred, [=](auto* property) {
                return property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(locals)) == actor;
            })) collect(actor);
    }

    void before_spawn(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        static const U::FName spawn_name{STR("SpawnLoot")};
        static const U::FName dig_graph{STR("ExecuteUbergraph_DigHole_BP")};
        static const U::FName thrown_bolt_graph{STR("ExecuteUbergraph_Bolt_Projectile_BP")};
        auto* function = frame.Node();
        if (!available() || !function || call.OriginalFunctionCallPrevented()) return;
        if (function->GetFName() == dig_graph) force_fixture_roll(receiver, frame);
        if (function->GetFName() == thrown_bolt_graph) observe_landing_bolt(receiver, frame);
        if (observing.frame || function->GetFName() != spawn_name || frame.Object() != receiver || !live(receiver)) return;
        auto* base = find<U::UClass>(npc_path);
        auto* caller = frame.PreviousFrame();
        if (!base || !receiver->IsA(base) || function != find<U::UFunction>(stock_spawn) || function->GetOuterPrivate() != base ||
            function->GetNumParms() != 0 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !caller || caller->Object() != receiver || caller->Node() != find<U::UFunction>(stock_graph)) return;

        auto* npc = static_cast<U::AActor*>(receiver);
        auto* world = npc->GetWorld();
        auto* instance = listen_instance(world);
        auto* alive = field<U::FBoolProperty>(npc, STR("Alive"));
        if (!instance || !authority_in_level(npc, world) || !alive || alive->GetPropertyValueInContainer(npc)) return;
        auto* actor = find<U::UClass>(actor_path);
        if (!actor || !frame.Locals()) return;

        // Each deferred result is live while FinishSpawningActor runs BeginPlay.
        // Reading this local at function return would lose every earlier loop item.
        constexpr auto root = STR("CallFunc_BeginDeferredActorSpawnFromClass_ReturnValue");
        Observation next;
        for (auto* prop : U::TFieldRange<U::FProperty>(function, U::EFieldIterationFlags::None))
        {
            const auto name = prop->GetFName().ToString();
            const std::basic_string_view<U::TCHAR> text{name};
            const std::basic_string_view<U::TCHAR> prefix{root};
            if (!text.starts_with(prefix)) continue;
            const auto suffix = text.substr(prefix.size());
            const int slot = suffix.empty() ? 0 : suffix.size() == 2 && suffix[0] == '_' &&
                suffix[1] >= '1' && suffix[1] <= '4' ? suffix[1] - '0' : -1;
            auto* local = U::CastField<U::FObjectProperty>(prop);
            auto* property_class = local ? local->GetPropertyClass().Get() : nullptr;
            if (slot < 0 || next.deferred[slot] || !property_class || local->GetArrayDim() != 1 ||
                !property_class->IsChildOf(actor) ||
                local->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm)) return;
            next.deferred[slot] = local;
        }
        if (std::ranges::any_of(next.deferred, [](auto* prop) { return prop == nullptr; })) return;
        auto* killer = object(npc, STR("LastDamagedMe"));
        auto* stock = object(instance, STR("SaveSlot_Ref"));
        silverfish::shared_loot::EscrowOwner participant{};
        if (live(killer) && killer->GetClassPrivate() == find<U::UClass>(pawn_path) &&
            silverfish::shared_loot::qualify_escrow_owner_in_world(world, address(killer),
                address(object(killer, STR("Save"))), address(stock), participant))
        {
            next.killer = RC::to_utf8_string(killer->GetFullName());
            if (participant.save != participant.stock)
            {
                next.killer_pawn = participant.pawn;
                next.killer_save = participant.save;
            }
        }
        next.frame = &frame;
        next.npc = npc;
        next.world = world;
        observing = std::move(next);
    }

    void after_spawn(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available()) return;
        if (&frame == bolt_observing.frame) bolt_observing = {};
        restore_fixture_roll(frame);
        static const U::FName dig_graph{STR("ExecuteUbergraph_DigHole_BP")};
        if (frame.Node() && frame.Node()->GetFName() == dig_graph && !call.OriginalFunctionCallPrevented())
            record_unearthed(receiver, frame);
        if (!observing.frame) return;
        if (&frame != observing.frame)
        {
            static const U::FName passive_name{STR("FindPassiveEffect")};
            auto* killer = observing.killer_pawn.Get();
            if (killer && frame.Node() && frame.Node()->GetFName() == passive_name &&
                frame.PreviousFrame() && frame.PreviousFrame()->PreviousFrame() == observing.frame &&
                !call.OriginalFunctionCallPrevented())
            {
                auto* world = static_cast<U::UWorld*>(observing.world.Get());
                auto* instance = listen_instance(world);
                silverfish::shared_loot::EscrowOwner participant{};
                if (instance && receiver == instance && silverfish::shared_loot::qualify_escrow_owner_in_world(
                        world, address(killer), address(observing.killer_save.Get()),
                        address(object(instance, STR("SaveSlot_Ref"))), participant))
                    silverfish::native_enemy_loot::apply_stock_guest_bounty(frame, *observing.frame,
                        observing.npc.Get(), instance, participant);
                return;
            }
            if (!call.OriginalFunctionCallPrevented() && frame.PreviousFrame() == observing.frame &&
                frame.Node() == find<U::UFunction>(spawn_item) &&
                receiver == listen_instance(static_cast<U::UWorld*>(observing.world.Get())))
            {
                auto* result = field<U::FObjectProperty>(frame.Node(), STR("Actor"));
                if (!result || !result->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm)) return;
                int remaining = frame.Node()->GetNumParms();
                for (auto* out = frame.OutParms(); out && remaining > 0; --remaining, out = out->NextOutParm)
                {
                    if (out->Property != result || !out->PropAddr) continue;
                    collect(result->GetObjectPropertyValue(out->PropAddr));
                    return;
                }
            }
            return;
        }
        auto finished = std::exchange(observing, {});
        auto* world = static_cast<U::UWorld*>(finished.world.Get());
        if (call.OriginalFunctionCallPrevented() || finished.overflow || finished.npc.Get() != receiver ||
            !listen_instance(world) || finished.items.empty() || !begin_roll(world))
            return;
        const auto source = RC::to_utf8_string(receiver->GetFullName());
        for (const auto& item : finished.items)
        {
            auto* actor = item.Get();
            if (item_in_world(actor, world)) record_drop(actor, source, finished.killer);
        }
    }

    int read_drop(const Lua& lua, bool physics_prop)
    {
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2)) return 0;
        const auto host = static_cast<Address>(lua.get_integer());
        const auto last = static_cast<std::uint64_t>(lua.get_integer());
        auto* world = static_cast<U::UWorld*>(reported_world.Get());
        auto* instance = listen_instance(world);
        auto* pawn = object(instance, STR("As Player FP"));
        if (!host || address(pawn) != host || !authority_in_level(static_cast<U::AActor*>(pawn), world)) return 0;
        for (const auto& drop : recent)
        {
            if (drop.sequence <= last || drop.physics_prop != physics_prop) continue;
            if (physics_prop)
            {
                auto* actor = static_cast<U::AActor*>(drop.actor.Get());
                if (!authority_in_level(actor, world)) continue;
                lua.set_integer(static_cast<std::int64_t>(drop.sequence));
                lua.set_string(drop.actor_name);
                lua.set_string(drop.class_path);
                return 3;
            }
            lua.set_integer(static_cast<std::int64_t>(drop.sequence));
            lua.set_integer(static_cast<std::int64_t>(drop.roll));
            lua.set_string(drop.npc);
            lua.set_string(drop.killer.empty() ? "-" : drop.killer);
            lua.set_string(drop.actor_name);
            lua.set_string(drop.class_path);
            lua.set_bool(item_in_world(drop.actor.Get(), world));
            return 7;
        }
        return 0;
    }

    int read(const Lua& lua) { return read_drop(lua, false); }
    int read_dig_prop(const Lua& lua) { return read_drop(lua, true); }
}

namespace silverfish::stock_enemy_drops
{
    bool track_observer_reward(U::UObject* observer, U::UObject* reward)
    {
        const auto* type = live(observer) ? observer->GetClassPrivate() : nullptr;
        if (!available() || !type || type->GetPathName() != STR("/Game/Classes/Anomalies/Observers_BP.Observers_BP_C"))
            return false;
        auto* world = static_cast<U::AActor*>(observer)->GetWorld();
        if (!listen_instance(world) || !authority_in_level(static_cast<U::AActor*>(observer), world) ||
            !item_in_world(reward, world) || !begin_roll(world)) return false;
        record_drop(reward, RC::to_utf8_string(observer->GetFullName()), {});
        return true;
    }

    bool verify_live_drop(Address host, Address actor, std::uint64_t requested,
                          shared_loot::EscrowOwner& context, U::UObject*& drop)
    {
        if (!available() || !host || !actor || !requested) return false;
        auto* world = static_cast<U::UWorld*>(reported_world.Get());
        auto* instance = listen_instance(world);
        auto* pawn = object(instance, STR("As Player FP"));
        auto* stock = object(instance, STR("SaveSlot_Ref"));
        if (address(pawn) != host ||
            !shared_loot::qualify_stock_drop_capture(world, host, address(stock), context)) return false;
        const auto found = std::ranges::find_if(recent, [requested](const auto& row) { return row.sequence == requested; });
        if (found == recent.end() || found->physics_prop) return false;
        auto* candidate = found->actor.Get();
        if (address(candidate) != actor || !item_in_world(candidate, world)) return false;
        drop = candidate;
        return true;
    }

    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        hooks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(before_spawn,
            {false, false, owner, STR("Observe the stock NPC loot frame")});
        hooks[1] = H::RegisterProcessLocalScriptFunctionPostCallback(after_spawn,
            {false, false, owner, STR("Record stock NPC drop actors")});
        hooks[2] = H::RegisterBeginPlayPostCallback(capture_begin,
            {false, false, owner, STR("Observe stock deferred item spawns")});
        if (std::ranges::find(hooks, H::ERROR_ID) != hooks.end()) uninstall_hooks();
        else installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        observing = {};
        dig_fixture = {};
        recent.clear();
        reported_world = U::FWeakObjectPtr{};
        for (auto& hook : hooks)
        {
            if (hook != H::ERROR_ID) H::UnregisterCallback(hook);
            hook = H::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadStockEnemyDrop", read);
        lua.register_function("SilverfishReadStockDigProp", read_dig_prop);
        if (dig_fixture_enabled()) lua.register_function("SilverfishArmStockDigRollFixture", arm_dig_fixture);
    }
}
