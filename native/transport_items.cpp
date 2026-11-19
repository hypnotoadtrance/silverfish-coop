#include "transport_items.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
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
    constexpr auto panel_path = STR("/Game/Classes/GUI/New/Components/InventoryPanel.InventoryPanel_C");
    constexpr auto context_menu_path = STR("/Game/Classes/GUI/New/Components/ItemContextMenu.ItemContextMenu_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto consumable_path = STR("/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto item_class_name = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto item_guid_name = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr auto item_ammo_name = STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr auto scope_lease = std::chrono::seconds(2);
    constexpr auto request_lease = std::chrono::seconds(2);
    constexpr int inventory_limit = 4096;
    using Guid = std::array<std::uint32_t, guid_names.size()>;

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
    constexpr const auto& blue = artifacts[2];
    constexpr const auto& gold = artifacts.back();

    enum class CubeKind { Blue, Gold };

    [[nodiscard]] constexpr const Artifact& cube_artifact(CubeKind kind)
    {
        return kind == CubeKind::Gold ? gold : blue;
    }

    [[nodiscard]] constexpr std::string_view cube_status(CubeKind kind, std::string_view gold_status, std::string_view blue_status)
    {
        return kind == CubeKind::Gold ? gold_status : blue_status;
    }

    [[nodiscard]] constexpr std::string_view queued_status(CubeKind kind)
    {
        return cube_status(kind, "gold_request_queued", "blue_request_queued");
    }

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

    struct CubeRequest
    {
        CubeKind kind;
        Guid guid;
        std::uint64_t generation;
        Clock::time_point expires;
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr save;
        std::string_view entry;
    };

    std::optional<Entries> entries;
    std::array<Hook::GlobalCallbackId, 3> hooks{};
    std::atomic<bool> hooks_ready{};
    std::atomic<std::uint64_t> generation{1};
    thread_local Scope scope;
    thread_local Receipt receipt;
    thread_local std::optional<CubeRequest> cube_request;

    [[nodiscard]] bool enabled()
    {
        std::array<char, 2> flag{};
        std::size_t size{};
        return getenv_s(&size, flag.data(), flag.size(), "SILVERFISH_COOP_NATIVE_TRANSPORT") == 0 &&
               size == flag.size() && flag[0] == '1';
    }

    [[nodiscard]] bool fixtures_enabled()
    {
        std::array<char, 2> flag{};
        std::size_t size{};
        return getenv_s(&size, flag.data(), flag.size(), "SILVERFISH_COOP_NATIVE_FIXTURES") == 0 &&
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

    struct ItemView
    {
        const Artifact* artifact{};
        Unreal::UScriptStruct* structure{};
        const void* value{};
        Unreal::UObject* panel{};
    };

    [[nodiscard]] ItemView inventory_item(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* item = field<Unreal::FStructProperty>(owner, name);
        auto* type = item ? item->GetStruct().Get() : nullptr;
        if (!type || type->GetPathName() != item_path)
        {
            return {};
        }
        const auto* value = item->ContainerPtrToValuePtr<void>(owner);
        return {identify(object_value(field<Unreal::FClassProperty>(type, item_class_name), value)), type, value};
    }

    [[nodiscard]] ItemView card_item(Unreal::UObject* card)
    {
        if (!exact_class(card, card_path))
        {
            return {};
        }
        auto item = inventory_item(card, STR("Item"));
        item.panel = object_field(card, STR("InventoryWidget"));
        return item;
    }

    [[nodiscard]] ItemView manager_item(Unreal::UObject* manager, const Unreal::FName& name)
    {
        if (name == entries->action_use)
        {
            auto* menu = object_field(manager, STR("ItemContextMenu"));
            return exact_class(menu, context_menu_path) ? card_item(object_field(menu, STR("Item Card"))) : ItemView{};
        }
        if (name == entries->quick_move)
        {
            auto item = card_item(object_field(manager, STR("HoveredCard")));
            auto* quick_use = field<Unreal::FBoolProperty>(item.panel, STR("Can Quick Use Items"));
            return quick_use && !quick_use->GetPropertyValueInContainer(item.panel) ? ItemView{} : item;
        }
        return inventory_item(manager, STR("HeldItem"));
    }

    struct GuidFields
    {
        Unreal::FStructProperty* property{};
        std::array<Unreal::FIntProperty*, guid_names.size()> words{};

        explicit GuidFields(Unreal::UScriptStruct* item)
        {
            property = field<Unreal::FStructProperty>(item, item_guid_name);
            auto* structure = property ? property->GetStruct().Get() : nullptr;
            if (structure && structure->GetPathName() == guid_path)
            {
                for (std::size_t i = 0; i < words.size(); ++i)
                {
                    words[i] = field<Unreal::FIntProperty>(structure, guid_names[i]);
                }
            }
        }

        [[nodiscard]] bool valid() const { return std::ranges::all_of(words, [](const auto* word) { return word; }); }

        [[nodiscard]] Guid read(const void* item) const
        {
            const auto* value = property->ContainerPtrToValuePtr<void>(item);
            Guid result{};
            for (std::size_t i = 0; i < words.size(); ++i)
            {
                result[i] = static_cast<std::uint32_t>(words[i]->GetPropertyValueInContainer(value));
            }
            return result;
        }

        void write(void* item, const Guid& guid) const
        {
            auto* value = property->ContainerPtrToValuePtr<void>(item);
            for (std::size_t i = 0; i < words.size(); ++i)
            {
                words[i]->SetPropertyValueInContainer(value, static_cast<std::int32_t>(guid[i]));
            }
        }
    };

    [[nodiscard]] bool parse_guid(std::string_view input, Guid& guid)
    {
        if (input.size() != 32 || !std::ranges::all_of(input, [](char value) {
                return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F');
            }))
        {
            return false;
        }
        for (std::size_t i = 0; i < guid.size(); ++i)
        {
            const auto* start = input.data() + i * 8;
            const auto parsed = std::from_chars(start, start + 8, guid[i], 16);
            if (parsed.ec != std::errc{} || parsed.ptr != start + 8)
            {
                return false;
            }
        }
        return guid != Guid{};
    }

    [[nodiscard]] auto format_guid(const Guid& guid)
    {
        constexpr std::string_view digits = "0123456789ABCDEF";
        std::array<char, 32> text{};
        for (std::size_t i = 0; i < guid.size(); ++i)
        {
            for (std::size_t digit = 0; digit < 8; ++digit)
            {
                text[i * 8 + digit] = digits[(guid[i] >> ((7 - digit) * 4)) & 0xF];
            }
        }
        return text;
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

    [[nodiscard]] bool active_scope()
    {
        return scope.generation == generation.load(std::memory_order_acquire) && Clock::now() < scope.expires &&
               current_player(scope);
    }

    [[nodiscard]] Unreal::UObject* canonical_host_save()
    {
        auto* pawn = scope.pawn.Get();
        auto* instance = scope.instance.Get();
        auto* slot = exact_class(instance, instance_path) ? object_field(instance, STR("SaveSlot_Ref")) : nullptr;
        return live(slot) && exact_class(slot, save_path) && object_field(pawn, STR("Save")) == slot ? slot : nullptr;
    }

    [[nodiscard]] bool current_host_argument(std::int64_t address)
    {
        return address > 0 && scope.address == static_cast<std::uintptr_t>(address) && scope.host &&
               hooks_ready.load(std::memory_order_acquire) && active_scope();
    }

    struct CubeInventory
    {
        Unreal::UObject* save{};
        Unreal::FArrayProperty* backpack{};
        Unreal::UScriptStruct* item{};
        Unreal::FClassProperty* item_class{};
        Unreal::FIntProperty* ammo{};
        Unreal::UClass* cube_class{};
        void* record{};
        int index{-1};
    };

    [[nodiscard]] bool item_struct(Unreal::FProperty* property, const Unreal::UScriptStruct* item)
    {
        const auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
        return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == item;
    }

    [[nodiscard]] std::string_view locate_cube(Unreal::UObject* save, CubeKind kind, const Guid& guid, CubeInventory& match)
    {
        if (!save)
        {
            return "canonical_host_save_required";
        }
        match.save = save;
        match.backpack = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
        auto* equipment = field<Unreal::FArrayProperty>(save, STR("PlayerEquipment"));
        auto* helmet = field<Unreal::FStructProperty>(save, STR("PlayerHelmet"));
        auto* armor = field<Unreal::FStructProperty>(save, STR("PlayerArmor"));
        const auto* backpack_row = match.backpack ? Unreal::CastField<Unreal::FStructProperty>(match.backpack->GetInner()) : nullptr;
        match.item = backpack_row ? backpack_row->GetStruct().Get() : nullptr;
        if (!match.item || match.item->GetPathName() != item_path || backpack_row->GetArrayDim() != 1 ||
            !equipment || !item_struct(equipment->GetInner(), match.item) ||
            !item_struct(helmet, match.item) || !item_struct(armor, match.item))
        {
            return "stock_backpack_schema_unavailable";
        }
        const GuidFields fields(match.item);
        match.item_class = field<Unreal::FClassProperty>(match.item, item_class_name);
        match.ammo = field<Unreal::FIntProperty>(match.item, item_ammo_name);
        match.cube_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, cube_artifact(kind).class_path);
        auto* defaults = match.cube_class ? match.cube_class->GetClassDefaultObject().Get() : nullptr;
        auto* stacks = field<Unreal::FBoolProperty>(defaults, STR("Stacks"));
        auto* expected_ammo = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
        auto* item_base = match.item_class ? match.item_class->GetMetaClass().Get() : nullptr;
        if (!fields.valid() || !item_base || !match.ammo || !stacks || !expected_ammo || !match.cube_class->IsChildOf(item_base) ||
            stacks->GetPropertyValueInContainer(defaults) || expected_ammo->GetPropertyValueInContainer(defaults) != -1)
        {
            return "stock_single_cube_schema_unavailable";
        }
        Unreal::FScriptArrayHelper backpack(match.backpack, match.backpack->ContainerPtrToValuePtr<void>(save));
        Unreal::FScriptArrayHelper loadout(equipment, equipment->ContainerPtrToValuePtr<void>(save));
        if (backpack.Num() > inventory_limit || loadout.Num() > inventory_limit)
        {
            return "stock_inventory_size_out_of_range";
        }
        for (int index = 0; index < backpack.Num(); ++index)
        {
            auto* row = backpack.GetRawPtr(index);
            if (fields.read(row) != guid)
            {
                continue;
            }
            if (match.record || object_value(match.item_class, row) != match.cube_class ||
                match.ammo->GetPropertyValueInContainer(row) != -1)
            {
                return "host_backpack_guid_conflict";
            }
            match.index = index;
            match.record = row;
        }
        for (int index = 0; index < loadout.Num(); ++index)
        {
            if (fields.read(loadout.GetRawPtr(index)) == guid)
            {
                return "host_equipment_guid_conflict";
            }
        }
        return fields.read(helmet->ContainerPtrToValuePtr<void>(save)) == guid ||
               fields.read(armor->ContainerPtrToValuePtr<void>(save)) == guid ? "host_equipment_guid_conflict" : "";
    }

    [[nodiscard]] bool exact_backpack_panel(Unreal::UObject* panel, Unreal::UObject* manager)
    {
        auto* index = exact_class(panel, panel_path) ? field<Unreal::FIntProperty>(panel, STR("InventoryIndex")) : nullptr;
        return index && index->GetPropertyValueInContainer(panel) == 0 &&
               object_field(panel, STR("Target")) == scope.instance.Get() && object_field(panel, STR("ItemManager")) == manager;
    }

    [[nodiscard]] bool request_current(const CubeRequest& request, Unreal::UObject* save)
    {
        return save && request.save.Get() == save && request.pawn.Get() == scope.pawn.Get() &&
               request.generation == scope.generation && Clock::now() < request.expires;
    }

    [[nodiscard]] std::string_view pending_conflict(const CubeRequest& pending, CubeKind kind)
    {
        return pending.kind != kind ? "another_cube_request_pending" :
            cube_status(kind, "another_gold_request_pending", "another_blue_request_pending");
    }

    [[nodiscard]] std::string_view queue_cube(CubeKind kind, const ItemView& candidate, std::string_view entry,
                                              Unreal::UObject* manager)
    {
        if (entry != "use_held_item" &&
            ((entry != "action_use" && entry != "quick_move") || !exact_backpack_panel(candidate.panel, manager)))
        {
            return "canonical_backpack_source_required";
        }
        const GuidFields fields(candidate.structure);
        const auto guid = fields.valid() ? fields.read(candidate.value) : Guid{};
        if (guid == Guid{})
        {
            return "coordinated_route_required";
        }
        auto* pawn = scope.pawn.Get();
        auto* submerged = field<Unreal::FBoolProperty>(pawn, STR("HeadUnderWater"));
        auto* dead = field<Unreal::FBoolProperty>(pawn, STR("Dead"));
        if (!submerged || !dead || submerged->GetPropertyValueInContainer(pawn) || dead->GetPropertyValueInContainer(pawn))
        {
            return "stock_transport_use_ineligible";
        }
        auto* save = canonical_host_save();
        CubeInventory match;
        if (const auto error = locate_cube(save, kind, guid, match); !error.empty())
        {
            return error;
        }
        if (!match.record || match.item != candidate.structure)
        {
            return "owned_backpack_cube_required";
        }
        if (cube_request && request_current(*cube_request, save))
        {
            return cube_request->kind == kind && cube_request->guid == guid ?
                cube_status(kind, "gold_request_pending", "blue_request_pending") : pending_conflict(*cube_request, kind);
        }
        cube_request = CubeRequest{kind, guid, scope.generation, Clock::now() + request_lease, pawn, save, entry};
        return queued_status(kind);
    }

    void record(const Artifact& item, const ItemView& candidate, std::string_view entry, Unreal::UObject* context, bool widget)
    {
        const bool qualified = active_scope() &&
                               (widget ? same_widget_player(context) : same_actor_player(context));
        const std::string_view role = !qualified ? "unverified" : scope.host ? "host" : "guest";
        std::string_view reason = !qualified ? "current_local_owner_unverified" :
            !scope.host ? "host_activation_required" : "coordinated_route_required";
        if (qualified && scope.host && widget && (&item == &gold || &item == &blue))
        {
            reason = queue_cube(&item == &gold ? CubeKind::Gold : CubeKind::Blue, candidate, entry, context);
        }
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
        ItemView candidate;
        std::string_view entry;
        if (manager)
        {
            if (!exact_class(context, manager_path) || function->GetOuterPrivate() != context->GetClassPrivate())
            {
                return;
            }
            candidate = manager_item(context, name);
            artifact = candidate.artifact;
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
            record(*artifact, candidate, entry, context, manager);
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
                cube_request.reset();
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

    int report_cube_request(const Lua& lua, bool ok, std::string_view detail, std::string_view entry = {})
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        lua.set_string(entry);
        return 3;
    }

    template <CubeKind kind>
    int lua_take_cube_request(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !current_host_argument(lua.get_integer(1)))
        {
            return report_cube_request(lua, false, "current_transport_host_required");
        }
        if (!cube_request || cube_request->kind != kind)
        {
            return report_cube_request(lua, false, cube_status(kind, "no_gold_request", "no_blue_request"));
        }
        const auto request = *cube_request;
        cube_request.reset();
        auto* save = canonical_host_save();
        if (!request_current(request, save))
        {
            return report_cube_request(lua, false, cube_status(kind, "stale_gold_request", "stale_blue_request"));
        }
        CubeInventory match;
        if (const auto error = locate_cube(save, kind, request.guid, match); !error.empty())
        {
            return report_cube_request(lua, false, error);
        }
        if (!match.record)
        {
            return report_cube_request(lua, false, "owned_backpack_cube_required");
        }
        const auto guid = format_guid(request.guid);
        return report_cube_request(lua, true, {guid.data(), guid.size()}, request.entry);
    }

    [[nodiscard]] std::string_view cube_inventory_argument(const Lua& lua, CubeKind kind, Guid& guid, CubeInventory& match)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2))
        {
            return cube_status(kind, "host_and_gold_guid_required_on_game_thread", "host_and_blue_guid_required_on_game_thread");
        }
        if (!parse_guid(lua.get_string(2), guid))
        {
            return cube_status(kind, "invalid_gold_guid", "invalid_blue_guid");
        }
        if (!current_host_argument(lua.get_integer(1)))
        {
            return "current_transport_host_required";
        }
        return locate_cube(canonical_host_save(), kind, guid, match);
    }

    [[nodiscard]] Unreal::UFunction* stock_no_args(Unreal::UObject* object, const Unreal::FName& name)
    {
        auto* function = object->GetFunctionByNameInChain(name);
        return function && function->GetOuterPrivate() == object->GetClassPrivate() && function->GetNumParms() == 0 &&
               !function->GetReturnProperty() && !function->HasAnyFunctionFlags(Unreal::FUNC_Native) &&
               !function->HasAnyFunctionFlags(Unreal::FUNC_Net) ? function : nullptr;
    }

    class RestoreItem
    {
    public:
        static bool supports(Unreal::FStructProperty* property, const Unreal::UScriptStruct* type)
        {
            return item_struct(property, type) && property->GetSize() > 0 && property->GetSize() <= capacity &&
                   property->GetMinAlignment() <= alignof(std::max_align_t);
        }

        RestoreItem(Unreal::FStructProperty* property, void* value) : property(property), value(value)
        {
            property->InitializeValue(original.data());
            property->CopyCompleteValue(original.data(), value);
        }

        ~RestoreItem()
        {
            property->CopyCompleteValue(value, original.data());
            property->DestroyValue(original.data());
        }

        RestoreItem(const RestoreItem&) = delete;
        RestoreItem& operator=(const RestoreItem&) = delete;

    private:
        static constexpr int capacity = 256;
        Unreal::FStructProperty* property;
        void* value;
        alignas(std::max_align_t) std::array<std::byte, capacity> original{};
    };

    template <CubeKind kind>
    int lua_inspect_cube(const Lua& lua)
    {
        Guid guid{};
        CubeInventory match;
        if (const auto error = cube_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        return probe_result(lua, true, match.record ? "present" : "absent");
    }

    template <CubeKind kind>
    int lua_commit_cube(const Lua& lua)
    {
        Guid guid{};
        CubeInventory match;
        if (const auto error = cube_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (!match.record)
        {
            return probe_result(lua, true, "absent");
        }
        auto* instance = scope.instance.Get();
        const Unreal::FName carry_name{STR("Calc_CarryWeight")};
        auto* recalculate = stock_no_args(instance, carry_name);
        if (!recalculate)
        {
            return probe_result(lua, false, "stock_carry_weight_function_unavailable");
        }
        Unreal::FScriptArrayHelper backpack(match.backpack, match.backpack->ContainerPtrToValuePtr<void>(match.save));
        backpack.RemoveValues(match.index, 1);
        instance->ProcessEvent(recalculate, nullptr);
        return probe_result(lua, true, "removed");
    }

    template <CubeKind kind>
    int lua_seed_cube(const Lua& lua)
    {
        if (!fixtures_enabled())
        {
            return probe_result(lua, false, cube_status(kind, "native_gold_fixtures_disabled", "native_blue_fixtures_disabled"));
        }
        Guid guid{};
        CubeInventory match;
        if (const auto error = cube_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (match.record)
        {
            return probe_result(lua, true, "present");
        }

        auto* defaults = match.cube_class->GetClassDefaultObject().Get();
        auto* stock_weight = field<Unreal::FDoubleProperty>(defaults, STR("Weight"));
        auto* stock_value = field<Unreal::FIntProperty>(defaults, STR("Value"));
        auto* stock_tag = field<Unreal::FNameProperty>(defaults, STR("DataTag"));
        auto* stock_data = field<Unreal::FStructProperty>(defaults, STR("DataStruct"));
        auto* stock_never_drop = field<Unreal::FBoolProperty>(defaults, STR("NeverDrop"));
        auto* item_weight = field<Unreal::FDoubleProperty>(match.item, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        auto* item_value = field<Unreal::FIntProperty>(match.item, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        auto* item_tag = field<Unreal::FNameProperty>(match.item, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        auto* item_data = field<Unreal::FStructProperty>(match.item, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        auto* item_never_drop = field<Unreal::FBoolProperty>(match.item, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        auto* data_type = item_data ? item_data->GetStruct().Get() : nullptr;
        if (!stock_weight || !stock_value || !stock_tag || !stock_data || !stock_never_drop ||
            !item_weight || !item_value || !item_tag || !data_type || !item_never_drop ||
            stock_data->GetStruct().Get() != data_type || data_type->GetPathName() != STR("/Script/StructUtils.InstancedStruct"))
        {
            return probe_result(lua, false, cube_status(kind, "stock_gold_defaults_unavailable", "stock_blue_defaults_unavailable"));
        }
        const auto weight = stock_weight->GetPropertyValueInContainer(defaults);
        const auto value = stock_value->GetPropertyValueInContainer(defaults);
        if (!std::isfinite(weight) || weight < 0 || value < 0)
        {
            return probe_result(lua, false, cube_status(kind, "invalid_stock_gold_defaults", "invalid_stock_blue_defaults"));
        }
        auto* instance = scope.instance.Get();
        const Unreal::FName carry_name{STR("Calc_CarryWeight")};
        auto* recalculate = stock_no_args(instance, carry_name);
        if (!recalculate)
        {
            return probe_result(lua, false, "stock_carry_weight_function_unavailable");
        }
        const GuidFields fields(match.item);
        Unreal::FScriptArrayHelper backpack(match.backpack, match.backpack->ContainerPtrToValuePtr<void>(match.save));
        if (backpack.Num() >= inventory_limit)
        {
            return probe_result(lua, false, "stock_inventory_size_out_of_range");
        }
        auto* item = backpack.GetRawPtr(backpack.AddValue());
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(item), match.cube_class);
        match.ammo->SetPropertyValueInContainer(item, -1);
        item_weight->SetPropertyValueInContainer(item, weight);
        item_value->SetPropertyValueInContainer(item, value);
        item_never_drop->SetPropertyValueInContainer(item, stock_never_drop->GetPropertyValueInContainer(defaults));
        item_tag->CopyCompleteValue(item_tag->ContainerPtrToValuePtr<void>(item), stock_tag->ContainerPtrToValuePtr<void>(defaults));
        item_data->CopyCompleteValue(item_data->ContainerPtrToValuePtr<void>(item), stock_data->ContainerPtrToValuePtr<void>(defaults));
        fields.write(item, guid);
        instance->ProcessEvent(recalculate, nullptr);
        return probe_result(lua, true, "seeded");
    }

    template <CubeKind kind>
    int lua_probe_cube(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !fixtures_enabled() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
        {
            return probe_result(lua, false, cube_status(kind, "gold_probe_fixture_required_on_game_thread",
                                                       "blue_probe_fixture_required_on_game_thread"));
        }
        Guid guid{};
        if (!parse_guid(lua.get_string(3), guid))
        {
            return probe_result(lua, false, cube_status(kind, "invalid_gold_guid", "invalid_blue_guid"));
        }
        const auto manager_address = lua.get_integer(2);
        if (manager_address <= 0 || !current_host_argument(lua.get_integer(1)))
        {
            return probe_result(lua, false, "current_transport_host_and_manager_required");
        }
        auto* manager = find_object(static_cast<std::uintptr_t>(manager_address), manager_path);
        if (!manager || !same_widget_player(manager))
        {
            return probe_result(lua, false, "current_local_item_manager_required");
        }
        auto* save = canonical_host_save();
        CubeInventory match;
        if (const auto error = locate_cube(save, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (!match.record)
        {
            return probe_result(lua, false, "owned_backpack_cube_required");
        }
        if (cube_request && request_current(*cube_request, save))
        {
            return probe_result(lua, false, pending_conflict(*cube_request, kind));
        }
        auto* held = field<Unreal::FStructProperty>(manager, STR("HeldItem"));
        const GuidFields fields(match.item);
        auto* function = stock_no_args(manager, entries->use_held);
        if (!RestoreItem::supports(held, match.item) || !function)
        {
            return probe_result(lua, false, "stock_held_item_schema_unavailable");
        }
        auto* value = held->ContainerPtrToValuePtr<void>(manager);
        if (object_value(match.item_class, value) || fields.read(value) != Guid{})
        {
            return probe_result(lua, false, "local_item_manager_is_holding_an_item");
        }
        const RestoreItem restore(held, value);

        // First prove this instance's stock entry is intercepted while the GUID is unusable.
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(value), match.cube_class);
        const auto before = receipt.count;
        manager->ProcessEvent(function, nullptr);
        if (receipt.count != before + 1 || receipt.role != "host" || receipt.item != cube_artifact(kind).name ||
            receipt.entry != "use_held_item" || receipt.reason != "coordinated_route_required")
        {
            return probe_result(lua, false, "stock_scratch_held_use_did_not_report_expected_denial");
        }
        held->CopyCompleteValue(value, match.record);
        manager->ProcessEvent(function, nullptr);
        CubeInventory after;
        const bool intact = locate_cube(canonical_host_save(), kind, guid, after).empty() && after.record &&
                            after.save == save && held->Identical(value, after.record);
        const bool queued = receipt.count == before + 2 && receipt.reason == queued_status(kind) && cube_request &&
                            cube_request->kind == kind && cube_request->guid == guid && request_current(*cube_request, save);
        if (!intact || !queued)
        {
            cube_request.reset();
            return probe_result(lua, false, intact ? cube_status(kind, "stock_gold_use_did_not_queue", "stock_blue_use_did_not_queue") :
                                                    "stock_backpack_item_was_changed");
        }
        return probe_result(lua, true, cube_status(kind, "stock_gold_use_queued", "stock_blue_use_queued"));
    }

    int lua_probe_gold_action(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !fixtures_enabled() || lua.get_stack_size() != 5 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_integer(4) || !lua.is_string(5))
        {
            return probe_result(lua, false, "gold_action_probe_widgets_required_on_game_thread");
        }
        Guid guid{};
        if (!parse_guid(lua.get_string(5), guid))
        {
            return probe_result(lua, false, "invalid_gold_guid");
        }
        const auto card_address = lua.get_integer(4);
        const auto panel_address = lua.get_integer(3);
        const auto manager_address = lua.get_integer(2);
        if (card_address <= 0 || panel_address <= 0 || manager_address <= 0 || !current_host_argument(lua.get_integer(1)))
        {
            return probe_result(lua, false, "current_transport_host_and_widgets_required");
        }

        auto* manager = find_object(static_cast<std::uintptr_t>(manager_address), manager_path);
        auto* panel = find_object(static_cast<std::uintptr_t>(panel_address), panel_path);
        auto* card = find_object(static_cast<std::uintptr_t>(card_address), card_path);
        auto* menu = object_field(manager, STR("ItemContextMenu"));
        if (!manager || !panel || !card || !live(menu) || !exact_class(menu, context_menu_path) ||
            !same_widget_player(manager) || !same_widget_player(panel) || !same_widget_player(card))
        {
            return probe_result(lua, false, "owned_stock_inventory_widgets_required");
        }

        auto* save = canonical_host_save();
        constexpr auto kind = CubeKind::Gold;
        CubeInventory match;
        if (const auto error = locate_cube(save, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (!match.record)
        {
            return probe_result(lua, false, "owned_backpack_cube_required");
        }
        if (cube_request && request_current(*cube_request, save))
        {
            return probe_result(lua, false, pending_conflict(*cube_request, kind));
        }

        auto* choose_card = field<Unreal::FObjectProperty>(menu, STR("Item Card"));
        auto* card_panel = field<Unreal::FObjectProperty>(card, STR("InventoryWidget"));
        auto* panel_target = field<Unreal::FObjectProperty>(panel, STR("Target"));
        auto* panel_manager = field<Unreal::FObjectProperty>(panel, STR("ItemManager"));
        auto* panel_index = field<Unreal::FIntProperty>(panel, STR("InventoryIndex"));
        auto* item = field<Unreal::FStructProperty>(card, STR("Item"));
        auto* function = stock_no_args(manager, entries->action_use);
        if (!choose_card || !card_panel || !panel_target || !panel_manager || !panel_index ||
            !RestoreItem::supports(item, match.item) || !function || panel_index->GetPropertyValueInContainer(panel) != 0)
        {
            return probe_result(lua, false, "stock_inventory_action_schema_unavailable");
        }

        const GuidFields fields(match.item);
        auto* value = item->ContainerPtrToValuePtr<void>(card);
        if (object_value(match.item_class, value) || fields.read(value) != Guid{} || object_value(choose_card, menu) ||
            object_value(card_panel, card) || object_value(panel_target, panel) || object_value(panel_manager, panel))
        {
            return probe_result(lua, false, "empty_scratch_inventory_widgets_required");
        }

        struct ScratchLinks
        {
            struct Link
            {
                Unreal::FObjectProperty* property;
                void* address;
                Unreal::UObject* target;
            };

            explicit ScratchLinks(std::array<Link, 4> links) : links(links)
            {
                for (const auto& link : links)
                {
                    link.property->SetObjectPropertyValue(link.address, link.target);
                }
            }

            ~ScratchLinks()
            {
                for (const auto& link : links)
                {
                    link.property->SetObjectPropertyValue(link.address, nullptr);
                }
            }

            std::array<Link, 4> links;
        };
        const ScratchLinks links(std::array{
            ScratchLinks::Link{panel_target, panel_target->ContainerPtrToValuePtr<void>(panel), scope.instance.Get()},
            ScratchLinks::Link{panel_manager, panel_manager->ContainerPtrToValuePtr<void>(panel), manager},
            ScratchLinks::Link{card_panel, card_panel->ContainerPtrToValuePtr<void>(card), panel},
            ScratchLinks::Link{choose_card, choose_card->ContainerPtrToValuePtr<void>(menu), card},
        });
        const RestoreItem restore(item, value);

        // Stock TakeCard looks up even zero GUIDs. Prove this hook using a GUID checked absent.
        auto unowned_guid = guid;
        unowned_guid.front() ^= 0x80000000U;
        if (unowned_guid == Guid{})
        {
            unowned_guid.back() = 1;
        }
        CubeInventory unowned;
        if (!locate_cube(save, kind, unowned_guid, unowned).empty() || unowned.record)
        {
            return probe_result(lua, false, "gold_action_canary_guid_not_absent");
        }
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(value), match.cube_class);
        fields.write(value, unowned_guid);
        const auto before = receipt.count;
        manager->ProcessEvent(function, nullptr);
        if (receipt.count != before + 1 || receipt.role != "host" || receipt.item != gold.name ||
            receipt.entry != "action_use" || receipt.reason != "owned_backpack_cube_required")
        {
            return probe_result(lua, false, "stock_scratch_inventory_action_did_not_report_expected_denial");
        }

        item->CopyCompleteValue(value, match.record);
        manager->ProcessEvent(function, nullptr);
        CubeInventory after;
        const bool intact = locate_cube(canonical_host_save(), kind, guid, after).empty() && after.record &&
                            after.save == save && item->Identical(value, after.record);
        const bool queued = receipt.count == before + 2 && receipt.reason == queued_status(kind) && cube_request &&
                            cube_request->kind == kind && cube_request->entry == "action_use" && cube_request->guid == guid &&
                            request_current(*cube_request, save);
        if (!intact || !queued)
        {
            cube_request.reset();
            return probe_result(lua, false, intact ? "stock_gold_action_use_did_not_queue" : "stock_backpack_item_was_changed");
        }
        return probe_result(lua, true, "stock_gold_action_use_queued");
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
        auto* class_property = field<Unreal::FClassProperty>(type, item_class_name);
        const GuidFields fields(type);
        auto* function = manager->GetFunctionByNameInChain(entries->use_held);
        auto* item_base = class_property ? class_property->GetMetaClass().Get() : nullptr;
        if (!item_class || !type || type->GetPathName() != item_path || !item_base || !item_class->IsChildOf(item_base) ||
            !fields.valid() || !function ||
            function->GetOuterPrivate() != manager->GetClassPrivate() || function->GetNumParms() != 0 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return probe_result(lua, false, "stock_held_item_schema_unavailable");
        }
        auto* record = held->ContainerPtrToValuePtr<void>(manager);
        auto* class_record = class_property->ContainerPtrToValuePtr<void>(record);
        if (class_property->GetObjectPropertyValue(class_record))
        {
            return probe_result(lua, false, "local_item_manager_is_holding_an_item");
        }
        if (fields.read(record) != Guid{})
        {
            return probe_result(lua, false, "local_held_item_guid_is_not_empty");
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
        cube_request.reset();
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
        lua.register_function("SilverfishTakeNativeGoldTransportRequest", lua_take_cube_request<CubeKind::Gold>);
        lua.register_function("SilverfishInspectNativeGoldTransport", lua_inspect_cube<CubeKind::Gold>);
        lua.register_function("SilverfishCommitNativeGoldTransport", lua_commit_cube<CubeKind::Gold>);
        lua.register_function("SilverfishTakeNativeBlueTransportRequest", lua_take_cube_request<CubeKind::Blue>);
        lua.register_function("SilverfishInspectNativeBlueTransport", lua_inspect_cube<CubeKind::Blue>);
        lua.register_function("SilverfishCommitNativeBlueTransport", lua_commit_cube<CubeKind::Blue>);
        if (fixtures_enabled())
        {
            lua.register_function("SilverfishSeedNativeGoldTransport", lua_seed_cube<CubeKind::Gold>);
            lua.register_function("SilverfishProbeNativeGoldTransport", lua_probe_cube<CubeKind::Gold>);
            lua.register_function("SilverfishProbeNativeGoldActionUse", lua_probe_gold_action);
            lua.register_function("SilverfishSeedNativeBlueTransport", lua_seed_cube<CubeKind::Blue>);
            lua.register_function("SilverfishProbeNativeBlueTransport", lua_probe_cube<CubeKind::Blue>);
        }
    }
}
