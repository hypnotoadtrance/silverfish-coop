#include "borough_barter.hpp"
#include "guest_armor.hpp"
#include "guest_item.hpp"
#include "guest_sickness.hpp"
#include "enemy_loot.hpp"
#include "loot_escrow.hpp"
#include "shared_loot.hpp"
#include "stock_wallet.hpp"
#include "npc_barter.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <functional>
#include <limits>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/Engine/UDataTable.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/Rotator.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;
    constexpr int capacity = 4096;
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough");
    constexpr auto npc_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.NPC_Mama_C_1");
    constexpr auto post_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.TradePost_BP_C_1080");
    constexpr auto copper_path = STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Intr_AP_BP.Ammo_Intr_AP_BP_C");
    constexpr auto base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto inventory_struct_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto observatory_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
    constexpr auto bench_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.StaticMeshActor_51");
    constexpr auto bench_class = STR("/Game/Assets/StaticMeshes/Mesh_Props/Furnature/CraftingBench_BP.CraftingBench_BP_C");
    constexpr auto board_path = STR("/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C");
    constexpr auto scrap_path = STR("/Game/Classes/Items/Item_ElectronicScrap.Item_ElectronicScrap_C");
    constexpr auto sewer_world = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P");
    constexpr auto sewer_npc = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.NPC_SewerTrader_C_1");
    constexpr auto sewer_post = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.TradePost_BP_C_1");
    constexpr auto sewer_trigger = STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P:PersistentLevel.Trigger_Volume_BP_C_1");
    constexpr int sewer_offer_count = 26;

    struct Recipe
    {
        const U::TCHAR* row;
        const U::TCHAR* payment;
        const U::TCHAR* reward;
        int index, offers, quantity, reward_value;
        double reward_weight;
        bool hide_unaffordable;
    };
    constexpr Recipe mama{STR("Trade_Mamma"), copper_path, ammo_path, 0, 5, 25, 8, 0.012, false};
    constexpr Recipe bench{STR("Crafting"), board_path, scrap_path, 14, 40, 1, 80, 0.2, true};

    bool flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return U::IsInGameThreadRaw() && flag("SILVERFISH_COOP_NATIVE_ZONE") && flag("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    template <typename T>
    T* find(const U::TCHAR* path) { return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path); }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    bool bool_value(U::UObject* owner, const U::TCHAR* name, const void* value, bool expected)
    {
        auto* prop = field<U::FBoolProperty>(owner, name);
        return prop && prop->GetPropertyValueInContainer(value) == expected;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = field<U::FObjectProperty>(owner, name);
        return prop ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool exact(U::UObject* value, const U::TCHAR* klass)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && value->GetClassPrivate() == find<U::UClass>(klass) &&
            !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool authority(U::UObject* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return role && role->GetPropertyValueInContainer(actor) == 3 &&
            bool_value(actor, STR("bActorIsBeingDestroyed"), actor, false) &&
            static_cast<U::AActor*>(actor)->GetWorld() == world && actor->GetOuterPrivate() == object(world, STR("PersistentLevel"));
    }

    bool near(U::UObject* pawn, U::UObject* npc)
    {
        auto* reach = field<U::FDoubleProperty>(pawn, STR("InteractionRange"));
        if (!reach) return false;
        const auto limit = reach->GetPropertyValueInContainer(pawn);
        auto first = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        auto second = static_cast<U::AActor*>(npc)->K2_GetActorLocation();
        const auto x = first.GetX() - second.GetX(), y = first.GetY() - second.GetY(), z = first.GetZ() - second.GetZ();
        const auto square = x * x + y * y + z * z;
        return std::isfinite(limit) && limit > 0 && limit <= 1000 && std::isfinite(square) && square <= limit * limit;
    }

    bool one_entry(U::UScriptStruct* schema, const void* row, const U::TCHAR* name, U::UClass* item, int quantity)
    {
        auto* map = field<U::FMapProperty>(schema, name);
        auto* key = map ? U::CastField<U::FClassProperty>(map->GetKeyProp()) : nullptr;
        auto* count = map ? U::CastField<U::FIntProperty>(map->GetValueProp()) : nullptr;
        if (!key || !count || key->GetMetaClass().Get() != find<U::UClass>(base_path) || key->GetOffset_Internal() != 0)
            return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), count->GetSize(), count->GetMinAlignment());
        const auto* values = map->ContainerPtrToValuePtr<U::FScriptMap>(row);
        if (count->GetOffset_Internal() != layout.ValueOffset || values->Num() != 1 || values->GetMaxIndex() > 16) return false;
        for (int index = 0; index < values->GetMaxIndex(); ++index)
        {
            if (!values->IsValidIndex(index)) continue;
            const auto* entry = values->GetData(index, layout);
            return entry && key->GetObjectPropertyValue(entry) == item && count->GetPropertyValueInContainer(entry) == quantity;
        }
        return false;
    }

    U::UObject* crafting_post(U::UObject* pawn, U::UWorld* world)
    {
        auto* actor = find<U::UObject>(bench_path);
        if (!exact(actor, bench_class) || !authority(actor, world) || !near(pawn, actor) ||
            object(actor, STR("As Game Instance FP")) != object(world, STR("OwningGameInstance"))) return nullptr;
        auto location = static_cast<U::AActor*>(actor)->K2_GetActorLocation();
        if (!(std::abs(location.GetX() + 5796.37) < 1.0 && std::abs(location.GetY() - 669.75) < 1.0 &&
              std::abs(location.GetZ() - 150.0) < 1.0)) return nullptr;
        auto* component = object(actor, STR("TradePost_BP"));
        auto* box = object(actor, STR("Box"));
        if (!exact(component, STR("/Script/Engine.ChildActorComponent")) || component->GetOuterPrivate() != actor ||
            !exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != actor) return nullptr;
        return object(component, STR("ChildActor"));
    }

    bool crafting_value()
    {
        auto* object = find<U::UObject>(STR("/Game/Classes/Libraries/DataTables/PassiveEffects_MetaData_DT.PassiveEffects_MetaData_DT"));
        if (!exact(object, STR("/Script/Engine.DataTable"))) return false;
        auto* table = static_cast<U::UDataTable*>(object);
        auto* schema = table->GetRowStruct().Get();
        auto* value = field<U::FDoubleProperty>(schema, STR("Value_16_86F011AF491996019F586C922A602871"));
        const U::FName name{STR("Crafting"), U::FNAME_Find};
        const auto* row = !name.IsNone() && name.ToString() == STR("Crafting") ? table->FindRowUnchecked(name) : nullptr;
        return value && row && value->GetPropertyValueInContainer(row) == 1.0 &&
            bool_value(schema, STR("Stacks_13_4531363945A1C92F2EBF0592D2CFF62F"), row, false);
    }

    bool qualified_recipe(U::UObject* post, U::UClass* payment, U::UClass* reward, const Recipe& profile)
    {
        auto* row_schema = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Inventory_Struct.Trade_Inventory_Struct"));
        auto* offer_schema = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Struct.Trade_Struct"));
        auto* table_object = find<U::UObject>(STR("/Game/Classes/Libraries/DataTables/TradeInventories_DT.TradeInventories_DT"));
        auto* handle = field<U::FStructProperty>(post, STR("ShopInventory"));
        auto* inventory = field<U::FStructProperty>(post, STR("ShopInv"));
        auto* handle_type = find<U::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table_field = field<U::FObjectProperty>(handle_type, STR("DataTable"));
        auto* name = field<U::FNameProperty>(handle_type, STR("RowName"));
        const U::FName row_name{profile.row, U::FNAME_Find};
        if (!row_schema || !offer_schema || !exact(table_object, STR("/Script/Engine.DataTable")) ||
            !handle || !handle_type || handle->GetStruct().Get() != handle_type || !table_field || !name ||
            !inventory || inventory->GetStruct().Get() != row_schema || row_name.IsNone() || row_name.ToString() != profile.row ||
            !bool_value(post, STR("Generate_RandomTrades"), post, false)) return false;
        const auto* handle_value = handle->ContainerPtrToValuePtr<void>(post);
        auto* table = static_cast<U::UDataTable*>(table_object);
        if (table->GetRowStruct().Get() != row_schema || name->GetPropertyValueInContainer(handle_value) != row_name ||
            table_field->GetObjectPropertyValue(table_field->ContainerPtrToValuePtr<void>(handle_value)) != table) return false;

        auto* offers = field<U::FArrayProperty>(row_schema, STR("TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F"));
        auto* inner = offers ? U::CastField<U::FStructProperty>(offers->GetInner()) : nullptr;
        auto* percent = field<U::FDoubleProperty>(row_schema, STR("PercentageShown_10_A0EA59BD4DAA778C39DDB080BA16FD67"));
        auto* rep = field<U::FMapProperty>(row_schema, STR("RepGain_16_EBE7110445B7872B32796C8504C42DEB"));
        auto* chance = field<U::FDoubleProperty>(offer_schema, STR("ChanceShown_4_18C5CA7A480470CDF14CAD8FBF228FF7"));
        auto* give = field<U::FIntProperty>(offer_schema, STR("Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F"));
        auto* receive = field<U::FIntProperty>(offer_schema, STR("Cash_Recive_12_FE560CBF4176840FDE3480B7FFB4C4F2"));
        if (!inner || inner->GetStruct().Get() != offer_schema || !percent || !rep || !chance || !give || !receive) return false;

        const auto* cooked = table->FindRowUnchecked(row_name);
        const auto* active = inventory->ContainerPtrToValuePtr<void>(post);
        if (!cooked || percent->GetPropertyValueInContainer(cooked) != 1.0 ||
            percent->GetPropertyValueInContainer(active) != 1.0 ||
            rep->ContainerPtrToValuePtr<U::FScriptMap>(cooked)->Num() != 0 ||
            rep->ContainerPtrToValuePtr<U::FScriptMap>(active)->Num() != 0) return false;
        U::FScriptArrayHelper authored(offers, offers->ContainerPtrToValuePtr<void>(cooked));
        U::FScriptArrayHelper live(offers, offers->ContainerPtrToValuePtr<void>(active));
        if (authored.Num() != profile.offers || live.Num() != profile.offers) return false;
        const auto* recipe = authored.GetRawPtr(profile.index);
        if (chance->GetPropertyValueInContainer(recipe) != 1.0 || give->GetPropertyValueInContainer(recipe) != 0 ||
            receive->GetPropertyValueInContainer(recipe) != 0 ||
            !bool_value(offer_schema, STR("RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2"), recipe, false) ||
            !bool_value(offer_schema, STR("Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174"), recipe, profile.hide_unaffordable) ||
            !one_entry(offer_schema, recipe, STR("Items_Give_20_B4929C76420B737D08B6E7A56384E8EC"), payment, 1) ||
            !one_entry(offer_schema, recipe, STR("Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD"), reward, profile.quantity))
            return false;
        int matches = 0;
        for (int index = 0; index < live.Num(); ++index)
            if (inner->Identical(recipe, live.GetRawPtr(index))) ++matches;
        return matches == 1;
    }

    struct Destroy
    {
        U::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, Destroy>;

    Value copy(U::FProperty* property, const void* source)
    {
        Value result(property->AllocateAndInitializeValue(), {property});
        if (result) property->CopyCompleteValue(result.get(), source);
        return result;
    }

    struct Inventory
    {
        U::UScriptStruct* type = find<U::UScriptStruct>(inventory_struct_path);
        U::FClassProperty* item = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FIntProperty* units = field<U::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        U::FIntProperty* value = field<U::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        U::FDoubleProperty* weight = field<U::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        U::FStructProperty* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        U::FNameProperty* tag = field<U::FNameProperty>(type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        U::FStructProperty* data = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        U::FBoolProperty* never = field<U::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        std::array<U::FIntProperty*, 4> words{};
        U::FArrayProperty* backpack{};
        U::FArrayProperty* equipment{};
        U::FStructProperty* armor{};
        U::FStructProperty* helmet{};

        bool row_is(U::FProperty* property) const
        {
            const auto* row = U::CastField<U::FStructProperty>(property);
            return row && row->GetArrayDim() == 1 && row->GetStruct().Get() == type;
        }

        bool bind(U::UObject* save)
        {
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            auto* payload = find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
            auto* base = find<U::UClass>(base_path);
            if (!item || !base || item->GetMetaClass().Get() != base || !units || !value || !weight || !guid || !guid_type ||
                guid->GetStruct().Get() != guid_type || guid->GetSize() != sizeof(Guid) || !tag || !never ||
                !data || !payload || data->GetStruct().Get() != payload) return false;
            constexpr std::array names{STR("A"), STR("B"), STR("C"), STR("D")};
            for (std::size_t i = 0; i < words.size(); ++i)
            {
                words[i] = field<U::FIntProperty>(guid_type, names[i]);
                if (!words[i] || words[i]->GetOffset_Internal() != static_cast<int>(i * sizeof(std::uint32_t))) return false;
            }
            backpack = field<U::FArrayProperty>(save, STR("PlayerBackpack"));
            equipment = field<U::FArrayProperty>(save, STR("PlayerEquipment"));
            armor = field<U::FStructProperty>(save, STR("PlayerArmor"));
            helmet = field<U::FStructProperty>(save, STR("PlayerHelmet"));
            return backpack && equipment && row_is(backpack->GetInner()) && row_is(equipment->GetInner()) &&
                row_is(armor) && row_is(helmet);
        }

        Guid identity(const void* row) const
        {
            const auto* payload = guid->ContainerPtrToValuePtr<void>(row);
            Guid result{};
            for (std::size_t i = 0; i < result.size(); ++i)
                result[i] = static_cast<std::uint32_t>(words[i]->GetPropertyValueInContainer(payload));
            return result;
        }

        U::UObject* klass(const void* row) const
        {
            return item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(row));
        }

        bool rows(U::UObject* save, auto&& visit) const
        {
            for (auto* field : {backpack, equipment})
            {
                U::FScriptArrayHelper rows(field, field->ContainerPtrToValuePtr<void>(save));
                if (rows.Num() < 0 || rows.Num() > capacity) return false;
                for (int i = 0; i < rows.Num(); ++i) visit(rows.GetRawPtr(i), field == backpack ? i : -1);
            }
            visit(armor->ContainerPtrToValuePtr<void>(save), -1);
            visit(helmet->ContainerPtrToValuePtr<void>(save), -1);
            return true;
        }

        bool matches_defaults(const void* row, U::UObject* defaults) const
        {
            auto* stock_tag = field<U::FNameProperty>(defaults, STR("DataTag"));
            auto* stock_data = field<U::FStructProperty>(defaults, STR("DataStruct"));
            auto* stock_never = field<U::FBoolProperty>(defaults, STR("NeverDrop"));
            return stock_tag && stock_data && stock_never && stock_data->GetStruct().Get() == data->GetStruct().Get() &&
                tag->GetPropertyValueInContainer(row) == stock_tag->GetPropertyValueInContainer(defaults) &&
                never->GetPropertyValueInContainer(row) == stock_never->GetPropertyValueInContainer(defaults) &&
                data->Identical(data->ContainerPtrToValuePtr<void>(row), stock_data->ContainerPtrToValuePtr<void>(defaults));
        }
    };

    bool stack_totals(const Inventory& inventory, const void* row, int units, const Recipe& recipe)
    {
        return units > 0 && units <= std::numeric_limits<int>::max() / recipe.reward_value &&
            inventory.units->GetPropertyValueInContainer(row) == units &&
            inventory.value->GetPropertyValueInContainer(row) == recipe.reward_value * units &&
            std::abs(inventory.weight->GetPropertyValueInContainer(row) - recipe.reward_weight * units) < 1e-9;
    }

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool output)
    {
        auto* prop = field<T>(function, name);
        return prop && prop->GetOffset_Internal() == offset && prop->HasAnyPropertyFlags(U::CPF_Parm) &&
            prop->HasAnyPropertyFlags(U::CPF_OutParm) == output ? prop : nullptr;
    }

    Value make_stack(const Inventory& layout, U::UObject* pawn, U::UClass* item, const Recipe& profile)
    {
        auto* function = find<U::UFunction>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeItemStack"));
        auto* library = find<U::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
        auto* klass = parameter<U::FClassProperty>(function, STR("Item_Class"), 0, false);
        auto* count = parameter<U::FIntProperty>(function, STR("Count"), 8, false);
        auto* context = parameter<U::FObjectProperty>(function, STR("__WorldContext"), 16, false);
        auto* out = parameter<U::FStructProperty>(function, STR("Item"), 24, true);
        constexpr std::size_t max_size = 256;
        auto* defaults = item->GetClassDefaultObject().Get();
        auto* unit_weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        auto* unit_value = field<U::FIntProperty>(defaults, STR("Value"));
        if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !klass || klass->GetMetaClass().Get() != layout.item->GetMetaClass().Get() || !count || !context ||
            !layout.row_is(out) || out->GetOffset_Internal() + out->GetSize() > function->GetParmsSize() ||
            function->GetParmsSize() > max_size || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), defaults, true) ||
            !unit_weight || unit_weight->GetPropertyValueInContainer(defaults) != profile.reward_weight ||
            !unit_value || unit_value->GetPropertyValueInContainer(defaults) != profile.reward_value) return {};

        alignas(std::max_align_t) std::array<std::byte, max_size> bytes{};
        out->InitializeValue_InContainer(bytes.data());
        const auto cleanup = [out](std::byte* container) { out->DestroyValue_InContainer(container); };
        std::unique_ptr<std::byte, decltype(cleanup)> guard(bytes.data(), cleanup);
        klass->SetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(bytes.data()), item);
        count->SetPropertyValueInContainer(bytes.data(), profile.quantity);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(bytes.data()), pawn);
        library->ProcessEvent(function, bytes.data());
        const auto* result = out->ContainerPtrToValuePtr<void>(bytes.data());
        if (layout.identity(result) == Guid{} || layout.klass(result) != item || !layout.matches_defaults(result, defaults) ||
            !stack_totals(layout, result, profile.quantity, profile)) return {};
        return copy(out, result);
    }

    int seed_crafting_scrap(const Lua& lua)
    {
        const auto result = [&](bool ok, std::string_view reason) {
            lua.set_bool(ok);
            lua.set_string(reason);
            return 2;
        };
        if (!enabled() || !flag("SILVERFISH_COOP_HEADLESS") || !flag("SILVERFISH_COOP_NATIVE_FIXTURES") ||
            lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return result(false, "crafting_scrap_fixture_unavailable");
        const auto pawn = lua.get_integer(), save = lua.get_integer(), stock = lua.get_integer();
        auto* world = find<U::UWorld>(observatory_path);
        silverfish::shared_loot::EscrowOwner owner;
        if (pawn <= 0 || save <= 0 || stock <= 0 || !world ||
            !silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, owner))
            return result(false, "crafting_scrap_fixture_owner_unavailable");
        Inventory layout;
        auto* item = find<U::UClass>(scrap_path);
        auto* flags = field<U::FMapProperty>(owner.stock, STR("Flags"));
        if (!item || !flags || !layout.bind(owner.save)) return result(false, "crafting_scrap_fixture_schema_unavailable");
        auto* stock_flags = flags->ContainerPtrToValuePtr<void>(owner.stock);
        auto* backpack = layout.backpack->ContainerPtrToValuePtr<void>(owner.save);
        const auto old_flags = copy(flags, stock_flags);
        const auto old_pack = copy(layout.backpack, backpack);
        const auto prepared = copy(layout.backpack, backpack);
        if (!old_flags || !old_pack || !prepared) return result(false, "crafting_scrap_fixture_snapshot_unavailable");
        auto fixture = bench;
        fixture.quantity = 2;
        const auto stack = make_stack(layout, owner.pawn, item, fixture);
        if (!flags->Identical(stock_flags, old_flags.get()) || !layout.backpack->Identical(backpack, old_pack.get()))
            return result(false, "mutation_unverified_crafting_scrap_fixture_create");
        if (!stack) return result(false, "crafting_scrap_fixture_stack_unavailable");
        const auto id = layout.identity(stack.get());
        bool conflict{};
        for (auto* frame : {owner.save, owner.stock})
            if (!layout.rows(frame, [&](const void* row, int) { conflict |= layout.identity(row) == id ||
                    (frame == owner.save && layout.klass(row) == item); }))
                return result(false, "crafting_scrap_fixture_inventory_unavailable");
        U::FScriptArrayHelper replacement(layout.backpack, prepared.get());
        if (conflict || replacement.Num() >= capacity) return result(false, "crafting_scrap_fixture_inventory_conflict");
        auto* target = replacement.GetRawPtr(replacement.AddValues(1));
        auto* row = layout.backpack->GetInner();
        row->CopyCompleteValue(target, stack.get());
        if (!row->Identical(target, stack.get())) return result(false, "crafting_scrap_fixture_prepared_unverified");
        layout.backpack->CopyCompleteValue(backpack, prepared.get());
        if (layout.backpack->Identical(backpack, prepared.get())) return result(true, "seeded");
        layout.backpack->CopyCompleteValue(backpack, old_pack.get());
        return result(false, layout.backpack->Identical(backpack, old_pack.get())
            ? "crafting_scrap_fixture_rolled_back" : "mutation_unverified_crafting_scrap_fixture_commit");
    }

    Guid parse_guid(std::string_view input)
    {
        Guid result{};
        if (input.size() != 32 || !std::ranges::all_of(input, [](char c) { return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'); }))
            return {};
        for (std::size_t i = 0; i < result.size(); ++i)
        {
            const auto* start = input.data() + i * 8;
            const auto converted = std::from_chars(start, start + 8, result[i], 16);
            if (converted.ec != std::errc{} || converted.ptr != start + 8) return {};
        }
        return result;
    }

    struct TradeAmount
    {
        U::UClass* item{};
        int count{};
    };

    struct SewerOffer
    {
        std::vector<TradeAmount> payment;
        TradeAmount reward;
        bool hidden{};
    };

    std::vector<TradeAmount> trade_amounts(U::UScriptStruct* schema, const void* row,
                                          const U::TCHAR* name, int limit, bool allow_zero)
    {
        auto* map = field<U::FMapProperty>(schema, name);
        auto* key = map ? U::CastField<U::FClassProperty>(map->GetKeyProp()) : nullptr;
        auto* value = map ? U::CastField<U::FIntProperty>(map->GetValueProp()) : nullptr;
        auto* base = find<U::UClass>(base_path);
        if (!key || !value || !base || key->GetMetaClass().Get() != base || key->GetOffset_Internal() != 0) return {};
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        auto* values = map->ContainerPtrToValuePtr<U::FScriptMap>(row);
        if (value->GetOffset_Internal() != layout.ValueOffset || values->Num() < 1 || values->Num() > limit ||
            values->GetMaxIndex() > 16) return {};
        std::vector<TradeAmount> result;
        for (int index = 0; index < values->GetMaxIndex(); ++index)
        {
            if (!values->IsValidIndex(index)) continue;
            auto* entry = values->GetData(index, layout);
            auto* item = entry ? static_cast<U::UClass*>(key->GetObjectPropertyValue(entry)) : nullptr;
            const auto count = entry ? value->GetPropertyValueInContainer(entry) : -1;
            if (!item || !item->IsChildOf(base) || item->IsUnreachable() || count < (allow_zero ? 0 : 1) ||
                count > 100 || std::ranges::any_of(result, [item](const TradeAmount& prior) { return prior.item == item; }))
                return {};
            result.push_back({item, count});
        }
        return result;
    }

    bool sewer_offers(U::UObject* post, std::array<SewerOffer, sewer_offer_count>& result)
    {
        auto* table = find<U::UDataTable>(STR("/Game/Classes/Libraries/DataTables/TradeInventories_DT.TradeInventories_DT"));
        auto* schema = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Inventory_Struct.Trade_Inventory_Struct"));
        auto* offer_type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Struct.Trade_Struct"));
        auto* handle_type = find<U::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* handle = field<U::FStructProperty>(post, STR("ShopInventory"));
        auto* inventory = field<U::FStructProperty>(post, STR("ShopInv"));
        auto* handle_table = field<U::FObjectProperty>(handle_type, STR("DataTable"));
        auto* handle_name = field<U::FNameProperty>(handle_type, STR("RowName"));
        const U::FName name{STR("SewerThing"), U::FNAME_Find};
        if (!exact(table, STR("/Script/Engine.DataTable")) || !schema || !offer_type || !handle_type || !handle ||
            handle->GetStruct().Get() != handle_type || !inventory || inventory->GetStruct().Get() != schema ||
            !handle_table || !handle_name || name.IsNone() || name.ToString() != STR("SewerThing") ||
            !bool_value(post, STR("Generate_RandomTrades"), post, false) || table->GetRowStruct().Get() != schema) return false;
        const auto* handle_value = handle->ContainerPtrToValuePtr<void>(post);
        if (handle_name->GetPropertyValueInContainer(handle_value) != name ||
            handle_table->GetObjectPropertyValue(handle_table->ContainerPtrToValuePtr<void>(handle_value)) != table) return false;

        auto* offers = field<U::FArrayProperty>(schema, STR("TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F"));
        auto* row_type = offers ? U::CastField<U::FStructProperty>(offers->GetInner()) : nullptr;
        auto* shown = field<U::FDoubleProperty>(schema, STR("PercentageShown_10_A0EA59BD4DAA778C39DDB080BA16FD67"));
        auto* reputation = field<U::FMapProperty>(schema, STR("RepGain_16_EBE7110445B7872B32796C8504C42DEB"));
        auto* chance = field<U::FDoubleProperty>(offer_type, STR("ChanceShown_4_18C5CA7A480470CDF14CAD8FBF228FF7"));
        auto* cash_give = field<U::FIntProperty>(offer_type, STR("Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F"));
        auto* cash_receive = field<U::FIntProperty>(offer_type, STR("Cash_Recive_12_FE560CBF4176840FDE3480B7FFB4C4F2"));
        auto* hide = field<U::FBoolProperty>(offer_type, STR("Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174"));
        if (!row_type || row_type->GetStruct().Get() != offer_type || !shown || !reputation || !chance ||
            !cash_give || !cash_receive || !hide) return false;
        const auto* cooked = table->FindRowUnchecked(name);
        const auto* live = inventory->ContainerPtrToValuePtr<void>(post);
        if (!cooked || shown->GetPropertyValueInContainer(cooked) != 1.0 || shown->GetPropertyValueInContainer(live) != 1.0 ||
            reputation->ContainerPtrToValuePtr<U::FScriptMap>(cooked)->Num() != 0 ||
            reputation->ContainerPtrToValuePtr<U::FScriptMap>(live)->Num() != 0) return false;
        U::FScriptArrayHelper authored(offers, offers->ContainerPtrToValuePtr<void>(cooked));
        U::FScriptArrayHelper active(offers, offers->ContainerPtrToValuePtr<void>(live));
        if (authored.Num() != sewer_offer_count || active.Num() != sewer_offer_count) return false;

        auto* weapon = find<U::UClass>(STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"));
        if (!weapon) return false;
        for (int index = 0; index < sewer_offer_count; ++index)
        {
            const auto* stock = authored.GetRawPtr(index);
            if (chance->GetPropertyValueInContainer(stock) != 1.0 || cash_give->GetPropertyValueInContainer(stock) != 0 ||
                cash_receive->GetPropertyValueInContainer(stock) != 0 ||
                !bool_value(offer_type, STR("RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2"), stock, false)) return false;
            int matches{};
            for (int check = 0; check < active.Num(); ++check) matches += row_type->Identical(stock, active.GetRawPtr(check));
            if (matches != 1) return false;
            auto give = trade_amounts(offer_type, stock, STR("Items_Give_20_B4929C76420B737D08B6E7A56384E8EC"), 3, false);
            auto receive = trade_amounts(offer_type, stock, STR("Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD"), 1, true);
            if (give.empty() || receive.size() != 1 || (!receive[0].count && !receive[0].item->IsChildOf(weapon))) return false;
            result[index] = {std::move(give), receive.front(), hide->GetPropertyValueInContainer(stock)};
        }
        return true;
    }

    bool inside_sewer_trigger(U::UObject* pawn, U::UWorld* world)
    {
        auto* trigger = find<U::UObject>(sewer_trigger);
        auto* box = object(trigger, STR("Box"));
        auto* query = find<U::UFunction>(STR("/Script/Engine.PrimitiveComponent:IsOverlappingActor"));
        auto* target = parameter<U::FObjectProperty>(query, STR("Other"), 0, false);
        auto* answer = field<U::FBoolProperty>(query, STR("ReturnValue"));
        if (!exact(trigger, STR("/Game/Classes/ScriptingActors/Trigger_Volume_BP.Trigger_Volume_BP_C")) ||
            !authority(trigger, world) || !exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != trigger ||
            !query || !target || !answer || query->GetNumParms() != 2 || query->GetReturnProperty() != answer ||
            answer->GetOffset_Internal() != sizeof(void*) || !query->HasAnyFunctionFlags(U::FUNC_Native)) return false;
        struct Overlap { U::UObject* pawn; bool inside{}; } args{pawn};
        if (query->GetParmsSize() < offsetof(Overlap, inside) + sizeof(bool) || query->GetParmsSize() > sizeof(args)) return false;
        box->ProcessEvent(query, &args);
        return answer->GetPropertyValueInContainer(&args);
    }

    struct Debit
    {
        Guid guid{};
        TradeAmount amount;
        int index{}, remaining{};
    };

    bool pay_offer(const Inventory& layout, U::UObject* save, const SewerOffer& offer, std::vector<Debit>& debits)
    {
        for (auto [item, needed] : offer.payment)
        {
            auto* defaults = item->GetClassDefaultObject().Get();
            auto* stacks = field<U::FBoolProperty>(defaults, STR("Stacks"));
            if (!stacks) return false;
            const bool stackable = stacks->GetPropertyValueInContainer(defaults);
            if (!layout.rows(save, [&](const void* row, int index) {
                    if (!needed || index < 0 || layout.klass(row) != item || (stackable && !layout.matches_defaults(row, defaults))) return;
                    const auto id = layout.identity(row);
                    const auto units = layout.units->GetPropertyValueInContainer(row);
                    if (id == Guid{} || (stackable ? units <= 0 : units != 0)) return;
                    const auto used = stackable ? std::min(units, needed) : 1;
                    debits.push_back({id, {item, used}, index, stackable ? units - used : 0});
                    needed -= used;
                }) || needed) return false;
        }
        return true;
    }

    std::string stock_class(U::UClass* item)
    {
        return RC::to_utf8_string(item->GetPathName());
    }

    std::string guid_string(const Guid& guid)
    {
        return std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]);
    }

    int sewer_quantity(const SewerOffer& offer)
    {
        auto* defaults = offer.reward.item->GetClassDefaultObject().Get();
        return bool_value(defaults, STR("Stacks"), defaults, true) ? offer.reward.count : 1;
    }

    Value sewer_reward(const silverfish::shared_loot::EscrowOwner& owner, const Inventory& layout,
                        const SewerOffer& offer, bool& retired)
    {
        struct Deferred
        {
            U::UObject* context{};
            U::UClass* item{};
            U::FTransform_As500Plus transform;
            std::uint8_t collision{1};
            U::UObject* owner{};
            std::uint8_t scale{1};
            U::AActor* result{};
        } begin;
        struct Finished
        {
            U::AActor* actor{};
            U::FTransform_As500Plus transform;
            std::uint8_t scale{1};
            U::AActor* result{};
        } finish;
        static_assert(offsetof(Deferred, transform) == 0x10 && offsetof(Deferred, result) == 0x88);
        static_assert(offsetof(Finished, transform) == 0x10 && offsetof(Finished, result) == 0x78);
        const auto* actor_type = find<U::UClass>(STR("/Script/Engine.Actor"));
        auto* transform = find<U::UScriptStruct>(STR("/Script/CoreUObject.Transform"));
        auto* statics = find<U::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
        auto* spawn = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:BeginDeferredActorSpawnFromClass"));
        auto* complete = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:FinishSpawningActor"));
        auto* spawn_type = parameter<U::FClassProperty>(spawn, STR("ActorClass"), 8, false);
        auto* start_transform = field<U::FStructProperty>(spawn, STR("SpawnTransform"));
        auto* end_transform = field<U::FStructProperty>(complete, STR("SpawnTransform"));
        auto* spawn_result = field<U::FObjectProperty>(spawn, STR("ReturnValue"));
        auto* complete_result = field<U::FObjectProperty>(complete, STR("ReturnValue"));
        if (!actor_type || !transform || !statics || !spawn || !complete || !spawn_type ||
            spawn_type->GetMetaClass().Get() != actor_type || !start_transform || start_transform->GetStruct().Get() != transform ||
            start_transform->GetOffset_Internal() != 0x10 || start_transform->GetSize() != 0x60 ||
            !end_transform || end_transform->GetStruct().Get() != transform || end_transform->GetOffset_Internal() != 0x10 ||
            end_transform->GetSize() != 0x60 || !spawn_result || !complete_result ||
            spawn_result->GetPropertyClass().Get() != actor_type || complete_result->GetPropertyClass().Get() != actor_type ||
            spawn_result->GetOffset_Internal() != 0x88 || complete_result->GetOffset_Internal() != 0x78 ||
            spawn->GetReturnProperty() != spawn_result || complete->GetReturnProperty() != complete_result ||
            spawn->GetOuterPrivate() != statics->GetClassPrivate() || complete->GetOuterPrivate() != statics->GetClassPrivate() ||
            !spawn->HasAnyFunctionFlags(U::FUNC_Native) || !complete->HasAnyFunctionFlags(U::FUNC_Native) ||
            spawn->GetNumParms() != 7 || spawn->GetParmsSize() != 0x90 ||
            complete->GetNumParms() != 4 || complete->GetParmsSize() != 0x80) return {};
        auto* item = offer.reward.item;
        auto* stock = item->GetClassDefaultObject().Get();
        auto* default_ammo = field<U::FIntProperty>(stock, STR("Ammo"));
        auto* weapon = find<U::UClass>(STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"));
        if (!default_ammo || !weapon) return {};
        const int units = item->IsChildOf(weapon) ? 0 : offer.reward.count > 0 ? offer.reward.count :
            default_ammo->GetPropertyValueInContainer(stock);
        auto location = static_cast<U::AActor*>(owner.pawn)->K2_GetActorLocation();
        location.SetZ(location.GetZ() + 1500.0);
        begin.context = owner.pawn;
        begin.owner = owner.pawn;
        begin.item = item;
        begin.transform = U::FTransform{U::FRotator(0.0, 0.0, 0.0).Quaternion(), location, U::FVector(1, 1, 1)};
        statics->ProcessEvent(spawn, &begin);
        auto* actor = begin.result;
        if (!actor) return {};
        retired = false;
        actor->SetFlags(U::RF_Transient);
        actor->SetActorEnableCollision(false);
        actor->SetActorHiddenInGame(true);
        auto* row = layout.backpack->GetInner();
        Value reward(row->AllocateAndInitializeValue(), {row});
        auto* amount = field<U::FIntProperty>(actor, STR("Ammo"));
        auto* respawn = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        auto* instigator = field<U::FObjectProperty>(actor, STR("Instigator"));
        const bool qualified = reward && actor->GetClassPrivate() == item && authority(actor, owner.world) &&
            !actor->HasAnyFlags(U::RF_WasLoaded) && amount && respawn && instigator && units >= 0;
        bool converted{};
        if (qualified)
        {
            amount->SetPropertyValueInContainer(actor, units);
            respawn->SetPropertyValueInContainer(actor, 0);
            instigator->SetObjectPropertyValue(instigator->ContainerPtrToValuePtr<void>(actor), owner.pawn);
            finish.actor = actor;
            finish.transform = begin.transform;
            statics->ProcessEvent(complete, &finish);
            if (finish.result == actor && authority(actor, owner.world) && amount->GetPropertyValueInContainer(actor) == units)
            {
                actor->SetActorEnableCollision(false);
                actor->SetActorHiddenInGame(true);
                converted = silverfish::loot_escrow::convert_stock_item(owner.pawn, actor, reward.get());
            }
        }
        actor->K2_DestroyActor();
        retired = !authority(actor, owner.world);
        if (!converted || !retired || layout.klass(reward.get()) != item ||
            layout.units->GetPropertyValueInContainer(reward.get()) != units) return {};
        return reward;
    }

    bool stock_stack(const Inventory& layout, const void* row, int count, U::UObject* defaults)
    {
        auto* value = field<U::FIntProperty>(defaults, STR("Value"));
        auto* weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        if (!value || !weight) return false;
        const int unit_value = value->GetPropertyValueInContainer(defaults);
        const double unit_weight = weight->GetPropertyValueInContainer(defaults);
        const auto total = static_cast<std::int64_t>(unit_value) * count;
        return count > 0 && std::isfinite(unit_weight) && unit_weight >= 0 &&
            total >= 0 && total <= std::numeric_limits<int>::max() &&
            layout.value->GetPropertyValueInContainer(row) == total &&
            layout.units->GetPropertyValueInContainer(row) == count &&
            std::abs(layout.weight->GetPropertyValueInContainer(row) - unit_weight * count) < 1e-8;
    }

    bool set_stack_quantity(const Inventory& layout, void* row, int count, U::UObject* defaults)
    {
        auto* value = field<U::FIntProperty>(defaults, STR("Value"));
        auto* weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        if (!value || !weight || count <= 0) return false;
        const auto total = static_cast<std::int64_t>(value->GetPropertyValueInContainer(defaults)) * count;
        const auto mass = weight->GetPropertyValueInContainer(defaults) * count;
        if (total < 0 || total > std::numeric_limits<int>::max() || !std::isfinite(mass) || mass < 0) return false;
        layout.units->SetPropertyValueInContainer(row, count);
        layout.value->SetPropertyValueInContainer(row, static_cast<int>(total));
        layout.weight->SetPropertyValueInContainer(row, mass);
        return stock_stack(layout, row, count, defaults);
    }

    int sewer_trade(const Lua& lua)
    {
        const auto reject = [&](std::string_view reason) {
            lua.set_bool(false);
            lua.set_string(reason);
            return 2;
        };
        if (!enabled() || lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_integer(4)) return reject("sewer_trade_refused_arguments");
        const auto pawn = lua.get_integer(), save = lua.get_integer(), stock = lua.get_integer(), requested = lua.get_integer();
        const bool fixture = requested == -1 && flag("SILVERFISH_COOP_HEADLESS") && flag("SILVERFISH_COOP_NATIVE_FIXTURES");
        if (pawn <= 0 || save <= 0 || stock <= 0 || stock == save || (!fixture && requested < 0) || requested > sewer_offer_count)
            return reject("sewer_trade_refused_arguments");
        auto* world = find<U::UWorld>(sewer_world);
        silverfish::shared_loot::EscrowOwner owner;
        if (!world || !silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, owner) ||
            !inside_sewer_trigger(owner.pawn, world)) return reject("sewer_trade_refused_guest_scope");
        auto* npc = find<U::UObject>(sewer_npc);
        auto* post = find<U::UObject>(sewer_post);
        if (!exact(npc, STR("/Game/Classes/NPCs/NPC_Characters/NPC_SewerTrader.NPC_SewerTrader_C")) || !authority(npc, world) ||
            !bool_value(npc, STR("Alive"), npc, true) || !exact(post, STR("/Game/Classes/Actors/TradePost_BP.TradePost_BP_C")) ||
            !authority(post, world)) return reject("sewer_trade_refused_stock_scene");
        Inventory layout;
        std::array<SewerOffer, sewer_offer_count> offers;
        if (!layout.bind(owner.save) || !sewer_offers(post, offers)) return reject("sewer_trade_refused_stock_offers");
        if (!requested)
        {
            std::string encoded;
            for (std::size_t index = 0; index < offers.size(); ++index)
            {
                const auto& offer = offers[index];
                std::vector<Debit> debits;
                const bool affordable = pay_offer(layout, owner.save, offer, debits);
                encoded += std::format("{}|{}|{}|{}|{}", index + 1, static_cast<int>(affordable),
                    static_cast<int>(offer.hidden), stock_class(offer.reward.item), sewer_quantity(offer));
                for (const auto& [item, quantity] : offer.payment) encoded += std::format("|{}|{}", stock_class(item), quantity);
                encoded += '\n';
            }
            lua.set_bool(true);
            lua.set_string("offers");
            lua.set_string(encoded);
            return 3;
        }

        SewerOffer fixture_offer;
        if (fixture)
        {
            if (offers[1].payment.size() != 1 || offers[1].payment.front().count != 1)
                return reject("sewer_trade_refused_fixture_offer");
            auto* item = offers[1].payment.front().item;
            auto* defaults = item->GetClassDefaultObject().Get();
            auto* stacks = field<U::FBoolProperty>(defaults, STR("Stacks"));
            if (!stacks || item->GetPathName() != STR("/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"))
                return reject("sewer_trade_refused_fixture_mushroom_class");
            fixture_offer.reward = {item, stacks->GetPropertyValueInContainer(defaults) ? 1 : 0};
        }
        const auto& offer = fixture ? fixture_offer : offers[static_cast<std::size_t>(requested - 1)];
        std::vector<Debit> debits;
        if (!pay_offer(layout, owner.save, offer, debits)) return reject("sewer_trade_refused_unaffordable");
        for (const auto& entry : debits)
        {
            int matches{};
            for (auto* frame : {owner.save, owner.stock})
                if (!layout.rows(frame, [&](const void* row, int) { matches += layout.identity(row) == entry.guid; }))
                    return reject("sewer_trade_refused_inventory_bounds");
            if (matches != 1) return reject("sewer_trade_refused_payment_identity");
        }

        auto* flags = field<U::FMapProperty>(owner.stock, STR("Flags"));
        auto* flags_value = flags ? flags->ContainerPtrToValuePtr<void>(owner.stock) : nullptr;
        auto* pack = layout.backpack->ContainerPtrToValuePtr<void>(owner.save);
        auto* host_pack = layout.backpack->ContainerPtrToValuePtr<void>(owner.stock);
        const auto old_flags = flags_value ? copy(flags, flags_value) : Value{};
        const auto old_pack = copy(layout.backpack, pack);
        const auto old_host = copy(layout.backpack, host_pack);
        const auto prepared = copy(layout.backpack, pack);
        if (!old_flags || !old_pack || !old_host || !prepared) return reject("sewer_trade_refused_snapshot");
        const auto stock_unchanged = [&] {
            return flags->Identical(flags_value, old_flags.get()) && layout.backpack->Identical(host_pack, old_host.get());
        };

        U::FScriptArrayHelper replacement(layout.backpack, prepared.get());
        std::ranges::sort(debits, std::greater{}, &Debit::index);
        for (const auto& entry : debits)
        {
            if (entry.index < 0 || entry.index >= replacement.Num()) return reject("sewer_trade_refused_payment_index");
            auto* row = replacement.GetRawPtr(entry.index);
            if (layout.identity(row) != entry.guid) return reject("sewer_trade_refused_payment_identity");
            if (!entry.remaining) replacement.RemoveValues(entry.index, 1);
            else
            {
                auto* defaults = entry.amount.item->GetClassDefaultObject().Get();
                if (!stock_stack(layout, row, entry.amount.count + entry.remaining, defaults) ||
                    !set_stack_quantity(layout, row, entry.remaining, defaults)) return reject("sewer_trade_refused_payment_stack");
            }
        }

        bool retired = true;
        const auto reward = sewer_reward(owner, layout, offer, retired);
        if (!retired || !stock_unchanged() || !layout.backpack->Identical(pack, old_pack.get()))
            return reject("mutation_unverified_sewer_trade_stock_reward");
        if (!reward) return reject("sewer_trade_refused_stock_reward");
        auto awarded = layout.identity(reward.get());
        if (awarded == Guid{}) return reject("sewer_trade_refused_reward_identity");
        bool duplicate{};
        for (auto* frame : {owner.save, owner.stock})
            if (!layout.rows(frame, [&](const void* row, int) { duplicate |= layout.identity(row) == awarded; }))
                return reject("sewer_trade_refused_inventory_bounds");
        if (duplicate) return reject("sewer_trade_refused_reward_identity");

        auto* award_defaults = offer.reward.item->GetClassDefaultObject().Get();
        const auto quantity = sewer_quantity(offer);
        bool merged{};
        if (bool_value(award_defaults, STR("Stacks"), award_defaults, true))
        {
            if (!stock_stack(layout, reward.get(), quantity, award_defaults) &&
                !set_stack_quantity(layout, reward.get(), quantity, award_defaults)) return reject("sewer_trade_refused_reward_stack");
            for (int index = 0; index < replacement.Num(); ++index)
            {
                auto* row = replacement.GetRawPtr(index);
                if (layout.klass(row) != offer.reward.item || !layout.matches_defaults(row, award_defaults)) continue;
                const int units = layout.units->GetPropertyValueInContainer(row);
                if (units <= 0 || units > std::numeric_limits<int>::max() - quantity || layout.identity(row) == Guid{} ||
                    !stock_stack(layout, row, units, award_defaults) ||
                    !set_stack_quantity(layout, row, units + quantity, award_defaults)) return reject("sewer_trade_refused_reward_stack");
                awarded = layout.identity(row);
                merged = true;
                break;
            }
        }
        if (!merged)
        {
            if (replacement.Num() >= capacity) return reject("sewer_trade_refused_backpack_capacity");
            auto* target = replacement.GetRawPtr(replacement.AddValues(1));
            auto* row = layout.backpack->GetInner();
            row->CopyCompleteValue(target, reward.get());
            if (!row->Identical(target, reward.get())) return reject("sewer_trade_refused_prepared_reward");
        }
        silverfish::shared_loot::EscrowOwner current;
        if (!silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, current) ||
            current.save != owner.save || !inside_sewer_trigger(current.pawn, world)) return reject("sewer_trade_refused_guest_scope_changed");

        layout.backpack->CopyCompleteValue(pack, prepared.get());
        if (!layout.backpack->Identical(pack, prepared.get()) || !stock_unchanged())
        {
            layout.backpack->CopyCompleteValue(pack, old_pack.get());
            return reject(layout.backpack->Identical(pack, old_pack.get()) && stock_unchanged()
                ? "sewer_trade_refused_rolled_back" : "mutation_unverified_sewer_trade_commit");
        }
        std::string paid;
        for (const auto& entry : debits)
            paid += std::format("{}|{}|{}|{}\n", guid_string(entry.guid), stock_class(entry.amount.item), entry.amount.count, entry.remaining);
        lua.set_bool(true);
        lua.set_string(fixture ? "seeded" : "exchanged");
        lua.set_string(paid);
        lua.set_string(guid_string(awarded));
        lua.set_string(stock_class(offer.reward.item));
        lua.set_integer(quantity);
        return 6;
    }

    template <bool crafting, bool preview = false>
    int trade(const Lua& lua)
    {
        const auto& profile = crafting ? bench : mama;
        Guid armor{};
        const auto result = [&](bool ok, std::string_view reason, const Guid& id = {}) {
            lua.set_bool(ok);
            lua.set_string(reason);
            if (!ok) return 2;
            const auto push_guid = [&](const Guid& value) {
                lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", value[0], value[1], value[2], value[3]));
            };
            if constexpr (preview)
            {
                if (id == Guid{}) lua.set_string("-");
                else push_guid(id);
            }
            else push_guid(id);
            if constexpr (crafting) push_guid(armor);
            return crafting ? 4 : 3;
        };
        if (!enabled() || lua.get_stack_size() != (preview ? 3 : 4) || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || (!preview && !lua.is_string(4))) return result(false, "borough_barter_refused_arguments");
        const auto pawn = lua.get_integer(), save = lua.get_integer(), stock = lua.get_integer();
        const auto payment = preview ? Guid{} : parse_guid(lua.get_string());
        if (pawn <= 0 || save <= 0 || stock <= 0 || stock == save || (!preview && payment == Guid{}))
            return result(false, "borough_barter_refused_identity");

        auto* world = find<U::UWorld>(crafting ? observatory_path : world_path);
        silverfish::shared_loot::EscrowOwner owner;
        if (!world || !silverfish::shared_loot::qualify_escrow_owner_in_world(world,
                static_cast<Address>(pawn), static_cast<Address>(save), static_cast<Address>(stock), owner))
            return result(false, "borough_barter_refused_exact_scope");

        U::UObject* post{};
        if constexpr (crafting)
        {
            post = crafting_post(owner.pawn, world);
            if (!post) return result(false, "borough_barter_refused_exact_workbench");
            if (!silverfish::native_guest_armor::worn_crafting_passive(pawn, save, armor) || !crafting_value())
                return result(false, "borough_barter_refused_detached_worn_crafting");
        }
        else
        {
            post = find<U::UObject>(post_path);
            auto* npc = find<U::UObject>(npc_path);
            auto* sphere = object(npc, STR("Sphere_Interact"));
            if (!exact(npc, STR("/Game/Classes/NPCs/NPC_Characters/NPC_Mama.NPC_Mama_C")) || !authority(npc, world) ||
                !bool_value(npc, STR("Alive"), npc, true) || !near(owner.pawn, npc) ||
                !exact(sphere, STR("/Script/Engine.SphereComponent")) || sphere->GetOuterPrivate() != npc)
                return result(false, "borough_barter_refused_exact_scope");
        }
        auto* box = object(post, STR("Box"));
        if (!exact(post, STR("/Game/Classes/Actors/TradePost_BP.TradePost_BP_C")) || !authority(post, world) ||
            !exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != post)
            return result(false, "borough_barter_refused_exact_scope");

        auto* ingredient = find<U::UClass>(profile.payment);
        auto* award = find<U::UClass>(profile.reward);
        auto* base = find<U::UClass>(base_path);
        if (!ingredient || !award || !base || !ingredient->IsChildOf(base) || !award->IsChildOf(base) ||
            !qualified_recipe(post, ingredient, award, profile))
            return result(false, "borough_barter_refused_authored_recipe");
        auto* defaults = ingredient->GetClassDefaultObject().Get();
        auto* input_units = field<U::FIntProperty>(defaults, STR("Ammo"));
        Inventory layout;
        if (!layout.bind(owner.save) || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !input_units || !bool_value(defaults, STR("Stacks"), defaults, false))
            return result(false, "borough_barter_refused_inventory_schema");

        const auto expected_units = crafting ? input_units->GetPropertyValueInContainer(defaults) : 0;
        const auto exact_ingredient = [&](const void* row) {
            return layout.klass(row) == ingredient && layout.units->GetPropertyValueInContainer(row) == expected_units &&
                layout.matches_defaults(row, defaults);
        };
        if constexpr (preview)
        {
            Guid board{};
            if (!layout.rows(owner.save, [&](const void* row, int index) {
                    if (index >= 0 && board == Guid{} && exact_ingredient(row)) board = layout.identity(row);
                })) return result(false, "borough_barter_refused_inventory_bounds");
            if (board == Guid{}) return result(true, "missing");
            int matches{};
            if (!layout.rows(owner.save, [&](const void* row, int) { matches += layout.identity(row) == board; }) || matches != 1)
                return result(false, "borough_barter_refused_exact_owned_computer_board");
            return result(true, "offer", board);
        }

        int source_index = -1, source_count = 0;
        bool existing_reward{};
        int merge_index = -1, merge_units{};
        Guid merge_id{};
        auto* award_defaults = award->GetClassDefaultObject().Get();
        if (!layout.rows(owner.save, [&](const void* row, int index) {
                if (layout.klass(row) == award)
                {
                    existing_reward = true;
                    if constexpr (crafting)
                    {
                        if (index >= 0 && merge_index < 0 && award_defaults && layout.matches_defaults(row, award_defaults))
                        {
                            merge_index = index;
                            merge_units = layout.units->GetPropertyValueInContainer(row);
                            merge_id = layout.identity(row);
                        }
                    }
                }
                if (layout.identity(row) != payment) return;
                ++source_count;
                if (exact_ingredient(row)) source_index = index;
            }) || source_count != 1 || source_index < 0)
            return result(false, crafting ? "borough_barter_refused_exact_owned_computer_board" :
                "borough_barter_refused_exact_owned_copper_wire");
        if constexpr (!crafting)
        {
            if (existing_reward) return result(false, "borough_barter_refused_existing_ap_ammo_stack");
        }

        auto* flags = field<U::FMapProperty>(owner.stock, STR("Flags"));
        auto* flags_value = flags ? flags->ContainerPtrToValuePtr<void>(owner.stock) : nullptr;
        auto* backpack = layout.backpack->ContainerPtrToValuePtr<void>(owner.save);
        const auto before = copy(layout.backpack, backpack);
        const auto prepared = copy(layout.backpack, backpack);
        const auto stock_flags = flags_value ? copy(flags, flags_value) : Value{};
        if (!before || !prepared || !stock_flags) return result(false, "borough_barter_refused_inventory_snapshot");

        U::FScriptArrayHelper replacement(layout.backpack, prepared.get());
        if (source_index >= replacement.Num()) return result(false, "borough_barter_refused_inventory_changed");
        if constexpr (crafting)
        {
            if ((existing_reward && merge_index < 0) || (merge_index >= 0 &&
                (merge_index >= replacement.Num() || merge_id == Guid{} || merge_id == payment ||
                 merge_units > std::numeric_limits<int>::max() / profile.reward_value - profile.quantity ||
                 !stack_totals(layout, replacement.GetRawPtr(merge_index), merge_units, profile))))
                return result(false, "borough_barter_refused_scrap_merge_shape");
        }

        const auto reward = make_stack(layout, owner.pawn, award, profile);
        if (!flags->Identical(flags_value, stock_flags.get()) || !layout.backpack->Identical(backpack, before.get()))
            return result(false, "mutation_unverified_borough_barter_stock_make_item");
        if (!reward) return result(false, crafting ? "borough_barter_refused_stock_scrap_shape" :
            "borough_barter_refused_stock_ammo_shape");
        const auto minted_id = layout.identity(reward.get());
        bool duplicate{};
        int merge_matches{};
        for (auto* frame : {owner.save, owner.stock})
            if (!layout.rows(frame, [&](const void* row, int) {
                    const auto id = layout.identity(row);
                    duplicate |= id == minted_id;
                    merge_matches += merge_index >= 0 && id == merge_id;
                }))
                return result(false, "borough_barter_refused_inventory_bounds");
        if (duplicate || (merge_index >= 0 && merge_matches != 1))
            return result(false, "borough_barter_refused_reward_guid_conflict");
        if constexpr (crafting)
        {
            silverfish::shared_loot::EscrowOwner current;
            Guid worn{};
            if (!silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, current) ||
                current.save != owner.save || !silverfish::native_guest_armor::worn_crafting_passive(pawn, save, worn) ||
                worn != armor) return result(false, "borough_barter_refused_crafting_scope_changed");
        }

        replacement.RemoveValues(source_index, 1);
        if (merge_index >= 0)
        {
            auto* target = replacement.GetRawPtr(merge_index - (source_index < merge_index ? 1 : 0));
            const auto total = merge_units + profile.quantity;
            layout.units->SetPropertyValueInContainer(target, total);
            layout.weight->SetPropertyValueInContainer(target, total * profile.reward_weight);
            layout.value->SetPropertyValueInContainer(target, total * profile.reward_value);
            if (layout.identity(target) != merge_id || !stack_totals(layout, target, total, profile))
                return result(false, "borough_barter_refused_prepared_scrap_merge_unverified");
        }
        else
        {
            auto* added = replacement.GetRawPtr(replacement.AddValues(1));
            auto* row = layout.backpack->GetInner();
            row->CopyCompleteValue(added, reward.get());
            if (!row->Identical(added, reward.get()))
                return result(false, "borough_barter_refused_prepared_inventory_unverified");
        }

        layout.backpack->CopyCompleteValue(backpack, prepared.get());
        if (layout.backpack->Identical(backpack, prepared.get()) && flags->Identical(flags_value, stock_flags.get()))
            return result(true, "exchanged", merge_index >= 0 ? merge_id : minted_id);
        layout.backpack->CopyCompleteValue(backpack, before.get());
        return result(false, layout.backpack->Identical(backpack, before.get()) && flags->Identical(flags_value, stock_flags.get())
            ? "borough_barter_refused_exchange_rolled_back" : "mutation_unverified_borough_barter_exchange");
    }
    constexpr auto barter_record_path = STR("/Game/Classes/Libraries/DataAssets/Barter/BarterSaveStruct.BarterSaveStruct");

    struct NpcBarter
    {
        silverfish::shared_loot::EscrowOwner owner;
        U::UObject* instance{};
        U::UObject* npc{};
        U::UObject* actor{};
        U::UObject* data{};
        U::FArrayProperty* inventory{};
        U::FArrayProperty* saved{};
        U::FArrayProperty* saved_inventory{};
        U::FStructProperty* saved_id{};
        U::FIntProperty* prior_day{};
        U::FIntProperty* day{};
        U::FDoubleProperty* prior_min_weapon{};
        U::FDoubleProperty* min_weapon{};
        U::UFunction* value_item{};
        U::FStructProperty* value_row{};
        U::FDoubleProperty* quoted{};
        U::UFunction* save_inventory{};
        Inventory layout;
        silverfish::stock_wallet::Wallet wallet;
        int money() const { return wallet.amount(instance); }

        Guid trader_id{};

        bool bind(Address pawn, Address save, Address stock, Address trader, const Guid& expected)
        {
            if (!pawn || !save || !stock || save == stock || !trader || expected == Guid{}) return false;
            auto* cls = silverfish::npc_barter::type(silverfish::npc_barter::trader_class);
            if (!cls) return false;
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (reinterpret_cast<Address>(candidate) != trader) return RC::LoopAction::Continue;
                if (silverfish::npc_barter::live(candidate) && candidate->GetClassPrivate() == cls) actor = candidate;
                return RC::LoopAction::Break;
            });
            if (!actor) return false;
            auto* world = static_cast<U::AActor*>(actor)->GetWorld();
            npc = silverfish::npc_barter::linked_npc(actor, world);
            if (!npc || !silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, owner)) return false;
            const auto active_authority = [](U::UObject* object) {
                auto* role = field<U::FByteProperty>(object, STR("Role"));
                return role && role->GetPropertyValueInContainer(object) == 3 &&
                    bool_value(object, STR("bActorIsBeingDestroyed"), object, false);
            };
            instance = object(world, STR("OwningGameInstance"));
            data = object(actor, STR("MerchantData"));
            if (!active_authority(npc) || !active_authority(actor) || !near(owner.pawn, actor) || !near(owner.pawn, npc) ||
                !bool_value(owner.pawn, STR("Dead"), owner.pawn, false) || !bool_value(actor, STR("Save"), actor, true) ||
                object(actor, STR("As Game Instance FP")) != instance || object(instance, STR("SaveSlot_Ref")) != owner.stock ||
                !wallet.bind(instance) || !layout.bind(owner.save)) return false;
            trader_id = expected;
            inventory = field<U::FArrayProperty>(actor, STR("Inventory"));
            saved = field<U::FArrayProperty>(owner.stock, STR("BarterInventories"));
            auto* record = find<U::UScriptStruct>(barter_record_path);
            auto* entry = saved ? U::CastField<U::FStructProperty>(saved->GetInner()) : nullptr;
            auto* id = field<U::FStructProperty>(actor, STR("TraderID"));
            saved_id = field<U::FStructProperty>(record, STR("TraderID_4_F301FE89472A9E41F27630A20A7CBFF4"));
            day = field<U::FIntProperty>(actor, STR("DayGenerated"));
            prior_day = field<U::FIntProperty>(record, STR("Day_6_C06910BA424639E2002A1ABDB6BE5272"));
            min_weapon = field<U::FDoubleProperty>(actor, STR("MinWeaponValue"));
            prior_min_weapon = field<U::FDoubleProperty>(record, STR("MinWeaponValue_13_BC19B130432FEB493DEB13BECD0875E3"));
            saved_inventory = field<U::FArrayProperty>(record, STR("Inventory_10_7102DB204B6D7CBB59DC659331FF259D"));
            value_item = find<U::UFunction>(STR("/Game/Classes/Actors/Barter_BP.Barter_BP_C:ValueItem"));
            value_row = parameter<U::FStructProperty>(value_item, STR("Item"), 0, false);
            quoted = parameter<U::FDoubleProperty>(value_item, STR("TraderValue"), 0x50, true);
            save_inventory = find<U::UFunction>(STR("/Game/Classes/Actors/Barter_BP.Barter_BP_C:SaveInventory"));
            if (!inventory || !layout.row_is(inventory->GetInner()) || !saved_inventory ||
                !layout.row_is(saved_inventory->GetInner()) || !entry || !record || entry->GetStruct().Get() != record ||
                !id || !saved_id || id->GetStruct().Get() != layout.guid->GetStruct().Get() ||
                saved_id->GetStruct().Get() != id->GetStruct().Get() || identity(id, actor) != trader_id ||
                !day || !prior_day || !min_weapon || !prior_min_weapon || !layout.row_is(value_row) || !quoted ||
                value_item->GetOuterPrivate() != actor->GetClassPrivate() || value_item->GetNumParms() != 2 ||
                value_item->GetParmsSize() != 0x58 || !save_inventory ||
                save_inventory->GetOuterPrivate() != actor->GetClassPrivate() || save_inventory->GetNumParms() != 0 ||
                save_inventory->GetParmsSize() != 0) return false;
            return money() >= 0;
        }

        Guid identity(U::FStructProperty* field, const void* row) const
        {
            const auto* value = field->ContainerPtrToValuePtr<void>(row);
            Guid result{};
            for (std::size_t i = 0; i < result.size(); ++i)
                result[i] = static_cast<std::uint32_t>(layout.words[i]->GetPropertyValueInContainer(value));
            return result;
        }

        double quote(const void* row) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x58> arguments{};
            value_row->InitializeValue_InContainer(arguments.data());
            value_row->CopyCompleteValue(value_row->ContainerPtrToValuePtr<void>(arguments.data()), row);
            actor->ProcessEvent(value_item, arguments.data());
            const auto result = quoted->GetPropertyValueInContainer(arguments.data());
            value_row->DestroyValue_InContainer(arguments.data());
            return result;
        }

        bool saved_matches() const
        {
            U::FScriptArrayHelper rows(saved, saved->ContainerPtrToValuePtr<void>(owner.stock));
            if (rows.Num() < 1 || rows.Num() > capacity) return false;
            int seen{};
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto* row = rows.GetRawPtr(index);
                if (identity(saved_id, row) != trader_id) continue;
                if (prior_day->GetPropertyValueInContainer(row) != day->GetPropertyValueInContainer(actor) ||
                    prior_min_weapon->GetPropertyValueInContainer(row) != min_weapon->GetPropertyValueInContainer(actor) ||
                    !saved_inventory->Identical(saved_inventory->ContainerPtrToValuePtr<void>(row),
                        inventory->ContainerPtrToValuePtr<void>(actor))) return false;
                ++seen;
            }
            return seen == 1;
        }
    };

    struct BarterSelection
    {
        Guid id;
        int units;
    };

    bool selections(std::string_view encoded, std::vector<BarterSelection>& rows)
    {
        if (encoded == "-") return true;
        while (!encoded.empty())
        {
            const auto boundary = encoded.find(',');
            const auto piece = encoded.substr(0, boundary);
            const auto id = parse_guid(piece.substr(0, 32));
            if (rows.size() >= 12 || id == Guid{} || piece.size() < 34 || piece[32] != ':' ||
                std::ranges::any_of(rows, [&id](const auto& prior) { return prior.id == id; })) return false;
            int units{};
            const auto parsed = std::from_chars(piece.data() + 33, piece.data() + piece.size(), units);
            if (parsed.ec != std::errc{} || parsed.ptr != piece.data() + piece.size() || units < 1 || units > 1000000)
                return false;
            rows.push_back({id, units});
            if (boundary == encoded.npos) return true;
            encoded.remove_prefix(boundary + 1);
            if (encoded.empty()) return false;
        }
        return true;
    }

    int barter_units(const Inventory& layout, const void* row)
    {
        auto* base = find<U::UClass>(base_path);
        auto* type = U::Cast<U::UClass>(layout.klass(row));
        auto* defaults = type && base && type->IsChildOf(base) ? type->GetClassDefaultObject().Get() : nullptr;
        auto* stack = field<U::FBoolProperty>(defaults, STR("Stacks"));
        if (!stack) return 0;
        return stack->GetPropertyValueInContainer(defaults) ? layout.units->GetPropertyValueInContainer(row) : 1;
    }

    bool keeps_weapon(const NpcBarter& barter, U::FScriptArrayHelper& inventory)
    {
        const auto minimum = barter.min_weapon->GetPropertyValueInContainer(barter.actor);
        if (!std::isfinite(minimum)) return false;
        if (minimum <= 0) return true;
        auto* weapon = find<U::UClass>(STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"));
        for (int index = 0; weapon && index < inventory.Num(); ++index)
        {
            const auto* row = inventory.GetRawPtr(index);
            auto* type = U::Cast<U::UClass>(barter.layout.klass(row));
            auto* defaults = type && type->IsChildOf(weapon) ? type->GetClassDefaultObject().Get() : nullptr;
            auto* category = field<U::FByteProperty>(defaults, STR("ItemType"));
            if (!category) continue;
            const auto kind = category->GetPropertyValueInContainer(defaults);
            if ((kind == 3 || kind == 4) && barter.quote(row) >= minimum) return true;
        }
        return false;
    }

    bool transfer_selection(const NpcBarter& barter, U::FScriptArrayHelper& source, U::FScriptArrayHelper& destination,
                            const std::vector<BarterSelection>& selections, bool outbound, double& total, std::string& receipt)
    {
        const auto& inventory = barter.layout;
        auto* row = U::CastField<U::FStructProperty>(inventory.backpack->GetInner());
        auto* weapon = find<U::UClass>(STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"));
        if (!weapon || !row) return false;
        const auto contains = [&](U::FScriptArrayHelper& rows, const Guid& id) {
            for (int index = 0; index < rows.Num(); ++index)
                if (inventory.identity(rows.GetRawPtr(index)) == id) return true;
            return false;
        };
        const auto new_identity = [&](const Guid& id, bool check_guest) {
            bool duplicate = id == Guid{} || contains(destination, id);
            const auto compare = [&](const void* entry, int) { duplicate |= inventory.identity(entry) == id; };
            return inventory.rows(barter.owner.stock, compare)
                && (!check_guest || inventory.rows(barter.owner.save, compare)) && !duplicate;
        };
        for (const auto& choice : selections)
        {
            int selected = -1;
            for (int index = 0; index < source.Num(); ++index)
            {
                if (inventory.identity(source.GetRawPtr(index)) != choice.id) continue;
                if (selected != -1) return false;
                selected = index;
            }
            if (selected < 0 || destination.Num() >= capacity) return false;
            auto* source_row = source.GetRawPtr(selected);
            const void* value = source_row;
            auto* item = U::Cast<U::UClass>(inventory.klass(value));
            const auto available = barter_units(inventory, value);
            if (!item || item->IsUnreachable() || inventory.never->GetPropertyValueInContainer(value) ||
                available < choice.units || (outbound && item->IsChildOf(weapon)) ||
                !new_identity(choice.id, !outbound)) return false;
            const auto partial = choice.units < available;
            Value portion(nullptr, {row}), remainder(nullptr, {row});
            auto transferred = choice.id;
            if (partial)
            {
                portion.reset(row->AllocateAndInitializeValue());
                remainder.reset(row->AllocateAndInitializeValue());
                if (!portion || !remainder || !silverfish::native_guest_item::split_stock_stack(
                    barter.owner.pawn, row, value, choice.units, portion.get(), remainder.get())) return false;
                transferred = inventory.identity(portion.get());
                if (inventory.klass(portion.get()) != item || inventory.klass(remainder.get()) != item ||
                    inventory.identity(remainder.get()) != choice.id || contains(source, transferred) ||
                    !new_identity(transferred, true) || barter_units(inventory, portion.get()) != choice.units ||
                    barter_units(inventory, remainder.get()) != available - choice.units) return false;
                value = portion.get();
            }
            const auto price = barter.quote(value);
            if (!std::isfinite(price) || price <= 0 || price > 1000000) return false;
            total += price;
            const auto type = stock_class(item);
            if (type.empty()) return false;
            receipt += std::format("{}|{}|", outbound ? "give" : "receive", guid_string(choice.id));
            if (partial) receipt += guid_string(transferred) + "|";
            receipt += std::format("{}|{}\n", type, choice.units);
            auto* target = destination.GetRawPtr(destination.AddValue());
            row->CopyCompleteValue(target, value);
            if (!row->Identical(target, value)) return false;
            if (partial)
            {
                row->CopyCompleteValue(source_row, remainder.get());
                if (!row->Identical(source_row, remainder.get())) return false;
            }
            else source.RemoveValues(selected);
        }
        return true;
    }


    bool npc_barter_passive(const NpcBarter& barter, const U::TCHAR* name, int& count, int& size)
    {
        auto* save = barter.owner.save;
        std::int32_t bonus{};
        if (!silverfish::native_enemy_loot::count_save_passive(save, U::FName{name}, count) ||
            (count > 0 && !silverfish::native_guest_sickness::read_passive_amplification(save, bonus))) return false;
        size = count + bonus;
        return true;
    }

    struct NpcBarterReputation
    {
        U::FName flag{};
        U::UFunction* apply{};
        int before{}, after{};

        bool prepare(const NpcBarter& barter, std::uint8_t faction_id, double gain)
        {
            if (!std::isfinite(gain) || gain > std::numeric_limits<int>::max()) return false;
            const auto reward = static_cast<int>(std::floor(std::max(0.0, gain)));
            if (!reward) return true;
            auto* library = find<U::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"));
            auto* compose = find<U::UFunction>(
                STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.FL_Characters_C:Compose_FactionRep_FlagName"));
            if (!library || !compose || !library->HasAnyFlags(U::RF_ClassDefaultObject) ||
                compose->GetOuterPrivate() != library->GetClassPrivate() || compose->GetNumParms() != 3 ||
                compose->GetParmsSize() != 24 || !parameter<U::FByteProperty>(compose, STR("Faction"), 0, false) ||
                !parameter<U::FObjectProperty>(compose, STR("__WorldContext"), 8, false) ||
                !parameter<U::FNameProperty>(compose, STR("Name"), 16, true)) return false;
            struct Key { std::uint8_t faction; U::UObject* context; U::FName name; } key{faction_id, barter.instance, {}};
            static_assert(sizeof(Key) == 24 && offsetof(Key, name) == 16);
            library->ProcessEvent(compose, &key);
            if (key.name.IsNone() || !key.name.ToString().starts_with(STR("Rep_"))) return false;
            apply = find<U::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Add_PointTo_SaveFlag"));
            if (!apply || apply->GetOuterPrivate() != barter.instance->GetClassPrivate() || apply->GetNumParms() != 4 ||
                apply->GetParmsSize() < 14 || apply->GetParmsSize() > 16 ||
                !parameter<U::FNameProperty>(apply, STR("KeyID"), 0, false) ||
                !parameter<U::FIntProperty>(apply, STR("Value"), 8, false) ||
                !parameter<U::FBoolProperty>(apply, STR("Overwrite"), 12, false) ||
                !parameter<U::FBoolProperty>(apply, STR("Force_Decrease"), 13, false)) return false;
            flag = key.name;
            const auto [prior, exists] = barter.wallet.flag_value(barter.instance, flag.ToString().c_str());
            int count{}, intensity{};
            if (exists && !npc_barter_passive(barter, STR("IntensePersonality"), count, intensity)) return false;
            const auto result = static_cast<std::int64_t>(prior) + static_cast<std::int64_t>(reward) * (1LL + intensity);
            if (result > std::numeric_limits<int>::max()) return false;
            before = prior;
            after = static_cast<int>(result);
            return true;
        }

        void commit(const NpcBarter& barter) const
        {
            if (!apply) return;
            struct Write { U::FName key; int result; bool overwrite; bool decrease; };
            static_assert(sizeof(Write) == 16 && offsetof(Write, overwrite) == 12);
            Write arguments{flag, after, true, false};
            barter.instance->ProcessEvent(apply, &arguments);
        }

        bool verified(const NpcBarter& barter) const
        {
            if (!apply) return true;
            const auto [actual, found] = barter.wallet.flag_value(barter.instance, flag.ToString().c_str());
            return found && actual == after;
        }

        std::string receipt() const
        {
            return apply ? std::format("reputation|{}|{}|{}\n", RC::to_utf8_string(flag.ToString()), before, after) : std::string{};
        }
    };

    int npc_barter_execute(const Lua& lua)
    {
        const auto reply = [&](bool okay, std::string_view status, const std::string& rows = {}, int before = 0, int after = 0) {
            lua.set_bool(okay);
            lua.set_string(status);
            lua.set_string(rows);
            lua.set_integer(before);
            lua.set_integer(after);
            return 5;
        };
        if (!enabled() || lua.get_stack_size() != 8 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) ||
            !lua.is_integer(4) || !lua.is_string(5) || !lua.is_string(6) || !lua.is_string(7) || !lua.is_integer(8))
            return reply(false, "barter_refused_arguments");
        const auto pawn = lua.get_integer();
        const auto save = lua.get_integer();
        const auto stock = lua.get_integer();
        const auto trader = lua.get_integer();
        const auto identity = parse_guid(lua.get_string());
        const auto player_selection = lua.get_string();
        const auto trader_selection = lua.get_string();
        const auto cash = lua.get_integer();
        NpcBarter barter;
        if (pawn <= 0 || save <= 0 || stock <= 0 || trader <= 0 || !barter.bind(pawn, save, stock, trader, identity))
            return reply(false, "barter_refused_exact_owner_and_trader");
        const auto funds = barter.money();
        if (cash < 0 || cash > funds || (cash && !bool_value(barter.actor, STR("AcceptCash"), barter.actor, true)))
            return reply(false, "barter_refused_wallet");
        int psychic_amount{};
        if (cash > 1000)
        {
            int count{}, intensity{};
            if (!npc_barter_passive(barter, STR("Skinflint"), count, intensity))
                return reply(false, "barter_refused_skinflint_cash_effect");
            if (count)
            {
                auto* screen = find<U::UClass>(STR("/Game/Classes/GUI/New/BarterScreen.BarterScreen_C"));
                auto* defaults = screen ? screen->GetClassDefaultObject().Get() : nullptr;
                auto* maximum = field<U::FIntProperty>(defaults, STR("Max Skin Flint Psy Dmg"));
                const auto cap = maximum ? maximum->GetPropertyValueInContainer(defaults) : 0;
                if (cap < 1 || cap > 100) return reply(false, "barter_refused_skinflint_cash_effect");
                psychic_amount = static_cast<int>(std::clamp(static_cast<std::int64_t>(cash / 1000) * intensity,
                    std::int64_t{1}, static_cast<std::int64_t>(cap)));
                if (!silverfish::native_guest_sickness::barter_psy(
                    barter.owner.pawn, barter.owner.save, barter.owner.stock, psychic_amount, false))
                    return reply(false, "barter_refused_skinflint_cash_effect");
            }
        }
        std::vector<BarterSelection> give, receive;
        if (!selections(player_selection, give) || !selections(trader_selection, receive) ||
            (give.empty() && cash == 0) || (give.empty() && receive.empty()))
            return reply(false, "barter_refused_selection");
        auto* bag = barter.layout.backpack;
        void* stock_list = barter.inventory->ContainerPtrToValuePtr<void>(barter.actor);
        void* bag_list = bag->ContainerPtrToValuePtr<void>(barter.owner.save);
        void* records = barter.saved->ContainerPtrToValuePtr<void>(barter.owner.stock);
        auto* flags = field<U::FMapProperty>(barter.owner.stock, STR("Flags"));
        if (!flags) return reply(false, "barter_refused_wallet_schema");
        void* flag_values = flags->ContainerPtrToValuePtr<void>(barter.owner.stock);
        U::FScriptArrayHelper offer(barter.inventory, stock_list), carry(bag, bag_list);
        if (offer.Num() < 1 || offer.Num() > 128 || carry.Num() < 0 || carry.Num() > capacity)
            return reply(false, "barter_refused_stock_bounds");
        auto old_bag = copy(bag, bag_list), new_bag = copy(bag, bag_list);
        auto old_stock = copy(barter.inventory, stock_list), new_stock = copy(barter.inventory, stock_list);
        auto old_records = copy(barter.saved, records), old_flags = copy(flags, flag_values);
        if (!old_bag || !new_bag || !old_stock || !new_stock || !old_records || !old_flags)
            return reply(false, "barter_refused_before_image");
        U::FScriptArrayHelper stock_result(barter.inventory, new_stock.get()), bag_result(bag, new_bag.get());
        double player_value{}, trade_value{};
        std::string receipt;
        if (!transfer_selection(barter, stock_result, bag_result, receive, false, trade_value, receipt) ||
            !transfer_selection(barter, bag_result, stock_result, give, true, player_value, receipt) ||
            !keeps_weapon(barter, stock_result)) return reply(false, "barter_refused_stock_selection");
        auto* cash_factor = field<U::FDoubleProperty>(barter.data, STR("CashValueMod"));
        auto* targets = field<U::FStructProperty>(barter.data, STR("TargetBalanceMin/Max"));
        auto* faction = field<U::FByteProperty>(barter.npc, STR("Faction"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector2D"));
        auto* best = field<U::FDoubleProperty>(vector, STR("X"));
        auto* neutral = field<U::FDoubleProperty>(vector, STR("Y"));
        if (!cash_factor || !targets || targets->GetStruct().Get() != vector || !best || !neutral || !faction)
            return reply(false, "barter_refused_quote_schema");
        const auto* target = targets->ContainerPtrToValuePtr<void>(barter.data);
        const auto ceiling = neutral->GetPropertyValueInContainer(target);
        auto base_minimum = ceiling;
        const auto faction_id = faction->GetPropertyValueInContainer(barter.npc);
        if (faction_id > 1)
        {
            auto* reputation = find<U::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Find_PlayerReputation"));
            auto* faction_param = parameter<U::FByteProperty>(reputation, STR("Faction"), 0, false);
            auto* points = parameter<U::FIntProperty>(reputation, STR("Points"), 4, true);
            if (!faction_param || !points || reputation->GetOuterPrivate() != barter.instance->GetClassPrivate() ||
                reputation->GetNumParms() != 4 || reputation->GetParmsSize() != 8)
                return reply(false, "barter_refused_reputation_quote");
            alignas(int) std::array<std::byte, 8> arguments{};
            faction_param->SetPropertyValueInContainer(arguments.data(), faction_id);
            barter.instance->ProcessEvent(reputation, arguments.data());
            const auto fraction = std::clamp((1000.0 - points->GetPropertyValueInContainer(arguments.data())) / 1100.0, 0.0, 1.0);
            const auto floor = best->GetPropertyValueInContainer(target);
            base_minimum = floor + (ceiling - floor) * fraction;
        }
        int haggler{}, amplitude{};
        if (!npc_barter_passive(barter, STR("Haggler"), haggler, amplitude))
            return reply(false, "barter_refused_passive_quote");
        const auto minimum = haggler ? base_minimum * (0.7 * amplitude) : base_minimum;
        const auto cash_value = cash * cash_factor->GetPropertyValueInContainer(barter.data);
        player_value += cash_value;
        const auto total = player_value + trade_value;
        const auto balance = player_value / total;
        if (!std::isfinite(minimum) || minimum <= 0.0 || minimum > 0.95 || !std::isfinite(balance) ||
            balance < minimum || balance > 1.0) return reply(false, "barter_refused_native_balance");
        NpcBarterReputation reputation;
        const auto ideal = minimum + 0.05;
        if (balance > ideal)
        {
            auto* factor = field<U::FDoubleProperty>(barter.data, STR("BarterRepFactor"));
            if (!factor) return reply(false, "barter_refused_reputation_schema");
            const auto surplus = (balance - ideal) / (1.0 - ideal) * total;
            const auto gain = (surplus - cash_value) * factor->GetPropertyValueInContainer(barter.data);
            if (!reputation.prepare(barter, faction_id, gain)) return reply(false, "barter_refused_reputation_effect");
        }
        bag->CopyCompleteValue(bag_list, new_bag.get());
        barter.inventory->CopyCompleteValue(stock_list, new_stock.get());
        reputation.commit(barter);
        if (cash) barter.wallet.change(barter.instance, static_cast<int>(cash), true);
        barter.actor->ProcessEvent(barter.save_inventory, nullptr);
        if (bag->Identical(bag_list, new_bag.get()) && barter.inventory->Identical(stock_list, new_stock.get()) &&
            barter.money() == funds - cash && reputation.verified(barter) && barter.saved_matches() &&
            (!psychic_amount || silverfish::native_guest_sickness::barter_psy(
                barter.owner.pawn, barter.owner.save, barter.owner.stock, psychic_amount, true)))
            return reply(true, "exchanged", receipt + reputation.receipt(), funds, barter.money());
        bag->CopyCompleteValue(bag_list, old_bag.get());
        barter.inventory->CopyCompleteValue(stock_list, old_stock.get());
        barter.saved->CopyCompleteValue(records, old_records.get());
        flags->CopyCompleteValue(flag_values, old_flags.get());
        const bool restored = bag->Identical(bag_list, old_bag.get()) &&
            barter.inventory->Identical(stock_list, old_stock.get()) && barter.saved->Identical(records, old_records.get()) &&
            flags->Identical(flag_values, old_flags.get());
        return reply(false, restored ? "barter_refused_rolled_back" : "mutation_unverified_npc_barter_commit");
    }

}

namespace silverfish::borough_barter
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        lua.register_function("SilverfishTradeNativeBoroughMama", trade<false>);
        lua.register_function("SilverfishTradeNativeCraftingBench", trade<true>);
        lua.register_function("SilverfishPreviewNativeCraftingBench", trade<true, true>);
        lua.register_function("SilverfishSeedNativeCraftingScrap", seed_crafting_scrap);
        lua.register_function("SilverfishNativeSewerTrade", sewer_trade);
        lua.register_function("SilverfishNativeNpcBarterExchange", npc_barter_execute);
    }
}
