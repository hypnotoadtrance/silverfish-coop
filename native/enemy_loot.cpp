#include "enemy_loot.hpp"
#include "shared_loot.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01");
    constexpr auto cqc_world_path = STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P");
    constexpr auto npc_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto bandit_path = STR("/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C");
    constexpr auto cqc_path = STR("/Game/Classes/NPCs/NPC_State_Infantry_CQC.NPC_State_Infantry_CQC_C");
    constexpr std::array cqc_actor_paths{
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_0"),
        STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P:PersistentLevel.NPC_State_Infantry_CQC_C_1"),
    };
    constexpr auto army_shotgun_path = STR("/Game/Classes/Weapons/Wep_ShotGun_Army.Wep_ShotGun_Army_C");
    constexpr auto ammo_type_path = STR("/Game/Classes/Libraries/Structs/AmmoType_Struct.AmmoType_Struct");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto add_item_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto shell_path = STR("/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C");
    constexpr auto projectile_path =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr int max_effects = 256;
    constexpr int max_cache_effects = 1024;
    constexpr int max_inventory = 4096;
    constexpr int max_guests = 4;

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool out)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == out && !property->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize()
            ? property : nullptr;
    }

    U::UObject* object(U::UObject* receiver, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(receiver, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(receiver)) : nullptr;
    }

    bool boolean(U::UObject* receiver, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(receiver, name);
        return property && property->GetPropertyValueInContainer(receiver) == expected;
    }

    bool live(U::UObject* candidate)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject |
            U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return candidate && !candidate->HasAnyFlags(excluded) && !candidate->IsUnreachable();
    }

    bool authority(U::UObject* actor, U::UObject* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    U::UObject* sole_object(U::UObject* receiver, const U::TCHAR* name)
    {
        auto* property = field<U::FArrayProperty>(receiver, name);
        auto* inner = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!inner) return nullptr;
        U::FScriptArrayHelper array(property, property->ContainerPtrToValuePtr<void>(receiver));
        return array.Num() == 1 ? inner->GetObjectPropertyValue(array.GetRawPtr(0)) : nullptr;
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool fixtures_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_ENEMY_LOOT") && env("SILVERFISH_COOP_NATIVE_NPC_TEST") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
    }

    bool ordinary_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_CQC_LOOT") && env("SILVERFISH_COOP_NATIVE_ZONE") &&
            env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_LOOT");
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    struct Participant
    {
        U::FWeakObjectPtr pawn{}, controller{}, player{}, save{};
        std::string character;
    };

    struct Schema
    {
        U::UFunction *deal{}, *feedback{}, *die{}, *script{}, *loot{}, *chance{}, *passive{}, *calc{};
        U::UClass *bullet{}, *ammo{};
        U::FObjectProperty *instigator{}, *ammo_result{};
        U::FDoubleProperty *health{}, *damage{};
        U::FBoolProperty *lethal{}, *count{}, *bounty{};
        U::FIntProperty* instances{};
        U::FNameProperty* effect{};
    };

    struct BountyCounts
    {
        int permanent{}, timed{}, gear{};
        int total() const { return permanent + timed + gear; }
    };

    struct BountyState
    {
        U::FName name{};
        BountyCounts counts{};
        std::array<U::FName, max_cache_effects> cache{};
        int cache_size{-1};
        int cache_bounty{};
    };

    U::FNameProperty* name_entries(U::FArrayProperty* property)
    {
        auto* name = property ? U::CastField<U::FNameProperty>(property->GetInner()) : nullptr;
        return name && name->GetArrayDim() == 1 && name->GetOffset_Internal() == 0 ? name : nullptr;
    }

    U::FArrayProperty* permanent_effects(U::UObject* save, void*& data)
    {
        auto* character = field<U::FStructProperty>(save, STR("Character"));
        auto* type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct"));
        auto* effects = field<U::FArrayProperty>(type, STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"));
        if (!type || !character || character->GetStruct().Get() != type || !name_entries(effects)) return nullptr;
        data = effects->ContainerPtrToValuePtr<void>(character->ContainerPtrToValuePtr<void>(save));
        return effects;
    }

    bool count_owned_effects(U::UObject* save, const U::FName& sought, BountyCounts& counts)
    {
        void* data{};
        auto* effects = permanent_effects(save, data);
        if (!effects || sought.IsNone()) return false;
        auto* names = name_entries(effects);
        U::FScriptArrayHelper permanent(effects, data);
        if (permanent.Num() < 0 || permanent.Num() > max_effects) return false;
        for (int index = 0; index < permanent.Num(); ++index)
            counts.permanent += names->GetPropertyValue(permanent.GetRawPtr(index)) == sought;

        auto* temporary = field<U::FMapProperty>(save, STR("PlayerTempEffects"));
        auto* key = temporary ? U::CastField<U::FNameProperty>(temporary->GetKeyProp()) : nullptr;
        auto* value = temporary ? U::CastField<U::FStructProperty>(temporary->GetValueProp()) : nullptr;
        auto* timespan = find<U::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
        if (!key || key->GetSize() != sizeof(U::FName) || key->GetOffset_Internal() != 0 ||
            !value || !timespan || value->GetStruct().Get() != timespan) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        const auto* map = temporary->ContainerPtrToValuePtr<U::FScriptMap>(save);
        const auto count = map->Num();
        const auto extent = map->GetMaxIndex();
        if (count < 0 || count > extent || extent > max_effects) return false;
        int seen = 0;
        for (int index = 0; index < extent; ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = map->GetData(index, layout);
            if (!entry) return false;
            counts.timed += key->GetPropertyValue(entry) == sought;
            ++seen;
        }
        // Compile_PassiveEffects takes each currently present map key once.
        return seen == count && counts.timed <= 1;
    }

    bool count_inventory_effects(U::UObject* save, const U::FName& sought, BountyCounts& counts)
    {
        auto* type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct"));
        auto* item = find<U::UClass>(item_path);
        auto* item_class = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        auto* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
        if (!type || !item || !item_class || item_class->GetMetaClass().Get() != item ||
            !guid || !guid_type || guid->GetStruct().Get() != guid_type || guid->GetSize() != 16) return false;
        constexpr std::array names{STR("A"), STR("B"), STR("C"), STR("D")};
        std::array<U::FIntProperty*, names.size()> words{};
        for (std::size_t index = 0; index < names.size(); ++index)
        {
            words[index] = field<U::FIntProperty>(guid_type, names[index]);
            if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * sizeof(std::int32_t)))
                return false;
        }

        const auto visit = [&](const void* row, bool backpack, const U::TCHAR* worn_type = nullptr) {
            auto* object_class = item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(row));
            if (!object_class) return true;
            auto* klass = U::Cast<U::UClass>(object_class);
            if (!live(klass) || !klass->IsChildOf(item)) return false;
            if (!backpack)
            {
                const auto* data = guid->ContainerPtrToValuePtr<void>(row);
                bool identified = false;
                for (auto* word : words) identified |= word->GetPropertyValueInContainer(data) != 0;
                if (!identified) return true;
                if (worn_type)
                {
                    auto* subtype = find<U::UClass>(worn_type);
                    if (!subtype) return false;
                    if (!klass->IsChildOf(subtype)) return true;
                }
            }
            auto* defaults = klass->GetClassDefaultObject().Get();
            if (!defaults || defaults->GetClassPrivate() != klass ||
                !defaults->HasAnyFlags(U::RF_ClassDefaultObject)) return false;
            if (backpack)
            {
                auto* category = field<U::FByteProperty>(defaults, STR("ItemType"));
                auto* forced = field<U::FBoolProperty>(defaults, STR("ForcePassives_InBackPack"));
                if (!category || !forced) return false;
                if (category->GetPropertyValueInContainer(defaults) != 0 &&
                    !forced->GetPropertyValueInContainer(defaults)) return true;
            }
            auto* passives = field<U::FArrayProperty>(defaults, STR("PassiveEffects"));
            auto* text = passives ? U::CastField<U::FStrProperty>(passives->GetInner()) : nullptr;
            if (!text || text->GetArrayDim() != 1 || text->GetOffset_Internal() != 0) return false;
            U::FScriptArrayHelper effects(passives, passives->ContainerPtrToValuePtr<void>(defaults));
            if (effects.Num() < 0 || effects.Num() > max_effects) return false;
            for (int index = 0; index < effects.Num(); ++index)
            {
                const auto& authored = text->GetPropertyValue(effects.GetRawPtr(index));
                if (authored.Len() < 0 || authored.Len() > 256) return false;
                if (!authored.IsEmpty()) counts.gear += U::FName{*authored, U::FNAME_Find} == sought;
            }
            return true;
        };

        const auto exact = [&](U::FStructProperty* prop) { return prop && prop->GetStruct().Get() == type; };
        constexpr std::array stores{std::pair{STR("PlayerBackpack"), true}, std::pair{STR("PlayerEquipment"), false}};
        for (const auto& [store, backpack] : stores)
        {
            auto* property = field<U::FArrayProperty>(save, store);
            if (!property || !exact(U::CastField<U::FStructProperty>(property->GetInner()))) return false;
            U::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(save));
            if (rows.Num() < 0 || rows.Num() > max_inventory) return false;
            for (int index = 0; index < rows.Num(); ++index)
                if (!visit(rows.GetRawPtr(index), backpack)) return false;
        }
        auto* armor = field<U::FStructProperty>(save, STR("PlayerArmor"));
        auto* helmet = field<U::FStructProperty>(save, STR("PlayerHelmet"));
        return exact(armor) && exact(helmet) &&
            visit(armor->ContainerPtrToValuePtr<void>(save), false,
                  STR("/Game/Classes/Armor_Items/Item_ArmorMaster_BP.Item_ArmorMaster_BP_C")) &&
            visit(helmet->ContainerPtrToValuePtr<void>(save), false,
                  STR("/Game/Classes/Armor_Items/Item_HelmetMaster_BP.Item_HelmetMaster_BP_C"));
    }

    bool count_bounty(U::UObject* save, const U::FName& name, BountyCounts& counts)
    {
        return count_owned_effects(save, name, counts) && count_inventory_effects(save, name, counts) &&
            counts.total() <= max_cache_effects;
    }

    bool cache_snapshot(U::UObject* instance, BountyState& state, bool compare)
    {
        auto* property = field<U::FArrayProperty>(instance, STR("PassiveEffects"));
        auto* names = name_entries(property);
        if (!names || state.name.IsNone()) return false;
        U::FScriptArrayHelper cache(property, property->ContainerPtrToValuePtr<void>(instance));
        const int size = cache.Num();
        if (size < 0 || size > max_cache_effects || (compare && size != state.cache_size)) return false;
        if (!compare)
        {
            state.cache_size = size;
            state.cache_bounty = 0;
        }
        for (int index = 0; index < size; ++index)
        {
            const auto name = names->GetPropertyValue(cache.GetRawPtr(index));
            auto& previous = state.cache[static_cast<std::size_t>(index)];
            if (compare && previous != name) return false;
            if (!compare)
            {
                state.cache_bounty += name == state.name;
                previous = name;
            }
        }
        return true;
    }


    struct Hit
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr killer{};
        U::FWeakObjectPtr save{};
        std::string character;
        std::string projectile;
        double before{};
        bool died{};
    };

    struct Proof
    {
        std::string_view status{"watching"};
        int hits{}, loot_calls{}, bounty_calls{}, bounty_instances{}, ammo_count{};
        bool bounty_active{}, original_bounty_active{}, cache_unchanged{};
        int original_bounty_instances{}, bounty_overrides{};
        std::string_view bounty_source{"pending"};
        U::FWeakObjectPtr killer{};
        U::FWeakObjectPtr killer_save{};
        U::FWeakObjectPtr ammo_drop{};
        bool captured{}, reserved{};
        std::string_view role{"-"};
        std::string killer_name, killer_character, projectile, ammo_actor, ammo_class;
        double damage{}, before{}, after{};
    };

    struct Scope
    {
        U::FWeakObjectPtr world{}, instance{}, driver{}, npc{};
        Address npc_identity{}, ammo_identity{};
        Participant host{};
        std::array<Participant, max_guests> guests{};
        Schema schema{};
        Hit hit{};
        Proof proof{};
        BountyState bounty{};
        U::FFrame* loot_frame{};
        Clock::time_point until{};
        std::uint64_t generation{};
        bool dead_bridge{}, ordinary{};
    };

    Scope fixture_scope;
    std::array<Scope, cqc_actor_paths.size()> cqc_scopes;
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};

    auto scopes() { return std::array{&fixture_scope, &cqc_scopes[0], &cqc_scopes[1]}; }

    Scope* cqc_slot(Address actor)
    {
        if (!actor) return nullptr;
        for (std::size_t index = 0; index < cqc_scopes.size(); ++index)
        {
            auto& candidate = cqc_scopes[index];
            if (candidate.npc_identity == actor && candidate.generation == generation.load(std::memory_order_acquire))
                return &candidate;
        }
        for (std::size_t index = 0; index < cqc_actor_paths.size(); ++index)
            if (address(find<U::AActor>(cqc_actor_paths[index])) == actor) return &cqc_scopes[index];
        return nullptr;
    }

    bool member_current(const Scope& state, const Participant& member, bool local)
    {
        auto* world = state.world.Get();
        auto* pawn = member.pawn.Get();
        auto* controller = member.controller.Get();
        auto* player = member.player.Get();
        auto* save = member.save.Get();
        if (!authority(pawn, world) || !authority(controller, world) || !live(player) || !live(save) ||
            object(pawn, STR("Save")) != save || object(pawn, STR("Controller")) != controller ||
            object(controller, STR("Pawn")) != pawn || object(controller, STR("Player")) != player ||
            object(player, STR("PlayerController")) != controller) return false;
        if (local) return true;
        auto* driver = state.driver.Get();
        if (!live(driver) || save == state.host.save.Get() || player == state.host.player.Get() ||
            object(player, STR("Driver")) != driver || object(player, STR("OwningActor")) != controller) return false;
        auto* property = field<U::FArrayProperty>(driver, STR("ClientConnections"));
        auto* inner = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!inner) return false;
        U::FScriptArrayHelper connections(property, property->ContainerPtrToValuePtr<void>(driver));
        if (connections.Num() < 1 || connections.Num() > max_guests) return false;
        int matches = 0;
        for (int index = 0; index < connections.Num(); ++index)
            matches += inner->GetObjectPropertyValue(connections.GetRawPtr(index)) == player;
        return matches == 1;
    }

    bool local_roster(const Scope& state)
    {
        auto* world = state.world.Get();
        auto* instance = state.instance.Get();
        auto* driver = state.driver.Get();
        auto* local = state.host.player.Get();
        if (!live(world) || !live(instance) || !live(driver) || !live(local) ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
            object(world, STR("OwningGameInstance")) != instance || object(world, STR("NetDriver")) != driver ||
            object(driver, STR("World")) != world || sole_object(instance, STR("LocalPlayers")) != local ||
            object(instance, STR("SaveSlot_Ref")) != state.host.save.Get() || !member_current(state, state.host, true))
            return false;
        return state.ordinary || (sole_object(driver, STR("ClientConnections")) == state.guests[0].player.Get() &&
            member_current(state, state.guests[0], false));
    }

    const Participant* exact_killer(const Scope& state, U::UObject* pawn)
    {
        if (pawn == state.host.pawn.Get()) return &state.host;
        for (const auto& guest : state.guests)
            if (pawn == guest.pawn.Get() && member_current(state, guest, false)) return &guest;
        return nullptr;
    }

    bool current(const Scope& state)
    {
        auto* npc = state.npc.Get();
        auto* world = state.world.Get();
        return state.generation == generation.load(std::memory_order_acquire) && authority(npc, world) &&
            local_roster(state) && object(npc, STR("As Game Instance FP")) == state.instance.Get() &&
            static_cast<U::AActor*>(npc)->GetLevel() == object(world, STR("PersistentLevel"));
    }

    bool ticket_current(const Scope& state)
    {
        return current(state) || (state.ordinary && (state.proof.reserved || state.proof.captured) &&
            state.generation == generation.load(std::memory_order_acquire) && local_roster(state));
    }

    bool cqc_ammunition(U::UObject* npc, U::UObject* world)
    {
        auto* weapon = object(npc, STR("Weapon_Ranged"));
        auto* army = find<U::UClass>(army_shotgun_path);
        auto* shell = find<U::UClass>(shell_path);
        auto* configured = field<U::FClassProperty>(npc, STR("AmmoItem_Class"));
        auto* ammo_type = field<U::FStructProperty>(weapon, STR("AmmoType"));
        auto* type = find<U::UScriptStruct>(ammo_type_path);
        auto* ammo_item = field<U::FClassProperty>(type, STR("Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582"));
        const auto* item_base = find<U::UClass>(item_path);
        return army && shell && item_base && authority(weapon, world) && weapon->GetClassPrivate() == army &&
            configured && configured->GetObjectPropertyValue(configured->ContainerPtrToValuePtr<void>(npc)) == shell &&
            ammo_type && type && ammo_type->GetStruct().Get() == type && ammo_item &&
            ammo_item->GetMetaClass().Get() == item_base && ammo_item->GetOffset_Internal() == 0x10 &&
            ammo_item->GetObjectPropertyValue(ammo_item->ContainerPtrToValuePtr<void>(
                ammo_type->ContainerPtrToValuePtr<void>(weapon))) == shell;
    }

    U::UFunction* script(U::UClass* type, const U::TCHAR* path, const U::TCHAR* name, int count)
    {
        RC::StringType full{path};
        full += STR(":");
        full += name;
        auto* function = find<U::UFunction>(full.c_str());
        return function && type && function->GetOuterPrivate() == type &&
            function->GetNumParms() == count && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    bool bind_schema(Scope& state)
    {
        auto* npc = state.npc.Get();
        auto* master = find<U::UClass>(npc_path);
        state.schema.bullet = find<U::UClass>(projectile_path);
        state.schema.ammo = find<U::UClass>(ammo_path);
        auto& schema = state.schema;
        schema.deal = script(master, npc_path, STR("DealDamage"), 7);
        schema.feedback = script(master, npc_path, STR("DamageFeedback"), 5);
        schema.die = script(master, npc_path, STR("Die"), 0);
        schema.script = script(master, npc_path, STR("ExecuteUbergraph_NPC_Master_BP"), 1);
        schema.loot = script(master, npc_path, STR("SpawnLoot"), 0);
        schema.calc = script(schema.bullet, projectile_path, STR("CalcDmg"), 1);
        schema.passive = script(state.instance.Get()->GetClassPrivate(), instance_path, STR("FindPassiveEffect"), 4);
        schema.chance = find<U::UFunction>(
            STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:LootItem_SpawnChance"));
        constexpr auto actor_path = STR("/Script/Engine.Actor");
        auto* actor = find<U::UClass>(actor_path);
        auto* item = find<U::UClass>(item_path);
        auto* ammo_class = field<U::FClassProperty>(npc, STR("AmmoItem_Class"));
        schema.health = field<U::FDoubleProperty>(npc, STR("Health"));
        schema.instigator = parameter<U::FObjectProperty>(schema.deal, STR("Instigator"), 0x150, false);
        schema.damage = parameter<U::FDoubleProperty>(schema.deal, STR("TotalDamage"), 0x178, true);
        schema.lethal = parameter<U::FBoolProperty>(schema.deal, STR("IsLethal"), 0x180, true);
        if (schema.loot)
        {
            // Numbered Kismet locals share a comparison index. Resolve the
            // exact reflected property rather than accepting the first alias.
            for (auto* property : U::TFieldRange<U::FProperty>(schema.loot, U::EFieldIterationFlags::None))
            {
                if (property->GetOffset_Internal() == 0x4b0 &&
                    property->GetFName().ToString() == STR("CallFunc_FinishSpawningActor_ReturnValue_3"))
                {
                    schema.ammo_result = U::CastField<U::FObjectProperty>(property);
                    break;
                }
            }
        }
        schema.effect = parameter<U::FNameProperty>(schema.passive, STR("EffectName"), 0, false);
        schema.count = parameter<U::FBoolProperty>(schema.passive, STR("CountInstances"), 8, false);
        schema.bounty = parameter<U::FBoolProperty>(schema.passive, STR("EffectActive"), 9, true);
        schema.instances = parameter<U::FIntProperty>(schema.passive, STR("Instances"), 0xc, true);
        return actor && item && master && npc->IsA(master) && schema.bullet && schema.ammo && schema.ammo->IsChildOf(item) &&
            schema.deal && npc->GetFunctionByNameInChain(U::FName{STR("DealDamage")}) == schema.deal &&
            schema.feedback && schema.die && schema.script && schema.loot && schema.calc && schema.passive &&
            schema.passive->GetParmsSize() == 16 &&
            schema.chance && schema.chance->GetOuterPrivate() == master && schema.chance->GetNumParms() == 2 &&
            U::CastField<U::FBoolProperty>(schema.chance->GetReturnProperty()) &&
            schema.health && schema.instigator && schema.instigator->GetPropertyClass().Get() == actor &&
            schema.damage && schema.lethal && schema.ammo_result && schema.ammo_result->GetArrayDim() == 1 &&
            schema.ammo_result->GetSize() == sizeof(void*) && schema.ammo_result->GetPropertyClass().Get() == item &&
            !schema.ammo_result->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm) &&
            ammo_class && ammo_class->GetMetaClass().Get() == item &&
            schema.effect && schema.count && schema.bounty && schema.instances;
    }

    bool set_participant(Participant& member, U::UObject* pawn, U::UClass* controller_type, U::UClass* save_type,
                         U::UClass* player_type)
    {
        auto* controller = object(pawn, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* save = object(pawn, STR("Save"));
        if (!live(controller) || controller->GetClassPrivate() != controller_type ||
            !live(player) || !player->IsA(player_type) || !live(save) || save->GetClassPrivate() != save_type)
            return false;
        member.pawn = pawn;
        member.controller = controller;
        member.player = player;
        member.save = save;
        return true;
    }

    std::string_view bind(Scope& next, Address host, Address guest, Address actor)
    {
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* bandit_type = find<U::UClass>(bandit_path);
        auto* cqc_type = find<U::UClass>(cqc_path);
        U::AActor* cqc{};
        auto* first = find<U::AActor>(cqc_actor_paths.front());
        if (address(first) == actor) cqc = first;
        if (next.ordinary && !cqc)
        {
            for (const auto* path : cqc_actor_paths)
            {
                auto* candidate = find<U::AActor>(path);
                if (address(candidate) == actor) cqc = candidate;
            }
        }
        auto* store = find<U::UClass>(save_path);
        auto* controller = find<U::UClass>(controller_path);
        auto* instance = find<U::UClass>(instance_path);
        auto* local = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        if (!pawn_type || !store || !controller || !instance || !local || !connection)
            return "stock_enemy_loot_classes_unavailable";
        U::UObject* home{};
        U::UObject* peer{};
        U::UObject* enemy{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) == host && candidate->GetClassPrivate() == pawn_type) home = candidate;
            if (address(candidate) == guest && candidate->GetClassPrivate() == pawn_type) peer = candidate;
            if (address(candidate) == actor && (candidate->GetClassPrivate() == bandit_type ||
                (candidate == cqc && candidate->GetClassPrivate() == cqc_type))) enemy = candidate;
            return home && (next.ordinary || peer) && enemy ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        next.dead_bridge = enemy && enemy == cqc;
        auto* world = find<U::UWorld>(next.dead_bridge ? cqc_world_path : world_path);
        auto* owner = object(world, STR("OwningGameInstance"));
        if (!live(world) || !live(owner) || owner->GetClassPrivate() != instance)
            return "stock_enemy_loot_classes_unavailable";
        next.world = world;
        next.driver = object(world, STR("NetDriver"));
        next.instance = owner;
        next.npc = enemy;
        next.npc_identity = address(enemy);
        next.generation = generation.load(std::memory_order_acquire);
        if ((next.ordinary && !next.dead_bridge) || !authority(enemy, world) ||
            !boolean(enemy, STR("Alive"), true) || !boolean(enemy, STR("Initialized"), true) ||
            !set_participant(next.host, home, controller, store, local) ||
            (!next.ordinary && !set_participant(next.guests[0], peer, controller, store, connection)) || !current(next))
            return "stock_enemy_loot_authority_or_roster_unavailable";
        auto* faction = field<U::FByteProperty>(enemy, STR("Faction"));
        if (next.dead_bridge && (!faction || faction->GetPropertyValueInContainer(enemy) != 4 || !cqc_ammunition(enemy, world)))
            return "stock_dead_bridge_cqc_loadout_unavailable";
        next.bounty.name = U::FName{STR("Bounty"), U::FNAME_Find};
        return bind_schema(next) ? std::string_view{} : "stock_enemy_loot_blueprint_schema_unavailable";
    }

    void* output(U::FFrame& frame, U::FProperty* property)
    {
        auto* entry = frame.OutParms();
        void* found{};
        for (int remaining = frame.Node()->GetNumParms(); entry && remaining > 0; --remaining, entry = entry->NextOutParm)
        {
            if (entry->Property != property) continue;
            if (found) return nullptr;
            found = entry->PropAddr;
        }
        return entry ? nullptr : found;
    }

    bool available()
    {
        return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw();
    }

    bool reserved(const Scope& state)
    {
        auto* world = state.world.Get();
        auto* driver = state.driver.Get();
        return state.ordinary && state.proof.reserved && state.generation == generation.load(std::memory_order_acquire) &&
            live(world) && live(driver) && object(world, STR("NetDriver")) == driver &&
            authority(state.proof.ammo_drop.Get(), world);
    }

    void reserve_pickup(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!available() || !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        static const U::FName interact{STR("SendInteractionToActor")};
        static const U::FName store{STR("PutInInventory")};
        static const U::FName pickup{STR("PickUp_Object")};
        static const U::FName add{STR("AddItemToInventory")};
        for (const auto& scope : cqc_scopes)
        {
            if (!scope.proof.reserved) continue;
            auto* shell = scope.proof.ammo_drop.Get();
            if ((name == interact || name == store || name == pickup) && receiver == shell &&
                function->GetOuterPrivate() == find<U::UClass>(item_path) && reserved(scope))
                call.PreventOriginalFunctionCall();
            else if (name == add && args && receiver == scope.instance.Get() &&
                function == find<U::UFunction>(add_item_path) && reserved(scope))
            {
                auto* item = parameter<U::FObjectProperty>(function, STR("Item"), 0, false);
                if (item && item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(args)) == shell)
                    call.PreventOriginalFunctionCall();
            }
            if (call.OriginalFunctionCallPrevented()) return;
        }
    }

    void prepare_hit(Scope& scope, U::UObject* npc, U::FFrame& frame)
    {
        if (scope.hit.frame || scope.proof.killer.Get() || !frame.Locals() || !boolean(npc, STR("Alive"), true)) return;
        auto* caller = frame.PreviousFrame();
        auto* actor = caller ? caller->Object() : nullptr;
        if (!actor || caller->Node() != scope.schema.calc || !actor->IsA(scope.schema.bullet) ||
            !authority(actor, scope.world.Get())) return;
        const auto* parameter = scope.schema.instigator->ContainerPtrToValuePtr<void>(frame.Locals());
        auto* killer = scope.schema.instigator->GetObjectPropertyValue(parameter);
        const auto* member = killer ? exact_killer(scope, killer) : nullptr;
        if (!member ||
            object(actor, STR("Owner")) != killer || object(actor, STR("Instigator")) != killer) return;
        const auto health = scope.schema.health->GetPropertyValueInContainer(npc);
        if (!std::isfinite(health) || health <= 0) return;
        scope.hit = {&frame, U::FWeakObjectPtr{killer}, member->save, member->character,
            RC::to_utf8_string(actor->GetFullName()), health, false};
    }

    bool attributed_killer(const Scope& scope, U::UObject* npc)
    {
        auto* last = object(npc, STR("LastDamagedMe"));
        const auto& proof = scope.proof;
        if (proof.killer_name.empty()) return false;
        if (auto* killer = proof.killer.Get()) return last == killer;
        // Unreal can clear the actor reference after an authenticated shooter disconnects.
        // A different last damager can never inherit the captured lethal proof.
        return scope.ordinary && !last;
    }

    void prepare_bounty(Scope& scope)
    {
        auto& bounty = scope.bounty;
        auto& proof = scope.proof;
        bounty.cache_size = -1;
        if (!cache_snapshot(scope.instance.Get(), bounty, false))
        {
            proof.bounty_source = "unqualified";
            return;
        }
        if (proof.role == "host")
        {
            proof.bounty_source = "stock_host";
            return;
        }
        if ((!scope.ordinary && !count_bounty(scope.guests[0].save.Get(), bounty.name, bounty.counts)) ||
            (scope.ordinary && proof.bounty_source != "guest_detached"))
        {
            proof.bounty_source = "unqualified";
            return;
        }
        proof.bounty_source = "guest_detached";
    }


    void on_npc_pre(Scope& scope, U::UObject* object_receiver, U::FFrame& frame)
    {
        auto* node = frame.Node();
        if ((node != scope.schema.deal && node != scope.schema.die && node != scope.schema.loot) ||
            Clock::now() >= scope.until || !current(scope)) return;
        if (node == scope.schema.deal)
        {
            if (scope.dead_bridge && !cqc_ammunition(object_receiver, scope.world.Get())) return;
            prepare_hit(scope, object_receiver, frame);
            return;
        }
        auto* caller = frame.PreviousFrame();
        if (!caller || caller->Object() != object_receiver) return;
        if (node == scope.schema.die && scope.hit.frame && caller->Node() == scope.schema.feedback &&
            caller->PreviousFrame() == scope.hit.frame &&
            object(object_receiver, STR("LastDamagedMe")) == scope.hit.killer.Get())
        {
            scope.hit.died = true;
        }
        if (node == scope.schema.loot && caller->Node() == scope.schema.script && !scope.proof.killer_name.empty() &&
            scope.proof.loot_calls == 0 && boolean(object_receiver, STR("Alive"), false))
        {
            if (!attributed_killer(scope, object_receiver))
            {
                scope.proof.status = "stock_loot_killer_changed";
                return;
            }
            prepare_bounty(scope);
            scope.loot_frame = &frame;
            ++scope.proof.loot_calls;
        }
    }

    void pre(Iteration& iteration, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available()) return;
        reserve_pickup(iteration, receiver, frame.Node(), frame.Locals());
        if (!receiver || frame.Object() != receiver || iteration.OriginalFunctionCallPrevented()) return;
        for (auto* scope : scopes())
        {
            if (scope->npc.Get() != receiver) continue;
            on_npc_pre(*scope, receiver, frame);
            return;
        }
    }

    void finish_hit(Scope& scope, Iteration& iteration, U::UObject* npc, U::FFrame& frame)
    {
        auto hit = std::move(scope.hit);
        scope.hit = {};
        auto* lethal = output(frame, scope.schema.lethal);
        auto* damage = output(frame, scope.schema.damage);
        auto* killer = hit.killer.Get();
        if (iteration.OriginalFunctionCallPrevented() || !killer || !lethal || !damage ||
            reinterpret_cast<Address>(damage) % alignof(double) != 0 || !current(scope)) return;
        const auto dealt = scope.schema.damage->GetPropertyValue(damage);
        const auto after = scope.schema.health->GetPropertyValueInContainer(npc);
        if (!std::isfinite(dealt) || !std::isfinite(after) || dealt <= 0 || after >= hit.before) return;
        ++scope.proof.hits;
        if (!scope.schema.lethal->GetPropertyValue(lethal)) return;
        if (!hit.died || after > 0 || !boolean(npc, STR("Alive"), false) || object(npc, STR("LastDamagedMe")) != killer)
        {
            scope.proof.status = "stock_lethal_kill_unverified";
            return;
        }
        scope.proof.killer = killer;
        scope.proof.killer_save = hit.save;
        scope.proof.killer_name = RC::to_utf8_string(killer->GetFullName());
        scope.proof.killer_character = std::move(hit.character);
        scope.proof.role = killer == scope.host.pawn.Get() ? "host" : "guest";
        scope.proof.projectile = std::move(hit.projectile);
        scope.proof.damage = dealt;
        scope.proof.before = hit.before;
        scope.proof.after = after;
        scope.proof.status = "stock_kill_confirmed";
        if (scope.ordinary && scope.proof.role == "guest")
        {
            auto* save = scope.proof.killer_save.Get();
            scope.proof.bounty_source = object(killer, STR("Save")) == save && live(save) &&
                count_bounty(save, scope.bounty.name, scope.bounty.counts) ? "guest_detached" : "unqualified";
        }
    }

    void observe_bounty(Scope& scope, Iteration& iteration, U::UObject* instance, U::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        const auto& schema = scope.schema;
        auto& proof = scope.proof;
        auto& bounty = scope.bounty;
        if (iteration.OriginalFunctionCallPrevented() || instance != scope.instance.Get() || frame.Object() != instance ||
            !frame.Locals() || !caller || caller->Node() != schema.chance || caller->Object() != scope.npc.Get() ||
            caller->PreviousFrame() != scope.loot_frame ||
            schema.effect->GetPropertyValueInContainer(frame.Locals()) != bounty.name ||
            !schema.count->GetPropertyValueInContainer(frame.Locals())) return;
        auto* active = output(frame, schema.bounty);
        auto* instances = output(frame, schema.instances);
        if (!active || !instances || active == instances || reinterpret_cast<Address>(instances) % alignof(std::int32_t) != 0)
        {
            proof.bounty_source = "unqualified";
            return;
        }
        const auto stock_count = schema.instances->GetPropertyValue(instances);
        const auto stock_active = schema.bounty->GetPropertyValue(active);
        if (stock_count < 0 || stock_active != (stock_count > 0) || (scope.ordinary && stock_count != bounty.cache_bounty))
        {
            proof.bounty_source = "unqualified";
            return;
        }
        if (proof.bounty_calls && (proof.original_bounty_active != stock_active || proof.original_bounty_instances != stock_count))
        {
            proof.status = "stock_bounty_cache_changed_during_loot";
            proof.bounty_source = "unqualified";
        }
        ++proof.bounty_calls;
        proof.original_bounty_active = stock_active;
        proof.original_bounty_instances = stock_count;
        if (proof.bounty_source == "guest_detached")
        {
            if (!current(scope) || (!scope.ordinary && proof.killer.Get() != scope.guests[0].pawn.Get()) ||
                !attributed_killer(scope, scope.npc.Get()) ||
                stock_count != bounty.cache_bounty || !cache_snapshot(instance, bounty, true))
            {
                proof.bounty_source = "unqualified";
                proof.status = "stock_bounty_source_changed_during_loot";
            }
            else
            {
                // This frame's exact out parms refer to the caller's local
                // result. Never install the guest Save or recompile passives.
                const auto total = bounty.counts.total();
                schema.bounty->SetPropertyValue(active, total > 0);
                schema.instances->SetPropertyValue(instances, total);
                if (schema.instances->GetPropertyValue(instances) == total &&
                    schema.bounty->GetPropertyValue(active) == (total > 0))
                    ++proof.bounty_overrides;
                else
                {
                    proof.bounty_source = "unqualified";
                    proof.status = "mutation_unverified_stock_bounty_result";
                }
            }
        }
        proof.bounty_active = schema.bounty->GetPropertyValue(active);
        proof.bounty_instances = schema.instances->GetPropertyValue(instances);
    }

    void finish_loot(Scope& scope, Iteration& iteration, U::UObject* npc, U::FFrame& frame)
    {
        scope.loot_frame = nullptr;
        if (iteration.OriginalFunctionCallPrevented() || !frame.Locals() || !current(scope) || !attributed_killer(scope, npc)) return;
        scope.proof.cache_unchanged = cache_snapshot(scope.instance.Get(), scope.bounty, true);
        if (!scope.proof.cache_unchanged && scope.proof.bounty_overrides)
            scope.proof.status = "stock_bounty_cache_changed_during_loot";
        if (scope.proof.status == "stock_kill_confirmed") scope.proof.status = "stock_loot_observed";
        auto* ammo = scope.schema.ammo_result->GetObjectPropertyValue(
            scope.schema.ammo_result->ContainerPtrToValuePtr<void>(frame.Locals()));
        auto* type = field<U::FClassProperty>(npc, STR("AmmoItem_Class"));
        if (!type) return;
        auto* expected = type->GetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(npc));
        if (!expected) return;
        if (!authority(ammo, scope.world.Get()) || ammo->GetClassPrivate() != expected || !ammo->IsA(scope.schema.ammo))
        {
            scope.proof.status = "stock_enemy_ammo_spawn_unverified";
            return;
        }
        auto* count = field<U::FIntProperty>(ammo, STR("Ammo"));
        const auto units = count ? count->GetPropertyValueInContainer(ammo) : -1;
        if (units < 3 || units > 9)
        {
            scope.proof.status = "stock_enemy_ammo_quantity_unverified";
            return;
        }
        scope.proof.ammo_actor = RC::to_utf8_string(ammo->GetFullName());
        scope.proof.ammo_class = RC::to_utf8_string(ammo->GetClassPrivate()->GetPathName());
        scope.proof.ammo_count = units;
        scope.proof.ammo_drop = ammo;
        scope.ammo_identity = address(ammo);
        if (scope.ordinary)
        {
            const auto& proof = scope.proof;
            const bool host = proof.bounty_source == "stock_host" && !proof.bounty_overrides &&
                proof.bounty_instances == proof.original_bounty_instances;
            const bool guest = proof.bounty_source == "guest_detached" && proof.bounty_overrides == proof.bounty_calls &&
                proof.bounty_instances == scope.bounty.counts.total();
            if (proof.status != "stock_loot_observed" || !proof.cache_unchanged || proof.bounty_calls < 1 || (!host && !guest) ||
                ammo->GetClassPrivate() != find<U::UClass>(shell_path) ||
                static_cast<U::AActor*>(ammo)->GetLevel() != object(scope.world.Get(), STR("PersistentLevel")))
            {
                scope.proof.status = "stock_enemy_drop_attribution_unverified";
                return;
            }
            scope.proof.reserved = true;
        }
    }

    void post(Iteration& iteration, U::UObject* object_receiver, U::FFrame& frame, void*)
    {
        if (!available()) return;
        for (auto* scope : scopes())
        {
            if (&frame == scope->hit.frame && object_receiver == scope->npc.Get())
                finish_hit(*scope, iteration, object_receiver, frame);
            if (!scope->loot_frame) continue;
            if (&frame == scope->loot_frame && object_receiver == scope->npc.Get())
                finish_loot(*scope, iteration, object_receiver, frame);
            else if (frame.Node() == scope->schema.passive)
                observe_bounty(*scope, iteration, object_receiver, frame);
        }
    }

    int answer(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    std::string_view seed_bounty_fixture(Scope& next)
    {
        auto* guest_save = next.guests[0].save.Get();
        auto* instance = next.instance.Get();
        auto& bounty = next.bounty;
        BountyCounts before;
        if (!count_bounty(guest_save, bounty.name, before) || !cache_snapshot(instance, bounty, false) ||
            bounty.cache_bounty != 0) return "stock_bounty_fixture_requires_clean_host_cache";
        if (before.total() == 1 && before.permanent == 1) return {};
        if (before.total() != 0) return "stock_bounty_fixture_requires_unmodified_guest_traits";

        void* data{};
        auto* property = permanent_effects(guest_save, data);
        auto* names = name_entries(property);
        if (!property || !names) return "stock_bounty_fixture_permanent_effects_unavailable";
        U::FScriptArrayHelper permanent(property, data);
        const auto size = permanent.Num();
        if (size < 0 || size >= max_effects) return "stock_bounty_fixture_permanent_effects_full";
        const auto index = permanent.AddValue();
        if (index != size || permanent.Num() != size + 1) return "mutation_unverified_stock_bounty_append";
        names->SetPropertyValue(permanent.GetRawPtr(index), bounty.name);
        BountyCounts after;
        if (current(next) && cache_snapshot(instance, bounty, true) &&
            count_bounty(guest_save, bounty.name, after) && after.permanent == 1 && after.total() == 1)
            return {};

        permanent.RemoveValues(index);
        BountyCounts restored;
        return current(next) && permanent.Num() == size && cache_snapshot(instance, bounty, true) &&
            count_bounty(guest_save, bounty.name, restored) && restored.total() == before.total()
            ? "stock_bounty_fixture_refused_after_rollback" : "mutation_unverified_stock_bounty_fixture";
    }


    int set_scope(const Lua& lua)
    {
        auto& scope = fixture_scope;
        if (!available() || !fixtures_enabled() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        const auto host = lua.get_integer();
        const auto guest = lua.get_integer();
        const auto actor = lua.get_integer();
        if (host <= 0 || guest <= 0 || actor <= 0 || host == guest ||
            (ordinary_enabled() && cqc_slot(static_cast<Address>(actor))))
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        if (address(scope.host.pawn.Get()) == static_cast<Address>(host) &&
            address(scope.guests[0].pawn.Get()) == static_cast<Address>(guest) &&
            address(scope.npc.Get()) == static_cast<Address>(actor) && current(scope))
        {
            scope.until = Clock::now() + lease;
            return answer(lua, true, scope.proof.status);
        }
        scope = {};
        Scope next;
        if (const auto error = bind(next, static_cast<Address>(host), static_cast<Address>(guest), static_cast<Address>(actor));
            !error.empty()) return answer(lua, false, error);
        if (env("SILVERFISH_COOP_NATIVE_ENEMY_BOUNTY_FIXTURE"))
        {
            if (const auto error = seed_bounty_fixture(next); !error.empty()) return answer(lua, false, error);
        }
        next.until = Clock::now() + lease;
        scope = std::move(next);
        return answer(lua, true, scope.proof.status);
    }

    bool character_id(std::string_view id)
    {
        if (id.size() != 36) return false;
        for (std::size_t index = 0; index < id.size(); ++index)
        {
            const auto c = id[index];
            const bool dash = index == 8 || index == 13 || index == 18 || index == 23;
            if (dash ? c != '-' : !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
        }
        return true;
    }

    int set_cqc_scope(const Lua& lua)
    {
        const auto size = lua.get_stack_size();
        if (!available() || !ordinary_enabled() || size < 2 || size > 2 + max_guests * 3 ||
            (size - 2) % 3 || !lua.is_integer(1) || !lua.is_integer(2))
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        const auto host = lua.get_integer();
        const auto npc = lua.get_integer();
        if (host <= 0 || npc <= 0 || host == npc)
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        auto* slot = cqc_slot(static_cast<Address>(npc));
        if (!slot) return answer(lua, false, "native_enemy_loot_exact_watch_required");
        auto& scope = *slot;
        const bool same_scope = scope.ordinary && address(scope.host.pawn.Get()) == static_cast<Address>(host) &&
            scope.npc_identity == static_cast<Address>(npc) && ticket_current(scope);
        if (!same_scope && reserved(scope)) return answer(lua, false, "native_enemy_loot_roll_pending");
        Scope next;
        if (!same_scope)
        {
            next.ordinary = true;
            if (const auto error = bind(next, static_cast<Address>(host), 0, static_cast<Address>(npc)); !error.empty())
                return answer(lua, false, error);
        }
        auto& active = same_scope ? scope : next;
        const auto count = (size - 2) / 3;
        std::array<Participant, max_guests> members{};
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* store = find<U::UClass>(save_path);
        auto* controller = find<U::UClass>(controller_path);
        auto* connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        if (!pawn_type || !store || !controller || !connection)
            return answer(lua, false, "stock_enemy_loot_classes_unavailable");
        for (int index = 0; index < count; ++index)
        {
            if (!lua.is_integer(1) || !lua.is_integer(2) || !lua.is_string(3))
                return answer(lua, false, "native_enemy_loot_exact_roster_required");
            const auto pawn = lua.get_integer();
            const auto save = lua.get_integer();
            const std::string id{lua.get_string()};
            if (pawn <= 0 || pawn == host || save <= 0 || !character_id(id))
                return answer(lua, false, "native_enemy_loot_exact_roster_required");
            auto& member = members[static_cast<std::size_t>(index)];
            U::UObject* actor{};
            for (const auto& prior : active.guests)
                if (address(prior.pawn.Get()) == static_cast<Address>(pawn)) actor = prior.pawn.Get();
            if (!actor)
                U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                    if (address(candidate) != static_cast<Address>(pawn)) return RC::LoopAction::Continue;
                    if (live(candidate) && candidate->GetClassPrivate() == pawn_type) actor = candidate;
                    return RC::LoopAction::Break;
                });
            if (!set_participant(member, actor, controller, store, connection) ||
                address(member.save.Get()) != static_cast<Address>(save) || !member_current(active, member, false))
                return answer(lua, false, "native_enemy_loot_exact_roster_required");
            member.character = id;
            for (int previous = 0; previous < index; ++previous)
            {
                const auto& other = members[static_cast<std::size_t>(previous)];
                if (other.pawn.Get() == actor || other.controller.Get() == member.controller.Get() ||
                    other.player.Get() == member.player.Get() || other.save.Get() == member.save.Get() || other.character == id)
                    return answer(lua, false, "native_enemy_loot_duplicate_roster_member");
            }
        }
        active.guests = std::move(members);
        active.until = Clock::now() + lease;
        if (!same_scope) scope = std::move(next);
        return answer(lua, true, scope.proof.status);
    }

    int read(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!available() || (arguments != 1 && arguments != 2) || !lua.is_integer(1) ||
            (arguments == 2 && !lua.is_integer(2))) return 0;
        const auto requested = lua.get_integer();
        const auto npc = arguments == 2 ? lua.get_integer() : 0;
        const auto* slot = arguments == 1 ? &fixture_scope : cqc_slot(static_cast<Address>(npc));
        if (!slot || (arguments == 2 && (!slot->ordinary || npc <= 0 || slot->npc_identity != static_cast<Address>(npc))))
            return 0;
        const auto& scope = *slot;
        if (requested <= 0 || address(scope.host.pawn.Get()) != static_cast<Address>(requested) ||
            (!scope.proof.reserved && Clock::now() >= scope.until) || !ticket_current(scope)) return 0;
        const auto& report = scope.proof;
        lua.set_string(report.reserved && !reserved(scope) ? "mutation_unverified_enemy_loot_reserved_shell_missing" : report.status);
        lua.set_integer(report.hits);
        lua.set_string(report.role);
        lua.set_string(report.killer_name);
        lua.set_string(report.projectile);
        lua.set_number(report.damage);
        lua.set_number(report.before);
        lua.set_number(report.after);
        lua.set_integer(report.loot_calls);
        lua.set_integer(report.bounty_calls);
        lua.set_bool(report.bounty_active);
        lua.set_integer(report.bounty_instances);
        lua.set_string(report.ammo_actor);
        lua.set_string(report.ammo_class);
        lua.set_integer(report.ammo_count);
        lua.set_string(report.bounty_source);
        lua.set_integer(report.bounty_overrides);
        lua.set_bool(report.original_bounty_active);
        lua.set_integer(report.original_bounty_instances);
        lua.set_integer(scope.bounty.counts.permanent);
        lua.set_integer(scope.bounty.counts.timed);
        lua.set_integer(scope.bounty.counts.gear);
        lua.set_bool(report.cache_unchanged);
        if (!scope.ordinary) return 23;
        lua.set_string(report.killer_character);
        lua.set_bool(report.reserved);
        lua.set_bool(report.captured);
        return 26;
    }

    int finish_cqc(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return answer(lua, false, "native_enemy_loot_retirement_unavailable");
        const auto host = lua.get_integer();
        const auto ammo = lua.get_integer();
        if (host > 0 && ammo > 0)
        {
            for (auto& scope : cqc_scopes)
            {
                if (!scope.proof.reserved || !scope.proof.captured || scope.ammo_identity != static_cast<Address>(ammo) ||
                    address(scope.host.pawn.Get()) != static_cast<Address>(host) || !ticket_current(scope) || reserved(scope)) continue;
                scope.proof.reserved = false;
                return answer(lua, true, "retired");
            }
        }
        return answer(lua, false, "native_enemy_loot_retirement_unavailable");
    }

    int cqc_pending(const Lua& lua)
    {
        bool pending = false;
        if (available())
            for (const auto& scope : cqc_scopes) pending = pending || scope.proof.reserved;
        lua.set_bool(pending);
        return 1;
    }

    int cqc_available(const Lua& lua)
    {
        lua.set_integer(available() && ordinary_enabled() ? 2 : 0);
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::native_enemy_loot::clear_scope();
        return 0;
    }
}

