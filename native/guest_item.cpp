#include "guest_item.hpp"
#include "guest_melee.hpp"
#include "guest_weapon_registry.hpp"
#include "interaction_input.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <map>
#include <memory>
#include <ranges>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/Engine/UDataTable.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Guid = std::array<std::uint32_t, 4>;
    namespace profiles = silverfish::guest_weapon_profiles;
    namespace Hook = Unreal::Hook;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto smg_path = STR("/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C");
    constexpr auto pistol_path = STR("/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C");
    struct BoltProfile
    {
        const Unreal::TCHAR* path;
        const Unreal::TCHAR* primary;
        double fire_rate;
    };
    constexpr BoltProfile stock_bolts[]{
        {STR("/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C"),
         STR("/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C:Use_Item_Primary"), 1.0},
        {STR("/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C"),
         STR("/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C:Use_Item_Primary"), 1.5},
    };
    constexpr auto rifle_fmj_path = STR("/Game/Classes/Items/Ammo_FullRifle_BP.Ammo_FullRifle_BP_C");
    constexpr auto rifle_incendiary_path = STR("/Game/Classes/Items/Ammo_30-06_Incendiary_BP.Ammo_30-06_Incendiary_BP_C");
    constexpr auto rifle_fmj_projectile = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Projectile_Rifle_Heavy.Projectile_Rifle_Heavy_C");
    constexpr auto rifle_incendiary_projectile =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Projectile_30-06_Incendiary.Projectile_30-06_Incendiary_C");
    constexpr auto ammo_table_path = STR("/Game/Classes/Libraries/DataTables/AmmoTypes_DT.AmmoTypes_DT");
    constexpr auto change_ammo_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:ChangeAmmoType");
    constexpr int bolt_magazine = 5;
    constexpr int rifle_caliber = 3;
    constexpr auto camera_path = STR("/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C");
    constexpr auto blowtorch_path = STR("/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C");
    constexpr auto crowbar_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C");
    constexpr std::int32_t blowtorch_capacity = 3;
    constexpr auto equipment_base_path = STR("/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C");
    constexpr auto grenade_base_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C");
    constexpr auto grenade_spawn_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C:SpawnNade");
    constexpr auto grenade_throw_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C:ThrowNade");
    struct ThrowableProfile
    {
        const Unreal::TCHAR* equipment;
        const Unreal::TCHAR* projectile;
        std::string_view fixture_kind;
    };
    constexpr ThrowableProfile throwables[]{
        {STR("/Game/Classes/Equipment/Eqp_Flare.Eqp_Flare_C"),
         STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Flare_Projectile_BP.Flare_Projectile_BP_C"), "stock-flare"},
        {STR("/Game/Classes/Equipment/Eqp_Flare_Blue.Eqp_Flare_Blue_C"),
         STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Blue_Flare_Projectile_BP.Blue_Flare_Projectile_BP_C"), "stock-flare-blue"},
        {STR("/Game/Classes/Equipment/Eqp_Flare_Yellow.Eqp_Flare_Yellow_C"),
         STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Yellow_Flare_Projectile_BP.Yellow_Flare_Projectile_BP_C"), "stock-flare-yellow"},
        {STR("/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C"),
         STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Bolt_Projectile_BP.Bolt_Projectile_BP_C"), "stock-bolt"},
    };
    constexpr auto old_pipe_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C");
    constexpr auto weapon_base_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C");
    constexpr auto ammo_master_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto ammo_type_path = STR("/Game/Classes/Libraries/Structs/AmmoType_Struct.AmmoType_Struct");
    constexpr auto projectile_path =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto primary_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Use_Item_Primary");
    constexpr auto update_mag_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Update_Mag");
    constexpr auto equip_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Equip_NextItem");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto sort_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:SortItemArray");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto class_name = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto ammo_name = STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788");
    constexpr auto weight_name = STR("Weight_12_11AB9ED547504E78A849A692A36DC938");
    constexpr auto payload_name = STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59");
    constexpr auto ammo_item_name = STR("Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582");
    constexpr auto ammo_caliber_name = STR("Caliber_3_3C88B25D42998F32221AA0AA7E1B4537");
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

    struct StackSplit
    {
        Guid created{};
        std::int32_t take{};
        std::int32_t expected{};
    };

    struct SeedSelection
    {
        const Unreal::TCHAR* path{smg_path};
        bool full_magazine{};
    };

    bool ownership_current(const Context& context)
    {
        if (!authority(context.pawn) || !authority(context.controller) || !live(context.connection) ||
            !boolean(context.pawn, STR("bActorIsBeingDestroyed"), false) ||
            !boolean(context.controller, STR("bActorIsBeingDestroyed"), false) ||
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
        if (!live(driver) || !field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) ||
            object(driver, STR("ServerConnection")) || object(driver, STR("World")) != context.world || !connection ||
            object(context.connection, STR("Driver")) != driver ||
            object(context.connection, STR("PlayerController")) != context.controller ||
            object(context.connection, STR("OwningActor")) != context.controller) return false;
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

    std::string_view resolve_owner(Context& context, std::int64_t pawn_address, std::int64_t save_address)
    {
        auto* pawn_class = find<Unreal::UClass>(pawn_path);
        auto* save_class = find<Unreal::UClass>(save_path);
        auto* instance_class = find<Unreal::UClass>(instance_path);
        auto* controller_class = find<Unreal::UClass>(controller_path);
        auto* connection_class = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        if (!pawn_class || !save_class || !instance_class || !controller_class || !connection_class)
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
            !live(context.controller) || context.controller->GetClassPrivate() != controller_class ||
            !live(context.connection) || !context.connection->IsA(connection_class) || !ownership_current(context))
            return "authenticated_guest_detached_save_required";
        return {};
    }

    std::string_view resolve(const Lua& lua, Context& context, SeedSelection* seed_selection,
                             std::int64_t* hold_duration = nullptr, StackSplit* split = nullptr, bool full_split = false,
                             int* requested_slot = nullptr)
    {
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE")) return "native_guest_weapon_scope_disabled";
        const auto count = lua.get_stack_size();
        const bool split_fields = split && count == 6 && lua.is_string(4) && lua.is_integer(5) && lua.is_integer(6);
        const bool legacy_fields = count == 3 || (count == 4 && ((seed_selection && lua.is_string(4)) ||
            ((hold_duration || requested_slot) && lua.is_integer(4))));
        if (!Unreal::IsInGameThreadRaw() || (split ? !split_fields : !legacy_fields) || (hold_duration && count != 4) ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3)) return "guest_pawn_save_and_guid_required";
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        if (pawn_address <= 0 || save_address <= 0 || !parse_guid(lua.get_string(), context.guid))
            return "guest_pawn_save_and_guid_required";
        if (split)
        {
            if (!parse_guid(lua.get_string(), split->created) || split->created == context.guid)
                return "native_inventory_split_refused_guid";
            const auto take = lua.get_integer();
            const auto expected = lua.get_integer();
            if (take < 1 || (full_split ? expected != take : expected <= take) || expected > INT32_MAX)
                return "native_inventory_split_refused_quantity";
            split->take = static_cast<std::int32_t>(take);
            split->expected = static_cast<std::int32_t>(expected);
        }
        else if (hold_duration)
        {
            *hold_duration = lua.get_integer();
            if (*hold_duration < 250 || *hold_duration > 2400) return "native_guest_smg_hold_duration_unqualified";
        }
        else if (requested_slot && count == 4)
        {
            const auto slot = lua.get_integer();
            if (slot < 0 || slot >= 8) return "native_inventory_equip_refused_slot";
            *requested_slot = static_cast<int>(slot);
        }
        else if (count == 4)
        {
            if (!env("SILVERFISH_COOP_HEADLESS")) return "stock_guest_weapon_fixture_profile_unqualified";
            const auto kind = lua.get_string();
            if (kind == "blowtorch") seed_selection->path = blowtorch_path;
            else if (kind == "crowbar") seed_selection->path = crowbar_path;
            else if (const auto throwable = std::ranges::find(throwables, kind, &ThrowableProfile::fixture_kind);
                     throwable != std::end(throwables))
                seed_selection->path = throwable->equipment;
            else
            {
                seed_selection->full_magazine = kind == "smg-standard-full";
                const std::string_view profile_kind = seed_selection->full_magazine ? "smg-standard" : kind;
                const auto profile = std::ranges::find(profiles::firearms, profile_kind, &profiles::Profile::kind);
                if (profile == profiles::firearms.end() || !profile->headless_seed)
                    return "stock_guest_weapon_fixture_profile_unqualified";
                seed_selection->path = profile->path;
            }
        }

        return resolve_owner(context, pawn_address, save_address);
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

    std::string_view bind_inventory(const Context& context, Inventory& inventory)
    {
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
        return {};
    }

    std::string_view locate(const Context& context, Inventory& inventory, Unreal::UClass* expected_class = nullptr)
    {
        if (const auto error = bind_inventory(context, inventory); !error.empty()) return error;
        if (!expected_class) expected_class = context.weapon;
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
                if (inventory.item || (expected_class && inventory.row_class(row) != expected_class)) return false;
                inventory.item = row;
                index = i;
            }
            return true;
        };
        return scan(inventory.backpack, inventory.backpack_index) && scan(inventory.equipment, inventory.equipment_index)
            ? "" : "guest_weapon_guid_conflict";
    }

    const BoltProfile* stock_bolt(Unreal::UClass* weapon)
    {
        if (!weapon) return nullptr;
        for (const auto& profile : stock_bolts)
            if (weapon == find<Unreal::UClass>(profile.path)) return &profile;
        return nullptr;
    }

    bool stock_bolt_shape(Unreal::UObject* weapon, const BoltProfile& profile)
    {
        auto* magazine = field<Unreal::FIntProperty>(weapon, STR("MagSize"));
        auto* caliber = field<Unreal::FByteProperty>(weapon, STR("Caliber"));
        auto* cost = field<Unreal::FIntProperty>(weapon, STR("AmmoCost_PerShot"));
        auto* cycle = field<Unreal::FDoubleProperty>(weapon, STR("FireRate"));
        return magazine && magazine->GetPropertyValueInContainer(weapon) == bolt_magazine &&
            caliber && caliber->GetPropertyValueInContainer(weapon) == rifle_caliber &&
            cost && cost->GetPropertyValueInContainer(weapon) == 1 &&
            cycle && cycle->GetPropertyValueInContainer(weapon) == profile.fire_rate &&
            boolean(weapon, STR("Action_OnRelease"), true) && boolean(weapon, STR("SingleReload"), true) &&
            boolean(weapon, STR("UseEatherAsAmmo"), false);
    }

    const profiles::Profile* stock_magazine(Unreal::UClass* weapon)
    {
        if (!weapon) return nullptr;
        for (const auto& profile : profiles::firearms)
            if (profile.capacity > 0 && weapon == find<Unreal::UClass>(profile.path)) return &profile;
        return nullptr;
    }

    bool stock_magazine_shape(Unreal::UObject* weapon, const profiles::Profile& profile)
    {
        auto* magazine = field<Unreal::FIntProperty>(weapon, STR("MagSize"));
        auto* burst = field<Unreal::FIntProperty>(weapon, STR("BurstLength"));
        auto* caliber = field<Unreal::FByteProperty>(weapon, STR("Caliber"));
        auto* cost = field<Unreal::FIntProperty>(weapon, STR("AmmoCost_PerShot"));
        auto* rate = field<Unreal::FDoubleProperty>(weapon, STR("FireRate"));
        return profile.ammo && magazine && magazine->GetPropertyValueInContainer(weapon) == profile.capacity &&
            burst && burst->GetPropertyValueInContainer(weapon) == profile.burst_length &&
            caliber && caliber->GetPropertyValueInContainer(weapon) == profile.ammo->caliber &&
            cost && cost->GetPropertyValueInContainer(weapon) == 1 &&
            rate && rate->GetPropertyValueInContainer(weapon) == profile.fire_rate &&
            field<Unreal::FObjectProperty>(weapon, STR("InputIntercept")) && !object(weapon, STR("InputIntercept")) &&
            boolean(weapon, STR("Action_OnRelease"), false) && boolean(weapon, STR("SingleReload"), false) &&
            boolean(weapon, STR("UseEatherAsAmmo"), false);
    }

    bool mounted_payload_matches(Unreal::UObject* actor, const Inventory& inventory)
    {
        auto* actor_field = field<Unreal::FStructProperty>(actor, STR("DataStruct"));
        auto* row_field = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        auto* type = find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
        return type && actor_field && row_field && actor_field->GetStruct().Get() == type &&
            row_field->GetStruct().Get() == type && actor_field->Identical(actor_field->ContainerPtrToValuePtr<void>(actor),
                row_field->ContainerPtrToValuePtr<void>(inventory.item));
    }

    Unreal::UDataTable* stock_ammo_table()
    {
        auto* object = find<Unreal::UObject>(ammo_table_path);
        auto* klass = find<Unreal::UClass>(STR("/Script/Engine.DataTable"));
        auto* row = find<Unreal::UScriptStruct>(ammo_type_path);
        if (!live(object) || !klass || object->GetClassPrivate() != klass || !row) return nullptr;
        auto* table = static_cast<Unreal::UDataTable*>(object);
        return table->GetRowStruct().Get() == row ? table : nullptr;
    }

    bool ammo_item_row_matches(Unreal::UClass* ammo, const profiles::StockAmmo& profile)
    {
        auto* defaults = ammo ? ammo->GetClassDefaultObject().Get() : nullptr;
        auto* handle_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* handle = field<Unreal::FStructProperty>(defaults, STR("AmmoType"));
        auto* caliber = field<Unreal::FByteProperty>(defaults, STR("Caliber"));
        auto* table = field<Unreal::FObjectProperty>(handle_type, STR("DataTable"));
        auto* name = field<Unreal::FNameProperty>(handle_type, STR("RowName"));
        auto* expected_table = stock_ammo_table();
        const Unreal::FName expected{profile.row, Unreal::FNAME_Find};
        if (!defaults || defaults->GetClassPrivate() != ammo || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            !handle || !handle_type || handle->GetStruct().Get() != handle_type || !table || !name || !expected_table ||
            expected.IsNone() || !caliber || caliber->GetPropertyValueInContainer(defaults) != profile.caliber) return false;
        const auto* row = handle->ContainerPtrToValuePtr<void>(defaults);
        return table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(row)) == expected_table &&
            name->GetPropertyValueInContainer(row) == expected;
    }

    std::string_view magazine_payload_issue(const profiles::Profile* profile, const Context& context,
                                            const Inventory& inventory, Unreal::UObject* actor)
    {
        if (!profile || !profile->require_default_payload) return {};
        auto* defaults = context.weapon->GetClassDefaultObject().Get();
        if (!mounted_payload_matches(defaults, inventory)) return "guest_weapon_saved_round_payload_unqualified";
        if (!actor) return {};
        // The approved receiver's own WeaponData_Struct contains the exact row handle
        // without underbarrel data. SetAmmoType resolves it only after the actor is equipped.
        auto* ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* type = find<Unreal::UScriptStruct>(ammo_type_path);
        if (!profile->ammo || !type || !ammo || ammo->GetStruct().Get() != type)
            return "guest_weapon_round_schema_unqualified";
        if (!mounted_payload_matches(actor, inventory)) return "guest_weapon_equipped_round_payload_unqualified";
        const void* expected = ammo->ContainerPtrToValuePtr<void>(defaults);
        if (profile->ammo != &profiles::nine_mm_ball)
        {
            auto* table = stock_ammo_table();
            const Unreal::FName row{profile->ammo->row, Unreal::FNAME_Find};
            expected = table && !row.IsNone() ? table->FindRowUnchecked(row) : nullptr;
        }
        return expected && ammo->Identical(ammo->ContainerPtrToValuePtr<void>(actor), expected)
            ? std::string_view{} : "guest_weapon_resolved_round_unqualified";
    }

    bool ordinary_magazine_payload(const profiles::Profile* profile, const Context& context, const Inventory& inventory,
                                   Unreal::UObject* actor = nullptr)
    {
        return magazine_payload_issue(profile, context, inventory, actor).empty();
    }

    Unreal::UClass* qualified_weapon(Unreal::UObject* item_class)
    {
        auto* weapon = Unreal::Cast<Unreal::UClass>(item_class);
        auto* base = find<Unreal::UClass>(weapon_base_path);
        if (!live(weapon) || !base || !weapon->IsChildOf(base)) return nullptr;
        if (weapon == find<Unreal::UClass>(smg_path) || weapon == find<Unreal::UClass>(pistol_path)) return weapon;
        if (const auto* profile = stock_magazine(weapon))
        {
            auto* defaults = weapon->GetClassDefaultObject().Get();
            return defaults && defaults->GetClassPrivate() == weapon && defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) &&
                stock_magazine_shape(defaults, *profile) ? weapon : nullptr;
        }
        const auto* bolt = stock_bolt(weapon);
        auto* defaults = bolt ? weapon->GetClassDefaultObject().Get() : nullptr;
        return defaults && defaults->GetClassPrivate() == weapon && defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) &&
            stock_bolt_shape(defaults, *bolt) ? weapon : nullptr;
    }

    Unreal::UClass* qualified_camera(Unreal::UObject* item_class)
    {
        auto* camera = find<Unreal::UClass>(camera_path);
        auto* equipment = find<Unreal::UClass>(equipment_base_path);
        if (!camera || item_class != camera || !equipment || !camera->IsChildOf(equipment)) return nullptr;
        auto* defaults = camera->GetClassDefaultObject().Get();
        auto* item_type = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
        auto* ammo = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
        return defaults && defaults->GetClassPrivate() == camera && defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) &&
            item_type && item_type->GetPropertyValueInContainer(defaults) == 5 &&
            ammo && ammo->GetPropertyValueInContainer(defaults) == -1 && boolean(defaults, STR("Stacks"), false)
            ? camera : nullptr;
    }

    Unreal::UClass* qualified_blowtorch(Unreal::UObject* item_class)
    {
        auto* torch = find<Unreal::UClass>(blowtorch_path);
        auto* equipment = find<Unreal::UClass>(equipment_base_path);
        if (!torch || item_class != torch || !equipment || !torch->IsChildOf(equipment)) return nullptr;
        auto* defaults = torch->GetClassDefaultObject().Get();
        auto* item_type = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
        auto* ammo = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
        return defaults && defaults->GetClassPrivate() == torch && defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) &&
            item_type && item_type->GetPropertyValueInContainer(defaults) == 5 &&
            ammo && ammo->GetPropertyValueInContainer(defaults) == blowtorch_capacity && boolean(defaults, STR("Stacks"), false)
            ? torch : nullptr;
    }

    Unreal::UClass* qualified_melee_item(Unreal::UObject* item_class, const Unreal::TCHAR* path)
    {
        auto* melee = find<Unreal::UClass>(path);
        auto* base = find<Unreal::UClass>(STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Master_BP.MeleeWep_Master_BP_C"));
        if (!melee || item_class != melee || !base || !melee->IsChildOf(base)) return nullptr;
        auto* defaults = melee->GetClassDefaultObject().Get();
        auto* category = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
        auto* ammo = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
        return defaults && defaults->GetClassPrivate() == melee && defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) &&
            category && category->GetPropertyValueInContainer(defaults) == 2 && ammo &&
            ammo->GetPropertyValueInContainer(defaults) == -1 && boolean(defaults, STR("Stacks"), false) ? melee : nullptr;
    }

    Unreal::UClass* qualified_old_pipe(Unreal::UObject* item_class, const Context& context, bool fixture = false)
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || !live(context.world)) return nullptr;
        if (fixture && (!env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") || !env("SILVERFISH_COOP_NATIVE_FIXTURES") ||
                        !env("SILVERFISH_COOP_HEADLESS"))) return nullptr;
        const auto world = context.world->GetPathName();
        if (fixture ? (world != STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P") &&
                       world != STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P")) :
                      !silverfish::native_guest_melee::ordinary_world(context.world)) return nullptr;
        return qualified_melee_item(item_class, old_pipe_path);
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

    bool camera_capture_ready(Unreal::UObject* camera)
    {
        auto* capture = object(camera, STR("SceneCaptureComponent2D"));
        if (!live(capture) || capture->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.SceneCaptureComponent2D")) ||
            capture->GetOuterPrivate() != camera) return false;
        auto* target = object(capture, STR("TextureTarget"));
        auto* fov = field<Unreal::FFloatProperty>(capture, STR("FOVAngle"));
        if (!live(target) || target != find<Unreal::UObject>(STR("/Game/Assets/Textures/VirtualTextures/VT_Camera.VT_Camera")) ||
            target->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.TextureRenderTarget2D")) || !fov) return false;
        const auto angle = fov->GetPropertyValueInContainer(capture);
        return std::isfinite(angle) && angle > 0.0f && angle < 179.0f;
    }

    bool camera_stock_ammo(std::int32_t value)
    {
        // The loose stock actor has -1; stock Shop.GenerateItem writes zero for non-ammo items.
        return value == -1 || value == 0;
    }

    enum class Operation { Seed, SeedAmmo, Equip, EquipOldPipe, Inspect, Fire, Disarm, Reload };

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

        bool source_totals_match(Unreal::UClass* item_class, const void* row, std::int32_t units) const
        {
            if (totals_match(row, units)) return true;
            // MakeDefaultItem copies unit totals for a fresh stock stack,
            // including ammo in a character preset. Accept that exact stock
            // representation and calculate the totals of both resulting rows.
            auto* defaults = item_class->GetClassDefaultObject().Get();
            auto* default_ammo = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
            return default_ammo && default_ammo->GetPropertyValueInContainer(defaults) == units && totals_match(row, 1);
        }
    };

    int seed(const Lua& lua, const Context& context, const Inventory& inventory, Unreal::UClass* item_class,
             const profiles::StockAmmo* ammo_fixture, bool full_magazine)
    {
        if (inventory.item && !ammo_fixture) return result(lua, true, "present");
        auto* source = item_class->GetClassDefaultObject().Get();
        const bool blowtorch = item_class == find<Unreal::UClass>(blowtorch_path);
        const bool crowbar = item_class == find<Unreal::UClass>(crowbar_path);
        const auto class_path = item_class->GetPathName();
        const bool throwable = std::ranges::any_of(throwables, [&](const auto& profile) { return class_path == profile.equipment; });
        auto* quantity = field<Unreal::FIntProperty>(source, ammo_fixture || blowtorch || crowbar || throwable ? STR("Ammo") : STR("MagSize"));
        const auto capacity = quantity ? quantity->GetPropertyValueInContainer(source) : -1;
        const auto* magazine = ammo_fixture ? nullptr : stock_magazine(item_class);
        const auto units = ammo_fixture ? ammo_fixture->fixture_units : blowtorch ? blowtorch_capacity : crowbar ? -1 : throwable ? 2 :
            magazine ? (full_magazine ? magazine->capacity : std::min(magazine_fixture, magazine->capacity)) : magazine_fixture;
        if (!source || !source->HasAnyFlags(Unreal::RF_ClassDefaultObject) || source->GetClassPrivate() != item_class ||
            (magazine && capacity != magazine->capacity) ||
            (ammo_fixture ? capacity != units || !boolean(source, STR("Stacks"), true) :
                throwable ? !quantity || !boolean(source, STR("Stacks"), true) : capacity < units))
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

    struct EquipCall
    {
        struct Parameters { std::int32_t index{}; bool prev{}; } parameters;
        Unreal::UFunction* action = find<Unreal::UFunction>(equip_path);
        Unreal::FIntProperty* index = input<Unreal::FIntProperty>(action, STR("Index"), offsetof(Parameters, index));
        Unreal::FBoolProperty* prev = input<Unreal::FBoolProperty>(action, STR("Prev"), offsetof(Parameters, prev));

        bool valid(Unreal::UObject* pawn) const
        {
            return index && prev && action->GetOuterPrivate() == pawn->GetClassPrivate() && action->GetNumParms() == 2 &&
                !action->GetReturnProperty() && action->GetParmsSize() >= offsetof(Parameters, prev) + 1 &&
                action->GetParmsSize() <= sizeof(Parameters);
        }

        void invoke(Unreal::UObject* pawn, int slot)
        {
            index->SetPropertyValueInContainer(&parameters, slot);
            prev->SetPropertyValueInContainer(&parameters, false);
            pawn->ProcessEvent(action, &parameters);
        }
    };

    int equip(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        const bool camera = context.weapon == find<Unreal::UClass>(camera_path);
        const bool old_pipe = context.weapon == find<Unreal::UClass>(old_pipe_path);
        const bool crowbar = context.weapon == find<Unreal::UClass>(crowbar_path);
        const bool blowtorch = context.weapon == find<Unreal::UClass>(blowtorch_path);
        const auto* magazine_profile = camera || blowtorch ? nullptr : stock_magazine(context.weapon);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory))
            return result(lua, false, "guest_weapon_payload_unqualified");
        if (auto* actor = equipped(context, inventory))
        {
            if (!camera && !old_pipe && !blowtorch && !crowbar)
            {
                const bool verified = ordinary_magazine_payload(magazine_profile, context, inventory, actor);
                return result(lua, verified, verified ? "equipped" : "guest_weapon_payload_unqualified");
            }
            const auto stored = inventory.ammo->GetPropertyValueInContainer(inventory.item);
            auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
            if (blowtorch)
            {
                const bool verified = 0 <= stored && stored <= blowtorch_capacity && ammo &&
                    ammo->GetPropertyValueInContainer(actor) == stored;
                return result(lua, verified, verified ? "blowtorch_equipped" : "stock_guest_blowtorch_fuel_unqualified");
            }
            if (old_pipe || crowbar)
            {
                const bool verified = stored == -1 && ammo && ammo->GetPropertyValueInContainer(actor) == stored;
                if (crowbar) return result(lua, verified, verified ? "crowbar_equipped" : "stock_guest_crowbar_row_unqualified");
                return result(lua, verified, verified ? "old_pipe_equipped" : "stock_guest_old_pipe_row_unqualified");
            }
            const bool verified = camera_stock_ammo(stored) && ammo && ammo->GetPropertyValueInContainer(actor) == stored &&
                camera_capture_ready(actor);
            return result(lua, verified, verified ? "camera_equipped" : "stock_guest_camera_capture_unavailable");
        }
        if (!ready_pawn(context)) return result(lua, false, "guest_weapon_pawn_ineligible");

        EquipCall call;
        if (!call.valid(context.pawn)) return result(lua, false, "stock_guest_weapon_equip_schema_unavailable");
        const auto loaded = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (camera)
        {
            if (!camera_stock_ammo(loaded)) return result(lua, false, "guest_camera_row_unqualified");
        }
        else if (blowtorch)
        {
            if (loaded < 0 || loaded > blowtorch_capacity) return result(lua, false, "stock_guest_blowtorch_fuel_unqualified");
        }
        else if (old_pipe || crowbar)
        {
            if (loaded != -1) return result(lua, false, crowbar ? "stock_guest_crowbar_row_unqualified" :
                "stock_guest_old_pipe_row_unqualified");
        }
        else
        {
            auto* defaults = context.weapon->GetClassDefaultObject().Get();
            auto* magazine = field<Unreal::FIntProperty>(defaults, STR("MagSize"));
            const auto minimum = (stock_bolt(context.weapon) || magazine_profile) ? 0 : 1;
            if (!magazine || loaded < minimum || loaded > magazine->GetPropertyValueInContainer(defaults))
                return result(lua, false, "guest_weapon_magazine_unqualified");
        }

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
        call.invoke(context.pawn, target);

        Inventory after;
        auto* actor = ownership_current(context) && locate(context, after).empty() ? equipped(context, after) : nullptr;
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        const bool proven = ammo && after.equipment_index == target &&
            ammo->GetPropertyValueInContainer(actor) == loaded && after.ammo->GetPropertyValueInContainer(after.item) == loaded &&
            ordinary_magazine_payload(magazine_profile, context, after, actor);
        if (camera)
        {
            const bool verified = proven && camera_capture_ready(actor);
            return result(lua, verified, verified ? "camera_equipped" : "mutation_unverified_guest_camera_equip");
        }
        if (old_pipe)
            return result(lua, proven, proven ? "old_pipe_equipped" : "mutation_unverified_guest_old_pipe_equip");
        if (crowbar)
            return result(lua, proven, proven ? "crowbar_equipped" : "mutation_unverified_guest_crowbar_equip");
        if (blowtorch)
            return result(lua, proven, proven ? "blowtorch_equipped" : "mutation_unverified_guest_blowtorch_equip");
        return result(lua, proven, proven ? "equipped" : "mutation_unverified_guest_weapon_equip");
    }

    int inspect(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        const auto stored = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        const bool camera = context.weapon == find<Unreal::UClass>(camera_path);
        const bool old_pipe = context.weapon == find<Unreal::UClass>(old_pipe_path);
        const bool crowbar = context.weapon == find<Unreal::UClass>(crowbar_path);
        const bool blowtorch = context.weapon == find<Unreal::UClass>(blowtorch_path);
        if ((old_pipe || crowbar) && stored != -1) return proof(lua, false, crowbar ? "stock_guest_crowbar_row_unqualified" :
            "stock_guest_old_pipe_row_unqualified");
        if (camera && !camera_stock_ammo(stored)) return proof(lua, false, "guest_camera_row_unqualified");
        if (blowtorch && (stored < 0 || stored > blowtorch_capacity))
            return proof(lua, false, "stock_guest_blowtorch_fuel_unqualified");
        const auto* magazine_profile = camera || blowtorch ? nullptr : stock_magazine(context.weapon);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory))
            return proof(lua, false, "guest_weapon_payload_unqualified");
        if (inventory.backpack_index >= 0)
            return proof(lua, true, old_pipe ? "old_pipe_backpack" : crowbar ? "crowbar_backpack" : camera ? "camera_backpack" :
                blowtorch ? "blowtorch_backpack" : "backpack", -1, stored);
        auto* actor = equipped(context, inventory);
        if (!actor)
            return proof(lua, true, old_pipe ? "old_pipe_stored_equipment" : crowbar ? "crowbar_stored_equipment" : camera ? "camera_stored_equipment" :
                blowtorch ? "blowtorch_stored_equipment" : "stored_equipment", -1, stored);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory, actor))
            return proof(lua, false, "guest_weapon_payload_unqualified");
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        if (!ammo) return proof(lua, false, "stock_guest_weapon_ammo_unavailable");
        const auto observed = ammo->GetPropertyValueInContainer(actor);
        if (camera && (observed != stored || !camera_capture_ready(actor)))
            return proof(lua, false, "stock_guest_camera_capture_unavailable");
        if ((old_pipe || crowbar) && observed != stored) return proof(lua, false, crowbar ? "stock_guest_crowbar_row_unqualified" :
            "stock_guest_old_pipe_row_unqualified");
        if (blowtorch && observed != stored) return proof(lua, false, "stock_guest_blowtorch_fuel_unqualified");
        return proof(lua, true, old_pipe ? "old_pipe_equipped" : crowbar ? "crowbar_equipped" : camera ? "camera_equipped" :
            blowtorch ? "blowtorch_equipped" : "equipped", observed, stored);
    }

    Unreal::UClass* stock_projectile(Unreal::UObject* actor, int expected_count = 1)
    {
        auto* ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* type = ammo ? ammo->GetStruct().Get() : nullptr;
        auto* count = field<Unreal::FIntProperty>(type, STR("Projectile_Count_12_A3D51CC4418048DC7C98AB8023D0AF39"));
        auto* klass = field<Unreal::FClassProperty>(type, STR("Base_Projectile_6_619A2F0C42DB644A02A7B4B4594BC74A"));
        auto* projectile_base = find<Unreal::UClass>(projectile_path);
        if (!count || !klass || !projectile_base) return nullptr;
        auto* data = ammo->ContainerPtrToValuePtr<void>(actor);
        auto* projectile = static_cast<Unreal::UClass*>(klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(data)));
        return count->GetPropertyValueInContainer(data) == expected_count && projectile && projectile->IsChildOf(projectile_base)
            ? projectile : nullptr;
    }

    Unreal::UClass* mounted_ammo(Unreal::UObject* actor)
    {
        auto* ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* type = ammo ? ammo->GetStruct().Get() : nullptr;
        auto* item = field<Unreal::FClassProperty>(type, ammo_item_name);
        if (!type || type != find<Unreal::UScriptStruct>(ammo_type_path) || !item) return nullptr;
        const auto* data = ammo->ContainerPtrToValuePtr<void>(actor);
        return Unreal::Cast<Unreal::UClass>(item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(data)));
    }

    bool stock_round_matches(Unreal::UObject* actor, Unreal::UClass* ammo, Unreal::UClass* projectile,
                          const profiles::StockAmmo* profile)
    {
        if (!profile || !ammo || ammo != find<Unreal::UClass>(profile->item) ||
            !projectile || projectile != find<Unreal::UClass>(profile->projectile)) return false;
        auto* data = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* caliber = data ? field<Unreal::FByteProperty>(data->GetStruct().Get(), ammo_caliber_name) : nullptr;
        return caliber && caliber->GetPropertyValueInContainer(data->ContainerPtrToValuePtr<void>(actor)) == profile->caliber &&
            (profile == &profiles::nine_mm_ball || ammo_item_row_matches(ammo, *profile));
    }

    bool bolt_round(Unreal::UObject* actor, Unreal::UClass* ammo, Unreal::UClass* projectile)
    {
        if (!ammo || !projectile) return false;
        auto* data = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* caliber = data ? field<Unreal::FByteProperty>(data->GetStruct().Get(), ammo_caliber_name) : nullptr;
        if (!caliber || caliber->GetPropertyValueInContainer(data->ContainerPtrToValuePtr<void>(actor)) != rifle_caliber) return false;
        return (ammo == find<Unreal::UClass>(rifle_fmj_path) && projectile == find<Unreal::UClass>(rifle_fmj_projectile)) ||
            (ammo == find<Unreal::UClass>(rifle_incendiary_path) && projectile == find<Unreal::UClass>(rifle_incendiary_projectile));
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

        bool proves_count(const Context& context, Unreal::UClass* type, int expected, int& observed) const
        {
            observed = 0;
            bool owned = true;
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
                if (!matches(context, type, candidate)) return RC::LoopAction::Continue;
                const auto end = previous.begin() + static_cast<std::ptrdiff_t>(count);
                if (std::find_if(previous.begin(), end, [candidate](const Unreal::FWeakObjectPtr& old) {
                        return old.Get() == candidate;
                    }) != end) return RC::LoopAction::Continue;

                ++observed;
                owned = object(candidate, STR("Owner")) == context.pawn && object(candidate, STR("Instigator")) == context.pawn;
                return observed <= expected && owned ? RC::LoopAction::Continue : RC::LoopAction::Break;
            });
            return observed == expected && owned;
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
        const auto* bolt = stock_bolt(context.weapon);
        const auto* stock_profile = stock_magazine(context.weapon);
        auto* action = find<Unreal::UFunction>(bolt ? bolt->primary : primary_path);
        struct Parameters { bool held{}; double variance{}; } parameters;
        auto* held = input<Unreal::FBoolProperty>(action, STR("Held"), offsetof(Parameters, held));
        auto* variance = input<Unreal::FDoubleProperty>(action, STR("VarianceScaler"), offsetof(Parameters, variance));
        auto* proxy = field<Unreal::FObjectProperty>(actor, STR("InputIntercept"));
        if (!held || !variance || !proxy || object(actor, STR("InputIntercept")) ||
            actor->GetFunctionByNameInChain(use_name) != action || action->GetNumParms() != 2 ||
            action->GetReturnProperty() || action->GetParmsSize() != sizeof(Parameters) ||
            (bolt && (action->GetOuterPrivate() != context.weapon ||
                action->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net))))
            return report(false, "stock_guest_weapon_primary_schema_unavailable");

        if (!boolean(actor, STR("Action_OnRelease"), bolt != nullptr) || (bolt && (disarm || !stock_bolt_shape(actor, *bolt))) ||
            (stock_profile && !stock_magazine_shape(actor, *stock_profile)))
            return report(false, "guest_weapon_release_mode_unqualified");
        if (bolt && (!field<Unreal::FObjectProperty>(context.pawn, STR("OffhandEquipment")) ||
            live(object(context.pawn, STR("OffhandEquipment"))) || !boolean(actor, STR("ReloadInput"), false) ||
            !boolean(actor, STR("IsSeerOpen"), false)))
            return report(false, "guest_weapon_primary_ineligible");
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
        // A factory-empty weapon can lack a resolved round payload. Its verified
        // empty trigger pull dispatches a click without entering the firing path.
        if (!disarm && before == 0 && stored == 0 && shot_cost->GetPropertyValueInContainer(actor) == 1)
            return report(false, "guest_weapon_preloaded_magazine_required", 0, 0);
        if (const auto issue = magazine_payload_issue(stock_profile, context, inventory, actor); !issue.empty())
            return report(false, issue);
        const auto expected_projectiles = stock_profile ? stock_profile->ammo->projectile_count : 1;
        auto* projectile = disarm ? nullptr : stock_projectile(actor, expected_projectiles);
        const auto minimum = bolt || stock_profile ? 1 : 2;
        if (!disarm && (before != stored || before < minimum || before > magazine->GetPropertyValueInContainer(actor) ||
                        shot_cost->GetPropertyValueInContainer(actor) != 1 || !projectile ||
                        (bolt && !bolt_round(actor, mounted_ammo(actor), projectile)) ||
                        (stock_profile && !stock_round_matches(actor, mounted_ammo(actor), projectile, stock_profile->ammo))))
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
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !ordinary_magazine_payload(stock_profile, context, after, actor) ||
            (bolt && (!boolean(actor, STR("IsCycling"), true) || !boolean(actor, STR("ChamberReady"), false))))
            return report(false, "mutation_unverified_guest_weapon_release", before);
        const auto remaining = live_ammo->GetPropertyValueInContainer(actor);
        if (disarm) return report(true, "disarmed", before, remaining);
        int observed_projectiles{};
        if (!projectiles.proves_count(context, projectile, expected_projectiles, observed_projectiles))
            return report(false, "mutation_unverified_guest_weapon_projectile", before, remaining, observed_projectiles);
        const auto after_stored = after.ammo->GetPropertyValueInContainer(after.item);
        if (remaining != before - 1 || (after_stored != stored && after_stored != remaining))
            return report(false, "mutation_unverified_guest_weapon_shot", before, remaining, observed_projectiles);

        after.ammo->SetPropertyValueInContainer(after.item, remaining);
        return after.ammo->GetPropertyValueInContainer(after.item) == remaining
            ? report(true, "fired", before, remaining, observed_projectiles)
            : report(false, "mutation_unverified_guest_weapon_ammo_commit", before, remaining, observed_projectiles);
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

    bool select_incendiary(Unreal::UObject* actor, const Inventory& inventory, Unreal::UClass* ammo)
    {
        auto* fn = find<Unreal::UFunction>(change_ammo_path);
        auto* handle = input<Unreal::FStructProperty>(fn, STR("InputPin"), 0);
        auto* handle_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* defaults = ammo->GetClassDefaultObject().Get();
        auto* ammo_handle = field<Unreal::FStructProperty>(defaults, STR("AmmoType"));
        auto* caliber = field<Unreal::FByteProperty>(defaults, STR("Caliber"));
        auto* table = field<Unreal::FObjectProperty>(handle_type, STR("DataTable"));
        auto* name = field<Unreal::FNameProperty>(handle_type, STR("RowName"));
        auto* expected_table = find<Unreal::UObject>(ammo_table_path);
        const Unreal::FName change_name{STR("ChangeAmmoType")};
        const Unreal::FName incendiary{STR("30-06_Incendiary"), Unreal::FNAME_Find};
        if (!fn || fn->GetOuterPrivate() != find<Unreal::UClass>(weapon_base_path) ||
            actor->GetFunctionByNameInChain(change_name) != fn || fn->GetNumParms() != 1 || fn->GetReturnProperty() ||
            fn->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !handle || !handle_type || handle->GetStruct().Get() != handle_type || fn->GetParmsSize() != handle->GetSize() ||
            !defaults || defaults->GetClassPrivate() != ammo || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            !ammo_handle || ammo_handle->GetStruct().Get() != handle_type || !caliber ||
            caliber->GetPropertyValueInContainer(defaults) != rifle_caliber || !table || !name || !live(expected_table) ||
            incendiary.IsNone() || incendiary.ToString() != STR("30-06_Incendiary") ||
            !mounted_payload_matches(actor, inventory)) return false;
        const auto* row = ammo_handle->ContainerPtrToValuePtr<void>(defaults);
        if (table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(row)) != expected_table ||
            name->GetPropertyValueInContainer(row) != incendiary) return false;
        // Stock ChangeAmmoType writes only the held weapon and its owning pawn's selected equipment row.
        auto* params = handle->AllocateAndInitializeValue();
        if (!params) return false;
        handle->CopyCompleteValue(params, row);
        actor->ProcessEvent(fn, params);
        handle->DestroyAndFreeValue(params);
        return true;
    }

    int reload(const Lua& lua, const Context& context, Inventory inventory)
    {
        auto* actor = equipped(context, inventory);
        if (!actor) return reload_proof(lua, false, "guest_weapon_actor_not_equipped");
        const auto* bolt = stock_bolt(context.weapon);
        const auto* stock_profile = stock_magazine(context.weapon);
        if (!ordinary_magazine_payload(stock_profile, context, inventory, actor))
            return reload_proof(lua, false, "guest_weapon_payload_unqualified");
        if (!ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
            !boolean(actor, STR("Reloading"), false) || !boolean(actor, STR("IsCycling"), false) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !boolean(actor, STR("SingleReload"), bolt != nullptr) || !boolean(actor, STR("UseEatherAsAmmo"), false) ||
            (stock_profile && !stock_magazine_shape(actor, *stock_profile)) ||
            (bolt && (!stock_bolt_shape(actor, *bolt) || !boolean(actor, STR("ReloadInput"), false) ||
                !mounted_payload_matches(actor, inventory))))
            return reload_proof(lua, false, "guest_weapon_reload_ineligible");

        auto* rounds = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        auto* capacity = field<Unreal::FIntProperty>(actor, STR("MagSize"));
        auto* update = find<Unreal::UFunction>(update_mag_path);
        const Unreal::FName update_name{STR("Update_Mag")};
        if (!rounds || !capacity || !update || actor->GetFunctionByNameInChain(update_name) != update ||
            update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty())
            return reload_proof(lua, false, "stock_guest_weapon_reload_schema_unavailable");

        auto* ammo_class = mounted_ammo(actor);
        AmmoStackSchema stack_schema;
        if (!stack_schema.read(ammo_class, inventory) ||
            (bolt && !bolt_round(actor, ammo_class, stock_projectile(actor))) ||
            (stock_profile && !stock_round_matches(actor, ammo_class,
                stock_projectile(actor, stock_profile->ammo->projectile_count), stock_profile->ammo)))
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
        bool change_to_incendiary = false;
        if (bolt && before == 0 && reserve_before == 0 && ammo_class == find<Unreal::UClass>(rifle_fmj_path))
        {
            auto* incendiary_class = find<Unreal::UClass>(rifle_incendiary_path);
            if (incendiary_class && find<Unreal::UClass>(rifle_incendiary_projectile))
            {
                if (!snapshot_reserve(context, inventory, incendiary_class, stacks, reserve_before))
                    return reload_proof(lua, false, "guest_weapon_reserve_unqualified", before, before);
                if (reserve_before > 0)
                {
                    if (!stack_schema.read(incendiary_class, inventory) || stack_schema.unit_value != 12 ||
                        stack_schema.unit_weight != 0.014)
                        return reload_proof(lua, false, "stock_guest_weapon_reserve_schema_unavailable", before, before);
                    ammo_class = incendiary_class;
                    change_to_incendiary = true;
                }
            }
        }
        if (reserve_before == 0)
            return reload_proof(lua, false, "no_matching_guest_ammo", before, before, 0, 0);

        // These stock magazine reloads exit after debiting the first matching Backpack stack.
        const auto available = stock_profile && stock_profile->reload_first_stack ? stacks.front().units : reserve_before;
        const auto transferred = bolt ? 1 : std::min(cap - before, available);
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

        if (change_to_incendiary)
        {
            const Unreal::FWeakObjectPtr held_actor{actor};
            if (!select_incendiary(actor, inventory, ammo_class))
                return reload_proof(lua, false, "stock_guest_weapon_ammo_switch_unqualified", before, before, reserve_before);
            Inventory selected;
            std::vector<ReserveStack> selected_stacks;
            std::int32_t selected_total{};
            if (held_actor.Get() != actor || !ownership_current(context) || !locate(context, selected).empty() ||
                equipped(context, selected) != actor || rounds->GetPropertyValueInContainer(actor) != before ||
                selected.ammo->GetPropertyValueInContainer(selected.item) != before ||
                mounted_ammo(actor) != ammo_class || !bolt_round(actor, ammo_class, stock_projectile(actor)) ||
                !mounted_payload_matches(actor, selected) ||
                !snapshot_reserve(context, selected, ammo_class, selected_stacks, selected_total) || selected_total != reserve_before ||
                !std::ranges::equal(stacks, selected_stacks, [](const ReserveStack& first, const ReserveStack& second) {
                    return first.guid == second.guid && first.index == second.index && first.units == second.units;
                }))
                return reload_proof(lua, false, "mutation_unverified_guest_weapon_ammo_switch", before, -1, reserve_before);
            inventory = selected;
        }

        // Publish the verified detached reserve and magazine in one game-thread turn.
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
            !ordinary_magazine_payload(stock_profile, context, after, actor) ||
            (bolt && (mounted_ammo(actor) != ammo_class || !mounted_payload_matches(actor, after))) ||
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

    const profiles::StockAmmo* reserve_fixture_profile(const Context& context)
    {
        auto* held = object(context.pawn, STR("EquipedItem"));
        const auto* profile = held ? stock_magazine(held->GetClassPrivate()) : nullptr;
        if (!env("SILVERFISH_COOP_HEADLESS") || !profile || profile->ammo == &profiles::nine_mm_ball)
            return &profiles::nine_mm_ball;

        Context receiver = context;
        Inventory inventory;
        if (!locate(context, inventory).empty()) return nullptr;
        const auto slot = inventory.selected->GetPropertyValueInContainer(context.save);
        Unreal::FScriptArrayHelper equipment(
            inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        if (slot < 0 || slot >= equipment.Num()) return nullptr;
        receiver.guid = inventory.identity(equipment.GetRawPtr(slot));
        receiver.weapon = qualified_weapon(held->GetClassPrivate());
        Inventory selected;
        if (receiver.guid == Guid{} || !receiver.weapon || !locate(receiver, selected).empty() ||
            equipped(receiver, selected) != held || !ordinary_magazine_payload(profile, receiver, selected, held) ||
            !stock_round_matches(held, mounted_ammo(held), stock_projectile(held, profile->ammo->projectile_count), profile->ammo))
            return nullptr;
        return profile->ammo;
    }

    using HoldClock = std::chrono::steady_clock;
    using HoldHook = Hook::TCallbackIterationData<void>;
    constexpr auto smg_hold_class = STR("/Game/Classes/Weapons/Wep_SMG_01.Wep_SMG_01_C");
    constexpr auto fire_weapon_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:FireWeapon");
    constexpr auto end_cycle_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:EndCycle");

    struct HoldParameters { bool held{}; double variance{}; };
    struct SmgHold
    {
        Unreal::FWeakObjectPtr pawn, save, stock, instance, controller, connection, world, weapon;
        Unreal::FWeakObjectPtr receiver, primary, fire, end_cycle, projectile, birth;
        Guid guid{};
        HoldClock::time_point deadline{};
        profiles::Profile profile{};
        std::string_view status{"inactive"};
        std::int64_t serial{};
        int before{-1}, after{-1}, shot_before{-1}, rounds{}, projectiles{}, shot_births{};
        bool active{}, closing{}, fenced{}, dirty{}, owned_birth{};

        Context context() const
        {
            return {guid, pawn.Get(), save.Get(), stock.Get(), instance.Get(), controller.Get(), connection.Get(),
                    static_cast<Unreal::UWorld*>(world.Get()), static_cast<Unreal::UClass*>(weapon.Get())};
        }
    };

    std::array<SmgHold, max_clients> smg_holds;
    SmgHold* shot_hold{};
    std::int64_t smg_hold_serial{};
    std::array hold_hooks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID,
                          Hook::ERROR_ID, Hook::ERROR_ID};

    bool hold_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_ZONE") &&
            (env("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE") ||
                (env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS")));
    }

    bool hold_primary_shape(Unreal::UObject* actor, Unreal::UFunction* action)
    {
        const Unreal::FName use_name{STR("Use_Item_Primary")};
        return actor && action && action->GetNumParms() == 2 && action->GetParmsSize() == sizeof(HoldParameters) &&
            !action->GetReturnProperty() && actor->GetFunctionByNameInChain(use_name) == action &&
            action->GetOuterPrivate() == find<Unreal::UClass>(weapon_base_path) &&
            !action->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) &&
            input<Unreal::FBoolProperty>(action, STR("Held"), offsetof(HoldParameters, held)) &&
            input<Unreal::FDoubleProperty>(action, STR("VarianceScaler"), offsetof(HoldParameters, variance)) &&
            field<Unreal::FObjectProperty>(actor, STR("InputIntercept")) && !object(actor, STR("InputIntercept")) &&
            boolean(actor, STR("Action_OnRelease"), false);
    }

    void hold_primary(Unreal::UObject* actor, Unreal::UFunction* action, bool pressed)
    {
        HoldParameters parameters{};
        input<Unreal::FBoolProperty>(action, STR("Held"), offsetof(HoldParameters, held))->
            SetPropertyValueInContainer(&parameters, pressed);
        actor->ProcessEvent(action, &parameters);
    }

    void hold_stop(SmgHold& smg_hold, std::string_view status, bool dirty = false)
    {
        if (!smg_hold.fenced) return;
        if (!smg_hold.dirty)
        {
            smg_hold.status = status;
            smg_hold.dirty = dirty;
        }
        smg_hold.active = false;
        smg_hold.closing = true;
    }

    void hold_release(SmgHold& smg_hold)
    {
        if (!smg_hold.closing || smg_hold.shot_before >= 0) return;
        smg_hold.closing = false;
        auto* actor = smg_hold.receiver.Get();
        auto* pawn = smg_hold.pawn.Get();
        auto* action = static_cast<Unreal::UFunction*>(smg_hold.primary.Get());
        if (authority(actor) && live(pawn) && actor->GetClassPrivate() == smg_hold.weapon.Get() &&
            smg_hold.world.Get() && static_cast<Unreal::AActor*>(actor)->GetWorld() == smg_hold.world.Get() &&
            object(actor, STR("Owner")) == pawn && object(actor, STR("Instigator")) == pawn &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false) && hold_primary_shape(actor, action))
        {
            // The qualified stock release has no Action_OnRelease shot or SaveGame call.
            hold_primary(actor, action, false);
            if (smg_hold.receiver.Get() == actor && boolean(actor, STR("Holding_Primary"), false) &&
                boolean(actor, STR("IsSeerOpen"), false))
            {
                smg_hold.fenced = false;
                return;
            }
        }
        if (!smg_hold.dirty) smg_hold.status = "mutation_unverified_guest_smg_hold_release";
        smg_hold.dirty = true;
        // Keep the exact old receiver fenced if it survived an unqualified release.
        smg_hold.fenced = smg_hold.receiver.Get() != nullptr;
    }

    bool hold_locate(const SmgHold& smg_hold, Unreal::UObject* receiver, Context& context, Inventory& inventory)
    {
        context = smg_hold.context();
        return ownership_current(context) && locate(context, inventory).empty() &&
            equipped(context, inventory) == receiver && stock_magazine_shape(receiver, smg_hold.profile) &&
            ordinary_magazine_payload(&smg_hold.profile, context, inventory, receiver);
    }

    void hold_pre(HoldHook& iteration, Unreal::UObject* receiver, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || !receiver) return;
        const auto match = std::ranges::find_if(smg_holds, [&](const SmgHold& hold) {
            return hold.fenced && (frame.Node() == hold.fire.Get() || frame.Node() == hold.end_cycle.Get()) &&
                receiver == hold.receiver.Get();
        });
        if (match == smg_holds.end()) return;
        auto& smg_hold = *match;
        const bool firing = frame.Node() == smg_hold.fire.Get();
        if (smg_hold.active && HoldClock::now() >= smg_hold.deadline) hold_stop(smg_hold, "expired");
        if (smg_hold.active && smg_hold.after == 0) hold_stop(smg_hold, "empty");
        if (smg_hold.active && (!ready_pawn(smg_hold.context()) || !boolean(smg_hold.pawn.Get(), STR("Wep_HeldAside"), false)))
            hold_stop(smg_hold, "ineligible");
        if (smg_hold.active && firing)
        {
            Context context;
            Inventory inventory;
            auto* ammo = field<Unreal::FIntProperty>(receiver, STR("Ammo"));
            if (iteration.OriginalFunctionCallPrevented() || shot_hold || !ammo ||
                !hold_locate(smg_hold, receiver, context, inventory) || smg_hold.after < 1 ||
                ammo->GetPropertyValueInContainer(receiver) != smg_hold.after ||
                inventory.ammo->GetPropertyValueInContainer(inventory.item) != smg_hold.after)
                hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_shot_scope", true);
            else
            {
                shot_hold = &smg_hold;
                smg_hold.shot_before = smg_hold.after;
                smg_hold.shot_births = 0;
                smg_hold.owned_birth = true;
                smg_hold.birth.Reset();
            }
        }
        if (!smg_hold.active && !iteration.OriginalFunctionCallPrevented()) iteration.PreventOriginalFunctionCall();
    }

    void hold_birth(HoldHook&, Unreal::AActor* actor)
    {
        if (!Unreal::IsInGameThreadRaw() || !shot_hold || !actor) return;
        auto& smg_hold = *shot_hold;
        if (smg_hold.shot_before < 0 ||
            actor->GetClassPrivate() != smg_hold.projectile.Get() || actor->GetWorld() != smg_hold.world.Get()) return;
        ++smg_hold.shot_births;
        auto* pawn = smg_hold.pawn.Get();
        smg_hold.owned_birth = smg_hold.owned_birth && pawn && authority(actor) &&
            object(actor, STR("Owner")) == pawn && object(actor, STR("Instigator")) == pawn;
        if (smg_hold.shot_births == 1) smg_hold.birth = actor;
    }

    void hold_post(HoldHook&, Unreal::UObject* receiver, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || !shot_hold) return;
        auto& smg_hold = *shot_hold;
        if (smg_hold.shot_before < 0 ||
            receiver != smg_hold.receiver.Get() || frame.Node() != smg_hold.fire.Get()) return;
        const auto remaining = smg_hold.shot_before - 1;
        shot_hold = nullptr;
        smg_hold.shot_before = -1;
        Context context;
        Inventory inventory;
        auto* ammo = field<Unreal::FIntProperty>(receiver, STR("Ammo"));
        if (smg_hold.shot_births != 1 || !smg_hold.owned_birth || !smg_hold.birth.Get() || !ammo ||
            !hold_locate(smg_hold, receiver, context, inventory) || ammo->GetPropertyValueInContainer(receiver) != remaining ||
            inventory.ammo->GetPropertyValueInContainer(inventory.item) != remaining)
        {
            hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_shot_readback", true);
            return;
        }
        smg_hold.after = remaining;
        ++smg_hold.rounds;
        ++smg_hold.projectiles;
    }

    void remove_hold_hooks()
    {
        for (auto& callback : hold_hooks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void tick_holds()
    {
        if (shot_hold)
        {
            shot_hold->shot_before = -1;
            hold_stop(*shot_hold, "mutation_unverified_guest_smg_hold_missing_post", true);
            shot_hold = nullptr;
        }
        const auto now = HoldClock::now();
        for (auto& smg_hold : smg_holds)
        {
            if (!smg_hold.fenced) continue;
            const auto context = smg_hold.context();
            if (smg_hold.active && now >= smg_hold.deadline) hold_stop(smg_hold, "expired");
            if (smg_hold.active && smg_hold.after == 0) hold_stop(smg_hold, "empty");
            if (smg_hold.active && (!ownership_current(context) || !ready_pawn(context)))
                hold_stop(smg_hold, "ineligible");
            hold_release(smg_hold);
        }
    }

    bool install_hold_hooks()
    {
        if (hold_hooks.front() != Hook::ERROR_ID) return true;
        constexpr auto owner = STR("SilverfishCoopNative");
        hold_hooks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            hold_pre, {false, false, owner, STR("Fence the exact guest SMG stock cycle")});
        hold_hooks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            hold_post, {false, false, owner, STR("Verify the exact guest SMG stock round")});
        hold_hooks[2] = Hook::RegisterBeginPlayPostCallback(
            hold_birth, {false, false, owner, STR("Verify the stock guest SMG projectile birth")});
        hold_hooks[3] = Hook::RegisterEngineTickPreCallback(
            [](HoldHook&, Unreal::UEngine*, float, bool) {
                if (Unreal::IsInGameThreadRaw()) tick_holds();
            }, {false, false, owner, STR("Expire the native guest SMG hold")});
        hold_hooks[4] = Hook::RegisterLoadMapPreCallback(
            [](Hook::TCallbackIterationData<bool>&, Unreal::UEngine*, Unreal::FWorldContext&, Unreal::FURL,
               Unreal::UPendingNetGame*, Unreal::FString&) {
                if (!Unreal::IsInGameThreadRaw()) return;
                for (auto& smg_hold : smg_holds)
                {
                    hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_world", true);
                    hold_release(smg_hold);
                }
            }, {false, true, owner, STR("Release the held guest SMG before map invalidation")});
        hold_hooks[5] = Hook::RegisterEndPlayPreCallback(
            [](HoldHook&, Unreal::AActor* actor, Unreal::EEndPlayReason) {
                if (!Unreal::IsInGameThreadRaw() || !actor) return;
                for (auto& smg_hold : smg_holds)
                {
                    if (!smg_hold.fenced || (actor != smg_hold.receiver.Get() && actor != smg_hold.pawn.Get() &&
                        actor != smg_hold.controller.Get())) continue;
                    hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_endplay", true);
                    hold_release(smg_hold);
                }
            }, {false, false, owner, STR("Invalidate the exact ending guest SMG scope")});
        if (std::ranges::find(hold_hooks, Hook::ERROR_ID) == hold_hooks.end()) return true;
        remove_hold_hooks();
        return false;
    }

    int hold_result(const Lua& lua, bool ok, std::string_view status, const SmgHold* hold = nullptr)
    {
        result(lua, ok, status);
        lua.set_integer(hold ? hold->serial : 0);
        lua.set_integer(hold ? hold->before : -1);
        lua.set_integer(hold ? hold->after : -1);
        lua.set_integer(hold ? hold->rounds : 0);
        lua.set_integer(hold ? hold->projectiles : 0);
        return 7;
    }

    int start_hold(const Lua& lua)
    {
        if (!hold_enabled()) return hold_result(lua, false, "native_guest_smg_hold_disabled");
        Context context;
        std::int64_t duration{};
        if (const auto error = resolve(lua, context, nullptr, &duration); !error.empty())
            return hold_result(lua, false, error);
        auto seat = std::ranges::find_if(smg_holds, [&](const SmgHold& hold) { return hold.pawn.Get() == context.pawn; });
        if (seat == smg_holds.end())
        {
            seat = std::ranges::min_element(smg_holds, {}, [](const SmgHold& hold) {
                return hold.fenced || hold.dirty ? (std::int64_t{1} << 53) : hold.serial;
            });
        }
        auto& smg_hold = *seat;
        if (smg_hold.fenced || smg_hold.dirty || smg_hold_serial == (std::int64_t{1} << 53) - 1)
            return hold_result(lua, false, "native_guest_smg_hold_busy");

        Inventory inventory;
        if (const auto error = locate(context, inventory); !error.empty()) return hold_result(lua, false, error);
        if (!inventory.item || inventory.row_class(inventory.item) != find<Unreal::UClass>(smg_hold_class))
            return hold_result(lua, false, "native_guest_smg_hold_exact_weapon_required");
        context.weapon = qualified_weapon(inventory.row_class(inventory.item));
        const auto* profile = stock_magazine(context.weapon);
        auto* actor = equipped(context, inventory);
        auto* action = find<Unreal::UFunction>(primary_path);
        auto* fire = find<Unreal::UFunction>(fire_weapon_path);
        auto* end = find<Unreal::UFunction>(end_cycle_path);
        auto* projectile = stock_projectile(actor);
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        const auto loaded = ammo ? ammo->GetPropertyValueInContainer(actor) : -1;
        const auto exact_signal = [&](Unreal::UFunction* function) {
            return actor && function && function->GetOuterPrivate() == find<Unreal::UClass>(weapon_base_path) &&
                actor->GetFunctionByNameInChain(function->GetFName()) == function &&
                !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) &&
                function->GetNumParms() == 0 && function->GetParmsSize() == 0 && !function->GetReturnProperty();
        };
        if (!profile || !actor || !hold_primary_shape(actor, action) || !exact_signal(fire) || !exact_signal(end) ||
            !ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !boolean(actor, STR("IsCycling"), false) || !boolean(actor, STR("Reloading"), false) ||
            !boolean(actor, STR("SemiAutoSelected"), false) || !stock_magazine_shape(actor, *profile) ||
            !stock_round_matches(actor, mounted_ammo(actor), projectile, profile->ammo) ||
            loaded < 1 || loaded > profile->capacity || loaded != inventory.ammo->GetPropertyValueInContainer(inventory.item))
            return hold_result(lua, false, "native_guest_smg_hold_ineligible");

        auto strict = *profile;
        strict.require_default_payload = true;
        if (!ordinary_magazine_payload(&strict, context, inventory, actor) || !install_hold_hooks())
            return hold_result(lua, false, "native_guest_smg_hold_scope_unavailable");
        smg_hold = {};
        smg_hold.pawn = context.pawn;
        smg_hold.save = context.save;
        smg_hold.stock = context.stock;
        smg_hold.instance = context.instance;
        smg_hold.controller = context.controller;
        smg_hold.connection = context.connection;
        smg_hold.world = context.world;
        smg_hold.weapon = context.weapon;
        smg_hold.receiver = actor;
        smg_hold.primary = action;
        smg_hold.fire = fire;
        smg_hold.end_cycle = end;
        smg_hold.projectile = projectile;
        smg_hold.guid = context.guid;
        smg_hold.profile = strict;
        smg_hold.serial = ++smg_hold_serial;
        smg_hold.before = smg_hold.after = loaded;
        smg_hold.status = "active";
        smg_hold.active = smg_hold.fenced = true;
        smg_hold.deadline = HoldClock::now() + std::chrono::milliseconds{duration};
        hold_primary(actor, action, true);
        if (smg_hold.rounds != 1)
        {
            if (shot_hold == &smg_hold) shot_hold = nullptr;
            smg_hold.shot_before = -1;
            hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_begin", true);
            hold_release(smg_hold);
        }
        return hold_result(lua, !smg_hold.dirty, smg_hold.closing && !smg_hold.dirty ? "closing" : smg_hold.status, &smg_hold);
    }

    enum class HoldAction { observe, stop, renew };

    template <HoldAction action>
    int read_hold(const Lua& lua)
    {
        constexpr bool renew = action == HoldAction::renew;
        if (!Unreal::IsInGameThreadRaw() || !hold_enabled() || lua.get_stack_size() != (renew ? 2 : 1) ||
            !lua.is_integer(1) || (renew && !lua.is_integer(2)))
            return hold_result(lua, false, "native_guest_smg_hold_exact_serial_required");
        const auto serial = lua.get_integer();
        const auto seat = std::ranges::find(smg_holds, serial, &SmgHold::serial);
        if (serial < 1 || seat == smg_holds.end())
            return hold_result(lua, false, "native_guest_smg_hold_exact_serial_required");
        auto& smg_hold = *seat;
        if constexpr (action == HoldAction::stop)
        {
            if (smg_hold.active) hold_stop(smg_hold, "released");
            hold_release(smg_hold);
        }
        if constexpr (renew)
        {
            const auto duration = lua.get_integer();
            if (duration < 250 || duration > 2400)
                return hold_result(lua, false, "native_guest_smg_hold_duration_unqualified");
            tick_holds();
            if (smg_hold.active) smg_hold.deadline = HoldClock::now() + std::chrono::milliseconds{duration};
        }
        return hold_result(lua, !smg_hold.dirty, smg_hold.closing && !smg_hold.dirty ? "closing" : smg_hold.status, &smg_hold);
    }

    struct PropertyDeleter
    {
        Unreal::FProperty* type;
        void operator()(void* storage) const { type->DestroyAndFreeValue(storage); }
    };

    bool accepts_stock_quick_slot(int index, Unreal::UClass* item_class, std::uint8_t item_type)
    {
        std::basic_string<Unreal::TCHAR> path{
            STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C:WidgetTree.ItemSlot_1")};
        path.back() += static_cast<Unreal::TCHAR>(index);
        auto* slot = find<Unreal::UObject>(path.c_str());
        if (!live(slot) || slot->GetClassPrivate() != find<Unreal::UClass>(
                STR("/Game/Classes/GUI/New/Components/ItemSlot.ItemSlot_C"))) return false;
        auto* inventory_index = field<Unreal::FIntProperty>(slot, STR("InventoryIndex"));
        auto* item_index = field<Unreal::FIntProperty>(slot, STR("ItemIndex"));
        auto* types = field<Unreal::FArrayProperty>(slot, STR("TypeFilters"));
        auto* classes = field<Unreal::FArrayProperty>(slot, STR("ClassFilters"));
        auto* type = types ? Unreal::CastField<Unreal::FByteProperty>(types->GetInner()) : nullptr;
        auto* cls = classes ? Unreal::CastField<Unreal::FClassProperty>(classes->GetInner()) : nullptr;
        if (!inventory_index || !item_index || inventory_index->GetPropertyValueInContainer(slot) != 0 ||
            item_index->GetPropertyValueInContainer(slot) != index || !type || !cls) return false;

        Unreal::FScriptArrayHelper type_filters(types, types->ContainerPtrToValuePtr<void>(slot));
        Unreal::FScriptArrayHelper class_filters(classes, classes->ContainerPtrToValuePtr<void>(slot));
        bool type_matches = type_filters.Num() == 0;
        for (int i = 0; i < type_filters.Num(); ++i)
            type_matches |= type->GetPropertyValue(type_filters.GetRawPtr(i)) == item_type;
        if (!type_matches) return false;
        if (class_filters.Num() == 0) return true;
        for (int i = 0; i < class_filters.Num(); ++i)
        {
            auto* allowed = Unreal::Cast<Unreal::UClass>(cls->GetObjectPropertyValue(class_filters.GetRawPtr(i)));
            if (allowed && item_class->IsChildOf(allowed)) return true;
        }
        return false;
    }

    constexpr int quick_slots = 8;

    const ThrowableProfile* throwable_profile(Unreal::UClass* type)
    {
        if (!live(type)) return nullptr;
        const auto path = type->GetPathName();
        const auto match = std::ranges::find_if(throwables, [&](const auto& profile) { return path == profile.equipment; });
        auto* base = match != std::ranges::end(throwables) ? find<Unreal::UClass>(grenade_base_path) : nullptr;
        return base && type->IsChildOf(base) ? &*match : nullptr;
    }

    bool supported_stack_equipment(Unreal::UClass* type, Unreal::UObject* defaults)
    {
        if (boolean(defaults, STR("Stacks"), false)) return true;
        const auto* throwable = throwable_profile(type);
        auto* projectile = throwable ? object(defaults, STR("Projectile")) : nullptr;
        return throwable && boolean(defaults, STR("Stacks"), true) && live(projectile) &&
            projectile->GetPathName() == throwable->projectile;
    }

    struct ThrowableCall
    {
        Unreal::UFunction* function{};
        Unreal::FBoolProperty* toss{};

        explicit ThrowableCall(const Unreal::TCHAR* path)
            : function(find<Unreal::UFunction>(path)), toss(input<Unreal::FBoolProperty>(function, STR("Toss"), 0)) {}

        bool valid() const
        {
            return function && toss && function->GetNumParms() == 1 && function->GetParmsSize() == 1 &&
                !function->GetReturnProperty() && function->GetOuterPrivate() == find<Unreal::UClass>(grenade_base_path);
        }

        void invoke(Unreal::UObject* actor, bool underhand) const
        {
            std::byte parameters{};
            toss->SetPropertyValueInContainer(&parameters, underhand);
            actor->ProcessEvent(function, &parameters);
        }
    };

    struct LocalFlareIntent
    {
        Unreal::FWeakObjectPtr pawn, save;
        Guid guid{};
        const ThrowableProfile* profile{};
        std::uint64_t sequence{};
        bool toss{};
        HoldClock::time_point deadline{};
    };
    LocalFlareIntent local_flare;
    std::uint64_t flare_sequence{};
    std::array flare_hooks{Hook::ERROR_ID, Hook::ERROR_ID};

    void intercept_flare(HoldHook& iteration, Unreal::UObject* actor, Unreal::UFunction* function, void* parameters)
    {
        static const Unreal::FName spawn{STR("SpawnNade")};
        if (!function || function->GetFName() != spawn || !parameters || !live(actor) ||
            !Unreal::IsInGameThreadRaw()) return;
        const auto* throwable = throwable_profile(actor->GetClassPrivate());
        auto* pawn = throwable ? object(actor, STR("Player_Ref")) : nullptr;
        if (!pawn || silverfish::native_interaction_input::current_guest_pawn(reinterpret_cast<std::uintptr_t>(pawn)) != pawn ||
            object(pawn, STR("EquipedItem")) != actor) return;
        ThrowableCall call{grenade_spawn_path};
        if (!call.valid() || call.function != function) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;

        const bool toss = call.toss->GetPropertyValueInContainer(parameters);
        // The native throw completion restores the hand pose and its audio. An
        // empty ThrownNade makes that branch return without consuming stock.
        ThrowableCall cleanup{grenade_throw_path};
        if (cleanup.valid() && !live(object(actor, STR("ThrownNade")))) cleanup.invoke(actor, toss);
        if (flare_sequence >= (1ULL << 53) - 1) return;
        Context context{};
        context.pawn = pawn;
        context.weapon = actor->GetClassPrivate();
        context.save = object(pawn, STR("Save"));
        context.world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        if (!live(context.save) || object(context.instance, STR("SaveSlot_Ref")) != context.save) return;
        Inventory inventory;
        if (!bind_inventory(context, inventory).empty()) return;
        inventory.equipment_index = inventory.selected->GetPropertyValueInContainer(context.save);
        Unreal::FScriptArrayHelper slots(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        if (inventory.equipment_index < 0 || inventory.equipment_index >= quick_slots ||
            inventory.equipment_index >= slots.Num() || equipped(context, inventory) != actor) return;
        const auto* row = slots.GetRawPtr(inventory.equipment_index);
        const auto guid = inventory.identity(row);
        const auto ammo = inventory.ammo->GetPropertyValueInContainer(row);
        if (guid == Guid{} || inventory.row_class(row) != context.weapon || ammo < 1) return;
        local_flare = {pawn, context.save, guid, throwable, ++flare_sequence, toss, HoldClock::now() + std::chrono::seconds(2)};
    }

    void flare_script_pre(HoldHook& iteration, Unreal::UObject* actor, Unreal::FFrame& frame, void*)
    {
        intercept_flare(iteration, actor, frame.Node(), frame.Locals());
    }

    int take_flare(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = lua.get_integer();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(address) : nullptr;
        if (!pawn || pawn != local_flare.pawn.Get() || object(pawn, STR("Save")) != local_flare.save.Get() ||
            !local_flare.profile || HoldClock::now() >= local_flare.deadline) return 0;
        const auto intent = std::exchange(local_flare, {});
        const auto& guid = intent.guid;
        lua.set_integer(intent.sequence);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        lua.set_bool(intent.toss);
        lua.set_string(RC::to_utf8_string(intent.profile->equipment));
        return 4;
    }

    int throw_flare(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) || !lua.is_bool(4))
            return refuse("native_guest_flare_refused_scope");
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        Context context{};
        if (pawn <= 0 || save <= 0 || !parse_guid(lua.get_string(), context.guid) ||
            !resolve_owner(context, pawn, save).empty() || !ready_pawn(context))
            return refuse("native_guest_flare_refused_owner");
        const bool toss = lua.get_bool();
        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item || inventory.equipment_index < 0 ||
            inventory.equipment_index >= quick_slots) return refuse("native_guest_flare_refused_slot");
        context.weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        const auto* throwable = throwable_profile(context.weapon);
        auto* actor = throwable ? equipped(context, inventory) : nullptr;
        auto* projectile_class = actor ? object(actor, STR("Projectile")) : nullptr;
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        const auto before = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (!actor || !live(projectile_class) || projectile_class->GetPathName() != throwable->projectile ||
            !boolean(actor, STR("Stacks"), true) || !ammo || before < 1 ||
            ammo->GetPropertyValueInContainer(actor) != before) return refuse("native_guest_flare_refused_equipment");
        ThrowableCall call{grenade_spawn_path};
        auto* equipped_property = field<Unreal::FObjectProperty>(context.pawn, STR("EquipedItem"));
        if (!call.valid() || !equipped_property) return refuse("native_guest_flare_refused_schema");
        const auto slot = inventory.equipment_index;
        auto* previous = object(actor, STR("ThrownNade"));

        using Value = std::unique_ptr<void, PropertyDeleter>;
        auto* host_inventory = inventory.equipment->ContainerPtrToValuePtr<void>(context.stock);
        Value prior_host_inventory(inventory.equipment->AllocateAndInitializeValue(), {inventory.equipment});
        if (!prior_host_inventory) return refuse("native_guest_flare_refused_storage");
        inventory.equipment->CopyCompleteValue(prior_host_inventory.get(), host_inventory);

        call.invoke(actor, toss);
        auto* spawned = object(actor, STR("ThrownNade"));
        Unreal::FScriptArrayHelper slots(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        auto* after = slot < slots.Num() ? slots.GetRawPtr(slot) : nullptr;
        const bool remaining = after && (before > 1
            ? inventory.identity(after) == context.guid && inventory.row_class(after) == context.weapon &&
                inventory.ammo->GetPropertyValueInContainer(after) == before - 1
            : !inventory.row_class(after) && inventory.identity(after) == Guid{});
        if (!ownership_current(context) || !remaining || !inventory.equipment->Identical(prior_host_inventory.get(), host_inventory) ||
            !authority(spawned) || spawned == previous || spawned->GetClassPrivate() != projectile_class ||
            static_cast<Unreal::AActor*>(spawned)->GetWorld() != context.world ||
            object(spawned, STR("Owner")) != context.pawn || object(spawned, STR("Instigator")) != context.pawn)
            return refuse("mutation_unverified_native_guest_flare_throw");
        if (before == 1 && object(context.pawn, STR("EquipedItem")) == actor)
        {
            // Stock depletion can leave the retired hand actor in an empty detached slot.
            // Keep any replacement that stock equipment selection already installed.
            if (live(actor)) static_cast<Unreal::AActor*>(actor)->K2_DestroyActor();
            auto* held = equipped_property->ContainerPtrToValuePtr<void>(context.pawn);
            if (equipped_property->GetObjectPropertyValue(held) == actor)
                equipped_property->SetObjectPropertyValue(held, nullptr);
        }
        result(lua, true, "thrown");
        lua.set_integer(before);
        lua.set_integer(before - 1);
        lua.set_string(RC::to_utf8_string(throwable->equipment));
        lua.set_string(RC::to_utf8_string(spawned->GetPathName()));
        return 6;
    }

    int select_stock_equipment(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        Context context;
        if (!resolve(lua, context, nullptr).empty() || !ready_pawn(context) ||
            std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return refuse("native_guest_selection_refused_owner");
        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item ||
            inventory.equipment_index < 0 || inventory.equipment_index >= quick_slots)
            return refuse("native_guest_selection_refused_slot");
        auto* type = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        auto* base = find<Unreal::UClass>(equipment_base_path);
        auto* defaults = live(type) && base && type != base && type->IsChildOf(base) ? type->GetClassDefaultObject().Get() : nullptr;
        if (!defaults || defaults->GetClassPrivate() != type ||
            !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !supported_stack_equipment(type, defaults))
            return refuse("native_guest_selection_refused_equipment");
        context.weapon = type;
        const auto loaded = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        const auto verifies = [&](const Inventory& selected) {
            auto* actor = equipped(context, selected);
            auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
            return ammo && ammo->GetPropertyValueInContainer(actor) == loaded &&
                selected.ammo->GetPropertyValueInContainer(selected.item) == loaded;
        };
        if (verifies(inventory)) return proof(lua, true, "equipped", loaded, loaded);

        EquipCall call;
        if (!call.valid(context.pawn)) return refuse("native_guest_selection_refused_schema");
        const auto previous = inventory.selected->GetPropertyValueInContainer(context.save);
        const auto target = inventory.equipment_index;
        if (auto* old = object(context.pawn, STR("EquipedItem")); live(old))
        {
            Unreal::FScriptArrayHelper equipment(
                inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
            if (previous == target || previous < 0 || previous >= equipment.Num() || !authority(old) ||
                object(old, STR("Owner")) != context.pawn || object(old, STR("Instigator")) != context.pawn ||
                inventory.row_class(equipment.GetRawPtr(previous)) != old->GetClassPrivate())
                return refuse("native_guest_selection_refused_previous");
        }
        call.invoke(context.pawn, target);
        Inventory after;
        if (!ownership_current(context) || !locate(context, after).empty() || after.equipment_index != target ||
            !verifies(after)) return refuse("mutation_unverified_guest_equipment_selection");
        return proof(lua, true, "equipped", loaded, loaded);
    }

    struct LocalSelection
    {
        Unreal::FWeakObjectPtr pawn, save, actor;
        Guid guid{};
        std::uint64_t sequence{};
    };
    LocalSelection local_selection;
    std::array selection_hooks{Hook::ERROR_ID, Hook::ERROR_ID};
    std::uint64_t selection_sequence{};

    void record_selection(Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        static const Unreal::FName name{STR("Equip_NextItem")};
        if (!function || function->GetFName() != name || !pawn || !parameters || !Unreal::IsInGameThreadRaw() ||
            silverfish::native_interaction_input::current_guest_pawn(reinterpret_cast<std::uintptr_t>(pawn)) != pawn ||
            function != find<Unreal::UFunction>(equip_path) || selection_sequence >= (1ULL << 53) - 1) return;
        EquipCall call;
        if (!call.valid(pawn)) return;
        const int index = call.index->GetPropertyValueInContainer(parameters);
        if (index < 0 || index >= quick_slots) return;
        Context context;
        context.pawn = pawn;
        context.save = object(pawn, STR("Save"));
        context.world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        if (!live(context.save) || object(context.instance, STR("SaveSlot_Ref")) != context.save) return;
        Inventory inventory;
        if (!bind_inventory(context, inventory).empty() ||
            inventory.selected->GetPropertyValueInContainer(context.save) != index) return;
        Unreal::FScriptArrayHelper slots(
            inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        if (slots.Num() < quick_slots || slots.Num() > max_inventory) return;
        auto* row = slots.GetRawPtr(index);
        const auto guid = inventory.identity(row);
        context.weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(row));
        auto* actor = object(pawn, STR("EquipedItem"));
        if (guid == Guid{} || !context.weapon || !authority(actor) || actor->GetClassPrivate() != context.weapon ||
            static_cast<Unreal::AActor*>(actor)->GetWorld() != context.world ||
            object(actor, STR("Owner")) != pawn || object(actor, STR("Instigator")) != pawn ||
            object(actor, STR("User")) != pawn || !boolean(actor, STR("HeldByPlayer"), true)) return;
        if (local_selection.pawn.Get() == pawn && local_selection.save.Get() == context.save &&
            local_selection.actor.Get() == actor && local_selection.guid == guid) return;
        local_selection = {pawn, context.save, actor, guid, ++selection_sequence};
    }

    void selection_event_post(HoldHook&, Unreal::UObject* pawn, Unreal::UFunction* function, void* parameters)
    {
        record_selection(pawn, function, parameters);
    }

    void selection_script_post(HoldHook&, Unreal::UObject* pawn, Unreal::FFrame& frame, void*)
    {
        record_selection(pawn, frame.Node(), frame.Locals());
    }

    int read_local_selection(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = lua.get_integer();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(address) : nullptr;
        auto* actor = local_selection.actor.Get();
        if (!pawn || pawn != local_selection.pawn.Get() || object(pawn, STR("Save")) != local_selection.save.Get() ||
            !actor || object(pawn, STR("EquipedItem")) != actor) return 0;
        const auto& guid = local_selection.guid;
        lua.set_integer(local_selection.sequence);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        return 2;
    }

    void remove_selection_hooks()
    {
        for (auto& callback : selection_hooks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void install_selection_hooks()
    {
        if (selection_hooks[0] != Hook::ERROR_ID) return;
        selection_hooks[0] = Hook::RegisterProcessEventPostCallback(
            selection_event_post, {false, false, STR("SilverfishCoopNative"), STR("Read stock guest quick slot selection")});
        selection_hooks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            selection_script_post, {false, false, STR("SilverfishCoopNative"), STR("Read stock guest quick slot selection")});
        if (selection_hooks[0] == Hook::ERROR_ID || selection_hooks[1] == Hook::ERROR_ID) remove_selection_hooks();
    }

    int store_stock_equipment(const Lua& lua)
    {
        Context context;
        int requested_slot = -1;
        if (!resolve(lua, context, nullptr, nullptr, nullptr, false, &requested_slot).empty() || !ready_pawn(context) ||
            std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return result(lua, false, "native_inventory_equip_refused_owner");
        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item || inventory.backpack_index < 0)
            return result(lua, false, "native_inventory_equip_refused_source");
        auto* type = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        auto* base = inventory.item_class->GetMetaClass().Get();
        auto* defaults = live(type) && base && type != base && type->IsChildOf(base) ? type->GetClassDefaultObject().Get() : nullptr;
        auto* item_type = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
        auto* payload = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        if (!defaults || !item_type || !supported_stack_equipment(type, defaults) || !payload ||
            payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")))
            return result(lua, false, "native_inventory_equip_refused_item");

        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.save);
        auto* equipped = inventory.equipment->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper equipment(inventory.equipment, equipped);
        if (equipment.Num() < quick_slots || equipment.Num() > max_inventory)
            return result(lua, false, "native_inventory_equip_refused_layout");
        const auto selected = inventory.selected->GetPropertyValueInContainer(context.save);
        const auto item_kind = item_type->GetPropertyValueInContainer(defaults);
        int target = -1;
        int occupied = -1;
        for (int index = 0; index < quick_slots; ++index)
        {
            if (index == selected || (requested_slot >= 0 && index != requested_slot) ||
                !accepts_stock_quick_slot(index, type, item_kind)) continue;
            const auto* row = equipment.GetRawPtr(index);
            const auto id = inventory.identity(row);
            auto* current_class = inventory.row_class(row);
            if (!current_class && id == Guid{})
            {
                target = index;
                break;
            }
            if (live(current_class) && id != Guid{} && occupied < 0) occupied = index;
        }
        if (target < 0) target = occupied;
        if (target < 0) return result(lua, false, "native_inventory_equip_refused_slot");

        using Value = std::unique_ptr<void, PropertyDeleter>;
        auto* bag_property = inventory.backpack;
        auto* equip_property = inventory.equipment;
        Value original_bag(bag_property->AllocateAndInitializeValue(), {bag_property});
        Value proposed_bag(bag_property->AllocateAndInitializeValue(), {bag_property});
        Value original_equipment(equip_property->AllocateAndInitializeValue(), {equip_property});
        Value proposed_equipment(equip_property->AllocateAndInitializeValue(), {equip_property});
        if (!original_bag || !proposed_bag || !original_equipment || !proposed_equipment)
            return result(lua, false, "native_inventory_equip_refused_storage");
        bag_property->CopyCompleteValue(original_bag.get(), bag);
        bag_property->CopyCompleteValue(proposed_bag.get(), bag);
        equip_property->CopyCompleteValue(original_equipment.get(), equipped);
        equip_property->CopyCompleteValue(proposed_equipment.get(), equipped);
        Unreal::FScriptArrayHelper proposed_items(bag_property, proposed_bag.get());
        Unreal::FScriptArrayHelper proposed_slots(equip_property, proposed_equipment.get());
        auto* source_row = proposed_items.GetRawPtr(inventory.backpack_index);
        auto* target_row = proposed_slots.GetRawPtr(target);
        const auto* old_slot = equipment.GetRawPtr(target);
        const auto old_guid = inventory.identity(old_slot);
        const bool replacing = old_guid != Guid{};
        if (replacing)
        {
            auto displaced = context;
            displaced.guid = old_guid;
            Inventory located;
            if (!locate(displaced, located).empty() || located.equipment_index != target)
                return result(lua, false, "native_inventory_equip_refused_displaced_guid");
        }
        // A replaced row keeps its GUID and full payload in the former source
        // position, so this move cannot create a second copy or lose a gun.
        inventory.type->CopyCompleteValue(target_row, inventory.item);
        if (replacing) inventory.type->CopyCompleteValue(source_row, old_slot);
        else proposed_items.RemoveValues(inventory.backpack_index, 1);

        const auto same_item = [&](const void* first, const void* second) {
            return inventory.type->Identical(first, second) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        };
        if (!same_item(target_row, inventory.item) || (replacing && !same_item(source_row, old_slot)) ||
            !ownership_current(context) || !bag_property->Identical(original_bag.get(), bag) ||
            !equip_property->Identical(original_equipment.get(), equipped))
            return result(lua, false, "native_inventory_equip_refused_staging");
        bag_property->CopyCompleteValue(bag, proposed_bag.get());
        equip_property->CopyCompleteValue(equipped, proposed_equipment.get());
        if (!ownership_current(context) || !bag_property->Identical(proposed_bag.get(), bag) ||
            !equip_property->Identical(proposed_equipment.get(), equipped) ||
            inventory.selected->GetPropertyValueInContainer(context.save) != selected)
        {
            if (ownership_current(context))
            {
                bag_property->CopyCompleteValue(bag, original_bag.get());
                equip_property->CopyCompleteValue(equipped, original_equipment.get());
                if (bag_property->Identical(original_bag.get(), bag) && equip_property->Identical(original_equipment.get(), equipped))
                    return result(lua, false, "native_inventory_equip_refused_rolled_back");
            }
            return result(lua, false, "mutation_unverified_native_inventory_equip");
        }
        result(lua, true, "stored");
        lua.set_integer(target);
        lua.set_string(RC::to_utf8_string(type->GetPathName()));
        std::array<char, 32> formatted{};
        constexpr std::string_view digits = "0123456789ABCDEF";
        for (std::size_t word = 0; word < old_guid.size(); ++word)
            for (int part = 0; part < 8; ++part)
                formatted[word * 8 + part] = digits[(old_guid[word] >> ((7 - part) * 4)) & 15];
        lua.set_string(replacing ? std::string_view{formatted.data(), formatted.size()} : std::string_view{"-"});
        return 5;
    }

    struct RemoveEquipmentCall
    {
        static constexpr int parameters_size = 0x69;
        Unreal::UFunction* function = find<Unreal::UFunction>(
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:RemoveItem"));
        Unreal::FStructProperty* guid = input<Unreal::FStructProperty>(function, STR("ItemGuid"), 0);
        Unreal::FIntProperty* source = input<Unreal::FIntProperty>(function, STR("Inventory"), 0x10);
        Unreal::FStructProperty* item = field<Unreal::FStructProperty>(function, STR("Item"));
        Unreal::FBoolProperty* success = field<Unreal::FBoolProperty>(function, STR("Success"));

        bool valid(const Context& context, const Inventory& inventory) const
        {
            const auto output = [](Unreal::FProperty* property, int offset, int size) {
                return property && property->GetOffset_Internal() == offset && property->GetSize() == size &&
                    property->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
                    property->HasAnyPropertyFlags(Unreal::CPF_OutParm) &&
                    !property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm | Unreal::CPF_ReferenceParm);
            };
            return function && function->GetOuterPrivate() == context.pawn->GetClassPrivate() &&
                !function->GetReturnProperty() && !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) &&
                function->GetNumParms() == 4 && function->GetParmsSize() >= parameters_size &&
                function->GetParmsSize() <= 0x70 && guid && source && guid->GetSize() == 0x10 &&
                guid->GetStruct().Get() == inventory.guid->GetStruct().Get() &&
                output(item, 0x18, 0x50) && item->GetStruct().Get() == inventory.type->GetStruct().Get() &&
                output(success, 0x68, 1);
        }

        bool invoke(const Context& context, const Inventory& inventory) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x70> storage{};
            item->InitializeValue_InContainer(storage.data());
            const auto destroy = [this](std::byte* data) { item->DestroyValue_InContainer(data); };
            const std::unique_ptr<std::byte, decltype(destroy)> cleanup(storage.data(), destroy);
            guid->CopyCompleteValue(guid->ContainerPtrToValuePtr<void>(storage.data()),
                                    inventory.guid->ContainerPtrToValuePtr<void>(inventory.item));
            source->SetPropertyValueInContainer(storage.data(), 0);
            context.pawn->ProcessEvent(function, storage.data());
            return success->GetPropertyValueInContainer(storage.data()) &&
                inventory.identity(item->ContainerPtrToValuePtr<void>(storage.data())) == context.guid &&
                inventory.row_class(item->ContainerPtrToValuePtr<void>(storage.data())) == context.weapon;
        }
    };

    bool held_for_unequip(const Context& context, Unreal::UObject* actor)
    {
        return authority(actor) && actor->GetClassPrivate() == context.weapon &&
            static_cast<Unreal::AActor*>(actor)->GetWorld() == context.world &&
            object(actor, STR("Owner")) == context.pawn && object(actor, STR("Instigator")) == context.pawn &&
            object(actor, STR("User")) == context.pawn && boolean(actor, STR("HeldByPlayer"), true) &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    int unequip_stock_equipment(const Lua& lua)
    {
        Context context;
        int slot = -1;
        if (!resolve(lua, context, nullptr, nullptr, nullptr, false, &slot).empty() || slot < 0 || !ready_pawn(context) ||
            std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return result(lua, false, "native_inventory_unequip_refused_owner");
        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item || inventory.equipment_index != slot)
            return result(lua, false, "native_inventory_unequip_refused_source");
        context.weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        auto* base = find<Unreal::UClass>(equipment_base_path);
        auto* payload = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        if (!live(context.weapon) || !base || context.weapon == base || !context.weapon->IsChildOf(base) ||
            !payload || payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")))
            return result(lua, false, "native_inventory_unequip_refused_item");

        const auto selected = inventory.selected->GetPropertyValueInContainer(context.save);
        auto* held = object(context.pawn, STR("EquipedItem"));
        auto* held_property = field<Unreal::FObjectProperty>(context.pawn, STR("EquipedItem"));
        auto ammo = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (selected == slot && live(held))
        {
            auto* rounds = field<Unreal::FIntProperty>(held, STR("Ammo"));
            if (!held_for_unequip(context, held) || !rounds)
                return result(lua, false, "native_inventory_unequip_refused_held_item");
            ammo = rounds->GetPropertyValueInContainer(held);
        }
        if (ammo < -1) return result(lua, false, "native_inventory_unequip_refused_ammo");
        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.save);
        auto* slots = inventory.equipment->ContainerPtrToValuePtr<void>(context.save);
        auto* stock_bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.stock);
        auto* stock_slots = inventory.equipment->ContainerPtrToValuePtr<void>(context.stock);
        Unreal::FScriptArrayHelper items(inventory.backpack, bag), equipment(inventory.equipment, slots);
        if (items.Num() >= max_inventory || equipment.Num() < quick_slots || !held_property ||
            object(context.pawn, STR("As Game Instance FP")) != context.instance)
            return result(lua, false, "native_inventory_unequip_refused_layout");
        RemoveEquipmentCall remove;
        if (!remove.valid(context, inventory)) return result(lua, false, "native_inventory_unequip_refused_schema");
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value previous_bag(inventory.backpack->AllocateAndInitializeValue(), {inventory.backpack});
        Value proposed_bag(inventory.backpack->AllocateAndInitializeValue(), {inventory.backpack});
        Value proposed_slots(inventory.equipment->AllocateAndInitializeValue(), {inventory.equipment});
        Value host_bag(inventory.backpack->AllocateAndInitializeValue(), {inventory.backpack});
        Value host_slots(inventory.equipment->AllocateAndInitializeValue(), {inventory.equipment});
        Value empty(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        if (!previous_bag || !proposed_bag || !proposed_slots || !host_bag || !host_slots || !empty)
            return result(lua, false, "native_inventory_unequip_refused_storage");
        inventory.backpack->CopyCompleteValue(previous_bag.get(), bag);
        inventory.backpack->CopyCompleteValue(proposed_bag.get(), bag);
        inventory.equipment->CopyCompleteValue(proposed_slots.get(), slots);
        inventory.backpack->CopyCompleteValue(host_bag.get(), stock_bag);
        inventory.equipment->CopyCompleteValue(host_slots.get(), stock_slots);
        const auto host_selected = inventory.selected->GetPropertyValueInContainer(context.stock);
        Unreal::FScriptArrayHelper staged_items(inventory.backpack, proposed_bag.get());
        Unreal::FScriptArrayHelper staged_slots(inventory.equipment, proposed_slots.get());
        auto* returned = staged_items.GetRawPtr(staged_items.AddValue());
        inventory.type->CopyCompleteValue(returned, inventory.item);
        if (!inventory.type->Identical(returned, inventory.item) ||
            !payload->Identical(payload->ContainerPtrToValuePtr<void>(returned),
                                payload->ContainerPtrToValuePtr<void>(inventory.item)) || !ownership_current(context))
            return result(lua, false, "native_inventory_unequip_refused_staging");
        inventory.ammo->SetPropertyValueInContainer(returned, ammo);
        inventory.type->CopyCompleteValue(staged_slots.GetRawPtr(slot), empty.get());

        const Unreal::FWeakObjectPtr previous_held{held};
        const bool removed = remove.invoke(context, inventory);
        if (!removed || !ownership_current(context) ||
            !inventory.backpack->Identical(previous_bag.get(), bag) ||
            !inventory.equipment->Identical(proposed_slots.get(), slots) ||
            !inventory.backpack->Identical(host_bag.get(), stock_bag) ||
            !inventory.equipment->Identical(host_slots.get(), stock_slots) ||
            inventory.selected->GetPropertyValueInContainer(context.stock) != host_selected ||
            (selected != slot && (object(context.pawn, STR("EquipedItem")) != held ||
                                   inventory.selected->GetPropertyValueInContainer(context.save) != selected)) ||
            (selected == slot && live(previous_held.Get()) &&
             boolean(previous_held.Get(), STR("bActorIsBeingDestroyed"), false)))
            return result(lua, false, "mutation_unverified_native_inventory_unequip");

        // Preserve anything that stock FindNextEquipment installed.
        if (selected == slot && held && object(context.pawn, STR("EquipedItem")) == held)
            held_property->SetObjectPropertyValue(held_property->ContainerPtrToValuePtr<void>(context.pawn), nullptr);
        inventory.backpack->CopyCompleteValue(bag, proposed_bag.get());
        Inventory relocated;
        if (!inventory.backpack->Identical(proposed_bag.get(), bag) || !locate(context, relocated).empty() ||
            relocated.backpack_index != staged_items.Num() - 1)
            return result(lua, false, "mutation_unverified_native_inventory_unequip_pack");
        result(lua, true, "unequipped");
        lua.set_integer(slot);
        lua.set_string(RC::to_utf8_string(context.weapon->GetPathName()));
        lua.set_integer(ammo);
        return 5;
    }

    int sort_backpack(const Lua& lua)
    {
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || !Unreal::IsInGameThreadRaw() ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "native_inventory_sort_refused_scope");
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        Context context;
        if (pawn_address <= 0 || save_address <= 0 || !resolve_owner(context, pawn_address, save_address).empty())
            return result(lua, false, "native_inventory_sort_refused_owner");
        if (!ready_pawn(context) || std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return result(lua, false, "native_inventory_sort_refused_busy");

        Inventory inventory;
        if (!bind_inventory(context, inventory).empty())
            return result(lua, false, "native_inventory_sort_refused_inventory_layout");
        auto* backpack = inventory.backpack;
        auto* current = backpack->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper references(backpack, current);
        const auto count = references.Num();
        if (count < 0 || count > max_inventory)
            return result(lua, false, "native_inventory_sort_refused_backpack_size");

        auto* function = find<Unreal::UFunction>(sort_path);
        auto* library = find<Unreal::UObject>(library_path);
        auto* input = field<Unreal::FArrayProperty>(function, STR("ItemArray"));
        auto* world_context = field<Unreal::FObjectProperty>(function, STR("__WorldContext"));
        auto* output = field<Unreal::FArrayProperty>(function, STR("SortedItemArray"));
        auto* row_type = inventory.type->GetStruct().Get();
        auto* payload = field<Unreal::FStructProperty>(row_type, payload_name);
        const auto matches_array = [row_type](Unreal::FArrayProperty* array, int offset) {
            auto* row = array ? Unreal::CastField<Unreal::FStructProperty>(array->GetInner()) : nullptr;
            return row && row->GetStruct().Get() == row_type && array->GetOffset_Internal() == offset &&
                array->GetSize() == 0x10 && array->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
                array->HasAnyPropertyFlags(Unreal::CPF_OutParm) && !array->HasAnyPropertyFlags(Unreal::CPF_ReturnParm);
        };
        if (!function || !library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            function->GetOuterPrivate() != library->GetClassPrivate() || !function->HasAnyFunctionFlags(Unreal::FUNC_Static) ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || function->GetNumParms() != 3 ||
            function->GetParmsSize() != 0x28 || function->GetReturnProperty() || !matches_array(input, 0) ||
            !input->HasAnyPropertyFlags(Unreal::CPF_ReferenceParm) || !matches_array(output, 0x18) ||
            output->HasAnyPropertyFlags(Unreal::CPF_ReferenceParm) || !world_context ||
            world_context->GetOffset_Internal() != 0x10 || world_context->GetSize() != sizeof(void*) ||
            !world_context->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            world_context->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) ||
            !world_context->GetPropertyClass().Get() || !context.pawn->IsA(world_context->GetPropertyClass().Get()) ||
            !payload || payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")))
            return result(lua, false, "native_inventory_sort_refused_stock_schema");

        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(backpack->AllocateAndInitializeValue(), {backpack});
        if (!original) return result(lua, false, "native_inventory_sort_refused_storage");
        backpack->CopyCompleteValue(original.get(), current);
        Unreal::FScriptArrayHelper before(backpack, original.get());
        auto* base = inventory.item_class->GetMetaClass().Get();
        std::map<Guid, const void*> originals;
        for (int index = 0; index < count; ++index)
        {
            const auto* row = before.GetRawPtr(index);
            const auto id = inventory.identity(row);
            auto* type = Unreal::Cast<Unreal::UClass>(inventory.row_class(row));
            if (id == Guid{} || !live(type) || !base || !type->IsChildOf(base) || !originals.emplace(id, row).second)
                return result(lua, false, "native_inventory_sort_refused_source_row");
        }

        alignas(std::max_align_t) std::array<std::byte, 0x28> parameters{};
        auto* storage = parameters.data();
        input->InitializeValue_InContainer(storage);
        output->InitializeValue_InContainer(storage);
        const auto destroy = [input, output](std::byte* data) {
            output->DestroyValue_InContainer(data);
            input->DestroyValue_InContainer(data);
        };
        const std::unique_ptr<std::byte, decltype(destroy)> cleanup(storage, destroy);
        input->CopyCompleteValue(input->ContainerPtrToValuePtr<void>(storage), original.get());
        world_context->SetObjectPropertyValue(world_context->ContainerPtrToValuePtr<void>(storage), context.pawn);
        library->ProcessEvent(function, storage);

        auto* candidate = output->ContainerPtrToValuePtr<void>(storage);
        Unreal::FScriptArrayHelper after(output, candidate);
        if (after.Num() != count)
            return result(lua, false, "native_inventory_sort_refused_stock_count");
        for (int index = 0; index < count; ++index)
        {
            const auto* row = after.GetRawPtr(index);
            auto previous = originals.extract(inventory.identity(row));
            if (previous.empty() || !inventory.type->Identical(previous.mapped(), row) ||
                !payload->Identical(payload->ContainerPtrToValuePtr<void>(previous.mapped()),
                                    payload->ContainerPtrToValuePtr<void>(row)))
                return result(lua, false, "native_inventory_sort_refused_payload");
        }

        if (!ownership_current(context) || !backpack->Identical(original.get(), current))
            return result(lua, false, "mutation_unverified_native_inventory_sort_scope");
        const bool changed = !backpack->Identical(candidate, current);
        if (changed)
        {
            backpack->CopyCompleteValue(current, candidate);
            if (!ownership_current(context) || !backpack->Identical(candidate, current))
            {
                if (ownership_current(context))
                {
                    backpack->CopyCompleteValue(current, original.get());
                    if (backpack->Identical(original.get(), current))
                        return result(lua, false, "native_inventory_sort_refused_rolled_back");
                }
                return result(lua, false, "mutation_unverified_native_inventory_sort");
            }
        }
        result(lua, true, changed ? "sorted" : "already_sorted");
        lua.set_integer(count);
        return 3;
    }

    int split_ammo(const Lua& lua)
    {
        Context context;
        StackSplit split;
        if (const auto error = resolve(lua, context, nullptr, nullptr, &split); !error.empty())
        {
            const auto detail = error.starts_with("native_inventory_split_refused_")
                ? error : std::string_view{"native_inventory_split_refused_owner"};
            return result(lua, false, detail);
        }
        if (!ready_pawn(context) || std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return result(lua, false, "native_inventory_split_refused_busy");
        Inventory inventory;
        if (!locate(context, inventory).empty() || inventory.backpack_index < 0 || !inventory.item)
            return result(lua, false, "native_inventory_split_refused_source");
        auto* item_class = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        if (!item_class || (item_class != find<Unreal::UClass>(profiles::nine_mm_ball.item) &&
            item_class != find<Unreal::UClass>(profiles::forty_five_ball.item) &&
            item_class != find<Unreal::UClass>(profiles::twelve_gauge_buck.item)))
            return result(lua, false, "native_inventory_split_refused_class");
        if (inventory.ammo->GetPropertyValueInContainer(inventory.item) != split.expected)
            return result(lua, false, "native_inventory_split_refused_stale_quantity");
        auto* row_type = inventory.type->GetStruct().Get();
        auto* never_drop = field<Unreal::FBoolProperty>(row_type,
            STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        auto* payload = field<Unreal::FStructProperty>(row_type, payload_name);
        AmmoStackSchema stack;
        if (!never_drop || never_drop->GetPropertyValueInContainer(inventory.item) ||
            !payload || payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")) ||
            !stack.read(item_class, inventory) || !stack.qualifies(split.expected) ||
            !stack.source_totals_match(item_class, inventory.item, split.expected))
            return result(lua, false, "native_inventory_split_refused_stock_stack");

        auto distinct = context;
        distinct.guid = split.created;
        Inventory collision;
        if (!locate(distinct, collision).empty() || collision.item)
            return result(lua, false, "native_inventory_split_refused_guid_collision");
        auto* property = inventory.backpack;
        auto* live_array = property->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper current(property, live_array);
        const auto count = current.Num();
        if (count >= max_inventory) return result(lua, false, "native_inventory_split_refused_backpack_full");
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(property->AllocateAndInitializeValue(), {property});
        Value proposed(property->AllocateAndInitializeValue(), {property});
        if (!original || !proposed) return result(lua, false, "native_inventory_split_refused_storage");
        property->CopyCompleteValue(original.get(), live_array);
        property->CopyCompleteValue(proposed.get(), live_array);
        Unreal::FScriptArrayHelper staged(property, proposed.get());
        const auto new_index = staged.AddValue();
        auto* remainder = staged.GetRawPtr(inventory.backpack_index);
        auto* created = staged.GetRawPtr(new_index);
        inventory.type->CopyCompleteValue(created, inventory.item);
        const auto* stock_payload = payload->ContainerPtrToValuePtr<void>(inventory.item);
        if (!inventory.type->Identical(remainder, inventory.item) || !inventory.type->Identical(created, inventory.item) ||
            !payload->Identical(stock_payload, payload->ContainerPtrToValuePtr<void>(remainder)) ||
            !payload->Identical(stock_payload, payload->ContainerPtrToValuePtr<void>(created)))
            return result(lua, false, "native_inventory_split_refused_payload");
        inventory.set_identity(created, split.created);
        const auto remaining = split.expected - split.take;
        inventory.ammo->SetPropertyValueInContainer(remainder, remaining);
        inventory.ammo->SetPropertyValueInContainer(created, split.take);
        stack.set_totals(remainder, remaining);
        stack.set_totals(created, split.take);
        if (staged.Num() != count + 1 || new_index != count || inventory.identity(remainder) != context.guid ||
            inventory.identity(created) != split.created || inventory.row_class(remainder) != item_class ||
            inventory.row_class(created) != item_class || !stack.totals_match(remainder, remaining) ||
            !stack.totals_match(created, split.take) || !ownership_current(context) ||
            !property->Identical(original.get(), live_array))
            return result(lua, false, "native_inventory_split_refused_staging");
        property->CopyCompleteValue(live_array, proposed.get());
        if (!ownership_current(context) || !property->Identical(proposed.get(), live_array))
        {
            if (ownership_current(context))
            {
                property->CopyCompleteValue(live_array, original.get());
                if (property->Identical(original.get(), live_array))
                    return result(lua, false, "native_inventory_split_refused_rolled_back");
            }
            return result(lua, false, "mutation_unverified_native_inventory_split");
        }
        proof(lua, true, "split", split.expected, remaining);
        lua.set_integer(split.take);
        lua.set_string(RC::to_utf8_string(item_class->GetPathName()));
        return 6;
    }

    const profiles::Profile* unload_profile(Unreal::UObject* item_class, int rounds)
    {
        const auto* profile = stock_magazine(Unreal::Cast<Unreal::UClass>(item_class));
        return profile && profile->ammo
            && rounds > 0 && rounds <= profile->capacity ? profile : nullptr;
    }

    template <typename T>
    T* split_parameter(Unreal::UFunction* function, const Unreal::TCHAR* name, int offset, bool output = false)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(Unreal::CPF_Parm)
            && property->HasAnyPropertyFlags(Unreal::CPF_OutParm) == output
            && !property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm)
            && property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize()
            ? property : nullptr;
    }

    struct StockSplit
    {
        Unreal::UObject* library{};
        Unreal::UFunction* function{};
        Unreal::FStructProperty* item{};
        Unreal::FIntProperty* amount{};
        Unreal::FObjectProperty* world{};
        Unreal::FStructProperty* portion{};
        Unreal::FStructProperty* remainder{};
        Unreal::FBoolProperty* entire{};
        Unreal::FBoolProperty* success{};
        alignas(std::max_align_t) std::array<std::byte, 0x108> buffer{};
        bool initialized{};

        ~StockSplit()
        {
            if (initialized)
                for (auto* row : {item, portion, remainder}) row->DestroyValue_InContainer(buffer.data());
        }

        bool bind(Unreal::UObject* context, const Unreal::UScriptStruct* row_type)
        {
            library = find<Unreal::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
            function = find<Unreal::UFunction>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:SplitStack"));
            if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function
                || function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 7
                || function->GetReturnProperty() || function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)
                || function->GetParmsSize() < 0x102 || function->GetParmsSize() > 0x108) return false;
            item = split_parameter<Unreal::FStructProperty>(function, STR("Item"), 0);
            amount = split_parameter<Unreal::FIntProperty>(function, STR("AmountToTake"), 0x50);
            world = split_parameter<Unreal::FObjectProperty>(function, STR("__WorldContext"), 0x58);
            portion = split_parameter<Unreal::FStructProperty>(function, STR("NewItem"), 0x60, true);
            remainder = split_parameter<Unreal::FStructProperty>(function, STR("RemainingStack"), 0xB0, true);
            entire = split_parameter<Unreal::FBoolProperty>(function, STR("EntireStack"), 0x100, true);
            success = split_parameter<Unreal::FBoolProperty>(function, STR("Success"), 0x101, true);
            const auto shape = [row_type](Unreal::FStructProperty* property) {
                return property && property->GetSize() == 0x50 && property->GetStruct().Get() == row_type;
            };
            return shape(item) && shape(portion) && shape(remainder) && amount && world && world->GetPropertyClass().Get()
                && context->IsA(world->GetPropertyClass().Get()) && entire && success;
        }

        bool run(Unreal::UObject* context, const void* source, int count)
        {
            for (auto* row : {item, portion, remainder}) row->InitializeValue_InContainer(buffer.data());
            initialized = true;
            item->CopyCompleteValue(item->ContainerPtrToValuePtr<void>(buffer.data()), source);
            amount->SetPropertyValueInContainer(buffer.data(), count);
            world->SetObjectPropertyValue(world->ContainerPtrToValuePtr<void>(buffer.data()), context);
            library->ProcessEvent(function, buffer.data());
            return success->GetPropertyValueInContainer(buffer.data()) && !entire->GetPropertyValueInContainer(buffer.data());
        }

        void* moved() { return portion->ContainerPtrToValuePtr<void>(buffer.data()); }
        void* left() { return remainder->ContainerPtrToValuePtr<void>(buffer.data()); }
    };

    int take_ammo(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        Context context;
        StackSplit selection;
        if (!resolve(lua, context, nullptr, nullptr, &selection, true).empty())
            return refuse("native_inventory_take_ammo_refused_owner_or_selection");
        if (!ready_pawn(context) || std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return refuse("native_inventory_take_ammo_refused_busy");
        Inventory inventory;
        if (!locate(context, inventory).empty() || inventory.backpack_index < 0 || !inventory.item)
            return refuse("native_inventory_take_ammo_refused_source");
        auto* weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        const auto* profile = unload_profile(weapon, selection.expected);
        auto* defaults = profile ? weapon->GetClassDefaultObject().Get() : nullptr;
        const auto* row_type = inventory.type->GetStruct().Get();
        auto* weight = field<Unreal::FDoubleProperty>(inventory.type->GetStruct().Get(), weight_name);
        auto* ammo_class = profile ? find<Unreal::UClass>(profile->ammo->item) : nullptr;
        AmmoStackSchema stock_ammo;
        if (!defaults || !row_type || !weight || inventory.ammo->GetPropertyValueInContainer(inventory.item) != selection.expected ||
            !mounted_payload_matches(defaults, inventory) || !ammo_item_row_matches(ammo_class, *profile->ammo)
            || !stock_ammo.read(ammo_class, inventory) || !stock_ammo.qualifies(selection.expected))
            return refuse("native_inventory_take_ammo_refused_loaded_state");

        auto distinct = context;
        distinct.guid = selection.created;
        Inventory collision;
        if (!locate(distinct, collision).empty() || collision.item)
            return refuse("native_inventory_take_ammo_refused_guid_collision");
        auto* property = inventory.backpack;
        auto* live_array = property->ContainerPtrToValuePtr<void>(context.save);
        auto* host_array = property->ContainerPtrToValuePtr<void>(context.stock);
        auto* flags = field<Unreal::FMapProperty>(context.stock, STR("Flags"));
        if (!flags || Unreal::FScriptArrayHelper(property, live_array).Num() >= max_inventory)
            return refuse("native_inventory_take_ammo_refused_backpack");

        StockSplit split;
        if (!split.bind(context.pawn, row_type))
            return refuse("native_inventory_take_ammo_refused_stock_parameters");

        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(property->AllocateAndInitializeValue(), {property});
        Value proposed(property->AllocateAndInitializeValue(), {property});
        Value host_before(property->AllocateAndInitializeValue(), {property});
        Value flags_before(flags->AllocateAndInitializeValue(), {flags});
        Value expected_weapon(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        if (!original || !proposed || !host_before || !flags_before || !expected_weapon)
            return refuse("native_inventory_take_ammo_refused_storage");
        auto* host_flags = flags->ContainerPtrToValuePtr<void>(context.stock);
        property->CopyCompleteValue(original.get(), live_array);
        property->CopyCompleteValue(proposed.get(), live_array);
        property->CopyCompleteValue(host_before.get(), host_array);
        flags->CopyCompleteValue(flags_before.get(), host_flags);
        const auto host_unchanged = [&] {
            return ownership_current(context) && property->Identical(host_before.get(), host_array)
                && flags->Identical(flags_before.get(), host_flags);
        };

        const auto did_split = split.run(context.pawn, inventory.item, 1);
        if (!host_unchanged() || !property->Identical(original.get(), live_array))
            return refuse("mutation_unverified_native_inventory_take_ammo_stock_call");
        auto* extracted = split.moved();
        auto* unloaded = split.left();
        const auto empty_weight = weight->GetPropertyValueInContainer(unloaded);
        if (!did_split
            || inventory.identity(extracted) == Guid{} || inventory.identity(extracted) == context.guid
            || inventory.row_class(extracted) != ammo_class || inventory.row_class(unloaded) != weapon
            || inventory.ammo->GetPropertyValueInContainer(extracted) != selection.expected
            || inventory.ammo->GetPropertyValueInContainer(unloaded) != 0 || !std::isfinite(empty_weight) || empty_weight < 0
            || !stock_ammo.totals_match(extracted, selection.expected))
            return refuse("native_inventory_take_ammo_refused_stock_result");
        inventory.type->CopyCompleteValue(expected_weapon.get(), inventory.item);
        inventory.ammo->SetPropertyValueInContainer(expected_weapon.get(), 0);
        weight->SetPropertyValueInContainer(expected_weapon.get(), empty_weight);
        if (!inventory.type->Identical(expected_weapon.get(), unloaded))
            return refuse("native_inventory_take_ammo_refused_weapon_identity");

        Unreal::FScriptArrayHelper staged(property, proposed.get());
        const auto inserted = staged.AddValue();
        inventory.type->CopyCompleteValue(staged.GetRawPtr(inventory.backpack_index), unloaded);
        inventory.set_identity(extracted, selection.created);
        inventory.type->CopyCompleteValue(staged.GetRawPtr(inserted), extracted);
        if (!inventory.type->Identical(staged.GetRawPtr(inventory.backpack_index), unloaded)
            || !inventory.type->Identical(staged.GetRawPtr(inserted), extracted))
            return refuse("native_inventory_take_ammo_refused_staging");
        property->CopyCompleteValue(live_array, proposed.get());
        if (!host_unchanged() || !property->Identical(proposed.get(), live_array))
        {
            if (host_unchanged())
            {
                property->CopyCompleteValue(live_array, original.get());
                if (property->Identical(original.get(), live_array))
                    return refuse("native_inventory_take_ammo_refused_rolled_back");
            }
            return refuse("mutation_unverified_native_inventory_take_ammo_commit");
        }
        proof(lua, true, "unloaded", selection.expected, 0);
        lua.set_integer(selection.expected);
        lua.set_string(RC::to_utf8_string(ammo_class->GetPathName()));
        lua.set_string(RC::to_utf8_string(weapon->GetPathName()));
        return 7;
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
        if constexpr (operation == Operation::Seed || operation == Operation::SeedAmmo || operation == Operation::Disarm)
        {
            if (!env("SILVERFISH_COOP_NATIVE_FIXTURES")) return fail("native_guest_weapon_fixtures_disabled");
        }
        Context context;
        SeedSelection seed_selection;
        if (const auto error = resolve(lua, context, operation == Operation::Seed ? &seed_selection : nullptr);
            !error.empty()) return fail(error);
        if constexpr (operation != Operation::Inspect)
            if (std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return fail("native_guest_smg_hold_busy");
        Inventory inventory;
        if constexpr (operation == Operation::Seed || operation == Operation::SeedAmmo)
        {
            const profiles::StockAmmo* reserve = nullptr;
            if constexpr (operation == Operation::SeedAmmo)
            {
                reserve = reserve_fixture_profile(context);
                if (!reserve) return fail("stock_guest_ammo_fixture_profile_unqualified");
            }
            const auto path = reserve ? reserve->item : seed_selection.path;
            auto* expected_class = find<Unreal::UClass>(path);
            if (!expected_class || (operation == Operation::Seed && !qualified_weapon(expected_class) &&
                !qualified_blowtorch(expected_class) && !qualified_melee_item(expected_class, crowbar_path) &&
                !throwable_profile(expected_class)))
                return fail(operation == Operation::SeedAmmo ? "stock_guest_ammo_fixture_class_unavailable"
                                                             : "stock_guest_weapon_class_unavailable");
            if (const auto error = locate(context, inventory, expected_class); !error.empty()) return fail(error);
            return seed(lua, context, inventory, expected_class, reserve, seed_selection.full_magazine);
        }
        if (const auto error = locate(context, inventory); !error.empty()) return fail(error);
        if (!inventory.item) return fail("owned_guest_weapon_guid_required");
        auto* row_class = inventory.row_class(inventory.item);
        if constexpr (operation == Operation::EquipOldPipe)
        {
            context.weapon = qualified_old_pipe(row_class, context, true);
            return context.weapon ? equip(lua, context, inventory) : fail("stock_guest_old_pipe_class_unqualified");
        }
        context.weapon = qualified_weapon(row_class);
        if constexpr (operation == Operation::Equip || operation == Operation::Inspect)
        {
            if (!context.weapon) context.weapon = qualified_camera(row_class);
            if (!context.weapon) context.weapon = qualified_old_pipe(row_class, context);
            if (!context.weapon) context.weapon = qualified_blowtorch(row_class);
            if (!context.weapon) context.weapon = qualified_melee_item(row_class, crowbar_path);
            if constexpr (operation == Operation::Inspect)
            {
                auto* item_type = Unreal::Cast<Unreal::UClass>(row_class);
                if (!context.weapon && throwable_profile(item_type)) context.weapon = item_type;
            }
        }
        if (!context.weapon) return fail("guest_weapon_class_unqualified");
        if constexpr (operation == Operation::Equip) return equip(lua, context, inventory);
        if constexpr (operation == Operation::Inspect) return inspect(lua, context, inventory);
        if constexpr (operation == Operation::Reload) return reload(lua, context, inventory);
        return fire(lua, context, inventory, operation == Operation::Disarm);
    }
}

bool silverfish::native_guest_item::take_ammo_supported(Unreal::UObject* item_class, int rounds)
{
    return unload_profile(item_class, rounds) != nullptr;
}

std::string_view silverfish::native_guest_item::finish_local_unequip(Unreal::UObject* pawn, Unreal::UObject* save,
                                                                    Unreal::UObject* prior_actor, int slot,
                                                                    std::string_view identity, int rounds)
{
    if (!Unreal::IsInGameThreadRaw() || slot < 0 || slot > 9 ||
        silverfish::native_interaction_input::current_guest_pawn(reinterpret_cast<std::uintptr_t>(pawn)) != pawn ||
        !live(pawn) || !live(save) || object(pawn, STR("Save")) != save) return "unequip_guest_scope_changed";
    Context context;
    if (!parse_guid(identity, context.guid)) return "unequip_item_identity_unavailable";
    context.pawn = pawn;
    context.save = save;
    context.world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
    context.instance = object(context.world, STR("OwningGameInstance"));
    if (!context.world || object(context.instance, STR("SaveSlot_Ref")) != save) return "unequip_guest_scope_changed";
    Inventory inventory;
    if (const auto error = locate(context, inventory); !error.empty()) return error;
    if (!inventory.item || inventory.backpack_index < 0) return "unequip_backpack_item_unavailable";
    if (inventory.ammo->GetPropertyValueInContainer(inventory.item) != rounds) return "unequip_backpack_ammunition_mismatch";
    if (slot >= quick_slots)
    {
        const bool head = slot == 8;
        auto* worn = head ? inventory.helmet : inventory.armor;
        const auto* row = worn->ContainerPtrToValuePtr<void>(save);
        auto* update = find<Unreal::UFunction>(head
            ? STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:UpdateHelmet")
            : STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:UpdateArmor"));
        auto* derived = field<Unreal::FStructProperty>(pawn, head ? STR("Helm_Struct") : STR("Armor_Struct"));
        auto* defaults = pawn->GetClassPrivate()->GetClassDefaultObject().Get();
        if (prior_actor || inventory.row_class(row) || inventory.identity(row) != Guid{} || !update || !derived ||
            !defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            update->GetOuterPrivate() != context.instance->GetClassPrivate() ||
            update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty() ||
            update->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return "unequip_worn_item_update_unavailable";
        // These stock functions target player zero in this guest process and
        // read only its verified private SaveSlot_Ref.
        context.instance->ProcessEvent(update, nullptr);
        return derived->Identical(derived->ContainerPtrToValuePtr<void>(pawn),
                                  derived->ContainerPtrToValuePtr<void>(defaults)) ? "" : "unequip_worn_stats_mismatch";
    }
    Unreal::FScriptArrayHelper slots(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(save));
    if (slot >= slots.Num() || inventory.row_class(slots.GetRawPtr(slot)) ||
        inventory.identity(slots.GetRawPtr(slot)) != Guid{}) return "unequip_equipment_slot_not_empty";

    // A later stock input may already have holstered/replaced the actor while the host settled.
    if (!prior_actor || object(pawn, STR("EquipedItem")) != prior_actor) return {};
    context.weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
    auto* held_property = field<Unreal::FObjectProperty>(pawn, STR("EquipedItem"));
    if (!held_property || !held_for_unequip(context, prior_actor)) return "unequip_held_actor_unqualified";
    const auto next = inventory.selected->GetPropertyValueInContainer(save);
    EquipCall equip;
    auto* base = find<Unreal::UClass>(equipment_base_path);
    auto* fallback = next >= 0 && next < slots.Num() && next != slot
        ? Unreal::Cast<Unreal::UClass>(inventory.row_class(slots.GetRawPtr(next))) : nullptr;
    if (fallback && (!base || !fallback->IsChildOf(base) || !equip.valid(pawn))) return "unequip_fallback_unqualified";
    const Unreal::FWeakObjectPtr old{prior_actor};
    static_cast<Unreal::AActor*>(prior_actor)->K2_DestroyActor();
    if (live(old.Get()) && boolean(old.Get(), STR("bActorIsBeingDestroyed"), false)) return "unequip_held_actor_still_live";
    if (object(pawn, STR("EquipedItem")) == prior_actor)
        held_property->SetObjectPropertyValue(held_property->ContainerPtrToValuePtr<void>(pawn), nullptr);
    if (fallback && !live(object(pawn, STR("EquipedItem")))) equip.invoke(pawn, next);
    return object(pawn, STR("EquipedItem")) != prior_actor ? "" : "unequip_held_reference_still_active";
}

bool silverfish::native_guest_item::split_stock_stack(Unreal::UObject* context, Unreal::FStructProperty* row_type,
                                                     const void* input, int amount, void* portion, void* remainder)
{
    StockSplit split;
    if (!split.bind(context, row_type->GetStruct().Get()) || !split.run(context, input, amount)) return false;
    row_type->CopyCompleteValue(portion, split.moved());
    row_type->CopyCompleteValue(remainder, split.left());
    return true;
}

void silverfish::native_guest_item::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (env("SILVERFISH_COOP_NATIVE_INTERACT_INPUT"))
    {
        lua.register_function("SilverfishReadLocalGuestEquipmentSelection", read_local_selection);
        install_selection_hooks();
        lua.register_function("SilverfishTakeLocalGuestFlare", take_flare);
        if (flare_hooks[0] == Hook::ERROR_ID)
        {
            flare_hooks[0] = Hook::RegisterProcessEventPreCallback(
                intercept_flare, {false, false, STR("SilverfishCoopNative"), STR("Route the stock guest flare spawn")});
            flare_hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
                flare_script_pre, {false, false, STR("SilverfishCoopNative"), STR("Route the stock guest flare call")});
        }
    }
    if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE")) return;
    lua.register_function("SilverfishEquipNativeGuestWeapon", run<Operation::Equip>);
    lua.register_function("SilverfishInspectNativeGuestWeapon", run<Operation::Inspect>);
    lua.register_function("SilverfishFireNativeGuestWeapon", run<Operation::Fire>);
    lua.register_function("SilverfishReloadNativeGuestWeapon", run<Operation::Reload>);
    lua.register_function("SilverfishSplitNativeGuestAmmoStack", split_ammo);
    lua.register_function("SilverfishTakeNativeGuestWeaponAmmo", take_ammo);
    lua.register_function("SilverfishSortNativeGuestBackpack", sort_backpack);
    lua.register_function("SilverfishStoreNativeGuestEquipment", store_stock_equipment);
    lua.register_function("SilverfishUnequipNativeGuestEquipment", unequip_stock_equipment);
    lua.register_function("SilverfishSelectNativeGuestEquipment", select_stock_equipment);
    lua.register_function("SilverfishThrowNativeGuestFlare", throw_flare);
    if (env("SILVERFISH_COOP_NATIVE_FIXTURES"))
    {
        if (env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") && env("SILVERFISH_COOP_HEADLESS"))
            lua.register_function("SilverfishEquipNativeGuestOldPipe", run<Operation::EquipOldPipe>);
        lua.register_function("SilverfishSeedNativeGuestWeapon", run<Operation::Seed>);
        lua.register_function("SilverfishSeedNativeGuestAmmo", run<Operation::SeedAmmo>);
        lua.register_function("SilverfishDisarmNativeGuestWeapon", run<Operation::Disarm>);
        if (hold_enabled())
        {
            lua.register_function("SilverfishStartNativeGuestSmgHold", start_hold);
            lua.register_function("SilverfishReadNativeGuestSmgHold", read_hold<HoldAction::observe>);
            lua.register_function("SilverfishStopNativeGuestSmgHold", read_hold<HoldAction::stop>);
            lua.register_function("SilverfishRenewNativeGuestSmgHold", read_hold<HoldAction::renew>);
        }
    }
}

void silverfish::native_guest_item::shutdown()
{
    if (Unreal::IsInGameThreadRaw())
    {
        for (auto& smg_hold : smg_holds)
        {
            hold_stop(smg_hold, "shutdown");
            hold_release(smg_hold);
        }
    }
    remove_hold_hooks();
    remove_selection_hooks();
    for (auto& callback : flare_hooks)
    {
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        callback = Hook::ERROR_ID;
    }
    local_flare = {};
}
