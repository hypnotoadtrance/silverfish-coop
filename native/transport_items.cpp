#include "transport_items.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <optional>
#include <string_view>

#include <DynamicOutput/DynamicOutput.hpp>
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

    constexpr auto mod_name = STR("SilverfishCoopNative");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto manager_path = STR("/Game/Classes/GUI/New/Components/ItemManager.ItemManager_C");
    constexpr auto card_path = STR("/Game/Classes/GUI/New/Components/ItemCard.ItemCard_C");
    constexpr auto context_menu_path = STR("/Game/Classes/GUI/New/Components/ItemContextMenu.ItemContextMenu_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto consumable_path = STR("/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C");
    constexpr auto scope_lease = std::chrono::seconds(2);

    struct Artifact
    {
        const Unreal::TCHAR* class_path;
        std::string_view name;
    };

    constexpr std::array artifacts{
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_DreamDust.Art_DreamDust_C"), "stardust"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_Tesseract_Green.Art_Tesseract_Green_C"), "green"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_Tesseract_Blue.Art_Tesseract_Blue_C"), "blue"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_Tesseract_Red.Art_Tesseract_Red_C"), "red"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_Tesseract_Gold.Art_Tesseract_Gold_C"), "gold"},
    };

    struct Entries
    {
        Unreal::FName action_use{STR("ActionUse")};
        Unreal::FName quick_move{STR("QuickMove")};
        Unreal::FName use_held{STR("UseHeldItem")};
        Unreal::FName confirm_use{STR("ConfirmUseItem")};
        Unreal::FName use_primary{STR("Use_Item_Primary")};
        Unreal::FName apply_effects{STR("ApplyItemEffects")};
    };

    struct Scope
    {
        std::uintptr_t address{};
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool host{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr local_player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr server;
        Unreal::FWeakObjectPtr instance;
        Unreal::FObjectProperty* pawn_controller{};
        Unreal::FObjectProperty* controller_pawn{};
        Unreal::FObjectProperty* controller_player{};
        Unreal::FObjectProperty* world_driver{};
        Unreal::FObjectProperty* world_instance{};
        Unreal::FObjectProperty* server_connection{};
        Unreal::FArrayProperty* local_players{};
        Unreal::FByteProperty* pawn_role{};
    };

    struct Receipt
    {
        std::int64_t count{};
        std::string_view role;
        std::string_view item;
        std::string_view entry;
        std::string_view reason;
    };

    std::optional<Entries> entries;
    std::array<Hook::GlobalCallbackId, 3> hooks{};
    std::atomic<bool> hooks_ready{};
    std::atomic<std::uint64_t> generation{1};
    thread_local Scope scope;
    thread_local Receipt receipt;

    [[nodiscard]] bool enabled()
    {
        std::array<char, 2> flag{};
        std::size_t size{};
        return getenv_s(&size, flag.data(), flag.size(), "SILVERFISH_COOP_NATIVE_TRANSPORT") == 0 &&
               size == flag.size() && flag[0] == '1';
    }

    template <typename T>
    [[nodiscard]] T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    [[nodiscard]] Unreal::UObject* object_value(Unreal::FObjectProperty* property, const void* owner)
    {
        return property && owner ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    [[nodiscard]] Unreal::UObject* object_field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        return object_value(field<Unreal::FObjectProperty>(owner, name), owner);
    }

    [[nodiscard]] bool live(Unreal::UObject* object)
    {
        constexpr auto flags = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject | Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(flags) && !object->IsUnreachable();
    }

    [[nodiscard]] bool exact_class(Unreal::UObject* object, const Unreal::TCHAR* path)
    {
        return object && object->GetClassPrivate() && object->GetClassPrivate()->GetPathName() == path;
    }

    [[nodiscard]] Unreal::UObject* find_object(std::uintptr_t address, const Unreal::TCHAR* class_path)
    {
        Unreal::UObject* result = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (reinterpret_cast<std::uintptr_t>(object) != address)
            {
                return RC::LoopAction::Continue;
            }
            if (exact_class(object, class_path) && live(object))
            {
                result = object;
            }
            return RC::LoopAction::Break;
        });
        return result;
    }

    [[nodiscard]] Unreal::UObject* only_local_player(const Scope& current, Unreal::UObject* instance)
    {
        const auto* inner = current.local_players ? Unreal::CastField<Unreal::FObjectProperty>(current.local_players->GetInner()) : nullptr;
        if (!inner)
        {
            return nullptr;
        }
        Unreal::FScriptArrayHelper players(current.local_players, current.local_players->ContainerPtrToValuePtr<void>(instance));
        return players.Num() == 1 ? inner->GetObjectPropertyValue(players.GetRawPtr()) : nullptr;
    }

    [[nodiscard]] bool current_player(const Scope& current)
    {
        auto* pawn = current.pawn.Get();
        auto* world = current.world.Get();
        auto* controller = current.controller.Get();
        auto* local = current.local_player.Get();
        auto* driver = current.driver.Get();
        auto* instance = current.instance.Get();
        auto* server = current.server.Get();
        const auto expected_role = current.host ? 3 : 2;
        return pawn && world && controller && local && driver && instance && current.pawn_role &&
               (current.host || server) && static_cast<Unreal::AActor*>(pawn)->GetWorld() == world &&
               current.pawn_role->GetPropertyValueInContainer(pawn) == expected_role &&
               object_value(current.pawn_controller, pawn) == controller &&
               object_value(current.controller_pawn, controller) == pawn &&
               object_value(current.controller_player, controller) == local &&
               object_value(current.world_driver, world) == driver &&
               object_value(current.world_instance, world) == instance &&
               object_value(current.server_connection, driver) == server && only_local_player(current, instance) == local;
    }

    [[nodiscard]] bool enroll(std::uintptr_t address, bool host)
    {
        const auto now = Clock::now();
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.address == address && scope.host == host && scope.generation == epoch)
        {
            if (!current_player(scope))
            {
                return false;
            }
            scope.expires = now + scope_lease;
            return true;
        }

        auto* pawn = find_object(address, pawn_path);
        auto* world = pawn ? static_cast<Unreal::AActor*>(pawn)->GetWorld() : nullptr;
        if (!world)
        {
            return false;
        }
        Scope next;
        next.pawn_controller = field<Unreal::FObjectProperty>(pawn, STR("Controller"));
        auto* controller = object_value(next.pawn_controller, pawn);
        if (!live(controller) || !exact_class(controller, controller_path))
        {
            return false;
        }
        next.controller_pawn = field<Unreal::FObjectProperty>(controller, STR("Pawn"));
        next.controller_player = field<Unreal::FObjectProperty>(controller, STR("Player"));
        auto* local = object_value(next.controller_player, controller);
        next.world_driver = field<Unreal::FObjectProperty>(world, STR("NetDriver"));
        next.world_instance = field<Unreal::FObjectProperty>(world, STR("OwningGameInstance"));
        auto* driver = object_value(next.world_driver, world);
        auto* instance = object_value(next.world_instance, world);
        if (!live(local) || !live(driver) || !live(instance))
        {
            return false;
        }
        next.local_players = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        next.server_connection = field<Unreal::FObjectProperty>(driver, STR("ServerConnection"));
        next.pawn_role = field<Unreal::FByteProperty>(pawn, STR("Role"));
        auto* server = object_value(next.server_connection, driver);
        if (!next.server_connection || (host ? server != nullptr : !live(server)))
        {
            return false;
        }
        next.address = address;
        next.host = host;
        next.generation = epoch;
        next.expires = now + scope_lease;
        next.pawn = pawn;
        next.controller = controller;
        next.local_player = local;
        next.world = world;
        next.driver = driver;
        next.server = server;
        next.instance = instance;
        if (!current_player(next))
        {
            return false;
        }
        scope = next;
        return true;
    }

    [[nodiscard]] const Artifact* identify(Unreal::UObject* type)
    {
        if (!type)
        {
            return nullptr;
        }
        const auto path = type->GetPathName();
        for (const auto& artifact : artifacts)
        {
            if (path == artifact.class_path)
            {
                return &artifact;
            }
        }
        return nullptr;
    }

    [[nodiscard]] const Artifact* inventory_item(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* item = field<Unreal::FStructProperty>(owner, name);
        auto* type = item ? item->GetStruct().Get() : nullptr;
        if (!type || type->GetPathName() != item_path)
        {
            return nullptr;
        }
        auto* item_class = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        return identify(object_value(item_class, item->ContainerPtrToValuePtr<void>(owner)));
    }

    [[nodiscard]] const Artifact* manager_item(Unreal::UObject* manager, const Unreal::FName& name)
    {
        if (name == entries->action_use)
        {
            auto* menu = object_field(manager, STR("ItemContextMenu"));
            auto* card = exact_class(menu, context_menu_path) ? object_field(menu, STR("Item Card")) : nullptr;
            return exact_class(card, card_path) ? inventory_item(card, STR("Item")) : nullptr;
        }
        if (name == entries->quick_move)
        {
            auto* card = object_field(manager, STR("HoveredCard"));
            if (!exact_class(card, card_path))
            {
                return nullptr;
            }
            auto* widget = object_field(card, STR("InventoryWidget"));
            auto* quick_use = field<Unreal::FBoolProperty>(widget, STR("Can Quick Use Items"));
            return quick_use && !quick_use->GetPropertyValueInContainer(widget) ? nullptr : inventory_item(card, STR("Item"));
        }
        return inventory_item(manager, STR("HeldItem"));
    }

    [[nodiscard]] bool same_widget_player(Unreal::UObject* manager)
    {
        auto* function = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(
            nullptr, nullptr, STR("/Script/UMG.Widget:GetOwningPlayer"));
        auto* result = function ? Unreal::CastField<Unreal::FObjectProperty>(function->GetReturnProperty()) : nullptr;
        if (!result || !function->HasAnyFunctionFlags(Unreal::FUNC_Native) || function->GetNumParms() != 1 ||
            function->GetParmsSize() != sizeof(Unreal::UObject*) || result->GetOffset_Internal() != 0)
        {
            return false;
        }
        Unreal::UObject* controller = nullptr;
        manager->ProcessEvent(function, &controller);
        auto* cached_pawn = object_field(manager, STR("Player_FP"));
        return controller && controller == scope.controller.Get() && (!cached_pawn || cached_pawn == scope.pawn.Get());
    }

    [[nodiscard]] bool same_actor_player(Unreal::UObject* actor)
    {
        auto* pawn = scope.pawn.Get();
        if (static_cast<Unreal::AActor*>(actor)->GetWorld() != scope.world.Get())
        {
            return false;
        }
        if (object_field(pawn, STR("EquipedItem")) == actor || object_field(pawn, STR("HeldObject")) == actor)
        {
            return true;
        }
        return object_field(actor, STR("Player_Ref")) == pawn &&
               (object_field(actor, STR("Instigator")) == pawn || object_field(actor, STR("Owner")) == pawn);
    }

    void record(const Artifact& item, std::string_view entry, Unreal::UObject* context, bool widget)
    {
        const bool qualified = scope.generation == generation.load(std::memory_order_acquire) &&
                               Clock::now() < scope.expires && current_player(scope) &&
                               (widget ? same_widget_player(context) : same_actor_player(context));
        const std::string_view role = !qualified ? "unverified" : scope.host ? "host" : "guest";
        const std::string_view reason = !qualified ? "current_local_owner_unverified" :
            scope.host ? "coordinated_route_required" : "host_activation_required";
        if (receipt.role != role || receipt.item != item.name || receipt.entry != entry || receipt.reason != reason)
        {
            RC::Output::send<RC::LogLevel::Verbose>(STR("[SilverfishCoopNative] Transport item denied for {}: {} via {} ({})\n"),
                RC::ensure_str(role), RC::ensure_str(item.name), RC::ensure_str(entry), RC::ensure_str(reason));
        }
        receipt = {receipt.count + 1, role, item.name, entry, reason};
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function)
    {
        if (!context || !function || !entries || iteration.OriginalFunctionCallPrevented())
        {
            return;
        }
        const auto name = function->GetNamePrivate();
        const bool manager = name == entries->action_use || name == entries->quick_move ||
                             name == entries->use_held || name == entries->confirm_use;
        const bool primary = name == entries->use_primary;
        const bool effects = name == entries->apply_effects;
        if (!(manager || primary || effects) || function->GetReturnProperty() ||
            function->GetNumParms() != (primary ? 2 : 0) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native) || function->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return;
        }

        const Artifact* artifact;
        std::string_view entry;
        if (manager)
        {
            if (!exact_class(context, manager_path) || function->GetOuterPrivate() != context->GetClassPrivate())
            {
                return;
            }
            artifact = manager_item(context, name);
            entry = name == entries->action_use ? "action_use" : name == entries->quick_move ? "quick_move" :
                name == entries->use_held ? "use_held_item" : "confirm_use_item";
        }
        else
        {
            artifact = identify(context->GetClassPrivate());
            if (!artifact)
            {
                return;
            }
            auto* owner = function->GetOuterPrivate();
            const bool inherited = primary && artifact == &artifacts[0] && owner && owner->GetPathName() == consumable_path;
            if (!inherited && owner != context->GetClassPrivate())
            {
                return;
            }
            entry = primary ? "use_item_primary" : "apply_item_effects";
        }
        if (!artifact)
        {
            return;
        }
        iteration.PreventOriginalFunctionCall();
        if (iteration.OriginalFunctionCallPrevented())
        {
            record(*artifact, entry, context, manager);
        }
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            intercept(iteration, context, frame.Node());
        }
    }

    int lua_enroll(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw())
        {
            if (hooks_ready.load(std::memory_order_acquire) && lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_bool(2))
            {
                const auto host = lua.get_bool(2);
                const auto address = lua.get_integer(1);
                accepted = address > 0 && enroll(static_cast<std::uintptr_t>(address), host);
            }
            if (!accepted)
            {
                scope = {};
            }
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_clear(const Lua&)
    {
        silverfish::native_transport::clear_scope();
        return 0;
    }

    int lua_status(const Lua& lua)
    {
        const bool game_thread = Unreal::IsInGameThreadRaw();
        const Receipt observed = game_thread ? receipt : Receipt{};
        lua.set_bool(game_thread && hooks_ready.load(std::memory_order_acquire));
        lua.set_integer(observed.count);
        lua.set_string(observed.role);
        lua.set_string(observed.item);
        lua.set_string(observed.entry);
        lua.set_string(observed.reason);
        return 6;
    }

    int probe_result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int lua_probe_held_item(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !hooks_ready.load(std::memory_order_acquire) ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2) ||
            scope.generation != generation.load(std::memory_order_acquire) || Clock::now() >= scope.expires ||
            !current_player(scope))
        {
            return probe_result(lua, false, "current_local_transport_scope_required");
        }
        const auto item_id = lua.get_string(2);
        const auto address = lua.get_integer(1);
        const Artifact* item = nullptr;
        for (const auto& candidate : artifacts)
        {
            if (candidate.name == item_id)
            {
                item = &candidate;
                break;
            }
        }
        if (address <= 0 || !item)
        {
            return probe_result(lua, false, "exact_transport_probe_input_required");
        }
        auto* manager = find_object(static_cast<std::uintptr_t>(address), manager_path);
        if (!manager || !same_widget_player(manager))
        {
            return probe_result(lua, false, "current_local_item_manager_required");
        }

        auto* item_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, item->class_path);
        auto* held = field<Unreal::FStructProperty>(manager, STR("HeldItem"));
        auto* type = held ? held->GetStruct().Get() : nullptr;
        auto* class_property = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        auto* guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        auto* guid_type = guid ? guid->GetStruct().Get() : nullptr;
        auto* function = manager->GetFunctionByNameInChain(entries->use_held);
        auto* item_base = class_property ? class_property->GetMetaClass().Get() : nullptr;
        if (!item_class || !type || type->GetPathName() != item_path || !item_base || !item_class->IsChildOf(item_base) ||
            !guid_type || guid_type->GetPathName() != STR("/Script/CoreUObject.Guid") || !function ||
            function->GetOuterPrivate() != manager->GetClassPrivate() || function->GetNumParms() != 0 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return probe_result(lua, false, "stock_held_item_schema_unavailable");
        }
        auto* record = held->ContainerPtrToValuePtr<void>(manager);
        auto* guid_record = guid->ContainerPtrToValuePtr<void>(record);
        auto* class_record = class_property->ContainerPtrToValuePtr<void>(record);
        if (class_property->GetObjectPropertyValue(class_record))
        {
            return probe_result(lua, false, "local_item_manager_is_holding_an_item");
        }
        constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
        for (const auto field_name : guid_names)
        {
            auto* word = field<Unreal::FIntProperty>(guid_type, field_name);
            if (!word || word->GetPropertyValueInContainer(guid_record) != 0)
            {
                return probe_result(lua, false, "local_held_item_guid_is_not_empty");
            }
        }

        // Stock ItemIsValid rejects the zero GUID even with a valid class.
        // This exercises the real entry with no owned consumable or item row.
        struct RestoreClass
        {
            Unreal::FClassProperty* property;
            void* address;
            ~RestoreClass() { property->SetObjectPropertyValue(address, nullptr); }
        } restore{class_property, class_record};
        class_property->SetObjectPropertyValue(class_record, item_class);
        const auto before = receipt.count;
        manager->ProcessEvent(function, nullptr);
        const bool denied = receipt.count == before + 1 && receipt.item == item->name &&
                            receipt.entry == "use_held_item" && receipt.role == (scope.host ? "host" : "guest");
        return probe_result(lua, denied, denied ? "stock_held_use_denied" : "stock_held_use_did_not_report_expected_denial");
    }
}

