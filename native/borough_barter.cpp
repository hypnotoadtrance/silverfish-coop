#include "borough_barter.hpp"
#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <memory>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/Engine/UDataTable.hpp>
#include <Unreal/NameTypes.hpp>
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
    constexpr int receive_count = 25;
    constexpr int ammo_value = 8;
    constexpr double ammo_weight = 0.012;
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough");
    constexpr auto npc_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.NPC_Mama_C_1");
    constexpr auto post_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.TradePost_BP_C_1080");
    constexpr auto copper_path = STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Intr_AP_BP.Ammo_Intr_AP_BP_C");
    constexpr auto base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto inventory_struct_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");

    bool enabled()
    {
        const auto flag = [](const char* name) {
            std::array<char, 2> value{};
            std::size_t size{};
            return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
        };
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

    bool qualified_recipe(U::UObject* post, U::UClass* copper, U::UClass* ammo)
    {
        auto* row_schema = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Inventory_Struct.Trade_Inventory_Struct"));
        auto* offer_schema = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Trade_Struct.Trade_Struct"));
        auto* table_object = find<U::UObject>(STR("/Game/Classes/Libraries/DataTables/TradeInventories_DT.TradeInventories_DT"));
        auto* handle = field<U::FStructProperty>(post, STR("ShopInventory"));
        auto* inventory = field<U::FStructProperty>(post, STR("ShopInv"));
        auto* handle_type = find<U::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table_field = field<U::FObjectProperty>(handle_type, STR("DataTable"));
        auto* name = field<U::FNameProperty>(handle_type, STR("RowName"));
        const U::FName row_name{STR("Trade_Mamma"), U::FNAME_Find};
        if (!row_schema || !offer_schema || !exact(table_object, STR("/Script/Engine.DataTable")) ||
            !handle || !handle_type || handle->GetStruct().Get() != handle_type || !table_field || !name ||
            !inventory || inventory->GetStruct().Get() != row_schema || row_name.IsNone() || row_name.ToString() != STR("Trade_Mamma") ||
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
        if (authored.Num() != 5 || live.Num() != 5) return false;
        const auto* recipe = authored.GetRawPtr(0);
        if (chance->GetPropertyValueInContainer(recipe) != 1.0 || give->GetPropertyValueInContainer(recipe) != 0 ||
            receive->GetPropertyValueInContainer(recipe) != 0 ||
            !bool_value(offer_schema, STR("RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2"), recipe, false) ||
            !bool_value(offer_schema, STR("Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174"), recipe, false) ||
            !one_entry(offer_schema, recipe, STR("Items_Give_20_B4929C76420B737D08B6E7A56384E8EC"), copper, 1) ||
            !one_entry(offer_schema, recipe, STR("Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD"), ammo, receive_count))
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

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool output)
    {
        auto* prop = field<T>(function, name);
        return prop && prop->GetOffset_Internal() == offset && prop->HasAnyPropertyFlags(U::CPF_Parm) &&
            prop->HasAnyPropertyFlags(U::CPF_OutParm) == output ? prop : nullptr;
    }

    Value make_ammo(const Inventory& layout, U::UObject* pawn, U::UClass* ammo)
    {
        auto* function = find<U::UFunction>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeItemStack"));
        auto* library = find<U::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
        auto* klass = parameter<U::FClassProperty>(function, STR("Item_Class"), 0, false);
        auto* count = parameter<U::FIntProperty>(function, STR("Count"), 8, false);
        auto* context = parameter<U::FObjectProperty>(function, STR("__WorldContext"), 16, false);
        auto* out = parameter<U::FStructProperty>(function, STR("Item"), 24, true);
        constexpr std::size_t max_size = 256;
        auto* defaults = ammo->GetClassDefaultObject().Get();
        auto* unit_weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        auto* unit_value = field<U::FIntProperty>(defaults, STR("Value"));
        if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !klass || klass->GetMetaClass().Get() != layout.item->GetMetaClass().Get() || !count || !context ||
            !layout.row_is(out) || out->GetOffset_Internal() + out->GetSize() > function->GetParmsSize() ||
            function->GetParmsSize() > max_size || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), defaults, true) ||
            !unit_weight || unit_weight->GetPropertyValueInContainer(defaults) != ammo_weight ||
            !unit_value || unit_value->GetPropertyValueInContainer(defaults) != ammo_value) return {};

        alignas(std::max_align_t) std::array<std::byte, max_size> bytes{};
        out->InitializeValue_InContainer(bytes.data());
        const auto cleanup = [out](std::byte* container) { out->DestroyValue_InContainer(container); };
        std::unique_ptr<std::byte, decltype(cleanup)> guard(bytes.data(), cleanup);
        klass->SetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(bytes.data()), ammo);
        count->SetPropertyValueInContainer(bytes.data(), receive_count);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(bytes.data()), pawn);
        library->ProcessEvent(function, bytes.data());
        const auto* result = out->ContainerPtrToValuePtr<void>(bytes.data());
        if (layout.identity(result) == Guid{} || layout.klass(result) != ammo || !layout.matches_defaults(result, defaults) ||
            layout.units->GetPropertyValueInContainer(result) != receive_count ||
            !(std::abs(layout.weight->GetPropertyValueInContainer(result) - ammo_weight * receive_count) < 1e-9) ||
            layout.value->GetPropertyValueInContainer(result) != receive_count * ammo_value) return {};
        return copy(out, result);
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

    int trade(const Lua& lua)
    {
        const auto result = [&](bool ok, std::string_view reason, const Guid& id = {}) {
            lua.set_bool(ok);
            lua.set_string(reason);
            if (!ok) return 2;
            lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", id[0], id[1], id[2], id[3]));
            return 3;
        };
        if (!enabled() || lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4)) return result(false, "borough_barter_refused_arguments");
        const auto pawn = lua.get_integer(), save = lua.get_integer(), stock = lua.get_integer();
        const auto payment = parse_guid(lua.get_string());
        if (pawn <= 0 || save <= 0 || stock <= 0 || stock == save || payment == Guid{})
            return result(false, "borough_barter_refused_identity");

        auto* world = find<U::UWorld>(world_path);
        silverfish::shared_loot::EscrowOwner owner;
        auto* post = find<U::UObject>(post_path);
        auto* npc = find<U::UObject>(npc_path);
        auto* box = object(post, STR("Box"));
        auto* sphere = object(npc, STR("Sphere_Interact"));
        if (!world || !silverfish::shared_loot::qualify_escrow_owner_in_world(world, static_cast<Address>(pawn),
                static_cast<Address>(save), static_cast<Address>(stock), owner) ||
            !exact(npc, STR("/Game/Classes/NPCs/NPC_Characters/NPC_Mama.NPC_Mama_C")) || !authority(npc, world) ||
            !bool_value(npc, STR("Alive"), npc, true) || !near(owner.pawn, npc) ||
            !exact(sphere, STR("/Script/Engine.SphereComponent")) || sphere->GetOuterPrivate() != npc ||
            !exact(post, STR("/Game/Classes/Actors/TradePost_BP.TradePost_BP_C")) || !authority(post, world) ||
            !exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != post)
            return result(false, "borough_barter_refused_exact_scope");

        auto* copper = find<U::UClass>(copper_path);
        auto* ammo = find<U::UClass>(ammo_path);
        auto* base = find<U::UClass>(base_path);
        if (!copper || !ammo || !base || !copper->IsChildOf(base) || !ammo->IsChildOf(base) || !qualified_recipe(post, copper, ammo))
            return result(false, "borough_barter_refused_authored_recipe");
        auto* copper_defaults = copper->GetClassDefaultObject().Get();
        Inventory layout;
        if (!layout.bind(owner.save) || !copper_defaults || !copper_defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(copper_defaults, STR("Stacks"), copper_defaults, false))
            return result(false, "borough_barter_refused_inventory_schema");

        int source_index = -1, source_count = 0;
        bool other_ammo{};
        if (!layout.rows(owner.save, [&](const void* row, int index) {
                if (layout.klass(row) == ammo) other_ammo = true;
                if (layout.identity(row) != payment) return;
                ++source_count;
                if (layout.klass(row) == copper && layout.units->GetPropertyValueInContainer(row) == 0 &&
                    layout.matches_defaults(row, copper_defaults)) source_index = index;
            }) || source_count != 1 || source_index < 0)
            return result(false, "borough_barter_refused_exact_owned_copper_wire");
        if (other_ammo) return result(false, "borough_barter_refused_existing_ap_ammo_stack");

        auto* flags = field<U::FMapProperty>(owner.stock, STR("Flags"));
        auto* flags_value = flags ? flags->ContainerPtrToValuePtr<void>(owner.stock) : nullptr;
        auto* backpack = layout.backpack->ContainerPtrToValuePtr<void>(owner.save);
        const auto before = copy(layout.backpack, backpack);
        const auto prepared = copy(layout.backpack, backpack);
        const auto stock_flags = flags_value ? copy(flags, flags_value) : Value{};
        if (!before || !prepared || !stock_flags) return result(false, "borough_barter_refused_inventory_snapshot");

        const auto reward = make_ammo(layout, owner.pawn, ammo);
        if (!flags->Identical(flags_value, stock_flags.get()) || !layout.backpack->Identical(backpack, before.get()))
            return result(false, "mutation_unverified_borough_barter_stock_make_item");
        if (!reward) return result(false, "borough_barter_refused_stock_ammo_shape");
        const auto reward_id = layout.identity(reward.get());
        bool duplicate{};
        for (auto* frame : {owner.save, owner.stock})
            if (!layout.rows(frame, [&](const void* row, int) { duplicate |= layout.identity(row) == reward_id; }))
                return result(false, "borough_barter_refused_inventory_bounds");
        if (duplicate) return result(false, "borough_barter_refused_reward_guid_conflict");

        U::FScriptArrayHelper replacement(layout.backpack, prepared.get());
        if (source_index >= replacement.Num()) return result(false, "borough_barter_refused_inventory_changed");
        replacement.RemoveValues(source_index, 1);
        const auto new_index = replacement.AddValues(1);
        auto* row = layout.backpack->GetInner();
        row->CopyCompleteValue(replacement.GetRawPtr(new_index), reward.get());
        if (!row->Identical(replacement.GetRawPtr(new_index), reward.get()))
            return result(false, "borough_barter_refused_prepared_inventory_unverified");

        layout.backpack->CopyCompleteValue(backpack, prepared.get());
        if (layout.backpack->Identical(backpack, prepared.get()) && flags->Identical(flags_value, stock_flags.get()))
            return result(true, "exchanged", reward_id);
        layout.backpack->CopyCompleteValue(backpack, before.get());
        return result(false, layout.backpack->Identical(backpack, before.get()) && flags->Identical(flags_value, stock_flags.get())
            ? "borough_barter_refused_exchange_rolled_back" : "mutation_unverified_borough_barter_exchange");
    }
}

namespace silverfish::borough_barter
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        lua.register_function("SilverfishTradeNativeBoroughMama", trade);
    }
}
