#include "quest_rewards.hpp"

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
    constexpr int max_guests = 4;
    constexpr int max_inventory = 4096;
    constexpr auto borough_world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough");
    constexpr auto scientist_world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01");
    constexpr auto black_market_world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto weapon_path = STR("/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_30-06_Incendiary_BP.Ammo_30-06_Incendiary_BP_C");
    constexpr auto pistol_path = STR("/Game/Classes/Weapons/Wep_Pistol_Auto.Wep_Pistol_Auto_C");
    constexpr auto ball_ammo_path = STR("/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C");
    constexpr auto wire_path = STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C");
    constexpr auto board_item_path = STR("/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C");
    constexpr auto item_base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto quest_path = STR("/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct");
    constexpr auto payload_path = STR("/Script/StructUtils.InstancedStruct");
    constexpr auto guid_path = STR("/Script/CoreUObject.Guid");
    constexpr auto inventory_library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto make_stack_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeItemStack");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};

    struct RewardItems
    {
        const U::TCHAR* weapon_path;
        const U::TCHAR* ammo_path;
        int units;
        int unit_value;
        double unit_weight;
    };
    constexpr RewardItems copper_stop_items{weapon_path, ammo_path, 14, 12, 0.014};
    constexpr RewardItems meet_broker_items{pistol_path, ball_ammo_path, 9, 3, 0.01};

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return opt_in("SILVERFISH_COOP_NATIVE_ZONE") && opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    template <typename T>
    T* find(const U::TCHAR* path) { return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path); }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* value = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool bool_value(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, U::UClass* type) { return type && live(value) && value->GetClassPrivate() == type; }
    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    bool authority(U::UObject* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            bool_value(actor, STR("bActorIsBeingDestroyed"), false) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool read_addresses(const Lua& lua, auto& addresses)
    {
        for (int i = 1; i <= static_cast<int>(addresses.size()); ++i) if (!lua.is_integer(i)) return false;
        for (auto& value : addresses)
        {
            const auto input = lua.get_integer();
            if (input <= 0) return false;
            value = static_cast<Address>(input);
        }
        return true;
    }

    Guid read_guid(std::string_view input)
    {
        Guid result{};
        if (input.size() != 32 || !std::ranges::all_of(input, [](char value) {
                return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'F');
            })) return {};
        for (std::size_t i = 0; i < result.size(); ++i)
        {
            const auto* start = input.data() + i * 8;
            const auto converted = std::from_chars(start, start + 8, result[i], 16);
            if (converted.ec != std::errc{} || converted.ptr != start + 8) return {};
        }
        return result;
    }

    std::string guid_text(const Guid& id) { return std::format("{:08X}{:08X}{:08X}{:08X}", id[0], id[1], id[2], id[3]); }

    int reply(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    struct DestroyValue
    {
        U::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, DestroyValue>;

    Value copy(U::FProperty* property, const void* source)
    {
        Value result(property->AllocateAndInitializeValue(), {property});
        if (result) property->CopyCompleteValue(result.get(), source);
        return result;
    }

    U::UWorld* current_campaign_world(Address pawn_address, U::UClass* pawn_class)
    {
        if (!pawn_class) return nullptr;
        U::UWorld* world{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
            if (exact(candidate, pawn_class)) world = static_cast<U::AActor*>(candidate)->GetWorld();
            return RC::LoopAction::Break;
        });
        if (!exact(world, find<U::UClass>(STR("/Script/Engine.World")))) return nullptr;
        const auto path = world->GetPathName();
        constexpr std::wstring_view prefix{STR("/Game/Classes/Maps/")};
        const auto separator = path.find('.');
        if (!path.starts_with(prefix) || separator == std::wstring::npos) return nullptr;
        const auto leaf = path.rfind('/', separator);
        const std::wstring_view name{path};
        if (leaf == std::wstring::npos || separator == leaf + 1 ||
            name.substr(leaf + 1, separator - leaf - 1) != name.substr(separator + 1)) return nullptr;
        return find<U::UWorld>(path.c_str()) == world ? world : nullptr;
    }

    enum class PartyWorld { borough, scientist, black_market, current_campaign };

    struct Party
    {
        U::UWorld* world{};
        U::UObject* driver{};
        U::UObject* instance{};
        U::UObject* host{};
        U::UObject* stock{};
        U::UObject* target{};
        U::UObject* target_seat{};
        U::UObject* detached{};
        std::array<U::UObject*, max_guests> other{};

        bool read(Address pawn_address, Address save_address, Address stock_address, bool guest,
                  PartyWorld location = PartyWorld::borough)
        {
            auto* pawn_class = find<U::UClass>(pawn_path);
            const auto* world_path = location == PartyWorld::scientist ? scientist_world_path :
                location == PartyWorld::black_market ? black_market_world_path : borough_world_path;
            world = location == PartyWorld::current_campaign ? current_campaign_world(pawn_address, pawn_class) :
                find<U::UWorld>(world_path);
            if (!live(world)) return false;
            auto* save_class = find<U::UClass>(save_path);
            auto* controller_class = find<U::UClass>(controller_path);
            auto* local_class = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
            auto* connection_class = find<U::UClass>(STR("/Script/Engine.NetConnection"));
            instance = object(world, STR("OwningGameInstance"));
            driver = object(world, STR("NetDriver"));
            if (!exact(instance, find<U::UClass>(instance_path)) || !live(driver) || object(driver, STR("World")) != world ||
                !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
                !pawn_class || !save_class || !controller_class || !local_class || !connection_class) return false;
            stock = object(instance, STR("SaveSlot_Ref"));
            if (!exact(stock, save_class) || address(stock) != stock_address) return false;

            auto* locals = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
            auto* local_item = locals ? U::CastField<U::FObjectProperty>(locals->GetInner()) : nullptr;
            auto* clients = field<U::FArrayProperty>(driver, STR("ClientConnections"));
            auto* client_item = clients ? U::CastField<U::FObjectProperty>(clients->GetInner()) : nullptr;
            if (!local_item || !client_item) return false;
            U::FScriptArrayHelper local_rows(locals, locals->ContainerPtrToValuePtr<void>(instance));
            U::FScriptArrayHelper client_rows(clients, clients->ContainerPtrToValuePtr<void>(driver));
            if (local_rows.Num() != 1 || client_rows.Num() > max_guests || (guest && client_rows.Num() < 1)) return false;

            const auto controlled = [&](U::UObject* seat) -> U::UObject* {
                auto* controller = object(seat, STR("PlayerController"));
                auto* pawn = object(controller, STR("Pawn"));
                return exact(controller, controller_class) && authority(controller, world) && object(controller, STR("Player")) == seat &&
                    exact(pawn, pawn_class) && authority(pawn, world) && object(pawn, STR("Controller")) == controller &&
                    object(pawn, STR("As Game Instance FP")) == instance ? pawn : nullptr;
            };
            auto* local = local_item->GetObjectPropertyValue(local_rows.GetRawPtr());
            if (!live(local) || !local->IsA(local_class)) return false;
            host = controlled(local);
            if (!host || object(host, STR("Save")) != stock) return false;
            if (!guest && address(host) == pawn_address && address(stock) == save_address)
            {
                target = host;
                target_seat = local;
                detached = stock;
            }

            std::array<U::UObject*, max_guests> seen_connections{};
            std::array<U::UObject*, max_guests> guest_saves{};
            for (int index = 0; index < client_rows.Num(); ++index)
            {
                auto* connection = client_item->GetObjectPropertyValue(client_rows.GetRawPtr(index));
                if (!live(connection) || !connection->IsA(connection_class) || object(connection, STR("Driver")) != driver ||
                    object(connection, STR("OwningActor")) != object(connection, STR("PlayerController")) ||
                    std::ranges::find(seen_connections, connection) != seen_connections.end()) return false;
                seen_connections[static_cast<std::size_t>(index)] = connection;
                auto* pawn = controlled(connection);
                if (!pawn || pawn == host) return false;
                auto* save = object(pawn, STR("Save"));
                if (!exact(save, save_class)) return false;
                guest_saves[static_cast<std::size_t>(index)] = save;
                if (guest && address(pawn) == pawn_address && address(save) == save_address && save != stock)
                {
                    target = pawn;
                    target_seat = connection;
                    detached = save;
                }
            }
            if (!target || !target_seat || !detached || (guest && std::ranges::count(guest_saves, detached) != 1))
                return false;
            std::size_t pos = 0;
            for (auto* save : guest_saves)
            {
                if (!save || save == stock || save == detached) continue;
                if (std::ranges::find(other, save) == other.end()) other[pos++] = save;
            }
            return true;
        }

        bool same_identity(const Party& second) const
        {
            return world == second.world && driver == second.driver && instance == second.instance && host == second.host &&
                stock == second.stock && target == second.target && target_seat == second.target_seat &&
                detached == second.detached && other == second.other;
        }
    };

    enum class WirePhase { active, complete };

    bool objective_recipe(U::UObject* stock, const U::TCHAR* quest, U::UClass* wanted, int expected_cash,
                          U::UClass* weapon, U::UClass* ammo, WirePhase phase, int expected_final_step = 2)
    {
        auto* type = find<U::UScriptStruct>(quest_path);
        auto* map = field<U::FMapProperty>(stock, STR("ActiveQuests"));
        auto* key = map ? U::CastField<U::FNameProperty>(map->GetKeyProp()) : nullptr;
        auto* row = map ? U::CastField<U::FStructProperty>(map->GetValueProp()) : nullptr;
        auto* step = field<U::FIntProperty>(type, STR("Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"));
        auto* final_step = field<U::FIntProperty>(type, STR("Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"));
        auto* objectives = field<U::FArrayProperty>(type, STR("Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"));
        auto* objective = objectives ? U::CastField<U::FClassProperty>(objectives->GetInner()) : nullptr;
        auto* consumed = field<U::FBoolProperty>(type, STR("ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333"));
        auto* items = field<U::FArrayProperty>(type, STR("Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"));
        auto* item = items ? U::CastField<U::FClassProperty>(items->GetInner()) : nullptr;
        auto* xp = field<U::FIntProperty>(type, STR("Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"));
        auto* cash = field<U::FIntProperty>(type, STR("Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"));
        const U::FName id{quest, U::FNAME_Find};
        if (!type || !key || !row || row->GetStruct().Get() != type || key->GetOffset_Internal() != 0 ||
            !step || !final_step || !objective || (!wanted && expected_final_step != 1) || !consumed || !item || !xp || !cash ||
            id.IsNone() || id.ToString() != quest) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), row->GetSize(), row->GetMinAlignment());
        if (row->GetOffset_Internal() != layout.ValueOffset) return false;
        const auto* quests = map->ContainerPtrToValuePtr<U::FScriptMap>(stock);
        if (quests->Num() < 1 || quests->Num() > quests->GetMaxIndex() || quests->GetMaxIndex() > max_inventory) return false;
        const void* found{};
        for (int index = 0; index < quests->GetMaxIndex(); ++index)
        {
            if (!quests->IsValidIndex(index)) continue;
            const auto* entry = quests->GetData(index, layout);
            if (!entry) return false;
            if (key->GetPropertyValue(entry) != id) continue;
            if (found) return false;
            found = row->ContainerPtrToValuePtr<void>(entry);
        }
        if (!found || final_step->GetPropertyValueInContainer(found) != expected_final_step ||
            consumed->GetPropertyValueInContainer(found) != (wanted != nullptr) ||
            xp->GetPropertyValueInContainer(found) != 1 || cash->GetPropertyValueInContainer(found) != expected_cash) return false;
        const auto current_step = step->GetPropertyValueInContainer(found);
        if (phase == WirePhase::complete ? current_step != -10 : current_step < 1 || current_step > expected_final_step)
            return false;
        U::FScriptArrayHelper requested(objectives, objectives->ContainerPtrToValuePtr<void>(found));
        if (requested.Num() != (wanted ? 1 : 0) ||
            (wanted && objective->GetObjectPropertyValue(requested.GetRawPtr()) != wanted)) return false;
        U::FScriptArrayHelper rewards(items, items->ContainerPtrToValuePtr<void>(found));
        if (!weapon && !ammo) return rewards.Num() == 0;
        return weapon && ammo && rewards.Num() == 2 && item->GetObjectPropertyValue(rewards.GetRawPtr(0)) == weapon &&
            item->GetObjectPropertyValue(rewards.GetRawPtr(1)) == ammo;
    }

    bool wire_recipe(U::UObject* stock, U::UClass* weapon, U::UClass* ammo, WirePhase phase)
    {
        return weapon && ammo && objective_recipe(stock, STR("Clt_Wire"), find<U::UClass>(wire_path), 0, weapon, ammo, phase);
    }

    struct Inventory
    {
        const RewardItems& reward_items;
        explicit Inventory(const RewardItems& rewards = copper_stop_items) : reward_items(rewards) {}

        U::UClass* save_class = find<U::UClass>(save_path);
        U::UClass* base_class = find<U::UClass>(item_base_path);
        U::UClass* weapon_class = find<U::UClass>(reward_items.weapon_path);
        U::UClass* ammo_class = find<U::UClass>(reward_items.ammo_path);
        U::UScriptStruct* type = find<U::UScriptStruct>(item_path);
        U::FArrayProperty* backpack = field<U::FArrayProperty>(save_class, STR("PlayerBackpack"));
        U::FArrayProperty* equipment = field<U::FArrayProperty>(save_class, STR("PlayerEquipment"));
        U::FStructProperty* armor = field<U::FStructProperty>(save_class, STR("PlayerArmor"));
        U::FStructProperty* helmet = field<U::FStructProperty>(save_class, STR("PlayerHelmet"));
        U::FStructProperty* row = backpack ? U::CastField<U::FStructProperty>(backpack->GetInner()) : nullptr;
        U::FClassProperty* item = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FIntProperty* ammo = field<U::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        U::FDoubleProperty* weight = field<U::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        U::FIntProperty* value = field<U::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        U::FNameProperty* tag = field<U::FNameProperty>(type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        U::FStructProperty* data = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        U::FBoolProperty* never_drop = field<U::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        U::FStructProperty* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<U::FIntProperty*, 4> words{};
        U::UObject* ammo_defaults{};
        int weapon_capacity{};

        bool same_type(U::FProperty* property) const
        {
            auto* structure = U::CastField<U::FStructProperty>(property);
            return structure && structure->GetArrayDim() == 1 && structure->GetStruct().Get() == type;
        }

        bool bind(bool reward_defaults = true)
        {
            auto* guid_type = find<U::UScriptStruct>(guid_path);
            auto* payload_type = find<U::UScriptStruct>(payload_path);
            if (!save_class || !base_class || !type || !equipment || !same_type(row) ||
                !same_type(equipment->GetInner()) || !same_type(armor) || !same_type(helmet) || !item ||
                item->GetMetaClass().Get() != base_class || !ammo || !weight || !value || !tag || !data || !payload_type ||
                data->GetStruct().Get() != payload_type || !never_drop || !guid || !guid_type ||
                guid->GetStruct().Get() != guid_type || guid->GetSize() != sizeof(Guid)) return false;
            for (std::size_t i = 0; i < words.size(); ++i)
            {
                words[i] = field<U::FIntProperty>(guid_type, guid_names[i]);
                if (!words[i] || words[i]->GetOffset_Internal() != static_cast<int>(i * sizeof(std::uint32_t))) return false;
            }
            if (!reward_defaults) return true;
            if (!weapon_class || !weapon_class->IsChildOf(base_class) || !ammo_class || !ammo_class->IsChildOf(base_class)) return false;
            ammo_defaults = ammo_class->GetClassDefaultObject().Get();
            auto* weapon_defaults = weapon_class->GetClassDefaultObject().Get();
            auto* count = field<U::FIntProperty>(ammo_defaults, STR("Ammo"));
            auto* unit_value = field<U::FIntProperty>(ammo_defaults, STR("Value"));
            auto* unit_weight = field<U::FDoubleProperty>(ammo_defaults, STR("Weight"));
            auto* capacity = field<U::FIntProperty>(weapon_defaults, STR("MagSize"));
            if (!ammo_defaults || !ammo_defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
                !weapon_defaults || !weapon_defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
                !bool_value(ammo_defaults, STR("Stacks"), true) || !bool_value(weapon_defaults, STR("Stacks"), false) ||
                !count || count->GetPropertyValueInContainer(ammo_defaults) != reward_items.units ||
                !unit_value || unit_value->GetPropertyValueInContainer(ammo_defaults) != reward_items.unit_value ||
                !unit_weight || unit_weight->GetPropertyValueInContainer(ammo_defaults) != reward_items.unit_weight || !capacity)
                return false;
            weapon_capacity = capacity->GetPropertyValueInContainer(weapon_defaults);
            return weapon_capacity > 0 && weapon_capacity <= 256;
        }

        Guid identity(const void* input) const
        {
            const auto* parts = guid->ContainerPtrToValuePtr<void>(input);
            Guid result{};
            for (std::size_t i = 0; i < result.size(); ++i)
                result[i] = static_cast<std::uint32_t>(words[i]->GetPropertyValueInContainer(parts));
            return result;
        }

        void set_identity(void* output, const Guid& id) const
        {
            auto* parts = guid->ContainerPtrToValuePtr<void>(output);
            for (std::size_t i = 0; i < id.size(); ++i)
                words[i]->SetPropertyValueInContainer(parts, static_cast<std::int32_t>(id[i]));
        }

        bool identical(const void* first, const void* second) const
        {
            return row->Identical(first, second) &&
                data->Identical(data->ContainerPtrToValuePtr<void>(first), data->ContainerPtrToValuePtr<void>(second));
        }

        U::UObject* klass(const void* input) const
        {
            return item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(input));
        }

        bool valid_weapon(const void* input) const
        {
            if (!input || klass(input) != weapon_class || identity(input) == Guid{}) return false;
            const int rounds = ammo->GetPropertyValueInContainer(input);
            const double kilos = weight->GetPropertyValueInContainer(input);
            return rounds >= 0 && rounds <= weapon_capacity && std::isfinite(kilos) && kilos >= 0.0 &&
                value->GetPropertyValueInContainer(input) >= 0;
        }

        bool valid_ammo(const void* input) const
        {
            if (!input || klass(input) != ammo_class || identity(input) == Guid{}) return false;
            auto* original_tag = field<U::FNameProperty>(ammo_defaults, STR("DataTag"));
            auto* original_data = field<U::FStructProperty>(ammo_defaults, STR("DataStruct"));
            auto* important = field<U::FBoolProperty>(ammo_defaults, STR("NeverDrop"));
            return ammo->GetPropertyValueInContainer(input) == reward_items.units &&
                std::abs(weight->GetPropertyValueInContainer(input) - reward_items.units * reward_items.unit_weight) < 1e-9 &&
                value->GetPropertyValueInContainer(input) == reward_items.units * reward_items.unit_value && original_tag &&
                tag->GetPropertyValueInContainer(input) == original_tag->GetPropertyValueInContainer(ammo_defaults) &&
                original_data && original_data->GetStruct().Get() == data->GetStruct().Get() &&
                data->Identical(data->ContainerPtrToValuePtr<void>(input), original_data->ContainerPtrToValuePtr<void>(ammo_defaults)) &&
                important && never_drop->GetPropertyValueInContainer(input) == important->GetPropertyValueInContainer(ammo_defaults);
        }

        bool same_as_defaults(U::UObject* save) const
        {
            auto* defaults = save_class->GetClassDefaultObject().Get();
            if (!defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject)) return false;
            for (auto* part : std::array<U::FProperty*, 3>{equipment, armor, helmet})
                if (!part->Identical_InContainer(save, defaults)) return false;
            return true;
        }

        struct Match { const void* row{}; int count{}; bool hand_item{}; int backpack_index = -1; };

        template <typename Visit>
        bool visit(U::UObject* save, Visit&& visit_row) const
        {
            for (auto* part : {backpack, equipment})
            {
                U::FScriptArrayHelper rows(part, part->ContainerPtrToValuePtr<void>(save));
                if (rows.Num() < 0 || rows.Num() > max_inventory) return false;
                for (int i = 0; i < rows.Num(); ++i) visit_row(rows.GetRawPtr(i), true, part == backpack ? i : -1);
            }
            for (auto* part : {armor, helmet}) visit_row(part->ContainerPtrToValuePtr<void>(save), false, -1);
            return true;
        }

        bool locate(U::UObject* save, const Guid& id, Match& found) const
        {
            return visit(save, [&](const void* row, bool hand_item, int backpack_index) {
                if (identity(row) != id) return;
                found.row = row;
                found.hand_item = hand_item;
                found.backpack_index = backpack_index;
                ++found.count;
            });
        }
    };

    U::UObject* isolated_save(Address expected, const Inventory& inventory)
    {
        auto* instance = find<U::UClass>(instance_path);
        auto* pawn = find<U::UClass>(pawn_path);
        auto* canonical = field<U::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* attached = field<U::FObjectProperty>(pawn, STR("Save"));
        if (!canonical || !attached || canonical->GetPropertyClass().Get() != inventory.save_class ||
            attached->GetPropertyClass().Get() != inventory.save_class) return nullptr;
        U::UObject* result{};
        bool occupied = false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* current, int, int) {
            if (!live(current)) return RC::LoopAction::Continue;
            auto* type = current->GetClassPrivate();
            auto* owner = type == instance ? canonical : type == pawn ? attached : nullptr;
            if (owner && address(owner->GetObjectPropertyValue(owner->ContainerPtrToValuePtr<void>(current))) == expected)
            {
                occupied = true;
                return RC::LoopAction::Break;
            }
            if (type == inventory.save_class && address(current) == expected) result = current;
            return RC::LoopAction::Continue;
        });
        return !occupied && result && result->GetOuterPrivate() &&
            result->GetOuterPrivate()->GetPathName() == STR("/Engine/Transient") ? result : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool output)
    {
        auto* property = field<T>(function, name);
        return property && property->GetOffset_Internal() == offset && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == output ? property : nullptr;
    }

    Value make_ammo(const Inventory& inventory, U::UObject* host)
    {
        auto* library = find<U::UObject>(inventory_library);
        auto* function = find<U::UFunction>(make_stack_path);
        auto* item = parameter<U::FClassProperty>(function, STR("Item_Class"), 0, false);
        auto* units = parameter<U::FIntProperty>(function, STR("Count"), 8, false);
        auto* context = parameter<U::FObjectProperty>(function, STR("__WorldContext"), 16, false);
        auto* out = parameter<U::FStructProperty>(function, STR("Item"), 24, true);
        constexpr std::size_t capacity = 256;
        if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !item || item->GetMetaClass().Get() != inventory.base_class || !units || !context ||
            !inventory.same_type(out) || out->GetOffset_Internal() + out->GetSize() > function->GetParmsSize() ||
            function->GetParmsSize() > capacity) return {};

        alignas(std::max_align_t) std::array<std::byte, capacity> parameters{};
        auto* container = parameters.data();
        out->InitializeValue_InContainer(container);
        const auto cleanup = [out](std::byte* memory) { out->DestroyValue_InContainer(memory); };
        std::unique_ptr<std::byte, decltype(cleanup)> guard(container, cleanup);
        item->SetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(container), inventory.ammo_class);
        units->SetPropertyValueInContainer(container, inventory.reward_items.units);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(container), host);
        library->ProcessEvent(function, container);
        const void* output = out->ContainerPtrToValuePtr<void>(container);
        return inventory.valid_ammo(output) ? copy(inventory.row, output) : Value{};
    }

    struct ProtectedInventory
    {
        U::UObject* save{};
        std::array<U::FProperty*, 4> parts{};
        std::array<Value, 4> before;

        bool capture(const Inventory& inventory, U::UObject* source)
        {
            save = source;
            parts = {inventory.backpack, inventory.equipment, inventory.armor, inventory.helmet};
            for (std::size_t i = 0; i < parts.size(); ++i)
            {
                before[i] = copy(parts[i], parts[i]->ContainerPtrToValuePtr<void>(save));
                if (!before[i]) return false;
            }
            return true;
        }

        bool unchanged(std::size_t start = 0) const
        {
            for (std::size_t i = start; i < parts.size(); ++i)
                if (!parts[i]->Identical(parts[i]->ContainerPtrToValuePtr<void>(save), before[i].get())) return false;
            return true;
        }
    };

    struct AppendPair
    {
        U::FScriptArrayHelper& rows;
        int original = rows.Num();
        bool committed{};

        ~AppendPair()
        {
            if (!committed && rows.Num() > original) rows.RemoveValues(original, rows.Num() - original);
        }

        bool append(const Inventory& inventory, const void* weapon, const void* ammo)
        {
            const auto added = rows.AddValues(2);
            if (added != original || rows.Num() != original + 2) return false;
            inventory.row->CopyCompleteValue(rows.GetRawPtr(original), weapon);
            inventory.row->CopyCompleteValue(rows.GetRawPtr(original + 1), ammo);
            return inventory.identical(rows.GetRawPtr(original), weapon) && inventory.identical(rows.GetRawPtr(original + 1), ammo);
        }
    };

    struct Template
    {
        const void* weapon{};
        const void* ammo{};
        Guid weapon_id{}, ammo_id{};
        int rounds{};

        bool read(const Inventory& inventory, U::UObject* escrow)
        {
            if (!escrow || !inventory.same_as_defaults(escrow)) return false;
            U::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
            if (rows.Num() != 2) return false;
            weapon = rows.GetRawPtr(0);
            ammo = rows.GetRawPtr(1);
            if (!inventory.valid_weapon(weapon) || !inventory.valid_ammo(ammo)) return false;
            weapon_id = inventory.identity(weapon);
            ammo_id = inventory.identity(ammo);
            rounds = inventory.ammo->GetPropertyValueInContainer(weapon);
            return weapon_id != ammo_id;
        }

        int response(const Lua& lua, std::string_view status, int units) const
        {
            reply(lua, true, status);
            lua.set_string(guid_text(weapon_id));
            lua.set_string(guid_text(ammo_id));
            lua.set_integer(rounds);
            lua.set_integer(units);
            return 6;
        }
    };

    bool available(const Lua& lua, int count)
    {
        return enabled() && U::IsInGameThreadRaw() && lua.get_stack_size() == count;
    }

    template <bool ComputerBoard>
    int stage_objective(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) {
            if constexpr (ComputerBoard)
            {
                const auto wire = reason.find("wire_objective");
                if (wire != std::string_view::npos)
                    return reply(lua, false, std::format("{}computer_board_{}", reason.substr(0, wire), reason.substr(wire + 5)));
            }
            return reply(lua, false, reason);
        };
        constexpr auto location = ComputerBoard ? PartyWorld::scientist : PartyWorld::borough;
        std::array<Address, 3> args{};
        if (!available(lua, 4) || !lua.is_string(4) || !read_addresses(lua, args)) return fail("wire_objective_stage_unavailable");
        const auto objective_id = read_guid(lua.get_string());
        if (objective_id == Guid{}) return fail("wire_objective_guid_required");
        Party party;
        Inventory inventory;
        if (!party.read(args[0], args[1], args[2], true, location)) return fail("wire_objective_exact_detached_guest_required");
        auto* wanted = find<U::UClass>(ComputerBoard ? board_item_path : wire_path);
        const auto matches_recipe = [&] {
            if constexpr (ComputerBoard)
                return objective_recipe(party.stock, STR("Clt_ComputerBoard"), wanted, 800, nullptr, nullptr, WirePhase::active);
            return wire_recipe(party.stock, inventory.weapon_class, inventory.ammo_class, WirePhase::active);
        };
        if (!inventory.bind(!ComputerBoard) || !matches_recipe())
            return fail("wire_objective_active_recipe_required");
        auto* defaults = wanted ? wanted->GetClassDefaultObject().Get() : nullptr;
        if (!wanted || !wanted->IsChildOf(inventory.base_class) || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), false)) return fail("wire_objective_nonstackable_stock_class_required");
        const auto is_objective = [&](const void* row) { return inventory.klass(row) == wanted; };

        Inventory::Match source;
        if (!inventory.locate(party.detached, objective_id, source) || source.count != 1 || source.backpack_index < 0 ||
            !is_objective(source.row)) return fail("wire_objective_exact_guest_backpack_item_required");
        const auto ammo = inventory.ammo->GetPropertyValueInContainer(source.row);
        if (ammo < 0 || ammo > 1) return fail("wire_objective_single_item_required");
        bool host_conflict = false;
        if (!inventory.visit(party.stock, [&](const void* row, bool, int) {
                host_conflict |= is_objective(row) || inventory.identity(row) == objective_id;
            })) return fail("wire_objective_host_inventory_unqualified");
        if (host_conflict) return fail("wire_objective_canonical_item_conflict");

        auto* from = inventory.backpack->ContainerPtrToValuePtr<void>(party.detached);
        auto* to = inventory.backpack->ContainerPtrToValuePtr<void>(party.stock);
        U::FScriptArrayHelper from_rows(inventory.backpack, from);
        U::FScriptArrayHelper to_rows(inventory.backpack, to);
        if (to_rows.Num() >= max_inventory) return fail("wire_objective_canonical_inventory_full");
        auto* from_buffer = from_rows.GetRawPtr();
        auto* to_buffer = to_rows.Num() ? to_rows.GetRawPtr() : nullptr;
        std::array<U::UObject*, max_guests + 2> owners{party.detached, party.stock};
        std::ranges::copy(party.other, owners.begin() + 2);
        std::array<ProtectedInventory, max_guests + 2> inventories;
        for (std::size_t i = 0; i < owners.size() && owners[i]; ++i)
        {
            auto* owner = owners[i];
            if (i >= 2)
            {
                Inventory::Match other;
                if (!inventory.locate(owner, objective_id, other) || other.count) return fail("wire_objective_other_owner_guid_conflict");
            }
            for (auto* part : {inventory.backpack, inventory.equipment})
            {
                auto* data = part->ContainerPtrToValuePtr<void>(owner);
                U::FScriptArrayHelper rows(part, data);
                auto* buffer = rows.Num() ? rows.GetRawPtr() : nullptr;
                if ((i != 0 || part != inventory.backpack) && (data == from || (from_buffer && from_buffer == buffer)))
                    return fail("wire_objective_inventory_alias");
                if ((i != 1 || part != inventory.backpack) && (data == to || (to_buffer && to_buffer == buffer)))
                    return fail("wire_objective_inventory_alias");
            }
            if (!inventories[i].capture(inventory, owner)) return fail("wire_objective_inventory_snapshot_unavailable");
        }

        auto prepared_from = copy(inventory.backpack, inventories[0].before[0].get());
        auto prepared_to = copy(inventory.backpack, inventories[1].before[0].get());
        if (!prepared_from || !prepared_to) return fail("wire_objective_transfer_preparation_unavailable");
        U::FScriptArrayHelper planned_from(inventory.backpack, prepared_from.get());
        U::FScriptArrayHelper planned_to(inventory.backpack, prepared_to.get());
        const auto new_index = planned_to.AddValues(1);
        if (new_index != to_rows.Num() || planned_to.Num() != to_rows.Num() + 1)
            return fail("wire_objective_prepared_append_unverified");
        inventory.row->CopyCompleteValue(planned_to.GetRawPtr(new_index), source.row);
        if (!inventory.identical(planned_to.GetRawPtr(new_index), source.row)) return fail("wire_objective_typed_copy_unverified");
        planned_from.RemoveValues(source.backpack_index, 1);
        if (planned_from.Num() != from_rows.Num() - 1) return fail("wire_objective_prepared_debit_unverified");
        for (int i = 0; i < planned_from.Num(); ++i)
            if (!inventory.identical(planned_from.GetRawPtr(i), from_rows.GetRawPtr(i + (i >= source.backpack_index))))
                return fail("wire_objective_prepared_guest_row_changed");
        for (int i = 0; i < new_index; ++i)
            if (!inventory.identical(planned_to.GetRawPtr(i), to_rows.GetRawPtr(i))) return fail("wire_objective_prepared_host_row_changed");

        const auto protected_unchanged = [&](std::size_t first_parts) {
            for (std::size_t i = 0; i < owners.size() && owners[i]; ++i)
                if (!inventories[i].unchanged(i < 2 ? first_parts : 0)) return false;
            return true;
        };
        const auto same_party_and_recipe = [&] {
            Party current;
            return current.read(args[0], args[1], args[2], true, location) && party.same_identity(current) && matches_recipe();
        };
        if (!same_party_and_recipe() || !protected_unchanged(0)) return fail("wire_objective_authority_or_inventory_changed");

        inventory.backpack->CopyCompleteValue(from, prepared_from.get());
        inventory.backpack->CopyCompleteValue(to, prepared_to.get());
        const auto verified = [&](const void* data, const Value& expected) {
            return inventory.backpack->Identical(data, expected.get());
        };
        if (!verified(from, prepared_from) || !verified(to, prepared_to) || !protected_unchanged(1) || !same_party_and_recipe())
        {
            inventory.backpack->CopyCompleteValue(to, inventories[1].before[0].get());
            inventory.backpack->CopyCompleteValue(from, inventories[0].before[0].get());
            return fail(protected_unchanged(0) ? "wire_objective_transfer_rolled_back" : "mutation_unverified_wire_objective_rollback");
        }
        return reply(lua, true, "staged");
    }

    template <bool MeetBroker>
    bool completed_reward_recipe(const Party& party, const Inventory& inventory)
    {
        if constexpr (MeetBroker)
            return inventory.weapon_class && inventory.ammo_class &&
                objective_recipe(party.stock, STR("MeetBroker"), nullptr, 0, inventory.weapon_class,
                                 inventory.ammo_class, WirePhase::complete, 1);
        return wire_recipe(party.stock, inventory.weapon_class, inventory.ammo_class, WirePhase::complete);
    }

    template <bool MeetBroker>
    int capture(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) { return reply(lua, false, reason); };
        std::array<Address, 3> args{};
        if (!available(lua, 4) || !lua.is_string(4) || !read_addresses(lua, args)) return fail("reward_capture_unavailable");
        const auto guid = read_guid(lua.get_string());
        if (guid == Guid{}) return fail("reward_stock_weapon_guid_required");
        Party party;
        Inventory inventory(MeetBroker ? meet_broker_items : copper_stop_items);
        constexpr auto location = MeetBroker ? PartyWorld::black_market : PartyWorld::borough;
        if (!party.read(args[0], args[1], args[1], false, location)) return fail("reward_canonical_host_required");
        if (!inventory.bind() || !completed_reward_recipe<MeetBroker>(party, inventory))
            return fail("reward_completed_recipe_required");
        auto* escrow = isolated_save(args[2], inventory);
        if (!escrow || !inventory.same_as_defaults(escrow)) return fail("reward_isolated_template_required");
        U::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        if (rows.Num() != 0) return fail("reward_empty_template_required");
        Inventory::Match stock_weapon;
        if (!inventory.locate(party.stock, guid, stock_weapon) || stock_weapon.count != 1 || !stock_weapon.hand_item ||
            !inventory.valid_weapon(stock_weapon.row)) return fail("reward_exact_host_weapon_required");
        ProtectedInventory stock_before;
        if (!stock_before.capture(inventory, party.stock)) return fail("reward_stock_snapshot_unavailable");
        auto weapon = copy(inventory.row, stock_weapon.row);
        auto ammo = make_ammo(inventory, party.host);
        if (!weapon || !ammo || inventory.identity(ammo.get()) == guid) return fail("reward_stock_ammo_template_unverified");
        Party current;
        if (!current.read(args[0], args[1], args[1], false, location) || !party.same_identity(current) || !stock_before.unchanged() ||
            isolated_save(args[2], inventory) != escrow) return fail("reward_host_or_template_changed");

        AppendPair pending{rows};
        Template stored;
        if (!pending.append(inventory, weapon.get(), ammo.get()) || !stored.read(inventory, escrow) ||
            stored.weapon_id != guid || stored.ammo_id != inventory.identity(ammo.get()) || !stock_before.unchanged())
            return fail("reward_escrow_template_append_unverified");
        pending.committed = true;
        return stored.response(lua, "captured", inventory.reward_items.units);
    }

    template <bool MeetBroker>
    int verify_template(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) { return reply(lua, false, reason); };
        std::array<Address, 1> args{};
        const bool compare_source = lua.get_stack_size() == 4;
        if (!available(lua, compare_source ? 4 : 3) || !lua.is_string(2) || !lua.is_string(3) ||
            (compare_source && !lua.is_integer(4)) || !read_addresses(lua, args))
            return fail("reward_verify_unavailable");
        const auto weapon = read_guid(lua.get_string());
        const auto ammo = read_guid(lua.get_string());
        if (weapon == Guid{} || ammo == Guid{} || weapon == ammo) return fail("reward_exact_template_guids_required");
        const auto source_address = compare_source ? lua.get_integer() : 0;
        if (compare_source && (source_address <= 0 || static_cast<Address>(source_address) == args[0]))
            return fail("reward_distinct_template_readback_required");
        Inventory inventory(MeetBroker ? meet_broker_items : copper_stop_items);
        if (!inventory.bind()) return fail("reward_inventory_schema_unavailable");
        Template stored;
        if (!stored.read(inventory, isolated_save(args[0], inventory)) || stored.weapon_id != weapon || stored.ammo_id != ammo)
            return fail("reward_exact_stock_template_unverified");
        if (compare_source)
        {
            Template source;
            if (!source.read(inventory, isolated_save(static_cast<Address>(source_address), inventory)) ||
                !inventory.identical(source.weapon, stored.weapon) || !inventory.identical(source.ammo, stored.ammo))
                return fail("reward_serialized_template_payload_changed");
        }
        return stored.response(lua, "verified", inventory.reward_items.units);
    }

    template <bool MeetBroker>
    int grant(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) { return reply(lua, false, reason); };
        std::array<Address, 4> args{};
        if (!available(lua, 6) || !lua.is_string(5) || !lua.is_string(6) || !read_addresses(lua, args))
            return fail("reward_grant_unavailable");
        const auto weapon = read_guid(lua.get_string());
        const auto ammo = read_guid(lua.get_string());
        if (weapon == Guid{} || ammo == Guid{} || weapon == ammo) return fail("reward_distinct_guest_guids_required");
        Party party;
        Inventory inventory(MeetBroker ? meet_broker_items : copper_stop_items);
        if (!party.read(args[0], args[1], args[2], true, PartyWorld::current_campaign))
            return fail("reward_exact_detached_guest_required");
        if (!inventory.bind() || !completed_reward_recipe<MeetBroker>(party, inventory))
            return fail("reward_completed_recipe_required");
        auto* escrow = isolated_save(args[3], inventory);
        Template stored;
        if (!stored.read(inventory, escrow)) return fail("reward_exact_stock_template_unverified");
        if (weapon == stored.weapon_id || weapon == stored.ammo_id || ammo == stored.weapon_id || ammo == stored.ammo_id)
            return fail("reward_guest_guid_matches_template");

        auto weapon_item = copy(inventory.row, stored.weapon);
        auto ammo_item = copy(inventory.row, stored.ammo);
        if (!weapon_item || !ammo_item) return fail("reward_typed_template_copy_unavailable");
        inventory.set_identity(weapon_item.get(), weapon);
        inventory.set_identity(ammo_item.get(), ammo);
        if (inventory.identity(weapon_item.get()) != weapon || inventory.identity(ammo_item.get()) != ammo)
            return fail("reward_guest_guid_remap_unverified");

        Inventory::Match prior_weapon, prior_ammo;
        if (!inventory.locate(party.detached, weapon, prior_weapon) || !inventory.locate(party.detached, ammo, prior_ammo))
            return fail("reward_guest_inventory_unqualified");

        std::array<U::UObject*, max_guests + 1> protected_saves{};
        protected_saves[0] = party.stock;
        std::ranges::copy(party.other, protected_saves.begin() + 1);
        std::array<ProtectedInventory, max_guests + 1> protected_rows;
        auto* backpack = inventory.backpack->ContainerPtrToValuePtr<void>(party.detached);
        U::FScriptArrayHelper destination(inventory.backpack, backpack);
        for (std::size_t i = 0; i < protected_saves.size() && protected_saves[i]; ++i)
        {
            auto* save = protected_saves[i];
            Inventory::Match other_weapon, other_ammo;
            if (!inventory.locate(save, weapon, other_weapon) || !inventory.locate(save, ammo, other_ammo) ||
                other_weapon.count || other_ammo.count) return fail("reward_other_owner_guid_conflict");
            auto* data = inventory.backpack->ContainerPtrToValuePtr<void>(save);
            U::FScriptArrayHelper other(inventory.backpack, data);
            if (data == backpack || (other.Num() && destination.Num() && other.GetRawPtr() == destination.GetRawPtr()))
                return fail("reward_other_owner_backpack_alias");
            if (!prior_weapon.count && !prior_ammo.count && !protected_rows[i].capture(inventory, save))
                return fail("reward_protected_inventory_snapshot_unavailable");
        }

        const auto granted = [&](std::string_view status) {
            reply(lua, true, status);
            lua.set_integer(stored.rounds);
            lua.set_integer(inventory.reward_items.units);
            return 4;
        };
        if (prior_weapon.count || prior_ammo.count)
        {
            if (prior_weapon.count == 1 && prior_ammo.count == 1 && prior_weapon.hand_item && prior_ammo.hand_item &&
                inventory.identical(prior_weapon.row, weapon_item.get()) && inventory.identical(prior_ammo.row, ammo_item.get()))
                return granted("already_granted");
            return fail("reward_guest_guid_conflict_or_partial_grant");
        }
        if (destination.Num() < 0 || destination.Num() > max_inventory - 2) return fail("reward_guest_inventory_full");
        ProtectedInventory target_before, escrow_before;
        if (!target_before.capture(inventory, party.detached) || !escrow_before.capture(inventory, escrow))
            return fail("reward_typed_inventory_snapshot_unavailable");
        U::FScriptArrayHelper escrow_rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        if (destination.Num() && destination.GetRawPtr() == escrow_rows.GetRawPtr()) return fail("reward_escrow_backpack_alias");

        const auto still_qualified = [&] {
            Party current;
            return current.read(args[0], args[1], args[2], true, PartyWorld::current_campaign) &&
                party.same_identity(current) && completed_reward_recipe<MeetBroker>(party, inventory);
        };
        if (!still_qualified()) return fail("reward_current_authority_or_recipe_changed");
        AppendPair pending{destination};
        const auto restored = [&](std::string_view reason) {
            inventory.backpack->CopyCompleteValue(backpack, target_before.before[0].get());
            pending.committed = true;
            return fail(target_before.unchanged() && escrow_before.unchanged() &&
                std::ranges::all_of(protected_rows, [](const ProtectedInventory& stock) { return !stock.save || stock.unchanged(); })
                ? reason : "mutation_unverified_wire_reward_rollback");
        };
        if (!pending.append(inventory, weapon_item.get(), ammo_item.get()) || !escrow_before.unchanged() ||
            !target_before.unchanged(1) || !still_qualified())
            return restored("reward_guest_append_rolled_back");
        U::FScriptArrayHelper old_rows(inventory.backpack, target_before.before[0].get());
        for (int i = 0; i < pending.original; ++i)
            if (!inventory.identical(old_rows.GetRawPtr(i), destination.GetRawPtr(i)))
                return restored("reward_guest_original_row_rolled_back");
        for (std::size_t i = 0; i < protected_saves.size() && protected_saves[i]; ++i)
            if (!protected_rows[i].unchanged()) return restored("reward_other_owner_inventory_changed");

        pending.committed = true;
        return granted("granted");
    }
}

void silverfish::native_quest_rewards::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishStageWireQuestObjective", stage_objective<false>);
    lua.register_function("SilverfishStageComputerBoardObjective", stage_objective<true>);
    lua.register_function("SilverfishCaptureWireRewards", capture<false>);
    lua.register_function("SilverfishVerifyWireRewards", verify_template<false>);
    lua.register_function("SilverfishGrantWireRewards", grant<false>);
    lua.register_function("SilverfishCaptureMeetBrokerRewards", capture<true>);
    lua.register_function("SilverfishVerifyMeetBrokerRewards", verify_template<true>);
    lua.register_function("SilverfishGrantMeetBrokerRewards", grant<true>);
}
