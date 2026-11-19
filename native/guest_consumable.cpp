#include "guest_consumable.hpp"
#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto character_path = STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct");
    constexpr auto item_base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto health_item_path = STR("/Game/Classes/Items/HealthItem_Master_BP.HealthItem_Master_BP_C");
    constexpr auto bandage_path = STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C");
    constexpr auto mre_path = STR("/Game/Classes/Items/Item_MRE.Item_MRE_C");
    constexpr auto gain_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:GainHealth");
    constexpr auto boost_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:GiveRegenBoost");
    constexpr auto intox_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Apply_PlayerIntox");
    constexpr std::array guid_component{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_clients = 4;
    constexpr int max_inventory = 4096;
    constexpr int max_effects = 256;
    struct Consumable
    {
        const Unreal::TCHAR* path;
        int value;
        double weight, regen_gain, food;
    };
    constexpr Consumable bandage{bandage_path, 15, 0.05, 45.0, 0.0};
    constexpr Consumable mre{mre_path, 25, 0.65, 10.0, 30.0};

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
               size == value.size() && value[0] == '1';
    }

    bool enabled() { return opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE"); }
    bool mre_enabled() { return enabled() && opt_in("SILVERFISH_COOP_NATIVE_GUEST_MRE"); }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* property = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Unreal::UObject* read_object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* property = field<Unreal::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool read_bool(Unreal::UObject* owner, const Unreal::TCHAR* name, bool expected)
    {
        auto* property = field<Unreal::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    template <typename T>
    bool number_is(Unreal::UObject* owner, const Unreal::TCHAR* name, auto expected)
    {
        auto* property = field<T>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(Unreal::UObject* object, Unreal::UClass* type)
    {
        return type && live(object) && object->GetClassPrivate() == type;
    }

    bool authority(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        return live(actor) && number_is<Unreal::FByteProperty>(actor, STR("Role"), 3) &&
               read_bool(actor, STR("bActorIsBeingDestroyed"), false) &&
               static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    bool parse_guid(std::string_view text, Guid& guid)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char word) {
                return (word >= '0' && word <= '9') || (word >= 'A' && word <= 'F');
            })) return false;
        for (std::size_t index = 0; index < guid.size(); ++index)
        {
            const auto* begin = text.data() + index * 8;
            const auto parsed = std::from_chars(begin, begin + 8, guid[index], 16);
            if (parsed.ec != std::errc{} || parsed.ptr != begin + 8) return false;
        }
        return guid != Guid{};
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    struct Scope
    {
        Unreal::UWorld* world{};
        Unreal::UObject* pawn{};
        Unreal::UObject* guest{};
        Unreal::UObject* stock{};
        Unreal::UObject* instance{};
        Unreal::UObject* controller{};
        Unreal::UObject* connection{};

        bool current() const
        {
            if (!live(world) || !authority(pawn, world) || !authority(controller, world) ||
                !live(guest) || !live(stock) || guest == stock || !live(instance) || !live(connection) ||
                read_object(pawn, STR("Save")) != guest || read_object(world, STR("OwningGameInstance")) != instance ||
                read_object(pawn, STR("As Game Instance FP")) != instance ||
                read_object(instance, STR("SaveSlot_Ref")) != stock || read_object(pawn, STR("Controller")) != controller ||
                read_object(controller, STR("Pawn")) != pawn || read_object(controller, STR("Player")) != connection)
                return false;

            auto* driver = read_object(world, STR("NetDriver"));
            auto* clients = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
            auto* client_entry = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
            auto* locals = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
            auto* local_entry = locals ? Unreal::CastField<Unreal::FObjectProperty>(locals->GetInner()) : nullptr;
            auto* connection_class = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
            auto* local_class = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
            if (!live(driver) || !field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) ||
                read_object(driver, STR("ServerConnection")) || read_object(driver, STR("World")) != world ||
                !client_entry || !local_entry || !connection_class || !local_class ||
                !connection->IsA(connection_class) || read_object(connection, STR("Driver")) != driver ||
                read_object(connection, STR("OwningActor")) != controller ||
                read_object(connection, STR("PlayerController")) != controller) return false;

            Unreal::FScriptArrayHelper local_players(locals, locals->ContainerPtrToValuePtr<void>(instance));
            if (local_players.Num() != 1) return false;
            auto* local_player = local_entry->GetObjectPropertyValue(local_players.GetRawPtr());
            auto* host_controller = read_object(local_player, STR("PlayerController"));
            auto* host_pawn = read_object(host_controller, STR("Pawn"));
            if (!live(local_player) || !local_player->IsA(local_class) ||
                !exact(host_controller, controller->GetClassPrivate()) || !authority(host_controller, world) ||
                read_object(host_controller, STR("Player")) != local_player ||
                !exact(host_pawn, pawn->GetClassPrivate()) || !authority(host_pawn, world) ||
                read_object(host_pawn, STR("Controller")) != host_controller ||
                read_object(host_pawn, STR("Save")) != stock) return false;

            Unreal::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(driver));
            if (remotes.Num() < 1 || remotes.Num() > max_clients) return false;
            std::array<Unreal::UObject*, max_clients> seen{};
            bool found = false;
            for (int index = 0; index < remotes.Num(); ++index)
            {
                auto* member = client_entry->GetObjectPropertyValue(remotes.GetRawPtr(index));
                if (!live(member) || !member->IsA(connection_class) ||
                    std::ranges::find(seen, member) != seen.end()) return false;
                seen[static_cast<std::size_t>(index)] = member;
                if (member == connection) found = true;
                else if (read_object(read_object(read_object(member, STR("PlayerController")), STR("Pawn")),
                                     STR("Save")) == guest) return false;
            }
            return found;
        }

        bool bind(Address pawn_address, Address guest_address, Address stock_address)
        {
            auto* pawn_class = find<Unreal::UClass>(pawn_path);
            auto* save_class = find<Unreal::UClass>(save_path);
            auto* instance_class = find<Unreal::UClass>(instance_path);
            auto* controller_class = find<Unreal::UClass>(controller_path);
            if (!pawn_class || !save_class || !instance_class || !controller_class) return false;
            Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
                if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
                if (exact(candidate, pawn_class)) pawn = candidate;
                return RC::LoopAction::Break;
            });
            if (!pawn) return false;
            world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
            guest = read_object(pawn, STR("Save"));
            instance = read_object(world, STR("OwningGameInstance"));
            stock = read_object(instance, STR("SaveSlot_Ref"));
            controller = read_object(pawn, STR("Controller"));
            connection = read_object(controller, STR("Player"));
            return exact(guest, save_class) && exact(stock, save_class) && exact(instance, instance_class) &&
                   exact(controller, controller_class) && address(guest) == guest_address &&
                   address(stock) == stock_address && current();
        }
    };

    struct RowLayout
    {
        Unreal::UScriptStruct* type = find<Unreal::UScriptStruct>(item_path);
        Unreal::UClass* item_base = find<Unreal::UClass>(item_base_path);
        Unreal::FClassProperty* item_class = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        Unreal::FIntProperty* quantity = field<Unreal::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        Unreal::FDoubleProperty* weight = field<Unreal::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
        Unreal::FIntProperty* value = field<Unreal::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        Unreal::FNameProperty* tag = field<Unreal::FNameProperty>(type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        Unreal::FStructProperty* data = field<Unreal::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        Unreal::FStructProperty* guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<Unreal::FIntProperty*, 4> words{};

        bool bind()
        {
            auto* guid_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            auto* data_type = find<Unreal::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
            if (!type || !item_base || !item_class || item_class->GetMetaClass().Get() != item_base ||
                !quantity || !weight || !value || !tag || !data_type || !data || data->GetStruct().Get() != data_type ||
                !guid_type || !guid || guid->GetStruct().Get() != guid_type || guid->GetSize() != sizeof(Guid)) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                words[index] = field<Unreal::FIntProperty>(guid_type, guid_component[index]);
                if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * sizeof(std::uint32_t)))
                    return false;
            }
            return true;
        }

        bool matches_type(Unreal::FStructProperty* property) const
        {
            return property && property->GetStruct().Get() == type;
        }

        bool matches_array(Unreal::FArrayProperty* property) const
        {
            return property && matches_type(Unreal::CastField<Unreal::FStructProperty>(property->GetInner()));
        }

        Unreal::UObject* read_class(const void* row) const
        {
            return item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(row));
        }

        Guid identity(const void* row) const
        {
            const auto* source = guid->ContainerPtrToValuePtr<void>(row);
            Guid result{};
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(source));
            return result;
        }

        bool has_no_gear_effect(const void* row) const
        {
            auto* item = read_class(row);
            if (!item) return true;
            auto* item_type = Unreal::Cast<Unreal::UClass>(item);
            if (!live(item_type) || !item_type->IsChildOf(item_base)) return false;
            auto* defaults = item_type->GetClassDefaultObject().Get();
            auto* passives = field<Unreal::FArrayProperty>(defaults, STR("PassiveEffects"));
            if (!defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
                defaults->GetClassPrivate() != item_type || !passives ||
                !Unreal::CastField<Unreal::FStrProperty>(passives->GetInner())) return false;
            Unreal::FScriptArrayHelper entries(passives, passives->ContainerPtrToValuePtr<void>(defaults));
            return entries.Num() == 0;
        }

        bool stock_consumable(const void* row, int units, const Consumable& profile) const
        {
            auto* type = find<Unreal::UClass>(profile.path);
            auto* health_item = find<Unreal::UClass>(health_item_path);
            if (!type || !health_item || !type->IsChildOf(health_item) || read_class(row) != type) return false;
            auto* defaults = type->GetClassDefaultObject().Get();
            auto* effect_map = field<Unreal::FMapProperty>(defaults, STR("TempEffects/Hours"));
            auto* default_tag = field<Unreal::FNameProperty>(defaults, STR("DataTag"));
            auto* default_payload = field<Unreal::FStructProperty>(defaults, STR("DataStruct"));
            if (!defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
                defaults->GetClassPrivate() != type || !read_bool(defaults, STR("Stacks"), true) ||
                !number_is<Unreal::FIntProperty>(defaults, STR("Value"), profile.value) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Weight"), profile.weight) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Regen_Health"), profile.regen_gain) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Instant_Health"), 0.0) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Regen_Boost"), profile.food) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Regen_SpeedStim"), 0.0) ||
                !number_is<Unreal::FIntProperty>(defaults, STR("Infection"), 0) ||
                !effect_map || effect_map->ContainerPtrToValuePtr<Unreal::FScriptMap>(defaults)->Num() != 0 ||
                !default_tag || !matches_payload(default_payload)) return false;
            return units > 0 && units <= INT32_MAX / profile.value &&
                   weight->GetPropertyValueInContainer(row) == units * profile.weight &&
                   value->GetPropertyValueInContainer(row) == units * profile.value &&
                   tag->Identical(tag->ContainerPtrToValuePtr<void>(row), default_tag->ContainerPtrToValuePtr<void>(defaults)) &&
                   data->Identical(data->ContainerPtrToValuePtr<void>(row), default_payload->ContainerPtrToValuePtr<void>(defaults));
        }

        bool matches_payload(Unreal::FStructProperty* property) const
        {
            return property && property->GetStruct().Get() == data->GetStruct().Get();
        }

        void set_remaining(void* row, int remaining, const Consumable& profile) const
        {
            // Stock FL_Items.StackItem_Stats applies the quantity difference and recomputes both totals.
            quantity->SetPropertyValueInContainer(row, remaining);
            weight->SetPropertyValueInContainer(row, remaining * profile.weight);
            value->SetPropertyValueInContainer(row, remaining * profile.value);
        }
    };

    struct Inventory
    {
        RowLayout row;
        Unreal::FArrayProperty* backpack{};
        int selected{-1};
        int units{};

        std::string_view locate(const Scope& scope, const Guid& requested, const Consumable& profile)
        {
            backpack = field<Unreal::FArrayProperty>(scope.guest, STR("PlayerBackpack"));
            auto* equipment = field<Unreal::FArrayProperty>(scope.guest, STR("PlayerEquipment"));
            auto* armor = field<Unreal::FStructProperty>(scope.guest, STR("PlayerArmor"));
            auto* helmet = field<Unreal::FStructProperty>(scope.guest, STR("PlayerHelmet"));
            if (!row.bind() || !row.matches_array(backpack) || !row.matches_array(equipment) ||
                !row.matches_type(armor) || !row.matches_type(helmet)) return "guest_bandage_refused_inventory_schema";
            for (auto* worn : {armor, helmet})
            {
                const auto* item = worn->ContainerPtrToValuePtr<void>(scope.guest);
                if (row.identity(item) == requested) return "guest_bandage_refused_guid_conflict";
                if (!row.has_no_gear_effect(item)) return "guest_bandage_refused_gear_passives";
            }

            Unreal::FScriptArrayHelper bag(backpack, bag_data(scope.guest));
            Unreal::FScriptArrayHelper host(backpack, bag_data(scope.stock));
            Unreal::FScriptArrayHelper equipped(equipment, equipment->ContainerPtrToValuePtr<void>(scope.guest));
            if (bag.Num() < 0 || bag.Num() > max_inventory || equipped.Num() < 0 || equipped.Num() > max_inventory ||
                host.Num() < 0 || host.Num() > max_inventory ||
                (host.Num() && bag.Num() && host.GetRawPtr() == bag.GetRawPtr()))
                return "guest_bandage_refused_inventory_extent";
            for (int index = 0; index < equipped.Num(); ++index)
            {
                const auto* item = equipped.GetRawPtr(index);
                if (row.identity(item) == requested) return "guest_bandage_refused_guid_conflict";
                if (!row.has_no_gear_effect(item)) return "guest_bandage_refused_gear_passives";
            }
            for (int index = 0; index < bag.Num(); ++index)
            {
                const auto* item = bag.GetRawPtr(index);
                if (!row.has_no_gear_effect(item)) return "guest_bandage_refused_gear_passives";
                if (row.identity(item) != requested) continue;
                if (selected != -1) return "guest_bandage_refused_guid_conflict";
                selected = index;
                units = row.quantity->GetPropertyValueInContainer(item);
                if (&profile == &mre && units == 0) units = 1;
                if (!row.stock_consumable(item, units, profile)) return "guest_bandage_refused_stock_stack";
            }
            return selected >= 0 ? "" : "guest_bandage_refused_owned_guid";
        }

        void* bag_data(Unreal::UObject* save) const { return backpack->ContainerPtrToValuePtr<void>(save); }
    };

    bool contains_name(Unreal::FArrayProperty* property, const void* container, const Unreal::FName& wanted,
                       bool& present)
    {
        auto* value = property ? Unreal::CastField<Unreal::FNameProperty>(property->GetInner()) : nullptr;
        if (!value || !container || value->GetArrayDim() != 1 || value->GetOffset_Internal() != 0) return false;
        Unreal::FScriptArrayHelper entries(property, property->ContainerPtrToValuePtr<void>(container));
        const auto count = entries.Num();
        if (count < 0 || count > max_effects) return false;
        for (int index = 0; index < count; ++index)
            present |= value->GetPropertyValue(entries.GetRawPtr(index)) == wanted;
        return true;
    }

    bool temporary_trait(Unreal::UObject* save, const Unreal::FName& wanted, bool& present)
    {
        auto* property = field<Unreal::FMapProperty>(save, STR("PlayerTempEffects"));
        auto* key = property ? Unreal::CastField<Unreal::FNameProperty>(property->GetKeyProp()) : nullptr;
        auto* value = property ? Unreal::CastField<Unreal::FStructProperty>(property->GetValueProp()) : nullptr;
        auto* timespan = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
        if (!key || key->GetSize() != sizeof(Unreal::FName) || key->GetOffset_Internal() != 0 ||
            !value || !timespan || value->GetStruct().Get() != timespan) return false;
        const auto layout = Unreal::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        const auto* map = property->ContainerPtrToValuePtr<Unreal::FScriptMap>(save);
        const int count = map->Num();
        const int extent = map->GetMaxIndex();
        if (count < 0 || count > extent || extent > max_effects) return false;
        for (int index = 0; index < extent; ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = map->GetData(index, layout);
            if (!entry) return false;
            present |= key->GetPropertyValue(entry) == wanted;
        }
        return true;
    }

    struct Vitals
    {
        double health{};
        double regen{};
        double food{};
        double stim{};

        bool finite() const
        {
            return std::isfinite(health) && std::isfinite(regen) && std::isfinite(food) && std::isfinite(stim);
        }

        bool operator==(const Vitals&) const = default;
    };

    struct Health
    {
        Unreal::FDoubleProperty* health{};
        Unreal::FDoubleProperty* regen{};
        Unreal::FDoubleProperty* food{};
        Unreal::FDoubleProperty* stim{};
        Unreal::FDoubleProperty* maximum{};
        Unreal::FArrayProperty* cache{};
        Unreal::UFunction* gain{};
        Vitals before{};
        double max_health{};
        bool hemophilia{};

        Vitals read(Unreal::UObject* save) const
        {
            return {
                health->GetPropertyValueInContainer(save), regen->GetPropertyValueInContainer(save),
                food->GetPropertyValueInContainer(save), stim->GetPropertyValueInContainer(save),
            };
        }

        std::string_view bind(const Scope& scope)
        {
            health = field<Unreal::FDoubleProperty>(scope.guest, STR("PlayerHealth"));
            regen = field<Unreal::FDoubleProperty>(scope.guest, STR("PlayerRegen"));
            food = field<Unreal::FDoubleProperty>(scope.guest, STR("PlayerFood"));
            stim = field<Unreal::FDoubleProperty>(scope.guest, STR("PlayerStim"));
            maximum = field<Unreal::FDoubleProperty>(scope.pawn, STR("MaxHealth"));
            gain = find<Unreal::UFunction>(gain_path);
            auto* amount = field<Unreal::FDoubleProperty>(gain, STR("Ammount"));
            if (!health || !regen || !food || !stim || !maximum || !amount ||
                gain->GetOuterPrivate() != scope.pawn->GetClassPrivate() || gain->GetNumParms() != 1 ||
                gain->GetReturnProperty() || gain->GetParmsSize() != sizeof(double) ||
                gain->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ||
                amount->GetOffset_Internal() != 0 || !amount->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
                amount->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm))
                return "guest_bandage_refused_health_schema";

            before = read(scope.guest);
            max_health = maximum->GetPropertyValueInContainer(scope.pawn);
            if (!before.finite() || !std::isfinite(max_health) || max_health < 25.0 ||
                before.health <= 0.0 || before.health > max_health || before.regen < 0.0 || before.regen > max_health ||
                before.food < 0.0 || before.food > 200.0 || before.stim < 0.0 ||
                !read_bool(scope.pawn, STR("Dead"), false) || !read_bool(scope.pawn, STR("UsingItem"), false) ||
                !read_bool(scope.pawn, STR("IsWeaponBlocked"), false) ||
                !read_bool(scope.pawn, STR("HeadUnderWater"), false))
                return "guest_bandage_refused_guest_vitals";

            const Unreal::FName trait{STR("Hemophilia"), Unreal::FNAME_Find};
            auto* character = field<Unreal::FStructProperty>(scope.guest, STR("Character"));
            auto* type = find<Unreal::UScriptStruct>(character_path);
            auto* effects = field<Unreal::FArrayProperty>(type, STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"));
            cache = field<Unreal::FArrayProperty>(scope.instance, STR("PassiveEffects"));
            bool cached = false;
            if (trait.IsNone() || trait.ToString() != STR("Hemophilia") || !character || !type ||
                character->GetStruct().Get() != type ||
                !contains_name(effects, character->ContainerPtrToValuePtr<void>(scope.guest), trait, hemophilia) ||
                !temporary_trait(scope.guest, trait, hemophilia) || !contains_name(cache, scope.instance, trait, cached))
                return "guest_bandage_refused_trait_schema";

            // GainHealth reads the GameInstance's compiled cache. A detached guest's
            // verified trait must agree since the cache is never switched or rewritten.
            return cached == hemophilia ? "" : "guest_bandage_refused_shared_trait_mismatch";
        }

        Vitals expected(double regen_gain) const
        {
            auto result = before;
            const double amount = hemophilia ? regen_gain * 0.6 : regen_gain;
            result.regen = std::clamp(before.regen + amount, 25.0, max_health);
            return result;
        }

        bool unchanged_except_regen(Unreal::UObject* save) const
        {
            auto current = read(save);
            current.regen = before.regen;
            return current == before;
        }

        bool no_food_traits(Unreal::UObject* save) const
        {
            auto* character = field<Unreal::FStructProperty>(save, STR("Character"));
            auto* type = find<Unreal::UScriptStruct>(character_path);
            auto* effects = field<Unreal::FArrayProperty>(type, STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"));
            if (!character || !type || character->GetStruct().Get() != type) return false;
            for (auto* name : {STR("Gourmand"), STR("HealthyEating")})
            {
                const Unreal::FName key{name, Unreal::FNAME_Find};
                bool present = false;
                if (key.IsNone() || key.ToString() != name ||
                    !contains_name(effects, character->ContainerPtrToValuePtr<void>(save), key, present) ||
                    !temporary_trait(save, key, present) || present) return false;
            }
            return true;
        }
    };

    struct ValueDelete
    {
        Unreal::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, ValueDelete>;

    Value copy_value(Unreal::FProperty* property, const void* current)
    {
        Value value(property->AllocateAndInitializeValue(), {property});
        if (value) property->CopyCompleteValue(value.get(), current);
        return value;
    }

    int use(const Lua& lua)
    {
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4))
            return refuse("guest_bandage_refused_scope");
        const auto pawn_address = lua.get_integer();
        const auto guest_address = lua.get_integer();
        const auto stock_address = lua.get_integer();
        Guid guid{};
        if (pawn_address <= 0 || guest_address <= 0 || stock_address <= 0 ||
            guest_address == stock_address || !parse_guid(lua.get_string(), guid))
            return refuse("guest_bandage_refused_identity");

        Scope scope;
        if (!scope.bind(pawn_address, guest_address, stock_address)) return refuse("guest_bandage_refused_exact_detached_save");
        Inventory inventory;
        if (const auto error = inventory.locate(scope, guid, bandage); !error.empty()) return refuse(error);
        Health health;
        if (const auto error = health.bind(scope); !error.empty()) return refuse(error);

        auto* bag = inventory.backpack;
        auto* source = inventory.bag_data(scope.guest);
        const auto host_vitals = health.read(scope.stock);
        auto* flags = field<Unreal::FMapProperty>(scope.stock, STR("Flags"));
        if (!flags || !host_vitals.finite()) return refuse("guest_bandage_refused_host_state");
        const auto source_before = copy_value(bag, source);
        const auto expected_bag = copy_value(bag, source);
        const auto stock_bag = copy_value(bag, inventory.bag_data(scope.stock));
        const auto stock_flags = copy_value(flags, flags->ContainerPtrToValuePtr<void>(scope.stock));
        const auto cache = copy_value(health.cache, health.cache->ContainerPtrToValuePtr<void>(scope.instance));
        if (!source_before || !expected_bag || !stock_bag || !stock_flags || !cache)
            return refuse("guest_bandage_refused_checkpoint");

        const int remaining = inventory.units - 1;
        Unreal::FScriptArrayHelper expected_rows(bag, expected_bag.get());
        if (remaining == 0) expected_rows.RemoveValues(inventory.selected);
        else inventory.row.set_remaining(expected_rows.GetRawPtr(inventory.selected), remaining, bandage);

        const auto stock_unchanged = [&] {
            return scope.current() && health.read(scope.stock) == host_vitals &&
                bag->Identical(inventory.bag_data(scope.stock), stock_bag.get()) &&
                flags->Identical(flags->ContainerPtrToValuePtr<void>(scope.stock), stock_flags.get()) &&
                health.cache->Identical(health.cache->ContainerPtrToValuePtr<void>(scope.instance), cache.get());
        };
        const auto preflight = [&] {
            return health.unchanged_except_regen(scope.guest) &&
                health.maximum->GetPropertyValueInContainer(scope.pawn) == health.max_health;
        };
        const auto rollback = [&](bool bag_written) {
            if (!stock_unchanged() || !preflight()) return false;
            // Before the explicit debit, any different bag was changed by another
            // callback and its contents cannot be safely restored here.
            if (bag_written) bag->CopyCompleteValue(source, source_before.get());
            if (!bag->Identical(source, source_before.get())) return false;
            health.regen->SetPropertyValueInContainer(scope.guest, health.before.regen);
            return stock_unchanged() && health.read(scope.guest) == health.before &&
                   bag->Identical(source, source_before.get());
        };

        // The exact clean Bandage has no instant healing, temporary effects, food,
        // stimulation or infection. Invoke only the qualified stock gameplay effect.
        // The stock consumable wrapper can fall back to player zero and runs latent UI.
        double amount = bandage.regen_gain;
        scope.pawn->ProcessEvent(health.gain, &amount);
        const auto expected_vitals = health.expected(bandage.regen_gain);
        if (!stock_unchanged() || !preflight() || health.read(scope.guest) != expected_vitals ||
            !bag->Identical(source, source_before.get()))
            return refuse(rollback(false) ? "guest_bandage_refused_heal_rolled_back"
                                          : "mutation_unverified_guest_bandage_heal");

        bag->CopyCompleteValue(source, expected_bag.get());
        if (!stock_unchanged() || !preflight() || health.read(scope.guest) != expected_vitals ||
            !bag->Identical(source, expected_bag.get()))
            return refuse(rollback(true) ? "guest_bandage_refused_debit_rolled_back"
                                         : "mutation_unverified_guest_bandage_debit");

        result(lua, true, "used");
        lua.set_integer(inventory.units);
        lua.set_integer(remaining);
        lua.set_number(health.before.regen);
        lua.set_number(expected_vitals.regen);
        lua.set_number(health.before.health);
        lua.set_number(expected_vitals.health);
        lua.set_number(health.max_health);
        lua.set_bool(health.hemophilia);
        return 10;
    }

    struct Intoxication
    {
        Unreal::FMapProperty* map{};
        Unreal::FNameProperty* key{};
        Unreal::FIntProperty* number{};
        Unreal::FScriptMapLayout layout{};
        Unreal::FName flag{STR("Player_Intox"), Unreal::FNAME_Find};

        bool bind(Unreal::UObject* save)
        {
            map = field<Unreal::FMapProperty>(save, STR("Flags"));
            key = map ? Unreal::CastField<Unreal::FNameProperty>(map->GetKeyProp()) : nullptr;
            number = map ? Unreal::CastField<Unreal::FIntProperty>(map->GetValueProp()) : nullptr;
            if (!key || !number || key->GetArrayDim() != 1 || number->GetArrayDim() != 1 ||
                key->GetSize() != sizeof(Unreal::FName) || key->GetOffset_Internal() != 0 ||
                number->GetSize() != sizeof(int) || flag.IsNone() || flag.ToString() != STR("Player_Intox") ||
                !key->HasAnyPropertyFlags(Unreal::CPF_HasGetValueTypeHash)) return false;
            layout = Unreal::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(),
                number->GetSize(), number->GetMinAlignment());
            return number->GetOffset_Internal() == layout.ValueOffset && usable(data(save));
        }

        Unreal::FScriptMap* data(Unreal::UObject* save) const
        {
            return map->ContainerPtrToValuePtr<Unreal::FScriptMap>(save);
        }

        bool usable(const Unreal::FScriptMap* entries) const
        {
            return entries && entries->Num() >= 0 && entries->Num() <= entries->GetMaxIndex() && entries->GetMaxIndex() < 16384;
        }

        auto hash() const { return [this](const void* raw) { return key->GetValueTypeHash(raw); }; }
        auto identical_key() const { return [this](const void* a, const void* b) { return key->Identical(a, b); }; }

        void* find_value(Unreal::FScriptMap* entries) const
        {
            return entries->FindValue(&flag, layout, hash(), identical_key());
        }

        int read(Unreal::FScriptMap* entries) const
        {
            const auto* value = find_value(entries);
            return value ? number->GetPropertyValue(value) : 0;
        }

        bool write(Unreal::FScriptMap* entries, int expected) const
        {
            if (!usable(entries) || expected < 0 || expected > 100) return false;
            const int count = entries->Num();
            const bool existed = find_value(entries) != nullptr;
            auto* value = entries->FindOrAdd(&flag, layout, hash(), identical_key(), [&](void* name, void* data) {
                key->InitializeValue(name);
                key->SetPropertyValue(name, flag);
                number->InitializeValue(data);
            });
            if (!value) return false;
            number->SetPropertyValue(value, expected);
            return entries->Num() == count + !existed && read(entries) == expected;
        }
    };

    struct FoodFunctions
    {
        Unreal::UFunction* effect{};
        Unreal::UFunction* intox{};
        Unreal::FIntProperty* difference{};
        Unreal::FIntProperty* pawn_intox{};

        bool bind()
        {
            auto* pawn = find<Unreal::UClass>(pawn_path);
            const auto function = [pawn](const Unreal::TCHAR* path, std::size_t size) -> Unreal::UFunction* {
                auto* value = find<Unreal::UFunction>(path);
                return pawn && value && value->GetOuterPrivate() == pawn && value->GetNumParms() == 1 &&
                    value->GetParmsSize() == size && !value->GetReturnProperty() &&
                    !value->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net) ? value : nullptr;
            };
            auto* food = function(boost_path, sizeof(double));
            auto* apply = function(intox_path, sizeof(int));
            auto* amount = field<Unreal::FDoubleProperty>(food, STR("Ammount"));
            auto* difference_field = field<Unreal::FIntProperty>(apply, STR("Difference"));
            auto* pawn_field = field<Unreal::FIntProperty>(pawn, STR("Intoxication"));
            const auto input = [](Unreal::FProperty* property) {
                return property && property->GetOffset_Internal() == 0 && property->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
                    !property->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm);
            };
            if (!input(amount) || !input(difference_field) || !pawn_field) return false;
            effect = food;
            intox = apply;
            difference = difference_field;
            pawn_intox = pawn_field;
            return true;
        }
    } food_functions;

    struct DuringMeal
    {
        Unreal::UObject* pawn{};
        int expected_delta{};
        int call_count{};
        bool applied{};
    };
    thread_local DuringMeal* meal{};
    Hook::GlobalCallbackId intox_hook = Hook::ERROR_ID;

    struct ScopedMeal
    {
        explicit ScopedMeal(DuringMeal& receipt) { meal = &receipt; }
        ~ScopedMeal() { meal = nullptr; }
        ScopedMeal(const ScopedMeal&) = delete;
        ScopedMeal& operator=(const ScopedMeal&) = delete;
    };

    void redirect_guest_intox(Hook::TCallbackIterationData<void>& iteration, Unreal::UObject* pawn, Unreal::FFrame& frame, void*)
    {
        if (!food_functions.intox || frame.Node() != food_functions.intox || !Unreal::IsInGameThreadRaw() ||
            iteration.OriginalFunctionCallPrevented() || !pawn || frame.Object() != pawn || !frame.Locals()) return;
        auto* guest = read_object(pawn, STR("Save"));
        auto* instance = read_object(pawn, STR("As Game Instance FP"));
        auto* stock = read_object(instance, STR("SaveSlot_Ref"));
        if (!silverfish::native_guest_regen::qualifies_detached_guest(pawn, guest, stock)) return;
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        const int delta = food_functions.difference->GetPropertyValueInContainer(frame.Locals());
        // In the cooked Apply_PlayerIntox graph, positive input can also grant DrunkPower.
        // The co-op MRE option handles the exact neutral/negative branches without a global Save write.
        if (delta > 0 || delta < -100) return;
        auto* caller = frame.PreviousFrame();
        auto* active = meal && pawn == meal->pawn ? meal : nullptr;
        if (active && (++active->call_count != 1 || !caller || caller->Node() != food_functions.effect ||
            caller->Object() != pawn || delta != active->expected_delta)) return;

        Intoxication personal;
        if (!personal.bind(guest)) return;
        auto* flags = personal.data(guest);
        const int previous = personal.read(flags);
        if (previous < 0 || previous > 100) return;
        const int after = std::clamp(previous + delta, 0, 100);
        if (!personal.write(flags, after)) return;
        food_functions.pawn_intox->SetPropertyValueInContainer(pawn, after);
        if (active) active->applied = food_functions.pawn_intox->GetPropertyValueInContainer(pawn) == after;
    }

    bool food_available()
    {
        return Unreal::IsInGameThreadRaw() && intox_hook != Hook::ERROR_ID && (food_functions.effect || food_functions.bind());
    }

    int prepare_food(const Lua& lua)
    {
        return result(lua, lua.get_stack_size() == 0 && food_available(), "stock_guest_mre_isolation");
    }

    int seed_mre_test(const Lua& lua)
    {
        const auto refusal = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!opt_in("SILVERFISH_COOP_HEADLESS") || !opt_in("SILVERFISH_COOP_NATIVE_FIXTURES") || !food_available() ||
            lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return refusal("guest_mre_refused_fixture_arguments");
        const auto pawn = lua.get_integer(), guest = lua.get_integer(), stock = lua.get_integer();
        Scope scope;
        Health health;
        Intoxication personal, canonical;
        if (pawn <= 0 || guest <= 0 || stock <= 0 || !scope.bind(pawn, guest, stock) || !health.bind(scope).empty() ||
            !personal.bind(scope.guest) || !canonical.bind(scope.stock))
            return refusal("guest_mre_refused_fixture_scope");
        auto* flags = personal.data(scope.guest);
        auto* stock_flags = canonical.data(scope.stock);
        const auto before = copy_value(personal.map, flags);
        const auto expected = copy_value(personal.map, flags);
        const auto stock_before = copy_value(canonical.map, stock_flags);
        constexpr int test_intoxication = 42;
        constexpr double test_food = 17;
        if (!before || !expected || !stock_before ||
            !personal.write(static_cast<Unreal::FScriptMap*>(expected.get()), test_intoxication))
            return refusal("guest_mre_refused_fixture_snapshot");
        const int pawn_before = food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn);
        personal.map->CopyCompleteValue(flags, expected.get());
        health.food->SetPropertyValueInContainer(scope.guest, test_food);
        food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, test_intoxication);
        auto vitals = health.before;
        vitals.food = test_food;
        const auto safe_stock = [&] { return scope.current() && canonical.map->Identical(stock_flags, stock_before.get()); };
        if (!safe_stock() || !personal.map->Identical(flags, expected.get()) || health.read(scope.guest) != vitals ||
            food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) != test_intoxication)
        {
            if (!safe_stock()) return refusal("mutation_unverified_guest_mre_fixture");
            personal.map->CopyCompleteValue(flags, before.get());
            health.food->SetPropertyValueInContainer(scope.guest, health.before.food);
            food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, pawn_before);
            return refusal(safe_stock() && personal.map->Identical(flags, before.get()) && health.read(scope.guest) == health.before &&
                food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) == pawn_before
                ? "guest_mre_refused_fixture_rolled_back" : "mutation_unverified_guest_mre_fixture");
        }
        result(lua, true, "verified");
        lua.set_number(test_food);
        lua.set_integer(test_intoxication);
        lua.set_integer(canonical.read(stock_flags));
        return 5;
    }

    int use_mre(const Lua& lua)
    {
        const auto refusal = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!food_available() || lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || meal) return refusal("guest_mre_refused_arguments");
        const auto pawn_address = lua.get_integer(), guest_address = lua.get_integer(), stock_address = lua.get_integer();
        Guid guid{};
        if (pawn_address <= 0 || guest_address <= 0 || stock_address <= 0 || guest_address == stock_address ||
            !parse_guid(lua.get_string(), guid)) return refusal("guest_mre_refused_identity");
        Scope scope;
        if (!scope.bind(pawn_address, guest_address, stock_address)) return refusal("guest_mre_refused_exact_detached_save");
        Inventory inventory;
        if (!inventory.locate(scope, guid, mre).empty()) return refusal("guest_mre_refused_stock_owned_stack");
        Health health;
        if (!health.bind(scope).empty() || health.max_health > 10'000 || !health.no_food_traits(scope.guest))
            return refusal("guest_mre_refused_vitals_or_traits");

        Intoxication personal, canonical;
        if (!personal.bind(scope.guest) || !canonical.bind(scope.stock) || personal.map != canonical.map)
            return refusal("guest_mre_refused_intoxication_schema");
        auto* guest_flags = personal.data(scope.guest);
        auto* host_flags = canonical.data(scope.stock);
        auto* bag = inventory.backpack;
        auto* guest_rows = inventory.bag_data(scope.guest);
        auto* host_rows = inventory.bag_data(scope.stock);
        const int intox_before = personal.read(guest_flags), pawn_before = food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn);
        const int host_intox = canonical.read(host_flags);
        const auto host_vitals = health.read(scope.stock);
        if (intox_before < 0 || intox_before > 100 || host_intox < 0 || host_intox > 100 ||
            pawn_before < 0 || pawn_before > 100 || !host_vitals.finite()) return refusal("guest_mre_refused_intoxication_values");

        const auto backup_flags = copy_value(personal.map, guest_flags);
        const auto expected_flags = copy_value(personal.map, guest_flags);
        const auto stock_flags = copy_value(canonical.map, host_flags);
        const auto backup_bag = copy_value(bag, guest_rows);
        const auto expected_bag = copy_value(bag, guest_rows);
        const auto stock_bag = copy_value(bag, host_rows);
        const auto cache = copy_value(health.cache, health.cache->ContainerPtrToValuePtr<void>(scope.instance));
        if (!backup_flags || !expected_flags || !stock_flags || !backup_bag || !expected_bag || !stock_bag || !cache)
            return refusal("guest_mre_refused_snapshot");

        auto expected = health.expected(mre.regen_gain);
        expected.food = std::clamp(health.before.food + mre.food, 0.0, 200.0);
        const int delta = static_cast<int>(mre.food * -0.1);
        const int intox_after = std::clamp(intox_before + delta, 0, 100);
        if (!personal.write(static_cast<Unreal::FScriptMap*>(expected_flags.get()), intox_after))
            return refusal("guest_mre_refused_prepared_intoxication");
        const int remaining = inventory.units - 1;
        Unreal::FScriptArrayHelper prepared(bag, expected_bag.get());
        if (remaining == 0) prepared.RemoveValues(inventory.selected);
        else inventory.row.set_remaining(prepared.GetRawPtr(inventory.selected), remaining, mre);

        const auto stock_unchanged = [&] {
            return scope.current() && bag->Identical(host_rows, stock_bag.get()) && canonical.map->Identical(host_flags, stock_flags.get()) &&
                health.read(scope.stock) == host_vitals && health.maximum->GetPropertyValueInContainer(scope.pawn) == health.max_health &&
                health.cache->Identical(health.cache->ContainerPtrToValuePtr<void>(scope.instance), cache.get());
        };
        const auto prove = [&](const void* expected_rows, const Vitals& expected_vitals) {
            return stock_unchanged() && health.read(scope.guest) == expected_vitals && bag->Identical(guest_rows, expected_rows) &&
                personal.map->Identical(guest_flags, expected_flags.get()) &&
                food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) == intox_after;
        };
        const auto rollback = [&](bool bag_written, const Vitals& after) {
            if (!prove(bag_written ? expected_bag.get() : backup_bag.get(), after)) return false;
            if (bag_written) bag->CopyCompleteValue(guest_rows, backup_bag.get());
            personal.map->CopyCompleteValue(guest_flags, backup_flags.get());
            health.food->SetPropertyValueInContainer(scope.guest, health.before.food);
            health.regen->SetPropertyValueInContainer(scope.guest, health.before.regen);
            food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, pawn_before);
            return stock_unchanged() && health.read(scope.guest) == health.before && bag->Identical(guest_rows, backup_bag.get()) &&
                personal.map->Identical(guest_flags, backup_flags.get()) &&
                food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) == pawn_before;
        };

        DuringMeal receipt{scope.pawn, delta};
        {
            ScopedMeal scope_call{receipt};
            double food = mre.food;
            scope.pawn->ProcessEvent(food_functions.effect, &food);
        }

        auto only_food = health.before;
        only_food.food = expected.food;
        if (receipt.call_count != 1 || !receipt.applied || !prove(backup_bag.get(), only_food))
            return refusal(rollback(false, only_food) ? "guest_mre_refused_food_rolled_back" : "mutation_unverified_guest_mre_food");
        double gain = mre.regen_gain;
        scope.pawn->ProcessEvent(health.gain, &gain);
        if (!prove(backup_bag.get(), expected))
            return refusal(rollback(false, only_food) ? "guest_mre_refused_regen_rolled_back" : "mutation_unverified_guest_mre_regen");
        bag->CopyCompleteValue(guest_rows, expected_bag.get());
        if (!prove(expected_bag.get(), expected))
            return refusal(rollback(true, expected) ? "guest_mre_refused_debit_rolled_back" : "mutation_unverified_guest_mre_debit");

        result(lua, true, "used");
        lua.set_integer(inventory.units);
        lua.set_integer(remaining);
        lua.set_number(health.before.food);
        lua.set_number(expected.food);
        lua.set_number(health.before.regen);
        lua.set_number(expected.regen);
        lua.set_number(health.before.health);
        lua.set_number(health.max_health);
        lua.set_integer(intox_before);
        lua.set_integer(intox_after);
        lua.set_integer(host_intox);
        lua.set_bool(health.hemophilia);
        return 14;
    }
}

void silverfish::native_guest_consumable::install_hooks()
{
    if (!mre_enabled() || intox_hook != Hook::ERROR_ID) return;
    intox_hook = Hook::RegisterProcessLocalScriptFunctionPreCallback(redirect_guest_intox,
        {false, false, STR("SilverfishCoopNative"), STR("Keep stock guest MRE intoxication in its detached Save")});
}

void silverfish::native_guest_consumable::uninstall_hooks()
{
    if (intox_hook != Hook::ERROR_ID) Hook::UnregisterCallback(intox_hook);
    intox_hook = Hook::ERROR_ID;
    food_functions = {};
}

void silverfish::native_guest_consumable::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (enabled()) lua.register_function("SilverfishUseNativeGuestBandage", use);
    if (mre_enabled())
    {
        lua.register_function("SilverfishPrepareNativeGuestMre", prepare_food);
        lua.register_function("SilverfishUseNativeGuestMre", use_mre);
        if (opt_in("SILVERFISH_COOP_HEADLESS") && opt_in("SILVERFISH_COOP_NATIVE_FIXTURES"))
            lua.register_function("SilverfishSeedNativeGuestMreFixture", seed_mre_test);
    }
}
