#include "kill_volume.hpp"

#include <array>
#include <chrono>
#include <cstddef>
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

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto volume_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.KillVolume_BP_C_0");
    constexpr auto volume_class = STR("/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto box_class = STR("/Script/Engine.BoxComponent");
    constexpr auto capsule_class = STR("/Script/Engine.CapsuleComponent");
    constexpr auto overlap_name = STR("BndEvt__KillVolume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto overlap_path = STR("/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C:BndEvt__KillVolume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto reload_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Load_GameState");
    constexpr auto intent_lease = std::chrono::seconds(5);

    struct Crossing
    {
        std::int64_t sequence{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
    };

    struct Visit
    {
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr volume;
        std::int64_t overlaps{};
        std::int64_t reloads{};
        std::array<Crossing, 5> active;
    };

    std::array<Hook::GlobalCallbackId, 2> hooks{Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Visit visit;
    thread_local std::int64_t sequence{};
    bool authority_enabled{};

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_TRANSPORT") &&
               env("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES");
    }

    bool installed() { return hooks[0] != Hook::ERROR_ID && hooks[1] != Hook::ERROR_ID; }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* result = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return result && result->GetArrayDim() == 1 ? result : nullptr;
    }

    Unreal::UObject* read_object(Unreal::FObjectProperty* property, const void* data)
    {
        return property && data ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(data)) : nullptr;
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

    bool live(Unreal::UObject* value)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(Unreal::UObject* value, const Unreal::TCHAR* path)
    {
        return live(value) && value->GetClassPrivate() == find<Unreal::UClass>(path);
    }

    bool authority(Unreal::UObject* actor)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            destroying && !destroying->GetPropertyValueInContainer(actor);
    }

    bool is_overlap(Unreal::UObject* box, Unreal::UObject* pawn)
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

    Unreal::UObject* listen_or_client_driver(Unreal::UWorld* world)
    {
        if (!live(world) || world->GetPathName() != world_path) return nullptr;
        auto* driver = object(world, STR("NetDriver"));
        if (!live(driver) || object(driver, STR("World")) != world ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection"))) return nullptr;
        if (!authority_enabled && !live(object(driver, STR("ServerConnection")))) return nullptr;
        return driver;
    }

    bool live_crossing(const Crossing& event, Unreal::UWorld* world)
    {
        auto* pawn = event.pawn.Get();
        auto* controller = event.controller.Get();
        return event.sequence && live(pawn) && live(controller) && pawn->GetWorld() == world && controller->GetWorld() == world &&
            object(pawn, STR("Controller")) == controller && object(controller, STR("Pawn")) == pawn;
    }

    void reset_visit(Unreal::UWorld* world, Unreal::UObject* driver)
    {
        if (visit.world.Get() == world && visit.driver.Get() == driver) return;
        visit = {};
        visit.world = world;
        visit.driver = driver;
    }

    void enqueue(Unreal::UWorld* world, Unreal::UObject* volume, Unreal::UObject* pawn, Unreal::UObject* controller)
    {
        Crossing* empty = nullptr;
        for (auto& event : visit.active)
        {
            if (live_crossing(event, world))
            {
                if (event.pawn.Get() == pawn) return;
            }
            else if (!empty)
            {
                empty = &event;
            }
        }
        if (!empty) return;
        visit.volume = volume;
        empty->sequence = ++sequence;
        empty->expires = Clock::now() + intent_lease;
        empty->pawn = pawn;
        empty->controller = controller;
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, const void* args)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed() || !context || !function ||
            iteration.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName overlap{overlap_name};
        static const Unreal::FName reload{STR("Load_GameState")};
        const auto name = function->GetFName();
        if (name != overlap && name != reload) return;
        auto* world = context->GetWorld();
        auto* driver = listen_or_client_driver(world);
        if (!driver) return;
        reset_visit(world, driver);

        if (name == reload)
        {
            if (context == object(world, STR("OwningGameInstance")) && function == find<Unreal::UFunction>(reload_path))
            {
                iteration.PreventOriginalFunctionCall();
                if (iteration.OriginalFunctionCallPrevented()) ++visit.reloads;
            }
            return;
        }

        if (!exact(context, volume_class) || context->GetPathName() != volume_path ||
            static_cast<Unreal::AActor*>(context)->GetLevel() != object(world, STR("PersistentLevel")) ||
            function != find<Unreal::UFunction>(overlap_path) || function->GetOuterPrivate() != context->GetClassPrivate()) return;
        auto* pawn = read_object(argument(function, STR("OtherActor"), sizeof(void*)), args);
        auto* player_type = find<Unreal::UClass>(pawn_class);
        // The unrelated Damage_Interface branch remains stock, including InstantKill on NPCs.
        if (!pawn || !player_type || !pawn->IsA(player_type)) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        ++visit.overlaps;
        if (!authority_enabled || object(driver, STR("ServerConnection")) || pawn->GetClassPrivate() != player_type ||
            !authority(context) || !authority(pawn) || pawn->GetWorld() != world ||
            function->GetNumParms() != 6 || function->GetReturnProperty()) return;

        auto* box = object(context, STR("Box"));
        auto* capsule = object(pawn, STR("CapsuleComponent"));
        auto* controller = object(pawn, STR("Controller"));
        if (!exact(box, box_class) || box->GetOuterPrivate() != context ||
            !exact(capsule, capsule_class) || capsule->GetOuterPrivate() != pawn ||
            !exact(controller, controller_class) || !authority(controller) || controller->GetWorld() != world ||
            object(controller, STR("Pawn")) != pawn ||
            box != read_object(argument(function, STR("OverlappedComponent"), 0), args) ||
            capsule != read_object(argument(function, STR("OtherComp"), sizeof(void*) * 2), args) ||
            !is_overlap(box, pawn)) return;
        enqueue(world, context, pawn, controller);
    }

    int status(const Lua& lua)
    {
        const bool ready = Unreal::IsInGameThreadRaw() && installed() && lua.get_stack_size() == 0;
        auto* world = ready ? find<Unreal::UWorld>(world_path) : nullptr;
        auto* driver = world ? listen_or_client_driver(world) : nullptr;
        if (driver) reset_visit(world, driver);
        lua.set_bool(driver != nullptr);
        lua.set_integer(driver ? visit.overlaps : 0);
        lua.set_integer(driver ? visit.reloads : 0);
        return 3;
    }

    int next_crossing(const Lua& lua)
    {
        const Crossing* next = nullptr;
        Unreal::UObject* volume = nullptr;
        if (Unreal::IsInGameThreadRaw() && installed() && lua.get_stack_size() == 1 && lua.is_integer(1))
        {
            auto* world = static_cast<Unreal::UWorld*>(visit.world.Get());
            volume = visit.volume.Get();
            if (live(volume) && volume->GetWorld() == world && visit.driver.Get() == listen_or_client_driver(world))
            {
                const auto after = lua.get_integer();
                for (const auto& event : visit.active)
                {
                    if (event.sequence > after && live_crossing(event, world) && (!next || next->sequence > event.sequence)) next = &event;
                }
            }
        }
        lua.set_integer(next ? next->sequence : 0);
        lua.set_integer(next ? reinterpret_cast<std::uintptr_t>(next->pawn.Get()) : 0);
        lua.set_integer(next ? reinterpret_cast<std::uintptr_t>(volume) : 0);
        lua.set_bool(next && Clock::now() >= next->expires);
        return 4;
    }

    int finish_crossing(const Lua& lua)
    {
        bool finished = false;
        if (Unreal::IsInGameThreadRaw() && installed() && lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_integer(2))
        {
            const auto id = lua.get_integer();
            const auto pawn = static_cast<std::uintptr_t>(lua.get_integer());
            for (auto& event : visit.active)
            {
                if (id > 0 && pawn > 0 && event.sequence == id && reinterpret_cast<std::uintptr_t>(event.pawn.Get()) == pawn)
                {
                    event = {};
                    finished = true;
                    break;
                }
            }
        }
        lua.set_bool(finished);
        return 1;
    }
}

namespace silverfish::native_kill_volume
{
    void install_hooks()
    {
        if (!enabled() || installed()) return;
        authority_enabled = env("SILVERFISH_COOP_NATIVE_EVENTS") && env("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
        hooks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::UFunction* function, void* args) {
                intercept(iteration, object, function, args);
            }, {false, false, owner, STR("Guard CrossRoads stock KillVolume callbacks")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*) {
                intercept(iteration, object, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Guard CrossRoads stock KillVolume Blueprint calls")});
        if (!installed()) uninstall_hooks();
    }

    void uninstall_hooks()
    {
        for (auto& id : hooks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeKillVolume", status);
        lua.register_function("SilverfishNextNativeKillVolumeCrossing", next_crossing);
        lua.register_function("SilverfishFinishNativeKillVolumeCrossing", finish_crossing);
    }
}
