#include "guest_item.hpp"
#include "campaign_npc.hpp"
#include "drop_item_class.hpp"
#include "guest_melee.hpp"
#include "guest_weapon_registry.hpp"
#include "interaction_input.hpp"
#include "loot_escrow.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <exception>
#include <format>
#include <map>
#include <memory>
#include <numbers>
#include <optional>
#include <ranges>
#include <set>
#include <span>
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
    using AimVector = silverfish::native_guest_item::ShotVector;
    using ShotView = silverfish::native_guest_item::ShotView;
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
    constexpr auto rifle_fmj_path = profiles::thirty_06_fmj.item;
    constexpr auto rifle_incendiary_path = profiles::thirty_06_incendiary.item;
    constexpr auto rifle_fmj_projectile = profiles::thirty_06_fmj.projectile;
    constexpr auto rifle_incendiary_projectile = profiles::thirty_06_incendiary.projectile;
    constexpr auto ammo_table_path = STR("/Game/Classes/Libraries/DataTables/AmmoTypes_DT.AmmoTypes_DT");
    constexpr auto change_ammo_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:ChangeAmmoType");
    constexpr int bolt_magazine = profiles::bolt_action_magazine;
    constexpr int rifle_caliber = 3;
    constexpr auto camera_path = STR("/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C");
    constexpr auto blowtorch_path = STR("/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C");
    constexpr auto crowbar_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C");
    constexpr auto spade_path = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C");
    constexpr std::int32_t blowtorch_capacity = 3;
    constexpr auto equipment_base_path = STR("/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C");
    constexpr auto landmine_path = STR("/Game/Classes/Equipment/Eqp_LandMine.Eqp_LandMine_C");
    constexpr auto grenade_base_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C");
    constexpr auto grenade_spawn_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C:SpawnNade");
    constexpr auto grenade_throw_path = STR("/Game/Classes/Equipment/Grenade_Master_BP.Grenade_Master_BP_C:ThrowNade");
    constexpr std::int32_t max_pipe_fuse_ms = 5000;
    struct ThrowableProfile
    {
        const Unreal::TCHAR* equipment;
        const Unreal::TCHAR* projectile;
        std::string_view fixture_kind;
        bool carries_fuse{};
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
        {STR("/Game/Classes/Equipment/Gren_PipeBomb.Gren_PipeBomb_C"),
         STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/PipeBomb_Projectile_BP.PipeBomb_Projectile_BP_C"),
         "stock-pipe-bomb", true},
    };
    constexpr auto old_pipe_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C");
    constexpr auto weapon_base_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C");
    constexpr auto ammo_master_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto ammo_type_path = STR("/Game/Classes/Libraries/Structs/AmmoType_Struct.AmmoType_Struct");
    constexpr auto projectile_path =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto primary_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Use_Item_Primary");
    constexpr auto update_mag_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:Update_Mag");
    constexpr auto loki_path = STR("/Game/Classes/Weapons/Wep_Assets/Weapon_UnderBarrel_Loki.Weapon_UnderBarrel_Loki_C");
    constexpr auto loki_base_update =
        STR("/Game/Classes/Weapons/Weapon_UnderBarrel_BP.Weapon_UnderBarrel_BP_C:Update_Mag");
    constexpr auto loki_change_ammo =
        STR("/Game/Classes/Weapons/Weapon_UnderBarrel_BP.Weapon_UnderBarrel_BP_C:ChangeAmmoType");
    constexpr profiles::Profile loki_magazine{
        "loki-underbarrel", loki_path, false, 4, 1, 1.0, true, &profiles::twelve_gauge_buck, true, false, nullptr, true};
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
    constexpr auto loki_rounds_name = STR("UnderBarrelAmmo_8_D30DE28A473111BF8B86299C237C9831");
    constexpr auto loki_ammo_name = STR("UnderBarrelAmmoType_7_27EBD17F43ACB75947A05EA7056298CF");
    constexpr auto ammo_item_name = STR("Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582");
    constexpr auto ammo_caliber_name = STR("Caliber_3_3C88B25D42998F32221AA0AA7E1B4537");
    constexpr auto value_name = STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9");
    constexpr auto guid_name = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr std::int32_t magazine_fixture = 7;
    constexpr int max_inventory = 4096;
    constexpr int max_clients = 4;

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

    const profiles::Profile* stock_magazine(Unreal::UClass* weapon);

    const Unreal::TCHAR* loki_mount(Unreal::UObject* receiver)
    {
        const auto* profile = receiver ? stock_magazine(receiver->GetClassPrivate()) : nullptr;
        return profile ? profile->underbarrel_mount : nullptr;
    }

    Unreal::UObject* mounted_loki(Unreal::UObject* receiver, Unreal::UObject* pawn)
    {
        const auto* mount_name = loki_mount(receiver);
        if (!mount_name || !live(receiver) || !live(pawn)) return nullptr;
        auto* mount = object(receiver, mount_name);
        auto* mount_class = find<Unreal::UClass>(STR("/Script/Engine.ChildActorComponent"));
        auto* child_class = find<Unreal::UClass>(loki_path);
        if (!mount_class || !live(mount) || mount->GetClassPrivate() != mount_class ||
            mount->GetOuterPrivate() != receiver || !child_class || object(mount, STR("ChildActorClass")) != child_class)
            return nullptr;
        auto* child = object(mount, STR("ChildActor"));
        if (!live(child) || child->GetClassPrivate() != child_class ||
            object(child, STR("ParentWeapon")) != receiver || object(child, STR("User")) != pawn ||
            object(child, STR("Player_Ref")) != pawn || !boolean(child, STR("HeldByPlayer"), true) ||
            static_cast<Unreal::AActor*>(child)->GetWorld() != static_cast<Unreal::AActor*>(receiver)->GetWorld())
            return nullptr;
        return child;
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

    unsigned contact_number(std::string_view name)
    {
        constexpr std::string_view prefix = "npc:";
        if (!name.starts_with(prefix)) return 0;
        name.remove_prefix(prefix.size());
        if (name.empty() || name.size() > 5 || name.front() == '0') return 0;
        unsigned id{};
        const auto [end, error] = std::from_chars(name.data(), name.data() + name.size(), id);
        return error == std::errc{} && end == name.data() + name.size() && id <= 65535 ? id : 0;
    }

    bool valid_contact_path(std::string_view path)
    {
        if (contact_number(path)) return true;
        constexpr std::string_view prefix = "/Game/Classes/Maps/";
        constexpr std::string_view level = ":PersistentLevel.";
        if (!path.starts_with(prefix) || path.size() > 256) return false;
        const auto separator = path.find(level, prefix.size());
        if (separator == std::string_view::npos || separator == prefix.size() ||
            separator + level.size() == path.size()) return false;
        constexpr auto path_characters = "._ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
        return path.substr(prefix.size(), separator - prefix.size()).find_first_not_of(
                "/._ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") == std::string_view::npos &&
            path.substr(separator + level.size()).find_first_not_of(path_characters) == std::string_view::npos;
    }

    bool shot_view_fields(const Lua& lua, int first, int count)
    {
        if (count != 3 && count != 9 && count != 12 && count != 16) return false;
        for (int index = 0; index < count; ++index)
            if (index == 12 ? !lua.is_string(first + index) : !lua.is_number(first + index)) return false;
        return true;
    }

    bool read_shot_view(const Lua& lua, int fields, ShotView& view)
    {
        for (auto& axis : view.direction) axis = lua.get_number();
        const auto length = std::hypot(view.direction[0], view.direction[1], view.direction[2]);
        if (!std::isfinite(length) || std::abs(length - 1.0) > .001) return false;
        for (auto& axis : view.direction) axis /= length;
        const auto position = [&]() -> std::optional<AimVector> {
            AimVector axes{};
            for (auto& axis : axes) axis = lua.get_number();
            if (!std::ranges::all_of(axes, [](double value) {
                return std::isfinite(value) && std::abs(value) <= 2000000;
            })) return {};
            return axes;
        };
        if (fields >= 9)
        {
            view.eye = position();
            view.target = position();
            if (!view.eye || !view.target) return false;
        }
        if (fields >= 12)
        {
            view.muzzle = position();
            if (!view.muzzle) return false;
        }
        if (fields == 16)
        {
            view.contact_actor = lua.get_string();
            if (!valid_contact_path(view.contact_actor)) return false;
            view.contact = position();
            if (!view.contact) return false;
        }
        return true;
    }

    std::string_view resolve(const Lua& lua, Context& context, SeedSelection* seed_selection,
                             std::int64_t* hold_duration = nullptr, StackSplit* split = nullptr, bool full_split = false,
                             int* requested_slot = nullptr, ShotView* shot_view = nullptr, std::string* reload_round = nullptr)
    {
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE")) return "native_guest_weapon_scope_disabled";
        const auto count = lua.get_stack_size();
        const bool split_fields = split && count == 6 && lua.is_string(4) && lua.is_integer(5) && lua.is_integer(6);
        const int first_view = hold_duration ? 5 : 4;
        const int view_fields = count - first_view + 1;
        const bool shot_fields = shot_view && view_fields > 0 && !split && !seed_selection && !requested_slot;
        if (shot_fields && !shot_view_fields(lua, first_view, view_fields)) return "guest_weapon_view_unqualified";
        const bool legacy_fields = count == 3 || (count == 4 && (((seed_selection || reload_round) && lua.is_string(4)) ||
            ((hold_duration || requested_slot) && lua.is_integer(4))));
        if (!Unreal::IsInGameThreadRaw() || (split ? !split_fields : !legacy_fields && !shot_fields) ||
            (hold_duration && (!lua.is_integer(4) || (count != 4 && !shot_fields))) ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3)) return "guest_pawn_save_and_guid_required";
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        if (pawn_address <= 0 || save_address <= 0 || !parse_guid(lua.get_string(), context.guid))
            return "guest_pawn_save_and_guid_required";
        if (hold_duration)
        {
            *hold_duration = lua.get_integer();
            if (*hold_duration < 250 || *hold_duration > 2400) return "native_guest_smg_hold_duration_unqualified";
        }
        if (shot_fields)
        {
            if (!read_shot_view(lua, view_fields, *shot_view)) return "guest_weapon_view_unqualified";
        }
        else if (split)
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
        else if (requested_slot && count == 4)
        {
            const auto slot = lua.get_integer();
            if (slot < 0 || slot >= 8) return "native_inventory_equip_refused_slot";
            *requested_slot = static_cast<int>(slot);
        }
        else if (count == 4 && reload_round)
            *reload_round = lua.get_string();
        else if (count == 4 && !hold_duration)
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
        const auto path = weapon->GetPathName();
        const auto match = std::ranges::find_if(profiles::firearms, [&](const auto& profile) {
            return profile.capacity > 0 && path == profile.path;
        });
        return match == profiles::firearms.end() ? nullptr : &*match;
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
            boolean(weapon, STR("Action_OnRelease"), profile.action_on_release) &&
            boolean(weapon, STR("SingleReload"), profile.single_reload) &&
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

    bool compatible_magazine_round(const profiles::StockAmmo& base, const profiles::StockAmmo& ammo)
    {
        return base.caliber == ammo.caliber;
    }

    const profiles::StockAmmo* magazine_round(const profiles::Profile& profile, Unreal::UClass* ammo)
    {
        if (!profile.ammo || !ammo) return nullptr;
        const auto path = ammo->GetPathName();
        const auto match = std::ranges::find_if(profiles::stock_ammo, [&](const profiles::StockAmmo* candidate) {
            return compatible_magazine_round(*profile.ammo, *candidate) && path == candidate->item;
        });
        return match == profiles::stock_ammo.end() ? nullptr : *match;
    }

    struct WeaponDataView
    {
        Unreal::UScriptStruct* type{};
        void* data{};
    };

    WeaponDataView weapon_data(Unreal::FStructProperty* property, const void* owner)
    {
        const auto* instanced = find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
        auto* type = find<Unreal::UScriptStruct>(
            STR("/Game/Classes/Libraries/Structs/WeaponData_Struct.WeaponData_Struct"));
        if (!owner || !property || !instanced || !type || property->GetStruct().Get() != instanced ||
            property->GetSize() != sizeof(WeaponDataView)) return {};
        // In the qualified cooked x64 build these are the reflected type and its data pointer.
        WeaponDataView view;
        std::memcpy(&view, property->ContainerPtrToValuePtr<void>(owner), sizeof(view));
        return view.type == type && view.data ? view : WeaponDataView{};
    }

    int loki_rounds(const Inventory& inventory)
    {
        auto* saved = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        const auto data = weapon_data(saved, inventory.item);
        auto* count = field<Unreal::FIntProperty>(data.type, loki_rounds_name);
        if (!count) return -1;
        const auto rounds = count->GetPropertyValueInContainer(data.data);
        return 0 <= rounds && rounds <= 4 ? rounds : -1;
    }

    const profiles::StockAmmo* round_from_handle(const profiles::Profile& profile, const WeaponDataView& loaded,
                                                const Unreal::TCHAR* member)
    {
        auto* selected = field<Unreal::FStructProperty>(loaded.type, member);
        auto* handle = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table = field<Unreal::FObjectProperty>(handle, STR("DataTable"));
        auto* name = field<Unreal::FNameProperty>(handle, STR("RowName"));
        auto* ammo_table = stock_ammo_table();
        if (!selected || !handle || selected->GetStruct().Get() != handle || !table || !name || !ammo_table)
            return nullptr;
        const auto* row = selected->ContainerPtrToValuePtr<void>(loaded.data);
        if (table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(row)) != ammo_table) return nullptr;
        const auto selected_name = name->GetPropertyValueInContainer(row);
        const auto match = std::ranges::find_if(profiles::stock_ammo, [&](const profiles::StockAmmo* candidate) {
            return compatible_magazine_round(*profile.ammo, *candidate) &&
                selected_name == Unreal::FName(candidate->row, Unreal::FNAME_Find);
        });
        return match == profiles::stock_ammo.end() || selected_name.IsNone() ? nullptr : *match;
    }

    const profiles::StockAmmo* saved_magazine_round(const profiles::Profile& profile, Unreal::UClass* weapon,
                                                    const Inventory& inventory, bool underbarrel = false)
    {
        if (!profile.ammo || !weapon) return nullptr;
        auto* defaults = weapon->GetClassDefaultObject().Get();
        const bool loki = profile.underbarrel_mount || (underbarrel && loki_mount(defaults));
        if (!loki && mounted_payload_matches(defaults, inventory)) return underbarrel ? nullptr : profile.ammo;
        auto* reference = field<Unreal::FStructProperty>(defaults, STR("DataStruct"));
        auto* saved = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        const auto stock = weapon_data(reference, defaults);
        const auto loaded = weapon_data(saved, inventory.item);
        if (!stock.type || stock.type != loaded.type || (underbarrel && !loki)) return nullptr;
        const auto* shell = loki && loki_rounds(inventory) >= 0 ? round_from_handle(loki_magazine, loaded, loki_ammo_name) : nullptr;
        if (loki && !shell) return nullptr;
        constexpr auto ammo_type = STR("AmmoType_2_039B4A454BFB079ACA16C3836A743543");
        const auto* selected = field<Unreal::FStructProperty>(stock.type, ammo_type);
        const auto* shell_count = loki ? field<Unreal::FIntProperty>(stock.type, loki_rounds_name) : nullptr;
        const auto* shell_type = loki ? field<Unreal::FStructProperty>(stock.type, loki_ammo_name) : nullptr;
        for (Unreal::TFieldIterator<Unreal::FProperty> member(stock.type); member; ++member)
        {
            if (*member != selected && *member != shell_count && *member != shell_type &&
                !member->Identical_InContainer(stock.data, loaded.data)) return nullptr;
        }
        return underbarrel ? shell : round_from_handle(profile, loaded, ammo_type);
    }

    bool ordinary_magazine_payload(const profiles::Profile* profile, const Context& context, const Inventory& inventory,
                                    Unreal::UObject* actor = nullptr, const profiles::StockAmmo* pinned_round = nullptr)
    {
        if (!profile || !profile->require_default_payload) return true;
        const auto* selected = saved_magazine_round(*profile, context.weapon, inventory);
        if (pinned_round && selected != pinned_round) return false;
        if (!selected || !actor) return selected != nullptr;
        auto* ammo = field<Unreal::FStructProperty>(actor, STR("AmmoType"));
        auto* type = find<Unreal::UScriptStruct>(ammo_type_path);
        if (!type || !ammo || ammo->GetStruct().Get() != type || !mounted_payload_matches(actor, inventory)) return false;
        const void* expected = nullptr;
        if (selected == &profiles::nine_mm_ball)
            expected = ammo->ContainerPtrToValuePtr<void>(context.weapon->GetClassDefaultObject().Get());
        else
        {
            auto* table = stock_ammo_table();
            const Unreal::FName row{selected->row, Unreal::FNAME_Find};
            if (table && !row.IsNone()) expected = table->FindRowUnchecked(row);
        }
        return expected && ammo->Identical(ammo->ContainerPtrToValuePtr<void>(actor), expected);
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
        const bool spade = context.weapon == find<Unreal::UClass>(spade_path);
        const bool melee_tool = old_pipe || crowbar || spade;
        const auto melee_equipped = spade ? "spade_equipped" : crowbar ? "crowbar_equipped" : "old_pipe_equipped";
        const auto melee_refused = spade ? "stock_guest_spade_row_unqualified" : crowbar ?
            "stock_guest_crowbar_row_unqualified" : "stock_guest_old_pipe_row_unqualified";
        const bool blowtorch = context.weapon == find<Unreal::UClass>(blowtorch_path);
        const auto* magazine_profile = camera || blowtorch ? nullptr : stock_magazine(context.weapon);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory))
            return result(lua, false, "guest_weapon_payload_unqualified");
        if (auto* actor = equipped(context, inventory))
        {
            if (!camera && !blowtorch && !melee_tool)
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
            if (melee_tool)
            {
                const bool verified = stored == -1 && ammo && ammo->GetPropertyValueInContainer(actor) == stored;
                return result(lua, verified, verified ? melee_equipped : melee_refused);
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
        else if (melee_tool)
        {
            if (loaded != -1) return result(lua, false, melee_refused);
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
        if (spade)
            return result(lua, proven, proven ? "spade_equipped" : "mutation_unverified_guest_spade_equip");
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
        const bool spade = context.weapon == find<Unreal::UClass>(spade_path);
        const bool blowtorch = context.weapon == find<Unreal::UClass>(blowtorch_path);
        const auto melee_refused = spade ? "stock_guest_spade_row_unqualified" : crowbar ?
            "stock_guest_crowbar_row_unqualified" : "stock_guest_old_pipe_row_unqualified";
        if ((old_pipe || crowbar || spade) && stored != -1) return proof(lua, false, melee_refused);
        if (camera && !camera_stock_ammo(stored)) return proof(lua, false, "guest_camera_row_unqualified");
        if (blowtorch && (stored < 0 || stored > blowtorch_capacity))
            return proof(lua, false, "stock_guest_blowtorch_fuel_unqualified");
        const auto* magazine_profile = camera || blowtorch ? nullptr : stock_magazine(context.weapon);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory))
            return proof(lua, false, "guest_weapon_payload_unqualified");
        if (inventory.backpack_index >= 0)
            return proof(lua, true, spade ? "spade_backpack" : old_pipe ? "old_pipe_backpack" : crowbar ? "crowbar_backpack" : camera ? "camera_backpack" :
                blowtorch ? "blowtorch_backpack" : "backpack", -1, stored);
        auto* actor = equipped(context, inventory);
        if (!actor)
            return proof(lua, true, spade ? "spade_stored_equipment" : old_pipe ? "old_pipe_stored_equipment" : crowbar ? "crowbar_stored_equipment" : camera ? "camera_stored_equipment" :
                blowtorch ? "blowtorch_stored_equipment" : "stored_equipment", -1, stored);
        if (!ordinary_magazine_payload(magazine_profile, context, inventory, actor))
            return proof(lua, false, "guest_weapon_payload_unqualified");
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        if (!ammo) return proof(lua, false, "stock_guest_weapon_ammo_unavailable");
        const auto observed = ammo->GetPropertyValueInContainer(actor);
        if (camera && (observed != stored || !camera_capture_ready(actor)))
            return proof(lua, false, "stock_guest_camera_capture_unavailable");
        if ((old_pipe || crowbar || spade) && observed != stored) return proof(lua, false, melee_refused);
        if (blowtorch && observed != stored) return proof(lua, false, "stock_guest_blowtorch_fuel_unqualified");
        return proof(lua, true, spade ? "spade_equipped" : old_pipe ? "old_pipe_equipped" : crowbar ? "crowbar_equipped" : camera ? "camera_equipped" :
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

    int inspect_underbarrel(const Lua& lua, const Context& context, const Inventory& inventory)
    {
        auto* receiver = equipped(context, inventory);
        auto* child = mounted_loki(receiver, context.pawn);
        auto* ammo = field<Unreal::FIntProperty>(child, STR("Ammo"));
        const auto* round = saved_magazine_round(loki_magazine, context.weapon, inventory, true);
        const auto stored = loki_rounds(inventory);
        if (!child || !ammo || !round || stored < 0 || ammo->GetPropertyValueInContainer(child) != stored ||
            !stock_round_matches(child, mounted_ammo(child), stock_projectile(child, round->projectile_count), round))
            return proof(lua, false, "guest_weapon_underbarrel_payload_unqualified");
        proof(lua, true, "underbarrel_equipped", stored, stored);
        lua.set_string(RC::to_utf8_string(round->item));
        return 5;
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

    // A round can hit and destroy its projectile during the same Blueprint call.
    // Count the exact actor when it starts play, before stock impact processing.
    std::string_view reconcile_projectile_contact(Unreal::AActor* projectile, Unreal::UObject* pawn,
                                                  const ShotView& view, bool single_projectile);
    struct ReportedNpcHit
    {
        Unreal::FWeakObjectPtr projectile;
        Unreal::FWeakObjectPtr target;
        AimVector direction{};
    };
    ReportedNpcHit reported_npc_hit(Unreal::AActor* projectile, Unreal::UObject* pawn, const ShotView& view);
    void commit_reported_npc_hit(const ReportedNpcHit& hit, Unreal::UObject* pawn);

    thread_local std::string_view fixture_shot_contact;

    bool fixture_shot_contact_enabled()
    {
        static const bool enabled = env("SILVERFISH_COOP_HEADLESS") &&
            (env("SILVERFISH_COOP_NATIVE_FIXTURES") || env("SILVERFISH_COOP_TITLE_FIXTURES"));
        return enabled;
    }

    int read_fixture_shot_contact(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 0) return 0;
        lua.set_string(fixture_shot_contact);
        return 1;
    }

    struct TapBirths
    {
        Unreal::UWorld* world;
        Unreal::UObject* pawn;
        Unreal::UClass* projectile;
        const ShotView* view;
        bool single_projectile{};
        int count{};
        bool owned{true};
        ReportedNpcHit hit;
    };

    thread_local TapBirths* active_tap{};
    auto tap_birth_hook = Hook::ERROR_ID;

    struct TapObservation
    {
        explicit TapObservation(TapBirths* shot) : previous(std::exchange(active_tap, shot)) {}
        ~TapObservation() { active_tap = previous; }

        TapBirths* previous;
    };

    void count_guest_projectile(Unreal::AActor* actor, Unreal::UObject* pawn, int& count, bool& owned,
                                const ShotView* view, bool single_projectile, ReportedNpcHit& pending)
    {
        if (!pawn) return;
        const auto* owner = object(actor, STR("Owner"));
        const auto* instigator = object(actor, STR("Instigator"));
        // Reactions inside the same Blueprint call can launch another actor's weapon.
        // A round with only one matching identity still invalidates the receipt.
        if (owner != pawn && instigator != pawn) return;
        ++count;
        owned = owned && authority(actor) && owner == pawn && instigator == pawn;
        std::string_view result = "no_contact";
        if (owned && view && view->contact)
        {
            if (single_projectile) pending = reported_npc_hit(actor, pawn, *view);
            result = pending.target.Get() ? "guest_npc_report_received" :
                reconcile_projectile_contact(actor, pawn, *view, single_projectile);
        }
        if (fixture_shot_contact_enabled()) fixture_shot_contact = result;
    }

    bool install_tap_birth_hook()
    {
        if (tap_birth_hook != Hook::ERROR_ID) return true;
        tap_birth_hook = Hook::RegisterBeginPlayPreCallback(
            [](Hook::TCallbackIterationData<void>&, Unreal::AActor* actor) {
                auto* shot = active_tap;
                if (!shot || !actor || actor->GetClassPrivate() != shot->projectile || actor->GetWorld() != shot->world) return;
                count_guest_projectile(actor, shot->pawn, shot->count, shot->owned, shot->view, shot->single_projectile, shot->hit);
            }, {false, false, STR("SilverfishCoopNative"), STR("Count stock guest tap projectiles before impact")});
        return tap_birth_hook != Hook::ERROR_ID;
    }

    constexpr auto aim_vector_type = STR("/Script/CoreUObject.Vector");
    constexpr auto aim_rotator_type = STR("/Script/CoreUObject.Rotator");
    constexpr auto aim_hit_type = STR("/Script/Engine.HitResult");
    constexpr auto scene_component_type = STR("/Script/Engine.SceneComponent");

    bool aim_vector_field(Unreal::FStructProperty* property, const Unreal::TCHAR* type)
    {
        return property && property->GetSize() == sizeof(AimVector) &&
            property->GetStruct().Get() == find<Unreal::UScriptStruct>(type);
    }

    std::optional<AimVector> read_aim_vector(Unreal::FStructProperty* property, const void* source)
    {
        AimVector result{};
        std::memcpy(result.data(), property->ContainerPtrToValuePtr<void>(source), sizeof(result));
        if (!std::ranges::all_of(result, [](double value) { return std::isfinite(value); })) return {};
        return result;
    }

    std::optional<AimVector> actor_aim_vector(Unreal::UObject* object, const Unreal::TCHAR* path)
    {
        auto* fn = find<Unreal::UFunction>(path);
        auto* result = fn ? Unreal::CastField<Unreal::FStructProperty>(fn->GetReturnProperty()) : nullptr;
        if (!fn || !fn->HasAnyFunctionFlags(Unreal::FUNC_Native) || fn->GetNumParms() != 1 ||
            fn->GetParmsSize() != sizeof(AimVector) || !aim_vector_field(result, aim_vector_type) ||
            result->GetOffset_Internal() != 0) return {};
        AimVector storage{};
        object->ProcessEvent(fn, storage.data());
        return read_aim_vector(result, storage.data());
    }

    Unreal::UObject* shot_component(Unreal::UObject* owner, const Unreal::TCHAR* name, const Unreal::TCHAR* type)
    {
        auto* component = object(owner, name);
        return live(component) && component->GetOuterPrivate() == owner &&
            component->GetClassPrivate() == find<Unreal::UClass>(type) ? component : nullptr;
    }

    constexpr auto component_location = STR("/Script/Engine.SceneComponent:K2_GetComponentLocation");
    constexpr auto component_forward = STR("/Script/Engine.SceneComponent:GetForwardVector");
    constexpr auto camera_component = STR("/Script/Engine.CameraComponent");
    constexpr auto muzzle_component = STR("/Script/Engine.ArrowComponent");

    struct AimParameters
    {
        alignas(16) std::array<std::byte, 512> data{};
        std::array<Unreal::FProperty*, 2> initialized{};
        std::size_t count{};

        void initialize(Unreal::FProperty* property)
        {
            property->InitializeValue_InContainer(data.data());
            initialized[count++] = property;
        }

        ~AimParameters()
        {
            while (count) initialized[--count]->DestroyValue_InContainer(data.data());
        }
    };

    struct AimContact
    {
        AimVector point;
        Unreal::UObject* actor{};
        bool hit{};
    };

    struct ContactHitResult
    {
        Unreal::UObject* actor;
        Unreal::FStructProperty* field;
        void* storage;
    };

    std::optional<AimContact> trace_shot(Unreal::UObject* context_object, const AimVector& origin,
                                        const AimVector& destination, std::uint8_t trace_channel,
                                        std::span<Unreal::UObject* const> exclusions, ContactHitResult* copy = nullptr)
    {
        auto* library = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetSystemLibrary"));
        auto* fn = find<Unreal::UFunction>(STR("/Script/Engine.KismetSystemLibrary:LineTraceSingle"));
        if (!library || !fn || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            !fn->HasAnyFunctionFlags(Unreal::FUNC_Native) || fn->GetNumParms() != 13 ||
            fn->GetParmsSize() > sizeof(AimParameters::data)) return {};
        auto* context = input<Unreal::FObjectProperty>(fn, STR("WorldContextObject"), 0);
        auto* start = input<Unreal::FStructProperty>(fn, STR("Start"), 8);
        auto* end = input<Unreal::FStructProperty>(fn, STR("End"), 0x20);
        auto* channel = input<Unreal::FByteProperty>(fn, STR("TraceChannel"), 0x38);
        auto* complex = input<Unreal::FBoolProperty>(fn, STR("bTraceComplex"), 0x39);
        auto* ignored = field<Unreal::FArrayProperty>(fn, STR("ActorsToIgnore"));
        auto* actor = ignored ? Unreal::CastField<Unreal::FObjectProperty>(ignored->GetInner()) : nullptr;
        auto* hit = field<Unreal::FStructProperty>(fn, STR("OutHit"));
        auto* hit_type = find<Unreal::UScriptStruct>(aim_hit_type);
        auto* impact = field<Unreal::FStructProperty>(hit_type, STR("ImpactPoint"));
        auto* ignore_self = field<Unreal::FBoolProperty>(fn, STR("bIgnoreSelf"));
        auto* found = Unreal::CastField<Unreal::FBoolProperty>(fn->GetReturnProperty());
        auto* hit_component = field<Unreal::FWeakObjectProperty>(hit_type, STR("Component"));
        if (!context || !aim_vector_field(start, aim_vector_type) || !aim_vector_field(end, aim_vector_type) ||
            !channel || !complex || !actor || actor->GetOffset_Internal() != 0 ||
            !hit || !hit_type || hit->GetStruct().Get() != hit_type || !hit->HasAnyPropertyFlags(Unreal::CPF_OutParm) ||
            !aim_vector_field(impact, STR("/Script/Engine.Vector_NetQuantize")) ||
            !ignore_self || !found) return {};

        AimParameters arguments;
        arguments.initialize(ignored);
        arguments.initialize(hit);
        auto* data = arguments.data.data();
        Unreal::FScriptArrayHelper actors(ignored, ignored->ContainerPtrToValuePtr<void>(data));
        for (auto* exclusion : exclusions)
            if (exclusion) actor->SetObjectPropertyValue(actors.GetRawPtr(actors.AddValue()), exclusion);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(data), context_object);
        std::memcpy(start->ContainerPtrToValuePtr<void>(data), origin.data(), sizeof(origin));
        std::memcpy(end->ContainerPtrToValuePtr<void>(data), destination.data(), sizeof(destination));
        channel->SetPropertyValueInContainer(data, trace_channel);
        complex->SetPropertyValueInContainer(data, true);
        ignore_self->SetPropertyValueInContainer(data, true);
        library->ProcessEvent(fn, data);
        if (!found->GetPropertyValueInContainer(data)) return AimContact{destination};
        const auto* result = hit->ContainerPtrToValuePtr<void>(data);
        const auto point = read_aim_vector(impact, result);
        if (!point) return {};
        auto* component = hit_component ? hit_component->GetPropertyValueInContainer(result).Get() : nullptr;
        auto* hit_actor = live(component) ? component->GetOuterPrivate() : nullptr;
        if (copy && hit_actor == copy->actor)
            copy->field->CopyCompleteValue(copy->field->ContainerPtrToValuePtr<void>(copy->storage), result);
        return AimContact{*point, hit_actor, true};
    }

    constexpr std::uint8_t visibility_trace = 1;
    constexpr std::uint8_t projectile_trace = 2; // Stock projectile MovementUpdate.
    constexpr std::size_t max_contact_barriers = 3;

    AimVector difference(const AimVector& left, const AimVector& right)
    {
        return {left[0] - right[0], left[1] - right[1], left[2] - right[2]};
    }

    AimVector ahead(const AimVector& point, const AimVector& forward, double distance)
    {
        return {point[0] + forward[0] * distance, point[1] + forward[1] * distance, point[2] + forward[2] * distance};
    }

    double squared_length(const AimVector& vector)
    {
        return vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2];
    }

    AimVector shot_axis(const AimVector& muzzle, const AimVector& target, const AimVector& view_forward)
    {
        const auto toward = difference(target, muzzle);
        const auto forward_distance = toward[0] * view_forward[0] + toward[1] * view_forward[1] +
            toward[2] * view_forward[2];
        // An obstruction between the eye and muzzle cannot make the round travel backward.
        if (forward_distance < 1.0) return view_forward;
        const double length = std::sqrt(squared_length(toward));
        return {toward[0] / length, toward[1] / length, toward[2] / length};
    }

    double stock_sight_range(Unreal::UObject* pawn)
    {
        auto* range = field<Unreal::FDoubleProperty>(pawn, STR("SightRange"));
        const double distance = range ? range->GetPropertyValueInContainer(pawn) : 0.0;
        return std::isfinite(distance) && distance > 0.0 ? distance : 0.0;
    }

    double projectile_real(Unreal::UObject* projectile, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FNumericProperty>(projectile, name);
        return property && property->IsFloatingPoint()
            ? property->GetFloatingPointPropertyValue(property->ContainerPtrToValuePtr<void>(projectile)) : 0.0;
    }

    std::optional<AimContact> guest_aim_point(Unreal::UObject* pawn, Unreal::UObject* weapon,
                                             const AimVector& eye, const AimVector& forward)
    {
        const auto distance = stock_sight_range(pawn);
        if (distance <= 0.0) return {};
        return trace_shot(pawn, eye, ahead(eye, forward, distance), visibility_trace, std::array{weapon});
    }

    bool available_projectile_target(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        auto* target = live(actor) ? Unreal::Cast<Unreal::AActor>(actor) : nullptr;
        if (!target || target->GetWorld() != world) return false;
        // Characters expose Alive. Stock destructible props only expose Health.
        auto* alive = field<Unreal::FBoolProperty>(target, STR("Alive"));
        if (alive && !alive->GetPropertyValueInContainer(target)) return false;
        static const Unreal::FName player_name{STR("Player_FP_C")};
        for (const Unreal::UStruct* type = target->GetClassPrivate(); type; type = type->GetSuperStruct())
            if (type->GetFName() == player_name && type->GetPathName() == pawn_path) return false;
        return true;
    }

    bool takes_projectile_damage(Unreal::UObject* actor)
    {
        const Unreal::FName damage_function{STR("DealDamage"), Unreal::FNAME_Find};
        return actor->GetFunctionByNameInChain(damage_function) != nullptr;
    }

    struct ContactReference
    {
        Unreal::FWeakObjectPtr marker;
        unsigned number;
    };
    std::map<Unreal::UObject*, ContactReference> contact_references;

    unsigned contact_reference(Unreal::UObject* target)
    {
        auto bound = contact_references.find(target);
        if (bound == contact_references.end()) return 0;
        auto* marker = bound->second.marker.Get();
        if (live(marker) && object(marker, STR("Owner")) == target) return bound->second.number;
        contact_references.erase(bound);
        return 0;
    }

    int bind_contact_reference(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw()) return 0;
        if (lua.get_stack_size() == 0)
        {
            contact_references.clear();
            return 0;
        }
        if (!lua.is_string()) return 0;
        const auto path = RC::to_generic_string(lua.get_string());
        auto* candidate = find<Unreal::UObject>(path.c_str());
        if (!live(candidate) || candidate->GetClassPrivate() != find<Unreal::UClass>(STR("/Script/Engine.TargetPoint")))
            return 0;
        auto* marker = static_cast<Unreal::AActor*>(candidate);
        auto* target = object(marker, STR("Owner"));
        if (!available_projectile_target(target, marker->GetWorld()) || !silverfish::campaign_npc::is_spawned_stock_npc(target))
            return 0;
        const auto point = actor_aim_vector(marker, STR("/Script/Engine.Actor:K2_GetActorLocation"));
        if (!point) return 0;
        const auto height = (*point)[2] - 262144; // replicated_actor_reference.lua
        const auto id = std::round(height);
        if (id >= 1 && id <= 65535 && std::abs(height - id) < .25)
            contact_references.insert_or_assign(target, ContactReference{Unreal::FWeakObjectPtr(marker), static_cast<unsigned>(id)});
        return 0;
    }

    Unreal::UObject* reported_npc(const ShotView& view, Unreal::UWorld* world)
    {
        const auto number = contact_number(view.contact_actor);
        Unreal::UObject* target{};
        if (number)
        {
            for (const auto& [candidate, reference] : contact_references)
            {
                if (reference.number != number) continue;
                auto* marker = reference.marker.Get();
                if (live(marker) && object(marker, STR("Owner")) == candidate)
                {
                    target = candidate;
                    break;
                }
            }
        }
        else
        {
            const auto path = RC::to_generic_string(view.contact_actor);
            auto* candidate = find<Unreal::UObject>(path.c_str());
            if (live(candidate) && candidate->GetPathName() == path &&
                !silverfish::campaign_npc::is_spawned_stock_npc(candidate)) target = candidate;
        }
        return authority(target) && available_projectile_target(target, world) &&
            field<Unreal::FBoolProperty>(target, STR("Alive")) && takes_projectile_damage(target) ? target : nullptr;
    }

    ReportedNpcHit reported_npc_hit(Unreal::AActor* projectile, Unreal::UObject* pawn, const ShotView& view)
    {
        if (!view.contact || !view.muzzle || !view.target) return {};
        const auto origin = actor_aim_vector(projectile, STR("/Script/Engine.Actor:K2_GetActorLocation"));
        auto* velocity_field = field<Unreal::FStructProperty>(projectile, STR("Velocity"));
        if (!origin || !aim_vector_field(velocity_field, aim_vector_type) ||
            squared_length(difference(*origin, *view.muzzle)) > 1.0) return {};
        const auto velocity = read_aim_vector(velocity_field, projectile);
        if (!velocity) return {};
        const auto offset = difference(*view.contact, *origin);
        const auto distance = std::sqrt(squared_length(offset));
        const auto speed = std::sqrt(squared_length(*velocity));
        const auto life = projectile_real(projectile, STR("Life"));
        const auto sight = stock_sight_range(pawn);
        const auto axis = shot_axis(*view.muzzle, *view.target, view.direction);
        if (!(distance >= 1.0 && speed >= 1.0 && life > 0.0 && distance <= std::min(sight, speed * life)) ||
            squared_length(difference(ahead(*origin, axis, distance), *view.contact)) > 1.0) return {};
        auto* target = reported_npc(view, projectile->GetWorld());
        if (!target) return {};
        return {Unreal::FWeakObjectPtr(projectile), Unreal::FWeakObjectPtr(target), axis};
    }

    void commit_reported_npc_hit(const ReportedNpcHit& hit, Unreal::UObject* pawn)
    {
        auto* projectile = Unreal::Cast<Unreal::AActor>(hit.projectile.Get());
        auto* target = hit.target.Get();
        if (!authority(projectile) || !authority(target) || !available_projectile_target(target, projectile->GetWorld()) ||
            object(projectile, STR("Instigator")) != pawn || object(projectile, STR("Owner")) != pawn) return;
        constexpr auto base = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
        constexpr auto calc_path = STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C:CalcDmg");
        auto* projectile_class = find<Unreal::UClass>(base);
        auto* calc = find<Unreal::UFunction>(calc_path);
        auto* result = input<Unreal::FStructProperty>(calc, STR("Hit"), 0);
        if (!calc || !projectile_class || !projectile->IsA(projectile_class) ||
            calc->GetOuterPrivate() != projectile_class || projectile->GetFunctionByNameInChain(calc->GetFName()) != calc ||
            calc->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || calc->GetNumParms() != 1 ||
            calc->GetReturnProperty() || !result || result->GetStruct().Get() != find<Unreal::UScriptStruct>(aim_hit_type) ||
            calc->GetParmsSize() != result->GetSize() || calc->GetParmsSize() > sizeof(AimParameters::data)) return;

        // Find the current target's hit component locally. The original route
        // through the host's world cannot veto the guest's contact.
        const auto center = actor_aim_vector(target, STR("/Script/Engine.Actor:K2_GetActorLocation"));
        if (!center) return;
        AimParameters arguments;
        arguments.initialize(result);
        ContactHitResult copy{target, result, arguments.data.data()};
        const std::array ignored{pawn, object(pawn, STR("EquipedItem"))};
        for (double height : {0.0, 35.0, -35.0, 70.0})
        {
            const auto point = ahead(*center, {0, 0, 1}, height);
            const auto start = ahead(point, hit.direction, -160.0);
            const auto end = ahead(point, hit.direction, 160.0);
            const auto contact = trace_shot(projectile, start, end, projectile_trace, ignored, &copy);
            if (!contact || contact->actor != target) continue;
            // The original shot has already spent its stock round. Resolve one
            // stock damage call and retire its projectile before it can hit again.
            projectile->SetActorTickEnabled(false);
            projectile->ProcessEvent(calc, arguments.data.data());
            if (auto* remaining = Unreal::Cast<Unreal::AActor>(hit.projectile.Get()); live(remaining))
                remaining->K2_DestroyActor();
            if (fixture_shot_contact_enabled()) fixture_shot_contact = "committed_guest_npc_contact";
            return;
        }
        if (fixture_shot_contact_enabled()) fixture_shot_contact = "guest_npc_component_unavailable";
    }

    struct HostShotLane
    {
        std::array<Unreal::UObject*, 2 + max_contact_barriers> ignored;
        std::size_t count{2};
        Unreal::UObject* target{};
        bool obstructed{};
    };

    HostShotLane host_shot_lane(Unreal::AActor* projectile, Unreal::UObject* pawn, const ShotView& view,
                               const AimVector& origin, const AimVector& end)
    {
        HostShotLane lane{{pawn, object(pawn, STR("EquipedItem"))}};
        const auto number = contact_number(view.contact_actor);
        const auto reported_actor = number ? RC::StringType{} : RC::to_generic_string(view.contact_actor);
        while (const auto hit = trace_shot(projectile, origin, end, projectile_trace, std::span{lane.ignored}.first(lane.count)))
        {
            auto* actor = hit->actor;
            if (!hit->hit) break;
            if (!live(actor) || !authority(actor))
            {
                lane.obstructed = true;
                break;
            }
            const bool damageable = takes_projectile_damage(actor);
            const bool matches = number ? contact_reference(actor) == number :
                actor->GetPathName() == reported_actor && !silverfish::campaign_npc::is_spawned_stock_npc(actor);
            if (matches && damageable && squared_length(difference(hit->point, *view.contact)) <= 100 * 100 &&
                available_projectile_target(actor, projectile->GetWorld()))
            {
                lane.target = actor;
                break;
            }
            // Preserve health-bearing cover and other targets in front of the reported hit.
            const auto ignored = std::span{lane.ignored}.first(lane.count);
            if (lane.count == lane.ignored.size() || damageable || std::ranges::find(ignored, actor) != ignored.end())
            {
                lane.obstructed = true;
                break;
            }
            lane.ignored[lane.count++] = actor;
        }
        return lane;
    }

    std::string_view reconcile_projectile_contact(Unreal::AActor* projectile, Unreal::UObject* pawn,
                                                  const ShotView& view, bool single_projectile)
    {
        if (!view.contact || view.contact_actor.empty()) return "no_contact";
        auto* velocity_field = field<Unreal::FStructProperty>(projectile, STR("Velocity"));
        if (!aim_vector_field(velocity_field, aim_vector_type)) return "velocity_schema";
        const auto origin = actor_aim_vector(projectile, STR("/Script/Engine.Actor:K2_GetActorLocation"));
        const auto velocity = read_aim_vector(velocity_field, projectile);
        if (!origin || !velocity) return "velocity_unavailable";
        const auto offset = difference(*view.contact, *origin);
        const double speed = std::sqrt(squared_length(*velocity));
        const double distance = std::sqrt(squared_length(offset));
        const auto remaining_life = projectile_real(projectile, STR("Life"));
        const auto gravity = std::abs(projectile_real(projectile, STR("Gravity")));
        const auto sight = stock_sight_range(pawn);
        const auto flight_range = (speed + .5 * gravity * remaining_life) * remaining_life;
        if (speed < 1 || remaining_life <= 0.0 || !std::isfinite(flight_range) || !std::isfinite(distance) ||
            sight <= 0.0 || distance < 1 || distance > std::min(sight, flight_range)) return "outside_flight_range";

        const AimVector direction{(*velocity)[0] / speed, (*velocity)[1] / speed, (*velocity)[2] / speed};
        if (squared_length(difference(ahead(*origin, direction, distance), *view.contact)) > 75 * 75)
            return "outside_stock_spread";
        auto lane = host_shot_lane(projectile, pawn, view, *origin, ahead(*origin, direction, distance + 100));
        if (lane.obstructed) return "host_damageable_cover";
        if (lane.count == 2) return lane.target ? "stock_contact" : "stock_miss";

        std::optional<AimVector> corrected_velocity;
        if (!lane.target && single_projectile)
        {
            // The host's random spread can miss the exact actor seen on the guest's
            // centerline after a host-only obstacle. Require a real stock collision
            // with that same actor before aligning this round to the reported ray.
            const auto contact_end = ahead(*view.contact, offset, 100.0 / distance);
            auto contact_lane = host_shot_lane(projectile, pawn, view, *origin, contact_end);
            if (contact_lane.obstructed || !contact_lane.target) return "guest_contact_unmatched";
            // Keep exclusions for the final ray only, so its own collision stays intact.
            lane = contact_lane;
            corrected_velocity = ahead(AimVector{}, offset, speed / distance);
        }
        if (!lane.target) return "stock_spread_miss";
        auto* health = field<Unreal::FDoubleProperty>(lane.target, STR("Health"));
        const auto current_health = health ? health->GetPropertyValueInContainer(lane.target) : 0.0;
        if (!std::isfinite(current_health) || current_health <= 0) return "target_unavailable";

        auto* ignored = field<Unreal::FArrayProperty>(projectile, STR("Actors to Ignore"));
        auto* actor = ignored ? Unreal::CastField<Unreal::FObjectProperty>(ignored->GetInner()) : nullptr;
        if (!actor || actor->GetOffset_Internal() != 0) return "collision_schema";
        Unreal::FScriptArrayHelper actors(ignored, ignored->ContainerPtrToValuePtr<void>(projectile));
        for (auto* barrier : std::span{lane.ignored}.subspan(2, lane.count - 2))
        {
            bool present = false;
            for (int index = 0; index < actors.Num() && !present; ++index)
                present = actor->GetObjectPropertyValue(actors.GetRawPtr(index)) == barrier;
            if (!present) actor->SetObjectPropertyValue(actors.GetRawPtr(actors.AddValue()), barrier);
        }
        if (corrected_velocity)
            std::memcpy(velocity_field->ContainerPtrToValuePtr<void>(projectile), corrected_velocity->data(), sizeof(AimVector));
        return corrected_velocity ? "matched_guest_ray" : "matched_stock_ray";
    }

    struct GuestMuzzleAim
    {
        Unreal::FWeakObjectPtr component;
        AimVector previous{};
        std::optional<AimVector> previous_location;

        static bool set_pose(Unreal::UObject* muzzle, const AimVector& rotation, bool relative,
                             const std::optional<AimVector>& position)
        {
            const auto method = position
                ? (relative ? STR("/Script/Engine.SceneComponent:K2_SetRelativeLocationAndRotation")
                            : STR("/Script/Engine.SceneComponent:K2_SetWorldLocationAndRotation"))
                : (relative ? STR("/Script/Engine.SceneComponent:K2_SetRelativeRotation")
                            : STR("/Script/Engine.SceneComponent:K2_SetWorldRotation"));
            const int offset = position ? sizeof(AimVector) : 0;
            auto* fn = find<Unreal::UFunction>(method);
            auto* location = position ? input<Unreal::FStructProperty>(fn, STR("NewLocation"), 0) : nullptr;
            auto* value = input<Unreal::FStructProperty>(fn, STR("NewRotation"), offset);
            auto* sweep = input<Unreal::FBoolProperty>(fn, STR("bSweep"), offset + 0x18);
            auto* hit = field<Unreal::FStructProperty>(fn, STR("SweepHitResult"));
            auto* teleport = input<Unreal::FBoolProperty>(fn, STR("bTeleport"), offset + 0x118);
            if (!fn || fn->GetOuterPrivate() != find<Unreal::UClass>(scene_component_type) ||
                !fn->HasAnyFunctionFlags(Unreal::FUNC_Native) || fn->GetNumParms() != (position ? 5 : 4) ||
                fn->GetReturnProperty() || (position && !aim_vector_field(location, aim_vector_type)) ||
                fn->GetParmsSize() > sizeof(AimParameters::data) || !aim_vector_field(value, aim_rotator_type) ||
                !sweep || !hit || hit->GetOffset_Internal() != offset + 0x20 ||
                hit->GetStruct().Get() != find<Unreal::UScriptStruct>(aim_hit_type) ||
                !hit->HasAnyPropertyFlags(Unreal::CPF_OutParm) || !teleport) return false;
            AimParameters args;
            args.initialize(hit);
            auto* data = args.data.data();
            if (position) std::memcpy(location->ContainerPtrToValuePtr<void>(data), position->data(), sizeof(AimVector));
            std::memcpy(value->ContainerPtrToValuePtr<void>(data), rotation.data(), sizeof(rotation));
            sweep->SetPropertyValueInContainer(data, false);
            teleport->SetPropertyValueInContainer(data, true);
            muzzle->ProcessEvent(fn, data);
            return true;
        }

        bool apply(Unreal::UObject* pawn, Unreal::UObject* weapon, const ShotView& view = {},
                   Unreal::UObject* barrel = nullptr)
        {
            previous_location.reset();
            auto* muzzle = shot_component(barrel ? barrel : weapon, STR("Arrow_Muzzle"), muzzle_component);
            if (!muzzle) return false;
            auto* relative = field<Unreal::FStructProperty>(muzzle, STR("RelativeRotation"));
            if (!aim_vector_field(relative, aim_rotator_type)) return false;
            const auto old = read_aim_vector(relative, muzzle);
            if (!old) return false;

            auto position = view.muzzle;
            auto target = view.target;
            auto direction = view.direction;
            auto origin = position;
            if (!position || !target)
            {
                // Direction-only and eye-only inputs predate an exact guest muzzle capture.
                auto* camera = shot_component(pawn, STR("Camera"), camera_component);
                if (!camera) return false;
                const auto eye = actor_aim_vector(camera, component_location);
                origin = actor_aim_vector(muzzle, component_location);
                if (!eye || !origin) return false;
                if (direction == AimVector{})
                {
                    const auto forward = actor_aim_vector(camera, component_forward);
                    if (!forward) return false;
                    direction = *forward;
                }
                if (!target)
                {
                    const auto aim = guest_aim_point(pawn, weapon, view.eye.value_or(*eye), direction);
                    if (!aim) return false;
                    target = aim->point;
                }
                if (!position && view.eye) position = ahead(*view.eye, difference(*origin, *eye), 1.0);
            }
            if (std::abs(std::hypot(direction[0], direction[1], direction[2]) - 1.0) > 0.001) return false;
            if (position)
            {
                auto* relative_location = field<Unreal::FStructProperty>(muzzle, STR("RelativeLocation"));
                if (!aim_vector_field(relative_location, aim_vector_type)) return false;
                previous_location = read_aim_vector(relative_location, muzzle);
                if (!previous_location) return false;
            }
            const auto toward = shot_axis(position ? *position : *origin, *target, direction);
            constexpr double degrees = 180.0 / std::numbers::pi;
            const AimVector rotation{std::atan2(toward[2], std::hypot(toward[0], toward[1])) * degrees,
                                     std::atan2(toward[1], toward[0]) * degrees, 0.0};
            if (!set_pose(muzzle, rotation, false, position)) return false;
            component = muzzle;
            previous = *old;
            return true;
        }

        bool restore()
        {
            auto* muzzle = component.Get();
            if (!muzzle) return true;
            component.Reset();
            return live(muzzle) && set_pose(muzzle, previous, true, previous_location);
        }

        ~GuestMuzzleAim() { restore(); }
    };

    int fire(const Lua& lua, const Context& context, const Inventory& inventory, bool disarm,
             const ShotView& shot_view, bool underbarrel)
    {
        const auto report = [&](bool ok, std::string_view status, std::int32_t first = -1, std::int32_t second = -1,
                                int projectiles = 0) {
            return proof(lua, ok, status, first, second, disarm ? -1 : projectiles);
        };
        auto* receiver = equipped(context, inventory);
        if (!receiver) return report(false, "guest_weapon_actor_not_equipped");
        auto* actor = underbarrel ? mounted_loki(receiver, context.pawn) : receiver;
        if (!actor) return report(false, "guest_weapon_underbarrel_unavailable");

        const Unreal::FName use_name{STR("Use_Item_Primary")};
        const auto* bolt = underbarrel ? nullptr : stock_bolt(context.weapon);
        const auto* stock_profile = underbarrel ? &loki_magazine : stock_magazine(context.weapon);
        auto* base_action = find<Unreal::UFunction>(bolt ? bolt->primary : primary_path);
        auto* action = actor->GetFunctionByNameInChain(use_name);
        // Some registered firearms forward through a Blueprint override. Preserve that
        // stock dispatch, while checking the function still implements the audited input.
        const auto descends_from_input = [&] {
            if (!base_action || !action) return false;
            if (bolt || !stock_profile) return action == base_action;
            for (auto* inherited = static_cast<Unreal::UStruct*>(action); inherited; inherited = inherited->GetSuperStruct())
                if (inherited == base_action) return true;
            return false;
        };
        struct Parameters { bool held{}; double variance{}; } parameters;
        auto* held = input<Unreal::FBoolProperty>(action, STR("Held"), offsetof(Parameters, held));
        auto* variance = input<Unreal::FDoubleProperty>(action, STR("VarianceScaler"), offsetof(Parameters, variance));
        auto* proxy = field<Unreal::FObjectProperty>(actor, STR("InputIntercept"));
        if (!held || !variance || !proxy || object(actor, STR("InputIntercept")) ||
            !descends_from_input() || action->GetNumParms() != 2 ||
            action->GetReturnProperty() || action->GetParmsSize() != sizeof(Parameters) ||
            action->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            (bolt && action->GetOuterPrivate() != context.weapon))
            return report(false, "stock_guest_weapon_primary_schema_unavailable");

        const bool fires_on_release = bolt || (stock_profile && stock_profile->action_on_release);
        if (!boolean(actor, STR("Action_OnRelease"), fires_on_release) || (disarm && fires_on_release) ||
            (bolt && !stock_bolt_shape(actor, *bolt)) ||
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
        const auto primary_before = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        const auto stored = underbarrel ? loki_rounds(inventory) : primary_before;
        // A factory-empty weapon can lack a resolved round payload. Its verified
        // empty trigger pull dispatches a click without entering the firing path.
        if (!disarm && before == 0 && stored == 0 && shot_cost->GetPropertyValueInContainer(actor) == 1)
            return report(false, "guest_weapon_preloaded_magazine_required", 0, 0);
        if (!ordinary_magazine_payload(stock_magazine(context.weapon), context, inventory, receiver))
            return report(false, "guest_weapon_payload_unqualified");
        auto* ammo_class = disarm ? nullptr : mounted_ammo(actor);
        const auto* round = stock_profile ? magazine_round(*stock_profile, ammo_class) : nullptr;
        if (underbarrel && round != saved_magazine_round(loki_magazine, context.weapon, inventory, true))
            return report(false, "guest_weapon_underbarrel_payload_unqualified");
        const auto expected_projectiles = round ? round->projectile_count : 1;
        auto* projectile = disarm ? nullptr : stock_projectile(actor, expected_projectiles);
        const auto minimum = bolt || stock_profile ? 1 : 2;
        if (!disarm && (before != stored || before < minimum || before > magazine->GetPropertyValueInContainer(actor) ||
                        shot_cost->GetPropertyValueInContainer(actor) != 1 || !projectile ||
                        (bolt && !bolt_round(actor, ammo_class, projectile)) ||
                        (stock_profile && !stock_round_matches(actor, ammo_class, projectile, round))))
            return report(false, "guest_weapon_preloaded_magazine_required");

        Unreal::FWeakObjectPtr held_actor;
        held_actor = actor;
        TapBirths projectiles{context.world, context.pawn, projectile, &shot_view, expected_projectiles == 1};
        // Pose the authoritative muzzle for the stock launch. The remote pawn's first-person
        // weapon animation does not run on this seat.
        GuestMuzzleAim aim;
        if (!disarm)
        {
            if (active_tap || !install_tap_birth_hook()) return report(false, "guest_weapon_projectile_scope_unavailable");
            if (!aim.apply(context.pawn, receiver, shot_view, actor)) return report(false, "guest_weapon_aim_unavailable");
        }
        {
            TapObservation observation(disarm ? nullptr : &projectiles);
            if (!disarm)
            {
                held->SetPropertyValueInContainer(&parameters, true);
                actor->ProcessEvent(action, &parameters);
            }
            if (held_actor.Get() != actor || !live(actor))
                return report(false, "mutation_unverified_guest_weapon_actor_lost", before);
            // Release the verified live actor before deciding whether the shot produced a valid receipt.
            held->SetPropertyValueInContainer(&parameters, false);
            actor->ProcessEvent(action, &parameters);
        }
        if (!aim.restore()) return report(false, "mutation_unverified_guest_weapon_muzzle_restore", before);

        Inventory after;
        if (!ownership_current(context) || !locate(context, after).empty() || equipped(context, after) != receiver ||
            (underbarrel && mounted_loki(receiver, context.pawn) != actor) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !ordinary_magazine_payload(stock_magazine(context.weapon), context, after, receiver))
            return report(false, "mutation_unverified_guest_weapon_release", before);

        const auto remaining = live_ammo->GetPropertyValueInContainer(actor);
        if (disarm) return report(true, "disarmed", before, remaining);
        const auto primary_after = after.ammo->GetPropertyValueInContainer(after.item);
        const auto after_stored = underbarrel ? loki_rounds(after) : primary_after;
        if (projectiles.count == 0 && remaining == before && after_stored == stored && boolean(actor, STR("IsCycling"), false))
            return report(false, "guest_weapon_stock_shot_not_started", before, remaining);
        if (!projectiles.owned || projectiles.count != expected_projectiles)
            return report(false, "mutation_unverified_guest_weapon_projectile", before, remaining, projectiles.count);
        if (remaining != before - 1 || (after_stored != stored && after_stored != remaining) ||
            (bolt && (!boolean(actor, STR("IsCycling"), true) || !boolean(actor, STR("ChamberReady"), false))))
            return report(false, "mutation_unverified_guest_weapon_shot", before, remaining, projectiles.count);

        if (underbarrel)
        {
            const bool verified = primary_after == primary_before && after_stored == remaining &&
                saved_magazine_round(loki_magazine, context.weapon, after, true) == round;
            if (verified) commit_reported_npc_hit(projectiles.hit, context.pawn);
            return report(verified, verified ? "fired" : "mutation_unverified_guest_underbarrel_ammo",
                          before, remaining, projectiles.count);
        }
        after.ammo->SetPropertyValueInContainer(after.item, remaining);
        if (after.ammo->GetPropertyValueInContainer(after.item) != remaining)
            return report(false, "mutation_unverified_guest_weapon_ammo_commit", before, remaining, projectiles.count);
        commit_reported_npc_hit(projectiles.hit, context.pawn);
        return report(true, "fired", before, remaining, projectiles.count);
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

    bool select_stock_round(Unreal::UObject* actor, Unreal::UClass* ammo,
                            const profiles::StockAmmo& selected, bool underbarrel)
    {
        auto* fn = find<Unreal::UFunction>(underbarrel ? loki_change_ammo : change_ammo_path);
        auto* handle = input<Unreal::FStructProperty>(fn, STR("InputPin"), 0);
        auto* handle_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        const Unreal::FName change_name{STR("ChangeAmmoType")};
        if (!fn || actor->GetFunctionByNameInChain(change_name) != fn || fn->GetNumParms() != 1 || fn->GetReturnProperty() ||
            fn->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
            !handle || !handle_type || handle->GetStruct().Get() != handle_type || fn->GetParmsSize() != handle->GetSize() ||
            !ammo_item_row_matches(ammo, selected)) return false;
        auto* defaults = ammo->GetClassDefaultObject().Get();
        auto* ammo_handle = field<Unreal::FStructProperty>(defaults, STR("AmmoType"));
        const auto* row = ammo_handle->ContainerPtrToValuePtr<void>(defaults);
        // Stock ChangeAmmoType writes only the held weapon and its owning pawn's selected equipment row.
        auto* params = handle->AllocateAndInitializeValue();
        if (!params) return false;
        handle->CopyCompleteValue(params, row);
        actor->ProcessEvent(fn, params);
        handle->DestroyAndFreeValue(params);
        return true;
    }

    int project_loki_round(const Lua& lua)
    {
        constexpr std::string_view refusal = "guest_underbarrel_round_projection_unavailable";
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 || !lua.is_integer(1) ||
            !lua.is_string(2) || !lua.is_string(3)) return result(lua, false, refusal);
        const auto address = lua.get_integer();
        Context context;
        if (address <= 0 || !parse_guid(lua.get_string(), context.guid)) return result(lua, false, refusal);
        const auto path = lua.get_string();
        const auto found = std::ranges::find_if(profiles::stock_ammo, [&](const auto* round) {
            return round->caliber == loki_magazine.ammo->caliber && path == RC::to_utf8_string(round->item);
        });
        if (found == profiles::stock_ammo.end()) return result(lua, false, refusal);
        const auto& round = **found;
        context.pawn = silverfish::native_interaction_input::current_guest_pawn(static_cast<std::uintptr_t>(address));
        if (!live(context.pawn)) return result(lua, false, refusal);
        context.world = static_cast<Unreal::AActor*>(context.pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        context.save = object(context.pawn, STR("Save"));
        if (!context.world || !live(context.save) || context.save->GetClassPrivate() != find<Unreal::UClass>(save_path) ||
            object(context.instance, STR("SaveSlot_Ref")) != context.save) return result(lua, false, refusal);

        auto* held = object(context.pawn, STR("EquipedItem"));
        context.weapon = live(held) ? qualified_weapon(held->GetClassPrivate()) : nullptr;
        Inventory inventory;
        if (!context.weapon || !locate(context, inventory).empty() || equipped(context, inventory) != held)
            return result(lua, false, refusal);
        auto* child = mounted_loki(held, context.pawn);
        auto* ammo = find<Unreal::UClass>(round.item);
        auto* table = stock_ammo_table();
        const Unreal::FName name{round.row, Unreal::FNAME_Find};
        const auto* stock_row = table && !name.IsNone() ? table->FindRowUnchecked(name) : nullptr;
        auto* live_round = field<Unreal::FStructProperty>(child, STR("AmmoType"));
        if (!child || !ammo || !stock_magazine_shape(child, loki_magazine) || !stock_row ||
            !live_round || live_round->GetStruct().Get() != table->GetRowStruct().Get() ||
            !ammo_item_row_matches(ammo, round))
            return result(lua, false, refusal);

        // Use the resolved stock data for the local held actor. The stock child reads
        // its ammo selector through its parent, which can lag the guest's inventory view.
        auto* actor_payload = field<Unreal::FStructProperty>(held, STR("DataStruct"));
        auto* row_payload = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        auto* instanced = find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
        auto* defaults = context.weapon->GetClassDefaultObject().Get();
        const auto stock_data = weapon_data(actor_payload, defaults);
        auto* saved_handle = field<Unreal::FStructProperty>(stock_data.type, loki_ammo_name);
        auto* default_ammo = ammo->GetClassDefaultObject().Get();
        auto* stock_handle = field<Unreal::FStructProperty>(default_ammo, STR("AmmoType"));
        if (!saved_handle || !stock_handle || !row_payload || !instanced || row_payload->GetStruct().Get() != instanced ||
            row_payload->GetSize() != sizeof(WeaponDataView) ||
            saved_handle->GetStruct().Get() != stock_handle->GetStruct().Get())
            return result(lua, false, "guest_underbarrel_round_payload_unavailable");
        auto held_data = weapon_data(actor_payload, held);
        auto saved_data = weapon_data(row_payload, inventory.item);
        // The guest's generic inventory can construct a stock row without a typed payload.
        // Reuse the held payload when available, and use the exact receiver default for a fresh mirror.
        if (!held_data.data)
        {
            const auto* source = saved_data.data ? row_payload->ContainerPtrToValuePtr<void>(inventory.item)
                                                : actor_payload->ContainerPtrToValuePtr<void>(defaults);
            actor_payload->CopyCompleteValue(actor_payload->ContainerPtrToValuePtr<void>(held), source);
            held_data = weapon_data(actor_payload, held);
        }
        if (!saved_data.data && held_data.data)
        {
            row_payload->CopyCompleteValue(row_payload->ContainerPtrToValuePtr<void>(inventory.item),
                                           actor_payload->ContainerPtrToValuePtr<void>(held));
            saved_data = weapon_data(row_payload, inventory.item);
        }
        if (held_data.type != stock_data.type || saved_data.type != stock_data.type)
            return result(lua, false, "guest_underbarrel_round_payload_unavailable");
        const auto* handle = stock_handle->ContainerPtrToValuePtr<void>(default_ammo);
        saved_handle->CopyCompleteValue(saved_handle->ContainerPtrToValuePtr<void>(held_data.data), handle);
        if (saved_data.data != held_data.data)
            saved_handle->CopyCompleteValue(saved_handle->ContainerPtrToValuePtr<void>(saved_data.data), handle);
        live_round->CopyCompleteValue(live_round->ContainerPtrToValuePtr<void>(child), stock_row);
        const bool verified = stock_round_matches(child, mounted_ammo(child), stock_projectile(child, round.projectile_count), &round);
        return result(lua, verified, verified ? "stock underbarrel round projected" : "guest_underbarrel_round_projection_unverified");
    }

    struct PropertyDeleter
    {
        Unreal::FProperty* type;
        void operator()(void* storage) const { type->DestroyAndFreeValue(storage); }
    };

    std::unique_ptr<void, PropertyDeleter> make_returned_rounds(const Context& context, const Inventory& inventory,
                                                                  Unreal::UClass* ammo, int amount)
    {
        using Value = std::unique_ptr<void, PropertyDeleter>;
        auto* make = find<Unreal::UFunction>(
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeItemStack"));
        auto* library = find<Unreal::UObject>(
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
        auto* klass = input<Unreal::FClassProperty>(make, STR("Item_Class"), 0);
        auto* count = input<Unreal::FIntProperty>(make, STR("Count"), 8);
        auto* world = input<Unreal::FObjectProperty>(make, STR("__WorldContext"), 16);
        auto* out = field<Unreal::FStructProperty>(make, STR("Item"));
        alignas(std::max_align_t) std::array<std::byte, 256> parameters{};
        if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !make ||
            make->GetOuterPrivate() != library->GetClassPrivate() || make->GetNumParms() != 4 || make->GetReturnProperty() ||
            make->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) || !klass || !count || !world || !out ||
            klass->GetMetaClass().Get() != inventory.item_class->GetMetaClass().Get() ||
            !out->HasAllPropertyFlags(Unreal::CPF_Parm | Unreal::CPF_OutParm) || out->GetOffset_Internal() != 24 ||
            out->GetStruct().Get() != inventory.type->GetStruct().Get() || 24 + out->GetSize() > make->GetParmsSize() ||
            make->GetParmsSize() > parameters.size()) return Value(nullptr, {inventory.type});
        auto* output = out->ContainerPtrToValuePtr<void>(parameters.data());
        out->InitializeValue(output);
        const auto cleanup = [out](void* value) { out->DestroyValue(value); };
        std::unique_ptr<void, decltype(cleanup)> temporary(output, cleanup);
        klass->SetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(parameters.data()), ammo);
        count->SetPropertyValueInContainer(parameters.data(), amount);
        world->SetObjectPropertyValue(world->ContainerPtrToValuePtr<void>(parameters.data()), context.pawn);
        library->ProcessEvent(make, parameters.data());
        Value row(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        if (row) inventory.type->CopyCompleteValue(row.get(), temporary.get());
        return row;
    }

    int reload(const Lua& lua, const Context& context, Inventory inventory, bool underbarrel, std::string_view requested_round)

    {
        auto* receiver = equipped(context, inventory);
        if (!receiver) return reload_proof(lua, false, "guest_weapon_actor_not_equipped");
        auto* actor = underbarrel ? mounted_loki(receiver, context.pawn) : receiver;
        if (!actor) return reload_proof(lua, false, "guest_weapon_underbarrel_unavailable");
        const auto* bolt = underbarrel ? nullptr : stock_bolt(context.weapon);
        const auto* receiver_profile = stock_magazine(context.weapon);
        const auto* stock_profile = underbarrel ? &loki_magazine : receiver_profile;
        if (!ordinary_magazine_payload(receiver_profile, context, inventory, receiver))
            return reload_proof(lua, false, "guest_weapon_payload_unqualified");
        const bool single_reload = bolt || (stock_profile && stock_profile->single_reload);
        if (!ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
            !boolean(actor, STR("Reloading"), false) || !boolean(actor, STR("IsCycling"), false) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !boolean(actor, STR("SingleReload"), single_reload) ||
            !boolean(actor, STR("UseEatherAsAmmo"), false) ||
            (stock_profile && !stock_magazine_shape(actor, *stock_profile)) ||
            (bolt && (!stock_bolt_shape(actor, *bolt) || !boolean(actor, STR("ReloadInput"), false) ||
                !mounted_payload_matches(actor, inventory))))
            return reload_proof(lua, false, "guest_weapon_reload_ineligible");

        auto* rounds = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        auto* capacity = field<Unreal::FIntProperty>(actor, STR("MagSize"));
        auto* update = find<Unreal::UFunction>(underbarrel ? loki_base_update : update_mag_path);
        const Unreal::FName update_name{STR("Update_Mag")};
        if (!rounds || !capacity || !update || actor->GetFunctionByNameInChain(update_name) != update ||
            update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty())
            return reload_proof(lua, false, "stock_guest_weapon_reload_schema_unavailable");

        auto* ammo_class = mounted_ammo(actor);
        const auto* round = stock_profile ? magazine_round(*stock_profile, ammo_class) : nullptr;
        if (underbarrel && round != saved_magazine_round(loki_magazine, context.weapon, inventory, true))
            return reload_proof(lua, false, "guest_weapon_underbarrel_payload_unqualified");
        AmmoStackSchema stack_schema;
        if (!stack_schema.read(ammo_class, inventory) ||
            (bolt && !bolt_round(actor, ammo_class, stock_projectile(actor))) ||
            (stock_profile && (!round || !stock_round_matches(actor, ammo_class,
                stock_projectile(actor, round->projectile_count), round))))
            return reload_proof(lua, false, "stock_guest_weapon_reserve_schema_unavailable");

        const auto before = rounds->GetPropertyValueInContainer(actor);
        const auto cap = capacity->GetPropertyValueInContainer(actor);
        const auto primary_before = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        const auto stored_rounds = [&](const Inventory& view) {
            return underbarrel ? loki_rounds(view) : view.ammo->GetPropertyValueInContainer(view.item);
        };
        if (cap <= 0 || before < 0 || before > cap || before != stored_rounds(inventory))
            return reload_proof(lua, false, "guest_weapon_magazine_unqualified", before, before);

        auto* old_ammo = ammo_class;
        const profiles::StockAmmo* changed_round = nullptr;
        if (!requested_round.empty() && requested_round != RC::to_utf8_string(ammo_class->GetPathName()))
        {
            const auto match = std::ranges::find_if(profiles::stock_ammo, [&](const auto* candidate) {
                return requested_round == RC::to_utf8_string(candidate->item);
            });
            if (match == profiles::stock_ammo.end())
                return reload_proof(lua, false, "guest_weapon_ammo_selection_unqualified");
            auto* selected = find<Unreal::UClass>((*match)->item);
            if (!selected || (stock_profile ? !magazine_round(*stock_profile, selected)
                : !bolt || (selected != find<Unreal::UClass>(rifle_fmj_path) &&
                            selected != find<Unreal::UClass>(profiles::thirty_06_incendiary.item))) ||
                !ammo_item_row_matches(selected, **match) || !stack_schema.read(selected, inventory))
                return reload_proof(lua, false, "guest_weapon_ammo_selection_unqualified");
            ammo_class = selected;
            changed_round = *match;
        }

        std::int32_t reserve_before{};
        std::vector<ReserveStack> stacks;
        if (!snapshot_reserve(context, inventory, ammo_class, stacks, reserve_before))
            return reload_proof(lua, false, "guest_weapon_reserve_unqualified", before, before);
        if (before == cap && !changed_round)
            return reload_proof(lua, false, "magazine_full", before, before, reserve_before, reserve_before);
        if (requested_round.empty() && before == 0 && reserve_before == 0)
        {
            // Stock FindAmmo_InInventory checks the Backpack in slot order
            // when an empty weapon has run out of its currently selected round.
            Unreal::FScriptArrayHelper backpack(inventory.backpack,
                inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
            auto* ammo_base = find<Unreal::UClass>(ammo_master_path);
            for (int index = 0; ammo_base && index < backpack.Num(); ++index)
            {
                auto* alternative = Unreal::Cast<Unreal::UClass>(inventory.row_class(backpack.GetRawPtr(index)));
                if (!alternative || alternative == ammo_class || !alternative->IsChildOf(ammo_base)) continue;
                const auto* candidate = stock_profile ? magazine_round(*stock_profile, alternative) : nullptr;
                if (bolt && ammo_class == find<Unreal::UClass>(rifle_fmj_path) &&
                    alternative == find<Unreal::UClass>(profiles::thirty_06_incendiary.item))
                    candidate = &profiles::thirty_06_incendiary;
                if (!candidate || !find<Unreal::UClass>(candidate->projectile)) continue;
                if (!snapshot_reserve(context, inventory, alternative, stacks, reserve_before))
                    return reload_proof(lua, false, "guest_weapon_reserve_unqualified", before, before);
                if (reserve_before == 0) continue;
                if (!ammo_item_row_matches(alternative, *candidate) || !stack_schema.read(alternative, inventory) ||
                    (bolt && (stack_schema.unit_value != 12 || stack_schema.unit_weight != 0.014)))
                    return reload_proof(lua, false, "stock_guest_weapon_reserve_schema_unavailable", before, before);
                ammo_class = alternative;
                changed_round = candidate;
                break;
            }
        }
        if (reserve_before == 0)
            return reload_proof(lua, false, "no_matching_guest_ammo", before, before, 0, 0);

        // Stage the previous load before changing its type. The old rounds return
        // to the same detached guest backpack even when the magazine was full.
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value returned(nullptr, {inventory.type});
        if (changed_round && before > 0)
        {
            AmmoStackSchema old_schema;
            if (!old_schema.read(old_ammo, inventory) || !old_schema.qualifies(before) ||
                Unreal::FScriptArrayHelper(inventory.backpack,
                    inventory.backpack->ContainerPtrToValuePtr<void>(context.save)).Num() >= max_inventory)
                return reload_proof(lua, false, "guest_weapon_previous_load_unqualified");
            returned = make_returned_rounds(context, inventory, old_ammo, before);
            auto check = context;
            check.guid = returned ? inventory.identity(returned.get()) : Guid{};
            Inventory collision;
            if (!returned || check.guid == Guid{} || inventory.row_class(returned.get()) != old_ammo ||
                !old_schema.totals_match(returned.get(), before) || !locate(check, collision).empty() || collision.item)
                return reload_proof(lua, false, "guest_weapon_previous_load_unqualified");
        }

        const auto loaded_before = changed_round ? 0 : before;
        // These stock magazine reloads exit after debiting the first matching Backpack stack.
        const auto available = stock_profile && stock_profile->reload_first_stack ? stacks.front().units : reserve_before;
        const auto transferred = single_reload ? 1 : std::min(cap - loaded_before, available);
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

        if (changed_round)
        {
            const Unreal::FWeakObjectPtr held_actor{actor};
            if (!mounted_payload_matches(receiver, inventory) || !select_stock_round(actor, ammo_class, *changed_round, underbarrel))
                return reload_proof(lua, false, "stock_guest_weapon_ammo_switch_unqualified", before, before, reserve_before);
            Inventory selected;
            std::vector<ReserveStack> selected_stacks;
            std::int32_t selected_total{};
            if (held_actor.Get() != actor || !ownership_current(context) || !locate(context, selected).empty() ||
                equipped(context, selected) != receiver || (underbarrel && mounted_loki(receiver, context.pawn) != actor) ||
                rounds->GetPropertyValueInContainer(actor) != before || stored_rounds(selected) != before ||
                mounted_ammo(actor) != ammo_class ||
                (bolt ? !bolt_round(actor, ammo_class, stock_projectile(actor))
                      : !stock_round_matches(actor, ammo_class, stock_projectile(actor, changed_round->projectile_count),
                                             changed_round) ||
                        !ordinary_magazine_payload(receiver_profile, context, selected, receiver)) ||
                !mounted_payload_matches(receiver, selected) ||
                !snapshot_reserve(context, selected, ammo_class, selected_stacks, selected_total) || selected_total != reserve_before ||
                !std::ranges::equal(stacks, selected_stacks, [](const ReserveStack& first, const ReserveStack& second) {
                    return first.guid == second.guid && first.index == second.index && first.units == second.units;
                }))
                return reload_proof(lua, false, "mutation_unverified_guest_weapon_ammo_switch", before, -1, reserve_before);
            inventory = selected;
        }

        // Publish the verified detached reserve and magazine in one game-thread turn.
        const auto refilled = loaded_before + transferred;
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
        if (returned) inventory.type->CopyCompleteValue(backpack.GetRawPtr(backpack.AddValue()), returned.get());
        if (!underbarrel) inventory.ammo->SetPropertyValueInContainer(inventory.item, refilled);
        rounds->SetPropertyValueInContainer(actor, refilled);
        const Unreal::FWeakObjectPtr held_actor{actor};
        actor->ProcessEvent(update, nullptr);

        Inventory after;
        if (held_actor.Get() != actor || !ownership_current(context) || !locate(context, after).empty() ||
            equipped(context, after) != receiver || (underbarrel && mounted_loki(receiver, context.pawn) != actor))
            return reload_proof(lua, false, "mutation_unverified_guest_weapon_reload_owner", before, -1, reserve_before);

        const auto observed = rounds->GetPropertyValueInContainer(actor);
        std::int32_t reserve_after{};
        std::vector<ReserveStack> remaining_stacks;
        if (observed != refilled || stored_rounds(after) != observed ||
            !ordinary_magazine_payload(receiver_profile, context, after, receiver) ||
            (underbarrel && (after.ammo->GetPropertyValueInContainer(after.item) != primary_before ||
                           saved_magazine_round(loki_magazine, context.weapon, after, true) !=
                               (changed_round ? changed_round : round))) ||
            ((bolt || changed_round) && (mounted_ammo(actor) != ammo_class || !mounted_payload_matches(receiver, after))) ||
            !snapshot_reserve(context, after, ammo_class, remaining_stacks, reserve_after) ||
            reserve_after != reserve_before - transferred)
            return reload_proof(lua, false, "mutation_unverified_guest_weapon_reload_commit", before, observed, reserve_before);

        auto surviving = stacks | std::views::filter([](const ReserveStack& stack) { return stack.units != stack.debit; });
        const bool proven = std::ranges::equal(surviving, remaining_stacks,
            [&](const ReserveStack& expected, const ReserveStack& actual) {
                return expected.guid == actual.guid && expected.units - expected.debit == actual.units &&
                    (expected.debit == 0 || stack_schema.totals_match(backpack.GetRawPtr(actual.index), actual.units));
            });
        const bool returned_verified = !returned ||
            (backpack.Num() > 0 && inventory.type->Identical(returned.get(), backpack.GetRawPtr(backpack.Num() - 1)));
        const bool settled = proven && returned_verified;
        return reload_proof(lua, settled, settled ? "reloaded" : "mutation_unverified_guest_weapon_reload_stacks",
                            loaded_before, observed, reserve_before, reserve_after);
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
    constexpr auto fire_weapon_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:FireWeapon");
    constexpr auto end_cycle_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:EndCycle");

    struct HoldParameters { bool held{}; double variance{}; };
    struct SmgHold
    {
        Unreal::FWeakObjectPtr pawn, save, stock, instance, controller, connection, world, weapon;
        Unreal::FWeakObjectPtr receiver, primary, fire, end_cycle, projectile;
        Guid guid{};
        HoldClock::time_point deadline{};
        profiles::Profile profile{};
        const profiles::StockAmmo* round{};
        GuestMuzzleAim aim;
        ShotView view;
        std::int64_t view_sequence{};
        std::string_view status{"inactive"};
        std::int64_t serial{};
        int before{-1}, after{-1}, shot_before{-1}, rounds{}, projectiles{}, shot_births{};
        bool active{}, closing{}, fenced{}, dirty{}, owned_birth{};
        ReportedNpcHit hit;

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
            ordinary_magazine_payload(&smg_hold.profile, context, inventory, receiver, smg_hold.round);
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
            else if (!smg_hold.aim.apply(context.pawn, receiver, smg_hold.view))
                hold_stop(smg_hold, "ineligible");
            else
            {
                shot_hold = &smg_hold;
                smg_hold.shot_before = smg_hold.after;
                smg_hold.shot_births = 0;
        smg_hold.hit = {};
                smg_hold.owned_birth = true;
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
        count_guest_projectile(actor, smg_hold.pawn.Get(), smg_hold.shot_births, smg_hold.owned_birth,
                               &smg_hold.view, smg_hold.round->projectile_count == 1, smg_hold.hit);
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
        if (!smg_hold.aim.restore() || smg_hold.shot_births != smg_hold.round->projectile_count ||
            !smg_hold.owned_birth || !ammo ||
            !hold_locate(smg_hold, receiver, context, inventory) || ammo->GetPropertyValueInContainer(receiver) != remaining ||
            inventory.ammo->GetPropertyValueInContainer(inventory.item) != remaining)
        {
            hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_shot_readback", true);
            return;
        }
        commit_reported_npc_hit(smg_hold.hit, context.pawn);
        smg_hold.after = remaining;
        ++smg_hold.rounds;
        smg_hold.projectiles += smg_hold.shot_births;
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
            shot_hold->aim.restore();
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
        ShotView view;
        if (const auto error = resolve(lua, context, nullptr, &duration, nullptr, false, nullptr, &view); !error.empty())
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
        if (!inventory.item) return hold_result(lua, false, "native_guest_smg_hold_exact_weapon_required");
        context.weapon = qualified_weapon(inventory.row_class(inventory.item));
        const auto* profile = stock_magazine(context.weapon);
        if (!profile || !profiles::stock_held_fire(profile->kind))
            return hold_result(lua, false, "native_guest_smg_hold_exact_weapon_required");
        auto* actor = equipped(context, inventory);
        auto* action = find<Unreal::UFunction>(primary_path);
        auto* fire = find<Unreal::UFunction>(fire_weapon_path);
        auto* end = find<Unreal::UFunction>(end_cycle_path);
        const auto* round = saved_magazine_round(*profile, context.weapon, inventory);
        auto* projectile = round ? stock_projectile(actor, round->projectile_count) : nullptr;
        auto* ammo = field<Unreal::FIntProperty>(actor, STR("Ammo"));
        const auto loaded = ammo ? ammo->GetPropertyValueInContainer(actor) : -1;
        const auto exact_signal = [&](Unreal::UFunction* function) {
            return actor && function && function->GetOuterPrivate() == find<Unreal::UClass>(weapon_base_path) &&
                actor->GetFunctionByNameInChain(function->GetFName()) == function &&
                !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) &&
                function->GetNumParms() == 0 && function->GetParmsSize() == 0 && !function->GetReturnProperty();
        };
        if (!actor || !hold_primary_shape(actor, action) || !exact_signal(fire) || !exact_signal(end) ||
            !ready_pawn(context) || !boolean(context.pawn, STR("Wep_HeldAside"), false) ||
            !boolean(actor, STR("Holding_Primary"), false) || !boolean(actor, STR("IsSeerOpen"), false) ||
            !boolean(actor, STR("IsCycling"), false) || !boolean(actor, STR("Reloading"), false) ||
            !(boolean(actor, STR("HasSelectFire"), false) ||
              (boolean(actor, STR("HasSelectFire"), true) && boolean(actor, STR("SemiAutoSelected"), false))) ||
            !stock_magazine_shape(actor, *profile) ||
            !stock_round_matches(actor, mounted_ammo(actor), projectile, round) ||
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
        smg_hold.round = round;
        smg_hold.view = view;
        smg_hold.serial = ++smg_hold_serial;
        smg_hold.before = smg_hold.after = loaded;
        smg_hold.status = "active";
        smg_hold.active = smg_hold.fenced = true;
        smg_hold.deadline = HoldClock::now() + std::chrono::milliseconds{duration};
        hold_primary(actor, action, true);
        if (smg_hold.rounds == 0 && !smg_hold.dirty && smg_hold.status == "ineligible")
        {
            hold_release(smg_hold);
            return hold_result(lua, false, "native_guest_smg_hold_ineligible");
        }
        if (smg_hold.rounds != 1)
        {
            if (shot_hold == &smg_hold)
            {
                smg_hold.aim.restore();
                shot_hold = nullptr;
            }
            smg_hold.shot_before = -1;
            hold_stop(smg_hold, "mutation_unverified_guest_smg_hold_begin", true);
            hold_release(smg_hold);
        }
        return hold_result(lua, !smg_hold.dirty, smg_hold.closing && !smg_hold.dirty ? "closing" : smg_hold.status, &smg_hold);
    }

    int update_hold_view(const Lua& lua)
    {
        const auto fields = lua.get_stack_size() - 2;
        if (!Unreal::IsInGameThreadRaw() || !hold_enabled() || !shot_view_fields(lua, 3, fields)
            || !lua.is_integer(1) || !lua.is_integer(2)) return 0;
        const auto serial = lua.get_integer();
        const auto sequence = lua.get_integer();
        const auto seat = std::ranges::find(smg_holds, serial, &SmgHold::serial);
        if (serial < 1 || seat == smg_holds.end() || !seat->active || sequence <= seat->view_sequence) return 0;
        ShotView view;
        if (!read_shot_view(lua, fields, view)) return 0;
        seat->view = view;
        seat->view_sequence = sequence;
        lua.set_bool(true);
        return 1;
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
        const auto path = type->GetPathName();
        if (path == STR("/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C") ||
            path == STR("/Game/Classes/Equipment/Eqp_LockPick_Hev.Eqp_LockPick_Hev_C"))
        {
            auto* base = find<Unreal::UClass>(equipment_base_path);
            auto* category = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
            return base && type->IsChildOf(base) && category &&
                category->GetPropertyValueInContainer(defaults) == 5 && boolean(defaults, STR("Stacks"), true);
        }
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
        std::int32_t fuse_ms{-1};
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
        std::int32_t fuse_ms = -1;
        if (throwable->carries_fuse)
        {
            auto* fuse = field<Unreal::FFloatProperty>(actor, STR("FuseTime_Player"));
            auto* cook = field<Unreal::FDoubleProperty>(actor, STR("CookTime"));
            if (fuse && cook)
            {
                const double stock_fuse = fuse->GetPropertyValueInContainer(actor);
                const double elapsed = cook->GetPropertyValueInContainer(actor);
                if (std::isfinite(stock_fuse) && stock_fuse >= 0 && stock_fuse <= max_pipe_fuse_ms / 1000.0 &&
                    std::isfinite(elapsed) && elapsed >= 0)
                    fuse_ms = static_cast<std::int32_t>(std::lround(std::max(0.0, stock_fuse - elapsed) * 1000));
            }
        }
        // The native throw completion restores the hand pose and its audio. An
        // empty ThrownNade makes that branch return without consuming stock.
        ThrowableCall cleanup{grenade_throw_path};
        if (cleanup.valid() && !live(object(actor, STR("ThrownNade")))) cleanup.invoke(actor, toss);
        if (flare_sequence >= (1ULL << 53) - 1 || (throwable->carries_fuse && fuse_ms < 0)) return;
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
        local_flare = {pawn, context.save, guid, throwable, ++flare_sequence, toss, fuse_ms,
            HoldClock::now() + std::chrono::seconds(2)};
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
        if (intent.fuse_ms >= 0)
        {
            lua.set_integer(intent.fuse_ms);
            return 5;
        }
        return 4;
    }

    int throw_flare(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        const auto arguments = lua.get_stack_size();
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || !Unreal::IsInGameThreadRaw() ||
            (arguments != 4 && arguments != 5) || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
            !lua.is_bool(4) || (arguments == 5 && !lua.is_integer(5)))
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
        const auto fuse_ms = arguments == 5 ? lua.get_integer() : -1;
        auto* fuse = throwable->carries_fuse ? field<Unreal::FFloatProperty>(actor, STR("FuseTime_Player")) : nullptr;
        auto* cook = throwable->carries_fuse ? field<Unreal::FDoubleProperty>(actor, STR("CookTime")) : nullptr;
        if ((arguments == 5) != throwable->carries_fuse || (throwable->carries_fuse &&
            (!fuse || !cook || fuse_ms < 0 || fuse_ms > max_pipe_fuse_ms)))
            return refuse("native_guest_flare_refused_fuse");
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

        if (fuse)
        {
            const auto stock_fuse = fuse->GetPropertyValueInContainer(actor);
            const auto stock_cook = cook->GetPropertyValueInContainer(actor);
            const Unreal::FWeakObjectPtr equipped_before{actor};
            // SpawnNade uses FuseTime_Player - CookTime. The guest already ran
            // the stock countdown on its own equipped item.
            fuse->SetPropertyValueInContainer(actor, static_cast<float>(fuse_ms) / 1000.0f);
            cook->SetPropertyValueInContainer(actor, 0);
            call.invoke(actor, toss);
            if (auto* equipped_after = equipped_before.Get(); live(equipped_after))
            {
                fuse->SetPropertyValueInContainer(equipped_after, stock_fuse);
                cook->SetPropertyValueInContainer(equipped_after, stock_cook);
            }
        }
        else call.invoke(actor, toss);
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
        if (fuse)
        {
            auto* applied_fuse = field<Unreal::FFloatProperty>(spawned, STR("FuseTime"));
            if (!applied_fuse || std::abs(applied_fuse->GetPropertyValueInContainer(spawned) - fuse_ms / 1000.0) > .001)
                return refuse("mutation_unverified_native_guest_flare_fuse");
        }
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
        if (fuse)
        {
            lua.set_integer(fuse_ms);
            return 7;
        }
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
    std::uint64_t selection_sequence{};

    int read_local_selection(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = lua.get_integer();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(address) : nullptr;
        if (!pawn) return 0;
        auto* actor = object(pawn, STR("EquipedItem"));
        if (!authority(actor)) return 0;
        Context context;
        context.pawn = pawn;
        context.save = object(pawn, STR("Save"));
        context.world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        if (!live(context.save) || object(context.instance, STR("SaveSlot_Ref")) != context.save ||
            static_cast<Unreal::AActor*>(actor)->GetWorld() != context.world ||
            object(actor, STR("Owner")) != pawn || object(actor, STR("Instigator")) != pawn ||
            object(actor, STR("User")) != pawn || !boolean(actor, STR("HeldByPlayer"), true)) return 0;

        // Travel and reconnect can restore the held actor before the co-op pawn
        // is scoped. Read the current stock slot when its pawn or actor changes.
        if (local_selection.pawn.Get() != pawn || local_selection.save.Get() != context.save ||
            local_selection.actor.Get() != actor)
        {
            if (selection_sequence >= (1ULL << 53) - 1) return 0;
            Inventory inventory;
            if (!bind_inventory(context, inventory).empty()) return 0;
            const auto index = inventory.selected->GetPropertyValueInContainer(context.save);
            Unreal::FScriptArrayHelper slots(
                inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
            if (index < 0 || index >= quick_slots || slots.Num() < quick_slots || slots.Num() > max_inventory) return 0;
            auto* row = slots.GetRawPtr(index);
            const auto guid = inventory.identity(row);
            if (guid == Guid{} || inventory.row_class(row) != actor->GetClassPrivate()) return 0;
            local_selection = {pawn, context.save, actor, guid, ++selection_sequence};
        }
        const auto& guid = local_selection.guid;
        lua.set_integer(local_selection.sequence);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        return 2;
    }

    bool local_inventory_current(const Context& context)
    {
        return context.pawn && context.save && context.world &&
            silverfish::native_interaction_input::current_guest_pawn(
                reinterpret_cast<std::uintptr_t>(context.pawn)) == context.pawn &&
            object(context.pawn, STR("Save")) == context.save &&
            static_cast<Unreal::AActor*>(context.pawn)->GetWorld() == context.world &&
            object(context.world, STR("OwningGameInstance")) == context.instance &&
            object(context.instance, STR("SaveSlot_Ref")) == context.save;
    }

    bool bind_local_inventory(Context& context, std::int64_t pawn, std::int64_t save, bool require_ready = true)
    {
        if (!Unreal::IsInGameThreadRaw() || pawn <= 0 || save <= 0) return false;
        context.pawn = silverfish::native_interaction_input::current_guest_pawn(static_cast<std::uintptr_t>(pawn));
        if (!live(context.pawn) || context.pawn->GetClassPrivate() != find<Unreal::UClass>(pawn_path)) return false;
        context.save = object(context.pawn, STR("Save"));
        if (!live(context.save) || context.save->GetClassPrivate() != find<Unreal::UClass>(save_path) ||
            reinterpret_cast<std::uintptr_t>(context.save) != static_cast<std::uintptr_t>(save)) return false;
        context.world = static_cast<Unreal::AActor*>(context.pawn)->GetWorld();
        context.instance = object(context.world, STR("OwningGameInstance"));
        return local_inventory_current(context) && (!require_ready || ready_pawn(context));
    }

    template <bool local>
    bool inventory_current(const Context& context)
    {
        if constexpr (local) return local_inventory_current(context);
        return ownership_current(context);
    }

    constexpr std::size_t max_inventory_item_text = 8192;

    std::string inventory_item_text(const Context& context, const Inventory& inventory, const void* row)
    {
        Unreal::FString text;
        inventory.type->ExportTextItem_Direct(text, row, nullptr, context.save, Unreal::PPF_Delimited);
        if (text.Len() <= 0 || static_cast<std::size_t>(text.Len()) > max_inventory_item_text) return {};
        auto encoded = RC::to_utf8_string(*text);
        if (encoded.size() > max_inventory_item_text || encoded.find_first_of("\t\r\n") != std::string::npos) return {};
        return encoded;
    }

    template <bool local>
    int export_inventory_item(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
            return result(lua, false, "native_inventory_export_refused_arguments");
        Context context;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (pawn <= 0 || save <= 0 || !parse_guid(lua.get_string(), context.guid))
            return result(lua, false, "native_inventory_export_refused_arguments");
        if constexpr (local)
        {
            if (!bind_local_inventory(context, pawn, save, false))
                return result(lua, false, "native_inventory_export_refused_owner");
        }
        else if (!resolve_owner(context, pawn, save).empty())
            return result(lua, false, "native_inventory_export_refused_owner");

        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item || !live(inventory.row_class(inventory.item)))
            return result(lua, false, "native_inventory_export_refused_row");
        const auto text = inventory_item_text(context, inventory, inventory.item);
        if (text.empty()) return result(lua, false, "native_inventory_export_refused_serialization");
        return result(lua, true, text);
    }

    struct InventoryGrant
    {
        std::uint64_t sequence{};
        Guid guid{};
        std::string item_class;
        std::string text;
    };

    struct LocalGrantScope
    {
        Unreal::FWeakObjectPtr pawn, save;
        std::array<Unreal::UFunction*, 2> functions{};
        std::map<Guid, std::pair<Unreal::UObject*, int>> before;
        std::deque<InventoryGrant> pending;
        std::set<Guid> granted;
        unsigned depth{};
    };

    LocalGrantScope grant_scope;
    struct StackGrantReceipt
    {
        Unreal::FWeakObjectPtr save;
        Guid guid;
        std::string item_class;
        int units;
    };
    std::map<Guid, StackGrantReceipt> stack_grant_receipts;
    std::uint64_t grant_sequence{};
    std::array grant_hooks{
        Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    constexpr std::size_t max_pending_grants = 64;

    bool grant_function(Unreal::UFunction* function)
    {
        return function && (function == grant_scope.functions[0] || function == grant_scope.functions[1]);
    }

    bool bind_grant_inventory(Context& context, Inventory& inventory)
    {
        return bind_local_inventory(context, reinterpret_cast<std::intptr_t>(grant_scope.pawn.Get()),
            reinterpret_cast<std::intptr_t>(grant_scope.save.Get()), false) && bind_inventory(context, inventory).empty();
    }

    void begin_grant(Unreal::UFunction* function)
    {
        if (!Unreal::IsInGameThreadRaw() || !grant_function(function)) return;
        if (grant_scope.depth++ != 0) return;
        grant_scope.before.clear();
        Context context;
        Inventory inventory;
        if (!bind_grant_inventory(context, inventory)) return;
        const auto remember = [&](const void* row) {
            if (auto* type = inventory.row_class(row))
                grant_scope.before.try_emplace(inventory.identity(row), type, inventory.ammo->GetPropertyValueInContainer(row));
        };
        for (auto* property : {inventory.backpack, inventory.equipment})
        {
            Unreal::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(context.save));
            if (rows.Num() > max_inventory)
            {
                grant_scope.before.clear();
                return;
            }
            for (int index = 0; index < rows.Num(); ++index) remember(rows.GetRawPtr(index));
        }
        for (auto* property : {inventory.helmet, inventory.armor})
            remember(property->ContainerPtrToValuePtr<void>(context.save));
        // Count zero is a valid initial backpack. An empty GUID marks a qualified capture.
        grant_scope.before.try_emplace(Guid{});
    }

    void finish_grant(Unreal::UFunction* function)
    {
        if (!Unreal::IsInGameThreadRaw() || !grant_function(function) ||
            grant_scope.depth == 0 || --grant_scope.depth != 0) return;
        auto before = std::exchange(grant_scope.before, {});
        Context context;
        Inventory inventory;
        if (!before.contains(Guid{}) || !bind_grant_inventory(context, inventory)) return;
        Unreal::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(context.save));
        if (rows.Num() > max_inventory) return;
        for (int index = 0; index < rows.Num() && grant_scope.pending.size() < max_pending_grants; ++index)
        {
            const auto* row = rows.GetRawPtr(index);
            const auto guid = inventory.identity(row);
            auto* type = inventory.row_class(row);
            if (!live(type) || guid == Guid{}) continue;
            std::string encoded;
            if (const auto old = before.find(guid); old != before.end())
            {
                const auto [old_type, old_count] = old->second;
                const int count = inventory.ammo->GetPropertyValueInContainer(row);
                auto* item = Unreal::Cast<Unreal::UClass>(type);
                if (old_type != type || old_count < 1 || count <= old_count ||
                    !item || !boolean(item->GetClassDefaultObject().Get(), STR("Stacks"), true)) continue;
                encoded = std::format("stack:{}", count - old_count);
            }
            else
            {
                encoded = inventory_item_text(context, inventory, row);
            }
            if (!encoded.empty())
                grant_scope.pending.push_back({++grant_sequence, guid, RC::to_utf8_string(type->GetPathName()), std::move(encoded)});
        }
    }

    void remove_grant_hooks()
    {
        grant_scope = {};
        for (auto& callback : grant_hooks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    bool install_grant_hooks()
    {
        if (grant_hooks.front() != Hook::ERROR_ID) return true;
        constexpr auto owner = STR("SilverfishCoopNative");
        constexpr auto pre = [](HoldHook&, Unreal::UObject*, Unreal::FFrame& frame, void*) { begin_grant(frame.Node()); };
        constexpr auto post = [](HoldHook&, Unreal::UObject*, Unreal::FFrame& frame, void*) { finish_grant(frame.Node()); };
        grant_hooks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(pre, {false, false, owner, STR("Capture stock gear grant input")});
        grant_hooks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(post, {false, false, owner, STR("Read stock gear grants")});
        grant_hooks[2] = Hook::RegisterProcessInternalPreCallback(pre, {false, false, owner, STR("Capture reflected gear grant input")});
        grant_hooks[3] = Hook::RegisterProcessInternalPostCallback(post, {false, false, owner, STR("Read reflected gear grants")});
        grant_hooks[4] = Hook::RegisterProcessEventPreCallback(
            [](HoldHook&, Unreal::UObject*, Unreal::UFunction* fn, void*) { begin_grant(fn); },
            {false, false, owner, STR("Capture direct stock gear grants")});
        grant_hooks[5] = Hook::RegisterProcessEventPostCallback(
            [](HoldHook&, Unreal::UObject*, Unreal::UFunction* fn, void*) { finish_grant(fn); },
            {false, false, owner, STR("Read direct stock gear grants")});
        if (std::ranges::find(grant_hooks, Hook::ERROR_ID) == grant_hooks.end()) return true;
        remove_grant_hooks();
        return false;
    }

    int arm_inventory_grants(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2)) return 0;
        Context context;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (!bind_local_inventory(context, pawn, save, false) || !install_grant_hooks()) return 0;
        if (grant_scope.save.Get() != context.save) grant_scope = {};
        grant_scope.save = context.save;
        grant_scope.pawn = context.pawn;
        if (!grant_scope.functions[0])
            grant_scope.functions[0] = find<Unreal::UFunction>(STR("/Game/Classes/GUI/Menu_Cheats_Widget.Menu_Cheats_Widget_C:GiveGear"));
        if (!grant_scope.functions[1])
            grant_scope.functions[1] = find<Unreal::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:SpawnItemForPlayer"));
        if (grant_scope.pending.empty()) return 0;
        lua.set_integer(grant_scope.pending.front().sequence);
        return 1;
    }

    int read_inventory_grant(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            grant_scope.pending.empty() || lua.get_integer() != grant_scope.pending.front().sequence) return 0;
        const auto& grant = grant_scope.pending.front();
        const auto& guid = grant.guid;
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        lua.set_string(grant.item_class);
        lua.set_string(grant.text);
        return 3;
    }

    int acknowledge_inventory_grant(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2) ||
            grant_scope.pending.empty() || lua.get_integer() != grant_scope.pending.front().sequence)
            return result(lua, false, "native_inventory_grant_ack_refused");
        if (lua.get_bool()) grant_scope.granted.insert(grant_scope.pending.front().guid);
        grant_scope.pending.pop_front();
        return result(lua, true, "native inventory grant acknowledged");
    }

    int preserve_inventory_grant_payloads(const Lua& lua)
    {
        constexpr std::string_view refused = "native_inventory_grant_projection_unavailable";
        if (grant_scope.granted.empty()) return result(lua, true, "no local grant payloads");
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3)) return result(lua, false, refused);
        Context source;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        const auto path = lua.get_string();
        if (!bind_local_inventory(source, pawn, save, false) || grant_scope.save.Get() != source.save ||
            !path.starts_with("/Engine/Transient.") || path.size() > 256) return result(lua, false, refused);
        Context view = source;
        view.save = find<Unreal::UObject>(RC::to_wstring(path).c_str());
        if (!live(view.save) || view.save == source.save || view.save->GetClassPrivate() != source.save->GetClassPrivate())
            return result(lua, false, refused);
        Inventory actual, staged;
        if (!bind_inventory(source, actual).empty() || !bind_inventory(view, staged).empty() || actual.type != staged.type)
            return result(lua, false, refused);
        auto* payload = field<Unreal::FStructProperty>(actual.type->GetStruct().Get(), payload_name);
        if (!payload || payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")))
            return result(lua, false, refused);
        const auto index = [](const Context& context, const Inventory& inventory) {
            std::map<Guid, void*> rows;
            for (auto* property : {inventory.backpack, inventory.equipment})
            {
                Unreal::FScriptArrayHelper values(property, property->ContainerPtrToValuePtr<void>(context.save));
                for (int position = 0; position < values.Num(); ++position)
                {
                    auto* row = values.GetRawPtr(position);
                    const auto guid = inventory.identity(row);
                    if (grant_scope.granted.contains(guid)) rows.emplace(guid, row);
                }
            }
            for (auto* property : {inventory.helmet, inventory.armor})
            {
                auto* row = property->ContainerPtrToValuePtr<void>(context.save);
                const auto guid = inventory.identity(row);
                if (grant_scope.granted.contains(guid)) rows.emplace(guid, row);
            }
            return rows;
        };
        const auto previous = index(source, actual);
        const auto upcoming = index(view, staged);
        for (const auto& [guid, row] : upcoming)
        {
            const auto found = previous.find(guid);
            if (found == previous.end() || actual.row_class(found->second) != staged.row_class(row)) continue;
            payload->CopyCompleteValue(payload->ContainerPtrToValuePtr<void>(row),
                                        payload->ContainerPtrToValuePtr<void>(found->second));
        }
        std::erase_if(grant_scope.granted, [&](const Guid& guid) { return !upcoming.contains(guid); });
        return result(lua, true, "native grant payloads preserved");
    }

    int clear_inventory_grants(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            remove_grant_hooks();
            stack_grant_receipts.clear();
        }
        return 0;
    }

    int fixture_copy_stock_weapon_payload(const Lua& lua)
    {
        constexpr std::string_view refused = "fixture_typed_weapon_payload_unavailable";
        if (!env("SILVERFISH_COOP_HEADLESS") || !env("SILVERFISH_COOP_TITLE_FIXTURES") ||
            lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
            return result(lua, false, refused);
        Context context;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (!parse_guid(lua.get_string(), context.guid) || !bind_local_inventory(context, pawn, save, false))
            return result(lua, false, refused);
        context.weapon = find<Unreal::UClass>(STR("/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C"));
        Inventory inventory;
        if (!context.weapon || !locate(context, inventory).empty() || inventory.backpack_index < 0)
            return result(lua, false, refused);
        auto* defaults = context.weapon->GetClassDefaultObject().Get();
        auto* source = field<Unreal::FStructProperty>(defaults, STR("DataStruct"));
        auto* target = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        if (!weapon_data(source, defaults).data || !target || target->GetStruct().Get() != source->GetStruct().Get())
            return result(lua, false, refused);
        target->CopyCompleteValue(target->ContainerPtrToValuePtr<void>(inventory.item), source->ContainerPtrToValuePtr<void>(defaults));
        return result(lua, !!weapon_data(target, inventory.item).data, "stock weapon payload copied");
    }

    int import_inventory_addition(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_string(3) || !lua.is_string(4) || !lua.is_string(5))
            return result(lua, false, "native_inventory_grant_refused_arguments");
        Context context;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (pawn <= 0 || save <= 0 || !parse_guid(lua.get_string(), context.guid))
            return result(lua, false, "native_inventory_grant_refused_arguments");
        const auto class_path = lua.get_string();
        const auto reported = lua.get_string();
        if (!silverfish::content_paths::item_class(std::string_view{class_path}, 512) ||
            reported.empty() || reported.size() > max_inventory_item_text || reported.front() != '(' ||
            reported.back() != ')' || reported.find_first_of("\t\r\n") != std::string::npos ||
            reported.find('\0') != std::string::npos)
            return result(lua, false, "native_inventory_grant_refused_encoding");
        if (!resolve_owner(context, pawn, save).empty())
            return result(lua, false, "native_inventory_grant_refused_owner");

        Inventory inventory;
        auto* expected = find<Unreal::UClass>(RC::to_wstring(class_path).c_str());
        if (!locate(context, inventory, expected).empty())
            return result(lua, false, "native_inventory_grant_refused_guid");
        auto* base = inventory.item_class->GetMetaClass().Get();
        if (!live(expected) || !base || expected == base || !expected->IsChildOf(base))
            return result(lua, false, "native_inventory_grant_refused_class");

        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value row(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        if (!row) return result(lua, false, "native_inventory_grant_refused_storage");
        std::wstring text;
        try
        {
            text = RC::to_wstring(reported);
        }
        catch (const std::exception&)
        {
            return result(lua, false, "native_inventory_grant_refused_encoding");
        }
        const auto* tail = inventory.type->ImportText_Direct(text.c_str(), row.get(), context.save,
                                                             Unreal::PPF_Delimited, nullptr);
        auto* payload = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        auto* weight = field<Unreal::FDoubleProperty>(inventory.type->GetStruct().Get(), weight_name);
        if (tail != text.data() + text.size() || inventory.row_class(row.get()) != expected ||
            inventory.identity(row.get()) != context.guid || !payload || !weight ||
            !std::isfinite(weight->GetPropertyValueInContainer(row.get())) ||
            inventory.ammo->GetPropertyValueInContainer(row.get()) < -1 ||
            payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")) ||
            inventory_item_text(context, inventory, row.get()) != reported)
            return result(lua, false, "native_inventory_grant_refused_serialization");

        const auto identical = [&](const void* target) {
            return inventory.type->Identical(row.get(), target) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(row.get()), payload->ContainerPtrToValuePtr<void>(target));
        };
        if (inventory.item)
        {
            if (!identical(inventory.item)) return result(lua, false, "native_inventory_grant_refused_guid");
            return result(lua, true, "present");
        }
        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper current(inventory.backpack, bag);
        if (current.Num() >= max_inventory || !ownership_current(context))
            return result(lua, false, "native_inventory_grant_refused_backpack");
        const auto position = current.AddValue();
        inventory.type->CopyCompleteValue(current.GetRawPtr(position), row.get());
        if (!ownership_current(context) || !identical(current.GetRawPtr(position)))
        {
            current.RemoveValues(position, 1);
            return result(lua, false, "native_inventory_grant_refused_readback");
        }
        return result(lua, true, "added");
    }

    int increase_inventory_stack(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 6 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
            !lua.is_string(4) || !lua.is_integer(5) || !lua.is_string(6))
            return result(lua, false, "native_inventory_grant_refused_arguments");
        Context context;
        Guid operation;
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (pawn <= 0 || save <= 0 || !parse_guid(lua.get_string(), context.guid))
            return result(lua, false, "native_inventory_grant_refused_arguments");
        const std::string class_path(lua.get_string());
        const auto units = lua.get_integer();
        if (units < 1 || units > INT32_MAX || !parse_guid(lua.get_string(), operation))
            return result(lua, false, "native_inventory_grant_refused_arguments");
        if (!silverfish::content_paths::item_class(std::string_view{class_path}, 512))
            return result(lua, false, "native_inventory_grant_refused_class");
        if (!resolve_owner(context, pawn, save).empty())
            return result(lua, false, "native_inventory_grant_refused_owner");
        if (const auto earlier = stack_grant_receipts.find(operation); earlier != stack_grant_receipts.end())
        {
            const auto& receipt = earlier->second;
            if (receipt.save.Get() != context.save || receipt.guid != context.guid ||
                receipt.item_class != class_path || receipt.units != units)
                return result(lua, false, "native_inventory_grant_refused_guid");
            return result(lua, true, "increased");
        }

        auto* item = find<Unreal::UClass>(RC::to_wstring(class_path).c_str());
        if (!live(item) || !boolean(item->GetClassDefaultObject().Get(), STR("Stacks"), true))
            return result(lua, false, "native_inventory_grant_refused_class");
        Inventory inventory;
        if (!locate(context, inventory, item).empty() || inventory.backpack_index < 0)
            return result(lua, false, "native_inventory_grant_refused_stack");
        const auto count = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (count < 1 || count > INT32_MAX - units)
            return result(lua, false, "native_inventory_grant_refused_stack");
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value previous(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        Value changed(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        if (!previous || !changed) return result(lua, false, "native_inventory_grant_refused_storage");
        inventory.type->CopyCompleteValue(previous.get(), inventory.item);
        auto* payload = field<Unreal::FStructProperty>(inventory.type->GetStruct().Get(), payload_name);
        auto* weight = field<Unreal::FDoubleProperty>(inventory.type->GetStruct().Get(), weight_name);
        auto* price = field<Unreal::FIntProperty>(inventory.type->GetStruct().Get(), value_name);
        const auto same_payload = [&](const void* first, const void* second) {
            return payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        };
        if (!payload || !weight || !price || !silverfish::loot_escrow::calculate_stock_stack(
                context.pawn, inventory.type, previous.get(), changed.get(), static_cast<int>(units)))
            return result(lua, false, "native_inventory_grant_stack_calculation_unavailable");
        const auto mass = weight->GetPropertyValueInContainer(changed.get());
        if (inventory.row_class(changed.get()) != item || inventory.identity(changed.get()) != context.guid ||
            inventory.ammo->GetPropertyValueInContainer(changed.get()) != count + units ||
            !std::isfinite(mass) || mass < 0 || price->GetPropertyValueInContainer(changed.get()) < 0 ||
            !same_payload(previous.get(), changed.get()))
            return result(lua, false, "native_inventory_grant_refused_stack");
        Inventory current;
        if (!ownership_current(context) || !locate(context, current, item).empty() || current.backpack_index < 0 ||
            !inventory.type->Identical(previous.get(), current.item) || !same_payload(previous.get(), current.item))
            return result(lua, false, "native_inventory_grant_refused_owner");
        // Keep the receipt in the active campaign even if bridge readback must be retried.
        const auto receipt = stack_grant_receipts.emplace(operation,
            StackGrantReceipt{context.save, context.guid, class_path, static_cast<int>(units)}).first;
        inventory.type->CopyCompleteValue(current.item, changed.get());
        if (!inventory.type->Identical(changed.get(), current.item) || !same_payload(changed.get(), current.item))
        {
            inventory.type->CopyCompleteValue(current.item, previous.get());
            stack_grant_receipts.erase(receipt);
            return result(lua, false, "native_inventory_grant_refused_readback");
        }
        return result(lua, true, "increased");
    }

    template <bool local>
    int store_stock_equipment(const Lua& lua)
    {
        Context context;
        int requested_slot = -1;
        bool reported = false;
        if constexpr (local)
        {
            const auto count = lua.get_stack_size();
            if (count < 3 || count > 4 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
                (count == 4 && !lua.is_integer(4))) return result(lua, false, "native_inventory_equip_refused_owner");
            const auto pawn = lua.get_integer();
            const auto save = lua.get_integer();
            if (!parse_guid(lua.get_string(), context.guid) || !bind_local_inventory(context, pawn, save))
                return result(lua, false, "native_inventory_equip_refused_owner");
            const auto slot = count == 4 ? lua.get_integer() : -1;
            if (slot < -1 || slot >= quick_slots) return result(lua, false, "native_inventory_equip_refused_slot");
            requested_slot = static_cast<int>(slot);
        }
        else
        {
            reported = lua.get_stack_size() == 5 && lua.is_bool(5) && lua.get_bool(5);
            if (!resolve(lua, context, nullptr, nullptr, nullptr, false, &requested_slot).empty())
                return result(lua, false, "native_inventory_equip_refused_owner");
        }

        // The stock menu already applied a reported layout on the owning PC.
        // Keep the fence for an active native weapon mutation separate from the pawn's animation flags.
        if ((!reported && !ready_pawn(context)) || std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
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
        const bool stock_landmine = defaults && type == find<Unreal::UClass>(landmine_path) &&
            boolean(defaults, STR("Stacks"), true) && boolean(defaults, STR("Armed"), false);
        if (!defaults || !item_type || (!stock_landmine && !supported_stack_equipment(type, defaults)) || !payload ||
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
            if ((requested_slot >= 0 && index != requested_slot) ||
                !accepts_stock_quick_slot(index, type, item_kind)) continue;
            const auto* row = equipment.GetRawPtr(index);
            const auto id = inventory.identity(row);
            auto* current_class = inventory.row_class(row);
            const bool empty = !current_class && id == Guid{};
            // A fresh character can select the melee slot before any item is held.
            if (index == selected && (!empty || live(object(context.pawn, STR("EquipedItem"))))) continue;
            if (empty)
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
            !inventory_current<local>(context) || !bag_property->Identical(original_bag.get(), bag) ||
            !equip_property->Identical(original_equipment.get(), equipped))
            return result(lua, false, "native_inventory_equip_refused_staging");
        bag_property->CopyCompleteValue(bag, proposed_bag.get());
        equip_property->CopyCompleteValue(equipped, proposed_equipment.get());
        if (!inventory_current<local>(context) || !bag_property->Identical(proposed_bag.get(), bag) ||
            !equip_property->Identical(proposed_equipment.get(), equipped) ||
            inventory.selected->GetPropertyValueInContainer(context.save) != selected)
        {
            if (inventory_current<local>(context))
            {
                bag_property->CopyCompleteValue(bag, original_bag.get());
                equip_property->CopyCompleteValue(equipped, original_equipment.get());
                if (bag_property->Identical(original_bag.get(), bag) && equip_property->Identical(original_equipment.get(), equipped))
                    return result(lua, false, "native_inventory_equip_refused_rolled_back");
            }
            return result(lua, false, "mutation_unverified_native_inventory_equip");
        }
        if (target == selected)
        {
            EquipCall call;
            if (call.valid(context.pawn)) call.invoke(context.pawn, target);
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

    int preview_worn_or_unequip(const Lua& lua)
    {
        if (lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
            !lua.is_integer(4) || !lua.is_bool(5)) return 0;
        const auto pawn = lua.get_integer();
        const auto save = lua.get_integer();
        Context context;
        if (!parse_guid(lua.get_string(), context.guid) || !bind_local_inventory(context, pawn, save)) return 0;
        const auto slot = lua.get_integer();
        const bool removing = lua.get_bool();
        if (slot < 0 || slot > 9 || (!removing && slot < quick_slots)) return 0;
        Inventory inventory;
        if (removing ? !bind_inventory(context, inventory).empty() : !locate(context, inventory).empty()) return 0;
        if (slot == inventory.selected->GetPropertyValueInContainer(context.save)) return 0;

        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper live_slots(inventory.equipment,
            inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
        auto* destination = slot == 8 ? inventory.helmet->ContainerPtrToValuePtr<void>(context.save) :
            slot == 9 ? inventory.armor->ContainerPtrToValuePtr<void>(context.save) :
            slot < live_slots.Num() ? live_slots.GetRawPtr(static_cast<int>(slot)) : nullptr;
        if (!destination) return 0;

        using Value = std::unique_ptr<void, PropertyDeleter>;
        auto* bag_property = inventory.backpack;
        auto* row_property = inventory.type;
        Value prepared_bag(bag_property->AllocateAndInitializeValue(), {bag_property});
        Value prepared_row(row_property->AllocateAndInitializeValue(), {row_property});
        if (!prepared_bag || !prepared_row) return 0;
        bag_property->CopyCompleteValue(prepared_bag.get(), bag);
        Unreal::FScriptArrayHelper items(bag_property, prepared_bag.get());
        const auto prior = inventory.identity(destination);
        auto* old_class = inventory.row_class(destination);
        if (removing)
        {
            if (prior != context.guid || !live(old_class) || items.Num() >= max_inventory ||
                inventory.ammo->GetPropertyValueInContainer(destination) < -1) return 0;
            row_property->CopyCompleteValue(items.GetRawPtr(items.AddValue()), destination);
            if (slot >= quick_slots)
            {
                auto* defaults = context.save->GetClassPrivate()->GetClassDefaultObject().Get();
                auto* worn = slot == 8 ? inventory.helmet : inventory.armor;
                if (!defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
                    defaults->GetClassPrivate() != context.save->GetClassPrivate()) return 0;
                row_property->CopyCompleteValue(prepared_row.get(), worn->ContainerPtrToValuePtr<void>(defaults));
            }
        }
        else
        {
            if (inventory.backpack_index < 0 || !inventory.item || (prior == Guid{}) != (old_class == nullptr)) return 0;
            auto* type = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
            auto* base = find<Unreal::UClass>(slot == 8 ?
                STR("/Game/Classes/Armor_Items/Item_HelmetMaster_BP.Item_HelmetMaster_BP_C") :
                STR("/Game/Classes/Armor_Items/Item_ArmorMaster_BP.Item_ArmorMaster_BP_C"));
            if (!live(type) || !base || type == base || !type->IsChildOf(base)) return 0;
            row_property->CopyCompleteValue(prepared_row.get(), inventory.item);
            if (prior == Guid{}) items.RemoveValues(inventory.backpack_index, 1);
            else row_property->CopyCompleteValue(items.GetRawPtr(inventory.backpack_index), destination);
        }
        if (!local_inventory_current(context)) return 0;
        const auto ammo = inventory.ammo->GetPropertyValueInContainer(destination);
        bag_property->CopyCompleteValue(bag, prepared_bag.get());
        row_property->CopyCompleteValue(destination, prepared_row.get());
        lua.set_integer(slot);
        lua.set_string(removing ? std::to_string(ammo) : prior == Guid{} ? "-" :
            std::format("{:08X}{:08X}{:08X}{:08X}", prior[0], prior[1], prior[2], prior[3]));
        return 2;
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

    std::optional<int> equipment_ammo(const Context& context, int slot, int stored)
    {
        auto* selected = field<Unreal::FIntProperty>(context.save, STR("PlayerEquipIndex"));
        if (!selected) return {};
        auto* held = object(context.pawn, STR("EquipedItem"));
        if (selected->GetPropertyValueInContainer(context.save) != slot || !live(held))
            return stored >= -1 ? std::optional{stored} : std::nullopt;
        auto* rounds = field<Unreal::FIntProperty>(held, STR("Ammo"));
        if (!rounds || !held_for_unequip(context, held)) return {};
        const auto loaded = rounds->GetPropertyValueInContainer(held);
        return loaded >= -1 ? std::optional{loaded} : std::nullopt;
    }

    int settle_lockpick(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || !Unreal::IsInGameThreadRaw() ||
            lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
            !lua.is_integer(4) || !lua.is_bool(5)) return refuse("stock_lockpick_refused_arguments");
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        Context context;
        if (pawn <= 0 || save <= 0 || !parse_guid(lua.get_string(), context.guid) ||
            !resolve_owner(context, pawn, save).empty() || !ready_pawn(context)) return refuse("stock_lockpick_refused_owner");
        const auto reported_units = lua.get_integer();
        const bool spent = lua.get_bool();
        Inventory inventory;
        if (reported_units < 1 || reported_units > INT32_MAX) return refuse("stock_lockpick_refused_arguments");
        if (!locate(context, inventory).empty() || !inventory.item || inventory.equipment_index < 0)
            return refuse("stock_lockpick_refused_stack");
        const auto units = inventory.ammo->GetPropertyValueInContainer(inventory.item);
        if (units < 1) return refuse("stock_lockpick_refused_stack");
        context.weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        if (!live(context.weapon)) return refuse("stock_lockpick_refused_class");
        const auto path = context.weapon->GetPathName();
        if (path != STR("/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C") &&
            path != STR("/Game/Classes/Equipment/Eqp_LockPick_Hev.Eqp_LockPick_Hev_C")) return refuse("stock_lockpick_refused_class");
        if (!spent)
        {
            result(lua, true, "retained");
            lua.set_integer(units);
            return 3;
        }
        // The owning client reports one spent pick. Apply it once to the exact current stack.
        const auto remaining = units - 1;
        const auto slot = inventory.equipment_index;
        auto* held = inventory.selected->GetPropertyValueInContainer(context.save) == slot
            ? object(context.pawn, STR("EquipedItem")) : nullptr;
        if (held && !held_for_unequip(context, held)) return refuse("stock_lockpick_refused_held_item");
        if (units == 1)
        {
            RemoveEquipmentCall remove;
            if (!remove.valid(context, inventory)) return refuse("stock_lockpick_refused_remove_function");
            if (!remove.invoke(context, inventory) || !ownership_current(context))
                return refuse("mutation_unverified_stock_lockpick_depletion");
            Unreal::FScriptArrayHelper slots(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
            if (slots.Num() <= slot || inventory.row_class(slots.GetRawPtr(slot)) || inventory.identity(slots.GetRawPtr(slot)) != Guid{})
                return refuse("mutation_unverified_stock_lockpick_depletion");
            auto* selected = field<Unreal::FObjectProperty>(context.pawn, STR("EquipedItem"));
            if (held && selected && object(context.pawn, STR("EquipedItem")) == held)
                selected->SetObjectPropertyValue(selected->ContainerPtrToValuePtr<void>(context.pawn), nullptr);
        }
        else
        {
            auto* ammo = field<Unreal::FIntProperty>(held, STR("Ammo"));
            auto* weight = field<Unreal::FDoubleProperty>(held, STR("Weight"));
            auto* defaults = context.weapon->GetClassDefaultObject().Get();
            if (held && (!ammo || !weight || !defaults || !std::isfinite(weight->GetPropertyValueInContainer(defaults))))
                return refuse("stock_lockpick_refused_held_schema");
            inventory.ammo->SetPropertyValueInContainer(inventory.item, remaining);
            if (held)
            {
                ammo->SetPropertyValueInContainer(held, remaining);
                weight->SetPropertyValueInContainer(held, weight->GetPropertyValueInContainer(defaults) * remaining);
            }
        }
        result(lua, true, "spent");
        lua.set_integer(remaining);
        return 3;
    }

    template <bool drop>
    int unequip_stock_equipment(const Lua& lua)
    {
        int expected_ammo = -1;
        bool reported = false;
        if constexpr (drop)
        {
            if (lua.get_stack_size() != 5 || !lua.is_integer(5))
                return result(lua, false, "native_inventory_unequip_refused_arguments");
            const auto expected = lua.get_integer(5);
            if (expected < -1 || expected > INT32_MAX)
                return result(lua, false, "native_inventory_unequip_refused_ammo");
            expected_ammo = static_cast<int>(expected);
        }
        else
        {
            reported = lua.get_stack_size() == 5 && lua.is_bool(5) && lua.get_bool(5);
        }
        Context context;
        int slot = -1;
        if (!resolve(lua, context, nullptr, nullptr, nullptr, false, &slot).empty() || slot < 0 ||
            (!reported && !ready_pawn(context)) ||
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
        const auto ammo = equipment_ammo(context, slot, inventory.ammo->GetPropertyValueInContainer(inventory.item));
        if (!ammo || (drop && *ammo != expected_ammo))
            return result(lua, false, "native_inventory_unequip_refused_ammo");
        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.save);
        auto* slots = inventory.equipment->ContainerPtrToValuePtr<void>(context.save);
        auto* stock_bag = inventory.backpack->ContainerPtrToValuePtr<void>(context.stock);
        auto* stock_slots = inventory.equipment->ContainerPtrToValuePtr<void>(context.stock);
        Unreal::FScriptArrayHelper items(inventory.backpack, bag), equipment(inventory.equipment, slots);
        if ((!drop && items.Num() >= max_inventory) || equipment.Num() < quick_slots || !held_property ||
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
        if constexpr (!drop)
        {
            auto* returned = staged_items.GetRawPtr(staged_items.AddValue());
            inventory.type->CopyCompleteValue(returned, inventory.item);
            if (!inventory.type->Identical(returned, inventory.item) ||
                !payload->Identical(payload->ContainerPtrToValuePtr<void>(returned),
                                    payload->ContainerPtrToValuePtr<void>(inventory.item)))
                return result(lua, false, "native_inventory_unequip_refused_staging");
            inventory.ammo->SetPropertyValueInContainer(returned, *ammo);
        }
        if (!ownership_current(context))
            return result(lua, false, "native_inventory_unequip_refused_staging");
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
        if constexpr (!drop)
            inventory.backpack->CopyCompleteValue(bag, proposed_bag.get());
        Inventory relocated;
        if (!inventory.backpack->Identical(proposed_bag.get(), bag) || !locate(context, relocated).empty() ||
            (drop ? relocated.item != nullptr : relocated.backpack_index != staged_items.Num() - 1))
            return result(lua, false, "mutation_unverified_native_inventory_unequip_pack");
        result(lua, true, drop ? "debited" : "unequipped");
        lua.set_integer(slot);
        lua.set_string(RC::to_utf8_string(context.weapon->GetPathName()));
        lua.set_integer(*ammo);
        return 5;
    }

    template <bool local>
    int sort_backpack(const Lua& lua)
    {
        if (!env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") || !Unreal::IsInGameThreadRaw() ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "native_inventory_sort_refused_scope");
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        Context context;
        if constexpr (local)
        {
            if (!bind_local_inventory(context, pawn_address, save_address))
                return result(lua, false, "native_inventory_sort_refused_owner");
        }
        else if (pawn_address <= 0 || save_address <= 0 || !resolve_owner(context, pawn_address, save_address).empty())
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

        if (!inventory_current<local>(context) || !backpack->Identical(original.get(), current))
            return result(lua, false, "mutation_unverified_native_inventory_sort_scope");
        const bool changed = !backpack->Identical(candidate, current);
        if (changed)
        {
            backpack->CopyCompleteValue(current, candidate);
            if (!inventory_current<local>(context) || !backpack->Identical(candidate, current))
            {
                if (inventory_current<local>(context))
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

    bool read_local_stack_selection(const Lua& lua, Context& context, StackSplit& split, bool full_split)
    {
        if (lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3) ||
            !lua.is_integer(4) || !lua.is_integer(5)) return false;
        const auto pawn = lua.get_integer();
        const auto save = lua.get_integer();
        if (!parse_guid(lua.get_string(), context.guid) || !bind_local_inventory(context, pawn, save)) return false;
        const auto take = lua.get_integer();
        const auto expected = lua.get_integer();
        if (take < 1 || (full_split ? expected != take : expected <= take) || expected > INT32_MAX) return false;
        split.take = static_cast<std::int32_t>(take);
        split.expected = static_cast<std::int32_t>(expected);
        return true;
    }

    template <bool local>
    int split_backpack_stack(const Lua& lua)
    {
        Context context;
        StackSplit split;
        if constexpr (local)
        {
            if (!read_local_stack_selection(lua, context, split, false))
                return result(lua, false, "native_inventory_split_refused_owner_or_selection");
        }
        else if (const auto error = resolve(lua, context, nullptr, nullptr, &split); !error.empty())
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
        if (!silverfish::drop_item_class::stacks(item_class).value_or(false))
            return result(lua, false, "native_inventory_split_refused_class");
        if (inventory.ammo->GetPropertyValueInContainer(inventory.item) != split.expected)
            return result(lua, false, "native_inventory_split_refused_stale_quantity");
        auto* row_type = inventory.type->GetStruct().Get();
        auto* never_drop = field<Unreal::FBoolProperty>(row_type,
            STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        auto* payload = field<Unreal::FStructProperty>(row_type, payload_name);
        auto* value = field<Unreal::FIntProperty>(row_type, value_name);
        auto* weight = field<Unreal::FDoubleProperty>(row_type, weight_name);
        if (!never_drop || never_drop->GetPropertyValueInContainer(inventory.item) || !value || !weight ||
            !payload || payload->GetStruct().Get() != find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct")))
            return result(lua, false, "native_inventory_split_refused_stock_stack");

        auto* property = inventory.backpack;
        auto* live_array = property->ContainerPtrToValuePtr<void>(context.save);
        Unreal::FScriptArrayHelper current(property, live_array);
        const auto count = current.Num();
        if (count >= max_inventory) return result(lua, false, "native_inventory_split_refused_backpack_full");
        auto* host_array = local ? nullptr : property->ContainerPtrToValuePtr<void>(context.stock);
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(property->AllocateAndInitializeValue(), {property});
        Value proposed(property->AllocateAndInitializeValue(), {property});
        Value host_before(host_array ? property->AllocateAndInitializeValue() : nullptr, {property});
        if (!original || !proposed || (host_array && !host_before))
            return result(lua, false, "native_inventory_split_refused_storage");
        property->CopyCompleteValue(original.get(), live_array);
        property->CopyCompleteValue(proposed.get(), live_array);
        if (host_array) property->CopyCompleteValue(host_before.get(), host_array);
        Unreal::FScriptArrayHelper original_rows(property, original.get()), staged(property, proposed.get());
        const auto new_index = staged.AddValue();
        auto* remainder = staged.GetRawPtr(inventory.backpack_index);
        auto* created = staged.GetRawPtr(new_index);
        const auto* source = original_rows.GetRawPtr(inventory.backpack_index);
        const bool split_ok = silverfish::native_guest_item::split_stock_stack(
            context.pawn, inventory.type, source, split.take, created, remainder);
        if (!inventory_current<local>(context) || !property->Identical(original.get(), live_array) ||
            (host_array && !property->Identical(host_before.get(), host_array)))
            return result(lua, false, "mutation_unverified_native_inventory_split_stock_call");
        if (!split_ok) return result(lua, false, "native_inventory_split_refused_stock_result");
        const auto* stock_payload = payload->ContainerPtrToValuePtr<void>(source);
        const auto remaining = split.expected - split.take;
        const auto valid_stack = [&](const void* row, int quantity) {
            const double mass = weight->GetPropertyValueInContainer(row);
            return inventory.row_class(row) == item_class && inventory.ammo->GetPropertyValueInContainer(row) == quantity &&
                value->GetPropertyValueInContainer(row) >= 0 && mass >= 0 && mass <= 100000 &&
                never_drop->GetPropertyValueInContainer(row) == false &&
                payload->Identical(stock_payload, payload->ContainerPtrToValuePtr<void>(row));
        };
        const auto stock_guid = inventory.identity(created);
        if constexpr (local) split.created = stock_guid;
        else inventory.set_identity(created, split.created);
        auto distinct = context;
        distinct.guid = split.created;
        Inventory collision;
        if (!locate(distinct, collision).empty() || collision.item)
            return result(lua, false, "native_inventory_split_refused_guid_collision");
        if (staged.Num() != count + 1 || new_index != count || inventory.identity(remainder) != context.guid ||
            stock_guid == Guid{} || stock_guid == context.guid || inventory.identity(created) != split.created ||
            !valid_stack(remainder, remaining) || !valid_stack(created, split.take))
            return result(lua, false, "native_inventory_split_refused_staging");
        property->CopyCompleteValue(live_array, proposed.get());
        if (!inventory_current<local>(context) || !property->Identical(proposed.get(), live_array))
        {
            if (inventory_current<local>(context))
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
        if constexpr (local)
        {
            const auto& guid = split.created;
            lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
            return 7;
        }
        return 6;
    }

    template <bool local>
    int merge_backpack_stacks(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        Context context;
        StackSplit move;
        if (lua.get_stack_size() != 6 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_string(3) || !lua.is_string(4) || !lua.is_integer(5) || !lua.is_integer(6))
            return refuse("native_inventory_merge_refused_arguments");
        const auto pawn = lua.get_integer(), save = lua.get_integer();
        if (!parse_guid(lua.get_string(), context.guid) || !parse_guid(lua.get_string(), move.created) ||
            context.guid == move.created) return refuse("native_inventory_merge_refused_identity");
        const auto take = lua.get_integer(), expected = lua.get_integer();
        if (take < 1 || expected < 1 || take > INT32_MAX || expected > INT32_MAX - take)
            return refuse("native_inventory_merge_refused_quantity");
        const bool qualified = local ? bind_local_inventory(context, pawn, save)
            : Unreal::IsInGameThreadRaw() && resolve_owner(context, pawn, save).empty();
        if (!qualified) return refuse("native_inventory_merge_refused_owner");
        auto target = context;
        target.guid = move.created;
        Inventory source, destination;
        if (!locate(context, source).empty() || source.backpack_index < 0 ||
            !locate(target, destination).empty() || destination.backpack_index < 0 ||
            source.ammo->GetPropertyValueInContainer(source.item) != take ||
            destination.ammo->GetPropertyValueInContainer(destination.item) != expected)
            return refuse("native_inventory_merge_refused_stale_stacks");
        auto* item_class = Unreal::Cast<Unreal::UClass>(source.row_class(source.item));
        auto* row_struct = source.type->GetStruct().Get();
        auto* payload = field<Unreal::FStructProperty>(row_struct, payload_name);
        auto* never_drop = field<Unreal::FBoolProperty>(row_struct, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        auto* weight = field<Unreal::FDoubleProperty>(row_struct, weight_name);
        auto* price = field<Unreal::FIntProperty>(row_struct, value_name);
        const auto same_payload = [&](const void* first, const void* second) {
            return payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        };
        if (!item_class || source.row_class(destination.item) != item_class ||
            !silverfish::drop_item_class::stacks(item_class).value_or(false) || !payload || !never_drop || !weight || !price ||
            never_drop->GetPropertyValueInContainer(source.item) || never_drop->GetPropertyValueInContainer(destination.item) ||
            !same_payload(source.item, destination.item)) return refuse("native_inventory_merge_refused_incompatible_stacks");

        auto* property = source.backpack;
        auto* live_array = property->ContainerPtrToValuePtr<void>(context.save);
        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(property->AllocateAndInitializeValue(), {property});
        Value proposed(property->AllocateAndInitializeValue(), {property});
        Value combined(source.type->AllocateAndInitializeValue(), {source.type});
        if (!original || !proposed || !combined) return refuse("native_inventory_merge_refused_storage");
        property->CopyCompleteValue(original.get(), live_array);
        property->CopyCompleteValue(proposed.get(), live_array);
        Unreal::FScriptArrayHelper staged(property, proposed.get());
        auto* surviving = staged.GetRawPtr(destination.backpack_index);
        const bool calculated = silverfish::loot_escrow::calculate_stock_stack(
            context.pawn, source.type, surviving, combined.get(), static_cast<int>(take));
        if (!inventory_current<local>(context) || !property->Identical(original.get(), live_array))
            return refuse("mutation_unverified_native_inventory_merge_stock_call");
        const auto mass = weight->GetPropertyValueInContainer(combined.get());
        const auto after = expected + take;
        if (!calculated || source.row_class(combined.get()) != item_class || source.identity(combined.get()) != target.guid ||
            source.ammo->GetPropertyValueInContainer(combined.get()) != after ||
            !std::isfinite(mass) || mass < 0 || price->GetPropertyValueInContainer(combined.get()) < 0 ||
            !same_payload(surviving, combined.get())) return refuse("native_inventory_merge_refused_stock_result");
        source.type->CopyCompleteValue(surviving, combined.get());
        staged.RemoveValues(source.backpack_index, 1);
        property->CopyCompleteValue(live_array, proposed.get());
        if (!inventory_current<local>(context) || !property->Identical(proposed.get(), live_array))
        {
            if (inventory_current<local>(context))
            {
                property->CopyCompleteValue(live_array, original.get());
                if (property->Identical(original.get(), live_array)) return refuse("native_inventory_merge_refused_rolled_back");
            }
            return refuse("mutation_unverified_native_inventory_merge_commit");
        }
        proof(lua, true, "merged", static_cast<std::int32_t>(expected), static_cast<std::int32_t>(after));
        lua.set_integer(take);
        lua.set_string(RC::to_utf8_string(item_class->GetPathName()));
        return 6;
    }

    const profiles::StockAmmo* unloaded_ammo(const profiles::Profile* profile, const BoltProfile* bolt, Unreal::UClass* ammo)
    {
        if (profile) return magazine_round(*profile, ammo);
        if (!bolt) return nullptr;
        if (ammo == find<Unreal::UClass>(rifle_fmj_path)) return &profiles::thirty_06_fmj;
        if (ammo == find<Unreal::UClass>(rifle_incendiary_path)) return &profiles::thirty_06_incendiary;
        return nullptr;
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

    template <bool local>
    int take_ammo(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        Context context;
        StackSplit selection;
        const bool qualified = local ? read_local_stack_selection(lua, context, selection, true)
            : resolve(lua, context, nullptr, nullptr, &selection, true).empty();
        if (!qualified)
            return refuse("native_inventory_take_ammo_refused_owner_or_selection");
        if (!ready_pawn(context) || std::ranges::any_of(smg_holds, [&](const SmgHold& hold) {
                return hold.fenced && hold.pawn.Get() == context.pawn;
            })) return refuse("native_inventory_take_ammo_refused_busy");
        Inventory inventory;
        if (!locate(context, inventory).empty() || !inventory.item)
            return refuse("native_inventory_take_ammo_refused_source");
        auto* weapon = Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item));
        const auto* profile = stock_magazine(weapon);
        const auto* bolt = stock_bolt(weapon);
        auto* defaults = weapon ? weapon->GetClassDefaultObject().Get() : nullptr;
        const auto* row_type = inventory.type->GetStruct().Get();
        auto* weight = field<Unreal::FDoubleProperty>(inventory.type->GetStruct().Get(), weight_name);
        if (!defaults || !row_type || !weight ||
            !silverfish::native_guest_item::take_ammo_supported(weapon, selection.expected) ||
            inventory.ammo->GetPropertyValueInContainer(inventory.item) != selection.expected ||
            (bolt ? !stock_bolt_shape(defaults, *bolt) : !profile || !saved_magazine_round(*profile, weapon, inventory)))
            return refuse("native_inventory_take_ammo_refused_loaded_state");

        context.weapon = weapon;
        const bool stored = inventory.equipment_index >= 0;
        auto* held = stored && inventory.equipment_index == inventory.selected->GetPropertyValueInContainer(context.save)
            ? object(context.pawn, STR("EquipedItem")) : nullptr;
        auto* rounds = field<Unreal::FIntProperty>(held, STR("Ammo"));
        auto* held_weight = field<Unreal::FDoubleProperty>(held, STR("Weight"));
        auto* update = held ? find<Unreal::UFunction>(update_mag_path) : nullptr;
        if (held && (held != equipped(context, inventory) || !rounds || !held_weight || !update ||
                held->GetFunctionByNameInChain(Unreal::FName(STR("Update_Mag"))) != update ||
                update->GetNumParms() != 0 || update->GetParmsSize() != 0 || update->GetReturnProperty() ||
                rounds->GetPropertyValueInContainer(held) != selection.expected ||
                !boolean(held, STR("Reloading"), false) || !boolean(held, STR("IsCycling"), false) ||
                !boolean(held, STR("Holding_Primary"), false) ||
                (bolt ? !stock_bolt_shape(held, *bolt) || !mounted_payload_matches(held, inventory)
                      : !ordinary_magazine_payload(profile, context, inventory, held))))
            return refuse("native_inventory_take_ammo_refused_held_weapon");

        auto* property = inventory.backpack;
        auto* live_array = property->ContainerPtrToValuePtr<void>(context.save);
        auto* host_array = local ? nullptr : property->ContainerPtrToValuePtr<void>(context.stock);
        auto* flags = local ? nullptr : field<Unreal::FMapProperty>(context.stock, STR("Flags"));
        if ((!local && !flags) || Unreal::FScriptArrayHelper(property, live_array).Num() >= max_inventory)
            return refuse("native_inventory_take_ammo_refused_backpack");

        StockSplit split;
        if (!split.bind(context.pawn, row_type))
            return refuse("native_inventory_take_ammo_refused_stock_parameters");

        using Value = std::unique_ptr<void, PropertyDeleter>;
        Value original(property->AllocateAndInitializeValue(), {property});
        Value proposed(property->AllocateAndInitializeValue(), {property});
        Value host_before(host_array ? property->AllocateAndInitializeValue() : nullptr, {property});
        Value flags_before(flags ? flags->AllocateAndInitializeValue() : nullptr, {flags});
        Value expected_weapon(inventory.type->AllocateAndInitializeValue(), {inventory.type});
        Value stored_before(stored ? inventory.type->AllocateAndInitializeValue() : nullptr, {inventory.type});
        if (!original || !proposed || (!local && (!host_before || !flags_before)) ||
            !expected_weapon || (stored && !stored_before))
            return refuse("native_inventory_take_ammo_refused_storage");
        auto* host_flags = flags ? flags->ContainerPtrToValuePtr<void>(context.stock) : nullptr;
        property->CopyCompleteValue(original.get(), live_array);
        property->CopyCompleteValue(proposed.get(), live_array);
        if (host_array) property->CopyCompleteValue(host_before.get(), host_array);
        if (host_flags) flags->CopyCompleteValue(flags_before.get(), host_flags);
        if (stored) inventory.type->CopyCompleteValue(stored_before.get(), inventory.item);
        const auto stored_row = [&] {
            Unreal::FScriptArrayHelper loadout(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
            if (!stored || inventory.equipment_index >= loadout.Num()) return static_cast<void*>(nullptr);
            return static_cast<void*>(loadout.GetRawPtr(inventory.equipment_index));
        };
        const auto scope_unchanged = [&] {
            if (!inventory_current<local>(context)) return false;
            if constexpr (local) return true;
            return property->Identical(host_before.get(), host_array) && flags->Identical(flags_before.get(), host_flags);
        };

        const auto did_split = split.run(context.pawn, inventory.item, 1);
        if (!scope_unchanged() || !property->Identical(original.get(), live_array) ||
            (stored && (stored_row() != inventory.item || !inventory.type->Identical(stored_before.get(), inventory.item))))
            return refuse("mutation_unverified_native_inventory_take_ammo_stock_call");
        auto* extracted = split.moved();
        auto* unloaded = split.left();
        const auto stock_guid = inventory.identity(extracted);
        const auto empty_weight = weight->GetPropertyValueInContainer(unloaded);
        auto* ammo_class = Unreal::Cast<Unreal::UClass>(inventory.row_class(extracted));
        const auto* ammo = unloaded_ammo(profile, bolt, ammo_class);
        AmmoStackSchema stock_ammo;
        // The stock split reads the exact saved ammo row, including a bolt-action's alternate load.
        if (!did_split || !ammo_class || !ammo || !ammo_item_row_matches(ammo_class, *ammo) ||
            !stock_ammo.read(ammo_class, inventory) || !stock_ammo.qualifies(selection.expected) ||
            (held && (mounted_ammo(held) != ammo_class || (bolt && !bolt_round(held, ammo_class, stock_projectile(held)))))
            || stock_guid == Guid{} || stock_guid == context.guid
            || inventory.row_class(unloaded) != weapon
            || inventory.ammo->GetPropertyValueInContainer(extracted) != selection.expected
            || inventory.ammo->GetPropertyValueInContainer(unloaded) != 0 || !std::isfinite(empty_weight) || empty_weight < 0
            || !stock_ammo.totals_match(extracted, selection.expected))
            return refuse("native_inventory_take_ammo_refused_stock_result");
        inventory.type->CopyCompleteValue(expected_weapon.get(), inventory.item);
        inventory.ammo->SetPropertyValueInContainer(expected_weapon.get(), 0);
        weight->SetPropertyValueInContainer(expected_weapon.get(), empty_weight);
        if (!inventory.type->Identical(expected_weapon.get(), unloaded))
            return refuse("native_inventory_take_ammo_refused_weapon_identity");
        if constexpr (local) selection.created = stock_guid;
        auto distinct = context;
        distinct.guid = selection.created;
        Inventory collision;
        if (!locate(distinct, collision).empty() || collision.item)
            return refuse("native_inventory_take_ammo_refused_guid_collision");

        Unreal::FScriptArrayHelper staged(property, proposed.get());
        const auto inserted = staged.AddValue();
        if (!stored) inventory.type->CopyCompleteValue(staged.GetRawPtr(inventory.backpack_index), unloaded);
        inventory.set_identity(extracted, selection.created);
        inventory.type->CopyCompleteValue(staged.GetRawPtr(inserted), extracted);
        if ((!stored && !inventory.type->Identical(staged.GetRawPtr(inventory.backpack_index), unloaded))
            || !inventory.type->Identical(staged.GetRawPtr(inserted), extracted))
            return refuse("native_inventory_take_ammo_refused_staging");
        property->CopyCompleteValue(live_array, proposed.get());
        if (stored) inventory.type->CopyCompleteValue(inventory.item, unloaded);
        if (!scope_unchanged() || !property->Identical(proposed.get(), live_array) ||
            (stored && (stored_row() != inventory.item || !inventory.type->Identical(expected_weapon.get(), inventory.item))))
        {
            if (scope_unchanged() && (!stored || stored_row() == inventory.item))
            {
                property->CopyCompleteValue(live_array, original.get());
                if (stored) inventory.type->CopyCompleteValue(inventory.item, stored_before.get());
                if (property->Identical(original.get(), live_array) &&
                    (!stored || inventory.type->Identical(stored_before.get(), inventory.item)))
                    return refuse("native_inventory_take_ammo_refused_rolled_back");
            }
            return refuse("mutation_unverified_native_inventory_take_ammo_commit");
        }
        const Unreal::FWeakObjectPtr held_actor{held};
        if (held)
        {
            rounds->SetPropertyValueInContainer(held, 0);
            held_weight->SetPropertyValueInContainer(held, empty_weight);
            held->ProcessEvent(update, nullptr);
        }
        if (!scope_unchanged() || !property->Identical(proposed.get(), live_array) ||
            (stored && (!stored_row() || !inventory.type->Identical(expected_weapon.get(), stored_row()))) ||
            (held && (held_actor.Get() != held || object(context.pawn, STR("EquipedItem")) != held ||
                       rounds->GetPropertyValueInContainer(held) != 0 || held_weight->GetPropertyValueInContainer(held) != empty_weight)))
            return refuse("mutation_unverified_native_inventory_take_ammo_commit");
        proof(lua, true, "unloaded", selection.expected, 0);
        lua.set_integer(selection.expected);
        lua.set_string(RC::to_utf8_string(ammo_class->GetPathName()));
        lua.set_string(RC::to_utf8_string(weapon->GetPathName()));
        if constexpr (local)
        {
            const auto& guid = selection.created;
            lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
            lua.set_integer(inventory.equipment_index);
            return 9;
        }
        return 7;
    }

    template <Operation operation, bool underbarrel = false>
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
        ShotView shot_view;
        std::string reload_round;
        if (const auto error = resolve(lua, context, operation == Operation::Seed ? &seed_selection : nullptr,
                                       nullptr, nullptr, false, nullptr, operation == Operation::Fire ? &shot_view : nullptr,
                                       operation == Operation::Reload ? &reload_round : nullptr);
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
            if (!context.weapon) context.weapon = qualified_melee_item(row_class, spade_path);
            if constexpr (operation == Operation::Inspect)
            {
                auto* item_type = Unreal::Cast<Unreal::UClass>(row_class);
                if (!context.weapon && throwable_profile(item_type)) context.weapon = item_type;
            }
        }
        if (!context.weapon) return fail("guest_weapon_class_unqualified");
        if constexpr (operation == Operation::Equip) return equip(lua, context, inventory);
        if constexpr (operation == Operation::Inspect)
            return underbarrel ? inspect_underbarrel(lua, context, inventory) : inspect(lua, context, inventory);
        if constexpr (operation == Operation::Reload) return reload(lua, context, inventory, underbarrel, reload_round);
        return fire(lua, context, inventory, operation == Operation::Disarm, shot_view, underbarrel);
    }
}

Unreal::UObject* silverfish::native_guest_item::active_loki_barrel(Unreal::UObject* pawn, Unreal::UObject* receiver)
{
    auto* child = mounted_loki(receiver, pawn);
    return child && boolean(receiver, STR("Tertiary_Mode"), true) && object(receiver, STR("InputIntercept")) == child
        ? child : nullptr;
}

std::string silverfish::native_guest_item::local_equipped_identity(Unreal::UObject* pawn, Unreal::UObject* receiver)
{
    if (!live(pawn) || !live(receiver) || object(pawn, STR("EquipedItem")) != receiver) return {};
    Context context;
    context.save = object(pawn, STR("Save"));
    if (!live(context.save) || context.save->GetClassPrivate() != find<Unreal::UClass>(save_path)) return {};
    Inventory inventory;
    if (!bind_inventory(context, inventory).empty()) return {};
    Unreal::FScriptArrayHelper rows(inventory.equipment, inventory.equipment->ContainerPtrToValuePtr<void>(context.save));
    const auto index = inventory.selected->GetPropertyValueInContainer(context.save);
    if (index < 0 || index >= rows.Num() || rows.Num() > max_inventory) return {};
    const auto* row = rows.GetRawPtr(index);
    if (inventory.row_class(row) != receiver->GetClassPrivate()) return {};
    const auto guid = inventory.identity(row);
    return guid != Guid{} ? std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]) : std::string{};
}

silverfish::native_guest_item::ShotView silverfish::native_guest_item::capture_shot_view(
    Unreal::UObject* pawn, Unreal::UObject* held, Unreal::UObject* barrel)
{
    ShotView view;
    auto* camera = shot_component(pawn, STR("Camera"), camera_component);
    if (!camera) return view;
    const auto direction = actor_aim_vector(camera, component_forward);
    if (!direction || std::abs(std::hypot((*direction)[0], (*direction)[1], (*direction)[2]) - 1.0) > 0.001)
        return view;
    view.direction = *direction;
    const auto eye = actor_aim_vector(camera, component_location);
    if (!eye) return view;
    const auto aim = guest_aim_point(pawn, held, *eye, view.direction);
    if (auto* muzzle = shot_component(barrel ? barrel : held, STR("Arrow_Muzzle"), muzzle_component))
        view.muzzle = actor_aim_vector(muzzle, component_location);

    const auto range = stock_sight_range(pawn);
    if (!aim && !view.muzzle) return view;
    view.eye = eye;
    // Keep the guest's origin even when its camera trace cannot resolve a target.
    view.target = aim ? aim->point : ahead(*view.muzzle, view.direction, std::max(1.0, range));
    if (!view.muzzle || range <= 0.0) return view;

    const auto axis = shot_axis(*view.muzzle, *view.target, view.direction);
    // A foreground sight obstruction can be behind the muzzle. Trace forward
    // from the muzzle in that case, using the same centerline as the stock launch.
    const auto target_offset = difference(*view.target, *view.muzzle);
    const auto target_distance = target_offset[0] * axis[0] + target_offset[1] * axis[1] + target_offset[2] * axis[2];
    // When the sight hits a capsule, allow another meter to reach its projectile collision.
    const auto distance = target_distance >= 1.0 ? target_distance + 100.0 : range;
    const auto end = ahead(*view.muzzle, axis, distance);
    const auto hit = trace_shot(pawn, *view.muzzle, end, projectile_trace, std::array{held});
    const auto world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
    if (hit && hit->hit && available_projectile_target(hit->actor, world) && takes_projectile_damage(hit->actor))
    {
        const auto number = contact_reference(hit->actor);
        auto path = number ? std::format("npc:{}", number) :
            silverfish::campaign_npc::is_spawned_stock_npc(hit->actor) ? std::string{} :
            RC::to_utf8_string(hit->actor->GetPathName());
        if (valid_contact_path(path))
        {
            view.contact_actor = std::move(path);
            view.contact = hit->point;
        }
    }
    return view;
}

bool silverfish::native_guest_item::take_ammo_supported(Unreal::UObject* item_class, int rounds)
{
    if (rounds <= 0) return false;
    auto* weapon = Unreal::Cast<Unreal::UClass>(item_class);
    if (const auto* profile = stock_magazine(weapon)) return profile->ammo && rounds <= profile->capacity;
    return stock_bolt(weapon) && rounds <= bolt_magazine;
}

std::optional<int> silverfish::native_guest_item::quick_drop_ammo(Unreal::UObject* pawn, Unreal::UObject* save,
                                                                  Unreal::UObject* item_class, int slot, int stored)
{
    Context context;
    context.pawn = pawn;
    context.save = save;
    context.world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
    context.weapon = Unreal::Cast<Unreal::UClass>(item_class);
    return equipment_ammo(context, slot, stored);
}

std::string_view silverfish::native_guest_item::finish_local_removal(Unreal::UObject* pawn, Unreal::UObject* save,
                                                                    Unreal::UObject* prior_actor, int slot,
                                                                    std::string_view identity, std::optional<int> rounds)
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
    if (rounds)
    {
        if (!inventory.item || inventory.backpack_index < 0) return "unequip_backpack_item_unavailable";
        if (inventory.ammo->GetPropertyValueInContainer(inventory.item) != *rounds) return "unequip_backpack_ammunition_mismatch";
    }
    else if (inventory.item) return "dropped_equipment_still_owned";
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
    context.weapon = rounds ? Unreal::Cast<Unreal::UClass>(inventory.row_class(inventory.item)) : prior_actor->GetClassPrivate();
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
    lua.register_function("SilverfishSettleNativeGuestLockpick", settle_lockpick);
    lua.register_function("SilverfishBindNativeShotTarget", bind_contact_reference);
    if (fixture_shot_contact_enabled())
        lua.register_function("SilverfishReadFixtureShotContact", read_fixture_shot_contact);
    if (env("SILVERFISH_COOP_NATIVE_INTERACT_INPUT"))
    {
        lua.register_function("SilverfishProjectNativeLokiRound", project_loki_round);
        lua.register_function("SilverfishReadLocalGuestEquipmentSelection", read_local_selection);
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
    lua.register_function("SilverfishInspectNativeGuestUnderbarrel", run<Operation::Inspect, true>);
    lua.register_function("SilverfishFireNativeGuestWeapon", run<Operation::Fire>);
    lua.register_function("SilverfishFireNativeGuestUnderbarrel", run<Operation::Fire, true>);
    lua.register_function("SilverfishReloadNativeGuestWeapon", run<Operation::Reload>);
    lua.register_function("SilverfishReloadNativeGuestUnderbarrel", run<Operation::Reload, true>);
    lua.register_function("SilverfishSplitNativeGuestAmmoStack", split_backpack_stack<false>);
    lua.register_function("SilverfishSplitLocalGuestStack", split_backpack_stack<true>);
    lua.register_function("SilverfishMergeNativeGuestStacks", merge_backpack_stacks<false>);
    lua.register_function("SilverfishMergeLocalGuestStacks", merge_backpack_stacks<true>);
    lua.register_function("SilverfishTakeNativeGuestWeaponAmmo", take_ammo<false>);
    lua.register_function("SilverfishTakeLocalGuestWeaponAmmo", take_ammo<true>);
    lua.register_function("SilverfishSortNativeGuestBackpack", sort_backpack<false>);
    lua.register_function("SilverfishStoreNativeGuestEquipment", store_stock_equipment<false>);
    lua.register_function("SilverfishSortLocalGuestBackpack", sort_backpack<true>);
    lua.register_function("SilverfishStoreLocalGuestEquipment", store_stock_equipment<true>);
    lua.register_function("SilverfishExportLocalInventoryItem", export_inventory_item<true>);
    lua.register_function("SilverfishExportDetachedInventoryItem", export_inventory_item<false>);
    lua.register_function("SilverfishImportGuestInventoryAddition", import_inventory_addition);
    lua.register_function("SilverfishArmInventoryGrants", arm_inventory_grants);
    lua.register_function("SilverfishReadInventoryGrant", read_inventory_grant);
    lua.register_function("SilverfishAcknowledgeInventoryGrant", acknowledge_inventory_grant);
    lua.register_function("SilverfishPreserveInventoryGrantPayloads", preserve_inventory_grant_payloads);
    lua.register_function("SilverfishClearInventoryGrants", clear_inventory_grants);
    lua.register_function("SilverfishIncreaseGuestInventoryStack", increase_inventory_stack);
    if (env("SILVERFISH_COOP_TITLE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS"))
        lua.register_function("SilverfishFixtureLocalTypedInventory", fixture_copy_stock_weapon_payload);
    lua.register_function("SilverfishPreviewLocalGuestGear", preview_worn_or_unequip);
    lua.register_function("SilverfishUnequipNativeGuestEquipment", unequip_stock_equipment<false>);
    lua.register_function("SilverfishDebitNativeGuestQuickDrop", unequip_stock_equipment<true>);
    lua.register_function("SilverfishSelectNativeGuestEquipment", select_stock_equipment);
    lua.register_function("SilverfishThrowNativeGuestFlare", throw_flare);
    if (env("SILVERFISH_COOP_NATIVE_FIXTURES"))
    {
        if (env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") && env("SILVERFISH_COOP_HEADLESS"))
            lua.register_function("SilverfishEquipNativeGuestOldPipe", run<Operation::EquipOldPipe>);
        lua.register_function("SilverfishSeedNativeGuestWeapon", run<Operation::Seed>);
        lua.register_function("SilverfishSeedNativeGuestAmmo", run<Operation::SeedAmmo>);
        lua.register_function("SilverfishDisarmNativeGuestWeapon", run<Operation::Disarm>);
    }
    if (hold_enabled())
    {
        lua.register_function("SilverfishStartNativeGuestSmgHold", start_hold);
        lua.register_function("SilverfishReadNativeGuestSmgHold", read_hold<HoldAction::observe>);
        lua.register_function("SilverfishStopNativeGuestSmgHold", read_hold<HoldAction::stop>);
        lua.register_function("SilverfishRenewNativeGuestSmgHold", read_hold<HoldAction::renew>);
        lua.register_function("SilverfishUpdateNativeGuestSmgAim", update_hold_view);
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
    remove_grant_hooks();
    stack_grant_receipts.clear();
    if (tap_birth_hook != Hook::ERROR_ID) Hook::UnregisterCallback(tap_birth_hook);
    tap_birth_hook = Hook::ERROR_ID;
    local_selection = {};
    for (auto& callback : flare_hooks)
    {
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        callback = Hook::ERROR_ID;
    }
    local_flare = {};
}
