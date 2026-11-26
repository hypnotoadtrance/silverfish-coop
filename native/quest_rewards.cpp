#include "quest_rewards.hpp"
#include "content_paths.hpp"
#include "guest_item.hpp"
#include "loot_escrow.hpp"
#include "stock_wallet.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <memory>
#include <string>
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
    constexpr auto scav_bar_world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P");
    constexpr auto shopkeep_world_path = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P");
    constexpr auto observatory_world_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
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
    constexpr auto coup_report_item_path = STR("/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C");
    constexpr auto depot_documents_item_path = STR("/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C");
    constexpr auto pocket_watch_item_path = STR("/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C");
    constexpr auto savlo_ring_item_path = STR("/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C");
    constexpr auto molasses_item_path = STR("/Game/Classes/Items/Item_Molasis.Item_Molasis_C");
    constexpr auto savlo_keycard_item_path = STR("/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C");
    constexpr auto pump_tape_item_path = STR("/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C");
    constexpr auto brandy_item_path = STR("/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C");
    constexpr auto missing_contact_package = STR("/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C");
    constexpr auto dead_drop_item_path = STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C");
    constexpr auto dead_drop_quest_id = STR("DeadDrop_CrossRoads");
    constexpr auto stolen_bag_item_path = STR("/Game/Classes/Items/Key_Items/Brokers_ArtifactsBag.Brokers_ArtifactsBag_C");
    constexpr auto black_box_item_path = STR("/Game/Classes/Items/Key_Items/Item_InniRadio_01.Item_InniRadio_01_C");
    constexpr auto stone_maze_tape_path = STR("/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C");
    constexpr auto stone_maze_armor_path = STR("/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C");
    constexpr auto stone_maze_id = STR("Clt_Tape_StoneMaze_01");
    constexpr auto prototype_drill_id = STR("Clt_DrillHead");
    constexpr auto prototype_drill_item = STR("/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C");
    constexpr auto prototype_drill_armor = STR("/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C");
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
        if (!silverfish::world_path::object(std::wstring_view{path}) ||
            path == STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P")) return nullptr;
        return find<U::UWorld>(path.c_str()) == world ? world : nullptr;
    }

    enum class PartyWorld { borough, scientist, black_market, scav_bar, shopkeep, observatory, current_campaign };

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
            switch (location)
            {
            case PartyWorld::borough: world = find<U::UWorld>(borough_world_path); break;
            case PartyWorld::scientist: world = find<U::UWorld>(scientist_world_path); break;
            case PartyWorld::black_market: world = find<U::UWorld>(black_market_world_path); break;
            case PartyWorld::scav_bar: world = find<U::UWorld>(scav_bar_world_path); break;
            case PartyWorld::shopkeep: world = find<U::UWorld>(shopkeep_world_path); break;
            case PartyWorld::observatory: world = find<U::UWorld>(observatory_world_path); break;
            case PartyWorld::current_campaign: world = current_campaign_world(pawn_address, pawn_class); break;
            }
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
                          U::UClass* first_reward, U::UClass* second_reward, WirePhase phase,
                          int expected_final_step = 2, int expected_xp = 1, int exact_active_step = 0,
                          const U::TCHAR* display_alias = nullptr)
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
            !step || !final_step || !objective || !consumed || !item || !xp || !cash || id.IsNone()) return false;
        const auto display = id.ToString();
        if (display != quest && (!display_alias || display != display_alias)) return false;
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
            xp->GetPropertyValueInContainer(found) != expected_xp || cash->GetPropertyValueInContainer(found) != expected_cash) return false;
        const auto current_step = step->GetPropertyValueInContainer(found);
        if (phase == WirePhase::complete ? current_step != -10 :
            current_step < 1 || current_step > expected_final_step || (exact_active_step && current_step != exact_active_step))
            return false;
        U::FScriptArrayHelper requested(objectives, objectives->ContainerPtrToValuePtr<void>(found));
        if (requested.Num() != (wanted ? 1 : 0) ||
            (wanted && objective->GetObjectPropertyValue(requested.GetRawPtr()) != wanted)) return false;
        U::FScriptArrayHelper rewards(items, items->ContainerPtrToValuePtr<void>(found));
        if (!first_reward) return !second_reward && rewards.Num() == 0;
        return rewards.Num() == (second_reward ? 2 : 1) && item->GetObjectPropertyValue(rewards.GetRawPtr(0)) == first_reward &&
            (!second_reward || item->GetObjectPropertyValue(rewards.GetRawPtr(1)) == second_reward);
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

    struct ObjectiveQuest
    {
        std::string_view fault;
        PartyWorld world;
        const U::TCHAR* id;
        const U::TCHAR* item;
        int cash;
        const U::TCHAR* reward{};
        int xp = 1;
        bool stackable_unit{};
        const U::TCHAR* display_alias{};
    };

    constexpr ObjectiveQuest copper_stop_objective{"wire", PartyWorld::borough, STR("Clt_Wire"), wire_path, 0};
    constexpr ObjectiveQuest computer_board_objective{"computer_board", PartyWorld::scientist, STR("Clt_ComputerBoard"), board_item_path, 800};
    constexpr ObjectiveQuest coup_report_objective{"coup_report", PartyWorld::borough, STR("Clt_CoupReport_01"), coup_report_item_path, 1200};
    constexpr ObjectiveQuest depot_documents_objective{"depot_documents", PartyWorld::scav_bar, STR("DepotDocs"), depot_documents_item_path, 1300};
    constexpr ObjectiveQuest brandy_objective{"brandy", PartyWorld::black_market, STR("Clt_Brandy"), brandy_item_path, 900};
    constexpr ObjectiveQuest missing_contact_objective{
        .fault = "missing_contact", .world = PartyWorld::black_market, .id = STR("Note_Farmhouse"),
        .item = missing_contact_package, .cash = 450, .display_alias = STR("Note_FarmHouse")};
    constexpr ObjectiveQuest dead_drop_objective{"dead_drop", PartyWorld::black_market, dead_drop_quest_id,
        dead_drop_item_path, 300};
    constexpr ObjectiveQuest metro_drop_objective{"metro_dead_drop", PartyWorld::black_market, STR("DeadDrop_Metro"),
        STR("/Game/Classes/Items/Key_Items/Bag_DeadDrop_Metro.Bag_DeadDrop_Metro_C"), 600};
    constexpr std::array dead_drop_objectives{&dead_drop_objective, &metro_drop_objective};
    constexpr ObjectiveQuest stolen_bag_objective{"stolen_bag", PartyWorld::black_market, STR("Clt_StolenBag"),
        stolen_bag_item_path, 1200};
    constexpr ObjectiveQuest black_box_objective{"black_box", PartyWorld::observatory, STR("Clct_Radio_Scar"),
        black_box_item_path, 12000};
    constexpr ObjectiveQuest pocket_watch_objective{"pocket_watch", PartyWorld::scav_bar, STR("Clt_Watch"),
        pocket_watch_item_path, 1000, nullptr, 1, true};
    constexpr ObjectiveQuest savlo_ring_objective{"savlo_ring", PartyWorld::scav_bar, STR("Clct_EvilEye"),
        savlo_ring_item_path, 20000};
    constexpr ObjectiveQuest pump_room_objective{"pump_room", PartyWorld::black_market, STR("Clt_EatherPumpTape"),
        pump_tape_item_path, 3200};
    constexpr ObjectiveQuest molasses_objective{"molasses", PartyWorld::shopkeep, STR("Clt_Molasis"),
        molasses_item_path, 30};
    constexpr ObjectiveQuest savlo_keycard_objective{"savlo_keycard", PartyWorld::observatory, STR("Clct_SavloKey"),
        savlo_keycard_item_path, 50000};
    constexpr ObjectiveQuest stone_maze_objective{"stone_maze", PartyWorld::scientist, stone_maze_id,
        stone_maze_tape_path, 7500, stone_maze_armor_path, 3};
    constexpr ObjectiveQuest prototype_drill_objective{"prototype_drill", PartyWorld::scientist, prototype_drill_id,
        prototype_drill_item, 10000, prototype_drill_armor, 3};

    struct BoardDeposit
    {
        int units;
        U::FName quest;
        Guid consumed{};

        U::UObject* library{};
        U::UFunction* turn_in{};
        U::FNameProperty* quest_arg{};
        U::FObjectProperty* instance_arg{};
        U::FBoolProperty* immediate_arg{};
        U::FObjectProperty* context_arg{};
        U::FBoolProperty* success{};
        U::FObjectProperty* popup{};
        static constexpr int capacity = 64;

        bool bind()
        {
            library = find<U::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"));
            turn_in = find<U::UFunction>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C:TurnIn_Quest"));
            if (!library || !turn_in || turn_in->GetOuterPrivate() != library->GetClassPrivate() ||
                turn_in->GetNumParms() != 6 || turn_in->GetReturnProperty() || turn_in->GetParmsSize() > capacity)
                return false;
            quest_arg = field<U::FNameProperty>(turn_in, STR("Quest"));
            instance_arg = field<U::FObjectProperty>(turn_in, STR("GameInstance"));
            immediate_arg = field<U::FBoolProperty>(turn_in, STR("NoConfirmation"));
            context_arg = field<U::FObjectProperty>(turn_in, STR("__WorldContext"));
            success = field<U::FBoolProperty>(turn_in, STR("Success"));
            popup = field<U::FObjectProperty>(turn_in, STR("ConfirmationPopup"));
            const auto valid_parameter = [&](U::FProperty* property, bool out) {
                return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
                    property->HasAnyPropertyFlags(U::CPF_OutParm) == out && property->GetOffset_Internal() >= 0 &&
                    property->GetOffset_Internal() + property->GetSize() <= turn_in->GetParmsSize();
            };
            return !quest.IsNone() && valid_parameter(quest_arg, true) && quest_arg->HasAllPropertyFlags(
                       U::CPF_ConstParm | U::CPF_ReferenceParm) &&
                valid_parameter(instance_arg, false) && valid_parameter(immediate_arg, false) &&
                valid_parameter(context_arg, false) && valid_parameter(success, true) && valid_parameter(popup, true);
        }

        bool redeem(const Party& party) const
        {
            alignas(std::max_align_t) std::array<std::byte, capacity> args{};
            *quest_arg->ContainerPtrToValuePtr<U::FName>(args.data()) = quest;
            instance_arg->SetObjectPropertyValue(instance_arg->ContainerPtrToValuePtr<void>(args.data()), party.instance);
            immediate_arg->SetPropertyValueInContainer(args.data(), true);
            context_arg->SetObjectPropertyValue(context_arg->ContainerPtrToValuePtr<void>(args.data()), party.host);
            library->ProcessEvent(turn_in, args.data());
            return success->GetPropertyValueInContainer(args.data()) &&
                !popup->GetObjectPropertyValue(popup->ContainerPtrToValuePtr<void>(args.data()));
        }
    };

    bool split_row_matches(const Inventory& inventory, const void* original, const void* portion, int units, const Guid& id)
    {
        const auto weight = inventory.weight->GetPropertyValueInContainer(portion);
        const auto value = inventory.value->GetPropertyValueInContainer(portion);
        if (!std::isfinite(weight) || weight < 0 || value < 0) return false;
        auto expected = copy(inventory.row, original);
        if (!expected) return false;
        inventory.ammo->SetPropertyValueInContainer(expected.get(), units);
        inventory.weight->SetPropertyValueInContainer(expected.get(), weight);
        inventory.value->SetPropertyValueInContainer(expected.get(), value);
        inventory.set_identity(expected.get(), id);
        return inventory.identical(expected.get(), portion);
    }

    template <typename Check, typename Fail>
    int stage_objective_transfer(const Lua& lua, const Check& matches_recipe, const Fail& fail,
                                 const std::array<Address, 3>& args, PartyWorld location, Party& party,
                                 const Inventory& inventory, U::UClass* wanted, const Guid& objective_id,
                                 bool stackable_unit, bool sealed_package, BoardDeposit* deposit = nullptr)
    {
        auto* defaults = wanted ? wanted->GetClassDefaultObject().Get() : nullptr;
        if (!wanted || !wanted->IsChildOf(inventory.base_class) || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), stackable_unit))
            return fail(stackable_unit ? "wire_objective_stackable_stock_class_required" :
                "wire_objective_nonstackable_stock_class_required");
        const auto is_objective = [&](const void* row) { return inventory.klass(row) == wanted; };

        Inventory::Match source;
        if (!inventory.locate(party.detached, objective_id, source) || source.count != 1 || source.backpack_index < 0 ||
            !is_objective(source.row)) return fail("wire_objective_exact_guest_backpack_item_required");
        const auto ammo = inventory.ammo->GetPropertyValueInContainer(source.row);
        if (ammo < (stackable_unit ? 1 : 0) || (deposit ? ammo != deposit->units : ammo > 1))
            return fail(deposit ? "board_objective_quantity_changed" : "wire_objective_single_item_required");
        if (sealed_package)
        {
            std::string stock_guid;
            if (!silverfish::loot_escrow::sealed_package_guid(party.detached, stock_guid) ||
                read_guid(stock_guid) != objective_id) return fail("wire_objective_sealed_package_unqualified");
        }
        bool host_conflict = false;
        if (!inventory.visit(party.stock, [&](const void* row, bool, int backpack_index) {
                host_conflict |= inventory.identity(row) == objective_id ||
                    (is_objective(row) && (!deposit || backpack_index < 0));
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

        const bool partial = deposit && stackable_unit && ammo > 1;
        Guid consumed = objective_id;
        const void* moving = source.row;
        Value portion(nullptr, {inventory.row}), remainder(nullptr, {inventory.row});
        if (partial)
        {
            U::FScriptArrayHelper saved_rows(inventory.backpack, inventories[0].before[0].get());
            const void* snapshot = saved_rows.GetRawPtr(source.backpack_index);
            portion.reset(inventory.row->AllocateAndInitializeValue());
            remainder.reset(inventory.row->AllocateAndInitializeValue());
            if (!portion || !remainder) return fail("board_objective_split_storage_unavailable");
            const bool split = silverfish::native_guest_item::split_stock_stack(
                party.target, inventory.row, snapshot, 1, portion.get(), remainder.get());
            if (!same_party_and_recipe() || !protected_unchanged(0))
                return fail("mutation_unverified_stock_board_objective_split");
            if (!split) return fail("board_objective_stock_split_unavailable");
            consumed = inventory.identity(portion.get());
            if (consumed == Guid{} || consumed == objective_id ||
                !split_row_matches(inventory, snapshot, portion.get(), 1, consumed) ||
                !split_row_matches(inventory, snapshot, remainder.get(), ammo - 1, objective_id))
                return fail("board_objective_stock_split_unverified");
            for (auto* owner : owners)
            {
                if (!owner) break;
                Inventory::Match collision;
                if (!inventory.locate(owner, consumed, collision) || collision.count)
                    return fail("board_objective_split_guid_conflict");
            }
            moving = portion.get();
        }

        auto prepared_from = copy(inventory.backpack, inventories[0].before[0].get());
        auto prepared_to = copy(inventory.backpack, inventories[1].before[0].get());
        if (!prepared_from || !prepared_to) return fail("wire_objective_transfer_preparation_unavailable");
        U::FScriptArrayHelper planned_from(inventory.backpack, prepared_from.get());
        U::FScriptArrayHelper planned_to(inventory.backpack, prepared_to.get());
        if (deposit)
        {
            // The stock quest consumes the first matching item on the host.
            // Expose the guest's unit without exposing the host's own stacks.
            for (int i = planned_to.Num() - 1; i >= 0; --i)
                if (is_objective(planned_to.GetRawPtr(i))) planned_to.RemoveValues(i, 1);
        }
        const int host_rows = planned_to.Num();
        const auto new_index = planned_to.AddValues(1);
        if (new_index != host_rows || planned_to.Num() != host_rows + 1)
            return fail("wire_objective_prepared_append_unverified");
        inventory.row->CopyCompleteValue(planned_to.GetRawPtr(new_index), moving);
        if (!inventory.identical(planned_to.GetRawPtr(new_index), moving)) return fail("wire_objective_typed_copy_unverified");
        if (partial) inventory.row->CopyCompleteValue(planned_from.GetRawPtr(source.backpack_index), remainder.get());
        else planned_from.RemoveValues(source.backpack_index, 1);
        if (planned_from.Num() != from_rows.Num() - (partial ? 0 : 1))
            return fail("wire_objective_prepared_debit_unverified");
        for (int i = 0; i < planned_from.Num(); ++i)
        {
            const void* expected = partial && i == source.backpack_index ? remainder.get() :
                from_rows.GetRawPtr(i + (!partial && i >= source.backpack_index));
            if (!inventory.identical(planned_from.GetRawPtr(i), expected))
                return fail("wire_objective_prepared_guest_row_changed");
        }
        int retained = 0;
        for (int i = 0; i < to_rows.Num(); ++i)
        {
            const auto* row = to_rows.GetRawPtr(i);
            if (deposit && is_objective(row)) continue;
            if (retained >= host_rows || !inventory.identical(planned_to.GetRawPtr(retained++), row))
                return fail("wire_objective_prepared_host_row_changed");
        }
        if (retained != host_rows) return fail("wire_objective_prepared_host_row_changed");

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
        if (!deposit) return reply(lua, true, "staged");

        bool redeemed = false;
        bool verified_debit = false;
        {
            // The stock callback completes synchronously. Restore the host's
            // exact original pack before releasing the game-thread operation.
            const auto restore_host = [&](void* target) {
                inventory.backpack->CopyCompleteValue(target, inventories[1].before[0].get());
            };
            const std::unique_ptr<void, decltype(restore_host)> restored_pack(to, restore_host);
            try
            {
                redeemed = deposit->redeem(party);
                planned_to.RemoveValues(new_index, 1);
                verified_debit = verified(to, prepared_to) && verified(from, prepared_from);
            }
            catch (const std::exception&)
            {
                // The enclosing action quarantines an ambiguous stock redemption.
            }
        }
        Party current;
        if (!redeemed || !verified_debit || !protected_unchanged(1) || !inventories[1].unchanged() ||
            !current.read(args[0], args[1], args[2], true, location) || !party.same_identity(current))
        {
            inventory.backpack->CopyCompleteValue(from, inventories[0].before[0].get());
            return fail("mutation_unverified_stock_board_objective_redemption");
        }
        deposit->consumed = consumed;
        return reply(lua, true, "redeemed");
    }

    template <const ObjectiveQuest& Quest>
    int stage_objective(const Lua& lua)
    {
        constexpr bool copper_stop = &Quest == &copper_stop_objective;
        const auto fail = [&](std::string_view reason) {
            if constexpr (!copper_stop)
            {
                const auto wire = reason.find("wire_objective");
                if (wire != std::string_view::npos)
                    return reply(lua, false, std::format("{}{}_{}", reason.substr(0, wire), Quest.fault, reason.substr(wire + 5)));
            }
            return reply(lua, false, reason);
        };
        constexpr auto location = Quest.world;
        std::array<Address, 3> args{};
        if (!available(lua, 4) || !lua.is_string(4) || !read_addresses(lua, args)) return fail("wire_objective_stage_unavailable");
        const auto objective_id = read_guid(lua.get_string());
        if (objective_id == Guid{}) return fail("wire_objective_guid_required");
        Party party;
        Inventory inventory;
        if (!party.read(args[0], args[1], args[2], true, location)) return fail("wire_objective_exact_detached_guest_required");
        auto* wanted = find<U::UClass>(Quest.item);
        const auto matches_recipe = [&] {
            if constexpr (copper_stop)
                return wire_recipe(party.stock, inventory.weapon_class, inventory.ammo_class, WirePhase::active);
            auto* reward = Quest.reward ? find<U::UClass>(Quest.reward) : nullptr;
            return (!Quest.reward || reward) && objective_recipe(party.stock, Quest.id, wanted, Quest.cash, reward,
                nullptr, WirePhase::active, 2, Quest.xp, 0, Quest.display_alias);
        };
        if (!inventory.bind(copper_stop) || !matches_recipe())
            return fail("wire_objective_active_recipe_required");
        return stage_objective_transfer(lua, matches_recipe, fail, args, location, party, inventory,
            wanted, objective_id, Quest.stackable_unit, &Quest == &missing_contact_objective);
    }

    int redeem_board_objective(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) { return reply(lua, false, reason); };
        std::array<Address, 3> addresses{};
        if (!available(lua, 10) || !lua.is_string(4) || !lua.is_string(5) || !lua.is_string(6) ||
            !lua.is_integer(7) || !lua.is_integer(8) || !lua.is_integer(9) || !lua.is_integer(10) ||
            !read_addresses(lua, addresses))
            return fail("board_objective_arguments_unqualified");
        const auto objective_id = read_guid(lua.get_string());
        const std::string quest{lua.get_string()}, class_path{lua.get_string()};
        const auto final_step = lua.get_integer(), cash = lua.get_integer(), xp = lua.get_integer(), units = lua.get_integer();
        constexpr auto max_value = 2147483647;
        const auto word = [](char value) {
            return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z') ||
                (value >= '0' && value <= '9') || value == '_';
        };
        if (objective_id == Guid{} || quest.empty() || quest.size() > 96 || !std::ranges::all_of(quest, word) ||
            !silverfish::content_paths::item_class(std::string_view{class_path}) ||
            final_step < 1 || final_step > max_value || cash < 0 || cash > max_value || xp < 0 || xp > max_value ||
            units < 0 || units > max_value)
            return fail("board_objective_arguments_unqualified");

        constexpr auto location = PartyWorld::current_campaign;
        Party party;
        Inventory inventory;
        if (!party.read(addresses[0], addresses[1], addresses[2], true, location) || !inventory.bind(false))
            return fail("board_objective_exact_detached_guest_required");
        const std::wstring quest_name(quest.begin(), quest.end()), stock_class(class_path.begin(), class_path.end());
        auto* wanted = find<U::UClass>(stock_class.c_str());
        if (!wanted || !wanted->IsChildOf(inventory.base_class)) return fail("board_objective_class_unavailable");
        const auto matches_recipe = [&] {
            return objective_recipe(party.stock, quest_name.c_str(), wanted, static_cast<int>(cash),
                nullptr, nullptr, WirePhase::active, static_cast<int>(final_step), static_cast<int>(xp));
        };
        if (!matches_recipe()) return fail("board_objective_active_recipe_required");
        auto* default_item = wanted->GetClassDefaultObject().Get();
        auto* stacks = field<U::FBoolProperty>(default_item, STR("Stacks"));
        if (!stacks) return fail("board_objective_stack_layout_unqualified");
        BoardDeposit deposit{static_cast<int>(units), U::FName{quest_name.c_str(), U::FNAME_Find}};
        if (!deposit.bind()) return fail("board_objective_turnin_unavailable");
        const auto results = stage_objective_transfer(lua, matches_recipe, fail, addresses, location, party, inventory,
            wanted, objective_id, stacks->GetPropertyValueInContainer(default_item), false, &deposit);
        if (deposit.consumed == Guid{}) return results;
        lua.set_string(guid_text(deposit.consumed));
        return results + 1;
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

    struct ArmorReward
    {
        const ObjectiveQuest& quest;
        double weight;
        int value;
    };
    constexpr ArmorReward stone_maze_reward{stone_maze_objective, 2.0, 1400};
    constexpr ArmorReward prototype_drill_reward{prototype_drill_objective, 10.5, 5200};

    template <const ArmorReward& Reward>
    int armor_reply(const Lua& lua, bool ok, std::string_view status)
    {
        if constexpr (&Reward == &prototype_drill_reward)
        {
            if (status == "stone_maze_reward_exact_host_hazop_required")
                return reply(lua, ok, "prototype_drill_reward_exact_host_armor_required");
            constexpr std::string_view prior{"stone_maze_reward"};
            if (const auto prefix = status.find(prior); prefix != std::string_view::npos)
                return reply(lua, ok, std::format("{}prototype_drill_reward{}", status.substr(0, prefix),
                    status.substr(prefix + prior.size())));
        }
        return reply(lua, ok, status);
    }

    template <const ArmorReward& Reward>
    bool armor_reward_row(const Inventory& inventory, const void* row)
    {
        auto* klass = find<U::UClass>(Reward.quest.reward);
        auto* defaults = klass ? klass->GetClassDefaultObject().Get() : nullptr;
        if (!row || !klass || !klass->IsChildOf(inventory.base_class) || inventory.klass(row) != klass ||
            inventory.identity(row) == Guid{} || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), false)) return false;
        auto* kind = field<U::FByteProperty>(defaults, STR("ItemType"));
        auto* ammo = field<U::FIntProperty>(defaults, STR("Ammo"));
        auto* weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        auto* value = field<U::FIntProperty>(defaults, STR("Value"));
        auto* tag = field<U::FNameProperty>(defaults, STR("DataTag"));
        auto* payload = field<U::FStructProperty>(defaults, STR("DataStruct"));
        auto* never_drop = field<U::FBoolProperty>(defaults, STR("NeverDrop"));
        return kind && kind->GetPropertyValueInContainer(defaults) == 7 && ammo && weight && value &&
            weight->GetPropertyValueInContainer(defaults) == Reward.weight && value->GetPropertyValueInContainer(defaults) == Reward.value &&
            inventory.ammo->GetPropertyValueInContainer(row) == ammo->GetPropertyValueInContainer(defaults) &&
            inventory.weight->GetPropertyValueInContainer(row) == Reward.weight && inventory.value->GetPropertyValueInContainer(row) == Reward.value &&
            tag && inventory.tag->GetPropertyValueInContainer(row) == tag->GetPropertyValueInContainer(defaults) &&
            never_drop && inventory.never_drop->GetPropertyValueInContainer(row) == never_drop->GetPropertyValueInContainer(defaults) &&
            payload && payload->GetStruct().Get() == inventory.data->GetStruct().Get() &&
            inventory.data->Identical(inventory.data->ContainerPtrToValuePtr<void>(row), payload->ContainerPtrToValuePtr<void>(defaults));
    }

    template <const ArmorReward& Reward>
    bool armor_quest_completed(const Party& party)
    {
        auto* armor = find<U::UClass>(Reward.quest.reward);
        return armor && objective_recipe(party.stock, Reward.quest.id, find<U::UClass>(Reward.quest.item), Reward.quest.cash,
            armor, nullptr, WirePhase::complete, 2, Reward.quest.xp);
    }

    template <const ArmorReward& Reward>
    const void* armor_template(const Inventory& inventory, Address address, const Guid& guid)
    {
        auto* escrow = isolated_save(address, inventory);
        if (!escrow || !inventory.same_as_defaults(escrow)) return nullptr;
        U::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        return rows.Num() == 1 && inventory.identity(rows.GetRawPtr()) == guid && armor_reward_row<Reward>(inventory, rows.GetRawPtr()) ?
            rows.GetRawPtr() : nullptr;
    }

    template <const ArmorReward& Reward>
    struct AppendReward
    {
        U::FScriptArrayHelper& rows;
        int before = rows.Num();
        bool committed = false;

        bool append(const Inventory& inventory, const void* row)
        {
            if (before < 0 || before >= max_inventory || rows.AddValues(1) != before || rows.Num() != before + 1) return false;
            auto* added = rows.GetRawPtr(before);
            inventory.row->CopyCompleteValue(added, row);
            return inventory.identical(added, row) && armor_reward_row<Reward>(inventory, added);
        }

        ~AppendReward()
        {
            if (!committed && rows.Num() == before + 1) rows.RemoveValues(before, 1);
        }
    };

    template <const ArmorReward& Reward>
    int capture_armor_reward(const Lua& lua)
    {
        std::array<Address, 3> args{};
        if (!available(lua, 4) || !lua.is_string(4) || !read_addresses(lua, args))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_capture_unavailable");
        const auto guid = read_guid(lua.get_string());
        Party party;
        Inventory inventory;
        if (guid == Guid{} || !inventory.bind(false) || !party.read(args[0], args[1], args[1], false, PartyWorld::scientist) ||
            !armor_quest_completed<Reward>(party)) return armor_reply<Reward>(lua, false, "stone_maze_reward_completed_host_required");
        auto* escrow = isolated_save(args[2], inventory);
        Inventory::Match stock;
        if (!escrow || !inventory.same_as_defaults(escrow) || !inventory.locate(party.stock, guid, stock) ||
            stock.count != 1 || stock.backpack_index < 0 || !armor_reward_row<Reward>(inventory, stock.row))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_exact_host_hazop_required");
        U::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        if (rows.Num() != 0) return armor_reply<Reward>(lua, false, "stone_maze_reward_empty_template_required");
        ProtectedInventory protected_stock;
        auto prepared = copy(inventory.row, stock.row);
        if (!prepared || !protected_stock.capture(inventory, party.stock)) return armor_reply<Reward>(lua, false, "stone_maze_reward_snapshot_failed");
        AppendReward<Reward> guard{rows};
        if (!guard.append(inventory, prepared.get()) || !protected_stock.unchanged() || !armor_template<Reward>(inventory, args[2], guid))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_template_append_unverified");
        guard.committed = true;
        return armor_reply<Reward>(lua, true, "captured");
    }

    template <const ArmorReward& Reward>
    int verify_armor_reward(const Lua& lua)
    {
        std::array<Address, 1> args{};
        const bool compare = lua.get_stack_size() == 3;
        if (!available(lua, compare ? 3 : 2) || !lua.is_string(2) || (compare && !lua.is_integer(3)) || !read_addresses(lua, args))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_verify_unavailable");
        const auto guid = read_guid(lua.get_string());
        Inventory inventory;
        const auto* stored = guid != Guid{} && inventory.bind(false) ? armor_template<Reward>(inventory, args[0], guid) : nullptr;
        if (!stored) return armor_reply<Reward>(lua, false, "stone_maze_reward_empty_or_changed_template");
        if (compare)
        {
            const auto source = lua.get_integer();
            if (source <= 0 || static_cast<Address>(source) == args[0]) return armor_reply<Reward>(lua, false, "stone_maze_reward_distinct_source_required");
            const auto* original = armor_template<Reward>(inventory, static_cast<Address>(source), guid);
            if (!original || !inventory.identical(original, stored)) return armor_reply<Reward>(lua, false, "stone_maze_reward_payload_changed");
        }
        return armor_reply<Reward>(lua, true, "verified");
    }

    template <const ArmorReward& Reward>
    int grant_armor_reward(const Lua& lua)
    {
        std::array<Address, 4> args{};
        if (!available(lua, 6) || !lua.is_string(5) || !lua.is_string(6) || !read_addresses(lua, args))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_grant_unavailable");
        const auto source = read_guid(lua.get_string());
        const auto target = read_guid(lua.get_string());
        Party party;
        Inventory inventory;
        if (source == Guid{} || target == Guid{} || source == target || !inventory.bind(false) ||
            !party.read(args[0], args[1], args[2], true, PartyWorld::current_campaign) || !armor_quest_completed<Reward>(party))
            return armor_reply<Reward>(lua, false, "stone_maze_reward_exact_detached_guest_required");
        const auto* stored = armor_template<Reward>(inventory, args[3], source);
        if (!stored) return armor_reply<Reward>(lua, false, "stone_maze_reward_exact_template_required");
        auto prepared = copy(inventory.row, stored);
        if (!prepared) return armor_reply<Reward>(lua, false, "stone_maze_reward_copy_failed");
        inventory.set_identity(prepared.get(), target);

        std::array<U::UObject*, max_guests + 2> owners{party.detached, party.stock};
        std::ranges::copy(party.other, owners.begin() + 2);
        std::array<ProtectedInventory, max_guests + 2> snapshots;
        Inventory::Match prior;
        if (!inventory.locate(party.detached, target, prior) || prior.count > 1) return armor_reply<Reward>(lua, false, "stone_maze_reward_guest_guid_conflict");
        for (std::size_t i = 0; i < owners.size() && owners[i]; ++i)
        {
            Inventory::Match conflict;
            if ((i != 0 && (!inventory.locate(owners[i], target, conflict) || conflict.count)) || !snapshots[i].capture(inventory, owners[i]))
                return armor_reply<Reward>(lua, false, "stone_maze_reward_party_guid_or_snapshot_conflict");
        }
        if (prior.count == 1)
            return prior.backpack_index >= 0 && inventory.identical(prior.row, prepared.get()) ? armor_reply<Reward>(lua, true, "already_granted") :
                armor_reply<Reward>(lua, false, "stone_maze_reward_guest_guid_conflict");

        auto* owned = inventory.backpack->ContainerPtrToValuePtr<void>(party.detached);
        U::FScriptArrayHelper rows(inventory.backpack, owned);
        if (rows.Num() >= max_inventory) return armor_reply<Reward>(lua, false, "stone_maze_reward_guest_backpack_full");
        const auto* buffer = rows.Num() ? rows.GetRawPtr() : nullptr;
        for (std::size_t i = 0; i < owners.size() && owners[i]; ++i)
            for (auto* part : {inventory.backpack, inventory.equipment})
            {
                auto* data = part->ContainerPtrToValuePtr<void>(owners[i]);
                U::FScriptArrayHelper siblings(part, data);
                if ((i != 0 || part != inventory.backpack) && (data == owned || (buffer && siblings.Num() && siblings.GetRawPtr() == buffer)))
                    return armor_reply<Reward>(lua, false, "stone_maze_reward_party_inventory_alias");
            }

        U::FScriptArrayHelper before(inventory.backpack, snapshots[0].before[0].get());
        AppendReward<Reward> guard{rows};
        if (!guard.append(inventory, prepared.get())) return armor_reply<Reward>(lua, false, "mutation_unverified_stone_maze_reward_append");
        for (int i = 0; i < before.Num(); ++i)
            if (!inventory.identical(before.GetRawPtr(i), rows.GetRawPtr(i))) return armor_reply<Reward>(lua, false, "mutation_unverified_stone_maze_reward_inventory");
        for (std::size_t i = 0; i < owners.size() && owners[i]; ++i)
            if (!snapshots[i].unchanged(i == 0 ? 1 : 0)) return armor_reply<Reward>(lua, false, "mutation_unverified_stone_maze_reward_other_inventory");
        Party current;
        if (!current.read(args[0], args[1], args[2], true, PartyWorld::current_campaign) || !party.same_identity(current) ||
            !armor_template<Reward>(inventory, args[3], source)) return armor_reply<Reward>(lua, false, "mutation_unverified_stone_maze_reward_scope");
        guard.committed = true;
        return armor_reply<Reward>(lua, true, "granted");
    }

    bool quest_callback(U::UFunction* function)
    {
        auto* quest_id = field<U::FNameProperty>(function, STR("QuestID"));
        return quest_id && function->GetNumParms() == 1 && function->GetParmsSize() == sizeof(U::FName) &&
            function->GetReturnProperty() == nullptr && quest_id->GetOffset_Internal() == 0 &&
            quest_id->HasAnyPropertyFlags(U::CPF_Parm) && !quest_id->HasAnyPropertyFlags(U::CPF_OutParm);
    }

    int prototype_drill_board(const Lua& lua)
    {
        std::array<Address, 4> args{};
        if (!available(lua, 5) || !lua.is_string(5) || !read_addresses(lua, args))
            return reply(lua, false, "prototype_drill_board_arguments_unqualified");
        const auto action = lua.get_string();
        if (action != "check" && action != "broadcast")
            return reply(lua, false, "prototype_drill_board_action_unqualified");

        Party party;
        if (!party.read(args[0], args[1], args[1], false, PartyWorld::scientist))
            return reply(lua, false, "prototype_drill_board_authority_unqualified");
        auto* board = find<U::UObject>(STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.BountyBoard_BP_C_2"));
        auto* level = object(party.world, STR("PersistentLevel"));
        auto* script = object(level, STR("LevelScriptActor"));
        if (!exact(board, find<U::UClass>(STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C"))) ||
            address(board) != args[2] || !authority(board, party.world) || !board->HasAnyFlags(U::RF_WasLoaded) ||
            !exact(script, find<U::UClass>(STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01_C"))) ||
            address(script) != args[3] || !authority(script, party.world) ||
            object(board, STR("As Game Instance FP")) != party.instance ||
            object(script, STR("As Game Instance FP")) != party.instance ||
            !bool_value(script, STR("DrillTested"), false))
            return reply(lua, false, "prototype_drill_board_or_level_unqualified");

        auto* callback = find<U::UFunction>(STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01_C:TurnedIn_Quest"));
        auto* signature = find<U::UFunction>(STR("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:TurnedInQuest__DelegateSignature"));
        auto* property = field<U::FMulticastInlineDelegateProperty>(board, STR("TurnedInQuest"));
        if (!quest_callback(callback) || !quest_callback(signature) || !property ||
            property->GetSignatureFunction().Get() != signature || property->GetSize() != sizeof(U::FMulticastScriptDelegate))
            return reply(lua, false, "prototype_drill_board_delegate_layout_unqualified");
        const auto* delegate = property->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(board);
        const U::FName listener{STR("TurnedIn_Quest"), U::FNAME_Find};
        if (listener.IsNone() || delegate->Num() != 1 || !delegate->Contains(script, listener))
            return reply(lua, false, "prototype_drill_board_exact_listener_unqualified");

        if (!objective_recipe(party.stock, prototype_drill_id, find<U::UClass>(prototype_drill_item), 10000,
            find<U::UClass>(prototype_drill_armor), nullptr, action == "check" ? WirePhase::active : WirePhase::complete, 2, 3))
            return reply(lua, false, "prototype_drill_board_quest_phase_unqualified");
        const silverfish::stock_wallet::Wallet flags;
        if (!flags.bind(party.instance) ||
            flags.flag_value(party.instance, STR("Mosby_DrillTest")).first != (action == "check" ? 0 : 1))
            return reply(lua, false, "prototype_drill_board_test_flag_unqualified");
        if (action == "check") return reply(lua, true, "verified");

        U::FName quest{prototype_drill_id, U::FNAME_Find};
        delegate->ProcessMulticastDelegate<U::UObject>(&quest);
        if (!bool_value(script, STR("DrillTested"), true))
            return reply(lua, false, "mutation_unverified_prototype_drill_board_listener");
        return reply(lua, true, "broadcast");
    }

    constexpr std::array valve_reward_paths{
        STR("/Game/Classes/Items/Artifacts/Art_AcidResist.Art_AcidResist_C"),
        STR("/Game/Classes/Armor_Items/Helm_HazOp.Helm_HazOp_C"),
    };
    using ValveGuids = std::array<Guid, valve_reward_paths.size()>;
    using ValveRows = std::array<const void*, valve_reward_paths.size()>;

    bool valve_completed(const Party& party)
    {
        auto* artifact = find<U::UClass>(valve_reward_paths[0]);
        auto* mask = find<U::UClass>(valve_reward_paths[1]);
        return artifact && mask && objective_recipe(party.stock, STR("Sub_Valve"), nullptr, 0,
            artifact, mask, WirePhase::complete);
    }

    bool valve_reward_row(const Inventory& inventory, const void* row, std::size_t index)
    {
        auto* kind = find<U::UClass>(valve_reward_paths[index]);
        auto* defaults = kind ? kind->GetClassDefaultObject().Get() : nullptr;
        if (!row || !kind || inventory.klass(row) != kind || inventory.identity(row) == Guid{} ||
            !kind->IsChildOf(inventory.base_class) || !defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            !bool_value(defaults, STR("Stacks"), false)) return false;
        auto* category = field<U::FByteProperty>(defaults, STR("ItemType"));
        auto* ammo = field<U::FIntProperty>(defaults, STR("Ammo"));
        auto* weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
        auto* value = field<U::FIntProperty>(defaults, STR("Value"));
        auto* tag = field<U::FNameProperty>(defaults, STR("DataTag"));
        auto* payload = field<U::FStructProperty>(defaults, STR("DataStruct"));
        auto* never_drop = field<U::FBoolProperty>(defaults, STR("NeverDrop"));
        return category && category->GetPropertyValueInContainer(defaults) == (index == 0 ? 0 : 6) &&
            ammo && inventory.ammo->GetPropertyValueInContainer(row) == ammo->GetPropertyValueInContainer(defaults) &&
            weight && inventory.weight->GetPropertyValueInContainer(row) == weight->GetPropertyValueInContainer(defaults) &&
            value && value->GetPropertyValueInContainer(defaults) == (index == 0 ? 3000 : 400) &&
            inventory.value->GetPropertyValueInContainer(row) == value->GetPropertyValueInContainer(defaults) &&
            tag && inventory.tag->GetPropertyValueInContainer(row) == tag->GetPropertyValueInContainer(defaults) &&
            never_drop && inventory.never_drop->GetPropertyValueInContainer(row) == never_drop->GetPropertyValueInContainer(defaults) &&
            payload && payload->GetStruct().Get() == inventory.data->GetStruct().Get() &&
            inventory.data->Identical(inventory.data->ContainerPtrToValuePtr<void>(row), payload->ContainerPtrToValuePtr<void>(defaults));
    }

    bool read_valve_guids(const Lua& lua, ValveGuids& result)
    {
        // LuaMadeSimple consumes arguments in order. Earlier addresses are already popped.
        if (!lua.is_string(1) || !lua.is_string(2)) return false;
        for (auto& guid : result) guid = read_guid(lua.get_string());
        return result[0] != Guid{} && result[1] != Guid{} && result[0] != result[1];
    }

    bool valve_template(const Inventory& inventory, Address target, const ValveGuids& ids, ValveRows& result)
    {
        auto* escrow = isolated_save(target, inventory);
        if (!escrow || !inventory.same_as_defaults(escrow)) return false;
        U::FScriptArrayHelper rows(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(escrow));
        if (rows.Num() != static_cast<int>(ids.size())) return false;
        for (std::size_t index = 0; index < ids.size(); ++index)
        {
            result[index] = rows.GetRawPtr(static_cast<int>(index));
            if (!valve_reward_row(inventory, result[index], index) || inventory.identity(result[index]) != ids[index]) return false;
        }
        return true;
    }

    bool append_valve_rows(const Inventory& inventory, void* value, const ValveRows& items)
    {
        U::FScriptArrayHelper rows(inventory.backpack, value);
        const int before = rows.Num(), added_count = static_cast<int>(items.size());
        if (before < 0 || before > max_inventory - added_count ||
            rows.AddValues(added_count) != before || rows.Num() != before + added_count) return false;
        for (std::size_t index = 0; index < items.size(); ++index)
        {
            auto* appended = rows.GetRawPtr(before + static_cast<int>(index));
            inventory.row->CopyCompleteValue(appended, items[index]);
            if (!inventory.identical(appended, items[index]) || !valve_reward_row(inventory, appended, index)) return false;
        }
        return true;
    }

    int capture_valve_rewards(const Lua& lua)
    {
        std::array<Address, 3> args{};
        ValveGuids ids{};
        Inventory inventory;
        Party party;
        if (!available(lua, 5) || !read_addresses(lua, args) || !read_valve_guids(lua, ids))
            return reply(lua, false, "subway_valve_reward_capture_arguments_unverified");
        if (!inventory.bind(false)) return reply(lua, false, "subway_valve_reward_inventory_schema_unverified");
        if (!party.read(args[0], args[1], args[1], false, PartyWorld::borough))
            return reply(lua, false, "subway_valve_reward_exact_host_required");
        if (!valve_completed(party)) return reply(lua, false, "subway_valve_reward_completed_recipe_unverified");
        auto* escrow = isolated_save(args[2], inventory);
        if (!escrow || !inventory.same_as_defaults(escrow)) return reply(lua, false, "subway_valve_reward_exact_template_required");
        auto* target = inventory.backpack->ContainerPtrToValuePtr<void>(escrow);
        U::FScriptArrayHelper before(inventory.backpack, target);
        ValveRows source{};
        for (std::size_t index = 0; index < ids.size(); ++index)
        {
            Inventory::Match item;
            if (!inventory.locate(party.stock, ids[index], item) || item.count != 1 || item.backpack_index < 0 ||
                !valve_reward_row(inventory, item.row, index)) return reply(lua, false, "subway_valve_reward_stock_row_unqualified");
            source[index] = item.row;
        }
        auto prepared = copy(inventory.backpack, target);
        if (before.Num() != 0 || !prepared || !append_valve_rows(inventory, prepared.get(), source))
            return reply(lua, false, "subway_valve_reward_template_stage_failed");
        inventory.backpack->CopyCompleteValue(target, prepared.get());
        ValveRows copied{};
        if (!valve_template(inventory, args[2], ids, copied) || !inventory.backpack->Identical(target, prepared.get()))
            return reply(lua, false, "subway_valve_reward_capture_unverified");
        return reply(lua, true, "captured");
    }

    int verify_valve_rewards(const Lua& lua)
    {
        const bool compare = lua.get_stack_size() == 4;
        std::array<Address, 1> args{};
        ValveGuids ids{};
        ValveRows stored{}, original{};
        Inventory inventory;
        if (!available(lua, compare ? 4 : 3) || !read_addresses(lua, args) || !read_valve_guids(lua, ids))
            return reply(lua, false, "subway_valve_reward_verify_arguments_unverified");
        if (!inventory.bind(false)) return reply(lua, false, "subway_valve_reward_inventory_schema_unverified");
        if (!valve_template(inventory, args[0], ids, stored))
            return reply(lua, false, "subway_valve_reward_saved_template_unverified");
        if (compare)
        {
            if (!lua.is_integer(1)) return reply(lua, false, "subway_valve_reward_template_source_unqualified");
            const auto input = lua.get_integer();
            if (input <= 0) return reply(lua, false, "subway_valve_reward_template_source_unqualified");
            const auto source = static_cast<Address>(input);
            if (source == args[0] || !valve_template(inventory, source, ids, original))
                return reply(lua, false, "subway_valve_reward_template_source_unqualified");
            for (std::size_t index = 0; index < ids.size(); ++index)
                if (!inventory.identical(original[index], stored[index]))
                    return reply(lua, false, "subway_valve_reward_template_payload_changed");
        }
        return reply(lua, true, "verified");
    }

    int grant_valve_rewards(const Lua& lua)
    {
        std::array<Address, 4> args{};
        ValveGuids ids{}, assigned{};
        ValveRows stored{};
        Party party;
        Inventory inventory;
        if (!available(lua, 8) || !read_addresses(lua, args) || !read_valve_guids(lua, ids) ||
            !read_valve_guids(lua, assigned) || assigned[0] == ids[0] || assigned[0] == ids[1] ||
            assigned[1] == ids[0] || assigned[1] == ids[1])
            return reply(lua, false, "subway_valve_reward_grant_arguments_unverified");
        if (!inventory.bind(false)) return reply(lua, false, "subway_valve_reward_inventory_schema_unverified");
        if (!party.read(args[0], args[1], args[2], true, PartyWorld::current_campaign))
            return reply(lua, false, "subway_valve_reward_exact_detached_guest_required");
        if (!valve_completed(party)) return reply(lua, false, "subway_valve_reward_completed_recipe_unverified");
        if (!valve_template(inventory, args[3], ids, stored))
            return reply(lua, false, "subway_valve_reward_saved_template_unverified");
        std::array<Value, valve_reward_paths.size()> prepared;
        ValveRows new_rows{};
        int present = 0;
        std::array<U::UObject*, max_guests + 2> owners{party.detached, party.stock};
        std::ranges::copy(party.other, owners.begin() + 2);
        for (std::size_t index = 0; index < ids.size(); ++index)
        {
            prepared[index] = copy(inventory.row, stored[index]);
            if (!prepared[index]) return reply(lua, false, "subway_valve_reward_copy_failed");
            inventory.set_identity(prepared[index].get(), assigned[index]);
            new_rows[index] = prepared[index].get();
            for (auto* owner : owners)
            {
                if (!owner) continue;
                Inventory::Match found;
                if (!inventory.locate(owner, assigned[index], found) || found.count > 1)
                    return reply(lua, false, "subway_valve_reward_guid_conflict");
                if (!found.count) continue;
                if (owner != party.detached || found.backpack_index < 0 || !inventory.identical(found.row, new_rows[index]))
                    return reply(lua, false, "subway_valve_reward_guid_conflict");
                ++present;
            }
        }
        if (present == static_cast<int>(ids.size())) return reply(lua, true, "already_granted");
        if (present) return reply(lua, false, "subway_valve_reward_partial_bundle");

        auto* target = inventory.backpack->ContainerPtrToValuePtr<void>(party.detached);
        U::FScriptArrayHelper live_rows(inventory.backpack, target);
        const auto* buffer = live_rows.Num() ? live_rows.GetRawPtr() : nullptr;
        std::array<ProtectedInventory, max_guests + 2> snapshots;
        for (std::size_t index = 0; index < owners.size() && owners[index]; ++index)
        {
            if (!snapshots[index].capture(inventory, owners[index])) return reply(lua, false, "subway_valve_reward_snapshot_failed");
            for (auto* property : {inventory.backpack, inventory.equipment})
            {
                if (index == 0 && property == inventory.backpack) continue;
                auto* value = property->ContainerPtrToValuePtr<void>(owners[index]);
                U::FScriptArrayHelper rows(property, value);
                if (value == target || (buffer && rows.Num() && buffer == rows.GetRawPtr()))
                    return reply(lua, false, "subway_valve_reward_inventory_alias");
            }
        }
        auto proposal = copy(inventory.backpack, snapshots[0].before[0].get());
        if (!proposal || !append_valve_rows(inventory, proposal.get(), new_rows))
            return reply(lua, false, "subway_valve_reward_bundle_stage_failed");
        inventory.backpack->CopyCompleteValue(target, proposal.get());
        Party current;
        bool verified = current.read(args[0], args[1], args[2], true, PartyWorld::current_campaign) && party.same_identity(current) &&
            inventory.backpack->Identical(target, proposal.get());
        for (std::size_t index = 0; verified && index < owners.size() && owners[index]; ++index)
            verified = snapshots[index].unchanged(index == 0 ? 1 : 0);
        if (!verified)
        {
            inventory.backpack->CopyCompleteValue(target, snapshots[0].before[0].get());
            return reply(lua, false, "mutation_unverified_subway_valve_reward_delivery");
        }
        return reply(lua, true, "granted");
    }
}

bool silverfish::native_quest_rewards::active_dead_drop(U::UObject* canonical_save, U::UClass* item)
{
    if (!live(item) || !exact(canonical_save, find<U::UClass>(save_path))) return false;
    const auto path = item->GetPathName();
    const auto quest = std::ranges::find_if(dead_drop_objectives, [&](const auto* objective) { return path == objective->item; });
    return quest != dead_drop_objectives.end() &&
        objective_recipe(canonical_save, (*quest)->id, item, (*quest)->cash, nullptr, nullptr,
            WirePhase::active, 2, (*quest)->xp, 1);
}

bool silverfish::native_quest_rewards::active_missing_contact(RC::Unreal::UObject* canonical_save)
{
    constexpr auto& quest = missing_contact_objective;
    auto* item = find<U::UClass>(quest.item);
    return item && exact(canonical_save, find<U::UClass>(save_path)) &&
        objective_recipe(canonical_save, quest.id, item, quest.cash, nullptr, nullptr,
            WirePhase::active, 2, quest.xp, 1, quest.display_alias);
}

void silverfish::native_quest_rewards::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishStageWireQuestObjective", stage_objective<copper_stop_objective>);
    lua.register_function("SilverfishStageComputerBoardObjective", stage_objective<computer_board_objective>);
    lua.register_function("SilverfishStageCoupReportObjective", stage_objective<coup_report_objective>);
    lua.register_function("SilverfishStageDepotDocumentsObjective", stage_objective<depot_documents_objective>);
    lua.register_function("SilverfishStageBrandyObjective", stage_objective<brandy_objective>);
    lua.register_function("SilverfishStageMissingContactObjective", stage_objective<missing_contact_objective>);
    lua.register_function("SilverfishStageStolenBagObjective", stage_objective<stolen_bag_objective>);
    lua.register_function("SilverfishStageBlackBoxObjective", stage_objective<black_box_objective>);
    lua.register_function("SilverfishStageCrossroadsDeadDropObjective", stage_objective<dead_drop_objective>);
    lua.register_function("SilverfishStageMetroDeadDropObjective", stage_objective<metro_drop_objective>);
    lua.register_function("SilverfishStagePocketWatchObjective", stage_objective<pocket_watch_objective>);
    lua.register_function("SilverfishStageSavloRingObjective", stage_objective<savlo_ring_objective>);
    lua.register_function("SilverfishStageMolassesObjective", stage_objective<molasses_objective>);
    lua.register_function("SilverfishRedeemBoardObjective", redeem_board_objective);
    lua.register_function("SilverfishStageSavloKeycardObjective", stage_objective<savlo_keycard_objective>);
    lua.register_function("SilverfishStagePumpRoomObjective", stage_objective<pump_room_objective>);
    lua.register_function("SilverfishStageStoneMazeObjective", stage_objective<stone_maze_objective>);
    lua.register_function("SilverfishCaptureStoneMazeReward", capture_armor_reward<stone_maze_reward>);
    lua.register_function("SilverfishVerifyStoneMazeReward", verify_armor_reward<stone_maze_reward>);
    lua.register_function("SilverfishGrantStoneMazeReward", grant_armor_reward<stone_maze_reward>);
    lua.register_function("SilverfishStagePrototypeDrillObjective", stage_objective<prototype_drill_objective>);
    lua.register_function("SilverfishCapturePrototypeDrillReward", capture_armor_reward<prototype_drill_reward>);
    lua.register_function("SilverfishVerifyPrototypeDrillReward", verify_armor_reward<prototype_drill_reward>);
    lua.register_function("SilverfishGrantPrototypeDrillReward", grant_armor_reward<prototype_drill_reward>);
    lua.register_function("SilverfishPrototypeDrillBoard", prototype_drill_board);
    lua.register_function("SilverfishCaptureSubwayValveRewards", capture_valve_rewards);
    lua.register_function("SilverfishVerifySubwayValveRewards", verify_valve_rewards);
    lua.register_function("SilverfishGrantSubwayValveRewards", grant_valve_rewards);
    lua.register_function("SilverfishCaptureWireRewards", capture<false>);
    lua.register_function("SilverfishVerifyWireRewards", verify_template<false>);
    lua.register_function("SilverfishGrantWireRewards", grant<false>);
    lua.register_function("SilverfishCaptureMeetBrokerRewards", capture<true>);
    lua.register_function("SilverfishVerifyMeetBrokerRewards", verify_template<true>);
    lua.register_function("SilverfishGrantMeetBrokerRewards", grant<true>);
}