namespace silverfish::native_enemy_loot
{
    bool qualify_escrow_claimant(Address pawn, Address save, Address stock, shared_loot::EscrowOwner& claimant)
    {
        if (!available()) return false;
        for (const auto* path : {world_path, cqc_world_path})
        {
            auto* world = find<U::UWorld>(path);
            if (live(world) && shared_loot::qualify_escrow_owner_in_world(world, pawn, save, stock, claimant)) return true;
        }
        return false;
    }

    bool verified_ammo_roll(Address bandit, Address ammo, shared_loot::EscrowOwner& claimant, U::UObject*& actor,
                            bool consume)
    {
        if (!available() || !bandit || !ammo) return false;
        auto* slot = cqc_slot(bandit);
        auto& scope = slot && slot->ordinary ? *slot : fixture_scope;
        if (scope.npc_identity != bandit || !ticket_current(scope) ||
            (scope.ordinary ? !reserved(scope) : Clock::now() >= scope.until)) return false;
        auto& proof = scope.proof;
        auto* dropped = proof.ammo_drop.Get();
        auto* world = scope.world.Get();
        auto* shell = find<U::UClass>(shell_path);
        auto* count = field<U::FIntProperty>(dropped, STR("Ammo"));
        if (proof.captured || proof.status != "stock_loot_observed" || proof.loot_calls != 1 || !proof.cache_unchanged ||
            (proof.bounty_source != "stock_host" && proof.bounty_source != "guest_detached") ||
            !authority(dropped, world) || address(dropped) != ammo || !shell || dropped->GetClassPrivate() != shell ||
            static_cast<U::AActor*>(dropped)->GetLevel() != object(world, STR("PersistentLevel")) ||
            !count || count->GetPropertyValueInContainer(dropped) != proof.ammo_count) return false;
        if (scope.ordinary)
        {
            // ItemActorToStruct needs only a live context in the same world. The
            // kill receipt already fixes the deceased or disconnected shooter.
            claimant = {static_cast<U::UWorld*>(world), scope.host.pawn.Get(), scope.host.save.Get(), scope.host.save.Get()};
        }
        else
        {
            const auto& participant = proof.role == "guest" ? scope.guests[0] : scope.host;
            auto* killer = participant.pawn.Get();
            if (proof.killer.Get() != killer || !qualify_escrow_claimant(address(killer), address(participant.save.Get()),
                                                                      address(scope.host.save.Get()), claimant)) return false;
        }
        actor = dropped;
        if (consume) proof.captured = true;
        return true;
    }

