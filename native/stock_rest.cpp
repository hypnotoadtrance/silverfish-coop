#include "stock_rest.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
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
    constexpr auto bed_class = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C");
    constexpr auto interaction_path = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C:SendInteractionToActor");
    constexpr auto bed_script_path = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C:ExecuteUbergraph_SavePoint_BP");
    constexpr auto sleep_path = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C:GoToSleep");
    constexpr auto screen_class = STR("/Game/Classes/GUI/New/SleepScreen.SleepScreen_C");
    constexpr auto screen_script = STR("/Game/Classes/GUI/New/SleepScreen.SleepScreen_C:ExecuteUbergraph_SleepScreen");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto gameplay_mode = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C:EnterGameplayMode");
    constexpr auto menu_mode = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C:EnterMenuMode");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto scope_lease = std::chrono::milliseconds(1250);
    constexpr auto request_lease = std::chrono::seconds(2);
    constexpr std::int32_t max_hours = 10;
    constexpr int max_clients = 4;

    struct Profile
    {
        RC::StringViewType world;
        RC::StringViewType bed;
        std::uint8_t faction;
        std::int32_t minimum;
        RC::StringViewType alternate{};
    };

    constexpr std::array<Profile, 11> profiles{{
        {STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"), STR("SavePoint_BP_C_1"), 4, 300, STR("LevelExit_Spawn_BP_C_4")},
        {STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"), STR("SavePoint_BP_C_3"), 8, -90, STR("LevelExit_Spawn_BP_C_1")},
        {STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P.SouthEast_Road_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_SafeZones/ArtifactForge_P.ArtifactForge_P"), STR("SavePoint_BP_C_1"), 0, 300},
        {STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"), STR("SavePoint_BP_C_1"), 2, 0, STR("LevelExit_Spawn_BP_C_1")},
        {STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"), STR("SavePoint_BP_C_1"), 0, 300},
    }};

    struct Guard
    {
        std::uint64_t generation{};
        bool host{};
        const Profile* profile{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr bed;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr server;
        Unreal::FWeakObjectPtr instance;
        Unreal::UClass* pawn_type{};
        Unreal::UClass* controller_type{};
        Unreal::UClass* connection_type{};
        Unreal::UFunction* interaction{};
        Unreal::UFunction* sleep{};
        Unreal::UFunction* bed_script{};
        Unreal::FObjectProperty* interaction_player{};
        std::uint64_t guest_denials{};
        std::string_view status{"unbound"};
    };

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr bed;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr save;
        Unreal::UFunction* sleep{};
        Unreal::FIntProperty* hours{};
        Unreal::UFunction* fixture_menu_mode{};
        Unreal::UFunction* fixture_gameplay_mode{};
        bool pending{};
        Clock::time_point request_expires{};
        std::int32_t selection{};
        std::string_view status{"unbound"};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 2> callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Guard guard;
    thread_local Scope scope;

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 &&
               size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_ZONE"); }

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

    bool is_live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool is_false(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FBoolProperty>(object, name);
        return value && !value->GetPropertyValueInContainer(object);
    }

    bool actor_in_world(Unreal::UObject* object, Unreal::UObject* world)
    {
        return is_live(object) && is_false(object, STR("bActorIsBeingDestroyed")) &&
               static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    bool has_role(Unreal::UObject* object, std::uint8_t expected)
    {
        auto* role = field<Unreal::FByteProperty>(object, STR("Role"));
        return role && role->GetPropertyValueInContainer(object) == expected;
    }

    bool authority(Unreal::UObject* object, Unreal::UObject* world)
    {
        return actor_in_world(object, world) && has_role(object, 3);
    }

    Address address(Unreal::UObject* object)
    {
        return reinterpret_cast<Address>(object);
    }

    bool sole_local_player(Unreal::UObject* instance, Unreal::UObject* player)
    {
        auto* array = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper players(array, array->ContainerPtrToValuePtr<void>(instance));
        return players.Num() == 1 && entry->GetObjectPropertyValue(players.GetRawPtr()) == player;
    }

    RC::StringType placed_path(const Profile& profile, RC::StringViewType name)
    {
        RC::StringType path{profile.world};
        path += STR(":PersistentLevel.");
        path += name;
        return path;
    }

    bool is_input(Unreal::FProperty* property, int offset)
    {
        return property && property->GetOffset_Internal() == offset &&
               property->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
               !property->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm);
    }

    bool live_guard(const Guard& current)
    {
        auto* world = current.world.Get();
        auto* pawn = current.pawn.Get();
        auto* bed = current.bed.Get();
        auto* controller = current.controller.Get();
        auto* instance = current.instance.Get();
        auto* net = current.driver.Get();
        auto* local = current.player.Get();
        if (!current.profile || !is_live(world) || !is_live(instance) || !is_live(net) || !is_live(local) ||
            !actor_in_world(bed, world) || !actor_in_world(pawn, world) || !actor_in_world(controller, world) ||
            !has_role(pawn, current.host ? 3 : 2) || !has_role(controller, current.host ? 3 : 2))
            return false;

        auto* level = object_field(world, STR("PersistentLevel"));
        if (!level || static_cast<Unreal::AActor*>(bed)->GetLevel() != level ||
            object_field(world, STR("NetDriver")) != net || object_field(net, STR("World")) != world ||
            object_field(world, STR("OwningGameInstance")) != instance ||
            object_field(pawn, STR("Controller")) != controller || object_field(controller, STR("Pawn")) != pawn ||
            object_field(controller, STR("Player")) != local || !sole_local_player(instance, local) ||
            !field<Unreal::FObjectProperty>(net, STR("ServerConnection"))) return false;

        auto* connection = object_field(net, STR("ServerConnection"));
        if (current.host) return !connection;
        return is_live(connection) && connection == current.server.Get() && connection->IsA(current.connection_type) &&
               object_field(connection, STR("Driver")) == net;
    }

    bool configure_guard(Address pawn_address, bool host)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (guard.generation == epoch && guard.host == host && address(guard.pawn.Get()) == pawn_address)
            return live_guard(guard);

        Guard next;
        next.host = host;
        next.pawn_type = find<Unreal::UClass>(pawn_class);
        next.controller_type = find<Unreal::UClass>(controller_class);
        next.connection_type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* rest_type = find<Unreal::UClass>(bed_class);
        auto* instance_type = find<Unreal::UClass>(instance_class);
        auto* scene_type = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
        if (!next.pawn_type || !next.controller_type || !next.connection_type || !local_type ||
            !rest_type || !instance_type || !scene_type) return false;

        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
            if (candidate->GetClassPrivate() == next.pawn_type && is_live(candidate)) next.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.pawn.Get();
        if (!pawn) return false;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        if (!is_live(world)) return false;
        const auto path = world->GetPathName();
        const auto profile = std::find_if(profiles.begin(), profiles.end(),
            [&](const Profile& candidate) { return path == candidate.world; });
        if (profile == profiles.end()) return false;

        auto* bed = find<Unreal::UObject>(placed_path(*profile, profile->bed).c_str());
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        if (!is_live(bed) || bed->GetClassPrivate() != rest_type ||
            !is_live(controller) || controller->GetClassPrivate() != next.controller_type ||
            !is_live(instance) || instance->GetClassPrivate() != instance_type ||
            !is_live(player) || !player->IsA(local_type)) return false;

        next.profile = &*profile;
        next.bed = bed;
        next.world = world;
        next.controller = controller;
        next.player = player;
        next.instance = instance;
        next.driver = object_field(world, STR("NetDriver"));
        next.server = object_field(next.driver.Get(), STR("ServerConnection"));
        next.interaction = find<Unreal::UFunction>(interaction_path);
        next.sleep = find<Unreal::UFunction>(sleep_path);
        next.bed_script = find<Unreal::UFunction>(bed_script_path);
        next.interaction_player = field<Unreal::FObjectProperty>(next.interaction, STR("Player"));
        auto* hit = field<Unreal::FObjectProperty>(next.interaction, STR("HitComponent"));
        if (!next.interaction || !next.sleep || !next.bed_script ||
            next.interaction->GetOuterPrivate() != rest_type || next.sleep->GetOuterPrivate() != rest_type ||
            next.bed_script->GetOuterPrivate() != rest_type || !is_input(next.interaction_player, 0) ||
            next.interaction_player->GetPropertyClass().Get() != next.pawn_type || !is_input(hit, sizeof(void*)) ||
            hit->GetPropertyClass().Get() != scene_type || next.interaction->GetNumParms() != 2 ||
            next.interaction->GetParmsSize() != 2 * sizeof(void*) || next.interaction->GetReturnProperty() ||
            !live_guard(next)) return false;

        next.generation = epoch;
        next.status = "armed";
        guard = next;
        return true;
    }

    bool exact_bed_profile(Unreal::UObject* bed, const Profile& profile, Unreal::UObject* world)
    {
        auto* faction = field<Unreal::FByteProperty>(bed, STR("Faction"));
        auto* minimum = field<Unreal::FIntProperty>(bed, STR("MinSafeRep"));
        auto* arrow = object_field(bed, STR("Arrow"));
        auto* alternate = object_field(bed, STR("Safe_AltSpawn"));
        if (!faction || faction->GetPropertyValueInContainer(bed) != profile.faction ||
            !minimum || minimum->GetPropertyValueInContainer(bed) != profile.minimum ||
            !is_live(arrow) || arrow->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.ArrowComponent")))
            return false;
        if (profile.alternate.empty()) return !alternate;
        return is_live(alternate) && alternate == find<Unreal::UObject>(placed_path(profile, profile.alternate).c_str()) &&
               alternate->GetClassPrivate() ==
                   find<Unreal::UClass>(STR("/Game/Classes/ScriptingActors/LevelExit_Spawn_BP.LevelExit_Spawn_BP_C")) &&
               authority(alternate, world);
    }

    bool qualified_guest(Unreal::UObject* pawn)
    {
        if (!guard.host) return pawn == guard.pawn.Get();
        auto* world = guard.world.Get();
        if (!is_live(pawn) || pawn == guard.pawn.Get() || pawn->GetClassPrivate() != guard.pawn_type ||
            !authority(pawn, world)) return false;
        auto* controller = object_field(pawn, STR("Controller"));
        if (!is_live(controller) || controller->GetClassPrivate() != guard.controller_type ||
            !authority(controller, world) || object_field(controller, STR("Pawn")) != pawn) return false;
        auto* connection = object_field(controller, STR("Player"));
        auto* net = guard.driver.Get();
        if (!is_live(connection) || !connection->IsA(guard.connection_type) ||
            object_field(connection, STR("Driver")) != net ||
            object_field(connection, STR("OwningActor")) != controller ||
            object_field(connection, STR("PlayerController")) != controller) return false;

        auto* clients = field<Unreal::FArrayProperty>(net, STR("ClientConnections"));
        auto* entry = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(net));
        if (remotes.Num() < 1 || remotes.Num() > max_clients) return false;
        int matches = 0;
        for (int index = 0; index < remotes.Num(); ++index)
            if (entry->GetObjectPropertyValue(remotes.GetRawPtr(index)) == connection) ++matches;
        return matches == 1;
    }

    bool live_scope(const Scope& current)
    {
        auto* world = current.world.Get();
        auto* pawn = current.pawn.Get();
        auto* bed = current.bed.Get();
        auto* controller = current.controller.Get();
        auto* instance = current.instance.Get();
        auto* save = current.save.Get();
        auto* net = current.driver.Get();
        auto* local = current.player.Get();
        if (!is_live(world) || !is_live(instance) || !is_live(save) || !is_live(net) || !is_live(local) ||
            !authority(pawn, world) || !authority(controller, world) || !authority(bed, world) ||
            !is_false(pawn, STR("Dead")) || !is_false(controller, STR("Loading"))) return false;

        auto* level = object_field(world, STR("PersistentLevel"));
        return level && static_cast<Unreal::AActor*>(bed)->GetLevel() == level &&
               object_field(world, STR("NetDriver")) == net && object_field(net, STR("World")) == world &&
               field<Unreal::FObjectProperty>(net, STR("ServerConnection")) && !object_field(net, STR("ServerConnection")) &&
               object_field(world, STR("OwningGameInstance")) == instance &&
               object_field(bed, STR("As Game Instance FP")) == instance &&
               object_field(instance, STR("SaveSlot_Ref")) == save && object_field(pawn, STR("Save")) == save &&
               object_field(pawn, STR("Controller")) == controller && object_field(controller, STR("Pawn")) == pawn &&
               object_field(controller, STR("Player")) == local && sole_local_player(instance, local);
    }

    bool configure(Address pawn_address, Address bed_address)
    {
        if (!configure_guard(pawn_address, true) || address(guard.bed.Get()) != bed_address) return false;
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && address(scope.pawn.Get()) == pawn_address && address(scope.bed.Get()) == bed_address)
        {
            if (!live_scope(scope)) return false;
            scope.expires = Clock::now() + scope_lease;
            return true;
        }

        auto* bed = guard.bed.Get();
        auto* world = guard.world.Get();
        auto* instance = guard.instance.Get();
        auto* save = object_field(instance, STR("SaveSlot_Ref"));
        if (!is_live(save) || save->GetClassPrivate() != find<Unreal::UClass>(save_class) ||
            !exact_bed_profile(bed, *guard.profile, world)) return false;
        Scope next;
        next.pawn = guard.pawn;
        next.bed = guard.bed;
        next.world = guard.world;
        next.controller = guard.controller;
        next.player = guard.player;
        next.driver = guard.driver;
        next.instance = guard.instance;
        next.save = save;
        next.sleep = guard.sleep;
        next.hours = field<Unreal::FIntProperty>(next.sleep, STR("Hours"));
        if (!is_input(next.hours, 0) || next.sleep->GetNumParms() != 1 ||
            next.sleep->GetParmsSize() != sizeof(std::int32_t) || next.sleep->GetReturnProperty() ||
            !live_scope(next)) return false;

        next.generation = epoch;
        next.expires = Clock::now() + scope_lease;
        next.status = "armed";
        scope = next;
        return true;
    }

    Unreal::UObject* qualified_screen(Unreal::FFrame& stack, Unreal::UObject* bed)
    {
        auto* caller = stack.PreviousFrame();
        auto* screen = caller ? caller->Object() : nullptr;
        auto* type = find<Unreal::UClass>(screen_class);
        auto* script = find<Unreal::UFunction>(screen_script);
        return is_live(screen) && type && script && caller->Node() == script && screen->GetClassPrivate() == type &&
               object_field(screen, STR("Bed")) == bed &&
               object_field(screen, STR("As Game Instance FP")) == scope.instance.Get() ? screen : nullptr;
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, const void* locals,
                   Unreal::FFrame* frame)
    {
        if (!Unreal::IsInGameThreadRaw() || !context || !installed.load(std::memory_order_acquire) ||
            iteration.OriginalFunctionCallPrevented()) return;
        const auto epoch = generation.load(std::memory_order_acquire);
        if (guard.generation != epoch || !function) return;
        if (scope.generation == epoch && scope.controller.Get() == context &&
            (function == scope.fixture_menu_mode || function == scope.fixture_gameplay_mode))
        {
            if (live_scope(scope)) iteration.PreventOriginalFunctionCall();
            return;
        }
        if ((function != guard.interaction && function != guard.sleep) ||
            guard.bed.Get() != context || !live_guard(guard)) return;
        if (function == guard.interaction)
        {
            auto* instigator = locals
                ? guard.interaction_player->GetObjectPropertyValue(
                    guard.interaction_player->ContainerPtrToValuePtr<void>(locals))
                : nullptr;
            if (qualified_guest(instigator))
            {
                iteration.PreventOriginalFunctionCall();
                if (iteration.OriginalFunctionCallPrevented())
                {
                    guard.status = "guest_interaction_denied";
                    ++guard.guest_denials;
                }
            }
            return;
        }

        // Stock GoToSleep can load a local save. Only a validated host SleepScreen
        // can publish a selection, and the original must not run on either peer.
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        auto* owner_pawn = object_field(context, STR("Player"));
        if (!guard.host || qualified_guest(owner_pawn))
        {
            guard.status = "guest_sleep_denied";
            ++guard.guest_denials;
            return;
        }
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        if (guard.profile->faction == 0 && owner_pawn == guard.pawn.Get() && caller &&
            caller->Object() == context && caller->Node() == guard.bed_script)
        {
            guard.status = "neutral_insomnia_veto";
            if (scope.generation == epoch) scope.status = guard.status;
            return;
        }
        auto* screen = frame ? qualified_screen(*frame, context) : nullptr;
        if (scope.generation != epoch || !screen || !live_scope(scope) || owner_pawn != scope.pawn.Get())
        {
            guard.status = "unqualified_native_rest_veto";
            return;
        }
        const auto now = Clock::now();
        if (now >= scope.expires)
        {
            scope.status = "stale_veto";
            return;
        }
        auto* count = field<Unreal::FIntProperty>(screen, STR("Count"));
        auto* maximum = field<Unreal::FIntProperty>(screen, STR("Max"));
        const auto hours = locals ? scope.hours->GetPropertyValueInContainer(locals) : -1;
        if (!count || !maximum || maximum->GetPropertyValueInContainer(screen) != max_hours ||
            hours < 0 || hours > max_hours || count->GetPropertyValueInContainer(screen) != hours)
        {
            scope.status = "unqualified_selection_veto";
            return;
        }
        if (scope.pending && now < scope.request_expires)
        {
            scope.status = "pending_selection_veto";
            return;
        }
        scope.pending = true;
        scope.selection = hours;
        scope.request_expires = now + request_lease;
        scope.status = "selected";
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        intercept(iteration, context, frame.Node(), frame.Locals(), &frame);
    }

    void intercept_event(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, void* parameters)
    {
        intercept(iteration, context, function, parameters, nullptr);
    }

    bool bound()
    {
        return installed.load(std::memory_order_acquire) && scope.generation == generation.load(std::memory_order_acquire) &&
               scope.sleep && live_scope(scope);
    }

    int lua_set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_integer(2))
        {
            const auto pawn = lua.get_integer();
            const auto bed = lua.get_integer();
            accepted = pawn > 0 && bed > 0 && configure(static_cast<Address>(pawn), static_cast<Address>(bed));
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_set_guard(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_bool(2))
        {
            const auto pawn = lua.get_integer();
            const auto host = lua.get_bool();
            accepted = pawn > 0 && configure_guard(static_cast<Address>(pawn), host);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_read_guard_status(const Lua& lua)
    {
        const bool current = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 &&
            installed.load(std::memory_order_acquire) && guard.generation == generation.load(std::memory_order_acquire) &&
            live_guard(guard);
        lua.set_bool(current);
        lua.set_string(current ? guard.status : "unbound");
        lua.set_integer(current ? static_cast<std::int64_t>(guard.guest_denials) : 0);
        return 3;
    }

    int lua_take_request(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && scope.pending)
        {
            accepted = bound() && Clock::now() < scope.expires && Clock::now() < scope.request_expires;
            scope.pending = false;
            scope.status = accepted ? "selection_taken" : "selection_expired";
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.pawn.Get())) : 0);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.bed.Get())) : 0);
        lua.set_integer(accepted ? scope.selection : 0);
        return 4;
    }

    int lua_read_status(const Lua& lua)
    {
        const bool current = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && bound();
        lua.set_bool(installed.load(std::memory_order_acquire));
        lua.set_bool(current);
        lua.set_bool(current && Clock::now() < scope.expires);
        lua.set_string(current ? scope.status : "unbound");
        return 4;
    }

    int lua_clear_scope(const Lua&)
    {
        silverfish::native_stock_rest::clear_scope();
        return 0;
    }

    int lua_test_menu_guard(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 1 && lua.is_bool(1) &&
            env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_FIXTURES"))
        {
            scope.fixture_menu_mode = scope.fixture_gameplay_mode = nullptr;
            const bool arm = lua.get_bool();
            if (arm && bound() && Clock::now() < scope.expires)
            {
                auto* menu = find<Unreal::UFunction>(menu_mode);
                auto* game = find<Unreal::UFunction>(gameplay_mode);
                auto* type = scope.controller.Get()->GetClassPrivate();
                if (menu && game && menu->GetOuterPrivate() == type && game->GetOuterPrivate() == type &&
                    !menu->GetReturnProperty() && !game->GetReturnProperty() &&
                    menu->GetNumParms() == 0 && game->GetNumParms() == 1)
                {
                    scope.fixture_menu_mode = menu;
                    scope.fixture_gameplay_mode = game;
                    accepted = true;
                }
            }
            else if (!arm)
            {
                accepted = true;
            }
        }
        lua.set_bool(accepted);
        return 1;
    }
}

namespace silverfish::native_stock_rest
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            intercept_script, {false, false, owner, STR("Stock co-op sleep selection")});
        callbacks[1] = Hook::RegisterProcessEventPreCallback(
            intercept_event, {false, false, owner, STR("Stock co-op rest interaction")});
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

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeStockRestScope", lua_set_scope);
        lua.register_function("SilverfishSetNativeStockRestGuard", lua_set_guard);
        lua.register_function("SilverfishTakeNativeStockRestRequest", lua_take_request);
        lua.register_function("SilverfishReadNativeStockRestStatus", lua_read_status);
        lua.register_function("SilverfishReadNativeStockRestGuardStatus", lua_read_guard_status);
        lua.register_function("SilverfishClearNativeStockRestScope", lua_clear_scope);
        lua.register_function("SilverfishSetNativeStockRestTestMenuGuard", lua_test_menu_guard);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (Unreal::IsInGameThreadRaw())
        {
            scope = {};
            guard = {};
        }
    }
}
