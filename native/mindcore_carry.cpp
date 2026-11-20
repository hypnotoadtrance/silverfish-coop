#include "mindcore_carry.hpp"
#include "guest_regen.hpp"
#include "interaction_input.hpp"
#include "stock_wallet.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <optional>
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
    namespace U = RC::Unreal;
    namespace Hooks = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hooks::TCallbackIterationData<void>;
    using Address = std::uintptr_t;
    using Clock = std::chrono::steady_clock;
    using Rotator = std::array<double, 3>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P");
    constexpr auto core_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.MindCore_BP_C_1");
    constexpr auto cradle_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.MindCore_Cradle_BP_C_1");
    constexpr auto core_class = STR("/Game/Classes/Actors/MindCore_BP.MindCore_BP_C");
    constexpr auto cradle_class = STR("/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto handle_class = STR("/Script/Engine.PhysicsHandleComponent");
    constexpr auto vector_rotation = STR("/Script/CoreUObject.Rotator");
    constexpr auto pickup_action = STR("/Game/Classes/Player/Player_Inputs/IA_PickUp.IA_PickUp");
    constexpr auto pickup_event = STR("InpActEvt_IA_PickUp_K2Node_EnhancedInputActionEvent_6");
    constexpr auto overlap_event =
        STR("BndEvt__MindCore_Cradle_BP_Capsule_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr auto timeout = std::chrono::milliseconds(950);
    constexpr auto max_serial = (std::uint64_t{1} << 53) - 1;

    enum class Input { none, interact, pickup };
    enum class Action { grab, release, hold };

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* value = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset)
    {
        auto* value = field<T>(function, name);
        return value && value->HasAnyPropertyFlags(U::CPF_Parm) && value->GetOffset_Internal() == offset &&
            offset + value->GetSize() <= function->GetParmsSize() ? value : nullptr;
    }

    U::UObject* object(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* container, const U::TCHAR* name)
    {
        return object(field<U::FObjectProperty>(container, name), container);
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->IsUnreachable() && !value->HasAnyFlags(excluded);
    }

    bool exact(U::UObject* value, const U::TCHAR* path)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(path);
    }

    bool flag(U::UObject* value, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(value, name);
        return property && property->GetPropertyValueInContainer(value) == expected;
    }

    bool actor(U::UObject* value, U::UWorld* world)
    {
        return live(value) && static_cast<U::AActor*>(value)->GetWorld() == world &&
            flag(value, STR("bActorIsBeingDestroyed"), false);
    }

    bool role(U::UObject* value, int desired)
    {
        auto* property = field<U::FByteProperty>(value, STR("Role"));
        return property && property->GetPropertyValueInContainer(value) == desired;
    }

    bool component(U::UObject* value, U::UObject* parent, const U::TCHAR* path)
    {
        return exact(value, path) && value->GetOuterPrivate() == parent;
    }

    Address address(const U::UObject* value) { return reinterpret_cast<Address>(value); }

    U::UFunction* script(const U::TCHAR* path, const U::TCHAR* name)
    {
        const auto qualified = RC::StringType{path} + STR(":") + name;
        auto* function = find<U::UFunction>(qualified.c_str());
        return function && function->GetOuterPrivate() == find<U::UClass>(path) &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    bool no_args(U::UFunction* function)
    {
        return function && !function->GetReturnProperty() && function->GetNumParms() == 0 && function->GetParmsSize() == 0;
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
            size == value.size() && value[0] == '1';
    }

    struct Scene
    {
        U::UWorld* world{};
        U::UObject *core{}, *cradle{}, *mesh{}, *capsule{}, *game{}, *driver{};

        bool bind()
        {
            world = find<U::UWorld>(world_path);
            core = find<U::UObject>(core_path);
            cradle = find<U::UObject>(cradle_path);
            if (!live(world) || !exact(core, core_class) || !exact(cradle, cradle_class) ||
                !actor(core, world) || !actor(cradle, world)) return false;
            auto* level = object(world, STR("PersistentLevel"));
            if (!live(level) || static_cast<U::AActor*>(core)->GetLevel() != level ||
                static_cast<U::AActor*>(cradle)->GetLevel() != level ||
                object(cradle, STR("MindCore_Ref")) != core) return false;
            mesh = object(core, STR("PropMesh"));
            capsule = object(cradle, STR("Capsule"));
            game = object(world, STR("OwningGameInstance"));
            driver = object(world, STR("NetDriver"));
            auto* console = field<U::FNameProperty>(cradle, STR("ConsoleID"));
            const U::FName expected{STR("MC_007_Console"), U::FNAME_Find};
            return component(mesh, core, STR("/Script/Engine.StaticMeshComponent")) &&
                object(core, STR("RootComponent")) == mesh &&
                component(capsule, cradle, STR("/Script/Engine.CapsuleComponent")) && console && !expected.IsNone() &&
                *console->ContainerPtrToValuePtr<U::FName>(cradle) == expected &&
                exact(game, STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")) &&
                live(driver) && object(driver, STR("World")) == world && field<U::FObjectProperty>(driver, STR("ServerConnection"));
        }

        bool dormant() const
        {
            auto* status = field<U::FIntProperty>(cradle, STR("Status"));
            return status && status->GetPropertyValueInContainer(cradle) == 0;
        }

        bool authority() const
        {
            return role(core, 3) && role(cradle, 3) && !object(driver, STR("ServerConnection"));
        }
    };

    std::optional<U::UObject*> grabbed(U::UObject* handle)
    {
        auto* fn = find<U::UFunction>(STR("/Script/Engine.PhysicsHandleComponent:GetGrabbedComponent"));
        auto* result = fn ? U::CastField<U::FObjectProperty>(fn->GetReturnProperty()) : nullptr;
        if (!live(handle) || !result || fn->GetOuterPrivate() != find<U::UClass>(handle_class) ||
            !fn->HasAnyFunctionFlags(U::FUNC_Native) || result->GetOffset_Internal() != 0 ||
            fn->GetNumParms() != 1 || fn->GetParmsSize() != sizeof(U::UObject*)) return {};
        U::UObject* component{};
        handle->ProcessEvent(fn, &component);
        return component;
    }

    bool free_hands(U::UObject* pawn)
    {
        auto* handle = object(pawn, STR("PhysicsHandle"));
        return component(handle, pawn, handle_class) && field<U::FObjectProperty>(pawn, STR("HeldObject")) &&
            !object(pawn, STR("HeldObject")) && grabbed(handle) == static_cast<U::UObject*>(nullptr);
    }

    bool focus(U::UObject* pawn, U::UObject* core)
    {
        auto* range = field<U::FDoubleProperty>(pawn, STR("InteractionRange"));
        auto* distance = field<U::FDoubleProperty>(pawn, STR("ActorLookedAt_Distance"));
        if (!range || !distance || !field<U::FObjectProperty>(pawn, STR("InteractionTarget_Component")) ||
            object(pawn, STR("InteractionTarget_Component")) || object(pawn, STR("ActorBeingLookedAt")) != core) return false;
        const auto limit = range->GetPropertyValueInContainer(pawn);
        const auto target = distance->GetPropertyValueInContainer(pawn);
        return std::isfinite(limit) && limit > 0 && limit <= 1000 && target > 0 && target < limit;
    }

    bool standing(U::UObject* pawn)
    {
        return flag(pawn, STR("Dead"), false);
    }

    bool living_save(U::UObject* save)
    {
        auto* hp = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
        const auto value = hp ? hp->GetPropertyValueInContainer(save) : 0.0;
        return std::isfinite(value) && value > 0;
    }

    U::UObject* host_pawn(const Scene& scene)
    {
        auto* host = object(scene.game, STR("As Player FP"));
        auto* controller = object(host, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        return exact(host, pawn_class) && actor(host, scene.world) && role(host, 3) &&
            exact(controller, controller_class) && actor(controller, scene.world) && role(controller, 3) &&
            exact(player, STR("/Script/Engine.LocalPlayer")) && object(player, STR("PlayerController")) == controller &&
            object(controller, STR("Pawn")) == host && object(host, STR("Save")) == object(scene.game, STR("SaveSlot_Ref"))
            ? host : nullptr;
    }

    struct Functions
    {
        U::UFunction *grab{}, *hold{}, *stats{};
        U::FBoolProperty *throwing{}, *dropping{}, *pickup{};

        bool bind()
        {
            grab = script(pawn_class, STR("Grab_Release_Object"));
            hold = script(pawn_class, STR("HoldObject"));
            stats = script(pawn_class, STR("ApplyDynamicStats"));
            throwing = parameter<U::FBoolProperty>(grab, STR("Throw"), 0);
            dropping = parameter<U::FBoolProperty>(grab, STR("Drop"), 1);
            pickup = parameter<U::FBoolProperty>(grab, STR("PickUp_Items"), 2);
            return throwing && dropping && pickup && grab->GetNumParms() == 3 &&
                grab->GetParmsSize() >= 3 && grab->GetParmsSize() <= 4 &&
                !grab->GetReturnProperty() && no_args(hold) && no_args(stats);
        }

        void invoke(U::UObject* pawn, Action action) const
        {
            if (action == Action::hold)
            {
                pawn->ProcessEvent(hold, nullptr);
                return;
            }
            std::array<std::byte, 4> params{};
            dropping->SetPropertyValueInContainer(params.data(), action == Action::release);
            pawn->ProcessEvent(grab, params.data());
        }
    };

    std::optional<Rotator> rotation(U::UObject* camera, bool relative)
    {
        auto* function = relative ? nullptr : find<U::UFunction>(STR("/Script/Engine.SceneComponent:K2_GetComponentRotation"));
        auto* property = relative ? field<U::FStructProperty>(camera, STR("RelativeRotation"))
            : function ? U::CastField<U::FStructProperty>(function->GetReturnProperty()) : nullptr;
        if (!property || property->GetStruct().Get() != find<U::UScriptStruct>(vector_rotation) ||
            property->GetSize() != sizeof(Rotator)) return {};
        Rotator value{};
        if (relative) std::memcpy(value.data(), property->ContainerPtrToValuePtr<void>(camera), sizeof(value));
        else
        {
            if (function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(value) ||
                property->GetOffset_Internal() != 0 || !function->HasAnyFunctionFlags(U::FUNC_Native)) return {};
            camera->ProcessEvent(function, value.data());
        }
        for (const auto angle : value) if (!std::isfinite(angle)) return {};
        return value;
    }

    bool same_rotation(const std::optional<Rotator>& actual, const Rotator& expected)
    {
        if (!actual) return false;
        for (std::size_t i = 0; i < expected.size(); ++i)
            if (std::abs(std::remainder((*actual)[i] - expected[i], 360.0)) > .05) return false;
        return true;
    }

    bool set_rotation(U::UObject* camera, const Rotator& rotation, bool relative)
    {
        auto* function = find<U::UFunction>(relative ? STR("/Script/Engine.SceneComponent:K2_SetRelativeRotation")
            : STR("/Script/Engine.SceneComponent:K2_SetWorldRotation"));
        auto* value = parameter<U::FStructProperty>(function, STR("NewRotation"), 0);
        auto* hit = parameter<U::FStructProperty>(function, STR("SweepHitResult"), 0x20);
        auto* sweep = parameter<U::FBoolProperty>(function, STR("bSweep"), 0x18);
        auto* teleport = parameter<U::FBoolProperty>(function, STR("bTeleport"), 0x118);
        alignas(16) std::array<std::byte, 512> params{};
        if (!value || value->GetStruct().Get() != find<U::UScriptStruct>(vector_rotation) || value->GetSize() != sizeof(rotation) ||
            !hit || hit->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/Engine.HitResult")) ||
            !hit->HasAnyPropertyFlags(U::CPF_OutParm) || !sweep || !teleport ||
            function->GetOuterPrivate() != find<U::UClass>(STR("/Script/Engine.SceneComponent")) ||
            !function->HasAnyFunctionFlags(U::FUNC_Native) || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->GetParmsSize() > params.size()) return false;
        hit->InitializeValue_InContainer(params.data());
        std::memcpy(value->ContainerPtrToValuePtr<void>(params.data()), rotation.data(), sizeof(rotation));
        sweep->SetPropertyValueInContainer(params.data(), false);
        teleport->SetPropertyValueInContainer(params.data(), true);
        camera->ProcessEvent(function, params.data());
        hit->DestroyValue_InContainer(params.data());
        return true;
    }

    struct GuestInput
    {
        U::FWeakObjectPtr pawn{}, core{};
        Clock::time_point expires{};
        bool holder{};
        std::uint64_t pending{};
        Input input{};
        Action intent{};
    };
    GuestInput guest;
    std::uint64_t sequence{};

    struct Holder
    {
        U::FWeakObjectPtr pawn{}, save{}, canonical{}, core{}, cradle{}, handle{}, camera{};
        Clock::time_point expires{};
        Rotator look{};

        bool same_scene(const Scene& scene) const
        {
            return core.Get() == scene.core && cradle.Get() == scene.cradle;
        }

        bool exact_seat(const Scene& scene) const
        {
            auto* p = pawn.Get();
            return p && same_scene(scene) && scene.authority() && actor(p, scene.world) && role(p, 3) &&
                exact(p, pawn_class) && live(save.Get()) && live(canonical.Get()) && save.Get() != canonical.Get() &&
                object(scene.game, STR("SaveSlot_Ref")) == canonical.Get() && object(p, STR("Save")) == save.Get() &&
                component(handle.Get(), p, handle_class) && object(p, STR("PhysicsHandle")) == handle.Get() &&
                component(camera.Get(), p, STR("/Script/Engine.CameraComponent")) && object(p, STR("Camera")) == camera.Get();
        }

        bool held(const Scene& scene) const
        {
            return exact_seat(scene) && object(pawn.Get(), STR("HeldObject")) == scene.core && grabbed(handle.Get()) == scene.mesh;
        }
    };
    std::optional<Holder> holder;
    U::FWeakObjectPtr managed_core{}, guarded_host{}, guarded_cradle{};
    Clock::time_point mount_fence{};
    std::string_view latest_state{"idle"}, latest_reason{"none"};
    Address receipt_pawn{};
    U::UObject *bracket_pawn{}, *pickup_pawn{};
    bool inside_overlap{};
    std::atomic<bool> installed{};
    std::array<Hooks::GlobalCallbackId, 4> hooks{Hooks::ERROR_ID, Hooks::ERROR_ID, Hooks::ERROR_ID, Hooks::ERROR_ID};
    U::FName grab_name{}, hold_name{}, stats_name{}, overlap_name{}, pickup_name{};

    bool available()
    {
        return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw();
    }

    void status(U::UObject* pawn, std::string_view state, std::string_view reason)
    {
        if (pawn) receipt_pawn = address(pawn);
        latest_state = state;
        latest_reason = reason;
    }

    bool run_stock(const Functions& functions, const Holder& current, Action action)
    {
        auto* pawn = current.pawn.Get();
        if (!pawn || bracket_pawn) return false;
        if (action == Action::release)
        {
            bracket_pawn = pawn;
            functions.invoke(pawn, action);
            bracket_pawn = nullptr;
            return true;
        }
        auto* camera = current.camera.Get();
        const auto saved = rotation(camera, true);
        if (!saved) return false;
        const bool aimed = set_rotation(camera, current.look, false) &&
            same_rotation(rotation(camera, false), current.look);
        if (aimed)
        {
            bracket_pawn = pawn;
            functions.invoke(pawn, action);
            bracket_pawn = nullptr;
        }
        const bool restored = set_rotation(camera, *saved, true) && same_rotation(rotation(camera, true), *saved);
        return aimed && restored;
    }

    bool release(const Scene& scene, std::string_view reason)
    {
        if (!holder) return false;
        auto* pawn = holder->pawn.Get();
        Functions schema;
        if (!holder->held(scene) || !schema.bind() ||
            !run_stock(schema, *holder, Action::release) || !free_hands(pawn))
        {
            status(pawn, "fault", "mutation_unverified_stock_core_release");
            return false;
        }
        holder.reset();
        status(pawn, "released", reason);
        return true;
    }

    void frame_tick()
    {
        if (!available() || !holder || bracket_pawn) return;
        auto* pawn = holder->pawn.Get();
        Scene scene;
        if (!scene.bind() || !holder->same_scene(scene) || (!pawn && !holder->handle.Get()))
        {
            holder.reset();
            status(pawn, "released", "stock_core_scope_ended");
            return;
        }
        if (!holder->exact_seat(scene))
        {
            holder.reset();
            status(pawn, "fault", "stock_core_holder_scope_lost");
            return;
        }
        if (!holder->held(scene))
        {
            if (free_hands(pawn)) status(pawn, "released", "stock_core_released_by_engine");
            else status(pawn, "fault", "mutation_unverified_stock_core_holder");
            holder.reset();
            return;
        }
        if (Clock::now() >= holder->expires || !scene.dormant() || !standing(pawn) || !living_save(holder->save.Get()) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, holder->save.Get(), holder->canonical.Get()))
        {
            release(scene, "stock_core_lease_expired");
            return;
        }
        Functions functions;
        if (!functions.bind() || !run_stock(functions, *holder, Action::hold))
        {
            const bool released = holder->held(scene) ? release(scene, "stock_core_watchdog_release") : free_hands(pawn);
            if (released) holder.reset();
            status(pawn, "fault", "mutation_unverified_stock_core_hold_pose");
            return;
        }
        if (free_hands(pawn))
        {
            holder.reset();
            status(pawn, "released", "stock_core_released_by_engine");
        }
    }

    bool guest_scene(Scene& scene, U::UObject* pawn)
    {
        return pawn && scene.bind() && actor(pawn, scene.world) && scene.dormant() && standing(pawn) &&
            role(scene.core, 1) && flag(scene.core, STR("bReplicateMovement"), true) &&
            flag(scene.capsule, STR("bGenerateOverlapEvents"), false) && live(object(scene.driver, STR("ServerConnection")));
    }

    bool frame_matches(U::FFrame* frame, U::UObject* key, U::UFunction* function)
    {
        return function && frame && frame->Object() == key && frame->Node() == function;
    }

    bool pickup_source(U::UFunction* event, const void* args)
    {
        if (!event || event != script(pawn_class, pickup_event) || event->GetNumParms() != 4) return false;
        auto* source = parameter<U::FObjectProperty>(event, STR("SourceAction"), 0x28);
        auto* action = find<U::UObject>(pickup_action);
        return live(action) && source && source->GetPropertyClass().Get() == find<U::UClass>(STR("/Script/EnhancedInput.InputAction")) &&
            object(source, args) == action;
    }

    bool mapped_pickup(U::UObject* pawn, U::FFrame* frame)
    {
        if (pickup_pawn == pawn) return true;
        auto* graph = frame ? frame->PreviousFrame() : nullptr;
        auto* wrapper = graph ? graph->PreviousFrame() : nullptr;
        return frame_matches(graph, pawn, script(pawn_class, STR("ExecuteUbergraph_Player_FP"))) &&
            wrapper && wrapper->Object() == pawn && pickup_source(wrapper->Node(), wrapper->Locals());
    }

    void local_input(Iteration& call, U::UObject* pawn, const Functions& functions, const void* args, U::FFrame* frame)
    {
        if (!args || !frame || !guest.pawn.Get() || guest.pawn.Get() != pawn || Clock::now() >= guest.expires ||
            functions.throwing->GetPropertyValueInContainer(args) || functions.dropping->GetPropertyValueInContainer(args) ||
            silverfish::native_interaction_input::current_guest_pawn(address(pawn)) != pawn) return;
        Scene scene;
        if (!guest_scene(scene, pawn) || scene.core != guest.core.Get() || !free_hands(pawn) ||
            (!guest.holder && !focus(pawn, scene.core))) return;
        const bool pickup = functions.pickup->GetPropertyValueInContainer(args);
        if (pickup ? !mapped_pickup(pawn, frame) : !silverfish::native_interaction_input::consume_current_press(pawn))
            return;
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented() && !guest.pending && sequence < max_serial)
        {
            guest.pending = ++sequence;
            guest.input = pickup ? Input::pickup : Input::interact;
            guest.intent = guest.holder ? Action::release : Action::grab;
        }
    }

    void capsule_overlap(Iteration& call, U::UObject* value, U::UFunction* function, const void* args)
    {
        // Only an exact, already managed Core can authorize the stock Player0 protection.
        if (!args || (!holder && !managed_core.Get())) return;
        Scene scene;
        if (!scene.bind() || !scene.authority() || !scene.dormant() || scene.cradle != value ||
            !flag(scene.capsule, STR("bGenerateOverlapEvents"), true) ||
            function != script(cradle_class, overlap_event) || function->GetNumParms() != 6 ||
            object(parameter<U::FObjectProperty>(function, STR("OverlappedComponent"), 0), args) != scene.capsule ||
            object(parameter<U::FObjectProperty>(function, STR("OtherActor"), 8), args) != scene.core) return;
        auto* other = object(parameter<U::FObjectProperty>(function, STR("OtherComp"), 16), args);
        if (!component(other, scene.core, STR("/Script/Engine.StaticMeshComponent"))) return;
        if (inside_overlap)
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        if (holder && holder->same_scene(scene))
        {
            inside_overlap = true;
            const bool released = release(scene, "stock_core_capsule_release");
            inside_overlap = false;
            if (!released)
            {
                call.PreventOriginalFunctionCall();
                return;
            }
        }
        if (managed_core.Get() != scene.core) return;
        auto* host = host_pawn(scene);
        auto* unrelated = object(host, STR("HeldObject"));
        if (live(unrelated) && unrelated != scene.core)
        {
            guarded_host = host;
            guarded_cradle = scene.cradle;
            mount_fence = Clock::now() + timeout;
        }
    }

    void intercept(Iteration& call, U::UObject* value, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!available() || !function || !value || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        if (name == pickup_name && !frame && guest.pawn.Get() == value &&
            Clock::now() < guest.expires && pickup_source(function, args))
        {
            pickup_pawn = value;
            return;
        }
        if (name == overlap_name)
        {
            capsule_overlap(call, value, function, args);
            return;
        }
        if (name != grab_name && name != hold_name && name != stats_name) return;
        if (name == stats_name)
        {
            if (value == bracket_pawn && function == script(pawn_class, STR("ApplyDynamicStats")) && no_args(function))
                call.PreventOriginalFunctionCall();
            return;
        }
        if (value == bracket_pawn) return;
        const bool managed_holder = holder && value == holder->pawn.Get();
        const bool guest_input = guest.pawn.Get() == value && Clock::now() < guest.expires;
        const bool mount_host = guarded_host.Get() == value && Clock::now() < mount_fence;
        if (!holder && !guest_input && !mount_host) return;
        Functions functions;
        if (!functions.bind()) return;
        if (function == functions.hold && managed_holder)
        {
            Scene scene;
            if (scene.bind() && holder->held(scene)) call.PreventOriginalFunctionCall();
            return;
        }
        if (function != functions.grab || !args) return;
        if (mount_host && functions.dropping->GetPropertyValueInContainer(args) &&
            !functions.throwing->GetPropertyValueInContainer(args) &&
            !functions.pickup->GetPropertyValueInContainer(args))
        {
            auto* ancestry = frame ? frame->PreviousFrame() : nullptr;
            auto* graph = script(cradle_class, STR("ExecuteUbergraph_MindCore_Cradle_BP"));
            auto* cradle = guarded_cradle.Get();
            if (frame_matches(ancestry, cradle, graph))
            {
                Scene scene;
                auto* prop = object(value, STR("HeldObject"));
                if (scene.bind() && scene.authority() && scene.cradle == cradle && host_pawn(scene) == value &&
                    live(prop) && prop != scene.core)
                {
                    call.PreventOriginalFunctionCall();
                    return;
                }
            }
        }
        if (guest_input)
        {
            local_input(call, value, functions, args, frame);
            return;
        }
        // Native stock Grab does not itself exclude a second grab of the same live component.
        if (holder && !managed_holder && !functions.throwing->GetPropertyValueInContainer(args) &&
            !functions.dropping->GetPropertyValueInContainer(args) && focus(value, holder->core.Get()))
        {
            Scene scene;
            if (scene.bind() && holder->held(scene) && host_pawn(scene) == value && free_hands(value))
                call.PreventOriginalFunctionCall();
        }
    }

    void clear_guest() { guest = {}; }

    int set_guest(const Lua& lua)
    {
        const auto report = [&](bool ok) { lua.set_bool(ok); return 1; };
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2)) return report(false);
        const auto requested = lua.get_integer();
        const bool owns = lua.get_bool();
        auto* pawn = requested > 0 ? silverfish::native_interaction_input::current_guest_pawn(static_cast<Address>(requested)) : nullptr;
        Scene scene;
        if (!guest_scene(scene, pawn))
        {
            clear_guest();
            return report(false);
        }
        const auto now = Clock::now();
        if (guest.pawn.Get() != pawn || guest.core.Get() != scene.core || now >= guest.expires) clear_guest();
        guest.pawn = pawn;
        guest.core = scene.core;
        guest.expires = now + timeout;
        guest.holder = owns;
        return report(true);
    }

    int take_guest(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1) || !guest.pending ||
            Clock::now() >= guest.expires) return 0;
        const auto requested = lua.get_integer();
        auto* pawn = guest.pawn.Get();
        Scene scene;
        if (requested <= 0 || static_cast<Address>(requested) != address(pawn) ||
            silverfish::native_interaction_input::current_guest_pawn(address(pawn)) != pawn ||
            !guest_scene(scene, pawn) || scene.core != guest.core.Get()) return 0;
        lua.set_integer(static_cast<std::int64_t>(guest.pending));
        lua.set_string(guest.intent == Action::grab ? "grab" : "release");
        lua.set_string(guest.input == Input::interact ? "interact" : "pickup");
        guest.pending = 0;
        return 3;
    }

    int run_authority(const Lua& lua)
    {
        const auto report = [&](bool ok, std::string_view reason) { lua.set_bool(ok); lua.set_string(reason); return 2; };
        if (!available() || bracket_pawn || lua.get_stack_size() != 8 || !lua.is_string(6) ||
            !lua.is_number(7) || !lua.is_number(8)) return report(false, "stock_core_carry_arguments");
        std::array<Address, 5> addresses{};
        for (auto& value : addresses)
        {
            if (!lua.is_integer(1)) return report(false, "stock_core_carry_arguments");
            const auto integer = lua.get_integer();
            if (integer <= 0) return report(false, "stock_core_carry_arguments");
            value = static_cast<Address>(integer);
        }
        const auto verb = lua.get_string();
        const auto pitch = lua.get_number();
        const auto yaw = lua.get_number();
        if ((verb != "grab" && verb != "hold" && verb != "release") ||
            !std::isfinite(pitch) || std::abs(pitch) > 89.9 || !std::isfinite(yaw) || std::abs(yaw) > 180)
            return report(false, "stock_core_carry_arguments");
        Scene scene;
        if (!scene.bind() || !scene.authority() || address(scene.core) != addresses[3] || address(scene.cradle) != addresses[4])
            return report(false, "stock_core_carry_scene");
        if (verb == "release")
        {
            if (!holder || !holder->same_scene(scene) || address(holder->pawn.Get()) != addresses[0] ||
                address(holder->save.Get()) != addresses[1] || address(holder->canonical.Get()) != addresses[2])
                return report(false, "stock_core_carry_owner");
            const bool released = release(scene, "stock_core_released");
            return report(released, released ? "stock_core_released" : latest_reason);
        }
        if (!scene.dormant() || !flag(scene.core, STR("bReplicates"), true) ||
            !flag(scene.core, STR("bReplicateMovement"), true))
            return report(false, "stock_core_carry_not_dormant_or_replicating");
        U::UObject* pawn{};
        if (holder && address(holder->pawn.Get()) == addresses[0]) pawn = holder->pawn.Get();
        else if (!holder && verb == "grab")
        {
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (address(candidate) != addresses[0]) return RC::LoopAction::Continue;
                pawn = candidate;
                return RC::LoopAction::Break;
            });
        }
        auto* detached = object(pawn, STR("Save"));
        auto* canonical = object(scene.game, STR("SaveSlot_Ref"));
        if (!pawn || address(detached) != addresses[1] || address(canonical) != addresses[2] ||
            !standing(pawn) || !living_save(detached) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, detached, canonical))
            return report(false, "stock_core_carry_owner");
        const Rotator look{pitch, yaw, 0.0};
        if (verb == "hold")
        {
            if (!holder || !holder->held(scene)) return report(false, "stock_core_carry_owner");
            holder->look = look;
            holder->expires = Clock::now() + timeout;
            status(pawn, "held", "stock_core_renewed");
            return report(true, "stock_core_renewed");
        }
        auto* host = host_pawn(scene);
        auto* host_handle = object(host, STR("PhysicsHandle"));
        if (holder || !host || !component(host_handle, host, handle_class))
            return report(false, "stock_core_carry_grab_unqualified");
        const auto host_held = grabbed(host_handle);
        const silverfish::stock_wallet::Wallet flags;
        if (!host_held || *host_held == scene.mesh || object(host, STR("HeldObject")) == scene.core || !focus(pawn, scene.core) ||
            !free_hands(pawn) || !flags.bind(scene.game) || flags.flag_value(scene.game, STR("MC_007_Console")).first != 0)
            return report(false, "stock_core_carry_grab_unqualified");
        Holder next{pawn, detached, canonical, scene.core, scene.cradle, object(pawn, STR("PhysicsHandle")),
            object(pawn, STR("Camera")), Clock::now() + timeout, look};
        Functions functions;
        if (!next.exact_seat(scene) || !functions.bind()) return report(false, "stock_core_carry_schema");
        const bool invoked = run_stock(functions, next, Action::grab);
        if (!next.held(scene))
        {
            if (free_hands(pawn)) return report(false, invoked ? "stock_core_grab_refused" : "stock_core_carry_pose");
            status(pawn, "fault", "mutation_unverified_stock_core_grab");
            return report(false, latest_reason);
        }
        holder = next;
        managed_core = scene.core;
        if (!invoked)
        {
            release(scene, "stock_core_watchdog_release");
            status(pawn, "fault", "mutation_unverified_stock_core_grab_pose");
            return report(false, latest_reason);
        }
        status(pawn, "held", "stock_core_grabbed");
        return report(true, "stock_core_grabbed");
    }

    int read_authority(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 0) return 0;
        lua.set_string(latest_state);
        lua.set_string(latest_reason);
        lua.set_integer(static_cast<std::int64_t>(receipt_pawn));
        return 3;
    }
}

