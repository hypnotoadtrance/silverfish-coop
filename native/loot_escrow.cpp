#include "loot_escrow.hpp"
#include "stock_enemy_drops.hpp"
#include "enemy_loot.hpp"
#include "shared_loot.hpp"
#include "stock_cash.hpp"
#include "stock_wallet.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <limits>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;
    using Owner = silverfish::shared_loot::EscrowOwner;
    using silverfish::shared_loot::ContainerActors;
    using silverfish::shared_loot::ContainerAddresses;
    using silverfish::shared_loot::max_container_slots;

    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto item_base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto mushroom_patch_path = STR("/Game/Assets/PCG_Brushes/MushroomPatch_BP.MushroomPatch_BP_C");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto stack_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.FL_Items_C:StackItem_Stats");
    constexpr auto stack_library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.Default__FL_Items_C");
    constexpr auto item_struct_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_struct_path = STR("/Script/CoreUObject.Guid");
    constexpr auto payload_struct_path = STR("/Script/StructUtils.InstancedStruct");
    constexpr auto spade_path = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C");
    constexpr auto sealed_package_path = STR("/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_inventory = 4096;
    constexpr int max_escrow = 2;
    constexpr int max_party = 5;
    constexpr std::size_t parameter_capacity = 256;
    std::array<Unreal::FWeakObjectPtr, 64> qualified_escrows{};
    std::size_t next_qualified_escrow{};

    template<typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* prop = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template<typename T>
    T* parameter(Unreal::UFunction* fn, const Unreal::TCHAR* name, int offset, bool output)
    {
        auto* prop = field<T>(fn, name);
        return prop && prop->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
               prop->HasAnyPropertyFlags(Unreal::CPF_OutParm) == output &&
               !prop->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) &&
               (!output || !prop->HasAnyPropertyFlags(Unreal::CPF_ConstParm)) &&
               prop->GetOffset_Internal() == offset && offset + prop->GetSize() <= fn->GetParmsSize() ? prop : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_LOOT") == 0 &&
               size == value.size() && value[0] == '1';
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    std::string print_guid(const Guid& guid)
    {
        return std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]);
    }

    Guid parse_guid(std::string_view text)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char value) {
                return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F');
            })) return {};
        Guid guid{};
        for (std::size_t index = 0; index < guid.size(); ++index)
        {
            const auto* start = text.data() + index * 8;
            const auto [end, error] = std::from_chars(start, start + 8, guid[index], 16);
            if (error != std::errc{} || end != start + 8) return {};
        }
        return guid;
    }

    bool read_addresses(const Lua& lua, std::span<Address> addresses, std::size_t required)
    {
        for (std::size_t index = 0; index < addresses.size(); ++index)
        {
            if (!lua.is_integer(1)) return false;
            const auto value = lua.get_integer();
            if (value < 0 || (!value && index < required)) return false;
            addresses[index] = static_cast<Address>(value);
        }
        return true;
    }

    struct DestroyValue
    {
        Unreal::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, DestroyValue>;

    Value copy_value(Unreal::FProperty* property, const void* source)
    {
        Value copy(property->AllocateAndInitializeValue(), {property});
        if (copy) property->CopyCompleteValue(copy.get(), source);
        return copy;
    }

    struct Inventory
    {
        Unreal::UClass* save_class = find<Unreal::UClass>(save_path);
        Unreal::UClass* item_base = find<Unreal::UClass>(item_base_path);
        Unreal::UScriptStruct* type = find<Unreal::UScriptStruct>(item_struct_path);
        Unreal::FArrayProperty* backpack = field<Unreal::FArrayProperty>(save_class, STR("PlayerBackpack"));
        Unreal::FArrayProperty* equipment = field<Unreal::FArrayProperty>(save_class, STR("PlayerEquipment"));
        Unreal::FStructProperty* armor = field<Unreal::FStructProperty>(save_class, STR("PlayerArmor"));
        Unreal::FStructProperty* helmet = field<Unreal::FStructProperty>(save_class, STR("PlayerHelmet"));
        Unreal::FStructProperty* row = backpack ? Unreal::CastField<Unreal::FStructProperty>(backpack->GetInner()) : nullptr;
        Unreal::FClassProperty* item_class = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        Unreal::FIntProperty* ammo = field<Unreal::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        Unreal::FDoubleProperty* weight = field<Unreal::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        Unreal::FIntProperty* value = field<Unreal::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        Unreal::FNameProperty* tag = field<Unreal::FNameProperty>(type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        Unreal::FStructProperty* payload = field<Unreal::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        Unreal::FBoolProperty* never_drop = field<Unreal::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        Unreal::FStructProperty* guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<Unreal::FIntProperty*, 4> words{};

        bool same_type(Unreal::FProperty* prop) const
        {
            auto* structure = Unreal::CastField<Unreal::FStructProperty>(prop);
            return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == type;
        }

        bool bind()
        {
            auto* guid_type = find<Unreal::UScriptStruct>(guid_struct_path);
            auto* instanced = find<Unreal::UScriptStruct>(payload_struct_path);
            if (!save_class || !item_base || !type || !backpack || !equipment || !same_type(row) ||
                !same_type(equipment->GetInner()) || !same_type(armor) || !same_type(helmet) ||
                !item_class || item_class->GetMetaClass().Get() != item_base || !ammo || !weight ||
                !value || !tag || !payload || !instanced || payload->GetStruct().Get() != instanced ||
                !never_drop || !guid || !guid_type || guid->GetStruct().Get() != guid_type) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                words[index] = field<Unreal::FIntProperty>(guid_type, guid_names[index]);
                if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * sizeof(std::uint32_t)))
                    return false;
            }
            return true;
        }

        Guid identity(const void* item) const
        {
            const auto* data = guid->ContainerPtrToValuePtr<void>(item);
            Guid result{};
            for (std::size_t index = 0; index < result.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return result;
        }

        Unreal::UObject* klass(const void* item) const
        {
            return item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(item));
        }

        bool identical(const void* first, const void* second) const
        {
            return row->Identical(first, second) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        }
    };

    struct StackTotals
    {
        int unit_value{};
        double unit_weight{};
        int preset_quantity{};

        bool safe(int quantity) const
        {
            return quantity > 0 && (unit_value == 0 || quantity <= (std::numeric_limits<int>::max)() / unit_value) &&
                std::isfinite(quantity * unit_weight);
        }

        bool matches(const Inventory& inventory, const void* row, int quantity) const
        {
            if (!safe(quantity)) return false;
            const auto value = inventory.value->GetPropertyValueInContainer(row);
            const auto weight = inventory.weight->GetPropertyValueInContainer(row);
            const auto near_weight = [&](double expected) { return std::abs(weight - expected) <= 1e-9; };
            return (value == quantity * unit_value && near_weight(quantity * unit_weight)) ||
                (quantity == preset_quantity && value == unit_value && near_weight(unit_weight));
        }
    };

    struct StackPickup
    {
        Value row{};
        Guid guid{};
        int index{};
        int before{};
        int units{};
    };

    StackPickup propose_stack(const Inventory& inventory, Unreal::UClass* type, const void* incoming,
                              Unreal::FScriptArrayHelper& destination)
    {
        auto* defaults = type ? type->GetClassDefaultObject().Get() : nullptr;
        auto* stacks = field<Unreal::FBoolProperty>(defaults, STR("Stacks"));
        auto* value = field<Unreal::FIntProperty>(defaults, STR("Value"));
        auto* weight = field<Unreal::FDoubleProperty>(defaults, STR("Weight"));
        auto* preset = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
        if (!stacks || !value || !weight || !preset || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            defaults->GetClassPrivate() != type || !stacks->GetPropertyValueInContainer(defaults)) return {};
        const StackTotals totals{value->GetPropertyValueInContainer(defaults),
            weight->GetPropertyValueInContainer(defaults), preset->GetPropertyValueInContainer(defaults)};
        const auto units = inventory.ammo->GetPropertyValueInContainer(incoming);
        if (totals.unit_value < 0 || totals.unit_weight < 0 || !totals.matches(inventory, incoming, units)) return {};

        auto row = copy_value(inventory.row, incoming);
        if (!row) return {};
        for (int index = 0; index < destination.Num(); ++index)
        {
            const auto* current = destination.GetRawPtr(index);
            if (inventory.klass(current) != type) continue;
            const auto guid = inventory.identity(current);
            const auto before = inventory.ammo->GetPropertyValueInContainer(current);
            if (guid == Guid{} || !totals.matches(inventory, current, before) ||
                before > (std::numeric_limits<int>::max)() - units || !totals.safe(before + units)) continue;

            // Compare the full typed row after normalizing the fields that change when stock stacks.
            inventory.guid->CopyCompleteValue(inventory.guid->ContainerPtrToValuePtr<void>(row.get()),
                                              inventory.guid->ContainerPtrToValuePtr<void>(current));
            inventory.ammo->SetPropertyValueInContainer(row.get(), before);
            inventory.value->SetPropertyValueInContainer(row.get(), inventory.value->GetPropertyValueInContainer(current));
            inventory.weight->SetPropertyValueInContainer(row.get(), inventory.weight->GetPropertyValueInContainer(current));
            if (!inventory.identical(row.get(), current)) continue;

            const auto after = before + units;
            inventory.ammo->SetPropertyValueInContainer(row.get(), after);
            inventory.value->SetPropertyValueInContainer(row.get(), after * totals.unit_value);
            inventory.weight->SetPropertyValueInContainer(row.get(), after * totals.unit_weight);
            return {std::move(row), guid, index, before, units};
        }
        return {};
    }

    Unreal::UObject* isolated_save(Address address, const Inventory& inventory, std::string_view& failure)
    {
        auto* instance = find<Unreal::UClass>(instance_path);
        auto* pawn = find<Unreal::UClass>(pawn_path);
        auto* canonical = field<Unreal::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* assigned = field<Unreal::FObjectProperty>(pawn, STR("Save"));
        if (!canonical || !assigned || canonical->GetPropertyClass().Get() != inventory.save_class ||
            assigned->GetPropertyClass().Get() != inventory.save_class)
        {
            failure = "native_loot_save_attachment_schema_unavailable";
            return nullptr;
        }
        bool attached = false;
        Unreal::UObject* result = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (!live(object)) return RC::LoopAction::Continue;
            auto* type = object->GetClassPrivate();
            auto* link = type == instance ? canonical : type == pawn ? assigned : nullptr;
            if (link && reinterpret_cast<Address>(link->GetObjectPropertyValue(link->ContainerPtrToValuePtr<void>(object))) == address)
            {
                attached = true;
                return RC::LoopAction::Break;
            }
            if (type == inventory.save_class && reinterpret_cast<Address>(object) == address)
                result = object;
            return RC::LoopAction::Continue;
        });
        if (attached)
        {
            failure = "native_loot_escrow_save_attached_to_owner";
            return nullptr;
        }
        if (!result)
        {
            failure = "native_loot_live_stock_escrow_save_missing";
            return nullptr;
        }
        auto* outer = result->GetOuterPrivate();
        if (!outer || outer->GetPathName() != STR("/Engine/Transient"))
        {
            failure = "native_loot_escrow_save_outer_unqualified";
            return nullptr;
        }
        if (std::ranges::none_of(qualified_escrows, [result](const auto& reference) { return reference.Get() == result; }))
        {
            qualified_escrows[next_qualified_escrow] = Unreal::FWeakObjectPtr(result);
            next_qualified_escrow = (next_qualified_escrow + 1) % qualified_escrows.size();
        }
        return result;
    }

    bool qualified_escrow(const Inventory& inventory, Unreal::UObject* save, Unreal::FScriptArrayHelper& rows,
                          int capacity = max_escrow)
    {
        auto* defaults = inventory.save_class->GetClassDefaultObject().Get();
        if (!defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) || rows.Num() < 0 || rows.Num() > capacity)
            return false;
        for (auto* prop : std::array<Unreal::FProperty*, 3>{inventory.equipment, inventory.armor, inventory.helmet})
        {
            if (!prop->Identical(prop->ContainerPtrToValuePtr<void>(save), prop->ContainerPtrToValuePtr<void>(defaults)))
                return false;
        }
        std::array<Guid, max_container_slots> seen{};
        for (int index = 0; index < rows.Num(); ++index)
        {
            const void* row = rows.GetRawPtr(index);
            const auto id = inventory.identity(row);
            auto* type = Unreal::Cast<Unreal::UClass>(inventory.klass(row));
            if (!live(type) || !type->IsChildOf(inventory.item_base) || id == Guid{} ||
                std::find(seen.begin(), seen.begin() + index, id) != seen.begin() + index) return false;
            seen[index] = id;
        }
        return true;
    }

    template<typename T>
    bool matches_actor(T* row_property, const void* row, Unreal::UObject* actor, const Unreal::TCHAR* name)
    {
        auto* actor_property = field<T>(actor, name);
        return actor_property && row_property->GetPropertyValueInContainer(row) == actor_property->GetPropertyValueInContainer(actor);
    }

    Value convert(const Inventory& inventory, Unreal::UObject* pawn, Unreal::UObject* actor)
    {
        auto* fn = find<Unreal::UFunction>(converter_path);
        auto* library = find<Unreal::UObject>(library_path);
        auto* object = parameter<Unreal::FObjectProperty>(fn, STR("ItemActor"), 0, false);
        auto* context = parameter<Unreal::FObjectProperty>(fn, STR("__WorldContext"), sizeof(void*), false);
        auto* output = parameter<Unreal::FStructProperty>(fn, STR("Item"), 2 * sizeof(void*), true);
        auto* actor_payload = field<Unreal::FStructProperty>(actor, STR("DataStruct"));
        if (!fn || !library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            fn->GetOuterPrivate() != library->GetClassPrivate() || fn->GetNumParms() != 3 || fn->GetReturnProperty() ||
            fn->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || fn->GetParmsSize() > parameter_capacity ||
            !object || object->GetPropertyClass().Get() != inventory.item_base || !actor->IsA(inventory.item_base) ||
            !context || !context->GetPropertyClass().Get() || !pawn->IsA(context->GetPropertyClass().Get()) ||
            !inventory.same_type(output) || !actor_payload || actor_payload->GetStruct().Get() != inventory.payload->GetStruct().Get())
            return {};

        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> params{};
        auto* storage = params.data();
        output->InitializeValue_InContainer(storage);
        const auto clean = [output](std::byte* address) { output->DestroyValue_InContainer(address); };
        const std::unique_ptr<std::byte, decltype(clean)> output_lifetime(storage, clean);
        object->SetObjectPropertyValue(object->ContainerPtrToValuePtr<void>(storage), actor);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(storage), pawn);
        library->ProcessEvent(fn, storage);
        const auto* generated = output->ContainerPtrToValuePtr<void>(storage);
        if (inventory.klass(generated) != actor->GetClassPrivate() || inventory.identity(generated) == Guid{} ||
            !matches_actor(inventory.ammo, generated, actor, STR("Ammo")) ||
            !matches_actor(inventory.weight, generated, actor, STR("Weight")) ||
            !matches_actor(inventory.value, generated, actor, STR("Value")) ||
            !matches_actor(inventory.tag, generated, actor, STR("DataTag")) ||
            !matches_actor(inventory.never_drop, generated, actor, STR("NeverDrop")) ||
            !inventory.payload->Identical(inventory.payload->ContainerPtrToValuePtr<void>(generated),
                                          actor_payload->ContainerPtrToValuePtr<void>(actor))) return {};
        auto proof = copy_value(inventory.row, generated);
        if (!proof || !inventory.identical(generated, proof.get())) return {};
        return proof;
    }

    struct AddedRows
    {
        Unreal::FScriptArrayHelper& rows;
        int before = rows.Num();
        bool committed{};
        ~AddedRows()
        {
            if (!committed && rows.Num() > before) rows.RemoveValues(before, rows.Num() - before);
        }
    };

    bool sealed_package_row(const Inventory& inventory, const void* row)
    {
        auto* type = find<Unreal::UClass>(sealed_package_path);
        if (!type || inventory.klass(row) != type || inventory.identity(row) == Guid{}) return false;
        const auto amount = inventory.ammo->GetPropertyValueInContainer(row);
        return (amount == 0 || amount == 1) && inventory.weight->GetPropertyValueInContainer(row) == 1.0 &&
            inventory.value->GetPropertyValueInContainer(row) == 50;
    }

    template <bool enemy>
    int capture(const Lua& lua)
    {
        std::array<Address, 2> arguments{};
        ContainerAddresses addresses{};
        const auto argc = lua.get_stack_size();
        if (!enabled() || !Unreal::IsInGameThreadRaw() || argc < 3 || argc > 2 + max_container_slots ||
            (enemy && argc != 2 + max_escrow)) return result(lua, false, "native_loot_escrow_capture_unavailable");
        const auto slots = static_cast<std::size_t>(argc - 2);
        if (!read_addresses(lua, arguments, 2) ||
            !read_addresses(lua, std::span{addresses}.first(slots), 0))
            return result(lua, false, "native_loot_escrow_capture_unavailable");
        Owner owner;
        ContainerActors actors{};
        const auto ticket = [&](bool consume) {
            if constexpr (enemy)
                return !addresses[1] && silverfish::native_enemy_loot::verified_ammo_roll(
                    arguments[0], addresses[0], owner, actors[0], consume);
            return silverfish::shared_loot::verified_escrow_roll(arguments[0], addresses, slots, owner, actors, consume);
        };
        if (!ticket(false))
            return result(lua, false, "native_loot_verified_roll_unavailable");
        Inventory inventory;
        if (!inventory.bind()) return result(lua, false, "native_loot_inventory_schema_unavailable");
        std::string_view failure;
        auto* scratch = isolated_save(arguments[1], inventory, failure);
        if (!scratch) return result(lua, false, failure);
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(scratch));
        if (rows.Num() != 0 || !qualified_escrow(inventory, scratch, rows))
            return result(lua, false, "native_loot_blank_escrow_required");

        std::array<Value, max_container_slots> items;
        std::array<std::string, max_container_slots> ids;
        int count = 0;
        for (std::size_t index = 0; index < actors.size(); ++index)
        {
            if (!actors[index]) continue;
            items[index] = convert(inventory, owner.pawn, actors[index]);
            if (!items[index]) return result(lua, false, "native_loot_stock_actor_conversion_unverified");
            ids[index] = print_guid(inventory.identity(items[index].get()));
            if (std::find(ids.begin(), ids.begin() + index, ids[index]) != ids.begin() + index)
                return result(lua, false, "native_loot_escrow_guid_conflict");
            ++count;
        }

        AddedRows append{rows};
        for (const auto& item : items)
        {
            if (!item) continue;
            const auto expected = rows.Num();
            const auto index = rows.AddValue();
            if (index != expected || rows.Num() != expected + 1)
                return result(lua, false, "native_loot_escrow_append_unverified");
            auto* destination = rows.GetRawPtr(index);
            inventory.row->CopyCompleteValue(destination, item.get());
            if (!inventory.identical(destination, item.get()))
                return result(lua, false, "native_loot_escrow_payload_unverified");
        }
        // Revalidate the live ticket after conversion. Consuming it is the
        // final non-Lua step so the same crate cannot produce a second escrow.
        if (rows.Num() != count || !qualified_escrow(inventory, scratch, rows, static_cast<int>(slots)) ||
            !ticket(true))
            return result(lua, false, "native_loot_escrow_roll_changed");
        append.committed = true;
        result(lua, true, "captured");
        lua.set_integer(count);
        for (std::size_t index = 0; index < slots; ++index) lua.set_string(ids[index]);
        return static_cast<int>(3 + slots);
    }

    int verify_escrow(const Lua& lua)
    {
        std::array<Address, 2> arguments{};
        const auto argc = lua.get_stack_size();
        if (!enabled() || !Unreal::IsInGameThreadRaw() || (argc != 2 && argc != 3) ||
            !read_addresses(lua, arguments, 2) || arguments[0] == arguments[1])
            return result(lua, false, "native_loot_escrow_verify_unavailable");
        int capacity = max_escrow;
        if (argc == 3)
        {
            std::array<Address, 1> container{};
            if (read_addresses(lua, container, 1)) capacity = silverfish::shared_loot::qualified_container_capacity(container[0]);
            else capacity = 0;
            if (!capacity) return result(lua, false, "native_loot_escrow_container_source_unqualified");
        }
        Inventory inventory;
        if (!inventory.bind()) return result(lua, false, "native_loot_inventory_schema_unavailable");
        std::string_view failure;
        auto* captured = isolated_save(arguments[0], inventory, failure);
        if (!captured) return result(lua, false, failure);
        auto* reloaded = isolated_save(arguments[1], inventory, failure);
        if (!reloaded) return result(lua, false, failure);
        Unreal::FScriptArrayHelper first(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(captured));
        Unreal::FScriptArrayHelper second(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(reloaded));
        if (!qualified_escrow(inventory, captured, first, capacity) ||
            !qualified_escrow(inventory, reloaded, second, capacity) || first.Num() != second.Num())
            return result(lua, false, "native_loot_escrow_shape_changed");
        for (int index = 0; index < first.Num(); ++index)
        {
            const auto* a = first.GetRawPtr(index);
            const auto* b = second.GetRawPtr(index);
            if (a == b || !inventory.identical(a, b)) return result(lua, false, "native_loot_reopened_escrow_payload_changed");
        }
        return result(lua, true, "identical");
    }

    int capture_stock_enemy_drop(const Lua& lua)
    {
        std::array<Address, 4> args{};
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != args.size() ||
            !read_addresses(lua, args, args.size())) return result(lua, false, "stock_enemy_escrow_capture_unavailable");
        Owner owner;
        Unreal::UObject* actor{};
        if (!silverfish::stock_enemy_drops::verify_live_drop(args[0], args[2], args[3], owner, actor))
            return result(lua, false, "stock_enemy_escrow_actor_unverified");
        Inventory inventory;
        if (!inventory.bind()) return result(lua, false, "native_loot_inventory_schema_unavailable");
        std::string_view failure;
        auto* scratch = isolated_save(args[1], inventory, failure);
        if (!scratch) return result(lua, false, failure);
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(scratch));
        if (rows.Num() != 0 || !qualified_escrow(inventory, scratch, rows, 1))
            return result(lua, false, "native_loot_blank_escrow_required");
        auto item = convert(inventory, owner.pawn, actor);
        if (!item) return result(lua, false, "native_loot_stock_actor_conversion_unverified");
        AddedRows append{rows};
        const auto index = rows.AddValue();
        if (index != 0 || rows.Num() != 1) return result(lua, false, "native_loot_escrow_append_unverified");
        auto* destination = rows.GetRawPtr(index);
        inventory.row->CopyCompleteValue(destination, item.get());
        Unreal::UObject* confirmed{};
        if (!inventory.identical(destination, item.get()) || !qualified_escrow(inventory, scratch, rows, 1) ||
            !silverfish::stock_enemy_drops::verify_live_drop(args[0], args[2], args[3], owner, confirmed) || confirmed != actor)
            return result(lua, false, "native_loot_escrow_payload_unverified");
        append.committed = true;
        result(lua, true, "captured");
        lua.set_string(print_guid(inventory.identity(item.get())));
        lua.set_integer(inventory.ammo->GetPropertyValueInContainer(item.get()));
        return 4;
    }

    Unreal::UObject* mushroom_source(const Owner& owner, const std::wstring& patch_path,
                                    const std::wstring& actor_path, const Guid& expected)
    {
        auto* patch = find<Unreal::UObject>(patch_path.c_str());
        auto* actor = find<Unreal::UObject>(actor_path.c_str());
        auto* level = field<Unreal::FObjectProperty>(owner.world, STR("PersistentLevel"));
        auto* persistent = level ? level->GetObjectPropertyValue(level->ContainerPtrToValuePtr<void>(owner.world)) : nullptr;
        if (!live(patch) || !live(actor) || !live(persistent) || patch->GetPathName() != patch_path ||
            actor->GetPathName() != actor_path || patch->GetOuterPrivate() != persistent || actor->GetOuterPrivate() != persistent ||
            !patch->HasAnyFlags(Unreal::RF_WasLoaded) || actor->HasAnyFlags(Unreal::RF_WasLoaded) ||
            patch->GetClassPrivate() != find<Unreal::UClass>(mushroom_patch_path) ||
            !actor->IsA(find<Unreal::UClass>(item_base_path))) return nullptr;

        auto* property = field<Unreal::FMapProperty>(patch, STR("SpawnedShrooms"));
        auto* key = property ? Unreal::CastField<Unreal::FObjectProperty>(property->GetKeyProp()) : nullptr;
        auto* value = property ? Unreal::CastField<Unreal::FStructProperty>(property->GetValueProp()) : nullptr;
        if (!key || key->GetPropertyClass().Get() != find<Unreal::UClass>(item_base_path) ||
            key->GetOffset_Internal() != 0 || !value || value->GetSize() != sizeof(Guid) ||
            value->GetStruct().Get() != find<Unreal::UScriptStruct>(guid_struct_path)) return nullptr;
        const auto layout = Unreal::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return nullptr;
        const auto* map = property->ContainerPtrToValuePtr<Unreal::FScriptMap>(patch);
        if (map->Num() < 1 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > 256) return nullptr;
        for (int index = 0; index < map->GetMaxIndex(); ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = map->GetData(index, layout);
            if (!entry || key->GetObjectPropertyValue(entry) != actor) continue;
            if (expected == *value->ContainerPtrToValuePtr<Guid>(entry)) return actor;
        }
        return nullptr;
    }

    int capture_mushroom(const Lua& lua)
    {
        std::array<Address, 4> args{};
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 7 || !lua.is_string(5) ||
            !lua.is_string(6) || !lua.is_string(7) || !read_addresses(lua, args, args.size()))
            return result(lua, false, "native_mushroom_escrow_scope_unavailable");
        const auto patch_path = RC::to_generic_string(lua.get_string());
        const auto actor_path = RC::to_generic_string(lua.get_string());
        const auto expected = parse_guid(lua.get_string());
        if (patch_path.size() > 256 || actor_path.size() > 256 || expected == Guid{} ||
            !patch_path.starts_with(STR("/Game/Classes/Maps/")) || !actor_path.starts_with(STR("/Game/Classes/Maps/")))
            return result(lua, false, "native_mushroom_escrow_scope_unavailable");
        Owner owner;
        auto* actor = silverfish::shared_loot::qualify_escrow_owner(args[0], args[1], args[2], owner) ?
            mushroom_source(owner, patch_path, actor_path, expected) : nullptr;
        if (!actor) return result(lua, false, "native_mushroom_escrow_source_unverified");

        Inventory inventory;
        if (!inventory.bind()) return result(lua, false, "native_loot_inventory_schema_unavailable");
        std::string_view failure;
        auto* scratch = isolated_save(args[3], inventory, failure);
        if (!scratch) return result(lua, false, failure);
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(scratch));
        if (rows.Num() != 0 || !qualified_escrow(inventory, scratch, rows, 1))
            return result(lua, false, "native_loot_blank_escrow_required");
        const auto item = convert(inventory, owner.pawn, actor);
        if (!item || mushroom_source(owner, patch_path, actor_path, expected) != actor)
            return result(lua, false, "native_loot_stock_actor_conversion_unverified");

        AddedRows append{rows};
        const auto index = rows.AddValue();
        if (index != 0 || rows.Num() != 1) return result(lua, false, "native_loot_escrow_append_unverified");
        auto* destination = rows.GetRawPtr(index);
        inventory.row->CopyCompleteValue(destination, item.get());
        if (!inventory.identical(destination, item.get()) || !qualified_escrow(inventory, scratch, rows, 1))
            return result(lua, false, "native_loot_escrow_payload_unverified");
        append.committed = true;
        result(lua, true, "captured");
        lua.set_string(print_guid(inventory.identity(item.get())));
        lua.set_integer(inventory.ammo->GetPropertyValueInContainer(item.get()));
        return 4;
    }

    struct Match
    {
        int count{};
        const void* row{};
    };

    bool inspect_owner(const Inventory& inventory, Unreal::UObject* save, const Guid& guid, Match& match)
    {
        const auto inspect = [&](const void* row) {
            if (inventory.identity(row) != guid) return;
            ++match.count;
            match.row = row;
        };
        for (auto* property : {inventory.backpack, inventory.equipment})
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(save));
            if (rows.Num() < 0 || rows.Num() > max_inventory) return false;
            for (int index = 0; index < rows.Num(); ++index) inspect(rows.GetRawPtr(index));
        }
        inspect(inventory.armor->ContainerPtrToValuePtr<void>(save));
        inspect(inventory.helmet->ContainerPtrToValuePtr<void>(save));
        return true;
    }

    bool other_saves(const Owner& owner, const Inventory& inventory,
                     std::array<Unreal::UObject*, max_party>& saves)
    {
        auto* pawn_type = owner.pawn->GetClassPrivate();
        auto* property = field<Unreal::FObjectProperty>(pawn_type, STR("Save"));
        if (!property || property->GetPropertyClass().Get() != inventory.save_class) return false;
        std::size_t count = 0;
        if (owner.stock != owner.save) saves[count++] = owner.stock;
        std::vector<Unreal::AActor*> pawns;
        Unreal::UGameplayStatics::GetAllActorsOfClass(owner.world, pawn_type, pawns);
        if (std::ranges::find(pawns, owner.pawn) == pawns.end()) return false;
        for (auto* object : pawns)
        {
            if (object == owner.pawn || !live(object) || object->GetClassPrivate() != pawn_type ||
                object->GetWorld() != owner.world) continue;
            auto* save = property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object));
            if (!live(save) || std::ranges::find(saves, save) != saves.end()) continue;
            if (save == owner.save && save == owner.stock) continue;
            if (save->GetClassPrivate() != inventory.save_class || save == owner.save || count == saves.size()) return false;
            saves[count++] = save;
        }
        return true;
    }

    Unreal::UObject* claim_escrow(Address address, const Inventory& inventory, const Owner& owner,
                                std::span<Unreal::UObject* const> protected_saves, std::string_view& failure)
    {
        const auto matches_address = [address](const Unreal::UObject* object) {
            return reinterpret_cast<Address>(object) == address;
        };
        if (matches_address(owner.save) || matches_address(owner.stock) ||
            std::ranges::any_of(protected_saves, matches_address))
        {
            failure = "native_loot_escrow_save_attached_to_owner";
            return nullptr;
        }
        // A released cache entry cannot recognize a recycled UObject address. Cold loads
        // still prove the stock save class, ownership and transient outer before caching.
        for (const auto& reference : qualified_escrows)
        {
            auto* save = reference.Get();
            if (matches_address(save) && live(save) && save->GetClassPrivate() == inventory.save_class &&
                save->GetOuterPrivate() && save->GetOuterPrivate()->GetPathName() == STR("/Engine/Transient"))
                return save;
        }
        return isolated_save(address, inventory, failure);
    }

    Unreal::UObject* canonical_game_instance(const Owner& owner)
    {
        auto* instance_link = field<Unreal::FObjectProperty>(owner.world, STR("OwningGameInstance"));
        auto* instance = instance_link ? instance_link->GetObjectPropertyValue(instance_link->ContainerPtrToValuePtr<void>(owner.world)) : nullptr;
        auto* save_link = field<Unreal::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        if (!live(instance) || instance->GetClassPrivate() != find<Unreal::UClass>(instance_path) ||
            !save_link || save_link->GetObjectPropertyValue(save_link->ContainerPtrToValuePtr<void>(instance)) != owner.stock) return nullptr;
        return instance;
    }

    template <bool enemy, bool merge_only = false>
    int claim(const Lua& lua)
    {
        std::array<Address, 4> arguments{};
        const auto argc = lua.get_stack_size();
        if (!enabled() || !Unreal::IsInGameThreadRaw() || (argc != 5 && (enemy || merge_only || argc != 6)) || !lua.is_string(5) ||
            !read_addresses(lua, arguments, 4)) return result(lua, false, "native_loot_escrow_claim_unavailable");
        const auto guid = parse_guid(lua.get_string());
        if (guid == Guid{}) return result(lua, false, "native_loot_exact_escrow_guid_required");
        const auto qualify = [&](Owner& context) {
            if constexpr (enemy)
                return silverfish::native_enemy_loot::qualify_escrow_claimant(arguments[0], arguments[1], arguments[2], context);
            return silverfish::shared_loot::qualify_escrow_owner(arguments[0], arguments[1], arguments[2], context);
        };
        Owner owner;
        if (!qualify(owner))
            return result(lua, false, "native_loot_exact_claimant_unavailable");
        int capacity = max_escrow;
        if (argc == 6)
        {
            std::array<Address, 1> container{};
            if (read_addresses(lua, container, 1)) capacity = silverfish::shared_loot::qualified_container_capacity(container[0], owner.world);
            else capacity = 0;
            if (!capacity) return result(lua, false, "native_loot_escrow_container_source_unqualified");
        }
        Inventory inventory;
        if (!inventory.bind()) return result(lua, false, "native_loot_inventory_schema_unavailable");
        std::array<Unreal::UObject*, max_party> protected_saves{};
        if (!other_saves(owner, inventory, protected_saves)) return result(lua, false, "native_loot_save_ownership_conflict");
        std::string_view failure;
        auto* escrow = claim_escrow(arguments[3], inventory, owner, protected_saves, failure);
        if (!escrow) return result(lua, false, failure);
        Unreal::FScriptArrayHelper source(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        if (!qualified_escrow(inventory, escrow, source, capacity)) return result(lua, false, "native_loot_escrow_shape_changed");
        const void* item = nullptr;
        for (int index = 0; index < source.Num(); ++index)
        {
            const void* row = source.GetRawPtr(index);
            if (inventory.identity(row) == guid) item = row;
        }
        if (!item) return result(lua, false, "native_loot_escrow_item_unavailable");
        if constexpr (enemy)
        {
            auto* shell = find<Unreal::UClass>(STR("/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"));
            const auto count = inventory.ammo->GetPropertyValueInContainer(item);
            if (!shell || source.Num() != 1 || inventory.klass(item) != shell || count < 3 || count > 9)
                return result(lua, false, "native_enemy_escrow_ammunition_unqualified");
        }

        auto* item_type = Unreal::Cast<Unreal::UClass>(inventory.klass(item));
        const bool cash = !enemy && silverfish::stock_cash::is_family(item_type);
        const int amount = cash ? inventory.value->GetPropertyValueInContainer(item) : 0;
        if (cash && !silverfish::stock_cash::qualified_value(item_type, amount))
            return result(lua, false, "native_loot_cash_payload_unqualified");

        Match recipient;
        if (!inspect_owner(inventory, owner.save, guid, recipient) || recipient.count > 1 ||
            (recipient.row && !inventory.identical(item, recipient.row)))
            return result(lua, false, "native_loot_claimant_guid_conflict");
        auto* backpack = inventory.backpack;
        auto* target = backpack->ContainerPtrToValuePtr<void>(owner.save);
        Unreal::FScriptArrayHelper destination(backpack, target);
        if (destination.Num() && source.GetRawPtr(0) == destination.GetRawPtr(0))
            return result(lua, false, "native_loot_escrow_backpack_alias");
        auto stack = cash || recipient.row ? StackPickup{} : propose_stack(inventory, item_type, item, destination);
        if constexpr (merge_only)
        {
            if (cash || recipient.row) return result(lua, false, "native_loot_ground_merge_already_owned_or_cash");
            if (!stack.row) return result(lua, true, "append");
        }
        std::array<Value, max_party> protected_backpacks;
        for (std::size_t index = 0; index < protected_saves.size() && protected_saves[index]; ++index)
        {
            auto* save = protected_saves[index];
            Match other;
            if (!inspect_owner(inventory, save, guid, other) || other.count)
                return result(lua, false, "native_loot_other_owner_guid_conflict");
            auto* data = backpack->ContainerPtrToValuePtr<void>(save);
            Unreal::FScriptArrayHelper rows(backpack, data);
            if (data == target || (rows.Num() && destination.Num() && rows.GetRawPtr(0) == destination.GetRawPtr(0)))
                return result(lua, false, "native_loot_save_backpack_alias");
            if (!recipient.row) protected_backpacks[index] = copy_value(backpack, data);
            if (!recipient.row && !protected_backpacks[index]) return result(lua, false, "native_loot_save_snapshot_unavailable");
        }
        if (recipient.row)
            return cash ? result(lua, false, "native_loot_cash_inventory_conflict") : result(lua, true, "already_claimed");
        auto before = copy_value(backpack, target);
        auto escrow_before = copy_value(backpack, backpack->ContainerPtrToValuePtr<void>(escrow));
        if (!before || !escrow_before) return result(lua, false, "native_loot_save_snapshot_unavailable");

        const auto same_owner_and_escrow = [&] {
            Owner current;
            return backpack->Identical(escrow_before.get(), backpack->ContainerPtrToValuePtr<void>(escrow)) &&
                qualify(current) && current.world == owner.world && current.pawn == owner.pawn &&
                current.save == owner.save && current.stock == owner.stock;
        };
        const auto protected_inventories_unchanged = [&] {
            for (std::size_t index = 0; index < protected_saves.size() && protected_saves[index]; ++index)
            {
                if (!backpack->Identical(protected_backpacks[index].get(),
                        backpack->ContainerPtrToValuePtr<void>(protected_saves[index]))) return false;
            }
            return true;
        };
        if (cash)
        {
            auto* instance = canonical_game_instance(owner);
            silverfish::stock_wallet::Wallet wallet;
            if (!instance || !wallet.bind(instance)) return result(lua, false, "native_loot_cash_schema_unavailable");
            const auto balance = wallet.amount(instance);
            if (balance < 0 || balance > (std::numeric_limits<int>::max)() - amount)
                return result(lua, false, "native_loot_cash_wallet_out_of_range");

            // Give_PlayerCash is the same stock call used by Money_Master's Interact.
            wallet.change(instance, amount, false);
            const auto [updated, found] = wallet.flag_value(instance, STR("Player_Cash"));
            if (!found || updated != balance + amount || !same_owner_and_escrow() ||
                canonical_game_instance(owner) != instance || !backpack->Identical(before.get(), target) ||
                !protected_inventories_unchanged())
                return result(lua, false, "mutation_unverified_native_loot_cash_readback");

            lua.set_bool(true);
            lua.set_string("cash_credited");
            lua.set_integer(amount);
            lua.set_integer(balance);
            lua.set_integer(updated);
            return 5;
        }

        if (stack.row)
        {
            const auto* current = destination.GetRawPtr(stack.index);
            Match existing;
            if (!inspect_owner(inventory, owner.save, stack.guid, existing) || existing.count != 1 || existing.row != current)
                return result(lua, false, "native_loot_claimant_stack_guid_conflict");
            for (auto* save : protected_saves)
            {
                if (!save) break;
                Match other;
                if (!inspect_owner(inventory, save, stack.guid, other) || other.count)
                    return result(lua, false, "native_loot_other_owner_stack_guid_conflict");
            }
            const std::array<Unreal::FProperty*, 3> gear{inventory.equipment, inventory.armor, inventory.helmet};
            std::array<Value, gear.size()> gear_before;
            for (std::size_t index = 0; index < gear.size(); ++index)
            {
                gear_before[index] = copy_value(gear[index], gear[index]->ContainerPtrToValuePtr<void>(owner.save));
                if (!gear_before[index]) return result(lua, false, "native_loot_save_snapshot_unavailable");
            }
            const auto gear_unchanged = [&] {
                for (std::size_t index = 0; index < gear.size(); ++index)
                    if (!gear[index]->Identical(gear_before[index].get(), gear[index]->ContainerPtrToValuePtr<void>(owner.save)))
                        return false;
                return true;
            };
            const bool stock_agrees = silverfish::loot_escrow::stock_stack_matches(
                owner.pawn, inventory.row, current, stack.row.get(), stack.units);
            if (!same_owner_and_escrow() || !backpack->Identical(before.get(), target) ||
                !gear_unchanged() || !protected_inventories_unchanged())
                return result(lua, false, "mutation_unverified_native_loot_stock_stack_calculation");
            if (stock_agrees)
            {
                auto proposed = copy_value(backpack, before.get());
                if (!proposed) return result(lua, false, "native_loot_save_snapshot_unavailable");
                Unreal::FScriptArrayHelper staged(backpack, proposed.get());
                inventory.row->CopyCompleteValue(staged.GetRawPtr(stack.index), stack.row.get());
                backpack->CopyCompleteValue(target, proposed.get());
                if (!same_owner_and_escrow() || !gear_unchanged() || !protected_inventories_unchanged() ||
                    !backpack->Identical(proposed.get(), target) ||
                    !inventory.identical(destination.GetRawPtr(stack.index), stack.row.get()))
                {
                    if (same_owner_and_escrow()) backpack->CopyCompleteValue(target, before.get());
                    return result(lua, false, "mutation_unverified_native_loot_stack_readback");
                }
                result(lua, true, "merged");
                lua.set_integer(stack.units);
                lua.set_integer(stack.before);
                lua.set_integer(stack.before + stack.units);
                lua.set_string(print_guid(stack.guid));
                return 6;
            }
        }
        if constexpr (merge_only) return result(lua, true, "append");

        if (destination.Num() >= max_inventory) return result(lua, false, "native_loot_claimant_backpack_full");
        AddedRows append{destination};
        const auto index = destination.AddValue();
        if (index != append.before || destination.Num() != append.before + 1)
            return result(lua, false, "native_loot_claim_append_unverified");
        auto* new_row = destination.GetRawPtr(index);
        inventory.row->CopyCompleteValue(new_row, item);
        if (!inventory.identical(new_row, item) || !same_owner_and_escrow())
            return result(lua, false, "native_loot_claim_payload_or_owner_unverified");
        Unreal::FScriptArrayHelper old(backpack, before.get());
        for (int i = 0; i < append.before; ++i)
        {
            if (!inventory.identical(old.GetRawPtr(i), destination.GetRawPtr(i)))
                return result(lua, false, "native_loot_claim_backpack_changed");
        }
        if (!protected_inventories_unchanged())
            return result(lua, false, "native_loot_other_owner_backpack_changed");
        append.committed = true;
        return result(lua, true, "claimed");
    }
}