void silverfish::native_transport::install_hooks()
{
    if (!enabled())
    {
        return;
    }
    entries.emplace();
    hooks[0] = Hook::RegisterProcessEventPreCallback(
        [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, void*) {
            if (Unreal::IsInGameThreadRaw())
            {
                intercept(iteration, context, function);
            }
        }, {false, false, mod_name, STR("Guard reflected transport use")});
    hooks[1] = Hook::RegisterProcessInternalPreCallback(
        intercept_script, {false, false, mod_name, STR("Guard Blueprint transport dispatch")});
    hooks[2] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
        intercept_script, {false, false, mod_name, STR("Guard Blueprint transport use")});
    const bool ready = hooks[0] != Hook::ERROR_ID && hooks[2] != Hook::ERROR_ID;
    hooks_ready.store(ready, std::memory_order_release);
    if (!ready)
    {
        RC::Output::send<RC::LogLevel::Warning>(STR("[SilverfishCoopNative] Transport item guard is missing a required entry point.\n"));
    }
}

void silverfish::native_transport::clear_scope()
{
    generation.fetch_add(1, std::memory_order_release);
    if (Unreal::IsInGameThreadRaw())
    {
        scope = {};
    }
}

void silverfish::native_transport::uninstall_hooks()
{
    hooks_ready.store(false, std::memory_order_release);
    clear_scope();
    for (const auto id : hooks)
    {
        if (id != Hook::ERROR_ID)
        {
            Hook::UnregisterCallback(id);
        }
    }
}

void silverfish::native_transport::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (enabled())
    {
        lua.register_function("SilverfishSetNativeTransportScope", lua_enroll);
        lua.register_function("SilverfishClearNativeTransportScope", lua_clear);
        lua.register_function("SilverfishReadNativeTransportStatus", lua_status);
        lua.register_function("SilverfishProbeNativeTransportHeldItem", lua_probe_held_item);
    }
}