namespace silverfish::native_mindcore_carry
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        grab_name = U::FName{STR("Grab_Release_Object"), U::FNAME_Add};
        hold_name = U::FName{STR("HoldObject"), U::FNAME_Add};
        stats_name = U::FName{STR("ApplyDynamicStats"), U::FNAME_Add};
        overlap_name = U::FName{overlap_event, U::FNAME_Add};
        pickup_name = U::FName{pickup_event, U::FNAME_Add};
        hooks[0] = Hooks::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* object, U::UFunction* function, void* args) {
                intercept(call, object, function, args, nullptr);
            }, {false, false, owner, STR("Fence one stock MindCore carry callback")});
        hooks[1] = Hooks::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* object, U::FFrame& frame, void*) {
                if (frame.Object() == object) intercept(call, object, frame.Node(), frame.Locals(), &frame);
            }, {false, false, owner, STR("Route mapped stock MindCore carry")});
        hooks[2] = Hooks::RegisterEngineTickPreCallback(
            [](Iteration&, U::UEngine*, float, bool) { frame_tick(); },
            {false, false, owner, STR("Maintain verified guest MindCore physics handle")});
        hooks[3] = Hooks::RegisterProcessEventPostCallback(
            [](Iteration&, U::UObject* object, U::UFunction* function, void*) {
                if (available() && pickup_pawn == object && function && function->GetFName() == pickup_name)
                    pickup_pawn = nullptr;
            }, {false, false, owner, STR("Clear exact mapped MindCore pickup context")});
        for (const auto hook : hooks)
        {
            if (hook != Hooks::ERROR_ID) continue;
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        for (auto& hook : hooks)
        {
            if (hook != Hooks::ERROR_ID) Hooks::UnregisterCallback(hook);
            hook = Hooks::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishSetNativeMindCoreCarryInputScope", set_guest);
        lua.register_function("SilverfishTakeNativeMindCoreCarryInput", take_guest);
        lua.register_function("SilverfishClearNativeMindCoreCarryInputScope", [](const Lua&) {
            if (available()) clear_guest();
            return 0;
        });
        lua.register_function("SilverfishRunNativeMindCoreCarry", run_authority);
        lua.register_function("SilverfishReadNativeMindCoreCarry", read_authority);
    }
}
