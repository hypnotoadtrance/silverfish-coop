#include "nothing_return.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
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
    constexpr auto world_path = STR("/Game/Classes/Maps/Dreams/Nothing_P.Nothing_P");
    constexpr auto volume_path = STR("/Game/Classes/Maps/Dreams/Nothing_P.Nothing_P:PersistentLevel.KillVolume_BP_C_0");
    constexpr auto volume_class = STR("/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C");
    constexpr auto overlap_name = STR("BndEvt__KillVolume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto overlap_path = STR("/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C:BndEvt__KillVolume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto load_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Load_GameState");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto scope_lease = std::chrono::milliseconds(1250);
    constexpr auto intent_lease = std::chrono::seconds(2);

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        Clock::time_point intent_expires{};
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr volume;
        Unreal::FWeakObjectPtr box;
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr local_player;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr save;
        bool host{};
        bool accepting{};
        bool pending{};
        std::string_view status{"unbound"};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 2> hooks{Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Scope scope;

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_TRANSPORT"); }

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

    Unreal::UObject* read_object(Unreal::FObjectProperty* member, const void* container)
    {
        return member && container ? member->GetObjectPropertyValue(member->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    Unreal::UObject* object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        return read_object(field<Unreal::FObjectProperty>(owner, name), owner);
    }

    Unreal::FObjectProperty* argument(Unreal::UFunction* function, const Unreal::TCHAR* name, std::size_t offset)
    {
        auto* value = field<Unreal::FObjectProperty>(function, name);
        return value && value->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
            !value->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) &&
            value->GetOffset_Internal() == static_cast<int>(offset) &&
            offset + sizeof(void*) <= function->GetParmsSize() ? value : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(Unreal::UObject* object, const Unreal::TCHAR* class_path)
    {
        return live(object) && object->GetClassPrivate() == find<Unreal::UClass>(class_path);
    }

    bool is_false(Unreal::UObject* actor, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FBoolProperty>(actor, name);
        return value && !value->GetPropertyValueInContainer(actor);
    }

    bool authority(Unreal::UObject* actor)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 && is_false(actor, STR("bActorIsBeingDestroyed"));
    }

    bool is_nothing(Unreal::UWorld* world) { return live(world) && world->GetPathName() == world_path; }
    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    Unreal::UObject* local_player(Unreal::UObject* instance)
    {
        auto* array = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* member = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!member) return nullptr;
        Unreal::FScriptArrayHelper players(array, array->ContainerPtrToValuePtr<void>(instance));
        return players.Num() == 1 ? member->GetObjectPropertyValue(players.GetRawPtr()) : nullptr;
    }

    bool qualified_volume(Unreal::UObject* volume, Unreal::UWorld* world)
    {
        auto* level = object(world, STR("PersistentLevel"));
        return level && exact(volume, volume_class) && volume->GetPathName() == volume_path &&
            volume->GetWorld() == world && static_cast<Unreal::AActor*>(volume)->GetLevel() == level;
    }

    bool stock_callbacks(Unreal::UObject* volume, Unreal::UObject* instance)
    {
        auto* event = find<Unreal::UFunction>(overlap_path);
        auto* load = find<Unreal::UFunction>(load_path);
        return event && load && event->GetOuterPrivate() == volume->GetClassPrivate() &&
            load->GetOuterPrivate() == instance->GetClassPrivate() && event->GetNumParms() == 6 &&
            load->GetNumParms() == 2 && !event->GetReturnProperty() && !load->GetReturnProperty() &&
            argument(event, STR("OverlappedComponent"), 0) && argument(event, STR("OtherActor"), sizeof(void*)) &&
            argument(event, STR("OtherComp"), sizeof(void*) * 2) &&
            field<Unreal::FStrProperty>(load, STR("SaveSlot_Name")) && field<Unreal::FBoolProperty>(load, STR("FromDeath"));
    }

    bool qualified(const Scope& value)
    {
        auto* world = static_cast<Unreal::UWorld*>(value.world.Get());
        auto* volume = value.volume.Get();
        auto* box = value.box.Get();
        auto* instance = value.instance.Get();
        auto* driver = value.driver.Get();
        auto* pawn = value.pawn.Get();
        auto* controller = value.controller.Get();
        auto* player = value.local_player.Get();
        if (!is_nothing(world) || !live(instance) || !live(driver) || !live(pawn) || !live(controller) ||
            !live(player) || !live(box) || !qualified_volume(volume, world) ||
            object(volume, STR("Box")) != box || box->GetOuterPrivate() != volume ||
            driver != object(world, STR("NetDriver")) || world != object(driver, STR("World")) ||
            instance != object(world, STR("OwningGameInstance")) || instance->GetWorld() != world ||
            pawn->GetWorld() != world || controller->GetWorld() != world ||
            controller != object(pawn, STR("Controller")) || pawn != object(controller, STR("Pawn")) ||
            player != object(controller, STR("Player")) || player != local_player(instance) ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection"))) return false;

        auto* server = object(driver, STR("ServerConnection"));
        if (!value.host) return live(server);
        auto* save = value.save.Get();
        return !server && authority(pawn) && authority(controller) && authority(volume) && exact(save, save_class) &&
            is_false(pawn, STR("Dead")) && is_false(controller, STR("Loading")) &&
            object(instance, STR("SaveSlot_Ref")) == save && object(pawn, STR("Save")) == save;
    }

    bool configure(Address pawn_address, Address volume_address, bool host, bool accepting)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && scope.host == host && address(scope.pawn.Get()) == pawn_address &&
            address(scope.volume.Get()) == volume_address)
        {
            if (!qualified(scope)) return false;
            scope.expires = Clock::now() + scope_lease;
            scope.accepting = accepting;
            return true;
        }

        auto* world = find<Unreal::UWorld>(world_path);
        auto* volume = find<Unreal::UObject>(volume_path);
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* player = local_player(instance);
        auto* controller = object(player, STR("PlayerController"));
        auto* pawn = object(controller, STR("Pawn"));
        auto* box = object(volume, STR("Box"));
        if (!is_nothing(world) || !qualified_volume(volume, world) || address(volume) != volume_address ||
            !exact(instance, instance_class) || !exact(controller, controller_class) || !exact(pawn, pawn_class) ||
            address(pawn) != pawn_address || !exact(box, STR("/Script/Engine.BoxComponent")) || !stock_callbacks(volume, instance)) return false;

        Scope next;
        next.world = world;
        next.volume = volume;
        next.box = box;
        next.pawn = pawn;
        next.local_player = player;
        next.controller = controller;
        next.driver = object(world, STR("NetDriver"));
        next.instance = instance;
        next.save = object(instance, STR("SaveSlot_Ref"));
        next.host = host;
        next.accepting = accepting;
        if (!qualified(next)) return false;
        next.generation = epoch;
        next.expires = Clock::now() + scope_lease;
        next.status = "armed";
        scope = next;
        return true;
    }

    bool inside(Unreal::UObject* box, Unreal::UObject* pawn)
    {
        struct Params { Unreal::UObject* other{}; bool overlaps{}; } args{pawn};
        auto* function = find<Unreal::UFunction>(STR("/Script/Engine.PrimitiveComponent:IsOverlappingActor"));
        auto* other = argument(function, STR("Other"), offsetof(Params, other));
        auto* result = field<Unreal::FBoolProperty>(function, STR("ReturnValue"));
        if (!other || !result || function->GetNumParms() != 2 || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            function->GetReturnProperty() != result || result->GetOffset_Internal() != offsetof(Params, overlaps) ||
            function->GetParmsSize() < offsetof(Params, overlaps) + sizeof(bool) || function->GetParmsSize() > sizeof(Params)) return false;
        box->ProcessEvent(function, &args);
        return result->GetPropertyValueInContainer(&args);
    }

    bool bound()
    {
        return installed.load(std::memory_order_acquire) && scope.generation == generation.load(std::memory_order_acquire) && qualified(scope);
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, const void* args)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || !context || !function ||
            iteration.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName overlap{overlap_name};
        static const Unreal::FName load{STR("Load_GameState")};
        const auto name = function->GetFName();
        if (name != overlap && name != load) return;
        auto* world = context->GetWorld();
        if (!is_nothing(world)) return;

        if (name == load)
        {
            if (context == object(world, STR("OwningGameInstance")) && function == find<Unreal::UFunction>(load_path))
            {
                iteration.PreventOriginalFunctionCall();
                if (scope.world.Get() == world && !scope.pending) scope.status = "stock_load_vetoed";
            }
            return;
        }

        if (!qualified_volume(context, world) || function != find<Unreal::UFunction>(overlap_path)) return;
        auto* other_field = argument(function, STR("OtherActor"), sizeof(void*));
        auto* other = read_object(other_field, args);
        auto* exact_player = find<Unreal::UClass>(pawn_class);
        if (other && exact_player && !other->IsA(exact_player)) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented() || !other || !exact_player || scope.world.Get() != world) return;

        auto* pawn = scope.pawn.Get();
        if (!scope.host || other != pawn)
        {
            if (!scope.pending) scope.status = "guest_overlap_denied";
            return;
        }
        if (scope.pending) return;
        auto* overlapped = argument(function, STR("OverlappedComponent"), 0);
        auto* component = argument(function, STR("OtherComp"), sizeof(void*) * 2);
        auto* capsule = object(pawn, STR("CapsuleComponent"));
        auto* box = scope.box.Get();
        const auto now = Clock::now();
        if (!bound() || !scope.accepting || scope.volume.Get() != context || now >= scope.expires ||
            other->GetClassPrivate() != exact_player || function->GetNumParms() != 6 || function->GetReturnProperty() ||
            !live(capsule) || capsule->GetOuterPrivate() != pawn || read_object(component, args) != capsule ||
            !box || read_object(overlapped, args) != box || !inside(box, pawn))
        {
            scope.status = "host_overlap_unqualified";
            return;
        }
        scope.pending = true;
        scope.intent_expires = now + intent_lease;
        scope.status = "host_overlap_queued";
    }

    int set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 4 &&
            lua.is_integer(1) && lua.is_integer(2) && lua.is_bool(3) && lua.is_bool(4))
        {
            const auto pawn = lua.get_integer();
            const auto volume = lua.get_integer();
            const auto host = lua.get_bool();
            const auto accepting = lua.get_bool();
            accepted = pawn > 0 && volume > 0 && configure(static_cast<Address>(pawn), static_cast<Address>(volume), host, accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int take_intent(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && scope.pending)
        {
            const auto now = Clock::now();
            accepted = bound() && scope.host && scope.accepting && now < scope.expires && now < scope.intent_expires;
            scope.pending = false;
            scope.status = accepted ? "host_overlap_taken" : "host_overlap_expired";
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.pawn.Get())) : 0);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.volume.Get())) : 0);
        return 3;
    }

    int read_status(const Lua& lua)
    {
        const bool armed = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && bound();
        lua.set_bool(installed.load(std::memory_order_acquire));
        lua.set_bool(armed);
        lua.set_bool(armed && Clock::now() < scope.expires);
        lua.set_string(armed ? scope.status : "unbound");
        return 4;
    }

    int clear(const Lua&)
    {
        silverfish::native_nothing_return::clear_scope();
        return 0;
    }
}

namespace silverfish::native_nothing_return
{
    void install_hooks()
    {
        if (!enabled() || hooks[0] != Hook::ERROR_ID) return;
        hooks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::UFunction* function, void* args) {
                intercept(iteration, object, function, args);
            }, {false, false, owner, STR("Isolate Nothing stock save reload event")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*) {
                intercept(iteration, object, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Isolate Nothing stock save reload script")});
        const bool ready = hooks[0] != Hook::ERROR_ID && hooks[1] != Hook::ERROR_ID;
        if (!ready) uninstall_hooks();
        installed.store(ready, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& id : hooks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishSetNativeNothingScope", set_scope);
        lua.register_function("SilverfishTakeNativeNothingReturn", take_intent);
        lua.register_function("SilverfishReadNativeNothingStatus", read_status);
        lua.register_function("SilverfishClearNativeNothingScope", clear);
    }

    void clear_scope() { generation.fetch_add(1, std::memory_order_acq_rel); }
}
