#include "transport_items.hpp"
#include "drop_item_class.hpp"
#include "guest_drop_maps.hpp"
#include "guest_weapon_registry.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <limits>
#include <optional>
#include <string>
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
    constexpr auto equipment_slot_path = STR("/Game/Classes/GUI/New/Components/ItemSlot.ItemSlot_C");
    constexpr auto context_menu_path = STR("/Game/Classes/GUI/New/Components/ItemContextMenu.ItemContextMenu_C");
    constexpr auto split_popup_path = STR("/Game/Classes/GUI/New/Components/SplitStackPopup.SplitStackPopup_C");
    constexpr auto inventory_screen_path = STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto consumable_path = STR("/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto item_class_name = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto item_guid_name = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr auto item_ammo_name = STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788");
    constexpr auto item_never_drop_name = STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D");
    constexpr auto item_weight_name = STR("Weight_12_11AB9ED547504E78A849A692A36DC938");
    constexpr auto item_value_name = STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr auto scope_lease = std::chrono::seconds(2);
    constexpr auto request_lease = std::chrono::seconds(2);
    constexpr auto host_drop_settlement_lease = std::chrono::seconds(45);
    constexpr std::int32_t max_drop_ammo = 200;
    constexpr std::int64_t max_drop_sequence = (std::int64_t{1} << 53) - 1;
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
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_FruitingDreamDust.Art_FruitingDreamDust_C"), "fruiting-stardust"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_Tesseract_Purple.Art_Tesseract_Purple_C"), "purple"},
        Artifact{STR("/Game/Classes/Items/Artifacts/Art_TeleportCube.Art_TeleportCube_C"), "teleport-cube"},
    };
    constexpr const auto& green = artifacts[1];
    constexpr const auto& blue = artifacts[2];
    constexpr const auto& red = artifacts[3];
    constexpr const auto& gold = artifacts[4];
    constexpr const auto& stardust = artifacts.front();
    constexpr const auto& fruiting_stardust = artifacts[5];
    constexpr const auto& purple = artifacts[6];
    constexpr const auto& teleport_cube = artifacts.back();

    enum class TransportKind { Gold, Blue, Stardust, Red, Green, FruitingStardust, Purple, Matrix };
    // Equipment input uses the Matrix-specific path and never enters the ItemManager consumable queue.
    constexpr std::array transport_artifacts{&gold, &blue, &stardust, &red, &green, &fruiting_stardust, &purple};

    [[nodiscard]] constexpr const Artifact& transport_artifact(TransportKind kind)
    {
        return *transport_artifacts[static_cast<std::size_t>(kind)];
    }

    [[nodiscard]] constexpr std::string_view transport_status(TransportKind kind, std::string_view gold_status,
                                                               std::string_view blue_status, std::string_view stardust_status,
                                                               std::string_view red_status, std::string_view green_status,
                                                               std::string_view fruiting_status, std::string_view purple_status)
    {
        return std::array{gold_status, blue_status, stardust_status, red_status, green_status, fruiting_status,
                          purple_status}[static_cast<std::size_t>(kind)];
    }

    [[nodiscard]] constexpr bool stackable_transport(TransportKind kind)
    {
        return kind == TransportKind::Stardust || kind == TransportKind::FruitingStardust;
    }

    [[nodiscard]] constexpr std::string_view stack_status(TransportKind kind, std::string_view stardust_status,
                                                          std::string_view fruiting_status)
    {
        return kind == TransportKind::FruitingStardust ? fruiting_status : stardust_status;
    }

    [[nodiscard]] constexpr std::string_view queued_status(TransportKind kind)
    {
        return transport_status(kind, "gold_request_queued", "blue_request_queued", "stardust_request_queued",
                                "red_request_queued", "green_request_queued", "fruiting_stardust_request_queued", "purple_request_queued");
    }

    [[nodiscard]] constexpr std::string_view owned_backpack_required(TransportKind kind)
    {
        return stackable_transport(kind) ? stack_status(kind, "owned_backpack_stardust_required", "owned_backpack_fruiting_stardust_required") :
                                           "owned_backpack_cube_required";
    }

    struct Entries
    {
        Unreal::FName drop_item{STR("DropItem")};
        Unreal::FName action_drop{STR("ActionDrop")};
        Unreal::FName action_equip{STR("ActionEquip")};
        Unreal::FName insert_item{STR("InsertItem")};
        Unreal::FName action_use{STR("ActionUse")};
        Unreal::FName sort_items{STR("SortItems")};
        Unreal::FName final_split{STR("FinalSplitStack")};
        Unreal::FName position_split{STR("PositionSplitStackPopup")};
        Unreal::FName quick_move{STR("QuickMove")};
        Unreal::FName use_held{STR("UseHeldItem")};
        Unreal::FName confirm_use{STR("ConfirmUseItem")};
        Unreal::FName use_primary{STR("Use_Item_Primary")};
        Unreal::FName use_secondary{STR("Use_Item_Secondary")};
        Unreal::FName teleport{STR("Teleport")};
        Unreal::FName matrix_script{STR("ExecuteUbergraph_Art_TeleportCube")};
        Unreal::FName apply_effects{STR("ApplyItemEffects")};
        std::array<Unreal::FName, 10> guarded_inventory_actions{
            Unreal::FName{STR("ActionPickup")}, Unreal::FName{STR("ActionQuickMove")},
            Unreal::FName{STR("ActionUnequip")}, Unreal::FName{STR("PickUpAction")},
            Unreal::FName{STR("InitiateItemTake")}, Unreal::FName{STR("FinalTakeItem")},
            Unreal::FName{STR("EquipToNumberSlot")}, Unreal::FName{STR("QuickSplitStack")},
            Unreal::FName{STR("InsertItem")}, Unreal::FName{STR("CashHeldMoney")},
        };
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

    struct TransportRequest
    {
        TransportKind kind;
        Guid guid;
        std::uint64_t generation;
        Clock::time_point expires;
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr save;
        std::string_view entry;
        Clock::time_point ready_after{};
        Unreal::FWeakObjectPtr equipped_actor{};
    };

    struct MatrixInput
    {
        std::uint64_t generation{};
        Unreal::FWeakObjectPtr equipment;
    };

    struct HostDropScope
    {
        std::array<char, 32> epoch{};
        Unreal::FWeakObjectPtr pawn, world, save;
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool stock_inventory{};
    };

    struct HostDropRequest
    {
        Guid guid;
        std::int32_t count;
        std::int64_t sequence;
        bool taken;
        Clock::time_point expires;
        Unreal::FWeakObjectPtr manager, screen, panel;
        std::string_view entry;
        std::int32_t expected{};
    };

    std::optional<Entries> entries;
    std::array<Hook::GlobalCallbackId, 3> hooks{};
    std::atomic<bool> hooks_ready{};
    std::atomic<std::uint64_t> generation{1};
    thread_local Scope scope;
    thread_local Receipt receipt;
    thread_local std::optional<TransportRequest> transport_request;
    thread_local std::array<MatrixInput, 2> matrix_inputs;
    thread_local HostDropScope host_drop_scope;
    thread_local std::optional<HostDropRequest> host_drop_request;
    thread_local std::int64_t host_drop_sequence{};

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

    [[nodiscard]] bool host_drop_enabled()
    {
        std::array<char, 2> flag{};
        std::size_t size{};
        return getenv_s(&size, flag.data(), flag.size(), "SILVERFISH_COOP_NATIVE_GUEST_SAVE") == 0 &&
               size == flag.size() && flag[0] == '1';
    }

    void clear_host_drop()
    {
        host_drop_scope = {};
        host_drop_request.reset();
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

    void clear_matrix_input()
    {
        matrix_inputs = {};
        if (transport_request && transport_request->kind == TransportKind::Matrix) transport_request.reset();
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
            if (now >= scope.expires) clear_matrix_input();
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
        clear_matrix_input();
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
        if (object_field(pawn, STR("EquipedItem")) == actor || object_field(pawn, STR("HeldObject")) == actor ||
            object_field(pawn, STR("OffhandEquipment")) == actor)
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

    struct TransportInventory
    {
        Unreal::UObject* save{};
        Unreal::FArrayProperty* backpack{};
        Unreal::UScriptStruct* item{};
        Unreal::FClassProperty* item_class{};
        Unreal::FIntProperty* ammo{};
        Unreal::UClass* transport_class{};
        Unreal::UObject* defaults{};
        void* record{};
        int index{-1};
        std::int32_t quantity{};
    };

    [[nodiscard]] bool item_struct(Unreal::FProperty* property, const Unreal::UScriptStruct* item)
    {
        const auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
        return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == item;
    }

    [[nodiscard]] std::string_view locate_transport(Unreal::UObject* save, TransportKind kind, const Guid& guid, TransportInventory& match)
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
        match.transport_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, transport_artifact(kind).class_path);
        match.defaults = match.transport_class ? match.transport_class->GetClassDefaultObject().Get() : nullptr;
        auto* stacks = field<Unreal::FBoolProperty>(match.defaults, STR("Stacks"));
        auto* expected_ammo = field<Unreal::FIntProperty>(match.defaults, STR("Ammo"));
        auto* item_base = match.item_class ? match.item_class->GetMetaClass().Get() : nullptr;
        const bool stackable = stackable_transport(kind);
        if (!fields.valid() || !item_base || !match.ammo || !stacks || !expected_ammo || !match.transport_class->IsChildOf(item_base) ||
            stacks->GetPropertyValueInContainer(match.defaults) != stackable ||
            expected_ammo->GetPropertyValueInContainer(match.defaults) != (stackable ? 1 : -1))
        {
            return stackable ? stack_status(kind, "stock_stardust_stack_schema_unavailable", "stock_fruiting_stardust_stack_schema_unavailable") :
                               "stock_single_cube_schema_unavailable";
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
            const auto ammo = match.ammo->GetPropertyValueInContainer(row);
            if (match.record || object_value(match.item_class, row) != match.transport_class || (stackable ? ammo <= 0 : ammo != -1))
            {
                return "host_backpack_guid_conflict";
            }
            match.index = index;
            match.record = row;
            match.quantity = stackable ? ammo : 1;
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

    struct MatrixInventory
    {
        Unreal::UClass* type{};
        Unreal::UScriptStruct* row_type{};
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        const void* row{};
        int backpack_index{-1};
        int equipped_index{-1};
    };

    [[nodiscard]] std::string_view bind_matrix_inventory(Unreal::UObject* save, MatrixInventory& inventory)
    {
        if (!save) return "canonical_host_save_required";
        auto* backpack = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
        auto* equipment = field<Unreal::FArrayProperty>(save, STR("PlayerEquipment"));
        auto* structure = backpack ? Unreal::CastField<Unreal::FStructProperty>(backpack->GetInner()) : nullptr;
        auto* row_type = structure ? structure->GetStruct().Get() : nullptr;
        auto* matrix_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, teleport_cube.class_path);
        if (!row_type || row_type->GetPathName() != item_path || structure->GetArrayDim() != 1 ||
            !equipment || !item_struct(equipment->GetInner(), row_type) || !matrix_class)
            return "stock_matrix_inventory_schema_unavailable";

        inventory.type = matrix_class;
        inventory.row_type = row_type;
        inventory.backpack = backpack;
        inventory.equipment = equipment;
        return {};
    }

    [[nodiscard]] std::string_view locate_matrix(Unreal::UObject* save, const Guid& guid, MatrixInventory& inventory)
    {
        if (const auto error = bind_matrix_inventory(save, inventory); !error.empty()) return error;
        auto* helmet = field<Unreal::FStructProperty>(save, STR("PlayerHelmet"));
        auto* armor = field<Unreal::FStructProperty>(save, STR("PlayerArmor"));
        auto* item_class = field<Unreal::FClassProperty>(inventory.row_type, item_class_name);
        const GuidFields fields(inventory.row_type);
        if (!item_class || !item_struct(helmet, inventory.row_type) || !item_struct(armor, inventory.row_type) || !fields.valid())
            return "stock_matrix_inventory_schema_unavailable";

        for (auto* property : {inventory.backpack, inventory.equipment})
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(save));
            if (rows.Num() > inventory_limit) return "stock_inventory_size_out_of_range";
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto* row = rows.GetRawPtr(index);
                if (fields.read(row) != guid) continue;
                if (inventory.row || object_value(item_class, row) != inventory.type) return "host_matrix_guid_conflict";

                inventory.row = row;
                if (property == inventory.equipment) inventory.equipped_index = index;
                else inventory.backpack_index = index;
            }
        }
        if (fields.read(helmet->ContainerPtrToValuePtr<void>(save)) == guid ||
            fields.read(armor->ContainerPtrToValuePtr<void>(save)) == guid)
            return "host_matrix_guid_conflict";
        return {};
    }

    [[nodiscard]] std::string_view equipped_matrix_guid(Unreal::UObject* save, Unreal::UObject* actor, Guid& guid)
    {
        auto* pawn = scope.pawn.Get();
        if (!live(actor) || !exact_class(actor, teleport_cube.class_path) ||
            object_field(pawn, STR("EquipedItem")) != actor || object_field(actor, STR("Player_Ref")) != pawn ||
            object_field(actor, STR("Owner")) != pawn || object_field(actor, STR("Instigator")) != pawn ||
            static_cast<Unreal::AActor*>(actor)->GetWorld() != scope.world.Get())
            return "stock_equipped_matrix_required";

        MatrixInventory inventory;
        if (const auto error = bind_matrix_inventory(save, inventory); !error.empty()) return error;
        auto* selected = field<Unreal::FIntProperty>(save, STR("PlayerEquipIndex"));
        const auto index = selected ? selected->GetPropertyValueInContainer(save) : -1;
        Unreal::FScriptArrayHelper equipment(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(save));
        if (index < 0 || index >= equipment.Num() || equipment.Num() > inventory_limit)
            return "stock_equipped_matrix_required";

        const GuidFields fields(inventory.row_type);
        auto* item_class = field<Unreal::FClassProperty>(inventory.row_type, item_class_name);
        const auto* row = equipment.GetRawPtr(index);
        if (!fields.valid() || !item_class || object_value(item_class, row) != actor->GetClassPrivate())
            return "stock_equipped_matrix_required";
        guid = fields.read(row);
        if (guid == Guid{}) return "owned_equipped_matrix_guid_required";

        MatrixInventory unique;
        if (const auto error = locate_matrix(save, guid, unique); !error.empty()) return error;
        return unique.row == row && unique.equipped_index == index ? "" : "owned_equipped_matrix_guid_required";
    }

    [[nodiscard]] bool exact_backpack_panel(Unreal::UObject* panel, Unreal::UObject* manager)
    {
        auto* index = exact_class(panel, panel_path) ? field<Unreal::FIntProperty>(panel, STR("InventoryIndex")) : nullptr;
        return index && index->GetPropertyValueInContainer(panel) == 0 &&
               object_field(panel, STR("Target")) == scope.instance.Get() && object_field(panel, STR("ItemManager")) == manager;
    }

    [[nodiscard]] bool array_has_widget(Unreal::UObject* owner, const Unreal::TCHAR* name, Unreal::UObject* widget,
                                        bool top_only)
    {
        auto* array = field<Unreal::FArrayProperty>(owner, name);
        auto* element = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!element || element->GetArrayDim() != 1 || !live(widget)) return false;
        Unreal::FScriptArrayHelper rows(array, array->ContainerPtrToValuePtr<void>(owner));
        if (rows.Num() < 1 || rows.Num() > inventory_limit) return false;
        for (int index = top_only ? rows.Num() - 1 : 0; index < rows.Num(); ++index)
        {
            if (object_value(element, rows.GetRawPtr(index)) == widget) return true;
        }
        return false;
    }

    [[nodiscard]] bool exact_drop_screen(Unreal::UObject* manager, Unreal::UObject* screen, Unreal::UObject* panel)
    {
        auto* floor = object_field(screen, STR("FloorPanel"));
        return live(manager) && exact_class(manager, manager_path) && live(screen) &&
               exact_class(screen, inventory_screen_path) && live(panel) && exact_backpack_panel(panel, manager) &&
               live(floor) && exact_class(floor, panel_path) && floor != panel &&
               object_field(manager, STR("ParentScreen")) == screen && object_field(screen, STR("ItemManager")) == manager &&
               object_field(screen, STR("BackpackPanel")) == panel && object_field(manager, STR("DropLocation")) == floor &&
               array_has_widget(scope.controller.Get(), STR("MenuStack"), screen, true) &&
               same_widget_player(manager) && same_widget_player(screen) && same_widget_player(panel);
    }

    [[nodiscard]] Unreal::UObject* top_stock_menu()
    {
        auto* controller = scope.controller.Get();
        auto* menus = field<Unreal::FArrayProperty>(controller, STR("MenuStack"));
        auto* element = menus ? Unreal::CastField<Unreal::FObjectProperty>(menus->GetInner()) : nullptr;
        if (!element || element->GetArrayDim() != 1) return nullptr;
        Unreal::FScriptArrayHelper stack(menus, menus->ContainerPtrToValuePtr<void>(controller));
        return stack.Num() > 0 && stack.Num() <= inventory_limit ? object_value(element, stack.GetRawPtr(stack.Num() - 1))
                                                               : nullptr;
    }

    [[nodiscard]] bool qualified_drop_seat(bool host)
    {
        return (!host || host_drop_enabled()) && scope.host == host &&
               scope.generation == generation.load(std::memory_order_acquire) && current_player(scope) &&
               silverfish::native_guest_drop_maps::contains(static_cast<Unreal::UWorld*>(scope.world.Get()));
    }

    [[nodiscard]] bool host_drop_scope_matches(Unreal::UObject* save)
    {
        return live(save) && host_drop_scope.save.Get() == save && host_drop_scope.pawn.Get() == scope.pawn.Get() &&
               host_drop_scope.world.Get() == scope.world.Get() && host_drop_scope.generation == scope.generation;
    }

    [[nodiscard]] bool drop_epoch_valid(std::string_view epoch)
    {
        return epoch.size() == host_drop_scope.epoch.size() &&
               std::ranges::all_of(epoch, [](char digit) {
                   return (digit >= '0' && digit <= '9') || (digit >= 'a' && digit <= 'f');
               });
    }

    [[nodiscard]] bool drop_argument(std::int64_t pawn, std::string_view epoch, bool host)
    {
        return drop_epoch_valid(epoch) && std::ranges::equal(epoch, host_drop_scope.epoch) &&
               pawn > 0 && scope.address == static_cast<std::uintptr_t>(pawn) &&
               hooks_ready.load(std::memory_order_acquire) && qualified_drop_seat(host) && Clock::now() < scope.expires &&
               host_drop_scope_matches(canonical_host_save()) &&
               Clock::now() < host_drop_scope.expires;
    }

    [[nodiscard]] bool exact_canonical_drop_row(Unreal::UObject* save, const ItemView& card,
                                                const Guid& guid, std::int32_t saved_ammo, Unreal::UObject* item_class,
                                                std::optional<int> equipped_index = {})
    {
        auto* backpack = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
        auto* equipment = field<Unreal::FArrayProperty>(save, STR("PlayerEquipment"));
        auto* armor = field<Unreal::FStructProperty>(save, STR("PlayerArmor"));
        auto* helmet = field<Unreal::FStructProperty>(save, STR("PlayerHelmet"));
        auto* count_field = field<Unreal::FIntProperty>(card.structure, item_ammo_name);
        auto* class_field = field<Unreal::FClassProperty>(card.structure, item_class_name);
        auto* never_drop = field<Unreal::FBoolProperty>(card.structure, item_never_drop_name);
        const GuidFields fields(card.structure);
        if (!backpack || !equipment || !item_struct(backpack->GetInner(), card.structure) ||
            !item_struct(equipment->GetInner(), card.structure) || !item_struct(armor, card.structure) ||
            !item_struct(helmet, card.structure) || !fields.valid() || !count_field || !class_field || !never_drop ||
            never_drop->GetPropertyValueInContainer(card.value))
            return false;

        Unreal::FScriptArrayHelper pack(backpack, backpack->ContainerPtrToValuePtr<void>(save));
        Unreal::FScriptArrayHelper loadout(equipment, equipment->ContainerPtrToValuePtr<void>(save));
        if (pack.Num() < 0 || pack.Num() > inventory_limit || loadout.Num() < 0 || loadout.Num() > inventory_limit)
            return false;

        const void* found{};
        const auto verify_rows = [&](Unreal::FScriptArrayHelper& rows, bool equipped) {
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto* row = rows.GetRawPtr(index);
                if (fields.read(row) != guid) continue;
                if (found || equipped != equipped_index.has_value() || (equipped && index != *equipped_index) ||
                    object_value(class_field, row) != item_class ||
                    count_field->GetPropertyValueInContainer(row) != saved_ammo || never_drop->GetPropertyValueInContainer(row))
                    return false;
                found = row;
            }
            return true;
        };
        return verify_rows(pack, false) && verify_rows(loadout, true) && found &&
               fields.read(armor->ContainerPtrToValuePtr<void>(save)) != guid &&
               fields.read(helmet->ContainerPtrToValuePtr<void>(save)) != guid;
    }

    [[nodiscard]] Unreal::UFunction* stock_no_args(Unreal::UObject* object, const Unreal::FName& name);

    [[nodiscard]] std::optional<int> drop_equipment_index(Unreal::UObject* manager, Unreal::UObject* slot)
    {
        if (!exact_class(slot, equipment_slot_path) || !same_widget_player(slot) ||
            object_field(slot, STR("Target")) != scope.pawn.Get() ||
            object_field(slot, STR("ItemManager")) != manager || !array_has_widget(manager, STR("EquipSlots"), slot, false))
            return {};
        auto* index = field<Unreal::FIntProperty>(slot, STR("ItemIndex"));
        const auto value = index ? index->GetPropertyValueInContainer(slot) : -1;
        return value >= 0 && value < 8 ? std::optional{value} : std::nullopt;
    }

    [[nodiscard]] bool pack_equipment_drop(Unreal::UObject* manager, Unreal::UObject* pack, Unreal::UObject* slot,
                                           const Guid& guid, Unreal::UObject* item_class)
    {
        auto* insert = manager->GetClassPrivate()->GetFunctionByNameInChain(entries->insert_item);
        auto* widget = field<Unreal::FObjectProperty>(insert, STR("InventoryWidget"));
        auto* index = field<Unreal::FIntProperty>(insert, STR("Index"));
        const auto held = inventory_item(manager, STR("HeldItem"));
        const GuidFields fields(held.structure);
        if (!insert || insert->GetOuterPrivate() != manager->GetClassPrivate() || insert->GetNumParms() != 2 ||
            insert->GetReturnProperty() || insert->GetParmsSize() > 16 ||
            insert->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !widget || widget->GetOffset_Internal() != 0 || !index || index->GetOffset_Internal() != 8 ||
            !fields.valid() || fields.read(held.value) != guid || object_field(manager, STR("ActiveReturnLocation")) != slot ||
            object_value(field<Unreal::FClassProperty>(held.structure, item_class_name), held.value) != item_class)
            return false;
        alignas(void*) std::array<std::byte, 16> arguments{};
        widget->SetObjectPropertyValue(widget->ContainerPtrToValuePtr<void>(arguments.data()), pack);
        index->SetPropertyValueInContainer(arguments.data(), -1);
        manager->ProcessEvent(insert, arguments.data());
        const auto after = inventory_item(manager, STR("HeldItem"));
        return after.structure == held.structure &&
               !object_value(field<Unreal::FClassProperty>(after.structure, item_class_name), after.value);
    }

    void intercept_drag_drop(Iteration& iteration, Unreal::UObject* manager, Unreal::UFunction* function,
                             const void* parameters)
    {
        if (!parameters || !live(manager) || !exact_class(manager, manager_path) || !qualified_drop_seat(true) ||
            function->GetOuterPrivate() != manager->GetClassPrivate() || function->GetNumParms() != 2 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* screen = object_field(manager, STR("ParentScreen"));
        auto* panel = object_field(screen, STR("BackpackPanel"));
        auto* floor = object_field(screen, STR("FloorPanel"));
        auto* origin = object_field(manager, STR("ActiveReturnLocation"));
        const auto equipped_index = drop_equipment_index(manager, origin);
        if (!exact_drop_screen(manager, screen, panel) ||
            object_value(field<Unreal::FObjectProperty>(function, STR("InventoryWidget")), parameters) != floor ||
            (origin != panel && !equipped_index)) return;

        const auto held = inventory_item(manager, STR("HeldItem"));
        auto* item_class = object_value(field<Unreal::FClassProperty>(held.structure, item_class_name), held.value);
        const auto stacks = silverfish::drop_item_class::stacks(Unreal::Cast<Unreal::UClass>(item_class));
        if (!stacks) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        const auto now = Clock::now();
        auto* save = canonical_host_save();
        auto* allow = field<Unreal::FBoolProperty>(manager, STR("CanDropItems"));
        auto* dead = field<Unreal::FBoolProperty>(scope.pawn.Get(), STR("Dead"));
        const GuidFields fields(held.structure);
        auto* count_field = field<Unreal::FIntProperty>(held.structure, item_ammo_name);
        auto* never_drop = field<Unreal::FBoolProperty>(held.structure, item_never_drop_name);
        auto* restore = manager->GetClassPrivate()->GetFunctionByNameInChain(Unreal::FName(STR("ReturnItem")));
        auto* clear = stock_no_args(manager, Unreal::FName(STR("ClearHeldItem")));
        auto* item = field<Unreal::FStructProperty>(restore, STR("Item"));
        auto* success = field<Unreal::FBoolProperty>(restore, STR("Success"));
        if (!host_drop_scope_matches(save) || now >= host_drop_scope.expires ||
            (host_drop_request && now < host_drop_request->expires) || host_drop_sequence >= max_drop_sequence ||
            !allow || !allow->GetPropertyValueInContainer(manager) || !dead || dead->GetPropertyValueInContainer(scope.pawn.Get()) ||
            !fields.valid() || !count_field || !never_drop || never_drop->GetPropertyValueInContainer(held.value) ||
            !clear || !restore || restore->GetOuterPrivate() != manager->GetClassPrivate() ||
            restore->GetNumParms() != 2 || restore->GetReturnProperty() || restore->GetParmsSize() > 96 ||
            restore->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !item_struct(item, held.structure) || item->GetOffset_Internal() != 0 || item->GetSize() != 0x50 ||
            !success || success->GetOffset_Internal() != 0x50) return;

        const Guid guid = fields.read(held.value);
        const auto saved_ammo = count_field->GetPropertyValueInContainer(held.value);
        if (guid == Guid{} || (*stacks && (saved_ammo < 1 || saved_ammo > max_drop_ammo))) return;
        if (equipped_index && !*stacks && pack_equipment_drop(manager, panel, origin, guid, item_class))
        {
            if (exact_canonical_drop_row(save, held, guid, saved_ammo, item_class))
                host_drop_request = HostDropRequest{guid, 1, ++host_drop_sequence, false,
                    now + request_lease, manager, screen, panel, "equipped_drag_to_floor"};
            return;
        }
        if (equipped_index) return;
        // Stock drag has already taken the item from the backpack. Restore that
        // stock row before the coordinator captures and debits its exact GUID.
        alignas(std::max_align_t) std::array<std::byte, 96> arguments{};
        item->InitializeValue(arguments.data());
        item->CopyCompleteValue(arguments.data(), held.value);
        manager->ProcessEvent(restore, arguments.data());
        const bool returned = success->GetPropertyValueInContainer(arguments.data());
        const ItemView original{held.artifact, held.structure, arguments.data()};
        const bool exact_return = returned && exact_canonical_drop_row(save, original, guid, saved_ammo, item_class);
        item->DestroyValue(arguments.data());
        if (!returned) return;
        manager->ProcessEvent(clear, nullptr);
        if (!exact_return) return;
        host_drop_request = HostDropRequest{guid, *stacks ? saved_ammo : 1, ++host_drop_sequence, false,
                                           now + request_lease, manager, screen, panel, "drag_to_floor"};
    }

    [[nodiscard]] std::string_view stock_backpack_use(Unreal::UObject* item_class)
    {
        if (!item_class) return {};
        const auto path = item_class->GetPathName();
        if (path == STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C")) return "use-bandage";
        if (path == STR("/Game/Classes/Items/Item_MRE.Item_MRE_C")) return "use-mre";
        if (path == STR("/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C")) return "use-canned-peaches";
        if (path == STR("/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C")) return "use-canned-beans";
        if (path == STR("/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C")) return "use-brown-mushroom";
        return {};
    }

    void intercept_backpack_sort(Iteration& iteration, Unreal::UObject* instance, Unreal::UFunction* function)
    {
        if (instance != scope.instance.Get() || !qualified_drop_seat(false) || !exact_class(instance, instance_path) ||
            function->GetOuterPrivate() != instance->GetClassPrivate() || function->GetNumParms() != 1 ||
            function->GetParmsSize() != 1 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* kind = field<Unreal::FByteProperty>(function, STR("SortType"));
        if (!kind || kind->GetOffset_Internal() != 0 || !kind->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            kind->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm)) return;
        if (!host_drop_scope_matches(canonical_host_save())) return;
        // The widget can request auto-sort while it is still being constructed.
        if (host_drop_scope.stock_inventory) iteration.PreventOriginalFunctionCall();
        auto* screen = top_stock_menu();
        auto* manager = object_field(screen, STR("ItemManager"));
        auto* panel = object_field(screen, STR("BackpackPanel"));
        if (!exact_drop_screen(manager, screen, panel)) return;

        iteration.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        auto* dead = field<Unreal::FBoolProperty>(scope.pawn.Get(), STR("Dead"));
        const auto held = inventory_item(manager, STR("HeldItem"));
        auto* held_class = field<Unreal::FClassProperty>(held.structure, item_class_name);
        if (!iteration.OriginalFunctionCallPrevented() || now >= host_drop_scope.expires ||
            (host_drop_request && now < host_drop_request->expires) || host_drop_sequence >= max_drop_sequence ||
            !dead || dead->GetPropertyValueInContainer(scope.pawn.Get()) || !held_class ||
            object_value(held_class, held.value)) return;
        // The cooked GameInstance uses its category sort for every SortType.
        host_drop_request = HostDropRequest{{}, 0, ++host_drop_sequence, false, now + request_lease,
                                            manager, screen, panel, "sort"};
    }

    void intercept_backpack_split(Iteration& iteration, Unreal::UObject* manager, Unreal::UFunction* function,
                                  const void* parameters)
    {
        if (!qualified_drop_seat(false) || !exact_class(manager, manager_path) ||
            function->GetOuterPrivate() != manager->GetClassPrivate() || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return;
        auto* screen = object_field(manager, STR("ParentScreen"));
        auto* panel = object_field(screen, STR("BackpackPanel"));
        auto* save = canonical_host_save();
        if (!exact_drop_screen(manager, screen, panel) || !host_drop_scope_matches(save)) return;

        if (function->GetNamePrivate() == entries->position_split)
        {
            std::array<char, 2> flag{};
            std::size_t size{};
            if (function->GetNumParms() == 0 &&
                getenv_s(&size, flag.data(), flag.size(), "SILVERFISH_COOP_HEADLESS") == 0 &&
                size == flag.size() && flag[0] == '1') iteration.PreventOriginalFunctionCall();
            return;
        }

        auto* amount = field<Unreal::FIntProperty>(function, STR("AmountToTake"));
        if (function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(std::int32_t) || !amount ||
            amount->GetOffset_Internal() != 0 || !amount->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            amount->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm)) return;
        iteration.PreventOriginalFunctionCall();
        const auto now = Clock::now();
        auto* dead = field<Unreal::FBoolProperty>(scope.pawn.Get(), STR("Dead"));
        auto* splitting = field<Unreal::FBoolProperty>(manager, STR("IsSplittingStack"));
        const auto held = inventory_item(manager, STR("HeldItem"));
        auto* held_class = field<Unreal::FClassProperty>(held.structure, item_class_name);
        auto* popup = object_field(manager, STR("SplitStackPopup"));
        if (!iteration.OriginalFunctionCallPrevented() || !parameters || now >= host_drop_scope.expires ||
            (host_drop_request && now < host_drop_request->expires) || host_drop_sequence >= max_drop_sequence ||
            !dead || dead->GetPropertyValueInContainer(scope.pawn.Get()) || !splitting ||
            !splitting->GetPropertyValueInContainer(manager) || !held_class || object_value(held_class, held.value) ||
            !exact_class(popup, split_popup_path) || !same_widget_player(popup)) return;

        const auto take = amount->GetPropertyValueInContainer(parameters);
        auto* count = field<Unreal::FIntProperty>(popup, STR("Count"));
        auto* card = object_field(popup, STR("ItemCard"));
        if (take < 1 || !count || count->GetPropertyValueInContainer(popup) != take ||
            !exact_class(card, card_path) || !same_widget_player(card) ||
            object_field(card, STR("ItemManager")) != manager || !array_has_widget(panel, STR("Cards"), card, false)) return;
        const auto candidate = card_item(card);
        const GuidFields fields(candidate.structure);
        auto* units = field<Unreal::FIntProperty>(candidate.structure, item_ammo_name);
        auto* type = object_value(field<Unreal::FClassProperty>(candidate.structure, item_class_name), candidate.value);
        if (candidate.panel != panel || !fields.valid() || !units || !type) return;
        namespace profiles = silverfish::guest_weapon_profiles;
        const auto path = type->GetPathName();
        if (path != profiles::nine_mm_ball.item && path != profiles::forty_five_ball.item &&
            path != profiles::twelve_gauge_buck.item) return;
        const auto guid = fields.read(candidate.value);
        const auto expected = units->GetPropertyValueInContainer(candidate.value);
        if (guid == Guid{} || take >= expected || !exact_canonical_drop_row(save, candidate, guid, expected, type)) return;
        host_drop_request = HostDropRequest{guid, take, ++host_drop_sequence, false, now + request_lease,
                                            manager, screen, panel, "split", expected};
    }

    void intercept_backpack_action(Iteration& iteration, Unreal::UObject* manager, Unreal::UFunction* function,
                                   bool from_menu, std::string_view action = {})
    {
        if (function->GetNumParms() != 0 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !live(manager) || !exact_class(manager, manager_path) ||
            function->GetOuterPrivate() != manager->GetClassPrivate() || !qualified_drop_seat(scope.host)) return;

        auto* screen = object_field(manager, STR("ParentScreen"));
        auto* panel = object_field(screen, STR("BackpackPanel"));
        if (!exact_drop_screen(manager, screen, panel)) return;

        auto* save = canonical_host_save();
        const auto now = Clock::now();
        const bool matches = host_drop_scope_matches(save);
        const bool bound = matches && now < host_drop_scope.expires;
        // A guest scope always routes drops through the host, including a
        // refused card. No stock mutation may proceed on the projected save.
        if (!scope.host)
        {
            if (!matches) return;
            iteration.PreventOriginalFunctionCall();
            if (!iteration.OriginalFunctionCallPrevented()) return;
        }
        auto* card = object_field(manager, STR("HoveredCard"));
        Unreal::UObject* menu = nullptr;
        if (from_menu)
        {
            menu = object_field(manager, STR("ItemContextMenu"));
            if (!live(menu) || !exact_class(menu, context_menu_path)) return;
            card = object_field(menu, STR("Item Card"));
        }
        if (!exact_class(card, card_path)) return;
        const auto candidate = card_item(card);
        const auto equipped_index = drop_equipment_index(manager, candidate.panel);
        auto* item_class = object_value(field<Unreal::FClassProperty>(candidate.structure, item_class_name), candidate.value);
        if ((candidate.panel != panel && !equipped_index) || !item_class) return;
        const auto stacks = action.empty() ? silverfish::drop_item_class::stacks(Unreal::Cast<Unreal::UClass>(item_class))
                                        : std::optional<bool>{false};
        if (!stacks) return;
        const bool stack = *stacks;

        // Suppress stock TakeCard even when the selected row is stale or a request is already pending.
        if (scope.host)
        {
            iteration.PreventOriginalFunctionCall();
            if (!iteration.OriginalFunctionCallPrevented()) return;
        }

        auto* allow = field<Unreal::FBoolProperty>(manager, STR("CanDropItems"));
        auto* dead = field<Unreal::FBoolProperty>(scope.pawn.Get(), STR("Dead"));
        const bool using_item = action.starts_with("use-");
        auto* underwater = using_item ? field<Unreal::FBoolProperty>(scope.pawn.Get(), STR("HeadUnderWater")) : nullptr;
        const auto held = inventory_item(manager, STR("HeldItem"));
        auto* held_class = field<Unreal::FClassProperty>(held.structure, item_class_name);
        if (!bound || (!scope.host && equipped_index) ||
            (host_drop_request && now < host_drop_request->expires) || host_drop_sequence >= max_drop_sequence ||
            (action.empty() && (!allow || !allow->GetPropertyValueInContainer(manager))) ||
            (using_item && (!underwater || underwater->GetPropertyValueInContainer(scope.pawn.Get()))) ||
            !dead || dead->GetPropertyValueInContainer(scope.pawn.Get()) ||
            (from_menu && (object_field(menu, STR("ItemManager")) != manager || !same_widget_player(menu))) ||
            !held_class || object_value(held_class, held.value) ||
            !live(card) || !same_widget_player(card) || object_field(card, STR("ItemManager")) != manager ||
            (equipped_index ? object_field(candidate.panel, STR("ItemCard")) != card :
             !array_has_widget(panel, STR("Cards"), card, false)))
            return;

        const GuidFields fields(candidate.structure);
        auto* count_field = field<Unreal::FIntProperty>(candidate.structure, item_ammo_name);
        if (!fields.valid() || !count_field) return;
        const Guid guid = fields.read(candidate.value);
        const auto saved_ammo = count_field->GetPropertyValueInContainer(candidate.value);
        if (guid == Guid{} || (stack && (saved_ammo < 1 || saved_ammo > max_drop_ammo)) ||
            !exact_canonical_drop_row(save, candidate, guid, saved_ammo, item_class, equipped_index)) return;
        if (equipped_index)
        {
            auto* hover = field<Unreal::FObjectProperty>(manager, STR("HoveredCard"));
            auto* begin = stock_no_args(manager, Unreal::FName(STR("InitiateItemTake")));
            auto* take = stock_no_args(manager, Unreal::FName(STR("FinalTakeItem")));
            if (stack || !hover || !begin || !take) return;
            hover->SetObjectPropertyValue(hover->ContainerPtrToValuePtr<void>(manager), card);
            manager->ProcessEvent(begin, nullptr);
            manager->ProcessEvent(take, nullptr);
            if (!pack_equipment_drop(manager, panel, candidate.panel, guid, item_class) ||
                !exact_canonical_drop_row(save, candidate, guid, saved_ammo, item_class)) return;
        }
        const auto count = stack ? saved_ammo : 1;
        const auto entry = !action.empty() ? action :
            equipped_index ? (from_menu ? "equipped_context_menu" : "equipped_ctrl_click") :
                             (from_menu ? "context_menu" : "ctrl_click");
        host_drop_request = HostDropRequest{guid, count, ++host_drop_sequence, false, now + request_lease, manager,
                                            screen, panel, entry};
    }

    [[nodiscard]] bool request_current(const TransportRequest& request, Unreal::UObject* save)
    {
        return save && request.save.Get() == save && request.pawn.Get() == scope.pawn.Get() &&
               request.generation == scope.generation && Clock::now() < request.expires;
    }

    [[nodiscard]] std::string_view pending_conflict(const TransportRequest& pending, TransportKind kind)
    {
        if (pending.kind != kind)
        {
            return stackable_transport(pending.kind) || stackable_transport(kind) ?
                "another_transport_request_pending" : "another_cube_request_pending";
        }
        return transport_status(kind, "another_gold_request_pending", "another_blue_request_pending",
                                "another_stardust_request_pending", "another_red_request_pending", "another_green_request_pending",
                                "another_fruiting_stardust_request_pending", "another_purple_request_pending");
    }

    [[nodiscard]] bool transport_use_eligible(Unreal::UObject* pawn)
    {
        auto* submerged = field<Unreal::FBoolProperty>(pawn, STR("HeadUnderWater"));
        auto* dead = field<Unreal::FBoolProperty>(pawn, STR("Dead"));
        return submerged && dead && !submerged->GetPropertyValueInContainer(pawn) && !dead->GetPropertyValueInContainer(pawn);
    }

    [[nodiscard]] std::string_view queue_transport(TransportKind kind, const ItemView& candidate, std::string_view entry,
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
        if (!transport_use_eligible(pawn))
        {
            return "stock_transport_use_ineligible";
        }
        auto* save = canonical_host_save();
        TransportInventory match;
        if (const auto error = locate_transport(save, kind, guid, match); !error.empty())
        {
            return error;
        }
        if (!match.record || match.item != candidate.structure)
        {
            return owned_backpack_required(kind);
        }
        if (transport_request && request_current(*transport_request, save))
        {
            return transport_request->kind == kind && transport_request->guid == guid ?
                transport_status(kind, "gold_request_pending", "blue_request_pending", "stardust_request_pending",
                                 "red_request_pending", "green_request_pending", "fruiting_stardust_request_pending",
                                 "purple_request_pending") :
                pending_conflict(*transport_request, kind);
        }
        transport_request = TransportRequest{kind, guid, scope.generation, Clock::now() + request_lease, pawn, save, entry};
        return queued_status(kind);
    }

    [[nodiscard]] std::string_view queue_matrix(Unreal::UObject* actor, bool record_anchor, std::optional<bool> held)
    {
        if (!held) return "stock_matrix_input_unavailable";
        auto& input = matrix_inputs[record_anchor ? 1 : 0];
        const std::string_view action = record_anchor ? "record" : "activate";
        const auto now = Clock::now();
        const bool same_press = input.generation == scope.generation && input.equipment.Get() == actor;
        if (!*held)
        {
            if (same_press) input = {};
            if (transport_request && transport_request->kind == TransportKind::Matrix &&
                transport_request->generation == scope.generation && transport_request->equipped_actor.Get() == actor &&
                transport_request->entry == action && now < transport_request->ready_after)
                transport_request.reset();
            return "matrix_input_released";
        }
        if (same_press) return "matrix_input_already_held";

        auto* pawn = scope.pawn.Get();
        if (!transport_use_eligible(pawn)) return "stock_transport_use_ineligible";

        auto* save = canonical_host_save();
        Guid guid{};
        if (const auto error = equipped_matrix_guid(save, actor, guid); !error.empty()) return error;
        input = {scope.generation, actor};
        if (transport_request && request_current(*transport_request, save)) return "another_transport_request_pending";

        const auto ready_after = now + (record_anchor ? std::chrono::seconds(1) : std::chrono::milliseconds(300));
        transport_request = TransportRequest{TransportKind::Matrix, guid, scope.generation, ready_after + request_lease,
                                             pawn, save, action, ready_after, actor};
        return record_anchor ? "matrix_record_queued" : "matrix_activate_queued";
    }

    void record(const Artifact& item, const ItemView& candidate, std::string_view entry, Unreal::UObject* context,
                bool widget, std::optional<bool> held)
    {
        const bool qualified = active_scope() &&
                               (widget ? same_widget_player(context) : same_actor_player(context));
        const std::string_view role = !qualified ? "unverified" : scope.host ? "host" : "guest";
        std::string_view reason = !qualified ? "current_local_owner_unverified" :
            !scope.host ? "host_activation_required" : "coordinated_route_required";
        if (qualified && scope.host && widget)
        {
            const auto found = std::ranges::find(transport_artifacts, &item);
            if (found != transport_artifacts.end())
            {
                const auto kind = static_cast<TransportKind>(found - transport_artifacts.begin());
                reason = queue_transport(kind, candidate, entry, context);
            }
        }
        if (qualified && scope.host && &item == &teleport_cube && !widget &&
            (entry == "use_item_primary" || entry == "use_item_secondary"))
        {
            reason = queue_matrix(context, entry == "use_item_secondary", held);
        }
        if (receipt.role != role || receipt.item != item.name || receipt.entry != entry || receipt.reason != reason)
        {
            RC::Output::send<RC::LogLevel::Verbose>(STR("[SilverfishCoopNative] Transport item denied for {}: {} via {} ({})\n"),
                RC::ensure_str(role), RC::ensure_str(item.name), RC::ensure_str(entry), RC::ensure_str(reason));
        }
        receipt = {receipt.count + 1, role, item.name, entry, reason};
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, const void* parameters)
    {
        if (!context || !function || !entries || iteration.OriginalFunctionCallPrevented())
        {
            return;
        }
        const auto name = function->GetNamePrivate();
        const auto guarded_inventory = [&] {
            if (!host_drop_scope.stock_inventory || !qualified_drop_seat(false) || !exact_class(context, manager_path) ||
                function->GetOuterPrivate() != context->GetClassPrivate() ||
                !host_drop_scope_matches(canonical_host_save())) return false;
            auto* screen = object_field(context, STR("ParentScreen"));
            return exact_drop_screen(context, screen, object_field(screen, STR("BackpackPanel")));
        };
        if (host_drop_scope.stock_inventory &&
            std::ranges::find(entries->guarded_inventory_actions, name) != entries->guarded_inventory_actions.end() &&
            guarded_inventory())
        {
            iteration.PreventOriginalFunctionCall();
            return;
        }
        if (name == entries->insert_item)
        {
            intercept_drag_drop(iteration, context, function, parameters);
            return;
        }
        if (name == entries->drop_item || name == entries->action_drop)
        {
            intercept_backpack_action(iteration, context, function, name == entries->action_drop);
            return;
        }
        if (name == entries->action_equip)
        {
            if (!scope.host) intercept_backpack_action(iteration, context, function, true, "equip");
            return;
        }
        if (name == entries->sort_items)
        {
            intercept_backpack_sort(iteration, context, function);
            return;
        }
        if (name == entries->final_split || name == entries->position_split)
        {
            intercept_backpack_split(iteration, context, function, parameters);
            return;
        }
        const bool manager = name == entries->action_use || name == entries->quick_move ||
                             name == entries->use_held || name == entries->confirm_use;
        const bool primary = name == entries->use_primary;
        const bool secondary = name == entries->use_secondary;
        const bool teleport = name == entries->teleport;
        const bool matrix_script = name == entries->matrix_script;
        const bool effects = name == entries->apply_effects;
        if (!(manager || primary || secondary || teleport || matrix_script || effects) || function->GetReturnProperty() ||
            function->GetNumParms() != (primary ? 2 : (secondary || matrix_script) ? 1 : 0) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native) || function->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return;
        }

        const Artifact* artifact;
        ItemView candidate;
        std::string_view entry;
        std::optional<bool> held;
        if (manager)
        {
            if (!exact_class(context, manager_path) || function->GetOuterPrivate() != context->GetClassPrivate())
            {
                return;
            }
            candidate = manager_item(context, name);
            artifact = candidate.artifact;
            if (!artifact && !scope.host && name == entries->action_use)
            {
                auto* item_class = object_value(field<Unreal::FClassProperty>(candidate.structure, item_class_name), candidate.value);
                if (const auto use = stock_backpack_use(item_class); !use.empty())
                {
                    intercept_backpack_action(iteration, context, function, true, use);
                    return;
                }
            }
            entry = name == entries->action_use ? "action_use" : name == entries->quick_move ? "quick_move" :
                name == entries->use_held ? "use_held_item" : "confirm_use_item";
            if (!artifact && guarded_inventory())
            {
                iteration.PreventOriginalFunctionCall();
                return;
            }
        }
        else
        {
            artifact = identify(context->GetClassPrivate());
            if (!artifact)
            {
                return;
            }
            if ((secondary || teleport || matrix_script) && artifact != &teleport_cube)
            {
                return;
            }
            auto* owner = function->GetOuterPrivate();
            const bool inherited = primary && (artifact == &stardust || artifact == &fruiting_stardust) &&
                                   owner && owner->GetPathName() == consumable_path;
            if (!inherited && owner != context->GetClassPrivate())
            {
                return;
            }
            if (matrix_script)
            {
                auto* script_entry = field<Unreal::FIntProperty>(function, STR("EntryPoint"));
                if (!parameters || !script_entry || script_entry->GetOffset_Internal() != 0) return;
                constexpr std::array blocked_entries{901, 2077, 2842, 4456, 4637};
                const auto number = script_entry->GetPropertyValueInContainer(parameters);
                if (std::ranges::find(blocked_entries, number) == blocked_entries.end()) return;
            }
            if (artifact == &teleport_cube && (primary || secondary))
            {
                auto* held_parameter = field<Unreal::FBoolProperty>(function, STR("Held"));
                if (parameters && held_parameter && held_parameter->GetOffset_Internal() == 0)
                    held = held_parameter->GetPropertyValueInContainer(parameters);
            }
            entry = primary ? "use_item_primary" : secondary ? "use_item_secondary" : teleport ? "teleport" :
                matrix_script ? "matrix_stock_script" : "apply_item_effects";
        }
        if (!artifact)
        {
            return;
        }
        iteration.PreventOriginalFunctionCall();
        if (iteration.OriginalFunctionCallPrevented())
        {
            record(*artifact, candidate, entry, context, manager, held);
        }
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            intercept(iteration, context, frame.Node(), frame.Locals());
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
                transport_request.reset();
                matrix_inputs = {};
                clear_host_drop();
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

    int report_transport_request(const Lua& lua, bool ok, std::string_view detail, std::string_view entry = {})
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        lua.set_string(entry);
        return 3;
    }

    int lua_take_matrix_request(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !current_host_argument(lua.get_integer(1)))
            return report_transport_request(lua, false, "current_transport_host_required");
        if (!transport_request || transport_request->kind != TransportKind::Matrix)
            return report_transport_request(lua, false, "no_matrix_request");

        const auto request = *transport_request;
        auto* save = canonical_host_save();
        if (!request_current(request, save))
        {
            transport_request.reset();
            return report_transport_request(lua, false, "stale_matrix_request");
        }
        if (Clock::now() < request.ready_after)
            return report_transport_request(lua, false, "matrix_input_waiting");

        transport_request.reset();
        if (!transport_use_eligible(scope.pawn.Get()))
            return report_transport_request(lua, false, "stock_transport_use_ineligible");
        Guid current_guid{};
        const auto error = equipped_matrix_guid(save, request.equipped_actor.Get(), current_guid);
        if (!error.empty()) return report_transport_request(lua, false, error);
        if (current_guid != request.guid)
            return report_transport_request(lua, false, "equipped_matrix_guid_changed");

        const auto guid = format_guid(request.guid);
        return report_transport_request(lua, true, {guid.data(), guid.size()}, request.entry);
    }

    int lua_inspect_matrix(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2))
            return probe_result(lua, false, "host_and_matrix_guid_required_on_game_thread");
        const auto address = lua.get_integer();
        Guid guid{};
        if (!parse_guid(lua.get_string(), guid)) return probe_result(lua, false, "invalid_matrix_guid");
        if (!current_host_argument(address)) return probe_result(lua, false, "current_transport_host_required");

        MatrixInventory inventory;
        const auto error = locate_matrix(canonical_host_save(), guid, inventory);
        if (!error.empty()) return probe_result(lua, false, error);
        return inventory.row ? probe_result(lua, true, "present") : probe_result(lua, false, "owned_matrix_guid_required");
    }

    template <TransportKind kind>
    int lua_take_transport_request(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !current_host_argument(lua.get_integer(1)))
        {
            return report_transport_request(lua, false, "current_transport_host_required");
        }
        if (!transport_request || transport_request->kind != kind)
        {
            return report_transport_request(lua, false, transport_status(kind, "no_gold_request", "no_blue_request",
                                                                         "no_stardust_request", "no_red_request", "no_green_request",
                                                                         "no_fruiting_stardust_request", "no_purple_request"));
        }
        const auto request = *transport_request;
        transport_request.reset();
        auto* save = canonical_host_save();
        if (!request_current(request, save))
        {
            return report_transport_request(lua, false, transport_status(kind, "stale_gold_request", "stale_blue_request",
                                                                         "stale_stardust_request", "stale_red_request", "stale_green_request",
                                                                         "stale_fruiting_stardust_request", "stale_purple_request"));
        }
        TransportInventory match;
        if (const auto error = locate_transport(save, kind, request.guid, match); !error.empty())
        {
            return report_transport_request(lua, false, error);
        }
        if (!match.record)
        {
            return report_transport_request(lua, false, owned_backpack_required(kind));
        }
        const auto guid = format_guid(request.guid);
        return report_transport_request(lua, true, {guid.data(), guid.size()}, request.entry);
    }

    [[nodiscard]] std::string_view transport_inventory_argument(const Lua& lua, TransportKind kind, Guid& guid, TransportInventory& match)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2))
        {
            return transport_status(kind, "host_and_gold_guid_required_on_game_thread", "host_and_blue_guid_required_on_game_thread",
                                    "host_and_stardust_guid_required_on_game_thread", "host_and_red_guid_required_on_game_thread",
                                    "host_and_green_guid_required_on_game_thread", "host_and_fruiting_stardust_guid_required_on_game_thread",
                                    "host_and_purple_guid_required_on_game_thread");
        }
        if (!parse_guid(lua.get_string(2), guid))
        {
            return transport_status(kind, "invalid_gold_guid", "invalid_blue_guid", "invalid_stardust_guid",
                                    "invalid_red_guid", "invalid_green_guid", "invalid_fruiting_stardust_guid", "invalid_purple_guid");
        }
        if (!current_host_argument(lua.get_integer(1)))
        {
            return "current_transport_host_required";
        }
        return locate_transport(canonical_host_save(), kind, guid, match);
    }

    [[nodiscard]] Unreal::UFunction* stock_no_args(Unreal::UObject* object, const Unreal::FName& name)
    {
        auto* function = object->GetFunctionByNameInChain(name);
        return function && function->GetOuterPrivate() == object->GetClassPrivate() && function->GetNumParms() == 0 &&
               !function->GetReturnProperty() && !function->HasAnyFunctionFlags(Unreal::FUNC_Native) &&
               !function->HasAnyFunctionFlags(Unreal::FUNC_Net) ? function : nullptr;
    }

    template <bool host>
    int lua_set_drop(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw())
        {
            const bool stock = !host && lua.get_stack_size() == 4 && lua.is_bool(4);
            if ((stock || lua.get_stack_size() == (host ? 2 : 3)) && lua.is_integer(1) && lua.is_string(2) &&
                (host || lua.is_integer(3)))
            {
                // LuaMadeSimple getters remove the value, so take arguments from the top down.
                const bool stock_inventory = stock && lua.get_bool(4);
                const auto view_address = host ? 0 : lua.get_integer(3);
                const std::string epoch{lua.get_string(2)};
                const auto pawn = lua.get_integer(1);
                auto* save = canonical_host_save();
                if (save && drop_epoch_valid(epoch) && pawn > 0 && scope.address == static_cast<std::uintptr_t>(pawn) &&
                    hooks_ready.load(std::memory_order_acquire) && qualified_drop_seat(host) && Clock::now() < scope.expires &&
                    (host || (view_address > 0 && reinterpret_cast<std::uintptr_t>(save) == static_cast<std::uintptr_t>(view_address))))
                {
                    if (!host_drop_scope_matches(save) || !std::ranges::equal(epoch, host_drop_scope.epoch))
                        clear_host_drop();
                    std::ranges::copy(epoch, host_drop_scope.epoch.begin());
                    host_drop_scope.pawn = scope.pawn.Get();
                    host_drop_scope.world = scope.world.Get();
                    host_drop_scope.save = save;
                    host_drop_scope.generation = scope.generation;
                    host_drop_scope.expires = Clock::now() + scope_lease;
                    host_drop_scope.stock_inventory = stock_inventory;
                    accepted = true;
                }
            }
            if (!accepted) clear_host_drop();
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_clear_host_drop(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw()) clear_host_drop();
        return 0;
    }

    template <bool host>
    int lua_take_drop(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2))
            return 0;
        const std::string epoch{lua.get_string(2)};
        const auto pawn = lua.get_integer(1);
        if (!drop_argument(pawn, epoch, host) || !host_drop_request ||
            host_drop_request->taken || Clock::now() >= host_drop_request->expires)
            return 0;

        auto& request = *host_drop_request;
        request.taken = true;
        request.expires = Clock::now() + host_drop_settlement_lease;
        const auto guid = format_guid(request.guid);
        lua.set_integer(request.sequence);
        lua.set_string({guid.data(), guid.size()});
        lua.set_integer(request.count);
        lua.set_string(request.entry);
        if (request.entry == "split")
        {
            lua.set_integer(request.expected);
            return 5;
        }
        return 4;
    }

    template <bool host>
    int lua_finish_drop(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_string(2) ||
            !lua.is_integer(3))
            return probe_result(lua, false, "scope_changed");
        const auto sequence = lua.get_integer(3);
        const std::string epoch{lua.get_string(2)};
        const auto pawn = lua.get_integer(1);
        if (!drop_argument(pawn, epoch, host) || !host_drop_request || !host_drop_request->taken ||
            host_drop_request->sequence != sequence || Clock::now() >= host_drop_request->expires)
            return probe_result(lua, false, "scope_changed");

        const auto completed = *host_drop_request;
        // FullRefresh can invoke automatic sorting. Keep the settled request
        // occupied until the stock panel has finished rebuilding its cards.
        const auto finish = [&](bool ok, std::string_view status) {
            host_drop_request.reset();
            return probe_result(lua, ok, status);
        };
        auto* manager = completed.manager.Get();
        auto* panel = completed.panel.Get();
        if (!exact_drop_screen(manager, completed.screen.Get(), panel))
            return finish(true, "screen_closed");
        if (completed.entry == "split")
        {
            auto* cancel = stock_no_args(manager, Unreal::FName(STR("CancelSplitStack")));
            if (!cancel) return finish(false, "refresh_unavailable");
            manager->ProcessEvent(cancel, nullptr);
        }
        if (completed.entry == "context_menu" || completed.entry == "equipped_context_menu" || completed.entry == "equip" ||
            completed.entry.starts_with("use-"))
        {
            const Unreal::FName close_name{STR("CloseContextMenu")};
            auto* close = stock_no_args(manager, close_name);
            if (!close) return finish(false, "refresh_unavailable");
            manager->ProcessEvent(close, nullptr);
            if (!exact_drop_screen(manager, completed.screen.Get(), panel))
                return finish(true, "screen_closed");
        }
        const Unreal::FName refresh_name{STR("FullRefresh")};
        auto* refresh = stock_no_args(panel, refresh_name);
        if (!refresh) return finish(false, "refresh_unavailable");
        panel->ProcessEvent(refresh, nullptr);
        if (completed.entry == "equip")
        {
            auto* slots = field<Unreal::FArrayProperty>(manager, STR("EquipSlots"));
            auto* slot_property = slots ? Unreal::CastField<Unreal::FObjectProperty>(slots->GetInner()) : nullptr;
            if (!slot_property) return finish(false, "refresh_unavailable");
            Unreal::FScriptArrayHelper widgets(slots, slots->ContainerPtrToValuePtr<void>(manager));
            if (widgets.Num() < 8 || widgets.Num() > 16) return finish(false, "refresh_unavailable");
            for (int index = 0; index < 8; ++index)
            {
                auto* slot = object_value(slot_property, widgets.GetRawPtr(index));
                if (!exact_class(slot, equipment_slot_path) || !same_widget_player(slot) ||
                    object_field(slot, STR("ItemManager")) != manager) return finish(false, "refresh_unavailable");
                auto* refresh_slot = stock_no_args(slot, refresh_name);
                if (!refresh_slot) return finish(false, "refresh_unavailable");
                slot->ProcessEvent(refresh_slot, nullptr);
            }
        }
        // A co-op drop changes both the source panel and the stock floor list.
        // Rebuild the floor cards from the live actors instead of keeping their old entries.
        auto* screen = completed.screen.Get();
        if (exact_drop_screen(manager, screen, panel))
        {
            auto* floor = object_field(screen, STR("FloorPanel"));
            auto* refresh_floor = stock_no_args(floor, refresh_name);
            if (!refresh_floor) return finish(false, "refresh_unavailable");
            floor->ProcessEvent(refresh_floor, nullptr);
        }
        return finish(true, "refreshed");
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

    template <TransportKind kind>
    int lua_inspect_cube(const Lua& lua)
    {
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        return probe_result(lua, true, match.record ? "present" : "absent");
    }

    template <TransportKind kind>
    int lua_commit_cube(const Lua& lua)
    {
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
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

    constexpr auto max_stack_quantity = std::numeric_limits<std::int32_t>::max();
    constexpr std::string_view stock_stardust_payload = "stock-default-v1";
    constexpr std::string_view stock_fruiting_payload = "stock-fruiting-default-v1";

    [[nodiscard]] constexpr std::string_view stack_recovery_payload(TransportKind kind)
    {
        return stack_status(kind, stock_stardust_payload, stock_fruiting_payload);
    }

    [[nodiscard]] constexpr std::string_view quantity_required(TransportKind kind)
    {
        return stack_status(kind, "positive_stardust_quantity_required_on_game_thread",
                            "positive_fruiting_stardust_quantity_required_on_game_thread");
    }

    struct StackTotals
    {
        Unreal::FDoubleProperty* weight{};
        Unreal::FIntProperty* value{};
        double total_weight{};
        std::int32_t total_value{};

        [[nodiscard]] bool matches(const void* item) const
        {
            return weight->GetPropertyValueInContainer(item) == total_weight && value->GetPropertyValueInContainer(item) == total_value;
        }

        void write(void* item) const
        {
            weight->SetPropertyValueInContainer(item, total_weight);
            value->SetPropertyValueInContainer(item, total_value);
        }
    };

    [[nodiscard]] bool calculate_totals(const TransportInventory& match, std::int32_t quantity, StackTotals& totals)
    {
        auto* stock_weight = field<Unreal::FDoubleProperty>(match.defaults, STR("Weight"));
        auto* stock_value = field<Unreal::FIntProperty>(match.defaults, STR("Value"));
        totals.weight = field<Unreal::FDoubleProperty>(match.item, item_weight_name);
        totals.value = field<Unreal::FIntProperty>(match.item, item_value_name);
        if (!stock_weight || !stock_value || !totals.weight || !totals.value || quantity <= 0)
        {
            return false;
        }
        const auto weight = stock_weight->GetPropertyValueInContainer(match.defaults) * quantity;
        const auto value = static_cast<std::int64_t>(stock_value->GetPropertyValueInContainer(match.defaults)) * quantity;
        if (!std::isfinite(weight) || weight < 0 || value < 0 || value > max_stack_quantity)
        {
            return false;
        }
        totals.total_weight = weight;
        totals.total_value = static_cast<std::int32_t>(value);
        return true;
    }

    int stack_result(const Lua& lua, bool ok, std::string_view detail, std::int32_t quantity = 0)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        lua.set_integer(quantity);
        return 3;
    }

    [[nodiscard]] std::optional<std::int32_t> stack_quantity_argument(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 || !lua.is_integer(3))
        {
            return std::nullopt;
        }
        const auto quantity = lua.get_integer(3);
        if (quantity <= 0 || quantity > max_stack_quantity)
        {
            return std::nullopt;
        }
        return static_cast<std::int32_t>(quantity);
    }

    template <TransportKind kind>
    int lua_commit_stack(const Lua& lua)
    {
        const auto before = stack_quantity_argument(lua);
        if (!before)
        {
            return stack_result(lua, false, quantity_required(kind));
        }
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return stack_result(lua, false, error);
        }

        const auto remaining = *before - 1;
        if (match.quantity == remaining)
        {
            return stack_result(lua, true, "already_debited", remaining);
        }
        if (match.quantity != *before)
        {
            const auto error = match.record ?
                stack_status(kind, "stardust_stack_quantity_mismatch", "fruiting_stardust_stack_quantity_mismatch") :
                stack_status(kind, "stardust_stack_missing", "fruiting_stardust_stack_missing");
            return stack_result(lua, false, error, match.quantity);
        }

        auto* instance = scope.instance.Get();
        const Unreal::FName carry_name{STR("Calc_CarryWeight")};
        auto* recalculate = stock_no_args(instance, carry_name);
        if (!recalculate)
        {
            return stack_result(lua, false, "stock_carry_weight_function_unavailable", match.quantity);
        }
        if (remaining == 0)
        {
            Unreal::FScriptArrayHelper backpack(match.backpack, match.backpack->ContainerPtrToValuePtr<void>(match.save));
            backpack.RemoveValues(match.index, 1);
        }
        else
        {
            StackTotals totals;
            if (!calculate_totals(match, remaining, totals))
            {
                return stack_result(lua, false, stack_status(kind, "stock_stardust_stack_totals_unavailable",
                                                             "stock_fruiting_stardust_stack_totals_unavailable"), match.quantity);
            }
            match.ammo->SetPropertyValueInContainer(match.record, remaining);
            totals.write(match.record);
        }
        instance->ProcessEvent(recalculate, nullptr);
        return stack_result(lua, true, remaining > 0 ? "decremented" : "removed", remaining);
    }

    struct StockItemMetadata
    {
        Unreal::UObject* defaults;
        Unreal::FNameProperty* stock_tag;
        Unreal::FStructProperty* stock_data;
        Unreal::FBoolProperty* stock_never_drop;
        Unreal::FNameProperty* item_tag;
        Unreal::FStructProperty* item_data;
        Unreal::FBoolProperty* item_never_drop;

        explicit StockItemMetadata(const TransportInventory& match) :
            defaults(match.defaults), stock_tag(field<Unreal::FNameProperty>(defaults, STR("DataTag"))),
            stock_data(field<Unreal::FStructProperty>(defaults, STR("DataStruct"))),
            stock_never_drop(field<Unreal::FBoolProperty>(defaults, STR("NeverDrop"))),
            item_tag(field<Unreal::FNameProperty>(match.item, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"))),
            item_data(field<Unreal::FStructProperty>(match.item, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"))),
            item_never_drop(field<Unreal::FBoolProperty>(match.item, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"))) {}

        [[nodiscard]] bool available() const
        {
            auto* data_type = item_data ? item_data->GetStruct().Get() : nullptr;
            return stock_tag && stock_data && stock_never_drop && item_tag && item_never_drop && data_type &&
                stock_data->GetStruct().Get() == data_type && data_type->GetPathName() == STR("/Script/StructUtils.InstancedStruct");
        }

        [[nodiscard]] bool matches(const void* item) const
        {
            return item_never_drop->GetPropertyValueInContainer(item) == stock_never_drop->GetPropertyValueInContainer(defaults) &&
                   item_tag->Identical(item_tag->ContainerPtrToValuePtr<void>(item), stock_tag->ContainerPtrToValuePtr<void>(defaults)) &&
                   item_data->Identical(item_data->ContainerPtrToValuePtr<void>(item), stock_data->ContainerPtrToValuePtr<void>(defaults));
        }

        void copy_to(void* item) const
        {
            item_never_drop->SetPropertyValueInContainer(item, stock_never_drop->GetPropertyValueInContainer(defaults));
            item_tag->CopyCompleteValue(item_tag->ContainerPtrToValuePtr<void>(item), stock_tag->ContainerPtrToValuePtr<void>(defaults));
            item_data->CopyCompleteValue(item_data->ContainerPtrToValuePtr<void>(item), stock_data->ContainerPtrToValuePtr<void>(defaults));
        }
    };

    [[nodiscard]] bool stock_stack(const TransportInventory& match)
    {
        const StockItemMetadata metadata(match);
        StackTotals totals;
        return match.record && metadata.available() && calculate_totals(match, match.quantity, totals) &&
               metadata.matches(match.record) && totals.matches(match.record);
    }

    [[nodiscard]] std::string_view append_stock_transport(TransportKind kind, const TransportInventory& match,
                                                         const Guid& guid, std::int32_t ammo)
    {
        const StockItemMetadata metadata(match);
        if (!metadata.available())
        {
            return kind == TransportKind::Matrix ? "stock_matrix_defaults_unavailable" :
                transport_status(kind, "stock_gold_defaults_unavailable", "stock_blue_defaults_unavailable",
                                 "stock_stardust_defaults_unavailable", "stock_red_defaults_unavailable",
                                 "stock_green_defaults_unavailable", "stock_fruiting_stardust_defaults_unavailable",
                                 "stock_purple_defaults_unavailable");
        }
        StackTotals totals;
        if (!calculate_totals(match, stackable_transport(kind) ? ammo : 1, totals))
        {
            return kind == TransportKind::Matrix ? "invalid_stock_matrix_defaults" :
                transport_status(kind, "invalid_stock_gold_defaults", "invalid_stock_blue_defaults",
                                 "invalid_stock_stardust_defaults", "invalid_stock_red_defaults", "invalid_stock_green_defaults",
                                 "invalid_stock_fruiting_stardust_defaults", "invalid_stock_purple_defaults");
        }
        auto* instance = scope.instance.Get();
        const Unreal::FName carry_name{STR("Calc_CarryWeight")};
        auto* recalculate = stock_no_args(instance, carry_name);
        if (!recalculate)
        {
            return "stock_carry_weight_function_unavailable";
        }
        const GuidFields fields(match.item);
        Unreal::FScriptArrayHelper backpack(match.backpack, match.backpack->ContainerPtrToValuePtr<void>(match.save));
        if (backpack.Num() >= inventory_limit)
        {
            return "stock_inventory_size_out_of_range";
        }
        auto* item = backpack.GetRawPtr(backpack.AddValue());
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(item), match.transport_class);
        match.ammo->SetPropertyValueInContainer(item, ammo);
        totals.write(item);
        metadata.copy_to(item);
        fields.write(item, guid);
        instance->ProcessEvent(recalculate, nullptr);
        return {};
    }

    [[nodiscard]] std::string_view matrix_fixture_arguments(const Lua& lua, Guid& guid, bool* record_anchor = nullptr)
    {
        if (!fixtures_enabled()) return "native_matrix_fixtures_disabled";
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != (record_anchor ? 3 : 2) ||
            !lua.is_integer(1) || !lua.is_string(2))
            return "matrix_fixture_pawn_and_guid_required";
        if (record_anchor && !lua.is_string(3)) return "matrix_fixture_action_required";

        // LuaMadeSimple removes each argument as it reads it, so consume from the front.
        const auto address = lua.get_integer();
        if (!parse_guid(lua.get_string(), guid)) return "invalid_matrix_guid";
        if (record_anchor)
        {
            const auto action = lua.get_string();
            if (action != "record" && action != "activate") return "matrix_fixture_action_required";
            *record_anchor = action == "record";
        }
        return current_host_argument(address) ? "" : "current_transport_host_required";
    }

    int lua_seed_matrix(const Lua& lua)
    {
        Guid guid{};
        if (const auto error = matrix_fixture_arguments(lua, guid); !error.empty()) return probe_result(lua, false, error);

        auto* save = canonical_host_save();
        MatrixInventory inventory;
        if (const auto error = locate_matrix(save, guid, inventory); !error.empty()) return probe_result(lua, false, error);
        if (inventory.row) return probe_result(lua, true, "present");

        TransportInventory stock;
        stock.save = save;
        stock.backpack = inventory.backpack;
        stock.item = inventory.row_type;
        stock.item_class = field<Unreal::FClassProperty>(stock.item, item_class_name);
        stock.ammo = field<Unreal::FIntProperty>(stock.item, item_ammo_name);
        stock.transport_class = inventory.type;
        stock.defaults = stock.transport_class->GetClassDefaultObject().Get();
        auto* default_ammo = field<Unreal::FIntProperty>(stock.defaults, STR("Ammo"));
        auto* stacks = field<Unreal::FBoolProperty>(stock.defaults, STR("Stacks"));
        if (!stock.item_class || !stock.ammo || !default_ammo || !stacks || stacks->GetPropertyValueInContainer(stock.defaults))
            return probe_result(lua, false, "stock_matrix_defaults_unavailable");

        const auto ammo = default_ammo->GetPropertyValueInContainer(stock.defaults);
        if (const auto error = append_stock_transport(TransportKind::Matrix, stock, guid, ammo); !error.empty())
            return probe_result(lua, false, error);

        MatrixInventory check;
        const StockItemMetadata metadata(stock);
        StackTotals totals;
        const bool intact = canonical_host_save() == save && locate_matrix(save, guid, check).empty() &&
                            check.backpack_index >= 0 && check.row && metadata.matches(check.row) &&
                            stock.ammo->GetPropertyValueInContainer(check.row) == ammo &&
                            calculate_totals(stock, 1, totals) && totals.matches(check.row);
        return probe_result(lua, intact, intact ? "seeded" : "stock_matrix_fixture_seed_unverified");
    }

    int lua_equip_matrix(const Lua& lua)
    {
        Guid guid{};
        if (const auto error = matrix_fixture_arguments(lua, guid); !error.empty()) return probe_result(lua, false, error);

        auto* save = canonical_host_save();
        auto* pawn = scope.pawn.Get();
        auto* equipped = object_field(pawn, STR("EquipedItem"));
        Guid actual{};
        if (equipped_matrix_guid(save, equipped, actual).empty() && actual == guid)
            return probe_result(lua, true, "equipped");

        MatrixInventory inventory;
        if (const auto error = locate_matrix(save, guid, inventory); !error.empty()) return probe_result(lua, false, error);
        if (!inventory.row || !transport_use_eligible(pawn)) return probe_result(lua, false, "stock_matrix_fixture_equip_ineligible");

        const Unreal::FName equip_name{STR("Equip_NextItem")};
        auto* equip = pawn->GetFunctionByNameInChain(equip_name);
        if (!equip || equip->GetOuterPrivate() != pawn->GetClassPrivate())
            return probe_result(lua, false, "stock_matrix_fixture_equip_function_unavailable");

        struct EquipParameters
        {
            std::int32_t index{};
            bool prev{};
        } parameters;
        auto* index = field<Unreal::FIntProperty>(equip, STR("Index"));
        auto* prev = field<Unreal::FBoolProperty>(equip, STR("Prev"));
        if (!index || !index->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            index->HasAnyPropertyFlags(Unreal::CPF_OutParm) || index->GetOffset_Internal() != offsetof(EquipParameters, index))
            return probe_result(lua, false, "stock_matrix_fixture_equip_index_unavailable");
        if (!prev || !prev->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            prev->HasAnyPropertyFlags(Unreal::CPF_OutParm) || prev->GetOffset_Internal() != offsetof(EquipParameters, prev))
            return probe_result(lua, false, "stock_matrix_fixture_equip_prev_unavailable");
        if (equip->GetNumParms() != 2 || equip->GetReturnProperty() ||
            equip->GetParmsSize() < offsetof(EquipParameters, prev) + sizeof(bool) || equip->GetParmsSize() > sizeof(EquipParameters))
            return probe_result(lua, false, "stock_matrix_fixture_equip_parameter_layout_unavailable");

        auto* selected = field<Unreal::FIntProperty>(save, STR("PlayerEquipIndex"));
        if (!selected) return probe_result(lua, false, "stock_matrix_fixture_selected_slot_unavailable");
        const Unreal::FName carry_name{STR("Calc_CarryWeight")};
        auto* instance = scope.instance.Get();
        auto* recalculate = stock_no_args(instance, carry_name);
        if (!recalculate) return probe_result(lua, false, "stock_carry_weight_function_unavailable");

        const auto old_slot = selected->GetPropertyValueInContainer(save);
        auto new_slot = inventory.equipped_index;
        if (new_slot < 0)
        {
            auto* item = Unreal::CastField<Unreal::FStructProperty>(inventory.equipment->GetInner());
            auto* item_class = field<Unreal::FClassProperty>(inventory.row_type, item_class_name);
            const GuidFields fields(inventory.row_type);
            if (!item || !item_class || !fields.valid()) return probe_result(lua, false, "stock_matrix_inventory_schema_unavailable");

            Unreal::FScriptArrayHelper equipment(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(save));
            for (int slot = 0; slot < equipment.Num(); ++slot)
            {
                const auto* row = equipment.GetRawPtr(slot);
                if (slot != old_slot && !object_value(item_class, row) && fields.read(row) == Guid{})
                {
                    new_slot = slot;
                    break;
                }
            }
            if (new_slot < 0) return probe_result(lua, false, "stock_matrix_fixture_empty_slot_required");

            Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(save));
            item->CopyCompleteValue(equipment.GetRawPtr(new_slot), inventory.row);
            backpack.RemoveValues(inventory.backpack_index, 1);
        }
        else if (new_slot == old_slot && live(equipped))
        {
            return probe_result(lua, false, "stock_matrix_fixture_equipped_slot_conflict");
        }

        index->SetPropertyValueInContainer(&parameters, new_slot);
        prev->SetPropertyValueInContainer(&parameters, false);
        pawn->ProcessEvent(equip, &parameters);
        instance->ProcessEvent(recalculate, nullptr);
        const bool verified = canonical_host_save() == save &&
            equipped_matrix_guid(save, object_field(pawn, STR("EquipedItem")), actual).empty() && actual == guid;
        return probe_result(lua, verified, verified ? "equipped" : "stock_matrix_fixture_equip_unverified");
    }

    int lua_probe_matrix(const Lua& lua)
    {
        Guid guid{};
        bool record_anchor = false;
        if (const auto error = matrix_fixture_arguments(lua, guid, &record_anchor); !error.empty())
            return probe_result(lua, false, error);
        const std::string_view action = record_anchor ? "record" : "activate";

        auto* save = canonical_host_save();
        auto* actor = object_field(scope.pawn.Get(), STR("EquipedItem"));
        Guid exact{};
        if (const auto error = equipped_matrix_guid(save, actor, exact); !error.empty()) return probe_result(lua, false, error);
        if (exact != guid) return probe_result(lua, false, "owned_equipped_matrix_guid_required");
        if (transport_request && request_current(*transport_request, save))
            return probe_result(lua, false, "another_transport_request_pending");

        auto* use = actor->GetFunctionByNameInChain(record_anchor ? entries->use_secondary : entries->use_primary);
        auto* held = field<Unreal::FBoolProperty>(use, STR("Held"));
        auto* variance = field<Unreal::FDoubleProperty>(use, STR("VarianceScaler"));
        struct UseParameters
        {
            bool held{};
            double variance{};
        } parameters;
        if (!held || use->GetOuterPrivate() != actor->GetClassPrivate() || use->GetReturnProperty() ||
            held->GetOffset_Internal() != offsetof(UseParameters, held) ||
            (record_anchor ? use->GetNumParms() != 1 || use->GetParmsSize() != sizeof(bool) :
                             use->GetNumParms() != 2 || use->GetParmsSize() != sizeof(UseParameters) ||
                                 !variance || variance->GetOffset_Internal() != offsetof(UseParameters, variance)))
            return probe_result(lua, false, "stock_matrix_fixture_use_schema_unavailable");

        // A prior completed probe may still own this input's pressed state.
        actor->ProcessEvent(use, &parameters);
        const auto count = receipt.count;
        held->SetPropertyValueInContainer(&parameters, true);
        actor->ProcessEvent(use, &parameters);
        const std::string_view queued = record_anchor ? "matrix_record_queued" : "matrix_activate_queued";
        const bool verified = receipt.count == count + 1 && receipt.role == "host" && receipt.item == teleport_cube.name &&
            receipt.reason == queued && transport_request && transport_request->kind == TransportKind::Matrix &&
            transport_request->entry == action && transport_request->guid == guid && request_current(*transport_request, save) &&
            transport_request->equipped_actor.Get() == actor && canonical_host_save() == save;
        if (!verified)
        {
            clear_matrix_input();
            return probe_result(lua, false, "stock_matrix_fixture_use_did_not_queue");
        }
        return probe_result(lua, true, queued);
    }

    template <TransportKind kind>
    int lua_seed_cube(const Lua& lua)
    {
        if (!fixtures_enabled())
        {
            return probe_result(lua, false, transport_status(kind, "native_gold_fixtures_disabled", "native_blue_fixtures_disabled",
                                                             "native_stardust_fixtures_disabled", "native_red_fixtures_disabled",
                                                             "native_green_fixtures_disabled", "native_fruiting_stardust_fixtures_disabled",
                                                             "native_purple_fixtures_disabled"));
        }
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (match.record)
        {
            return probe_result(lua, true, "present");
        }
        const auto error = append_stock_transport(kind, match, guid, -1);
        return error.empty() ? probe_result(lua, true, "seeded") : probe_result(lua, false, error);
    }

    template <TransportKind kind>
    int lua_inspect_stack(const Lua& lua)
    {
        Guid guid{};
        TransportInventory match;
        const auto error = transport_inventory_argument(lua, kind, guid, match);
        if (!error.empty())
        {
            stack_result(lua, false, error);
            lua.set_string("");
            return 4;
        }
        stack_result(lua, true, match.record ? "present" : "absent", match.quantity);
        lua.set_string(stock_stack(match) ? stack_recovery_payload(kind) : std::string_view{});
        return 4;
    }

    template <TransportKind kind>
    int lua_seed_stack(const Lua& lua)
    {
        if (!fixtures_enabled())
        {
            return stack_result(lua, false, stack_status(kind, "native_stardust_fixtures_disabled",
                                                         "native_fruiting_stardust_fixtures_disabled"));
        }
        const auto quantity = stack_quantity_argument(lua);
        if (!quantity)
        {
            return stack_result(lua, false, quantity_required(kind));
        }
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return stack_result(lua, false, error);
        }
        if (match.record)
        {
            return match.quantity == *quantity ? stack_result(lua, true, "present", match.quantity) :
                stack_result(lua, false, stack_status(kind, "stardust_seed_quantity_mismatch", "fruiting_stardust_seed_quantity_mismatch"),
                             match.quantity);
        }
        const auto error = append_stock_transport(kind, match, guid, *quantity);
        return error.empty() ? stack_result(lua, true, "seeded", *quantity) : stack_result(lua, false, error);
    }

    template <TransportKind kind>
    int lua_restore_stack(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4 || !lua.is_string(4) ||
            lua.get_string(4) != stack_recovery_payload(kind))
        {
            return stack_result(lua, false, stack_status(kind, "stock_stardust_recovery_payload_required_on_game_thread",
                                                         "stock_fruiting_stardust_recovery_payload_required_on_game_thread"));
        }
        const auto quantity = stack_quantity_argument(lua);
        if (!quantity)
        {
            return stack_result(lua, false, quantity_required(kind));
        }
        Guid guid{};
        TransportInventory match;
        if (const auto error = transport_inventory_argument(lua, kind, guid, match); !error.empty())
        {
            return stack_result(lua, false, error);
        }
        if (match.record)
        {
            return match.quantity == *quantity && stock_stack(match) ? stack_result(lua, true, "present", match.quantity) :
                stack_result(lua, false, stack_status(kind, "stardust_recovery_item_conflict", "fruiting_stardust_recovery_item_conflict"),
                             match.quantity);
        }

        // The Lua bridge supplies this only for the persisted residual of a committed debit.
        const auto error = append_stock_transport(kind, match, guid, *quantity);
        return error.empty() ? stack_result(lua, true, "restored", *quantity) : stack_result(lua, false, error);
    }

    template <TransportKind kind>
    int lua_probe_transport(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !fixtures_enabled() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
        {
            return probe_result(lua, false, transport_status(kind, "gold_probe_fixture_required_on_game_thread",
                                                              "blue_probe_fixture_required_on_game_thread",
                                                              "stardust_probe_fixture_required_on_game_thread",
                                                              "red_probe_fixture_required_on_game_thread",
                                                              "green_probe_fixture_required_on_game_thread",
                                                              "fruiting_stardust_probe_fixture_required_on_game_thread",
                                                              "purple_probe_fixture_required_on_game_thread"));
        }
        Guid guid{};
        if (!parse_guid(lua.get_string(3), guid))
        {
            return probe_result(lua, false, transport_status(kind, "invalid_gold_guid", "invalid_blue_guid",
                                                             "invalid_stardust_guid", "invalid_red_guid", "invalid_green_guid",
                                                             "invalid_fruiting_stardust_guid", "invalid_purple_guid"));
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
        TransportInventory match;
        if (const auto error = locate_transport(save, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (!match.record)
        {
            return probe_result(lua, false, owned_backpack_required(kind));
        }
        if (transport_request && request_current(*transport_request, save))
        {
            return probe_result(lua, false, pending_conflict(*transport_request, kind));
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
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(value), match.transport_class);
        const auto before = receipt.count;
        manager->ProcessEvent(function, nullptr);
        if (receipt.count != before + 1 || receipt.role != "host" || receipt.item != transport_artifact(kind).name ||
            receipt.entry != "use_held_item" || receipt.reason != "coordinated_route_required")
        {
            return probe_result(lua, false, "stock_scratch_held_use_did_not_report_expected_denial");
        }
        held->CopyCompleteValue(value, match.record);
        manager->ProcessEvent(function, nullptr);
        TransportInventory after;
        const bool intact = locate_transport(canonical_host_save(), kind, guid, after).empty() && after.record &&
                            after.save == save && held->Identical(value, after.record);
        const bool queued = receipt.count == before + 2 && receipt.reason == queued_status(kind) && transport_request &&
                            transport_request->kind == kind && transport_request->guid == guid && request_current(*transport_request, save);
        if (!intact || !queued)
        {
            transport_request.reset();
            return probe_result(lua, false, intact ? transport_status(kind, "stock_gold_use_did_not_queue", "stock_blue_use_did_not_queue",
                                                                      "stock_stardust_use_did_not_queue", "stock_red_use_did_not_queue",
                                                                      "stock_green_use_did_not_queue", "stock_fruiting_stardust_use_did_not_queue",
                                                                      "stock_purple_use_did_not_queue") :
                                                    "stock_backpack_item_was_changed");
        }
        return probe_result(lua, true, transport_status(kind, "stock_gold_use_queued", "stock_blue_use_queued",
                                                        "stock_stardust_use_queued", "stock_red_use_queued", "stock_green_use_queued",
                                                        "stock_fruiting_stardust_use_queued", "stock_purple_use_queued"));
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
        constexpr auto kind = TransportKind::Gold;
        TransportInventory match;
        if (const auto error = locate_transport(save, kind, guid, match); !error.empty())
        {
            return probe_result(lua, false, error);
        }
        if (!match.record)
        {
            return probe_result(lua, false, "owned_backpack_cube_required");
        }
        if (transport_request && request_current(*transport_request, save))
        {
            return probe_result(lua, false, pending_conflict(*transport_request, kind));
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
        TransportInventory unowned;
        if (!locate_transport(save, kind, unowned_guid, unowned).empty() || unowned.record)
        {
            return probe_result(lua, false, "gold_action_canary_guid_not_absent");
        }
        match.item_class->SetObjectPropertyValue(match.item_class->ContainerPtrToValuePtr<void>(value), match.transport_class);
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
        TransportInventory after;
        const bool intact = locate_transport(canonical_host_save(), kind, guid, after).empty() && after.record &&
                            after.save == save && item->Identical(value, after.record);
        const bool queued = receipt.count == before + 2 && receipt.reason == queued_status(kind) && transport_request &&
                            transport_request->kind == kind && transport_request->entry == "action_use" && transport_request->guid == guid &&
                            request_current(*transport_request, save);
        if (!intact || !queued)
        {
            transport_request.reset();
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
        [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, void* parameters) {
            if (Unreal::IsInGameThreadRaw())
            {
                intercept(iteration, context, function, parameters);
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
        transport_request.reset();
        matrix_inputs = {};
        clear_host_drop();
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
        lua.register_function("SilverfishSetNativeHostDropScope", lua_set_drop<true>);
        lua.register_function("SilverfishSetNativeGuestDropScope", lua_set_drop<false>);
        lua.register_function("SilverfishClearNativeHostDropScope", lua_clear_host_drop);
        lua.register_function("SilverfishTakeNativeHostDropRequest", lua_take_drop<true>);
        lua.register_function("SilverfishFinishNativeHostDropRequest", lua_finish_drop<true>);
        lua.register_function("SilverfishTakeNativeGuestDropRequest", lua_take_drop<false>);
        lua.register_function("SilverfishFinishNativeGuestDropRequest", lua_finish_drop<false>);
        lua.register_function("SilverfishProbeNativeTransportHeldItem", lua_probe_held_item);
        lua.register_function("SilverfishTakeNativeGoldTransportRequest", lua_take_transport_request<TransportKind::Gold>);
        lua.register_function("SilverfishInspectNativeGoldTransport", lua_inspect_cube<TransportKind::Gold>);
        lua.register_function("SilverfishCommitNativeGoldTransport", lua_commit_cube<TransportKind::Gold>);
        lua.register_function("SilverfishTakeNativeBlueTransportRequest", lua_take_transport_request<TransportKind::Blue>);
        lua.register_function("SilverfishInspectNativeBlueTransport", lua_inspect_cube<TransportKind::Blue>);
        lua.register_function("SilverfishCommitNativeBlueTransport", lua_commit_cube<TransportKind::Blue>);
        lua.register_function("SilverfishTakeNativeStardustTransportRequest", lua_take_transport_request<TransportKind::Stardust>);
        lua.register_function("SilverfishInspectNativeStardustTransport", lua_inspect_stack<TransportKind::Stardust>);
        lua.register_function("SilverfishCommitNativeStardustTransport", lua_commit_stack<TransportKind::Stardust>);
        lua.register_function("SilverfishRestoreNativeStardustTransport", lua_restore_stack<TransportKind::Stardust>);
        lua.register_function("SilverfishTakeNativeFruitingStardustTransportRequest", lua_take_transport_request<TransportKind::FruitingStardust>);
        lua.register_function("SilverfishInspectNativeFruitingStardustTransport", lua_inspect_stack<TransportKind::FruitingStardust>);
        lua.register_function("SilverfishCommitNativeFruitingStardustTransport", lua_commit_stack<TransportKind::FruitingStardust>);
        lua.register_function("SilverfishRestoreNativeFruitingStardustTransport", lua_restore_stack<TransportKind::FruitingStardust>);
        lua.register_function("SilverfishTakeNativeRedTransportRequest", lua_take_transport_request<TransportKind::Red>);
        lua.register_function("SilverfishInspectNativeRedTransport", lua_inspect_cube<TransportKind::Red>);
        lua.register_function("SilverfishCommitNativeRedTransport", lua_commit_cube<TransportKind::Red>);
        lua.register_function("SilverfishTakeNativeGreenTransportRequest", lua_take_transport_request<TransportKind::Green>);
        lua.register_function("SilverfishInspectNativeGreenTransport", lua_inspect_cube<TransportKind::Green>);
        lua.register_function("SilverfishCommitNativeGreenTransport", lua_commit_cube<TransportKind::Green>);
        lua.register_function("SilverfishTakeNativePurpleTransportRequest", lua_take_transport_request<TransportKind::Purple>);
        lua.register_function("SilverfishInspectNativePurpleTransport", lua_inspect_cube<TransportKind::Purple>);
        lua.register_function("SilverfishCommitNativePurpleTransport", lua_commit_cube<TransportKind::Purple>);
        lua.register_function("SilverfishTakeNativeMatrixTransportRequest", lua_take_matrix_request);
        lua.register_function("SilverfishInspectNativeMatrixTransport", lua_inspect_matrix);
        if (fixtures_enabled())
        {
            lua.register_function("SilverfishSeedNativeGoldTransport", lua_seed_cube<TransportKind::Gold>);
            lua.register_function("SilverfishProbeNativeGoldTransport", lua_probe_transport<TransportKind::Gold>);
            lua.register_function("SilverfishProbeNativeGoldActionUse", lua_probe_gold_action);
            lua.register_function("SilverfishSeedNativeBlueTransport", lua_seed_cube<TransportKind::Blue>);
            lua.register_function("SilverfishProbeNativeBlueTransport", lua_probe_transport<TransportKind::Blue>);
            lua.register_function("SilverfishSeedNativeStardustTransport", lua_seed_stack<TransportKind::Stardust>);
            lua.register_function("SilverfishProbeNativeStardustTransport", lua_probe_transport<TransportKind::Stardust>);
            lua.register_function("SilverfishSeedNativeFruitingStardustTransport", lua_seed_stack<TransportKind::FruitingStardust>);
            lua.register_function("SilverfishProbeNativeFruitingStardustTransport", lua_probe_transport<TransportKind::FruitingStardust>);
            lua.register_function("SilverfishSeedNativeRedTransport", lua_seed_cube<TransportKind::Red>);
            lua.register_function("SilverfishProbeNativeRedTransport", lua_probe_transport<TransportKind::Red>);
            lua.register_function("SilverfishSeedNativeGreenTransport", lua_seed_cube<TransportKind::Green>);
            lua.register_function("SilverfishProbeNativeGreenTransport", lua_probe_transport<TransportKind::Green>);
            lua.register_function("SilverfishSeedNativePurpleTransport", lua_seed_cube<TransportKind::Purple>);
            lua.register_function("SilverfishProbeNativePurpleTransport", lua_probe_transport<TransportKind::Purple>);
            lua.register_function("SilverfishSeedNativeMatrixTransport", lua_seed_matrix);
            lua.register_function("SilverfishEquipNativeMatrixTransport", lua_equip_matrix);
            lua.register_function("SilverfishProbeNativeMatrixTransport", lua_probe_matrix);
        }
    }
}
