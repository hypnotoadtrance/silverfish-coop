#include "stock_enemy_drops.hpp"
#include "enemy_loot.hpp"
#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdlib>
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
    };

    Observation observing;
    U::FWeakObjectPtr reported_world;
    std::deque<Drop> recent;
    std::uint64_t sequence{}, roll{};
    std::atomic<bool> installed{};
    std::array<H::GlobalCallbackId, 3> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};

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

    void record_drop(U::UObject* actor, const std::string& source, const std::string& killer)
    {
        if (recent.size() == max_recent_drops) recent.pop_front();
        recent.push_back({actor, ++sequence, roll, source, killer, RC::to_utf8_string(actor->GetFullName()),
            RC::to_utf8_string(actor->GetClassPrivate()->GetPathName())});
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
        if (!available() || !observing.frame || call.OriginalFunctionCallPrevented()) return;
        auto* locals = observing.frame->Locals();
        if (locals && std::ranges::any_of(observing.deferred, [=](auto* property) {
                return property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(locals)) == actor;
            })) collect(actor);
    }

    void before_spawn(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        static const U::FName spawn_name{STR("SpawnLoot")};
        auto* function = frame.Node();
        if (!available() || observing.frame || !function || function->GetFName() != spawn_name ||
            call.OriginalFunctionCallPrevented() || frame.Object() != receiver || !live(receiver)) return;
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
        if (!available() || !observing.frame) return;
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

    int read(const Lua& lua)
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
            if (drop.sequence <= last) continue;
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
        if (found == recent.end()) return false;
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
        if (enabled()) lua.register_function("SilverfishReadStockEnemyDrop", read);
    }
}
