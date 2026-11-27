#include "inventory_view.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>

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
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto inventory_path = STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C");
    constexpr auto menu_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C:OpenMenu");
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto request_lease = std::chrono::seconds(2);

    struct Scope
    {
        std::uint64_t generation{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr connection;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr inventory_class;
        Unreal::UFunction* open_menu{};
        Unreal::FClassProperty* menu_class{};
        Clock::time_point expires{};
        Clock::time_point request_expires{};
        std::uint64_t serial{};
        bool accepting{};
        bool visible{};
        bool pending{};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 2> callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Scope scope;
    thread_local std::uint64_t next_serial{};

    bool flag(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 &&
               size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return flag("SILVERFISH_COOP_NATIVE_INVENTORY_VIEW") && flag("SILVERFISH_COOP_NATIVE_ZONE");
    }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* value = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FObjectProperty>(object, name);
        return value ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool local_proxy(Unreal::UObject* actor, Unreal::UObject* world)
    {
        if (!live(actor)) return false;
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        return destroying && !destroying->GetPropertyValueInContainer(actor) && role &&
               role->GetPropertyValueInContainer(actor) == 2 && static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    bool live_scope(const Scope& binding)
    {
        auto* world = binding.world.Get();
        auto* pawn = binding.pawn.Get();
        auto* controller = binding.controller.Get();
        auto* player = binding.player.Get();
        auto* driver = binding.driver.Get();
        auto* connection = binding.connection.Get();
        auto* instance = binding.instance.Get();
        if (!live(world) || !live(player) || !live(driver) || !live(connection) || !live(instance) ||
            !live(binding.inventory_class.Get()) || !local_proxy(pawn, world) || !local_proxy(controller, world))
            return false;
        auto* locals = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = locals ? Unreal::CastField<Unreal::FObjectProperty>(locals->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper slots(locals, locals->ContainerPtrToValuePtr<void>(instance));
        return slots.Num() == 1 && entry->GetObjectPropertyValue(slots.GetRawPtr()) == player &&
               object_field(world, STR("NetDriver")) == driver && object_field(driver, STR("World")) == world &&
               object_field(driver, STR("ServerConnection")) == connection && object_field(connection, STR("Driver")) == driver &&
               object_field(world, STR("OwningGameInstance")) == instance &&
               object_field(controller, STR("Player")) == player && object_field(player, STR("PlayerController")) == controller &&
               object_field(pawn, STR("Controller")) == controller && object_field(controller, STR("Pawn")) == pawn;
    }

    bool configure(Address pawn_address, bool accepting)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && reinterpret_cast<Address>(scope.pawn.Get()) == pawn_address &&
            live_scope(scope))
        {
            scope.expires = Clock::now() + lease;
            scope.accepting = accepting;
            if (!accepting) scope.pending = scope.visible = false;
            return true;
        }

        Scope next;
        auto* pawn_type = find<Unreal::UClass>(pawn_path);
        auto* controller_type = find<Unreal::UClass>(controller_path);
        auto* instance_type = find<Unreal::UClass>(instance_path);
        auto* local_player = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* net_connection = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        auto* widget = find<Unreal::UClass>(STR("/Script/UMG.UserWidget"));
        auto* inventory = find<Unreal::UClass>(inventory_path);
        if (!pawn_type || !controller_type || !instance_type || !local_player || !net_connection ||
            !widget || !inventory || !inventory->IsChildOf(widget)) return false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (reinterpret_cast<Address>(object) != pawn_address) return RC::LoopAction::Continue;
            if (live(object) && object->GetClassPrivate() == pawn_type) next.pawn = object;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.pawn.Get();
        if (!pawn) return false;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* connection = object_field(driver, STR("ServerConnection"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        if (!live(controller) || controller->GetClassPrivate() != controller_type ||
            !live(player) || !player->IsA(local_player) || !live(connection) || !connection->IsA(net_connection) ||
            !live(instance) || instance->GetClassPrivate() != instance_type) return false;

        next.controller = controller;
        next.player = player;
        next.world = world;
        next.driver = driver;
        next.connection = connection;
        next.instance = instance;
        next.inventory_class = inventory;
        next.open_menu = find<Unreal::UFunction>(menu_path);
        next.menu_class = field<Unreal::FClassProperty>(next.open_menu, STR("MenuClass"));
        auto* created = field<Unreal::FObjectProperty>(next.open_menu, STR("CreatedMenu"));
        if (!next.open_menu || next.open_menu->GetOuterPrivate() != controller_type || !next.menu_class ||
            !created || next.menu_class->GetOffset_Internal() != 0 || created->GetOffset_Internal() != sizeof(void*) ||
            !next.menu_class->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            next.menu_class->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) ||
            !created->HasAnyPropertyFlags(Unreal::CPF_OutParm) || created->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) ||
            next.menu_class->GetMetaClass().Get() != widget || created->GetPropertyClass().Get() != widget ||
            next.open_menu->GetNumParms() != 2 || next.open_menu->GetParmsSize() != 2 * sizeof(void*) ||
            next.open_menu->GetReturnProperty() || !live_scope(next))
            return false;

        next.generation = epoch;
        next.expires = Clock::now() + lease;
        next.accepting = accepting;
        scope = next;
        return true;
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, const void* parameters)
    {
        if (!parameters || !Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) ||
            scope.generation != generation.load(std::memory_order_acquire) || callee != scope.open_menu ||
            context != scope.controller.Get() || iteration.OriginalFunctionCallPrevented()) return;
        auto* type = scope.menu_class->GetObjectPropertyValue(scope.menu_class->ContainerPtrToValuePtr<void>(parameters));
        if (type != scope.inventory_class.Get() || !live_scope(scope)) return;

        iteration.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!iteration.OriginalFunctionCallPrevented() || !scope.accepting || now >= scope.expires) return;
        scope.pending = true;
        scope.visible = !scope.visible;
        scope.serial = ++next_serial;
        scope.request_expires = now + request_lease;
    }

    void intercept_event(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void* parameters)
    {
        intercept(iteration, context, callee, parameters);
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        intercept(iteration, context, frame.Node(), frame.Locals());
    }

    int set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_bool(2))
        {
            const auto pawn = lua.get_integer();
            const auto accepting = lua.get_bool();
            accepted = pawn > 0 && configure(static_cast<Address>(pawn), accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int take_request(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && scope.pending)
        {
            const auto now = Clock::now();
            accepted = installed.load(std::memory_order_acquire) &&
                scope.generation == generation.load(std::memory_order_acquire) &&
                scope.accepting && now < scope.expires && now < scope.request_expires && live_scope(scope);
            scope.pending = false;
            if (!accepted) scope.visible = false;
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? static_cast<std::int64_t>(scope.serial) : 0);
        lua.set_bool(accepted && scope.visible);
        return 3;
    }

    int clear(const Lua&)
    {
        silverfish::native_inventory_view::clear_scope();
        return 0;
    }

}

namespace silverfish::native_inventory_view
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            intercept_event, {false, false, owner, STR("Guest stock inventory menu view")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            intercept_script, {false, false, owner, STR("Guest stock inventory key view")});
        if (callbacks[0] == Hook::ERROR_ID || callbacks[1] == Hook::ERROR_ID)
        {
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& callback : callbacks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeInventoryViewScope", set_scope);
        lua.register_function("SilverfishTakeNativeInventoryViewRequest", take_request);
        lua.register_function("SilverfishClearNativeInventoryViewScope", clear);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
