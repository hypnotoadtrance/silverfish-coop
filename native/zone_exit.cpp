#include "zone_exit.hpp"

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
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/SoftObjectPtr.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto exit_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C");
    constexpr auto overlap_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:BndEvt__Level_Exit_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto activate_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:ActivateLevel");
    constexpr auto prep_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Prep_OpenNextLevel");
    constexpr auto load_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:LoadNextLevel");
    constexpr auto door_path = STR("/Game/Classes/Actors/Dungeon_TransitionDoor_BP.Dungeon_TransitionDoor_BP_C:SendInteractionToActor");
    constexpr auto door_power_path = STR("/Game/Classes/Actors/Dungeon_TransitionDoor_BP.Dungeon_TransitionDoor_BP_C:Send_PowerSignal");
    constexpr auto exit_power_path = STR("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C:Send_PowerSignal");
    constexpr auto exchange_world = STR("/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P.Exchange_01_P");
    constexpr auto exchange_actor = STR("/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P.Exchange_01_P:PersistentLevel.Dream_CountDown_BP_C_1");
    constexpr auto dream_power_path = STR("/Game/Classes/Actors/Dream_CountDown_BP.Dream_CountDown_BP_C:Send_PowerSignal");
    constexpr auto dream_script_path = STR("/Game/Classes/Actors/Dream_CountDown_BP.Dream_CountDown_BP_C:ExecuteUbergraph_Dream_CountDown_BP");
    constexpr int exchange_timed_return = 588;
    constexpr int exchange_signal_return = 15;
    constexpr auto ttl = std::chrono::seconds(2);
    constexpr double max_distance_squared = 1500.0 * 1500.0;
    constexpr double max_interaction_squared = 350.0 * 350.0;

    struct Route
    {
        RC::StringViewType world;
        RC::StringViewType exit;
        RC::StringViewType destination;
        RC::StringViewType entrance;
        RC::StringViewType door_destination{};
        std::array<RC::StringViewType, 2> alternate_exits{};
        RC::StringViewType secondary_exit{};
        RC::StringViewType secondary_destination{};
        RC::StringViewType secondary_entrance{};
    };

    constexpr std::array routes{
        Route{STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01"), STR("Level_Exit_BP_C_0"), {}, {}},
        Route{STR("/Game/Classes/Maps/PlaytestMap_02.PlaytestMap_02"), STR("Level_Exit_BP_C_0"), {}, {}},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"), STR("Level_Exit_BP_C_275"),
              STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"), STR("DB_CR"),
              STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P"), {}, STR("Level_Exit_BP_C_2"),
              STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"), STR("Obsv_Etr")},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"), STR("Level_Exit_BP_C_3"),
              STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"), STR("DB_CR")},
        Route{STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P"), {}, {}, {},
              STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P")},
        Route{STR("/Game/Classes/Maps/Dreams/GoldNexus_P.GoldNexus_P"), STR("Level_Exit_BP_C_178"),
              STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"), STR("Nexus_CrossRoads"), {},
              {STR("Level_Exit_BP_C_181"), STR("Level_Exit_BP_C_182")}},
        Route{STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"), STR("Level_Exit_BP_C_1"),
              STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"), STR("Obsv_Etr")},
        Route{exchange_world, {}, {}, {}},
    };

    std::atomic<std::uint64_t> generation{};
    std::atomic<bool> installed{};
    std::array<Hook::GlobalCallbackId, 2> callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool host{};
        bool accepting{};
        bool request_latched{};
        Unreal::FWeakObjectPtr local;
        Unreal::FWeakObjectPtr remote;
        Unreal::FWeakObjectPtr fixture;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr dream;
        Unreal::UClass* pawn_class{};
        Unreal::UClass* exit_class{};
        Unreal::UFunction* overlap{};
        Unreal::UFunction* activate{};
        Unreal::UFunction* prep{};
        Unreal::UFunction* load{};
        Unreal::UFunction* door{};
        Unreal::UFunction* door_power{};
        Unreal::UFunction* exit_power{};
        Unreal::UFunction* dream_power{};
        Unreal::UFunction* dream_script{};
        Unreal::FObjectProperty* door_player{};
        Unreal::FObjectProperty* door_hit{};
        Unreal::FObjectProperty* door_instigator{};
        Unreal::FObjectProperty* exit_instigator{};
        Unreal::FBoolProperty* exit_powered{};
        Unreal::FBoolProperty* dream_powered{};
        Unreal::FObjectProperty* dream_instigator{};
        Unreal::FIntProperty* dream_entry{};
        Unreal::FObjectProperty* overlap_actor{};
        Unreal::FObjectProperty* overlap_component{};
        Unreal::FObjectProperty* fixture_box{};
        Unreal::FSoftObjectProperty* next_level{};
        Unreal::FStrProperty* entrance_tag{};
        Unreal::FByteProperty* role{};
        const Route* route{};
        std::string_view event;
        std::string_view door_event;
        Unreal::FWeakObjectPtr pending_door;
        Unreal::FWeakObjectPtr pending_child;
        Clock::time_point pending_expires{};
        bool dream_return_requested{};
    };

    thread_local Scope scope;

    bool enabled()
    {
        const auto* value = std::getenv("SILVERFISH_COOP_NATIVE_ZONE");
        return value && std::string_view{value} == "1";
    }

    template <typename Property>
    Property* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        if (!object) return nullptr;
        auto* property = Unreal::CastField<Property>(object->GetPropertyByNameInChain(name));
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Unreal::UObject* read_object(Unreal::FObjectProperty* property, void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    Unreal::UWorld* actor_world(Unreal::UObject* object)
    {
        return object ? static_cast<Unreal::AActor*>(object)->GetWorld() : nullptr;
    }

    Address address(const Unreal::UObject* object)
    {
        return reinterpret_cast<Address>(object);
    }

    template <typename Object>
    Object* find(const RC::CharType* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<Object*>(nullptr, nullptr, path);
    }

    bool live_game_roster(const Scope& current)
    {
        auto* world = current.world.Get();
        auto* driver = read_object(field<Unreal::FObjectProperty>(world, STR("NetDriver")), world);
        if (!driver) return false;
        auto* server_field = field<Unreal::FObjectProperty>(driver, STR("ServerConnection"));
        auto* clients_field = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        if (!server_field || !clients_field || (read_object(server_field, driver) != nullptr) == current.host)
        {
            return false;
        }
        if (!current.host) return true;

        auto* remote = current.remote.Get();
        Unreal::FScriptArrayHelper clients(clients_field, clients_field->ContainerPtrToValuePtr<void>(driver));
        if (!remote) return clients.Num() == 0;
        if (clients.Num() != 1) return false;
        auto* controller = read_object(field<Unreal::FObjectProperty>(remote, STR("Controller")), remote);
        auto* player = read_object(field<Unreal::FObjectProperty>(controller, STR("Player")), controller);
        auto* connection = Unreal::CastField<Unreal::FObjectProperty>(clients_field->GetInner());
        return player && player == read_object(connection, clients.GetRawPtr(0));
    }

    bool is_local_pawn(Unreal::UObject* pawn)
    {
        auto* controller = read_object(field<Unreal::FObjectProperty>(pawn, STR("Controller")), pawn);
        auto* player = read_object(field<Unreal::FObjectProperty>(controller, STR("Player")), controller);
        auto* local_player = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        return player && local_player && player->IsA(local_player);
    }

    double distance_squared(Unreal::UObject* one, Unreal::UObject* two)
    {
        auto first = static_cast<Unreal::AActor*>(one)->K2_GetActorLocation();
        auto second = static_cast<Unreal::AActor*>(two)->K2_GetActorLocation();
        const auto dx = first.GetX() - second.GetX();
        const auto dy = first.GetY() - second.GetY();
        const auto dz = first.GetZ() - second.GetZ();
        return dx * dx + dy * dy + dz * dz;
    }

    bool allows_distance(const Scope& current, Unreal::UObject* local)
    {
        auto* remote = current.remote.Get();
        if (!remote) return true;
        if (actor_world(remote) != current.world.Get() ||
            current.role->GetPropertyValueInContainer(remote) != 3)
        {
            return false;
        }
        const auto squared = distance_squared(local, remote);
        return std::isfinite(squared) && squared <= max_distance_squared;
    }

    bool matches_target(Unreal::UObject* exit, Unreal::FSoftObjectProperty* level, Unreal::FStrProperty* entrance_tag,
                        RC::StringViewType target, RC::StringViewType tag)
    {
        if (!level || !entrance_tag || level->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/Engine.World")))
        {
            return false;
        }
        // UE4SS stores the largest supported soft pointer layout in C++. The
        // accessor resolves the reflected engine layout, so do not copy it.
        const auto* soft = level->ContainerPtrToValuePtr<Unreal::FSoftObjectPtr>(exit);
        const auto& path = soft->ToSoftObjectPath();
        const auto destination = path.GetAssetPathString();
        const auto& entrance = entrance_tag->GetPropertyValueInContainer(exit);
        return path.GetSubPathString().IsEmpty() && RC::StringViewType{*destination} == target && RC::StringViewType{*entrance} == tag;
    }

    bool is_secondary_exit(const Scope& current, Unreal::UObject* exit)
    {
        return !current.route->secondary_exit.empty() && exit->GetName() == current.route->secondary_exit;
    }

    bool matches_authored_route(const Scope& current, Unreal::UObject* exit, bool secondary)
    {
        const auto destination = secondary ? current.route->secondary_destination : current.route->destination;
        const auto entrance = secondary ? current.route->secondary_entrance : current.route->entrance;
        return destination.empty() || matches_target(exit, current.next_level, current.entrance_tag, destination, entrance);
    }

    bool in_persistent_level(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        auto* level = read_object(field<Unreal::FObjectProperty>(world, STR("PersistentLevel")), world);
        return level && static_cast<Unreal::AActor*>(actor)->GetLevel() == level;
    }

    bool is_route_exit(const Scope& current, Unreal::UObject* actor, Unreal::UWorld* world)
    {
        if (!current.route || actor->GetClassPrivate() != current.exit_class || !in_persistent_level(actor, world))
        {
            return false;
        }
        const auto name = actor->GetName();
        if (name == current.route->exit || (!current.route->secondary_exit.empty() && name == current.route->secondary_exit))
        {
            return true;
        }
        for (const auto alternate : current.route->alternate_exits)
        {
            if (!alternate.empty() && name == alternate) return true;
        }
        return false;
    }

    bool configure(Address local_address, Address remote_address, Address exit_address, bool host, bool accepting)
    {
        const auto current_generation = generation.load(std::memory_order_acquire);
        if (!local_address || local_address == remote_address || (!host && remote_address)) return false;
        if (scope.generation == current_generation && address(scope.local.Get()) == local_address &&
            address(scope.remote.Get()) == remote_address && address(scope.fixture.Get()) == exit_address && scope.host == host)
        {
            scope.expires = Clock::now() + ttl;
            scope.accepting = accepting;
            return true;
        }

        Scope next;
        next.pawn_class = find<Unreal::UClass>(pawn_path);
        next.exit_class = find<Unreal::UClass>(exit_path);
        if (!next.pawn_class || (exit_address && !next.exit_class)) return false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (object->HasAnyFlags(Unreal::RF_ClassDefaultObject)) return RC::LoopAction::Continue;
            if (address(object) == local_address && object->GetClassPrivate() == next.pawn_class) next.local = object;
            if (address(object) == remote_address && object->GetClassPrivate() == next.pawn_class) next.remote = object;
            if (address(object) == exit_address && object->GetClassPrivate() == next.exit_class) next.fixture = object;
            return next.local.Get() && (!remote_address || next.remote.Get()) && (!exit_address || next.fixture.Get())
                ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        auto* local = next.local.Get();
        auto* remote = next.remote.Get();
        auto* fixture = next.fixture.Get();
        auto* world = actor_world(local);
        if (!world || !is_local_pawn(local) || (remote_address && actor_world(remote) != world) ||
            (exit_address && actor_world(fixture) != world)) return false;
        const auto map = world->GetPathName();
        for (const auto& route : routes)
        {
            if (map == route.world)
            {
                next.route = &route;
                break;
            }
        }
        if (!next.route || (fixture && !is_route_exit(next, fixture, world))) return false;

        next.world = world;
        next.instance = read_object(field<Unreal::FObjectProperty>(world, STR("OwningGameInstance")), world);
        next.host = host;
        next.role = field<Unreal::FByteProperty>(next.pawn_class, STR("Role"));
        next.overlap = find<Unreal::UFunction>(overlap_path);
        next.activate = find<Unreal::UFunction>(activate_path);
        next.prep = find<Unreal::UFunction>(prep_path);
        next.load = find<Unreal::UFunction>(load_path);
        next.door = find<Unreal::UFunction>(door_path);
        next.door_power = find<Unreal::UFunction>(door_power_path);
        next.exit_power = find<Unreal::UFunction>(exit_power_path);
        next.door_player = field<Unreal::FObjectProperty>(next.door, STR("Player"));
        next.door_hit = field<Unreal::FObjectProperty>(next.door, STR("HitComponent"));
        next.door_instigator = field<Unreal::FObjectProperty>(next.door_power, STR("Instigator"));
        next.exit_instigator = field<Unreal::FObjectProperty>(next.exit_power, STR("Instigator"));
        next.exit_powered = field<Unreal::FBoolProperty>(next.exit_power, STR("Powered"));
        next.overlap_actor = field<Unreal::FObjectProperty>(next.overlap, STR("OtherActor"));
        next.overlap_component = field<Unreal::FObjectProperty>(next.overlap, STR("OverlappedComponent"));
        next.fixture_box = field<Unreal::FObjectProperty>(fixture, STR("Box"));
        next.next_level = field<Unreal::FSoftObjectProperty>(fixture, STR("NextLevel"));
        next.entrance_tag = field<Unreal::FStrProperty>(fixture, STR("Entrance Tag"));
        if (!next.instance.Get() || !next.role || !next.prep || !next.load || !live_game_roster(next)) return false;
        if (map == exchange_world)
        {
            auto* dream = find<Unreal::UObject>(exchange_actor);
            next.dream_power = find<Unreal::UFunction>(dream_power_path);
            next.dream_script = find<Unreal::UFunction>(dream_script_path);
            next.dream_powered = field<Unreal::FBoolProperty>(next.dream_power, STR("Powered"));
            next.dream_instigator = field<Unreal::FObjectProperty>(next.dream_power, STR("Instigator"));
            next.dream_entry = field<Unreal::FIntProperty>(next.dream_script, STR("EntryPoint"));
            if (!dream || !next.dream_power || !next.dream_script || !next.dream_powered || !next.dream_instigator ||
                !next.dream_entry || dream->GetClassPrivate() != next.dream_power->GetOuterPrivate() ||
                dream->GetClassPrivate() != next.dream_script->GetOuterPrivate() || actor_world(dream) != world ||
                !in_persistent_level(dream, world)) return false;
            next.dream = dream;
            next.dream_return_requested = scope.dream.Get() == dream && scope.dream_return_requested;
        }
        else if (!next.exit_class || !next.overlap_actor || !next.overlap_component || (fixture && !next.fixture_box) ||
                 !next.activate || !next.exit_power) return false;
        next.generation = current_generation;
        next.expires = Clock::now() + ttl;
        next.accepting = accepting;
        scope = next;
        return true;
    }

    bool host_is_ready(Unreal::UObject* local)
    {
        return local && scope.host && scope.accepting && !scope.request_latched && Clock::now() < scope.expires &&
               actor_world(local) == scope.world.Get() && live_game_roster(scope) && is_local_pawn(local) &&
               scope.role->GetPropertyValueInContainer(local) == 3;
    }

    Unreal::UObject* matching_door_child(Unreal::UObject* door)
    {
        constexpr auto tag = STR("CR_Cstrn");
        const auto target = scope.route->door_destination;
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        if (!door || target.empty() || door->GetName() != STR("Dungeon_TransitionDoor_BP_C_1") ||
            door->GetClassPrivate() != scope.door->GetOuterPrivate() || actor_world(door) != world ||
            !in_persistent_level(door, world)) return nullptr;

        auto* component = read_object(field<Unreal::FObjectProperty>(door, STR("Level_Exit_BP")), door);
        auto* child = read_object(field<Unreal::FObjectProperty>(component, STR("ChildActor")), component);
        if (!child || child->GetClassPrivate() != scope.exit_class || actor_world(child) != world ||
            child != read_object(field<Unreal::FObjectProperty>(door, STR("As Level Exit BP")), door)) return nullptr;
        if (!matches_target(door, field<Unreal::FSoftObjectProperty>(door, STR("Next Level")),
                            field<Unreal::FStrProperty>(door, STR("Entrance Tag")), target, tag) ||
            !matches_target(child, field<Unreal::FSoftObjectProperty>(child, STR("NextLevel")),
                            field<Unreal::FStrProperty>(child, STR("Entrance Tag")), target, tag)) return nullptr;
        return child;
    }

    void handle_door_interaction(Iteration& iteration, Unreal::UObject* door, void* locals)
    {
        auto* player = read_object(scope.door_player, locals);
        auto* local = scope.local.Get();
        if (!scope.host || !player || player != local)
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "guest_denied";
            return;
        }
        auto* child = matching_door_child(door);
        auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
        auto* hit = read_object(scope.door_hit, locals);
        if (!child || !locked || !hit || (hit != read_object(field<Unreal::FObjectProperty>(door, STR("Box")), door) &&
            hit != read_object(field<Unreal::FObjectProperty>(door, STR("SK_DungeonDoor_01")), door)))
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "unsupported_door";
            return;
        }
        if (!host_is_ready(local) || !(distance_squared(local, door) <= max_interaction_squared) || !allows_distance(scope, local))
        {
            iteration.PreventOriginalFunctionCall();
            scope.door_event = "host_out_of_range";
            return;
        }
        if (locked->GetPropertyValueInContainer(door))
        {
            scope.door_event = "host_locked";
            return;
        }

        // The stock unlocked branch starts its animation, then powers this
        // exact child after 0.75 seconds. That child owns the travel handoff.
        scope.pending_door = door;
        scope.pending_child = child;
        scope.pending_expires = Clock::now() + ttl;
        scope.door_event = "host_opening";
    }

    void handle_exit_power(Iteration& iteration, Unreal::UObject* context, void* locals)
    {
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented() || context != scope.pending_child.Get()) return;
        auto* door = scope.pending_door.Get();
        auto* local = scope.local.Get();
        auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
        scope.pending_door.Reset();
        scope.pending_child.Reset();
        if (!door || !locked || !locals || !scope.exit_powered || !scope.exit_instigator ||
            !scope.exit_powered->GetPropertyValueInContainer(locals) || read_object(scope.exit_instigator, locals) ||
            locked->GetPropertyValueInContainer(door) || matching_door_child(door) != context ||
            Clock::now() >= scope.pending_expires || !host_is_ready(local) || !allows_distance(scope, local))
        {
            scope.door_event = "handoff_denied";
            return;
        }
        scope.request_latched = true;
        scope.event = "door_request";
        scope.door_event = "travel_requested";
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void* locals)
    {
        if (!Unreal::IsInGameThreadRaw() || !context || !callee || !installed.load(std::memory_order_acquire) ||
            scope.generation != generation.load(std::memory_order_acquire)) return;
        if (scope.dream.Get() == context && (callee == scope.dream_power || callee == scope.dream_script) && locals &&
            !iteration.OriginalFunctionCallPrevented())
        {
            if (callee == scope.dream_power)
            {
                const auto powered = scope.dream_powered->GetPropertyValueInContainer(locals);
                auto* instigator = read_object(scope.dream_instigator, locals);
                auto* local = scope.local.Get();
                iteration.PreventOriginalFunctionCall();
                if (powered && iteration.OriginalFunctionCallPrevented() && host_is_ready(local) &&
                    (!instigator || instigator == local || (instigator->GetClassPrivate() != scope.pawn_class &&
                                                            actor_world(instigator) == scope.world.Get())))
                {
                    scope.dream_return_requested = true;
                }
            }
            else
            {
                const auto entry = scope.dream_entry->GetPropertyValueInContainer(locals);
                if (entry == exchange_timed_return || entry == exchange_signal_return) iteration.PreventOriginalFunctionCall();
            }
            return;
        }
        const bool is_overlap = callee == scope.overlap;
        const bool is_instance = callee == scope.prep || callee == scope.load;
        if (!is_overlap && !is_instance && callee != scope.activate && callee != scope.door &&
            callee != scope.exit_power && callee != scope.door_power) return;
        auto* world = scope.world.Get();
        if (!world || (is_instance ? scope.instance.Get() != context : actor_world(context) != world) ||
            iteration.OriginalFunctionCallPrevented()) return;

        if (callee == scope.door)
        {
            handle_door_interaction(iteration, context, locals);
            return;
        }
        if (callee == scope.exit_power)
        {
            handle_exit_power(iteration, context, locals);
            return;
        }
        if (callee == scope.door_power)
        {
            auto* instigator = read_object(scope.door_instigator, locals);
            if (instigator && instigator->GetClassPrivate() == scope.pawn_class &&
                (!scope.host || instigator != scope.local.Get()))
            {
                iteration.PreventOriginalFunctionCall();
                scope.door_event = "guest_denied";
            }
            return;
        }

        auto* other = is_overlap ? read_object(scope.overlap_actor, locals) : nullptr;
        if (other && other->GetClassPrivate() != scope.pawn_class) return;
        iteration.PreventOriginalFunctionCall();
        if (!is_overlap || !iteration.OriginalFunctionCallPrevented() || scope.request_latched || !other) return;
        auto* local = scope.local.Get();
        if (!scope.host || other != local)
        {
            scope.event = "guest_denied";
            return;
        }
        auto* component = read_object(scope.overlap_component, locals);
        if (!scope.fixture.Get() || !is_route_exit(scope, context, static_cast<Unreal::UWorld*>(world)) ||
            !component || component != read_object(scope.fixture_box, context))
        {
            scope.event = "unsupported_exit";
            return;
        }
        if (!host_is_ready(local))
        {
            scope.event = "host_not_ready";
            return;
        }
        if (!allows_distance(scope, local))
        {
            scope.event = "too_far";
            return;
        }
        const bool secondary = is_secondary_exit(scope, context);
        if (!matches_authored_route(scope, context, secondary))
        {
            scope.event = "destination_mismatch";
            return;
        }
        scope.request_latched = true;
        scope.event = secondary ? "secondary_request" : "request";
    }

    int lua_set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 5 &&
            lua.is_integer(1) && lua.is_integer(2) && lua.is_integer(3) && lua.is_bool(4) && lua.is_bool(5))
        {
            const auto local = lua.get_integer();
            const auto remote = lua.get_integer();
            const auto fixture = lua.get_integer();
            const auto host = lua.get_bool();
            const auto accepting = lua.get_bool();
            accepted = local > 0 && remote >= 0 && fixture >= 0 &&
                       configure(static_cast<Address>(local), static_cast<Address>(remote), static_cast<Address>(fixture), host, accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_clear(const Lua&)
    {
        silverfish::native_zone::clear_scope();
        return 0;
    }

    int lua_pause(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire))
        {
            scope.accepting = false;
        }
        return 0;
    }

    int lua_take_event(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire) || scope.event.empty())
        {
            lua.set_nil();
            return 1;
        }
        lua.set_string(scope.event);
        scope.event = {};
        return 1;
    }

    int lua_take_door_event(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || scope.generation != generation.load(std::memory_order_acquire) || scope.door_event.empty())
        {
            lua.set_nil();
            return 1;
        }
        lua.set_string(scope.door_event);
        scope.door_event = {};
        return 1;
    }

    int lua_release_request(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire))
        {
            scope.request_latched = false;
        }
        return 0;
    }

    int lua_inspect_exchange(const Lua& lua)
    {
        const bool match = Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            scope.generation == generation.load(std::memory_order_acquire) && scope.dream.Get() &&
            lua.get_stack_size() == 1 && lua.is_integer(1) &&
            address(scope.dream.Get()) == static_cast<Address>(lua.get_integer()) && Clock::now() < scope.expires;
        lua.set_bool(match);
        lua.set_string(match ? "native_countdown_guard_ready" : "native_countdown_guard_unavailable");
        return 2;
    }

    int lua_take_exchange_return(const Lua& lua)
    {
        bool requested = false;
        if (Unreal::IsInGameThreadRaw() && scope.generation == generation.load(std::memory_order_acquire) &&
            scope.dream.Get() && host_is_ready(scope.local.Get()))
        {
            requested = scope.dream_return_requested;
            scope.dream_return_requested = false;
        }
        lua.set_bool(requested);
        return 1;
    }
}

namespace silverfish::native_zone
{
    void install_hooks()
    {
        if (!enabled()) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(intercept,
            {false, false, owner, STR("Stock level exit event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*) {
                intercept(iteration, context, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Stock level exit script")});
        installed.store(callbacks[0] != Hook::ERROR_ID && callbacks[1] != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeZoneScope", lua_set_scope);
        lua.register_function("SilverfishClearNativeZoneScope", lua_clear);
        lua.register_function("SilverfishPauseNativeZoneScope", lua_pause);
        lua.register_function("SilverfishTakeNativeZoneEvent", lua_take_event);
        lua.register_function("SilverfishTakeNativeDoorEvent", lua_take_door_event);
        lua.register_function("SilverfishReleaseNativeZoneRequest", lua_release_request);
        lua.register_function("SilverfishInspectNativeExchangeCountdown", lua_inspect_exchange);
        lua.register_function("SilverfishTakeNativeExchangeReturn", lua_take_exchange_return);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_release);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
