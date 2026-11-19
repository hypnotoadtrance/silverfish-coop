#include "inventory_wipe.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <memory>
#include <set>
#include <string_view>
#include <vector>

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
    constexpr auto row_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto payload_path = STR("/Script/StructUtils.InstancedStruct");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_array_rows = 4096;
    constexpr int max_items = 2 * max_array_rows + 2;

    template <typename T>
    T* find(const Unreal::TCHAR* name)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, name);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* result = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return result && result->GetArrayDim() == 1 ? result : nullptr;
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
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
               size == value.size() && value[0] == '1';
    }

    int result(const Lua& lua, bool ok, std::string_view message)
    {
        lua.set_bool(ok);
        lua.set_string(message);
        return 2;
    }

    template <std::size_t Count>
    bool exact_addresses(const Lua& lua, std::array<Address, Count>& addresses)
    {
        for (auto& address : addresses)
        {
            if (!lua.is_integer(1)) return false;
            const auto value = lua.get_integer();
            if (value <= 0 || std::ranges::find(addresses, static_cast<Address>(value)) != addresses.end())
                return false;
            address = static_cast<Address>(value);
        }
        return true;
    }

    template <std::size_t Count>
    bool resolve_saves(const std::array<Address, Count>& addresses, std::array<Unreal::UObject*, Count>& saves)
    {
        auto* type = find<Unreal::UClass>(save_path);
        auto* instance = find<Unreal::UClass>(instance_path);
        auto* pawn = find<Unreal::UClass>(pawn_path);
        auto* canonical = field<Unreal::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* assigned = field<Unreal::FObjectProperty>(pawn, STR("Save"));
        if (!type || !canonical || !assigned || canonical->GetPropertyClass().Get() != type ||
            assigned->GetPropertyClass().Get() != type) return false;

        bool attached = false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (!live(object)) return RC::LoopAction::Continue;
            auto* candidate_type = object->GetClassPrivate();
            auto* link = candidate_type == instance ? canonical : candidate_type == pawn ? assigned : nullptr;
            if (link)
            {
                const auto ref = reinterpret_cast<Address>(link->GetObjectPropertyValue(link->ContainerPtrToValuePtr<void>(object)));
                if (std::ranges::find(addresses, ref) != addresses.end())
                {
                    attached = true;
                    return RC::LoopAction::Break;
                }
            }
            if (candidate_type == type)
            {
                const auto match = std::ranges::find(addresses, reinterpret_cast<Address>(object));
                if (match != addresses.end()) saves[match - addresses.begin()] = object;
            }
            return RC::LoopAction::Continue;
        });
        return !attached && std::ranges::all_of(saves, [](Unreal::UObject* save) {
            auto* outer = save ? save->GetOuterPrivate() : nullptr;
            return outer && outer->GetPathName() == STR("/Engine/Transient");
        });
    }

    struct Dispose
    {
        Unreal::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, Dispose>;

    Value new_value(Unreal::FProperty* property, const void* original = nullptr)
    {
        Value value(property->AllocateAndInitializeValue(), {property});
        if (value && original) property->CopyCompleteValue(value.get(), original);
        return value;
    }

    struct Inventory
    {
        Unreal::UClass* base = find<Unreal::UClass>(item_base_path);
        Unreal::UScriptStruct* type = find<Unreal::UScriptStruct>(row_path);
        Unreal::FClassProperty* item_class = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        Unreal::FStructProperty* guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        Unreal::FStructProperty* payload = field<Unreal::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        std::array<Unreal::FIntProperty*, 4> words{};
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        Unreal::FStructProperty* armor{};
        Unreal::FStructProperty* helmet{};
        Unreal::FStructProperty* row{};

        bool same_type(Unreal::FProperty* property) const
        {
            auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
            return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == type;
        }

        bool bind(Unreal::UObject* source)
        {
            auto* guid_type = find<Unreal::UScriptStruct>(guid_path);
            auto* payload_type = find<Unreal::UScriptStruct>(payload_path);
            backpack = field<Unreal::FArrayProperty>(source, STR("PlayerBackpack"));
            equipment = field<Unreal::FArrayProperty>(source, STR("PlayerEquipment"));
            armor = field<Unreal::FStructProperty>(source, STR("PlayerArmor"));
            helmet = field<Unreal::FStructProperty>(source, STR("PlayerHelmet"));
            row = backpack ? Unreal::CastField<Unreal::FStructProperty>(backpack->GetInner()) : nullptr;
            if (!base || !type || !item_class || item_class->GetMetaClass().Get() != base ||
                !guid_type || !guid || guid->GetStruct().Get() != guid_type ||
                !payload_type || !payload || payload->GetStruct().Get() != payload_type ||
                !equipment || !same_type(row) || !same_type(equipment->GetInner()) ||
                !same_type(armor) || !same_type(helmet)) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                auto* part = field<Unreal::FIntProperty>(guid_type, guid_names[index]);
                if (!part || part->GetOffset_Internal() != static_cast<int>(index * sizeof(std::uint32_t))) return false;
                words[index] = part;
            }
            return true;
        }

        Guid id(const void* item) const
        {
            Guid id{};
            const auto* data = guid->ContainerPtrToValuePtr<void>(item);
            for (std::size_t index = 0; index < id.size(); ++index)
                id[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return id;
        }

        Unreal::UObject* klass(const void* item) const
        {
            return item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(item));
        }

        bool blank(const void* item) const { return !klass(item) && id(item) == Guid{}; }

        bool identical(const void* first, const void* second) const
        {
            return row->Identical(first, second) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        }

        bool matches_array(Unreal::FArrayProperty* property, const void* first, const void* second, int maximum) const
        {
            Unreal::FScriptArrayHelper left(property, first);
            Unreal::FScriptArrayHelper right(property, second);
            if (left.Num() < 0 || left.Num() > maximum || left.Num() != right.Num()) return false;
            if (left.Num() && left.GetRawPtr(0) == right.GetRawPtr(0)) return false;
            for (int index = 0; index < left.Num(); ++index)
                if (!identical(left.GetRawPtr(index), right.GetRawPtr(index))) return false;
            return true;
        }
    };

    enum class Disposition : std::size_t { Keep, Drop, Discard };

    struct Choice
    {
        Disposition action;
        bool seen{};
    };

    Guid parse_guid(std::string_view text)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char ch) {
                return (ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F');
            })) return {};
        Guid result{};
        for (std::size_t index = 0; index < result.size(); ++index)
        {
            const auto* start = text.data() + index * 8;
            const auto [end, error] = std::from_chars(start, start + 8, result[index], 16);
            if (error != std::errc{} || end != start + 8) return {};
        }
        return result;
    }

    struct Partition
    {
        const Inventory& inventory;
        std::map<Guid, Choice> choices;
        std::array<int, 3> counts{};
        std::vector<const void*> bag_rows;

        bool parse(std::string_view text, Disposition action)
        {
            if (text.size() > max_items * 33 || (!text.empty() && (text.size() + 1) % 33 != 0)) return false;
            while (!text.empty())
            {
                const auto id = parse_guid(text.substr(0, 32));
                if (id == Guid{} || choices.size() == max_items || !choices.emplace(id, Choice{action}).second) return false;
                if (text.size() == 32) break;
                if (text[32] != ',') return false;
                text.remove_prefix(33);
            }
            return true;
        }

        bool read_row(const void* row, bool& kept)
        {
            kept = true;
            if (inventory.blank(row)) return true;
            auto* type = Unreal::Cast<Unreal::UClass>(inventory.klass(row));
            const auto choice = choices.find(inventory.id(row));
            if (!live(type) || !type->IsChildOf(inventory.base) || choice == choices.end() || choice->second.seen)
                return false;
            const auto action = choice->second.action;
            choice->second.seen = true;
            ++counts[static_cast<std::size_t>(action)];
            kept = action == Disposition::Keep;
            if (action == Disposition::Drop) bag_rows.push_back(row);
            return true;
        }

        bool complete() const
        {
            return std::ranges::all_of(choices, [](const auto& entry) { return entry.second.seen; });
        }
    };

    bool verify_rows(const Inventory& inventory, Unreal::FScriptArrayHelper& array, const std::vector<const void*>& expected)
    {
        if (array.Num() != static_cast<int>(expected.size())) return false;
        for (int index = 0; index < array.Num(); ++index)
            if (array.GetRawPtr(index) == expected[index] || !inventory.identical(array.GetRawPtr(index), expected[index])) return false;
        return true;
    }

    struct PreparedStore
    {
        Unreal::FProperty* property;
        const void* frozen;
        void* destination;
        Value original;
        Value projected;
    };

    PreparedStore store(Unreal::FProperty* property, const Unreal::UObject* source, Unreal::UObject* candidate)
    {
        return {property, property->ContainerPtrToValuePtr<void>(source), property->ContainerPtrToValuePtr<void>(candidate)};
    }

    bool stage_source(PreparedStore& store)
    {
        store.original = new_value(store.property, store.frozen);
        if (!store.original) return false;
        store.projected = new_value(store.property, store.frozen);
        return bool(store.projected);
    }

    bool project_array(PreparedStore& store, const Inventory& inventory, Partition& partition, const void* blank, bool compact)
    {
        auto* property = Unreal::CastField<Unreal::FArrayProperty>(store.property);
        if (!inventory.matches_array(property, store.frozen, store.destination, max_array_rows) || !stage_source(store) ||
            !inventory.matches_array(property, store.frozen, store.original.get(), max_array_rows) ||
            !inventory.matches_array(property, store.frozen, store.projected.get(), max_array_rows)) return false;
        Unreal::FScriptArrayHelper frozen(property, store.frozen);
        Unreal::FScriptArrayHelper projected(property, store.projected.get());
        std::vector<int> erase;
        std::vector<const void*> expected;
        expected.reserve(frozen.Num());
        for (int index = 0; index < frozen.Num(); ++index)
        {
            const void* row = frozen.GetRawPtr(index);
            bool kept{};
            if (!partition.read_row(row, kept)) return false;
            if (kept) expected.push_back(row);
            else if (compact) erase.push_back(index);
            else
            {
                inventory.row->CopyCompleteValue(projected.GetRawPtr(index), blank);
                expected.push_back(blank);
            }
        }
        for (auto index = erase.rbegin(); index != erase.rend(); ++index) projected.RemoveValues(*index, 1);
        return verify_rows(inventory, projected, expected);
    }

    bool project_scalar(PreparedStore& store, const Inventory& inventory, Partition& partition, const void* blank)
    {
        bool kept{};
        if (!inventory.identical(store.frozen, store.destination) || !partition.read_row(store.frozen, kept) ||
            !stage_source(store) || !inventory.identical(store.frozen, store.original.get()) ||
            !inventory.identical(store.frozen, store.projected.get())) return false;
        if (!kept) store.property->CopyCompleteValue(store.projected.get(), blank);
        return inventory.identical(store.projected.get(), kept ? store.frozen : blank);
    }

    bool bag_gear_empty(const Inventory& inventory, Unreal::UObject* bag)
    {
        Unreal::FScriptArrayHelper loadout(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(bag));
        if (loadout.Num() < 0 || loadout.Num() > max_array_rows ||
            !inventory.blank(inventory.armor->ContainerPtrToValuePtr<void>(bag)) ||
            !inventory.blank(inventory.helmet->ContainerPtrToValuePtr<void>(bag))) return false;
        for (int index = 0; index < loadout.Num(); ++index)
            if (!inventory.blank(loadout.GetRawPtr(index))) return false;
        return true;
    }

    bool blank_bag(const Inventory& inventory, Unreal::UObject* bag)
    {
        Unreal::FScriptArrayHelper contents(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(bag));
        return contents.Num() == 0 && bag_gear_empty(inventory, bag);
    }

    int prepare(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 6)
            return result(lua, false, "native_inventory_wipe_scope_unavailable");
        std::array<Address, 3> addresses{};
        if (!exact_addresses(lua, addresses)) return result(lua, false, "native_inventory_wipe_exact_saves_required");
        std::array<Unreal::UObject*, 3> saves{};
        if (!resolve_saves(addresses, saves)) return result(lua, false, "native_inventory_wipe_exact_isolated_saves_required");
        const auto [source, candidate, bag] = saves;
        Inventory inventory;
        if (!inventory.bind(source)) return result(lua, false, "native_inventory_wipe_stock_schema_unavailable");
        Partition partition{inventory};
        for (const auto action : {Disposition::Keep, Disposition::Drop, Disposition::Discard})
        {
            if (!lua.is_string(1) || !partition.parse(lua.get_string(), action))
                return result(lua, false, "native_inventory_wipe_invalid_partition");
        }
        const auto blank = new_value(inventory.row);
        if (!blank || !inventory.blank(blank.get()) || !blank_bag(inventory, bag))
            return result(lua, false, "native_inventory_wipe_blank_stock_bag_required");

        auto backpack = store(inventory.backpack, source, candidate);
        auto equipment = store(inventory.equipment, source, candidate);
        auto armor = store(inventory.armor, source, candidate);
        auto helmet = store(inventory.helmet, source, candidate);
        if (!project_array(backpack, inventory, partition, blank.get(), true) ||
            !project_array(equipment, inventory, partition, blank.get(), false) ||
            !project_scalar(armor, inventory, partition, blank.get()) ||
            !project_scalar(helmet, inventory, partition, blank.get()) || !partition.complete())
            return result(lua, false, "native_inventory_wipe_partition_or_source_copy_unverified");

        auto staged_bag = new_value(inventory.backpack);
        if (!staged_bag) return result(lua, false, "native_inventory_wipe_stock_bag_allocation_failed");
        Unreal::FScriptArrayHelper bag_rows(inventory.backpack, staged_bag.get());
        for (const auto* row : partition.bag_rows)
            inventory.row->CopyCompleteValue(bag_rows.GetRawPtr(bag_rows.AddValue()), row);
        if (!verify_rows(inventory, bag_rows, partition.bag_rows))
            return result(lua, false, "native_inventory_wipe_stock_bag_payload_unverified");

        // Only the isolated candidate and scratch bag are written. Any failure
        // after this point requires discarding both generations without saving.
        for (auto* entry : {&backpack, &equipment, &armor, &helmet})
            entry->property->CopyCompleteValue(entry->destination, entry->projected.get());
        auto* bag_destination = inventory.backpack->ContainerPtrToValuePtr<void>(bag);
        inventory.backpack->CopyCompleteValue(bag_destination, staged_bag.get());

        const auto arrays_verified = [&] {
            for (auto* entry : {&backpack, &equipment})
            {
                auto* array = Unreal::CastField<Unreal::FArrayProperty>(entry->property);
                if (!inventory.matches_array(array, entry->original.get(), entry->frozen, max_array_rows) ||
                    !inventory.matches_array(array, entry->projected.get(), entry->destination, max_array_rows)) return false;
            }
            return inventory.matches_array(inventory.backpack, staged_bag.get(), bag_destination, max_items);
        };
        const auto scalars_verified = [&] {
            for (auto* entry : {&armor, &helmet})
                if (!inventory.identical(entry->original.get(), entry->frozen) ||
                    !inventory.identical(entry->projected.get(), entry->destination)) return false;
            return true;
        };
        if (!arrays_verified() || !scalars_verified())
            return result(lua, false, "native_inventory_wipe_discard_prepared_generations");

        result(lua, true, "prepared");
        for (auto count : partition.counts) lua.set_integer(count);
        return 5;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool authoritative_actor(Unreal::UObject* object, Unreal::UClass* exact_class, Unreal::UWorld* world)
    {
        if (!live(object) || object->GetClassPrivate() != exact_class) return false;
        auto* role = field<Unreal::FByteProperty>(object, STR("Role"));
        auto* tearing_down = field<Unreal::FBoolProperty>(object, STR("bActorIsBeingDestroyed"));
        return role && role->GetPropertyValueInContainer(object) == 3 &&
            tearing_down && !tearing_down->GetPropertyValueInContainer(object) &&
            static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    bool enrolled_connection(Unreal::UObject* driver, Unreal::UObject* player, Unreal::UObject* controller)
    {
        constexpr int max_remote_players = 4;
        auto* type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        if (!type || !player->IsA(type) || object_field(player, STR("Driver")) != driver ||
            object_field(player, STR("PlayerController")) != controller || object_field(player, STR("OwningActor")) != controller)
            return false;

        auto* property = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* entry = property ? Unreal::CastField<Unreal::FObjectProperty>(property->GetInner()) : nullptr;
        if (!entry || entry->GetArrayDim() != 1) return false;
        Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(driver));
        if (rows.Num() < 1 || rows.Num() > max_remote_players) return false;
        int matches = 0;
        for (int index = 0; index < rows.Num(); ++index)
            matches += entry->GetObjectPropertyValue(rows.GetRawPtr(index)) == player;
        return matches == 1;
    }

    Unreal::UObject* qualified_live_save(Address pawn_address, Address save_address)
    {
        auto* pawn_class = find<Unreal::UClass>(pawn_path);
        auto* save_class = find<Unreal::UClass>(save_path);
        auto* instance_class = find<Unreal::UClass>(instance_path);
        auto* controller_class = find<Unreal::UClass>(STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"));
        auto* local_class = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!pawn_class || !save_class || !instance_class || !controller_class || !local_class) return nullptr;

        Unreal::UObject* pawn = nullptr;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (reinterpret_cast<Address>(object) != pawn_address) return RC::LoopAction::Continue;
            if (live(object) && object->GetClassPrivate() == pawn_class) pawn = object;
            return RC::LoopAction::Break;
        });
        auto* world = pawn ? static_cast<Unreal::AActor*>(pawn)->GetWorld() : nullptr;
        if (!live(world) || !authoritative_actor(pawn, pawn_class, world)) return nullptr;
        auto* controller = object_field(pawn, STR("Controller"));
        if (!authoritative_actor(controller, controller_class, world) || object_field(controller, STR("Pawn")) != pawn)
            return nullptr;

        auto* driver = object_field(world, STR("NetDriver"));
        auto* server = field<Unreal::FObjectProperty>(driver, STR("ServerConnection"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        if (!live(driver) || object_field(driver, STR("World")) != world || !server ||
            server->GetObjectPropertyValue(server->ContainerPtrToValuePtr<void>(driver)) ||
            !live(instance) || instance->GetClassPrivate() != instance_class) return nullptr;

        auto* target = object_field(pawn, STR("Save"));
        auto* canonical = object_field(instance, STR("SaveSlot_Ref"));
        auto* player = object_field(controller, STR("Player"));
        if (!live(target) || target->GetClassPrivate() != save_class || reinterpret_cast<Address>(target) != save_address ||
            !live(canonical) || canonical->GetClassPrivate() != save_class || !live(player)) return nullptr;

        auto* local_players = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* local_entry = local_players ? Unreal::CastField<Unreal::FObjectProperty>(local_players->GetInner()) : nullptr;
        if (!local_entry || local_entry->GetArrayDim() != 1) return nullptr;
        Unreal::FScriptArrayHelper players(local_players, local_players->ContainerPtrToValuePtr<void>(instance));
        if (players.Num() != 1) return nullptr;
        auto* local = local_entry->GetObjectPropertyValue(players.GetRawPtr(0));
        if (!live(local) || !local->IsA(local_class)) return nullptr;
        auto* host_controller = object_field(local, STR("PlayerController"));
        auto* host = object_field(host_controller, STR("Pawn"));
        if (!authoritative_actor(host_controller, controller_class, world) || object_field(host_controller, STR("Player")) != local ||
            !authoritative_actor(host, pawn_class, world) || object_field(host, STR("Controller")) != host_controller ||
            object_field(host, STR("Save")) != canonical) return nullptr;

        if (pawn == host)
        {
            if (player != local || target != canonical) return nullptr;
        }
        else if (target == canonical || !enrolled_connection(driver, player, controller))
        {
            return nullptr;
        }

        bool shared = false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (!live(object)) return RC::LoopAction::Continue;
            auto* kind = object->GetClassPrivate();
            if ((kind == pawn_class && object != pawn && object_field(object, STR("Save")) == target) ||
                (kind == instance_class && (target != canonical || object != instance) &&
                 object_field(object, STR("SaveSlot_Ref")) == target))
            {
                shared = true;
                return RC::LoopAction::Break;
            }
            return RC::LoopAction::Continue;
        });
        return shared ? nullptr : target;
    }

    bool matches_store(const Inventory& inventory, Unreal::FProperty* property, const void* left, const void* right)
    {
        if (left == right) return false;
        auto* array = Unreal::CastField<Unreal::FArrayProperty>(property);
        return array ? inventory.matches_array(array, left, right, max_array_rows) : inventory.identical(left, right);
    }

    bool independent_store(Unreal::FProperty* property, const void* left, const void* right)
    {
        if (left == right) return false;
        auto* array = Unreal::CastField<Unreal::FArrayProperty>(property);
        if (!array) return true;
        Unreal::FScriptArrayHelper first(array, left);
        Unreal::FScriptArrayHelper second(array, right);
        return first.Num() >= 0 && first.Num() <= max_array_rows && second.Num() >= 0 && second.Num() <= max_array_rows &&
            (!first.Num() || !second.Num() || first.GetRawPtr(0) != second.GetRawPtr(0));
    }

    int apply_prepared(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4)
            return result(lua, false, "native_inventory_wipe_apply_scope_unavailable");
        std::array<Address, 4> addresses{};
        if (!exact_addresses(lua, addresses)) return result(lua, false, "native_inventory_wipe_apply_exact_addresses_required");
        const std::array frames{addresses[0], addresses[1]};
        std::array<Unreal::UObject*, 2> saves{};
        if (!resolve_saves(frames, saves)) return result(lua, false, "native_inventory_wipe_exact_isolated_saves_required");
        const auto [source, prepared] = saves;
        auto* target = qualified_live_save(addresses[3], addresses[2]);
        if (!target) return result(lua, false, "native_inventory_wipe_authoritative_owner_unavailable");
        Inventory inventory;
        if (!inventory.bind(source)) return result(lua, false, "native_inventory_wipe_stock_schema_unavailable");
        std::array stores{store(inventory.backpack, source, target), store(inventory.equipment, source, target),
                          store(inventory.armor, source, target), store(inventory.helmet, source, target)};
        bool unchanged = true;
        bool already_applied = true;
        for (const auto& entry : stores)
        {
            auto* property = entry.property;
            const auto* projected = property->ContainerPtrToValuePtr<void>(prepared);
            if (!independent_store(property, entry.frozen, projected) || !independent_store(property, entry.frozen, entry.destination) ||
                !independent_store(property, projected, entry.destination))
                return result(lua, false, "native_inventory_wipe_apply_inventory_alias_or_bounds");
            unchanged &= matches_store(inventory, property, entry.frozen, entry.destination);
            already_applied &= matches_store(inventory, property, projected, entry.destination);
        }
        if (already_applied) return result(lua, true, "already_applied");
        if (!unchanged) return result(lua, false, "native_inventory_wipe_live_inventory_changed");

        // Capture both shadows before any live write. Reflected copies do not
        // dispatch Blueprint or touch state outside the four inventory stores.
        for (auto& entry : stores)
        {
            const auto* projected = entry.property->ContainerPtrToValuePtr<void>(prepared);
            entry.original = new_value(entry.property, entry.destination);
            entry.projected = new_value(entry.property, projected);
            if (!entry.original || !entry.projected ||
                !matches_store(inventory, entry.property, entry.original.get(), entry.destination) ||
                !matches_store(inventory, entry.property, entry.projected.get(), projected))
                return result(lua, false, "native_inventory_wipe_apply_shadow_unverified");
        }
        for (const auto& entry : stores)
            entry.property->CopyCompleteValue(entry.destination, entry.projected.get());

        const auto verify_frame = [&](bool projected) {
            return std::ranges::all_of(stores, [&](const auto& entry) {
                auto* property = entry.property;
                return matches_store(inventory, property, entry.original.get(), entry.frozen) &&
                    matches_store(inventory, property, entry.projected.get(), property->ContainerPtrToValuePtr<void>(prepared)) &&
                    matches_store(inventory, property, projected ? entry.projected.get() : entry.original.get(), entry.destination);
            });
        };
        if (qualified_live_save(addresses[3], addresses[2]) == target)
        {
            if (verify_frame(true)) return result(lua, true, "applied");
            for (const auto& entry : stores)
                entry.property->CopyCompleteValue(entry.destination, entry.original.get());
            if (verify_frame(false)) return result(lua, false, "native_inventory_wipe_apply_rolled_back");
        }
        return result(lua, false, "mutation_unverified_native_inventory_wipe_apply_rollback");
    }

    bool read_recovery_guid(const Inventory& inventory, const void* row, Guid& guid)
    {
        guid = inventory.id(row);
        auto* object = inventory.klass(row);
        if (!object) return guid == Guid{};
        auto* type = Unreal::Cast<Unreal::UClass>(object);
        return live(type) && type->IsChildOf(inventory.base) && guid != Guid{};
    }

    std::string_view recovery_target_guids(const Inventory& inventory, Unreal::UObject* target, std::set<Guid>& seen)
    {
        const auto check_row = [&](const void* row) -> std::string_view {
            Guid guid{};
            if (!read_recovery_guid(inventory, row, guid)) return "native_inventory_wipe_recovery_live_inventory_invalid";
            if (guid != Guid{} && !seen.insert(guid).second) return "native_inventory_wipe_recovery_guid_collision";
            return {};
        };
        for (auto* property : {inventory.backpack, inventory.equipment})
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(target));
            if (rows.Num() < 0 || rows.Num() > max_array_rows) return "native_inventory_wipe_recovery_inventory_bounds";
            for (int index = 0; index < rows.Num(); ++index)
                if (const auto failure = check_row(rows.GetRawPtr(index)); !failure.empty()) return failure;
        }
        const auto armor = check_row(inventory.armor->ContainerPtrToValuePtr<void>(target));
        return armor.empty() ? check_row(inventory.helmet->ContainerPtrToValuePtr<void>(target)) : armor;
    }

    int recover_bag(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3)
            return result(lua, false, "native_inventory_wipe_recovery_scope_unavailable");
        std::array<Address, 3> addresses{};
        if (!exact_addresses(lua, addresses)) return result(lua, false, "native_inventory_wipe_recovery_exact_addresses_required");
        const std::array pool_address{addresses[0]};
        std::array<Unreal::UObject*, 1> pools{};
        if (!resolve_saves(pool_address, pools)) return result(lua, false, "native_inventory_wipe_exact_isolated_bag_required");
        auto* pool = pools.front();
        auto* target = qualified_live_save(addresses[2], addresses[1]);
        if (!target) return result(lua, false, "native_inventory_wipe_authoritative_owner_unavailable");
        Inventory inventory;
        if (!inventory.bind(pool)) return result(lua, false, "native_inventory_wipe_stock_schema_unavailable");
        if (!bag_gear_empty(inventory, pool)) return result(lua, false, "native_inventory_wipe_recovery_bag_invalid");

        auto* backpack = inventory.backpack;
        auto* pool_data = backpack->ContainerPtrToValuePtr<void>(pool);
        auto* target_data = backpack->ContainerPtrToValuePtr<void>(target);
        Unreal::FScriptArrayHelper bag_rows(backpack, pool_data);
        Unreal::FScriptArrayHelper target_rows(backpack, target_data);
        const auto count = bag_rows.Num();
        const auto carried = target_rows.Num();
        if (count < 1 || count > max_items) return result(lua, false, "native_inventory_wipe_recovery_bag_invalid");
        if (carried < 0 || carried > max_array_rows || count > max_array_rows - carried)
            return result(lua, false, "native_inventory_wipe_recovery_backpack_capacity");
        auto* loadout = inventory.equipment->ContainerPtrToValuePtr<void>(target);
        if (!independent_store(backpack, pool_data, target_data) || !independent_store(backpack, target_data, loadout))
            return result(lua, false, "native_inventory_wipe_recovery_inventory_alias_or_bounds");

        std::set<Guid> seen;
        std::vector<const void*> expected;
        expected.reserve(static_cast<std::size_t>(carried + count));
        for (int index = 0; index < carried; ++index) expected.push_back(target_rows.GetRawPtr(index));
        for (int index = 0; index < count; ++index)
        {
            const auto* row = bag_rows.GetRawPtr(index);
            Guid guid{};
            if (!read_recovery_guid(inventory, row, guid) || guid == Guid{} || !seen.insert(guid).second)
                return result(lua, false, "native_inventory_wipe_recovery_bag_invalid");
            expected.push_back(row);
        }
        if (const auto failure = recovery_target_guids(inventory, target, seen); !failure.empty())
            return result(lua, false, failure);

        auto original = new_value(backpack, target_data);
        auto projected = new_value(backpack, target_data);
        auto bag_shadow = new_value(backpack, pool_data);
        if (!original || !projected || !bag_shadow ||
            !matches_store(inventory, backpack, original.get(), target_data) ||
            !matches_store(inventory, backpack, projected.get(), target_data) ||
            !matches_store(inventory, backpack, bag_shadow.get(), pool_data))
            return result(lua, false, "native_inventory_wipe_recovery_shadow_unverified");
        std::array gear{store(inventory.equipment, target, target), store(inventory.armor, target, target),
                        store(inventory.helmet, target, target)};
        for (auto& slot : gear)
        {
            slot.original = new_value(slot.property, slot.frozen);
            if (!slot.original || !matches_store(inventory, slot.property, slot.original.get(), slot.frozen))
                return result(lua, false, "native_inventory_wipe_recovery_shadow_unverified");
        }

        Unreal::FScriptArrayHelper staged(backpack, projected.get());
        for (int index = 0; index < count; ++index)
            inventory.row->CopyCompleteValue(staged.GetRawPtr(staged.AddValue()), bag_rows.GetRawPtr(index));
        if (!verify_rows(inventory, staged, expected))
            return result(lua, false, "native_inventory_wipe_recovery_payload_unverified");

        // Only the current owner's backpack is written, in a single game-thread
        // copy. The durable bag generation and the surrounding gear stay read-only.
        backpack->CopyCompleteValue(target_data, projected.get());
        const auto verify_recovery = [&](const Value& expected_backpack) {
            return matches_store(inventory, backpack, expected_backpack.get(), target_data) &&
                matches_store(inventory, backpack, bag_shadow.get(), pool_data) &&
                std::ranges::all_of(gear, [&](const auto& slot) {
                    return matches_store(inventory, slot.property, slot.original.get(), slot.frozen);
                });
        };
        if (qualified_live_save(addresses[2], addresses[1]) == target)
        {
            if (verify_recovery(projected))
            {
                result(lua, true, "recovered");
                lua.set_integer(count);
                return 3;
            }
            backpack->CopyCompleteValue(target_data, original.get());
            if (verify_recovery(original)) return result(lua, false, "native_inventory_wipe_recovery_rolled_back");
        }
        return result(lua, false, "mutation_unverified_native_inventory_wipe_recovery_rollback");
    }

    int verify_prepared(const Lua& lua)
    {
        std::array<Address, 2> addresses{};
        std::array<Unreal::UObject*, 2> saves{};
        bool confirmed = false;
        if (enabled() && Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 2 &&
            exact_addresses(lua, addresses) && resolve_saves(addresses, saves))
        {
            const auto [left, right] = saves;
            Inventory inventory;
            if (inventory.bind(left))
            {
                const auto matches_array = [&](Unreal::FArrayProperty* property, int maximum) {
                    return inventory.matches_array(property, property->ContainerPtrToValuePtr<void>(left),
                        property->ContainerPtrToValuePtr<void>(right), maximum);
                };
                const auto matches_scalar = [&](Unreal::FStructProperty* property) {
                    return inventory.identical(property->ContainerPtrToValuePtr<void>(left),
                        property->ContainerPtrToValuePtr<void>(right));
                };
                confirmed = matches_array(inventory.backpack, max_items) && matches_array(inventory.equipment, max_array_rows) &&
                    matches_scalar(inventory.armor) && matches_scalar(inventory.helmet);
            }
        }
        lua.set_bool(confirmed);
        return 1;
    }
}

namespace silverfish::inventory_wipe
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishPrepareNativeInventoryWipe", prepare);
        lua.register_function("SilverfishVerifyPreparedNativeInventory", verify_prepared);
        lua.register_function("SilverfishApplyPreparedNativeInventory", apply_prepared);
        lua.register_function("SilverfishRecoverNativeInventoryBag", recover_bag);
    }
}
