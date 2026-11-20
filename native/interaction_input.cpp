#include "interaction_input.hpp"
#include "mosby_station_input.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
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
    using Address = std::intptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto environment = "SILVERFISH_COOP_NATIVE_INTERACT_INPUT";
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto action_path = STR("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact");
    constexpr auto started_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19");
    constexpr auto completed_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21");
    constexpr auto binding_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:EnhancedInputActionDelegateBinding_0");
    constexpr auto wire_board_class = STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C");
    constexpr auto wire_interaction_path = STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:SendInteractionToActor");
    constexpr auto wire_script_path = STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:ExecuteUbergraph_BountyBoard_BP");
    constexpr auto subway_world = STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P");
    constexpr auto subway_valve = STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P:PersistentLevel.ValveSwitch_BP_C_1");
    constexpr auto valve_class = STR("/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C");
    constexpr auto valve_interaction = STR("/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C:SendInteractionToActor");
    constexpr int wire_menu_entry = 0x15b;
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto receipt_lifetime = std::chrono::milliseconds(1500);
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;
    constexpr std::size_t pending_limit = 8;

    struct BoardProfile
    {
        const Unreal::TCHAR* world;
        const Unreal::TCHAR* actor;
        std::uint8_t turn_in_id;
        bool displays_global_contracts;
    };

    constexpr std::array board_profiles{
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.BountyBoard_BP_C_3"), 5, false,
        },
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2"), 4, false,
        },
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P:PersistentLevel.BountyBoard_BP_C_2"), 10, true,
        },
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.BountyBoard_BP_C_0"), 1, true,
        },
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P:PersistentLevel.BountyBoard_BP_C_1"), 2, false,
        },
        BoardProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.BountyBoard_BP_C_0"), 3, true,
        },
    };

    struct DialogueProfile
    {
        const Unreal::TCHAR* world;
        const Unreal::TCHAR* actor;
        const Unreal::TCHAR* actor_class;
        const Unreal::TCHAR* component;
        const Unreal::TCHAR* component_class;
        const Unreal::TCHAR* callback;
    };

    constexpr std::array dialogue_profiles{
        DialogueProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.NPC_Castaway_C_1"),
            STR("/Game/Classes/NPCs/NPC_Characters/NPC_Castaway.NPC_Castaway_C"),
            STR("Sphere_Interact"),
            STR("/Script/Engine.SphereComponent"),
            STR("/Game/Classes/NPCs/NPC_Characters/NPC_Castaway.NPC_Castaway_C:SendInteractionToActor"),
        },
        DialogueProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.Generic_Interact_BP_C_1"),
            STR("/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C"),
            STR("Box"),
            STR("/Script/Engine.BoxComponent"),
            STR("/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C:SendInteractionToActor"),
        },
        DialogueProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P:PersistentLevel.Generic_Interact_BP_C_1"),
            STR("/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C"),
            STR("Box"),
            STR("/Script/Engine.BoxComponent"),
            STR("/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C:SendInteractionToActor"),
        },
        DialogueProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.NPC_Mama_C_1"),
            STR("/Game/Classes/NPCs/NPC_Characters/NPC_Mama.NPC_Mama_C"),
            STR("Sphere_Interact"),
            STR("/Script/Engine.SphereComponent"),
            STR("/Game/Classes/NPCs/NPC_Characters/NPC_Mama.NPC_Mama_C:SendInteractionToActor"),
        },
    };

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
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

    bool fixture_enabled()
    {
        return env_flag("SILVERFISH_COOP_HEADLESS") &&
               (env_flag("SILVERFISH_COOP_NATIVE_FIXTURES") || env_flag("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"));
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    struct Wrapper
    {
        Unreal::FWeakObjectPtr function{};
        Unreal::FStructProperty* value{};
        Unreal::FFloatProperty* elapsed{};
        Unreal::FFloatProperty* triggered{};
        Unreal::FObjectProperty* source{};
    };

    struct Scope
    {
        std::uint64_t generation{};
        bool host{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr pawn{}, controller{}, world{}, instance{}, player{}, driver{}, server{}, action{};
        Wrapper started{}, completed{};
    };

    struct Receipt
    {
        std::uint64_t sequence{};
        Clock::time_point expires{};
    };

    struct ScopedInteraction
    {
        Unreal::FWeakObjectPtr actor{}, component{}, pawn{};
        bool accepting{};
        Clock::time_point expires{};
        Receipt pending{};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    Scope scope;
    ScopedInteraction wire_board, dialogue_input, valve_input;
    std::array<Receipt, pending_limit> receipts{};
    std::size_t first{}, count{}, event_depth{};
    std::uint64_t sequence{}, consumed_dispatches{};
    std::uint64_t wire_sequence{};
    std::uint64_t dialogue_sequence{}, valve_sequence{};
    bool dispatching_valve = false;
    bool event_source_matches{}, event_consumed{};

    bool live_actor(Unreal::UObject* actor, Unreal::UObject* world, std::uint8_t role)
    {
        auto* role_field = field<Unreal::FByteProperty>(actor, STR("Role"));
        auto* destroyed = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role_field && role_field->GetPropertyValueInContainer(actor) == role &&
            destroyed && !destroyed->GetPropertyValueInContainer(actor) &&
            static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    bool live_scope(const Scope& current)
    {
        auto* pawn = current.pawn.Get();
        auto* controller = current.controller.Get();
        auto* world = current.world.Get();
        auto* instance = current.instance.Get();
        auto* player = current.player.Get();
        auto* driver = current.driver.Get();
        const std::uint8_t role = current.host ? 3 : 2;
        if (current.generation != generation.load(std::memory_order_acquire) || !live(world) ||
            !live_actor(pawn, world, role) || !live_actor(controller, world, role) ||
            !live(instance) || !live(player) || !live(driver) || !live(current.action.Get()) ||
            !current.started.function.Get() || object_field(pawn, STR("Controller")) != controller ||
            object_field(controller, STR("Pawn")) != pawn || object_field(controller, STR("Player")) != player ||
            object_field(player, STR("PlayerController")) != controller ||
            object_field(world, STR("OwningGameInstance")) != instance ||
            object_field(world, STR("NetDriver")) != driver || object_field(driver, STR("World")) != world)
            return false;

        auto* locals = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = locals ? Unreal::CastField<Unreal::FObjectProperty>(locals->GetInner()) : nullptr;
        auto* connection = field<Unreal::FObjectProperty>(driver, STR("ServerConnection"));
        if (!entry || !connection) return false;
        Unreal::FScriptArrayHelper players(locals, locals->ContainerPtrToValuePtr<void>(instance));
        if (players.Num() != 1 || entry->GetObjectPropertyValue(players.GetRawPtr(0)) != player) return false;
        auto* server = connection->GetObjectPropertyValue(connection->ContainerPtrToValuePtr<void>(driver));
        return current.host ? server == nullptr : live(server) && server == current.server.Get() &&
            object_field(server, STR("Driver")) == driver;
    }

    bool standing(Unreal::UObject* pawn)
    {
        auto* dead = field<Unreal::FBoolProperty>(pawn, STR("Dead"));
        return dead && !dead->GetPropertyValueInContainer(pawn);
    }

    bool bound_event(Unreal::UClass* pawn_type, Unreal::UFunction* function, Unreal::UObject* action, int desired)
    {
        auto* binding = find<Unreal::UObject>(binding_path);
        auto* type = find<Unreal::UClass>(STR("/Script/EnhancedInput.EnhancedInputActionDelegateBinding"));
        auto* row_type = find<Unreal::UScriptStruct>(STR("/Script/EnhancedInput.BlueprintEnhancedInputActionBinding"));
        auto* enum_type = find<Unreal::UEnum>(STR("/Script/EnhancedInput.ETriggerEvent"));
        if (!live(binding) || !type || binding->GetClassPrivate() != type || binding->GetOuterPrivate() != pawn_type ||
            !row_type || !enum_type) return false;
        auto* array = field<Unreal::FArrayProperty>(binding, STR("InputActionDelegateBindings"));
        auto* inner = array ? Unreal::CastField<Unreal::FStructProperty>(array->GetInner()) : nullptr;
        auto* source = field<Unreal::FObjectProperty>(row_type, STR("InputAction"));
        auto* trigger = field<Unreal::FEnumProperty>(row_type, STR("TriggerEvent"));
        auto* name = field<Unreal::FNameProperty>(row_type, STR("FunctionNameToBind"));
        auto* number = trigger ? trigger->GetUnderlyingProperty() : nullptr;
        if (!inner || inner->GetStruct().Get() != row_type || !source || source->GetOffset_Internal() != 0 ||
            !trigger || trigger->GetEnum().Get() != enum_type || trigger->GetOffset_Internal() != 8 ||
            !number || number->GetSize() != 1 || !name || name->GetOffset_Internal() != 0xc)
            return false;
        Unreal::FScriptArrayHelper rows(array, array->ContainerPtrToValuePtr<void>(binding));
        if (rows.Num() <= 0 || rows.Num() > 128) return false;
        int matches{};
        for (int index = 0; index < rows.Num(); ++index)
        {
            const auto* row = rows.GetRawPtr(index);
            if (name->GetPropertyValueInContainer(row) != function->GetFName()) continue;
            if (source->GetObjectPropertyValue(source->ContainerPtrToValuePtr<void>(row)) != action ||
                number->GetUnsignedIntPropertyValue(trigger->ContainerPtrToValuePtr<void>(row)) != desired)
                return false;
            ++matches;
        }
        return matches == 1;
    }

    Wrapper bind_wrapper(Unreal::UClass* pawn_type, Unreal::UObject* action, const Unreal::TCHAR* path, int trigger)
    {
        auto* function = find<Unreal::UFunction>(path);
        auto* value_type = find<Unreal::UScriptStruct>(STR("/Script/EnhancedInput.InputActionValue"));
        if (!function || function->GetOuterPrivate() != pawn_type || function->GetNumParms() != 4 ||
            function->GetParmsSize() != 0x30 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !value_type || !bound_event(pawn_type, function, action, trigger)) return {};
        Wrapper wrapper;
        wrapper.value = input<Unreal::FStructProperty>(function, STR("ActionValue"), 0);
        wrapper.elapsed = input<Unreal::FFloatProperty>(function, STR("ElapsedTime"), 0x20);
        wrapper.triggered = input<Unreal::FFloatProperty>(function, STR("TriggeredTime"), 0x24);
        wrapper.source = input<Unreal::FObjectProperty>(function, STR("SourceAction"), 0x28);
        if (!wrapper.value || wrapper.value->GetSize() != 0x20 || wrapper.value->GetStruct().Get() != value_type ||
            !wrapper.elapsed || !wrapper.triggered || !wrapper.source ||
            wrapper.source->GetPropertyClass().Get() != action->GetClassPrivate()) return {};
        wrapper.function = function;
        return wrapper;
    }

    std::string_view bind_scope(Scope& next, Address requested, bool host)
    {
        auto* pawn_type = find<Unreal::UClass>(pawn_path);
        auto* controller_type = find<Unreal::UClass>(controller_path);
        auto* instance_type = find<Unreal::UClass>(instance_path);
        auto* player_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* action_type = find<Unreal::UClass>(STR("/Script/EnhancedInput.InputAction"));
        auto* connection_type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        auto* action = find<Unreal::UObject>(action_path);
        if (!pawn_type || !controller_type || !instance_type || !player_type || !connection_type ||
            !action_type || !live(action) || action->GetClassPrivate() != action_type)
            return "stock_interact_class_unavailable";
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != requested) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == pawn_type) next.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.pawn.Get();
        if (!pawn) return "stock_interact_exact_pawn_unavailable";
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* server = object_field(driver, STR("ServerConnection"));
        if (!live(world) || !live(controller) || controller->GetClassPrivate() != controller_type ||
            !live(player) || !player->IsA(player_type) || !live(instance) || instance->GetClassPrivate() != instance_type ||
            !live(driver) || (!host && (!live(server) || !server->IsA(connection_type))))
            return "stock_interact_local_owner_unavailable";
        next.world = world;
        next.controller = controller;
        next.instance = instance;
        next.player = player;
        next.driver = driver;
        next.server = server;
        next.action = action;
        next.host = host;
        next.generation = generation.load(std::memory_order_acquire);
        next.started = bind_wrapper(pawn_type, action, started_path, 2);
        if (!next.started.function.Get()) return "stock_interact_started_schema_unavailable";
        if (fixture_enabled()) next.completed = bind_wrapper(pawn_type, action, completed_path, 16);
        return live_scope(next) ? std::string_view{} : "stock_interact_net_role_unavailable";
    }

    void discard_expired(Clock::time_point now)
    {
        while (count && receipts[first].expires <= now)
        {
            first = (first + 1) % pending_limit;
            --count;
        }
    }

    bool available()
    {
        return installed.load(std::memory_order_acquire) && Unreal::IsInGameThreadRaw();
    }

    const BoardProfile* wire_board_profile(Unreal::UObject* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        for (const auto& profile : board_profiles)
            if (path == profile.world) return &profile;
        return nullptr;
    }

    const DialogueProfile* dialogue_profile(Unreal::UObject* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        for (const auto& profile : dialogue_profiles)
            if (path == profile.world) return &profile;
        return nullptr;
    }

    std::string_view qualify_wire_board(Unreal::UObject* world, const BoardProfile& profile, Unreal::UObject* actor, Unreal::UObject* box)
    {
        if (!live(actor) || actor->GetClassPrivate() != find<Unreal::UClass>(wire_board_class) ||
            actor->GetPathName() != profile.actor) return "native_wire_board_actor";
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        if (!destroying || destroying->GetPropertyValueInContainer(actor) ||
            static_cast<Unreal::AActor*>(actor)->GetWorld() != world ||
            (scope.host && !live_actor(actor, world, 3))) return "native_wire_board_actor_world";
        if (!live(box) || box->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.BoxComponent")) ||
            box->GetOuterPrivate() != actor || object_field(actor, STR("Box")) != box) return "native_wire_board_box";
        auto* id = field<Unreal::FByteProperty>(actor, STR("TurnIn_ID"));
        const auto equals = [&](const Unreal::TCHAR* name, bool expected) {
            auto* value = field<Unreal::FBoolProperty>(actor, name);
            return value && value->GetPropertyValueInContainer(actor) == expected;
        };
        return id && id->GetPropertyValueInContainer(actor) == profile.turn_in_id &&
            equals(STR("BountyBoard_RedeemableHere"), true) && equals(STR("AcceptAllGlobalContracts"), true) &&
            equals(STR("Display_GlobalContracts"), profile.displays_global_contracts) && equals(STR("ShowAllContracts"), false)
                ? std::string_view{} : "native_wire_board_configuration";
    }

    bool interaction_in_reach(Unreal::UObject* pawn, Unreal::UObject* actor, Unreal::UObject* component)
    {
        auto* range = field<Unreal::FDoubleProperty>(pawn, STR("InteractionRange"));
        auto* getter = find<Unreal::UFunction>(STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"));
        auto* output = field<Unreal::FStructProperty>(getter, STR("ReturnValue"));
        auto* vector = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        std::array<double, 3> point{};
        if (!range || !output || !vector || output != getter->GetReturnProperty() || getter->GetNumParms() != 1 ||
            output->GetStruct().Get() != vector || output->GetOffset_Internal() != 0 ||
            getter->GetParmsSize() != sizeof(point) || !getter->HasAnyFunctionFlags(Unreal::FUNC_Native)) return false;
        const double reach = range->GetPropertyValueInContainer(pawn);
        if (!std::isfinite(reach) || reach <= 0 || reach > 1000) return false;
        component->ProcessEvent(getter, point.data());
        auto player = static_cast<Unreal::AActor*>(pawn)->K2_GetActorLocation();
        auto board = static_cast<Unreal::AActor*>(actor)->K2_GetActorLocation();
        const auto near = [&](double x, double y, double z) {
            const double dx = player.GetX() - x, dy = player.GetY() - y, dz = player.GetZ() - z;
            const double square = dx * dx + dy * dy + dz * dz;
            return std::isfinite(square) && square <= reach * reach;
        };
        return near(point[0], point[1], point[2]) && near(board.GetX(), board.GetY(), board.GetZ());
    }

    void intercept_wire_board(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        if (!actor || !function || !parameters || actor != wire_board.actor.Get() ||
            iteration.OriginalFunctionCallPrevented() || !live_scope(scope) ||
            Clock::now() >= wire_board.expires) return;
        static const Unreal::FName interaction{STR("SendInteractionToActor")};
        static const Unreal::FName script{STR("ExecuteUbergraph_BountyBoard_BP")};
        const auto name = function->GetFName();
        if (name != interaction && name != script) return;
        auto* world = scope.world.Get();
        auto* profile = wire_board_profile(world);
        auto* box = wire_board.component.Get();
        if (!profile || !qualify_wire_board(world, *profile, actor, box).empty() || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        if (name == script)
        {
            auto* entry = input<Unreal::FIntProperty>(function, STR("EntryPoint"), 0);
            if (function == find<Unreal::UFunction>(wire_script_path) && entry && function->GetNumParms() == 1 &&
                function->GetParmsSize() == sizeof(std::int32_t) && entry->GetPropertyValueInContainer(parameters) == wire_menu_entry)
                iteration.PreventOriginalFunctionCall();
            return;
        }
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* component = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        if (function != find<Unreal::UFunction>(wire_interaction_path) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 2 * sizeof(void*) || !player || !component ||
            player->GetPropertyClass().Get() != find<Unreal::UClass>(pawn_path) ||
            component->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"))) return;
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        auto* hit = component->GetObjectPropertyValue(component->ContainerPtrToValuePtr<void>(parameters));
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented() || !pawn || pawn != wire_board.pawn.Get() || hit != box ||
            object_field(pawn, STR("InteractionTarget_Component")) != box ||
            !silverfish::native_interaction_input::consume_current_press(pawn) || !wire_board.accepting ||
            wire_sequence == max_sequence || !interaction_in_reach(pawn, actor, box)) return;
        wire_board.pending = {++wire_sequence, Clock::now() + receipt_lifetime};
    }

    bool exact_dialogue_actor(const DialogueProfile& profile, Unreal::UObject* actor, Unreal::UObject* component)
    {
        auto* world = scope.world.Get();
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return !scope.host && live(world) && world->GetPathName() == profile.world
            && live(actor) && actor->GetClassPrivate() == find<Unreal::UClass>(profile.actor_class)
            && actor->GetPathName() == profile.actor && destroying && !destroying->GetPropertyValueInContainer(actor)
            && static_cast<Unreal::AActor*>(actor)->GetWorld() == world && live(component)
            && component->GetClassPrivate() == find<Unreal::UClass>(profile.component_class)
            && component->GetOuterPrivate() == actor && component == object_field(actor, profile.component);
    }

    void intercept_dialogue(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        if (!actor || actor != dialogue_input.actor.Get() || !function || !parameters ||
            call.OriginalFunctionCallPrevented() || Clock::now() >= dialogue_input.expires ||
            !live_scope(scope)) return;
        auto* target = dialogue_input.component.Get();
        const auto* profile = dialogue_profile(scope.world.Get());
        if (!profile || function != find<Unreal::UFunction>(profile->callback)
            || !exact_dialogue_actor(*profile, actor, target) || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* component = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        if (!player || !component || player->GetPropertyClass().Get() != find<Unreal::UClass>(pawn_path) ||
            component->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"))) return;
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        if (!pawn || pawn != dialogue_input.pawn.Get() || pawn != scope.pawn.Get()) return;
        call.PreventOriginalFunctionCall();
        auto* hit = component->GetObjectPropertyValue(component->ContainerPtrToValuePtr<void>(parameters));
        if (!call.OriginalFunctionCallPrevented() || hit != target || !dialogue_input.accepting ||
            object_field(pawn, STR("InteractionTarget_Component")) != target || dialogue_sequence == max_sequence ||
            !interaction_in_reach(pawn, actor, target) ||
            !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        dialogue_input.pending = {++dialogue_sequence, Clock::now() + receipt_lifetime};
    }

    bool exact_valve(Unreal::UObject* actor)
    {
        auto* world = scope.world.Get();
        auto* dying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        auto* one_use = field<Unreal::FBoolProperty>(actor, STR("Lock_AfterUse"));
        return live(world) && world->GetPathName() == subway_world && live(actor) &&
            actor->GetClassPrivate() == find<Unreal::UClass>(valve_class) && actor->GetPathName() == subway_valve &&
            dying && !dying->GetPropertyValueInContainer(actor) && one_use && one_use->GetPropertyValueInContainer(actor) &&
            static_cast<Unreal::AActor*>(actor)->GetWorld() == world &&
            (!scope.host || live_actor(actor, world, 3));
    }

    bool exact_valve_box(Unreal::UObject* actor, Unreal::UObject* box)
    {
        return live(box) && box == object_field(actor, STR("Box")) && box->GetOuterPrivate() == actor &&
            box->GetClassPrivate() == find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"));
    }

    bool valve_signature(Unreal::UObject* actor, Unreal::UFunction* function)
    {
        const auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        const auto* component = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        return function && function == find<Unreal::UFunction>(valve_interaction) && function->GetOuterPrivate() == actor->GetClassPrivate() &&
            function->GetNumParms() == 2 && function->GetParmsSize() == 2 * sizeof(void*) && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) && player && component &&
            player->GetPropertyClass().Get() == find<Unreal::UClass>(pawn_path) &&
            component->GetPropertyClass().Get() == find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
    }

    void intercept_valve(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        static const Unreal::FName interact{STR("SendInteractionToActor")};
        if (dispatching_valve || !function || function->GetFName() != interact || !parameters ||
            call.OriginalFunctionCallPrevented() || !exact_valve(actor) || !valve_signature(actor, function)) return;
        call.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!call.OriginalFunctionCallPrevented() || now >= scope.expires || !live_scope(scope)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* component = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        auto* box = component->GetObjectPropertyValue(component->ContainerPtrToValuePtr<void>(parameters));
        if (!pawn || pawn != scope.pawn.Get() || !exact_valve_box(actor, box) ||
            object_field(pawn, STR("InteractionTarget_Component")) != box || valve_sequence == max_sequence ||
            !interaction_in_reach(pawn, actor, box) || !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        valve_input.pawn = pawn;
        valve_input.actor = actor;
        valve_input.component = box;
        valve_input.accepting = true;
        valve_input.expires = now + receipt_lifetime;
        valve_input.pending = {++valve_sequence, valve_input.expires};
    }

    void route_interaction(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        intercept_wire_board(iteration, actor, function, parameters);
        intercept_dialogue(iteration, actor, function, parameters);
        intercept_valve(iteration, actor, function, parameters);
        silverfish::mosby_station_input::route(iteration, actor, function, parameters);
    }

    bool exact_dispatch(Unreal::UObject* pawn, Unreal::UFunction* function)
    {
        return pawn && function && pawn == scope.pawn.Get() && function == scope.started.function.Get();
    }

    void record(Iteration& iteration, Unreal::UObject* pawn, const void* parameters)
    {
        const auto now = Clock::now();
        if (!parameters || iteration.OriginalFunctionCallPrevented() || now >= scope.expires ||
            !live_scope(scope) || !standing(pawn) || sequence == max_sequence ||
            scope.started.source->GetObjectPropertyValue(
                scope.started.source->ContainerPtrToValuePtr<void>(parameters)) != scope.action.Get()) return;
        const auto elapsed = scope.started.elapsed->GetPropertyValueInContainer(parameters);
        const auto triggered = scope.started.triggered->GetPropertyValueInContainer(parameters);
        if (!std::isfinite(elapsed) || elapsed < 0 || !std::isfinite(triggered) || triggered < 0) return;
        discard_expired(now);
        if (count == pending_limit) return;
        receipts[(first + count) % pending_limit] = {++sequence, now + receipt_lifetime};
        ++count;
    }

    void event_pre(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        if (!available()) return;
        if (!exact_dispatch(pawn, function))
        {
            route_interaction(iteration, pawn, function, parameters);
            return;
        }
        if (event_depth++ == 0)
        {
            event_consumed = false;
            event_source_matches = parameters && scope.started.source->GetObjectPropertyValue(
                scope.started.source->ContainerPtrToValuePtr<void>(parameters)) == scope.action.Get();
        }
    }

    void event_post(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        if (!available() || !exact_dispatch(pawn, function) || !event_depth) return;
        if (--event_depth != 0) return;
        if (!event_consumed) record(iteration, pawn, parameters);
        event_source_matches = event_consumed = false;
    }

    void script_pre(Iteration& iteration, Unreal::UObject* pawn, Unreal::FFrame& frame, void*)
    {
        // ProcessEvent owns its whole synchronous dispatch, including the VM.
        // Script-to-script invocations can reach the wrapper without ProcessEvent.
        if (!available() || frame.Object() != pawn) return;
        if (exact_dispatch(pawn, frame.Node()))
        {
            if (!event_depth) record(iteration, pawn, frame.Locals());
        }
        else route_interaction(iteration, pawn, frame.Node(), frame.Locals());
    }

    int report(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2))
            return report(lua, false, "native_interact_scope_unavailable");
        const auto pawn = lua.get_integer();
        const auto host = lua.get_bool();
        if (pawn <= 0) return report(lua, false, "native_interact_exact_pawn_required");
        const auto now = Clock::now();
        if (address(scope.pawn.Get()) == pawn && scope.host == host && live_scope(scope))
        {
            if (now >= scope.expires) first = count = 0;
            scope.expires = now + lease;
            return report(lua, true, "ready");
        }
        silverfish::native_interaction_input::clear_scope();
        Scope next;
        if (const auto error = bind_scope(next, pawn, host); !error.empty()) return report(lua, false, error);
        next.expires = now + lease;
        scope = next;
        return report(lua, true, "ready");
    }

    int take_press(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto target = lua.get_integer();
        const auto now = Clock::now();
        auto* pawn = scope.pawn.Get();
        if (!pawn || address(pawn) != target || now >= scope.expires || !live_scope(scope) || !standing(pawn)) return 0;
        discard_expired(now);
        if (!count) return 0;
        lua.set_integer(static_cast<std::int64_t>(receipts[first].sequence));
        lua.set_integer(target);
        first = (first + 1) % pending_limit;
        --count;
        return 2;
    }

    int lua_clear(const Lua&)
    {
        silverfish::native_interaction_input::clear_scope();
        return 0;
    }

    void bind_interaction(ScopedInteraction& target, Unreal::UObject* actor, Unreal::UObject* component,
                          Unreal::UObject* pawn, bool accepting, Clock::time_point now)
    {
        if (actor != target.actor.Get() || component != target.component.Get() || pawn != target.pawn.Get() ||
            !accepting || target.expires <= now) target.pending = {};
        target.actor = actor;
        target.component = component;
        target.pawn = pawn;
        target.accepting = accepting;
        target.expires = now + lease;
    }

    int set_wire_board(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_bool(2) || !lua.is_bool(3))
            return report(lua, false, "native_wire_board_arguments");
        const auto requested = lua.get_integer();
        const bool host = lua.get_bool();
        const bool accepting = lua.get_bool();
        auto* pawn = scope.pawn.Get();
        const auto now = Clock::now();
        if (!pawn || address(pawn) != requested || host != scope.host || now >= scope.expires || !live_scope(scope))
            return report(lua, false, "native_wire_board_local_input");
        auto* world = scope.world.Get();
        auto* profile = wire_board_profile(world);
        if (!profile) return report(lua, false, "native_wire_board_world");
        auto* actor = find<Unreal::AActor>(profile->actor);
        auto* box = object_field(actor, STR("Box"));
        if (const auto reason = qualify_wire_board(world, *profile, actor, box); !reason.empty()) return report(lua, false, reason);
        bind_interaction(wire_board, actor, box, pawn, accepting, now);
        return report(lua, true, "ready");
    }

    int take_interaction_press(const Lua& lua, ScopedInteraction& target)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        auto* pawn = target.pawn.Get();
        const auto now = Clock::now();
        if (!pawn || address(pawn) != requested || pawn != scope.pawn.Get() || !target.accepting ||
            now >= target.expires || !target.pending.sequence || now >= target.pending.expires ||
            !live_scope(scope) || !standing(pawn)) return 0;
        lua.set_integer(static_cast<std::int64_t>(target.pending.sequence));
        target.pending = {};
        return 1;
    }

    int take_wire_board_press(const Lua& lua) { return take_interaction_press(lua, wire_board); }

    int clear_wire_board(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw()) wire_board = {};
        return 0;
    }

    int set_dialogue_scope(const Lua& lua)
    {
        const auto respond = [&](bool value) { lua.set_bool(value); return 1; };
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2))
            return respond(false);
        const auto requested = lua.get_integer();
        const auto accepting = lua.get_bool();
        auto* pawn = scope.pawn.Get();
        const auto now = Clock::now();
        if (scope.host || !pawn || address(pawn) != requested || now >= scope.expires || !live_scope(scope))
            return respond(false);
        const auto* profile = dialogue_profile(scope.world.Get());
        if (!profile) return respond(false);
        auto* actor = find<Unreal::AActor>(profile->actor);
        auto* component = object_field(actor, profile->component);
        if (!exact_dialogue_actor(*profile, actor, component)) return respond(false);
        bind_interaction(dialogue_input, actor, component, pawn, accepting, now);
        return respond(true);
    }

    int take_dialogue_press(const Lua& lua) { return take_interaction_press(lua, dialogue_input); }
    int take_valve_press(const Lua& lua) { return take_interaction_press(lua, valve_input); }

    int dispatch_valve(const Lua& lua)
    {
        const auto respond = [&](bool value) { lua.set_bool(value); return 1; };
        if (!available() || !scope.host || dispatching_valve || !live_scope(scope) || Clock::now() >= scope.expires ||
            lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3)) return respond(false);
        const auto pawn_address = lua.get_integer();
        const auto valve_address = lua.get_integer();
        const auto box_address = lua.get_integer();
        auto* actor = find<Unreal::AActor>(subway_valve);
        auto* box = object_field(actor, STR("Box"));
        auto* callback = find<Unreal::UFunction>(valve_interaction);
        if (address(actor) != valve_address || address(box) != box_address || !exact_valve(actor) ||
            !exact_valve_box(actor, box) || !valve_signature(actor, callback)) return respond(false);
        auto* pawn = scope.pawn.Get();
        if (address(pawn) != pawn_address)
        {
            pawn = nullptr;
            auto* player_type = find<Unreal::UClass>(pawn_path);
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
                if (address(object) != pawn_address) return RC::LoopAction::Continue;
                if (player_type && live(object) && object->GetClassPrivate() == player_type) pawn = object;
                return RC::LoopAction::Break;
            });
            auto* controller = object_field(pawn, STR("Controller"));
            auto* net_player = object_field(controller, STR("Player"));
            auto* net_connection = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
            auto* detached = object_field(pawn, STR("Save"));
            if (!live_actor(controller, scope.world.Get(), 3) || controller->GetClassPrivate() != find<Unreal::UClass>(controller_path) ||
                object_field(controller, STR("Pawn")) != pawn || !live(net_player) || !net_connection || !net_player->IsA(net_connection) ||
                object_field(net_player, STR("Driver")) != scope.driver.Get() || object_field(net_player, STR("PlayerController")) != controller ||
                !live(detached) || detached == object_field(scope.instance.Get(), STR("SaveSlot_Ref"))) return respond(false);
        }
        if (!live_actor(pawn, scope.world.Get(), 3) || !standing(pawn) || !interaction_in_reach(pawn, actor, box))
            return respond(false);
        struct Invocation
        {
            Invocation() { dispatching_valve = true; }
            ~Invocation() { dispatching_valve = false; }
        } guard;
        std::array<Unreal::UObject*, 2> arguments{pawn, box};
        actor->ProcessEvent(callback, arguments.data());
        return respond(true);
    }

    int clear_dialogue_scope(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw()) dialogue_input = {};
        return 0;
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

    struct ValueFactory
    {
        Unreal::UObject* library{};
        Unreal::UFunction* function{};
        Unreal::FDoubleProperty* x{};
        Unreal::FStructProperty* output{};

        bool bind(Unreal::UObject* action, Unreal::UScriptStruct* value_type)
        {
            library = find<Unreal::UObject>(STR("/Script/EnhancedInput.Default__EnhancedInputLibrary"));
            function = find<Unreal::UFunction>(STR("/Script/EnhancedInput.EnhancedInputLibrary:MakeInputActionValueOfType"));
            if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function ||
                function->GetOuterPrivate() != library->GetClassPrivate() || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
                function->HasAnyFunctionFlags(Unreal::FUNC_Net) || function->GetNumParms() != 5 ||
                function->GetParmsSize() != 0x40) return false;
            x = input<Unreal::FDoubleProperty>(function, STR("X"), 0);
            output = field<Unreal::FStructProperty>(function, STR("ReturnValue"));
            auto* enum_type = find<Unreal::UEnum>(STR("/Script/EnhancedInput.EInputActionValueType"));
            auto* value = input<Unreal::FEnumProperty>(function, STR("ValueType"), 0x18);
            auto* action_value = field<Unreal::FEnumProperty>(action, STR("ValueType"));
            auto* number = action_value ? action_value->GetUnderlyingProperty() : nullptr;
            return x && input<Unreal::FDoubleProperty>(function, STR("Y"), 8) &&
                input<Unreal::FDoubleProperty>(function, STR("Z"), 0x10) && enum_type &&
                value && value->GetEnum().Get() == enum_type && value->GetSize() == 1 &&
                action_value && action_value->GetEnum().Get() == enum_type && number && number->GetSize() == 1 &&
                number->GetUnsignedIntPropertyValue(action_value->ContainerPtrToValuePtr<void>(action)) == 0 &&
                output && output == function->GetReturnProperty() && output->GetOffset_Internal() == 0x20 &&
                output->GetSize() == 0x20 && output->GetStruct().Get() == value_type;
        }

        void dispatch(Unreal::UObject* pawn, Unreal::UObject* action, const Wrapper& wrapper, bool pressed) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x40> factory{};
            alignas(std::max_align_t) std::array<std::byte, 0x30> parameters{};
            StructParameter result(output, factory.data());
            StructParameter value(wrapper.value, parameters.data());
            // Boolean action type, Y and Z are zero-initialized.
            x->SetPropertyValueInContainer(factory.data(), pressed ? 1.0 : 0.0);
            library->ProcessEvent(function, factory.data());
            wrapper.value->CopyCompleteValue(wrapper.value->ContainerPtrToValuePtr<void>(parameters.data()),
                output->ContainerPtrToValuePtr<void>(factory.data()));
            wrapper.elapsed->SetPropertyValueInContainer(parameters.data(), pressed ? 0.0f : 0.05f);
            wrapper.triggered->SetPropertyValueInContainer(parameters.data(), pressed ? 0.0f : 0.05f);
            wrapper.source->SetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(parameters.data()), action);
            pawn->ProcessEvent(static_cast<Unreal::UFunction*>(wrapper.function.Get()), parameters.data());
        }
    };

    int probe(const Lua& lua)
    {
        const auto argument_count = lua.get_stack_size();
        if (!available() || !fixture_enabled() || (argument_count != 1 && argument_count != 2) || !lua.is_integer(1) ||
            (argument_count == 2 && !lua.is_bool(2)))
            return report(lua, false, "native_interact_fixture_unavailable");
        const auto requested = lua.get_integer();
        const bool allow_consumed = argument_count == 2 && lua.get_bool();
        auto* pawn = scope.pawn.Get();
        auto* action = scope.action.Get();
        const auto now = Clock::now();
        if (!pawn || address(pawn) != requested || now >= scope.expires || !live_scope(scope) || !standing(pawn))
            return report(lua, false, "native_interact_fixture_exact_local_pawn_required");
        discard_expired(now);
        if (count || event_depth || sequence == max_sequence)
            return report(lua, false, "native_interact_fixture_input_busy");
        ValueFactory factory;
        auto* held = field<Unreal::FBoolProperty>(pawn, STR("Holding_Interact"));
        if (!scope.completed.function.Get() || !held || held->GetPropertyValueInContainer(pawn) ||
            !factory.bind(action, scope.started.value->GetStruct().Get()))
            return report(lua, false, "stock_interact_fixture_schema_or_hold_unavailable");
        const auto before = sequence;
        const auto consumed_before = consumed_dispatches;
        factory.dispatch(pawn, action, scope.started, true);
        if (!live_scope(scope) || scope.pawn.Get() != pawn || !standing(pawn))
            return report(lua, false, "mutation_unverified_stock_interact_fixture_owner_changed");
        factory.dispatch(pawn, action, scope.completed, false);
        if (!live_scope(scope) || scope.pawn.Get() != pawn || !standing(pawn))
            return report(lua, false, "mutation_unverified_stock_interact_fixture_owner_changed");
        // The standalone synthetic press must not leave the pawn holding F.
        held->SetPropertyValueInContainer(pawn, false);
        if (allow_consumed && sequence == before && !count && consumed_dispatches == consumed_before + 1)
        {
            report(lua, true, "stock_started_consumed");
            lua.set_integer(0);
            return 3;
        }
        if (sequence != before + 1 || count != 1)
            return report(lua, false, "mutation_unverified_stock_interact_fixture_receipt");
        report(lua, true, "stock_started");
        lua.set_integer(static_cast<std::int64_t>(sequence));
        return 3;
    }
}

