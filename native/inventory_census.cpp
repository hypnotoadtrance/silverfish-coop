#include "inventory_census.hpp"
#include "drop_item_class.hpp"
#include "guest_item.hpp"
#include "loot_escrow.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <iterator>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

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
        Unreal::FIntProperty* value = field<Unreal::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        Unreal::FDoubleProperty* weight = field<Unreal::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
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

        void write_guid(void* data, const Guid& identity) const
        {
            for (std::size_t index = 0; index < identity.size(); ++index)
                guid_words[index]->SetPropertyValueInContainer(data, static_cast<std::int32_t>(identity[index]));
        }

        Unreal::UObject* klass(const void* data) const
        {
            return item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(data));
        }
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
        bool include_stock_ammo{};
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
            const auto ammo = inventory.ammo->GetPropertyValueInContainer(value);
            const std::int32_t quantity = stock.stackable ? ammo : 1;
            if (quantity <= 0) return "native_inventory_census_invalid_stack_quantity";

            const bool droppable = stock.drop_class && (source != "equipment" || (index < 8 && !stock.stackable)) &&
                !inventory.never_drop->GetPropertyValueInContainer(value);
            std::format_to(std::back_inserter(lines), "{:08X}{:08X}{:08X}{:08X}\t{}\t{}\t{}\t{}\t{:d}\t{:d}\t{:d}\t{:d}",
                           identity[0], identity[1], identity[2], identity[3], source, index, stock.path, quantity,
                           stock.stackable, stock.important, stock.cannot_keep, droppable);
            if (include_stock_ammo)
            {
                if (!inventory.value || !inventory.weight) return "native_stash_census_display_fields_unavailable";
                const double weight = inventory.weight->GetPropertyValueInContainer(value);
                if (!std::isfinite(weight) || weight < 0 || weight > 100000) return "native_stash_census_invalid_item_weight";
                std::format_to(std::back_inserter(lines), "\t{}\t{}\t{}\t{:d}", ammo, weight,
                               inventory.value->GetPropertyValueInContainer(value),
                               inventory.never_drop->GetPropertyValueInContainer(value));
            }
            lines.push_back('\n');
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

    int emit_census(const Lua& lua, Unreal::UObject* snapshot)
    {
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

    int read_census(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return result(lua, false, "native_inventory_census_scope_unavailable");
        const auto input = lua.get_integer();
        auto* snapshot = input > 0 ? isolated_snapshot(static_cast<Address>(input)) : nullptr;
        if (!snapshot) return result(lua, false, "native_inventory_census_exact_isolated_save_required");
        return emit_census(lua, snapshot);
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

    struct StashStorage
    {
        Unreal::FArrayProperty* array{};
        Unreal::FStructProperty* id{};
        Unreal::FArrayProperty* contents{};

        bool bind(Unreal::UObject* save, const Inventory& inventory)
        {
            array = field<Unreal::FArrayProperty>(save, STR("Stashes"));
            auto* entry = array ? Unreal::CastField<Unreal::FStructProperty>(array->GetInner()) : nullptr;
            auto* type = find<Unreal::UScriptStruct>(stash_struct_path);
            id = field<Unreal::FStructProperty>(type, STR("Stash_ID_6_90081F6E44D7BFA5FA8C15887774759B"));
            contents = field<Unreal::FArrayProperty>(type, STR("Contents_9_134977CA456AB05C7AE11DAAF3F758BE"));
            return type && entry && entry->GetStruct().Get() == type &&
                id && id->GetStruct().Get() == find<Unreal::UScriptStruct>(guid_struct_path) &&
                contents && inventory.same_type(contents->GetInner());
        }

        int select(Unreal::FScriptArrayHelper& stashes, const Inventory& inventory,
                   bool combined, const Guid& target) const
        {
            if (combined) return stashes.Num() > 0 ? 0 : -1;
            int selected = -1;
            for (int index = 0; index < stashes.Num(); ++index)
            {
                if (inventory.read_guid(id->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(index))) != target) continue;
                if (selected >= 0) return -2;
                selected = index;
            }
            return selected;
        }
    };

    Unreal::UObject* authoritative_save(Address address, bool combined, Unreal::UObject** context = nullptr);

    int stash_census(const Lua& lua, bool read_live)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_bool(2) || !lua.is_string(3))
            return result(lua, false, "native_stash_census_scope_unavailable");
        const auto input = lua.get_integer();
        const bool combined = lua.get_bool();
        const auto target = stash_identity(lua.get_string());
        if (input <= 0) return result(lua, false, "native_stash_census_exact_save_and_site_required");
        const auto address = static_cast<Address>(input);
        auto* snapshot = read_live ? authoritative_save(address, combined) : isolated_snapshot(address);
        if (!snapshot || target == Guid{}) return result(lua, false, "native_stash_census_exact_save_and_site_required");
        Inventory inventory;
        StashStorage storage;
        if (!inventory.bind(snapshot) || !storage.bind(snapshot, inventory))
            return result(lua, false, "native_stash_census_schema_unavailable");

        Unreal::FScriptArrayHelper stashes(storage.array, storage.array->ContainerPtrToValuePtr<void>(snapshot));
        if (stashes.Num() < 0 || stashes.Num() > max_array_rows)
            return result(lua, false, "native_stash_census_array_out_of_bounds");
        const int selected = storage.select(stashes, inventory, combined, target);
        if (selected == -2) return result(lua, false, "native_stash_census_duplicate_site");
        Census census{inventory, read_live};
        if (selected >= 0)
        {
            if (const auto error = census.array(storage.contents, stashes.GetRawPtr(selected), "stash"); !error.empty())
                return result(lua, false, error);
        }
        result(lua, true, "complete");
        lua.set_string(census.lines);
        lua.set_integer(static_cast<std::int64_t>(census.seen.size()));
        return 4;
    }

    int read_stash(const Lua& lua) { return stash_census(lua, false); }
    int read_live_stash(const Lua& lua) { return stash_census(lua, true); }

    Unreal::UObject* object_field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    Unreal::UObject* live_exact(Address address, Unreal::UClass* type)
    {
        if (!address || !type) return nullptr;
        Unreal::UObject* matched = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (reinterpret_cast<Address>(candidate) != address) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == type) matched = candidate;
            return RC::LoopAction::Break;
        });
        return matched;
    }

    Unreal::UObject* live_pawn_save(Unreal::UObject* pawn, Unreal::UObject*& stock)
    {
        auto* role = field<Unreal::FByteProperty>(pawn, STR("Role"));
        if (!role || role->GetPropertyValueInContainer(pawn) != 3) return nullptr;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        auto* instance = object_field(world, STR("OwningGameInstance"));
        stock = object_field(instance, STR("SaveSlot_Ref"));
        auto* save = object_field(pawn, STR("Save"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* save_type = find<Unreal::UClass>(save_path);
        if (!live(driver) || object_field(driver, STR("World")) != world ||
            object_field(driver, STR("ServerConnection")) || !live(save) || !live(stock) ||
            save->GetClassPrivate() != save_type || stock->GetClassPrivate() != save_type) return nullptr;
        return save;
    }

    Unreal::UObject* authoritative_save(Address address, bool combined, Unreal::UObject** context)
    {
        auto* pawn = live_exact(address, find<Unreal::UClass>(pawn_path));
        auto* dead = field<Unreal::FBoolProperty>(pawn, STR("Dead"));
        if (!dead || dead->GetPropertyValueInContainer(pawn)) return nullptr;
        Unreal::UObject* stock{};
        auto* save = live_pawn_save(pawn, stock);
        auto* setting = save ? field<Unreal::FBoolProperty>(stock, STR("CombineStashes")) : nullptr;
        if (!setting || setting->GetPropertyValueInContainer(stock) != combined) return nullptr;
        if (context)
        {
            if (save == stock) return nullptr; // A partial transfer belongs to the detached guest inventory.
            *context = pawn;
        }
        return save;
    }

    int read_live_census(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_integer(2)) return result(lua, false, "native_live_inventory_scope_unavailable");
        const auto address = static_cast<Address>(lua.get_integer());
        const auto owner_save = static_cast<Address>(lua.get_integer());
        auto* pawn = live_exact(address, find<Unreal::UClass>(pawn_path));
        Unreal::UObject* stock{};
        auto* save = live_pawn_save(pawn, stock);
        if (!save || reinterpret_cast<Address>(save) != owner_save)
            return result(lua, false, "native_live_inventory_owner_unavailable");
        return emit_census(lua, save);
    }

    int locate(const Inventory& inventory, Unreal::FScriptArrayHelper& rows, const Guid& id, int& found)
    {
        if (rows.Num() < 0 || rows.Num() > max_array_rows) return -1;
        int count = 0;
        for (int index = 0; index < rows.Num(); ++index)
        {
            if (inventory.identity(rows.GetRawPtr(index)) != id) continue;
            found = index;
            ++count;
        }
        return count;
    }

    struct ItemDelete
    {
        Unreal::FStructProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };

    bool same_stack_metadata(const Inventory& inventory, const void* source, const void* destination)
    {
        auto* tag = field<Unreal::FNameProperty>(inventory.type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        auto* data = field<Unreal::FStructProperty>(inventory.type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        return tag && data && tag->Identical(tag->ContainerPtrToValuePtr<void>(source),
                   tag->ContainerPtrToValuePtr<void>(destination)) &&
            data->Identical(data->ContainerPtrToValuePtr<void>(source), data->ContainerPtrToValuePtr<void>(destination)) &&
            inventory.never_drop->GetPropertyValueInContainer(source) ==
                inventory.never_drop->GetPropertyValueInContainer(destination);
    }

    struct StackTotals
    {
        int unit_value{};
        double unit_weight{};

        bool bind(Unreal::UClass* type)
        {
            auto* defaults = type->GetClassDefaultObject().Get();
            auto* value = field<Unreal::FIntProperty>(defaults, STR("Value"));
            auto* weight = field<Unreal::FDoubleProperty>(defaults, STR("Weight"));
            if (!value || !weight) return false;
            unit_value = value->GetPropertyValueInContainer(defaults);
            unit_weight = weight->GetPropertyValueInContainer(defaults);
            return unit_value >= 0 && std::isfinite(unit_weight) && unit_weight >= 0;
        }

        bool valid(std::int64_t count) const
        {
            return count > 0 && count <= std::numeric_limits<int>::max() &&
                count * unit_value <= std::numeric_limits<int>::max() && weight(count) <= 100000;
        }

        double weight(std::int64_t count) const { return static_cast<double>(count) * unit_weight; }

        void write(const Inventory& inventory, void* row, int count) const
        {
            inventory.ammo->SetPropertyValueInContainer(row, count);
            inventory.weight->SetPropertyValueInContainer(row, weight(count));
            inventory.value->SetPropertyValueInContainer(row, count * unit_value);
        }
    };

    struct StashMerge
    {
        Guid guid{};
        int index{-1};
        int before{};
        int after{};
        std::unique_ptr<void, ItemDelete> original, staged;

        explicit StashMerge(Unreal::FStructProperty* property) : original(nullptr, {property}), staged(nullptr, {property}) {}
    };

    template <bool Live, bool Reorder = false, bool Split = false, bool Merge = false>
    int move_stash(const Lua& lua)
    {
        static_assert((!Reorder && !Split) || (Live && !(Reorder && Split)));
        static_assert(!Merge || Split);
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != (Split ? 9 : Reorder ? 7 : 6) ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_bool(3) ||
            !lua.is_string(4) || !lua.is_string(5) || !lua.is_bool(6) || ((Reorder || Split) && !lua.is_string(7)) ||
            (Split && (!lua.is_integer(8) || !lua.is_integer(9))))
            return result(lua, false, "native_stash_move_unavailable");
        const auto pawn = static_cast<Address>(lua.get_integer());
        const auto frame = static_cast<Address>(lua.get_integer());
        const bool combined = lua.get_bool();
        const auto site = stash_identity(lua.get_string());
        const auto id = stash_identity(lua.get_string());
        const bool deposit = lua.get_bool();
        Guid created{};
        int take{}, expected{};
        if constexpr (Split)
        {
            created = stash_identity(lua.get_string());
            const auto quantity = lua.get_integer();
            const auto source = lua.get_integer();
            if (created == Guid{} || id == created || quantity < 1 || quantity >= source ||
                source > std::numeric_limits<int>::max())
                return result(lua, false, "native_stash_move_split_selection");
            take = static_cast<int>(quantity);
            expected = static_cast<int>(source);
        }
        Unreal::UObject* context{};
        auto* save = authoritative_save(pawn, combined, Split ? &context : nullptr);
        auto* snapshot = Live ? nullptr : isolated_snapshot(frame);
        if (!save || (Live ? frame != 0 : !snapshot) || site == Guid{} || id == Guid{} || (Reorder && deposit))
            return result(lua, false, "native_stash_move_exact_save_and_site_required");
        Inventory inventory;
        StashStorage storage;
        if (!inventory.bind(save) || !storage.bind(save, inventory))
            return result(lua, false, "native_stash_move_schema_unavailable");
        if constexpr (!Live)
        {
            const std::array<Unreal::FProperty*, 5> stores{
                inventory.backpack, inventory.equipment, inventory.armor, inventory.helmet, storage.array};
            for (auto* property : stores)
            {
                if (!property->Identical(property->ContainerPtrToValuePtr<void>(save),
                                         property->ContainerPtrToValuePtr<void>(snapshot)))
                    return result(lua, false, "native_stash_move_stale_frame");
            }
        }
        if (inventory.identity(inventory.armor->ContainerPtrToValuePtr<void>(save)) == id ||
            inventory.identity(inventory.helmet->ContainerPtrToValuePtr<void>(save)) == id)
            return result(lua, false, "native_stash_move_equipped_item");
        if constexpr (Split)
            if (inventory.identity(inventory.armor->ContainerPtrToValuePtr<void>(save)) == created ||
                inventory.identity(inventory.helmet->ContainerPtrToValuePtr<void>(save)) == created)
                return result(lua, false, "native_stash_move_split_duplicate");

        Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(save));
        Unreal::FScriptArrayHelper equipment(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(save));
        Unreal::FScriptArrayHelper stashes(storage.array, storage.array->ContainerPtrToValuePtr<void>(save));
        if (stashes.Num() < 0 || stashes.Num() > max_array_rows) return result(lua, false, "native_stash_move_bounds");
        int selected = storage.select(stashes, inventory, combined, site);
        if (selected == -2 || (selected < 0 && stashes.Num() == max_array_rows))
            return result(lua, false, "native_stash_move_site_unavailable");

        int pack_index = -1, stash_index = -1, ignored = -1;
        const int pack_count = locate(inventory, backpack, id, pack_index);
        if (pack_count != (deposit ? 1 : 0) || locate(inventory, equipment, id, ignored) != 0)
            return result(lua, false, "native_stash_move_backpack_identity");
        if constexpr (Split)
            if (locate(inventory, backpack, created, ignored) != 0 || locate(inventory, equipment, created, ignored) != 0)
                return result(lua, false, "native_stash_move_split_duplicate");
        for (int index = 0; index < stashes.Num(); ++index)
        {
            Unreal::FScriptArrayHelper rows(storage.contents,
                storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(index)));
            int found = -1;
            if (locate(inventory, rows, id, found) != (!deposit && index == selected ? 1 : 0))
                return result(lua, false, "native_stash_move_stored_identity");
            if constexpr (Split)
                if (locate(inventory, rows, created, ignored) != 0)
                    return result(lua, false, "native_stash_move_split_duplicate");
            if (index == selected) stash_index = found;
        }
        if ((!deposit && stash_index < 0) || (!Reorder && !Split && !deposit && backpack.Num() == max_array_rows))
            return result(lua, false, "native_stash_move_source_or_capacity");

        const void* source_row = deposit ? backpack.GetRawPtr(pack_index) : nullptr;
        if (selected >= 0)
        {
            Unreal::FScriptArrayHelper rows(storage.contents,
                storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(selected)));
            if (!Split && deposit && rows.Num() == max_array_rows)
                return result(lua, false, "native_stash_move_source_or_capacity");
            if (!deposit) source_row = rows.GetRawPtr(stash_index);
        }
        auto* item_type = Unreal::Cast<Unreal::UClass>(inventory.klass(source_row));
        ItemClass item;
        if (!live(item_type) || !item_type->IsChildOf(inventory.base) || !item.read(item_type))
            return result(lua, false, "native_stash_move_item_unqualified");
        const auto quantity = item.stackable ? inventory.ammo->GetPropertyValueInContainer(source_row) : 1;
        if (quantity < 1) return result(lua, false, "native_stash_move_quantity_unqualified");

        auto* property = Unreal::CastField<Unreal::FStructProperty>(inventory.backpack->GetInner());
        std::unique_ptr<void, ItemDelete> portion{nullptr, {property}}, remainder{nullptr, {property}},
            original_copy{nullptr, {property}};
        StashMerge merge(property);
        if constexpr (Split)
        {
            if (!item.stackable || quantity != expected || !inventory.weight || !inventory.value)
                return result(lua, false, "native_stash_move_split_stale_amount");
            if (selected >= 0)
            {
                Unreal::FScriptArrayHelper rows(storage.contents,
                    storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(selected)));
                auto& destination = deposit ? rows : backpack;
                for (int index = 0; index < destination.Num(); ++index)
                {
                    const auto* target = destination.GetRawPtr(index);
                    if (inventory.klass(target) != item_type) continue;
                    if constexpr (!Merge) return result(lua, false, "native_stash_move_split_destination_stack");
                    merge.guid = inventory.identity(target);
                    merge.index = index;
                    merge.before = inventory.ammo->GetPropertyValueInContainer(target);
                    auto* weapon = find<Unreal::UClass>(STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"));
                    if (merge.guid == Guid{} || merge.guid == id || merge.guid == created ||
                        merge.before <= 0 || (weapon && item_type->IsChildOf(weapon)) ||
                        !silverfish::drop_item_class::stacks(item_type).value_or(false) ||
                        !same_stack_metadata(inventory, source_row, target))
                        return result(lua, false, "native_stash_move_split_destination_stack");
                    StackTotals totals;
                    const auto combined_count = static_cast<std::int64_t>(merge.before) + take;
                    if (!totals.bind(item_type) || !totals.valid(combined_count) ||
                        !totals.valid(take) || !totals.valid(expected - take))
                        return result(lua, false, "native_stash_move_split_stack_out_of_range");
                    merge.after = static_cast<int>(combined_count);
                    merge.original.reset(property->AllocateAndInitializeValue());
                    merge.staged.reset(property->AllocateAndInitializeValue());
                    if (!merge.original || !merge.staged)
                        return result(lua, false, "native_stash_move_split_storage");
                    property->CopyCompleteValue(merge.original.get(), target);
                    property->CopyCompleteValue(merge.staged.get(), target);
                    totals.write(inventory, merge.staged.get(), merge.after);
                    if (!property->Identical(merge.original.get(), target))
                        return result(lua, false, "native_stash_move_split_storage");
                    break; // Both stock AddItem paths stack into the first exact class.
                }
                if (merge.index < 0 && destination.Num() >= max_array_rows)
                    return result(lua, false, "native_stash_move_source_or_capacity");
            }
            if (merge.index >= 0)
            {
                auto* helmet = inventory.helmet->ContainerPtrToValuePtr<void>(save);
                auto* armor = inventory.armor->ContainerPtrToValuePtr<void>(save);
                if (inventory.identity(helmet) == merge.guid || inventory.identity(armor) == merge.guid ||
                    locate(inventory, equipment, merge.guid, ignored) != 0 ||
                    locate(inventory, backpack, merge.guid, ignored) != (deposit ? 0 : 1))
                    return result(lua, false, "native_stash_move_split_duplicate");
                for (int index = 0; index < stashes.Num(); ++index)
                {
                    Unreal::FScriptArrayHelper rows(storage.contents,
                        storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(index)));
                    if (locate(inventory, rows, merge.guid, ignored) != (deposit && index == selected ? 1 : 0))
                        return result(lua, false, "native_stash_move_split_duplicate");
                }
            }
            portion.reset(property->AllocateAndInitializeValue());
            remainder.reset(property->AllocateAndInitializeValue());
            original_copy.reset(property->AllocateAndInitializeValue());
            if (!portion || !remainder || !original_copy)
                return result(lua, false, "native_stash_move_split_storage");
            property->CopyCompleteValue(original_copy.get(), source_row);
            if (!property->Identical(original_copy.get(), source_row) ||
                !silverfish::native_guest_item::split_stock_stack(context, property, source_row, take,
                    portion.get(), remainder.get()))
                return result(lua, false, "native_stash_move_split_stock_unavailable");
            const auto exact = [&](const void* row, int amount) {
                const auto weight = inventory.weight->GetPropertyValueInContainer(row);
                return inventory.klass(row) == item_type && inventory.ammo->GetPropertyValueInContainer(row) == amount &&
                    std::isfinite(weight) && weight >= 0 && weight <= 100000 &&
                    inventory.value->GetPropertyValueInContainer(row) >= 0 &&
                    inventory.never_drop->GetPropertyValueInContainer(row) ==
                        inventory.never_drop->GetPropertyValueInContainer(source_row);
            };
            const auto generated = inventory.identity(portion.get());
            if (generated == Guid{} || generated == id || inventory.identity(remainder.get()) != id ||
                !exact(portion.get(), take) || !exact(remainder.get(), expected - take))
                return result(lua, false, "native_stash_move_split_stock_result");
            if (merge.index >= 0 &&
                (!same_stack_metadata(inventory, source_row, remainder.get()) ||
                 !silverfish::loot_escrow::stock_stack_matches(context, property, merge.original.get(),
                     merge.staged.get(), take)))
                return result(lua, false, "native_stash_move_split_stack_stock_unverified");
            if (merge.index < 0)
                inventory.write_guid(inventory.guid->ContainerPtrToValuePtr<void>(portion.get()), created);
        }

        if constexpr (Reorder)
        {
            if (item.stackable) return result(lua, false, "native_stash_move_reorder_stackable");
            const auto row_size = storage.contents->GetInner()->GetElementSize();
            if (row_size <= 0) return result(lua, false, "native_stash_move_reorder_unqualified");
            Unreal::FScriptArrayHelper rows(storage.contents,
                storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(selected)));
            int destination = rows.Num() - 1;
            const auto before = lua.get_string();
            if (before != "-")
            {
                const auto anchor = stash_identity(before);
                if (anchor == Guid{} || anchor == id || locate(inventory, rows, anchor, destination) != 1)
                    return result(lua, false, "native_stash_move_reorder_anchor_unavailable");
                if (stash_index < destination) --destination;
            }
            for (int current = stash_index; current != destination;)
            {
                const int next = current + (current < destination ? 1 : -1);
                // Swap complete reflected rows using the same spacing as FScriptArrayHelper.
                // Its SwapValues implementation calls a memory routine absent from the pinned import library.
                auto* row = rows.GetRawPtr(current);
                std::swap_ranges(row, row + row_size, rows.GetRawPtr(next));
                current = next;
            }
            if (inventory.identity(rows.GetRawPtr(destination)) != id)
                return result(lua, false, "mutation_unverified_native_stash_reorder");
            result(lua, true, "moved");
            lua.set_string(item.path);
            lua.set_integer(1);
            return 4;
        }

        const bool new_stash = selected < 0;
        if (new_stash)
        {
            selected = stashes.AddValue();
            auto* encoded = storage.id->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(selected));
            inventory.write_guid(encoded, site);
        }
        Unreal::FScriptArrayHelper rows(storage.contents,
            storage.contents->ContainerPtrToValuePtr<void>(stashes.GetRawPtr(selected)));
        auto& source = deposit ? backpack : rows;
        auto& destination = deposit ? rows : backpack;
        const int previous_source = source.Num(), previous_destination = destination.Num();
        const int source_index = deposit ? pack_index : stash_index;
        if constexpr (Split)
        {
            if (merge.index >= 0)
            {
                if (source_index < 0 || source_index >= source.Num() || merge.index >= destination.Num() ||
                    object_field(context, STR("Save")) != save)
                    return result(lua, false, "native_stash_move_split_stale_amount");
                auto* original = source.GetRawPtr(source_index);
                auto* target = destination.GetRawPtr(merge.index);
                if (original == target || !property->Identical(original, original_copy.get()) ||
                    !property->Identical(target, merge.original.get()))
                    return result(lua, false, "native_stash_move_split_stale_amount");
                property->CopyCompleteValue(target, merge.staged.get());
                property->CopyCompleteValue(original, remainder.get());
                if (source.Num() != previous_source || destination.Num() != previous_destination ||
                    !property->Identical(original, remainder.get()) || !property->Identical(target, merge.staged.get()))
                {
                    property->CopyCompleteValue(original, original_copy.get());
                    property->CopyCompleteValue(target, merge.original.get());
                    const bool restored = property->Identical(original, original_copy.get()) &&
                        property->Identical(target, merge.original.get());
                    return result(lua, false, restored ? "native_stash_move_split_stack_copy"
                        : "mutation_unverified_native_stash_split_stack");
                }
                result(lua, true, "merged");
                lua.set_string(item.path);
                lua.set_integer(take);
                lua.set_integer(inventory.ammo->GetPropertyValueInContainer(original));
                lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", merge.guid[0], merge.guid[1], merge.guid[2], merge.guid[3]));
                lua.set_integer(merge.before);
                lua.set_integer(inventory.ammo->GetPropertyValueInContainer(target));
                return 8;
            }

            auto* copied = destination.GetRawPtr(destination.AddValue());
            auto* original = source.GetRawPtr(source_index);
            const auto rollback_portion = [&] {
                destination.RemoveValues(previous_destination, 1);
                if (new_stash) stashes.RemoveValues(selected, 1);
            };
            property->CopyCompleteValue(copied, portion.get());
            if (!property->Identical(copied, portion.get()))
            {
                rollback_portion();
                return result(lua, false, "native_stash_move_split_portion_copy");
            }
            property->CopyCompleteValue(original, remainder.get());
            if (source.Num() != previous_source || destination.Num() != previous_destination + 1 ||
                !property->Identical(original, remainder.get()) || inventory.identity(copied) != created)
            {
                property->CopyCompleteValue(original, original_copy.get());
                const bool restored = property->Identical(original, original_copy.get());
                rollback_portion();
                return result(lua, false, restored ? "native_stash_move_split_remainder_copy"
                    : "mutation_unverified_native_stash_split_remainder");
            }
            result(lua, true, "moved");
            lua.set_string(item.path);
            lua.set_integer(inventory.ammo->GetPropertyValueInContainer(copied));
            lua.set_integer(inventory.ammo->GetPropertyValueInContainer(original));
            lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", created[0], created[1], created[2], created[3]));
            return 6;
        }
        auto* copied = destination.GetRawPtr(destination.AddValue());
        property->CopyCompleteValue(copied, source.GetRawPtr(source_index));
        if (!property->Identical(copied, source.GetRawPtr(source_index)))
            return result(lua, false, "mutation_unverified_native_stash_payload");
        source.RemoveValues(source_index, 1);
        if (source.Num() != previous_source - 1 || destination.Num() != previous_destination + 1 ||
            locate(inventory, source, id, ignored) != 0 || locate(inventory, destination, id, ignored) != 1)
            return result(lua, false, "mutation_unverified_native_stash_move");
        result(lua, true, "moved");
        lua.set_string(item.path);
        lua.set_integer(quantity);
        return 4;
    }

    int verify_stash_save(const Lua& lua)
    {
        bool matches = false;
        if (enabled() && Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 2 &&
            lua.is_integer(1) && lua.is_integer(2))
        {
            const auto source = static_cast<Address>(lua.get_integer());
            const auto loaded = static_cast<Address>(lua.get_integer());
            auto* save = live_exact(source, find<Unreal::UClass>(save_path));
            auto* snapshot = source != loaded ? isolated_snapshot(loaded) : nullptr;
            auto* stashes = field<Unreal::FArrayProperty>(save, STR("Stashes"));
            auto* backpack = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
            matches = snapshot && stashes && backpack &&
                stashes->Identical(stashes->ContainerPtrToValuePtr<void>(save),
                                   stashes->ContainerPtrToValuePtr<void>(snapshot)) &&
                backpack->Identical(backpack->ContainerPtrToValuePtr<void>(save),
                                     backpack->ContainerPtrToValuePtr<void>(snapshot));
        }
        lua.set_bool(matches);
        return 1;
    }
}

namespace silverfish::inventory_census
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeInventoryCensus", read_census);
        lua.register_function("SilverfishReadLiveNativeInventoryCensus", read_live_census);
        lua.register_function("SilverfishReadNativeStashCensus", read_stash);
        lua.register_function("SilverfishReadLiveNativeStashCensus", read_live_stash);
        lua.register_function("SilverfishMoveNativeStashItem", move_stash<false>);
        lua.register_function("SilverfishMoveLiveNativeStashItem", move_stash<true>);
        lua.register_function("SilverfishReorderLiveNativeStashItem", move_stash<true, true>);
        lua.register_function("SilverfishSplitLiveNativeStashItem", move_stash<true, false, true>);
        lua.register_function("SilverfishSplitMergeLiveNativeStashItem", move_stash<true, false, true, true>);
        lua.register_function("SilverfishVerifyNativeStashSave", verify_stash_save);
    }
}
