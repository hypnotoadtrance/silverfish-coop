#include "broker_input.hpp"
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
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01");
    constexpr auto station_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.ItemRequest_TradeStation_BP_C_1");
    constexpr auto station_class = STR("/Game/Classes/Actors/ItemRequest_TradeStation_BP.ItemRequest_TradeStation_BP_C");
    constexpr auto callback_path = STR("/Game/Classes/Actors/ItemRequest_TradeStation_BP.ItemRequest_TradeStation_BP_C:SendInteractionToActor");
    constexpr auto buyer_enum = STR("/Game/Classes/Libraries/Enums_Lib/QuestTurnIns_Enum.QuestTurnIns_Enum");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr std::uint8_t broker_buyer = 1;
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto max_sequence = (std::uint64_t{1} << 53) - 1;

    struct Scope
    {
        U::FWeakObjectPtr pawn{}, controller{}, player{}, instance{}, driver{}, connection{}, world{}, station{}, box{};
        U::UFunction* callback{};
        U::FObjectProperty *interactor{}, *component{};
        U::FByteProperty* buyer{};
        Clock::time_point expires{}, pending_expires{};
        std::uint64_t generation{}, pending{};
        bool accepting{};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    Scope scope;
    std::uint64_t sequence{};

    bool flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return flag("SILVERFISH_COOP_NATIVE_ZONE") && flag("SILVERFISH_COOP_NATIVE_BROKER_INPUT");
    }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* container, const U::TCHAR* name)
    {
        auto* property = container ? U::CastField<T>(container->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* container, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(container, name), container);
    }

    bool is_false(U::UObject* container, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(container, name);
        return property && !property->GetPropertyValueInContainer(container);
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, U::UClass* type)
    {
        return live(value) && type && value->GetClassPrivate() == type;
    }

    bool in_world(U::UObject* actor, U::UObject* world)
    {
        return live(actor) && is_false(actor, STR("bActorIsBeingDestroyed")) &&
            static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool autonomous(U::UObject* actor)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return role && role->GetPropertyValueInContainer(actor) == 2;
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool live_scope(const Scope& current)
    {
        auto* pawn = current.pawn.Get();
        auto* controller = current.controller.Get();
        auto* player = current.player.Get();
        auto* instance = current.instance.Get();
        auto* world = current.world.Get();
        auto* driver = current.driver.Get();
        auto* connection = current.connection.Get();
        auto* station = current.station.Get();
        auto* box = current.box.Get();
        if (!live(world) || !live(instance) || !live(player) || !live(driver) || !live(connection) || !live(box) ||
            !in_world(pawn, world) || !autonomous(pawn) || !in_world(controller, world) || !autonomous(controller) ||
            !in_world(station, world) || static_cast<U::AActor*>(station)->GetLevel() != object(world, STR("PersistentLevel")) ||
            box->GetOuterPrivate() != station || object(station, STR("Box")) != box || !current.buyer ||
            current.buyer->GetPropertyValueInContainer(station) != broker_buyer ||
            object(world, STR("OwningGameInstance")) != instance || object(world, STR("NetDriver")) != driver ||
            object(driver, STR("World")) != world || object(driver, STR("ServerConnection")) != connection ||
            object(connection, STR("Driver")) != driver || object(controller, STR("Player")) != player ||
            object(player, STR("PlayerController")) != controller || object(controller, STR("Pawn")) != pawn ||
            object(pawn, STR("Controller")) != controller) return false;
        auto* local = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = local ? U::CastField<U::FObjectProperty>(local->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper slots(local, local->ContainerPtrToValuePtr<void>(instance));
        return slots.Num() == 1 && read(entry, slots.GetRawPtr(0)) == player;
    }

    bool qualify_action(U::FObjectProperty* argument, U::UClass* type, int offset)
    {
        return argument && type && argument->GetPropertyClass().Get() == type &&
            argument->GetOffset_Internal() == offset && argument->HasAnyPropertyFlags(U::CPF_Parm) &&
            !argument->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm);
    }

    bool configure(Address requested, bool accepting)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && address(scope.pawn.Get()) == requested && live_scope(scope))
        {
            scope.accepting = accepting;
            if (!accepting) scope.pending = 0;
            scope.expires = Clock::now() + lease;
            return true;
        }

        scope = {};
        Scope next;
        auto* pawn_type = find<U::UClass>(pawn_class);
        auto* controller_type = find<U::UClass>(controller_class);
        auto* player_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* connection_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        auto* station_type = find<U::UClass>(station_class);
        auto* world = find<U::UWorld>(world_path);
        auto* station = find<U::UObject>(station_path);
        auto* box = object(station, STR("Box"));
        auto* buyer_type = find<U::UEnum>(buyer_enum);
        if (!pawn_type || !controller_type || !player_type || !connection_type || !buyer_type ||
            !live(world) || !exact(station, station_type) || !exact(box, find<U::UClass>(STR("/Script/Engine.BoxComponent"))))
            return false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != requested) return RC::LoopAction::Continue;
            if (exact(candidate, pawn_type)) next.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* controller = object(next.pawn.Get(), STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = object(driver, STR("ServerConnection"));
        if (!exact(controller, controller_type) || !live(player) || !player->IsA(player_type) ||
            !exact(instance, find<U::UClass>(instance_class)) || !live(driver) ||
            !live(connection) || !connection->IsA(connection_type)) return false;
        next.controller = controller;
        next.player = player;
        next.instance = instance;
        next.driver = driver;
        next.connection = connection;
        next.world = world;
        next.station = station;
        next.box = box;
        next.buyer = field<U::FByteProperty>(station, STR("Buyer"));
        next.callback = find<U::UFunction>(callback_path);
        next.interactor = field<U::FObjectProperty>(next.callback, STR("Player"));
        next.component = field<U::FObjectProperty>(next.callback, STR("HitComponent"));
        if (!next.buyer || next.buyer->GetEnum().Get() != buyer_type || !next.callback ||
            next.callback->GetOuterPrivate() != station_type || next.callback->GetNumParms() != 2 ||
            next.callback->GetParmsSize() != sizeof(void*) * 2 || next.callback->GetReturnProperty() ||
            next.callback->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !qualify_action(next.interactor, pawn_type, 0) ||
            !qualify_action(next.component, find<U::UClass>(STR("/Script/Engine.SceneComponent")), sizeof(void*)) ||
            !live_scope(next)) return false;
        next.generation = epoch;
        next.expires = Clock::now() + lease;
        next.accepting = accepting;
        scope = next;
        return true;
    }

    bool available()
    {
        return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw();
    }

    void intercept(Iteration& iteration, U::UObject* actor, U::UFunction* action, const void* parameters)
    {
        if (!available() || !actor || actor != scope.station.Get() || !action || action != scope.callback ||
            !parameters || iteration.OriginalFunctionCallPrevented() ||
            scope.generation != generation.load(std::memory_order_acquire) || !live_scope(scope)) return;
        auto* pawn = scope.pawn.Get();
        if (read(scope.interactor, parameters) != pawn) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        // Broker handles this physical press. Do not dispatch a second generic
        // cooperative interaction from the same stock wrapper.
        const bool from_input = silverfish::native_interaction_input::consume_current_press(pawn);
        const auto now = Clock::now();
        auto* box = scope.box.Get();
        if (!from_input || !scope.accepting || now >= scope.expires || sequence == max_sequence ||
            (scope.pending && now < scope.pending_expires) || read(scope.component, parameters) != box ||
            object(pawn, STR("InteractionTarget_Component")) != box || !is_false(pawn, STR("Dead")) ||
            !is_false(scope.controller.Get(), STR("Loading"))) return;
        scope.pending = ++sequence;
        scope.pending_expires = now + lease;
    }

    int set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (available() && lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_bool(2))
        {
            const auto pawn = lua.get_integer();
            const auto accepting = lua.get_bool();
            accepted = pawn > 0 && configure(static_cast<Address>(pawn), accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int take(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        if (requested <= 0 || address(scope.pawn.Get()) != static_cast<Address>(requested) || !scope.pending) return 0;
        const auto now = Clock::now();
        const auto pending = scope.pending;
        scope.pending = 0;
        if (now >= scope.expires || now >= scope.pending_expires || !scope.accepting ||
            scope.generation != generation.load(std::memory_order_acquire) || !live_scope(scope)) return 0;
        lua.set_integer(static_cast<std::int64_t>(pending));
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::native_broker_input::clear_scope();
        return 0;
    }
}

namespace silverfish::native_broker_input
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, U::UObject* actor, U::UFunction* action, void* parameters) {
                intercept(iteration, actor, action, parameters);
            }, {false, false, owner, STR("Route stock local Broker redemption")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, U::UObject* actor, U::FFrame& frame, void*) {
                if (frame.Object() == actor) intercept(iteration, actor, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Route Blueprint guest Broker redemption")});
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
        lua.register_function("SilverfishSetNativeBrokerInputScope", set_scope);
        lua.register_function("SilverfishTakeNativeBrokerInputPress", take);
        lua.register_function("SilverfishClearNativeBrokerInputScope", clear);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (U::IsInGameThreadRaw()) scope = {};
    }
}
