#include "interaction_input.hpp"
#include "content_paths.hpp"
#include "mosby_station_input.hpp"
#include "mindcore_input.hpp"
#include "npc_barter.hpp"
#include "zone_exit.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
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
    constexpr auto wire_validate_contracts =
        STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:TurnIn_Contracts");
    constexpr auto quest_log = STR("/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C");
    constexpr auto quest_details = STR("/Game/Classes/GUI/SubWidgets/Quest_DetailsWindow_Widget.Quest_DetailsWindow_Widget_C");
    constexpr auto quest_turnin = STR("/Game/Classes/GUI/SubWidgets/Quest_TurnIn_Widget.Quest_TurnIn_Widget_C");
    constexpr auto quest_accept_function =
        STR("/Game/Classes/GUI/SubWidgets/Quest_DetailsWindow_Widget.Quest_DetailsWindow_Widget_C:AddQuest");
    constexpr auto quest_turnin_function =
        STR("/Game/Classes/GUI/SubWidgets/Quest_TurnIn_Widget.Quest_TurnIn_Widget_C:TurnIn");
    constexpr auto subway_world = STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P");
    constexpr auto subway_valve = STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P:PersistentLevel.ValveSwitch_BP_C_1");
    constexpr auto valve_class = STR("/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C");
    constexpr auto valve_interaction = STR("/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C:SendInteractionToActor");
    constexpr auto stash_class = STR("/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C");
    constexpr auto lantern_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C");
    constexpr auto door_latch_class = STR("/Game/Assets/StaticMeshes/Doors/DoorLatch_BP.DoorLatch_BP_C");
    constexpr auto linked_door_class = STR("/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C");
    constexpr auto gas_lamp_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_GassLight_01.Prop_GassLight_01_C");
    constexpr auto lantern_interaction = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:SendInteractionToActor");
    constexpr auto lantern_ignite = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:SetOnFire");
    constexpr auto lantern_extinguish = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:Extinguish");
    constexpr std::array candles{
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_02.Prop_Candle_02_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_03.Prop_Candle_03_C"),
    };
    constexpr auto radio_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Radio01.Prop_Radio01_C");
    constexpr std::array power_switches{
        STR("/Game/Classes/ScriptingActors/CircutBox_BP.CircutBox_BP_C"),
        STR("/Game/Classes/ScriptingActors/LightSwitch_BP.LightSwitch_BP_C"),
        STR("/Game/Classes/ScriptingActors/LightSwitch_02_BP.LightSwitch_02_BP_C"),
        radio_class,
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_TV_01.Prop_TV_01_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_TV_02.Prop_TV_02_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_TV_03.Prop_TV_03_C"),
    };

    constexpr auto sewer_screen_class = STR("/Game/Classes/GUI/Menu_Trade_Widget.Menu_Trade_Widget_C");
    constexpr auto sewer_card_class = STR("/Game/Classes/GUI/SubWidgets/TradeOffer_Widget.TradeOffer_Widget_C");

    constexpr auto npc_barter_complete = STR("/Game/Classes/GUI/New/BarterScreen.BarterScreen_C:CompleteTrade");
    constexpr int sewer_confirm_entry = 3258;
    constexpr int sewer_offer_count = 26;
    constexpr int wire_menu_entry = 0x15b;
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto receipt_lifetime = std::chrono::milliseconds(1500);
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;
    constexpr std::size_t pending_limit = 8;

    constexpr auto wire_board_box = STR("/Script/Engine.BoxComponent");
    constexpr auto wire_board_turn_in_enum = STR("/Game/Classes/Libraries/Enums_Lib/QuestTurnIns_Enum.QuestTurnIns_Enum");
    constexpr auto prototype_drill_board = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2");
    constexpr auto wire_board_turn_in_delegate = STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:TurnedInQuest__DelegateSignature");
    constexpr auto black_market_jammer_board = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.BountyBoard_BP_C_0");
    constexpr auto observatory_jammer_board = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.BountyBoard_BP_C_0");

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

    constexpr auto sewer_world = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P");
    constexpr auto sewer_volume = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.Trigger_Volume_BP_C_1");
    constexpr auto sewer_volume_class = STR("/Game/Classes/ScriptingActors/Trigger_Volume_BP.Trigger_Volume_BP_C");
    constexpr auto sewer_overlap = STR("/Game/Classes/ScriptingActors/Trigger_Volume_BP.Trigger_Volume_BP_C:BndEvt__Trigger_Volume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature");
    constexpr DialogueProfile sewer_dialogue{sewer_world, sewer_volume, sewer_volume_class, STR("Box"),
        STR("/Script/Engine.BoxComponent"), sewer_overlap};

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
        Unreal::FWeakObjectPtr focus{};
    };

    struct ScopedInteraction
    {
        Unreal::FWeakObjectPtr actor{}, component{}, pawn{};
        bool accepting{};
        Clock::time_point expires{};
        Receipt pending{};
    };

    struct PropCarryMovement
    {
        Unreal::UFunction* notify{};
        Unreal::FStructProperty* body_instance{};
        Unreal::FBoolProperty* simulating{};
        Unreal::FBoolProperty* level_visible{};
        Unreal::FWeakObjectPtr pawn{}, actor{}, root{}, grip{}, level{}, world{}, driver{}, server{};
        std::uint64_t scope_generation{};
        std::string lease;
        Clock::time_point expires{};
        std::uint64_t suppressed{}, passed{};
        bool released{};
    };

    struct StockBoardAction
    {
        Receipt receipt{};
        Unreal::FWeakObjectPtr menu{};
        std::string quest{};
        bool turnin{};
    };


    struct StockBoardMenu
    {
        Unreal::FWeakObjectPtr board{};
        Unreal::FWeakObjectPtr menu{};
        Clock::time_point render_lease{};
        unsigned validation_calls{};
        bool ready{};
    };

    struct SewerTradeInput
    {
        Unreal::FWeakObjectPtr screen{};
        std::array<Unreal::FWeakObjectPtr, sewer_offer_count> cards{};
        Clock::time_point expires{};
        Receipt pending{};
        int ordinal{};
    };

    SewerTradeInput sewer_trade;
    struct CastawayMenu
    {
        Unreal::FWeakObjectPtr screen;
        std::array<Unreal::FWeakObjectPtr, 3> responses;
        Receipt pending{};
        int ordinal{};
    };
    CastawayMenu castaway_menu;
    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    Scope scope;
    PropCarryMovement prop_movement;
    ScopedInteraction wire_board, dialogue_input, valve_input, stash_input, lantern_input;
    ScopedInteraction lantern_ignition;

    ScopedInteraction npc_barter_input, npc_barter_confirm;
    Unreal::FWeakObjectPtr npc_barter_screen;
    std::array<Unreal::FWeakObjectPtr, 2> npc_barter_backups;
    std::uint64_t npc_barter_sequence{};
    StockBoardAction stock_board_action;
    StockBoardMenu stock_board_menu;
    std::array<Receipt, pending_limit> receipts{};
    std::size_t first{}, count{}, event_depth{};
    std::uint64_t sequence{}, consumed_dispatches{};
    std::uint64_t wire_sequence{};
    std::uint64_t dialogue_sequence{}, valve_sequence{}, stash_sequence{}, lantern_sequence{};
    Clock::time_point stash_lease{};
    bool dispatching_valve = false;
    bool event_source_matches{}, event_consumed{};
    Unreal::FWeakObjectPtr event_focus;
    bool sewer_answering{};

    bool live_actor_role(Unreal::UObject* actor, std::uint8_t role)
    {
        auto* role_field = field<Unreal::FByteProperty>(actor, STR("Role"));
        auto* destroyed = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role_field && role_field->GetPropertyValueInContainer(actor) == role &&
            destroyed && !destroyed->GetPropertyValueInContainer(actor);
    }

    bool live_actor(Unreal::UObject* actor, Unreal::UObject* world, std::uint8_t role)
    {
        return live_actor_role(actor, role) && static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    bool visible_interactable_actor(Unreal::UObject* actor, Unreal::UObject* world)
    {
        return (live_actor_role(actor, 1) || live_actor_role(actor, 3)) &&
            silverfish::native_zone::visible_actor_level(static_cast<Unreal::AActor*>(actor),
                                                         static_cast<Unreal::UWorld*>(world));
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

    const DialogueProfile* dialogue_profile(Unreal::UObject* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        if (path == sewer_world) return &sewer_dialogue;
        for (const auto& profile : dialogue_profiles)
            if (path == profile.world) return &profile;
        return nullptr;
    }

    bool uses_stock_board_functions(Unreal::UClass* type)
    {
        auto* base = find<Unreal::UClass>(wire_board_class);
        if (!type || !base || !type->IsChildOf(base)) return false;
        if (type == base) return true;
        static const std::array functions{
            Unreal::FName{STR("SendInteractionToActor")},
            Unreal::FName{STR("Send_PowerSignal")},
            Unreal::FName{STR("Configure_Stock")},
            Unreal::FName{STR("TurnIn_Contracts")},
        };
        return std::ranges::all_of(functions, [base, type](const auto& name) {
            auto* stock = base->GetFunctionByNameInChain(name);
            return stock && type->GetFunctionByNameInChain(name) == stock;
        });
    }

    bool placed_wire_board(Unreal::UObject* world, Unreal::UObject* actor, Unreal::UObject* box)
    {
        if (!live(actor) || !uses_stock_board_functions(actor->GetClassPrivate()) ||
            !actor->HasAnyFlags(Unreal::RF_WasLoaded)) return false;
        auto* placed = static_cast<Unreal::AActor*>(actor);
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return destroying && !destroying->GetPropertyValueInContainer(actor) &&
            silverfish::native_zone::visible_actor_level(placed, static_cast<Unreal::UWorld*>(world)) &&
            (!scope.host || live_actor_role(actor, 3)) &&
            object_field(actor, STR("As Game Instance FP")) == scope.instance.Get() &&
            live(box) && box->GetClassPrivate() == find<Unreal::UClass>(wire_board_box) &&
            box->GetOuterPrivate() == actor && object_field(actor, STR("Box")) == box;
    }

    bool stock_board_policy(Unreal::UObject* actor)
    {
        auto* id = field<Unreal::FByteProperty>(actor, STR("TurnIn_ID"));
        auto* type = id ? id->GetEnum().Get() : nullptr;
        auto* all = field<Unreal::FBoolProperty>(actor, STR("ShowAllContracts"));
        if (!type || type != find<Unreal::UEnum>(wire_board_turn_in_enum) ||
            !all || all->GetPropertyValueInContainer(actor) ||
            !field<Unreal::FBoolProperty>(actor, STR("BountyBoard_RedeemableHere")) ||
            !field<Unreal::FBoolProperty>(actor, STR("AcceptAllGlobalContracts")) ||
            !field<Unreal::FBoolProperty>(actor, STR("Display_GlobalContracts"))) return false;
        const auto value = type->GetNameByValue(id->GetPropertyValueInContainer(actor));
        if (value.IsNone() || value.ToString().ends_with(STR("_MAX"))) return false;
        auto* property = field<Unreal::FMulticastInlineDelegateProperty>(actor, STR("TurnedInQuest"));
        auto* signature = find<Unreal::UFunction>(wire_board_turn_in_delegate);
        if (!property || !signature || property->GetSignatureFunction().Get() != signature ||
            property->GetSize() != sizeof(Unreal::FMulticastScriptDelegate)) return false;
        const auto* callbacks = property->ContainerPtrToValuePtr<Unreal::FMulticastScriptDelegate>(actor);
        // The existing Prototype Drill adapter handles the authored Scientist listener.
        return callbacks->Num() == 0 || actor->GetPathName() == prototype_drill_board;
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
        if (!actor || !function || !parameters || iteration.OriginalFunctionCallPrevented() ||
            Clock::now() >= wire_board.expires) return;
        static const Unreal::FName interaction{STR("SendInteractionToActor")};
        static const Unreal::FName script{STR("ExecuteUbergraph_BountyBoard_BP")};
        const auto name = function->GetFName();
        if (name != interaction && name != script) return;
        auto* pawn = wire_board.pawn.Get();
        if (!pawn || pawn != scope.pawn.Get() || !live_scope(scope) ||
            function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* world = scope.world.Get();
        if (name == script)
        {
            if (actor != wire_board.actor.Get() || !placed_wire_board(world, actor, wire_board.component.Get())) return;
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
        auto* interactor = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        auto* box = component->GetObjectPropertyValue(component->ContainerPtrToValuePtr<void>(parameters));
        if (interactor != pawn || object_field(pawn, STR("InteractionTarget_Component")) != box ||
            !placed_wire_board(world, actor, box) || !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        iteration.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!iteration.OriginalFunctionCallPrevented() || !wire_board.accepting ||
            (wire_board.pending.sequence && now < wire_board.pending.expires) ||
            wire_sequence == max_sequence || !standing(pawn) ||
            !stock_board_policy(actor) || !interaction_in_reach(pawn, actor, box)) return;
        wire_board.actor = actor;
        wire_board.component = box;
        wire_board.pending = {++wire_sequence, now + receipt_lifetime};
    }

    Unreal::UObject* top_menu()
    {
        auto* controller = scope.controller.Get();
        auto* stack = field<Unreal::FArrayProperty>(controller, STR("MenuStack"));
        auto* object = stack ? Unreal::CastField<Unreal::FObjectProperty>(stack->GetInner()) : nullptr;
        if (!object) return nullptr;
        Unreal::FScriptArrayHelper entries(stack, stack->ContainerPtrToValuePtr<void>(controller));
        return entries.Num() > 0 && entries.Num() <= 16 ? object->GetObjectPropertyValue(entries.GetRawPtr(entries.Num() - 1)) : nullptr;
    }

    Unreal::UObject* stock_menu_board(Unreal::UObject* log)
    {
        auto* world = scope.world.Get();
        auto* board = object_field(log, STR("BountyBoard_Ref"));
        auto* bounty = field<Unreal::FBoolProperty>(log, STR("BountyBoard"));
        if (!live_scope(scope) || !live(log) ||
            log->GetClassPrivate() != find<Unreal::UClass>(quest_log) ||
            log != top_menu() || log->GetWorld() != world ||
            !bounty || !bounty->GetPropertyValueInContainer(log) ||
            !placed_wire_board(world, board, object_field(board, STR("Box")))) return nullptr;
        return board;
    }

    void intercept_stock_board_validation(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function)
    {
        if (!actor || !function || actor != stock_board_menu.board.Get() ||
            Clock::now() >= stock_board_menu.render_lease || call.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName validate{STR("TurnIn_Contracts")};
        if (function->GetFName() != validate || function != find<Unreal::UFunction>(wire_validate_contracts) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            function->GetNumParms() != 0 || function->GetParmsSize() != 0 || function->GetReturnProperty() ||
            stock_menu_board(stock_board_menu.menu.Get()) != actor) return;
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented()) ++stock_board_menu.validation_calls;
    }

    void intercept_stock_board(Iteration& iteration, Unreal::UObject* widget, Unreal::UFunction* function)
    {
        if (!widget || !function || iteration.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName add{STR("AddQuest")}, turnin{STR("TurnIn")};
        const auto name = function->GetFName();
        if (name != add && name != turnin) return;
        const bool redeem = name == turnin;
        auto* type = find<Unreal::UClass>(redeem ? quest_turnin : quest_details);
        if (!type || !live(widget) || widget->GetClassPrivate() != type || function->GetOuterPrivate() != type ||
            function != find<Unreal::UFunction>(redeem ? quest_turnin_function : quest_accept_function) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || function->GetReturnProperty() ||
            function->GetNumParms() != (redeem ? 1 : 0) || function->GetParmsSize() != (redeem ? 1 : 0) ||
            !live_scope(scope)) return;
        auto* world = scope.world.Get();
        auto* log = object_field(widget, STR("QuestLog_MenuRef"));
        if (widget->GetWorld() != world) return;
        auto* board = stock_menu_board(log);
        const bool projected = board && log == stock_board_menu.menu.Get() && board == stock_board_menu.board.Get();
        if (!board)
        {
            if (log && log == stock_board_menu.menu.Get() && log == top_menu())
                iteration.PreventOriginalFunctionCall();
            return;
        }
        const auto path = board->GetPathName();
        iteration.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!iteration.OriginalFunctionCallPrevented() || !stock_board_policy(board) ||
            (projected && !stock_board_menu.ready) ||
            now >= scope.expires || !standing(scope.pawn.Get()) ||
            (stock_board_action.receipt.sequence && now < stock_board_action.receipt.expires) || wire_sequence == max_sequence) return;
        auto* quest = field<Unreal::FNameProperty>(widget, redeem ? STR("Quest_IDTag") : STR("Quest_ID"));
        if (!quest) return;
        const auto key = quest->GetPropertyValueInContainer(widget);
        static const Unreal::FName lake{STR("Jmr_Lake")}, crossroads{STR("Jmr_CrossRoads")};
        if (!projected && ((path != black_market_jammer_board && path != observatory_jammer_board) ||
            (key != lake && (key != crossroads || path != observatory_jammer_board)))) return;

        std::string id;
        if (projected)
        {
            const auto text = key.ToString();
            if (key.IsNone() || !silverfish::content_paths::quest_id(std::wstring_view{text})) return;
            for (const auto ch : text) id.push_back(static_cast<char>(ch));
        }
        else id = key == lake ? "Jmr_Lake" : "Jmr_CrossRoads";
        stock_board_action = {{++wire_sequence, now + receipt_lifetime}, log, std::move(id), redeem};
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
        if (!profile || profile == &sewer_dialogue || function != find<Unreal::UFunction>(profile->callback)
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

    void intercept_sewer_overlap(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        static const Unreal::FName overlap_name{STR("BndEvt__Trigger_Volume_BP_Box_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature")};
        if (!function || function->GetFName() != overlap_name || !live(actor) || !parameters ||
            call.OriginalFunctionCallPrevented() || actor->GetPathName() != sewer_volume ||
            actor->GetClassPrivate() != find<Unreal::UClass>(sewer_volume_class) ||
            function != find<Unreal::UFunction>(sewer_overlap) || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetNumParms() != 6 || function->GetReturnProperty()) return;
        auto* world = static_cast<Unreal::AActor*>(actor)->GetWorld();
        auto* driver = object_field(world, STR("NetDriver"));
        auto* other = input<Unreal::FObjectProperty>(function, STR("OtherActor"), sizeof(void*));
        auto* pawn = other ? other->GetObjectPropertyValue(other->ContainerPtrToValuePtr<void>(parameters)) : nullptr;
        if (!live(world) || world->GetPathName() != sewer_world || !live(driver) || object_field(driver, STR("World")) != world ||
            !live(pawn) || pawn->GetClassPrivate() != find<Unreal::UClass>(pawn_path)) return;
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!object_field(driver, STR("ServerConnection")) && live(player) && local_type && player->IsA(local_type) &&
            object_field(controller, STR("Pawn")) == pawn && live_actor(pawn, world, 3)) return;
        call.PreventOriginalFunctionCall();
    }

    bool queue_sewer_dialogue(Unreal::UObject* pawn)
    {
        const auto now = Clock::now();
        if (!pawn || pawn != scope.pawn.Get() || pawn != dialogue_input.pawn.Get() || scope.host ||
            now >= dialogue_input.expires || !dialogue_input.accepting || dialogue_sequence == max_sequence ||
            !live_scope(scope) || !standing(pawn) || dialogue_profile(scope.world.Get()) != &sewer_dialogue ||
            !exact_dialogue_actor(sewer_dialogue, dialogue_input.actor.Get(), dialogue_input.component.Get())) return false;
        if (dialogue_input.pending.sequence && now < dialogue_input.pending.expires) return true;
        dialogue_input.pending = {++dialogue_sequence, now + receipt_lifetime};
        return true;
    }

    void intercept_sewer_trade(Iteration& call, Unreal::UObject* card, Unreal::UFunction* function, const void* parameters)
    {
        if (!card || !function || call.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName pressed{STR("BndEvt__TradeOffer_Widget_Button_ConfirmTrade_K2Node_ComponentBoundEvent_0_Pressed__DelegateSignature")};
        static const Unreal::FName script{STR("ExecuteUbergraph_TradeOffer_Widget")};
        const auto name = function->GetFName();
        if (name != pressed && name != script) return;
        auto* card_type = find<Unreal::UClass>(sewer_card_class);
        auto* screen = object_field(card, STR("ParentWidget"));
        if (!live(card) || card->GetClassPrivate() != card_type || function->GetOuterPrivate() != card_type ||
            !live(screen) || screen->GetClassPrivate() != find<Unreal::UClass>(sewer_screen_class) ||
            object_field(screen, STR("Shop"))) return;
        auto* world = screen->GetWorld();
        if (!live(world) || world->GetPathName() != sewer_world) return;
        if (name == script)
        {
            auto* entry = input<Unreal::FIntProperty>(function, STR("EntryPoint"), 0);
            if (parameters && entry && function->GetNumParms() == 1 && function->GetParmsSize() == sizeof(std::int32_t) &&
                entry->GetPropertyValueInContainer(parameters) == sewer_confirm_entry)
                call.PreventOriginalFunctionCall();
            return;
        }
        if (function->GetNumParms() != 0 || function->GetParmsSize() != 0 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        // A detached stock screen never runs the local inventory-spending Blueprint.
        call.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!call.OriginalFunctionCallPrevented() || screen != sewer_trade.screen.Get() || scope.host ||
            now >= sewer_trade.expires || now >= scope.expires || !live_scope(scope) || !standing(scope.pawn.Get()) ||
            top_menu() != screen || dialogue_sequence == max_sequence ||
            (sewer_trade.pending.sequence && now < sewer_trade.pending.expires)) return;
        for (std::size_t i = 0; i < sewer_trade.cards.size(); ++i)
        {
            if (card != sewer_trade.cards[i].Get()) continue;
            sewer_trade.ordinal = static_cast<int>(i) + 1;
            sewer_trade.pending = {++dialogue_sequence, now + receipt_lifetime};
            sewer_trade.expires = {};
            break;
        }
    }

    int set_sewer_trade_screen(const Lua& lua)
    {
        const auto respond = [&](bool value) { lua.set_bool(value); return 1; };
        const auto arguments = lua.get_stack_size();
        if (!available() || arguments < 1 || arguments > 2 * sewer_offer_count + 1 ||
            arguments % 2 == 0 || !lua.is_integer(1) || scope.host || Clock::now() >= scope.expires ||
            !live_scope(scope) || !standing(scope.pawn.Get())) return respond(false);
        auto* world = scope.world.Get();
        auto* screen = top_menu();
        if (world->GetPathName() != sewer_world || !live(screen) || address(screen) != lua.get_integer(1) ||
            screen->GetClassPrivate() != find<Unreal::UClass>(sewer_screen_class) || screen->GetWorld() != world ||
            object_field(screen, STR("Shop"))) return respond(false);
        SewerTradeInput next;
        next.screen = screen;
        std::array<Address, sewer_offer_count> requested{};
        auto* card_type = find<Unreal::UClass>(sewer_card_class);
        if (!card_type) return respond(false);
        int needed{};
        for (int index = 2; index < arguments; index += 2)
        {
            if (!lua.is_integer(1) || !lua.is_integer(2)) return respond(false);
            const auto ordinal = lua.get_integer();
            const auto candidate = lua.get_integer();
            if (ordinal < 1 || ordinal > sewer_offer_count || candidate <= 0 || requested[ordinal - 1])
                return respond(false);
            for (auto address : requested)
                if (candidate == address) return respond(false);
            requested[ordinal - 1] = candidate;
            ++needed;
        }
        if (needed)
        {
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* card, int, int) {
                if (!live(card) || card->GetClassPrivate() != card_type ||
                    object_field(card, STR("ParentWidget")) != screen)
                    return RC::LoopAction::Continue;
                for (std::size_t i = 0; i < requested.size(); ++i)
                {
                    if (address(card) != requested[i]) continue;
                    next.cards[i] = card;
                    --needed;
                    break;
                }
                return needed ? RC::LoopAction::Continue : RC::LoopAction::Break;
            });
        }
        if (needed) return respond(false);
        next.expires = Clock::now() + lease;
        sewer_trade = next;
        return respond(true);
    }

    int take_sewer_trade_press(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1) || scope.host ||
            Clock::now() >= scope.expires || !live_scope(scope) || !standing(scope.pawn.Get()) ||
            sewer_trade.screen.Get() != top_menu() || !sewer_trade.screen.Get() ||
            address(sewer_trade.screen.Get()) != lua.get_integer(1)) return 0;
        const auto now = Clock::now();
        const auto pending = sewer_trade.pending;
        sewer_trade.pending = {};
        if (!pending.sequence)
        {
            if (now < sewer_trade.expires) sewer_trade.expires = now + lease;
            return 0;
        }
        if (now >= pending.expires) return 0;
        lua.set_integer(static_cast<std::int64_t>(pending.sequence));
        lua.set_integer(sewer_trade.ordinal);
        return 2;
    }

    constexpr auto castaway_screen_class = STR("/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C");
    constexpr auto castaway_response_class = STR("/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C");

    bool castaway_screen_active(Unreal::UObject* screen)
    {
        auto* world = scope.world.Get();
        return !scope.host && live_scope(scope) && live(world) && world->GetPathName() == dialogue_profiles.front().world &&
            live(screen) && screen->GetClassPrivate() == find<Unreal::UClass>(castaway_screen_class) &&
            screen->GetWorld() == world && top_menu() == screen && standing(scope.pawn.Get());
    }

    void intercept_castaway_response(Iteration& call, Unreal::UObject* response, Unreal::UFunction* function, const void* args)
    {
        auto* screen = castaway_menu.screen.Get();
        if (!screen || !response || !function || call.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName click{STR("BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature")};
        static const Unreal::FName graph{STR("ExecuteUbergraph_PlayerResponce_Widget")};
        const auto name = function->GetFName();
        if (name != click && name != graph) return;
        auto* type = find<Unreal::UClass>(castaway_response_class);
        if (!type || !live(response) || response->GetClassPrivate() != type ||
            function->GetOuterPrivate() != type || object_field(response, STR("ParentWidget_Ref")) != screen) return;
        if (name == graph)
        {
            auto* entry = input<Unreal::FIntProperty>(function, STR("EntryPoint"), 0);
            if (!args || !entry || function->GetParmsSize() != sizeof(std::int32_t) ||
                entry->GetPropertyValueInContainer(args) != 423) return;
        }
        else if (function->GetNumParms() || function->GetParmsSize() || function->GetReturnProperty()) return;
        call.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (!call.OriginalFunctionCallPrevented() || !castaway_screen_active(screen) || now >= scope.expires ||
            dialogue_sequence == max_sequence || castaway_menu.pending.sequence) return;
        for (std::size_t index = 0; index < castaway_menu.responses.size(); ++index)
        {
            if (response != castaway_menu.responses[index].Get()) continue;
            castaway_menu.ordinal = static_cast<int>(index) + 1;
            castaway_menu.pending = {++dialogue_sequence, now + receipt_lifetime};
            break;
        }
    }

    int set_castaway_screen(const Lua& lua)
    {
        const auto respond = [&](bool value) { lua.set_bool(value); return 1; };
        const auto count = lua.get_stack_size();
        if (!available() || !count || !(count % 2) || count > 7 || !lua.is_integer(1)) return respond(false);
        const auto requested_screen = lua.get_integer();
        if (!requested_screen && count == 1) { castaway_menu = {}; return respond(true); }
        auto* screen = top_menu();
        if (count < 3 || !castaway_screen_active(screen) || address(screen) != requested_screen) return respond(false);
        std::array<Address, 3> requested{};
        for (int index = 1; index < count; index += 2)
        {
            if (!lua.is_integer(1) || !lua.is_integer(2)) return respond(false);
            const auto ordinal = lua.get_integer();
            const auto candidate = lua.get_integer();
            if (ordinal < 1 || ordinal > 3 || candidate <= 0 || requested[ordinal - 1] ||
                std::ranges::find(requested, candidate) != requested.end()) return respond(false);
            requested[ordinal - 1] = candidate;
        }
        CastawayMenu next;
        next.screen = screen;
        auto* type = find<Unreal::UClass>(castaway_response_class);
        if (!type) return respond(false);
        int missing = (count - 1) / 2;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* response, int, int) {
            if (!live(response) || response->GetClassPrivate() != type || object_field(response, STR("ParentWidget_Ref")) != screen)
                return RC::LoopAction::Continue;
            for (std::size_t index = 0; index < requested.size(); ++index)
            {
                if (address(response) != requested[index]) continue;
                next.responses[index] = response;
                --missing;
                break;
            }
            return missing ? RC::LoopAction::Continue : RC::LoopAction::Break;
        });
        if (missing) return respond(false);
        castaway_menu = next;
        return respond(true);
    }

    int take_castaway_response(const Lua& lua)
    {
        auto* screen = castaway_menu.screen.Get();
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !castaway_screen_active(screen) || address(screen) != lua.get_integer(1)) return 0;
        const auto pending = castaway_menu.pending;
        castaway_menu.pending = {};
        if (!pending.sequence || Clock::now() >= pending.expires) return 0;
        lua.set_integer(static_cast<std::int64_t>(pending.sequence));
        lua.set_integer(castaway_menu.ordinal);
        return 2;
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

    bool stock_stash(Unreal::UObject* actor)
    {
        auto* base = find<Unreal::UClass>(stash_class);
        auto* dying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && base && actor->GetClassPrivate()->IsChildOf(base) && dying &&
            !dying->GetPropertyValueInContainer(actor) && static_cast<Unreal::AActor*>(actor)->GetWorld() == scope.world.Get();
    }

    void intercept_stash(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        static const Unreal::FName interact{STR("SendInteractionToActor")};
        const auto now = Clock::now();
        if (scope.host || !function || function->GetFName() != interact || !parameters ||
            call.OriginalFunctionCallPrevented() || now >= stash_lease || now >= scope.expires ||
            !live_scope(scope) || !stock_stash(actor)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* hit = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        auto* pawn_type = find<Unreal::UClass>(pawn_path);
        auto* component_type = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
        if (!player || !hit || player->GetPropertyClass().Get() != pawn_type || hit->GetPropertyClass().Get() != component_type ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        if (pawn != scope.pawn.Get()) return;

        // The original stash screen mutates the process-wide save reference.
        call.PreventOriginalFunctionCall();
        auto* component = hit->GetObjectPropertyValue(hit->ContainerPtrToValuePtr<void>(parameters));
        if (!call.OriginalFunctionCallPrevented() || stash_sequence == max_sequence || !live(component) ||
            component->GetOuterPrivate() != actor || !component->GetClassPrivate()->IsChildOf(component_type) ||
            object_field(pawn, STR("InteractionTarget_Component")) != component ||
            !interaction_in_reach(pawn, actor, component) ||
            !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        stash_input.actor = actor;
        stash_input.component = component;
        stash_input.pawn = pawn;
        stash_input.accepting = true;
        stash_input.expires = now + receipt_lifetime;
        stash_input.pending = {++stash_sequence, stash_input.expires};
    }

    bool stock_lantern(Unreal::UObject* actor)
    {
        auto* world = scope.world.Get();
        if (!live(world) || !live(actor) || !actor->HasAnyFlags(Unreal::RF_WasLoaded)) return false;
        auto* base = find<Unreal::UClass>(lantern_class);
        return base && actor->GetClassPrivate()->IsChildOf(base) &&
            visible_interactable_actor(actor, world);
    }

    void remember_lantern_press(ScopedInteraction& input, Unreal::UObject* pawn,
                               Unreal::UObject* actor, Unreal::UObject* component)
    {
        const auto expires = Clock::now() + receipt_lifetime;
        input.actor = actor;
        input.component = component;
        input.pawn = pawn;
        input.accepting = true;
        input.expires = expires;
        input.pending = {++lantern_sequence, expires};
    }

    bool queue_visible_lantern_press(Unreal::UObject* pawn)
    {
        if (scope.host || lantern_sequence == max_sequence || Clock::now() >= scope.expires ||
            live(object_field(pawn, STR("InteractionTarget_Component")))) return false;
        auto* actor = object_field(pawn, STR("ActorBeingLookedAt"));
        if (!stock_lantern(actor) || !live_scope(scope) || !standing(pawn)) return false;
        auto* lit = field<Unreal::FBoolProperty>(actor, STR("Lit"));
        auto* mesh = object_field(actor, STR("PropMesh"));
        if (!lit || !lit->GetPropertyValueInContainer(actor) || !live(mesh) ||
            mesh->GetOuterPrivate() != actor || mesh != object_field(actor, STR("RootComponent")) ||
            !interaction_in_reach(pawn, actor, mesh)) return false;
        // The visibility trace can select the lantern at a frame where the
        // stock interaction trace has no component at its clipped endpoint.
        remember_lantern_press(lantern_input, pawn, actor, mesh);
        return true;
    }

    void intercept_lantern(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        static const Unreal::FName interact{STR("SendInteractionToActor")};
        static const Unreal::FName ignite{STR("SetOnFire")};
        static const Unreal::FName extinguish{STR("Extinguish")};
        if (scope.host || !actor || !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        if ((name != interact && name != ignite && name != extinguish) ||
            Clock::now() >= scope.expires || !stock_lantern(actor) || !live_scope(scope)) return;
        if (name == ignite && function == find<Unreal::UFunction>(lantern_ignite))
        {
            call.PreventOriginalFunctionCall();
            if (!call.OriginalFunctionCallPrevented() || !parameters || lantern_sequence == max_sequence) return;
            auto* instigator = input<Unreal::FObjectProperty>(function, STR("Instigator"), 2 * sizeof(double));
            auto* pawn = scope.pawn.Get();
            auto* mesh = object_field(actor, STR("PropMesh"));
            auto* lit = field<Unreal::FBoolProperty>(actor, STR("Lit"));
            if (instigator && pawn && standing(pawn) && live(mesh) && lit && !lit->GetPropertyValueInContainer(actor) &&
                instigator->GetObjectPropertyValue(instigator->ContainerPtrToValuePtr<void>(parameters)) == pawn)
                remember_lantern_press(lantern_ignition, pawn, actor, mesh);
            return;
        }
        if (name == extinguish && function == find<Unreal::UFunction>(lantern_extinguish))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        if (function != find<Unreal::UFunction>(lantern_interaction) || !parameters ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* hit = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        auto* pawn_type = find<Unreal::UClass>(pawn_path);
        auto* component_type = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
        if (!player || !hit || player->GetPropertyClass().Get() != pawn_type || hit->GetPropertyClass().Get() != component_type) return;
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(parameters));
        if (pawn != scope.pawn.Get()) return;
        call.PreventOriginalFunctionCall();
        auto* component = hit->GetObjectPropertyValue(hit->ContainerPtrToValuePtr<void>(parameters));
        if (!call.OriginalFunctionCallPrevented() || lantern_sequence == max_sequence || !live(component) ||
            component->GetOuterPrivate() != actor || !component->GetClassPrivate()->IsChildOf(component_type) ||
            object_field(pawn, STR("InteractionTarget_Component")) != component ||
            !interaction_in_reach(pawn, actor, component) ||
            !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        remember_lantern_press(lantern_input, pawn, actor, component);
    }

    void intercept_npc_barter(Iteration&, Unreal::UObject*, Unreal::UFunction*);

    enum class SwitchKind { Unsupported, Lantern, Candle, GasLamp, DoorLatch, Power };

    SwitchKind stock_switch_kind(const Unreal::UClass* klass)
    {
        for (; klass; klass = klass->GetSuperClass())
        {
            const auto path = klass->GetPathName();
            if (path == lantern_class) return SwitchKind::Lantern;
            if (path == gas_lamp_class) return SwitchKind::GasLamp;
            if (path == door_latch_class) return SwitchKind::DoorLatch;
            if (std::ranges::find(candles, path) != candles.end()) return SwitchKind::Candle;
            if (std::ranges::find(power_switches, path) != power_switches.end()) return SwitchKind::Power;
        }
        return SwitchKind::Unsupported;
    }

    void intercept_stock_switch(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* args)
    {
        static const Unreal::FName interaction{STR("SendInteractionToActor")};
        static const Unreal::FName ignite{STR("SetOnFire")};
        static const Unreal::FName extinguish{STR("Extinguish")};
        if (!function || scope.host) return;
        const auto name = function->GetFName();
        if (name != interaction && name != ignite && name != extinguish) return;
        if (call.OriginalFunctionCallPrevented() || !live(actor) || !actor->HasAnyFlags(Unreal::RF_WasLoaded)) return;
        auto* pawn = scope.pawn.Get();
        auto* world = scope.world.Get();
        if (!pawn || Clock::now() >= scope.expires || !live_scope(scope)) return;
        auto* klass = actor->GetClassPrivate();
        const auto kind = stock_switch_kind(klass);
        if (kind == SwitchKind::Unsupported || kind == SwitchKind::Lantern) return;
        const bool candle = kind == SwitchKind::Candle;
        const bool flame_prop = candle || kind == SwitchKind::GasLamp;
        if (!visible_interactable_actor(actor, world)) return;
        if (name != interaction)
        {
            if (candle && function == find<Unreal::UFunction>(name == ignite ? lantern_ignite : lantern_extinguish))
                call.PreventOriginalFunctionCall();
            return;
        }
        if (!args) return;
        if (klass->GetFunctionByNameInChain(interaction) != function || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        auto* target = input<Unreal::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        if (!player || !target || player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(args)) != pawn) return;
        auto* hit = target->GetObjectPropertyValue(target->ContainerPtrToValuePtr<void>(args));
        const auto* component = flame_prop ? STR("Sphere") : kind == SwitchKind::DoorLatch ? STR("Box_Knob") : STR("Box");
        if (live(hit) && hit->GetOuterPrivate() == actor &&
            (hit == object_field(actor, component) ||
                (flame_prop && hit == object_field(actor, STR("PropMesh")))))
            call.PreventOriginalFunctionCall();
    }

    void intercept_critter_pickup(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* args)
    {
        static const Unreal::FName interaction{STR("SendInteractionToActor")};
        if (!function || function->GetFName() != interaction || !args || scope.host ||
            call.OriginalFunctionCallPrevented() || !live(actor)) return;
        auto* pawn = scope.pawn.Get();
        auto* world = scope.world.Get();
        if (!pawn || Clock::now() >= scope.expires || !live_scope(scope)) return;
        auto* klass = actor->GetClassPrivate();
        const auto path = klass->GetPathName();
        // These stock overrides create an inventory item and immediately invoke its pickup.
        if (path != STR("/Game/Classes/Ambiant_FX/Critter_Frog_BP.Critter_Frog_BP_C") &&
            path != STR("/Game/Classes/Ambiant_FX/Critter_Weta_BP.Critter_Weta_BP_C") &&
            path != STR("/Game/Classes/Ambiant_FX/Critter_Beetle_BP.Critter_Beetle_BP_C")) return;
        if (!visible_interactable_actor(actor, world)) return;
        if (function->GetOuterPrivate() != klass || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), 0);
        if (player && player->GetPropertyClass().Get() == pawn->GetClassPrivate() &&
            player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(args)) == pawn)
            call.PreventOriginalFunctionCall();
    }

    void route_interaction(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        intercept_critter_pickup(iteration, actor, function, parameters);
        intercept_stock_switch(iteration, actor, function, parameters);
        intercept_wire_board(iteration, actor, function, parameters);
        intercept_npc_barter(iteration, actor, function);
        intercept_stock_board_validation(iteration, actor, function);
        intercept_stock_board(iteration, actor, function);
        intercept_sewer_trade(iteration, actor, function, parameters);
        intercept_castaway_response(iteration, actor, function, parameters);
        intercept_sewer_overlap(iteration, actor, function, parameters);
        intercept_dialogue(iteration, actor, function, parameters);
        intercept_valve(iteration, actor, function, parameters);
        intercept_stash(iteration, actor, function, parameters);
        intercept_lantern(iteration, actor, function, parameters);
        silverfish::mosby_station_input::route(iteration, actor, function, parameters);
        silverfish::mindcore_input::route(iteration, actor, function, parameters);
    }


    bool exact_dispatch(Unreal::UObject* pawn, Unreal::UFunction* function)
    {
        return pawn && function && pawn == scope.pawn.Get() && function == scope.started.function.Get();
    }

    Unreal::FWeakObjectPtr pressed_focus(Unreal::UObject* pawn)
    {
        // The stock focus can change before the co-op input queue is drained.
        if (scope.host) return {};
        auto* can_interact = field<Unreal::FBoolProperty>(pawn, STR("CanInteract"));
        if (!can_interact || !can_interact->GetPropertyValueInContainer(pawn)) return {};
        auto* component = object_field(pawn, STR("InteractionTarget_Component"));
        return live(component) ? Unreal::FWeakObjectPtr{component} : Unreal::FWeakObjectPtr{};
    }

    void record(Iteration& iteration, Unreal::UObject* pawn, const void* parameters, Unreal::FWeakObjectPtr focus)
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
        receipts[(first + count) % pending_limit] = {++sequence, now + receipt_lifetime, focus};
        ++count;
    }

    bool current_prop_grip(const PropCarryMovement& bound, Clock::time_point now)
    {
        if (scope.host || now >= scope.expires || bound.scope_generation != scope.generation || !live_scope(scope) ||
            bound.world.Get() != scope.world.Get() || bound.driver.Get() != scope.driver.Get() ||
            bound.server.Get() != scope.server.Get()) return false;
        auto* pawn = bound.pawn.Get();
        auto* actor = bound.actor.Get();
        auto* root = bound.root.Get();
        auto* grip = bound.grip.Get();
        auto* level = bound.level.Get();
        if (!pawn || pawn != scope.pawn.Get() || !standing(pawn) || !live(root) || !live(grip) || !live(level) ||
            !live_actor_role(actor, 1)) return false; // ROLE_SimulatedProxy
        auto* placed = static_cast<Unreal::AActor*>(actor);
        if (placed->GetLevel() != level ||
            (bound.level_visible ? !bound.level_visible->GetPropertyValueInContainer(level)
                                 : placed->GetWorld() != scope.world.Get())) return false;
        auto* movement = field<Unreal::FBoolProperty>(actor, STR("bReplicateMovement"));
        return movement && movement->GetPropertyValueInContainer(actor) &&
            object_field(pawn, STR("HeldObject")) == actor && object_field(pawn, STR("PhysicsHandle")) == grip &&
            object_field(grip, STR("GrabbedComponent")) == root && object_field(actor, STR("RootComponent")) == root &&
            object_field(actor, STR("PropMesh")) == root && root->GetOuterPrivate() == actor && grip->GetOuterPrivate() == pawn &&
            bound.simulating->GetPropertyValueInContainer(bound.body_instance->ContainerPtrToValuePtr<void>(root));
    }

    bool prop_carry_movement_active(Clock::time_point now)
    {
        if (prop_movement.released || now >= prop_movement.expires) return false;
        if (current_prop_grip(prop_movement, now)) return true;
        prop_movement.released = true;
        return false;
    }

    enum class LeanTraceStage : std::size_t { event_pre, event_post, script_pre };

    struct LeanDispatchTrace
    {
        Unreal::FWeakObjectPtr pawn, action;
        std::array<Wrapper, 3> callbacks;
        std::array<std::array<unsigned, 3>, 3> calls{};
        std::array<unsigned, 3> prevented{};
        bool active{};
    };

    LeanDispatchTrace lean_trace;

    void observe_lean_dispatch(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function,
        const void* parameters, LeanTraceStage stage)
    {
        if (!function || !pawn || pawn != lean_trace.pawn.Get() || pawn != scope.pawn.Get() || !parameters) return;
        const auto* action = lean_trace.action.Get();
        if (!action) return;
        for (std::size_t i = 0; i < lean_trace.callbacks.size(); ++i)
        {
            const auto& callback = lean_trace.callbacks[i];
            if (function != callback.function.Get() ||
                callback.source->GetObjectPropertyValue(callback.source->ContainerPtrToValuePtr<void>(parameters)) != action)
                continue;
            ++lean_trace.calls[static_cast<std::size_t>(stage)][i];
            if (stage == LeanTraceStage::event_post && iteration.OriginalFunctionCallPrevented()) ++lean_trace.prevented[i];
            break;
        }
    }

    void event_pre(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        if (!available()) return;
        if (lean_trace.active) observe_lean_dispatch(iteration, pawn, function, parameters, LeanTraceStage::event_pre);
        if (function && function == prop_movement.notify && pawn == prop_movement.actor.Get())
        {
            if (iteration.OriginalFunctionCallPrevented()) return;
            if (prop_carry_movement_active(Clock::now())) iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented()) ++prop_movement.suppressed;
            else ++prop_movement.passed;
            return;
        }
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
            event_focus = event_source_matches ? pressed_focus(pawn) : Unreal::FWeakObjectPtr{};
            if (event_source_matches && !iteration.OriginalFunctionCallPrevented() &&
                ((sewer_answering && queue_sewer_dialogue(pawn)) || (!event_focus.Get() && queue_visible_lantern_press(pawn))))
            {
                iteration.PreventOriginalFunctionCall();
                event_consumed = iteration.OriginalFunctionCallPrevented();
                if (event_consumed) ++consumed_dispatches;
            }
        }
    }

    void event_post(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        if (!available()) return;
        if (lean_trace.active) observe_lean_dispatch(iteration, pawn, function, parameters, LeanTraceStage::event_post);
        if (!exact_dispatch(pawn, function) || !event_depth) return;
        if (--event_depth != 0) return;
        if (!event_consumed) record(iteration, pawn, parameters, event_focus);
        event_focus = Unreal::FWeakObjectPtr{};
        event_source_matches = event_consumed = false;
    }

    void script_pre(Iteration& iteration, Unreal::UObject* pawn, Unreal::FFrame& frame, void*)
    {
        // ProcessEvent owns its whole synchronous dispatch, including the VM.
        // Script-to-script invocations can reach the wrapper without ProcessEvent.
        if (!available() || frame.Object() != pawn) return;
        if (lean_trace.active)
            observe_lean_dispatch(iteration, pawn, frame.Node(), frame.Locals(), LeanTraceStage::script_pre);
        if (exact_dispatch(pawn, frame.Node()))
        {
            if (!event_depth) record(iteration, pawn, frame.Locals(), pressed_focus(pawn));
        }
        else route_interaction(iteration, pawn, frame.Node(), frame.Locals());
    }

    int report(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int arm_prop_carry_movement(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || !lua.is_integer(5))
            return report(lua, false, "stock_prop_movement_arguments");
        const auto pawn_address = lua.get_integer();
        const auto actor_address = lua.get_integer();
        const auto root_address = lua.get_integer();
        const std::string lease_id{lua.get_string()};
        const auto duration = lua.get_integer();
        if (lease_id.size() != 32 || lease_id.find_first_not_of("0123456789abcdef") != std::string::npos ||
            duration <= 0 || duration > 1000)
            return report(lua, false, "stock_prop_movement_lease");
        auto* pawn = scope.pawn.Get();
        if (!pawn || address(pawn) != pawn_address || scope.host)
            return report(lua, false, "stock_prop_movement_pawn");
        auto* actor = object_field(pawn, STR("HeldObject"));
        auto* root = object_field(actor, STR("RootComponent"));
        auto* grip = object_field(pawn, STR("PhysicsHandle"));
        if (!live(actor) || address(actor) != actor_address || !live(root) || address(root) != root_address || !live(grip))
            return report(lua, false, "stock_prop_movement_grip");

        const auto now = Clock::now();
        if (prop_movement.lease == lease_id)
        {
            if (prop_movement.released || prop_movement.pawn.Get() != pawn || prop_movement.actor.Get() != actor ||
                prop_movement.root.Get() != root || prop_movement.grip.Get() != grip || !current_prop_grip(prop_movement, now))
            {
                prop_movement.released = true;
                return report(lua, false, "stock_prop_movement_released");
            }
            prop_movement.expires = now + std::chrono::milliseconds(duration);
            return report(lua, true, "stock_prop_movement_armed");
        }

        auto* base = find<Unreal::UClass>(STR("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C"));
        auto* core = find<Unreal::UClass>(STR("/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"));
        auto* mesh = find<Unreal::UClass>(STR("/Script/Engine.StaticMeshComponent"));
        auto* world = scope.world.Get();
        if (!base || !actor->IsA(base) || (core && actor->IsA(core)) || !mesh || !root->IsA(mesh) ||
            !silverfish::native_zone::visible_actor_level(static_cast<Unreal::AActor*>(actor),
                                                          static_cast<Unreal::UWorld*>(world)))
            return report(lua, false, "stock_prop_movement_replica");
        auto* level = static_cast<Unreal::AActor*>(actor)->GetLevel();
        const bool streamed = level != object_field(world, STR("PersistentLevel"));
        auto* visible = streamed ? field<Unreal::FBoolProperty>(level, STR("bIsVisible")) : nullptr;
        if (streamed && !visible) return report(lua, false, "stock_prop_movement_replica");

        auto* owner_type = find<Unreal::UClass>(STR("/Script/Engine.Actor"));
        auto* notify = find<Unreal::UFunction>(STR("/Script/Engine.Actor:OnRep_ReplicatedMovement"));
        auto* instance_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.BodyInstance"));
        auto* body = field<Unreal::FStructProperty>(root, STR("BodyInstance"));
        auto* simulating = field<Unreal::FBoolProperty>(instance_type, STR("bSimulatePhysics"));
        if (!owner_type || !notify || notify->GetOuterPrivate() != owner_type || notify->GetNumParms() ||
            notify->GetParmsSize() || notify->GetReturnProperty() || !notify->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            notify->HasAnyFunctionFlags(Unreal::FUNC_Net) ||
            actor->GetClassPrivate()->GetFunctionByNameInChain(notify->GetFName()) != notify ||
            !instance_type || !body || body->GetStruct().Get() != instance_type || !simulating)
            return report(lua, false, "stock_prop_movement_schema");

        PropCarryMovement next{notify, body, simulating, visible, pawn, actor, root, grip, level, world,
                               scope.driver, scope.server, scope.generation, lease_id,
                               now + std::chrono::milliseconds(duration)};
        if (!current_prop_grip(next, now)) return report(lua, false, "stock_prop_movement_grip");
        prop_movement = std::move(next);
        return report(lua, true, "stock_prop_movement_armed");
    }

    int release_prop_carry_movement(const Lua&)
    {
        if (available()) prop_movement.released = true;
        return 0;
    }

    int prop_carry_movement_status(const Lua& lua)
    {
        if (!available() || prop_movement.lease.empty()) return 0;
        lua.set_string(prop_movement.lease);
        lua.set_bool(prop_carry_movement_active(Clock::now()));
        lua.set_integer(static_cast<std::int64_t>(prop_movement.suppressed));
        lua.set_integer(static_cast<std::int64_t>(prop_movement.passed));
        return 4;
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
        auto* focus = receipts[first].focus.Get();
        lua.set_string(live(focus) ? RC::to_string(focus->GetPathName()) : std::string{});
        first = (first + 1) % pending_limit;
        --count;
        return 3;
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
        if (pawn != wire_board.pawn.Get() || now >= wire_board.expires) wire_board = {};
        wire_board.pawn = pawn;
        wire_board.accepting = accepting;
        wire_board.expires = now + lease;
        return report(lua, true, "ready");
    }

    std::uint64_t take_interaction_sequence(Address requested, ScopedInteraction& target)
    {
        auto* pawn = target.pawn.Get();
        const auto now = Clock::now();
        if (!pawn || address(pawn) != requested || pawn != scope.pawn.Get() || !target.accepting ||
            now >= target.expires || !target.pending.sequence || now >= target.pending.expires ||
            !live_scope(scope) || !standing(pawn)) return 0;
        const auto accepted = target.pending.sequence;
        target.pending = {};
        return accepted;
    }

    int take_interaction_press(const Lua& lua, ScopedInteraction& target)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto accepted = take_interaction_sequence(lua.get_integer(), target);
        if (!accepted) return 0;
        lua.set_integer(static_cast<std::int64_t>(accepted));
        return 1;
    }

    int take_wire_board_press(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        auto* board = wire_board.actor.Get();
        if (wire_board.pending.sequence && placed_wire_board(scope.world.Get(), board, wire_board.component.Get()) &&
            stock_board_policy(board))
        {
            if (const auto accepted = take_interaction_sequence(requested, wire_board))
            {
                lua.set_integer(static_cast<std::int64_t>(accepted));
                lua.set_string(RC::to_string(board->GetPathName()));
                return 2;
            }
        }

        // Simmi's stock dialogue powers its own board without pressing Interact on a box.
        const auto now = Clock::now();
        auto* pawn = scope.pawn.Get();
        if (!pawn || pawn != wire_board.pawn.Get() || address(pawn) != requested || now >= scope.expires ||
            now >= wire_board.expires || wire_sequence == max_sequence || !standing(pawn)) return 0;
        auto* menu = top_menu();
        if (!live(menu) || menu == stock_board_menu.menu.Get() ||
            menu->GetClassPrivate() != find<Unreal::UClass>(quest_log)) return 0;
        board = stock_menu_board(menu);
        if (!board || board->GetPathName() != observatory_jammer_board || !stock_board_policy(board)) return 0;

        wire_board.actor = board;
        wire_board.component = object_field(board, STR("Box"));
        stock_board_menu = {board, menu};
        stock_board_action = {};
        lua.set_integer(static_cast<std::int64_t>(++wire_sequence));
        lua.set_string(RC::to_string(board->GetPathName()));
        lua.set_bool(true);
        return 3;
    }

    int qualify_stock_board_policy(const Lua& lua)
    {
        const auto result = [&](bool ok) { lua.set_bool(ok); return 1; };
        if (!available() || !scope.host || !live_scope(scope) || Clock::now() >= scope.expires ||
            lua.get_stack_size() != 2 || !lua.is_string(1) || !lua.is_integer(2)) return result(false);
        const auto path = RC::to_wstring(lua.get_string());
        const auto requested = lua.get_integer();
        if (path.empty() || path.size() > 512) return result(false);
        auto* board = find<Unreal::UObject>(path.c_str());
        return result(address(board) == requested &&
            placed_wire_board(scope.world.Get(), board, object_field(board, STR("Box"))) && stock_board_policy(board));
    }

    int take_stock_board_action(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            address(scope.pawn.Get()) != lua.get_integer() || !live_scope(scope)) return 0;
        const auto action = stock_board_action;
        stock_board_action = {};
        if (!action.receipt.sequence || Clock::now() >= action.receipt.expires ||
            !action.menu.Get() || action.menu.Get() != top_menu()) return 0;
        lua.set_integer(static_cast<std::int64_t>(action.receipt.sequence));
        lua.set_string(action.turnin ? "stock-turnin" : "stock-start");
        lua.set_string(action.quest);
        return 3;
    }

    int render_stock_board(const Lua& lua)
    {
        const auto result = [&](bool ok, unsigned count = 0) {
            lua.set_bool(ok);
            lua.set_integer(count);
            return 2;
        };
        if (!available() || lua.get_stack_size() != 3 || !lua.is_integer(1) ||
            !lua.is_integer(2) || !lua.is_bool(3)) return result(false);
        const auto pawn = lua.get_integer(), menu_address = lua.get_integer();
        const auto start = lua.get_bool();
        auto* menu = top_menu();
        auto* board = stock_menu_board(menu);
        if (!board || board != wire_board.actor.Get() || !stock_board_policy(board) || !standing(scope.pawn.Get()) ||
            pawn != address(scope.pawn.Get()) || menu_address != address(menu)) return result(false);

        if (start)
        {
            stock_board_menu = {board, menu, Clock::now() + receipt_lifetime};
            stock_board_action = {};
            return result(true);
        }
        if (board != stock_board_menu.board.Get() || menu != stock_board_menu.menu.Get()) return result(false);
        stock_board_menu.render_lease = {};
        stock_board_menu.ready = stock_board_menu.validation_calls > 0;
        return result(stock_board_menu.ready, stock_board_menu.validation_calls);
    }

    int clear_wire_board(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            wire_board = {};
            stock_board_action = {};
            stock_board_menu = {};
        }
        return 0;
    }

    int set_dialogue_scope(const Lua& lua)
    {
        const auto respond = [&](bool value) { lua.set_bool(value); return 1; };
        const auto arguments = lua.get_stack_size();
        if (!available() || (arguments != 2 && arguments != 3) || !lua.is_integer(1) || !lua.is_bool(2) ||
            (arguments == 3 && !lua.is_bool(3)))
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
        sewer_answering = profile == &sewer_dialogue && arguments == 3 && accepting && lua.get_bool();
        return respond(true);
    }


    bool stock_npc_barter_menu(Unreal::UObject* screen)
    {
        if (!live(screen) || screen->GetClassPrivate() != silverfish::npc_barter::type(silverfish::npc_barter::screen_class))
            return false;
        auto* trader = object_field(screen, STR("Trader"));
        auto* npc = silverfish::npc_barter::linked_npc(trader, static_cast<Unreal::UWorld*>(scope.world.Get()));
        auto* save = field<Unreal::FBoolProperty>(trader, STR("Save"));
        return npc && save && (trader == npc_barter_input.actor.Get() || save->GetPropertyValueInContainer(trader));
    }

    bool npc_barter_menu_active(Unreal::UObject* screen)
    {
        auto* top = top_menu();
        if (!live(screen) || !live(top)) return false;
        if (top == screen) return true;
        auto* tree = top->GetOuterPrivate();
        auto* confirmation = silverfish::npc_barter::type(STR("/Game/Classes/GUI/New/ConfirmationScreen.ConfirmationScreen_C"));
        return confirmation && top->GetClassPrivate() == confirmation && live(tree) && tree->GetOuterPrivate() == screen;
    }

    void intercept_npc_barter(Iteration& call, Unreal::UObject* object, Unreal::UFunction* function)
    {
        if (!object || !function || call.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName confirm{STR("CompleteTrade")};
        if (function->GetFName() != confirm || function != find<Unreal::UFunction>(npc_barter_complete) ||
            function->GetNumParms() != 0 || function->GetParmsSize() != 0 || function->GetReturnProperty() ||
            !live_scope(scope)) return;
        const auto tracked = object == npc_barter_screen.Get();
        auto* trader = object_field(object, STR("Trader"));
        auto* linked = silverfish::npc_barter::linked_npc(trader, static_cast<Unreal::UWorld*>(scope.world.Get()), false);
        auto* alive = field<Unreal::FBoolProperty>(linked, STR("Alive"));
        const bool dead = alive && !alive->GetPropertyValueInContainer(linked);
        const bool can_route = !scope.host && stock_npc_barter_menu(object);
        if (!dead && !tracked && !can_route) return;
        // Keep stock selection and confirmation controls. The guest's local
        // GameInstance must never run the exchange against its inventory copy.
        call.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (call.OriginalFunctionCallPrevented() && can_route && tracked && now < npc_barter_input.expires &&
            now < scope.expires && live_scope(scope) && standing(scope.pawn.Get()) && npc_barter_menu_active(object) &&
            !npc_barter_confirm.pending.sequence && npc_barter_sequence < max_sequence)
            npc_barter_confirm.pending = {++npc_barter_sequence, now + receipt_lifetime};
    }

    int capture_npc_barter_menu(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer() || scope.host ||
            !npc_barter_input.accepting || Clock::now() >= npc_barter_input.expires || !live_scope(scope) ||
            npc_barter_sequence == max_sequence) return 0;
        auto* screen = top_menu();
        if (address(screen) != lua.get_integer() || !stock_npc_barter_menu(screen)) return 0;
        lua.set_integer(static_cast<std::int64_t>(++npc_barter_sequence));
        return 1;
    }

    int set_npc_barter(const Lua& lua)
    {
        const auto respond = [&](bool result) { lua.set_bool(result); return 1; };
        if (!available() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_bool(3) ||
            scope.host || Clock::now() >= scope.expires || !live_scope(scope)) return respond(false);
        auto* pawn = scope.pawn.Get();
        if (address(pawn) != lua.get_integer()) return respond(false);
        auto* screen = npc_barter_screen.Get();
        if (!npc_barter_menu_active(screen)) screen = top_menu();
        auto* actor = object_field(screen, STR("Trader"));
        if (address(actor) != lua.get_integer() || !stock_npc_barter_menu(screen)) return respond(false);
        auto* box = object_field(actor, STR("Box"));
        if (!live(box) || box->GetOuterPrivate() != actor) return respond(false);
        bind_interaction(npc_barter_input, actor, box, pawn, lua.get_bool(), Clock::now());
        if (npc_barter_confirm.pawn.Get() != pawn)
        {
            npc_barter_confirm = {};
            npc_barter_confirm.pawn = pawn;
        }
        npc_barter_confirm.accepting = true;
        npc_barter_confirm.expires = npc_barter_input.expires;
        return respond(true);
    }

    void clear_npc_barter_screen()
    {
        for (auto& backup : npc_barter_backups)
        {
            if (auto* object = backup.Get(); live(object) && object->IsRootSet()) object->ClearRootSet();
            backup = Unreal::FWeakObjectPtr{};
        }
        npc_barter_confirm.pending = {};
        npc_barter_screen = Unreal::FWeakObjectPtr{};
    }

    int set_npc_barter_screen(const Lua& lua)
    {
        const auto respond = [&](bool result) { lua.set_bool(result); return 1; };
        const auto count = lua.get_stack_size();
        if (!available() || (count != 1 && count != 3) || !lua.is_integer()) return respond(false);
        const auto requested = lua.get_integer();
        if (!requested && count == 1) { clear_npc_barter_screen(); return respond(true); }
        if (count != 3 || scope.host || !live_scope(scope) || !lua.is_integer(1) || !lua.is_integer(2))
            return respond(false);
        const std::array<Address, 2> snapshots{lua.get_integer(), lua.get_integer()};
        auto* screen = top_menu();
        auto* type = find<Unreal::UClass>(STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"));
        if (address(screen) != requested || !stock_npc_barter_menu(screen) || !type ||
            snapshots[0] <= 0 || snapshots[1] <= 0 || snapshots[0] == snapshots[1]) return respond(false);
        std::array<Unreal::UObject*, 2> objects{};
        auto* canonical = object_field(scope.instance.Get(), STR("SaveSlot_Ref"));
        auto* pawn_save = object_field(scope.pawn.Get(), STR("Save"));
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (!live(object) || object->GetClassPrivate() != type || object == canonical ||
                object == pawn_save || object->IsRootSet()) return RC::LoopAction::Continue;
            for (std::size_t index = 0; index < snapshots.size(); ++index)
                if (address(object) == snapshots[index]) objects[index] = object;
            return objects[0] && objects[1] ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        if (!objects[0] || !objects[1]) return respond(false);
        clear_npc_barter_screen();
        for (std::size_t index = 0; index < objects.size(); ++index)
        {
            objects[index]->SetRootSet();
            npc_barter_backups[index] = objects[index];
        }
        npc_barter_screen = screen;
        return respond(true);
    }

    int take_npc_barter_confirm(const Lua& lua) { return take_interaction_press(lua, npc_barter_confirm); }

    int open_sewer_dialogue(const Lua& lua)
    {
        auto* pawn = scope.pawn.Get();
        lua.set_bool(available() && lua.get_stack_size() == 1 && lua.is_integer(1) && pawn &&
            address(pawn) == lua.get_integer() && queue_sewer_dialogue(pawn));
        return 1;
    }

    int take_dialogue_press(const Lua& lua) { return take_interaction_press(lua, dialogue_input); }
    int take_valve_press(const Lua& lua) { return take_interaction_press(lua, valve_input); }
    int take_lantern_press(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = lua.get_integer();
        for (auto* input : {&lantern_ignition, &lantern_input})
        {
            auto* actor = input->actor.Get();
            if (!input->pending.sequence || !stock_lantern(actor)) continue;
            if (const auto accepted = take_interaction_sequence(address, *input))
            {
                lua.set_integer(static_cast<std::int64_t>(accepted));
                lua.set_string(RC::to_string(actor->GetFullName()));
                lua.set_bool(input == &lantern_ignition);
                return 3;
            }
        }
        return 0;
    }

    Unreal::UObject* scoped_switch_actor(Address world_address, Address expected, std::string_view path)
    {
        auto* world = scope.world.Get();
        if (expected <= 0 || !live(world) || address(world) != world_address ||
            scope.generation != generation.load(std::memory_order_acquire) || Clock::now() >= scope.expires)
            return nullptr;

        struct SwitchReader
        {
            Unreal::FWeakObjectPtr actor, level;
            std::string path;
            bool streamed;
        };
        static Unreal::FWeakObjectPtr last_world;
        static std::uint64_t last_generation{};
        static std::unordered_map<Address, SwitchReader> switches;
        if (last_world.Get() != world || last_generation != scope.generation)
        {
            switches.clear();
            last_world = world;
            last_generation = scope.generation;
        }
        auto entry = switches.find(expected);
        auto* actor = entry != switches.end() ? entry->second.actor.Get() : nullptr;
        if (actor && entry->second.path != path) return nullptr;
        if (!actor)
        {
            if (path.empty() || path.size() > 384 || path.find('\0') != std::string_view::npos) return nullptr;
            actor = find<Unreal::AActor>(RC::to_wstring(path).c_str());
            if (address(actor) != expected || !live(actor)) return nullptr;
            auto* actor_class = find<Unreal::UClass>(STR("/Script/Engine.Actor"));
            auto* level = actor->GetOuterPrivate();
            if (!actor_class || !actor->IsA(actor_class) || !live(level)) return nullptr;
            const bool streamed = level != object_field(world, STR("PersistentLevel"));
            if (streamed && !silverfish::native_zone::visible_actor_level(static_cast<Unreal::AActor*>(actor),
                                                                        static_cast<Unreal::UWorld*>(world)))
                return nullptr;
            entry = switches.insert_or_assign(expected, SwitchReader{actor, level, std::string{path}, streamed}).first;
        }
        auto* level = entry->second.level.Get();
        if (!live(actor) || !live(level) || actor->GetOuterPrivate() != level) return nullptr;
        if (entry->second.streamed)
        {
            // Enrollment resolves the exact loaded streaming level. On steady reads check that
            // it is still owned by this world and visible, and requalify it after a hide.
            auto* visible = field<Unreal::FBoolProperty>(level, STR("bIsVisible"));
            if (object_field(level, STR("OwningWorld")) != world ||
                !visible || !visible->GetPropertyValueInContainer(level))
            {
                switches.erase(entry);
                return nullptr;
            }
        }
        if (scope.host) return live_actor_role(actor, 3) ? actor : nullptr;
        auto* destroyed = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return destroyed && !destroyed->GetPropertyValueInContainer(actor) ? actor : nullptr;
    }

    int read_switch_power(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        const auto count = lua_gettop(stack);
        if (!available() || (count != 5 && count != 6) || !lua_isinteger(stack, 1) || !lua_isinteger(stack, 2) ||
            lua_type(stack, 3) != LUA_TSTRING || lua_type(stack, 4) != LUA_TSTRING || !lua_isboolean(stack, 5) ||
            (count == 6 && !lua_isboolean(stack, 6))) return 0;
        std::size_t path_size{}, power_size{};
        const auto* path_text = lua_tolstring(stack, 3, &path_size);
        const auto* power_text = lua_tolstring(stack, 4, &power_size);
        const std::string_view power{power_text, power_size};
        const bool lantern = lua_toboolean(stack, 5);
        const bool candle_overlap = count == 6 && lua_toboolean(stack, 6);
        const auto* property_name = power == "Lit" ? STR("Lit") :
            power == "Powered" ? STR("Powered") : power == "Power" ? STR("Power") : nullptr;
        if (!property_name || (candle_overlap && (scope.host || power != "Lit"))) return 0;
        auto* actor = scoped_switch_actor(lua_tointeger(stack, 1), lua_tointeger(stack, 2), {path_text, path_size});
        if (!actor) return 0;
        auto* power_field = field<Unreal::FBoolProperty>(actor, property_name);
        auto* broken = lantern ? field<Unreal::FBoolProperty>(actor, STR("Broken_0")) : nullptr;
        if (!power_field || (lantern && !broken)) return 0;
        lua_pushboolean(stack, power_field->GetPropertyValueInContainer(actor));
        if (candle_overlap)
        {
            auto* member = field<Unreal::FObjectProperty>(actor, STR("Sphere"));
            auto* sphere = member ? member->GetObjectPropertyValue(member->ContainerPtrToValuePtr<void>(actor)) : nullptr;
            auto* type = member ? member->GetPropertyClass().Get() : nullptr;
            auto* overlaps = live(sphere) && sphere->GetOuterPrivate() == actor && type && sphere->IsA(type)
                ? field<Unreal::FBoolProperty>(sphere, STR("bGenerateOverlapEvents")) : nullptr;
            if (broken) lua_pushboolean(stack, broken->GetPropertyValueInContainer(actor));
            else lua_pushnil(stack);
            if (!overlaps) return 2;
            lua_pushboolean(stack, overlaps->GetPropertyValueInContainer(sphere));
            return 3;
        }
        if (broken)
        {
            lua_pushboolean(stack, broken->GetPropertyValueInContainer(actor));
            return 2;
        }
        return 1;
    }

    int read_door_latch(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (!available() || lua_gettop(stack) != 5 || !lua_isinteger(stack, 1) || !lua_isinteger(stack, 2) ||
            lua_type(stack, 3) != LUA_TSTRING || !lua_isinteger(stack, 4) || !lua_isinteger(stack, 5)) return 0;
        std::size_t path_size{};
        const auto* path_text = lua_tolstring(stack, 3, &path_size);
        auto* actor = scoped_switch_actor(lua_tointeger(stack, 1), lua_tointeger(stack, 2), {path_text, path_size});
        if (!actor) return 0;

        static Unreal::FWeakObjectPtr latch_class, door_class, rotator_type;
        if (!latch_class.Get()) latch_class = find<Unreal::UClass>(door_latch_class);
        if (!door_class.Get()) door_class = find<Unreal::UClass>(linked_door_class);
        if (!rotator_type.Get()) rotator_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Rotator"));
        auto* latch_type = static_cast<Unreal::UClass*>(latch_class.Get());
        auto* door_type = static_cast<Unreal::UClass*>(door_class.Get());
        if (!latch_type || !door_type || !actor->IsA(latch_type)) return 0;
        auto* door = object_field(actor, STR("Door"));
        auto* dial = object_field(actor, STR("SM_Prop_Kitchen_Grill_01_Dial_01"));
        if (!live(door) || address(door) != lua_tointeger(stack, 4) || !door->IsA(door_type) ||
            !live(dial) || address(dial) != lua_tointeger(stack, 5) || dial->GetOuterPrivate() != actor) return 0;
        auto* destroying = field<Unreal::FBoolProperty>(door, STR("bActorIsBeingDestroyed"));
        auto* locked = field<Unreal::FBoolProperty>(door, STR("Locked"));
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        if (!destroying || destroying->GetPropertyValueInContainer(door) || !locked ||
            !silverfish::native_zone::visible_actor_level(static_cast<Unreal::AActor*>(door), world)) return 0;

        auto* rotation = field<Unreal::FStructProperty>(dial, STR("RelativeRotation"));
        auto* type = rotation ? rotation->GetStruct().Get() : nullptr;
        if (!type || type != rotator_type.Get()) return 0;
        auto* data = rotation->ContainerPtrToValuePtr<void>(dial);
        std::array<double, 3> values{};
        constexpr std::array axes{STR("Pitch"), STR("Yaw"), STR("Roll")};
        for (std::size_t index = 0; index < axes.size(); ++index)
        {
            auto* axis = field<Unreal::FDoubleProperty>(type, axes[index]);
            if (!axis || axis->GetOffset_Internal() < 0 ||
                axis->GetOffset_Internal() + static_cast<int>(sizeof(double)) > rotation->GetSize()) return 0;
            values[index] = axis->GetPropertyValueInContainer(data);
            if (!std::isfinite(values[index])) return 0;
        }
        lua_pushboolean(stack, locked->GetPropertyValueInContainer(door));
        for (auto axis : values) lua_pushnumber(stack, axis);
        lua.set_string(RC::to_string(door->GetFullName()));
        return 5;
    }

    int take_stash_press(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer(1);
        if (requested <= 0 || !silverfish::native_interaction_input::current_guest_pawn(requested)) return 0;
        stash_lease = Clock::now() + lease;
        if (!stash_input.pending.sequence) return 0;
        auto* actor = stash_input.actor.Get();
        if (!stock_stash(actor)) return 0;
        const auto accepted = take_interaction_sequence(requested, stash_input);
        if (!accepted) return 0;
        lua.set_integer(static_cast<std::int64_t>(accepted));
        lua.set_string(RC::to_string(actor->GetPathName()));
        return 2;
    }

    int read_local_lean(const Lua& lua)
    {
        if (lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_bool(3)) return 0;
        const auto pawn_address = lua.get_integer();
        const auto world_address = lua.get_integer();
        const auto host = lua.get_bool();
        auto* pawn = silverfish::native_interaction_input::current_local_pawn(pawn_address);
        if (!pawn || scope.host != host || address(scope.world.Get()) != world_address) return 0;
        if (!standing(pawn))
        {
            lua.set_number(0);
            return 1;
        }

        auto* arrow = object_field(pawn, STR("LeanPivotArrow"));
        auto* capsule = object_field(pawn, STR("CapsuleComponent"));
        if (!live(arrow) || arrow->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.ArrowComponent")) ||
            arrow->GetOuterPrivate() != pawn || !live(capsule) || object_field(arrow, STR("AttachParent")) != capsule)
            return 0;
        auto* rotation = field<Unreal::FStructProperty>(arrow, STR("RelativeRotation"));
        auto* type = rotation ? rotation->GetStruct().Get() : nullptr;
        if (!type || type != find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Rotator"))) return 0;
        auto* roll = field<Unreal::FDoubleProperty>(type, STR("Roll"));
        if (!roll || roll->GetOffset_Internal() < 0 ||
            roll->GetOffset_Internal() + static_cast<int>(sizeof(double)) > rotation->GetSize()) return 0;
        const auto angle = roll->GetPropertyValueInContainer(rotation->ContainerPtrToValuePtr<void>(arrow));
        if (!std::isfinite(angle) || std::abs(angle) > 40) return 0;
        lua.set_number(angle);
        return 1;
    }

    int read_ready_roster(const Lua& lua)
    {
        if (!available() || !scope.host || lua.get_stack_size() != 4 || !lua.is_integer(1) ||
            !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_integer(4)) return 0;
        const auto expected_world = lua.get_integer();
        const auto expected_pawn = lua.get_integer();
        const auto expected_controller = lua.get_integer();
        const auto expected_count = lua.get_integer();
        auto* world = scope.world.Get();
        auto* host = scope.pawn.Get();
        auto* local_controller = scope.controller.Get();
        auto* driver = scope.driver.Get();
        if (address(world) != expected_world || address(host) != expected_pawn ||
            address(local_controller) != expected_controller || Clock::now() >= scope.expires || !live_scope(scope))
            return 0;

        struct Participant
        {
            Unreal::UObject* connection{};
            Unreal::UObject* controller{};
            Unreal::UObject* pawn{};
            std::string name;
        };
        std::array<Participant, 4> participants{};
        auto* connections = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* entry = connections ? Unreal::CastField<Unreal::FObjectProperty>(connections->GetInner()) : nullptr;
        if (!entry || expected_count < 0 || expected_count > static_cast<int>(participants.size())) return 0;
        Unreal::FScriptArrayHelper clients(connections, connections->ContainerPtrToValuePtr<void>(driver));
        if (clients.Num() != expected_count) return 0;

        const auto identity = [](Unreal::UObject* object) -> std::string {
            auto name = RC::to_string(object->GetFullName());
            if (name.empty() || name.size() > 512 || name.contains("Default__") ||
                std::ranges::any_of(name, [](unsigned char ch) { return ch < 32 || ch == 127; })) return {};
            return name;
        };
        auto driver_name = identity(driver);
        if (driver_name.empty()) return 0;
        auto* connection_type = expected_count ? find<Unreal::UClass>(STR("/Script/Engine.NetConnection")) : nullptr;
        for (int index = 0; index < clients.Num(); ++index)
        {
            auto* connection = entry->GetObjectPropertyValue(clients.GetRawPtr(index));
            if (!connection_type || !live(connection) || !connection->IsA(connection_type) ||
                object_field(connection, STR("Driver")) != driver) return 0;
            auto* controller = object_field(connection, STR("PlayerController"));
            if (!live(controller) || controller == local_controller ||
                controller->GetClassPrivate() != local_controller->GetClassPrivate() || !live_actor(controller, world, 3) ||
                object_field(controller, STR("Player")) != connection ||
                object_field(connection, STR("OwningActor")) != controller) return 0;
            auto* controller_connection = object_field(controller, STR("NetConnection"));
            if (live(controller_connection) && controller_connection != connection) return 0;
            auto* pawn = object_field(controller, STR("Pawn"));
            if (!live(pawn) || pawn == host || pawn->GetClassPrivate() != host->GetClassPrivate() ||
                !live_actor(pawn, world, 3) || object_field(pawn, STR("Controller")) != controller) return 0;

            auto name = identity(connection);
            if (name.empty() || std::any_of(participants.begin(), participants.begin() + index, [&](const auto& prior) {
                    return prior.connection == connection || prior.name == name || prior.controller == controller ||
                        prior.pawn == pawn;
                })) return 0;
            participants[index] = {connection, controller, pawn, std::move(name)};
        }

        auto* stack = lua.get_lua_state();
        lua.set_integer(address(driver));
        lua.set_string(driver_name);
        lua_createtable(stack, clients.Num(), 0);
        for (int index = 0; index < clients.Num(); ++index)
        {
            const auto& participant = participants[index];
            lua_createtable(stack, 4, 0);
            lua.set_integer(address(participant.connection));
            lua_rawseti(stack, -2, 1);
            lua.set_string(participant.name);
            lua_rawseti(stack, -2, 2);
            lua.set_integer(address(participant.controller));
            lua_rawseti(stack, -2, 3);
            lua.set_integer(address(participant.pawn));
            lua_rawseti(stack, -2, 4);
            lua_rawseti(stack, -2, index + 1);
        }
        return 3;
    }

    Unreal::UObject* remote_avatar_pawn(std::int64_t world_address, std::int64_t pawn_address)
    {
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        auto* local = scope.pawn.Get();
        auto* driver = scope.driver.Get();
        const auto now = Clock::now();
        if (!live(world) || address(world) != world_address || !live(local) || !live(driver) ||
            pawn_address <= 0 || address(local) == pawn_address ||
            now >= scope.expires || scope.generation != generation.load(std::memory_order_acquire) ||
            object_field(world, STR("NetDriver")) != driver || object_field(driver, STR("World")) != world)
            return nullptr;

        struct Census
        {
            Unreal::FWeakObjectPtr world, local, driver;
            std::uint64_t generation{};
            std::array<Unreal::FWeakObjectPtr, 5> remotes{};
            Clock::time_point next_scan{};
        };
        static Census census;
        if (census.world.Get() != world || census.local.Get() != local ||
            census.driver.Get() != driver || census.generation != scope.generation)
        {
            census = {};
            census.world = world;
            census.local = local;
            census.driver = driver;
            census.generation = scope.generation;
        }
        const auto remote = [&]() -> Unreal::UObject* {
            for (const auto& item : census.remotes)
                if (auto* pawn = item.Get(); address(pawn) == pawn_address &&
                    live_actor(pawn, world, scope.host ? 3 : 1)) return pawn;
            return nullptr;
        };
        auto* pawn = remote();
        if (!pawn && now >= census.next_scan)
        {
            census.next_scan = now + std::chrono::milliseconds(250);
            census.remotes = {};
            auto* type = find<Unreal::UClass>(pawn_path);
            if (!type) return nullptr;
            std::vector<Unreal::AActor*> actors;
            Unreal::UGameplayStatics::GetAllActorsOfClass(world, type, actors);
            std::size_t count = 0;
            for (auto* actor : actors)
            {
                if (count == census.remotes.size()) break;
                if (actor != local && live(actor) && actor->GetClassPrivate() == type &&
                    live_actor(actor, world, scope.host ? 3 : 1))
                    census.remotes[count++] = actor;
            }
            pawn = remote();
        }
        return pawn;
    }

    using AimRotation = std::array<double, 3>;

    bool aim_rotator(Unreal::FStructProperty* property)
    {
        auto* type = property ? property->GetStruct().Get() : nullptr;
        if (!type || type != find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Rotator")) ||
            property->GetSize() != sizeof(AimRotation)) return false;
        constexpr std::array axes{STR("Pitch"), STR("Yaw"), STR("Roll")};
        for (std::size_t index = 0; index < axes.size(); ++index)
        {
            auto* axis = field<Unreal::FDoubleProperty>(type, axes[index]);
            if (!axis || axis->GetOffset_Internal() != static_cast<int>(index * sizeof(double))) return false;
        }
        return true;
    }

    int read_remote_aim(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2)) return 0;
        const auto world_address = lua.get_integer();
        const auto pawn_address = lua.get_integer();
        auto* pawn = remote_avatar_pawn(world_address, pawn_address);
        if (!pawn) return 0;
        static Unreal::FWeakObjectPtr cached_aim;
        auto* aim = static_cast<Unreal::UFunction*>(cached_aim.Get());
        if (!aim)
        {
            aim = find<Unreal::UFunction>(STR("/Script/Engine.Pawn:GetBaseAimRotation"));
            auto* result = aim ? Unreal::CastField<Unreal::FStructProperty>(aim->GetReturnProperty()) : nullptr;
            if (!aim_rotator(result) || result->GetArrayDim() != 1 || result->GetOffset_Internal() != 0 ||
                !aim->HasAnyFunctionFlags(Unreal::FUNC_Native) || aim->GetNumParms() != 1 ||
                aim->GetParmsSize() != sizeof(AimRotation) ||
                aim->GetOuterPrivate() != find<Unreal::UClass>(STR("/Script/Engine.Pawn"))) return 0;
            cached_aim = aim;
        }
        AimRotation rotation{};
        pawn->ProcessEvent(aim, rotation.data());
        if (!std::isfinite(rotation[0]) || !std::isfinite(rotation[1])) return 0;
        lua.set_number(rotation[0]);
        lua.set_number(rotation[1]);
        return 2;
    }

    struct HeadRotationWriter
    {
        static constexpr int parameter_capacity = 512, parameter_alignment = 16;
        Unreal::FWeakObjectPtr setter{}, scene{}, skeletal_mesh{};
        Unreal::FStructProperty *rotation{}, *hit{};
        Unreal::FBoolProperty *sweep{}, *teleport{}, *absolute{}, *replicated{};
        Unreal::FObjectProperty* parent{};
        Unreal::FNameProperty* socket{};
        Unreal::FName head{};

        void bind()
        {
            auto* scene_class = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
            auto* mesh_class = find<Unreal::UClass>(STR("/Script/Engine.SkeletalMeshComponent"));
            auto* function = find<Unreal::UFunction>(STR("/Script/Engine.SceneComponent:K2_SetWorldRotation"));
            if (!scene_class || !mesh_class || !function || function->GetOuterPrivate() != scene_class ||
                !function->HasAnyFunctionFlags(Unreal::FUNC_Native) || function->GetNumParms() != 4 ||
                function->GetReturnProperty() || function->GetParmsSize() > parameter_capacity) return;

            rotation = field<Unreal::FStructProperty>(function, STR("NewRotation"));
            sweep = field<Unreal::FBoolProperty>(function, STR("bSweep"));
            teleport = field<Unreal::FBoolProperty>(function, STR("bTeleport"));
            hit = field<Unreal::FStructProperty>(function, STR("SweepHitResult"));
            auto* hit_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.HitResult"));
            if (!aim_rotator(rotation) || !sweep || !teleport || !hit || !hit_type ||
                hit->GetStruct().Get() != hit_type || !sweep->IsNativeBool() || !teleport->IsNativeBool() ||
                sweep->GetByteOffset() >= sweep->GetSize() || teleport->GetByteOffset() >= teleport->GetSize()) return;

            const std::array<Unreal::FProperty*, 4> parameters{rotation, hit, sweep, teleport};
            const std::array<int, 4> alignments{rotation->GetStruct().Get()->GetMinAlignment(), hit_type->GetMinAlignment(),
                                                alignof(bool), alignof(bool)};
            for (std::size_t index = 0; index < parameters.size(); ++index)
            {
                auto* parameter = parameters[index];
                const int offset = parameter->GetOffset_Internal(), size = parameter->GetSize();
                const int alignment = alignments[index];
                if (!parameter->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
                    parameter->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) ||
                    parameter->HasAnyPropertyFlags(Unreal::CPF_OutParm) != (parameter == hit) ||
                    alignment <= 0 || alignment > parameter_alignment || (alignment & (alignment - 1)) ||
                    offset < 0 || offset % alignment || size <= 0 || size > function->GetParmsSize() - offset) return;
                for (std::size_t prior = 0; prior < index; ++prior)
                {
                    const int earlier = parameters[prior]->GetOffset_Internal();
                    if (offset < earlier + parameters[prior]->GetSize() && earlier < offset + size) return;
                }
            }

            parent = field<Unreal::FObjectProperty>(scene_class, STR("AttachParent"));
            socket = field<Unreal::FNameProperty>(scene_class, STR("AttachSocketName"));
            absolute = field<Unreal::FBoolProperty>(scene_class, STR("bAbsoluteRotation"));
            replicated = field<Unreal::FBoolProperty>(scene_class, STR("bReplicates"));
            if (!parent || parent->GetPropertyClass().Get() != scene_class || !socket || !absolute || !replicated)
                return;
            head = Unreal::FName{STR("Head"), Unreal::FNAME_Find};
            if (head == Unreal::FName{}) return;

            setter = function;
            scene = scene_class;
            skeletal_mesh = mesh_class;
        }

        Unreal::UObject* attachment(Unreal::UObject* component) const
        {
            return parent->GetObjectPropertyValue(parent->ContainerPtrToValuePtr<void>(component));
        }

        bool attached_head(Unreal::UObject* pawn, Unreal::UObject* pivot) const
        {
            if (!live(pivot) || pivot->GetClassPrivate() != scene.Get() || pivot->GetOuterPrivate() != pawn ||
                socket->GetPropertyValueInContainer(pivot) != head || !absolute->GetPropertyValueInContainer(pivot) ||
                replicated->GetPropertyValueInContainer(pivot)) return false;

            auto* body = attachment(pivot);
            auto* stock = object_field(pawn, STR("Mesh"));
            return live(body) && body->GetClassPrivate() == skeletal_mesh.Get() && body->GetOuterPrivate() == pawn &&
                !replicated->GetPropertyValueInContainer(body) && live(stock) && stock->GetOuterPrivate() == pawn &&
                body != stock && attachment(body) == stock;
        }

        void apply(Unreal::UObject* pivot, const AimRotation& angles) const
        {
            alignas(parameter_alignment) std::array<std::byte, parameter_capacity> params{};
            hit->InitializeValue_InContainer(params.data());
            std::memcpy(rotation->ContainerPtrToValuePtr<void>(params.data()), angles.data(), sizeof(angles));
            sweep->SetPropertyValueInContainer(params.data(), false);
            teleport->SetPropertyValueInContainer(params.data(), true);
            pivot->ProcessEvent(static_cast<Unreal::UFunction*>(setter.Get()), params.data());
            hit->DestroyValue_InContainer(params.data());
        }
    };

    int write_remote_head_rotation(const Lua& lua)
    {
        constexpr std::size_t max_path_size = 512;
        if (!available() || lua.get_stack_size() != 6 || !lua.is_integer(1) || !lua.is_integer(2) ||
            lua_type(lua.get_lua_state(), 3) != LUA_TSTRING || !lua.is_number(4) ||
            !lua.is_number(5) || !lua.is_number(6)) return 0;

        auto* stack = lua.get_lua_state();
        const auto world_address = lua_tointeger(stack, 1);
        const auto pawn_address = lua_tointeger(stack, 2);
        std::size_t path_size{};
        const auto* path_chars = lua_tolstring(stack, 3, &path_size);
        const std::string_view path{path_chars, path_size};
        const AimRotation angles{lua_tonumber(stack, 4), lua_tonumber(stack, 5), lua_tonumber(stack, 6)};
        if (path.empty() || path.size() > max_path_size || path.find('\0') != std::string_view::npos ||
            std::any_of(angles.begin(), angles.end(), [](double angle) {
                return !std::isfinite(angle) || std::abs(angle) > 360;
            })) return 0;

        auto* pawn = remote_avatar_pawn(world_address, pawn_address);
        if (!pawn) return 0;

        struct Binding
        {
            Unreal::FWeakObjectPtr pawn{}, pivot{};
            std::string path;
            Clock::time_point next_lookup{};
        };
        static HeadRotationWriter writer;
        static std::array<Binding, 5> bindings;
        static std::uint64_t binding_generation{};
        if (binding_generation != scope.generation)
        {
            writer = {};
            writer.bind();
            bindings = {};
            binding_generation = scope.generation;
        }
        if (!writer.setter.Get() || !writer.scene.Get() || !writer.skeletal_mesh.Get()) return 0;
        Binding* binding = nullptr;
        for (auto& candidate : bindings)
        {
            if (candidate.pawn.Get() == pawn)
            {
                binding = &candidate;
                break;
            }
            if (!binding && !candidate.pawn.Get()) binding = &candidate;
        }
        if (!binding) return 0;

        auto* pivot = binding->path == path ? binding->pivot.Get() : nullptr;
        if (!pivot)
        {
            const auto now = Clock::now();
            if (now < binding->next_lookup) return 0;
            binding->pawn = pawn;
            binding->next_lookup = now + std::chrono::milliseconds(250);
            const auto outer_path = RC::to_string(pawn->GetPathName());
            if (!path.starts_with(outer_path) || path.size() <= outer_path.size() || path[outer_path.size()] != '.')
                return 0;
            pivot = find<Unreal::UObject>(RC::to_wstring(path).c_str());
            if (!writer.attached_head(pawn, pivot)) return 0;
            *binding = Binding{pawn, pivot, std::string(path), {}};
        }
        else if (!writer.attached_head(pawn, pivot)) return 0;

        writer.apply(pivot, angles);
        lua.set_bool(true);
        return 1;
    }

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
        if (Unreal::IsInGameThreadRaw())
        {
            dialogue_input = {};
            sewer_trade = {};
            castaway_menu = {};
            sewer_answering = false;
        }
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
        std::uint8_t action_value_type{};

        bool bind(Unreal::UObject* action, Unreal::UScriptStruct* value_type, std::uint8_t maximum_value_type = 0)
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
            if (!enum_type || !action_value || action_value->GetEnum().Get() != enum_type ||
                !number || number->GetSize() != 1) return false;
            const auto action_type = number->GetUnsignedIntPropertyValue(
                action_value->ContainerPtrToValuePtr<void>(action));
            if (action_type > maximum_value_type) return false;
            action_value_type = static_cast<std::uint8_t>(action_type);
            return x && input<Unreal::FDoubleProperty>(function, STR("Y"), 8) &&
                input<Unreal::FDoubleProperty>(function, STR("Z"), 0x10) &&
                value && value->GetEnum().Get() == enum_type && value->GetSize() == 1 &&
                output && output == function->GetReturnProperty() && output->GetOffset_Internal() == 0x20 &&
                output->GetSize() == 0x20 && output->GetStruct().Get() == value_type;
        }

        void make_value(void* parameters, double axis) const
        {
            x->SetPropertyValueInContainer(parameters, axis);
            static_cast<std::byte*>(parameters)[0x18] = std::byte{action_value_type};
            library->ProcessEvent(function, parameters);
        }

        void dispatch(Unreal::UObject* pawn, Unreal::UObject* action, const Wrapper& wrapper, bool pressed) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x40> factory{};
            alignas(std::max_align_t) std::array<std::byte, 0x30> parameters{};
            StructParameter result(output, factory.data());
            StructParameter value(wrapper.value, parameters.data());
            make_value(factory.data(), pressed ? 1.0 : 0.0);
            wrapper.value->CopyCompleteValue(wrapper.value->ContainerPtrToValuePtr<void>(parameters.data()),
                output->ContainerPtrToValuePtr<void>(factory.data()));
            wrapper.elapsed->SetPropertyValueInContainer(parameters.data(), pressed ? 0.0f : 0.05f);
            wrapper.triggered->SetPropertyValueInContainer(parameters.data(), pressed ? 0.0f : 0.05f);
            wrapper.source->SetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(parameters.data()), action);
            pawn->ProcessEvent(static_cast<Unreal::UFunction*>(wrapper.function.Get()), parameters.data());
        }
    };

    bool lean_fixture_enabled()
    {
        return env_flag("SILVERFISH_COOP_HEADLESS") && env_flag("SILVERFISH_COOP_TITLE_FIXTURES");
    }

    Unreal::UObject* lean_player_subsystem()
    {
        auto* type = find<Unreal::UClass>(STR("/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem"));
        auto* library = find<Unreal::UObject>(STR("/Script/Engine.Default__SubsystemBlueprintLibrary"));
        auto* function = find<Unreal::UFunction>(
            STR("/Script/Engine.SubsystemBlueprintLibrary:GetLocalPlayerSubSystemFromPlayerController"));
        auto* player = input<Unreal::FObjectProperty>(function, STR("PlayerController"), 0);
        auto* cls = input<Unreal::FClassProperty>(function, STR("Class"), sizeof(void*));
        auto* output = function ? Unreal::CastField<Unreal::FObjectProperty>(function->GetReturnProperty()) : nullptr;
        if (!type || !library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            library->IsUnreachable() || !function || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 3 ||
            function->GetParmsSize() != 3 * sizeof(void*) || !player || !cls || !output ||
            output->GetOffset_Internal() != 2 * sizeof(void*)) return nullptr;
        std::array<Unreal::UObject*, 3> parameters{scope.controller.Get(), type};
        library->ProcessEvent(function, parameters.data());
        auto* subsystem = parameters[2];
        return live(subsystem) && subsystem->GetClassPrivate() == type && subsystem->GetOuterPrivate() == scope.player.Get()
            ? subsystem : nullptr;
    }

    bool arm_lean_trace(Unreal::UObject* pawn, Unreal::UObject* action, bool left)
    {
        static constexpr auto left_callbacks = std::to_array({
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Left_K2Node_EnhancedInputActionEvent_3"),
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Left_K2Node_EnhancedInputActionEvent_4"),
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Left_K2Node_EnhancedInputActionEvent_5"),
        });
        static constexpr auto right_callbacks = std::to_array({
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Right_K2Node_EnhancedInputActionEvent_10"),
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Right_K2Node_EnhancedInputActionEvent_11"),
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Lean_Right_K2Node_EnhancedInputActionEvent_12"),
        });
        static constexpr std::array events{2, 8, 16}; // Started, Canceled, Completed
        LeanDispatchTrace trace{Unreal::FWeakObjectPtr{pawn}, Unreal::FWeakObjectPtr{action}};
        const auto& paths = left ? left_callbacks : right_callbacks;
        for (std::size_t i = 0; i < trace.callbacks.size(); ++i)
        {
            trace.callbacks[i] = bind_wrapper(pawn->GetClassPrivate(), action, paths[i], events[i]);
            if (!trace.callbacks[i].function.Get()) return false;
        }
        trace.active = true;
        lean_trace = trace;
        return true;
    }

    int read_lean_trace(const Lua& lua)
    {
        if (!available() || !lean_fixture_enabled() || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_bool(2)) return 0;
        auto* pawn = lean_trace.pawn.Get();
        if (!pawn || pawn != scope.pawn.Get() || address(pawn) != lua.get_integer()) return 0;
        std::string result;
        auto append = [&](const std::array<unsigned, 3>& counts) {
            for (const auto count : counts)
            {
                if (!result.empty()) result += ',';
                result += std::to_string(count);
            }
        };
        for (const auto& calls : lean_trace.calls) append(calls);
        append(lean_trace.prevented);
        if (lua.get_bool(2)) lean_trace = {};
        lua.set_string(result);
        return 1;
    }

    int probe_continuous_lean(const Lua& lua)
    {
        if (!available() || !lean_fixture_enabled() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_string(2) || !lua.is_bool(3))
            return report(lua, false, "lean_fixture_unavailable");
        const auto requested = lua.get_integer();
        const auto side = lua.get_string();
        const bool pressed = lua.get_bool();
        auto* pawn = scope.pawn.Get();
        if (!pawn || address(pawn) != requested || Clock::now() >= scope.expires || !live_scope(scope) ||
            (pressed && !standing(pawn)) || (side != "left" && side != "right"))
            return report(lua, false, "lean_fixture_exact_local_pawn_required");
        auto* subsystem = lean_player_subsystem();
        if (!subsystem) return report(lua, false, "lean_fixture_local_subsystem_unavailable");
        auto* action = find<Unreal::UObject>(side == "left"
            ? STR("/Game/Classes/Player/Player_Inputs/IA_Lean_Left.IA_Lean_Left")
            : STR("/Game/Classes/Player/Player_Inputs/IA_Lean_Right.IA_Lean_Right"));
        auto* function = find<Unreal::UFunction>(pressed
            ? STR("/Script/EnhancedInput.EnhancedInputSubsystemInterface:StartContinuousInputInjectionForAction")
            : STR("/Script/EnhancedInput.EnhancedInputSubsystemInterface:StopContinuousInputInjectionForAction"));
        auto* source = input<Unreal::FObjectProperty>(function, STR("Action"), 0);
        if (!live(action) || !function || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            !source || source->GetPropertyClass().Get() != action->GetClassPrivate() || function->GetReturnProperty())
            return report(lua, false, "lean_fixture_injection_unavailable");
        if (!pressed)
        {
            if (function->GetParmsSize() != sizeof(void*) || function->GetNumParms() != 1)
                return report(lua, false, "lean_fixture_stop_schema_unavailable");
            subsystem->ProcessEvent(function, &action);
            return report(lua, true, "stopped");
        }

        auto* raw = field<Unreal::FStructProperty>(function, STR("RawValue"));
        auto* modifiers = field<Unreal::FArrayProperty>(function, STR("Modifiers"));
        auto* triggers = field<Unreal::FArrayProperty>(function, STR("Triggers"));
        ValueFactory factory;
        if (function->GetNumParms() != 4 || function->GetParmsSize() != 0x48 || !raw ||
            !raw->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            raw->GetOffset_Internal() != 8 || raw->GetSize() != 0x20 ||
            !modifiers || modifiers->GetOffset_Internal() != 0x28 ||
            !triggers || triggers->GetOffset_Internal() != 0x38)
            return report(lua, false, "lean_fixture_start_schema_unavailable");
        // Build the scalar value with the action's reflected Boolean or Axis1D type.
        if (!factory.bind(action, raw->GetStruct().Get(), 1))
            return report(lua, false, "lean_fixture_value_factory_unavailable");
        alignas(std::max_align_t) std::array<std::byte, 0x40> generated{};
        alignas(std::max_align_t) std::array<std::byte, 0x48> arguments{};
        StructParameter made(factory.output, generated.data());
        StructParameter value(raw, arguments.data());
        factory.make_value(generated.data(), 1.0);
        raw->CopyCompleteValue(raw->ContainerPtrToValuePtr<void>(arguments.data()),
            factory.output->ContainerPtrToValuePtr<void>(generated.data()));
        source->SetObjectPropertyValue(arguments.data(), action);
        if (!arm_lean_trace(pawn, action, side == "left"))
            return report(lua, false, "lean_fixture_stock_callbacks_unavailable");
        subsystem->ProcessEvent(function, arguments.data());
        return report(lua, true, "started");
    }

    int probe_radio_playback(const Lua& lua)
    {
        if (!available() || !env_flag("SILVERFISH_COOP_HEADLESS") || !env_flag("SILVERFISH_COOP_TITLE_FIXTURES") ||
            lua.get_stack_size() != 1 || !lua.is_string(1) || Clock::now() >= scope.expires || !live_scope(scope)) return 0;
        const auto path = lua.get_string();
        if (path.size() > 512 || !path.starts_with("/Game/Classes/Maps/") ||
            path.find(":PersistentLevel.") == std::string_view::npos) return 0;

        auto* world = scope.world.Get();
        auto* actor = find<Unreal::UObject>(RC::to_wstring(path).c_str());
        if (!live(actor) || actor->GetClassPrivate() != find<Unreal::UClass>(radio_class) ||
            actor->GetOuterPrivate() != object_field(world, STR("PersistentLevel")) ||
            (!live_actor(actor, world, 3) && (scope.host || !live_actor(actor, world, 1)))) return 0;

        auto* media_class = find<Unreal::UClass>(STR("/Script/MediaAssets.MediaPlayer"));
        auto* media = object_field(actor, STR("MediaPlayer"));
        auto* query = find<Unreal::UFunction>(STR("/Script/MediaAssets.MediaPlayer:IsPlaying"));
        auto* playing = query ? Unreal::CastField<Unreal::FBoolProperty>(query->GetReturnProperty()) : nullptr;
        if (!live(media) || media->GetOuterPrivate() != actor || media->GetClassPrivate() != media_class ||
            !playing || query->GetOuterPrivate() != media_class || query->GetNumParms() != 1 ||
            query->GetParmsSize() != sizeof(bool) || playing->GetOffset_Internal() != 0) return 0;

        bool result{};
        media->ProcessEvent(query, &result);
        lua.set_bool(playing->GetPropertyValueInContainer(&result));
        auto* song = field<Unreal::FStrProperty>(actor, STR("CurrentSong"));
        if (!song) return 1;
        const auto name = song->GetPropertyValueInContainer(actor);
        lua.set_string(name.Len() > 0 && name.Len() < 256 ? RC::to_string(*name) : "");
        return 2;
    }

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

    Unreal::UWorld* current_listen_world()
    {
        if (!available() || Clock::now() >= scope.expires || !live_scope(scope)) return nullptr;
        return static_cast<Unreal::UWorld*>(scope.world.Get());
    }

    Unreal::UObject* current_local_pawn(std::uintptr_t requested)
    {
        if (!available() || Clock::now() >= scope.expires || !live_scope(scope)) return nullptr;
        auto* pawn = scope.pawn.Get();
        return pawn && reinterpret_cast<std::uintptr_t>(pawn) == requested ? pawn : nullptr;
    }

    Unreal::UObject* current_guest_pawn(std::uintptr_t requested)
    {
        auto* pawn = current_local_pawn(requested);
        return pawn && !scope.host ? pawn : nullptr;
    }

    bool local_interaction_in_reach(Unreal::UObject* pawn, Unreal::UObject* actor, Unreal::UObject* component)
    {
        return pawn && current_local_pawn(reinterpret_cast<std::uintptr_t>(pawn)) == pawn && standing(pawn) &&
            actor && component && interaction_in_reach(pawn, actor, component);
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
        const auto* grip_filter = std::getenv("SILVERFISH_COOP_PROP_GRIP_FILTER");
        if (!grip_filter || std::string_view{grip_filter} != "0")
        {
            lua.register_function("SilverfishSetNativePropCarryMovement", arm_prop_carry_movement);
            lua.register_function("SilverfishReleaseNativePropCarryMovement", release_prop_carry_movement);
            lua.register_function("SilverfishReadNativePropCarryMovement", prop_carry_movement_status);
        }
        lua.register_function("SilverfishSetNativeWireBoardScope", set_wire_board);
        lua.register_function("SilverfishTakeNativeWireBoardPress", take_wire_board_press);
        lua.register_function("SilverfishTakeNativeStockBoardAction", take_stock_board_action);
        lua.register_function("SilverfishRenderNativeStockBoard", render_stock_board);
        lua.register_function("SilverfishQualifyNativeStockBoardPolicy", qualify_stock_board_policy);
        lua.register_function("SilverfishClearNativeWireBoardScope", clear_wire_board);
        lua.register_function("SilverfishSetNativeDialogueInputScope", set_dialogue_scope);

        lua.register_function("SilverfishSetNativeSewerTradeScreen", set_sewer_trade_screen);
        lua.register_function("SilverfishTakeNativeSewerTradePress", take_sewer_trade_press);

        lua.register_function("SilverfishSetNativeNpcBarter", set_npc_barter);
        lua.register_function("SilverfishSetNativeNpcBarterScreen", set_npc_barter_screen);
        lua.register_function("SilverfishCaptureNativeNpcBarterMenu", capture_npc_barter_menu);
        lua.register_function("SilverfishTakeNativeNpcBarterConfirm", take_npc_barter_confirm);
        lua.register_function("SilverfishOpenNativeSewerDialogue", open_sewer_dialogue);
        lua.register_function("SilverfishTakeNativeDialogueInput", take_dialogue_press);
        lua.register_function("SilverfishClearNativeDialogueInputScope", clear_dialogue_scope);
        lua.register_function("SilverfishSetNativeCastawayDialogueScreen", set_castaway_screen);
        lua.register_function("SilverfishTakeNativeCastawayResponse", take_castaway_response);
        lua.register_function("SilverfishTakeNativeSubwayValvePress", take_valve_press);
        lua.register_function("SilverfishTakeNativeStashPress", take_stash_press);
        lua.register_function("SilverfishReadNativeLocalLean", read_local_lean);
        lua.register_function("SilverfishReadNativeRemoteAim", read_remote_aim);
        lua.register_function("SilverfishWriteNativeRemoteHeadRotation", write_remote_head_rotation);
        lua.register_function("SilverfishReadNativeReadyRoster", read_ready_roster);
        lua.register_function("SilverfishReadNativeSwitchPower", read_switch_power);
        lua.register_function("SilverfishReadNativeDoorLatch", read_door_latch);
        lua.register_function("SilverfishTakeNativeLanternPress", take_lantern_press);
        lua.register_function("SilverfishDispatchNativeSubwayValve", dispatch_valve);
        mosby_station_input::register_lua(lua);
        mindcore_input::register_lua(lua);
        if (lean_fixture_enabled())
        {
            lua.register_function("SilverfishProbeNativeRadioPlayback", probe_radio_playback);
            lua.register_function("SilverfishProbeContinuousLean", probe_continuous_lean);
            lua.register_function("SilverfishReadLeanDispatchTrace", read_lean_trace);
        }
        if (fixture_enabled()) lua.register_function("SilverfishProbeNativeInteractInput", probe);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        lean_trace = {};
        if (!Unreal::IsInGameThreadRaw()) return;
        scope = {};
        prop_movement = {};
        wire_board = {};
        stock_board_action = {};
        stock_board_menu = {};
        dialogue_input = {};
        sewer_answering = false;
        sewer_trade = {};
        castaway_menu = {};
        valve_input = {};
        stash_input = {};
        lantern_input = {};
        lantern_ignition = {};
        clear_npc_barter_screen();
        npc_barter_input = {};
        npc_barter_confirm = {};
        stash_lease = {};
        mosby_station_input::clear_scope();
        mindcore_input::clear_scope();
        first = count = event_depth = 0;
        event_focus = Unreal::FWeakObjectPtr{};
        event_source_matches = event_consumed = false;
    }
}
