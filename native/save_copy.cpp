#include "save_copy.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <exception>
#include <format>
#include <limits>
#include <string>
#include <string_view>
#include <vector>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto bandage_path = STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C");
    constexpr std::array vitals_names{
        STR("PlayerHealth"), STR("PlayerRegen"), STR("PlayerFood"),
        STR("PlayerStim"), STR("PlayerRads"), STR("PlayerShieldHealth")};
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    std::vector<Unreal::FWeakObjectPtr> retained_saves;

    struct SavePair
    {
        Unreal::UObject* stock{};
        Unreal::UObject* guest{};
        Unreal::UObject* instance{};
    };

    struct EscrowReference
    {
        std::uintptr_t address{};
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr save;
    };
    std::vector<EscrowReference> retained_escrows;

    template <typename Property>
    auto field(Unreal::UObject* object, const Unreal::TCHAR* name) -> Property*
    {
        auto* property = Unreal::CastField<Property>(object->GetPropertyByName(name));
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    bool live_instance(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    auto resolve_pair(const Lua& lua, SavePair& pair) -> std::string_view
    {
        if (!Unreal::IsInGameThreadRaw())
        {
            return "game_thread_required";
        }
        if (lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
        {
            return "two_save_addresses_required";
        }
        const auto guest_address = lua.get_integer(2);
        const auto stock_address = lua.get_integer(1);
        if (stock_address <= 0 || guest_address <= 0 || stock_address == guest_address)
        {
            return "distinct_save_addresses_required";
        }

        auto* save_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, save_path);
        auto* instance_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, instance_path);
        auto* slot = instance_class ? field<Unreal::FObjectProperty>(instance_class, STR("SaveSlot_Ref")) : nullptr;
        if (!save_class || !slot || slot->GetPropertyClass().Get() != save_class)
        {
            return "stock_save_schema_unavailable";
        }

        // Addresses from Lua are compared against registered objects before any dereference.
        // Only the detached clone may be written, even if the arguments are reversed.
        bool stock_is_slot = false;
        bool guest_is_slot = false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            const auto address = reinterpret_cast<std::uintptr_t>(object);
            if (object->GetClassPrivate() == instance_class && live_instance(object))
            {
                const auto* current = slot->GetObjectPropertyValue(slot->ContainerPtrToValuePtr<void>(object));
                const auto current_address = reinterpret_cast<std::uintptr_t>(current);
                if (current_address == static_cast<std::uintptr_t>(stock_address)) pair.instance = object;
                stock_is_slot |= current_address == static_cast<std::uintptr_t>(stock_address);
                guest_is_slot |= current_address == static_cast<std::uintptr_t>(guest_address);
            }
            else if (address == static_cast<std::uintptr_t>(stock_address) &&
                     object->GetClassPrivate() == save_class && live_instance(object))
            {
                pair.stock = object;
            }
            else if (address == static_cast<std::uintptr_t>(guest_address) &&
                     object->GetClassPrivate() == save_class && live_instance(object))
            {
                pair.guest = object;
            }
            return RC::LoopAction::Continue;
        });
        return pair.stock && pair.guest && stock_is_slot && !guest_is_slot ? "" : "invalid_stock_or_detached_save";
    }

    struct InventoryFields
    {
        Unreal::UScriptStruct* item{};
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        Unreal::FStructProperty* helmet{};
        Unreal::FStructProperty* armor{};
    };

    bool item_struct(Unreal::FProperty* property, const Unreal::UScriptStruct* item)
    {
        auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
        return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == item;
    }

    bool resolve_inventory(Unreal::UObject* save, InventoryFields& fields)
    {
        fields.item = Unreal::UObjectGlobals::StaticFindObject<Unreal::UScriptStruct*>(nullptr, nullptr, item_path);
        fields.backpack = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
        fields.equipment = field<Unreal::FArrayProperty>(save, STR("PlayerEquipment"));
        fields.helmet = field<Unreal::FStructProperty>(save, STR("PlayerHelmet"));
        fields.armor = field<Unreal::FStructProperty>(save, STR("PlayerArmor"));
        return fields.item && fields.backpack && fields.equipment &&
               item_struct(fields.backpack->GetInner(), fields.item) &&
               item_struct(fields.equipment->GetInner(), fields.item) &&
               item_struct(fields.helmet, fields.item) && item_struct(fields.armor, fields.item);
    }

    int result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int reference_detached_save(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if ((arguments != 3 && arguments != 4) || !lua.is_bool(3) ||
            (arguments == 4 && !lua.is_bool(4)))
        {
            return result(lua, false, "expected_save_addresses_and_keep");
        }
        const bool newly_constructed = arguments == 4 && lua.get_bool(4);
        const bool keep = lua.get_bool(3);
        SavePair pair;
        if (const auto error = resolve_pair(lua, pair); !error.empty())
        {
            return result(lua, false, error);
        }
        const auto owned = std::ranges::find_if(retained_saves, [&pair](const auto& save) { return save.Get() == pair.guest; });
        if (!keep)
        {
            if (owned != retained_saves.end())
            {
                pair.guest->ClearRootSet();
                retained_saves.erase(owned);
            }
            return result(lua, true, "detached_save_released");
        }
        if (owned != retained_saves.end() || (pair.guest->IsRootSet() && !newly_constructed))
        {
            return result(lua, false, "detached_save_already_retained");
        }
        retained_saves.emplace_back(pair.guest);
        if (!pair.guest->IsRootSet())
        {
            pair.guest->SetRootSet();
        }
        if (!pair.guest->IsRootSet())
        {
            retained_saves.pop_back();
            return result(lua, false, "detached_save_reference_mismatch");
        }
        return result(lua, true, "detached_save_retained");
    }

    // The reflected UGameInstance array is visited by the engine's garbage
    // collector. Setting the internal root flag alone does not register a root.
    bool update_escrow_reference(Unreal::UObject* instance, Unreal::UObject* save, bool retain)
    {
        auto* references = field<Unreal::FArrayProperty>(instance, STR("ReferencedObjects"));
        auto* entry = references ? Unreal::CastField<Unreal::FObjectProperty>(references->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper objects(references, references->ContainerPtrToValuePtr<void>(instance));
        for (int index = 0; index < objects.Num(); ++index)
        {
            if (entry->GetObjectPropertyValue(objects.GetRawPtr(index)) != save) continue;
            if (retain) return false;
            objects.RemoveValues(index, 1);
            return true;
        }
        if (!retain) return true;
        const auto index = objects.AddValue();
        if (index < 0 || index >= objects.Num()) return false;
        auto* storage = objects.GetRawPtr(index);
        entry->SetObjectPropertyValue(storage, save);
        if (entry->GetObjectPropertyValue(storage) == save) return true;
        objects.RemoveValues(index, 1);
        return false;
    }

    int retain_loot_escrow(const Lua& lua)
    {
        SavePair pair;
        if (const auto error = resolve_pair(lua, pair); !error.empty()) return result(lua, false, error);
        auto* outer = pair.guest->GetOuterPrivate();
        if (!outer || outer->GetPathName() != STR("/Engine/Transient"))
            return result(lua, false, "loot_escrow_not_transient");
        const auto address = reinterpret_cast<std::uintptr_t>(pair.guest);
        if (std::ranges::any_of(retained_escrows, [address](const auto& row) { return row.address == address; }) ||
            !update_escrow_reference(pair.instance, pair.guest, true))
            return result(lua, false, "loot_escrow_reference_unavailable");
        retained_escrows.push_back({address, Unreal::FWeakObjectPtr(pair.instance), Unreal::FWeakObjectPtr(pair.guest)});
        return result(lua, true, "loot_escrow_retained");
    }

    int release_loot_escrow(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return result(lua, false, "expected_retained_save_address");
        const auto address = lua.get_integer();
        if (address <= 0) return result(lua, false, "expected_retained_save_address");
        const auto owned = std::ranges::find_if(retained_escrows, [address](const auto& row) {
            return row.address == static_cast<std::uintptr_t>(address);
        });
        if (owned == retained_escrows.end()) return result(lua, false, "loot_escrow_not_retained");
        auto* instance = owned->instance.Get();
        auto* save = owned->save.Get();
        if (instance && save && !update_escrow_reference(instance, save, false))
            return result(lua, false, "loot_escrow_reference_unavailable");
        retained_escrows.erase(owned);
        return result(lua, true, "loot_escrow_released");
    }

    int copy_guest_save(const Lua& lua)
    {
        SavePair pair;
        if (const auto error = resolve_pair(lua, pair); !error.empty())
        {
            return result(lua, false, error);
        }

        InventoryFields inventory;
        if (!resolve_inventory(pair.stock, inventory))
        {
            return result(lua, false, "stock_inventory_schema_unavailable");
        }
        std::array<Unreal::FProperty*, 5 + vitals_names.size()> properties{
            inventory.backpack, inventory.equipment, inventory.helmet, inventory.armor,
            field<Unreal::FIntProperty>(pair.stock, STR("PlayerEquipIndex"))};
        for (std::size_t i = 0; i < vitals_names.size(); ++i)
        {
            properties[5 + i] = field<Unreal::FDoubleProperty>(pair.stock, vitals_names[i]);
        }
        if (std::ranges::find(properties, nullptr) != properties.end())
        {
            return result(lua, false, "stock_vitals_schema_unavailable");
        }
        for (auto* property : properties)
        {
            property->CopyCompleteValue_InContainer(pair.guest, pair.stock);
            if (!property->Identical_InContainer(pair.guest, pair.stock))
            {
                return result(lua, false, "detached_save_copy_mismatch");
            }
        }
        return result(lua, true, "detached_save_copied");
    }

    using Guid = std::array<std::uint32_t, guid_names.size()>;
    using GuidFields = std::array<Unreal::FIntProperty*, guid_names.size()>;

    Guid read_guid(Unreal::FStructProperty* guid, const GuidFields& fields, const void* item)
    {
        const void* value = guid->ContainerPtrToValuePtr<void>(item);
        Guid parts{};
        for (std::size_t part = 0; part < parts.size(); ++part)
        {
            parts[part] = static_cast<std::uint32_t>(fields[part]->GetPropertyValueInContainer(value));
        }
        return parts;
    }

    std::string prove_item(Unreal::FProperty* property, Unreal::FIntProperty* ammo,
                           const void* source, void* destination, const Unreal::UObject* asset,
                           const Guid& guid, std::string_view origin, int index, int count)
    {
        if (source == destination || !property->Identical(source, destination))
        {
            return {};
        }

        const auto original = ammo->GetPropertyValueInContainer(source);
        const auto adjusted = original == std::numeric_limits<std::int32_t>::max() ? original - 1 : original + 1;
        ammo->SetPropertyValueInContainer(destination, adjusted);
        const bool independent = ammo->GetPropertyValueInContainer(source) == original &&
                                 ammo->GetPropertyValueInContainer(destination) == adjusted;
        ammo->SetPropertyValueInContainer(destination, original);
        if (!independent || !property->Identical(source, destination))
        {
            return {};
        }
        return std::format(
            "origin={} index={} count={} class={} guid={:08X}{:08X}{:08X}{:08X} ammo={} test_ammo={} restored=1",
            origin, index, count, RC::to_string(asset->GetPathName()), guid[0], guid[1], guid[2], guid[3], original, adjusted);
    }

    class ScratchSlots
    {
    public:
        explicit ScratchSlots(Unreal::FScriptArrayHelper& array) : array(array), first(array.AddValues(2)) {}
        ~ScratchSlots() { array.RemoveValues(first, 2); }
        ScratchSlots(const ScratchSlots&) = delete;
        ScratchSlots& operator=(const ScratchSlots&) = delete;

        void* source() { return array.GetRawPtr(first); }
        void* destination() { return array.GetRawPtr(first + 1); }

    private:
        Unreal::FScriptArrayHelper& array;
        int first;
    };

    int probe_inventory(const Lua& lua, bool use_fixture)
    {
        SavePair pair;
        if (const auto error = resolve_pair(lua, pair); !error.empty())
        {
            return result(lua, false, error);
        }
        InventoryFields inventory;
        if (!resolve_inventory(pair.stock, inventory))
        {
            return result(lua, false, "stock_inventory_schema_unavailable");
        }

        auto* item_class = field<Unreal::FClassProperty>(inventory.item, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        auto* ammo = field<Unreal::FIntProperty>(inventory.item, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        auto* guid = field<Unreal::FStructProperty>(inventory.item, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        auto* guid_struct = Unreal::UObjectGlobals::StaticFindObject<Unreal::UScriptStruct*>(nullptr, nullptr, guid_path);
        if (!item_class || !ammo || !guid_struct || !item_struct(guid, guid_struct))
        {
            return result(lua, false, "stock_item_schema_unavailable");
        }
        GuidFields guid_fields{};
        for (std::size_t i = 0; i < guid_names.size(); ++i)
        {
            guid_fields[i] = field<Unreal::FIntProperty>(guid_struct, guid_names[i]);
        }
        if (std::ranges::find(guid_fields, nullptr) != guid_fields.end())
        {
            return result(lua, false, "stock_guid_schema_unavailable");
        }

        const std::array arrays{inventory.backpack, inventory.equipment};
        for (auto* array : arrays)
        {
            Unreal::FScriptArrayHelper stock(array, array->ContainerPtrToValuePtr<void>(pair.stock));
            Unreal::FScriptArrayHelper guest(array, array->ContainerPtrToValuePtr<void>(pair.guest));
            if (stock.Num() != guest.Num() || (stock.Num() > 0 && stock.GetRawPtr() == guest.GetRawPtr()) ||
                !array->Identical_InContainer(pair.stock, pair.guest))
            {
                return result(lua, false, "inventory_arrays_not_independent_copies");
            }
        }

        for (auto* array : arrays)
        {
            Unreal::FScriptArrayHelper stock(array, array->ContainerPtrToValuePtr<void>(pair.stock));
            Unreal::FScriptArrayHelper guest(array, array->ContainerPtrToValuePtr<void>(pair.guest));
            for (int index = 0; index < stock.Num(); ++index)
            {
                const void* source = stock.GetRawPtr(index);
                void* destination = guest.GetRawPtr(index);
                const auto* asset = item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(source));
                if (!asset)
                {
                    continue;
                }
                const auto parts = read_guid(guid, guid_fields, source);
                if (std::ranges::all_of(parts, [](auto part) { return part == 0; }))
                {
                    continue;
                }
                if (use_fixture)
                {
                    return result(lua, false, "existing_stock_item_available");
                }
                const auto proof = prove_item(array->GetInner(), ammo, source, destination, asset,
                                             parts, "existing_stock_item", index, stock.Num());
                if (proof.empty())
                {
                    return result(lua, false, "stock_item_changed_or_restore_mismatched");
                }
                return result(lua, true, std::format("array={} {}",
                    array == inventory.backpack ? "PlayerBackpack" : "PlayerEquipment", proof));
            }
        }
        if (!use_fixture)
        {
            return result(lua, false, "no_populated_native_item_with_guid");
        }

        auto* bandage = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, bandage_path);
        auto* item_base = item_class->GetMetaClass().Get();
        if (!bandage || !item_base || !bandage->IsChildOf(item_base))
        {
            return result(lua, false, "fixture_bandage_class_unavailable");
        }

        auto* backpack = inventory.backpack;
        Unreal::FScriptArrayHelper guest(backpack, backpack->ContainerPtrToValuePtr<void>(pair.guest));
        const auto before = guest.Num();
        std::string proof;
        {
            // The fixture exists only in two constructed slots of the detached guest array.
            ScratchSlots slots(guest);
            constexpr Guid fixture_guid{0x5346434FU, 0x4F504C41U, 0x42464958U, 0x54555245U};
            auto* source = slots.source();
            auto* destination = slots.destination();
            item_class->SetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(source), bandage);
            ammo->SetPropertyValueInContainer(source, 1);
            auto* guid_value = guid->ContainerPtrToValuePtr<void>(source);
            for (std::size_t part = 0; part < fixture_guid.size(); ++part)
            {
                guid_fields[part]->SetPropertyValueInContainer(guid_value, static_cast<std::int32_t>(fixture_guid[part]));
            }
            const auto* asset = item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(source));
            const auto stored_guid = read_guid(guid, guid_fields, source);
            if (asset == bandage && stored_guid == fixture_guid)
            {
                backpack->GetInner()->CopyCompleteValue(destination, source);
                proof = prove_item(backpack->GetInner(), ammo, source, destination, asset,
                                  stored_guid, "synthetic_fixture", before + 1, guest.Num());
            }
        }
        if (guest.Num() != before || !backpack->Identical_InContainer(pair.stock, pair.guest))
        {
            return result(lua, false, "fixture_cleanup_mismatched");
        }
        if (proof.empty())
        {
            return result(lua, false, "fixture_copy_mismatched");
        }
        return result(lua, true, std::format("array=PlayerBackpack {} cleaned_count={}", proof, before));
    }

    int probe_stock_inventory(const Lua& lua) { return probe_inventory(lua, false); }
    int probe_fixture_inventory(const Lua& lua) { return probe_inventory(lua, true); }

    template <int (*Operation)(const Lua&)>
    int run(const Lua& lua)
    {
        try
        {
            return Operation(lua);
        }
        catch (const std::exception&)
        {
            return result(lua, false, "native_save_operation_failed");
        }
    }
}

void silverfish::register_native_save_copy_lua(RC::LuaMadeSimple::Lua& lua)
{
    lua.register_function("SilverfishReferenceNativeDetachedSave", run<reference_detached_save>);
    lua.register_function("SilverfishRetainNativeLootEscrow", run<retain_loot_escrow>);
    lua.register_function("SilverfishReleaseNativeLootEscrow", run<release_loot_escrow>);
    lua.register_function("SilverfishCopyNativeGuestSave", run<copy_guest_save>);
    lua.register_function("SilverfishProbeNativeInventory", run<probe_stock_inventory>);
    lua.register_function("SilverfishProbeNativeInventoryFixture", run<probe_fixture_inventory>);
}
