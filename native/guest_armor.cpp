#include "guest_armor.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
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
    constexpr auto armor_path = STR("/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C");
    constexpr auto armor_base = STR("/Game/Classes/Armor_Items/Item_ArmorMaster_BP.Item_ArmorMaster_BP_C");
    constexpr auto armor_type = STR("/Game/Classes/Libraries/Structs/Armor_Struct.Armor_Struct");
    constexpr auto helmet_type = STR("/Game/Classes/Libraries/Structs/Helmet_Struct.Helmet_Struct");
    constexpr auto damage_type = STR("/Game/Classes/Libraries/Structs/Damage_Struct.Damage_Struct");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto factory_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeDefaultItem");
    constexpr auto resistance = STR("Resistance_2_2E35E4354FE2AD598EB8A48221FD001E");
    constexpr auto armor_weight = STR("Weight_36_1CC3CC9C4DAD1ADFEBC9D5B45765C95D");
    constexpr auto carry_bonus = STR("CaryBonus_43_3DF7AA974A13A7ADF22D3BA63B29FEF2");
    constexpr std::array guid_words{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr double bomber_slash = 0.2;
    constexpr double bomber_acid = 0.2;
    struct ResistanceField
    {
        const U::TCHAR* name;
        std::string_view refusal;
        double expected;
    };
    constexpr std::array damage_fields{
        ResistanceField{STR("Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5"), "native_guest_armor_kinetic_unqualified", 0.0},
        ResistanceField{STR("ArmorPenitration_4_E48DD35840D3699D8F37E48678EF29E4"), "native_guest_armor_penetration_unqualified", 0.0},
        ResistanceField{STR("BluntForce_6_6923B3964740DE281BD773BCCAAC2772"), "native_guest_armor_blunt_unqualified", 0.0},
        ResistanceField{STR("Stabbing_8_64710D2D4AB4232B4DD604AC3CD0219C"), "native_guest_armor_stabbing_unqualified", 0.1},
        ResistanceField{STR("Slashing_10_58FFFE444BD41CE5D15EAA80BC0D9DF1"), "native_guest_armor_slash_unqualified", bomber_slash},
        ResistanceField{STR("Fire_12_2B415A724C533C075B6BB390B76BF247"), "native_guest_armor_fire_unqualified", 0.1},
        ResistanceField{STR("Poison_14_68E96E324AD705A7F95362BBC781E220"), "native_guest_armor_poison_unqualified", 0.0},
        ResistanceField{STR("Electric_16_8F945E8C491C5825EC19E3837E266D48"), "native_guest_armor_electric_unqualified", 0.1},
        ResistanceField{STR("Radiation_18_E8FC71B2490E3C6050B6E999C65A13CB"), "native_guest_armor_radiation_unqualified", 0.0},
        ResistanceField{STR("Acid_20_F4C551B34F620FDF72D2F2A64DE15F3E"), "native_guest_armor_acid_unqualified", bomber_acid},
        ResistanceField{STR("OverPressure_22_3355FA8E45511902B20DD69D844D5020"), "native_guest_armor_overpressure_unqualified", 0.0},
    };
    constexpr int max_items = 4096;
    constexpr int max_clients = 4;

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

        bool locate(U::UObject* save, const Guid& wanted, int& backpack_index, bool& worn) const
        {
            U::FScriptArrayHelper bag(backpack, backpack->ContainerPtrToValuePtr<void>(save));
            U::FScriptArrayHelper gear(equipment, equipment->ContainerPtrToValuePtr<void>(save));
            if (bag.Num() < 0 || bag.Num() > max_items || gear.Num() < 0 || gear.Num() > max_items) return false;
            if (item_guid(helmet->ContainerPtrToValuePtr<void>(save)) == wanted) return false;
            for (int i = 0; i < gear.Num(); ++i)
                if (item_guid(gear.GetRawPtr(i)) == wanted) return false;
            worn = item_guid(armor->ContainerPtrToValuePtr<void>(save)) == wanted;
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

    U::UObject* bomber_defaults()
    {
        auto* klass = find<U::UClass>(armor_path);
        auto* base = find<U::UClass>(armor_base);
        auto* cdo = klass ? klass->GetClassDefaultObject().Get() : nullptr;
        auto* type = field<U::FByteProperty>(cdo, STR("ItemType"));
        auto* disguise = field<U::FByteProperty>(cdo, STR("DisguiseFaction"));
        auto* effects = field<U::FArrayProperty>(cdo, STR("PassiveEffects"));
        if (!base || !cdo || cdo->GetClassPrivate() != klass || !klass->IsChildOf(base) ||
            !cdo->HasAnyFlags(U::RF_ClassDefaultObject) || !type || type->GetPropertyValueInContainer(cdo) != 7 ||
            !disguise || disguise->GetPropertyValueInContainer(cdo) != 0 || !effects ||
            !field<U::FClassProperty>(cdo, STR("PowerArmor")) || object(cdo, STR("PowerArmor")) ||
            !boolean(cdo, STR("Stacks"), false)) return nullptr;
        U::FScriptArrayHelper passives(effects, effects->ContainerPtrToValuePtr<void>(cdo));
        return passives.Num() == 0 ? cdo : nullptr;
    }

    struct Projection
    {
        U::FStructProperty *body{}, *helm{}, *resist{};
        U::FDoubleProperty *carry{}, *minimum{}, *helm_weight{};
        U::UScriptStruct *body_type{}, *resist_type{};
        U::FStructProperty *body_resist{}, *helm_resist{}, *default_resist{};

        std::string_view bind(U::UObject* pawn, U::UObject* defaults)
        {
            body = field<U::FStructProperty>(pawn, STR("Armor_Struct"));
            helm = field<U::FStructProperty>(pawn, STR("Helm_Struct"));
            resist = field<U::FStructProperty>(pawn, STR("Resistance_Armor"));
            carry = field<U::FDoubleProperty>(pawn, STR("Inventory_CarryWeight"));
            minimum = field<U::FDoubleProperty>(pawn, STR("MinPenaltyWeight"));
            body_type = find<U::UScriptStruct>(armor_type);
            auto* helm_type = find<U::UScriptStruct>(helmet_type);
            resist_type = find<U::UScriptStruct>(damage_type);
            body_resist = field<U::FStructProperty>(body_type, resistance);
            helm_resist = field<U::FStructProperty>(helm_type, resistance);
            default_resist = field<U::FStructProperty>(defaults, STR("Resistance"));
            helm_weight = field<U::FDoubleProperty>(helm_type, armor_weight);
            if (!body || !body_type || body->GetStruct().Get() != body_type)
                return "native_guest_armor_body_struct_unqualified";
            if (!helm || !helm_type || !helm_weight || helm->GetStruct().Get() != helm_type)
                return "native_guest_armor_helmet_struct_unqualified";
            if (!resist || !resist_type || !body_resist || !helm_resist || !default_resist ||
                resist->GetStruct().Get() != resist_type || body_resist->GetStruct().Get() != resist_type ||
                helm_resist->GetStruct().Get() != resist_type || default_resist->GetStruct().Get() != resist_type)
                return "native_guest_armor_damage_struct_unqualified";
            if (!carry || !minimum)
                return "native_guest_armor_weight_fields_unqualified";
            if (!field<U::FObjectProperty>(pawn, STR("PowerArmor")) || object(pawn, STR("PowerArmor")))
                return "native_guest_armor_power_armor_unqualified";
            return {};
        }

        std::string_view prepare(U::UObject* pawn, U::UObject* defaults, void* next_body, void* next_resist,
                                 double& helmet_weight, double& threshold) const
        {
            struct Scalar { const U::TCHAR* source; const U::TCHAR* dest; std::string_view refusal; double value; };
            constexpr std::array scalars{
                Scalar{STR("Weight"), armor_weight, "native_guest_armor_body_weight_unqualified", 0.5},
                Scalar{STR("SpeedMod"), STR("SpeedMod_5_3D8A954E435B2C83978893A2C5C5D69E"), "native_guest_armor_speed_unqualified", 1.0},
                Scalar{STR("NoiseMod"), STR("Move_Noise_11_E2EDC7A04D5942755A28FE9479BE0793"), "native_guest_armor_noise_unqualified", 0.9},
                Scalar{STR("VisibilityMod"), STR("VisibilityMod_13_E33F6D50463CE2650C61E29B5C66545D"), "native_guest_armor_visibility_unqualified", 0.9},
                Scalar{STR("CarryWeight_Bonus"), carry_bonus, "native_guest_armor_carry_bonus_unqualified", 5.0},
            };
            for (const auto& scalar : scalars)
            {
                auto* source = field<U::FDoubleProperty>(defaults, scalar.source);
                auto* dest = field<U::FDoubleProperty>(body_type, scalar.dest);
                if (!source || !dest || source->GetPropertyValueInContainer(defaults) != scalar.value) return scalar.refusal;
                dest->SetPropertyValueInContainer(next_body, scalar.value);
            }
            auto* sprint = field<U::FBoolProperty>(body_type, STR("CanSprint_8_6852FE6E4BFD0ADB2A5474983720198A"));
            auto* walk = field<U::FObjectProperty>(body_type, STR("WalkSFXLayer_40_5B99B98249FDD245846185B23C70CE5F"));
            auto* defaults_walk = field<U::FObjectProperty>(defaults, STR("Walk_SFXLayer"));
            if (!sprint || !boolean(defaults, STR("CanSprint"), true))
                return "native_guest_armor_sprint_unqualified";
            if (!walk || !defaults_walk || walk->GetPropertyClass().Get() != defaults_walk->GetPropertyClass().Get())
                return "native_guest_armor_walk_sound_unqualified";
            sprint->SetPropertyValueInContainer(next_body, true);
            walk->SetObjectPropertyValue(walk->ContainerPtrToValuePtr<void>(next_body), object(defaults, STR("Walk_SFXLayer")));
            const auto* default_damage = default_resist->ContainerPtrToValuePtr<void>(defaults);
            const auto* worn_helmet = helm->ContainerPtrToValuePtr<void>(pawn);
            const auto* helmet_damage = helm_resist->ContainerPtrToValuePtr<void>(worn_helmet);
            helmet_weight = helm_weight->GetPropertyValueInContainer(worn_helmet);
            if (helmet_weight != 0.0) return "native_guest_armor_helmet_weight_unqualified";
            body_resist->CopyCompleteValue(body_resist->ContainerPtrToValuePtr<void>(next_body), default_damage);
            for (const auto& damage : damage_fields)
            {
                auto* property = field<U::FDoubleProperty>(resist_type, damage.name);
                if (!property || property->GetPropertyValueInContainer(default_damage) != damage.expected)
                    return damage.refusal;
                if (property->GetPropertyValueInContainer(helmet_damage) != 0.0)
                    return "native_guest_armor_helmet_damage_unqualified";
                property->SetPropertyValueInContainer(next_resist, damage.expected);
            }
            threshold = 25.0; // Stock ApplyArmorStats computes 20 plus the wearer's body carry bonus.
            return {};
        }
    };

    struct Report
    {
        int before{-1}, after{-1};
        double carry_before{-1.0}, carry_after{-1.0}, minimum{-1.0}, slash{-1.0}, acid{-1.0};
    };

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
        return 9;
    }

    int seed(const Lua& lua, const Scope& scope, const Inventory& inventory, U::UObject* defaults, const Guid& id)
    {
        if (!fixtures()) return report(lua, false, "native_guest_armor_fixtures_disabled");
        int index{};
        bool worn{};
        if (!inventory.locate(scope.guest, id, index, worn) || index >= 0 || worn)
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
            inventory.item_guid(row) != Guid{} && inventory.weight->GetPropertyValueInContainer(row) == 0.5;
        auto* destination = inventory.backpack->ContainerPtrToValuePtr<void>(scope.guest);
        U::FScriptArrayHelper bag(inventory.backpack, destination);
        Report proof{bag.Num(), bag.Num()};
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

    int apply(const Lua& lua, const Scope& scope, const Inventory& inventory, U::UObject* defaults,
              const Guid& id, bool restore_only)
    {
        int index{}, host_index{};
        bool worn{}, host_worn{};
        if (!inventory.locate(scope.guest, id, index, worn) || !inventory.locate(scope.stock, id, host_index, host_worn) ||
            host_index >= 0 || host_worn || (!worn && (restore_only || index < 0)))
            return report(lua, false, "native_guest_armor_exact_owned_row_required");
        auto* bag = inventory.backpack->ContainerPtrToValuePtr<void>(scope.guest);
        auto* armor = inventory.armor->ContainerPtrToValuePtr<void>(scope.guest);
        const auto* helmet = inventory.helmet->ContainerPtrToValuePtr<void>(scope.guest);
        auto* save_defaults = scope.guest->GetClassPrivate()->GetClassDefaultObject().Get();
        if (!save_defaults || save_defaults->GetClassPrivate() != scope.guest->GetClassPrivate() ||
            !save_defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            inventory.item_class(helmet) || inventory.item_guid(helmet) != Guid{} ||
            !inventory.helmet->Identical(helmet, inventory.helmet->ContainerPtrToValuePtr<void>(save_defaults)) ||
            (!worn && (inventory.item_class(armor) || inventory.item_guid(armor) != Guid{} ||
                !inventory.armor->Identical(armor, inventory.armor->ContainerPtrToValuePtr<void>(save_defaults)))))
            return report(lua, false, "native_guest_armor_slot_occupied");
        U::FScriptArrayHelper current(inventory.backpack, bag);
        const auto* approved = worn ? armor : current.GetRawPtr(index);
        if (inventory.item_class(approved) != defaults->GetClassPrivate() ||
            inventory.weight->GetPropertyValueInContainer(approved) != 0.5)
            return report(lua, false, "native_guest_armor_exact_class_required");
        Projection projection;
        if (const auto error = projection.bind(scope.pawn, defaults); !error.empty())
            return report(lua, false, error);
        auto* body = projection.body->ContainerPtrToValuePtr<void>(scope.pawn);
        auto* resist = projection.resist->ContainerPtrToValuePtr<void>(scope.pawn);
        Copy original_bag(inventory.backpack, bag);
        Copy next_bag(inventory.backpack, bag);
        Copy original_armor(inventory.armor, armor);
        Copy next_armor(inventory.armor, approved);
        Copy original_body(projection.body, body);
        Copy original_resist(projection.resist, resist);
        Copy next_body(projection.body);
        Copy next_resist(projection.resist);
        if (!original_bag.value || !next_bag.value || !original_armor.value || !next_armor.value ||
            !original_body.value || !original_resist.value || !next_body.value || !next_resist.value)
            return report(lua, false, "native_guest_armor_copy_unavailable");

        Report proof{current.Num(), current.Num()};
        double helmet_weight{};
        U::FScriptArrayHelper prepared(inventory.backpack, next_bag.value);
        if (!worn) prepared.RemoveValues(index, 1);
        proof.after = prepared.Num();
        if (const auto error = projection.prepare(
                scope.pawn, defaults, next_body.value, next_resist.value, helmet_weight, proof.minimum); !error.empty())
            return report(lua, false, error, proof);
        if (!inventory.carry(scope.guest, bag, worn ? 0.5 : 0.0, helmet_weight, proof.carry_before) ||
            !inventory.carry(scope.guest, next_bag.value, 0.5, helmet_weight, proof.carry_after))
            return report(lua, false, "native_guest_armor_carry_weights_unqualified", proof);
        if (!scope.current()) return report(lua, false, "native_guest_armor_exact_owner_required", proof);
        proof.slash = bomber_slash;
        proof.acid = bomber_acid;
        const auto old_carry = projection.carry->GetPropertyValueInContainer(scope.pawn);
        const auto old_minimum = projection.minimum->GetPropertyValueInContainer(scope.pawn);
        const auto matches = [&] {
            return next_bag.matches(bag) && next_armor.matches(armor) && next_body.matches(body) && next_resist.matches(resist) &&
                projection.carry->GetPropertyValueInContainer(scope.pawn) == proof.carry_after &&
                projection.minimum->GetPropertyValueInContainer(scope.pawn) == proof.minimum;
        };
        if (worn && matches()) return report(lua, true, "already_applied", proof);

        // The cooked stock UI changes its canonical Save and caches. Here only the
        // exact detached Backpack/armor and the corresponding derived guest body change.
        if (!worn)
        {
            next_armor.restore(armor);
            next_bag.restore(bag);
        }
        next_body.restore(body);
        next_resist.restore(resist);
        projection.carry->SetPropertyValueInContainer(scope.pawn, proof.carry_after);
        projection.minimum->SetPropertyValueInContainer(scope.pawn, proof.minimum);
        if (scope.current() && matches()) return report(lua, true, worn ? "reapplied" : "equipped", proof);

        if (!worn)
        {
            original_armor.restore(armor);
            original_bag.restore(bag);
        }
        original_body.restore(body);
        original_resist.restore(resist);
        projection.carry->SetPropertyValueInContainer(scope.pawn, old_carry);
        projection.minimum->SetPropertyValueInContainer(scope.pawn, old_minimum);
        const bool rolled_back = scope.current() && original_bag.matches(bag) && original_armor.matches(armor) &&
            original_body.matches(body) && original_resist.matches(resist) &&
            projection.carry->GetPropertyValueInContainer(scope.pawn) == old_carry &&
            projection.minimum->GetPropertyValueInContainer(scope.pawn) == old_minimum;
        return report(lua, false, rolled_back ? "native_guest_armor_rolled_back" : "mutation_unverified_guest_armor_equip", proof);
    }

    enum class Operation { Equip, Project, Seed };

    template <Operation operation> int run(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
            return report(lua, false, "native_guest_armor_exact_context_required");
        const auto pawn = lua.get_integer();
        const auto guest = lua.get_integer();
        Guid guid{};
        Scope scope;
        if (pawn <= 0 || guest <= 0 || !read_guid(lua.get_string(), guid) ||
            !scope.bind(static_cast<std::uintptr_t>(pawn), static_cast<std::uintptr_t>(guest)))
            return report(lua, false, "native_guest_armor_exact_owner_required");
        if constexpr (operation != Operation::Project)
        {
            auto* health = field<U::FDoubleProperty>(scope.guest, STR("PlayerHealth"));
            if (!health || !std::isfinite(health->GetPropertyValueInContainer(scope.guest)) ||
                health->GetPropertyValueInContainer(scope.guest) <= 0 ||
                !boolean(scope.pawn, STR("Dead"), false) || !boolean(scope.pawn, STR("IsWeaponBlocked"), false) ||
                !boolean(scope.pawn, STR("UsingItem"), false) || !boolean(scope.pawn, STR("Reloading"), false))
                return report(lua, false, "native_guest_armor_pawn_ineligible");
        }
        Inventory inventory;
        auto* defaults = bomber_defaults();
        if (!defaults || !inventory.bind(scope.guest))
            return report(lua, false, "native_guest_armor_stock_schema_unqualified");
        if constexpr (operation == Operation::Seed) return seed(lua, scope, inventory, defaults, guid);
        return apply(lua, scope, inventory, defaults, guid, operation == Operation::Project);
    }
}

void silverfish::native_guest_armor::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishEquipNativeGuestArmor", run<Operation::Equip>);
    lua.register_function("SilverfishProjectNativeGuestArmor", run<Operation::Project>);
    if (fixtures()) lua.register_function("SilverfishSeedNativeGuestArmor", run<Operation::Seed>);
}
