#include "guest_primary_input.hpp"

#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

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
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::intptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto action_path = STR("/Game/Classes/Player/Player_Inputs/IA_Use_Primary.IA_Use_Primary");
    constexpr auto started_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36");
    constexpr auto completed_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_37");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_struct = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto item_class = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto item_guid = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr auto scope_lease = std::chrono::seconds(2);
    constexpr auto receipt_lifetime = std::chrono::milliseconds(1500);
    constexpr auto release_lease = std::chrono::seconds(12);
    constexpr std::size_t receipt_limit = 8;
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;

    struct Weapon
    {
        std::string_view kind;
        const U::TCHAR* path;
    };

    constexpr std::array weapons{
        Weapon{"smg", STR("/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C")},
        Weapon{"light-pistol", STR("/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C")},
        Weapon{"nagant-scoped", STR("/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C")},
    };

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* input(U::UFunction* function, const U::TCHAR* name, int offset)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize() ?
            property : nullptr;
    }

    U::UObject* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool false_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property && !property->GetPropertyValueInContainer(object);
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT") && env("SILVERFISH_COOP_NATIVE_ZONE");
    }

    bool fixtures()
    {
        return env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES");
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool parse_guid(std::string_view text, Guid& value)
    {
        if (text.size() != 32) return false;
        for (std::size_t index = 0; index < value.size(); ++index)
        {
            const auto part = text.substr(index * 8, 8);
            for (const char c : part)
                if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'))) return false;
            const auto conversion = std::from_chars(part.data(), part.data() + 8, value[index], 16);
            if (conversion.ec != std::errc{} || conversion.ptr != part.data() + 8) return false;
        }
        return value != Guid{};
    }

    struct Wrapper
    {
        U::FWeakObjectPtr function{};
        U::FStructProperty* value{};
        U::FFloatProperty* elapsed{};
        U::FFloatProperty* triggered{};
        U::FObjectProperty* source{};
    };

    struct Client
    {
        U::FWeakObjectPtr world{}, controller{}, pawn{}, local{}, instance{}, driver{}, server{};
        U::FWeakObjectPtr held{};
        bool mirrored{};
    };

    struct Scope
    {
        Client client{};
        U::FWeakObjectPtr action{};
        Wrapper started{}, completed{};
        Clock::time_point expires{};
        const Weapon* weapon{};
        Guid guid{};
        std::string text;
        std::uint64_t generation{};
    };

    struct CapturedPress
    {
        Client client{};
        U::FWeakObjectPtr action{};
        Wrapper completed{};
        Clock::time_point expires{};
        std::uint64_t generation{};
    };

    struct Receipt { std::uint64_t sequence{}; Clock::time_point expires{}; };
    Scope scope;
    CapturedPress press;
    std::array<Receipt, receipt_limit> receipts{};
    std::size_t head{}, count{};
    std::uint64_t sequence{};
    std::atomic<std::uint64_t> generation{1};
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool current_client(const Client& client)
    {
        auto* world = client.world.Get();
        auto* controller = client.controller.Get();
        auto* pawn = client.pawn.Get();
        auto* local = client.local.Get();
        auto* instance = client.instance.Get();
        auto* driver = client.driver.Get();
        auto* server = client.server.Get();
        auto* role = field<U::FByteProperty>(pawn, STR("Role"));
        if (!live(world) || !live(pawn) || !role || role->GetPropertyValueInContainer(pawn) != 2 ||
            !false_field(pawn, STR("bActorIsBeingDestroyed")) || !live(controller) ||
            !false_field(controller, STR("bActorIsBeingDestroyed")) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            !live(local) || !live(instance) || !live(driver) || !live(server) ||
            object_field(pawn, STR("Controller")) != controller || object_field(controller, STR("Pawn")) != pawn ||
            object_field(controller, STR("Player")) != local || object_field(local, STR("PlayerController")) != controller ||
            object_field(world, STR("OwningGameInstance")) != instance || object_field(world, STR("NetDriver")) != driver ||
            object_field(driver, STR("World")) != world || object_field(driver, STR("ServerConnection")) != server ||
            object_field(server, STR("Driver")) != driver) return false;
        auto* players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = players ? U::CastField<U::FObjectProperty>(players->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        return locals.Num() == 1 && entry->GetObjectPropertyValue(locals.GetRawPtr(0)) == local;
    }

    bool matching_local_item(const Scope& current)
    {
        auto* pawn = current.client.pawn.Get();
        if (!field<U::FObjectProperty>(pawn, STR("EquipedItem")) || !field<U::FObjectProperty>(pawn, STR("HeldObject")) ||
            object_field(pawn, STR("HeldObject"))) return false;
        auto* held = object_field(pawn, STR("EquipedItem"));
        // Stock held-actor references are not replicated. An authenticated
        // server authorization can cover an otherwise empty local proxy.
        if (!current.client.mirrored) return held == nullptr;
        auto* weapon = find<U::UClass>(current.weapon->path);
        if (!live(held) || held != current.client.held.Get() || !weapon || held->GetClassPrivate() != weapon ||
            object_field(held, STR("Owner")) != pawn || object_field(held, STR("Instigator")) != pawn ||
            static_cast<U::AActor*>(held)->GetWorld() != current.client.world.Get()) return false;
        auto* save = object_field(pawn, STR("Save"));
        if (!live(save) || save->GetClassPrivate() != find<U::UClass>(save_path)) return false;

        auto* selected = field<U::FIntProperty>(save, STR("PlayerEquipIndex"));
        auto* rows = field<U::FArrayProperty>(save, STR("PlayerEquipment"));
        auto* inner = rows ? U::CastField<U::FStructProperty>(rows->GetInner()) : nullptr;
        auto* type = find<U::UScriptStruct>(item_struct);
        auto* klass = field<U::FClassProperty>(type, item_class);
        auto* guid = field<U::FStructProperty>(type, item_guid);
        auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
        if (!selected || !inner || !type || inner->GetStruct().Get() != type || !klass ||
            !guid || guid->GetSize() != 16 || !guid_type || guid->GetStruct().Get() != guid_type) return false;
        U::FScriptArrayHelper items(rows, rows->ContainerPtrToValuePtr<void>(save));
        const auto index = selected->GetPropertyValueInContainer(save);
        if (index < 0 || index >= items.Num() || items.Num() > 4096) return false;
        const auto* row = items.GetRawPtr(index);
        if (klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(row)) != weapon) return false;
        const auto* raw = guid->ContainerPtrToValuePtr<void>(row);
        constexpr std::array words{STR("A"), STR("B"), STR("C"), STR("D")};
        for (std::size_t i = 0; i < words.size(); ++i)
        {
            auto* word = field<U::FIntProperty>(guid_type, words[i]);
            if (!word || static_cast<std::uint32_t>(word->GetPropertyValueInContainer(raw)) != current.guid[i])
                return false;
        }
        return true;
    }

    bool qualified(const Scope& current, Clock::time_point now)
    {
        return current.weapon && current.generation == generation.load(std::memory_order_acquire) &&
            now < current.expires && current_client(current.client) && matching_local_item(current);
    }

    bool can_act(const Client& client)
    {
        auto* pawn = client.pawn.Get();
        return false_field(client.controller.Get(), STR("Loading")) && false_field(pawn, STR("Dead")) &&
            false_field(pawn, STR("IsWeaponBlocked")) && false_field(pawn, STR("UsingItem")) &&
            false_field(pawn, STR("Reloading")) && false_field(pawn, STR("IsChangingAmmo")) &&
            false_field(pawn, STR("Speaking")) && false_field(pawn, STR("HeadUnderWater")) &&
            false_field(pawn, STR("Wep_HeldAside")) && false_field(pawn, STR("OnLadder"));
    }

    bool verifies_source(const Wrapper& wrapper, U::UObject* action, const void* arguments)
    {
        if (!arguments || !action || !wrapper.source ||
            wrapper.source->GetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(arguments)) != action)
            return false;
        const auto elapsed = wrapper.elapsed->GetPropertyValueInContainer(arguments);
        const auto triggered = wrapper.triggered->GetPropertyValueInContainer(arguments);
        return std::isfinite(elapsed) && elapsed >= 0 && std::isfinite(triggered) && triggered >= 0;
    }

    Wrapper bind_wrapper(U::UClass* pawn, U::UObject* action, const U::TCHAR* path, std::uint8_t expected_trigger)
    {
        auto* function = find<U::UFunction>(path);
        auto* value_type = find<U::UScriptStruct>(STR("/Script/EnhancedInput.InputActionValue"));
        if (!function || function->GetOuterPrivate() != pawn || function->GetNumParms() != 4 ||
            function->GetParmsSize() != 0x30 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !value_type) return {};
        auto* binding = find<U::UObject>(
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:EnhancedInputActionDelegateBinding_0"));
        auto* rows = field<U::FArrayProperty>(binding, STR("InputActionDelegateBindings"));
        auto* inner = rows ? U::CastField<U::FStructProperty>(rows->GetInner()) : nullptr;
        auto* type = find<U::UScriptStruct>(STR("/Script/EnhancedInput.BlueprintEnhancedInputActionBinding"));
        auto* name = field<U::FNameProperty>(type, STR("FunctionNameToBind"));
        auto* source = field<U::FObjectProperty>(type, STR("InputAction"));
        auto* trigger = field<U::FEnumProperty>(type, STR("TriggerEvent"));
        auto* trigger_type = find<U::UEnum>(STR("/Script/EnhancedInput.ETriggerEvent"));
        auto* trigger_value = trigger ? trigger->GetUnderlyingProperty() : nullptr;
        if (!live(binding) || binding->GetOuterPrivate() != pawn || !inner || !type || inner->GetStruct().Get() != type ||
            !name || !source || !trigger || !trigger_type || trigger->GetEnum().Get() != trigger_type ||
            !trigger_value || trigger_value->GetSize() != 1) return {};
        U::FScriptArrayHelper bindings(rows, rows->ContainerPtrToValuePtr<void>(binding));
        if (bindings.Num() < 1 || bindings.Num() > 128) return {};
        int matches = 0;
        for (int i = 0; i < bindings.Num(); ++i)
        {
            const auto* row = bindings.GetRawPtr(i);
            if (name->GetPropertyValueInContainer(row) != function->GetFName()) continue;
            if (source->GetObjectPropertyValue(source->ContainerPtrToValuePtr<void>(row)) != action ||
                trigger_value->GetUnsignedIntPropertyValue(trigger->ContainerPtrToValuePtr<void>(row)) != expected_trigger)
                return {};
            ++matches;
        }
        if (matches != 1) return {};
        Wrapper result;
        result.value = input<U::FStructProperty>(function, STR("ActionValue"), 0);
        result.elapsed = input<U::FFloatProperty>(function, STR("ElapsedTime"), 0x20);
        result.triggered = input<U::FFloatProperty>(function, STR("TriggeredTime"), 0x24);
        result.source = input<U::FObjectProperty>(function, STR("SourceAction"), 0x28);
        if (!result.value || result.value->GetSize() != 0x20 || result.value->GetStruct().Get() != value_type ||
            !result.elapsed || !result.triggered || !result.source ||
            result.source->GetPropertyClass().Get() != action->GetClassPrivate()) return {};
        result.function = function;
        return result;
    }

    std::string_view bind(Scope& next, Address requested_pawn, Address requested_controller, Address requested_world)
    {
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* controller_type = find<U::UClass>(controller_path);
        auto* connection_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* action_type = find<U::UClass>(STR("/Script/EnhancedInput.InputAction"));
        auto* action = find<U::UObject>(action_path);
        if (!pawn_type || !controller_type || !connection_type || !local_type ||
            !action_type || !live(action) || action->GetClassPrivate() != action_type)
            return "stock_guest_primary_classes_unavailable";
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != requested_pawn) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == pawn_type) next.client.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.client.pawn.Get();
        if (!pawn) return "stock_guest_primary_pawn_unavailable";
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* local = object_field(controller, STR("Player"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* server = object_field(driver, STR("ServerConnection"));
        if (address(world) != requested_world || address(controller) != requested_controller ||
            !live(controller) || controller->GetClassPrivate() != controller_type || !live(local) ||
            !local->IsA(local_type) || !live(server) || !server->IsA(connection_type))
            return "stock_guest_primary_connection_unavailable";
        next.client.world = world;
        next.client.controller = controller;
        next.client.instance = object_field(world, STR("OwningGameInstance"));
        next.client.local = local;
        next.client.driver = driver;
        next.client.server = server;
        auto* held = object_field(pawn, STR("EquipedItem"));
        next.client.mirrored = held != nullptr;
        next.client.held = held;
        next.action = action;
        if (!current_client(next.client) || !matching_local_item(next)) return "stock_guest_primary_local_equipment_unqualified";
        next.started = bind_wrapper(pawn_type, action, started_path, 2);
        next.completed = bind_wrapper(pawn_type, action, completed_path, 16);
        return next.started.function.Get() && next.completed.function.Get() ?
            std::string_view{} : "stock_guest_primary_wrappers_unavailable";
    }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    void discard_expired(Clock::time_point now)
    {
        while (count && now >= receipts[head].expires)
        {
            head = (head + 1) % receipt_limit;
            --count;
        }
    }

    void intercept(Iteration& iteration, U::UObject* pawn, U::UFunction* function, const void* arguments)
    {
        if (!available() || !pawn || !function || iteration.OriginalFunctionCallPrevented()) return;
        const bool released = pawn == press.client.pawn.Get() && function == press.completed.function.Get();
        const bool started = pawn == scope.client.pawn.Get() && function == scope.started.function.Get();
        if (!released && !started) return;
        const auto now = Clock::now();
        const auto active_generation = generation.load(std::memory_order_acquire);
        if (released && press.generation == active_generation && now < press.expires && current_client(press.client) &&
            verifies_source(press.completed, press.action.Get(), arguments))
        {
            auto* current = object_field(pawn, STR("EquipedItem"));
            const bool same_actor = press.client.mirrored ? current && current == press.client.held.Get() : !current;
            press = {};
            if (same_actor) iteration.PreventOriginalFunctionCall();
            return;
        }
        if (!started || !qualified(scope, now) || !verifies_source(scope.started, scope.action.Get(), arguments)) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        if (press.generation == active_generation && now < press.expires &&
            press.client.pawn.Get() == pawn && press.client.controller.Get() == scope.client.controller.Get() &&
            press.client.world.Get() == scope.client.world.Get()) return;
        press = {scope.client, scope.action, scope.completed, now + release_lease, active_generation};
        if (!can_act(scope.client) || sequence == max_sequence) return;
        discard_expired(now);
        if (count == receipt_limit) return;
        receipts[(head + count) % receipt_limit] = {++sequence, now + receipt_lifetime};
        ++count;
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5))
            return result(lua, false, "native_guest_primary_exact_scope_required");
        const auto pawn = lua.get_integer();
        const auto controller = lua.get_integer();
        const auto world = lua.get_integer();
        const std::string text{lua.get_string()};
        const auto kind = lua.get_string();
        Scope next;
        if (pawn <= 0 || controller <= 0 || world <= 0 || !parse_guid(text, next.guid))
            return result(lua, false, "native_guest_primary_exact_scope_required");
        for (const auto& candidate : weapons)
            if (candidate.kind == kind) next.weapon = &candidate;
        if (!next.weapon) return result(lua, false, "native_guest_primary_weapon_unqualified");
        const auto now = Clock::now();
        if (scope.weapon == next.weapon && scope.guid == next.guid && address(scope.client.pawn.Get()) == pawn &&
            address(scope.client.controller.Get()) == controller && address(scope.client.world.Get()) == world &&
            qualified(scope, now))
        {
            scope.expires = now + scope_lease;
            return result(lua, true, scope.client.mirrored ? "stock_held" : "host_equipped");
        }
        scope = {};
        head = count = 0;
        if (const auto failure = bind(next, pawn, controller, world); !failure.empty()) return result(lua, false, failure);
        next.expires = now + scope_lease;
        next.text = text;
        next.generation = generation.load(std::memory_order_acquire);
        scope = std::move(next);
        return result(lua, true, scope.client.mirrored ? "stock_held" : "host_equipped");
    }

    int take(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto pawn = lua.get_integer();
        const auto now = Clock::now();
        if (pawn != address(scope.client.pawn.Get()) || !qualified(scope, now) || !can_act(scope.client)) return 0;
        discard_expired(now);
        if (!count) return 0;
        lua.set_integer(static_cast<std::int64_t>(receipts[head].sequence));
        lua.set_integer(pawn);
        lua.set_string(scope.text);
        lua.set_string(scope.weapon->kind);
        head = (head + 1) % receipt_limit;
        --count;
        return 4;
    }

    int clear(const Lua&)
    {
        if (U::IsInGameThreadRaw())
        {
            scope = {};
            head = count = 0;
            // A consumed Started may still need its matching physical release.
            // This one old, exact stroke cannot enqueue another shot.
        }
        return 0;
    }

    struct StructValue
    {
        U::FStructProperty* property;
        void* buffer;
        StructValue(U::FStructProperty* property, void* buffer) : property(property), buffer(buffer)
        {
            property->InitializeValue_InContainer(buffer);
        }
        ~StructValue() { property->DestroyValue_InContainer(buffer); }
        StructValue(const StructValue&) = delete;
        StructValue& operator=(const StructValue&) = delete;
    };

    struct ValueFactory
    {
        U::UObject* library{};
        U::UFunction* function{};
        U::FDoubleProperty* x{};
        U::FStructProperty* output{};

        bool bind(U::UObject* action, U::UScriptStruct* value_type)
        {
            library = find<U::UObject>(STR("/Script/EnhancedInput.Default__EnhancedInputLibrary"));
            function = find<U::UFunction>(STR("/Script/EnhancedInput.EnhancedInputLibrary:MakeInputActionValueOfType"));
            if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
                function->GetOuterPrivate() != library->GetClassPrivate() || !function->HasAnyFunctionFlags(U::FUNC_Native) ||
                function->HasAnyFunctionFlags(U::FUNC_Net) || function->GetNumParms() != 5 ||
                function->GetParmsSize() != 0x40) return false;
            x = input<U::FDoubleProperty>(function, STR("X"), 0);
            output = field<U::FStructProperty>(function, STR("ReturnValue"));
            auto* type = find<U::UEnum>(STR("/Script/EnhancedInput.EInputActionValueType"));
            auto* value = input<U::FEnumProperty>(function, STR("ValueType"), 0x18);
            auto* action_value = field<U::FEnumProperty>(action, STR("ValueType"));
            auto* number = action_value ? action_value->GetUnderlyingProperty() : nullptr;
            return x && input<U::FDoubleProperty>(function, STR("Y"), 8) &&
                input<U::FDoubleProperty>(function, STR("Z"), 0x10) && type &&
                value && value->GetEnum().Get() == type && value->GetSize() == 1 &&
                action_value && action_value->GetEnum().Get() == type && number && number->GetSize() == 1 &&
                number->GetUnsignedIntPropertyValue(action_value->ContainerPtrToValuePtr<void>(action)) == 0 &&
                output && output == function->GetReturnProperty() && output->GetOffset_Internal() == 0x20 &&
                output->GetSize() == 0x20 && output->GetStruct().Get() == value_type;
        }

        void dispatch(U::UObject* pawn, U::UObject* action, const Wrapper& wrapper, bool down) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x40> packed{};
            alignas(std::max_align_t) std::array<std::byte, 0x30> input{};
            StructValue generated(output, packed.data());
            StructValue value(wrapper.value, input.data());
            x->SetPropertyValueInContainer(packed.data(), down ? 1.0 : 0.0);
            library->ProcessEvent(function, packed.data());
            wrapper.value->CopyCompleteValue(wrapper.value->ContainerPtrToValuePtr<void>(input.data()),
                output->ContainerPtrToValuePtr<void>(packed.data()));
            wrapper.elapsed->SetPropertyValueInContainer(input.data(), down ? 0.0f : 0.05f);
            wrapper.triggered->SetPropertyValueInContainer(input.data(), down ? 0.0f : 0.05f);
            wrapper.source->SetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(input.data()), action);
            pawn->ProcessEvent(static_cast<U::UFunction*>(wrapper.function.Get()), input.data());
        }
    };

    int probe(const Lua& lua)
    {
        if (!available() || !fixtures() || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return result(lua, false, "native_guest_primary_fixture_unavailable");
        const auto now = Clock::now();
        auto* pawn = scope.client.pawn.Get();
        auto* action = scope.action.Get();
        if (lua.get_integer() != address(pawn) || !qualified(scope, now) || !can_act(scope.client))
            return result(lua, false, "native_guest_primary_fixture_scope_unavailable");
        discard_expired(now);
        if (count || (press.client.pawn.Get() && now < press.expires) || sequence == max_sequence)
            return result(lua, false, "native_guest_primary_fixture_input_busy");
        ValueFactory factory;
        if (!factory.bind(action, scope.started.value->GetStruct().Get()))
            return result(lua, false, "native_guest_primary_fixture_value_unavailable");
        const auto before = sequence;
        factory.dispatch(pawn, action, scope.started, true);
        if (press.client.pawn.Get() != pawn || !qualified(scope, Clock::now()))
            return result(lua, false, "native_guest_primary_fixture_start_unverified");
        factory.dispatch(pawn, action, scope.completed, false);
        if (press.client.pawn.Get() || sequence != before + 1 || count != 1)
            return result(lua, false, "native_guest_primary_fixture_release_unverified");
        result(lua, true, "stock_pressed");
        lua.set_integer(static_cast<std::int64_t>(sequence));
        lua.set_integer(2);
        return 4;
    }
}

namespace silverfish::native_guest_primary_input
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            intercept, {false, false, owner, STR("Route stock guest primary input")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, U::UObject* pawn, U::FFrame& frame, void*) {
                if (frame.Object() == pawn) intercept(iteration, pawn, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Route Blueprint guest primary input")});
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

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeGuestPrimaryScope", set_scope);
        lua.register_function("SilverfishTakeNativeGuestPrimaryIntent", take);
        lua.register_function("SilverfishClearNativeGuestPrimaryScope", clear);
        if (fixtures()) lua.register_function("SilverfishProbeNativeGuestPrimaryInput", probe);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (!U::IsInGameThreadRaw()) return;
        scope = {};
        press = {};
        head = count = 0;
    }
}
