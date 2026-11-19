#include "loot_crate_input.hpp"
#include "interaction_input.hpp"
#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/NameTypes.hpp>
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

    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto crate_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.LootCrate_Small_BP_C_3");
    constexpr auto crate_type_path = STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C");
    constexpr auto open_path = STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:SendInteractionToActor");
    constexpr auto script_path = STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:ExecuteUbergraph_LootCrate_Small_BP");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto receipt_lifetime = std::chrono::seconds(4);
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;
    constexpr int open_entry = 0x376;
    constexpr int spawn_salt = -1911374248;

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* method, const U::TCHAR* name, int offset)
    {
        auto* prop = field<T>(method, name);
        return prop && prop->GetOffset_Internal() == offset && prop->HasAnyPropertyFlags(U::CPF_Parm) &&
            !prop->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) &&
            prop->GetSize() + offset <= method->GetParmsSize() ? prop : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container))
                                     : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(owner, name), owner);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* prop = field<U::FBoolProperty>(owner, name);
        return prop && prop->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool actor_in_world(U::UObject* actor, U::UWorld* world)
    {
        return live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false) &&
            static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool has_role(U::UObject* actor, int role)
    {
        auto* value = field<U::FByteProperty>(actor, STR("Role"));
        return value && value->GetPropertyValueInContainer(actor) == role;
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_LOOT") == 0 &&
            size == value.size() && value[0] == '1';
    }

    struct Local
    {
        U::UObject* pawn{};
        bool host{};
    };

    Local local_owner(U::UWorld* world)
    {
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        auto* server_slot = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (!live(driver) || object(driver, STR("World")) != world || !server_slot || !exact(instance, instance_path))
            return {};
        auto* server = read(server_slot, driver);
        auto* net_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        const bool host = server == nullptr;
        if (!host && (!live(server) || !net_type || !server->IsA(net_type) || object(server, STR("Driver")) != driver))
            return {};

        auto* players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = players ? U::CastField<U::FObjectProperty>(players->GetInner()) : nullptr;
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!entry || !local_type) return {};
        U::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        if (locals.Num() != 1) return {};
        auto* player = read(entry, locals.GetRawPtr());
        auto* controller = object(player, STR("PlayerController"));
        auto* pawn = object(controller, STR("Pawn"));
        const auto role = host ? 3 : 2;
        if (!live(player) || !player->IsA(local_type) || !exact(controller, controller_path) ||
            !exact(pawn, pawn_path) || !actor_in_world(controller, world) || !actor_in_world(pawn, world) ||
            !has_role(controller, role) || !has_role(pawn, role) ||
            object(controller, STR("Player")) != player || object(pawn, STR("Controller")) != controller)
            return {};
        return {pawn, host};
    }

    U::UWorld* stock_world(U::UObject* crate, std::string_view* failure = nullptr)
    {
        const auto refuse = [&](std::string_view reason) -> U::UWorld* {
            if (failure) *failure = reason;
            return nullptr;
        };
        if (!live(crate)) return refuse("crate_missing");
        if (!exact(crate, crate_type_path) || crate->GetPathName() != crate_path) return refuse("crate_identity");
        auto* actor = static_cast<U::AActor*>(crate);
        auto* world = actor->GetWorld();
        if (!live(world) || world->GetPathName() != world_path) return refuse("world_identity");
        if (!actor_in_world(crate, world)) return refuse("crate_destroying");
        auto* salt = field<U::FIntProperty>(crate, STR("SpawnSalt"));
        if (!salt || salt->GetPropertyValueInContainer(crate) != spawn_salt) return refuse("crate_salt");
        if (actor->GetLevel() != object(world, STR("PersistentLevel"))) return refuse("crate_level");
        return world;
    }

    U::UObject* stock_box(U::UObject* crate)
    {
        auto* box = object(crate, STR("Box"));
        return exact(box, STR("/Script/Engine.BoxComponent")) && box->GetOuterPrivate() == crate ? box : nullptr;
    }

    struct PresentationBox
    {
        std::string_view state{"scope_inactive"};
        U::UObject* box{};
    };

    PresentationBox presentation_box(U::UObject* crate)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.BoxComponent"));
        auto* property = field<U::FObjectProperty>(crate, STR("Box"));
        if (!type || !property || property->GetPropertyClass().Get() != type) return {"box_field"};
        auto* box = read(property, crate);
        if (!box) return {"missing"};
        if (!live(box) || U::FWeakObjectPtr(box).Get(false) != box) return {"retired"};
        if (box->GetClassPrivate() != type || box->GetOuterPrivate() != crate) return {"box_identity"};

        // K2_DestroyComponent removes the object from the actor before the
        // Blueprint property necessarily clears. Only registered ownership
        // can supply the presentation or fixture a live query component.
        const auto owned = static_cast<U::AActor*>(crate)->K2_GetComponentsByClass(type);
        if (owned.Num() < 0 || owned.Num() > 16) return {"box_extent"};
        for (int index = 0; index < owned.Num(); ++index)
            if (owned[index] == box) return {"live", box};
        return {"retired"};
    }


    bool in_reach(U::UObject* pawn, U::UObject* box)
    {
        auto* range = field<U::FDoubleProperty>(pawn, STR("InteractionRange"));
        auto* getter = find<U::UFunction>(STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"));
        auto* output = field<U::FStructProperty>(getter, STR("ReturnValue"));
        auto* type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        std::array<double, 3> point{};
        if (!range || !output || !type || output != getter->GetReturnProperty() || getter->GetNumParms() != 1 ||
            output->GetStruct().Get() != type || output->GetOffset_Internal() != 0 ||
            getter->GetParmsSize() != sizeof(point) || !getter->HasAnyFunctionFlags(U::FUNC_Native)) return false;
        const double reach = range->GetPropertyValueInContainer(pawn);
        if (!std::isfinite(reach) || reach <= 0 || reach > 1000) return false;
        box->ProcessEvent(getter, point.data());
        auto location = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        const double x = location.GetX() - point[0];
        const double y = location.GetY() - point[1];
        const double z = location.GetZ() - point[2];
        const double square = x * x + y * y + z * z;
        return std::isfinite(square) && square <= reach * reach;
    }

    struct Scope
    {
        U::FWeakObjectPtr crate{}, pawn{};
        bool host{};
        Clock::time_point expires{}, receipt{};
        std::uint64_t observed{};
    } scope;
    std::uint64_t sequence{};
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool ready() { return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire); }

    bool stock_function(U::UObject* crate, U::UFunction* method, int count, int size)
    {
        return method->GetOuterPrivate() == crate->GetClassPrivate() && method->GetNumParms() == count &&
            method->GetParmsSize() == size && !method->GetReturnProperty() &&
            !method->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net);
    }

    bool same_scope(U::UObject* crate, Local local)
    {
        return crate && crate == scope.crate.Get() && scope.host == local.host && local.pawn && local.pawn == scope.pawn.Get();
    }

    void intercept(Iteration& call, U::UObject* crate, U::UFunction* method, const void* args)
    {
        if (!ready() || !crate || !method || !args || call.OriginalFunctionCallPrevented()) return;
        static const U::FName open_name{STR("SendInteractionToActor")};
        static const U::FName script_name{STR("ExecuteUbergraph_LootCrate_Small_BP")};
        const auto name = method->GetFName();
        if ((name != open_name && name != script_name) || (name == script_name && method != find<U::UFunction>(script_path)) ||
            (name == open_name && method != find<U::UFunction>(open_path))) return;
        auto* world = stock_world(crate);
        if (!world) return;
        const auto local = local_owner(world);
        if (!local.pawn) return;

        if (name == script_name)
        {
            auto* entry = parameter<U::FIntProperty>(method, STR("EntryPoint"), 0);
            if (stock_function(crate, method, 1, sizeof(std::int32_t)) && entry &&
                entry->GetPropertyValueInContainer(args) == open_entry &&
                !silverfish::shared_loot::permits_stock_script(crate, method)) call.PreventOriginalFunctionCall();
            return;
        }
        auto* player = parameter<U::FObjectProperty>(method, STR("Player"), 0);
        auto* component = parameter<U::FObjectProperty>(method, STR("HitComponent"), sizeof(void*));
        if (!stock_function(crate, method, 2, 2 * sizeof(void*)) || !player || !component ||
            player->GetPropertyClass().Get() != find<U::UClass>(pawn_path) ||
            component->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.SceneComponent"))) return;
        auto* pawn = read(player, args);
        auto* hit = read(component, args);
        if (local.host && silverfish::shared_loot::permits_stock_interaction(crate, pawn, hit, method)) return;

        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || pawn != local.pawn ||
            !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        const auto now = Clock::now();
        if (!same_scope(crate, local) || now >= scope.expires || sequence == max_sequence ||
            !boolean(crate, STR("Looted"), false) || !boolean(pawn, STR("Holding_Interact"), true) ||
            !hit || hit != stock_box(crate) || object(pawn, STR("InteractionTarget_Component")) != hit ||
            !in_reach(pawn, hit)) return;
        scope.observed = ++sequence;
        scope.receipt = now + receipt_lifetime;
    }

    int set_scope(const Lua& lua)
    {
        std::string_view status{"hook_unavailable"};
        if (ready())
        {
            status = "scope_arguments";
            if (lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_bool(2))
            {
                const auto pawn = lua.get_integer();
                const bool host = lua.get_bool();
                auto* crate = find<U::AActor>(crate_path);
                auto* world = stock_world(crate, &status);
                if (world)
                {
                    status = "local_owner";
                    const auto local = local_owner(world);
                    if (local.pawn && pawn == reinterpret_cast<std::intptr_t>(local.pawn) && host == local.host)
                    {
                        if (!same_scope(crate, local)) scope = {crate, local.pawn, host};
                        scope.expires = Clock::now() + lease;
                        status = "bound";
                    }
                }
            }
        }
        lua.set_bool(status == "bound");
        lua.set_string(status);
        return 2;
    }

    int status(const Lua& lua)
    {
        auto* crate = ready() ? scope.crate.Get() : nullptr;
        auto* world = stock_world(crate);
        const auto now = Clock::now();
        const bool active = world && now < scope.expires && same_scope(crate, local_owner(world));
        const auto box = active ? presentation_box(crate) : PresentationBox{};
        lua.set_bool(active);
        lua.set_integer(active && now < scope.receipt ? static_cast<std::int64_t>(scope.observed) : 0);
        lua.set_string(box.state);
        lua.set_integer(reinterpret_cast<std::intptr_t>(box.box));
        return 4;
    }

    int clear(const Lua&)
    {
        if (U::IsInGameThreadRaw()) scope = {};
        return 0;
    }
}

namespace silverfish::native_loot_crate_input
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* crate, U::UFunction* method, void* parameters) {
                intercept(call, crate, method, parameters);
            }, {false, false, STR("SilverfishCoopNative"), STR("Route qualified CrossRoads crate input")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* crate, U::FFrame& frame, void*) {
                intercept(call, crate, frame.Node(), frame.Locals());
            }, {false, false, STR("SilverfishCoopNative"), STR("Route qualified CrossRoads crate script")});
        if (std::ranges::find(callbacks, Hook::ERROR_ID) != callbacks.end())
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
        lua.register_function("SilverfishSetNativeLootCrateInputScope", set_scope);
        lua.register_function("SilverfishReadNativeLootCrateInput", status);
        lua.register_function("SilverfishClearNativeLootCrateInput", clear);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) scope = {};
    }
}
