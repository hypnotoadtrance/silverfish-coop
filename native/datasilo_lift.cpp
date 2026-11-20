#include "datasilo_lift.hpp"

#include <array>
#include <atomic>
#include <cstdlib>
#include <optional>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P");
    constexpr auto lift_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.RailLift_BP_C_1");
    constexpr auto terminal_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.Terminal_BP_C_1");
    constexpr auto lift_type = STR("/Game/Classes/Actors/RailLift_BP.RailLift_BP_C");
    constexpr auto terminal_type = STR("/Game/Classes/Actors/Terminal_BP.Terminal_BP_C");
    constexpr auto widget_type = STR("/Game/Classes/GUI/Menu_Terminal_Widget.Menu_Terminal_Widget_C");
    constexpr auto pawn_type = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_type = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_type = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto interact_path = STR("/Game/Classes/Actors/RailLift_BP.RailLift_BP_C:SendInteractionToActor");
    constexpr auto power_path = STR("/Game/Classes/Actors/RailLift_BP.RailLift_BP_C:Send_PowerSignal");
    constexpr auto move_path = STR("/Game/Classes/Actors/RailLift_BP.RailLift_BP_C:SendCar");
    constexpr auto graph_path = STR("/Game/Classes/Actors/RailLift_BP.RailLift_BP_C:ExecuteUbergraph_RailLift_BP");
    constexpr auto activate_path = STR("/Game/Classes/GUI/Menu_Terminal_Widget.Menu_Terminal_Widget_C:Activate_FunctionActor");

    // The cooked wrappers enter the graph at these offsets. Latent completion enters at a separate offset.
    constexpr int power_entry = 287;
    constexpr int interaction_entry = 343;
    constexpr int movement_entry = 790;

    std::atomic<bool> installed{};
    std::array<Hook::GlobalCallbackId, 4> hooks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local U::UObject* event_terminal{};
    thread_local U::UObject* script_terminal{};
    thread_local U::FFrame* terminal_frame{};
    thread_local U::UObject* event_lift{};
    thread_local U::UFunction* event_lift_wrapper{};
    thread_local U::UObject* script_lift{};
    thread_local U::FFrame* script_lift_frame{};

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_ZONE"); }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* value, const U::TCHAR* name)
    {
        auto* property = value ? U::CastField<T>(value->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* input(U::UFunction* function, const U::TCHAR* name)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) &&
            property->GetOffset_Internal() >= 0 &&
            property->GetOffset_Internal() + property->GetSize() <= function->GetParmsSize() ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* value)
    {
        return property && value ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(value)) : nullptr;
    }

    U::UObject* object(U::UObject* value, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(value, name), value);
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->IsUnreachable() && !value->HasAnyFlags(excluded);
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool flag(U::UObject* value, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(value, name);
        return property && property->GetPropertyValueInContainer(value) == expected;
    }

    bool stock_actor(U::UObject* value, U::UWorld* world)
    {
        return live(value) && static_cast<U::AActor*>(value)->GetWorld() == world &&
            flag(value, STR("bActorIsBeingDestroyed"), false);
    }

    bool authority(U::UObject* value)
    {
        auto* role = field<U::FByteProperty>(value, STR("Role"));
        return role && role->GetPropertyValueInContainer(value) == 3;
    }

    bool script(U::UFunction* function, const U::TCHAR* path, const U::TCHAR* type)
    {
        return function && function == find<U::UFunction>(path) && function->GetOuterPrivate() == find<U::UClass>(type) &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net);
    }

    U::UObject* array_element(U::UObject* value, const U::TCHAR* name, bool only)
    {
        auto* property = field<U::FArrayProperty>(value, name);
        auto* entry = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!entry) return nullptr;
        U::FScriptArrayHelper values(property, property->ContainerPtrToValuePtr<void>(value));
        const auto count = values.Num();
        return count > 0 && (!only || count == 1) ? entry->GetObjectPropertyValue(values.GetRawPtr(count - 1)) : nullptr;
    }

    struct Scene
    {
        U::UWorld* world{};
        U::UObject* instance{};
        bool client{};

        bool bind(U::UObject* lift)
        {
            if (!exact(lift, lift_type) || lift->GetPathName() != lift_path) return false;
            world = static_cast<U::AActor*>(lift)->GetWorld();
            if (!live(world) || world->GetPathName() != world_path || !stock_actor(lift, world) ||
                static_cast<U::AActor*>(lift)->GetLevel() != object(world, STR("PersistentLevel"))) return false;
            auto* driver = object(world, STR("NetDriver"));
            auto* connection = field<U::FObjectProperty>(driver, STR("ServerConnection"));
            instance = object(world, STR("OwningGameInstance"));
            if (!live(driver) || !connection || object(driver, STR("World")) != world || !exact(instance, instance_type))
                return false;
            auto* server = read(connection, driver);
            client = server != nullptr;
            return client ? live(server) && object(server, STR("Driver")) == driver : authority(lift);
        }

        U::UObject* host_pawn() const
        {
            auto* local = array_element(instance, STR("LocalPlayers"), true);
            auto* controller = object(local, STR("PlayerController"));
            auto* pawn = object(controller, STR("Pawn"));
            auto* canonical = object(instance, STR("SaveSlot_Ref"));
            return !client && exact(local, STR("/Script/Engine.LocalPlayer")) && exact(controller, controller_type) &&
                exact(pawn, pawn_type) && stock_actor(controller, world) && stock_actor(pawn, world) &&
                authority(controller) && authority(pawn) && object(controller, STR("Player")) == local &&
                object(pawn, STR("Controller")) == controller && live(canonical) &&
                object(pawn, STR("Save")) == canonical && object(instance, STR("As Player FP")) == pawn &&
                flag(pawn, STR("Dead"), false) && flag(controller, STR("Loading"), false) ? pawn : nullptr;
        }
    };

    std::optional<U::UObject*> widget_owner(U::UObject* widget)
    {
        auto* function = find<U::UFunction>(STR("/Script/UMG.Widget:GetOwningPlayer"));
        auto* result = function ? U::CastField<U::FObjectProperty>(function->GetReturnProperty()) : nullptr;
        if (!result || result->GetOffset_Internal() != 0 || function->GetNumParms() != 1 ||
            function->GetParmsSize() != sizeof(U::UObject*) || !function->HasAnyFunctionFlags(U::FUNC_Native))
            return {};
        U::UObject* controller{};
        widget->ProcessEvent(function, &controller);
        return controller;
    }

    bool terminal_origin(U::UObject* widget, U::UObject* pawn, const Scene& scene)
    {
        if (!exact(widget, widget_type) || widget->GetWorld() != scene.world) return false;
        auto* controller = object(pawn, STR("Controller"));
        auto* terminal = object(widget, STR("TerminalRef"));
        if (!exact(terminal, terminal_type) || terminal->GetPathName() != terminal_path ||
            !stock_actor(terminal, scene.world) || !authority(terminal) || object(terminal, STR("Player")) != pawn ||
            array_element(controller, STR("MenuStack"), false) != widget) return false;
        const auto player = widget_owner(widget);
        // The stock terminal creates a widget without passing an explicit controller.
        return player && (*player == controller || (!*player &&
            (widget->GetOuterPrivate() == scene.instance || widget->GetOuterPrivate() == scene.world)));
    }

    void admit_movement(U::UObject* receiver, U::UFunction* function, U::FFrame* frame)
    {
        if (frame && !script_lift_frame)
        {
            script_lift = receiver;
            script_lift_frame = frame;
        }
        else if (!frame && !event_lift_wrapper)
        {
            event_lift = receiver;
            event_lift_wrapper = function;
        }
    }

    void intercept(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!available() || !receiver || !function || call.OriginalFunctionCallPrevented()) return;
        static const U::FName activate{STR("Activate_FunctionActor")};
        static const U::FName interact{STR("SendInteractionToActor")};
        static const U::FName power{STR("Send_PowerSignal")};
        static const U::FName move{STR("SendCar")};
        static const U::FName graph{STR("ExecuteUbergraph_RailLift_BP")};
        const auto name = function->GetFName();
        if (name == activate && exact(receiver, widget_type) && script(function, activate_path, widget_type))
        {
            if (!frame) event_terminal = receiver;
            else if (!terminal_frame)
            {
                script_terminal = receiver;
                terminal_frame = frame;
            }
            return;
        }
        if (name != interact && name != power && name != move && name != graph) return;
        Scene scene;
        if (!scene.bind(receiver)) return;

        const bool stock_movement = !scene.client && (receiver == event_lift || receiver == script_lift);
        if (name == graph && script(function, graph_path, lift_type))
        {
            auto* entry = input<U::FIntProperty>(function, STR("EntryPoint"));
            if (!entry || !args) return;
            const auto offset = entry->GetPropertyValueInContainer(args);
            if (!stock_movement && (offset == power_entry || offset == interaction_entry || offset == movement_entry))
                call.PreventOriginalFunctionCall();
        }
        else if (name == move && script(function, move_path, lift_type))
        {
            if (!stock_movement) call.PreventOriginalFunctionCall();
        }
        else if (name == interact && script(function, interact_path, lift_type))
        {
            auto* pawn = scene.host_pawn();
            auto* player = input<U::FObjectProperty>(function, STR("Player"));
            auto* component = input<U::FObjectProperty>(function, STR("HitComponent"));
            auto* hit = read(component, args);
            auto* primitive = find<U::UClass>(STR("/Script/Engine.PrimitiveComponent"));
            if (!pawn || function->GetNumParms() != 2 || function->GetReturnProperty() || read(player, args) != pawn ||
                !live(hit) || !primitive || !hit->IsA(primitive) || hit->GetOuterPrivate() != receiver)
                call.PreventOriginalFunctionCall();
            else admit_movement(receiver, function, frame);
        }
        else if (name == power && script(function, power_path, lift_type))
        {
            auto* pawn = scene.host_pawn();
            auto* powered = input<U::FBoolProperty>(function, STR("Powered"));
            auto* source = input<U::FObjectProperty>(function, STR("Instigator"));
            if (!pawn || !powered || !source || !args || function->GetNumParms() != 2 || function->GetReturnProperty())
            {
                call.PreventOriginalFunctionCall();
                return;
            }
            auto* instigator = read(source, args);
            const bool fixture = instigator == pawn && env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
            if (!fixture && (instigator || (!terminal_origin(script_terminal, pawn, scene) &&
                                           !terminal_origin(event_terminal, pawn, scene))))
                call.PreventOriginalFunctionCall();
            else admit_movement(receiver, function, frame);
        }
    }
}

