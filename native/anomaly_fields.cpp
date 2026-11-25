#include "anomaly_fields.hpp"

#include "guest_regen.hpp"
#include "stock_enemy_drops.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
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
    using Clock = std::chrono::steady_clock;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto field_class = STR("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C");
    constexpr auto day_cycle_class = STR("/Game/Classes/Actors/DayCycle_SunControler_BP.DayCycle_SunControler_BP_C");
    constexpr auto mushroom_patch = STR("/Game/Assets/PCG_Brushes/MushroomPatch_BP.MushroomPatch_BP_C");
    constexpr auto trap_overlap = STR("BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto glass_web_class = STR("/Game/Classes/Anomalies/GlassWeb_BP.GlassWeb_BP_C");
    constexpr auto glass_thread_class = STR("/Game/Classes/Anomalies/GlassThread_BP.GlassThread_BP_C");
    constexpr auto electrode_class = STR("/Game/Classes/Anomalies/Electrode_BP.Electrode_BP_C");
    constexpr auto acid_pool_class = STR("/Game/Classes/Anomalies/AcidPool_BP.AcidPool_BP_C");
    constexpr auto acid_dot_class = STR("/Game/Classes/Actors/StatusEffect_Actors/DotVolume.DotVolume_C");
    constexpr auto gravity_class = STR("/Game/Classes/Anomalies/GravityAnomaly_BP.GravityAnomaly_BP_C");
    constexpr auto pyrogeist_class = STR("/Game/Classes/Anomalies/Pyrogeist_BP.Pyrogeist_BP_C");
    constexpr auto directed_jet_class = STR("/Game/Classes/Anomalies/Fire_Jet_Directed_BP.Fire_Jet_Directed_BP_C");
    constexpr auto fire_class = STR("/Game/Classes/Anomalies/Fire_BP.Fire_BP_C");
    constexpr auto shadow_class = STR("/Game/Classes/Anomalies/ShadowSphere_BP.ShadowSphere_BP_C");
    constexpr auto angler_class = STR("/Game/Classes/Anomalies/AnglerLamp_BP.AnglerLamp_BP_C");
    constexpr auto observer_class = STR("/Game/Classes/Anomalies/Observers_BP.Observers_BP_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto crossroads_world = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto vortex_class = STR("/Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C");
    constexpr auto roaming_teleport_class = STR("/Game/Classes/Anomalies/Wandering_Teleport_BP.Wandering_Teleport_BP_C");
    constexpr auto lingering_class = STR("/Game/Classes/Anomalies/Lingering_Teleport_BP1.Lingering_Teleport_BP1_C");
    constexpr auto artifact_spawner_class = STR("/Game/Classes/Actors/Artifact_Spawner_BP.Artifact_Spawner_BP_C");
    constexpr auto lingering_overlap = STR("BndEvt__Lingering_Teleport_BP1_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto physics_prop = STR("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C");
    constexpr std::wstring_view stock_prop_package = STR("/Game/Assets/StaticMeshes/Mesh_Props/");
    constexpr auto acid_bottle = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_AcidBottle.Prop_AcidBottle_C");
    constexpr auto scrap_wood_post = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_ScrapWood_Post.Prop_ScrapWood_Post_C");
    constexpr auto fixed_prop = STR("/Game/Assets/StaticMeshes/Destructable/Explosive_Prop_Master_BP.Explosive_Prop_Master_BP_C");
    constexpr auto standing_tank_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Standing_FuelTank.Prop_Standing_FuelTank_C");
    constexpr auto destructible = STR("/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP.Static_DestructableObject_Master_BP_C");
    constexpr auto drum_fire_class = STR("/Game/Classes/Anomalies/Fire_Molotov.Fire_Molotov_C");
    constexpr std::array<std::wstring_view, 4> canister_classes{
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_PropaneTank_Small.Prop_PropaneTank_Small_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_PropaneTank_Tall.Prop_PropaneTank_Tall_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_FuelDrum.Prop_FuelDrum_C"),
        standing_tank_class,
    };
    constexpr std::array<std::wstring_view, 7> replicated_classes{
        STR("/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C"),
        STR("/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C"),
        vortex_class,
        gravity_class,
        electrode_class,
        roaming_teleport_class,
        glass_web_class,
    };
    std::array<H::GlobalCallbackId, 5> hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};
    U::FName spawn_field, overlap, electrode, roaming_teleport, fixed_prop_name, update_storm_track;
    U::FName gravity, gravity_graph, gravity_add_actors;
    U::FName glass_web, glass_thread, glass_generate, glass_overlap, glass_break;
    U::FName pyrogeist, pyrogeist_graph, directed_jet, fire_graph, fire_damage;
    U::FName shadow, shadow_graph;
    U::FName angler;
    std::array<U::FName, 4> angler_events;
    U::FName observer, observer_check, observer_absorb;
    std::array<U::FName, 3> observer_events;
    bool shadow_replication{};
    bool angler_replication{};
    bool observer_enabled{};
    bool observer_fixture{};
    bool observer_reward_replication_fixture{};
    H::GlobalCallbackId stock_script_post = H::ERROR_ID, stock_internal_post = H::ERROR_ID;
    H::GlobalCallbackId stock_event_post = H::ERROR_ID, stock_actor_begin = H::ERROR_ID;
    U::FName patch_name;
    std::array<U::FName, 3> patch_events;
    std::array<U::FName, 6> electrode_events;
    std::array<U::FName, 9> roaming_events;
    U::FName lingering_teleport, artifact_spawner, spawn_anomalies, travel_pool, teleport_fx, teleport_actor;
    std::array<U::FName, canister_classes.size()> canister_names;
    std::array<U::FName, 16> canister_events;
    U::FName fuel_drum, fuel_drum_graph, prop_broken, receive_tick, drum_fire_name, standing_tank_name;
    U::FName user_construction_script, scrap_wood_post_name;
    std::array<U::FName, 5> fixed_prop_events;
    U::FName destructible_name;
    U::FName acid_pool_name, acid_dot_name, acid_fizzle, acid_extinguish, acid_actor_damage, dot_damage, receive_begin;
    std::array<U::FName, 8> destructible_events;
    thread_local bool guest_session{};
    thread_local U::FWeakObjectPtr projecting_acid_pool;
    thread_local U::FWeakObjectPtr observed_world;
    thread_local std::int64_t skipped{}, blocked_triggers{}, blocked_canister_calls{}, blocked_fixed_prop_calls{};
    thread_local U::FWeakObjectPtr patch_world;
    thread_local std::int64_t blocked_patches{};
    thread_local U::FWeakObjectPtr storm_world;
    thread_local std::int64_t blocked_storm_updates{};

    struct PendingLoad
    {
        U::FWeakObjectPtr driver, connection;
        U::FWorldContext* context{};
    };
    thread_local PendingLoad loading;

    struct ObserverTarget
    {
        U::FWeakObjectPtr actor, pawn;
        Clock::time_point expires;
    };
    thread_local std::array<ObserverTarget, 32> observer_targets;

    struct ObserverCheck
    {
        U::FWeakObjectPtr actor, pawn, prior;
        U::FFrame* frame{};
    };
    thread_local ObserverCheck checking_observer;
    struct ObserverOffering
    {
        U::FFrame* frame{};
        U::FObjectProperty* deferred{};
        U::FWeakObjectPtr world, reward;
    };
    thread_local ObserverOffering observer_offering;
    thread_local std::array<U::FWeakObjectPtr, 32> observer_rewards;
    thread_local std::int64_t observer_offers{}, observer_reward_results{};

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

    bool actual_observer(U::UObject* actor)
    {
        auto* type = actor ? actor->GetClassPrivate() : nullptr;
        return type && type->GetFName() == observer && type->GetPathName() == observer_class;
    }

    bool qualifies_observer_target(U::UObject* actor, U::UObject* pawn)
    {
        constexpr auto destroyed = static_cast<U::EObjectFlags>(U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        if (!actual_observer(actor) || !pawn || actor->HasAnyFlags(destroyed)) return false;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* canonical = object(instance, STR("SaveSlot_Ref"));
        if (!world || world->GetPathName() != crossroads_world ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, object(pawn, STR("Save")), canonical)) return false;
        auto* body = static_cast<U::AActor*>(pawn);
        if (body->GetWorld() != world) return false;
        const auto at = static_cast<U::AActor*>(actor)->K2_GetActorLocation();
        const auto point = body->K2_GetActorLocation();
        const auto x = at.X() - point.X(), y = at.Y() - point.Y(), z = at.Z() - point.Z();
        return x*x + y*y + z*z <= 2000.0 * 2000.0;
    }

    int set_observer_target(const Lua& lua)
    {
        bool accepted = false;
        if (observer_enabled && !guest_session && U::IsInGameThreadRaw() &&
            stock_script_post != H::ERROR_ID && stock_internal_post != H::ERROR_ID &&
            lua.get_stack_size() == 2 && lua.is_string(1) && lua.is_string(2))
        {
            const auto actor_path = RC::to_generic_string(lua.get_string());
            const auto pawn_path = RC::to_generic_string(lua.get_string());
            constexpr std::wstring_view persistent = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.");
            if (actor_path.size() <= 512 && pawn_path.size() <= 512 && actor_path.starts_with(persistent) &&
                (pawn_path.empty() || pawn_path.starts_with(persistent)))
            {
                auto* actor = U::UObjectGlobals::StaticFindObject<U::UObject*>(nullptr, nullptr, actor_path.c_str());
                auto* pawn = pawn_path.empty() ? nullptr
                    : U::UObjectGlobals::StaticFindObject<U::UObject*>(nullptr, nullptr, pawn_path.c_str());
                if (actual_observer(actor) && (pawn_path.empty() || qualifies_observer_target(actor, pawn)))
                {
                    const auto now = Clock::now();
                    auto found = std::ranges::find_if(observer_targets, [&](const ObserverTarget& entry) { return entry.actor.Get() == actor; });
                    if (found == observer_targets.end() && pawn)
                        found = std::ranges::find_if(observer_targets, [&](const ObserverTarget& entry) {
                            return !entry.actor.Get() || entry.expires <= now;
                        });
                    if (found != observer_targets.end())
                    {
                        *found = pawn ? ObserverTarget{actor, pawn, now + std::chrono::milliseconds(750)} : ObserverTarget{};
                        accepted = true;
                    }
                }
            }
        }
        lua.set_bool(accepted);
        return 1;
    }

    void begin_observer_check(Iteration& call, U::UObject* actor, U::FFrame& frame)
    {
        auto* method = frame.Node();
        if (!observer_enabled || guest_session || checking_observer.frame || !method ||
            method->GetFName() != observer_check || !U::IsInGameThreadRaw() || call.OriginalFunctionCallPrevented() ||
            !actual_observer(actor) || method->GetOuterPrivate() != actor->GetClassPrivate()) return;
        const auto now = Clock::now();
        auto found = std::ranges::find_if(observer_targets, [&](const ObserverTarget& entry) {
            return entry.expires > now && entry.actor.Get() == actor;
        });
        if (found == observer_targets.end()) return;
        auto* pawn = found->pawn.Get();
        auto* member = property<U::FObjectProperty>(actor, STR("As Player FP"));
        if (!member || !qualifies_observer_target(actor, pawn)) return;
        auto* address = member->ContainerPtrToValuePtr<void>(actor);
        checking_observer = {actor, pawn, member->GetObjectPropertyValue(address), &frame};
        member->SetObjectPropertyValue(address, pawn);
    }

    void end_observer_check(Iteration&, U::UObject*, U::FFrame& frame, void*)
    {
        if (checking_observer.frame != &frame) return;
        auto* actor = checking_observer.actor.Get();
        auto* member = property<U::FObjectProperty>(actor, STR("As Player FP"));
        if (member)
        {
            auto* address = member->ContainerPtrToValuePtr<void>(actor);
            if (member->GetObjectPropertyValue(address) == checking_observer.pawn.Get())
                member->SetObjectPropertyValue(address, checking_observer.prior.Get());
        }
        checking_observer = {};
    }

    void begin_observer_offering(Iteration& call, U::UObject* actor, U::FFrame& frame)
    {
        auto* method = frame.Node();
        if (!observer_enabled || guest_session || observer_offering.frame || !method ||
            method->GetFName() != observer_absorb || frame.Object() != actor || call.OriginalFunctionCallPrevented() ||
            !U::IsInGameThreadRaw() || !actual_observer(actor) || method->GetOuterPrivate() != actor->GetClassPrivate() ||
            method->GetNumParms() != 1 || method->GetReturnProperty() || !frame.Locals()) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        if (!world || world->GetPathName() != crossroads_world || !driver || object(driver, STR("World")) != world ||
            !property<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection"))) return;
        ++observer_offers;

        // Follow only the stock artifact spawn. The unsuffixed local is the damaging burst.
        for (auto* member : U::TFieldRange<U::FProperty>(method, U::EFieldIterationFlags::None))
        {
            if (member->GetFName().ToString() != STR("CallFunc_BeginDeferredActorSpawnFromClass_ReturnValue_1")) continue;
            auto* deferred = U::CastField<U::FObjectProperty>(member);
            if (deferred && deferred->GetArrayDim() == 1 &&
                !deferred->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm))
                observer_offering = {&frame, deferred, world, {}};
            break;
        }
    }

    void capture_observer_reward(Iteration& call, U::AActor* actor)
    {
        if (!observer_offering.frame || call.OriginalFunctionCallPrevented() || !actor || !U::IsInGameThreadRaw()) return;
        const auto* member = observer_offering.deferred;
        auto* locals = observer_offering.frame->Locals();
        if (!locals || member->GetObjectPropertyValue(member->ContainerPtrToValuePtr<void>(locals)) != actor) return;
        if (actor->GetWorld() == observer_offering.world.Get()) observer_offering.reward = actor;
    }

    void end_observer_offering(Iteration& call, U::UObject* observer_actor, U::FFrame& frame)
    {
        if (observer_offering.frame != &frame) return;
        auto* item = observer_offering.reward.Get();
        auto* world = observer_offering.world.Get();
        observer_offering = {};
        if (call.OriginalFunctionCallPrevented()) return;
        auto* base = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, item_class);
        if (!item || !base || !item->IsA(base) || static_cast<U::AActor*>(item)->GetWorld() != world) return;
        if (!observer_reward_replication_fixture)
        {
            if (silverfish::stock_enemy_drops::track_observer_reward(observer_actor, item)) ++observer_reward_results;
            return;
        }
        ++observer_reward_results;
        if (std::ranges::any_of(observer_rewards, [&](const auto& entry) { return entry.Get() == item; })) return;
        auto slot = std::ranges::find_if(observer_rewards, [](const auto& entry) { return !entry.Get(); });
        if (slot != observer_rewards.end()) *slot = item;
    }

    int take_observer_reward(const Lua& lua)
    {
        if (!observer_reward_replication_fixture || guest_session || !U::IsInGameThreadRaw() ||
            lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto world = static_cast<std::uintptr_t>(lua.get_integer());
        for (auto& entry : observer_rewards)
        {
            auto* item = entry.Get();
            entry = U::FWeakObjectPtr{};
            if (!item || reinterpret_cast<std::uintptr_t>(static_cast<U::AActor*>(item)->GetWorld()) != world) continue;
            lua.set_string(RC::to_utf8_string(item->GetPathName()));
            return 1;
        }
        return 0;
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

    struct DrumFire
    {
        U::FWeakObjectPtr actor, world;
    };
    thread_local std::array<DrumFire, 4> drum_fires;

    struct DrumBreak
    {
        const Iteration* call{};
        U::FWeakObjectPtr source, world, child;
    };
    thread_local std::array<DrumBreak, 4> drum_breaks;

    void begin_drum_break(Iteration& call, U::UObject* actor, U::UFunction* function)
    {
        if (!function || function->GetFName() != prop_broken || !U::IsInGameThreadRaw() || guest_session ||
            call.OriginalFunctionCallPrevented() || !actor) return;
        auto* type = actor->GetClassPrivate();
        if (!type || type->GetFName() != fuel_drum || !canister_class(type) || function->GetOuterPrivate() != type ||
            !actor->HasAnyFlags(U::RF_WasLoaded) || actor->IsUnreachable()) return;
        if (std::ranges::any_of(drum_breaks, [actor](const auto& active) { return active.source.Get() == actor; })) return;
        const auto slot = std::ranges::find_if(drum_breaks, [](const auto& active) { return !active.call; });
        if (slot == drum_breaks.end()) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        auto* role = property<U::FByteProperty>(actor, STR("Role"));
        if (!world || object(world, STR("PersistentLevel")) != actor->GetOuterPrivate() ||
            !driver || object(driver, STR("World")) != world || !property<U::FObjectProperty>(driver, STR("ServerConnection")) ||
            object(driver, STR("ServerConnection")) || !role || role->GetPropertyValueInContainer(actor) != 3) return;
        *slot = {&call, actor, world, {}};
    }

    void capture_drum_fire(Iteration& call, U::AActor* child)
    {
        if (!child || !U::IsInGameThreadRaw() || guest_session || call.OriginalFunctionCallPrevented() ||
            std::ranges::all_of(drum_breaks, [](const auto& active) { return !active.call; })) return;
        auto* child_type = child->GetClassPrivate();
        constexpr auto destroyed = static_cast<U::EObjectFlags>(U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        if (!child_type || child_type->GetFName() != drum_fire_name || child_type->GetPathName() != drum_fire_class ||
            child->HasAnyFlags(destroyed) || child->IsUnreachable()) return;
        auto* world = child->GetWorld();
        if (!world) return;
        auto* role = property<U::FByteProperty>(child, STR("Role"));
        auto* size = property<U::FDoubleProperty>(child, STR("Size"));
        auto* burn = property<U::FDoubleProperty>(child, STR("BurnTime"));
        if (!role || role->GetPropertyValueInContainer(child) != 3 || !size ||
            size->GetPropertyValueInContainer(child) != 8 || !burn) return;
        const double seconds = burn->GetPropertyValueInContainer(child);
        if (!(seconds >= 8 && seconds <= 13)) return;

        // FinishSpawning uses the drum's current location and calls BeginPlay synchronously.
        // A second drum may break inside the original explosion, so corroborate the source.
        const auto location = child->K2_GetActorLocation();
        for (auto current = drum_breaks.rbegin(); current != drum_breaks.rend(); ++current)
        {
            auto* source = static_cast<U::AActor*>(current->source.Get());
            if (!source || current->world.Get() != world || source->GetWorld() != world || current->child.Get()) continue;
            const auto origin = source->K2_GetActorLocation();
            const auto x = location.X() - origin.X(), y = location.Y() - origin.Y(), z = location.Z() - origin.Z();
            if (x * x + y * y + z * z <= 25.0 * 25.0)
            {
                current->child = child;
                return;
            }
        }
    }

    void end_drum_break(Iteration& call, U::UFunction* function)
    {
        if (!function || function->GetFName() != prop_broken || !U::IsInGameThreadRaw()) return;
        const auto ended = std::ranges::find_if(drum_breaks, [&call](const auto& active) { return active.call == &call; });
        if (ended == drum_breaks.end()) return;
        auto* child = ended->child.Get();
        auto* world = ended->world.Get();
        *ended = {};
        if (!child || !world || call.OriginalFunctionCallPrevented() ||
            std::ranges::any_of(drum_fires, [child](const auto& slot) { return slot.actor.Get() == child; })) return;
        const auto slot = std::ranges::find_if(drum_fires, [world](const auto& fire) {
            return !fire.actor.Get() || fire.world.Get() != world;
        });
        if (slot == drum_fires.end()) return;
        *slot = {child, world};
    }

    int take_drum_fire(const Lua& lua)
    {
        if (guest_session || !U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto world = static_cast<std::uintptr_t>(lua.get_integer());
        for (auto& slot : drum_fires)
        {
            auto* actor = slot.actor.Get();
            if (!actor || reinterpret_cast<std::uintptr_t>(slot.world.Get()) != world)
            {
                slot = {};
                continue;
            }
            lua.set_string(RC::to_utf8_string(actor->GetPathName()));
            slot = {};
            return 1;
        }
        return 0;
    }

    bool fixed_prop_class(U::UObject* type)
    {
        if (type && type->GetFName() == standing_tank_name) return type->GetPathName() == standing_tank_class;
        auto* base = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, fixed_prop);
        return type && base && static_cast<U::UClass*>(type)->IsChildOf(base);
    }

    bool destructible_class(U::UObject* type)
    {
        auto* base = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, destructible);
        return type && base && static_cast<U::UClass*>(type)->IsChildOf(base);
    }

    bool replicated_field(U::UObject* field, U::UWorld* world)
    {
        auto* roster = property<U::FArrayProperty>(field, STR("Anomalies"));
        auto* entry = roster ? U::CastField<U::FClassProperty>(roster->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper classes(roster, roster->ContainerPtrToValuePtr<void>(field));
        if (classes.Num() == 0) return false;
        // Only suppress a guest's roll if the host publishes every possible result.
        for (int index = 0; index < classes.Num(); ++index)
        {
            auto* type = entry->GetObjectPropertyValue(classes.GetRawPtr(index));
            if (trap_class(type)) continue;
            if (!type || !world || world->GetPathName() != crossroads_world) return false;
            const auto name = type->GetFName();
            if (name == pyrogeist && type->GetPathName() == pyrogeist_class) continue;
            if (angler_replication && name == angler && type->GetPathName() == angler_class) continue;
            if (observer_enabled && name == observer && type->GetPathName() == observer_class) continue;
            if (shadow_replication && name == shadow && type->GetPathName() == shadow_class) continue;
            return false;
        }
        return true;
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

    U::UWorld* stock_acid_pool(U::UObject* actor)
    {
        auto* type = actor ? actor->GetClassPrivate() : nullptr;
        if (!type || type->GetFName() != acid_pool_name || type->GetPathName() != acid_pool_class ||
            actor->IsUnreachable()) return nullptr;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* replicates = property<U::FBoolProperty>(actor, STR("bReplicates"));
        auto* dot = object(actor, STR("DotVolume"));
        auto* dot_type = dot ? dot->GetClassPrivate() : nullptr;
        if (!world || actor->GetOuterPrivate() != object(world, STR("PersistentLevel")) ||
            !replicates || replicates->GetPropertyValueInContainer(actor) || !dot_type ||
            dot_type->GetFName() != acid_dot_name || dot_type->GetPathName() != acid_dot_class ||
            dot->GetOuterPrivate() != actor) return nullptr;
        return world;
    }

    void suppress_acid_pool(Iteration& call, U::UObject* context, U::UFunction* function, const U::FName& name)
    {
        auto* type = context->GetClassPrivate();
        if (!type || function->GetOuterPrivate() != type || function->GetReturnProperty()) return;
        if (type->GetFName() == acid_pool_name &&
            (name == acid_fizzle || name == acid_extinguish || name == acid_actor_damage) &&
            function->GetNumParms() == (name == acid_fizzle ? 0 : 1) &&
            projecting_acid_pool.Get() != context && guest_world(stock_acid_pool(context)))
        {
            call.PreventOriginalFunctionCall();
        }
        else if (type->GetFName() == acid_dot_name && type->GetPathName() == acid_dot_class &&
                 ((name == receive_begin && function->GetNumParms() == 0) ||
                  (name == dot_damage && function->GetNumParms() == 2)))
        {
            auto* owner = context->GetOuterPrivate();
            if (!guest_world(stock_acid_pool(owner)) || object(owner, STR("DotVolume")) != context) return;
            call.PreventOriginalFunctionCall();
            if (name == receive_begin && call.OriginalFunctionCallPrevented())
                if (auto* active = property<U::FBoolProperty>(context, STR("DotActive")))
                    active->SetPropertyValueInContainer(context, false);
        }
    }

    int acid_pool_view(const Lua& lua)
    {
        const auto count = lua.get_stack_size();
        if (!U::IsInGameThreadRaw() || (count != 2 && count != 3) ||
            !lua.is_string(1) || !lua.is_integer(2) || (count == 3 && !lua.is_string(3))) return 0;
        // LuaMadeSimple removes each value as it is read.
        const auto path = RC::to_generic_string(lua.get_string());
        const auto address = static_cast<std::uintptr_t>(lua.get_integer());
        auto* actor = path.size() <= 512 && path.starts_with(STR("/Game/Classes/Maps/")) ?
            U::UObjectGlobals::StaticFindObject<U::UObject*>(nullptr, nullptr, path.c_str()) : nullptr;
        auto* world = stock_acid_pool(actor);
        const bool accepted = world && reinterpret_cast<std::uintptr_t>(world) == address;
        const bool guest = accepted && guest_world(world);
        if (count == 2)
        {
            lua.set_bool(accepted);
            lua.set_bool(guest);
            return 2;
        }
        const auto phase = lua.get_string();
        if (!guest || (phase != "f" && phase != "e"))
        {
            lua.set_bool(false);
            return 1;
        }
        auto* type = actor->GetClassPrivate();
        auto* function = actor->GetFunctionByNameInChain(phase == "f" ? acid_fizzle : acid_extinguish);
        auto* chance = phase == "e" ? property<U::FDoubleProperty>(function, STR("ExtinguishChance")) : nullptr;
        if (!function || function->GetOuterPrivate() != type || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            (phase == "f" ? function->GetNumParms() != 0 || function->GetParmsSize() != 0 :
             !chance || function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(double) ||
             chance->GetOffset_Internal() != 0 || !chance->HasAnyPropertyFlags(U::CPF_Parm) ||
             chance->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm)))
        {
            lua.set_bool(false);
            return 1;
        }
        double parameters = 1.0;
        projecting_acid_pool = actor;
        actor->ProcessEvent(function, phase == "f" ? nullptr : &parameters);
        projecting_acid_pool.Reset();
        lua.set_bool(true);
        return 1;
    }

    struct TravelSpawn
    {
        const Iteration* call{};
        U::FWeakObjectPtr source, child, world;
    };

    thread_local TravelSpawn travel_spawn;
    thread_local std::array<TravelSpawn, 64> lingering_spawns;

    void begin_travel_spawn(Iteration& call, U::UObject* actor, U::UFunction* function)
    {
        if (!function || function->GetFName() != spawn_anomalies || !actor || !U::IsInGameThreadRaw() ||
            call.OriginalFunctionCallPrevented()) return;
        auto* type = actor->GetClassPrivate();
        if (!type || type->GetFName() != artifact_spawner || type->GetPathName() != artifact_spawner_class ||
            function->GetOuterPrivate() != type || function->GetNumParms() || !actor->HasAnyFlags(U::RF_WasLoaded)) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (!world || !world->GetPathName().starts_with(STR("/Game/Classes/Maps/")) ||
            actor->GetOuterPrivate() != object(world, STR("PersistentLevel"))) return;
        auto* pool = property<U::FStructProperty>(actor, STR("Pool"));
        auto* row = pool ? property<U::FNameProperty>(pool->GetStruct().Get(), STR("RowName")) : nullptr;
        auto* center = property<U::FBoolProperty>(actor, STR("Center_Amomally"));
        if (!row || !center || !center->GetPropertyValueInContainer(actor) ||
            row->GetPropertyValueInContainer(pool->ContainerPtrToValuePtr<void>(actor)) != travel_pool) return;
        if (guest_world(world))
        {
            // Preserve the Travel row's physics setting for the separate stock artifact roll.
            auto* physics = property<U::FBoolProperty>(actor, STR("UsePhysics"));
            if (!physics) return;
            physics->SetPropertyValueInContainer(actor, true);
            call.PreventOriginalFunctionCall();
        }
        else if (!guest_session && !travel_spawn.call) travel_spawn = {&call, actor, {}, world};
    }

    void capture_lingering(Iteration& call, U::AActor* child)
    {
        if (!travel_spawn.call || travel_spawn.child.Get() || call.OriginalFunctionCallPrevented() || !child) return;
        auto* type = child->GetClassPrivate();
        if (!type || type->GetFName() != lingering_teleport || type->GetPathName() != lingering_class ||
            child->HasAnyFlags(U::RF_WasLoaded)) return;
        auto* source = static_cast<U::AActor*>(travel_spawn.source.Get());
        auto* world = child->GetWorld();
        if (!source || !world || world != travel_spawn.world.Get() || source->GetWorld() != world) return;
        const auto first = source->K2_GetActorLocation(), second = child->K2_GetActorLocation();
        const auto x = first.X() - second.X(), y = first.Y() - second.Y(), z = first.Z() - second.Z();
        if (x*x + y*y + z*z <= 4.0) travel_spawn.child = child;
    }

    void end_travel_spawn(Iteration& call)
    {
        if (travel_spawn.call != &call) return;
        auto* child = travel_spawn.child.Get();
        auto* world = travel_spawn.world.Get();
        if (child && world && !call.OriginalFunctionCallPrevented())
        {
            const auto slot = std::ranges::find_if(lingering_spawns, [world](const auto& entry) {
                return !entry.child.Get() || entry.world.Get() != world;
            });
            if (slot != lingering_spawns.end()) *slot = travel_spawn;
        }
        travel_spawn = {};
    }

    int take_lingering_spawn(const Lua& lua)
    {
        if (guest_session || !U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto world = static_cast<std::uintptr_t>(lua.get_integer());
        for (auto& entry : lingering_spawns)
        {
            auto* child = entry.child.Get();
            auto* source = entry.source.Get();
            if (!child || !source || reinterpret_cast<std::uintptr_t>(entry.world.Get()) != world) continue;
            lua.set_string(RC::to_utf8_string(child->GetPathName()));
            lua.set_string(RC::to_utf8_string(source->GetPathName()));
            entry = {};
            return 2;
        }
        return 0;
    }

    struct PropBreakMedia
    {
        std::string_view kind;
        std::wstring_view particle, sound;
        bool spawns_wood_debris{};
    };

    constexpr std::array<std::wstring_view, 3> wood_debris_classes{
        STR("/Game/Assets/StaticMeshes/Destructable/ChairGib_Wood_01_BP.ChairGib_Wood_01_BP_C"),
        STR("/Game/Assets/StaticMeshes/Destructable/CrateGib_Wood_01_BP.CrateGib_Wood_01_BP_C"),
        STR("/Game/Assets/StaticMeshes/Destructable/PalleteGib_Wood_01_BP1.PalleteGib_Wood_01_BP1_C"),
    };
    constexpr auto wood_break_sound =
        STR("/Game/Assets/Sounds/CUEs/PhysImpact_Cues/Destruction_Wood_Cue.Destruction_Wood_Cue");

    constexpr std::array prop_break_media{
        PropBreakMedia{"bottle", STR("/Game/Assets/PFX/NI_BrokenBottle_WtLiquid.NI_BrokenBottle_WtLiquid"),
            STR("/Game/Assets/Sounds/CUEs/BulletHit_Cues/Impact_Glass_Liquid_Cue.Impact_Glass_Liquid_Cue")},
        PropBreakMedia{"ceramic", STR("/Game/Assets/PFX/NI_China_Break.NI_China_Break"),
            STR("/Game/Assets/Sounds/CUEs/BulletHit_Cues/Impact_Glass_Cue.Impact_Glass_Cue")},
        PropBreakMedia{"platestand", STR("/Game/Assets/PFX/NI_China_Break.NI_China_Break"),
            STR("/Game/Assets/Sounds/CUEs/PhysImpact_Cues/Impact_SmallGlass_Cue.Impact_SmallGlass_Cue")},
        PropBreakMedia{"glass", STR("/Game/Assets/PFX/NI_BHit_Glass.NI_BHit_Glass"),
            STR("/Game/Assets/Sounds/CUEs/BulletHit_Cues/Impact_Glass_Cue.Impact_Glass_Cue")},
        PropBreakMedia{"paper", STR("/Game/Assets/PFX/NI_PaperScatter_Omni.NI_PaperScatter_Omni"),
            STR("/Game/Assets/Sounds/CUEs/BulletHit_Cues/BHit_Paper_Cue.BHit_Paper_Cue")},
        PropBreakMedia{"cardboard", STR("/Game/Assets/PFX/NI_CardboardBox_Break.NI_CardboardBox_Break"),
            STR("/Game/Assets/Sounds/CUEs/BulletHit_Cues/Break_CardboardBox.Break_CardboardBox")},
        PropBreakMedia{"wood", {}, wood_break_sound, true},
        PropBreakMedia{"woodchips", STR("/Game/Assets/PFX/NI_BHit_Wood.NI_BHit_Wood"), wood_break_sound, true},
    };

    struct PropBreak
    {
        const Iteration* call{};
        U::FWeakObjectPtr actor, world;
        std::array<double, 3> point{};
        std::string_view kind;
    };

    struct SpatialCue
    {
        U::FWeakObjectPtr world;
        std::array<double, 3> point{};
        Clock::time_point expires{};
        std::string_view kind;
    };

    thread_local std::array<PropBreak, 4> prop_breaks;
    thread_local std::array<SpatialCue, 12> spatial_cues;

    void begin_prop_break(Iteration& call, U::UObject* actor, U::UFunction* function, const void* locals)
    {
        if (!function || function->GetFName() != prop_broken || !actor || !locals ||
            !U::IsInGameThreadRaw() || call.OriginalFunctionCallPrevented()) return;
        auto* type = actor->GetClassPrivate();
        auto* binding = function->GetOuterPrivate();
        if (!type || !binding || actor->IsUnreachable() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 32 || function->GetReturnProperty()) return;
        const auto type_path = type->GetPathName();
        if (!type_path.starts_with(stock_prop_package) || actor->GetFunctionByNameInChain(prop_broken) != function)
            return;
        const auto* parent = binding == type && type_path == acid_bottle ? type->GetSuperClass() : nullptr;
        const bool acid_override = parent && parent->GetPathName() == physics_prop;
        if (!acid_override &&
            (binding->GetPathName() != physics_prop || !type->IsChildOf(static_cast<U::UClass*>(binding)))) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        if (!world || !driver || object(driver, STR("World")) != world || !property<U::FObjectProperty>(driver, STR("ServerConnection")))
            return;
        auto* role = property<U::FByteProperty>(actor, STR("Role"));
        auto* destroys = property<U::FBoolProperty>(actor, STR("DestroyIfBroken"));
        auto* particle = object(actor, STR("Break_PFX"));
        auto* sound = object(actor, STR("Break_SFX"));
        auto* debris = object(actor, STR("Break_IntoActor"));
        if (!role || !destroys || !destroys->GetPropertyValueInContainer(actor) || !sound ||
            object(actor, STR("FireRef"))) return;
        if (debris && std::ranges::find(wood_debris_classes, debris->GetPathName()) == wood_debris_classes.end())
            return;
        const auto particle_path = particle ? particle->GetPathName() : std::wstring{};
        const auto sound_path = sound->GetPathName();
        const auto media = std::ranges::find_if(prop_break_media, [&](const PropBreakMedia& effect) {
            return effect.spawns_wood_debris == (debris != nullptr) && effect.particle == particle_path &&
                   effect.sound == sound_path;
        });
        if (media == prop_break_media.end()) return;

        auto* server = object(driver, STR("ServerConnection"));
        const auto local_role = role->GetPropertyValueInContainer(actor);
        if (guest_session)
        {
            // The acid override spawns its own pool after the inherited glass break.
            // Stop the override before either side effect can run on a guest.
            if (server_connection(driver, server) && (local_role == 1 || local_role == 2 || acid_override))
                call.PreventOriginalFunctionCall();
            return;
        }
        auto* broken = property<U::FBoolProperty>(actor, STR("Broken"));
        auto* replicates = property<U::FBoolProperty>(actor, STR("bReplicates"));
        if (server || local_role != 3 || !replicates || !replicates->GetPropertyValueInContainer(actor) ||
            !broken || broken->GetPropertyValueInContainer(actor)) return;
        if (std::ranges::any_of(prop_breaks, [actor](const auto& entry) { return entry.actor.Get() == actor; })) return;
        const auto slot = std::ranges::find_if(prop_breaks, [](const auto& entry) { return !entry.call; });
        if (slot == prop_breaks.end()) return;
        auto* hit = property<U::FStructProperty>(function, STR("HitLoc"));
        auto* instigator = property<U::FObjectProperty>(function, STR("Instigator"));
        auto* vector = hit ? hit->GetStruct().Get() : nullptr;
        std::array<double, 3> point{};
        if (!vector || vector->GetPathName() != STR("/Script/CoreUObject.Vector") ||
            hit->GetOffset_Internal() != 0 || hit->GetSize() != sizeof(point) ||
            !instigator || instigator->GetOffset_Internal() != sizeof(point)) return;
        std::memcpy(point.data(), locals, sizeof(point));
        if (!std::ranges::all_of(point, [](double value) { return std::isfinite(value) && std::abs(value) <= 1e7; }))
            return;
        *slot = {&call, actor, world, point, media->kind};
    }

    void queue_spatial_cue(U::UObject* world, const std::array<double, 3>& point, std::string_view kind)
    {
        const auto now = Clock::now();
        auto pending = std::ranges::find_if(spatial_cues, [world, now](const auto& cue) {
            return cue.world.Get() != world || cue.expires <= now;
        });
        if (pending == spatial_cues.end()) pending = std::ranges::min_element(spatial_cues, {}, &SpatialCue::expires);
        *pending = {world, point, now + std::chrono::milliseconds(1250), kind};
    }

    void end_prop_break(Iteration& call, U::UFunction* function)
    {
        if (!function || function->GetFName() != prop_broken || !U::IsInGameThreadRaw()) return;
        const auto slot = std::ranges::find_if(prop_breaks, [&call](const auto& entry) { return entry.call == &call; });
        if (slot == prop_breaks.end()) return;
        const auto observed = *slot;
        *slot = {};
        auto* actor = observed.actor.Get();
        auto* world = observed.world.Get();
        auto* broken = property<U::FBoolProperty>(actor, STR("Broken"));
        if (!world || !broken || !broken->GetPropertyValueInContainer(actor) ||
            call.OriginalFunctionCallPrevented()) return;
        queue_spatial_cue(world, observed.point, observed.kind);
    }

    void observe_teleport_fx(Iteration& call, U::UObject* actor, U::FFrame& frame)
    {
        auto* function = frame.Node();
        if (!function || function->GetFName() != teleport_fx || guest_session || !U::IsInGameThreadRaw() ||
            call.OriginalFunctionCallPrevented() || !actor || frame.Object() != actor || !frame.Locals()) return;
        auto* caller = frame.PreviousFrame();
        auto* type = actor->GetClassPrivate();
        if (!type || !caller || caller->Object() != actor || !caller->Node() ||
            caller->Node()->GetFName() != teleport_actor || caller->Node()->GetOuterPrivate() != type ||
            function->GetOuterPrivate() != type || function->GetNumParms() != 1 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        const auto name = type->GetFName();
        if ((name != roaming_teleport || type->GetPathName() != roaming_teleport_class) &&
            (name != lingering_teleport || type->GetPathName() != lingering_class)) return;
        if (actor->IsUnreachable() || actor->HasAnyFlags(static_cast<U::EObjectFlags>(U::RF_BeginDestroyed | U::RF_FinishDestroyed))) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        auto* role = property<U::FByteProperty>(actor, STR("Role"));
        auto* replicates = property<U::FBoolProperty>(actor, STR("bReplicates"));
        if (!world || !driver || object(driver, STR("World")) != world || object(driver, STR("ServerConnection")) ||
            !role || role->GetPropertyValueInContainer(actor) != 3 ||
            !replicates || !replicates->GetPropertyValueInContainer(actor)) return;

        auto* input = property<U::FStructProperty>(function, STR("InputPin"));
        auto* vector = input ? input->GetStruct().Get() : nullptr;
        std::array<double, 3> point{};
        if (!vector || vector->GetPathName() != STR("/Script/CoreUObject.Vector") ||
            input->GetOffset_Internal() != 0 || input->GetSize() != sizeof(point) ||
            function->GetParmsSize() != sizeof(point)) return;
        std::memcpy(point.data(), frame.Locals(), sizeof(point));
        if (std::ranges::all_of(point, [](double axis) { return std::isfinite(axis) && std::abs(axis) <= 1e7; }))
            queue_spatial_cue(world, point, "wandering");
    }

    int take_spatial_cue(const Lua& lua)
    {
        if (guest_session || !U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto world = static_cast<std::uintptr_t>(lua.get_integer());
        const auto now = Clock::now();
        auto next = spatial_cues.end();
        for (auto it = spatial_cues.begin(); it != spatial_cues.end(); ++it)
        {
            if (reinterpret_cast<std::uintptr_t>(it->world.Get()) != world || it->expires <= now)
                *it = {};
            else if (next == spatial_cues.end() || it->expires < next->expires)
                next = it;
        }
        if (next == spatial_cues.end()) return 0;
        lua.set_string(next->kind);
        for (const auto axis : next->point) lua.set_number(axis);
        *next = {};
        return 4;
    }

    void intercept(Iteration& call, U::UObject* actor, U::UFunction* function)
    {
        if (!guest_session || !actor || !function) return;
        const auto name = function->GetFName();
        if (name == user_construction_script)
        {
            auto* type = actor->GetClassPrivate();
            if (type && type == function->GetOuterPrivate() && type->GetFName() == scrap_wood_post_name &&
                type->GetPathName() == scrap_wood_post && !actor->IsUnreachable() &&
                !actor->HasAnyFlags(U::RF_WasLoaded) && U::IsInGameThreadRaw() && !function->GetNumParms() &&
                !function->GetReturnProperty() && !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) &&
                !call.OriginalFunctionCallPrevented() &&
                guest_world(static_cast<U::AActor*>(actor)->GetWorld()))
            {
                // A host post using the default mesh sends no mesh delta. Preserve that
                // default on a guest instead of rerolling it during construction.
                if (auto* random = property<U::FBoolProperty>(actor, STR("Random")))
                    random->SetPropertyValueInContainer(actor, false);
            }
            return;
        }
        if ((name == acid_fizzle || name == acid_extinguish || name == acid_actor_damage ||
             name == receive_begin || name == dot_damage) && U::IsInGameThreadRaw() &&
            !call.OriginalFunctionCallPrevented() && !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
        {
            suppress_acid_pool(call, actor, function, name);
            if (call.OriginalFunctionCallPrevented()) return;
        }
        if (name == update_storm_track)
        {
            auto* day = actor->GetClassPrivate();
            if (!U::IsInGameThreadRaw() || !day || day != function->GetOuterPrivate() || day->GetPathName() != day_cycle_class ||
                !actor->HasAnyFlags(U::RF_WasLoaded) || actor->IsUnreachable() || function->GetNumParms() != 0 ||
                function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                call.OriginalFunctionCallPrevented()) return;
            auto* world = static_cast<U::AActor*>(actor)->GetWorld();
            if (!guest_world(world) || actor->GetOuterPrivate() != object(world, STR("PersistentLevel")) ||
                object(actor, STR("Game Instance")) != object(world, STR("OwningGameInstance"))) return;
            // The pending guest driver already scopes map startup even before
            // the world publishes NetDriver or the host's first weather frame.
            call.PreventOriginalFunctionCall();
            if (call.OriginalFunctionCallPrevented())
            {
                if (storm_world.Get() != world)
                {
                    storm_world = world;
                    blocked_storm_updates = 0;
                }
                ++blocked_storm_updates;
            }
            return;
        }
        if (name == glass_generate || name == glass_overlap || name == glass_break)
        {
            auto* type = actor->GetClassPrivate();
            if (!U::IsInGameThreadRaw() || !type || type != function->GetOuterPrivate() || actor->IsUnreachable() ||
                function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                call.OriginalFunctionCallPrevented()) return;
            const bool generator = name == glass_generate && type->GetFName() == glass_web &&
                type->GetPathName() == glass_web_class && function->GetNumParms() == 0;
            const bool strand = (name == glass_overlap || name == glass_break) && type->GetFName() == glass_thread &&
                type->GetPathName() == glass_thread_class && function->GetNumParms() == (name == glass_break ? 0 : 6);
            if (generator || strand)
            {
                auto* world = static_cast<U::AActor*>(actor)->GetWorld();
                if (!guest_world(world)) return;
                call.PreventOriginalFunctionCall();
                if (!call.OriginalFunctionCallPrevented()) return;
                if (observed_world.Get() != world)
                {
                    observed_world = world;
                    skipped = 0;
                    blocked_triggers = 0;
                    blocked_canister_calls = 0;
                    blocked_fixed_prop_calls = 0;
                }
                ++blocked_triggers;
                return;
            }
        }
        const bool field = name == spawn_field;
        const bool trap = name == overlap ||
            std::find(electrode_events.begin(), electrode_events.end(), name) != electrode_events.end();
        const bool roaming_event = std::ranges::find(roaming_events, name) != roaming_events.end();
        const bool gravity_event = name == gravity_graph || name == gravity_add_actors;
        const bool pyrogeist_event = name == pyrogeist_graph;
        const bool fire_event = name == fire_graph || name == fire_damage;
        const bool shadow_event = shadow_replication && name == shadow_graph;
        const bool angler_event = angler_replication && std::ranges::find(angler_events, name) != angler_events.end();
        const bool observer_event = observer_enabled && std::ranges::find(observer_events, name) != observer_events.end();
        const bool prop_event = name == receive_tick || std::ranges::find(canister_events, name) != canister_events.end();
        const bool fixed_event = !field && std::find(fixed_prop_events.begin(), fixed_prop_events.end(), name) != fixed_prop_events.end();
        const bool destructible_event = !field && std::ranges::find(destructible_events, name) != destructible_events.end();
        const bool patch_event = std::ranges::find(patch_events, name) != patch_events.end();
        if ((!field && !trap && !roaming_event && !gravity_event && !pyrogeist_event && !fire_event &&
            !shadow_event && !angler_event && !observer_event && !prop_event && !fixed_event && !destructible_event &&
            !patch_event) || !U::IsInGameThreadRaw() ||
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
        const bool drum = class_name == fuel_drum;
        const bool standing_tank = class_name == standing_tank_name;
        const bool canister = prop_event && (name != receive_tick || drum || standing_tank) &&
            std::ranges::find(canister_names, class_name) != canister_names.end();
        const bool roaming = roaming_event &&
            ((class_name == roaming_teleport && type->GetPathName() == roaming_teleport_class) ||
             (class_name == lingering_teleport && type->GetPathName() == lingering_class));
        const bool gravity_well = gravity_event && class_name == gravity && type->GetPathName() == gravity_class;
        auto* binding = function->GetOuterPrivate();
        if (destructible_event && binding && binding->GetFName() == destructible_name &&
            binding->GetPathName() == destructible && type->IsChildOf(static_cast<U::UClass*>(binding)) &&
            actor->HasAnyFlags(U::RF_WasLoaded))
        {
            auto* replicates = property<U::FBoolProperty>(actor, STR("bReplicates"));
            auto* world = static_cast<U::AActor*>(actor)->GetWorld();
            if (replicates && !replicates->GetPropertyValueInContainer(actor) &&
                guest_world(world) && actor->GetOuterPrivate() == object(world, STR("PersistentLevel")))
                call.PreventOriginalFunctionCall();
            return;
        }
        const bool pyrogeist_body = pyrogeist_event && class_name == pyrogeist && type->GetPathName() == pyrogeist_class;
        const bool pyrogeist_jet = fire_event && passive_directed_jet(actor, type, binding);
        const bool shadow_sphere = shadow_event && class_name == shadow && type->GetPathName() == shadow_class;
        const bool angler_lamp = angler_event && class_name == angler && type->GetPathName() == angler_class;
        const bool observing = observer_event && class_name == observer && type->GetPathName() == observer_class;
        const bool fixed = fixed_event && binding && binding->GetFName() == fixed_prop_name &&
            binding->GetPathName() == fixed_prop && type->IsChildOf(static_cast<U::UClass*>(binding));
        if (!field && !trap && !roaming && !gravity_well && !pyrogeist_body && !pyrogeist_jet &&
            !shadow_sphere && !angler_lamp && !observing && !canister && !fixed) return;
        auto* world = static_cast<U::AActor*>(actor)->GetWorld();
        if (canister)
        {
            if (!canister_class(type) || !binding || (binding != type && binding->GetPathName() != physics_prop)) return;
            if ((drum || standing_tank) && (!actor->HasAnyFlags(U::RF_WasLoaded) || !world ||
                object(world, STR("PersistentLevel")) != actor->GetOuterPrivate() || (name == receive_tick && binding != type))) return;
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
        else if (pyrogeist_body || pyrogeist_jet || shadow_sphere || angler_lamp || observing)
        {
            if (!world || world->GetPathName() != crossroads_world) return;
        }
        else if (!canister && !fixed && !roaming && !gravity_well &&
                 (function->GetNumParms() != 6 || !trap_class(type))) return;
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
                storm_world.Reset();
                blocked_storm_updates = 0;
                loading = {};
                drum_fires = {};
                drum_breaks = {};
                prop_breaks = {};
                spatial_cues = {};
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
    bool exact_observer_gaze(U::UObject* actor, U::UObject* pawn)
    {
        return observer_enabled && checking_observer.frame && checking_observer.actor.Get() == actor &&
            checking_observer.pawn.Get() == pawn && object(actor, STR("As Player FP")) == pawn;
    }

    void install_hooks()
    {
        if (installed()) return;
        spawn_field = U::FName{STR("SpawnField"), U::FNAME_Add};
        update_storm_track = U::FName{STR("UpdateStormTrack"), U::FNAME_Add};
        glass_web = U::FName{STR("GlassWeb_BP_C"), U::FNAME_Add};
        glass_thread = U::FName{STR("GlassThread_BP_C"), U::FNAME_Add};
        glass_generate = U::FName{STR("GenerateWeb"), U::FNAME_Add};
        glass_overlap = U::FName{STR("BndEvt__GlassThread_BP_StaticMesh_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"), U::FNAME_Add};
        glass_break = U::FName{STR("Break"), U::FNAME_Add};
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
        const bool angler_fixture = opt_in("SILVERFISH_COOP_HEADLESS") && opt_in("SILVERFISH_COOP_ANGLER_LAMP_FIXTURE");
        angler_replication = opt_in("SILVERFISH_COOP_NATIVE_ZONE") &&
            (opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || angler_fixture);
        angler = U::FName{STR("AnglerLamp_BP_C"), U::FNAME_Add};
        angler_events = {
            U::FName{STR("ExecuteUbergraph_AnglerLamp_BP"), U::FNAME_Add},
            U::FName{STR("Attract_NPCs"), U::FNAME_Add},
            U::FName{STR("AddLook"), U::FNAME_Add},
            U::FName{STR("SubtractLook"), U::FNAME_Add},
        };
        observer_enabled = opt_in("SILVERFISH_COOP_NATIVE_ZONE");
        observer_fixture = observer_enabled && opt_in("SILVERFISH_COOP_HEADLESS") && opt_in("SILVERFISH_COOP_OBSERVER_FIXTURE");
        observer_reward_replication_fixture = observer_fixture && opt_in("SILVERFISH_COOP_OBSERVER_REWARD_REPLICATION_FIXTURE");
        observer = U::FName{STR("Observers_BP_C"), U::FNAME_Add};
        observer_check = U::FName{STR("CheckPlayerLooking"), U::FNAME_Add};
        observer_absorb = U::FName{STR("AbsorbItem"), U::FNAME_Add};
        observer_events = {
            U::FName{STR("ExecuteUbergraph_Observers_BP"), U::FNAME_Add},
            U::FName{STR("TryCatchItem"), U::FNAME_Add},
            U::FName{STR("AbsorbItem"), U::FNAME_Add},
        };
        patch_name = U::FName{STR("MushroomPatch_BP_C"), U::FNAME_Add};
        patch_events = {
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("GrowMushrooms"), U::FNAME_Add},
            U::FName{STR("LoadMushrooms"), U::FNAME_Add},
        };
        overlap = U::FName{trap_overlap, U::FNAME_Add};
        electrode = U::FName{STR("Electrode_BP_C"), U::FNAME_Add};
        roaming_teleport = U::FName{STR("Wandering_Teleport_BP_C"), U::FNAME_Add};
        lingering_teleport = U::FName{STR("Lingering_Teleport_BP1_C"), U::FNAME_Add};
        artifact_spawner = U::FName{STR("Artifact_Spawner_BP_C"), U::FNAME_Add};
        spawn_anomalies = U::FName{STR("SpawnAnomalies"), U::FNAME_Add};
        travel_pool = U::FName{STR("Travel"), U::FNAME_Add};
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
            U::FName{STR("ExecuteUbergraph_Lingering_Teleport_BP1"), U::FNAME_Add},
            U::FName{lingering_overlap, U::FNAME_Add},
        };
        fuel_drum = U::FName{STR("Prop_FuelDrum_C"), U::FNAME_Add};
        standing_tank_name = U::FName{STR("Prop_Standing_FuelTank_C"), U::FNAME_Add};
        fuel_drum_graph = U::FName{STR("ExecuteUbergraph_Prop_FuelDrum"), U::FNAME_Add};
        prop_broken = U::FName{STR("PropBroken"), U::FNAME_Add};
        teleport_fx = U::FName{STR("Tele_FX"), U::FNAME_Add};
        teleport_actor = U::FName{STR("Teleport_Actor"), U::FNAME_Add};
        user_construction_script = U::FName{STR("UserConstructionScript"), U::FNAME_Add};
        scrap_wood_post_name = U::FName{STR("Prop_ScrapWood_Post_C"), U::FNAME_Add};
        drum_fire_name = U::FName{STR("Fire_Molotov_C"), U::FNAME_Add};
        receive_tick = U::FName{STR("ReceiveTick"), U::FNAME_Add};
        receive_begin = U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add};
        acid_pool_name = U::FName{STR("AcidPool_BP_C"), U::FNAME_Add};
        acid_dot_name = U::FName{STR("DotVolume_C"), U::FNAME_Add};
        acid_fizzle = U::FName{STR("FizzleOut"), U::FNAME_Add};
        acid_extinguish = U::FName{STR("Extinguish"), U::FNAME_Add};
        acid_actor_damage = U::FName{STR("FireDmg_Actor"), U::FNAME_Add};
        dot_damage = U::FName{STR("DamageActor"), U::FNAME_Add};
        canister_names = {
            U::FName{STR("Prop_PropaneTank_Small_C"), U::FNAME_Add},
            U::FName{STR("Prop_PropaneTank_Tall_C"), U::FNAME_Add},
            fuel_drum,
            standing_tank_name,
        };
        canister_events = {
            U::FName{STR("DealDamage"), U::FNAME_Add},
            U::FName{STR("InstantKill"), U::FNAME_Add},
            U::FName{STR("PersistantDeath"), U::FNAME_Add},
            prop_broken,
            U::FName{STR("SetOnFire"), U::FNAME_Add},
            U::FName{STR("Extinguish"), U::FNAME_Add},
            U::FName{STR("Ignite"), U::FNAME_Add},
            U::FName{STR("Shock"), U::FNAME_Add},
            U::FName{STR("EMP"), U::FNAME_Add},
            U::FName{STR("Start_Jet"), U::FNAME_Add},
            U::FName{STR("AlightThruster"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Prop_PropaneTank_Small"), U::FNAME_Add},
            U::FName{STR("ExecuteUbergraph_Prop_PropaneTank_Tall"), U::FNAME_Add},
            U::FName{STR("IgniteOnFire"), U::FNAME_Add},
            fuel_drum_graph,
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
        destructible_name = U::FName{STR("Static_DestructableObject_Master_BP_C"), U::FNAME_Add};
        destructible_events = {
            U::FName{STR("DealDamage"), U::FNAME_Add},
            U::FName{STR("Damage_Feedback"), U::FNAME_Add},
            U::FName{STR("PryOpen"), U::FNAME_Add},
            U::FName{STR("Ignite"), U::FNAME_Add},
            U::FName{STR("SetOnFire"), U::FNAME_Add},
            U::FName{STR("PersistantDeath"), U::FNAME_Add},
            U::FName{STR("ReceiveBeginPlay"), U::FNAME_Add},
            U::FName{STR("InstantKill"), U::FNAME_Add},
        };
        const auto script = [](Iteration& call, U::UObject* actor, U::FFrame& frame, void*) {
            intercept(call, actor, frame.Node());
            begin_observer_check(call, actor, frame);
            begin_observer_offering(call, actor, frame);
        };
        hooks[0] = H::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* function, void* locals) {
                if (stock_actor_begin != H::ERROR_ID && stock_event_post != H::ERROR_ID)
                    begin_travel_spawn(call, actor, function);
                intercept(call, actor, function);
                if (stock_actor_begin != H::ERROR_ID && stock_event_post != H::ERROR_ID)
                    begin_drum_break(call, actor, function);
                if (stock_event_post != H::ERROR_ID)
                    begin_prop_break(call, actor, function, locals);
            },
            {false, false, owner, STR("Suppress independent guest trap field generation")});
        hooks[1] = H::RegisterProcessLocalScriptFunctionPreCallback(
            [script](Iteration& call, U::UObject* actor, U::FFrame& frame, void* result) {
                if (stock_actor_begin != H::ERROR_ID && stock_script_post != H::ERROR_ID)
                    begin_travel_spawn(call, actor, frame.Node());
                script(call, actor, frame, result);
                if (stock_actor_begin != H::ERROR_ID && stock_script_post != H::ERROR_ID)
                    begin_drum_break(call, actor, frame.Node());
                if (stock_script_post != H::ERROR_ID)
                    begin_prop_break(call, actor, frame.Node(), frame.Locals());
            },
            {false, false, owner, STR("Suppress guest trap field Blueprint calls")});
        hooks[2] = H::RegisterProcessInternalPreCallback(
            [script](Iteration& call, U::UObject* actor, U::FFrame& frame, void* result) {
                if (stock_actor_begin != H::ERROR_ID && stock_internal_post != H::ERROR_ID)
                    begin_travel_spawn(call, actor, frame.Node());
                script(call, actor, frame, result);
                if (stock_actor_begin != H::ERROR_ID && stock_internal_post != H::ERROR_ID)
                    begin_drum_break(call, actor, frame.Node());
                if (stock_internal_post != H::ERROR_ID)
                    begin_prop_break(call, actor, frame.Node(), frame.Locals());
            },
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
        const auto stock_result = [](Iteration& call, U::UObject* actor, U::FFrame& frame, void* result) {
            end_travel_spawn(call);
            end_drum_break(call, frame.Node());
            end_prop_break(call, frame.Node());
            if (observer_enabled)
            {
                end_observer_check(call, actor, frame, result);
                end_observer_offering(call, actor, frame);
            }
        };
        stock_script_post = H::RegisterProcessLocalScriptFunctionPostCallback(
            [stock_result](Iteration& call, U::UObject* actor, U::FFrame& frame, void* result) {
                stock_result(call, actor, frame, result);
                observe_teleport_fx(call, actor, frame);
            }, {false, false, owner, STR("Read stock anomaly and prop results")});
        stock_internal_post = H::RegisterProcessInternalPostCallback(
            stock_result, {false, false, owner, STR("Read reflected anomaly and prop results")});
        stock_event_post = H::RegisterProcessEventPostCallback(
            [](Iteration& call, U::UObject*, U::UFunction* function, void*) {
                end_travel_spawn(call);
                end_drum_break(call, function);
                end_prop_break(call, function);
            }, {false, false, owner, STR("Read stock prop break results")});
        stock_actor_begin = H::RegisterBeginPlayPostCallback(
            [](Iteration& call, U::AActor* actor) {
                capture_lingering(call, actor);
                capture_drum_fire(call, actor);
                if (observer_enabled) capture_observer_reward(call, actor);
            }, {false, false, owner, STR("Track actual stock anomaly and prop child actors")});
        if (!installed()) uninstall_hooks();
    }

    void uninstall_hooks()
    {
        travel_spawn = {};
        lingering_spawns = {};
        drum_fires = {};
        drum_breaks = {};
        prop_breaks = {};
        spatial_cues = {};
        for (auto* post : {&stock_script_post, &stock_internal_post, &stock_event_post, &stock_actor_begin})
        {
            if (*post != H::ERROR_ID) H::UnregisterCallback(*post);
            *post = H::ERROR_ID;
        }
        for (auto& id : hooks)
        {
            if (id != H::ERROR_ID) H::UnregisterCallback(id);
            id = H::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeAnomalyGuest", set_guest);
        lua.register_function("SilverfishTakeNativeAnomalySpatialCue", take_spatial_cue);
        lua.register_function("SilverfishReadNativeAnomalySkips", count);
        lua.register_function("SilverfishReadNativeGuestStormGuard", [](const Lua& current) {
            if (!U::IsInGameThreadRaw() || !guest_session || current.get_stack_size() != 0) return 0;
            auto* world = storm_world.Get();
            if (!world) return 0;
            current.set_integer(reinterpret_cast<std::uintptr_t>(world));
            current.set_integer(blocked_storm_updates);
            return 2;
        });
        if (stock_actor_begin != H::ERROR_ID &&
            (stock_event_post != H::ERROR_ID || stock_script_post != H::ERROR_ID || stock_internal_post != H::ERROR_ID))
        {
            lua.register_function("SilverfishTakeNativeDrumFire", take_drum_fire);
            lua.register_function("SilverfishTakeNativeLingeringTeleport", take_lingering_spawn);
        }
        if (observer_enabled) lua.register_function("SilverfishSetNativeObserverTarget", set_observer_target);
        if (observer_reward_replication_fixture)
            lua.register_function("SilverfishTakeNativeObserverReward", take_observer_reward);
        if (observer_fixture)
        {
            lua.register_function("SilverfishReadObserverOfferingFixture", [](const Lua& access) {
                access.set_integer(observer_offers);
                access.set_integer(observer_reward_results);
                return 2;
            });
        }
        lua.register_function("SilverfishReadNativeMushroomSkips", [](const Lua& current) {
            const bool match = U::IsInGameThreadRaw() && guest_session && current.get_stack_size() == 1 &&
                current.is_integer(1) && reinterpret_cast<std::uintptr_t>(patch_world.Get()) ==
                    static_cast<std::uintptr_t>(current.get_integer());
            current.set_integer(match ? blocked_patches : 0);
            return 1;
        });
        lua.register_function("SilverfishQualifyNativePlacedTrap",
            [](const Lua& current) { return qualify_placed(current, trap_class); });
        lua.register_function("SilverfishNativeAcidPool", acid_pool_view);
        lua.register_function("SilverfishQualifyNativePlacedCanister",
            [](const Lua& current) { return qualify_placed(current, canister_class); });
        lua.register_function("SilverfishQualifyNativePlacedFixedExplosive",
            [](const Lua& current) { return qualify_placed(current, fixed_prop_class); });
        lua.register_function("SilverfishQualifyNativePlacedDestructible",
            [](const Lua& current) { return qualify_placed(current, destructible_class); });
    }
}