namespace silverfish::loot_escrow
{
    bool stock_stack_matches(Unreal::UObject* pawn, Unreal::FStructProperty* row,
                             const void* previous, const void* expected, int difference)
    {
        auto* fn = find<Unreal::UFunction>(stack_path);
        auto* library = find<Unreal::UObject>(stack_library);
        auto* input = field<Unreal::FStructProperty>(fn, STR("InventoryItem_Struct"));
        auto* delta = parameter<Unreal::FIntProperty>(fn, STR("Difference"), 0x50, false);
        auto* context = parameter<Unreal::FObjectProperty>(fn, STR("__WorldContext"), 0x58, false);
        auto* output = parameter<Unreal::FStructProperty>(fn, STR("StackedStruct"), 0x60, true);
        auto* payload = field<Unreal::FStructProperty>(row->GetStruct().Get(), STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        if (!fn || !library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            fn->GetOuterPrivate() != library->GetClassPrivate() || fn->GetNumParms() != 4 ||
            fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || !input || !delta || !context || !output || !payload ||
            !input->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm | Unreal::CPF_ReferenceParm | Unreal::CPF_ConstParm) ||
            input->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) || input->GetOffset_Internal() != 0 ||
            output->HasAnyPropertyFlags(Unreal::CPF_ReferenceParm) || input->GetSize() != 0x50 || output->GetSize() != 0x50 ||
            input->GetStruct().Get() != row->GetStruct().Get() || output->GetStruct().Get() != row->GetStruct().Get() ||
            context->GetPropertyClass().Get() != find<Unreal::UClass>(STR("/Script/CoreUObject.Object"))) return false;

        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        auto* storage = buffer.data();
        input->InitializeValue_InContainer(storage);
        output->InitializeValue_InContainer(storage);
        auto release = [input, output](std::byte* data) {
            output->DestroyValue_InContainer(data);
            input->DestroyValue_InContainer(data);
        };
        const std::unique_ptr<std::byte, decltype(release)> lifetime(storage, release);
        input->CopyCompleteValue(input->ContainerPtrToValuePtr<void>(storage), previous);
        delta->SetPropertyValueInContainer(storage, difference);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(storage), pawn);
        library->ProcessEvent(fn, storage);
        const auto* calculated = output->ContainerPtrToValuePtr<void>(storage);
        return row->Identical(calculated, expected) &&
            payload->Identical(payload->ContainerPtrToValuePtr<void>(calculated), payload->ContainerPtrToValuePtr<void>(expected));
    }

    bool convert_stock_item(Unreal::UObject* pawn, Unreal::UObject* actor, void* initialized_row)
    {
        Inventory inventory;
        if (!pawn || !actor || !initialized_row || !inventory.bind()) return false;
        const auto row = convert(inventory, pawn, actor);
        if (!row) return false;
        inventory.row->CopyCompleteValue(initialized_row, row.get());
        return inventory.identical(initialized_row, row.get());
    }
    bool backpack_has_class(Unreal::UObject* save, Unreal::UClass* item_class)
    {
        Inventory inventory;
        if (!Unreal::IsInGameThreadRaw() || !live(save) || save->GetClassPrivate() != inventory.save_class ||
            !inventory.bind() || !live(item_class) || !item_class->IsChildOf(inventory.item_base)) return false;
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(save));
        if (rows.Num() < 0 || rows.Num() > max_inventory) return false;
        for (int index = 0; index < rows.Num(); ++index)
        {
            const auto* row = rows.GetRawPtr(index);
            if (inventory.klass(row) != item_class) continue;
            const auto id = inventory.identity(row);
            Match identity;
            if (id != Guid{} && inspect_owner(inventory, save, id, identity) && identity.count == 1 && identity.row == row)
                return true;
        }
        return false;
    }

    bool stock_spade_guid(Unreal::UObject* save, std::string& guid)
    {
        Inventory inventory;
        if (!Unreal::IsInGameThreadRaw() || !live(save) || save->GetClassPrivate() != inventory.save_class ||
            !inventory.bind()) return false;
        auto* selected = field<Unreal::FIntProperty>(save, STR("PlayerEquipIndex"));
        auto* type = find<Unreal::UClass>(spade_path);
        if (!selected || !type) return false;
        const auto index = selected->GetPropertyValueInContainer(save);
        Unreal::FScriptArrayHelper rows(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(save));
        if (index < 0 || index >= rows.Num() || rows.Num() > max_inventory) return false;
        const auto* row = rows.GetRawPtr(index);
        const auto id = inventory.identity(row);
        if (inventory.klass(row) != type || inventory.ammo->GetPropertyValueInContainer(row) != -1 || id == Guid{}) return false;
        Match identity;
        if (!inspect_owner(inventory, save, id, identity) || identity.count != 1 || identity.row != row) return false;
        guid = print_guid(id);
        return true;
    }

    bool sealed_package_guid(Unreal::UObject* save, std::string& guid)
    {
        Inventory inventory;
        auto* type = find<Unreal::UClass>(sealed_package_path);
        if (!Unreal::IsInGameThreadRaw() || !type || !live(save) || save->GetClassPrivate() != inventory.save_class ||
            !inventory.bind()) return false;
        Guid id{};
        const void* assigned{};
        for (auto* property : {inventory.backpack, inventory.equipment})
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(save));
            if (rows.Num() < 0 || rows.Num() > max_inventory) return false;
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto* row = rows.GetRawPtr(index);
                if (inventory.klass(row) != type) continue;
                if (assigned || property != inventory.backpack || !sealed_package_row(inventory, row)) return false;
                assigned = row;
                id = inventory.identity(row);
            }
        }
        if (inventory.klass(inventory.armor->ContainerPtrToValuePtr<void>(save)) == type ||
            inventory.klass(inventory.helmet->ContainerPtrToValuePtr<void>(save)) == type) return false;
        if (assigned)
        {
            Match identity;
            if (!inspect_owner(inventory, save, id, identity) || identity.count != 1 || identity.row != assigned) return false;
            guid = print_guid(id);
        }
        else guid.clear();
        return true;
    }

    bool capture_sealed_package(Unreal::UObject* pawn, Unreal::UObject* package, Address scratch_address, std::string& guid)
    {
        Inventory inventory;
        auto* type = find<Unreal::UClass>(sealed_package_path);
        if (!Unreal::IsInGameThreadRaw() || !type || !live(package) || package->GetClassPrivate() != type ||
            !live(pawn) || !inventory.bind()) return false;
        std::string_view failure;
        auto* scratch = isolated_save(scratch_address, inventory, failure);
        if (!scratch) return false;
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(scratch));
        if (rows.Num() != 0 || !qualified_escrow(inventory, scratch, rows, 1)) return false;
        const auto item = convert(inventory, pawn, package);
        if (!item || !sealed_package_row(inventory, item.get())) return false;
        AddedRows append{rows};
        const auto index = rows.AddValue();
        if (index != 0 || rows.Num() != 1) return false;
        inventory.row->CopyCompleteValue(rows.GetRawPtr(0), item.get());
        if (!inventory.identical(rows.GetRawPtr(0), item.get()) || !qualified_escrow(inventory, scratch, rows, 1)) return false;
        guid = print_guid(inventory.identity(item.get()));
        append.committed = true;
        return true;
    }

    bool verify_sealed_package(Address scratch_address, std::string_view guid, Unreal::UObject* pawn, Unreal::UObject* package)
    {
        Inventory inventory;
        const auto id = parse_guid(guid);
        if (!Unreal::IsInGameThreadRaw() || id == Guid{} || !inventory.bind()) return false;
        std::string_view failure;
        auto* scratch = isolated_save(scratch_address, inventory, failure);
        if (!scratch) return false;
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(scratch));
        if (rows.Num() != 1 || !qualified_escrow(inventory, scratch, rows, 1)) return false;
        const auto* stored = rows.GetRawPtr(0);
        if (inventory.identity(stored) != id || !sealed_package_row(inventory, stored)) return false;
        if (!package) return pawn == nullptr;
        if (!live(pawn) || !live(package) || package->GetClassPrivate() != inventory.klass(stored)) return false;
        const auto actual = convert(inventory, pawn, package);
        if (!actual) return false;
        // ItemActorToStruct assigns a fresh GUID on every conversion. Only the
        // scratch copy is normalized before comparing all other stock fields.
        inventory.guid->CopyCompleteValue(inventory.guid->ContainerPtrToValuePtr<void>(actual.get()),
                                          inventory.guid->ContainerPtrToValuePtr<void>(stored));
        return inventory.identical(stored, actual.get());
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishCaptureNativeLootEscrow", capture<false>);
        lua.register_function("SilverfishVerifyNativeLootEscrow", verify_escrow);
        lua.register_function("SilverfishClaimNativeLootEscrow", claim<false>);
        lua.register_function("SilverfishMergeNativeGroundStack", claim<false, true>);
        lua.register_function("SilverfishCaptureNativeEnemyLootEscrow", capture<true>);
        lua.register_function("SilverfishCaptureStockEnemyDrop", capture_stock_enemy_drop);
        lua.register_function("SilverfishCaptureNativeMushroom", capture_mushroom);
        lua.register_function("SilverfishClaimNativeEnemyLootEscrow", claim<true>);
    }
}