    void install_hooks()
    {
        if ((!fixtures_enabled() && !ordinary_enabled()) || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            pre, {false, false, owner, STR("Observe stock enemy projectiles and death")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            post, {false, false, owner, STR("Observe attributed stock enemy loot")});
        if (ordinary_enabled())
            callbacks[2] = Hook::RegisterProcessEventPreCallback(
                reserve_pickup, {false, false, owner, STR("Fence exact bankable enemy shells")});
        if (callbacks[0] == Hook::ERROR_ID || callbacks[1] == Hook::ERROR_ID ||
            (ordinary_enabled() && callbacks[2] == Hook::ERROR_ID))
        {
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& callback : callbacks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!fixtures_enabled() && !ordinary_enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeEnemyLootScope", set_scope);
        lua.register_function("SilverfishReadNativeEnemyLoot", read);
        lua.register_function("SilverfishClearNativeEnemyLootScope", clear);
        if (ordinary_enabled())
        {
            lua.register_function("SilverfishSetNativeCqcEnemyLootScope", set_cqc_scope);
            lua.register_function("SilverfishFinishNativeCqcEnemyLoot", finish_cqc);
            lua.register_function("SilverfishNativeCqcEnemyLootPending", cqc_pending);
            lua.register_function("SilverfishNativeCqcEnemyLootAvailable", cqc_available);
        }
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (U::IsInGameThreadRaw())
        {
            fixture_scope = {};
            cqc_scopes = {};
        }
    }
}