namespace silverfish::native_interaction_input
{
    bool has_current_host_press(Unreal::UObject* pawn)
    {
        return available() && scope.host && event_depth && event_source_matches && !event_consumed && pawn &&
            pawn == scope.pawn.Get() && Clock::now() < scope.expires && live_scope(scope) && standing(pawn);
    }

    Unreal::UObject* current_guest_pawn(std::uintptr_t requested)
    {
        if (!available() || scope.host || Clock::now() >= scope.expires || !live_scope(scope)) return nullptr;
        auto* pawn = scope.pawn.Get();
        return pawn && reinterpret_cast<std::uintptr_t>(pawn) == requested ? pawn : nullptr;
    }

    bool guest_interaction_in_reach(Unreal::UObject* pawn, Unreal::UObject* actor, Unreal::UObject* component)
    {
        return pawn && current_guest_pawn(reinterpret_cast<std::uintptr_t>(pawn)) == pawn && standing(pawn) &&
            actor && component && interaction_in_reach(pawn, actor, component);
    }

    bool consume_current_press(Unreal::UObject* pawn)
    {
        if (!available() || !event_depth || !event_source_matches || event_consumed || !pawn ||
            pawn != scope.pawn.Get() || Clock::now() >= scope.expires || !live_scope(scope) || !standing(pawn))
            return false;
        event_consumed = true;
        ++consumed_dispatches;
        return true;
    }

