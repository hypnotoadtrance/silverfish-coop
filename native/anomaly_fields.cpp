#include "anomaly_fields.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <string_view>

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
    using Iteration = H::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto field_class = STR("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C");
    constexpr std::array<std::wstring_view, 2> replicated_classes{
        STR("/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C"),
        STR("/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C"),
    };
    std::array<H::GlobalCallbackId, 3> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};
    U::FName spawn_field;
    thread_local bool guest_session{};
    thread_local U::FWeakObjectPtr observed_world;
    thread_local std::int64_t skipped{};

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

    bool replicated_field(U::UObject* field)
    {
        auto* roster = property<U::FArrayProperty>(field, STR("Anomalies"));
        auto* entry = roster ? U::CastField<U::FClassProperty>(roster->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper classes(roster, roster->ContainerPtrToValuePtr<void>(field));
        if (classes.Num() != 1) return false;
        auto* kind = entry->GetObjectPropertyValue(classes.GetRawPtr(0));
        if (!kind) return false;
        const auto path = kind->GetPathName();
        return std::ranges::find(replicated_classes, std::wstring_view{path}) != replicated_classes.end();
    }

    void intercept(Iteration& call, U::UObject* actor, U::UFunction* function)
    {
        if (!guest_session || !actor || !function || function->GetFName() != spawn_field ||
            !U::IsInGameThreadRaw() || call.OriginalFunctionCallPrevented()) return;
        auto* type = actor->GetClassPrivate();
        if (!type || function->GetOuterPrivate() != type || type->GetPathName() != field_class ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetNumParms() != 0 ||
            !replicated_field(actor)) return;

        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = object(driver, STR("ServerConnection"));
        if (!world || !connection || object(driver, STR("World")) != world ||
            object(connection, STR("Driver")) != driver) return;

        if (observed_world.Get() != world)
        {
            observed_world = world;
            skipped = 0;
        }
        ++skipped;
        call.PreventOriginalFunctionCall();
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
        return 1;
    }
}

namespace silverfish::native_anomaly_fields
{
    void install_hooks()
    {
        if (installed()) return;
        spawn_field = U::FName{STR("SpawnField"), U::FNAME_Add};
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
    }
}
