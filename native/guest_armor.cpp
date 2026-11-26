#include "guest_armor.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <string>
#include <string_view>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto item_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto armor_base = STR("/Game/Classes/Armor_Items/Item_ArmorMaster_BP.Item_ArmorMaster_BP_C");
    constexpr auto helmet_base = STR("/Game/Classes/Armor_Items/Item_HelmetMaster_BP.Item_HelmetMaster_BP_C");
    constexpr auto armor_type = STR("/Game/Classes/Libraries/Structs/Armor_Struct.Armor_Struct");
    constexpr auto helmet_type = STR("/Game/Classes/Libraries/Structs/Helmet_Struct.Helmet_Struct");
    constexpr auto faction_type = STR("/Game/Classes/Libraries/Enums_Lib/Factions_Enum.Factions_Enum");
    constexpr auto configure_disguise = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Configure_Disguise");
    constexpr std::uint8_t state_faction = 4;
    constexpr auto damage_type = STR("/Game/Classes/Libraries/Structs/Damage_Struct.Damage_Struct");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto factory_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeDefaultItem");
    constexpr auto resistance = STR("Resistance_2_2E35E4354FE2AD598EB8A48221FD001E");
    constexpr auto armor_weight = STR("Weight_36_1CC3CC9C4DAD1ADFEBC9D5B45765C95D");
    constexpr auto carry_bonus = STR("CaryBonus_43_3DF7AA974A13A7ADF22D3BA63B29FEF2");
    constexpr auto helmet_light = STR("FlashLight_41_E2EDC7A04D5942755A28FE9479BE0793");
    constexpr auto helmet_hud = STR("FullHUD_39_6852FE6E4BFD0ADB2A5474983720198A");
    constexpr auto helmet_visibility = STR("VisibilityMod_13_E33F6D50463CE2650C61E29B5C66545D");
    constexpr auto helmet_light_origin = STR("LightLocation_47_C3B1387F4CE0EF84D4C4CB996C535017");
    constexpr std::array guid_words{STR("A"), STR("B"), STR("C"), STR("D")};
    struct ResistanceField
    {
        const U::TCHAR* name;
        std::string_view refusal;
    };
    constexpr std::array damage_fields{
        ResistanceField{STR("Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5"), "native_guest_armor_kinetic_unqualified"},
        ResistanceField{STR("ArmorPenitration_4_E48DD35840D3699D8F37E48678EF29E4"), "native_guest_armor_penetration_unqualified"},
        ResistanceField{STR("BluntForce_6_6923B3964740DE281BD773BCCAAC2772"), "native_guest_armor_blunt_unqualified"},
        ResistanceField{STR("Stabbing_8_64710D2D4AB4232B4DD604AC3CD0219C"), "native_guest_armor_stabbing_unqualified"},
        ResistanceField{STR("Slashing_10_58FFFE444BD41CE5D15EAA80BC0D9DF1"), "native_guest_armor_slash_unqualified"},
        ResistanceField{STR("Fire_12_2B415A724C533C075B6BB390B76BF247"), "native_guest_armor_fire_unqualified"},
        ResistanceField{STR("Poison_14_68E96E324AD705A7F95362BBC781E220"), "native_guest_armor_poison_unqualified"},
        ResistanceField{STR("Electric_16_8F945E8C491C5825EC19E3837E266D48"), "native_guest_armor_electric_unqualified"},
        ResistanceField{STR("Radiation_18_E8FC71B2490E3C6050B6E999C65A13CB"), "native_guest_armor_radiation_unqualified"},
        ResistanceField{STR("Acid_20_F4C551B34F620FDF72D2F2A64DE15F3E"), "native_guest_armor_acid_unqualified"},
        ResistanceField{STR("OverPressure_22_3355FA8E45511902B20DD69D844D5020"), "native_guest_armor_overpressure_unqualified"},
    };
    struct ArmorProfile
    {
        const U::TCHAR* path;
        std::string_view name;
        double weight, noise, visibility, bonus;
        int value;
        std::array<double, damage_fields.size()> damage;
        bool helmet{};
        const U::TCHAR* vox{};
        std::basic_string_view<U::TCHAR> passive{};
        std::uint8_t disguise_faction{};
        std::uint8_t flashlight{};
        std::array<double, 3> light_origin{0.0, 0.0, 170.0};
    };
    constexpr std::array armor_profiles{
        ArmorProfile{STR("/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"), "bomber",
            0.5, 0.9, 0.9, 5.0, 325, {0.0, 0.0, 0.0, 0.1, 0.2, 0.1, 0.0, 0.1, 0.0, 0.2, 0.0}},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"), "hazop",
            2.0, 1.2, 0.9, 12.0, 1400, {0.25, 0.0, 0.1, 0.3, 0.2, 0.2, 0.1, 0.15, 0.25, 0.2, 0.1}},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"), "dust-filter",
            0.4, 1.0, 1.0, 0.0, 80, {0.0, 0.0, 0.0, 0.0, 0.1, 0.1, 0.5, 0.0, 0.0, 0.2, 0.0}, true,
            STR("/Game/Assets/Sounds/AudioEffects/GasMask_Filter_01.GasMask_Filter_01")},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Helm_Basic.Helm_Basic_C"), "basic-helmet",
            1.4, 1.0, 1.0, 0.0, 250, {0.2, 0.0, 0.2, 0.1, 0.1, 0.0, 0.0, 0.0, 0.0, 0.1, 0.1}, true},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Helm_Goggles.Helm_Goggles_C"), "goggles",
            0.1, 1.0, 1.0, 0.0, 30, {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.0}, true},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C"), "hunter-jacket",
            1.2, 0.9, 0.75, 5.0, 250, {0.0, 0.0, 0.05, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.05, 0.0}},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"), "hazard-suit",
            10.5, 1.6, 1.2, 8.0, 5200, {0.15, 0.0, 0.2, 0.15, 0.3, 0.5, 0.1, 0.5, 0.4, 0.5, 0.25},
            false, nullptr, STR("Crafting")},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Armor_ConscriptUniform.Armor_ConscriptUniform_C"), "conscript-uniform",
            1.8, 1.1, 0.9, 8.0, 800, {0.05, 0.0, 0.1, 0.05, 0.15, 0.2, 0.1, 0.15, 0.0, 0.2, 0.05},
            false, nullptr, {}, state_faction},
        ArmorProfile{STR("/Game/Classes/Armor_Items/Helm_StandardInf.Helm_StandardInf_C"), "standard-infantry-helmet",
            2.4, 1.0, 1.0, 0.0, 2500, {0.15, 0.0, 0.2, 0.1, 0.1, 0.1, 0.8, 0.1, 0.2, 0.2, 0.1}, true,
            STR("/Game/Assets/Sounds/AudioEffects/GasMask_Filter_02.GasMask_Filter_02"), {}, state_faction, 2,
            {11.287835, -14.388264, 168.1}},
    };
    constexpr auto& bomber = armor_profiles[0];
    constexpr std::size_t slashing = 4, poison = 6, acid = 9;
    constexpr int max_items = 4096;
    constexpr int max_clients = 4;
    constexpr auto vox_class = STR("/Script/Engine.SoundEffectSourcePresetChain");

    bool env(const char* name)
    {
        std::array<char, 2> buf{};
        std::size_t size{};
        return getenv_s(&size, buf.data(), buf.size(), name) == 0 && size == buf.size() && buf[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_ZONE"); }
    bool fixtures() { return env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_FIXTURES"); }

    template <typename T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = field<U::FObjectProperty>(owner, name);
        return prop ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* ptr)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return ptr && !ptr->IsUnreachable() &&
            !ptr->HasAnyFlags(excluded);
    }

    bool exact(U::UObject* ptr, U::UClass* type) { return live(ptr) && type && ptr->GetClassPrivate() == type; }

    bool boolean(U::UObject* obj, const U::TCHAR* name, bool expected)
    {
        auto* prop = field<U::FBoolProperty>(obj, name);
        return prop && prop->GetPropertyValueInContainer(obj) == expected;
    }

    bool authority(U::UObject* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool read_guid(std::string_view text, Guid& guid)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char next) {
            return (next >= '0' && next <= '9') || (next >= 'A' && next <= 'F');
        })) return false;
        for (std::size_t i = 0; i < guid.size(); ++i)
        {
            const auto* first = text.data() + i * 8;
            if (std::from_chars(first, first + 8, guid[i], 16).ec != std::errc{}) return false;
        }
        return guid != Guid{};
    }

    struct Scope
    {
        U::UObject *pawn{}, *guest{}, *stock{}, *instance{}, *controller{}, *connection{};
        U::UWorld* world{};

        bool current() const
        {
            if (!live(world) || !authority(pawn, world) || !authority(controller, world) ||
                !live(guest) || !live(stock) || guest == stock || !live(instance) || !live(connection) ||
                object(pawn, STR("Save")) != guest || object(world, STR("OwningGameInstance")) != instance ||
                object(pawn, STR("As Game Instance FP")) != instance || object(instance, STR("SaveSlot_Ref")) != stock ||
                object(pawn, STR("Controller")) != controller || object(controller, STR("Pawn")) != pawn ||
                object(controller, STR("Player")) != connection) return false;
            auto* driver = object(world, STR("NetDriver"));
            auto* clients = field<U::FArrayProperty>(driver, STR("ClientConnections"));
            auto* entry = clients ? U::CastField<U::FObjectProperty>(clients->GetInner()) : nullptr;
            auto* net_connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
            if (!live(driver) || !entry || !net_connection || !connection->IsA(net_connection) ||
                !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
                object(driver, STR("World")) != world || object(connection, STR("Driver")) != driver ||
                object(connection, STR("PlayerController")) != controller || object(connection, STR("OwningActor")) != controller)
                return false;
            U::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(driver));
            if (remotes.Num() < 1 || remotes.Num() > max_clients) return false;
            int matched = 0;
            for (int i = 0; i < remotes.Num(); ++i)
            {
                auto* remote = entry->GetObjectPropertyValue(remotes.GetRawPtr(i));
                if (!live(remote) || !remote->IsA(net_connection)) return false;
                if (remote == connection) ++matched;
                else if (object(object(object(remote, STR("PlayerController")), STR("Pawn")), STR("Save")) == guest)
                    return false;
            }
            return matched == 1;
        }

        bool bind(std::uintptr_t pawn_address, std::uintptr_t save_address)
        {
            auto* klass = find<U::UClass>(pawn_path);
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (reinterpret_cast<std::uintptr_t>(candidate) != pawn_address) return RC::LoopAction::Continue;
                if (exact(candidate, klass)) pawn = candidate;
                return RC::LoopAction::Break;
            });
            if (!pawn) return false;
            world = static_cast<U::AActor*>(pawn)->GetWorld();
            guest = object(pawn, STR("Save"));
            instance = object(world, STR("OwningGameInstance"));
            stock = object(instance, STR("SaveSlot_Ref"));
            controller = object(pawn, STR("Controller"));
            connection = object(controller, STR("Player"));
            auto* save_class = find<U::UClass>(save_path);
            return reinterpret_cast<std::uintptr_t>(guest) == save_address && exact(guest, save_class) &&
                exact(stock, save_class) && exact(instance, find<U::UClass>(instance_path)) &&
                exact(controller, find<U::UClass>(controller_path)) && current();
        }
    };

    // Engine-owned property copies preserve the GUID, data tag and InstancedStruct payload.
    struct Copy
    {
        U::FProperty* prop;
        void* value;

        explicit Copy(U::FProperty* property, const void* source = nullptr)
            : prop(property), value(property->AllocateAndInitializeValue())
        {
            if (value && source) prop->CopyCompleteValue(value, source);
        }
        Copy(const Copy&) = delete;
        Copy& operator=(const Copy&) = delete;
        ~Copy() { if (value) prop->DestroyAndFreeValue(value); }
        bool matches(const void* target) const { return value && prop->Identical(value, target); }
        void restore(void* target) const { prop->CopyCompleteValue(target, value); }
    };

    struct Inventory
    {
        U::FArrayProperty *backpack{}, *equipment{};
        U::FStructProperty *item{}, *armor{}, *helmet{}, *guid{};
        U::FClassProperty* klass{};
        U::FDoubleProperty* weight{};
        std::array<U::FIntProperty*, 4> words{};

        bool bind(U::UObject* save)
        {
            backpack = field<U::FArrayProperty>(save, STR("PlayerBackpack"));
            equipment = field<U::FArrayProperty>(save, STR("PlayerEquipment"));
            armor = field<U::FStructProperty>(save, STR("PlayerArmor"));
            helmet = field<U::FStructProperty>(save, STR("PlayerHelmet"));
            item = backpack ? U::CastField<U::FStructProperty>(backpack->GetInner()) : nullptr;
            auto* equipped = equipment ? U::CastField<U::FStructProperty>(equipment->GetInner()) : nullptr;
            auto* type = find<U::UScriptStruct>(item_path);
            if (!type || !item || !equipped || !armor || !helmet || item->GetStruct().Get() != type ||
                equipped->GetStruct().Get() != type || armor->GetStruct().Get() != type || helmet->GetStruct().Get() != type)
                return false;
            klass = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
            weight = field<U::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
            guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            if (!klass || !weight || !guid_type || !guid || guid->GetStruct().Get() != guid_type) return false;
            for (std::size_t i = 0; i < words.size(); ++i)
                if (!(words[i] = field<U::FIntProperty>(guid_type, guid_words[i]))) return false;
            return true;
        }

        U::UObject* item_class(const void* row) const
        {
            return klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(row));
        }

        Guid item_guid(const void* row) const
        {
            Guid result{};
            const auto* value = guid->ContainerPtrToValuePtr<void>(row);
            for (std::size_t i = 0; i < words.size(); ++i)
                result[i] = static_cast<std::uint32_t>(words[i]->GetPropertyValueInContainer(value));
            return result;
        }

        bool locate(U::UObject* save, const Guid& wanted, int& backpack_index, bool& worn, bool head = false) const
        {
            U::FScriptArrayHelper bag(backpack, backpack->ContainerPtrToValuePtr<void>(save));
            U::FScriptArrayHelper gear(equipment, equipment->ContainerPtrToValuePtr<void>(save));
            if (bag.Num() < 0 || bag.Num() > max_items || gear.Num() < 0 || gear.Num() > max_items) return false;
            if (item_guid((head ? armor : helmet)->ContainerPtrToValuePtr<void>(save)) == wanted) return false;
            for (int i = 0; i < gear.Num(); ++i)
                if (item_guid(gear.GetRawPtr(i)) == wanted) return false;
            worn = item_guid((head ? helmet : armor)->ContainerPtrToValuePtr<void>(save)) == wanted;
            backpack_index = -1;
            for (int i = 0; i < bag.Num(); ++i)
            {
                if (item_guid(bag.GetRawPtr(i)) != wanted) continue;
                if (worn || backpack_index >= 0) return false;
                backpack_index = i;
            }
            return true;
        }

        bool carry(U::UObject* saved, const void* prepared_bag, double worn_weight, double helmet_weight, double& result) const
        {
            U::FScriptArrayHelper bag(backpack, prepared_bag);
            U::FScriptArrayHelper gear(equipment, equipment->ContainerPtrToValuePtr<void>(saved));
            result = worn_weight * 0.5 + helmet_weight * 0.5;
            const auto add = [&](const void* row) {
                const auto amount = weight->GetPropertyValueInContainer(row);
                if (!std::isfinite(amount) || amount < 0.0) return false;
                result += amount;
                return true;
            };
            for (int i = 0; i < bag.Num(); ++i)
                if (!add(bag.GetRawPtr(i))) return false;
            for (int i = 0; i < gear.Num(); ++i)
            {
                const auto* row = gear.GetRawPtr(i);
                if (item_class(row) && item_guid(row) != Guid{} && !add(row)) return false;
            }
            return std::isfinite(result);
        }
    };

    const ArmorProfile* profile_for(U::UObject* klass)
    {
        if (!klass) return nullptr;
        for (const auto& profile : armor_profiles)
            if (klass == find<U::UClass>(profile.path)) return &profile;
        return nullptr;
    }

    bool faction_property(U::FByteProperty* property)
    {
        auto* type = property ? property->GetEnum().Get() : nullptr;
        return type && type->GetPathName() == faction_type &&
            type->GetNameByValue(state_faction).ToString() == STR("Factions_Enum::NewEnumerator4");
    }

    U::UObject* armor_defaults(const ArmorProfile& profile)
    {
        auto* klass = find<U::UClass>(profile.path);
        auto* base = find<U::UClass>(profile.helmet ? helmet_base : armor_base);
        auto* cdo = klass ? klass->GetClassDefaultObject().Get() : nullptr;
        auto* type = field<U::FByteProperty>(cdo, STR("ItemType"));
        auto* value = field<U::FIntProperty>(cdo, STR("Value"));
        auto* disguise = field<U::FByteProperty>(cdo, STR("DisguiseFaction"));
        auto* effects = field<U::FArrayProperty>(cdo, STR("PassiveEffects"));
        if (!base || !cdo || cdo->GetClassPrivate() != klass || !klass->IsChildOf(base) ||
            !cdo->HasAnyFlags(U::RF_ClassDefaultObject) || !type ||
            type->GetPropertyValueInContainer(cdo) != (profile.helmet ? 6 : 7) ||
            !value || value->GetPropertyValueInContainer(cdo) != profile.value || !effects ||
            !boolean(cdo, STR("Stacks"), false)) return nullptr;
        if (!profile.helmet && (!disguise || disguise->GetPropertyValueInContainer(cdo) != profile.disguise_faction ||
            (profile.disguise_faction && !faction_property(disguise)) ||
            !field<U::FClassProperty>(cdo, STR("PowerArmor")) || object(cdo, STR("PowerArmor")))) return nullptr;
        if (profile.helmet)
        {
            auto* factions = field<U::FArrayProperty>(cdo, STR("DisguisableFactions"));
            auto* reputation = field<U::FMapProperty>(cdo, STR("RepBonus"));
            auto* chain_type = find<U::UClass>(vox_class);
            auto* vox_property = field<U::FObjectProperty>(cdo, STR("VoxEffect"));
            auto* voice = object(cdo, STR("VoxEffect"));
            if (!factions || !reputation || !chain_type || !vox_property ||
                vox_property->GetPropertyClass().Get() != chain_type ||
                (profile.vox ? !exact(voice, chain_type) || voice->GetPathName() != profile.vox : voice != nullptr))
                return nullptr;
            U::FScriptArrayHelper list(factions, factions->ContainerPtrToValuePtr<void>(cdo));
            if (list.Num() < 0 || list.Num() > 256 || reputation->ContainerPtrToValuePtr<U::FScriptMap>(cdo)->Num())
                return nullptr;
            if (profile.disguise_faction || list.Num())
            {
                auto* faction = U::CastField<U::FByteProperty>(factions->GetInner());
                if (!faction_property(faction) || faction->GetArrayDim() != 1 || faction->GetOffset_Internal() != 0)
                    return nullptr;
                if (profile.disguise_faction && (list.Num() != 1 ||
                    faction->GetPropertyValueInContainer(list.GetRawPtr()) != profile.disguise_faction)) return nullptr;
            }
        }
        U::FScriptArrayHelper passives(effects, effects->ContainerPtrToValuePtr<void>(cdo));
        if (profile.passive.empty()) return passives.Num() == 0 ? cdo : nullptr;
        auto* text = U::CastField<U::FStrProperty>(effects->GetInner());
        if (passives.Num() != 1 || !text || text->GetArrayDim() != 1 || text->GetOffset_Internal() != 0)
            return nullptr;
        const auto& passive = text->GetPropertyValue(passives.GetRawPtr(0));
        return passive.Len() == static_cast<int>(profile.passive.size()) &&
            std::basic_string_view<U::TCHAR>{*passive, profile.passive.size()} == profile.passive ? cdo : nullptr;
    }


    struct RuntimeGear
    {
        std::basic_string<U::TCHAR> path, voice_path;
        ArmorProfile profile{};
    };

    // This is the stock stat projection for ordinary clothing and helmets. Gear with
    // dedicated behavior must have a matching projection before it can be equipped.
    const ArmorProfile* qualify_profile(U::UObject* item_class, bool head, RuntimeGear& storage)
    {
        if (const auto* named = profile_for(item_class)) return named->helmet == head ? named : nullptr;
        auto* klass = U::Cast<U::UClass>(item_class);
        auto* base = find<U::UClass>(head ? helmet_base : armor_base);
        if (!klass || !base || klass == base || !klass->IsChildOf(base)) return nullptr;
        auto* defaults = klass->GetClassDefaultObject().Get();
        auto& profile = storage.profile;
        profile.helmet = head;
        storage.path = klass->GetPathName();
        profile.path = storage.path.c_str();
        if (!head && storage.path == STR("/Game/Classes/Armor_Items/Armor_ExpiditionLight.Armor_ExpiditionLight_C"))
            profile.passive = STR("HeavyHolster");
        auto* value = field<U::FIntProperty>(defaults, STR("Value"));
        if (!value) return nullptr;
        profile.value = value->GetPropertyValueInContainer(defaults);
        const auto read_number = [](U::UObject* owner, const void* data, const U::TCHAR* name, double& result) {
            auto* property = field<U::FDoubleProperty>(owner, name);
            if (!property) return false;
            result = property->GetPropertyValueInContainer(data);
            return std::isfinite(result);
        };
        if (!read_number(defaults, defaults, STR("Weight"), profile.weight) || profile.weight < 0.0 ||
            !read_number(defaults, defaults, STR("VisibilityMod"), profile.visibility)) return nullptr;
        if (head)
        {
            auto* light = field<U::FByteProperty>(defaults, STR("FlashLightType"));
            auto* origin = field<U::FStructProperty>(defaults, STR("FlashlightLocation"));
            auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
            if (!light || !origin || !vector || origin->GetStruct().Get() != vector) return nullptr;
            profile.flashlight = light->GetPropertyValueInContainer(defaults);
            const auto* position = origin->ContainerPtrToValuePtr<void>(defaults);
            if (!read_number(vector, position, STR("X"), profile.light_origin[0]) ||
                !read_number(vector, position, STR("Y"), profile.light_origin[1]) ||
                !read_number(vector, position, STR("Z"), profile.light_origin[2])) return nullptr;
            if (auto* voice = object(defaults, STR("VoxEffect")))
            {
                storage.voice_path = voice->GetPathName();
                profile.vox = storage.voice_path.c_str();
            }
        }
        else if (!read_number(defaults, defaults, STR("NoiseMod"), profile.noise) ||
                 !read_number(defaults, defaults, STR("CarryWeight_Bonus"), profile.bonus) ||
                 20.0 + profile.bonus < 0.0)
        {
            return nullptr;
        }

        auto* resist = field<U::FStructProperty>(defaults, STR("Resistance"));
        auto* type = find<U::UScriptStruct>(damage_type);
        if (!resist || !type || resist->GetStruct().Get() != type) return nullptr;
        const auto* damage = resist->ContainerPtrToValuePtr<void>(defaults);
        for (std::size_t i = 0; i < damage_fields.size(); ++i)
            if (!read_number(type, damage, damage_fields[i].name, profile.damage[i]) || profile.damage[i] < 0.0)
                return nullptr;
        return &profile;
    }

    bool stock_gear_row(const Inventory& inventory, const void* row, U::UObject* cdo, const ArmorProfile& profile)
    {
        auto* type = inventory.item->GetStruct().Get();
        auto* rounds = field<U::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        auto* default_rounds = field<U::FIntProperty>(cdo, STR("Ammo"));
        auto* value = field<U::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
        auto* tag = field<U::FNameProperty>(type, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
        auto* default_tag = field<U::FNameProperty>(cdo, STR("DataTag"));
        auto* payload = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        auto* default_payload = field<U::FStructProperty>(cdo, STR("DataStruct"));
        auto* structure = find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
        auto* never = field<U::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        auto* default_never = field<U::FBoolProperty>(cdo, STR("NeverDrop"));
        return rounds && default_rounds && default_rounds->GetPropertyValueInContainer(cdo) == 1 &&
            rounds->GetPropertyValueInContainer(row) == 1 && value && value->GetPropertyValueInContainer(row) == profile.value &&
            tag && default_tag && tag->GetPropertyValueInContainer(row) == default_tag->GetPropertyValueInContainer(cdo) &&
            payload && default_payload && structure && payload->GetStruct().Get() == structure &&
            default_payload->GetStruct().Get() == structure &&
            payload->Identical(payload->ContainerPtrToValuePtr<void>(row), default_payload->ContainerPtrToValuePtr<void>(cdo)) &&
            never && default_never && never->GetPropertyValueInContainer(row) == default_never->GetPropertyValueInContainer(cdo);
    }

    bool isolated_rows(const Scope& scope, const Inventory& inventory, const Guid& selected, const Guid& replaced = {})
    {
        auto* guest_data = inventory.backpack->ContainerPtrToValuePtr<void>(scope.guest);
        U::FScriptArrayHelper guest_bag(inventory.backpack, guest_data);
        auto* guest_buffer = guest_bag.Num() ? guest_bag.GetRawPtr() : nullptr;
        const auto isolated = [&](U::UObject* save, bool guest) {
            for (auto* property : {inventory.backpack, inventory.equipment})
            {
                if (guest && property == inventory.backpack) continue;
                auto* data = property->ContainerPtrToValuePtr<void>(save);
                U::FScriptArrayHelper rows(property, data);
                if (rows.Num() < 0 || rows.Num() > max_items || data == guest_data ||
                    (guest_buffer && rows.Num() && rows.GetRawPtr() == guest_buffer)) return false;
            }
            if (guest) return true;
            int index{};
            bool worn{};
            return inventory.locate(save, selected, index, worn) && index < 0 && !worn &&
                (replaced == Guid{} || (inventory.locate(save, replaced, index, worn) && index < 0 && !worn));
        };
        if (!isolated(scope.guest, true) || !isolated(scope.stock, false)) return false;
        auto* driver = object(scope.world, STR("NetDriver"));
        auto* connections = field<U::FArrayProperty>(driver, STR("ClientConnections"));
        auto* entry = connections ? U::CastField<U::FObjectProperty>(connections->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper clients(connections, connections->ContainerPtrToValuePtr<void>(driver));
        if (clients.Num() < 1 || clients.Num() > max_clients) return false;
        for (int i = 0; i < clients.Num(); ++i)
        {
            auto* connection = entry->GetObjectPropertyValue(clients.GetRawPtr(i));
            auto* saved = object(object(object(connection, STR("PlayerController")), STR("Pawn")), STR("Save"));
            if (connection == scope.connection || !saved || saved == scope.stock) continue;
            if (saved == scope.guest || !exact(saved, scope.stock->GetClassPrivate()) || !isolated(saved, false)) return false;
        }
        return true;
    }

    struct Projection
    {
        U::FStructProperty *body{}, *helm{}, *resist{};
        U::FDoubleProperty *carry{}, *minimum{}, *helm_weight{}, *body_weight{}, *bonus{};
        U::UScriptStruct *body_type{}, *helm_type{}, *resist_type{};
        U::FStructProperty *body_resist{}, *helm_resist{};
        U::UObject* baseline{};

        std::string_view bind(U::UObject* pawn)
        {
            body = field<U::FStructProperty>(pawn, STR("Armor_Struct"));
            helm = field<U::FStructProperty>(pawn, STR("Helm_Struct"));
            resist = field<U::FStructProperty>(pawn, STR("Resistance_Armor"));
            carry = field<U::FDoubleProperty>(pawn, STR("Inventory_CarryWeight"));
            minimum = field<U::FDoubleProperty>(pawn, STR("MinPenaltyWeight"));
            body_type = find<U::UScriptStruct>(armor_type);
            helm_type = find<U::UScriptStruct>(helmet_type);
            resist_type = find<U::UScriptStruct>(damage_type);
            body_resist = field<U::FStructProperty>(body_type, resistance);
            helm_resist = field<U::FStructProperty>(helm_type, resistance);
            body_weight = field<U::FDoubleProperty>(body_type, armor_weight);
            helm_weight = field<U::FDoubleProperty>(helm_type, armor_weight);
            bonus = field<U::FDoubleProperty>(body_type, carry_bonus);
            baseline = pawn->GetClassPrivate()->GetClassDefaultObject().Get();
            if (!body || !body_type || !body_weight || !bonus || body->GetStruct().Get() != body_type)
                return "native_guest_armor_body_struct_unqualified";
            if (!helm || !helm_type || !helm_weight || helm->GetStruct().Get() != helm_type)
                return "native_guest_armor_helmet_struct_unqualified";
            if (!resist || !resist_type || !body_resist || !helm_resist ||
                resist->GetStruct().Get() != resist_type || body_resist->GetStruct().Get() != resist_type ||
                helm_resist->GetStruct().Get() != resist_type)
                return "native_guest_armor_damage_struct_unqualified";
            if (!carry || !minimum || !baseline || !baseline->HasAnyFlags(U::RF_ClassDefaultObject) ||
                baseline->GetClassPrivate() != pawn->GetClassPrivate())
                return "native_guest_armor_weight_fields_unqualified";
            if (!field<U::FObjectProperty>(pawn, STR("PowerArmor")) || object(pawn, STR("PowerArmor")))
                return "native_guest_armor_power_armor_unqualified";
            return {};
        }

        std::string_view copy_resistance(U::UObject* defaults, const ArmorProfile& profile,
                                        U::FStructProperty* destination, void* prepared) const
        {
            auto* source = field<U::FStructProperty>(defaults, STR("Resistance"));
            if (!source || source->GetStruct().Get() != resist_type) return "native_guest_armor_damage_struct_unqualified";
            const auto* value = source->ContainerPtrToValuePtr<void>(defaults);
            for (std::size_t i = 0; i < damage_fields.size(); ++i)
            {
                auto* component = field<U::FDoubleProperty>(resist_type, damage_fields[i].name);
                if (!component || component->GetPropertyValueInContainer(value) != profile.damage[i])
                    return damage_fields[i].refusal;
            }
            destination->CopyCompleteValue(destination->ContainerPtrToValuePtr<void>(prepared), value);
            return {};
        }

        std::string_view prepare_body(U::UObject* defaults, const ArmorProfile& profile, void* next) const
        {
            struct Scalar { const U::TCHAR* source; const U::TCHAR* dest; std::string_view refusal; double value; };
            const std::array scalars{
                Scalar{STR("Weight"), armor_weight, "native_guest_armor_body_weight_unqualified", profile.weight},
                Scalar{STR("SpeedMod"), STR("SpeedMod_5_3D8A954E435B2C83978893A2C5C5D69E"), "native_guest_armor_speed_unqualified", 1.0},
                Scalar{STR("NoiseMod"), STR("Move_Noise_11_E2EDC7A04D5942755A28FE9479BE0793"), "native_guest_armor_noise_unqualified", profile.noise},
                Scalar{STR("VisibilityMod"), STR("VisibilityMod_13_E33F6D50463CE2650C61E29B5C66545D"), "native_guest_armor_visibility_unqualified", profile.visibility},
                Scalar{STR("CarryWeight_Bonus"), carry_bonus, "native_guest_armor_carry_bonus_unqualified", profile.bonus},
            };
            for (const auto& scalar : scalars)
            {
                auto* source = field<U::FDoubleProperty>(defaults, scalar.source);
                auto* dest = field<U::FDoubleProperty>(body_type, scalar.dest);
                if (!source || !dest || source->GetPropertyValueInContainer(defaults) != scalar.value) return scalar.refusal;
                dest->SetPropertyValueInContainer(next, scalar.value);
            }
            auto* sprint = field<U::FBoolProperty>(body_type, STR("CanSprint_8_6852FE6E4BFD0ADB2A5474983720198A"));
            auto* walk = field<U::FObjectProperty>(body_type, STR("WalkSFXLayer_40_5B99B98249FDD245846185B23C70CE5F"));
            auto* defaults_walk = field<U::FObjectProperty>(defaults, STR("Walk_SFXLayer"));
            if (!sprint || !boolean(defaults, STR("CanSprint"), true))
                return "native_guest_armor_sprint_unqualified";
            if (!walk || !defaults_walk || walk->GetPropertyClass().Get() != defaults_walk->GetPropertyClass().Get())
                return "native_guest_armor_walk_sound_unqualified";
            sprint->SetPropertyValueInContainer(next, true);
            walk->SetObjectPropertyValue(walk->ContainerPtrToValuePtr<void>(next), object(defaults, STR("Walk_SFXLayer")));
            return copy_resistance(defaults, profile, body_resist, next);
        }

        std::string_view prepare_helmet(U::UObject* defaults, const ArmorProfile& profile, void* next) const
        {
            auto* weight = field<U::FDoubleProperty>(defaults, STR("Weight"));
            auto* source_visible = field<U::FDoubleProperty>(defaults, STR("VisibilityMod"));
            auto* target_visible = field<U::FDoubleProperty>(helm_type, helmet_visibility);
            auto* source_light = field<U::FByteProperty>(defaults, STR("FlashLightType"));
            auto* target_light = field<U::FByteProperty>(helm_type, helmet_light);
            auto* hud = field<U::FBoolProperty>(helm_type, helmet_hud);
            auto* source_hud = field<U::FBoolProperty>(defaults, STR("FullHUD"));
            auto* source_origin = field<U::FStructProperty>(defaults, STR("FlashlightLocation"));
            auto* target_origin = field<U::FStructProperty>(helm_type, helmet_light_origin);
            auto* vector_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
            auto* x = field<U::FDoubleProperty>(vector_type, STR("X"));
            auto* y = field<U::FDoubleProperty>(vector_type, STR("Y"));
            auto* z = field<U::FDoubleProperty>(vector_type, STR("Z"));
            if (!weight || weight->GetPropertyValueInContainer(defaults) != profile.weight || !source_visible || !target_visible ||
                source_visible->GetPropertyValueInContainer(defaults) != profile.visibility ||
                !hud || !source_hud || !source_light || !target_light ||
                source_light->GetPropertyValueInContainer(defaults) != profile.flashlight ||
                !source_origin || !target_origin || !vector_type || source_origin->GetStruct().Get() != vector_type ||
                target_origin->GetStruct().Get() != vector_type || !x || !y || !z) return "native_guest_armor_helmet_stats_unqualified";
            const auto* origin = source_origin->ContainerPtrToValuePtr<void>(defaults);
            if (x->GetPropertyValueInContainer(origin) != profile.light_origin[0] ||
                y->GetPropertyValueInContainer(origin) != profile.light_origin[1] ||
                z->GetPropertyValueInContainer(origin) != profile.light_origin[2])
                return "native_guest_armor_helmet_light_unqualified";
            helm_weight->SetPropertyValueInContainer(next, profile.weight);
            target_visible->SetPropertyValueInContainer(next, profile.visibility);
            hud->SetPropertyValueInContainer(next, source_hud->GetPropertyValueInContainer(defaults));
            target_light->SetPropertyValueInContainer(next, profile.flashlight);
            target_origin->CopyCompleteValue(target_origin->ContainerPtrToValuePtr<void>(next), origin);
            return copy_resistance(defaults, profile, helm_resist, next);
        }

        std::string_view compose(const ArmorProfile* torso, const ArmorProfile* head, void* next_body, void* next_helmet,
                                 void* next_resist, std::array<double, damage_fields.size()>& totals, double& threshold) const
        {
            if (torso)
            {
                if (const auto error = prepare_body(armor_defaults(*torso), *torso, next_body); !error.empty()) return error;
            }
            else body->CopyCompleteValue(next_body, body->ContainerPtrToValuePtr<void>(baseline));
            if (head)
            {
                if (const auto error = prepare_helmet(armor_defaults(*head), *head, next_helmet); !error.empty()) return error;
            }
            else helm->CopyCompleteValue(next_helmet, helm->ContainerPtrToValuePtr<void>(baseline));

            if (body_weight->GetPropertyValueInContainer(next_body) != (torso ? torso->weight : 0.0) ||
                helm_weight->GetPropertyValueInContainer(next_helmet) != (head ? head->weight : 0.0) ||
                bonus->GetPropertyValueInContainer(next_body) != (torso ? torso->bonus : 0.0))
                return "native_guest_armor_stock_default_weights_unqualified";
            const auto* torso_resist = body_resist->ContainerPtrToValuePtr<void>(next_body);
            const auto* head_resist = helm_resist->ContainerPtrToValuePtr<void>(next_helmet);
            for (std::size_t i = 0; i < damage_fields.size(); ++i)
            {
                auto* component = field<U::FDoubleProperty>(resist_type, damage_fields[i].name);
                if (!component) return damage_fields[i].refusal;
                const double first = component->GetPropertyValueInContainer(torso_resist);
                const double second = component->GetPropertyValueInContainer(head_resist);
                if (first != (torso ? torso->damage[i] : 0.0) || second != (head ? head->damage[i] : 0.0))
                    return damage_fields[i].refusal;
                totals[i] = first + second;
                component->SetPropertyValueInContainer(next_resist, totals[i]);
            }
            threshold = 20.0 + (torso ? torso->bonus : 0.0);
            return {};
        }
    };

    struct Report
    {
        int before{-1}, after{-1};
        double carry_before{-1.0}, carry_after{-1.0}, minimum{-1.0}, slash{-1.0}, acid{-1.0};
        const ArmorProfile* selected{};
        const ArmorProfile* previous{};
        Guid replaced{};
        const ArmorProfile* torso{};
        std::array<double, damage_fields.size()> combined{};
        bool voice_filter{};
    };

    struct DisguiseProjection
    {
        U::UFunction* configure{};
        U::FByteProperty* faction{};
        U::FBoolProperty* compromised{};
        std::uint8_t prior_faction{};
        bool prior_compromised{};

        bool bind(U::UObject* pawn)
        {
            configure = find<U::UFunction>(configure_disguise);
            faction = field<U::FByteProperty>(pawn, STR("Disguise"));
            compromised = field<U::FBoolProperty>(pawn, STR("Disguise_Compromised"));
            if (!configure || configure->GetOuterPrivate() != pawn->GetClassPrivate() ||
                configure->GetNumParms() != 0 || configure->GetParmsSize() != 0 ||
                configure->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                !faction_property(faction) || !compromised) return false;
            prior_faction = faction->GetPropertyValueInContainer(pawn);
            prior_compromised = compromised->GetPropertyValueInContainer(pawn);
            return true;
        }

        bool refresh(const Scope& scope, bool paired) const
        {
            if (!configure) return true;
            // Stock Configure_Disguise reads the pawn's own worn rows and the shared reputation.
            scope.pawn->ProcessEvent(configure, nullptr);
            const auto configured = faction->GetPropertyValueInContainer(scope.pawn);
            return scope.current() && (configured == 0 || (paired && configured == state_faction)) &&
                compromised->GetPropertyValueInContainer(scope.pawn) == prior_compromised;
        }

        void rollback(U::UObject* pawn) const
        {
            if (!configure) return;
            faction->SetPropertyValueInContainer(pawn, prior_faction);
            compromised->SetPropertyValueInContainer(pawn, prior_compromised);
        }
    };


    std::string profile_receipt(const ArmorProfile* profile)
    {
        if (!profile) return "-";
        if (!profile->name.empty()) return std::string{profile->name};
        // The values originate in the host's validated class defaults.
        return std::format("{},{},{},{},{},{},{}", RC::to_utf8_string(profile->path),
            profile->weight, profile->damage[slashing], profile->damage[acid], profile->damage[poison],
            20.0 + profile->bonus, profile->vox ? 1 : 0);
    }

    int report(const Lua& lua, bool ok, std::string_view status, const Report& result = {})
    {
        lua.set_bool(ok);
        lua.set_string(status);
        lua.set_integer(result.before);
        lua.set_integer(result.after);
        lua.set_number(result.carry_before);
        lua.set_number(result.carry_after);
        lua.set_number(result.minimum);
        lua.set_number(result.slash);
        lua.set_number(result.acid);
        lua.set_string(profile_receipt(result.selected));
        lua.set_string(profile_receipt(result.previous));
        lua.set_string(result.replaced == Guid{} ? std::string{"-"} : std::format("{:08X}{:08X}{:08X}{:08X}",
            result.replaced[0], result.replaced[1], result.replaced[2], result.replaced[3]));
        lua.set_string(profile_receipt(result.torso));
        lua.set_number(result.combined[slashing]);
        lua.set_number(result.combined[acid]);
        lua.set_number(result.combined[poison]);
        lua.set_bool(result.voice_filter);
        return 17;
    }

    int seed(const Lua& lua, const Scope& scope, const Inventory& inventory, const ArmorProfile& profile,
             U::UObject* defaults, const Guid& id)
    {
        if (!fixtures()) return report(lua, false, "native_guest_armor_fixtures_disabled");
        int index{};
        bool worn{};
        if (!inventory.locate(scope.guest, id, index, worn, profile.helmet) || index >= 0 || worn ||
            !isolated_rows(scope, inventory, id))
            return report(lua, false, "native_guest_armor_guid_conflict");
        auto* library = find<U::UObject>(library_path);
        auto* function = find<U::UFunction>(factory_path);
        auto* klass = field<U::FClassProperty>(function, STR("Item_Class"));
        auto* world = field<U::FObjectProperty>(function, STR("__WorldContext"));
        auto* item = field<U::FStructProperty>(function, STR("Item"));
        if (!library || !function || library->GetClassPrivate() != function->GetOuterPrivate() ||
            function->GetNumParms() != 3 || function->GetParmsSize() != 0x60 ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetReturnProperty() ||
            !klass || !world || !item || !klass->HasAnyPropertyFlags(U::CPF_Parm) || !world->HasAnyPropertyFlags(U::CPF_Parm) ||
            !item->HasAnyPropertyFlags(U::CPF_OutParm) || !item->HasAnyPropertyFlags(U::CPF_Parm) ||
            klass->GetOffset_Internal() != 0 || world->GetOffset_Internal() != 8 || item->GetOffset_Internal() != 0x10 ||
            item->GetStruct().Get() != inventory.item->GetStruct().Get())
            return report(lua, false, "native_guest_armor_fixture_schema_unqualified");
        alignas(16) std::byte args[0x60]{};
        item->InitializeValue_InContainer(args);
        klass->SetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(args), defaults->GetClassPrivate());
        world->SetObjectPropertyValue(world->ContainerPtrToValuePtr<void>(args), scope.pawn);
        library->ProcessEvent(function, args);
        auto* row = item->ContainerPtrToValuePtr<void>(args);
        const bool generated = scope.current() && inventory.item_class(row) == defaults->GetClassPrivate() &&
            inventory.item_guid(row) != Guid{} && inventory.weight->GetPropertyValueInContainer(row) == profile.weight &&
            (&profile == &bomber || stock_gear_row(inventory, row, defaults, profile));
        auto* destination = inventory.backpack->ContainerPtrToValuePtr<void>(scope.guest);
        U::FScriptArrayHelper bag(inventory.backpack, destination);
        Report proof{bag.Num(), bag.Num()};
        proof.selected = &profile;
        if (!generated || bag.Num() >= max_items)
        {
            item->DestroyValue_InContainer(args);
            return report(lua, false, "native_guest_armor_fixture_unqualified");
        }
        auto* typed_guid = inventory.guid->ContainerPtrToValuePtr<void>(row);
        for (std::size_t i = 0; i < id.size(); ++i)
            inventory.words[i]->SetPropertyValueInContainer(typed_guid, static_cast<std::int32_t>(id[i]));
        Copy before(inventory.backpack, destination);
        Copy prepared(inventory.backpack, destination);
        if (!before.value || !prepared.value)
        {
            item->DestroyValue_InContainer(args);
            return report(lua, false, "native_guest_armor_copy_unavailable");
        }
        U::FScriptArrayHelper new_bag(inventory.backpack, prepared.value);
        inventory.item->CopyCompleteValue(new_bag.GetRawPtr(new_bag.AddValue()), row);
        item->DestroyValue_InContainer(args);
        prepared.restore(destination);
        if (!prepared.matches(destination) || !scope.current())
        {
            before.restore(destination);
            return report(lua, false, before.matches(destination) && scope.current() ?
                "native_guest_armor_seed_rolled_back" : "mutation_unverified_guest_armor_seed", proof);
        }
        proof.after = new_bag.Num();
        return report(lua, true, "seeded", proof);
    }

    int apply(const Lua& lua, const Scope& scope, const Inventory& inventory, const Guid& id,
              bool restore_only, bool head, bool remove, bool drop)
    {
        int index{};
        bool worn{};
        if (!inventory.locate(scope.guest, id, index, worn, head) || (!worn && (restore_only || remove || index < 0)))
            return report(lua, false, "native_guest_armor_exact_owned_row_required");
        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(scope.guest);
        auto* slot = head ? inventory.helmet : inventory.armor;
        auto* companion = head ? inventory.armor : inventory.helmet;
        auto* active = slot->ContainerPtrToValuePtr<void>(scope.guest);
        const auto* other = companion->ContainerPtrToValuePtr<void>(scope.guest);
        auto* save_defaults = scope.guest->GetClassPrivate()->GetClassDefaultObject().Get();
        if (!save_defaults || save_defaults->GetClassPrivate() != scope.guest->GetClassPrivate() ||
            !save_defaults->HasAnyFlags(U::RF_ClassDefaultObject))
            return report(lua, false, "native_guest_armor_stock_defaults_unqualified");

        RuntimeGear selected_gear, previous_gear, companion_gear;
        const auto qualify = [&](const void* row, bool is_helmet, RuntimeGear& storage) -> const ArmorProfile* {
            if (inventory.item_guid(row) == Guid{}) return nullptr;
            const auto* profile = qualify_profile(inventory.item_class(row), is_helmet, storage);
            if (!profile ||
                inventory.weight->GetPropertyValueInContainer(row) != profile->weight) return nullptr;
            // The host already owns this exact typed row. Its visual payload can
            // differ from the defaults, while the class defines the worn stats.
            return armor_defaults(*profile) ? profile : nullptr;
        };
        const auto empty = [&](U::FStructProperty* property, const void* row) {
            return !inventory.item_class(row) && inventory.item_guid(row) == Guid{} &&
                property->Identical(row, property->ContainerPtrToValuePtr<void>(save_defaults));
        };
        const auto exact_worn = [&](const Guid& guid, bool is_helmet) {
            int source{};
            bool held{};
            return inventory.locate(scope.guest, guid, source, held, is_helmet) && held && source < 0;
        };

        U::FScriptArrayHelper current(inventory.backpack, bag);
        const auto* approved = worn ? active : current.GetRawPtr(index);
        const auto* selected = qualify(approved, head, selected_gear);
        if (!selected) return report(lua, false, "native_guest_armor_exact_class_required");
        auto* rounds = remove ? field<U::FIntProperty>(inventory.item->GetStruct().Get(),
            STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788")) : nullptr;
        const auto returned_rounds = rounds ? rounds->GetPropertyValueInContainer(approved) : -2;
        if (remove && !drop && (current.Num() >= max_items || returned_rounds < -1))
            return report(lua, false, "native_guest_armor_removed_item_unqualified");
        const ArmorProfile* previous{};
        Guid replaced{};
        if (!worn && !empty(slot, active))
        {
            previous = qualify(active, head, previous_gear);
            replaced = inventory.item_guid(active);
            if (!previous || !exact_worn(replaced, head))
                return report(lua, false, "native_guest_armor_slot_occupied");
        }

        const auto* other_profile = qualify(other, !head, companion_gear);
        if (other_profile ? !exact_worn(inventory.item_guid(other), !head) : !empty(companion, other))
            return report(lua, false, "native_guest_armor_companion_slot_unqualified");
        if (!isolated_rows(scope, inventory, id, replaced) ||
            (other_profile && !isolated_rows(scope, inventory, inventory.item_guid(other))))
            return report(lua, false, "native_guest_armor_exact_owned_row_required");

        const auto* torso = head ? other_profile : remove ? nullptr : selected;
        const auto* helmet = head ? (remove ? nullptr : selected) : other_profile;
        const auto state_gear = [](const ArmorProfile* profile) {
            return profile && profile->disguise_faction == state_faction;
        };
        const bool state_torso = state_gear(torso);
        bool state_helmet = state_gear(helmet);
        if (state_torso && helmet && !state_helmet)
        {
            auto* defaults = armor_defaults(*helmet);
            auto* factions = field<U::FArrayProperty>(defaults, STR("DisguisableFactions"));
            auto* faction = factions ? U::CastField<U::FByteProperty>(factions->GetInner()) : nullptr;
            if (faction_property(faction))
            {
                U::FScriptArrayHelper list(factions, factions->ContainerPtrToValuePtr<void>(defaults));
                for (int i = 0; i < list.Num(); ++i)
                    state_helmet |= faction->GetPropertyValueInContainer(list.GetRawPtr(i)) == state_faction;
            }
        }
        DisguiseProjection disguise;
        if ((state_torso || state_helmet || state_gear(previous) || (remove && state_gear(selected))) && !disguise.bind(scope.pawn))
            return report(lua, false, "native_guest_armor_disguise_schema_unqualified");
        // The stock character copies the selected class's voice filter to this pawn.
        const bool sets_voice = helmet || (remove && head);
        auto* vox = sets_voice ? field<U::FObjectProperty>(scope.pawn, STR("Vox Effect")) : nullptr;
        auto* next_voice = helmet ? object(armor_defaults(*helmet), STR("VoxEffect")) : nullptr;
        if (sets_voice && (!vox || vox->GetPropertyClass().Get() != find<U::UClass>(vox_class) ||
                          (helmet && helmet->vox && !next_voice)))
            return report(lua, false, "native_guest_armor_voice_filter_unqualified");
        auto* voice_slot = vox ? vox->ContainerPtrToValuePtr<void>(scope.pawn) : nullptr;
        auto* old_voice = voice_slot ? vox->GetObjectPropertyValue(voice_slot) : nullptr;
        Projection projection;
        if (const auto error = projection.bind(scope.pawn); !error.empty()) return report(lua, false, error);
        auto* body = projection.body->ContainerPtrToValuePtr<void>(scope.pawn);
        auto* helm = projection.helm->ContainerPtrToValuePtr<void>(scope.pawn);
        auto* resist = projection.resist->ContainerPtrToValuePtr<void>(scope.pawn);
        Copy original_bag(inventory.backpack, bag);
        Copy next_bag(inventory.backpack, bag);
        Copy original_slot(slot, active);
        Copy next_slot(slot, remove ? slot->ContainerPtrToValuePtr<void>(save_defaults) : approved);
        Copy original_body(projection.body, body);
        Copy original_helm(projection.helm, helm);
        Copy original_resist(projection.resist, resist);
        Copy next_body(projection.body);
        Copy next_helm(projection.helm);
        Copy next_resist(projection.resist);
        if (!original_bag.value || !next_bag.value || !original_slot.value || !next_slot.value ||
            !original_body.value || !original_helm.value || !original_resist.value || !next_body.value || !next_helm.value || !next_resist.value)
            return report(lua, false, "native_guest_armor_copy_unavailable");

        Report proof{current.Num(), current.Num()};
        proof.selected = selected;
        proof.previous = previous;
        proof.replaced = replaced;
        proof.torso = torso;
        U::FScriptArrayHelper prepared(inventory.backpack, next_bag.value);
        if (remove && !drop)
        {
            auto* returned = prepared.GetRawPtr(prepared.AddValue());
            inventory.item->CopyCompleteValue(returned, active);
            if (!original_slot.matches(returned) || !empty(slot, next_slot.value))
                return report(lua, false, "native_guest_armor_prepared_return_unqualified");
        }
        else if (previous) inventory.item->CopyCompleteValue(prepared.GetRawPtr(index), active);
        else if (!worn) prepared.RemoveValues(index, 1);
        proof.after = prepared.Num();
        if (previous && (proof.after != proof.before || inventory.item_guid(prepared.GetRawPtr(index)) != replaced ||
            !inventory.item->Identical(prepared.GetRawPtr(index), original_slot.value)))
            return report(lua, false, "native_guest_armor_prepared_swap_unqualified", proof);
        if (const auto error = projection.compose(torso, helmet, next_body.value, next_helm.value, next_resist.value,
                                                  proof.combined, proof.minimum); !error.empty())
            return report(lua, false, error, proof);

        const double old_weight = worn ? selected->weight : previous ? previous->weight : 0.0;
        const double torso_weight = torso ? torso->weight : 0.0;
        const double helmet_weight = helmet ? helmet->weight : 0.0;
        if (!inventory.carry(scope.guest, bag, head ? torso_weight : old_weight, head ? old_weight : helmet_weight, proof.carry_before) ||
            !inventory.carry(scope.guest, next_bag.value, torso_weight, helmet_weight, proof.carry_after))
            return report(lua, false, "native_guest_armor_carry_weights_unqualified", proof);
        if (!scope.current()) return report(lua, false, "native_guest_armor_exact_owner_required", proof);
        proof.slash = selected->damage[slashing];
        proof.acid = selected->damage[acid];

        const auto old_carry = projection.carry->GetPropertyValueInContainer(scope.pawn);
        const auto old_minimum = projection.minimum->GetPropertyValueInContainer(scope.pawn);
        const auto matches = [&] {
            int restored_index{};
            bool restored_worn{};
            bool destination_matches;
            if (remove)
            {
                const auto destination_index = drop ? -1 : proof.before;
                destination_matches = inventory.locate(scope.guest, id, restored_index, restored_worn, head) &&
                    !restored_worn && restored_index == destination_index;
            }
            else destination_matches = exact_worn(id, head);
            return destination_matches &&
                (!previous || (inventory.locate(scope.guest, replaced, restored_index, restored_worn, head) &&
                               !restored_worn && restored_index == index)) &&
                next_bag.matches(bag) && next_slot.matches(active) && next_body.matches(body) && next_helm.matches(helm) && next_resist.matches(resist) &&
                projection.carry->GetPropertyValueInContainer(scope.pawn) == proof.carry_after &&
                projection.minimum->GetPropertyValueInContainer(scope.pawn) == proof.minimum &&
                (!voice_slot || vox->GetObjectPropertyValue(voice_slot) == next_voice);
        };
        const auto success = [&](std::string_view status) {
            if (remove)
            {
                lua.set_bool(true);
                lua.set_string(drop ? "debited" : "unequipped");
                lua.set_integer(head ? 8 : 9);
                lua.set_string(RC::to_utf8_string(selected->path));
                lua.set_integer(returned_rounds);
                return 5;
            }
            proof.voice_filter = voice_slot && vox->GetObjectPropertyValue(voice_slot);
            return report(lua, true, status, proof);
        };
        if (worn && !remove && matches())
        {
            if (disguise.refresh(scope, state_torso && state_helmet)) return success("already_applied");
            disguise.rollback(scope.pawn);
            return report(lua, false, scope.current() ? "native_guest_armor_disguise_rolled_back" :
                          "mutation_unverified_guest_armor_disguise", proof);
        }

        // The two saved rows remain independent. Only the guest pawn's derived stats combine them.
        if (!worn || remove)
        {
            next_slot.restore(active);
            next_bag.restore(bag);
        }
        next_body.restore(body);
        next_helm.restore(helm);
        next_resist.restore(resist);
        if (voice_slot) vox->SetObjectPropertyValue(voice_slot, next_voice);
        projection.carry->SetPropertyValueInContainer(scope.pawn, proof.carry_after);
        projection.minimum->SetPropertyValueInContainer(scope.pawn, proof.minimum);
        if (scope.current() && matches() && disguise.refresh(scope, state_torso && state_helmet) && matches())
            return success(previous ? "swapped" : worn ? "reapplied" : "equipped");

        if (!worn || remove)
        {
            original_slot.restore(active);
            original_bag.restore(bag);
        }
        original_body.restore(body);
        original_helm.restore(helm);
        original_resist.restore(resist);
        if (voice_slot) vox->SetObjectPropertyValue(voice_slot, old_voice);
        projection.carry->SetPropertyValueInContainer(scope.pawn, old_carry);
        projection.minimum->SetPropertyValueInContainer(scope.pawn, old_minimum);
        disguise.rollback(scope.pawn);
        const bool rolled_back = scope.current() && original_bag.matches(bag) && original_slot.matches(active) &&
            original_body.matches(body) && original_helm.matches(helm) && original_resist.matches(resist) &&
            projection.carry->GetPropertyValueInContainer(scope.pawn) == old_carry &&
            projection.minimum->GetPropertyValueInContainer(scope.pawn) == old_minimum &&
            (!voice_slot || vox->GetObjectPropertyValue(voice_slot) == old_voice);
        return report(lua, false, rolled_back ? "native_guest_armor_rolled_back" : "mutation_unverified_guest_armor_equip", proof);
    }

    enum class Operation { Equip, Project, Seed, EquipHelmet, ProjectHelmet, Unequip, Drop };

    template <Operation operation> int run(const Lua& lua)
    {
        constexpr bool seed_operation = operation == Operation::Seed;
        constexpr bool drop = operation == Operation::Drop;
        constexpr bool remove = operation == Operation::Unequip || drop;
        const auto arguments = lua.get_stack_size();
        const bool named_seed = seed_operation && arguments == 4 && lua.is_string(4);
        const bool removal_slot = remove && arguments == 4 && lua.is_integer(4);
        if (!enabled() || !U::IsInGameThreadRaw() || (remove ? !removal_slot : (arguments != 3 && !named_seed)) ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
            return report(lua, false, "native_guest_armor_exact_context_required");
        const auto slot = remove ? lua.get_integer(4) : -1;
        if (remove && slot != 8 && slot != 9) return report(lua, false, "native_guest_armor_worn_slot_unqualified");
        const auto pawn = lua.get_integer();
        const auto guest = lua.get_integer();
        Guid guid{};
        Scope scope;
        if (pawn <= 0 || guest <= 0 || !read_guid(lua.get_string(), guid) ||
            !scope.bind(static_cast<std::uintptr_t>(pawn), static_cast<std::uintptr_t>(guest)))
            return report(lua, false, "native_guest_armor_exact_owner_required");
        constexpr bool project = operation == Operation::Project || operation == Operation::ProjectHelmet;
        const bool head = operation == Operation::EquipHelmet || operation == Operation::ProjectHelmet || (remove && slot == 8);
        if constexpr (!project)
        {
            auto* health = field<U::FDoubleProperty>(scope.guest, STR("PlayerHealth"));
            if (!health || !std::isfinite(health->GetPropertyValueInContainer(scope.guest)) ||
                health->GetPropertyValueInContainer(scope.guest) <= 0 ||
                !boolean(scope.pawn, STR("Dead"), false) || !boolean(scope.pawn, STR("IsWeaponBlocked"), false) ||
                !boolean(scope.pawn, STR("UsingItem"), false) || !boolean(scope.pawn, STR("Reloading"), false))
                return report(lua, false, "native_guest_armor_pawn_ineligible");
        }
        Inventory inventory;
        if (!inventory.bind(scope.guest))
            return report(lua, false, "native_guest_armor_stock_schema_unqualified");
        if constexpr (seed_operation)
        {
            const ArmorProfile* profile = &bomber;
            if (named_seed)
            {
                const auto name = lua.get_string();
                if (name != "basic-helmet" && name != "dust-filter" && name != "goggles" &&
                    name != "hunter-jacket" && name != "hazard-suit" &&
                    name != "conscript-uniform" && name != "standard-infantry-helmet")
                    return report(lua, false, "native_guest_armor_fixture_profile_unavailable");
                const auto selected = std::ranges::find(armor_profiles, name, &ArmorProfile::name);
                if (selected == armor_profiles.end())
                    return report(lua, false, "native_guest_armor_fixture_profile_unavailable");
                profile = &*selected;
            }
            auto* defaults = armor_defaults(*profile);
            if (!defaults) return report(lua, false, "native_guest_armor_stock_schema_unqualified");
            return seed(lua, scope, inventory, *profile, defaults, guid);
        }
        return apply(lua, scope, inventory, guid, project, head, remove, drop);
    }
}

bool silverfish::native_guest_armor::worn_crafting_passive(std::uintptr_t pawn, std::uintptr_t save, Guid& suit)
{
    if (!enabled() || !U::IsInGameThreadRaw()) return false;
    Scope scope;
    Inventory inventory;
    if (!scope.bind(pawn, save) || !inventory.bind(scope.guest)) return false;
    const auto* row = inventory.armor->ContainerPtrToValuePtr<void>(scope.guest);
    const auto* profile = profile_for(inventory.item_class(row));
    if (!profile || profile->passive != STR("Crafting")) return false;
    auto* defaults = armor_defaults(*profile);
    const auto id = inventory.item_guid(row);
    int index{};
    bool worn{};
    if (!defaults || id == Guid{} || !stock_gear_row(inventory, row, defaults, *profile) ||
        !inventory.locate(scope.guest, id, index, worn) || !worn || index >= 0 ||
        !isolated_rows(scope, inventory, id)) return false;
    suit = id;
    return true;
}

void silverfish::native_guest_armor::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishEquipNativeGuestArmor", run<Operation::Equip>);
    lua.register_function("SilverfishProjectNativeGuestArmor", run<Operation::Project>);
    lua.register_function("SilverfishEquipNativeGuestHelmet", run<Operation::EquipHelmet>);
    lua.register_function("SilverfishProjectNativeGuestHelmet", run<Operation::ProjectHelmet>);
    lua.register_function("SilverfishUnequipNativeGuestArmor", run<Operation::Unequip>);
    lua.register_function("SilverfishDebitNativeGuestWornDrop", run<Operation::Drop>);
    if (fixtures()) lua.register_function("SilverfishSeedNativeGuestArmor", run<Operation::Seed>);
}