    void install_hooks()
    {
        if (!env_flag(environment) || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            event_pre, {false, false, owner, STR("Bracket stock local interact input")});
        callbacks[1] = Hook::RegisterProcessEventPostCallback(
            event_post, {false, false, owner, STR("Observe stock local interact input")});
        callbacks[2] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            script_pre, {false, false, owner, STR("Observe Blueprint local interact input")});
        for (const auto callback : callbacks)
        {
            if (callback == Hook::ERROR_ID)
            {
                uninstall_hooks();
                return;
            }
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
        if (!env_flag(environment)) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeInteractScope", set_scope);
        lua.register_function("SilverfishTakeNativeInteractPress", take_press);
        lua.register_function("SilverfishClearNativeInteractScope", lua_clear);
        lua.register_function("SilverfishSetNativeWireBoardScope", set_wire_board);
        lua.register_function("SilverfishTakeNativeWireBoardPress", take_wire_board_press);
        lua.register_function("SilverfishClearNativeWireBoardScope", clear_wire_board);
        lua.register_function("SilverfishSetNativeDialogueInputScope", set_dialogue_scope);
        lua.register_function("SilverfishTakeNativeDialogueInput", take_dialogue_press);
        lua.register_function("SilverfishClearNativeDialogueInputScope", clear_dialogue_scope);
        lua.register_function("SilverfishTakeNativeSubwayValvePress", take_valve_press);
        lua.register_function("SilverfishDispatchNativeSubwayValve", dispatch_valve);
        mosby_station_input::register_lua(lua);
        if (fixture_enabled()) lua.register_function("SilverfishProbeNativeInteractInput", probe);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (!Unreal::IsInGameThreadRaw()) return;
        scope = {};
        wire_board = {};
        dialogue_input = {};
        valve_input = {};
        mosby_station_input::clear_scope();
        first = count = event_depth = 0;
        event_source_matches = event_consumed = false;
    }
}
