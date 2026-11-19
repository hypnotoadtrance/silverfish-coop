#include "pickup.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <exception>
#include <string_view>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto bandage_path = STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int native_inventory_limit = 4096;

    template <typename T>
    T* find(const Unreal::TCHAR* name)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, name);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* property = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool is_structure(Unreal::FProperty* property, Unreal::UScriptStruct* structure)
    {
        auto* typed = Unreal::CastField<Unreal::FStructProperty>(property);
        return typed && typed->GetArrayDim() == 1 && typed->GetStruct().Get() == structure;
    }

    int result(const Lua& lua, bool success, std::string_view detail)
    {
        lua.set_bool(success);
        lua.set_string(detail);
        return 2;
    }

    struct ItemData
    {
        std::int32_t ammo{};
        double weight{};
        std::int32_t value{};
        bool never_drop{};
    };

    bool valid_values(std::int64_t ammo, double weight, std::int64_t value)
    {
        return ammo >= -1 && ammo <= 1000000 &&
               std::isfinite(weight) && weight >= 0 && weight <= 1000000 &&
               value >= 0 && value <= 1000000000;
    }

    struct Bandage
    {
        Unreal::UClass* type = find<Unreal::UClass>(bandage_path);
        Unreal::UObject* defaults = type ? type->GetClassDefaultObject().Get() : nullptr;
        Unreal::FNameProperty* tag = field<Unreal::FNameProperty>(defaults, STR("DataTag"));
        Unreal::FStructProperty* data = field<Unreal::FStructProperty>(defaults, STR("DataStruct"));

        std::string_view failure() const
        {
            if (!type) return "stock_bandage_class_unavailable";
            if (!defaults || defaults->GetClassPrivate() != type ||
                !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject))
            {
                return "stock_bandage_class_default_unavailable";
            }
            if (!tag) return "stock_bandage_datatag_unavailable";
            if (!data) return "stock_bandage_datastruct_unavailable";
            return {};
        }
    };

    int read_pickup(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1))
        {
            return result(lua, false, "pickup_actor_required_on_game_thread");
        }
        const auto target = lua.get_integer(1);
        const Bandage bandage;
        if (target <= 0)
        {
            return result(lua, false, "invalid_pickup_actor");
        }
        if (const auto failure = bandage.failure(); !failure.empty())
        {
            return result(lua, false, failure);
        }
        Unreal::UObject* actor{};
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (reinterpret_cast<std::uintptr_t>(object) != static_cast<std::uintptr_t>(target))
            {
                return RC::LoopAction::Continue;
            }
            if (object->GetClassPrivate() == bandage.type && live(object))
            {
                actor = object;
            }
            return RC::LoopAction::Break;
        });
        if (!actor)
        {
            return result(lua, false, "stock_bandage_actor_unavailable");
        }
        if (!bandage.tag->Identical_InContainer(actor, bandage.defaults))
        {
            return result(lua, false, "pickup_datatag_differs_from_stock");
        }
        if (!bandage.data->Identical_InContainer(actor, bandage.defaults))
        {
            return result(lua, false, "pickup_datastruct_differs_from_stock");
        }
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        auto* weight = field<Unreal::FDoubleProperty>(actor, STR("Weight"));
        auto* value = field<Unreal::FIntProperty>(actor, STR("Value"));
        auto* never_drop = field<Unreal::FBoolProperty>(actor, STR("NeverDrop"));
        if (!ammo || !weight || !value || !never_drop)
        {
            return result(lua, false, "pickup_actor_schema_unavailable");
        }
        const ItemData record{ammo->GetPropertyValueInContainer(actor), weight->GetPropertyValueInContainer(actor),
                              value->GetPropertyValueInContainer(actor), never_drop->GetPropertyValueInContainer(actor)};
        if (!valid_values(record.ammo, record.weight, record.value))
        {
            return result(lua, false, "pickup_actor_values_out_of_range");
        }
        lua.set_bool(true);
        lua.set_integer(record.ammo);
        lua.set_number(record.weight);
        lua.set_integer(record.value);
        lua.set_bool(record.never_drop);
        return 5;
    }

    bool parse_guid(std::string_view input, Guid& guid)
    {
        if (input.size() != 32 || !std::ranges::all_of(input, [](char value) {
                return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F');
            }))
        {
            return false;
        }
        for (std::size_t index = 0; index < guid.size(); ++index)
        {
            const char* start = input.data() + index * 8;
            const auto parsed = std::from_chars(start, start + 8, guid[index], 16);
            if (parsed.ec != std::errc{} || parsed.ptr != start + 8)
            {
                return false;
            }
        }
        return std::ranges::any_of(guid, [](auto value) { return value != 0; });
    }

    struct SaveTargets
    {
        Unreal::UObject* stock{};
        Unreal::UObject* guest{};
    };

    bool resolve_targets(std::int64_t stock_address, std::int64_t pawn_address, std::int64_t guest_address,
                         SaveTargets& targets)
    {
        if (stock_address <= 0 || pawn_address <= 0 || guest_address <= 0 || stock_address == guest_address)
        {
            return false;
        }
        auto* save_class = find<Unreal::UClass>(save_path);
        auto* pawn_class = find<Unreal::UClass>(pawn_path);
        auto* instance_class = find<Unreal::UClass>(instance_path);
        auto* slot = field<Unreal::FObjectProperty>(instance_class, STR("SaveSlot_Ref"));
        auto* pawn_save = field<Unreal::FObjectProperty>(pawn_class, STR("Save"));
        if (!save_class || !slot || !pawn_save || slot->GetPropertyClass().Get() != save_class ||
            pawn_save->GetPropertyClass().Get() != save_class)
        {
            return false;
        }
        Unreal::UObject* pawn{};
        bool stock_is_canonical = false;
        bool guest_is_canonical = false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            const auto address = reinterpret_cast<std::uintptr_t>(object);
            auto* type = object->GetClassPrivate();
            if (type == instance_class && live(object))
            {
                const auto current = reinterpret_cast<std::uintptr_t>(
                    slot->GetObjectPropertyValue(slot->ContainerPtrToValuePtr<void>(object)));
                stock_is_canonical |= current == static_cast<std::uintptr_t>(stock_address);
                guest_is_canonical |= current == static_cast<std::uintptr_t>(guest_address);
            }
            else if (address == static_cast<std::uintptr_t>(pawn_address) && type == pawn_class && live(object))
            {
                pawn = object;
            }
            else if (address == static_cast<std::uintptr_t>(stock_address) && type == save_class && live(object))
            {
                targets.stock = object;
            }
            else if (address == static_cast<std::uintptr_t>(guest_address) && type == save_class && live(object))
            {
                targets.guest = object;
            }
            return RC::LoopAction::Continue;
        });
        return targets.stock && targets.guest && stock_is_canonical && !guest_is_canonical && pawn &&
               pawn_save->GetObjectPropertyValue(pawn_save->ContainerPtrToValuePtr<void>(pawn)) == targets.guest;
    }

    struct ItemSchema
    {
        Unreal::UScriptStruct* structure = find<Unreal::UScriptStruct>(item_path);
        Unreal::UScriptStruct* guid_structure = find<Unreal::UScriptStruct>(guid_path);
        Unreal::FClassProperty* type = field<Unreal::FClassProperty>(structure, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        Unreal::FIntProperty* ammo = field<Unreal::FIntProperty>(structure, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        Unreal::FDoubleProperty* weight = field<Unreal::FDoubleProperty>(structure, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        Unreal::FIntProperty* value = field<Unreal::FIntProperty>(structure, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        Unreal::FNameProperty* tag = field<Unreal::FNameProperty>(structure, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        Unreal::FStructProperty* data = field<Unreal::FStructProperty>(structure, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        Unreal::FBoolProperty* never_drop = field<Unreal::FBoolProperty>(structure, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        Unreal::FStructProperty* guid = field<Unreal::FStructProperty>(structure, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<Unreal::FIntProperty*, 4> components{
            field<Unreal::FIntProperty>(guid_structure, guid_names[0]), field<Unreal::FIntProperty>(guid_structure, guid_names[1]),
            field<Unreal::FIntProperty>(guid_structure, guid_names[2]), field<Unreal::FIntProperty>(guid_structure, guid_names[3])};

        bool available(const Bandage& bandage) const
        {
            auto* base = type ? type->GetMetaClass().Get() : nullptr;
            return base && bandage.type->IsChildOf(base) &&
                   ammo && value && weight && tag && data && never_drop && guid_structure &&
                   is_structure(guid, guid_structure) && data->GetStruct().Get() == bandage.data->GetStruct().Get() &&
                   std::ranges::all_of(components, [](const auto* field) { return field != nullptr; });
        }

        Guid item_guid(const void* item) const
        {
            const void* source = guid->ContainerPtrToValuePtr<void>(item);
            return {
                static_cast<std::uint32_t>(components[0]->GetPropertyValueInContainer(source)),
                static_cast<std::uint32_t>(components[1]->GetPropertyValueInContainer(source)),
                static_cast<std::uint32_t>(components[2]->GetPropertyValueInContainer(source)),
                static_cast<std::uint32_t>(components[3]->GetPropertyValueInContainer(source)),
            };
        }

        bool stock_metadata(const void* item, const Bandage& bandage) const
        {
            return type->GetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(item)) == bandage.type &&
                   tag->Identical(tag->ContainerPtrToValuePtr<void>(item),
                                  bandage.tag->ContainerPtrToValuePtr<void>(bandage.defaults)) &&
                   data->Identical(data->ContainerPtrToValuePtr<void>(item),
                                   bandage.data->ContainerPtrToValuePtr<void>(bandage.defaults));
        }

        bool matches(const void* item, const Bandage& bandage, const ItemData& values) const
        {
            return stock_metadata(item, bandage) &&
                   ammo->GetPropertyValueInContainer(item) == values.ammo &&
                   weight->GetPropertyValueInContainer(item) == values.weight &&
                   value->GetPropertyValueInContainer(item) == values.value &&
                   never_drop->GetPropertyValueInContainer(item) == values.never_drop;
        }

        void write(void* item, const Guid& identity, const Bandage& bandage, const ItemData& values) const
        {
            type->SetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(item), bandage.type);
            ammo->SetPropertyValueInContainer(item, values.ammo);
            weight->SetPropertyValueInContainer(item, values.weight);
            value->SetPropertyValueInContainer(item, values.value);
            never_drop->SetPropertyValueInContainer(item, values.never_drop);
            tag->CopyCompleteValue(tag->ContainerPtrToValuePtr<void>(item),
                                   bandage.tag->ContainerPtrToValuePtr<void>(bandage.defaults));
            data->CopyCompleteValue(data->ContainerPtrToValuePtr<void>(item),
                                    bandage.data->ContainerPtrToValuePtr<void>(bandage.defaults));
            void* destination = guid->ContainerPtrToValuePtr<void>(item);
            for (std::size_t index = 0; index < identity.size(); ++index)
            {
                components[index]->SetPropertyValueInContainer(destination, static_cast<std::int32_t>(identity[index]));
            }
        }
    };

    struct Search
    {
        const void* item{};
        int count{};
        int backpack_index{-1};

        void consider(const ItemSchema& schema, const Guid& guid, const void* candidate, int index = -1)
        {
            if (schema.item_guid(candidate) == guid)
            {
                item = candidate;
                backpack_index = index;
                ++count;
            }
        }
    };

    struct Inventory
    {
        Unreal::FArrayProperty* backpack;
        Unreal::FArrayProperty* equipment;
        Unreal::FStructProperty* helmet;
        Unreal::FStructProperty* armor;

        explicit Inventory(Unreal::UObject* guest) :
            backpack(field<Unreal::FArrayProperty>(guest, STR("PlayerBackpack"))),
            equipment(field<Unreal::FArrayProperty>(guest, STR("PlayerEquipment"))),
            helmet(field<Unreal::FStructProperty>(guest, STR("PlayerHelmet"))),
            armor(field<Unreal::FStructProperty>(guest, STR("PlayerArmor"))) {}

        bool matches(Unreal::UScriptStruct* type) const
        {
            return backpack && equipment && is_structure(backpack->GetInner(), type) &&
                   is_structure(equipment->GetInner(), type) && is_structure(helmet, type) && is_structure(armor, type);
        }

        bool scan(const SaveTargets& targets, const ItemSchema& schema, const Guid& guid,
                  Search& stock, Search& guest) const
        {
            for (auto* array : {backpack, equipment})
            {
                Unreal::FScriptArrayHelper stock_items(array, array->ContainerPtrToValuePtr<void>(targets.stock));
                Unreal::FScriptArrayHelper guest_items(array, array->ContainerPtrToValuePtr<void>(targets.guest));
                if (stock_items.Num() > native_inventory_limit || guest_items.Num() > native_inventory_limit ||
                    (stock_items.Num() > 0 && guest_items.Num() > 0 && stock_items.GetRawPtr() == guest_items.GetRawPtr()))
                {
                    return false;
                }
                for (int index = 0; index < stock_items.Num(); ++index)
                {
                    stock.consider(schema, guid, stock_items.GetRawPtr(index));
                }
                for (int index = 0; index < guest_items.Num(); ++index)
                {
                    guest.consider(schema, guid, guest_items.GetRawPtr(index), array == backpack ? index : -1);
                }
            }
            for (auto* worn : {helmet, armor})
            {
                stock.consider(schema, guid, worn->ContainerPtrToValuePtr<void>(targets.stock));
                guest.consider(schema, guid, worn->ContainerPtrToValuePtr<void>(targets.guest));
            }
            return true;
        }
    };

    class NewSlot
    {
    public:
        explicit NewSlot(Unreal::FScriptArrayHelper& array) : array(array), index(array.AddValue()) {}
        ~NewSlot()
        {
            if (!keep)
            {
                array.RemoveValues(index, 1);
            }
        }
        NewSlot(const NewSlot&) = delete;
        NewSlot& operator=(const NewSlot&) = delete;

        void* item() { return array.GetRawPtr(index); }
        void commit() { keep = true; }

    private:
        Unreal::FScriptArrayHelper& array;
        int index;
        bool keep{};
    };

    int append_pickup(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 8 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) ||
            !lua.is_string(4) || !lua.is_integer(5) || !lua.is_number(6) ||
            !lua.is_integer(7) || !lua.is_bool(8))
        {
            return result(lua, false, "pickup_data_required_on_game_thread");
        }
        // The bridge getters consume values. Read from the end of the stack.
        const auto never_drop = lua.get_bool(8);
        const auto value = lua.get_integer(7);
        const auto weight = lua.get_number(6);
        const auto ammo = lua.get_integer(5);
        Guid guid{};
        if (!parse_guid(lua.get_string(4), guid) || !valid_values(ammo, weight, value))
        {
            return result(lua, false, "pickup_payload_out_of_range");
        }
        const auto guest_address = lua.get_integer(3);
        const auto pawn_address = lua.get_integer(2);
        const auto stock_address = lua.get_integer(1);
        const ItemData record{static_cast<std::int32_t>(ammo), weight, static_cast<std::int32_t>(value), never_drop};
        SaveTargets targets;
        if (!resolve_targets(stock_address, pawn_address, guest_address, targets))
        {
            return result(lua, false, "pickup_guest_save_not_detached");
        }
        const Bandage bandage;
        if (const auto failure = bandage.failure(); !failure.empty())
        {
            return result(lua, false, failure);
        }
        const ItemSchema schema;
        const Inventory inventory(targets.guest);
        if (!schema.available(bandage) || !inventory.matches(schema.structure))
        {
            return result(lua, false, "pickup_inventory_schema_unavailable");
        }

        Search stock, guest;
        if (!inventory.scan(targets, schema, guid, stock, guest))
        {
            return result(lua, false, "pickup_inventory_invalid");
        }
        if (stock.count != 0 || guest.count > 1 || (guest.item && !schema.matches(guest.item, bandage, record)))
        {
            return result(lua, false, "pickup_guid_already_in_use");
        }
        if (guest.item)
        {
            return result(lua, true, "pickup_already_present");
        }

        auto* backpack = inventory.backpack;
        Unreal::FScriptArrayHelper destination(backpack, backpack->ContainerPtrToValuePtr<void>(targets.guest));
        if (destination.Num() >= native_inventory_limit)
        {
            return result(lua, false, "pickup_backpack_full");
        }
        NewSlot slot(destination);
        auto* added = slot.item();
        schema.write(added, guid, bandage, record);
        if (schema.item_guid(added) != guid || !schema.matches(added, bandage, record))
        {
            return result(lua, false, "pickup_write_mismatch");
        }
        slot.commit();
        return result(lua, true, "pickup_appended");
    }

    int remove_pickup(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4))
        {
            return result(lua, false, "pickup_guid_required_on_game_thread");
        }
        Guid guid{};
        if (!parse_guid(lua.get_string(4), guid))
        {
            return result(lua, false, "invalid_pickup_guid");
        }
        const auto guest_address = lua.get_integer(3);
        const auto pawn_address = lua.get_integer(2);
        const auto stock_address = lua.get_integer(1);
        SaveTargets targets;
        if (!resolve_targets(stock_address, pawn_address, guest_address, targets))
        {
            return result(lua, false, "pickup_guest_save_not_detached");
        }
        const Bandage bandage;
        if (const auto failure = bandage.failure(); !failure.empty())
        {
            return result(lua, false, failure);
        }
        const ItemSchema schema;
        const Inventory inventory(targets.guest);
        if (!schema.available(bandage) || !inventory.matches(schema.structure))
        {
            return result(lua, false, "pickup_inventory_schema_unavailable");
        }

        Search stock, guest;
        if (!inventory.scan(targets, schema, guid, stock, guest))
        {
            return result(lua, false, "pickup_inventory_invalid");
        }
        if (stock.count != 0 || guest.count > 1)
        {
            return result(lua, false, "pickup_guid_already_in_use");
        }
        if (guest.count == 0)
        {
            return result(lua, true, "pickup_already_absent");
        }
        if (guest.backpack_index < 0 || !schema.stock_metadata(guest.item, bandage))
        {
            return result(lua, false, "pickup_is_not_a_managed_backpack_bandage");
        }

        auto* backpack = inventory.backpack;
        Unreal::FScriptArrayHelper destination(backpack, backpack->ContainerPtrToValuePtr<void>(targets.guest));
        const int before = destination.Num();
        destination.RemoveValues(guest.backpack_index, 1);
        Search stock_after, guest_after;
        if (destination.Num() != before - 1 || !inventory.scan(targets, schema, guid, stock_after, guest_after) ||
            stock_after.count != 0 || guest_after.count != 0)
        {
            return result(lua, false, "pickup_removal_mismatch");
        }
        return result(lua, true, "pickup_removed");
    }

    template <int (*Operation)(const Lua&)>
    int run(const Lua& lua)
    {
        try
        {
            return Operation(lua);
        }
        catch (const std::exception&)
        {
            return result(lua, false, "native_pickup_operation_failed");
        }
    }
}

void silverfish::register_native_pickup_lua(RC::LuaMadeSimple::Lua& lua)
{
    lua.register_function("SilverfishReadNativePickup", run<read_pickup>);
    lua.register_function("SilverfishAppendNativePickup", run<append_pickup>);
    lua.register_function("SilverfishRemoveNativePickup", run<remove_pickup>);
}