namespace silverfish::native_datasilo_lift
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        hooks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* value, U::UFunction* function, void* args) {
                intercept(call, value, function, args, nullptr);
            }, {false, false, owner, STR("Guard Data Silo lift event")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* value, U::FFrame& frame, void*) {
                if (frame.Object() == value) intercept(call, value, frame.Node(), frame.Locals(), &frame);
            }, {false, false, owner, STR("Guard Data Silo lift script")});
        hooks[2] = Hook::RegisterProcessEventPostCallback(
            [](Iteration&, U::UObject* value, U::UFunction* function, void*) {
                if (available() && value && value == event_terminal && script(function, activate_path, widget_type))
                    event_terminal = nullptr;
                if (available() && value == event_lift && function == event_lift_wrapper)
                {
                    event_lift = nullptr;
                    event_lift_wrapper = nullptr;
                }
            }, {false, false, owner, STR("Clear Data Silo terminal event context")});
        hooks[3] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            [](Iteration&, U::UObject*, U::FFrame& frame, void*) {
                if (available() && &frame == terminal_frame)
                {
                    script_terminal = nullptr;
                    terminal_frame = nullptr;
                }
                if (available() && &frame == script_lift_frame)
                {
                    script_lift = nullptr;
                    script_lift_frame = nullptr;
                }
            }, {false, false, owner, STR("Clear Data Silo terminal script context")});
        for (const auto hook : hooks)
        {
            if (hook != Hook::ERROR_ID) continue;
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
            if (hook != Hook::ERROR_ID) Hook::UnregisterCallback(hook);
            hook = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishDataSiloLiftGuardAvailable", [](const Lua& state) {
            Scene scene;
            state.set_bool(available() && scene.bind(find<U::UObject>(lift_path)));
            return 1;
        });
    }
}
