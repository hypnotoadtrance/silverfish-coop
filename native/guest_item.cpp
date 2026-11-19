#include "guest_item.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <ranges>
#include <string_view>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto weapon_path = STR("/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C");
    constexpr auto ammo_fixture_path = STR("/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C");
    constexpr auto ammo_master_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto ammo_type_path = STR("/Game/Classes/Libraries/Structs/AmmoType_Struct.AmmoType_Struct");
    constexpr auto projectile_path =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto primary_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Use_Item_Primary");
    constexpr auto update_mag_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Update_Mag");
    constexpr auto equip_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Equip_NextItem");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto class_name = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto ammo_name = STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788");
    constexpr auto weight_name = STR("Weight_12_11AB9ED547504E78A849A692A36DC938");
    constexpr auto value_name = STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9");
    constexpr auto guid_name = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr std::int32_t magazine_fixture = 7;
    constexpr int max_inventory = 4096;
    constexpr int max_clients = 4;
    constexpr std::size_t max_observed_projectiles = 128;

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* prop = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <typename T>
    T* input(Unreal::UFunction* function, const Unreal::TCHAR* name, std::size_t offset)
    {
        auto* prop = field<T>(function, name);
        return prop && prop->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
               !prop->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) &&
               prop->GetOffset_Internal() == static_cast<int>(offset) ? prop : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    Unreal::UObject* object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool boolean(Unreal::UObject* owner, const Unreal::TCHAR* name, bool expected)
    {
        auto* property = field<Unreal::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool authority(Unreal::UObject* actor)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3;
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t length{};
        return getenv_s(&length, value.data(), value.size(), name) == 0 && length == value.size() && value[0] == '1';
    }

    bool parse_guid(std::string_view text, Guid& guid)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char value) {
                return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F');
            })) return false;
        for (std::size_t index = 0; index < guid.size(); ++index)
        {
            const auto* start = text.data() + index * 8;
            if (std::from_chars(start, start + 8, guid[index], 16).ec != std::errc{}) return false;
        }
        return guid != Guid{};
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int proof(const Lua& lua, bool ok, std::string_view status, std::int32_t first = -1, std::int32_t second = -1,
              int projectiles = -1)
    {
        result(lua, ok, status);
        lua.set_integer(first);
        lua.set_integer(second);
        if (projectiles < 0) return 4;
        lua.set_integer(projectiles);
        return 5;
    }

    int reload_proof(const Lua& lua, bool ok, std::string_view status, std::int32_t before = -1, std::int32_t after = -1,
                     std::int32_t reserve_before = -1, std::int32_t reserve_after = -1)
    {
        proof(lua, ok, status, before, after);
        lua.set_integer(reserve_before);
        lua.set_integer(reserve_after);
        return 6;
    }

    struct Context
    {
        Guid guid{};
        Unreal::UObject* pawn{};
        Unreal::UObject* save{};
        Unreal::UObject* stock{};
        Unreal::UObject* instance{};
        Unreal::UObject* controller{};
        Unreal::UObject* connection{};
        Unreal::UWorld* world{};
        Unreal::UClass* weapon{};
    };

    bool ownership_current(const Context& context)
    {
        if (!authority(context.pawn) || !live(context.controller) || !live(context.connection) ||
            static_cast<Unreal::AActor*>(context.pawn)->GetWorld() != context.world ||
            static_cast<Unreal::AActor*>(context.controller)->GetWorld() != context.world ||
            object(context.pawn, STR("Controller")) != context.controller ||
            object(context.controller, STR("Pawn")) != context.pawn ||
            object(context.controller, STR("Player")) != context.connection ||
            !live(context.save) || object(context.pawn, STR("Save")) != context.save ||
            !live(context.instance) || object(context.world, STR("OwningGameInstance")) != context.instance ||
            !live(context.stock) || object(context.instance, STR("SaveSlot_Ref")) != context.stock ||
            context.save == context.stock) return false;

        auto* driver = object(context.world, STR("NetDriver"));
        auto* clients = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* connection = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
        if (!live(driver) || object(driver, STR("ServerConnection")) || !connection) return false;
        Unreal::FScriptArrayHelper list(clients, clients->ContainerPtrToValuePtr<void>(driver));
        if (list.Num() < 1 || list.Num() > max_clients) return false;
        std::array<Unreal::UObject*, max_clients> seen{};
        bool matched = false;
        for (int index = 0; index < list.Num(); ++index)
        {
            auto* candidate = connection->GetObjectPropertyValue(list.GetRawPtr(index));
            if (!live(candidate) || std::ranges::find(seen, candidate) != seen.end()) return false;
            seen[static_cast<std::size_t>(index)] = candidate;
            if (candidate == context.connection)
            {
                matched = true;
                continue;
            }
            // An assigned neighboring guest must not share this detached save.
            auto* controller = object(candidate, STR("PlayerController"));
            auto* pawn = object(controller, STR("Pawn"));
            if (!live(controller) || object(controller, STR("Player")) != candidate ||
                !authority(pawn) || pawn->GetClassPrivate() != context.pawn->GetClassPrivate() ||
                static_cast<Unreal::AActor*>(pawn)->GetWorld() != context.world ||
                object(pawn, STR("Controller")) != controller || object(pawn, STR("Save")) == context.save)
                return false;
        }
        return matched;
    }

    std::string_view resolve(const Lua& lua, Context& context)
    {
        if (!env("SILVERFISH_COOP_NATIVE_FIXTURES")) return "native_guest_weapon_fixtures_disabled";
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3)) return "guest_pawn_save_and_guid_required";
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        if (pawn_address <= 0 || save_address <= 0 || !parse_guid(lua.get_string(), context.guid))
            return "guest_pawn_save_and_guid_required";

        auto* pawn_class = find<Unreal::UClass>(pawn_path);
        auto* save_class = find<Unreal::UClass>(save_path);
        auto* instance_class = find<Unreal::UClass>(instance_path);
        auto* controller_class = find<Unreal::UClass>(STR("/Script/Engine.PlayerController"));
        auto* connection_class = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        context.weapon = find<Unreal::UClass>(weapon_path);
        if (!pawn_class || !save_class || !instance_class || !controller_class || !connection_class || !context.weapon)
            return "stock_guest_weapon_class_unavailable";

        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (reinterpret_cast<std::uintptr_t>(candidate) != static_cast<std::uintptr_t>(pawn_address))
                return RC::LoopAction::Continue;
            if (candidate->GetClassPrivate() == pawn_class && authority(candidate)) context.pawn = candidate;
            return RC::LoopAction::Break;
        });
        if (!context.pawn) return "authoritative_guest_pawn_required";
        context.save = object(context.pawn, STR("Save"));
        if (!live(context.save) || context.save->GetClassPrivate() != save_class ||
            reinterpret_cast<std::uintptr_t>(context.save) != static_cast<std::uintptr_t>(save_address))
            return "detached_guest_save_required";

        context.world = static_cast<Unreal::AActor*>(context.pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        context.stock = object(context.instance, STR("SaveSlot_Ref"));
        context.controller = object(context.pawn, STR("Controller"));
        context.connection = object(context.controller, STR("Player"));
        if (!context.world || !live(context.instance) || context.instance->GetClassPrivate() != instance_class ||
            !live(context.stock) || context.stock->GetClassPrivate() != save_class || context.stock == context.save ||
            !live(context.controller) || !context.controller->IsA(controller_class) ||
            !live(context.connection) || !context.connection->IsA(connection_class) || !ownership_current(context))
            return "authenticated_guest_detached_save_required";
        return {};
    }

    struct Inventory
    {
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        Unreal::FStructProperty* type{};
        Unreal::FStructProperty* armor{};
        Unreal::FStructProperty* helmet{};
        Unreal::FClassProperty* item_class{};
        Unreal::FIntProperty* ammo{};
        Unreal::FIntProperty* selected{};
        Unreal::FStructProperty* guid{};
        std::array<Unreal::FIntProperty*, 4> words{};
        void* item{};
        int backpack_index{-1};
        int equipment_index{-1};

        Guid identity(const void* row) const
        {
            Guid result{};
            const auto* value = guid->ContainerPtrToValuePtr<void>(row);
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(value));
            return result;
        }

        void set_identity(void* row, const Guid& identity) const
        {
            auto* value = guid->ContainerPtrToValuePtr<void>(row);
            for (std::size_t index = 0; index < words.size(); ++index)
                words[index]->SetPropertyValueInContainer(value, static_cast<std::int32_t>(identity[index]));
        }

        Unreal::UObject* row_class(const void* row) const
        {
            return item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(row));
        }
    };

    std::string_view locate(const Context& context, Inventory& inventory, Unreal::UClass* expected_class = nullptr)
    {
        if (!expected_class) expected_class = context.weapon;
        inventory.backpack = field<Unreal::FArrayProperty>(context.save, STR("PlayerBackpack"));
        inventory.equipment = field<Unreal::FArrayProperty>(context.save, STR("PlayerEquipment"));
        inventory.type = inventory.backpack ? Unreal::CastField<Unreal::FStructProperty>(inventory.backpack->GetInner()) : nullptr;
        auto* equipment_row = inventory.equipment ?
            Unreal::CastField<Unreal::FStructProperty>(inventory.equipment->GetInner()) : nullptr;
        inventory.armor = field<Unreal::FStructProperty>(context.save, STR("PlayerArmor"));
        inventory.helmet = field<Unreal::FStructProperty>(context.save, STR("PlayerHelmet"));
        auto* row_type = find<Unreal::UScriptStruct>(item_path);
        auto* guid_type = find<Unreal::UScriptStruct>(guid_path);
        inventory.item_class = field<Unreal::FClassProperty>(row_type, class_name);
        inventory.ammo = field<Unreal::FIntProperty>(row_type, ammo_name);
        inventory.guid = field<Unreal::FStructProperty>(row_type, guid_name);
        inventory.selected = field<Unreal::FIntProperty>(context.save, STR("PlayerEquipIndex"));
        if (!row_type || !inventory.type || !equipment_row || !inventory.armor || !inventory.helmet ||
            inventory.type->GetStruct().Get() != row_type || equipment_row->GetStruct().Get() != row_type ||
            inventory.armor->GetStruct().Get() != row_type || inventory.helmet->GetStruct().Get() != row_type ||
            !inventory.item_class || !inventory.ammo || !inventory.selected || !inventory.guid ||
            !guid_type || inventory.guid->GetStruct().Get() != guid_type) return "guest_weapon_inventory_schema_unavailable";
        for (std::size_t index = 0; index < inventory.words.size(); ++index)
        {
            inventory.words[index] = field<Unreal::FIntProperty>(guid_type, guid_names[index]);
            if (!inventory.words[index]) return "guest_weapon_guid_schema_unavailable";
        }
        if (inventory.identity(inventory.armor->ContainerPtrToValuePtr<void>(context.save)) == context.guid ||
            inventory.identity(inventory.helmet->ContainerPtrToValuePtr<void>(context.save)) == context.guid)
            return "guest_weapon_guid_conflict";

        const auto scan = [&](Unreal::FArrayProperty* property, int& index) {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(context.save));
            if (rows.Num() > max_inventory) return false;
            for (int i = 0; i < rows.Num(); ++i)
            {
                auto* row = rows.GetRawPtr(i);
                if (inventory.identity(row) != context.guid) continue;
                if (inventory.item || inventory.row_class(row) != expected_class) return false;
                inventory.item = row;
                index = i;
            }
            return true;
        };
        return scan(inventory.backpack, inventory.backpack_index) && scan(inventory.equipment, inventory.equipment_index)
            ? "" : "guest_weapon_guid_conflict";
    }

    bool ready_pawn(const Context& context)
    {
        auto* health = field<Unreal::FDoubleProperty>(context.save, STR("PlayerHealth"));
        const auto hp = health ? health->GetPropertyValueInContainer(context.save) : 0.0;
        return std::isfinite(hp) && hp > 0 && boolean(context.pawn, STR("Dead"), false) &&
            boolean(context.pawn, STR("IsWeaponBlocked"), false) && boolean(context.pawn, STR("UsingItem"), false) &&
            boolean(context.pawn, STR("Reloading"), false) && boolean(context.pawn, STR("HeadUnderWater"), false);
    }

    Unreal::UObject* equipped(const Context& context, const Inventory& inventory)
    {
        if (inventory.equipment_index < 0 ||
            inventory.selected->GetPropertyValueInContainer(context.save) != inventory.equipment_index) return nullptr;
        auto* actor = object(context.pawn, STR("EquipedItem"));
        return authority(actor) && actor->GetClassPrivate() == context.weapon &&
            static_cast<Unreal::AActor*>(actor)->GetWorld() == context.world &&
            object(actor, STR("Owner")) == context.pawn && object(actor, STR("Instigator")) == context.pawn &&
            object(actor, STR("Player_Ref")) == context.pawn && object(actor, STR("User")) == context.pawn &&
            object(actor, STR("As Game Instance FP")) == context.instance &&
            boolean(actor, STR("HeldByPlayer"), true) ? actor : nullptr;
    }

    enum class Operation { Seed, SeedAmmo, Equip, Inspect, Fire, Disarm, Reload };

    struct AmmoStackSchema
    {
        Unreal::FIntProperty* value{};
        Unreal::FDoubleProperty* weight{};
        std::int32_t unit_value{};
        double unit_weight{};

        bool read(Unreal::UClass* item_class, const Inventory& inventory)
        {
            auto* base = find<Unreal::UClass>(ammo_master_path);
            if (!base || !item_class || !item_class->IsChildOf(base)) return false;
            auto* defaults = item_class->GetClassDefaultObject().Get();
            auto* base_value = field<Unreal::FIntProperty>(defaults, STR("Value"));
            auto* base_weight = field<Unreal::FDoubleProperty>(defaults, STR("Weight"));
            auto* row_type = inventory.type->GetStruct().Get();
            value = field<Unreal::FIntProperty>(row_type, value_name);
            weight = field<Unreal::FDoubleProperty>(row_type, weight_name);
            if (!defaults || defaults->GetClassPrivate() != item_class ||
                !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !boolean(defaults, STR("Stacks"), true) ||
                !base_value || !base_weight || !value || !weight) return false;
            unit_value = base_value->GetPropertyValueInContainer(defaults);
            unit_weight = base_weight->GetPropertyValueInContainer(defaults);
            return unit_value >= 0 && std::isfinite(unit_weight) && unit_weight >= 0.0;
        }

        bool qualifies(std::int32_t units) const
        {
            return units >= 0 && (unit_value == 0 || units <= INT32_MAX / unit_value) &&
                std::isfinite(units * unit_weight);
        }

        void set_totals(void* row, std::int32_t units) const
        {
            // Stock FL_Items.StackItem_Stats recomputes both totals from the ammo CDO.
            value->SetPropertyValueInContainer(row, units * unit_value);
            weight->SetPropertyValueInContainer(row, units * unit_weight);
        }

        bool totals_match(const void* row, std::int32_t units) const
        {
            return value->GetPropertyValueInContainer(row) == units * unit_value &&
                weight->GetPropertyValueInContainer(row) == units * unit_weight;
        }
    };

    int seed(const Lua& lua, const Context& context, const Inventory& inventory, Unreal::UClass* item_class, bool ammo_fixture)
    {
        if (inventory.item && !ammo_fixture) return result(lua, true, "present");
        auto* source = item_class->GetClassDefaultObject().Get();
        auto* quantity = field<Unreal::FIntProperty>(source, ammo_fixture ? STR("Ammo") : STR("MagSize"));
        const auto capacity = quantity ? quantity->GetPropertyValueInContainer(source) : -1;
        constexpr auto ammo_fixture_size = 9;
        const auto units = ammo_fixture ? ammo_fixture_size : magazine_fixture;
        if (!source || !source->HasAnyFlags(Unreal::RF_ClassDefaultObject) || source->GetClassPrivate() != item_class ||
            (ammo_fixture ? capacity != units || !boolean(source, STR("Stacks"), true) : capacity < units))
            return result(lua, false, "stock_guest_weapon_seed_schema_unavailable");
        AmmoStackSchema stack;
        if (ammo_fixture && (!stack.read(item_class, inventory) || !stack.qualifies(units)))
            return result(lua, false, "stock_guest_ammo_seed_schema_unavailable");
        if (inventory.item)
        {
            const bool present = inventory.backpack_index >= 0 &&
                inventory.ammo->GetPropertyValueInContainer(inventory.item) == units &&
                stack.totals_match(inventory.item, units);
            return result(lua, present, present ? "present" : "guest_ammo_fixture_guid_conflict");
        }
        auto* library = find<Unreal::UObject>(library_path);
        auto* converter = find<Unreal::UFunction>(converter_path);
        constexpr std::size_t buffer_size = 256;
        if (!library || !converter ||
            library->GetClassPrivate() != converter->GetOuterPrivate() || converter->GetNumParms() != 3 ||
            converter->GetParmsSize() > buffer_size) return result(lua, false, "stock_guest_weapon_seed_schema_unavailable");

        auto* item = input<Unreal::FObjectProperty>(converter, STR("ItemActor"), 0);
        auto* world = input<Unreal::FObjectProperty>(converter, STR("__WorldContext"), sizeof(void*));
        auto* out = field<Unreal::FStructProperty>(converter, STR("Item"));
        if (!item || !world || !out || !out->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm) ||
            out->GetStruct().Get() != inventory.type->GetStruct().Get() || out->GetOffset_Internal() != 2 * sizeof(void*) ||
            out->GetOffset_Internal() + out->GetSize() > converter->GetParmsSize())
            return result(lua, false, "stock_guest_weapon_converter_schema_unavailable");

        Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
        if (backpack.Num() >= max_inventory) return result(lua, false, "guest_weapon_backpack_full");
        alignas(std::max_align_t) std::array<std::byte, buffer_size> parameters{};
        auto* storage = parameters.data();
        out->InitializeValue_InContainer(storage);
        item->SetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(storage), source);
        world->SetObjectPropertyValue(world->ContainerPtrToValuePtr<void>(storage), context.pawn);
        library->ProcessEvent(converter, storage);
        auto* generated = out->ContainerPtrToValuePtr<void>(storage);
        const bool valid = ownership_current(context) && inventory.row_class(generated) == item_class &&
            inventory.identity(generated) != Guid{} &&
            (!ammo_fixture || inventory.ammo->GetPropertyValueInContainer(generated) == units);
        if (valid)
        {
            inventory.set_identity(generated, context.guid);
            inventory.ammo->SetPropertyValueInContainer(generated, units);
            if (ammo_fixture) stack.set_totals(generated, units);
            inventory.type->CopyCompleteValue(backpack.GetRawPtr(backpack.AddValue()), generated);
        }
        out->DestroyValue_InContainer(storage);
        if (!valid) return result(lua, false, "stock_guest_weapon_conversion_unverified");

        Inventory after;
        const bool proof = locate(context, after, item_class).empty() && after.backpack_index >= 0 &&
            after.ammo->GetPropertyValueInContainer(after.item) == units &&
            (!ammo_fixture || stack.totals_match(after.item, units));
        return result(lua, proof, proof ? "seeded" : "mutation_unverified_guest_weapon_seed");
    }

    int equip(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        if (equipped(context, inventory)) return result(lua, true, "equipped");
        if (!ready_pawn(context)) return result(lua, false, "guest_weapon_pawn_ineligible");

        struct Parameters { std::int32_t index{}; bool prev{}; } parameters;
        auto* action = find<Unreal::UFunction>(equip_path);
        auto* index = input<Unreal::FIntProperty>(action, STR("Index"), offsetof(Parameters, index));
        auto* prev = input<Unreal::FBoolProperty>(action, STR("Prev"), offsetof(Parameters, prev));
        if (!index || !prev || action->GetOuterPrivate() != context.pawn->GetClassPrivate() || action->GetNumParms() != 2 ||
            action->GetReturnProperty() || action->GetParmsSize() < offsetof(Parameters, prev) + 1 ||
            action->GetParmsSize() > sizeof(Parameters))
            return result(lua, false, "stock_guest_weapon_equip_schema_unavailable");
        const auto loaded = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        auto* defaults = context.weapon->GetClassDefaultObject().Get();
        auto* magazine = field<Unreal::FIntProperty>(defaults, STR("MagSize"));
        if (!magazine || loaded < 1 || loaded > magazine->GetPropertyValueInContainer(defaults))
            return result(lua, false, "guest_weapon_magazine_unqualified");

        const auto previous = inventory.selected->GetPropertyValueInContainer(context.save);
        auto* old = object(context.pawn, STR("EquipedItem"));
        auto target = inventory.equipment_index;
        if (target == previous && live(old)) return result(lua, false, "guest_weapon_selected_actor_conflict");
        Unreal::FScriptArrayHelper equipment(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        if (live(old))
        {
            if (previous < 0 || previous >= equipment.Num() || !authority(old) ||
                object(old, STR("Owner")) != context.pawn || object(old, STR("Instigator")) != context.pawn ||
                inventory.row_class(equipment.GetRawPtr(previous)) != old->GetClassPrivate())
                return result(lua, false, "guest_weapon_previous_actor_conflict");
        }
        if (target < 0)
        {
            for (int slot = 0; slot < equipment.Num(); ++slot)
            {
                auto* row = equipment.GetRawPtr(slot);
                if (slot != previous && !inventory.row_class(row) && inventory.identity(row) == Guid{})
                {
                    target = slot;
                    break;
                }
            }
            if (target < 0) return result(lua, false, "guest_weapon_empty_slot_required");
            Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
            inventory.type->CopyCompleteValue(equipment.GetRawPtr(target), inventory.item);
            backpack.RemoveValues(inventory.backpack_index, 1);
        }
        index->SetPropertyValueInContainer(&parameters, target);
        prev->SetPropertyValueInContainer(&parameters, false);
        context.pawn->ProcessEvent(action, &parameters);

        Inventory after;
        auto* actor = ownership_current(context) && locate(context, after).empty() ? equipped(context, after) : nullptr;
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        const bool proven = ammo && after.equipment_index == target &&
            ammo->GetPropertyValueInContainer(actor) == loaded && after.ammo->GetPropertyValueInContainer(after.item) == loaded;
        return result(lua, proven, proven ? "equipped" : "mutation_unverified_guest_weapon_equip");
    }

    int inspect(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        const auto stored = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (inventory.backpack_index >= 0) return proof(lua, true, "backpack", -1, stored);
        auto* actor = equipped(context, inventory);
        if (!actor) return proof(lua, true, "stored_equipment", -1, stored);
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        if (!ammo) return proof(lua, false, "stock_guest_weapon_ammo_unavailable");
        return proof(lua, true, "equipped", ammo->GetPropertyValueInContainer(actor), stored);
    }

    Unreal::UClass* single_stock_projectile(Unreal::UObject* actor)
    {
        auto* ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* type = ammo ? ammo->GetStruct().Get() : nullptr;
        auto* count = field<Unreal::FIntProperty>(type, STR("Projectile_Count_12_A3D51CC4418048DC7C98AB8023D0AF39"));
        auto* klass = field<Unreal::FClassProperty>(type, STR("Base_Projectile_6_619A2F0C42DB644A02A7B4B4594BC74A"));
        auto* projectile_base = find<Unreal::UClass>(projectile_path);
        if (!count || !klass || !projectile_base) return nullptr;
        auto* data = ammo->ContainerPtrToValuePtr<void>(actor);
        auto* projectile = static_cast<Unreal::UClass*>(klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(data)));
        return count->GetPropertyValueInContainer(data) == 1 && projectile && projectile->IsChildOf(projectile_base)
            ? projectile : nullptr;
    }

    struct ProjectileSnapshot
    {
        std::array<Unreal::FWeakObjectPtr, max_observed_projectiles> previous;
        std::size_t count{};

        bool capture(const Context& context, Unreal::UClass* type)
        {
            bool complete = true;
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
                if (!matches(context, type, candidate)) return RC::LoopAction::Continue;
                if (count == previous.size())
                {
                    complete = false;
                    return RC::LoopAction::Break;
                }
                previous[count++] = candidate;
                return RC::LoopAction::Continue;
            });
            return complete;
        }

        bool proves_one(const Context& context, Unreal::UClass* type) const
        {
            int spawned = 0;
            bool owned = false;
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
                if (!matches(context, type, candidate)) return RC::LoopAction::Continue;
                const auto end = previous.begin() + static_cast<std::ptrdiff_t>(count);
                if (std::find_if(previous.begin(), end, [candidate](const Unreal::FWeakObjectPtr& old) {
                        return old.Get() == candidate;
                    }) != end) return RC::LoopAction::Continue;

                ++spawned;
                owned = object(candidate, STR("Owner")) == context.pawn && object(candidate, STR("Instigator")) == context.pawn;
                return spawned == 1 && owned ? RC::LoopAction::Continue : RC::LoopAction::Break;
            });
            return spawned == 1 && owned;
        }

    private:
        static bool matches(const Context& context, Unreal::UClass* type, Unreal::UObject* candidate)
        {
            return candidate->GetClassPrivate() == type && authority(candidate) &&
                   static_cast<Unreal::AActor*>(candidate)->GetWorld() == context.world &&
                   boolean(candidate, STR("bActorIsBeingDestroyed"), false);
        }
    };

    int fire(const Lua& lua, const Context& context, const Inventory& inventory, bool disarm)
    {
        const auto report = [&](bool ok, std::string_view status, std::int32_t first = -1, std::int32_t second = -1,
                                int projectiles = 0) {
            return proof(lua, ok, status, first, second, disarm ? -1 : projectiles);
        };
        auto* actor = equipped(context, inventory);
        if (!actor) return report(false, "guest_weapon_actor_not_equipped");

        const Unreal::FName use_name{STR("Use_Item_Primary")};
        auto* action = find<Unreal::UFunction>(primary_path);
        struct Parameters { bool held{}; double variance{}; } parameters;
        auto* held = input<Unreal::FBoolProperty>(action, STR("Held"), offsetof(Parameters, held));
        auto* variance = input<Unreal::FDoubleProperty>(action, STR("VarianceScaler"), offsetof(Parameters, variance));
        auto* proxy = field<Unreal::FObjectProperty>(actor, STR("InputIntercept"));
        if (!held || !variance || !proxy || object(actor, STR("InputIntercept")) ||
            actor->GetFunctionByNameInChain(use_name) != action || action->GetNumParms() != 2 ||
            action->GetReturnProperty() || action->GetParmsSize() != sizeof(Parameters))
            return report(false, "stock_guest_weapon_primary_schema_unavailable");

        if (!boolean(actor, STR("Action_OnRelease"), false))
            return report(false, "guest_weapon_release_mode_unqualified");
        if (!disarm &&
            (!ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
             !boolean(actor, STR("Reloading"), false) || !boolean(actor, STR("IsCycling"), false) ||
             !boolean(actor, STR("Holding_Primary"), false)))
            return report(false, "guest_weapon_primary_ineligible");

        auto* live_ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        auto* shot_cost = field<Unreal::FIntProperty>(actor, STR("AmmoCost_PerShot"));
        auto* magazine = field<Unreal::FIntProperty>(actor, STR("MagSize"));
        if (!live_ammo || !shot_cost || !magazine) return report(false, "stock_guest_weapon_ammo_unavailable");
        const auto before = live_ammo->GetPropertyValueInContainer(actor);
        const auto stored = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        auto* projectile = disarm ? nullptr : single_stock_projectile(actor);
        if (!disarm && (before != stored || before < 2 || before > magazine->GetPropertyValueInContainer(actor) ||
                        shot_cost->GetPropertyValueInContainer(actor) != 1 || !projectile))
            return report(false, "guest_weapon_preloaded_magazine_required");

        Unreal::FWeakObjectPtr held_actor;
        held_actor = actor;
        ProjectileSnapshot projectiles;
        if (!disarm)
        {
            if (!projectiles.capture(context, projectile)) return report(false, "guest_weapon_projectile_scope_full");
            held->SetPropertyValueInContainer(&parameters, true);
            actor->ProcessEvent(action, &parameters);
        }
        if (held_actor.Get() != actor || !live(actor))
            return report(false, "mutation_unverified_guest_weapon_actor_lost", before);
        // Always release the verified live actor before deciding whether the shot produced a valid receipt.
        held->SetPropertyValueInContainer(&parameters, false);
        actor->ProcessEvent(action, &parameters);

        Inventory after;
        if (!ownership_current(context) || !locate(context, after).empty() || equipped(context, after) != actor ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false))
            return report(false, "mutation_unverified_guest_weapon_release", before);
        const auto remaining = live_ammo->GetPropertyValueInContainer(actor);
        if (disarm) return report(true, "disarmed", before, remaining);
        if (!projectiles.proves_one(context, projectile))
            return report(false, "mutation_unverified_guest_weapon_projectile", before, remaining);
        const auto after_stored = after.ammo->GetPropertyValueInContainer(after.item);
        if (remaining != before - 1 || (after_stored != stored && after_stored != remaining))
            return report(false, "mutation_unverified_guest_weapon_shot", before, remaining, 1);

        after.ammo->SetPropertyValueInContainer(after.item, remaining);
        return after.ammo->GetPropertyValueInContainer(after.item) == remaining
            ? report(true, "fired", before, remaining, 1)
            : report(false, "mutation_unverified_guest_weapon_ammo_commit", before, remaining, 1);
    }

    struct ReserveStack
    {
        Guid guid{};
        int index{};
        std::int32_t units{};
        std::int32_t debit{};
    };

    bool snapshot_reserve(const Context& context, const Inventory& inventory, Unreal::UClass* ammo_class,
                          std::vector<ReserveStack>& stacks, std::int32_t& total)
    {
        Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
        Unreal::FScriptArrayHelper equipment(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        if (backpack.Num() > max_inventory || equipment.Num() > max_inventory) return false;
        std::vector<Guid> identities;
        identities.reserve(static_cast<std::size_t>(backpack.Num() + equipment.Num() + 2));
        for (int index = 0; index < backpack.Num(); ++index)
        {
            const auto* row = backpack.GetRawPtr(index);
            const auto guid = inventory.identity(row);
            identities.push_back(guid);
            if (inventory.row_class(row) != ammo_class) continue;
            const auto units = inventory.ammo->GetPropertyValueInContainer(row);
            if (guid == Guid{} || units <= 0 || units > INT32_MAX - total) return false;
            stacks.push_back({guid, index, units});
            total += units;
        }
        for (int index = 0; index < equipment.Num(); ++index)
            identities.push_back(inventory.identity(equipment.GetRawPtr(index)));
        identities.push_back(inventory.identity(inventory.armor->ContainerPtrToValuePtr<void>(context.save)));
        identities.push_back(inventory.identity(inventory.helmet->ContainerPtrToValuePtr<void>(context.save)));
        std::ranges::sort(identities);
        return std::ranges::all_of(stacks, [&](const ReserveStack& stack) {
            const auto [first, last] = std::ranges::equal_range(identities, stack.guid);
            return last - first == 1;
        });
    }

    int reload(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        auto* actor = equipped(context, inventory);
        if (!actor) return reload_proof(lua, false, "guest_weapon_actor_not_equipped");
        if (!ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
            !boolean(actor, STR("Reloading"), false) || !boolean(actor, STR("IsCycling"), false) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !boolean(actor, STR("SingleReload"), false) || !boolean(actor, STR("UseEatherAsAmmo"), false))
            return reload_proof(lua, false, "guest_weapon_reload_ineligible");

        auto* rounds = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        auto* capacity = field<Unreal::FIntProperty>(actor, STR("MagSize"));
        auto* current_ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* ammo_type = current_ammo ? current_ammo->GetStruct().Get() : nullptr;
        auto* reserve_type = field<Unreal::FClassProperty>(ammo_type, STR("Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582"));
        auto* update = find<Unreal::UFunction>(update_mag_path);
        const Unreal::FName update_name{STR("Update_Mag")};
        if (!rounds || !capacity || !ammo_type || ammo_type != find<Unreal::UScriptStruct>(ammo_type_path) ||
            !reserve_type || !update || actor->GetFunctionByNameInChain(update_name) != update ||
            update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty())
            return reload_proof(lua, false, "stock_guest_weapon_reload_schema_unavailable");

        auto* ammo_data = current_ammo->ContainerPtrToValuePtr<void>(actor);
        auto* ammo_class = static_cast<Unreal::UClass*>(
            reserve_type->GetObjectPropertyValue(reserve_type->ContainerPtrToValuePtr<void>(ammo_data)));
        AmmoStackSchema stack_schema;
        if (!stack_schema.read(ammo_class, inventory))
            return reload_proof(lua, false, "stock_guest_weapon_reserve_schema_unavailable");

        const auto before = rounds->GetPropertyValueInContainer(actor);
        const auto cap = capacity->GetPropertyValueInContainer(actor);
        if (cap <= 0 || before < 0 || before > cap || before != inventory.ammo->GetPropertyValueInContainer(inventory.item))
            return reload_proof(lua, false, "guest_weapon_magazine_unqualified", before, before);

        std::int32_t reserve_before{};
        std::vector<ReserveStack> stacks;
        if (!snapshot_reserve(context, inventory, ammo_class, stacks, reserve_before))
            return reload_proof(lua, false, "guest_weapon_reserve_unqualified", before, before);
        if (before == cap)
            return reload_proof(lua, false, "magazine_full", before, before, reserve_before, reserve_before);
        if (reserve_before == 0)
            return reload_proof(lua, false, "no_matching_guest_ammo", before, before, 0, 0);

        const auto transferred = std::min(cap - before, reserve_before);
        auto needed = transferred;
        for (auto& stack : stacks)
        {
            stack.debit = std::min(needed, stack.units);
            needed -= stack.debit;
            if (stack.debit > 0 && !stack_schema.qualifies(stack.units - stack.debit))
                return reload_proof(lua, false, "guest_weapon_reserve_unqualified", before, before, reserve_before, reserve_before);
        }
        if (needed != 0 || !ownership_current(context))
            return reload_proof(lua, false, "guest_weapon_reload_precondition_changed", before, before, reserve_before, reserve_before);

        // Publish the detached reserve and magazine in one game-thread turn before any stock callback.
        const auto refilled = before + transferred;
        Unreal::FScriptArrayHelper backpack(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
        for (auto it = stacks.rbegin(); it != stacks.rend(); ++it)
        {
            if (it->debit == 0) continue;
            const auto remaining = it->units - it->debit;
            if (remaining == 0)
            {
                backpack.RemoveValues(it->index, 1);
                continue;
            }
            auto* row = backpack.GetRawPtr(it->index);
            inventory.ammo->SetPropertyValueInContainer(row, remaining);
            stack_schema.set_totals(row, remaining);
        }
        inventory.ammo->SetPropertyValueInContainer(inventory.item, refilled);
        rounds->SetPropertyValueInContainer(actor, refilled);
        const Unreal::FWeakObjectPtr held_actor{actor};
        actor->ProcessEvent(update, nullptr);

        Inventory after;
        if (held_actor.Get() != actor || !ownership_current(context) || !locate(context, after).empty() ||
            equipped(context, after) != actor)
            return reload_proof(lua, false, "mutation_unverified_guest_weapon_reload_owner", before, -1, reserve_before);

        const auto observed = rounds->GetPropertyValueInContainer(actor);
        std::int32_t reserve_after{};
        std::vector<ReserveStack> remaining_stacks;
        if (observed != refilled || after.ammo->GetPropertyValueInContainer(after.item) != observed ||
            !snapshot_reserve(context, after, ammo_class, remaining_stacks, reserve_after) ||
            reserve_after != reserve_before - transferred)
            return reload_proof(lua, false, "mutation_unverified_guest_weapon_reload_commit", before, observed, reserve_before);

        auto surviving = stacks | std::views::filter([](const ReserveStack& stack) { return stack.units != stack.debit; });
        const bool proven = std::ranges::equal(surviving, remaining_stacks,
            [&](const ReserveStack& expected, const ReserveStack& actual) {
                return expected.guid == actual.guid && expected.units - expected.debit == actual.units &&
                    (expected.debit == 0 || stack_schema.totals_match(backpack.GetRawPtr(actual.index), actual.units));
            });
        return reload_proof(lua, proven, proven ? "reloaded" : "mutation_unverified_guest_weapon_reload_stacks",
                            before, observed, reserve_before, reserve_after);
    }

    template <Operation operation>
    int run(const Lua& lua)
    {
        constexpr bool reports_ammo = operation == Operation::Inspect || operation == Operation::Fire ||
                                      operation == Operation::Disarm;
        const auto fail = [&](std::string_view error) {
            if constexpr (operation == Operation::Reload) return reload_proof(lua, false, error);
            return reports_ammo ? proof(lua, false, error, -1, -1, operation == Operation::Fire ? 0 : -1)
                                : result(lua, false, error);
        };
        Context context;
        if (const auto error = resolve(lua, context); !error.empty()) return fail(error);
        auto* expected_class = context.weapon;
        if constexpr (operation == Operation::SeedAmmo)
        {
            expected_class = find<Unreal::UClass>(ammo_fixture_path);
            if (!expected_class) return fail("stock_guest_ammo_fixture_class_unavailable");
        }
        Inventory inventory;
        if (const auto error = locate(context, inventory, expected_class); !error.empty()) return fail(error);
        if constexpr (operation == Operation::Seed || operation == Operation::SeedAmmo)
        {
            return seed(lua, context, inventory, expected_class, operation == Operation::SeedAmmo);
        }
        else
        {
            if (!inventory.item) return fail("owned_guest_weapon_guid_required");
            if constexpr (operation == Operation::Equip) return equip(lua, context, inventory);
            if constexpr (operation == Operation::Inspect) return inspect(lua, context, inventory);
            if constexpr (operation == Operation::Reload) return reload(lua, context, inventory);
            return fire(lua, context, inventory, operation == Operation::Disarm);
        }
    }
}

void silverfish::native_guest_item::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!env("SILVERFISH_COOP_NATIVE_FIXTURES")) return;
    lua.register_function("SilverfishSeedNativeGuestWeapon", run<Operation::Seed>);
    lua.register_function("SilverfishSeedNativeGuestAmmo", run<Operation::SeedAmmo>);
    lua.register_function("SilverfishEquipNativeGuestWeapon", run<Operation::Equip>);
    lua.register_function("SilverfishInspectNativeGuestWeapon", run<Operation::Inspect>);
    lua.register_function("SilverfishFireNativeGuestWeapon", run<Operation::Fire>);
    lua.register_function("SilverfishDisarmNativeGuestWeapon", run<Operation::Disarm>);
    lua.register_function("SilverfishReloadNativeGuestWeapon", run<Operation::Reload>);
}
