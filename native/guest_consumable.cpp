#include "guest_consumable.hpp"
#include "enemy_loot.hpp"
#include "guest_meal_profiles.hpp"
#include "guest_regen.hpp"
#include "guest_sickness.hpp"
#include "loot_escrow.hpp"

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
#include <Unreal/Core/Containers/FString.hpp>
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
    constexpr auto item_base_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto health_item_path = STR("/Game/Classes/Items/HealthItem_Master_BP.HealthItem_Master_BP_C");
    constexpr auto bandage_path = STR("/Game/Classes/Items/Item_Bandage.Item_Bandage_C");
    constexpr auto starter_bag_path = STR("/Game/Classes/Items/Random_GearBag_Starter.Random_GearBag_Starter_C");
    constexpr auto hyper_bag_path = STR("/Game/Classes/Items/HyperRandom_GearBag_Starter.HyperRandom_GearBag_Starter_C");
    constexpr auto boost_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:GiveRegenBoost");
    constexpr auto intox_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Apply_PlayerIntox");
    constexpr std::array guid_component{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_clients = 4;
    constexpr int max_inventory = 4096;
    using silverfish::native_guest_consumable::Consumable;
    constexpr Consumable bandage{bandage_path, 15, 0.05, 45.0, 0.0};

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
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Instant_Health"), profile.instant) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Regen_Boost"), profile.food) ||
                !number_is<Unreal::FDoubleProperty>(defaults, STR("Regen_SpeedStim"), profile.stim) ||
                !number_is<Unreal::FIntProperty>(defaults, STR("Infection"), profile.infection) ||
                !effect_map || effect_map->ContainerPtrToValuePtr<Unreal::FScriptMap>(defaults)->Num() != 0 ||
                !default_tag || !matches_payload(default_payload)) return false;
            if (profile.food > 0.0 && !read_bool(defaults, STR("CanCook"), profile.can_cook)) return false;
            if (profile.fatigue && !number_is<Unreal::FIntProperty>(defaults, STR("Ammount_Fatigue"), profile.fatigue)) return false;
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
            }
            for (int index = 0; index < bag.Num(); ++index)
            {
                const auto* item = bag.GetRawPtr(index);
                if (row.identity(item) != requested) continue;
                if (selected != -1) return "guest_bandage_refused_guid_conflict";
                selected = index;
                units = row.quantity->GetPropertyValueInContainer(item);
                if (&profile != &bandage && units == 0) units = 1;
                if (!row.stock_consumable(item, units, profile)) return "guest_bandage_refused_stock_stack";
            }
            return selected >= 0 ? "" : "guest_bandage_refused_owned_guid";
        }

        void* bag_data(Unreal::UObject* save) const { return backpack->ContainerPtrToValuePtr<void>(save); }
    };

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
            cache = field<Unreal::FArrayProperty>(scope.instance, STR("PassiveEffects"));
            auto* names = cache ? Unreal::CastField<Unreal::FNameProperty>(cache->GetInner()) : nullptr;
            if (!health || !regen || !food || !stim || !maximum || !names || names->GetArrayDim() != 1 ||
                names->GetOffset_Internal() != 0) return "guest_bandage_refused_health_schema";

            before = read(scope.guest);
            max_health = maximum->GetPropertyValueInContainer(scope.pawn);
            if (!before.finite() || !std::isfinite(max_health) || max_health < 25.0 ||
                before.health <= 0.0 || before.health > max_health || before.regen < 0.0 || before.regen > max_health ||
                before.food < 0.0 || before.food > 200.0 || before.stim < 0.0 ||
                !read_bool(scope.pawn, STR("Dead"), false) || !read_bool(scope.pawn, STR("UsingItem"), false) ||
                !read_bool(scope.pawn, STR("IsWeaponBlocked"), false) ||
                !read_bool(scope.pawn, STR("HeadUnderWater"), false))
                return "guest_bandage_refused_guest_vitals";

            std::int32_t count{};
            if (!silverfish::native_enemy_loot::count_save_passive(
                    scope.guest, Unreal::FName{STR("Hemophilia"), Unreal::FNAME_Find}, count))
                return "guest_bandage_refused_trait_schema";
            hemophilia = count > 0;
            return {};
        }

        Vitals expected(double regen_gain, double instant = 0.0, double over_stim = 0.0) const
        {
            auto result = before;
            const double amount = hemophilia && regen_gain > 0.0 ? regen_gain * 0.6 : regen_gain;
            const double instant_gain = hemophilia ? instant * 0.6 : instant;
            result.health = std::clamp(before.health + instant_gain, 0.0, max_health);
            result.regen = std::clamp(std::min(before.regen + instant_gain, max_health) + amount, 25.0, max_health);
            result.stim = std::max(before.stim, over_stim);
            return result;
        }

        void write_healing(Unreal::UObject* save, const Vitals& vitals) const
        {
            health->SetPropertyValueInContainer(save, vitals.health);
            regen->SetPropertyValueInContainer(save, vitals.regen);
            stim->SetPropertyValueInContainer(save, vitals.stim);
        }

        bool unchanged_except_regen(Unreal::UObject* save) const
        {
            auto current = read(save);
            current.regen = before.regen;
            return current == before;
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

        const auto expected_vitals = health.expected(bandage.regen_gain);
        // GainHealth would read the host's traits. The Bandage only changes the
        // selected guest's stock regeneration buffer and the exact consumed row.
        health.regen->SetPropertyValueInContainer(scope.guest, expected_vitals.regen);
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

    int open_starter_bag(const Lua& lua)
    {
        constexpr int max_rewards = 16;
        const auto refuse = [&](std::string_view reason) { return result(lua, false, reason); };
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 4 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4))
            return refuse("guest_starter_bag_refused_scope");
        const auto pawn = lua.get_integer(), detached = lua.get_integer(), stock = lua.get_integer();
        Guid source{};
        Scope scope;
        if (pawn <= 0 || detached <= 0 || stock <= 0 || !parse_guid(lua.get_string(), source) ||
            !scope.bind(pawn, detached, stock) || !read_bool(scope.pawn, STR("Dead"), false) ||
            !read_bool(scope.pawn, STR("HeadUnderWater"), false))
            return refuse("guest_starter_bag_refused_owner");

        RowLayout row;
        auto* backpack = field<Unreal::FArrayProperty>(scope.guest, STR("PlayerBackpack"));
        auto* equipment = field<Unreal::FArrayProperty>(scope.guest, STR("PlayerEquipment"));
        auto* armor = field<Unreal::FStructProperty>(scope.guest, STR("PlayerArmor"));
        auto* helmet = field<Unreal::FStructProperty>(scope.guest, STR("PlayerHelmet"));
        auto* flags = field<Unreal::FMapProperty>(scope.stock, STR("Flags"));
        auto* destroy = find<Unreal::UFunction>(STR("/Script/Engine.Actor:K2_DestroyActor"));
        if (!row.bind() || !row.matches_array(backpack) || !row.matches_array(equipment) ||
            !row.matches_type(armor) || !row.matches_type(helmet) || !flags || !destroy ||
            destroy->GetNumParms() || destroy->GetParmsSize() || !destroy->HasAnyFunctionFlags(Unreal::FUNC_Native))
            return refuse("guest_starter_bag_refused_schema");

        auto* guest_rows = backpack->ContainerPtrToValuePtr<void>(scope.guest);
        auto* stock_rows = backpack->ContainerPtrToValuePtr<void>(scope.stock);
        Unreal::FScriptArrayHelper guest(backpack, guest_rows), host(backpack, stock_rows);
        if (guest.Num() < 1 || guest.Num() > max_inventory || host.Num() > max_inventory ||
            (host.Num() && guest.GetRawPtr() == host.GetRawPtr()))
            return refuse("guest_starter_bag_refused_inventory");

        const auto rows_contain = [&](Unreal::FScriptArrayHelper& rows, const Guid& guid) {
            for (int index = 0; index < rows.Num(); ++index)
                if (row.identity(rows.GetRawPtr(index)) == guid) return true;
            return false;
        };
        const auto gear_contains = [&](Unreal::UObject* save, const Guid& guid) {
            Unreal::FScriptArrayHelper slots(equipment, equipment->ContainerPtrToValuePtr<void>(save));
            return slots.Num() > max_inventory || rows_contain(slots, guid) ||
                row.identity(armor->ContainerPtrToValuePtr<void>(save)) == guid ||
                row.identity(helmet->ContainerPtrToValuePtr<void>(save)) == guid;
        };
        int selected = -1;
        Unreal::UClass* type{};
        for (int index = 0; index < guest.Num(); ++index)
        {
            const auto* item = guest.GetRawPtr(index);
            if (row.identity(item) != source) continue;
            if (selected != -1) return refuse("guest_starter_bag_refused_duplicate");
            selected = index;
            type = Unreal::Cast<Unreal::UClass>(row.read_class(item));
        }
        const auto bag_path = type ? type->GetPathName() : RC::StringType{};
        if (selected == -1 || (bag_path != starter_bag_path && bag_path != hyper_bag_path) ||
            !type->IsChildOf(row.item_base) || rows_contain(host, source) || gear_contains(scope.guest, source) ||
            gear_contains(scope.stock, source))
            return refuse("guest_starter_bag_refused_exact_bag");

        auto* contents = field<Unreal::FArrayProperty>(type, STR("Contents"));
        auto* entry = contents ? Unreal::CastField<Unreal::FClassProperty>(contents->GetInner()) : nullptr;
        auto* row_property = Unreal::CastField<Unreal::FStructProperty>(backpack->GetInner());
        const auto before = copy_value(backpack, guest_rows);
        const auto prepared = copy_value(backpack, guest_rows);
        const auto host_before = copy_value(backpack, stock_rows);
        const auto host_flags = copy_value(flags, flags->ContainerPtrToValuePtr<void>(scope.stock));
        if (!entry || entry->GetMetaClass().Get() != row.item_base || !before || !prepared || !host_before || !host_flags)
            return refuse("guest_starter_bag_refused_snapshot");
        const auto unchanged = [&] {
            return scope.current() && backpack->Identical(stock_rows, host_before.get()) &&
                flags->Identical(flags->ContainerPtrToValuePtr<void>(scope.stock), host_flags.get());
        };
        const auto generation_failed = [&](bool retired, std::string_view reason) {
            if (!retired) return refuse(reason);
            if (!scope.current()) return refuse("mutation_unverified_guest_starter_bag_owner_changed");
            if (!backpack->Identical(stock_rows, host_before.get()))
                return refuse("mutation_unverified_guest_starter_bag_host_backpack");
            if (!flags->Identical(flags->ContainerPtrToValuePtr<void>(scope.stock), host_flags.get()))
                return refuse("mutation_unverified_guest_starter_bag_host_flags");
            if (!backpack->Identical(guest_rows, before.get()))
                return refuse("mutation_unverified_guest_starter_bag_guest_backpack");
            return refuse(reason);
        };

        // Set the no-respawn policy before BeginPlay so temporary stock items
        // cannot create world persistence entries in the canonical save.
        struct Deferred
        {
            Unreal::UObject* context{};
            Unreal::UClass* item{};
            Unreal::FTransform_As500Plus transform;
            std::uint8_t collision{1};
            Unreal::UObject* owner{};
            std::uint8_t scale{1};
            Unreal::AActor* result{};
        } begin;
        struct Finished
        {
            Unreal::AActor* actor{};
            Unreal::FTransform_As500Plus transform;
            std::uint8_t scale{1};
            Unreal::AActor* result{};
        } finish;
        static_assert(offsetof(Deferred, transform) == 0x10 && offsetof(Deferred, result) == 0x88);
        static_assert(offsetof(Finished, transform) == 0x10 && offsetof(Finished, result) == 0x78);
        auto* statics = find<Unreal::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
        auto* deferred = find<Unreal::UFunction>(STR("/Script/Engine.GameplayStatics:BeginDeferredActorSpawnFromClass"));
        auto* complete = find<Unreal::UFunction>(STR("/Script/Engine.GameplayStatics:FinishSpawningActor"));
        auto* actor_class = find<Unreal::UClass>(STR("/Script/Engine.Actor"));
        auto* transform = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Transform"));
        const auto spawn_function = [&](Unreal::UFunction* function, int count, int size, int output) {
            auto* position = field<Unreal::FStructProperty>(function, STR("SpawnTransform"));
            auto* result_actor = field<Unreal::FObjectProperty>(function, STR("ReturnValue"));
            return statics && actor_class && transform && function && function->GetOuterPrivate() == statics->GetClassPrivate() &&
                function->HasAnyFunctionFlags(Unreal::FUNC_Native) && function->GetNumParms() == count &&
                function->GetParmsSize() == size && position && position->GetStruct().Get() == transform &&
                position->GetSize() == 0x60 && position->GetOffset_Internal() == 0x10 && result_actor &&
                function->GetReturnProperty() == result_actor && result_actor->GetOffset_Internal() == output &&
                result_actor->GetPropertyClass().Get() == actor_class;
        };
        auto* item_arg = field<Unreal::FClassProperty>(deferred, STR("ActorClass"));
        if (!spawn_function(deferred, 7, 0x90, 0x88) || !spawn_function(complete, 4, 0x80, 0x78) ||
            !item_arg || item_arg->GetOffset_Internal() != 8 || item_arg->GetMetaClass().Get() != actor_class)
            return refuse("guest_starter_bag_refused_spawn_schema");

        auto position = static_cast<Unreal::AActor*>(scope.pawn)->K2_GetActorLocation();
        position.SetZ(position.GetZ() + 1500.0);
        begin.context = scope.pawn;
        begin.owner = scope.pawn;
        begin.transform = Unreal::FTransform{Unreal::FQuat(0, 0, 0, 1), position, Unreal::FVector(1, 1, 1)};
        const auto spawn = [&](Unreal::UClass* item, bool& qualified) {
            qualified = false;
            begin.item = item;
            begin.result = nullptr;
            statics->ProcessEvent(deferred, &begin);
            auto* actor = begin.result;
            if (!exact(actor, item) || !authority(actor, scope.world) || actor->HasAnyFlags(Unreal::RF_WasLoaded)) return actor;
            auto* respawn = field<Unreal::FByteProperty>(actor, STR("Item_RespawnMode"));
            auto* instigator = field<Unreal::FObjectProperty>(actor, STR("Instigator"));
            if (!respawn || !instigator || !scope.pawn->IsA(instigator->GetPropertyClass().Get())) return actor;
            actor->SetFlags(Unreal::RF_Transient);
            actor->SetActorEnableCollision(false);
            actor->SetActorHiddenInGame(true);
            respawn->SetPropertyValueInContainer(actor, 0);
            instigator->SetObjectPropertyValue(instigator->ContainerPtrToValuePtr<void>(actor), scope.pawn);
            finish.actor = actor;
            finish.transform = begin.transform;
            finish.result = nullptr;
            statics->ProcessEvent(complete, &finish);
            qualified = finish.result == actor && authority(actor, scope.world) && respawn->GetPropertyValueInContainer(actor) == 0;
            if (qualified)
            {
                actor->SetActorEnableCollision(false);
                actor->SetActorHiddenInGame(true);
            }
            return actor;
        };
        const auto retire = [&](Unreal::AActor* actor) {
            if (!live(actor) || read_bool(actor, STR("bActorIsBeingDestroyed"), true)) return true;
            const Unreal::FWeakObjectPtr pointer{actor};
            actor->K2_DestroyActor();
            auto* remaining = pointer.Get();
            return !live(remaining) || read_bool(remaining, STR("bActorIsBeingDestroyed"), true) ||
                static_cast<Unreal::AActor*>(remaining)->GetWorld() != scope.world;
        };
        const auto refused_bag = [&](Unreal::AActor* actor) {
            const bool retired = retire(actor);
            return generation_failed(retired, retired ? "guest_starter_bag_refused_stock_contents" :
                "mutation_unverified_guest_starter_bag_retire_bag");
        };
        bool bag_ready{};
        auto* bag = spawn(type, bag_ready);
        if (!bag_ready) return refused_bag(bag);
        Unreal::FScriptArrayHelper stock_contents(contents, contents->ContainerPtrToValuePtr<void>(bag));
        std::array<Unreal::UClass*, max_rewards> rewards{};
        int reward_count = 0;
        if (stock_contents.Num() < 1 || stock_contents.Num() > max_rewards) return refused_bag(bag);
        for (int index = 0; index < stock_contents.Num(); ++index)
        {
            auto* value = entry->GetObjectPropertyValue(stock_contents.GetRawPtr(index));
            if (!value) continue;
            auto* reward = Unreal::Cast<Unreal::UClass>(value);
            if (!live(reward) || !reward->IsChildOf(row.item_base) || reward->GetPathName() == starter_bag_path ||
                reward->GetPathName() == hyper_bag_path) return refused_bag(bag);
            rewards[reward_count++] = reward;
        }
        if (!retire(bag)) return refuse("mutation_unverified_guest_starter_bag_retire_bag");
        if (reward_count == 0 || guest.Num() - 1 + reward_count > max_inventory)
            return generation_failed(true, "guest_starter_bag_refused_stock_contents");

        Unreal::FScriptArrayHelper destination(backpack, prepared.get());
        destination.RemoveValues(selected);
        std::string awarded;
        for (int index = 0; index < reward_count; ++index)
        {
            bool reward_ready{};
            auto* actor = spawn(rewards[index], reward_ready);
            const auto converted = Value(row_property->AllocateAndInitializeValue(), {row_property});
            const bool valid_row = reward_ready && converted &&
                silverfish::loot_escrow::convert_stock_item(scope.pawn, actor, converted.get());
            if (!retire(actor)) return refuse("mutation_unverified_guest_starter_bag_retire_reward");
            if (!valid_row) return generation_failed(true, "guest_starter_bag_refused_stock_reward");
            const auto guid = row.identity(converted.get());
            if (guid == Guid{} || guid == source || rows_contain(destination, guid) || rows_contain(host, guid) ||
                gear_contains(scope.guest, guid) || gear_contains(scope.stock, guid))
                return generation_failed(true, "guest_starter_bag_refused_reward_identity");
            const int inserted = destination.AddValue();
            row_property->CopyCompleteValue(destination.GetRawPtr(inserted), converted.get());
            if (!awarded.empty()) awarded.push_back(',');
            awarded += std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]);
        }
        if (!unchanged() || !backpack->Identical(guest_rows, before.get()))
            return generation_failed(true, "guest_starter_bag_refused_stock_contents");
        backpack->CopyCompleteValue(guest_rows, prepared.get());
        if (!unchanged() || !backpack->Identical(guest_rows, prepared.get()))
        {
            if (!unchanged()) return refuse("mutation_unverified_guest_starter_bag_commit");
            backpack->CopyCompleteValue(guest_rows, before.get());
            return generation_failed(true, "guest_starter_bag_refused_commit");
        }
        result(lua, true, "used");
        lua.set_string(bag_path == starter_bag_path ? "random" : "hyperrandom");
        lua.set_string(awarded);
        return 4;
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

        void* find_value(Unreal::FScriptMap* entries, const Unreal::FName& name) const
        {
            return entries->FindValue(&name, layout, hash(), identical_key());
        }

        int read(Unreal::FScriptMap* entries, const Unreal::FName& name) const
        {
            const auto* value = find_value(entries, name);
            return value ? number->GetPropertyValue(value) : 0;
        }

        int read(Unreal::FScriptMap* entries) const { return read(entries, flag); }

        bool write(Unreal::FScriptMap* entries, const Unreal::FName& name, int expected) const
        {
            if (!usable(entries) || name.IsNone()) return false;
            const int count = entries->Num();
            const bool existed = find_value(entries, name) != nullptr;
            auto* value = entries->FindOrAdd(&name, layout, hash(), identical_key(), [&](void* destination, void* data) {
                key->InitializeValue(destination);
                key->SetPropertyValue(destination, name);
                number->InitializeValue(data);
            });
            if (!value) return false;
            number->SetPropertyValue(value, expected);
            return entries->Num() == count + !existed && read(entries, name) == expected;
        }

        bool write(Unreal::FScriptMap* entries, int expected) const
        {
            return expected >= 0 && expected <= 100 && write(entries, flag, expected);
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
        auto* caller = frame.PreviousFrame();
        auto* active = meal && pawn == meal->pawn ? meal : nullptr;
        // A prepared meal has already checked the guest's positive intoxication branch.
        if (!active && (delta > 0 || delta < -100)) return;
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
        const auto stock_vitals = health.read(scope.stock);
        constexpr int test_intoxication = 42;
        if (health.max_health != 250.0 || health.before.health != 250.0 || health.before.regen != 250.0 ||
            !stock_vitals.finite() || !before || !expected || !stock_before ||
            !personal.write(static_cast<Unreal::FScriptMap*>(expected.get()), test_intoxication))
            return refusal("guest_mre_refused_fixture_snapshot");
        // Seed the isolated test frame before exercising normal stock item use.
        auto vitals = health.before;
        vitals.food = 17.0;
        vitals.health = 210.0;
        vitals.regen = 230.0;
        const auto write_baseline = [&](const Vitals& value) {
            health.food->SetPropertyValueInContainer(scope.guest, value.food);
            health.health->SetPropertyValueInContainer(scope.guest, value.health);
            health.regen->SetPropertyValueInContainer(scope.guest, value.regen);
        };
        const int pawn_before = food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn);
        personal.map->CopyCompleteValue(flags, expected.get());
        write_baseline(vitals);
        food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, test_intoxication);
        const auto safe_stock = [&] { return scope.current() && health.read(scope.stock) == stock_vitals &&
            canonical.map->Identical(stock_flags, stock_before.get()); };
        if (!safe_stock() || !personal.map->Identical(flags, expected.get()) || health.read(scope.guest) != vitals ||
            food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) != test_intoxication)
        {
            if (!safe_stock()) return refusal("mutation_unverified_guest_mre_fixture");
            personal.map->CopyCompleteValue(flags, before.get());
            write_baseline(health.before);
            food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, pawn_before);
            return refusal(safe_stock() && personal.map->Identical(flags, before.get()) && health.read(scope.guest) == health.before &&
                food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) == pawn_before
                ? "guest_mre_refused_fixture_rolled_back" : "mutation_unverified_guest_mre_fixture");
        }
        result(lua, true, "verified");
        lua.set_number(vitals.food);
        lua.set_integer(test_intoxication);
        lua.set_integer(canonical.read(stock_flags));
        return 5;
    }

    int stock_antibiotic_roll()
    {
        auto* library = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetMathLibrary"));
        auto* roll = find<Unreal::UFunction>(STR("/Script/Engine.KismetMathLibrary:RandomIntegerInRange"));
        auto* minimum = field<Unreal::FIntProperty>(roll, STR("Min"));
        auto* maximum = field<Unreal::FIntProperty>(roll, STR("Max"));
        auto* result = field<Unreal::FIntProperty>(roll, STR("ReturnValue"));
        if (!library || !roll || roll->GetOuterPrivate() != library->GetClassPrivate() || roll->GetNumParms() != 3 ||
            roll->GetParmsSize() != 12 || !minimum || !maximum || !result || result != roll->GetReturnProperty() ||
            minimum->GetOffset_Internal() != 0 || maximum->GetOffset_Internal() != 4 || result->GetOffset_Internal() != 8) return 0;
        std::array<std::int32_t, 3> values{1, 3, 0};
        library->ProcessEvent(roll, values.data());
        return values[2];
    }

    int use_meal(const Lua& lua)
    {
        if (lua.get_stack_size() != 5 || !lua.is_string(5)) return result(lua, false, "guest_meal_refused_arguments");
        const auto entry = lua.get_string(5);
        const auto& consumables = silverfish::native_guest_consumable::consumables;
        const auto found = std::ranges::find(consumables, entry, &Consumable::entry);
        if (found == consumables.end()) return result(lua, false, "guest_meal_refused_profile");
        const auto& profile = *found;
        std::string kind(profile.entry.substr(4));
        std::ranges::replace(kind, '-', '_');
        const auto refuse = [&](std::string_view reason) {
            return result(lua, false, std::string("guest_").append(kind).append("_refused_").append(reason));
        };
        const auto unverified = [&](std::string_view reason) {
            return result(lua, false, std::string("mutation_unverified_guest_").append(kind).append("_").append(reason));
        };
        if (!food_available() || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || meal) return refuse("arguments");
        const auto pawn_address = lua.get_integer(), guest_address = lua.get_integer(), stock_address = lua.get_integer();
        Guid guid{};
        if (pawn_address <= 0 || guest_address <= 0 || stock_address <= 0 || guest_address == stock_address ||
            !parse_guid(lua.get_string(), guid)) return refuse("identity");
        Scope scope;
        if (!scope.bind(pawn_address, guest_address, stock_address)) return refuse("exact_detached_save");
        Inventory inventory;
        if (!inventory.locate(scope, guid, profile).empty()) return refuse("stock_owned_stack");
        Health health;
        if (!health.bind(scope).empty() || health.max_health > 10'000)
            return refuse("vitals_or_traits");

        std::int32_t gourmand{}, healthy_eating{}, bonus{};
        if (!silverfish::native_enemy_loot::count_save_passive(
                scope.guest, Unreal::FName{STR("Gourmand"), Unreal::FNAME_Find}, gourmand) ||
            !silverfish::native_enemy_loot::count_save_passive(
                scope.guest, Unreal::FName{STR("HealthyEating"), Unreal::FNAME_Find}, healthy_eating) ||
            ((gourmand || healthy_eating) &&
                !silverfish::native_guest_sickness::read_passive_amplification(scope.guest, bonus)))
            return refuse("food_traits");
        gourmand += gourmand > 0 ? bonus : 0;
        healthy_eating += healthy_eating > 0 ? bonus : 0;
        // The stock HealthItem graph uses the effective food amount for HealthyEating.
        const double appetite = profile.food * (1.0 - static_cast<double>(gourmand) * 0.2);
        const double regen = profile.regen_gain + static_cast<double>(healthy_eating) * appetite * 0.3;
        const int delta = static_cast<int>(appetite * -0.1);
        if (delta > 0)
        {
            std::int32_t drunk_power{};
            if (!silverfish::native_enemy_loot::count_save_passive(
                    scope.guest, Unreal::FName{STR("DrunkPower"), Unreal::FNAME_Find}, drunk_power) || drunk_power)
                return refuse("positive_intoxication_effect");
        }

        Intoxication personal, canonical;
        if (!personal.bind(scope.guest) || !canonical.bind(scope.stock) || personal.map != canonical.map)
            return refuse("intoxication_schema");
        auto* guest_flags = personal.data(scope.guest);
        auto* host_flags = canonical.data(scope.stock);
        const Unreal::FName sickness{STR("Sickness"), Unreal::FNAME_Find};
        if (sickness.IsNone()) return refuse("sickness_schema");
        // Honey Egg's stock ApplyItemEffects adds fatigue through the global save.
        // Apply its audited Add_PlayerFatigue result to this guest's own flag map.
        const Unreal::FName fatigue{STR("Player_Fatigue"), Unreal::FNAME_Find};
        int fatigue_before{}, fatigue_after{}, fatigue_minimum{};
        if (profile.fatigue)
        {
            std::int32_t trauma{};
            if (fatigue.IsNone() || !silverfish::native_enemy_loot::count_save_passive(
                    scope.guest, Unreal::FName{STR("LingeringTrauma"), Unreal::FNAME_Find}, trauma))
                return refuse("fatigue_schema");
            fatigue_before = personal.read(guest_flags, fatigue);
            if (fatigue_before < 0 || fatigue_before > 100) return refuse("fatigue_value");
            fatigue_minimum = trauma > 0 ? 15 : 0;
            fatigue_after = std::clamp(fatigue_before + profile.fatigue, fatigue_minimum, 100);
        }
        const auto sickness_before = personal.read(guest_flags, sickness);
        std::int32_t sickness_after = sickness_before;
        int dose = profile.infection;
        const Unreal::FName resistance{STR("Use_AntiBio"), Unreal::FNAME_Find};
        int resistance_before{}, resistance_after{};
        if (profile.antibiotic)
        {
            if (resistance.IsNone()) return refuse("antibiotic_schema");
            resistance_before = personal.read(guest_flags, resistance);
            if (resistance_before < 0 || resistance_before > INT32_MAX - 3)
                return refuse("antibiotic_resistance");
            const auto increase = stock_antibiotic_roll();
            if (increase < 1 || increase > 3) return refuse("antibiotic_roll");
            resistance_after = resistance_before + increase;
            dose += resistance_before;
        }
        if (dose > 0)
        {
            std::int32_t iron_gut{};
            if (!silverfish::native_enemy_loot::count_save_passive(
                    scope.guest, Unreal::FName{STR("IronGut"), Unreal::FNAME_Find}, iron_gut))
                return refuse("infection_passive");
            if (iron_gut) dose = 0;
        }
        if (dose && (!silverfish::native_guest_sickness::food_infection(
                scope.pawn, scope.guest, scope.stock, dose, sickness_after, false) || sickness_after != sickness_before))
            return refuse("infection_schema");

        auto* bag = inventory.backpack;
        auto* guest_rows = inventory.bag_data(scope.guest);
        auto* host_rows = inventory.bag_data(scope.stock);
        const int intox_before = personal.read(guest_flags), pawn_before = food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn);
        const int host_intox = canonical.read(host_flags);
        const auto host_vitals = health.read(scope.stock);
        if (intox_before < 0 || intox_before > 100 || host_intox < 0 || host_intox > 100 ||
            pawn_before < 0 || pawn_before > 100 || !host_vitals.finite()) return refuse("intoxication_values");

        const auto backup_flags = copy_value(personal.map, guest_flags);
        const auto expected_flags = copy_value(personal.map, guest_flags);
        const auto stock_flags = copy_value(canonical.map, host_flags);
        const auto backup_bag = copy_value(bag, guest_rows);
        const auto expected_bag = copy_value(bag, guest_rows);
        const auto stock_bag = copy_value(bag, host_rows);
        const auto cache = copy_value(health.cache, health.cache->ContainerPtrToValuePtr<void>(scope.instance));
        if (!backup_flags || !expected_flags || !stock_flags || !backup_bag || !expected_bag || !stock_bag || !cache)
            return refuse("snapshot");

        auto expected = health.expected(regen, profile.instant, profile.stim);
        expected.food = std::clamp(health.before.food + appetite, 0.0, 200.0);
        const int intox_after = std::clamp(intox_before + delta, 0, 100);
        if (!personal.write(static_cast<Unreal::FScriptMap*>(expected_flags.get()), intox_after))
            return refuse("prepared_intoxication");
        const int remaining = inventory.units - 1;
        Unreal::FScriptArrayHelper prepared(bag, expected_bag.get());
        if (remaining == 0) prepared.RemoveValues(inventory.selected);
        else inventory.row.set_remaining(prepared.GetRawPtr(inventory.selected), remaining, profile);

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
            health.write_healing(scope.guest, health.before);
            food_functions.pawn_intox->SetPropertyValueInContainer(scope.pawn, pawn_before);
            return stock_unchanged() && health.read(scope.guest) == health.before && bag->Identical(guest_rows, backup_bag.get()) &&
                personal.map->Identical(guest_flags, backup_flags.get()) &&
                food_functions.pawn_intox->GetPropertyValueInContainer(scope.pawn) == pawn_before;
        };

        DuringMeal receipt{scope.pawn, delta};
        {
            ScopedMeal scope_call{receipt};
            double food = appetite;
            scope.pawn->ProcessEvent(food_functions.effect, &food);
        }

        auto only_food = health.before;
        only_food.food = expected.food;
        if (receipt.call_count != 1 || !receipt.applied || !prove(backup_bag.get(), only_food))
            return rollback(false, only_food) ? refuse("food_rolled_back") : unverified("food");
        health.write_healing(scope.guest, expected);
        if (!prove(backup_bag.get(), expected))
            return rollback(false, only_food) ? refuse("regen_rolled_back") : unverified("regen");
        if (dose)
        {
            // Keep the stock infection roll and update the expected copy used for the atomic readback.
            if (!silverfish::native_guest_sickness::food_infection(
                    scope.pawn, scope.guest, scope.stock, dose, sickness_after, true) ||
                (sickness_after != sickness_before &&
                    !personal.write(static_cast<Unreal::FScriptMap*>(expected_flags.get()), sickness, sickness_after)) ||
                !prove(backup_bag.get(), expected)) return unverified("infection");
        }
        if (profile.antibiotic &&
            (!personal.write(static_cast<Unreal::FScriptMap*>(expected_flags.get()), resistance, resistance_after) ||
             !personal.write(guest_flags, resistance, resistance_after) || !prove(backup_bag.get(), expected)))
            return unverified("antibiotic_resistance");
        if (profile.fatigue &&
            (!personal.write(static_cast<Unreal::FScriptMap*>(expected_flags.get()), fatigue, fatigue_after) ||
             !personal.write(guest_flags, fatigue, fatigue_after) || !prove(backup_bag.get(), expected)))
            return unverified("fatigue");
        bag->CopyCompleteValue(guest_rows, expected_bag.get());
        if (!prove(expected_bag.get(), expected))
            return rollback(true, expected) ? refuse("debit_rolled_back") : unverified("debit");

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
        lua.set_integer(gourmand);
        lua.set_integer(healthy_eating);
        lua.set_integer(sickness_before);
        lua.set_integer(sickness_after);
        lua.set_integer(dose);
        if (profile.medicine)
        {
            lua.set_number(expected.health);
            lua.set_number(health.before.stim);
            lua.set_number(expected.stim);
            if (profile.antibiotic)
            {
                lua.set_integer(resistance_before);
                lua.set_integer(resistance_after);
                return 24;
            }
            return 22;
        }
        if (profile.fatigue)
        {
            lua.set_integer(fatigue_before);
            lua.set_integer(fatigue_after);
            lua.set_integer(fatigue_minimum);
            return 22;
        }
        return 19;
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
    if (enabled())
    {
        lua.register_function("SilverfishUseNativeGuestBandage", use);
        lua.register_function("SilverfishOpenNativeGuestStarterBag", open_starter_bag);
    }
    if (mre_enabled())
    {
        lua.register_function("SilverfishPrepareNativeGuestMre", prepare_food);
        lua.register_function("SilverfishUseNativeGuestMeal", use_meal);
        if (opt_in("SILVERFISH_COOP_HEADLESS") && opt_in("SILVERFISH_COOP_NATIVE_FIXTURES"))
            lua.register_function("SilverfishSeedNativeGuestMreFixture", seed_mre_test);
    }
}
