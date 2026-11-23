#include "interaction_input.hpp"
#include "mosby_station_input.hpp"
#include "mindcore_input.hpp"
#include "npc_barter.hpp"

#include <array>
#include <atomic>
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
    constexpr auto lantern_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Prop_Lantern_01_C_1");
    constexpr auto lantern_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C");
    constexpr auto lantern_interaction = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:SendInteractionToActor");
    constexpr auto lantern_ignite = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:SetOnFire");
    constexpr auto lantern_extinguish = STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Candle_01.Prop_Candle_01_C:Extinguish");

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
    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    Scope scope;
    ScopedInteraction wire_board, dialogue_input, valve_input, stash_input, lantern_input;

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

    const DialogueProfile* dialogue_profile(Unreal::UObject* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        if (path == sewer_world) return &sewer_dialogue;
        for (const auto& profile : dialogue_profiles)
            if (path == profile.world) return &profile;
        return nullptr;
    }

    bool placed_wire_board(Unreal::UObject* world, Unreal::UObject* actor, Unreal::UObject* box)
    {
        if (!live(actor) || actor->GetClassPrivate() != find<Unreal::UClass>(wire_board_class) ||
            !actor->HasAnyFlags(Unreal::RF_WasLoaded)) return false;
        auto* placed = static_cast<Unreal::AActor*>(actor);
        auto* destroying = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return destroying && !destroying->GetPropertyValueInContainer(actor) &&
            placed->GetWorld() == world &&
            placed->GetLevel() == object_field(world, STR("PersistentLevel")) &&
            (!scope.host || live_actor(actor, world, 3)) &&
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
            function->GetOuterPrivate() != actor->GetClassPrivate() || function->GetReturnProperty() ||
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
            function->GetOuterPrivate() != actor->GetClassPrivate() ||
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
        auto* board = stock_menu_board(log);
        const bool projected = board && log == stock_board_menu.menu.Get() && board == stock_board_menu.board.Get();
        if (!board || widget->GetWorld() != world) return;
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
            if (key.IsNone() || text.empty() || text.size() > 96) return;
            for (const auto ch : text)
            {
                if (!((ch >= L'A' && ch <= L'Z') || (ch >= L'a' && ch <= L'z') ||
                      (ch >= L'0' && ch <= L'9') || ch == L'_')) return;
                id.push_back(static_cast<char>(ch));
            }
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

    void intercept_lantern(Iteration& call, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        static const Unreal::FName interact{STR("SendInteractionToActor")};
        static const Unreal::FName ignite{STR("SetOnFire")};
        static const Unreal::FName extinguish{STR("Extinguish")};
        if (scope.host || !actor || !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        if ((name != interact && name != ignite && name != extinguish) ||
            Clock::now() >= scope.expires || !live_scope(scope) ||
            actor->GetClassPrivate() != find<Unreal::UClass>(lantern_class) ||
            actor != find<Unreal::UObject>(lantern_path) || actor->GetPathName() != lantern_path ||
            !live_actor(actor, scope.world.Get(), 1)) return;
        if ((name == ignite && function == find<Unreal::UFunction>(lantern_ignite)) ||
            (name == extinguish && function == find<Unreal::UFunction>(lantern_extinguish)))
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
        const auto expires = Clock::now() + receipt_lifetime;
        lantern_input.actor = actor;
        lantern_input.component = component;
        lantern_input.pawn = pawn;
        lantern_input.accepting = true;
        lantern_input.expires = expires;
        lantern_input.pending = {++lantern_sequence, expires};
    }

    void intercept_npc_barter(Iteration&, Unreal::UObject*, Unreal::UFunction*);

    void route_interaction(Iteration& iteration, Unreal::UObject* actor, Unreal::UFunction* function, const void* parameters)
    {
        intercept_wire_board(iteration, actor, function, parameters);
        intercept_npc_barter(iteration, actor, function);
        intercept_stock_board_validation(iteration, actor, function);
        intercept_stock_board(iteration, actor, function);
        intercept_sewer_trade(iteration, actor, function, parameters);
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
            event_focus = event_source_matches ? pressed_focus(pawn) : Unreal::FWeakObjectPtr{};
            if (sewer_answering && event_source_matches && !iteration.OriginalFunctionCallPrevented() && queue_sewer_dialogue(pawn))
            {
                iteration.PreventOriginalFunctionCall();
                event_consumed = iteration.OriginalFunctionCallPrevented();
                if (event_consumed) ++consumed_dispatches;
            }
        }
    }

    void event_post(Iteration& iteration, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        if (!available() || !exact_dispatch(pawn, function) || !event_depth) return;
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
        auto* board = wire_board.actor.Get();
        if (!placed_wire_board(scope.world.Get(), board, wire_board.component.Get()) || !stock_board_policy(board)) return 0;
        const auto accepted = take_interaction_sequence(lua.get_integer(), wire_board);
        if (!accepted) return 0;
        lua.set_integer(static_cast<std::int64_t>(accepted));
        lua.set_string(RC::to_string(board->GetPathName()));
        return 2;
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
        if (function->GetFName() != confirm || scope.host || function != find<Unreal::UFunction>(npc_barter_complete) ||
            function->GetNumParms() != 0 || function->GetParmsSize() != 0 || function->GetReturnProperty() ||
            !stock_npc_barter_menu(object)) return;
        // Keep stock selection and confirmation controls. The guest's local
        // GameInstance must never run the exchange against its inventory copy.
        call.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        if (call.OriginalFunctionCallPrevented() && object == npc_barter_screen.Get() && now < npc_barter_input.expires &&
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
    int take_lantern_press(const Lua& lua) { return take_interaction_press(lua, lantern_input); }

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
        lua.register_function("SilverfishTakeNativeSubwayValvePress", take_valve_press);
        lua.register_function("SilverfishTakeNativeStashPress", take_stash_press);
        lua.register_function("SilverfishTakeNativeLanternPress", take_lantern_press);
        lua.register_function("SilverfishDispatchNativeSubwayValve", dispatch_valve);
        mosby_station_input::register_lua(lua);
        mindcore_input::register_lua(lua);
        if (fixture_enabled()) lua.register_function("SilverfishProbeNativeInteractInput", probe);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (!Unreal::IsInGameThreadRaw()) return;
        scope = {};
        wire_board = {};
        stock_board_action = {};
        stock_board_menu = {};
        dialogue_input = {};
        sewer_answering = false;
        sewer_trade = {};
        valve_input = {};
        stash_input = {};
        lantern_input = {};
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
