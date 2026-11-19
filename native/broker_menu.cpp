#include "broker_menu.hpp"

#include <array>
#include <atomic>
#include <chrono>
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
    using Address = std::intptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01");
    constexpr auto dialogue_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.Dialogue_Controler_BP_C_3");
    constexpr auto receiver_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.GlobalPing_ScriptingReciver_BP_C_5");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto dialogue_class = STR("/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C");
    constexpr auto receiver_class = STR("/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C");
    constexpr auto library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C");
    constexpr auto dialogue_widget = STR("/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C");
    constexpr auto ping = STR("BrokerQuest_02");
    constexpr auto quest = STR("Clt_DrillHead_Brkr");

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

    Unreal::UObject* object_field(Unreal::UObject* receiver, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(receiver, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(receiver)) : nullptr;
    }

    Unreal::UObject* sole_object(Unreal::UObject* receiver, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FArrayProperty>(receiver, name);
        auto* entry = property ? Unreal::CastField<Unreal::FObjectProperty>(property->GetInner()) : nullptr;
        if (!entry) return nullptr;
        Unreal::FScriptArrayHelper array(property, property->ContainerPtrToValuePtr<void>(receiver));
        return array.Num() == 1 ? entry->GetObjectPropertyValue(array.GetRawPtr(0)) : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject |
            Unreal::RF_ArchetypeObject | Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool authority(Unreal::UObject* actor, Unreal::UObject* world)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        auto* destroyed = field<Unreal::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 && destroyed &&
            !destroyed->GetPropertyValueInContainer(actor) && static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    bool env_flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env_flag("SILVERFISH_COOP_HEADLESS") && env_flag("SILVERFISH_COOP_NATIVE_FIXTURES");
    }

    struct State
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr world{}, dialogue{}, receiver{}, controller{}, pawn{}, instance{}, driver{}, player{}, save{};
        Unreal::UFunction *enter_menu{}, *enter_gameplay{}, *add_menu{}, *open_menu{}, *close_menu{}, *close_all{},
            *dialogue_script{}, *clear_ui{}, *turn_in{}, *send_ping{}, *global_ping{};
        Unreal::UClass* widget_type{};
        Unreal::FNameProperty* quest_parameter{};
        Unreal::FClassProperty* widget_parameter{};
        Unreal::FStrProperty* ping_parameter{};
        Unreal::FObjectProperty* sender_parameter{};
        int pings{}, menu_blocks{}, gameplay_blocks{};
    };

    State scope;
    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    Hook::GlobalCallbackId hook = Hook::ERROR_ID;

    bool qualified(const State& state)
    {
        auto* world = state.world.Get();
        auto* dialogue = state.dialogue.Get();
        auto* receiver = state.receiver.Get();
        auto* controller = state.controller.Get();
        auto* pawn = state.pawn.Get();
        auto* instance = state.instance.Get();
        auto* driver = state.driver.Get();
        auto* player = state.player.Get();
        auto* save = state.save.Get();
        auto* level = object_field(world, STR("PersistentLevel"));
        auto* id = field<Unreal::FStrProperty>(receiver, STR("Ping_ID"));
        return state.generation == generation.load(std::memory_order_acquire) && live(world) && live(level) &&
            authority(dialogue, world) && authority(receiver, world) && authority(controller, world) &&
            authority(pawn, world) && live(instance) && live(player) && live(save) && live(driver) &&
            static_cast<Unreal::AActor*>(dialogue)->GetLevel() == level &&
            static_cast<Unreal::AActor*>(receiver)->GetLevel() == level &&
            object_field(world, STR("OwningGameInstance")) == instance && object_field(world, STR("NetDriver")) == driver &&
            object_field(driver, STR("World")) == world && field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) &&
            !object_field(driver, STR("ServerConnection")) && sole_object(instance, STR("LocalPlayers")) == player &&
            object_field(player, STR("PlayerController")) == controller && object_field(controller, STR("Player")) == player &&
            object_field(controller, STR("Pawn")) == pawn && object_field(pawn, STR("Controller")) == controller &&
            object_field(instance, STR("SaveSlot_Ref")) == save && object_field(pawn, STR("Save")) == save &&
            sole_object(receiver, STR("ActorsToPower")) == dialogue && id &&
            RC::StringViewType{*id->GetPropertyValueInContainer(receiver)} == ping;
    }

    Unreal::UFunction* script(Unreal::UClass* owner_type, const Unreal::TCHAR* owner_path, const Unreal::TCHAR* name)
    {
        RC::StringType path{owner_path};
        path += STR(":");
        path += name;
        auto* function = find<Unreal::UFunction>(path.c_str());
        return function && owner_type && function->GetOuterPrivate() == owner_type &&
            !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ? function : nullptr;
    }

    bool frame_is(Unreal::FFrame* frame, Unreal::UFunction* node, Unreal::UObject* object)
    {
        return frame && frame->Node() == node && frame->Object() == object;
    }

    bool is_drill_turn_in(Unreal::FFrame* frame)
    {
        if (!frame || frame->Node() != scope.turn_in) return false;
        auto* property = scope.quest_parameter;
        auto* parameter = frame->OutParms();
        for (int left = scope.turn_in->GetNumParms(); parameter && left > 0; --left, parameter = parameter->NextOutParm)
        {
            if (parameter->Property != property) continue;
            return parameter->PropAddr && property->GetPropertyValue(parameter->PropAddr).ToString() == quest;
        }
        return false;
    }

    void observe(Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*)
    {
        if (!installed.load(std::memory_order_acquire) || !Unreal::IsInGameThreadRaw() ||
            Clock::now() >= scope.expires || iteration.OriginalFunctionCallPrevented()) return;
        auto* node = frame.Node();
        if (node != scope.send_ping && node != scope.enter_menu && node != scope.enter_gameplay) return;
        if (object != frame.Object() || !qualified(scope)) return;
        auto* parent = frame.PreviousFrame();
        auto* above = parent ? parent->PreviousFrame() : nullptr;
        auto* origin = above ? above->PreviousFrame() : nullptr;
        auto* local = scope.controller.Get();
        auto* conversation = scope.dialogue.Get();
        if (node == scope.send_ping && object == scope.receiver.Get() && parent && parent->Node() == scope.global_ping &&
            is_drill_turn_in(above) && frame.Locals())
        {
            if (RC::StringViewType{*scope.ping_parameter->GetPropertyValueInContainer(frame.Locals())} == ping &&
                scope.sender_parameter->GetObjectPropertyValue(
                    scope.sender_parameter->ContainerPtrToValuePtr<void>(frame.Locals())) == scope.pawn.Get())
                ++scope.pings;
            return;
        }
        if (object != local) return;
        if (node == scope.enter_menu && frame_is(parent, scope.add_menu, local) &&
            frame_is(above, scope.open_menu, local) && frame_is(origin, scope.dialogue_script, conversation) &&
            above->Locals() && scope.widget_parameter->GetObjectPropertyValue(
                scope.widget_parameter->ContainerPtrToValuePtr<void>(above->Locals())) == scope.widget_type)
        {
            iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented()) ++scope.menu_blocks;
        }
        if (node == scope.enter_gameplay &&
            ((frame_is(parent, scope.close_menu, local) && is_drill_turn_in(above)) ||
             (frame_is(parent, scope.close_all, local) && frame_is(above, scope.clear_ui, conversation) &&
              frame_is(origin, scope.dialogue_script, conversation))))
        {
            iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented()) ++scope.gameplay_blocks;
        }
    }

    bool bind(State& next, Address controller_address, Address dialogue_address)
    {
        auto* world = find<Unreal::UWorld>(world_path);
        auto* dialogue = find<Unreal::UObject>(dialogue_path);
        auto* receiver = find<Unreal::UObject>(receiver_path);
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* player = sole_object(instance, STR("LocalPlayers"));
        auto* controller = object_field(player, STR("PlayerController"));
        auto* pawn = object_field(controller, STR("Pawn"));
        auto* save = object_field(instance, STR("SaveSlot_Ref"));
        const auto exact = [](Unreal::UObject* object, const Unreal::TCHAR* path) {
            return live(object) && object->GetClassPrivate() == find<Unreal::UClass>(path);
        };
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (reinterpret_cast<Address>(dialogue) != dialogue_address || reinterpret_cast<Address>(controller) != controller_address ||
            !exact(dialogue, dialogue_class) || !exact(receiver, receiver_class) || !exact(controller, controller_path) ||
            !exact(pawn, pawn_path) || !exact(instance, STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")) ||
            !exact(save, STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")) || !live(player) || !local_type ||
            !player->IsA(local_type)) return false;
        next.world = world;
        next.dialogue = dialogue;
        next.receiver = receiver;
        next.controller = controller;
        next.pawn = pawn;
        next.instance = instance;
        next.driver = object_field(world, STR("NetDriver"));
        next.player = player;
        next.save = save;
        next.generation = generation.load(std::memory_order_acquire);
        if (!qualified(next)) return false;

        auto* type = controller->GetClassPrivate();
        auto* dialogue_type = dialogue->GetClassPrivate();
        auto* library_type = find<Unreal::UClass>(library);
        next.enter_menu = script(type, controller_path, STR("EnterMenuMode"));
        next.enter_gameplay = script(type, controller_path, STR("EnterGameplayMode"));
        next.add_menu = script(type, controller_path, STR("AddMenu"));
        next.open_menu = script(type, controller_path, STR("OpenMenu"));
        next.close_menu = script(type, controller_path, STR("CloseMenu"));
        next.close_all = script(type, controller_path, STR("CloseAllMenus"));
        next.dialogue_script = script(dialogue_type, dialogue_class, STR("ExecuteUbergraph_Dialogue_Controler_BP"));
        next.clear_ui = script(dialogue_type, dialogue_class, STR("ClearUI"));
        next.turn_in = script(library_type, library, STR("TurnIn_Quest"));
        next.global_ping = script(library_type, library, STR("GlobalPing"));
        next.send_ping = script(receiver->GetClassPrivate(), receiver_class, STR("Send_GlobalPing_ToActor"));
        next.quest_parameter = field<Unreal::FNameProperty>(next.turn_in, STR("Quest"));
        next.widget_parameter = field<Unreal::FClassProperty>(next.open_menu, STR("MenuClass"));
        next.ping_parameter = field<Unreal::FStrProperty>(next.send_ping, STR("Ping_ID"));
        next.sender_parameter = field<Unreal::FObjectProperty>(next.send_ping, STR("SendingActor"));
        next.widget_type = find<Unreal::UClass>(dialogue_widget);
        return next.enter_menu && next.enter_menu->GetNumParms() == 0 && !next.enter_menu->GetReturnProperty() &&
            next.enter_gameplay && next.enter_gameplay->GetNumParms() == 1 && !next.enter_gameplay->GetReturnProperty() &&
            next.add_menu && next.open_menu && next.close_menu && next.close_all && next.dialogue_script &&
            next.clear_ui && next.global_ping && next.send_ping && next.send_ping->GetNumParms() == 2 &&
            next.turn_in && next.turn_in->GetNumParms() == 6 && next.widget_type && next.widget_parameter &&
            next.widget_parameter->GetOffset_Internal() == 0 && next.quest_parameter &&
            next.quest_parameter->HasAnyPropertyFlags(Unreal::CPF_OutParm) && next.ping_parameter &&
            next.ping_parameter->GetOffset_Internal() == 0 && next.sender_parameter &&
            next.sender_parameter->GetOffset_Internal() == 0x10;
    }

    int result(const Lua& lua, bool ok, std::string_view reason)
    {
        lua.set_bool(ok);
        lua.set_string(reason);
        return 2;
    }

    int arm(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "stock_broker_menu_guard_unavailable");
        const auto controller = lua.get_integer();
        const auto dialogue = lua.get_integer();
        if (controller <= 0 || dialogue <= 0) return result(lua, false, "stock_broker_menu_guard_exact_actors_required");
        State next;
        if (!bind(next, controller, dialogue)) return result(lua, false, "stock_broker_menu_guard_schema_unavailable");
        next.expires = Clock::now() + std::chrono::seconds(3);
        scope = next;
        return result(lua, true, "armed");
    }

    int read(const Lua& lua)
    {
        const auto valid = Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && qualified(scope);
        lua.set_bool(valid && Clock::now() < scope.expires);
        lua.set_integer(valid ? scope.pings : 0);
        lua.set_integer(valid ? scope.menu_blocks : 0);
        lua.set_integer(valid ? scope.gameplay_blocks : 0);
        return 4;
    }
}

namespace silverfish::native_broker_menu
{
    void install_hooks()
    {
        if (!enabled() || hook != Hook::ERROR_ID) return;
        hook = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            observe, {false, false, owner, STR("Isolate headless stock Broker dialogue input")});
        installed.store(hook != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        if (hook != Hook::ERROR_ID) Hook::UnregisterCallback(hook);
        hook = Hook::ERROR_ID;
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishArmNativeBrokerMenuGuard", arm);
        lua.register_function("SilverfishReadNativeBrokerMenuGuard", read);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
