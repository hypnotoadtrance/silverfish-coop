#include "shop_view.hpp"
#include "interaction_input.hpp"

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
    struct Merchant
    {
        const Unreal::TCHAR* world;
        const Unreal::TCHAR* actor;
        const Unreal::TCHAR* data;
    };

    constexpr std::array merchants{
        Merchant{
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Shop_BP_C_2"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/Observitory_ShopData.Observitory_ShopData"),
        },
        Merchant{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P:PersistentLevel.Shop_BP_C_2"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/ScavBar_ShopData.ScavBar_ShopData"),
        },
        Merchant{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.Shop_BP_C_1"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/BlacMarket_ShopData.BlacMarket_ShopData"),
        },
    };
    constexpr auto shop_class = STR("/Game/Classes/Actors/Shop_BP.Shop_BP_C");
    constexpr auto action_path = STR("/Game/Classes/Actors/Shop_BP.Shop_BP_C:SendInteractionToActor");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto max_sequence = (std::uint64_t{1} << 53) - 1;

    struct Scope
    {
        std::uint64_t generation{};
        bool host{};
        bool accepting{};
        Unreal::FWeakObjectPtr pawn, controller, player, instance, world, driver, connection, shop, box, data;
        Unreal::UClass* pawn_type{};
        Unreal::UClass* controller_type{};
        Unreal::UClass* connection_type{};
        Unreal::UFunction* action{};
        Unreal::FObjectProperty* player_arg{};
        Unreal::FObjectProperty* component_arg{};
        Clock::time_point expires{};
        Clock::time_point request_expires{};
        std::uint64_t pending{};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 2> callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Scope scope;
    thread_local std::uint64_t sequence{};

    bool flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return flag("SILVERFISH_COOP_NATIVE_ZONE") && flag("SILVERFISH_COOP_NATIVE_SHOP_VIEW");
    }

    template<typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Unreal::UObject* read(Unreal::FObjectProperty* property, const void* data)
    {
        return property && data ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(data)) : nullptr;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        return read(field<Unreal::FObjectProperty>(object, name), object);
    }

    bool is_false(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FBoolProperty>(object, name);
        return property && !property->GetPropertyValueInContainer(object);
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(Unreal::UObject* object, Unreal::UClass* type)
    {
        return type && live(object) && object->GetClassPrivate() == type;
    }

    bool actor_in_world(Unreal::UObject* object, Unreal::UObject* world)
    {
        return live(object) && is_false(object, STR("bActorIsBeingDestroyed")) &&
            static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    bool role(Unreal::UObject* object, std::uint8_t expected)
    {
        auto* value = field<Unreal::FByteProperty>(object, STR("Role"));
        return value && value->GetPropertyValueInContainer(object) == expected;
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    bool live_scope(const Scope& binding)
    {
        auto* pawn = binding.pawn.Get();
        auto* controller = binding.controller.Get();
        auto* player = binding.player.Get();
        auto* instance = binding.instance.Get();
        auto* world = binding.world.Get();
        auto* driver = binding.driver.Get();
        auto* shop = binding.shop.Get();
        auto* box = binding.box.Get();
        auto* data = binding.data.Get();
        const auto expected_role = binding.host ? 3 : 2;
        if (!live(world) || !live(instance) || !live(player) || !live(driver) || !live(box) || !live(data) ||
            !actor_in_world(pawn, world) || !actor_in_world(controller, world) || !actor_in_world(shop, world) ||
            !role(pawn, expected_role) || !role(controller, expected_role) ||
            static_cast<Unreal::AActor*>(shop)->GetLevel() != object_field(world, STR("PersistentLevel")) ||
            box->GetOuterPrivate() != shop || object_field(shop, STR("Box")) != box ||
            object_field(shop, STR("MerchantData")) != data || !is_false(shop, STR("IsCheatShop")) ||
            object_field(world, STR("OwningGameInstance")) != instance ||
            object_field(world, STR("NetDriver")) != driver || object_field(driver, STR("World")) != world ||
            object_field(controller, STR("Player")) != player || object_field(player, STR("PlayerController")) != controller ||
            object_field(controller, STR("Pawn")) != pawn || object_field(pawn, STR("Controller")) != controller ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection"))) return false;
        auto* local = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = local ? Unreal::CastField<Unreal::FObjectProperty>(local->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper local_players(local, local->ContainerPtrToValuePtr<void>(instance));
        if (local_players.Num() != 1 || read(entry, local_players.GetRawPtr()) != player) return false;
        auto* server = object_field(driver, STR("ServerConnection"));
        if (binding.host) return !server;
        return live(server) && server == binding.connection.Get() && server->IsA(binding.connection_type) &&
            object_field(server, STR("Driver")) == driver;
    }

    bool current()
    {
        return installed.load(std::memory_order_acquire) && Unreal::IsInGameThreadRaw() &&
            scope.generation == generation.load(std::memory_order_acquire) && scope.action && live_scope(scope);
    }

    bool configure(Address pawn_address, bool host, bool accepting)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && scope.host == host && address(scope.pawn.Get()) == pawn_address && live_scope(scope))
        {
            scope.accepting = accepting;
            if (!accepting) scope.pending = 0;
            scope.expires = Clock::now() + lease;
            return true;
        }
        Scope next;
        next.host = host;
        next.pawn_type = find<Unreal::UClass>(pawn_class);
        next.controller_type = find<Unreal::UClass>(controller_class);
        next.connection_type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        auto* local_player_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* scene_type = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
        auto* shop_type = find<Unreal::UClass>(shop_class);
        if (!next.pawn_type || !next.controller_type || !next.connection_type || !local_player_type ||
            !scene_type || !shop_type) return false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (address(object) != pawn_address) return RC::LoopAction::Continue;
            if (exact(object, next.pawn_type)) next.pawn = object;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.pawn.Get();
        if (!live(pawn)) return false;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        if (!live(world)) return false;

        const Merchant* profile = nullptr;
        const auto world_name = world->GetPathName();
        for (const auto& merchant : merchants)
        {
            if (world_name == merchant.world)
            {
                profile = &merchant;
                break;
            }
        }
        if (!profile || find<Unreal::UWorld>(profile->world) != world) return false;
        auto* shop = find<Unreal::UObject>(profile->actor);
        auto* data = find<Unreal::UObject>(profile->data);
        auto* box = object_field(shop, STR("Box"));
        if (!exact(shop, shop_type) || !live(data) ||
            !exact(box, find<Unreal::UClass>(STR("/Script/Engine.BoxComponent")))) return false;
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* driver = object_field(world, STR("NetDriver"));
        if (!exact(controller, next.controller_type) || !live(player) || !player->IsA(local_player_type) ||
            !exact(instance, find<Unreal::UClass>(instance_class)) || !live(driver)) return false;

        next.world = world;
        next.shop = shop;
        next.box = box;
        next.data = data;
        next.controller = controller;
        next.player = player;
        next.instance = instance;
        next.driver = driver;
        next.connection = object_field(driver, STR("ServerConnection"));
        next.action = find<Unreal::UFunction>(action_path);
        next.player_arg = field<Unreal::FObjectProperty>(next.action, STR("Player"));
        next.component_arg = field<Unreal::FObjectProperty>(next.action, STR("HitComponent"));
        const auto input = [](Unreal::FObjectProperty* property, Unreal::UClass* type, int offset) {
            return property && property->GetPropertyClass().Get() == type && property->GetOffset_Internal() == offset &&
                property->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
                !property->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm);
        };
        if (!next.action || next.action->GetOuterPrivate() != shop_type || next.action->GetNumParms() != 2 ||
            next.action->GetParmsSize() != 2 * sizeof(void*) || next.action->GetReturnProperty() ||
            !input(next.player_arg, next.pawn_type, 0) || !input(next.component_arg, scene_type, sizeof(void*)) ||
            !live_scope(next)) return false;

        next.generation = epoch;
        next.accepting = accepting;
        next.expires = Clock::now() + lease;
        scope = next;
        return true;
    }

    bool remote_guest(Unreal::UObject* pawn)
    {
        auto* world = scope.world.Get();
        if (!exact(pawn, scope.pawn_type) || pawn == scope.pawn.Get() || !actor_in_world(pawn, world) || !role(pawn, 3))
            return false;
        auto* controller = object_field(pawn, STR("Controller"));
        auto* connection = object_field(controller, STR("Player"));
        auto* driver = scope.driver.Get();
        if (!exact(controller, scope.controller_type) || !actor_in_world(controller, world) || !role(controller, 3) ||
            object_field(controller, STR("Pawn")) != pawn || !live(connection) || !connection->IsA(scope.connection_type) ||
            object_field(connection, STR("Driver")) != driver || object_field(connection, STR("PlayerController")) != controller ||
            object_field(connection, STR("OwningActor")) != controller) return false;
        auto* array = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* entry = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper clients(array, array->ContainerPtrToValuePtr<void>(driver));
        if (clients.Num() < 1 || clients.Num() > 4) return false;
        int matches = 0;
        for (int i = 0; i < clients.Num(); ++i)
            if (read(entry, clients.GetRawPtr(i)) == connection) ++matches;
        return matches == 1;
    }

    void intercept(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* action, const void* parameters)
    {
        if (!Unreal::IsInGameThreadRaw() || !actor || actor != scope.shop.Get() || !action || action != scope.action || !parameters ||
            iteration.OriginalFunctionCallPrevented() || !current()) return;
        auto* pawn = read(scope.player_arg, parameters);
        if (!pawn || (scope.host ? !remote_guest(pawn) : pawn != scope.pawn.Get())) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented() || scope.host) return;

        // A handled shop press must never become a second sidecar interact.
        const bool from_input = silverfish::native_interaction_input::consume_current_press(pawn);
        const auto now = Clock::now();
        auto* box = scope.box.Get();
        if (!from_input || !scope.accepting || now >= scope.expires || sequence == max_sequence ||
            read(scope.component_arg, parameters) != box || object_field(pawn, STR("InteractionTarget_Component")) != box)
            return;
        scope.pending = ++sequence;
        scope.request_expires = now + lease;
    }

    void event_pre(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* action, void* parameters)
    {
        intercept(iteration, actor, action, parameters);
    }

    void script_pre(Iteration& iteration, Unreal::UObject* actor, Unreal::FFrame& frame, void*)
    {
        intercept(iteration, actor, frame.Node(), frame.Locals());
    }

    int set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 3 && lua.is_integer(1) && lua.is_bool(2) && lua.is_bool(3))
        {
            const auto pawn = lua.get_integer();
            const bool host = lua.get_bool(), accepting = lua.get_bool();
            accepted = pawn > 0 && configure(static_cast<Address>(pawn), host, !host && accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int take(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_integer(1) || !current() || scope.host ||
            static_cast<Address>(lua.get_integer()) != address(scope.pawn.Get())) return 0;
        const auto now = Clock::now();
        if (!scope.accepting || !scope.pending || now >= scope.request_expires || now >= scope.expires) return 0;
        lua.set_integer(static_cast<std::int64_t>(scope.pending));
        scope.pending = 0;
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::native_shop_view::clear_scope();
        return 0;
    }
}

namespace silverfish::native_shop_view
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            event_pre, {false, false, owner, STR("Guard exact stock guest merchant")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            script_pre, {false, false, owner, STR("Capture exact stock guest merchant interaction")});
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
        lua.register_function("SilverfishSetNativeShopViewScope", set_scope);
        lua.register_function("SilverfishTakeNativeShopViewPress", take);
        lua.register_function("SilverfishClearNativeShopViewScope", clear);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
