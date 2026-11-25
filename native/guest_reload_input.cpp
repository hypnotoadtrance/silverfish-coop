#include "guest_reload_input.hpp"
#include "guest_item.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>
#include <string>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
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
    constexpr auto environment = "SILVERFISH_COOP_NATIVE_GUEST_RELOAD_INPUT";
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto input_path = STR("/Game/Classes/Player/Player_Inputs/IA_Reload.IA_Reload");
    constexpr auto selector_path = STR("/Game/Classes/GUI/New/AmmoTypeSelect.AmmoTypeSelect_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr std::array event_paths{
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Reload_K2Node_EnhancedInputActionEvent_31"),
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Reload_K2Node_EnhancedInputActionEvent_30"),
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Reload_K2Node_EnhancedInputActionEvent_32"),
    };
    constexpr auto press_timeout = std::chrono::seconds(2);
    constexpr auto intent_timeout = std::chrono::seconds(3);
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* prop = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <typename T>
    T* input(Unreal::UFunction* function, const Unreal::TCHAR* name, int offset)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
            !property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm | Unreal::CPF_OutParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize()
            ? property : nullptr;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool env_flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool enabled() { return env_flag(environment); }

    bool fixture_enabled()
    {
        return enabled() && env_flag("SILVERFISH_COOP_HEADLESS") &&
            env_flag("SILVERFISH_COOP_NATIVE_GUEST_RELOAD_FIXTURES");
    }

    struct InputEvent
    {
        Unreal::FWeakObjectPtr function{};
        Unreal::FStructProperty* value{};
        Unreal::FFloatProperty* elapsed{};
        Unreal::FFloatProperty* triggered{};
        Unreal::FObjectProperty* source{};
    };

    struct Schema
    {
        Unreal::FWeakObjectPtr pawn{};
        Unreal::FWeakObjectPtr controller{};
        Unreal::FWeakObjectPtr local_player{};
        Unreal::FWeakObjectPtr connection{};
        Unreal::FWeakObjectPtr input{};
        std::array<InputEvent, 3> events{};
    };

    Schema schema;
    Clock::time_point next_lookup{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    std::atomic<bool> installed{};

    struct ReloadWeapon
    {
        Unreal::FWeakObjectPtr receiver{};
        std::string guid;
        bool underbarrel{};

        bool matches(const ReloadWeapon& other) const
        {
            return receiver.Get() && receiver.Get() == other.receiver.Get() &&
                guid == other.guid && underbarrel == other.underbarrel;
        }
    };

    ReloadWeapon reload_weapon(Unreal::UObject* pawn)
    {
        auto* receiver = object_field(pawn, STR("EquipedItem"));
        auto guid = silverfish::native_guest_item::local_equipped_identity(pawn, receiver);
        if (guid.empty()) return {};
        const auto* intercept = object_field(receiver, STR("InputIntercept"));
        const bool loki = intercept && silverfish::native_guest_item::active_loki_barrel(pawn, receiver) == intercept;
        if (intercept && !loki) return {};
        return {Unreal::FWeakObjectPtr{receiver}, std::move(guid), loki};
    }

    struct Input
    {
        Unreal::FWeakObjectPtr pawn{};
        Clock::time_point expires{};
        ReloadWeapon weapon;
        std::string ammo;
    };

    Input press;
    Input pending;
    std::uint64_t sequence{};

    void bind_schema()
    {
        auto* pawn = find<Unreal::UClass>(pawn_path);
        if (!pawn) return;
        schema.pawn = pawn;
        schema.controller = find<Unreal::UClass>(controller_path);
        schema.local_player = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        schema.connection = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        schema.input = find<Unreal::UObject>(input_path);
        auto* action_type = find<Unreal::UClass>(STR("/Script/EnhancedInput.InputAction"));
        auto* value_type = find<Unreal::UScriptStruct>(STR("/Script/EnhancedInput.InputActionValue"));
        for (std::size_t index = 0; index < event_paths.size(); ++index)
        {
            auto* function = find<Unreal::UFunction>(event_paths[index]);
            if (!function || function->GetOuterPrivate() != pawn) continue;
            auto& event = schema.events[index];
            event.function = function;
            if (function->GetNumParms() != 4 || function->GetParmsSize() != 0x30 || function->GetReturnProperty() ||
                function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) continue;
            auto* value = input<Unreal::FStructProperty>(function, STR("ActionValue"), 0);
            auto* source = input<Unreal::FObjectProperty>(function, STR("SourceAction"), 0x28);
            if (!value || !value_type || value->GetStruct().Get() != value_type || value->GetSize() != 0x20 ||
                !action_type || !source || source->GetPropertyClass().Get() != action_type) continue;
            event.value = value;
            event.elapsed = input<Unreal::FFloatProperty>(function, STR("ElapsedTime"), 0x20);
            event.triggered = input<Unreal::FFloatProperty>(function, STR("TriggeredTime"), 0x24);
            event.source = source;
        }
    }

    // Scope the guard to the physically local client player. A listen host's
    // stock reload remains untouched, even if the feature environment leaks.
    bool local_client(Unreal::UObject* pawn)
    {
        if (!live(pawn) || pawn->GetClassPrivate() != schema.pawn.Get()) return false;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* server = object_field(driver, STR("ServerConnection"));
        auto* local_type = Unreal::Cast<Unreal::UClass>(schema.local_player.Get());
        auto* connection_type = Unreal::Cast<Unreal::UClass>(schema.connection.Get());
        return live(world) && live(controller) && controller->GetClassPrivate() == schema.controller.Get() &&
            object_field(controller, STR("Pawn")) == pawn && static_cast<Unreal::AActor*>(controller)->GetWorld() == world &&
            live(player) && local_type && player->IsA(local_type) && object_field(player, STR("PlayerController")) == controller &&
            live(driver) && object_field(driver, STR("World")) == world && live(server) && connection_type &&
            server->IsA(connection_type) && object_field(server, STR("Driver")) == driver;
    }

    bool standing_autonomous(Unreal::UObject* pawn)
    {
        auto* role = field<Unreal::FByteProperty>(pawn, STR("Role"));
        auto* dead = field<Unreal::FBoolProperty>(pawn, STR("Dead"));
        return role && role->GetPropertyValueInContainer(pawn) == 2 && dead && !dead->GetPropertyValueInContainer(pawn);
    }

    bool close_selector(Unreal::UObject* widget, Unreal::UFunction* function)
    {
        static const Unreal::FName close_name{STR("Close")};
        if (function->GetFName() != close_name || !live(widget)) return false;
        auto* selector = find<Unreal::UClass>(selector_path);
        auto* medical = field<Unreal::FBoolProperty>(widget, STR("Medical"));
        auto* highlighted = field<Unreal::FObjectProperty>(widget, STR("HighlightedButton"));
        auto* pawn = object_field(widget, STR("As Player FP"));
        if (!selector || widget->GetClassPrivate() != selector || !local_client(pawn) ||
            function->GetOuterPrivate() != selector || function->GetNumParms() != 0 || function->GetParmsSize() != 0 ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !medical || medical->GetPropertyValueInContainer(widget) || !highlighted) return false;

        press = {};
        if (auto* changing = field<Unreal::FBoolProperty>(pawn, STR("IsChangingAmmo")))
            changing->SetPropertyValueInContainer(pawn, false);
        auto* selected = object_field(widget, STR("HighlightedButton"));
        if (!live(selected)) return true;
        auto* buttons = field<Unreal::FArrayProperty>(widget, STR("Buttons"));
        auto* entry = buttons ? Unreal::CastField<Unreal::FObjectProperty>(buttons->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper rows(buttons, buttons->ContainerPtrToValuePtr<void>(widget));
        if (rows.Num() <= 0 || rows.Num() > 256 || entry->GetObjectPropertyValue(rows.GetRawPtr(0)) == selected) return true;

        // Stock Close restores the cursor and removes the widget, then ejects the
        // magazine and reloads. Suppress just that inventory branch on the guest.
        highlighted->SetObjectPropertyValue(highlighted->ContainerPtrToValuePtr<void>(widget), nullptr);
        if (!standing_autonomous(pawn) || sequence == max_sequence) return true;
        auto weapon = reload_weapon(pawn);
        auto* receiver = weapon.receiver.Get();
        auto* barrel = weapon.underbarrel ? object_field(receiver, STR("InputIntercept")) : receiver;
        if (weapon.guid.empty() || !barrel || object_field(widget, STR("Weapon")) != barrel) return true;

        bool listed = false;
        for (int index = 1; index < rows.Num(); ++index)
            if (entry->GetObjectPropertyValue(rows.GetRawPtr(index)) == selected) { listed = true; break; }
        const auto* item = field<Unreal::FStructProperty>(selected, STR("Item"));
        auto* item_schema = item ? item->GetStruct().Get() : nullptr;
        auto* cls = item_schema ? Unreal::CastField<Unreal::FClassProperty>(item_schema->GetPropertyByNameInChain(
            STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"))) : nullptr;
        auto* ammo = cls && listed ? Unreal::Cast<Unreal::UClass>(cls->GetObjectPropertyValue(
            cls->ContainerPtrToValuePtr<void>(item->ContainerPtrToValuePtr<void>(selected)))) : nullptr;
        auto* ammo_base = find<Unreal::UClass>(ammo_path);
        if (ammo && ammo_base && ammo != ammo_base && ammo->IsChildOf(ammo_base))
        {
            pending = {pawn, Clock::now() + intent_timeout, std::move(weapon), RC::to_utf8_string(ammo->GetPathName())};
            ++sequence;
        }
        return true;
    }

    void intercept(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, const void* parameters)
    {
        if (!installed.load(std::memory_order_acquire) || !Unreal::IsInGameThreadRaw() || !pawn || !function ||
            iteration.OriginalFunctionCallPrevented()) return;
        if (!schema.pawn.Get() && Clock::now() >= next_lookup)
        {
            next_lookup = Clock::now() + std::chrono::milliseconds(250);
            schema = {};
            bind_schema();
        }
        if (close_selector(pawn, function)) return;
        if (pawn->GetClassPrivate() != schema.pawn.Get()) return;
        const auto match = std::ranges::find_if(schema.events, [&](const auto& event) { return event.function.Get() == function; });
        if (match == schema.events.end() || !local_client(pawn)) return;
        const bool valid_event = parameters && match->elapsed && match->triggered && match->source &&
            schema.input.Get() && standing_autonomous(pawn) &&
            match->source->GetObjectPropertyValue(match->source->ContainerPtrToValuePtr<void>(parameters)) == schema.input.Get();
        const auto elapsed = valid_event ? match->elapsed->GetPropertyValueInContainer(parameters) : -1.0f;
        const auto triggered = valid_event ? match->triggered->GetPropertyValueInContainer(parameters) : -1.0f;
        if (!std::isfinite(elapsed) || elapsed < 0 || !std::isfinite(triggered) || triggered < 0)
        {
            iteration.PreventOriginalFunctionCall();
            press = {};
            return;
        }

        const auto now = Clock::now();
        const auto index = match - schema.events.begin();
        auto* changing_ammo = field<Unreal::FBoolProperty>(pawn, STR("IsChangingAmmo"));
        if (index == 2 && changing_ammo && changing_ammo->GetPropertyValueInContainer(pawn))
        {
            // The stock Completed wrapper only clears this flag while the selector is open.
            press = {};
            return;
        }
        const bool current = press.pawn.Get() == pawn && now < press.expires;
        if (index == 1 && current && press.weapon.matches(reload_weapon(pawn)))
        {
            // Triggered opens the stock selector at its original threshold.
            press.expires = now + press_timeout;
            return;
        }

        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        if (index == 0)
        {
            press = {};
            press.weapon = reload_weapon(pawn);
            if (press.weapon.guid.empty()) return;
            press.pawn = pawn;
            press.expires = now + press_timeout;
            return;
        }
        if (!current || index != 2 || sequence == max_sequence) return;
        auto started = std::exchange(press, {});
        if (!started.weapon.matches(reload_weapon(pawn))) return;
        pending = {started.pawn, now + intent_timeout, std::move(started.weapon)};
        ++sequence;
    }

    struct StructParameter
    {
        Unreal::FStructProperty* property;
        void* parameters;
        StructParameter(Unreal::FStructProperty* property, void* parameters) : property(property), parameters(parameters)
        {
            property->InitializeValue_InContainer(parameters);
        }
        ~StructParameter() { property->DestroyValue_InContainer(parameters); }
        StructParameter(const StructParameter&) = delete;
        StructParameter& operator=(const StructParameter&) = delete;
    };

    struct InputValueFactory
    {
        Unreal::UObject* library{};
        Unreal::UFunction* function{};
        Unreal::FDoubleProperty* x{};
        Unreal::FEnumProperty* type{};
        Unreal::FStructProperty* output{};

        bool bind(Unreal::UObject* action, Unreal::UScriptStruct* value_type)
        {
            library = find<Unreal::UObject>(STR("/Script/EnhancedInput.Default__EnhancedInputLibrary"));
            function = find<Unreal::UFunction>(STR("/Script/EnhancedInput.EnhancedInputLibrary:MakeInputActionValueOfType"));
            if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function ||
                function->GetOuterPrivate() != library->GetClassPrivate() || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
                function->HasAnyFunctionFlags(Unreal::FUNC_Net) || function->GetNumParms() != 5 || function->GetParmsSize() != 0x40)
                return false;
            x = input<Unreal::FDoubleProperty>(function, STR("X"), 0);
            type = input<Unreal::FEnumProperty>(function, STR("ValueType"), 0x18);
            output = field<Unreal::FStructProperty>(function, STR("ReturnValue"));
            auto* action_type = field<Unreal::FEnumProperty>(action, STR("ValueType"));
            auto* enum_type = find<Unreal::UEnum>(STR("/Script/EnhancedInput.EInputActionValueType"));
            auto* number = action_type ? action_type->GetUnderlyingProperty() : nullptr;
            return x && input<Unreal::FDoubleProperty>(function, STR("Y"), 0x08) &&
                input<Unreal::FDoubleProperty>(function, STR("Z"), 0x10) && enum_type && type && type->GetEnum().Get() == enum_type &&
                type->GetSize() == 1 && type->GetUnderlyingProperty() && type->GetUnderlyingProperty()->GetSize() == 1 &&
                action_type && action_type->GetEnum().Get() == enum_type && number && number->GetSize() == 1 &&
                number->GetUnsignedIntPropertyValue(action_type->ContainerPtrToValuePtr<void>(action)) == 0 &&
                output && output == function->GetReturnProperty() && output->GetOffset_Internal() == 0x20 &&
                output->GetSize() == 0x20 && output->GetStruct().Get() == value_type;
        }

        void fill(Unreal::FStructProperty* destination, void* event, bool pressed) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x40> builder{};
            StructParameter result(output, builder.data());
            // Zero-valued Y, Z and EInputActionValueType::Boolean are already initialized.
            x->SetPropertyValueInContainer(builder.data(), pressed ? 1.0 : 0.0);
            library->ProcessEvent(function, builder.data());
            destination->CopyCompleteValue(destination->ContainerPtrToValuePtr<void>(event),
                output->ContainerPtrToValuePtr<void>(builder.data()));
        }
    };

    void dispatch_fixture(Unreal::UObject* pawn, const InputEvent& event, const InputValueFactory& factory,
                          bool pressed, float seconds = -1.0f)
    {
        alignas(std::max_align_t) std::array<std::byte, 0x30> parameters{};
        StructParameter value(event.value, parameters.data());
        factory.fill(event.value, parameters.data(), pressed);
        const auto duration = seconds >= 0 ? seconds : pressed ? 0.0f : 0.3f;
        event.elapsed->SetPropertyValueInContainer(parameters.data(), duration);
        event.triggered->SetPropertyValueInContainer(parameters.data(), duration);
        event.source->SetObjectPropertyValue(event.source->ContainerPtrToValuePtr<void>(parameters.data()), schema.input.Get());
        pawn->ProcessEvent(static_cast<Unreal::UFunction*>(event.function.Get()), parameters.data());
    }

    int fixture_result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        if (!ok) return 2;
        lua.set_integer(static_cast<std::int64_t>(sequence));
        lua.set_integer(2);
        return 4;
    }

    int probe_input(const Lua& lua)
    {
        if (!fixture_enabled() || !Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) ||
            (lua.get_stack_size() != 1 && lua.get_stack_size() != 2) || !lua.is_integer(1) ||
            (lua.get_stack_size() == 2 && !lua.is_string(2)))
            return fixture_result(lua, false, "native_guest_reload_fixture_scope_unavailable");
        const auto edge = lua.get_stack_size() == 2 ? lua.get_string(2) : std::string{};
        if (!edge.empty() && edge != "start" && edge != "cancel" && edge != "hold")
            return fixture_result(lua, false, "native_guest_reload_fixture_scope_unavailable");
        const auto address = lua.get_integer(1);
        if (address <= 0) return fixture_result(lua, false, "native_guest_reload_fixture_exact_pawn_required");
        schema = {};
        bind_schema();
        Unreal::UObject* pawn = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (reinterpret_cast<std::intptr_t>(candidate) != address) return RC::LoopAction::Continue;
            pawn = candidate;
            return RC::LoopAction::Break;
        });
        if (!local_client(pawn) || !standing_autonomous(pawn))
            return fixture_result(lua, false, "native_guest_reload_fixture_exact_local_client_required");
        const auto now = Clock::now();
        const bool pressed = press.pawn.Get() == pawn && now < press.expires;
        if ((edge == "cancel" ? !pressed : pressed) || sequence == max_sequence)
            return fixture_result(lua, false, "native_guest_reload_fixture_input_busy");
        const auto& started = schema.events[0];
        const auto& completed = schema.events[2];
        const auto valid_event = [](const InputEvent& event) {
            return event.function.Get() && event.value && event.elapsed && event.triggered && event.source;
        };
        InputValueFactory factory;
        if (!valid_event(started) || !valid_event(completed) || !live(schema.input.Get()) ||
            started.value->GetStruct().Get() != completed.value->GetStruct().Get() ||
            !factory.bind(schema.input.Get(), started.value->GetStruct().Get()))
            return fixture_result(lua, false, "native_guest_reload_fixture_wrapper_schema_unavailable");

        const auto before = sequence;
        if (edge == "cancel")
        {
            dispatch_fixture(pawn, completed, factory, false);
            const bool cancelled = !press.pawn.Get() && !pending.pawn.Get() && sequence == before;
            return fixture_result(lua, cancelled, cancelled ? "stock_cancelled" : "stock_reload_was_not_cancelled");
        }
        press = {};
        pending = {};
        dispatch_fixture(pawn, started, factory, true);
        if (press.pawn.Get() != pawn || sequence != before)
        {
            press = {};
            pending = {};
            return fixture_result(lua, false, "native_guest_reload_fixture_started_not_guarded");
        }
        if (edge == "start") return fixture_result(lua, true, "stock_started");
        if (edge == "hold")
        {
            const auto& triggered = schema.events[1];
            if (!valid_event(triggered) || triggered.value->GetStruct().Get() != started.value->GetStruct().Get())
                return fixture_result(lua, false, "native_guest_reload_fixture_wrapper_schema_unavailable");
            dispatch_fixture(pawn, triggered, factory, true, 0.35f);
            auto* changing = field<Unreal::FBoolProperty>(pawn, STR("IsChangingAmmo"));
            if (!changing || !changing->GetPropertyValueInContainer(pawn))
                return fixture_result(lua, false, "native_guest_reload_fixture_selector_did_not_open");
            return fixture_result(lua, true, "stock_selector_opened");
        }
        dispatch_fixture(pawn, completed, factory, false);
        if (press.pawn.Get() || pending.pawn.Get() != pawn || sequence != before + 1 || !local_client(pawn))
        {
            press = {};
            pending = {};
            return fixture_result(lua, false, "native_guest_reload_fixture_completed_not_guarded");
        }
        return fixture_result(lua, true, "stock_pressed");
    }

    int read_intent(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 0 || !installed.load(std::memory_order_acquire)) return 0;
        auto* pawn = pending.pawn.Get();
        if (!pawn || Clock::now() >= pending.expires || !local_client(pawn) || !standing_autonomous(pawn)) return 0;
        auto* receiver = object_field(pawn, STR("EquipedItem"));
        if (!receiver || receiver != pending.weapon.receiver.Get() ||
            (object_field(receiver, STR("InputIntercept")) != nullptr) != pending.weapon.underbarrel) return 0;
        lua.set_integer(static_cast<std::int64_t>(sequence));
        lua.set_integer(reinterpret_cast<std::intptr_t>(pawn));
        lua.set_string(pending.weapon.guid);
        lua.set_string(pending.weapon.underbarrel ? "loki" : "primary");
        if (pending.ammo.empty()) return 4;
        lua.set_string(pending.ammo);
        return 5;
    }

    int clear_intent(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            press = {};
            pending = {};
        }
        return 0;
    }
}

namespace silverfish::native_guest_reload_input
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::UFunction* function, void* arguments) {
                intercept(iteration, object, function, arguments);
            }, {false, false, owner, STR("Guard the stock local guest reload input")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*) {
                intercept(iteration, object, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Guard Blueprint guest reload input")});
        if (std::ranges::find(callbacks, Hook::ERROR_ID) == callbacks.end())
            installed.store(true, std::memory_order_release);
        else
            uninstall_hooks();
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeGuestReloadIntent", read_intent);
        lua.register_function("SilverfishClearNativeGuestReloadIntent", clear_intent);
        if (fixture_enabled()) lua.register_function("SilverfishProbeNativeGuestReloadInput", probe_input);
    }
}
