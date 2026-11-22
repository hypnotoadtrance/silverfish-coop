#include "anomaly_fields.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
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
    constexpr auto mushroom_patch = STR("/Game/Assets/PCG_Brushes/MushroomPatch_BP.MushroomPatch_BP_C");
    constexpr auto trap_overlap = STR("BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto electrode_class = STR("/Game/Classes/Anomalies/Electrode_BP.Electrode_BP_C");
    constexpr auto gravity_class = STR("/Game/Classes/Anomalies/GravityAnomaly_BP.GravityAnomaly_BP_C");
    constexpr auto pyrogeist_class = STR("/Game/Classes/Anomalies/Pyrogeist_BP.Pyrogeist_BP_C");
    constexpr auto directed_jet_class = STR("/Game/Classes/Anomalies/Fire_Jet_Directed_BP.Fire_Jet_Directed_BP_C");
    constexpr auto fire_class = STR("/Game/Classes/Anomalies/Fire_BP.Fire_BP_C");
    constexpr auto shadow_class = STR("/Game/Classes/Anomalies/ShadowSphere_BP.ShadowSphere_BP_C");
    constexpr auto crossroads_world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto roaming_teleport_class = STR("/Game/Classes/Anomalies/Wandering_Teleport_BP.Wandering_Teleport_BP_C");
    constexpr auto physics_prop = STR("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C");
    constexpr auto fixed_prop = STR("/Game/Assets/StaticMeshes/Destructable/Explosive_Prop_Master_BP.Explosive_Prop_Master_BP_C");
    constexpr std::array<std::wstring_view, 2> canister_classes{
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_PropaneTank_Small.Prop_PropaneTank_Small_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_PropaneTank_Tall.Prop_PropaneTank_Tall_C"),
    };
    constexpr std::array<std::wstring_view, 5> replicated_classes{
        STR("/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C"),
        STR("/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C"),
        STR("/Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C"),
        electrode_class,
        roaming_teleport_class,
    };
    std::array<H::GlobalCallbackId, 5> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};
    U::FName spawn_field, overlap, electrode, roaming_teleport, fixed_prop_name;
    U::FName gravity, gravity_graph, gravity_add_actors;
    U::FName pyrogeist, pyrogeist_graph, directed_jet, fire_graph, fire_damage;
    U::FName shadow, shadow_graph;
    bool shadow_replication{};
    U::FName patch_name;
    std::array<U::FName, 3> patch_events;
    std::array<U::FName, 6> electrode_events;
    std::array<U::FName, 7> roaming_events;
    std::array<U::FName, 2> canister_names;
    std::array<U::FName, 14> canister_events;
    std::array<U::FName, 5> fixed_prop_events;
    thread_local bool guest_session{};
    thread_local U::FWeakObjectPtr observed_world;
    thread_local std::int64_t skipped{}, blocked_triggers{}, blocked_canister_calls{}, blocked_fixed_prop_calls{};
    thread_local U::FWeakObjectPtr patch_world;
    thread_local std::int64_t blocked_patches{};

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

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
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

    bool canister_class(U::UObject* type)
    {
        if (!type) return false;
        for (std::size_t index = 0; index < canister_names.size(); ++index)
            if (type->GetFName() == canister_names[index]) return type->GetPathName() == canister_classes[index];
        return false;
    }

    bool fixed_prop_class(U::UObject* type)
    {
        auto* base = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, fixed_prop);
        return type && base && static_cast<U::UClass*>(type)->IsChildOf(base);
    }

    bool replicated_field(U::UObject* field, U::UWorld* world)
    {
        auto* roster = property<U::FArrayProperty>(field, STR("Anomalies"));
        auto* entry = roster ? U::CastField<U::FClassProperty>(roster->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper classes(roster, roster->ContainerPtrToValuePtr<void>(field));
        if (classes.Num() != 1) return false;
        auto* type = entry->GetObjectPropertyValue(classes.GetRawPtr(0));
        if (trap_class(type)) return true;
        if (!type || !world || world->GetPathName() != crossroads_world) return false;
        const auto name = type->GetFName();
        if (name == gravity) return type->GetPathName() == gravity_class;
        if (name == pyrogeist) return type->GetPathName() == pyrogeist_class;
        return shadow_replication && name == shadow && type->GetPathName() == shadow_class;
    }

    bool passive_directed_jet(U::UObject* actor, U::UObject* type, U::UObject* binding)
    {
        if (type->GetFName() != directed_jet || type->GetPathName() != directed_jet_class ||
            !binding || binding->GetPathName() != fire_class) return false;
        auto* role = property<U::FByteProperty>(actor, STR("Role"));
        if (!role || role->GetPropertyValueInContainer(actor) != 1) return false;
        // Initial replication can run BeginPlay before the Instigator NetGUID resolves.
        auto* source = object(actor, STR("Instigator"));
        return !source || (source->GetClassPrivate() && source->GetClassPrivate()->GetPathName() == pyrogeist_class);
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
        const bool field = name == spawn_field;
        const bool trap = name == overlap ||
            std::find(electrode_events.begin(), electrode_events.end(), name) != electrode_events.end();
        const bool roaming_event = std::ranges::find(roaming_events, name) != roaming_events.end();
        const bool gravity_event = name == gravity_graph || name == gravity_add_actors;
        const bool pyrogeist_event = name == pyrogeist_graph;
        const bool fire_event = name == fire_graph || name == fire_damage;
        const bool shadow_event = shadow_replication && name == shadow_graph;
        const bool prop_event = !field && std::find(canister_events.begin(), canister_events.end(), name) != canister_events.end();
        const bool fixed_event = !field && std::find(fixed_prop_events.begin(), fixed_prop_events.end(), name) != fixed_prop_events.end();
        const bool patch_event = std::ranges::find(patch_events, name) != patch_events.end();
        if ((!field && !trap && !roaming_event && !gravity_event && !pyrogeist_event && !fire_event &&
            !shadow_event && !prop_event && !fixed_event && !patch_event) || !U::IsInGameThreadRaw() ||
            call.OriginalFunctionCallPrevented() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* type = actor->GetClassPrivate();
        if (!type) return;
        const auto class_name = type->GetFName();
        if (patch_event && class_name == patch_name && type == function->GetOuterPrivate() &&
            type->GetPathName() == mushroom_patch && function->GetNumParms() == 0 && actor->HasAnyFlags(U::RF_WasLoaded))
        {
            auto* world = static_cast<U::AActor*>(actor)->GetWorld();
            if (!guest_world(world) || object(world, STR("PersistentLevel")) != actor->GetOuterPrivate()) return;
            if (patch_world.Get() != world)
            {
                patch_world = world;
                blocked_patches = 0;
            }
            ++blocked_patches;
            call.PreventOriginalFunctionCall();
            return;
        }
        const bool canister = prop_event && (class_name == canister_names[0] || class_name == canister_names[1]);
        const bool roaming = roaming_event && class_name == roaming_teleport && type->GetPathName() == roaming_teleport_class;
        const bool gravity_well = gravity_event && class_name == gravity && type->GetPathName() == gravity_class;
        auto* binding = function->GetOuterPrivate();
        const bool pyrogeist_body = pyrogeist_event && class_name == pyrogeist && type->GetPathName() == pyrogeist_class;
        const bool pyrogeist_jet = fire_event && passive_directed_jet(actor, type, binding);
        const bool shadow_sphere = shadow_event && class_name == shadow && type->GetPathName() == shadow_class;
        const bool fixed = fixed_event && binding && binding->GetFName() == fixed_prop_name &&
            binding->GetPathName() == fixed_prop && type->IsChildOf(static_cast<U::UClass*>(binding));
        if (!field && !trap && !roaming && !gravity_well && !pyrogeist_body && !pyrogeist_jet &&
            !shadow_sphere && !canister && !fixed) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (canister)
        {
            if (!canister_class(type) || !binding || (binding != type && binding->GetPathName() != physics_prop)) return;
        }
        else if (!fixed && !pyrogeist_jet && binding != type) return;
        if (field)
        {
            if (type->GetPathName() != field_class || function->GetNumParms() != 0 || !replicated_field(actor, world)) return;
        }
        else if (!canister && !fixed && class_name == electrode)
        {
            // Keep arming, blast creation, and queued graph continuations on the host.
            if (type->GetPathName() != electrode_class) return;
        }
        else if (gravity_well || pyrogeist_body || pyrogeist_jet || shadow_sphere)
        {
            if (!world || world->GetPathName() != crossroads_world) return;
        }
        else if (!canister && !fixed && !roaming && (function->GetNumParms() != 6 || !trap_class(type))) return;
        if (!guest_world(world)) return;

        if (observed_world.Get() != world)
        {
            observed_world = world;
            skipped = 0;
            blocked_triggers = 0;
            blocked_canister_calls = 0;
            blocked_fixed_prop_calls = 0;
        }
        if (field) ++skipped;
        else if (canister) ++blocked_canister_calls;
        else if (fixed) ++blocked_fixed_prop_calls;
        else ++blocked_triggers;
        call.PreventOriginalFunctionCall();
    }

    int qualify_placed(const Lua& lua, bool (*matches)(U::UObject*))
    {
        U::UObject* placed{};
        if (U::IsInGameThreadRaw() && lua.get_stack_size() == 1 && lua.is_string(1))
        {
            const auto path = RC::to_generic_string(lua.get_string());
            if (path.size() <= 512 && path.starts_with(STR("/Game/Classes/Maps/")))
                placed = U::UObjectGlobals::StaticFindObject<U::UObject*>(nullptr, nullptr, path.c_str());
        }
        const bool qualified = placed && placed->HasAnyFlags(U::RF_WasLoaded) && !placed->IsUnreachable() &&
            matches(placed->GetClassPrivate());
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
                blocked_canister_calls = 0;
                blocked_fixed_prop_calls = 0;
                patch_world = U::FWeakObjectPtr{};
                blocked_patches = 0;
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
        lua.set_integer(current ? blocked_canister_calls : 0);
        lua.set_integer(current ? blocked_fixed_prop_calls : 0);
        return 4;
    }
}

namespace silverfish::native_anomaly_fields
{
    void install_hooks()
    {
        if (installed()) return;
        spawn_field = U::FName{STR("SpawnField"), U::FNAME_Add};
        gravity = U::FName{STR("GravityAnomaly_BP_C"), U::FNAME_Add};
        gravity_graph = U::FName{STR("ExecuteUbergraph_GravityAnomaly_BP"), U::FNAME_Add};
        gravity_add_actors = U::FName{STR("AddActors"), U::FNAME_Add};
        pyrogeist = U::FName{STR("Pyrogeist_BP_C"), U::FNAME_Add};
        pyrogeist_graph = U::FName{STR("ExecuteUbergraph_Pyrogeist_BP"), U::FNAME_Add};
        directed_jet = U::FName{STR("Fire_Jet_Directed_BP_C"), U::FNAME_Add};
        fire_graph = U::FName{STR("ExecuteUbergraph_Fire_BP"), U::FNAME_Add};
        fire_damage = U::FName{STR("FireDmg_Actor"), U::FNAME_Add};
        const bool shadow_fixture = opt_in("SILVERFISH_COOP_HEADLESS") && opt_in("SILVERFISH_COOP_SHADOW_SPHERE_FIXTURE");
        shadow_replication = opt_in("SILVERFISH_COOP_NATIVE_ZONE") &&
            (opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || shadow_fixture);
        shadow = U::FName{STR("ShadowSphere_BP_C"), U::FNAME_Add};
        shadow_graph = U::FName{STR("ExecuteUbergraph_ShadowSphere_BP"), U::FNAME_Add};
        patch_name = U::FName{STR("MushroomPatch_BP_C"), U::FNAME_Add};
        patch_events = {
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("GrowMushrooms"), U::FNAME_Add},
            U::FName{STR("LoadMushrooms"), U::FNAME_Add},
        };
        overlap = U::FName{trap_overlap, U::FNAME_Add};
        electrode = U::FName{STR("Electrode_BP_C"), U::FNAME_Add};
        roaming_teleport = U::FName{STR("Wandering_Teleport_BP_C"), U::FNAME_Add};
        electrode_events = {
            U::FName{STR("BndEvt__Electrode_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"), U::FNAME_Add},
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Electrode_BP"), U::FNAME_Add},
            U::FName{STR("Burst"), U::FNAME_Add},
            U::FName{STR("EMP"), U::FNAME_Add},
            U::FName{STR("Shock"), U::FNAME_Add},
        };
        roaming_events = {
            U::FName{STR("ReceiveTick"), U::FNAME_Add},
            U::FName{STR("Wander"), U::FNAME_Add},
            U::FName{STR("ForceMove"), U::FNAME_Add},
            U::FName{STR("Authenticate_Target"), U::FNAME_Add},
            U::FName{STR("Teleport_Actor"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Wandering_Teleport_BP"), U::FNAME_Add},
            U::FName{STR("BndEvt__Wandering_Teleport_BP_Capsule_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"), U::FNAME_Add},
        };
        canister_names = {
            U::FName{STR("Prop_PropaneTank_Small_C"), U::FNAME_Add},
            U::FName{STR("Prop_PropaneTank_Tall_C"), U::FNAME_Add},
        };
        canister_events = {
            U::FName{STR("DealDamage"), U::FNAME_Add},
            U::FName{STR("InstantKill"), U::FNAME_Add},
            U::FName{STR("PersistantDeath"), U::FNAME_Add},
            U::FName{STR("PropBroken"), U::FNAME_Add},
            U::FName{STR("SetOnFire"), U::FNAME_Add},
            U::FName{STR("Extinguish"), U::FNAME_Add},
            U::FName{STR("Ignite"), U::FNAME_Add},
            U::FName{STR("Shock"), U::FNAME_Add},
            U::FName{STR("EMP"), U::FNAME_Add},
            U::FName{STR("Start_Jet"), U::FNAME_Add},
            U::FName{STR("AlightThruster"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Prop_PropaneTank_Small"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Prop_PropaneTank_Tall"), U::FNAME_Add},
            U::FName{STR("BndEvt__PhysicsProp_Master_BP_PropMesh_K2Node_ComponentBoundEvent_0_ComponentHitSignature__DelegateSignature"), U::FNAME_Add},
        };
        fixed_prop_name = U::FName{STR("Explosive_Prop_Master_BP_C"), U::FNAME_Add};
        fixed_prop_events = {
            U::FName{STR("DealDamage"), U::FNAME_Add},
            U::FName{STR("Hit_Dmg_Feedback"), U::FNAME_Add},
            U::FName{STR("IgniteOnFire"), U::FNAME_Add},
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Explosive_Prop_Master_BP"), U::FNAME_Add},
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
        lua.register_function("SilverfishReadNativeMushroomSkips", [](const Lua& current) {
            const bool match = U::IsInGameThreadRaw() && guest_session && current.get_stack_size() == 1 &&
                current.is_integer(1) && reinterpret_cast<std::uintptr_t>(patch_world.Get()) ==
                    static_cast<std::uintptr_t>(current.get_integer());
            current.set_integer(match ? blocked_patches : 0);
            return 1;
        });
        lua.register_function("SilverfishQualifyNativePlacedTrap",
            [](const Lua& current) { return qualify_placed(current, trap_class); });
        lua.register_function("SilverfishQualifyNativePlacedCanister",
            [](const Lua& current) { return qualify_placed(current, canister_class); });
        lua.register_function("SilverfishQualifyNativePlacedFixedExplosive",
            [](const Lua& current) { return qualify_placed(current, fixed_prop_class); });
    }
}
