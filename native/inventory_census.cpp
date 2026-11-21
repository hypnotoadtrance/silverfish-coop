#include "inventory_census.hpp"
#include "drop_item_class.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <iterator>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>

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
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto item_base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto item_struct_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_struct_path = STR("/Script/CoreUObject.Guid");
    constexpr auto stash_struct_path = STR("/Game/Classes/Libraries/Structs/Stash_Struct.Stash_Struct");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_array_rows = 4096; // Matches the frozen planner and native guest inventory scanner.
    constexpr std::size_t max_class_path = 512;

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
        std::size_t length{};
        return getenv_s(&length, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
               length == value.size() && value[0] == '1';
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    Unreal::UObject* isolated_snapshot(Address address)
    {
        auto* save_type = find<Unreal::UClass>(save_path);
        auto* instance = find<Unreal::UClass>(instance_path);
        auto* pawn = find<Unreal::UClass>(pawn_path);
        auto* canonical = field<Unreal::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* assigned = field<Unreal::FObjectProperty>(pawn, STR("Save"));
        if (!save_type || !canonical || !assigned || canonical->GetPropertyClass().Get() != save_type ||
            assigned->GetPropertyClass().Get() != save_type) return nullptr;

        bool attached = false;
        Unreal::UObject* snapshot = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (!live(object)) return RC::LoopAction::Continue;
            auto* type = object->GetClassPrivate();
            auto* attachment = type == instance ? canonical : type == pawn ? assigned : nullptr;
            if (attachment && reinterpret_cast<Address>(attachment->GetObjectPropertyValue(
                    attachment->ContainerPtrToValuePtr<void>(object))) == address)
            {
                attached = true;
                return RC::LoopAction::Break;
            }
            if (type == save_type && reinterpret_cast<Address>(object) == address) snapshot = object;
            return RC::LoopAction::Continue;
        });
        auto* outer = snapshot ? snapshot->GetOuterPrivate() : nullptr;
        return !attached && outer && outer->GetPathName() == STR("/Engine/Transient") ? snapshot : nullptr;
    }

    struct Inventory
    {
        Unreal::UClass* base = find<Unreal::UClass>(item_base_path);
        Unreal::UScriptStruct* type = find<Unreal::UScriptStruct>(item_struct_path);
        Unreal::FClassProperty* item_class = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        Unreal::FIntProperty* ammo = field<Unreal::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        Unreal::FBoolProperty* never_drop = field<Unreal::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        Unreal::FStructProperty* guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<Unreal::FIntProperty*, 4> guid_words{};
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        Unreal::FStructProperty* armor{};
        Unreal::FStructProperty* helmet{};

        bool same_type(Unreal::FProperty* property) const
        {
            auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
            return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == type;
        }

        bool bind(Unreal::UObject* snapshot)
        {
            auto* guid_type = find<Unreal::UScriptStruct>(guid_struct_path);
            backpack = field<Unreal::FArrayProperty>(snapshot, STR("PlayerBackpack"));
            equipment = field<Unreal::FArrayProperty>(snapshot, STR("PlayerEquipment"));
            armor = field<Unreal::FStructProperty>(snapshot, STR("PlayerArmor"));
            helmet = field<Unreal::FStructProperty>(snapshot, STR("PlayerHelmet"));
            if (!base || !type || !item_class || item_class->GetMetaClass().Get() != base || !ammo ||
                !never_drop || !guid || !guid_type || guid->GetStruct().Get() != guid_type || !backpack || !equipment ||
                !same_type(backpack->GetInner()) || !same_type(equipment->GetInner()) ||
                !same_type(armor) || !same_type(helmet)) return false;

            for (std::size_t index = 0; index < guid_words.size(); ++index)
            {
                auto* part = field<Unreal::FIntProperty>(guid_type, guid_names[index]);
                if (!part || part->GetOffset_Internal() != static_cast<int>(index * sizeof(std::uint32_t))) return false;
                guid_words[index] = part;
            }
            return true;
        }

        Guid read_guid(const void* data) const
        {
            Guid identity{};
            for (std::size_t index = 0; index < identity.size(); ++index)
                identity[index] = static_cast<std::uint32_t>(guid_words[index]->GetPropertyValueInContainer(data));
            return identity;
        }

        Guid identity(const void* item) const { return read_guid(guid->ContainerPtrToValuePtr<void>(item)); }
    };

    bool asset_path(std::string_view path)
    {
        if (!path.starts_with("/Game/") || !path.ends_with("_C") || path.size() > max_class_path) return false;
        const auto dot = path.find('.', 6);
        if (dot == std::string_view::npos || dot == 6 || dot + 3 >= path.size()) return false;
        const auto ordinary = [](char ch) {
            return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') ||
                   (ch >= '0' && ch <= '9') || ch == '_' || ch == '%' || ch == '-';
        };
        return std::ranges::all_of(path.substr(6, dot - 6), [&](char ch) { return ordinary(ch) || ch == '/'; }) &&
               std::ranges::all_of(path.substr(dot + 1), ordinary);
    }

    struct ItemClass
    {
        std::string path;
        bool stackable{};
        bool important{};
        bool cannot_keep{};
        bool drop_class{};

        bool read(Unreal::UClass* type)
        {
            auto* defaults = type->GetClassDefaultObject().Get();
            if (!defaults || defaults->GetClassPrivate() != type || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
                defaults->IsUnreachable()) return false;
            auto* stacks = field<Unreal::FBoolProperty>(defaults, STR("Stacks"));
            // The stock important-only filter and stock keep widget read these class defaults.
            auto* importance = field<Unreal::FBoolProperty>(defaults, STR("Important"));
            auto* retention = field<Unreal::FBoolProperty>(defaults, STR("CanNotKeep_AfterDeath"));
            path = RC::to_string(type->GetPathName());
            if (!stacks || !importance || !retention || !asset_path(path)) return false;
            stackable = stacks->GetPropertyValueInContainer(defaults);
            important = importance->GetPropertyValueInContainer(defaults);
            cannot_keep = retention->GetPropertyValueInContainer(defaults);
            drop_class = silverfish::drop_item_class::stacks(type).has_value();
            return true;
        }
    };

    struct Census
    {
        const Inventory& inventory;
        std::unordered_map<Unreal::UClass*, ItemClass> classes;
        std::set<Guid> seen;
        std::string lines;

        std::string_view row(const void* value, std::string_view source, int index)
        {
            const auto identity = inventory.identity(value);
            auto* object = inventory.item_class->GetObjectPropertyValue(inventory.item_class->ContainerPtrToValuePtr<void>(value));
            if (!object && identity == Guid{}) return {}; // Blank stock equipment and wearable slots.
            auto* type = Unreal::Cast<Unreal::UClass>(object);
            if (!live(type) || !type->IsChildOf(inventory.base) || identity == Guid{})
                return "native_inventory_census_invalid_item";
            if (!seen.insert(identity).second) return "native_inventory_census_duplicate_guid";
            auto [entry, inserted] = classes.try_emplace(type);
            auto& stock = entry->second;
            if (inserted && !stock.read(type)) return "native_inventory_census_item_class_unqualified";
            const std::int32_t quantity = stock.stackable ? inventory.ammo->GetPropertyValueInContainer(value) : 1;
            if (quantity <= 0) return "native_inventory_census_invalid_stack_quantity";

            const bool droppable = source == "backpack" && stock.drop_class &&
                !inventory.never_drop->GetPropertyValueInContainer(value);
            std::format_to(std::back_inserter(lines), "{:08X}{:08X}{:08X}{:08X}\t{}\t{}\t{}\t{}\t{:d}\t{:d}\t{:d}\t{:d}\n",
                           identity[0], identity[1], identity[2], identity[3], source, index, stock.path, quantity,
                           stock.stackable, stock.important, stock.cannot_keep, droppable);
            return {};
        }

        std::string_view array(Unreal::FArrayProperty* property, const void* snapshot, std::string_view source)
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(snapshot));
            if (rows.Num() < 0 || rows.Num() > max_array_rows) return "native_inventory_census_array_out_of_bounds";
            for (int index = 0; index < rows.Num(); ++index)
            {
                if (const auto error = row(rows.GetRawPtr(index), source, index); !error.empty()) return error;
            }
            return {};
        }
    };

    int read_census(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return result(lua, false, "native_inventory_census_scope_unavailable");
        const auto input = lua.get_integer();
        auto* snapshot = input > 0 ? isolated_snapshot(static_cast<Address>(input)) : nullptr;
        if (!snapshot) return result(lua, false, "native_inventory_census_exact_isolated_save_required");
        Inventory inventory;
        if (!inventory.bind(snapshot)) return result(lua, false, "native_inventory_census_stock_schema_unavailable");

        Census census{inventory};
        auto error = census.array(inventory.backpack, snapshot, "backpack");
        if (error.empty()) error = census.array(inventory.equipment, snapshot, "equipment");
        if (error.empty()) error = census.row(inventory.armor->ContainerPtrToValuePtr<void>(snapshot), "armor", 0);
        if (error.empty()) error = census.row(inventory.helmet->ContainerPtrToValuePtr<void>(snapshot), "helmet", 0);
        if (!error.empty()) return result(lua, false, error);

        result(lua, true, "complete");
        lua.set_string(census.lines);
        lua.set_integer(static_cast<std::int64_t>(census.seen.size()));
        return 4;
    }

    Guid stash_identity(std::string_view hex)
    {
        if (hex.size() != 32) return {};
        Guid words{};
        for (auto& word : words)
        {
            const auto [end, error] = std::from_chars(hex.data(), hex.data() + 8, word, 16);
            if (error != std::errc{} || end != hex.data() + 8) return {};
            hex.remove_prefix(8);
        }
        return words;
    }

    int read_stash(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_bool(2) || !lua.is_string(3))
            return result(lua, false, "native_stash_census_scope_unavailable");
        const auto input = lua.get_integer();
        const bool combined = lua.get_bool();
        const auto target = stash_identity(lua.get_string());
        auto* snapshot = input > 0 ? isolated_snapshot(static_cast<Address>(input)) : nullptr;
        if (!snapshot || target == Guid{}) return result(lua, false, "native_stash_census_exact_save_and_site_required");
        Inventory inventory;
        auto* storage = field<Unreal::FArrayProperty>(snapshot, STR("Stashes"));
        auto* entry = storage ? Unreal::CastField<Unreal::FStructProperty>(storage->GetInner()) : nullptr;
        auto* type = find<Unreal::UScriptStruct>(stash_struct_path);
        auto* guid = field<Unreal::FStructProperty>(type, STR("Stash_ID_6_90081F6E44D7BFA5FA8C15887774759B"));
        auto* contents = field<Unreal::FArrayProperty>(type, STR("Contents_9_134977CA456AB05C7AE11DAAF3F758BE"));
        if (!inventory.bind(snapshot) || !type || !entry || entry->GetStruct().Get() != type ||
            !guid || guid->GetStruct().Get() != find<Unreal::UScriptStruct>(guid_struct_path) ||
            !contents || !inventory.same_type(contents->GetInner()))
            return result(lua, false, "native_stash_census_schema_unavailable");

        Unreal::FScriptArrayHelper stashes(storage, storage->ContainerPtrToValuePtr<void>(snapshot));
        if (stashes.Num() < 0 || stashes.Num() > max_array_rows)
            return result(lua, false, "native_stash_census_array_out_of_bounds");
        const void* selected = nullptr;
        if (combined && stashes.Num() > 0)
        {
            selected = stashes.GetRawPtr(0);
        }
        else if (!combined)
        {
            for (int index = 0; index < stashes.Num(); ++index)
            {
                const auto* row = stashes.GetRawPtr(index);
                if (inventory.read_guid(guid->ContainerPtrToValuePtr<void>(row)) != target) continue;
                if (selected) return result(lua, false, "native_stash_census_duplicate_site");
                selected = row;
            }
        }
        Census census{inventory};
        if (selected)
        {
            if (const auto error = census.array(contents, selected, "stash"); !error.empty())
                return result(lua, false, error);
        }
        result(lua, true, "complete");
        lua.set_string(census.lines);
        lua.set_integer(static_cast<std::int64_t>(census.seen.size()));
        return 4;
    }
}

namespace silverfish::inventory_census
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeInventoryCensus", read_census);
        lua.register_function("SilverfishReadNativeStashCensus", read_stash);
    }
}
