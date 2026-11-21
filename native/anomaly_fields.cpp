#include "anomaly_fields.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <string_view>

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
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Iteration = H::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto field_class = STR("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C");
    constexpr auto trap_overlap = STR("BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto electrode_class = STR("/Game/Classes/Anomalies/Electrode_BP.Electrode_BP_C");
    constexpr std::array<std::wstring_view, 4> replicated_classes{
        STR("/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C"),
        STR("/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C"),
        STR("/Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C"),
        electrode_class,
    };
    std::array<H::GlobalCallbackId, 5> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};
    U::FName spawn_field, overlap, electrode;
    std::array<U::FName, 6> electrode_events;
    thread_local bool guest_session{};
    thread_local U::FWeakObjectPtr observed_world;
    thread_local std::int64_t skipped{}, blocked_triggers{};

    struct PendingLoad
    {
        U::FWeakObjectPtr driver, connection;
        U::FWorldContext* context{};
    };
    thread_local PendingLoad loading;

    bool installed()
    {
        for (const auto id : hooks) if (id == H::ERROR_ID) return false;
        return true;
    }

    template <typename T>
    T* property(U::UObject* owner, const U::TCHAR* name)
    {
        return owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* member = property<U::FObjectProperty>(owner, name);
        return member ? member->GetObjectPropertyValue(member->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool trap_class(U::UObject* kind)
    {
        if (!kind) return false;
        const auto path = kind->GetPathName();
        return std::ranges::find(replicated_classes, std::wstring_view{path}) != replicated_classes.end();
    }

    bool replicated_field(U::UObject* field)
    {
        auto* roster = property<U::FArrayProperty>(field, STR("Anomalies"));
        auto* entry = roster ? U::CastField<U::FClassProperty>(roster->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper classes(roster, roster->ContainerPtrToValuePtr<void>(field));
        if (classes.Num() != 1) return false;
        return trap_class(entry->GetObjectPropertyValue(classes.GetRawPtr(0)));
    }

    bool server_connection(U::UObject* driver, U::UObject* connection)
    {
        return driver && connection && object(connection, STR("Driver")) == driver;
    }

    bool guest_world(U::UWorld* world)
    {
        if (!guest_session || !world) return false;
        auto* driver = object(world, STR("NetDriver"));
        if (object(driver, STR("World")) == world && server_connection(driver, object(driver, STR("ServerConnection"))))
            return true;
        // A guest can enter stock BeginPlay before the new world's driver has been published.
        auto* pending = loading.driver.Get();
        return loading.context && (!driver || driver == pending) && server_connection(pending, loading.connection.Get());
    }

    void intercept(Iteration& call, U::UObject* actor, U::UFunction* function)
    {
        if (!guest_session || !actor || !function) return;
        const auto name = function->GetFName();
        if ((name != spawn_field && name != overlap &&
             std::find(electrode_events.begin(), electrode_events.end(), name) == electrode_events.end()) ||
            !U::IsInGameThreadRaw() ||
            call.OriginalFunctionCallPrevented() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* type = actor->GetClassPrivate();
        if (!type || function->GetOuterPrivate() != type) return;
        const bool field = name == spawn_field;
        if (field)
        {
            if (type->GetPathName() != field_class || function->GetNumParms() != 0 || !replicated_field(actor)) return;
        }
        else if (type->GetFName() == electrode)
        {
            // Keep arming, blast creation, and queued graph continuations on the host.
            if (type->GetPathName() != electrode_class) return;
        }
        else if (function->GetNumParms() != 6 || !trap_class(type)) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (!guest_world(world)) return;

        if (observed_world.Get() != world)
        {
            observed_world = world;
            skipped = 0;
            blocked_triggers = 0;
        }
        if (field) ++skipped; else ++blocked_triggers;
        call.PreventOriginalFunctionCall();
    }

    int placed_trap(const Lua& lua)
    {
        U::UObject* placed{};
        if (U::IsInGameThreadRaw() && lua.get_stack_size() == 1 && lua.is_string(1))
        {
            const auto path = RC::to_generic_string(lua.get_string());
            if (path.size() <= 512 && path.starts_with(STR("/Game/Classes/Maps/")))
                placed = U::UObjectGlobals::StaticFindObject<U::UObject*>(nullptr, nullptr, path.c_str());
        }
        const bool qualified = placed && placed->HasAnyFlags(U::RF_WasLoaded) && !placed->IsUnreachable() &&
            trap_class(placed->GetClassPrivate());
        auto* world = qualified ? static_cast<U::AActor*>(placed)->GetWorld() : nullptr;
        lua.set_bool(world != nullptr);
        lua.set_bool(guest_world(world));
        return 2;
    }

    int set_guest(const Lua& lua)
    {
        const bool valid = U::IsInGameThreadRaw() && installed() && lua.get_stack_size() == 1 && lua.is_bool(1);
        if (valid)
        {
            guest_session = lua.get_bool();
            if (!guest_session)
            {
                observed_world = U::FWeakObjectPtr{};
                skipped = 0;
                blocked_triggers = 0;
                loading = {};
            }
        }
        lua.set_bool(valid);
        return 1;
    }

    int count(const Lua& lua)
    {
        const bool current = U::IsInGameThreadRaw() && guest_session && lua.get_stack_size() == 1 && lua.is_integer(1) &&
            reinterpret_cast<std::uintptr_t>(observed_world.Get()) == static_cast<std::uintptr_t>(lua.get_integer());
        lua.set_integer(current ? skipped : 0);
        lua.set_integer(current ? blocked_triggers : 0);
        return 2;
    }
}

namespace silverfish::native_anomaly_fields
{
    void install_hooks()
    {
        if (installed()) return;
        spawn_field = U::FName{STR("SpawnField"), U::FNAME_Add};
        overlap = U::FName{trap_overlap, U::FNAME_Add};
        electrode = U::FName{STR("Electrode_BP_C"), U::FNAME_Add};
        electrode_events = {
            U::FName{STR("BndEvt__Electrode_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"), U::FNAME_Add},
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Electrode_BP"), U::FNAME_Add},
            U::FName{STR("Burst"), U::FNAME_Add},
            U::FName{STR("EMP"), U::FNAME_Add},
            U::FName{STR("Shock"), U::FNAME_Add},
        };
        const auto script = [](Iteration& call, U::UObject* actor, U::FFrame& frame, void*) {
            intercept(call, actor, frame.Node());
        };
        hooks[0] = H::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* function, void*) { intercept(call, actor, function); },
            {false, false, owner, STR("Suppress independent guest trap field generation")});
        hooks[1] = H::RegisterProcessLocalScriptFunctionPreCallback(script,
            {false, false, owner, STR("Suppress guest trap field Blueprint calls")});
        hooks[2] = H::RegisterProcessInternalPreCallback(script,
            {false, false, owner, STR("Suppress guest trap field reflected calls")});
        hooks[3] = H::RegisterLoadMapPreCallback(
            [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL url, U::UPendingNetGame* pending, U::FString&) {
                if (!guest_session) return;
                loading = {};
                if (!pending || url.Map.IsEmpty() || !std::wstring_view{*url.Map}.starts_with(STR("/Game/Classes/Maps/"))) return;
                auto* object_pending = reinterpret_cast<U::UObject*>(pending);
                if (object_pending->GetClassPrivate()->GetPathName() != STR("/Script/Engine.PendingNetGame")) return;
                auto* driver = object(object_pending, STR("NetDriver"));
                auto* connection = object(driver, STR("ServerConnection"));
                if (server_connection(driver, connection)) loading = {driver, connection, &context};
            }, {false, true, owner, STR("Keep placed traps passive during guest map loading")});
        hooks[4] = H::RegisterLoadMapPostCallback(
            [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context,
               U::FURL, U::UPendingNetGame*, U::FString&) {
                if (loading.context == &context) loading = {};
            }, {false, true, owner, STR("Release the guest trap map loading scope")});
        if (!installed()) uninstall_hooks();
    }

    void uninstall_hooks()
    {
        for (auto& id : hooks)
        {
            if (id != H::ERROR_ID) H::UnregisterCallback(id);
            id = H::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeAnomalyGuest", set_guest);
        lua.register_function("SilverfishReadNativeAnomalySkips", count);
        lua.register_function("SilverfishQualifyNativePlacedTrap", placed_trap);
    }
}
